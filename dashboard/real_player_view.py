"""
Real Player view (Platform Recovery block, 2026-09-30): the ONE real-data
structure dashboard/pages/30_Players.py and dashboard/pages/25_Player_Intelligence.py's
default views read from. Real player identity (players table), real
current team (team_membership_events -- the same dated real feed
dashboard/real_team_intelligence_view.py already uses), today's real
scheduled opponent if the player's current team plays today, and the
player's own real market state -- the single eligible PLAYER_SOG_ALTERNATE
leg for them if one currently exists (cross-referenced by player_id
against the SAME real_player_props_view.py rows Player Props itself
shows -- never a second, contradictory source), else an honest
MARKET_UNAVAILABLE.

This module never imports the demo data module.
"""
from __future__ import annotations

import datetime as dt

MARKET_UNAVAILABLE = "MARKET_UNAVAILABLE"
MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"


def search_real_players(conn, query: str = "", limit: int = 300) -> list[dict]:
    q = f"%{query.strip()}%" if query.strip() else "%"
    rows = conn.execute(
        "SELECT player_id, full_name, position FROM players WHERE full_name LIKE ? "
        "ORDER BY full_name LIMIT ?", (q, limit)).fetchall()
    return [dict(r) for r in rows]


def real_player_current_team(conn, player_id: str) -> str | None:
    row = conn.execute(
        "SELECT team_id FROM team_membership_events WHERE player_id = ? "
        "ORDER BY effective_at_utc DESC LIMIT 1", (player_id,)).fetchone()
    return row["team_id"] if row else None


def real_player_identity(conn, player_id: str) -> dict | None:
    row = conn.execute("SELECT player_id, full_name, position FROM players WHERE player_id = ?",
                        (player_id,)).fetchone()
    if row is None:
        return None
    return {"player_id": row["player_id"], "full_name": row["full_name"], "position": row["position"],
            "team": real_player_current_team(conn, player_id)}


def real_player_today_opponent(conn, team_id: str | None, now: dt.datetime) -> dict | None:
    if team_id is None:
        return None
    from dashboard.real_team_intelligence_view import real_current_opponent
    return real_current_opponent(conn, team_id, now)


def real_player_market_row(player_id: str, real_sog_rows: list[dict]) -> dict | None:
    """The SAME real eligible-leg row Player Props shows for this player,
    matched by player_id -- never re-derived, never a second source of
    truth. None (MARKET_UNAVAILABLE) if this player has no currently
    eligible real leg."""
    for row in real_sog_rows:
        if row.get("player_id") == player_id:
            return row
    return None


def build_real_player_state(conn, player_id: str, *, now: dt.datetime | None = None,
                            real_sog_rows: list[dict] | None = None) -> dict:
    """The one real state a Player Intelligence detail view reads from.
    `real_sog_rows` is injectable for tests; defaults to a fresh real
    Player Props computation."""
    now = now or dt.datetime.now(dt.timezone.utc)
    identity = real_player_identity(conn, player_id)
    if identity is None:
        return {"generated_at_utc": now.isoformat(), "provenance": "REAL NHL", "player": None}

    if real_sog_rows is None:
        from dashboard.real_player_props_view import build_real_player_props_state
        real_sog_rows = build_real_player_props_state(conn, now=now)["rows"]
    market_row = real_player_market_row(player_id, real_sog_rows)
    opponent = real_player_today_opponent(conn, identity["team"], now)

    return {
        "generated_at_utc": now.isoformat(),
        "provenance": "REAL NHL",
        "player": identity,
        "today_opponent": opponent,
        "market_state": market_row if market_row is not None else MARKET_UNAVAILABLE,
    }
