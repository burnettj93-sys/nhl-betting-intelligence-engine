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
from operational import ingestion_health


SCHEDULE_FORWARD_DAYS = 14  # Real Recommendation Pipeline block (2026-09-24):
# a real gap found while building operational.real_odds_bridge -- the
# original +1 day forward window meant nhl.db's schedule NEVER reached
# far enough forward to contain a game DraftKings had already priced
# (confirmed live: the earliest currently-listed DraftKings event is
# ~9 days out, and real odds have been observed up to 9 days out in
# this project's own archive). Without a matching real game_id already
# in nhl.db, real_odds_bridge.sync_moneyline_odds_to_snapshots() can
# never match a single real odds row to anything. The NHL schedule
# endpoint is free (no credit cost, confirmed via this project's own
# live headers) -- widening this costs nothing extra. 14 days gives
# comfortable headroom beyond the furthest real event observed so far.


def default_sync_window(today: dt.date | None = None) -> tuple[dt.date, dt.date]:
    """Part 2's original instruction was "AT MINIMUM yesterday/today/
    tomorrow" -- a floor, not a ceiling. The forward side is widened to
    SCHEDULE_FORWARD_DAYS (see its own comment for the real gap this
    closes); the backward side stays at 1 day, which is all that's
    needed to catch yesterday's newly-final results. `today` is
    injectable for deterministic tests; defaults to the real current
    UTC date."""
    today = today or dt.datetime.utcnow().date()
    return today - dt.timedelta(days=1), today + dt.timedelta(days=SCHEDULE_FORWARD_DAYS)


ORPHAN_BACKFILL_GRACE_HOURS = 6.0  # a game within this many hours of its own
# scheduled start might still genuinely be in progress or awaiting the
# official API to post FINAL -- only re-check games safely past that.
MAX_ORPHAN_DATES_PER_RUN = 10  # bounds one run's duration/API usage during a
# large one-time catch-up; any remaining orphaned dates are picked up by the
# next daily run rather than all at once.


def find_orphaned_unfinalized_game_dates(conn, now: dt.datetime | None = None,
                                          grace_hours: float = ORPHAN_BACKFILL_GRACE_HOURS) -> list[str]:
    """Production Gap Closure sprint (2026-10-01): default_sync_window()'s
    rolling [-1, +SCHEDULE_FORWARD_DAYS] window means a game whose result
    never arrived within that single backward day (an API hiccup, a
    double-OT boxscore delay, this Mac asleep that day) is NEVER queried
    again -- confirmed live: 42 real games stuck at game_state='SCHEDULED'
    with scheduled_start_utc in the past (some over a month old),
    games_finalized=0 on every sync log entry for 7 straight days. This
    finds every such orphan's DISTINCT game_date so backfill_orphaned_games()
    can re-ingest exactly those dates regardless of how far outside the
    rolling window they've fallen."""
    now = now or dt.datetime.now(dt.timezone.utc)
    cutoff = (now - dt.timedelta(hours=grace_hours)).strftime("%Y-%m-%dT%H:%M:%S")
    rows = conn.execute(
        "SELECT DISTINCT game_date FROM games WHERE game_state = 'SCHEDULED' "
        "AND scheduled_start_utc IS NOT NULL AND scheduled_start_utc < ? ORDER BY game_date",
        (cutoff,)).fetchall()
    return [r["game_date"] for r in rows]


def backfill_orphaned_games(conn, session=None, now: dt.datetime | None = None) -> dict:
    """Re-ingests each orphaned date found by find_orphaned_unfinalized_game_dates()
    via the SAME ingest_range() every other sync uses (never a second,
    parallel ingestion path) -- one call per distinct date, since a
    single-day ingest_range() re-fetches that date's real current
    schedule/result/boxscore state and correctly upserts it (idempotent
    either way). Never raises past the caller -- same discipline as
    run_nhl_sync()."""
    dates = find_orphaned_unfinalized_game_dates(conn, now=now)
    summary = {"status": "SUCCESS", "orphaned_dates_found": len(dates),
               "dates_checked": dates[:MAX_ORPHAN_DATES_PER_RUN], "games_finalized": 0, "error": None}
    try:
        for date_str in summary["dates_checked"]:
            d = dt.date.fromisoformat(date_str)
            result = nhl_api.ingest_range(conn, d, d, session=session)
            summary["games_finalized"] += result["games_finalized"]
    except Exception as exc:  # noqa: BLE001 -- a backfill pass must never crash its caller
        summary["status"] = "FAILED"
        summary["error"] = f"{exc.__class__.__name__}: {exc}"
    return summary


def run_nhl_sync(conn=None, today: dt.date | None = None, session=None,
                  sync_current_rosters: bool = True) -> dict:
    """Runs ingest_range() over default_sync_window() against the REAL
    production database (or an injected `conn` for tests), then —
    because a same-day roster move matters for tomorrow's lineup context
    — a current-roster reconciliation pass for every team seen in the
    window. Returns a summary dict consumed by operational/report.py and
    operational/readiness.py. Never raises past the caller for a genuine
    network/API problem — see the try/except below; a real programming
    bug is still allowed to propagate (never silently swallowed).

    Reliability fix (2026-09-24 hardening pass, docs/RELIABILITY_429_FIX.md):
    `status` is now one of three values, not a binary OK/FAIL --
    schedule/boxscore ingestion (the critical path settlement depends on)
    and roster reconciliation (non-critical, best-effort) are tracked
    SEPARATELY in `components`, because a roster-only failure (e.g. a
    429 that survives ingest_current_roster_identities()'s own retries)
    must never be reported the same way as schedule/boxscore data itself
    failing to ingest:
      - "SUCCESS": schedule/boxscore ingested AND roster fully synced
        (or roster sync was skipped/not applicable).
      - "PARTIAL_SUCCESS": schedule/boxscore ingested fine, but roster
        sync degraded (some teams failed) -- downstream code that
        actually needs roster data should check `components["roster"]`
        before trusting it; downstream code that only needs game
        results (e.g. settlement) is unaffected.
      - "FAILED": the critical schedule/boxscore path itself raised --
        this is the only case that should ever be treated as a real
        sync failure by a caller like sync_daily.py."""
    owns_conn = conn is None
    conn = conn or db.get_conn()
    start_date, end_date = default_sync_window(today)
    summary = {
        "window_start": start_date.isoformat(), "window_end": end_date.isoformat(),
        "status": "SUCCESS", "error": None,
        "games_seen": 0, "games_finalized": 0,
        "teams_roster_synced": 0, "players_removed_this_pass": 0,
        "components": {"schedule_boxscore": "SUCCESS", "roster": "SKIPPED"},
        "roster_status": "SKIPPED", "roster_failed_teams": [],
    }
    try:
        result = nhl_api.ingest_range(conn, start_date, end_date, session=session)
        summary["games_seen"] = result["games_seen"]
        summary["games_finalized"] = result["games_finalized"]

        # Production Gap Closure sprint (2026-10-01): catch up any game that
        # fell outside the rolling window above before it was ever finalized
        # (see find_orphaned_unfinalized_game_dates()'s own docstring for the
        # real 42-game backlog this closes). Never blocks/fails the main
        # sync -- a backfill problem is reported in its own component, same
        # pattern as roster sync below.
        orphan_result = backfill_orphaned_games(conn, session=session)
        summary["orphan_backfill"] = orphan_result
        summary["games_finalized"] += orphan_result["games_finalized"]
        summary["components"]["orphan_backfill"] = orphan_result["status"]

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
                summary["roster_status"] = roster_result["status"]
                summary["roster_failed_teams"] = roster_result["failed_teams"]
                summary["components"]["roster"] = roster_result["status"]
            else:
                summary["roster_status"] = "SUCCESS"  # nothing to sync is not a failure
                summary["components"]["roster"] = "SUCCESS"

        # Schedule/boxscore itself never raised -- the ONLY things that can
        # demote the overall status below SUCCESS at this point are roster
        # or orphan-backfill degrading, both non-critical by design (Section F
        # of the Production Readiness Audit: settlement reads game results,
        # never roster; and an orphan-backfill failure leaves prior accepted
        # data untouched -- the main window's own ingest already succeeded).
        if summary["components"]["roster"] in ("PARTIAL_SUCCESS", "FAILED"):
            summary["status"] = "PARTIAL_SUCCESS"
        if summary["components"]["orphan_backfill"] == "FAILED":
            summary["status"] = "PARTIAL_SUCCESS"
    except Exception as exc:  # noqa: BLE001 — deliberately broad: a sync failure must
        # never crash the caller; it must be reported and leave prior data untouched
        # (Part 9: "If validation fails: retain previous accepted state").
        summary["status"] = "FAILED"
        summary["components"]["schedule_boxscore"] = "FAILED"
        summary["error"] = f"{exc.__class__.__name__}: {exc}"
    finally:
        if owns_conn:
            conn.close()
    ingestion_health.record_run("nhl_sync_full", summary)
    if summary["games_finalized"] > 0:
        # Job Sequencing Fix (Production Gap Closure sprint, 2026-09-30): this
        # run just finalized game(s) -- retrigger settlement the SAME run
        # instead of waiting for the once-daily 07:15 clock slot (see
        # operational/settlement_trigger_hook.py).
        from operational import settlement_trigger_hook
        settlement_trigger_hook.trigger_after("nhl_sync_full")
    return summary


def teams_playing_on(conn, date: dt.date) -> list[str]:
    """Real home+away teams for games scheduled on exactly `date`, per
    nhl.db's own current schedule state. Used for the midday refresh's
    scoping; the pregame refresh uses the tighter, puck-drop-relative
    teams_playing_in_window() instead, so a fixed-interval job never
    resweeps the same teams repeatedly all day."""
    rows = conn.execute("SELECT DISTINCT home_team FROM games WHERE game_date = ?", (date.isoformat(),)).fetchall()
    rows += conn.execute("SELECT DISTINCT away_team FROM games WHERE game_date = ?", (date.isoformat(),)).fetchall()
    return sorted({r[0] for r in rows})


PREGAME_ROSTER_WINDOW_HOURS = (3.0, 4.5)  # matches the odds engine's own
# first-sweep window (research/live_odds_daily_pull.py) -- same
# puck-drop-relative discipline, applied here to roster freshness
# instead of prop odds.


def teams_playing_in_window(conn, now: dt.datetime, window_hours: tuple[float, float] = PREGAME_ROSTER_WINDOW_HOURS) -> list[str]:
    """Home+away teams for games whose scheduled_start_utc falls inside
    `window_hours` from `now` -- e.g. (3.0, 4.5) means "starts 3 to 4.5
    hours from now." Intraday Refresh Architecture, Part D: this is what
    lets run_targeted_pregame_refresh() be scheduled on a short,
    frequent interval (like the odds engine's own prop sweeps) WITHOUT
    resweeping the same teams every firing -- most firings will find
    zero games in-window and make zero roster calls at all, per 'do NOT
    create broad high-frequency polling.'"""
    lo, hi = window_hours
    rows = conn.execute(
        "SELECT DISTINCT home_team, away_team, scheduled_start_utc FROM games "
        "WHERE game_state = 'SCHEDULED' AND scheduled_start_utc IS NOT NULL").fetchall()
    teams: set[str] = set()
    for row in rows:
        try:
            start = dt.datetime.fromisoformat(row["scheduled_start_utc"].replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        if start.tzinfo is None:
            # ingest/timestamps.py::normalize_utc_timestamp() canonicalizes
            # every stored timestamp in this project to a naive string that
            # is always implicitly UTC (confirmed live: real rows in nhl.db
            # are stored as e.g. "2026-09-24T23:00:00", no "Z"/offset at
            # all) -- found via this exact TypeError on a real manual dry
            # run before this job was ever scheduled. Never compare it
            # against an aware `now` without attaching UTC first.
            start = start.replace(tzinfo=dt.timezone.utc)
        hours_until = (start - now).total_seconds() / 3600.0
        if lo <= hours_until <= hi:
            teams.add(row["home_team"])
            teams.add(row["away_team"])
    return sorted(teams)


def run_midday_refresh(conn=None, today: dt.date | None = None, session=None) -> dict:
    """Intraday Refresh Architecture (docs/INTRADAY_REFRESH_SCHEDULE.md),
    Part C: a free schedule-only recheck for TODAY only -- catches a
    same-day postponement or start-time change. Deliberately does NOT
    resweep rosters for the whole league a second time: the confirmed
    NHL roster-endpoint rate-limit sensitivity (docs/RELIABILITY_429_FIX.md)
    means a second full sweep would just reproduce the same 429s for no
    real benefit; a targeted, playing-teams-only roster refresh is
    run_targeted_pregame_refresh()'s job instead, closer to game time."""
    owns_conn = conn is None
    conn = conn or db.get_conn()
    today = today or dt.datetime.utcnow().date()
    summary = {
        "component": "nhl_midday_schedule_refresh", "date": today.isoformat(),
        "status": "SUCCESS", "error": None, "games_seen": 0, "games_finalized": 0,
    }
    try:
        result = nhl_api.ingest_range(conn, today, today, session=session)
        summary["games_seen"] = result["games_seen"]
        summary["games_finalized"] = result["games_finalized"]
    except Exception as exc:  # noqa: BLE001
        summary["status"] = "FAILED"
        summary["error"] = f"{exc.__class__.__name__}: {exc}"
    finally:
        if owns_conn:
            conn.close()
    ingestion_health.record_run("nhl_midday_schedule_refresh", summary)
    if summary["games_finalized"] > 0:
        from operational import settlement_trigger_hook
        settlement_trigger_hook.trigger_after("nhl_midday_schedule_refresh")
    return summary


def run_targeted_pregame_refresh(conn=None, today: dt.date | None = None, session=None,
                                  now: dt.datetime | None = None,
                                  window_hours: tuple[float, float] = PREGAME_ROSTER_WINDOW_HOURS) -> dict:
    """Intraday Refresh Architecture, Part D: schedule recheck for today
    (cheap, free -- always run) PLUS a roster refresh scoped to ONLY the
    teams whose game starts within `window_hours` from `now` (via
    teams_playing_in_window()) -- never a second full-league sweep, and
    typically zero teams (zero roster API calls) most times this fires,
    since most 30-minute firings won't land inside any game's pregame
    window. Same component/status-independence discipline as
    run_nhl_sync(): a degraded roster refresh never demotes below
    PARTIAL_SUCCESS, because schedule/boxscore data (what settlement
    actually needs) is unaffected by it."""
    owns_conn = conn is None
    conn = conn or db.get_conn()
    today = today or dt.datetime.utcnow().date()
    now = now or dt.datetime.now(dt.timezone.utc)
    summary = {
        "component": "nhl_pregame_targeted_refresh", "date": today.isoformat(),
        "status": "SUCCESS", "error": None, "games_seen": 0, "games_finalized": 0,
        "teams_targeted": 0, "teams_roster_synced": 0, "players_removed_this_pass": 0,
        "components": {"schedule": "SUCCESS", "roster": "SKIPPED"},
        "roster_status": "SKIPPED", "roster_failed_teams": [],
    }
    try:
        result = nhl_api.ingest_range(conn, today, today, session=session)
        summary["games_seen"] = result["games_seen"]
        summary["games_finalized"] = result["games_finalized"]

        teams = teams_playing_in_window(conn, now, window_hours)
        summary["teams_targeted"] = len(teams)
        if teams:
            roster_result = nhl_api.ingest_current_roster_identities(
                conn, session or __import__("requests").Session(), teams)
            summary["teams_roster_synced"] = roster_result["teams_processed"]
            summary["players_removed_this_pass"] = roster_result["players_removed_this_pass"]
            summary["roster_status"] = roster_result["status"]
            summary["roster_failed_teams"] = roster_result["failed_teams"]
            summary["components"]["roster"] = roster_result["status"]
        else:
            summary["roster_status"] = "SUCCESS"  # no games today -- nothing to target, not a failure
            summary["components"]["roster"] = "SUCCESS"

        if summary["components"]["roster"] in ("PARTIAL_SUCCESS", "FAILED"):
            summary["status"] = "PARTIAL_SUCCESS"
    except Exception as exc:  # noqa: BLE001
        summary["status"] = "FAILED"
        summary["components"]["schedule"] = "FAILED"
        summary["error"] = f"{exc.__class__.__name__}: {exc}"
    finally:
        if owns_conn:
            conn.close()
    ingestion_health.record_run("nhl_pregame_targeted_refresh", summary)
    if summary["games_finalized"] > 0:
        from operational import settlement_trigger_hook
        settlement_trigger_hook.trigger_after("nhl_pregame_targeted_refresh")
    return summary


def _main() -> None:
    import argparse
    import json

    from operational import deployment_mode as dm
    if not dm.require_active_scheduler_or_exit("nhl_sync"):
        return

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("full", "midday", "pregame"), default="full")
    args = parser.parse_args()

    if args.mode == "full":
        result = run_nhl_sync()
    elif args.mode == "midday":
        result = run_midday_refresh()
    else:
        # Live Run Reliability block (2026-09-25): this existing 30-minute job is the lightweight periodic
        # checker. If the Mac was off at the 07:00 slot, recover the morning dependency chain (bounded, locked,
        # idempotent, STANDBY-aware; a no-op in the normal case). See operational/morning_catchup.py.
        catchup = None
        try:
            from operational import morning_catchup
            catchup = morning_catchup.run_catchup()
        except Exception as exc:  # noqa: BLE001 -- must never stop the pregame refresh
            catchup = {"status": "FAILED", "reason": f"{type(exc).__name__}"}
        result = run_targeted_pregame_refresh()
        if catchup and catchup.get("reason") != "NOTHING_DUE":
            result["morning_catchup"] = catchup

    print(json.dumps(result, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    _main()
