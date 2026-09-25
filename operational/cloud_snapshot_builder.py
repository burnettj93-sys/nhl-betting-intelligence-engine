"""
Builds the compact "current" Cloud snapshot from the LOCAL engine's real
operational state (Cloud live-data sprint, 2026-09-25).

STRICTLY READ-ONLY: every database is opened `mode=ro` (or replaced by an empty
in-memory schema when absent); nothing here creates a bet, an observation, a
file in the operational databases, or makes a network call. It never runs a
model -- it only READS what the engine already produced.

Each section is built independently: if one fails it is omitted and reported in
`section_errors`, the rest are still built (the publisher then reports
PARTIAL_SUCCESS). Only shared betting data is emitted: no user data, no
Yahoo content (excluded structurally by cloud_snapshot_schema.validate_snapshot),
no credentials, no absolute paths (sanitized).

The deterministic `demo` section is copied from the git-bundled board.json
(dashboard/cloud_snapshot/board.json), which tests prove equals a live
recompute -- so a publication never has to construct the ~430 MB model stack.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import subprocess
from pathlib import Path

from operational import cloud_snapshot_schema as schema

REPO_ROOT = Path(__file__).resolve().parent.parent
BUNDLED_PATH = REPO_ROOT / "dashboard" / "cloud_snapshot" / "board.json"

MAX_BETS_PER_TRACK = 200
MAX_LEDGER_ROWS_PER_TYPE = 100
MAX_REAL_REC_ROWS = 300

_MONEYLINE_REC_FIELDS = (
    "game_id", "game_date", "event_start_utc", "team", "opponent", "market_id", "market_family", "side",
    "prospective_status", "confidence", "raw_probability", "conservative_probability",
    "market_no_vig_probability", "odds_american", "sportsbook", "created_at_utc", "prediction_checkpoint",
    "odds_captured_at_utc", "source", "is_demo", "has_real_paper_bet", "player_id", "player_name_snapshot", "threshold",
)
_LEDGER_ROW_FIELDS = (
    "prediction_id", "record_type", "game_id", "game_date", "event_start_utc", "team", "opponent",
    "market_id", "market_family", "side", "threshold", "player_id", "player_name_snapshot",
    "raw_probability", "conservative_probability", "market_no_vig_probability", "odds_american",
    "prospective_status", "result_status", "actual_outcome", "profit_loss", "created_at_utc",
    "prediction_checkpoint", "sportsbook",
)


def _pick(row: dict, fields: tuple[str, ...]) -> dict:
    return {k: row.get(k) for k in fields if k in row}


def _ro(path: Path) -> sqlite3.Connection | None:
    if not Path(path).exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _ledger_conn() -> sqlite3.Connection:
    from operational import prospective_ledger as pl
    conn = _ro(pl.DB_PATH)
    if conn is not None:
        return conn
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    with open(pl.SCHEMA_PATH) as f:
        conn.executescript(f.read())
    return conn


def _bankroll_conn() -> sqlite3.Connection:
    from operational import paper_bankroll as pb
    conn = _ro(pb.DB_PATH)
    return conn if conn is not None else pb.init_db(Path(":memory:"))


def _git_head() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short=12", "HEAD"], cwd=REPO_ROOT, capture_output=True,
                             text=True, timeout=10)
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


# ---- sections -------------------------------------------------------------------------------------
def _demo_section() -> dict:
    """The deterministic demo board. Every recommendation row in it is stamped with the canonical
    demo provenance (`source` / `is_demo`) if it does not already carry one, so a consumer can
    never mistake a simulated row for a real one."""
    demo = json.loads(BUNDLED_PATH.read_text())["demo"]
    for key in ("opportunities", "prop_opportunities", "goalie_saves_opportunities"):
        for row in demo.get(key, []):
            row.setdefault("source", schema.PROVENANCE_DEMO)
            row.setdefault("is_demo", True)
    demo["provenance"] = schema.PROVENANCE_DEMO
    return demo


def _live_rows() -> list[dict]:
    from dashboard import live_dk as ldk
    return ldk.build_live_moneyline_comparisons()


def _real_recommendations() -> dict:
    from dashboard import real_recommendations_view as rrv
    from operational import paper_bankroll as pb
    pl_conn, pb_conn = _ledger_conn(), _bankroll_conn()
    try:
        ml = rrv.real_moneyline_recommendations(pl_conn=pl_conn, bankroll_conn=pb_conn)
        ml = ml[-MAX_REAL_REC_ROWS:]
        props = rrv.real_prop_recommendations_for_conviction_and_parlay(pl_conn=pl_conn)[-MAX_REAL_REC_ROWS:]
        parlays = _real_parlays(props)
    finally:
        pl_conn.close()
        pb_conn.close()
    return {
        "provenance": schema.PROVENANCE_REAL_MARKET,
        "moneyline": [_pick(r, _MONEYLINE_REC_FIELDS) for r in ml],
        "props": props,
        "game_edge_parlays": parlays,
    }


def _real_parlays(props: list[dict]) -> list[dict]:
    """Game Edge Parlays over REAL prop recommendations only (empty until a
    prop contract is verified and a BET-grade real leg exists)."""
    import dataclasses
    from research.game_edge_parlay import engine as gep
    teams = {(o["team"], o["opponent"]) for o in props if o.get("team") and o.get("opponent")}
    out = []
    for team, opponent in sorted(teams):
        result = gep.build_game_edge_parlay(props, team, opponent)
        if result["status"] != "QUALIFIED":
            continue
        combo = result["combo"]
        out.append({"team": team, "opponent": opponent, "recommended_legs": result["recommended_legs"],
                    "combo": dataclasses.asdict(combo), "provenance": schema.PROVENANCE_REAL_MARKET})
    return out


def _performance() -> dict:
    from dashboard import paper_performance_view as ppv
    conn = _bankroll_conn()
    try:
        return ppv.read_dashboard_state(conn, max_bets=MAX_BETS_PER_TRACK)
    finally:
        conn.close()


def _morning_review() -> dict:
    from operational import daily_postmortem as dpm
    conn = _bankroll_conn()
    try:
        return dpm.run_daily_postmortem(conn)
    finally:
        conn.close()


def _model_learning() -> dict:
    from operational import daily_model_review as dmr
    conn = _ledger_conn()
    try:
        return dmr.run_daily_review(conn)
    finally:
        conn.close()


def _ledger() -> dict:
    from operational import prospective_ledger as pl
    conn = _ledger_conn()
    try:
        rows_by_type = {}
        for record_type in pl.RECORD_TYPES:
            rows = pl.query_observations(conn, record_type=record_type)
            rows_by_type[record_type] = [_pick(dict(r), _LEDGER_ROW_FIELDS) for r in rows[-MAX_LEDGER_ROWS_PER_TYPE:]]
        return {"summary": pl.summary_metrics(conn), "operational": pl.operational_summary(conn),
                "rows": rows_by_type, "row_cap_per_type": MAX_LEDGER_ROWS_PER_TYPE,
                "shadow_cohorts": {prop: pl.raw_vs_adjusted_summary(conn, prop) for prop in ("GOALS", "POINTS")}}
    finally:
        conn.close()


def _data_status() -> dict:
    from dashboard import data_status_view as dv
    from operational import ingestion_health
    cache = dv.load_readiness_cache()
    return {"readiness_cache": cache, "ingestion_health": ingestion_health.load_health()}


def _health() -> dict:
    from operational import system_health as sh
    items = []
    for key, item in sh.build_system_health().items():
        entry = {"key": key, "label": item.get("label"), "status": item.get("status")}
        if item.get("status") not in ("OK", "NOT_REQUIRED"):
            entry["message"] = item.get("message")   # volatile ages only appear on non-OK items
        items.append(entry)
    summary = {k: {"status": v.get("status")} for k, v in sh.production_health_summary(include_yahoo=False).items()}
    # `last_updated_utc` is the time of the latest 15-minute prop sweep -- it changes on every firing and says
    # nothing about market data, so it must not make an otherwise identical snapshot look "changed".
    odds_status = {k: v for k, v in sh.odds_collection_status().items() if k != "last_updated_utc"}
    ops: dict = {}
    try:                                     # ADMIN diagnostics: what the next decision cluster needs (read-only)
        from operational import moneyline_pregame, prop_discovery
        ops["next_decision_cluster"] = moneyline_pregame.next_decision_cluster()
        obs = moneyline_pregame.live_observed()
        from operational import first_live_certification as flc
        pre = flc.preflight()
        ops["moneyline_t35"] = {"architecture_ready": pre["architecture_ready"], "live_observed": obs["live_observed"],
                                "live_certified": obs.get("live_certified", False), "status": pre["state"],
                                "last_cluster": moneyline_pregame.last_cluster_outcome(),
                                "scheduler": {k: pre["scheduler"].get(k) for k in ("loaded", "branch", "commit", "on_master")},
                                "credits_remaining": pre["quota"].get("credits_remaining"), "quota_sufficient": pre["quota"].get("sufficient"),
                                "cloud_publisher_enabled": pre["cloud_publisher_enabled"], "power_risk": pre["power_risk"],
                                "preflight": pre.get("preflight_verdict"),
                                "wake": {k: (pre.get("wake") or {}).get(k) for k in ("state", "when_utc")}}
        ps = prop_discovery.status()
        ops["prop_discovery"] = {"mode": ps["mode"], "market_states": ps["market_states"], "daily_budget": ps["daily_budget"]}
    except Exception as exc:  # noqa: BLE001 -- diagnostics must never fail the snapshot
        ops["error"] = type(exc).__name__
    return {"items": items, "production": summary, "odds_status": odds_status, "operations": ops}


_SECTION_BUILDERS = {
    "live_moneyline_rows": _live_rows,
    "real_recommendations": _real_recommendations,
    "performance": _performance,
    "morning_review": _morning_review,
    "model_learning": _model_learning,
    "ledger": _ledger,
    "data_status": _data_status,
    "health": _health,
}


def _max_ts(values) -> str | None:
    parsed = [(schema.parse_utc(v), v) for v in values if v]
    parsed = [(p, v) for p, v in parsed if p is not None]
    return max(parsed, key=lambda pv: pv[0])[1] if parsed else None


def _freshness(sections: dict) -> dict:
    from operational import ingestion_health
    health = ingestion_health.load_health()

    def last_success(component):
        return (health.get(component) or {}).get("last_success_utc")

    rows = sections.get("live_moneyline_rows") or []
    real = sections.get("real_recommendations") or {}
    rec_times = [r.get("created_at_utc") for r in (real.get("moneyline") or [])] + \
                [r.get("created_at_utc") for r in (real.get("props") or [])]
    return {
        "nhl_data": last_success("nhl_sync_full"),
        "odds": _max_ts(r.get("captured_at_utc") for r in rows),
        "recommendations": _max_ts(rec_times),
        "settlement": last_success("settlement"),
        "postmortem": last_success("postmortem"),
    }


def _sanitize_strings(node, repo_root: str):
    if isinstance(node, dict):
        return {k: _sanitize_strings(v, repo_root) for k, v in node.items()}
    if isinstance(node, list):
        return [_sanitize_strings(v, repo_root) for v in node]
    if isinstance(node, str):
        return schema.sanitize_text(node, repo_root)
    return node


def build_live_snapshot(now: dt.datetime | None = None, *, sections: tuple[str, ...] | None = None
                        ) -> tuple[dict, dict[str, str]]:
    """Returns (snapshot document, {section: error} for sections that failed).
    `sections` restricts the optional sections built (tests); default builds all."""
    from operational import deployment_mode, runtime_mode
    now = now or dt.datetime.now(dt.timezone.utc)
    errors: dict[str, str] = {}
    built: dict = {}
    for name, fn in _SECTION_BUILDERS.items():
        if sections is not None and name not in sections:
            continue
        try:
            built[name] = fn()
        except Exception as exc:  # noqa: BLE001 -- section isolation is the point
            errors[name] = f"{type(exc).__name__}: {schema.sanitize_text(str(exc), str(REPO_ROOT))[:200]}"
    fresh = _freshness(built)
    data_as_of = _max_ts([fresh["odds"], fresh["recommendations"]]) or fresh["nhl_data"]
    doc = {
        "schema_version": schema.SCHEMA_VERSION,
        "metadata": {
            "schema_version": schema.SCHEMA_VERSION,
            "generated_at": now.isoformat(),
            "generated_by": "operational.publish_cloud_snapshot",
            "source_master_commit": _git_head(),
            "engine_mode": f"{runtime_mode.current_mode()}/{deployment_mode.current_mode()}",
            "data_as_of": data_as_of,
            "freshness": fresh,
            "simulated_slate_date": json.loads(BUNDLED_PATH.read_text())["meta"].get("simulated_slate_date"),
            "provenance": {"demo": schema.PROVENANCE_DEMO, "live_moneyline_rows": schema.PROVENANCE_LIVE,
                           "real_recommendations": schema.PROVENANCE_REAL_MARKET},
            "sections_omitted": sorted(errors),
        },
        "demo": _demo_section(),
        **built,
    }
    doc = _sanitize_strings(doc, str(REPO_ROOT))
    doc = json.loads(schema.strict_dumps(doc))       # strict round trip: NaN/Infinity/non-serializable raise
    return doc, errors
