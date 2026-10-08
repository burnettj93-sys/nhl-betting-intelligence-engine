"""
Moves simulated (demo_generator) history out of the production database.

The production nhl.db had simulated seasons (source = 'demo_generator', 2022-2026-DEMO, ~1,060 games, synthetic
players, odds and lineups) sitting beside the real NHL data. Pages used them as default history and the moneyline
model walked them as if they were results. This builds a real-only database and swaps it in; the complete original
is kept as a timestamped backup (the isolated demo archive) and nothing is deleted without that copy.

    python3 -m operational.isolate_demo_history            # dry run: counts only
    python3 -m operational.isolate_demo_history --apply    # backup, rebuild real-only, verify, swap

What is real: games whose source is not demo-like (nhl_api), their stats/odds/events, players with numeric NHL ids and
their membership events. What is demo: everything attached to a demo game, and players with synthetic ids.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import db  # noqa: E402

GAME_TABLES = ("game_result_events", "game_schedule_events", "goalie_game_stats", "goalie_status_events",
               "lineup_snapshots", "odds_snapshots", "player_game_stats", "pp_unit_snapshots", "predictions", "bets",
               "team_game_stats", "roster_status_events")
DEMO_SOURCE_SQL = "(source IS NOT NULL AND (lower(source) LIKE 'demo%' OR lower(source) = 'test'))"


def _columns(conn: sqlite3.Connection, schema: str, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA {schema}.table_info({table})")]


def plan(conn: sqlite3.Connection) -> dict:
    demo_games = conn.execute(f"SELECT COUNT(*) FROM games WHERE {DEMO_SOURCE_SQL}").fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]
    demo_players = conn.execute("SELECT COUNT(*) FROM players WHERE player_id NOT GLOB '[0-9]*'").fetchone()[0]
    per_table = {}
    for t in GAME_TABLES:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone():
            continue
        if "game_id" not in _columns(conn, "main", t):
            continue
        per_table[t] = conn.execute(
            f"SELECT COUNT(*) FROM {t} WHERE game_id IN (SELECT game_id FROM games WHERE {DEMO_SOURCE_SQL})").fetchone()[0]
    return {"games_total": total, "demo_games": demo_games, "real_games": total - demo_games,
            "demo_players": demo_players, "demo_rows_by_table": per_table}


def build_real_only(src_path: Path, dst_path: Path) -> dict:
    """Creates dst_path with the production schema and copies only real rows from src_path."""
    if dst_path.exists():
        dst_path.unlink()
    new = db.init_db(db_path=dst_path, wipe=True)
    new.close()
    conn = sqlite3.connect(dst_path)
    conn.execute(f"ATTACH DATABASE '{src_path}' AS old")
    conn.execute("PRAGMA foreign_keys = OFF")
    copied: dict[str, int] = {}

    def copy(table: str, where: str = "") -> None:
        if not conn.execute("SELECT 1 FROM old.sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            return
        cols = [c for c in _columns(conn, "main", table) if c in _columns(conn, "old", table)]
        col_sql = ", ".join(f'"{c}"' for c in cols)
        cur = conn.execute(f"INSERT OR IGNORE INTO main.{table} ({col_sql}) SELECT {col_sql} FROM old.{table} {where}")
        copied[table] = cur.rowcount

    real_games = f"game_id IN (SELECT game_id FROM old.games WHERE NOT {DEMO_SOURCE_SQL})"
    copy("teams")
    copy("games", f"WHERE NOT {DEMO_SOURCE_SQL}")
    for table in GAME_TABLES:
        if not conn.execute("SELECT 1 FROM old.sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            continue
        cols = _columns(conn, "old", table)
        if "game_id" in cols:
            copy(table, f"WHERE {real_games}")
        elif "player_id" in cols:
            copy(table, "WHERE player_id GLOB '[0-9]*'")
    copy("players", "WHERE player_id GLOB '[0-9]*'")
    copy("team_membership_events", "WHERE player_id GLOB '[0-9]*'")
    conn.commit()
    conn.execute("DETACH DATABASE old")
    conn.close()
    return copied


def verify(src_path: Path, dst_path: Path) -> list[str]:
    problems = []
    new = sqlite3.connect(dst_path)
    old = sqlite3.connect(src_path)
    if new.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        problems.append("integrity_check failed on the rebuilt database")
    if new.execute(f"SELECT COUNT(*) FROM games WHERE {DEMO_SOURCE_SQL}").fetchone()[0]:
        problems.append("demo games remain")
    if new.execute("SELECT COUNT(*) FROM players WHERE player_id NOT GLOB '[0-9]*'").fetchone()[0]:
        problems.append("synthetic players remain")
    real_old = old.execute(f"SELECT COUNT(*) FROM games WHERE NOT {DEMO_SOURCE_SQL}").fetchone()[0]
    if new.execute("SELECT COUNT(*) FROM games").fetchone()[0] != real_old:
        problems.append("real game count differs")
    for t in ("player_game_stats", "goalie_game_stats", "odds_snapshots", "game_result_events"):
        want = old.execute(f"SELECT COUNT(*) FROM {t} WHERE game_id IN (SELECT game_id FROM games WHERE NOT {DEMO_SOURCE_SQL})").fetchone()[0]
        got = new.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        if want != got:
            problems.append(f"{t}: expected {want} real rows, rebuilt has {got}")
    new.close(); old.close()
    return problems


def apply(db_path: Path | None = None, backup_dir: Path | None = None) -> dict:
    src = Path(db_path or db.RUNTIME_DB_PATH)
    backup_dir = Path(backup_dir or REPO / "operational" / "backups")
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = backup_dir / f"nhl_with_demo_history_{stamp}.db"
    conn = sqlite3.connect(src)
    conn.execute("BEGIN EXCLUSIVE")                      # no writer can change the file while it is copied and swapped
    before = plan(conn)
    shutil.copy2(src, backup)
    tmp = src.with_suffix(".real_only.tmp")
    copied = build_real_only(backup, tmp)
    problems = verify(backup, tmp)
    if problems:
        conn.rollback(); conn.close(); tmp.unlink(missing_ok=True)
        return {"status": "ABORTED", "problems": problems, "before": before}
    os.replace(tmp, src)
    conn.rollback(); conn.close()
    return {"status": "ISOLATED", "backup": str(backup), "before": before, "rows_copied": copied}


# ----------------------------------------------------------------- simulated paper bets ----

SIMULATED_TRACKS = ("DEMO_PAPER", "GAME_PARLAY_PAPER")


def plan_paper_bets(ledger_path: Path) -> dict:
    conn = sqlite3.connect(ledger_path)
    try:
        rows = conn.execute("SELECT track, result_status, COUNT(*) FROM paper_bets GROUP BY 1, 2").fetchall()
        return {"by_track_status": [list(r) for r in rows],
                "simulated": conn.execute(f"SELECT COUNT(*) FROM paper_bets WHERE track IN ({','.join('?' * len(SIMULATED_TRACKS))})",
                                           SIMULATED_TRACKS).fetchone()[0]}
    finally:
        conn.close()


def apply_paper_bets(ledger_path: Path | None = None, backup_dir: Path | None = None) -> dict:
    """Moves simulated paper bets (DEMO_PAPER, GAME_PARLAY_PAPER) out of the live account into an archive file. The real
    REAL_MARKET_PAPER tickets, their ids, settlements, alerts and audit rows are not touched; the archive holds the complete
    original ledger, so nothing is lost."""
    from operational import paper_bankroll as pb
    ledger_path = Path(ledger_path or pb.DB_PATH)
    backup_dir = Path(backup_dir or REPO / "operational" / "backups")
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = backup_dir / f"paper_bankroll_with_simulated_bets_{stamp}.db"
    conn = sqlite3.connect(ledger_path)
    conn.execute("BEGIN EXCLUSIVE")
    try:
        before = conn.execute("SELECT COUNT(*) FROM paper_bets").fetchone()[0]
        real_before = conn.execute("SELECT COUNT(*) FROM paper_bets WHERE track = 'REAL_MARKET_PAPER'").fetchone()[0]
        simulated = conn.execute("SELECT COUNT(*) FROM paper_bets WHERE track IN ('DEMO_PAPER', 'GAME_PARLAY_PAPER')").fetchone()[0]
        if not simulated:
            conn.rollback()
            return {"status": "NOTHING_TO_ISOLATE", "real_tickets": real_before}
        shutil.copy2(ledger_path, archive)
        marks = ",".join("?" * len(SIMULATED_TRACKS))
        ids = [r[0] for r in conn.execute(f"SELECT paper_bet_id FROM paper_bets WHERE track IN ({marks})", SIMULATED_TRACKS)]
        id_marks = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM paper_audit_log WHERE paper_bet_id IN ({id_marks})", ids)
        conn.execute(f"DELETE FROM ticket_alerts WHERE paper_bet_id IN ({id_marks})", ids)
        conn.execute(f"DELETE FROM paper_bets WHERE paper_bet_id IN ({id_marks})", ids)
        after_real = conn.execute("SELECT COUNT(*) FROM paper_bets WHERE track = 'REAL_MARKET_PAPER'").fetchone()[0]
        if after_real != real_before or conn.execute("SELECT COUNT(*) FROM paper_bets").fetchone()[0] != before - simulated:
            conn.rollback()
            return {"status": "ABORTED", "problem": "real ticket count changed"}
        conn.commit()
    finally:
        conn.close()
    return {"status": "ISOLATED", "archive": str(archive), "moved": simulated, "real_tickets": real_before}


if __name__ == "__main__":
    if "--paper-bets" in sys.argv:
        from operational import paper_bankroll as _pb
        print(json.dumps(apply_paper_bets() if "--apply" in sys.argv else plan_paper_bets(_pb.DB_PATH), indent=1))
    elif "--apply" in sys.argv:
        print(json.dumps(apply(), indent=1))
    else:
        c = sqlite3.connect(db.RUNTIME_DB_PATH)
        print(json.dumps(plan(c), indent=1))
