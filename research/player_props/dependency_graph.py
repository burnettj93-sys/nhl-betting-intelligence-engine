"""
Part 31: machine-readable dependency graph -- which foundational
PROCESSES each canonical market depends on, and which processes
themselves depend on other processes. Two separate graphs, both DAGs
(checked directly, not assumed):

  MARKET_PROCESS_DEPENDENCIES: market_id -> tuple of process names it needs
    (already present on every research.player_props.market_registry.MarketDefinition
    as `underlying_process` -- this module doesn't duplicate that, it
    re-derives the market-level view from the registry itself so the two
    can never drift apart).

  PROCESS_DEPENDENCY_GRAPH: process -> tuple of OTHER processes it itself
    requires before it can be built (Part 31's own worked examples,
    e.g. FIRST_GOAL_SCORER <- PLAYER_GOAL_HAZARD <- EVENT_TIMELINE,
    generalized to this project's actual 17 process names).
"""
from __future__ import annotations

from research.player_props import market_registry as mr

# Part 31: process-level prerequisites (a process may itself require
# other, more foundational processes before it can be built).
PROCESS_DEPENDENCY_GRAPH: dict[str, tuple[str, ...]] = {
    "PLAYER_ACTIVE_ROLE_TOI": (),  # foundational -- already built, shared by every prop this session
    "PLAYER_SHOT_GENERATION": ("PLAYER_ACTIVE_ROLE_TOI",),
    "PLAYER_BLOCK_EVENT_GENERATION": ("PLAYER_ACTIVE_ROLE_TOI",),
    "PLAYER_HIT_EVENT_GENERATION": ("PLAYER_ACTIVE_ROLE_TOI",),
    "PLAYER_GOAL_GENERATION": ("PLAYER_ACTIVE_ROLE_TOI", "PLAYER_SHOT_GENERATION"),
    "GOAL_ASSIST_POINT_ATTRIBUTION": ("PLAYER_ACTIVE_ROLE_TOI", "PLAYER_GOAL_GENERATION"),
    "SPECIAL_TEAMS_STATE": ("PLAYER_ACTIVE_ROLE_TOI",),
    "PENALTY_PROCESS": ("PLAYER_ACTIVE_ROLE_TOI",),
    "FACEOFF_PROCESS": ("PLAYER_ACTIVE_ROLE_TOI",),
    "GOALIE_WORKLOAD_SAVE_PROCESS": ("TEAM_SHOT_GENERATION",),
    "TEAM_SHOT_GENERATION": ("PLAYER_SHOT_GENERATION",),
    "TEAM_GOAL_GENERATION": ("PLAYER_GOAL_GENERATION", "TEAM_SHOT_GENERATION"),
    "PERIOD_EVENT_TIMING": ("PLAYER_ACTIVE_ROLE_TOI",),  # requires real play-by-play, orthogonal to the count models above
    "GAME_SCORE_STATE": ("TEAM_GOAL_GENERATION", "PERIOD_EVENT_TIMING"),
    "EMPTY_NET_STATE": ("GAME_SCORE_STATE", "PERIOD_EVENT_TIMING"),
    "OT_SHOOTOUT_STATE": ("GAME_SCORE_STATE",),
    "JOINT_DEPENDENCE_SIMULATION": (
        "TEAM_GOAL_GENERATION", "GAME_SCORE_STATE", "SPECIAL_TEAMS_STATE",
        "GOALIE_WORKLOAD_SAVE_PROCESS", "PERIOD_EVENT_TIMING", "EMPTY_NET_STATE", "OT_SHOOTOUT_STATE",
    ),  # the eventual full game simulator (Part 28) -- sits at the top of the graph by construction
}


def market_process_dependencies() -> dict[str, tuple[str, ...]]:
    return {m.market_id: m.underlying_process for m in mr.CANONICAL_MARKETS}


def _all_process_dependencies(process: str, _seen: set[str] | None = None) -> set[str]:
    """Transitive closure of a process's prerequisites."""
    seen = _seen if _seen is not None else set()
    for dep in PROCESS_DEPENDENCY_GRAPH.get(process, ()):
        if dep not in seen:
            seen.add(dep)
            _all_process_dependencies(dep, seen)
    return seen


def is_acyclic() -> bool:
    """DFS-based cycle check over PROCESS_DEPENDENCY_GRAPH. A process
    that transitively depends on itself would indicate a real design
    error -- checked directly, not assumed."""
    for process in PROCESS_DEPENDENCY_GRAPH:
        if process in _all_process_dependencies(process):
            return False
    return True


def market_transitive_processes(market_id: str) -> set[str]:
    """All processes (direct + transitive) a market ultimately depends on."""
    entry = mr.get(market_id)
    if entry is None:
        raise KeyError(market_id)
    out: set[str] = set()
    for p in entry.underlying_process:
        out.add(p)
        out |= _all_process_dependencies(p)
    return out


def unfinished_process_leverage() -> dict[str, int]:
    """Part 33: for each NOT-yet-fully-validated process, how many
    canonical markets (direct dependency only, matching
    market_registry.market_leverage_counts()) would it unlock or
    materially advance."""
    return mr.market_leverage_counts()


# Multi-Season PBP Expansion slice, Part 36: READINESS METADATA ONLY.
# PROCESS_DEPENDENCY_GRAPH above is left structurally untouched -- the
# 4-season contract-drift audit found no real finding that requires
# correcting a prerequisite edge. This is a SEPARATE annotation answering
# only "does real historical event-level data now exist to support this
# process," never "has a model for this process been built" (that remains
# tracked per-market via market_registry.py's model_status, untouched
# here). DATA_FOUNDATION_READY on PERIOD_EVENT_TIMING means exactly that --
# it must NEVER be read as "PERIOD_EVENT_TIMING model built."
PROCESS_DATA_FOUNDATION_STATUS: dict[str, str] = {
    "PLAYER_ACTIVE_ROLE_TOI": "DATA_FOUNDATION_READY",       # pre-existing, unaffected by this slice
    "PLAYER_SHOT_GENERATION": "DATA_FOUNDATION_READY",        # pre-existing (MoneyPuck), now also PBP-backed
    "PLAYER_BLOCK_EVENT_GENERATION": "DATA_FOUNDATION_READY",
    "PLAYER_HIT_EVENT_GENERATION": "DATA_FOUNDATION_READY",   # newly PBP-backed this slice
    "PLAYER_GOAL_GENERATION": "DATA_FOUNDATION_READY",
    "GOAL_ASSIST_POINT_ATTRIBUTION": "DATA_FOUNDATION_READY",
    "SPECIAL_TEAMS_STATE": "DATA_FOUNDATION_READY",           # newly PBP-backed this slice (situationCode)
    "PENALTY_PROCESS": "DATA_FOUNDATION_READY",               # newly PBP-backed this slice
    "FACEOFF_PROCESS": "DATA_FOUNDATION_READY",               # newly PBP-backed this slice
    "GOALIE_WORKLOAD_SAVE_PROCESS": "DATA_FOUNDATION_READY",  # Event-Timing Utility Closure slice: period-level
                                                                # goalie-tenure reconstruction now ready too (was
                                                                # PARTIAL; see PROCESS_READINESS_NOTES below)
    "TEAM_SHOT_GENERATION": "DATA_FOUNDATION_READY",
    "TEAM_GOAL_GENERATION": "DATA_FOUNDATION_READY",
    "PERIOD_EVENT_TIMING": "DATA_FOUNDATION_READY",           # this slice's primary target -- DATA ONLY, no model
    "GAME_SCORE_STATE": "DATA_FOUNDATION_READY",              # newly PBP-backed this slice (score reconstruction)
    "EMPTY_NET_STATE": "DATA_FOUNDATION_READY",               # newly PBP-backed this slice (joint two-signal rule)
    "OT_SHOOTOUT_STATE": "DATA_FOUNDATION_READY",             # newly PBP-backed this slice
    "JOINT_DEPENDENCE_SIMULATION": "NOT_APPLICABLE",          # every data prerequisite is now ready, but the
                                                                # simulator itself remains entirely NOT_BUILT --
                                                                # deliberately NOT marked READY to avoid implying
                                                                # the simulator exists (Part 36's explicit ban)
}


def process_data_foundation_status(process: str) -> str:
    return PROCESS_DATA_FOUNDATION_STATUS.get(process, "NOT_APPLICABLE")


# Event-Timing Utility Closure slice, Part 24: a short, human-readable note
# per process about what SPECIFIC derivation is now ready -- additive only,
# PROCESS_DEPENDENCY_GRAPH's structure and PROCESS_DATA_FOUNDATION_STATUS's
# existing values are otherwise untouched by this slice except where noted.
PROCESS_READINESS_NOTES: dict[str, str] = {
    "GOALIE_WORKLOAD_SAVE_PROCESS": (
        "Event-level goalie-tenure reconstruction ready (research/real_nhl_pbp/goalie_tenure.py): "
        "mid-period substitutions, empty-net pulls, and same-goalie returns are all correctly "
        "distinguished. Period and full-game saves both derive from this -- see "
        "EVENT_TIMING_UTILITY_CLOSURE_REPORT.md."
    ),
    "GAME_SCORE_STATE": (
        "Game-winning-goal deterministic derivation ready (research/real_nhl_pbp/gwg.py): the exact "
        "final-score-dependent NHL definition, corpus-validated with 0 invariant violations across "
        "4,875 non-shootout games."
    ),
}
