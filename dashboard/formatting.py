"""
Section 70: centralized number formatting for the operational dashboard.
Every opportunity-card-style page should import from here rather than
hand-rolling its own f-string formatting -- keeps probability/odds/edge
units consistent everywhere (Section AF: edge is percentage POINTS,
"pp"; EV is a percentage return, "%" -- never mixed).
"""
from __future__ import annotations

import datetime as dt

NO_LIVE_PRICE = "NO LIVE PRICE"
NOT_AVAILABLE = "NOT AVAILABLE"
NOT_YET_AVAILABLE = "NOT YET AVAILABLE"


def format_probability(p: float | None, digits: int = 1) -> str:
    if p is None:
        return "—"
    return f"{p * 100:.{digits}f}%"


def format_pp_delta(delta: float | None, digits: int = 1) -> str:
    """Percentage-POINT delta (e.g. context-overlay adjustment) -- always
    'pp', never bare '%', since a probability delta is not itself a
    percentage return."""
    if delta is None:
        return "—"
    sign = "+" if delta >= 0 else ""
    return f"{sign}{delta * 100:.{digits}f}pp"


def format_american_odds(odds: float | None) -> str:
    if odds is None:
        return NO_LIVE_PRICE
    sign = "+" if odds >= 0 else ""
    return f"{sign}{odds:.0f}"


def format_decimal_odds(odds: float | None) -> str:
    if odds is None:
        return NO_LIVE_PRICE
    return f"{odds:.2f}"


def format_edge(edge: float | None) -> str:
    """Conservative/raw edge is a probability-point quantity -- 'pp'."""
    return format_pp_delta(edge)


def format_ev(ev: float | None) -> str:
    """EV is a percentage RETURN on stake -- '%', not 'pp'. Callers must
    ensure the value passed here is genuinely a return, not a
    probability-point edge; the two are only numerically identical by
    coincidence for even-money prices."""
    if ev is None:
        return "—"
    sign = "+" if ev >= 0 else ""
    return f"{sign}{ev * 100:.1f}%"


def format_timestamp(iso_ts: str | None, now: dt.datetime | None = None) -> str:
    """Compact relative freshness string ('7m ago', '2h ago') rather than
    a full timestamp, for card footers (Section B9: don't repeat long
    'captured X minutes ago' text in multiple places)."""
    if not iso_ts:
        return "—"
    try:
        ts = dt.datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except ValueError:
        return iso_ts
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=dt.timezone.utc)
    now = now or dt.datetime.now(dt.timezone.utc)
    delta_minutes = (now - ts).total_seconds() / 60.0
    if delta_minutes < 60:
        return f"{delta_minutes:.0f}m ago"
    if delta_minutes < 24 * 60:
        return f"{delta_minutes / 60:.1f}h ago"
    return f"{delta_minutes / (24 * 60):.1f}d ago"
