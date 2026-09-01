-- Fantasy domain storage schema (2026-09-01 sprint).
--
-- Isolated from the betting engine's operational stores
-- (operational/prospective_observations.db, operational/paper_bankroll.db)
-- -- a separate database file, never shared tables.
--
-- PRIVACY (Part 127): every table below is keyed by user_key. Every
-- fantasy_store.py read/write function requires an explicit user_key
-- argument -- there is no "current user" global anywhere in this
-- schema or its accessor module, so a public viewer can never see
-- another authenticated user's data via URL manipulation as long as
-- callers always pass the real authenticated session's own user_key
-- (enforced at the dashboard page layer, tested in
-- tests/test_fantasy_privacy.py).
--
-- TEMPORAL INTEGRITY (Part 19/83): every *_snapshots table is
-- APPEND-ONLY. Rows are never UPDATEd or DELETEd by normal sync
-- operation -- each sync writes a NEW row with its own observed_at, so
-- "what did the assistant know at time T" is always reconstructable.
-- fantasy_recommendations is the same append-only pattern (Part 81/83
-- -- a recommendation snapshot is never retroactively mutated after
-- outcomes arrive).

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

-- Yahoo OAuth tokens -- one row per authenticated user. This table (and
-- the whole fantasy_store.db file) is gitignored; NEVER committed.
-- access_token/refresh_token are the real Yahoo-issued values -- never
-- logged, never displayed in the UI (Part 118's own explicit rule).
CREATE TABLE IF NOT EXISTS yahoo_tokens (
    user_key TEXT PRIMARY KEY,
    access_token TEXT NOT NULL,
    refresh_token TEXT NOT NULL,
    expires_at_epoch REAL NOT NULL,
    updated_at_utc TEXT NOT NULL
);

-- Which league/team the user has selected (Part 9/10) -- remembered
-- per authenticated user, never global state.
CREATE TABLE IF NOT EXISTS user_selection (
    user_key TEXT PRIMARY KEY,
    game_key TEXT,
    league_key TEXT,
    team_key TEXT,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS league_settings_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_key TEXT NOT NULL,
    league_key TEXT NOT NULL,
    settings_json TEXT NOT NULL,
    settings_hash TEXT NOT NULL,
    observed_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_league_settings_lookup
    ON league_settings_snapshots (user_key, league_key, observed_at_utc);

CREATE TABLE IF NOT EXISTS roster_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_key TEXT NOT NULL,
    league_key TEXT NOT NULL,
    team_key TEXT NOT NULL,
    roster_json TEXT NOT NULL,
    observed_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_roster_lookup
    ON roster_snapshots (user_key, team_key, observed_at_utc);

CREATE TABLE IF NOT EXISTS standings_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_key TEXT NOT NULL,
    league_key TEXT NOT NULL,
    standings_json TEXT NOT NULL,
    observed_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_standings_lookup
    ON standings_snapshots (user_key, league_key, observed_at_utc);

CREATE TABLE IF NOT EXISTS matchup_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_key TEXT NOT NULL,
    league_key TEXT NOT NULL,
    team_key TEXT NOT NULL,
    week TEXT,
    matchup_json TEXT NOT NULL,
    observed_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_matchup_lookup
    ON matchup_snapshots (user_key, team_key, week, observed_at_utc);

CREATE TABLE IF NOT EXISTS available_players_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_key TEXT NOT NULL,
    league_key TEXT NOT NULL,
    players_json TEXT NOT NULL,
    observed_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_available_players_lookup
    ON available_players_snapshots (user_key, league_key, observed_at_utc);

-- Part 81: prospective recommendation ledger -- immutable once written.
CREATE TABLE IF NOT EXISTS fantasy_recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_key TEXT NOT NULL,
    league_key TEXT NOT NULL,
    team_key TEXT NOT NULL,
    player_id TEXT,
    recommendation_type TEXT NOT NULL,  -- START | BENCH | ADD | DROP | STREAM
    reason TEXT NOT NULL,
    projection_json TEXT,
    confidence TEXT,
    executed INTEGER NOT NULL DEFAULT 0,  -- Part 88: RECOMMENDED vs EXECUTED -- never assumed 1
    observed_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_recommendations_lookup
    ON fantasy_recommendations (user_key, team_key, observed_at_utc);

-- Part 77: private per-user watchlist (app-internal; Yahoo has no
-- relevant write endpoint for this).
CREATE TABLE IF NOT EXISTS watchlist (
    user_key TEXT NOT NULL,
    player_id TEXT NOT NULL,
    added_at_utc TEXT NOT NULL,
    PRIMARY KEY (user_key, player_id)
);

INSERT INTO schema_version (version) SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM schema_version);
