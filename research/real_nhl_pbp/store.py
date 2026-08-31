"""
Part 28: one unified research store for the multi-season play-by-play
corpus -- a single SQLite database (`season` is a column, not a separate
database per season) built directly from the archived raw JSON via the
same, unmodified `normalize.py` used everywhere else in this package.

Schema (Part 32 -- indexes limited to what Part 29's query helpers and
Part 31's benchmark queries actually need, not blind over-indexing):

  pbp_games(game_id PK, season, game_type, game_date, away_team_id,
            home_team_id, final_period_type, ot_periods, reg_periods)
    index: season, game_date

  pbp_events(game_id, event_id, event_sequence, event_type, type_code,
             period_number, period_type, seconds_elapsed_in_period,
             seconds_remaining_in_period, regulation_elapsed_seconds,
             team_id, situation_code, zone_code, x_coord, y_coord,
             is_statistical, game_date, season -- PK(game_id, event_id))
    index: event_type, game_date, season, team_id, (event_type, game_date)
    `game_date`/`season` are deliberately denormalized here (not just
    joined from pbp_games) so Part 29's date-filtered queries never need a
    join on the hot path.

  pbp_event_players(game_id, event_id, role, player_id, game_date, season,
                     team_id)
    index: player_id, (player_id, game_date), game_id
    A separate table (not a JSON blob column) specifically so `player_id`
    can be indexed directly, per Part 32's own explicit instruction.

This module only BUILDS and reads the store -- see query.py for the
as-of/temporal research helpers (Part 29) built on top of it.
"""
from __future__ import annotations

import sqlite3

from research.real_nhl_pbp import normalize, raw_archive

DB_PATH = raw_archive.RAW_ROOT.replace("/raw", "") + "/research_pbp.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pbp_games (
    game_id INTEGER PRIMARY KEY,
    season TEXT NOT NULL,
    game_type INTEGER NOT NULL,
    game_date TEXT NOT NULL,
    away_team_id INTEGER NOT NULL,
    home_team_id INTEGER NOT NULL,
    final_period_type TEXT NOT NULL,
    ot_periods INTEGER NOT NULL,
    reg_periods INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_games_season ON pbp_games(season);
CREATE INDEX IF NOT EXISTS idx_games_date ON pbp_games(game_date);

CREATE TABLE IF NOT EXISTS pbp_events (
    game_id INTEGER NOT NULL,
    event_id INTEGER NOT NULL,
    event_sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    type_code INTEGER NOT NULL,
    period_number INTEGER NOT NULL,
    period_type TEXT NOT NULL,
    seconds_elapsed_in_period INTEGER NOT NULL,
    seconds_remaining_in_period INTEGER,
    regulation_elapsed_seconds INTEGER,
    team_id INTEGER,
    situation_code TEXT,
    zone_code TEXT,
    x_coord INTEGER,
    y_coord INTEGER,
    is_statistical INTEGER NOT NULL,
    game_date TEXT NOT NULL,
    season TEXT NOT NULL,
    PRIMARY KEY (game_id, event_id)
);
CREATE INDEX IF NOT EXISTS idx_events_type ON pbp_events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_date ON pbp_events(game_date);
CREATE INDEX IF NOT EXISTS idx_events_season ON pbp_events(season);
CREATE INDEX IF NOT EXISTS idx_events_team ON pbp_events(team_id);
CREATE INDEX IF NOT EXISTS idx_events_type_date ON pbp_events(event_type, game_date);

CREATE TABLE IF NOT EXISTS pbp_event_players (
    game_id INTEGER NOT NULL,
    event_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    player_id INTEGER NOT NULL,
    game_date TEXT NOT NULL,
    season TEXT NOT NULL,
    team_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_ep_player ON pbp_event_players(player_id);
CREATE INDEX IF NOT EXISTS idx_ep_player_date ON pbp_event_players(player_id, game_date);
CREATE INDEX IF NOT EXISTS idx_ep_game ON pbp_event_players(game_id);
"""


def get_connection(path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def build_store(seasons: tuple[str, ...], path: str = DB_PATH) -> dict:
    """(Re)builds the research store from the archived raw corpus for the
    given seasons. Idempotent: clears and reinserts each named season's
    rows only -- other seasons already in the store are untouched."""
    conn = get_connection(path)
    games_inserted = 0
    events_inserted = 0
    players_inserted = 0
    try:
        with conn:
            for season in seasons:
                conn.execute("DELETE FROM pbp_event_players WHERE season = ?", (season,))
                conn.execute("DELETE FROM pbp_events WHERE season = ?", (season,))
                conn.execute("DELETE FROM pbp_games WHERE season = ?", (season,))

                for game_id in raw_archive.archived_game_ids(season):
                    raw = raw_archive.load_raw_pbp(season, game_id)
                    game = normalize.normalize_game(raw, raw_sha256="", source_url="", retrieved_at_utc="")
                    events = normalize.normalize_game_events(raw)

                    conn.execute(
                        "INSERT INTO pbp_games VALUES (?,?,?,?,?,?,?,?,?)",
                        (game.game_id, game.season, game.game_type, game.game_date,
                         game.away_team_id, game.home_team_id, game.final_period_type,
                         game.ot_periods, game.reg_periods),
                    )
                    games_inserted += 1

                    event_rows = []
                    player_rows = []
                    for e in events:
                        event_rows.append((
                            e.game_id, e.event_id, e.event_sequence, e.event_type, e.type_code,
                            e.period_number, e.period_type, e.seconds_elapsed_in_period,
                            e.seconds_remaining_in_period, e.regulation_elapsed_seconds,
                            e.team_id, e.situation_code, e.zone_code, e.x_coord, e.y_coord,
                            int(e.is_statistical), game.game_date, season,
                        ))
                        for role, pid in e.players.items():
                            player_rows.append((e.game_id, e.event_id, role, pid,
                                                 game.game_date, season, e.team_id))
                    conn.executemany(
                        "INSERT INTO pbp_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        event_rows,
                    )
                    conn.executemany(
                        "INSERT INTO pbp_event_players VALUES (?,?,?,?,?,?,?)",
                        player_rows,
                    )
                    events_inserted += len(event_rows)
                    players_inserted += len(player_rows)
    finally:
        conn.close()
    return {
        "games_inserted": games_inserted,
        "events_inserted": events_inserted,
        "player_rows_inserted": players_inserted,
    }
