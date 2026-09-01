"""
Goalie fantasy value (Part 64-68). Thin wrapper around
fantasy.projections.fantasy_projection.project_goalie_categories() that
adds the explicit starter-certainty disclosure Part 65 requires.

Only `goalie_saves` has a real per-game projection model in this engine
(Part 66) -- Wins/GAA/SV%/Shutouts have no validated model here, so a
league scoring those categories gets an honest reduced-completeness
projection (Part 15), never a fabricated number for them.
"""
from __future__ import annotations

from dataclasses import dataclass

from fantasy.league.scoring import CategoryProjection
from fantasy.projections.fantasy_projection import project_goalie_categories


@dataclass(frozen=True)
class GoalieFantasyValue:
    goalie_id: str
    name: str
    projected_starter: bool
    start_certainty: float | None  # the engine's own real starter_probability -- never a live-confirmed feed
    projections: dict[str, CategoryProjection]
    disclosure: str


HONEST_STARTER_DISCLOSURE = (
    "This engine has no verified live confirmed-starter feed -- start_certainty is a real, "
    "PIT-safe PROJECTION from recent rotation history, never a live lineup confirmation."
)


def build_goalie_fantasy_value(goalie_id: str, name: str, expected_saves: float | None,
                                starter_probability: float | None, confidence: str) -> GoalieFantasyValue:
    projections = project_goalie_categories(expected_saves, confidence)
    projected_starter = bool(starter_probability is not None and starter_probability >= 0.5)
    return GoalieFantasyValue(
        goalie_id=goalie_id, name=name, projected_starter=projected_starter,
        start_certainty=starter_probability, projections=projections, disclosure=HONEST_STARTER_DISCLOSURE,
    )
