"""
Simple, interpretable projected-starter probability model (Part 8: no
neural nets/random forests/gradient boosting/ensembles). A multinomial
logit ("softmax regression") over a small, shared, hand-chosen feature
set -- the standard simple choice for "probability over N candidates
that must sum to 1" (Part 7), generalizing cleanly to however many
goalies a team has actually used recently (2 in the normal case, 3+ if
an emergency call-up appears) rather than a hardcoded binary choice.

FEATURES (5, shared weights -- no per-goalie fixed effects, so the model
reflects ROTATION LOGIC, not "which named goalie is good"):
  1. started_previous_game       (0/1)
  2. consecutive_start_count     (capped at MAX_STREAK_FEATURE, only
                                   nonzero for the current streak-holder
                                   -- same capping spirit as the earlier
                                   MOV-Elo experiment, to stop one long
                                   iron-man stretch from dominating)
  3. recent_start_share_10       (0 if <10 games of history)
  4. season_start_share          (0 if no season games yet)
  5. back_to_back_after_playing_previous_night   (0/1 -- expected to
                                   pick up a NEGATIVE weight, testing
                                   Part 4's hypothesis directly rather
                                   than assuming it)

P(goalie_i | candidates) = softmax(w . f_i) over the eligible candidate
pool for that team-game (features.eligible_goalies()).
"""
from __future__ import annotations

import math

from research.goalie_intelligence import features as gf

FEATURE_NAMES = [
    "started_previous_game",
    "consecutive_start_count",
    "recent_start_share_10",
    "season_start_share",
    "back_to_back_after_playing_previous_night",
]
MAX_STREAK_FEATURE = 6.0


def build_feature_vector(history: list[dict], goalie_id: str, season: int,
                          prediction_game_date: str, is_back_to_back: bool) -> list[float]:
    prev_starter = gf.previous_game_starter(history)
    started_prev = 1.0 if prev_starter == goalie_id else 0.0

    streak = 0.0
    if started_prev:
        streak = min(float(gf.consecutive_start_count(history)), MAX_STREAK_FEATURE)

    share10 = gf.recent_start_share(history, goalie_id, window=10) or 0.0
    season_share = gf.season_start_share(history, goalie_id, season) or 0.0

    b2b_played_prev = 0.0
    if is_back_to_back and gf.goalie_played_previous_night(history, goalie_id, prediction_game_date):
        b2b_played_prev = 1.0

    return [started_prev, streak, share10, season_share, b2b_played_prev]


def softmax(scores: list[float]) -> list[float]:
    m = max(scores)
    exps = [math.exp(s - m) for s in scores]
    total = sum(exps)
    return [e / total for e in exps]


def score_candidates(weights: list[float], feature_vectors: list[list[float]]) -> list[float]:
    scores = [sum(w * f for w, f in zip(weights, fv)) for fv in feature_vectors]
    return softmax(scores)


def fit_weights(training_examples: list[dict], lr: float = 0.3, n_iter: int = 2000) -> list[float]:
    """training_examples: [{"feature_vectors": [[...], ...], "target_index": int}, ...]
    -- target_index is the position of the ACTUAL starter within that
    example's candidate list. Plain batch gradient descent minimizing
    multinomial cross-entropy -- same style/transparency as
    research/xg_model_comparison.py::fit_logistic_weights, generalized
    from binary to multinomial."""
    k = len(FEATURE_NAMES)
    weights = [0.0] * k
    n = len(training_examples)
    for _ in range(n_iter):
        grad = [0.0] * k
        for ex in training_examples:
            probs = score_candidates(weights, ex["feature_vectors"])
            for i, fv in enumerate(ex["feature_vectors"]):
                y = 1.0 if i == ex["target_index"] else 0.0
                err = probs[i] - y
                for j in range(k):
                    grad[j] += err * fv[j]
        for j in range(k):
            weights[j] -= lr * grad[j] / n
    return weights


# --------------------------------------------------------------------------
# Naive baselines (Part 17) -- each returns a single top-1 pick (goalie_id)
# among the eligible candidates, or None if no defensible pick exists.
# --------------------------------------------------------------------------

def baseline_season_leader(history: list[dict], candidates: list[str], season: int) -> str | None:
    if not candidates:
        return None
    return max(candidates, key=lambda g: gf.season_start_share(history, g, season) or 0.0)


def baseline_last_game_starter(history: list[dict], candidates: list[str]) -> str | None:
    prev = gf.previous_game_starter(history)
    return prev if prev in candidates else None


def baseline_recent_leader(history: list[dict], candidates: list[str], window: int = 10) -> str | None:
    if not candidates:
        return None
    def key(g):
        share = gf.recent_start_share(history, g, window)
        return share if share is not None else -1.0
    best = max(candidates, key=key)
    if gf.recent_start_share(history, best, window) is None:
        return None
    return best


def baseline_b2b_aware(history: list[dict], candidates: list[str], season: int,
                        is_back_to_back: bool, prediction_game_date: str) -> str | None:
    if not is_back_to_back:
        return baseline_recent_leader(history, candidates) or baseline_season_leader(history, candidates, season)
    rested = [g for g in candidates if not gf.goalie_played_previous_night(history, g, prediction_game_date)]
    pool = rested if rested else candidates
    return baseline_recent_leader(history, pool) or baseline_season_leader(history, pool, season)
