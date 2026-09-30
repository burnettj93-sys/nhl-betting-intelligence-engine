"""
Platform Recovery block (2026-09-29): the single source of truth for
"what day is today" for real NHL schedule purposes.

NHL games are scheduled on an Eastern-time hockey day (`games.game_date`,
sourced directly from the NHL API's own `gameDate` field, already reflects
this). A naive `dt.date.today()` depends on whatever timezone the server
process happens to be running in, and a raw UTC `.date()` rolls over to
"tomorrow" at 8 PM Eastern (Daylight) / 7 PM Eastern (Standard) -- hours
before tonight's real games have even started. Every "is this today's real
game" comparison must go through eastern_today() here, never either of
those two.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/Toronto")


def eastern_today(now: dt.datetime | None = None) -> str:
    """The current Eastern-time calendar date, as 'YYYY-MM-DD'. `now` must
    be timezone-aware (naive datetimes are ambiguous about which timezone
    they're already in) -- defaults to the real current UTC time."""
    now = now or dt.datetime.now(dt.timezone.utc)
    return now.astimezone(EASTERN).date().isoformat()
