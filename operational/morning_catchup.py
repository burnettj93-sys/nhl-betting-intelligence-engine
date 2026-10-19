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
would record them. Bounds: single-instance lock, bounded BACKOFF between attempts of one stage (15, 30, 60, then
every 120 minutes: the NHL API is never hammered) with a DAILY_ATTEMPT_CAP per stage per local day, a hard
subprocess timeout per stage, a stage never runs when its upstream stage has still not succeeded, STANDBY
machines do nothing, and it never raises. Every action records its reason (`CATCHUP_AFTER_MISSED_SLOT`). The
normal schedule is untouched.

Network and states (2026-10-19). A stage that needs the network (the NHL sync) is not attempted while the
machine is offline: it waits a bounded moment for the network (the Mac wakes before Wi-Fi is up), and if it is
still down the stage is DEFERRED with NETWORK_DOWN and NO attempt is consumed, so a temporary outage cannot
burn the day's retries. Every stage is always in exactly one of these states (chain_status):
    PENDING    its slot has not come yet, or the scheduled run is still inside the grace window
    DEFERRED   not attempted or not ready (network down, upstream stage not recovered, slot missed and waiting)
    FAILED     attempted and did not succeed; the next retry time is shown, or `exhausted` once the cap is used
    RECOVERED  succeeded, but only after a failure or deferral today (a catch-up run counts)
    COMPLETED  succeeded with no failure or deferral recorded today
Scheduled runs that fail or defer are recorded by ingestion_health.record_run (it calls note_event), so a
later success is told apart from a clean run. Completed work is never redone: a stage with a success since its
slot is not due, so a retry runs only what is still missing. After the cap a later day starts fresh.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from operational import state_paths as _sp

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = _sp.path("morning_catchup_state.json")
LOCK_PATH = _sp.path("morning_catchup.lock")

GRACE_MIN = 30
BACKOFF_MIN = (15, 30, 60, 120)       # wait after attempt n before attempt n+1 (the last value repeats)
DAILY_ATTEMPT_CAP = 8                  # per stage per local day; the next day starts fresh
NEEDS_NETWORK = frozenset({"nhl_sync_full"})
NETWORK_WAIT_S = 90                    # a woken Mac reaches the network a little after it wakes
NETWORK_POLL_S = 10
EVENT_KEEP = 20
KEEP_DAYS = 3
REASON = "CATCHUP_AFTER_MISSED_SLOT"
STATES = ("PENDING", "DEFERRED", "FAILED", "RECOVERED", "COMPLETED")

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
                python: str | None = None, network_fn: Callable[[], bool] | None = None,
                sleep_fn: Callable[[float], None] | None = None) -> dict:
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
            if network_fn is None:
                from operational import morning_refresh
                network_fn = morning_refresh.network_up
            return _run_locked(now, due, out, runner, health_fn, state_path, python or sys.executable, network_fn, sleep_fn or time.sleep)
    except Exception as exc:  # noqa: BLE001 -- a maintenance hook must never break its host job
        out.update(status="FAILED", reason=f"{type(exc).__name__}: {str(exc)[:160]}")
        return out


def _backoff_gap(attempts: int) -> dt.timedelta:
    return dt.timedelta(minutes=BACKOFF_MIN[min(max(attempts, 1), len(BACKOFF_MIN)) - 1])


def _add_event(rec: dict, now: dt.datetime, status: str, detail: str | None = None) -> None:
    ev = rec.setdefault("events", [])
    ev.append({"at": now.isoformat(), "status": status, **({"detail": str(detail)[:200]} if detail else {})})
    del ev[:-EVENT_KEEP]


def _wait_for_network(network_fn, sleep_fn, wait_s: float = NETWORK_WAIT_S, poll_s: float = NETWORK_POLL_S) -> bool:
    waited = 0.0
    while True:
        try:
            if network_fn():
                return True
        except Exception:  # noqa: BLE001 - a probe that fails is "not up"
            pass
        if waited >= wait_s:
            return False
        sleep_fn(poll_s)
        waited += poll_s


def _run_locked(now, due, out, runner, health_fn, state_path, python, network_fn=None, sleep_fn=time.sleep):
    today = now.astimezone().date().isoformat()
    state = _load_state(state_path)
    day = state["days"].setdefault(today, {})
    unrecovered: set[str] = set()
    for stage in due:
        comp = stage["component"]
        dep = stage["depends_on"]
        rec = day.setdefault(comp, {"attempts": 0})
        if dep and dep in unrecovered:
            rec.update(status="DEFERRED", reason="UPSTREAM_NOT_RECOVERED", slot_utc=stage["slot_utc"])
            out["actions"].append({"component": comp, "action": "SKIPPED_UPSTREAM_NOT_RECOVERED", "depends_on": dep})
            unrecovered.add(comp)
            _save_state(state, state_path, today)
            continue
        if rec["attempts"] >= DAILY_ATTEMPT_CAP:
            rec.update(status="FAILED", exhausted=True, slot_utc=stage["slot_utc"])
            out["actions"].append({"component": comp, "action": "SKIPPED_MAX_ATTEMPTS", "attempts": rec["attempts"]})
            unrecovered.add(comp)
            _save_state(state, state_path, today)
            continue
        last = _utc(rec.get("last_attempt_utc"))
        if last is not None and now - last < _backoff_gap(rec["attempts"]):
            nxt = (last + _backoff_gap(rec["attempts"])).isoformat()
            rec["next_retry_utc"] = nxt
            out["actions"].append({"component": comp, "action": "SKIPPED_RETRY_TOO_SOON", "last_attempt_utc": rec["last_attempt_utc"], "next_retry_utc": nxt})
            unrecovered.add(comp)
            _save_state(state, state_path, today)
            continue
        if comp in NEEDS_NETWORK and network_fn is not None and not _wait_for_network(network_fn, sleep_fn):
            rec.update(status="DEFERRED", reason="NETWORK_DOWN", slot_utc=stage["slot_utc"])
            _add_event(rec, now, "DEFERRED", "NETWORK_DOWN")
            out["actions"].append({"component": comp, "action": "DEFERRED_NETWORK_DOWN", "attempts": rec["attempts"]})      # no attempt consumed: the machine was offline, the source did not fail
            unrecovered.add(comp)
            _save_state(state, state_path, today)
            continue

        rec.update(attempts=rec["attempts"] + 1, last_attempt_utc=now.isoformat(), reason=REASON,
                   slot_utc=stage["slot_utc"], status="ATTEMPTING")
        rec.pop("next_retry_utc", None)
        _save_state(state, state_path, today)                      # recorded BEFORE running: a crash still counts
        action = {"component": comp, "action": "RAN", "reason": REASON, "attempt": rec["attempts"]}
        detail = None
        try:
            proc = runner([python, *stage["command"]], cwd=str(REPO_ROOT), capture_output=True, text=True,
                          timeout=stage["timeout"])
            action["returncode"] = proc.returncode
        except subprocess.TimeoutExpired:
            action.update(action="TIMEOUT", returncode=None)
            detail = "TIMEOUT"
        except OSError as exc:
            action.update(action="ERROR", error=type(exc).__name__)
            detail = type(exc).__name__
        recovered = succeeded_since(health_fn(), comp, _utc(stage["slot_utc"]))
        finished = dt.datetime.now(dt.timezone.utc)
        state = _load_state(state_path)                             # the stage's own process may have noted an event in the meantime: never overwrite it with a stale copy
        day = state["days"].setdefault(today, {})
        rec = day.setdefault(comp, {"attempts": 1})
        rec.update(status="RECOVERED" if recovered else "FAILED", finished_utc=finished.isoformat())
        if not recovered:
            gap = _backoff_gap(rec["attempts"])
            rec["next_retry_utc"] = (now + gap).isoformat() if rec["attempts"] < DAILY_ATTEMPT_CAP else None
            rec["exhausted"] = rec["attempts"] >= DAILY_ATTEMPT_CAP
        _add_event(rec, finished, "RECOVERED" if recovered else "FAILED", detail or (f"returncode {action.get('returncode')}" if not recovered else None))
        action["recovered"] = recovered
        if not recovered:
            unrecovered.add(comp)
        out["actions"].append(action)
        _save_state(state, state_path, today)
    out["status"] = "CATCHUP_RAN" if any(a["action"] == "RAN" for a in out["actions"]) else "OK"
    out["reason"] = REASON if out["status"] == "CATCHUP_RAN" else "NOTHING_RUN"
    return out


def note_event(component: str, status: str, detail: str | None = None, *, now: dt.datetime | None = None, state_path: Path | None = None) -> None:
    """A scheduled (launchd) run of a chain stage failed or deferred: remember it so a later success is RECOVERED rather than COMPLETED. Never raises; never counts as a catch-up attempt."""
    try:
        if component not in {c[0] for c in CHAIN}:
            return
        now = now or dt.datetime.now(dt.timezone.utc)
        today = now.astimezone().date().isoformat()
        state = _load_state(state_path)
        rec = state["days"].setdefault(today, {}).setdefault(component, {"attempts": 0})
        _add_event(rec, now, status, detail)
        if rec.get("status") not in ("RECOVERED",):
            rec["status"] = "DEFERRED" if status == "DEFERRED" else "FAILED"
        _save_state(state, state_path, today)
    except Exception:  # noqa: BLE001
        pass


def chain_status(now: dt.datetime | None = None, health: dict | None = None, state_path: Path | None = None) -> list[dict]:
    """The state of each stage of today's morning chain, for the watchdog, readiness and the day timeline (see STATES)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    if health is None:
        from operational import ingestion_health
        health = ingestion_health.load_health()
    local = now.astimezone()
    day = _load_state(state_path)["days"].get(local.date().isoformat(), {})
    out = []
    for component, hm, _cmd, _timeout, dep in CHAIN:
        slot = slot_utc(local, hm)
        rec = day.get(component) or {}
        events = [e for e in rec.get("events", []) if _utc(e.get("at")) and _utc(e["at"]) >= slot]
        bad_events = [e for e in events if e.get("status") in ("FAILED", "DEFERRED")]
        row = {"component": component, "slot_utc": slot.isoformat(), "attempts": rec.get("attempts", 0), "exhausted": bool(rec.get("exhausted")), "next_retry_utc": rec.get("next_retry_utc")}
        if succeeded_since(health, component, slot):
            row.update(state="RECOVERED" if (bad_events or rec.get("reason") == REASON) else "COMPLETED", detail=None)
        elif now < slot + dt.timedelta(minutes=GRACE_MIN) and not bad_events:
            row.update(state="PENDING", detail="its scheduled run is not due yet" if now < slot else "inside the grace window for its scheduled run")
        else:
            last = bad_events[-1] if bad_events else None
            failed = (rec.get("status") == "FAILED") or (last is not None and last.get("status") == "FAILED")
            row.update(state="FAILED" if failed else "DEFERRED",
                       detail=(last or {}).get("detail") or rec.get("reason") or "slot missed; waiting for the catch-up")
        out.append(row)
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
            "max_attempts_per_stage_per_day": DAILY_ATTEMPT_CAP, "backoff_minutes": list(BACKOFF_MIN), "grace_minutes": GRACE_MIN,
            "chain": {r["component"]: r["state"] for r in chain_status(now, health, state_path)}}
