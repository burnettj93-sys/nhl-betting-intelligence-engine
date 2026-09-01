"""
Accessor layer for fantasy/storage/fantasy_schema.sql. Every function
requires an explicit user_key -- there is no "current user" global
state anywhere in this module (Part 127 privacy isolation, enforced and
tested in tests/test_fantasy_privacy.py).

KNOWN LIMITATION (Part 124/125): the default DB path is a local SQLite
file. On Streamlit Community Cloud this filesystem is ephemeral -- see
fantasy/yahoo/cache.py's module docstring and
YAHOO_FANTASY_INTEGRATION_GUIDE.md for the honest deployment note.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA_PATH = Path(__file__).resolve().parent / "fantasy_schema.sql"
DEFAULT_DB_PATH = REPO_ROOT / "fantasy" / "storage" / "fantasy_store.db"


def _now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    return conn


# ---------------------------------------------------------------------
# Token storage (TokenStore interface expected by fantasy.yahoo.client)
# ---------------------------------------------------------------------

def save_token(conn: sqlite3.Connection, user_key: str, access_token: str, refresh_token: str,
               expires_at_epoch: float) -> None:
    conn.execute(
        "INSERT INTO yahoo_tokens (user_key, access_token, refresh_token, expires_at_epoch, updated_at_utc) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT(user_key) DO UPDATE SET "
        "access_token=excluded.access_token, refresh_token=excluded.refresh_token, "
        "expires_at_epoch=excluded.expires_at_epoch, updated_at_utc=excluded.updated_at_utc",
        (user_key, access_token, refresh_token, expires_at_epoch, _now_utc()),
    )
    conn.commit()


def load_token_row(conn: sqlite3.Connection, user_key: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM yahoo_tokens WHERE user_key = ?", (user_key,)).fetchone()


def disconnect(conn: sqlite3.Connection, user_key: str) -> None:
    """Part 119: DISCONNECT YAHOO -- removes stored credentials only.
    Never deletes fantasy_recommendations/snapshots history (explicit
    rule: 'Do not delete historical fantasy recommendation data unless
    explicitly requested')."""
    conn.execute("DELETE FROM yahoo_tokens WHERE user_key = ?", (user_key,))
    conn.execute("DELETE FROM user_selection WHERE user_key = ?", (user_key,))
    conn.commit()


# ---------------------------------------------------------------------
# League/team selection (Part 9/10)
# ---------------------------------------------------------------------

def save_selection(conn: sqlite3.Connection, user_key: str, game_key: str | None,
                    league_key: str | None, team_key: str | None) -> None:
    conn.execute(
        "INSERT INTO user_selection (user_key, game_key, league_key, team_key, updated_at_utc) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT(user_key) DO UPDATE SET "
        "game_key=excluded.game_key, league_key=excluded.league_key, team_key=excluded.team_key, "
        "updated_at_utc=excluded.updated_at_utc",
        (user_key, game_key, league_key, team_key, _now_utc()),
    )
    conn.commit()


def load_selection(conn: sqlite3.Connection, user_key: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM user_selection WHERE user_key = ?", (user_key,)).fetchone()


# ---------------------------------------------------------------------
# Append-only snapshots (Part 18/19)
# ---------------------------------------------------------------------

def record_league_settings_snapshot(conn: sqlite3.Connection, user_key: str, league_key: str,
                                     settings_json: dict, settings_hash: str) -> None:
    conn.execute(
        "INSERT INTO league_settings_snapshots (user_key, league_key, settings_json, settings_hash, observed_at_utc) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_key, league_key, json.dumps(settings_json), settings_hash, _now_utc()),
    )
    conn.commit()


def latest_league_settings_snapshot(conn: sqlite3.Connection, user_key: str, league_key: str) -> dict | None:
    row = conn.execute(
        "SELECT settings_json, settings_hash, observed_at_utc FROM league_settings_snapshots "
        "WHERE user_key = ? AND league_key = ? ORDER BY observed_at_utc DESC LIMIT 1",
        (user_key, league_key),
    ).fetchone()
    if row is None:
        return None
    return {"settings": json.loads(row["settings_json"]), "settings_hash": row["settings_hash"],
            "observed_at_utc": row["observed_at_utc"]}


def record_roster_snapshot(conn: sqlite3.Connection, user_key: str, league_key: str, team_key: str,
                            roster_json: list) -> None:
    conn.execute(
        "INSERT INTO roster_snapshots (user_key, league_key, team_key, roster_json, observed_at_utc) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_key, league_key, team_key, json.dumps(roster_json), _now_utc()),
    )
    conn.commit()


def latest_roster_snapshot(conn: sqlite3.Connection, user_key: str, team_key: str) -> dict | None:
    row = conn.execute(
        "SELECT roster_json, observed_at_utc FROM roster_snapshots "
        "WHERE user_key = ? AND team_key = ? ORDER BY observed_at_utc DESC LIMIT 1",
        (user_key, team_key),
    ).fetchone()
    if row is None:
        return None
    return {"roster": json.loads(row["roster_json"]), "observed_at_utc": row["observed_at_utc"]}


def record_standings_snapshot(conn: sqlite3.Connection, user_key: str, league_key: str, standings_json: list) -> None:
    conn.execute(
        "INSERT INTO standings_snapshots (user_key, league_key, standings_json, observed_at_utc) VALUES (?, ?, ?, ?)",
        (user_key, league_key, json.dumps(standings_json), _now_utc()),
    )
    conn.commit()


def latest_standings_snapshot(conn: sqlite3.Connection, user_key: str, league_key: str) -> dict | None:
    row = conn.execute(
        "SELECT standings_json, observed_at_utc FROM standings_snapshots "
        "WHERE user_key = ? AND league_key = ? ORDER BY observed_at_utc DESC LIMIT 1",
        (user_key, league_key),
    ).fetchone()
    if row is None:
        return None
    return {"standings": json.loads(row["standings_json"]), "observed_at_utc": row["observed_at_utc"]}


# ---------------------------------------------------------------------
# Recommendation ledger (Part 81/82/83/88)
# ---------------------------------------------------------------------

def record_recommendation(conn: sqlite3.Connection, *, user_key: str, league_key: str, team_key: str,
                           player_id: str | None, recommendation_type: str, reason: str,
                           projection: dict | None, confidence: str | None) -> int:
    cur = conn.execute(
        "INSERT INTO fantasy_recommendations "
        "(user_key, league_key, team_key, player_id, recommendation_type, reason, projection_json, "
        "confidence, executed, observed_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
        (user_key, league_key, team_key, player_id, recommendation_type, reason,
         json.dumps(projection) if projection is not None else None, confidence, _now_utc()),
    )
    conn.commit()
    return cur.lastrowid


def recommendations_for_team(conn: sqlite3.Connection, user_key: str, team_key: str,
                              recommendation_type: str | None = None) -> list[sqlite3.Row]:
    if recommendation_type:
        return conn.execute(
            "SELECT * FROM fantasy_recommendations WHERE user_key = ? AND team_key = ? "
            "AND recommendation_type = ? ORDER BY observed_at_utc DESC",
            (user_key, team_key, recommendation_type),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM fantasy_recommendations WHERE user_key = ? AND team_key = ? ORDER BY observed_at_utc DESC",
        (user_key, team_key),
    ).fetchall()


# ---------------------------------------------------------------------
# Watchlist (Part 77)
# ---------------------------------------------------------------------

def add_to_watchlist(conn: sqlite3.Connection, user_key: str, player_id: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO watchlist (user_key, player_id, added_at_utc) VALUES (?, ?, ?)",
        (user_key, player_id, _now_utc()),
    )
    conn.commit()


def remove_from_watchlist(conn: sqlite3.Connection, user_key: str, player_id: str) -> None:
    conn.execute("DELETE FROM watchlist WHERE user_key = ? AND player_id = ?", (user_key, player_id))
    conn.commit()


def load_watchlist(conn: sqlite3.Connection, user_key: str) -> list[str]:
    rows = conn.execute("SELECT player_id FROM watchlist WHERE user_key = ? ORDER BY added_at_utc DESC",
                         (user_key,)).fetchall()
    return [r["player_id"] for r in rows]
