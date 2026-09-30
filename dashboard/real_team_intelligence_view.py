"""
Real Team Intelligence view (Platform Recovery block, 2026-09-29): the ONE
canonical real-data structure dashboard/pages/31_Team_Intelligence.py's
default view reads from. Real current NHL team list (nhl.db's own teams
table), real current roster (team_membership_events -- a dated,
append-only real feed, never a snapshot that silently goes stale), and
today's real scheduled opponent if the team plays today (America/Toronto
calendar day, matching real_today_view.py's own Eastern-day semantics).

Team-level model context (e.g. Elo rating) is intentionally NOT invented
here: no real current-season team-strength number has been wired into
this page before, and fabricating one now would violate the same "never
invent simulated opponent context" rule this block exists to enforce.
It is reported as UNAVAILABLE, honestly, not silently omitted.

This module never imports the demo data module.
"""
from __future__ import annotations

import datetime as dt


def real_team_list(conn) -> list[str]:
    rows = conn.execute("SELECT DISTINCT team_id FROM teams WHERE team_id IS NOT NULL ORDER BY team_id").fetchall()
    return [r["team_id"] for r in rows]


def real_current_roster(conn, team_id: str) -> list[dict]:
    """Every player whose LATEST real team_membership_events row (by
    effective_at_utc) names this team -- a player traded/waived/sent down
    away from this team never lingers on its roster here, unlike a static
    snapshot."""
    rows = conn.execute(
        """
        SELECT p.player_id, p.full_name, p.position, latest.team_id, latest.effective_at_utc
        FROM players p
        JOIN (
            SELECT t1.player_id, t1.team_id, t1.effective_at_utc
            FROM team_membership_events t1
            JOIN (
                SELECT player_id, MAX(effective_at_utc) AS max_eff
                FROM team_membership_events
                GROUP BY player_id
            ) t2 ON t1.player_id = t2.player_id AND t1.effective_at_utc = t2.max_eff
        ) latest ON latest.player_id = p.player_id
        WHERE latest.team_id = ?
        ORDER BY p.position, p.full_name
        """, (team_id,)).fetchall()
    return [{"player_id": r["player_id"], "full_name": r["full_name"], "position": r["position"],
             "since_utc": r["effective_at_utc"]} for r in rows]


def real_current_opponent(conn, team_id: str, now: dt.datetime) -> dict | None:
    """Today's (Eastern calendar day) real scheduled/live/final game for
    this team, if any -- never a simulated opponent invented when the
    team has no real game today."""
    from operational import eastern_time as et

    today = et.eastern_today(now)
    row = conn.execute(
        """SELECT game_id, home_team, away_team, game_state, scheduled_start_utc FROM games
           WHERE game_date = ? AND (home_team = ? OR away_team = ?) LIMIT 1""",
        (today, team_id, team_id)).fetchone()
    if row is None:
        return None
    is_home = row["home_team"] == team_id
    return {
        "game_id": row["game_id"], "opponent": row["away_team"] if is_home else row["home_team"],
        "is_home": is_home, "game_state": row["game_state"], "scheduled_start_utc": row["scheduled_start_utc"],
    }


def build_real_team_intelligence_state(conn, team_id: str, *, now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    roster = real_current_roster(conn, team_id)
    opponent = real_current_opponent(conn, team_id, now)
    return {
        "generated_at_utc": now.isoformat(),
        "provenance": "REAL MARKET DATA",
        "team": team_id,
        "roster": roster,
        "roster_count": len(roster),
        "today_opponent": opponent,
        "team_strength_model": "UNAVAILABLE — no real current-season team-strength model is wired to this page",
    }


def build_all_teams_state(conn, *, now: dt.datetime | None = None) -> dict:
    """Every real team's state in one dict, keyed by team_id -- cheap
    enough (32 small roster queries) to publish in full for Community
    Cloud, which can never open nhl.db directly to answer a per-team
    selection live the way LOCAL mode does."""
    now = now or dt.datetime.now(dt.timezone.utc)
    return {team_id: build_real_team_intelligence_state(conn, team_id, now=now)
            for team_id in real_team_list(conn)}
