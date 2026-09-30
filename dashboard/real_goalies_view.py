"""
Real Goalies view (Platform Recovery block, 2026-09-30): the ONE real-data
structure dashboard/pages/27_Goalies.py's default view reads from. Real
current goalies (players table, position='G', current team via
team_membership_events), today's real scheduled opponent if their team
plays today, and real starter status via
features/point_in_time.py::goalie_status() -- the SAME sanctioned,
already-tested accessor the real T-35 decision engine uses, called with
prediction_time_utc=now so it answers "confirmed/expected/unknown RIGHT
NOW," never a point-in-time-restricted historical read done directly
against goalie_status_events (which this module never touches).

GOALIE_SAVES has no certified real DraftKings payload contract today
(research/generic_prop_pricing/provider_adapter.py's own VERIFIED_CONTRACTS)
-- market state is honestly MARKET_UNAVAILABLE, never a demo save line.

This module never imports the demo data module.
"""
from __future__ import annotations

import datetime as dt

MARKET_UNAVAILABLE = "MARKET_UNAVAILABLE"
STARTER_UNCONFIRMED = "UNCONFIRMED"
STARTER_CONFIRMED = "CONFIRMED"


def real_current_goalies(conn) -> list[dict]:
    """Every real player whose position is 'G' and whose LATEST real
    team_membership_events row still names a team (never a goalie who has
    since left/been removed)."""
    rows = conn.execute(
        """
        SELECT p.player_id, p.full_name, latest.team_id
        FROM players p
        JOIN (
            SELECT t1.player_id, t1.team_id
            FROM team_membership_events t1
            JOIN (
                SELECT player_id, MAX(effective_at_utc) AS max_eff
                FROM team_membership_events
                GROUP BY player_id
            ) t2 ON t1.player_id = t2.player_id AND t1.effective_at_utc = t2.max_eff
        ) latest ON latest.player_id = p.player_id
        WHERE p.position = 'G'
        ORDER BY p.full_name
        """).fetchall()
    return [{"player_id": r["player_id"], "full_name": r["full_name"], "team": r["team_id"]} for r in rows]


def _real_starter_status(conn, game_id, team_id: str, now: dt.datetime) -> dict:
    from features.point_in_time import goalie_status
    status = goalie_status(conn, game_id, team_id, now.isoformat())
    label = STARTER_CONFIRMED if status.status == "CONFIRMED" else STARTER_UNCONFIRMED
    return {"player_id": status.player_id, "raw_status": status.status, "label": label,
            "observed_at_utc": status.observed_at_utc}


def build_real_goalies_state(conn, *, now: dt.datetime | None = None) -> dict:
    from dashboard.real_team_intelligence_view import real_current_opponent

    now = now or dt.datetime.now(dt.timezone.utc)
    rows = []
    for g in real_current_goalies(conn):
        opponent = real_current_opponent(conn, g["team"], now) if g["team"] else None
        starter = _real_starter_status(conn, opponent["game_id"], g["team"], now) if opponent else None
        # Production Gap Closure sprint (2026-09-30): _real_starter_status()
        # answers "who is TOR's confirmed starter tonight" (one answer per
        # TEAM/game) -- it used to be stamped verbatim as `starter_status`
        # onto EVERY goalie on that team, so a real backup goalie's own row
        # displayed the literal text "CONFIRMED" (only the badge color used
        # the correctly-scoped is_confirmed_starter check). starter_status
        # is now derived FROM is_confirmed_starter, so only the one, real,
        # specifically-named starter is ever labeled CONFIRMED for himself.
        is_confirmed_starter = bool(starter and starter["player_id"] == g["player_id"]
                                     and starter["label"] == STARTER_CONFIRMED)
        rows.append({
            **g, "today_opponent": opponent,
            "starter_status": (STARTER_CONFIRMED if is_confirmed_starter else STARTER_UNCONFIRMED)
                               if starter else None,
            "is_confirmed_starter": is_confirmed_starter,
            "market_state": MARKET_UNAVAILABLE,
        })
    return {"generated_at_utc": now.isoformat(), "provenance": "REAL NHL", "goalies": rows}
