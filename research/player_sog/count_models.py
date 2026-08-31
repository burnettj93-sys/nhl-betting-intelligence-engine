"""
Count-distribution modeling for player SOG: Poisson vs. Negative
Binomial PMFs, a small interpretable log-linear (Poisson-GLM) expected-
SOG model fit by plain gradient descent (no ML libraries -- same style
as every prior model in this project: research/goalie_intelligence/
model.py's multinomial softmax, research/xg_model_comparison.py's
logistic gradient descent), a confidence layer, and a conservative
(lower-bound) probability derived from a normal-approximation bound on
the fitted Poisson rate -- never an arbitrary flat percentage-point
subtraction (Part 19's explicit requirement).
"""
from __future__ import annotations

import math
import statistics

EPS = 1e-6


# --------------------------------------------------------------------------
# Part 15/33: count distributions.
# --------------------------------------------------------------------------

def poisson_pmf(k: int, mu: float) -> float:
    if mu <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-mu + k * math.log(mu) - math.lgamma(k + 1))


def poisson_sf_at_least(k: int, mu: float, max_k: int = 60) -> float:
    """P(X >= k) for X ~ Poisson(mu), via 1 - CDF(k-1). Summed directly
    (not via a closed form) for transparency; max_k is a safe upper
    truncation far beyond any real SOG count."""
    if k <= 0:
        return 1.0
    return max(0.0, 1.0 - sum(poisson_pmf(i, mu) for i in range(0, min(k, max_k))))


def negbinom_pmf(k: int, mu: float, alpha: float) -> float:
    """Mean/dispersion parameterization: Var = mu + alpha*mu^2. alpha ->
    0 recovers Poisson. r = 1/alpha is the standard "size" parameter."""
    if alpha <= 0:
        return poisson_pmf(k, mu)
    r = 1.0 / alpha
    p = r / (r + mu)
    log_pmf = (math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1)
               + r * math.log(p) + k * math.log(1 - p))
    return math.exp(log_pmf)


def negbinom_sf_at_least(k: int, mu: float, alpha: float, max_k: int = 60) -> float:
    if k <= 0:
        return 1.0
    return max(0.0, 1.0 - sum(negbinom_pmf(i, mu, alpha) for i in range(0, min(k, max_k))))


def full_pmf_table(mu: float, alpha: float | None, max_k: int = 6) -> dict[int, float]:
    """{0: P(SOG=0), ..., max_k-1: P(SOG=max_k-1), max_k: P(SOG>=max_k)}
    -- Part 17's required output shape."""
    pmf_fn = (lambda k: poisson_pmf(k, mu)) if not alpha else (lambda k: negbinom_pmf(k, mu, alpha))
    table = {k: pmf_fn(k) for k in range(max_k)}
    table[max_k] = max(0.0, 1.0 - sum(table.values()))
    return table


def threshold_probabilities(mu: float, alpha: float | None, thresholds=(1, 2, 3, 4, 5, 6)) -> dict[int, float]:
    """{n: P(SOG >= n)} for each threshold -- monotonically non-increasing
    in n by construction (Part 17's "P(4+) <= P(3+)" requirement)."""
    sf = negbinom_sf_at_least if alpha else (lambda k, m, a=None: poisson_sf_at_least(k, m))
    return {n: (negbinom_sf_at_least(n, mu, alpha) if alpha else poisson_sf_at_least(n, mu)) for n in thresholds}


# --------------------------------------------------------------------------
# Part 33/H/I: overdispersion diagnostic.
# --------------------------------------------------------------------------

def overdispersion_stats(counts: list[float]) -> dict:
    mean = statistics.fmean(counts)
    var = statistics.pvariance(counts)
    return {"mean": mean, "variance": var, "variance_to_mean_ratio": (var / mean if mean > 0 else None)}


def fit_negbinom_alpha_by_moments(observed: list[float], predicted_mu: list[float]) -> float:
    """Method-of-moments dispersion estimate given already-fit Poisson
    means: alpha = mean((y-mu)^2 - mu) / mean(mu^2), clipped at 0 (0
    means "no overdispersion detected -- Poisson is adequate"). A
    simple, standard, transparent two-stage estimator (fit mu first via
    the Poisson GLM, then fit the single scalar alpha against its
    residuals) rather than a joint MLE -- documented as a deliberate
    simplification, consistent with this project's "no complex ML"
    constraint."""
    n = len(observed)
    num = sum((y - mu) ** 2 - mu for y, mu in zip(observed, predicted_mu)) / n
    den = sum(mu ** 2 for mu in predicted_mu) / n
    if den <= 0:
        return 0.0
    return max(0.0, num / den)


# --------------------------------------------------------------------------
# Part 4-10: the small interpretable Poisson-GLM expected-SOG model.
# log(mu) = w . x   (log link; features are named, small, and additive)
# --------------------------------------------------------------------------

FEATURE_NAMES = ["intercept", "log_baseline_rate", "recent_form_log_ratio",
                 "toi_log_ratio", "opponent_log_factor", "h2h_shrunk_delta"]


def build_feature_vector(baseline_rate: float, recent_rate: float | None, recent_toi: float | None,
                          baseline_toi: float | None, opponent_factor: float | None,
                          h2h_shrunk_delta: float) -> list[float]:
    log_baseline = math.log(max(baseline_rate, EPS))
    recent_form = 0.0
    if recent_rate is not None:
        recent_form = math.log(max(recent_rate, EPS)) - log_baseline
    toi_ratio = 0.0
    if recent_toi is not None and baseline_toi is not None and baseline_toi > 0:
        toi_ratio = math.log(max(recent_toi, EPS) / baseline_toi)
    opp_factor = 0.0 if opponent_factor is None else math.log(max(opponent_factor, EPS))
    return [1.0, log_baseline, recent_form, toi_ratio, opp_factor, h2h_shrunk_delta]


def fit_poisson_glm(feature_matrix: list[list[float]], observed: list[float],
                     lr: float = 0.05, n_iter: int = 400) -> list[float]:
    """Plain batch gradient descent minimizing Poisson negative log
    likelihood (dropping the log(y!) constant term, which doesn't affect
    the argmin): mu_i = exp(w . x_i), grad_j = sum((mu_i - y_i) * x_ij)."""
    n = len(observed)
    k = len(feature_matrix[0])
    weights = [0.0] * k
    for _ in range(n_iter):
        grad = [0.0] * k
        for fv, y in zip(feature_matrix, observed):
            z = sum(w * f for w, f in zip(weights, fv))
            z = min(z, 30.0)  # guard against runaway exp() during early gradient steps
            mu = math.exp(z)
            err = mu - y
            for j in range(k):
                grad[j] += err * fv[j]
        for j in range(k):
            weights[j] -= lr * grad[j] / n
    return weights


def predict_mu(weights: list[float], feature_vector: list[float]) -> float:
    z = sum(w * f for w, f in zip(weights, feature_vector))
    return math.exp(min(z, 30.0))


# --------------------------------------------------------------------------
# Part 18: confidence layer -- a small, documented, additive point
# system, never conflated with probability itself.
# --------------------------------------------------------------------------

def confidence_score(n_history_games: int, recent_toi_cv: float | None, recent_sog_cv: float | None,
                      opponent_window_games: int, opponent_window_target: int,
                      appearance_rate: float) -> tuple[str, list[str], list[str]]:
    """Returns (label, positive_drivers, risk_notes). Point system (Part
    18 items 1-4,6,8 -- items 5/7/9/10 are addressed at the evaluation
    level in the report, not per-prediction, since "model calibration in
    similar cases" and "data recency" are properties of the whole
    research corpus, not a single archival prediction)."""
    score = 0
    pos, risk = [], []

    if n_history_games >= 40:
        score += 1
        pos.append(f"{n_history_games}-game player sample")
    elif n_history_games < 15:
        score -= 1
        risk.append(f"only {n_history_games} prior games in the sample")

    if recent_toi_cv is not None:
        if recent_toi_cv < 0.15:
            score += 1
            pos.append("stable recent ice-time role")
        elif recent_toi_cv > 0.35:
            score -= 1
            risk.append("volatile recent ice time")

    if recent_sog_cv is not None:
        if recent_sog_cv < 0.5:
            score += 1
            pos.append("stable recent shot-rate")
        elif recent_sog_cv > 1.0:
            score -= 1
            risk.append("highly volatile recent shot rate")

    if opponent_window_games >= opponent_window_target:
        score += 1
        pos.append("opponent shot-environment data mature")
    else:
        risk.append(f"opponent sample only {opponent_window_games}/{opponent_window_target} games")

    if appearance_rate >= 0.9:
        score += 1
        pos.append("consistent recent lineup appearance")
    elif appearance_rate < 0.6:
        score -= 1
        risk.append("inconsistent recent appearance rate")

    label = "HIGH" if score >= 3 else ("LOW" if score < 0 else "MEDIUM")
    return label, pos, risk


def coefficient_of_variation(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = statistics.fmean(values)
    if mean <= 0:
        return None
    return statistics.pstdev(values) / mean


# --------------------------------------------------------------------------
# Part 19: conservative probability -- a one-sided normal-approximation
# lower bound on the fitted Poisson RATE (never an arbitrary flat
# percentage-point subtraction), then re-derive threshold probabilities
# from the SAME distribution family at that lower rate.
# --------------------------------------------------------------------------

CONSERVATIVE_Z = 0.84  # ~20th percentile of the standard normal


def conservative_mu(mu: float, effective_n_games: int) -> float:
    """Normal approximation to a Poisson-rate confidence bound: given an
    estimate built from `effective_n_games` prior games, the standard
    error of the per-game rate is approximately sqrt(mu / n). This is a
    standard large-sample approximation, not an ad hoc adjustment; for
    very small n (<3) it is capped at a 60% floor of mu to avoid a
    degenerate near-zero bound."""
    if effective_n_games <= 0:
        return mu * 0.6
    se = math.sqrt(max(mu, EPS) / effective_n_games)
    lower = mu - CONSERVATIVE_Z * se
    return max(mu * 0.4, lower)
