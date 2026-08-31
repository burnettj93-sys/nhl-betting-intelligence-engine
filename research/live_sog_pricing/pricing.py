"""
Model-vs-market pricing for one SOG quote (one side of one market): no-vig
market probability where a coherent two-sided market exists, model fair
price, conservative fair price, raw/conservative edge, raw/conservative
EV, maximum acceptable price, and a BET/WATCH/WAIT/PASS/DATA_UNAVAILABLE
decision.

Reuses pricing/odds_math.py's real, already-tested functions
(american_to_prob, prob_to_american, no_vig_two_way, expected_value,
max_acceptable_price, dynamic_max_staleness_minutes) UNCHANGED --
Part: "Reuse existing project thresholds where suitable" / "Do not
reuse the previously fixed incorrect formula" (i.e. do not
reimplement a formula pricing/odds_math.py already got right). Also
reuses config.py's real production thresholds
(MIN_CONSERVATIVE_EDGE, MIN_EV, EDGE_GREEN/LIGHT_GREEN/YELLOW,
ODDS_STALENESS_TIERS) rather than inventing new SOG-specific ones --
Part: "document SOG-specific thresholds rather than optimizing them
against today's markets" (there IS no live market to optimize against
this slice anyway -- see the report's Section A/G finding).

pricing/engine.py and pricing/decision.py themselves are NEVER imported
here -- both are nhl.db-coupled production modules this project's own
house rules forbid research code from touching (see
tests/test_live_sog_pricing.py::TestProductionModelUnchanged). Only the
pure, side-effect-free math in pricing/odds_math.py is reused.
"""
from __future__ import annotations

import math

import config
from pricing import odds_math

# BUG-204 (preseason product audit): a count model can legitimately
# return an EXACT 0.0 or 1.0 for a sufficiently extreme threshold/mu
# combination (e.g. P(6+ SOG) for a very low-volume player) -- real,
# reproduced during this audit. odds_math.prob_to_american() correctly
# refuses an exact 0/1 (no finite American price represents a literal
# impossibility/certainty), but that ValueError was propagating
# uncaught out of price_observation() and crashing the ENTIRE live
# refresh batch over ONE extreme observation, discarding every other
# already-priced observation in that run. Clipping to a tiny epsilon
# away from the boundary keeps the number honestly extreme (a very
# long or very short fair price) rather than mathematically undefined.
_PRICEABLE_PROB_EPS = 1e-6

# Preseason Operational Readiness Closure sprint (2026-08-30), Part 43 "market
# decision eligibility": only these 4 thresholds are actually validated per
# PLAYER_SOG_FOUNDATION_REPORT.md Section AI ("2+/3+/4+/5+, the standard
# sportsbook SOG lines") -- 1+ was never separately tested (near-universal
# base rate) and 6+/7+/8+ were never bootstrap-validated (real tail
# sparsity). Kept in sync with research/model_registry.py's PLAYER_SOG entry
# (both were corrected together this sprint; see
# tests/test_registry_cross_consistency.py for the guard against them
# drifting apart again). A quote at any other threshold still prices
# cleanly (the math is real and correct) but can never become a real
# decision -- see the ineligibility override in price_observation() below.
MODEL_VALIDATED_THRESHOLDS = (2, 3, 4, 5)


def _clip_to_priceable_range(p: float) -> float:
    return min(max(p, _PRICEABLE_PROB_EPS), 1.0 - _PRICEABLE_PROB_EPS)


def threshold_from_point(point: float) -> int:
    """Standard sportsbook SOG lines are half-integers (2.5, 3.5, ...).
    "Over 3.5" means "at least 4" -- the count-distribution threshold
    this project's P(SOG >= n) functions expect."""
    return math.floor(point) + 1


def model_prob_for_side(side: str, threshold: int, probs: dict[int, float]) -> float:
    """`probs`: {n: P(SOG>=n)} from research.player_sog.count_models.threshold_probabilities.
    OVER (or OVER_MILESTONE) -> P(SOG >= threshold). UNDER -> P(SOG <= threshold-1)
    = 1 - P(SOG >= threshold)."""
    p_over = probs.get(threshold)
    if p_over is None:
        raise ValueError(f"no model probability computed for threshold {threshold}")
    if side in ("OVER", "OVER_MILESTONE"):
        return p_over
    if side == "UNDER":
        return 1.0 - p_over
    raise ValueError(f"unrecognized side {side!r}")


def zone(conservative_edge: float) -> str:
    """Identical bucketing to pricing/engine.py::_zone -- reused as a
    documented policy choice, not re-derived."""
    if conservative_edge >= config.EDGE_GREEN:
        return "GREEN"
    if conservative_edge >= config.EDGE_LIGHT_GREEN:
        return "LIGHT GREEN"
    if conservative_edge >= config.EDGE_YELLOW:
        return "YELLOW"
    return "RED"


def decide(conservative_edge: float, ev: float, raw_edge: float, confidence: str,
           lineup_status: str) -> tuple[str, str]:
    """Returns (action, reason). Part: "confidence as a decision-quality
    gate... prefer conservative probability for pricing" -- confidence
    NEVER changes a probability number, it only caps how strong an
    action the SAME numbers are allowed to produce:

      1. Compute the base action from edge/EV alone (BET if both meet
         config thresholds; WATCH if there's a positive raw edge that
         doesn't clear the BET bar; PASS if no real edge at all).
      2. LOW confidence (or unconfirmed/PROJECTED lineup status, since no
         live confirmation source exists this slice -- see Part
         "LINEUP STATUS") downgrades a would-be BET or WATCH to WAIT --
         the opportunity may be real, but the data quality behind it
         isn't trusted enough to act on yet. A PASS stays PASS (there is
         nothing to "wait" on if there was no edge to begin with)."""
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


def price_observation(*, side: str, point: float | None, milestone_threshold: int | None,
                       price_american: float, opposing_price_american: float | None,
                       probs: dict[int, float], conservative_probs: dict[int, float],
                       confidence: str, lineup_status: str,
                       quote_age_minutes: float, hours_to_puck_drop: float) -> dict:
    """The single entry point tying together market probability, model
    probability, edge/EV, staleness, and the decision -- one call per
    quote side. Returns a flat dict matching the "CURRENT LIVE BOARD"
    column list in the required report."""
    threshold = milestone_threshold if milestone_threshold is not None else threshold_from_point(point)

    max_staleness = odds_math.dynamic_max_staleness_minutes(hours_to_puck_drop)
    if quote_age_minutes > max_staleness:
        return {"status": "DATA_UNAVAILABLE",
                "reason": f"quote age {quote_age_minutes:.1f} min exceeds the "
                          f"{max_staleness:.1f}-min policy window at {hours_to_puck_drop:.2f}h to puck drop",
                "threshold": threshold, "side": side}

    market_raw_prob = odds_math.american_to_prob(price_american)
    no_vig_prob = None
    no_vig_available = opposing_price_american is not None
    if no_vig_available:
        selection_no_vig, _ = odds_math.no_vig_two_way(price_american, opposing_price_american)
        no_vig_prob = selection_no_vig

    model_prob = _clip_to_priceable_range(model_prob_for_side(side, threshold, probs))
    conservative_prob = _clip_to_priceable_range(model_prob_for_side(side, threshold, conservative_probs))

    model_fair_price = odds_math.prob_to_american(model_prob)
    conservative_fair_price = odds_math.prob_to_american(conservative_prob)

    market_prob_for_edge = no_vig_prob if no_vig_available else market_raw_prob
    raw_edge = model_prob - market_prob_for_edge
    conservative_edge = conservative_prob - market_prob_for_edge

    raw_ev = odds_math.expected_value(model_prob, price_american)
    conservative_ev = odds_math.expected_value(conservative_prob, price_american)

    max_price = None
    if no_vig_available:
        max_price = odds_math.max_acceptable_price(
            conservative_prob, config.MIN_CONSERVATIVE_EDGE, opposing_price_american)

    action, reason = decide(conservative_edge, conservative_ev, raw_edge, confidence, lineup_status)
    if threshold not in MODEL_VALIDATED_THRESHOLDS:
        action, reason = "NOT_MODEL_VALIDATED", (
            f"threshold {threshold}+ is not among PLAYER_SOG's validated thresholds "
            f"{MODEL_VALIDATED_THRESHOLDS} (PLAYER_SOG_FOUNDATION_REPORT.md Section AI) -- "
            f"pricing math is real but this market can never become a real decision")

    return {
        "status": "PRICED", "side": side, "threshold": threshold, "point": point,
        "draftkings_price": price_american,
        "market_raw_probability": market_raw_prob,
        "market_no_vig_probability": no_vig_prob,
        "no_vig_available": no_vig_available,
        "model_probability": model_prob, "conservative_probability": conservative_prob,
        "model_fair_price": model_fair_price, "conservative_fair_price": conservative_fair_price,
        "raw_edge": raw_edge, "conservative_edge": conservative_edge,
        "raw_ev": raw_ev, "conservative_ev": conservative_ev,
        "maximum_acceptable_price": max_price,
        "zone": zone(conservative_edge),
        "confidence": confidence, "lineup_status": lineup_status,
        "quote_age_minutes": quote_age_minutes, "max_staleness_minutes": max_staleness,
        "action": action, "action_reason": reason,
    }
