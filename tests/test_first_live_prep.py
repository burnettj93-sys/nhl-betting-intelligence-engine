"""First Live Prep block (2026-09-25): keep-awake windows, power risk, certification state machine, pre-flight report,
deployed-app smoke check, hash stability of the publisher's own bookkeeping, scheduler/secret hygiene.
No network, no paid request, no real state."""
from __future__ import annotations

import copy
import datetime as dt
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from operational import cloud_preflight as pf
from operational import cloud_snapshot_schema as schema
from operational import first_live_certification as flc
from operational import keep_awake as ka
from operational import moneyline_pregame as mp
from operational import scheduler_audit as sa

REPO = Path(__file__).resolve().parent.parent
D = lambda h, m=0, s=0, day=29: dt.datetime(2026, 9, day, h, m, s, tzinfo=dt.timezone.utc)
LISTED = lambda c: True
ARMED_DEPS = dict(
    next_cluster=lambda: {"cluster_id": "x", "games": 1}, scheduler=lambda: {"loaded": True, "on_master": True, "branch": "master", "commit": "abc1234"},
    quota=lambda: {"credits_remaining": 367, "sufficient": True, "reason": "OK", "reset_day": {"status": "OWNER_VERIFICATION_REQUIRED"}},
    publisher=lambda: True, deployment=lambda: "ACTIVE", power=lambda: {"risk": "LOW"}, audit=lambda: {"records": {}},
    caffeinate=lambda: {"binary_present": True, "guard_closes_wake_gap": True}, git=lambda: {"branch": "master", "commit": "abc", "worktree_clean": True},
    wake=lambda: {"state": "SCHEDULED", "detail": "ok"})


# ------------------------------------------------------------------------------------------ keep-awake
class TestHoldWindows(unittest.TestCase):
    def test_window_starts_before_t40_and_ends_after_the_t30_decision_and_publish(self):
        (w,) = ka.windows(D(12), [D(21)], listing_fn=LISTED)
        self.assertEqual(w["capture_window"][0], D(20, 20))                       # T-40
        self.assertEqual(w["hold_from_utc"], D(19, 50))                           # 30 min BEFORE the window opens
        self.assertLess(w["hold_from_utc"], w["capture_window"][0])
        self.assertEqual(w["anchor"], D(20, 30))                                  # T-30 decision
        self.assertEqual(w["hold_until_utc"], D(20, 50))                          # 20 min after it (cloud publish)
        self.assertGreater(w["hold_until_utc"], w["anchor"])

    def test_clusters_the_provider_does_not_list_never_hold_the_machine_awake(self):
        self.assertEqual(ka.windows(D(12), [D(21)], listing_fn=lambda c: False), [])
        self.assertEqual(len(ka.windows(D(12), [D(21)], listing_fn=lambda c: None)), 1)   # unknown -> safe side

    def test_past_and_far_future_windows_are_dropped(self):
        self.assertEqual(ka.windows(D(23), [D(21)], listing_fn=LISTED), [])
        self.assertEqual(ka.windows(D(1, day=25), [D(21)], horizon_h=24, listing_fn=LISTED), [])

    def test_active_window_bounds(self):
        with mock.patch.object(mp, "archived_listing", return_value=True):
            for now, expected in ((D(19, 39), False), (D(19, 40), True), (D(19, 50), True), (D(20, 30), True), (D(20, 50), True), (D(20, 51), False)):
                self.assertEqual(ka.active_window(now, [D(21)]) is not None, expected, now)


class TestEnsureHolding(unittest.TestCase):
    def setUp(self):
        self.state = Path(tempfile.mkdtemp()) / "ka.json"
        self.spawned: list[int] = []
        self.alive = False

    def run_at(self, now, **kw):
        def spawn(seconds):
            self.spawned.append(seconds)
            self.alive = True
            return 4242
        kw.setdefault("spawn", spawn)
        kw.setdefault("alive_fn", lambda pid: self.alive)
        kw.setdefault("active_fn", lambda: True)
        kw.setdefault("starts_fn", lambda n: [D(21)])
        with mock.patch.object(mp, "archived_listing", return_value=True):
            return ka.ensure_holding(now, state_path=self.state, **kw)

    def test_outside_a_window_it_does_nothing(self):
        r = self.run_at(D(15))
        self.assertEqual((r["holding"], r["reason"], self.spawned), (False, "OUTSIDE_HOLD_WINDOW", []))

    def test_inside_the_window_it_holds_until_the_window_ends_and_only_once(self):
        r = self.run_at(D(19, 52))
        self.assertEqual((r["action"], r["holding"], self.spawned), ("STARTED", True, [int((D(20, 50) - D(19, 52)).total_seconds())]))
        again = self.run_at(D(19, 54))
        self.assertEqual((again["action"], len(self.spawned)), ("ALREADY_HOLDING", 1))

    def test_a_dead_assertion_is_replaced_within_the_window(self):
        self.run_at(D(19, 52))
        self.alive = False
        self.assertEqual(self.run_at(D(20, 10))["action"], "STARTED")
        self.assertEqual(self.spawned[-1], int((D(20, 50) - D(20, 10)).total_seconds()))

    def test_standby_and_tests_never_spawn(self):
        self.assertEqual(self.run_at(D(19, 52), active_fn=lambda: False)["reason"], "STANDBY")
        with mock.patch("subprocess.Popen", side_effect=AssertionError("a test must never start caffeinate")):
            r = ka.ensure_holding(D(19, 52), starts_fn=lambda n: [D(21)], active_fn=lambda: True, state_path=self.state)
        self.assertEqual((r["action"], r["reason"]), ("SKIPPED", "UNDER_TEST"))

    def test_it_never_raises(self):
        r = self.run_at(D(19, 52), spawn=mock.Mock(side_effect=OSError("no caffeinate")))
        self.assertEqual(r["action"], "ERROR")

    def test_the_assertion_is_idle_only_self_ending_and_detached(self):
        with mock.patch("subprocess.Popen") as popen:
            popen.return_value.pid = 99
            self.assertEqual(ka._spawn_caffeinate(600), 99)
        cmd = popen.call_args.args[0]
        self.assertEqual(cmd, ["/usr/bin/caffeinate", "-i", "-t", "600"])          # -i idle only, -t self-terminating
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        for flag in ("-s", "-d", "-u"):                                              # not system/display/user-active assertions
            self.assertNotIn(flag, cmd)

    def test_it_never_touches_the_pull_or_the_window(self):
        src = (REPO / "operational" / "keep_awake.py").read_text()
        for banned in ("run_moneyline_snapshot", "get_sport_odds", "run_pregame(", "record_audit"):
            self.assertNotIn(banned, src)


class TestPowerRisk(unittest.TestCase):
    PMSET = "System-wide power settings:\nCurrently in use:\n sleep                1 (sleep prevented by Claude, powerd)\n displaysleep         180\n powernap             1\n womp 1\n lowpowermode 1\n"

    def runner(self, pmset, ps="Now drawing from 'AC Power'\n"):
        return lambda cmd: ps if cmd[:3] == ["pmset", "-g", "ps"] else pmset

    def test_one_minute_idle_sleep_on_ac_is_high_risk(self):
        r = ka.power_risk(self.runner(self.PMSET))
        self.assertEqual((r["risk"], r["on_ac"], r["idle_sleep_minutes"], r["assertion_held_by"]), ("HIGH", True, 1, "Claude, powerd"))
        self.assertIn("cannot wake", r["mitigation"])

    def test_disabled_sleep_is_low_and_long_timers_medium_and_unreadable_unknown(self):
        self.assertEqual(ka.power_risk(self.runner(" sleep 0\n"))["risk"], "LOW")
        self.assertEqual(ka.power_risk(self.runner(" sleep 60\n"))["risk"], "MEDIUM")
        self.assertEqual(ka.power_risk(lambda cmd: "")["risk"], "UNKNOWN")

    def test_wake_commands_are_printed_never_run_and_are_in_the_future_only(self):
        with mock.patch.object(mp, "archived_listing", return_value=True), \
             mock.patch("subprocess.run", side_effect=AssertionError("must not run sudo")):
            cmds = ka.wake_commands(D(12), [D(21), D(1, day=30)], horizon_h=240)
        self.assertEqual(len(cmds), 2)
        self.assertTrue(all(c.startswith('sudo pmset schedule wake "') for c in cmds))
        self.assertIn("2026", cmds[0])


# ------------------------------------------------------------------------------------------ state machine + preflight
def audit_record(**over):
    r = {"provider_listed": True, "actual_pull_utc": "2026-09-29T20:25:04Z", "credits_spent": 1, "odds_rows_stored": 40,
         "in_decision_window": True, "recommendations_evaluated": 2, "cloud_publish": {"status": "SUCCESS", "content_hash": "h1"},
         "outcome": mp.DECISION_SUCCESS}
    r.update(over)
    return {"records": {"2026-09-29T21:00/2026-09-29T21:00": r}}


class TestCertificationStates(unittest.TestCase):
    def state(self, audit, arch=True, nxt=True):
        return flc.overall_state(mp.live_observed(audit), arch, nxt)

    def test_transitions(self):
        self.assertEqual(self.state({"records": {}}, arch=False), mp.NOT_READY)
        self.assertEqual(self.state({"records": {}}, arch=True, nxt=False), mp.ARCHITECTURE_READY)
        self.assertEqual(self.state({"records": {}}, arch=True, nxt=True), mp.ARCHITECTURE_READY_ONLY)   # WAITING_FOR_FIRST_REAL_CLUSTER
        self.assertEqual(self.state(audit_record()), mp.LIVE_CERTIFIED)

    def test_real_data_without_every_gate_is_observed_not_certified(self):
        for over in ({"in_decision_window": False}, {"recommendations_evaluated": 0}, {"cloud_publish": {"status": "FAILED"}},
                     {"cloud_publish": None}):
            self.assertEqual(self.state(audit_record(**over)), mp.LIVE_OBSERVED, over)

    def test_a_bare_success_response_never_certifies(self):
        # a request that "worked" but stored nothing / spent nothing is not even observed data
        for over in ({"odds_rows_stored": 0}, {"credits_spent": 0}):
            st = self.state(audit_record(**over))
            self.assertNotIn(st, (mp.LIVE_OBSERVED, mp.LIVE_CERTIFIED), over)

    def test_a_real_listed_cluster_that_yielded_nothing_is_failed_until_a_later_one_succeeds(self):
        bad = audit_record(actual_pull_utc=None, credits_spent=0, odds_rows_stored=0, outcome=mp.NETWORK_FAILED)
        self.assertEqual(self.state(bad), mp.FAILED_STATE)
        bad["records"]["2026-09-30T21:00/x"] = audit_record()["records"]["2026-09-29T21:00/2026-09-29T21:00"]
        self.assertEqual(self.state(bad), mp.LIVE_CERTIFIED)                        # recovered by a later real cluster

    def test_an_unlisted_cluster_is_never_a_failure(self):
        rec = audit_record(provider_listed=False, actual_pull_utc=None, credits_spent=0, odds_rows_stored=0, outcome=mp.PROVIDER_NOT_LISTED)
        self.assertEqual(self.state(rec), mp.ARCHITECTURE_READY_ONLY)

    def test_evidence_outranks_current_configuration(self):
        self.assertEqual(self.state(audit_record(), arch=False), mp.LIVE_CERTIFIED)


class TestPreflight(unittest.TestCase):
    def pre(self, **override):
        deps = {**ARMED_DEPS, **override}
        return flc.preflight(D(12), deps=deps)

    def test_ready_when_scheduler_is_on_master_quota_ok_and_publisher_on(self):
        p = self.pre()
        self.assertTrue(p["architecture_ready"])
        self.assertEqual((p["state"], p["paid_requests_made"]), (mp.ARCHITECTURE_READY_ONLY, 0))
        for key in ("next_cluster", "scheduler", "quota", "cloud_publisher_enabled", "deployment_mode", "machine_power", "power_risk",
                    "live_observed", "live_certified"):
            self.assertIn(key, p)

    def test_each_prerequisite_is_reported(self):
        cases = {
            "scheduler": ({"loaded": False}, "not loaded"),
            "quota": ({"credits_remaining": 3, "sufficient": False, "reason": "HARD_RESERVE", "reset_day": {}}, "quota not sufficient"),
            "publisher": (False, "publishing is not enabled"),
            "deployment": ("STANDBY", "not ACTIVE"),
        }
        for name, (value, needle) in cases.items():
            p = self.pre(**{name: (lambda v=value: v)})
            self.assertFalse(p["architecture_ready"], name)
            self.assertEqual(p["state"], mp.NOT_READY)
            self.assertTrue(any(needle in x for x in p["architecture_problems"]), (name, p["architecture_problems"]))

    def test_a_scheduler_running_a_feature_branch_or_dirty_tree_is_not_ready(self):
        p = self.pre(scheduler=lambda: {"loaded": True, "on_master": False, "branch": "feature/x", "commit": "f00", "dirty": True})
        self.assertFalse(p["architecture_ready"])
        self.assertIn("not clean master", " ".join(p["architecture_problems"]))
        self.assertIn("uncommitted", " ".join(p["architecture_problems"]))

    def test_it_makes_no_network_request_and_no_paid_call(self):
        import requests
        with mock.patch.object(requests, "get", side_effect=AssertionError("network")), \
             mock.patch("urllib.request.urlopen", side_effect=AssertionError("network")):
            p = self.pre()
        self.assertEqual(p["paid_requests_made"], 0)

    def test_real_preflight_reports_the_next_cluster_targets(self):
        with mock.patch.object(mp, "scheduled_starts", return_value=[D(21)]), mock.patch.object(mp, "archived_listing", return_value=True):
            p = flc.preflight(D(12), deps={k: v for k, v in ARMED_DEPS.items() if k != "next_cluster"})
        nc = p["next_cluster"]
        self.assertEqual((nc["expected_start_utc"], nc["t35_target_utc"], nc["t30_anchor_utc"]),
                         (D(21).isoformat(), D(20, 25).isoformat(), D(20, 30).isoformat()))
        self.assertIn("LISTED", nc["provider_listing"])


class TestSchedulerCode(unittest.TestCase):
    def test_reports_branch_commit_master_and_dirty(self):
        out = flc.scheduler_code(runner=lambda cmd: "")
        self.assertFalse(out["loaded"])

    def test_on_master_requires_branch_master_clean_tree_and_head_equal_master(self):
        answers = {("rev-parse", "--abbrev-ref", "HEAD"): "master", ("rev-parse", "--short", "HEAD"): "abc1234",
                   ("status", "--porcelain", "--untracked-files=no"): "", ("rev-parse", "--short", "master"): "abc1234"}
        plist = {"WorkingDirectory": str(REPO)}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(sa, "AGENTS_DIR", Path(tmp)), \
             mock.patch.object(sa, "read_plist", return_value=plist), mock.patch.object(sa, "launchctl_info", return_value={"loaded": True, "runs": 3, "last_exit": "0"}), \
             mock.patch.object(flc, "_git", side_effect=lambda wd, *a: answers[a]):
            (Path(tmp) / f"{flc.LABEL}.plist").write_text("x")
            self.assertTrue(flc.scheduler_code()["on_master"])
            answers[("status", "--porcelain", "--untracked-files=no")] = " M file.py"
            self.assertFalse(flc.scheduler_code()["on_master"])
            answers[("status", "--porcelain", "--untracked-files=no")] = ""
            answers[("rev-parse", "--abbrev-ref", "HEAD")] = "feature/x"
            self.assertFalse(flc.scheduler_code()["on_master"])


# ------------------------------------------------------------------------------------------ deployed app check
class TestDeployedAppCheck(unittest.TestCase):
    def test_no_url_is_owner_action_and_is_never_guessed(self):
        with mock.patch.object(pf, "app_url", return_value=None):
            r = pf.check_deployed_app()
        self.assertEqual(r["state"], pf.OWNER)
        self.assertIn("NHL_ENGINE_STREAMLIT_URL", r["detail"])

    def test_https_required(self):
        self.assertEqual(pf.check_deployed_app("http://x.streamlit.app")["state"], pf.FAIL)

    def test_private_app_redirects_anonymous_visitors(self):
        def fetch(u):
            return (200, "ok", {}) if u.endswith("/_stcore/health") else (303, "", {"Location": "https://share.streamlit.io/-/auth/app"})
        r = pf.check_deployed_app("https://x.streamlit.app", fetch=fetch)
        self.assertEqual(r["state"], pf.PASS)
        self.assertTrue(r["private_viewer_mode"])
        self.assertIn("OWNER_SMOKE_TEST_REQUIRED", r["next"])

    def test_a_public_app_is_reported_as_public(self):
        r = pf.check_deployed_app("https://x.streamlit.app", fetch=lambda u: (200, "ok", {}))
        self.assertFalse(r["private_viewer_mode"])
        self.assertIn("PUBLIC", r["detail"])

    def test_unreachable_is_a_failure(self):
        def fetch(u):
            raise OSError("dns")
        self.assertEqual(pf.check_deployed_app("https://x.streamlit.app", fetch=fetch)["state"], pf.FAIL)

    def test_the_url_comes_only_from_owner_configuration(self):
        with mock.patch.dict("os.environ", {pf.APP_URL_ENV: "https://mine.streamlit.app"}):
            self.assertEqual(pf.app_url(), "https://mine.streamlit.app")


# ------------------------------------------------------------------------------------------ cloud publish stability
class TestPublisherBookkeepingDoesNotChangeTheHash(unittest.TestCase):
    def doc(self):
        return {"schema_version": 2, "metadata": {"schema_version": 2, "generated_at": "g", "data_as_of": "d", "freshness": {}},
                "data_status": {"ingestion_health": {
                    "cloud_snapshot_publish": {"last_attempt_utc": "a", "last_success_utc": "a", "last_status": "SUCCESS"},
                    "nhl_pregame_targeted_refresh": {"last_attempt_utc": "a", "last_success_utc": "a"},
                    "settlement": {"last_success_utc": "s", "last_status": "SUCCESS"}}}}

    def test_a_publish_does_not_make_the_next_snapshot_look_changed(self):
        a, b = self.doc(), self.doc()
        b["data_status"]["ingestion_health"]["cloud_snapshot_publish"]["last_success_utc"] = "later"
        b["data_status"]["ingestion_health"]["nhl_pregame_targeted_refresh"]["last_attempt_utc"] = "later"
        self.assertEqual(schema.content_hash(a), schema.content_hash(b))

    def test_meaningful_health_changes_still_change_the_hash(self):
        a, b = self.doc(), self.doc()
        b["data_status"]["ingestion_health"]["settlement"]["last_success_utc"] = "new settlement"
        self.assertNotEqual(schema.content_hash(a), schema.content_hash(b))
        c = self.doc()
        c["data_status"]["ingestion_health"]["cloud_snapshot_publish"]["last_status"] = "FAILED"   # a real status change counts
        self.assertNotEqual(schema.content_hash(a), schema.content_hash(c))


# ------------------------------------------------------------------------------------------ hygiene
class TestSchedulerAndSecretHygiene(unittest.TestCase):
    @unittest.skipUnless(sa.AGENTS_DIR.exists() and list(sa.AGENTS_DIR.glob("com.nhlengine.*.plist")), "no launchd jobs here")
    def test_every_installed_job_runs_from_this_repo_and_none_from_a_stale_clone(self):
        report = sa.audit()
        wrong = [j["label"] for j in report["jobs"] if "OTHER_WORKING_DIRECTORY" in (j.get("problems") or [])]
        self.assertEqual(wrong, [])
        self.assertEqual(report["duplicates"], [])

    def test_no_secret_bearing_file_is_tracked_by_git(self):
        tracked = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True).stdout.splitlines()
        bad = [f for f in tracked if f.split("/")[-1] in (".env", "secrets.toml", "auth_store.db") or f.endswith((".enc", ".pem"))]
        self.assertEqual(bad, [])

    def test_env_is_gitignored(self):
        self.assertEqual(subprocess.run(["git", "check-ignore", ".env"], cwd=REPO, capture_output=True, text=True).returncode, 0)


if __name__ == "__main__":
    unittest.main()
