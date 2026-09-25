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
from operational import state_paths as _sp

STATE_PATH = _sp.path("moneyline_pregame_state.json")
LOCK_PATH = _sp.path("moneyline_pregame.lock")
AUDIT_PATH = _sp.path("moneyline_pregame_audit.json")

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


LOOKBACK_H = 12                  # missed-window detection needs clusters whose window already closed


def scheduled_starts(now: dt.datetime, hours: float = LOOKAHEAD_H, db_path: Path | None = None,
                     back_hours: float = LOOKBACK_H) -> list[dt.datetime]:
    """SCHEDULED game start times from `back_hours` ago to `hours` ahead, from the existing NHL schedule
    (read-only). The lookback only feeds MISSED_WINDOW detection; a closed window is never pulled late."""
    import db
    path = db_path or db.resolve_db_path()
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT scheduled_start_utc FROM games WHERE game_state = 'SCHEDULED' "
                            "AND scheduled_start_utc >= ? AND scheduled_start_utc <= ?",
                            ((now - dt.timedelta(hours=back_hours)).strftime("%Y-%m-%dT%H:%M"),
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
    except (OSError, ValueError, AttributeError):
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
                state_path: Path | None = None, lock_path: Path | None = None,
                audit_path: Path | None = None) -> dict:
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
            return _run_locked(now, result, starts_fn, pull_fn, guard_fn, listing_fn, state_path, audit_path)
    except Exception as exc:  # noqa: BLE001 -- a scheduler job must never crash
        result.update(status="FAILED", reason=f"{type(exc).__name__}: {str(exc)[:160]}")
        return result


def _run_locked(now, result, starts_fn, pull_fn, guard_fn, listing_fn, state_path, audit_path=None):
    starts = (starts_fn or scheduled_starts)(now)
    clusters = plan_clusters(starts)
    state = load_state(state_path)
    # heartbeat: a firing every ~2 minutes; a gap that spans a capture window means the machine (or the job) was
    # not running -- that is how MACHINE_ASLEEP is told apart from a failed request
    state["prev_heartbeat_utc"] = state.get("heartbeat_utc")
    state["heartbeat_utc"] = now.isoformat()
    state.setdefault("first_heartbeat_utc", now.isoformat())   # windows that closed before the job existed are not "missed"
    missed = detect_missed_windows(now, clusters, state, audit_path=audit_path)
    if missed:
        result["missed_windows"] = [m["cluster_id"] for m in missed]
    due = due_clusters(clusters, now, state)
    if not due:
        _save_state(state, state_path, now)
        upcoming = [c for c in clusters if c.due_window[1] >= now]
        result.update(reason="NO_CLUSTER_DUE", next_cluster=upcoming[0].key if upcoming else None)
        return result

    cluster = due[0]
    # every cluster whose capture window contains this instant is served by the same single call
    served = [c for c in clusters if c.window[0] <= now <= c.window[1]] or [cluster]
    result["cluster"] = cluster.key
    result["clusters_detail"] = [_describe(c) for c in served]

    guard = (guard_fn or _default_guard)()
    if not guard.get("allow"):
        for c in served:
            state["clusters"].setdefault(c.key, {})["last_skip"] = {"at": now.isoformat(), "reason": guard.get("reason")}
        _save_state(state, state_path, now)
        result.update(status="DEFERRED", reason=f"QUOTA_{guard.get('reason')}", guard=guard, listed=None)
        return result

    # The provider does not list every game (e.g. preseason games days out): the free events call tells us
    # whether it lists ANY game of this cluster. If not, spending a credit could only buy nothing.
    listing = (listing_fn or _default_listing)(served)
    if listing.get("listed") is False:
        for c in served:
            state["clusters"].setdefault(c.key, {}).update(status="DONE", outcome="NOT_LISTED_BY_PROVIDER",
                                                          last_attempt_utc=now.isoformat(), credits=0)
        _save_state(state, state_path, now)
        result.update(status="SKIPPED", reason="NOT_LISTED_BY_PROVIDER (no credit spent)", listed=False)
        return result

    for c in served:                       # record intent BEFORE spending: a crash mid-call still counts
        rec = state["clusters"].setdefault(c.key, {})
        rec.update(status="ATTEMPTING", attempts=int(rec.get("attempts") or 0) + 1, last_attempt_utc=now.isoformat(),
                   games=c.games)
    _save_state(state, state_path, now)

    result["listed"] = listing.get("listed")
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


# ---- classification, missed windows, audit records -----------------------------------------------------------------
# Outcomes (never one generic FAILED):
MACHINE_ASLEEP, PROVIDER_NOT_LISTED, QUOTA_DEFERRED = "MACHINE_ASLEEP", "PROVIDER_NOT_LISTED", "QUOTA_DEFERRED"
NETWORK_FAILED, API_FAILED, EMPTY_RESPONSE = "NETWORK_FAILED", "API_FAILED", "EMPTY_RESPONSE"
STORED_SUCCESSFULLY, DECISION_SUCCESS, DECISION_DATA_UNAVAILABLE = "STORED_SUCCESSFULLY", "DECISION_SUCCESS", "DECISION_DATA_UNAVAILABLE"
MISSED_WINDOW = "MISSED_WINDOW"
AUDIT_KEEP = 200
LIVE_OBSERVED, ARCHITECTURE_READY_ONLY = "LIVE_OBSERVED", "WAITING_FOR_FIRST_REAL_CLUSTER"


def _describe(c: Cluster) -> dict:
    return {"key": c.key, "smin": c.smin.isoformat(), "smax": c.smax.isoformat(), "games": c.games,
            "target_pull_utc": c.target_pull.isoformat(), "anchor_utc": c.anchor.isoformat(),
            "window": [c.window[0].isoformat(), c.window[1].isoformat()]}


def load_audit(path: Path | None = None) -> dict:
    try:
        data = json.loads((path or AUDIT_PATH).read_text())
        return data if isinstance(data.get("records"), dict) else {"records": {}}
    except (OSError, ValueError, AttributeError):
        return {"records": {}}


def _save_audit(audit: dict, path: Path | None = None) -> None:
    path = path or AUDIT_PATH
    keep = sorted(audit["records"].items(), key=lambda kv: kv[0])[-AUDIT_KEEP:]
    audit["records"] = dict(keep)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(audit, indent=2, sort_keys=True))
    tmp.replace(path)


def classify_failure(error: str | None) -> str:
    """NETWORK_FAILED for transport errors, API_FAILED for an answered-but-rejected request."""
    text = (error or "").lower()
    if "network error" in text or "timeout" in text or "connection" in text:
        return NETWORK_FAILED
    return API_FAILED


def classify_outcome(*, listed, guard_reason=None, api_error=None, pull_ran=False, rows_stored=0,
                     evaluated=0, data_unavailable=0) -> tuple[str, list[str]]:
    """(primary outcome, tags). One precise label per cluster."""
    if listed is False:
        return PROVIDER_NOT_LISTED, [PROVIDER_NOT_LISTED]
    if guard_reason:
        return QUOTA_DEFERRED, [QUOTA_DEFERRED]
    if api_error:
        c = classify_failure(api_error)
        return c, [c]
    if not pull_ran:
        return MISSED_WINDOW, [MISSED_WINDOW]
    if not rows_stored:
        return EMPTY_RESPONSE, [EMPTY_RESPONSE]
    tags = [STORED_SUCCESSFULLY]
    tags.append(DECISION_SUCCESS if evaluated else DECISION_DATA_UNAVAILABLE)
    return tags[-1], tags


def detect_missed_windows(now: dt.datetime, clusters: list[Cluster], state: dict,
                          audit_path: Path | None = None) -> list[dict]:
    """A cluster whose capture window has CLOSED without a pull is recorded exactly once as MISSED_WINDOW
    (subtype MACHINE_ASLEEP when no firing of this job fell inside the window). It is NEVER pulled late: a
    quote captured after the window cannot satisfy the decision policy, and pretending it does would break
    temporal integrity -- the decision stays DATA_UNAVAILABLE."""
    out = []
    prev = _utc(state.get("prev_heartbeat_utc"))
    first = _utc(state.get("first_heartbeat_utc"))
    for c in clusters:
        lo, hi = c.window
        if hi >= now or (first is not None and lo < first):
            continue
        rec = state["clusters"].setdefault(c.key, {})
        if rec.get("audited") or rec.get("status") == "DONE":
            continue
        rec["audited"] = True
        error = rec.get("error")
        skip = (rec.get("last_skip") or {}).get("reason")
        attempts = int(rec.get("attempts") or 0)
        if attempts and error:
            primary, tags = classify_outcome(listed=True, api_error=error)
        elif skip and not attempts:
            primary, tags = classify_outcome(listed=None, guard_reason=skip)
        else:
            primary, tags = MISSED_WINDOW, [MISSED_WINDOW]
            if prev is None or prev < lo:
                tags.append(MACHINE_ASLEEP)
        record = _base_audit(c)
        record["provider_listed"] = archived_listing(c)
        record.update(outcome=primary, tags=tags, attempts=attempts, api_status=error or ("NOT_ATTEMPTED" if not attempts else "UNKNOWN"),
                      credits_spent=0, odds_rows_stored=0, in_decision_window=False,
                      note="window closed without a valid pull; NOT pulled late (temporal integrity)",
                      updated_at=now.isoformat())
        audit = load_audit(audit_path)
        audit["records"][c.key] = record
        _save_audit(audit, audit_path)
        out.append(record)
    return out


def _base_audit(c: Cluster) -> dict:
    return {"cluster_id": c.key, "games": c.games, "scheduled_starts": sorted({c.smin.isoformat(), c.smax.isoformat()}),
            "target_pull_utc": c.target_pull.isoformat(), "decision_anchor_utc": c.anchor.isoformat(),
            "actual_pull_utc": None, "provider_listed": None, "api_status": "NOT_ATTEMPTED", "credits_spent": 0,
            "odds_rows_stored": 0, "recommendations_evaluated": 0, "bet_count": 0, "wait_count": 0, "pass_count": 0,
            "data_unavailable_count": 0, "cloud_publish": None, "outcome": None, "tags": []}


def archived_listing(c: Cluster, archive_dir: Path | None = None) -> bool | None:
    """Offline hint (NO network): does the newest ARCHIVED provider events response list a game near this
    cluster? None if there is no archive. Used for missed-window records and the pre-flight report."""
    try:
        if archive_dir is None:
            from research.live_sog_pricing import archive
            archive_dir = archive.ARCHIVE_DIR
        files = sorted(Path(archive_dir).glob("*events_na_none.json"))
        if not files:
            return None
        events = json.loads(files[-1].read_text()).get("response") or []
    except (OSError, ValueError, AttributeError):
        return None
    lo = c.smin - dt.timedelta(minutes=LISTING_TOLERANCE_MIN)
    hi = c.smax + dt.timedelta(minutes=LISTING_TOLERANCE_MIN)
    for e in events if isinstance(events, list) else []:
        t = _utc(e.get("commence_time"))
        if t and lo <= t <= hi:
            return True
    return False


def cluster_game_ids(c: Cluster, db_path: Path | None = None) -> set:
    import db
    path = db_path or db.resolve_db_path()
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT game_id FROM games WHERE scheduled_start_utc >= ? AND scheduled_start_utc <= ?",
                            (c.smin.strftime("%Y-%m-%dT%H:%M"), (c.smax + dt.timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M"))).fetchall()
    finally:
        conn.close()
    return {r[0] for r in rows}


def record_audit(result: dict, now: dt.datetime | None = None, *, game_ids_fn: Callable | None = None,
                 audit_path: Path | None = None) -> list[dict]:
    """Persist one compact audit record per served cluster after a firing that did something (called by the job
    AFTER the downstream chain and the cloud publish so those results are included). No raw payloads, no secrets.
    IDLE firings record nothing."""
    now = now or dt.datetime.now(dt.timezone.utc)
    details = result.get("clusters_detail") or []
    if not details or result.get("status") == "IDLE":
        return []
    game_ids_fn = game_ids_fn or cluster_game_ids
    orch = result.get("real_recommendation_orchestrator") or {}
    bridge = result.get("real_odds_bridge") or {}
    publish = result.get("cloud_publish") or {}
    audit = load_audit(audit_path)
    written = []
    for d in details:
        c = Cluster(_utc(d["smin"]), _utc(d["smax"]), d["games"])
        try:
            ids = game_ids_fn(c)
        except Exception:  # noqa: BLE001
            ids = set()
        rows = [r for r in (orch.get("results") or []) if r.get("game_id") in ids]
        counts = {a: sum(1 for r in rows if r.get("action") == a) for a in ("BET", "WAIT", "PASS", "DATA_UNAVAILABLE")}
        evaluated = counts["BET"] + counts["WAIT"] + counts["PASS"]
        rows_stored = int(bridge.get("rows_written") or 0) if result.get("ran") else 0
        skipped = result.get("status") == "SKIPPED"
        deferred = result.get("status") == "DEFERRED"
        primary, tags = classify_outcome(
            listed=False if skipped else result.get("listed"),
            guard_reason=(result.get("guard") or {}).get("reason") if deferred else None,
            api_error=None if result.get("ran") or skipped or deferred else (result.get("reason") or result.get("api_error") or "unknown"),
            pull_ran=bool(result.get("ran")), rows_stored=rows_stored, evaluated=evaluated,
            data_unavailable=counts["DATA_UNAVAILABLE"])
        rec = audit["records"].get(c.key) or _base_audit(c)
        captured = result.get("captured_at_utc")
        rec.update(
            provider_listed=result.get("listed"), api_status=("OK" if result.get("ran") else str(result.get("reason") or "NOT_ATTEMPTED")[:120]),
            actual_pull_utc=captured or rec.get("actual_pull_utc"),
            credits_spent=int(result.get("credits_spent_this_run") or 0) if result.get("ran") else rec.get("credits_spent", 0),
            game_ids=sorted(ids) if ids else rec.get("game_ids", []),
            odds_rows_stored=rows_stored, recommendations_evaluated=evaluated, bet_count=counts["BET"],
            wait_count=counts["WAIT"], pass_count=counts["PASS"], data_unavailable_count=counts["DATA_UNAVAILABLE"],
            in_decision_window=bool(captured) and c.window[0] <= _utc(captured) <= c.window[1],
            cloud_publish={"status": publish.get("status"), "reason": publish.get("reason"),
                           "content_hash": publish.get("content_hash")} if publish else None,
            outcome=primary, tags=tags, updated_at=now.isoformat(), attempts=(rec.get("attempts") or 0) + (1 if result.get("ran") or result.get("status") == "FAILED" else 0))
        audit["records"][c.key] = rec
        written.append(rec)
    _save_audit(audit, audit_path)
    return written


NOT_READY, ARCHITECTURE_READY, LIVE_CERTIFIED, FAILED_STATE = "NOT_READY", "ARCHITECTURE_READY", "LIVE_CERTIFIED", "FAILED"
_FAILED_OUTCOMES = (NETWORK_FAILED, API_FAILED, EMPTY_RESPONSE, MISSED_WINDOW, QUOTA_DEFERRED)


def live_observed(audit: dict | None = None) -> dict:
    """Certification state machine (exact transitions):

      NOT_READY                       prerequisites missing (decided by first_live_certification.preflight)
      ARCHITECTURE_READY              prerequisites met, but no provider-listed cluster is upcoming yet
      WAITING_FOR_FIRST_REAL_CLUSTER  prerequisites met and a real (listed) cluster is upcoming / not yet fired
      LIVE_OBSERVED                   a REAL provider-listed cluster fired and produced real provider data (a credit
                                      was spent and odds rows were stored) but not every certification gate is met
      LIVE_CERTIFIED                  one real cluster met EVERY gate: quote captured inside [T-40, T-30], expected
                                      credit spent, rows stored, a decision (BET / WAIT / PASS -- a BET is not required)
                                      evaluated at T-30, and the resulting cloud snapshot published. (The read-only
                                      certification command additionally checks the immutable observation and the
                                      paper bet rule against the ledgers.) A bare HTTP 200 never certifies.
      FAILED                          the most recent real cluster ended without provider data
                                      (network/API failure, empty response, missed window, quota deferral) and no cluster
                                      has certified; it clears when a later cluster observes or certifies.
    """
    audit = audit if audit is not None else load_audit()
    certified, observed, partial, failures = [], [], [], []
    for key, r in sorted(audit["records"].items()):
        if r.get("provider_listed") is not True:
            continue
        has_data = bool(r.get("actual_pull_utc")) and int(r.get("credits_spent") or 0) >= 1 and bool(r.get("odds_rows_stored"))
        if not has_data:
            if r.get("outcome") in _FAILED_OUTCOMES:
                failures.append({"cluster_id": key, "outcome": r.get("outcome")})
            continue
        gaps = []
        if not r.get("in_decision_window"):
            gaps.append("quote outside the decision window")
        if not r.get("recommendations_evaluated"):
            gaps.append("no decision evaluated")
        pub = (r.get("cloud_publish") or {}).get("status")
        if pub not in ("SUCCESS", "PARTIAL_SUCCESS"):
            gaps.append(f"cloud publish {pub or 'not recorded'}")
        observed.append(key)
        (partial if gaps else certified).append({"cluster_id": key, "gaps": gaps})
    base = {"architecture_ready": True, "complete_clusters": len(certified), "observed_clusters": len(observed),
            "incomplete_real_clusters": partial[-3:]}
    if certified:
        return {**base, "status": LIVE_CERTIFIED, "live_observed": True, "live_certified": True,
                "first_complete_cluster": certified[0]["cluster_id"]}
    if observed:
        return {**base, "status": LIVE_OBSERVED, "live_observed": True, "live_certified": False}
    if failures:
        return {**base, "status": FAILED_STATE, "live_observed": False, "live_certified": False, "last_failure": failures[-1]}
    return {**base, "status": ARCHITECTURE_READY_ONLY, "live_observed": False, "live_certified": False}


def last_cluster_outcome(audit: dict | None = None) -> dict | None:
    audit = audit if audit is not None else load_audit()
    if not audit["records"]:
        return None
    key = sorted(audit["records"])[-1]
    r = audit["records"][key]
    return {"cluster_id": key, "outcome": r.get("outcome"), "tags": r.get("tags"), "games": r.get("games"),
            "provider_listed": r.get("provider_listed"), "credits_spent": r.get("credits_spent"),
            "in_decision_window": r.get("in_decision_window"), "cloud_publish": (r.get("cloud_publish") or {}).get("status")}


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
