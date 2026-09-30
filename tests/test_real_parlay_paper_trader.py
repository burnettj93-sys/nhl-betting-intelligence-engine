"""
Platform Recovery block (2026-09-29): proves the missing link is actually
closed -- a real, qualifying cross-game parlay is staked as a real $10
paper bet against the real paper_bankroll schema, staking is idempotent
per Eastern calendar day, and settlement (previously "manual invocation
only") now runs and correctly resolves a real WIN, feeding bankroll_summary
exactly as Paper Performance and the daily postmortem already read it.
"""
from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import db
from operational import paper_bankroll as pb
from operational import real_parlay_paper_trader as trader
from research.real_market_parlay.engine import ParlayLeg

_REAL_GET_CONN = db.get_conn
_REAL_PB_INIT_DB = pb.init_db


def _leg(game_id, market_family="MONEYLINE", threshold=None, participant_id="TOR",
         conservative_probability=0.90, american_price=-150, side="HOME"):
    return ParlayLeg(
        game_id=game_id, event_id=f"evt-{game_id}", market_family=market_family,
        participant_id=participant_id, participant_name=participant_id, side=side, threshold=threshold,
        american_price=american_price, conservative_probability=conservative_probability,
        sportsbook="draftkings", captured_at_utc="2026-09-29T18:00:00Z",
        provider_contract_verified=True, model_threshold_eligible=True, identity_resolved=True,
        price_fresh=True, event_not_started=True)


def _fresh_nhl_db_with_games(games: list[dict]) -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = db.init_db(db_path=Path(tmp.name), wipe=True)
    for g in games:
        for t in (g["home"], g["away"]):
            conn.execute("INSERT OR IGNORE INTO teams (team_id, full_name) VALUES (?, ?)", (t, t))
        conn.execute(
            "INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team, away_team, "
            "schedule_observed_at_utc, game_state, home_score, away_score, source) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (g["game_id"], "20262027", g["date"], g["start"], g["home"], g["away"], g["start"],
             g.get("state", "SCHEDULED"), g.get("home_score"), g.get("away_score"), "test"))
    conn.commit()
    conn.close()
    return Path(tmp.name)


class TestQualifyingParlayIsStaked(unittest.TestCase):
    def test_a_qualifying_parlay_stakes_a_real_ten_dollar_bet(self):
        nhl_path = _fresh_nhl_db_with_games([
            {"game_id": 1, "date": "2026-09-29", "start": "2026-09-29T23:00:00", "home": "TOR", "away": "MTL"},
            {"game_id": 2, "date": "2026-09-29", "start": "2026-09-29T23:30:00", "home": "BOS", "away": "NYR"},
            {"game_id": 3, "date": "2026-09-29", "start": "2026-09-30T00:00:00", "home": "EDM", "away": "VAN"},
        ])
        legs = [_leg("1", conservative_probability=0.90), _leg("2", conservative_probability=0.90),
                _leg("3", conservative_probability=0.90)]
        bankroll_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        bankroll_tmp.close()

        with mock.patch.object(db, "get_conn", lambda: _REAL_GET_CONN(nhl_path)), \
             mock.patch.object(pb, "init_db", lambda: _REAL_PB_INIT_DB(Path(bankroll_tmp.name))), \
             mock.patch("research.real_market_parlay.real_slate_adapter.moneyline_candidate_legs",
                        return_value=(legs, [])), \
             mock.patch("research.real_market_parlay.real_slate_adapter.sog_alternate_candidate_legs",
                        return_value=([], [])), \
             mock.patch.object(trader, "_recent_archive_payloads", return_value=[]):
            result = trader.run(now=dt.datetime(2026, 9, 29, 18, 0, tzinfo=dt.timezone.utc))

        self.assertEqual(result["stake_result"]["status"], "INSERTED")
        self.assertEqual(result["stake_result"]["recommended_legs"], 3)

        bankroll_conn = pb.init_db(Path(bankroll_tmp.name))
        rows = pb.query_paper_bets(bankroll_conn, track="REAL_MARKET_PAPER", is_combo=True)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stake"], 10.00)
        self.assertEqual(rows[0]["event_start_utc"], "2026-09-29T23:00:00")  # earliest of the 3 games

    def test_rerunning_the_same_eastern_day_never_double_stakes(self):
        nhl_path = _fresh_nhl_db_with_games([
            {"game_id": 1, "date": "2026-09-29", "start": "2026-09-29T23:00:00", "home": "TOR", "away": "MTL"},
            {"game_id": 2, "date": "2026-09-29", "start": "2026-09-29T23:30:00", "home": "BOS", "away": "NYR"},
            {"game_id": 3, "date": "2026-09-29", "start": "2026-09-30T00:00:00", "home": "EDM", "away": "VAN"},
        ])
        legs = [_leg("1", conservative_probability=0.90), _leg("2", conservative_probability=0.90),
                _leg("3", conservative_probability=0.90)]
        bankroll_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        bankroll_tmp.close()

        with mock.patch.object(db, "get_conn", lambda: _REAL_GET_CONN(nhl_path)), \
             mock.patch.object(pb, "init_db", lambda: _REAL_PB_INIT_DB(Path(bankroll_tmp.name))), \
             mock.patch("research.real_market_parlay.real_slate_adapter.moneyline_candidate_legs",
                        return_value=(legs, [])), \
             mock.patch("research.real_market_parlay.real_slate_adapter.sog_alternate_candidate_legs",
                        return_value=([], [])), \
             mock.patch.object(trader, "_recent_archive_payloads", return_value=[]):
            first = trader.run(now=dt.datetime(2026, 9, 29, 18, 0, tzinfo=dt.timezone.utc))
            # 8:30 PM EDT later the same Eastern day -- UTC has already rolled to Sept 30.
            second = trader.run(now=dt.datetime(2026, 9, 30, 0, 30, tzinfo=dt.timezone.utc))

        self.assertEqual(first["stake_result"]["status"], "INSERTED")
        self.assertEqual(second["stake_result"]["status"], "ALREADY_STAKED_TODAY")

        bankroll_conn = pb.init_db(Path(bankroll_tmp.name))
        rows = pb.query_paper_bets(bankroll_conn, track="REAL_MARKET_PAPER", is_combo=True)
        self.assertEqual(len(rows), 1, "must never stake a second $10 bet for the same Eastern day")

    def test_no_qualifying_parlay_stakes_nothing(self):
        nhl_path = _fresh_nhl_db_with_games([])
        bankroll_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        bankroll_tmp.close()

        with mock.patch.object(db, "get_conn", lambda: _REAL_GET_CONN(nhl_path)), \
             mock.patch.object(pb, "init_db", lambda: _REAL_PB_INIT_DB(Path(bankroll_tmp.name))), \
             mock.patch("research.real_market_parlay.real_slate_adapter.moneyline_candidate_legs",
                        return_value=([], [])), \
             mock.patch("research.real_market_parlay.real_slate_adapter.sog_alternate_candidate_legs",
                        return_value=([], [])), \
             mock.patch.object(trader, "_recent_archive_payloads", return_value=[]):
            result = trader.run()

        self.assertEqual(result["stake_result"]["status"], "NO_QUALIFYING_PARLAY")
        bankroll_conn = pb.init_db(Path(bankroll_tmp.name))
        self.assertEqual(pb.query_paper_bets(bankroll_conn, track="REAL_MARKET_PAPER"), [])


class TestFullLifecycleThroughSettlement(unittest.TestCase):
    def test_a_staked_parlay_settles_win_and_feeds_bankroll_summary(self):
        # Two already-FINAL games: TOR beat MTL (leg picks TOR -> WIN), BOS beat NYR (leg picks BOS -> WIN).
        nhl_path = _fresh_nhl_db_with_games([
            {"game_id": 1, "date": "2026-09-29", "start": "2026-09-29T23:00:00", "home": "TOR", "away": "MTL",
             "state": "FINAL", "home_score": 4, "away_score": 2},
            {"game_id": 2, "date": "2026-09-29", "start": "2026-09-29T23:30:00", "home": "BOS", "away": "NYR",
             "state": "FINAL", "home_score": 3, "away_score": 1},
            {"game_id": 3, "date": "2026-09-29", "start": "2026-09-30T00:00:00", "home": "EDM", "away": "VAN",
             "state": "FINAL", "home_score": 5, "away_score": 2},
        ])
        legs = [_leg("1", participant_id="TOR", side="HOME", conservative_probability=0.90),
                _leg("2", participant_id="BOS", side="HOME", conservative_probability=0.90),
                _leg("3", participant_id="EDM", side="HOME", conservative_probability=0.90)]
        bankroll_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        bankroll_tmp.close()

        with mock.patch.object(db, "get_conn", lambda: _REAL_GET_CONN(nhl_path)), \
             mock.patch.object(pb, "init_db", lambda: _REAL_PB_INIT_DB(Path(bankroll_tmp.name))), \
             mock.patch("research.real_market_parlay.real_slate_adapter.moneyline_candidate_legs",
                        return_value=(legs, [])), \
             mock.patch("research.real_market_parlay.real_slate_adapter.sog_alternate_candidate_legs",
                        return_value=([], [])), \
             mock.patch.object(trader, "_recent_archive_payloads", return_value=[]):
            result = trader.run(now=dt.datetime(2026, 9, 30, 3, 0, tzinfo=dt.timezone.utc))

        self.assertEqual(result["stake_result"]["status"], "INSERTED")
        self.assertEqual(result["settlement_summary"]["settled"], 1)
        self.assertEqual(result["settlement_summary"]["results"][0]["status"], "WIN")

        bankroll_conn = pb.init_db(Path(bankroll_tmp.name))
        summary = pb.bankroll_summary(bankroll_conn, "REAL_MARKET_PAPER")
        self.assertEqual(summary["wins"], 1)
        self.assertEqual(summary["losses"], 0)
        self.assertGreater(summary["net_profit"], 0.0)
        self.assertEqual(summary["hit_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
