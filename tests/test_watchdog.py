import datetime as dt
import json
import unittest

from operational import watchdog as wd

NOW = dt.datetime(2026, 10, 9, 12, 0, tzinfo=dt.timezone.utc)
LAUNCHCTL = "\n".join(f"-\t0\t{j}" for j in wd.EXPECTED_JOBS)


class TestChecks(unittest.TestCase):
    def test_all_jobs_loaded_is_ok_and_a_missing_job_is_fail(self):
        self.assertEqual(wd.check_jobs(LAUNCHCTL)["status"], wd.OK)
        partial = "\n".join(LAUNCHCTL.splitlines()[1:])
        r = wd.check_jobs(partial)
        self.assertEqual(r["status"], wd.FAIL)
        self.assertIn("real-parlay-paper-trader", r["detail"])

    def test_a_job_that_last_exited_with_an_error_is_a_warning(self):
        bad = LAUNCHCTL.replace(f"-\t0\t{wd.EXPECTED_JOBS[1]}", f"-\t78\t{wd.EXPECTED_JOBS[1]}")
        self.assertEqual(wd.check_jobs(bad)["status"], wd.WARN)

    def test_release_checks(self):
        self.assertEqual(wd.check_release("a" * 40, False, "a" * 40)["status"], wd.OK)
        self.assertEqual(wd.check_release("a" * 40, False, "b" * 40)["status"], wd.WARN)
        self.assertEqual(wd.check_release("a" * 40, True, "a" * 40)["status"], wd.FAIL)
        self.assertEqual(wd.check_release(None, False, "a" * 40)["status"], wd.FAIL)
        self.assertEqual(wd.check_release("a" * 40, False, None)["status"], wd.WARN)

    def test_ages(self):
        self.assertEqual(wd.check_age("t", "2026-10-09T11:50:00Z", 45, NOW, "x")["status"], wd.OK)
        self.assertEqual(wd.check_age("t", "2026-10-09T10:00:00Z", 45, NOW, "x")["status"], wd.FAIL)
        self.assertEqual(wd.check_age("t", None, 45, NOW, "x")["status"], wd.FAIL)


class TestRun(unittest.TestCase):
    def runner(self, cmd, cwd=None, timeout=30):
        if cmd[0] == "launchctl":
            return LAUNCHCTL
        if cmd[:2] == ["git", "rev-parse"]:
            return "c" * 40 + "\n"
        if cmd[:2] == ["git", "status"]:
            return ""
        if cmd[:2] == ["git", "ls-remote"]:
            return "c" * 40 + "\trefs/heads/master\n"
        raise AssertionError(cmd)

    def test_run_writes_state_and_view_reages_it(self):
        state = wd.run(NOW, runner=self.runner, notify=False, deep=False)
        self.assertIn(state["status"], (wd.OK, wd.WARN, wd.FAIL))
        self.assertEqual({c["name"] for c in state["checks"]}, {"jobs_loaded", "release_pinned", "trader_recent", "publish_recent", "database_path", "publishing_enabled"})
        stored = wd.load_state()
        self.assertEqual(stored["checked_at_utc"], "2026-10-09T12:00:00Z")
        self.assertEqual(wd.view(stored, NOW + dt.timedelta(minutes=20))["status"], stored["status"])
        late = wd.view(stored, NOW + dt.timedelta(minutes=120))
        self.assertEqual(late["status"], wd.FAIL)
        self.assertIn("watchdog itself", late["message"])
        self.assertEqual(wd.view(None)["status"], "UNKNOWN")

    def test_state_contains_no_secret_like_keys(self):
        wd.run(NOW, runner=self.runner, notify=False, deep=False)
        from operational import cloud_snapshot_schema as schema
        blob = json.dumps(wd.load_state())
        self.assertIsNone(schema._ABSOLUTE_PATH_RE.search(blob))
