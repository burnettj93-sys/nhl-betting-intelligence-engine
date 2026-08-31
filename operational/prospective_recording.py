"""
Prospective recording orchestration (Preseason Closing sprint, Track 1).

Thin layer wiring the already-complete prospective ledger
(operational/prospective_ledger.py -- NOT rebuilt here) into the real
prediction workflow. This module's only job is to decide ELIGIBILITY and
shape a completed prediction into the ledger's insert call -- it never
computes a probability, never touches decision_policy, and never writes
to the ledger except through prospective_ledger's own public API.

Recording must happen strictly:
    AFTER probability computation, context overlay/coherence, and
    confidence determination
    BEFORE the game result is known (enforced by insert_prediction's own
    pre-game temporal guard)

DEMO_NOT_RECORDABLE is a hard, structural guard (Part 5): passing
is_demo=True always short-circuits before any ledger call is made.
"""
from __future__ import annotations

from operational import prospective_ledger as pl
from research.model_registry import get as get_model_registry_entry

# Statuses that must NEVER be prospectively recorded as validated
# operational model observations (Part 18). Research/failed work can
# still be tracked, but not through this eligibility gate.
INELIGIBLE_STATUSES = {"REJECTED", "INSUFFICIENT_DATA", "ATTEMPTED_NOT_VALIDATED"}

CHECKPOINTS = ("PRIMARY_DAILY", "PRE_GAME_UPDATE", "MARKET_REFRESH")
DEFAULT_CHECKPOINT = "PRIMARY_DAILY"


def is_eligible_for_recording(model_id: str) -> tuple[bool, str]:
    """Part 18/19: only VALIDATED / PARTIAL / SHADOW_VALIDATED-status
    models may be prospectively recorded as operational observations."""
    entry = get_model_registry_entry(model_id)
    if entry is None:
        return False, f"unknown model_id {model_id!r} -- not in MODEL_REGISTRY"
    if entry.status in INELIGIBLE_STATUSES:
        return False, f"model status {entry.status} is not eligible for prospective recording"
    return True, entry.status


class CheckpointOrderingError(Exception):
    pass


def latest_checkpoint_row(conn, *, game_id, player_id, market_id, threshold, side=None,
                           checkpoint: str | None = None) -> dict | None:
    """Preseason Operational Readiness Closure sprint (2026-08-30), Track
    4 Part 25/26: returns the most-recently-created row for one LOGICAL
    bet (game_id, player_id, market_id, threshold, side) across all its
    checkpoints, or -- if `checkpoint` is given -- only that specific
    checkpoint's row. Never conflates PRIMARY_DAILY and PRE_GAME_UPDATE
    rows silently: each checkpoint is its own separate, immutable insert
    (predictions are append-only by design -- nothing here "overwrites"
    anything at the DB level), and a caller that wants the canonical
    daily number must ask for checkpoint="PRIMARY_DAILY" explicitly
    rather than getting whichever row happens to be newest."""
    clauses = ["game_id = ?", "player_id = ?", "market_id = ?", "threshold = ?"]
    params = [game_id, player_id, market_id, threshold]
    if side is not None:
        clauses.append("side = ?")
        params.append(side)
    if checkpoint is not None:
        clauses.append("prediction_checkpoint = ?")
        params.append(checkpoint)
    row = conn.execute(
        f"SELECT * FROM predictions WHERE {' AND '.join(clauses)} "
        f"ORDER BY created_at_utc DESC LIMIT 1", params).fetchone()
    return dict(row) if row else None


def record_observation(conn, prediction: dict, is_demo: bool, checkpoint: str = DEFAULT_CHECKPOINT) -> dict:
    """The single entry point every real integration should call.

    `prediction` must already carry every field computed at prediction
    time: raw/context_adjusted/coherent/conservative probabilities,
    confidence, context_state, model/overlay/policy version snapshots,
    and market fields (NULL if no real price exists yet -- Part 13).
    `prediction["model_id"]` must name a real research.model_registry
    entry; `prediction["record_type"]` defaults to MODEL_OBSERVATION.

    Returns {"status": "DEMO_NOT_RECORDABLE"} without touching the
    ledger at all if is_demo=True (Part 5) -- checked FIRST, before any
    other validation, so a demo caller can never reach the ledger even
    via a malformed eligibility bypass.
    """
    if is_demo:
        return {"status": "DEMO_NOT_RECORDABLE", "prediction_id": None}

    if checkpoint not in CHECKPOINTS:
        raise ValueError(f"unknown prediction_checkpoint {checkpoint!r}, must be one of {CHECKPOINTS}")

    if checkpoint != "PRIMARY_DAILY":
        # Part 25/26: PRIMARY_DAILY is the canonical, first-of-day
        # checkpoint for a given (game, player, market, threshold, side)
        # -- a PRE_GAME_UPDATE or MARKET_REFRESH is a real, LATER,
        # legitimate refinement (price change, starter confirmation,
        # role freshness) and is always stored as its own separate row,
        # never as an update to the PRIMARY_DAILY row. Requiring
        # PRIMARY_DAILY to already exist first prevents a checkpoint
        # ordering bug where a later-checkpoint-only observation could
        # be mistaken for the canonical daily one.
        existing_primary = latest_checkpoint_row(
            conn, game_id=prediction.get("game_id"), player_id=prediction.get("player_id"),
            market_id=prediction.get("market_id"), threshold=prediction.get("threshold"),
            side=prediction.get("side"), checkpoint="PRIMARY_DAILY")
        if existing_primary is None:
            raise CheckpointOrderingError(
                f"{checkpoint} requires an existing PRIMARY_DAILY checkpoint for this same "
                f"(game_id={prediction.get('game_id')!r}, player_id={prediction.get('player_id')!r}, "
                f"market_id={prediction.get('market_id')!r}, threshold={prediction.get('threshold')!r}) "
                f"prediction first")

    model_id = prediction.get("model_id")
    eligible, reason = is_eligible_for_recording(model_id)
    if not eligible:
        return {"status": "INELIGIBLE_MODEL", "reason": reason, "prediction_id": None}

    fields = {k: v for k, v in prediction.items() if k != "model_id"}
    fields["prediction_checkpoint"] = checkpoint
    fields.setdefault("prospective_status", "PROSPECTIVE_PENDING")
    fields.setdefault("model_status", reason)

    record_type = fields.pop("record_type", "MODEL_OBSERVATION")
    if record_type == "SHADOW_POLICY_OBSERVATION":
        return pl.record_shadow_observation(conn, **fields)
    return pl.record_model_observation(conn, **fields)


def record_sog_board_row(conn, row: dict, checkpoint: str = DEFAULT_CHECKPOINT, is_demo: bool = False) -> dict:
    """Section 15: the REFERENCE integration. Live SOG is the only
    market with a verified sportsbook payload contract today -- every
    other market's future integration should follow this exact pattern:
    adapt that market's own real row schema into the generic
    `prediction` dict `record_observation` expects, never invent fields
    that row schema doesn't actually have."""
    prediction = {
        "record_type": "MODEL_OBSERVATION", "model_id": "PLAYER_SOG",
        "event_start_utc": row["event_start_utc"],
        "prediction_cutoff_utc": row.get("quote_captured_at_utc"),
        "game_id": row.get("game_id"), "game_date": row.get("game_date"),
        "player_id": row.get("player_id"), "player_name_snapshot": row.get("player_name_raw"),
        "team": row.get("team"), "opponent": row.get("opponent"),
        "market_id": "PLAYER_SOG_3PLUS", "market_family": "SOG",
        "threshold": row.get("threshold"), "side": row.get("side"),
        "raw_probability": row.get("model_probability"),
        "context_adjusted_probability": row.get("model_probability"),
        "coherent_probability": row.get("model_probability"),
        "conservative_probability": row.get("conservative_probability"),
        "confidence": row.get("confidence"), "model_version": row.get("model_version"),
        "sportsbook": "DraftKings" if row.get("draftkings_price") is not None else None,
        "market_key": row.get("market"), "line": row.get("point"),
        "odds_american": row.get("draftkings_price"),
        "market_no_vig_probability": row.get("market_no_vig_probability"),
        "odds_captured_at_utc": row.get("quote_captured_at_utc"),
    }
    return record_observation(conn, prediction, is_demo=is_demo, checkpoint=checkpoint)


def record_context_eligible_observation(conn, *, model_id: str, player_id: str, game_id: str, game_date: str,
                                         event_start_utc: str, prediction_cutoff_utc: str, team: str,
                                         opponent: str, raw_probability: float,
                                         context_adjusted_probability: float, coherent_probability: float,
                                         confidence: str, context_state: str | None = None,
                                         model_version: str | None = None,
                                         context_overlay_version: str | None = None,
                                         current_policy_status: str | None = None,
                                         checkpoint: str = DEFAULT_CHECKPOINT, is_demo: bool = False) -> dict:
    """Sections 4/16: Goals/Points (and, generically, any prop this
    project's context overlay covers) -- recordable even with NO
    verified live sportsbook payload; market fields simply stay NULL
    (Part 13). Automatically records as SHADOW_POLICY_OBSERVATION with
    the raw/shadow policy pair populated when context_state indicates
    the player is in the overlay-eligible state; MODEL_OBSERVATION
    otherwise -- callers never have to choose the record_type by hand."""
    is_shadow_eligible = context_state == "COLD_AND_TOI_DECLINE"
    prediction = {
        "record_type": "SHADOW_POLICY_OBSERVATION" if is_shadow_eligible else "MODEL_OBSERVATION",
        "model_id": model_id, "player_id": player_id, "game_id": game_id, "game_date": game_date,
        "event_start_utc": event_start_utc, "prediction_cutoff_utc": prediction_cutoff_utc,
        "team": team, "opponent": opponent,
        "market_id": f"PLAYER_{model_id}_1PLUS", "market_family": model_id, "threshold": "1+",
        "raw_probability": raw_probability, "context_adjusted_probability": context_adjusted_probability,
        "coherent_probability": coherent_probability, "confidence": confidence,
        "context_state": context_state, "model_version": model_version,
        "context_overlay_version": context_overlay_version,
        "current_policy_status": current_policy_status,
        "raw_policy_input_probability": raw_probability if is_shadow_eligible else None,
        "shadow_context_policy_probability": context_adjusted_probability if is_shadow_eligible else None,
    }
    return record_observation(conn, prediction, is_demo=is_demo, checkpoint=checkpoint)


def settle_completed_observation(conn, prediction_id: str, actual_outcome, result_status: str,
                                  closing_odds: float | None = None,
                                  closing_captured_at_utc: str | None = None,
                                  notes: str | None = None) -> dict:
    """Section 20/21/23/24: a safe, thin settlement wrapper. Uses ONLY
    official-outcome data supplied by the caller (Section 21: no
    bookmaker-result scraping happens here or anywhere in this project)
    and pl.settle_prediction's own write-only-result-fields guarantee --
    this function computes nothing new except CLV, and only when both a
    real recorded price and a real closing price exist (Section 24)."""
    obs = pl.get_observation(conn, prediction_id)
    if obs is None:
        raise ValueError(f"no observation with id {prediction_id}")
    clv = None
    if obs.get("odds_american") is not None and closing_odds is not None:
        if obs.get("event_start_utc") and closing_captured_at_utc and closing_captured_at_utc >= obs["event_start_utc"]:
            raise ValueError("closing_captured_at_utc must be strictly before event_start_utc (Section 23)")
        from pricing import odds_math as pm
        clv = pm.american_to_prob(closing_odds) - pm.american_to_prob(obs["odds_american"])
    return pl.settle_prediction(conn, prediction_id, result_status, actual_outcome=str(actual_outcome),
                                 closing_odds=closing_odds, closing_captured_at_utc=closing_captured_at_utc, clv=clv,
                                 notes=notes)
