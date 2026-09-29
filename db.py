"""SQLite connection + schema bootstrap."""
import os
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).parent
SCHEMA_PATH = REPO_ROOT / "schema.sql"

# Runtime DB hygiene (2026-09-25): the live, scheduler-mutated NHL database
# must not be a git-tracked file -- every sync used to dirty `git status`
# and produce "routine capture" commits of a 14MB binary. Resolution order,
# in ONE place (every consumer goes through this module):
#   1. NHL_DB_PATH (environment, or a NHL_DB_PATH=... line in .env) -- the
#      VPS sets this to /opt/nhl-engine/data/nhl.db (docs/VPS_PRODUCTION_LAYOUT.md).
#   2. operational/runtime/nhl.db, if it exists -- gitignored, the local
#      default once migrated (docs/RUNTIME_DB_HYGIENE.md).
#   3. LEGACY_DB_PATH (the tracked nhl.db at the repo root) -- a frozen
#      snapshot that keeps a fresh clone / Streamlit Community Cloud
#      (which only has tracked files) working exactly as before.
ENV_VAR = "NHL_DB_PATH"
LEGACY_DB_PATH = REPO_ROOT / "nhl.db"
RUNTIME_DB_PATH = REPO_ROOT / "operational" / "runtime" / "nhl.db"


def _env_value() -> str | None:
    value = (os.environ.get(ENV_VAR) or "").strip()
    if value:
        return value
    env_file = REPO_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            key, sep, val = line.strip().partition("=")
            if sep and key.strip() == ENV_VAR and val.strip():
                return val.strip()
    return None


def resolve_db_path() -> Path:
    configured = _env_value()
    if configured:
        return Path(configured).expanduser()
    if RUNTIME_DB_PATH.exists():
        return RUNTIME_DB_PATH
    return LEGACY_DB_PATH


DB_PATH = resolve_db_path()


def get_conn(db_path: Path | None = None) -> sqlite3.Connection:
    # Looked up at call time (never bound as a default) so tests that patch
    # db.DB_PATH actually take effect.
    conn = sqlite3.connect(db_path if db_path is not None else DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # Hits/Blocked-Shots Settlement Enablement block (2026-09-29): production
    # ingestion (sync_daily.py, operational/nhl_sync.py, ...) opens the real
    # runtime DB via get_conn() directly, never init_db() -- so the additive
    # column migration must run HERE to actually reach the live database, not
    # only the fresh-DB path. A single `PRAGMA table_info` read per connection
    # open is microseconds; the guard inside skips entirely once the column
    # already exists (the overwhelming majority of calls, forever after the
    # first). Never touches moneyline/odds/T-35 tables -- see
    # tests/test_schema_migration.py.
    _migrate_additive_columns(conn)
    return conn


def init_db(db_path: Path | None = None, wipe: bool = False) -> sqlite3.Connection:
    db_path = db_path if db_path is not None else DB_PATH
    if wipe and db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = get_conn(db_path)
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    _migrate_additive_columns(conn)
    conn.commit()
    return conn


# Hits/Blocked-Shots Settlement Enablement block (2026-09-29): schema.sql's
# `executescript()` above is a CREATE-TABLE-IF-NOT-EXISTS pass -- it does
# nothing for a table that already exists on an existing runtime DB, so a
# newly-added COLUMN needs its own idempotent, additive-only migration step.
# No destructive rebuild, no data loss: ADD COLUMN only, guarded so re-running
# it (every init_db() call, including every ordinary process start) is a
# harmless no-op once the column exists. See tests/test_schema_migration.py.
_ADDITIVE_COLUMN_MIGRATIONS = (
    ("player_game_stats", "hits", "INTEGER"),
    ("player_game_stats", "blocked_shots", "INTEGER"),
)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


_TEAM_GAME_STATS_DDL = """
CREATE TABLE IF NOT EXISTS team_game_stats (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id           INTEGER,
    team_id           TEXT,
    sog               INTEGER,
    revision_number   INTEGER DEFAULT 1,
    effective_at_utc  TEXT,
    observed_at_utc   TEXT,
    source            TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_team_game_stat_revision
    ON team_game_stats (game_id, team_id, revision_number);
"""


def _migrate_additive_columns(conn: sqlite3.Connection) -> None:
    for table, column, coltype in _ADDITIVE_COLUMN_MIGRATIONS:
        if not _table_exists(conn, table):
            continue   # a brand-new DB: schema.sql's CREATE TABLE (run via init_db) already has the column
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
            conn.commit()
    # New table (not a column add): CREATE TABLE IF NOT EXISTS is already idempotent/safe on every call,
    # including a brand-new DB (harmless duplicate of what init_db()'s schema.sql script also creates).
    conn.executescript(_TEAM_GAME_STATS_DDL)
    conn.commit()


def team_ids(conn: sqlite3.Connection) -> list[str]:
    """v2.1.2 spec item 2: THE authoritative production team universe --
    derived from the normalized `teams` table, never from
    ingest.demo_data.TEAMS (the synthetic 12-team demo league only).
    run_slate.py, backtest.py, and any other production model-state
    reconstruction must call this rather than assuming any fixed team
    list, so the engine works correctly against a real NHL database
    containing teams the demo world never had (e.g. EDM, VGK, COL) --
    see tests/test_dynamic_team_universe.py."""
    rows = conn.execute("SELECT team_id FROM teams ORDER BY team_id").fetchall()
    return [r["team_id"] for r in rows]
