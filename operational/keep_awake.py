"""
Keep the engine's Mac awake through the windows that matter, so a ticket's prices can be captured and compared at one evaluation time.

Why. launchd does not replay a slot that passed while the Mac slept: on 2026-10-12 the 8:00 AM look ran four minutes late only because the Mac woke. The windows below are the ones whose loss costs
a ticket: the 8:00 AM look, and for every wave of games (puck drops within 90 minutes of its first) the stretch from just before the pregame capture opens (credit_planner.FIRST_CAPTURE_HOURS before the
wave's first game) to that first puck drop, which also covers the T-35 moneyline pull and the recording cycles.

What it does. Every trader cycle calls `hold(now, nhl_conn)`. When the next window opens within LEAD_MIN (or is open) it starts one detached `caffeinate -i -s` for the rest of the window, once
(a state file names the process). That prevents IDLE sleep (and system sleep on power). It cannot do three things, which are stated, not hidden: a closed lid still sleeps the Mac unless it is on power
with an external display; a Mac that is already asleep cannot start this (a scheduled wake needs an administrator command, see docs/KEEP_AWAKE.md); and it never changes any setting. Nothing here spends credits.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess

from operational import state_paths

STATE_NAME = "keep_awake.json"
LEAD_MIN = 25.0              # start holding this long before a window opens: one trader cycle is 15 minutes, so the cycle before the window always starts it
MORNING_ET = ((7, 50), (8, 35))
PREGAME_LEAD_MIN = 105.0 + 20.0     # the pregame capture opens 105 minutes before the wave's first game; hold from 20 minutes earlier so the capture cycle is awake
AFTER_FIRST_START_MIN = 0.0


def _et_bounds(day: dt.date, hm_from, hm_to) -> tuple[dt.datetime, dt.datetime]:
    from zoneinfo import ZoneInfo
    z = ZoneInfo("America/New_York")
    f = dt.datetime.combine(day, dt.time(*hm_from), tzinfo=z).astimezone(dt.timezone.utc)
    t = dt.datetime.combine(day, dt.time(*hm_to), tzinfo=z).astimezone(dt.timezone.utc)
    return f, t


def windows(now: dt.datetime, nhl_conn) -> list[tuple[dt.datetime, dt.datetime, str]]:
    """[(opens_utc, closes_utc, why)] for today's Eastern day, merged where they overlap, in time order."""
    from operational import eastern_time as et
    day = dt.date.fromisoformat(et.eastern_today(now))
    out = [(*_et_bounds(day, *MORNING_ET), "the 8:00 AM look")]
    try:
        rows = nhl_conn.execute("SELECT game_id, scheduled_start_utc FROM games WHERE game_date = ? AND game_state = 'SCHEDULED' ORDER BY scheduled_start_utc", (str(day),)).fetchall()
        starts = {}
        for r in rows:
            t = dt.datetime.fromisoformat(r["scheduled_start_utc"].replace("Z", "+00:00"))
            starts[str(r["game_id"])] = t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
        from operational import credit_planner as cp
        for w in cp.waves(starts):
            first = starts[w[0]]
            out.append((first - dt.timedelta(minutes=PREGAME_LEAD_MIN), first + dt.timedelta(minutes=AFTER_FIRST_START_MIN), f"the pregame wave of {len(w)} game(s) at {first.strftime('%H:%MZ')}"))
    except Exception:  # noqa: BLE001 - without a schedule only the morning window is held
        pass
    out.sort()
    merged: list[list] = []
    for a, b, why in out:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
            merged[-1][2] += f" + {why}"
        else:
            merged.append([a, b, why])
    return [tuple(m) for m in merged]


def _alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def _state() -> dict:
    try:
        return json.loads(state_paths.path(STATE_NAME).read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def hold(now: dt.datetime, nhl_conn, *, spawn=None) -> dict:
    """Start (once) the hold for the window that is open or opens within LEAD_MIN. Returns what it did. Never raises."""
    if spawn is None and state_paths.under_test():       # a test run never starts a real process
        return {"status": "SKIPPED_UNDER_TEST"}
    spawn = spawn or (lambda secs: subprocess.Popen(["caffeinate", "-i", "-s", "-t", str(int(secs))], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                                     stderr=subprocess.DEVNULL, start_new_session=True).pid)
    try:
        for opens, closes, why in windows(now, nhl_conn):
            if now >= closes:
                continue
            if now < opens - dt.timedelta(minutes=LEAD_MIN):
                return {"status": "NOT_NEEDED_YET", "next_window_opens_utc": opens.strftime("%Y-%m-%dT%H:%M:%SZ"), "why": why}
            secs = (closes - now).total_seconds() + 300.0
            st = _state()
            if st.get("until_utc") and _alive(st.get("pid")) and dt.datetime.fromisoformat(st["until_utc"]) >= closes:
                return {"status": "HOLDING", "pid": st["pid"], "until_utc": st["until_utc"], "why": why}
            pid = spawn(secs)
            doc = {"pid": pid, "started_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "until_utc": (closes + dt.timedelta(minutes=5)).isoformat(), "why": why}
            p = state_paths.path(STATE_NAME)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(doc))
            return {"status": "STARTED", **doc}
        return {"status": "NO_MORE_WINDOWS_TODAY"}
    except Exception as exc:  # noqa: BLE001 - keeping the Mac awake must never stop a trader cycle
        return {"status": "ERROR", "reason": f"{exc.__class__.__name__}: {exc}"}
