"""
Real Game Detail view (Production Gap Closure sprint, 2026-09-30).

Game Detail previously looked up EVERY incoming game_id against the frozen
historical research corpus (research/real_nhl_results/normalized_regular_
season_games.jsonl) -- a real, CURRENT game_id from Today's real slate is
never in that corpus (which stops in April), so the page silently fell
back to browsing the corpus's own last available date, rendering an
unrelated April game with only a small warning banner. That is a labeled
substitution, not the honest "this game's detail isn't available here"
state the product actually needs.

This module looks the SAME real, current game up directly from nhl.db's
own `games` table (via the caller-supplied connection -- this module never
imports db.py itself, exactly like every other dashboard/real_*_view.py
module) so a real Today game_id always resolves to itself, or to an
explicit NOT_FOUND, never a substitute. Moneyline model state reuses the
SAME real decision engine (run_slate.build_prediction_for_game +
pricing.engine.evaluate_moneyline_for_game) research/real_market_parlay/
real_slate_adapter.py already drives -- never a second, parallel
eligibility system -- but is looked up for the ONE requested game_id
regardless of date, since Game Detail must be able to show a real game
that started earlier today or is already LIVE/FINAL, not only games that
still qualify as a fresh "today's slate" parlay candidate.
"""
from __future__ import annotations

import datetime as dt


def build_real_game_detail_state(conn, game_id, *, now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    row = conn.execute(
        "SELECT game_id, game_date, scheduled_start_utc, home_team, away_team, game_state, "
        "home_score, away_score FROM games WHERE game_id = ?", (str(game_id),)).fetchone()
    if row is None:
        return {"status": "NOT_FOUND", "game_id": str(game_id)}

    return {
        "status": "FOUND",
        "game_id": str(row["game_id"]),
        "game_date_et": row["game_date"],
        "home_team": row["home_team"],
        "away_team": row["away_team"],
        "game_state": row["game_state"],
        "scheduled_start_utc": row["scheduled_start_utc"],
        "home_score": row["home_score"],
        "away_score": row["away_score"],
        "moneyline": _real_moneyline_state(conn, row, now),
    }


def _real_moneyline_state(conn, row, now: dt.datetime) -> dict:
    if row["game_state"] != "SCHEDULED":
        return {"status": "GAME_NOT_SCHEDULED",
                "reason": f"game_state={row['game_state']} -- moneyline model state is only "
                          f"evaluated for a game that hasn't started"}
    if row["scheduled_start_utc"]:
        start = dt.datetime.fromisoformat(row["scheduled_start_utc"]).replace(tzinfo=dt.timezone.utc)
        if now >= start:
            return {"status": "EVENT_ALREADY_STARTED"}

    try:
        from run_slate import build_prediction_for_game
        from pricing import engine as pricing_engine
        pred = build_prediction_for_game(conn, row["game_id"])
        label = f"{pred.away_team} @ {pred.home_team} ({pred.game_date[:10]})"
        reports = pricing_engine.evaluate_moneyline_for_game(conn, pred, label)
    except Exception as exc:  # noqa: BLE001 -- one game's model state must never crash Game Detail
        return {"status": "MODEL_UNAVAILABLE", "reason": f"{type(exc).__name__}: {exc}"}

    return {"status": "AVAILABLE", "reports": [
        {"selection": r.selection, "action": r.action, "action_reason": r.action_reason,
         "model_conservative_probability": r.model_conservative_probability,
         "current_draftkings_price": r.current_draftkings_price}
        for r in reports]}
