"""
operational/real_parlay_paper_trader.py: the scheduled job end to end against
isolated databases (leg collection is stubbed; selection, recording, funds,
settlement and the state document are real).
"""
from __future__ import annotations

import contextlib
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


def _legs(n, start="2026-09-29T23:00:00Z", price=-105, p=0.60):
    return [ParlayLeg(
        game_id=str(i), event_id=f"evt-{i}", market_family="PLAYER_SOG_ALTERNATE", participant_id=f"P{i}",
        participant_name=f"Player {i}", side="OVER", threshold=3, american_price=price, conservative_probability=p,
        sportsbook="draftkings", captured_at_utc="2026-09-29T17:50:00Z", provider_contract_verified=True,
        model_threshold_eligible=True, identity_resolved=True, price_fresh=True, event_not_started=True,
        team="TOR", opponent="MTL", game_start_utc=start, model_version="test") for i in range(1, n + 1)]


GAMES = [{"game_id": i, "date": "2026-09-29", "start": "2026-09-29T23:00:00", "home": "TOR", "away": "MTL"}
         for i in range(1, 9)]


def _run_with(nhl_path, bankroll_path, legs, now, *, revalidation=None):
    collected = {"legs": legs, "sources": {}, "second_opinion": {}}
    patches = [
        mock.patch.object(db, "get_conn", lambda: _REAL_GET_CONN(nhl_path)),
        mock.patch.object(pb, "init_db", lambda: _REAL_PB_INIT_DB(Path(bankroll_path))),
        mock.patch.object(trader.daily_tickets, "collect_candidate_legs", return_value=collected),
        mock.patch("operational.best_bets.refresh", return_value={"status": "OK", "changed": False}),
        mock.patch("operational.cloud_publish_hook.publish_after", return_value={"status": "SKIPPED"}),
    ]
    if revalidation is not None:
        patches.append(mock.patch.object(trader.bet_revalidation, "revalidate_pending_real_market_bets",
                                         return_value=revalidation))
    with contextlib.ExitStack() as stack:
        for patch in patches:
            stack.enter_context(patch)
        return trader.run(now=now)


def _bankroll():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return tmp.name


NOW1 = dt.datetime(2026, 9, 29, 18, 0, tzinfo=dt.timezone.utc)


class TestTraderRun(unittest.TestCase):
    def test_a_run_records_up_to_five_ten_dollar_tickets(self):
        nhl_path, bankroll = _fresh_nhl_db_with_games(GAMES), _bankroll()
        result = _run_with(nhl_path, bankroll, _legs(8), NOW1)
        self.assertEqual(result["stake_result"]["newly_recorded"], 5)
        conn = pb.init_db(Path(bankroll))
        rows = pb.query_paper_bets(conn, track="REAL_MARKET_PAPER", is_combo=True)
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(r["stake"] == 10.0 and r["event_start_utc"] == "2026-09-29T23:00:00Z" for r in rows))
        self.assertEqual(pb.account_state(conn, "REAL_MARKET_PAPER")["available_cash"], 450.0)

    def test_rerunning_the_same_day_never_double_stakes(self):
        nhl_path, bankroll = _fresh_nhl_db_with_games(GAMES), _bankroll()
        first = _run_with(nhl_path, bankroll, _legs(8), NOW1)
        # 8:30 PM EDT the same Eastern day -- UTC has already rolled to Sept 30.
        second = _run_with(nhl_path, bankroll, _legs(8, price=-110, p=0.62),
                           dt.datetime(2026, 9, 30, 0, 30, tzinfo=dt.timezone.utc))
        self.assertEqual(first["stake_result"]["newly_recorded"], 5)
        self.assertEqual(second["stake_result"]["newly_recorded"], 0)
        conn = pb.init_db(Path(bankroll))
        self.assertEqual(len(pb.query_paper_bets(conn, track="REAL_MARKET_PAPER", is_combo=True)), 5)

    def test_a_new_eastern_day_is_not_blocked(self):
        nhl_path, bankroll = _fresh_nhl_db_with_games(GAMES), _bankroll()
        _run_with(nhl_path, bankroll, _legs(8), NOW1)
        day2 = _run_with(nhl_path, bankroll, _legs(8, start="2026-09-30T23:00:00Z"), dt.datetime(2026, 9, 30, 17, 0, tzinfo=dt.timezone.utc))
        self.assertEqual(day2["stake_result"]["newly_recorded"], 5)
        conn = pb.init_db(Path(bankroll))
        self.assertEqual(pb.account_state(conn, "REAL_MARKET_PAPER")["available_cash"], 400.0)

    def test_no_legs_records_nothing_and_says_why(self):
        nhl_path, bankroll = _fresh_nhl_db_with_games(GAMES), _bankroll()
        result = _run_with(nhl_path, bankroll, [], NOW1)
        self.assertEqual(result["stake_result"]["newly_recorded"], 0)
        self.assertIn("still to start today", result["stake_result"]["reason"])
        self.assertIn("No fresh price qualifies right now", result["stake_result"]["reason"])

    def test_revalidation_alerts_never_change_the_account(self):
        nhl_path, bankroll = _fresh_nhl_db_with_games(GAMES), _bankroll()
        _run_with(nhl_path, bankroll, _legs(8), NOW1)
        conn = pb.init_db(Path(bankroll))
        ticket = pb.query_paper_bets(conn, track="REAL_MARKET_PAPER")[0]
        pb.record_ticket_alert(conn, ticket["paper_bet_id"], "ROSTER_STATUS_CHANGED", "Player 1 is now OUT")
        _run_with(nhl_path, bankroll, _legs(8), NOW1 + dt.timedelta(minutes=15))
        account = pb.account_state(conn, "REAL_MARKET_PAPER")
        self.assertEqual((account["available_cash"], account["open_stakes"]), (450.0, 50.0))

    def test_due_tickets_are_settled_and_reconcile_to_the_account(self):
        games = [dict(g, state="FINAL", home_score=3, away_score=1) for g in GAMES]
        nhl_path, bankroll = _fresh_nhl_db_with_games(games), _bankroll()
        nhl = _REAL_GET_CONN(nhl_path)
        for i in range(1, 9):   # every Player i took 4 shots: all 3+ legs win
            nhl.execute(
                "INSERT INTO player_game_stats (game_id, player_id, team_id, toi_minutes, goals, assists, shots, "
                "hits, blocked_shots, played, revision_number, effective_at_utc, observed_at_utc, source) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?,?)",
                (i, f"P{i}", "TOR", 18.0, 0, 0, 4, None, None, 1, "2026-09-29T23:30:00Z", "2026-09-29T23:30:00Z", "test"))
        nhl.commit()
        nhl.close()
        # Games began on 2026-09-20 (long past), so tickets recorded then are due immediately.
        result = _run_with(nhl_path, bankroll, _legs(8, start="2026-09-20T23:00:00Z"),
                           dt.datetime(2026, 9, 20, 17, 0, tzinfo=dt.timezone.utc))
        self.assertEqual(result["stake_result"]["newly_recorded"], 5)
        self.assertEqual(result["settlement_summary"]["settled"], 5)
        conn = _REAL_PB_INIT_DB(Path(bankroll))
        account = pb.account_state(conn, "REAL_MARKET_PAPER")
        self.assertEqual(account["open_stakes"], 0.0)
        self.assertGreater(account["settled_pnl"], 0.0)
        self.assertAlmostEqual(account["available_cash"], 500.0 + account["settled_pnl"], places=2)
        self.assertTrue(all(r["result_status"] == "WIN" for r in pb.query_paper_bets(conn, track="REAL_MARKET_PAPER")))


class TestPublishHeartbeat(unittest.TestCase):
    def test_a_quiet_board_is_republished_once_the_last_publication_is_old_enough(self):
        import json
        tmp = Path(tempfile.mkdtemp()) / "state.json"
        now = dt.datetime(2026, 10, 7, 19, 0, tzinfo=dt.timezone.utc)
        with mock.patch.object(trader._sp, "path", return_value=tmp):
            self.assertTrue(trader._publish_heartbeat_due(now))                                  # never published
            tmp.write_text(json.dumps({"last_success_at": (now - dt.timedelta(minutes=10)).isoformat()}))
            self.assertFalse(trader._publish_heartbeat_due(now))
            tmp.write_text(json.dumps({"last_success_at": (now - dt.timedelta(minutes=30)).isoformat()}))
            self.assertTrue(trader._publish_heartbeat_due(now))


if __name__ == "__main__":
    unittest.main()
