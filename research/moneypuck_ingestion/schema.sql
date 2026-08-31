-- Separate RESEARCH storage for MoneyPuck team game-by-game data.
--
-- Deliberately NOT part of nhl.db / schema.sql's production PIT schema,
-- and deliberately NOT reusing production player_game_stats /
-- goalie_game_stats -- MoneyPuck is an external analytics source with
-- its own provenance/licensing posture, kept fully separate per
-- MONEYPUCK_DATA_CONTRACT_REVIEW.md and this slice's explicit
-- instruction. game_id is the NHL's own canonical game id (verified
-- identical ID space to the real NHL corpus -- see
-- validate_against_nhl_corpus.py), used here purely as a join key, NOT
-- as a claim that this table participates in the production PIT
-- architecture.
--
-- Revision model: append-only. A row is only ever INSERTed, never
-- UPDATEd -- a later raw snapshot that disagrees with an earlier one for
-- the same natural key produces a NEW row (a new revision), never an
-- overwrite. "Current" value for a natural key is the row with the
-- latest downloaded_at_utc. See research/moneypuck_ingestion/ingest.py's
-- classify_and_ingest_row() for how NEW/REVISED/UNCHANGED is decided
-- before a row is written.

CREATE TABLE IF NOT EXISTS research_moneypuck_team_game_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- game identity
    game_id INTEGER NOT NULL,
    season INTEGER NOT NULL,             -- canonical YYYYZZZZ form, e.g. 20222023
    game_date TEXT NOT NULL,             -- YYYY-MM-DD
    team TEXT NOT NULL,
    opponent TEXT NOT NULL,
    situation TEXT NOT NULL,             -- 'all' / '5on5' / '5on4' / '4on5' / 'other' -- preserved verbatim, never collapsed
    home_or_away TEXT NOT NULL,

    -- ice time (MoneyPuck's `iceTime`, verified real units: SECONDS, not
    -- minutes -- e.g. a real 5on4 row of 372.0 is a plausible ~6.2-minute
    -- single-game PP shift total, not 372 minutes). Stored in raw source
    -- units; conversion (e.g. to a per-60-minute rate) happens at the
    -- point of use in feature-computation code, never at ingestion.
    -- Added for the special-teams feature slice -- absent from the
    -- original team-data ingestion foundation, backfilled via a REVISED
    -- re-ingestion of the same already-archived raw file (see
    -- MONEYPUCK_SPECIAL_TEAMS_EXPERIMENT_REPORT.md).
    ice_time_seconds REAL,

    -- result / basic performance
    goals_for INTEGER NOT NULL,
    goals_against INTEGER NOT NULL,
    shots_for INTEGER NOT NULL,
    shots_against INTEGER NOT NULL,

    -- expected goals
    xg_for REAL NOT NULL,
    xg_against REAL NOT NULL,

    -- shot / chance quality
    shot_attempts_for INTEGER,
    shot_attempts_against INTEGER,
    unblocked_shot_attempts_for INTEGER,
    unblocked_shot_attempts_against INTEGER,
    high_danger_shots_for INTEGER,
    high_danger_shots_against INTEGER,
    medium_danger_shots_for INTEGER,
    medium_danger_shots_against INTEGER,
    low_danger_shots_for INTEGER,
    low_danger_shots_against INTEGER,
    high_danger_xg_for REAL,
    high_danger_xg_against REAL,
    medium_danger_xg_for REAL,
    medium_danger_xg_against REAL,
    low_danger_xg_for REAL,
    low_danger_xg_against REAL,
    rebounds_for INTEGER,
    rebounds_against INTEGER,

    -- score-adjusted fields
    score_adjusted_shot_attempts_for REAL,
    score_adjusted_shot_attempts_against REAL,
    score_venue_adjusted_xg_for REAL,
    score_venue_adjusted_xg_against REAL,

    -- provenance (Section: PROVENANCE MODEL / S)
    provenance_type TEXT NOT NULL,       -- 'ARCHIVAL_RESEARCH' or 'LIVE_OBSERVED' -- NEVER a fabricated historical observed_at
    source TEXT NOT NULL DEFAULT 'MoneyPuck',
    source_file TEXT NOT NULL,           -- archived raw file path (research/moneypuck_ingestion/raw/...)
    source_sha256 TEXT NOT NULL,         -- sha256 of the raw file this row was parsed from
    downloaded_at_utc TEXT NOT NULL,     -- when the raw file was actually obtained (never fabricated/backdated)
    ingested_at_utc TEXT NOT NULL,       -- when THIS row was written to this table

    -- xG version-semantics warning (Section: XG VERSION SEMANTICS) --
    -- carried on every row so no downstream consumer can lose the caveat
    -- by joining only against a subset of columns.
    xg_model_version_semantics TEXT NOT NULL DEFAULT 'UNKNOWN'
);

-- One row per (game_id, team, situation, source_sha256): re-ingesting the
-- IDENTICAL raw file is a structural no-op at the SQL level (INSERT OR
-- IGNORE), on top of the classification logic in ingest.py that decides
-- NEW vs REVISED vs UNCHANGED before even attempting the insert.
CREATE UNIQUE INDEX IF NOT EXISTS idx_research_moneypuck_natural_key_per_snapshot
    ON research_moneypuck_team_game_stats(game_id, team, situation, source_sha256);

-- Fast lookup for the PIT-safe research query helpers in query.py.
CREATE INDEX IF NOT EXISTS idx_research_moneypuck_team_date
    ON research_moneypuck_team_game_stats(team, situation, game_date);

CREATE INDEX IF NOT EXISTS idx_research_moneypuck_game
    ON research_moneypuck_team_game_stats(game_id, team, situation);
