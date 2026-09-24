"""
Structured per-component ingestion health (P0.1 of the 2026-09-24
hardening block). Every scheduled ingestion job (NHL sync -- full,
midday, pregame -- settlement, post-mortem) calls record_run() once
after it finishes, so the admin/data-health surfaces can show a real
last-attempt / last-success / status / data-age reading per component
instead of inferring health indirectly from unrelated caches.

Append-only in spirit but keyed (one row per component, overwritten on
each run) -- this is CURRENT STATUS, not a history log; the odds
archive / prospective ledger already own real historical record-keeping
elsewhere in this project.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_PATH = REPO_ROOT / "operational" / "ingestion_health_cache.json"

# A component's own summary dict may use "status", "SUCCESS"/"FAILED"/etc.
# under different historical field names across this project's modules
# (see docs/RELIABILITY_429_FIX.md) -- this set is what actually counts
# as "did NOT succeed" for last_success_utc purposes, matching every
# component's own real vocabulary rather than assuming one. "DEFERRED"
# (Part 24, 2026-09-24 Real Recommendation Pipeline block) is a run that
# deliberately skipped itself because an upstream dependency wasn't
# ready -- it must never be counted as a success (that would silently
# mask the very dependency gap it exists to surface).
_FAILURE_STATUSES = frozenset({"FAILED", "FAIL", "HALT", "DEFERRED"})


def _now_utc_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _load(cache_path: Path) -> dict:
    try:
        return json.loads(cache_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def record_run(component: str, summary: dict, *, cache_path: Path | None = None) -> dict:
    """Records one run's outcome for `component`. `summary` is whatever
    dict the component's own run function already returns -- this reads
    `summary.get("status")` (falling back to "UNKNOWN" if the component
    doesn't report one) rather than requiring every caller to translate
    into a single shared vocabulary. Returns the row just written."""
    cache_path = cache_path if cache_path is not None else DEFAULT_CACHE_PATH
    health = _load(cache_path)
    status = summary.get("status", "UNKNOWN")
    now = _now_utc_iso()
    row = health.get(component, {})
    row["component"] = component
    row["last_attempt_utc"] = now
    row["last_status"] = status
    row["last_detail"] = summary.get("error") or summary.get("reason")
    if status not in _FAILURE_STATUSES:
        row["last_success_utc"] = now
        row["last_success_status"] = status
    health[component] = row
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(health, indent=2, sort_keys=True))
    return row


def load_health(*, cache_path: Path | None = None) -> dict:
    cache_path = cache_path if cache_path is not None else DEFAULT_CACHE_PATH
    return _load(cache_path)


def dependency_ready(component: str, *, max_age_hours: float, cache_path: Path | None = None,
                      now: dt.datetime | None = None) -> tuple[bool, str]:
    """Real Recommendation Pipeline block (2026-09-24), Part 24: a
    dependency-awareness check for the morning workflow (07:00 sync ->
    07:15 settlement -> 07:30 postmortem -> 07:45 backup). Each of these
    was previously only clock-scheduled -- if an earlier stage failed or
    never ran, the next one would still fire and either operate on stale
    data or (for postmortem) report a misleadingly "complete" day. This
    answers one question only: is `component`'s last recorded run recent
    enough AND not itself a recorded failure -- never a retry loop, just
    a single yes/no a caller uses to decide whether to proceed or defer
    to the next scheduled run."""
    health = load_health(cache_path=cache_path)
    row = health.get(component)
    if row is None:
        return False, f"{component} has never recorded a run"
    if row.get("last_status") in _FAILURE_STATUSES:
        return False, f"{component}'s last recorded run did not succeed (status={row.get('last_status')}, " \
                       f"detail={row.get('last_detail')})"
    age = component_age_hours(row, now=now)
    if age is None:
        return False, f"{component} has never recorded a successful run"
    if age > max_age_hours:
        return False, f"{component}'s last success was {age:.1f}h ago (max allowed {max_age_hours}h)"
    return True, f"{component} last succeeded {age:.1f}h ago"


def component_age_hours(component_row: dict, *, now: dt.datetime | None = None) -> float | None:
    """Hours since last_success_utc -- None if this component has never
    once succeeded (an honest UNKNOWN age, never a fabricated 0)."""
    last_success = component_row.get("last_success_utc")
    if not last_success:
        return None
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        success_dt = dt.datetime.fromisoformat(last_success)
    except ValueError:
        return None
    return (now - success_dt).total_seconds() / 3600.0
