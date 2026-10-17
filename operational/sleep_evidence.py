"""
Evidence that the Mac was asleep, from the operating system's own power log (`pmset -g log`).

A missed capture window is attributed to machine sleep ONLY when this log shows the machine in a sleep state during the window. A gap in the engine's own heartbeat is not sleep evidence: the
job could have crashed, been unloaded, or lost its network. Without sleep evidence the window is reported as an observed engine outage ("the job did not run"), never as sleep.

macOS only. On any other platform, or when the log cannot be read, `slept_during` returns evidence UNAVAILABLE and nothing is attributed to sleep.
"""
from __future__ import annotations

import datetime as dt
import re
import subprocess
import time

_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [+-]\d{4})\s+(Clamshell Sleep|DarkWake|Sleep|Wake)(?=\s{2,}|\t)")      # the kind column only ("Wake Requests", "WakeTime" are other columns)
_cache: dict = {"at": 0.0, "text": None}
CACHE_SECONDS = 60.0


def read_log() -> str | None:
    """The raw `pmset -g log` text (cached for a minute), or None when unavailable."""
    if time.monotonic() - _cache["at"] < CACHE_SECONDS and _cache["text"] is not None:
        return _cache["text"]
    try:
        out = subprocess.run(["pmset", "-g", "log"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    _cache.update(at=time.monotonic(), text=out)
    return out


def events(text: str) -> list[tuple[dt.datetime, str]]:
    """[(time UTC, 'Sleep' | 'Wake')] in order. DarkWake counts as still asleep (a brief maintenance wake with the screen off); Clamshell Sleep counts as Sleep."""
    out = []
    for line in text.splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        kind = m.group(2)
        t = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S %z").astimezone(dt.timezone.utc)
        out.append((t, "Wake" if kind == "Wake" else "Sleep"))
    return sorted(out, key=lambda e: e[0])


def sleep_intervals(text: str, now: dt.datetime) -> list[tuple[dt.datetime, dt.datetime]]:
    out, start = [], None
    for t, kind in events(text):
        if kind == "Sleep" and start is None:
            start = t
        elif kind == "Wake" and start is not None:
            out.append((start, t))
            start = None
    if start is not None:
        out.append((start, now))                                  # still asleep at the last event
    return out


def slept_during(window_start: dt.datetime, window_end: dt.datetime, now: dt.datetime | None = None, *, text: str | None = None) -> dict:
    """{"evidence": "ASLEEP" | "AWAKE" | "UNAVAILABLE", "slept_minutes", "intervals"} for the window. ASLEEP needs at least 5 minutes of sleep state inside it."""
    now = now or dt.datetime.now(dt.timezone.utc)
    text = text if text is not None else read_log()
    if not text or not events(text):
        return {"evidence": "UNAVAILABLE", "slept_minutes": 0.0, "intervals": []}
    if events(text)[0][0] > window_start:                         # the log does not reach back to the window: absence of a Sleep line proves nothing
        first = events(text)[0][0]
        covered = False
    else:
        first, covered = events(text)[0][0], True
    inside, total = [], 0.0
    for a, b in sleep_intervals(text, now):
        lo, hi = max(a, window_start), min(b, window_end)
        if hi > lo:
            inside.append((lo.strftime("%H:%MZ"), hi.strftime("%H:%MZ")))
            total += (hi - lo).total_seconds() / 60.0
    if total >= 5.0:
        return {"evidence": "ASLEEP", "slept_minutes": round(total, 1), "intervals": inside}
    if not covered:
        return {"evidence": "UNAVAILABLE", "slept_minutes": round(total, 1), "intervals": inside, "log_starts_utc": first.strftime("%Y-%m-%dT%H:%M:%SZ")}
    return {"evidence": "AWAKE", "slept_minutes": round(total, 1), "intervals": inside}
