"""
Player POINTS -- Redesign Cycle 2: Empirical-Baseline Challenge.

EVALUATION STATUS: REUSED HISTORICAL DATA UNDER NEW DEVELOPMENT CYCLE.
2024-25 and 2025-26 were the true-evaluation seasons already consumed by
Cycle 1 (PLAYER_POINTS_VALIDATION_REPORT.md) -- they are NOT pristine
holdout here. This driver instead runs a 3-fold ROLLING-ORIGIN walk-
forward across all 4 real seasons (train-through-season-X, validate
season-X+1), consistent with the real season boundaries
(20222023 -> 20232024 -> 20242025 -> 20252026, verified non-overlapping
in calendar date). The final fold (train through 2024-25, validate
2025-26) is the strongest available check and is called out as such --
but this is POST-HOLDOUT DEVELOPMENT VALIDATION, not first-use evaluation.

Five interpretable candidates (Part 26's cap), directly answering "why
did the old GLM lose":
  C1 -- existing empirical baseline (flat league shrinkage), UNCHANGED
        from Cycle 1's research.run_player_points_model.empirical_threshold_probs
        logic, just re-scoped to each fold's own train pool.
  C2 -- shrunk empirical baseline: PLAYER -> ROLE -> LEAGUE partial
        pooling (research.player_points.hierarchy), nonparametric
        threshold reads.
  C3 -- C2's hierarchical mean as a FIXED OFFSET + a small context
        adjustment (PP role/opponent/team/H2H) fit via offset-GLM
        (research.player_points.redesign), then ONE coherent NegBin
        shape at the adjusted mean (monotonic by construction).
  C4 -- C2's SAME hierarchical mean, but read through a parametric
        NegBin shape instead of the nonparametric empirical CDF --
        isolates shape-misspecification from mean-estimation quality.
  C5 -- Cycle 1's LOCKED Negative Binomial GLM, reused UNCHANGED (not
        refit) as the reference point for "how much better is a good
        player-level mean than pure feature-based pooling."
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
from research.player_points import features as ptf
from research.player_points import hierarchy as ph
from research.player_points import redesign as pr
from research.player_sog import count_models as cm
from research.run_player_sog_model import build_team_schedules, NHL_CORPUS_PATH
import research.run_player_points_model as cycle1

SEASONS_ORDER = [20222023, 20232024, 20242025, 20252026]
FOLDS = [
    {"name": "fold1_train2223_val2324", "train_seasons": [20222023], "val_season": 20232024},
    {"name": "fold2_train222324_val2425", "train_seasons": [20222023, 20232024], "val_season": 20242025},
    {"name": "fold3_train22232425_val2526", "train_seasons": [20222023, 20232024, 20242025], "val_season": 20252026},
]
DEV_SEASON = 20222023
EMPIRICAL_SHRINK_GAMES = 20
THRESHOLDS = (1, 2, 3)
K_PLAYER_GRID = (15, 30)
WINDOW_GRID = (41, 82, None)
CONTEXT_STAGES = [
    ("X0_none", set()), ("X1_pp", {0}), ("X2_pp_opp", {0, 1}),
    ("X3_pp_opp_team", {0, 1, 2}), ("X4_pp_opp_team_h2h", {0, 1, 2, 3}),
]


def flat_empirical_threshold_probs(history, league_rates: dict, thresholds=THRESHOLDS) -> dict:
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


def windowed(history: list[dict], window: int | None) -> list[dict]:
    return history if window is None else history[-window:]


def build_common_context(rows_for_context):
    totals = ptf.build_team_game_points_totals(rows_for_context)
    team_offense_hist = ptf.build_team_offense_history(totals)
    opponent_env = ptf.build_opponent_points_allowed(totals)
    league_avg = statistics.fmean(v["points_for"] for v in totals.values()) if totals else 0.45
    return team_offense_hist, opponent_env, league_avg


def base_example_fields(row, index, team_schedules):
    player_id, team, opponent, date = row["player_id"], row["team"], row["opponent"], row["game_date"]
    history = index.history_as_of(player_id, date)
    if len(history) < 3:
        return None
    team_sched_prior = [g for g in team_schedules.get(team, []) if g["game_date"] < date]
    if not ptf.projected_active(history, team_sched_prior):
        return None
    is_forward = row["position"] in ph.FORWARD_POSITIONS
    pp_icetime_recent = ptf.rolling_pp_mean(history, "icetime_seconds", 10) or 0.0
    role = ph.target_role_tag(is_forward, pp_icetime_recent)
    recent_team_games = team_sched_prior[-ptf.ELIGIBILITY_WINDOW_TEAM_GAMES:]
    appearance_rate = 1.0
    if recent_team_games:
        played_dates = {r["game_date"] for r in history}
        appearance_rate = sum(1 for g in recent_team_games if g["game_date"] in played_dates) / len(recent_team_games)
    toi_window = history[-10:]
    points_window = history[-10:]
    recent_toi_cv = cm.coefficient_of_variation([r["icetime_seconds"] for r in toi_window]) if toi_window else None
    recent_points_cv = cm.coefficient_of_variation([r["points"] for r in points_window]) if points_window else None
    return {
        "player_id": player_id, "player_name": row["player_name"], "game_id": row["game_id"], "game_date": date,
        "season": row["season"], "team": team, "opponent": opponent, "position": row["position"],
        "is_forward": is_forward, "role": role, "pp_icetime_recent": pp_icetime_recent,
        "actual_goals": row["goals"], "actual_assists": row["assists"], "actual_points": row["points"],
        "history": history, "history_len": len(history), "appearance_rate": appearance_rate,
        "recent_toi_cv": recent_toi_cv, "recent_points_cv": recent_points_cv,
    }


def poisson_nll(actual, mu):
    k = int(round(actual)); mu = max(mu, cm.EPS)
    return mu - k * math.log(mu) + math.lgamma(k + 1)


def negbin_nll(actual, mu, alpha):
    k = int(round(actual))
    return -math.log(max(cm.negbinom_pmf(k, mu, alpha), 1e-12))


def threshold_metrics_from_probs(examples, probs_by_t, thresholds=THRESHOLDS):
    per_t = {}
    for t in thresholds:
        briers, loglosses, preds, actuals = [], [], [], []
        for ex, p in zip(examples, probs_by_t[t]):
            y = 1.0 if ex["actual_points"] >= t else 0.0
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


# ============================================================================
# DEV SANDBOX (2022-23 only, internal 70/30 split) -- decide k_player,
# window, and context-feature keep/drop BEFORE any rolling fold is built.
# Fold validation seasons (2023-24, 2024-25, 2025-26) are NEVER touched here.
# ============================================================================

def run_dev_sandbox(rows, index, team_schedules):
    dev_rows = [r for r in rows if r["season"] == DEV_SEASON]
    dev_dates = sorted({r["game_date"] for r in dev_rows})
    split_date = dev_dates[int(len(dev_dates) * 0.7)]
    dev_fit_rows = [r for r in dev_rows if r["game_date"] < split_date]
    dev_select_rows = [r for r in dev_rows if r["game_date"] >= split_date]

    rates = ph.RoleLeagueRates(dev_fit_rows)
    dev_select_examples = []
    for row in dev_select_rows:
        base = base_example_fields(row, index, team_schedules)
        if base is None:
            continue
        dev_select_examples.append(base)

    # ---- grid search k_player x window on dev_select, candidate-2 style ----
    grid_results = {}
    best = None
    for k_player in K_PLAYER_GRID:
        for window in WINDOW_GRID:
            probs_by_t = {t: [] for t in THRESHOLDS}
            for ex in dev_select_examples:
                hist = windowed(ex["history"], window)
                for t in THRESHOLDS:
                    probs_by_t[t].append(ph.player_role_hierarchical_threshold_rate(hist, ex["role"], t, rates, k_player))
            tm = threshold_metrics_from_probs(dev_select_examples, probs_by_t, (1,))
            brier1 = tm[1]["brier"]
            key = f"k{k_player}_w{window}"
            grid_results[key] = {"k_player": k_player, "window": window, "brier_1plus": brier1}
            if best is None or brier1 < best[1]:
                best = (key, brier1)
    best_k_player = grid_results[best[0]]["k_player"]
    best_window = grid_results[best[0]]["window"]

    # ---- context-feature ablation, offset-GLM, dev_fit -> dev_select ----
    team_offense_hist, opponent_env, league_avg = build_common_context(dev_fit_rows)
    dev_fit_examples = []
    for row in dev_fit_rows:
        base = base_example_fields(row, index, team_schedules)
        if base is None:
            continue
        dev_fit_examples.append(base)

    def context_fv_and_mu_base(ex):
        hist = windowed(ex["history"], best_window)
        mu_base = ph.player_role_hierarchical_mean(hist, ex["role"], rates, best_k_player)
        pp_rate = ptf.rolling_pp_mean(ex["history"], "points", 10)
        opp_allowed = ptf.rolling_opponent_points_allowed(opponent_env, ex["opponent"], ex["game_date"], 20)
        opponent_factor = None if opp_allowed is None else opp_allowed / league_avg
        team_offense = ptf.rolling_team_points_for(team_offense_hist, ex["team"], ex["game_date"], 20)
        team_factor = None if team_offense is None else team_offense / league_avg
        h2h_rate, h2h_games = ptf.h2h_shrunk_points_rate(ex["history"], ex["opponent"], mu_base)
        h2h_delta = h2h_rate - mu_base
        fv = pr.context_feature_vector(pp_rate, opponent_factor, team_factor, h2h_delta)
        return fv, mu_base, h2h_games

    fit_fm, fit_offsets, fit_obs = [], [], []
    for ex in dev_fit_examples:
        fv, mu_base, _ = context_fv_and_mu_base(ex)
        fit_fm.append(fv); fit_offsets.append(math.log(max(mu_base, pr.EPS))); fit_obs.append(ex["actual_points"])

    stage_weights = {}
    for name, keep in CONTEXT_STAGES:
        masked = pr.masked_context_matrix(fit_fm, keep)
        stage_weights[name] = pr.fit_poisson_glm_with_offset(masked, fit_obs, fit_offsets)

    select_fv, select_offsets, select_mu_base = [], [], []
    for ex in dev_select_examples:
        fv, mu_base, _ = context_fv_and_mu_base(ex)
        select_fv.append(fv); select_offsets.append(math.log(max(mu_base, pr.EPS))); select_mu_base.append(mu_base)

    stage_mus = {name: [pr.predict_mu_with_offset(stage_weights[name], fv, off) for fv, off in zip(select_fv, select_offsets)]
                 for name, _ in CONTEXT_STAGES}
    stage_probs1 = {name: [cm.poisson_sf_at_least(1, mu) for mu in mus] for name, mus in stage_mus.items()}
    stage_brier1 = {name: [(p - (1.0 if ex["actual_points"] >= 1 else 0.0)) ** 2 for p, ex in zip(probs, dev_select_examples)]
                    for name, probs in stage_probs1.items()}

    names = [s[0] for s in CONTEXT_STAGES]
    context_value_tests = {}
    for i in range(1, len(names)):
        prev, cur = names[i - 1], names[i]
        context_value_tests[f"{prev}_to_{cur}"] = game_clustered_bootstrap(dev_select_examples, stage_brier1[prev], stage_brier1[cur])

    context_verdicts = {
        "pp_added_value": context_value_tests["X0_none_to_X1_pp"]["frac_improved"] >= 0.95,
        "opponent_added_value": context_value_tests["X1_pp_to_X2_pp_opp"]["frac_improved"] >= 0.95,
        "team_added_value": context_value_tests["X2_pp_opp_to_X3_pp_opp_team"]["frac_improved"] >= 0.95,
        "h2h_added_value": context_value_tests["X3_pp_opp_team_to_X4_pp_opp_team_h2h"]["frac_improved"] >= 0.95,
    }
    kept_context_idx = set()
    for i, feat_name in enumerate(pr.CONTEXT_FEATURE_NAMES):
        key = {0: "pp_added_value", 1: "opponent_added_value", 2: "team_added_value", 3: "h2h_added_value"}[i]
        if context_verdicts[key]:
            kept_context_idx.add(i)

    return {
        "dev_split_date": split_date, "dev_fit_n": len(dev_fit_examples), "dev_select_n": len(dev_select_examples),
        "k_player_window_grid": grid_results, "best_k_player": best_k_player, "best_window": best_window,
        "context_stage_results_dev_select": {name: {"mean_brier_1plus": statistics.fmean(b)} for name, b in stage_brier1.items()},
        "context_value_tests": context_value_tests, "context_verdicts": context_verdicts,
        "kept_context_idx": kept_context_idx, "locked_context_stage": "X4_pp_opp_team_h2h",
    }


# ============================================================================
# ROLLING FOLDS
# ============================================================================

def run_fold(fold, rows, index, team_schedules, dev_result, cycle1_globals, cycle1_results):
    train_rows = [r for r in rows if r["season"] in fold["train_seasons"]]
    val_rows = [r for r in rows if r["season"] == fold["val_season"]]

    rates = ph.RoleLeagueRates(train_rows)
    team_offense_hist, opponent_env, league_avg = build_common_context(train_rows)
    k_player, window = dev_result["best_k_player"], dev_result["best_window"]
    locked_context_idx = dev_result["kept_context_idx"]

    league_rates_flat = rates.league_threshold_rate

    def context_fv_and_mu_base(ex):
        hist = windowed(ex["history"], window)
        mu_base = ph.player_role_hierarchical_mean(hist, ex["role"], rates, k_player)
        pp_rate = ptf.rolling_pp_mean(ex["history"], "points", 10)
        opp_allowed = ptf.rolling_opponent_points_allowed(opponent_env, ex["opponent"], ex["game_date"], 20)
        opponent_factor = None if opp_allowed is None else opp_allowed / league_avg
        team_offense = ptf.rolling_team_points_for(team_offense_hist, ex["team"], ex["game_date"], 20)
        team_factor = None if team_offense is None else team_offense / league_avg
        h2h_rate, h2h_games = ptf.h2h_shrunk_points_rate(ex["history"], ex["opponent"], mu_base)
        h2h_delta = h2h_rate - mu_base
        fv = pr.context_feature_vector(pp_rate, opponent_factor, team_factor, h2h_delta)
        return fv, mu_base, h2h_games

    # ---- fit C3's context weights on TRAIN rows of this fold ----
    train_examples = [e for e in (base_example_fields(r, index, team_schedules) for r in train_rows) if e is not None]
    fit_fm, fit_offsets, fit_obs, fit_mu_base = [], [], [], []
    for ex in train_examples:
        fv, mu_base, _ = context_fv_and_mu_base(ex)
        masked_fv = [v if i in locked_context_idx else 0.0 for i, v in enumerate(fv)]
        fit_fm.append(masked_fv); fit_offsets.append(math.log(max(mu_base, pr.EPS)))
        fit_obs.append(ex["actual_points"]); fit_mu_base.append(mu_base)
    context_weights = pr.fit_poisson_glm_with_offset(fit_fm, fit_obs, fit_offsets)
    alpha = cm.fit_negbinom_alpha_by_moments(fit_obs, fit_mu_base)

    # ---- score VALIDATION rows for all 5 candidates ----
    examples = [e for e in (base_example_fields(r, index, team_schedules) for r in val_rows) if e is not None]
    total_target = len(val_rows)
    excluded = total_target - len(examples)

    probs = {c: {t: [] for t in THRESHOLDS} for c in ("C1", "C2", "C3", "C4", "C5")}
    mus = {c: [] for c in ("C2", "C3", "C4", "C5")}
    for ex in examples:
        hist_full = ex["history"]
        hist_win = windowed(hist_full, window)

        c1_probs = flat_empirical_threshold_probs(hist_full, league_rates_flat)
        c2_probs = {t: ph.player_role_hierarchical_threshold_rate(hist_win, ex["role"], t, rates, k_player) for t in THRESHOLDS}
        mu_base = ph.player_role_hierarchical_mean(hist_win, ex["role"], rates, k_player)

        fv, _, h2h_games = context_fv_and_mu_base(ex)
        masked_fv = [v if i in locked_context_idx else 0.0 for i, v in enumerate(fv)]
        mu_c3 = pr.predict_mu_with_offset(context_weights, masked_fv, math.log(max(mu_base, pr.EPS)))
        c3_probs = cm.threshold_probabilities(mu_c3, alpha if alpha > 0.01 else None, THRESHOLDS)
        c4_probs = cm.threshold_probabilities(mu_base, alpha if alpha > 0.01 else None, THRESHOLDS)

        cyc1_ex = cycle1.build_example(
            {"player_id": ex["player_id"], "team": ex["team"], "opponent": ex["opponent"], "game_date": ex["game_date"],
             "season": ex["season"], "player_name": ex["player_name"], "game_id": ex["game_id"],
             "home_or_away": "HOME", "position": ex["position"], "goals": ex["actual_goals"], "assists": ex["actual_assists"],
             "points": ex["actual_points"], "icetime_seconds": ex["history"][-1]["icetime_seconds"] if ex["history"] else 1000.0},
            index, team_schedules, *cycle1_globals)
        if cyc1_ex is not None:
            locked_weights_dict = cycle1_results["stage_weights"][cycle1_results["config"]["locked_stage"]]
            locked_weights = [locked_weights_dict[n] for n in cycle1_results["config"]["feature_names"]]
            mu_c5 = cm.predict_mu(locked_weights, cyc1_ex["feature_vector"])
            c5_alpha = cycle1_results["alpha"] if cycle1_results["alpha"] > 0.01 else None
            c5_probs = cm.threshold_probabilities(mu_c5, c5_alpha, THRESHOLDS)
        else:
            mu_c5 = mu_base
            c5_probs = c4_probs

        ex["h2h_games"] = h2h_games
        ex["mu_c2"] = mu_base
        for t in THRESHOLDS:
            probs["C1"][t].append(c1_probs[t]); probs["C2"][t].append(c2_probs[t])
            probs["C3"][t].append(c3_probs[t]); probs["C4"][t].append(c4_probs[t]); probs["C5"][t].append(c5_probs[t])
        mus["C2"].append(mu_base); mus["C3"].append(mu_c3); mus["C4"].append(mu_base); mus["C5"].append(mu_c5)

    candidate_results = {}
    for c in ("C1", "C2", "C3", "C4", "C5"):
        tm = threshold_metrics_from_probs(examples, probs[c], THRESHOLDS)
        candidate_results[c] = {
            "n": len(examples),
            "thresholds": {str(t): {"n": v["n"], "brier": v["brier"], "log_loss": v["log_loss"],
                                     "actual_rate": v["actual_rate"], "brier_skill_score": skill_score(v["brier"], v["actual_rate"])}
                           for t, v in tm.items()},
            "_briers": {t: v["_briers"] for t, v in tm.items()},
        }

    # ---- headline bootstrap: every candidate vs C1 (existing empirical) ----
    bootstrap_vs_c1 = {}
    date_sensitivity_vs_c1 = {}
    for c in ("C2", "C3", "C4", "C5"):
        bootstrap_vs_c1[c] = game_clustered_bootstrap(examples, candidate_results["C1"]["_briers"][1], candidate_results[c]["_briers"][1])
        date_sensitivity_vs_c1[c] = date_clustered_bootstrap(examples, candidate_results["C1"]["_briers"][1], candidate_results[c]["_briers"][1])

    # ---- overdispersion / hurdle diagnostic for this fold ----
    observed_zero_rate = sum(1 for ex in examples if ex["actual_points"] == 0) / len(examples) if examples else 0.0
    mean_actual = statistics.fmean(ex["actual_points"] for ex in examples) if examples else 0.0
    negbin_p0 = cm.negbinom_pmf(0, mean_actual, alpha) if alpha > 0.01 else cm.poisson_pmf(0, mean_actual)

    return {
        "fold_name": fold["name"], "train_seasons": fold["train_seasons"], "val_season": fold["val_season"],
        "total_target_player_games": total_target, "excluded": excluded, "common_evaluation_n": len(examples),
        "k_player": k_player, "window": window, "alpha": alpha,
        "context_weights": dict(zip(pr.CONTEXT_FEATURE_NAMES, context_weights)),
        "candidate_results": {c: {k: v for k, v in res.items() if not k.startswith("_")} for c, res in candidate_results.items()},
        "bootstrap_vs_c1": bootstrap_vs_c1, "date_sensitivity_vs_c1": date_sensitivity_vs_c1,
        "observed_zero_rate": observed_zero_rate, "negbin_implied_zero_rate": negbin_p0,
        "hurdle_gap": abs(negbin_p0 - observed_zero_rate),
        "_examples": examples, "_candidate_briers": {c: candidate_results[c]["_briers"] for c in candidate_results},
        "_probs": probs,
    }


def run_all():
    rows = ptf.load_points_corpus()
    index = ptf.PlayerHistoryIndex(rows)
    games = ec.load_corpus(str(NHL_CORPUS_PATH))
    team_schedules = build_team_schedules(games)

    cycle1_results = json.loads((REPO_ROOT / "research" / "player_points_results.json").read_text())
    cyc1_totals = ptf.build_team_game_points_totals(rows)
    cyc1_team_offense_hist = ptf.build_team_offense_history(cyc1_totals)
    cyc1_opponent_env = ptf.build_opponent_points_allowed(cyc1_totals)
    cyc1_league_avg = statistics.fmean(v["points_for"] for v in cyc1_totals.values())
    pre_lock_rows = [r for r in rows if r["season"] in (cycle1.WARMUP_SEASON, cycle1.TUNING_SEASON)
                      and r["game_date"] < cycle1.TUNING_SPLIT_DATE]
    cyc1_league_empirical_rates = {t: sum(1 for r in pre_lock_rows if r["points"] >= t) / len(pre_lock_rows) for t in cycle1.THRESHOLDS}
    cycle1_globals = (cyc1_team_offense_hist, cyc1_opponent_env, cyc1_league_avg, cyc1_league_avg, cyc1_league_empirical_rates)

    dev_result = run_dev_sandbox(rows, index, team_schedules)

    fold_results = []
    for fold in FOLDS:
        fr = run_fold(fold, rows, index, team_schedules, dev_result, cycle1_globals, cycle1_results)
        fold_results.append(fr)

    # ---- pooled diagnostics across all 3 folds (Part 3 error decomposition) ----
    pooled_examples = [ex for fr in fold_results for ex in fr["_examples"]]
    pooled_c1 = [b for fr in fold_results for b in fr["_probs"]["C1"][1]]
    pooled_c_best_name = min(("C2", "C3", "C4", "C5"),
                              key=lambda c: statistics.fmean(fr["candidate_results"][c]["thresholds"]["1"]["brier"] for fr in fold_results))
    pooled_best = [p for fr in fold_results for p in fr["_probs"][pooled_c_best_name][1]]

    def bucket_report(buckets):
        out = {}
        for label, keep in buckets.items():
            idx = [i for i, ex in enumerate(pooled_examples) if keep(ex)]
            if not idx:
                continue
            y = [1.0 if pooled_examples[i]["actual_points"] >= 1 else 0.0 for i in idx]
            p_emp = [pooled_c1[i] for i in idx]
            p_new = [pooled_best[i] for i in idx]
            brier_emp = statistics.fmean((p - yy) ** 2 for p, yy in zip(p_emp, y))
            brier_new = statistics.fmean((p - yy) ** 2 for p, yy in zip(p_new, y))
            out[label] = {"n": len(idx), "empirical_brier": brier_emp, "redesign_brier": brier_new,
                          "redesign_minus_empirical": brier_new - brier_emp}
        return out

    baseline_rate_terciles = sorted(ex["mu_c2"] for ex in pooled_examples)
    t1 = baseline_rate_terciles[len(baseline_rate_terciles) // 3]
    t2 = baseline_rate_terciles[2 * len(baseline_rate_terciles) // 3]

    error_decomposition = {
        "by_player_skill_tercile": bucket_report({
            "LOW": lambda ex: ex["mu_c2"] <= t1, "MEDIUM": lambda ex: t1 < ex["mu_c2"] <= t2, "HIGH": lambda ex: ex["mu_c2"] > t2,
        }),
        "by_position": bucket_report({"FORWARD": lambda ex: ex["is_forward"], "DEFENSE": lambda ex: not ex["is_forward"]}),
        "by_pp_role": bucket_report({
            "HIGH_PP": lambda ex: ex["pp_icetime_recent"] > 90.0, "MEDIUM_PP": lambda ex: 0 < ex["pp_icetime_recent"] <= 90.0,
            "LOW_PP": lambda ex: ex["pp_icetime_recent"] == 0.0,
        }),
        "by_history_length": bucket_report({
            "LOW_SAMPLE": lambda ex: ex["history_len"] < 20, "MEDIUM": lambda ex: 20 <= ex["history_len"] < 60,
            "MATURE": lambda ex: ex["history_len"] >= 60,
        }),
    }
    prob_buckets = {}
    for lo in range(0, 100, 10):
        hi = lo + 10
        label = f"{lo}-{hi}%"
        idx = [i for i, p in enumerate(pooled_c1) if lo / 100.0 <= p < hi / 100.0]
        if not idx:
            continue
        y = [1.0 if pooled_examples[i]["actual_points"] >= 1 else 0.0 for i in idx]
        p_emp = [pooled_c1[i] for i in idx]
        p_new = [pooled_best[i] for i in idx]
        prob_buckets[label] = {"n": len(idx), "mean_pred_empirical": statistics.fmean(p_emp),
                               "actual_rate": statistics.fmean(y), "mean_pred_redesign": statistics.fmean(p_new)}
    error_decomposition["by_probability_range_empirical_1plus"] = prob_buckets

    by_threshold = {}
    for t in THRESHOLDS:
        pc1 = [p for fr in fold_results for p in fr["_probs"]["C1"][t]]
        pbest = [p for fr in fold_results for p in fr["_probs"][pooled_c_best_name][t]]
        y = [1.0 if ex["actual_points"] >= t else 0.0 for ex in pooled_examples]
        brier_emp = statistics.fmean((p - yy) ** 2 for p, yy in zip(pc1, y))
        brier_new = statistics.fmean((p - yy) ** 2 for p, yy in zip(pbest, y))
        by_threshold[str(t)] = {"empirical_brier": brier_emp, "redesign_brier": brier_new}
    error_decomposition["by_threshold"] = by_threshold
    error_decomposition["best_redesign_candidate"] = pooled_c_best_name

    # ---- confidence + conservative-probability diagnostics on pooled set, C1 vs best ----
    for ex in pooled_examples:
        label, pos, risk = cm.confidence_score(ex["history_len"], ex["recent_toi_cv"], ex["recent_points_cv"],
                                                20, 20, ex["appearance_rate"])
        ex["confidence"] = label

    confidence_diag = {}
    for label in ("HIGH", "MEDIUM", "LOW"):
        idx = [i for i, ex in enumerate(pooled_examples) if ex["confidence"] == label]
        if not idx:
            continue
        y = [1.0 if pooled_examples[i]["actual_points"] >= 1 else 0.0 for i in idx]
        p_emp = [pooled_c1[i] for i in idx]
        p_new = [pooled_best[i] for i in idx]
        brier_emp = statistics.fmean((p - yy) ** 2 for p, yy in zip(p_emp, y))
        brier_new = statistics.fmean((p - yy) ** 2 for p, yy in zip(p_new, y))
        ar = statistics.fmean(y)
        confidence_diag[label] = {"n": len(idx), "actual_rate_1plus": ar,
                                   "empirical_brier": brier_emp, "empirical_skill": skill_score(brier_emp, ar),
                                   "redesign_brier": brier_new, "redesign_skill": skill_score(brier_new, ar),
                                   "mean_history_len": statistics.fmean(pooled_examples[i]["history_len"] for i in idx),
                                   "mean_toi_cv": statistics.fmean(pooled_examples[i]["recent_toi_cv"] for i in idx
                                                                    if pooled_examples[i]["recent_toi_cv"] is not None)
                                   if any(pooled_examples[i]["recent_toi_cv"] is not None for i in idx) else None}

    conservative_mus = [cm.conservative_mu(ex["mu_c2"], min(ex["history_len"], 20)) for ex in pooled_examples]
    conservative_probs1 = [cm.poisson_sf_at_least(1, m) for m in conservative_mus]
    frac_conservative_leq_raw = sum(1 for c, r in zip(conservative_probs1, pooled_c1) if c <= r + 1e-9) / len(pooled_examples)

    # ---- 3+ support, new pre-specified standard (Part 28) ----
    n3 = sum(1 for ex in pooled_examples if ex["actual_points"] >= 3)
    n3_by_conf = {lab: sum(1 for ex in pooled_examples if ex["actual_points"] >= 3 and ex["confidence"] == lab)
                  for lab in ("HIGH", "MEDIUM", "LOW")}
    three_plus_status = "SUPPORTED" if (n3 >= 500 and all(v >= 50 for v in n3_by_conf.values())) else "INSUFFICIENT_DATA"

    # ---- representative examples (mechanical, non-cherry-picked) ----
    def find_example(pred):
        for ex, p_e, p_n in zip(pooled_examples, pooled_c1, pooled_best):
            if pred(ex, p_e, p_n):
                return ex, p_e, p_n
        return None

    reps_raw = {
        "empirical_clearly_wins": find_example(lambda ex, pe, pn: abs(pe - (1.0 if ex["actual_points"] >= 1 else 0.0)) < 0.2 and abs(pn - (1.0 if ex["actual_points"] >= 1 else 0.0)) > 0.4),
        "redesign_clearly_wins": find_example(lambda ex, pe, pn: abs(pn - (1.0 if ex["actual_points"] >= 1 else 0.0)) < 0.2 and abs(pe - (1.0 if ex["actual_points"] >= 1 else 0.0)) > 0.4),
        "high_pp_player": find_example(lambda ex, pe, pn: ex["pp_icetime_recent"] > 120.0),
        "defenseman": find_example(lambda ex, pe, pn: not ex["is_forward"] and ex["mu_c2"] > 0.3),
        "elite_scorer": find_example(lambda ex, pe, pn: ex["mu_c2"] > 1.0),
        "low_sample_player": find_example(lambda ex, pe, pn: ex["history_len"] < 15),
        "low_confidence_failure": find_example(lambda ex, pe, pn: ex["confidence"] == "LOW" and abs(pn - (1.0 if ex["actual_points"] >= 1 else 0.0)) > 0.5),
        "high_confidence_success": find_example(lambda ex, pe, pn: ex["confidence"] == "HIGH" and abs(pn - (1.0 if ex["actual_points"] >= 1 else 0.0)) < 0.2),
    }
    representative_examples = {}
    for name, r in reps_raw.items():
        if r is None:
            representative_examples[name] = None
            continue
        ex, p_e, p_n = r
        representative_examples[name] = {
            "player": ex["player_name"], "team": ex["team"], "opponent": ex["opponent"], "game_date": ex["game_date"],
            "empirical_p_1plus": round(p_e, 3), "redesign_p_1plus": round(p_n, 3), "confidence": ex["confidence"],
            "actual_points": ex["actual_points"], "history_len": ex["history_len"],
        }

    test_proc = subprocess.run([sys.executable, "-m", "unittest", "discover", "tests"], cwd=str(REPO_ROOT), capture_output=True, text=True)

    out = {
        "evaluation_status": "REUSED HISTORICAL DATA UNDER NEW DEVELOPMENT CYCLE",
        "dev_sandbox": {k: v for k, v in dev_result.items() if k != "kept_context_idx"},
        "dev_sandbox_kept_context_idx": sorted(dev_result["kept_context_idx"]),
        "folds": [{k: v for k, v in fr.items() if not k.startswith("_")} for fr in fold_results],
        "error_decomposition": error_decomposition,
        "confidence_diagnostics": confidence_diag,
        "conservative_probability_check": {"fraction_conservative_leq_raw": frac_conservative_leq_raw},
        "three_plus_support": {"total_3plus_events": n3, "events_per_confidence_bucket": n3_by_conf, "status": three_plus_status},
        "representative_examples": representative_examples,
        "test_suite_returncode": test_proc.returncode,
        "test_suite_stderr_tail": "\n".join(test_proc.stderr.strip().splitlines()[-8:]),
    }
    return out


if __name__ == "__main__":
    out = run_all()
    out_path = REPO_ROOT / "research" / "player_points_redesign_results.json"
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, sort_keys=True, default=str)
    print(f"wrote {out_path}")
    print("test suite returncode:", out["test_suite_returncode"])
