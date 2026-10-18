"""
The 07:45 ET morning refresh: schedules, results, player statistics, rosters and the available participation evidence, BEFORE the 08:00 analysis and the first league-wide pull.

It runs from its own launchd job at 07:45 and, so that an early first game never waits for it, from the 15-minute trader as soon as `capture_timeline.prep_times` says it is due (earlier than
07:45 when the day's first game is early; at wake-up when the machine slept through 07:45). Once per Eastern day: the state file says whether it ran, what each step did and whether it succeeded.

Steps (each independent; one failing never stops the next, and every outcome is written down):
  statistics   sync_daily.run(): NHL schedule, results, box scores and current rosters, then the MoneyPuck current-season check (the free public sources; no Odds API credit)
  starters     the starting-goalie evidence reader (Daily Faceoff when the owner has enabled it, rate-limited inside the module; confirmations recorded by hand or by a recognised source stay as they are)
"""
from __future__ import annotations

import datetime as dt
import json
import sys

from operational import state_paths

STATE_NAME = "morning_refresh_state.json"


def read() -> dict | None:
    try:
        return json.loads(state_paths.path(STATE_NAME).read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _write(doc: dict) -> None:
    try:
        p = state_paths.path(STATE_NAME)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, indent=1, sort_keys=True, default=str))
        tmp.replace(p)
    except OSError:
        pass


def first_start_today(now: dt.datetime) -> dt.datetime | None:
    """The earliest scheduled start (UTC) of today's Eastern-day games, from the schedule database (read-only)."""
    import db
    from operational import eastern_time as et
    conn = db.get_conn()
    try:
        rows = conn.execute("SELECT scheduled_start_utc FROM games WHERE game_date = ?", (et.eastern_today(now),)).fetchall()
    finally:
        conn.close()
    starts = []
    for (s,) in [tuple(r) for r in rows]:
        if s:
            t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
            starts.append(t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc))
    return min(starts) if starts else None


RETRY_GAP_MIN = 60
MAX_ATTEMPTS = 4                # a failed step is retried at most this many times a day


def due(now: dt.datetime, first_start: dt.datetime | None) -> dict:
    from operational import capture_timeline as ct, eastern_time as et
    day = et.eastern_today(now)
    at = ct.prep_times(dt.date.fromisoformat(day), first_start)["refresh_at"]
    st = read() or {}
    failed_steps = [k for k, v in (st.get("steps") or {}).items() if str((v or {}).get("status")) in ("ERROR", "FAILED")] if st.get("day") == day else []
    if st.get("day") == day and st.get("status") == "DONE":
        return {"due": False, "reason": "DONE_TODAY", "due_at_utc": at.strftime("%Y-%m-%dT%H:%M:%SZ")}
    if st.get("day") == day and st.get("status") == "PARTIAL" and not (failed_steps and int(st.get("attempts") or 1) < MAX_ATTEMPTS):
        return {"due": False, "reason": "DONE_TODAY" if not failed_steps else "RETRIES_EXHAUSTED", "due_at_utc": at.strftime("%Y-%m-%dT%H:%M:%SZ")}   # a PARTIAL with a failed step is retried (2026-10-18: the network was not up when the Mac woke)
    if now < at:
        return {"due": False, "reason": "NOT_YET", "due_at_utc": at.strftime("%Y-%m-%dT%H:%M:%SZ")}
    tried = st.get("finished_utc") if st.get("day") == day else None
    if tried and (now - dt.datetime.fromisoformat(tried.replace("Z", "+00:00"))).total_seconds() < RETRY_GAP_MIN * 60:
        return {"due": False, "reason": "RETRY_GAP", "due_at_utc": at.strftime("%Y-%m-%dT%H:%M:%SZ")}    # a failed refresh is retried, not every cycle
    return {"due": True, "reason": "DUE", "due_at_utc": at.strftime("%Y-%m-%dT%H:%M:%SZ"), "late_min": round((now - at).total_seconds() / 60.0, 1)}


def run_if_due(now: dt.datetime | None = None, *, first_start: dt.datetime | None | str = "lookup", sync=None, starters=None, force: bool = False) -> dict:
    """Run the refresh when it is due (or `force`). `sync` and `starters` are injectable for tests."""
    from operational import eastern_time as et
    now = now or dt.datetime.now(dt.timezone.utc)
    if first_start == "lookup":
        try:
            first_start = first_start_today(now)
        except Exception:  # noqa: BLE001 - without a schedule the default 07:45 applies
            first_start = None
    import fcntl
    lock_path = state_paths.path("morning_refresh.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return {"status": "SKIPPED", "reason": "ANOTHER_REFRESH_IS_RUNNING"}     # the launchd job and the trader never run the refresh at the same time
        return _run_locked(now, first_start, sync, starters, force)


def _run_locked(now, first_start, sync, starters, force) -> dict:
    from operational import eastern_time as et
    d = due(now, first_start)
    if not d["due"] and not force:
        return {"status": "SKIPPED", **d}
    _t0 = dt.datetime.now(dt.timezone.utc)
    prev = read() or {}
    attempts = (int(prev.get("attempts") or 1) + 1) if prev.get("day") == et.eastern_today(now) else 1
    doc = {"attempts": attempts, "day": et.eastern_today(now), "started_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "due": d, "steps": {}}
    try:
        import sync_daily
        rc = (sync or sync_daily.run)()
        doc["steps"]["statistics"] = {"status": "OK" if rc == 0 else "FAILED", "exit_code": rc}
    except Exception as exc:  # noqa: BLE001
        doc["steps"]["statistics"] = {"status": "ERROR", "reason": f"{exc.__class__.__name__}: {exc}"}
    try:
        def _starters():
            import db
            from operational import dailyfaceoff
            conn = db.get_conn()
            try:
                st = dailyfaceoff.refresh(now)
                return {"status": st.get("status"), "last_error": st.get("last_error")}
            finally:
                conn.close()
        doc["steps"]["starters"] = (starters or _starters)()
    except Exception as exc:  # noqa: BLE001
        doc["steps"]["starters"] = {"status": "ERROR", "reason": f"{exc.__class__.__name__}: {exc}"}
    doc["finished_utc"] = (now + (dt.datetime.now(dt.timezone.utc) - _t0)).strftime("%Y-%m-%dT%H:%M:%SZ")      # `now` plus the real elapsed time (a test clock stays consistent)
    bad = [k for k, v in doc["steps"].items() if str(v.get("status")) in ("ERROR", "FAILED")]
    doc["status"] = "DONE" if not bad else ("PARTIAL" if len(bad) < len(doc["steps"]) else "FAILED")
    _write(doc)
    try:
        from operational import ingestion_health
        ingestion_health.record_run("morning_refresh", {"status": "SUCCESS" if doc["status"] != "FAILED" else "FAILED", "steps": {k: v.get("status") for k, v in doc["steps"].items()}})
    except Exception:  # noqa: BLE001
        pass
    return doc


def main() -> int:
    from operational import deployment_mode as dm
    if not dm.require_active_scheduler_or_exit("morning_refresh"):
        return 0
    out = run_if_due()
    print(json.dumps(out, indent=1, default=str))
    return 0 if out.get("status") in ("DONE", "PARTIAL", "SKIPPED") else 1


if __name__ == "__main__":
    sys.exit(main())
