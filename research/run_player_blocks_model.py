"""
Driver for the Player BLOCKED SHOTS Probability Foundation -- the second
prop model built on the shared player-prop framework (Section F/G/H of
the Preseason Product Sprint). Deliberately mirrors
research/run_player_sog_model.py's structure (same walk-forward
discipline, same stepwise value-test design, same baselines) but
independently audits and tests its OWN features -- no statistical
assumption is carried over from SOG without being re-tested here
(H2H/TOI/opponent/recent-form must each earn their place again; see
Section T/U/V of the sprint prompt).

Reuses research/player_sog/count_models.py's Poisson/NegBin math,
Poisson-GLM fit, confidence scoring, and conservative-probability bound
DIRECTLY (verified prop-agnostic, zero SOG-specific hardcoding) --
genuine shared-framework reuse, not a second bespoke implementation.
The validated SOG MODEL (its fitted weights) is never read or depended
on.
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
from research.player_blocks import features as bf
from research.player_sog import count_models as cm
from research.run_player_sog_model import build_team_schedules, NHL_CORPUS_PATH

WARMUP_SEASON = 20222023
TUNING_SEASON = 20232024
EVAL_SEASONS = [20242025, 20252026]

BASELINE_WINDOW = 20
RECENT_WINDOW_5 = 5
RECENT_WINDOW_10 = 10
TOI_RECENT_WINDOW = 10
OPPONENT_WINDOW = 20
THRESHOLDS = (1, 2, 3, 4, 5, 6)
HEADLINE_THRESHOLDS = (1, 2, 3)  # blocked shots is a lower-volume stat than SOG — 1+/2+/3+ are the realistic sportsbook range

STAGES = [
    ("M0_baseline_only", {0, 1}),
    ("M1_plus_recent_form", {0, 1, 2}),
    ("M2_plus_toi_role", {0, 1, 2, 3}),
    ("M3_plus_opponent", {0, 1, 2, 3, 4}),
    ("M4_plus_h2h", {0, 1, 2, 3, 4, 5}),
]


def masked_matrix(feature_matrix: list[list[float]], keep_idx: set[int]) -> list[list[float]]:
    return [[v if i in keep_idx else 0.0 for i, v in enumerate(fv)] for fv in feature_matrix]


def build_example(all_rows: list[dict], row: dict, player_index: bf.PlayerHistoryIndex,
                   team_schedules: dict, opponent_env: dict, league_avg_opp_shot_attempts: float) -> dict | None:
    player_id, team, opponent = row["player_id"], row["team"], row["opponent"]
    date = row["game_date"]

    history = player_index.history_as_of(player_id, date)
    if len(history) < 3:
        return None

    team_sched_prior = [g for g in team_schedules.get(team, []) if g["game_date"] < date]
    if not bf.projected_active(history, team_sched_prior):
        return None

    baseline_rate = bf.rolling_mean(history, "blocks", BASELINE_WINDOW)
    if baseline_rate is None:
        baseline_rate = bf.season_to_date_mean(history, "blocks", row["season"])
    if baseline_rate is None or baseline_rate <= 0:
        baseline_rate = 0.3

    recent_rate5 = bf.rolling_mean(history, "blocks", RECENT_WINDOW_5)
    recent_rate10 = bf.rolling_mean(history, "blocks", RECENT_WINDOW_10)
    recent_toi = bf.rolling_mean(history, "icetime_seconds", TOI_RECENT_WINDOW)
    baseline_toi = bf.rolling_mean(history, "icetime_seconds", BASELINE_WINDOW)

    opp_hist = bf.opponent_environment_history_as_of(opponent_env, opponent, date)
    opp_attempts = bf.rolling_opponent_shot_attempts(opponent_env, opponent, date, OPPONENT_WINDOW)
    opponent_factor = None if opp_attempts is None else opp_attempts / league_avg_opp_shot_attempts

    h2h_rate, h2h_games = bf.h2h_shrunk_blocks_rate(history, opponent, baseline_rate)
    h2h_delta = h2h_rate - baseline_rate

    is_home = row["home_or_away"] == "HOME"
    recent_team_games = team_sched_prior[-bf.ELIGIBILITY_WINDOW_TEAM_GAMES:]
    appearance_rate = 1.0
    if recent_team_games:
        played_dates = {r["game_date"] for r in history}
        appearance_rate = sum(1 for g in recent_team_games if g["game_date"] in played_dates) / len(recent_team_games)

    toi_window = history[-TOI_RECENT_WINDOW:]
    blocks_window = history[-RECENT_WINDOW_10:]
    recent_toi_cv = cm.coefficient_of_variation([r["icetime_seconds"] for r in toi_window]) if toi_window else None
    recent_blocks_cv = cm.coefficient_of_variation([r["blocks"] for r in blocks_window]) if blocks_window else None

    fv = cm.build_feature_vector(baseline_rate, recent_rate5, recent_toi, baseline_toi, opponent_factor, h2h_delta)

    season_rate = bf.season_to_date_mean(history, "blocks", row["season"])

    pk_toi = bf.rolling_pk_mean(history, "icetime_seconds", TOI_RECENT_WINDOW)

    return {
        "player_id": player_id, "player_name": row["player_name"], "team": team, "opponent": opponent,
        "game_id": row["game_id"], "game_date": date, "season": row["season"], "position": row["position"],
        "home_or_away": row["home_or_away"],
        "actual_blocks": row["blocks"], "history_len": len(history),
        "baseline_rate": baseline_rate, "recent_rate5": recent_rate5, "recent_rate10": recent_rate10,
        "recent_toi": recent_toi, "baseline_toi": baseline_toi, "pk_toi": pk_toi,
        "opponent_factor": opponent_factor, "opponent_window_games": len(opp_hist),
        "h2h_games": h2h_games, "h2h_delta": h2h_delta, "appearance_rate": appearance_rate,
        "recent_toi_cv": recent_toi_cv, "recent_blocks_cv": recent_blocks_cv,
        "feature_vector": fv, "season_rate": season_rate,
    }


def poisson_nll(actual: float, mu: float) -> float:
    k = int(round(actual))
    mu = max(mu, cm.EPS)
    return mu - k * math.log(mu) + math.lgamma(k + 1)


def negbin_nll(actual: float, mu: float, alpha: float) -> float:
    k = int(round(actual))
    p = max(cm.negbinom_pmf(k, mu, alpha), 1e-12)
    return -math.log(p)


def threshold_prob(mu: float, alpha: float | None, t: int) -> float:
    return cm.negbinom_sf_at_least(t, mu, alpha) if alpha else cm.poisson_sf_at_least(t, mu)


def threshold_metrics(examples: list[dict], mus: list[float], alpha: float | None,
                       thresholds=HEADLINE_THRESHOLDS) -> dict[int, dict]:
    per_t = {t: {"briers": [], "loglosses": [], "preds": [], "actuals": []} for t in thresholds}
    for ex, mu in zip(examples, mus):
        k_actual = int(round(ex["actual_blocks"]))
        for t in thresholds:
            p = threshold_prob(mu, alpha, t)
            y = 1.0 if k_actual >= t else 0.0
            per_t[t]["briers"].append((p - y) ** 2)
            per_t[t]["loglosses"].append(-(y * math.log(max(p, 1e-12)) + (1 - y) * math.log(max(1 - p, 1e-12))))
            per_t[t]["preds"].append(p)
            per_t[t]["actuals"].append(y)
    return {t: {"n": len(examples), "brier": statistics.fmean(v["briers"]),
                "log_loss": statistics.fmean(v["loglosses"]), "_briers": v["briers"],
                "mean_pred": statistics.fmean(v["preds"]), "actual_rate": statistics.fmean(v["actuals"])}
            for t, v in per_t.items()}


def mean_abs_error(mus, actuals) -> float:
    return statistics.fmean(abs(m - a) for m, a in zip(mus, actuals))


def rmse(mus, actuals) -> float:
    return math.sqrt(statistics.fmean((m - a) ** 2 for m, a in zip(mus, actuals)))


def skill_score(brier, actual_rate):
    naive = actual_rate * (1 - actual_rate)
    return None if naive <= 0 else 1.0 - brier / naive


def paired_bootstrap_fast(baseline_scores, candidate_scores, n_resamples=1000, subsample_size=10000, seed=20242025):
    n_full = len(baseline_scores)
    if n_full > subsample_size:
        idx = random.Random(seed).sample(range(n_full), subsample_size)
        baseline_scores = [baseline_scores[i] for i in idx]
        candidate_scores = [candidate_scores[i] for i in idx]
    return ec.paired_bootstrap_delta(baseline_scores, candidate_scores, n_resamples=n_resamples)


def run_all() -> dict:
    rows = bf.load_blocks_corpus()
    index = bf.PlayerHistoryIndex(rows)
    totals = bf.build_team_game_shot_attempt_totals(rows)
    opponent_env = bf.build_opponent_shot_attempt_environment(totals)
    league_avg_opp_shot_attempts = statistics.fmean(v["shot_attempts_against_for_team"] for v in totals.values())

    games = ec.load_corpus(str(NHL_CORPUS_PATH))
    team_schedules = build_team_schedules(games)

    examples_by_season = defaultdict(list)
    excluded = {"insufficient_history": 0, "not_projected_active": 0}
    for row in rows:
        if row["season"] not in (TUNING_SEASON, *EVAL_SEASONS):
            continue
        ex = build_example(rows, row, index, team_schedules, opponent_env, league_avg_opp_shot_attempts)
        if ex is None:
            history = index.history_as_of(row["player_id"], row["game_date"])
            if len(history) < 3:
                excluded["insufficient_history"] += 1
            else:
                excluded["not_projected_active"] += 1
            continue
        examples_by_season[row["season"]].append(ex)

    tuning = examples_by_season[TUNING_SEASON]
    eval_examples = [ex for s in EVAL_SEASONS for ex in examples_by_season[s]]

    league_hist = defaultdict(int)
    for ex in tuning:
        league_hist[min(int(round(ex["actual_blocks"])), 6)] += 1
    league_total = sum(league_hist.values())
    league_hist_frac = {k: v / league_total for k, v in league_hist.items()}

    FIT_SUBSAMPLE_SIZE = 12000
    FIT_SUBSAMPLE_SEED = 20232024
    fit_pool = tuning if len(tuning) <= FIT_SUBSAMPLE_SIZE else random.Random(FIT_SUBSAMPLE_SEED).sample(tuning, FIT_SUBSAMPLE_SIZE)
    tuning_fm = [ex["feature_vector"] for ex in fit_pool]
    tuning_obs = [ex["actual_blocks"] for ex in fit_pool]
    stage_weights = {}
    for name, keep in STAGES:
        stage_weights[name] = cm.fit_poisson_glm(masked_matrix(tuning_fm, keep), tuning_obs)

    headline_stage = "M4_plus_h2h"
    tuning_mu_full = [cm.predict_mu(stage_weights[headline_stage], fv) for fv in tuning_fm]
    alpha = cm.fit_negbinom_alpha_by_moments(tuning_obs, tuning_mu_full)
    overdispersion = cm.overdispersion_stats([ex["actual_blocks"] for ex in tuning])

    eval_fm = [ex["feature_vector"] for ex in eval_examples]
    eval_obs = [ex["actual_blocks"] for ex in eval_examples]
    stage_mus = {name: [cm.predict_mu(stage_weights[name], fv) for fv in eval_fm] for name, _ in STAGES}

    baseline_a_mus = [ex["season_rate"] if ex["season_rate"] else ex["baseline_rate"] for ex in eval_examples]
    baseline_b_mus = [ex["recent_rate10"] if ex["recent_rate10"] else ex["baseline_rate"] for ex in eval_examples]
    baseline_a_mus = [m if m and m > 0 else 0.3 for m in baseline_a_mus]
    baseline_b_mus = [m if m and m > 0 else 0.3 for m in baseline_b_mus]

    def eval_candidate(mus, alpha_val=None, thresholds=HEADLINE_THRESHOLDS):
        tm = threshold_metrics(eval_examples, mus, alpha_val, thresholds)
        return {
            "n": len(eval_examples),
            "nll_mean": statistics.fmean(
                (negbin_nll(a, m, alpha_val) if alpha_val else poisson_nll(a, m)) for a, m in zip(eval_obs, mus)),
            "mae": mean_abs_error(mus, eval_obs), "rmse": rmse(mus, eval_obs),
            "thresholds": {str(t): {"n": v["n"], "brier": v["brier"], "log_loss": v["log_loss"],
                                     "mean_pred": v["mean_pred"], "actual_rate": v["actual_rate"],
                                     "brier_skill_score": skill_score(v["brier"], v["actual_rate"])}
                            for t, v in tm.items()},
            "_threshold_briers": {t: v["_briers"] for t, v in tm.items()},
        }

    headline_full_thresholds_poisson = eval_candidate(stage_mus[headline_stage], None, THRESHOLDS)
    headline_full_thresholds_negbin = eval_candidate(stage_mus[headline_stage], alpha, THRESHOLDS)
    stage_results = {name: eval_candidate(stage_mus[name]) for name, _ in STAGES}
    baseline_results = {
        "A_season_average": eval_candidate(baseline_a_mus),
        "B_last10_average": eval_candidate(baseline_b_mus),
    }

    value_tests = {}
    stage_names = [s[0] for s in STAGES]
    for i in range(1, len(stage_names)):
        prev, cur = stage_names[i - 1], stage_names[i]
        bs = paired_bootstrap_fast(stage_results[prev]["_threshold_briers"][2], stage_results[cur]["_threshold_briers"][2])
        value_tests[f"{prev}_to_{cur}"] = bs

    baseline_vs_full = {}
    for name, res in baseline_results.items():
        bs = paired_bootstrap_fast(res["_threshold_briers"][2], stage_results[headline_stage]["_threshold_briers"][2])
        baseline_vs_full[name] = bs

    season_breakdown = {}
    for season in EVAL_SEASONS:
        idx = [i for i, ex in enumerate(eval_examples) if ex["season"] == season]
        if not idx:
            continue
        sub_mus = [stage_mus[headline_stage][i] for i in idx]
        sub_obs = [eval_obs[i] for i in idx]
        sub_examples = [eval_examples[i] for i in idx]
        tm = threshold_metrics(sub_examples, sub_mus, None, HEADLINE_THRESHOLDS)
        season_breakdown[str(season)] = {"n": len(idx), "mae": mean_abs_error(sub_mus, sub_obs),
                                          "thresholds": {str(t): {"brier": v["brier"], "log_loss": v["log_loss"]}
                                                         for t, v in tm.items()}}

    def position_group(pos: str) -> str:
        return "DEFENSE" if pos == "D" else "FORWARD"

    def segment_eval(predicate):
        idx = [i for i, ex in enumerate(eval_examples) if predicate(ex)]
        if not idx:
            return None
        sub_mus = [stage_mus[headline_stage][i] for i in idx]
        sub_examples = [eval_examples[i] for i in idx]
        tm = threshold_metrics(sub_examples, sub_mus, None, HEADLINE_THRESHOLDS)
        return {"n": len(idx), "thresholds": {str(t): {"brier": v["brier"], "log_loss": v["log_loss"],
                                                         "actual_rate": v["actual_rate"],
                                                         "brier_skill_score": skill_score(v["brier"], v["actual_rate"])}
                                               for t, v in tm.items()}}

    segments = {
        "FORWARD": segment_eval(lambda ex: position_group(ex["position"]) == "FORWARD"),
        "DEFENSE": segment_eval(lambda ex: position_group(ex["position"]) == "DEFENSE"),
    }

    calibration = {str(t): [] for t in HEADLINE_THRESHOLDS}
    for t in HEADLINE_THRESHOLDS:
        rows_ = []
        for ex, mu in zip(eval_examples, stage_mus[headline_stage]):
            p = threshold_prob(mu, None, t)
            y = 1.0 if int(round(ex["actual_blocks"])) >= t else 0.0
            rows_.append((p, y))
        edges = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
        for lo, hi in zip(edges, edges[1:]):
            bucket = [(p, y) for p, y in rows_ if (lo <= p < hi if hi < 1.0 else lo <= p <= hi)]
            if not bucket:
                calibration[str(t)].append({"lo": lo, "hi": hi, "n": 0, "mean_pred": None, "actual_rate": None})
                continue
            mp = statistics.fmean(p for p, _ in bucket)
            ar = statistics.fmean(y for _, y in bucket)
            calibration[str(t)].append({"lo": lo, "hi": hi, "n": len(bucket), "mean_pred": mp, "actual_rate": ar,
                                         "calibration_error": abs(mp - ar)})

    for ex, mu in zip(eval_examples, stage_mus[headline_stage]):
        label, pos, risk = cm.confidence_score(
            ex["history_len"], ex["recent_toi_cv"], ex["recent_blocks_cv"],
            ex["opponent_window_games"], OPPONENT_WINDOW, ex["appearance_rate"])
        ex["confidence"] = label
        ex["expected_blocks"] = mu
        eff_n = min(ex["history_len"], BASELINE_WINDOW)
        ex["conservative_blocks"] = cm.conservative_mu(mu, eff_n)

    confidence_breakdown = {}
    for label in ("HIGH", "MEDIUM", "LOW"):
        idx = [i for i, ex in enumerate(eval_examples) if ex["confidence"] == label]
        if not idx:
            continue
        sub_mus = [stage_mus[headline_stage][i] for i in idx]
        sub_examples = [eval_examples[i] for i in idx]
        tm = threshold_metrics(sub_examples, sub_mus, None, HEADLINE_THRESHOLDS)
        confidence_breakdown[label] = {"n": len(idx), "thresholds": {
            str(t): {"brier": v["brier"], "actual_rate": v["actual_rate"],
                      "brier_skill_score": skill_score(v["brier"], v["actual_rate"])}
            for t, v in tm.items()}}

    return {
        "rows": rows, "tuning": tuning, "eval_examples": eval_examples, "excluded": excluded,
        "total_eligible_player_games_eval": sum(1 for r in rows if r["season"] in EVAL_SEASONS),
        "stage_weights": stage_weights, "alpha": alpha, "overdispersion": overdispersion,
        "headline_stage": headline_stage,
        "headline_full_thresholds_poisson": headline_full_thresholds_poisson,
        "headline_full_thresholds_negbin": headline_full_thresholds_negbin,
        "stage_results": stage_results, "baseline_results": baseline_results,
        "value_tests": value_tests, "baseline_vs_full": baseline_vs_full,
        "season_breakdown": season_breakdown, "segments": segments, "calibration": calibration,
        "confidence_breakdown": confidence_breakdown,
    }


def strip_internal(d):
    return {k: v for k, v in d.items() if not k.startswith("_")}


def build_full_results() -> dict:
    r = run_all()
    results = {
        "config": {"warmup_season": WARMUP_SEASON, "tuning_season": TUNING_SEASON, "eval_seasons": EVAL_SEASONS,
                   "headline_thresholds": list(HEADLINE_THRESHOLDS), "feature_names": cm.FEATURE_NAMES},
        "corpus_size": len(r["rows"]),
        "common_evaluation_set": {
            "eval_examples_n": len(r["eval_examples"]), "tuning_examples_n": len(r["tuning"]),
            "excluded": r["excluded"],
            "coverage_pct_eval": round(100.0 * len(r["eval_examples"]) / max(1, r["total_eligible_player_games_eval"]), 2),
        },
        "overdispersion": r["overdispersion"], "negbinom_alpha_fitted": r["alpha"],
        "stage_weights": {name: dict(zip(cm.FEATURE_NAMES, w)) for name, w in r["stage_weights"].items()},
        "headline_stage": r["headline_stage"],
        "headline_full_thresholds_poisson": strip_internal(r["headline_full_thresholds_poisson"]),
        "headline_full_thresholds_negbin": strip_internal(r["headline_full_thresholds_negbin"]),
        "stage_results": {name: strip_internal(res) for name, res in r["stage_results"].items()},
        "baseline_results": {name: strip_internal(res) for name, res in r["baseline_results"].items()},
        "value_tests_stage_deltas_threshold2": {k: {kk: vv for kk, vv in v.items()} for k, v in r["value_tests"].items()},
        "baseline_vs_full_threshold2": {k: {kk: vv for kk, vv in v.items()} for k, v in r["baseline_vs_full"].items()},
        "season_breakdown": r["season_breakdown"], "player_segments": r["segments"],
        "calibration": r["calibration"], "confidence_breakdown": r["confidence_breakdown"],
    }
    return results


if __name__ == "__main__":
    out = build_full_results()
    proc = subprocess.run([sys.executable, "-m", "unittest", "discover", "tests"],
                           cwd=str(REPO_ROOT), capture_output=True, text=True)
    out["test_suite_stderr_tail"] = "\n".join(proc.stderr.strip().splitlines()[-8:])
    out["test_suite_returncode"] = proc.returncode
    out_path = REPO_ROOT / "research" / "player_blocks_results.json"
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, sort_keys=True, default=str)
    print(f"wrote {out_path}")
    print("test suite returncode:", out["test_suite_returncode"])
    print(out["test_suite_stderr_tail"])
