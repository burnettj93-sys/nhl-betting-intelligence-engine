"""
Real Recommendation Pipeline block (2026-09-24), Parts 14-16: the missing
bridge between the real archived DraftKings price history now accumulating
in odds_snapshots (via operational.real_odds_bridge -- every real intraday
snapshot is preserved there, never overwritten, thanks to its own UNIQUE
index on captured_at_utc) and the existing, already-tested closing-price
mechanism (operational.clv_resolver.find_closing_price/compute_clv).

Writes NO new CLV math whatsoever -- this module only assembles the real
price_history list that find_closing_price()'s own documented contract
already expects, then returns its result completely unmodified.

Provider-label normalization: real_odds_bridge.py stores
data_provider="the-odds-api" (the exact real value that provider's own
API returns) on every odds_snapshots row it writes, while
clv_resolver.REAL_PRICE_SOURCES is keyed on "THE_ODDS_API" (chosen before
any real odds had ever been ingested, so it was never reconciled against
a real value). Both are correct labels for the exact same real provider
-- there is no discrepancy in the underlying data, only in the two
modules' independently-chosen label spelling. Normalizing here (rather
than changing either already-tested module's own vocabulary) keeps
clv_resolver's pure-function contract and real_odds_bridge's already-
verified provider label both completely unchanged.
"""
from __future__ import annotations

from operational import clv_resolver

_PROVIDER_LABEL_MAP = {"the-odds-api": "THE_ODDS_API"}


def real_price_history_for_market(conn, *, game_id, market: str, selection: str) -> list[dict]:
    """Reads every real odds_snapshots row ever archived for this exact
    (game_id, market, selection) -- across however many real intraday
    snapshots were captured -- normalized into the {"captured_at_utc",
    "american_price", "source"} shape clv_resolver.find_closing_price()
    already expects. Never filters by recency itself; find_closing_price
    does its own strictly-before-event_start_utc filtering (Part 44)."""
    rows = conn.execute(
        "SELECT captured_at_utc, price_american, data_provider FROM odds_snapshots "
        "WHERE game_id = ? AND market = ? AND selection = ? AND status = 'ACTIVE'",
        (game_id, market, selection)).fetchall()
    return [
        {"captured_at_utc": r["captured_at_utc"], "american_price": r["price_american"],
         "source": _PROVIDER_LABEL_MAP.get(r["data_provider"], r["data_provider"])}
        for r in rows
    ]


def resolve_real_moneyline_closing_price(conn, *, game_id, selection: str, event_start_utc: str) -> dict:
    """The single entry point settlement calls for a MONEYLINE
    observation: real archived price history in, clv_resolver's own
    unmodified RESOLVED/CLV_NOT_AVAILABLE result out. `conn` must be the
    real nhl.db connection (odds_snapshots lives in the root schema, a
    separate database from the prospective ledger)."""
    history = real_price_history_for_market(conn, game_id=game_id, market="MONEYLINE", selection=selection)
    return clv_resolver.find_closing_price(history, event_start_utc)
