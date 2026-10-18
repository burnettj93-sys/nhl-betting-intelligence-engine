"""The 07:45 refresh, sleep evidence, run heartbeats, the day status document, the retired jobs, and the spreads reaching the product state."""
from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import operational.system_health  # noqa: F401  (imported before any test patches state_paths, so no module captures a temporary path)
from operational import credit_planner as cp
from operational import day_status, morning_refresh, run_heartbeat, sleep_evidence

U = dt.timezone.utc
ET = dt.timezone(dt.timedelta(hours=-4))
DAY = "2026-10-17"


def at(h, m=0):
    return dt.datetime(2026, 10, 17, h, m, tzinfo=ET).astimezone(U)


class Isolated(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        for p in (mock.patch.object(cp.state_paths, "path", side_effect=lambda name, **kw: self.dir / name),
                  mock.patch.object(cp.odds_quota, "reset_status", return_value={"status": "OWNER_VERIFICATION_REQUIRED", "reset_day": 1, "assumed": True, "invalid": False}),
                  mock.patch.object(cp.odds_quota, "latest_remaining", return_value=150)):
            p.start()
            self.addCleanup(p.stop)


class TestMorningRefresh(Isolated):
    def test_it_is_due_from_0745_once_a_day_and_earlier_for_an_early_first_game(self):
        self.assertFalse(morning_refresh.due(at(7, 30), at(13))["due"])
        self.assertTrue(morning_refresh.due(at(7, 46), at(13))["due"])
        self.assertTrue(morning_refresh.due(at(7, 20), at(10))["due"])                          # a 10:00 game: the refresh moves to 07:15
        self.assertFalse(morning_refresh.due(at(7, 10), at(10))["due"])

    def test_it_runs_its_steps_records_the_state_and_is_then_done_for_the_day(self):
        calls = []
        out = morning_refresh.run_if_due(at(7, 50), first_start=at(13), sync=lambda: calls.append("sync") or 0, starters=lambda: {"status": "DISABLED"})
        self.assertEqual((out["status"], calls), ("DONE", ["sync"]))
        self.assertEqual(out["steps"]["statistics"]["status"], "OK")
        self.assertEqual(morning_refresh.read()["day"], DAY)
        again = morning_refresh.run_if_due(at(8, 5), first_start=at(13), sync=lambda: calls.append("again") or 0, starters=lambda: {})
        self.assertEqual((again["status"], again["reason"], calls), ("SKIPPED", "DONE_TODAY", ["sync"]))

    def test_one_failing_step_does_not_stop_the_other_and_is_reported(self):
        def boom():
            raise RuntimeError("source down")
        out = morning_refresh.run_if_due(at(7, 50), first_start=at(13), sync=boom, starters=lambda: {"status": "OK"})
        self.assertEqual(out["status"], "PARTIAL")
        self.assertEqual(out["steps"]["statistics"]["status"], "ERROR")
        self.assertEqual(out["steps"]["starters"]["status"], "OK")

    def test_a_failed_refresh_is_retried_after_a_gap_not_every_cycle(self):
        out = morning_refresh.run_if_due(at(7, 50), first_start=at(13), sync=lambda: 1, starters=lambda: {"status": "ERROR"})
        self.assertEqual(out["status"], "FAILED")
        self.assertEqual(morning_refresh.run_if_due(at(8, 5), first_start=at(13), sync=lambda: 0, starters=lambda: {})["reason"], "RETRY_GAP")
        self.assertEqual(morning_refresh.run_if_due(at(9, 5), first_start=at(13), sync=lambda: 0, starters=lambda: {})["status"], "DONE")

    def test_a_partial_refresh_with_a_failed_step_is_retried_after_the_gap_up_to_the_limit(self):
        out = morning_refresh.run_if_due(at(7, 50), first_start=at(13), sync=lambda: 1, starters=lambda: {"status": "DISABLED"})      # the network was not up: statistics FAILED, starters fine
        self.assertEqual(out["status"], "PARTIAL")
        self.assertEqual(morning_refresh.run_if_due(at(8, 5), first_start=at(13), sync=lambda: 0, starters=lambda: {})["reason"], "RETRY_GAP")
        retry = morning_refresh.run_if_due(at(9, 5), first_start=at(13), sync=lambda: 0, starters=lambda: {"status": "DISABLED"})
        self.assertEqual((retry["status"], retry["attempts"]), ("DONE", 2))
        self.assertEqual(morning_refresh.run_if_due(at(12, 5), first_start=at(13), sync=lambda: self.fail("done: no more runs"), starters=lambda: {})["reason"], "DONE_TODAY")

    def test_retries_stop_at_the_limit(self):
        for hour in (7, 9, 11, 13):
            morning_refresh.run_if_due(at(hour, 50), first_start=at(18), sync=lambda: 1, starters=lambda: {"status": "DISABLED"})
        self.assertEqual(morning_refresh.read()["attempts"], morning_refresh.MAX_ATTEMPTS)
        self.assertEqual(morning_refresh.run_if_due(at(16, 5), first_start=at(18), sync=lambda: self.fail("no more"), starters=lambda: {})["reason"], "RETRIES_EXHAUSTED")

    def test_two_runners_never_refresh_at_the_same_time(self):
        import fcntl
        with open(self.dir / "morning_refresh.lock", "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)                                                      # the launchd job holds the refresh
            out = morning_refresh.run_if_due(at(7, 50), first_start=at(13), sync=lambda: self.fail("must not run"), starters=lambda: {})
        self.assertEqual(out["reason"], "ANOTHER_REFRESH_IS_RUNNING")

    def test_a_machine_that_slept_through_0745_runs_it_on_waking(self):
        out = morning_refresh.run_if_due(at(10, 20), first_start=at(13), sync=lambda: 0, starters=lambda: {})
        self.assertEqual(out["status"], "DONE")
        self.assertGreater(out["due"]["late_min"], 150)                                        # recorded as late, not hidden


POWER_LOG = """\
2026-10-17 12:00:00 -0400 Sleep               \tEntering Sleep state due to 'Sleep Service Back to Sleep':TCPKeepAlive=active
2026-10-17 12:10:00 -0400 DarkWake            \tDarkWake from Deep Idle [CDNP]
2026-10-17 12:10:05 -0400 Wake Requests       \t[*process=dasd request=SleepService]
2026-10-17 12:10:10 -0400 Sleep               \tEntering Sleep state due to 'Maintenance Sleep'
2026-10-17 15:00:00 -0400 Wake                \tWake from Deep Idle [CDNVA] : due to lid
2026-10-17 15:30:00 -0400 WakeTime            \tWakeTime: 0.192 sec
"""


class TestSleepEvidence(unittest.TestCase):
    def test_only_the_kind_column_counts_and_darkwake_stays_asleep(self):
        kinds = [k for _, k in sleep_evidence.events(POWER_LOG)]
        self.assertEqual(kinds, ["Sleep", "Sleep", "Sleep", "Wake"])                           # "Wake Requests" and "WakeTime" are other columns

    def test_a_window_inside_the_sleep_is_asleep_and_one_after_waking_is_awake(self):
        now = at(16)
        asleep = sleep_evidence.slept_during(at(13), at(14), now, text=POWER_LOG)
        self.assertEqual((asleep["evidence"], asleep["slept_minutes"]), ("ASLEEP", 60.0))
        awake = sleep_evidence.slept_during(at(15, 10), at(15, 50), now, text=POWER_LOG)
        self.assertEqual(awake["evidence"], "AWAKE")

    def test_a_log_that_does_not_reach_the_window_proves_nothing(self):
        self.assertEqual(sleep_evidence.slept_during(at(7), at(8), at(16), text=POWER_LOG)["evidence"], "UNAVAILABLE")
        self.assertEqual(sleep_evidence.slept_during(at(7), at(8), at(16), text="")["evidence"], "UNAVAILABLE")

    def test_still_asleep_at_the_last_event_counts_to_now(self):
        log = POWER_LOG.split("2026-10-17 15:00:00")[0]
        self.assertEqual(sleep_evidence.slept_during(at(13), at(14), at(16), text=log)["evidence"], "ASLEEP")


class TestHeartbeats(Isolated):
    def test_a_run_inside_a_window_is_seen_and_a_record_that_starts_later_proves_nothing(self):
        for t in (at(9), at(9, 15), at(12, 30)):
            run_heartbeat.beat("real_parlay_paper_trader", t)
        self.assertTrue(run_heartbeat.ran_between("real_parlay_paper_trader", at(12), at(13)))
        self.assertFalse(run_heartbeat.ran_between("real_parlay_paper_trader", at(10), at(12)))
        self.assertIsNone(run_heartbeat.ran_between("real_parlay_paper_trader", at(7), at(8)))        # the record begins after the window
        self.assertEqual(run_heartbeat.last_before("real_parlay_paper_trader", at(11)), at(9, 15))


class TestDayStatus(Isolated):
    def plan(self):
        p = cp.build_day_plan(at(8), DAY, {"1": at(13), "2": at(19), "3": at(19, 20)}, remaining=52, counts_ahead={"2026-10-18": 4}, quality={"1": 5, "2": 7, "3": 4})
        cp._persist(p)
        return p

    def test_no_plan_says_so(self):
        d = day_status.build(at(9))
        self.assertFalse(d["plan_present"])
        self.assertIn("No credit plan", d["note"])

    def test_it_reports_waves_budget_assumptions_and_the_games_the_budget_excluded(self):
        p = self.plan()
        d = day_status.build(at(11, 40))
        self.assertTrue(d["plan_present"])
        self.assertEqual([w["state"] for w in d["waves"]][0], "DUE")
        self.assertEqual(d["waves"][1]["state"], "PENDING")
        excluded = {e["game_id"] for e in d["excluded_by_budget"]}
        self.assertEqual(excluded, set(p["games_not_priced"]))
        self.assertFalse(d["budget"]["reset_verified"])
        self.assertIn("ASSUMED", d["budget"]["reset_assumption"])
        self.assertTrue(any("assumed to cost" in n for n in d["budget"]["notes"]))
        self.assertIn("1", {g["game_id"] for g in d["waves"][0]["games"] if g["selected"]})

    def test_it_carries_the_balance_the_guard_believes(self):
        self.plan()
        cp.record(cp.PROPS, 1, at(11, 35), game_id="1", markets=cp.SHOTS_KEY, planned=1, remaining_header="149")
        cp.record(cp.GOALS, 1, at(11, 36), game_id="1", markets=cp.GOALS_KEY, planned=1)
        with mock.patch("operational.system_health.odds_collection_status", return_value={"last_updated_utc": None, "credits_remaining": None}):
            d = day_status.build(at(11, 40))
        self.assertEqual((d["balance"]["baseline_value"], d["balance"]["deducted"], d["balance"]["remaining"]), (149, 1.0, 148))

    def test_a_captured_wave_shows_on_time_with_its_credits_and_a_reconciliation(self):
        self.plan()
        (self.dir / "wave_capture_state.json").write_text(json.dumps({"day": DAY, "waves": {"0": {"games": {"1": {"captured_at_utc": "x"}}, "on_time": True}}}))
        cp.record(cp.PROPS, 1, at(11, 35), game_id="1", markets=cp.SHOTS_KEY, planned=1)
        cp.record(cp.GOALS, 1, at(11, 35), game_id="1", markets=cp.GOALS_KEY, planned=1)
        d = day_status.build(at(11, 40))
        self.assertEqual(d["waves"][0]["state"], "CAPTURED_ON_TIME")
        self.assertEqual(d["waves"][0]["credits"], 2.0)
        self.assertEqual(d["reconciliation"][cp.PROPS], {"calls": 1, "planned": 1.0, "actual": 1.0})

    def test_a_wave_priced_under_an_earlier_plan_shows_as_captured_earlier_not_as_excluded(self):
        p = cp.build_day_plan(at(8), DAY, {"1": at(13), "2": at(19), "3": at(19, 20)}, remaining=52, counts_ahead={"2026-10-18": 4}, quality={"1": 5, "2": 7, "3": 4})
        for w in p["waves"]:
            w["selected"] = []                                                                    # the plan was rebuilt later and no longer selects the games already priced
        p["games_priced"] = []
        cp._persist(p)
        cp.record(cp.PROPS, 1, at(11, 35), game_id="1", markets=cp.SHOTS_KEY, planned=1)
        d = day_status.build(at(15))
        self.assertEqual(d["waves"][0]["state"], "CAPTURED_EARLIER")
        self.assertNotIn("1", {e["game_id"] for e in d["excluded_by_budget"]})

    def test_a_wave_past_its_last_chance_with_nothing_captured_is_closed_or_missed(self):
        self.plan()
        d = day_status.build(at(12, 50))
        self.assertIn(d["waves"][0]["state"], ("CLOSED", "MISSED"))


class TestRetiredJobs(Isolated):
    def test_the_t35_decision_job_makes_no_call_and_writes_no_audit_record(self):
        from operational import moneyline_pregame
        with mock.patch.object(cp, "enforced", return_value=True):
            out = moneyline_pregame.run_pregame(at(17, 40))
        self.assertEqual((out["status"], out["reason"]), ("RETIRED", "RETIRED_BY_CREDIT_PLAN"))
        self.assertFalse(out["ran"])

    def test_both_prop_sweeps_are_retired_and_make_no_request(self):
        from operational import live_odds_daily_pull as lop
        with mock.patch.object(cp, "enforced", return_value=True), mock.patch.object(lop.client, "get_nhl_events", side_effect=AssertionError("no network")):
            for sweep in ("first", "second"):
                out = lop.run_targeted_prop_sweep(sweep)
                self.assertIn("RETIRED_BY_CREDIT_PLAN", out["reason"])
                self.assertFalse(out["ran"])

    def test_the_watchdog_does_not_expect_retired_jobs_and_does_expect_the_morning_refresh(self):
        from operational import watchdog
        for j in watchdog.RETIRED_JOBS:
            self.assertNotIn(j, watchdog.EXPECTED_JOBS)
        self.assertIn("com.nhlengine.morning-refresh", watchdog.EXPECTED_JOBS)


class TestSpreadsReachTheProductState(Isolated):
    def test_the_standard_line_is_keyed_by_game_with_the_provider_quote_time_and_age(self):
        from operational import live_odds_daily_pull as lop, product_data
        cache = self.dir / "ml.json"
        cache.write_text(json.dumps({"rows": [{"home_team_abbrev": "FLA", "away_team_abbrev": "CAR", "captured_at_utc": "2026-10-17T15:00:00Z",
                                                "spreads": {"status": "POSTED", "home_point": -1.5, "home_price": 190, "away_point": 1.5, "away_price": -235, "last_update": "2026-10-17T14:58:00Z"}},
                                               {"home_team_abbrev": "TOR", "away_team_abbrev": "MTL", "captured_at_utc": "2026-10-17T15:00:00Z", "spreads": {"status": "NOT_POSTED", "reason": "x"}},
                                               {"home_team_abbrev": "BOS", "away_team_abbrev": "UTA", "captured_at_utc": "2026-10-17T15:00:00Z"}]}))
        with mock.patch.object(lop, "MONEYLINE_CACHE_PATH", cache):
            q = product_data.puck_line_quotes(dt.datetime(2026, 10, 17, 15, 20, tzinfo=U))
        self.assertEqual(q[("FLA", "CAR")]["home_price"], 190)
        self.assertEqual(q[("FLA", "CAR")]["age_min"], 22.0)                                    # from the provider's own quote time, not the retrieval time
        self.assertEqual(q[("TOR", "MTL")]["status"], "NOT_POSTED")
        self.assertNotIn(("BOS", "UTA"), q)                                                      # an older pull without the field: no price is invented


if __name__ == "__main__":
    unittest.main()
