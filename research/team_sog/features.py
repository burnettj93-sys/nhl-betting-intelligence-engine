"""
PIT-safe team-SOG features, built from research/team_sog/team_game_sog.jsonl.
Mirrors research/team_goals_period/features.py's discipline exactly (same
STRICT PRIOR-GAME-DATE gate via bisect, same rolling/H2H-shrinkage style)
-- rebuilt here rather than imported, per this project's per-package
convention.

TARGET-GAME SOG NEVER APPEARS ON THE FEATURE SIDE: every function takes an
explicit `prediction_game_date` and only reads rows with
`game_date < prediction_game_date`.
"""
from __future__ import annotations

import bisect
import datetime as dt
import json
import statistics
from collections import defaultdict
from pathlib import Path

CORPUS_PATH = Path(__file__).resolve().parent / "team_game_sog.jsonl"

H2H_SHRINKAGE_GAMES = 8


def load_team_sog_corpus(path: str | Path = CORPUS_PATH) -> list[dict]:
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    rows.sort(key=lambda r: (r["game_date"], r["game_id"], r["team"]))
    return rows


class TeamHistoryIndex:
    def __init__(self, rows: list[dict]):
        by_team = defaultdict(list)
        for r in rows:
            by_team[r["team"]].append(r)
        self._by_team: dict[str, tuple[list[str], list[dict]]] = {}
        for team, trows in by_team.items():
            ordered = sorted(trows, key=lambda r: (r["game_date"], r["game_id"]))
            self._by_team[team] = ([r["game_date"] for r in ordered], ordered)

    def history_as_of(self, team: str, prediction_game_date: str) -> list[dict]:
        entry = self._by_team.get(team)
        if entry is None:
            return []
        dates, ordered = entry
        cut = bisect.bisect_left(dates, prediction_game_date)
        return ordered[:cut]


def rolling_mean(history: list[dict], field: str, window: int | None) -> float | None:
    recent = history if window is None else history[-window:]
    if not recent:
        return None
    return statistics.fmean(r[field] for r in recent)


def h2h_shrunk_rate(history: list[dict], opponent: str, field: str, baseline: float,
                     shrinkage_games: int = H2H_SHRINKAGE_GAMES) -> tuple[float, int]:
    h2h = [r for r in history if r["opponent"] == opponent]
    n = len(h2h)
    if n == 0:
        return baseline, 0
    h2h_mean = statistics.fmean(r[field] for r in h2h)
    shrink = n / (n + shrinkage_games)
    return baseline + shrink * (h2h_mean - baseline), n


def parse_date(date_str: str) -> dt.date:
    return dt.date.fromisoformat(date_str)


def team_rest_days(team_history: list[dict], prediction_game_date: str) -> int | None:
    if not team_history:
        return None
    last_date = team_history[-1]["game_date"]
    return (parse_date(prediction_game_date) - parse_date(last_date)).days


def team_is_back_to_back(team_history: list[dict], prediction_game_date: str) -> bool:
    d = team_rest_days(team_history, prediction_game_date)
    return d == 1


def games_in_previous_n_days(team_history: list[dict], prediction_game_date: str, n_days: int) -> int:
    cutoff = parse_date(prediction_game_date)
    return sum(1 for r in team_history if 0 < (cutoff - parse_date(r["game_date"])).days <= n_days)
