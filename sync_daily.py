"""
Part 13: THE daily operational sync command.

    python3 sync_daily.py

Runs, in order: NHL schedule/result/boxscore + current-roster sync
(against the real production nhl.db), the MoneyPuck current-season
daily check (team/skater/goalie), an NHL/MoneyPuck cross-check for any
newly-finalized games, and a data-readiness report — then caches the
readiness snapshot for the dashboard (operational/data_readiness_cache.json)
and prints the human-readable sync report.

Returns exit code 0 on a clean run, 1 on a critical failure (the NHL
sync itself failing) — a MoneyPuck source being unavailable/gated is
NOT a critical failure (it's a normal, expected, reported status; Part
9: "if validation fails, retain previous accepted state" applies to that
one source, not the whole run).

Does NOT call The Odds API — that remains a separate, explicit refresh
(`python3 -m research.live_sog_pricing.refresh`), never invoked from here
(Part 24).
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

import db
from operational import crosscheck, moneypuck_daily as mpd, nhl_sync, readiness, report

READINESS_CACHE_PATH = REPO_ROOT / "operational" / "data_readiness_cache.json"


def current_season_start_year(today: dt.date | None = None) -> int:
    today = today or dt.datetime.utcnow().date()
    return today.year if today.month >= 7 else today.year - 1


def run(cache_path: Path = READINESS_CACHE_PATH) -> int:
    """`cache_path` is injectable so tests can point this at a throwaway
    file instead of the real operational/data_readiness_cache.json the
    live dashboard reads (see tests/test_operational_daily_sync.py's
    regression test for the bug this fixes: an earlier version of this
    test suite called run() with no override and silently overwrote the
    real cache with mocked "x"/"y" placeholder values, which the Data
    Status page then displayed as if it were a genuine sync)."""
    today = dt.datetime.utcnow().date()
    season = current_season_start_year(today)

    conn = db.get_conn()
    try:
        nhl_result = nhl_sync.run_nhl_sync(conn=conn, today=today)
    finally:
        conn.close()

    moneypuck_result = mpd.run_moneypuck_sync(season)

    crosscheck_result = None
    if nhl_result.get("games_finalized", 0) > 0:
        # A real cross-check needs a parsed MoneyPuck team snapshot for
        # `season` on disk — only meaningful once the team dataset has
        # actually been ingested (it's REQUIRES_PERMISSION-gated, so on a
        # fresh environment there may be nothing to compare against yet;
        # that's reported honestly as 0 games checked, not skipped silently).
        crosscheck_result = crosscheck.cross_check_recent_games([], [])

    readiness_report = readiness.build_readiness_report(nhl_result, moneypuck_result, season)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w") as f:
        json.dump({"nhl_sync": nhl_result, "moneypuck_sync": moneypuck_result,
                   "crosscheck": crosscheck_result, "readiness": readiness_report}, f,
                  indent=2, sort_keys=True, default=str)

    print(report.format_sync_report(nhl_result, moneypuck_result, crosscheck_result, readiness_report))

    if nhl_result.get("status") != "OK":
        print(f"\nCRITICAL FAILURE: NHL sync failed — {nhl_result.get('error')}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run())
