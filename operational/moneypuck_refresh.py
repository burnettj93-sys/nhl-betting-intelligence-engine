"""
Afternoon and evening MoneyPuck re-check.

The daily sync (`sync_daily.py`, 07:00) downloads MoneyPuck's player and goalie game logs once. The source publishes the previous night's games LATER than 07:00 (observed 2026-10-10: at 07:00 the logs ran
through Oct 8; by 15:30 the source had updated), so every player page, projection and goalie line stayed a day behind for the whole of the day. This job asks the source again at 13:30 and 17:30 ET.
It is the same free download and the same archive-and-promote step as the daily sync (no odds credits), changes nothing when the source has not moved, and the next 15-minute trader cycle rebuilds the
player and goalie data from whatever is newest.

    python3 -m operational.moneypuck_refresh
"""
from __future__ import annotations

import datetime as dt
import sys

DATASETS = ("skater", "goalie", "team")


def season_start_year(today: dt.date | None = None) -> int:
    today = today or dt.datetime.utcnow().date()
    return today.year if today.month >= 7 else today.year - 1


def run(*, sync=None, today: dt.date | None = None, record=None) -> dict:
    """Returns {"status", "season", "datasets": {name: status}, "updated": [names]}. `sync` and `record` are injectable for tests."""
    from operational import moneypuck_daily as mpd
    season = season_start_year(today)
    try:
        res = (sync or mpd.run_moneypuck_sync)(season, DATASETS)
    except Exception as exc:  # noqa: BLE001 - a source that is down is reported, never raised into launchd
        out = {"status": "FAILED", "season": season, "error": f"{exc.__class__.__name__}: {str(exc)[:160]}", "datasets": {}, "updated": []}
    else:
        statuses = {name: (v or {}).get("status", "UNKNOWN") for name, v in (res.get("datasets") or {}).items()}
        out = {"status": "SUCCESS", "season": season, "datasets": statuses, "updated": sorted(n for n, s in statuses.items() if s == "UPDATED")}
    try:
        (record or __import__("operational.ingestion_health", fromlist=["record_run"]).record_run)("moneypuck_afternoon_refresh", out)
    except Exception:  # noqa: BLE001
        pass
    return out


def main() -> int:
    from operational import deployment_mode as dm
    if not dm.require_active_scheduler_or_exit("moneypuck_refresh"):
        return 0
    out = run()
    print(out)
    return 0 if out["status"] == "SUCCESS" else 1


if __name__ == "__main__":
    sys.exit(main())
