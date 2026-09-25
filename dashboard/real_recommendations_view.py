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


# ---------------------------------------------------------------------
# Live SOG + Saves Production Certification block (2026-09-24), Parts
# 19-22: the minimum shape adapter letting a real SOG/Saves prospective-
# ledger row become eligible input to the EXISTING dashboard/conviction.py
# (top_conviction/combo_eligible_legs) and research/game_edge_parlay
# engines -- neither of which is modified. Both already operate on the
# same generic "opportunity dict" shape dashboard/eligible_bets.py
# produces; this only translates a real ledger row into that exact
# shape, deriving edge/EV from the ledger's own stored probabilities via
# pricing/odds_math.py's real, unmodified functions (the ledger schema
# has no separate edge/EV columns -- Part 8's field list stores
# probabilities and odds, not their derived comparison, by design).
# ---------------------------------------------------------------------

_MARKET_FAMILY_TO_PROP = {"SOG": "sog", "GOALIE_SAVES": "saves"}


def real_prop_observation_to_opportunity_shape(row: dict) -> dict | None:
    """Converts one real PLAYER_SOG/GOALIE_SAVES prospective-ledger row
    into the opportunity-dict shape dashboard/conviction.py and
    research/game_edge_parlay/engine.py already consume. Returns None
    for a row this adapter doesn't recognize (any market_family other
    than SOG/GOALIE_SAVES) rather than guessing a shape for it."""
    from pricing import odds_math

    prop = _MARKET_FAMILY_TO_PROP.get(row.get("market_family"))
    if prop is None:
        return None

    odds_american = row.get("odds_american")
    no_vig = row.get("market_no_vig_probability")
    conservative_p = row.get("conservative_probability")
    raw_p = row.get("raw_probability")
    raw_edge = (raw_p - no_vig) if (raw_p is not None and no_vig is not None) else None
    conservative_edge = (conservative_p - no_vig) if (conservative_p is not None and no_vig is not None) else None
    ev = odds_math.expected_value(conservative_p, odds_american) if (
        conservative_p is not None and odds_american is not None) else None

    return {
        "player_id": row.get("player_id"), "player": row.get("player_name_snapshot"),
        "team": row.get("team"), "opponent": row.get("opponent"), "prop": prop,
        "market": row.get("market_family"), "market_id": row.get("market_id"),
        "threshold": row.get("threshold"), "side": row.get("side"),
        "decision": row.get("prospective_status"), "actionable": True,
        "confidence": row.get("confidence"), "conservative_probability": conservative_p,
        "raw_probability": raw_p, "raw_edge": raw_edge, "conservative_edge": conservative_edge,
        "ev": ev, "current_odds": odds_american,
        "starter_certainty": None,  # Part 12: no real confirmed-starter source exists yet
        "source": LIVE_SOURCE_LABEL if row.get("prospective_status") == "BET"
        else f"{REAL_MARKET_SOURCE_LABEL_PREFIX} — {row.get('prospective_status') or 'UNKNOWN'}",
        "is_demo": False,
        # freshness truth (presentation only): when the recommendation was recorded, when the price was
        # captured, and when the game starts
        "created_at_utc": row.get("created_at_utc"), "odds_captured_at_utc": row.get("odds_captured_at_utc"),
        "event_start_utc": row.get("event_start_utc"),
    }


def real_prop_recommendations_for_conviction_and_parlay(pl_conn=None) -> list[dict]:
    """Every real SOG/Saves observation the real orchestrator
    (operational/real_prop_orchestrator.py) has recorded, converted to
    the shape dashboard/conviction.py::top_conviction()/combo_eligible_legs()
    and research/game_edge_parlay/engine.py::build_game_edge_parlay()
    already expect. Currently always empty in real production (no SOG/
    Saves contract has ever been verified -- see
    docs/LIVE_SOG_SAVES_CERTIFICATION.md), which is the correct, honest,
    fail-closed result, not a bug in this function."""
    owns_pl = pl_conn is None
    pl_conn = pl_conn or pl.init_db()
    try:
        rows = pl_conn.execute(
            "SELECT * FROM predictions WHERE market_family IN ('SOG', 'GOALIE_SAVES') ORDER BY created_at_utc"
        ).fetchall()
        out = []
        for r in rows:
            converted = real_prop_observation_to_opportunity_shape(dict(r))
            if converted is not None:
                out.append(converted)
        return out
    finally:
        if owns_pl:
            pl_conn.close()
