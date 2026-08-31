"""
Core period-timing / first-goal / score-state / empty-net research
(Parts 10-15, 18-22), built from ONE additional full-corpus pass over
research/real_nhl_pbp/research_pbp.db via
research.period_event_timing.event_extraction.extract_game -- kept
separate from run_period_event_timing_special_teams.py's own pass
(Part 69: two genuinely different extractions, not worth forcing into
one combined function).

Run manually:
    python3 -m research.run_period_event_timing_core
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.period_event_timing import event_extraction as ee
from research.real_nhl_pbp.store import DB_PATH

RESULTS_PATH = REPO_ROOT / "research" / "period_event_timing_core_results.json"


def _score_bucket(diff: int) -> str:
    if diff == 0:
        return "TIED"
    if diff == 1:
        return "LEADING_1"
    if diff >= 2:
        return "LEADING_2PLUS"
    if diff == -1:
        return "TRAILING_1"
    return "TRAILING_2PLUS"


def build_all(db_path: str = DB_PATH) -> dict:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT game_id, home_team_id, away_team_id, season FROM pbp_games ORDER BY game_id")
    games = cur.fetchall()

    t0 = time.time()
    period_goal_counts: dict[str, int] = {}      # "1"/"2"/"3"/"OT" -> total goals
    period_sog_counts: dict[str, int] = {}
    within_period_bins: dict[int, int] = {}
    first_goal_seconds: list[int] = []
    scoreless_through = {"5min": 0, "10min": 0, "15min": 0, "end_of_p1": 0}
    first_team_to_score = {"HOME": 0, "AWAY": 0, "NONE": 0}
    n_games_with_goal = 0
    goal_score_state_period: dict[tuple, int] = {}   # (period_number_or_OT, bucket) -> count
    sog_score_state_period: dict[tuple, int] = {}
    strength_type_goal_counts: dict[str, int] = {}
    pulls_by_reason: dict[str, int] = {}
    trailing_pulls: list[dict] = []   # OTHER-reason pulls only, for the empty-net timing model
    n_games = 0
    n_ot_games = 0

    for game_id, home_team_id, away_team_id, season in games:
        g = ee.extract_game(conn, game_id, home_team_id, away_team_id, season)
        n_games += 1
        for period_num, n in g["period_goal_counts"].items():
            key = str(period_num) if period_num <= 3 else "OT"
            period_goal_counts[key] = period_goal_counts.get(key, 0) + n
        for period_num, n in g["period_sog_counts"].items():
            key = str(period_num) if period_num <= 3 else "OT"
            period_sog_counts[key] = period_sog_counts.get(key, 0) + n
        for bin_idx, n in g["within_period_goal_bins"].items():
            within_period_bins[bin_idx] = within_period_bins.get(bin_idx, 0) + n

        if g["goals"]:
            n_games_with_goal += 1
            fgs = g["first_goal_seconds_into_game"]
            first_goal_seconds.append(fgs)
            if fgs >= 300:
                scoreless_through["5min"] += 1
            if fgs >= 600:
                scoreless_through["10min"] += 1
            if fgs >= 900:
                scoreless_through["15min"] += 1
            if fgs >= 1200:
                scoreless_through["end_of_p1"] += 1
        else:
            for k in scoreless_through:
                scoreless_through[k] += 1
        first_team_to_score[g["first_team_to_score"]] += 1

        for goal in g["goals"]:
            period_key = goal["period_number"] if goal["period_number"] <= 3 else "OT"
            bucket = _score_bucket(goal["score_differential_before"])
            key = (period_key, bucket)
            goal_score_state_period[key] = goal_score_state_period.get(key, 0) + 1
            strength_type_goal_counts[goal["strength_type"]] = strength_type_goal_counts.get(
                goal["strength_type"], 0) + 1
            if goal["period_type"] == "OT":
                n_ot_games += 0  # counted separately below

        for side, info in g["first_pull"].items():
            pulls_by_reason[info["reason"]] = pulls_by_reason.get(info["reason"], 0) + 1
            if info["reason"] == "OTHER":
                trailing_pulls.append(info)

    elapsed = time.time() - t0

    n_first_goal_home = first_team_to_score["HOME"]
    n_decided = first_team_to_score["HOME"] + first_team_to_score["AWAY"]
    home_first_goal_rate = n_first_goal_home / n_decided if n_decided else None

    sorted_fgs = sorted(first_goal_seconds)
    median_fgs = sorted_fgs[len(sorted_fgs) // 2] if sorted_fgs else None

    # Trailing-pull timing conditioned on score/time (Part 21/22): bucket
    # by score differential at the moment of the pull, report the mean/
    # median "seconds remaining in regulation" (3600 - seconds_into_game,
    # clipped at 0 for pulls that happen in OT).
    pulls_by_score_diff: dict[int, list[float]] = {}
    for p in trailing_pulls:
        diff = p["score_differential_at_pull"]
        remaining = max(0, 3600 - p["seconds_into_game"])
        pulls_by_score_diff.setdefault(diff, []).append(remaining)
    pull_timing_by_score_diff = {}
    for diff, remainings in pulls_by_score_diff.items():
        s = sorted(remainings)
        pull_timing_by_score_diff[str(diff)] = {
            "n": len(s), "mean_seconds_remaining": sum(s) / len(s),
            "median_seconds_remaining": s[len(s) // 2],
        }

    return {
        "built_at_seconds": elapsed, "games_processed": n_games,
        "period_goal_counts": period_goal_counts, "period_sog_counts": period_sog_counts,
        "period_goal_rate_per_game": {k: v / n_games for k, v in period_goal_counts.items()},
        "period_sog_rate_per_game": {k: v / n_games for k, v in period_sog_counts.items()},
        "within_period_goal_bins_5min": within_period_bins,
        "first_goal_seconds_mean": sum(first_goal_seconds) / len(first_goal_seconds) if first_goal_seconds else None,
        "first_goal_seconds_median": median_fgs,
        "games_with_zero_goals": n_games - n_games_with_goal,
        "scoreless_through_probability": {k: v / n_games for k, v in scoreless_through.items()},
        "first_team_to_score_counts": first_team_to_score,
        "home_first_goal_rate": home_first_goal_rate,
        "goal_counts_by_period_and_score_state": {f"{k[0]}|{k[1]}": v for k, v in goal_score_state_period.items()},
        "goal_counts_by_strength_type": strength_type_goal_counts,
        "goalie_pulls_by_reason": pulls_by_reason,
        "trailing_pull_timing_by_score_differential": pull_timing_by_score_diff,
        "n_trailing_pulls_analyzed": len(trailing_pulls),
    }


if __name__ == "__main__":
    result = build_all()
    with open(RESULTS_PATH, "w") as f:
        json.dump(result, f, indent=2, sort_keys=True, default=str)
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
