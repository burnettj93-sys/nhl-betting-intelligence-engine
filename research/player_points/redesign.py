"""
Redesign Cycle 2, Part 5: offset-GLM machinery for "empirical baseline +
context adjustment" -- log(mu) = offset + w.x, where the offset is the
FIXED log of the player's own hierarchical (player->role->league)
empirical mean (Section H's winning baseline), and w.x is a small,
regularization-free correction learned ONLY from context features
(PP role, opponent, team, H2H). This is a genuine, minimal extension of
research.player_sog.count_models.fit_poisson_glm's plain-gradient-descent
style (same NLL, same update rule) with one added fixed-per-example
offset term -- count_models.py itself is left untouched (it is shared by
the three ALREADY-VALIDATED SOG/blocks/assists models).
"""
from __future__ import annotations

import math

EPS = 1e-6


def fit_poisson_glm_with_offset(feature_matrix: list[list[float]], observed: list[float],
                                 offsets: list[float], lr: float = 0.05, n_iter: int = 400) -> list[float]:
    n = len(observed)
    k = len(feature_matrix[0])
    weights = [0.0] * k
    for _ in range(n_iter):
        grad = [0.0] * k
        for fv, y, off in zip(feature_matrix, observed, offsets):
            z = off + sum(w * f for w, f in zip(weights, fv))
            z = min(z, 30.0)
            mu = math.exp(z)
            err = mu - y
            for j in range(k):
                grad[j] += err * fv[j]
        for j in range(k):
            weights[j] -= lr * grad[j] / n
    return weights


def predict_mu_with_offset(weights: list[float], feature_vector: list[float], offset: float) -> float:
    z = offset + sum(w * f for w, f in zip(weights, feature_vector))
    return math.exp(min(z, 30.0))


CONTEXT_FEATURE_NAMES = ["pp_role_rate", "opponent_log_factor", "team_context_log_factor", "h2h_shrunk_delta"]


def context_feature_vector(pp_rate: float | None, opponent_factor: float | None,
                            team_factor: float | None, h2h_delta: float) -> list[float]:
    pp = 0.0 if pp_rate is None else pp_rate
    opp = 0.0 if opponent_factor is None else math.log(max(opponent_factor, EPS))
    team = 0.0 if team_factor is None else math.log(max(team_factor, EPS))
    return [pp, opp, team, h2h_delta]


def masked_context_matrix(fm: list[list[float]], keep_idx: set[int]) -> list[list[float]]:
    return [[v if i in keep_idx else 0.0 for i, v in enumerate(fv)] for fv in fm]
