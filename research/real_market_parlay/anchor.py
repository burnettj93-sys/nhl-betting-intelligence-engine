"""
Anchor tickets: parlays built from legs that are likely to hit, priced at real DraftKings prices.

The objective: up to five +100-or-better parlays whose legs are well supported -- above all 2+ shots on goal for consistent high-volume shooters, combined with moneylines
and other supported markets -- ranked by estimated hit chance, each with value that holds at the real price. Empty slots are a correct answer.

What changed from the earlier selector, and why (each item is a fact about the evidence, not a tuned number):

  * The estimate for a shots leg is the player's own record, not the model alone. The calibrated skater model under-predicts consistent shooters: on the held-out 2023, 2024 and 2025
    seasons its 0.64-0.83 estimates were 3-8 points below what happened, while the share of his last 30 games in which the player reached the threshold (shrunk toward the league rate
    by 8 pseudo-games, research/product_models/live.shot_frequencies) was calibrated in every one of those seasons for 2+ shots at 0.60-1.00 and for 3+ shots at 0.60-0.70 (docs/validation/anchor_calibration.json; above 0.70 for 3+ there is too little data, so nothing higher is claimed).
    The estimate used is that frequency, capped at the model's number plus ANCHOR_CAP so one hot month cannot run away from the model. Both numbers, the sample (k of n games) and the
    price-implied probability are shown beside the estimate.
  * A moneyline is estimated at the market's own no-vig probability. No model here beats a DraftKings moneyline, so the honest estimate is the market's, which is worth -(the margin) by
    construction; such a leg never creates value, it can only make a ticket clear +100. It is used only when the Elo model does not disagree with the market by more than ELO_DISAGREE.
  * Points and anytime-goal legs are not used. They are speculative (no ticket needs them) and have no calibration evidence of the kind above.
  * The per-leg "must beat its own price" test is replaced by a ticket-level test at the price actually offered. A leg may cost up to LEG_MIN_EV (about a bookmaker's margin) so a
    moneyline can complete a ticket, but the ticket as a whole must show estimated value of at least MIN_ESTIMATED_EV and must stay non-negative with every leg lowered by LEG_PROBABILITY_MARGIN.
  * Up to MAX_LEGS legs, from different games, one leg per game: two legs from one game would need a joint price that DraftKings has not quoted and this code cannot honestly derive.
  * A longer ticket is kept only when no shorter ticket from its own legs already passes (an extra leg must be needed, never merely possible).

Nothing here is a validated edge. The estimates are calibrated on held-out player-games, not on sportsbook prices (none exist), and Ontario prices and settlement rules are unverified.
"""
from __future__ import annotations

import dataclasses
import math
from collections import defaultdict
from itertools import combinations, product

ANCHOR_CAP = 0.10            # an estimate never exceeds the model's probability by more than this (held-out gaps were 0.03-0.08)
MIN_SAMPLE_GAMES = 20        # games behind a shots estimate (the window is 30; an early-season player carries last season's games)
HIGH_LINE_CAP = 0.70         # 3+ shots and above: the record was calibrated for 0.60-0.70 estimates only (above that there are few player-games and 2024 over-stated it), so nothing higher is claimed
SHOTS_LEG_FLOOR = 0.60       # the calibrated band is 0.60-1.00; a leg below it is not "well supported"
MONEYLINE_LEG_FLOOR = 0.55
ELO_DISAGREE = 0.05
LEG_MIN_EV = -0.05           # a leg may give up at most about a bookmaker's margin at the price offered
MAX_LEGS = 5
MAX_GAMES_SEARCHED = 10
FRONTIER_PER_GAME = 4
MONEYLINE_SE = 0.03

SHOT_FAMILIES = ("PLAYER_SOG_ALTERNATE", "PLAYER_SOG")


@dataclasses.dataclass(frozen=True)
class Estimate:
    p: float
    se: float
    basis: str


def leg_estimate(leg) -> Estimate | None:
    """The hit-chance estimate for one leg, or None if the leg is not well supported."""
    if leg.market_family in SHOT_FAMILIES:
        if (leg.empirical_probability is None or leg.model_probability is None or (leg.empirical_games or 0) < MIN_SAMPLE_GAMES):
            return None
        p = min(leg.empirical_probability, leg.model_probability + ANCHOR_CAP, HIGH_LINE_CAP if (leg.threshold or 0) >= 3 else 1.0)
        if p < SHOTS_LEG_FLOOR:
            return None
        se = math.sqrt(p * (1.0 - p) / ((leg.empirical_games or 0) + 8))
        capped = (" (held to the model +10 points)" if leg.empirical_probability > leg.model_probability + ANCHOR_CAP
                  else " (held to 70%: higher estimates for 3+ shots are not supported)" if p < leg.empirical_probability else "")
        return Estimate(p, se, f"{leg.empirical_hits} of his last {leg.empirical_games} games with {leg.threshold}+ shots{capped}")
    if leg.market_family == "MONEYLINE":
        if leg.market_probability is None or not (0.0 < leg.market_probability < 1.0):
            return None
        if leg.market_probability < MONEYLINE_LEG_FLOOR or leg.conservative_probability < leg.market_probability - ELO_DISAGREE:
            return None
        return Estimate(leg.market_probability, MONEYLINE_SE, "the market's own no-vig probability")
    return None


def _decimal(leg) -> float:
    a = leg.american_price
    return 1.0 + (a / 100.0 if a > 0 else 100.0 / abs(a))


def prepare_pool(eligible_legs: list) -> list:
    """Eligible legs that are well supported, each carrying its hit-chance estimate as conservative_probability (so recording, settlement and display use the number stated)."""
    pool = []
    for leg in eligible_legs:
        est = leg_estimate(leg)
        if est is None:
            continue
        if est.p * _decimal(leg) - 1.0 < LEG_MIN_EV:
            continue
        pool.append(dataclasses.replace(leg, conservative_probability=round(est.p, 4),
                                        model_version=f"{leg.model_version}+anchor-last30" if leg.market_family in SHOT_FAMILIES else leg.model_version))
    return pool


def _frontier(legs: list) -> list:
    """Legs of one game not beaten on both hit chance and payout by another leg of that game."""
    out, best_dec = [], 0.0
    for leg in sorted(legs, key=lambda l: (-l.conservative_probability, -_decimal(l))):
        if _decimal(leg) > best_dec + 1e-9:
            out.append(leg)
            best_dec = _decimal(leg)
    return out[:FRONTIER_PER_GAME]


def midpoint_probability(leg) -> float:
    """Halfway between the leg's estimate and the probability its own price implies (before removing the bookmaker's margin, so the midpoint is, if anything, high-side generous to the price).
    A shots leg's estimate comes from the player's record; the market is an independent opinion that sees the opponent, the lineup and the news. A ticket whose value survives if the truth
    is halfway between them does not depend on the market being wrong. A moneyline's estimate already is the market's."""
    if leg.market_family in SHOT_FAMILIES:
        return (leg.conservative_probability + 1.0 / _decimal(leg)) / 2.0
    return leg.conservative_probability


def midpoint_ev(legs: list) -> float:
    p, dec = 1.0, 1.0
    for l in legs:
        p *= midpoint_probability(l)
        dec *= _decimal(l)
    return p * dec - 1.0


def uncertainty(legs: list) -> dict:
    """A plausible range for the ticket's hit chance: every leg moved one standard error together (the widest honest reading; the legs are in different games)."""
    low = high = 1.0
    for l in legs:
        est = leg_estimate(l) if l.empirical_probability is not None or l.market_probability is not None else None
        se = est.se if est else 0.05
        low *= max(l.conservative_probability - se, 0.0)
        high *= min(l.conservative_probability + se, 1.0)
    return {"low": round(low, 4), "high": round(high, 4)}


def qualifying(pool: list, policy, evaluate, veto) -> tuple[list, dict]:
    """Every cross-game ticket of 2..MAX_LEGS legs (one leg per game) that passes the +100 and value tests, minus tickets made redundant by a passing shorter one.
    `evaluate(legs)` builds the ParlayResult; `veto(combo, policy)` is the policy's own floor/value test. Also returns diagnostics for the empty-board explanation."""
    by_game: dict[str, list] = defaultdict(list)
    for leg in pool:
        by_game[leg.game_id].append(leg)
    fronts = {g: _frontier(ls) for g, ls in by_game.items()}
    games = sorted(fronts, key=lambda g: -max(l.conservative_probability * _decimal(l) for l in fronts[g]))[:MAX_GAMES_SEARCHED]
    diag = {"games_with_supported_legs": len(by_game), "supported_legs": len(pool), "combinations_checked": 0, "reaching_plus_100": 0,
            "best_reaching_plus_100": None}
    passing: dict[frozenset, object] = {}
    if len(games) < 2:
        return [], diag
    for size in range(2, min(MAX_LEGS, len(games)) + 1):
        for gs in combinations(games, size):
            for group in product(*(fronts[g] for g in gs)):
                diag["combinations_checked"] += 1
                dec = 1.0
                for l in group:
                    dec *= _decimal(l)
                if dec < policy.min_combined_decimal:
                    continue
                combo = evaluate(list(group))
                if combo is None:
                    continue
                diag["reaching_plus_100"] += 1
                best = diag["best_reaching_plus_100"]
                shaded = 1.0
                for l in combo.legs:
                    shaded *= max(l.conservative_probability - policy.value_shade, 0.0)
                cand = {"hit_probability": combo.joint_probability, "ev_estimated": combo.ev_estimated, "ev_after_shade": shaded * combo.combined_decimal - 1.0,
                        "price": combo.estimated_combo_price, "legs": [(l.participant_name, l.threshold, l.market_family, l.american_price) for l in combo.legs]}
                if best is None or cand["ev_after_shade"] > best["ev_after_shade"]:
                    diag["best_reaching_plus_100"] = cand
                if combo.ev_estimated < policy.min_estimated_ev or veto(combo, policy) is not None or combo.ev_conservative < 0.0:
                    continue
                keys = frozenset((l.game_id, l.participant_id, l.market_family, l.threshold, l.side) for l in combo.legs)
                if size > 2 and any(frozenset(sub) in passing for n in range(2, size) for sub in combinations(keys, n)):
                    continue
                passing[keys] = combo
    return list(passing.values()), diag


def leg_evidence(leg) -> dict:
    """What stands behind one leg's number, for display: the estimate, where it came from, the model's number and the price-implied probability."""
    est = leg_estimate(leg)
    d = _decimal(leg)
    out = {"estimate": leg.conservative_probability, "price_implied": round(1.0 / d, 4), "model": leg.model_probability,
           "basis": est.basis if est else None, "standard_error": round(est.se, 4) if est else None}
    if leg.market_family in SHOT_FAMILIES:
        out.update(kind="player_record", hits=leg.empirical_hits, games=leg.empirical_games, record_rate=leg.empirical_probability)
    else:
        out.update(kind="market", elo=leg.model_probability)
    return out


def describe(combo, policy) -> dict:
    """Ticket-level numbers an owner needs: the plausible range, the value at three stated assumptions, and a short plain-language rationale."""
    legs = combo.legs
    rng = uncertainty(legs)
    mid = midpoint_ev(legs)
    shaded = 1.0
    for l in legs:
        shaded *= max(l.conservative_probability - policy.value_shade, 0.0)
    shaded_ev = shaded * combo.combined_decimal - 1.0
    parts = []
    for l in legs:
        ev = leg_evidence(l)
        if ev["kind"] == "player_record":
            parts.append(f"{l.participant_name} {l.threshold}+ shots in {ev['hits']} of his last {ev['games']} games ({ev['record_rate']:.0%}; the model says {ev['model']:.0%}, the price implies {ev['price_implied']:.0%})")
        else:
            parts.append(f"{l.participant_name} to win (the market's own chance {ev['estimate']:.0%}; it adds price, not value)")
    text = (f"{'; '.join(parts)}. Together about {combo.joint_probability:.0%} (plausible range {rng['low']:.0%}-{rng['high']:.0%}) against {1.0 / combo.combined_decimal:.0%} implied by the prices. "
            f"Estimated value {combo.ev_estimated:+.0%}; {shaded_ev:+.0%} with every leg lowered {policy.value_shade * 100:.0f} points; {mid:+.0%} if the true chance is halfway between the record and the price. "
            "The record was calibrated on three held-out seasons, but DraftKings prices these legs lower, so the value depends on the record being the better guide; prices are US-feed quotes, not verified for Ontario.")
    return {"hit_low": rng["low"], "hit_high": rng["high"], "midpoint_ev": round(mid, 4), "ev_after_shade": round(shaded_ev, 4), "rationale": text}
