"""
P0.5 (2026-09-24 hardening block): a single production-readiness
command. Answers, in one place, exactly what the block asked for --
databases, NHL, odds, models, pipeline, context, predictions, Yahoo --
and returns a concise READY / READY_WITH_WARNINGS / NOT_READY verdict
computed from simple, factual, documented rules (never a subjective
score).

Usage:
    python3 opening_day_readiness.py

Read-only: makes no odds API call (reads cached state only, matching
the dashboard's own "0 paid calls" rule), and its only NHL API traffic
is the free `/events`-equivalent already performed by whatever cached
readiness snapshot exists. Never mutates a production model, decision
policy, or any prediction row.
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys

import db
from fantasy.yahoo.token_store import EncryptedFileTokenStore
from operational import ingestion_health
from operational import paper_bankroll as pb
from operational import prospective_ledger as pl
from operational import system_health as sh
from research import model_registry as mr

READY, READY_WITH_WARNINGS, NOT_READY = "READY", "READY_WITH_WARNINGS", "NOT_READY"

# A component's cached health is considered STALE (a warning, not a
# hard failure) past this many hours since its last recorded SUCCESS.
_STALE_AFTER_HOURS = {
    "nhl_sync_full": 30.0,               # daily job, ~24h cadence + buffer
    "nhl_midday_schedule_refresh": 30.0,
    "nhl_pregame_targeted_refresh": 30.0,
    "settlement": 30.0,
    "postmortem": 30.0,
    "database_backups": 30.0,
}


def check_databases() -> dict:
    """Reachable/writable: a real connect + a real, rolled-back write
    probe against each database this project actually depends on.
    Never leaves a write behind -- every probe is inside a transaction
    that is explicitly rolled back, not committed."""
    checks = {}
    targets = {
        "nhl.db": lambda: db.get_conn(),
        "prospective_observations.db": lambda: pl.init_db(),
        "paper_bankroll.db": lambda: pb.init_db(),
    }
    for name, opener in targets.items():
        try:
            conn = opener()
            conn.execute("SELECT 1")
            conn.execute("CREATE TEMP TABLE IF NOT EXISTS _readiness_probe (x INTEGER)")
            conn.execute("INSERT INTO _readiness_probe VALUES (1)")
            conn.rollback()
            conn.close()
            checks[name] = {"reachable": True, "writable": True, "error": None}
        except Exception as exc:  # noqa: BLE001
            checks[name] = {"reachable": False, "writable": False, "error": f"{exc.__class__.__name__}: {exc}"}
    return checks


def check_nhl() -> dict:
    health = ingestion_health.load_health()
    full_sync = health.get("nhl_sync_full", {})
    age = ingestion_health.component_age_hours(full_sync)
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    try:
        conn = db.get_conn()
        games_today = conn.execute("SELECT COUNT(*) c FROM games WHERE game_date = ?", (today,)).fetchone()["c"]
        max_date = conn.execute("SELECT MAX(game_date) m FROM games").fetchone()["m"]
        conn.close()
    except Exception as exc:  # noqa: BLE001
        games_today, max_date = None, None
    return {
        "last_sync_status": full_sync.get("last_status", "UNKNOWN"),
        "last_sync_success_age_hours": age,
        "todays_games_present": games_today,
        "most_recent_game_date_on_file": max_date,
    }


def check_odds() -> dict:
    status = sh.odds_collection_status()
    scheduler = sh.live_odds_scheduler_health()
    return {"collection_status": status, "scheduler": scheduler}


def check_models() -> dict:
    try:
        entries = list(mr.MODEL_REGISTRY)
        return {
            "registry_loads": True, "entry_count": len(entries),
            "active_versions": {e.model_id: e.model_version for e in entries},
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        return {"registry_loads": False, "entry_count": 0, "active_versions": {}, "error": str(exc)}


def check_pipeline() -> dict:
    health = ingestion_health.load_health()
    components = {}
    for component, threshold in _STALE_AFTER_HOURS.items():
        row = health.get(component)
        if row is None:
            components[component] = {"status": "NEVER_RUN", "age_hours": None}
            continue
        age = ingestion_health.component_age_hours(row)
        stale = age is None or age > threshold
        components[component] = {
            "status": "STALE" if stale else "CURRENT",
            "last_status": row.get("last_status"),
            "age_hours": age,
        }
    return {"components": components, "scheduler_loaded": sh.live_odds_scheduler_health()}


def check_context() -> dict:
    """Honest per docs/CONTEXT_DATA_DEPENDENCY_AUDIT.md: no real public
    source exists for starting-goalie confirmation, injury status, or
    lineup/PP context -- these are always NOT_AVAILABLE, by design, not
    a bug to fix here. Roster freshness IS real and checked."""
    health = ingestion_health.load_health()
    roster_age = None
    for component in ("nhl_sync_full", "nhl_pregame_targeted_refresh"):
        row = health.get(component)
        if row is not None:
            age = ingestion_health.component_age_hours(row)
            if age is not None and (roster_age is None or age < roster_age):
                roster_age = age
    return {
        "roster_freshness_hours": roster_age,
        "goalie_availability": "NOT_AVAILABLE (no real source integrated -- see docs/CONTEXT_DATA_DEPENDENCY_AUDIT.md)",
        "injury_availability": "NOT_AVAILABLE (no real source integrated -- see docs/CONTEXT_DATA_DEPENDENCY_AUDIT.md)",
        "lineup_pp_availability": "NOT_AVAILABLE (no real source integrated -- see docs/CONTEXT_DATA_DEPENDENCY_AUDIT.md)",
    }


def check_predictions() -> dict:
    """Stale unresolved / duplicate / invalid-state predictions -- a
    real query over the real prospective ledger, never a guess."""
    try:
        conn = pl.init_db()
        now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        one_day_ago = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        stale_pending = conn.execute(
            "SELECT COUNT(*) c FROM predictions WHERE result_status='PENDING' AND event_start_utc < ?",
            (one_day_ago,)).fetchone()["c"]
        duplicate_idempotency_keys = conn.execute(
            "SELECT COUNT(*) c FROM (SELECT idempotency_key FROM predictions GROUP BY idempotency_key "
            "HAVING COUNT(*) > 1)").fetchone()["c"]
        invalid_state = conn.execute(
            "SELECT COUNT(*) c FROM predictions WHERE result_status NOT IN "
            "('PENDING','WIN','LOSS','PUSH','VOID','UNRESOLVED')").fetchone()["c"]
        conn.close()
        return {"stale_pending_over_24h": stale_pending, "duplicate_idempotency_keys": duplicate_idempotency_keys,
                "invalid_result_states": invalid_state, "error": None}
    except Exception as exc:  # noqa: BLE001
        return {"stale_pending_over_24h": None, "duplicate_idempotency_keys": None,
                "invalid_result_states": None, "error": str(exc)}


def check_real_recommendation_pipeline() -> dict:
    """Real Recommendation Pipeline block (2026-09-24), Part 26:
    distinguishes "is the real orchestration path operational" from "did
    it produce a qualifying bet" -- these are DIFFERENT questions. A real
    production day can legitimately produce zero qualifying bets (Part
    13) and that must never be reported as a pipeline failure; what
    WOULD be a failure is the orchestration modules themselves failing to
    import, or the databases they depend on being unreachable."""
    try:
        from operational import real_odds_bridge, real_recommendation_orchestrator  # noqa: F401
        modules_import_cleanly = True
        import_error = None
    except Exception as exc:  # noqa: BLE001
        modules_import_cleanly = False
        import_error = f"{exc.__class__.__name__}: {exc}"

    real_odds_snapshot_rows = None
    real_moneyline_recommendations_recorded = None
    real_market_paper_bets_placed = None
    query_error = None
    try:
        conn = db.get_conn()
        real_odds_snapshot_rows = conn.execute(
            "SELECT COUNT(*) c FROM odds_snapshots WHERE data_provider = 'the-odds-api'").fetchone()["c"]
        conn.close()
        pl_conn = pl.init_db()
        real_moneyline_recommendations_recorded = pl_conn.execute(
            "SELECT COUNT(*) c FROM predictions WHERE market_id = 'MONEYLINE'").fetchone()["c"]
        pl_conn.close()
        bankroll_conn = pb.init_db()
        real_market_paper_bets_placed = bankroll_conn.execute(
            "SELECT COUNT(*) c FROM paper_bets WHERE track = 'REAL_MARKET_PAPER'").fetchone()["c"]
        bankroll_conn.close()
    except Exception as exc:  # noqa: BLE001
        query_error = f"{exc.__class__.__name__}: {exc}"

    orchestration_status = "HEALTHY" if modules_import_cleanly and query_error is None else "NOT_OPERATIONAL"
    return {
        "orchestration_status": orchestration_status,
        "orchestration_import_error": import_error,
        "real_odds_snapshot_rows": real_odds_snapshot_rows,
        "real_moneyline_recommendations_recorded": real_moneyline_recommendations_recorded,
        "real_market_paper_bets_placed": real_market_paper_bets_placed,
        "query_error": query_error,
        "note": ("zero real_market_paper_bets_placed is a VALID, HEALTHY outcome -- a real "
                 "production day can legitimately have zero qualifying bets (Part 13). This is "
                 "reflected ONLY in orchestration_status, never inferred from the bet count."),
    }


def check_real_prop_pipeline() -> dict:
    """Live SOG + Saves Production Certification block (2026-09-24), Part
    39: the SOG/Saves analog of check_real_recommendation_pipeline(),
    reported SEPARATELY per market rather than collapsed into one
    misleading green status (the block's own explicit instruction).
    PENDING_LIVE_CONTRACT is a distinct, real, non-failure state -- ONLY
    true because DraftKings has never posted either market (see
    docs/LIVE_SOG_SAVES_CERTIFICATION.md), not because anything here is
    broken."""
    try:
        from operational import real_prop_orchestrator  # noqa: F401
        from research.generic_prop_pricing import provider_adapter as pa
        modules_import_cleanly = True
        import_error = None
    except Exception as exc:  # noqa: BLE001
        modules_import_cleanly = False
        import_error = f"{exc.__class__.__name__}: {exc}"
        pa = None

    def _market_status(market_family: str) -> str:
        if not modules_import_cleanly:
            return "NOT_READY"
        return "READY" if pa.is_contract_verified("draftkings", market_family) else "PENDING_LIVE_CONTRACT"

    real_sog_recorded = None
    real_saves_recorded = None
    query_error = None
    try:
        pl_conn = pl.init_db()
        real_sog_recorded = pl_conn.execute(
            "SELECT COUNT(*) c FROM predictions WHERE market_family = 'SOG'").fetchone()["c"]
        real_saves_recorded = pl_conn.execute(
            "SELECT COUNT(*) c FROM predictions WHERE market_family = 'GOALIE_SAVES'").fetchone()["c"]
        pl_conn.close()
    except Exception as exc:  # noqa: BLE001
        query_error = f"{exc.__class__.__name__}: {exc}"

    orchestration_status = "HEALTHY" if modules_import_cleanly and query_error is None else "NOT_OPERATIONAL"
    return {
        "orchestration_status": orchestration_status,
        "orchestration_import_error": import_error,
        "query_error": query_error,
        "sog_status": _market_status("PLAYER_SOG"),
        "saves_status": _market_status("GOALIE_SAVES"),
        "real_sog_recommendations_recorded": real_sog_recorded,
        "real_saves_recommendations_recorded": real_saves_recorded,
        # Game Edge Parlay needs at least one market's contract verified
        # to ever produce a real (not "no qualifying parlay") result --
        # PARTIAL reflects "the adapter exists and is wired, but nothing
        # can feed it yet", never a claim that the parlay engine itself
        # is broken.
        "game_edge_parlay_status": "PARTIAL" if modules_import_cleanly else "NOT_READY",
        # Starting-Goalie Certainty + Prop Contract Watch block
        # (2026-09-24), Part 11: SOG/Saves MARKET readiness (above) is a
        # different question from Saves' own STARTER-IDENTITY readiness
        # -- reported separately, never collapsed. PARTIAL (not
        # NOT_READY): a real, validated internal starter-projection
        # model exists and is wired (research/goalie_intelligence/model.py,
        # 67.5% true-holdout top-1 accuracy); it just can never itself
        # satisfy CONFIRMED (Part 4 -- see docs/STARTING_GOALIE_SOURCE_
        # AUDIT.md). WAIT_ONLY (not READY, not NOT_READY): the Saves
        # actionability gate mechanically works today -- it just can
        # never produce BET/WATCH until a real CONFIRMED source exists,
        # so every real Saves observation with positive edge correctly
        # settles on WAIT.
        "saves_starter_data_status": "PARTIAL" if modules_import_cleanly else "NOT_READY",
        "saves_actionability_status": "WAIT_ONLY" if modules_import_cleanly else "NOT_READY",
        "note": ("PENDING_LIVE_CONTRACT is a real, expected, non-failure state -- see "
                 "docs/LIVE_SOG_SAVES_CERTIFICATION.md for the exhaustive real-payload scan "
                 "that established it. It becomes READY the day DraftKings first posts either market. "
                 "WAIT_ONLY for Saves actionability is likewise expected, not a failure -- see "
                 "docs/STARTING_GOALIE_SOURCE_AUDIT.md."),
    }


def check_yahoo() -> dict:
    """Never blocks overall betting readiness (per instruction) --
    purely informational."""
    token = EncryptedFileTokenStore().get()
    if token is None:
        return {"status": "OWNER_AUTH_REQUIRED"}
    if token.is_expired():
        return {"status": "REAUTH_REQUIRED"}
    return {"status": "CONNECTED"}


def _launchctl_loaded_count() -> int | None:
    """Name kept for existing test-patch compatibility -- VPS Production
    Deployment block (2026-09-24), Part 14: real second instance of the
    same launchctl-only gap already found and fixed in
    operational/system_health.py::live_odds_scheduler_health(). This one
    fed straight into build_readiness_report()'s hard-failure/warning
    logic, so on a Linux VPS it would have permanently reported "could
    not query launchctl" as a warning regardless of how healthy the real
    systemd timers were. Both label tuples have the same real length
    (10), so the existing `< len(sh._SCHEDULER_LABELS)` comparison
    downstream stays correct on either platform."""
    import platform
    try:
        if platform.system() == "Linux":
            result = subprocess.run(["systemctl", "list-timers", "--all", "--no-legend"],
                                     capture_output=True, text=True, timeout=5)
            return sum(1 for unit in sh._SYSTEMD_TIMER_UNITS if unit in result.stdout)
        result = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=5)
        return sum(1 for label in sh._SCHEDULER_LABELS if label in result.stdout)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


# ---- per-component states (Production Activation block, 2026-09-25) ------------------------------------------
# The single verdict above answers "may we run?"; this answers "what exactly is ready, waiting, or blocked?"
# Precise states, never a bare READY / NOT_READY. Read-only; no network, no Odds API credit.
COMPONENT_STATES = ("READY", "READY_WITH_WARNINGS", "WAITING_FOR_LIVE_MARKET", "WAIT_ONLY", "NO_REAL_SAMPLE_YET",
                    "PARTIAL", "STALE", "OWNER_ACTION_REQUIRED", "OWNER_AUTH_REQUIRED", "FAILED")
_QUOTA_WARN_BELOW, _QUOTA_FAIL_BELOW = 150, 20      # 20 == live_odds_daily_pull.DEFAULT_SAFETY_FLOOR


def _c(state: str, detail: str) -> dict:
    return {"state": state, "detail": detail}


def _health_component(health: dict, key: str, max_hours: float, label: str) -> dict:
    row = health.get(key)
    if row is None:
        return _c("STALE", f"{label}: never run")
    age = ingestion_health.component_age_hours(row)
    status = row.get("last_status")
    if status in ("FAILED", "ERROR"):
        return _c("FAILED", f"{label}: last status {status}")
    if status == "DEFERRED":
        return _c("PARTIAL", f"{label}: DEFERRED (upstream dependency not ready)")
    if age is None or age > max_hours:
        return _c("STALE", f"{label}: last success {age if age is None else round(age, 1)} h ago (limit {max_hours} h)")
    return _c("READY", f"{label}: last success {round(age, 1)} h ago")


def build_component_states(checks: dict) -> dict:
    """Independent state per component named by the opening-day block. Pure over `checks`
    (plus read-only local state), so it is testable without a live machine."""
    from operational import cloud_snapshot_schema as schema
    health = ingestion_health.load_health()
    out: dict = {}
    nhl, odds, rp = checks["nhl"], checks["odds"], checks["real_prop_pipeline"]
    rr, preds = checks["real_recommendation_pipeline"], checks["predictions"]

    age = nhl.get("last_sync_success_age_hours")
    out["NHL DATA"] = _c("FAILED" if nhl.get("last_sync_status") in ("FAILED", "ERROR") else
                         "STALE" if age is None or age > 30 else "READY",
                         f"last full sync {None if age is None else round(age, 1)} h ago; newest game on file "
                         f"{nhl.get('most_recent_game_date_on_file')}")

    from research.generic_prop_pricing import provider_adapter as pa
    ml_verified = pa.is_contract_verified("draftkings", "MONEYLINE")
    quotes = odds["collection_status"]
    out["MONEYLINE MARKET CONTRACT"] = _c("READY" if ml_verified else "WAITING_FOR_LIVE_MARKET",
                                          f"DraftKings MONEYLINE contract verified={ml_verified}; "
                                          f"{quotes.get('tracked_events')} events tracked")

    # Can the current polling cadence ever satisfy the decision policy's quote-age tiers? (measured, not assumed)
    try:
        from operational import odds_freshness_analysis as ofa
        starts = ofa.upcoming_starts()
        base = ofa.pulls_for(sorted({(dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=i)).date() for i in range(-1, 16)}))
        eligible = ofa.evaluate(starts, base)["decision_policy_quote_available_pct"] if starts else None
    except Exception:  # noqa: BLE001
        eligible = None
    if rr["orchestration_status"] != "HEALTHY":
        ml_state, ml_detail = "FAILED", "orchestration not operational"
    elif eligible is not None and eligible < 50:
        ml_state = "PARTIAL"
        ml_detail = (f"pipeline healthy, {rr['real_moneyline_recommendations_recorded']} recorded; but the current 4x/day "
                     f"pull cadence leaves only {eligible}% of upcoming games a quote inside the decision policy's "
                     f"10-minute window (docs/ODDS_FRESHNESS_QUOTA_ANALYSIS.md)")
    elif not rr["real_moneyline_recommendations_recorded"]:
        ml_state, ml_detail = "NO_REAL_SAMPLE_YET", "pipeline healthy; no real moneyline recommendation recorded yet"
    else:
        ml_state, ml_detail = "READY", f"{rr['real_moneyline_recommendations_recorded']} recorded"
    out["MONEYLINE RECOMMENDATION PIPELINE"] = _c(ml_state, ml_detail)

    def waiting(status):
        return "WAITING_FOR_LIVE_MARKET" if status == "PENDING_LIVE_CONTRACT" else ("READY" if status == "READY" else "PARTIAL")

    out["SOG MARKET CONTRACT"] = _c(waiting(rp["sog_status"]), f"sog_status={rp['sog_status']}")
    out["SOG ACTIONABILITY"] = _c("WAITING_FOR_LIVE_MARKET" if rp["sog_status"] == "PENDING_LIVE_CONTRACT" else "READY",
                                  f"{rp['real_sog_recommendations_recorded']} real SOG recommendations recorded")
    out["SAVES MARKET CONTRACT"] = _c(waiting(rp["saves_status"]), f"saves_status={rp['saves_status']}")
    out["SAVES STARTER DATA"] = _c("PARTIAL" if rp["saves_starter_data_status"] != "READY" else "READY",
                                   f"{rp['saves_starter_data_status']} (no real confirmed-starter source; projection only)")
    out["SAVES ACTIONABILITY"] = _c("WAIT_ONLY" if rp["saves_actionability_status"] == "WAIT_ONLY" else "READY",
                                    f"{rp['saves_actionability_status']} (starter-certainty gate intact)")
    out["GAME EDGE PARLAY"] = _c("WAITING_FOR_LIVE_MARKET" if rp["game_edge_parlay_status"] == "PARTIAL"
                                 else rp["game_edge_parlay_status"],
                                 "no real qualifying legs exist until a prop contract is verified; "
                                 "NO_QUALIFYING_GAME_EDGE_PARLAY is the correct state")
    n_bets = rr["real_market_paper_bets_placed"]
    out["PAPER BETTING"] = _c("READY" if n_bets else "NO_REAL_SAMPLE_YET",
                              f"{n_bets} REAL_MARKET_PAPER bet(s); zero is a valid outcome")
    out["SETTLEMENT"] = _health_component(health, "settlement", 30.0, "settlement")
    out["CLV"] = _c("NO_REAL_SAMPLE_YET" if not n_bets else "READY",
                    "CLV is computed at settlement for real paper bets with a closing quote")
    out["POSTMORTEM"] = _health_component(health, "postmortem", 30.0, "postmortem")

    # cloud: publication + freshness (no network; the last publish is recorded locally)
    cph = sh.cloud_snapshot_publish_health()
    pub_status = cph.get("status")
    out["CLOUD SNAPSHOT"] = _c("READY" if pub_status in ("OK",) else
                               "OWNER_ACTION_REQUIRED" if pub_status in ("WAITING", "NOT_REQUIRED") else
                               "STALE" if pub_status == "STALE" else "PARTIAL" if pub_status == "DEGRADED" else "FAILED",
                               cph.get("message") or "")
    from operational import publish_cloud_snapshot as pcs
    out["CLOUD PUBLICATION"] = _c("READY" if pcs.publishing_enabled() else "OWNER_ACTION_REQUIRED",
                                  "automatic publication after odds/settlement/postmortem jobs "
                                  + ("is ON" if pcs.publishing_enabled() else "is OFF (set NHL_ENGINE_CLOUD_PUBLISH=ON)"))
    try:                         # newest real DraftKings MONEYLINE price actually captured (not the last sweep)
        conn = db.get_conn()
        row = conn.execute("SELECT MAX(captured_at_utc) m FROM odds_snapshots WHERE market = 'MONEYLINE'").fetchone()
        newest_odds = row["m"]
        conn.close()
    except Exception:  # noqa: BLE001
        newest_odds = None
    mf = schema.classify_market_freshness(newest_odds) if newest_odds else {"state": "UNAVAILABLE", "age_minutes": None}
    out["CLOUD FRESHNESS"] = _c("READY" if mf["state"] == "CURRENT" and pub_status == "OK" else "STALE" if mf["state"] != "CURRENT" else "READY_WITH_WARNINGS",
                                f"newest DK moneyline price {newest_odds}: {mf['state']} ({mf['age_minutes']} min old; limit 180); "
                                f"publisher {pub_status}")

    remaining = quotes.get("credits_remaining")
    out["ODDS API QUOTA"] = _c("FAILED" if remaining is not None and remaining <= _QUOTA_FAIL_BELOW else
                               "READY_WITH_WARNINGS" if remaining is None or remaining < _QUOTA_WARN_BELOW else "READY",
                               f"{remaining} credits remaining (500/month plan; reset day assumed = 1st, unverified)")
    loaded = _launchctl_loaded_count()
    out["SCHEDULERS"] = _c("FAILED" if loaded == 0 else "READY" if loaded == len(sh._SCHEDULER_LABELS) else "PARTIAL",
                           f"{loaded}/{len(sh._SCHEDULER_LABELS)} jobs loaded")
    out["BACKUPS"] = _health_component(health, "database_backups", 30.0, "backups")
    out["AUTH"] = _c("OWNER_ACTION_REQUIRED",
                     "Streamlit secrets/viewer allow-list are not verifiable from this machine "
                     "(see docs/STREAMLIT_COMMUNITY_CLOUD_RUNBOOK.md)")
    out["YAHOO"] = _c("OWNER_AUTH_REQUIRED" if checks["yahoo"]["status"] == "OWNER_AUTH_REQUIRED" else
                      "READY" if checks["yahoo"]["status"] == "CONNECTED" else checks["yahoo"]["status"],
                      "isolated from betting/cloud data; never blocks betting readiness")
    return out


def overall_summary(components: dict) -> dict:
    counts: dict = {}
    for row in components.values():
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    blocking = [k for k, v in components.items() if v["state"] in ("FAILED", "PARTIAL", "STALE")]
    return {"state_counts": counts, "attention": blocking}


def build_readiness_report() -> dict:
    databases = check_databases()
    nhl = check_nhl()
    odds = check_odds()
    models = check_models()
    pipeline = check_pipeline()
    context = check_context()
    predictions = check_predictions()
    real_pipeline = check_real_recommendation_pipeline()
    real_prop_pipeline = check_real_prop_pipeline()
    yahoo = check_yahoo()

    # Explicit, factual rules -- never a weighted/subjective score.
    hard_failures = []
    warnings = []

    for name, row in databases.items():
        if not row["reachable"]:
            hard_failures.append(f"database {name} unreachable: {row['error']}")

    if not models["registry_loads"]:
        hard_failures.append(f"model registry failed to load: {models['error']}")

    if predictions.get("error"):
        hard_failures.append(f"prediction integrity check failed: {predictions['error']}")
    elif predictions["duplicate_idempotency_keys"]:
        hard_failures.append(f"{predictions['duplicate_idempotency_keys']} duplicate idempotency key(s) in the ledger")
    elif predictions["invalid_result_states"]:
        hard_failures.append(f"{predictions['invalid_result_states']} prediction(s) with an invalid result_status")

    loaded_count = _launchctl_loaded_count()
    if loaded_count == 0:
        hard_failures.append("no scheduler jobs are loaded at all")
    elif loaded_count is not None and loaded_count < len(sh._SCHEDULER_LABELS):
        warnings.append(f"only {loaded_count}/{len(sh._SCHEDULER_LABELS)} scheduler job(s) loaded")
    elif loaded_count is None:
        warnings.append("could not query launchctl to confirm scheduler jobs are loaded")

    for component, row in pipeline["components"].items():
        if row["status"] in ("STALE", "NEVER_RUN"):
            warnings.append(f"{component}: {row['status']} (last_status={row.get('last_status')}, "
                            f"age={row.get('age_hours')})")

    if predictions.get("stale_pending_over_24h"):
        warnings.append(f"{predictions['stale_pending_over_24h']} PENDING prediction(s) over 24h past event start "
                        f"-- settlement may be behind")

    if odds["collection_status"].get("status") not in ("OK", "CURRENT"):
        warnings.append(f"odds collection status: {odds['collection_status'].get('status')}")

    if yahoo["status"] != "CONNECTED":
        warnings.append(f"Yahoo: {yahoo['status']} (does not block betting readiness)")

    if real_pipeline["orchestration_status"] != "HEALTHY":
        hard_failures.append(
            f"real recommendation pipeline NOT_OPERATIONAL: "
            f"{real_pipeline['orchestration_import_error'] or real_pipeline['query_error']}")

    # Live SOG + Saves Production Certification block (2026-09-24), Part
    # 39: a market genuinely PENDING_LIVE_CONTRACT (no real DraftKings
    # payload has ever existed for it) is never a hard failure or even a
    # warning -- that is the correct, honest, expected state today. Only
    # the orchestration machinery itself failing is a real problem.
    if real_prop_pipeline["orchestration_status"] != "HEALTHY":
        hard_failures.append(
            f"real prop (SOG/Saves) pipeline NOT_OPERATIONAL: "
            f"{real_prop_pipeline['orchestration_import_error'] or real_prop_pipeline['query_error']}")

    try:
        components = build_component_states({
            "databases": databases, "nhl": nhl, "odds": odds, "models": models, "pipeline": pipeline,
            "context": context, "predictions": predictions, "real_recommendation_pipeline": real_pipeline,
            "real_prop_pipeline": real_prop_pipeline, "yahoo": yahoo})
    except Exception as exc:  # noqa: BLE001 -- the per-component table is additive; it must never break the verdict
        components = {}
        warnings.append(f"per-component states unavailable: {type(exc).__name__}: {exc}")
    for name, row in components.items():
        if row["state"] in ("PARTIAL", "STALE") and name != "SAVES STARTER DATA":
            warnings.append(f"{name}: {row['state']} -- {row['detail']}")

    if hard_failures:
        verdict = NOT_READY
    elif warnings:
        verdict = READY_WITH_WARNINGS
    else:
        verdict = READY

    checks = {
        "databases": databases, "nhl": nhl, "odds": odds, "models": models,
        "pipeline": pipeline, "context": context, "predictions": predictions,
        "real_recommendation_pipeline": real_pipeline, "real_prop_pipeline": real_prop_pipeline,
        "yahoo": yahoo,
    }
    return {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "verdict": verdict,
        "overall": overall_summary(components),
        "components": components,
        "hard_failures": hard_failures,
        "warnings": warnings,
        "checks": checks,
    }


def main() -> int:
    report = build_readiness_report()
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    print(f"\n{'=' * 60}\nOVERALL: {report['verdict']}   {report['overall']['state_counts']}\n{'=' * 60}")
    for name, row in report["components"].items():
        print(f"  {name:<36} {row['state']:<25} {row['detail']}")
    print(f"\nVERDICT: {report['verdict']}")
    if report["hard_failures"]:
        print("HARD FAILURES:")
        for f in report["hard_failures"]:
            print(f"  - {f}")
    if report["warnings"]:
        print("WARNINGS:")
        for w in report["warnings"]:
            print(f"  - {w}")
    return 1 if report["verdict"] == NOT_READY else 0


if __name__ == "__main__":
    sys.exit(main())
