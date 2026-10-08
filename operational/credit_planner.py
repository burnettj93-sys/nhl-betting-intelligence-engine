"""
The daily Odds API credit plan: one place that decides what the day's credits buy, so no job can spend the month ahead of the others.

Why it exists (docs/ODDS_CREDIT_AUDIT.md): in the first eight days of the cycle the jobs spent about 27 credits/day against an even pace of
about 12, because each job was allowed up to three times the even pace on its own and several bought the same game's shots prices.

The plan (all numbers recomputed from the provider's own remaining-credit header, so it corrects itself):

  day budget D       = (credits remaining now + credits already spent today - reserve) / days left in the cycle at the start of today
  priority waterfall  1. MONEYLINE_DECISION  the T-35 league-wide pull per start cluster (1 credit each; the decision feed). Always first.
                      2. MONEYLINE_UI        one display refresh (a second only after step 3 is satisfied)
                      3. PROPS_PREGAME       shots (alternate) + points for each game, 2 credits, ONE capture per game in its actionable window
                      4. SAVES_CONFIRMED     player_total_saves, 1 credit, only for a game with a confirmed starter, one capture in the last 75 minutes
                      5. GOALS               anytime goal scorer, +1 credit for a game already planned in step 3
                      6. REFRESH             a second props capture per game, only if credits are left over
  The plan names which games get step 3 when D cannot cover all of them: games are ranked wave by wave (a wave is the games whose puck drops
  are within 90 minutes of the wave's first), earliest first inside a wave, taking one game from each wave in turn so no wave is starved. The
  chosen set is saved for the day and never reshuffled by a later cycle.

A single capture per game at actionable time: a price captured 100 minutes before puck drop is still inside the near-game freshness limit at
puck drop, so it needs no refresh. Timestamp safeguards are unchanged (provider update time, retrieval time, freshness limits).
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from operational import odds_quota, state_paths

LEDGER_NAME = "credit_plan_ledger.jsonl"
PLAN_NAME = "credit_plan_state.json"

MONEYLINE_DECISION, MONEYLINE_UI, PROPS, SAVES, GOALS, REFRESH = ("MONEYLINE_DECISION", "MONEYLINE_UI", "PROPS_PREGAME", "SAVES_CONFIRMED",
                                                                   "GOALS", "REFRESH")
DECISION_RESERVE = 3          # T-35 pulls per day the plan always funds (typical day: 2-3 start clusters)
UI_FIRST, UI_SECOND = 1, 1
BASE_COST = 2                 # shots (alternate) + points
SAVES_COST, GOALS_COST = 1, 1
SAVES_MAX_PER_DAY = 3
FIRST_CAPTURE_HOURS = 1.75    # one capture per game at or inside this many hours before puck drop; fresh through puck drop
WAVE_MINUTES = 90


# ---------------------------------------------------------------- ledger ----

def _ledger():
    return state_paths.path(LEDGER_NAME)


def record(klass: str, credits: float, now: dt.datetime, **extra) -> None:
    """Append a paid call after it happened. Never raises: accounting must not break a job."""
    try:
        p = _ledger()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a") as f:
            f.write(json.dumps({"at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "class": klass, "credits": float(credits), **extra}) + "\n")
    except OSError:
        pass


def _et_day(stamp: str) -> str:
    from operational import eastern_time as et
    return et.eastern_today(dt.datetime.fromisoformat(stamp.replace("Z", "+00:00")))


def read_ledger(day: str | None = None) -> list[dict]:
    p = _ledger()
    if not p.exists():
        return []
    rows = []
    for line in p.read_text().splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if day is None or _et_day(r["at"]) == day:
            rows.append(r)
    return rows


def spent_today(now: dt.datetime) -> dict[str, float]:
    out: dict[str, float] = {}
    from operational import eastern_time as et
    for r in read_ledger(et.eastern_today(now)):
        out[r["class"]] = out.get(r["class"], 0.0) + r["credits"]
    return out


# ---------------------------------------------------------------- budget ----

def day_budget(now: dt.datetime, remaining: int | None, spent_all_today: float) -> dict:
    """Today's credits. Anchored to the start of today so it does not shrink as the day's own spending happens."""
    if remaining is None:
        return {"D": 0.0, "reason": "QUOTA_UNKNOWN"}
    start = dt.datetime.combine(now.date(), dt.time.min, dt.timezone.utc)
    days_left = max(odds_quota.days_left_in_cycle(now.date()), 1)
    usable_at_start = max(remaining + spent_all_today - odds_quota.RESERVE, 0)
    return {"D": round(usable_at_start / days_left, 2), "days_left": days_left, "usable_at_start_of_day": usable_at_start, "reason": "OK",
            "start_of_day_utc": start.strftime("%Y-%m-%dT%H:%M:%SZ")}


def waves(starts: dict[str, dt.datetime]) -> list[list[str]]:
    """Group game ids into waves: puck drops within WAVE_MINUTES of the wave's first."""
    out: list[list[str]] = []
    first = None
    for gid, t in sorted(starts.items(), key=lambda kv: (kv[1], kv[0])):
        if first is None or (t - first).total_seconds() > WAVE_MINUTES * 60:
            out.append([])
            first = t
        out[-1].append(gid)
    return out


def rank_games(starts: dict[str, dt.datetime]) -> list[str]:
    """Round-robin across waves, earliest first inside each wave."""
    ws = waves(starts)
    order, i = [], 0
    while any(ws):
        w = ws[i % len(ws)]
        if w:
            order.append(w.pop(0))
        i += 1
    return order


def _take(items: list, n: int) -> list:
    """The first n of a priority-ordered list (a budget cut-off for today's price purchases; nothing here decides what any model learns)."""
    return [x for i, x in enumerate(items) if i < n]


def allocate(D: float, starts: dict[str, dt.datetime], confirmed_games: set[str] | None = None, spent_by_class: dict | None = None) -> dict:
    """The waterfall. `starts` = today's games {game_id: puck drop}; returns what each class may spend and which games are priced."""
    spent_by_class = spent_by_class or {}
    confirmed_games = confirmed_games or set()
    n = len(starts)
    left = D
    take = lambda want: min(max(left, 0.0), want)  # noqa: E731
    decision = take(DECISION_RESERVE); left -= decision
    ui = take(UI_FIRST); left -= ui
    order = rank_games(starts)
    k_base = min(n, int(max(left, 0.0) // BASE_COST))
    props_games = _take(order, k_base)
    props = k_base * BASE_COST; left -= props
    saves_games = _take([g for g in order if g in confirmed_games], SAVES_MAX_PER_DAY)
    k_saves = min(len(saves_games), int(max(left, 0.0) // SAVES_COST))
    saves_games = _take(saves_games, k_saves); left -= k_saves * SAVES_COST
    ui_extra = take(UI_SECOND) if k_base == n else 0.0; left -= ui_extra
    k_goals = min(len(props_games), int(max(left, 0.0) // GOALS_COST))
    goals_games = _take(props_games, k_goals); left -= k_goals * GOALS_COST
    refresh = max(left, 0.0)
    full_need = DECISION_RESERVE + UI_FIRST + UI_SECOND + n * (BASE_COST + GOALS_COST) + min(n, SAVES_MAX_PER_DAY) * SAVES_COST
    base_need = DECISION_RESERVE + UI_FIRST + n * BASE_COST
    return {"D": D, "games_today": n, "games_priced": props_games, "games_not_priced": [g for g in order if g not in props_games],
            "goals_games": goals_games, "saves_games": saves_games,
            "allowance": {MONEYLINE_DECISION: decision, MONEYLINE_UI: ui + ui_extra, PROPS: float(props), SAVES: float(k_saves * SAVES_COST),
                          GOALS: float(k_goals * GOALS_COST), REFRESH: round(refresh, 2)},
            "need_for_all_games_and_markets": full_need, "need_for_required_only": base_need,
            "shortfall_per_day_required_only": round(max(base_need - D, 0.0), 1), "shortfall_per_day_everything": round(max(full_need - D, 0.0), 1)}


# ---------------------------------------------------------------- state ----

def _plan_path():
    return state_paths.path(PLAN_NAME)


def load_plan(day: str) -> dict | None:
    try:
        p = json.loads(_plan_path().read_text())
        return p if p.get("day") == day else None
    except (OSError, json.JSONDecodeError):
        return None


def day_plan(now: dt.datetime, day: str, starts: dict[str, dt.datetime], *, remaining: int | None, confirmed_games: set[str] | None = None) -> dict:
    """The saved plan for the ET day, built once (first cycle that sees the day's games) and kept. Confirmed-starter saves games and the
    budget are refreshed each call, but the set of PRICED games never changes after it is first chosen."""
    spent = spent_today(now)
    budget = day_budget(now, remaining, sum(spent.values()))
    saved = load_plan(day)
    fresh = allocate(budget["D"], starts, confirmed_games, spent)
    if saved is None:
        plan = {"day": day, "made_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), **fresh, "budget": budget}
        _persist(plan)
        return plan
    # keep the chosen priced set and goals subset; update everything that legitimately changes during the day
    plan = {**saved, "saves_games": fresh["saves_games"], "budget": budget, "allowance": {**saved["allowance"], SAVES: fresh["allowance"][SAVES]}}
    _persist(plan)
    return plan


def _persist(plan: dict) -> None:
    try:
        p = _plan_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(plan, default=str, sort_keys=True))
        tmp.replace(p)
    except OSError:
        pass


# ---------------------------------------------------------------- decisions ----

def authorize(klass: str, planned: float, now: dt.datetime, *, remaining: int | None = None, plan: dict | None = None) -> dict:
    """May this class spend `planned` credits now? Always honours the hard reserve; T-35 decision pulls only need the reserve."""
    if remaining is None:
        remaining = odds_quota.latest_remaining()
    if remaining is None:
        return {"allow": False, "reason": "QUOTA_UNKNOWN"}
    if remaining - planned < odds_quota.RESERVE:
        return {"allow": False, "reason": "HARD_RESERVE", "remaining": remaining}
    if klass == MONEYLINE_DECISION:
        return {"allow": True, "reason": "OK_DECISION_FEED", "remaining": remaining}
    spent = spent_today(now)
    allow = ((plan or {}).get("allowance") or {}).get(klass)
    if allow is None:
        budget = day_budget(now, remaining, sum(spent.values()))
        allow = budget["D"] - DECISION_RESERVE if klass == MONEYLINE_UI else 0.0
        if klass == MONEYLINE_UI:
            allow = min(UI_FIRST + UI_SECOND, max(budget["D"] - DECISION_RESERVE, 0.0))
    if spent.get(klass, 0.0) + planned > allow + 1e-9:
        return {"allow": False, "reason": f"{klass}_DAILY_ALLOWANCE", "allowance": allow, "spent": spent.get(klass, 0.0)}
    return {"allow": True, "reason": "OK", "allowance": allow, "spent": spent.get(klass, 0.0), "remaining": remaining}


def month_view(now: dt.datetime, remaining: int | None, starts_per_day: float = 7.0) -> dict:
    """What the month looks like under the plan: the credits needed for full coverage versus the credits that exist."""
    spent = spent_today(now)
    b = day_budget(now, remaining, sum(spent.values()))
    D = b["D"]
    n = int(round(starts_per_day))
    synthetic = {f"g{i}": dt.datetime(2026, 1, 1, 22, 0, tzinfo=dt.timezone.utc) + dt.timedelta(minutes=20 * i) for i in range(n)}
    a = allocate(D, synthetic)
    days = b.get("days_left", 1)
    return {"D": D, "typical_games_per_day": n, "games_priced_per_day": len(a["games_priced"]), "need_required_only_per_day": a["need_for_required_only"],
            "need_everything_per_day": a["need_for_all_games_and_markets"], "shortfall_month_required_only": round(a["shortfall_per_day_required_only"] * days),
            "shortfall_month_everything": round(a["shortfall_per_day_everything"] * days), "days_left": days}


def enforced() -> bool:
    """The plan governs paid captures in production; unit tests keep the legacy cadence unless they ask for the plan."""
    import os
    return (not state_paths.under_test()) or os.environ.get("NHL_ENGINE_CREDIT_PLAN", "").upper() == "ON"
