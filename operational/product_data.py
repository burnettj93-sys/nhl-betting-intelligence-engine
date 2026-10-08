"""
The product's data layer: everything the Games, Game Detail, Players, Goalies, Teams and Model Health pages show,
built once per trader cycle from OBSERVED data and written to `product_state.json`. The hosted app reads the same
document through the published snapshot (sections `product_*`), so local and hosted pages cannot disagree.

Sources, none simulated:
  * schedule, results, scores, rosters: nhl.db (NHL API ingest);
  * player and goalie game logs: MoneyPuck game-by-game files (see research/product_models/history.py);
  * goalie season records: the NHL's own player pages (operational/nhl_goalie_stats.py);
  * prices: DraftKings via The Odds API, with the provider's own quote time;
  * projections: research/product_models (skater rate x minutes model, goalie/team model), each with a validation report.

Rules this module keeps:
  * dates are Eastern-time hockey days derived from the start instant; games.game_date is cross-checked;
  * "current" means the current season: a different season is reachable only through the dated history picker;
  * a value that cannot be supported is None with a stated reason -- never a placeholder number.
"""
from __future__ import annotations

import datetime as dt
import json
from collections import defaultdict
from pathlib import Path

from operational import eastern_time as et
from operational import state_paths

STATE_NAME = "product_state.json"
SCHEMA_VERSION = 1
CURRENT_SEASON_ID = "20262027"
DETAIL_PAST_DAYS = 3
DETAIL_FUTURE_DAYS = 7
GAME_TYPES = {"01": "PRESEASON", "02": "REGULAR", "03": "PLAYOFF"}
CURRENT_SEASON_START = "2026-09-01"
SAVE_LADDER = tuple(range(24, 35))                  # the DraftKings Ontario saves ladder observed 24+ .. 34+
SHOTS_KEYS = tuple(f"shots>={k}" for k in range(1, 6))
SHOW_KEYS = SHOTS_KEYS + ("points>=1", "points>=2", "goals>=1", "assists>=1", "hits>=1", "hits>=2", "hits>=3", "blocks>=1", "blocks>=2")
TOP_SKATERS_PER_TEAM = 18


def _iso(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def _start_label(start: dt.datetime | None) -> str | None:
    return None if start is None else start.astimezone(et.EASTERN).strftime("%-I:%M %p ET")


def game_type(game_id) -> str:
    return GAME_TYPES.get(str(game_id)[4:6], "OTHER")


def season_label(season_id: str) -> str:
    return f"{str(season_id)[:4]}-{str(season_id)[6:]}"


# ------------------------------------------------------------------ schedule ----

def load_games(nhl, now: dt.datetime) -> list[dict]:
    rows = nhl.execute("SELECT game_id, season, game_date, scheduled_start_utc, home_team, away_team, game_state, home_score, "
                       "away_score, final_period_type, result_observed_at_utc FROM games ORDER BY scheduled_start_utc, game_id").fetchall()
    out = []
    for r in rows:
        start = _utc(r["scheduled_start_utc"])
        date_et = et.eastern_today(start) if start else r["game_date"]
        final = r["game_state"] == "FINAL"
        if final:
            state = "FINAL"
        elif start and start <= now:
            state = "STARTED"                       # past puck drop, no final result recorded yet
        else:
            state = "SCHEDULED"
        out.append({"game_id": str(r["game_id"]), "season": str(r["season"]), "season_label": season_label(r["season"]),
                    "type": game_type(r["game_id"]), "date_et": date_et, "date_in_db": r["game_date"],
                    "start_utc": _iso(start) if start else None, "start_et": _start_label(start), "state": state,
                    "home": r["home_team"], "away": r["away_team"], "home_score": r["home_score"] if final else None,
                    "away_score": r["away_score"] if final else None, "period_type": r["final_period_type"] if final else None,
                    "result_observed_at_utc": r["result_observed_at_utc"] if final else None})
    return out


def team_records(games: list[dict]) -> dict[str, dict]:
    """Regular-season record of the current season from final games (OT/SO losses counted separately)."""
    rec = defaultdict(lambda: {"gp": 0, "w": 0, "l": 0, "otl": 0, "gf": 0, "ga": 0, "last5": []})
    for g in sorted((g for g in games if g["state"] == "FINAL" and g["type"] == "REGULAR" and g["season"] == CURRENT_SEASON_ID),
                    key=lambda g: (g["start_utc"], g["game_id"])):
        for team, gf, ga, opp, home in ((g["home"], g["home_score"], g["away_score"], g["away"], True),
                                        (g["away"], g["away_score"], g["home_score"], g["home"], False)):
            r = rec[team]
            r["gp"] += 1
            r["gf"] += gf
            r["ga"] += ga
            win = gf > ga
            if win:
                r["w"] += 1
            elif g["period_type"] in ("OT", "SO"):
                r["otl"] += 1
            else:
                r["l"] += 1
            r["last5"].append({"game_id": g["game_id"], "date_et": g["date_et"], "opp": opp, "home": home, "gf": gf, "ga": ga,
                               "result": "W" if win else ("OTL" if g["period_type"] in ("OT", "SO") else "L"),
                               "period_type": g["period_type"]})
    for r in rec.values():
        r["last5"] = r["last5"][-5:][::-1]
    return dict(rec)


def moneyline_quotes(nhl, now: dt.datetime) -> dict[str, dict]:
    """Latest DraftKings moneyline per game side, with the time the quote was captured and its age."""
    rows = nhl.execute(
        "SELECT game_id, selection, price_american, captured_at_utc, status FROM odds_snapshots WHERE market = 'MONEYLINE' "
        "AND id IN (SELECT MAX(id) FROM odds_snapshots WHERE market = 'MONEYLINE' GROUP BY game_id, selection)").fetchall()
    out: dict[str, dict] = defaultdict(dict)
    for r in rows:
        captured = _utc(r["captured_at_utc"])
        out[str(r["game_id"])][r["selection"]] = {
            "american": r["price_american"], "quote_captured_at_utc": _iso(captured) if captured else None,
            "age_min": round((now - captured).total_seconds() / 60.0, 1) if captured else None, "status": r["status"]}
    return dict(out)


# ------------------------------------------------------------------ goalies ----

def goalie_candidates(nhl, history_goalies: list[dict], live_tg: dict) -> dict[str, dict]:
    """Every goalie who is current: has played this season, or sits on a roster (latest membership event)."""
    from research.product_models import history
    cur = {r["player_id"] for r in history_goalies if r["season"] == history.CURRENT_SEASON}
    state = live_tg["state"]
    out = {}
    for pid in cur:
        g = state.goalies.get(pid)
        out[pid] = {"player_id": pid, "name": g.name if g else pid, "team": g.team if g else None}
    rows = nhl.execute(
        "SELECT p.player_id, p.full_name, m.team_id, m.event_type FROM players p JOIN team_membership_events m "
        "ON m.player_id = p.player_id AND m.id = (SELECT MAX(id) FROM team_membership_events WHERE player_id = p.player_id) "
        "WHERE p.position = 'G'").fetchall()
    for r in rows:
        if r["event_type"] in ("JOINED", "ADDED", "ROSTER", "ACTIVE", "ASSIGNED", "SIGNED", "TRADED_IN") or r["team_id"]:
            if r["player_id"] not in out and r["team_id"]:
                out[r["player_id"]] = {"player_id": r["player_id"], "name": r["full_name"], "team": r["team_id"]}
            elif r["player_id"] in out and r["team_id"]:
                out[r["player_id"]]["team"] = r["team_id"]
    return out


def goalie_recent(state, pid: str) -> list[dict]:
    g = state.goalies.get(pid)
    if not g:
        return []
    out = []
    for date, opp, sa, saves, ga, started, toi in reversed(g.recent):
        out.append({"date": date, "season": "current" if date >= CURRENT_SEASON_START else "previous", "opp": opp,
                    "shots_against": sa, "saves": saves, "goals_against": ga,
                    "save_pct": round(saves / sa, 3) if sa else None, "started": started, "toi": round(toi, 1)})
    return out[:5]


def build_goalie_projection(live_tg: dict, goalie: dict, team: str, opp: str, date: str, p_team_win: dict | None) -> dict:
    from research.product_models import team_goalie as tg
    state = live_tg["state"]
    alpha = live_tg["alpha"]
    pr = state.goalie_projection(goalie["player_id"], team, opp, alpha)
    mean_saves = pr["expected_saves"]
    lo, hi = tg.saves_quantile_range(mean_saves, alpha)
    ga_lo, ga_hi = tg.saves_quantile_range(pr["expected_goals_against"], live_tg["validation"].get("alpha_ga", 0.0))
    ladder = {f"saves>={k}": round(tg.prob_saves_at_least(k, mean_saves, alpha), 4) for k in SAVE_LADDER}
    g = state.goalies.get(goalie["player_id"])
    shots_faced_total = g.shots if g else 0.0
    return {"opponent": opp, "expected_shots_against": round(pr["expected_shots_against"], 1), "save_pct_used": round(pr["save_pct"], 4),
            "expected_saves": round(mean_saves, 1), "expected_goals_against": round(pr["expected_goals_against"], 2),
            "goals_against_range_80": [ga_lo, ga_hi], "saves_range_80": [lo, hi], "saves_probabilities": ladder,
            "sample": {"games": g.games if g else 0, "starts": g.starts if g else 0, "shots_faced_weighted": round(shots_faced_total)},
            "model_version": tg.MODEL_VERSION}


# ------------------------------------------------------------------ build ----

def build_state(now: dt.datetime | None = None, *, nhl=None, tickets_state: dict | None = None, fetch_goalie_stats: bool = True,
                skater_rows: list | None = None, goalie_rows: list | None = None) -> dict:
    from research.product_models import history, live as skater_live, team_goalie as tg
    from operational import nhl_goalie_stats
    import db

    now = now or dt.datetime.now(dt.timezone.utc)
    owns = nhl is None
    nhl = nhl or db.get_conn()
    try:
        today = et.eastern_today(now)
        games = load_games(nhl, now)
        records = team_records(games)
        quotes = moneyline_quotes(nhl, now)
        grows = goalie_rows if goalie_rows is not None else history.goalie_games()
        srows = skater_rows if skater_rows is not None else history.skater_games()
        live_tg = tg.build_live(grows)
        sk = skater_live.build(srows)
        tg_state = live_tg["state"]
        validation_goalie = live_tg["validation"]
        saves_verdicts = tg.saves_verdicts(validation_goalie)

        tickets_state = tickets_state if tickets_state is not None else _tickets_state()
        ticket_cards = []
        if tickets_state:
            ticket_cards = (tickets_state.get("tickets") or []) + (tickets_state.get("manual_tickets") or []) \
                + (tickets_state.get("earlier_open_tickets") or [])
        tickets_by_game: dict[str, list] = defaultdict(list)
        for c in ticket_cards:
            for l in c["legs"]:
                if c["ticket_id"] not in tickets_by_game[str(l["game_id"])]:
                    tickets_by_game[str(l["game_id"])].append(c["ticket_id"])
        options = (tickets_state or {}).get("options") or {}
        person_option = {pid: p.get("option_id") for pid, p in (options.get("persons") or {}).items()}

        upcoming = [g for g in games if g["state"] in ("SCHEDULED", "STARTED") and g["type"] == "REGULAR"]
        next_game: dict[str, dict] = {}
        for g in sorted(upcoming, key=lambda g: (g["start_utc"], g["game_id"])):
            for team, opp, home in ((g["home"], g["away"], True), (g["away"], g["home"], False)):
                if g["state"] == "SCHEDULED" and team not in next_game:
                    next_game[team] = {"game_id": g["game_id"], "date_et": g["date_et"], "start_utc": g["start_utc"],
                                       "start_et": g["start_et"], "opp": opp, "home": home}

        # ---- goalies
        cands = goalie_candidates(nhl, grows, live_tg)
        landing = nhl_goalie_stats.refresh(sorted(cands), now=now) if fetch_goalie_stats else nhl_goalie_stats.load_cache()
        starters_by_game = _game_starters(grows)

        def expected_starters(team: str, date: str) -> list[dict]:
            return tg.start_probabilities(live_tg, team, date)

        goalies_out: dict[str, dict] = {}
        for pid, c in cands.items():
            team = c["team"]
            ng = next_game.get(team) if team else None
            land = landing.get(pid) or {}
            stats = land.get("stats") or None
            g = tg_state.goalies.get(pid)
            rec = {"player_id": pid, "name": c["name"], "team": team,
                   "catches": (stats or {}).get("catches"), "sweater": (stats or {}).get("sweater"),
                   "season": ({k: stats.get(k) for k in ("games", "starts", "wins", "losses", "ot_losses", "save_pct", "gaa", "shutouts",
                                                         "shots_against", "goals_against")} if stats else None),
                   "season_source": {"name": "NHL.com player page", "fetched_at_utc": land.get("fetched_at_utc"),
                                     "last_ok_utc": land.get("last_ok_utc"), "error": land.get("error")},
                   "recent_starts": goalie_recent(tg_state, pid), "games_in_log": g.games if g else 0,
                   "next_game": ng, "start": None, "projection": None, "confirmation": _confirmation(pid, team, ng, nhl)}
            if ng:
                sp = {s["goalie_id"]: s for s in expected_starters(team, ng["date_et"])}
                s = sp.get(pid)
                if s is not None:
                    rec["start"] = {"probability": round(s["start_probability"], 3),
                                    "basis": ("Share of the team's last %d starts and rest/back-to-back, from a model validated on the "
                                              "2025-26 season." % s["features"]["n_recent"]),
                                    "kind": "ESTIMATE", "back_to_back": s["features"]["back_to_back"],
                                    "share_last10": s["features"]["share_last10"]}
                if s is not None or (g and g.games):
                    rec["projection"] = build_goalie_projection(live_tg, {"player_id": pid}, team, ng["opp"], ng["date_et"], None)
                    rec["projection"]["win_probability"] = _team_win(live_tg, team, ng, pid)
            goalies_out[pid] = rec

        # ---- skaters
        players_out, by_team = {}, defaultdict(list)
        for pid, p in sk["players"].items():
            team = p["team"]
            ng = next_game.get(team)
            proj = None
            if ng:
                m = skater_live.project_matchup(sk, pid, ng["opp"])
                if m:
                    proj = {"opponent": ng["opp"], "expected": m["expected"], "games_observed": m["games_observed"],
                            "limited_history": m["limited_history"], "pricing_eligible": m["pricing_eligible"],
                            "probabilities": {k: m["probabilities"][k]["calibrated"] for k in SHOW_KEYS},
                            "model_version": m["model_version"]}
            rec = {"player_id": pid, "name": p["name"], "team": team, "position": p["position"], "line": p["line"], "pp_unit": p["pp_unit"],
                   "role_source": p["role_source"], "role_games": p["role_games"], "season": p["season_totals"],
                   "recent_games": p["recent_games"], "recent_avg": {k.replace("recent_", ""): round(p[k], 2) for k in
                                                                    ("recent_toi", "recent_toi_pp", "recent_shots", "recent_goals", "recent_assists", "recent_hits", "recent_blocks")},
                   "last_game_date": p["last_game_date"], "games_total": p["games_total"], "next_game": ng, "projection": proj,
                   "option_id": person_option.get(pid)}
            players_out[pid] = rec
            by_team[team].append(pid)

        # ---- games with predictions
        sm_state = tg_state
        date_of_last_obs = live_tg["newest_game_date"]
        games_out = []
        for g in games:
            row = dict(g)
            row["tickets"] = tickets_by_game.get(g["game_id"], [])
            if g["state"] == "SCHEDULED" and g["type"] == "REGULAR":
                wp = tg.win_probability(live_tg, g["home"], g["away"])
                row["win_probability"] = {"home": round(wp["base"], 4), "away": round(1 - wp["base"], 4),
                                          "model": "goalie-team-v1 strength model (validated on 2025-26 games, shootouts excluded)"}
                q = quotes.get(g["game_id"])
                row["moneyline"] = ({"home": q.get(g["home"]), "away": q.get(g["away"]), "provider": "DraftKings (US feed via The Odds API)"}
                                    if q else None)
                row["goalies"] = {"home": _short_goalies(goalies_out, expected_starters(g["home"], g["date_et"]), tg_state),
                                  "away": _short_goalies(goalies_out, expected_starters(g["away"], g["date_et"]), tg_state)}
            elif g["state"] == "FINAL":
                st = starters_by_game.get(int(g["game_id"]), {})
                row["goalies"] = {"home": st.get(g["home"]), "away": st.get(g["away"]), "kind": "ACTUAL_STARTERS"}
            games_out.append(row)

        # ---- details for the near window
        win_start = (dt.date.fromisoformat(today) - dt.timedelta(days=DETAIL_PAST_DAYS)).isoformat()
        win_end = (dt.date.fromisoformat(today) + dt.timedelta(days=DETAIL_FUTURE_DAYS)).isoformat()
        details = {}
        for row in games_out:
            if not (win_start <= row["date_et"] <= win_end):
                continue
            details[row["game_id"]] = _game_detail(row, records, players_out, by_team, goalies_out, tg_state, live_tg, options,
                                                   next_game, expected_starters)

        teams_out = _teams(games_out, records, players_out, by_team, goalies_out, tg_state, today)

        default_date = _default_date(games_out, today)
        dates = sorted({g["date_et"] for g in games_out})
        data_through = {"skaters": sk["newest_game_date"], "goalies": date_of_last_obs,
                        "schedule_results": max((g["start_utc"] for g in games_out if g["state"] == "FINAL"), default=None)}
        model_health = _model_health(now, sk, live_tg, saves_verdicts, tickets_state, data_through, goalies_out, players_out, games_out)
        return {
            "schema_version": SCHEMA_VERSION, "generated_at_utc": _iso(now), "et_today": today,
            "default_date": default_date, "data_through": data_through, "current_season": CURRENT_SEASON_ID,
            "games": {"dates": dates, "games": games_out, "default_date": default_date, "et_today": today,
                      "records": {t: {k: v for k, v in r.items() if k != "last5"} for t, r in records.items()}},
            "game_details": details, "players": players_out, "goalies": goalies_out, "teams": teams_out, "model_health": model_health,
            "options": options,
        }
    finally:
        if owns:
            nhl.close()


def _tickets_state() -> dict | None:
    from operational import daily_tickets
    return daily_tickets.read_state()


def _confirmation(pid: str, team: str | None, ng: dict | None, nhl=None) -> dict:
    """Starter confirmation: nothing publishes confirmed starters in a form this system may use, so every goalie is
    UNCONFIRMED unless a manual confirmation exists (operational/goalie_confirmations.py)."""
    try:
        from operational import goalie_confirmations
        found = goalie_confirmations.lookup(nhl, ng["game_id"], team, pid) if (ng and nhl is not None) else None
    except Exception:  # noqa: BLE001 - absence means no manual confirmation
        found = None
    if found:
        return found
    return {"status": "UNCONFIRMED", "source": None, "checked_at_utc": None,
            "note": "No confirmation has been recorded. No automated starting-goalie feed can be used, so the chance shown is an estimate "
                    "from recent usage. Saves props that depend on a named goalie stay blocked until a person records a confirmation."}


def _team_win(live_tg: dict, team: str, ng: dict, pid: str) -> dict:
    """The goalie's team win probability: the validated strength model, plus a clearly labelled scenario with this goalie's
    expected goals saved per game added (opponent goalie taken as league average)."""
    from research.product_models import team_goalie as tg
    home = team if ng["home"] else ng["opp"]
    away = ng["opp"] if ng["home"] else team
    base = tg.win_probability(live_tg, home, away)
    b0, b1 = live_tg["strength_coef"]
    diff_home = base["strength_diff_goals_per_game"]
    gd = live_tg["state"].saved_goals_per_game(pid, team, ng["opp"])
    if ng["home"]:
        team_base, scenario = base["base"], tg._sigmoid(b0 + b1 * (diff_home + gd))
    else:
        team_base, scenario = 1 - base["base"], 1 - tg._sigmoid(b0 + b1 * (diff_home - gd))
    return {"team_win_probability": round(team_base, 4), "if_this_goalie_starts": round(scenario, 4),
            "goalie_effect_goals_per_game": round(gd, 3), "status": "SCENARIO_NOT_VALIDATED",
            "note": "Team win probability is the validated strength model. The goalie-specific figure adds this goalie's expected "
                    "goals saved per game; on held-out games that adjustment did not improve the forecast, so it is a scenario only."}


def _short_goalies(goalies_out: dict, probs: list[dict], tg_state=None) -> list[dict]:
    def name(gid):
        n = (goalies_out.get(gid) or {}).get("name")
        if n is None and tg_state is not None and gid in tg_state.goalies:
            n = tg_state.goalies[gid].name
        return n
    return [{"player_id": p["goalie_id"], "name": name(p["goalie_id"]),
             "start_probability": round(p["start_probability"], 3), "status": "UNCONFIRMED"} for p in probs]


def _game_starters(goalie_rows: list[dict]) -> dict[int, dict]:
    by_game: dict[int, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for r in goalie_rows:
        by_game[r["game_id"]][r["team"]].append(r)
    out = {}
    for gid, teams in by_game.items():
        out[gid] = {}
        for team, rs in teams.items():
            s = max(rs, key=lambda x: x["toi"])
            out[gid][team] = {"player_id": s["player_id"], "name": s["name"], "shots_against": s["shots_against"],
                              "saves": s["shots_against"] - s["goals_against"], "goals_against": s["goals_against"],
                              "toi": round(s["toi"], 1)}
    return out


def _game_detail(row: dict, records: dict, players: dict, by_team: dict, goalies: dict, tg_state, live_tg: dict, options: dict,
                 next_game: dict, expected_starters) -> dict:
    """One game's page: both teams' context plus ids of the players and goalies to show (the page reads their full
    records from the players / goalies sections, so nothing is stored twice)."""
    d = dict(row)
    sides = {}
    for side, team in (("home", row["home"]), ("away", row["away"])):
        plist = sorted((players[pid] for pid in by_team.get(team, [])), key=lambda p: -p["recent_avg"]["toi"])
        gl = sorted((g for g in goalies.values() if g["team"] == team and (g["games_in_log"] or (g["season"] or {}).get("games"))),
                    key=lambda g: -(g["start"] or {}).get("probability", 0.0))
        sides[side] = {"team": team, "record": records.get(team), "strength_rating": round(tg_state.team_strength(team), 3),
                       "skater_ids": [p["player_id"] for p in plist[:TOP_SKATERS_PER_TEAM]],
                       "goalie_ids": [g["player_id"] for g in gl[:3]]}
    d["sides"] = sides
    return d


def _teams(games_out, records, players, by_team, goalies, tg_state, today) -> dict:
    teams = sorted({g["home"] for g in games_out} | {g["away"] for g in games_out})
    out = {}
    for t in teams:
        recent = records.get(t, {}).get("last5", [])
        upcoming = [{"game_id": g["game_id"], "date_et": g["date_et"], "start_et": g["start_et"], "opp": g["away"] if g["home"] == t else g["home"],
                     "home": g["home"] == t, "state": g["state"]} for g in games_out
                    if t in (g["home"], g["away"]) and g["state"] in ("SCHEDULED", "STARTED") and g["type"] == "REGULAR"][:6]
        skaters = sorted((players[pid] for pid in by_team.get(t, [])), key=lambda p: -p["recent_avg"]["toi"])
        gl = [g for g in goalies.values() if g["team"] == t]
        out[t] = {"team": t, "record": {k: v for k, v in records.get(t, {}).items() if k != "last5"} or None, "last5": recent,
                  "upcoming": upcoming, "strength_rating": round(tg_state.team_strength(t), 3),
                  "skaters": [{"player_id": p["player_id"], "name": p["name"], "position": p["position"], "line": p["line"],
                               "pp_unit": p["pp_unit"], "toi_recent": p["recent_avg"]["toi"], "season": p["season"]} for p in skaters[:26]],
                  "goalies": [{"player_id": g["player_id"], "name": g["name"], "season": g["season"],
                               "start": g["start"], "confirmation": g["confirmation"]} for g in gl]}
    return out


def _default_date(games_out: list[dict], today: str) -> str:
    """Today's Eastern date if it has current-season games; else the next date with one; else the most recent."""
    reg = [g for g in games_out if g["type"] == "REGULAR" and g["season"] == CURRENT_SEASON_ID]
    dates = sorted({g["date_et"] for g in reg})
    if today in dates:
        return today
    later = [d for d in dates if d > today]
    if later:
        return later[0]
    return dates[-1] if dates else today


# ------------------------------------------------------------------ model health ----

def _model_health(now, sk, live_tg, saves_verdicts, tickets_state, data_through, goalies_out, players_out, games_out) -> dict:
    from research.product_models import live as skater_live
    sk_verdicts = skater_live.market_verdicts(skater_live.load_validation())
    coverage = (tickets_state or {}).get("coverage") or {}
    ticket_sources = ((tickets_state or {}).get("diagnostics") or {}).get("sources") or {}
    sk_val = skater_live.load_validation()
    g_val = live_tg["validation"]

    def age_days(date_str):
        if not date_str:
            return None
        return (dt.date.fromisoformat(et.eastern_today(now)) - dt.date.fromisoformat(date_str[:10])).days

    def verdicts_summary(vs):
        beat = sum(1 for v in vs.values() if v["verdict"] == "BEATS_BASELINES")
        return {"markets_scored": len(vs), "beating_baselines": beat,
                "not_beating": sorted(k for k, v in vs.items() if v["verdict"] != "BEATS_BASELINES")}

    models = [
        {"id": "skater-projection", "name": "Skater matchup projection", "version": sk_val["model_version"], "role": "LIVE",
         "purpose": "Expected time on ice, power-play time, shots, goals, assists, points, hits and blocks per player and opponent; "
                    "threshold probabilities for shots (1+ to 5+), points (1+, 2+), goals (1+), assists, hits, blocks.",
         "data": {"source": "MoneyPuck game-by-game logs, 2022-23 through 2026-27 to date", "through": data_through["skaters"],
                  "age_days": age_days(data_through["skaters"]), "players_projected": sum(1 for p in players_out.values() if p["projection"]),
                  "current_season_players": len(players_out)},
         "validation": {"split": sk_val["split"], "rows": sk_val["rows"], "summary": verdicts_summary(sk_verdicts),
                        "report": "docs/validation/skater_projection_validation.json",
                        "method": "Chronological walk; train 2022-24 fit, calibration season 2024-25 (dispersion, Platt), final evaluation "
                                  "2025-26 scored once; compared with the recent 30-game rate and the previous live formula; Brier, log loss, "
                                  "calibration tables, limited-history and changed-role slices."},
         "limits": ["Scores players conditional on dressing: injuries and scratches are not modelled. The 'dressed in the team's last game' "
                    "proxy decides who is priced.", "Probabilities for players with fewer than 20 prior games over-predict in testing and are "
                    "not used for pricing.", "Hits: calibrated probability does not clearly beat the simple baseline for 1+; display only.",
                    "There are no historical sportsbook prices, so profitability is NOT claimed; evaluation is forward and frozen."],
         "markets": {"SHOTS (alternate ladder 1+..5+)": "PRICING_ACTIVE", "POINTS (1+, 2+)": "PRICING_ACTIVE",
                     "ANYTIME GOAL (1+)": "MODEL_READY_PRICES_NOT_CAPTURED", "HITS / BLOCKS": "DISPLAY_ONLY"}},
        {"id": "goalie-saves", "name": "Goalie saves and goals against", "version": g_val["model_version"], "role": "LIVE_DISPLAY",
         "purpose": "Expected shots against, save percentage, saves and goals against for a named goalie and opponent, with an 80% range "
                    "and a saves ladder 24+ to 34+.",
         "data": {"source": "MoneyPuck goalie logs + NHL.com season records", "through": data_through["goalies"], "age_days": age_days(data_through["goalies"]),
                  "goalies_tracked": len(goalies_out)},
         "validation": {"split": g_val["splits"], "rows": g_val["rows"], "summary": verdicts_summary(saves_verdicts),
                        "report": "docs/validation/goalie_team_validation.json",
                        "range_coverage": g_val["saves_80pct_range_coverage"],
                        "error": g_val["expected_value_mae_final"]},
         "limits": ["The starter must be known: no confirmation source is connected, so saves props stay blocked from tickets (the gate is not "
                    "bypassed).", "The fitted calibration did not improve held-out log loss, so raw probabilities are used."],
         "markets": {"SAVES ladder 24+..34+": "BLOCKED_NO_STARTER_CONFIRMATION"}},
        {"id": "team-win", "name": "Team strength and win probability", "version": g_val["model_version"], "role": "LIVE_DISPLAY",
         "purpose": "Home/away win probability from decayed goal differential (shootouts counted in the result for display).",
         "data": {"source": "MoneyPuck goalie logs (game results derived)", "through": data_through["goalies"]},
         "validation": {"split": g_val["splits"], "result": {k: g_val["win_probability"][k] for k in
                                                              ("games_scored", "shootouts_excluded", "home_rate_baseline", "strength_only", "strength_and_named_goalies",
                                                               "strength_and_goalies_tied")},
                        "report": "docs/validation/goalie_team_validation.json"},
         "limits": ["The named-goalie adjustment did not improve held-out forecasts; it is a scenario, not the probability.",
                    "Moneyline and puck-line pricing are not driven by this model yet: the existing moneyline path (Elo with a conservative bound) "
                    "stays the only source for MONEYLINE legs and is unvalidated on this corpus."],
         "markets": {"MONEYLINE": "BLOCKED_NO_FRESH_PRICES_OR_UNVALIDATED_MODEL", "PUCK LINE": "BLOCKED_SPREAD_PRICES_NOT_CAPTURED_NO_MARGIN_MODEL"}},
    ]
    pipelines = [{"name": "NHL schedule and results", "through": data_through["schedule_results"], "source": "NHL API → nhl.db"},
                 {"name": "Skater logs", "through": data_through["skaters"], "source": "MoneyPuck daily download"},
                 {"name": "Goalie logs", "through": data_through["goalies"], "source": "MoneyPuck daily download"}]
    return {"generated_at_utc": _iso(now), "models": models, "pipelines": pipelines, "market_coverage": coverage,
            "ticket_sources": ticket_sources, "skater_verdicts": sk_verdicts, "saves_verdicts": saves_verdicts,
            "guarantees": ["No simulated data is shown anywhere in the product.", "Partial or unvalidated status is stated, not relabelled."]}


# ------------------------------------------------------------------ file ----

def state_path() -> Path:
    return state_paths.path(STATE_NAME)


def write_state(state: dict) -> None:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, default=str))
    tmp.replace(p)


def read_state() -> dict | None:
    p = state_path()
    try:
        return json.loads(p.read_text()) if p.exists() else None
    except (OSError, json.JSONDecodeError):
        return None


def refresh_state(now: dt.datetime | None = None, **kwargs) -> dict:
    """Build and write. Never raises into the trader: a failure keeps the last good file and reports why."""
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        state = build_state(now, **kwargs)
    except Exception as exc:  # noqa: BLE001
        return {"status": "ERROR", "reason": f"{exc.__class__.__name__}: {exc}"}
    write_state(state)
    return {"status": "OK", "generated_at_utc": state["generated_at_utc"], "games": len(state["games"]["games"]),
            "players": len(state["players"]), "goalies": len(state["goalies"])}
