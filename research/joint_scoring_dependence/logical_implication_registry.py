"""
Part 47: a machine-readable, research-level logical implication graph
for the scoring/contribution event family -- deliberately separate from
both the sportsbook market registry (research/player_props/
market_registry.py, untouched by this slice) and the research-level
JOINT_DEPENDENCE_REGISTRY (which tracks per-COMBINATION validation
status, not the underlying logical relationships themselves).

This graph is the single source of truth future work should query to
answer "does event A structurally guarantee event B" -- reusable by
future SGP pricing, a parlay optimizer's redundant-leg detection, joint
simulation, and general coherence checking (Part 47's own stated
purposes). It is intentionally a plain directed graph (event label ->
list of implied event labels), not a class hierarchy, so it stays trivial
to serialize, diff, and extend.
"""
from __future__ import annotations

IMPLICATION_GRAPH: dict[str, list[str]] = {
    "GOAL_1_PLUS": ["POINT_1_PLUS", "SOG_1_PLUS"],
    "ASSIST_1_PLUS": ["POINT_1_PLUS"],
}


def implies(event_a: str, event_b: str) -> bool:
    """True if event_a logically guarantees event_b (directly -- this
    graph is small enough that transitive closure is unnecessary today;
    add it if the graph grows beyond 2 hops)."""
    return event_b in IMPLICATION_GRAPH.get(event_a, [])


def implied_by(event_b: str) -> list[str]:
    """Every event_a that logically implies event_b."""
    return [a for a, implied in IMPLICATION_GRAPH.items() if event_b in implied]


def detect_redundant_leg(events: list[str]) -> str | None:
    """Given a list of event labels forming one combination, returns the
    label of the first leg found to be fully redundant (logically
    implied by another leg already present) -- Part 31's own
    requirement, applied automatically. Returns None if the combination
    has no internal redundancy."""
    for a in events:
        for b in events:
            if a != b and implies(a, b):
                return b
    return None


def minimal_equivalent_combination(events: list[str]) -> list[str]:
    """Strips every redundant leg from a combination, returning the
    smallest logically-equivalent event list -- e.g.
    ["SOG_1_PLUS", "GOAL_1_PLUS", "POINT_1_PLUS"] -> ["SOG_1_PLUS", "GOAL_1_PLUS"]
    since GOAL_1_PLUS already implies POINT_1_PLUS."""
    kept = list(events)
    changed = True
    while changed:
        changed = False
        redundant = detect_redundant_leg(kept)
        if redundant is not None:
            kept.remove(redundant)
            changed = True
    return kept
