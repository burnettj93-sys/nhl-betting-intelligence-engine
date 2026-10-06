"""
Best Bets (+100 target), 2026-10-06. The owner's explicit requirement: "I need
a high chance of cashing, AND I need it to reach +100 odds in as few legs as
possible." This module produces exactly that for today's real games, from REAL
DraftKings prices matched against the project's own player models:

  1. Capture (credit-metered, bounded): for each of today's upcoming games,
     pull DraftKings' player_shots_on_goal_alternate + player_points once when
     the game is within CAPTURE_HORIZON_H, and once more near puck drop
     (REFRESH_WITHIN_H) if the first capture has aged -- never more than
     DAILY_CREDIT_CAP credits a day, never past the global odds_quota guard.
  2. Model: shots (empirical, conservative: the LOWER of last-20 and last-60 hit
     rates, shrunk toward the position average) and points (the project's locked
     points model blended with the last-60 hit rate, home/away adjusted), built
     from the FULL MoneyPuck history (all teams) refreshed daily by
     operational/moneypuck_daily.py. Only players who dressed in their team's
     most recent real game are considered (the only lineup signal that exists
     here -- there is no injury/lineup feed).
  3. Picks: SINGLES priced >= +100 first (one leg is always better than two at
     the same price), then 2-LEG parlays whose combined price is >= +100,
     cross-game only (legs from different games, so the joint probability is an
     honest product). Ranked by modeled hit chance, and only if the model's
     edge over the book's own implied probability clears a minimum margin.

HONEST LIMITS (stated on the page too): at >= +100 the book's own price implies
~50%, so a genuinely higher hit chance needs a real edge over DraftKings -- the
model is a research model, not validated against live results; edges here are
small. No injury/lineup/goalie feed exists. Prices move.

Run: python3 -m operational.best_bets
"""
from __future__ import annotations

import csv
import datetime as dt
import fcntl
import glob
import hashlib
import io
import itertools
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

MAX_PRICE_AGE_MIN_FAR = 150.0      # game >= 2h away
MAX_PRICE_AGE_MIN_NEAR = 100.0     # game < 2h away
MIN_LEG_DECIMAL_FOR_PARLAY = 1.20  # a -1100 "leg" is filler: it only lowers the hit chance for a bigger payout
MIN_SINGLE_P = 0.40
MIN_SINGLE_EDGE = 0.04
MIN_PARLAY_LEG_P = 0.50
MIN_PARLAY_EV = 0.05
TOP_SINGLES = 5
TOP_PARLAYS = 5
MAX_PLAYER_REUSE = 2

SOG_K = (1, 2, 3, 4, 5)
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
        model[f"{norm_name(name)}|{team}"] = {"name": name, "team": team, "opp": opp, "home": is_home, "probs": probs}
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
        if cached.get("date") == date and cached.get("mp_checksum") == checksum and cached.get("dressed") == signature:
            return cached["model"]
    rows, checksum = history_rows()
    model = compute_model(rows, today, dressed, curteam, date)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({"date": date, "mp_checksum": checksum, "dressed": signature, "model": model}))
    return model


# ------------------------------------------------------------------- capture --

def _parse_utc(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def _archive_dir() -> Path:
    from research.live_sog_pricing import archive
    return Path(archive.ARCHIVE_DIR)


def latest_capture(event_id: str, archive_dir: Path | None = None) -> tuple[dt.datetime, dict] | None:
    """Newest archived best-bets payload (MARKETS) for one provider event."""
    archive_dir = archive_dir or _archive_dir()
    best = None
    for path in glob.glob(str(archive_dir / f"*events-{event_id}-odds*.json")):
        try:
            doc = json.loads(Path(path).read_text())
        except (OSError, json.JSONDecodeError):
            continue
        meta = doc.get("meta") or {}
        filt = meta.get("market_filter") or ""
        if "player_points" not in filt or "player_shots_on_goal_alternate" not in filt:
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


# --------------------------------------------------------------------- picks --

def _legs_from_payload(payload: dict, captured_at: dt.datetime, model: dict, today_by_pair: dict) -> list[dict]:
    from research.live_sog_pricing import event_mapping
    home = event_mapping.normalize_team_name(payload.get("home_team", ""))
    away = event_mapping.normalize_team_name(payload.get("away_team", ""))
    game = today_by_pair.get((home, away))
    if game is None:
        return []
    legs = []
    for bm in payload.get("bookmakers", []):
        if bm.get("key") != "draftkings":
            continue
        for m in bm.get("markets", []):
            for o in m.get("outcomes", []):
                if o.get("name") != "Over" or o.get("point") is None or o.get("price") is None:
                    continue
                k = int(o["point"] + 0.5)
                if m["key"] == "player_shots_on_goal_alternate":
                    pkey, label = f"SOG{k}", f"{k}+ shots on goal"
                elif m["key"] == "player_points":
                    pkey, label = f"PTS{k}", f"{k}+ point{'s' if k > 1 else ''}"
                else:
                    continue
                entry = None
                for team in (home, away):
                    entry = model.get(f"{norm_name(o.get('description', ''))}|{team}")
                    if entry:
                        break
                if not entry or pkey not in entry["probs"]:
                    continue
                dec = american_to_decimal(o["price"])
                p = entry["probs"][pkey]
                legs.append({
                    "player": entry["name"], "team": entry["team"], "opp": entry["opp"], "home": entry["home"],
                    "game_key": game["game_key"], "start_utc": game["start_utc"], "label": label, "market": pkey,
                    "price": int(o["price"]), "decimal": dec, "p": p, "implied": 1.0 / dec,
                    "edge": p - 1.0 / dec, "captured_at_utc": captured_at.isoformat()})
    return legs


def build_picks(legs: list[dict], now: dt.datetime) -> dict:
    fresh = []
    for l in legs:
        hours = (_parse_utc(l["start_utc"]) - now).total_seconds() / 3600.0
        age = (now - _parse_utc(l["captured_at_utc"])).total_seconds() / 60.0
        if hours <= 0:
            continue
        if age > (MAX_PRICE_AGE_MIN_FAR if hours >= 2.0 else MAX_PRICE_AGE_MIN_NEAR):
            continue
        fresh.append({**l, "price_age_min": round(age, 1), "hours_to_start": round(hours, 2)})

    singles = [l for l in fresh if l["decimal"] >= 2.0 and l["p"] >= MIN_SINGLE_P and l["edge"] >= MIN_SINGLE_EDGE]
    singles.sort(key=lambda l: (-l["p"], -l["edge"]))
    singles = singles[:TOP_SINGLES]

    pool = [l for l in fresh if l["p"] >= MIN_PARLAY_LEG_P and l["decimal"] >= MIN_LEG_DECIMAL_FOR_PARLAY]
    combos = []
    for a, b in itertools.combinations(pool, 2):
        if a["game_key"] == b["game_key"]:
            continue
        dec = a["decimal"] * b["decimal"]
        if dec < 2.0:
            continue
        p = a["p"] * b["p"]
        if p * dec - 1 < MIN_PARLAY_EV:
            continue
        combos.append((p, dec, a, b))
    combos.sort(key=lambda t: -t[0])
    used: dict = defaultdict(int)
    parlays = []
    for p, dec, a, b in combos:
        if used[a["player"]] >= MAX_PLAYER_REUSE or used[b["player"]] >= MAX_PLAYER_REUSE:
            continue
        used[a["player"]] += 1
        used[b["player"]] += 1
        parlays.append({"legs": [a, b], "decimal": dec, "american": decimal_to_american(dec), "p": p, "ev": p * dec - 1})
        if len(parlays) >= TOP_PARLAYS:
            break
    return {"singles": singles, "parlays": parlays, "priced_legs_considered": len(fresh)}


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


def _refresh_locked(now, *, conn, capture, client) -> dict:
    import db
    from operational import eastern_time as et
    owns = conn is None
    conn = conn or db.get_conn()
    try:
        date = et.eastern_today(now)
        rows = conn.execute(
            "select game_id, home_team, away_team, scheduled_start_utc from games "
            "where game_date=? and game_state='SCHEDULED'", (date,)).fetchall()
        today, by_pair = {}, {}
        for r in rows:
            start = r["scheduled_start_utc"] if r["scheduled_start_utc"].endswith("Z") else r["scheduled_start_utc"] + "Z"
            if _parse_utc(start) <= now:
                continue
            today[r["home_team"]] = (r["away_team"], True, r["game_id"])
            today[r["away_team"]] = (r["home_team"], False, r["game_id"])
            by_pair[(r["home_team"], r["away_team"])] = {"game_key": str(r["game_id"]), "start_utc": start}
        capture_summary = capture_prices(now, client=client) if (capture and today) else None

        legs: list[dict] = []
        events_priced = 0
        if today:
            model = _load_or_build_model(conn, today, date, now)
            seen_events = set()
            for payload_path in glob.glob(str(_archive_dir() / f"{now.strftime('%Y%m%d')}T*events-*odds*.json")) + \
                    glob.glob(str(_archive_dir() / f"{(now - dt.timedelta(days=1)).strftime('%Y%m%d')}T*events-*odds*.json")):
                m = re.search(r"events-([0-9a-f]{32})-odds", payload_path)
                if m:
                    seen_events.add(m.group(1))
            for event_id in seen_events:
                cap = latest_capture(event_id)
                if cap is None:
                    continue
                got = _legs_from_payload(cap[1], cap[0], model, by_pair)
                if got:
                    events_priced += 1
                legs.extend(got)
        picks = build_picks(legs, now)
        state = {
            "status": "OK" if picks["singles"] or picks["parlays"] else "NO_QUALIFYING_PICKS",
            "date_et": date, "generated_at_utc": now.isoformat(),
            "games_today_upcoming": len(by_pair), "events_priced": events_priced,
            "modelled_players": len(model) if today else 0, **picks,
            "capture": capture_summary,
            "limits": ("Modeled hit chances, not guarantees. At +100 or better the book's own price implies about "
                       "50%, so any edge shown is small and the model is a research model not yet validated against "
                       "live results. There is no injury/lineup/goalie feed -- only players who dressed in their "
                       "team's last game are considered. Prices move; confirm before betting."),
        }
        old = read_state()
        changed = old is None or _content_hash(old) != _content_hash(state)
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1, default=str))
        tmp.replace(path)
        return {"status": state["status"], "changed": changed, "singles": len(picks["singles"]),
                "parlays": len(picks["parlays"]), "capture": capture_summary}
    finally:
        if owns:
            conn.close()


if __name__ == "__main__":
    print(json.dumps(refresh(), indent=2, default=str))
