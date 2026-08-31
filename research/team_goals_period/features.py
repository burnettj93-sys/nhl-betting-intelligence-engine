"""
PIT-safe team-period goal features, built from
research/team_goals_period/team_game_period_goals.jsonl. Mirrors
research/player_sog_period/features.py's discipline exactly (same STRICT
PRIOR-GAME-DATE gate, same shrinkage-by-game-count convention) at team
granularity -- rebuilt here rather than imported, matching this project's
per-package convention.

TARGET-GAME PERIOD GOALS NEVER APPEAR ON THE FEATURE SIDE (Part 3): every
function takes an explicit `prediction_game_date` and only reads rows with
`game_date < prediction_game_date`.
"""
from __future__ import annotations

import bisect
import json
import statistics
from collections import defaultdict
from pathlib import Path

CORPUS_PATH = Path(__file__).resolve().parent / "team_game_period_goals.jsonl"

PERIODS = (1, 2, 3)
H2H_SHRINKAGE_GAMES = 10


def load_team_period_corpus(path: str | Path = CORPUS_PATH) -> list[dict]:
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    rows.sort(key=lambda r: (r["game_date"], r["game_id"], r["team"]))
    return rows


class TeamPeriodHistoryIndex:
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


def rolling_period_mean(history: list[dict], period: int, window: int | None) -> float | None:
    recent = history if window is None else history[-window:]
    if not recent:
        return None
    return statistics.fmean(r[f"period_{period}_goals"] for r in recent)


def rolling_opponent_period_allowed(history: list[dict], period: int, window: int) -> float | None:
    """Team's own history already carries `opponent_period_k_goals` (what
    THEY allowed that game) -- Part 9's defensive context, no second join
    needed (the corpus builder already computed this symmetrically)."""
    recent = history[-window:]
    if not recent:
        return None
    return statistics.fmean(r[f"opponent_period_{period}_goals"] for r in recent)


def h2h_period_shrunk_rate(history: list[dict], opponent: str, period: int, baseline_rate: float,
                            shrinkage_games: int = H2H_SHRINKAGE_GAMES) -> tuple[float, int]:
    h2h = [r for r in history if r["opponent"] == opponent]
    n = len(h2h)
    if n == 0:
        return baseline_rate, 0
    h2h_mean = statistics.fmean(r[f"period_{period}_goals"] for r in h2h)
    shrink = n / (n + shrinkage_games)
    return baseline_rate + shrink * (h2h_mean - baseline_rate), n
