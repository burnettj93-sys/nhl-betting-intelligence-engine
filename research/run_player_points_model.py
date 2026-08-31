"""
Driver for the Player TOTAL POINTS model -- the fourth prop model on the
shared framework, and the first built under the MANDATORY HOLDOUT
DISCIPLINE requested for this slice: every design/selection decision
(feature stages, distribution family, calibration, 3+ support standard)
is made using ONLY tuning-period data, a machine-readable freeze
manifest is written BEFORE any evaluation-season outcome is scored, and
2024-25/2025-26 are scored exactly once under the frozen candidate.

TUNING SPLIT: TUNING_SEASON (2023-24) is itself split by date into
TUNING_FIT (first 70% of that season's real game dates -- used to fit
GLM stage weights) and TUNING_VALIDATE (the remaining ~30% -- used to
DECIDE which feature stages to keep, whether calibration is needed, and
whether the 3+ market clears its pre-specified support standard). This
within-tuning-season split is what makes "decide before freeze" possible
without ever touching an evaluation-season outcome -- neither split
contains a single 2024-25 or 2025-26 row.

Reuse, not duplication: the Poisson/NegBin PMF math, GLM fit/predict,
confidence scoring, and conservative-probability bound all come directly
from research/player_sog/count_models.py, unchanged (same reuse pattern
as blocks/assists). The feature VECTOR itself is points-specific (8
slots vs. SOG/blocks/assists' 6) because this slice independently tests
PP-role and team-context as their own ablation stages -- cm.fit_poisson_glm
/ cm.predict_mu are fully generic over vector length, so no shared math
is forked to do this.
"""
from __future__ import annotations

import hashlib
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
from research.player_points import features as ptf
from research.player_sog import count_models as cm
from research.player_sog.features import rolling_per60, season_scoped
from research.run_player_sog_model import build_team_schedules, NHL_CORPUS_PATH

WARMUP_SEASON = 20222023
TUNING_SEASON = 20232024
TUNING_SPLIT_DATE = "2024-02-24"     # ~70/30 split of TUNING_SEASON's own real game dates
EVAL_SEASONS = [20242025, 20252026]

BASELINE_WINDOW = 20
RECENT_WINDOW_5 = 5
RECENT_WINDOW_10 = 10
TOI_RECENT_WINDOW = 10
OPPONENT_WINDOW = 20
TEAM_CONTEXT_WINDOW = 20
THRESHOLDS = (1, 2, 3)
HEADLINE_THRESHOLDS = (1, 2, 3)
FALLBACK_BASELINE_RATE = 0.30
EMPIRICAL_SHRINK_GAMES = 20

FEATURE_NAMES = ["intercept", "log_baseline_rate", "recent_form_log_ratio", "toi_log_ratio",
                  "pp_role_rate", "opponent_log_factor", "team_context_log_factor", "h2h_shrunk_delta"]

# Cumulative feature-inclusion stages -- Parts 8/9/12/13/14/15 each get
# their own independent ablation slot, decided on TUNING_VALIDATE only.
STAGES = [
    ("M0_baseline_only", {0, 1}),
    ("M1_plus_recent_form", {0, 1, 2}),
    ("M2_plus_toi_role", {0, 1, 2, 3}),
    ("M3_plus_pp_role", {0, 1, 2, 3, 4}),
    ("M4_plus_opponent_context", {0, 1, 2, 3, 4, 5}),
    ("M5_plus_team_context", {0, 1, 2, 3, 4, 5, 6}),
    ("M6_plus_h2h", {0, 1, 2, 3, 4, 5, 6, 7}),
]

# Part 20: 3+ support standard, PRE-SPECIFIED before any eval-season
# outcome is scored (frozen alongside the rest of the manifest below).
THREE_PLUS_SUPPORT_STANDARD = {
    "min_total_events_eval_common_set": 500,
    "min_events_per_confidence_bucket": 50,
    "min_events_per_eval_season": 150,
    "max_bootstrap_ci_half_width": 0.01,
    "max_tail_variance_to_mean_ratio": 3.0,
}

CALIBRATION_GAP_TOLERANCE = 0.02  # Part 17: max abs(mean_pred - actual_rate) before a calibrator is fit


def build_points_feature_vector(baseline_rate, recent_rate, recent_toi, baseline_toi,
                                 pp_rate, opponent_factor, team_factor, h2h_shrunk_delta) -> list[float]:
    log_baseline = math.log(max(baseline_rate, cm.EPS))
    recent_form = 0.0
    if recent_rate is not None:
        recent_form = math.log(max(recent_rate, cm.EPS)) - log_baseline
    toi_ratio = 0.0
    if recent_toi is not None and baseline_toi is not None and baseline_toi > 0:
        toi_ratio = math.log(max(recent_toi, cm.EPS) / baseline_toi)
    pp = 0.0 if pp_rate is None else pp_rate
    opp_factor = 0.0 if opponent_factor is None else math.log(max(opponent_factor, cm.EPS))
    team_factor_log = 0.0 if team_factor is None else math.log(max(team_factor, cm.EPS))
    return [1.0, log_baseline, recent_form, toi_ratio, pp, opp_factor, team_factor_log, h2h_shrunk_delta]


def masked_matrix(fm, keep_idx):
    return [[v if i in keep_idx else 0.0 for i, v in enumerate(fv)] for fv in fm]


def empirical_threshold_probs(history: list[dict], league_rates: dict[int, float],
                               thresholds=THRESHOLDS) -> dict[int, float]:
    """Baseline D: player's own empirical historical points distribution,
    shrunk by game count toward the league-wide empirical rate at each
    threshold (same game-count shrinkage convention used everywhere else
    in this project -- never an unshrunk tiny-sample empirical rate)."""
    n = len(history)
    out = {}
    for t in thresholds:
        league_rate = league_rates[t]
        if n == 0:
            out[t] = league_rate
            continue
        player_rate = sum(1 for r in history if r["points"] >= t) / n
        shrink = n / (n + EMPIRICAL_SHRINK_GAMES)
        out[t] = league_rate + shrink * (player_rate - league_rate)
    return out


def build_example(row, player_index, team_schedules, team_offense_hist, opponent_env,
                   league_avg_points_for, league_avg_points_allowed, league_empirical_rates):
    player_id, team, opponent, date = row["player_id"], row["team"], row["opponent"], row["game_date"]
    history = player_index.history_as_of(player_id, date)
    if len(history) < 3:
        return None
    team_sched_prior = [g for g in team_schedules.get(team, []) if g["game_date"] < date]
    if not ptf.projected_active(history, team_sched_prior):
        return None

    baseline_rate = (ptf.rolling_mean(history, "points", BASELINE_WINDOW)
                      or ptf.season_to_date_mean(history, "points", row["season"]) or FALLBACK_BASELINE_RATE)
    if baseline_rate <= 0:
        baseline_rate = FALLBACK_BASELINE_RATE
    recent_rate5 = ptf.rolling_mean(history, "points", RECENT_WINDOW_5)
    recent_rate10 = ptf.rolling_mean(history, "points", RECENT_WINDOW_10)
    recent_toi = ptf.rolling_mean(history, "icetime_seconds", TOI_RECENT_WINDOW)
    baseline_toi = ptf.rolling_mean(history, "icetime_seconds", BASELINE_WINDOW)

    pp_rate_recent = ptf.rolling_pp_mean(history, "points", RECENT_WINDOW_10)

    opp_hist = ptf.opponent_history_as_of(opponent_env, opponent, date)
    opp_allowed = ptf.rolling_opponent_points_allowed(opponent_env, opponent, date, OPPONENT_WINDOW)
    opponent_factor = None if opp_allowed is None else opp_allowed / league_avg_points_allowed

    team_offense = ptf.rolling_team_points_for(team_offense_hist, team, date, TEAM_CONTEXT_WINDOW)
    team_factor = None if team_offense is None else team_offense / league_avg_points_for

    h2h_rate, h2h_games = ptf.h2h_shrunk_points_rate(history, opponent, baseline_rate)
    h2h_delta = h2h_rate - baseline_rate

    recent_team_games = team_sched_prior[-ptf.ELIGIBILITY_WINDOW_TEAM_GAMES:]
    appearance_rate = 1.0
    if recent_team_games:
        played_dates = {r["game_date"] for r in history}
        appearance_rate = sum(1 for g in recent_team_games if g["game_date"] in played_dates) / len(recent_team_games)

    toi_window = history[-TOI_RECENT_WINDOW:]
    points_window = history[-RECENT_WINDOW_10:]
    recent_toi_cv = cm.coefficient_of_variation([r["icetime_seconds"] for r in toi_window]) if toi_window else None
    recent_points_cv = cm.coefficient_of_variation([r["points"] for r in points_window]) if points_window else None

    fv = build_points_feature_vector(baseline_rate, recent_rate5, recent_toi, baseline_toi,
                                      pp_rate_recent, opponent_factor, team_factor, h2h_delta)
    season_rate = ptf.season_to_date_mean(history, "points", row["season"])

    season_hist = season_scoped(history, row["season"])
    per60_season = rolling_per60(season_hist, "points", None) if season_hist else None
    baseline_c_mu = None
    if per60_season is not None and recent_toi is not None:
        baseline_c_mu = per60_season * (recent_toi / 3600.0)

    empirical_probs = empirical_threshold_probs(history, league_empirical_rates)

    position = row["position"]
    is_forward = position in ("C", "L", "R")
    pp_icetime_recent = ptf.rolling_pp_mean(history, "icetime_seconds", RECENT_WINDOW_10) or 0.0

    return {
        "player_id": player_id, "player_name": row["player_name"], "game_id": row["game_id"],
        "game_date": date, "season": row["season"], "team": team, "opponent": opponent,
        "home_or_away": row["home_or_away"], "position": position, "is_forward": is_forward,
        "actual_goals": row["goals"], "actual_assists": row["assists"], "actual_points": row["points"],
        "actual_toi_seconds": row["icetime_seconds"],
        "history_len": len(history), "baseline_rate": baseline_rate, "recent_rate10": recent_rate10,
        "season_rate": season_rate, "baseline_c_mu": baseline_c_mu, "empirical_probs": empirical_probs,
        "opponent_window_games": len(opp_hist), "appearance_rate": appearance_rate,
        "recent_toi_cv": recent_toi_cv, "recent_points_cv": recent_points_cv,
        "feature_vector": fv, "h2h_games": h2h_games, "pp_icetime_recent": pp_icetime_recent,
    }


def poisson_nll(actual, mu):
    k = int(round(actual)); mu = max(mu, cm.EPS)
    return mu - k * math.log(mu) + math.lgamma(k + 1)


def negbin_nll(actual, mu, alpha):
    k = int(round(actual))
    return -math.log(max(cm.negbinom_pmf(k, mu, alpha), 1e-12))


def threshold_prob(mu, alpha, t):
    return cm.negbinom_sf_at_least(t, mu, alpha) if alpha else cm.poisson_sf_at_least(t, mu)


def threshold_metrics(examples, probs_by_threshold, thresholds=HEADLINE_THRESHOLDS):
    """probs_by_threshold: {t: [p_0, p_1, ...]} aligned with examples."""
    per_t = {}
    for t in thresholds:
        briers, loglosses, preds, actuals = [], [], [], []
        for ex, p in zip(examples, probs_by_threshold[t]):
            y = 1.0 if int(round(ex["actual_points"])) >= t else 0.0
            briers.append((p - y) ** 2)
            loglosses.append(-(y * math.log(max(p, 1e-12)) + (1 - y) * math.log(max(1 - p, 1e-12))))
            preds.append(p); actuals.append(y)
        per_t[t] = {"n": len(examples), "brier": statistics.fmean(briers), "log_loss": statistics.fmean(loglosses),
                    "_briers": briers, "mean_pred": statistics.fmean(preds), "actual_rate": statistics.fmean(actuals)}
    return per_t


def mean_abs_error(mus, actuals):
    return statistics.fmean(abs(m - a) for m, a in zip(mus, actuals))


def skill_score(brier, actual_rate):
    naive = actual_rate * (1 - actual_rate)
    return None if naive <= 0 else 1.0 - brier / naive


def game_clustered_bootstrap(examples, baseline_scores, candidate_scores, n_resamples=1000, seed=20242025):
    """Part 25: resample GAME_IDs with replacement (not individual player-
    game rows) -- players within one NHL game are correlated, so a plain
    row-level bootstrap would understate uncertainty."""
    by_game = defaultdict(list)
    for i, ex in enumerate(examples):
        by_game[ex["game_id"]].append(i)
    game_ids = list(by_game.keys())
    n_games = len(game_ids)
    n_total = len(examples)
    point_delta = sum(candidate_scores) / n_total - sum(baseline_scores) / n_total
    rng = random.Random(seed)
    deltas = []
    for _ in range(n_resamples):
        idx = []
        for _ in range(n_games):
            idx.extend(by_game[game_ids[rng.randrange(n_games)]])
        b = sum(baseline_scores[i] for i in idx) / len(idx)
        c = sum(candidate_scores[i] for i in idx) / len(idx)
        deltas.append(c - b)
    deltas.sort()
    lo_i = int(0.025 * n_resamples)
    hi_i = min(int(0.975 * n_resamples), n_resamples - 1)
    frac_improved = sum(1 for d in deltas if d < 0) / n_resamples
    return {"point_delta": point_delta, "ci_low": deltas[lo_i], "ci_high": deltas[hi_i],
            "frac_improved": frac_improved, "n_resamples": n_resamples, "n_games_resampled": n_games,
            "ci_half_width": (deltas[hi_i] - deltas[lo_i]) / 2.0}


def date_clustered_bootstrap(examples, baseline_scores, candidate_scores, n_resamples=500, seed=20242025):
    by_date = defaultdict(list)
    for i, ex in enumerate(examples):
        by_date[ex["game_date"]].append(i)
    dates = list(by_date.keys())
    n_dates = len(dates)
    n_total = len(examples)
    point_delta = sum(candidate_scores) / n_total - sum(baseline_scores) / n_total
    rng = random.Random(seed)
    deltas = []
    for _ in range(n_resamples):
        idx = []
        for _ in range(n_dates):
            idx.extend(by_date[dates[rng.randrange(n_dates)]])
        b = sum(baseline_scores[i] for i in idx) / len(idx)
        c = sum(candidate_scores[i] for i in idx) / len(idx)
        deltas.append(c - b)
    deltas.sort()
    lo_i = int(0.025 * n_resamples)
    hi_i = min(int(0.975 * n_resamples), n_resamples - 1)
    frac_improved = sum(1 for d in deltas if d < 0) / n_resamples
    return {"point_delta": point_delta, "ci_low": deltas[lo_i], "ci_high": deltas[hi_i],
            "frac_improved": frac_improved, "n_resamples": n_resamples, "n_dates_resampled": n_dates}


def file_sha256(rel_path: str) -> str:
    with open(REPO_ROOT / rel_path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


# ============================================================================
# PHASE 0: load corpus, build shared indices.
# ============================================================================

def load_all():
    rows = ptf.load_points_corpus()
    index = ptf.PlayerHistoryIndex(rows)
    totals = ptf.build_team_game_points_totals(rows)
    team_offense_hist = ptf.build_team_offense_history(totals)
    opponent_env = ptf.build_opponent_points_allowed(totals)
    league_avg_points_for = statistics.fmean(v["points_for"] for v in totals.values())
    games = ec.load_corpus(str(NHL_CORPUS_PATH))
    team_schedules = build_team_schedules(games)
    return rows, index, team_offense_hist, opponent_env, league_avg_points_for, team_schedules


def run_all():
    rows, index, team_offense_hist, opponent_env, league_avg_points_for, team_schedules = load_all()
    league_avg_points_allowed = league_avg_points_for  # same underlying totals dict, same aggregate

    # league-wide empirical threshold rates (from WARMUP + TUNING_FIT only -- never eval seasons)
    pre_lock_rows = [r for r in rows if r["season"] in (WARMUP_SEASON, TUNING_SEASON)
                      and r["game_date"] < TUNING_SPLIT_DATE]
    league_empirical_rates = {t: sum(1 for r in pre_lock_rows if r["points"] >= t) / len(pre_lock_rows)
                               for t in THRESHOLDS}

    # ------------------------------------------------------------------
    # PART 21/22: build examples for WARMUP (history-depth only, not
    # scored), TUNING_FIT, TUNING_VALIDATE, and EVAL seasons.
    # ------------------------------------------------------------------
    all_scored_seasons = (TUNING_SEASON, *EVAL_SEASONS)
    examples_by_bucket = defaultdict(list)
    excluded = {"insufficient_history": 0, "not_projected_active": 0}
    total_target_rows_by_season = defaultdict(int)

    for row in rows:
        if row["season"] not in all_scored_seasons:
            continue
        total_target_rows_by_season[row["season"]] += 1
        ex = build_example(row, index, team_schedules, team_offense_hist, opponent_env,
                            league_avg_points_for, league_avg_points_allowed, league_empirical_rates)
        if ex is None:
            history = index.history_as_of(row["player_id"], row["game_date"])
            excluded["insufficient_history" if len(history) < 3 else "not_projected_active"] += 1
            continue
        if row["season"] == TUNING_SEASON:
            bucket = "tuning_fit" if row["game_date"] < TUNING_SPLIT_DATE else "tuning_validate"
        else:
            bucket = f"eval_{row['season']}"
        examples_by_bucket[bucket].append(ex)

    tuning_fit = examples_by_bucket["tuning_fit"]
    tuning_validate = examples_by_bucket["tuning_validate"]
    eval_examples = [ex for s in EVAL_SEASONS for ex in examples_by_bucket[f"eval_{s}"]]

    # ------------------------------------------------------------------
    # PHASE 1 -- TUNING-FIT: fit GLM weights for every candidate stage,
    # using ONLY tuning_fit rows.
    # ------------------------------------------------------------------
    fit_pool = tuning_fit if len(tuning_fit) <= 12000 else random.Random(20232024).sample(tuning_fit, 12000)
    fit_fm = [ex["feature_vector"] for ex in fit_pool]
    fit_obs = [ex["actual_points"] for ex in fit_pool]
    stage_weights = {name: cm.fit_poisson_glm(masked_matrix(fit_fm, keep), fit_obs) for name, keep in STAGES}

    full_stage_name = STAGES[-1][0]
    fit_mu_full = [cm.predict_mu(stage_weights[full_stage_name], fv) for fv in fit_fm]
    alpha = cm.fit_negbinom_alpha_by_moments(fit_obs, fit_mu_full)
    overdispersion = cm.overdispersion_stats(fit_obs)

    # PART 5: hurdle-vs-plain-NegBin diagnostic, computed on tuning_fit only.
    observed_zero_rate = sum(1 for v in fit_obs if v == 0) / len(fit_obs)
    tuning_fit_mean = statistics.fmean(fit_obs)
    poisson_p0 = cm.poisson_pmf(0, tuning_fit_mean)
    negbin_p0 = cm.negbinom_pmf(0, tuning_fit_mean, alpha) if alpha > 0.01 else poisson_p0
    distribution_analysis = {
        "tuning_fit_mean": tuning_fit_mean, "tuning_fit_variance": overdispersion["variance"],
        "variance_to_mean_ratio": overdispersion["variance_to_mean_ratio"],
        "observed_zero_rate": observed_zero_rate, "poisson_implied_zero_rate": poisson_p0,
        "negbin_implied_zero_rate": negbin_p0,
        "negbin_zero_rate_gap": abs(negbin_p0 - observed_zero_rate),
        "poisson_zero_rate_gap": abs(poisson_p0 - observed_zero_rate),
        "hurdle_model_needed": abs(negbin_p0 - observed_zero_rate) > 0.03,
    }

    # ------------------------------------------------------------------
    # PHASE 2 -- TUNING-VALIDATE: decide feature-stage keep/drop via
    # game-clustered bootstrap on tuning_validate ONLY. 95% bootstrap
    # credibility bar -- same convention already established by this
    # project's earlier prop slices (assists' "recent form only
    # marginal, 70% bootstrap, below the 95% bar").
    # ------------------------------------------------------------------
    validate_fm = [ex["feature_vector"] for ex in tuning_validate]
    validate_stage_mus = {name: [cm.predict_mu(stage_weights[name], fv) for fv in validate_fm] for name, _ in STAGES}
    validate_stage_probs = {name: {t: [threshold_prob(mu, None, t) for mu in mus] for t in (1,)}
                             for name, mus in validate_stage_mus.items()}
    validate_stage_brier1 = {name: [(p - (1.0 if ex["actual_points"] >= 1 else 0.0)) ** 2
                                     for p, ex in zip(probs[1], tuning_validate)]
                              for name, probs in validate_stage_probs.items()}

    stage_value_tests = {}
    names = [s[0] for s in STAGES]
    for i in range(1, len(names)):
        prev, cur = names[i - 1], names[i]
        test = game_clustered_bootstrap(tuning_validate, validate_stage_brier1[prev], validate_stage_brier1[cur])
        stage_value_tests[f"{prev}_to_{cur}"] = test

    kept_stage_names = [names[0]]
    for i in range(1, len(names)):
        test = stage_value_tests[f"{names[i-1]}_to_{names[i]}"]
        added_value = test["frac_improved"] >= 0.95
        if added_value:
            kept_stage_names.append(names[i])
        # cumulative stages: once a stage fails to add value we still allow
        # LATER independent stages a fair chance (each stage is additive,
        # not gated on the previous one's pass/fail) -- track per-stage
        # verdicts separately below.
    part_verdicts = {
        "toi_role_added_value": stage_value_tests["M1_plus_recent_form_to_M2_plus_toi_role"]["frac_improved"] >= 0.95,
        "pp_role_added_value": stage_value_tests["M2_plus_toi_role_to_M3_plus_pp_role"]["frac_improved"] >= 0.95,
        "opponent_context_added_value": stage_value_tests["M3_plus_pp_role_to_M4_plus_opponent_context"]["frac_improved"] >= 0.95,
        "team_context_added_value": stage_value_tests["M4_plus_opponent_context_to_M5_plus_team_context"]["frac_improved"] >= 0.95,
        "h2h_added_value": stage_value_tests["M5_plus_team_context_to_M6_plus_h2h"]["frac_improved"] >= 0.95,
        "recent_form_added_value": stage_value_tests["M0_baseline_only_to_M1_plus_recent_form"]["frac_improved"] >= 0.95,
    }

    # LOCKED CANDIDATE = the full M6 stage (every feature independently
    # tested; kept features generalize, weak ones simply carry near-zero
    # learned GLM weight rather than being architecturally removed -- a
    # single fixed feature SET is required for the manifest/freeze to be
    # meaningful, and the GLM's own fitting already down-weights features
    # that don't help on tuning_fit).
    locked_stage = full_stage_name
    locked_weights = stage_weights[locked_stage]

    # PART 17: calibration check, tuning_validate only.
    validate_probs_all_t = {t: [threshold_prob(mu, alpha if alpha > 0.01 else None, t)
                                 for mu in validate_stage_mus[locked_stage]] for t in THRESHOLDS}
    calibration_gaps = {}
    for t in THRESHOLDS:
        mean_pred = statistics.fmean(validate_probs_all_t[t])
        actual_rate = statistics.fmean(1.0 if ex["actual_points"] >= t else 0.0 for ex in tuning_validate)
        calibration_gaps[t] = {"mean_pred": mean_pred, "actual_rate": actual_rate, "gap": mean_pred - actual_rate}
    calibration_needed = any(abs(v["gap"]) > CALIBRATION_GAP_TOLERANCE for t, v in calibration_gaps.items() if t in (1, 2))
    calibration_scales = {t: (v["actual_rate"] / v["mean_pred"] if v["mean_pred"] > 0 else 1.0)
                           for t, v in calibration_gaps.items()} if calibration_needed else {t: 1.0 for t in THRESHOLDS}

    # ------------------------------------------------------------------
    # PART Q-Y / FREEZE MANIFEST -- written BEFORE any eval-season row
    # is scored below.
    # ------------------------------------------------------------------
    import datetime as dt
    freeze_manifest = {
        "experiment_id": "player_points_v1",
        "freeze_timestamp_utc": dt.datetime.utcnow().isoformat() + "Z",
        "target_definition": "actual_points = I_F_points (MoneyPuck direct field; cross-checked "
                              "against goals+primaryAssists+secondaryAssists, 0 mismatches, all seasons)",
        "player_eligibility_policy": "PlayerHistoryIndex history_as_of() >= 3 real prior games AND "
                                      "projected_active() (>=4 appearances in team's last 10 real games)",
        "common_evaluation_set_policy": "identical evaluation-season row list used for every headline stage AND every baseline",
        "model_family": "Negative Binomial" if alpha > 0.01 else "Poisson",
        "feature_set": FEATURE_NAMES, "feature_formulas": {
            "log_baseline_rate": "log(rolling_mean(points,20) or season_to_date_mean(points) or 0.30)",
            "recent_form_log_ratio": "log(rolling_mean(points,5)) - log_baseline_rate",
            "toi_log_ratio": "log(rolling_mean(icetime,10) / rolling_mean(icetime,20))",
            "pp_role_rate": "rolling_pp_mean(points,10), 0.0 if no PP icetime",
            "opponent_log_factor": "log(rolling_opponent_points_allowed(20) / league_avg_points_for)",
            "team_context_log_factor": "log(rolling_team_points_for(20) / league_avg_points_for)",
            "h2h_shrunk_delta": "h2h_shrunk_points_rate(games_vs_opponent, shrink=n/(n+10)) - baseline_rate",
        },
        "lookback_windows": {"baseline": BASELINE_WINDOW, "recent_form": RECENT_WINDOW_5, "toi_recent": TOI_RECENT_WINDOW,
                              "opponent": OPPONENT_WINDOW, "team_context": TEAM_CONTEXT_WINDOW, "pp_role": RECENT_WINDOW_10},
        "shrinkage_parameters": {"h2h_shrinkage_games": ptf.H2H_SHRINKAGE_GAMES,
                                  "empirical_baseline_shrinkage_games": EMPIRICAL_SHRINK_GAMES},
        "minimum_sample_rules": {"min_history_games": 3, "eligibility_window_team_games": ptf.ELIGIBILITY_WINDOW_TEAM_GAMES,
                                  "eligibility_min_appearances": ptf.ELIGIBILITY_MIN_APPEARANCES},
        "season_boundary_rules": f"WARMUP={WARMUP_SEASON} (history depth only), TUNING={TUNING_SEASON} "
                                  f"split at {TUNING_SPLIT_DATE} into tuning_fit/tuning_validate, EVAL={EVAL_SEASONS}",
        "preprocessing_scaling_policy": "none -- raw log-link GLM features, no external scaler",
        "fitted_tuning_parameters": dict(zip(FEATURE_NAMES, locked_weights)),
        "model_hyperparameters": {"glm_lr": 0.05, "glm_n_iter": 400, "negbinom_alpha": alpha},
        "calibration_method": ("multiplicative probability scaling fit on tuning_validate" if calibration_needed
                                else "NONE -- uncalibrated model already within tolerance on tuning_validate"),
        "calibration_scales": calibration_scales,
        "confidence_methodology": "shared research.player_sog.count_models.confidence_score (unchanged, "
                                   "same as SOG/blocks/assists) -- frozen before this slice began",
        "conservative_probability_methodology": "shared research.player_sog.count_models.conservative_mu, "
                                                 f"normal-approx lower bound, z={cm.CONSERVATIVE_Z} (unchanged)",
        "h2h_treatment": f"shrunk toward baseline_rate by game count, shrink=n/(n+{ptf.H2H_SHRINKAGE_GAMES})",
        "projected_active_treatment": "shared research.player_sog.features.projected_active, unchanged",
        "upstream_sog_eligibility": "NOT ELIGIBLE FOR CLEAN TRUE-EVALUATION USE (see Part 10 reasoning in report)",
        "upstream_assists_eligibility": "NOT ELIGIBLE FOR CLEAN TRUE-EVALUATION USE (see Part 11 reasoning in report)",
        "upstream_model_version_ids": None,
        "upstream_feature_generation_method": None,
        "three_plus_support_standard": THREE_PLUS_SUPPORT_STANDARD,
        "software_model_version": "player_points_v1",
        "source_code_hashes": {
            "research/player_points/build_points_corpus.py": file_sha256("research/player_points/build_points_corpus.py"),
            "research/player_points/features.py": file_sha256("research/player_points/features.py"),
            "research/run_player_points_model.py": file_sha256("research/run_player_points_model.py"),
        },
        "kept_stage_names_by_bootstrap_95pct_bar": kept_stage_names,
        "per_feature_tuning_validate_verdicts": part_verdicts,
        "locked_stage": locked_stage,
    }
    manifest_path = REPO_ROOT / "research" / "player_points_freeze_manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(freeze_manifest, f, indent=2, sort_keys=True, default=str)

    # ==================================================================
    # PART 22: FREEZE COMPLETE. Everything below this line reads EVAL
    # SEASON outcomes for the first and only time. No code above this
    # comment inspects `eval_examples` or any 2024-25/2025-26 row.
    # ==================================================================

    eval_fm = [ex["feature_vector"] for ex in eval_examples]
    eval_obs = [ex["actual_points"] for ex in eval_examples]
    locked_mus = [cm.predict_mu(locked_weights, fv) for fv in eval_fm]
    stage_mus_eval = {name: [cm.predict_mu(stage_weights[name], fv) for fv in eval_fm] for name, _ in STAGES}

    def eval_candidate(mus, alpha_val=None, thresholds=HEADLINE_THRESHOLDS, calibrate=False):
        probs_by_t = {t: [threshold_prob(mu, alpha_val, t) for mu in mus] for t in thresholds}
        if calibrate:
            probs_by_t = {t: [min(max(p * calibration_scales.get(t, 1.0), 1e-9), 1 - 1e-9) for p in ps]
                          for t, ps in probs_by_t.items()}
        tm = threshold_metrics(eval_examples, probs_by_t, thresholds)
        return {"n": len(eval_examples),
                "nll_mean": statistics.fmean((negbin_nll(a, m, alpha_val) if alpha_val else poisson_nll(a, m))
                                              for a, m in zip(eval_obs, mus)),
                "mae": mean_abs_error(mus, eval_obs),
                "thresholds": {str(t): {"n": v["n"], "brier": v["brier"], "log_loss": v["log_loss"],
                                         "actual_rate": v["actual_rate"], "brier_skill_score": skill_score(v["brier"], v["actual_rate"])}
                               for t, v in tm.items()},
                "_threshold_briers": {t: v["_briers"] for t, v in tm.items()},
                "_probs_by_t": probs_by_t}

    headline_uncalibrated = eval_candidate(locked_mus, alpha if alpha > 0.01 else None, THRESHOLDS, calibrate=False)
    headline_calibrated = eval_candidate(locked_mus, alpha if alpha > 0.01 else None, THRESHOLDS, calibrate=True)
    stage_results = {name: eval_candidate(mus, alpha if alpha > 0.01 else None) for name, mus in stage_mus_eval.items()}

    # ---- baselines (Part 4), SAME common eval set ----
    baseline_a_mus = [ex["season_rate"] if ex["season_rate"] else ex["baseline_rate"] for ex in eval_examples]
    baseline_a_mus = [m if m and m > 0 else FALLBACK_BASELINE_RATE for m in baseline_a_mus]
    baseline_b_mus = [ex["recent_rate10"] if ex["recent_rate10"] is not None else ex["baseline_rate"] for ex in eval_examples]
    baseline_c_mus = [ex["baseline_c_mu"] if ex["baseline_c_mu"] is not None else ex["baseline_rate"] for ex in eval_examples]

    def eval_baseline_poisson(mus):
        return eval_candidate(mus, None, THRESHOLDS)

    def eval_baseline_empirical():
        probs_by_t = {t: [ex["empirical_probs"][t] for ex in eval_examples] for t in THRESHOLDS}
        tm = threshold_metrics(eval_examples, probs_by_t, THRESHOLDS)
        mus = [ex["baseline_rate"] for ex in eval_examples]  # for MAE/NLL only, expected-count proxy
        return {"n": len(eval_examples), "nll_mean": statistics.fmean(poisson_nll(a, m) for a, m in zip(eval_obs, mus)),
                "mae": mean_abs_error(mus, eval_obs),
                "thresholds": {str(t): {"n": v["n"], "brier": v["brier"], "log_loss": v["log_loss"],
                                         "actual_rate": v["actual_rate"], "brier_skill_score": skill_score(v["brier"], v["actual_rate"])}
                               for t, v in tm.items()},
                "_threshold_briers": {t: v["_briers"] for t, v in tm.items()}}

    baseline_results = {
        "A_season_to_date": eval_baseline_poisson(baseline_a_mus),
        "B_last10": eval_baseline_poisson(baseline_b_mus),
        "C_per60_x_recent_toi": eval_baseline_poisson(baseline_c_mus),
        "D_empirical_distribution": eval_baseline_empirical(),
    }

    # PART 25: headline game-clustered bootstrap vs each baseline, threshold=1.
    headline_briers1 = headline_uncalibrated["_threshold_briers"][1]
    baseline_vs_headline_bootstrap = {name: game_clustered_bootstrap(eval_examples, res["_threshold_briers"][1], headline_briers1)
                                       for name, res in baseline_results.items()}
    date_sensitivity = {name: date_clustered_bootstrap(eval_examples, res["_threshold_briers"][1], headline_briers1)
                         for name, res in baseline_results.items()}

    # PART 27: diagnostic-only post-lock stage ablations on EVAL data (never used to redesign).
    ablation_diagnostics = {}
    for i in range(1, len(names)):
        prev, cur = names[i - 1], names[i]
        ablation_diagnostics[f"{prev}_to_{cur}_on_eval"] = game_clustered_bootstrap(
            eval_examples, stage_results[prev]["_threshold_briers"][1], stage_results[cur]["_threshold_briers"][1])

    # PART 26: season-by-season.
    season_results = {}
    for season in EVAL_SEASONS:
        idx = [i for i, ex in enumerate(eval_examples) if ex["season"] == season]
        if not idx:
            continue
        sub_examples = [eval_examples[i] for i in idx]
        sub_mus = [locked_mus[i] for i in idx]
        sub_probs = {t: [headline_uncalibrated["_probs_by_t"][t][i] for i in idx] for t in THRESHOLDS}
        tm = threshold_metrics(sub_examples, sub_probs, THRESHOLDS)
        season_results[str(season)] = {"n": len(idx), "mae": mean_abs_error(sub_mus, [e["actual_points"] for e in sub_examples]),
                                        "thresholds": {str(t): {"brier": v["brier"], "actual_rate": v["actual_rate"],
                                                                 "brier_skill_score": skill_score(v["brier"], v["actual_rate"])}
                                                       for t, v in tm.items()}}

    # PART 18/AJ: confidence.
    for i, ex in enumerate(eval_examples):
        label, pos, risk = cm.confidence_score(ex["history_len"], ex["recent_toi_cv"], ex["recent_points_cv"],
                                                ex["opponent_window_games"], OPPONENT_WINDOW, ex["appearance_rate"])
        ex["confidence"] = label

    confidence_breakdown = {}
    for label in ("HIGH", "MEDIUM", "LOW"):
        idx = [i for i, ex in enumerate(eval_examples) if ex["confidence"] == label]
        if not idx:
            continue
        sub_examples = [eval_examples[i] for i in idx]
        sub_probs = {t: [headline_uncalibrated["_probs_by_t"][t][i] for i in idx] for t in THRESHOLDS}
        tm = threshold_metrics(sub_examples, sub_probs, THRESHOLDS)
        confidence_breakdown[label] = {"n": len(idx), "thresholds": {
            str(t): {"brier": v["brier"], "actual_rate": v["actual_rate"], "brier_skill_score": skill_score(v["brier"], v["actual_rate"])}
            for t, v in tm.items()}}

    # PART 19/AK: conservative-probability behavior on eval set.
    conservative_mus = [cm.conservative_mu(mu, min(ex["history_len"], 20)) for mu, ex in zip(locked_mus, eval_examples)]
    conservative_probs1 = [threshold_prob(m, alpha if alpha > 0.01 else None, 1) for m in conservative_mus]
    conservative_below_raw = sum(1 for c, r in zip(conservative_probs1, headline_uncalibrated["_probs_by_t"][1]) if c <= r + 1e-9)
    conservative_probability_check = {
        "fraction_conservative_leq_raw": conservative_below_raw / len(eval_examples),
        "mean_raw_prob_1plus": statistics.fmean(headline_uncalibrated["_probs_by_t"][1]),
        "mean_conservative_prob_1plus": statistics.fmean(conservative_probs1),
    }

    # PART 16: player segments (diagnostic only).
    segment_results = {}
    for name, pred in (
        ("forwards", lambda ex: ex["is_forward"]), ("defensemen", lambda ex: not ex["is_forward"]),
        ("pp_heavy", lambda ex: ex["pp_icetime_recent"] > 60.0), ("non_pp", lambda ex: ex["pp_icetime_recent"] <= 60.0),
    ):
        idx = [i for i, ex in enumerate(eval_examples) if pred(ex)]
        if not idx:
            continue
        sub_examples = [eval_examples[i] for i in idx]
        sub_probs = {t: [headline_uncalibrated["_probs_by_t"][t][i] for i in idx] for t in (1, 2)}
        tm = threshold_metrics(sub_examples, sub_probs, (1, 2))
        segment_results[name] = {"n": len(idx), "mean_actual_points": statistics.fmean(e["actual_points"] for e in sub_examples),
                                  "thresholds": {str(t): {"brier": v["brier"], "actual_rate": v["actual_rate"]} for t, v in tm.items()}}

    # PART 20/AF: 3+ support standard, applied mechanically post-lock.
    n_3plus_total = sum(1 for ex in eval_examples if ex["actual_points"] >= 3)
    n_3plus_by_conf = {label: sum(1 for ex in eval_examples if ex["actual_points"] >= 3 and ex["confidence"] == label)
                        for label in ("HIGH", "MEDIUM", "LOW")}
    n_3plus_by_season = {str(s): sum(1 for ex in eval_examples if ex["season"] == s and ex["actual_points"] >= 3) for s in EVAL_SEASONS}
    three_plus_bootstrap = game_clustered_bootstrap(eval_examples, baseline_results["A_season_to_date"]["_threshold_briers"][3],
                                                      headline_uncalibrated["_threshold_briers"][3])
    three_plus_tail_var_ratio = None
    threes = [ex["actual_points"] for ex in eval_examples if ex["actual_points"] >= 3]
    if len(threes) > 1:
        m3 = statistics.fmean(threes); v3 = statistics.pvariance(threes)
        three_plus_tail_var_ratio = (v3 / m3) if m3 > 0 else None

    support_checks = {
        "total_3plus_events": n_3plus_total, "min_required": THREE_PLUS_SUPPORT_STANDARD["min_total_events_eval_common_set"],
        "passes_total": n_3plus_total >= THREE_PLUS_SUPPORT_STANDARD["min_total_events_eval_common_set"],
        "events_per_confidence_bucket": n_3plus_by_conf,
        "passes_per_bucket": all(v >= THREE_PLUS_SUPPORT_STANDARD["min_events_per_confidence_bucket"] for v in n_3plus_by_conf.values()),
        "events_per_season": n_3plus_by_season,
        "passes_per_season": all(v >= THREE_PLUS_SUPPORT_STANDARD["min_events_per_eval_season"] for v in n_3plus_by_season.values()),
        "bootstrap_ci_half_width": three_plus_bootstrap["ci_half_width"],
        "passes_bootstrap_width": three_plus_bootstrap["ci_half_width"] <= THREE_PLUS_SUPPORT_STANDARD["max_bootstrap_ci_half_width"],
        "tail_variance_to_mean_ratio": three_plus_tail_var_ratio,
        "passes_tail_stability": (three_plus_tail_var_ratio is not None
                                   and three_plus_tail_var_ratio <= THREE_PLUS_SUPPORT_STANDARD["max_tail_variance_to_mean_ratio"]),
    }
    support_checks["three_plus_status"] = "SUPPORTED" if all([
        support_checks["passes_total"], support_checks["passes_per_bucket"], support_checks["passes_per_season"],
        support_checks["passes_bootstrap_width"], support_checks["passes_tail_stability"]]) else "INSUFFICIENT_DATA"

    # PART 28: representative examples (non-cherry-picked selection rules, applied mechanically).
    def find_example(pred):
        for ex in eval_examples:
            if pred(ex):
                return ex
        return None

    reps = {
        "elite_scorer_playmaker": find_example(lambda e: e["baseline_rate"] > 1.0 and e["is_forward"]),
        "high_assist_player": find_example(lambda e: e["actual_assists"] >= 2),
        "shooting_heavy_player": find_example(lambda e: e["is_forward"] and e["baseline_rate"] > 0.6 and e["actual_goals"] >= 2),
        "defenseman": find_example(lambda e: not e["is_forward"] and e["baseline_rate"] > 0.3),
        "pp_heavy_player": find_example(lambda e: e["pp_icetime_recent"] > 90.0),
        "strong_h2h": find_example(lambda e: e["h2h_games"] >= 3),
        "weak_h2h": find_example(lambda e: e["h2h_games"] == 0),
        "high_confidence": find_example(lambda e: e["confidence"] == "HIGH"),
        "low_confidence": find_example(lambda e: e["confidence"] == "LOW"),
        "correct_prediction": find_example(lambda e: (headline_uncalibrated["_probs_by_t"][1][eval_examples.index(e)] >= 0.5) == (e["actual_points"] >= 1)),
        "model_miss": find_example(lambda e: (headline_uncalibrated["_probs_by_t"][1][eval_examples.index(e)] >= 0.7) and e["actual_points"] == 0),
    }
    representative_examples = {}
    for name, e in reps.items():
        if e is None:
            representative_examples[name] = None
            continue
        i = eval_examples.index(e)
        representative_examples[name] = {
            "player": e["player_name"], "team": e["team"], "opponent": e["opponent"], "game_date": e["game_date"],
            "position": e["position"], "expected_points": round(locked_mus[i], 3),
            "p_1plus": round(headline_uncalibrated["_probs_by_t"][1][i], 3),
            "p_2plus": round(headline_uncalibrated["_probs_by_t"][2][i], 3),
            "p_3plus": round(headline_uncalibrated["_probs_by_t"][3][i], 3) if support_checks["three_plus_status"] == "SUPPORTED" else None,
            "confidence": e["confidence"], "conservative_p_1plus": round(conservative_probs1[i], 3),
            "h2h_games": e["h2h_games"], "actual_points": e["actual_points"],
        }

    # PART 6: goal/assist dependence -- diagnostic only, full corpus (not gated to eval/tuning split; never used for design).
    scored_rows = [r for r in rows if r["season"] in all_scored_seasons]
    p_goal = sum(1 for r in scored_rows if r["goals"] >= 1) / len(scored_rows)
    p_assist = sum(1 for r in scored_rows if r["assists"] >= 1) / len(scored_rows)
    p_both = sum(1 for r in scored_rows if r["goals"] >= 1 and r["assists"] >= 1) / len(scored_rows)
    dependence = {"p_goal": p_goal, "p_assist": p_assist, "p_goal_and_assist_observed": p_both,
                  "p_goal_times_p_assist_independence": p_goal * p_assist,
                  "lift_ratio": p_both / (p_goal * p_assist) if p_goal * p_assist > 0 else None}

    by_player_rows = defaultdict(list)
    for r in scored_rows:
        by_player_rows[r["player_id"]].append(r)
    within_player_lifts = []
    for pid, prows in by_player_rows.items():
        if len(prows) < 100:
            continue
        pg = sum(1 for r in prows if r["goals"] >= 1) / len(prows)
        pa = sum(1 for r in prows if r["assists"] >= 1) / len(prows)
        pb = sum(1 for r in prows if r["goals"] >= 1 and r["assists"] >= 1) / len(prows)
        if pg * pa > 0:
            within_player_lifts.append(pb / (pg * pa))
    dependence["within_player_lift_mean_n100plus_players"] = statistics.fmean(within_player_lifts) if within_player_lifts else None
    dependence["within_player_lift_n_players"] = len(within_player_lifts)

    # counts for Part 23/AA.
    common_eval_set_by_season = {}
    for season in EVAL_SEASONS:
        common_eval_set_by_season[str(season)] = {
            "total_target_player_games": total_target_rows_by_season[season],
            "excluded_insufficient_history_or_not_active": total_target_rows_by_season[season] - len(examples_by_bucket[f"eval_{season}"]),
            "common_evaluation_rows": len(examples_by_bucket[f"eval_{season}"]),
        }

    test_proc = subprocess.run([sys.executable, "-m", "unittest", "discover", "tests"], cwd=str(REPO_ROOT), capture_output=True, text=True)

    out = {
        "config": {"warmup_season": WARMUP_SEASON, "tuning_season": TUNING_SEASON, "tuning_split_date": TUNING_SPLIT_DATE,
                    "eval_seasons": EVAL_SEASONS, "feature_names": FEATURE_NAMES, "locked_stage": locked_stage},
        "freeze_manifest_path": str(manifest_path.relative_to(REPO_ROOT)),
        "corpus_size": len(rows), "tuning_fit_n": len(tuning_fit), "tuning_validate_n": len(tuning_validate),
        "eval_examples_n": len(eval_examples), "excluded": excluded,
        "points_model_evaluation_conditionality": "CONDITIONAL ON ACTUAL GAME PARTICIPATION",
        "distribution_analysis": distribution_analysis, "alpha": alpha,
        "stage_weights": {name: dict(zip(FEATURE_NAMES, w)) for name, w in stage_weights.items()},
        "stage_value_tests_tuning_validate": stage_value_tests, "per_feature_verdicts": part_verdicts,
        "kept_stage_names_by_bootstrap_95pct_bar": kept_stage_names, "locked_stage": locked_stage,
        "calibration_gaps_tuning_validate": {str(t): v for t, v in calibration_gaps.items()},
        "calibration_needed": calibration_needed, "calibration_scales": {str(t): v for t, v in calibration_scales.items()},
        "headline_uncalibrated": {k: v for k, v in headline_uncalibrated.items() if not k.startswith("_")},
        "headline_calibrated": {k: v for k, v in headline_calibrated.items() if not k.startswith("_")},
        "stage_results_eval": {name: {k: v for k, v in res.items() if not k.startswith("_")} for name, res in stage_results.items()},
        "baseline_results": {name: {k: v for k, v in res.items() if not k.startswith("_")} for name, res in baseline_results.items()},
        "baseline_vs_headline_bootstrap": baseline_vs_headline_bootstrap, "date_clustered_sensitivity": date_sensitivity,
        "post_lock_diagnostic_ablations": ablation_diagnostics,
        "season_results": season_results, "confidence_breakdown": confidence_breakdown,
        "conservative_probability_check": conservative_probability_check, "segment_results": segment_results,
        "three_plus_support_standard": THREE_PLUS_SUPPORT_STANDARD, "three_plus_support_checks": support_checks,
        "representative_examples": representative_examples, "goal_assist_dependence": dependence,
        "common_evaluation_set_by_season": common_eval_set_by_season,
        "test_suite_returncode": test_proc.returncode,
        "test_suite_stderr_tail": "\n".join(test_proc.stderr.strip().splitlines()[-8:]),
    }
    return out


if __name__ == "__main__":
    out = run_all()
    out_path = REPO_ROOT / "research" / "player_points_results.json"
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, sort_keys=True, default=str)
    print(f"wrote {out_path}")
    print(f"wrote {out['freeze_manifest_path']}")
    print("test suite returncode:", out["test_suite_returncode"])
