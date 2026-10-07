"""
Tests for operational/paper_bet_settlement_driver.py (P0 block, 2026-09-29).
Covers: straight-bet settlement via the real resolve_prediction() dispatch,
combo/parlay aggregation (any LOSS -> LOSS, still-pending legs never forced,
a genuine push voids the whole ticket, a data-gap leg settles UNRESOLVED
rather than guessed), and the end-to-end driver against real, isolated
nhl.db + paper_bankroll.db fixtures -- never the real production databases.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import db
from operational import outcome_resolver as resolver
from operational import paper_bankroll as pb
from operational import paper_bet_settlement_driver as driver


def _fresh_nhl_conn():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = db.init_db(db_path=Path(tmp.name), wipe=True)
    for t in ("TOR", "MTL", "CAR", "FLA"):
        conn.execute("INSERT OR IGNORE INTO teams (team_id, full_name) VALUES (?, ?)", (t, t))
    return conn


def _insert_game(conn, game_id, home="TOR", away="MTL", state="FINAL", home_score=4, away_score=2):
    conn.execute(
        "INSERT INTO games (game_id, season, game_date, home_team, away_team, game_state, "
        "home_score, away_score, final_period_type, source) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (game_id, "20262027", "2026-10-15", home, away, state, home_score, away_score, "REG", "test"))
    conn.commit()


def _insert_player_stat(conn, game_id, player_id, team_id, shots=0):
    conn.execute(
        "INSERT INTO player_game_stats (game_id, player_id, team_id, toi_minutes, goals, assists, "
        "shots, hits, blocked_shots, played, revision_number, effective_at_utc, observed_at_utc, source) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?,?)",
        (game_id, player_id, team_id, 18.0, 0, 0, shots, None, None, 1,
         "2026-10-15T23:30:00Z", "2026-10-15T23:30:00Z", "test"))
    conn.commit()


def _insert_goalie_stat(conn, game_id, player_id, team_id, saves=0, started=True):
    conn.execute(
        "INSERT INTO goalie_game_stats (game_id, player_id, team_id, started, shots_against, "
        "saves, goals_against, revision_number, effective_at_utc, observed_at_utc, source) "
        "VALUES (?,?,?,?,?,?,?,1,?,?,?)",
        (game_id, player_id, team_id, 1 if started else 0, saves + 2, saves, 2,
         "2026-10-15T23:30:00Z", "2026-10-15T23:30:00Z", "test"))
    conn.commit()


def _fresh_bankroll_conn():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return pb.init_db(Path(tmp.name))


class TestStraightBetSettlement(unittest.TestCase):
    def test_moneyline_win(self):
        conn = _fresh_nhl_conn()
        _insert_game(conn, 1, home="TOR", away="MTL", home_score=4, away_score=2)
        bet = {"market_id": "MONEYLINE", "threshold": None, "side": "TOR", "event_id": "1", "team": "TOR"}
        result = driver.resolve_straight_bet(conn, bet)
        self.assertEqual(result["status"], resolver.RESOLVED)
        self.assertTrue(result["outcome_hit"])

    def test_moneyline_loss(self):
        conn = _fresh_nhl_conn()
        _insert_game(conn, 1, home="TOR", away="MTL", home_score=1, away_score=5)
        bet = {"market_id": "MONEYLINE", "threshold": None, "side": "TOR", "event_id": "1", "team": "TOR"}
        result = driver.resolve_straight_bet(conn, bet)
        self.assertFalse(result["outcome_hit"])

    def test_sog_straight_bet(self):
        conn = _fresh_nhl_conn()
        _insert_game(conn, 1)
        _insert_player_stat(conn, 1, "P1", "TOR", shots=5)
        bet = {"market_id": "PLAYER_SOG_4PLUS", "threshold": "4+", "side": "OVER", "event_id": "1",
               "player_id": "P1"}
        result = driver.resolve_straight_bet(conn, bet)
        self.assertEqual(result["status"], resolver.RESOLVED)
        self.assertTrue(result["outcome_hit"])

    def test_game_not_final_stays_unsettled(self):
        conn = _fresh_nhl_conn()
        _insert_game(conn, 1, state="SCHEDULED")
        bet = {"market_id": "MONEYLINE", "threshold": None, "side": "TOR", "event_id": "1", "team": "TOR"}
        result = driver.resolve_straight_bet(conn, bet)
        self.assertEqual(result["status"], resolver.GAME_NOT_FINAL)


class TestComboSettlement(unittest.TestCase):
    def _leg(self, game_id, market_family="PLAYER_SOG_ALTERNATE", threshold=4, participant_id="P1", side="OVER"):
        return {"game_id": str(game_id), "market_family": market_family, "threshold": threshold,
                "side": side, "participant_id": participant_id, "participant_name": participant_id}

    def test_all_legs_win_is_a_win(self):
        conn = _fresh_nhl_conn()
        _insert_game(conn, 1)
        _insert_game(conn, 2)
        _insert_player_stat(conn, 1, "P1", "TOR", shots=5)
        _insert_player_stat(conn, 2, "P2", "TOR", shots=6)
        bet = {"legs_json": json.dumps([self._leg(1, participant_id="P1"), self._leg(2, participant_id="P2")])}
        result = driver.resolve_combo_bet(conn, bet)
        self.assertEqual(result["status"], "WIN")

    def test_any_loss_makes_the_whole_combo_a_loss(self):
        conn = _fresh_nhl_conn()
        _insert_game(conn, 1)
        _insert_game(conn, 2)
        _insert_player_stat(conn, 1, "P1", "TOR", shots=5)   # wins (4+)
        _insert_player_stat(conn, 2, "P2", "TOR", shots=1)   # loses (needed 4+)
        bet = {"legs_json": json.dumps([self._leg(1, participant_id="P1"), self._leg(2, participant_id="P2")])}
        result = driver.resolve_combo_bet(conn, bet)
        self.assertEqual(result["status"], "LOSS")

    def test_loss_short_circuits_even_if_another_leg_is_still_pending(self):
        conn = _fresh_nhl_conn()
        _insert_game(conn, 1)
        _insert_game(conn, 2, state="SCHEDULED")
        _insert_player_stat(conn, 1, "P1", "TOR", shots=1)   # a real, already-final loss
        bet = {"legs_json": json.dumps([self._leg(1, participant_id="P1"), self._leg(2, participant_id="P2")])}
        result = driver.resolve_combo_bet(conn, bet)
        self.assertEqual(result["status"], "LOSS")

    def test_a_still_pending_leg_with_no_loss_keeps_the_whole_combo_pending(self):
        conn = _fresh_nhl_conn()
        _insert_game(conn, 1)
        _insert_game(conn, 2, state="SCHEDULED")
        _insert_player_stat(conn, 1, "P1", "TOR", shots=5)
        bet = {"legs_json": json.dumps([self._leg(1, participant_id="P1"), self._leg(2, participant_id="P2")])}
        result = driver.resolve_combo_bet(conn, bet)
        self.assertEqual(result["status"], driver.PENDING_STILL_WAITING)

    def test_a_dnp_leg_is_removed_and_the_parlay_is_repriced_on_the_remaining_legs(self):
        conn = _fresh_nhl_conn()
        _insert_game(conn, 1)
        _insert_game(conn, 2)
        _insert_player_stat(conn, 1, "P1", "TOR", shots=5)
        # P2 never dressed for game 2 -- no player_game_stats row at all.
        legs = [dict(self._leg(1, participant_id="P1"), american_price=+150),
                dict(self._leg(2, participant_id="P2"), american_price=-120)]
        result = driver.resolve_combo_bet(conn, {"legs_json": json.dumps(legs)})
        self.assertEqual(result["status"], "WIN")
        self.assertEqual(result["settled_odds"], 150.0)   # only the +150 leg remains
        self.assertEqual(len(result["voided_legs"]), 1)

    def test_a_dnp_leg_never_hides_a_lost_leg(self):
        conn = _fresh_nhl_conn()
        _insert_game(conn, 1)
        _insert_game(conn, 2)
        _insert_player_stat(conn, 1, "P1", "TOR", shots=1)   # needs 4+, real loss
        legs = [dict(self._leg(1, participant_id="P1"), american_price=+150),
                dict(self._leg(2, participant_id="P2"), american_price=-120)]
        self.assertEqual(driver.resolve_combo_bet(conn, {"legs_json": json.dumps(legs)})["status"], "LOSS")

    def test_every_leg_void_refunds_the_ticket(self):
        conn = _fresh_nhl_conn()
        _insert_game(conn, 1)
        _insert_game(conn, 2)
        legs = [dict(self._leg(1, participant_id="P1"), american_price=+150),
                dict(self._leg(2, participant_id="P2"), american_price=-120)]
        self.assertEqual(driver.resolve_combo_bet(conn, {"legs_json": json.dumps(legs)})["status"], "VOID")

    def test_a_dnp_leg_plus_a_data_gap_leg_stays_unresolved(self):
        conn = _fresh_nhl_conn()
        _insert_game(conn, 1)
        _insert_game(conn, 2)
        legs = [dict(self._leg(1, participant_id="P1"), american_price=+150),
                {"game_id": "2", "market_family": "SOMETHING_UNRECOGNIZED", "threshold": 1,
                 "side": "OVER", "participant_id": "PX", "participant_name": "PX", "american_price": 110}]
        self.assertEqual(driver.resolve_combo_bet(conn, {"legs_json": json.dumps(legs)})["status"], "UNRESOLVED")

    def test_a_data_gap_leg_settles_unresolved_never_guessed(self):
        conn = _fresh_nhl_conn()
        _insert_game(conn, 1)
        _insert_player_stat(conn, 1, "P1", "TOR", shots=5)
        legs = [self._leg(1, participant_id="P1"),
                {"game_id": "1", "market_family": "SOMETHING_UNRECOGNIZED", "threshold": 1,
                 "side": "OVER", "participant_id": "PX", "participant_name": "PX"}]
        bet = {"legs_json": json.dumps(legs)}
        result = driver.resolve_combo_bet(conn, bet)
        self.assertEqual(result["status"], "UNRESOLVED")

    def test_empty_legs_is_unsupported_never_a_guess(self):
        conn = _fresh_nhl_conn()
        result = driver.resolve_combo_bet(conn, {"legs_json": "[]"})
        self.assertEqual(result["status"], resolver.UNSUPPORTED_SETTLEMENT_MARKET)

    def test_standard_player_sog_leg_settles_real_win_not_unsupported(self):
        """Standard SOG/Saves Certification block (2026-10-01): real bug caught
        while wiring real_slate_adapter.py::sog_standard_candidate_legs() into
        the paper trader -- _leg_settlement_market_id() had no case for the
        bare "PLAYER_SOG" market_family (only "PLAYER_SOG_ALTERNATE"), so a
        combo containing a real standard-SOG leg would settle
        UNSUPPORTED_SETTLEMENT_MARKET -> UNRESOLVED forever, never WIN/LOSS."""
        conn = _fresh_nhl_conn()
        _insert_game(conn, 1)
        _insert_player_stat(conn, 1, "P1", "TOR", shots=5)
        bet = {"legs_json": json.dumps([self._leg(1, market_family="PLAYER_SOG", participant_id="P1")])}
        result = driver.resolve_combo_bet(conn, bet)
        self.assertEqual(result["status"], "WIN")

    def test_goalie_saves_leg_settles_real_win_not_unsupported(self):
        """Same real bug, for GOALIE_SAVES -- see test above."""
        conn = _fresh_nhl_conn()
        _insert_game(conn, 1)
        _insert_goalie_stat(conn, 1, "G1", "TOR", saves=27)
        bet = {"legs_json": json.dumps(
            [self._leg(1, market_family="GOALIE_SAVES", threshold=25, participant_id="G1")])}
        result = driver.resolve_combo_bet(conn, bet)
        self.assertEqual(result["status"], "WIN")

    def test_goalie_saves_leg_settles_real_loss(self):
        conn = _fresh_nhl_conn()
        _insert_game(conn, 1)
        _insert_goalie_stat(conn, 1, "G1", "TOR", saves=18)
        bet = {"legs_json": json.dumps(
            [self._leg(1, market_family="GOALIE_SAVES", threshold=25, participant_id="G1")])}
        result = driver.resolve_combo_bet(conn, bet)
        self.assertEqual(result["status"], "LOSS")


class TestSettleDueBetsDriver(unittest.TestCase):
    def test_end_to_end_settles_a_real_pending_bet(self):
        nhl_conn = _fresh_nhl_conn()
        _insert_game(nhl_conn, 1, home="TOR", away="MTL", home_score=4, away_score=2)
        bankroll_conn = _fresh_bankroll_conn()
        pb.record_paper_bet(bankroll_conn, track="REAL_MARKET_PAPER", price_source="LIVE_DRAFTKINGS",
                             market_id="MONEYLINE", entry_odds=-150, event_id="1", team="TOR",
                             event_start_utc="2026-09-28T23:00:00Z")
        summary = driver.settle_due_bets(bankroll_conn, nhl_conn)
        self.assertEqual(summary["scanned"], 1)
        self.assertEqual(summary["settled"], 1)
        rows = pb.query_paper_bets(bankroll_conn, track="REAL_MARKET_PAPER")
        self.assertEqual(rows[0]["result_status"], "WIN")

    def test_a_bet_whose_game_has_not_finished_is_skipped_not_forced(self):
        nhl_conn = _fresh_nhl_conn()
        _insert_game(nhl_conn, 1, state="SCHEDULED")
        bankroll_conn = _fresh_bankroll_conn()
        pb.record_paper_bet(bankroll_conn, track="REAL_MARKET_PAPER", price_source="LIVE_DRAFTKINGS",
                             market_id="MONEYLINE", entry_odds=-150, event_id="1", team="TOR",
                             event_start_utc="2026-09-28T23:00:00Z")
        summary = driver.settle_due_bets(bankroll_conn, nhl_conn)
        self.assertEqual(summary["settled"], 0)
        self.assertEqual(summary["skipped_still_pending"], 1)
        rows = pb.query_paper_bets(bankroll_conn, track="REAL_MARKET_PAPER")
        self.assertEqual(rows[0]["result_status"], "PENDING")

    def test_no_pending_bets_settles_nothing(self):
        nhl_conn = _fresh_nhl_conn()
        bankroll_conn = _fresh_bankroll_conn()
        summary = driver.settle_due_bets(bankroll_conn, nhl_conn)
        self.assertEqual(summary["scanned"], 0)
        self.assertEqual(summary["settled"], 0)


if __name__ == "__main__":
    unittest.main()
