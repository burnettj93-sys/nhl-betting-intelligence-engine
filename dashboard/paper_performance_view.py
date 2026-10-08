"""
Paper Performance data: the real $500 / $10-per-ticket paper account, read from the stored ledger rows only.

The account is one shared pool of cash. Results are reported three ways -- AUTOMATIC tickets (selected and recorded by the
engine), MANUALLY_ADDED tickets (added by hand with the explicit action) and ALL -- so a manual ticket is never folded into
the engine's own record. Simulated tracks are not part of the product; this module reads REAL_MARKET_PAPER only and creates
nothing.
"""
from __future__ import annotations

from operational import paper_bankroll as pb
from operational import runtime_mode

TRACK = "REAL_MARKET_PAPER"


def read_dashboard_state(conn, *, max_bets: int | None = None) -> dict:
    """PURE READ from an open bankroll connection (shared by the page and the Cloud snapshot builder)."""
    bets = pb.query_paper_bets(conn, track=TRACK)
    if max_bets is not None:
        bets = bets[-max_bets:]
    out = {"account": pb.account_state(conn, TRACK), "summary": pb.bankroll_summary(conn, TRACK),
           "answer": pb.answer_theoretical_bankroll_question(conn, TRACK), "origins": pb.origin_performance(conn, TRACK),
           "breakdowns": {"ALL": pb.performance_breakdowns(conn, TRACK)}, "bets": bets}
    for origin in pb.ORIGINS:
        out["breakdowns"][origin] = pb.performance_breakdowns(conn, TRACK, origin=origin)
    return out


def full_dashboard_state() -> dict:
    """In COMMUNITY_CLOUD_MODE the `performance` section of the current snapshot (raises SnapshotUnavailable when it is
    not served); locally, a read-only look at the ledger."""
    if runtime_mode.is_community_cloud():
        from dashboard import cloud_snapshot
        return cloud_snapshot.performance_state()
    conn = pb.init_db()
    try:
        return read_dashboard_state(conn)
    finally:
        conn.close()
