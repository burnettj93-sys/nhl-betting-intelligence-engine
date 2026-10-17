import datetime as dt
import json
import unittest
from pathlib import Path
from unittest import mock

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
        self.assertEqual({c["name"] for c in state["checks"]}, {"jobs_loaded", "release_pinned", "trader_recent", "publish_recent", "database_path", "publishing_enabled", "recording_pause", "ticket_policy", "prediction_log", "jobs_ran", "missed_windows", "morning_update"})
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


class TestPredictionLogCheck(unittest.TestCase):
    def setUp(self):
        import tempfile
        from operational import state_paths
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(state_paths, "path", side_effect=lambda n, **kw: Path(self.tmp.name) / n)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_logger_that_stopped_is_a_warning_and_a_quiet_pool_is_not(self):
        import json
        from operational import shadow_selection as sh
        now = dt.datetime(2026, 10, 12, 12, 0, tzinfo=dt.timezone.utc)
        self.assertEqual(wd.check_prediction_log(now)["status"], wd.WARN)                      # never written
        (Path(self.tmp.name) / sh.STATE).write_text(json.dumps({"pool_key": "x", "written_at_utc": "2026-10-11T12:00:00Z", "checked_at_utc": "2026-10-12T11:50:00Z"}))
        self.assertEqual(wd.check_prediction_log(now)["status"], wd.OK)                        # nothing changed since yesterday, but it looked 10 minutes ago
        (Path(self.tmp.name) / sh.STATE).write_text(json.dumps({"pool_key": "x", "written_at_utc": "2026-10-12T09:00:00Z", "checked_at_utc": "2026-10-12T09:00:00Z"}))
        self.assertEqual(wd.check_prediction_log(now)["status"], wd.WARN)                      # not looked at for three hours: it stopped

    def test_every_look_stamps_the_state_even_when_nothing_changes(self):
        from operational import shadow_selection as sh
        now = dt.datetime(2026, 10, 12, 12, 0, tzinfo=dt.timezone.utc)
        self.assertEqual(sh.record([], now)["status"], "EMPTY")
        import json
        self.assertEqual(json.loads((Path(self.tmp.name) / sh.STATE).read_text())["checked_at_utc"], "2026-10-12T12:00:00Z")


class TestMorningUpdateCheck(unittest.TestCase):
    """The day's preparation (07:45 refresh, 08:00 whole-slate analysis, league pull) is judged from the day status: late is WARN, very late FAIL, and each complete day is logged persistently."""
    DAY = "2026-10-09"

    def setUp(self):
        import tempfile
        from pathlib import Path
        from unittest import mock
        from operational import credit_planner as cp
        self.cp = cp
        self.tmp = tempfile.TemporaryDirectory()
        for p in (mock.patch.object(cp.state_paths, "path", side_effect=lambda n, **kw: Path(self.tmp.name) / n),
                  mock.patch.object(cp.odds_quota, "reset_status", return_value={"status": "OWNER_VERIFICATION_REQUIRED", "reset_day": 1, "assumed": True, "invalid": False})):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def at(self, h, m=0):
        return dt.datetime(2026, 10, 9, h + 4, m, tzinfo=dt.timezone.utc)     # ET wall clock

    def plan(self, first=13):
        self.cp._persist(self.cp.build_day_plan(self.at(7, 30), self.DAY, {"g1": self.at(first), "g2": self.at(19)}, remaining=200, counts_ahead={"2026-10-10": 4}))

    def write(self, name, doc):
        import json
        from pathlib import Path
        (Path(self.tmp.name) / name).write_text(json.dumps(doc))

    def prepared(self, refresh_status="DONE", board_at=None, pulls=True):
        self.plan()
        self.write("morning_refresh_state.json", {"day": self.DAY, "status": refresh_status, "finished_utc": self.at(7, 50).strftime("%Y-%m-%dT%H:%M:%SZ")})
        if board_at is not None:
            self.write("slate_board.json", {"date_et": self.DAY, "generated_at_utc": board_at.strftime("%Y-%m-%dT%H:%M:%SZ"), "counts": {"analysed": 2}})
        if pulls:
            self.cp.record(self.cp.MONEYLINE_UI, 2, self.at(8, 1), markets="h2h,spreads")

    def test_before_the_analysis_time_nothing_is_due(self):
        self.assertEqual(wd.check_morning_update(self.at(7, 0))["status"], wd.OK)

    def test_no_plan_is_warn_after_845_and_fail_after_10(self):
        self.assertEqual(wd.check_morning_update(self.at(8, 30))["status"], wd.OK)
        self.assertEqual(wd.check_morning_update(self.at(8, 50))["status"], wd.WARN)
        self.assertEqual(wd.check_morning_update(self.at(10, 5))["status"], wd.FAIL)

    def test_done_when_refreshed_analysed_published_and_pulled(self):
        self.prepared(board_at=self.at(8, 4))
        r = wd.check_morning_update(self.at(9, 0))
        self.assertEqual(r["status"], wd.OK)
        self.assertIn("preparation done", r["detail"])
        self.assertTrue([e for e in wd.morning_evidence() if e["state"] == "DONE"])

    def test_a_refresh_that_never_completed_warns_then_fails(self):
        self.prepared(refresh_status="NOT_RUN_YET", board_at=self.at(8, 4))
        self.assertEqual(wd.check_morning_update(self.at(8, 25))["status"], wd.OK)       # inside the 45-minute grace after 07:45
        self.assertEqual(wd.check_morning_update(self.at(8, 50))["status"], wd.WARN)
        r = wd.check_morning_update(self.at(9, 50))
        self.assertEqual(r["status"], wd.FAIL)
        self.assertIn("refresh", r["detail"])

    def test_an_analysis_published_before_8_does_not_count_and_a_late_one_is_named(self):
        self.prepared(board_at=self.at(7, 30))
        r = wd.check_morning_update(self.at(9, 0))
        self.assertEqual(r["status"], wd.WARN)
        self.assertIn("whole-slate analysis", r["detail"])
        self.assertFalse([e for e in wd.morning_evidence() if e["state"] == "DONE"])

    def test_a_missing_league_pull_is_named(self):
        self.prepared(board_at=self.at(8, 4), pulls=False)
        r = wd.check_morning_update(self.at(9, 0))
        self.assertEqual(r["status"], wd.WARN)
        self.assertIn("league-wide moneyline", r["detail"])

    def test_an_early_first_game_moves_the_due_times_earlier(self):
        self.plan(first=10)
        from operational import day_status
        ds = day_status.build(self.at(8, 0))
        self.assertTrue(ds["prep"]["refresh_due_utc"].startswith("2026-10-09T11:15"))     # 07:15 ET for a 10:00 AM first game
        self.assertTrue(ds["prep"]["analysis_due_utc"].startswith("2026-10-09T11:30"))

    def test_readiness_calls_the_preparation_unmet_until_a_prepared_morning_is_on_record(self):
        feats = {f["feature"]: f for f in wd.readiness(self.at(9, 0))["features"]}
        prep = next(v for k, v in feats.items() if k.startswith("Morning preparation"))
        self.assertEqual(prep["status"], "NOT_VERIFIED")
        self.assertIn("UNMET", prep["detail"])
        self.assertEqual(feats["Coordinated player-odds captures by start-time wave"]["status"], "LIMITED")
        self.prepared(board_at=self.at(8, 4))
        wd.check_morning_update(self.at(9, 0))
        feats = {f["feature"]: f for f in wd.readiness(self.at(9, 0))["features"]}
        self.assertEqual(next(v for k, v in feats.items() if k.startswith("Morning preparation"))["status"], "WORKING")


class TestJobsActuallyRan(unittest.TestCase):
    DEFS = {"com.nhlengine.real-parlay-paper-trader": {"StartInterval": 900, "StandardOutPath": "/x/trader.log"},
            "com.nhlengine.morning-update": {"StartCalendarInterval": {"Hour": 8, "Minute": 0}, "StandardOutPath": "/x/morning.log"},
            "com.nhlengine.watchdog": {"StartInterval": 1800, "StandardOutPath": "/x/watchdog.log"}}

    def check(self, ages_min):
        now = dt.datetime(2026, 10, 17, 21, 0, tzinfo=dt.timezone.utc)

        def mtime(path):
            if path not in ages_min:
                raise OSError("no such log")
            return now.timestamp() - ages_min[path] * 60
        fresh = (now - dt.timedelta(minutes=2)).isoformat().replace("+00:00", "Z")
        health = {"real_parlay_paper_trader": {"last_success_utc": fresh, "last_attempt_utc": fresh, "last_status": "OK"}}
        return wd.check_jobs_ran(now, definitions=self.DEFS, mtime=mtime, launchctl_text="", health=health)

    def test_jobs_that_wrote_to_their_logs_inside_their_schedule_are_ok(self):
        with mock.patch.object(wd, "EXPECTED_JOBS", ("com.nhlengine.real-parlay-paper-trader", "com.nhlengine.morning-update")):
            r = self.check({"/x/trader.log": 20, "/x/morning.log": 600})
        self.assertEqual(r["status"], wd.OK)
        self.assertIn("all 2 scheduled jobs", r["detail"])                          # the watchdog does not check itself

    def test_a_loaded_job_that_has_not_run_is_named_with_its_age(self):
        with mock.patch.object(wd, "EXPECTED_JOBS", ("com.nhlengine.real-parlay-paper-trader", "com.nhlengine.morning-update")):
            r = self.check({"/x/trader.log": 4 * 60, "/x/morning.log": 600})
        self.assertEqual(r["status"], wd.WARN)
        self.assertIn("real-parlay-paper-trader OVERDUE (last started 4.0 h ago", r["detail"])
        self.assertNotIn("morning-update", r["detail"])

    def test_a_calendar_job_is_late_after_26_hours_and_a_job_with_no_log_is_reported(self):
        with mock.patch.object(wd, "EXPECTED_JOBS", ("com.nhlengine.real-parlay-paper-trader", "com.nhlengine.morning-update")):
            r = self.check({"/x/trader.log": 5, "/x/morning.log": 27 * 60})
            self.assertIn("morning-update OVERDUE (last started 27.0 h ago", r["detail"])
            r2 = self.check({"/x/trader.log": 5})
            self.assertIn("morning-update OVERDUE (no log at all)", r2["detail"])

    def test_missing_definitions_are_a_warning_not_all_ok(self):
        now = dt.datetime(2026, 10, 17, 21, 0, tzinfo=dt.timezone.utc)
        r = wd.check_jobs_ran(now, definitions={}, mtime=lambda p: now.timestamp(), launchctl_text="", health={})
        self.assertEqual(r["status"], wd.WARN)
        self.assertIn("could be checked", r["detail"])


class JobStateTests(unittest.TestCase):
    """RUNNING / FAILED / OVERDUE / STALE_OUTPUT / OK are told apart from launchctl, the log's age and the recorded output; log activity alone is not success."""
    NOW = dt.datetime(2026, 10, 17, 15, 0, tzinfo=dt.timezone.utc)
    LABEL = "com.nhlengine.real-parlay-paper-trader"

    def _rows(self, *, launchctl="", health=None, log_age_min=5.0):
        defs = {self.LABEL: {"StartInterval": 900, "StandardOutPath": "/x/trader.log"}}
        mtime = lambda _p: (self.NOW - dt.timedelta(minutes=log_age_min)).timestamp()  # noqa: E731
        return wd.job_states(self.NOW, launchctl_text=launchctl, health=health or {}, definitions=defs, mtime=mtime)[0]

    def _health(self, ok_min_ago, status="OK", try_min_ago=1):
        iso = lambda m: (self.NOW - dt.timedelta(minutes=m)).isoformat().replace("+00:00", "Z")  # noqa: E731
        return {"real_parlay_paper_trader": {"last_success_utc": None if ok_min_ago is None else iso(ok_min_ago), "last_attempt_utc": iso(try_min_ago), "last_status": status}}

    def test_ok_when_started_and_output_fresh(self):
        self.assertEqual(self._rows(health=self._health(3))["state"], "OK")

    def test_running_when_a_pid_is_alive(self):
        self.assertEqual(self._rows(launchctl=f"4242\t0\t{self.LABEL}\n", health=self._health(3))["state"], "RUNNING")

    def test_failed_when_the_last_attempt_failed_after_the_last_success(self):
        row = self._rows(health=self._health(120, status="FAILED", try_min_ago=2))
        self.assertEqual(row["state"], "FAILED")

    def test_failed_from_launchd_exit_status(self):
        self.assertEqual(self._rows(launchctl=f"-\t1\t{self.LABEL}\n", health=self._health(3))["state"], "FAILED")

    def test_overdue_when_the_log_is_old(self):
        self.assertEqual(self._rows(health=self._health(3), log_age_min=300)["state"], "OVERDUE")

    def test_stale_output_when_it_runs_but_produces_nothing_valid(self):
        row = self._rows(health=self._health(600), log_age_min=2)          # logging every cycle, last success ten hours ago
        self.assertEqual(row["state"], "STALE_OUTPUT")

    def test_stale_output_when_it_never_succeeded(self):
        self.assertEqual(self._rows(health=self._health(None), log_age_min=2)["state"], "STALE_OUTPUT")
