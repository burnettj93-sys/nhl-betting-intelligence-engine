"""
Parts 2-5: PIT-safe performance-state (cold/hot) classification.

Cold/hot state is measured EXPECTATION-RELATIVE (Part 3), not by raw
recent production alone: the primary signal is the player's own recent-5
actual rate relative to their own rolling baseline-20 rate (log-ratio) --
the SAME "recent_form_log_ratio" feature already computed inside every
frozen marginal model's own feature vector (`cm.build_feature_vector`),
reused here as a state-classification signal rather than a model input.
This makes a Matthews 1-goal-in-5 outing genuinely different from a
4th-liner's 1-goal-in-5 outing: both are compared to their OWN baseline,
not a league constant.

TOI trend (recent-10 icetime vs baseline-20 icetime) is the second,
pre-specified signal for the Part 4 multi-signal state -- kept small and
interpretable, not a large state classifier.

Thresholds are TUNING-fit percentile cutoffs, frozen before EVAL scoring
(Part 34/35's discipline) -- never re-derived per season.
"""
from __future__ import annotations

import math
import statistics


def form_log_ratio(recent_rate: float | None, baseline_rate: float, eps: float = 1e-6) -> float | None:
    if recent_rate is None or baseline_rate is None or baseline_rate <= 0:
        return None
    return math.log(max(recent_rate, eps)) - math.log(max(baseline_rate, eps))


def toi_log_ratio(recent_toi: float | None, baseline_toi: float | None, eps: float = 1e-6) -> float | None:
    if recent_toi is None or baseline_toi is None or baseline_toi <= 0:
        return None
    return math.log(max(recent_toi, eps)) - math.log(max(baseline_toi, eps))


class StateThresholds:
    """TUNING-fit percentile cutoffs for the form-ratio signal -- frozen,
    never recomputed on EVAL data. COLD = bottom `pct`, HOT = top `pct`,
    NORMAL = everything else."""

    def __init__(self, tuning_form_ratios: list[float], pct: float = 0.20):
        values = sorted(v for v in tuning_form_ratios if v is not None)
        n = len(values)
        if n == 0:
            self.cold_cutoff, self.hot_cutoff = -0.3, 0.3
            return
        self.cold_cutoff = values[int(n * pct)]
        self.hot_cutoff = values[int(n * (1 - pct))]

    def classify(self, form_ratio: float | None) -> str:
        if form_ratio is None:
            return "UNKNOWN"
        if form_ratio <= self.cold_cutoff:
            return "COLD"
        if form_ratio >= self.hot_cutoff:
            return "HOT"
        return "NORMAL"


def classify_multi_signal(sog_state: str, toi_ratio: float | None, toi_decline_cutoff: float) -> str:
    """Part 4's multi-signal state: COLD_AND_TOI_DECLINE requires BOTH a
    COLD production state AND a real TOI decline -- deliberately a small,
    interpretable AND-rule, not a fitted classifier."""
    toi_declining = toi_ratio is not None and toi_ratio <= toi_decline_cutoff
    if sog_state == "COLD" and toi_declining:
        return "COLD_AND_TOI_DECLINE"
    return sog_state


def mean_or_none(values: list[float]) -> float | None:
    vals = [v for v in values if v is not None]
    return statistics.fmean(vals) if vals else None
