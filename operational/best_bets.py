"""
Price capture + second-opinion player model for the unified ticket workflow.

This module no longer recommends anything. It used to build its own +100
singles and 2-leg parlays under rules that differed from the ledger's, so what
Today showed was not what the paper trader staked. The one selector is now
research/real_market_parlay/engine.py::select_tickets, driven by
operational/daily_tickets.py. What remains here:

  1. Capture (credit-metered, bounded): for each of today's upcoming games,
     pull DraftKings' player_shots_on_goal_alternate + player_points once when
     the game is within CAPTURE_HORIZON_H, then again each time the newest
     prices are REFRESH_LEAD_MIN short of the freshness limit (so eligible prices
     never expire between captures) -- never more than DAILY_CREDIT_CAP credits a
     day, never past the global odds_quota guard. Roughly four captures (8
     credits) per game; on a big slate the cap stops the farthest-out refreshes.
     The newest capture of EITHER job counts, so the prop sweeps' pulls are not duplicated.
     The archived payloads feed the same adapters the ticket selector uses.
  2. Model (second opinion): shots (empirical, conservative: the LOWER of
     last-20 and last-60 hit rates, shrunk toward the position average) and
     points (the project's locked points model blended with the last-60 hit
     rate, home/away adjusted), built from the full MoneyPuck history
     refreshed daily by operational/moneypuck_daily.py. Only players who
     dressed in their team's most recent real game are modelled (the only
     lineup signal that exists here -- there is no injury/lineup feed).
     daily_tickets.py lowers each shots leg's probability to the lower of the
     validated pipeline's and this model's, and drops shots legs for players
     this model has no dress confirmation for.

Run: python3 -m operational.best_bets
"""
from __future__ import annotations

import csv
import datetime as dt
import fcntl
import glob
import hashlib
import io
import json
import re
import statistics
import unicodedata
import zipfile
from collections import defaultdict
from pathlib import Path

from operational import quote_freshness, state_paths

REPO_ROOT = Path(__file__).resolve().parent.parent
MP_RAW_DIR = REPO_ROOT / "research" / "player_sog" / "raw"
MP_DAILY_ROOT = REPO_ROOT / "data" / "raw" / "moneypuck" / "skater" / "2026"

MARKETS = "player_shots_on_goal_alternate,player_points"
CAPTURE_HORIZON_H = 5.0
REFRESH_LEAD_MIN = 45.0            # refresh this long BEFORE a price would exceed its freshness limit (3 trader cycles)
DAILY_CREDIT_CAP = 36
EST_COST_PER_EVENT = 2


SOG_K = (1, 2, 3, 4, 5)
MODEL_VERSION = "player-rate-toi-v2+platt-2026-10"          # live probability source (validated; see docs/MODEL_VALIDATION.md)
PREVIOUS_MODEL_VERSION = "EXPERIMENTAL-rolling-l20-l60-shrunk-v1"   # compute_model() below; kept so earlier tickets stay interpretable
MIN_EXPECTED_TOI = 12.0
MAX_PRICE_AGE_MIN_FAR = 150.0      # game >= 2h away
MAX_PRICE_AGE_MIN_NEAR = 100.0     # game < 2h away
HISTORY_CACHE_NAME = "best_bets_history_2022_2025.jsonl"
STATE_NAME = "best_bets_state.json"
MODEL_CACHE_NAME = "best_bets_model.json"
LOCK_NAME = "best_bets.lock"


def _state_path() -> Path:
    return state_paths.path(STATE_NAME)


# ---------------------------------------------------------------- price math --

def american_to_decimal(a: float) -> float:
    return 1 + a / 100.0 if a > 0 else 1 + 100.0 / abs(a)


def decimal_to_american(d: float) -> int:
    return round((d - 1) * 100) if d >= 2.0 else round(-100 / (d - 1))


def norm_name(s: str) -> str:
    ascii_ = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", "", ascii_.replace("-", " "))).strip()


# ------------------------------------------------------------------- history --

def _rows_from_csv(fh) -> list[dict]:
    from research.moneypuck_ingestion.ingest import derive_game_type, derive_nhl_season, REGULAR_SEASON_GAME_TYPE

    def f(r, k):
        return float(r[k])

    def iso(d):
        return f"{d[0:4]}-{d[4:6]}-{d[6:8]}"

    allrows, pp, t5 = [], {}, {}
    for r in csv.DictReader(fh):
        sit = r["situation"]
        key = (r["playerId"], int(r["gameId"]))
        if sit == "all":
            allrows.append(r)
        elif sit == "5on4":
            pp[key] = r
        elif sit == "5on5":
            t5[key] = f(r, "icetime")
    out = []
    for r in allrows:
        gid = int(r["gameId"])
        if derive_game_type(gid) != REGULAR_SEASON_GAME_TYPE:
            continue
        key = (r["playerId"], gid)
        ppr, blk = pp.get(key), None
        if ppr is not None and f(ppr, "icetime") > 0:
            blk = {"icetime_seconds": f(ppr, "icetime"), "points": f(ppr, "I_F_points"), "goals": f(ppr, "I_F_goals"),
                   "assists": f(ppr, "I_F_primaryAssists") + f(ppr, "I_F_secondaryAssists")}
        out.append({
            "player_id": r["playerId"], "player_name": r["name"], "game_id": gid,
            "season": derive_nhl_season(int(r["season"])), "game_date": iso(r["gameDate"]), "team": r["playerTeam"],
            "opponent": r["opposingTeam"], "home_or_away": r["home_or_away"], "position": r["position"],
            "icetime_seconds": f(r, "icetime"), "toi_5v5_seconds": t5.get(key, 0.0), "goals": f(r, "I_F_goals"),
            "assists": f(r, "I_F_primaryAssists") + f(r, "I_F_secondaryAssists"), "points": f(r, "I_F_points"),
            "sog": f(r, "I_F_shotsOnGoal"), "blocks": f(r, "shotsBlockedByPlayer"), "pp": blk})
    return out


def _static_history_rows(raw_dir: Path | None = None, cache: Path | None = None) -> list[dict]:
    """2022-2025 seasons: built once from the archived MoneyPuck CSVs (~1-2 min),
    cached to a gitignored runtime file."""
    raw_dir = raw_dir or MP_RAW_DIR
    cache = cache or state_paths.path(HISTORY_CACHE_NAME)
    if cache.exists():
        return [json.loads(line) for line in cache.read_text().splitlines() if line.strip()]
    rows: list[dict] = []
    for season in (2022, 2023, 2024, 2025):
        with open(raw_dir / f"{season}.csv", newline="") as fh:
            rows.extend(_rows_from_csv(fh))
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r) + "\n" for r in rows))
    tmp.replace(cache)
    return rows


def _current_season_rows(daily_root: Path | None = None) -> tuple[list[dict], str | None]:
    """The CURRENT season's MoneyPuck file (refreshed daily by moneypuck_daily),
    plus its checksum for cache keying."""
    daily_root = daily_root or MP_DAILY_ROOT
    manifest_path = daily_root / "manifest.json"
    if not manifest_path.exists():
        return [], None
    manifest = json.loads(manifest_path.read_text())
    archived = REPO_ROOT / manifest["archived_file"]
    if not archived.exists():
        return [], None
    with zipfile.ZipFile(archived) as z:
        name = next(n for n in z.namelist() if n.endswith(".csv"))
        with io.TextIOWrapper(z.open(name), encoding="utf-8", newline="") as fh:
            rows = _rows_from_csv(fh)
    return rows, manifest.get("latest_accepted_checksum")


def history_rows() -> tuple[list[dict], str | None]:
    static = _static_history_rows()
    current, checksum = _current_season_rows()
    rows = static + current
    rows.sort(key=lambda r: (r["game_date"], r["game_id"], r["player_id"]))
    return rows, checksum


# --------------------------------------------------------------------- model --

def _dressed_and_current_team(conn, teams, as_of_utc: str | None = None) -> tuple[dict, dict]:
    from features import point_in_time as pit
    as_of = as_of_utc or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    dressed = {}
    for t in teams:
        row = conn.execute(
            "select game_id from games where game_state='FINAL' and (home_team=? or away_team=?) "
            "order by game_date desc, game_id desc limit 1", (t, t)).fetchone()
        dressed[t] = pit.team_players_who_played(conn, row[0], t, as_of) if row else set()
    return dressed, pit.latest_team_by_player_since(conn, "2026-09-01", as_of)


def compute_model(rows: list[dict], today: dict, dressed: dict, curteam: dict, date: str) -> dict:
    """{ "<norm name>|<TEAM>": {name, team, opp, home, probs: {"SOG1":..,"PTS1":..}} }"""
    from research.player_points import features as ptf
    from research.player_points.live_projection import project_player_points
    from research.run_player_points_model import threshold_prob as tp_pts

    index = ptf.PlayerHistoryIndex(rows)
    totals = ptf.build_team_game_points_totals(rows)
    toff, oenv = ptf.build_team_offense_history(totals), ptf.build_opponent_points_allowed(totals)
    lavg = statistics.fmean(v["points_for"] for v in totals.values())
    gm: dict = {}
    for r in rows:
        g = gm.setdefault(r["game_id"], {"game_id": r["game_id"], "game_date": r["game_date"]})
        g["home_team" if r["home_or_away"] == "HOME" else "away_team"] = r["team"]
    sched = defaultdict(list)
    for g in gm.values():
        if "home_team" in g and "away_team" in g:
            sched[g["home_team"]].append(g)
            sched[g["away_team"]].append(g)
    for t in sched:
        sched[t].sort(key=lambda g: (g["game_date"], g["game_id"]))
    pres = json.load(open(REPO_ROOT / "research" / "player_points_results.json"))
    pw = [pres["stage_weights"][pres["config"]["locked_stage"]][n] for n in pres["config"]["feature_names"]]
    palpha = pres["alpha"] if pres["alpha"] > 0.01 else None
    home = [r["points"] for r in rows if r["home_or_away"] == "HOME"]
    away = [r["points"] for r in rows if r["home_or_away"] == "AWAY"]
    tt = statistics.fmean(home + away)
    ph, pa = statistics.fmean(home) / tt, statistics.fmean(away) / tt
    recent = [r for r in rows if r["season"] == 20252026 and r["icetime_seconds"] > 600]
    prior = {gp: {k: statistics.fmean(1.0 if r["sog"] >= k else 0.0 for r in recent if (r["position"] == "D") == (gp == "D"))
                  for k in SOG_K} for gp in ("F", "D")}

    def shr(vals, pr, k=8):
        return (sum(vals) + k * pr) / (len(vals) + k)

    latest: dict = {}
    for r in rows:
        if r["player_id"] not in latest or r["game_date"] > latest[r["player_id"]][0]:
            latest[r["player_id"]] = (r["game_date"], r["team"], r["player_name"], r["position"])
    season = int(date[:4]) * 10000 + int(date[:4]) + 1 if int(date[5:7]) >= 8 else (int(date[:4]) - 1) * 10000 + int(date[:4])
    model: dict = {}
    for pid, (_d, team, name, pos) in latest.items():
        team = curteam.get(pid, team)
        if team not in today or pos == "G" or pid not in dressed.get(team, set()):
            continue
        opp, is_home, _gid = today[team]
        hist = index.history_as_of(pid, date)
        if len(hist) < 20:
            continue
        tp = [g for g in sched[team] if g["game_date"] < date]
        if not ptf.projected_active(hist, tp):
            continue
        l20, l60 = hist[-20:], hist[-60:]
        if statistics.fmean(r["icetime_seconds"] for r in l20) < 720:
            continue
        grp = "D" if pos == "D" else "F"
        probs = {}
        for k in SOG_K:
            probs[f"SOG{k}"] = min(shr([1.0 if r["sog"] >= k else 0.0 for r in l20], prior[grp][k]),
                                   shr([1.0 if r["sog"] >= k else 0.0 for r in l60], prior[grp][k]))
        v = project_player_points(rows, index, sched, toff, oenv, lavg, pw, palpha, {}, pid, team, opp, date, season)
        if v["status"] == "PROJECTED_ACTIVE":
            mu = v["expected_points"] * (ph if is_home else pa)
            emp = lambda n: sum(1 for r in l60 if r["points"] >= n) / len(l60)  # noqa: E731
            probs["PTS1"] = 0.5 * tp_pts(mu, palpha, 1) + 0.5 * emp(1)
            probs["PTS2"] = 0.5 * tp_pts(mu, palpha, 2) + 0.5 * emp(2)
        model[f"{norm_name(name)}|{team}"] = {"player_id": str(pid), "name": name, "team": team, "opp": opp,
                                              "home": is_home, "probs": probs}
    return model


def compute_model_v2(today: dict, dressed: dict, curteam: dict, date: str, *, rows: list | None = None) -> dict:
    """Same output shape as compute_model(), from the chronologically validated skater model
    (research/product_models) with its calibration. Only players who dressed in their team's last real game, have at
    least live.MIN_GAMES_FOR_PRICING games of history and an expected 12+ minutes qualify, as before."""
    from research.product_models import live
    state = live.build(rows)
    model: dict = {}
    for pid, p in state["players"].items():
        team = curteam.get(pid, p["team"])
        if team not in today or p["position"] == "G" or pid not in dressed.get(team, set()):
            continue
        opp, is_home, _gid = today[team]
        proj = live.project_matchup(state, pid, opp)
        if proj is None or not proj["pricing_eligible"] or proj["expected"]["toi"] < MIN_EXPECTED_TOI:
            continue
        pr = proj["probabilities"]
        probs = {f"SOG{k}": pr[f"shots>={k}"]["calibrated"] for k in SOG_K}
        probs["PTS1"], probs["PTS2"] = pr["points>=1"]["calibrated"], pr["points>=2"]["calibrated"]
        probs["GOAL1"] = pr["goals>=1"]["calibrated"]
        model[f"{norm_name(p['name'])}|{team}"] = {"player_id": str(pid), "name": p["name"], "team": team, "opp": opp,
                                                    "home": is_home, "probs": probs}
    return model


def _load_or_build_model(conn, today: dict, date: str, now: dt.datetime | None = None) -> dict:
    from research.product_models import live
    as_of = (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y-%m-%dT%H:%M:%S")
    dressed, curteam = _dressed_and_current_team(conn, list(today), as_of)
    signature = hashlib.sha256(json.dumps({t: sorted(v) for t, v in sorted(dressed.items())}).encode()).hexdigest()[:16]
    cache_path = state_paths.path(MODEL_CACHE_NAME)
    rows = checksum = None
    if cache_path.exists():
        cached = json.loads(cache_path.read_text())
        _, checksum = _current_season_rows()
        if (cached.get("date") == date and cached.get("mp_checksum") == checksum and cached.get("dressed") == signature
                    and cached.get("version") == MODEL_VERSION
                    and cached.get("min_games") == live.MIN_GAMES_FOR_PRICING):
            return cached["model"]
    _rows, checksum = _current_season_rows()
    model = compute_model_v2(today, dressed, curteam, date)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({"date": date, "mp_checksum": checksum, "dressed": signature,
                                      "version": MODEL_VERSION, "model": model,
                                      "min_games": live.MIN_GAMES_FOR_PRICING}))
    return model


# ------------------------------------------------------------------- capture --

def _parse_utc(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def _archive_dir() -> Path:
    from research.live_sog_pricing import archive
    return Path(archive.ARCHIVE_DIR)


def latest_capture(event_id: str, archive_dir: Path | None = None, *,
                   require_points: bool = True, market: str | None = None) -> tuple[dt.datetime, dict] | None:
    """Newest archived payload for one provider event. By default only this
    job's own captures (shots-alternate AND points) count -- that is what the
    capture cadence keys on. require_points=False accepts any capture that
    includes the shots-alternate market (e.g. the prop sweeps'), which is what
    pricing wants: the freshest real DraftKings shots prices, whoever pulled them."""
    archive_dir = archive_dir or _archive_dir()
    best = None
    for path in glob.glob(str(archive_dir / f"*events-{event_id}-odds*.json")):
        try:
            doc = json.loads(Path(path).read_text())
        except (OSError, json.JSONDecodeError):
            continue
        meta = doc.get("meta") or {}
        filt = meta.get("market_filter") or ""
        if market is not None:
            if market not in filt:
                continue
        elif "player_shots_on_goal_alternate" not in filt or (require_points and "player_points" not in filt):
            continue
        ts = meta.get("retrieved_at_utc")
        if not ts:
            continue
        got = _parse_utc(ts)
        if best is None or got > best[0]:
            best = (got, doc["response"])
    return best


def credits_spent_today_by_this_job(now: dt.datetime, archive_dir: Path | None = None) -> int:
    archive_dir = archive_dir or _archive_dir()
    midnight = dt.datetime.combine(now.date(), dt.time.min, dt.timezone.utc)
    total = 0
    for path in glob.glob(str(archive_dir / f"{now.strftime('%Y%m%d')}T*events-*odds*.json")):
        try:
            meta = json.loads(Path(path).read_text()).get("meta") or {}
        except (OSError, json.JSONDecodeError):
            continue
        filt = meta.get("market_filter") or ""
        if "player_points" in filt and "player_shots_on_goal_alternate" in filt and meta.get("retrieved_at_utc"):
            if _parse_utc(meta["retrieved_at_utc"]) >= midnight:
                total += int(meta.get("requests_last_header") or 0)
    return total


def price_age_limit_min(hours_to_start: float) -> float:
    return MAX_PRICE_AGE_MIN_FAR if hours_to_start >= 2.0 else MAX_PRICE_AGE_MIN_NEAR


def capture_decision(hours_to_start: float, last_capture_age_min: float | None) -> str | None:
    """'FIRST' / 'REFRESH' / None -- pure, so the cadence is testable. A refresh is due once the
    newest price is within REFRESH_LEAD_MIN of the freshness limit that applies at this distance
    from puck drop, so a price is replaced before the selector would reject it as stale."""
    if hours_to_start <= 0 or hours_to_start > CAPTURE_HORIZON_H:
        return None
    if last_capture_age_min is None:
        return "FIRST"
    if last_capture_age_min >= price_age_limit_min(hours_to_start) - REFRESH_LEAD_MIN:
        return "REFRESH"
    return None


SOG_MARKET_KEY = "player_shots_on_goal_alternate"
POINTS_MARKET_KEY = "player_points"
GOALS_MARKET_KEY = "player_goal_scorer_anytime"       # optional: captured only when the month balances (operational/credit_allocation.py)


def decision_age_min(event_id: str, now: dt.datetime, markets: tuple[str, ...] = (SOG_MARKET_KEY, POINTS_MARKET_KEY)) -> float | None:
    """Age of the OLDER of the newest captures of the markets that are being bought (shots and points by default; by any job); None if any is missing."""
    ages = []
    for market in markets:
        cap = latest_capture(event_id, market=market)
        if cap is None:
            return None
        ages.append((now - cap[0]).total_seconds() / 60.0)
    return max(ages)


def match_game(event: dict, games: dict) -> tuple[str, dict] | None:
    """The nhl.db game (id, info) a provider event is, by team abbreviations."""
    from research.live_sog_pricing import event_mapping
    home = event_mapping.normalize_team_name(event.get("home_team", ""))
    away = event_mapping.normalize_team_name(event.get("away_team", ""))
    for gid, g in games.items():
        if g["home"] == home and g["away"] == away:
            return gid, g
    return None


def effective_start(provider_commence_utc: str, official_start_utc: str | None) -> dt.datetime:
    """Start time used for every cutoff (capture window, freshness limit, 'not started'): the EARLIER of the
    provider's commence_time and the official NHL schedule. Never the later one, so a leg is never offered
    after either source says the game began. Display and settlement use the official schedule."""
    def parse(value: str) -> dt.datetime:
        parsed = _parse_utc(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)   # nhl.db stores naive UTC
    provider = parse(provider_commence_utc)
    if not official_start_utc:
        return provider
    return min(provider, parse(official_start_utc))


def planned_decision(plan: dict, game_id: str | None, hours: float, age_min: float | None, now: dt.datetime) -> tuple[str | None, str]:
    """(decision, reason) under the day's credit plan. FIRST: one capture per priced game, inside its actionable window (a price taken
    FIRST_CAPTURE_HOURS before puck drop is still inside the near-game freshness limit at puck drop). REFRESH only with leftover credits."""
    from operational import credit_planner as cp
    if hours <= 0 or hours > CAPTURE_HORIZON_H:
        return None, "OUTSIDE_HORIZON"
    if game_id is None or str(game_id) not in set(plan["games_priced"]):
        return None, "NOT_IN_CREDIT_PLAN"
    # A capture taken BEFORE the actionable window (an older cadence, a manual pull) is not this game's planned capture: it will be stale at puck drop.
    in_window_capture = age_min is not None and (hours + age_min / 60.0) <= cp.FIRST_CAPTURE_HOURS + 0.05
    if not in_window_capture:
        return ("FIRST", "OK") if hours <= cp.FIRST_CAPTURE_HOURS else (None, "BEFORE_ACTIONABLE_WINDOW")
    if capture_decision(hours, age_min) != "REFRESH":
        return None, "FRESH_ENOUGH"
    left = plan["allowance"].get(cp.REFRESH, 0.0) - cp.spent_today(now).get(cp.REFRESH, 0.0)
    return ("REFRESH", "OK") if left >= EST_COST_PER_EVENT else (None, "NO_CREDITS_FOR_REFRESH")


def capture_prices(now: dt.datetime, *, client=None, archive_mod=None, guard=None, games: dict | None = None, plan: dict | None = None) -> dict:
    """Per-game DraftKings prop captures. With a day plan (production) the credit planner decides which games are priced, with which markets and
    how often; without one (tests, manual use) the legacy cadence and the month rule for the goals market apply."""
    from operational import odds_quota
    from research.live_sog_pricing import archive as _archive, client as _client
    client = client or _client
    archive_mod = archive_mod or _archive
    guard = guard or (lambda planned: odds_quota.guard(
        planned=planned, now=now, soft_multiplier=odds_quota.PREGAME_SOFT_MULTIPLIER))
    summary = {"events_seen": 0, "events_captured": 0, "credits_spent": 0, "skipped": [], "error": None}
    events = client.get_nhl_events()
    if not events.ok:
        summary["error"] = events.error
        return summary
    spent_today = credits_spent_today_by_this_job(now)
    games = games or {}
    if plan is None:
        from operational import credit_allocation
        goals = credit_allocation.goals_decision(now)
        legacy_markets = f"{MARKETS},{GOALS_MARKET_KEY}" if goals["allow"] else MARKETS
        legacy_cost = EST_COST_PER_EVENT + (1 if goals["allow"] else 0)
        summary["goals_market"] = {k: goals.get(k) for k in ("allow", "reason", "shortfall", "extra_credits_per_day")}
    else:
        summary["plan"] = {"day": plan.get("day"), "D": plan["budget"].get("D"), "games_priced": len(plan["games_priced"]),
                           "games_not_priced": len(plan["games_not_priced"]), "goals_games": len(plan["goals_games"])}

    def start_of(e):
        matched = match_game(e, games)
        return effective_start(e["commence_time"], matched[1]["start_utc"] if matched else None)

    upcoming = sorted((e for e in events.data if start_of(e) > now), key=start_of)
    for e in upcoming:
        hours = (start_of(e) - now).total_seconds() / 3600.0
        from operational import credit_planner as _cp
        age_min = decision_age_min(e["id"], now, _cp.prop_markets() if plan is not None else (SOG_MARKET_KEY, POINTS_MARKET_KEY))
        matched = match_game(e, games)
        gid = matched[0] if matched else None
        if plan is None:
            decision, markets, est_cost, klass = capture_decision(hours, age_min), legacy_markets, legacy_cost, None
            why = "OK"
        else:
            from operational import credit_planner as cp
            decision, why = planned_decision(plan, gid, hours, age_min, now)
            klass = cp.PROPS if decision == "FIRST" else cp.REFRESH
            with_goals = decision == "FIRST" and str(gid) in set(plan["goals_games"])
            base_markets = ",".join(cp.prop_markets())
            markets = f"{base_markets},{GOALS_MARKET_KEY}" if with_goals else base_markets
            est_cost = cp.base_cost() + (cp.GOALS_COST if with_goals else 0)
        if decision is None:
            if why == "NOT_IN_CREDIT_PLAN":
                summary["skipped"].append({"event_id": e["id"], "game_id": gid, "reason": why})
            continue
        summary["events_seen"] += 1
        if plan is None and spent_today + est_cost > DAILY_CREDIT_CAP:
            summary["skipped"].append({"event_id": e["id"], "reason": "BEST_BETS_DAILY_CREDIT_CAP"})
            continue
        if plan is None:
            gate = guard(est_cost)
        else:
            # the goals market is its own class in the plan (its own allowance); a denied goals credit drops the goals market, not the game
            gate = cp.authorize(klass, cp.base_cost(), now, plan=plan)
            if gate.get("allow") and markets.endswith(GOALS_MARKET_KEY) and not cp.authorize(cp.GOALS, cp.GOALS_COST, now, plan=plan).get("allow"):
                markets, est_cost = ",".join(cp.prop_markets()), cp.base_cost()
        if not gate.get("allow"):
            summary["skipped"].append({"event_id": e["id"], "reason": gate.get("reason")})
            break
        r = client.get_event_odds(e["id"], markets=markets)
        if not r.ok:
            summary["skipped"].append({"event_id": e["id"], "reason": f"API_ERROR: {r.error}"})
            continue
        archive_mod.archive_result(r, event_id=e["id"], market_filter=markets, bookmaker_filter="draftkings")
        cost = int(r.requests_last or 0)
        if plan is not None:
            goals_part = cp.GOALS_COST if markets.endswith(GOALS_MARKET_KEY) and cost > cp.base_cost() else 0
            cp.record(klass, cost - goals_part, now, event=e["id"], game_id=gid, markets=markets)
            if goals_part:
                cp.record(cp.GOALS, goals_part, now, event=e["id"], game_id=gid, markets=markets)
        spent_today += cost
        summary["credits_spent"] += cost
        summary["events_captured"] += 1
    return summary


def _legs_from_payload(payload: dict, captured_at: dt.datetime, snapshot: dict, now: dt.datetime,
                       only_market: str | None = None) -> list:
    """ParlayLegs for DraftKings' Over prices -- shots-on-goal alternate ladder
    (k+ shots) and player_points (Over 0.5 / 1.5 = 1+ / 2+ points) -- for players
    the rolling-form model covers (dressed in their team's last game, 40+ games
    of history). Both contracts are certified against real archived payloads
    (provider_adapter.VERIFIED_CONTRACTS)."""
    from research.generic_prop_pricing import provider_adapter
    from research.generic_prop_pricing.line_mapping import SOG_ACTIONABLE_THRESHOLDS
    from research.live_sog_pricing import event_mapping
    from research.real_market_parlay.engine import ParlayLeg

    home = event_mapping.normalize_team_name(payload.get("home_team", ""))
    away = event_mapping.normalize_team_name(payload.get("away_team", ""))
    game_id = next((gid for gid, g in snapshot["games"].items() if g["home"] == home and g["away"] == away), None)
    if game_id is None:
        return []
    game = snapshot["games"][game_id]
    # Cutoffs use the earlier of the provider's and the official start (see effective_start); the leg
    # displays and stores the official start, and also keeps the provider's.
    hours = (effective_start(payload.get("commence_time") or game["start_utc"], game["start_utc"]) - now
             ).total_seconds() / 3600.0
    limit = price_age_limit_min(hours)
    verified = {"PLAYER_SOG_ALTERNATE": provider_adapter.is_contract_verified("draftkings", "PLAYER_SOG_ALTERNATE"),
                "PLAYER_POINTS": provider_adapter.is_contract_verified("draftkings", "PLAYER_POINTS"),
                "PLAYER_GOALS": provider_adapter.is_contract_verified("draftkings", "PLAYER_GOALS")}
    from research.real_market_parlay.engine import GOALS_ACTIONABLE_THRESHOLDS, POINTS_ACTIONABLE_THRESHOLDS
    markets = {"player_shots_on_goal_alternate": ("PLAYER_SOG_ALTERNATE", "SOG", SOG_ACTIONABLE_THRESHOLDS),
               "player_points": ("PLAYER_POINTS", "PTS", POINTS_ACTIONABLE_THRESHOLDS),
               GOALS_MARKET_KEY: ("PLAYER_GOALS", "GOAL", GOALS_ACTIONABLE_THRESHOLDS)}
    legs = []
    for bm in payload.get("bookmakers", []):
        if bm.get("key") != "draftkings":
            continue
        for m in bm.get("markets", []):
            if m.get("key") not in markets or (only_market and m.get("key") != only_market):
                continue
            family, prob_prefix, thresholds = markets[m["key"]]
            # The provider's own update time for this market (bookmaker-level time only if the market has none).
            # Retrieval time says when WE fetched; only this says how old the price is.
            quote = quote_freshness.assess(m.get("last_update") or bm.get("last_update"), captured_at.isoformat(),
                                           now, limit)
            fresh = hours > 0 and quote["fresh"]
            for o in m.get("outcomes", []):
                if m.get("key") == GOALS_MARKET_KEY:          # one-sided "Yes" prices: anytime goal == 1+ goals, no point, no "No" side
                    if o.get("name") != "Yes" or o.get("price") is None:
                        continue
                    k = 1
                elif o.get("name") != "Over" or o.get("point") is None or o.get("price") is None:
                    continue
                else:
                    k = int(o["point"] + 0.5)
                entry = None
                for team in (home, away):
                    entry = snapshot["model"].get(f"{norm_name(o.get('description', ''))}|{team}")
                    if entry:
                        break
                if not entry or f"{prob_prefix}{k}" not in entry["probs"] or not entry.get("player_id"):
                    continue
                legs.append(ParlayLeg(
                    game_id=game_id, event_id=payload.get("id"), market_family=family,
                    participant_id=entry["player_id"], participant_name=entry["name"], side="OVER", threshold=k,
                    american_price=float(o["price"]), conservative_probability=entry["probs"][f"{prob_prefix}{k}"],
                    sportsbook="draftkings", captured_at_utc=captured_at.isoformat(),
                    retrieved_at_utc=quote["retrieved_at_utc"], quote_updated_utc=quote["quote_updated_utc"],
                    quote_age_min=quote["quote_age_min"], freshness_status=quote["status"],
                    provider_contract_verified=verified[family], model_threshold_eligible=k in thresholds,
                    identity_resolved=True, price_fresh=fresh, event_not_started=hours > 0,
                    team=entry["team"], opponent=entry["opp"], game_start_utc=game["start_utc"],
                    provider_start_utc=payload.get("commence_time"), model_version=MODEL_VERSION))
    return legs


def candidate_legs(conn, now: dt.datetime) -> tuple[list, dict]:
    """Shots-on-goal ParlayLegs from the newest archived DraftKings capture of
    every game still to start today, priced against the rolling-form model.
    Returns (legs, report). The ticket selector decides what, if anything, to
    recommend; this only supplies legs."""
    snapshot = current_model(conn, now)
    report = {"games_upcoming": len(snapshot["games"]), "modelled_players": len(snapshot["model"]),
              "events_with_capture": 0, "legs": 0}
    if not snapshot["games"]:
        return [], report
    seen_events = set()
    for day in (now, now - dt.timedelta(days=1)):
        for payload_path in glob.glob(str(_archive_dir() / f"{day.strftime('%Y%m%d')}T*events-*odds*.json")):
            m = re.search(r"events-([0-9a-f]{32})-odds", payload_path)
            if m:
                seen_events.add(m.group(1))
    legs: list = []
    for event_id in sorted(seen_events):
        got_any = False
        for market in (SOG_MARKET_KEY, POINTS_MARKET_KEY, GOALS_MARKET_KEY):      # newest capture of EACH market, whichever job pulled it
            cap = latest_capture(event_id, market=market)
            if cap is None:
                continue
            got = _legs_from_payload(cap[1], cap[0], snapshot, now, only_market=market)
            got_any = got_any or bool(got)
            legs.extend(got)
        if got_any:
            report["events_with_capture"] += 1
    report["legs"] = len(legs)
    return legs, report


def _latest_events_listing(archive_dir: Path | None = None) -> list[dict]:
    """The newest archived (free) provider events listing: id, teams, commence_time."""
    files = sorted(glob.glob(str((archive_dir or _archive_dir()) / "*events_na_none.json")))
    for path in reversed(files):
        try:
            doc = json.loads(Path(path).read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(doc.get("response"), list):
            return doc["response"]
    return []


def capture_plan(now: dt.datetime, *, games: dict | None = None, hours_ahead: float = 30.0) -> list[dict]:
    """Per upcoming game: provider and official start times, which one the cutoffs use, when OUR capture
    window opens, the newest shots/points prices and their age, when they stop being fresh, and the
    next planned capture. The window is our own credit policy (CAPTURE_HORIZON_H), not the provider's
    availability: DraftKings has had these markets posted for days (docs/MARKET_COVERAGE_AUDIT.md)."""
    games = games or {}
    plan = []
    for e in _latest_events_listing():
        matched = match_game(e, games)
        official = matched[1]["start_utc"] if matched else None
        start = effective_start(e["commence_time"], official)
        hours = (start - now).total_seconds() / 3600.0
        if hours <= 0 or hours > hours_ahead:
            continue
        opens = start - dt.timedelta(hours=CAPTURE_HORIZON_H)
        shots, pts = latest_capture(e["id"], market=SOG_MARKET_KEY), latest_capture(e["id"], market=POINTS_MARKET_KEY)
        age = decision_age_min(e["id"], now)
        decision = capture_decision(hours, age)
        fmt = lambda t: t.strftime("%b %-d %H:%M") + " UTC"  # noqa: E731
        if age is None and now < opens:
            nxt = f"first capture on the first 15-minute trader cycle at or after {fmt(opens)}"
        elif decision:
            nxt = f"{decision} on the next 15-minute trader cycle"
        else:
            oldest = min(c[0] for c in (shots, pts))
            far_t = oldest + dt.timedelta(minutes=MAX_PRICE_AGE_MIN_FAR - REFRESH_LEAD_MIN)
            if (start - far_t).total_seconds() / 3600.0 >= 2.0:
                due = far_t
            else:
                due = max(oldest + dt.timedelta(minutes=MAX_PRICE_AGE_MIN_NEAR - REFRESH_LEAD_MIN),
                          start - dt.timedelta(hours=2.0))
            nxt = f"refresh on the first cycle at or after {fmt(due)}" if due < start else "none planned before puck drop"
        stale_at = None
        if shots and pts:
            oldest = min(shots[0], pts[0])
            stale_at = (oldest + dt.timedelta(minutes=price_age_limit_min(hours))).isoformat()
        plan.append({
            "provider_event_id": e["id"], "matchup": f"{e['away_team']} at {e['home_team']}",
            "provider_start_utc": e["commence_time"], "official_start_utc": official,
            "start_discrepancy_min": None if not official else round(
                (_parse_utc(e["commence_time"]) - _parse_utc(official)).total_seconds() / 60.0, 1),
            "cutoff_start_utc": start.isoformat().replace("+00:00", "Z"),
            "capture_window_opens_utc": opens.isoformat().replace("+00:00", "Z"),
            "newest_shots_price_utc": None if shots is None else shots[0].isoformat(),
            "newest_points_price_utc": None if pts is None else pts[0].isoformat(),
            "price_age_min": None if age is None else round(age, 1),
            "prices_stop_being_fresh_utc": stale_at, "next_planned_capture": nxt})
    plan.sort(key=lambda r: r["cutoff_start_utc"])
    return plan


def _content_hash(state: dict) -> str:
    body = {k: v for k, v in state.items() if k not in ("generated_at_utc", "capture")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def read_state() -> dict | None:
    path = _state_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def refresh(now: dt.datetime | None = None, *, conn=None, capture: bool = True, client=None) -> dict:
    """Capture (bounded) -> model (cached per day/lineup/data) -> picks -> state file.
    Returns {"status", "changed", ...}. Never raises into a scheduled job."""
    now = now or dt.datetime.now(dt.timezone.utc)
    if state_paths.under_test() and client is None and conn is None:
        return {"status": "SKIPPED", "reason": "UNDER_TEST", "changed": False}
    lock_path = state_paths.path(LOCK_NAME)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(lock_path, "w")
    try:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return {"status": "SKIPPED", "reason": "ANOTHER_REFRESH_IN_PROGRESS", "changed": False}
        return _refresh_locked(now, conn=conn, capture=capture, client=client)
    except Exception as exc:  # noqa: BLE001 -- a scheduled job must never crash on this
        return {"status": "ERROR", "reason": f"{exc.__class__.__name__}: {exc}", "changed": False}
    finally:
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()


def current_model(conn, now: dt.datetime, *, upcoming_only: bool = True) -> dict:
    """Today's (ET) not-yet-started games and the second-opinion player model.
    {"date", "games": {game_id: {"home", "away", "start_utc"}}, "model": {...}}"""
    from operational import eastern_time as et
    date = et.eastern_today(now)
    rows = conn.execute(
        "select game_id, home_team, away_team, scheduled_start_utc from games "
        "where game_date=? and game_state='SCHEDULED'", (date,)).fetchall()
    today, games = {}, {}
    for r in rows:
        start = r["scheduled_start_utc"] if r["scheduled_start_utc"].endswith("Z") else r["scheduled_start_utc"] + "Z"
        if upcoming_only and _parse_utc(start) <= now:
            continue
        today[r["home_team"]] = (r["away_team"], True, r["game_id"])
        today[r["away_team"]] = (r["home_team"], False, r["game_id"])
        games[str(r["game_id"])] = {"home": r["home_team"], "away": r["away_team"], "start_utc": start}
    model = _load_or_build_model(conn, today, date, now) if today else {}
    return {"date": date, "games": games, "model": model}


def _day_plan(conn, now: dt.datetime) -> dict | None:
    """The saved credit plan for today's (ET) games; None outside production so tests and manual runs keep the legacy cadence."""
    from operational import credit_planner as cp, eastern_time as et, goalie_confirmations, odds_quota
    if not cp.enforced():
        return None
    day = et.eastern_today(now)
    rows = conn.execute("SELECT game_id, scheduled_start_utc FROM games WHERE game_date = ?", (day,)).fetchall()
    starts = {str(r["game_id"]): _parse_utc(r["scheduled_start_utc"] if r["scheduled_start_utc"].endswith("Z") else r["scheduled_start_utc"] + "Z") for r in rows}
    return cp.day_plan(now, day, starts, remaining=odds_quota.latest_remaining(),
                       confirmed_games={g for g in goalie_confirmations.confirmed_games(conn, now)})


def _refresh_locked(now, *, conn, capture, client) -> dict:
    import db
    owns = conn is None
    conn = conn or db.get_conn()
    try:
        # Capture first, so the model and the downstream selector see the
        # prices just pulled. current_model() needs only the schedule.
        snapshot = current_model(conn, now)
        plan = _day_plan(conn, now) if (capture and snapshot["games"]) else None
        capture_summary = (capture_prices(now, client=client, games=snapshot["games"], plan=plan)
                           if (capture and snapshot["games"]) else None)
        state = {
            "status": "OK" if snapshot["games"] else "NO_UPCOMING_GAMES",
            "date_et": snapshot["date"], "generated_at_utc": now.isoformat(),
            "games_today_upcoming": len(snapshot["games"]),
            "modelled_players": len(snapshot["model"]),
            "capture": capture_summary,
        }
        old = read_state()
        changed = old is None or _content_hash(old) != _content_hash(state)
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1, default=str))
        tmp.replace(path)
        return {"status": state["status"], "changed": changed, "capture": capture_summary,
                "modelled_players": state["modelled_players"]}
    finally:
        if owns:
            conn.close()


if __name__ == "__main__":
    print(json.dumps(refresh(), indent=2, default=str))
