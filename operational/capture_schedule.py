"""
Constants and helpers for the free/paid look at TOMORROW's games and for the legacy morning-status record.

Player-odds captures are NOT scheduled here any more: they follow the wave timeline in operational/capture_timeline.py (07:45 refresh, 08:00 analysis and league pull, one coordinated capture per
selected game 90 minutes before its wave). What remains: the Eastern clock helpers and the tomorrow-check slots (a paid tomorrow look exists only from explicit surplus).
"""
from __future__ import annotations

import datetime as dt

MORNING_START_ET = (8, 0)
TOMORROW_CHECKS_ET = ((8, 10), (20, 15))   # tomorrow's games: two cheap availability checks a day (morning and evening); each is an observation, with its time
SLOT_CLOSE_HOURS = 3.0                # morning/midday slots close this long before puck drop
RECHECK_MINUTES = 60.0                # re-ask for a market that was NOT_POSTED when last asked


def _localize(day: dt.date, hh: int, mm: int) -> dt.datetime:
    from zoneinfo import ZoneInfo
    return dt.datetime.combine(day, dt.time(hh, mm), tzinfo=ZoneInfo("America/New_York")).astimezone(dt.timezone.utc)


def _et_date(now: dt.datetime) -> dt.date:
    from operational import eastern_time as et
    return dt.date.fromisoformat(et.eastern_today(now))


def tomorrow_check_due(now: dt.datetime, last_check: dt.datetime | None) -> bool:
    """Due when a check time of today (08:10 or 20:15 ET) has passed and no check has been made since it."""
    passed = [t for t in (_localize(_et_date(now), h, m) for h, m in TOMORROW_CHECKS_ET) if t <= now]
    if not passed:
        return False
    latest = max(passed)
    return last_check is None or last_check < latest
