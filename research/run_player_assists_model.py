"""
Driver for the Player ASSISTS Probability Foundation -- the third prop
model on the shared framework. Assists are far sparser than SOG/blocks
(real corpus: 76.4% zero-assist games) and only mildly overdispersed
(variance/mean ~1.09 on the real 2024 season sample) -- both audited
directly from the real corpus before modeling, not assumed. Headline
thresholds are 1+/2+ only (3+ is too rare in the real data to evaluate
meaningfully with this corpus size -- see the report's own coverage
numbers for the actual count at 3+).

Same reuse pattern as research/run_player_blocks_model.py: the Poisson/
NegBin math, GLM fit, confidence scoring, and conservative bound all
come directly from research/player_sog/count_models.py, unmodified.
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
from research.player_assists import features as af
from research.player_sog import count_models as cm
from research.run_player_sog_model import build_team_schedules, NHL_CORPUS_PATH

TUNING_SEASON = 20232024
EVAL_SEASONS = [20242025, 20252026]
BASELINE_WINDOW = 20
RECENT_WINDOW_5 = 5
RECENT_WINDOW_10 = 10
TOI_RECENT_WINDOW = 10
OPPONENT_WINDOW = 20
THRESHOLDS = (1, 2, 3)
HEADLINE_THRESHOLDS = (1, 2)

STAGES = [
    ("M0_baseline_only", {0, 1}), ("M1_plus_recent_form", {0, 1, 2}),
    ("M2_plus_toi_role", {0, 1, 2, 3}), ("M3_plus_opponent", {0, 1, 2, 3, 4}),
    ("M4_plus_h2h", {0, 1, 2, 3, 4, 5}),
]


def masked_matrix(fm, keep_idx):
    return [[v if i in keep_idx else 0.0 for i, v in enumerate(fv)] for fv in fm]


def build_example(row, player_index, team_schedules, opponent_env, league_avg_points_allowed):
    player_id, team, opponent, date = row["player_id"], row["team"], row["opponent"], row["game_date"]
    history = player_index.history_as_of(player_id, date)
    if len(history) < 3:
        return None
    team_sched_prior = [g for g in team_schedules.get(team, []) if g["game_date"] < date]
    if not af.projected_active(history, team_sched_prior):
        return None

    baseline_rate = af.rolling_mean(history, "assists", BASELINE_WINDOW) or af.season_to_date_mean(history, "assists", row["season"]) or 0.15
    if baseline_rate <= 0:
        baseline_rate = 0.15
    recent_rate5 = af.rolling_mean(history, "assists", RECENT_WINDOW_5)
    recent_rate10 = af.rolling_mean(history, "assists", RECENT_WINDOW_10)
    recent_toi = af.rolling_mean(history, "icetime_seconds", TOI_RECENT_WINDOW)
    baseline_toi = af.rolling_mean(history, "icetime_seconds", BASELINE_WINDOW)

    opp_hist = af.opponent_history_as_of(opponent_env, opponent, date)
    opp_allowed = af.rolling_opponent_points_allowed(opponent_env, opponent, date, OPPONENT_WINDOW)
    opponent_factor = None if opp_allowed is None else opp_allowed / league_avg_points_allowed

    h2h_rate, h2h_games = af.h2h_shrunk_assists_rate(history, opponent, baseline_rate)
    h2h_delta = h2h_rate - baseline_rate

    recent_team_games = team_sched_prior[-af.ELIGIBILITY_WINDOW_TEAM_GAMES:]
    appearance_rate = 1.0
    if recent_team_games:
        played_dates = {r["game_date"] for r in history}
        appearance_rate = sum(1 for g in recent_team_games if g["game_date"] in played_dates) / len(recent_team_games)

    toi_window = history[-TOI_RECENT_WINDOW:]
    assists_window = history[-RECENT_WINDOW_10:]
    recent_toi_cv = cm.coefficient_of_variation([r["icetime_seconds"] for r in toi_window]) if toi_window else None
    recent_assists_cv = cm.coefficient_of_variation([r["assists"] for r in assists_window]) if assists_window else None

    fv = cm.build_feature_vector(baseline_rate, recent_rate5, recent_toi, baseline_toi, opponent_factor, h2h_delta)
    season_rate = af.season_to_date_mean(history, "assists", row["season"])

    return {"player_id": player_id, "game_id": row["game_id"], "game_date": date, "season": row["season"],
            "actual_assists": row["assists"], "history_len": len(history), "baseline_rate": baseline_rate,
            "recent_rate10": recent_rate10, "opponent_window_games": len(opp_hist),
            "appearance_rate": appearance_rate, "recent_toi_cv": recent_toi_cv,
            "recent_assists_cv": recent_assists_cv, "feature_vector": fv, "season_rate": season_rate}


def poisson_nll(actual, mu):
    k = int(round(actual)); mu = max(mu, cm.EPS)
    return mu - k * math.log(mu) + math.lgamma(k + 1)


def negbin_nll(actual, mu, alpha):
    k = int(round(actual))
    return -math.log(max(cm.negbinom_pmf(k, mu, alpha), 1e-12))


def threshold_prob(mu, alpha, t):
    return cm.negbinom_sf_at_least(t, mu, alpha) if alpha else cm.poisson_sf_at_least(t, mu)


def threshold_metrics(examples, mus, alpha, thresholds=HEADLINE_THRESHOLDS):
    per_t = {t: {"briers": [], "loglosses": [], "preds": [], "actuals": []} for t in thresholds}
    for ex, mu in zip(examples, mus):
        k = int(round(ex["actual_assists"]))
        for t in thresholds:
            p = threshold_prob(mu, alpha, t)
            y = 1.0 if k >= t else 0.0
            per_t[t]["briers"].append((p - y) ** 2)
            per_t[t]["loglosses"].append(-(y * math.log(max(p, 1e-12)) + (1 - y) * math.log(max(1 - p, 1e-12))))
            per_t[t]["preds"].append(p); per_t[t]["actuals"].append(y)
    return {t: {"n": len(examples), "brier": statistics.fmean(v["briers"]), "log_loss": statistics.fmean(v["loglosses"]),
                "_briers": v["briers"], "mean_pred": statistics.fmean(v["preds"]), "actual_rate": statistics.fmean(v["actuals"])}
            for t, v in per_t.items()}


def mean_abs_error(mus, actuals):
    return statistics.fmean(abs(m - a) for m, a in zip(mus, actuals))


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


def run_all():
    rows = af.load_assists_corpus()
    index = af.PlayerHistoryIndex(rows)
    totals = af.build_team_game_points_totals(rows)
    opponent_env = af.build_opponent_points_allowed(totals)
    league_avg_points_allowed = statistics.fmean(v["points_for"] for v in totals.values())
    games = ec.load_corpus(str(NHL_CORPUS_PATH))
    team_schedules = build_team_schedules(games)

    examples_by_season = defaultdict(list)
    excluded = {"insufficient_history": 0, "not_projected_active": 0}
    for row in rows:
        if row["season"] not in (TUNING_SEASON, *EVAL_SEASONS):
            continue
        ex = build_example(row, index, team_schedules, opponent_env, league_avg_points_allowed)
        if ex is None:
            history = index.history_as_of(row["player_id"], row["game_date"])
            excluded["insufficient_history" if len(history) < 3 else "not_projected_active"] += 1
            continue
        examples_by_season[row["season"]].append(ex)

    tuning = examples_by_season[TUNING_SEASON]
    eval_examples = [ex for s in EVAL_SEASONS for ex in examples_by_season[s]]

    fit_pool = tuning if len(tuning) <= 12000 else random.Random(20232024).sample(tuning, 12000)
    tuning_fm = [ex["feature_vector"] for ex in fit_pool]
    tuning_obs = [ex["actual_assists"] for ex in fit_pool]
    stage_weights = {name: cm.fit_poisson_glm(masked_matrix(tuning_fm, keep), tuning_obs) for name, keep in STAGES}

    headline_stage = "M4_plus_h2h"
    tuning_mu_full = [cm.predict_mu(stage_weights[headline_stage], fv) for fv in tuning_fm]
    alpha = cm.fit_negbinom_alpha_by_moments(tuning_obs, tuning_mu_full)
    overdispersion = cm.overdispersion_stats([ex["actual_assists"] for ex in tuning])

    eval_fm = [ex["feature_vector"] for ex in eval_examples]
    eval_obs = [ex["actual_assists"] for ex in eval_examples]
    stage_mus = {name: [cm.predict_mu(stage_weights[name], fv) for fv in eval_fm] for name, _ in STAGES}

    baseline_a_mus = [ex["season_rate"] if ex["season_rate"] else ex["baseline_rate"] for ex in eval_examples]
    baseline_a_mus = [m if m and m > 0 else 0.15 for m in baseline_a_mus]

    def eval_candidate(mus, alpha_val=None, thresholds=HEADLINE_THRESHOLDS):
        tm = threshold_metrics(eval_examples, mus, alpha_val, thresholds)
        return {"n": len(eval_examples),
                "nll_mean": statistics.fmean((negbin_nll(a, m, alpha_val) if alpha_val else poisson_nll(a, m)) for a, m in zip(eval_obs, mus)),
                "mae": mean_abs_error(mus, eval_obs),
                "thresholds": {str(t): {"n": v["n"], "brier": v["brier"], "log_loss": v["log_loss"],
                                         "actual_rate": v["actual_rate"], "brier_skill_score": skill_score(v["brier"], v["actual_rate"])}
                               for t, v in tm.items()},
                "_threshold_briers": {t: v["_briers"] for t, v in tm.items()}}

    headline_poisson = eval_candidate(stage_mus[headline_stage], None, THRESHOLDS)
    headline_negbin = eval_candidate(stage_mus[headline_stage], alpha, THRESHOLDS)
    stage_results = {name: eval_candidate(stage_mus[name]) for name, _ in STAGES}
    baseline_results = {"A_season_average": eval_candidate(baseline_a_mus)}

    value_tests = {}
    names = [s[0] for s in STAGES]
    for i in range(1, len(names)):
        prev, cur = names[i - 1], names[i]
        value_tests[f"{prev}_to_{cur}"] = paired_bootstrap_fast(
            stage_results[prev]["_threshold_briers"][1], stage_results[cur]["_threshold_briers"][1])

    baseline_vs_full = {name: paired_bootstrap_fast(res["_threshold_briers"][1], stage_results[headline_stage]["_threshold_briers"][1])
                         for name, res in baseline_results.items()}

    for ex, mu in zip(eval_examples, stage_mus[headline_stage]):
        label, pos, risk = cm.confidence_score(ex["history_len"], ex["recent_toi_cv"], ex["recent_assists_cv"],
                                                ex["opponent_window_games"], OPPONENT_WINDOW, ex["appearance_rate"])
        ex["confidence"] = label

    confidence_breakdown = {}
    for label in ("HIGH", "MEDIUM", "LOW"):
        idx = [i for i, ex in enumerate(eval_examples) if ex["confidence"] == label]
        if not idx:
            continue
        sub_mus = [stage_mus[headline_stage][i] for i in idx]
        sub_examples = [eval_examples[i] for i in idx]
        tm = threshold_metrics(sub_examples, sub_mus, None, HEADLINE_THRESHOLDS)
        confidence_breakdown[label] = {"n": len(idx), "thresholds": {
            str(t): {"brier": v["brier"], "actual_rate": v["actual_rate"], "brier_skill_score": skill_score(v["brier"], v["actual_rate"])}
            for t, v in tm.items()}}

    return {
        "corpus_size": len(rows), "eval_examples_n": len(eval_examples), "tuning_examples_n": len(tuning),
        "excluded": excluded, "overdispersion": overdispersion, "alpha": alpha,
        "stage_weights": {name: dict(zip(cm.FEATURE_NAMES, w)) for name, w in stage_weights.items()},
        "headline_poisson": {k: v for k, v in headline_poisson.items() if not k.startswith("_")},
        "headline_negbin": {k: v for k, v in headline_negbin.items() if not k.startswith("_")},
        "stage_results": {name: {k: v for k, v in res.items() if not k.startswith("_")} for name, res in stage_results.items()},
        "baseline_results": {name: {k: v for k, v in res.items() if not k.startswith("_")} for name, res in baseline_results.items()},
        "value_tests_threshold1": value_tests, "baseline_vs_full_threshold1": baseline_vs_full,
        "confidence_breakdown": confidence_breakdown,
    }


if __name__ == "__main__":
    out = run_all()
    proc = subprocess.run([sys.executable, "-m", "unittest", "discover", "tests"], cwd=str(REPO_ROOT), capture_output=True, text=True)
    out["test_suite_returncode"] = proc.returncode
    out["test_suite_stderr_tail"] = "\n".join(proc.stderr.strip().splitlines()[-8:])
    out_path = REPO_ROOT / "research" / "player_assists_results.json"
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, sort_keys=True, default=str)
    print(f"wrote {out_path}")
    print("test suite returncode:", out["test_suite_returncode"])
