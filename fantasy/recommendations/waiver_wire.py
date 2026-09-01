"""
Waiver wire ranking (Part 42-46). Ranks REAL available players (never a
rostered player -- Part 42's own explicit rule) by a presentation-level
ADD PRIORITY score (Part 46 -- a ranking score, NOT a probability,
exactly the same design principle as the betting engine's own
conviction_score, reimplemented independently here rather than shared,
per this package's isolation rule).
"""
from __future__ import annotations

from dataclasses import dataclass

from fantasy.league.scoring import CategoryProjection
from fantasy.league.settings import LeagueSettings
from fantasy.recommendations.lineup_optimizer import player_value


@dataclass(frozen=True)
class AvailablePlayer:
    player_id: str
    name: str
    eligible_positions: tuple[str, ...]
    projections: dict[str, CategoryProjection]
    raw_games_next_7: int | None = None
    startable_games_next_7: int | None = None
    role_confidence: str = "MEDIUM"  # HIGH/MEDIUM/LOW -- role/PP stability


@dataclass(frozen=True)
class WaiverCard:
    player_id: str
    name: str
    add_priority: float  # 0-100 presentation ranking, never a probability
    projected_value: float
    startable_games_next_7: int | None
    role_confidence: str
    why: str


_ROLE_CONFIDENCE_WEIGHT = {"HIGH": 1.0, "MEDIUM": 0.65, "LOW": 0.3}


def add_priority_score(settings: LeagueSettings, player: AvailablePlayer, *, max_observed_value: float) -> float:
    """0-100 ranking score (Part 46): value component (normalized
    against the strongest available player in this pool, so the scale
    is always meaningful for THIS waiver wire, not an arbitrary global
    constant) + a startability component + a role-confidence multiplier.
    Never a probability -- callers must not present this as one."""
    value = player_value(settings, player.projections)
    value_component = max(0.0, value) / max_observed_value if max_observed_value > 0 else 0.0
    value_component = min(value_component, 1.0)

    startability_component = 0.5
    if player.startable_games_next_7 is not None:
        startability_component = min(player.startable_games_next_7 / 4.0, 1.0)

    role_weight = _ROLE_CONFIDENCE_WEIGHT.get(player.role_confidence, 0.5)

    raw = (0.55 * value_component + 0.25 * startability_component) * role_weight * 100
    return round(min(max(raw, 0.0), 100.0), 1)


def rank_waiver_wire(settings: LeagueSettings, available_players: list[AvailablePlayer],
                      *, top_n: int = 25) -> list[WaiverCard]:
    """Never includes a rostered player -- callers are responsible for
    passing ONLY the real Yahoo-confirmed available-player pool (Part
    42/43); this function does not itself fetch or filter against
    roster state."""
    if not available_players:
        return []

    values = {p.player_id: player_value(settings, p.projections) for p in available_players}
    max_value = max(values.values(), default=0.0)

    cards = []
    for p in available_players:
        score = add_priority_score(settings, p, max_observed_value=max_value)
        why_parts = [f"projected value {values[p.player_id]:.2f}"]
        if p.startable_games_next_7 is not None:
            why_parts.append(f"{p.startable_games_next_7} startable game(s) next 7 days")
        why_parts.append(f"{p.role_confidence.lower()} role confidence")
        cards.append(WaiverCard(
            player_id=p.player_id, name=p.name, add_priority=score, projected_value=values[p.player_id],
            startable_games_next_7=p.startable_games_next_7, role_confidence=p.role_confidence,
            why="; ".join(why_parts),
        ))
    cards.sort(key=lambda c: -c.add_priority)
    return cards[:top_n]


def suggest_drop_pairing(waiver_card: WaiverCard, current_roster_values: dict[str, float],
                          *, min_margin: float = 0.0) -> str | None:
    """Part 45: pairs a recommended add with the weakest real roster
    player it would upgrade over, if any. Returns None (never a
    fabricated pairing) if no current roster player has genuinely lower
    projected value than this waiver add."""
    if not current_roster_values:
        return None
    weakest_id = min(current_roster_values, key=lambda pid: current_roster_values[pid])
    weakest_value = current_roster_values[weakest_id]
    if waiver_card.projected_value > weakest_value + min_margin:
        return weakest_id
    return None
