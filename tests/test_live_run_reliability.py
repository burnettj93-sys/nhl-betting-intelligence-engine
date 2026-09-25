"""
Live Run Reliability block (2026-09-25): test/production state isolation, late-boot morning catch-up, T-35
audit + failure classification + LIVE_OBSERVED transition, missed-window handling, first-live certification.
Everything uses temp paths / fakes; nothing here touches production state or the network.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from operational import first_live_certification as flc
from operational import morning_catchup as mc
from operational import moneyline_pregame as mp
from operational import runtime_hygiene as hy
from operational import state_paths as sp

REPO = Path(__file__).resolve().parent.parent
D = lambda h, m=0, s=0, day=26: dt.datetime(2026, 9, day, h, m, s, tzinfo=dt.timezone.utc)


def local(h, m=0, day=25):
    """An aware datetime for a LOCAL wall-clock time (the launchd slots are local)."""
    return dt.datetime(2026, 9, day, h, m).astimezone()


# --------------------------------------------------------------------------------------- isolation
class TestTestRuntimeIsolation(unittest.TestCase):
    def test_this_process_is_recognised_as_a_test_run(self):
        self.assertTrue(sp.under_test())

    def test_every_production_state_path_resolves_outside_production(self):
        from ingest import nhl_api
        from operational import ingestion_health, live_odds_daily_pull, prop_discovery, publish_cloud_snapshot as pub
        from operational import real_prop_orchestrator
        paths = {
            "ingestion health cache": ingestion_health.DEFAULT_CACHE_PATH,
            "cloud publish state": pub.STATE_PATH, "cloud publish lock": pub.LOCK_PATH,
            "prop discovery state": prop_discovery.STATE_PATH,
            "prop candidate log": real_prop_orchestrator.PROP_CONTRACT_CANDIDATES_PATH,
            "new-contract candidate log": live_odds_daily_pull.NEW_CONTRACT_CANDIDATES_PATH,
            "pregame state": mp.STATE_PATH, "pregame lock": mp.LOCK_PATH, "pregame audit": mp.AUDIT_PATH,
            "catch-up state": mc.STATE_PATH, "catch-up lock": mc.LOCK_PATH,
            "nhl api retry log": nhl_api.RETRY_LOG_PATH,
        }
        for name, p in paths.items():
            self.assertFalse(sp.is_production_path(Path(p)), f"{name} resolves to a PRODUCTION path under test: {p}")
            self.assertIn("nhl_engine_test_state_", str(p), name)

    def test_an_explicit_state_dir_wins_and_production_paths_apply_outside_tests(self):
        with mock.patch.dict(os.environ, {sp.ENV_DIR: "/tmp/somewhere"}):
            self.assertEqual(sp.path("x.json"), Path("/tmp/somewhere/runtime/x.json"))
        with mock.patch.object(sp, "under_test", return_value=False):
            self.assertEqual(sp.path("x.json"), sp.PRODUCTION_RUNTIME_DIR / "x.json")
            self.assertEqual(sp.path("y.jsonl", area="operational"), sp.PRODUCTION_OPERATIONAL_DIR / "y.jsonl")

    def test_state_written_through_default_paths_never_reaches_production_files(self):
        """A child test process writes probe markers through every default state path; the production files
        must not contain them. (Regression for the fixture-* records / real-publisher pollution.)"""
        marker = "isolation-probe-" + os.urandom(6).hex()
        child = f'''
import unittest, datetime as dt
class T(unittest.TestCase):
    def test_write(self):
        from operational import prop_discovery, moneyline_pregame as mp, morning_catchup as mc, real_prop_orchestrator as rpo
        from operational import ingestion_health
        now = dt.datetime.now(dt.timezone.utc)
        prop_discovery.mark_swept("first", "{marker}", now)
        prop_discovery.record_spend(1, now)
        mp._save_state({{"clusters": {{"{marker}/x": {{"status": "DONE"}}}}}})
        mp._save_audit({{"records": {{"{marker}": {{"cluster_id": "{marker}"}}}}}})
        mc._save_state({{"days": {{"2026-09-25": {{"{marker}": {{}}}}}}}}, None, "2026-09-25")
        ingestion_health.record_run("{marker}", {{"status": "SUCCESS"}})
        rpo.PROP_CONTRACT_CANDIDATES_PATH.parent.mkdir(parents=True, exist_ok=True)
        rpo.PROP_CONTRACT_CANDIDATES_PATH.write_text('{{"event_id": "{marker}"}}\\n')
if __name__ == "__main__":
    unittest.main()
'''
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "test_probe.py"
            script.write_text(child)
            proc = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", tmp, "-p", "test_probe.py"], cwd=REPO, capture_output=True, text=True,
                                  env={**os.environ, "PYTHONPATH": str(REPO)}, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr[-400:])
        production = [sp.PRODUCTION_RUNTIME_DIR / n for n in ("prop_discovery_state.json", "moneyline_pregame_state.json",
                                                              "moneyline_pregame_audit.json", "morning_catchup_state.json")]
        production += [sp.PRODUCTION_OPERATIONAL_DIR / n for n in ("ingestion_health_cache.json", "prop_contract_candidates.jsonl")]
        for p in production:
            if p.exists():
                self.assertNotIn(marker, p.read_text(), f"probe marker leaked into production file {p}")

    def test_a_test_run_never_makes_a_real_odds_api_request(self):
        from research.live_sog_pricing import client
        with mock.patch.object(client, "get_the_odds_api_key", return_value="real-looking-key"):
            r = client.get_nhl_events()                         # requests.get is NOT patched here
        self.assertFalse(r.ok)
        self.assertIn("blocked", r.error)

    def test_production_files_hold_no_test_residue(self):
        """Tripwire: after everything ran before this test (alphabetical order, discovery already imported all
        modules), the real runtime must still be clean."""
        report = hy.audit()
        self.assertTrue(report["clean"], report["findings"])


# --------------------------------------------------------------------------------------- hygiene cleanup
class TestSyntheticCleanup(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.fixtures = self.tmp / "fixtures"
        self.fixtures.mkdir()
        (self.fixtures / "sog.json").write_text(json.dumps({"id": "fixture-sog-evt-1", "bookmakers": []}))
        self.log = self.tmp / "cands.jsonl"
        self.runtime = self.tmp / "runtime"
        self.runtime.mkdir()
        self.removals = self.tmp / "removals.jsonl"
        self.real = {"market_key": "player_shots_on_goal", "event_id": "a" * 32, "status": "CONTRACT_CANDIDATE"}
        self.fake = {"market_key": "player_shots_on_goal", "event_id": "fixture-sog-evt-1"}
        self.unknown = {"market_key": "player_total_saves", "event_id": "fixture-not-in-any-file"}

    def write(self, *lines):
        self.log.write_text("".join((l if isinstance(l, str) else json.dumps(l, sort_keys=True)) + "\n" for l in lines))

    def clean(self, **kw):
        return hy.clean(self.log, self.runtime, self.fixtures, self.removals, **kw)

    def test_removes_only_records_proven_synthetic_and_preserves_everything_else_byte_for_byte(self):
        self.write(self.real, self.fake, self.unknown, "not json at all")
        real_line = json.dumps(self.real, sort_keys=True)
        out = self.clean()
        self.assertEqual([r["event_id"] for r in out["removed"]], ["fixture-sog-evt-1"])
        lines = self.log.read_text().splitlines()
        self.assertEqual(lines, [real_line, json.dumps(self.unknown, sort_keys=True), "not json at all"])

    def test_every_removal_is_recorded(self):
        self.write(self.fake)
        self.clean()
        rec = json.loads(self.removals.read_text().splitlines()[0])
        self.assertEqual((rec["kind"], rec["event_id"]), ("synthetic_candidate_record", "fixture-sog-evt-1"))
        self.assertIn("fixture", rec["proof"])

    def test_dry_run_changes_nothing(self):
        self.write(self.real, self.fake)
        before = self.log.read_text()
        out = self.clean(dry_run=True)
        self.assertEqual(len(out["removed"]), 1)
        self.assertEqual(self.log.read_text(), before)
        self.assertFalse(self.removals.exists())

    def test_only_zero_byte_databases_are_removed(self):
        (self.runtime / "empty.db").write_bytes(b"")
        (self.runtime / "real.db").write_bytes(b"SQLite format 3\x00" + b"x" * 100)
        (self.runtime / "notes.txt").write_text("")
        out = self.clean()
        self.assertEqual([r["file"] for r in out["removed"] if r["kind"] == "empty_stray_db"], ["empty.db"])
        self.assertFalse((self.runtime / "empty.db").exists())
        self.assertTrue((self.runtime / "real.db").exists())
        self.assertTrue((self.runtime / "notes.txt").exists())

    def test_audit_flags_synthetic_and_empty_and_passes_when_clean(self):
        self.write(self.real, self.fake)
        (self.runtime / "empty.db").write_bytes(b"")
        rep = hy.audit(self.log, self.runtime, self.fixtures)
        self.assertFalse(rep["clean"])
        self.assertEqual(rep["candidate_records"], {"real": 1, "synthetic": 1})
        self.clean()
        rep = hy.audit(self.log, self.runtime, self.fixtures)
        self.assertTrue(rep["clean"], rep["findings"])
        self.assertEqual(rep["candidate_records"], {"real": 1, "synthetic": 0})

    def test_a_real_event_id_is_never_synthetic(self):
        self.assertFalse(hy.is_proven_synthetic(self.real, {"a" * 32}))
        self.assertFalse(hy.is_proven_synthetic({"event_id": "fixture-x"}, set()))         # not proven by a fixture file


# --------------------------------------------------------------------------------------- morning catch-up
def health_with(**succeeded_at):
    return {k: {"last_status": "SUCCESS", "last_success_utc": v.astimezone(dt.timezone.utc).isoformat()} for k, v in succeeded_at.items()}


class CatchupCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.state = self.tmp / "s.json"
        self.lock = self.tmp / "l"
        self.health: dict = {}
        self.ran: list[list[str]] = []
        self.now = local(8, 30)

    def runner(self, succeed=True, rc=0):
        def fn(cmd, **kw):
            self.ran.append(cmd)
            comp = {"sync_daily.py": "nhl_sync_full", "operational.settle_daily_observations": "settlement",
                    "operational.daily_postmortem": "postmortem", "operational.backup_databases": "database_backups"}[cmd[-1]]
            if succeed:
                self.health[comp] = {"last_status": "SUCCESS", "last_success_utc": self.now.astimezone(dt.timezone.utc).isoformat()}
            return mock.Mock(returncode=rc)
        return fn

    def run_catchup(self, now=None, **kw):
        kw.setdefault("runner", self.runner())
        kw.setdefault("active_fn", lambda: True)
        return mc.run_catchup(now or self.now, health=None, health_fn=lambda: self.health, state_path=self.state,
                              lock_path=self.lock, **kw)


class TestStagesNeedingCatchup(unittest.TestCase):
    def test_nothing_is_due_before_the_slot_plus_grace(self):
        self.assertEqual(mc.stages_needing_catchup(local(7, 29), {}), [])                  # 07:00 + 30 min not yet reached
        self.assertEqual([s["component"] for s in mc.stages_needing_catchup(local(7, 30), {})], ["nhl_sync_full"])

    def test_a_success_since_the_slot_means_nothing_to_do(self):
        h = health_with(nhl_sync_full=local(7, 5), settlement=local(7, 20), postmortem=local(7, 33), database_backups=local(7, 47))
        self.assertEqual(mc.stages_needing_catchup(local(9, 0), h), [])

    def test_yesterdays_success_does_not_count(self):
        h = health_with(nhl_sync_full=local(7, 5, day=24))
        self.assertIn("nhl_sync_full", [s["component"] for s in mc.stages_needing_catchup(local(8, 0), h)])

    def test_a_deferred_or_failed_last_run_without_a_new_success_is_still_due(self):
        h = {"settlement": {"last_status": "DEFERRED", "last_success_utc": local(7, 15, day=24).astimezone(dt.timezone.utc).isoformat()}}
        self.assertIn("settlement", [s["component"] for s in mc.stages_needing_catchup(local(8, 30), h)])


class TestLateBootRecovery(CatchupCase):
    def test_mac_starting_at_0830_recovers_the_whole_chain_in_order_once(self):
        r = self.run_catchup()
        self.assertEqual(r["status"], "CATCHUP_RAN")
        self.assertEqual(r["reason"], "CATCHUP_AFTER_MISSED_SLOT")
        order = [c[-1] for c in self.ran]
        self.assertEqual(order, ["sync_daily.py", "operational.settle_daily_observations",
                                 "operational.daily_postmortem", "operational.backup_databases"])
        self.assertTrue(all(a["recovered"] for a in r["actions"]))
        self.assertTrue(all(a["reason"] == "CATCHUP_AFTER_MISSED_SLOT" for a in r["actions"]))

    def test_it_is_idempotent_a_second_pass_runs_nothing(self):
        self.run_catchup()
        n = len(self.ran)
        again = self.run_catchup(now=self.now + dt.timedelta(minutes=30))
        self.assertEqual(again["reason"], "NOTHING_DUE")
        self.assertEqual(len(self.ran), n)

    def test_normal_day_does_nothing(self):
        self.health = health_with(nhl_sync_full=local(7, 1), settlement=local(7, 16), postmortem=local(7, 31), database_backups=local(7, 46))
        r = self.run_catchup()
        self.assertEqual((r["reason"], self.ran), ("NOTHING_DUE", []))

    def test_only_the_missing_stages_are_run(self):
        self.health = health_with(nhl_sync_full=local(7, 44))                      # sync was run manually at 07:44
        self.run_catchup()
        self.assertEqual([c[-1] for c in self.ran], ["operational.settle_daily_observations", "operational.daily_postmortem",
                                                       "operational.backup_databases"])

    def test_a_failed_sync_blocks_settlement_and_postmortem_but_not_the_independent_backup(self):
        r = self.run_catchup(runner=self.runner(succeed=False, rc=1))
        by = {a["component"]: a["action"] for a in r["actions"]}
        self.assertEqual(by["nhl_sync_full"], "RAN")
        self.assertEqual(by["settlement"], "SKIPPED_UPSTREAM_NOT_RECOVERED")
        self.assertEqual(by["postmortem"], "SKIPPED_UPSTREAM_NOT_RECOVERED")
        self.assertEqual(by["database_backups"], "RAN")                             # independent of the chain
        self.assertEqual(sum(1 for c in self.ran if c[-1] == "sync_daily.py"), 1)

    def test_attempts_are_bounded_spaced_and_never_an_infinite_loop(self):
        bad = self.runner(succeed=False, rc=1)
        self.run_catchup(runner=bad)                                                # attempt 1 (08:30)
        r = self.run_catchup(runner=bad, now=self.now + dt.timedelta(minutes=10))
        self.assertIn("SKIPPED_RETRY_TOO_SOON", [a["action"] for a in r["actions"]])   # the NHL API is not hammered
        self.run_catchup(runner=bad, now=self.now + dt.timedelta(minutes=35))       # attempt 2
        r = self.run_catchup(runner=bad, now=self.now + dt.timedelta(minutes=80))
        self.assertIn("SKIPPED_MAX_ATTEMPTS", [a["action"] for a in r["actions"]])
        syncs = [c for c in self.ran if c[-1] == "sync_daily.py"]
        self.assertEqual(len(syncs), mc.MAX_ATTEMPTS)

    def test_the_attempt_is_recorded_before_running_so_a_crash_still_counts(self):
        def boom(cmd, **kw):
            raise RuntimeError("machine slept mid-run")
        r = self.run_catchup(runner=boom)
        self.assertEqual(r["status"], "FAILED")                                     # never raises
        day = json.loads(self.state.read_text())["days"]
        self.assertEqual(list(day.values())[0]["nhl_sync_full"]["attempts"], 1)

    def test_timeouts_are_bounded_and_reported(self):
        def slow(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
        r = self.run_catchup(runner=slow)
        self.assertEqual(r["actions"][0]["action"], "TIMEOUT")
        self.assertFalse(r["actions"][0]["recovered"])

    def test_a_test_run_never_launches_the_real_morning_jobs(self):
        """Regression: a test that called nhl_sync._main(--mode=pregame) once ran the REAL sync, settlement,
        post-mortem and backup (and, through them, the real cloud publisher)."""
        with mock.patch("subprocess.run", side_effect=AssertionError("a real job was launched")):
            r = mc.run_catchup(self.now, health_fn=lambda: {}, state_path=self.state, lock_path=self.lock,
                               active_fn=lambda: True)               # no runner => the real one => refused under test
        self.assertEqual((r["status"], r["reason"]), ("SKIPPED", "UNDER_TEST"))

    def test_child_processes_of_a_test_inherit_the_under_test_marker(self):
        self.assertTrue(sp.under_test())
        self.assertEqual(os.environ.get(sp.ENV_TESTING), "1")
        out = subprocess.run([sys.executable, "-c", "from operational import state_paths as s; print(s.under_test())"],
                             capture_output=True, text=True, cwd=str(REPO), env={**os.environ, "PYTHONPATH": str(REPO)}).stdout.strip()
        self.assertEqual(out, "True")

    def test_standby_machines_do_nothing(self):
        r = self.run_catchup(active_fn=lambda: False)
        self.assertEqual((r["status"], r["reason"], self.ran), ("SKIPPED", "STANDBY", []))

    def test_a_held_lock_prevents_a_duplicate_sync(self):
        import fcntl
        with open(self.lock, "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            r = self.run_catchup()
        self.assertEqual((r["status"], r["reason"], self.ran), ("SKIPPED", "ANOTHER_INSTANCE_RUNNING", []))

    def test_status_reports_pending_and_what_ran_today(self):
        self.assertIn("nhl_sync_full", mc.status(self.now, health={}, state_path=self.state)["stages_pending_catchup"])
        self.run_catchup()
        s = mc.status(self.now, health=self.health, state_path=self.state)
        self.assertEqual(s["stages_pending_catchup"], [])
        self.assertEqual(s["catchups_today"]["nhl_sync_full"], "RECOVERED")


class TestPregameRefreshHostsTheCatchup(unittest.TestCase):
    def _run_main(self, catchup):
        from operational import nhl_sync
        with mock.patch("operational.deployment_mode.require_active_scheduler_or_exit", return_value=True), \
             mock.patch("sys.argv", ["nhl_sync", "--mode=pregame"]), \
             mock.patch.object(nhl_sync, "run_targeted_pregame_refresh", return_value={"status": "SUCCESS"}) as refresh, \
             mock.patch.object(mc, "run_catchup", **catchup), mock.patch("builtins.print") as printed:
            nhl_sync._main()
        return refresh, json.loads(printed.call_args[0][0])

    def test_catchup_runs_first_and_is_reported_when_it_did_something(self):
        refresh, out = self._run_main({"return_value": {"status": "CATCHUP_RAN", "reason": "CATCHUP_AFTER_MISSED_SLOT", "actions": []}})
        refresh.assert_called_once()
        self.assertEqual(out["morning_catchup"]["status"], "CATCHUP_RAN")

    def test_nothing_due_is_not_noise_in_the_log(self):
        _, out = self._run_main({"return_value": {"status": "OK", "reason": "NOTHING_DUE", "actions": []}})
        self.assertNotIn("morning_catchup", out)

    def test_a_crashing_catchup_never_stops_the_pregame_refresh(self):
        refresh, out = self._run_main({"side_effect": RuntimeError("x")})
        refresh.assert_called_once()
        self.assertEqual(out["status"], "SUCCESS")

    def test_other_modes_do_not_run_the_catchup(self):
        from operational import nhl_sync
        with mock.patch("operational.deployment_mode.require_active_scheduler_or_exit", return_value=True), \
             mock.patch("sys.argv", ["nhl_sync", "--mode=midday"]), \
             mock.patch.object(nhl_sync, "run_midday_refresh", return_value={"status": "SUCCESS"}), \
             mock.patch.object(mc, "run_catchup") as c, mock.patch("builtins.print"):
            nhl_sync._main()
        c.assert_not_called()


# --------------------------------------------------------------------------------------- T-35 audit
ORCH = lambda ids, actions: {"results": [{"game_id": g, "action": a, "status": "DATA_UNAVAILABLE" if a == "DATA_UNAVAILABLE" else "INSERTED"}
                                          for g in ids for a in actions]}


class PregameCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.state, self.lock, self.audit = self.tmp / "s.json", self.tmp / "l", self.tmp / "a.json"

    def fire(self, now, starts=(D(23, 0),), pull=None, guard=None, listing=None):
        return mp.run_pregame(now, starts_fn=lambda n: list(starts), pull_fn=pull or (lambda: {
            "ran": True, "captured_at_utc": "2026-09-26T22:25:04Z", "credits_spent_this_run": 1}),
            guard_fn=guard or (lambda: {"allow": True}), listing_fn=listing or (lambda c: {"listed": True}),
            state_path=self.state, lock_path=self.lock, audit_path=self.audit)

    def audited(self, result, ids=(101,), **extra):
        result = {**result, **extra}
        return mp.record_audit(result, D(22, 26), game_ids_fn=lambda c: set(ids), audit_path=self.audit)


class TestMissedWindow(PregameCase):
    def test_a_machine_that_slept_through_the_window_is_recorded_once_and_never_pulled_late(self):
        self.fire(D(20, 0))                                                    # heartbeat before the window
        pulls = []
        r = self.fire(D(22, 45), pull=lambda: pulls.append(1) or {"ran": True})  # first firing AFTER T-30 closed
        self.assertEqual(pulls, [])                                            # no late paid pull
        self.assertEqual(r["missed_windows"], ["2026-09-26T23:00/2026-09-26T23:00"])
        rec = mp.load_audit(self.audit)["records"]["2026-09-26T23:00/2026-09-26T23:00"]
        self.assertEqual(rec["outcome"], mp.MISSED_WINDOW)
        self.assertIn(mp.MACHINE_ASLEEP, rec["tags"])
        self.assertEqual((rec["credits_spent"], rec["odds_rows_stored"]), (0, 0))
        again = self.fire(D(22, 50))
        self.assertNotIn("missed_windows", again)                              # recorded exactly once

    def test_a_late_firing_inside_a_closed_window_cannot_satisfy_the_policy(self):
        self.fire(D(20, 0))
        r = self.fire(D(22, 31))                                               # 1 minute after the T-30 edge
        self.assertEqual(r["reason"], "NO_CLUSTER_DUE")

    def test_firings_inside_the_window_that_failed_are_network_failures_not_sleep(self):
        self.fire(D(20, 0))
        bad = lambda: {"ran": False, "api_error": "network error: ConnectionError"}
        self.fire(D(22, 24), pull=bad)
        self.fire(D(22, 26), pull=bad)
        self.fire(D(22, 28), pull=bad)
        self.fire(D(22, 40))
        rec = mp.load_audit(self.audit)["records"]["2026-09-26T23:00/2026-09-26T23:00"]
        self.assertEqual(rec["outcome"], mp.NETWORK_FAILED)
        self.assertNotIn(mp.MACHINE_ASLEEP, rec["tags"])

    def test_a_quota_deferral_through_the_window_is_classified(self):
        self.fire(D(20, 0))
        self.fire(D(22, 25), guard=lambda: {"allow": False, "reason": "HARD_RESERVE"})
        self.fire(D(22, 40))
        self.assertEqual(mp.load_audit(self.audit)["records"]["2026-09-26T23:00/2026-09-26T23:00"]["outcome"], mp.QUOTA_DEFERRED)

    def test_windows_that_closed_before_the_job_existed_are_not_reported_as_missed(self):
        r = self.fire(D(22, 45))                                               # very first firing ever
        self.assertNotIn("missed_windows", r)
        self.assertEqual(mp.load_audit(self.audit)["records"], {})


class TestFailureClassification(unittest.TestCase):
    def test_each_situation_gets_its_own_label(self):
        c = mp.classify_outcome
        self.assertEqual(c(listed=False)[0], mp.PROVIDER_NOT_LISTED)
        self.assertEqual(c(listed=True, guard_reason="DAILY_SOFT_BUDGET")[0], mp.QUOTA_DEFERRED)
        self.assertEqual(c(listed=True, api_error="network error: ReadTimeout")[0], mp.NETWORK_FAILED)
        self.assertEqual(c(listed=True, api_error="HTTP 401 unauthorized")[0], mp.API_FAILED)
        self.assertEqual(c(listed=True, pull_ran=True, rows_stored=0)[0], mp.EMPTY_RESPONSE)
        primary, tags = c(listed=True, pull_ran=True, rows_stored=40, evaluated=2)
        self.assertEqual((primary, tags), (mp.DECISION_SUCCESS, [mp.STORED_SUCCESSFULLY, mp.DECISION_SUCCESS]))
        primary, tags = c(listed=True, pull_ran=True, rows_stored=40, evaluated=0, data_unavailable=2)
        self.assertEqual((primary, tags), (mp.DECISION_DATA_UNAVAILABLE, [mp.STORED_SUCCESSFULLY, mp.DECISION_DATA_UNAVAILABLE]))
        self.assertEqual(c(listed=True, pull_ran=False)[0], mp.MISSED_WINDOW)

    def test_nothing_collapses_into_a_generic_failed(self):
        for kwargs in ({"listed": False}, {"listed": True, "guard_reason": "X"}, {"listed": True, "api_error": "boom"},
                       {"listed": True, "pull_ran": True}):
            self.assertNotEqual(mp.classify_outcome(**kwargs)[0], "FAILED")


class TestClusterAuditRecord(PregameCase):
    def full_chain(self, actions=("BET", "PASS"), publish="SUCCESS"):
        r = self.fire(D(22, 25))
        return self.audited(r, real_odds_bridge={"rows_written": 40},
                            real_recommendation_orchestrator=ORCH([101, 102], actions),
                            cloud_publish={"status": publish, "reason": "PUBLISHED"})

    def test_record_has_every_field_the_first_live_review_needs(self):
        (rec,) = self.full_chain()
        for field in ("cluster_id", "games", "scheduled_starts", "target_pull_utc", "actual_pull_utc", "provider_listed",
                      "api_status", "credits_spent", "odds_rows_stored", "decision_anchor_utc", "recommendations_evaluated",
                      "bet_count", "wait_count", "pass_count", "data_unavailable_count", "cloud_publish", "outcome", "tags"):
            self.assertIn(field, rec)
        self.assertEqual((rec["provider_listed"], rec["credits_spent"], rec["odds_rows_stored"]), (True, 1, 40))
        self.assertEqual((rec["bet_count"], rec["pass_count"], rec["recommendations_evaluated"]), (1, 1, 2))
        self.assertEqual(rec["decision_anchor_utc"], D(22, 30).isoformat())
        self.assertTrue(rec["in_decision_window"])
        self.assertEqual(rec["cloud_publish"]["status"], "SUCCESS")
        self.assertEqual(rec["outcome"], mp.DECISION_SUCCESS)

    def test_counts_only_cover_the_clusters_own_games(self):
        r = self.fire(D(22, 25))
        (rec,) = mp.record_audit({**r, "real_odds_bridge": {"rows_written": 4},
                                  "real_recommendation_orchestrator": ORCH([101, 999], ["WAIT"])},
                                 D(22, 26), game_ids_fn=lambda c: {101}, audit_path=self.audit)
        self.assertEqual(rec["wait_count"], 1)

    def test_no_secrets_or_raw_payloads_in_the_record(self):
        (rec,) = self.full_chain()
        text = json.dumps(rec).lower()
        for banned in ("apikey", "api_key", "bookmakers", "outcomes", "token"):
            self.assertNotIn(banned, text)

    def test_idle_firings_write_nothing(self):
        r = self.fire(D(20, 0))
        self.assertEqual(mp.record_audit(r, D(20, 0), audit_path=self.audit), [])

    def test_provider_not_listed_is_audited_at_zero_credits(self):
        r = self.fire(D(22, 25), listing=lambda c: {"listed": False})
        (rec,) = self.audited(r)
        self.assertEqual((rec["outcome"], rec["credits_spent"], rec["provider_listed"]), (mp.PROVIDER_NOT_LISTED, 0, False))

    def test_a_failed_request_is_audited_as_network_failed(self):
        r = self.fire(D(22, 25), pull=lambda: {"ran": False, "api_error": "network error: ConnectionError"})
        (rec,) = self.audited(r)
        self.assertEqual(rec["outcome"], mp.NETWORK_FAILED)

    def test_a_quota_deferral_is_audited(self):
        r = self.fire(D(22, 25), guard=lambda: {"allow": False, "reason": "HARD_RESERVE"})
        (rec,) = self.audited(r)
        self.assertEqual(rec["outcome"], mp.QUOTA_DEFERRED)

    def test_audit_is_bounded(self):
        audit = {"records": {f"2026-01-{i:02d}T00:00/x": {} for i in range(1, 29)}}
        with mock.patch.object(mp, "AUDIT_KEEP", 5):
            mp._save_audit(audit, self.audit)
        self.assertEqual(len(mp.load_audit(self.audit)["records"]), 5)


class TestLiveObservedTransition(PregameCase):
    def test_architecture_ready_but_not_live_observed_until_a_real_cluster_completes(self):
        self.assertEqual(mp.live_observed({"records": {}}), {"status": mp.ARCHITECTURE_READY_ONLY, "architecture_ready": True,
                                                            "live_observed": False, "incomplete_real_clusters": [], "complete_clusters": 0})

    def test_a_not_listed_cluster_never_counts(self):
        r = self.fire(D(22, 25), listing=lambda c: {"listed": False})
        self.audited(r)
        self.assertFalse(mp.live_observed(mp.load_audit(self.audit))["live_observed"])

    def test_every_link_of_the_chain_is_required_and_a_bet_is_not(self):
        base = self.fire(D(22, 25))
        complete = dict(real_odds_bridge={"rows_written": 40}, real_recommendation_orchestrator=ORCH([101], ["PASS", "WAIT"]),
                        cloud_publish={"status": "SUCCESS"})
        for missing, override in (("no decision evaluated", {"real_recommendation_orchestrator": ORCH([101], ["DATA_UNAVAILABLE"])}),
                                  ("no odds rows stored", {"real_odds_bridge": {"rows_written": 0}}),
                                  ("cloud publish", {"cloud_publish": {"status": "FAILED"}}),
                                  ("cloud publish", {"cloud_publish": None})):
            self.audit.unlink(missing_ok=True)
            self.audited(base, **{**complete, **override})
            obs = mp.live_observed(mp.load_audit(self.audit))
            self.assertFalse(obs["live_observed"], missing)
            self.assertTrue(any(missing in g for g in obs["incomplete_real_clusters"][-1]["gaps"]), (missing, obs))
        self.audit.unlink(missing_ok=True)
        self.audited(base, **complete)                                          # PASS/WAIT only, no BET
        obs = mp.live_observed(mp.load_audit(self.audit))
        self.assertEqual((obs["status"], obs["live_observed"]), (mp.LIVE_OBSERVED, True))

    def test_a_quote_outside_the_window_does_not_certify(self):
        r = self.fire(D(22, 25), pull=lambda: {"ran": True, "captured_at_utc": "2026-09-26T22:33:00Z", "credits_spent_this_run": 1})
        self.audited(r, real_odds_bridge={"rows_written": 40}, real_recommendation_orchestrator=ORCH([101], ["PASS"]),
                     cloud_publish={"status": "SUCCESS"})
        self.assertFalse(mp.live_observed(mp.load_audit(self.audit))["live_observed"])


# --------------------------------------------------------------------------------------- certification command
class TestFirstLiveCertification(PregameCase):
    def test_reports_waiting_when_no_real_cluster_exists_and_spends_nothing(self):
        rep = flc.certify({"records": {}})
        self.assertEqual((rep["architecture_ready"], rep["live_observed"], rep["status"]), (True, False, mp.ARCHITECTURE_READY_ONLY))
        self.assertEqual(rep["checks"]["real_cluster_seen"]["state"], flc.PENDING)

    def test_command_is_read_only_and_has_no_path_to_the_paid_api(self):
        src = (REPO / "operational" / "first_live_certification.py").read_text()
        for banned in ("live_sog_pricing", "requests", "urllib", "get_sport_odds", "run_moneyline_snapshot", "INSERT", "UPDATE ", "DELETE"):
            self.assertNotIn(banned, src)
        self.assertIn("mode=ro", src)

    def _ledger_and_bankroll(self, persisted, bets):
        from operational import paper_bankroll, prospective_ledger as pl
        pl_path, bk_path = self.tmp / "pl.db", self.tmp / "bk.db"
        pl_conn, bk_conn = pl.init_db(pl_path), paper_bankroll.init_db(bk_path)
        pl_conn.close()
        bk_conn.close()
        return pl_path, bk_path

    def test_full_pass_for_a_completed_cluster_without_a_bet(self):
        r = self.fire(D(22, 25))
        self.audited(r, real_odds_bridge={"rows_written": 40}, real_recommendation_orchestrator=ORCH([101], ["PASS", "WAIT"]),
                     cloud_publish={"status": "SUCCESS"})
        audit = mp.load_audit(self.audit)
        pl_path, bk_path = self._ledger_and_bankroll(0, 0)
        with mock.patch.object(flc, "_persisted", return_value=2):
            rep = flc.certify(audit, ledger_path=pl_path, bankroll_path=bk_path)
        self.assertTrue(rep["live_observed"])
        states = {k: v["state"] for k, v in rep["checks"].items()}
        self.assertEqual(states["quote_timing_valid"], flc.PASS)
        self.assertEqual(states["observation_persisted"], flc.PASS)
        self.assertEqual(states["paper_bet_if_bet"], flc.NA)
        self.assertEqual(states["cloud_published"], flc.PASS)

    def test_a_bet_requires_a_matching_paper_bet(self):
        r = self.fire(D(22, 25))
        self.audited(r, real_odds_bridge={"rows_written": 40}, real_recommendation_orchestrator=ORCH([101], ["BET"]),
                     cloud_publish={"status": "SUCCESS"})
        audit = mp.load_audit(self.audit)
        with mock.patch.object(flc, "_persisted", return_value=2), mock.patch.object(flc, "_paper_bets", return_value=0):
            self.assertEqual(flc.certify(audit)["checks"]["paper_bet_if_bet"]["state"], flc.FAIL)
        with mock.patch.object(flc, "_persisted", return_value=2), mock.patch.object(flc, "_paper_bets", return_value=2):
            self.assertEqual(flc.certify(audit)["checks"]["paper_bet_if_bet"]["state"], flc.PASS)

    def test_missing_persistence_and_publish_are_reported_as_failures(self):
        r = self.fire(D(22, 25))
        self.audited(r, real_odds_bridge={"rows_written": 40}, real_recommendation_orchestrator=ORCH([101], ["PASS"]),
                     cloud_publish={"status": "FAILED"})
        with mock.patch.object(flc, "_persisted", return_value=0):
            rep = flc.certify(mp.load_audit(self.audit))
        self.assertEqual(rep["checks"]["observation_persisted"]["state"], flc.FAIL)
        self.assertEqual(rep["checks"]["cloud_published"]["state"], flc.FAIL)
        self.assertFalse(rep["live_observed"])


if __name__ == "__main__":
    unittest.main()
