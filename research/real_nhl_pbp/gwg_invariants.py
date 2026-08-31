"""Part 21: GWG invariants, checked directly against a GwgResult and the
game's own normalized events -- never re-deriving the answer, only
verifying the one already produced by gwg.derive_gwg()."""
from __future__ import annotations

from research.real_nhl_pbp.gwg import STATUS_NO_PLAYER_GWG_SHOOTOUT, STATUS_RESOLVED, GwgResult
from research.real_nhl_pbp.schema import PbpEvent


def check_all(result: GwgResult, events: list[PbpEvent]) -> list[dict]:
    violations = []

    if result.status == STATUS_NO_PLAYER_GWG_SHOOTOUT:
        if result.gwg_event_id is not None:
            violations.append({"reason": "shootout game must not carry a GWG event id"})
        if result.final_home_score != result.final_away_score:
            violations.append({"reason": "NO_PLAYER_GWG_SHOOTOUT status but statistical score is not tied"})
        return violations

    if result.status != STATUS_RESOLVED:
        violations.append({"reason": f"unexpected status {result.status!r}"})
        return violations

    goal_events = {e.event_id: e for e in events if e.event_type == "goal" and e.is_statistical}
    gwg_event = goal_events.get(result.gwg_event_id)
    if gwg_event is None:
        violations.append({"reason": "GWG event_id does not exist among this game's statistical goals"})
        return violations

    # GWG belongs to the winning team
    if gwg_event.team_id != result.winning_team:
        violations.append({"reason": "GWG event's team is not the winning team"})

    # GWG is a statistical (non-SO) goal, never a shootout tally
    if gwg_event.period_type == "SO" or not gwg_event.is_statistical:
        violations.append({"reason": "GWG event is not a statistical (non-SO) goal"})

    # GWG event occurs exactly once (no duplicate goal event_id in this game)
    ids = list(goal_events.keys())
    if len(ids) != len(set(ids)):
        violations.append({"reason": "duplicate goal event_id in this game"})

    # GWG ordinal invariant: winning-team ordinal == losing_final_goals + 1
    winning_team_goals = sorted(
        (e for e in events if e.event_type == "goal" and e.is_statistical and e.team_id == result.winning_team),
        key=lambda e: e.event_sequence,
    )
    winning_final_goals = len(winning_team_goals)
    losing_final_goals = (result.final_home_score + result.final_away_score) - winning_final_goals
    expected_ordinal = losing_final_goals + 1
    winning_goal_ids = [e.event_id for e in winning_team_goals]
    actual_ordinal = winning_goal_ids.index(result.gwg_event_id) + 1 if result.gwg_event_id in winning_goal_ids else None
    if actual_ordinal != expected_ordinal:
        violations.append({
            "reason": "GWG ordinal != losing_final_goals + 1",
            "expected_ordinal": expected_ordinal, "actual_ordinal": actual_ordinal,
        })

    return violations
