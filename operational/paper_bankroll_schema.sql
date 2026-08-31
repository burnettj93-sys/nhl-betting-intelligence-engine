-- Live DK / Paper Bankroll completion sprint (2026-08-31), Parts 24-49.
-- A SEPARATE SQLite database from both nhl.db (production demo DB) and
-- operational/prospective_observations.db (the real prospective
-- evaluation ledger) -- PAPER_BET is a distinct concept from both
-- MODEL_OBSERVATION and REAL_BET (Part 25/26), so it gets its own store
-- rather than overloading either existing one's schema/validation rules.

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS paper_bets (
    paper_bet_id            TEXT PRIMARY KEY,
    idempotency_key         TEXT NOT NULL UNIQUE,
    track                   TEXT NOT NULL CHECK (track IN ('REAL_MARKET_PAPER', 'DEMO_PAPER')),
    is_combo                INTEGER NOT NULL DEFAULT 0,
    top_conviction          INTEGER NOT NULL DEFAULT 0,

    -- identity (frozen at entry, Part 30)
    event_id                TEXT,
    game_date               TEXT,
    player_id               TEXT,
    player_name_snapshot    TEXT,
    team                    TEXT,
    opponent                TEXT,
    market_id               TEXT NOT NULL,
    market_family           TEXT,
    threshold                TEXT,
    side                     TEXT,
    price_source             TEXT NOT NULL CHECK (price_source IN ('LIVE_DRAFTKINGS', 'SIMULATED_DEMO')),
    legs_json                 TEXT,  -- combo legs snapshot (Part 45), NULL for a straight bet

    -- frozen entry snapshot (Part 30 -- NEVER updated after insert)
    entry_odds                REAL NOT NULL,
    model_probability          REAL,
    conservative_probability    REAL,
    market_no_vig_probability    REAL,
    edge                          REAL,
    ev                             REAL,
    confidence                      TEXT,
    model_version                    TEXT,
    prediction_checkpoint             TEXT,

    stake                              REAL NOT NULL,
    created_at_utc                      TEXT NOT NULL,
    event_start_utc                      TEXT,

    -- settlement (Part 33 -- the ONLY columns settle_paper_bet() ever writes)
    result_status                         TEXT NOT NULL DEFAULT 'PENDING'
                                           CHECK (result_status IN ('PENDING', 'WIN', 'LOSS', 'VOID', 'UNRESOLVED')),
    settled_at_utc                        TEXT,
    profit_loss                           REAL,
    closing_odds                          REAL,
    closing_captured_at_utc               REAL,
    clv                                   REAL,
    notes                                 TEXT
);

CREATE TRIGGER IF NOT EXISTS paper_bets_immutability
BEFORE UPDATE ON paper_bets
FOR EACH ROW
WHEN
    NEW.track IS NOT OLD.track OR
    NEW.is_combo IS NOT OLD.is_combo OR
    NEW.top_conviction IS NOT OLD.top_conviction OR
    NEW.market_id IS NOT OLD.market_id OR
    NEW.threshold IS NOT OLD.threshold OR
    NEW.side IS NOT OLD.side OR
    NEW.price_source IS NOT OLD.price_source OR
    NEW.entry_odds IS NOT OLD.entry_odds OR
    NEW.model_probability IS NOT OLD.model_probability OR
    NEW.conservative_probability IS NOT OLD.conservative_probability OR
    NEW.stake IS NOT OLD.stake OR
    NEW.created_at_utc IS NOT OLD.created_at_utc OR
    NEW.idempotency_key IS NOT OLD.idempotency_key
BEGIN
    SELECT RAISE(ABORT, 'paper_bets: entry fields are immutable after creation -- only settlement columns may change');
END;

CREATE TABLE IF NOT EXISTS paper_audit_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_utc   TEXT NOT NULL,
    paper_bet_id     TEXT NOT NULL,
    action            TEXT NOT NULL CHECK (action IN ('INSERT', 'SETTLE', 'VOID'))
);
