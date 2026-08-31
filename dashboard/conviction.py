"""
Same-Day Demo Experience sprint (2026-08-31): Top Conviction ranking +
High-Confidence Combos. Presentation-level logic only -- never a new
probability model, never a refit, never a change to decision_policy.

CONVICTION SCORE (Part 14): a ranking score, NEVER presented as a
literal probability. Combines conservative probability, no-vig edge,
EV, and confidence -- deliberately weighted so a high-probability but
bad-value leg (e.g. 90% at -800 simulated, no real edge) does NOT
outrank a lower-probability, better-value leg (Part 13/18).

JOINT DEPENDENCE (Part 18/19): combos are built ONLY from the real,
frozen per-pair correlations in research/joint_scoring_dependence_results.json
(`rho_by_name`), fed into the real, already-tested
research.joint_scoring_dependence.joint_models.gaussian_copula_joint_upper_tail
-- never reimplemented, never a blind independence multiply. A leg pair
with no real frozen rho is marked JOINT_DEPENDENCE_NOT_VALIDATED and
excluded from Top Conviction Combos (may appear in a separately-labeled
research/exploration list only).
"""
from __future__ import annotations

from pathlib import Path

from dashboard import data_access as da
from research.joint_scoring_dependence import joint_models as jm
from research.joint_scoring_dependence.logical_implication_registry import detect_redundant_leg

REPO_ROOT = Path(__file__).resolve().parent.parent
_RHO_PATH = REPO_ROOT / "research" / "joint_scoring_dependence_results.json"

CONFIDENCE_WEIGHT = {"HIGH": 1.0, "MEDIUM": 0.6, "LOW": 0.0}

# Combo-name convention -> (prop_a, threshold_a, prop_b, threshold_b).
# Sourced directly from the real rho_by_name keys -- never invented.
_RHO_KEY_TO_PAIR = {
    "SOG2_GOAL": ("sog", 2, "goals", 1), "SOG3_GOAL": ("sog", 3, "goals", 1),
    "SOG4_GOAL": ("sog", 4, "goals", 1), "SOG2_ASSIST": ("sog", 2, "assists", 1),
    "SOG3_ASSIST": ("sog", 3, "assists", 1), "SOG3_POINT": ("sog", 3, "points", 1),
    "SOG4_POINT": ("sog", 4, "points", 1),
}

# Redundant-leg pairs (Part 20): logical identities, never priced as an
# independent statistical combo -- collapsed/flagged, not combined.
_REDUNDANT_PAIRS = {("goals", 1, "points", 1), ("points", 1, "goals", 1),
                     ("assists", 1, "points", 1), ("points", 1, "assists", 1)}


def _load_rho_by_name() -> dict[str, float]:
    # da.load_json_safely(), never a bare json.load() -- see
    # tests/test_dashboard.py's malformed-cache-file AST guard (BUG-202).
    data = da.load_json_safely(_RHO_PATH)
    return data["rho_by_name"] if data else {}


def _rho_lookup() -> dict[tuple, float]:
    rho_by_name = _load_rho_by_name()
    out = {}
    for key, rho in rho_by_name.items():
        pair = _RHO_KEY_TO_PAIR.get(key)
        if pair is None:
            continue
        out[pair] = rho
        a, ta, b, tb = pair
        out[(b, tb, a, ta)] = rho
    return out


_RHO_LOOKUP = _rho_lookup()


def conviction_score(opp: dict) -> float:
    """Part 14: a presentation-level ranking score, NEVER a literal
    probability. Weighted so value (edge/EV) matters as much as raw
    probability -- a 90% leg with -800 simulated pricing (no real edge)
    scores LOWER than a 78% leg with a real, meaningful edge."""
    conservative_p = opp.get("conservative_probability") or 0.0
    edge = opp.get("conservative_edge") or 0.0
    ev = opp.get("ev") or 0.0
    confidence_w = CONFIDENCE_WEIGHT.get(opp.get("confidence"), 0.3)
    edge_component = min(max(edge, 0.0), 0.15) / 0.15
    ev_component = min(max(ev, 0.0), 0.30) / 0.30
    return (0.30 * conservative_p) + (0.35 * edge_component) + (0.20 * ev_component) + (0.15 * confidence_w)


def top_conviction(opportunities: list[dict], *, max_n: int = 5, min_edge: float = 0.02) -> list[dict]:
    """Part 12/13/44: only BET-grade, real-value opportunities qualify
    -- decision == "BET" already means decision_policy's real edge/EV/
    confidence gates were cleared (Part 6's threshold-validation gate is
    upstream of that too, via eligible_bets.py). No separate, hand-
    picked probability floor is layered on top: Part 12's "prefer high
    model probability" is honored through conviction_score's own 30%
    weight on conservative_probability during RANKING, not as a second
    hard cutoff that could arbitrarily zero out every real opportunity
    on a given simulated slate (never lower the bar to force a number,
    but never invent a second bar either). Never pads to max_n --
    returns however many genuinely clear it (0 to max_n), sorted by
    conviction_score descending."""
    candidates = [
        o for o in opportunities
        if o.get("decision") == "BET"
        and o.get("actionable", True)
        and (o.get("conservative_edge") or 0.0) >= min_edge
        and o.get("confidence") in ("HIGH", "MEDIUM")
        and (o.get("starter_certainty") is None or o.get("starter_certainty") >= 0.6)
    ]
    for o in candidates:
        o["conviction_score"] = conviction_score(o)
    return sorted(candidates, key=lambda o: -o["conviction_score"])[:max_n]


def _pair_key(leg_a: dict, leg_b: dict) -> tuple | None:
    if leg_a["player_id"] != leg_b["player_id"]:
        return None
    ta, tb = int(leg_a["threshold"].rstrip("+")), int(leg_b["threshold"].rstrip("+"))
    return (leg_a["prop"], ta, leg_b["prop"], tb)


def joint_probability_for_pair(leg_a: dict, leg_b: dict) -> dict:
    """Returns {"status": "VALIDATED"|"REDUNDANT"|"JOINT_DEPENDENCE_NOT_VALIDATED",
    "joint_probability": float|None, "method": str|None, "rho": float|None}."""
    key = _pair_key(leg_a, leg_b)
    if key is None:
        return {"status": "JOINT_DEPENDENCE_NOT_VALIDATED", "joint_probability": None,
                "method": None, "rho": None,
                "note": "legs belong to different players -- no validated cross-player dependence model"}
    if key[0] == key[2] and leg_a["player_id"] == leg_b["player_id"]:
        # Same prop, same player, two different thresholds (e.g. SOG 2+
        # and SOG 4+, or Points 1+ and Points 2+) are trivially nested
        # events -- the higher threshold exactly implies the lower one.
        # logical_control_probability() applies exactly the same as the
        # cross-family registry identities below, never a separate case.
        lower_leg = leg_a if key[1] < key[3] else leg_b
        return {"status": "REDUNDANT", "joint_probability": jm.logical_control_probability(
                    max(leg_a["conservative_probability"], leg_b["conservative_probability"])),
                "method": "LOGICAL_IDENTITY -- same-family nested threshold (Part 20)", "rho": None,
                "note": f"{lower_leg['market']} {lower_leg['threshold']} is implied by the other leg's "
                        f"higher threshold -- not independent added value"}
    if key in _REDUNDANT_PAIRS:
        p = min(leg_a["conservative_probability"], leg_b["conservative_probability"])
        return {"status": "REDUNDANT", "joint_probability": p, "method": "LOGICAL_IDENTITY (Part 20)",
                "rho": None, "note": "one leg logically implies the other -- not independent added value"}
    rho = _RHO_LOOKUP.get(key)
    if rho is None:
        return {"status": "JOINT_DEPENDENCE_NOT_VALIDATED", "joint_probability": None, "method": None,
                "rho": None, "note": "no validated joint-dependence model exists for this pair"}
    joint_p = jm.gaussian_copula_joint_upper_tail(leg_a["conservative_probability"],
                                                   leg_b["conservative_probability"], rho)
    return {"status": "VALIDATED", "joint_probability": joint_p,
            "method": "Gaussian copula (frozen rho, research/joint_scoring_dependence_results.json)",
            "rho": rho}


def _combo_from_legs(legs: list[dict]) -> dict | None:
    """Builds one combo from 2 or 3 legs, checking every pairwise
    dependency. If ANY pair is redundant, the combo is skipped (Part
    20 -- collapse, don't present as added value). If ANY pair is
    unvalidated, the whole combo is JOINT_DEPENDENCE_NOT_VALIDATED and
    must never enter Top Conviction Combos (Part 19)."""
    from itertools import combinations
    from pricing import odds_math as pm

    pair_results = []
    for a, b in combinations(legs, 2):
        result = joint_probability_for_pair(a, b)
        if result["status"] == "REDUNDANT":
            return None  # collapse entirely -- never present as a combo
        pair_results.append(result)

    if any(r["status"] == "JOINT_DEPENDENCE_NOT_VALIDATED" for r in pair_results):
        overall_status = "JOINT_DEPENDENCE_NOT_VALIDATED"
        joint_p = None
    else:
        overall_status = "VALIDATED"
        # Chain the pairwise joint probabilities conservatively: apply
        # each successive leg's copula adjustment against the running
        # joint estimate, clipped to Frechet bounds at every step --
        # never a blind product once ANY real dependence exists.
        joint_p = legs[0]["conservative_probability"]
        for leg, pair_result in zip(legs[1:], pair_results):
            if pair_result["rho"] is not None:
                joint_p = jm.gaussian_copula_joint_upper_tail(joint_p, leg["conservative_probability"],
                                                                pair_result["rho"])
            else:
                joint_p = jm.clip_to_frechet(joint_p * leg["conservative_probability"], joint_p,
                                              leg["conservative_probability"])

    simulated_product = 1.0
    fair_product_prob = 1.0
    for leg in legs:
        simulated_product *= _implied_prob(leg["current_odds"])
        fair_product_prob *= leg["coherent_probability"]

    return {
        "legs": legs, "status": overall_status, "joint_probability": joint_p,
        "pairwise": pair_results,
        "simulated_combo_price": pm.prob_to_american(simulated_product) if overall_status == "VALIDATED" else None,
        "fair_combo_price": pm.prob_to_american(joint_p) if joint_p else None,
        "naive_independent_fair_price": pm.prob_to_american(fair_product_prob),
        "combo_edge": (joint_p - simulated_product) if (joint_p is not None) else None,
    }


def _implied_prob(american_odds: float) -> float:
    from pricing import odds_math as pm
    return pm.american_to_prob(american_odds)


def combo_eligible_legs(opportunities: list[dict]) -> list[dict]:
    """Part 21's own combo-leg eligibility list: validated threshold
    (already true of everything eligible_bets.py marks actionable),
    positive edge, positive EV, high/medium confidence, real
    simulated/live market available -- deliberately BET-or-WATCH grade
    (not BET-only), since a real, validated same-player dependency pair
    is the point of a combo even when neither leg alone quite clears the
    solo BET bar (Part 17's whole premise: combining individually solid
    legs). Never includes PASS/WAIT/RESEARCH_ONLY."""
    return [
        o for o in opportunities
        if o.get("decision") in ("BET", "WATCH")
        and o.get("actionable", True)
        # raw_edge > 0, matching decide()'s own WATCH definition exactly
        # (research/live_sog_pricing/pricing.py::decide) -- a combo leg
        # is inherently exploratory (Part 17), so it uses the same real
        # bar a solo WATCH already clears, not a stricter one invented
        # here.
        and (o.get("raw_edge") or 0.0) > 0
        and o.get("confidence") in ("HIGH", "MEDIUM")
        and o.get("current_odds") is not None
    ]


def build_high_confidence_combos(opportunities: list[dict], *, max_combos: int = 4) -> dict:
    """Part 16-22: 2-leg and 3-leg combos, built per-player (a validated
    joint-dependence pair is always a same-player relationship in this
    project's real registry -- see _pair_key) from every combo-eligible
    leg, not just the narrow Top Conviction list -- Top Conviction is a
    single-leg ranking, this is a separate, real search over every
    eligible pair/triple. Returns {"validated": [...], "not_validated":
    [...]} -- the two are NEVER mixed in the same list, and only
    "validated" may ever be labeled Top Conviction Combos in the UI."""
    from collections import defaultdict
    from itertools import combinations

    eligible = combo_eligible_legs(opportunities)
    by_player = defaultdict(list)
    for o in eligible:
        by_player[o["player_id"]].append(o)

    validated, not_validated = [], []
    seen_validated_keys = set()
    for player_legs in by_player.values():
        if len(player_legs) < 2:
            continue
        for size in (2, 3):
            if len(player_legs) < size:
                continue
            for leg_group in combinations(player_legs, size):
                combo = _combo_from_legs(list(leg_group))
                if combo is None:
                    continue
                if combo["status"] == "VALIDATED":
                    key = tuple(sorted((l["player_id"], l["prop"], l["threshold"]) for l in leg_group))
                    if key in seen_validated_keys:
                        continue
                    seen_validated_keys.add(key)
                    validated.append(combo)
                else:
                    not_validated.append(combo)
    validated.sort(key=lambda c: -(c["combo_edge"] or -1))
    return {"validated": validated[:max_combos], "not_validated": not_validated[:max_combos]}
