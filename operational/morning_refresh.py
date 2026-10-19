"""
The 07:45 ET morning refresh: schedules, results, player statistics, rosters and the available participation evidence, BEFORE the 08:00 analysis and the first league-wide pull.

It runs from its own launchd job at 07:45 and, so that an early first game never waits for it, from the 10-minute trader as soon as `capture_timeline.prep_times` says it is due (earlier than
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


_NET_CACHE: dict = {"at": None, "up": None}


def network_up(host: str = "api-web.nhle.com", port: int = 443, timeout: float = 4.0) -> bool:
    """Can this machine reach the NHL API host right now (DNS + TCP)? Tells 'the network was not back yet' from 'the source failed'. The whole probe (including name resolution, which a socket
    timeout does not bound) is cut off after `timeout` + 2 s, and an answer is reused for 60 s so a slow resolver cannot stall every cycle."""
    import socket
    import threading
    import time
    if state_paths.under_test():
        return True                                  # a unit test never probes the network (tests that need an offline machine inject `connectivity`)
    now = time.monotonic()
    if _NET_CACHE["at"] is not None and now - _NET_CACHE["at"] < 60 and _NET_CACHE["up"] is not None:
        return bool(_NET_CACHE["up"])
    result = {"up": False}

    def probe():
        try:
            socket.create_connection((host, port), timeout=timeout).close()
            result["up"] = True
        except OSError:
            result["up"] = False

    t = threading.Thread(target=probe, daemon=True)
    t.start()
    t.join(timeout + 2.0)
    _NET_CACHE.update(at=now, up=result["up"])
    return result["up"]


def _failed_steps(doc: dict) -> list[str]:
    return [k for k, v in (doc.get("steps") or {}).items() if str((v or {}).get("status")) in ("ERROR", "FAILED")]


def due(now: dt.datetime, first_start: dt.datetime | None, *, connectivity=None) -> dict:
    """Is the refresh (or a retry of its failed components) due? DONE ends the day's work. PARTIAL and FAILED are INCOMPLETE: the failed components are retried, bounded by MAX_ATTEMPTS real
    attempts a day. If the last failure was a lost connection the retry is due the moment the connection is back (no waiting gap); any other failure waits RETRY_GAP_MIN."""
    from operational import capture_timeline as ct, eastern_time as et
    day = et.eastern_today(now)
    at = ct.prep_times(dt.date.fromisoformat(day), first_start)["refresh_at"]
    at_s = at.strftime("%Y-%m-%dT%H:%M:%SZ")
    st = read() or {}
    same_day = st.get("day") == day
    if same_day and st.get("status") == "DONE":
        return {"due": False, "reason": "DONE_TODAY", "due_at_utc": at_s}
    if now < at:
        return {"due": False, "reason": "NOT_YET", "due_at_utc": at_s}
    if same_day and st.get("status") in ("PARTIAL", "FAILED"):
        if int(st.get("attempts") or 1) >= MAX_ATTEMPTS:
            return {"due": False, "reason": "RETRIES_EXHAUSTED", "due_at_utc": at_s, "failed": _failed_steps(st)}
        if st.get("network_down"):
            if (connectivity or network_up)():
                return {"due": True, "reason": "RETRY_NETWORK_BACK", "due_at_utc": at_s, "failed": _failed_steps(st)}
            return {"due": False, "reason": "WAITING_FOR_NETWORK", "due_at_utc": at_s, "failed": _failed_steps(st)}
        tried = st.get("finished_utc")
        if tried and (now - dt.datetime.fromisoformat(tried.replace("Z", "+00:00"))).total_seconds() < RETRY_GAP_MIN * 60:
            return {"due": False, "reason": "RETRY_GAP", "due_at_utc": at_s, "failed": _failed_steps(st)}
        return {"due": True, "reason": "RETRY_FAILED_STEPS", "due_at_utc": at_s, "failed": _failed_steps(st)}
    return {"due": True, "reason": "DUE", "due_at_utc": at_s, "late_min": round((now - at).total_seconds() / 60.0, 1)}


def run_if_due(now: dt.datetime | None = None, *, first_start: dt.datetime | None | str = "lookup", sync=None, starters=None, force: bool = False, connectivity=None) -> dict:
    """Run the refresh when it is due (or `force`). `sync`, `starters` and `connectivity` are injectable for tests."""
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
        return _run_locked(now, first_start, sync, starters, force, connectivity or network_up)


def _stamp(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_locked(now, first_start, sync, starters, force, connectivity) -> dict:
    """One attempt at the day's components, each with its own timestamps. A component that already succeeded today (OK or DISABLED) is carried over as it was, never re-run; only failed or
    never-run components are attempted. If the network is down nothing is attempted and no attempt is used up: the failure is recorded as a network failure and the retry is due when it returns."""
    from operational import eastern_time as et
    d = due(now, first_start, connectivity=connectivity)
    if not d["due"] and not force:
        return {"status": "SKIPPED", **d}
    day = et.eastern_today(now)
    prev = read() or {}
    carried = {k: v for k, v in (prev.get("steps") or {}).items() if prev.get("day") == day and str((v or {}).get("status")) in ("OK", "DISABLED")}
    attempts = int(prev.get("attempts") or 0) if prev.get("day") == day else 0
    t0 = dt.datetime.now(dt.timezone.utc)

    def clock() -> dt.datetime:                       # `now` plus the real elapsed time (a test clock stays consistent)
        return now + (dt.datetime.now(dt.timezone.utc) - t0)

    doc = {"day": day, "started_utc": _stamp(now), "due": d, "steps": dict(carried), "attempts": attempts}
    online = connectivity()
    if not online and carried.keys() >= {"statistics", "starters"}:
        return {"status": "SKIPPED", **d, "reason": "ALL_COMPONENTS_ALREADY_DONE"}        # a forced run on a finished day changes nothing, offline or not
    if not online:
        for name in ("statistics", "starters"):
            if name not in carried:
                doc["steps"][name] = {"status": "FAILED", "reason": "NO_CONNECTIVITY", "started_utc": _stamp(now), "finished_utc": _stamp(now)}
        doc["network_down"] = True
        doc["finished_utc"] = _stamp(now)
        doc["status"] = "FAILED" if len(doc["steps"]) and not carried else "PARTIAL"
        _write(doc)
        return doc
    doc["attempts"] = attempts + 1
    if "statistics" not in carried:
        started = clock()
        try:
            import sync_daily
            rc = (sync or sync_daily.run)()
            step = {"status": "OK" if rc == 0 else "FAILED", "exit_code": rc}
        except Exception as exc:  # noqa: BLE001
            step = {"status": "ERROR", "reason": f"{exc.__class__.__name__}: {exc}"}
        doc["steps"]["statistics"] = {**step, "started_utc": _stamp(started), "finished_utc": _stamp(clock())}
    if "starters" not in carried:
        started = clock()
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
            step = (starters or _starters)()
        except Exception as exc:  # noqa: BLE001
            step = {"status": "ERROR", "reason": f"{exc.__class__.__name__}: {exc}"}
        doc["steps"]["starters"] = {**step, "started_utc": _stamp(started), "finished_utc": _stamp(clock())}
    doc["finished_utc"] = _stamp(clock())
    bad = _failed_steps(doc)
    if bad and not connectivity():
        doc["network_down"] = True                      # the sources failed because the connection dropped: retry when it is back
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
    return 0 if out.get("status") in ("DONE", "PARTIAL", "SKIPPED") or out.get("network_down") else 1       # waiting for the network is not a job failure


if __name__ == "__main__":
    sys.exit(main())
