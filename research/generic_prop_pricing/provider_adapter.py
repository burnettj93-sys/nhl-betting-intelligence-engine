"""
Preseason Operational Readiness Closure sprint (2026-08-30), Track 5 Part
40: the explicit provider-adapter boundary. This is where "we have never
observed a real payload for this market" gets enforced structurally,
rather than left to callers to remember.

Live DK / Paper Bankroll completion sprint (2026-08-31), Parts 9-17:
Part 41's first-real-payload workflow was actually completed for
MONEYLINE -- a real DraftKings h2h payload was captured via a live,
credit-metered Odds API probe (archived under
data/raw/the_odds_api/live/, see the completion sprint's own report for
the exact archive filenames, credit cost, and both real events
observed), inspected, and given a real sanitized fixture + regression
test (tests/test_generic_prop_pricing.py::TestMoneylineContractParity).
VERIFIED_CONTRACTS now has exactly that one entry -- every other family
(PLAYER SOG, GOALS, ASSISTS, POINTS, GOALIE SAVES, and DraftKings'
`spreads`/`totals` markets, which ALSO came back real but have no
corresponding internal model to compare against) remains unverified,
deliberately, per Part 16's "do not overgeneralize" instruction: one
observed payload shape does not validate an unrelated family.
"""
from __future__ import annotations

from research.generic_prop_pricing.evaluator import CONTRACT_NOT_VERIFIED
from research.generic_prop_pricing.normalized_market import NormalizedMoneylineMarket, NormalizedPropMarket
from research.live_sog_pricing.event_mapping import normalize_team_name

# (sportsbook, canonical_market_id) pairs whose real payload shape has
# been observed, archived, and regression-tested against a REAL response
# -- see Part 41 / the completion sprint's Parts 9-17.
VERIFIED_CONTRACTS: frozenset[tuple[str, str]] = frozenset({("draftkings", "MONEYLINE")})


def is_contract_verified(sportsbook: str, canonical_market_id: str) -> bool:
    return (sportsbook.lower(), canonical_market_id) in VERIFIED_CONTRACTS


def parse_the_odds_api_market(raw_payload: dict, *, sportsbook: str, canonical_market_id: str,
                               event_id: str | None, player_id: str | None = None,
                               team_id: str | None = None) -> dict:
    """Returns either {"status": "PARSED", "market": NormalizedPropMarket}
    or {"status": CONTRACT_NOT_VERIFIED, "reason": ...} -- NEVER attempts
    a best-effort parse of an unverified market's payload shape (Part 40's
    explicit instruction: "unverified market families should return
    CONTRACT_NOT_VERIFIED rather than parse guesses"). Only
    research/live_sog_pricing/market_parser.py has ever had its
    documented-contract assumption exercised against real market
    structure at all, and even that was never against a real posted
    price (Section G/H of PLAYER_SOG_LIVE_PRICING_REPORT.md) -- so this
    function, deliberately, currently parses NOTHING for real; it exists
    as the enforcement point for the day one real payload does arrive."""
    if not is_contract_verified(sportsbook, canonical_market_id):
        return {"status": CONTRACT_NOT_VERIFIED,
                "reason": f"{sportsbook}/{canonical_market_id} payload contract has never been "
                          f"observed live -- see FIRST_LIVE_NHL_DAY_CHECKLIST.md's first-real-payload "
                          f"workflow before adding it to VERIFIED_CONTRACTS"}
    # Unreachable until VERIFIED_CONTRACTS gains a real entry via Part 41's
    # workflow -- deliberately left unimplemented rather than guessed.
    raise NotImplementedError(
        f"{sportsbook}/{canonical_market_id} was added to VERIFIED_CONTRACTS but no real parser "
        f"exists for it yet -- implement one against the actual observed payload, do not guess")


def parse_the_odds_api_h2h_market(event_payload: dict, *, sportsbook: str = "draftkings") -> dict:
    """MONEYLINE's real, observed payload shape (GET /v4/sports/icehockey_nhl/
    events/{id}/odds with markets=h2h): the event-level object itself (not
    a single market object) -- {"id", "home_team", "away_team",
    "commence_time", "bookmakers": [{"key", "markets": [{"key": "h2h",
    "last_update", "outcomes": [{"name": <full team name>, "price":
    <American odds>}, ...]}]}]}. Captured live and archived under
    data/raw/the_odds_api/live/ (completion sprint Part 14) -- see
    tests/test_generic_prop_pricing.py::TestMoneylineContractParity for
    the sanitized real fixture this parser is regression-tested against.

    Returns {"status": "PARSED", "market": NormalizedMoneylineMarket} or
    {"status": CONTRACT_NOT_VERIFIED | "DATA_UNAVAILABLE", "reason": ...}.
    Never fabricates a missing side -- a one-sided or missing h2h market
    returns DATA_UNAVAILABLE, exactly like the prop-market parser's own
    Part 42 rule."""
    if not is_contract_verified(sportsbook, "MONEYLINE"):
        return {"status": CONTRACT_NOT_VERIFIED,
                "reason": f"{sportsbook}/MONEYLINE payload contract has never been observed live"}

    home_name = event_payload.get("home_team")
    away_name = event_payload.get("away_team")
    home_abbrev = normalize_team_name(home_name) if home_name else None
    away_abbrev = normalize_team_name(away_name) if away_name else None
    if home_abbrev is None or away_abbrev is None:
        return {"status": "DATA_UNAVAILABLE",
                "reason": f"unrecognized team name(s): home={home_name!r} away={away_name!r}"}

    bookmaker = next((bm for bm in event_payload.get("bookmakers", []) if bm.get("key") == sportsbook), None)
    if bookmaker is None:
        return {"status": "DATA_UNAVAILABLE", "reason": f"no {sportsbook} bookmaker block in this event"}

    h2h = next((m for m in bookmaker.get("markets", []) if m.get("key") == "h2h"), None)
    if h2h is None:
        return {"status": "DATA_UNAVAILABLE", "reason": f"no h2h market posted by {sportsbook} for this event"}

    outcomes = {o.get("name"): o.get("price") for o in h2h.get("outcomes", [])}
    home_price = outcomes.get(home_name)
    away_price = outcomes.get(away_name)
    if home_price is None or away_price is None:
        return {"status": "DATA_UNAVAILABLE",
                "reason": "h2h market present but missing one or both team outcomes -- never fabricating "
                           "the missing side"}

    return {"status": "PARSED", "market": NormalizedMoneylineMarket(
        event_id=event_payload.get("id"), sportsbook=sportsbook,
        home_team_abbrev=home_abbrev, away_team_abbrev=away_abbrev,
        home_price=float(home_price), away_price=float(away_price),
        captured_at_utc=h2h.get("last_update") or "", provenance="THE_ODDS_API",
        commence_time_utc=event_payload.get("commence_time"),
        bookmaker_last_update_utc=h2h.get("last_update"), market_last_update_utc=h2h.get("last_update"),
    )}
