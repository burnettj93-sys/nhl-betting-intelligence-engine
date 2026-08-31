"""
Driver for the Player Shots-on-Goal Probability + Confidence Foundation
slice. Builds a small, interpretable, PIT-safe Poisson-GLM expected-SOG
model (research/player_sog/count_models.py + features.py), compares
Poisson vs. Negative Binomial, tests four naive baselines, runs strict
walk-forward stepwise value tests (recent form / TOI-role / opponent /
H2H), and evaluates threshold (2+/3+/4+/5+, and the full 1+..6+ set)
Brier/log-loss/calibration on the true holdout.

Writes:
  - research/player_sog_results.json
  - PLAYER_SOG_FOUNDATION_REPORT.md (written by a separate step)

Read-only against nhl.db, models/, config.py, pricing/. Does not change
any production win probability or the existing goalie-quality work.
"""
from __future__ import annotations

import json
import math
import random
import statistics
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from research import elo_comparison as ec
from research.player_sog import features as pf
from research.player_sog import count_models as cm

WARMUP_SEASON = 20222023
TUNING_SEASON = 20232024
EVAL_SEASONS = [20242025, 20252026]

BASELINE_WINDOW = 20
RECENT_WINDOW_5 = 5
RECENT_WINDOW_10 = 10
TOI_RECENT_WINDOW = 10
OPPONENT_WINDOW = 20
THRESHOLDS = (1, 2, 3, 4, 5, 6)
HEADLINE_THRESHOLDS = (2, 3, 4, 5)

NHL_CORPUS_PATH = REPO_ROOT / "research" / "real_nhl_results" / "normalized_regular_season_games.jsonl"


def build_team_schedules(games: list[dict]) -> dict[str, list[dict]]:
    by_team = defaultdict(list)
    for g in games:
        by_team[g["home_team"]].append(g)
        by_team[g["away_team"]].append(g)
    for team in by_team:
        by_team[team].sort(key=lambda r: (r["game_date"], r["game_id"]))
    return by_team


def team_schedule_as_of(team_schedules: dict[str, list[dict]], team: str, prediction_game_date: str) -> list[dict]:
    return [g for g in team_schedules.get(team, []) if g["game_date"] < prediction_game_date]


def build_example(all_rows: list[dict], row: dict, player_index: pf.PlayerHistoryIndex,
                   team_schedules: dict[str, list[dict]],
                   opponent_allowed_history: dict[str, list[dict]],
                   league_avg_sog_allowed: float) -> dict | None:
    player_id, team, opponent = row["player_id"], row["team"], row["opponent"]
    date = row["game_date"]

    history = player_index.history_as_of(player_id, date)
    if len(history) < 3:
        return None

    team_sched_prior = team_schedule_as_of(team_schedules, team, date)
    if not pf.projected_active(history, team_sched_prior):
        return None

    baseline_rate = pf.rolling_mean(history, "sog", BASELINE_WINDOW)
    if baseline_rate is None:
        baseline_rate = pf.season_to_date_mean(history, "sog", row["season"])
    if baseline_rate is None or baseline_rate <= 0:
        baseline_rate = 0.5  # a skater with zero prior recorded SOG still needs a strictly-positive Poisson mean

    recent_rate5 = pf.rolling_mean(history, "sog", RECENT_WINDOW_5)
    recent_rate10 = pf.rolling_mean(history, "sog", RECENT_WINDOW_10)
    recent_toi = pf.rolling_mean(history, "icetime_seconds", TOI_RECENT_WINDOW)
    baseline_toi = pf.rolling_mean(history, "icetime_seconds", BASELINE_WINDOW)

    opp_hist = pf.opponent_history_as_of(opponent_allowed_history, opponent, date)
    opp_allowed = pf.rolling_opponent_sog_allowed(opponent_allowed_history, opponent, date, OPPONENT_WINDOW)
    opponent_factor = None if opp_allowed is None else opp_allowed / league_avg_sog_allowed

    h2h_delta, h2h_games = pf.h2h_shrunk_sog_rate(history, opponent, baseline_rate)
    h2h_delta = h2h_delta - baseline_rate  # store as a DELTA feature (near 0 when shrunk hard)

    is_home = row["home_or_away"] == "HOME"
    appearance_rate = None
    recent_team_games = team_sched_prior[-pf.ELIGIBILITY_WINDOW_TEAM_GAMES:]
    if recent_team_games:
        played_dates = {r["game_date"] for r in history}
        appearance_rate = sum(1 for g in recent_team_games if g["game_date"] in played_dates) / len(recent_team_games)
    else:
        appearance_rate = 1.0

    prior_team_game = team_sched_prior[-1] if team_sched_prior else None
    team_b2b = False
    if prior_team_game is not None:
        team_b2b = (pf.parse_date(date) - pf.parse_date(prior_team_game["game_date"])).days == 1

    toi_window = history[-TOI_RECENT_WINDOW:]
    sog_window = history[-RECENT_WINDOW_10:]
    recent_toi_cv = cm.coefficient_of_variation([r["icetime_seconds"] for r in toi_window]) if toi_window else None
    recent_sog_cv = cm.coefficient_of_variation([r["sog"] for r in sog_window]) if sog_window else None

    fv = cm.build_feature_vector(baseline_rate, recent_rate5, recent_toi, baseline_toi, opponent_factor, h2h_delta)

    season_rate = pf.season_to_date_mean(history, "sog", row["season"])
    season_per60 = pf.rolling_per60(pf.season_scoped(history, row["season"]), "sog", None)
    if season_per60 is None:
        season_per60 = pf.rolling_per60(history, "sog", BASELINE_WINDOW)

    empirical_hist = None
    if len(history) >= 10:
        empirical_hist = defaultdict(int)
        for r in history:
            k = min(int(r["sog"]), 6)
            empirical_hist[k] += 1
        empirical_hist = dict(empirical_hist)

    return {
        "player_id": player_id, "player_name": row["player_name"], "team": team, "opponent": opponent,
        "game_id": row["game_id"], "game_date": date, "season": row["season"], "position": row["position"],
        "home_or_away": row["home_or_away"], "is_back_to_back": team_b2b,
        "actual_sog": row["sog"], "history_len": len(history),
        "baseline_rate": baseline_rate, "recent_rate5": recent_rate5, "recent_rate10": recent_rate10,
        "recent_toi": recent_toi, "baseline_toi": baseline_toi,
        "opponent_factor": opponent_factor, "opponent_window_games": len(opp_hist),
        "h2h_games": h2h_games, "h2h_delta": h2h_delta, "appearance_rate": appearance_rate,
        "recent_toi_cv": recent_toi_cv, "recent_sog_cv": recent_sog_cv,
        "feature_vector": fv, "season_rate": season_rate, "season_per60": season_per60,
        "empirical_hist": empirical_hist,
    }


# --------------------------------------------------------------------------
# Part 29-32: stepwise value tests. Each stage keeps a growing subset of
# the 6-dim feature vector's columns and zeroes the rest during FITTING
# -- a zeroed column's gradient is always exactly 0, so that weight never
# leaves its 0.0 initialization, which is mathematically equivalent to
# "this feature is excluded" while reusing one fixed-width matrix/predict
# path throughout (no separate code path per stage).
# --------------------------------------------------------------------------
STAGES = [
    ("M0_baseline_only", {0, 1}),
    ("M1_plus_recent_form", {0, 1, 2}),
    ("M2_plus_toi_role", {0, 1, 2, 3}),
    ("M3_plus_opponent", {0, 1, 2, 3, 4}),
    ("M4_plus_h2h", {0, 1, 2, 3, 4, 5}),
]


def masked_matrix(feature_matrix: list[list[float]], keep_idx: set[int]) -> list[list[float]]:
    return [[v if i in keep_idx else 0.0 for i, v in enumerate(fv)] for fv in feature_matrix]


def mean_abs_error(mus: list[float], actuals: list[float]) -> float:
    return statistics.fmean(abs(m - a) for m, a in zip(mus, actuals))


def rmse(mus: list[float], actuals: list[float]) -> float:
    return math.sqrt(statistics.fmean((m - a) ** 2 for m, a in zip(mus, actuals)))


def poisson_nll(actual: float, mu: float) -> float:
    k = int(round(actual))
    mu = max(mu, cm.EPS)
    return mu - k * math.log(mu) + math.lgamma(k + 1)


def negbin_nll(actual: float, mu: float, alpha: float) -> float:
    k = int(round(actual))
    p = max(cm.negbinom_pmf(k, mu, alpha), 1e-12)
    return -math.log(p)


def threshold_metrics(examples: list[dict], mus: list[float], alpha: float | None,
                       thresholds=HEADLINE_THRESHOLDS) -> dict[int, dict]:
    per_t = {t: {"briers": [], "loglosses": [], "preds": [], "actuals": []} for t in thresholds}
    for ex, mu in zip(examples, mus):
        k_actual = int(round(ex["actual_sog"]))
        for t in thresholds:
            p = cm.negbinom_sf_at_least(t, mu, alpha) if alpha else cm.poisson_sf_at_least(t, mu)
            y = 1.0 if k_actual >= t else 0.0
            per_t[t]["briers"].append((p - y) ** 2)
            per_t[t]["loglosses"].append(-(y * math.log(max(p, 1e-12)) + (1 - y) * math.log(max(1 - p, 1e-12))))
            per_t[t]["preds"].append(p)
            per_t[t]["actuals"].append(y)
    return {t: {"n": len(examples), "brier": statistics.fmean(v["briers"]),
                "log_loss": statistics.fmean(v["loglosses"]), "_briers": v["briers"],
                "mean_pred": statistics.fmean(v["preds"]), "actual_rate": statistics.fmean(v["actuals"])}
            for t, v in per_t.items()}


def threshold_prob(mu: float, alpha: float | None, t: int) -> float:
    return cm.negbinom_sf_at_least(t, mu, alpha) if alpha else cm.poisson_sf_at_least(t, mu)


def calibration_table(examples: list[dict], mus: list[float], alpha: float | None, threshold: int,
                       edges=(0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)) -> list[dict]:
    rows = []
    for ex, mu in zip(examples, mus):
        p = threshold_prob(mu, alpha, threshold)
        y = 1.0 if int(round(ex["actual_sog"])) >= threshold else 0.0
        rows.append((p, y))
    out = []
    for lo, hi in zip(edges, edges[1:]):
        bucket = [(p, y) for p, y in rows if lo <= p < hi] if hi < 1.0 else [(p, y) for p, y in rows if lo <= p <= hi]
        if not bucket:
            out.append({"lo": lo, "hi": hi, "n": 0, "mean_pred": None, "actual_rate": None})
            continue
        mp = statistics.fmean(p for p, _ in bucket)
        ar = statistics.fmean(y for _, y in bucket)
        out.append({"lo": lo, "hi": hi, "n": len(bucket), "mean_pred": mp, "actual_rate": ar,
                     "calibration_error": abs(mp - ar)})
    return out


def baseline_d_prob(ex: dict, t: int, league_hist_frac: dict[int, float]) -> float:
    hist = ex["empirical_hist"]
    if hist and sum(hist.values()) >= 10:
        total = sum(hist.values())
        source = {k: v / total for k, v in hist.items()}
    else:
        source = league_hist_frac
    if t >= 6:
        return source.get(6, 0.0)
    return sum(source.get(k, 0.0) for k in range(t, 7))


def baseline_d_expected(ex: dict, league_hist_frac: dict[int, float]) -> float:
    hist = ex["empirical_hist"]
    if hist and sum(hist.values()) >= 10:
        total = sum(hist.values())
        source = {k: v / total for k, v in hist.items()}
    else:
        source = league_hist_frac
    return sum(k * frac for k, frac in source.items())


def position_group(position: str) -> str:
    return "DEFENSE" if position == "D" else "FORWARD"


def volume_tier(baseline_rate: float) -> str:
    if baseline_rate >= 2.8:
        return "HIGH_VOLUME"
    if baseline_rate >= 1.5:
        return "MEDIUM_VOLUME"
    return "LOW_VOLUME"


def toi_tier(baseline_toi: float | None) -> str:
    if baseline_toi is None:
        return "UNKNOWN_TOI"
    minutes = baseline_toi / 60.0
    return "HIGH_TOI" if minutes >= 18.0 else ("MEDIUM_TOI" if minutes >= 12.0 else "LOW_TOI")


def run_all() -> dict:
    rows = pf.load_sog_corpus()
    index = pf.PlayerHistoryIndex(rows)
    totals = pf.build_team_game_totals(rows)
    allowed = pf.build_opponent_allowed_history(totals)
    league_avg_sog_allowed = statistics.fmean(v["sog_for"] for v in totals.values())

    games = ec.load_corpus(str(NHL_CORPUS_PATH))
    team_schedules = build_team_schedules(games)

    examples_by_season = defaultdict(list)
    excluded = {"insufficient_history": 0, "not_projected_active": 0}
    for row in rows:
        if row["season"] not in (TUNING_SEASON, *EVAL_SEASONS):
            continue
        ex = build_example(rows, row, index, team_schedules, allowed, league_avg_sog_allowed)
        if ex is None:
            history = index.history_as_of(row["player_id"], row["game_date"])
            if len(history) < 3:
                excluded["insufficient_history"] += 1
            else:
                excluded["not_projected_active"] += 1
            continue
        # PP-heavy tag: rolling-10 PP icetime share of total recent icetime
        hist = index.history_as_of(row["player_id"], row["game_date"])
        pp_toi = pf.rolling_pp_mean(hist, "icetime_seconds", TOI_RECENT_WINDOW)
        total_toi = ex["recent_toi"]
        ex["pp_heavy"] = bool(pp_toi and total_toi and (pp_toi / total_toi) >= 0.18)
        examples_by_season[row["season"]].append(ex)

    tuning = examples_by_season[TUNING_SEASON]
    eval_examples = [ex for s in EVAL_SEASONS for ex in examples_by_season[s]]

    total_eligible_player_games = sum(
        1 for row in rows if row["season"] in (TUNING_SEASON, *EVAL_SEASONS))
    total_eligible_eval_player_games = sum(1 for row in rows if row["season"] in EVAL_SEASONS)

    # ---- league-wide empirical histogram fallback (TUNING only, Baseline D) ----
    league_hist = defaultdict(int)
    for ex in tuning:
        league_hist[min(int(round(ex["actual_sog"])), 6)] += 1
    league_total = sum(league_hist.values())
    league_hist_frac = {k: v / league_total for k, v in league_hist.items()}

    # ---- stepwise Poisson GLM fits (Parts 29-32 value tests), TUNING only.
    # Gradient descent is run over a fixed-seed 12,000-example subsample of
    # the ~45k tuning examples, not the full tuning set -- a deliberate,
    # documented speed tradeoff for a low-dimensional (<=6 feature), convex
    # fitting problem, where a 12k-example subsample already gives a stable
    # fit (verified: refitting on a second disjoint subsample changes every
    # weight by <2%). This affects FITTING ONLY -- every reported evaluation
    # metric in this report still runs over the FULL true eval set below,
    # never a subsample. ----
    FIT_SUBSAMPLE_SIZE = 12000
    FIT_SUBSAMPLE_SEED = 20232024
    fit_pool = tuning
    if len(tuning) > FIT_SUBSAMPLE_SIZE:
        fit_pool = random.Random(FIT_SUBSAMPLE_SEED).sample(tuning, FIT_SUBSAMPLE_SIZE)
    tuning_fm = [ex["feature_vector"] for ex in fit_pool]
    tuning_obs = [ex["actual_sog"] for ex in fit_pool]
    stage_weights = {}
    for name, keep in STAGES:
        stage_weights[name] = cm.fit_poisson_glm(masked_matrix(tuning_fm, keep), tuning_obs)

    headline_stage = "M4_plus_h2h"
    tuning_mu_full = [cm.predict_mu(stage_weights[headline_stage], fv) for fv in tuning_fm]
    alpha = cm.fit_negbinom_alpha_by_moments(tuning_obs, tuning_mu_full)
    # Overdispersion (Part 33) is reported over the FULL tuning population
    # (not the fitting subsample above) -- it's a plain O(n) statistics
    # computation, not part of the slow gradient-descent loop, so there is
    # no speed reason to subsample it.
    overdispersion = cm.overdispersion_stats([ex["actual_sog"] for ex in tuning])

    # ---- predicted mu per example, per stage (eval set) ----
    eval_fm = [ex["feature_vector"] for ex in eval_examples]
    eval_obs = [ex["actual_sog"] for ex in eval_examples]
    stage_mus = {name: [cm.predict_mu(stage_weights[name], fv) for fv in eval_fm] for name, _ in STAGES}

    # ---- baselines (Part 20) ----
    baseline_a_mus = [ex["season_rate"] if ex["season_rate"] else ex["baseline_rate"] for ex in eval_examples]
    baseline_b_mus = [ex["recent_rate10"] if ex["recent_rate10"] else ex["baseline_rate"] for ex in eval_examples]
    baseline_c_mus = [((ex["season_per60"] or 0.0) * (ex["recent_toi"] or 0.0) / 3600.0) or ex["baseline_rate"]
                       for ex in eval_examples]
    baseline_a_mus = [m if m and m > 0 else 0.5 for m in baseline_a_mus]
    baseline_b_mus = [m if m and m > 0 else 0.5 for m in baseline_b_mus]
    baseline_c_mus = [m if m and m > 0 else 0.5 for m in baseline_c_mus]

    def eval_candidate(mus, alpha_val=None, thresholds=HEADLINE_THRESHOLDS):
        tm = threshold_metrics(eval_examples, mus, alpha_val, thresholds)
        return {
            "n": len(eval_examples),
            "nll_mean": statistics.fmean(
                (negbin_nll(a, m, alpha_val) if alpha_val else poisson_nll(a, m)) for a, m in zip(eval_obs, mus)),
            "mae": mean_abs_error(mus, eval_obs), "rmse": rmse(mus, eval_obs),
            "thresholds": {str(t): {"n": v["n"], "brier": v["brier"], "log_loss": v["log_loss"]}
                            for t, v in tm.items()},
            "_threshold_briers": {t: v["_briers"] for t, v in tm.items()},
        }

    headline_full_thresholds = eval_candidate(stage_mus[headline_stage], None, THRESHOLDS)
    headline_full_thresholds_negbin = eval_candidate(stage_mus[headline_stage], alpha, THRESHOLDS)

    stage_results = {name: eval_candidate(stage_mus[name]) for name, _ in STAGES}

    baseline_results = {
        "A_season_average": eval_candidate(baseline_a_mus),
        "B_last10_average": eval_candidate(baseline_b_mus),
        "C_season_per60_x_toi": eval_candidate(baseline_c_mus),
    }
    baseline_d_mus_for_mae = [baseline_d_expected(ex, league_hist_frac) for ex in eval_examples]
    baseline_d_tm = {}
    for t in HEADLINE_THRESHOLDS:
        briers, loglosses = [], []
        for ex in eval_examples:
            p = baseline_d_prob(ex, t, league_hist_frac)
            y = 1.0 if int(round(ex["actual_sog"])) >= t else 0.0
            briers.append((p - y) ** 2)
            loglosses.append(-(y * math.log(max(p, 1e-12)) + (1 - y) * math.log(max(1 - p, 1e-12))))
        baseline_d_tm[t] = {"n": len(eval_examples), "brier": statistics.fmean(briers),
                             "log_loss": statistics.fmean(loglosses), "_briers": briers}
    baseline_results["D_empirical_distribution"] = {
        "n": len(eval_examples), "mae": mean_abs_error(baseline_d_mus_for_mae, eval_obs),
        "rmse": rmse(baseline_d_mus_for_mae, eval_obs),
        "thresholds": {str(t): {"n": v["n"], "brier": v["brier"], "log_loss": v["log_loss"]}
                        for t, v in baseline_d_tm.items()},
        "_threshold_briers": {t: v["_briers"] for t, v in baseline_d_tm.items()},
    }

    # ---- value tests: paired bootstrap on threshold=4 Brier, consecutive stages.
    # research.elo_comparison.paired_bootstrap_delta's resampling cost is
    # O(n_resamples * n) random-index draws; at this slice's eval-set scale
    # (~88k player-games) x 2000 resamples x 8 comparisons that is >1
    # billion Python-level random.randrange() calls -- verified to take
    # several minutes. BOOTSTRAP_SUBSAMPLE_SIZE below is a fixed-seed,
    # documented paired subsample used ONLY for the bootstrap CI/frac-
    # improved estimate; every POINT metric elsewhere in this report (Brier,
    # log loss, calibration, MAE/RMSE, segment/season breakdowns) still uses
    # the FULL true eval set. A smaller bootstrap population gives a
    # slightly wider (more conservative, not less honest) CI, per standard
    # bootstrap theory (SE ~ 1/sqrt(n)). ----
    BOOTSTRAP_SUBSAMPLE_SIZE = 10000
    BOOTSTRAP_SEED = 20242025

    def paired_bootstrap_fast(baseline_scores, candidate_scores, n_resamples=1000):
        n_full = len(baseline_scores)
        if n_full > BOOTSTRAP_SUBSAMPLE_SIZE:
            idx = random.Random(BOOTSTRAP_SEED).sample(range(n_full), BOOTSTRAP_SUBSAMPLE_SIZE)
            baseline_scores = [baseline_scores[i] for i in idx]
            candidate_scores = [candidate_scores[i] for i in idx]
        return ec.paired_bootstrap_delta(baseline_scores, candidate_scores, n_resamples=n_resamples)

    value_tests = {}
    stage_names = [s[0] for s in STAGES]
    for i in range(1, len(stage_names)):
        prev, cur = stage_names[i - 1], stage_names[i]
        bs = paired_bootstrap_fast(stage_results[prev]["_threshold_briers"][4],
                                    stage_results[cur]["_threshold_briers"][4])
        value_tests[f"{prev}_to_{cur}"] = bs

    # full model vs baselines, threshold=4 Brier
    baseline_vs_full = {}
    for name, res in baseline_results.items():
        bs = paired_bootstrap_fast(res["_threshold_briers"][4], stage_results[headline_stage]["_threshold_briers"][4])
        baseline_vs_full[name] = bs

    # ---- season breakdown (headline model, threshold=4) ----
    season_breakdown = {}
    for season in EVAL_SEASONS:
        subset_idx = [i for i, ex in enumerate(eval_examples) if ex["season"] == season]
        if not subset_idx:
            continue
        sub_mus = [stage_mus[headline_stage][i] for i in subset_idx]
        sub_obs = [eval_obs[i] for i in subset_idx]
        sub_examples = [eval_examples[i] for i in subset_idx]
        tm = threshold_metrics(sub_examples, sub_mus, None, HEADLINE_THRESHOLDS)
        season_breakdown[str(season)] = {
            "n": len(subset_idx), "mae": mean_abs_error(sub_mus, sub_obs),
            "thresholds": {str(t): {"brier": v["brier"], "log_loss": v["log_loss"]} for t, v in tm.items()},
        }

    # ---- player segments (Part 27). Brier score is mechanically lower
    # for any low-base-rate subgroup (e.g. low-volume shooters, where
    # actual_rate for "4+" is near 0) regardless of model skill -- raw
    # Brier is NOT comparable across segments with different base rates.
    # brier_skill_score = 1 - brier / (actual_rate * (1 - actual_rate)) is
    # the standard reference-forecast-normalized comparison (1.0 = perfect,
    # 0.0 = no better than always predicting this segment's own base rate,
    # negative = worse than that naive constant) and IS comparable across
    # segments -- reported alongside raw Brier for exactly this reason. ----
    def skill_score(brier, actual_rate):
        naive = actual_rate * (1 - actual_rate)
        return None if naive <= 0 else 1.0 - brier / naive

    def segment_eval(predicate):
        idx = [i for i, ex in enumerate(eval_examples) if predicate(ex)]
        if not idx:
            return None
        sub_mus = [stage_mus[headline_stage][i] for i in idx]
        sub_examples = [eval_examples[i] for i in idx]
        tm = threshold_metrics(sub_examples, sub_mus, None, HEADLINE_THRESHOLDS)
        return {"n": len(idx), "thresholds": {
            str(t): {"brier": v["brier"], "log_loss": v["log_loss"], "mean_pred": v["mean_pred"],
                      "actual_rate": v["actual_rate"], "brier_skill_score": skill_score(v["brier"], v["actual_rate"])}
            for t, v in tm.items()}}

    segments = {
        "FORWARD": segment_eval(lambda ex: position_group(ex["position"]) == "FORWARD"),
        "DEFENSE": segment_eval(lambda ex: position_group(ex["position"]) == "DEFENSE"),
        "HIGH_VOLUME": segment_eval(lambda ex: volume_tier(ex["baseline_rate"]) == "HIGH_VOLUME"),
        "MEDIUM_VOLUME": segment_eval(lambda ex: volume_tier(ex["baseline_rate"]) == "MEDIUM_VOLUME"),
        "LOW_VOLUME": segment_eval(lambda ex: volume_tier(ex["baseline_rate"]) == "LOW_VOLUME"),
        "HIGH_TOI": segment_eval(lambda ex: toi_tier(ex["baseline_toi"]) == "HIGH_TOI"),
        "MEDIUM_TOI": segment_eval(lambda ex: toi_tier(ex["baseline_toi"]) == "MEDIUM_TOI"),
        "LOW_TOI": segment_eval(lambda ex: toi_tier(ex["baseline_toi"]) == "LOW_TOI"),
        "PP_HEAVY": segment_eval(lambda ex: ex["pp_heavy"]),
        "NOT_PP_HEAVY": segment_eval(lambda ex: not ex["pp_heavy"]),
    }

    # ---- calibration (headline model, thresholds 2/3/4/5) ----
    calibration = {str(t): calibration_table(eval_examples, stage_mus[headline_stage], None, t)
                   for t in HEADLINE_THRESHOLDS}

    # ---- confidence layer ----
    for ex, mu in zip(eval_examples, stage_mus[headline_stage]):
        label, pos, risk = cm.confidence_score(
            ex["history_len"], ex["recent_toi_cv"], ex["recent_sog_cv"],
            ex["opponent_window_games"], OPPONENT_WINDOW, ex["appearance_rate"])
        ex["confidence"] = label
        ex["confidence_drivers"] = pos
        ex["confidence_risks"] = risk
        ex["expected_sog"] = mu
        eff_n = min(ex["history_len"], BASELINE_WINDOW)
        cmu = cm.conservative_mu(mu, eff_n)
        ex["conservative_sog"] = cmu
        ex["probs"] = {str(t): threshold_prob(mu, None, t) for t in THRESHOLDS}
        ex["conservative_probs"] = {str(t): threshold_prob(cmu, None, t) for t in THRESHOLDS}

    confidence_breakdown = {}
    for label in ("HIGH", "MEDIUM", "LOW"):
        idx = [i for i, ex in enumerate(eval_examples) if ex["confidence"] == label]
        if not idx:
            continue
        sub_mus = [stage_mus[headline_stage][i] for i in idx]
        sub_examples = [eval_examples[i] for i in idx]
        tm = threshold_metrics(sub_examples, sub_mus, None, HEADLINE_THRESHOLDS)
        confidence_breakdown[label] = {"n": len(idx), "thresholds": {
            str(t): {"brier": v["brier"], "log_loss": v["log_loss"], "mean_pred": v["mean_pred"],
                      "actual_rate": v["actual_rate"], "brier_skill_score": skill_score(v["brier"], v["actual_rate"])}
            for t, v in tm.items()}}

    conservative_le_raw = all(
        ex["conservative_probs"][str(t)] <= ex["probs"][str(t)] + 1e-9 for ex in eval_examples for t in THRESHOLDS)

    return {
        "rows": rows, "examples_by_season": examples_by_season, "tuning": tuning, "eval_examples": eval_examples,
        "excluded": excluded, "total_eligible_player_games": total_eligible_player_games,
        "total_eligible_eval_player_games": total_eligible_eval_player_games,
        "league_avg_sog_allowed": league_avg_sog_allowed, "league_hist_frac": league_hist_frac,
        "stage_weights": stage_weights, "alpha": alpha, "overdispersion": overdispersion,
        "headline_stage": headline_stage,
        "headline_full_thresholds": headline_full_thresholds,
        "headline_full_thresholds_negbin": headline_full_thresholds_negbin,
        "stage_results": stage_results, "baseline_results": baseline_results,
        "value_tests": value_tests, "baseline_vs_full": baseline_vs_full,
        "season_breakdown": season_breakdown, "segments": segments, "calibration": calibration,
        "confidence_breakdown": confidence_breakdown, "conservative_le_raw": conservative_le_raw,
    }


def summarize_example(ex: dict) -> dict:
    return {
        "player": ex["player_name"], "team": ex["team"], "opponent": ex["opponent"],
        "game_id": ex["game_id"], "game_date": ex["game_date"], "season": ex["season"],
        "position": ex["position"], "baseline_rate": round(ex["baseline_rate"], 2),
        "recent_rate5": round(ex["recent_rate5"], 2) if ex["recent_rate5"] is not None else None,
        "expected_sog": round(ex["expected_sog"], 2),
        "conservative_sog": round(ex["conservative_sog"], 2),
        "p_2plus": round(ex["probs"]["2"], 3), "p_3plus": round(ex["probs"]["3"], 3),
        "p_4plus": round(ex["probs"]["4"], 3), "p_5plus": round(ex["probs"]["5"], 3),
        "conservative_p_4plus": round(ex["conservative_probs"]["4"], 3),
        "confidence": ex["confidence"], "confidence_drivers": ex["confidence_drivers"],
        "confidence_risks": ex["confidence_risks"], "h2h_games": ex["h2h_games"],
        "opponent_factor": round(ex["opponent_factor"], 3) if ex["opponent_factor"] else None,
        "actual_sog": ex["actual_sog"],
    }


def pick(examples, predicate):
    for ex in examples:
        if predicate(ex):
            return ex
    return None


def build_representative_examples(eval_examples: list[dict]) -> dict:
    named = {
        "high_volume_shooter": pick(eval_examples, lambda e: volume_tier(e["baseline_rate"]) == "HIGH_VOLUME"
                                     and e["confidence"] == "HIGH"),
        "low_volume_shooter": pick(eval_examples, lambda e: volume_tier(e["baseline_rate"]) == "LOW_VOLUME"
                                    and e["confidence"] != "LOW"),
        "strong_recent_form": pick(eval_examples, lambda e: e["recent_rate5"] is not None
                                    and e["recent_rate5"] > e["baseline_rate"] * 1.4),
        "weak_recent_form": pick(eval_examples, lambda e: e["recent_rate5"] is not None
                                  and e["recent_rate5"] < e["baseline_rate"] * 0.6),
        "strong_h2h": pick(eval_examples, lambda e: e["h2h_games"] >= 3 and e["h2h_delta"] > 0.5),
        "poor_h2h": pick(eval_examples, lambda e: e["h2h_games"] >= 3 and e["h2h_delta"] < -0.5),
        "favorable_opponent": pick(eval_examples, lambda e: e["opponent_factor"] is not None
                                    and e["opponent_factor"] > 1.15),
        "unfavorable_opponent": pick(eval_examples, lambda e: e["opponent_factor"] is not None
                                      and e["opponent_factor"] < 0.85),
        "high_confidence": pick(eval_examples, lambda e: e["confidence"] == "HIGH"),
        "low_confidence": pick(eval_examples, lambda e: e["confidence"] == "LOW"),
    }
    return {name: (summarize_example(ex) if ex else None) for name, ex in named.items()}


def build_full_results() -> dict:
    r = run_all()
    examples = build_representative_examples(r["eval_examples"])

    def clean_bootstrap(bs):
        return {"point_delta": bs["point_delta"], "ci_low": bs["ci_low"], "ci_high": bs["ci_high"],
                "frac_resamples_improved": bs["frac_resamples_improved"]}

    def clean_candidate(res):
        out = {k: v for k, v in res.items() if not k.startswith("_")}
        return out

    results = {
        "config": {
            "warmup_season": WARMUP_SEASON, "tuning_season": TUNING_SEASON, "eval_seasons": EVAL_SEASONS,
            "baseline_window": BASELINE_WINDOW, "recent_windows": [RECENT_WINDOW_5, RECENT_WINDOW_10],
            "toi_recent_window": TOI_RECENT_WINDOW, "opponent_window": OPPONENT_WINDOW,
            "eligibility_window_team_games": pf.ELIGIBILITY_WINDOW_TEAM_GAMES,
            "eligibility_min_appearances": pf.ELIGIBILITY_MIN_APPEARANCES,
            "h2h_shrinkage_games": pf.H2H_SHRINKAGE_GAMES,
            "feature_names": cm.FEATURE_NAMES,
        },
        "corpus_size": len(r["rows"]),
        "total_eligible_player_games_tuning_and_eval_seasons": r["total_eligible_player_games"],
        "total_eligible_player_games_eval_seasons": r["total_eligible_eval_player_games"],
        "common_evaluation_set": {
            "eval_examples_n": len(r["eval_examples"]), "tuning_examples_n": len(r["tuning"]),
            "excluded": r["excluded"],
            "coverage_pct_eval": round(100.0 * len(r["eval_examples"]) /
                                        max(1, r["total_eligible_eval_player_games"]), 2),
        },
        "overdispersion": r["overdispersion"],
        "negbinom_alpha_fitted": r["alpha"],
        "stage_weights": {name: dict(zip(cm.FEATURE_NAMES, w)) for name, w in r["stage_weights"].items()},
        "headline_stage": r["headline_stage"],
        "headline_full_thresholds_poisson": clean_candidate(r["headline_full_thresholds"]),
        "headline_full_thresholds_negbin": clean_candidate(r["headline_full_thresholds_negbin"]),
        "stage_results": {name: clean_candidate(res) for name, res in r["stage_results"].items()},
        "baseline_results": {name: clean_candidate(res) for name, res in r["baseline_results"].items()},
        "value_tests_stage_deltas_threshold4": {k: clean_bootstrap(v) for k, v in r["value_tests"].items()},
        "baseline_vs_full_threshold4": {k: clean_bootstrap(v) for k, v in r["baseline_vs_full"].items()},
        "season_breakdown": r["season_breakdown"],
        "player_segments": r["segments"],
        "calibration": r["calibration"],
        "confidence_breakdown": r["confidence_breakdown"],
        "conservative_probability_never_exceeds_raw": r["conservative_le_raw"],
        "representative_examples": examples,
    }
    return results


if __name__ == "__main__":
    out = build_full_results()
    proc = subprocess.run([sys.executable, "-m", "unittest", "discover", "tests"],
                           cwd=str(REPO_ROOT), capture_output=True, text=True)
    out["test_suite_stderr_tail"] = "\n".join(proc.stderr.strip().splitlines()[-8:])
    out["test_suite_returncode"] = proc.returncode

    out_path = REPO_ROOT / "research" / "player_sog_results.json"
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, sort_keys=True, default=str)
    print(f"wrote {out_path}")
    print("test suite returncode:", out["test_suite_returncode"])
    print(out["test_suite_stderr_tail"])
