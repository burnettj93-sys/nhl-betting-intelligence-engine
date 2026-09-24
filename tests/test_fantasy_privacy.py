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

    def test_token_functions_are_removed_not_silently_reused(self):
        """Yahoo compliance rebuild (2026-09-24): save_token/load_token_row
        used to store tokens as PLAIN TEXT and modeled multiple users --
        both wrong for the actual architecture (single ADMIN-only Yahoo
        connection, tokens encrypted at rest). See
        fantasy/yahoo/token_store.py and tests/test_yahoo_token_store.py
        for the real replacement and its own coverage."""
        with self.assertRaises(fantasy_store._PlaintextTokenStorageRemoved):
            fantasy_store.save_token(self.conn, "user_a", "token_a", "refresh_a", 9999999999.0)
        with self.assertRaises(fantasy_store._PlaintextTokenStorageRemoved):
            fantasy_store.load_token_row(self.conn, "user_a")

    def test_user_a_cannot_read_user_b_league_selection(self):
        fantasy_store.save_selection(self.conn, "user_a", "nhl", "nhl.l.1", "nhl.l.1.t.1")
        fantasy_store.save_selection(self.conn, "user_b", "nhl", "nhl.l.2", "nhl.l.2.t.5")
        sel_a = fantasy_store.load_selection(self.conn, "user_a")
        sel_b = fantasy_store.load_selection(self.conn, "user_b")
        self.assertEqual(sel_a["league_key"], "nhl.l.1")
        self.assertEqual(sel_b["league_key"], "nhl.l.2")

    def test_roster_settings_and_standings_snapshots_are_removed(self):
        """Yahoo compliance rebuild (2026-09-24): these persisted actual
        Yahoo Fantasy Information (roster/settings/standings contents) --
        prohibited outright by the signed agreement's Section 2.c.vii.
        There is no compliant replacement that persists this data; the
        replacement is transient per-request fetch (fantasy/yahoo/
        diagnostic.py), so there's nothing to redirect these to."""
        with self.assertRaises(fantasy_store._FantasyInformationPersistenceRemoved):
            fantasy_store.record_roster_snapshot(self.conn, "user_a", "nhl.l.1", "nhl.l.1.t.1", [{"name": "Player A"}])
        with self.assertRaises(fantasy_store._FantasyInformationPersistenceRemoved):
            fantasy_store.latest_roster_snapshot(self.conn, "user_a", "nhl.l.1.t.1")
        with self.assertRaises(fantasy_store._FantasyInformationPersistenceRemoved):
            fantasy_store.record_league_settings_snapshot(self.conn, "user_a", "nhl.l.1", {}, "hash")
        with self.assertRaises(fantasy_store._FantasyInformationPersistenceRemoved):
            fantasy_store.record_standings_snapshot(self.conn, "user_a", "nhl.l.1", [])

    def test_disconnect_preserves_recommendation_history(self):
        fantasy_store.record_recommendation(self.conn, user_key="user_a", league_key="L", team_key="T",
                                             player_id="P1", recommendation_type="START", reason="test",
                                             projection=None, confidence="HIGH")
        fantasy_store.disconnect(self.conn, "user_a")
        recs = fantasy_store.recommendations_for_team(self.conn, "user_a", "T")
        self.assertEqual(len(recs), 1)  # Part 119: disconnect never deletes recommendation history


class TestNoGlobalUserState(unittest.TestCase):
    """Every fantasy_store.py function requires an explicit user_key
    parameter -- there is no function that reads/writes without one."""

    # Yahoo compliance rebuild (2026-09-24): these module-level names are
    # now bound to a shared guard function (raises unconditionally,
    # *_args/**_kwargs signature) rather than a real per-user accessor --
    # see fantasy/storage/fantasy_store.py's own comments. They're
    # excluded here deliberately, not because the user_key discipline
    # stopped mattering, but because there is no real function body left
    # to check it against.
    _REMOVED_FUNCTION_NAMES = frozenset({
        "save_token", "load_token_row",
        "record_league_settings_snapshot", "latest_league_settings_snapshot",
        "record_roster_snapshot", "latest_roster_snapshot",
        "record_standings_snapshot", "latest_standings_snapshot",
    })

    def test_every_public_function_requires_user_key_argument(self):
        for name, func in inspect.getmembers(fantasy_store, inspect.isfunction):
            if name.startswith("_") or name in ("get_connection",) or name in self._REMOVED_FUNCTION_NAMES:
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
