"""
Game-Day Moneyline Freshness block (2026-09-29): keeps the ORDINARY
(UI-facing) DraftKings moneyline capture safely inside this project's own
existing freshness contract (operational/cloud_snapshot_schema.py: CURRENT
<= 180 min normally, <= 90 min once a game starts within 4 h) at the
lowest possible credit cost -- without touching the T-35 decision-policy
pull, its state, or its thresholds at all.

Problem this replaces: the pre-existing `moneyline-snapshot` launchd job
fires unconditionally 4x/day (8/13/17/20 local) regardless of whether
today has a game or whether the last capture is already fresh -- spending
credits identically on a game day and a no-game day, and leaving real gaps
(the 08:00->13:00 span alone is 5 h, already past the 180-min CURRENT
limit for roughly half of it).

Design (Option B): rather than add more fixed launchd calendar slots, a
DUE-CHECK is called from the ALREADY-SCHEDULED, already-free, every-2-
minute `moneyline-pregame` firing (see live_odds_daily_pull.py's
--mode=moneyline-pregame handler) -- moneyline_pregame.py's own T-35
cluster logic is completely untouched; this is an independent,
independently-locked call made ALONGSIDE it, never inside it, and it can
never block or delay a T-35 pull. The pre-existing fixed 4x/day
`--mode=moneyline` CLI entry point is also routed through the same
due()/run_if_due() gate (see live_odds_daily_pull.py), so it now costs
0 credits on a day with no remaining unstarted games today, instead of
spending unconditionally.

Because a T-35 pull and an ordinary refresh both write the SAME
operational/moneyline_snapshot_cache.json `generated_at_utc` this due()
check reads, whichever happens first naturally resets the other's age
clock -- no special-casing is needed to avoid two back-to-back ordinary/
T-35 spends near a capture window.

Target margins (deliberately inside the real limits, not flush with
them): 150 min (30 min of margin under the 180-min CURRENT limit) once no
unstarted game remains within 4 h; 75 min (15 min of margin under the
90-min near-game limit) once one does. On a day with no unstarted games
left, due() is always False -- zero paid ordinary refreshes.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import sqlite3
from pathlib import Path
from typing import Callable

from operational import cloud_snapshot_schema as schema
from operational import state_paths as _sp

STATE_PATH = _sp.path("moneyline_freshness_state.json")
LOCK_PATH = _sp.path("moneyline_freshness.lock")

# 30 min / 15 min of margin under cloud_snapshot_schema's real CURRENT limits (180 / 90 min) -- a
# refresh scheduled to land AT the limit would already be stale by the time it's read.
FAR_TARGET_MAX_AGE_MIN = schema.MARKET_CURRENT_MAX_MINUTES - 30.0        # 150
NEAR_TARGET_MAX_AGE_MIN = schema.NEAR_GAME_CURRENT_MAX_MINUTES - 15.0    # 75
MIN_RETRY_GAP_MIN = 10.0   # belt-and-braces alongside the lock, not the primary duplicate-spend defense


def today_unstarted_game_starts(now: dt.datetime, db_path: Path | None = None) -> list[dt.datetime]:
    """Today's (viewer's LOCAL calendar date) SCHEDULED games that have not yet started -- read-only,
    free. Deliberately scoped to TODAY (not a rolling hours-ahead window): a game tomorrow should not
    make today's late-evening refreshes look "due" once tonight's own games are all underway."""
    import db
    local_now = now.astimezone()
    local_date = local_now.date()
    lo = dt.datetime.combine(local_date, dt.time.min, tzinfo=local_now.tzinfo).astimezone(dt.timezone.utc)
    hi = dt.datetime.combine(local_date, dt.time.max, tzinfo=local_now.tzinfo).astimezone(dt.timezone.utc)
    path = db_path or db.resolve_db_path()
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT scheduled_start_utc FROM games WHERE game_state = 'SCHEDULED' "
            "AND scheduled_start_utc >= ? AND scheduled_start_utc <= ?",
            (lo.strftime("%Y-%m-%dT%H:%M"), hi.strftime("%Y-%m-%dT%H:%M"))).fetchall()
    finally:
        conn.close()
    out = [t for (s,) in rows if (t := schema.parse_utc(s)) is not None and t > now]
    return sorted(out)


def target_max_age_minutes(now: dt.datetime, unstarted_starts: list[dt.datetime]) -> float | None:
    """None means "no ordinary refresh is ever needed right now" (no unstarted game left today)."""
    if not unstarted_starts:
        return None
    hours_until_soonest = (min(unstarted_starts) - now).total_seconds() / 3600.0
    return (NEAR_TARGET_MAX_AGE_MIN if hours_until_soonest <= schema.NEAR_GAME_WINDOW_HOURS
            else FAR_TARGET_MAX_AGE_MIN)


def _latest_capture_utc(cache_path: Path) -> dt.datetime | None:
    try:
        payload = json.loads(cache_path.read_text())
    except (OSError, ValueError):
        return None
    return schema.parse_utc(payload.get("generated_at_utc"))


def due(now: dt.datetime | None = None, *, unstarted_starts_fn: Callable | None = None,
        cache_path: Path | None = None) -> dict:
    """Pure read-only decision -- no network call, spends nothing. {"due": bool, "reason": str,
    "target_max_age_min": float | None, "age_min": float | None}."""
    now = now or dt.datetime.now(dt.timezone.utc)
    if cache_path is None:
        from operational import live_odds_daily_pull as lop
        cache_path = lop.MONEYLINE_CACHE_PATH
    starts = (unstarted_starts_fn or today_unstarted_game_starts)(now)
    target = target_max_age_minutes(now, starts)
    if target is None:
        return {"due": False, "reason": "NO_UNSTARTED_GAME_TODAY", "target_max_age_min": None, "age_min": None}
    last = _latest_capture_utc(cache_path)
    if last is None:
        return {"due": True, "reason": "NEVER_CAPTURED", "target_max_age_min": target, "age_min": None}
    age_min = (now - last).total_seconds() / 60.0
    is_due = age_min > target
    return {"due": is_due, "reason": "AGE_EXCEEDS_TARGET" if is_due else "WITHIN_TARGET",
            "target_max_age_min": target, "age_min": round(age_min, 1)}


def _load_state(path: Path | None) -> dict:
    try:
        return json.loads((path or STATE_PATH).read_text())
    except (OSError, ValueError):
        return {}


def _save_state(state: dict, path: Path | None) -> None:
    path = path or STATE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.replace(path)


def _default_guard(now: dt.datetime) -> dict:
    from operational import odds_quota
    return odds_quota.guard(planned=1, now=now)   # default (1.0x) soft pace -- T-35 keeps its own 3x priority


def run_if_due(now: dt.datetime | None = None, label: str | None = "ordinary_due", *,
               due_fn: Callable | None = None, pull_fn: Callable | None = None,
               guard_fn: Callable | None = None, state_path: Path | None = None,
               lock_path: Path | None = None, active_fn: Callable[[], bool] | None = None) -> dict:
    """The gated, side-effecting entry point: due-check -> quota guard -> single-instance lock -> at
    most ONE real live_odds_daily_pull.run_moneyline_snapshot() call. Never raises. Completely
    independent of moneyline_pregame.py's T-35 pull -- called ALONGSIDE it, never inside it, and never
    gates or delays it. On success, the returned dict carries the pull's own keys directly (so
    `result.get("ran")` works exactly as it does for a plain run_moneyline_snapshot() result)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    out = {"action": "NONE", "reason": None, "ran": False}
    try:
        if active_fn is None:
            from operational import deployment_mode as dm
            active_fn = dm.is_active_scheduler
        if not active_fn():
            out.update(action="SKIPPED", reason="STANDBY")
            return out
        decision = (due_fn or due)(now)
        out["due_detail"] = decision
        if not decision["due"]:
            out.update(action="NONE", reason=decision["reason"])
            return out
        guard = (guard_fn or _default_guard)(now)
        if not guard.get("allow"):
            out.update(action="DEFERRED", reason=f"QUOTA_{guard.get('reason')}", guard=guard)
            return out
        lock_path = lock_path or LOCK_PATH
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                out.update(action="SKIPPED", reason="ANOTHER_INSTANCE_RUNNING")
                return out
            state = _load_state(state_path)
            last_attempt = schema.parse_utc(state.get("last_attempt_utc"))
            if last_attempt is not None and now - last_attempt < dt.timedelta(minutes=MIN_RETRY_GAP_MIN):
                out.update(action="SKIPPED", reason="RETRY_TOO_SOON")
                return out
            state["last_attempt_utc"] = now.isoformat()
            _save_state(state, state_path)      # recorded BEFORE spending: a crash mid-call still counts
            if pull_fn is None:
                if _sp.under_test():
                    out.update(action="SKIPPED", reason="UNDER_TEST")
                    return out
                from operational import live_odds_daily_pull as lop

                def pull_fn():
                    return lop.run_moneyline_snapshot(label)
            pull_result = pull_fn()
            out.update(pull_result)
            out["action"] = "RAN"
            state["last_result"] = {"ran": pull_result.get("ran"), "api_error": pull_result.get("api_error"),
                                     "credits_spent_this_run": pull_result.get("credits_spent_this_run")}
            _save_state(state, state_path)
    except Exception as exc:  # noqa: BLE001 -- a maintenance hook must never break its host job
        out.update(action="ERROR", reason=f"{type(exc).__name__}: {str(exc)[:160]}")
    return out


def run_forced(now: dt.datetime | None = None, label: str | None = "owner_forced", **kwargs) -> dict:
    """MANUAL FORCE mode (Part A1/A3, Production Hardening block, 2026-09-29): deliberately invoked by
    the owner. Bypasses ONLY the freshness due-check -- the quota hard reserve, the single-instance
    lock, the retry-gap check, and normal store/publish behavior are all IDENTICAL to run_if_due() (same
    function, same code path, just a due_fn that always says yes). Never used by the scheduled job."""
    def _always_due(_now):
        return {"due": True, "reason": "FORCED_BY_OWNER", "target_max_age_min": None, "age_min": None}
    return run_if_due(now, label=label, due_fn=_always_due, **kwargs)


def status(now: dt.datetime | None = None) -> dict:
    """Read-only summary for System Health / readiness: the next ordinary refresh, distinguished from
    the next T-35 execution capture (Part 12's explicit instruction -- never blur the two concepts)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    starts = today_unstarted_game_starts(now)
    d = due(now, unstarted_starts_fn=lambda _n: starts)
    if d["target_max_age_min"] is None:
        next_refresh_utc = None
    elif d["due"]:
        next_refresh_utc = now
    else:
        from operational import live_odds_daily_pull as lop
        last = _latest_capture_utc(lop.MONEYLINE_CACHE_PATH)
        next_refresh_utc = (last + dt.timedelta(minutes=d["target_max_age_min"])) if last else now
    return {"due_now": d["due"], "reason": d["reason"], "target_max_age_min": d["target_max_age_min"],
            "age_min": d["age_min"], "next_ordinary_refresh_utc": next_refresh_utc.isoformat() if next_refresh_utc else None,
            "unstarted_games_today": len(starts)}
