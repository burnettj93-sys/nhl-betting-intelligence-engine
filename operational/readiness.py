"""
Part 12: the operational data-readiness model. Deliberately NOT a single
green/red flag — one status per real data source, each with its own
honest state, so "is the engine ready" is always answerable per-source
rather than hidden behind one aggregate boolean.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from operational import moneypuck_daily as mpd

REPO_ROOT = Path(__file__).resolve().parent.parent
ODDS_CACHE_PATH = REPO_ROOT / "research" / "live_sog_board_cache.json"
STARTER_RESULTS_PATH = REPO_ROOT / "research" / "goalie_intelligence_results.json"

MONEYPUCK_STALE_AFTER_HOURS = 36.0
ODDS_STALE_AFTER_HOURS = 24.0


def _hours_since(iso_ts: str, now: dt.datetime) -> float:
    ts = dt.datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=dt.timezone.utc)
    return (now - ts).total_seconds() / 3600.0


def moneypuck_dataset_status(dataset: str, season: int, now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    manifest = mpd.load_manifest(dataset, season)
    if manifest is None:
        return {"status": "UNAVAILABLE", "reason": "no accepted snapshot exists for this season yet"}
    age_hours = _hours_since(manifest["latest_accepted_at_utc"], now)
    status = "CURRENT" if age_hours <= MONEYPUCK_STALE_AFTER_HOURS else "STALE"
    return {"status": status, "last_accepted_at_utc": manifest["latest_accepted_at_utc"],
            "age_hours": round(age_hours, 1)}


def odds_status(now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    if not ODDS_CACHE_PATH.exists():
        return {"status": "NOT_REFRESHED", "reason": "no refresh has ever been run"}
    with open(ODDS_CACHE_PATH) as f:
        cache = json.load(f)
    summary = cache.get("summary", {})
    if summary.get("api_error"):
        return {"status": "UNAVAILABLE", "reason": summary["api_error"]}
    refreshed_at = summary.get("refreshed_at_utc")
    if not refreshed_at:
        return {"status": "NOT_REFRESHED", "reason": "cache present but has no refresh timestamp"}
    age_hours = _hours_since(refreshed_at, now)
    status = "CURRENT" if age_hours <= ODDS_STALE_AFTER_HOURS else "STALE"
    return {"status": status, "last_refreshed_at_utc": refreshed_at, "age_hours": round(age_hours, 1)}


def starter_intelligence_status() -> dict:
    if not STARTER_RESULTS_PATH.exists():
        return {"status": "UNAVAILABLE", "reason": "Stage 1 starter-projection results not found"}
    return {"status": "PROJECTED",
            "reason": "internal historical-rotation projection available; no live lineup "
                      "confirmation source is integrated, so this is never CONFIRMED"}


def build_readiness_report(nhl_sync_result: dict, moneypuck_sync_result: dict | None,
                            season: int, now: dt.datetime | None = None) -> dict:
    """Assembles the full Part 12 readiness snapshot from a completed
    NHL sync result and (optionally) a MoneyPuck sync result for
    `season` — if `moneypuck_sync_result` is None (MoneyPuck wasn't
    synced this run), per-dataset status is still computed from
    whatever manifest already exists on disk."""
    now = now or dt.datetime.now(dt.timezone.utc)

    nhl_schedule_status = "CURRENT" if nhl_sync_result.get("status") == "OK" else "STALE"
    nhl_results_status = "CURRENT" if nhl_sync_result.get("status") == "OK" else "STALE"

    mp_status = {}
    for dataset in ("team", "skater", "goalie"):
        if moneypuck_sync_result and dataset in moneypuck_sync_result.get("datasets", {}):
            live = moneypuck_sync_result["datasets"][dataset]
            if live["status"] in ("REQUIRES_PERMISSION", "SOURCE_CONTRACT_FAILURE") or (
                    live["status"] == "UNAVAILABLE" and mpd.load_manifest(dataset, season) is None):
                mp_status[dataset] = {"status": live["status"], "reason": live.get("reason")}
                continue
        mp_status[dataset] = moneypuck_dataset_status(dataset, season, now)

    return {
        "generated_at_utc": now.isoformat(),
        "nhl_schedule": {"status": nhl_schedule_status,
                          "window": f"{nhl_sync_result.get('window_start')}..{nhl_sync_result.get('window_end')}"},
        "nhl_results": {"status": nhl_results_status,
                         "games_finalized_this_run": nhl_sync_result.get("games_finalized", 0),
                         "current_through_utc": now.isoformat() if nhl_sync_result.get("status") == "OK" else None},
        "moneypuck_team": mp_status["team"],
        "moneypuck_skater": mp_status["skater"],
        "moneypuck_goalie": mp_status["goalie"],
        "odds": odds_status(now),
        "starter_intelligence": starter_intelligence_status(),
    }
