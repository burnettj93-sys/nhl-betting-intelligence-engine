-- Prospective observation store schema (v1). Separate database from
-- nhl.db (production demo DB) -- this is new operational infrastructure,
-- never mixed with the production schema.
--
-- Immutability (Part 3/6 of the Preseason Operationalization sprint) is
-- enforced at the DATABASE level, not just in the Python API: the
-- predictions_immutability trigger below aborts any UPDATE that touches
-- a prediction-time field. Only settle_prediction()'s settlement columns
-- (result_status, actual_outcome, settled_at_utc, profit_loss,
-- closing_odds, closing_captured_at_utc, clv, notes) may ever change
-- after insertion.

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS predictions (
    prediction_id               TEXT PRIMARY KEY,
    idempotency_key              TEXT NOT NULL UNIQUE,
    record_type                  TEXT NOT NULL CHECK (record_type IN
                                  ('MODEL_OBSERVATION', 'SHADOW_POLICY_OBSERVATION',
                                   'REAL_BET', 'HISTORICAL_RESEARCH')),
    created_at_utc                TEXT NOT NULL,
    prediction_cutoff_utc         TEXT,
    event_start_utc               TEXT NOT NULL,

    game_id                       TEXT,
    game_date                     TEXT,
    player_id                     TEXT,
    player_name_snapshot          TEXT,
    team                          TEXT,
    opponent                      TEXT,

    market_id                     TEXT NOT NULL,
    market_family                 TEXT,
    threshold                     TEXT,
    side                          TEXT,
    prediction_checkpoint         TEXT CHECK (prediction_checkpoint IS NULL OR prediction_checkpoint IN
                                   ('PRIMARY_DAILY', 'PRE_GAME_UPDATE', 'MARKET_REFRESH')),

    raw_probability               REAL,
    context_adjusted_probability  REAL,
    coherent_probability          REAL,
    conservative_probability      REAL,
    confidence                    TEXT,
    context_state                 TEXT,
    context_overlay_status        TEXT,
    model_status                  TEXT,
    prospective_status            TEXT,

    decision_policy_version       TEXT,
    current_policy_status         TEXT,
    shadow_policy_status          TEXT,
    raw_policy_input_probability  REAL,
    shadow_context_policy_probability REAL,
    future_policy_candidate       TEXT,

    model_version                 TEXT,
    model_hash                    TEXT,
    context_overlay_version       TEXT,
    context_overlay_hash          TEXT,
    registry_version               TEXT,
    registry_hash                  TEXT,
    data_snapshot_references       TEXT,  -- JSON blob

    sportsbook                    TEXT,
    book_event_id                 TEXT,
    market_key                    TEXT,
    line                           REAL,
    odds_american                 REAL,
    odds_decimal                   REAL,
    market_implied_probability     REAL,
    market_no_vig_probability      REAL,
    odds_captured_at_utc           TEXT,
    odds_received_at_utc           TEXT,
    data_freshness_status          TEXT,

    -- REAL_BET-only fields, required together when record_type = 'REAL_BET'
    stake                          REAL,
    placed_odds                    REAL,
    placed_at_utc                  TEXT,

    -- v3 (Live Special-Teams Role Shadow sprint): SOG PP-role SHADOW
    -- overlay fields -- a snapshot of the shadow probability and the
    -- exact role features used to compute it AT PREDICTION TIME (Part
    -- 62); never touched by the shadow overlay's own real BET/decision
    -- logic (there is none -- see operational/sog_shadow_overlay.py's
    -- own docstring: shadow probabilities never affect a real decision).
    sog_shadow_raw_probability          REAL,
    sog_shadow_conservative_probability REAL,
    pp_role_state                       TEXT,
    pp_role_certainty                   REAL,
    pp_transition_state                 TEXT,
    pp_games_since_transition           INTEGER,
    role_overlay_version                TEXT,

    -- settlement fields (mutable via settle_prediction() only)
    result_status                  TEXT NOT NULL DEFAULT 'PENDING' CHECK (result_status IN
                                    ('PENDING', 'WIN', 'LOSS', 'PUSH', 'VOID', 'UNRESOLVED')),
    actual_outcome                 TEXT,
    settled_at_utc                 TEXT,
    profit_loss                    REAL,
    closing_odds                   REAL,
    closing_captured_at_utc        TEXT,
    clv                            REAL,
    notes                          TEXT
);

CREATE INDEX IF NOT EXISTS idx_predictions_record_type ON predictions(record_type);
CREATE INDEX IF NOT EXISTS idx_predictions_game_date ON predictions(game_date);
CREATE INDEX IF NOT EXISTS idx_predictions_player_id ON predictions(player_id);
CREATE INDEX IF NOT EXISTS idx_predictions_market_id ON predictions(market_id);
CREATE INDEX IF NOT EXISTS idx_predictions_context_state ON predictions(context_state);

CREATE TRIGGER IF NOT EXISTS predictions_immutability
BEFORE UPDATE ON predictions
FOR EACH ROW
WHEN
    NEW.record_type IS NOT OLD.record_type OR
    NEW.created_at_utc IS NOT OLD.created_at_utc OR
    NEW.prediction_cutoff_utc IS NOT OLD.prediction_cutoff_utc OR
    NEW.event_start_utc IS NOT OLD.event_start_utc OR
    NEW.game_id IS NOT OLD.game_id OR
    NEW.player_id IS NOT OLD.player_id OR
    NEW.market_id IS NOT OLD.market_id OR
    NEW.threshold IS NOT OLD.threshold OR
    NEW.side IS NOT OLD.side OR
    NEW.prediction_checkpoint IS NOT OLD.prediction_checkpoint OR
    NEW.raw_probability IS NOT OLD.raw_probability OR
    NEW.context_adjusted_probability IS NOT OLD.context_adjusted_probability OR
    NEW.coherent_probability IS NOT OLD.coherent_probability OR
    NEW.conservative_probability IS NOT OLD.conservative_probability OR
    NEW.confidence IS NOT OLD.confidence OR
    NEW.context_state IS NOT OLD.context_state OR
    NEW.model_version IS NOT OLD.model_version OR
    NEW.model_hash IS NOT OLD.model_hash OR
    NEW.context_overlay_version IS NOT OLD.context_overlay_version OR
    NEW.context_overlay_hash IS NOT OLD.context_overlay_hash OR
    NEW.decision_policy_version IS NOT OLD.decision_policy_version OR
    NEW.odds_captured_at_utc IS NOT OLD.odds_captured_at_utc OR
    NEW.odds_american IS NOT OLD.odds_american OR
    NEW.idempotency_key IS NOT OLD.idempotency_key OR
    NEW.sog_shadow_raw_probability IS NOT OLD.sog_shadow_raw_probability OR
    NEW.sog_shadow_conservative_probability IS NOT OLD.sog_shadow_conservative_probability OR
    NEW.pp_role_state IS NOT OLD.pp_role_state OR
    NEW.pp_role_certainty IS NOT OLD.pp_role_certainty OR
    NEW.pp_transition_state IS NOT OLD.pp_transition_state OR
    NEW.pp_games_since_transition IS NOT OLD.pp_games_since_transition OR
    NEW.role_overlay_version IS NOT OLD.role_overlay_version
BEGIN
    SELECT RAISE(ABORT, 'immutable prediction field cannot be modified after insertion');
END;

CREATE TABLE IF NOT EXISTS audit_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_utc   TEXT NOT NULL,
    prediction_id   TEXT NOT NULL,
    action          TEXT NOT NULL CHECK (action IN ('INSERT', 'SETTLE', 'VOID'))
);
