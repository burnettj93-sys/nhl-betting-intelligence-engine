"""
The day's capture timeline as one published document: what ran, what is due, what was captured, what the budget excluded, and why. Read-only; spends nothing.

It answers, for the Eastern day: did the 07:45 refresh run; was the whole-slate analysis published after 08:00; which league-wide pulls were made (and what they cost against what was planned);
and, for every start-time wave, its target, deadline and state, which games were selected, which markets each got and posted, which games the budget excluded, and any missed window with its evidenced cause.
The budget block states the actual remaining credits, the reset-date assumption and the planned cost of a league pull (observed or assumed).
"""
from __future__ import annotations

import datetime as dt
import json

from operational import state_paths

NAME = "day_status.json"

PENDING, DUE, ON_TIME, LATE, PARTIAL, MISSED, NONE_SELECTED, CLOSED = "PENDING", "DUE", "CAPTURED_ON_TIME", "CAPTURED_LATE", "PARTIAL", "MISSED", "NONE_SELECTED", "CLOSED"
CAPTURED_EARLIER = "CAPTURED_EARLIER"       # priced today under an earlier version of the plan (the plan was rebuilt): the ledger shows the credits, the wave state does not


def _utc(v):
    if not v:
        return None
    t = dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def _read(name: str):
    try:
        return json.loads(state_paths.path(name).read_text())
    except (OSError, json.JSONDecodeError):
        return None


def build(now: dt.datetime) -> dict:
    from operational import capture_timeline as ct, credit_planner as cp, eastern_time as et, morning_refresh, price_availability as pa
    day = et.eastern_today(now)
    plan = cp.load_plan(day)
    out = {"generated_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "day": day, "plan_present": plan is not None}
    if plan is None:
        out["note"] = "No credit plan has been made for today yet (it is made by the first trader cycle that sees the day's games)."
        return out
    prep = plan.get("prep") or {}
    refresh = morning_refresh.read() or {}
    board = _read("slate_board.json") or {}
    analysis_due = _utc(prep.get("analysis_at"))
    board_at = _utc(board.get("generated_at_utc")) if board.get("date_et") == day else None
    out["prep"] = {"refresh_due_utc": prep.get("refresh_at"), "refresh_status": refresh.get("status") if refresh.get("day") == day else "NOT_RUN_YET",
                   "refresh_finished_utc": refresh.get("finished_utc") if refresh.get("day") == day else None,
                   "analysis_due_utc": prep.get("analysis_at"), "analysis_published_utc": board_at.strftime("%Y-%m-%dT%H:%M:%SZ") if board_at else None,
                   "analysis_games": (board.get("counts") or {}).get("analysed"), "slate_games": plan.get("games_today")}
    pulls = [r for r in cp.read_ledger(day) if r["class"] == cp.MONEYLINE_UI]
    lp = plan.get("league_pulls") or {}
    out["league_pulls"] = {"planned": lp.get("planned"), "cost_each": lp.get("cost_each"), "cost_observed": lp.get("cost_observed"),
                           "made": [{"at_utc": r["at"], "credits": r["credits"], "planned_credits": r.get("planned"), "markets": r.get("markets")} for r in pulls]}
    state = _read("wave_capture_state.json") or {}
    wstate = (state.get("waves") or {}) if state.get("day") == day else {}
    ledger = cp.read_ledger(day)
    avail = pa.read(day).get("games") or {}
    games_info = {}
    for r in ledger:
        if r.get("game_id") is not None and r["class"] in (cp.PROPS, cp.GOALS, cp.SAVES, cp.POINTS_ADDON, cp.REFRESH):
            games_info[str(r["game_id"])] = games_info.get(str(r["game_id"]), 0.0) + r["credits"]
    waves_out, excluded = [], []
    for w in plan.get("waves") or []:
        tgt, dl, last = _utc(w["target_utc"]), _utc(w["deadline_utc"]), _utc(w["last_chance_utc"])
        ws = wstate.get(str(w["index"])) or {}
        sel = w.get("selected") or []
        rows = []
        for gid in w["games"]:
            a = (avail.get(gid) or {})
            mk = {k.replace("player_", ""): (v or {}).get("status") for k, v in (a.get("markets") or {}).items()}
            rows.append({"game_id": gid, "matchup": a.get("matchup"), "selected": gid in sel, "markets_planned": [m.replace("player_", "") for m in (plan.get("game_markets") or {}).get(gid, [])],
                         "market_status": mk, "credits": games_info.get(gid, 0.0), "missed": a.get("missed_windows") or [], "start_utc": a.get("start_utc")})
            if gid not in sel and not games_info.get(gid):
                excluded.append({"game_id": gid, "matchup": a.get("matchup"), "wave": w["index"], "reason": "not selected: the day's player-odds share did not reach this game (credit budget)"})
        captured = [r for r in rows if r["selected"] and ((ws.get("games") or {}).get(r["game_id"]) is not None)]
        if not sel and any(games_info.get(r["game_id"]) for r in rows):
            st = CAPTURED_EARLIER
        elif not sel:
            st = NONE_SELECTED
        elif len(captured) == len(sel):
            st = ON_TIME if ws.get("on_time") else LATE
        elif any(r["missed"] for r in rows if r["selected"]) and now > last:
            st = MISSED
        elif captured:
            st = PARTIAL if now <= last else MISSED
        elif now < tgt:
            st = PENDING
        elif now <= last:
            st = DUE
        else:
            st = CLOSED
        waves_out.append({"index": w["index"], "first_game_utc": w["first_start_utc"], "target_utc": w["target_utc"], "publish_deadline_utc": w["deadline_utc"], "last_chance_utc": w["last_chance_utc"],
                          "state": st, "selected": len(sel), "games": rows, "credits": round(sum(r["credits"] for r in rows), 1)})
    out["waves"] = waves_out
    out["excluded_by_budget"] = excluded
    b = plan.get("budget") or {}
    out["budget"] = {k: b.get(k) for k in ("remaining_header", "remaining_header_now", "usable_at_start_of_day", "league_total", "recovery_pool", "player_pool", "player_today", "days_left", "reset_day",
                                           "reset_verified", "reset_assumption", "reserve")}
    out["budget"]["surplus_credits"] = plan.get("surplus_credits")
    out["budget"]["notes"] = plan.get("notes") or []
    from operational import odds_quota
    out["balance"] = odds_quota.balance_detail(now)                 # what the spending guard believes: the newest provider header minus every later charge
    out["reconciliation"] = cp.reconciliation(day)
    out["spent_today"] = cp.spent_today(now)
    return out


def write(now: dt.datetime) -> dict:
    doc = build(now)
    try:
        p = state_paths.path(NAME)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, indent=1, sort_keys=True, default=str))
        tmp.replace(p)
    except OSError:
        pass
    return doc


def load() -> dict | None:
    return _read(NAME)
