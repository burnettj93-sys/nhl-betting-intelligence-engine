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
    track                   TEXT NOT NULL CHECK (track IN ('REAL_MARKET_PAPER', 'DEMO_PAPER', 'GAME_PARLAY_PAPER')),
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
    notes                                 TEXT,
    settlement_json                       TEXT, -- v3: per-leg results + repriced odds applied at settlement

    -- v4: where the ticket came from. Existing rows are AUTOMATIC (the column default; nothing was rewritten).
    -- MANUALLY_ADDED tickets carry the exact details the user accepted in provenance_json, frozen like every entry field.
    origin                                TEXT NOT NULL DEFAULT 'AUTOMATIC' CHECK (origin IN ('AUTOMATIC', 'MANUALLY_ADDED')),
    provenance_json                       TEXT
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
    NEW.idempotency_key IS NOT OLD.idempotency_key OR
    NEW.legs_json IS NOT OLD.legs_json OR
    NEW.origin IS NOT OLD.origin OR
    NEW.provenance_json IS NOT OLD.provenance_json
BEGIN
    SELECT RAISE(ABORT, 'paper_bets: entry fields are immutable after creation -- only settlement columns may change');
END;

CREATE TABLE IF NOT EXISTS paper_audit_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_utc   TEXT NOT NULL,
    paper_bet_id     TEXT NOT NULL,
    action            TEXT NOT NULL CHECK (action IN ('INSERT', 'SETTLE', 'VOID'))
);

-- v3: revalidation findings are ALERTS ONLY. A changed goalie, roster move or
-- schedule revision never refunds, voids or edits a recorded ticket; it is
-- written here so the Today screen can show it. UNIQUE keeps a 15-minute
-- revalidation cycle from writing the same alert repeatedly.
CREATE TABLE IF NOT EXISTS ticket_alerts (
    alert_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_bet_id    TEXT NOT NULL,
    kind            TEXT NOT NULL,
    detail          TEXT NOT NULL,
    created_at_utc  TEXT NOT NULL,
    UNIQUE (paper_bet_id, kind, detail)
);

-- v3 hardening: the database itself refuses a row that could corrupt the account, whatever wrote it. A
-- non-positive or missing stake, or a price that is not American odds, aborts the insert. (Applies to new rows
-- only; nothing already stored is touched.)
CREATE TRIGGER IF NOT EXISTS paper_bets_valid_stake_and_odds
BEFORE INSERT ON paper_bets
FOR EACH ROW
WHEN NEW.stake IS NULL OR NEW.stake <= 0 OR NEW.entry_odds IS NULL OR ABS(NEW.entry_odds) < 100
BEGIN
    SELECT RAISE(ABORT, 'paper_bets: stake must be > 0 and entry_odds must be American odds (|odds| >= 100)');
END;

-- v4: every manual "Add to paper book" request that reached the engine, whatever happened to it. One row per order id, so a
-- repeated click or a retried transport can never be processed twice; the ticket (if any) is the paper_bets row.
CREATE TABLE IF NOT EXISTS manual_orders (
    order_id           TEXT PRIMARY KEY,
    source             TEXT NOT NULL,
    option_id          TEXT,
    status             TEXT NOT NULL CHECK (status IN ('RECORDED', 'ALREADY_RECORDED', 'NEEDS_ACCEPTANCE', 'REJECTED')),
    reason             TEXT,
    ticket_id          TEXT,
    request_json       TEXT NOT NULL,
    detail_json        TEXT,
    received_at_utc    TEXT NOT NULL,
    processed_at_utc   TEXT NOT NULL
);

-- v4: manual Ontario spot checks. A person looks at the exact selection in DraftKings Ontario and records the price they saw. This is
-- evidence about one selection at one moment; it never changes a stored ticket and does not make the US-feed price an Ontario price.
CREATE TABLE IF NOT EXISTS ontario_verifications (
    verification_id    TEXT PRIMARY KEY,
    source             TEXT NOT NULL,
    game_id            TEXT NOT NULL,
    participant_id     TEXT NOT NULL,
    participant_name   TEXT,
    market_family      TEXT NOT NULL,
    threshold          INTEGER,
    side               TEXT,
    ontario_price      REAL NOT NULL CHECK (ABS(ontario_price) >= 100),
    us_price_shown     REAL,
    observed_at_utc    TEXT NOT NULL,
    recorded_at_utc    TEXT NOT NULL,
    where_seen         TEXT NOT NULL,
    notes              TEXT
);
