"""
Where the product pages get their data. Community Cloud reads sections of the published snapshot; a local run reads the
documents the engine writes (product_state.json, tickets_state.json) and the ledger. Both come from the same builders, so
the two modes show the same thing. A missing source raises Unavailable with a plain-language cause for the page to show.
"""
from __future__ import annotations

from dashboard import cloud_snapshot
from operational import runtime_mode


class Unavailable(Exception):
    """Carries the text a page shows in place of data: what is missing and why."""


def _cloud() -> bool:
    return runtime_mode.is_community_cloud()


def _wrap(fn):
    try:
        return fn()
    except cloud_snapshot.SnapshotUnavailable as exc:
        raise Unavailable(str(exc)) from exc


def _local_state() -> dict:
    from operational import product_data
    state = product_data.read_state()
    if state is None:
        raise Unavailable("The product data document has not been built yet; it is written by the paper-trader job every "
                          "15 minutes (operational/product_data.py).")
    return state


def meta() -> dict:
    if _cloud():
        return _wrap(cloud_snapshot.product_meta)
    s = _local_state()
    return {k: s[k] for k in ("schema_version", "generated_at_utc", "et_today", "default_date", "data_through", "current_season")}


def games() -> dict:
    return _wrap(cloud_snapshot.product_games) if _cloud() else _local_state()["games"]


def game_details() -> dict:
    return _wrap(cloud_snapshot.product_game_details) if _cloud() else _local_state()["game_details"]


def players() -> dict:
    return _wrap(cloud_snapshot.product_players) if _cloud() else _local_state()["players"]


def goalies() -> dict:
    return _wrap(cloud_snapshot.product_goalies) if _cloud() else _local_state()["goalies"]


def teams() -> dict:
    return _wrap(cloud_snapshot.product_teams) if _cloud() else _local_state()["teams"]


def model_health() -> dict:
    return _wrap(cloud_snapshot.product_model_health) if _cloud() else _local_state()["model_health"]


def tickets() -> dict:
    if _cloud():
        return _wrap(cloud_snapshot.tickets)
    from operational import daily_tickets
    state = daily_tickets.read_state()
    if state is None:
        raise Unavailable("The ticket board has not been built yet; it is written by the 15-minute paper-trader job.")
    return state


def options() -> dict:
    t = tickets()
    opts = t.get("options")
    if not opts:
        raise Unavailable("No best-option document has been built for today yet (written with the ticket board).")
    return opts


def manual_orders() -> list[dict]:
    if _cloud():
        try:
            return _wrap(cloud_snapshot.manual_orders).get("orders", [])
        except Unavailable:
            return []
    from operational import manual_orders as mo
    from operational import paper_bankroll as pb
    conn = pb.init_db()
    try:
        return mo.recent_orders(conn)
    finally:
        conn.close()


def performance() -> dict:
    from dashboard import paper_performance_view as ppv
    try:
        return ppv.full_dashboard_state()
    except cloud_snapshot.SnapshotUnavailable as exc:
        raise Unavailable(str(exc)) from exc


def ontario_verifications() -> list[dict]:
    if _cloud():
        try:
            return _wrap(cloud_snapshot.manual_orders).get("ontario_verifications", [])
        except Unavailable:
            return []
    from operational import manual_orders as mo
    from operational import paper_bankroll as pb
    conn = pb.init_db()
    try:
        return mo.recent_verifications(conn)
    finally:
        conn.close()
