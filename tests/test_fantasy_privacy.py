"""
Privacy/multi-user isolation tests (Part 147). A tempfile-backed store
per test -- never touches the real fantasy_store.db.
"""
from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path

from fantasy.storage import fantasy_store


class TestUserIsolation(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test_fantasy_store.db"
        self.conn = fantasy_store.get_connection(self.db_path)

    def tearDown(self):
        self.conn.close()
        self._tmpdir.cleanup()

    def test_user_a_cannot_read_user_b_token(self):
        fantasy_store.save_token(self.conn, "user_a", "token_a", "refresh_a", 9999999999.0)
        fantasy_store.save_token(self.conn, "user_b", "token_b", "refresh_b", 9999999999.0)
        row_a = fantasy_store.load_token_row(self.conn, "user_a")
        row_b = fantasy_store.load_token_row(self.conn, "user_b")
        self.assertEqual(row_a["access_token"], "token_a")
        self.assertEqual(row_b["access_token"], "token_b")
        self.assertNotEqual(row_a["access_token"], row_b["access_token"])

    def test_user_a_cannot_read_user_b_league_selection(self):
        fantasy_store.save_selection(self.conn, "user_a", "nhl", "nhl.l.1", "nhl.l.1.t.1")
        fantasy_store.save_selection(self.conn, "user_b", "nhl", "nhl.l.2", "nhl.l.2.t.5")
        sel_a = fantasy_store.load_selection(self.conn, "user_a")
        sel_b = fantasy_store.load_selection(self.conn, "user_b")
        self.assertEqual(sel_a["league_key"], "nhl.l.1")
        self.assertEqual(sel_b["league_key"], "nhl.l.2")

    def test_user_a_cannot_read_user_b_roster_snapshot(self):
        fantasy_store.record_roster_snapshot(self.conn, "user_a", "nhl.l.1", "nhl.l.1.t.1", [{"name": "Player A"}])
        fantasy_store.record_roster_snapshot(self.conn, "user_b", "nhl.l.2", "nhl.l.2.t.5", [{"name": "Player B"}])
        roster_a = fantasy_store.latest_roster_snapshot(self.conn, "user_a", "nhl.l.1.t.1")
        roster_b_attempt = fantasy_store.latest_roster_snapshot(self.conn, "user_a", "nhl.l.2.t.5")
        self.assertEqual(roster_a["roster"][0]["name"], "Player A")
        self.assertIsNone(roster_b_attempt)  # user_a querying user_b's team_key gets nothing

    def test_disconnect_only_removes_that_users_tokens(self):
        fantasy_store.save_token(self.conn, "user_a", "t", "r", 9999999999.0)
        fantasy_store.save_token(self.conn, "user_b", "t", "r", 9999999999.0)
        fantasy_store.disconnect(self.conn, "user_a")
        self.assertIsNone(fantasy_store.load_token_row(self.conn, "user_a"))
        self.assertIsNotNone(fantasy_store.load_token_row(self.conn, "user_b"))

    def test_disconnect_preserves_recommendation_history(self):
        fantasy_store.save_token(self.conn, "user_a", "t", "r", 9999999999.0)
        fantasy_store.record_recommendation(self.conn, user_key="user_a", league_key="L", team_key="T",
                                             player_id="P1", recommendation_type="START", reason="test",
                                             projection=None, confidence="HIGH")
        fantasy_store.disconnect(self.conn, "user_a")
        recs = fantasy_store.recommendations_for_team(self.conn, "user_a", "T")
        self.assertEqual(len(recs), 1)  # Part 119: disconnect never deletes recommendation history


class TestNoGlobalUserState(unittest.TestCase):
    """Every fantasy_store.py function requires an explicit user_key
    parameter -- there is no function that reads/writes without one."""

    def test_every_public_function_requires_user_key_argument(self):
        for name, func in inspect.getmembers(fantasy_store, inspect.isfunction):
            if name.startswith("_") or name in ("get_connection",):
                continue
            sig = inspect.signature(func)
            self.assertIn("user_key", sig.parameters,
                          f"{name} has no user_key parameter -- risks global/shared state")


class TestRecommendationLedgerImmutability(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.conn = fantasy_store.get_connection(Path(self._tmpdir.name) / "test.db")

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_recording_twice_creates_two_rows_never_overwrites(self):
        fantasy_store.record_recommendation(self.conn, user_key="u", league_key="L", team_key="T",
                                             player_id="P1", recommendation_type="ADD", reason="first",
                                             projection=None, confidence="HIGH")
        fantasy_store.record_recommendation(self.conn, user_key="u", league_key="L", team_key="T",
                                             player_id="P1", recommendation_type="ADD", reason="second",
                                             projection=None, confidence="MEDIUM")
        recs = fantasy_store.recommendations_for_team(self.conn, "u", "T")
        self.assertEqual(len(recs), 2)
        reasons = {r["reason"] for r in recs}
        self.assertEqual(reasons, {"first", "second"})

    def test_new_recommendation_defaults_to_not_executed(self):
        fantasy_store.record_recommendation(self.conn, user_key="u", league_key="L", team_key="T",
                                             player_id="P1", recommendation_type="STREAM", reason="r",
                                             projection=None, confidence="HIGH")
        rec = fantasy_store.recommendations_for_team(self.conn, "u", "T")[0]
        self.assertEqual(rec["executed"], 0)  # RECOMMENDED, never assumed EXECUTED (Part 88)


if __name__ == "__main__":
    unittest.main()
