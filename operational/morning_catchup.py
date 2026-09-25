"""
Bounded catch-up for the MORNING DEPENDENCY CHAIN after a missed launchd slot (Live Run Reliability block,
2026-09-25).

Problem. launchd does not replay a `StartCalendarInterval` slot that passed while the Mac was OFF (the
2026-09-25 07:00 sync fell 8 minutes before the 07:08 boot). One missed 07:00 sync silently stalls the whole
chain for a day: settlement (07:15) DEFERs on a stale sync, the post-mortem waits on settlement, ...

Design. This is NOT a new job. `operational.nhl_sync --mode=pregame` (the existing every-30-minutes NHL job)
calls `run_catchup()` first. In the normal case it is a couple of dictionary lookups and does nothing. It acts
only when a chain stage's scheduled slot (+ a 30-minute grace, so the normal launchd run is never raced) has
passed today with NO recorded success since that slot:

    07:00 nhl_sync_full  ->  07:15 settlement  ->  07:30 postmortem       (each depends on the previous)
    07:45 database_backups                                                (independent)

Stages are run in that order, once, via the SAME entry points launchd uses (`sync_daily.py`,
`python -m operational.settle_daily_observations`, ...), so results are recorded exactly as a scheduled run
would record them. Bounds: single-instance lock, at most MAX_ATTEMPTS per stage per local day with at least
MIN_RETRY_GAP_MIN between attempts (the NHL API is never hammered), a hard subprocess timeout per stage, a
stage never runs when its upstream stage has still not succeeded, STANDBY machines do nothing, and it never
raises. Every action records its reason (`CATCHUP_AFTER_MISSED_SLOT`). The normal schedule is untouched.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import subprocess
import sys
from pathlib import Path
from typing import Callable

from operational import state_paths as _sp

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = _sp.path("morning_catchup_state.json")
LOCK_PATH = _sp.path("morning_catchup.lock")

GRACE_MIN = 30
MAX_ATTEMPTS = 2
MIN_RETRY_GAP_MIN = 30
KEEP_DAYS = 3
REASON = "CATCHUP_AFTER_MISSED_SLOT"

# (health component, local slot hour, minute, command, timeout seconds, depends on)
CHAIN = (
    ("nhl_sync_full", (7, 0), ["sync_daily.py"], 900, None),
    ("settlement", (7, 15), ["-m", "operational.settle_daily_observations"], 300, "nhl_sync_full"),
    ("postmortem", (7, 30), ["-m", "operational.daily_postmortem"], 300, "settlement"),
    ("database_backups", (7, 45), ["-m", "operational.backup_databases"], 600, None),
)


def _utc(value) -> dt.datetime | None:
    from operational import cloud_snapshot_schema as schema
    return schema.parse_utc(value)


def slot_utc(local_now: dt.datetime, hm: tuple[int, int]) -> dt.datetime:
    """Today's (local-date) slot as an aware UTC datetime."""
    return local_now.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0).astimezone(dt.timezone.utc)


def succeeded_since(health: dict, component: str, since_utc: dt.datetime) -> bool:
    row = health.get(component) or {}
    last = _utc(row.get("last_success_utc"))
    return last is not None and last >= since_utc


def stages_needing_catchup(now: dt.datetime, health: dict) -> list[dict]:
    """Pure: which stages' slots (+ grace) have passed today without a success since the slot."""
    local = now.astimezone()
    due = []
    for component, hm, cmd, timeout, dep in CHAIN:
        slot = slot_utc(local, hm)
        if now < slot + dt.timedelta(minutes=GRACE_MIN):
            continue
        if succeeded_since(health, component, slot):
            continue
        due.append({"component": component, "slot_utc": slot.isoformat(), "command": cmd, "timeout": timeout, "depends_on": dep})
    return due


def _load_state(path: Path | None = None) -> dict:
    try:
        st = json.loads((path or STATE_PATH).read_text())
        return st if isinstance(st.get("days"), dict) else {"days": {}}
    except (OSError, ValueError, AttributeError):
        return {"days": {}}


def _save_state(state: dict, path: Path | None, today: str) -> None:
    path = path or STATE_PATH
    keep = sorted(state["days"])[-KEEP_DAYS:]
    state["days"] = {d: state["days"][d] for d in keep}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.replace(path)


def run_catchup(now: dt.datetime | None = None, *, runner: Callable | None = None, health: dict | None = None,
                health_fn: Callable[[], dict] | None = None, state_path: Path | None = None,
                lock_path: Path | None = None, active_fn: Callable[[], bool] | None = None,
                python: str | None = None) -> dict:
    """One bounded pass. Returns {"status", "actions": [...], "reason"}; never raises."""
    now = now or dt.datetime.now(dt.timezone.utc)
    out = {"status": "OK", "reason": None, "actions": []}
    try:
        if runner is None:
            if _sp.under_test():
                # a test run must never launch the real sync / settlement / post-mortem / backup jobs
                out.update(status="SKIPPED", reason="UNDER_TEST")
                return out
            runner = subprocess.run
        if active_fn is None:
            from operational import deployment_mode as dm
            active_fn = dm.is_active_scheduler
        if not active_fn():
            out.update(status="SKIPPED", reason="STANDBY")
            return out
        if health_fn is None:
            from operational import ingestion_health
            health_fn = ingestion_health.load_health
        health = health_fn() if health is None else health
        due = stages_needing_catchup(now, health)
        if not due:
            out["reason"] = "NOTHING_DUE"
            return out

        lock_path = lock_path or LOCK_PATH
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                out.update(status="SKIPPED", reason="ANOTHER_INSTANCE_RUNNING")
                return out
            return _run_locked(now, due, out, runner, health_fn, state_path, python or sys.executable)
    except Exception as exc:  # noqa: BLE001 -- a maintenance hook must never break its host job
        out.update(status="FAILED", reason=f"{type(exc).__name__}: {str(exc)[:160]}")
        return out


def _run_locked(now, due, out, runner, health_fn, state_path, python):
    today = now.astimezone().date().isoformat()
    state = _load_state(state_path)
    day = state["days"].setdefault(today, {})
    failed_upstream: set[str] = set()
    for stage in due:
        comp = stage["component"]
        dep = stage["depends_on"]
        rec = day.setdefault(comp, {"attempts": 0})
        if dep and dep in failed_upstream:
            out["actions"].append({"component": comp, "action": "SKIPPED_UPSTREAM_NOT_RECOVERED", "depends_on": dep})
            failed_upstream.add(comp)
            continue
        if rec["attempts"] >= MAX_ATTEMPTS:
            out["actions"].append({"component": comp, "action": "SKIPPED_MAX_ATTEMPTS", "attempts": rec["attempts"]})
            failed_upstream.add(comp)
            continue
        last = _utc(rec.get("last_attempt_utc"))
        if last is not None and now - last < dt.timedelta(minutes=MIN_RETRY_GAP_MIN):
            out["actions"].append({"component": comp, "action": "SKIPPED_RETRY_TOO_SOON", "last_attempt_utc": rec["last_attempt_utc"]})
            failed_upstream.add(comp)
            continue

        rec.update(attempts=rec["attempts"] + 1, last_attempt_utc=now.isoformat(), reason=REASON,
                   slot_utc=stage["slot_utc"], status="ATTEMPTING")
        _save_state(state, state_path, today)                      # recorded BEFORE running: a crash still counts
        action = {"component": comp, "action": "RAN", "reason": REASON, "attempt": rec["attempts"]}
        try:
            proc = runner([python, *stage["command"]], cwd=str(REPO_ROOT), capture_output=True, text=True,
                          timeout=stage["timeout"])
            action["returncode"] = proc.returncode
        except subprocess.TimeoutExpired:
            action.update(action="TIMEOUT", returncode=None)
        except OSError as exc:
            action.update(action="ERROR", error=type(exc).__name__)
        recovered = succeeded_since(health_fn(), comp, _utc(stage["slot_utc"]))
        rec.update(status="RECOVERED" if recovered else "NOT_RECOVERED", finished_utc=dt.datetime.now(dt.timezone.utc).isoformat())
        action["recovered"] = recovered
        if not recovered:
            failed_upstream.add(comp)
        out["actions"].append(action)
        _save_state(state, state_path, today)
    out["status"] = "CATCHUP_RAN" if any(a["action"] == "RAN" for a in out["actions"]) else "OK"
    out["reason"] = REASON if out["status"] == "CATCHUP_RAN" else "NOTHING_RUN"
    return out


def status(now: dt.datetime | None = None, health: dict | None = None, state_path: Path | None = None) -> dict:
    """Read-only summary for readiness: today's chain state and whether a catch-up ever ran."""
    now = now or dt.datetime.now(dt.timezone.utc)
    if health is None:
        from operational import ingestion_health
        health = ingestion_health.load_health()
    pending = [s["component"] for s in stages_needing_catchup(now, health)]
    day = _load_state(state_path)["days"].get(now.astimezone().date().isoformat(), {})
    return {"stages_pending_catchup": pending,
            "catchups_today": {k: v.get("status") for k, v in day.items()},
            "max_attempts_per_stage_per_day": MAX_ATTEMPTS, "grace_minutes": GRACE_MIN}
