"""
PIT-safe player-level TOTAL POINTS features. Reuses the genuinely
prop-agnostic parts of research/player_sog/features.py directly (same
justification as research/player_blocks/features.py and
research/player_assists/features.py) -- PlayerHistoryIndex,
player_history_as_of, rolling_mean, season_to_date_mean, projected_active,
parse_date.

New here (points-specific, independently value-tested -- Parts 8/9/12/
13/14/15 each get their own real test, nothing assumed from SOG/blocks/
assists):
  - rolling_pp_mean            (PP role signal, mirrors the SOG "pp" /
                                 blocks "pk" nested-block pattern, but
                                 over POINTS scored on the power play)
  - team offense environment    (Part 14: team's own rolling points-for,
                                 a TEAM-context feature -- distinct from
                                 opponent context below)
  - opponent points-allowed     (Part 15: what the upcoming opponent has
                                 historically allowed -- same technique as
                                 research/player_assists/features.py's
                                 opponent-context functions, independently
                                 rebuilt here over the points corpus)
  - H2H shrinkage over points   (Part 12)
"""
from __future__ import annotations

import statistics
from collections import defaultdict
import json
from pathlib import Path

from research.player_sog.features import (
    PlayerHistoryIndex, ELIGIBILITY_WINDOW_TEAM_GAMES, ELIGIBILITY_MIN_APPEARANCES,
    parse_date, player_history_as_of, projected_active, rolling_mean, season_to_date_mean,
)

CORPUS_PATH = Path(__file__).resolve().parent / "player_game_points.jsonl"
H2H_SHRINKAGE_GAMES = 10


def load_points_corpus(path: str | Path = CORPUS_PATH) -> list[dict]:
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    rows.sort(key=lambda r: (r["game_date"], r["game_id"], r["player_id"]))
    return rows


# --------------------------------------------------------------------------
# Part 9: power-play role. `pp` is a nested block, None on games with zero
# 5-on-4 icetime -- treated as a real, PIT-safe 0 for rolling means (same
# convention as the SOG "pp" / blocks "pk" blocks).
# --------------------------------------------------------------------------

def rolling_pp_mean(history: list[dict], field: str, window: int | None) -> float | None:
    recent = history if window is None else history[-window:]
    if not recent:
        return None
    values = [(r["pp"][field] if r["pp"] is not None else 0.0) for r in recent]
    return statistics.fmean(values)


# --------------------------------------------------------------------------
# Part 14/15: team-game POINTS totals, the shared aggregate both the
# team-context (Part 14, team's own offense) and opponent-context (Part
# 15, opponent's defense/points allowed) features are built from.
# --------------------------------------------------------------------------

def build_team_game_points_totals(all_rows: list[dict]) -> dict[tuple[str, int], dict]:
    totals: dict[tuple[str, int], dict] = {}
    for r in all_rows:
        key = (r["team"], r["game_id"])
        if key not in totals:
            totals[key] = {"game_date": r["game_date"], "season": r["season"], "opponent": r["opponent"],
                            "points_for": 0.0}
        totals[key]["points_for"] += r["points"]
    return totals


def build_team_offense_history(team_game_totals: dict[tuple[str, int], dict]) -> dict[str, list[dict]]:
    """Part 14: TEAM's own rolling points-for -- a team-offensive-
    environment proxy, distinct from what the opponent allows below."""
    out: dict[str, list[dict]] = defaultdict(list)
    for (team, game_id), row in team_game_totals.items():
        out[team].append({"game_date": row["game_date"], "game_id": game_id, "season": row["season"],
                           "points_for": row["points_for"]})
    for team in out:
        out[team].sort(key=lambda r: (r["game_date"], r["game_id"]))
    return out


def team_history_as_of(team_history: dict[str, list[dict]], team: str, prediction_game_date: str) -> list[dict]:
    rows = team_history.get(team, [])
    return [r for r in rows if r["game_date"] < prediction_game_date]


def rolling_team_points_for(team_history: dict[str, list[dict]], team: str,
                             prediction_game_date: str, window: int) -> float | None:
    hist = team_history_as_of(team_history, team, prediction_game_date)
    recent = hist[-window:]
    if not recent:
        return None
    return statistics.fmean(r["points_for"] for r in recent)


def build_opponent_points_allowed(team_game_totals: dict[tuple[str, int], dict]) -> dict[str, list[dict]]:
    """Part 15: what team X's OWN opponents have scored against X, i.e.
    points allowed BY X -- querying this dict keyed by the UPCOMING
    opponent's name gives that opponent's own defensive environment
    (same technique as research/player_assists/features.py, rebuilt here
    independently over the points corpus)."""
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


# --------------------------------------------------------------------------
# Part 12: head-to-head, shrunk by GAME count over the POINTS label.
# --------------------------------------------------------------------------

def h2h_history(history: list[dict], opponent: str) -> list[dict]:
    return [r for r in history if r["opponent"] == opponent]


def h2h_shrunk_points_rate(history: list[dict], opponent: str, baseline_rate: float) -> tuple[float, int]:
    h2h = h2h_history(history, opponent)
    n = len(h2h)
    if n == 0:
        return baseline_rate, 0
    h2h_mean = statistics.fmean(r["points"] for r in h2h)
    shrink = n / (n + H2H_SHRINKAGE_GAMES)
    return baseline_rate + shrink * (h2h_mean - baseline_rate), n
