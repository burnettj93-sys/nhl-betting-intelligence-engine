"""
Minimal, credit-conscious client for The Odds API (https://the-odds-api.com/).
Uses the OFFICIAL documented v4 REST contract only -- no scraping of
DraftKings or the-odds-api.com's own website. Every method returns a
plain result object rather than raising past the caller (Part: "fail
clearly... do not crash dashboard"), and every method logs (via the
returned `credits_used`/`credits_remaining` header fields) exactly what
the request cost, so callers can be deliberate about spend.

Three endpoints used, in increasing cost order:
  - GET /v4/sports                                    (no odds -> free)
  - GET /v4/sports/{sport}/events                      (no odds -> free)
  - GET /v4/sports/{sport}/events/{event_id}/odds       (COSTS CREDITS --
    proportional to markets x regions requested; call this only for a
    specific event/market combination actually needed)
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
    try:
        resp = requests.get(f"{BASE_URL}{path}", params={**params, "apiKey": api_key},
                             timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        return ApiResult(ok=False, status_code=None, data=None,
                          error=f"network error: {exc.__class__.__name__}",
                          endpoint=path, retrieved_at_utc=retrieved_at)

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
