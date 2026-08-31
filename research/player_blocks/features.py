"""
PIT-safe player-level BLOCKED SHOTS features, from
research/player_blocks/player_game_blocks.jsonl (see build_blocks_corpus.py).

REUSE, NOT DUPLICATION, of the genuinely prop-agnostic parts of
research/player_sog/features.py (Section F's shared player-prop
framework, applied concretely): PlayerHistoryIndex, player_history_as_of,
rolling_mean, season_to_date_mean, projected_active, and the date
helpers are pure functions over generic {player_id, game_date, ...} rows
with zero SOG-specific field names inside them -- verified by reading
that module -- so they are imported directly here rather than copy-
pasted. research/player_sog/count_models.py (Poisson/NegBin math,
Poisson-GLM fit, confidence scoring, conservative-probability bound) is
reused the same way from research/run_player_blocks_model.py. The
VALIDATED SOG model itself is never imported, modified, or depended on
for its FITTED WEIGHTS -- only these shared, prop-agnostic utility
functions.

Only genuinely BLOCKS-SPECIFIC logic is new here: PK-situation rolling
means (mirrors the SOG corpus's "pp" block, different field), the
opponent shot-attempts-environment aggregation (a different field than
SOG's opponent-SOG-allowed), and H2H shrinkage over the blocks label.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from pathlib import Path

from research.player_sog.features import (  # genuinely prop-agnostic — reused, not duplicated
    PlayerHistoryIndex, ELIGIBILITY_WINDOW_TEAM_GAMES, ELIGIBILITY_MIN_APPEARANCES,
    parse_date, played_previous_night, games_in_previous_n_days,
    player_history_as_of, projected_active, rolling_mean, season_to_date_mean,
)

CORPUS_PATH = Path(__file__).resolve().parent / "player_game_blocks.jsonl"
H2H_SHRINKAGE_GAMES = 10


def load_blocks_corpus(path: str | Path = CORPUS_PATH) -> list[dict]:
    import json
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    rows.sort(key=lambda r: (r["game_date"], r["game_id"], r["player_id"]))
    return rows


def rolling_pk_mean(history: list[dict], field: str, window: int | None) -> float | None:
    recent = history if window is None else history[-window:]
    if not recent:
        return None
    values = [(r["pk"][field] if r["pk"] is not None else 0.0) for r in recent]
    return statistics.fmean(values)


def build_team_game_shot_attempt_totals(all_rows: list[dict]) -> dict[tuple[str, int], dict]:
    """Team-game totals of shot attempts AGAINST this team while these
    skaters were on ice (OnIce_A_shotAttempts, summed) -- used to derive
    "how many shot attempts did the OPPONENT generate," i.e. this team's
    block-opportunity environment (more attempts against = more chances
    to block). Same aggregation PATTERN as
    research.player_sog.features.build_team_game_totals, but a different
    field, so written directly rather than parameterizing that function
    (which would mean touching the validated SOG module for a one-field
    change with no other caller)."""
    totals: dict[tuple[str, int], dict] = {}
    for r in all_rows:
        key = (r["team"], r["game_id"])
        if key not in totals:
            totals[key] = {"game_date": r["game_date"], "season": r["season"], "opponent": r["opponent"],
                            "shot_attempts_against_for_team": 0.0}
        totals[key]["shot_attempts_against_for_team"] += r["shot_attempts_against_on_ice"]
    return totals


def build_opponent_shot_attempt_environment(team_game_totals: dict[tuple[str, int], dict]) -> dict[str, list[dict]]:
    """{team: [{game_date, game_id, season, opponent_shot_attempts_generated}, ...]}
    -- team T's row for a game is the OPPOSING team's own shot-attempts-
    against-them total, i.e. what the opponent actually generated that
    game (T's block-opportunity environment)."""
    by_team_game = dict(team_game_totals)
    out: dict[str, list[dict]] = defaultdict(list)
    for (team, game_id), row in team_game_totals.items():
        opp = row["opponent"]
        # the opponent's OWN "shot_attempts_against_for_team" total is
        # what THIS team (as the opponent's opponent) actually generated
        opp_row = by_team_game.get((opp, game_id))
        if opp_row is None:
            continue
        out[team].append({"game_date": row["game_date"], "game_id": game_id, "season": row["season"],
                           "opponent_shot_attempts_generated": opp_row["shot_attempts_against_for_team"]})
    for team in out:
        out[team].sort(key=lambda r: (r["game_date"], r["game_id"]))
    return out


def opponent_environment_history_as_of(opponent_env: dict[str, list[dict]], team: str,
                                        prediction_game_date: str) -> list[dict]:
    rows = opponent_env.get(team, [])
    return [r for r in rows if r["game_date"] < prediction_game_date]


def rolling_opponent_shot_attempts(opponent_env: dict[str, list[dict]], team: str,
                                    prediction_game_date: str, window: int) -> float | None:
    hist = opponent_environment_history_as_of(opponent_env, team, prediction_game_date)
    recent = hist[-window:]
    if not recent:
        return None
    return statistics.fmean(r["opponent_shot_attempts_generated"] for r in recent)


def h2h_history(history: list[dict], opponent: str) -> list[dict]:
    return [r for r in history if r["opponent"] == opponent]


def h2h_shrunk_blocks_rate(history: list[dict], opponent: str, baseline_rate: float) -> tuple[float, int]:
    h2h = h2h_history(history, opponent)
    n = len(h2h)
    if n == 0:
        return baseline_rate, 0
    h2h_mean = statistics.fmean(r["blocks"] for r in h2h)
    shrink = n / (n + H2H_SHRINKAGE_GAMES)
    return baseline_rate + shrink * (h2h_mean - baseline_rate), n
