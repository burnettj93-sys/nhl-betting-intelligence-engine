"""P0.8 (2026-09-24 hardening block): tests for
operational/backup_databases.py -- entirely isolated temp databases and
temp backup directories, never the real operational databases."""
from __future__ import annotations

import datetime as dt
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from operational import backup_databases as bd, ingestion_health

# VPS Production Deployment block (2026-09-24), Part 13: real gap found
# -- run_all_backups() calls ingestion_health.record_run("database_backups",
# ...) unconditionally, and only one test in this file
# (test_records_ingestion_health) isolated it; test_one_failure_does_not_
# abort_the_whole_batch was silently overwriting the REAL
# operational/ingestion_health_cache.json on every run despite this
# file's own docstring's isolation guarantee. Isolated module-wide so no
# future test here can reintroduce the gap.
_health_cache_patcher = None


def setUpModule():
    global _health_cache_patcher
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    _health_cache_patcher = mock.patch.object(ingestion_health, "DEFAULT_CACHE_PATH", Path(tmp.name))
    _health_cache_patcher.start()


def tearDownModule():
    _health_cache_patcher.stop()


def _make_db(path: Path, rows: int = 3) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, value TEXT)")
    for i in range(rows):
        conn.execute("INSERT INTO t (value) VALUES (?)", (f"row-{i}",))
    conn.commit()
    conn.close()


class TestBackupOne(unittest.TestCase):
    def test_backs_up_a_real_database_correctly(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.db"
            _make_db(source, rows=5)
            backup_root = Path(tmp) / "backups"
            result = bd.backup_one("test_db", source, backup_root=backup_root)
            self.assertEqual(result["status"], "SUCCESS")
            backup_path = Path(result["backup_path"])
            self.assertTrue(backup_path.exists())
            conn = sqlite3.connect(backup_path)
            count = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
            self.assertEqual(count, 5)

    def test_missing_source_is_skipped_not_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "does_not_exist.db"
            result = bd.backup_one("test_db", source, backup_root=Path(tmp) / "backups")
            self.assertEqual(result["status"], "SKIPPED")

    def test_backup_is_a_real_independent_copy_not_a_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.db"
            _make_db(source, rows=1)
            backup_root = Path(tmp) / "backups"
            result = bd.backup_one("test_db", source, backup_root=backup_root)
            backup_path = Path(result["backup_path"])

            # Modify the source AFTER backing it up.
            conn = sqlite3.connect(source)
            conn.execute("INSERT INTO t (value) VALUES ('added-after-backup')")
            conn.commit()
            conn.close()

            backup_conn = sqlite3.connect(backup_path)
            count = backup_conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
            self.assertEqual(count, 1)  # backup is frozen at the moment it was taken

    def test_retention_keeps_only_the_most_recent_n_backups(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.db"
            _make_db(source)
            backup_root = Path(tmp) / "backups"
            for i in range(bd.RETENTION_COUNT + 5):
                now = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc) + dt.timedelta(hours=i)
                bd.backup_one("test_db", source, backup_root=backup_root, now=now)
            remaining = sorted((backup_root / "test_db").glob("test_db_*.db"))
            self.assertEqual(len(remaining), bd.RETENTION_COUNT)

    def test_retention_keeps_the_newest_not_the_oldest(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.db"
            _make_db(source)
            backup_root = Path(tmp) / "backups"
            for i in range(bd.RETENTION_COUNT + 2):
                now = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc) + dt.timedelta(hours=i)
                bd.backup_one("test_db", source, backup_root=backup_root, now=now)
            remaining = sorted((backup_root / "test_db").glob("test_db_*.db"))
            # the two OLDEST timestamps must be gone
            self.assertNotIn("20260901T000000Z", remaining[0].name)
            self.assertNotIn("20260901T010000Z", remaining[0].name)


class TestRunAllBackups(unittest.TestCase):
    def test_records_ingestion_health(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_db = Path(tmp) / "fake.db"
            _make_db(fake_db)
            health_cache = Path(tmp) / "health.json"
            with mock.patch.object(bd, "CRITICAL_DATABASES", {"fake": fake_db}), \
                 mock.patch("operational.ingestion_health.DEFAULT_CACHE_PATH", health_cache):
                summary = bd.run_all_backups(backup_root=Path(tmp) / "backups")
            self.assertEqual(summary["status"], "SUCCESS")
            from operational import ingestion_health
            health = ingestion_health.load_health(cache_path=health_cache)
            self.assertIn("database_backups", health)
            self.assertEqual(health["database_backups"]["last_status"], "SUCCESS")

    def test_one_failure_does_not_abort_the_whole_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            good_db = Path(tmp) / "good.db"
            _make_db(good_db)
            bad_db = Path(tmp) / "bad.db"
            bad_db.write_text("not a real sqlite file")  # will fail to back up
            with mock.patch.object(bd, "CRITICAL_DATABASES", {"good": good_db, "bad": bad_db}):
                summary = bd.run_all_backups(backup_root=Path(tmp) / "backups")
            self.assertEqual(summary["results"]["good"]["status"], "SUCCESS")
            self.assertEqual(summary["results"]["bad"]["status"], "FAILED")
            self.assertEqual(summary["status"], "FAILED")  # overall reflects the real failure

    def test_failed_backup_never_leaves_a_partial_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad_db = Path(tmp) / "bad.db"
            bad_db.write_text("not a real sqlite file")
            backup_root = Path(tmp) / "backups"
            result = bd.backup_one("bad", bad_db, backup_root=backup_root)
            self.assertEqual(result["status"], "FAILED")
            leftover = list((backup_root / "bad").glob("*.db")) if (backup_root / "bad").exists() else []
            self.assertEqual(leftover, [])


class TestRestore(unittest.TestCase):
    def test_restore_produces_a_real_independent_working_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.db"
            _make_db(source, rows=7)
            backup_root = Path(tmp) / "backups"
            result = bd.backup_one("test_db", source, backup_root=backup_root)
            restore_target = Path(tmp) / "restored.db"
            bd.restore_from_backup(Path(result["backup_path"]), restore_target)
            conn = sqlite3.connect(restore_target)
            count = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
            self.assertEqual(count, 7)

    def test_restore_never_touches_the_original_backup_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.db"
            _make_db(source)
            backup_root = Path(tmp) / "backups"
            result = bd.backup_one("test_db", source, backup_root=backup_root)
            backup_path = Path(result["backup_path"])
            original_bytes = backup_path.read_bytes()
            bd.restore_from_backup(backup_path, Path(tmp) / "restored.db")
            self.assertEqual(backup_path.read_bytes(), original_bytes)


if __name__ == "__main__":
    unittest.main()
