"""
PIT-safe player-level GOALS features. Reuses the genuinely prop-agnostic
parts of research/player_sog/features.py directly (same justification as
every other prop this session) -- PlayerHistoryIndex, player_history_as_of,
rolling_mean, season_to_date_mean, projected_active, parse_date.

New here (goals-specific, independently value-tested):
  - rolling_pp_mean                 (PP role signal, same nested-block pattern)
  - team goals-for / opponent goals-allowed environment (Part 17/18)
  - H2H shrinkage over GOALS *and* over SOG separately (Part 16 --
    "H2H shots useful, H2H goals useless" is a real hypothesis to test,
    not assumed either way)
  - career shooting-talent shrinkage (Part 8/21/22) -- shrinks toward a
    role/league prior by CAREER SHOT VOLUME (not game count), since
    shooting percentage is a per-shot rate, not a per-game rate; heavier
    shrinkage for low-volume shooters, consistent with the real season-
    to-season persistence-by-volume finding in this slice's own audit.
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

CORPUS_PATH = Path(__file__).resolve().parent / "player_game_goals.jsonl"
H2H_SHRINKAGE_GAMES = 10
SHOOTING_TALENT_SHRINKAGE_SHOTS = 150   # frozen via dev-sandbox grid search, see run_player_goals_model.py


def load_goals_corpus(path: str | Path = CORPUS_PATH) -> list[dict]:
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


# --------------------------------------------------------------------------
# Part 8/21/22: shooting-talent shrinkage, by CAREER SHOT VOLUME.
# --------------------------------------------------------------------------

def career_shooting_pct_shrunk(history: list[dict], league_shooting_pct: float,
                                shrinkage_shots: int = SHOOTING_TALENT_SHRINKAGE_SHOTS) -> tuple[float, int]:
    """Returns (shrunk_shooting_pct, career_shots_in_history). Shrinkage
    weight is n_shots/(n_shots+shrinkage_shots) -- CAREER shot volume,
    not game count, since shooting% is inherently a per-shot statistic
    (a 40-game player with 200 shots has a far more reliable shooting%
    than a 40-game player with 20 shots)."""
    career_shots = sum(r["sog"] for r in history)
    if career_shots <= 0:
        return league_shooting_pct, 0
    career_goals = sum(r["goals"] for r in history)
    raw_pct = career_goals / career_shots
    w = career_shots / (career_shots + shrinkage_shots)
    return league_shooting_pct + w * (raw_pct - league_shooting_pct), int(career_shots)


# --------------------------------------------------------------------------
# Part 17/18: team goals-for / opponent goals-allowed environment.
# --------------------------------------------------------------------------

def build_team_game_goals_totals(all_rows: list[dict]) -> dict[tuple[str, int], dict]:
    totals: dict[tuple[str, int], dict] = {}
    for r in all_rows:
        key = (r["team"], r["game_id"])
        if key not in totals:
            totals[key] = {"game_date": r["game_date"], "season": r["season"], "opponent": r["opponent"],
                            "goals_for": 0.0}
        totals[key]["goals_for"] += r["goals"]
    return totals


def build_team_offense_history(team_game_totals: dict[tuple[str, int], dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for (team, game_id), row in team_game_totals.items():
        out[team].append({"game_date": row["game_date"], "game_id": game_id, "season": row["season"],
                           "goals_for": row["goals_for"]})
    for team in out:
        out[team].sort(key=lambda r: (r["game_date"], r["game_id"]))
    return out


def team_history_as_of(team_history: dict[str, list[dict]], team: str, prediction_game_date: str) -> list[dict]:
    rows = team_history.get(team, [])
    return [r for r in rows if r["game_date"] < prediction_game_date]


def rolling_team_goals_for(team_history: dict[str, list[dict]], team: str,
                            prediction_game_date: str, window: int) -> float | None:
    hist = team_history_as_of(team_history, team, prediction_game_date)
    recent = hist[-window:]
    if not recent:
        return None
    return statistics.fmean(r["goals_for"] for r in recent)


def build_opponent_goals_allowed(team_game_totals: dict[tuple[str, int], dict]) -> dict[str, list[dict]]:
    by_team_game = dict(team_game_totals)
    out: dict[str, list[dict]] = defaultdict(list)
    for (team, game_id), row in team_game_totals.items():
        opp = row["opponent"]
        opp_offense = by_team_game.get((opp, game_id))
        if opp_offense is None:
            continue
        out[team].append({"game_date": row["game_date"], "game_id": game_id, "season": row["season"],
                           "goals_allowed": opp_offense["goals_for"]})
    for team in out:
        out[team].sort(key=lambda r: (r["game_date"], r["game_id"]))
    return out


def opponent_history_as_of(opponent_allowed: dict[str, list[dict]], team: str, prediction_game_date: str) -> list[dict]:
    rows = opponent_allowed.get(team, [])
    return [r for r in rows if r["game_date"] < prediction_game_date]


def rolling_opponent_goals_allowed(opponent_allowed: dict[str, list[dict]], team: str,
                                    prediction_game_date: str, window: int) -> float | None:
    hist = opponent_history_as_of(opponent_allowed, team, prediction_game_date)
    recent = hist[-window:]
    if not recent:
        return None
    return statistics.fmean(r["goals_allowed"] for r in recent)


# --------------------------------------------------------------------------
# Part 16: H2H over GOALS and separately over SOG -- independently
# tested, not assumed to behave the same way.
# --------------------------------------------------------------------------

def h2h_history(history: list[dict], opponent: str) -> list[dict]:
    return [r for r in history if r["opponent"] == opponent]


def h2h_shrunk_goals_rate(history: list[dict], opponent: str, baseline_rate: float) -> tuple[float, int]:
    h2h = h2h_history(history, opponent)
    n = len(h2h)
    if n == 0:
        return baseline_rate, 0
    h2h_mean = statistics.fmean(r["goals"] for r in h2h)
    shrink = n / (n + H2H_SHRINKAGE_GAMES)
    return baseline_rate + shrink * (h2h_mean - baseline_rate), n


def h2h_shrunk_sog_rate(history: list[dict], opponent: str, baseline_sog_rate: float) -> tuple[float, int]:
    h2h = h2h_history(history, opponent)
    n = len(h2h)
    if n == 0:
        return baseline_sog_rate, 0
    h2h_mean = statistics.fmean(r["sog"] for r in h2h)
    shrink = n / (n + H2H_SHRINKAGE_GAMES)
    return baseline_sog_rate + shrink * (h2h_mean - baseline_sog_rate), n
