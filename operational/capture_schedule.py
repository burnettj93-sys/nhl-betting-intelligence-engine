"""
When prices are fetched during the day. One place, pure functions, no network.

The old rule bought one price per game when the game came within about five hours of puck drop (about 100 minutes before it, in the credit plan), so the
app held no player prices until mid-afternoon. That was our own policy, not the bookmaker's: DraftKings posts player shots, points and goals on the morning
of the game (docs/MORNING_WORKFLOW.md, "What DraftKings actually posts"). The schedule is now a set of SLOTS per game on the Eastern clock:

  MORNING   opens 08:00 ET     every game today. A first look: provisional recommendations, never recorded as tickets.
  MIDDAY    opens 12:30 ET     a second look, only with credits left over (the REFRESH class).
  PREGAME   opens 105 min before puck drop (credit_planner.FIRST_CAPTURE_HOURS). The price a ticket is recorded on: fresh through puck drop.
  A morning/midday slot closes 3 hours before puck drop (the pregame slot takes over); a game that starts too early in the day has no morning slot.

A slot is satisfied by ANY capture of the planned markets taken at or after the slot opened (whichever job pulled it). A market the bookmaker had not posted
when we asked (NOT_POSTED, cost 0) is looked at again after RECHECK_MINUTES, so the first time it appears is caught without waiting for the next slot.
Tomorrow's games get two cheap checks a day (TOMORROW_CHECKS_ET) and are otherwise covered by the morning slot once they become "today". Each check is recorded as an observation with its time.

Using a price is judged by its actual age, not by the slot that fetched it: a price inside the freshness limit (150 minutes; 100 within two hours of puck drop) can be shown, added to a
personal log, and (capped, see daily_tickets.EARLY_TICKET_CAP) recorded on an automatic ticket at any hour; one that has aged out can only be shown as provisional. The pregame slot exists
because it is the last look before the game, not because nothing earlier may be acted on.
"""
from __future__ import annotations

import datetime as dt

MORNING, MIDDAY, PREGAME = "MORNING", "MIDDAY", "PREGAME"
MORNING_START_ET = (8, 0)
MIDDAY_START_ET = (12, 30)
TOMORROW_CHECKS_ET = ((8, 10), (20, 15))   # tomorrow's games: two cheap availability checks a day (morning and evening); each is an observation, with its time
SLOT_CLOSE_HOURS = 3.0                # morning/midday slots close this long before puck drop
RECHECK_MINUTES = 60.0                # re-ask for a market that was NOT_POSTED when last asked
PREGAME_HOURS = 1.75                  # == credit_planner.FIRST_CAPTURE_HOURS (kept equal by a test)


def _localize(day: dt.date, hh: int, mm: int) -> dt.datetime:
    from zoneinfo import ZoneInfo
    return dt.datetime.combine(day, dt.time(hh, mm), tzinfo=ZoneInfo("America/New_York")).astimezone(dt.timezone.utc)


def windows(now: dt.datetime, start: dt.datetime) -> dict[str, tuple[dt.datetime, dt.datetime]]:
    """{slot: (opens_utc, closes_utc)} for a game starting at `start` (UTC), judged on `now`'s Eastern day. Absent slot = no such window for this game."""
    out = {}
    close = start - dt.timedelta(hours=SLOT_CLOSE_HOURS)
    m_open = _localize(_et_date(now), *MORNING_START_ET)
    d_open = _localize(_et_date(now), *MIDDAY_START_ET)
    if close > m_open:
        out[MORNING] = (m_open, close)
    if close > d_open:
        out[MIDDAY] = (d_open, close)
    out[PREGAME] = (start - dt.timedelta(hours=PREGAME_HOURS), start)
    return out


def _et_date(now: dt.datetime) -> dt.date:
    from operational import eastern_time as et
    return dt.date.fromisoformat(et.eastern_today(now))


def decide(now: dt.datetime, start: dt.datetime, captured_at: dict[str, dt.datetime | None], markets: list[str],
           not_posted_checked: dict[str, dt.datetime] | None = None) -> tuple[str | None, str]:
    """(slot, reason) due for this game right now, or (None, reason it is not).

    captured_at        {market: UTC time of the newest capture that returned the market (None = none today)}
    not_posted_checked {market: UTC time we last asked and the provider had not posted it}
    PREGAME is returned only as a hint: its own FIRST / REFRESH logic (freshness limits, leftover credits) stays in best_bets.planned_decision."""
    if start <= now:
        return None, "STARTED"
    w = windows(now, start)
    p_open, _ = w[PREGAME]
    if now >= p_open:
        return PREGAME, "PREGAME_WINDOW_OPEN"
    not_posted_checked = not_posted_checked or {}
    active = None
    for slot in (MIDDAY, MORNING):                      # the later slot that is already open covers the earlier one
        if slot in w and w[slot][0] <= now < w[slot][1]:
            active = slot
            break
    if active is None:
        first_open = min((w[s][0] for s in (MORNING, MIDDAY) if s in w), default=None)
        if first_open is not None and now < first_open:
            return None, "BEFORE_MORNING_UPDATE"
        return None, "BETWEEN_SLOTS"
    if active == MIDDAY and MORNING in w:
        m_open = w[MORNING][0]
        looked = any((captured_at.get(m) is not None and captured_at[m] >= m_open) or (not_posted_checked.get(m) is not None and not_posted_checked[m] >= m_open) for m in markets)
        if not looked:
            active = MORNING               # the first look of the day was missed (late start, outage): it is still the FIRST look, not a second one, and uses the morning allowance
    opens = w[active][0]
    missing = []
    for m in markets:
        got = captured_at.get(m)
        if got is not None and got >= opens:
            continue
        asked = not_posted_checked.get(m)
        if asked is not None and asked >= opens and (now - asked).total_seconds() / 60.0 < RECHECK_MINUTES:
            continue
        missing.append(m)
    if missing:
        return active, "SLOT_OPEN_NOT_CAPTURED" if len(missing) == len(markets) else "SLOT_OPEN_PARTLY_CAPTURED"
    return None, f"{active}_SLOT_SATISFIED"


def tomorrow_check_due(now: dt.datetime, last_check: dt.datetime | None) -> bool:
    """Due when a check time of today (08:10 or 20:15 ET) has passed and no check has been made since it."""
    passed = [t for t in (_localize(_et_date(now), h, m) for h, m in TOMORROW_CHECKS_ET) if t <= now]
    if not passed:
        return False
    latest = max(passed)
    return last_check is None or last_check < latest
