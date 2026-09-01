"""
Schedule/startable-games intelligence (Part 31-33).

RAW_GAMES vs STARTABLE_GAMES (Part 33): a player's NHL games in a
window only have fantasy value if the roster actually has an open
lineup slot on that date -- a 4-game week is not automatically better
than a 3-game week if all 4 games collide with an already-full lineup
(Part 32).

KNOWN LIMITATION, disclosed honestly rather than worked around: this
project's real NHL schedule feed (research/real_nhl_results,
operational/system_health's own "nhl_schedule" check) currently only
covers HISTORICAL games -- there is no real, live 2026-27 forward
schedule in this repo yet (a pre-existing constraint of this project,
not something Yahoo access blocks). The functions below are real,
tested, schedule-agnostic logic: they operate on whatever real game-date
list a caller provides. Until a real forward schedule feed exists,
callers must pass SCHEDULE_NOT_AVAILABLE rather than a fabricated date
list -- see fantasy/recommendations/lineup_optimizer.py's own handling
of this exact case.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass


@dataclass(frozen=True)
class ScheduleWindow:
    raw_games: int
    startable_games: int
    off_night_games: int  # games on a date where < half the league's teams play, i.e. less bench competition
    back_to_backs: int
    game_dates: tuple[str, ...]


def startable_games(game_dates: list[str], *, roster_open_slots_by_date: dict[str, int],
                     other_committed_games_by_date: dict[str, int] | None = None) -> ScheduleWindow:
    """`roster_open_slots_by_date`: for each date this player could play,
    how many total lineup slots (across the position(s) this player is
    eligible for) are still open once every OTHER already-rostered
    player's own game that date is accounted for. `other_committed_games_by_date`
    is optional context (how many other real games are already
    "spoken for" that date) -- used only for the off-night heuristic
    below, never to silently reduce startable_games itself (that's
    already fully captured by roster_open_slots_by_date, which callers
    are responsible for computing correctly from the real lineup
    optimizer state)."""
    other_committed = other_committed_games_by_date or {}
    raw = len(game_dates)
    startable = sum(1 for d in game_dates if roster_open_slots_by_date.get(d, 0) > 0)
    off_night = sum(1 for d in game_dates if other_committed.get(d, 99) <= 4)

    back_to_backs = 0
    sorted_dates = sorted(dt.date.fromisoformat(d) for d in game_dates)
    for i in range(1, len(sorted_dates)):
        if (sorted_dates[i] - sorted_dates[i - 1]).days == 1:
            back_to_backs += 1

    return ScheduleWindow(raw_games=raw, startable_games=startable, off_night_games=off_night,
                           back_to_backs=back_to_backs, game_dates=tuple(game_dates))


SCHEDULE_NOT_AVAILABLE = "SCHEDULE_NOT_AVAILABLE"
