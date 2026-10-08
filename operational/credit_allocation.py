"""
Odds API credit audit and the bounded allocation for optional markets (anytime goals).

Everything here is read from what the provider itself reported: each archived response carries `x-requests-last` (credits that call
cost), `-used` and `-remaining`. Nothing makes a network call or spends a credit.

Findings this module computes (see docs/ODDS_CREDIT_AUDIT.md for the written audit):
  * call classes: the all-games moneyline call (1 credit), the per-game prop call (1 credit per market that returns data), and the free
    events listing (0);
  * the burn rate over the trailing window and the projected exhaustion date against the monthly allowance;
  * the marginal cost of one more market on a per-game call: exactly one credit per captured game (observed: a five-market call that
    returned four markets cost 4; one that returned three cost 3);
  * the rule for optional markets: an optional market may be captured only when the month still balances AFTER its cost. If the
    trailing burn already exceeds the even daily pace, the optional market stays off and the shortfall is stated -- it is never
    squeezed out of the reserve.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
from pathlib import Path

from operational import odds_quota

OPTIONAL_GOALS_MARKET = "player_goal_scorer_anytime"
WINDOW_DAYS = 7.0


def _archive_dir() -> Path:
    from research.live_sog_pricing import archive
    return Path(archive.ARCHIVE_DIR)


def _classify(meta: dict) -> str:
    ep = meta.get("endpoint") or ""
    if ep.endswith("/events"):
        return "events_listing"
    if "/events/" in ep:
        return "event_props"
    return "all_games_moneyline"


def read_calls(archive_dir: Path | None = None, since: dt.datetime | None = None) -> list[dict]:
    rows = []
    for path in glob.glob(str((archive_dir or _archive_dir()) / "*.json")):
        name = Path(path).name
        if since is not None and name[:8].isdigit() and name[:8] < (since - dt.timedelta(days=1)).strftime("%Y%m%d"):
            continue                                   # file names start with the UTC capture date; skip what cannot be in the window
        try:
            meta = (json.loads(Path(path).read_text()).get("meta")) or {}
        except (OSError, json.JSONDecodeError):
            continue
        ts = meta.get("retrieved_at_utc")
        if not ts or meta.get("requests_last_header") in (None, ""):
            continue
        at = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if since is not None and at < since:
            continue
        rows.append({"at": at, "class": _classify(meta), "credits": float(meta["requests_last_header"]), "markets": meta.get("market_filter"),
                     "used": int(meta["requests_used_header"]) if meta.get("requests_used_header") not in (None, "") else None,
                     "remaining": int(meta["requests_remaining_header"]) if meta.get("requests_remaining_header") not in (None, "") else None})
    return sorted(rows, key=lambda r: r["at"])


def spend_by_class(rows: list[dict]) -> dict:
    out: dict[str, dict] = {}
    total = sum(r["credits"] for r in rows) or 1.0
    for r in rows:
        key = r["class"] if r["class"] != "event_props" else f"event_props[{r['markets']}]"
        d = out.setdefault(key, {"calls": 0, "credits": 0.0})
        d["calls"] += 1
        d["credits"] += r["credits"]
    for d in out.values():
        d["share"] = round(d["credits"] / total, 3)
    return out


def status(now: dt.datetime | None = None, *, rows: list[dict] | None = None, remaining: int | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    window_start = now - dt.timedelta(days=WINDOW_DAYS)
    rows = rows if rows is not None else read_calls(since=window_start)
    window = [r for r in rows if r["at"] >= window_start]
    span_days = max((now - min((r["at"] for r in window), default=now)).total_seconds() / 86400.0, 1.0)
    burn = sum(r["credits"] for r in window) / span_days
    if remaining is None:
        known = [r["remaining"] for r in rows if r["remaining"] is not None]
        remaining = known[-1] if known else odds_quota.latest_remaining()
    days_left = (dt.datetime.combine(dt.date(now.year + (now.month == 12), now.month % 12 + 1, 1), dt.time.min, dt.timezone.utc) - now).total_seconds() / 86400.0
    usable = max((remaining or 0) - odds_quota.RESERVE, 0)
    even = usable / days_left if days_left > 0 else 0.0
    runway = usable / burn if burn > 0 else None
    shortfall = max(burn * days_left - usable, 0.0)
    return {"as_of_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "remaining": remaining, "reserve": odds_quota.RESERVE, "usable": usable,
            "days_left_in_cycle": round(days_left, 1), "even_daily_pace": round(even, 1), "trailing_daily_burn": round(burn, 1),
            "runway_days": None if runway is None else round(runway, 1),
            "projected_exhaustion_utc": None if runway is None else (now + dt.timedelta(days=runway)).strftime("%Y-%m-%d"),
            "month_shortfall_at_current_burn": round(shortfall), "spend_by_class": spend_by_class(window), "window_days": round(span_days, 1)}


def marginal_cost_of_goals(rows: list[dict]) -> dict:
    """Observed cost of the calls that included the goals market versus the same calls' market count."""
    goal_calls = [r for r in rows if r["class"] == "event_props" and r["markets"] and OPTIONAL_GOALS_MARKET in r["markets"]]
    return {"calls_observed": len(goal_calls), "credits_charged": sum(r["credits"] for r in goal_calls),
            "rule": "one credit per market that returns data, per game, per call; the goals market adds exactly one credit per captured game"}


def goals_capture_decision(st: dict, captured_games_per_day: float) -> dict:
    """May the optional goals market be added to captures? Only if the month balances after its cost."""
    extra = captured_games_per_day * 1.0
    need = (st["trailing_daily_burn"] + extra) * st["days_left_in_cycle"]
    short = max(need - st["usable"], 0.0)
    return {"allow": short <= 0.0, "extra_credits_per_day": round(extra, 1), "month_need": round(need), "month_usable": st["usable"],
            "shortfall": round(short), "reason": "OK" if short <= 0.0 else "MONTH_DOES_NOT_BALANCE_WITH_GOALS"}


def captures_per_day(rows: list[dict], now: dt.datetime, days: float = WINDOW_DAYS) -> float:
    """Per-game prop captures that include shots and points (the trader's call) per day over the window."""
    start = now - dt.timedelta(days=days)
    n = sum(1 for r in rows if r["class"] == "event_props" and r["at"] >= start and r["markets"] and "player_points" in r["markets"])
    return n / days


def goals_decision(now: dt.datetime | None = None) -> dict:
    """The live decision best_bets asks before adding the goals market to a capture. Any failure denies (never spends on a guess)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    from operational import state_paths
    if state_paths.under_test():
        return {"allow": False, "reason": "UNDER_TEST"}          # a test run never reads the live archive to decide a purchase
    try:
        rows = read_calls(since=now - dt.timedelta(days=WINDOW_DAYS + 1))
        st = status(now, rows=rows)
        return {**goals_capture_decision(st, captures_per_day(rows, now)), "remaining": st["remaining"], "trailing_daily_burn": st["trailing_daily_burn"]}
    except Exception as exc:  # noqa: BLE001
        return {"allow": False, "reason": f"ALLOCATION_UNAVAILABLE: {exc.__class__.__name__}"}
