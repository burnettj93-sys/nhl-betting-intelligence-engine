"""
Real Recommendation Pipeline block (2026-09-24): the orchestrator that
connects REAL CURRENT DATA + REAL SPORTSBOOK MARKET -> EXISTING MODEL ->
EXISTING DECISION ENGINE -> IMMUTABLE REAL-MARKET PREDICTION SNAPSHOT ->
PAPER BET IF ACTIONABLE, for MONEYLINE -- the only DraftKings market
contract verified live in this project (Part 5). Player props are
deliberately NOT touched here; they auto-activate per-market only once
their own provider contract is verified (Part 6).

This module writes NO new model math and NO new decision rule. Every
probability and every BET/WAIT/PASS/DATA_UNAVAILABLE action below comes
unmodified from:
  - run_slate.build_prediction_for_game() (models/combined_model.py's
    point-in-time-safe ELO reconstruction), and
  - pricing/engine.py::evaluate_moneyline_for_game() (the existing,
    already-tested moneyline decision engine -- goalie-confirmation gate,
    no-vig pricing, edge/EV thresholds).
Its only real job is translating that existing output into:
  (a) a real, immutable row in the NEW prospective ledger via
      operational/prospective_recording.py::record_observation(), so
      settlement/CLV/postmortem/Game Edge Parlay -- all of which already
      read that ledger -- can see it, and
  (b) a real paper bet via operational/paper_bankroll.py::record_paper_bet()
      on the REAL_MARKET_PAPER track, when (and only when) the existing
      decision engine's action is BET.

Fails closed (Part 4): DATA_UNAVAILABLE reports (no verified real
DraftKings quote for both sides, as already determined by
evaluate_moneyline_for_game() itself) are never recorded as a real
recommendation -- there is no real market snapshot to make immutable.
This module never overrides or second-guesses that existing gate.

Idempotency (Part 9): `prediction_cutoff_utc` is set to the REAL DraftKings
snapshot's own `captured_at_utc` (not a fixed schedule-derived time), so
prospective_ledger's existing idempotency key (game_id, market_id, side,
model_version, prediction_cutoff_utc) naturally makes an identical rerun
against the same market snapshot a no-op DUPLICATE, while a genuinely new
DraftKings price snapshot (new captured_at_utc) naturally produces a new
immutable row. The first observation for a given (game, side) each day is
recorded at checkpoint PRIMARY_DAILY; any subsequent one is MARKET_REFRESH
(prospective_recording.record_observation()'s existing checkpoint-ordering
guard already requires PRIMARY_DAILY to exist first).

Paper-bet idempotency (Part 10) is separate and price-independent, exactly
as paper_bankroll.compute_paper_idempotency_key() already keys it (track +
event + participant + market + threshold + side + price_source, NOT
odds/checkpoint) -- so a later MARKET_REFRESH snapshot of the same real
BET recommendation never places a second $10 stake.
"""
from __future__ import annotations

import datetime as dt

import db
from operational import paper_bankroll
from operational import prospective_ledger as pl
from operational import prospective_recording as pr
from pricing import engine as pricing_engine
from run_slate import build_prediction_for_game

MODEL_ID = "NHL_WIN_MODEL"
MARKET_ID = "MONEYLINE"


def _prediction_dict(pred, report, prediction_cutoff_utc: str | None) -> dict:
    """Shapes one BetReport + its GamePrediction into the generic
    `prediction` dict prospective_recording.record_observation() expects
    -- following record_sog_board_row()'s own documented pattern of
    adapting a real market's own row schema into that generic shape,
    never inventing a field prospective_schema.sql doesn't have."""
    opponent = pred.away_team if report.selection == pred.home_team else pred.home_team
    return {
        "record_type": "MODEL_OBSERVATION",
        "model_id": MODEL_ID,
        # created_at_utc is pinned to the model's own prediction_time_utc
        # (always strictly before scheduled_start_utc by construction --
        # see run_slate.build_prediction_for_game) rather than wall-clock
        # "now": this is the moment the recommendation was actually
        # computed, and it keeps insert_prediction()'s pre-game temporal
        # guard (created_at_utc < event_start_utc) correct regardless of
        # when this orchestrator happens to be invoked in real wall-clock
        # time relative to the game.
        "created_at_utc": pred.prediction_time_utc,
        "event_start_utc": pred.scheduled_start_utc,
        "prediction_cutoff_utc": prediction_cutoff_utc,
        "game_id": str(pred.game_id),
        "game_date": pred.game_date,
        "team": report.selection,
        "opponent": opponent,
        "market_id": MARKET_ID,
        "market_family": "MONEYLINE",
        "threshold": None,
        "side": report.selection,
        "raw_probability": report.model_true_probability,
        "context_adjusted_probability": report.model_true_probability,
        "coherent_probability": report.model_true_probability,
        "conservative_probability": report.model_conservative_probability,
        "confidence": None,
        "model_version": pred.model_version,
        "sportsbook": report.sportsbook,
        "market_key": report.market,
        "line": None,
        "odds_american": report.current_draftkings_price,
        "market_implied_probability": report.market_implied_probability,
        "market_no_vig_probability": report.market_no_vig_probability,
        "odds_captured_at_utc": prediction_cutoff_utc,
        "prospective_status": report.action,
    }


def _process_report(nhl_conn, pl_conn, bankroll_conn, pred, report) -> dict:
    if report.action == "DATA_UNAVAILABLE":
        return {"game_id": pred.game_id, "selection": report.selection, "status": "DATA_UNAVAILABLE",
                "action": "DATA_UNAVAILABLE", "reason": report.action_reason, "recorded": False,
                "paper_bet_created": False}

    snap_row = nhl_conn.execute(
        "SELECT captured_at_utc FROM odds_snapshots WHERE id = ?",
        (report.odds_snapshot_id_selection,)).fetchone()
    captured_at_utc = snap_row["captured_at_utc"] if snap_row else None
    if captured_at_utc is None:
        # Fail closed (Part 4): the decision engine reported a real price
        # but the snapshot row backing it can no longer be found -- never
        # fabricate a cutoff time for an immutable record.
        return {"game_id": pred.game_id, "selection": report.selection, "status": "DATA_UNAVAILABLE",
                "action": "DATA_UNAVAILABLE", "reason": "backing odds_snapshots row not found",
                "recorded": False, "paper_bet_created": False}

    existing_primary = pr.latest_checkpoint_row(
        pl_conn, game_id=str(pred.game_id), player_id=None, market_id=MARKET_ID,
        threshold=None, side=report.selection, checkpoint="PRIMARY_DAILY")
    checkpoint = "MARKET_REFRESH" if existing_primary else "PRIMARY_DAILY"

    prediction = _prediction_dict(pred, report, captured_at_utc)
    record_result = pr.record_observation(pl_conn, prediction, is_demo=False, checkpoint=checkpoint)

    outcome = {
        "game_id": pred.game_id, "selection": report.selection, "action": report.action,
        "status": record_result["status"], "checkpoint": checkpoint,
        "prediction_id": record_result.get("prediction_id"),
        "recorded": record_result["status"] in ("INSERTED", "DUPLICATE"),
        "paper_bet_created": False,
    }
    if record_result["status"] not in ("INSERTED", "DUPLICATE"):
        outcome["reason"] = record_result.get("reason")
        return outcome

    if report.action == "BET":
        opponent = pred.away_team if report.selection == pred.home_team else pred.home_team
        bet_result = paper_bankroll.record_paper_bet(
            bankroll_conn, track="REAL_MARKET_PAPER", price_source="LIVE_DRAFTKINGS",
            market_id=MARKET_ID, entry_odds=report.current_draftkings_price,
            event_id=str(pred.game_id), game_date=pred.game_date, team=report.selection,
            opponent=opponent, market_family="MONEYLINE", side=report.selection,
            model_probability=report.model_true_probability,
            conservative_probability=report.model_conservative_probability,
            market_no_vig_probability=report.market_no_vig_probability,
            edge=report.conservative_edge, ev=report.expected_value,
            model_version=pred.model_version, prediction_checkpoint=checkpoint,
            event_start_utc=pred.scheduled_start_utc)
        outcome["paper_bet_created"] = bet_result["status"] == "INSERTED"
        outcome["paper_bet_status"] = bet_result["status"]
        outcome["paper_bet_id"] = bet_result.get("paper_bet_id")
    return outcome


def run_real_moneyline_recommendations(conn=None, game_ids=None, now: dt.datetime | None = None,
                                        pl_conn=None, bankroll_conn=None) -> dict:
    """The production entry point (Part 3/20/22): for each real
    upcoming/priceable game, run the existing model + existing decision
    engine against real DraftKings data already synced into
    odds_snapshots by operational.real_odds_bridge, and record the
    result. A valid production run may legitimately record zero BET
    recommendations (Part 13) -- that is a healthy outcome, not a
    failure.

    `pl_conn`/`bankroll_conn` are injectable (isolated temp databases in
    tests); when omitted, the real, sanctioned
    prospective_observations.db / paper_bankroll.db are opened via each
    module's own init_db()."""
    owns_conn = conn is None
    conn = conn or db.get_conn()
    now = now or dt.datetime.now(dt.timezone.utc)
    summary = {"status": "SUCCESS", "games_evaluated": 0, "reports_evaluated": 0,
               "recommendations_recorded": 0, "data_unavailable": 0, "paper_bets_created": 0,
               "results": [], "error": None}

    owns_pl_conn = pl_conn is None
    owns_bankroll_conn = bankroll_conn is None
    pl_conn = pl_conn or pl.init_db()
    bankroll_conn = bankroll_conn or paper_bankroll.init_db()
    try:
        if game_ids is None:
            game_ids = [r["game_id"] for r in conn.execute(
                "SELECT game_id FROM games WHERE game_state = 'SCHEDULED' ORDER BY game_date, game_id"
            ).fetchall()]

        for game_id in game_ids:
            summary["games_evaluated"] += 1
            try:
                pred = build_prediction_for_game(conn, game_id)
            except Exception as exc:  # noqa: BLE001 -- one bad game must never abort the slate
                summary["results"].append({"game_id": game_id, "status": "ERROR",
                                            "reason": f"{exc.__class__.__name__}: {exc}"})
                continue
            label = f"{pred.away_team} @ {pred.home_team} ({pred.game_date[:10]})"
            reports = pricing_engine.evaluate_moneyline_for_game(conn, pred, label)
            for report in reports:
                summary["reports_evaluated"] += 1
                result = _process_report(conn, pl_conn, bankroll_conn, pred, report)
                summary["results"].append(result)
                if result["status"] == "DATA_UNAVAILABLE":
                    summary["data_unavailable"] += 1
                elif result.get("recorded"):
                    summary["recommendations_recorded"] += 1
                if result.get("paper_bet_created"):
                    summary["paper_bets_created"] += 1
    finally:
        if owns_pl_conn:
            pl_conn.close()
        if owns_bankroll_conn:
            bankroll_conn.close()
        if owns_conn:
            conn.close()
    return summary


if __name__ == "__main__":
    import json as _json
    result = run_real_moneyline_recommendations()
    print(_json.dumps(result, indent=2, default=str))
