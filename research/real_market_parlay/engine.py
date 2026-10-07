"""
Unified NHL paper-ticket selector (the ONE engine behind Today, the paper
trader, ticket history, settlement and postmortems).

History: this module began as the Real-Market Paper Parlay engine V1 (3-4
leg parlays, hard 70% joint-probability floor). The +100 "Best Bets" work
then produced a second, parallel recommendation list under different rules,
so what the user saw was not what the ledger staked. Both now go through
`select_tickets()` below; there is no other place that decides what a
recommended ticket is.

TICKET POLICY (all constants below, all documented, none tuned to fill a board):
  * Cross-game parlays only. Two legs from the same nhl.db game are never
    combined -- no same-game parlay without a joint-probability model and a
    verified combined price, neither of which exists here.
  * Combined decimal price >= 2.0 (+100 or better). The combined price is the
    product of each leg's own sportsbook price. It is an ESTIMATED combined
    price, not a quoted DraftKings parlay price.
  * Two legs by default. A longer ticket is only allowed when no sub-ticket
    of two or more of its legs passes this same policy on its own ("add legs
    only when justified").
  * EV policy replaces the old inherited 70% hit-chance rule. A hit chance
    cannot be both high and +100 for long, so the test is value, not size:
      - each leg's conservative probability must beat its own implied price;
      - estimated EV  = P_joint * decimal - 1  >= MIN_ESTIMATED_EV;
      - model-uncertainty EV, with every leg's probability lowered by
        LEG_PROBABILITY_MARGIN, must still be >= 0.
    Probabilities are never inflated; the margin only ever lowers them.
  * Up to MAX_TICKETS_PER_DAY distinct tickets per Eastern day. Singles are a
    separate, informational list and do not count toward it.
  * No blanket "a game can be used once" rule across tickets. Instead:
    identical leg sets are duplicates, one leg may sit on at most
    MAX_TICKETS_PER_LEG tickets, one game on at most MAX_TICKETS_PER_GAME
    tickets, and opposite moneyline sides of one game are never both held.

Everything else below (ParlayLeg, leg_is_eligible, etc.) is the original
leg-eligibility machinery, unchanged: the contract-verified allowlist, the
validated SOG/Saves thresholds and the identity/price/start checks are the
same gates as before.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

from pricing import odds_math
from research.generic_prop_pricing.line_mapping import SOG_ACTIONABLE_THRESHOLDS

# Mirrors operational/real_prop_orchestrator.py::SAVES_VALIDATED_THRESHOLDS --
# the GOALIE_SAVES model's own validated threshold set
# (GOALIE_SAVES_VALIDATION_REPORT.md). Not imported directly to avoid a
# research -> operational layering inversion (operational already depends on
# research, never the reverse); duplicated here as a small, static, already-
# published model-validation constant, not business logic expected to drift.
SAVES_VALIDATED_THRESHOLDS = frozenset({20, 25})

# Standard SOG/Saves Certification block (2026-10-01): PLAYER_SOG and
# GOALIE_SAVES added. PLAYER_SOG's real, certified standard-market shape is
# two-sided (unlike PLAYER_SOG_ALTERNATE's one-sided ladder), so it CAN clear
# a genuine two-sided no-vig edge and reach a real solo BET action from
# research/generic_prop_pricing/evaluator.py::evaluate_prop() -- see
# research/real_market_parlay/real_slate_adapter.py::sog_standard_candidate_legs().
# GOALIE_SAVES is added for architectural completeness and future-readiness,
# but structurally produces ZERO real legs today: every real Saves quote
# routes through operational/real_prop_orchestrator.py::
# _apply_starter_certainty_gate(), which forces WAIT on any would-be BET/WATCH
# unless a real external starter-confirmation source exists -- and none does
# (docs/STARTING_GOALIE_SOURCE_AUDIT.md). This is a genuine, deliberate,
# preserved gate (betting a specific named goalie's saves line when the wrong
# goalie plays is a void/mispriced bet, not merely "less certain" the way a
# team-level moneyline probability is) -- never weakened to manufacture legs.
# Unified ticket workflow (2026-10-07): PLAYER_POINTS added after its own contract
# certification (a real archived DraftKings player_points payload, see
# provider_adapter.VERIFIED_CONTRACTS) and a settlement mapping. Lines on the DK Ontario
# menu are the 1+/2+ milestones (Over 0.5 / 1.5); 3+ is not modeled.
POINTS_ACTIONABLE_THRESHOLDS = frozenset({1, 2})
ALLOWED_MARKET_FAMILIES = frozenset({"MONEYLINE", "PLAYER_SOG_ALTERNATE", "PLAYER_SOG", "GOALIE_SAVES",
                                     "PLAYER_POINTS"})

MIN_LEGS = 2
MAX_LEGS = 4

MIN_COMBINED_DECIMAL = 2.0          # +100 or better, always
MIN_LEG_DECIMAL = 1.20              # a -500 leg is filler: lowers the hit chance for almost no payout
MIN_ESTIMATED_EV = 0.05             # P_joint * decimal - 1, at the conservative leg probabilities
LEG_PROBABILITY_MARGIN = 0.03       # absolute haircut per leg for the uncertainty test (never added)
MAX_TICKETS_PER_DAY = 5
MAX_TICKETS_PER_LEG = 2
MAX_TICKETS_PER_GAME = 3
MAX_POOL_FOR_LONG_TICKETS = 24      # bounds the 3-4 leg search; best legs by edge are kept
MAX_SINGLES = 5


@dataclass(frozen=True)
class ParlayLeg:
    """One candidate leg. Carries its own eligibility ingredients rather
    than a single pre-computed boolean, so leg_is_eligible() stays a
    transparent, independently-auditable function -- never a black box
    the caller has to trust blindly."""
    game_id: str                   # internal nhl.db game_id -- NEVER the provider's event_id
    event_id: str | None
    market_family: str             # "MONEYLINE" | "PLAYER_SOG_ALTERNATE" | "PLAYER_SOG" | "GOALIE_SAVES" | "PLAYER_POINTS"
    participant_id: str            # team abbrev (MONEYLINE) or player_id/goalie_id (SOG/Saves)
    participant_name: str
    side: str                      # "HOME"/"AWAY" (MONEYLINE); "OVER" (PLAYER_SOG_ALTERNATE -- the
                                    # only side DK posts); "OVER"/"UNDER" (PLAYER_SOG/GOALIE_SAVES --
                                    # real two-sided standard markets, though the real production path
                                    # only ever prices the Over side -- see sog_standard_candidate_legs())
    threshold: int | None          # None for MONEYLINE; 2-5 for SOG; 20/25 for Saves
    american_price: float
    conservative_probability: float
    sportsbook: str
    captured_at_utc: str | None
    provider_contract_verified: bool
    model_threshold_eligible: bool
    identity_resolved: bool
    price_fresh: bool
    event_not_started: bool
    # Display / audit context frozen onto recorded tickets. All optional so the
    # eligibility machinery above is unaffected.
    team: str | None = None
    opponent: str | None = None
    game_start_utc: str | None = None
    model_version: str = ""


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
    if leg.market_family in ("PLAYER_SOG_ALTERNATE", "PLAYER_SOG") and leg.threshold not in SOG_ACTIONABLE_THRESHOLDS:
        return False
    if leg.market_family == "GOALIE_SAVES" and leg.threshold not in SAVES_VALIDATED_THRESHOLDS:
        return False
    if leg.market_family == "PLAYER_POINTS" and leg.threshold not in POINTS_ACTIONABLE_THRESHOLDS:
        return False
    if not (0.0 < leg.conservative_probability < 1.0):
        return False
    return all([leg.provider_contract_verified, leg.model_threshold_eligible,
                leg.identity_resolved, leg.price_fresh, leg.event_not_started])


def legs_share_a_game(legs: list[ParlayLeg]) -> bool:
    game_ids = [l.game_id for l in legs]
    return len(set(game_ids)) != len(game_ids)


def _economic_identity(leg: ParlayLeg) -> tuple:
    """What makes two ParlayLeg objects the SAME real bet, independent of
    Python object identity. Production Gap Closure sprint (2026-09-30):
    two archive captures of the same underlying DraftKings quote (taken
    minutes apart) previously produced two distinct ParlayLeg objects that
    this engine's old id()-based dedup treated as genuinely different legs
    -- they could then land in two different "independent" tickets in the
    same run, staking the same real bet twice."""
    return (leg.game_id, leg.participant_id, leg.market_family, leg.threshold, leg.side)


def dedupe_legs_by_economic_identity(legs: list[ParlayLeg]) -> list[ParlayLeg]:
    """Collapses legs that represent the SAME real bet down to one --
    keeping the one with the LATEST captured_at_utc (the freshest real
    quote actually priced this leg last), falling back to the first-seen
    leg when neither/both timestamps are missing or tied, so the choice is
    deterministic rather than dict-ordering-dependent."""
    best: dict[tuple, ParlayLeg] = {}
    for leg in legs:
        key = _economic_identity(leg)
        current = best.get(key)
        if current is None or (leg.captured_at_utc or "") > (current.captured_at_utc or ""):
            best[key] = leg
    return list(best.values())


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


LegIdentity = tuple


def leg_identity(leg: ParlayLeg) -> LegIdentity:
    """Economic identity of a leg, line included, price excluded: the same
    bet at a moved price is the same leg."""
    return (leg.game_id, leg.participant_id, leg.market_family, leg.threshold, leg.side)


def leg_decimal(leg: ParlayLeg) -> float:
    a = leg.american_price
    return 1.0 + (a / 100.0 if a > 0 else 100.0 / abs(a))


def leg_label(leg: ParlayLeg) -> str:
    if leg.market_family == "MONEYLINE":
        return f"{leg.participant_name} to win"
    if leg.market_family in ("PLAYER_SOG_ALTERNATE", "PLAYER_SOG"):
        return f"{leg.participant_name} {leg.threshold}+ shots on goal"
    if leg.market_family == "GOALIE_SAVES":
        return f"{leg.participant_name} {leg.threshold}+ saves"
    if leg.market_family == "PLAYER_POINTS":
        return f"{leg.participant_name} {leg.threshold}+ point{'s' if leg.threshold != 1 else ''}"
    return f"{leg.participant_name} {leg.market_family} {leg.threshold}"


@dataclass(frozen=True)
class ParlayResult:
    legs: list[ParlayLeg]
    joint_probability: float
    fair_combo_price: float          # the MODEL's own fair price for the joint probability
    estimated_combo_price: float     # product of each leg's OWN real price -- never a DK SGP quote
    combo_edge: float                # joint_probability - product of each leg's own raw implied prob
    combined_decimal: float = 0.0
    ev_estimated: float = 0.0        # P_joint * decimal - 1
    ev_conservative: float = 0.0     # same, with every leg's probability lowered by LEG_PROBABILITY_MARGIN
    offered_parlay_price: None = field(default=None)  # NEVER fabricated -- see module docstring


def _evaluate_combo(legs: list[ParlayLeg]) -> ParlayResult | None:
    """None means this leg group is not a candidate at all (shares a game)."""
    if legs_share_a_game(legs):
        return None
    jp = joint_probability(legs)
    decimal_price = 1.0
    for leg in legs:
        decimal_price *= leg_decimal(leg)
    p_low = 1.0
    for leg in legs:
        p_low *= max(leg.conservative_probability - LEG_PROBABILITY_MARGIN, 0.0)
    implied = 1.0 / decimal_price
    return ParlayResult(
        legs=legs, joint_probability=jp,
        fair_combo_price=odds_math.prob_to_american(jp),
        estimated_combo_price=odds_math.prob_to_american(implied),
        combo_edge=jp - implied,
        combined_decimal=decimal_price,
        ev_estimated=jp * decimal_price - 1.0,
        ev_conservative=p_low * decimal_price - 1.0,
    )


def leg_has_edge(leg: ParlayLeg) -> bool:
    return leg.conservative_probability * leg_decimal(leg) - 1.0 > 0.0 and leg_decimal(leg) >= MIN_LEG_DECIMAL


def ticket_passes_policy(combo: ParlayResult) -> bool:
    return (combo.combined_decimal >= MIN_COMBINED_DECIMAL
            and combo.ev_estimated >= MIN_ESTIMATED_EV
            and combo.ev_conservative >= 0.0)


def _qualifying_tickets(pool: list[ParlayLeg]) -> list[ParlayResult]:
    """Every cross-game ticket of 2..MAX_LEGS legs that passes the policy and
    is not made redundant by a passing sub-ticket (longer tickets must be
    needed, not merely possible)."""
    passing: dict[frozenset, ParlayResult] = {}
    ordered = sorted(pool, key=lambda l: -(l.conservative_probability * leg_decimal(l)))
    for size in range(MIN_LEGS, MAX_LEGS + 1):
        candidates = ordered if size == MIN_LEGS else ordered[:MAX_POOL_FOR_LONG_TICKETS]
        for group in combinations(candidates, size):
            keys = [leg_identity(l) for l in group]
            if size > MIN_LEGS and any(
                    frozenset(sub) in passing
                    for n in range(MIN_LEGS, size) for sub in combinations(keys, n)):
                continue
            combo = _evaluate_combo(list(group))
            if combo is None or not ticket_passes_policy(combo):
                continue
            passing[frozenset(keys)] = combo
    return list(passing.values())


def _prepare_pool(candidate_legs: list[ParlayLeg]) -> list[ParlayLeg]:
    eligible = dedupe_legs_by_economic_identity([l for l in candidate_legs if leg_is_eligible(l)])
    return [l for l in eligible if leg_has_edge(l)]


def selection_funnel(candidate_legs: list[ParlayLeg]) -> dict:
    """Counts at each selection stage, so an empty or short board can be explained
    exactly (diagnostic only; selection itself is select_tickets)."""
    eligible = dedupe_legs_by_economic_identity([l for l in candidate_legs if leg_is_eligible(l)])
    pool = [l for l in eligible if leg_has_edge(l)]
    f = {"legs_offered": len(candidate_legs), "legs_eligible": len(eligible),
         "legs_failed_eligibility": len(candidate_legs) - len(eligible),
         "legs_without_positive_edge": len(eligible) - len(pool), "legs_in_pool": len(pool),
         "two_leg_pairs_cross_game": 0, "pairs_reaching_plus_100": 0, "pairs_with_ev_at_least_min": 0,
         "pairs_passing_haircut": 0, "best_pair_by_ev": None}
    best = None
    for a, b in combinations(pool, 2):
        combo = _evaluate_combo([a, b])
        if combo is None:
            continue
        f["two_leg_pairs_cross_game"] += 1
        if combo.combined_decimal < MIN_COMBINED_DECIMAL:
            continue
        f["pairs_reaching_plus_100"] += 1
        if combo.ev_estimated < MIN_ESTIMATED_EV:
            continue
        f["pairs_with_ev_at_least_min"] += 1
        if combo.ev_conservative >= 0.0:
            f["pairs_passing_haircut"] += 1
    for a, b in combinations(pool, 2):
        combo = _evaluate_combo([a, b])
        if combo is not None and combo.combined_decimal >= MIN_COMBINED_DECIMAL and (
                best is None or combo.ev_conservative > best.ev_conservative):
            best = combo
    if best is not None:
        f["best_pair_by_ev"] = {"legs": [leg_label(l) for l in best.legs], "combined_decimal": round(best.combined_decimal, 3),
                                "ev_estimated": round(best.ev_estimated, 4), "ev_after_haircut": round(best.ev_conservative, 4)}
    return f


def select_singles(candidate_legs: list[ParlayLeg], limit: int = MAX_SINGLES) -> list[ParlayLeg]:
    """Informational single-leg ideas at +100 or better under the same EV
    policy. Not staked, and not counted toward the daily parlay tickets."""
    singles = []
    for leg in _prepare_pool(candidate_legs):
        d = leg_decimal(leg)
        if (d >= MIN_COMBINED_DECIMAL and leg.conservative_probability * d - 1.0 >= MIN_ESTIMATED_EV
                and (leg.conservative_probability - LEG_PROBABILITY_MARGIN) * d - 1.0 >= 0.0):
            singles.append(leg)
    singles.sort(key=lambda l: (-l.conservative_probability, -(l.conservative_probability * leg_decimal(l))))
    return singles[:limit]


def select_tickets(candidate_legs: list[ParlayLeg], *, existing: list[list[LegIdentity]] | None = None,
                   max_tickets: int = MAX_TICKETS_PER_DAY) -> dict:
    """Pick up to `max_tickets` NEW tickets, given the leg sets already
    recorded today (`existing`; they count toward every exposure limit and can
    never be re-selected). Ranked by hit probability, then EV, among tickets
    that pass the policy. Returns
      {"tickets": [ParlayResult...], "pool_size": int, "qualifying": int, "reason": str|None}
    `reason` explains an empty or short result; it is never padded."""
    existing = existing or []
    pool = _prepare_pool(candidate_legs)
    result = {"tickets": [], "pool_size": len(pool), "qualifying": 0, "reason": None}
    if max_tickets <= 0:
        result["reason"] = "all of today's ticket slots are already used"
        return result
    if len(pool) < MIN_LEGS:
        result["reason"] = (f"only {len(pool)} eligible leg(s) with a positive edge; a ticket needs at least "
                            f"{MIN_LEGS} legs from different games")
        return result
    qualifying = _qualifying_tickets(pool)
    result["qualifying"] = len(qualifying)
    if not qualifying:
        result["reason"] = (f"no cross-game combination reaches +100 with estimated EV >= "
                            f"{MIN_ESTIMATED_EV:.0%} that also survives the uncertainty haircut")
        return result

    taken_sets = {frozenset(e) for e in existing}
    leg_use: dict = {}
    game_use: dict = {}
    ml_side: dict = {}
    for e in existing:
        for ident in e:
            leg_use[ident] = leg_use.get(ident, 0) + 1
            game_use[ident[0]] = game_use.get(ident[0], 0) + 1
            if ident[2] == "MONEYLINE":
                ml_side[ident[0]] = ident[1]

    qualifying.sort(key=lambda c: (-c.joint_probability, -c.ev_estimated, len(c.legs)))
    blocked = 0
    for combo in qualifying:
        if len(result["tickets"]) >= max_tickets:
            break
        idents = [leg_identity(l) for l in combo.legs]
        if frozenset(idents) in taken_sets:
            continue
        if any(leg_use.get(i, 0) >= MAX_TICKETS_PER_LEG for i in idents):
            blocked += 1
            continue
        if any(game_use.get(i[0], 0) >= MAX_TICKETS_PER_GAME for i in idents):
            blocked += 1
            continue
        if any(i[2] == "MONEYLINE" and ml_side.get(i[0], i[1]) != i[1] for i in idents):
            blocked += 1
            continue
        result["tickets"].append(combo)
        taken_sets.add(frozenset(idents))
        for i in idents:
            leg_use[i] = leg_use.get(i, 0) + 1
            game_use[i[0]] = game_use.get(i[0], 0) + 1
            if i[2] == "MONEYLINE":
                ml_side[i[0]] = i[1]
    if len(result["tickets"]) < max_tickets:
        if result["tickets"]:
            result["reason"] = (f"only {len(result['tickets'])} more ticket(s) qualified; the other qualifying "
                                f"combinations were duplicates or would exceed the shared-exposure limits")
        else:
            result["reason"] = (f"{len(qualifying)} qualifying ticket(s) exist but all are already recorded or "
                                f"would exceed the shared-exposure limits")
    return result


# --- compatibility wrappers (old return shapes; the logic is select_tickets) ----------------

def build_top_real_market_parlays(candidate_legs: list[ParlayLeg], max_parlays: int = MAX_TICKETS_PER_DAY) -> dict:
    picked = select_tickets(candidate_legs, max_tickets=max_parlays)
    if not picked["tickets"]:
        return {"status": "NO_QUALIFYING_PARLAY", "reason": picked["reason"]}
    return {"status": "QUALIFIED",
            "parlays": [{"recommended_legs": len(c.legs), "combo": c} for c in picked["tickets"]]}


def build_real_market_parlay(candidate_legs: list[ParlayLeg]) -> dict:
    result = build_top_real_market_parlays(candidate_legs, max_parlays=1)
    if result["status"] != "QUALIFIED":
        return result
    entry = result["parlays"][0]
    return {"status": "QUALIFIED", "recommended_legs": entry["recommended_legs"], "combo": entry["combo"]}
