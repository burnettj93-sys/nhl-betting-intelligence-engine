"""
Minimal, credit-conscious client for The Odds API (https://the-odds-api.com/).
Uses the OFFICIAL documented v4 REST contract only -- no scraping of
DraftKings or the-odds-api.com's own website. Every method returns a
plain result object rather than raising past the caller (Part: "fail
clearly... do not crash dashboard"), and every method logs (via the
returned `credits_used`/`credits_remaining` header fields) exactly what
the request cost, so callers can be deliberate about spend.

Four endpoints used, in increasing cost order:
  - GET /v4/sports                                    (no odds -> free)
  - GET /v4/sports/{sport}/events                      (no odds -> free)
  - GET /v4/sports/{sport}/odds                         (COSTS CREDITS --
    proportional to markets x regions requested, REGARDLESS of how many
    events it returns. One call here returns odds for every currently-
    listed event with that market posted. This is the correct endpoint
    for a league-wide snapshot -- e.g. MONEYLINE -- see get_sport_odds().)
  - GET /v4/sports/{sport}/events/{event_id}/odds       (COSTS CREDITS --
    proportional to markets x regions requested, PER EVENT. Only
    appropriate when a specific event/market combination is actually
    needed one at a time, e.g. event-specific player-prop sweeps -- see
    get_event_odds(). Live Odds API Cost Optimization Correction
    (2026-09-15): a real moneyline snapshot mistakenly looped this
    endpoint once per event (20 events -> 20 credits) instead of using
    the sport-level endpoint above (~1 credit for the same 20 events) --
    see ODDS_API_COST_OPTIMIZATION_CORRECTION_REPORT.md. Never use this
    endpoint in a loop to build a league-wide snapshot again.
These are the provider's own documented cost tiers -- this module does
not assume a specific number and instead reads the real
`x-requests-used` / `x-requests-remaining` response headers on every
call and surfaces them to the caller.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import requests

from research.live_sog_pricing.env_config import get_the_odds_api_key

BASE_URL = "https://api.the-odds-api.com/v4"
SPORT_KEY = "icehockey_nhl"
REQUEST_TIMEOUT_SECONDS = 15
# Quota + Moneyline Activation block (2026-09-25): ~20 % of scheduled moneyline runs failed with one-shot
# network errors. Bounded retry ONLY where a retry cannot double-charge: a connection that never reached the
# server (ConnectionError / ConnectTimeout) or an HTTP 502/503/504 (not billed). A ReadTimeout is NOT retried
# -- the server may already have counted the request. Never more than MAX_ATTEMPTS, exponential backoff.
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (1.0, 3.0)
RETRYABLE_STATUS = (502, 503, 504)
_sleep = time.sleep


@dataclass
class ApiResult:
    ok: bool
    status_code: int | None
    data: object | None
    error: str | None
    requests_used: str | None = None
    requests_remaining: str | None = None
    requests_last: str | None = None
    endpoint: str = ""
    retrieved_at_utc: str = ""


def _headers_of_interest(resp: requests.Response) -> dict:
    return {
        "requests_used": resp.headers.get("x-requests-used"),
        "requests_remaining": resp.headers.get("x-requests-remaining"),
        "requests_last": resp.headers.get("x-requests-last"),
    }


def _get(path: str, params: dict) -> ApiResult:
    """The API key is passed ONLY as a request query parameter value to
    `requests.get(params=...)` -- never string-formatted into a URL that
    could end up in a log line, and never returned by this function in
    `endpoint` (which stores the path only, never the query string)."""
    api_key = get_the_odds_api_key()
    retrieved_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if not api_key:
        return ApiResult(ok=False, status_code=None, data=None,
                          error="THE_ODDS_API_KEY not configured (no .env, no environment variable)",
                          endpoint=path, retrieved_at_utc=retrieved_at)
    resp = None
    for attempt in range(MAX_ATTEMPTS):
        last = attempt == MAX_ATTEMPTS - 1
        try:
            resp = requests.get(f"{BASE_URL}{path}", params={**params, "apiKey": api_key},
                                 timeout=REQUEST_TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            never_reached_server = isinstance(exc, requests.ConnectionError)   # includes ConnectTimeout
            if last or not never_reached_server:
                return ApiResult(ok=False, status_code=None, data=None,
                                  error=f"network error: {exc.__class__.__name__}",
                                  endpoint=path, retrieved_at_utc=retrieved_at)
            _sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)])
            continue
        if resp.status_code in RETRYABLE_STATUS and not last:
            _sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)])
            continue
        break

    headers = _headers_of_interest(resp)
    if resp.status_code != 200:
        # Never include the raw response body in the error string -- some
        # providers echo the request (including query params) back in
        # error bodies; keep this to status code + a short reason only.
        reason = {401: "unauthorized (invalid or missing API key)",
                  429: "rate limited / out of credits",
                  404: "not found"}.get(resp.status_code, f"HTTP {resp.status_code}")
        return ApiResult(ok=False, status_code=resp.status_code, data=None, error=reason,
                          endpoint=path, retrieved_at_utc=retrieved_at, **headers)
    try:
        data = resp.json()
    except ValueError:
        return ApiResult(ok=False, status_code=resp.status_code, data=None,
                          error="malformed JSON in response", endpoint=path,
                          retrieved_at_utc=retrieved_at, **headers)
    return ApiResult(ok=True, status_code=200, data=data, error=None,
                      endpoint=path, retrieved_at_utc=retrieved_at, **headers)


def get_sports() -> ApiResult:
    """GET /v4/sports -- confirms auth and whether icehockey_nhl is
    currently an active sport key. Documented as not consuming odds
    credits; this module still reads and surfaces the real headers
    rather than assuming that."""
    return _get("/sports", {})


def get_nhl_events() -> ApiResult:
    """GET /v4/sports/icehockey_nhl/events -- upcoming/live event IDs,
    team names, commence times. No markets/odds requested, so this is
    the correct way to discover events BEFORE spending credits on the
    per-event odds endpoint."""
    return _get(f"/sports/{SPORT_KEY}/events", {})


def get_sport_odds(markets: str = "h2h", bookmakers: str = "draftkings",
                    odds_format: str = "american", date_format: str = "iso") -> ApiResult:
    """GET /v4/sports/icehockey_nhl/odds -- THE SPORT-LEVEL, CREDIT-COSTING
    CALL. Per the provider's own documented cost model, this endpoint
    charges (markets requested) x (regions requested) REGARDLESS of how
    many events it returns -- unlike get_event_odds() below, querying
    the entire league's currently-listed slate in one call costs the
    same as querying a single event. `bookmakers=draftkings` (not
    `regions=us`) is used for the same reason as get_event_odds(): the
    provider's documented bookmaker filter is more precise than a
    whole-region pull. Use this for any league-wide snapshot (e.g.
    MONEYLINE) -- NEVER loop get_event_odds() once per event to build
    one; that multiplies real cost by event count for the exact same
    data this single call already returns. `.data` is a list of event
    objects, each shaped identically to get_event_odds()'s single-event
    `.data` (so the same per-event parser, e.g.
    provider_adapter.parse_the_odds_api_h2h_market, works unchanged on
    each item)."""
    return _get(f"/sports/{SPORT_KEY}/odds",
                {"markets": markets, "bookmakers": bookmakers,
                 "oddsFormat": odds_format, "dateFormat": date_format})


def get_event_odds(event_id: str, markets: str = "player_shots_on_goal,player_shots_on_goal_alternate",
                    bookmakers: str = "draftkings", odds_format: str = "american",
                    date_format: str = "iso") -> ApiResult:
    """GET /v4/sports/icehockey_nhl/events/{event_id}/odds -- THE
    CREDIT-COSTING CALL. `bookmakers=draftkings` (not `regions=us`) is
    used deliberately: the provider's own docs support filtering by an
    explicit bookmaker key, which is more precise than a whole-region
    pull and avoids paying for/parsing books this project doesn't use
    as its reference book (Part: "DraftKings first")."""
    return _get(f"/sports/{SPORT_KEY}/events/{event_id}/odds",
                {"markets": markets, "bookmakers": bookmakers,
                 "oddsFormat": odds_format, "dateFormat": date_format})
