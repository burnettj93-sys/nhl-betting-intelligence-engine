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
MODEL_CACHE_SCHEMA = 5                                              # 5: player names are the NHL's spelling and same-name players are withdrawn (a cached model with MoneyPuck's accent-stripped names or an overwritten twin must not be served); 4: the record also says how many of its games are this season's (n_current); 2: entries carry "emp" (last-30-game shot frequencies); 3: the record is topped up from NHL API final games after MoneyPuck's newest
PREVIOUS_MODEL_VERSION = "EXPERIMENTAL-rolling-l20-l60-shrunk-v1"   # compute_model() below; kept so earlier tickets stay interpretable
MIN_EXPECTED_TOI = 12.0
from operational.pricing_policy import PROVISIONAL_MAX_AGE_MIN  # noqa: E402  (shared with the hosted pages; see that module)
from operational.quote_freshness import PRICE_LIMIT_FAR_MIN as MAX_PRICE_AGE_MIN_FAR, PRICE_LIMIT_NEAR_MIN as MAX_PRICE_AGE_MIN_NEAR     # game >= 2h away / < 2h away (one definition: operational/quote_freshness.py)
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


def roster_duplicate_keys(conn, teams, as_of: str) -> dict:
    """{"name|TEAM": [ids]} for every team in `teams` whose WHOLE current roster (not just the players who qualify for pricing) holds two players of one name: a price that carries only the name cannot
    be matched to one of them, even if the other twin is scratched, short of minutes or short of history."""
    from features import point_in_time as pit
    from operational import player_integrity
    out: dict = {}
    for team in teams:
        try:
            ids = pit.roster_ids_for_team(conn, team, as_of)
            names = {str(pid): (conn.execute("SELECT full_name FROM players WHERE player_id = ?", (pid,)).fetchone() or [None])[0] for pid in ids}
        except Exception:  # noqa: BLE001
            continue
        for nm, pids in player_integrity.duplicate_names({k: v for k, v in names.items() if v}).items():
            out[f"{nm}|{team}"] = pids
    return out


def _put_player(model: dict, collisions: dict, key: str, entry: dict) -> None:
    """Add a player to the pricing model; a SECOND player under the same name|team key (two Elias Pettersons on Vancouver) is a collision, not an overwrite: the name cannot be matched to one person."""
    if key in model or key in collisions:
        collisions.setdefault(key, []).append(entry)
        if key in model:
            collisions[key].append(model.pop(key))
        return
    model[key] = entry


def _withdraw_collisions(model: dict, collisions: dict, date: str) -> None:
    """Record the withdrawn ambiguous names (shown on the board and in the audit) and keep them out of every join."""
    from operational import player_integrity
    items = [{"team": v[0]["team"], "name": v[0]["name"], "ids": sorted({str(e["player_id"]) for e in v})} for v in collisions.values()]
    try:
        player_integrity.record_ambiguous(date, items, merge=True)
    except OSError:
        pass


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
    collisions: dict = {}
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
        _put_player(model, collisions, f"{norm_name(name)}|{team}", {"player_id": str(pid), "name": name, "team": team, "opp": opp, "home": is_home, "probs": probs})
    _withdraw_collisions(model, collisions, date)
    return model


def _now_naive_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat()


def nhl_shot_rows_after(conn, newest_date: str | None, as_of: str | None = None) -> list[dict]:
    """The NHL API's player-game shot counts for FINAL games dated after the newest MoneyPuck game (MoneyPuck publishes a game's log the following afternoon, so its history is a day or two behind
    the schedule). Used only to bring the last-30-game SHOT RECORD up to date; the projection model keeps its MoneyPuck inputs. Where both sources cover a game the shot counts agree for 98% of
    player-games (693 of 704 on 2026-10-09..10). Read through features/point_in_time (latest revision observed by `as_of`); a game not final or a row without a shot count is skipped."""
    if conn is None or not newest_date:
        return []
    from features import point_in_time as pit
    return [{"player_id": str(r["player_id"]), "date": r["date"], "shots": float(r["shots"])} for r in pit.player_shots_since(conn, newest_date, as_of or _now_naive_utc())]


def nhl_data_through(conn, as_of: str | None = None) -> str | None:
    if conn is None:
        return None
    try:
        from features import point_in_time as pit
        return pit.final_stats_through(conn, as_of or _now_naive_utc())
    except Exception:  # noqa: BLE001
        return None


def compute_model_v2(today: dict, dressed: dict, curteam: dict, date: str, *, rows: list | None = None, conn=None) -> dict:
    """Same output shape as compute_model(), from the chronologically validated skater model
    (research/product_models) with its calibration. Only players who dressed in their team's last real game, have at
    least live.MIN_GAMES_FOR_PRICING games of history and an expected 12+ minutes qualify, as before."""
    from research.product_models import live
    state = live.build(rows)
    extra = nhl_shot_rows_after(conn, state.get("newest_game_date"))
    if extra:                                              # bring the shot record up to the latest final games the NHL API has (the model itself stays on MoneyPuck)
        from research.product_models import history
        state["shot_freq"] = live.shot_frequencies(list(rows if rows is not None else history.skater_games()) + extra)
    model: dict = {}
    collisions: dict = {}
    from operational import player_integrity
    nhl_names = player_integrity.official_names(conn) if conn is not None else {}
    if conn is not None:
        as_of_now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        for key, pids in roster_duplicate_keys(conn, list(today), as_of_now).items():
            team = key.rsplit("|", 1)[1]
            collisions[key] = [{"player_id": x, "name": nhl_names.get(x) or key.split("|")[0], "team": team} for x in pids]
    for pid, p in state["players"].items():
        p = {**p, "name": nhl_names.get(str(pid)) or p["name"]}          # the NHL's spelling, not MoneyPuck's accent-stripped one: otherwise the provider's name never matches
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
        freq = state.get("shot_freq", {}).get(pid) or {}
        emp = {f"SOG{k}": {"p": c["p"], "hits": c["hits"], "n": c["n"], "n_current": c.get("n_current")} for k, c in freq.items() if k in SOG_K}
        _put_player(model, collisions, f"{norm_name(p['name'])}|{team}", {"player_id": str(pid), "name": p["name"], "team": team, "opp": opp, "home": is_home, "probs": probs, "emp": emp})
    _withdraw_collisions(model, collisions, date)
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
                    and cached.get("version") == MODEL_VERSION and cached.get("schema") == MODEL_CACHE_SCHEMA and cached.get("nhl_through") == nhl_data_through(conn)
                    and cached.get("min_games") == live.MIN_GAMES_FOR_PRICING):
            return cached["model"]
    _rows, checksum = _current_season_rows()
    model = compute_model_v2(today, dressed, curteam, date, conn=conn)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({"date": date, "mp_checksum": checksum, "dressed": signature,
                                      "version": MODEL_VERSION, "schema": MODEL_CACHE_SCHEMA, "nhl_through": nhl_data_through(conn), "model": model,
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
SAVES_MARKET_KEY = "player_total_saves"
GOALS_MARKET_KEY = "player_goal_scorer_anytime"       # optional: captured only when the month balances (operational/credit_allocation.py)


def _bought_markets() -> tuple[str, ...]:
    """The per-game markets the credit plan buys (shots only unless the owner adds points); the legacy un-planned path buys both."""
    from operational import credit_planner as cp
    return tuple(cp.prop_markets()) if cp.enforced() else (SOG_MARKET_KEY, POINTS_MARKET_KEY)


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


def _posted_in(payload: dict, market: str) -> bool:
    for bm in (payload or {}).get("bookmakers") or []:
        if bm.get("key") == "draftkings":
            for m in bm.get("markets") or []:
                if m.get("key") == market and m.get("outcomes"):
                    return True
    return False


def _captured_times(event_id: str, markets: list[str], archive_dir: Path | None = None) -> dict:
    """{market: when the newest capture that actually CONTAINED that market was retrieved}. An answer with nothing in it (the bookmaker had not posted the market)
    is not a capture of it: that case is tracked as NOT_POSTED in operational/price_availability.py and re-asked after capture_schedule.RECHECK_MINUTES."""
    archive_dir = archive_dir or _archive_dir()
    out: dict = {m: None for m in markets}
    for path in glob.glob(str(archive_dir / f"*events-{event_id}-odds*.json")):
        try:
            doc = json.loads(Path(path).read_text())
        except (OSError, json.JSONDecodeError):
            continue
        meta = doc.get("meta") or {}
        filt, ts = meta.get("market_filter") or "", meta.get("retrieved_at_utc")
        if not ts:
            continue
        got = _parse_utc(ts)
        for m in markets:
            if m in filt and (out[m] is None or got > out[m]) and _posted_in(doc.get("response") or {}, m):
                out[m] = got
    return out


def _posted_context(day: str, game_id: str | None) -> dict:
    """{market: UTC time of the newest answer today in which DraftKings HAD posted the market} for one game, from the availability record (independent of the archive files)."""
    from operational import price_availability as pa, quote_freshness as qf
    g = (pa.read(day).get("games") or {}).get(str(game_id)) or {}
    return {m: qf.parse_utc(v.get("checked_utc")) for m, v in (g.get("markets") or {}).items() if v.get("status") == pa.POSTED}


def _availability_context(day: str, game_id: str | None) -> dict:
    """{market: UTC time of the newest 'we asked and the bookmaker had not posted it' answer today} for one game."""
    from operational import price_availability as pa, quote_freshness as qf
    g = (pa.read(day).get("games") or {}).get(str(game_id)) or {}
    return {m: qf.parse_utc(v.get("checked_utc")) for m, v in (g.get("markets") or {}).items() if v.get("status") == pa.NOT_POSTED}


def _record_answer(day: str, gid, requested: list[str], r, now: dt.datetime, hours: float, matchup: str | None, game_date: str | None, start_utc: str | None = None) -> dict:
    """Turn one provider answer into per-market availability (POSTED / NOT_POSTED, or FETCH_ERROR when the request failed)."""
    from operational import price_availability as pa
    if not r.ok:
        for m in requested:
            pa.record(day, gid, m, pa.FETCH_ERROR, now, hours_to_start=hours, detail=str(getattr(r, "error", None)), matchup=matchup, game_date=game_date, start_utc=start_utc)
        return {}
    got = pa.classify_payload(r.data if isinstance(r.data, dict) else None, requested)
    for m, info in got.items():
        pa.record(day, gid, m, info["status"], now, hours_to_start=hours, outcomes=info["outcomes"], last_update=info["last_update"],
                  matchup=matchup, game_date=game_date, start_utc=start_utc)
    return got


def capture_prices(now: dt.datetime, *, client=None, archive_mod=None, guard=None, games: dict | None = None, plan: dict | None = None) -> dict:
    """Per-game DraftKings prop captures. With a day plan (production) the coordinated WAVE captures run (capture_wave_prices: operational/capture_timeline.py, operational/credit_planner.py); every
    answer, including 'the bookmaker has not posted this', is recorded in operational/price_availability.py. Without a plan (tests, manual use) the legacy cadence below applies: one capture per
    event as it nears puck drop, guarded by the quota rule and the job's own daily cap."""
    from operational import odds_quota
    from research.live_sog_pricing import archive as _archive, client as _client
    client = client or _client
    archive_mod = archive_mod or _archive
    if plan is not None:
        return capture_wave_prices(now, client=client, archive_mod=archive_mod, games=games, plan=plan)
    guard = guard or (lambda planned: odds_quota.guard(
        planned=planned, now=now, soft_multiplier=odds_quota.PREGAME_SOFT_MULTIPLIER))
    summary = {"events_seen": 0, "events_captured": 0, "credits_spent": 0, "skipped": [], "error": None, "slots": {}}
    events = client.get_nhl_events()
    if not events.ok:
        summary["error"] = events.error
        return summary
    spent_today = credits_spent_today_by_this_job(now)
    games = games or {}
    from operational import credit_allocation
    goals = credit_allocation.goals_decision(now)
    legacy_markets = f"{MARKETS},{GOALS_MARKET_KEY}" if goals["allow"] else MARKETS
    legacy_cost = EST_COST_PER_EVENT + (1 if goals["allow"] else 0)
    summary["goals_market"] = {k: goals.get(k) for k in ("allow", "reason", "shortfall", "extra_credits_per_day")}

    def start_of(e):
        matched = match_game(e, games)
        return effective_start(e["commence_time"], matched[1]["start_utc"] if matched else None)

    upcoming = sorted((e for e in events.data if start_of(e) > now), key=start_of)
    for e in upcoming:
        hours = (start_of(e) - now).total_seconds() / 3600.0
        age_min = decision_age_min(e["id"], now, (SOG_MARKET_KEY, POINTS_MARKET_KEY))
        decision, markets, est_cost = capture_decision(hours, age_min), legacy_markets, legacy_cost
        summary["slots"][e["id"]] = {"slot": None, "decision": decision, "why": "OK"}
        if decision is None:
            continue
        summary["events_seen"] += 1
        if spent_today + est_cost > DAILY_CREDIT_CAP:
            summary["skipped"].append({"event_id": e["id"], "reason": "BEST_BETS_DAILY_CREDIT_CAP"})
            continue
        gate = guard(est_cost)
        if not gate.get("allow"):
            summary["skipped"].append({"event_id": e["id"], "reason": gate.get("reason")})
            break
        r = client.get_event_odds(e["id"], markets=markets)
        if not r.ok:
            summary["skipped"].append({"event_id": e["id"], "reason": f"API_ERROR: {r.error}"})
            continue
        archive_mod.archive_result(r, event_id=e["id"], market_filter=markets, bookmaker_filter="draftkings")
        cost = int(r.requests_last or 0)
        spent_today += cost
        summary["credits_spent"] += cost
        summary["events_captured"] += 1
    return summary


WAVE_STATE_NAME = "wave_capture_state.json"
RETRY_MINUTES = 20.0              # a market that was not posted when asked is asked again after this long (a request that returns nothing costs 0 credits)


def _wave_state_path():
    return state_paths.path(WAVE_STATE_NAME)


def _load_wave_state(day: str) -> dict:
    try:
        d = json.loads(_wave_state_path().read_text())
        return d if d.get("day") == day else {"day": day, "waves": {}}
    except (OSError, json.JSONDecodeError):
        return {"day": day, "waves": {}}


def _save_wave_state(doc: dict) -> None:
    try:
        p = _wave_state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, indent=1, sort_keys=True, default=str))
        tmp.replace(p)
    except OSError:
        pass


def _class_of(market: str) -> str:
    from operational import credit_planner as cp
    return {cp.SHOTS_KEY: cp.PROPS, cp.GOALS_KEY: cp.GOALS, cp.SAVES_KEY: cp.SAVES, cp.POINTS_KEY: cp.POINTS_ADDON}.get(market, cp.PROPS)


def capture_wave_prices(now: dt.datetime, *, client=None, archive_mod=None, games: dict | None = None, plan: dict | None = None) -> dict:
    """Coordinated player-odds captures by start-time wave (docs/ODDS_PLAN_DECISION.md, operational/capture_timeline.py).

    For each wave of the day's plan, from its target (90 minutes before its first game) until LAST_CHANCE (15 minutes before it), every SELECTED game gets ONE capture of the markets the plan chose for it
    (shots ladder + anytime goal + goalie saves; points only from surplus), in the same cycle, so the wave's prices can be compared at one time. A capture after the 60-minute publish deadline is recorded
    LATE (a sleep or an outage), never skipped; after LAST_CHANCE the window is MISSED (reported by note_missed_pregame_windows) and nothing is bought. Markets already captured today are never bought
    again; a market DraftKings had not posted when asked is asked again after RETRY_MINUTES (0 credits until it is posted). Each credit is RESERVED before the call and SETTLED to the actual charge."""
    from operational import capture_timeline as ct, credit_planner as cp, eastern_time as et, odds_quota, price_availability as pa
    from research.live_sog_pricing import archive as _archive, client as _client
    client = client or _client
    archive_mod = archive_mod or _archive
    games = games or {}
    summary = {"events_seen": 0, "events_captured": 0, "credits_spent": 0, "skipped": [], "error": None, "slots": {}, "mode": "WAVES",
               "plan": {"day": plan.get("day"), "games_priced": len(plan.get("games_priced") or []), "games_not_priced": len(plan.get("games_not_priced") or []),
                        "waves": len(plan.get("waves") or []), "league_pulls_planned": (plan.get("league_pulls") or {}).get("planned")}}
    day = plan.get("day")
    selected_all = set(plan.get("games_priced") or [])
    if not selected_all:
        return summary
    events = client.get_nhl_events()                              # free
    if not events.ok:
        summary["error"] = events.error
        return summary
    by_gid = {}
    for e in events.data:
        m = match_game(e, games)
        if m:
            by_gid[str(m[0])] = (e, m[1])
    state = _load_wave_state(day)
    for wdoc in plan.get("waves") or []:
        w = ct.Wave(wdoc["index"], _parse_utc(wdoc["first_start_utc"]), tuple(wdoc["games"]))
        chosen = [g for g in wdoc.get("selected") or [] if g in selected_all]
        if not chosen or now < w.target or now > w.last_chance:
            summary["slots"][f"wave{w.index}"] = {"state": ct.NOT_YET if now < w.target else "CLOSED", "selected": len(chosen)}
            continue
        wstate = state["waves"].setdefault(str(w.index), {"games": {}})
        late = now > w.deadline
        for gid in chosen:
            if gid not in by_gid:
                summary["skipped"].append({"game_id": gid, "reason": "EVENT_NOT_LISTED_BY_THE_PROVIDER"})
                pa.note_unfetched(day, gid, cp.SHOTS_KEY, pa.FETCH_ERROR, now, detail="the provider's event list does not include this game", game_date=day)
                continue
            e, g_info = by_gid[gid]
            start = effective_start(e["commence_time"], g_info["start_utc"])
            if start <= now:
                continue
            if (start - now).total_seconds() < ct.LAST_CHANCE_MIN * 60:
                summary["skipped"].append({"game_id": gid, "reason": "GAME_STARTS_WITHIN_THE_LAST_CHANCE_LIMIT"})       # the game's own start (the provider may have moved it earlier) closes its window
                continue
            wanted = list((plan.get("game_markets") or {}).get(gid) or [])
            got = _captured_times(e["id"], wanted)
            for m_, t_ in _posted_context(day, gid).items():
                if t_ is not None and (got.get(m_) is None or t_ > got[m_]):
                    got[m_] = t_                                       # the availability record counts too: a capture is a capture even if the archive file cannot be read
            avail_ctx = _availability_context(day, gid)
            missing = []
            for m in wanted:
                if got.get(m) is not None and got[m] >= w.target - dt.timedelta(minutes=30):
                    continue                                       # already captured for this wave: never bought again
                asked = avail_ctx.get(m)
                if asked is not None and (now - asked).total_seconds() / 60.0 < RETRY_MINUTES and asked >= w.target - dt.timedelta(minutes=30):
                    continue
                missing.append(m)
            matchup = f"{e.get('away_team')} at {e.get('home_team')}"
            if not missing:
                summary["slots"][e["id"]] = {"wave": w.index, "decision": None, "why": "ALREADY_CAPTURED_OR_RECHECK_NOT_DUE"}
                continue
            summary["events_seen"] += 1
            reservations = []
            for m in list(missing):
                klass = _class_of(m)
                key = f"{day}:{gid}:{m}:w{w.index}"
                res = cp.reserve(klass, 1, now, plan=plan, note=f"{gid}:{m}", key=key)
                if not res.get("allow") and res.get("reason") in (f"{klass}_ALLOWANCE",) and not res.get("pending"):
                    res = cp.reserve(cp.REFRESH, 1, now, plan=plan, note=f"recovery {gid}:{m}", key=key)         # the recovery pool covers a market the class allowance no longer does
                    klass = cp.REFRESH
                if not res.get("allow"):
                    missing.remove(m)
                    if res.get("reason") == "DUPLICATE":
                        summary["skipped"].append({"game_id": gid, "market": m, "reason": "ALREADY_BOUGHT_FOR_THIS_WAVE"})      # a restart lost the wave file: the ledger still knows it was paid for
                        continue
                    pa.note_unfetched(day, gid, m, pa.BUDGET_BLOCKED, now, detail=str(res.get("reason")), matchup=matchup, game_date=day, start_utc=start.isoformat())
                    summary["skipped"].append({"game_id": gid, "market": m, "reason": res.get("reason")})
                    continue
                reservations.append((m, klass, res["id"]))
            if not missing:
                continue
            r = client.get_event_odds(e["id"], markets=",".join(missing))
            if not r.ok:
                for m, klass, rid in reservations:
                    cp.release(rid, now)
                summary["skipped"].append({"event_id": e["id"], "reason": f"API_ERROR: {r.error}"})
                _record_answer(day, gid, missing, r, now, (start - now).total_seconds() / 3600.0, matchup, day, start.isoformat())
                continue
            cost = len(reservations) if r.requests_last is None else max(int(r.requests_last), 0)        # no header: assume what was reserved rather than record a free call
            attributed = {m: 0 for m in missing}
            try:
                archive_mod.archive_result(r, event_id=e["id"], market_filter=",".join(missing), bookmaker_filter="draftkings")
                got_now = _record_answer(day, gid, missing, r, now, (start - now).total_seconds() / 3600.0, matchup, day, start.isoformat())
                posted = [m for m in missing if (got_now.get(m) or {}).get("status") == pa.POSTED]
                attributed = {m: (1 if m in posted else 0) for m in missing}
                diff = cost - sum(attributed.values())
                if diff > 0:                                          # the provider charged more than one credit per returned market: the difference lands on the first market
                    attributed[missing[0]] += diff
                for m in posted:                                      # ... or less: take it back from posted markets, never below zero
                    if diff >= 0:
                        break
                    attributed[m] -= 1
                    diff += 1
            finally:                                                  # a paid call is ALWAYS ledgered, even if archiving or recording it raised
                if not any(attributed.values()) and cost:
                    attributed[missing[0]] = cost
                for m, klass, rid in reservations:
                    cp.settle(rid, attributed.get(m, 0), now, klass=klass, event=e["id"], game_id=gid, markets=m, wave=w.index, slot="WAVE", late=late, remaining_header=r.requests_remaining, header_at=getattr(r, "retrieved_at_utc", None))
            summary["credits_spent"] += cost
            summary["events_captured"] += 1
            entry = {"captured_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "markets": missing, "posted": posted, "credits": cost,
                     "minutes_before_wave_first_game": round((w.first_start - now).total_seconds() / 60.0, 1), "late": late}
            if posted:
                wstate["games"][gid] = entry
            else:                                                     # asked, nothing posted: that is an attempt (retried on schedule), not a capture
                wstate.setdefault("tried", {})[gid] = entry
            summary["slots"][e["id"]] = {"wave": w.index, "decision": "FIRST" if len(missing) == len(wanted) else "RETRY", "why": "OK", "late": late}
        done = all(g in wstate["games"] or g not in by_gid for g in chosen)
        wstate["state"] = "CAPTURED" if done and wstate["games"] else wstate.get("state", "PENDING")
        wstate["target_utc"], wstate["deadline_utc"], wstate["first_game_utc"] = w.target.strftime("%Y-%m-%dT%H:%M:%SZ"), w.deadline.strftime("%Y-%m-%dT%H:%M:%SZ"), w.first_start.strftime("%Y-%m-%dT%H:%M:%SZ")
        wstate["on_time"] = bool(wstate["games"]) and not any(v.get("late") for v in wstate["games"].values())
    _save_wave_state(state)
    summary["wave_state"] = {k: {kk: v for kk, v in w_.items() if kk != "games"} for k, w_ in state["waves"].items()}
    return summary


def _blocked_in_window(day: str, gid: str, start: dt.datetime, end: dt.datetime) -> str | None:
    """Why the trader, running inside the window, did not capture this game: a BUDGET_BLOCKED or FETCH_ERROR answer recorded for it between `start` and `end`."""
    from operational import price_availability as pa
    try:
        markets = (((pa.read(day).get("games") or {}).get(gid)) or {}).get("markets") or {}
    except Exception:  # noqa: BLE001
        return None
    for m, rec in markets.items():
        if rec.get("status") in (pa.BUDGET_BLOCKED, pa.FETCH_ERROR) and rec.get("checked_utc"):
            t = _parse_utc(rec["checked_utc"])
            if start <= t <= end:
                return f"{m} was {str(rec['status']).replace('_', ' ').lower()}" + (f" ({rec['detail']})" if rec.get("detail") else "")
    return None


def note_missed_pregame_windows(now: dt.datetime, plan: dict, games: dict | None = None, *, sleep_fn=None, ran_fn=None, wave_state: dict | None = None) -> dict:
    """Recovery honesty: a wave whose capture window (target 90 minutes before its first game until LAST_CHANCE 15 minutes before it) CLOSED with a selected game still uncaptured is written down as
    a MISSED WINDOW for that game. It is never bought late and never backfilled.

    Attribution is by evidence, not by assumption:
      MACHINE_ASLEEP            the operating system's power log (operational/sleep_evidence.py) shows the machine asleep for at least five minutes of the window;
      ENGINE_OUTAGE_OBSERVED    no sleep evidence, and the trader did not start a single run inside the window (the job was down, unloaded or crashing);
      (no extra tag)            the trader ran during the window and still made no capture (credits, an API error or the plan).
    Games still inside their window are untouched: the normal wave capture takes them as soon as the machine is running again."""
    from operational import capture_timeline as ct, credit_planner as cp, price_availability as pa, run_heartbeat, sleep_evidence
    day = plan.get("day")
    if not day or not plan.get("waves"):
        return {"recorded": 0, "considered": 0}
    sleep_fn = sleep_fn or (lambda a, b: sleep_evidence.slept_during(a, b, now))
    ran_fn = ran_fn or (lambda a, b: run_heartbeat.ran_between("real_parlay_paper_trader", a, b))
    wave_state = wave_state if wave_state is not None else _load_wave_state(day)
    captured = {str(r.get("game_id")) for r in cp.read_ledger(day) if r.get("game_id") is not None and r.get("credits", 0) > 0 and r.get("class") in (cp.PROPS, cp.GOALS, cp.SAVES, cp.REFRESH)}
    recorded = considered = 0
    for wdoc in plan["waves"]:
        w = ct.Wave(wdoc["index"], _parse_utc(wdoc["first_start_utc"]), tuple(wdoc["games"]))
        if now <= w.last_chance:
            continue                                                  # the window is still open
        for gid in wdoc.get("selected") or []:
            considered += 1
            if gid in captured or gid in ((wave_state.get("waves") or {}).get(str(w.index)) or {}).get("games", {}):
                continue
            ev = sleep_fn(w.target, w.last_chance)
            window_min = (w.last_chance - w.target).total_seconds() / 60.0
            ran = ran_fn(w.target, w.last_chance)
            blocked = _blocked_in_window(day, str(gid), w.target, w.last_chance)
            # Sleep is named as the cause only when it explains the miss: the power log shows sleep AND the trader did not run (or the machine slept through most of the window), and the game was not
            # simply refused by the budget or the provider while the trader was running.
            asleep = ev.get("evidence") == "ASLEEP" and not blocked and (ran is not True or ev.get("slept_minutes", 0) >= window_min / 2.0)
            if asleep:
                tag, detail = "MACHINE_ASLEEP", (f"the Mac was asleep for {ev['slept_minutes']:.0f} of the window's minutes (power log: {', '.join(f'{a}-{b}' for a, b in ev['intervals'][:4])}); "
                                                   "the game's wave closed before a capture could be made. Nothing is bought after the window.")
            elif blocked:
                tag, detail = None, (f"the trader was running during the window and the game was not captured: {blocked}. Nothing is bought after the window.")
            elif ran is False:
                tag, detail = "ENGINE_OUTAGE_OBSERVED", ("no sleep evidence, and the 10-minute trader did not start a run between the wave's target and its last chance: an engine outage was observed "
                                                          "(job unloaded, crashed or blocked). Nothing is bought after the window.")
            else:
                tag, detail = None, ("the trader was running during the window but no capture was made (credits, an API error or the plan"
                                     + ("; the power log was unavailable" if ev.get("evidence") == "UNAVAILABLE" else "") + "); nothing is bought after the window.")
            g = (games or {}).get(str(gid)) or {}
            if pa.record_missed_window(day, str(gid), f"WAVE{w.index}", w.target, w.last_chance, now, asleep=False, detail=detail, tag=tag,
                                       matchup=(f"{g.get('away')} at {g.get('home')}" if g.get("away") else None), start_utc=g.get("start_utc")):
                recorded += 1
    return {"recorded": recorded, "considered": considered}


def check_tomorrow(now: dt.datetime, *, client=None, archive_mod=None, plan: dict | None = None, force: bool = False, games: dict | None = None) -> dict:
    """Twice a day (capture_schedule.TOMORROW_CHECKS_ET): has DraftKings posted tomorrow's player prices at this hour? The earliest game is asked about every market we price, the
    others about shots. An answer with nothing in it costs 0 credits, so the check is free until the bookmaker has posted something; a posted answer is paid for from the TOMORROW
    class, and any game after the allowance is used is recorded BUDGET_BLOCKED (the provider may well have posted it; we did not look). Each answer is stored as an OBSERVATION with its
    time (price_availability.observations): it says what DraftKings had posted when asked, nothing more.
    Tomorrow's moneylines need no call of their own: every league-wide moneyline pull already returns them."""
    from operational import capture_schedule as sched, credit_planner as cp, eastern_time as et, price_availability as pa
    from research.live_sog_pricing import archive as _archive, client as _client
    client, archive_mod = client or _client, archive_mod or _archive
    out = {"ran": False, "checked": 0, "posted": 0, "reason": None}
    if not force and not sched.tomorrow_check_due(now, pa.tomorrow_last_check()):
        out["reason"] = "NOT_DUE"
        return out
    events = client.get_nhl_events()
    if not events.ok:
        out["reason"] = f"API_ERROR: {events.error}"
        return out
    tomorrow = (dt.date.fromisoformat(et.eastern_today(now)) + dt.timedelta(days=1)).isoformat()
    todays = [e for e in events.data if et.eastern_today(_parse_utc(e["commence_time"])) == tomorrow]
    out["ran"] = True
    out["games"] = len(todays)
    ALL_MARKETS = [SOG_MARKET_KEY, POINTS_MARKET_KEY, GOALS_MARKET_KEY, "player_total_saves"]
    for i, e in enumerate(sorted(todays, key=lambda x: x["commence_time"])):
        hours = (_parse_utc(e["commence_time"]) - now).total_seconds() / 3600.0
        matched = match_game(e, games or {})
        gid = str(matched[0]) if matched else f"event:{e['id']}"
        matchup = f"{e.get('away_team')} at {e.get('home_team')}"
        # The earliest game is asked about every market we price (a market not posted costs 0), so each check records what DraftKings had posted for all four at that
        # hour; the other games are asked about shots only, which bounds the cost if DraftKings has started posting.
        asked = ALL_MARKETS if i == 0 else [SOG_MARKET_KEY]
        gate = cp.authorize(cp.TOMORROW, 1, now, plan=plan) if cp.enforced() else {"allow": True}
        if (not gate.get("allow") and str(gate.get("reason", "")).endswith("_DAILY_ALLOWANCE") and cp.spent_today(now).get(cp.TOMORROW, 0.0) < cp.TOMORROW_COST):
            gate = {"allow": True, "reason": "OK_FIRST_TOMORROW_CREDIT"}   # a day whose plan left nothing over still asks; asking costs 0 unless DraftKings has posted, and a credit a day or so at most
        if not gate.get("allow"):
            pa.note_unfetched(tomorrow, gid, SOG_MARKET_KEY, pa.BUDGET_BLOCKED, now, detail=f"tomorrow's check allowance is used ({gate.get('reason')})",
                              matchup=matchup, game_date=tomorrow)
            continue
        mk = ",".join(asked)
        r = client.get_event_odds(e["id"], markets=mk)
        out["checked"] += 1
        got = _record_answer(tomorrow, gid, asked, r, now, hours, matchup, tomorrow, e["commence_time"])
        if not r.ok:
            continue
        archive_mod.archive_result(r, event_id=e["id"], market_filter=mk, bookmaker_filter="draftkings")
        cost = int(r.requests_last or 0)
        if cost:
            cp.record(cp.TOMORROW, cost, now, event=e["id"], markets=mk, remaining_header=r.requests_remaining, header_at=getattr(r, "retrieved_at_utc", None))
        if any((v or {}).get("status") == pa.POSTED for v in got.values()):
            out["posted"] += 1
    pa.mark_tomorrow_checked(now)
    return out


def _cp_enforced() -> bool:
    from operational import credit_planner as cp
    return cp.enforced()


def _evidence(entry: dict, family: str, prob_prefix: str, k: int) -> dict:
    """The model's probability and, for shots legs, the player's last-30-game frequency, as ParlayLeg fields."""
    out = {"model_probability": entry["probs"].get(f"{prob_prefix}{k}")}
    emp = (entry.get("emp") or {}).get(f"{prob_prefix}{k}") if family == "PLAYER_SOG_ALTERNATE" else None
    if emp:
        out.update(empirical_probability=emp["p"], empirical_hits=emp["hits"], empirical_games=emp["n"], empirical_games_current=emp.get("n_current"))
    return out


def _legs_from_payload(payload: dict, captured_at: dt.datetime, snapshot: dict, now: dt.datetime,
                       only_market: str | None = None) -> list:
    """ParlayLegs for DraftKings' Over prices -- shots-on-goal alternate ladder
    (k+ shots) and player_points (Over 0.5 / 1.5 = 1+ / 2+ points) -- for players
    the rolling-form model covers (dressed in their team's last game, 40+ games
    of history). Both contracts are certified against real archived payloads
    (provider_adapter.VERIFIED_CONTRACTS)."""
    from operational import player_integrity
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
    try:                                    # the provider's event must be THIS game: the same two teams on another night (a home-and-home) is a different event
        if abs((_parse_utc(payload["commence_time"]) - _parse_utc(game["start_utc"])).total_seconds()) > 12 * 3600:
            return []
    except (KeyError, ValueError, TypeError):
        pass
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
    official = player_integrity._load(player_integrity.ROSTER_STATE)         # read once per event
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
            # An EARLY price: fresh by age, but retrieved from the morning/midday look, long before puck drop. It is as usable as any fresh price (options,
            # automatic tickets); automatic tickets built on early prices are capped per day, and every ticket is re-judged at the moment of recording
            # (operational/daily_tickets.py), so a price that has aged out by then is not used.
            early = False
            if fresh:
                from operational import capture_schedule as _sched
                start_eff = effective_start(payload.get("commence_time") or game["start_utc"], game["start_utc"])
                early = captured_at < start_eff - dt.timedelta(hours=_sched.SLOT_CLOSE_HOURS)
            # Provisional: NOT fresh any more, but the provider's own quote time is within PROVISIONAL_MAX_AGE_MIN and we retrieved it today (Eastern), so it is today's look
            # at the market, not a leftover from yesterday. Judged by the same function as every other quote (same missing/malformed/future rules).
            provisional = False
            if hours > 0 and not fresh:
                wide = quote_freshness.assess(m.get("last_update") or bm.get("last_update"), captured_at.isoformat(), now, PROVISIONAL_MAX_AGE_MIN)
                from operational import eastern_time as _et
                provisional = bool(wide["fresh"]) and _et.eastern_today(captured_at) == _et.eastern_today(now)
            status = quote["status"]
            for o in m.get("outcomes", []):
                if m.get("key") == GOALS_MARKET_KEY:          # one-sided "Yes" prices: anytime goal == 1+ goals, no point, no "No" side
                    if o.get("name") != "Yes" or o.get("price") is None:
                        continue
                    k = 1
                elif o.get("name") != "Over" or o.get("point") is None or o.get("price") is None:
                    continue
                else:
                    k = int(o["point"] + 0.5)
                found = [snapshot["model"].get(f"{norm_name(o.get('description', ''))}|{team}") for team in (home, away)]
                found = [e for e in found if e]
                if len(found) > 1:                                  # the same name on BOTH teams of the game (two Sebastian Ahos): the price carries only the name, so it matches neither
                    continue
                entry = found[0] if found else None
                if not entry or f"{prob_prefix}{k}" not in entry["probs"] or not entry.get("player_id"):
                    continue
                legs.append(ParlayLeg(
                    game_id=game_id, event_id=payload.get("id"), market_family=family,
                    participant_id=entry["player_id"], participant_name=entry["name"], side="OVER", threshold=k,
                    american_price=float(o["price"]), conservative_probability=entry["probs"][f"{prob_prefix}{k}"],
                    sportsbook="draftkings", captured_at_utc=captured_at.isoformat(),
                    retrieved_at_utc=quote["retrieved_at_utc"], quote_updated_utc=quote["quote_updated_utc"],
                    quote_age_min=quote["quote_age_min"], freshness_status=status, provisional=provisional, early=early,
                    provider_contract_verified=verified[family], model_threshold_eligible=k in thresholds,
                    identity_resolved=not player_integrity.blocking(player_integrity.check_selection(entry["player_id"], entry["name"], entry["team"], {"home": home, "away": away}, snapshot.get("date") or "", now, rosters=official)["status"]),
                    price_fresh=fresh, event_not_started=hours > 0,
                    team=entry["team"], opponent=entry["opp"], game_start_utc=game["start_utc"],
                    provider_start_utc=payload.get("commence_time"), model_version=MODEL_VERSION,
                    **_evidence(entry, family, prob_prefix, k)))
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
    listing = _latest_events_listing()
    starts_by_gid = {}
    for e in listing:                                       # wave-synchronised pregame captures: a game's pregame look opens with its wave's first game (see capture_prices)
        m = match_game(e, games)
        if m:
            t = effective_start(e["commence_time"], m[1]["start_utc"])
            if t > now:
                starts_by_gid[str(m[0])] = t
    from operational import credit_planner as _cpw
    wave_anchor = {g: starts_by_gid[w[0]] for w in _cpw.waves(starts_by_gid) for g in w}
    for e in listing:
        matched = match_game(e, games)
        official = matched[1]["start_utc"] if matched else None
        start = effective_start(e["commence_time"], official)
        hours = (start - now).total_seconds() / 3600.0
        if hours <= 0 or hours > hours_ahead:
            continue
        opens = start - dt.timedelta(hours=CAPTURE_HORIZON_H)
        dstart = wave_anchor.get(str(matched[0])) if matched else None
        dstart = dstart or start
        shots, pts = latest_capture(e["id"], market=SOG_MARKET_KEY), latest_capture(e["id"], market=POINTS_MARKET_KEY)
        age = decision_age_min(e["id"], now, _bought_markets())
        decision = capture_decision(hours, age)
        fmt = lambda t: t.strftime("%b %-d %H:%M") + " UTC"  # noqa: E731
        slots = None
        if _cp_enforced():
            from operational import credit_planner as _cpl, eastern_time as _et
            day_plan = _cpl.load_plan(_et.eastern_today(now)) or {}
            gid_s = str(matched[0]) if matched else None
            wdoc = next((w for w in day_plan.get("waves") or [] if gid_s in w.get("games", [])), None)
            if wdoc:
                slots = {"WAVE": {"opens_utc": wdoc["target_utc"], "closes_utc": wdoc["last_chance_utc"], "publish_deadline_utc": wdoc["deadline_utc"]}}
                opens = _parse_utc(wdoc["target_utc"])
            if not day_plan or gid_s is None:
                nxt = "no plan for this game yet"
            elif gid_s not in (day_plan.get("games_priced") or []):
                nxt = "no player-odds capture: today's credit plan does not cover this game (excluded by budget); its analysis is on the Slate Board without prices"
            elif wdoc is None:
                nxt = "priced by today's plan, but its capture wave is not in the plan document yet"
            elif now < opens:
                nxt = f"coordinated capture ({', '.join(m.replace('player_', '') for m in (day_plan.get('game_markets') or {}).get(gid_s, []))}) from {fmt(opens)}, 90 minutes before wave {wdoc['index'] + 1}'s first game"
            elif now <= _parse_utc(wdoc["last_chance_utc"]):
                nxt = "coordinated capture is due now (on the next 10-minute trader cycle)"
            else:
                nxt = "the capture window has closed: nothing is bought after it"
        elif age is None and now < opens:
            nxt = f"first capture on the first 10-minute trader cycle at or after {fmt(opens)}"
        elif decision:
            nxt = f"{decision} on the next 10-minute trader cycle"
        else:
            oldest = min(c[0] for c in (shots, pts) if c)
            far_t = oldest + dt.timedelta(minutes=MAX_PRICE_AGE_MIN_FAR - REFRESH_LEAD_MIN)
            if (start - far_t).total_seconds() / 3600.0 >= 2.0:
                due = far_t
            else:
                due = max(oldest + dt.timedelta(minutes=MAX_PRICE_AGE_MIN_NEAR - REFRESH_LEAD_MIN),
                          start - dt.timedelta(hours=2.0))
            nxt = f"refresh on the first cycle at or after {fmt(due)}" if due < start else "none planned before puck drop"
        stale_at = None
        if shots and (pts or "player_points" not in _bought_markets()):
            oldest = min(c[0] for c in (shots, pts) if c)
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
            "prices_stop_being_fresh_utc": stale_at, "next_planned_capture": nxt, "slots": slots})
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


def tomorrow_games(conn, now: dt.datetime) -> dict:
    """{game_id: {"home", "away", "start_utc"}} for tomorrow's (ET) scheduled games."""
    from operational import eastern_time as et
    date = (dt.date.fromisoformat(et.eastern_today(now)) + dt.timedelta(days=1)).isoformat()
    rows = conn.execute("select game_id, home_team, away_team, scheduled_start_utc from games where game_date=? and game_state='SCHEDULED'", (date,)).fetchall()
    return {str(r["game_id"]): {"home": r["home_team"], "away": r["away_team"],
                                "start_utc": r["scheduled_start_utc"] if r["scheduled_start_utc"].endswith("Z") else r["scheduled_start_utc"] + "Z"} for r in rows}


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
    from operational import credit_planner as cp, eastern_time as et, goalie_confirmations, odds_quota, product_data
    if not cp.enforced():
        return None
    day = et.eastern_today(now)
    rows = conn.execute("SELECT game_id, scheduled_start_utc FROM games WHERE game_date = ?", (day,)).fetchall()
    starts = {str(r["game_id"]): _parse_utc(r["scheduled_start_utc"] if r["scheduled_start_utc"].endswith("Z") else r["scheduled_start_utc"] + "Z") for r in rows}
    horizon = (dt.date.fromisoformat(day) + dt.timedelta(days=odds_quota.days_left_in_cycle(now.date()))).isoformat()
    counts_ahead = {r["game_date"]: r["n"] for r in conn.execute("SELECT game_date, COUNT(*) n FROM games WHERE game_date > ? AND game_date < ? GROUP BY game_date", (day, horizon)).fetchall()}
    try:
        product = product_data.read_state()
    except Exception:  # noqa: BLE001 - quality is a tie-breaker; the plan is still made without it
        product = None
    quality = {g: cp.game_quality(product, g) for g in starts} if product else None
    return cp.day_plan(now, day, starts, remaining=odds_quota.latest_remaining(), confirmed_games={g for g in goalie_confirmations.confirmed_games(conn, now)},
                       counts_ahead=counts_ahead, quality=quality)


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
        from operational import credit_planner as _cpl
        missed_summary = None
        if capture and _cpl.enforced():
            try:                                   # every game of the day, started or not: the last game's window closes after nothing upcoming is left
                from operational import eastern_time as _et
                _day = _et.eastern_today(now)
                _plan = plan or _cpl.load_plan(_day)
                _rows = conn.execute("SELECT game_id, home_team, away_team, scheduled_start_utc FROM games WHERE game_date = ?", (_day,)).fetchall()
                _all = {str(r["game_id"]): {"home": r["home_team"], "away": r["away_team"],
                                            "start_utc": r["scheduled_start_utc"] if r["scheduled_start_utc"].endswith("Z") else r["scheduled_start_utc"] + "Z"} for r in _rows}
                missed_summary = note_missed_pregame_windows(now, _plan, _all) if _plan else None
            except Exception as exc:  # noqa: BLE001 - reporting a missed window must never stop a capture cycle
                missed_summary = {"error": f"{exc.__class__.__name__}: {exc}"}
        if capture and _cpl.enforced():
            try:
                if ((plan or {}).get("allowance") or {}).get(_cpl.TOMORROW, 0.0) <= 0:
                    tomorrow_summary = {"ran": False, "reason": "NOT_IN_THE_CORE_ALLOCATION: tomorrow's games are covered by the league-wide pulls (prices and their age); a paid look needs explicit surplus"}
                else:
                    tomorrow_summary = check_tomorrow(now, client=client, plan=plan, games=tomorrow_games(conn, now))
            except Exception as exc:  # noqa: BLE001 - a failed look at tomorrow must never stop today's captures or recording
                tomorrow_summary = {"ran": False, "reason": f"{exc.__class__.__name__}: {exc}"}
            if capture_summary is not None:
                capture_summary["tomorrow"] = tomorrow_summary
                capture_summary["missed_windows"] = missed_summary
        state = {
            "status": "OK" if snapshot["games"] else "NO_UPCOMING_GAMES",
            "date_et": snapshot["date"], "generated_at_utc": now.isoformat(),
            "games_today_upcoming": len(snapshot["games"]),
            "modelled_players": len(snapshot["model"]),
            "capture": capture_summary, "missed_windows": missed_summary,
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
