"""
Process diagnostics for the ADMIN-only Diagnostics page (Community Cloud
memory sprint, 2026-09-25, Part 23). Deliberately tiny and dependency-free
(no psutil): process RSS, Python/Streamlit versions, runtime mode, and which
heavy libraries/modules are currently loaded.

Never exposes environment variables, filesystem secrets, tokens or paths --
only the fields returned below.
"""
from __future__ import annotations

import platform
import resource
import subprocess
import sys

# Top-level module prefixes whose presence in sys.modules is informative for
# memory work. "research" is counted by module, the libraries are booleans.
_LIBRARIES = ("pandas", "numpy", "altair", "pyarrow", "requests", "cryptography")


def current_rss_mb() -> float | None:
    """Resident set size of THIS process in MB, or None if unavailable."""
    try:  # Linux (Community Cloud): /proc is exact and free
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except OSError:
        pass
    try:  # macOS / other POSIX
        import os
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())],
                             capture_output=True, text=True, timeout=3).stdout.strip()
        return round(int(out) / 1024, 1)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def peak_rss_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # ru_maxrss is bytes on macOS, kilobytes on Linux.
    return round(peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024, 1)


def _snapshot_diagnostics() -> dict:
    """Snapshot source / remote fetch status / last success / generated_at / age / schema /
    content hash (Cloud live-data sprint, Part 24). Never includes a token or a URL query."""
    from dashboard import snapshot_source
    try:
        return snapshot_source.diagnostics()
    except Exception as exc:  # noqa: BLE001 -- diagnostics must never break the page
        return {"snapshot_source": "ERROR", "last_error": f"{type(exc).__name__}"}


def process_diagnostics() -> dict:
    import streamlit as st
    from operational import runtime_mode
    loaded = set(sys.modules)
    return {
        "runtime_mode": runtime_mode.current_mode(),
        "rss_mb": current_rss_mb(),
        "peak_rss_mb": peak_rss_mb(),
        "python_version": platform.python_version(),
        "streamlit_version": st.__version__,
        "libraries_loaded": {name: name in loaded for name in _LIBRARIES},
        "research_modules_loaded": sum(1 for m in loaded if m == "research" or m.startswith("research.")),
        "model_stack_loaded": "research.context_overlay.prediction_stack" in loaded,
        "snapshot": _snapshot_diagnostics(),
    }


def _t35_row(health: dict) -> dict:
    t35 = ((health.get("operations") or {}).get("moneyline_t35")) or {}
    last = t35.get("last_cluster") or {}
    state = t35.get("status") or "UNKNOWN"
    sc = t35.get("scheduler") or {}
    detail = (f"scheduler loaded={sc.get('loaded')} code {sc.get('branch')}@{sc.get('commit')} (clean master={sc.get('on_master')}); "
              f"credits {t35.get('credits_remaining')} (sufficient={t35.get('quota_sufficient')}); publisher enabled={t35.get('cloud_publisher_enabled')}; "
              f"sleep risk {t35.get('power_risk')}; "
              f"ARCHITECTURE_READY={'yes' if t35.get('architecture_ready') else 'no'}; LIVE_OBSERVED=" + ("yes" if t35.get("live_observed") else "no")
              + "; LIVE_CERTIFIED=" + ("yes" if t35.get("live_certified") else "no")
              + (f"; last cluster {last.get('cluster_id')}: {last.get('outcome')} (listed={last.get('provider_listed')}, "
                 f"credits={last.get('credits_spent')}, in window={last.get('in_decision_window')}, publish={last.get('cloud_publish')})"
                 if last else "; no cluster audited yet"))
    return {"question": "Moneyline T-35 live status", "state": state, "detail": detail}


def _next_cluster_row(health: dict) -> dict:
    info = ((health.get("operations") or {}).get("next_decision_cluster")) or {}
    if info.get("next_cluster_start_utc"):
        return {"question": "Next decision cluster", "state": info.get("status") or "UNKNOWN",
                "detail": f"{info.get('games_in_cluster')} game(s) at {info['next_cluster_start_utc']}; pull ~{info.get('target_pull_utc')}; "
                          f"decision anchor {info.get('decision_anchor_utc')}; scheduler armed={info.get('scheduler_armed')}; "
                          f"quota sufficient={info.get('quota_sufficient')}"}
    return {"question": "Next decision cluster", "state": info.get("status") or "UNKNOWN",
            "detail": "none within the lookahead" if info else "not in this snapshot"}


def owner_daily_rows(doc: dict | None, now=None) -> list[dict]:
    """The ADMIN's concise answer to "is the engine OK today?", derived ONLY from the published snapshot
    (production activation block, 2026-09-25) so it works in Community Cloud without touching any database:
    health, cloud freshness, odds freshness, sync / predictions / paper bets / settlement / postmortem,
    Odds API credits, and the list of anything not OK. Ages come from factual timestamps."""
    from operational import cloud_snapshot_schema as schema
    if not doc or doc.get("schema_version") != 2:
        return [{"question": "Snapshot available?", "state": "UNAVAILABLE",
                 "detail": "no schema-2 snapshot loaded (bundled fallback or remote failure)"}]
    meta = schema.metadata_of(doc)
    fresh = meta.get("freshness") or {}
    health = doc.get("health") or {}
    items = health.get("items") or []
    not_ok = [f"{i.get('label') or i.get('key')}: {i.get('status')}" for i in items
              if i.get("status") not in ("OK", "NOT_REQUIRED", "WAITING")]

    def daily(key: str, label: str, limit_h: float = 30.0) -> dict:
        age = schema.age_hours(fresh.get(key), now)
        if age is None:
            return {"question": label, "state": "NO", "detail": "no successful run recorded"}
        return {"question": label, "state": "YES" if age <= limit_h else "STALE",
                "detail": f"last success {age:.1f} h ago (limit {limit_h:.0f} h)"}

    mf = schema.classify_market_freshness(fresh.get("odds"), None, now)
    real = doc.get("real_recommendations") or {}
    n_ml, n_props = len(real.get("moneyline") or []), len(real.get("props") or [])
    perf = doc.get("performance") or {}
    real_track = perf.get("REAL_MARKET_PAPER") or {}
    n_bets = len(real_track.get("bets") or [])
    credits = (health.get("odds_status") or {}).get("credits_remaining")
    snap_age = schema.age_hours(meta.get("generated_at"), now)
    snap_state = schema.classify_freshness(meta.get("data_as_of"), now)
    return [
        {"question": "Is the engine healthy?", "state": "YES" if not not_ok else "ATTENTION",
         "detail": "all components OK" if not not_ok else "; ".join(not_ok[:6])},
        {"question": "Is cloud data current?", "state": snap_state,
         "detail": f"snapshot published {snap_age:.1f} h ago; data as of {meta.get('data_as_of')}" if snap_age is not None else "unknown"},
        {"question": "Are odds current?", "state": mf["state"],
         "detail": f"newest price {mf['age_minutes']} min old (limit 180 min; 90 min within 4 h of a game)"
         if mf["age_minutes"] is not None else "no price timestamp"},
        daily("nhl_data", "Did the NHL sync run?"),
        {"question": "Did predictions run?", "state": "YES" if (n_ml or n_props) else "NO_REAL_SAMPLE_YET",
         "detail": f"{n_ml} real moneyline and {n_props} real prop recommendation(s) recorded"},
        {"question": "Any paper bets?", "state": "YES" if n_bets else "NONE_YET", "detail": f"{n_bets} REAL_MARKET_PAPER bet(s); zero is a valid outcome"},
        daily("settlement", "Did settlement run?"),
        daily("postmortem", "Did the post-mortem run?"),
        {"question": "Odds API credits remaining", "state": "OK" if isinstance(credits, (int, float)) and credits >= 150
         else "LOW" if isinstance(credits, (int, float)) else "UNKNOWN", "detail": f"{credits} of 500/month"},
        _next_cluster_row(health),
        _t35_row(health),
        {"question": "Any blockers?", "state": "NONE" if not not_ok else "SEE_ABOVE",
         "detail": "no component reports a problem" if not not_ok else f"{len(not_ok)} component(s) not OK"},
    ]
