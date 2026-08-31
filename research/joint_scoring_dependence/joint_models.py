"""
Parts 6-25: joint probability models for the scoring/contribution chain.

Two structurally different kinds of "joint probability" are handled
explicitly, never conflated (Part 6/22's own instruction):

1. EXACT LOGICAL IDENTITIES (Goal>=1 -> Point>=1, Assist>=1 -> Point>=1,
   Goal>=1 -> SOG>=1): the correct joint probability is the SUBSET
   event's own marginal, exactly. No statistical model is fit for these
   -- `logical_control_probability()` returns the exact answer, and
   naive independence is computed alongside ONLY to show how wrong it
   would be (Part 22), never presented as a real contender.

2. GENUINE STATISTICAL/STRUCTURAL DEPENDENCE (SOG+Goal at a nontrivial
   SOG threshold, SOG+Point, SOG+Assist): modeled via a shrunk player-
   level conversion-rate Binomial mixture over the frozen SOG marginal's
   own Poisson mean -- the same Poisson-mixture-plus-Binomial-conversion
   architecture research/joint_shot_workload/joint_models.py established
   and validated, reimplemented here (not imported) per this project's
   per-package convention. Reported probabilities are ALWAYS clipped to
   the Frechet bounds of the FROZEN marginals actually used (Part 11/23)
   -- the coherence fix from the prior slice is applied here from the
   start, not discovered as a bug a second time.
"""
from __future__ import annotations

import math
import random
import statistics

from research.player_sog import count_models as cm

MAX_SOG = 25  # a real player rarely exceeds ~15-18 SOG/game; 25 is a safe truncation


def binomial_pmf(k: int, n: int, p: float) -> float:
    if k < 0 or k > n:
        return 0.0
    if p <= 0.0:
        return 1.0 if k == 0 else 0.0
    if p >= 1.0:
        return 1.0 if k == n else 0.0
    return math.comb(n, k) * (p ** k) * ((1 - p) ** (n - k))


def binomial_sf_at_least(k: int, n: int, p: float) -> float:
    """P(X >= k) for X ~ Binomial(n, p), via the iterative PMF recursion
    (same optimization as research/joint_shot_workload/joint_models.py --
    avoids k independent math.comb(n, i) big-integer computations in
    what is a hot loop)."""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    q = 1.0 - p
    ratio = p / q
    pmf = q ** n
    cdf = pmf
    for i in range(k - 1):
        pmf *= (n - i) / (i + 1) * ratio
        cdf += pmf
    return max(0.0, 1.0 - cdf)


def _poisson_pmf_table(mu: float, max_n: int = MAX_SOG) -> list[float]:
    return [cm.poisson_pmf(n, mu) for n in range(max_n + 1)]


# ============================================================================
# Part 9/10: shrunk conversion-rate models -- goal-per-shot, point-per-shot,
# assist-per-shot. Pooled sum/sum ratios (not mean-of-per-game-ratios),
# shrunk toward the TUNING league-wide rate by the player's own shot volume
# -- mirrors research/player_goals/features.py::career_shooting_pct_shrunk's
# own shrink-by-shot-volume discipline (reimplemented, not imported).
# ============================================================================

class ConversionRates:
    def __init__(self, tuning_rows: list[dict], numerator_field: str, k_shots: int = 150):
        self.numerator_field = numerator_field
        self.k_shots = k_shots
        total_num = sum(r[numerator_field] for r in tuning_rows)
        total_sog = sum(r["actual_sog"] for r in tuning_rows)
        self.league_rate = (total_num / total_sog) if total_sog > 0 else 0.09

    def shrunk_rate(self, history: list[dict]) -> float:
        total_sog = sum(r["actual_sog"] for r in history)
        if total_sog <= 0:
            return self.league_rate
        total_num = sum(r[self.numerator_field] for r in history)
        raw_rate = total_num / total_sog
        w = total_sog / (total_sog + self.k_shots)
        return self.league_rate + w * (raw_rate - self.league_rate)


# ============================================================================
# Part 9/16: structural conditional joint probability -- P(Event>=1 |
# SOG=n) ~ Binomial(n, shrunk_conversion_rate), mixed over the frozen
# SOG marginal's own Poisson(mu_sog).
# ============================================================================

def structural_marginal_event_sf(mu_sog: float, rate: float, x_event: int, max_n: int = MAX_SOG) -> float:
    table = _poisson_pmf_table(mu_sog, max_n)
    return sum(table[n] * binomial_sf_at_least(x_event, n, rate) for n in range(0, max_n + 1))


def structural_joint_sog_event(mu_sog: float, rate: float, x_sog: int, x_event: int,
                                max_n: int = MAX_SOG) -> float:
    table = _poisson_pmf_table(mu_sog, max_n)
    return sum(table[n] * binomial_sf_at_least(x_event, n, rate) for n in range(x_sog, max_n + 1))


# ============================================================================
# Part 22/23: logical control + Frechet bounds.
# ============================================================================

def logical_control_probability(p_subset_event: float) -> float:
    """The EXACT correct joint probability when one event logically
    implies the other -- P(A subset of B) = P(A). No fitting."""
    return p_subset_event


def frechet_bounds(p_a: float, p_b: float) -> tuple[float, float]:
    lower = max(0.0, p_a + p_b - 1.0)
    upper = min(p_a, p_b)
    return lower, upper


def clip_to_frechet(joint_p: float, p_a: float, p_b: float) -> float:
    lo, hi = frechet_bounds(p_a, p_b)
    return min(max(joint_p, lo), hi)


# ============================================================================
# Part 20: shrunk empirical joint + conditional empirical baselines --
# TUNING league-wide, same disclosed scope simplification as the prior
# joint slice (a real per-player joint co-occurrence sample is too thin).
# ============================================================================

def league_empirical_joint_rate(tuning_rows: list[dict], field_a: str, x_a: int,
                                 field_b: str, x_b: int) -> tuple[float, int]:
    n = len(tuning_rows)
    hits = sum(1 for r in tuning_rows if r[field_a] >= x_a and r[field_b] >= x_b)
    return (hits / n if n else 0.0), n


def shrunk_empirical_joint(empirical_rate: float, n_support: int, naive_independent: float,
                            k_shrink: int = 2000) -> float:
    w = n_support / (n_support + k_shrink)
    return naive_independent + w * (empirical_rate - naive_independent)


def league_conditional_rate(tuning_rows: list[dict], field_a: str, x_a: int,
                             field_b: str, x_b: int) -> tuple[float, int]:
    b_rows = [r for r in tuning_rows if r[field_b] >= x_b]
    n = len(b_rows)
    if n == 0:
        return 0.0, 0
    hits = sum(1 for r in b_rows if r[field_a] >= x_a)
    return hits / n, n


# ============================================================================
# Part 20/21: Gaussian copula benchmark -- deterministic quadrature (no
# Monte Carlo noise), same implementation as the prior joint slice,
# reimplemented per-package.
# ============================================================================

def _norm_ppf(p: float) -> float:
    if p <= 0.0:
        return -8.0
    if p >= 1.0:
        return 8.0
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00]
    p_low, p_high = 0.02425, 1 - 0.02425
    if p < p_low:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p <= p_high:
        q = p - 0.5
        r = q * q
        return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
               (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
    q = math.sqrt(-2 * math.log(1 - p))
    return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
             ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)


def _std_normal_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _bvn_density_at_t(h: float, k: float, t: float) -> float:
    denom = 1 - t * t
    if denom <= 1e-12:
        denom = 1e-12
    return (1.0 / (2 * math.pi * math.sqrt(denom))) * math.exp(-(h * h - 2 * t * h * k + k * k) / (2 * denom))


def _bvn_cdf(h: float, k: float, rho: float, n_quad: int = 48) -> float:
    base = _std_normal_cdf(h) * _std_normal_cdf(k)
    if rho == 0.0:
        return base
    sign = 1.0 if rho > 0 else -1.0
    lo, hi = (0.0, rho) if rho > 0 else (rho, 0.0)
    step = (hi - lo) / n_quad
    total = _bvn_density_at_t(h, k, lo) + _bvn_density_at_t(h, k, hi)
    for i in range(1, n_quad):
        t = lo + i * step
        weight = 4.0 if i % 2 == 1 else 2.0
        total += weight * _bvn_density_at_t(h, k, t)
    integral = sign * (step / 3.0) * total
    return base + integral


def fit_gaussian_copula_rho(residuals_a: list[float], residuals_b: list[float]) -> float:
    n = len(residuals_a)
    if n < 2:
        return 0.0
    mu_a, mu_b = statistics.fmean(residuals_a), statistics.fmean(residuals_b)
    cov = sum((a - mu_a) * (b - mu_b) for a, b in zip(residuals_a, residuals_b))
    sa = math.sqrt(sum((a - mu_a) ** 2 for a in residuals_a))
    sb = math.sqrt(sum((b - mu_b) ** 2 for b in residuals_b))
    return cov / (sa * sb) if sa > 0 and sb > 0 else 0.0


def gaussian_copula_joint_upper_tail(p_a: float, p_b: float, rho: float) -> float:
    """A valid copula's implied joint probability satisfies Frechet
    bounds automatically as a mathematical property -- the explicit
    clip below is a defensive guard against numerical-quadrature
    approximation error only (Part 23's "every reported pair joint
    probability must satisfy" applies to whichever candidate is
    actually reported, not only the structural one)."""
    if rho >= 0.999:
        rho = 0.999
    if rho <= -0.999:
        rho = -0.999
    h = _norm_ppf(1 - p_a)
    k = _norm_ppf(1 - p_b)
    raw = max(0.0, min(1.0, 1 - _std_normal_cdf(h) - _std_normal_cdf(k) + _bvn_cdf(h, k, rho)))
    return clip_to_frechet(raw, p_a, p_b)


# ============================================================================
# Part 31/47: logical implication map + redundant-leg detection -- reuses
# research/joint_scoring_dependence/logical_implication_registry.py
# directly (the SAME package, not a sibling -- no reimplementation needed
# or wanted here, since this module IS one of that registry's intended
# consumers, Part 47's own stated purpose).
# ============================================================================

from research.joint_scoring_dependence.logical_implication_registry import (  # noqa: E402
    IMPLICATION_GRAPH as LOGICAL_IMPLICATIONS,
    detect_redundant_leg,
    implies,
)


# ============================================================================
# Part 25/28/29: literal generative Monte Carlo sampler from the SAME
# structural conditional model candidate D scores exactly (Poisson SOG,
# Binomial goal/assist conversion) -- used for marginal-recovery
# verification, not as a scored headline candidate. Hard invariants
# (Part 29) hold by construction: Goals <= SOG (a Binomial(n, p) draw can
# never exceed n); no separate "Points" draw is sampled -- Points is
# always exactly Goals + Assists (Part 29's own explicit requirement),
# never a third independent quantity.
# ============================================================================

def sample_scoring_outcomes(mu_sog: float, goal_rate: float, assist_rate: float,
                             n_samples: int = 5000, seed: int = 20232024) -> dict:
    rng = random.Random(seed)
    sog_samples, goal_samples, assist_samples, point_samples = [], [], [], []
    for _ in range(n_samples):
        n_sog = _sample_poisson(rng, mu_sog)
        n_goals = _sample_binomial(rng, n_sog, goal_rate)
        n_assists = _sample_binomial(rng, n_sog, assist_rate)
        sog_samples.append(n_sog)
        goal_samples.append(n_goals)
        assist_samples.append(n_assists)
        point_samples.append(n_goals + n_assists)  # Part 29: Points = Goals + Assists, always
    return {"sog": sog_samples, "goals": goal_samples, "assists": assist_samples, "points": point_samples}


def _sample_poisson(rng: "random.Random", mu: float) -> int:
    l = math.exp(-mu)
    k = 0
    p = 1.0
    while True:
        k += 1
        p *= rng.random()
        if p <= l:
            return k - 1


def _sample_binomial(rng: "random.Random", n: int, p: float) -> int:
    if n <= 0:
        return 0
    return sum(1 for _ in range(n) if rng.random() < p)
