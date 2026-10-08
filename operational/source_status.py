"""
One coherent status per data source, derived from the sources' own evidence and re-aged whenever it is looked at.

Why this exists (2026-10-08 defect): the Data Status page showed the once-a-day sync's readiness cache verbatim. That cache was written at 07:01 ET and never
again, so every badge stayed as it was at 07:01 for the rest of the day; its odds entry was computed from a retired research file last touched on
2026-08-27 (age "1000 hours"), so Odds read STALE while real quotes were minutes old.

How it works now:
  1. `build()` runs on the engine every time a snapshot is published (the trader publishes at least every ~25 minutes) and records, per source, the
     TIMESTAMPS of the evidence: data-through, last successful fetch, last attempt, the freshness policy that applies, when the next refresh is due, and
     what (if anything) is limiting it (disabled feed, permission, credit budget). It stores timestamps, never ages.
  2. `evaluate()` runs wherever the status is displayed (the hosted page included) and derives the state from those timestamps and the CURRENT time, so a
     badge ages by itself and cannot stay green after its data ages out, even if the engine stops publishing.
  3. The published document's own age is checked separately: a stale STATUS SNAPSHOT (the engine stopped publishing) is a different thing from stale DATA.

States: CURRENT (within its policy) | STALE (past its policy) | NOT_DUE (nothing needs it yet) | DISABLED (a feed that is switched off) |
BUDGET_LIMITED (stale or not refreshed because the credit allowance/reserve forbids a purchase) | BLOCKED (permission or contract) | UNAVAILABLE |
ESTIMATE (a model estimate, not a source).
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from operational import state_paths

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA = 1
PUBLISH_INTERVAL_MIN = 25.0            # the trader's heartbeat publication
SNAPSHOT_STALE_AFTER_MIN = 60.0        # past this, the status snapshot itself is stale
MONEYLINE_FAR_MIN, MONEYLINE_NEAR_MIN, NEAR_WINDOW_H = 180.0, 90.0, 4.0     # same rule as the market-freshness banner
DAILY_MIN, MONEYPUCK_MIN = 24 * 60.0, 36 * 60.0

CURRENT, STALE, NOT_DUE, DISABLED, BUDGET_LIMITED, BLOCKED, UNAVAILABLE, ESTIMATE = (
    "CURRENT", "STALE", "NOT_DUE", "DISABLED", "BUDGET_LIMITED", "BLOCKED", "UNAVAILABLE", "ESTIMATE")


def _iso(t: dt.datetime | None) -> str | None:
    return None if t is None else t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(stamp) -> dt.datetime | None:
    if not stamp:
        return None
    try:
        t = dt.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def _json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def next_local_time(now: dt.datetime, hour: int, minute: int = 0) -> dt.datetime:
    """The next occurrence of hour:minute US/Eastern after `now` (the daily jobs' local schedule), in UTC."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("America/New_York")
    local = now.astimezone(tz)
    cand = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if cand <= local:
        cand += dt.timedelta(days=1)
    return cand.astimezone(dt.timezone.utc)


def moneyline_limit_min(now: dt.datetime, starts: list[dt.datetime]) -> float:
    near = any(0 < (s - now).total_seconds() <= NEAR_WINDOW_H * 3600 for s in starts)
    return MONEYLINE_NEAR_MIN if near else MONEYLINE_FAR_MIN


# ------------------------------------------------------------------ build (engine side, at publication) ----

def _src(key, label, *, basis, data_through=None, last_success=None, last_attempt=None, max_age_min=None, next_refresh=None, next_note=None,
         fixed=None, limiter=None, reason="", detail=None, **extra) -> dict:
    return {"key": key, "label": label, "age_basis_utc": _iso(basis), "data_through": data_through, "last_success_utc": _iso(last_success),
            "last_attempt_utc": _iso(last_attempt), "max_age_min": max_age_min, "next_refresh_utc": _iso(next_refresh), "next_refresh_note": next_note,
            "fixed_state": fixed, "limiter": limiter, "reason": reason, "detail": detail or {}, **extra}


def build(now: dt.datetime | None = None, *, nhl=None, health: dict | None = None, readiness: dict | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    sources: list[dict] = []
    health = health if health is not None else (_json(REPO_ROOT / "operational" / "ingestion_health_cache.json") or {})
    readiness = readiness if readiness is not None else (_json(REPO_ROOT / "operational" / "data_readiness_cache.json") or {})
    own = nhl is None
    if own:
        import db
        try:
            nhl = db.get_conn()
        except Exception:  # noqa: BLE001
            nhl = None
    try:
        starts = _unstarted_starts(nhl, now)
        sources += _nhl_sources(nhl, now, health, readiness, starts)
        sources += _moneypuck_sources(now, health)
        sources += _odds_sources(now, starts)
        sources += _feed_sources(nhl, now)
    finally:
        if own and nhl is not None:
            nhl.close()
    return {"schema": SCHEMA, "generated_at_utc": _iso(now), "publish_interval_min": PUBLISH_INTERVAL_MIN,
            "snapshot_stale_after_min": SNAPSHOT_STALE_AFTER_MIN, "sources": sources}


def _unstarted_starts(nhl, now: dt.datetime) -> list[dt.datetime]:
    if nhl is None:
        return []
    rows = nhl.execute("SELECT scheduled_start_utc FROM games WHERE game_state = 'SCHEDULED' AND scheduled_start_utc >= ? ORDER BY scheduled_start_utc LIMIT 60",
                       (now.strftime("%Y-%m-%dT%H:%M:%S"),)).fetchall()
    return [t for t in (parse(r[0]) for r in rows) if t]


def _nhl_sources(nhl, now, health, readiness, starts) -> list[dict]:
    comps = [health.get(k) or {} for k in ("nhl_sync_full", "nhl_midday_schedule_refresh", "nhl_pregame_targeted_refresh")]
    succ = [parse(c.get("last_success_utc")) for c in comps if c.get("last_success_utc")]
    att = [parse(c.get("last_attempt_utc")) for c in comps if c.get("last_attempt_utc")]
    last_success, last_attempt = (max(succ) if succ else None), (max(att) if att else None)
    failing = [c for c in comps if c.get("last_status") in ("FAILED", "FAIL", "HALT")]
    soon = [s for s in starts if (s - now).total_seconds() <= 6 * 3600]
    nxt = (last_attempt + dt.timedelta(minutes=30)) if (soon and last_attempt) else next_local_time(now, 7)
    nxt_note = "every 30 minutes while games are within 6 hours; otherwise the daily 07:00 ET sync"
    window_end = (readiness.get("nhl_sync") or {}).get("window_end")
    results_through = games_final = overdue = None
    if nhl is not None:
        row = nhl.execute("SELECT max(game_date), max(result_observed_at_utc) FROM games WHERE game_state = 'FINAL'").fetchone()
        results_through, games_final = row[0], row[1]
        overdue = nhl.execute("SELECT count(*) FROM games WHERE game_state IN ('SCHEDULED','LIVE') AND scheduled_start_utc < ?",
                              ((now - dt.timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%S"),)).fetchone()[0]
    return [
        _src("nhl_schedule", "NHL schedule", basis=last_success, data_through=f"schedule through {window_end}" if window_end else None, last_success=last_success,
             last_attempt=last_attempt, max_age_min=DAILY_MIN, next_refresh=nxt, next_note=nxt_note,
             reason="the last schedule sync failed" if failing else "", detail={"components": {k: (health.get(k) or {}).get("last_status") for k in
                                                                                          ("nhl_sync_full", "nhl_midday_schedule_refresh", "nhl_pregame_targeted_refresh")}}),
        _src("nhl_results", "NHL results", basis=last_success, data_through=f"results through {results_through}" if results_through else None,
             last_success=last_success, last_attempt=last_attempt, max_age_min=DAILY_MIN, next_refresh=nxt, next_note=nxt_note,
             reason=(f"{overdue} game(s) started more than 5 hours ago are not yet final" if overdue else ""),
             detail={"latest_result_observed_at_utc": games_final, "games_started_over_5h_ago_not_final": overdue}),
    ]


def _moneypuck_sources(now, health) -> list[dict]:
    from operational import moneypuck_daily as mpd
    pstate = _json(REPO_ROOT / "operational" / "runtime" / "product_state.json") or {}
    through = pstate.get("data_through") or {}
    nxt = next_local_time(now, 7)
    attempt = parse((health.get("nhl_sync_full") or {}).get("last_attempt_utc"))
    out = []
    for dataset, label, thr in (("team", "MoneyPuck team", None), ("skater", "MoneyPuck skater logs", through.get("skaters")), ("goalie", "MoneyPuck goalie logs", through.get("goalies"))):
        try:
            m = mpd.load_manifest(dataset, 2026)
        except Exception:  # noqa: BLE001
            m = None
        if m is None:
            out.append(_src(f"moneypuck_{dataset}", label, basis=None, fixed=UNAVAILABLE, reason="no accepted snapshot exists for this season yet", next_refresh=nxt,
                            next_note="daily check at 07:00 ET"))
            continue
        acc = parse(m.get("latest_accepted_at_utc"))
        out.append(_src(f"moneypuck_{dataset}", label, basis=acc, data_through=(f"games through {thr}" if thr else None), last_success=acc, last_attempt=attempt,
                        max_age_min=MONEYPUCK_MIN, next_refresh=nxt, next_note="daily check at 07:00 ET (MoneyPuck publishes with a lag)"))
    return out


def _odds_sources(now, starts) -> list[dict]:
    from operational import credit_allocation, credit_planner as cp, eastern_time as et, odds_quota
    out = []
    ml = _json(REPO_ROOT / "operational" / "moneyline_snapshot_cache.json") or {}
    rows = ml.get("rows") or []
    quote_times = [t for t in (parse(r.get("quote_updated_at_utc")) for r in rows) if t]
    fetched = parse(ml.get("generated_at_utc"))
    through = max(quote_times) if quote_times else None
    remaining = odds_quota.latest_remaining()
    day = et.eastern_today(now)
    plan = cp.load_plan(day)
    ui = cp.authorize(cp.MONEYLINE_UI, 1, now, remaining=remaining, plan=plan)
    next_cluster = None
    try:
        from operational import moneyline_pregame
        nc = moneyline_pregame.next_decision_cluster(now)
        next_cluster = parse(nc.get("target_pull_utc"))
    except Exception:  # noqa: BLE001
        pass
    limit = moneyline_limit_min(now, starts)
    cands = [t for t in (next_cluster,) if t and t > now]
    limiter = None if ui.get("allow") else "BUDGET"
    reason = ""
    if limiter:
        reason = (f"the display refresh is not allowed now ({ui.get('reason', '').replace('_', ' ').lower()}); the next decision pull "
                  + (f"is due {_iso(next_cluster)}" if next_cluster else "is not scheduled"))
    out.append(_src("odds_moneyline", "Moneyline prices (DraftKings)", basis=through, data_through=(f"newest quote {_iso(through)}" if through else None),
                    last_success=fetched, last_attempt=fetched, max_age_min=limit, next_refresh=(min(cands) if cands else None),
                    next_note="next decision pull ~35 minutes before the next puck drop; a display refresh when the price nears its freshness limit",
                    limiter=limiter, reason=reason, near_game=limit == MONEYLINE_NEAR_MIN, game_starts=[_iso(s) for s in starts[:12]],
                    detail={"quotes": len(rows), "freshness_limit_min_far": MONEYLINE_FAR_MIN, "freshness_limit_min_near": MONEYLINE_NEAR_MIN}))
    # player props: one capture per planned game in its actionable window
    calls = credit_allocation.read_calls(since=now - dt.timedelta(days=1))
    props = [r for r in calls if r["class"] == "event_props"]
    last_props = max((r["at"] for r in props), default=None)
    priced = list((plan or {}).get("games_priced") or [])
    captured_events_today = {r.get("game_id") for r in cp.read_ledger(day) if r.get("class") == cp.PROPS}
    games_by_id = {}
    pending = []
    if priced:
        import db
        try:
            conn = db.get_conn()
            for gid in priced:
                g = conn.execute("SELECT scheduled_start_utc FROM games WHERE game_id = ?", (gid,)).fetchone()
                if g:
                    games_by_id[gid] = parse(g[0])
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        for gid, t in games_by_id.items():
            if t and t > now and gid not in captured_events_today:
                pending.append(t - dt.timedelta(hours=cp.FIRST_CAPTURE_HOURS))
    due_now = [w for w in pending if w <= now]
    future = [w for w in pending if w > now]
    n = (plan or {}).get("games_today", 0)
    if not plan or not priced:
        state_fixed, why = NOT_DUE, "no games are planned for pricing yet today"
    elif due_now:
        state_fixed, why = None, f"{len(due_now)} planned game(s) are inside their capture window and have no capture yet"
    elif future:
        state_fixed, why = NOT_DUE, f"{len(priced)} of {n} games are planned for pricing; the next capture window opens {_iso(min(future))}"
    else:
        state_fixed, why = NOT_DUE, f"all {len(priced)} planned game(s) are captured or started; {n - len(priced)} of {n} games are not priced under the credit plan"
    prop_limit = None
    if remaining is not None and remaining - 1 < odds_quota.RESERVE:
        prop_limit = "BUDGET"
    out.append(_src("odds_props", "Player prop prices (shots, points, goals)", basis=last_props, data_through=(f"newest capture {_iso(last_props)}" if last_props else None),
                    last_success=last_props, last_attempt=last_props, max_age_min=None if state_fixed else 30.0, next_refresh=(min(future) if future else None),
                    next_note="one capture per planned game, about 105 minutes before puck drop", fixed=state_fixed, limiter=prop_limit, reason=why,
                    detail={"games_today": n, "games_priced": len(priced), "captured_today": len(captured_events_today)}))
    # credits
    headers = [r for r in credit_allocation.read_calls(since=now - dt.timedelta(days=2)) if r.get("remaining") is not None]
    last_hdr = max((r["at"] for r in headers), default=None)
    usable = None if remaining is None else max(remaining - odds_quota.RESERVE, 0)
    exhausted = usable is not None and usable <= 0
    out.append(_src("odds_credits", "Odds API credits", basis=last_hdr, data_through=(f"{remaining} remaining, {usable} usable" if remaining is not None else None),
                    last_success=last_hdr, max_age_min=DAILY_MIN, fixed=BUDGET_LIMITED if exhausted else None, limiter="BUDGET" if exhausted else None,
                    reason="no credits are left above the reserve" if exhausted else "",
                    next_refresh=None, next_note="the counter is read from every paid response and resets on the first of the month (UTC), observed once on 2026-10-01 00:04Z",
                    detail={"remaining": remaining, "reserve": odds_quota.RESERVE}))
    return out


def _feed_sources(nhl, now) -> list[dict]:
    from operational import dailyfaceoff
    st = dailyfaceoff.load_state()
    on = dailyfaceoff.enabled()
    manual = 0
    if nhl is not None:
        manual = nhl.execute("SELECT count(*) FROM goalie_status_events WHERE source LIKE 'manual:%' AND observed_at_utc >= ?",
                             ((now - dt.timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%S"),)).fetchone()[0]
    why_off = ("switched off: the site's network terms restrict automated access, and no licence or permission has been established "
               "(docs/STARTING_GOALIE_SOURCE_AUDIT.md)")
    goalies_at = parse((st.get("goalies") or {}).get("fetched_at_utc"))
    lines_at = parse((st.get("lines") or {}).get("fetched_at_utc"))
    return [
        _src("starter_confirmations", "Starting-goalie confirmations", basis=goalies_at if on else None, data_through=f"{manual} recorded by hand in the last 30 hours",
             last_success=goalies_at, max_age_min=dailyfaceoff.FEED_MAX_AGE_MIN, fixed=None if on else DISABLED, limiter=None if on else "PERMISSION",
             reason="" if on else why_off + "; confirmations can still be recorded by hand", next_note="every 20 minutes when enabled",
             detail={"automated_source_status": st.get("status"), "manual_recent": manual}),
        _src("reported_lineups", "Reported lines, power-play units and injuries", basis=lines_at if on else None, last_success=lines_at,
             max_age_min=dailyfaceoff.LINEUP_MAX_AGE_H * 60, fixed=None if on else DISABLED, limiter=None if on else "PERMISSION",
             reason="" if on else why_off + "; Players shows only estimated usage until a permitted source exists", next_note="every 3 hours when enabled"),
        _src("starter_estimates", "Start-chance estimates (model)", basis=None, fixed=ESTIMATE,
             reason="an estimate from recent usage and rest, never a confirmation"),
    ]


# ------------------------------------------------------------------ evaluate (view side, at the current time) ----

def evaluate(doc: dict, now: dt.datetime | None = None) -> dict:
    """Rows for display, with ages and states derived from the stored timestamps and `now`. Pure."""
    now = now or dt.datetime.now(dt.timezone.utc)
    gen = parse(doc.get("generated_at_utc"))
    snap_age = None if gen is None else (now - gen).total_seconds() / 60.0
    snap_stale = snap_age is None or snap_age > doc.get("snapshot_stale_after_min", SNAPSHOT_STALE_AFTER_MIN)
    rows = []
    for s in doc.get("sources", []):
        basis = parse(s.get("age_basis_utc"))
        limit = s.get("max_age_min")
        if s.get("key") == "odds_moneyline":                       # the policy depends on how close the next game is NOW
            limit = moneyline_limit_min(now, [t for t in (parse(x) for x in s.get("game_starts", [])) if t])
        age = None if basis is None else (now - basis).total_seconds() / 60.0
        fixed = s.get("fixed_state")
        nxt = parse(s.get("next_refresh_utc"))
        overdue = nxt is not None and nxt < now
        if fixed:
            state, why = fixed, s.get("reason", "")
        elif age is None:
            state, why = UNAVAILABLE, s.get("reason") or "no evidence of a successful fetch"
        elif limit is not None and age > limit:
            if s.get("limiter") == "BUDGET":
                state, why = BUDGET_LIMITED, s.get("reason") or "past its freshness limit and the credit allowance does not allow a refresh now"
            else:
                state, why = STALE, f"{_fmt_age(age)} old; the limit is {_fmt_age(limit)}" + ("; its refresh is overdue" if overdue else "")
        else:
            state, why = CURRENT, s.get("reason", "")
        rows.append({"key": s["key"], "label": s["label"], "state": state, "reason": why, "age_min": age, "limit_min": limit, "data_through": s.get("data_through"),
                     "last_success_utc": s.get("last_success_utc"), "last_attempt_utc": s.get("last_attempt_utc"),
                     "next_refresh_utc": s.get("next_refresh_utc"), "next_refresh_note": s.get("next_refresh_note"), "next_overdue": overdue,
                     "limiter": s.get("limiter"), "detail": s.get("detail")})
    return {"as_of_utc": _iso(now), "snapshot_generated_at_utc": doc.get("generated_at_utc"), "snapshot_age_min": snap_age, "snapshot_stale": snap_stale, "rows": rows}


def _fmt_age(minutes: float | None) -> str:
    if minutes is None:
        return "unknown"
    return f"{minutes:.0f} min" if minutes < 120 else f"{minutes / 60:.1f} h"
