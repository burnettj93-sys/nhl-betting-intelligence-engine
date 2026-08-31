"""
Part 21: event-level invariants applicable to real (not simulated) event
data. These are the subset of SIMULATION_INVARIANTS.md's invariants that
can be checked directly against an ARCHIVED REAL GAME's normalized events
today, ahead of any future simulator existing. Each function returns a
list of violation dicts (empty list == invariant holds); nothing raises,
so a caller can run every check and report the full violation set rather
than stopping at the first failure (Part 37 spirit: report, don't hide).
"""
from __future__ import annotations

from research.real_nhl_pbp.normalize import (
    REGULAR_SEASON_OT_SECONDS,
    REGULATION_PERIOD_SECONDS,
    reconstruct_statistical_score,
)
from research.real_nhl_pbp.schema import PbpEvent


def check_event_times_within_period_bounds(events: list[PbpEvent]) -> list[dict]:
    violations = []
    for ev in events:
        if ev.period_type == "SO":
            if ev.seconds_elapsed_in_period != 0:
                violations.append({"event_id": ev.event_id, "reason": "SO event has non-zero clock time"})
            continue
        limit = REGULATION_PERIOD_SECONDS if ev.period_type == "REG" else REGULAR_SEASON_OT_SECONDS
        if not (0 <= ev.seconds_elapsed_in_period <= limit):
            violations.append({
                "event_id": ev.event_id, "reason": "elapsed time outside period bounds",
                "seconds_elapsed_in_period": ev.seconds_elapsed_in_period, "limit": limit,
            })
    return violations


def check_event_order_deterministic(events: list[PbpEvent]) -> list[dict]:
    seqs = [e.event_sequence for e in events]
    violations = []
    if seqs != sorted(seqs):
        violations.append({"reason": "event_sequence not sorted ascending after normalize_game_events()"})
    if len(set(seqs)) != len(seqs):
        violations.append({"reason": "duplicate event_sequence values within one game"})
    return violations


def check_goals_increment_once(events: list[PbpEvent]) -> list[dict]:
    goal_events = [e for e in events if e.event_type == "goal"]
    ids = [e.event_id for e in goal_events]
    violations = []
    if len(set(ids)) != len(ids):
        violations.append({"reason": "duplicate goal event_id -- would double-count a score increment"})
    return violations


def check_shootout_excluded_from_statistical(events: list[PbpEvent]) -> list[dict]:
    violations = []
    for ev in events:
        if ev.period_type == "SO" and ev.event_type == "goal" and ev.is_statistical:
            violations.append({"event_id": ev.event_id, "reason": "SO goal marked is_statistical=True"})
        if ev.period_type != "SO" and ev.event_type == "goal" and not ev.is_statistical:
            violations.append({"event_id": ev.event_id, "reason": "non-SO goal marked is_statistical=False"})
    return violations


def check_player_goals_reconcile_with_team_goals(events: list[PbpEvent], home_team_id: int,
                                                  away_team_id: int) -> list[dict]:
    violations = []
    scorer_home = sum(1 for e in events if e.event_type == "goal" and e.is_statistical
                       and e.team_id == home_team_id and "scorer" in e.players)
    scorer_away = sum(1 for e in events if e.event_type == "goal" and e.is_statistical
                       and e.team_id == away_team_id and "scorer" in e.players)
    team_home = sum(1 for e in events if e.event_type == "goal" and e.is_statistical and e.team_id == home_team_id)
    team_away = sum(1 for e in events if e.event_type == "goal" and e.is_statistical and e.team_id == away_team_id)
    if scorer_home != team_home:
        violations.append({"reason": "home scorer-attributed goals != home statistical goal count",
                            "scorer_count": scorer_home, "team_count": team_home})
    if scorer_away != team_away:
        violations.append({"reason": "away scorer-attributed goals != away statistical goal count",
                            "scorer_count": scorer_away, "team_count": team_away})
    return violations


def check_assists_only_on_goals_max_two(events: list[PbpEvent]) -> list[dict]:
    violations = []
    for ev in events:
        has_assist = "assist1" in ev.players or "assist2" in ev.players
        if has_assist and ev.event_type != "goal":
            violations.append({"event_id": ev.event_id, "reason": "assist role present on non-goal event"})
        if "assist3PlayerId" in ev.raw_details:
            violations.append({"event_id": ev.event_id, "reason": "more than two assists on one goal"})
        if "assist2" in ev.players and "assist1" not in ev.players:
            violations.append({"event_id": ev.event_id, "reason": "assist2 present without assist1"})
    return violations


def check_period_goal_totals_reconcile(events: list[PbpEvent]) -> list[dict]:
    goal_events = [e for e in events if e.event_type == "goal" and e.is_statistical]
    by_period = {}
    for e in goal_events:
        by_period[e.period_number] = by_period.get(e.period_number, 0) + 1
    total_from_periods = sum(by_period.values())
    violations = []
    if total_from_periods != len(goal_events):
        violations.append({"reason": "sum of period goal totals != total statistical goal count",
                            "by_period": by_period, "total_statistical_goals": len(goal_events)})
    return violations


def check_final_score_reconciles(events: list[PbpEvent], home_team_id: int, away_team_id: int,
                                  expected_home_score: int, expected_away_score: int,
                                  final_period_type: str) -> list[dict]:
    """Compares the independently-reconstructed statistical score against
    the real final boxscore/schedule score. For SO games, the boxscore
    score includes the shootout-winning bonus goal (Part 6), so the
    reconstructed STATISTICAL score is expected to be exactly one goal
    lower than the boxscore score for the winning team, never equal --
    that is the correct outcome, not a mismatch."""
    timeline = reconstruct_statistical_score(events, home_team_id, away_team_id)
    recon_home = timeline[-1]["home_score"] if timeline else 0
    recon_away = timeline[-1]["away_score"] if timeline else 0
    violations = []
    if final_period_type != "SO":
        if recon_home != expected_home_score or recon_away != expected_away_score:
            violations.append({
                "reason": "reconstructed statistical score != expected final score",
                "reconstructed": {"home": recon_home, "away": recon_away},
                "expected": {"home": expected_home_score, "away": expected_away_score},
            })
    else:
        home_diff = expected_home_score - recon_home
        away_diff = expected_away_score - recon_away
        ok = (home_diff, away_diff) in ((1, 0), (0, 1))
        if not ok:
            violations.append({
                "reason": "SO game: expected score is not exactly one shootout-bonus goal "
                          "above the reconstructed statistical score for exactly one team",
                "reconstructed": {"home": recon_home, "away": recon_away},
                "expected": {"home": expected_home_score, "away": expected_away_score},
            })
    return violations


def check_sog_monotonic(events: list[PbpEvent]) -> list[dict]:
    violations = []
    away_sog = 0
    home_sog = 0
    for ev in events:
        if ev.event_type != "shot-on-goal":
            continue
        a = ev.raw_details.get("awaySOG")
        h = ev.raw_details.get("homeSOG")
        if a is not None and a < away_sog:
            violations.append({"event_id": ev.event_id, "reason": "awaySOG decreased"})
        if h is not None and h < home_sog:
            violations.append({"event_id": ev.event_id, "reason": "homeSOG decreased"})
        away_sog = a if a is not None else away_sog
        home_sog = h if h is not None else home_sog
    return violations


ALL_CHECKS = (
    "check_event_times_within_period_bounds",
    "check_event_order_deterministic",
    "check_goals_increment_once",
    "check_shootout_excluded_from_statistical",
    "check_player_goals_reconcile_with_team_goals",
    "check_assists_only_on_goals_max_two",
    "check_period_goal_totals_reconcile",
    "check_final_score_reconciles",
    "check_sog_monotonic",
)
