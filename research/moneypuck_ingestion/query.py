"""
Research query API for research_moneypuck_team_game_stats -- the ONLY
sanctioned way future feature-engineering code should read this table.
Enforces STRICT PRIOR-GAME-DATE (this slice's RESEARCH TEMPORAL POLICY):
for a target game on NHL calendar date D, only MoneyPuck rows with
game_date < D are eligible -- same-day and future rows are excluded.
Ordering/eligibility is always by the real `game_date` column, never by
`game_id`, `id` (autoincrement), or row/list position.

This module does not compute any derived feature (rolling xG, xG%,
opponent-adjusted anything) -- it returns raw eligible historical rows
only, exactly per this slice's "NO DERIVED MODEL FEATURES YET" scope.
Feature engineering is the next slice's job, built on top of this API so
it structurally cannot query a future/same-day row by accident.
"""
from __future__ import annotations

import sqlite3


def team_stats_as_of(conn: sqlite3.Connection, team: str, prediction_game_date: str,
                      situation: str = "all") -> list[dict]:
    """Every research_moneypuck_team_game_stats row for `team` (as the
    `team` side, i.e. this team's own For/Against stats) with
    game_date < prediction_game_date and matching `situation`, ordered
    chronologically (oldest first) by game_date. Returns the CURRENT
    (latest-ingested) revision for each (game_id, team, situation) --
    never an earlier, superseded revision, and never a same-day or
    future row."""
    rows = conn.execute(
        """
        SELECT t.* FROM research_moneypuck_team_game_stats t
        INNER JOIN (
            SELECT game_id, team, situation, MAX(ingested_at_utc) AS latest_ingested_at_utc
            FROM research_moneypuck_team_game_stats
            WHERE team = ? AND situation = ? AND game_date < ?
            GROUP BY game_id, team, situation
        ) latest
        ON t.game_id = latest.game_id AND t.team = latest.team
           AND t.situation = latest.situation
           AND t.ingested_at_utc = latest.latest_ingested_at_utc
        WHERE t.team = ? AND t.situation = ? AND t.game_date < ?
        ORDER BY t.game_date ASC, t.game_id ASC
        """,
        (team, situation, prediction_game_date, team, situation, prediction_game_date),
    ).fetchall()
    return [dict(r) for r in rows]


def team_stats_for_game(conn: sqlite3.Connection, game_id: int, team: str,
                         situation: str = "all") -> dict | None:
    """The current (latest-ingested) normalized row for one specific
    (game_id, team, situation) -- no date filtering, since this is an
    identity lookup (e.g. for cross-validation against the real NHL
    corpus), not a PIT-eligibility read. NOT safe to use as a historical
    feature source -- use team_stats_as_of() for that."""
    row = conn.execute(
        """
        SELECT * FROM research_moneypuck_team_game_stats
        WHERE game_id = ? AND team = ? AND situation = ?
        ORDER BY ingested_at_utc DESC LIMIT 1
        """,
        (game_id, team, situation),
    ).fetchone()
    return dict(row) if row is not None else None


def unique_game_coverage(conn: sqlite3.Connection, situation: str = "all") -> set[int]:
    """The set of distinct NHL game_ids represented in the normalized
    table at a given situation -- used for coverage-percentage reporting
    (Part N), never for eligibility gating."""
    rows = conn.execute(
        "SELECT DISTINCT game_id FROM research_moneypuck_team_game_stats WHERE situation = ?",
        (situation,),
    ).fetchall()
    return {r["game_id"] for r in rows}
