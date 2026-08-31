"""
Multi-market daily live-odds pull (user-requested 2026-08-30): pulls real
DraftKings odds via The Odds API for every currently-known NHL event, as
far forward as the provider lists them -- for the markets this project
has a validated model for (SOG, Goals, Assists, Points), plus Goalie
Saves and a speculative Team SOG probe, both explicitly requested despite
not yet being VALIDATED / confirmed to exist as a real market.

Real findings this was built against (verified live, not assumed):
  - The Odds API only charges credits for markets ACTUALLY RETURNED with
    data, not for every market key requested. A real test call for a
    game 30 days out, requesting 8 markets, cost exactly 3 credits (only
    h2h/spreads/totals were posted; none of the 5 requested player-prop
    markets existed yet and cost nothing). Requesting a broad market list
    for a far-out event is therefore NOT the same as "wasting credits" --
    the real cost driver is only markets that have actually started being
    priced by the book.
  - `team_totals` / `alternate_team_totals` were tried live against a
    real event and returned zero bookmaker data at zero cost. This does
    NOT confirm DraftKings offers a real Team SOG market -- it only
    confirms trying costs nothing. Left in as a standing zero-cost probe;
    if it ever returns real data, that is the first real confirmation
    this market exists at all.
  - The real 2026-27 NHL preseason starts 2026-09-19 (`gameType == 1`,
    confirmed live from api-web.nhle.com/v1/schedule), found dynamically
    below rather than hardcoded, so this stays correct if the schedule
    the provider carries ever shifts.

Credit management (per explicit user direction): games are queried
soonest-puck-drop-first; a per-day credit budget is computed from the
REAL remaining quota (read from the API's own response headers) divided
by the days left in the current billing cycle, so a full month's worth
of games can't be exhausted in the first day or two; a safety floor is
always preserved so this job can never fully zero the account. Every
credit counted here is a REAL cost read from
`ApiResult.requests_last` / the archived response's own
`requests_last_header` -- never estimated or assumed.

Run manually or on a schedule:
    python3 -m operational.live_odds_daily_pull

Never called automatically by the dashboard or the test suite -- both
would read the cache this writes (`operational/live_multimarket_board_cache.json`),
never the network, matching this project's existing "no polling from the
dashboard" rule (see research/live_sog_pricing/refresh.py's own docstring).
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import requests

from ingest.nhl_api import fetch_schedule_range, NHLApiSchemaError
from research.live_sog_pricing import client, archive
from research.player_props import registry as prop_registry

ARCHIVE_DIR = archive.ARCHIVE_DIR
BOARD_CACHE_PATH = REPO_ROOT / "operational" / "live_multimarket_board_cache.json"

# Every market this pull requests. Cost is 0 for any key that returns no
# data for a given event (see module docstring) -- so being inclusive
# here costs nothing until a book actually posts the market.
TARGET_MARKETS = (
    "player_shots_on_goal,player_goals,player_assists,player_points,"
    "player_total_saves,team_totals,alternate_team_totals"
)

DEFAULT_CYCLE_RESET_DAY = 1     # ASSUMPTION: calendar-month reset, used only to
                                # pace daily_budget across the cycle. The Odds
                                # API does not expose the real reset date in
                                # any response header observed so far --
                                # confirm against the account dashboard if
                                # this assumption turns out to be wrong.
DEFAULT_SAFETY_FLOOR = 20      # never spend the account down to zero
PRESEASON_LEAD_DAYS = 2         # "48 hours before the preseason starts"

_ODDS_KEY_TO_MARKET_TYPE = {
    e.odds_api_market_key: e.market_type
    for e in prop_registry.REGISTRY if e.odds_api_market_key
}


def _now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def find_next_preseason_start(session: requests.Session | None = None,
                               scan_days: int = 90) -> dt.date | None:
    """Real preseason start date, found live from the NHL's own schedule
    API (gameType == 1) -- never hardcoded, so this can't go stale.
    Returns None (never fabricates a date) if no future preseason game is
    found within `scan_days` of today."""
    session = session or requests.Session()
    today = _now_utc().date()
    try:
        games = fetch_schedule_range(session, today, today + dt.timedelta(days=scan_days))
    except (NHLApiSchemaError, requests.RequestException):
        return None
    preseason_dates = sorted({
        dt.date.fromisoformat(g["gameDate"]) for g in games
        if g.get("gameType") == 1 and g.get("gameDate")
    })
    return preseason_dates[0] if preseason_dates else None


def should_run_today(today: dt.date, preseason_start: dt.date | None,
                      lead_days: int = PRESEASON_LEAD_DAYS) -> bool:
    """False (no-op, no API calls at all -- not even the free ones) until
    we're within `lead_days` of the real preseason start, or once we're
    past it (the whole point is to run FROM that point forward)."""
    if preseason_start is None:
        return False
    return today >= preseason_start - dt.timedelta(days=lead_days)


def _add_month(d: dt.date) -> dt.date:
    if d.month == 12:
        return d.replace(year=d.year + 1, month=1)
    return d.replace(month=d.month + 1)


def _cycle_start(today: dt.date, reset_day: int = DEFAULT_CYCLE_RESET_DAY) -> dt.date:
    if today.day >= reset_day:
        return today.replace(day=reset_day)
    prev_month_last_day = today.replace(day=1) - dt.timedelta(days=1)
    return prev_month_last_day.replace(day=min(reset_day, prev_month_last_day.day))


def _next_cycle_start(today: dt.date, reset_day: int = DEFAULT_CYCLE_RESET_DAY) -> dt.date:
    current = _cycle_start(today, reset_day)
    nxt = _add_month(current)
    last_day_of_month = (_add_month(nxt.replace(day=1)) - dt.timedelta(days=1)).day
    return nxt.replace(day=min(reset_day, last_day_of_month))


def _credits_spent_since(since: dt.datetime, archive_dir: Path | None = None) -> int:
    """Sums REAL cost from every archived response's own
    `requests_last_header` since `since` -- derived from the archive
    (the actual record of what happened), never a separately maintained
    counter that could drift from reality.

    `archive_dir` defaults to None rather than the module-level
    ARCHIVE_DIR directly (Preseason Operational Readiness Closure sprint,
    Part 5/Part 7 fix): a mutable default bound at function-definition
    time freezes whatever ARCHIVE_DIR equaled at import time, so
    mock.patch("operational.live_odds_daily_pull.ARCHIVE_DIR", ...) in a
    test would silently have NO effect on this function -- a real,
    confirmed bug this sprint found (see archive_result()'s identical
    fix in research/live_sog_pricing/archive.py). Looking ARCHIVE_DIR up
    fresh, by name, inside the function body is what makes mock.patch on
    the module attribute actually work."""
    if archive_dir is None:
        archive_dir = ARCHIVE_DIR
    if not archive_dir.exists():
        return 0
    total = 0
    for f in archive_dir.glob("*.json"):
        try:
            meta = json.loads(f.read_text())["meta"]
            retrieved = dt.datetime.fromisoformat(meta["retrieved_at_utc"].replace("Z", "+00:00"))
            if retrieved >= since:
                total += int(meta.get("requests_last_header") or 0)
        except (json.JSONDecodeError, KeyError, ValueError, TypeError, OSError):
            continue
    return total


def _future_events_sorted(events: list[dict], now: dt.datetime) -> list[dict]:
    out = []
    for e in events:
        try:
            commence = dt.datetime.fromisoformat(e["commence_time"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if commence > now:
            out.append(e)
    return sorted(out, key=lambda e: e["commence_time"])


def _parse_event_odds_generic(event: dict, odds_data: dict) -> list[dict]:
    """Deliberately shallow, honest capture -- outcome name/description/
    price/point exactly as returned, tagged with this project's own real
    registry status for that market (never guessing threshold/milestone
    semantics per market the way market_parser.py does for the one market
    -- SOG -- that has actually been researched against a real payload).
    An empty list is a completely normal, expected result for a
    not-yet-posted market."""
    quotes = []
    for bm in odds_data.get("bookmakers", []):
        for market in bm.get("markets", []):
            market_key = market.get("key")
            entry_type = _ODDS_KEY_TO_MARKET_TYPE.get(market_key)
            entry = prop_registry.get(entry_type) if entry_type else None
            for outcome in market.get("outcomes", []):
                quotes.append({
                    "event_id": event["id"], "home_team": event["home_team"],
                    "away_team": event["away_team"], "commence_time": event["commence_time"],
                    "bookmaker": bm.get("key"), "bookmaker_last_update_utc": bm.get("last_update"),
                    "market_key": market_key, "market_last_update_utc": market.get("last_update"),
                    "player_or_side": outcome.get("description") or outcome.get("name"),
                    "outcome_name": outcome.get("name"), "point": outcome.get("point"),
                    "price_american": outcome.get("price"),
                    "model_status": entry.model_status if entry else "NO_MODEL_THIS_MARKET",
                    "retrieved_at_utc": odds_data.get("_retrieved_at_utc"),
                })
    return quotes


def run_daily_pull(cycle_reset_day: int = DEFAULT_CYCLE_RESET_DAY,
                    safety_floor: int = DEFAULT_SAFETY_FLOOR,
                    lead_days: int = PRESEASON_LEAD_DAYS) -> dict:
    """Never raises past the caller -- any real failure is captured in
    the summary dict, matching this project's established
    refresh()/record_daily_predictions.py error philosophy.

    The daily credit budget is derived from the REAL `x-requests-remaining`
    value on the most recent live response, not from a locally
    reconstructed "spent so far" total -- the account's own count is
    ground truth and can't drift out of sync with this job's local
    archive (e.g. if any call to this key is ever made outside this
    script). `credits_spent_this_cycle_via_this_job` is reported
    separately, purely informationally, from this job's own archive."""
    now = _now_utc()
    today = now.date()
    summary = {
        "run_at_utc": now.isoformat(), "preseason_start": None, "ran": False,
        "reason": None, "events_seen": 0, "events_queried": 0, "quotes_captured": 0,
        "credits_spent_this_run": 0, "credits_spent_this_cycle_via_this_job": None,
        "remaining_quota_last_seen": None, "api_error": None,
    }

    preseason_start = find_next_preseason_start()
    summary["preseason_start"] = preseason_start.isoformat() if preseason_start else None
    if not should_run_today(today, preseason_start, lead_days):
        summary["reason"] = (
            "preseason start not yet known" if preseason_start is None
            else f"today ({today}) is more than {lead_days} day(s) before preseason start "
                 f"({preseason_start}) -- no API calls made")
        return summary

    r_events = client.get_nhl_events()
    if not r_events.ok:
        summary["api_error"] = r_events.error
        return summary
    archive.archive_result(r_events, event_id=None, market_filter=None, bookmaker_filter=None)
    summary["ran"] = True
    real_remaining = int(r_events.requests_remaining or 0)
    summary["remaining_quota_last_seen"] = real_remaining
    cycle_start = dt.datetime.combine(_cycle_start(today, cycle_reset_day), dt.time.min, dt.timezone.utc)
    summary["credits_spent_this_cycle_via_this_job"] = _credits_spent_since(cycle_start)

    days_left_in_cycle = max((_next_cycle_start(today, cycle_reset_day) - today).days, 1)
    remaining_after_floor = max(real_remaining - safety_floor, 0)
    daily_budget = remaining_after_floor // days_left_in_cycle

    events = r_events.data
    summary["events_seen"] = len(events)
    future_events = _future_events_sorted(events, now)  # soonest puck-drop first

    if daily_budget <= 0:
        summary["reason"] = (
            f"daily credit budget is 0 (real remaining={real_remaining}, safety floor={safety_floor}, "
            f"days left in cycle={days_left_in_cycle}) -- events list still archived, no per-event odds queried")
        return summary

    spent_today = 0
    board_rows = []
    for event in future_events:
        if spent_today >= daily_budget:
            summary["reason"] = f"stopped: today's dynamic credit budget ({daily_budget}) reached"
            break
        r_odds = client.get_event_odds(event["id"], markets=TARGET_MARKETS)
        summary["events_queried"] += 1
        if not r_odds.ok:
            continue
        archive.archive_result(r_odds, event_id=event["id"], market_filter=TARGET_MARKETS,
                                bookmaker_filter="draftkings")
        cost = int(r_odds.requests_last or 0)
        spent_today += cost
        summary["credits_spent_this_run"] += cost
        summary["remaining_quota_last_seen"] = int(r_odds.requests_remaining or 0)
        r_odds.data["_retrieved_at_utc"] = r_odds.retrieved_at_utc
        quotes = _parse_event_odds_generic(event, r_odds.data)
        summary["quotes_captured"] += len(quotes)
        board_rows.extend(quotes)

    _write_board_cache(board_rows, summary)
    return summary


def _write_board_cache(rows: list[dict], summary: dict) -> None:
    payload = {"generated_at_utc": _now_utc().isoformat(), "summary": summary, "rows": rows}
    BOARD_CACHE_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    result = run_daily_pull()
    print(json.dumps(result, indent=2, sort_keys=True))
