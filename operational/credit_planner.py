"""
The Odds API credit plan (version 4, 2026-10-17): one place that decides what the allowance buys, so no job can spend the cycle ahead of the others.

WHAT IT PLANS FROM (never a fresh 500): the provider's own remaining-credit header, the schedule still to come in this billing cycle, and the reset date. The reset date is NOT provider-confirmed;
it is an assumption (the 1st: the counter fell from 182 used to 0 between 2026-09-30 23:55 UTC and 2026-10-01, seen once) and every plan says so. Until the owner sets NHL_ENGINE_ODDS_RESET_DAY the plan
keeps the standing 20-credit reserve and labels the uncertainty.

THE ENVELOPE (the owner's initial 30-day model; reconciled against the real charges recorded in the ledger):
  league-wide moneylines + standard spreads   2 pulls a day (the 08:00 morning pull and ONE pregame pull shared by every wave) x the cost of a pull (2 credits: h2h + spreads, ASSUMED until a charge header
                                              confirms it, then the observed cost is used)
  player-odds captures                        one coordinated capture per selected game: shots ladder + anytime goal + goalie saves = 3 credits (about four games a day on average)
  recovery                                    a small pool (20 per 30 days) for late captures after an outage and for selective retries of a market that was not posted when asked
  reserve                                     20 credits are never spent
Not in the core allocation: T-35 decision pulls, a morning player-odds look, hourly polling, paid tomorrow checks, second captures of fresh markets. Points prices and extra league pulls for later
waves are funded ONLY from explicit surplus: credits left in today's share after every eligible game already has its bundle.

HOW THE DAY'S SHARE IS CHOSEN: spendable credits at the start of today (remaining + spent today - reserve), minus the league pulls and the recovery pool still to come, are the player pool. It is divided across
the remaining days of the cycle in proportion to each day's games (capped at 8 so one huge night does not starve the rest), which is a rollover by construction: a quiet day's unspent share returns to the pool and
the next day's share is computed from the actual balance. Inside the day, the share is split across the start-time waves BEFORE anything is spent: the first wave is covered first, then the largest later waves, then
the best remaining games by data quality; an early afternoon game never starves the evening and the evening never absorbs everything.

The plan is built once per Eastern day and kept (nothing is reshuffled by a later cycle), except that a plan written by an older version is rebuilt once on deployment.

Spending is RESERVED before a call and SETTLED to the actual charge afterwards (reserve / settle): concurrent jobs see each other's pending reservations under a file lock, so one allocation is never spent twice.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import math
import uuid
from pathlib import Path

from operational import capture_timeline, odds_quota, state_paths

LEDGER_NAME = "credit_plan_ledger.jsonl"
PLAN_NAME = "credit_plan_state.json"
RESERVATIONS_NAME = "credit_reservations.json"
RESERVATION_TTL_MIN = 15.0

MONEYLINE_DECISION, MONEYLINE_UI, PROPS, SAVES, GOALS, REFRESH = ("MONEYLINE_DECISION", "MONEYLINE_UI", "PROPS_PREGAME", "SAVES_CONFIRMED",
                                                                   "GOALS", "REFRESH")
MORNING, TOMORROW = "PROPS_MORNING", "TOMORROW_CHECK"
POINTS_ADDON = "PROPS_POINTS"
SHOTS_KEY, POINTS_KEY, GOALS_KEY, SAVES_KEY = "player_shots_on_goal_alternate", "player_points", "player_goal_scorer_anytime", "player_total_saves"
VALID_PROP_MARKETS = (SHOTS_KEY, POINTS_KEY)
DEFAULT_PROP_MARKETS = (SHOTS_KEY,)
PROP_MARKETS_ENV = "NHL_ENGINE_PROP_MARKETS"
BASE_COST = 1
SAVES_COST, GOALS_COST, POINTS_COST = 1, 1, 1
BUNDLE_MARKETS = (SHOTS_KEY, GOALS_KEY, SAVES_KEY)             # one coordinated capture per selected game
FIRST_CAPTURE_HOURS = capture_timeline.CAPTURE_TARGET_MIN / 60.0
WAVE_MINUTES = capture_timeline.WAVE_MINUTES
TOMORROW_COST = 1                                              # a paid tomorrow look exists only as an explicit-surplus extra; the core allocation is 0

CYCLE_CREDITS = 500
RECOVERY_PER_30_DAYS = 20
LEAGUE_PULLS_PER_DAY = 2                                       # morning + one shared pregame pull
LEAGUE_COST_ASSUMED = 2                                        # h2h + spreads, markets x regions; replaced by the observed charge once a spreads pull has been recorded
DAY_WEIGHT_CAP = 8
ALLOC_VERSION = 4
RESET_EVIDENCE = "the counter fell from 182 used to 0 between 2026-09-30 23:55 UTC and 2026-10-01 (observed once); the provider publishes no reset date"


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
        klass, credits = r["class"], r["credits"]
        if klass == PROPS and GOALS_KEY in str(r.get("markets") or "") and credits >= 1 and "planned" not in r:
            out[GOALS] = out.get(GOALS, 0.0) + GOALS_COST          # rows written before goals had their own class: the goals market's one credit
            credits -= GOALS_COST
        out[klass] = out.get(klass, 0.0) + credits
    return out


def league_pull_times(now: dt.datetime) -> list[dt.datetime]:
    """Today's league-wide pulls (UTC), oldest first, from the ledger."""
    from operational import eastern_time as et
    out = []
    for r in read_ledger(et.eastern_today(now)):
        if r["class"] in (MONEYLINE_UI, MONEYLINE_DECISION) and r.get("label") != "reconcile":
            out.append(dt.datetime.fromisoformat(r["at"].replace("Z", "+00:00")))
    return sorted(out)


def observed_league_cost() -> tuple[int, bool]:
    """(credits one league pull costs, whether that is observed). Observed from the newest ledger row of a pull that asked for spreads; otherwise the assumption."""
    for r in reversed(read_ledger()):
        if r["class"] == MONEYLINE_UI and "spreads" in str(r.get("markets") or "") and r.get("credits") is not None:
            return max(int(round(r["credits"])), 0), True
    return LEAGUE_COST_ASSUMED, False


def captured_games_today(now: dt.datetime) -> set[str]:
    """Games that already have a player-odds capture today (any bundle class)."""
    from operational import eastern_time as et
    return {str(r["game_id"]) for r in read_ledger(et.eastern_today(now)) if r.get("game_id") is not None and r["class"] in (PROPS, GOALS, SAVES, POINTS_ADDON, MORNING, REFRESH)}


# ---------------------------------------------------------------- waves, markets ----

def waves(starts: dict[str, dt.datetime]) -> list[list[str]]:
    """Group game ids into waves (compatible start times), earliest first."""
    return [list(w.games) for w in capture_timeline.waves(starts)]


def prop_markets() -> tuple[str, ...]:
    """Kept for the builder and older callers: the base market a game's capture always includes (shots). Points are a surplus extra, never part of the base."""
    import os
    raw = os.environ.get(PROP_MARKETS_ENV) or ""
    chosen = tuple(m for m in (x.strip() for x in raw.split(",")) if m in VALID_PROP_MARKETS)
    return chosen or DEFAULT_PROP_MARKETS


def base_cost() -> int:
    return len(prop_markets())


POINTS_ADDON_ENV = "NHL_ENGINE_POINTS_ADDON"


def points_addon_enabled() -> bool:
    """Points prices ride on a selected game's capture only from explicit surplus. `NHL_ENGINE_POINTS_ADDON=off` switches that off."""
    import os
    raw = os.environ.get(POINTS_ADDON_ENV) or ""
    if not raw:
        env = state_paths.REPO_ROOT / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                k, sep, v = line.strip().partition("=")
                if sep and k.strip() == POINTS_ADDON_ENV:
                    raw = v.strip().strip("\"'")
    return POINTS_KEY not in prop_markets() and raw.strip().lower() not in ("off", "0", "false", "no")


MORNING_ENV = "NHL_ENGINE_MORNING_LOOK"


def morning_enabled() -> bool:
    """The morning player-odds look is retired in version 4 (the 08:00 slot is the league-wide pull and the whole-slate analysis). Kept so the old switch still reads."""
    return False


# ---------------------------------------------------------------- the day's plan ----

def game_quality(product: dict | None, game_id: str) -> float:
    """Data quality of one game for choosing what to capture: how many skaters on both teams have a pricing-eligible projection and a TOI role, and whether both goalies have a projection.
    0 means nothing to price. Pure reading of the product state; it never looks at prices or results."""
    if not product:
        return 1.0
    d = (product.get("game_details") or {}).get(str(game_id)) or {}
    players = product.get("players") or {}
    goalies = product.get("goalies") or {}
    sides = d.get("sides") or {}
    n_sk = n_g = 0
    for s in ("home", "away"):
        for pid in (sides.get(s) or {}).get("skater_ids") or []:
            p = players.get(pid) or {}
            proj = p.get("projection") or {}
            toi = ((proj.get("expected") or {}).get("toi")) or 0.0
            if proj.get("pricing_eligible") and toi >= 10.0 and (p.get("games_total") or 0) >= 10:
                n_sk += 1
        for gid in (sides.get(s) or {}).get("goalie_ids") or []:
            g = goalies.get(gid) or {}
            if g.get("projection") or g.get("start") is not None:
                n_g += 1
                break
    return round(n_sk + 4.0 * n_g, 2)


def select_games(wave_list: list, quality: dict[str, float], eligible: set[str], k: int) -> list[str]:
    """Choose k games to capture. Pass 1: the best game of the FIRST wave (the first game must never wait for the evening cluster). Pass 2: the best game of each later wave, largest wave
    first (earliest on a tie). Pass 3: the best remaining games by data quality, preferring waves that already have a selected game (their prices can be compared at one time). Returns
    the chosen game ids in start order."""
    if k <= 0:
        return []
    cand = {w.index: sorted([g for g in w.games if g in eligible], key=lambda g: (-quality.get(g, 0.0), g)) for w in wave_list}
    chosen: list[str] = []

    def take(widx):
        for g in cand.get(widx, []):
            if g not in chosen:
                chosen.append(g)
                return True
        return False
    for w in wave_list:                                           # pass 1: the first wave that has an eligible game
        if cand.get(w.index):
            take(w.index)
            break
    later = sorted([w for w in wave_list if cand.get(w.index) and not any(g in chosen for g in w.games)], key=lambda w: (-len(cand[w.index]), w.first_start))
    for w in later:                                               # pass 2
        if len(chosen) >= k:
            break
        take(w.index)
    rest = [g for w in wave_list for g in cand.get(w.index, []) if g not in chosen]
    rest.sort(key=lambda g: (0 if any(g in w.games and any(c in w.games for c in chosen) for w in wave_list) else 1, -quality.get(g, 0.0), g))
    for g in rest:                                                # pass 3
        if len(chosen) >= k:
            break
        chosen.append(g)
    chosen = chosen[:k]
    start_of = {g: w.first_start for w in wave_list for g in w.games}
    return sorted(chosen, key=lambda g: (start_of.get(g), g))


def cycle_budget(now: dt.datetime, remaining: int | None, spent_all_today: float, counts_by_day: dict[str, int], today: str, *, league_cost: int, league_cost_observed: bool) -> dict:
    """The cycle's money, from the real balance. `counts_by_day` is {Eastern day: games} for today and every remaining day of the cycle (today's from the schedule)."""
    reset = odds_quota.reset_status()
    days_left = max(odds_quota.days_left_in_cycle(now.date()), 1)
    base = {"reset_day": reset["reset_day"], "reset_verified": not reset["assumed"], "reset_assumption": None if not reset["assumed"] else
            f"ASSUMED the 1st ({RESET_EVIDENCE}); set NHL_ENGINE_ODDS_RESET_DAY once the provider account confirms it", "days_left": days_left,
            "league_cost_each": league_cost, "league_cost_observed": league_cost_observed, "reserve": odds_quota.RESERVE}
    if remaining is None:
        return {**base, "reason": "QUOTA_UNKNOWN", "D": 0.0, "usable_at_start_of_day": 0, "league_total": 0, "recovery_pool": 0, "player_pool": 0.0, "player_today": 0.0}
    usable = max(remaining + spent_all_today - odds_quota.RESERVE, 0.0)
    days = sorted(d for d, n in counts_by_day.items() if d >= today)[:days_left]
    game_days = [d for d in days if counts_by_day.get(d, 0) > 0]
    league_total = len(game_days) * LEAGUE_PULLS_PER_DAY * league_cost
    recovery = math.ceil(RECOVERY_PER_30_DAYS * days_left / 30.0)
    if usable < league_total + recovery:                           # the balance cannot cover everything: league pulls come first, then recovery, then nothing is left for players
        league_total = min(league_total, usable)
        recovery = min(recovery, max(usable - league_total, 0.0))
    player_pool = max(usable - league_total - recovery, 0.0)
    weights = {d: min(counts_by_day.get(d, 0), DAY_WEIGHT_CAP) for d in days}
    w_total = sum(weights.values())
    w_today = weights.get(today, 0)
    player_today = player_pool * (w_today / w_total) if w_total else 0.0
    return {**base, "reason": "OK", "remaining_header": remaining, "spent_today_at_plan": round(spent_all_today, 1), "usable_at_start_of_day": round(usable, 1),
            "league_total": round(league_total, 1), "recovery_pool": recovery, "player_pool": round(player_pool, 1), "weight_today": w_today, "weight_total": w_total,
            "player_today": round(player_today, 2), "game_days_remaining": len(game_days), "D": round(usable / max(len(game_days), 1), 2)}


def build_day_plan(now: dt.datetime, day: str, starts: dict[str, dt.datetime], *, remaining: int | None, counts_ahead: dict[str, int] | None = None,
                   quality: dict[str, float] | None = None, confirmed_games: set[str] | None = None, done_games: set[str] | None = None) -> dict:
    """The plan for one Eastern day (pure given its inputs). `starts` = every game of the day {game_id: start_utc}; started games are listed but never selected."""
    spent = spent_today(now)
    spent_all = sum(spent.values())
    lc, observed = observed_league_cost()
    counts = dict(counts_ahead or {})
    counts[day] = len(starts)
    budget = cycle_budget(now, remaining, spent_all, counts, day, league_cost=lc, league_cost_observed=observed)
    wave_list = capture_timeline.waves(starts)
    done = set(done_games or ())
    eligible = {g for g, t in starts.items() if t > now and g not in done and (quality is None or quality.get(g, 1.0) > 0)}
    quality = quality or {}
    spent_player = sum(spent.get(k, 0.0) for k in (PROPS, GOALS, SAVES, POINTS_ADDON, MORNING, REFRESH))
    avail = max(budget["player_today"] - spent_player, 0.0)
    bundle = len(BUNDLE_MARKETS)
    k = int(avail // bundle)
    chosen = select_games(wave_list, quality, eligible, min(k, len(eligible)))
    markets = {g: list(BUNDLE_MARKETS) for g in chosen}
    left = avail - bundle * len(chosen)
    partial = None
    if left >= 1 and len(chosen) < len(eligible):                 # a remainder buys a partial bundle (shots first, then goals) for the next best game
        extra = select_games(wave_list, quality, eligible - set(chosen), 1)
        if extra:
            partial = extra[0]
            markets[partial] = [SHOTS_KEY, GOALS_KEY][:int(left)]
            left -= len(markets[partial])
            chosen = sorted(chosen + [partial], key=lambda g: (next(w.first_start for w in wave_list if g in w.games), g))
    points_games, extra_pulls = [], 0
    if len(chosen) >= len(eligible) and left >= 1:                # surplus: every eligible game already has a full bundle
        if points_addon_enabled():
            k_p = int(left // POINTS_COST)
            points_games = chosen[:k_p]
            for g in points_games:
                markets[g] = markets[g] + [POINTS_KEY]
            left -= len(points_games) * POINTS_COST
        later_waves = max(len([w for w in wave_list if any(g in eligible or g in done for g in w.games) and w.last_chance > now]) - 1, 0)
        extra_pulls = min(later_waves, int(left // max(lc, 1)))
        left -= extra_pulls * lc
    shots_c = sum(1 for g in chosen if SHOTS_KEY in markets[g])
    goals_games = [g for g in chosen if GOALS_KEY in markets[g]]
    saves_games = [g for g in chosen if SAVES_KEY in markets[g]]
    league_planned = (LEAGUE_PULLS_PER_DAY + extra_pulls) if budget["league_total"] >= LEAGUE_PULLS_PER_DAY * lc else int(budget["league_total"] // max(lc, 1))
    recovery_today = math.ceil(budget["recovery_pool"] / max(budget["days_left"], 1)) if budget["recovery_pool"] else 0
    # reserve() compares today's SPENT + pending + planned with the allowance, so a plan rebuilt mid-day (a version change, a lost file) adds what the class already spent: the games still to be
    # captured keep exactly the credits the plan gives them instead of being starved by the captures that already happened.
    allowance = {MONEYLINE_UI: float(league_planned * lc), PROPS: float(shots_c * BASE_COST) + spent.get(PROPS, 0.0), GOALS: float(len(goals_games) * GOALS_COST) + spent.get(GOALS, 0.0),
                 SAVES: float(len(saves_games) * SAVES_COST) + spent.get(SAVES, 0.0), POINTS_ADDON: float(len(points_games) * POINTS_COST) + spent.get(POINTS_ADDON, 0.0),
                 REFRESH: float(recovery_today), MONEYLINE_DECISION: 0.0, MORNING: 0.0, TOMORROW: 0.0}
    first_start = min(starts.values()) if starts else None
    prep = capture_timeline.prep_times(dt.date.fromisoformat(day), first_start) if first_start else {}
    wave_docs = [{"index": w.index, "first_start_utc": w.first_start.strftime("%Y-%m-%dT%H:%M:%SZ"), "target_utc": w.target.strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "deadline_utc": w.deadline.strftime("%Y-%m-%dT%H:%M:%SZ"), "last_chance_utc": w.last_chance.strftime("%Y-%m-%dT%H:%M:%SZ"), "games": list(w.games),
                  "selected": [g for g in chosen if g in w.games]} for w in wave_list]
    notes = []
    if not budget.get("reset_verified"):
        notes.append(budget["reset_assumption"])
    if not budget.get("league_cost_observed"):
        notes.append(f"A league pull is assumed to cost {lc} credits (moneyline + spreads); the first recorded charge replaces the assumption.")
    return {"day": day, "made_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "alloc_version": ALLOC_VERSION, "budget": budget, "waves": wave_docs, "prep": {k_: v.strftime("%Y-%m-%dT%H:%M:%SZ") for k_, v in prep.items()},
            "games_today": len(starts), "games_priced": chosen, "games_not_priced": [g for g in starts if g not in chosen and g not in done], "game_markets": markets,
            "goals_games": goals_games, "saves_games": saves_games, "points_games": points_games, "morning_games": [], "morning_markets": [],
            "league_pulls": {"planned": league_planned, "cost_each": lc, "cost_observed": observed, "extra_for_later_waves": extra_pulls}, "allowance": allowance,
            "confirmed_games": sorted(confirmed_games or []), "surplus_credits": round(max(left, 0.0), 2), "notes": notes}


def _plan_path():
    return state_paths.path(PLAN_NAME)


def load_plan(day: str) -> dict | None:
    try:
        p = json.loads(_plan_path().read_text())
        return p if p.get("day") == day else None
    except (OSError, json.JSONDecodeError):
        return None


def _persist(plan: dict) -> None:
    try:
        p = _plan_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(plan, default=str, sort_keys=True))
        tmp.replace(p)
    except OSError:
        pass


def day_plan(now: dt.datetime, day: str, starts: dict[str, dt.datetime], *, remaining: int | None, confirmed_games: set[str] | None = None,
             counts_ahead: dict[str, int] | None = None, quality: dict[str, float] | None = None) -> dict:
    """The saved plan for the Eastern day, built once (first cycle that sees the day's games) and kept: nothing is reshuffled by a later cycle. A plan written by an older version is rebuilt once."""
    saved = load_plan(day)
    if saved is not None and saved.get("alloc_version") == ALLOC_VERSION:
        saved["confirmed_games"] = sorted(confirmed_games or [])
        if remaining is not None:
            saved["budget"] = {**saved["budget"], "remaining_header_now": remaining}
        _persist(saved)
        return saved
    plan = build_day_plan(now, day, starts, remaining=remaining, counts_ahead=counts_ahead, quality=quality, confirmed_games=confirmed_games, done_games=captured_games_today(now))
    _persist(plan)
    return plan


def D_of(plan: dict) -> float:  # noqa: N802
    return float((plan.get("budget") or {}).get("D") or plan.get("D") or 0.0)


# ---------------------------------------------------------------- reservations and decisions ----

class _Lock:
    def __init__(self):
        self.path = state_paths.path("credit_reservations.lock")

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(self.path, "w")
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *a):
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


def _load_reservations(now: dt.datetime) -> list[dict]:
    try:
        rows = json.loads(state_paths.path(RESERVATIONS_NAME).read_text())
    except (OSError, json.JSONDecodeError):
        rows = []
    cutoff = now - dt.timedelta(minutes=RESERVATION_TTL_MIN)
    return [r for r in rows if dt.datetime.fromisoformat(r["at"].replace("Z", "+00:00")) >= cutoff]


def _save_reservations(rows: list[dict]) -> None:
    try:
        p = state_paths.path(RESERVATIONS_NAME)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows))
        tmp.replace(p)
    except OSError:
        pass


def pending_reserved(now: dt.datetime) -> dict[str, float]:
    out: dict[str, float] = {}
    for r in _load_reservations(now):
        out[r["class"]] = out.get(r["class"], 0.0) + r["credits"]
    return out


def reserve(klass: str, planned: float, now: dt.datetime, *, plan: dict | None = None, remaining: int | None = None, note: str = "", key: str | None = None) -> dict:
    """Reserve `planned` credits for `klass` BEFORE a paid call. Under a file lock it checks the real balance minus every pending reservation (so a concurrent job cannot spend the same
    credits), the hard reserve and the class's allowance for today. `key` names WHAT is being bought (day, game, market, wave): a second reservation with the same key while the first is pending, or
    after a paid call with that key was ledgered, is refused as DUPLICATE, so two jobs that both saw "missing" cannot buy the same price twice. Returns {"allow": True, "id": ...} or {"allow": False, "reason": ...}. Settle it with settle() after the call."""
    if remaining is None:
        remaining = odds_quota.latest_remaining()
    if remaining is None:
        return {"allow": False, "reason": "QUOTA_UNKNOWN"}
    with _Lock():
        rows = _load_reservations(now)
        if key is not None:
            if any(r.get("key") == key for r in rows):
                return {"allow": False, "reason": "DUPLICATE", "pending": 1}
            if any(r.get("key") == key and r.get("credits", 0) > 0 for r in read_ledger()):
                return {"allow": False, "reason": "DUPLICATE", "pending": 0}
        pending_total = sum(r["credits"] for r in rows)
        if remaining - pending_total - planned < odds_quota.RESERVE:
            return {"allow": False, "reason": "HARD_RESERVE", "remaining": remaining, "pending": pending_total}
        allowance = ((plan or {}).get("allowance") or {}).get(klass)
        spent = spent_today(now).get(klass, 0.0)
        pend_cls = sum(r["credits"] for r in rows if r["class"] == klass)
        if allowance is None or spent + pend_cls + planned > allowance + 1e-9:
            return {"allow": False, "reason": f"{klass}_ALLOWANCE", "allowance": allowance, "spent": spent, "pending": pend_cls}
        rid = uuid.uuid4().hex[:12]
        rows.append({"id": rid, "class": klass, "credits": float(planned), "at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "note": note, **({"key": key} if key else {})})
        _save_reservations(rows)
        return {"allow": True, "id": rid, "reason": "OK", "remaining": remaining, "allowance": allowance, "spent": spent}


def settle(reservation_id: str, actual: float, now: dt.datetime, *, klass: str | None = None, **extra) -> None:
    """Replace a reservation by what the provider actually charged (the `x-requests-last` header). The ledger row keeps the planned figure too, so a mismatch is visible.
    The reservation is removed and the charge written under ONE lock, so a concurrent reserve() never sees neither. If the reservation already expired (the call outlived its 15-minute life)
    the charge is still recorded, in `klass`, because a paid call is never left out of the ledger."""
    with _Lock():
        all_rows = []
        try:
            all_rows = json.loads(state_paths.path(RESERVATIONS_NAME).read_text())
        except (OSError, json.JSONDecodeError):
            pass
        mine = next((r for r in all_rows if r.get("id") == reservation_id), None)
        _save_reservations([r for r in _load_reservations(now) if r["id"] != reservation_id])
        if mine is not None:
            record(mine["class"], max(float(actual), 0.0), now, planned=mine["credits"], **({"key": mine["key"]} if mine.get("key") else {}), **extra)
        elif klass is not None:
            record(klass, max(float(actual), 0.0), now, planned=None, expired_reservation=True, **extra)


def release(reservation_id: str, now: dt.datetime) -> None:
    """The call was never made (or failed before the provider counted it): drop the reservation."""
    with _Lock():
        _save_reservations([r for r in _load_reservations(now) if r["id"] != reservation_id])


def reconciliation(day: str | None = None) -> dict:
    """Planned versus actually charged credits per class, from ledger rows that carry both."""
    out: dict[str, dict] = {}
    for r in read_ledger(day):
        if "planned" not in r:
            continue
        o = out.setdefault(r["class"], {"calls": 0, "planned": 0.0, "actual": 0.0})
        o["calls"] += 1
        o["planned"] += r["planned"]
        o["actual"] += r["credits"]
    return out


def authorize(klass: str, planned: float, now: dt.datetime, *, remaining: int | None = None, plan: dict | None = None) -> dict:
    """May this class spend `planned` credits now? A read-only check against the hard reserve, pending reservations and the class's allowance (use reserve() before a real call)."""
    if remaining is None:
        remaining = odds_quota.latest_remaining()
    if remaining is None:
        return {"allow": False, "reason": "QUOTA_UNKNOWN"}
    pending = sum(pending_reserved(now).values())
    if remaining - pending - planned < odds_quota.RESERVE:
        return {"allow": False, "reason": "HARD_RESERVE", "remaining": remaining}
    spent = spent_today(now)
    allow = ((plan or {}).get("allowance") or {}).get(klass)
    if allow is None:
        allow = 0.0
    if spent.get(klass, 0.0) + pending_reserved(now).get(klass, 0.0) + planned > allow + 1e-9:
        return {"allow": False, "reason": f"{klass}_DAILY_ALLOWANCE", "allowance": allow, "spent": spent.get(klass, 0.0)}
    return {"allow": True, "reason": "OK", "allowance": allow, "spent": spent.get(klass, 0.0), "remaining": remaining}


def month_view(now: dt.datetime, remaining: int | None, starts_per_day: float = 4.0) -> dict:
    """The cycle at a glance for the credit panel: what is left, what the plan reserves for league pulls and recovery, and how many games a day the player pool covers."""
    lc, observed = observed_league_cost()
    days_left = max(odds_quota.days_left_in_cycle(now.date()), 1)
    usable = max((remaining or 0) - odds_quota.RESERVE, 0)
    league = days_left * LEAGUE_PULLS_PER_DAY * lc
    recovery = math.ceil(RECOVERY_PER_30_DAYS * days_left / 30.0)
    pool = max(usable - league - recovery, 0)
    per_day_games = pool / len(BUNDLE_MARKETS) / days_left
    return {"remaining": remaining, "days_left": days_left, "league_pulls_credits": league, "league_cost_each": lc, "league_cost_observed": observed, "recovery_credits": recovery,
            "player_pool_credits": pool, "games_per_day_covered": round(per_day_games, 1), "reset_assumed": odds_quota.reset_status()["assumed"]}


def enforced() -> bool:
    """The plan governs paid captures in production; unit tests keep the legacy cadence unless they ask for the plan."""
    import os
    return (not state_paths.under_test()) or os.environ.get("NHL_ENGINE_CREDIT_PLAN", "").upper() == "ON"
