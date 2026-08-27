-- NHL Betting Intelligence Engine — schema v2 (temporal-integrity rewrite)
--
-- Core design rule: almost every fact that can change over time (roster
-- membership, injury status, lineup, starting goalie, sportsbook price) is
-- stored as an APPEND-ONLY event with two timestamps:
--   effective_at_utc  — when the fact became true in the world (or, for a
--                       scheduled/projected fact, when it's expected to
--                       apply)
--   observed_at_utc   — when THIS system learned about it
-- Every point-in-time query in features/point_in_time.py filters on
-- observed_at_utc <= prediction_time_utc. That is the single mechanism
-- that prevents look-ahead leakage — not game_id ordering, not caller
-- discipline. Nothing overwrites a prior row; corrections are new rows.

CREATE TABLE IF NOT EXISTS teams (
    team_id      TEXT PRIMARY KEY,
    full_name    TEXT,
    conference   TEXT,
    division     TEXT
);

-- Player identity only. Deliberately has NO team_id — affiliation is
-- temporal (see team_membership_events) so a trade doesn't rewrite who a
-- player was on their old team for games already played.
CREATE TABLE IF NOT EXISTS players (
    player_id   TEXT PRIMARY KEY,
    full_name   TEXT,
    position    TEXT       -- F / D / G
);

-- Dated team membership. A trade/recall/assignment is a NEW row, never an
-- edit of an old one. current membership = the row with the latest
-- effective_at_utc <= as_of and no later row superseding it.
CREATE TABLE IF NOT EXISTS team_membership_events (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id         TEXT,
    team_id           TEXT,
    effective_at_utc  TEXT,     -- when the move actually happened
    observed_at_utc   TEXT,     -- when we learned about it
    event_type        TEXT,     -- TRADE / RECALL / ASSIGNMENT / SIGNING / DRAFT / WAIVER
    source            TEXT
);

-- Games: schedule facts and result facts are stored with SEPARATE
-- observed timestamps, because they become knowable at very different
-- times. The schedule (teams, date, venue) is typically public months
-- ahead of puck drop; the result obviously isn't known until after.
-- Rest/schedule-congestion features should key off schedule_observed_at,
-- not result_observed_at.
--
-- v2.1 NOTE: game_date / scheduled_start_utc / home_team / away_team /
-- venue on THIS table are a "latest-known" convenience cache only -- they
-- are NOT point-in-time safe (a later schedule correction overwrites
-- them in place). Anything used to build a historical prediction's
-- features must instead go through game_schedule_events /
-- features.point_in_time.game_schedule_as_of(), which is append-only and
-- genuinely reconstructable as of any prediction_time_utc. game_id and
-- season are stable identity and are never revised.
--
-- v2.1.1 NOTE: game_state / home_score / away_score / final_period_type /
-- result_observed_at_utc are, likewise, a latest-known CURRENT-STATE CACHE
-- ONLY -- never authoritative for historical reconstruction. The
-- append-only game_result_events table below is the only point-in-time-
-- safe source for a game's result; anything deciding training eligibility
-- or what a historical model learned must go through
-- features.point_in_time.game_result_as_of() /
-- completed_games_known_before(), never these columns directly.
CREATE TABLE IF NOT EXISTS games (
    game_id                 INTEGER PRIMARY KEY,
    season                  TEXT,
    game_date               TEXT,          -- ISO date, UTC -- CACHE ONLY, see note above
    scheduled_start_utc     TEXT,          -- CACHE ONLY, see note above
    home_team               TEXT REFERENCES teams(team_id),  -- CACHE ONLY, see note above
    away_team               TEXT REFERENCES teams(team_id),  -- CACHE ONLY, see note above
    venue                   TEXT,          -- CACHE ONLY, see note above
    schedule_observed_at_utc TEXT,          -- when the schedule slot was FIRST known
    game_state               TEXT,          -- SCHEDULED / LIVE / FINAL
    home_score                INTEGER,
    away_score                INTEGER,
    final_period_type         TEXT,         -- REG / OT / SO
    result_observed_at_utc    TEXT,         -- when the final result became known; NULL until FINAL
    source                    TEXT
);

-- Append-only, revision-versioned final-game-result history (v2.1.1).
-- Same rationale as player_game_stats/goalie_game_stats below: a score
-- correction pulled later must never retroactively change WHEN a
-- prediction claims the result first became known, nor silently rewrite
-- what a historical model already learned from this game. Only FINISHED
-- games get a row here (game_state is always 'FINAL' in practice; the
-- column exists for forward-compatibility, not to record SCHEDULED/LIVE
-- state). Each observation (the first pull, or a later correction)
-- APPENDS a new row with revision_number = previous + 1; nothing is ever
-- UPDATEd in place. features.point_in_time.game_result_as_of(game_id,
-- as_of_utc) returns the latest revision observed by as_of_utc;
-- features.point_in_time.completed_games_known_before() derives
-- eligibility and chronological order from the FIRST observed_at_utc
-- across a game's revisions -- i.e. when the system first learned the
-- game was FINAL -- never from a later correction's timestamp.
CREATE TABLE IF NOT EXISTS game_result_events (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id            INTEGER,
    home_score         INTEGER,
    away_score         INTEGER,
    final_period_type  TEXT,      -- REG / OT / SO
    game_state         TEXT,      -- FINAL (only finished-game results are recorded here)
    effective_at_utc   TEXT,      -- when the result became true in the world
    observed_at_utc    TEXT,      -- when this system learned/re-learned this revision
    revision_number    INTEGER DEFAULT 1,
    source             TEXT,
    data_provider      TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_game_result_revision
    ON game_result_events (game_id, revision_number);

-- Append-only schedule history (v2.1). Every schedule fact used to build a
-- historical prediction's features must be read through this table (via
-- features.point_in_time.game_schedule_as_of), never from games' cache
-- columns above. A genuine schedule correction (time moved, venue
-- changed, teams swapped for a neutral-site game) appends a NEW row; it
-- never rewrites a prior one. Re-observing an unchanged schedule state is
-- a no-op (see ingest/nhl_api.py::ingest_schedule and
-- ingest/demo_data.py). effective_at_utc is when the schedule fact became
-- true in the world; observed_at_utc is when this system learned it --
-- exactly the same bitemporal pattern as every other event table here.
CREATE TABLE IF NOT EXISTS game_schedule_events (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id              INTEGER,
    game_date            TEXT,
    scheduled_start_utc  TEXT,
    home_team            TEXT,
    away_team            TEXT,
    venue                TEXT,
    effective_at_utc     TEXT,
    observed_at_utc      TEXT,
    source                TEXT,
    data_provider         TEXT
);

-- Roster/availability status events (spec sec.17/19): OUT / QUESTIONABLE /
-- IR / GTD / ACTIVE / SCRATCHED. Superseding status is a new row.
CREATE TABLE IF NOT EXISTS roster_status_events (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id          TEXT,
    team_id            TEXT,
    status             TEXT,     -- ACTIVE / OUT / QUESTIONABLE / IR / SCRATCHED / GTD
    effective_at_utc   TEXT,
    observed_at_utc    TEXT,
    expected_return_at TEXT,     -- nullable estimate, informational only
    confidence         REAL,     -- 0-1, source reliability (spec sec.62)
    source             TEXT
);

-- Per-game lineup construction: which line/pairing a player is projected
-- or confirmed in. Multiple rows per (game_id, player_id) over time as
-- the lineup firms up (spec sec.12/49/58 pregame timeline) — never update
-- a row in place.
CREATE TABLE IF NOT EXISTS lineup_snapshots (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id           INTEGER,
    team_id           TEXT,
    player_id         TEXT,
    role              TEXT,      -- L1F/L2F/.../D1/D2/D3 etc, or SCRATCHED
    status            TEXT,      -- PROJECTED / CONFIRMED
    effective_at_utc  TEXT,
    observed_at_utc   TEXT,
    source            TEXT
);

CREATE TABLE IF NOT EXISTS pp_unit_snapshots (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id           INTEGER,
    team_id           TEXT,
    player_id         TEXT,
    pp_unit           INTEGER,   -- 1 / 2
    status            TEXT,      -- PROJECTED / CONFIRMED
    effective_at_utc  TEXT,
    observed_at_utc   TEXT,
    source            TEXT
);

-- Starting goalie status, tracked independently per team per game — this
-- is what pricing/engine.py's WAIT logic reads. UNKNOWN -> EXPECTED ->
-- CONFIRMED -> CHANGED are all distinct rows over time, never overwrites.
CREATE TABLE IF NOT EXISTS goalie_status_events (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id           INTEGER,
    team_id           TEXT,
    player_id         TEXT,      -- nullable when status=UNKNOWN
    status            TEXT,      -- UNKNOWN / EXPECTED / CONFIRMED / CHANGED
    effective_at_utc  TEXT,
    observed_at_utc   TEXT,
    source            TEXT
);

-- Final box score. Explicitly POSTGAME-ONLY data — nothing in the
-- point-in-time feature layer may read `played`/goals/assists from here
-- to decide pregame availability; that comes from roster_status_events /
-- lineup_snapshots instead. This table exists for model *learning*
-- (post-hoc, after result_observed_at_utc) and for settlement, not pricing.
--
-- v2.1: REVISION-VERSIONED, not a single mutable row per (game_id,
-- player_id). A stat provider can and does correct box scores after the
-- fact (a missed assist, a shot-count fix); a correction must never be
-- allowed to retroactively change a model update that already happened
-- based on the version known at the time. Each correction APPENDS a new
-- row with revision_number = previous + 1 and its own observed_at_utc;
-- nothing is ever UPDATEd in place. features.point_in_time.
-- player_game_stats_as_of(game_id, learn_time_utc) returns, per player,
-- only the latest revision that had been observed by learn_time_utc.
CREATE TABLE IF NOT EXISTS player_game_stats (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id           INTEGER,
    player_id         TEXT,
    team_id           TEXT,
    toi_minutes       REAL,
    goals             INTEGER,
    assists           INTEGER,
    shots             INTEGER,
    played            INTEGER DEFAULT 1,
    revision_number   INTEGER DEFAULT 1,
    effective_at_utc  TEXT,
    observed_at_utc   TEXT,
    source            TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_player_game_stat_revision
    ON player_game_stats (game_id, player_id, revision_number);

-- v2.1: same revision-versioning rationale as player_game_stats above.
CREATE TABLE IF NOT EXISTS goalie_game_stats (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id           INTEGER,
    player_id         TEXT,
    team_id           TEXT,
    started           INTEGER,
    shots_against     INTEGER,
    saves             INTEGER,
    goals_against     INTEGER,
    revision_number   INTEGER DEFAULT 1,
    effective_at_utc  TEXT,
    observed_at_utc   TEXT,
    source            TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_goalie_game_stat_revision
    ON goalie_game_stats (game_id, player_id, revision_number);

-- Chronological Elo rating history — one row per team per game it played,
-- for audit only; the live model keeps ratings in memory during a run.
CREATE TABLE IF NOT EXISTS elo_ratings (
    team_id        TEXT,
    as_of_game_id  INTEGER,
    game_date      TEXT,
    rating         REAL,
    PRIMARY KEY (team_id, as_of_game_id)
);

-- Sportsbook prices. DraftKings is the engine's exclusive EXECUTION /
-- REFERENCE sportsbook for every supported market (config.REFERENCE_SPORTSBOOK)
-- — the only book any BET/WAIT/PASS decision is ever priced against. The
-- `sportsbook` column is free text specifically so this table can ALSO
-- later hold rows from other licensed books purely as MARKET-INTELLIGENCE
-- signals (consensus, lead/lag, sharp movement) without a schema change —
-- see config.MARKET_INTELLIGENCE_SPORTSBOOKS. That is explicitly not
-- implemented yet (v2.1 scope is temporal-integrity hardening only); this
-- comment exists so a future change doesn't require a destructive rewrite.
-- `sportsbook` (book of record) and `data_provider` (who we obtained the
-- row from) are deliberately separate columns; never conflate them.
-- `captured_at_utc` is the provider's own timestamp for when the price was
-- live at the book; `received_at_utc` is when this system ingested the row.
-- Append-only: the unique index makes a true duplicate a no-op insert,
-- never a silent overwrite of history.
CREATE TABLE IF NOT EXISTS odds_snapshots (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id           INTEGER,
    sportsbook        TEXT,        -- book of record, e.g. 'DraftKings'
    data_provider     TEXT,        -- who supplied this row, e.g. 'the-odds-api'
    market            TEXT,        -- MONEYLINE / PUCK_LINE / TOTAL / ...
    selection         TEXT,        -- team_id or player+side
    event_start_utc   TEXT,        -- scheduled puck drop, for post-start rejection
    line              REAL,        -- spread/total number, null for ML
    price_american    REAL,
    status            TEXT DEFAULT 'ACTIVE',  -- ACTIVE / SUSPENDED / INCOMPLETE
    captured_at_utc   TEXT,        -- provider's timestamp for this price
    received_at_utc   TEXT,        -- when this system ingested the row
    snapshot_label    TEXT         -- OPEN / OVERNIGHT / MORNING / ... / CLOSE
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_odds_snapshot_identity
    ON odds_snapshots (sportsbook, game_id, market, selection, captured_at_utc);

-- Every decision the engine has ever made, persisted atomically with the
-- full feature snapshot that produced it — this is what makes a decision
-- reproducible (see pricing/decision.py: recompute_probability_from_features
-- is a pure function over feature_snapshot_json, so replaying a stored row
-- through the same model_version must yield the same numbers).
CREATE TABLE IF NOT EXISTS predictions (
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id                         INTEGER,
    market                          TEXT,
    selection                       TEXT,
    prediction_time_utc             TEXT NOT NULL,
    model_version                   TEXT NOT NULL,
    feature_version                 TEXT NOT NULL,
    feature_snapshot_json           TEXT NOT NULL,   -- every raw input the model used
    model_true_probability          REAL,
    model_conservative_probability  REAL,
    ci_low                          REAL,
    ci_high                         REAL,
    market_no_vig_probability       REAL,
    sportsbook                      TEXT,             -- 'DraftKings' or NULL if unavailable
    odds_snapshot_id_selection      INTEGER,
    odds_snapshot_id_opponent       INTEGER,
    conservative_edge               REAL,
    expected_value                  REAL,
    maximum_acceptable_price        REAL,
    action                          TEXT,             -- BET / WAIT / PASS / DATA_UNAVAILABLE
    action_reason                   TEXT,
    stake_fraction                  REAL,
    home_goalie_status              TEXT,
    away_goalie_status               TEXT,
    generated_at_utc                 TEXT
);

CREATE TABLE IF NOT EXISTS bets (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_id        INTEGER REFERENCES predictions(id),
    game_id             INTEGER,
    market               TEXT,
    selection            TEXT,
    price_taken           REAL,
    stake                 REAL,
    placed_at_utc         TEXT,
    result                TEXT,
    profit                REAL,
    closing_odds_snapshot_id INTEGER   -- filled in after the fact, for CLV only
);
