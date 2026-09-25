"""
Runtime DB hygiene (2026-09-25): the live, scheduler-mutated NHL database
must not be a git-tracked file. Tests for db.py's centralized path
resolution and for the consumers that must follow it. Every test uses
temp files -- none may write the real runtime DB (one test proves that).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import db
from operational import backup_databases as bd, ingestion_health, nhl_sync, system_health

REPO = Path(db.__file__).resolve().parent


def _sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


class TestPathResolution(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        # No real .env / real runtime file may influence resolution.
        self._p1 = mock.patch.object(db, "REPO_ROOT", self.tmp)
        self._p2 = mock.patch.object(db, "RUNTIME_DB_PATH", self.tmp / "operational" / "runtime" / "nhl.db")
        self._p3 = mock.patch.object(db, "LEGACY_DB_PATH", self.tmp / "nhl.db")
        for p in (self._p1, self._p2, self._p3):
            p.start()
        self._env = mock.patch.dict(os.environ, {}, clear=False)
        self._env.start()
        os.environ.pop("NHL_DB_PATH", None)

    def tearDown(self):
        self._env.stop()
        for p in (self._p3, self._p2, self._p1):
            p.stop()
        self._tmp.cleanup()

    def test_default_falls_back_to_the_tracked_snapshot_when_no_runtime_db_exists(self):
        """Fresh clone / Streamlit Community Cloud (tracked files only)."""
        self.assertEqual(db.resolve_db_path(), self.tmp / "nhl.db")

    def test_default_prefers_the_runtime_db_once_it_exists(self):
        db.RUNTIME_DB_PATH.parent.mkdir(parents=True)
        db.RUNTIME_DB_PATH.touch()
        self.assertEqual(db.resolve_db_path(), db.RUNTIME_DB_PATH)

    def test_env_var_wins_over_everything(self):
        db.RUNTIME_DB_PATH.parent.mkdir(parents=True)
        db.RUNTIME_DB_PATH.touch()
        os.environ["NHL_DB_PATH"] = str(self.tmp / "custom.db")
        self.assertEqual(db.resolve_db_path(), self.tmp / "custom.db")

    def test_dotenv_line_is_honored_like_every_other_setting(self):
        (self.tmp / ".env").write_text("THE_ODDS_API_KEY=x\nNHL_DB_PATH=/opt/nhl-engine/data/nhl.db\n")
        self.assertEqual(db.resolve_db_path(), Path("/opt/nhl-engine/data/nhl.db"))

    def test_real_environment_variable_beats_dotenv(self):
        (self.tmp / ".env").write_text("NHL_DB_PATH=/from/dotenv.db\n")
        os.environ["NHL_DB_PATH"] = "/from/env.db"
        self.assertEqual(db.resolve_db_path(), Path("/from/env.db"))


class TestGetConnFollowsTheResolvedPath(unittest.TestCase):
    def test_default_is_looked_up_at_call_time_not_bound_at_definition(self):
        """This codebase's known footgun: a default bound at def time
        silently defeats mock.patch. get_conn()/init_db() must honor a
        patched db.DB_PATH."""
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "elsewhere" / "nhl.db"
            with mock.patch.object(db, "DB_PATH", target):
                conn = db.init_db()
                conn.execute("INSERT INTO teams (team_id) VALUES ('ZZZ')")
                conn.commit()
                conn.close()
            self.assertTrue(target.exists())
            check = sqlite3.connect(target)
            self.assertEqual(check.execute("SELECT COUNT(*) FROM teams WHERE team_id='ZZZ'").fetchone()[0], 1)
            check.close()

    def test_explicit_path_argument_still_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            explicit = Path(tmp) / "explicit.db"
            conn = db.init_db(explicit)
            conn.close()
            self.assertTrue(explicit.exists())


class _OneGameSession:
    def get(self, url, timeout=15):
        class R:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self_inner):
                return {"gameWeek": [{"date": "2026-09-25", "games": [{
                    "id": 2026029999, "season": 20262027, "startTimeUTC": "2026-09-26T23:00:00Z",
                    "homeTeam": {"abbrev": "TOR"}, "awayTeam": {"abbrev": "BOS"}}]}], "nextStartDate": None}
        return R()


class TestSchedulerAndConsumersUseTheResolvedRuntimePath(unittest.TestCase):
    def test_a_real_scheduler_job_writes_to_the_resolved_db_and_never_to_the_tracked_snapshot(self):
        legacy_before = _sha(db.LEGACY_DB_PATH)
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / "runtime" / "nhl.db"
            db.init_db(runtime).close()
            health = Path(tmp) / "health.json"
            with mock.patch.object(db, "DB_PATH", runtime), \
                 mock.patch.object(ingestion_health, "DEFAULT_CACHE_PATH", health):
                result = nhl_sync.run_midday_refresh(today=dt.date(2026, 9, 25), session=_OneGameSession())
            self.assertEqual(result["status"], "SUCCESS")
            check = sqlite3.connect(runtime)
            n = check.execute("SELECT COUNT(*) FROM games WHERE game_id=2026029999").fetchone()[0]
            check.close()
            self.assertEqual(n, 1, "the scheduler job did not write to the resolved runtime DB")
        self.assertEqual(_sha(db.LEGACY_DB_PATH), legacy_before, "the tracked snapshot was modified")

    def test_app_health_check_reads_the_resolved_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / "nhl.db"
            db.init_db(runtime).close()
            with mock.patch.object(db, "DB_PATH", runtime):
                item = system_health.database_health()
        self.assertEqual(item["status"], "OK")
        self.assertIn(str(runtime), item["source"])

    def test_backup_follows_the_resolved_db_not_the_frozen_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / "nhl.db"
            with mock.patch.object(db, "resolve_db_path", return_value=runtime):
                self.assertEqual(bd.critical_sources()["nhl_db"], runtime)

    def test_backup_still_honors_a_patched_critical_databases_dict(self):
        fake = {"nhl_db": Path("/tmp/patched.db")}
        with mock.patch.object(bd, "CRITICAL_DATABASES", fake):
            self.assertEqual(bd.critical_sources(), fake)


class TestGitStaysCleanAndRealRuntimeDbIsNeverTouchedByTests(unittest.TestCase):
    def test_runtime_directory_is_gitignored_and_untracked(self):
        ignored = subprocess.run(["git", "check-ignore", "-q", "operational/runtime/nhl.db"], cwd=REPO)
        self.assertEqual(ignored.returncode, 0, "operational/runtime/ must be gitignored")
        tracked = subprocess.run(["git", "ls-files", "operational/runtime"], cwd=REPO,
                                 capture_output=True, text=True).stdout.strip()
        self.assertEqual(tracked, "")

    def test_a_write_to_the_runtime_path_does_not_appear_in_git_status(self):
        """Representative scheduler write: create/modify a file at the
        runtime path inside a throwaway clone of the ignore rules."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            (repo / ".gitignore").write_text((REPO / ".gitignore").read_text())
            runtime = repo / "operational" / "runtime" / "nhl.db"
            runtime.parent.mkdir(parents=True)
            conn = sqlite3.connect(runtime)
            conn.execute("CREATE TABLE t (x)")
            conn.execute("INSERT INTO t VALUES (1)")
            conn.commit()
            conn.close()
            status = subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                                    capture_output=True, text=True).stdout
        self.assertNotIn("nhl.db", status)

    def test_running_the_sync_test_classes_never_touches_the_real_runtime_db(self):
        real = db.resolve_db_path()
        before = (_sha(real), _sha(db.LEGACY_DB_PATH))
        loader, suite = unittest.TestLoader(), unittest.TestSuite()
        for cls in ("TestNHLSyncIdempotency", "TestNHLSyncWindow", "TestNHLSyncRosterDegradationIsNonCritical"):
            suite.addTests(loader.loadTestsFromName(f"tests.test_operational_daily_sync.{cls}"))
        result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
        self.assertTrue(result.wasSuccessful())
        self.assertEqual((_sha(real), _sha(db.LEGACY_DB_PATH)), before)


if __name__ == "__main__":
    unittest.main()
