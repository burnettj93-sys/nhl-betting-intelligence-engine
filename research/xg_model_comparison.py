"""
Controlled research comparison: does adding a small MoneyPuck team
xG/shot-quality feature layer improve NHL moneyline win-probability
calibration beyond the current production model, evaluated on real NHL
games?

BASELINE DEFINITION (Part 9 audit finding -- read plainly, not glossed
over): "the current production model unchanged" is required to include
Elo, home advantage, season regression, the player PPG heuristic, the
goalie save% heuristic, rest/back-to-back, and the uncertainty
framework. The real NHL corpus (research/real_nhl_results/) has NO
player, goalie, or roster data for these real games -- only
schedule/result. `models/combined_model.py::compute_probability_from_features()`
is additive: with player_quality_home=away=0, goalie_adj_home=away=0,
and rest_adj_home=away=0 (the only honest values when no real data
exists for those signals), it collapses to EXACTLY
`models/elo_model.py::EloModel.win_probability()` -- the same Elo-only
baseline already computed and validated in the prior Elo experiment
(ELO_REAL_DATA_COMPARISON_REPORT.md). This is not a scope-reduction
workaround; it is a mathematical identity of the unmodified production
formula given the data this project actually has. This module reuses
that exact, already-tested baseline trajectory
(research/elo_comparison.py::run_walkforward(games, weight_fn=None)) --
Elo's own update rule is NEVER touched by anything in this module,
exactly like production (MoneyPuck features, like player/goalie/rest
features, only ever adjust the PREDICTION, never Elo's update()).

INTEGRATION FORMULA (Part 11, option B: fit a simple logistic
coefficient using tuning data only): for feature vector z (one or more
STANDARDIZED, mean-0/std-1 differentials, home minus away), a candidate
probability is

    p_candidate = sigmoid( logit(p_baseline) + sum_j(beta_j * z_j) )

beta is fit by plain gradient descent (no external ML library -- Part
11 explicitly forbids GBM/RF/neural nets) minimizing mean log loss
against ACTUAL outcomes from research/real_nhl_results (Part 15: NEVER
MoneyPuck's own goals_for/goals_against, which excludes the
shootout-deciding goal -- see MONEYPUCK_TEAM_INGESTION_REPORT.md
Section P), using ONLY the 2023-24 tuning season's mature-feature games.
Standardization parameters (mean/std) are likewise fit on tuning data
only (Part 14 -- no future-distribution leakage into 2024-25/2025-26).

This module does not modify models/elo_model.py, models/combined_model.py,
or any production file -- it is a read-only research comparison, exactly
like research/elo_comparison.py before it.
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from research import elo_comparison as ec
from research import moneypuck_team_features as mpf
from research.moneypuck_ingestion.ingest_moneypuck_team import get_connection as get_moneypuck_conn

NHL_CORPUS_PATH = str(REPO_ROOT / "research" / "real_nhl_results" / "normalized_regular_season_games.jsonl")

WARMUP_SEASON = 20222023
TUNING_SEASON = 20232024
EVAL_SEASONS = [20242025, 20252026]

WINDOW_GRID = [10, 25]
FORM_SHORT_WINDOW = 10
FORM_LONG_WINDOW = 25

EPS = 1e-9


def logit(p: float) -> float:
    p = min(max(p, EPS), 1 - EPS)
    return math.log(p / (1 - p))


def sigmoid(z: float) -> float:
    if z >= 0:
        ez = math.exp(-z)
        return 1.0 / (1.0 + ez)
    ez = math.exp(z)
    return ez / (1.0 + ez)


def season_slice(records, seasons):
    seasons = {seasons} if isinstance(seasons, int) else set(seasons)
    return [r for r in records if r["season"] in seasons]


def fit_logistic_weights(base_logits: list[float], feature_matrix: list[list[float]],
                          actual: list[float], lr: float = 0.3, n_iter: int = 3000) -> list[float]:
    """Plain batch gradient descent, minimizing mean log loss of
    sigmoid(base_logit + weights . features) against `actual`. No
    external ML library -- k (the number of features) is always 1 or 2
    in this experiment."""
    n = len(actual)
    k = len(feature_matrix[0])
    weights = [0.0] * k
    for _ in range(n_iter):
        grad = [0.0] * k
        for i in range(n):
            z = base_logits[i] + sum(weights[j] * feature_matrix[i][j] for j in range(k))
            p = sigmoid(z)
            err = p - actual[i]
            for j in range(k):
                grad[j] += err * feature_matrix[i][j]
        for j in range(k):
            weights[j] -= lr * grad[j] / n
    return weights


def standardize_fit(values: list[float]) -> tuple[float, float]:
    mean = statistics.fmean(values)
    stdev = statistics.pstdev(values)
    if stdev == 0:
        stdev = 1.0
    return mean, stdev


def standardize_apply(value: float, mean: float, stdev: float) -> float:
    return (value - mean) / stdev


# --------------------------------------------------------------------------
# Feature extraction per game
# --------------------------------------------------------------------------

def compute_feature_for_game(conn, record: dict, feature_fn, **kwargs) -> float | None:
    """feature_fn is one of research.moneypuck_team_features's functions.
    Returns (home_value - away_value), or None if either side is
    immature (DATA_UNAVAILABLE) -- this experiment never fills in a
    missing feature with a default; the game is simply excluded from
    that candidate's mature set (Part 6/17)."""
    home_val = feature_fn(conn, record["home_team"], record["game_date"], record["season"], **kwargs)
    away_val = feature_fn(conn, record["away_team"], record["game_date"], record["season"], **kwargs)
    if home_val is None or away_val is None:
        return None
    return home_val - away_val


FEATURE_SPECS = {}
for w in WINDOW_GRID:
    FEATURE_SPECS[f"5v5_xg_share_{w}"] = dict(
        fn=mpf.rolling_xg_share, kwargs={"window": w, "situation": mpf.SITUATION_5V5})
    FEATURE_SPECS[f"all_xg_diff_{w}"] = dict(
        fn=mpf.rolling_xg_diff_per_game, kwargs={"window": w, "situation": mpf.SITUATION_ALL})
FEATURE_SPECS[f"xg_form_delta_{FORM_SHORT_WINDOW}_{FORM_LONG_WINDOW}"] = dict(
    fn=mpf.xg_form_delta, kwargs={"short_window": FORM_SHORT_WINDOW, "long_window": FORM_LONG_WINDOW,
                                   "situation": mpf.SITUATION_5V5})


def compute_all_features(conn, records: list[dict]) -> dict[str, dict[int, float]]:
    """{feature_name: {game_id: (home-away) diff or None}} for every
    record in `records`."""
    out = {name: {} for name in FEATURE_SPECS}
    for rec in records:
        for name, spec in FEATURE_SPECS.items():
            out[name][rec["game_id"]] = compute_feature_for_game(conn, rec, spec["fn"], **spec["kwargs"])
    return out
