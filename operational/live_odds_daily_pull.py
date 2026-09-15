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
  - Live Odds Collection + Parlay + Post-Mortem activation sprint
    (2026-09-15): re-confirmed live that 33 real NHL events are already
    listed by The Odds API, earliest 2026-09-29 -- games are being
    priced well before the preseason itself starts. The owner explicitly
    authorized starting collection now rather than waiting for the old
    2-day preseason lead window (see PRESEASON_LEAD_DAYS below).

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
PRESEASON_LEAD_DAYS = 7         # Live Odds/Parlay/Post-Mortem activation sprint
                                 # (2026-09-15): was 2 ("48 hours before the
                                 # preseason starts"), bumped to 7 on explicit
                                 # owner authorization to begin collection now
                                 # rather than wait, backed by real evidence
                                 # (33 NHL events already listed by The Odds
                                 # API, earliest 2026-09-29 -- confirmed live,
                                 # 0-credit call, same day this was changed).

_ODDS_KEY_TO_MARKET_TYPE = {
    e.odds_api_market_key: e.market_type
    for e in prop_registry.REGISTRY if e.odds_api_market_key
}

# Market keys this project has already deliberately probed and knows
# about, even though most have no internal model to compare against
# (Part 17 warning: team_totals/alternate_team_totals are NOT assumed to
# equal Team SOG -- see their real payload semantics before ever treating
# them as such). A market key outside this set is genuinely new evidence
# -- flagged, archived, and NEVER auto-verified (Part 15).
_KNOWN_UNMODELED_MARKET_KEYS = frozenset({"team_totals", "alternate_team_totals", "spreads", "totals",
                                           "h2h"})  # h2h IS modeled (MONEYLINE) but via the dedicated
                                                    # run_moneyline_snapshot()/provider_adapter path, not
                                                    # this generic prop parser -- known, not "new".
NEW_CONTRACT_CANDIDATES_PATH = REPO_ROOT / "operational" / "new_contract_candidates.jsonl"


def _flag_new_contract_candidate(market_key: str, market: dict, event: dict, retrieved_at_utc: str | None) -> None:
    """Append-only, real-evidence log of a market key never seen before
    by this pull job -- Part 15: 'archive, flag as NEW CONTRACT
    CANDIDATE, but DO NOT auto-verify.' Verification still requires a
    human to inspect the real payload, map participants, confirm
    threshold/side/price semantics, and write a sanitized fixture +
    regression test (see provider_adapter.py's own VERIFIED_CONTRACTS
    workflow) -- nothing here does any of that automatically."""
    NEW_CONTRACT_CANDIDATES_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "flagged_at_utc": retrieved_at_utc,
        "market_key": market_key,
        "event_id": event.get("id"),
        "home_team": event.get("home_team"),
        "away_team": event.get("away_team"),
        "sample_outcome_keys": sorted({k for o in market.get("outcomes", []) for k in o.keys()}),
        "outcome_count": len(market.get("outcomes", [])),
        "status": "NEW_CONTRACT_CANDIDATE",
        "auto_verified": False,
    }
    with open(NEW_CONTRACT_CANDIDATES_PATH, "a") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


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
            if entry is None and market_key not in _ODDS_KEY_TO_MARKET_TYPE and market_key not in _KNOWN_UNMODELED_MARKET_KEYS:
                model_status = "NEW_CONTRACT_CANDIDATE"
                _flag_new_contract_candidate(market_key, market, event, odds_data.get("_retrieved_at_utc"))
            else:
                model_status = entry.model_status if entry else "NO_MODEL_THIS_MARKET"
            for outcome in market.get("outcomes", []):
                quotes.append({
                    "event_id": event["id"], "home_team": event["home_team"],
                    "away_team": event["away_team"], "commence_time": event["commence_time"],
                    "bookmaker": bm.get("key"), "bookmaker_last_update_utc": bm.get("last_update"),
                    "market_key": market_key, "market_last_update_utc": market.get("last_update"),
                    "player_or_side": outcome.get("description") or outcome.get("name"),
                    "outcome_name": outcome.get("name"), "point": outcome.get("point"),
                    "price_american": outcome.get("price"),
                    "model_status": model_status,
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


# ---------------------------------------------------------------------
# Moneyline snapshots (Part 8): MONEYLINE is the one VERIFIED_CONTRACTS
# market and is cheap, so this runs more often and separately from the
# budget-gated player-prop sweep above. Reuses the real, regression-
# tested h2h parser rather than the generic/unverified prop path.
# ---------------------------------------------------------------------

MONEYLINE_MARKET = "h2h"
MONEYLINE_CACHE_PATH = REPO_ROOT / "operational" / "moneyline_snapshot_cache.json"
MONEYLINE_MAX_EVENTS_PER_SNAPSHOT = 20  # league-wide, but still a real cap --
                                        # Part 2's "do not blindly poll everything".


def _auto_snapshot_label(now: dt.datetime) -> str:
    """Derives a human-readable operational label from wall-clock hour
    (America/Toronto) so the scheduler's plist doesn't need a separate
    entry per time slot just to pass a different --label. Cosmetic only
    -- see this function's caller's docstring for why the TRUE
    first-observed/closing semantics never depend on this label."""
    try:
        from zoneinfo import ZoneInfo
        local_hour = now.astimezone(ZoneInfo("America/Toronto")).hour
    except Exception:  # noqa: BLE001 -- never let a tz-data issue break a real snapshot
        local_hour = now.hour
    if local_hour < 11:
        return "morning"
    if local_hour < 15:
        return "afternoon"
    if local_hour < 19:
        return "pregame"
    return "late"


def run_moneyline_snapshot(snapshot_label: str | None = None,
                            max_events: int = MONEYLINE_MAX_EVENTS_PER_SNAPSHOT) -> dict:
    """League-wide moneyline snapshot. `snapshot_label` is operational
    bookkeeping only (e.g. "morning"/"afternoon"/"pregame"/"closing" --
    auto-derived from wall-clock hour if not given explicitly, so one
    scheduled command works at every time slot) -- the TRUE
    first-observed/closing semantics (Parts 21-22) are derived later
    from the archive's own `retrieved_at_utc` timestamps (min/max per
    event), never from this label, so a missed or renamed scheduled run
    can't corrupt that history. Never queries an event whose
    commence_time has already passed (Part 20).

    Odds API Cost Optimization Correction (2026-09-15): this used to
    loop client.get_event_odds() once PER EVENT to build a league-wide
    snapshot -- a real 20-event run genuinely cost 20 credits this way
    (confirmed live, one credit per event, see
    ODDS_API_COST_OPTIMIZATION_CORRECTION_REPORT.md). Per the provider's
    own documented cost model, ONE call to the sport-level
    /sports/{sport}/odds endpoint (client.get_sport_odds()) returns odds
    for every currently-listed event at a cost proportional only to
    markets x regions requested -- NOT to event count. This function now
    makes exactly one paid call (skipped entirely if there are no future
    events to report), regardless of how many games are listed."""
    now = _now_utc()
    snapshot_label = snapshot_label or _auto_snapshot_label(now)
    summary = {
        "run_at_utc": now.isoformat(), "snapshot_label": snapshot_label, "ran": False,
        "events_seen": 0, "events_queried": 0, "parsed_count": 0, "credits_spent_this_run": 0,
        "remaining_quota_last_seen": None, "api_error": None,
    }

    r_events = client.get_nhl_events()
    if not r_events.ok:
        summary["api_error"] = r_events.error
        return summary
    archive.archive_result(r_events, event_id=None, market_filter=None, bookmaker_filter=None)
    summary["ran"] = True
    summary["remaining_quota_last_seen"] = int(r_events.requests_remaining or 0)

    events = r_events.data
    summary["events_seen"] = len(events)
    future_events = _future_events_sorted(events, now)[:max_events]

    if not future_events:
        # Nothing to report -- skip the paid sport-level call entirely
        # rather than spend credits confirming an already-known empty set.
        payload = {"generated_at_utc": now.isoformat(), "summary": summary, "rows": []}
        MONEYLINE_CACHE_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True))
        return summary

    from research.generic_prop_pricing import provider_adapter

    r_odds = client.get_sport_odds(markets=MONEYLINE_MARKET, bookmakers="draftkings")
    if not r_odds.ok:
        summary["api_error"] = r_odds.error
        return summary
    archive.archive_result(r_odds, event_id=None, market_filter=MONEYLINE_MARKET,
                            bookmaker_filter="draftkings")
    cost = int(r_odds.requests_last or 0)
    summary["credits_spent_this_run"] = cost
    summary["remaining_quota_last_seen"] = int(r_odds.requests_remaining or 0)

    future_event_ids = {e["id"] for e in future_events}
    rows = []
    for event_payload in r_odds.data:
        if event_payload.get("id") not in future_event_ids:
            continue  # league-wide response can include events outside our future/max_events window
        summary["events_queried"] += 1
        parsed = provider_adapter.parse_the_odds_api_h2h_market(event_payload)
        if parsed["status"] == "PARSED":
            summary["parsed_count"] += 1
            m = parsed["market"]
            rows.append({
                "event_id": m.event_id, "home_team_abbrev": m.home_team_abbrev,
                "away_team_abbrev": m.away_team_abbrev, "home_price": m.home_price,
                "away_price": m.away_price, "commence_time_utc": m.commence_time_utc,
                "captured_at_utc": r_odds.retrieved_at_utc, "snapshot_label": snapshot_label,
            })

    payload = {"generated_at_utc": now.isoformat(), "summary": summary, "rows": rows}
    MONEYLINE_CACHE_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True))
    return summary


# ---------------------------------------------------------------------
# Two-stage targeted prop sweep (Part 11): first sweep narrows to
# SOG+Saves only (Part 9's primary priority) for events now inside the
# ~3-4h pre-puck-drop window; second sweep re-pulls only events the
# first sweep already found a live quote for, ~45-75 minutes out.
# ---------------------------------------------------------------------

FIRST_SWEEP_MARKETS = "player_shots_on_goal,player_total_saves"
FIRST_SWEEP_WINDOW_HOURS = (3.0, 4.5)
SECOND_SWEEP_WINDOW_HOURS = (0.75, 1.25)
SWEEP_CACHE_PATH = REPO_ROOT / "operational" / "targeted_prop_sweep_cache.json"


def _events_in_window(events: list[dict], now: dt.datetime, window_hours: tuple[float, float]) -> list[dict]:
    lo, hi = window_hours
    out = []
    for e in events:
        try:
            commence = dt.datetime.fromisoformat(e["commence_time"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if commence <= now:
            continue  # never poll a started/past event (Part 20)
        hours_until = (commence - now).total_seconds() / 3600.0
        if lo <= hours_until <= hi:
            out.append(e)
    return sorted(out, key=lambda e: e["commence_time"])


def _load_json_cache(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def run_targeted_prop_sweep(sweep: str) -> dict:
    """`sweep` is `"first"` or `"second"`. First sweep: every event
    currently 3-4.5h from puck drop, SOG+Saves only. Second sweep: every
    event currently 45-75 minutes from puck drop, re-pulled only if the
    first sweep's own cache shows it produced at least one real quote
    for that event (Part 11's "re-pull only if... meaningful edge /
    starter certainty resolving / Top Conviction candidate" -- approximated
    here as "the market actually exists," the cheapest real signal
    available without a live in-season model-scoring pass; see the
    activation report's Known Limitations for the fuller pre-screening
    this stops short of)."""
    if sweep not in ("first", "second"):
        raise ValueError(f"sweep must be 'first' or 'second', got {sweep!r}")

    now = _now_utc()
    summary = {
        "run_at_utc": now.isoformat(), "sweep": sweep, "ran": False,
        "events_in_window": 0, "events_queried": 0, "quotes_captured": 0,
        "credits_spent_this_run": 0, "remaining_quota_last_seen": None, "api_error": None,
    }

    r_events = client.get_nhl_events()
    if not r_events.ok:
        summary["api_error"] = r_events.error
        return summary
    archive.archive_result(r_events, event_id=None, market_filter=None, bookmaker_filter=None)
    summary["ran"] = True
    summary["remaining_quota_last_seen"] = int(r_events.requests_remaining or 0)

    window = FIRST_SWEEP_WINDOW_HOURS if sweep == "first" else SECOND_SWEEP_WINDOW_HOURS
    candidates = _events_in_window(r_events.data, now, window)
    summary["events_in_window"] = len(candidates)

    if sweep == "second":
        first_cache = _load_json_cache(SWEEP_CACHE_PATH) or {}
        events_with_quotes = {r["event_id"] for r in first_cache.get("rows", [])}
        candidates = [e for e in candidates if e["id"] in events_with_quotes]

    board_rows = []
    for event in candidates:
        r_odds = client.get_event_odds(event["id"], markets=FIRST_SWEEP_MARKETS)
        summary["events_queried"] += 1
        if not r_odds.ok:
            continue
        archive.archive_result(r_odds, event_id=event["id"], market_filter=FIRST_SWEEP_MARKETS,
                                bookmaker_filter="draftkings")
        cost = int(r_odds.requests_last or 0)
        summary["credits_spent_this_run"] += cost
        summary["remaining_quota_last_seen"] = int(r_odds.requests_remaining or 0)
        r_odds.data["_retrieved_at_utc"] = r_odds.retrieved_at_utc
        quotes = _parse_event_odds_generic(event, r_odds.data)
        summary["quotes_captured"] += len(quotes)
        board_rows.extend(quotes)

    payload = {"generated_at_utc": now.isoformat(), "summary": summary, "rows": board_rows}
    SWEEP_CACHE_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True))
    return summary


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("props", "moneyline", "sweep-first", "sweep-second"),
                         default="props")
    parser.add_argument("--label", default=None,
                         help="operational label for --mode=moneyline (auto-derived from wall-clock hour if omitted)")
    args = parser.parse_args()

    if args.mode == "props":
        result = run_daily_pull()
    elif args.mode == "moneyline":
        result = run_moneyline_snapshot(args.label)
    elif args.mode == "sweep-first":
        result = run_targeted_prop_sweep("first")
    else:
        result = run_targeted_prop_sweep("second")

    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    _main()
