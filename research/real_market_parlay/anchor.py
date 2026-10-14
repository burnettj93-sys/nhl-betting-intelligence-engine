"""
Anchor tickets: parlays built from legs that are likely to hit, priced at real DraftKings prices.

The objective: up to five +100-or-better parlays whose legs are well supported -- above all 2+ shots on goal for consistent high-volume shooters, combined with moneylines
and other supported markets -- ranked by estimated hit chance, each with value that holds at the real price. Empty slots are a correct answer.

What changed from the earlier selector, and why (each item is a fact about the evidence, not a tuned number):

  * The estimate for a shots leg is the player's own record, not the model alone. The record is the share of his last 30 games with at least the line's shots, shrunk toward the league rate by 8
    pseudo-games (research/product_models/live.shot_frequencies). On the project's walk-forward evaluation (deploy/anchor_calibration.py -> docs/validation/anchor_calibration.json, the live model's own
    configuration and Platt parameters) the calibrated model under-predicted consistent shooters by about 2-4 points at 0.70+ for 2+ shots, and the exact estimate used here -- the record, capped at the
    model plus ANCHOR_CAP and at HIGH_LINE_CAP for 3+ shots -- was within 2 points of what happened on 2025, the only season out of sample for the model (2023 and 2024 are model-fitting seasons and are
    reported as such). Three numbers are always kept apart: the recent hit rate (k of n games), the model's probability and the final estimate, with the price-implied probability beside them.
  * A moneyline is estimated at the market's own no-vig probability. No model here beats a DraftKings moneyline, so the honest estimate is the market's, which is worth -(the margin) by
    construction; such a leg never creates value, it can only make a ticket clear +100. It is used only when the Elo model also has that side as the favourite (Elo's probabilities are compressed toward 50%, so its size is not compared to the price).
  * Points and anytime-goal legs are not used. They are speculative (no ticket needs them) and have no calibration evidence of the kind above.
  * The per-leg "must beat its own price" test is replaced by a ticket-level test at the price actually offered. A leg may cost up to LEG_MIN_EV (about a bookmaker's margin) so a
    moneyline can complete a ticket, but the ticket as a whole must show estimated value of at least MIN_ESTIMATED_EV and must stay non-negative with every leg lowered by LEG_PROBABILITY_MARGIN.
  * Up to MAX_LEGS legs, from different games, one leg per game: two legs from one game would need a joint price that DraftKings has not quoted and this code cannot honestly derive.
  * A longer ticket is kept only when no shorter ticket from its own legs already passes (an extra leg must be needed, never merely possible).

Nothing here is a validated edge. The estimates were checked for calibration on walk-forward player-games (out of sample for the model only in 2025), not on sportsbook prices (none exist),
not on the subset where a sportsbook prices a leg cheaper than the estimate, and Ontario prices and settlement rules are unverified.
"""
from __future__ import annotations

import dataclasses
import math
from collections import defaultdict
from itertools import combinations

ANCHOR_CAP_V1 = 0.10         # the cap of policy anchor-tickets-2026-10-11 (digest c236c526e00b9f04): on legs where it bound, the estimate over-stated the result by about 8 points in every validation season. Superseded; kept so history can be reproduced
ANCHOR_CAP = 0.02            # an estimate never exceeds the model's calibrated probability by more than this (policy anchor-tickets-2026-10-13; chosen on 2024, evaluated on 2025: docs/PROPOSAL_ANCHOR_CAP.md)
V1_VERSION_TAG = "+anchor-last30"            # on the model_version of every leg and ticket estimated under the +0.10 cap
V2_VERSION_TAG = "+anchor-last30-cap02"      # under the +0.02 cap
MIN_SAMPLE_GAMES = 20        # games behind a shots estimate (the window is 30; an early-season player carries last season's games)
HIGH_LINE_CAP = 0.70         # 3+ shots and above: the final estimate was calibrated at 0.60-0.70 only (above that there are few player-games and 2024 over-stated it), so nothing higher is claimed
SHOTS_LEG_FLOOR = 0.60       # "well supported" starts here; below it the record is not a strong case
MONEYLINE_LEG_FLOOR = 0.55
LEG_MIN_EV = -0.05           # a leg may give up at most about a bookmaker's margin at the price offered
MAX_LEGS = 5
MONEYLINE_SE = 0.03

SHOT_FAMILIES = ("PLAYER_SOG_ALTERNATE", "PLAYER_SOG")


@dataclasses.dataclass(frozen=True)
class Estimate:
    p: float
    se: float
    basis: str


def cap_for(policy) -> float:
    """The shots cap a policy states (None = the original +0.10, for policies written before the field existed)."""
    cap = getattr(policy, "anchor_cap", None)
    return ANCHOR_CAP_V1 if cap is None else cap


def version_tag(cap: float) -> str:
    return V1_VERSION_TAG if abs(cap - ANCHOR_CAP_V1) < 1e-9 else f"{V1_VERSION_TAG}-cap{round(cap * 100):02d}"


def estimate_rule_note(model_version: str | None) -> str | None:
    """For a recorded ticket or leg: a plain sentence when its estimate came from the superseded +0.10 cap, else None. Read from the model_version stored with the entry; no stored number is changed."""
    mv = model_version or ""
    if V1_VERSION_TAG in mv and V2_VERSION_TAG not in mv:
        return ("Estimated under the earlier shots rule (record held to the model + 10 points), superseded on 2026-10-13 because it over-stated such legs by about 8 points in the chronological "
                "validation. The entry numbers shown are the ones recorded and are unchanged.")
    return None


def shots_final_estimate(model_p: float | None, record_p: float | None, threshold: int | None, cap: float | None = None) -> float | None:
    """The final estimate for a shots line from the model's probability and the shrunk last-30 record: the record, held to the model + the cap (ANCHOR_CAP unless a policy's cap is passed) and to
    HIGH_LINE_CAP for 3+ shots. One definition, used by the ticket legs and by the Paper Parlay Builder's line display, so a card never shows a different estimate from the one a ticket would use.
    None without both inputs."""
    if model_p is None or record_p is None:
        return None
    return min(record_p, model_p + (ANCHOR_CAP if cap is None else cap), HIGH_LINE_CAP if (threshold or 0) >= 3 else 1.0)


def leg_estimate(leg, cap: float | None = None) -> Estimate | None:
    """The hit-chance estimate for one leg, or None if the leg is not well supported. `cap` is the policy's shots cap (ANCHOR_CAP when omitted)."""
    cap = ANCHOR_CAP if cap is None else cap
    if leg.market_family in SHOT_FAMILIES:
        if (leg.empirical_probability is None or leg.model_probability is None or (leg.empirical_games or 0) < MIN_SAMPLE_GAMES):
            return None
        p = shots_final_estimate(leg.model_probability, leg.empirical_probability, leg.threshold, cap)
        if p < SHOTS_LEG_FLOOR:
            return None
        se = math.sqrt(p * (1.0 - p) / ((leg.empirical_games or 0) + 8))
        capped = (f" (held to the model +{cap * 100:.0f} points)" if leg.empirical_probability > leg.model_probability + cap
                  else " (held to 70%: higher estimates for 3+ shots are not supported)" if p < leg.empirical_probability else "")
        return Estimate(p, se, f"{leg.empirical_hits} of his last {leg.empirical_games} games with {leg.threshold}+ shots{capped}")
    if leg.market_family == "MONEYLINE":
        if leg.market_probability is None or not (0.0 < leg.market_probability < 1.0):
            return None
        if leg.market_probability < MONEYLINE_LEG_FLOOR or (leg.model_probability is not None and leg.model_probability < 0.5):
            return None                       # the Elo model must at least agree on who the favourite is (its probabilities are compressed toward 50%, so it is not compared to the price)
        return Estimate(leg.market_probability, MONEYLINE_SE, "the market's own no-vig probability")
    return None


def leg_decision(leg) -> tuple[float | None, str]:
    """(final estimate or None, decision code) for one leg, for the frozen prediction record: why a priced leg was or was not a candidate for a ticket."""
    if leg.market_family not in SHOT_FAMILIES and leg.market_family != "MONEYLINE":
        return None, "NOT_USED_IN_TICKETS_MARKET"
    if leg.market_family in SHOT_FAMILIES:
        if leg.empirical_probability is None or leg.model_probability is None:
            return None, "NO_PLAYER_RECORD"
        if (leg.empirical_games or 0) < MIN_SAMPLE_GAMES:
            return None, "THIN_SAMPLE"
    est = leg_estimate(leg)
    if est is None:
        if leg.market_family == "MONEYLINE":
            return None, "MONEYLINE_NOT_A_CLEAR_FAVOURITE"
        return None, "BELOW_STRONG_LEG_FLOOR"
    if est.p * _decimal(leg) - 1.0 < LEG_MIN_EV:
        return est.p, "GIVES_UP_MORE_THAN_THE_MARGIN"
    return est.p, "SUPPORTED"


def _decimal(leg) -> float:
    a = leg.american_price
    return 1.0 + (a / 100.0 if a > 0 else 100.0 / abs(a))


def prepare_pool(eligible_legs: list, cap: float | None = None) -> list:
    """Eligible legs that are well supported, each carrying its hit-chance estimate as conservative_probability (so recording, settlement and display use the number stated).
    Shots legs are stamped with the rule that produced the estimate (version_tag), so an entry can always be traced to the cap it was made under."""
    cap = ANCHOR_CAP if cap is None else cap
    pool = []
    for leg in eligible_legs:
        est = leg_estimate(leg, cap)
        if est is None:
            continue
        if est.p * _decimal(leg) - 1.0 < LEG_MIN_EV:
            continue
        pool.append(dataclasses.replace(leg, conservative_probability=round(est.p, 4),
                                        model_version=f"{leg.model_version}{version_tag(cap)}" if leg.market_family in SHOT_FAMILIES else leg.model_version))
    return pool


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


def uncertainty(legs: list, cap: float | None = None) -> dict:
    """A plausible range for the ticket's hit chance: every leg moved one standard error together (the widest honest reading; the legs are in different games)."""
    low = high = 1.0
    for l in legs:
        est = leg_estimate(l, cap) if l.empirical_probability is not None or l.market_probability is not None else None
        se = est.se if est else 0.05
        low *= max(l.conservative_probability - se, 0.0)
        high *= min(l.conservative_probability + se, 1.0)
    return {"low": round(low, 4), "high": round(high, 4)}


def ticket_passes(combo, policy, veto) -> bool:
    """The policy's acceptance test for ONE ticket, in one place (used by the search above and by Best Options for single-leg offers)."""
    return (combo.combined_decimal >= policy.min_combined_decimal and within_ceiling(combo.combined_decimal, policy)
            and combo.ev_estimated >= policy.min_estimated_ev and combo.ev_conservative >= 0.0 and veto(combo, policy) is None)


def within_ceiling(decimal: float, policy) -> bool:
    """The policy's longest allowed combined price (+500 for the primary slip), judged on the American price as it is stated, so a ticket whose product rounds to +500 is inside."""
    ceiling = getattr(policy, "max_combined_decimal", None)
    return ceiling is None or round((decimal - 1.0) * 100.0) <= round((ceiling - 1.0) * 100.0)


MAX_SEARCH_NODES = 5_000_000   # a safety bound, not a tuning knob: a search that reaches it says so (diag["truncated"]) instead of silently dropping tickets


def qualifying(pool: list, policy, evaluate, veto) -> tuple[list, dict]:
    """EVERY cross-game ticket of 2..MAX_LEGS legs (one leg per game) that passes the policy, minus tickets made redundant by a passing shorter one. This is the ONE candidate evaluation: the recording
    selector, the Today board, the provisional board and the Best Options list all read it, so they cannot disagree about what qualifies.

    Nothing is pruned by a heuristic that could hide a ticket (an earlier version kept only 4 legs per game and ten games, and lost tickets that passed every test: docs/validation/selector_cap_2026-10-12.md).
    The only pruning is exact: a ticket's hit chance can only fall as legs are added, so a partial ticket already under the policy's floor is abandoned; and a ticket reaches +100, the value tests and the floor
    only if its own numbers do, which are checked on plain floats before any object is built. `evaluate(legs)` builds the ParlayResult; `veto(combo, policy)` is the policy's own floor/value/market test.
    Also returns diagnostics for the empty-board explanation."""
    from research.real_market_parlay import engine as _eng
    cache_key = (policy.digest(), tuple(pool), evaluate, veto)           # legs are frozen dataclasses: identical content, identical key
    hit = _SEARCH_CACHE.get(cache_key)
    if hit is not None:
        return list(hit[0]), dict(hit[1])
    found_list, diagnostics = _search(pool, policy, evaluate, veto, _eng)
    if len(_SEARCH_CACHE) >= 4:
        _SEARCH_CACHE.pop(next(iter(_SEARCH_CACHE)))
    _SEARCH_CACHE[cache_key] = (found_list, diagnostics)
    return list(found_list), dict(diagnostics)


_SEARCH_CACHE: dict = {}      # one trader cycle asks the same question three times (recording, Today's provisional board, Best Options): answer it once


def _search(pool: list, policy, evaluate, veto, _eng) -> tuple[list, dict]:
    margin = _eng.LEG_PROBABILITY_MARGIN
    by_game: dict[str, list] = defaultdict(list)
    for leg in pool:
        by_game[leg.game_id].append((leg, leg.conservative_probability, _decimal(leg), max(leg.conservative_probability - margin, 0.0), max(leg.conservative_probability - policy.value_shade, 0.0)))
    games = sorted(by_game)
    for g in games:
        by_game[g].sort(key=lambda t: -t[1])                     # most likely leg first, so the floor test can stop a game's loop at the first leg that fails it (exact: the rest are lower)
    top_p = [by_game[g][0][1] for g in games]
    diag = {"games_with_supported_legs": len(by_game), "supported_legs": len(pool), "combinations_checked": 0, "reaching_plus_100": 0, "best_reaching_plus_100": None, "truncated": False,
            "below_minimum": 0, "above_maximum": 0, "insufficient_value": 0, "other_rejected": 0}
    ml = [l for l in pool if l.market_family == "MONEYLINE"]
    shots = [l for l in pool if l.market_family != "MONEYLINE"]
    diag.update({"moneyline_legs": len(ml), "moneyline_games": len({l.game_id for l in ml}), "prop_legs": len(shots), "prop_games": len({l.game_id for l in shots})})
    if len(games) < 2:
        return [], diag
    found: dict[frozenset, object] = {}
    nodes = 0
    floor = policy.floor
    value_bar = 1.0 + policy.min_value_after_shade
    ev_bar = 1.0 + policy.min_estimated_ev
    chosen: list = []
    _ceiling = getattr(policy, "max_combined_decimal", None)
    ceiling_D = float("inf") if _ceiling is None else 1.0 + (round((_ceiling - 1.0) * 100.0) + 0.5) / 100.0      # a product that rounds to the ceiling's American price is inside (same rule as within_ceiling)

    def key_of(legs):
        return frozenset((l.game_id, l.participant_id, l.market_family, l.threshold, l.side) for l in legs)

    def visit(start: int, P: float, D: float, Pm: float, Ps: float):
        nonlocal nodes
        for gi in range(start, len(games)):
            if P * top_p[gi] < floor:
                continue                                          # no leg of this game keeps the hit chance at the floor
            for leg, p, d, pm, ps in by_game[games[gi]]:
                P2 = P * p
                if P2 < floor:
                    break
                nodes += 1
                if nodes > MAX_SEARCH_NODES:
                    diag["truncated"] = True
                    return
                D2, Pm2, Ps2 = D * d, Pm * pm, Ps * ps
                if D2 > ceiling_D:                                  # (precomputed: this test runs once per node of a search that can visit millions)
                    if chosen:
                        diag["above_maximum"] += 1                  # a combination longer than the policy's ceiling (counted where the search stops; its longer extensions are not enumerated)
                    continue                                      # the price only lengthens as legs are added, so nothing below this leg can be inside the ceiling either
                chosen.append(leg)
                if len(chosen) >= 2:
                    diag["combinations_checked"] += 1
                    if D2 < policy.min_combined_decimal:
                        diag["below_minimum"] += 1
                    else:
                        diag["reaching_plus_100"] += 1                                  # inside the price range (+100 up to the ceiling)
                        best = diag["best_reaching_plus_100"]
                        if best is None or Ps2 * D2 - 1.0 > best["ev_after_shade"]:
                            diag["best_reaching_plus_100"] = {"hit_probability": P2, "ev_estimated": P2 * D2 - 1.0, "ev_after_shade": Ps2 * D2 - 1.0,
                                                              "price": 100.0 * (D2 - 1.0), "legs": [(l.participant_name, l.threshold, l.market_family, l.american_price) for l in chosen]}
                        if P2 * D2 >= ev_bar and Ps2 * D2 >= value_bar and Pm2 * D2 >= 1.0:
                            combo = evaluate(list(chosen))
                            if combo is not None and ticket_passes(combo, policy, veto):
                                found[key_of(chosen)] = combo
                            else:
                                diag["other_rejected"] += 1                              # priced inside the range with value on the plain numbers, but a ticket-level test or exposure rule refused it
                        else:
                            diag["insufficient_value"] += 1
                if len(chosen) < MAX_LEGS:
                    visit(gi + 1, P2, D2, Pm2, Ps2)
                chosen.pop()
                if diag["truncated"]:
                    return

    visit(0, 1.0, 1.0, 1.0, 1.0)
    passing = {}
    for keys, combo in sorted(found.items(), key=lambda kv: len(kv[0])):          # shortest first: a longer ticket is kept only when no shorter ticket from its own legs passes
        n = len(keys)
        if n > 2 and any(frozenset(sub) in passing for m in range(2, n) for sub in combinations(keys, m)):
            continue
        passing[keys] = combo
    return list(passing.values()), diag


def leg_evidence(leg, cap: float | None = None) -> dict:
    """What stands behind one leg's number, for display, with the four numbers kept apart: the recent hit rate (k of n, unshrunk), the model's probability, the final estimate and the price-implied probability."""
    est = leg_estimate(leg, cap)
    d = _decimal(leg)
    out = {"estimate": leg.conservative_probability, "price_implied": round(1.0 / d, 4), "model": leg.model_probability,
           "basis": est.basis if est else None, "standard_error": round(est.se, 4) if est else None}
    if leg.market_family in SHOT_FAMILIES:
        games = leg.empirical_games or 0
        out.update(kind="player_record", hits=leg.empirical_hits, games=games, recent_hit_rate=(round(leg.empirical_hits / games, 4) if games and leg.empirical_hits is not None else None),
                   record_shrunk=leg.empirical_probability, games_current=getattr(leg, "empirical_games_current", None))
    else:
        out.update(kind="market", elo=leg.model_probability)
    return out


def describe(combo, policy) -> dict:
    """Ticket-level numbers an owner needs: the plausible range, the value at three stated assumptions, and a short plain-language rationale."""
    legs = combo.legs
    cap = cap_for(policy)
    rng = uncertainty(legs, cap)
    mid = midpoint_ev(legs)
    shaded = 1.0
    for l in legs:
        shaded *= max(l.conservative_probability - policy.value_shade, 0.0)
    shaded_ev = shaded * combo.combined_decimal - 1.0
    parts = []
    for l in legs:
        ev = leg_evidence(l, cap)
        if ev["kind"] == "player_record":
            parts.append(f"{l.participant_name} {l.threshold}+ shots: recent hit rate {ev['hits']} of {ev['games']} games ({ev['recent_hit_rate']:.0%}); model {ev['model']:.0%}; final estimate "
                         f"{ev['estimate']:.0%}; price implies {ev['price_implied']:.0%}")
        else:
            parts.append(f"{l.participant_name} to win: final estimate {ev['estimate']:.0%} is the market's own no-vig chance (price implies {ev['price_implied']:.0%}); it adds price, not value")
    text = (f"{'; '.join(parts)}. Together about {combo.joint_probability:.0%} (plausible range {rng['low']:.0%}-{rng['high']:.0%}) against {1.0 / combo.combined_decimal:.0%} implied by the prices. "
            f"Estimated value {combo.ev_estimated:+.0%}; {shaded_ev:+.0%} with every leg lowered {policy.value_shade * 100:.0f} points; {mid:+.0%} if the true chance is halfway between the final estimate and the price. "
            f"The final estimate is the recent record shrunk toward the league rate and held to the model plus {cap * 100:.0f} points (70% for 3+ shots). The cap was chosen on the 2024 season and checked on 2025 "
            "(docs/PROPOSAL_ANCHOR_CAP.md); DraftKings prices these legs lower than the record implies, so the value depends on the record being the better guide. Prices are US-feed quotes, not verified for Ontario.")
    return {"hit_low": rng["low"], "hit_high": rng["high"], "midpoint_ev": round(mid, 4), "ev_after_shade": round(shaded_ev, 4), "rationale": text}
