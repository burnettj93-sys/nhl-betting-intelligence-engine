"""
Part 1: real goalie-game saves/shots-faced label corpus, plus a companion
team-game SOG-for/SOG-against corpus (workload context, Parts 5/12/13/14),
built directly from the accepted 4-season PBP foundation
(research/real_nhl_pbp/). Reuses the ALREADY-AUDITED, unmodified
goalie_tenure.py / period_saves.py -- 5,248/5,248 games showed exact
official boxscore save reconciliation in the prior Event-Timing Utility
Closure slice (research/real_nhl_pbp/goalie_tenure_audit_results.json),
so this corpus does not re-fetch boxscores; it reuses that already-proven
event-level reconstruction as its single source of truth (Part 22 "one
source of truth" convention, applied here to the corpus layer).

Goalie appearance rows (one per real goalie who saw ice time in a game,
both teams): saves/goals-against/shots-faced are period_saves.py's
period_saves_by_goalie() aggregated per goalie, with OT folded into a
separate ot_saves/ot_shots_faced field (never merged into period 3 --
matches the team_goals_period / player_sog_period convention). Shootout
is excluded entirely at the period_saves.py layer already.

actual_started / actual_relief come from goalie_tenure.py's own
interval_type vocabulary: a goalie is actual_started if any of their
intervals is type STARTER; actual_relief if any interval is type RELIEF
(a goalie who starts and later returns after an empty-net pull is still
only ever STARTER/RETURN_AFTER_EMPTY_NET, never RELIEF -- Part 6 of the
original goalie-tenure slice).

Team-game SOG rows carry sog_for/sog_against per period plus a real
situationCode-derived PP/PK split (same joint-situationCode rule reused
unchanged from team_goals_period's build_team_goals_period_corpus.py,
generalized here from goals to all real shot-on-goal + goal events).
"""
from __future__ import annotations

import collections
import json
import os

from research.real_nhl_pbp import goalie_tenure, normalize, period_saves, raw_archive

SEASONS = ("20222023", "20232024", "20242025", "20252026")
GOALIE_OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "goalie_game_saves.jsonl")
TEAM_SOG_OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "team_game_sog.jsonl")


def _pp_state(situation_code: str | None, team_id: int, home_id: int, away_id: int) -> str:
    """Part 14: real PP/PK/EV state via the accepted joint situationCode
    rule (both goalie digits '1', unequal skater counts) -- generalized
    from team_goals_period's goal-only version to any shot event."""
    if not situation_code or len(situation_code) != 4:
        return "EV"
    away_goalie, away_skaters, home_skaters, home_goalie = situation_code[0], situation_code[1], \
        situation_code[2], situation_code[3]
    if away_goalie != "1" or home_goalie != "1":
        return "EV"
    if away_skaters == home_skaters:
        return "EV"
    is_away = (team_id == away_id)
    own_skaters = away_skaters if is_away else home_skaters
    other_skaters = home_skaters if is_away else away_skaters
    if own_skaters > other_skaters:
        return "PP"
    if own_skaters < other_skaters:
        return "PK"
    return "EV"


def _interval_duration_seconds(interval) -> int | None:
    try:
        start_type = "REG" if interval.start_period <= 3 else "OT"
        end_type = "REG" if interval.end_period <= 3 else "OT"
        start_abs = normalize.compute_regulation_elapsed_seconds(
            interval.start_period, start_type, normalize.seconds_elapsed(interval.start_time_in_period))
        end_abs = normalize.compute_regulation_elapsed_seconds(
            interval.end_period, end_type, normalize.seconds_elapsed(interval.end_time_in_period))
        if start_abs is None or end_abs is None:
            return None
        return max(0, end_abs - start_abs)
    except Exception:
        return None


def build_one_game(season: str, game_id: int) -> tuple[list[dict], list[dict]]:
    raw = raw_archive.load_raw_pbp(season, game_id)
    home_id, away_id = raw["homeTeam"]["id"], raw["awayTeam"]["id"]
    home_abbrev, away_abbrev = raw["homeTeam"]["abbrev"], raw["awayTeam"]["abbrev"]
    game_date = raw["gameDate"]
    events = normalize.normalize_game_events(raw)

    tenure = goalie_tenure.reconstruct_goalie_tenure(events, home_id, away_id)
    period_stats = period_saves.period_saves_by_goalie(events)  # {(goalie_id, period): {...}}

    goalie_rows: list[dict] = []
    for team_id, opp_id, team_abbrev, opp_abbrev, is_home in (
        (home_id, away_id, home_abbrev, away_abbrev, True),
        (away_id, home_id, away_abbrev, home_abbrev, False),
    ):
        intervals = tenure[team_id]
        goalies_seen: dict[int, dict] = {}
        for iv in intervals:
            if iv.goalie_id is None:
                continue
            g = goalies_seen.setdefault(iv.goalie_id, {"started": False, "relief": False, "seconds": 0})
            if iv.interval_type == "STARTER":
                g["started"] = True
            elif iv.interval_type == "RELIEF":
                g["relief"] = True
            dur = _interval_duration_seconds(iv)
            if dur is not None:
                g["seconds"] += dur

        for goalie_id, tag in goalies_seen.items():
            p1 = period_stats.get((goalie_id, 1), {"saves": 0, "goals_against": 0, "shots_faced": 0})
            p2 = period_stats.get((goalie_id, 2), {"saves": 0, "goals_against": 0, "shots_faced": 0})
            p3 = period_stats.get((goalie_id, 3), {"saves": 0, "goals_against": 0, "shots_faced": 0})
            ot_saves = ot_shots = ot_goals = 0
            for (gid_key, period_num), stat in period_stats.items():
                if gid_key == goalie_id and period_num >= 4:
                    ot_saves += stat["saves"]
                    ot_shots += stat["shots_faced"]
                    ot_goals += stat["goals_against"]

            full_saves = p1["saves"] + p2["saves"] + p3["saves"] + ot_saves
            full_shots = p1["shots_faced"] + p2["shots_faced"] + p3["shots_faced"] + ot_shots
            full_goals = p1["goals_against"] + p2["goals_against"] + p3["goals_against"] + ot_goals

            goalie_rows.append({
                "game_id": game_id, "game_date": game_date, "season": int(season),
                "goalie_id": goalie_id, "team_id": team_id, "team": team_abbrev,
                "opponent_id": opp_id, "opponent": opp_abbrev,
                "home_away": "home" if is_home else "away",
                "actual_started": tag["started"], "actual_relief": tag["relief"],
                "actual_saves": full_saves, "actual_shots_faced": full_shots, "actual_goals_allowed": full_goals,
                "period_1_saves": p1["saves"], "period_2_saves": p2["saves"], "period_3_saves": p3["saves"],
                "period_1_shots_faced": p1["shots_faced"], "period_2_shots_faced": p2["shots_faced"],
                "period_3_shots_faced": p3["shots_faced"],
                "period_1_goals_against": p1["goals_against"], "period_2_goals_against": p2["goals_against"],
                "period_3_goals_against": p3["goals_against"],
                "ot_saves": ot_saves, "ot_shots_faced": ot_shots, "ot_goals_against": ot_goals,
                "tenure_seconds": tag["seconds"] if tag["seconds"] > 0 else None,
            })

    team_sog: dict[int, dict] = {
        home_id: {"periods": {1: {"sog": 0, "pp_sog": 0, "pk_sog": 0}, 2: {"sog": 0, "pp_sog": 0, "pk_sog": 0},
                               3: {"sog": 0, "pp_sog": 0, "pk_sog": 0}}, "ot_sog": 0},
        away_id: {"periods": {1: {"sog": 0, "pp_sog": 0, "pk_sog": 0}, 2: {"sog": 0, "pp_sog": 0, "pk_sog": 0},
                               3: {"sog": 0, "pp_sog": 0, "pk_sog": 0}}, "ot_sog": 0},
    }
    for e in events:
        if e.period_type == "SO" or not e.is_statistical:
            continue
        if e.event_type not in ("shot-on-goal", "goal"):
            continue
        shooting_team = e.team_id
        if shooting_team not in (home_id, away_id):
            continue
        state = _pp_state(e.situation_code, shooting_team, home_id, away_id)
        if e.period_number <= 3:
            bucket = team_sog[shooting_team]["periods"][e.period_number]
            bucket["sog"] += 1
            if state == "PP":
                bucket["pp_sog"] += 1
            elif state == "PK":
                bucket["pk_sog"] += 1
        else:
            team_sog[shooting_team]["ot_sog"] += 1

    team_rows: list[dict] = []
    for team_id, opp_id, team_abbrev, opp_abbrev, is_home in (
        (home_id, away_id, home_abbrev, away_abbrev, True),
        (away_id, home_id, away_abbrev, home_abbrev, False),
    ):
        t = team_sog[team_id]
        opp = team_sog[opp_id]
        p1, p2, p3 = t["periods"][1], t["periods"][2], t["periods"][3]
        op1, op2, op3 = opp["periods"][1], opp["periods"][2], opp["periods"][3]
        team_rows.append({
            "game_id": game_id, "game_date": game_date, "season": int(season),
            "team_id": team_id, "team": team_abbrev, "opponent_id": opp_id, "opponent": opp_abbrev,
            "home_away": "home" if is_home else "away",
            "period_1_sog": p1["sog"], "period_2_sog": p2["sog"], "period_3_sog": p3["sog"],
            "period_1_pp_sog": p1["pp_sog"], "period_2_pp_sog": p2["pp_sog"], "period_3_pp_sog": p3["pp_sog"],
            "period_1_pk_sog": p1["pk_sog"], "period_2_pk_sog": p2["pk_sog"], "period_3_pk_sog": p3["pk_sog"],
            "ot_sog": t["ot_sog"],
            "full_game_sog": p1["sog"] + p2["sog"] + p3["sog"] + t["ot_sog"],
            "opponent_period_1_sog": op1["sog"], "opponent_period_2_sog": op2["sog"],
            "opponent_period_3_sog": op3["sog"],
            "opponent_period_1_pp_sog": op1["pp_sog"], "opponent_period_2_pp_sog": op2["pp_sog"],
            "opponent_period_3_pp_sog": op3["pp_sog"],
            "opponent_full_game_sog": op1["sog"] + op2["sog"] + op3["sog"] + opp["ot_sog"],
        })
    return goalie_rows, team_rows


def build_corpus(seasons: tuple[str, ...] = SEASONS) -> dict:
    counts = {}
    with open(GOALIE_OUTPUT_PATH, "w") as gf, open(TEAM_SOG_OUTPUT_PATH, "w") as tf:
        for season in seasons:
            game_ids = raw_archive.archived_game_ids(season)
            n_goalie_rows = 0
            n_team_rows = 0
            for gid in game_ids:
                goalie_rows, team_rows = build_one_game(season, gid)
                for row in goalie_rows:
                    gf.write(json.dumps(row) + "\n")
                for row in team_rows:
                    tf.write(json.dumps(row) + "\n")
                n_goalie_rows += len(goalie_rows)
                n_team_rows += len(team_rows)
            counts[season] = {"games": len(game_ids), "goalie_rows": n_goalie_rows, "team_rows": n_team_rows}
    return counts


if __name__ == "__main__":
    result = build_corpus()
    for season, c in result.items():
        print(season, c)
