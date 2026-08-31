"""
PIT-safe player-level ASSISTS features. Reuses the genuinely prop-
agnostic parts of research/player_sog/features.py directly (same
justification as research/player_blocks/features.py) -- PlayerHistoryIndex,
player_history_as_of, rolling_mean, season_to_date_mean, projected_active.
Only the assists-specific opponent-context aggregation (team POINTS
allowed, a defensive-environment proxy relevant to assist opportunity)
and H2H shrinkage over the assists label are new here.
"""
from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path

from research.player_sog.features import (
    PlayerHistoryIndex, ELIGIBILITY_WINDOW_TEAM_GAMES, ELIGIBILITY_MIN_APPEARANCES,
    parse_date, player_history_as_of, projected_active, rolling_mean, season_to_date_mean,
)

CORPUS_PATH = Path(__file__).resolve().parent / "player_game_assists.jsonl"
H2H_SHRINKAGE_GAMES = 10


def load_assists_corpus(path: str | Path = CORPUS_PATH) -> list[dict]:
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    rows.sort(key=lambda r: (r["game_date"], r["game_id"], r["player_id"]))
    return rows


def rolling_pp_mean(history: list[dict], field: str, window: int | None) -> float | None:
    recent = history if window is None else history[-window:]
    if not recent:
        return None
    values = [(r["pp"][field] if r["pp"] is not None else 0.0) for r in recent]
    return statistics.fmean(values)


def build_team_game_points_totals(all_rows: list[dict]) -> dict[tuple[str, int], dict]:
    totals: dict[tuple[str, int], dict] = {}
    for r in all_rows:
        key = (r["team"], r["game_id"])
        if key not in totals:
            totals[key] = {"game_date": r["game_date"], "season": r["season"], "opponent": r["opponent"],
                            "points_for": 0.0}
        totals[key]["points_for"] += r["points"]
    return totals


def build_opponent_points_allowed(team_game_totals: dict[tuple[str, int], dict]) -> dict[str, list[dict]]:
    by_team_game = dict(team_game_totals)
    out: dict[str, list[dict]] = defaultdict(list)
    for (team, game_id), row in team_game_totals.items():
        opp = row["opponent"]
        opp_offense = by_team_game.get((opp, game_id))
        if opp_offense is None:
            continue
        out[team].append({"game_date": row["game_date"], "game_id": game_id, "season": row["season"],
                           "points_allowed": opp_offense["points_for"]})
    for team in out:
        out[team].sort(key=lambda r: (r["game_date"], r["game_id"]))
    return out


def opponent_history_as_of(opponent_allowed: dict[str, list[dict]], team: str, prediction_game_date: str) -> list[dict]:
    rows = opponent_allowed.get(team, [])
    return [r for r in rows if r["game_date"] < prediction_game_date]


def rolling_opponent_points_allowed(opponent_allowed: dict[str, list[dict]], team: str,
                                     prediction_game_date: str, window: int) -> float | None:
    hist = opponent_history_as_of(opponent_allowed, team, prediction_game_date)
    recent = hist[-window:]
    if not recent:
        return None
    return statistics.fmean(r["points_allowed"] for r in recent)


def h2h_history(history: list[dict], opponent: str) -> list[dict]:
    return [r for r in history if r["opponent"] == opponent]


def h2h_shrunk_assists_rate(history: list[dict], opponent: str, baseline_rate: float) -> tuple[float, int]:
    h2h = h2h_history(history, opponent)
    n = len(h2h)
    if n == 0:
        return baseline_rate, 0
    h2h_mean = statistics.fmean(r["assists"] for r in h2h)
    shrink = n / (n + H2H_SHRINKAGE_GAMES)
    return baseline_rate + shrink * (h2h_mean - baseline_rate), n
