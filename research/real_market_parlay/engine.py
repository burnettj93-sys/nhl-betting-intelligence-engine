"""
Real-Market Paper Parlay engine V1 (Production Hardening + Parlay Build
block, 2026-09-29). Built only after PLAYER_SOG_ALTERNATE became the first
DK-Ontario-aligned prop family to clear every gate in the DraftKings-
Ontario-aligned parlay roadmap (MODEL STATUS -> TARGET_BOOK_AVAILABLE ->
LIVE PROVIDER CONTRACT -> INGESTION -> IDENTITY -> SETTLEMENT -> CONTEXT
GATE -> PARLAY ELIGIBLE) -- see the RECOVERY PASS block's own
FIRST_PROP_FAMILY_FULLY_GREEN=YES determination for PLAYER_SOG_ALTERNATE
thresholds 2-5.

Deliberately narrow for V1: exactly two market families are ever eligible
(ALLOWED_MARKET_FAMILIES below) -- MONEYLINE (real and verified since the
Live DK completion sprint) and PLAYER_SOG_ALTERNATE (this block's own
certification, restricted to its validated thresholds 2-5). Every other
family this project has ever modeled or discussed for DK Ontario (Saves,
Points, Assists, Team/Game Totals, Hits, Blocks, ...) stays OUT of this
engine's allowlist until it independently earns its own place here --
never "if it's eligible somewhere else, allow it here too."

CROSS-GAME ONLY, by design, not by omission: this engine refuses to
combine two legs that share the same internal nhl.db game_id (see
legs_share_a_game()). This is the one design choice that lets every
combo's joint probability be an honest, un-fudged product of each leg's
own conservative_probability -- two different NHL games are genuinely
independent events (no shared roster, no shared game state), so no
correlation modeling is needed, invented, or assumed away. This project's
own same-game combo engine (research/game_edge_parlay/engine.py) already
exists specifically to handle same-game dependence (same-player copulas,
a Frechet-bounded cross-player shooter/goalie case) -- reusing that
machinery here would be the wrong tool: this product is explicitly a
cross-game, MONEYLINE+SOG-only daily parlay, never a same-game builder.
Naively combining two same-game legs without going through that real
dependence machinery would be exactly the "blindly multiplying same-game
legs" this block explicitly forbids -- so this engine simply never does
it, rather than attempting a partial, unvalidated correlation estimate.

ONE-SIDED PRICING, explicitly reconciled: research/generic_prop_pricing/
evaluator.py::evaluate_prop() -- the generic pricer real PLAYER_SOG_ALTERNATE
quotes are actually priced through -- returns action="NOT_AVAILABLE" for
every one-sided market (Part 42's deliberate "never fake the opposite
side" rule), because it refuses to derive an edge/EV against a raw,
un-vig-stripped implied probability. Since the real, certified
PLAYER_SOG_ALTERNATE payload shape is ALWAYS one-sided (DraftKings has
never posted an Under here -- see provider_adapter.VERIFIED_CONTRACTS's
own evidence), this means no real SOG leg can ever reach a solo "BET"
action label from that generic pipeline alone -- a real, disclosed
consequence of Part 42's conservatism, not something this module patches
over. This engine therefore never requires a candidate leg to already
carry a solo "BET" action; it requires the narrower, real ingredients
from the SOG threshold-eligibility matrix (provider_contract_verified,
model_threshold_eligible, identity_resolved, price_fresh,
event_not_started) plus a real conservative_probability -- see
leg_is_eligible(). COMBO-level value is judged separately (see
_evaluate_combo()'s combo_edge), using each leg's own single-sided
implied probability -- the SAME honest pattern research/game_edge_parlay/
engine.py already uses for its own "estimated_combo_price" (explicitly
never a real DK combined price, Part 47).

NEVER FABRICATES a combined DraftKings same-game-parlay price: the only
"price" this module ever reports for a qualifying combo is
estimated_combo_price, the plain product of each leg's own real,
verified American price converted to implied probability and back --
labeled exactly that, never presented as a real DK SGP quote. A real
combined DK price for this cross-game combination does not exist (DK
does not price cross-game same-slip combos at a discount/premium the way
a same-game parlay is priced) and is not needed: paper settlement uses
this same estimated_combo_price as the paper stake's entry price,
exactly as research/game_edge_parlay/engine.py's own ComboResult already
does for its single-game product.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

from pricing import odds_math
from research.generic_prop_pricing.line_mapping import SOG_ACTIONABLE_THRESHOLDS

ALLOWED_MARKET_FAMILIES = frozenset({"MONEYLINE", "PLAYER_SOG_ALTERNATE"})

MIN_LEGS = 3
MAX_LEGS = 4

# A HARD floor, not a soft target/tie-breaker like research/game_edge_parlay's
# own 0.50 floor / 0.70 preference split -- this block's own explicit
# instruction for the real-market product is "require conservative joint
# probability >= 70%", never merely "prefer it".
MIN_JOINT_PROBABILITY = 0.70


@dataclass(frozen=True)
class ParlayLeg:
    """One candidate leg. Carries its own eligibility ingredients rather
    than a single pre-computed boolean, so leg_is_eligible() stays a
    transparent, independently-auditable function -- never a black box
    the caller has to trust blindly."""
    game_id: str                   # internal nhl.db game_id -- NEVER the provider's event_id
    event_id: str | None
    market_family: str             # "MONEYLINE" | "PLAYER_SOG_ALTERNATE"
    participant_id: str            # team abbrev (MONEYLINE) or player_id (SOG)
    participant_name: str
    side: str                      # "HOME"/"AWAY" (MONEYLINE) or "OVER" (SOG -- the only side DK posts)
    threshold: int | None          # None for MONEYLINE; 2-5 for a real, validated SOG leg
    american_price: float
    conservative_probability: float
    sportsbook: str
    captured_at_utc: str | None
    provider_contract_verified: bool
    model_threshold_eligible: bool
    identity_resolved: bool
    price_fresh: bool
    event_not_started: bool


def leg_is_eligible(leg: ParlayLeg) -> bool:
    """The narrow PARLAY_ELIGIBLE gate for this engine's V1 allowlist.
    Deliberately does NOT check two_sided_no_vig_possible or
    starter_active_status_satisfied -- see the module docstring's
    ONE-SIDED PRICING note for why the former is inapplicable to this
    family's real payload shape; the latter (lineup/starter confirmation)
    stays the responsibility of whichever upstream pipeline sources a
    real leg, exactly like every other real recommendation path in this
    project already requires before calling into shared pricing code."""
    if leg.market_family not in ALLOWED_MARKET_FAMILIES:
        return False
    if leg.market_family == "PLAYER_SOG_ALTERNATE" and leg.threshold not in SOG_ACTIONABLE_THRESHOLDS:
        return False
    if not (0.0 < leg.conservative_probability < 1.0):
        return False
    return all([leg.provider_contract_verified, leg.model_threshold_eligible,
                leg.identity_resolved, leg.price_fresh, leg.event_not_started])


def legs_share_a_game(legs: list[ParlayLeg]) -> bool:
    game_ids = [l.game_id for l in legs]
    return len(set(game_ids)) != len(game_ids)


def joint_probability(legs: list[ParlayLeg]) -> float:
    """Cross-game legs are independent by construction (see module
    docstring) -- the joint probability is the plain product of each
    leg's own conservative_probability. Callers outside this module
    should never call this directly on an unchecked leg set;
    _evaluate_combo() always checks legs_share_a_game() first."""
    p = 1.0
    for leg in legs:
        p *= leg.conservative_probability
    return p


@dataclass(frozen=True)
class ParlayResult:
    legs: list[ParlayLeg]
    joint_probability: float
    fair_combo_price: float          # the MODEL's own fair price for the joint probability
    estimated_combo_price: float     # product of each leg's OWN real price -- never a DK SGP quote
    combo_edge: float                # joint_probability - product of each leg's own raw implied prob
    offered_parlay_price: None = field(default=None)  # NEVER fabricated -- see module docstring


def _evaluate_combo(legs: list[ParlayLeg]) -> ParlayResult | None:
    """None means this leg group is not a candidate combo at all (shares
    a game) -- distinct from a candidate that simply fails the quality
    gates, mirroring research/game_edge_parlay/engine.py's own
    None-means-rejected-outright convention."""
    if legs_share_a_game(legs):
        return None
    jp = joint_probability(legs)
    estimated_product_prob = 1.0
    for leg in legs:
        estimated_product_prob *= odds_math.american_to_prob(leg.american_price)
    return ParlayResult(
        legs=legs, joint_probability=jp,
        fair_combo_price=odds_math.prob_to_american(jp),
        estimated_combo_price=odds_math.prob_to_american(estimated_product_prob),
        combo_edge=jp - estimated_product_prob,
    )


def _passes_quality_gates(combo: ParlayResult) -> bool:
    return combo.joint_probability >= MIN_JOINT_PROBABILITY and combo.combo_edge > 0.0


def _best_combo_of_size(legs: list[ParlayLeg], size: int) -> ParlayResult | None:
    best: ParlayResult | None = None
    for group in combinations(legs, size):
        combo = _evaluate_combo(list(group))
        if combo is None or not _passes_quality_gates(combo):
            continue
        if best is None or combo.joint_probability > best.joint_probability:
            best = combo
    return best


def build_real_market_parlay(candidate_legs: list[ParlayLeg]) -> dict:
    """The main entry point. Returns either:
      {"status": "QUALIFIED", "recommended_legs": 3|4, "combo": ParlayResult, "alternative_3leg": ParlayResult|None}
      {"status": "NO_QUALIFYING_PARLAY", "reason": str}
    Never manufactures a result: 0 qualifying parlays on a given slate is
    a real, expected, correct PASS outcome, never forced up to meet a
    bet count."""
    eligible = [l for l in candidate_legs if leg_is_eligible(l)]
    if len(eligible) < MIN_LEGS:
        return {"status": "NO_QUALIFYING_PARLAY",
                "reason": f"only {len(eligible)} PARLAY_ELIGIBLE leg(s) on the allowlist "
                          f"({sorted(ALLOWED_MARKET_FAMILIES)}) -- need at least {MIN_LEGS}"}

    best_3 = _best_combo_of_size(eligible, 3)
    if best_3 is None:
        return {"status": "NO_QUALIFYING_PARLAY",
                "reason": "no 3-leg, single-game-max combination cleared both the "
                          f">= {MIN_JOINT_PROBABILITY:.0%} joint-probability floor and a positive combo edge"}

    best_4 = _best_combo_of_size(eligible, MAX_LEGS) if len(eligible) >= MAX_LEGS else None
    # The 4th leg is added only if it doesn't drag the combo below the real
    # floor and combo-level value stays positive -- otherwise the 3-leg
    # stands (always compare, never assume more legs is better).
    if best_4 is not None and _passes_quality_gates(best_4):
        return {"status": "QUALIFIED", "recommended_legs": 4, "combo": best_4, "alternative_3leg": best_3}
    return {"status": "QUALIFIED", "recommended_legs": 3, "combo": best_3, "alternative_3leg": None}
