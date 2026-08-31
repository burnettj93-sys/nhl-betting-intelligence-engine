"""
Driver for the Player GOALS / ANYTIME GOAL model -- the fifth prop model
this session, built directly on the lesson from Points: test a player-
specific hierarchical empirical baseline as a serious candidate from the
start (Part 9), rather than defaulting to a feature-regression GLM and
discovering later that it underperforms.

Tuning/lock/freeze discipline, mirroring research/run_player_points_model.py's
Cycle-1 structure (this is Goals' FIRST validation cycle, not a redesign,
so a single clean tuning/eval split is appropriate -- Part 29's own
preferred structure): TUNING_SEASON (2023-24) split by date into
TUNING_FIT / TUNING_VALIDATE for feature-stage and candidate-family
selection; EVAL_SEASONS (2024-25, 2025-26) scored ONCE after the freeze
manifest is written.

Six candidates (Part 10's own list, kept complete since the prompt named
all six explicitly):
  A -- player empirical event probability, PLAYER->ROLE->LEAGUE
       hierarchical shrinkage (research.player_goals.hierarchy),
       nonparametric threshold reads.
  B -- logistic regression directly on P(1+) (reuses
       research.xg_model_comparison.sigmoid/fit_logistic_weights
       UNCHANGED -- an existing, already-tested plain-gradient-descent
       utility from an earlier, unrelated experiment).
  C -- Poisson count GLM (reuses research.player_sog.count_models.
       fit_poisson_glm/predict_mu UNCHANGED, same as every other prop).
  D -- Negative Binomial, only reported if real tuning-fit overdispersion
       is found (alpha > 0.01).
  E -- candidate A's hierarchical mean as a FIXED OFFSET + a context
       adjustment fit via the same offset-GLM technique introduced in
       research/player_points/redesign.py (rewritten here, not imported,
       matching this project's per-prop-package convention).
  F -- shot-generation x conversion: mu = recent_sog_rate (PIT-safe,
       player's own rolling SOG -- NOT the validated SOG model's
       prediction) x shrunk career shooting percentage. Purely
       mechanistic, no fitting at all.
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
from research.player_goals import features as gf
from research.player_goals import hierarchy as gh
from research.player_sog import count_models as cm
from research.xg_model_comparison import sigmoid, fit_logistic_weights
from research.run_player_sog_model import build_team_schedules, NHL_CORPUS_PATH

WARMUP_SEASON = 20222023
TUNING_SEASON = 20232024
EVAL_SEASONS = [20242025, 20252026]
BASELINE_WINDOW = 20
RECENT_WINDOW_5 = 5
RECENT_WINDOW_10 = 10
TOI_RECENT_WINDOW = 10
OPPONENT_WINDOW = 20
TEAM_CONTEXT_WINDOW = 20
THRESHOLDS = (1, 2)
FALLBACK_BASELINE_RATE = 0.10
EMPIRICAL_SHRINK_GAMES = 20
K_PLAYER_GRID = (15, 30, 50)
SHOOTING_SHRINK_GRID = (75, 150, 250)

FEATURE_NAMES = ["intercept", "log_baseline_rate", "recent_form_log_ratio", "toi_log_ratio",
                  "pp_role_rate", "shot_volume_log_ratio", "shrunk_shooting_pct_log",
                  "opponent_log_factor", "team_context_log_factor", "h2h_sog_delta", "h2h_goals_delta"]

STAGES = [
    ("M0_baseline_only", {0, 1}),
    ("M1_plus_recent_form", {0, 1, 2}),
    ("M2_plus_toi", {0, 1, 2, 3}),
    ("M3_plus_pp_role", {0, 1, 2, 3, 4}),
    ("M4_plus_shot_volume", {0, 1, 2, 3, 4, 5}),
    ("M5_plus_shooting_talent", {0, 1, 2, 3, 4, 5, 6}),
    ("M6_plus_opponent", {0, 1, 2, 3, 4, 5, 6, 7}),
    ("M7_plus_team_context", {0, 1, 2, 3, 4, 5, 6, 7, 8}),
    ("M8_plus_h2h_sog", {0, 1, 2, 3, 4, 5, 6, 7, 8, 9}),
    ("M9_plus_h2h_goals", {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10}),
]

TWO_PLUS_SUPPORT_STANDARD = {
    "min_total_events_eval_common_set": 300,
    "min_events_per_confidence_bucket": 30,
    "min_events_per_eval_season": 100,
    "max_bootstrap_ci_half_width": 0.01,
}


def file_sha256(rel_path: str) -> str:
    with open(REPO_ROOT / rel_path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


def build_feature_vector(baseline_rate, recent_rate, recent_toi, baseline_toi, pp_rate,
                          recent_sog_rate, baseline_sog_rate, shrunk_shooting_pct, league_shooting_pct,
                          opponent_factor, team_factor, h2h_sog_delta, h2h_goals_delta) -> list[float]:
    log_baseline = math.log(max(baseline_rate, cm.EPS))
    recent_form = 0.0
    if recent_rate is not None:
        recent_form = math.log(max(recent_rate, cm.EPS)) - log_baseline
    toi_ratio = 0.0
    if recent_toi is not None and baseline_toi is not None and baseline_toi > 0:
        toi_ratio = math.log(max(recent_toi, cm.EPS) / baseline_toi)
    pp = 0.0 if pp_rate is None else pp_rate
    shot_volume_ratio = 0.0
    if recent_sog_rate is not None and baseline_sog_rate is not None and baseline_sog_rate > 0:
        shot_volume_ratio = math.log(max(recent_sog_rate, cm.EPS) / baseline_sog_rate)
    shooting_log = math.log(max(shrunk_shooting_pct, cm.EPS) / max(league_shooting_pct, cm.EPS))
    opp_factor = 0.0 if opponent_factor is None else math.log(max(opponent_factor, cm.EPS))
    team_factor_log = 0.0 if team_factor is None else math.log(max(team_factor, cm.EPS))
    return [1.0, log_baseline, recent_form, toi_ratio, pp, shot_volume_ratio, shooting_log,
            opp_factor, team_factor_log, h2h_sog_delta, h2h_goals_delta]


def masked_matrix(fm, keep_idx):
    return [[v if i in keep_idx else 0.0 for i, v in enumerate(fv)] for fv in fm]


def fit_poisson_glm_with_offset(feature_matrix, observed, offsets, lr=0.05, n_iter=400):
    n = len(observed); k = len(feature_matrix[0])
    weights = [0.0] * k
    for _ in range(n_iter):
        grad = [0.0] * k
        for fv, y, off in zip(feature_matrix, observed, offsets):
            z = min(off + sum(w * f for w, f in zip(weights, fv)), 30.0)
            mu = math.exp(z)
            err = mu - y
            for j in range(k):
                grad[j] += err * fv[j]
        for j in range(k):
            weights[j] -= lr * grad[j] / n
    return weights


def predict_mu_with_offset(weights, fv, offset):
    z = offset + sum(w * f for w, f in zip(weights, fv))
    return math.exp(min(z, 30.0))


def empirical_threshold_probs(history, league_rates, thresholds=THRESHOLDS):
    n = len(history)
    out = {}
    for t in thresholds:
        league_rate = league_rates[t]
        if n == 0:
            out[t] = league_rate
            continue
        player_rate = sum(1 for r in history if r["goals"] >= t) / n
        shrink = n / (n + EMPIRICAL_SHRINK_GAMES)
        out[t] = league_rate + shrink * (player_rate - league_rate)
    return out


def build_example(row, player_index, team_schedules, team_offense_hist, opponent_env,
                   league_avg_goals_for, league_shooting_pct, league_empirical_rates):
    player_id, team, opponent, date = row["player_id"], row["team"], row["opponent"], row["game_date"]
    history = player_index.history_as_of(player_id, date)
    if len(history) < 3:
        return None
    team_sched_prior = [g for g in team_schedules.get(team, []) if g["game_date"] < date]
    if not gf.projected_active(history, team_sched_prior):
        return None

    baseline_rate = (gf.rolling_mean(history, "goals", BASELINE_WINDOW)
                      or gf.season_to_date_mean(history, "goals", row["season"]) or FALLBACK_BASELINE_RATE)
    if baseline_rate <= 0:
        baseline_rate = FALLBACK_BASELINE_RATE
    recent_rate5 = gf.rolling_mean(history, "goals", RECENT_WINDOW_5)
    recent_toi = gf.rolling_mean(history, "icetime_seconds", TOI_RECENT_WINDOW)
    baseline_toi = gf.rolling_mean(history, "icetime_seconds", BASELINE_WINDOW)
    pp_rate_recent = gf.rolling_pp_mean(history, "goals", RECENT_WINDOW_10)

    recent_sog_rate = gf.rolling_mean(history, "sog", RECENT_WINDOW_10)
    baseline_sog_rate = gf.rolling_mean(history, "sog", BASELINE_WINDOW)
    shrunk_shooting_pct, career_shots = gf.career_shooting_pct_shrunk(history, league_shooting_pct)

    opp_allowed = gf.rolling_opponent_goals_allowed(opponent_env, opponent, date, OPPONENT_WINDOW)
    opponent_factor = None if opp_allowed is None else opp_allowed / league_avg_goals_for
    team_offense = gf.rolling_team_goals_for(team_offense_hist, team, date, TEAM_CONTEXT_WINDOW)
    team_factor = None if team_offense is None else team_offense / league_avg_goals_for

    h2h_sog_rate, h2h_sog_games = gf.h2h_shrunk_sog_rate(history, opponent, baseline_sog_rate or FALLBACK_BASELINE_RATE)
    h2h_sog_delta = h2h_sog_rate - (baseline_sog_rate or FALLBACK_BASELINE_RATE)
    h2h_goals_rate, h2h_goals_games = gf.h2h_shrunk_goals_rate(history, opponent, baseline_rate)
    h2h_goals_delta = h2h_goals_rate - baseline_rate

    fv = build_feature_vector(baseline_rate, recent_rate5, recent_toi, baseline_toi, pp_rate_recent,
                               recent_sog_rate, baseline_sog_rate, shrunk_shooting_pct, league_shooting_pct,
                               opponent_factor, team_factor, h2h_sog_delta, h2h_goals_delta)

    is_forward = row["position"] in gh.FORWARD_POSITIONS
    pp_icetime_recent = gf.rolling_pp_mean(history, "icetime_seconds", RECENT_WINDOW_10) or 0.0
    role = gh.target_role_tag(is_forward, pp_icetime_recent)

    recent_team_games = team_sched_prior[-gf.ELIGIBILITY_WINDOW_TEAM_GAMES:]
    appearance_rate = 1.0
    if recent_team_games:
        played_dates = {r["game_date"] for r in history}
        appearance_rate = sum(1 for g in recent_team_games if g["game_date"] in played_dates) / len(recent_team_games)

    toi_window = history[-TOI_RECENT_WINDOW:]
    goals_window = history[-RECENT_WINDOW_10:]
    recent_toi_cv = cm.coefficient_of_variation([r["icetime_seconds"] for r in toi_window]) if toi_window else None
    recent_goals_cv = cm.coefficient_of_variation([r["goals"] for r in goals_window]) if goals_window else None

    empirical_probs = empirical_threshold_probs(history, league_empirical_rates)

    return {
        "player_id": player_id, "player_name": row["player_name"], "game_id": row["game_id"],
        "game_date": date, "season": row["season"], "team": team, "opponent": opponent,
        "position": row["position"], "is_forward": is_forward, "role": role,
        "actual_goals": row["goals"], "actual_sog": row["sog"],
        "history_len": len(history), "baseline_rate": baseline_rate, "recent_sog_rate": recent_sog_rate,
        "shrunk_shooting_pct": shrunk_shooting_pct, "career_shots": career_shots,
        "raw_shooting_pct": (sum(r["goals"] for r in history) / sum(r["sog"] for r in history)
                              if sum(r["sog"] for r in history) > 0 else None),
        "opponent_window_games": len(gf.opponent_history_as_of(opponent_env, opponent, date)),
        "appearance_rate": appearance_rate, "recent_toi_cv": recent_toi_cv, "recent_goals_cv": recent_goals_cv,
        "feature_vector": fv, "h2h_sog_games": h2h_sog_games, "h2h_goals_games": h2h_goals_games,
        "pp_icetime_recent": pp_icetime_recent, "empirical_probs": empirical_probs,
    }


def threshold_prob(mu, alpha, t):
    return cm.negbinom_sf_at_least(t, mu, alpha) if alpha else cm.poisson_sf_at_least(t, mu)


def threshold_metrics(examples, probs_by_threshold, thresholds=THRESHOLDS):
    per_t = {}
    for t in thresholds:
        briers, loglosses, preds, actuals = [], [], [], []
        for ex, p in zip(examples, probs_by_threshold[t]):
            y = 1.0 if ex["actual_goals"] >= t else 0.0
            briers.append((p - y) ** 2)
            loglosses.append(-(y * math.log(max(p, 1e-12)) + (1 - y) * math.log(max(1 - p, 1e-12))))
            preds.append(p); actuals.append(y)
        per_t[t] = {"n": len(examples), "brier": statistics.fmean(briers), "log_loss": statistics.fmean(loglosses),
                    "_briers": briers, "mean_pred": statistics.fmean(preds), "actual_rate": statistics.fmean(actuals)}
    return per_t


def skill_score(brier, actual_rate):
    naive = actual_rate * (1 - actual_rate)
    return None if naive <= 0 else 1.0 - brier / naive


def game_clustered_bootstrap(examples, baseline_scores, candidate_scores, n_resamples=1000, seed=20242025):
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
    lo_i = int(0.025 * n_resamples); hi_i = min(int(0.975 * n_resamples), n_resamples - 1)
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
    lo_i = int(0.025 * n_resamples); hi_i = min(int(0.975 * n_resamples), n_resamples - 1)
    frac_improved = sum(1 for d in deltas if d < 0) / n_resamples
    return {"point_delta": point_delta, "ci_low": deltas[lo_i], "ci_high": deltas[hi_i],
            "frac_improved": frac_improved, "n_resamples": n_resamples, "n_dates_resampled": n_dates}


# ============================================================================
# PHASE 0: load corpus, build shared indices.
# ============================================================================

def load_all():
    rows = gf.load_goals_corpus()
    index = gf.PlayerHistoryIndex(rows)
    totals = gf.build_team_game_goals_totals(rows)
    team_offense_hist = gf.build_team_offense_history(totals)
    opponent_env = gf.build_opponent_goals_allowed(totals)
    league_avg_goals_for = statistics.fmean(v["goals_for"] for v in totals.values())
    all_sog = sum(r["sog"] for r in rows)
    league_shooting_pct = sum(r["goals"] for r in rows) / all_sog if all_sog > 0 else 0.09
    games = ec.load_corpus(str(NHL_CORPUS_PATH))
    team_schedules = build_team_schedules(games)
    return rows, index, team_offense_hist, opponent_env, league_avg_goals_for, league_shooting_pct, team_schedules


def run_all():
    rows, index, team_offense_hist, opponent_env, league_avg_goals_for, league_shooting_pct, team_schedules = load_all()

    dev_dates = sorted({r["game_date"] for r in rows if r["season"] == TUNING_SEASON})
    tuning_split_date = dev_dates[int(len(dev_dates) * 0.7)]

    pre_lock_rows = [r for r in rows if r["season"] in (WARMUP_SEASON, TUNING_SEASON) and r["game_date"] < tuning_split_date]
    league_empirical_rates = {t: sum(1 for r in pre_lock_rows if r["goals"] >= t) / len(pre_lock_rows) for t in THRESHOLDS}

    all_scored_seasons = (TUNING_SEASON, *EVAL_SEASONS)
    examples_by_bucket = defaultdict(list)
    rows_by_bucket = defaultdict(list)  # raw corpus rows (not build_example() output) -- what
                                          # gh.RoleLeagueRates needs, keyed the SAME way as examples_by_bucket
    excluded = {"insufficient_history": 0, "not_projected_active": 0}
    total_target_rows_by_season = defaultdict(int)

    for row in rows:
        if row["season"] not in all_scored_seasons:
            continue
        total_target_rows_by_season[row["season"]] += 1
        if row["season"] == TUNING_SEASON:
            bucket = "tuning_fit" if row["game_date"] < tuning_split_date else "tuning_validate"
        else:
            bucket = f"eval_{row['season']}"
        rows_by_bucket[bucket].append(row)

        ex = build_example(row, index, team_schedules, team_offense_hist, opponent_env,
                            league_avg_goals_for, league_shooting_pct, league_empirical_rates)
        if ex is None:
            history = index.history_as_of(row["player_id"], row["game_date"])
            excluded["insufficient_history" if len(history) < 3 else "not_projected_active"] += 1
            continue
        examples_by_bucket[bucket].append(ex)

    tuning_fit = examples_by_bucket["tuning_fit"]
    tuning_validate = examples_by_bucket["tuning_validate"]
    tuning_fit_rows = rows_by_bucket["tuning_fit"]
    tuning_validate_rows = rows_by_bucket["tuning_validate"]
    eval_examples = [ex for s in EVAL_SEASONS for ex in examples_by_bucket[f"eval_{s}"]]

    # ------------------------------------------------------------------
    # PHASE 1 -- fit GLM (candidate C) + logistic (candidate B) stage
    # weights on tuning_fit only.
    # ------------------------------------------------------------------
    fit_pool = tuning_fit if len(tuning_fit) <= 12000 else random.Random(20232024).sample(tuning_fit, 12000)
    fit_fm = [ex["feature_vector"] for ex in fit_pool]
    fit_obs = [ex["actual_goals"] for ex in fit_pool]
    fit_bin1 = [1.0 if ex["actual_goals"] >= 1 else 0.0 for ex in fit_pool]

    stage_weights_c = {name: cm.fit_poisson_glm(masked_matrix(fit_fm, keep), fit_obs) for name, keep in STAGES}
    stage_weights_b = {name: fit_logistic_weights([0.0] * len(fit_pool), masked_matrix(fit_fm, keep), fit_bin1)
                       for name, keep in STAGES}

    full_stage_name = STAGES[-1][0]
    fit_mu_full = [cm.predict_mu(stage_weights_c[full_stage_name], fv) for fv in fit_fm]
    alpha = cm.fit_negbinom_alpha_by_moments(fit_obs, fit_mu_full)
    overdispersion = cm.overdispersion_stats(fit_obs)

    observed_zero_rate = sum(1 for v in fit_obs if v == 0) / len(fit_obs)
    tuning_fit_mean = statistics.fmean(fit_obs)
    poisson_p0 = cm.poisson_pmf(0, tuning_fit_mean)
    negbin_p0 = cm.negbinom_pmf(0, tuning_fit_mean, alpha) if alpha > 0.01 else poisson_p0
    distribution_analysis = {
        "tuning_fit_mean": tuning_fit_mean, "tuning_fit_variance": overdispersion["variance"],
        "variance_to_mean_ratio": overdispersion["variance_to_mean_ratio"], "observed_zero_rate": observed_zero_rate,
        "poisson_implied_zero_rate": poisson_p0, "negbin_implied_zero_rate": negbin_p0,
        "hurdle_model_needed": abs(negbin_p0 - observed_zero_rate) > 0.03,
    }

    # ------------------------------------------------------------------
    # PHASE 2 -- TUNING-VALIDATE: decide k_player/shooting-shrink grid,
    # and feature-stage keep/drop, via game-clustered bootstrap, 95% bar.
    # ------------------------------------------------------------------
    rates_dev = gh.RoleLeagueRates(tuning_fit_rows)
    grid_results = {}
    best = None
    for k_player in K_PLAYER_GRID:
        for shrink_shots in SHOOTING_SHRINK_GRID:
            probs1 = []
            for ex in tuning_validate:
                history = index.history_as_of(ex["player_id"], ex["game_date"])
                p = gh.player_role_hierarchical_threshold_rate(history, ex["role"], 1, rates_dev, k_player)
                probs1.append(p)
            briers = [(p - (1.0 if ex["actual_goals"] >= 1 else 0.0)) ** 2 for p, ex in zip(probs1, tuning_validate)]
            brier1 = statistics.fmean(briers)
            key = f"k{k_player}_shrink{shrink_shots}"
            grid_results[key] = {"k_player": k_player, "shrink_shots": shrink_shots, "brier_1plus": brier1}
            if best is None or brier1 < best[1]:
                best = (key, brier1)
    best_k_player = grid_results[best[0]]["k_player"]
    best_shrink_shots = grid_results[best[0]]["shrink_shots"]

    validate_fm = [ex["feature_vector"] for ex in tuning_validate]
    validate_stage_mus = {name: [cm.predict_mu(stage_weights_c[name], fv) for fv in validate_fm] for name, _ in STAGES}
    validate_stage_brier1 = {
        name: [(threshold_prob(mu, None, 1) - (1.0 if ex["actual_goals"] >= 1 else 0.0)) ** 2
               for mu, ex in zip(mus, tuning_validate)]
        for name, mus in validate_stage_mus.items()}

    names = [s[0] for s in STAGES]
    stage_value_tests = {}
    for i in range(1, len(names)):
        prev, cur = names[i - 1], names[i]
        stage_value_tests[f"{prev}_to_{cur}"] = game_clustered_bootstrap(
            tuning_validate, validate_stage_brier1[prev], validate_stage_brier1[cur])

    part_verdicts = {
        "recent_form_added_value": stage_value_tests["M0_baseline_only_to_M1_plus_recent_form"]["frac_improved"] >= 0.95,
        "toi_added_value": stage_value_tests["M1_plus_recent_form_to_M2_plus_toi"]["frac_improved"] >= 0.95,
        "pp_role_added_value": stage_value_tests["M2_plus_toi_to_M3_plus_pp_role"]["frac_improved"] >= 0.95,
        "shot_volume_added_value": stage_value_tests["M3_plus_pp_role_to_M4_plus_shot_volume"]["frac_improved"] >= 0.95,
        "shooting_talent_added_value": stage_value_tests["M4_plus_shot_volume_to_M5_plus_shooting_talent"]["frac_improved"] >= 0.95,
        "opponent_context_added_value": stage_value_tests["M5_plus_shooting_talent_to_M6_plus_opponent"]["frac_improved"] >= 0.95,
        "team_context_added_value": stage_value_tests["M6_plus_opponent_to_M7_plus_team_context"]["frac_improved"] >= 0.95,
        "h2h_sog_added_value": stage_value_tests["M7_plus_team_context_to_M8_plus_h2h_sog"]["frac_improved"] >= 0.95,
        "h2h_goals_added_value": stage_value_tests["M8_plus_h2h_sog_to_M9_plus_h2h_goals"]["frac_improved"] >= 0.95,
    }

    locked_stage = full_stage_name
    locked_weights_c = stage_weights_c[locked_stage]
    locked_weights_b = stage_weights_b[locked_stage]

    # candidate E's context set = whichever of {shot_volume, shooting_talent,
    # opponent, team, h2h_sog, h2h_goals} independently cleared the 95% bar
    context_feature_idx = {5: "shot_volume_added_value", 6: "shooting_talent_added_value",
                            7: "opponent_context_added_value", 8: "team_context_added_value",
                            9: "h2h_sog_added_value", 10: "h2h_goals_added_value"}
    locked_context_idx = {i for i, key in context_feature_idx.items() if part_verdicts[key]}

    # ---- upstream SOG eligibility (Part 6) ----
    upstream_sog_eligibility = ("NOT ELIGIBLE FOR CLEAN EVALUATION -- consistent with the same "
                                 "structural finding documented for PLAYER_POINTS_VALIDATION_REPORT.md "
                                 "Part 10/Section I: the validated SOG model exposes a single globally-"
                                 "fitted weight state whose own headline-stage selection was reported "
                                 "directly against its eval seasons rather than a separate pre-registered "
                                 "tuning-only split, so a genuinely clean out-of-fold reuse cannot be "
                                 "guaranteed. A player's own PIT-safe rolling SOG rate (NOT the SOG "
                                 "model's prediction) is used instead as the shot-volume signal.")

    # ------------------------------------------------------------------
    # FREEZE MANIFEST -- written before any EVAL_SEASONS row is scored.
    # ------------------------------------------------------------------
    import datetime as dt
    freeze_manifest = {
        "experiment_id": "player_goals_v1",
        "freeze_timestamp_utc": dt.datetime.utcnow().isoformat() + "Z",
        "target_definition": "actual_goals = I_F_goals (MoneyPuck direct field)",
        "player_eligibility_policy": "PlayerHistoryIndex history_as_of() >= 3 real prior games AND projected_active()",
        "model_family_candidates": ["A_hierarchical_empirical", "B_logistic", "C_poisson_glm",
                                     "D_negbinom_if_overdispersed", "E_hierarchical_plus_context_offset",
                                     "F_shot_generation_x_conversion"],
        "feature_set": FEATURE_NAMES,
        "lookback_windows": {"baseline": BASELINE_WINDOW, "recent_form": RECENT_WINDOW_5, "toi_recent": TOI_RECENT_WINDOW,
                              "opponent": OPPONENT_WINDOW, "team_context": TEAM_CONTEXT_WINDOW, "pp_role": RECENT_WINDOW_10,
                              "shot_volume": RECENT_WINDOW_10},
        "shrinkage_parameters": {"h2h_shrinkage_games": gf.H2H_SHRINKAGE_GAMES,
                                  "empirical_baseline_shrinkage_games": EMPIRICAL_SHRINK_GAMES,
                                  "role_hierarchical_k_player": best_k_player,
                                  "shooting_talent_shrinkage_shots": best_shrink_shots},
        "shooting_talent_methodology": "career shots-on-goal-weighted shrinkage toward league shooting%, "
                                        "n_shots/(n_shots+K) credibility weight, K chosen via tuning-validate grid search",
        "season_boundary_rules": f"WARMUP={WARMUP_SEASON} (history depth only), TUNING={TUNING_SEASON} "
                                  f"split at {tuning_split_date}, EVAL={EVAL_SEASONS}",
        "fitted_tuning_parameters_candidate_c": dict(zip(FEATURE_NAMES, locked_weights_c)),
        "fitted_tuning_parameters_candidate_b": dict(zip(FEATURE_NAMES, locked_weights_b)),
        "model_hyperparameters": {"glm_lr": 0.05, "glm_n_iter": 400, "logistic_lr": 0.3, "logistic_n_iter": 3000,
                                   "negbinom_alpha": alpha},
        "calibration_method": "NONE this cycle -- see distribution_analysis for hurdle/overdispersion check",
        "confidence_methodology": "shared research.player_sog.count_models.confidence_score (unchanged) -- "
                                   "per explicit instruction, NOT redesigned this slice",
        "conservative_probability_methodology": "shared research.player_sog.count_models.conservative_mu (unchanged)",
        "upstream_sog_eligibility": upstream_sog_eligibility,
        "two_plus_support_standard": TWO_PLUS_SUPPORT_STANDARD,
        "software_model_version": "player_goals_v1",
        "source_code_hashes": {
            "research/player_goals/build_goals_corpus.py": file_sha256("research/player_goals/build_goals_corpus.py"),
            "research/player_goals/features.py": file_sha256("research/player_goals/features.py"),
            "research/player_goals/hierarchy.py": file_sha256("research/player_goals/hierarchy.py"),
            "research/run_player_goals_model.py": file_sha256("research/run_player_goals_model.py"),
        },
        "per_feature_tuning_validate_verdicts": part_verdicts,
        "locked_context_idx_for_candidate_e": sorted(locked_context_idx),
        "locked_stage": locked_stage,
    }
    manifest_path = REPO_ROOT / "research" / "player_goals_freeze_manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(freeze_manifest, f, indent=2, sort_keys=True, default=str)

    # ==================================================================
    # FREEZE COMPLETE. Everything below reads EVAL SEASON outcomes for
    # the first and only time.
    # ==================================================================

    rates_final = gh.RoleLeagueRates(tuning_fit_rows + tuning_validate_rows)

    # ---- candidate E: fit context-offset weights on FULL tuning (fit+validate) ----
    e_fit_fm, e_fit_offsets, e_fit_obs = [], [], []
    for ex in (tuning_fit + tuning_validate):
        history = index.history_as_of(ex["player_id"], ex["game_date"])
        mu_base = gh.player_role_hierarchical_mean(history, ex["role"], rates_final, best_k_player)
        fv_masked = [v if i in locked_context_idx else 0.0 for i, v in enumerate(ex["feature_vector"])]
        e_fit_fm.append(fv_masked); e_fit_offsets.append(math.log(max(mu_base, 1e-6))); e_fit_obs.append(ex["actual_goals"])
    context_weights_e = fit_poisson_glm_with_offset(e_fit_fm, e_fit_obs, e_fit_offsets)
    alpha_e = cm.fit_negbinom_alpha_by_moments(e_fit_obs, [math.exp(o) for o in e_fit_offsets])

    eval_fm = [ex["feature_vector"] for ex in eval_examples]
    eval_obs = [ex["actual_goals"] for ex in eval_examples]

    mus_c = [cm.predict_mu(locked_weights_c, fv) for fv in eval_fm]
    logits_b = [sum(w * f for w, f in zip(locked_weights_b, fv)) for fv in eval_fm]
    probs1_b = [sigmoid(z) for z in logits_b]

    probs_a, mus_e, mus_f = [], [], []
    for ex in eval_examples:
        history = index.history_as_of(ex["player_id"], ex["game_date"])
        role = ex["role"]
        a_probs = {t: gh.player_role_hierarchical_threshold_rate(history, role, t, rates_final, best_k_player) for t in THRESHOLDS}
        probs_a.append(a_probs)
        mu_base = gh.player_role_hierarchical_mean(history, role, rates_final, best_k_player)
        fv_masked = [v if i in locked_context_idx else 0.0 for i, v in enumerate(ex["feature_vector"])]
        mu_e = predict_mu_with_offset(context_weights_e, fv_masked, math.log(max(mu_base, 1e-6)))
        mus_e.append(mu_e)
        recent_sog = ex["recent_sog_rate"] or 0.0
        mu_f = recent_sog * ex["shrunk_shooting_pct"]
        mus_f.append(mu_f)

    alpha_val = alpha if alpha > 0.01 else None
    alpha_e_val = alpha_e if alpha_e > 0.01 else None

    def candidate_metrics(probs_by_t):
        tm = threshold_metrics(eval_examples, probs_by_t, THRESHOLDS)
        return {"n": len(eval_examples),
                "thresholds": {str(t): {"n": v["n"], "brier": v["brier"], "log_loss": v["log_loss"],
                                         "actual_rate": v["actual_rate"], "brier_skill_score": skill_score(v["brier"], v["actual_rate"])}
                               for t, v in tm.items()},
                "_briers": {t: v["_briers"] for t, v in tm.items()}}

    probs_c = {t: [threshold_prob(mu, alpha_val, t) for mu in mus_c] for t in THRESHOLDS}
    probs_d = probs_c if alpha_val is None else {t: [threshold_prob(mu, None, t) for mu in mus_c] for t in THRESHOLDS}
    probs_e = {t: [threshold_prob(mu, alpha_e_val, t) for mu in mus_e] for t in THRESHOLDS}
    probs_f = {t: [threshold_prob(mu, None, t) for mu in mus_f] for t in THRESHOLDS}
    probs_a_by_t = {t: [p[t] for p in probs_a] for t in THRESHOLDS}
    probs_b_by_t = {1: probs1_b}

    tm_b = threshold_metrics(eval_examples, probs_b_by_t, (1,))
    candidate_results = {
        "A_hierarchical_empirical": candidate_metrics(probs_a_by_t),
        "B_logistic": {
            "n": len(eval_examples),
            "thresholds": {"1": {"n": tm_b[1]["n"], "brier": tm_b[1]["brier"], "log_loss": tm_b[1]["log_loss"],
                                  "actual_rate": tm_b[1]["actual_rate"],
                                  "brier_skill_score": skill_score(tm_b[1]["brier"], tm_b[1]["actual_rate"])}},
            "_briers": {1: tm_b[1]["_briers"]},
        },
        "C_poisson_glm": candidate_metrics(probs_c),
        "E_hierarchical_plus_context": candidate_metrics(probs_e),
        "F_shot_generation_x_conversion": candidate_metrics(probs_f),
    }
    if alpha_val is not None:
        candidate_results["D_negbinom"] = candidate_metrics(probs_d)

    # ---- baselines (Part 5) ----
    baseline_a_mus = [ex["baseline_rate"] for ex in eval_examples]
    baseline_b_mus = []
    for ex in eval_examples:
        history = index.history_as_of(ex["player_id"], ex["game_date"])
        season_hist = [r for r in history if r["season"] == ex["season"]]
        total_toi = sum(r["icetime_seconds"] for r in season_hist)
        per60 = (sum(r["goals"] for r in season_hist) * 3600.0 / total_toi) if total_toi > 0 else ex["baseline_rate"] * 200
        recent_toi = gf.rolling_mean(history, "icetime_seconds", 10) or 1000.0
        baseline_b_mus.append(per60 * recent_toi / 3600.0)
    baseline_c_mus = [(ex["recent_sog_rate"] or 0.0) * ex["shrunk_shooting_pct"] for ex in eval_examples]
    baseline_d_probs1 = []
    for ex in eval_examples:
        history = index.history_as_of(ex["player_id"], ex["game_date"])
        n = len(history)
        baseline_d_probs1.append((sum(1 for r in history if r["goals"] >= 1) / n) if n else league_empirical_rates[1])
    baseline_e_probs = {t: [ex["empirical_probs"][t] for ex in eval_examples] for t in THRESHOLDS}

    tm_d = threshold_metrics(eval_examples, {1: baseline_d_probs1}, (1,))
    baseline_results = {
        "A_season_to_date": candidate_metrics({t: [threshold_prob(m, None, t) for m in baseline_a_mus] for t in THRESHOLDS}),
        "B_per60_x_recent_toi": candidate_metrics({t: [threshold_prob(m, None, t) for m in baseline_b_mus] for t in THRESHOLDS}),
        "C_sog_x_shooting_pct": candidate_metrics({t: [threshold_prob(m, None, t) for m in baseline_c_mus] for t in THRESHOLDS}),
        "D_empirical_unshrunk": {
            "n": len(eval_examples),
            "thresholds": {"1": {"n": tm_d[1]["n"], "brier": tm_d[1]["brier"], "log_loss": tm_d[1]["log_loss"],
                                  "actual_rate": tm_d[1]["actual_rate"],
                                  "brier_skill_score": skill_score(tm_d[1]["brier"], tm_d[1]["actual_rate"])}},
            "_briers": {1: tm_d[1]["_briers"]},
        },
        "E_empirical_shrunk": candidate_metrics(baseline_e_probs),
    }

    # ---- headline bootstrap: every candidate vs best baseline, t=1 ----
    best_baseline_name = min(baseline_results, key=lambda k: baseline_results[k]["thresholds"]["1"]["brier"])
    best_baseline_briers1 = baseline_results[best_baseline_name]["_briers"][1]
    bootstrap_vs_best_baseline = {name: game_clustered_bootstrap(eval_examples, best_baseline_briers1, res["_briers"][1])
                                   for name, res in candidate_results.items()}
    best_candidate_name = min(candidate_results, key=lambda k: candidate_results[k]["thresholds"]["1"]["brier"])
    date_sensitivity_best = date_clustered_bootstrap(eval_examples, best_baseline_briers1,
                                                      candidate_results[best_candidate_name]["_briers"][1])

    # ---- season-by-season (best candidate) ----
    season_results = {}
    for season in EVAL_SEASONS:
        idx = [i for i, ex in enumerate(eval_examples) if ex["season"] == season]
        if not idx:
            continue
        sub_examples = [eval_examples[i] for i in idx]
        best_probs = candidate_results[best_candidate_name]
        sub_probs = {t: [probs_a_by_t.get(t, probs_c.get(t, probs1_b if t == 1 else []))[i] for i in idx] for t in THRESHOLDS if t in (probs_a_by_t if best_candidate_name == "A_hierarchical_empirical" else probs_c)}
        # season split computed directly from the winning candidate's own probability arrays
        winning_probs_by_t = {"A_hierarchical_empirical": probs_a_by_t, "B_logistic": probs_b_by_t, "C_poisson_glm": probs_c,
                               "D_negbinom": probs_d, "E_hierarchical_plus_context": probs_e, "F_shot_generation_x_conversion": probs_f}[best_candidate_name]
        sub_probs = {t: [winning_probs_by_t[t][i] for i in idx] for t in winning_probs_by_t}
        tm = threshold_metrics(sub_examples, sub_probs, tuple(sub_probs.keys()))
        season_results[str(season)] = {"n": len(idx), "thresholds": {
            str(t): {"brier": v["brier"], "actual_rate": v["actual_rate"], "brier_skill_score": skill_score(v["brier"], v["actual_rate"])}
            for t, v in tm.items()}}

    # ---- confidence (Part 24: unchanged shared architecture) ----
    winning_probs_by_t = {"A_hierarchical_empirical": probs_a_by_t, "B_logistic": probs_b_by_t, "C_poisson_glm": probs_c,
                           "D_negbinom": probs_d, "E_hierarchical_plus_context": probs_e, "F_shot_generation_x_conversion": probs_f}[best_candidate_name]
    for i, ex in enumerate(eval_examples):
        label, pos, risk = cm.confidence_score(ex["history_len"], ex["recent_toi_cv"], ex["recent_goals_cv"],
                                                ex["opponent_window_games"], OPPONENT_WINDOW, ex["appearance_rate"])
        ex["confidence"] = label

    confidence_breakdown = {}
    for label in ("HIGH", "MEDIUM", "LOW"):
        idx = [i for i, ex in enumerate(eval_examples) if ex["confidence"] == label]
        if not idx:
            continue
        sub_examples = [eval_examples[i] for i in idx]
        sub_probs = {1: [winning_probs_by_t[1][i] for i in idx]}
        tm = threshold_metrics(sub_examples, sub_probs, (1,))
        confidence_breakdown[label] = {"n": len(idx), "thresholds": {
            "1": {"brier": tm[1]["brier"], "actual_rate": tm[1]["actual_rate"],
                  "brier_skill_score": skill_score(tm[1]["brier"], tm[1]["actual_rate"])}}}

    # ---- conservative probability (best candidate's mu where available) ----
    mu_by_candidate = {"A_hierarchical_empirical": None, "B_logistic": None, "C_poisson_glm": mus_c,
                       "D_negbinom": mus_c, "E_hierarchical_plus_context": mus_e, "F_shot_generation_x_conversion": mus_f}
    conservative_check = None
    if mu_by_candidate[best_candidate_name] is not None:
        mus_best = mu_by_candidate[best_candidate_name]
        conservative_mus = [cm.conservative_mu(mu, min(ex["history_len"], 20)) for mu, ex in zip(mus_best, eval_examples)]
        conservative_probs1 = [threshold_prob(m, None, 1) for m in conservative_mus]
        frac_leq = sum(1 for c, r in zip(conservative_probs1, winning_probs_by_t[1]) if c <= r + 1e-9) / len(eval_examples)
        conservative_check = {"fraction_conservative_leq_raw": frac_leq,
                               "mean_raw_prob_1plus": statistics.fmean(winning_probs_by_t[1]),
                               "mean_conservative_prob_1plus": statistics.fmean(conservative_probs1)}

    # ---- 2+ support standard ----
    n_2plus_total = sum(1 for ex in eval_examples if ex["actual_goals"] >= 2)
    n_2plus_by_conf = {label: sum(1 for ex in eval_examples if ex["actual_goals"] >= 2 and ex["confidence"] == label)
                        for label in ("HIGH", "MEDIUM", "LOW")}
    n_2plus_by_season = {str(s): sum(1 for ex in eval_examples if ex["season"] == s and ex["actual_goals"] >= 2) for s in EVAL_SEASONS}
    # B (logistic) is a 1+-only binary model with no 2+ output -- fall back
    # to the best 2+-capable candidate for this specific check if it won overall.
    two_plus_candidate_name = best_candidate_name if best_candidate_name != "B_logistic" else min(
        (n for n in candidate_results if n != "B_logistic"), key=lambda k: candidate_results[k]["thresholds"]["2"]["brier"])
    two_plus_bootstrap = game_clustered_bootstrap(eval_examples, baseline_results["A_season_to_date"]["_briers"][2],
                                                    candidate_results[two_plus_candidate_name]["_briers"][2])
    support_checks = {
        "total_2plus_events": n_2plus_total, "passes_total": n_2plus_total >= TWO_PLUS_SUPPORT_STANDARD["min_total_events_eval_common_set"],
        "events_per_confidence_bucket": n_2plus_by_conf,
        "passes_per_bucket": all(v >= TWO_PLUS_SUPPORT_STANDARD["min_events_per_confidence_bucket"] for v in n_2plus_by_conf.values()),
        "events_per_season": n_2plus_by_season,
        "passes_per_season": all(v >= TWO_PLUS_SUPPORT_STANDARD["min_events_per_eval_season"] for v in n_2plus_by_season.values()),
        "bootstrap_ci_half_width": two_plus_bootstrap["ci_half_width"],
        "passes_bootstrap_width": two_plus_bootstrap["ci_half_width"] <= TWO_PLUS_SUPPORT_STANDARD["max_bootstrap_ci_half_width"],
    }
    support_checks["two_plus_status"] = "SUPPORTED" if all([support_checks["passes_total"], support_checks["passes_per_bucket"],
                                                             support_checks["passes_per_season"], support_checks["passes_bootstrap_width"]]) else "INSUFFICIENT_DATA"

    # ---- representative examples ----
    def find_example(pred):
        for i, ex in enumerate(eval_examples):
            if pred(ex):
                return i, ex
        return None

    reps_raw = {
        "elite_scorer": find_example(lambda e: e["baseline_rate"] > 0.35),
        "high_volume_shooter": find_example(lambda e: (e["recent_sog_rate"] or 0) > 3.5),
        "low_volume_finisher": find_example(lambda e: 0 < (e["recent_sog_rate"] or 0) < 1.5 and e["baseline_rate"] > 0.1),
        "pp_heavy_player": find_example(lambda e: e["pp_icetime_recent"] > 120.0),
        "defenseman": find_example(lambda e: not e["is_forward"] and e["baseline_rate"] > 0.08),
        "high_shooting_pct_small_sample": find_example(lambda e: e["career_shots"] < 30 and (e["raw_shooting_pct"] or 0) > 0.2),
        "heavily_shrunk_talent": find_example(lambda e: e["career_shots"] < 30 and e["raw_shooting_pct"] is not None
                                               and abs(e["shrunk_shooting_pct"] - e["raw_shooting_pct"]) > 0.05),
        "high_confidence": find_example(lambda e: e["confidence"] == "HIGH"),
        "low_confidence": find_example(lambda e: e["confidence"] == "LOW"),
    }
    representative_examples = {}
    for name, r in reps_raw.items():
        if r is None:
            representative_examples[name] = None
            continue
        i, ex = r
        representative_examples[name] = {
            "player": ex["player_name"], "team": ex["team"], "opponent": ex["opponent"], "game_date": ex["game_date"],
            "position": ex["position"], "p_1plus": round(winning_probs_by_t[1][i], 3),
            "raw_shooting_pct": round(ex["raw_shooting_pct"], 3) if ex["raw_shooting_pct"] is not None else None,
            "shrunk_shooting_pct": round(ex["shrunk_shooting_pct"], 3), "career_shots": ex["career_shots"],
            "confidence": ex["confidence"], "actual_goals": ex["actual_goals"],
        }
    # model hit / miss (mechanical, threshold 0.5)
    hit = find_example(lambda e: False)
    for i, ex in enumerate(eval_examples):
        p = winning_probs_by_t[1][i]
        pred_hit = (p >= 0.5) == (ex["actual_goals"] >= 1)
        if pred_hit and representative_examples.get("model_hit") is None:
            representative_examples["model_hit"] = {"player": ex["player_name"], "p_1plus": round(p, 3), "actual_goals": ex["actual_goals"]}
        if p >= 0.4 and ex["actual_goals"] == 0 and representative_examples.get("model_miss") is None:
            representative_examples["model_miss"] = {"player": ex["player_name"], "p_1plus": round(p, 3), "actual_goals": ex["actual_goals"]}

    common_eval_set_by_season = {}
    for season in EVAL_SEASONS:
        common_eval_set_by_season[str(season)] = {
            "total_target_player_games": total_target_rows_by_season[season],
            "excluded": total_target_rows_by_season[season] - len(examples_by_bucket[f"eval_{season}"]),
            "common_evaluation_rows": len(examples_by_bucket[f"eval_{season}"]),
        }

    test_proc = subprocess.run([sys.executable, "-m", "unittest", "discover", "tests"], cwd=str(REPO_ROOT), capture_output=True, text=True)

    out = {
        "config": {"warmup_season": WARMUP_SEASON, "tuning_season": TUNING_SEASON, "tuning_split_date": tuning_split_date,
                    "eval_seasons": EVAL_SEASONS, "feature_names": FEATURE_NAMES, "locked_stage": locked_stage},
        "freeze_manifest_path": str(manifest_path.relative_to(REPO_ROOT)),
        "corpus_size": len(rows), "tuning_fit_n": len(tuning_fit), "tuning_validate_n": len(tuning_validate),
        "eval_examples_n": len(eval_examples), "excluded": excluded,
        "goals_model_evaluation_conditionality": "CONDITIONAL ON ACTUAL GAME PARTICIPATION",
        "distribution_analysis": distribution_analysis, "alpha": alpha, "alpha_e": alpha_e,
        "context_weights_e": dict(zip(FEATURE_NAMES, context_weights_e)),
        "k_player_shrink_grid": grid_results, "best_k_player": best_k_player, "best_shrink_shots": best_shrink_shots,
        "stage_value_tests_tuning_validate": stage_value_tests, "per_feature_verdicts": part_verdicts,
        "locked_context_idx_for_candidate_e": sorted(locked_context_idx), "locked_stage": locked_stage,
        "upstream_sog_eligibility": upstream_sog_eligibility,
        "candidate_results": {name: {k: v for k, v in res.items() if not k.startswith("_")} for name, res in candidate_results.items()},
        "baseline_results": {name: {k: v for k, v in res.items() if not k.startswith("_")} for name, res in baseline_results.items()},
        "best_baseline_name": best_baseline_name, "best_candidate_name": best_candidate_name,
        "bootstrap_vs_best_baseline": bootstrap_vs_best_baseline, "date_sensitivity_best_candidate": date_sensitivity_best,
        "season_results": season_results, "confidence_breakdown": confidence_breakdown,
        "conservative_probability_check": conservative_check,
        "two_plus_support_standard": TWO_PLUS_SUPPORT_STANDARD, "two_plus_support_checks": support_checks,
        "representative_examples": representative_examples, "common_evaluation_set_by_season": common_eval_set_by_season,
        "test_suite_returncode": test_proc.returncode,
        "test_suite_stderr_tail": "\n".join(test_proc.stderr.strip().splitlines()[-8:]),
    }
    return out


if __name__ == "__main__":
    out = run_all()
    out_path = REPO_ROOT / "research" / "player_goals_results.json"
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, sort_keys=True, default=str)
    print(f"wrote {out_path}")
    print(f"wrote {out['freeze_manifest_path']}")
    print("test suite returncode:", out["test_suite_returncode"])
