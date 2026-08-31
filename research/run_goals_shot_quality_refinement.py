"""
Goals Model -- Shot-Quality Refinement Cycle.

EVALUATION STATUS: REUSED HISTORICAL DATA UNDER NEW GOALS DEVELOPMENT
CYCLE. The original Goals validation (PLAYER_GOALS_VALIDATION_REPORT.md)
already scored 2024-25/2025-26 once -- not pristine holdout here. This
cycle uses a 3-fold ROLLING-ORIGIN design across all 4 real seasons,
same structure as research/run_player_points_redesign.py.

CRITICAL RULE: the incumbent Goals model (candidate E from the original
cycle) is FROZEN and reused EXACTLY -- its RoleLeagueRates, k_player,
and context_weights_e/alpha_e are loaded from the already-persisted
research/player_goals_results.json and never refit. Every "incumbent"
prediction in every fold uses these same frozen artifacts. Only the
CHALLENGER's small shot-quality offset term is fit per fold (on that
fold's own train rows), exactly mirroring how
research/run_player_points_redesign.py's candidate C3 refit its context
weights per fold on top of a frozen hierarchical base.
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
from research.player_goals import shot_quality as sq
from research.player_sog import count_models as cm
from research.run_player_sog_model import build_team_schedules, NHL_CORPUS_PATH
import research.run_player_goals_model as gm

SEASONS_ORDER = [20222023, 20232024, 20242025, 20252026]


def file_sha256(rel_path: str) -> str:
    with open(REPO_ROOT / rel_path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


def load_incumbent():
    results = json.loads((REPO_ROOT / "research" / "player_goals_results.json").read_text())
    rows = gf.load_goals_corpus()
    totals = gf.build_team_game_goals_totals(rows)
    team_offense_hist = gf.build_team_offense_history(totals)
    opponent_env = gf.build_opponent_goals_allowed(totals)
    league_avg_goals_for = statistics.fmean(v["goals_for"] for v in totals.values())
    all_sog = sum(r["sog"] for r in rows)
    league_shooting_pct = sum(r["goals"] for r in rows) / all_sog
    index = gf.PlayerHistoryIndex(rows)
    games = ec.load_corpus(str(NHL_CORPUS_PATH))
    team_schedules = build_team_schedules(games)

    # the FROZEN incumbent aggregate: built from the ORIGINAL tuning pool
    # (2023-24, both halves) -- reused unchanged for every fold below.
    tuning_split_date = results["config"]["tuning_split_date"]
    original_tuning_rows = [r for r in rows if r["season"] == gm.TUNING_SEASON]
    rates_incumbent = gh.RoleLeagueRates(original_tuning_rows)
    k_player = results["best_k_player"]
    context_weights_e = [results["context_weights_e"][n] for n in results["config"]["feature_names"]]
    alpha_e = results["alpha_e"] if results["alpha_e"] > 0.01 else None
    locked_context_idx = set(results["locked_context_idx_for_candidate_e"])

    league_xg_per_shot = sum(r["individual_xg"] for r in rows) / all_sog
    all_attempts = sum(r["shot_attempts"] for r in rows)
    league_hd_share = sum(r["high_danger_shots"] for r in rows) / all_attempts
    pp_rows_all = [r for r in rows if r["pp"] is not None]
    pp_sog_total = sum(r["pp"]["sog"] for r in pp_rows_all)
    league_pp_xg_per_shot = (sum(r["pp"]["individual_xg"] for r in pp_rows_all) / pp_sog_total) if pp_sog_total > 0 else league_xg_per_shot

    return {
        "rows": rows, "index": index, "team_schedules": team_schedules, "team_offense_hist": team_offense_hist,
        "opponent_env": opponent_env, "league_avg_goals_for": league_avg_goals_for, "league_shooting_pct": league_shooting_pct,
        "rates_incumbent": rates_incumbent, "k_player": k_player, "context_weights_e": context_weights_e,
        "alpha_e": alpha_e, "locked_context_idx": locked_context_idx,
        "league_xg_per_shot": league_xg_per_shot, "league_hd_share": league_hd_share, "league_pp_xg_per_shot": league_pp_xg_per_shot,
        "results": results,
    }


def incumbent_mu(inc, row):
    history = inc["index"].history_as_of(row["player_id"], row["game_date"])
    if len(history) < 3:
        return None
    ex = gm.build_example(row, inc["index"], inc["team_schedules"], inc["team_offense_hist"], inc["opponent_env"],
                           inc["league_avg_goals_for"], inc["league_shooting_pct"], {1: 0.15, 2: 0.02})
    if ex is None:
        return None
    role = ex["role"]
    mu_base = gh.player_role_hierarchical_mean(history, role, inc["rates_incumbent"], inc["k_player"])
    fv_masked = [v if i in inc["locked_context_idx"] else 0.0 for i, v in enumerate(ex["feature_vector"])]
    mu = gm.predict_mu_with_offset(inc["context_weights_e"], fv_masked, math.log(max(mu_base, 1e-6)))
    return mu, ex


def threshold_prob(mu, alpha, t):
    return cm.negbinom_sf_at_least(t, mu, alpha) if alpha else cm.poisson_sf_at_least(t, mu)


def brier(p, y):
    return (p - y) ** 2


def skill_score(brier_val, actual_rate):
    naive = actual_rate * (1 - actual_rate)
    return None if naive <= 0 else 1.0 - brier_val / naive


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
            "frac_improved": frac_improved, "n_resamples": n_resamples, "n_games_resampled": n_games}


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


def build_row_example(inc, row):
    out = incumbent_mu(inc, row)
    if out is None:
        return None
    mu_inc, ex = out
    history = inc["index"].history_as_of(row["player_id"], row["game_date"])
    xg_shot, career_shots_xg = sq.xg_per_shot_shrunk(history, inc["league_xg_per_shot"], 100)
    hd_share, career_attempts = sq.high_danger_share_shrunk(history, inc["league_hd_share"], 200)
    fin_xg, n_games_fin = sq.finishing_above_xg_shrunk(history, 100)
    pp_xg_shot, career_pp_shots = sq.pp_xg_per_shot_shrunk(history, inc["league_pp_xg_per_shot"], 100)
    return {
        "player_id": row["player_id"], "player_name": row["player_name"], "game_id": row["game_id"],
        "game_date": row["game_date"], "season": row["season"], "position": row["position"],
        "is_forward": ex["is_forward"], "actual_goals": row["goals"], "mu_incumbent": mu_inc,
        "xg_per_shot": xg_shot, "high_danger_share": hd_share, "finishing_above_xg": fin_xg,
        "pp_xg_per_shot": pp_xg_shot, "career_shots": career_shots_xg,
        "history_len": ex["history_len"], "recent_toi_cv": ex["recent_toi_cv"], "recent_goals_cv": ex["recent_goals_cv"],
        "opponent_window_games": ex["opponent_window_games"], "appearance_rate": ex["appearance_rate"],
    }


def fit_offset_1d(mu_base_list, feature_list, obs_list, lr=0.05, n_iter=300):
    w = 0.0
    n = len(obs_list)
    for _ in range(n_iter):
        grad = 0.0
        for mu_b, f, y in zip(mu_base_list, feature_list, obs_list):
            z = min(math.log(max(mu_b, 1e-6)) + w * f, 30.0)
            mu = math.exp(z)
            grad += (mu - y) * f
        w -= lr * grad / n
    return w


def predict_with_1d_offset(mu_base, feature, w):
    z = math.log(max(mu_base, 1e-6)) + w * feature
    return math.exp(min(z, 30.0))


# ============================================================================
# METHODOLOGY NOTE (deliberate, disclosed): the incumbent's own frozen
# RoleLeagueRates/context_weights_e were fit using ALL of TUNING_SEASON
# (2023-24) data (research/run_player_goals_model.py's `rates_final`).
# Scoring 2022-23 rows with those frozen artifacts would mean the
# "incumbent" prediction for a 2022-23 game encodes information from
# 2023-24 -- a real, if often-overlooked, backward temporal-leakage risk
# at the AGGREGATE level (player-level history stays correctly gated
# throughout via player_history_as_of() either way). To avoid this, this
# refinement cycle does NOT use 2022-23 as a development sandbox. Instead:
#   DEV        = 2024-25, split internally 70/30 by date (both halves
#                strictly postdate the incumbent's own 2023-24 fit data)
#   FINAL FOLD = 2025-26 (the freshest available season, used exactly once)
# This is a single final fold rather than the "multiple rolling folds"
# ideal, precisely because the data budget for temporally-VALID forward
# folds is genuinely exhausted after the incumbent's own fitting and the
# original Goals cycle's eval -- reported honestly, not worked around.
# ============================================================================

DEV_EVAL_SEASON = 20242025
FINAL_SEASON = 20252026


def run_all():
    inc = load_incumbent()
    rows, index = inc["rows"], inc["index"]

    dev_dates = sorted({r["game_date"] for r in rows if r["season"] == DEV_EVAL_SEASON})
    dev_split_date = dev_dates[int(len(dev_dates) * 0.7)]
    dev_fit_rows = [r for r in rows if r["season"] == DEV_EVAL_SEASON and r["game_date"] < dev_split_date]
    dev_select_rows = [r for r in rows if r["season"] == DEV_EVAL_SEASON and r["game_date"] >= dev_split_date]

    dev_fit_examples = [e for e in (build_row_example(inc, r) for r in dev_fit_rows) if e is not None]
    dev_select_examples = [e for e in (build_row_example(inc, r) for r in dev_select_rows) if e is not None]

    incumbent_briers_dev = [brier(threshold_prob(e["mu_incumbent"], inc["alpha_e"], 1),
                                    1.0 if e["actual_goals"] >= 1 else 0.0) for e in dev_select_examples]

    candidate_features = {
        "B_xg_per_shot": "xg_per_shot", "C_high_danger_rate": "high_danger_share",
        "D_finishing_above_xg": "finishing_above_xg", "E_pp_xg_per_shot": "pp_xg_per_shot",
    }
    dev_value_tests = {}
    dev_fitted_weights = {}
    for name, field in candidate_features.items():
        mu_base_fit = [e["mu_incumbent"] for e in dev_fit_examples]
        feat_fit = [e[field] for e in dev_fit_examples]
        obs_fit = [e["actual_goals"] for e in dev_fit_examples]
        w = fit_offset_1d(mu_base_fit, feat_fit, obs_fit)
        dev_fitted_weights[name] = w
        probs_challenger = [threshold_prob(predict_with_1d_offset(e["mu_incumbent"], e[field], w), inc["alpha_e"], 1)
                             for e in dev_select_examples]
        briers_challenger = [brier(p, 1.0 if e["actual_goals"] >= 1 else 0.0) for p, e in zip(probs_challenger, dev_select_examples)]
        dev_value_tests[name] = game_clustered_bootstrap(dev_select_examples, incumbent_briers_dev, briers_challenger)

    # combined term D: xg_per_shot + high_danger_share together (only if
    # BOTH individually looked promising -- otherwise skip per "do not
    # stack strongly redundant features simply because each sounds useful")
    combined_promising = (dev_value_tests["B_xg_per_shot"]["frac_improved"] >= 0.7
                           and dev_value_tests["C_high_danger_rate"]["frac_improved"] >= 0.7)
    combined_weights = None
    if combined_promising:
        mu_base_fit = [e["mu_incumbent"] for e in dev_fit_examples]
        f1 = [e["xg_per_shot"] for e in dev_fit_examples]
        f2 = [e["high_danger_share"] for e in dev_fit_examples]
        obs_fit = [e["actual_goals"] for e in dev_fit_examples]
        w1, w2 = 0.0, 0.0
        n = len(obs_fit)
        for _ in range(300):
            g1 = g2 = 0.0
            for mb, x1, x2, y in zip(mu_base_fit, f1, f2, obs_fit):
                z = min(math.log(max(mb, 1e-6)) + w1 * x1 + w2 * x2, 30.0)
                mu = math.exp(z)
                g1 += (mu - y) * x1; g2 += (mu - y) * x2
            w1 -= 0.05 * g1 / n; w2 -= 0.05 * g2 / n
        combined_weights = (w1, w2)
        probs_combined = []
        for e in dev_select_examples:
            z = min(math.log(max(e["mu_incumbent"], 1e-6)) + w1 * e["xg_per_shot"] + w2 * e["high_danger_share"], 30.0)
            probs_combined.append(threshold_prob(math.exp(z), inc["alpha_e"], 1))
        briers_combined = [brier(p, 1.0 if e["actual_goals"] >= 1 else 0.0) for p, e in zip(probs_combined, dev_select_examples)]
        dev_value_tests["D_combined_xg_hd"] = game_clustered_bootstrap(dev_select_examples, incumbent_briers_dev, briers_combined)

    # ---- pick the best-performing challenger on DEV, freeze it ----
    best_challenger_name = min(dev_value_tests, key=lambda k: dev_value_tests[k]["point_delta"])
    challenger_cleared_bar = dev_value_tests[best_challenger_name]["frac_improved"] >= 0.95

    manifest = {
        "experiment_id": "goals_shot_quality_refinement_v1",
        "incumbent_version": "player_goals_v1 (candidate E, unchanged)",
        "challenger_version": "goals_shot_quality_v1",
        "dev_season": DEV_EVAL_SEASON, "dev_split_date": dev_split_date, "final_fold_season": FINAL_SEASON,
        "candidate_features_tested": list(candidate_features.values()) + (["xg_per_shot+high_danger_share"] if combined_promising else []),
        "shrinkage": {"xg_per_shot_shots": 100, "high_danger_share_attempts": 200,
                      "finishing_above_xg_games": 100, "pp_xg_per_shot_shots": 100},
        "dev_value_tests": {k: v["frac_improved"] for k, v in dev_value_tests.items()},
        "best_challenger_on_dev": best_challenger_name, "challenger_cleared_95pct_bar_on_dev": challenger_cleared_bar,
        "dev_fitted_weights": dev_fitted_weights, "combined_weights_xg_hd": combined_weights,
        "calibration_policy": "none -- diagnostic calibration-by-region reported, no calibrator fit",
        "confidence_policy": "UNCHANGED shared research.player_sog.count_models.confidence_score",
        "conservative_probability_policy": "UNCHANGED shared research.player_sog.count_models.conservative_mu",
        "freeze_timestamp_utc": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "source_code_hashes": {
            "research/player_goals/shot_quality.py": file_sha256("research/player_goals/shot_quality.py"),
            "research/run_goals_shot_quality_refinement.py": file_sha256("research/run_goals_shot_quality_refinement.py"),
        },
    }
    manifest_path = REPO_ROOT / "research" / "goals_shot_quality_manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True, default=str)

    # ==================================================================
    # FREEZE COMPLETE -- score the FINAL FOLD (2025-26) for the first time.
    # ==================================================================
    final_rows = [r for r in rows if r["season"] == FINAL_SEASON]
    final_examples = [e for e in (build_row_example(inc, r) for r in final_rows) if e is not None]

    def challenger_prob(e, name):
        if name == "D_combined_xg_hd" and combined_weights is not None:
            w1, w2 = combined_weights
            z = min(math.log(max(e["mu_incumbent"], 1e-6)) + w1 * e["xg_per_shot"] + w2 * e["high_danger_share"], 30.0)
            return threshold_prob(math.exp(z), inc["alpha_e"], 1)
        field = candidate_features[name]
        w = dev_fitted_weights[name]
        return threshold_prob(predict_with_1d_offset(e["mu_incumbent"], e[field], w), inc["alpha_e"], 1)

    incumbent_probs_final = [threshold_prob(e["mu_incumbent"], inc["alpha_e"], 1) for e in final_examples]
    incumbent_briers_final = [brier(p, 1.0 if e["actual_goals"] >= 1 else 0.0) for p, e in zip(incumbent_probs_final, final_examples)]

    final_results = {}
    for name in dev_value_tests:
        probs = [challenger_prob(e, name) for e in final_examples]
        briers_c = [brier(p, 1.0 if e["actual_goals"] >= 1 else 0.0) for p, e in zip(probs, final_examples)]
        actual_rate = statistics.fmean(1.0 if e["actual_goals"] >= 1 else 0.0 for e in final_examples)
        gb = game_clustered_bootstrap(final_examples, incumbent_briers_final, briers_c)
        db = date_clustered_bootstrap(final_examples, incumbent_briers_final, briers_c)
        final_results[name] = {
            "n": len(final_examples), "brier": statistics.fmean(briers_c),
            "brier_skill_score": skill_score(statistics.fmean(briers_c), actual_rate),
            "log_loss": statistics.fmean(-(y * math.log(max(p, 1e-12)) + (1 - y) * math.log(max(1 - p, 1e-12)))
                                          for p, y in zip(probs, (1.0 if e["actual_goals"] >= 1 else 0.0 for e in final_examples))),
            "game_bootstrap_vs_incumbent": gb, "date_bootstrap_vs_incumbent": db,
            "_briers": briers_c, "_probs": probs,
        }

    incumbent_actual_rate = statistics.fmean(1.0 if e["actual_goals"] >= 1 else 0.0 for e in final_examples)
    incumbent_final_metrics = {
        "n": len(final_examples), "brier": statistics.fmean(incumbent_briers_final),
        "brier_skill_score": skill_score(statistics.fmean(incumbent_briers_final), incumbent_actual_rate),
        "log_loss": statistics.fmean(-(y * math.log(max(p, 1e-12)) + (1 - y) * math.log(max(1 - p, 1e-12)))
                                      for p, y in zip(incumbent_probs_final, (1.0 if e["actual_goals"] >= 1 else 0.0 for e in final_examples))),
    }

    best_final_name = min(final_results, key=lambda k: final_results[k]["brier"])
    final_best_beats_incumbent = final_results[best_final_name]["game_bootstrap_vs_incumbent"]["frac_improved"] >= 0.95

    # ---- diagnostic robustness check: same challenger re-scored on
    # dev_select (2024-25) too -- reported, not decision-driving ----
    dev_select_diag = {}
    for name in dev_value_tests:
        probs = [challenger_prob(e, name) for e in dev_select_examples]
        briers_c = [brier(p, 1.0 if e["actual_goals"] >= 1 else 0.0) for p, e in zip(probs, dev_select_examples)]
        dev_select_diag[name] = {"frac_improved_vs_incumbent": dev_value_tests[name]["frac_improved"],
                                  "brier": statistics.fmean(briers_c)}

    # ---- segments (best final candidate vs incumbent) ----
    def segment_report(pred, best_name):
        idx = [i for i, e in enumerate(final_examples) if pred(e)]
        if not idx:
            return None
        y = [1.0 if final_examples[i]["actual_goals"] >= 1 else 0.0 for i in idx]
        p_inc = [incumbent_probs_final[i] for i in idx]
        p_chal = [final_results[best_name]["_probs"][i] for i in idx]
        b_inc = statistics.fmean((p - yy) ** 2 for p, yy in zip(p_inc, y))
        b_chal = statistics.fmean((p - yy) ** 2 for p, yy in zip(p_chal, y))
        return {"n": len(idx), "incumbent_brier": b_inc, "challenger_brier": b_chal, "delta": b_chal - b_inc}

    shot_terciles = sorted(e["career_shots"] for e in final_examples)
    t1 = shot_terciles[len(shot_terciles) // 3]; t2 = shot_terciles[2 * len(shot_terciles) // 3]
    segments = {
        "shot_volume": {
            "LOW": segment_report(lambda e: e["career_shots"] <= t1, best_final_name),
            "MEDIUM": segment_report(lambda e: t1 < e["career_shots"] <= t2, best_final_name),
            "HIGH": segment_report(lambda e: e["career_shots"] > t2, best_final_name),
        },
        "position": {
            "FORWARD": segment_report(lambda e: e["is_forward"], best_final_name),
            "DEFENSE": segment_report(lambda e: not e["is_forward"], best_final_name),
        },
        "sample_size": {
            "LOW": segment_report(lambda e: e["history_len"] < 20, best_final_name),
            "MEDIUM": segment_report(lambda e: 20 <= e["history_len"] < 60, best_final_name),
            "MATURE": segment_report(lambda e: e["history_len"] >= 60, best_final_name),
        },
    }

    # ---- calibration by probability region ----
    calib_regions = {}
    for lo in range(0, 60, 10):
        idx = [i for i, p in enumerate(incumbent_probs_final) if lo / 100.0 <= p < (lo + 10) / 100.0]
        if not idx:
            continue
        y = [1.0 if final_examples[i]["actual_goals"] >= 1 else 0.0 for i in idx]
        calib_regions[f"{lo}-{lo+10}%"] = {
            "n": len(idx), "actual_rate": statistics.fmean(y),
            "incumbent_mean_pred": statistics.fmean(incumbent_probs_final[i] for i in idx),
            "challenger_mean_pred": statistics.fmean(final_results[best_final_name]["_probs"][i] for i in idx),
        }

    # ---- confidence (incumbent vs best challenger) ----
    for e in final_examples:
        label, _, _ = cm.confidence_score(e["history_len"], e["recent_toi_cv"], e["recent_goals_cv"],
                                           e["opponent_window_games"], 20, e["appearance_rate"])
        e["confidence"] = label
    confidence_comparison = {}
    for label in ("HIGH", "MEDIUM", "LOW"):
        idx = [i for i, e in enumerate(final_examples) if e["confidence"] == label]
        if not idx:
            continue
        y = [1.0 if final_examples[i]["actual_goals"] >= 1 else 0.0 for i in idx]
        p_inc = [incumbent_probs_final[i] for i in idx]
        p_chal = [final_results[best_final_name]["_probs"][i] for i in idx]
        b_inc = statistics.fmean((p - yy) ** 2 for p, yy in zip(p_inc, y))
        b_chal = statistics.fmean((p - yy) ** 2 for p, yy in zip(p_chal, y))
        ar = statistics.fmean(y)
        confidence_comparison[label] = {"n": len(idx), "incumbent_skill": skill_score(b_inc, ar),
                                         "challenger_skill": skill_score(b_chal, ar)}

    # ---- 2+ diagnostic (Part 26: report only, no re-adoption of the
    # pre-specified support rule) -- this refinement only fit/tested a
    # threshold-1 offset, so 2+ is reported purely as a raw event count,
    # not a recomputed probability claim.
    n_2plus = sum(1 for e in final_examples if e["actual_goals"] >= 2)

    test_proc = subprocess.run([sys.executable, "-m", "unittest", "discover", "tests"], cwd=str(REPO_ROOT), capture_output=True, text=True)

    out = {
        "evaluation_status": "REUSED HISTORICAL DATA UNDER NEW GOALS DEVELOPMENT CYCLE",
        "methodology_note": "DEV=2024-25 (internal split), FINAL FOLD=2025-26 -- single final fold, "
                             "not multiple rolling folds, because 2022-23/2023-24 already fed the "
                             "incumbent's own fit and cannot be reused without backward temporal leakage.",
        "freeze_manifest_path": str(manifest_path.relative_to(REPO_ROOT)),
        "dev_fit_n": len(dev_fit_examples), "dev_select_n": len(dev_select_examples), "final_fold_n": len(final_examples),
        "dev_value_tests": {k: v for k, v in dev_value_tests.items()},
        "best_challenger_on_dev": best_challenger_name, "challenger_cleared_95pct_bar_on_dev": challenger_cleared_bar,
        "incumbent_final_metrics": incumbent_final_metrics,
        "challenger_final_metrics": {k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")} for k, v in final_results.items()},
        "best_final_candidate": best_final_name, "final_best_beats_incumbent_95pct_bar": final_best_beats_incumbent,
        "dev_select_diagnostic_recheck": dev_select_diag,
        "segments": segments, "calibration_by_region": calib_regions,
        "confidence_comparison": confidence_comparison, "n_2plus_events_final_fold": n_2plus,
        "test_suite_returncode": test_proc.returncode,
        "test_suite_stderr_tail": "\n".join(test_proc.stderr.strip().splitlines()[-8:]),
    }
    return out


if __name__ == "__main__":
    out = run_all()
    out_path = REPO_ROOT / "research" / "goals_shot_quality_results.json"
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, sort_keys=True, default=str)
    print(f"wrote {out_path}")
    print("test suite returncode:", out["test_suite_returncode"])
