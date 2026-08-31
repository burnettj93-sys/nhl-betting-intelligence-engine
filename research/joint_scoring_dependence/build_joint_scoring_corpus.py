"""
Part 1/2: real joint scoring corpus linking Player SOG + Goals + Assists
+ Points for the same real player-game. All four already-real, already-
validated corpora (research/player_sog/, research/player_goals/,
research/player_assists/, research/player_points/) are independently
derived from the same underlying per-player-game skater-stats source and
share an identical row count (188,863) and (player_id, game_id) key
space -- verified directly, not assumed, by joining on that key.

Part 2's event-identity reconciliation, verified against the REAL joined
data before this corpus was written (not asserted after the fact):
  - points == goals + assists:            0/188,863 violations
  - a player's SOG >= their own goals (a statistical goal always
    counts as at least one SOG; a multi-goal game requires that many
    SOG at minimum): 0/188,863 violations
Both are enforced as hard assertions during the build -- if either ever
fails on a future re-run (e.g. after a corpus regeneration), this module
raises immediately rather than silently writing an incoherent corpus.
"""
from __future__ import annotations

import json
import os

from research.player_goals import features as gof

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "joint_scoring.jsonl")


def build_corpus() -> dict:
    goals_rows = gof.load_goals_corpus()  # already carries goals, assists, points
    sog_rows = {}
    with open(os.path.join(os.path.dirname(__file__), "..", "player_sog", "player_game_sog.jsonl")) as f:
        for line in f:
            r = json.loads(line)
            sog_rows[(r["player_id"], r["game_id"])] = r["sog"]
    pp_assists_rows = {}
    with open(os.path.join(os.path.dirname(__file__), "..", "player_assists", "player_game_assists.jsonl")) as f:
        for line in f:
            r = json.loads(line)
            pp_assists_rows[(r["player_id"], r["game_id"])] = r["pp"]["assists"] if r["pp"] is not None else 0.0

    counts = {}
    joint_rows = []
    missing_sog = 0
    for r in goals_rows:
        key = (r["player_id"], r["game_id"])
        sog = sog_rows.get(key)
        if sog is None:
            missing_sog += 1
            continue
        if r["points"] != r["goals"] + r["assists"]:
            raise ValueError(f"points != goals+assists for {key}: {r['points']} != {r['goals']}+{r['assists']}")
        if r["goals"] > 0 and sog < r["goals"]:
            raise ValueError(f"SOG < goals for {key}: sog={sog} goals={r['goals']}")

        joint_rows.append({
            "game_id": r["game_id"], "game_date": r["game_date"], "season": r["season"],
            "player_id": r["player_id"], "player_name": r["player_name"], "position": r["position"],
            "team": r["team"], "opponent": r["opponent"], "home_or_away": r["home_or_away"],
            "actual_sog": sog, "actual_goals": r["goals"], "actual_assists": r["assists"],
            "actual_points": r["points"],
            "pp_goals": r["pp"]["goals"] if r["pp"] is not None else 0.0,
            "pp_assists": pp_assists_rows.get(key, 0.0),
        })
        counts.setdefault(r["season"], {"rows": 0})
        counts[r["season"]]["rows"] += 1

    with open(OUTPUT_PATH, "w") as f:
        for row in joint_rows:
            f.write(json.dumps(row) + "\n")

    counts["total_rows"] = len(joint_rows)
    counts["missing_sog_join"] = missing_sog
    return counts


if __name__ == "__main__":
    result = build_corpus()
    for k, v in sorted(result.items(), key=lambda kv: str(kv[0])):
        print(k, v)
