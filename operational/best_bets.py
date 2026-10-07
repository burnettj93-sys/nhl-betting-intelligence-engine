"""
Price capture + second-opinion player model for the unified ticket workflow.

This module no longer recommends anything. It used to build its own +100
singles and 2-leg parlays under rules that differed from the ledger's, so what
Today showed was not what the paper trader staked. The one selector is now
research/real_market_parlay/engine.py::select_tickets, driven by
operational/daily_tickets.py. What remains here:

  1. Capture (credit-metered, bounded): for each of today's upcoming games,
     pull DraftKings' player_shots_on_goal_alternate + player_points once when
     the game is within CAPTURE_HORIZON_H, and once more near puck drop
     (REFRESH_WITHIN_H) if the first capture has aged -- never more than
     DAILY_CREDIT_CAP credits a day, never past the global odds_quota guard.
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

from operational import state_paths

REPO_ROOT = Path(__file__).resolve().parent.parent
MP_RAW_DIR = REPO_ROOT / "research" / "player_sog" / "raw"
MP_DAILY_ROOT = REPO_ROOT / "data" / "raw" / "moneypuck" / "skater" / "2026"

MARKETS = "player_shots_on_goal_alternate,player_points"
CAPTURE_HORIZON_H = 5.0
REFRESH_WITHIN_H = 1.5
REFRESH_MIN_AGE_MIN = 90.0
DAILY_CREDIT_CAP = 36
EST_COST_PER_EVENT = 2


SOG_K = (1, 2, 3, 4, 5)
MODEL_VERSION = "rolling-l20-l60-shrunk-v1"
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


def _load_or_build_model(conn, today: dict, date: str, now: dt.datetime | None = None) -> dict:
    as_of = (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y-%m-%dT%H:%M:%S")
    dressed, curteam = _dressed_and_current_team(conn, list(today), as_of)
    signature = hashlib.sha256(json.dumps({t: sorted(v) for t, v in sorted(dressed.items())}).encode()).hexdigest()[:16]
    cache_path = state_paths.path(MODEL_CACHE_NAME)
    rows = checksum = None
    if cache_path.exists():
        cached = json.loads(cache_path.read_text())
        _, checksum = _current_season_rows()
        if (cached.get("date") == date and cached.get("mp_checksum") == checksum and cached.get("dressed") == signature
                    and cached.get("version") == MODEL_VERSION):
            return cached["model"]
    rows, checksum = history_rows()
    model = compute_model(rows, today, dressed, curteam, date)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({"date": date, "mp_checksum": checksum, "dressed": signature,
                                      "version": MODEL_VERSION, "model": model}))
    return model


# ------------------------------------------------------------------- capture --

def _parse_utc(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def _archive_dir() -> Path:
    from research.live_sog_pricing import archive
    return Path(archive.ARCHIVE_DIR)


def latest_capture(event_id: str, archive_dir: Path | None = None, *,
                   require_points: bool = True) -> tuple[dt.datetime, dict] | None:
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
        if "player_shots_on_goal_alternate" not in filt or (require_points and "player_points" not in filt):
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


def capture_decision(hours_to_start: float, last_capture_age_min: float | None) -> str | None:
    """'FIRST' / 'REFRESH' / None -- pure, so the cadence is testable."""
    if hours_to_start <= 0 or hours_to_start > CAPTURE_HORIZON_H:
        return None
    if last_capture_age_min is None:
        return "FIRST"
    if hours_to_start <= REFRESH_WITHIN_H and last_capture_age_min >= REFRESH_MIN_AGE_MIN:
        return "REFRESH"
    return None


def capture_prices(now: dt.datetime, *, client=None, archive_mod=None, guard=None) -> dict:
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
    upcoming = sorted((e for e in events.data if _parse_utc(e["commence_time"]) > now),
                      key=lambda e: e["commence_time"])
    for e in upcoming:
        hours = (_parse_utc(e["commence_time"]) - now).total_seconds() / 3600.0
        last = latest_capture(e["id"])
        age_min = None if last is None else (now - last[0]).total_seconds() / 60.0
        decision = capture_decision(hours, age_min)
        if decision is None:
            continue
        summary["events_seen"] += 1
        if spent_today + EST_COST_PER_EVENT > DAILY_CREDIT_CAP:
            summary["skipped"].append({"event_id": e["id"], "reason": "BEST_BETS_DAILY_CREDIT_CAP"})
            continue
        gate = guard(EST_COST_PER_EVENT)
        if not gate.get("allow"):
            summary["skipped"].append({"event_id": e["id"], "reason": gate.get("reason")})
            break
        r = client.get_event_odds(e["id"], markets=MARKETS)
        if not r.ok:
            summary["skipped"].append({"event_id": e["id"], "reason": f"API_ERROR: {r.error}"})
            continue
        archive_mod.archive_result(r, event_id=e["id"], market_filter=MARKETS, bookmaker_filter="draftkings")
        cost = int(r.requests_last or 0)
        spent_today += cost
        summary["credits_spent"] += cost
        summary["events_captured"] += 1
    return summary


def _legs_from_payload(payload: dict, captured_at: dt.datetime, snapshot: dict, now: dt.datetime) -> list:
    """ParlayLegs for DraftKings' Over prices -- shots-on-goal alternate ladder
    (k+ shots) and player_points (Over 0.5 / 1.5 = 1+ / 2+ points) -- for players
    the rolling-form model covers (dressed in their team's last game, 20+ games
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
    hours = (_parse_utc(game["start_utc"]) - now).total_seconds() / 3600.0
    age_min = (now - captured_at).total_seconds() / 60.0
    fresh = hours > 0 and age_min <= (MAX_PRICE_AGE_MIN_FAR if hours >= 2.0 else MAX_PRICE_AGE_MIN_NEAR)
    verified = {"PLAYER_SOG_ALTERNATE": provider_adapter.is_contract_verified("draftkings", "PLAYER_SOG_ALTERNATE"),
                "PLAYER_POINTS": provider_adapter.is_contract_verified("draftkings", "PLAYER_POINTS")}
    from research.real_market_parlay.engine import POINTS_ACTIONABLE_THRESHOLDS
    markets = {"player_shots_on_goal_alternate": ("PLAYER_SOG_ALTERNATE", "SOG", SOG_ACTIONABLE_THRESHOLDS),
               "player_points": ("PLAYER_POINTS", "PTS", POINTS_ACTIONABLE_THRESHOLDS)}
    legs = []
    for bm in payload.get("bookmakers", []):
        if bm.get("key") != "draftkings":
            continue
        for m in bm.get("markets", []):
            if m.get("key") not in markets:
                continue
            family, prob_prefix, thresholds = markets[m["key"]]
            for o in m.get("outcomes", []):
                if o.get("name") != "Over" or o.get("point") is None or o.get("price") is None:
                    continue
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
                    provider_contract_verified=verified[family], model_threshold_eligible=k in thresholds,
                    identity_resolved=True, price_fresh=fresh, event_not_started=hours > 0,
                    team=entry["team"], opponent=entry["opp"], game_start_utc=game["start_utc"],
                    model_version=MODEL_VERSION))
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
        cap = latest_capture(event_id, require_points=False)
        if cap is None:
            continue
        got = _legs_from_payload(cap[1], cap[0], snapshot, now)
        if got:
            report["events_with_capture"] += 1
        legs.extend(got)
    report["legs"] = len(legs)
    return legs, report


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


def _refresh_locked(now, *, conn, capture, client) -> dict:
    import db
    owns = conn is None
    conn = conn or db.get_conn()
    try:
        # Capture first, so the model and the downstream selector see the
        # prices just pulled. current_model() needs only the schedule.
        snapshot = current_model(conn, now)
        capture_summary = capture_prices(now, client=client) if (capture and snapshot["games"]) else None
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
