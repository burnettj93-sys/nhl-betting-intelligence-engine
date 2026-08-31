"""
PIT-safe goalie-save and team-SOG features, built from
research/goalie_saves/goalie_game_saves.jsonl and team_game_sog.jsonl.
Mirrors research/team_goals_period/features.py's discipline exactly (same
STRICT PRIOR-GAME-DATE gate via bisect, same rolling/H2H-shrinkage style)
-- rebuilt here rather than imported, per this project's per-package
convention.

TARGET-GAME SAVES/SHOTS-FACED NEVER APPEAR ON THE FEATURE SIDE: every
function takes an explicit `prediction_game_date` and only reads rows with
`game_date < prediction_game_date`.
"""
from __future__ import annotations

import bisect
import datetime as dt
import json
import statistics
from collections import defaultdict
from pathlib import Path

GOALIE_CORPUS_PATH = Path(__file__).resolve().parent / "goalie_game_saves.jsonl"
TEAM_SOG_CORPUS_PATH = Path(__file__).resolve().parent / "team_game_sog.jsonl"

H2H_SHRINKAGE_GAMES = 8


def load_goalie_corpus(path: str | Path = GOALIE_CORPUS_PATH) -> list[dict]:
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    rows.sort(key=lambda r: (r["game_date"], r["game_id"], r["goalie_id"]))
    return rows


def load_team_sog_corpus(path: str | Path = TEAM_SOG_CORPUS_PATH) -> list[dict]:
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    rows.sort(key=lambda r: (r["game_date"], r["game_id"], r["team"]))
    return rows


def parse_date(date_str: str) -> dt.date:
    return dt.date.fromisoformat(date_str)


class GoalieHistoryIndex:
    """Indexed by goalie_id, ALL real appearances (starts + relief) in
    chronological order -- callers filter to starts-only where the
    population definition (Part 2) requires it via `starts_only()`."""

    def __init__(self, rows: list[dict]):
        by_goalie = defaultdict(list)
        for r in rows:
            by_goalie[r["goalie_id"]].append(r)
        self._by_goalie: dict[int, tuple[list[str], list[dict]]] = {}
        for goalie_id, grows in by_goalie.items():
            ordered = sorted(grows, key=lambda r: (r["game_date"], r["game_id"]))
            self._by_goalie[goalie_id] = ([r["game_date"] for r in ordered], ordered)

    def history_as_of(self, goalie_id: int, prediction_game_date: str) -> list[dict]:
        entry = self._by_goalie.get(goalie_id)
        if entry is None:
            return []
        dates, ordered = entry
        cut = bisect.bisect_left(dates, prediction_game_date)
        return ordered[:cut]


class TeamSogHistoryIndex:
    """Indexed by team abbrev, one row per team per game (from
    team_game_sog.jsonl) -- doubles as this team's real schedule for
    rest/back-to-back detection, same trick research/goalie_intelligence
    /features.py uses over its own label corpus."""

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


def starts_only(history: list[dict]) -> list[dict]:
    return [r for r in history if r["actual_started"]]


def rolling_mean(history: list[dict], field: str, window: int | None) -> float | None:
    recent = history if window is None else history[-window:]
    if not recent:
        return None
    return statistics.fmean(r[field] for r in recent)


def rolling_save_pct(history: list[dict], window: int) -> float | None:
    recent = history[-window:]
    total_shots = sum(r["actual_shots_faced"] for r in recent)
    if total_shots == 0:
        return None
    return sum(r["actual_saves"] for r in recent) / total_shots


def h2h_shrunk_rate(history: list[dict], opponent: str, field: str, baseline: float,
                     shrinkage_games: int = H2H_SHRINKAGE_GAMES) -> tuple[float, int]:
    h2h = [r for r in history if r["opponent"] == opponent]
    n = len(h2h)
    if n == 0:
        return baseline, 0
    h2h_mean = statistics.fmean(r[field] for r in h2h)
    shrink = n / (n + shrinkage_games)
    return baseline + shrink * (h2h_mean - baseline), n


def team_rest_days(team_history: list[dict], prediction_game_date: str) -> int | None:
    if not team_history:
        return None
    last_date = team_history[-1]["game_date"]
    return (parse_date(prediction_game_date) - parse_date(last_date)).days


def team_is_back_to_back(team_history: list[dict], prediction_game_date: str) -> bool:
    d = team_rest_days(team_history, prediction_game_date)
    return d == 1


def goalie_rest_days(goalie_history: list[dict], prediction_game_date: str) -> int | None:
    if not goalie_history:
        return None
    last_date = goalie_history[-1]["game_date"]
    return (parse_date(prediction_game_date) - parse_date(last_date)).days


def goalie_played_previous_night(goalie_history: list[dict], prediction_game_date: str) -> bool:
    d = goalie_rest_days(goalie_history, prediction_game_date)
    return d == 1
