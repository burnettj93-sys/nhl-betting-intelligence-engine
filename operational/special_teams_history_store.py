"""
Part 4: normalized storage for per-player-game PP/SH/EV/total ice time --
the single, unified store both the BACKFILL (Part 36, reusing the
existing local archival corpus) and the GOING-FORWARD live ingestion
(operational/special_teams_toi_report.py, official NHL TOI reports)
write into, so the live role-feature computation (Part 7) never needs to
know which source populated a given row.

PRIMARY KEY (game_id, player_id) makes re-ingestion of an already-stored
game a real, safe no-op (Part 60: duplicate ingestion must not duplicate
rows) via INSERT OR REPLACE -- a later, corrected re-ingestion of the
SAME game intentionally overwrites the prior row (Part 61's "known-at"
semantics are handled one level up, by the prospective ledger's own
prediction-time SNAPSHOT of whatever these features were AT THAT MOMENT
-- this store itself is a current-best-knowledge table, not an
append-only history of revisions).
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "operational" / "special_teams_history.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS special_teams_history (
    game_id INTEGER NOT NULL,
    player_id TEXT NOT NULL,
    game_date TEXT NOT NULL,
    team TEXT NOT NULL,
    player_name TEXT,
    total_toi_seconds REAL NOT NULL,
    ev_toi_seconds REAL,
    pp_toi_seconds REAL NOT NULL,
    sh_toi_seconds REAL,
    played INTEGER NOT NULL,
    source TEXT NOT NULL,
    observed_at_utc TEXT NOT NULL,
    PRIMARY KEY (game_id, player_id)
);
CREATE INDEX IF NOT EXISTS idx_sth_player_date ON special_teams_history(player_id, game_date);
CREATE INDEX IF NOT EXISTS idx_sth_game ON special_teams_history(game_id);
CREATE INDEX IF NOT EXISTS idx_sth_team_date ON special_teams_history(team, game_date);
"""


def get_connection(path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def upsert_records(conn: sqlite3.Connection, records: list[dict]) -> int:
    """INSERT OR REPLACE on (game_id, player_id) -- idempotent re-
    ingestion by construction (Part 60)."""
    n = 0
    for r in records:
        conn.execute(
            """INSERT OR REPLACE INTO special_teams_history
               (game_id, player_id, game_date, team, player_name, total_toi_seconds,
                ev_toi_seconds, pp_toi_seconds, sh_toi_seconds, played, source, observed_at_utc)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (r["game_id"], r["player_id"], r["game_date"], r["team"], r.get("player_name"),
             r["total_toi_seconds"], r.get("ev_toi_seconds"), r["pp_toi_seconds"], r.get("sh_toi_seconds"),
             1 if r.get("played") else 0, r["source"], r.get("observed_at_utc") or _now_utc()))
        n += 1
    conn.commit()
    return n


def player_history_before(conn: sqlite3.Connection, player_id: str, before_date: str) -> list[dict]:
    """Part 3's strict PIT boundary: game_date < before_date, NEVER <=.
    Ordered chronologically ascending (oldest first), matching the
    research pipeline's own convention."""
    cur = conn.execute(
        """SELECT * FROM special_teams_history
           WHERE player_id = ? AND game_date < ? ORDER BY game_date ASC""",
        (player_id, before_date))
    return [dict(row) for row in cur.fetchall()]


def team_game_totals(conn: sqlite3.Connection, game_id: int, team: str) -> dict:
    """Real team-level PP/SH totals for one game, derived by summing
    this store's own player rows -- never a separate, second source of
    truth for team-level special-teams time."""
    cur = conn.execute(
        "SELECT pp_toi_seconds, sh_toi_seconds FROM special_teams_history WHERE game_id=? AND team=?",
        (game_id, team))
    rows = cur.fetchall()
    return {"n_players": len(rows)}


def coverage_summary(conn: sqlite3.Connection) -> dict:
    cur = conn.execute("SELECT COUNT(*), MIN(game_date), MAX(game_date) FROM special_teams_history")
    n, min_date, max_date = cur.fetchone()
    cur = conn.execute("SELECT source, COUNT(*) FROM special_teams_history GROUP BY source")
    by_source = {row[0]: row[1] for row in cur.fetchall()}
    return {"total_rows": n, "earliest_game_date": min_date, "latest_game_date": max_date,
            "rows_by_source": by_source}
