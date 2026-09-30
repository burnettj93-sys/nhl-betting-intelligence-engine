"""
Settlement retrigger hook (Production Gap Closure sprint, 2026-09-30).

Problem this closes: operational.settle_daily_observations previously ran
on ONE fixed clock slot a day (07:15, right after the 07:00 full sync).
Games can go FINAL any time a same-day ingestion job runs -- the midday
schedule refresh, the pregame targeted refresh, even the next morning's
full sync itself -- and none of those retriggered settlement. A game that
went FINAL at, say, 13:26 UTC sat PENDING until 07:15 the NEXT day even
though the settlement job's own health check reported SUCCESS the whole
time (it had nothing new to do at ITS clock slot, which is a legitimate
SUCCESS, not a lie -- the gap was in scheduling, not in settlement's own
logic).

Called at the END of any ingestion job that can finalize a game (see
operational.settle_daily_observations.GENERATION_COMPONENTS) -- it is NOT
a scheduled job of its own, mirrors operational/cloud_publish_hook.py's
guarantees exactly:
  * Bounded: a non-blocking file lock means an already-running settlement
    (the scheduled 07:15 job, or another trigger firing close behind this
    one) is never raced -- this call just reports SKIPPED and returns.
  * Idempotent: delegates to settle_daily_observations.run_if_new_generation(),
    which is itself a fast no-op when nothing new has landed since the
    last successful settlement run.
  * Never raises and never changes the calling ingestion job's own outcome.
  * Respects STANDBY (a STANDBY machine never settles or publishes).
  * On an actual settlement change, publishes the cloud snapshot downstream
    (the same opt-in cloud_publish_hook the scheduled job already uses) --
    cloud viewers should not have to wait for an unrelated job to publish
    results this trigger just settled.
"""
from __future__ import annotations

import fcntl
from pathlib import Path

from operational import state_paths as _sp

LOCK_PATH = _sp.path("settlement_trigger.lock", area="operational")


def trigger_after(component: str) -> dict:
    result = {"triggered_by": component, "status": "SKIPPED", "reason": None}
    try:
        from operational import deployment_mode
        if _sp.under_test():
            # a test run must never launch real settlement / cloud publish --
            # tests exercise settle_daily_observations.run_if_new_generation()
            # and this hook's locking/dispatch logic directly instead.
            result["reason"] = "UNDER_TEST"
            return result
        if not deployment_mode.is_active_scheduler():
            result["reason"] = "STANDBY"
            return result

        LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOCK_PATH, "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                result["reason"] = "ANOTHER_SETTLEMENT_RUNNING"
                return result

            from operational import settle_daily_observations as sdo
            settle_result = sdo.run_if_new_generation()
            result.update(status=settle_result["status"], reason=settle_result.get("reason"),
                          settled_win=settle_result.get("settled_win"),
                          settled_loss=settle_result.get("settled_loss"),
                          settled_void=settle_result.get("settled_void"),
                          settled_unresolved=settle_result.get("settled_unresolved"))

            if settle_result["status"] == "SUCCESS" and settle_result.get("total_candidates", 0) > 0:
                from operational import cloud_publish_hook
                result["cloud_publish"] = cloud_publish_hook.publish_after("settlement")
    except Exception as exc:  # noqa: BLE001 -- a retrigger hook must never break its host ingestion job
        result.update(status="FAILED", reason=f"{type(exc).__name__}: {str(exc)[:200]}")
    return result
