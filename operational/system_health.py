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
    }
