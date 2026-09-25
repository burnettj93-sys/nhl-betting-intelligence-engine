"""
Real SYSTEM_HEALTH object (Preseason Operationalization sprint, Sections
26-31). Never populated from demo constants -- every component below
reads an actual file, timestamp, DB connection, or registry-load result.

Reuses operational/readiness.py's existing, already-real per-source
status model (CURRENT/STALE/UNAVAILABLE/etc., cached in
operational/data_readiness_cache.json) rather than duplicating it --
this module's job is to translate that existing readiness data (plus a
few new components readiness.py doesn't cover: the registries, the
prospective ledger, and the database) into the canonical health
taxonomy requested this sprint: OK / STALE / WAITING / ERROR /
NOT_REQUIRED / UNKNOWN.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
READINESS_CACHE_PATH = REPO_ROOT / "operational" / "data_readiness_cache.json"

# Central freshness configuration (Section 31) -- never scattered per-page.
FRESHNESS_TTL_HOURS = {
    "schedule": 24.0,
    "roster": 24.0,
    "odds": 24.0,
    "moneypuck": 36.0,
    "starter_projection": 12.0,
    "prediction": 6.0,
}

_READINESS_TO_HEALTH = {
    "CURRENT": "OK", "OK": "OK",
    "STALE": "STALE",
    "PROJECTED": "OK",  # a real, honest projection is not itself unhealthy
    "NOT_REFRESHED": "WAITING",
    "UNAVAILABLE": "WAITING",
    "REQUIRES_PERMISSION": "WAITING",
    "SOURCE_CONTRACT_FAILURE": "ERROR",
}


def _health_item(status: str, label: str, last_updated_utc: str | None, message: str, source: str,
                  age_hours: float | None = None, technical_detail: str | None = None) -> dict:
    return {"status": status, "label": label, "last_updated_utc": last_updated_utc, "age_hours": age_hours,
            "message": message, "source": source, "technical_detail": technical_detail}


def _load_readiness_cache() -> dict | None:
    if not READINESS_CACHE_PATH.exists():
        return None
    try:
        with open(READINESS_CACHE_PATH) as f:
            return json.load(f)
    except json.JSONDecodeError:
        return None


def _from_readiness_block(cache: dict | None, key: str, label: str) -> dict:
    if cache is None or "readiness" not in cache or key not in cache["readiness"]:
        return _health_item("UNKNOWN", label, None, "no readiness snapshot available yet",
                             "operational/data_readiness_cache.json")
    block = cache["readiness"][key]
    raw_status = block.get("status", "UNKNOWN")
    status = _READINESS_TO_HEALTH.get(raw_status, "UNKNOWN")
    return _health_item(status, label, block.get("last_refreshed_at_utc") or block.get("last_accepted_at_utc")
                         or cache.get("readiness", {}).get("generated_at_utc"),
                         block.get("reason", raw_status), "operational/data_readiness_cache.json",
                         age_hours=block.get("age_hours"), technical_detail=json.dumps(block))


def nhl_api_health(cache: dict | None = None) -> dict:
    cache = cache if cache is not None else _load_readiness_cache()
    return _from_readiness_block(cache, "nhl_schedule", "NHL API")


def schedule_health(cache: dict | None = None) -> dict:
    cache = cache if cache is not None else _load_readiness_cache()
    return _from_readiness_block(cache, "nhl_schedule", "Schedule")


def rosters_health(cache: dict | None = None) -> dict:
    # No dedicated roster-sync timestamp exists separately from the NHL
    # schedule sync today -- reuse it explicitly rather than fabricate
    # a separate roster-freshness signal that doesn't exist.
    cache = cache if cache is not None else _load_readiness_cache()
    item = _from_readiness_block(cache, "nhl_results", "Rosters")
    item["technical_detail"] = "Rosters are refreshed by the same nhl_sync run as results/schedule."
    return item


def moneypuck_health(cache: dict | None = None) -> dict:
    cache = cache if cache is not None else _load_readiness_cache()
    parts = [_from_readiness_block(cache, k, k) for k in ("moneypuck_team", "moneypuck_skater", "moneypuck_goalie")]
    statuses = [p["status"] for p in parts]
    if "ERROR" in statuses:
        overall = "ERROR"
    elif all(s == "OK" for s in statuses):
        overall = "OK"
    elif all(s == "WAITING" for s in statuses):
        overall = "WAITING"
    else:
        overall = "STALE"
    messages = "; ".join(f"{p['label']}: {p['message']}" for p in parts)
    return _health_item(overall, "MoneyPuck", None, messages, "operational/data_readiness_cache.json",
                         technical_detail=json.dumps(parts))


def odds_api_health(cache: dict | None = None) -> dict:
    cache = cache if cache is not None else _load_readiness_cache()
    return _from_readiness_block(cache, "odds", "Odds API")


def draftkings_markets_health(cache: dict | None = None) -> dict:
    # No dedicated DraftKings-market-availability signal exists separately
    # from the Odds API cache today -- SOG is the only live-tested family.
    cache = cache if cache is not None else _load_readiness_cache()
    item = _from_readiness_block(cache, "odds", "DraftKings Markets")
    item["technical_detail"] = "Only Player SOG has a live-tested DraftKings payload contract today."
    return item


def _registry_health(loader, label: str) -> dict:
    try:
        n = loader()
        return _health_item("OK", label, dt.datetime.now(dt.timezone.utc).isoformat(),
                             f"{n} entries loaded successfully", label)
    except Exception as e:
        return _health_item("ERROR", label, None, f"failed to load: {e}", label, technical_detail=repr(e))


def model_registry_health() -> dict:
    def _load():
        from research.model_registry import MODEL_REGISTRY
        return len(MODEL_REGISTRY)
    return _registry_health(_load, "Model Registry")


def market_registry_health() -> dict:
    def _load():
        from research.player_props.market_registry import CANONICAL_MARKETS
        return len(CANONICAL_MARKETS)
    return _registry_health(_load, "Market Registry")


def joint_registry_health() -> dict:
    def _load():
        from research.joint_shot_workload.joint_dependence_registry import JOINT_DEPENDENCE_REGISTRY
        return len(JOINT_DEPENDENCE_REGISTRY)
    return _registry_health(_load, "Joint Dependence Registry")


def context_overlay_registry_health() -> dict:
    def _load():
        import json as _json
        from research.context_overlay.registry import REGISTRY_PATH
        if not REGISTRY_PATH.exists():
            raise FileNotFoundError(str(REGISTRY_PATH))
        with open(REGISTRY_PATH) as f:
            return len(_json.load(f))
    return _registry_health(_load, "Context Overlay Registry")


def database_health() -> dict:
    import sqlite3
    from db import DB_PATH
    if not DB_PATH.exists():
        return _health_item("ERROR", "Database", None, "nhl.db not found", str(DB_PATH))
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("SELECT 1")
        conn.close()
        return _health_item("OK", "Database", dt.datetime.now(dt.timezone.utc).isoformat(),
                             "connection OK", str(DB_PATH))
    except Exception as e:
        return _health_item("ERROR", "Database", None, f"connection failed: {e}", str(DB_PATH),
                             technical_detail=repr(e))


def prospective_ledger_health() -> dict:
    from operational import prospective_ledger as pl
    if not pl.DB_PATH.exists():
        return _health_item("NOT_REQUIRED", "Prospective Ledger", None,
                             "no observations recorded yet this preseason", str(pl.DB_PATH))
    try:
        conn = pl.get_conn(pl.DB_PATH)
        n = conn.execute("SELECT COUNT(*) AS n FROM predictions").fetchone()["n"]
        conn.close()
        return _health_item("OK", "Prospective Ledger", dt.datetime.now(dt.timezone.utc).isoformat(),
                             f"{n} observations recorded", str(pl.DB_PATH))
    except Exception as e:
        return _health_item("ERROR", "Prospective Ledger", None, f"query failed: {e}", str(pl.DB_PATH),
                             technical_detail=repr(e))


def last_sync_health(cache: dict | None = None) -> dict:
    cache = cache if cache is not None else _load_readiness_cache()
    if cache is None or "readiness" not in cache:
        return _health_item("UNKNOWN", "Last Sync", None, "no sync has ever completed",
                             "operational/data_readiness_cache.json")
    generated_at = cache["readiness"].get("generated_at_utc")
    return _health_item("OK", "Last Sync", generated_at, f"last full sync at {generated_at}",
                         "operational/data_readiness_cache.json")


def special_teams_role_freshness_health() -> dict:
    """Preseason Operational Readiness Closure sprint (2026-08-30), Track
    7 Part 48: special-teams role data freshness -- reads
    operational/special_teams_history.db's own real coverage summary
    (built this session's earlier sprint), never a separate, invented
    freshness signal."""
    try:
        from operational import special_teams_history_store as sths
        if not sths.DB_PATH.exists():
            return _health_item("WAITING", "Special-Teams Role History", None,
                                 "special_teams_history.db not found", str(sths.DB_PATH))
        conn = sths.get_connection()
        summary = sths.coverage_summary(conn)
        conn.close()
        return _health_item("OK", "Special-Teams Role History", summary.get("latest_game_date"),
                             f"{summary.get('total_rows', 0)} rows, through {summary.get('latest_game_date')}",
                             str(sths.DB_PATH), technical_detail=json.dumps(summary))
    except Exception as e:
        return _health_item("ERROR", "Special-Teams Role History", None, f"query failed: {e}",
                             "operational/special_teams_history.db", technical_detail=repr(e))


def odds_archive_freshness_health() -> dict:
    """Part 48: Odds API archive freshness -- the real, on-disk raw
    capture directory (data/raw/the_odds_api/live/), never the demo
    board cache. Reports the most recent REAL capture's timestamp; a
    directory with zero files is WAITING (never seen a real pull yet),
    never ERROR (that's an expected preseason state, not a failure)."""
    archive_dir = REPO_ROOT / "data" / "raw" / "the_odds_api" / "live"
    if not archive_dir.exists():
        return _health_item("WAITING", "Odds API Archive", None, "archive directory does not exist yet",
                             str(archive_dir))
    files = sorted(archive_dir.glob("*.json"))
    if not files:
        return _health_item("WAITING", "Odds API Archive", None, "no captures recorded yet", str(archive_dir))
    try:
        latest = max(files, key=lambda p: p.stat().st_mtime)
        with open(latest) as f:
            meta = json.load(f).get("meta", {})
        retrieved_at = meta.get("retrieved_at_utc")
        return _health_item("OK", "Odds API Archive", retrieved_at,
                             f"{len(files)} real capture(s) on disk, latest at {retrieved_at}",
                             str(archive_dir))
    except Exception as e:
        return _health_item("ERROR", "Odds API Archive", None, f"could not read latest capture: {e}",
                             str(archive_dir), technical_detail=repr(e))


def contract_status_health() -> dict:
    """Part 50: VERIFIED LIVE CONTRACTS is reported directly from
    research/generic_prop_pricing/provider_adapter.py::VERIFIED_CONTRACTS
    -- never inferred from demo-mode market availability, which this
    function does not even import. Currently 0, honestly, and stays 0
    until Part 41's real workflow adds a real entry."""
    from research.generic_prop_pricing.provider_adapter import VERIFIED_CONTRACTS
    n = len(VERIFIED_CONTRACTS)
    status = "OK" if n == 0 else "OK"  # zero is the expected, healthy preseason state, not an error
    return _health_item(status, "Sportsbook Contract Status", None,
                         f"VERIFIED LIVE CONTRACTS: {n}", "research/generic_prop_pricing/provider_adapter.py",
                         technical_detail=json.dumps(sorted(VERIFIED_CONTRACTS)))


def settlement_backlog_health() -> dict:
    """Part 48/49: unresolved-past-final observations, UNRESOLVED-status
    rows (the resolver's own honest "cannot settle this yet" bucket), and
    any settlement errors -- read directly from the real prospective
    ledger, never a simulated count."""
    from operational import prospective_ledger as pl
    from operational import settle_daily_observations as sdo
    if not pl.DB_PATH.exists():
        return _health_item("NOT_REQUIRED", "Settlement Backlog", None,
                             "no observations recorded yet this preseason", str(pl.DB_PATH))
    try:
        conn = pl.get_conn(pl.DB_PATH)
        candidates = sdo.find_settlement_candidates(conn)
        unresolved = conn.execute(
            "SELECT COUNT(*) AS n FROM predictions WHERE result_status='UNRESOLVED'").fetchone()["n"]
        conn.close()
        n_pending_past_start = len(candidates)
        status = "OK" if n_pending_past_start == 0 else "WAITING"
        return _health_item(
            status, "Settlement Backlog", dt.datetime.now(dt.timezone.utc).isoformat(),
            f"{n_pending_past_start} observation(s) past event start awaiting settlement, "
            f"{unresolved} UNRESOLVED (fail-closed) total", str(pl.DB_PATH))
    except Exception as e:
        return _health_item("ERROR", "Settlement Backlog", None, f"query failed: {e}", str(pl.DB_PATH),
                             technical_detail=repr(e))


_SCHEDULER_LABELS = ("com.nhlengine.moneyline-snapshot", "com.nhlengine.daily-props-pull",
                     "com.nhlengine.prop-sweep-first", "com.nhlengine.prop-sweep-second",
                     # P0.1 (2026-09-24 hardening block): the odds scheduler was extended with
                     # 5 more real jobs (NHL sync full/midday/pregame, settlement, post-mortem) --
                     # this health check must reflect the FULL real scheduler, not just its
                     # original 4 odds-only jobs.
                     "com.nhlengine.daily-nhl-sync", "com.nhlengine.midday-schedule-refresh",
                     "com.nhlengine.pregame-targeted-refresh", "com.nhlengine.daily-settlement",
                     "com.nhlengine.daily-postmortem", "com.nhlengine.database-backup")

# VPS Production Deployment block (2026-09-24), Part 14: the 1:1 mapping
# from each macOS launchd label above to its VPS systemd timer unit, per
# docs/VPS_DEPLOYMENT_PREP.md's own naming -- same job order, same count.
_SYSTEMD_TIMER_UNITS = ("nhlengine-moneyline.timer", "nhlengine-props-pull.timer",
                        "nhlengine-sweep-first.timer", "nhlengine-sweep-second.timer",
                        "nhlengine-nhl-sync.timer", "nhlengine-midday-refresh.timer",
                        "nhlengine-pregame-refresh.timer", "nhlengine-settlement.timer",
                        "nhlengine-postmortem.timer", "nhlengine-backup.timer")


def live_odds_scheduler_health() -> dict:
    """Part 75 (extended P0.1; made cross-platform in the VPS Production
    Deployment block, 2026-09-24 Part 14): LIVE_ODDS_SCHEDULER -- real
    scheduler state via a local OS query (never a network call, never an
    Odds API credit -- Part 26's dashboard-safety rule is about the
    sportsbook API specifically, not local process introspection).
    `launchctl list` only exists on macOS; running this unchanged on a
    Linux VPS always returned FileNotFoundError -> UNKNOWN, permanently,
    even with every systemd timer enabled and healthy. Real gap found
    via code inspection, not a query failure that had actually happened
    yet (no VPS exists). Dispatches on the real platform; never assumes
    installed."""
    import platform
    if platform.system() == "Linux":
        return _scheduler_health_via_systemctl()
    return _scheduler_health_via_launchctl()


def _scheduler_health_via_launchctl() -> dict:
    import subprocess
    try:
        result = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=5)
        listed = result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        return _health_item("UNKNOWN", "Live Odds Scheduler", None, f"could not query launchctl: {e}",
                             "launchctl list", technical_detail=repr(e))
    loaded = [label for label in _SCHEDULER_LABELS if label in listed]
    if not loaded:
        return _health_item("WAITING", "Live Odds Scheduler", None,
                             "no scheduler jobs loaded -- activate per README's scheduler section",
                             "launchctl list")
    missing = [label for label in _SCHEDULER_LABELS if label not in loaded]
    status = "OK" if not missing else "STALE"
    message = f"{len(loaded)}/{len(_SCHEDULER_LABELS)} scheduler job(s) loaded"
    if missing:
        message += f" -- missing: {', '.join(missing)}"
    return _health_item(status, "Live Odds Scheduler", dt.datetime.now(dt.timezone.utc).isoformat(),
                         message, "launchctl list", technical_detail=", ".join(loaded))


def _scheduler_health_via_systemctl() -> dict:
    import subprocess
    try:
        result = subprocess.run(["systemctl", "list-timers", "--all", "--no-legend"],
                                 capture_output=True, text=True, timeout=5)
        listed = result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        return _health_item("UNKNOWN", "Live Odds Scheduler", None, f"could not query systemctl: {e}",
                             "systemctl list-timers", technical_detail=repr(e))
    loaded = [unit for unit in _SYSTEMD_TIMER_UNITS if unit in listed]
    if not loaded:
        return _health_item("WAITING", "Live Odds Scheduler", None,
                             "no scheduler timers loaded -- see docs/VPS_DEPLOYMENT_PREP.md",
                             "systemctl list-timers")
    missing = [unit for unit in _SYSTEMD_TIMER_UNITS if unit not in loaded]
    status = "OK" if not missing else "STALE"
    message = f"{len(loaded)}/{len(_SYSTEMD_TIMER_UNITS)} scheduler timer(s) loaded"
    if missing:
        message += f" -- missing: {', '.join(missing)}"
    return _health_item(status, "Live Odds Scheduler", dt.datetime.now(dt.timezone.utc).isoformat(),
                         message, "systemctl list-timers", technical_detail=", ".join(loaded))


def _load_cache_safely(path: Path) -> dict | None:
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def odds_collection_status() -> dict:
    """Part 25/75: ODDS LAST UPDATED / CREDITS REMAINING / TRACKED
    EVENTS / PLAYER PROP COVERAGE -- read from the real cache files the
    collector writes (never a live call). Combines the moneyline and
    targeted-sweep caches since either may be the most recent."""
    moneyline = _load_cache_safely(REPO_ROOT / "operational" / "moneyline_snapshot_cache.json")
    props_board = _load_cache_safely(REPO_ROOT / "operational" / "live_multimarket_board_cache.json")
    sweep = _load_cache_safely(REPO_ROOT / "operational" / "targeted_prop_sweep_cache.json")

    candidates = [c for c in (moneyline, props_board, sweep) if c and c.get("generated_at_utc")]
    if not candidates:
        return {
            "last_updated_utc": None, "credits_remaining": None, "tracked_events": 0,
            "player_prop_quotes": 0, "status": "WAITING",
        }
    latest = max(candidates, key=lambda c: c["generated_at_utc"])
    credits_remaining = (latest.get("summary") or {}).get("remaining_quota_last_seen")
    tracked_events = (moneyline or {}).get("summary", {}).get("events_seen") or \
        (props_board or {}).get("summary", {}).get("events_seen") or 0
    prop_quotes = len((props_board or {}).get("rows", [])) + len((sweep or {}).get("rows", []))
    return {
        "last_updated_utc": latest["generated_at_utc"], "credits_remaining": credits_remaining,
        "tracked_events": tracked_events, "player_prop_quotes": prop_quotes, "status": "OK",
    }


def new_contract_candidates_health() -> dict:
    """Part 15/75: how many genuinely-new market keys have been flagged
    (never auto-verified) since the collector started running."""
    from operational.live_odds_daily_pull import NEW_CONTRACT_CANDIDATES_PATH
    if not NEW_CONTRACT_CANDIDATES_PATH.exists():
        return _health_item("OK", "New Contract Candidates", None, "0 flagged", str(NEW_CONTRACT_CANDIDATES_PATH))
    try:
        n = sum(1 for _ in open(NEW_CONTRACT_CANDIDATES_PATH))
    except OSError as e:
        return _health_item("ERROR", "New Contract Candidates", None, f"could not read: {e}",
                             str(NEW_CONTRACT_CANDIDATES_PATH), technical_detail=repr(e))
    return _health_item("OK", "New Contract Candidates", None, f"{n} flagged, none auto-verified",
                         str(NEW_CONTRACT_CANDIDATES_PATH))


def postmortem_status_health() -> dict:
    """Part 75: POSTMORTEM_STATUS / LAST_POSTMORTEM -- the real
    reports/daily/postmortem_*.md files this project's own
    operational.daily_postmortem.write_report_markdown() writes."""
    reports_dir = REPO_ROOT / "reports" / "daily"
    if not reports_dir.exists():
        return _health_item("WAITING", "Daily Post-Mortem", None, "no post-mortem has run yet", str(reports_dir))
    reports = sorted(reports_dir.glob("postmortem_*.md"))
    if not reports:
        return _health_item("WAITING", "Daily Post-Mortem", None, "no post-mortem has run yet", str(reports_dir))
    latest = reports[-1]
    return _health_item("OK", "Daily Post-Mortem",
                         dt.datetime.fromtimestamp(latest.stat().st_mtime, tz=dt.timezone.utc).isoformat(),
                         f"latest report: {latest.name}", str(reports_dir))


def build_system_health() -> dict:
    """The full SYSTEM_HEALTH object (Section 27): one entry per
    component, all real. Call this once per page render; each
    sub-function is cheap (JSON reads / a single SQLite ping)."""
    cache = _load_readiness_cache()
    return {
        "NHL_API": nhl_api_health(cache),
        "SCHEDULE": schedule_health(cache),
        "ROSTERS": rosters_health(cache),
        "MONEYPUCK": moneypuck_health(cache),
        "ODDS_API": odds_api_health(cache),
        "DRAFTKINGS_MARKETS": draftkings_markets_health(cache),
        "MODEL_REGISTRY": model_registry_health(),
        "MARKET_REGISTRY": market_registry_health(),
        "JOINT_REGISTRY": joint_registry_health(),
        "CONTEXT_OVERLAY_REGISTRY": context_overlay_registry_health(),
        "DATABASE": database_health(),
        "PROSPECTIVE_LEDGER": prospective_ledger_health(),
        "LAST_SYNC": last_sync_health(cache),
        "SPECIAL_TEAMS_HISTORY": special_teams_role_freshness_health(),
        "ODDS_ARCHIVE": odds_archive_freshness_health(),
        "CONTRACT_STATUS": contract_status_health(),
        "SETTLEMENT_BACKLOG": settlement_backlog_health(),
        "LIVE_ODDS_SCHEDULER": live_odds_scheduler_health(),
        "NEW_CONTRACT_CANDIDATES": new_contract_candidates_health(),
        "DAILY_POSTMORTEM": postmortem_status_health(),
    }


def _job_health(component: str, max_age_hours: float) -> dict:
    from operational import ingestion_health as ih
    ready, reason = ih.dependency_ready(component, max_age_hours=max_age_hours)
    row = ih.load_health().get(component)
    return {"status": "OK" if ready else "NOT_READY", "reason": reason,
            "last_run": row.get("recorded_at_utc") if row else None}


def production_health_summary() -> dict:
    """VPS Production Deployment block (2026-09-24), Part 13: the
    smallest practical unified health check for ops/monitoring -- APP,
    DATABASES, NHL_DATA, ODDS, SCHEDULERS, SETTLEMENT, POSTMORTEM,
    BACKUPS, plus YAHOO reported separately. Reuses every existing
    granular health/readiness function (build_system_health() above,
    ingestion_health.dependency_ready() -- already the real dependency
    mechanism wired into the sync -> settlement -> postmortem -> backup
    chain, see those modules' own main()) and opening_day_readiness's
    own check_yahoo() -- no new data source, no new status vocabulary.
    Yahoo's OWNER_AUTH_REQUIRED/REAUTH_REQUIRED/CONNECTED states are
    surfaced under their own YAHOO key and never affect any other key
    here (Part 13/20: Yahoo must never fail betting-side health).
    Imports opening_day_readiness lazily -- that module already imports
    this one (for the scheduler-label constants), so a top-level import
    here would be circular."""
    import opening_day_readiness as odr

    system = build_system_health()
    return {
        "APP": {"status": "OK", "message": "process responding (this call executed)"},
        "DATABASES": {"status": system["DATABASE"]["status"], "message": system["DATABASE"]["message"]},
        "NHL_DATA": _job_health("nhl_sync_full", max_age_hours=30.0),
        "ODDS": {"status": system["ODDS_API"]["status"], "message": system["ODDS_API"]["message"]},
        "SCHEDULERS": {"status": system["LIVE_ODDS_SCHEDULER"]["status"],
                       "message": system["LIVE_ODDS_SCHEDULER"]["message"]},
        "SETTLEMENT": _job_health("settlement", max_age_hours=30.0),
        "POSTMORTEM": _job_health("postmortem", max_age_hours=30.0),
        "BACKUPS": _job_health("database_backups", max_age_hours=30.0),
        "YAHOO": odr.check_yahoo(),
    }
