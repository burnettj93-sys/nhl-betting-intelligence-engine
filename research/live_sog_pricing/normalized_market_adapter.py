"""
Live SOG + Saves Production Certification block (2026-09-24): a thin
adapter from research/live_sog_pricing/market_parser.py's own quote dict
shape (built for the SOG-specific price_observation() pipeline) into
research/generic_prop_pricing/normalized_market.py::NormalizedPropMarket
-- the shape research/generic_prop_pricing/evaluator.py::evaluate_prop()
(the market-family-agnostic pricing/decision core) actually consumes.

Writes no parsing logic and no contract policy of its own: the quote's
fields come entirely from market_parser.py's already-tested parser, and
whether the contract counts as verified comes entirely from
research/generic_prop_pricing/provider_adapter.py::is_contract_verified()
-- this module never hardcodes a verified flag.
"""
from __future__ import annotations

from research.generic_prop_pricing.normalized_market import NormalizedPropMarket
from research.generic_prop_pricing.provider_adapter import is_contract_verified


def quote_to_normalized_market(quote: dict, *, market_family: str, canonical_market_id: str,
                                threshold: int, side: str, opposing_price: float | None,
                                player_id: str | None = None, goalie_id: str | None = None,
                                sportsbook: str = "draftkings") -> tuple[NormalizedPropMarket, bool]:
    """Returns (market, provider_contract_verified). `quote` is one
    market_parser.py-produced dict (either side of a standard Over/Under
    pair, or a milestone quote) -- never a demo/simulated observation.
    `market_family` (e.g. "PLAYER_SOG", "GOALIE_SAVES") is the bare key
    provider_adapter.VERIFIED_CONTRACTS is keyed on -- kept separate from
    `canonical_market_id` (which may be threshold-specific, e.g.
    "PLAYER_SOG_4PLUS", purely descriptive on the market object itself)."""
    market = NormalizedPropMarket(
        event_id=quote.get("provider_event_id"), sportsbook=sportsbook,
        canonical_market_id=canonical_market_id, threshold=threshold, side=side,
        american_price=quote["price_american"], opposing_side_price=opposing_price,
        captured_at_utc=quote.get("bookmaker_last_update_utc") or "", provenance="THE_ODDS_API",
        player_id=player_id, goalie_id=goalie_id,
        bookmaker_last_update_utc=quote.get("bookmaker_last_update_utc"),
        market_last_update_utc=quote.get("market_last_update_utc"),
    )
    verified = is_contract_verified(sportsbook, market_family)
    return market, verified
