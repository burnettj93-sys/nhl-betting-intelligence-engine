"""
Part 1-6: deterministic goalie-tenure reconstruction from event-level
evidence only. Answers "who was actually in net for this event?" (Part 2)
-- it never infers identity from final boxscore order, and it never
fabricates a wall-clock timestamp where only event order is reliable
(Part 4's explicit instruction): interval boundaries are event_sequence
values, with the real raw (period, timeInPeriod) carried alongside purely
for readability.

The canonical per-event goalie signal is exactly the one normalize.py
already establishes: `event.players.get("goalie")` is the real NHL player
ID of the goalie in net for that event, or absent when the net was empty
(the same single source of truth period_saves.py uses for save-counting --
Part 22's "do not create two competing implementations", applied here to
goalie identity the same way it was applied to score reconstruction).

A tenure interval is emitted every time the observed state changes, using
one categorical `interval_type` rather than several booleans that could
disagree with each other:
  STARTER              -- first real goalie this team used in this game
  RELIEF               -- a genuinely different goalie took over (not an
                           empty-net return of the same goalie -- Part 6)
  RETURN_AFTER_EMPTY_NET -- the SAME goalie resumed after an empty-net gap
  EMPTY_NET             -- no goalie in net (pulled for an extra attacker)

Shootout (period_type == "SO") events are excluded entirely -- a shootout
goalie's presence is a fundamentally different structural regime (Part 14
of the GWG half of this slice makes the same exclusion for goals) and
tenure inside a shootout is not a period-saves concept.
"""
from __future__ import annotations

from dataclasses import dataclass

from research.real_nhl_pbp.schema import PbpEvent

INTERVAL_TYPES = ("STARTER", "RELIEF", "RETURN_AFTER_EMPTY_NET", "EMPTY_NET")


@dataclass
class GoalieTenureInterval:
    game_id: int
    team_id: int                    # the DEFENDING team -- whose goalie this is
    goalie_id: int | None           # None only for interval_type == "EMPTY_NET"
    interval_type: str              # one of INTERVAL_TYPES
    start_event_sequence: int
    end_event_sequence: int
    start_period: int
    start_time_in_period: str
    end_period: int
    end_time_in_period: str

    @property
    def is_starter(self) -> bool:
        return self.interval_type == "STARTER"

    @property
    def is_relief(self) -> bool:
        return self.interval_type == "RELIEF"

    @property
    def is_empty_net_interval(self) -> bool:
        return self.interval_type == "EMPTY_NET"


def reconstruct_goalie_tenure(events: list[PbpEvent], home_team_id: int, away_team_id: int
                               ) -> dict[int, list[GoalieTenureInterval]]:
    """Returns {team_id: [GoalieTenureInterval, ...]} for both teams,
    intervals in event_sequence order."""
    result: dict[int, list[GoalieTenureInterval]] = {home_team_id: [], away_team_id: []}

    for defending_team in (home_team_id, away_team_id):
        attacking_team = away_team_id if defending_team == home_team_id else home_team_id
        relevant = [
            e for e in events
            if e.period_type != "SO" and e.is_statistical
            and e.event_type in ("shot-on-goal", "missed-shot", "goal")
            and e.team_id == attacking_team
        ]
        relevant.sort(key=lambda e: e.event_sequence)

        intervals: list[GoalieTenureInterval] = []
        current_goalie: int | None = None       # last REAL goalie identity seen (persists through empty-net gaps)
        current_type: str | None = None
        interval_start: PbpEvent | None = None
        last_event: PbpEvent | None = None
        seen_any_real_goalie = False

        def close_interval(end_event: PbpEvent) -> None:
            if interval_start is None:
                return
            intervals.append(GoalieTenureInterval(
                game_id=interval_start.game_id, team_id=defending_team,
                goalie_id=current_goalie if current_type != "EMPTY_NET" else None,
                interval_type=current_type,
                start_event_sequence=interval_start.event_sequence,
                end_event_sequence=end_event.event_sequence,
                start_period=interval_start.period_number, start_time_in_period=interval_start.time_in_period,
                end_period=end_event.period_number, end_time_in_period=end_event.time_in_period,
            ))

        for e in relevant:
            observed_goalie = e.players.get("goalie")  # None == empty net at this event
            if observed_goalie is not None:
                if not seen_any_real_goalie:
                    new_type = "STARTER"
                elif observed_goalie == current_goalie:
                    new_type = current_type if current_type in ("STARTER", "RELIEF", "RETURN_AFTER_EMPTY_NET") \
                        else "RETURN_AFTER_EMPTY_NET"
                elif current_type == "EMPTY_NET" and observed_goalie == _last_real_goalie(intervals, current_goalie):
                    new_type = "RETURN_AFTER_EMPTY_NET"
                else:
                    new_type = "RELIEF"
                new_goalie = observed_goalie
                seen_any_real_goalie = True
            else:
                new_type = "EMPTY_NET"
                new_goalie = current_goalie  # remembered for a later RETURN_AFTER_EMPTY_NET check

            if current_type is None:
                interval_start = e
                current_type, current_goalie = new_type, new_goalie
            elif new_type == current_type and (new_type == "EMPTY_NET" or new_goalie == current_goalie):
                pass  # extend current interval
            else:
                close_interval(last_event)
                interval_start = e
                current_type, current_goalie = new_type, new_goalie
            last_event = e

        if interval_start is not None and last_event is not None:
            close_interval(last_event)

        result[defending_team] = intervals

    return result


def _last_real_goalie(intervals: list[GoalieTenureInterval], fallback: int | None) -> int | None:
    for interval in reversed(intervals):
        if interval.goalie_id is not None:
            return interval.goalie_id
    return fallback


def mid_period_changes(intervals: list[GoalieTenureInterval]) -> list[tuple[GoalieTenureInterval, GoalieTenureInterval]]:
    """Pairs of (previous_interval, new_interval) where a RELIEF change
    happened WITHOUT a period boundary between them -- Part 5's explicit
    ask. A goalie pulled for an extra attacker (EMPTY_NET) is never
    counted here (Part 5: 'do not treat goalie pull for extra attacker as
    a relief-goalie substitution')."""
    changes = []
    for prev, curr in zip(intervals, intervals[1:]):
        if curr.interval_type == "RELIEF" and prev.end_period == curr.start_period:
            changes.append((prev, curr))
    return changes
