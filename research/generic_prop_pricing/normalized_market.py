"""
Preseason Operational Readiness Closure sprint (2026-08-30), Track 5 Part
31: a generic, INTERNAL representation of one sportsbook quote for one
side of one player/team prop market -- deliberately provider-agnostic.

This is NOT a claim about The Odds API's real payload shape (that shape
has never been observed for any of these markets -- see
PLAYER_SOG_LIVE_PRICING_REPORT.md Section G/H). It exists so the pricing/
decision core (evaluator.py) never has to know which provider a quote
came from, or whether it came from a provider at all -- provider-specific
parsing (Part 40) is a completely separate concern that PRODUCES one of
these objects, it never gets entangled with the probability/pricing math
that CONSUMES one."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NormalizedPropMarket:
    event_id: str | None
    sportsbook: str
    canonical_market_id: str
    threshold: int
    side: str                                 # "OVER" / "OVER_MILESTONE" / "UNDER"
    american_price: float
    opposing_side_price: float | None         # None => no two-sided market (Part 42)
    captured_at_utc: str
    provenance: str                           # e.g. "THE_ODDS_API" -- never invented/guessed
    player_id: str | None = None
    goalie_id: str | None = None
    team_id: str | None = None
    bookmaker_last_update_utc: str | None = None
    market_last_update_utc: str | None = None

    def has_two_sided_market(self) -> bool:
        return self.opposing_side_price is not None
