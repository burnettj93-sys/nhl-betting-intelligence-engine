"""
Part 2 — NHL morning sync. A thin, deliberately small orchestration
layer around the ALREADY-ACCEPTED, ALREADY-TESTED production ingestion
functions in ingest/nhl_api.py (schedule/result/boxscore via
ingest_range(), current-roster reconciliation via
ingest_current_roster_identities()) — this module adds NO new NHL
parsing/write logic of its own; it only decides WHICH date window and
WHICH teams a daily run should cover, against the REAL production
database (db.py::get_conn(), i.e. nhl.db).

Idempotency, revision-versioning, and point-in-time-safe knowledge-time
stamping are entirely inherited from ingest/nhl_api.py, unchanged (see
that module's own extensive docstrings) — this file does not re-implement
or duplicate any of that.
"""
from __future__ import annotations

import datetime as dt

import db
from ingest import nhl_api


def default_sync_window(today: dt.date | None = None) -> tuple[dt.date, dt.date]:
    """Part 2: "at minimum account for yesterday's completed games,
    today's scheduled games, tomorrow's scheduled games" — NOT a blind
    wide pull. `today` is injectable for deterministic tests; defaults to
    the real current UTC date."""
    today = today or dt.datetime.utcnow().date()
    return today - dt.timedelta(days=1), today + dt.timedelta(days=1)


def run_nhl_sync(conn=None, today: dt.date | None = None, session=None,
                  sync_current_rosters: bool = True) -> dict:
    """Runs ingest_range() over default_sync_window() against the REAL
    production database (or an injected `conn` for tests), then —
    because a same-day roster move matters for tomorrow's lineup context
    — a current-roster reconciliation pass for every team seen in the
    window. Returns a summary dict consumed by operational/report.py and
    operational/readiness.py. Never raises past the caller for a genuine
    network/API problem — see the try/except below; a real programming
    bug is still allowed to propagate (never silently swallowed)."""
    owns_conn = conn is None
    conn = conn or db.get_conn()
    start_date, end_date = default_sync_window(today)
    summary = {
        "window_start": start_date.isoformat(), "window_end": end_date.isoformat(),
        "status": "OK", "error": None,
        "games_seen": 0, "games_finalized": 0,
        "teams_roster_synced": 0, "players_removed_this_pass": 0,
    }
    try:
        result = nhl_api.ingest_range(conn, start_date, end_date, session=session)
        summary["games_seen"] = result["games_seen"]
        summary["games_finalized"] = result["games_finalized"]

        if sync_current_rosters:
            teams = [r["home_team"] for r in conn.execute(
                "SELECT DISTINCT home_team FROM games WHERE game_date BETWEEN ? AND ?",
                (start_date.isoformat(), end_date.isoformat())).fetchall()]
            teams += [r["away_team"] for r in conn.execute(
                "SELECT DISTINCT away_team FROM games WHERE game_date BETWEEN ? AND ?",
                (start_date.isoformat(), end_date.isoformat())).fetchall()]
            teams = sorted(set(teams))
            if teams:
                roster_result = nhl_api.ingest_current_roster_identities(conn, session or __import__("requests").Session(), teams)
                summary["teams_roster_synced"] = roster_result["teams_processed"]
                summary["players_removed_this_pass"] = roster_result["players_removed_this_pass"]
    except Exception as exc:  # noqa: BLE001 — deliberately broad: a sync failure must
        # never crash the caller; it must be reported and leave prior data untouched
        # (Part 9: "If validation fails: retain previous accepted state").
        summary["status"] = "FAIL"
        summary["error"] = f"{exc.__class__.__name__}: {exc}"
    finally:
        if owns_conn:
            conn.close()
    return summary
