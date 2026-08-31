"""
PIT-safe period-level SOG features, built from
research/player_sog_period/player_game_period_sog.jsonl. Mirrors
research/player_sog/features.py's discipline exactly (same STRICT
PRIOR-GAME-DATE gate, same shrinkage-by-game-count convention) but
computed on period-level counts -- rebuilt here rather than imported,
matching this project's per-package convention.

TARGET-GAME PERIOD SOG NEVER APPEARS ON THE FEATURE SIDE (Part 4): every
function takes an explicit `prediction_game_date` and only reads rows with
`game_date < prediction_game_date`.
"""
from __future__ import annotations

import bisect
import json
import statistics
from collections import defaultdict
from pathlib import Path

CORPUS_PATH = Path(__file__).resolve().parent / "player_game_period_sog.jsonl"

H2H_SHRINKAGE_GAMES = 10
PERIODS = (1, 2, 3)


def load_period_corpus(path: str | Path = CORPUS_PATH) -> list[dict]:
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    rows.sort(key=lambda r: (r["game_date"], r["game_id"], r["player_id"]))
    return rows


class PeriodHistoryIndex:
    def __init__(self, rows: list[dict]):
        by_player = defaultdict(list)
        for r in rows:
            by_player[r["player_id"]].append(r)
        self._by_player: dict[str, tuple[list[str], list[dict]]] = {}
        for player_id, prows in by_player.items():
            ordered = sorted(prows, key=lambda r: (r["game_date"], r["game_id"]))
            self._by_player[player_id] = ([r["game_date"] for r in ordered], ordered)

    def history_as_of(self, player_id: str, prediction_game_date: str) -> list[dict]:
        entry = self._by_player.get(player_id)
        if entry is None:
            return []
        dates, ordered = entry
        cut = bisect.bisect_left(dates, prediction_game_date)
        return ordered[:cut]


def period_history_as_of(all_rows: list[dict], player_id: str, prediction_game_date: str) -> list[dict]:
    return [r for r in all_rows if r["player_id"] == player_id and r["game_date"] < prediction_game_date]


def rolling_period_mean(history: list[dict], period: int, window: int | None) -> float | None:
    recent = history if window is None else history[-window:]
    if not recent:
        return None
    return statistics.fmean(r[f"period_{period}_sog"] for r in recent)


def rolling_period_mean_recent(history: list[dict], period: int, window: int) -> float | None:
    return rolling_period_mean(history, period, window)


# --------------------------------------------------------------------------
# Part 10/11: team- and opponent-period shot environment, aggregated
# PIT-safely from the SAME period player-game corpus (every skater on a
# team, summed per game per period) -- no separate ingestion needed.
# --------------------------------------------------------------------------

def build_team_game_period_totals(all_rows: list[dict]) -> dict[tuple[str, int], dict]:
    totals: dict[tuple[str, int], dict] = {}
    for r in all_rows:
        key = (r["team"], r["game_id"])
        if key not in totals:
            totals[key] = {"game_date": r["game_date"], "season": r["season"], "opponent": r["opponent"],
                            "period_1_sog": 0, "period_2_sog": 0, "period_3_sog": 0}
        for k in PERIODS:
            totals[key][f"period_{k}_sog"] += r[f"period_{k}_sog"]
    return totals


def build_opponent_period_allowed_history(team_game_period_totals: dict[tuple[str, int], dict]
                                           ) -> dict[str, list[dict]]:
    by_team_game = dict(team_game_period_totals)
    allowed: dict[str, list[dict]] = defaultdict(list)
    for (team, game_id), row in team_game_period_totals.items():
        opp = row["opponent"]
        opp_offense = by_team_game.get((opp, game_id))
        if opp_offense is None:
            continue
        allowed[team].append({
            "game_date": row["game_date"], "game_id": game_id, "season": row["season"],
            "period_1_allowed": opp_offense["period_1_sog"], "period_2_allowed": opp_offense["period_2_sog"],
            "period_3_allowed": opp_offense["period_3_sog"],
        })
    for team in allowed:
        allowed[team].sort(key=lambda r: (r["game_date"], r["game_id"]))
    return allowed


def opponent_period_history_as_of(opponent_allowed_history: dict[str, list[dict]], team: str,
                                   prediction_game_date: str) -> list[dict]:
    rows = opponent_allowed_history.get(team, [])
    return [r for r in rows if r["game_date"] < prediction_game_date]


def rolling_opponent_period_allowed(opponent_allowed_history: dict[str, list[dict]], team: str,
                                     prediction_game_date: str, period: int, window: int) -> float | None:
    hist = opponent_period_history_as_of(opponent_allowed_history, team, prediction_game_date)
    recent = hist[-window:]
    if not recent:
        return None
    return statistics.fmean(r[f"period_{period}_allowed"] for r in recent)


def build_team_period_history(team_game_period_totals: dict[tuple[str, int], dict]) -> dict[str, list[dict]]:
    """Part 10: team's OWN period shot-generation tendency (distinct from
    opponent-ALLOWED above)."""
    by_team: dict[str, list[dict]] = defaultdict(list)
    for (team, game_id), row in team_game_period_totals.items():
        by_team[team].append({
            "game_date": row["game_date"], "game_id": game_id,
            "period_1_for": row["period_1_sog"], "period_2_for": row["period_2_sog"],
            "period_3_for": row["period_3_sog"],
        })
    for team in by_team:
        by_team[team].sort(key=lambda r: (r["game_date"], r["game_id"]))
    return by_team


def rolling_team_period_rate(team_history: dict[str, list[dict]], team: str, prediction_game_date: str,
                              period: int, window: int) -> float | None:
    hist = [r for r in team_history.get(team, []) if r["game_date"] < prediction_game_date]
    recent = hist[-window:]
    if not recent:
        return None
    return statistics.fmean(r[f"period_{period}_for"] for r in recent)


# --------------------------------------------------------------------------
# Part 17: period-specific H2H, aggressively shrunk (sparser than
# full-game H2H by construction -- same game count, 1/3 the events).
# --------------------------------------------------------------------------

def h2h_period_shrunk_rate(history: list[dict], opponent: str, period: int, baseline_rate: float,
                            shrinkage_games: int = H2H_SHRINKAGE_GAMES) -> tuple[float, int]:
    h2h = [r for r in history if r["opponent"] == opponent]
    n = len(h2h)
    if n == 0:
        return baseline_rate, 0
    h2h_mean = statistics.fmean(r[f"period_{period}_sog"] for r in h2h)
    shrink = n / (n + shrinkage_games)
    return baseline_rate + shrink * (h2h_mean - baseline_rate), n
