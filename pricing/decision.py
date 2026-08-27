"""
Persists every prediction/decision atomically, with the full feature
snapshot that produced it, so its MODEL PREDICTION can be replayed later
(spec item 8/10 — "rerunning a stored prediction reproduces the original
output"). See reproduce()'s docstring (clarified v2.1.2a spec item 10) for
the exact, narrower scope of what "replayed" means here.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3

import config


def persist_bare_prediction(conn: sqlite3.Connection, pred) -> int:
    """Used during walk-forward training/backtesting: records the model's
    raw probability output (no odds, no action) for audit + reproducibility
    testing. `pred` is a models.combined_model.GamePrediction."""
    cur = conn.execute(
        """INSERT INTO predictions
           (game_id, market, selection, prediction_time_utc, model_version, feature_version,
            feature_snapshot_json, model_true_probability, model_conservative_probability,
            ci_low, ci_high, home_goalie_status, away_goalie_status, generated_at_utc)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (pred.game_id, "MONEYLINE", pred.home_team, pred.prediction_time_utc,
         pred.model_version, pred.feature_version, json.dumps(pred.feature_snapshot),
         pred.model_prob_home, pred.conservative_prob_home, pred.ci_low, pred.ci_high,
         pred.home_goalie_status, pred.away_goalie_status, dt.datetime.utcnow().isoformat()),
    )
    return cur.lastrowid


def persist_full_decision(conn: sqlite3.Connection, pred, report) -> int:
    """Used at real pricing time: records the model output PLUS the market
    comparison and the BET/WAIT/PASS/DATA_UNAVAILABLE decision — everything
    needed to explain or replay this exact recommendation later."""
    cur = conn.execute(
        """INSERT INTO predictions
           (game_id, market, selection, prediction_time_utc, model_version, feature_version,
            feature_snapshot_json, model_true_probability, model_conservative_probability,
            ci_low, ci_high, market_no_vig_probability, sportsbook,
            odds_snapshot_id_selection, odds_snapshot_id_opponent,
            conservative_edge, expected_value, maximum_acceptable_price,
            action, action_reason, stake_fraction,
            home_goalie_status, away_goalie_status, generated_at_utc)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (pred.game_id, report.market, report.selection, pred.prediction_time_utc,
         pred.model_version, pred.feature_version, json.dumps(pred.feature_snapshot),
         report.model_true_probability, report.model_conservative_probability,
         pred.ci_low, pred.ci_high, report.market_no_vig_probability, report.sportsbook,
         report.odds_snapshot_id_selection, report.odds_snapshot_id_opponent,
         report.conservative_edge, report.expected_value, report.maximum_acceptable_draftkings_price,
         report.action, report.action_reason, report.kelly_stake_fraction,
         pred.home_goalie_status, pred.away_goalie_status, dt.datetime.utcnow().isoformat()),
    )
    conn.commit()
    return cur.lastrowid


def reproduce(conn: sqlite3.Connection, prediction_id: int) -> dict:
    """MODEL-PREDICTION reproducibility, NOT full decision reproducibility
    (v2.1.2a spec item 10 — clarified terminology; behavior unchanged).
    Replays a stored prediction from its persisted feature snapshot alone
    (no DB reads beyond fetching the row itself) and returns both the
    original and recomputed {model_prob_home, conservative_prob_home,
    ci_low, ci_high} so a caller/test can assert equality.

    This does NOT recompute or compare the no-vig market probability,
    conservative edge, expected value, maximum acceptable price, staking,
    or the BET/WAIT/PASS/DATA_UNAVAILABLE action — those are pricing-time
    outputs of pricing/engine.py, not the model. Calling this against a
    prediction_id that was persisted via persist_full_decision() (which
    also stores those pricing fields) still only replays and compares the
    model-prediction fields above; the pricing fields it stored are simply
    not touched by this function either way. A full, versioned DECISION
    replay (recomputing and comparing the pricing/action layer too) is
    explicitly out of scope this slice — see spec item 11 — and is not
    built here."""
    from models.combined_model import reproduce_prediction

    row = conn.execute("SELECT * FROM predictions WHERE id=?", (prediction_id,)).fetchone()
    if row is None:
        raise ValueError(f"no prediction with id {prediction_id}")
    fs = json.loads(row["feature_snapshot_json"])
    recomputed = reproduce_prediction(fs)
    original = {
        "model_prob_home": row["model_true_probability"],
        "conservative_prob_home": row["model_conservative_probability"],
        "ci_low": row["ci_low"], "ci_high": row["ci_high"],
    }
    return {"original": original, "recomputed": recomputed,
            "model_version": row["model_version"], "feature_version": row["feature_version"]}
