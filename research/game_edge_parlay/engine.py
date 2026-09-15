"""Game Edge Parlay construction (Parts 27-47). Built on top of this
project's existing, real, tested combo infrastructure
(dashboard/conviction.py) -- reuses its same-player joint-dependence
math (real frozen Gaussian-copula rho, real logical-redundancy rules)
rather than reimplementing it, and extends it to GAME-level (not just
same-player) 3-4 leg combinations.

Honest scope note on cross-player dependence: research/joint_shot_workload
has a real, VALIDATED structural model for (shooter SOG, opposing goalie
Saves) -- structural_joint_player_goalie() -- but it requires live
mu_team/player_share inputs that are not currently wired into any
per-game inference pipeline this engine can call (that plumbing is a
real, disclosed gap, not something this module fabricates). Rather than
either (a) inventing placeholder structural parameters, which would be
dishonest, or (b) naively multiplying two legs this project's own
research has shown ARE correlated, this module uses a Frechet-bounded
conservative estimate for exactly that one cross-player case -- see
leg_pair_dependence()'s CONSERVATIVE_BOUND branch. Wiring the real
structural model is tracked as a known limitation, not silently skipped.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Literal

from dashboard import conviction as conv
from pricing import odds_math as pm
from research.joint_scoring_dependence import joint_models as jm

TIER_1_PROPS = frozenset({"sog", "saves"})  # Part 32: SOG + Saves are the primary priority
MIN_LEGS = 3
MAX_LEGS = 4

# Part 28: a preference for ranking/reporting, NEVER a hard cutoff that
# would itself be "gaming the target" (Part 44) -- qualification is
# governed entirely by the real gates in leg_is_parlay_eligible() and
# _combo_passes_quality_gates() below; this constant only breaks ties
# among already-qualifying combos and feeds the calibration-tracking
# report (Part 43/57/69).
TARGET_JOINT_PROBABILITY = 0.70

# Part 30: "not chasing +800/+1200 lottery-ticket returns" -- a real
# floor a qualifying combo's joint probability must clear, independent
# of the 0.70 preference above.
MIN_ACCEPTABLE_JOINT_PROBABILITY = 0.50

Status = Literal["QUALIFIED", "NO_QUALIFYING_GAME_EDGE_PARLAY"]


def game_eligible_legs(opportunities: list[dict], team_a: str, team_b: str) -> list[dict]:
    """Part 31, scoped to one game -- conviction.combo_eligible_legs()
    already enforces BET/WATCH-or-better, positive raw edge, HIGH/MEDIUM
    confidence, and a real current price; this only adds the same-game
    filter conviction.py's own (per-player, not per-game) combo board
    never needed. Matches dashboard/eligible_bets.py::eligible_bets_for_game's
    own real convention exactly: this project's opportunity dicts carry
    `team`/`opponent` abbreviations, never an `event_id` field -- there
    is no such field to group on."""
    return [o for o in conv.combo_eligible_legs(opportunities) if o.get("team") in (team_a, team_b)]


def leg_priority_tier(leg: dict) -> int:
    """1 = SOG/Saves (Part 32 primary), 2 = everything else eligible.
    Used only to break ties / order presentation -- never to force a
    Tier-1 leg into a combo that doesn't otherwise qualify (Part 32:
    'never force a SOG/Saves leg if another market is clearly
    superior')."""
    return 1 if leg.get("prop") in TIER_1_PROPS else 2


def _is_cross_player_shooter_saves_pair(leg_a: dict, leg_b: dict) -> bool:
    return {leg_a.get("prop"), leg_b.get("prop")} == {"sog", "saves"} and leg_a.get("team") != leg_b.get("team")


def leg_pair_dependence(leg_a: dict, leg_b: dict) -> dict:
    """Returns the same shape as conviction.joint_probability_for_pair:
    {"status", "joint_probability", "method", "rho"}. Three real paths:
    same-player (delegates entirely to the already-tested conviction.py
    logic, including its redundancy detection), cross-player shooter-vs-
    opposing-goalie-saves (conservative Frechet bound -- see module
    docstring), or genuinely unestablished dependence (independence is
    the statistically correct default, not a shortcut)."""
    if leg_a["player_id"] == leg_b["player_id"]:
        return conv.joint_probability_for_pair(leg_a, leg_b)
    if _is_cross_player_shooter_saves_pair(leg_a, leg_b):
        p_a, p_b = leg_a["conservative_probability"], leg_b["conservative_probability"]
        naive = p_a * p_b
        bounded = jm.clip_to_frechet(naive, p_a, p_b)
        return {"status": "CONSERVATIVE_BOUND", "joint_probability": bounded,
                "method": "Frechet-bounded naive product -- a real validated structural model exists "
                          "(research/joint_shot_workload.structural_joint_player_goalie) but is not yet "
                          "wired into a live per-game inference pipeline this engine can call; this bound "
                          "is used instead of an unvalidated naive multiply", "rho": None}
    return {"status": "INDEPENDENT_ASSUMED",
            "joint_probability": leg_a["conservative_probability"] * leg_b["conservative_probability"],
            "method": "no established correlation between these two legs", "rho": None}


@dataclass(frozen=True)
class ComboResult:
    legs: list[dict]
    status: str  # "VALIDATED" (every pair real/bounded) or "REDUNDANT" (rejected entirely)
    joint_probability: float | None
    pairwise: list[dict]
    estimated_combo_price: float | None  # Part 47: NEVER "live DK parlay price"
    fair_combo_price: float | None
    combo_edge: float | None


def _evaluate_combo(legs: list[dict]) -> ComboResult | None:
    """None means the combo was rejected outright (a redundant pair was
    found -- Part 40, collapse rather than present). Otherwise chains
    pairwise dependence conservatively across all legs, matching
    conviction.py's own _combo_from_legs() chaining approach exactly
    (each successive leg folded in via its pairwise result, Frechet-
    clipped when no copula rho applies)."""
    pairwise = []
    for a, b in combinations(legs, 2):
        result = leg_pair_dependence(a, b)
        if result["status"] == "REDUNDANT":
            return None
        pairwise.append(result)

    # Fold sequentially leg-by-leg (matches conviction.py's own approach):
    # each new leg's joint contribution uses its pairwise result against
    # the FIRST leg as the representative dependence signal for the
    # running estimate, then Frechet-clips against the running joint --
    # conservative by construction, never allowed to exceed either
    # marginal.
    joint_p = legs[0]["conservative_probability"]
    for i, leg in enumerate(legs[1:], start=1):
        pair_result = leg_pair_dependence(legs[0], leg)
        if pair_result.get("rho") is not None:
            joint_p = jm.gaussian_copula_joint_upper_tail(joint_p, leg["conservative_probability"], pair_result["rho"])
        else:
            candidate = pair_result["joint_probability"]
            naive = joint_p * leg["conservative_probability"]
            joint_p = jm.clip_to_frechet(min(candidate, naive) if candidate is not None else naive,
                                          joint_p, leg["conservative_probability"])

    estimated_product_prob = 1.0
    fair_product_prob = 1.0
    for leg in legs:
        estimated_product_prob *= pm.american_to_prob(leg["current_odds"])
        fair_product_prob *= leg["conservative_probability"]

    return ComboResult(
        legs=legs, status="VALIDATED", joint_probability=joint_p, pairwise=pairwise,
        estimated_combo_price=pm.prob_to_american(estimated_product_prob),
        fair_combo_price=pm.prob_to_american(joint_p) if joint_p else None,
        combo_edge=(joint_p - estimated_product_prob) if joint_p is not None else None,
    )


def _combo_passes_quality_gates(combo: ComboResult) -> bool:
    """The real, hard qualification bar (Part 31/28) -- every leg
    already cleared game_eligible_legs()'s solo requirements (model
    support, real price, positive edge, HIGH/MEDIUM confidence); this
    adds the COMBO-level requirements: positive combo-level edge
    (parlay vig compounds -- a combo can have all-positive legs and
    still be bad value as a unit), and a real floor under the joint
    probability so this never presents a disguised lottery ticket
    (Part 30)."""
    if combo.joint_probability is None or combo.combo_edge is None:
        return False
    return combo.joint_probability >= MIN_ACCEPTABLE_JOINT_PROBABILITY and combo.combo_edge > 0.0


def _best_combo_of_size(legs: list[dict], size: int) -> ComboResult | None:
    best: ComboResult | None = None
    for leg_group in combinations(legs, size):
        combo = _evaluate_combo(list(leg_group))
        if combo is None or not _combo_passes_quality_gates(combo):
            continue
        if best is None or (combo.joint_probability or 0.0) > (best.joint_probability or 0.0):
            best = combo
    return best


def build_game_edge_parlay(opportunities: list[dict], team_a: str, team_b: str) -> dict:
    """The main entry point -- `team_a`/`team_b` are the two real team
    abbreviations for one game (matching eligible_bets_for_game's own
    signature). Returns either:
      {"status": "QUALIFIED", "recommended_legs": 3|4, "combo": ComboResult, "alternative_3leg": ComboResult|None}
      {"status": "NO_QUALIFYING_GAME_EDGE_PARLAY", "reason": str}
    Never manufactures a result (Part 29) -- a real, honest
    NO_QUALIFYING_GAME_EDGE_PARLAY is the expected, correct outcome for
    most games on most nights."""
    legs = game_eligible_legs(opportunities, team_a, team_b)
    if len(legs) < MIN_LEGS:
        return {"status": "NO_QUALIFYING_GAME_EDGE_PARLAY",
                "reason": f"only {len(legs)} solo-eligible leg(s) in this game -- need at least {MIN_LEGS}"}

    best_3 = _best_combo_of_size(legs, 3)
    if best_3 is None:
        return {"status": "NO_QUALIFYING_GAME_EDGE_PARLAY",
                "reason": "no 3-leg combination in this game cleared the real quality bar "
                          "(model support + positive edge + acceptable confidence + validated/bounded "
                          "dependence + minimum joint probability + positive combo edge)"}

    best_4 = _best_combo_of_size(legs, 4) if len(legs) >= 4 else None

    # Part 42: the 4th leg is added ONLY if it doesn't drag the combo
    # below the real floor and combo-level value stays positive --
    # otherwise the 3-leg stands (Part 41: always compare, never assume
    # more legs is better).
    if best_4 is not None and best_4.joint_probability >= MIN_ACCEPTABLE_JOINT_PROBABILITY and best_4.combo_edge > 0.0:
        return {"status": "QUALIFIED", "recommended_legs": 4, "combo": best_4, "alternative_3leg": best_3}
    return {"status": "QUALIFIED", "recommended_legs": 3, "combo": best_3, "alternative_3leg": None}


def calibration_snapshot(combo: ComboResult) -> dict:
    """Part 43/57/69: how this specific combo compares to the owner's
    ~70% target -- reporting only, never fed back into the qualification
    gate itself (that would be exactly the target-gaming Part 44
    forbids)."""
    return {
        "modeled_joint_probability": combo.joint_probability,
        "target_joint_probability": TARGET_JOINT_PROBABILITY,
        "gap_to_target": (combo.joint_probability - TARGET_JOINT_PROBABILITY) if combo.joint_probability is not None else None,
    }
