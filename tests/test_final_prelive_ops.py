"""Final Pre-Live Ops block (2026-09-25): wake planning (man-verified pmset syntax), owner-only sudo, wake coverage, the
wake -> assertion handoff (wake-guard), lid limitation, notifications, first-live record retention, pre-flight verdict,
owner-action readiness. No sudo is ever executed; no paid request; no sleep setting is changed."""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from operational import first_live_certification as flc
from operational import keep_awake as ka
from operational import moneyline_pregame as mp
from operational import notify
from operational import schedule_next_wake as snw

REPO = Path(__file__).resolve().parent.parent
D = lambda h, m=0, s=0, day=29: dt.datetime(2026, 9, day, h, m, s, tzinfo=dt.timezone.utc)
LISTED = lambda c: True
SCHED = ("Scheduled power events:\n [0]  wake at 09/29/2026 15:45:00 by 'nhl-engine'\n"
         " [1]  wake at 09/25/2026 17:00:00 by 'com.apple.alarm.user-visible-com.apple.donotdisturb.server.ScheduleLifetimeMonitor.timer' User visible: true\n")


def local_ts(y, mo, d, h, mi=0):
    return dt.datetime(y, mo, d, h, mi).astimezone(dt.timezone.utc)


# --------------------------------------------------------------------------------------- wake planning
class TestWakePlan(unittest.TestCase):
    def w(self):
        (w,) = ka.windows(D(12), [D(21)], listing_fn=LISTED)
        return w

    def test_wake_is_at_least_30_minutes_before_the_capture_window_and_inside_the_assertion_window(self):
        w = self.w()
        t = ka.wake_time(w)
        self.assertEqual(t, D(19, 45))                                           # 15:45 EDT for the 21:00Z game
        self.assertGreaterEqual((w["capture_window"][0] - t), dt.timedelta(minutes=30))
        self.assertGreaterEqual(t, w["assert_from_utc"])                          # the guard holds it immediately after the wake

    def test_command_syntax_matches_man_pmset(self):
        """man pmset: pmset schedule <type> "MM/dd/yy HH:mm:ss" [owner]  (24-hour, LOCAL time, two-digit year, quoted)."""
        cmd = ka.pmset_wake_command(D(19, 45))
        self.assertRegex(cmd, r'^sudo pmset schedule wake "\d\d/\d\d/\d\d \d\d:\d\d:\d\d" nhl-engine$')
        local = D(19, 45).astimezone()
        self.assertIn(local.strftime("%m/%d/%y %H:%M:%S"), cmd)

    def test_only_future_wakes_are_planned_and_unlisted_clusters_get_none(self):
        with mock.patch.object(mp, "archived_listing", return_value=False):
            self.assertEqual(ka.wake_commands(D(12), [D(21)]), [])
        with mock.patch.object(mp, "archived_listing", return_value=True):
            self.assertEqual(len(ka.wake_commands(D(12), [D(21)], horizon_h=240)), 1)
            self.assertEqual(ka.wake_commands(D(20), [D(21)], horizon_h=240), [])          # the wake time already passed

    def test_next_wake_reports_the_cluster_times(self):
        n = snw.next_wake(D(12), starts_fn=lambda now: [D(21)], listing_fn=LISTED)
        self.assertEqual(n["wake_utc"], D(19, 45))
        self.assertEqual(n["window"]["capture_window"][0], D(20, 20))                     # T-40
        self.assertIn("sudo pmset schedule wake", n["command"])

    def test_the_planner_never_changes_a_power_setting(self):
        for src in ((REPO / "operational" / "schedule_next_wake.py").read_text(), (REPO / "operational" / "keep_awake.py").read_text()):
            for banned in ("pmset -a", "pmset -c", "pmset -b", "disablesleep", "pmset repeat"):
                self.assertNotIn(banned, src)
            self.assertNotRegex(src, r'"pmset",\s*"-[abc]"')
            self.assertNotIn('"sleep", "0"', src)


class TestPmsetSchedParsing(unittest.TestCase):
    def test_parses_the_os_listing_including_four_digit_years(self):
        ev = ka.parse_sched(SCHED)
        self.assertEqual(len(ev), 2)
        self.assertEqual((ev[0]["type"], ev[0]["owner"]), ("wake", "nhl-engine"))
        self.assertEqual(ev[0]["when_utc"], local_ts(2026, 9, 29, 15, 45))

    def test_two_digit_years_and_garbage(self):
        ev = ka.parse_sched(" [0]  wake at 09/29/26 15:45:00 by 'x'\n[1] wake at 99/99/26 aa by 'y'")
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["when_utc"].year, 2026)


class TestWakeCoverage(unittest.TestCase):
    def setUp(self):
        (self.w,) = ka.windows(D(12), [D(21)], listing_fn=LISTED)

    def ev(self, when):
        return [{"type": "wake", "when_utc": when, "owner": "o"}]

    def test_an_event_in_the_right_span_covers_the_cluster(self):
        for when in (D(19, 20), D(19, 45), D(20, 0), D(20, 20)):
            self.assertIsNotNone(ka.wake_covers(self.w, self.ev(when)), when)

    def test_too_early_or_after_the_window_opens_does_not(self):
        for when in (D(15, 0), D(19, 19), D(20, 21), D(23, 0)):
            self.assertIsNone(ka.wake_covers(self.w, self.ev(when)), when)

    def test_verify_reads_the_os_listing_not_an_exit_code(self):
        nxt = snw.next_wake(D(12), starts_fn=lambda n: [D(21)], listing_fn=LISTED)
        ok = snw.verify(D(12), nxt=nxt, runner=lambda cmd: SCHED)
        self.assertEqual((ok["state"], ok["type"], ok["owner"]), ("SCHEDULED", "wake", "nhl-engine"))
        self.assertIn("covers", ok["detail"])
        missing = snw.verify(D(12), nxt=nxt, runner=lambda cmd: "Scheduled power events:\n")
        self.assertEqual(missing["state"], "OWNER_ACTION_REQUIRED")
        self.assertIn("sudo pmset schedule wake", missing["command"])

    def test_no_cluster_ahead_is_waiting_for_event(self):
        with mock.patch.object(snw, "next_wake", return_value=None):
            self.assertEqual(snw.verify(D(12))["state"], "WAITING_FOR_EVENT")


class TestOwnerOnlySudo(unittest.TestCase):
    def nxt(self):
        return snw.next_wake(D(12), starts_fn=lambda n: [D(21)], listing_fn=LISTED)

    def test_default_and_plan_modes_never_run_anything(self):
        with mock.patch("subprocess.run", side_effect=AssertionError("must not run")), mock.patch.object(snw, "next_wake", return_value=self.nxt()), \
             mock.patch("builtins.print"):
            snw.main([])

    def test_apply_uses_non_interactive_sudo_and_fails_closed_without_a_password(self):
        calls = []

        def run(cmd):
            calls.append(cmd)
            return mock.Mock(returncode=1, stderr="sudo: a password is required", stdout="")
        with mock.patch.object(snw, "next_wake", return_value=self.nxt()):
            r = snw.apply(D(12), runner=run)
        self.assertFalse(r["applied"])
        self.assertIn("never asks", r["reason"])
        self.assertIn("sudo pmset schedule wake", r["run_this"])
        self.assertEqual(calls[0][:3], ["sudo", "-n", "pmset"])                          # -n: never prompt
        self.assertEqual(len(calls), 1)                                                  # no retry, no password handling

    def test_apply_is_judged_by_the_os_listing_not_the_exit_code(self):
        nxt = self.nxt()
        with mock.patch.object(snw, "next_wake", return_value=nxt), \
             mock.patch.object(ka, "_run", return_value="Scheduled power events:\n"):
            r = snw.apply(D(12), runner=lambda cmd: mock.Mock(returncode=0))
        self.assertFalse(r["applied"])                                                    # exit 0 but nothing in the listing
        with mock.patch.object(snw, "next_wake", return_value=nxt), \
             mock.patch.object(snw, "verify", return_value={"state": "SCHEDULED"}):
            self.assertTrue(snw.apply(D(12), runner=lambda cmd: mock.Mock(returncode=0))["applied"])

    def test_no_code_path_passes_a_password(self):
        src = (REPO / "operational" / "schedule_next_wake.py").read_text()
        for banned in ("getpass", "input(", "-S", "password=", "echo "):
            self.assertNotIn(banned, src.replace("(this tool never asks for one)", "").replace("password is required", "").replace("needs a password", ""))

    def test_a_test_run_cannot_reach_sudo(self):
        with mock.patch("subprocess.run", side_effect=AssertionError("sudo must not run in tests")):
            self.assertIsNotNone(snw.next_wake(D(12), starts_fn=lambda n: [D(21)], listing_fn=LISTED))

    def test_lid_and_shutdown_limits_are_stated_honestly(self):
        note = snw.LID_NOTE.lower()
        for phrase in ("lid open", "nor caffeinate can override closed-lid", "shut down"):
            self.assertIn(phrase, note)
        self.assertNotIn("guarantee", note)


# --------------------------------------------------------------------------------------- wake -> caffeinate handoff
class TestWakeHandoff(unittest.TestCase):
    """The Mac sleeps after ONE idle minute. After a scheduled wake the assertion must start within seconds, not at
    the next 2-minute launchd tick. The wake-guard (a detached process that merely SUSPENDS during sleep) closes it."""

    def simulate(self, wake_at):
        t = {"now": D(19, 44)}
        holds: list = []
        ticks = {"n": 0}

        def clock():
            return t["now"]

        def sleep(sec):
            ticks["n"] += 1
            # the machine is ASLEEP until wake_at: the guard is suspended, then the sleep call returns at wake
            t["now"] = max(t["now"] + dt.timedelta(seconds=sec), wake_at) if t["now"] < wake_at else t["now"] + dt.timedelta(seconds=sec)

        def ensure(now):
            holds.append(now)

        with mock.patch.object(mp, "archived_listing", return_value=True):
            reason = ka.guard_loop(clock=clock, sleep=sleep, ensure=ensure, starts_fn=lambda n: [D(21)], max_iterations=200)
        return holds, reason

    def test_the_assertion_starts_within_the_guard_poll_period_of_the_wake(self):
        wake = D(19, 45)
        holds, _ = self.simulate(wake)
        self.assertTrue(holds)
        first = holds[0]
        self.assertGreaterEqual(first, D(19, 40))                                   # only inside the assertion window
        self.assertLessEqual((first - wake).total_seconds(), ka.GUARD_POLL_S)        # <= 5 s after waking ...
        self.assertLess(ka.GUARD_POLL_S, 60)                                         # ... far inside the 1-minute idle timer

    def test_a_wake_before_the_assertion_window_is_not_held_and_that_is_by_design(self):
        holds, _ = self.simulate(D(15, 0))
        self.assertTrue(all(h >= D(19, 40) for h in holds))

    def test_guard_exits_when_nothing_is_ahead_and_never_runs_forever(self):
        with mock.patch.object(mp, "archived_listing", return_value=True):
            self.assertEqual(ka.guard_loop(clock=lambda: D(23), sleep=lambda s: None, ensure=lambda n: None,
                                           starts_fn=lambda n: [D(21)], max_iterations=5), "NO_WINDOW_AHEAD")
            clock = iter([D(19, 40) + dt.timedelta(minutes=10 * i) for i in range(60)])
            with mock.patch.object(ka, "GUARD_MAX_AGE_H", 1):
                self.assertEqual(ka.guard_loop(clock=lambda: next(clock), sleep=lambda s: None, ensure=lambda n: None,
                                               starts_fn=lambda n: [D(21)], max_iterations=50), "MAX_AGE")

    def test_the_guard_ends_holding_by_itself_the_assertion_is_time_boxed(self):
        with mock.patch("subprocess.Popen") as popen:
            popen.return_value.pid = 5
            ka._spawn_caffeinate(600)
        self.assertEqual(popen.call_args.args[0][:3], ["/usr/bin/caffeinate", "-i", "-t"])

    def test_the_job_starts_one_guard_only_when_a_listed_window_is_ahead(self):
        state = Path(tempfile.mkdtemp()) / "g.json"
        spawned = []
        with mock.patch.object(mp, "archived_listing", return_value=True):
            r = ka.ensure_guard(D(12), starts_fn=lambda n: [D(21)], spawn=lambda: spawned.append(1) or 77, alive_fn=lambda p: False,
                                state_path=state, active_fn=lambda: True)
            self.assertEqual((r["guard"], spawned), ("STARTED", [1]))
            r = ka.ensure_guard(D(12), starts_fn=lambda n: [D(21)], spawn=lambda: spawned.append(1) or 78, alive_fn=lambda p: True,
                                state_path=state, active_fn=lambda: True)
            self.assertEqual((r["guard"], len(spawned)), ("ALREADY_RUNNING", 1))
            r = ka.ensure_guard(D(12), starts_fn=lambda n: [], spawn=lambda: spawned.append(1), state_path=Path(tempfile.mkdtemp()) / "x.json",
                                active_fn=lambda: True)
            self.assertEqual(r["reason"], "NO_LISTED_WINDOW_AHEAD")

    def test_guard_never_starts_under_test_or_on_standby(self):
        with mock.patch("subprocess.Popen", side_effect=AssertionError("no guard in tests")):
            self.assertEqual(ka.ensure_guard(D(12), starts_fn=lambda n: [D(21)])["reason"], "UNDER_TEST")
        self.assertEqual(ka.ensure_guard(D(12), spawn=lambda: 1, active_fn=lambda: False)["reason"], "STANDBY")

    def test_a_missed_window_is_still_missed_the_keep_awake_code_never_pulls(self):
        src = (REPO / "operational" / "keep_awake.py").read_text()
        for banned in ("run_moneyline_snapshot", "get_sport_odds", "record_audit", "_default_pull", "requests"):
            self.assertNotIn(banned, src)
        cluster, = mp.plan_clusters([D(21)])
        self.assertFalse(mp.covers(D(20, 31), D(21)))                                # a late quote is not policy-valid


# --------------------------------------------------------------------------------------- notifications
class TestNotifications(unittest.TestCase):
    def rec(self, outcome, **kw):
        return {"cluster_id": "2026-09-29T21:00/x", "outcome": outcome, "provider_listed": True, "tags": [], "credits_spent": 1,
                "recommendations_evaluated": 2, "bet_count": 0, "wait_count": 2, "pass_count": 0, "api_status": "OK", **kw}

    def test_success_failure_and_missed_window_are_announced(self):
        self.assertIn("SUCCESS", notify.messages_for({}, [self.rec(mp.DECISION_SUCCESS)])[0][0])
        self.assertIn("FAILED", notify.messages_for({}, [self.rec(mp.NETWORK_FAILED, api_status="network error")])[0][0])
        m = notify.messages_for({}, [self.rec(mp.MISSED_WINDOW, tags=[mp.MISSED_WINDOW, mp.MACHINE_ASLEEP])])[0]
        self.assertIn("MISSED_WINDOW", m[0])
        self.assertIn("Never pulled late", m[1])

    def test_unlisted_clusters_are_silent(self):
        self.assertEqual(notify.messages_for({}, [self.rec(mp.PROVIDER_NOT_LISTED, provider_listed=False)]), [])
        self.assertEqual(notify.messages_for({}, [self.rec(mp.MISSED_WINDOW, provider_listed=False)]), [])

    def test_send_is_detached_local_and_failure_proof(self):
        seen = []
        self.assertTrue(notify.send("t", 'say "hi"', spawn=seen.append))
        self.assertEqual(seen[0][0], "osascript")
        self.assertNotIn('"hi"', seen[0][2])                                         # quotes are neutralised
        self.assertFalse(notify.send("t", "m", spawn=mock.Mock(side_effect=OSError("x"))))

    def test_off_under_test_and_never_uses_the_network_or_paid_services(self):
        with mock.patch("subprocess.Popen", side_effect=AssertionError("no notification in tests")):
            self.assertFalse(notify.send("t", "m"))
        src = (REPO / "operational" / "notify.py").read_text()
        for banned in ("smtplib", "requests", "urllib", "twilio", "sendgrid"):
            self.assertNotIn(banned, src)

    def test_the_job_notifies_downstream_and_a_broken_notifier_never_breaks_it(self):
        from operational import live_odds_daily_pull as lop
        result = {"ran": True, "status": "SUCCESS", "clusters_detail": [{"key": "k"}]}
        audited = [self.rec(mp.DECISION_SUCCESS)]
        sent = []
        with mock.patch("operational.moneyline_pregame.run_pregame", return_value=result), \
             mock.patch.object(lop, "_moneyline_downstream"), \
             mock.patch("operational.cloud_publish_hook.publish_after", return_value={"status": "SUCCESS"}), \
             mock.patch("operational.moneyline_pregame.record_audit", return_value=audited), \
             mock.patch("operational.keep_awake.ensure_holding", return_value={"action": "NONE"}), \
             mock.patch("operational.keep_awake.ensure_guard", return_value={"guard": "NONE"}), \
             mock.patch("operational.notify.send", side_effect=lambda t, m: sent.append(t)), \
             mock.patch("operational.deployment_mode.require_active_scheduler_or_exit", return_value=True), \
             mock.patch("sys.argv", ["lop", "--mode=moneyline-pregame"]), mock.patch("builtins.print") as printed:
            lop._main()
        self.assertEqual(len(sent), 1)
        self.assertEqual(json.loads(printed.call_args[0][0])["audit"][0]["outcome"], mp.DECISION_SUCCESS)
        with mock.patch("operational.moneyline_pregame.run_pregame", return_value=result), mock.patch.object(lop, "_moneyline_downstream"), \
             mock.patch("operational.cloud_publish_hook.publish_after", return_value={"status": "SUCCESS"}), \
             mock.patch("operational.moneyline_pregame.record_audit", return_value=audited), \
             mock.patch("operational.keep_awake.ensure_holding", return_value={"action": "NONE"}), \
             mock.patch("operational.keep_awake.ensure_guard", return_value={"guard": "NONE"}), \
             mock.patch("operational.notify.send", side_effect=RuntimeError("osascript exploded")), \
             mock.patch("operational.deployment_mode.require_active_scheduler_or_exit", return_value=True), \
             mock.patch("sys.argv", ["lop", "--mode=moneyline-pregame"]), mock.patch("builtins.print") as printed:
            lop._main()
        self.assertEqual(json.loads(printed.call_args[0][0])["status"], "SUCCESS")


# --------------------------------------------------------------------------------------- first-live record retention
class TestFirstLiveRetention(unittest.TestCase):
    def setUp(self):
        self.audit = Path(tempfile.mkdtemp()) / "audit.json"

    def real(self, key, **kw):
        return {"cluster_id": key, "provider_listed": True, "actual_pull_utc": "t", "credits_spent": 1, "odds_rows_stored": 40,
                "in_decision_window": True, "recommendations_evaluated": 2, "wait_count": 2, "cloud_publish": {"status": "SUCCESS", "content_hash": "h"},
                "outcome": mp.DECISION_SUCCESS, **kw}

    def test_the_first_real_cluster_survives_later_clusters_and_the_rolling_cap(self):
        first = self.real("2026-09-29T21:00/x")
        mp._save_audit({"records": {first["cluster_id"]: first}}, self.audit)
        records = {first["cluster_id"]: first}
        for i in range(250):                                                         # far beyond AUDIT_KEEP
            k = f"2026-10-{1 + i % 28:02d}T{i % 24:02d}:00/{i}"
            records[k] = self.real(k)
            mp._save_audit({"records": dict(records)}, self.audit)
            records = mp.load_audit(self.audit)["records"]
        self.assertNotIn("2026-09-29T21:00/x", records)                              # rolled out of the audit ...
        kept = mp.load_milestones(self.audit)
        self.assertEqual(kept["first_observed"]["cluster_id"], "2026-09-29T21:00/x")   # ... but preserved
        self.assertEqual(kept["first_certified"]["cluster_id"], "2026-09-29T21:00/x")

    def test_milestones_are_write_once(self):
        mp._save_audit({"records": {"a": self.real("a")}}, self.audit)
        mp._save_audit({"records": {"a": self.real("a"), "b": self.real("b", credits_spent=9)}}, self.audit)
        self.assertEqual(mp.load_milestones(self.audit)["first_observed"]["cluster_id"], "a")
        self.assertEqual(mp.load_milestones(self.audit)["first_observed"]["record"]["credits_spent"], 1)

    def test_a_first_failure_is_kept_and_no_secrets_or_raw_responses_are_stored(self):
        bad = self.real("f", actual_pull_utc=None, credits_spent=0, odds_rows_stored=0, outcome=mp.NETWORK_FAILED)
        mp._save_audit({"records": {"f": bad}}, self.audit)
        text = json.dumps(mp.load_milestones(self.audit)).lower()
        self.assertIn("first_failure", text)
        for banned in ("apikey", "api_key", "bookmakers", "outcomes", "token", "password"):
            self.assertNotIn(banned, text)

    def test_nothing_is_preserved_for_unlisted_clusters(self):
        mp._save_audit({"records": {"u": self.real("u", provider_listed=False)}}, self.audit)
        self.assertEqual(mp.load_milestones(self.audit), {})


# --------------------------------------------------------------------------------------- pre-flight verdict
ARMED = dict(next_cluster=lambda: {"cluster_id": "x", "games": 1}, scheduler=lambda: {"loaded": True, "on_master": True, "branch": "master", "commit": "abc"},
             quota=lambda: {"credits_remaining": 367, "sufficient": True, "reason": "OK", "reset_day": {"status": "OWNER_CONFIGURED"}},
             publisher=lambda: True, deployment=lambda: "ACTIVE", power=lambda: {"risk": "HIGH"}, audit=lambda: {"records": {}},
             caffeinate=lambda: {"binary_present": True, "guard_closes_wake_gap": True, "limits": "x"},
             git=lambda: {"branch": "master", "commit": "abc1234", "worktree_clean": True},
             wake=lambda: {"state": "SCHEDULED", "detail": "wake event ok"})


class TestPreflightVerdict(unittest.TestCase):
    def pre(self, **o):
        return flc.preflight(D(12), deps={**ARMED, **o})

    def test_ready_when_everything_including_the_wake_is_in_place(self):
        p = self.pre()
        self.assertEqual(p["preflight_verdict"], "READY")
        self.assertEqual(p["owner_actions"], [])
        for key in ("git", "wake", "keep_awake", "lid_note", "paid_requests_made"):
            self.assertIn(key, p)
        self.assertEqual(p["paid_requests_made"], 0)

    def test_an_unscheduled_wake_on_a_high_risk_mac_is_an_owner_action_not_a_failure(self):
        p = self.pre(wake=lambda: {"state": "OWNER_ACTION_REQUIRED", "command": 'sudo pmset schedule wake "09/29/26 15:45:00" nhl-engine', "detail": "d"})
        self.assertEqual(p["preflight_verdict"], "OWNER_ACTION_REQUIRED")
        self.assertTrue(any("pmset schedule wake" in a for a in p["owner_actions"]))
        self.assertTrue(p["architecture_ready"])                                    # the ENGINE is ready; the owner still has to act

    def test_an_unset_reset_day_is_a_warning_not_a_blocker_when_credits_are_sufficient(self):
        p = self.pre(quota=lambda: {"credits_remaining": 367, "sufficient": True, "reason": "OK", "reset_day": {"status": "OWNER_VERIFICATION_REQUIRED"}})
        self.assertEqual(p["preflight_verdict"], "READY")
        self.assertEqual(p["owner_actions"], [])
        self.assertTrue(any("RESET_DAY" in w for w in p["warnings"]))

    def test_an_unset_reset_day_with_insufficient_credits_is_still_not_ready(self):
        p = self.pre(quota=lambda: {"credits_remaining": 20, "sufficient": False, "reason": "HARD_RESERVE", "reset_day": {"status": "OWNER_VERIFICATION_REQUIRED"}})
        self.assertEqual(p["preflight_verdict"], "NOT_READY")
        self.assertTrue(any("quota not sufficient" in x for x in p["architecture_problems"]))

    def test_the_real_guard_enforces_the_reserve_without_a_reset_day(self):
        from operational import odds_quota as oq
        ok = oq.guard(1, now=D(12), soft_multiplier=oq.PREGAME_SOFT_MULTIPLIER, remaining=365, spent_today=0)
        self.assertTrue(ok["allow"])
        low = oq.guard(1, now=D(12), soft_multiplier=oq.PREGAME_SOFT_MULTIPLIER, remaining=oq.RESERVE, spent_today=0)
        self.assertFalse(low["allow"])
        self.assertEqual(low["reason"], "HARD_RESERVE")

    def test_dirty_tree_feature_branch_or_missing_keep_awake_is_not_ready(self):
        self.assertEqual(self.pre(git=lambda: {"branch": "master", "commit": "a", "worktree_clean": False})["preflight_verdict"], "NOT_READY")
        self.assertEqual(self.pre(scheduler=lambda: {"loaded": True, "on_master": False, "branch": "feature/x", "commit": "f"})["preflight_verdict"], "NOT_READY")
        self.assertEqual(self.pre(caffeinate=lambda: {"binary_present": False, "guard_closes_wake_gap": True})["preflight_verdict"], "NOT_READY")

    def test_no_network_no_sudo_no_credit(self):
        import requests
        with mock.patch.object(requests, "get", side_effect=AssertionError("network")), mock.patch("urllib.request.urlopen", side_effect=AssertionError("network")):
            p = self.pre()
        self.assertEqual(p["paid_requests_made"], 0)

    def test_the_wake_plan_flag_prints_the_owner_command(self):
        with mock.patch.object(snw, "next_wake", return_value=snw.next_wake(D(12), starts_fn=lambda n: [D(21)], listing_fn=LISTED)), \
             mock.patch("builtins.print") as printed, mock.patch.object(ka, "_run", return_value=""):
            flc.main(["--wake-plan"])
        text = " ".join(str(c.args[0]) for c in printed.call_args_list)
        self.assertIn("sudo pmset schedule wake", text)
        self.assertIn("pmset -g sched", text)


# --------------------------------------------------------------------------------------- readiness owner-action states
class TestReadinessOwnerStates(unittest.TestCase):
    def test_the_new_components_exist_and_are_never_hard_failures(self):
        import opening_day_readiness as odr
        src = (REPO / "opening_day_readiness.py").read_text()
        for name in ("MAC_WAKE_PLAN", "MAC_WAKE_SCHEDULED", "TEMP_KEEP_AWAKE", "STREAMLIT_URL", "STREAMLIT_OWNER_CONFIG", "ODDS_RESET_DAY"):
            self.assertIn(f'"{name}"', src)
        for state in ("READY", "OWNER_ACTION_REQUIRED", "WAITING_FOR_LIVE_MARKET", "FAILED"):
            self.assertIn(state, odr.COMPONENT_STATES)


if __name__ == "__main__":
    unittest.main()
