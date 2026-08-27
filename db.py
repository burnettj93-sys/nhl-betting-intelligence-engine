"""SQLite connection + schema bootstrap."""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "nhl.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def get_conn(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path = DB_PATH, wipe: bool = False) -> sqlite3.Connection:
    if wipe and db_path.exists():
        db_path.unlink()
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
