"""
Real Product Bridge block (2026-09-29): the single operational-layer entry
point that opens the real nhl.db connection and calls
dashboard/real_today_view.py::build_real_today_state() with it.

This exists as its own module -- separate from
operational/cloud_snapshot_builder.py -- specifically so
dashboard/pages/21_Today.py's LOCAL-mode branch can import it without ever
referencing "cloud_snapshot_builder" in a dashboard/*.py file at all: Cloud
mode always reads through dashboard/cloud_snapshot.py's own remote-fetch
wrapper, never the builder directly (tests/test_cloud_live_data.py::
test_cloud_pages_never_import_the_publisher_or_builder is a strict,
text-level AST-adjacent check with no exceptions). dashboard/*.py files also
must never import db.py directly (it can trigger schema migrations on a
read-only presentation page -- tests/test_dashboard.py's own structural
check), so this function owns opening/closing the real connection on the
page's behalf.

operational/cloud_snapshot_builder.py's own "real_today" section builder
calls this SAME function, so LOCAL mode and the published Cloud snapshot are
never two separately-derived sources of truth.
"""
from __future__ import annotations


def open_real_today_state(**kwargs) -> dict:
    from dashboard import real_today_view as rtv
    import db
    conn = db.get_conn()
    try:
        return rtv.build_real_today_state(conn, **kwargs)
    finally:
        conn.close()


def open_real_player_props_state(**kwargs) -> dict:
    """Same rationale as open_real_today_state(), for
    dashboard/pages/26_Player_Props.py's real default view."""
    from dashboard import real_player_props_view as rppv
    import db
    conn = db.get_conn()
    try:
        return rppv.build_real_player_props_state(conn, **kwargs)
    finally:
        conn.close()


def open_real_team_intelligence_state(team_id: str, **kwargs) -> dict:
    """Same rationale as open_real_today_state(), for
    dashboard/pages/31_Team_Intelligence.py's real default view."""
    from dashboard import real_team_intelligence_view as rtiv
    import db
    conn = db.get_conn()
    try:
        return rtiv.build_real_team_intelligence_state(conn, team_id, **kwargs)
    finally:
        conn.close()


def open_all_teams_state(**kwargs) -> dict:
    """Publishing-only entry point: every real team's state in one dict,
    for the Cloud snapshot's own real_team_intelligence section (Cloud
    mode can never open nhl.db directly to answer a live per-team
    selection)."""
    from dashboard import real_team_intelligence_view as rtiv
    import db
    conn = db.get_conn()
    try:
        return rtiv.build_all_teams_state(conn, **kwargs)
    finally:
        conn.close()


def open_real_team_list() -> list[str]:
    """Same rationale as open_real_today_state(), for
    dashboard/pages/31_Team_Intelligence.py's team selector."""
    from dashboard import real_team_intelligence_view as rtiv
    import db
    conn = db.get_conn()
    try:
        return rtiv.real_team_list(conn)
    finally:
        conn.close()


def open_real_player_search(query: str = "", **kwargs) -> list[dict]:
    """Same rationale as open_real_today_state(), for
    dashboard/pages/30_Players.py's real default search."""
    from dashboard import real_player_view as rpv
    import db
    conn = db.get_conn()
    try:
        return rpv.search_real_players(conn, query, **kwargs)
    finally:
        conn.close()


def open_all_players_list() -> list[dict]:
    """Publishing-only entry point: every real player's identity (id,
    name, position, current team) in one cheap list, for the Cloud
    snapshot's own real_all_players section."""
    from dashboard import real_player_view as rpv
    import db
    conn = db.get_conn()
    try:
        players = rpv.search_real_players(conn, "", limit=100000)
        return [{**p, "team": rpv.real_player_current_team(conn, p["player_id"])} for p in players]
    finally:
        conn.close()


def open_real_player_state(player_id: str, **kwargs) -> dict:
    """Same rationale as open_real_today_state(), for
    dashboard/pages/25_Player_Intelligence.py's real default view."""
    from dashboard import real_player_view as rpv
    import db
    conn = db.get_conn()
    try:
        return rpv.build_real_player_state(conn, player_id, **kwargs)
    finally:
        conn.close()


def open_real_goalies_state(**kwargs) -> dict:
    """Same rationale as open_real_today_state(), for
    dashboard/pages/27_Goalies.py's real default view."""
    from dashboard import real_goalies_view as rgv
    import db
    conn = db.get_conn()
    try:
        return rgv.build_real_goalies_state(conn, **kwargs)
    finally:
        conn.close()


def open_real_game_detail_state(game_id, **kwargs) -> dict:
    """Same rationale as open_real_today_state(), for
    dashboard/pages/2_Game_Detail.py's real (non-demo, non-historical) path."""
    from dashboard import real_game_detail_view as rgdv
    import db
    conn = db.get_conn()
    try:
        return rgdv.build_real_game_detail_state(conn, game_id, **kwargs)
    finally:
        conn.close()


def open_real_game_details_for_today(**kwargs) -> dict:
    """Cloud mode has no live nhl_conn to look up an arbitrary game_id on
    demand -- this precomputes EVERY one of today's real games' detail
    state (bounded by today's real game count, exactly like every other
    per-day real section) so the published snapshot can serve Game Detail
    for any of today's games, keyed by game_id."""
    from dashboard import real_game_detail_view as rgdv
    from dashboard import real_today_view as rtv
    import datetime as dt
    import db
    conn = db.get_conn()
    try:
        now = dt.datetime.now(dt.timezone.utc)
        games = rtv._today_real_games(conn, now)
        return {g["game_id"]: rgdv.build_real_game_detail_state(conn, g["game_id"], now=now) for g in games}
    finally:
        conn.close()
