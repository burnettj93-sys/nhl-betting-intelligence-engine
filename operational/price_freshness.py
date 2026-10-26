"""Is this sportsbook price CURRENT? Market-specific, from the capture schedule and from how prices actually move (docs/validation/price_freshness.json, built by deploy/analyze_price_freshness.py and pinned by
the cohort policy digest).

  FRESH    age <= fresh_max: nothing the capture schedule produces is older at the game's start. Normal admission, no mark on the card.
  AGING    fresh_max < age <= aging_max and no newer scheduled capture for the game is overdue: the best price that exists yet, still inside what the market's own movement allows. Admitted, with a visible
           PRICE_AGING note (a price/value caution: it is not a hockey contradiction and counts toward none).
  STALE    older than that, or a newer scheduled capture was due and does not exist. Not a current price: the leg is NOT admitted to a parlay, with the reason STALE MARKET PRICE / MARKET DATA QUALITY.
  MISSING  no real observation time can be established (missing, malformed or in the future).

A stale price says nothing about the hockey. The leg is still reviewed; its probability, contradiction and calibration are exactly what they were; only its admission is refused, and for a market-data reason that is
kept apart from every hockey and value reason (HOCKEY PROBABILITY is not MARKET DATA QUALITY is not PRICE / VALUE).
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from operational import capture_timeline as ct

STANDARD_PATH = Path(__file__).resolve().parent.parent / "docs" / "validation" / "price_freshness.json"
FRESH, AGING, STALE, MISSING = "FRESH", "AGING", "STALE", "MISSING"
REASON = "STALE MARKET PRICE / MARKET DATA QUALITY"
_cache: dict = {"mtime": None, "data": None}


def standard() -> dict:
    """The per-market limits. An unreadable file means NO standard: nothing is then judged stale on a number the engine cannot name (the schedule check still applies)."""
    try:
        mt = STANDARD_PATH.stat().st_mtime
        if _cache["mtime"] != mt:
            _cache.update(mtime=mt, data=json.loads(STANDARD_PATH.read_text()))
        return _cache["data"] or {}
    except (OSError, ValueError):
        return {}


def _p(s) -> dt.datetime | None:
    if not isinstance(s, str) or not s.strip():
        return None
    try:
        d = dt.datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def _z(d: dt.datetime | None) -> str | None:
    return None if d is None else d.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def classify(leg, now: dt.datetime, *, wave_first_start: dt.datetime | None = None, first_wave_start: dt.datetime | None = None, wave_pulls_funded: bool = False) -> dict:
    """The freshness of `leg`'s price at `now`. `wave_first_start` is the first puck drop of the leg's game's wave and `first_wave_start` that of the day's first wave (None when the schedule is unknown: then
    only the age limits apply). `wave_pulls_funded`: the day's plan buys a league pull for each later wave (otherwise a later wave's moneyline is served by the day's pregame pull alone)."""
    fam = getattr(leg, "market_family", None)
    std = (standard().get("markets") or {}).get(fam) or {}
    cad = standard().get("cadence") or {}
    fresh_max = float(std.get("fresh_max_min") or cad.get("fresh_max_min") or ct.CAPTURE_TARGET_MIN + ct.WAVE_MINUTES)
    aging_max = float(std.get("aging_max_min") or fresh_max)
    tol = float(cad.get("overdue_tolerance_min") or 10.0)
    obs = _p(getattr(leg, "retrieved_at_utc", None)) or _p(getattr(leg, "quote_updated_utc", None)) or _p(getattr(leg, "captured_at_utc", None))
    start = _p(getattr(leg, "game_start_utc", None)) or _p(getattr(leg, "provider_start_utc", None))
    base = {"category": "MARKET_DATA_QUALITY", "market": fam, "observed_at_utc": _z(obs), "game_start_utc": _z(start), "fresh_max_min": fresh_max, "aging_max_min": aging_max}
    if obs is None or (obs - now).total_seconds() > 60.0:
        return {**base, "status": MISSING, "age_min": None, "reason": "no real observation time can be established for this price (missing, malformed or in the future): it cannot be called current", "newer_capture_due_at_utc": None, "newer_capture_due": None}
    age = round(max((now - obs).total_seconds(), 0.0) / 60.0, 1)
    # the scheduled captures that serve this market for this game (UTC targets): a prop's own wave capture; a moneyline's day pregame pull, and its own wave's pull when the plan funds one
    targets = []
    if fam == "MONEYLINE":
        if first_wave_start is not None:
            targets.append(first_wave_start - dt.timedelta(minutes=ct.CAPTURE_TARGET_MIN))
        if wave_pulls_funded and wave_first_start is not None:
            targets.append(wave_first_start - dt.timedelta(minutes=ct.CAPTURE_TARGET_MIN))
    elif wave_first_start is not None:
        targets.append(wave_first_start - dt.timedelta(minutes=ct.CAPTURE_TARGET_MIN))
    due = min((t for t in targets if obs < t - dt.timedelta(minutes=tol)), default=None)          # the first scheduled capture AFTER this price was observed
    grace = dt.timedelta(minutes=ct.CAPTURE_TARGET_MIN - ct.PUBLISH_DEADLINE_MIN)               # a capture due at its target is expected to be complete by the publish deadline
    overdue = bool(due is not None and now >= due + grace)
    out = {**base, "age_min": age, "newer_capture_due_at_utc": _z(due), "newer_capture_due": None if due is None else bool(now >= due)}
    if overdue:
        return {**out, "status": STALE, "reason": f"a newer {fam} capture was due at {_z(due)} (complete by {_z(due + grace)}) and none exists: this price is from {_z(obs)}, {age:.0f} minutes ago"}
    if age <= fresh_max:
        return {**out, "status": FRESH, "reason": f"{age:.0f} minutes old: within {fresh_max:.0f}, the oldest the capture schedule leaves a price at puck drop"}
    if age <= aging_max:
        return {**out, "status": AGING, "reason": f"{age:.0f} minutes old: past {fresh_max:.0f} but within {aging_max:.0f} (how far this market's price moves), and no newer capture is overdue"}
    why = (f"{age:.0f} minutes old: outside the {aging_max:.0f} minutes this market's price can be called current" +
           ("; no newer capture was scheduled for this game yet (later waves' moneyline pulls run only when the plan funds them), so the old price is simply all there is" if due is None or now < due else ""))
    return {**out, "status": STALE, "reason": why}
