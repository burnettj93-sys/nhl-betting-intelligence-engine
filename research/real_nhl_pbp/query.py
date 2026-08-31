"""
Part 29: as-of / temporal research access helpers over the unified PBP
research store (store.py).

IMPORTANT semantic note (Part 29's own explicit caution): every row in
this store was retrieved TODAY, as ARCHIVAL_RESEARCH -- the retrieval
timestamp does NOT represent original historical knowledge time. What
these helpers filter on is each event's real historical `game_date`, which
is a fact about when the game itself was played, not about when this
project learned it. That is exactly analogous to this project's existing
`player_history_as_of()` gate used across every prop model: a strict
"games before this date" filter, never "games retrieved before this
time." A caller building point-in-time features from this corpus must
still apply that same discipline -- these helpers make the correct query
easy, they do not make an incorrect one impossible.

All three functions use a strict `<` on `game_date` (ISO "YYYY-MM-DD"
string comparison, valid since the column is always that format) --
"before", not "on or before" -- matching this project's established PIT
convention everywhere else.
"""
from __future__ import annotations

import sqlite3


def events_before(conn: sqlite3.Connection, game_date: str, event_type: str | None = None) -> list[sqlite3.Row]:
    if event_type is None:
        cur = conn.execute(
            "SELECT * FROM pbp_events WHERE game_date < ? ORDER BY game_date, game_id, event_sequence",
            (game_date,),
        )
    else:
        cur = conn.execute(
            "SELECT * FROM pbp_events WHERE game_date < ? AND event_type = ? "
            "ORDER BY game_date, game_id, event_sequence",
            (game_date, event_type),
        )
    return cur.fetchall()


def player_events_before(conn: sqlite3.Connection, player_id: int, game_date: str,
                          role: str | None = None) -> list[sqlite3.Row]:
    if role is None:
        cur = conn.execute(
            "SELECT ep.*, e.event_type, e.period_number, e.period_type, e.regulation_elapsed_seconds "
            "FROM pbp_event_players ep JOIN pbp_events e "
            "  ON ep.game_id = e.game_id AND ep.event_id = e.event_id "
            "WHERE ep.player_id = ? AND ep.game_date < ? "
            "ORDER BY ep.game_date, ep.game_id",
            (player_id, game_date),
        )
    else:
        cur = conn.execute(
            "SELECT ep.*, e.event_type, e.period_number, e.period_type, e.regulation_elapsed_seconds "
            "FROM pbp_event_players ep JOIN pbp_events e "
            "  ON ep.game_id = e.game_id AND ep.event_id = e.event_id "
            "WHERE ep.player_id = ? AND ep.game_date < ? AND ep.role = ? "
            "ORDER BY ep.game_date, ep.game_id",
            (player_id, game_date, role),
        )
    return cur.fetchall()


def team_events_before(conn: sqlite3.Connection, team_id: int, game_date: str,
                        event_type: str | None = None) -> list[sqlite3.Row]:
    if event_type is None:
        cur = conn.execute(
            "SELECT * FROM pbp_events WHERE team_id = ? AND game_date < ? "
            "ORDER BY game_date, game_id, event_sequence",
            (team_id, game_date),
        )
    else:
        cur = conn.execute(
            "SELECT * FROM pbp_events WHERE team_id = ? AND game_date < ? AND event_type = ? "
            "ORDER BY game_date, game_id, event_sequence",
            (team_id, game_date, event_type),
        )
    return cur.fetchall()
