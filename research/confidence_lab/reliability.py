"""
Confidence Framework Redesign: candidate reliability-score architectures.

CRITICAL PRINCIPLE (Part 2 of the prompt): confidence is not probability.
The CURRENT system (research.player_sog.count_models.confidence_score,
reused unchanged across SOG/blocks/assists/points) is a hand-designed
additive point system built entirely from DATA-MATURITY proxies (sample
size, TOI stability, stat-rate volatility, opponent-window maturity,
appearance rate) -- none of its five inputs is a direct measurement of
historical RELIABILITY (how often the model's stated probability has
actually been trustworthy in similar situations). Candidates B/C/D below
are three different ways of closing that gap while keeping the system
small, interpretable, and auditable (Part 17's explicit requirement --
no black-box ML).

  B -- SIMPLE RELIABILITY SCORE: the SAME five maturity inputs the
       current system already uses, made CONTINUOUS (z-scored and
       summed) instead of discretized into +-1 points. Tests whether
       finer resolution on the EXISTING information helps at all, with
       zero new information added.
  C -- CALIBRATED MULTI-FACTOR SCORE: three DEV-period (tuning-only)
       empirical skill-deviation lookups -- by probability region
       (Part 9), by sample-size bucket (Part 7), and by role-stability
       bucket (Part 8, via TOI coefficient of variation) -- each
       measuring actual historical Brier-skill in that bucket relative
       to the overall DEV average, summed. This is a genuinely
       different architecture: it is built from REALIZED reliability,
       not maturity proxies. C pools tuning rows ACROSS every available
       prop family into one shared set of lookup tables.
  D -- PROP-SPECIFIC CALIBRATED VERSION OF C: identical formula, but
       each prop family gets its OWN lookup tables (never pooled across
       props) -- directly tests Part 11's instruction to prefer a
       shared architecture with prop-specific calibrated parameters
       over one identical set of thresholds for every market.

All three reuse the SAME underlying, ALREADY-FROZEN raw model
probabilities -- nothing here refits or alters any prop's raw model.
"""
from __future__ import annotations

import statistics
from collections import defaultdict

PROB_BINS = 10          # decile buckets, Part 9
SAMPLE_BUCKETS = ((0, 10), (10, 25), (25, 50), (50, float("inf")))   # Part 7
TOI_CV_BUCKETS = ((0.0, 0.15), (0.15, 0.35), (0.35, float("inf")))    # Part 8


def prob_bin(p: float, n_bins: int = PROB_BINS) -> int:
    return min(int(p * n_bins), n_bins - 1)


def sample_bucket(n: int) -> int:
    for i, (lo, hi) in enumerate(SAMPLE_BUCKETS):
        if lo <= n < hi:
            return i
    return len(SAMPLE_BUCKETS) - 1


def toi_cv_bucket(cv: float | None) -> int:
    if cv is None:
        return 1  # treat missing as the middle (moderate) bucket
    for i, (lo, hi) in enumerate(TOI_CV_BUCKETS):
        if lo <= cv < hi:
            return i
    return len(TOI_CV_BUCKETS) - 1


def _skill(brier: float, actual_rate: float) -> float:
    naive = actual_rate * (1 - actual_rate)
    return 0.0 if naive <= 0 else 1.0 - brier / naive


def build_skill_deviation_tables(dev_examples: list[dict]) -> dict:
    """dev_examples: each {"prob": float, "actual": 0/1, "history_len": int,
    "toi_cv": float|None}. Returns the three DEV-period lookup tables plus
    the overall DEV average skill each is a deviation FROM."""
    briers = [(ex["prob"] - ex["actual"]) ** 2 for ex in dev_examples]
    actual_rate_overall = statistics.fmean(ex["actual"] for ex in dev_examples)
    overall_skill = _skill(statistics.fmean(briers), actual_rate_overall)

    def table_for(key_fn, n_keys):
        by_key = defaultdict(list)
        for ex, b in zip(dev_examples, briers):
            by_key[key_fn(ex)].append((b, ex["actual"]))
        table = {}
        for k in range(n_keys):
            items = by_key.get(k, [])
            if not items:
                table[k] = 0.0
                continue
            bs = [b for b, _ in items]
            ars = statistics.fmean(a for _, a in items)
            table[k] = _skill(statistics.fmean(bs), ars) - overall_skill
        return table

    region_table = table_for(lambda ex: prob_bin(ex["prob"]), PROB_BINS)
    sample_table = table_for(lambda ex: sample_bucket(ex["history_len"]), len(SAMPLE_BUCKETS))
    role_table = table_for(lambda ex: toi_cv_bucket(ex["toi_cv"]), len(TOI_CV_BUCKETS))
    return {"overall_skill": overall_skill, "region_table": region_table,
            "sample_table": sample_table, "role_table": role_table}


def candidate_c_score(prob: float, history_len: int, toi_cv: float | None, tables: dict) -> float:
    """Sum of three DEV-derived skill-deviation terms -- Part 20's
    proper-scoring-rule (Brier-skill) comparison, never raw hit rate."""
    return (tables["region_table"][prob_bin(prob)]
            + tables["sample_table"][sample_bucket(history_len)]
            + tables["role_table"][toi_cv_bucket(toi_cv)])


def candidate_b_score(history_len: int, toi_cv: float | None, stat_cv: float | None,
                       opponent_window_games: int, opponent_window_target: int, appearance_rate: float) -> float:
    """Continuous version of the CURRENT system's five inputs -- no new
    information, just z-scored/continuous instead of discretized +-1."""
    score = 0.0
    score += min(max((history_len - 15) / 25.0, -1.0), 1.0)
    if toi_cv is not None:
        score += min(max((0.25 - toi_cv) / 0.10, -1.0), 1.0)
    if stat_cv is not None:
        score += min(max((0.75 - stat_cv) / 0.25, -1.0), 1.0)
    score += min(max((opponent_window_games - opponent_window_target * 0.5) / (opponent_window_target * 0.5), -1.0), 1.0)
    score += min(max((appearance_rate - 0.75) / 0.15, -1.0), 1.0)
    return score


def cutoffs_from_dev(scores: list[float]) -> tuple[float, float]:
    """Tertile cutoffs chosen from DEV-period score distribution -- Part
    15/16: bucket boundaries selected from tuning evidence, frozen before
    evaluation, never re-picked after seeing validation-fold outcomes."""
    s = sorted(scores)
    n = len(s)
    lo = s[n // 3]
    hi = s[(2 * n) // 3]
    return lo, hi


def label_from_score(score: float, lo_cutoff: float, hi_cutoff: float) -> str:
    if score >= hi_cutoff:
        return "HIGH"
    if score < lo_cutoff:
        return "LOW"
    return "MEDIUM"
