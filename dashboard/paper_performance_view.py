"""
Live DK / Paper Bankroll completion sprint (2026-08-31), Parts 41-49:
the dashboard-facing orchestration for the Paper Performance page. Idempotent
by design (operational/paper_bankroll.py's idempotency key) -- calling
ensure_*_paper_bets_created() on every page load is safe and never places
a duplicate $10 bet for the same real-world opportunity."""
from __future__ import annotations

from dashboard import conviction as cv
from dashboard import eligible_bets as eb
from dashboard import live_dk as ldk
from operational import paper_bankroll as pb


def ensure_demo_paper_bets_created(conn) -> list[dict]:
    """Part 28: one $10 DEMO_PAPER bet per BET-grade demo opportunity
    (straight legs), plus one DEMO_COMBO_PAPER_BET per HIGH_CONFIDENCE
    combo (Part 45) -- never a VALUE/RESEARCH combo."""
    opportunities = eb.all_opportunities()
    top = cv.top_conviction(opportunities)
    top_keys = {(o["player_id"], o["market_id"]) for o in top}
    for o in opportunities:
        o["_top_conviction"] = (o["player_id"], o["market_id"]) in top_keys
        o.setdefault("event_id", f"demo-{o['team']}-{o['opponent']}")
        o.setdefault("game_date", None)
        o.setdefault("event_start_utc", None)
    results = pb.auto_create_paper_bets_from_opportunities(
        conn, opportunities, track="DEMO_PAPER", price_source="SIMULATED_DEMO")

    board = cv.build_combo_board(opportunities)
    for combo in board["high_confidence"]:
        results.append(pb.create_demo_combo_paper_bet(conn, combo))
    return results


def ensure_real_market_paper_bets_created(conn) -> list[dict]:
    """Part 28, applied to real DraftKings MONEYLINE comparisons.
    Today's real evidence stales every row to WAIT (dashboard/live_dk.py's
    staleness gate), so this correctly creates zero bets right now --
    that is the honest result, not a bug (Part 20)."""
    rows = ldk.build_live_moneyline_comparisons()
    priced = [r for r in rows if r.get("status") == "PRICED"]
    return pb.auto_create_paper_bets_from_opportunities(
        conn, priced, track="REAL_MARKET_PAPER", price_source="LIVE_DRAFTKINGS")


def full_dashboard_state() -> dict:
    """One call for the Paper Performance page: ensures today's
    idempotent bet creation has run for both tracks, then returns each
    track's real summary + breakdowns, computed only from stored data
    (Part 49)."""
    conn = pb.init_db()
    ensure_demo_paper_bets_created(conn)
    ensure_real_market_paper_bets_created(conn)
    return {
        track: {
            "summary": pb.bankroll_summary(conn, track),
            "breakdowns": pb.performance_breakdowns(conn, track),
            "bets": pb.query_paper_bets(conn, track=track),
            "answer": pb.answer_theoretical_bankroll_question(conn, track),
        }
        for track in pb.TRACKS
    }
