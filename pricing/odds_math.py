"""
American odds <-> probability conversion, no-vig stripping, EV, Kelly.
Spec refs: Technical Agent Specification sections 40, 41, 48, 49, 59.
"""
from __future__ import annotations

import datetime as dt

import config


class InvalidOddsError(ValueError):
    pass


def validate_american_odds(odds: float) -> None:
    """American odds are only ever <= -100 or >= +100. 0, None, NaN, and
    anything strictly between -100 and 100 (exclusive) are impossible
    prices and must be rejected rather than silently converted."""
    if odds is None:
        raise InvalidOddsError("odds is None")
    try:
        odds = float(odds)
    except (TypeError, ValueError):
        raise InvalidOddsError(f"odds is not a number: {odds!r}")
    if odds != odds:  # NaN
        raise InvalidOddsError("odds is NaN")
    if -100.0 < odds < 100.0:
        raise InvalidOddsError(f"impossible American odds: {odds}")


def american_to_prob(odds: float) -> float:
    """Raw implied probability from an American price. Spec sec.40."""
    validate_american_odds(odds)
    if odds > 0:
        return 100.0 / (odds + 100.0)
    return abs(odds) / (abs(odds) + 100.0)


def american_to_decimal(odds: float) -> float:
    validate_american_odds(odds)
    if odds > 0:
        return 1.0 + odds / 100.0
    return 1.0 + 100.0 / abs(odds)


def prob_to_american(p: float) -> float:
    """Fair American price for a model probability. Spec sec.48."""
    if not 0.0 < p < 1.0:
        raise ValueError("probability must be strictly between 0 and 1")
    if p > 0.5:
        return -100.0 * p / (1.0 - p)
    return 100.0 * (1.0 - p) / p


def no_vig_two_way(price_a: float, price_b: float) -> tuple[float, float]:
    """Strip the vig from a two-sided market. Spec sec.41."""
    raw_a = american_to_prob(price_a)
    raw_b = american_to_prob(price_b)
    total = raw_a + raw_b
    return raw_a / total, raw_b / total


def expected_value(model_prob: float, american_odds: float) -> float:
    """EV as a fraction of stake at the given price. Spec sec.49."""
    return model_prob * american_to_decimal(american_odds) - 1.0


def kelly_fraction(model_prob: float, american_odds: float) -> float:
    """
    Full Kelly fraction of bankroll. The spec (sec.59) is explicit: never
    default to full Kelly — the caller applies config.KELLY_FRACTION_MULTIPLIER
    on top of this.
    """
    b = american_to_decimal(american_odds) - 1.0
    p = model_prob
    q = 1.0 - p
    if b <= 0:
        return 0.0
    f = (b * p - q) / b
    return max(f, 0.0)


def max_acceptable_price(conservative_prob: float, min_edge: float,
                          opponent_price: float) -> float | None:
    """
    The worst American price on the TARGET side that still clears
    `min_edge` -- a probability-POINT edge, per
    `conservative_edge = conservative_prob - market_no_vig_probability >=
    config.MIN_CONSERVATIVE_EDGE` -- against the exact SAME two-sided
    no-vig probability the BET/PASS decision itself uses
    (`no_vig_two_way`), not an approximation from the target's own raw
    implied probability alone. Spec sec.51 (v2.1.1a spec item 3 fix: the
    prior implementation solved `conservative_prob / (1 + min_edge)`,
    which is a multiplicative-edge breakeven inconsistent with the
    engine's additive probability-point edge definition, and ignored the
    opponent price entirely).

    Because the engine's edge is measured against a TWO-SIDED no-vig
    probability, the target side's own no-vig probability -- and
    therefore its breakeven price -- depends on the CURRENT opponent
    price too; there is no opponent-independent answer. `opponent_price`
    is held fixed (the opponent's price as currently known) while solving
    for the worst target price P such that
    `no_vig_two_way(P, opponent_price)[0] == conservative_prob - min_edge`
    exactly -- any target price at least that good (a longer/more
    favorable price) clears min_edge; anything worse than it does not.

    Returns None (rather than a misleading number) when no valid American
    price can satisfy the requested edge at all -- e.g. `min_edge >=
    conservative_prob` (no price, however long, could ever clear it), or
    the opponent price is itself so extreme that no finite American price
    solves the two-way system.
    """
    target_max_no_vig = conservative_prob - min_edge
    if not 0.0 < target_max_no_vig < 1.0:
        return None
    raw_opponent = american_to_prob(opponent_price)
    denom = 1.0 - target_max_no_vig
    if denom <= 0.0:
        return None
    raw_target = target_max_no_vig * raw_opponent / denom
    if not 0.0 < raw_target < 1.0:
        return None
    return prob_to_american(raw_target)


def hours_between(earlier_iso: str, later_iso: str) -> float:
    """later - earlier, in hours (can be negative if later is actually
    before earlier). Pure function, no wall-clock involved -- both
    timestamps are supplied by the caller."""
    a = dt.datetime.fromisoformat(earlier_iso)
    b = dt.datetime.fromisoformat(later_iso)
    return (b - a).total_seconds() / 3600.0


def dynamic_max_staleness_minutes(hours_to_puck_drop: float) -> float:
    """v2.1 (spec item 15): the maximum allowed DraftKings quote age
    SCALES with how close we are to puck drop -- a quote that's fine a
    day out is dangerously stale 10 minutes before puck drop, and a
    single static window (config.MAX_ODDS_STALENESS_MINUTES) can't
    express that. Looks up config.ODDS_STALENESS_TIERS, a list of
    (lower_bound_hours, max_age_minutes) pairs checked top-down; the
    first tier whose lower bound hours_to_puck_drop meets or exceeds
    wins. Pure function -- no DB, no wall-clock; the caller supplies
    hours_to_puck_drop (see pricing/engine.py)."""
    tiers = config.ODDS_STALENESS_TIERS
    for lower_bound_hours, max_age_minutes in tiers:
        if hours_to_puck_drop >= lower_bound_hours:
            return max_age_minutes
    return tiers[-1][1]
