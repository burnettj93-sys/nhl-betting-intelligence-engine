"""A unit-test run can never open, migrate or write the production ledgers (found 2026-10-17: a branch test run migrated the live paper_bankroll.db through a dashboard page's default path)."""
from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

from operational import paper_bankroll as pb
from operational import prospective_ledger as pl
from operational import state_paths


def digest(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


class TestProductionLedgersAreNeverOpenedByATest(unittest.TestCase):
    def test_the_default_path_resolves_to_a_throwaway_file_not_the_production_file(self):
        for mod, prod in ((pb, pb.DB_PATH), (pl, pl.DB_PATH)):
            redirected = state_paths.guard_database(prod)
            self.assertNotEqual(redirected.resolve(), Path(prod).resolve(), mod.__name__)
            self.assertIn("nhl_engine_test_state_", str(redirected))

    def test_opening_and_initialising_by_the_default_path_leaves_the_production_file_byte_identical(self):
        before = {p: digest(Path(p)) for p in (pb.DB_PATH, pl.DB_PATH)}
        conn = pb.init_db()                                   # the dashboard's and the trader's default call
        self.assertEqual(pb.active_experiment(conn)["experiment_id"], "EXP-1")        # a fresh isolated book, not the production one
        self.assertEqual(pb.account_state(conn, "REAL_MARKET_PAPER")["available_cash"], 500.0)
        conn.close()
        pl.init_db().close()
        self.assertEqual({p: digest(Path(p)) for p in (pb.DB_PATH, pl.DB_PATH)}, before)

    def test_a_direct_read_write_open_of_a_production_ledger_is_refused_but_read_only_is_allowed(self):
        import sqlite3
        with self.assertRaises(AssertionError):
            sqlite3.connect(str(pb.DB_PATH))
        with self.assertRaises(AssertionError):
            sqlite3.connect(f"file:{pl.DB_PATH}?mode=rwc", uri=True)
        sqlite3.connect(f"file:{pb.DB_PATH}?mode=ro", uri=True).close()                       # reading is fine

    def test_a_real_temporary_path_is_not_redirected_and_memory_is_untouched(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(state_paths.guard_database(Path(d) / "x.db"), Path(d) / "x.db")
        self.assertEqual(str(state_paths.guard_database(":memory:")), ":memory:")

    def test_the_operator_state_dir_override_alone_never_redirects_a_ledger(self):
        from unittest import mock
        with mock.patch.object(state_paths, "under_test", return_value=False), mock.patch.dict("os.environ", {"NHL_ENGINE_STATE_DIR": "/tmp/somewhere"}):
            self.assertEqual(state_paths.guard_database(pb.DB_PATH), Path(pb.DB_PATH))

    def test_outside_a_test_run_the_path_is_unchanged(self):
        from unittest import mock
        with mock.patch.object(state_paths, "state_dir", return_value=None):
            self.assertEqual(state_paths.guard_database(pb.DB_PATH), Path(pb.DB_PATH))


if __name__ == "__main__":
    unittest.main()
