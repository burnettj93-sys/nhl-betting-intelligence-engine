"""
Live use of the validated skater projection model: walk every observed game once, then project each active skater's
next game, with calibrated threshold probabilities and role assignments inferred from recent ice time.

Nothing here invents a value. Where the history cannot support one (too few games, no scheduled game, no usable
ice-time sample) the field is None and the caller shows why.

Probabilities: raw negative-binomial threshold probability -> Platt calibration fitted on the calibration season in
docs/validation/skater_projection_validation.json. A market's probability is only offered to the ticket pipeline when
that validation shows it beating both baselines on the held-out season (`market_verdict`).

Roles (line number, power-play unit) are INFERRED from time on ice in the most recent games. There is no official
depth-chart or line-combination feed, so every role carries its source, the games it was inferred from and the
timestamp of the newest game used.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from . import history
from . import projections as P
from .validate import apply_platt

REPO = Path(__file__).resolve().parent.parent.parent
VALIDATION_PATH = REPO / "docs" / "validation" / "skater_projection_validation.json"
EVENTS = {"shots": (1, 2, 3, 4, 5), "points": (1, 2), "goals": (1,), "assists": (1,), "hits": (1, 2, 3), "blocks": (1, 2)}
ROLE_WINDOW = 6                 # most recent games used to infer line / power-play unit
from operational.pricing_policy import MIN_GAMES_FOR_PRICING  # noqa: E402  (policy lives in a dependency-free module)


def load_validation(path: Path = VALIDATION_PATH) -> dict:
    return json.loads(path.read_text())


def market_verdicts(validation: dict) -> dict:
    """{"shots>=2": {"verdict": ..., "log_loss": ..}} -- calibrated model against both baselines, held-out season."""
    out = {}
    for key, m in validation["markets"].items():
        model = m["model_platt"]["log_loss"]
        baselines = {"recent 30-game empirical rate": m["baseline_last30_empirical"]["log_loss"]}
        if m.get("baseline_live_formula"):
            baselines["previous live formula"] = m["baseline_live_formula"]["log_loss"]
        beats = all(model < v for v in baselines.values())
        out[key] = {"verdict": "BEATS_BASELINES" if beats else "DOES_NOT_BEAT_BASELINES", "model_log_loss": model,
                    "baselines_log_loss": baselines, "brier": m["model_platt"]["brier"], "base_rate": m["model_platt"]["base_rate"],
                    "rows": m["common_rows"]}
    return out


def _by_date(rows: list[dict]) -> list[tuple[str, list[dict]]]:
    days: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        days[r["date"]].append(r)
    return sorted(days.items())


def walk_history(rows: list[dict] | None = None, config: P.ModelConfig | None = None, validation: dict | None = None):
    """Returns (model, newest_date, rows_by_player_recent). `model` has consumed every observed game."""
    validation = validation or load_validation()
    cfg = config or P.ModelConfig(**{k: (tuple(v) if k == "opp_cap" else v) for k, v in validation["chosen_config"].items()})
    rows = rows if rows is not None else history.skater_games()
    model = P.SkaterModel(cfg)
    newest = ""
    recent: dict[str, list[dict]] = defaultdict(list)
    for date, day in _by_date(rows):
        model.consume_day(day)
        newest = date
        for r in day:
            lst = recent[r["player_id"]]
            lst.append(r)
            if len(lst) > 12:
                del lst[0]
    return model, newest, recent


def infer_roles(players: dict[str, dict]) -> None:
    """Adds an ESTIMATED usage tier and power-play usage to every player dict in place, per team, from the ROLE_WINDOW most recent games.

    Ranking forwards by ice time does not establish which line a player was assigned to, and ranking power-play minutes does not
    establish a power-play unit, so these are named for what they are (usage_tier, pp_usage) and never presented as Line 1 / PP1. Reported
    assignments come from a lineup source (operational/dailyfaceoff.py) and are carried separately."""
    by_team: dict[str, list[dict]] = defaultdict(list)
    for p in players.values():
        by_team[p["team"]].append(p)
    for team, plist in by_team.items():
        usable = [p for p in plist if p["role_games"] >= 2]
        forwards = sorted([p for p in usable if p["position_group"] == "F"], key=lambda p: -p["recent_toi"])
        defense = sorted([p for p in usable if p["position_group"] == "D"], key=lambda p: -p["recent_toi"])
        for i, p in enumerate(forwards):
            p["usage_tier"] = min(i // 3 + 1, 4)
        for i, p in enumerate(defense):
            p["usage_tier"] = min(i // 2 + 1, 3)
        pp_ranked = sorted(usable, key=lambda p: -p["recent_toi_pp"])
        for i, p in enumerate(pp_ranked):
            p["pp_usage"] = 1 if i < 5 else 2 if i < 10 else None
            if p["recent_toi_pp"] < 0.25:
                p["pp_usage"] = None
        for p in plist:
            p.setdefault("usage_tier", None)
            p.setdefault("pp_usage", None)
            p["usage_source"] = (f"Inferred from time on ice in each player's last {p['role_games']} game(s), ranked within "
                                f"{team}. An estimate of usage, not an assigned line or power-play unit.") if p["role_games"] >= 2 else None


def season_totals(recent_all: list[dict], season: int) -> dict:
    cur = [r for r in recent_all if r["season"] == season]
    n = len(cur)
    if not n:
        return {"games": 0}
    tot = {k: sum(r[k] for r in cur) for k in ("shots", "goals", "assists", "points", "hits", "blocks", "toi", "toi_pp")}
    return {"games": n, "toi_avg": tot["toi"] / n, "toi_pp_avg": tot["toi_pp"] / n,
            **{k: tot[k] for k in ("shots", "goals", "assists", "points", "hits", "blocks")}}


def build(rows: list[dict] | None = None, season: int = history.CURRENT_SEASON, as_of_date: str | None = None) -> dict:
    """Projection state as of the newest observed game: per-player current numbers plus a `project(pid, opp)` closure
    result for every upcoming opponent supplied later by the caller."""
    validation = load_validation()
    rows = rows if rows is not None else history.skater_games()
    model, newest, recent = walk_history(rows, validation=validation)
    alphas = validation["dispersion"]
    platt = validation["platt_params"]
    players: dict[str, dict] = {}
    all_by_player: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r["season"] in (season, season - 1):
            all_by_player[r["player_id"]].append(r)
    for pid, st in model.players.items():
        last = recent[pid][-1]
        in_season = [r for r in all_by_player.get(pid, []) if r["season"] == season]
        if not in_season:                       # a player with no game yet this season is not shown as active
            continue
        last_n = recent[pid][-ROLE_WINDOW:]
        cur_last_n = [r for r in last_n if r["season"] == season] or last_n
        n = len(cur_last_n)
        players[pid] = {
            "player_id": pid, "name": last["name"], "team": last["team"], "position": last["pos"],
            "position_group": st.pos, "last_game_date": last["date"], "games_total": st.games,
            "season_totals": season_totals(in_season, season),
            "last_season_games": len([r for r in all_by_player.get(pid, []) if r["season"] == season - 1]),
            "recent_games": [{"date": r["date"], "season": f"{r['season']}-{(r['season'] + 1) % 100:02d}", "opp": r["opp"], "home": r["home"], "toi": round(r["toi"], 1),
                              "toi_pp": round(r["toi_pp"], 1), "shots": r["shots"], "goals": r["goals"],
                              "assists": r["assists"], "hits": r["hits"], "blocks": r["blocks"]}
                             for r in recent[pid][-5:][::-1]],
            "recent_toi": sum(r["toi"] for r in cur_last_n) / n, "recent_toi_pp": sum(r["toi_pp"] for r in cur_last_n) / n,
            "recent_shots": sum(r["shots"] for r in cur_last_n) / n, "recent_goals": sum(r["goals"] for r in cur_last_n) / n,
            "recent_assists": sum(r["assists"] for r in cur_last_n) / n, "recent_hits": sum(r["hits"] for r in cur_last_n) / n,
            "recent_blocks": sum(r["blocks"] for r in cur_last_n) / n, "role_games": n,
        }
    infer_roles(players)
    return {"model": model, "players": players, "newest_game_date": newest, "alphas": alphas, "platt": platt,
            "verdicts": market_verdicts(validation), "validation_version": validation["model_version"]}


def project_matchup(state: dict, player_id: str, opp_team: str) -> dict | None:
    """Expected values and calibrated threshold probabilities for one player against one opponent, or None."""
    model: P.SkaterModel = state["model"]
    base = model.project(player_id, opp_team)
    if base is None:
        return None
    probs = {}
    for stat, ks in EVENTS.items():
        for k in ks:
            raw = P.prob_at_least(k, base[stat], state["alphas"][stat])
            a, b = state["platt"][f"{stat}>={k}"]
            probs[f"{stat}>={k}"] = {"raw": round(raw, 4), "calibrated": round(apply_platt(raw, a, b), 4)}
    games = base["games_observed"]
    return {"expected": {s: round(base[s], 3) for s in P.STATS} | {"toi": round(base["toi"], 2), "toi_pp": round(base["toi_pp"], 2)},
            "probabilities": probs, "games_observed": games,
            "pricing_eligible": games >= MIN_GAMES_FOR_PRICING,
            "limited_history": games < MIN_GAMES_FOR_PRICING, "opponent": opp_team, "model_version": P.MODEL_VERSION}
