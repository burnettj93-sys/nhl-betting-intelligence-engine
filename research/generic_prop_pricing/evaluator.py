"""
Preseason Operational Readiness Closure sprint (2026-08-30), Track 5 Parts
30/32: the shared, market-family-agnostic pricing/decision core.

Part 30's three concerns, kept genuinely separate:
    A. MODEL PROBABILITY   -- computed elsewhere (research.player_sog,
                               research.player_goals, etc.), passed in
                               here as a plain float. This module never
                               fits or re-derives one.
    B. NORMALIZED MARKET   -- a NormalizedPropMarket (or None if no real
                               market exists yet). Provider-specific
                               parsing (Part 40) produces this; it is
                               never entangled with the code below.
    C. PRICING / DECISION  -- this module. Reuses pricing/odds_math.py's
                               real, unmodified functions -- the SAME
                               ones research/live_sog_pricing/pricing.py
                               already uses -- never a second,
                               reimplemented formula.

`evaluate_prop()` is a straight generalization of research/live_sog_
pricing/pricing.py::price_observation() (which remains untouched, and is
proven to reproduce this evaluator's output exactly for its own validated
thresholds -- see tests/test_generic_prop_pricing.py::TestSOGParity).
"""
from __future__ import annotations

import config
from pricing import odds_math
from research.generic_prop_pricing.normalized_market import NormalizedPropMarket

_PRICEABLE_PROB_EPS = 1e-6

DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
CONTRACT_NOT_VERIFIED = "CONTRACT_NOT_VERIFIED"
NOT_MODEL_VALIDATED = "NOT_MODEL_VALIDATED"
PRICED = "PRICED"


def _clip_to_priceable_range(p: float) -> float:
    return min(max(p, _PRICEABLE_PROB_EPS), 1.0 - _PRICEABLE_PROB_EPS)


def model_prob_for_side(side: str, threshold: int, probs: dict[int, float]) -> float:
    p_over = probs.get(threshold)
    if p_over is None:
        raise ValueError(f"no model probability computed for threshold {threshold}")
    if side in ("OVER", "OVER_MILESTONE"):
        return p_over
    if side == "UNDER":
        return 1.0 - p_over
    raise ValueError(f"unrecognized side {side!r}")


def zone(conservative_edge: float) -> str:
    if conservative_edge >= config.EDGE_GREEN:
        return "GREEN"
    if conservative_edge >= config.EDGE_LIGHT_GREEN:
        return "LIGHT GREEN"
    if conservative_edge >= config.EDGE_YELLOW:
        return "YELLOW"
    return "RED"


def decide(conservative_edge: float, ev: float, raw_edge: float, confidence: str,
           lineup_status: str) -> tuple[str, str]:
    """Identical policy to research/live_sog_pricing/pricing.py::decide()
    -- reused as the one shared decision rule every prop family goes
    through, never re-derived per family."""
    meets_edge = conservative_edge >= config.MIN_CONSERVATIVE_EDGE
    meets_ev = ev >= config.MIN_EV
    if meets_edge and meets_ev:
        base, base_reason = "BET", ""
    elif raw_edge > 0:
        base = "WATCH"
        base_reason = (f"raw edge {raw_edge:+.1%} positive but conservative edge "
                        f"{conservative_edge:+.1%} or EV {ev:+.1%} below the BET bar "
                        f"(edge>= {config.MIN_CONSERVATIVE_EDGE:+.1%}, EV>= {config.MIN_EV:+.1%})")
    else:
        base = "PASS"
        base_reason = f"no positive raw edge ({raw_edge:+.1%})"

    quality_ok = confidence != "LOW"
    if not quality_ok and base in ("BET", "WATCH"):
        return "WAIT", (f"would otherwise be {base} ({base_reason or 'edge/EV clear'}), but model "
                         f"confidence is LOW -- data quality insufficient to act on this edge yet")
    return base, base_reason


def evaluate_prop(*, market_family: str, model_validated_thresholds: tuple[int, ...],
                   threshold: int, side: str, probs: dict[int, float],
                   conservative_probs: dict[int, float], confidence: str, lineup_status: str,
                   market: NormalizedPropMarket | None, provider_contract_verified: bool,
                   quote_age_minutes: float | None = None, hours_to_puck_drop: float | None = None) -> dict:
    """The single shared entry point for every prop family (Part 32).
    Part 43's full decision-eligibility checklist, applied in order:
      1. model threshold eligible       -> NOT_MODEL_VALIDATED otherwise
      2. a market object exists at all  -> DATA_UNAVAILABLE otherwise
      3. provider contract verified     -> CONTRACT_NOT_VERIFIED otherwise
      4. price fresh (staleness policy) -> DATA_UNAVAILABLE otherwise
      5. two-sided no-vig where claimed -> never fakes the opposite side
         (Part 42) -- a one-sided market prices with no_vig_available=False
         and no_vig_probability=None, exactly like research/live_sog_
         pricing/pricing.py already does; it does not block pricing
         (max_acceptable_price/edge are unavailable, not the whole result).
      6. event not started              -> caller's responsibility
         (this function has no clock of its own; the prospective ledger's
         own exact-start guard is the enforcement point, Part 27).
      7. decision policy permits        -> decide() below, same as SOG.
    """
    if threshold not in model_validated_thresholds:
        return {"status": NOT_MODEL_VALIDATED, "market_family": market_family, "threshold": threshold,
                "reason": f"{market_family} threshold {threshold} is not among its validated "
                          f"thresholds {model_validated_thresholds}"}

    if market is None:
        return {"status": DATA_UNAVAILABLE, "market_family": market_family, "threshold": threshold,
                "reason": "no real sportsbook market exists for this prediction yet"}

    if not provider_contract_verified:
        return {"status": CONTRACT_NOT_VERIFIED, "market_family": market_family, "threshold": threshold,
                "reason": f"{market.sportsbook} payload contract for {market.canonical_market_id} has "
                          f"never been observed live -- pricing math is real but this market can never "
                          f"become a real decision until Part 41's first-real-payload workflow completes"}

    if quote_age_minutes is not None and hours_to_puck_drop is not None:
        max_staleness = odds_math.dynamic_max_staleness_minutes(hours_to_puck_drop)
        if quote_age_minutes > max_staleness:
            return {"status": DATA_UNAVAILABLE, "market_family": market_family, "threshold": threshold,
                    "reason": f"quote age {quote_age_minutes:.1f} min exceeds the {max_staleness:.1f}-min "
                              f"policy window at {hours_to_puck_drop:.2f}h to puck drop"}

    market_raw_prob = odds_math.american_to_prob(market.american_price)
    no_vig_available = market.has_two_sided_market()
    no_vig_prob = None
    if no_vig_available:
        selection_no_vig, _ = odds_math.no_vig_two_way(market.american_price, market.opposing_side_price)
        no_vig_prob = selection_no_vig

    model_prob = _clip_to_priceable_range(model_prob_for_side(side, threshold, probs))
    conservative_prob = _clip_to_priceable_range(model_prob_for_side(side, threshold, conservative_probs))

    model_fair_price = odds_math.prob_to_american(model_prob)
    conservative_fair_price = odds_math.prob_to_american(conservative_prob)

    # Part 42: never fake the opposite side. Without a real two-sided
    # market, edge/EV/max-buy are NOT_AVAILABLE, not silently derived
    # from the raw single-sided implied probability as if it were vig-free.
    if no_vig_available:
        market_prob_for_edge = no_vig_prob
        raw_edge = model_prob - market_prob_for_edge
        conservative_edge = conservative_prob - market_prob_for_edge
        raw_ev = odds_math.expected_value(model_prob, market.american_price)
        conservative_ev = odds_math.expected_value(conservative_prob, market.american_price)
        max_price = odds_math.max_acceptable_price(
            conservative_prob, config.MIN_CONSERVATIVE_EDGE, market.opposing_side_price)
        action, reason = decide(conservative_edge, conservative_ev, raw_edge, confidence, lineup_status)
        zone_label = zone(conservative_edge)
    else:
        raw_edge = conservative_edge = raw_ev = conservative_ev = max_price = None
        action, reason = "NOT_AVAILABLE", "no two-sided market -- no-vig probability cannot be computed"
        zone_label = "NOT_AVAILABLE"

    return {
        "status": PRICED, "market_family": market_family, "side": side, "threshold": threshold,
        "sportsbook_price": market.american_price, "market_raw_probability": market_raw_prob,
        "market_no_vig_probability": no_vig_prob, "no_vig_available": no_vig_available,
        "model_probability": model_prob, "conservative_probability": conservative_prob,
        "model_fair_price": model_fair_price, "conservative_fair_price": conservative_fair_price,
        "raw_edge": raw_edge, "conservative_edge": conservative_edge,
        "raw_ev": raw_ev, "conservative_ev": conservative_ev, "maximum_acceptable_price": max_price,
        "zone": zone_label, "confidence": confidence, "lineup_status": lineup_status,
        "action": action, "action_reason": reason,
    }


def market_decision_eligible(*, model_threshold_eligible: bool, identity_resolved: bool,
                              starter_active_status_satisfied: bool, price_fresh: bool,
                              two_sided_no_vig_possible: bool, provider_contract_verified: bool,
                              event_not_started: bool, decision_policy_permits: bool) -> bool:
    """Part 43's checklist as one explicit, testable function -- a market
    is decision-eligible only if EVERY one of these is true. Deliberately
    a pure boolean function (no side effects, no I/O) so each condition
    can be unit-tested independently."""
    return all([model_threshold_eligible, identity_resolved, starter_active_status_satisfied,
                price_fresh, two_sided_no_vig_possible, provider_contract_verified,
                event_not_started, decision_policy_permits])
