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
from research.generic_prop_pricing.line_mapping import NonHalfPointLineError, line_to_threshold
from research.live_sog_pricing.market_parser import ALTERNATE_MARKET_KEY

MALFORMED_QUOTE_SHAPE = "MALFORMED_QUOTE_SHAPE"

# (sportsbook, canonical_market_id) pairs whose real payload shape has
# been observed, archived, and regression-tested against a REAL response
# -- see Part 41 / the completion sprint's Parts 9-17.
#
# SOG Contract Certification block (2026-09-29): two more entries added, both
# against REAL archived DraftKings payloads (never a new paid request this
# block -- both recovered from already-retained archives):
#   - (draftkings, PLAYER_SOG_ALTERNATE): player_shots_on_goal_alternate, real
#     payload 2026-09-29T12:15:00Z, FLA@CAR, 22 players -- see
#     tests/fixtures/draftkings_player_sog_alternate_real_payload.json and
#     tests/test_generic_prop_pricing.py::TestPlayerSogAlternateContractParity.
#   - (draftkings, ALTERNATE_TEAM_TOTAL): alternate_team_totals, real payload
#     2026-09-25T12:15:10Z, PIT@WSH, both teams, 24 outcomes -- see
#     tests/fixtures/draftkings_alternate_team_totals_real_payload.json and
#     tests/test_generic_prop_pricing.py::TestAlternateTeamTotalContractParity.
#
# Deliberately named PLAYER_SOG_ALTERNATE, never the bare "PLAYER_SOG" family
# id: the real evidence certifies ONLY the player_shots_on_goal_alternate
# ladder shape (every outcome Over-only, one row per point). The DIFFERENT
# player_shots_on_goal ("standard") shape -- a two-sided Over/Under pair at
# one line -- has NEVER been observed against a real payload (see
# research/live_sog_pricing/market_parser.py's own header: "NOT against a
# genuine captured non-empty NHL payload... zero DraftKings markets currently
# posted"). A bare "PLAYER_SOG" entry here would have been read as verified by
# every caller that keys on that family string regardless of which raw market
# key actually produced the quote -- concretely, operational/
# real_prop_orchestrator.py::quote_to_normalized_market() checks
# is_contract_verified(sportsbook, "PLAYER_SOG") with no shape awareness at
# all, and operational/prop_discovery.py::MARKET_TO_CONTRACT maps BOTH the
# standard and alternate raw keys to that same bare string -- so a bare entry
# would have silently promoted the live, already-scheduled standard-market
# path (and the prop-discovery credit-budget mode) to "verified" the instant
# DraftKings ever posts under the never-observed standard key, in direct
# violation of Part 16's "one observed payload shape never validates an
# unrelated family" rule. Discovered and fixed within this same block, before
# any commit, via the failing pre-existing test suite (tests/
# test_real_prop_orchestrator.py, tests/test_quota_moneyline_activation.py,
# tests/test_opening_day_readiness.py, tests/test_prop_contract_certification.py
# all correctly still expect bare "PLAYER_SOG" to be CONTRACT_NOT_VERIFIED).
#
# Every other family remains deliberately unverified -- one observed payload
# shape never validates an unrelated family (Part 16's instruction, unchanged).
VERIFIED_CONTRACTS: frozenset[tuple[str, str]] = frozenset({
    ("draftkings", "MONEYLINE"),
    ("draftkings", "PLAYER_SOG_ALTERNATE"),
    ("draftkings", "ALTERNATE_TEAM_TOTAL"),
})


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
    if canonical_market_id == "PLAYER_SOG_ALTERNATE":
        return _parse_player_sog_alternate_quote(raw_payload, sportsbook=sportsbook, event_id=event_id,
                                                  player_id=player_id)
    if canonical_market_id == "ALTERNATE_TEAM_TOTAL":
        return _parse_alternate_team_total_pair(raw_payload, sportsbook=sportsbook, event_id=event_id,
                                                 team_id=team_id)
    # Unreachable until VERIFIED_CONTRACTS gains another real entry via Part 41's
    # workflow -- deliberately left unimplemented rather than guessed.
    raise NotImplementedError(
        f"{sportsbook}/{canonical_market_id} was added to VERIFIED_CONTRACTS but no real parser "
        f"exists for it yet -- implement one against the actual observed payload, do not guess")


def _parse_player_sog_alternate_quote(quote: dict, *, sportsbook: str, event_id: str | None,
                                       player_id: str | None) -> dict:
    """SOG Contract Certification block (2026-09-29): `quote` is ONE already-extracted quote
    dict, exactly as research.live_sog_pricing.market_parser.parse_alternate_market() /
    group_alternate_ladder() produce -- this function does NOT re-parse a raw event payload
    or do identity resolution itself (that stays in market_parser.py/player_mapping.py,
    already built and tested); it is the narrow boundary that turns one already-identified
    quote into the canonical NormalizedPropMarket shape. Fails closed
    (MALFORMED_QUOTE_SHAPE) on a shape this contract has never actually observed (a genuine
    "milestone"-named outcome, or a non-half-point `point`) rather than guessing. Also fails
    closed if the quote did not actually come from the certified player_shots_on_goal_alternate
    market key -- the standard player_shots_on_goal key produces an outcome dict that is
    otherwise INDISTINGUISHABLE at this point (same shape="over_under", same side values), but
    its payload shape has never been observed for real and must never ride on this
    certification (see VERIFIED_CONTRACTS's own comment on this exact risk)."""
    if quote.get("market_key") != ALTERNATE_MARKET_KEY:
        return {"status": MALFORMED_QUOTE_SHAPE,
                "reason": f"quote market_key={quote.get('market_key')!r} is not the certified "
                          f"{ALTERNATE_MARKET_KEY!r} -- PLAYER_SOG_ALTERNATE only certifies that "
                          f"specific market key's payload shape, never the standard key's"}
    if quote.get("shape") != "over_under" or quote.get("side") not in ("OVER", "UNDER"):
        return {"status": MALFORMED_QUOTE_SHAPE,
                "reason": f"player_shots_on_goal_alternate quote has shape={quote.get('shape')!r} "
                          f"side={quote.get('side')!r} -- the only shape this contract has ever "
                          f"actually observed is a real half-point Over/Under line"}
    try:
        threshold = line_to_threshold(quote.get("point"))
    except NonHalfPointLineError as exc:
        return {"status": MALFORMED_QUOTE_SHAPE, "reason": str(exc)}
    market = NormalizedPropMarket(
        event_id=event_id, sportsbook=sportsbook, canonical_market_id="PLAYER_SOG_ALTERNATE", threshold=threshold,
        side=quote["side"], american_price=quote["price_american"], opposing_side_price=None,
        captured_at_utc=quote.get("market_last_update_utc"), provenance="THE_ODDS_API",
        player_id=player_id, market_last_update_utc=quote.get("market_last_update_utc"),
        bookmaker_last_update_utc=quote.get("bookmaker_last_update_utc"),
    )
    return {"status": "PARSED", "market": market}


def _parse_alternate_team_total_pair(pair: dict, *, sportsbook: str, event_id: str | None,
                                      team_id: str | None) -> dict:
    """`pair` is ONE {"over": quote_or_None, "under": quote_or_None} entry, exactly as
    research.generic_prop_pricing.team_totals_parser.group_alternate_team_total_ladder()
    produces for one (event, team, point) key."""
    over_q, under_q = pair.get("over"), pair.get("under")
    any_q = over_q or under_q
    if any_q is None:
        return {"status": MALFORMED_QUOTE_SHAPE, "reason": "alternate_team_totals pair has neither an Over nor an Under quote"}
    try:
        threshold = line_to_threshold(any_q.get("point"))
    except NonHalfPointLineError as exc:
        return {"status": MALFORMED_QUOTE_SHAPE, "reason": str(exc)}
    side = "OVER" if over_q else "UNDER"
    opposing_price = (under_q or {}).get("price_american") if over_q else (over_q or {}).get("price_american")
    market = NormalizedPropMarket(
        event_id=event_id, sportsbook=sportsbook, canonical_market_id="ALTERNATE_TEAM_TOTAL",
        threshold=threshold, side=side, american_price=any_q["price_american"],
        opposing_side_price=opposing_price, captured_at_utc=any_q.get("market_last_update_utc"),
        provenance="THE_ODDS_API", team_id=team_id, market_last_update_utc=any_q.get("market_last_update_utc"),
        bookmaker_last_update_utc=any_q.get("bookmaker_last_update_utc"),
    )
    return {"status": "PARSED", "market": market}


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
