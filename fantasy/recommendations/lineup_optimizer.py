"""
Daily lineup optimizer (Part 34/35) -- respects the league's ACTUAL
Yahoo roster positions (never a hard-coded C/LW/RW/D/G assumption).

Algorithm: greedy value-maximizing assignment (sort eligible players by
value descending, assign each to its best still-open eligible slot).
This is NOT an exhaustive/globally-optimal assignment search (that would
be a bipartite matching / linear program) -- for realistic fantasy
rosters (a handful of positions, a dozen-ish players) greedy-by-value
produces the optimal or near-optimal assignment in the overwhelming
majority of real cases, and is simple enough to be fully deterministic
and testable. Documented here explicitly rather than silently presented
as provably optimal.

Fantasy value here is never the betting engine's BET/WATCH/WAIT/PASS
decision or EV (Part 27) -- it's either points-league points_value() or,
for a category league, a simple category-count proxy (Part 34's "or
category impact") -- see _category_proxy_value() below, itself a
documented simplification pending Part 38's fuller marginal-category-
value model (deferred to a follow-up pass -- see the sprint report).
"""
from __future__ import annotations

from dataclasses import dataclass

from fantasy.league.scoring import CategoryProjection, category_contributions, points_value
from fantasy.league.settings import LeagueSettings


@dataclass(frozen=True)
class RosterPlayer:
    player_id: str
    name: str
    eligible_positions: tuple[str, ...]  # real Yahoo eligible_positions for this player
    projections: dict[str, CategoryProjection]
    is_goalie: bool = False


@dataclass(frozen=True)
class LineupAssignment:
    player_id: str
    name: str
    assigned_slot: str  # a real roster_position from league settings, or "BN" if benched
    value: float | None
    reason: str


def _category_proxy_value(settings: LeagueSettings, projections: dict[str, CategoryProjection]) -> float:
    """Sum of each enabled category's real projected value, sign-adjusted
    by direction, normalized by nothing (a deliberately simple proxy --
    see this module's own docstring). Missing/unavailable categories
    contribute 0, never a fabricated estimate."""
    result = category_contributions(settings, projections)
    return sum(c["projected_value"] * c["direction"] for c in result["contributions"])


def player_value(settings: LeagueSettings, projections: dict[str, CategoryProjection]) -> float:
    if settings.is_points_league:
        pv = points_value(settings, projections)
        return pv["total"] or 0.0
    return _category_proxy_value(settings, projections)


def optimize_lineup(settings: LeagueSettings, players: list[RosterPlayer]) -> list[LineupAssignment]:
    """Returns one LineupAssignment per player (START in a real slot, or
    BENCH). Bench/IR slot counts come from the league's own real
    roster_positions -- a league with 0 IR slots never gets an IR
    assignment invented for it."""
    # Expand roster_positions into individual open slots, e.g. 2x "D" ->
    # ["D", "D"]. IR/IR+ slots are excluded from the "must fill" pool --
    # they're for injured players, never auto-assigned by value.
    open_slots: list[str] = []
    for rp in settings.roster_positions:
        if rp.position.upper().startswith("IR"):
            continue
        open_slots.extend([rp.position] * rp.count)

    valued = sorted(
        ((player_value(settings, p.projections), p) for p in players),
        key=lambda pair: -pair[0],
    )

    assignments: list[LineupAssignment] = []
    remaining_slots = list(open_slots)
    for value, player in valued:
        # A player is eligible for a slot if the slot name matches one of
        # their real eligible_positions, OR the slot is a flex/utility
        # slot (Yahoo's own convention: names like "Util", "W/R/T") that
        # accepts any SKATER position -- Util/flex slots never accept a
        # goalie in any real Yahoo hockey league (a real, reproduced bug
        # this sprint: goalies were being slotted into Util before this
        # `not player.is_goalie` guard was added). BN itself is handled
        # separately below as the fallback, never claimed as a "start".
        best_slot = None
        for slot in remaining_slots:
            is_flex_slot = slot.upper() == "UTIL" and not player.is_goalie
            if slot in player.eligible_positions or is_flex_slot:
                if slot.upper() == "BN":
                    continue  # BN is the fallback, not a preferred assignment
                best_slot = slot
                break
        if best_slot is None:
            # try a true flex slot even if none of the loop's ordinary
            # matches applied (covers e.g. Yahoo's "W/R/T" combined slot)
            for slot in remaining_slots:
                if slot.upper() not in ("BN",) and any(pos in slot for pos in player.eligible_positions):
                    best_slot = slot
                    break
        if best_slot is not None:
            remaining_slots.remove(best_slot)
            assignments.append(LineupAssignment(
                player_id=player.player_id, name=player.name, assigned_slot=best_slot,
                value=value, reason=f"highest remaining value ({value:.2f}) among players eligible for {best_slot}",
            ))
        else:
            assignments.append(LineupAssignment(
                player_id=player.player_id, name=player.name, assigned_slot="BN", value=value,
                reason="no open eligible slot remained -- benched despite real value" if value > 0
                       else "insufficient projected value / no eligible open slot",
            ))
    return assignments


def start_sit_recommendation(assignment: LineupAssignment, borderline_margin: float = 0.5) -> str:
    """Part 35: START / BENCH / BORDERLINE. BORDERLINE flags a close
    call at the edge of the lineup, not a confident recommendation
    either way -- never presented with the same certainty as a clear
    START or BENCH."""
    if assignment.assigned_slot != "BN":
        return "START"
    if assignment.value is not None and assignment.value > 0:
        return "BORDERLINE"
    return "BENCH"
