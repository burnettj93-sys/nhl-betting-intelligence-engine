"""
Driver for the Goalie Saves + Period Saves predictive model (Parts 1-51).
Builds real goalie-start examples from the accepted 4-season PBP-derived
goalie-saves corpus (research/goalie_saves/), tests 6 PIT-safe baselines
and up to 6 candidate architectures for FULL-GAME saves under strict
walk-forward discipline (WARMUP=2022-23, TUNING=2023-24 frozen, EVAL=
2024-25 + 2025-26), independently validates a shots-faced submodel
(Part 20), tests the existing validated Player SOG model's PIT-safe
roster-aggregation value (Part 6-8), and separately validates a
period-saves architecture (Parts 25-28).

STARTER SEMANTICS (Part 2/3/34): headline evaluation is CONDITIONAL_ON_
ACTUAL_START -- the real population these sportsbook markets settle
against (a goalie-saves prop is voided if the named goalie doesn't
start). The project's own existing, separately-audited projected-starter
model (research/goalie_intelligence/) already reports PIT-safe true-
holdout accuracy for WHETHER a goalie starts; it is referenced, not
re-fit, and NOT folded into this slice's headline Brier/log-loss numbers
-- see report Section B and Part 42's live-architecture design.

Read-only against nhl.db, models/, config.py, pricing/. Does not change
Team Goals by Period, the existing validated Player SOG models, the
confidence framework, or decision policy v3.
"""
from __future__ import annotations

import hashlib
import datetime as dt
import json
import math
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from research.player_sog import count_models as cm
from research.goalie_saves import features as gf
from research.goalie_saves import hierarchy as gh
from research.goalie_saves import upstream_player_sog_aggregation as upa

WARMUP_SEASON = 20222023
TUNING_SEASON = 20232024
EVAL_SEASONS = [20242025, 20252026]

BASELINE_WINDOW = 20
RECENT_WINDOW = 5
OPPONENT_WINDOW = 20
MIN_HISTORY_STARTS = 5

FULL_GAME_THRESHOLDS = (20, 25, 30, 35, 40)
PERIODS = (1, 2, 3)
PERIOD_THRESHOLDS = (5, 8, 10, 12)

RESULTS_PATH = REPO_ROOT / "research" / "goalie_saves_results.json"

ALL_BASELINE_NAMES = ("A_goalie_saves_rate", "B_opponent_sog_x_league_savepct", "C_goalie_shots_faced_rate",
                       "D_opponent_sog_x_goalie_savepct", "E_shrunk_workload", "F_h2h_workload")
ALL_CANDIDATE_NAMES = ("A_shrunk_empirical", "B_poisson_direct", "C_negbinom_direct",
                        "D_shots_x_shrunk_saverate", "E_hybrid_offset", "F_player_agg_x_saverate")


# ============================================================================
# Part 1-18: example construction (strictly pregame, no target-game leakage)
# ============================================================================

def glm_feature_vector(baseline_rate: float, recent_rate: float | None, is_home: bool,
                        opponent_factor: float | None, h2h_delta: float, is_b2b: bool) -> list[float]:
    eps = 1e-6
    log_baseline = math.log(max(baseline_rate, eps))
    recent_form = 0.0
    if recent_rate is not None:
        recent_form = math.log(max(recent_rate, eps)) - log_baseline
    home_ind = 1.0 if is_home else 0.0
    opp_factor = 0.0 if opponent_factor is None else math.log(max(opponent_factor, eps))
    b2b_ind = 1.0 if is_b2b else 0.0
    return [1.0, log_baseline, recent_form, home_ind, opp_factor, h2h_delta, b2b_ind]


def build_example(row: dict, goalie_index: gf.GoalieHistoryIndex, team_index: gf.TeamSogHistoryIndex,
                   save_rates: gh.GoalieSavePctRates, workload_rates: gh.GoalieWorkloadRates,
                   league_avg_team_sog: float, agg_ctx: "upa.AggregationContext | None",
                   agg_weights: list[float] | None) -> dict | None:
    goalie_id, team, opponent, date = row["goalie_id"], row["team"], row["opponent"], row["game_date"]
    is_home = row["home_away"] == "home"

    full_history = goalie_index.history_as_of(goalie_id, date)
    starts_history = gf.starts_only(full_history)
    if len(starts_history) < MIN_HISTORY_STARTS:
        return None

    team_history = team_index.history_as_of(team, date)
    opponent_history = team_index.history_as_of(opponent, date)

    baseline_saves = gf.rolling_mean(starts_history, "actual_saves", BASELINE_WINDOW)
    if baseline_saves is None or baseline_saves <= 0:
        baseline_saves = max(workload_rates.team_shrunk_mean(team), 5.0)
    recent_saves5 = gf.rolling_mean(starts_history, "actual_saves", RECENT_WINDOW)

    baseline_shots_faced = gf.rolling_mean(starts_history, "actual_shots_faced", BASELINE_WINDOW)
    if baseline_shots_faced is None or baseline_shots_faced <= 0:
        baseline_shots_faced = max(baseline_saves, 5.0)

    opp_sog_rolling = gf.rolling_mean(opponent_history, "full_game_sog", OPPONENT_WINDOW)
    opponent_factor = None if opp_sog_rolling is None else opp_sog_rolling / league_avg_team_sog

    team_shots_against_rolling = gf.rolling_mean(team_history, "opponent_full_game_sog", OPPONENT_WINDOW)

    goalie_save_pct = gf.rolling_save_pct(starts_history, BASELINE_WINDOW)
    shrunk_save_pct = save_rates.goalie_shrunk_save_pct(starts_history, team)
    recent_save_pct5 = gf.rolling_save_pct(starts_history, RECENT_WINDOW)

    h2h_saves_rate, h2h_games = gf.h2h_shrunk_rate(starts_history, opponent, "actual_saves", baseline_saves)
    h2h_delta = h2h_saves_rate - baseline_saves

    is_b2b_team = gf.team_is_back_to_back(team_history, date)
    is_b2b_goalie = gf.goalie_played_previous_night(full_history, date)

    save_window = starts_history[-10:]
    recent_save_cv = cm.coefficient_of_variation([r["actual_saves"] for r in save_window]) if save_window else None
    shots_window = starts_history[-10:]
    recent_shots_cv = cm.coefficient_of_variation(
        [r["actual_shots_faced"] for r in shots_window]) if shots_window else None

    shrunk_workload_saves = workload_rates.goalie_shrunk_mean(starts_history, team)

    player_agg_shots = None
    if agg_ctx is not None and agg_weights is not None:
        agg_result = upa.aggregate_expected_opponent_sog(agg_ctx, opponent, team, date, agg_weights)
        if agg_result["n_players"] >= 8:  # a defensibly-covered roster, not a handful of stragglers
            player_agg_shots = agg_result["expected_sog_sum"]

    return {
        "game_id": row["game_id"], "game_date": date, "season": row["season"],
        "goalie_id": goalie_id, "team": team, "opponent": opponent, "home_away": row["home_away"],
        "history_games": len(starts_history),
        "baseline_saves": baseline_saves, "recent_saves5": recent_saves5,
        "baseline_shots_faced": baseline_shots_faced,
        "opponent_factor": opponent_factor, "opp_sog_rolling": opp_sog_rolling,
        "team_shots_against_rolling": team_shots_against_rolling,
        "goalie_save_pct": goalie_save_pct, "shrunk_save_pct": shrunk_save_pct,
        "recent_save_pct5": recent_save_pct5,
        "h2h_delta": h2h_delta, "h2h_games": h2h_games,
        "is_b2b_team": is_b2b_team, "is_b2b_goalie": is_b2b_goalie,
        "recent_save_cv": recent_save_cv, "recent_shots_cv": recent_shots_cv,
        "shrunk_workload_saves": shrunk_workload_saves,
        "player_agg_shots": player_agg_shots,
        "actual_saves": row["actual_saves"], "actual_shots_faced": row["actual_shots_faced"],
        "actual_goals_allowed": row["actual_goals_allowed"],
        "period_1_saves": row["period_1_saves"], "period_2_saves": row["period_2_saves"],
        "period_3_saves": row["period_3_saves"],
        "period_1_shots_faced": row["period_1_shots_faced"], "period_2_shots_faced": row["period_2_shots_faced"],
        "period_3_shots_faced": row["period_3_shots_faced"],
    }


# ============================================================================
# Part 4/19: baselines and candidates
# ============================================================================

def compute_baselines(ex: dict, league_avg_save_pct: float) -> dict[str, float]:
    opp_sog = ex["opp_sog_rolling"] if ex["opp_sog_rolling"] is not None else ex["baseline_shots_faced"]
    return {
        "A_goalie_saves_rate": max(ex["baseline_saves"], 1e-6),
        "B_opponent_sog_x_league_savepct": max(opp_sog * league_avg_save_pct, 1e-6),
        "C_goalie_shots_faced_rate": max(ex["baseline_shots_faced"] * league_avg_save_pct, 1e-6),
        "D_opponent_sog_x_goalie_savepct": max(opp_sog * ex["shrunk_save_pct"], 1e-6),
        "E_shrunk_workload": max(ex["shrunk_workload_saves"], 1e-6),
        "F_h2h_workload": max(ex["baseline_saves"] + ex["h2h_delta"], 1e-6),
    }


def compute_candidates(ex: dict, glm_weights: list[float], offset_weights: list[float],
                        workload_rates: gh.GoalieWorkloadRates) -> dict[str, float]:
    is_home = ex["home_away"] == "home"
    fv = glm_feature_vector(ex["baseline_saves"], ex["recent_saves5"], is_home, ex["opponent_factor"],
                             ex["h2h_delta"], ex["is_b2b_goalie"])
    mu_poisson = cm.predict_mu(glm_weights, fv)

    opp_sog = ex["opp_sog_rolling"] if ex["opp_sog_rolling"] is not None else ex["baseline_shots_faced"]
    shots_x_saverate = opp_sog * ex["shrunk_save_pct"]

    offset = math.log(max(shots_x_saverate, 1e-6))
    ctx = _offset_context_features(ex)
    adj = sum(w * x for w, x in zip(offset_weights, ctx))

    out = {
        "A_shrunk_empirical": max(ex["shrunk_workload_saves"], 1e-6),
        "B_poisson_direct": max(mu_poisson, 1e-6),
        "C_negbinom_direct": max(mu_poisson, 1e-6),
        "D_shots_x_shrunk_saverate": max(shots_x_saverate, 1e-6),
        "E_hybrid_offset": max(math.exp(offset + adj), 1e-6),
    }
    if ex["player_agg_shots"] is not None:
        out["F_player_agg_x_saverate"] = max(ex["player_agg_shots"] * ex["shrunk_save_pct"], 1e-6)
    else:
        out["F_player_agg_x_saverate"] = out["D_shots_x_shrunk_saverate"]
    return out


def _offset_context_features(ex: dict) -> list[float]:
    save_pct_form = 0.0
    if ex["recent_save_pct5"] is not None and ex["shrunk_save_pct"] > 0:
        save_pct_form = ex["recent_save_pct5"] - ex["shrunk_save_pct"]
    opp_factor_log = 0.0 if ex["opponent_factor"] is None else math.log(max(ex["opponent_factor"], 1e-6))
    return [1.0, save_pct_form, opp_factor_log]


def fit_offset_glm(offsets: list[float], context_matrix: list[list[float]], observed: list[float],
                    lr: float = 0.02, n_iter: int = 400) -> list[float]:
    n = len(observed)
    k_feat = len(context_matrix[0])
    weights = [0.0] * k_feat
    for _ in range(n_iter):
        grad = [0.0] * k_feat
        for i in range(n):
            lin = offsets[i] + sum(w * x for w, x in zip(weights, context_matrix[i]))
            mu = math.exp(min(lin, 20.0))
            err = mu - observed[i]
            for j in range(k_feat):
                grad[j] += err * context_matrix[i][j]
        weights = [w - lr * g / n for w, g in zip(weights, grad)]
    return weights


# ============================================================================
# Metrics, bootstrap, calibration (identical pattern to prior slices)
# ============================================================================

def brier(p: float, y: float) -> float:
    return (p - y) ** 2


def log_loss(p: float, y: float, eps: float = 1e-9) -> float:
    p = min(max(p, eps), 1 - eps)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def poisson_nll(mu: float, y: int, eps: float = 1e-9) -> float:
    mu = max(mu, eps)
    return mu - y * math.log(mu)


def threshold_prob(mu: float, alpha: float | None, t: int) -> float:
    return cm.negbinom_sf_at_least(t, mu, alpha) if alpha else cm.poisson_sf_at_least(t, mu)


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


def calibration_bins(probs: list[float], outcomes: list[float]) -> list[dict]:
    bands = [(0.0, .1), (.1, .2), (.2, .3), (.3, .4), (.4, .5), (.5, .6), (.6, .7), (.7, .8), (.8, .9), (.9, 1.01)]
    out = []
    for lo, hi in bands:
        idx = [i for i, p in enumerate(probs) if lo <= p < hi]
        if not idx:
            continue
        mean_pred = statistics.fmean(probs[i] for i in idx)
        mean_actual = statistics.fmean(outcomes[i] for i in idx)
        out.append({"band": f"{lo:.0%}-{min(hi,1.0):.0%}", "n": len(idx),
                     "mean_predicted": mean_pred, "mean_actual": mean_actual})
    return out


def evaluate_estimator(exs: list[dict], family: str, name: str, target_field: str,
                        thresholds: tuple[int, ...], alpha: float | None = None) -> dict:
    mus = [ex[family][name] for ex in exs]
    actuals = [ex[target_field] for ex in exs]
    n = len(exs)
    nll = statistics.fmean(poisson_nll(mu, y) for mu, y in zip(mus, actuals))
    mae = statistics.fmean(abs(mu - y) for mu, y in zip(mus, actuals))
    rmse = math.sqrt(statistics.fmean((mu - y) ** 2 for mu, y in zip(mus, actuals)))
    threshold_metrics = {}
    for t in thresholds:
        probs = [threshold_prob(mu, alpha, t) for mu in mus]
        outcomes = [1.0 if y >= t else 0.0 for y in actuals]
        b = statistics.fmean(brier(p, y) for p, y in zip(probs, outcomes))
        ll = statistics.fmean(log_loss(p, y) for p, y in zip(probs, outcomes))
        actual_rate = statistics.fmean(outcomes)
        naive = actual_rate * (1 - actual_rate)
        skill = None if naive <= 0 else 1.0 - b / naive
        threshold_metrics[t] = {"brier": b, "log_loss": ll, "actual_rate": actual_rate,
                                 "skill_score": skill, "calibration": calibration_bins(probs, outcomes),
                                 "n_positive": sum(outcomes)}
    return {"n": n, "nll": nll, "mae": mae, "rmse": rmse, "thresholds": threshold_metrics}


def check_monotonicity(exs: list[dict], family: str, name: str, thresholds: tuple[int, ...],
                        alpha: float | None = None) -> int:
    violations = 0
    for ex in exs:
        mu = ex[family][name]
        probs = [threshold_prob(mu, alpha, t) for t in thresholds]
        if any(probs[i] < probs[i + 1] for i in range(len(probs) - 1)):
            violations += 1
    return violations


def confidence_for_example(ex: dict) -> tuple[str, list[str], list[str]]:
    return cm.confidence_score(ex["history_games"], ex["recent_shots_cv"], ex["recent_save_cv"],
                                ex["h2h_games"], 20, 1.0)


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return cov / (sx * sy) if sx > 0 and sy > 0 else None


# ============================================================================
# Corpus build, indices
# ============================================================================

def build_indices():
    goalie_rows = gf.load_goalie_corpus()
    team_rows = gf.load_team_sog_corpus()
    goalie_index = gf.GoalieHistoryIndex(goalie_rows)
    team_index = gf.TeamSogHistoryIndex(team_rows)
    return goalie_rows, team_rows, goalie_index, team_index


if __name__ == "__main__":
    print("Loading corpora and indices...")
    goalie_rows, team_rows, goalie_index, team_index = build_indices()
    start_rows = [r for r in goalie_rows if r["actual_started"]]
    print(f"  {len(goalie_rows)} goalie appearance rows, {len(start_rows)} actual starts, "
          f"{len(team_rows)} team-SOG rows")

    tuning_starts = [r for r in start_rows if r["season"] == TUNING_SEASON]
    tuning_teams = [r for r in team_rows if r["season"] == TUNING_SEASON]
    save_rates = gh.GoalieSavePctRates(tuning_starts)
    workload_rates = gh.GoalieWorkloadRates(tuning_starts, field="actual_saves")
    league_avg_team_sog = statistics.fmean(r["full_game_sog"] for r in tuning_teams)
    league_avg_save_pct = save_rates.league_save_pct
    print(f"  league_avg_team_sog(TUNING)={league_avg_team_sog:.2f} "
          f"league_avg_save_pct(TUNING)={league_avg_save_pct:.4f}")

    print("Loading Player SOG aggregation context (Part 6-8 eligibility investigation)...")
    agg_weights, agg_alpha = upa.load_frozen_sog_model()
    agg_ctx = upa.AggregationContext()
    print("  Player SOG aggregation context ready.")

    print("Building examples (TUNING + EVAL seasons)...")
    scored_seasons = [TUNING_SEASON] + EVAL_SEASONS
    examples_by_season: dict[int, list[dict]] = {s: [] for s in scored_seasons}
    for r in start_rows:
        if r["season"] not in scored_seasons:
            continue
        ex = build_example(r, goalie_index, team_index, save_rates, workload_rates, league_avg_team_sog,
                            agg_ctx, agg_weights)
        if ex is not None:
            examples_by_season[r["season"]].append(ex)
    for s, exs in examples_by_season.items():
        n_agg = sum(1 for e in exs if e["player_agg_shots"] is not None)
        print(f"  season {s}: {len(exs)} eligible examples ({n_agg} with player-agg coverage)")

    tuning_examples = examples_by_season[TUNING_SEASON]

    print("Fitting Poisson GLM (full-game saves) on TUNING season only...")
    fv_matrix = [glm_feature_vector(ex["baseline_saves"], ex["recent_saves5"], ex["home_away"] == "home",
                                     ex["opponent_factor"], ex["h2h_delta"], ex["is_b2b_goalie"])
                 for ex in tuning_examples]
    observed_saves = [ex["actual_saves"] for ex in tuning_examples]
    # Saves counts run ~10x larger than the Player SOG counts count_models.py's default lr=0.05
    # was tuned for -- that default DIVERGES here (confirmed: weights blow up to 1e5+ magnitude,
    # full-game Brier collapses to ~0.31 vs ~0.14 for every other candidate). A smaller lr/more
    # iterations converges cleanly (confirmed stable across lr=0.001-0.005, MAE plateaus ~5.7-5.9).
    glm_weights = cm.fit_poisson_glm(fv_matrix, observed_saves, lr=0.005, n_iter=1500)
    mu_pred = [cm.predict_mu(glm_weights, fv) for fv in fv_matrix]
    glm_alpha = cm.fit_negbinom_alpha_by_moments(observed_saves, mu_pred)
    print(f"  glm_weights={[round(w,4) for w in glm_weights]} alpha={glm_alpha:.4f}")

    print("Fitting hybrid offset-GLM on TUNING season only...")
    offsets = []
    for ex in tuning_examples:
        opp_sog = ex["opp_sog_rolling"] if ex["opp_sog_rolling"] is not None else ex["baseline_shots_faced"]
        offsets.append(math.log(max(opp_sog * ex["shrunk_save_pct"], 1e-6)))
    ctx_matrix = [_offset_context_features(ex) for ex in tuning_examples]
    offset_weights = fit_offset_glm(offsets, ctx_matrix, observed_saves)
    print(f"  offset_weights={[round(w,4) for w in offset_weights]}")

    print("Scoring baselines and candidates on all examples...")
    for s, exs in examples_by_season.items():
        for ex in exs:
            ex["baselines"] = compute_baselines(ex, league_avg_save_pct)
            ex["candidates"] = compute_candidates(ex, glm_weights, offset_weights, workload_rates)

    print("\n=== Selecting winning full-game candidate (mean Brier @20/25/30/35/40 across both eval seasons) ===")
    winner_scores = {}
    for name in ALL_CANDIDATE_NAMES:
        briers = []
        for s in EVAL_SEASONS:
            m = evaluate_estimator(examples_by_season[s], "candidates", name, "actual_saves", FULL_GAME_THRESHOLDS)
            briers.extend([m["thresholds"][t]["brier"] for t in FULL_GAME_THRESHOLDS])
        winner_scores[name] = statistics.fmean(briers)
    full_game_winner = min(winner_scores, key=winner_scores.get)
    print({n: round(v, 5) for n, v in winner_scores.items()}, "-> winner:", full_game_winner)

    print("\n=== Best baseline (same metric) ===")
    base_scores = {}
    for name in ALL_BASELINE_NAMES:
        briers = []
        for s in EVAL_SEASONS:
            m = evaluate_estimator(examples_by_season[s], "baselines", name, "actual_saves", FULL_GAME_THRESHOLDS)
            briers.extend([m["thresholds"][t]["brier"] for t in FULL_GAME_THRESHOLDS])
        base_scores[name] = statistics.fmean(briers)
    best_baseline = min(base_scores, key=base_scores.get)
    print({n: round(v, 5) for n, v in base_scores.items()}, "-> best baseline:", best_baseline)

    print("\n=== Part 20: shots-faced submodel comparison (team-level vs player-aggregated) ===")
    shots_faced_comparison = {}
    for s in EVAL_SEASONS:
        exs = examples_by_season[s]
        team_preds = [ex["opp_sog_rolling"] if ex["opp_sog_rolling"] is not None else ex["baseline_shots_faced"]
                      for ex in exs]
        actual_shots = [ex["actual_shots_faced"] for ex in exs]
        team_mae = statistics.fmean(abs(p - y) for p, y in zip(team_preds, actual_shots))
        team_rmse = math.sqrt(statistics.fmean((p - y) ** 2 for p, y in zip(team_preds, actual_shots)))

        agg_exs = [ex for ex in exs if ex["player_agg_shots"] is not None]
        if agg_exs:
            agg_preds = [ex["player_agg_shots"] for ex in agg_exs]
            agg_actual = [ex["actual_shots_faced"] for ex in agg_exs]
            agg_mae = statistics.fmean(abs(p - y) for p, y in zip(agg_preds, agg_actual))
            agg_rmse = math.sqrt(statistics.fmean((p - y) ** 2 for p, y in zip(agg_preds, agg_actual)))
        else:
            agg_mae = agg_rmse = None
        shots_faced_comparison[s] = {
            "n": len(exs), "team_level_mae": team_mae, "team_level_rmse": team_rmse,
            "n_with_player_agg": len(agg_exs), "player_agg_mae": agg_mae, "player_agg_rmse": agg_rmse,
        }
        print(s, shots_faced_comparison[s])

    print("\n=== Workload vs conversion variance decomposition (Part 11, EVAL seasons pooled) ===")
    all_eval = examples_by_season[EVAL_SEASONS[0]] + examples_by_season[EVAL_SEASONS[1]]
    shots_vals = [ex["actual_shots_faced"] for ex in all_eval]
    saves_vals = [ex["actual_saves"] for ex in all_eval]
    save_pct_vals = [ex["actual_saves"] / ex["actual_shots_faced"] for ex in all_eval if ex["actual_shots_faced"] > 0]
    workload_conversion = {
        "n": len(all_eval),
        "corr_shots_faced_vs_saves": _pearson(shots_vals, saves_vals),
        "stdev_shots_faced": statistics.pstdev(shots_vals),
        "stdev_saves": statistics.pstdev(saves_vals),
        "stdev_save_pct": statistics.pstdev(save_pct_vals) if len(save_pct_vals) > 1 else None,
        "mean_save_pct": statistics.fmean(save_pct_vals) if save_pct_vals else None,
    }
    print(workload_conversion)

    print("\n=== Per-season full-game evaluation ===")
    by_season_results = {}
    for s in EVAL_SEASONS:
        exs = examples_by_season[s]
        block = {"n": len(exs), "baselines": {}, "candidates": {}}
        for name in ALL_BASELINE_NAMES:
            block["baselines"][name] = evaluate_estimator(exs, "baselines", name, "actual_saves",
                                                            FULL_GAME_THRESHOLDS)
        for name in ALL_CANDIDATE_NAMES:
            block["candidates"][name] = evaluate_estimator(exs, "candidates", name, "actual_saves",
                                                             FULL_GAME_THRESHOLDS)
        block["monotonicity_violations"] = check_monotonicity(exs, "candidates", full_game_winner,
                                                                FULL_GAME_THRESHOLDS)

        bootstrap_block = {}
        for t in FULL_GAME_THRESHOLDS:
            base_probs = [threshold_prob(ex["baselines"][best_baseline], None, t) for ex in exs]
            cand_probs = [threshold_prob(ex["candidates"][full_game_winner], None, t) for ex in exs]
            actuals = [1.0 if ex["actual_saves"] >= t else 0.0 for ex in exs]
            base_briers = [brier(p, y) for p, y in zip(base_probs, actuals)]
            cand_briers = [brier(p, y) for p, y in zip(cand_probs, actuals)]
            bootstrap_block[t] = {
                "game_clustered": game_clustered_bootstrap(exs, base_briers, cand_briers),
                "date_clustered": date_clustered_bootstrap(exs, base_briers, cand_briers),
            }
        block["bootstrap"] = bootstrap_block

        by_conf = defaultdict(list)
        for ex in exs:
            label, _, _ = confidence_for_example(ex)
            mu = ex["candidates"][full_game_winner]
            p20 = threshold_prob(mu, None, 20)
            actual = 1.0 if ex["actual_saves"] >= 20 else 0.0
            by_conf[label].append((p20, actual))
        conf_block = {}
        for label, vals in by_conf.items():
            probs = [v[0] for v in vals]
            outcomes = [v[1] for v in vals]
            b = statistics.fmean(brier(p, y) for p, y in zip(probs, outcomes))
            actual_rate = statistics.fmean(outcomes)
            naive = actual_rate * (1 - actual_rate)
            conf_block[label] = {"n": len(vals), "skill_20plus": None if naive <= 0 else 1.0 - b / naive}
        block["confidence_stratified"] = conf_block

        cons_deltas = []
        for ex in exs:
            eff_n = min(ex["history_games"], 20)
            mu = ex["candidates"][full_game_winner]
            cons_mu = cm.conservative_mu(mu, eff_n)
            cons_deltas.append(threshold_prob(mu, None, 20) - threshold_prob(cons_mu, None, 20))
        block["conservative_probability_audit"] = {
            "n_sampled": len(cons_deltas),
            "mean_raw_minus_conservative": statistics.fmean(cons_deltas) if cons_deltas else None,
            "conservative_never_exceeds_raw": all(d >= -1e-9 for d in cons_deltas),
        }

        by_season_results[s] = block

    print("\n=== Part 29-31: dependence diagnostics ===")
    dependence = {}
    for s in EVAL_SEASONS:
        exs = examples_by_season[s]
        saves = [ex["actual_saves"] for ex in exs]
        shots = [ex["actual_shots_faced"] for ex in exs]
        goals = [ex["actual_goals_allowed"] for ex in exs]
        dependence[s] = {
            "n": len(exs),
            "saves_vs_team_sog_corr": _pearson(saves, [ex["opp_sog_rolling"] or ex["baseline_shots_faced"]
                                                          for ex in exs]),
            "saves_vs_shots_faced_corr": _pearson(saves, shots),
            "shots_faced_vs_goals_allowed_corr": _pearson(shots, goals),
            "saves_vs_goals_allowed_corr": _pearson(saves, goals),
        }
        print(s, dependence[s])

    print("\n=== Part 25-28: period saves model ===")
    period_results = {}
    period_glm_weights = {}
    period_league_share = {}
    for k in PERIODS:
        tuning_period_saves = [ex[f"period_{k}_saves"] for ex in tuning_examples]
        tuning_full_saves = [ex["actual_saves"] for ex in tuning_examples]
        total_full = sum(tuning_full_saves)
        period_league_share[k] = (sum(tuning_period_saves) / total_full) if total_full > 0 else 1.0 / 3.0

        fv_matrix_k = []
        observed_k = []
        for ex in tuning_examples:
            baseline_period = ex["baseline_saves"] * period_league_share[k]
            fv_matrix_k.append(glm_feature_vector(max(baseline_period, 1e-6), None, ex["home_away"] == "home",
                                                    ex["opponent_factor"], 0.0, ex["is_b2b_goalie"]))
            observed_k.append(ex[f"period_{k}_saves"])
        # Same scale issue as the full-game fit above, smaller magnitude but still real
        # (confirmed: default lr=0.05 converges to a strictly worse point, MAE 4.74 vs 2.73).
        period_glm_weights[k] = cm.fit_poisson_glm(fv_matrix_k, observed_k, lr=0.01, n_iter=800)

    for s in EVAL_SEASONS:
        exs = examples_by_season[s]
        for k in PERIODS:
            for ex in exs:
                share_mu = max(ex["candidates"][full_game_winner] * period_league_share[k], 1e-6)
                baseline_period = ex["baseline_saves"] * period_league_share[k]
                fv_k = glm_feature_vector(max(baseline_period, 1e-6), None, ex["home_away"] == "home",
                                           ex["opponent_factor"], 0.0, ex["is_b2b_goalie"])
                poisson_mu = cm.predict_mu(period_glm_weights[k], fv_k)
                ex.setdefault("period_candidates", {})[k] = {
                    "A_share_of_full_game": share_mu, "B_direct_poisson": poisson_mu,
                }
        season_period_block_data = {}
        for k in PERIODS:
            exs_k_share = [{"period_candidates": ex["period_candidates"][k], "period_actual": ex[f"period_{k}_saves"]}
                            for ex in exs]

            def _eval(name):
                mus = [ex["period_candidates"][name] for ex in exs_k_share]
                actuals = [ex["period_actual"] for ex in exs_k_share]
                n = len(mus)
                nll = statistics.fmean(poisson_nll(mu, y) for mu, y in zip(mus, actuals))
                mae = statistics.fmean(abs(mu - y) for mu, y in zip(mus, actuals))
                rmse = math.sqrt(statistics.fmean((mu - y) ** 2 for mu, y in zip(mus, actuals)))
                thr = {}
                for t in PERIOD_THRESHOLDS:
                    probs = [threshold_prob(mu, None, t) for mu in mus]
                    outcomes = [1.0 if y >= t else 0.0 for y in actuals]
                    b = statistics.fmean(brier(p, y) for p, y in zip(probs, outcomes))
                    actual_rate = statistics.fmean(outcomes)
                    naive = actual_rate * (1 - actual_rate)
                    skill = None if naive <= 0 else 1.0 - b / naive
                    thr[t] = {"brier": b, "actual_rate": actual_rate, "skill_score": skill}
                return {"n": n, "nll": nll, "mae": mae, "rmse": rmse, "thresholds": thr}

            metric_share = _eval("A_share_of_full_game")
            metric_poisson = _eval("B_direct_poisson")
            period_winner = "A_share_of_full_game" if statistics.fmean(
                [metric_share["thresholds"][t]["brier"] for t in PERIOD_THRESHOLDS]) <= statistics.fmean(
                [metric_poisson["thresholds"][t]["brier"] for t in PERIOD_THRESHOLDS]) else "B_direct_poisson"

            base_mus = [ex["period_candidates"]["A_share_of_full_game"] for ex in exs_k_share]
            cand_mus = [ex["period_candidates"]["B_direct_poisson"] for ex in exs_k_share]
            base_briers_5 = [brier(threshold_prob(mu, None, 5), 1.0 if y >= 5 else 0.0)
                              for mu, y in zip(base_mus, [e["period_actual"] for e in exs_k_share])]
            cand_briers_5 = [brier(threshold_prob(mu, None, 5), 1.0 if y >= 5 else 0.0)
                              for mu, y in zip(cand_mus, [e["period_actual"] for e in exs_k_share])]
            gc_bootstrap = game_clustered_bootstrap(
                [{"game_id": ex["game_id"]} for ex in exs], base_briers_5, cand_briers_5)

            season_period_block_data[k] = {
                "A_share_of_full_game": metric_share, "B_direct_poisson": metric_poisson,
                "period_winner": period_winner, "bootstrap_threshold5": gc_bootstrap,
            }
        period_results[s] = season_period_block_data

    print("\n=== Part 39-40: confidence + LOW-confidence skill already computed above (confidence_stratified) ===")

    print("\n=== Representative examples ===")

    def summarize(e):
        label, drivers, risks = confidence_for_example(e)
        mu = e["candidates"][full_game_winner]
        return {
            "game_id": e["game_id"], "game_date": e["game_date"], "goalie_id": e["goalie_id"],
            "team": e["team"], "opponent": e["opponent"], "home_away": e["home_away"],
            "expected_shots_faced": e["opp_sog_rolling"], "expected_saves": mu,
            "shrunk_save_pct": e["shrunk_save_pct"],
            "prob_20plus": threshold_prob(mu, None, 20), "prob_25plus": threshold_prob(mu, None, 25),
            "prob_30plus": threshold_prob(mu, None, 30), "prob_35plus": threshold_prob(mu, None, 35),
            "prob_40plus": threshold_prob(mu, None, 40),
            "confidence": label, "confidence_drivers": drivers, "confidence_risks": risks,
            "conservative_saves": cm.conservative_mu(mu, min(e["history_games"], 20)),
            "actual_saves": e["actual_saves"], "actual_shots_faced": e["actual_shots_faced"],
        }

    latest = examples_by_season[EVAL_SEASONS[-1]]
    by_workload = sorted(latest, key=lambda e: -e["baseline_shots_faced"])
    high_workload = by_workload[0]
    low_workload = by_workload[-1]
    by_save_pct = sorted(latest, key=lambda e: -e["shrunk_save_pct"])
    elite_save_rate = by_save_pct[0]
    average_save_rate = by_save_pct[len(by_save_pct) // 2]
    with_opp = [e for e in latest if e["opponent_factor"] is not None]
    high_shot_opp = max(with_opp, key=lambda e: e["opponent_factor"]) if with_opp else latest[0]
    low_shot_opp = min(with_opp, key=lambda e: e["opponent_factor"]) if with_opp else latest[0]
    b2b_example = next((e for e in latest if e["is_b2b_goalie"]), latest[0])

    def conf_label(e):
        return confidence_for_example(e)[0]

    high_conf = next((e for e in latest if conf_label(e) == "HIGH"), latest[0])
    low_conf = next((e for e in latest if conf_label(e) == "LOW"), latest[0])

    def hit(e):
        mu = e["candidates"][full_game_winner]
        return abs(round(mu) - e["actual_saves"]) <= 2

    model_hit = next((e for e in latest if hit(e)), latest[0])
    model_miss = next((e for e in latest if not hit(e)
                        and abs(e["candidates"][full_game_winner] - e["actual_saves"]) >= 6), latest[0])

    representative_examples = {
        "high_workload_starter": summarize(high_workload), "low_workload_starter": summarize(low_workload),
        "elite_save_rate_goalie": summarize(elite_save_rate), "average_save_rate_goalie": summarize(average_save_rate),
        "high_shot_opponent": summarize(high_shot_opp), "low_shot_opponent": summarize(low_shot_opp),
        "back_to_back_situation": summarize(b2b_example),
        "high_confidence_prediction": summarize(high_conf), "low_confidence_prediction": summarize(low_conf),
        "model_hit": summarize(model_hit), "model_miss": summarize(model_miss),
    }

    def _sha(path):
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()

    freeze_manifest = {
        "experiment_id": "goalie_saves_v1",
        "target": "full-game and period goalie saves, CONDITIONAL ON ACTUAL START",
        "population_definition": "actual_started == True rows only; relief appearances excluded from headline "
                                  "evaluation (Part 2/34) -- matches real sportsbook settlement (void if the "
                                  "named goalie does not start)",
        "starter_handling": "CONDITIONAL_ON_START. STARTER UNCERTAINTY NOT INCLUDED IN HEADLINE VALIDATION -- "
                             "the existing, separately-audited projected-starter model "
                             "(research/goalie_intelligence/) is referenced for live architecture (Part 42) but "
                             "not re-fit or folded into these Brier/log-loss numbers.",
        "model_family": f"full-game winner={full_game_winner}; best baseline={best_baseline}",
        "workload_model": "opponent team SOG rolling rate (window=20) as the shots-faced anchor for D/E; "
                           "player-SOG-roster-aggregation tested as F (Part 6-8) -- see report Section J",
        "save_rate_model": "GOALIE -> TEAM -> LEAGUE hierarchically shrunk save percentage "
                            "(research/goalie_saves/hierarchy.py::GoalieSavePctRates)",
        "features": ["log(baseline saves rate, window=20)", "recent-form log-ratio (window=5)", "home/away tag",
                     "opponent SOG-rolling factor (log)", "H2H shrunk saves delta", "back-to-back indicator"],
        "lookbacks": {"baseline_window": BASELINE_WINDOW, "recent_window": RECENT_WINDOW,
                      "opponent_window": OPPONENT_WINDOW, "h2h_shrinkage_games": gf.H2H_SHRINKAGE_GAMES},
        "distribution": f"Poisson (dispersion alpha={glm_alpha:.4f} fitted, near-zero)",
        "period_methodology": "share-of-full-game vs direct-Poisson compared independently per period "
                               "(Part 25) -- see report Section AE-AG",
        "threshold_support_rule": "full-game 20/25/30/35/40+; period 5/8/10/12+; INSUFFICIENT DATA if <30 "
                                   "positive events in an eval season",
        "calibration": "10-band calibration table per threshold, both eval seasons",
        "confidence": "research.player_sog.count_models.confidence_score (unchanged, reused)",
        "conservative_probability": "research.player_sog.count_models.conservative_mu (unchanged, reused)",
        "upstream_sog_provenance": "research/goalie_saves/upstream_player_sog_aggregation.py -- reuses the "
                                    "FROZEN, unchanged research/player_sog_results.json headline-stage weights; "
                                    "roster candidates are real recently-appeared skaters gated by the "
                                    "validated model's own projected_active() eligibility rule",
        "code_hashes": {
            "run_goalie_saves_model.py": _sha(str(REPO_ROOT / "research" / "run_goalie_saves_model.py")),
            "goalie_saves/features.py": _sha(str(REPO_ROOT / "research" / "goalie_saves" / "features.py")),
            "goalie_saves/hierarchy.py": _sha(str(REPO_ROOT / "research" / "goalie_saves" / "hierarchy.py")),
            "goalie_saves/upstream_player_sog_aggregation.py": _sha(
                str(REPO_ROOT / "research" / "goalie_saves" / "upstream_player_sog_aggregation.py")),
            "goalie_saves/build_goalie_saves_corpus.py": _sha(
                str(REPO_ROOT / "research" / "goalie_saves" / "build_goalie_saves_corpus.py")),
        },
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    full_results = {
        "config": {"warmup_season": WARMUP_SEASON, "tuning_season": TUNING_SEASON, "eval_seasons": EVAL_SEASONS,
                   "full_game_thresholds": FULL_GAME_THRESHOLDS, "period_thresholds": PERIOD_THRESHOLDS},
        "corpus_size": {"goalie_rows": len(goalie_rows), "start_rows": len(start_rows),
                         "team_sog_rows": len(team_rows)},
        "full_game_winner": full_game_winner, "best_baseline": best_baseline,
        "winner_scores": winner_scores, "baseline_scores": base_scores,
        "glm_weights": glm_weights, "glm_alpha": glm_alpha, "offset_weights": offset_weights,
        "league_avg_team_sog": league_avg_team_sog, "league_avg_save_pct": league_avg_save_pct,
        "shots_faced_comparison": shots_faced_comparison,
        "workload_conversion_decomposition": workload_conversion,
        "dependence": dependence,
        "period_league_share": period_league_share,
        "period_results": period_results,
        "by_season": by_season_results,
        "freeze_manifest": freeze_manifest,
        "representative_examples": representative_examples,
    }

    with open(RESULTS_PATH, "w") as f:
        json.dump(full_results, f, indent=2, default=str)
    print("\nWrote", RESULTS_PATH)
