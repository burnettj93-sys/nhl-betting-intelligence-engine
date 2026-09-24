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
# Token storage -- REPLACED (2026-09-24 Yahoo compliance rebuild).
# ---------------------------------------------------------------------
# These used to write access_token/refresh_token as PLAIN TEXT columns
# to yahoo_tokens -- not encrypted, and (confirmed via a real audit
# before any change) never actually reached by any working flow: the
# old dashboard page built an authorization URL but had no callback
# handler that ever called save_token(). Tokens now belong in
# fantasy/yahoo/token_store.py::EncryptedFileTokenStore (AES-256-GCM,
# adapted from ~/yahoo-fantasy-cockpit's proven implementation). These
# two now raise rather than silently writing plaintext secrets again.

class _PlaintextTokenStorageRemoved(RuntimeError):
    pass


def save_token(*_args, **_kwargs):
    raise _PlaintextTokenStorageRemoved(
        "fantasy_store.save_token() stored tokens as plain text and is no longer used -- "
        "use fantasy.yahoo.token_store.EncryptedFileTokenStore instead.")


def load_token_row(*_args, **_kwargs):
    raise _PlaintextTokenStorageRemoved(
        "fantasy_store.load_token_row() read plaintext tokens and is no longer used -- "
        "use fantasy.yahoo.token_store.EncryptedFileTokenStore instead.")


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
# Append-only snapshots -- REMOVED (2026-09-24 Yahoo compliance rebuild).
# ---------------------------------------------------------------------
# These persisted actual Yahoo Fantasy Information (league settings,
# roster contents, standings) to disk -- squarely what the signed API
# agreement's Section 2.c.vii prohibits ("shall not store, cache or
# index the Yahoo Fantasy Information"). Confirmed via a real audit
# before any change: every one of these tables held zero rows, so
# nothing is being deleted here, only the ability to write more. The
# compliant replacement (Phase 9/10) is transient: fetch from Yahoo,
# compute, display, discard -- see fantasy/yahoo/diagnostic.py for the
# pattern. Do not resurrect a snapshot table for Yahoo-sourced data.

class _FantasyInformationPersistenceRemoved(RuntimeError):
    pass


def _snapshot_removed(*_args, **_kwargs):
    raise _FantasyInformationPersistenceRemoved(
        "Persisting Yahoo Fantasy Information (league settings/roster/standings snapshots) is "
        "prohibited by the signed API Access and Use Agreement (Section 2.c.vii). Fetch fresh from "
        "Yahoo for each use instead -- see fantasy/yahoo/diagnostic.py.")


record_league_settings_snapshot = _snapshot_removed
latest_league_settings_snapshot = _snapshot_removed
record_roster_snapshot = _snapshot_removed
latest_roster_snapshot = _snapshot_removed
record_standings_snapshot = _snapshot_removed
latest_standings_snapshot = _snapshot_removed


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
