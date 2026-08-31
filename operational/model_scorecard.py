"""
2026-27 Continuous Learning + Daily Model Audit framework, Part 2/3/4/6-14:
the core, reusable scorecard/calibration/residual engine that operates
over real prospective_ledger rows. Every function here is a pure
computation over already-recorded, already-settled rows -- nothing here
writes to the ledger, mutates a prediction, or touches a production
model/decision_policy.

Per this project's own established convention (see
research/special_teams_role_overlay/evaluate.py's own docstring: "a
small, local, per-file brier/log_loss implementation is this project's
established convention, not a shared cross-package utility"), brier()/
log_loss()/calibration_bins() are small, local reimplementations here
rather than an import from an unrelated research submodule.

DESIGN NOTE on fields the ledger does not persist directly: `decision`
(BET/WATCH/WAIT/PASS) and `edge`/`max_acceptable_price` are not stored
columns -- they are DETERMINISTIC functions of already-immutable, already
-stored fields (conservative_probability, market_no_vig_probability,
confidence) under the CURRENT, UNCHANGED decision_policy v3. This module
recomputes them rather than requiring a schema migration for values that
are already fully determined by what's on the row. "Expected-count MAE"
(Part 2) is explicitly NOT_AVAILABLE today -- the ledger does not persist
the underlying model mu, only the threshold probability -- flagged
honestly rather than approximated.
"""
from __future__ import annotations

import datetime as dt
import math
import statistics
from collections import defaultdict

import config
from pricing import odds_math

# ---------------------------------------------------------------- math --

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


def expected_calibration_error(probs: list[float], outcomes: list[float]) -> float | None:
    """Sample-size-weighted mean |predicted - actual| across calibration
    bins -- None (never 0.0) when there is nothing to bin."""
    bins = calibration_bins(probs, outcomes)
    if not bins:
        return None
    total = sum(b["n"] for b in bins)
    return sum(b["n"] * abs(b["mean_predicted"] - b["mean_actual"]) for b in bins) / total


# ------------------------------------------------------------ windows --

TIME_WINDOWS = ("LAST_1_DAY", "LAST_7_DAYS", "LAST_14_DAYS", "LAST_30_DAYS",
                "SEASON_TO_DATE", "FULL_PROSPECTIVE_SAMPLE")
_WINDOW_DAYS = {"LAST_1_DAY": 1, "LAST_7_DAYS": 7, "LAST_14_DAYS": 14, "LAST_30_DAYS": 30}

# Part 58: below this, a trend must be labeled LOW_SAMPLE, never
# "improving"/"deteriorating". Deliberately smaller than the 200-300
# obs PROMOTION bar in PROSPECTIVE_VALIDATION_PROTOCOL.md -- this is a
# display-honesty floor for a single day/window's own metrics, not a
# promotion gate.
MIN_SAMPLE_FOR_TREND = 20


def filter_by_window(rows: list[dict], window: str, now_utc: dt.datetime,
                      season_start_utc: str | None = None) -> list[dict]:
    if window not in TIME_WINDOWS:
        raise ValueError(f"unknown window {window!r}, must be one of {TIME_WINDOWS}")
    if window == "FULL_PROSPECTIVE_SAMPLE":
        return list(rows)
    if window == "SEASON_TO_DATE":
        if season_start_utc is None:
            return list(rows)
        return [r for r in rows if r.get("event_start_utc", "") >= season_start_utc]
    cutoff = (now_utc - dt.timedelta(days=_WINDOW_DAYS[window])).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return [r for r in rows if r.get("event_start_utc", "") >= cutoff]


# ------------------------------------------------------- settled rows --

def settled_rows(rows: list[dict]) -> list[dict]:
    """Only WIN/LOSS rows carry a real, binary-resolvable outcome --
    PENDING (not yet settled), PUSH (no clean hit/miss), VOID (never a
    real observation of the threshold outcome), and UNRESOLVED (resolver
    could not determine truth) are all excluded from scoring, never
    silently coerced into a hit or a miss."""
    return [r for r in rows if r.get("result_status") in ("WIN", "LOSS")]


def _hit(row: dict) -> float:
    return 1.0 if row["result_status"] == "WIN" else 0.0


# ------------------------------------------------------------ decision --

def recompute_decision(row: dict) -> dict:
    """Part 12: BET/WATCH/WAIT/PASS/NOT_AVAILABLE, recomputed from
    already-immutable stored fields under the CURRENT, unchanged
    decision_policy thresholds (config.MIN_CONSERVATIVE_EDGE/MIN_EV) --
    never a stored column, because it is fully determined by fields that
    cannot change after insertion. Returns NOT_AVAILABLE (not a guess)
    when market_no_vig_probability or conservative_probability is
    missing (research-only / no market observations)."""
    conservative_prob = row.get("conservative_probability")
    no_vig = row.get("market_no_vig_probability")
    if conservative_prob is None or no_vig is None:
        return {"decision": "NOT_AVAILABLE", "edge": None}
    edge = conservative_prob - no_vig
    ev = None
    if row.get("odds_american") is not None:
        ev = odds_math.expected_value(conservative_prob, row["odds_american"])
    meets_edge = edge >= config.MIN_CONSERVATIVE_EDGE
    meets_ev = ev is None or ev >= config.MIN_EV
    if meets_edge and meets_ev:
        base = "BET"
    elif edge > 0:
        base = "WATCH"
    else:
        base = "PASS"
    if row.get("confidence") == "LOW" and base in ("BET", "WATCH"):
        base = "WAIT"
    return {"decision": base, "edge": edge, "ev": ev}


EDGE_BUCKETS = ("<0pp", "0-2pp", "2-4pp", "4-6pp", "6-10pp", ">10pp")


def edge_bucket(edge_pp: float) -> str:
    if edge_pp < 0:
        return "<0pp"
    if edge_pp < 0.02:
        return "0-2pp"
    if edge_pp < 0.04:
        return "2-4pp"
    if edge_pp < 0.06:
        return "4-6pp"
    if edge_pp < 0.10:
        return "6-10pp"
    return ">10pp"


def implied_max_acceptable_price(conservative_probability: float, market_no_vig_probability: float,
                                  min_edge: float = config.MIN_CONSERVATIVE_EDGE) -> float | None:
    """Part 10: the worst American price that still clears min_edge over
    the ALREADY-COMPUTED no-vig baseline (the ledger persists no-vig
    probability, not the raw two-sided prices needed to recompute
    odds_math.max_acceptable_price's own opposing-side-aware formula
    exactly) -- an honest, algebraically-derived equivalent, not a
    silent approximation presented as the identical function."""
    if conservative_probability is None or market_no_vig_probability is None:
        return None
    target_prob = market_no_vig_probability + min_edge
    if target_prob <= 0 or target_prob >= 1:
        return None
    return odds_math.prob_to_american(target_prob)


# ----------------------------------------------------------- scorecard --

def compute_scorecard(rows: list[dict]) -> dict:
    """Part 2: the full per-(model/market/threshold) scorecard for
    whatever rows are passed in (caller filters by market/threshold/
    window first). Every metric is computed only from SETTLED
    (WIN/LOSS) rows; prediction_count still reflects the full input
    (including PENDING) so "how many predictions exist" and "how many
    can be scored yet" are never conflated."""
    scored = settled_rows(rows)
    out = {
        "prediction_count": len(rows),
        "event_count": len(scored),
        "mean_predicted_probability": None,
        "empirical_hit_rate": None,
        "calibration_error": None,
        "brier_score": None,
        "log_loss": None,
        "expected_count_mae": "NOT_AVAILABLE (model mu is not persisted in the ledger schema, "
                               "only the threshold probability)",
        "residual_mean": None,
        "residual_variance": None,
        "confidence_bucket_performance": {},
        "player_concentration": {},
    }
    if not scored:
        return out

    probs = [r["raw_probability"] for r in scored if r.get("raw_probability") is not None]
    hits = [_hit(r) for r in scored if r.get("raw_probability") is not None]
    if probs:
        out["mean_predicted_probability"] = statistics.fmean(probs)
        out["empirical_hit_rate"] = statistics.fmean(hits)
        out["calibration_error"] = expected_calibration_error(probs, hits)
        out["brier_score"] = statistics.fmean(brier(p, y) for p, y in zip(probs, hits))
        out["log_loss"] = statistics.fmean(log_loss(p, y) for p, y in zip(probs, hits))
        residuals = [y - p for p, y in zip(probs, hits)]
        out["residual_mean"] = statistics.fmean(residuals)
        out["residual_variance"] = statistics.pvariance(residuals) if len(residuals) > 1 else 0.0

    by_confidence = defaultdict(list)
    for r in scored:
        if r.get("confidence") and r.get("raw_probability") is not None:
            by_confidence[r["confidence"]].append(r)
    for bucket, bucket_rows in by_confidence.items():
        bp = [r["raw_probability"] for r in bucket_rows]
        bh = [_hit(r) for r in bucket_rows]
        out["confidence_bucket_performance"][bucket] = {
            "n": len(bucket_rows), "mean_predicted": statistics.fmean(bp),
            "empirical_hit_rate": statistics.fmean(bh),
            "brier_score": statistics.fmean(brier(p, y) for p, y in zip(bp, bh)),
        }

    by_player = defaultdict(int)
    for r in scored:
        if r.get("player_id"):
            by_player[r["player_id"]] += 1
    if by_player:
        total = sum(by_player.values())
        top_player, top_n = max(by_player.items(), key=lambda kv: kv[1])
        out["player_concentration"] = {"unique_players": len(by_player),
                                        "top_player_id": top_player,
                                        "top_player_share": top_n / total}
    return out


def compare_base_vs_shadow(rows: list[dict], base_field: str = "raw_probability",
                            shadow_field: str = "sog_shadow_raw_probability") -> dict:
    """Part 4/5: paired base-vs-shadow comparison over rows carrying
    BOTH fields (never imputes a missing shadow value). Reports each
    side's own scorecard plus the mean signed difference -- never merges
    the two into one number."""
    paired = [r for r in settled_rows(rows) if r.get(base_field) is not None and r.get(shadow_field) is not None]
    if not paired:
        return {"n": 0, "base": None, "shadow": None, "mean_shadow_minus_base": None}
    base_scorecard = compute_scorecard([{**r, "raw_probability": r[base_field]} for r in paired])
    shadow_scorecard = compute_scorecard([{**r, "raw_probability": r[shadow_field]} for r in paired])
    diffs = [r[shadow_field] - r[base_field] for r in paired]
    return {"n": len(paired), "base": base_scorecard, "shadow": shadow_scorecard,
            "mean_shadow_minus_base": statistics.fmean(diffs)}


# ------------------------------------------------------------------ CLV --

def clv_summary(rows: list[dict], group_by: str | None = None) -> dict:
    """Part 8: aggregates the ledger's OWN already-computed `clv` column
    (set only by settle_completed_observation when a real closing price
    existed) -- never recomputes CLV itself (operational/clv_resolver.py
    owns that math). Rows with clv=None are excluded, never treated as
    0."""
    with_clv = [r for r in rows if r.get("clv") is not None]
    if group_by is None:
        if not with_clv:
            return {"n": 0, "mean_clv": None}
        return {"n": len(with_clv), "mean_clv": statistics.fmean(r["clv"] for r in with_clv)}
    groups = defaultdict(list)
    for r in with_clv:
        groups[r.get(group_by)].append(r["clv"])
    return {g: {"n": len(vs), "mean_clv": statistics.fmean(vs)} for g, vs in groups.items()}


def edge_bucket_performance(rows: list[dict]) -> dict:
    """Part 9: outcomes grouped by recomputed edge bucket -- used to
    check whether estimated edge increases monotonically with realized
    hit rate / CLV, never assumed to."""
    buckets = defaultdict(list)
    for r in settled_rows(rows):
        decision = recompute_decision(r)
        if decision["edge"] is None:
            continue
        buckets[edge_bucket(decision["edge"])].append(r)
    out = {}
    for bucket in EDGE_BUCKETS:
        bucket_rows = buckets.get(bucket, [])
        if not bucket_rows:
            continue
        hits = [_hit(r) for r in bucket_rows]
        clvs = [r["clv"] for r in bucket_rows if r.get("clv") is not None]
        out[bucket] = {"n": len(bucket_rows), "empirical_hit_rate": statistics.fmean(hits),
                        "mean_clv": statistics.fmean(clvs) if clvs else None}
    return out


def decision_state_breakdown(rows: list[dict]) -> dict:
    """Part 12: every decision state evaluated separately, including
    counterfactual hit-rate for WATCH/WAIT/PASS -- but REAL_BET P&L is
    reported ONLY for rows that are actually record_type='REAL_BET',
    never merged with the counterfactual numbers for other states
    (Part 44)."""
    by_state = defaultdict(list)
    for r in settled_rows(rows):
        by_state[recompute_decision(r)["decision"]].append(r)
    out = {}
    for state, state_rows in by_state.items():
        hits = [_hit(r) for r in state_rows]
        entry = {"n": len(state_rows), "empirical_hit_rate": statistics.fmean(hits)}
        if state == "BET":
            real_bets = [r for r in state_rows if r.get("record_type") == "REAL_BET"
                         and r.get("profit_loss") is not None]
            entry["real_bet_pnl_sum"] = (sum(r["profit_loss"] for r in real_bets)
                                          if real_bets else None)
            entry["real_bet_n"] = len(real_bets)
        out[state] = entry
    return out


def market_movement_summary(rows: list[dict]) -> dict:
    """Part 13: whether the market moved toward or away from the model
    between observation and close -- requires both market_no_vig_
    probability (at observation) and a real closing price; NOT_AVAILABLE
    otherwise, never inferred from one side alone."""
    toward, away, unchanged, skipped = 0, 0, 0, 0
    for r in rows:
        obs_prob = r.get("market_no_vig_probability")
        model_prob = r.get("raw_probability")
        closing_odds = r.get("closing_odds")
        if obs_prob is None or model_prob is None or closing_odds is None:
            skipped += 1
            continue
        closing_prob = odds_math.american_to_prob(closing_odds)
        obs_gap = abs(model_prob - obs_prob)
        close_gap = abs(model_prob - closing_prob)
        if close_gap < obs_gap - 1e-9:
            toward += 1
        elif close_gap > obs_gap + 1e-9:
            away += 1
        else:
            unchanged += 1
    return {"toward_model": toward, "away_from_model": away, "unchanged": unchanged,
            "not_available": skipped}
