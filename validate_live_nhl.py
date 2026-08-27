"""
LIVE NHL CORE INGESTION SMOKE TEST (v2.1.2 spec item 7/8; strengthened by
v2.1.2a spec items 2/6/7/8).

    python3 validate_live_nhl.py                          # backwards-search default
    python3 validate_live_nhl.py --start 2026-01-05 --end 2026-01-12
    python3 validate_live_nhl.py 10                        # deprecated: fixed 10-day window_days

This is SEPARATE from `validate.py` and must NEVER be silently combined
with synthetic validation. `validate.py` proves the pipeline's temporal-
integrity architecture and math are sound against
`ingest/demo_data.py`'s SYNTHETIC league; it can never prove real NHL API
behavior, because it never talks to the network. This script is the
other half: it hits the REAL NHL API (api-web.nhle.com) and proves the
core ingestion path actually works against real, live responses.

Uses a FRESH TEMPORARY sqlite database for every run -- never `nhl.db`,
never any persistent file -- so it is always safe to run repeatedly.

What it does, in order:
  1. Selects a date range. By default (v2.1.2a spec item 7) it searches
     BACKWARDS from today in small (default 7-day) windows until a window
     contains at least 3 finalized games, up to a configurable maximum
     lookback (default 150 days) -- so it stays correct during the NHL
     offseason (e.g. an August run) instead of the old fixed "today minus
     7 days" window, which can legitimately return zero completed games
     for months at a time. `--start YYYY-MM-DD --end YYYY-MM-DD` pins an
     exact range instead, for a human-chosen smoke-test window. The exact
     selected range is always reported.
  2. Ingests schedule -> result -> boxscore for each finalized game,
     through the exact same production functions ingest_range() uses,
     each with its OWN freshly-captured observed_at_utc (v2.1.2a spec
     item 5 -- see ingest/nhl_api.py).
  3. Checks structural sanity: teams auto-bootstrapped (spec item 1, no
     manual pre-seeding), zero `PRAGMA foreign_key_check` violations,
     every game has its required fields, every FINAL game has a complete
     game_result_events row, every finalized game's boxscore produced
     non-empty player_game_stats AND goalie_game_stats rows for BOTH
     teams (a structurally-changed or empty boxscore response is a FAIL,
     not a quiet pass), every persisted timestamp is already in canonical
     form (normalize_utc_timestamp(v) == v), non-empty player/team IDs,
     and -- where the boxscore response reports a team-level SOG total --
     that total is cross-checked against the stored per-skater shots sum.
  4. Reruns the EXACT SAME range a second time, INCLUDING re-fetching and
     re-processing boxscores this time (v2.1.2a spec item 2 -- the old
     rerun only replayed schedule/result and could never prove player/
     goalie stat idempotency against real data), and confirms idempotency
     (no duplicate schedule/result/stat revisions).
  5. Runs a CURRENT-roster identity/membership ingestion pass (v2.1.2a
     spec item 3/8, via ingest_current_roster_identities() /
     /v1/roster/{team}/current -- NOT the season-scoped endpoint) for
     every team encountered in the range, then reruns it a second time
     against an unchanged snapshot and confirms zero membership churn.
     This does NOT validate injury/availability status or starting-
     goalie announcements -- see README's three-tier ingestion-validation
     distinction.

Deliberately does NOT import ingest.demo_data.TEAMS or assume any fixed
team list anywhere (spec item 8) -- the team universe here is whatever
the live schedule pull actually returns.

FAILS LOUDLY: a live response that doesn't match the shape
ingest/nhl_api.py expects is caught and reported with the endpoint, the
game/team identifier, and the underlying error -- never silently
defaulted, skipped, or made to "pass" on partial/wrong data.

Error classification (v2.1.2a spec item 7): the outermost handler below
(`if __name__ == "__main__":`) only prints the "NOT EXECUTED -- NETWORK
UNAVAILABLE" sentinel for an exception that plainly looks like a DNS/
proxy/connection/timeout failure (see _looks_like_network_unavailable()).
Anything else escaping run() -- a programming bug, a sqlite error, an
assertion failure, an unexpected API structure not already caught inside
run()'s own per-step try/excepts -- is reported as "LIVE NHL CORE
INGESTION: FAIL" with the real exception and a traceback. A software
defect must never be allowed to hide behind the network-unavailable
label.
"""
from __future__ import annotations

import datetime as dt
import sys
import tempfile
from pathlib import Path

import db
from ingest import nhl_api
from ingest.timestamps import normalize_utc_timestamp


def _fresh_temp_db():
    fd_path = Path(tempfile.mkstemp(suffix=".db")[1])
    fd_path.unlink()   # db.init_db creates it fresh
    conn = db.init_db(db_path=fd_path, wipe=False)
    return conn, fd_path


def _looks_like_network_unavailable(exc: Exception) -> bool:
    """Best-effort classification so the report can say the exact
    'NOT EXECUTED -- NETWORK UNAVAILABLE' sentinel only when the failure
    is plainly an environment-level connectivity problem (proxy/DNS/
    connection-refused/timeout), rather than a genuine live schema
    mismatch, programming bug, or other software defect that should be
    investigated and reported as a real FAIL (v2.1.2a spec item 7)."""
    text = repr(exc).lower()
    return any(s in text for s in (
        "proxyerror", "connectionerror", "maxretryerror", "nameresolution",
        "timeout", "temporary failure in name resolution", "connection refused",
    ))


def _counts(conn) -> dict:
    tables = ("games", "game_schedule_events", "game_result_events",
              "player_game_stats", "goalie_game_stats", "team_membership_events")
    return {name: conn.execute(f"SELECT COUNT(*) c FROM {name}").fetchone()["c"]
            for name in tables}


def _stat_row_count(conn, game_id) -> int:
    p = conn.execute(
        "SELECT COUNT(*) c FROM player_game_stats WHERE game_id=?", (game_id,)).fetchone()["c"]
    g = conn.execute(
        "SELECT COUNT(*) c FROM goalie_game_stats WHERE game_id=?", (game_id,)).fetchone()["c"]
    return p + g


def _fk_violations(conn) -> list:
    return conn.execute("PRAGMA foreign_key_check").fetchall()


def _canonical_timestamp_violations(conn) -> list[str]:
    """v2.1.2a spec item 6: every relevant persisted non-null timestamp
    must already be in canonical form -- normalize_utc_timestamp(v) == v.
    A value that changes under normalization means something upstream
    wrote a non-canonical representation directly, which would silently
    break the lexicographic/ISO-parse comparisons in
    features/point_in_time.py."""
    checks = [
        ("games", "schedule_observed_at_utc"),
        ("games", "result_observed_at_utc"),
        ("games", "scheduled_start_utc"),
        ("game_schedule_events", "observed_at_utc"),
        ("game_schedule_events", "effective_at_utc"),
        ("game_schedule_events", "scheduled_start_utc"),
        ("game_result_events", "observed_at_utc"),
        ("game_result_events", "effective_at_utc"),
        ("player_game_stats", "observed_at_utc"),
        ("player_game_stats", "effective_at_utc"),
        ("goalie_game_stats", "observed_at_utc"),
        ("goalie_game_stats", "effective_at_utc"),
        ("team_membership_events", "observed_at_utc"),
        ("team_membership_events", "effective_at_utc"),
    ]
    violations: list[str] = []
    for table, col in checks:
        rows = conn.execute(f"SELECT {col} AS v FROM {table} WHERE {col} IS NOT NULL").fetchall()
        for row in rows:
            v = row["v"]
            try:
                if normalize_utc_timestamp(v) != v:
                    violations.append(f"{table}.{col}={v!r} is not already canonical")
            except Exception as e:
                violations.append(f"{table}.{col}={v!r} failed to normalize: {e!r}")
    return violations


def _select_date_range(session, explicit_start: dt.date | None = None,
                        explicit_end: dt.date | None = None, window_days: int = 7,
                        min_finals: int = 3, max_lookback_days: int = 150):
    """v2.1.2a spec item 7. If both explicit_start/explicit_end are given,
    use them exactly, no search -- a human-pinned smoke-test window.
    Otherwise search BACKWARDS from today in `window_days`-sized windows
    until one window contains >= `min_finals` finalized games, capped at
    `max_lookback_days` total lookback so this can never end up silently
    ingesting months of games just to find one (e.g. deep into an
    offseason). Returns (start, end, games, note)."""
    if explicit_start is not None or explicit_end is not None:
        if explicit_start is None or explicit_end is None:
            raise ValueError("--start and --end must both be given together")
        games = nhl_api.fetch_schedule_range(session, explicit_start, explicit_end)
        return explicit_start, explicit_end, games, "explicit --start/--end (no backwards search)"

    end = dt.date.today()
    start = end - dt.timedelta(days=window_days)
    games: list = []
    lookback = 0
    while True:
        start = end - dt.timedelta(days=window_days)
        games = nhl_api.fetch_schedule_range(session, start, end)
        finals = [g for g in games if g.get("gameState") in ("OFF", "FINAL")]
        if len(finals) >= min_finals:
            return start, end, games, (
                f"backwards search: window {start.isoformat()}..{end.isoformat()} has "
                f"{len(finals)} finalized game(s) (>= required {min_finals})")
        lookback += window_days
        if lookback >= max_lookback_days:
            return start, end, games, (
                f"exhausted {max_lookback_days}-day max lookback without any "
                f"{window_days}-day window reaching {min_finals} finalized games; "
                f"using the oldest window searched, {start.isoformat()}..{end.isoformat()} "
                f"({len(finals)} finalized game(s) found there)")
        end = start


def run(start_date: dt.date | None = None, end_date: dt.date | None = None,
        window_days: int = 7, min_finals: int = 3, max_lookback_days: int = 150) -> dict:
    import requests

    conn, path = _fresh_temp_db()
    report: dict = {"ok": True, "errors": [], "steps": {}}
    try:
        session = requests.Session()

        # --- range selection (spec item 7) ---
        try:
            start, end, games, selection_note = _select_date_range(
                session, explicit_start=start_date, explicit_end=end_date,
                window_days=window_days, min_finals=min_finals,
                max_lookback_days=max_lookback_days)
        except Exception as e:
            report["ok"] = False
            report["network_unavailable"] = _looks_like_network_unavailable(e)
            report["errors"].append(
                f"endpoint=schedule/{{date}}; identifier=range selection; error={e!r}")
            return report

        report["steps"]["selected_range"] = f"{start.isoformat()} .. {end.isoformat()}"
        report["steps"]["range_selection"] = selection_note
        report["steps"]["games_seen"] = len(games)
        if not games:
            report["ok"] = False
            report["errors"].append(
                f"no games returned for {start}..{end} -- an empty smoke test proves nothing")

        # --- pass 1: schedule + result + boxscore, via the real functions,
        #     each with its OWN freshly-captured observed_at_utc (item 5) --
        finals_seen = []
        team_sog_reported: dict = {}
        for g in games:
            gid = g.get("id")
            schedule_observed_at = dt.datetime.utcnow().isoformat()
            try:
                nhl_api.ingest_schedule(conn, g, schedule_observed_at)
            except Exception as e:
                report["ok"] = False
                report["errors"].append(
                    f"endpoint=schedule; identifier=game_id={gid}; "
                    f"missing_field_or_error={e!r}; context={g}")
                continue
            if g.get("gameState") in ("OFF", "FINAL"):
                try:
                    nhl_api.ingest_result(conn, g, schedule_observed_at)
                    box = nhl_api.fetch_boxscore(session, gid)
                    boxscore_observed_at = dt.datetime.utcnow().isoformat()
                    before_rows = _stat_row_count(conn, gid)
                    nhl_api.upsert_player_stats_from_boxscore(conn, box, boxscore_observed_at)
                    after_rows = _stat_row_count(conn, gid)
                    if after_rows == before_rows:
                        report["ok"] = False
                        report["errors"].append(
                            f"endpoint=boxscore; identifier=game_id={gid}; boxscore "
                            f"fetched successfully but produced ZERO new "
                            f"player_game_stats/goalie_game_stats rows -- a "
                            f"structurally-changed or empty boxscore must FAIL, not "
                            f"silently pass")
                    for side in ("homeTeam", "awayTeam"):
                        reported = box.get(side, {}).get("sog")
                        abbrev = box.get(side, {}).get("abbrev")
                        if reported is not None and abbrev is not None:
                            team_sog_reported[(gid, abbrev)] = reported
                    finals_seen.append(gid)
                except Exception as e:
                    report["ok"] = False
                    report["errors"].append(
                        f"endpoint=result/boxscore; identifier=game_id={gid}; error={e!r}")
        conn.commit()
        report["steps"]["games_finalized"] = len(finals_seen)

        # --- structural sanity (spec item 6) ---
        report["steps"]["teams_bootstrapped"] = conn.execute(
            "SELECT COUNT(*) c FROM teams").fetchone()["c"]
        report["steps"].update(_counts(conn))

        fk_bad = _fk_violations(conn)
        if fk_bad:
            report["ok"] = False
            report["errors"].append(
                f"PRAGMA foreign_key_check found {len(fk_bad)} violation(s): "
                f"{[dict(r) for r in fk_bad[:5]]}")

        bad_games = conn.execute(
            "SELECT COUNT(*) c FROM games WHERE game_id IS NULL OR season IS NULL "
            "OR game_date IS NULL OR scheduled_start_utc IS NULL OR home_team IS NULL "
            "OR away_team IS NULL"
        ).fetchone()["c"]
        if bad_games:
            report["ok"] = False
            report["errors"].append(
                f"{bad_games} games row(s) missing a required field after ingestion")

        bad_finals = conn.execute(
            "SELECT g.game_id AS gid FROM games g WHERE g.game_state='FINAL' AND NOT EXISTS ("
            "  SELECT 1 FROM game_result_events e WHERE e.game_id=g.game_id "
            "  AND e.home_score IS NOT NULL AND e.away_score IS NOT NULL "
            "  AND e.final_period_type IS NOT NULL)"
        ).fetchall()
        if bad_finals:
            report["ok"] = False
            report["errors"].append(
                f"{len(bad_finals)} FINAL game(s) missing a complete game_result_events "
                f"row: {[r['gid'] for r in bad_finals][:5]}")

        for gid in finals_seen:
            for table in ("player_game_stats", "goalie_game_stats"):
                per_team = conn.execute(
                    f"SELECT team_id, COUNT(*) c FROM {table} WHERE game_id=? "
                    f"GROUP BY team_id", (gid,)).fetchall()
                if len(per_team) < 2:
                    report["ok"] = False
                    report["errors"].append(
                        f"game_id={gid}: {table} has non-empty rows for only "
                        f"{len(per_team)}/2 teams -- an empty row family for a "
                        f"finalized boxscore is a FAIL, not a quiet pass")

        ts_bad = _canonical_timestamp_violations(conn)
        if ts_bad:
            report["ok"] = False
            report["errors"].append(
                f"{len(ts_bad)} non-canonical persisted timestamp(s): {ts_bad[:5]}")

        bad_player_ids = conn.execute(
            "SELECT COUNT(*) c FROM players WHERE player_id IS NULL OR player_id=''"
        ).fetchone()["c"]
        if bad_player_ids:
            report["ok"] = False
            report["errors"].append(f"{bad_player_ids} player row(s) with an empty player_id")
        bad_team_ids = conn.execute(
            "SELECT COUNT(*) c FROM teams WHERE team_id IS NULL OR team_id=''"
        ).fetchone()["c"]
        if bad_team_ids:
            report["ok"] = False
            report["errors"].append(f"{bad_team_ids} team row(s) with an empty team_id")

        sog_mismatches = []
        for (gid, team_abbrev), reported in team_sog_reported.items():
            stored = conn.execute(
                "SELECT COALESCE(SUM(shots),0) s FROM player_game_stats "
                "WHERE game_id=? AND team_id=?", (gid, team_abbrev)).fetchone()["s"]
            if stored != reported:
                sog_mismatches.append(
                    f"game_id={gid} team={team_abbrev}: boxscore-reported team SOG="
                    f"{reported} vs stored skater-shots sum={stored}")
        report["steps"]["team_sog_cross_checks_available"] = len(team_sog_reported)
        if sog_mismatches:
            report["ok"] = False
            report["errors"].append(
                f"{len(sog_mismatches)} team-SOG cross-check mismatch(es): {sog_mismatches[:5]}")

        # --- idempotency rerun: schedule + result + BOXSCORE (spec item 2) --
        before = _counts(conn)
        for g in games:
            gid = g.get("id")
            schedule_observed_at = dt.datetime.utcnow().isoformat()
            nhl_api.ingest_schedule(conn, g, schedule_observed_at)
            if g.get("gameState") in ("OFF", "FINAL"):
                nhl_api.ingest_result(conn, g, schedule_observed_at)
                try:
                    box = nhl_api.fetch_boxscore(session, gid)
                    boxscore_observed_at = dt.datetime.utcnow().isoformat()
                    nhl_api.upsert_player_stats_from_boxscore(conn, box, boxscore_observed_at)
                except Exception as e:
                    report["ok"] = False
                    report["errors"].append(
                        f"endpoint=boxscore (idempotency rerun); identifier=game_id={gid}; "
                        f"error={e!r}")
        conn.commit()
        after = _counts(conn)
        report["steps"]["idempotency_rerun_stable"] = (before == after)
        if before != after:
            report["ok"] = False
            report["errors"].append(f"idempotency violated on rerun: {before} -> {after}")

        # --- CURRENT roster-IDENTITY ingestion pass (spec item 3/8), rerun
        #     a second time against an unchanged snapshot to prove the
        #     reconciliation itself is idempotent (no membership churn) --
        home_and_away_abbrevs = (
            {g["homeTeam"]["abbrev"] for g in games} | {g["awayTeam"]["abbrev"] for g in games})
        teams_encountered = sorted(home_and_away_abbrevs)
        roster_errors = 0
        try:
            nhl_api.ingest_current_roster_identities(conn, session, teams_encountered)
        except Exception as e:
            roster_errors += 1
            report["errors"].append(
                f"endpoint=roster/current; identifier=teams={teams_encountered}; error={e!r}")
        membership_before = conn.execute(
            "SELECT COUNT(*) c FROM team_membership_events").fetchone()["c"]
        try:
            nhl_api.ingest_current_roster_identities(conn, session, teams_encountered)
        except Exception as e:
            roster_errors += 1
            report["errors"].append(
                f"endpoint=roster/current (idempotency rerun); "
                f"identifier=teams={teams_encountered}; error={e!r}")
        membership_after = conn.execute(
            "SELECT COUNT(*) c FROM team_membership_events").fetchone()["c"]
        report["steps"]["teams_with_current_roster_ingested"] = (
            len(teams_encountered) - roster_errors)
        report["steps"]["current_roster_idempotency_stable"] = (
            membership_before == membership_after)
        if membership_before != membership_after:
            report["ok"] = False
            report["errors"].append(
                f"current-roster sync produced membership churn on an identical "
                f"rerun: {membership_before} -> {membership_after} "
                f"team_membership_events rows")
        if roster_errors:
            report["ok"] = False

        bad_players = conn.execute(
            "SELECT COUNT(*) c FROM players WHERE full_name IS NULL OR full_name='' "
            "OR position IS NULL OR position=''"
        ).fetchone()["c"]
        if bad_players:
            report["ok"] = False
            report["errors"].append(f"{bad_players} player row(s) with missing full_name/position")
        report["steps"]["players_total"] = conn.execute(
            "SELECT COUNT(*) c FROM players").fetchone()["c"]

        report["steps"]["note"] = (
            "this validates SCHEDULE/RESULT/BOXSCORE ingestion, CURRENT roster "
            "membership reconciliation (additions AND departures, via "
            "/v1/roster/{team}/current), and idempotency of all of the above -- "
            "injury/availability status and starting-goalie announcements are NOT "
            "covered (no public NHL source exists for either); see README.")
    finally:
        conn.close()
        path.unlink(missing_ok=True)
    return report


def print_report(report: dict) -> None:
    print("=" * 72)
    print("LIVE NHL CORE INGESTION SMOKE TEST")
    print("=" * 72)
    print("Uses a FRESH TEMPORARY database -- never nhl.db. This is NOT synthetic")
    print("validation -- see validate.py for the synthetic architecture/correctness report.")
    print()
    if report.get("network_unavailable"):
        print("LIVE NHL CORE INGESTION: NOT EXECUTED -- NETWORK UNAVAILABLE")
        print("  (this environment could not reach api-web.nhle.com -- see the error below;")
        print("   run this script from an environment with normal internet access)")
        print()
    for k, v in report["steps"].items():
        print(f"  {k}: {v}")
    print()
    if report["errors"]:
        print("ERRORS:")
        for e in report["errors"]:
            print(f"  - {e}")
        print()
    if not report.get("network_unavailable"):
        print(f"LIVE NHL CORE INGESTION: {'PASS' if report['ok'] and not report['errors'] else 'FAIL'}")
    print(f"RESULT: {'PASS' if report['ok'] and not report['errors'] else 'FAIL'}")
    print("=" * 72)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Live NHL core ingestion smoke test (v2.1.2a).")
    parser.add_argument("--start", type=str, default=None,
                         help="YYYY-MM-DD -- pin an exact smoke-test start date "
                              "(must be given together with --end)")
    parser.add_argument("--end", type=str, default=None,
                         help="YYYY-MM-DD -- pin an exact smoke-test end date "
                              "(must be given together with --start)")
    parser.add_argument("days", nargs="?", type=int, default=None,
                         help="deprecated positional form (pre-v2.1.2a); if given, used "
                              "as the backwards-search window_days instead of the default "
                              "7. Prefer --start/--end for a pinned range.")
    args = parser.parse_args()

    start_arg = dt.date.fromisoformat(args.start) if args.start else None
    end_arg = dt.date.fromisoformat(args.end) if args.end else None
    window_days_arg = args.days if args.days else 7

    # v2.1.2a spec item 7: only a genuine network/connectivity failure gets
    # the NOT EXECUTED -- NETWORK UNAVAILABLE label. Anything else escaping
    # run() (a programming bug, a sqlite error, an unexpected structure not
    # already caught inside run()'s own per-step try/excepts) is reported
    # as a real FAIL, with the actual exception and a traceback -- never
    # hidden behind the network-unavailable sentinel.
    try:
        rep = run(start_date=start_arg, end_date=end_arg, window_days=window_days_arg)
    except Exception as e:
        if _looks_like_network_unavailable(e):
            print("LIVE NHL CORE INGESTION: NOT EXECUTED -- NETWORK UNAVAILABLE")
            print(f"  ({e!r})")
            sys.exit(2)
        print("LIVE NHL CORE INGESTION: FAIL")
        print(f"  unexpected error (NOT a network-connectivity failure): {e!r}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    print_report(rep)
    sys.exit(0 if rep["ok"] and not rep["errors"] else 1)
