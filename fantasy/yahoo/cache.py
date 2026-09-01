"""
Server-side cache for Yahoo API responses (Part 20/21) -- a Streamlit
rerun must never trigger a fresh Yahoo API call. SQLite-backed, matching
this project's existing storage convention.

KNOWN LIMITATION (Part 124/125, documented honestly rather than faked):
on Streamlit Community Cloud specifically, the local filesystem is
EPHEMERAL -- it can be wiped on redeploy, app sleep/wake, or container
restart. This cache (and fantasy_store.py's token/snapshot storage)
works correctly for local development and for a single long-lived
session, but is NOT a durable production persistence layer on that
host. See YAHOO_FANTASY_INTEGRATION_GUIDE.md's Deployment section for
what a genuinely persistent backend would require.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB_PATH = REPO_ROOT / "fantasy" / "storage" / "fantasy_cache.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_cache (
    cache_key TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    cached_at_epoch REAL NOT NULL,
    ttl_seconds REAL NOT NULL
);
"""

# Part 131/132: settings change infrequently, roster/matchup more often.
TTL_LEAGUE_SETTINGS_SECONDS = 6 * 3600
TTL_STANDINGS_SECONDS = 30 * 60
TTL_ROSTER_SECONDS = 10 * 60
TTL_MATCHUP_SECONDS = 10 * 60
TTL_AVAILABLE_PLAYERS_SECONDS = 15 * 60


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def get_cached(conn: sqlite3.Connection, cache_key: str) -> dict | None:
    row = conn.execute("SELECT payload_json, cached_at_epoch, ttl_seconds FROM api_cache WHERE cache_key = ?",
                        (cache_key,)).fetchone()
    if row is None:
        return None
    age = time.time() - row["cached_at_epoch"]
    if age > row["ttl_seconds"]:
        return None
    return json.loads(row["payload_json"])


def cache_age_seconds(conn: sqlite3.Connection, cache_key: str) -> float | None:
    row = conn.execute("SELECT cached_at_epoch FROM api_cache WHERE cache_key = ?", (cache_key,)).fetchone()
    if row is None:
        return None
    return time.time() - row["cached_at_epoch"]


def set_cached(conn: sqlite3.Connection, cache_key: str, payload: dict, ttl_seconds: float) -> None:
    conn.execute(
        "INSERT INTO api_cache (cache_key, payload_json, cached_at_epoch, ttl_seconds) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(cache_key) DO UPDATE SET payload_json=excluded.payload_json, "
        "cached_at_epoch=excluded.cached_at_epoch, ttl_seconds=excluded.ttl_seconds",
        (cache_key, json.dumps(payload), time.time(), ttl_seconds),
    )
    conn.commit()


def invalidate(conn: sqlite3.Connection, cache_key: str) -> None:
    conn.execute("DELETE FROM api_cache WHERE cache_key = ?", (cache_key,))
    conn.commit()
