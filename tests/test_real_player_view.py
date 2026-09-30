"""
Platform Recovery block (2026-09-30): proves dashboard/pages/30_Players.py
and dashboard/pages/25_Player_Intelligence.py's new real default views --
real identity, real current team, real today's opponent, and an honest
MARKET_UNAVAILABLE when no real eligible leg exists for that player
(cross-referenced by player_id against the same rows Player Props shows,
never a second source of truth).
"""
from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path

import db
from dashboard import real_player_view as rpv


def _fresh_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = db.init_db(db_path=Path(tmp.name), wipe=True)
    for t in ("TOR", "MTL"):
        conn.execute("INSERT OR IGNORE INTO teams (team_id, full_name) VALUES (?, ?)", (t, t))
    conn.execute("INSERT INTO players (player_id, full_name, position) VALUES (?, ?, ?)",
                 ("P1", "Test Forward", "F"))
    conn.execute("INSERT INTO team_membership_events (player_id, team_id, effective_at_utc, observed_at_utc, "
                 "event_type, source) VALUES (?,?,?,?,?,?)",
                 ("P1", "TOR", "2026-07-01T00:00:00", "2026-07-01T00:00:00", "SIGNING", "test"))
    conn.execute(
        "INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team, away_team, "
        "schedule_observed_at_utc, game_state, source) VALUES (?,?,?,?,?,?,?,?,?)",
        (1, "20262027", "2026-09-29", "2026-09-29T23:00:00", "TOR", "MTL", "2026-09-29T12:00:00", "SCHEDULED", "test"))
    conn.commit()
    return conn


class TestNeverImportsDemoData(unittest.TestCase):
    def test_module_never_imports_the_demo_data_module(self):
        import inspect
        self.assertNotIn("demo_data", inspect.getsource(rpv))


class TestRealPlayerIdentityAndSearch(unittest.TestCase):
    def test_search_finds_a_real_player_by_partial_name(self):
        conn = _fresh_db()
        results = rpv.search_real_players(conn, "Forward")
        self.assertEqual([r["player_id"] for r in results], ["P1"])

    def test_identity_includes_real_current_team(self):
        conn = _fresh_db()
        identity = rpv.real_player_identity(conn, "P1")
        self.assertEqual(identity["team"], "TOR")

    def test_unknown_player_id_is_none_never_fabricated(self):
        conn = _fresh_db()
        self.assertIsNone(rpv.real_player_identity(conn, "NOPE"))


class TestRealMarketStateNeverASecondSourceOfTruth(unittest.TestCase):
    def test_a_player_with_a_real_eligible_leg_shows_it_verbatim(self):
        conn = _fresh_db()
        now = dt.datetime(2026, 9, 29, 18, 0, tzinfo=dt.timezone.utc)
        fake_row = {"player_id": "P1", "player": "Test Forward", "conservative_probability": 0.6}
        state = rpv.build_real_player_state(conn, "P1", now=now, real_sog_rows=[fake_row])
        self.assertEqual(state["market_state"], fake_row)

    def test_a_player_with_no_real_eligible_leg_is_honestly_market_unavailable(self):
        conn = _fresh_db()
        now = dt.datetime(2026, 9, 29, 18, 0, tzinfo=dt.timezone.utc)
        state = rpv.build_real_player_state(conn, "P1", now=now, real_sog_rows=[])
        self.assertEqual(state["market_state"], rpv.MARKET_UNAVAILABLE)

    def test_todays_real_opponent_is_included_when_the_players_team_plays(self):
        conn = _fresh_db()
        now = dt.datetime(2026, 9, 29, 18, 0, tzinfo=dt.timezone.utc)
        state = rpv.build_real_player_state(conn, "P1", now=now, real_sog_rows=[])
        self.assertEqual(state["today_opponent"]["opponent"], "MTL")


if __name__ == "__main__":
    unittest.main()
