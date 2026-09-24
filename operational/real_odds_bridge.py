"""
Real Recommendation Pipeline block (2026-09-24): the missing bridge
between the real DraftKings moneyline data already being collected
(operational/live_odds_daily_pull.py -> operational/moneyline_snapshot_cache.json)
and the EXISTING, complete, already-tested moneyline decision engine
(pricing/engine.py::evaluate_moneyline_for_game(), models/combined_model.py,
run_slate.py) -- which reads its market data from the root schema.sql's
`odds_snapshots` table via features/point_in_time.py, a table that has
never once been populated with a real price until this module exists.

This module writes NO new decision logic and duplicates NO model math --
it only converts an already-real, already-verified DraftKings moneyline
observation into the row shape odds_snapshots already expects, so the
existing engine can run against it completely unmodified.

Idempotent by construction: odds_snapshots' own UNIQUE index
(sportsbook, game_id, market, selection, captured_at_utc) means
re-running this against the same cache file twice never duplicates a
row -- each real snapshot's own real captured_at_utc is the natural
de-duplication key, exactly like every other real snapshot ingestion in
this project.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import db
from ingest.timestamps import normalize_utc_timestamp
from operational.live_odds_daily_pull import MONEYLINE_CACHE_PATH

MONEYLINE_MARKET = "MONEYLINE"
SPORTSBOOK = "DraftKings"
DATA_PROVIDER = "the-odds-api"


def _load_cache(cache_path: Path) -> dict | None:
    try:
        return json.loads(cache_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _find_real_game_id(conn, home_abbrev: str, away_abbrev: str, commence_time_utc: str) -> int | None:
    """Matches a real Odds API event (identified only by team
    abbreviations + a real commence time -- the Odds API's own event_id
    has no relationship to nhl.db's real game_id) to the real nhl.db
    game it refers to. Matches on the exact real team pair within a
    +/-1 day window of the odds-side commence time, since a schedule
    revision can shift a game's exact minute without changing which
    real game it is. Returns None (never guesses) if zero or more than
    one real game matches -- an ambiguous match must never silently
    pick one."""
    try:
        commence = dt.datetime.fromisoformat(commence_time_utc.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    window_start = (commence - dt.timedelta(days=1)).date().isoformat()
    window_end = (commence + dt.timedelta(days=1)).date().isoformat()
    rows = conn.execute(
        "SELECT game_id FROM games WHERE home_team = ? AND away_team = ? "
        "AND game_date BETWEEN ? AND ?",
        (home_abbrev, away_abbrev, window_start, window_end)).fetchall()
    if len(rows) != 1:
        return None
    return rows[0]["game_id"]


def sync_moneyline_odds_to_snapshots(conn=None, cache_path: Path | None = None,
                                      now: dt.datetime | None = None) -> dict:
    """Reads the real moneyline snapshot cache and writes each real
    (home_price, away_price) pair into odds_snapshots as two real rows
    (one per side) -- the exact shape features/point_in_time.py::
    latest_draftkings_two_sided()/closing_draftkings_snapshot() already
    query. Returns a real, structured summary; never raises past the
    caller (one bad/unmatched row must not abort the whole sync)."""
    owns_conn = conn is None
    conn = conn or db.get_conn()
    cache_path = cache_path if cache_path is not None else MONEYLINE_CACHE_PATH
    now = now or dt.datetime.now(dt.timezone.utc)
    summary = {"status": "SUCCESS", "rows_seen": 0, "rows_written": 0, "rows_skipped_unmatched": 0,
               "unmatched": [], "error": None}

    cache = _load_cache(cache_path)
    if cache is None:
        summary["status"] = "SKIPPED"
        summary["error"] = "no real moneyline snapshot cache found yet"
        if owns_conn:
            conn.close()
        return summary

    try:
        # Real Recommendation Pipeline block (2026-09-24): every timestamp
        # this bridge writes MUST go through this codebase's own
        # canonical normalizer (ingest/timestamps.py::normalize_utc_timestamp)
        # -- a real bug caught only by running this against real production
        # data (Part 20): the original code did a bare `.replace("Z", "")`
        # on event_start_utc and left captured_at_utc/received_at_utc
        # untouched, so a real Odds-API "...12:00:52Z" timestamp kept its
        # literal "Z" while every other timestamp in this project
        # (scheduled_start_utc, prediction_time_utc, etc.) is the naive,
        # no-suffix canonical form -- fromisoformat() then parsed the "Z"
        # row as timezone-AWARE and the naive prediction_time_utc as
        # timezone-NAIVE, and subtracting them in
        # features/point_in_time.py::latest_draftkings_snapshot() raised
        # TypeError. This never surfaced in tests because every test
        # fixture already used the naive convention throughout.
        received_at = normalize_utc_timestamp(now.isoformat())
        for row in cache.get("rows", []):
            summary["rows_seen"] += 1
            game_id = _find_real_game_id(conn, row["home_team_abbrev"], row["away_team_abbrev"],
                                          row["commence_time_utc"])
            if game_id is None:
                summary["rows_skipped_unmatched"] += 1
                summary["unmatched"].append({"home": row["home_team_abbrev"], "away": row["away_team_abbrev"],
                                              "commence_time_utc": row["commence_time_utc"]})
                continue

            event_start_utc = normalize_utc_timestamp(row["commence_time_utc"])
            captured_at = normalize_utc_timestamp(row["captured_at_utc"])
            for selection, price in ((row["home_team_abbrev"], row["home_price"]),
                                      (row["away_team_abbrev"], row["away_price"])):
                try:
                    cur = conn.execute(
                        """INSERT OR IGNORE INTO odds_snapshots
                           (game_id, sportsbook, data_provider, market, selection, event_start_utc,
                            line, price_american, status, captured_at_utc, received_at_utc, snapshot_label)
                           VALUES (?, ?, ?, ?, ?, ?, NULL, ?, 'ACTIVE', ?, ?, ?)""",
                        (game_id, SPORTSBOOK, DATA_PROVIDER, MONEYLINE_MARKET, selection,
                         event_start_utc, price, captured_at, received_at, row.get("snapshot_label")))
                    if cur.rowcount > 0:
                        summary["rows_written"] += 1
                except Exception as exc:  # noqa: BLE001 -- one bad row must never abort the sync
                    summary["error"] = f"{exc.__class__.__name__}: {exc}"
        conn.commit()
    finally:
        if owns_conn:
            conn.close()
    return summary
