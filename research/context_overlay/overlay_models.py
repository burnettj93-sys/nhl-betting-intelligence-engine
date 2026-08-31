"""
Part 7/8: the small, pre-specified adjustment-candidate family for the
Context-State Probability Overlay slice. Every candidate maps a single
frozen raw probability to a single adjusted probability -- never a new
per-player model, never a feature model. All parameters are fit on
DEVELOPMENT (TUNING season) data only by the driver; this module itself
contains no data, no fitting-on-eval, no hardcoded magic numbers beyond
the small pre-registered constants documented below.

A. NO_ADJUSTMENT           -- the null candidate; always in the running.
B. FIXED_LOGIT_OFFSET      -- logit(p_adj) = logit(p_raw) + offset,
                               offset <= 0, offset chosen by grid search
                               minimizing DEV log loss (Part 8's preferred
                               representation).
C. SHRUNK_LOGIT_OFFSET     -- B's offset shrunk toward 0 by DEV sample
                               size (n / (n + K_SHRINK)) -- a regularized
                               variant, not a second independent fit.
D. BAYESIAN_CONTEXT_BLEND  -- probability-space shift: raw_p + shrink *
                               (dev_actual_rate - dev_mean_raw_p), shrink
                               = n / (n + K_BAYES). A different
                               (multiplicative-evidence-weight) family
                               from B/C's logit-additive family, so the
                               driver's winner selection is a genuine
                               contest, not two disguised copies of the
                               same idea.
E. ISOTONIC_BIN_RECAL      -- equal-frequency DEV bins on raw_p, pooled
                               via pool-adjacent-violators (PAVA) to force
                               a non-decreasing recalibration curve, then
                               applied to EVAL rows by frozen bin-edge
                               lookup. Only fit if DEV sample size clears
                               MIN_ISOTONIC_N (else E is INSUFFICIENT_DATA
                               and drops out of the contest -- Part 7's
                               "only if sample size supports it").

Winner selection (done by the driver, not here): lowest DEV log loss,
Brier as tie-break, among whichever of A-E actually fit. This module
never picks a winner itself -- it only fits and applies.
"""
from __future__ import annotations

import math

EPS = 1e-9
OFFSET_GRID_MIN = -3.0
OFFSET_GRID_MAX = 0.0
OFFSET_GRID_STEP = 0.01
K_SHRINK = 100
K_BAYES = 200
MIN_ISOTONIC_N = 400
ISOTONIC_MIN_BIN_N = 40
ISOTONIC_MAX_BINS = 10


def logit(p: float) -> float:
    p = min(max(p, EPS), 1 - EPS)
    return math.log(p / (1 - p))


def inv_logit(z: float) -> float:
    if z >= 0:
        ez = math.exp(-z)
        return 1.0 / (1.0 + ez)
    ez = math.exp(z)
    return ez / (1.0 + ez)


def brier(p: float, y: float) -> float:
    return (p - y) ** 2


def log_loss(p: float, y: float) -> float:
    p = min(max(p, EPS), 1 - EPS)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def mean_log_loss(pairs: list[tuple[float, float]]) -> float:
    return sum(log_loss(p, y) for p, y in pairs) / len(pairs)


def mean_brier(pairs: list[tuple[float, float]]) -> float:
    return sum(brier(p, y) for p, y in pairs) / len(pairs)


# ---- A. no adjustment ----

def candidate_a_apply(p: float) -> float:
    return p


# ---- B. fixed logit offset ----

def fit_fixed_logit_offset(dev_pairs: list[tuple[float, float]]) -> dict:
    best_offset, best_loss = 0.0, mean_log_loss(dev_pairs)
    n_steps = int(round((OFFSET_GRID_MAX - OFFSET_GRID_MIN) / OFFSET_GRID_STEP))
    for i in range(n_steps + 1):
        offset = OFFSET_GRID_MIN + i * OFFSET_GRID_STEP
        adjusted_pairs = [(inv_logit(logit(p) + offset), y) for p, y in dev_pairs]
        loss = mean_log_loss(adjusted_pairs)
        if loss < best_loss:
            best_offset, best_loss = offset, loss
    return {"offset": best_offset, "dev_log_loss": best_loss}


def candidate_b_apply(p: float, offset: float) -> float:
    return inv_logit(logit(p) + offset)


# ---- C. shrunk logit offset ----

def fit_shrunk_logit_offset(dev_pairs: list[tuple[float, float]], raw_offset: float) -> dict:
    n = len(dev_pairs)
    shrunk_offset = raw_offset * (n / (n + K_SHRINK))
    adjusted_pairs = [(inv_logit(logit(p) + shrunk_offset), y) for p, y in dev_pairs]
    return {"offset": shrunk_offset, "raw_offset": raw_offset, "n_dev": n, "k_shrink": K_SHRINK,
            "dev_log_loss": mean_log_loss(adjusted_pairs)}


def candidate_c_apply(p: float, shrunk_offset: float) -> float:
    return inv_logit(logit(p) + shrunk_offset)


# ---- D. Bayesian-flavored probability-space blend ----

def fit_bayesian_blend(dev_pairs: list[tuple[float, float]]) -> dict:
    n = len(dev_pairs)
    mean_raw = sum(p for p, _ in dev_pairs) / n
    mean_actual = sum(y for _, y in dev_pairs) / n
    shrink_weight = n / (n + K_BAYES)
    shift = shrink_weight * (mean_actual - mean_raw)
    adjusted_pairs = [(min(max(p + shift, EPS), 1 - EPS), y) for p, y in dev_pairs]
    return {"shift": shift, "mean_raw": mean_raw, "mean_actual": mean_actual,
            "shrink_weight": shrink_weight, "n_dev": n, "k_bayes": K_BAYES,
            "dev_log_loss": mean_log_loss(adjusted_pairs)}


def candidate_d_apply(p: float, shift: float) -> float:
    return min(max(p + shift, EPS), 1 - EPS)


# ---- E. isotonic bin recalibration (PAVA) ----

def _pava(values: list[float], weights: list[float]) -> list[float]:
    blocks = []  # each: [avg, weight, start, end]
    for i, (v, w) in enumerate(zip(values, weights)):
        blocks.append([v, w, i, i])
        while len(blocks) >= 2 and blocks[-2][0] > blocks[-1][0]:
            b2 = blocks.pop()
            b1 = blocks.pop()
            new_w = b1[1] + b2[1]
            new_avg = (b1[0] * b1[1] + b2[0] * b2[1]) / new_w
            blocks.append([new_avg, new_w, b1[2], b2[3]])
    result = [0.0] * len(values)
    for avg, _, s, e in blocks:
        for j in range(s, e + 1):
            result[j] = avg
    return result


def fit_isotonic_bins(dev_pairs: list[tuple[float, float]]) -> dict | None:
    n = len(dev_pairs)
    if n < MIN_ISOTONIC_N:
        return None
    n_bins = min(ISOTONIC_MAX_BINS, n // ISOTONIC_MIN_BIN_N)
    if n_bins < 2:
        return None
    ordered = sorted(dev_pairs, key=lambda pr: pr[0])
    bin_size = n // n_bins
    bin_edges_hi = []
    bin_mean_p = []
    bin_actual_rate = []
    bin_n = []
    idx = 0
    for b in range(n_bins):
        end = n if b == n_bins - 1 else idx + bin_size
        chunk = ordered[idx:end]
        if not chunk:
            continue
        bin_mean_p.append(sum(p for p, _ in chunk) / len(chunk))
        bin_actual_rate.append(sum(y for _, y in chunk) / len(chunk))
        bin_n.append(len(chunk))
        bin_edges_hi.append(chunk[-1][0])
        idx = end
    pooled_rate = _pava(bin_actual_rate, [float(x) for x in bin_n])
    adjusted_pairs = []
    for p, y in dev_pairs:
        adjusted_pairs.append((_isotonic_lookup(p, bin_edges_hi, pooled_rate), y))
    return {"bin_edges_hi": bin_edges_hi, "bin_mean_p": bin_mean_p, "bin_actual_rate": bin_actual_rate,
            "pooled_rate": pooled_rate, "bin_n": bin_n, "n_bins": len(bin_edges_hi), "n_dev": n,
            "dev_log_loss": mean_log_loss(adjusted_pairs)}


def _isotonic_lookup(p: float, bin_edges_hi: list[float], pooled_rate: list[float]) -> float:
    for edge, rate in zip(bin_edges_hi, pooled_rate):
        if p <= edge:
            return rate
    return pooled_rate[-1]


def candidate_e_apply(p: float, isotonic_fit: dict) -> float:
    return _isotonic_lookup(p, isotonic_fit["bin_edges_hi"], isotonic_fit["pooled_rate"])
