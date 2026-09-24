"""
Real Recommendation Pipeline block (2026-09-24), Parts 2/12/13: the real,
non-demo counterpart to dashboard/eligible_bets.py. Reads ONLY real
MONEYLINE predictions the real orchestrator
(operational/real_recommendation_orchestrator.py) has recorded into the
real prospective ledger and real paper bankroll -- never a simulated
price, never demo_data. dashboard/eligible_bets.py's own demo path is
completely untouched by this module.

Every row this module returns is unambiguously labeled with
dashboard/live_dk.py's already-canonical LIVE_SOURCE_LABEL when a real
paper bet exists for it (a real BET decision), or an equally explicit
"REAL MARKET" label carrying the real decision status otherwise (WAIT/
PASS) -- a caller must never be able to mistake one of these rows for a
dashboard/eligible_bets.py row, and vice versa (Part 12: "never merge
visually without clear provenance").

Part 13: a real production day can have ZERO qualifying real bets --
`real_recommendation_summary_label()` returns the honest
"NO QUALIFYING BETS" string in that case rather than a page inventing or
implying a weak recommendation.
"""
from __future__ import annotations

from dashboard.live_dk import LIVE_SOURCE_LABEL
from operational import paper_bankroll as pb
from operational import prospective_ledger as pl

REAL_MARKET_SOURCE_LABEL_PREFIX = "REAL MARKET"
NO_QUALIFYING_BETS_LABEL = "NO QUALIFYING BETS"


def _real_market_label(prospective_status: str | None, has_paper_bet: bool) -> str:
    if has_paper_bet:
        return LIVE_SOURCE_LABEL
    return f"{REAL_MARKET_SOURCE_LABEL_PREFIX} — {prospective_status or 'UNKNOWN'}"


def real_moneyline_recommendations(pl_conn=None, bankroll_conn=None) -> list[dict]:
    """Every real MONEYLINE row the real orchestrator has ever recorded
    (PRIMARY_DAILY and any later MARKET_REFRESH), each carrying an
    unambiguous `source` label and `is_demo=False`. `pl_conn`/
    `bankroll_conn` are injectable for tests; default to the real,
    sanctioned databases via each module's own init_db()."""
    owns_pl = pl_conn is None
    owns_bankroll = bankroll_conn is None
    pl_conn = pl_conn or pl.init_db()
    bankroll_conn = bankroll_conn or pb.init_db()
    try:
        rows = pl_conn.execute(
            "SELECT * FROM predictions WHERE market_id = 'MONEYLINE' ORDER BY created_at_utc"
        ).fetchall()
        real_bets = {
            (r["event_id"], r["team"]) for r in
            pb.query_paper_bets(bankroll_conn, track="REAL_MARKET_PAPER")
        }
        out = []
        for r in rows:
            row = dict(r)
            has_paper_bet = (row.get("game_id"), row.get("team")) in real_bets
            out.append({
                **row,
                "source": _real_market_label(row.get("prospective_status"), has_paper_bet),
                "is_demo": False,
                "has_real_paper_bet": has_paper_bet,
            })
        return out
    finally:
        if owns_pl:
            pl_conn.close()
        if owns_bankroll:
            bankroll_conn.close()


def real_moneyline_recommendations_for_team(team: str, rows: list[dict] | None = None) -> list[dict]:
    rows = rows if rows is not None else real_moneyline_recommendations()
    return [r for r in rows if r.get("team") == team]


def real_moneyline_recommendations_for_game(team_a: str, team_b: str,
                                             rows: list[dict] | None = None) -> list[dict]:
    rows = rows if rows is not None else real_moneyline_recommendations()
    return [r for r in rows if r.get("team") in (team_a, team_b)]


def real_market_summary_label(rows: list[dict]) -> str:
    """Part 13: a valid production day can have ZERO qualifying bets --
    this must read as an honest, deliberate "NO QUALIFYING BETS" state,
    never a silently empty list a page might mistake for "not loaded
    yet" or paper over with a manufactured recommendation."""
    bet_rows = [r for r in rows if r.get("has_real_paper_bet")]
    if not bet_rows:
        return NO_QUALIFYING_BETS_LABEL
    return f"{len(bet_rows)} QUALIFYING BET(S)"
