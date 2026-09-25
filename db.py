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
    return conn


def init_db(db_path: Path | None = None, wipe: bool = False) -> sqlite3.Connection:
    db_path = db_path if db_path is not None else DB_PATH
    if wipe and db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = get_conn(db_path)
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.commit()
    return conn


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
