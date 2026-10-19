"""Daily-job recovery after a wake with no network (2026-10-19): the morning chain (sync, settlement, postmortem, backup) retries with bounded backoff, never burns attempts while the machine is offline,
keeps completed work, never settles or pays twice, and reports deferred / failed / recovered / completed honestly. Isolated: fake runner, fake network, temp state, in-memory ledger; nothing here touches
production state, a real job or the network."""
from __future__ import annotations

import datetime as dt
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from operational import ingestion_health
from operational import morning_catchup as mc
from operational import paper_bankroll as pb
from operational import prospective_ledger as pl
from operational import settle_daily_observations as sdo
from operational import watchdog

COMP = {"sync_daily.py": "nhl_sync_full", "operational.settle_daily_observations": "settlement", "operational.daily_postmortem": "postmortem", "operational.backup_databases": "database_backups"}


def local(h, m=0, day=19):
    return dt.datetime(2026, 10, day, h, m).astimezone()


class Sim(unittest.TestCase):
    """A machine: health records, a network switch, per-component outcomes, and the catch-up's own state file."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.state, self.lock = self.tmp / "s.json", self.tmp / "l"
        self.health: dict = {}
        self.ran: list[str] = []
        self.network = True
        self.net_polls = 0
        self.outcome = {c: True for c in COMP.values()}            # component -> does its job succeed when run
        self.clock = local(7, 36)

    # what each real job does to ingestion_health, as the real ones do
    def runner(self, cmd, **kw):
        comp = COMP[cmd[-1]]
        self.ran.append(comp)
        ok = self.outcome[comp] and (self.network or comp != "nhl_sync_full")
        if ok:
            self.health[comp] = {"last_status": "SUCCESS", "last_success_utc": self.clock.astimezone(dt.timezone.utc).isoformat()}
        else:
            self.health[comp] = {**self.health.get(comp, {}), "last_status": "FAILED"}
            mc.note_event(comp, "FAILED", "ConnectionError", now=self.clock, state_path=self.state)       # what ingestion_health.record_run does for a failed run
        return mock.Mock(returncode=0 if ok else 1)

    def net(self):
        self.net_polls += 1
        return self.network

    def cycle(self, at, **kw):
        self.clock = at
        kw.setdefault("runner", self.runner)
        kw.setdefault("network_fn", self.net)
        kw.setdefault("sleep_fn", lambda s: None)
        kw.setdefault("clock", lambda: self.clock)                 # simulated time everywhere, never the real clock
        kw.setdefault("settle_work_fn", lambda: None)
        return mc.run_catchup(at, health_fn=lambda: self.health, state_path=self.state, lock_path=self.lock, active_fn=lambda: True, **kw)

    def states(self, at):
        return {r["component"]: r["state"] for r in mc.chain_status(at, self.health, self.state)}


class WakeWithoutNetwork(Sim):
    def test_offline_wake_defers_the_sync_and_what_depends_on_it_without_using_an_attempt(self):
        self.network = False
        r = self.cycle(local(8, 20))                                                          # every stage's slot and grace have passed
        by = {a["component"]: a["action"] for a in r["actions"]}
        self.assertEqual(by["nhl_sync_full"], "DEFERRED_NETWORK_DOWN")
        self.assertEqual(by["settlement"], "SKIPPED_UPSTREAM_NOT_RECOVERED")
        self.assertEqual(by["postmortem"], "SKIPPED_UPSTREAM_NOT_RECOVERED")
        self.assertEqual(by["database_backups"], "RAN")                                       # local work does not wait for the network
        self.assertNotIn("nhl_sync_full", self.ran)
        day = mc._load_state(self.state)["days"][local(8, 20).date().isoformat()]
        self.assertEqual(day["nhl_sync_full"]["attempts"], 0)                                 # the outage did not burn a retry
        self.assertEqual(self.states(local(8, 21))["nhl_sync_full"], "DEFERRED")
        self.assertEqual(self.states(local(8, 21))["settlement"], "DEFERRED")

    def test_a_long_outage_never_exhausts_the_retries(self):
        self.network = False
        for minutes in range(0, 600, 30):                                                     # ten hours of offline cycles
            self.cycle(local(7, 36) + dt.timedelta(minutes=minutes))
        day = mc._load_state(self.state)["days"][local(7, 36).date().isoformat()]
        self.assertNotIn("nhl_sync_full", self.ran)
        self.assertEqual(day["nhl_sync_full"]["attempts"], 0)
        self.network = True                                                                   # the network comes back at 17:36
        r = self.cycle(local(17, 36))
        self.assertEqual([a["component"] for a in r["actions"] if a["action"] == "RAN"][:3], ["nhl_sync_full", "settlement", "postmortem"])
        self.assertEqual(set(self.states(local(17, 40)).values()), {"RECOVERED"})

    def test_the_network_that_arrives_a_few_seconds_after_the_wake_is_waited_for(self):
        answers = iter([False, False, True])
        r = self.cycle(local(7, 36), network_fn=lambda: next(answers, True))
        self.assertEqual(r["actions"][0]["action"], "RAN")                                    # no deferral, no wasted cycle
        self.assertEqual(self.states(local(7, 40))["nhl_sync_full"], "RECOVERED")

    def test_wait_for_network_is_bounded(self):
        slept = []
        ok = mc._wait_for_network(lambda: False, slept.append)
        self.assertFalse(ok)
        self.assertLessEqual(sum(slept), mc.NETWORK_WAIT_S)


class RetryAndRecovery(Sim):
    def test_failure_defers_dependents_then_a_later_cycle_recovers_the_chain_in_order(self):
        self.outcome["nhl_sync_full"] = False
        r = self.cycle(local(8, 30))
        self.assertEqual(self.states(local(8, 31))["nhl_sync_full"], "FAILED")
        self.assertEqual(self.states(local(8, 31))["settlement"], "DEFERRED")
        too_soon = self.cycle(local(8, 40))
        self.assertIn("SKIPPED_RETRY_TOO_SOON", [a["action"] for a in too_soon["actions"]])
        self.assertEqual(self.ran.count("nhl_sync_full"), 1)                                  # backoff: not hammered
        self.outcome["nhl_sync_full"] = True
        self.ran.clear()
        self.cycle(local(8, 50))                                                              # 20 minutes after the failure: past the 15-minute backoff
        self.assertEqual(self.ran[:3], ["nhl_sync_full", "settlement", "postmortem"])
        self.assertEqual(self.states(local(8, 55)), {"nhl_sync_full": "RECOVERED", "settlement": "RECOVERED", "postmortem": "RECOVERED", "database_backups": "RECOVERED"})

    def test_completed_work_is_preserved_a_retry_runs_only_what_is_missing(self):
        self.outcome["settlement"] = False
        self.cycle(local(8, 30))
        self.assertEqual(self.ran.count("nhl_sync_full"), 1)
        self.outcome["settlement"] = True
        self.ran.clear()
        self.cycle(local(9, 0))
        self.assertNotIn("nhl_sync_full", self.ran)                                           # the successful sync is not repeated
        self.assertIn("settlement", self.ran)
        self.assertEqual(self.ran.count("settlement"), 1)

    def test_when_retries_are_used_up_the_stage_is_FAILED_and_exhausted_and_the_next_day_starts_fresh(self):
        self.outcome["nhl_sync_full"] = False
        t = local(8, 30)
        for gap in (0, 15, 30, 60, 120, 120, 120, 120):
            t = t + dt.timedelta(minutes=gap)
            self.cycle(t)
        self.assertEqual(self.ran.count("nhl_sync_full"), mc.DAILY_ATTEMPT_CAP)
        self.cycle(t + dt.timedelta(hours=3))
        self.assertEqual(self.ran.count("nhl_sync_full"), mc.DAILY_ATTEMPT_CAP)
        row = [r for r in mc.chain_status(t + dt.timedelta(hours=3), self.health, self.state) if r["component"] == "nhl_sync_full"][0]
        self.assertEqual((row["state"], row["exhausted"]), ("FAILED", True))
        self.outcome["nhl_sync_full"] = True                                                  # tomorrow: a fresh budget, and the chain catches up
        self.cycle(local(8, 30, day=20))
        self.assertEqual(self.ran.count("nhl_sync_full"), mc.DAILY_ATTEMPT_CAP + 1)

    def test_a_recovered_chain_is_never_run_again(self):
        self.cycle(local(8, 30))
        n = len(self.ran)
        again = self.cycle(local(9, 30))
        self.assertEqual((again["reason"], len(self.ran)), ("NOTHING_DUE", n))


class StatesAreDistinguished(Sim):
    def test_pending_before_the_slot_completed_when_clean_recovered_after_a_failure_deferred_when_missed(self):
        self.assertEqual(set(self.states(local(6, 0)).values()), {"PENDING"})
        self.health = {"nhl_sync_full": {"last_status": "SUCCESS", "last_success_utc": local(7, 1).astimezone(dt.timezone.utc).isoformat()}}
        self.assertEqual(self.states(local(7, 10))["nhl_sync_full"], "COMPLETED")             # ran on its own slot, no failure recorded
        mc.note_event("nhl_sync_full", "FAILED", "ConnectionError", now=local(7, 0, ) + dt.timedelta(seconds=30), state_path=self.state)
        self.assertEqual(self.states(local(7, 10))["nhl_sync_full"], "RECOVERED")             # the same success after a recorded failure
        self.assertEqual(self.states(local(8, 0))["settlement"], "DEFERRED")                  # slot long gone, nothing recorded, waiting for the catch-up

    @unittest.skipUnless(__import__("operational.state_paths", fromlist=["x"]).under_test(), "writes health state: only inside the isolated test run")
    def test_the_scheduled_run_failing_is_recorded_by_ingestion_health_for_the_chain_only(self):
        with mock.patch.object(mc, "STATE_PATH", self.state), mock.patch.object(ingestion_health, "DEFAULT_CACHE_PATH", self.tmp / "health.json"):
            ingestion_health.record_run("nhl_sync_full", {"status": "FAILED", "error": "NameResolutionError"})
            ingestion_health.record_run("settlement", {"status": "DEFERRED", "reason": "upstream not ready"})
            ingestion_health.record_run("moneyline_snapshot", {"status": "FAILED", "error": "x"})      # not part of the chain
            day = mc._load_state(self.state)["days"][dt.datetime.now().astimezone().date().isoformat()]
        self.assertEqual(set(day), {"nhl_sync_full", "settlement"})
        self.assertEqual(day["settlement"]["events"][0]["status"], "DEFERRED")
        self.assertEqual(day["nhl_sync_full"]["events"][0]["detail"], "NameResolutionError")


class WatchdogReportsTheStates(Sim):
    def check(self, at):
        with mock.patch.object(mc, "STATE_PATH", self.state), mock.patch.object(ingestion_health, "load_health", return_value=self.health), \
             mock.patch("operational.deployment_mode.is_active_scheduler", return_value=True):
            return watchdog.check_daily_chain(at)

    def test_ok_warn_fail(self):
        self.health = {k: {"last_status": "SUCCESS", "last_success_utc": local(7, mm).astimezone(dt.timezone.utc).isoformat()} for k, mm in
                       (("nhl_sync_full", 5), ("settlement", 20), ("postmortem", 35), ("database_backups", 50))}
        self.assertEqual(self.check(local(8, 0))["status"], "OK")
        self.health = {}
        self.network = False
        self.cycle(local(7, 36))                                                              # deferred by the network, retrying: a warning
        warn = self.check(local(7, 40))
        self.assertEqual(warn["status"], "WARN")
        self.assertIn("sync DEFERRED", warn["detail"])
        self.assertEqual(self.check(local(10, 5))["status"], "FAIL")                          # still unrecovered three hours after the slot


class NothingToSettleDoesNotWait(unittest.TestCase):
    def _main(self, ledger):
        recorded = []
        from operational import settlement_trigger_hook as hook
        with mock.patch.object(hook, "LOCK_PATH", Path(tempfile.mkdtemp()) / "settlement_trigger.lock"), \
             mock.patch("operational.deployment_mode.require_active_scheduler_or_exit", return_value=True), \
             mock.patch.object(sdo.pl, "init_db", return_value=ledger), \
             mock.patch.object(sdo.ingestion_health, "dependency_ready", return_value=(False, "nhl_sync_full's last recorded run did not succeed")), \
             mock.patch.object(sdo.ingestion_health, "record_run", side_effect=lambda c, s: recorded.append((c, s))):
            sdo.main()
        return recorded

    def test_a_failed_sync_does_not_leave_settlement_waiting_when_nothing_is_pending(self):
        ledger = pl.init_db(db_path=":memory:")
        self.assertEqual(self._main(ledger), [("settlement", {"status": "SUCCESS", "reason": "NOTHING_TO_SETTLE"})])

    def test_with_work_waiting_it_still_defers_and_settles_nothing(self):
        import tests.test_settle_daily_observations as t
        ledger = pl.init_db(db_path=":memory:")
        t._record_model_observation(ledger, "pred-1")
        rec = self._main(ledger)
        self.assertEqual(rec[0][0], "settlement")
        self.assertEqual(rec[0][1]["status"], "DEFERRED")
        self.assertEqual(pl.get_observation(ledger, "pred-1")["result_status"], "PENDING")


class NoDuplicateSettlementOrPayout(unittest.TestCase):
    def test_a_replayed_catchup_settlement_never_selects_a_settled_row_again(self):
        import tests.test_settle_daily_observations as t
        ledger = pl.init_db(db_path=":memory:")
        official = t._fresh_official_db()
        t._insert_game(official, 1)
        t._insert_player_stat(official, 1, "P1", shots=5)
        t._record_model_observation(ledger, "pred-1")
        runs = [sdo.run_settlement_batch(ledger, official) for _ in range(3)]                 # the scheduled run, the new-results trigger and a catch-up replay
        self.assertEqual([r["settled_win"] for r in runs], [1, 0, 0])
        self.assertEqual([r["total_candidates"] for r in runs], [1, 0, 0])
        self.assertEqual(pl.get_observation(ledger, "pred-1")["result_status"], "WIN")

    def test_a_paper_bet_pays_out_once_and_a_second_settlement_changes_nothing(self):
        conn = pb.init_db(db_path=":memory:")
        bet = pb.record_paper_bet(conn, track="REAL_MARKET_PAPER", price_source="LIVE_DRAFTKINGS", market_id="PLAYER_SOG", entry_odds=150, event_id="g1", game_date="2026-10-18",
                                  player_id="P1", player_name_snapshot="P One", team="MIN", opponent="CBJ", market_family="PLAYER_SOG", threshold="2+", side="OVER")
        pnl = pb.settle_paper_bet(conn, bet["paper_bet_id"], "WIN")["profit_loss"]
        with self.assertRaises(pb.InvalidPaperBetError):
            pb.settle_paper_bet(conn, bet["paper_bet_id"], "WIN")
        row = conn.execute("SELECT result_status, profit_loss FROM paper_bets WHERE paper_bet_id=?", (bet["paper_bet_id"],)).fetchone()
        self.assertEqual((row["result_status"], row["profit_loss"]), ("WIN", pnl))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM paper_bets").fetchone()[0], 1)

    def test_a_scheduled_or_catchup_settlement_cannot_run_while_another_settlement_holds_the_lock(self):
        import fcntl
        from operational import settlement_trigger_hook as hook
        ledger = pl.init_db(db_path=":memory:")
        with mock.patch.object(hook, "LOCK_PATH", self.lock_path), mock.patch("operational.deployment_mode.require_active_scheduler_or_exit", return_value=True), \
             mock.patch.object(sdo.pl, "init_db", return_value=ledger) as init, mock.patch.object(sdo.ingestion_health, "record_run") as rec:
            with open(self.lock_path, "w") as held:
                fcntl.flock(held, fcntl.LOCK_EX)                                          # the new-results trigger is settling right now
                sdo.main()
            init.assert_not_called()                                                      # nothing opened, nothing recorded, nothing settled twice
            rec.assert_not_called()

    def setUp(self):
        self.lock_path = Path(tempfile.mkdtemp()) / "settlement_trigger.lock"


class ReviewFixes(Sim):
    def test_events_noted_by_a_scheduled_job_during_the_network_wait_are_not_overwritten(self):
        self.network = False

        def sleep(s):
            if not getattr(self, "_noted", False):
                self._noted = True
                mc.note_event("settlement", "DEFERRED", "upstream not ready", now=self.clock, state_path=self.state)       # the 07:15 scheduled run lands during the wait
        self.cycle(local(8, 20), sleep_fn=sleep)
        day = mc._load_state(self.state)["days"][local(8, 20).date().isoformat()]
        self.assertEqual([e["detail"] for e in day["settlement"]["events"] if e["status"] == "DEFERRED"][:1], ["upstream not ready"])

    def test_the_catchup_and_note_event_do_not_share_a_temp_file(self):
        mc._save_state({"days": {}}, self.state, "2026-10-19")
        self.assertFalse(any(p.name == "s.json.tmp" for p in self.tmp.iterdir()))

    def test_a_failure_after_a_clean_success_is_not_hidden_and_does_not_make_it_recovered(self):
        self.health = {"settlement": {"last_status": "SUCCESS", "last_success_utc": local(7, 16).astimezone(dt.timezone.utc).isoformat()}}
        mc.note_event("settlement", "FAILED", "later trigger run", now=local(15, 0), state_path=self.state)
        row = [r for r in mc.chain_status(local(15, 5), self.health, self.state) if r["component"] == "settlement"][0]
        self.assertEqual(row["state"], "COMPLETED")
        self.assertIn("a later run failed", row["detail"])

    def test_settlement_with_nothing_pending_completes_even_while_the_sync_is_unrecovered(self):
        self.outcome["nhl_sync_full"] = False
        self.cycle(local(8, 30), settle_work_fn=lambda: 0)
        self.assertIn("settlement", self.ran)                                               # not left waiting for tomorrow
        self.assertEqual(self.states(local(8, 31))["settlement"], "RECOVERED")
        self.assertEqual(self.states(local(8, 31))["nhl_sync_full"], "FAILED")

    def test_settlement_with_work_waiting_still_waits_for_the_sync(self):
        self.outcome["nhl_sync_full"] = False
        self.cycle(local(8, 30), settle_work_fn=lambda: 3)
        self.assertNotIn("settlement", self.ran)

    def test_a_mac_that_was_off_all_night_is_a_warning_not_a_failure(self):
        with mock.patch.object(mc, "STATE_PATH", self.state), mock.patch.object(ingestion_health, "load_health", return_value={}),              mock.patch("operational.deployment_mode.is_active_scheduler", return_value=True):
            out = watchdog.check_daily_chain(local(10, 30))                                 # woke at 10:30, no catch-up cycle has run yet
        self.assertEqual(out["status"], "WARN")
        self.assertIn("no catch-up cycle has run yet", out["detail"])

    def test_a_standby_machine_is_not_judged_on_the_morning_chain(self):
        with mock.patch("operational.deployment_mode.is_active_scheduler", return_value=False):
            self.assertEqual(watchdog.check_daily_chain(local(10, 30))["status"], "OK")

    def test_the_default_network_probe_asks_again_each_poll(self):
        from operational import morning_refresh
        morning_refresh._NET_CACHE.update(at=1.0, up=False)
        with mock.patch.object(morning_refresh, "network_up", side_effect=lambda: morning_refresh._NET_CACHE["up"] is None):
            self.assertTrue(mc._fresh_network_probe())                                       # the stale "down" answer was cleared before asking


if __name__ == "__main__":
    unittest.main()
