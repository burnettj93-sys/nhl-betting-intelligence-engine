"""
Targeted PREGAME moneyline pull (Quota + Moneyline Activation block, 2026-09-25).

WHY. The real moneyline pipeline prices every game at anchor = puck drop - 30 min and accepts only a
DraftKings quote captured in the 10 minutes before that anchor (config.ODDS_STALENESS_TIERS; unchanged).
Four fixed daily pulls satisfy that for ~1 % of games. This module adds ONE league-wide DraftKings
MONEYLINE pull (1 credit, the same call `--mode=moneyline` makes) per start-time cluster, timed to land
inside [start - 40 min, start - 30 min], i.e. ~T-35. Decision policy, anchor and tolerance are untouched.

HOW. `plan_clusters()` groups SCHEDULED games from the existing NHL schedule (nhl.db `games`) whose start
times are within CLUSTER_SPREAD of each other, so one call serves the whole cluster. A cluster with start
times [smin, smax] is served by any capture in [smax - 40, smin - 30]; the job is fired every couple of
minutes by launchd, does nothing (no network, no credit) unless a cluster's *due window* -- that
capture window shrunk by a safety margin -- is open, and then makes exactly one paid call.

SAFETY. Idempotent per cluster (state file written BEFORE spending; at most MAX_ATTEMPTS per cluster, a
retry only after a FAILED attempt while the window is still open), single-instance lock, quota guard
before every spend (deferred, never overspent), bounded HTTP retries live in the client. Never raises.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import sqlite3
from pathlib import Path
from typing import Callable, NamedTuple

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = REPO_ROOT / "operational" / "runtime" / "moneyline_pregame_state.json"
LOCK_PATH = REPO_ROOT / "operational" / "runtime" / "moneyline_pregame.lock"

ANCHOR_MIN = 30                  # decision anchor: puck drop - 30 min       (pricing: prediction_time_for_game)
TOLERANCE_MIN = 10               # quote must be captured <= 10 min before it (config.ODDS_STALENESS_TIERS)
CLUSTER_SPREAD_MIN = 5.0         # games this close share one call; leaves a >= 5 min capture window
MARGIN_S = 30                    # stay this far inside both edges of the capture window
MAX_ATTEMPTS = 2
LOOKAHEAD_H = 4                  # only look at games starting soon (cheap DB read)
STATE_KEEP_DAYS = 3
# True once tests/test_quota_moneyline_activation.py::TestEndToEndDryRun (quote at T-35 -> model -> pricing ->
# immutable observation -> REAL_MARKET_PAPER -> cloud section) passes; readiness reads this flag.
END_TO_END_CERTIFIED = True


class Cluster(NamedTuple):
    smin: dt.datetime
    smax: dt.datetime
    games: int

    @property
    def key(self) -> str:
        return f"{self.smin:%Y-%m-%dT%H:%M}/{self.smax:%Y-%m-%dT%H:%M}"

    @property
    def window(self) -> tuple[dt.datetime, dt.datetime]:
        """Capture window that satisfies the decision policy for EVERY game in the cluster."""
        return (self.smax - dt.timedelta(minutes=ANCHOR_MIN + TOLERANCE_MIN),
                self.smin - dt.timedelta(minutes=ANCHOR_MIN))

    @property
    def due_window(self) -> tuple[dt.datetime, dt.datetime]:
        lo, hi = self.window
        return lo + dt.timedelta(seconds=MARGIN_S), hi - dt.timedelta(seconds=MARGIN_S)

    @property
    def target_pull(self) -> dt.datetime:
        lo, hi = self.window
        return lo + (hi - lo) / 2

    @property
    def anchor(self) -> dt.datetime:
        return self.smin - dt.timedelta(minutes=ANCHOR_MIN)


def _utc(value) -> dt.datetime | None:
    from operational import cloud_snapshot_schema as schema
    return schema.parse_utc(value)


def plan_clusters(starts: list, spread_min: float = CLUSTER_SPREAD_MIN) -> list[Cluster]:
    """Group start times: a new cluster begins when a game starts more than `spread_min` after the
    cluster's first game. Deterministic, order-independent."""
    times = sorted(t for t in (_utc(s) if not isinstance(s, dt.datetime) else s for s in starts) if t is not None)
    clusters: list[Cluster] = []
    for t in times:
        if clusters and (t - clusters[-1].smin) <= dt.timedelta(minutes=spread_min):
            c = clusters[-1]
            clusters[-1] = Cluster(c.smin, max(c.smax, t), c.games + 1)
        else:
            clusters.append(Cluster(t, t, 1))
    return clusters


def covers(pull_time: dt.datetime, start: dt.datetime) -> bool:
    """Does a capture at `pull_time` satisfy the (unchanged) decision policy for a game starting `start`?"""
    anchor = start - dt.timedelta(minutes=ANCHOR_MIN)
    return anchor - dt.timedelta(minutes=TOLERANCE_MIN) <= pull_time <= anchor


def scheduled_starts(now: dt.datetime, hours: float = LOOKAHEAD_H, db_path: Path | None = None) -> list[dt.datetime]:
    """SCHEDULED game start times in the next `hours`, from the existing NHL schedule (read-only)."""
    import db
    path = db_path or db.resolve_db_path()
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT scheduled_start_utc FROM games WHERE game_state = 'SCHEDULED' "
                            "AND scheduled_start_utc >= ? AND scheduled_start_utc <= ?",
                            ((now - dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
                             (now + dt.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M"))).fetchall()
    finally:
        conn.close()
    return [t for t in (_utc(r[0]) for r in rows) if t]


# ---- state ------------------------------------------------------------------------------------------------
def load_state(path: Path | None = None) -> dict:
    path = path or STATE_PATH
    try:
        state = json.loads(path.read_text())
        return state if isinstance(state.get("clusters"), dict) else {"clusters": {}}
    except (OSError, ValueError):
        return {"clusters": {}}


def _save_state(state: dict, path: Path | None = None, now: dt.datetime | None = None) -> None:
    path = path or STATE_PATH
    now = now or dt.datetime.now(dt.timezone.utc)
    cutoff = (now - dt.timedelta(days=STATE_KEEP_DAYS)).strftime("%Y-%m-%dT%H:%M")
    state["clusters"] = {k: v for k, v in state["clusters"].items() if k.split("/")[0] >= cutoff}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.replace(path)


def due_clusters(clusters: list[Cluster], now: dt.datetime, state: dict) -> list[Cluster]:
    out = []
    for c in clusters:
        lo, hi = c.due_window
        if not (lo <= now <= hi):
            continue
        rec = state["clusters"].get(c.key) or {}
        if rec.get("status") == "DONE" or int(rec.get("attempts") or 0) >= MAX_ATTEMPTS:
            continue
        out.append(c)
    return out


# ---- runner -----------------------------------------------------------------------------------------------
def run_pregame(now: dt.datetime | None = None, *, starts_fn: Callable | None = None,
                pull_fn: Callable[[], dict] | None = None, guard_fn: Callable[[], dict] | None = None,
                listing_fn: Callable | None = None,
                state_path: Path | None = None, lock_path: Path | None = None) -> dict:
    """One firing. Returns a structured result; `ran` is True only if a paid pull was attempted and the
    API answered. Never raises."""
    now = now or dt.datetime.now(dt.timezone.utc)
    result = {"mode": "moneyline-pregame", "run_at_utc": now.isoformat(), "ran": False, "status": "IDLE",
              "reason": None, "cluster": None, "credits_spent_this_run": 0}
    try:
        lock_path = lock_path or LOCK_PATH
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                result.update(status="SKIPPED", reason="ANOTHER_INSTANCE_RUNNING")
                return result
            return _run_locked(now, result, starts_fn, pull_fn, guard_fn, listing_fn, state_path)
    except Exception as exc:  # noqa: BLE001 -- a scheduler job must never crash
        result.update(status="FAILED", reason=f"{type(exc).__name__}: {str(exc)[:160]}")
        return result


def _run_locked(now, result, starts_fn, pull_fn, guard_fn, listing_fn, state_path):
    starts = (starts_fn or scheduled_starts)(now)
    clusters = plan_clusters(starts)
    state = load_state(state_path)
    due = due_clusters(clusters, now, state)
    if not due:
        upcoming = [c for c in clusters if c.due_window[1] >= now]
        result.update(reason="NO_CLUSTER_DUE", next_cluster=upcoming[0].key if upcoming else None)
        return result

    cluster = due[0]
    # every cluster whose capture window contains this instant is served by the same single call
    served = [c for c in clusters if c.window[0] <= now <= c.window[1]] or [cluster]
    result["cluster"] = cluster.key

    guard = (guard_fn or _default_guard)()
    if not guard.get("allow"):
        for c in served:
            state["clusters"].setdefault(c.key, {})["last_skip"] = {"at": now.isoformat(), "reason": guard.get("reason")}
        _save_state(state, state_path, now)
        result.update(status="DEFERRED", reason=f"QUOTA_{guard.get('reason')}", guard=guard)
        return result

    # The provider does not list every game (e.g. preseason games days out): the free events call tells us
    # whether it lists ANY game of this cluster. If not, spending a credit could only buy nothing.
    listing = (listing_fn or _default_listing)(served)
    if listing.get("listed") is False:
        for c in served:
            state["clusters"].setdefault(c.key, {}).update(status="DONE", outcome="NOT_LISTED_BY_PROVIDER",
                                                          last_attempt_utc=now.isoformat(), credits=0)
        _save_state(state, state_path, now)
        result.update(status="SKIPPED", reason="NOT_LISTED_BY_PROVIDER (no credit spent)")
        return result

    for c in served:                       # record intent BEFORE spending: a crash mid-call still counts
        rec = state["clusters"].setdefault(c.key, {})
        rec.update(status="ATTEMPTING", attempts=int(rec.get("attempts") or 0) + 1, last_attempt_utc=now.isoformat(),
                   games=c.games)
    _save_state(state, state_path, now)

    pull = (pull_fn or _default_pull)()
    ok = bool(pull.get("ran")) and not pull.get("api_error")
    captured = pull.get("captured_at_utc")
    for c in served:
        rec = state["clusters"][c.key]
        if ok:
            in_window = bool(captured) and c.window[0] <= _utc(captured) <= c.window[1]
            rec.update(status="DONE", captured_at_utc=captured, credits=pull.get("credits_spent_this_run"),
                       in_decision_window=in_window)
        else:
            rec.update(status="FAILED", error=str(pull.get("api_error") or pull.get("reason") or "no response")[:160])
    _save_state(state, state_path, now)
    result.update(pull)
    result.update(mode="moneyline-pregame", cluster=cluster.key, served_clusters=[c.key for c in served],
                  status="SUCCESS" if ok else "FAILED", reason=None if ok else state["clusters"][cluster.key]["error"])
    result["ran"] = ok
    return result


def _default_guard() -> dict:
    from operational import odds_quota
    return odds_quota.guard(planned=1, soft_multiplier=odds_quota.PREGAME_SOFT_MULTIPLIER)


LISTING_TOLERANCE_MIN = 20         # provider commence times can differ from the NHL schedule (seen: +10 min)


def _default_listing(clusters: list[Cluster]) -> dict:
    """Free call (0 credits): does the provider list at least one event near this cluster's start time?"""
    from research.live_sog_pricing import client
    r = client.get_nhl_events()
    if not r.ok:
        return {"listed": None, "error": r.error}
    lo = min(c.smin for c in clusters) - dt.timedelta(minutes=LISTING_TOLERANCE_MIN)
    hi = max(c.smax for c in clusters) + dt.timedelta(minutes=LISTING_TOLERANCE_MIN)
    for e in r.data:
        t = _utc(e.get("commence_time"))
        if t and lo <= t <= hi:
            return {"listed": True}
    return {"listed": False}


def _default_pull() -> dict:
    from operational import live_odds_daily_pull as lop
    return lop.run_moneyline_snapshot("pregame")


# ---- diagnostics ------------------------------------------------------------------------------------------
def next_decision_cluster(now: dt.datetime | None = None, *, starts_fn: Callable | None = None,
                          state_path: Path | None = None, armed: bool | None = None,
                          quota: dict | None = None, hours: float = 36.0) -> dict:
    """Admin diagnostic: the next start-time cluster, when its T-35 pull is due, its T-30 decision anchor,
    whether the scheduler is armed and whether quota is sufficient. Read-only; no network."""
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        starts = (starts_fn or (lambda n: scheduled_starts(n, hours)))(now)
    except Exception as exc:  # noqa: BLE001
        return {"status": "UNAVAILABLE", "reason": f"{type(exc).__name__}"}
    state = load_state(state_path)
    upcoming = [c for c in plan_clusters(starts) if c.window[1] >= now
                and (state["clusters"].get(c.key) or {}).get("status") != "DONE"]
    if armed is None:
        try:
            from operational import scheduler_audit
            armed = scheduler_audit.job_loaded("com.nhlengine.moneyline-pregame")
        except Exception:  # noqa: BLE001
            armed = None
    if quota is None:
        try:
            from operational import odds_quota
            quota = odds_quota.guard(planned=1, now=now, soft_multiplier=odds_quota.PREGAME_SOFT_MULTIPLIER)
        except Exception:  # noqa: BLE001
            quota = {"allow": None, "reason": "UNKNOWN"}
    if not upcoming:
        return {"status": "NO_CLUSTER_SCHEDULED", "scheduler_armed": armed, "quota_sufficient": quota.get("allow"),
                "quota_reason": quota.get("reason")}
    c = upcoming[0]
    return {"status": "ARMED" if armed else ("SCHEDULER_NOT_LOADED" if armed is False else "UNKNOWN"),
            "next_cluster_start_utc": c.smin.isoformat(), "games_in_cluster": c.games,
            "target_pull_utc": c.target_pull.isoformat(), "decision_anchor_utc": c.anchor.isoformat(),
            "scheduler_armed": armed, "quota_sufficient": quota.get("allow"), "quota_reason": quota.get("reason")}
