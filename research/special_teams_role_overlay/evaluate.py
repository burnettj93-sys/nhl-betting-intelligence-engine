"""
Evaluation utilities for the role-overlay challengers: per-threshold
Brier/log-loss/calibration/MAE (mirrors research/run_goalie_saves_model.py's
own evaluate_estimator pattern -- a small, local, per-file
brier/log_loss implementation is this project's established convention,
not a shared cross-package utility) plus game-clustered and
date-clustered bootstrap (mirrors research/run_player_sog_period_model.py's
own game_clustered_bootstrap/date_clustered_bootstrap exactly).
"""
from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict


def brier(p: float, y: float) -> float:
    return (p - y) ** 2


def log_loss(p: float, y: float, eps: float = 1e-9) -> float:
    p = min(max(p, eps), 1 - eps)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def calibration_bins(probs: list[float], outcomes: list[float]) -> list[dict]:
    bands = [(0.0, .1), (.1, .2), (.2, .3), (.3, .4), (.4, .5), (.5, .6), (.6, .7), (.7, .8), (.8, .9), (.9, 1.01)]
    out = []
    for lo, hi in bands:
        idx = [i for i, p in enumerate(probs) if lo <= p < hi]
        if not idx:
            continue
        out.append({"band": f"{lo:.0%}-{min(hi, 1.0):.0%}", "n": len(idx),
                     "mean_predicted": statistics.fmean(probs[i] for i in idx),
                     "mean_actual": statistics.fmean(outcomes[i] for i in idx)})
    return out


def evaluate_thresholds(mus: list[float], actuals: list[float], alpha: float | None,
                         thresholds: tuple[int, ...], threshold_prob_fn) -> dict:
    """`threshold_prob_fn(mu, alpha, t) -> float`. Returns per-threshold
    {brier, log_loss, actual_rate, skill} plus overall MAE/RMSE of the
    expected count itself."""
    n = len(mus)
    mae = statistics.fmean(abs(mu - y) for mu, y in zip(mus, actuals))
    rmse = (statistics.fmean((mu - y) ** 2 for mu, y in zip(mus, actuals))) ** 0.5
    per_threshold = {}
    for t in thresholds:
        probs = [threshold_prob_fn(mu, alpha, t) for mu in mus]
        outcomes = [1.0 if y >= t else 0.0 for y in actuals]
        b = statistics.fmean(brier(p, y) for p, y in zip(probs, outcomes))
        ll = statistics.fmean(log_loss(p, y) for p, y in zip(probs, outcomes))
        actual_rate = statistics.fmean(outcomes)
        naive = actual_rate * (1 - actual_rate)
        skill = None if naive <= 0 else 1.0 - b / naive
        per_threshold[t] = {"brier": b, "log_loss": ll, "actual_rate": actual_rate, "skill": skill,
                             "calibration": calibration_bins(probs, outcomes)}
    return {"n": n, "mae_count": mae, "rmse_count": rmse, "by_threshold": per_threshold}


def game_clustered_bootstrap(examples: list[dict], baseline_scores: list[float],
                              candidate_scores: list[float], n_resamples: int = 1000,
                              seed: int = 20242025) -> dict:
    by_game = defaultdict(list)
    for i, ex in enumerate(examples):
        by_game[ex["game_id"]].append(i)
    game_ids = list(by_game.keys())
    n_games = len(game_ids)
    n_total = len(examples)
    point_delta = sum(candidate_scores) / n_total - sum(baseline_scores) / n_total
    rng = random.Random(seed)
    deltas = []
    for _ in range(n_resamples):
        idx = []
        for _ in range(n_games):
            idx.extend(by_game[game_ids[rng.randrange(n_games)]])
        b = sum(baseline_scores[i] for i in idx) / len(idx)
        c = sum(candidate_scores[i] for i in idx) / len(idx)
        deltas.append(c - b)
    deltas.sort()
    lo_i, hi_i = int(0.025 * n_resamples), min(int(0.975 * n_resamples), n_resamples - 1)
    frac_improved = sum(1 for d in deltas if d < 0) / n_resamples
    return {"point_delta": point_delta, "ci_low": deltas[lo_i], "ci_high": deltas[hi_i],
            "frac_improved": frac_improved, "n_resamples": n_resamples, "n_games_resampled": n_games}


def date_clustered_bootstrap(examples: list[dict], baseline_scores: list[float],
                              candidate_scores: list[float], n_resamples: int = 500,
                              seed: int = 20242025) -> dict:
    by_date = defaultdict(list)
    for i, ex in enumerate(examples):
        by_date[ex["game_date"]].append(i)
    dates = list(by_date.keys())
    n_dates = len(dates)
    n_total = len(examples)
    point_delta = sum(candidate_scores) / n_total - sum(baseline_scores) / n_total
    rng = random.Random(seed)
    deltas = []
    for _ in range(n_resamples):
        idx = []
        for _ in range(n_dates):
            idx.extend(by_date[dates[rng.randrange(n_dates)]])
        b = sum(baseline_scores[i] for i in idx) / len(idx)
        c = sum(candidate_scores[i] for i in idx) / len(idx)
        deltas.append(c - b)
    deltas.sort()
    lo_i, hi_i = int(0.025 * n_resamples), min(int(0.975 * n_resamples), n_resamples - 1)
    frac_improved = sum(1 for d in deltas if d < 0) / n_resamples
    return {"point_delta": point_delta, "ci_low": deltas[lo_i], "ci_high": deltas[hi_i],
            "frac_improved": frac_improved, "n_resamples": n_resamples, "n_dates_resampled": n_dates}


def player_clustered_bootstrap(examples: list[dict], baseline_scores: list[float],
                                candidate_scores: list[float], n_resamples: int = 500,
                                seed: int = 20242025) -> dict:
    by_player = defaultdict(list)
    for i, ex in enumerate(examples):
        by_player[ex["player_id"]].append(i)
    players = list(by_player.keys())
    n_players = len(players)
    n_total = len(examples)
    point_delta = sum(candidate_scores) / n_total - sum(baseline_scores) / n_total
    rng = random.Random(seed)
    deltas = []
    for _ in range(n_resamples):
        idx = []
        for _ in range(n_players):
            idx.extend(by_player[players[rng.randrange(n_players)]])
        b = sum(baseline_scores[i] for i in idx) / len(idx)
        c = sum(candidate_scores[i] for i in idx) / len(idx)
        deltas.append(c - b)
    deltas.sort()
    lo_i, hi_i = int(0.025 * n_resamples), min(int(0.975 * n_resamples), n_resamples - 1)
    frac_improved = sum(1 for d in deltas if d < 0) / n_resamples
    return {"point_delta": point_delta, "ci_low": deltas[lo_i], "ci_high": deltas[hi_i],
            "frac_improved": frac_improved, "n_resamples": n_resamples, "n_players_resampled": n_players}
