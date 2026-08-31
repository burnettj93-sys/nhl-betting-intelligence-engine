"""
Parts 7-22: joint probability candidate models. Candidate family kept
small and interpretable, per the explicit instruction:

A. NAIVE_INDEPENDENCE       -- product of the two/three frozen marginals
B. SHRUNK_EMPIRICAL_JOINT   -- TUNING-season league-wide empirical
                               co-occurrence rate for the exact threshold
                               combination, shrunk toward naive
                               independence by sample count (Part 18).
                               Deliberately LEAGUE-WIDE, not per-player/
                               per-team: a real per-entity joint
                               co-occurrence sample (e.g. "this specific
                               player's 3+ SOG together with a 30+ team
                               game") is far too thin at any single
                               entity to support entity-specific
                               shrinkage -- a disclosed scope choice, not
                               a silent simplification.
C. CONDITIONAL_EMPIRICAL    -- TUNING-season empirical P(A|B), combined
                               with the row's own real frozen marginal
                               P(B) (Part 19).
D. STRUCTURAL_FACTORIZATION -- P(Team SOG) x P(Player SOG | Team SOG) x
                               P(Goalie Saves | Team SOG, goalie context),
                               following the real hockey accounting
                               identity (Parts 8/10/11/16), computed by
                               EXACT numerical enumeration over Team SOG
                               (a Poisson mixture), not sampling -- more
                               precise than Monte Carlo and fully
                               reproducible.
E. GAUSSIAN_COPULA          -- benchmark only (Part 21), correlation
                               parameter fit on TUNING development data,
                               joint CDF evaluated via a small deterministic
                               Monte Carlo integral (bivariate normal CDF
                               has no simple closed form).
F. MONTE_CARLO_SAMPLER      -- literal random sampling from D's own
                               generative structure (Part 22), used for
                               marginal-recovery verification (Part 47)
                               and representative examples, not as a
                               separately-scored headline candidate.

Player SOG is modeled as a BINOMIAL split of Team SOG (Part 8's own
suggested family, tested rather than assumed): Player SOG | Team SOG=n
~ Binomial(n, player_share). Goalie Saves is modeled via the real
accounting identity: Team SOG minus a TUNING-fit empty-net-count
distribution gives shots faced; Saves | shots_faced ~ Binomial(shots_faced,
league_save_pct) -- LEAGUE-average save%, never goalie-specific (Part 11's
explicit instruction: "do not assume save% talent adds value -- previous
Goalie Saves research found it did not").
"""
from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict

from research.player_sog import count_models as cm

MAX_TEAM_SOG = 80


def binomial_pmf(k: int, n: int, p: float) -> float:
    if k < 0 or k > n:
        return 0.0
    if p <= 0.0:
        return 1.0 if k == 0 else 0.0
    if p >= 1.0:
        return 1.0 if k == n else 0.0
    return math.comb(n, k) * (p ** k) * ((1 - p) ** (n - k))


def binomial_sf_at_least(k: int, n: int, p: float) -> float:
    """P(X >= k) for X ~ Binomial(n, p) = 1 - P(X < k). P(X<k) is
    accumulated via the standard iterative PMF recursion pmf(i+1) =
    pmf(i) * (n-i)/(i+1) * p/(1-p) rather than k independent
    math.comb(n, i) big-integer computations -- this function sits in a
    hot loop (called O(max_team_sog x empty_net_states) times per joint
    probability), and the recursion is materially faster at this scale
    (n up to ~80, k up to ~25) while being numerically identical."""
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


# ============================================================================
# Part 9/8: player share of Team SOG -- PIT-safe, shrunk.
# ============================================================================

class PlayerShareRates:
    def __init__(self, tuning_joint_rows: list[dict]):
        total_player = sum(r["actual_player_sog"] for r in tuning_joint_rows)
        total_team = sum(r["actual_team_sog"] for r in tuning_joint_rows)
        self.league_avg_share = (total_player / total_team) if total_team > 0 else 0.06

    def shrunk_share(self, player_history: list[dict], k_games: int = 20) -> float:
        n = len(player_history)
        if n == 0:
            return self.league_avg_share
        total_player = sum(r["actual_player_sog"] for r in player_history)
        total_team = sum(r["actual_team_sog"] for r in player_history)
        if total_team <= 0:
            return self.league_avg_share
        raw_share = total_player / total_team
        w = n / (n + k_games)
        return self.league_avg_share + w * (raw_share - self.league_avg_share)


# ============================================================================
# Part 10-12: Team SOG -> Goalie Saves structural accounting.
# ============================================================================

class StructuralParams:
    def __init__(self, tuning_joint_rows: list[dict], tuning_league_save_pct: float):
        counts = defaultdict(int)
        for r in tuning_joint_rows:
            e = min(r["empty_net_sog_count"], 3)
            counts[e] += 1
        total = sum(counts.values())
        self.empty_net_dist = {e: c / total for e, c in counts.items()} if total else {0: 1.0}
        self.league_save_pct = tuning_league_save_pct


def saves_sf_given_team_sog(n: int, y: int, params: StructuralParams) -> float:
    """P(Saves >= y | Team SOG = n), marginalizing the empty-net-count
    distribution (Part 14) and applying the league-average save%
    conversion (Part 11/12)."""
    total = 0.0
    for e, p_e in params.empty_net_dist.items():
        shots_faced = max(n - e, 0)
        total += p_e * binomial_sf_at_least(y, shots_faced, params.league_save_pct)
    return total


# ============================================================================
# Part 16: structural conditional factorization -- exact numerical
# enumeration over Team SOG (a Poisson mixture).
# ============================================================================

def _poisson_pmf_table(mu: float, max_n: int = MAX_TEAM_SOG) -> list[float]:
    return [cm.poisson_pmf(n, mu) for n in range(max_n + 1)]


def structural_joint_player_team(mu_team: float, player_share: float, x_player: int, y_team: int,
                                  max_n: int = MAX_TEAM_SOG) -> float:
    table = _poisson_pmf_table(mu_team, max_n)
    return sum(table[n] * binomial_sf_at_least(x_player, n, player_share)
               for n in range(y_team, max_n + 1))


def structural_joint_team_goalie(mu_team: float, params: StructuralParams, y_team: int, z_saves: int,
                                  max_n: int = MAX_TEAM_SOG) -> float:
    table = _poisson_pmf_table(mu_team, max_n)
    return sum(table[n] * saves_sf_given_team_sog(n, z_saves, params)
               for n in range(y_team, max_n + 1))


def structural_joint_player_goalie(mu_team: float, player_share: float, params: StructuralParams,
                                    x_player: int, z_saves: int, max_n: int = MAX_TEAM_SOG) -> float:
    table = _poisson_pmf_table(mu_team, max_n)
    return sum(table[n] * binomial_sf_at_least(x_player, n, player_share) * saves_sf_given_team_sog(n, z_saves, params)
               for n in range(0, max_n + 1))


def structural_joint_three_way(mu_team: float, player_share: float, params: StructuralParams,
                                x_player: int, y_team: int, z_saves: int,
                                max_n: int = MAX_TEAM_SOG) -> float:
    table = _poisson_pmf_table(mu_team, max_n)
    return sum(table[n] * binomial_sf_at_least(x_player, n, player_share) * saves_sf_given_team_sog(n, z_saves, params)
               for n in range(y_team, max_n + 1))


def structural_marginal_team_sf(mu_team: float, y_team: int, max_n: int = MAX_TEAM_SOG) -> float:
    return cm.poisson_sf_at_least(y_team, mu_team)


def structural_marginal_player_sf(mu_team: float, player_share: float, x_player: int,
                                   max_n: int = MAX_TEAM_SOG) -> float:
    table = _poisson_pmf_table(mu_team, max_n)
    return sum(table[n] * binomial_sf_at_least(x_player, n, player_share) for n in range(0, max_n + 1))


def structural_marginal_goalie_sf(mu_team: float, params: StructuralParams, z_saves: int,
                                   max_n: int = MAX_TEAM_SOG) -> float:
    table = _poisson_pmf_table(mu_team, max_n)
    return sum(table[n] * saves_sf_given_team_sog(n, z_saves, params) for n in range(0, max_n + 1))


# ============================================================================
# Part 21: Gaussian copula -- benchmark only.
# ============================================================================

def _norm_ppf(p: float) -> float:
    """Acklam's rational approximation to the standard normal inverse
    CDF -- simple, transparent, accurate to ~1e-9, no external deps."""
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


def fit_gaussian_copula_rho(residuals_a: list[float], residuals_b: list[float]) -> float:
    """Fits rho as the plain Pearson correlation of each marginal's own
    STANDARDIZED residual ((actual - mu) / sqrt(mu), the natural Poisson-
    scale standardization already used throughout this project) --
    development-data only, a simpler and equally valid parameterization
    of dependence strength than a full PIT-copula transform for a
    benchmark-only candidate (Part 21)."""
    n = len(residuals_a)
    if n < 2:
        return 0.0
    mu_a, mu_b = statistics.fmean(residuals_a), statistics.fmean(residuals_b)
    cov = sum((a - mu_a) * (b - mu_b) for a, b in zip(residuals_a, residuals_b))
    sa = math.sqrt(sum((a - mu_a) ** 2 for a in residuals_a))
    sb = math.sqrt(sum((b - mu_b) ** 2 for b in residuals_b))
    return cov / (sa * sb) if sa > 0 and sb > 0 else 0.0


def _std_normal_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _bvn_density_at_t(h: float, k: float, t: float) -> float:
    denom = 1 - t * t
    if denom <= 1e-12:
        denom = 1e-12
    return (1.0 / (2 * math.pi * math.sqrt(denom))) * math.exp(-(h * h - 2 * t * h * k + k * k) / (2 * denom))


def _bvn_cdf(h: float, k: float, rho: float, n_quad: int = 48) -> float:
    """Standard bivariate normal CDF P(X<=h, Y<=k) via the standard
    representation Phi2(h,k,rho) = Phi(h)Phi(k) + integral_0^rho
    phi2(h,k,t) dt, evaluated with Simpson's rule -- deterministic,
    no RNG, accurate to several decimal places with n_quad=48. Faster
    and more precise than drawing fresh Monte Carlo samples per query
    (a literal generative Monte Carlo sampler is used elsewhere in this
    module for Part 22/47's distinct purpose -- verification sampling,
    not CDF evaluation)."""
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


def gaussian_copula_joint_upper_tail(p_a: float, p_b: float, rho: float) -> float:
    """P(U > 1-p_a, V > 1-p_b) under a Gaussian copula with correlation
    rho: P(X>h,Y>k) = 1 - Phi(h) - Phi(k) + Phi2(h,k,rho). Deterministic
    numerical quadrature (see _bvn_cdf) -- no sampling noise, exact
    reproducibility, and fast enough for row-by-row evaluation."""
    if rho >= 0.999:
        rho = 0.999
    if rho <= -0.999:
        rho = -0.999
    h = _norm_ppf(1 - p_a)
    k = _norm_ppf(1 - p_b)
    return max(0.0, min(1.0, 1 - _std_normal_cdf(h) - _std_normal_cdf(k) + _bvn_cdf(h, k, rho)))


# ============================================================================
# Part 22: literal generative Monte Carlo sampler from the structural
# model -- distinct from the copula's own (deterministic) CDF evaluation
# above. Used for marginal-recovery verification (Part 47) and
# representative-example generation (Part 49), not as a scored headline
# candidate.
# ============================================================================

def sample_structural_joint(mu_team: float, player_share: float, params: "StructuralParams",
                             n_samples: int = 5000, seed: int = 20232024) -> dict:
    """Draws (team_sog, player_sog, goalie_saves) triples from the SAME
    generative structure candidate D scores exactly (Poisson team SOG,
    Binomial player allocation, empty-net-adjusted Binomial save
    conversion) -- Part 54's explicit scope limit: no goals timeline,
    assists, penalties, period state, OT, or shootout."""
    rng = random.Random(seed)
    empty_net_keys = list(params.empty_net_dist.keys())
    empty_net_weights = list(params.empty_net_dist.values())
    team_samples, player_samples, goalie_samples = [], [], []
    for _ in range(n_samples):
        n_team = _sample_poisson(rng, mu_team)
        n_player = _sample_binomial(rng, n_team, player_share)
        e = rng.choices(empty_net_keys, weights=empty_net_weights, k=1)[0]
        shots_faced = max(n_team - e, 0)
        n_saves = _sample_binomial(rng, shots_faced, params.league_save_pct)
        team_samples.append(n_team)
        player_samples.append(n_player)
        goalie_samples.append(n_saves)
    return {"team_sog": team_samples, "player_sog": player_samples, "goalie_saves": goalie_samples}


def _sample_poisson(rng: random.Random, mu: float) -> int:
    """Knuth's algorithm -- simple, exact, adequate for mu in this
    project's real range (team SOG ~20-40)."""
    l = math.exp(-mu)
    k = 0
    p = 1.0
    while True:
        k += 1
        p *= rng.random()
        if p <= l:
            return k - 1


def _sample_binomial(rng: random.Random, n: int, p: float) -> int:
    if n <= 0:
        return 0
    return sum(1 for _ in range(n) if rng.random() < p)


# ============================================================================
# Parts 18/19: shrunk empirical joint + conditional empirical baselines.
# ============================================================================

def league_empirical_joint_rate(tuning_joint_rows: list[dict], field_a: str, x_a: int,
                                 field_b: str, x_b: int) -> tuple[float, int]:
    n = len(tuning_joint_rows)
    hits = sum(1 for r in tuning_joint_rows if r[field_a] >= x_a and r[field_b] >= x_b)
    return (hits / n if n else 0.0), n


def shrunk_empirical_joint(empirical_rate: float, n_support: int, naive_independent: float,
                            k_shrink: int = 2000) -> float:
    w = n_support / (n_support + k_shrink)
    return naive_independent + w * (empirical_rate - naive_independent)


def league_conditional_rate(tuning_joint_rows: list[dict], field_a: str, x_a: int,
                             field_b: str, x_b: int) -> tuple[float, int]:
    """Empirical P(A|B) from TUNING league-wide data."""
    b_rows = [r for r in tuning_joint_rows if r[field_b] >= x_b]
    n = len(b_rows)
    if n == 0:
        return 0.0, 0
    hits = sum(1 for r in b_rows if r[field_a] >= x_a)
    return hits / n, n


# ============================================================================
# Part 25: Frechet bounds.
# ============================================================================

def frechet_bounds(p_a: float, p_b: float) -> tuple[float, float]:
    lower = max(0.0, p_a + p_b - 1.0)
    upper = min(p_a, p_b)
    return lower, upper


def clip_to_frechet(joint_p: float, p_a: float, p_b: float) -> float:
    lo, hi = frechet_bounds(p_a, p_b)
    return min(max(joint_p, lo), hi)
