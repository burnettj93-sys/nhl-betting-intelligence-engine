"""
Hits/Blocked-Shots Settlement Enablement block (2026-09-29): the additive
column/table migration db.py runs on every get_conn() call. No destructive
rebuild, no data loss, idempotent, safe against a real pre-existing runtime
DB that predates these columns.
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import db


class TestAdditiveColumnMigration(unittest.TestCase):
    def _old_shape_db(self) -> Path:
        """A DB built from schema.sql's player_game_stats shape BEFORE this block -- no hits/
        blocked_shots columns, no team_game_stats table -- simulating a real pre-existing runtime DB."""
        path = Path(tempfile.mkdtemp()) / "old.db"
        conn = sqlite3.connect(path)
        conn.execute("""CREATE TABLE player_game_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT, game_id INTEGER, player_id TEXT, team_id TEXT,
            toi_minutes REAL, goals INTEGER, assists INTEGER, shots INTEGER, played INTEGER DEFAULT 1,
            revision_number INTEGER DEFAULT 1, effective_at_utc TEXT, observed_at_utc TEXT, source TEXT)""")
        conn.execute("INSERT INTO player_game_stats (game_id, player_id, team_id, goals, assists, shots) "
                     "VALUES (2025020123, '8478402', 'TOR', 2, 1, 6)")
        conn.commit()
        conn.close()
        return path

    def test_upgrading_an_existing_db_adds_columns_without_losing_data(self):
        path = self._old_shape_db()
        conn = db.get_conn(path)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(player_game_stats)")}
        self.assertIn("hits", cols)
        self.assertIn("blocked_shots", cols)
        row = conn.execute("SELECT goals, assists, shots, hits, blocked_shots FROM player_game_stats "
                            "WHERE player_id='8478402'").fetchone()
        self.assertEqual(tuple(row[:3]), (2, 1, 6))     # pre-existing data untouched
        self.assertIsNone(row[3])                        # new column: NULL, never guessed/backfilled
        self.assertIsNone(row[4])
        conn.close()

    def test_migration_creates_team_game_stats_table(self):
        path = self._old_shape_db()
        conn = db.get_conn(path)
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='team_game_stats'").fetchone()
        self.assertIsNotNone(exists)
        conn.close()

    def test_running_the_migration_twice_is_a_harmless_no_op(self):
        path = self._old_shape_db()
        conn1 = db.get_conn(path)
        conn1.execute("UPDATE player_game_stats SET hits=3 WHERE player_id='8478402'")
        conn1.commit()
        conn1.close()
        conn2 = db.get_conn(path)   # second connection -- migration runs again, must not error or reset data
        row = conn2.execute("SELECT hits FROM player_game_stats WHERE player_id='8478402'").fetchone()
        self.assertEqual(row[0], 3)
        conn2.close()

    def test_a_brand_new_db_from_init_db_already_has_the_new_shape(self):
        path = Path(tempfile.mkdtemp()) / "fresh.db"
        conn = db.init_db(path)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(player_game_stats)")}
        self.assertIn("hits", cols)
        self.assertIn("blocked_shots", cols)
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='team_game_stats'").fetchone()
        self.assertIsNotNone(exists)
        conn.close()

    def test_migration_never_touches_moneyline_or_odds_tables(self):
        """Structural proof this block never touched moneyline/T-35 -- the migration function only
        knows about player_game_stats and team_game_stats, nothing odds/moneyline-related."""
        import inspect
        src = inspect.getsource(db._migrate_additive_columns) + repr(db._ADDITIVE_COLUMN_MIGRATIONS) + db._TEAM_GAME_STATS_DDL
        for forbidden in ("odds", "moneyline", "draftkings"):
            self.assertNotIn(forbidden, src.lower())


if __name__ == "__main__":
    unittest.main()
