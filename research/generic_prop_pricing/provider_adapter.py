"""
Preseason Operational Readiness Closure sprint (2026-08-30), Track 5 Part
40: the explicit provider-adapter boundary. This is where "we have never
observed a real payload for this market" gets enforced structurally,
rather than left to callers to remember.

VERIFIED_CONTRACTS is intentionally EMPTY right now -- true as of this
sprint (NHL_ENGINE_STATE_OF_THE_UNION_2026_08_30.md Part 33: zero of 142
canonical markets have dk_contract_verified=True). Part 41's first-real-
payload workflow is the ONLY sanctioned way an entry gets added here: a
real payload must be archived, inspected, and given a real regression
test fixture FIRST (see FIRST_LIVE_NHL_DAY_CHECKLIST.md's "first real
payload" section, added this sprint) -- this module refuses to guess a
shape in the meantime.
"""
from __future__ import annotations

from research.generic_prop_pricing.evaluator import CONTRACT_NOT_VERIFIED
from research.generic_prop_pricing.normalized_market import NormalizedPropMarket

# (sportsbook, canonical_market_id) pairs whose real payload shape has
# been observed, archived, and regression-tested against a REAL response
# -- see Part 41. Empty until that actually happens for something.
VERIFIED_CONTRACTS: frozenset[tuple[str, str]] = frozenset()


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
