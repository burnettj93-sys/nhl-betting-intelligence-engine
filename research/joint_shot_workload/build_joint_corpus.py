"""
Part 1: real joint corpus linking PLAYER-game + TEAM-game + OPPOSING
GOALIE-game rows, built by joining three already-real, already-validated
label corpora (research/player_sog/, research/team_sog/,
research/goalie_saves/) -- no new event-level extraction, this is purely
an accounting join on (team, game_id).

Part 2's population definition: a joint row exists only where the
opponent's real STARTING goalie for that game can be identified
(`goalie_start_status == "STARTER"`) -- the headline recommendation.
`goalie_full_game_status` distinguishes whether that starter was the
ONLY goalie the opponent used that game (`FULL_GAME`) or was relieved
mid-game (`RELIEVED`) -- Part 2's explicit sensitivity-population split,
reported separately, never silently dropped.

`empty_net_sog_count` is the real, disclosed structural gap already
quantified in the Team SOG and Goalie Saves slices: team SOG minus the
SUM of shots faced across every goalie the opponent used that game
(always >= 0 -- an empty-net shot adds to the shooting team's SOG but is
never any goalie's shot faced).
"""
from __future__ import annotations

import json
import os
from collections import defaultdict

from research.goalie_saves import features as gf
from research.player_sog import features as pf
from research.team_sog import features as tf

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "joint_shot_workload.jsonl")


def build_corpus() -> dict:
    player_rows = pf.load_sog_corpus()
    team_rows = tf.load_team_sog_corpus()
    goalie_rows = gf.load_goalie_corpus()

    team_by_game = {(r["team"], r["game_id"]): r for r in team_rows}
    goalie_by_game_team: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for r in goalie_rows:
        goalie_by_game_team[(r["team"], r["game_id"])].append(r)

    counts = defaultdict(lambda: {"player_rows": 0, "joint_rows": 0, "no_team_row": 0, "no_opponent_starter": 0})
    joint_rows = []
    for pr in player_rows:
        season = pr["season"]
        counts[season]["player_rows"] += 1
        team_row = team_by_game.get((pr["team"], pr["game_id"]))
        if team_row is None:
            counts[season]["no_team_row"] += 1
            continue

        opp_goalies = goalie_by_game_team.get((pr["opponent"], pr["game_id"]), [])
        opp_starter = next((g for g in opp_goalies if g["actual_started"]), None)
        if opp_starter is None:
            counts[season]["no_opponent_starter"] += 1
            continue

        opp_shots_faced_sum = sum(g["actual_shots_faced"] for g in opp_goalies)
        empty_net_sog_count = team_row["actual_team_sog"] - opp_shots_faced_sum
        multi_goalie = len(opp_goalies) > 1

        joint_rows.append({
            "game_id": pr["game_id"], "game_date": pr["game_date"], "season": season,
            "player_id": pr["player_id"], "player_team": pr["team"], "opponent_team": pr["opponent"],
            "home_or_away": pr["home_or_away"],
            "actual_player_sog": pr["sog"],
            "actual_team_sog": team_row["actual_team_sog"],
            "P1_team_sog": team_row["P1_team_sog"], "P2_team_sog": team_row["P2_team_sog"],
            "P3_team_sog": team_row["P3_team_sog"],
            "opposing_goalie_id": opp_starter["goalie_id"],
            "actual_goalie_saves": opp_starter["actual_saves"],
            "actual_goalie_shots_faced": opp_starter["actual_shots_faced"],
            "goalie_start_status": "STARTER",
            "goalie_full_game_status": "RELIEVED" if multi_goalie else "FULL_GAME",
            "multi_goalie_game": multi_goalie,
            "empty_net_sog_count": empty_net_sog_count,
        })
        counts[season]["joint_rows"] += 1

    with open(OUTPUT_PATH, "w") as f:
        for row in joint_rows:
            f.write(json.dumps(row) + "\n")

    return dict(counts)


if __name__ == "__main__":
    result = build_corpus()
    for season, c in sorted(result.items()):
        print(season, c)
