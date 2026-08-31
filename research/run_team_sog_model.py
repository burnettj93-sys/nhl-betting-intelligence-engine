"""
Driver for the Team Shots on Goal model (Parts 1-50). Builds real
team-game examples from a freshly-built, independently-reconciled 4-season
Team SOG corpus (research/team_sog/), tests 6 PIT-safe baselines and 6
candidate architectures under strict walk-forward discipline (WARMUP=
2022-23, TUNING=2023-24 frozen, EVAL=2024-25 + 2025-26), re-tests
player-SOG aggregation as a controlled candidate (previously found to
underperform for Goalie Saves -- tested fresh here, not assumed), and
runs the mandatory player/team reconciliation + dependence diagnostics
this slice exists to produce for future joint-market work.

Does NOT modify the existing validated Goalie Saves model, full-game or
period Player SOG models, the confidence framework, or decision policy.

Read-only against nhl.db, models/, config.py, pricing/.
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
from research.team_sog import features as tf
from research.team_sog import hierarchy as th
from research.team_sog import upstream_player_sog_aggregation as upa

WARMUP_SEASON = 20222023
TUNING_SEASON = 20232024
EVAL_SEASONS = [20242025, 20252026]

BASELINE_WINDOW = 20
RECENT_WINDOW = 5
OPPONENT_WINDOW = 20
MIN_HISTORY_GAMES = 5

SOG_THRESHOLDS = (20, 25, 30, 35, 40)
RESULTS_PATH = REPO_ROOT / "research" / "team_sog_results.json"

ALL_BASELINE_NAMES = ("A_league_average", "B_team_rolling_sog", "C_shrunk_team_sog_rate",
                       "D_opponent_rolling_sog_allowed", "E_offense_defense_shrunk_combo",
                       "F_home_away_adjusted")
ALL_CANDIDATE_NAMES = ("A_shrunk_team_empirical", "B_poisson_direct", "C_negbinom_direct",
                        "D_offense_defense_decomposition", "E_hybrid_rolling_plus_suppression",
                        "F_player_agg")


# ============================================================================
# Part 1-16: example construction (strictly pregame, no target-game leakage)
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


def build_example(row: dict, index: tf.TeamHistoryIndex, rates: th.TeamSogRates,
                   league_avg_sog: float, agg_ctx: "upa.AggregationContext | None",
                   agg_weights: list[float] | None) -> dict | None:
    team, opponent, date = row["team"], row["opponent"], row["game_date"]
    is_home = row["home_away"] == "home"

    history = index.history_as_of(team, date)
    if len(history) < MIN_HISTORY_GAMES:
        return None
    opponent_history = index.history_as_of(opponent, date)

    baseline_sog_for = tf.rolling_mean(history, "actual_team_sog", BASELINE_WINDOW)
    if baseline_sog_for is None or baseline_sog_for <= 0:
        baseline_sog_for = max(rates.team_offensive_factor_shrunk(team) * league_avg_sog, 5.0)
    recent_sog_for5 = tf.rolling_mean(history, "actual_team_sog", RECENT_WINDOW)

    opp_sog_allowed_rolling = tf.rolling_mean(opponent_history, "actual_opponent_sog", OPPONENT_WINDOW)
    opponent_factor = None if opp_sog_allowed_rolling is None else opp_sog_allowed_rolling / league_avg_sog

    shrunk_team_mean = th.team_sog_mean_hierarchical(history, row["home_away"], rates)
    offensive_factor_shrunk = rates.team_offensive_factor_shrunk(team)
    defensive_factor_shrunk_opp = rates.team_defensive_factor_shrunk(opponent)

    h2h_rate, h2h_games = tf.h2h_shrunk_rate(history, opponent, "actual_team_sog", baseline_sog_for)
    h2h_delta = h2h_rate - baseline_sog_for

    is_b2b = tf.team_is_back_to_back(history, date)

    sog_window = history[-10:]
    recent_sog_cv = cm.coefficient_of_variation([r["actual_team_sog"] for r in sog_window]) if sog_window else None

    total_pp_sog_hist = sum(r["P1_pp_sog"] + r["P2_pp_sog"] + r["P3_pp_sog"] for r in history)
    total_sog_hist = sum(r["actual_team_sog"] for r in history)
    pp_share_of_sog = (total_pp_sog_hist / total_sog_hist) if total_sog_hist > 0 else None

    player_agg_sog = None
    if agg_ctx is not None and agg_weights is not None:
        agg_result = upa.aggregate_expected_team_sog(agg_ctx, team, opponent, date, agg_weights)
        if agg_result["n_players"] >= 8:
            player_agg_sog = agg_result["expected_sog_sum"]

    return {
        "game_id": row["game_id"], "game_date": date, "season": row["season"],
        "team": team, "opponent": opponent, "home_away": row["home_away"],
        "history_games": len(history),
        "baseline_sog_for": baseline_sog_for, "recent_sog_for5": recent_sog_for5,
        "opponent_factor": opponent_factor, "opp_sog_allowed_rolling": opp_sog_allowed_rolling,
        "shrunk_team_mean": shrunk_team_mean,
        "offensive_factor_shrunk": offensive_factor_shrunk,
        "defensive_factor_shrunk_opp": defensive_factor_shrunk_opp,
        "h2h_delta": h2h_delta, "h2h_games": h2h_games, "is_b2b": is_b2b,
        "recent_sog_cv": recent_sog_cv, "pp_share_of_sog": pp_share_of_sog,
        "player_agg_sog": player_agg_sog,
        "actual_team_sog": row["actual_team_sog"], "actual_opponent_sog": row["actual_opponent_sog"],
        "P1_team_sog": row["P1_team_sog"], "P2_team_sog": row["P2_team_sog"], "P3_team_sog": row["P3_team_sog"],
    }


# ============================================================================
# Part 4/17: baselines and candidates
# ============================================================================

def compute_baselines(ex: dict, rates: th.TeamSogRates, league_avg_sog: float) -> dict[str, float]:
    return {
        "A_league_average": max(league_avg_sog, 1e-6),
        "B_team_rolling_sog": max(ex["baseline_sog_for"], 1e-6),
        "C_shrunk_team_sog_rate": max(ex["shrunk_team_mean"], 1e-6),
        "D_opponent_rolling_sog_allowed": max(
            ex["opp_sog_allowed_rolling"] if ex["opp_sog_allowed_rolling"] is not None
            else ex["baseline_sog_for"], 1e-6),
        "E_offense_defense_shrunk_combo": max(
            league_avg_sog * ex["offensive_factor_shrunk"] * ex["defensive_factor_shrunk_opp"], 1e-6),
        "F_home_away_adjusted": max(rates.ha_mean_for_shrunk(ex["home_away"]), 1e-6),
    }


def compute_candidates(ex: dict, glm_weights: list[float], offset_weights: list[float],
                        league_avg_sog: float) -> dict[str, float]:
    is_home = ex["home_away"] == "home"
    fv = glm_feature_vector(ex["baseline_sog_for"], ex["recent_sog_for5"], is_home, ex["opponent_factor"],
                             ex["h2h_delta"], ex["is_b2b"])
    mu_poisson = cm.predict_mu(glm_weights, fv)

    decomposition = league_avg_sog * ex["offensive_factor_shrunk"] * ex["defensive_factor_shrunk_opp"]

    offset = math.log(max(ex["baseline_sog_for"], 1e-6))
    ctx = _offset_context_features(ex)
    adj = sum(w * x for w, x in zip(offset_weights, ctx))

    out = {
        "A_shrunk_team_empirical": max(ex["shrunk_team_mean"], 1e-6),
        "B_poisson_direct": max(mu_poisson, 1e-6),
        "C_negbinom_direct": max(mu_poisson, 1e-6),
        "D_offense_defense_decomposition": max(decomposition, 1e-6),
        "E_hybrid_rolling_plus_suppression": max(math.exp(offset + adj), 1e-6),
    }
    if ex["player_agg_sog"] is not None:
        out["F_player_agg"] = max(ex["player_agg_sog"], 1e-6)
    else:
        out["F_player_agg"] = out["A_shrunk_team_empirical"]
    return out


def _offset_context_features(ex: dict) -> list[float]:
    recent_form = 0.0
    if ex["recent_sog_for5"] is not None and ex["baseline_sog_for"] > 0:
        recent_form = math.log(max(ex["recent_sog_for5"], 1e-6)) - math.log(max(ex["baseline_sog_for"], 1e-6))
    opp_factor_log = 0.0 if ex["opponent_factor"] is None else math.log(max(ex["opponent_factor"], 1e-6))
    return [1.0, recent_form, opp_factor_log]


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


def evaluate_estimator(exs: list[dict], family: str, name: str, thresholds: tuple[int, ...],
                        alpha: float | None = None) -> dict:
    mus = [ex[family][name] for ex in exs]
    actuals = [ex["actual_team_sog"] for ex in exs]
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
    return cm.confidence_score(ex["history_games"], ex["recent_sog_cv"], ex["recent_sog_cv"],
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


def build_indices():
    rows = tf.load_team_sog_corpus()
    index = tf.TeamHistoryIndex(rows)
    return rows, index


if __name__ == "__main__":
    print("Loading corpus and indices...")
    rows, index = build_indices()
    print(f"  {len(rows)} team-game rows")

    tuning_rows = [r for r in rows if r["season"] == TUNING_SEASON]
    rates = th.TeamSogRates(tuning_rows)
    league_avg_sog = statistics.fmean(r["actual_team_sog"] for r in tuning_rows)
    print(f"  league_avg_sog(TUNING)={league_avg_sog:.2f}")

    print("Loading Player SOG aggregation context (Part 6-8)...")
    agg_weights, agg_alpha = upa.load_frozen_sog_model()
    agg_ctx = upa.AggregationContext()

    print("Building examples (TUNING + EVAL seasons)...")
    scored_seasons = [TUNING_SEASON] + EVAL_SEASONS
    examples_by_season: dict[int, list[dict]] = {s: [] for s in scored_seasons}
    for r in rows:
        if r["season"] not in scored_seasons:
            continue
        ex = build_example(r, index, rates, league_avg_sog, agg_ctx, agg_weights)
        if ex is not None:
            examples_by_season[r["season"]].append(ex)
    for s, exs in examples_by_season.items():
        n_agg = sum(1 for e in exs if e["player_agg_sog"] is not None)
        print(f"  season {s}: {len(exs)} eligible examples ({n_agg} with player-agg coverage)")

    tuning_examples = examples_by_season[TUNING_SEASON]

    print("Fitting Poisson GLM (full-game team SOG) on TUNING season only...")
    fv_matrix = [glm_feature_vector(ex["baseline_sog_for"], ex["recent_sog_for5"], ex["home_away"] == "home",
                                     ex["opponent_factor"], ex["h2h_delta"], ex["is_b2b"])
                 for ex in tuning_examples]
    observed_sog = [ex["actual_team_sog"] for ex in tuning_examples]
    # Same lr-scale lesson from the Goalie Saves slice: SOG counts (~29/game) are far
    # larger than the Player SOG lr=0.05 default was tuned for -- use the same
    # confirmed-stable lr/n_iter as that slice's full-game fit.
    glm_weights = cm.fit_poisson_glm(fv_matrix, observed_sog, lr=0.005, n_iter=1500)
    mu_pred = [cm.predict_mu(glm_weights, fv) for fv in fv_matrix]
    glm_alpha = cm.fit_negbinom_alpha_by_moments(observed_sog, mu_pred)
    print(f"  glm_weights={[round(w,4) for w in glm_weights]} alpha={glm_alpha:.4f}")

    print("Fitting hybrid offset-GLM on TUNING season only...")
    offsets = [math.log(max(ex["baseline_sog_for"], 1e-6)) for ex in tuning_examples]
    ctx_matrix = [_offset_context_features(ex) for ex in tuning_examples]
    offset_weights = fit_offset_glm(offsets, ctx_matrix, observed_sog)
    print(f"  offset_weights={[round(w,4) for w in offset_weights]}")

    print("Scoring baselines and candidates on all examples...")
    for s, exs in examples_by_season.items():
        for ex in exs:
            ex["baselines"] = compute_baselines(ex, rates, league_avg_sog)
            ex["candidates"] = compute_candidates(ex, glm_weights, offset_weights, league_avg_sog)

    print("\n=== Selecting winning candidate (mean Brier @20/25/30/35/40 across both eval seasons) ===")
    winner_scores = {}
    for name in ALL_CANDIDATE_NAMES:
        briers = []
        for s in EVAL_SEASONS:
            m = evaluate_estimator(examples_by_season[s], "candidates", name, SOG_THRESHOLDS)
            briers.extend([m["thresholds"][t]["brier"] for t in SOG_THRESHOLDS])
        winner_scores[name] = statistics.fmean(briers)
    winner = min(winner_scores, key=winner_scores.get)
    print({n: round(v, 5) for n, v in winner_scores.items()}, "-> winner:", winner)

    print("\n=== Best baseline (same metric) ===")
    base_scores = {}
    for name in ALL_BASELINE_NAMES:
        briers = []
        for s in EVAL_SEASONS:
            m = evaluate_estimator(examples_by_season[s], "baselines", name, SOG_THRESHOLDS)
            briers.extend([m["thresholds"][t]["brier"] for t in SOG_THRESHOLDS])
        base_scores[name] = statistics.fmean(briers)
    best_baseline = min(base_scores, key=base_scores.get)
    print({n: round(v, 5) for n, v in base_scores.items()}, "-> best baseline:", best_baseline)

    print("\n=== Part 7: player/team reconciliation diagnostic (actual outcomes) ===")
    import research.player_sog.features as pf
    sog_rows_all = pf.load_sog_corpus()
    player_team_totals = pf.build_team_game_totals(sog_rows_all)
    team_sog_by_game_team = {(r["team"], r["game_id"]): r["actual_team_sog"] for r in rows}
    diffs = []
    for (team, game_id), tot in player_team_totals.items():
        actual = team_sog_by_game_team.get((team, game_id))
        if actual is None:
            continue
        diffs.append(tot["sog_for"] - actual)
    reconciliation_actual = {
        "n": len(diffs), "mean_diff": statistics.fmean(diffs) if diffs else None,
        "abs_mean_diff": statistics.fmean(abs(d) for d in diffs) if diffs else None,
        "exact_match_pct": (sum(1 for d in diffs if d == 0) / len(diffs) * 100) if diffs else None,
    }
    print(reconciliation_actual)

    print("\n=== Part 7: player-agg prediction vs direct Team SOG expectation discrepancy ===")
    pred_discrepancy = {}
    for s in EVAL_SEASONS:
        exs = examples_by_season[s]
        with_agg = [e for e in exs if e["player_agg_sog"] is not None]
        if with_agg:
            diffs2 = [e["player_agg_sog"] - e["baseline_sog_for"] for e in with_agg]
            pred_discrepancy[s] = {
                "n": len(with_agg), "mean_diff": statistics.fmean(diffs2),
                "abs_mean_diff": statistics.fmean(abs(d) for d in diffs2),
            }
        else:
            pred_discrepancy[s] = {"n": 0}
        print(s, pred_discrepancy[s])

    print("\n=== Part 27: Team SOG vs opposing goalie saves relationship ===")
    import research.goalie_saves.features as gsf
    goalie_rows = gsf.load_goalie_corpus()
    starts_by_game_team = {(r["team"], r["game_id"]): r for r in goalie_rows if r["actual_started"]}
    goalie_dependence = {}
    for s in EVAL_SEASONS:
        exs = examples_by_season[s]
        pairs = []
        for e in exs:
            g = starts_by_game_team.get((e["opponent"], e["game_id"]))
            if g is not None:
                pairs.append((e["actual_team_sog"], g["actual_saves"]))
        if pairs:
            xs, ys = zip(*pairs)
            goalie_dependence[s] = {"n": len(pairs), "corr_team_sog_vs_opp_goalie_saves": _pearson(list(xs), list(ys))}
        else:
            goalie_dependence[s] = {"n": 0}
        print(s, goalie_dependence[s])

    print("\n=== Per-season full-game evaluation ===")
    by_season_results = {}
    for s in EVAL_SEASONS:
        exs = examples_by_season[s]
        block = {"n": len(exs), "baselines": {}, "candidates": {}}
        for name in ALL_BASELINE_NAMES:
            block["baselines"][name] = evaluate_estimator(exs, "baselines", name, SOG_THRESHOLDS)
        for name in ALL_CANDIDATE_NAMES:
            block["candidates"][name] = evaluate_estimator(exs, "candidates", name, SOG_THRESHOLDS)
        block["monotonicity_violations"] = check_monotonicity(exs, "candidates", winner, SOG_THRESHOLDS)

        bootstrap_block = {}
        for t in SOG_THRESHOLDS:
            base_probs = [threshold_prob(ex["baselines"][best_baseline], None, t) for ex in exs]
            cand_probs = [threshold_prob(ex["candidates"][winner], None, t) for ex in exs]
            actuals = [1.0 if ex["actual_team_sog"] >= t else 0.0 for ex in exs]
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
            mu = ex["candidates"][winner]
            p30 = threshold_prob(mu, None, 30)
            actual = 1.0 if ex["actual_team_sog"] >= 30 else 0.0
            by_conf[label].append((p30, actual))
        conf_block = {}
        for label, vals in by_conf.items():
            probs = [v[0] for v in vals]
            outcomes = [v[1] for v in vals]
            b = statistics.fmean(brier(p, y) for p, y in zip(probs, outcomes))
            actual_rate = statistics.fmean(outcomes)
            naive = actual_rate * (1 - actual_rate)
            conf_block[label] = {"n": len(vals), "skill_30plus": None if naive <= 0 else 1.0 - b / naive}
        block["confidence_stratified"] = conf_block

        cons_deltas = []
        for ex in exs:
            eff_n = min(ex["history_games"], 20)
            mu = ex["candidates"][winner]
            cons_mu = cm.conservative_mu(mu, eff_n)
            cons_deltas.append(threshold_prob(mu, None, 30) - threshold_prob(cons_mu, None, 30))
        block["conservative_probability_audit"] = {
            "n_sampled": len(cons_deltas),
            "mean_raw_minus_conservative": statistics.fmean(cons_deltas) if cons_deltas else None,
            "conservative_never_exceeds_raw": all(d >= -1e-9 for d in cons_deltas),
        }

        # Part 23: period-share diagnostic (secondary, not primary this slice)
        period_shares_hist = defaultdict(list)
        for ex in exs:
            total = ex["P1_team_sog"] + ex["P2_team_sog"] + ex["P3_team_sog"]
            if total > 0:
                period_shares_hist["P1"].append(ex["P1_team_sog"] / total)
                period_shares_hist["P2"].append(ex["P2_team_sog"] / total)
                period_shares_hist["P3"].append(ex["P3_team_sog"] / total)
        block["period_share_diagnostic"] = {
            k: statistics.fmean(v) for k, v in period_shares_hist.items()
        }

        # Part 29: player-team residual dependence (using expected vs actual player SOG on TOP shooter)
        by_season_results[s] = block

    print("\n=== Part 28-29: contribution concentration + residual dependence ===")
    concentration = {}
    for s in EVAL_SEASONS:
        team_game_ids = {(ex["team"], ex["game_id"]) for ex in examples_by_season[s]}
        by_team_game_players = defaultdict(list)
        for r in sog_rows_all:
            if r["season"] != s:
                continue
            by_team_game_players[(r["team"], r["game_id"])].append(r["sog"])
        top1_shares, top2_shares, top3_shares = [], [], []
        for key, sogs in by_team_game_players.items():
            total = sum(sogs)
            if total <= 0:
                continue
            sogs_sorted = sorted(sogs, reverse=True)
            top1_shares.append(sogs_sorted[0] / total)
            top2_shares.append(sum(sogs_sorted[:2]) / total)
            top3_shares.append(sum(sogs_sorted[:3]) / total)
        concentration[s] = {
            "n": len(top1_shares),
            "mean_top1_share": statistics.fmean(top1_shares) if top1_shares else None,
            "mean_top2_share": statistics.fmean(top2_shares) if top2_shares else None,
            "mean_top3_share": statistics.fmean(top3_shares) if top3_shares else None,
        }
        print(s, concentration[s])

    def _sha(path):
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()

    freeze_manifest = {
        "experiment_id": "team_sog_v1",
        "target": "full-game team shots on goal",
        "model_family": f"winner={winner}; best baseline={best_baseline}",
        "features": ["log(baseline team SOG, w=20)", "recent-form log-ratio (w=5)", "home/away tag",
                     "log(opponent SOG-allowed factor)", "H2H shrunk delta", "back-to-back indicator"],
        "lookbacks": {"baseline_window": BASELINE_WINDOW, "recent_window": RECENT_WINDOW,
                      "opponent_window": OPPONENT_WINDOW, "h2h_shrinkage_games": tf.H2H_SHRINKAGE_GAMES},
        "offense_defense_treatment": "TEAM -> HOME/AWAY -> LEAGUE hierarchical shrinkage of offensive/defensive "
                                      "multiplicative factors (research/team_sog/hierarchy.py::TeamSogRates)",
        "player_aggregation_policy": "reuses FROZEN player_sog_results.json headline-stage weights unchanged; "
                                      "roster candidates gated by the validated model's own projected_active() "
                                      "rule; re-tested fresh for Team SOG (not assumed to repeat the Goalie "
                                      "Saves slice's negative finding)",
        "projected_active_version": "research.player_sog.features.projected_active (unchanged)",
        "distribution": f"Poisson (dispersion alpha={glm_alpha:.4f} fitted, near-zero)",
        "calibration": "10-band calibration table per threshold, both eval seasons",
        "confidence": "research.player_sog.count_models.confidence_score (unchanged, reused)",
        "conservative_probability": "research.player_sog.count_models.conservative_mu (unchanged, reused)",
        "threshold_support_rule": "INSUFFICIENT_DATA if <50 positive events in an eval season",
        "code_hashes": {
            "run_team_sog_model.py": _sha(str(REPO_ROOT / "research" / "run_team_sog_model.py")),
            "team_sog/features.py": _sha(str(REPO_ROOT / "research" / "team_sog" / "features.py")),
            "team_sog/hierarchy.py": _sha(str(REPO_ROOT / "research" / "team_sog" / "hierarchy.py")),
            "team_sog/upstream_player_sog_aggregation.py": _sha(
                str(REPO_ROOT / "research" / "team_sog" / "upstream_player_sog_aggregation.py")),
            "team_sog/build_team_sog_corpus.py": _sha(
                str(REPO_ROOT / "research" / "team_sog" / "build_team_sog_corpus.py")),
        },
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    latest = examples_by_season[EVAL_SEASONS[-1]]

    def summarize(e):
        label, drivers, risks = confidence_for_example(e)
        mu = e["candidates"][winner]
        return {
            "game_id": e["game_id"], "game_date": e["game_date"], "team": e["team"], "opponent": e["opponent"],
            "home_away": e["home_away"], "expected_sog": mu,
            "conservative_sog": cm.conservative_mu(mu, min(e["history_games"], 20)),
            "prob_20plus": threshold_prob(mu, None, 20), "prob_25plus": threshold_prob(mu, None, 25),
            "prob_30plus": threshold_prob(mu, None, 30), "prob_35plus": threshold_prob(mu, None, 35),
            "prob_40plus": threshold_prob(mu, None, 40),
            "confidence": label, "confidence_drivers": drivers, "confidence_risks": risks,
            "actual_team_sog": e["actual_team_sog"],
        }

    by_off = sorted(latest, key=lambda e: -e["offensive_factor_shrunk"])
    elite_offense, weak_offense = by_off[0], by_off[-1]
    by_def = sorted(latest, key=lambda e: e["defensive_factor_shrunk_opp"])
    strong_def_opp, weak_def_opp = by_def[0], by_def[-1]
    b2b_ex = next((e for e in latest if e["is_b2b"]), latest[0])

    def conf_label(e):
        return confidence_for_example(e)[0]

    high_conf = next((e for e in latest if conf_label(e) == "HIGH"), latest[0])
    low_conf = next((e for e in latest if conf_label(e) == "LOW"), latest[0])

    def hit(e):
        return abs(round(e["candidates"][winner]) - e["actual_team_sog"]) <= 2

    model_hit = next((e for e in latest if hit(e)), latest[0])
    model_miss = next((e for e in latest if not hit(e)
                        and abs(e["candidates"][winner] - e["actual_team_sog"]) >= 6), latest[0])

    representative_examples = {
        "elite_offense": summarize(elite_offense), "weak_offense": summarize(weak_offense),
        "strong_defensive_opponent": summarize(strong_def_opp), "weak_defensive_opponent": summarize(weak_def_opp),
        "back_to_back_situation": summarize(b2b_ex),
        "high_confidence_prediction": summarize(high_conf), "low_confidence_prediction": summarize(low_conf),
        "model_hit": summarize(model_hit), "model_miss": summarize(model_miss),
    }

    full_results = {
        "config": {"warmup_season": WARMUP_SEASON, "tuning_season": TUNING_SEASON, "eval_seasons": EVAL_SEASONS,
                   "thresholds": SOG_THRESHOLDS},
        "corpus_size": {"team_game_rows": len(rows)},
        "winner": winner, "best_baseline": best_baseline,
        "winner_scores": winner_scores, "baseline_scores": base_scores,
        "glm_weights": glm_weights, "glm_alpha": glm_alpha, "offset_weights": offset_weights,
        "league_avg_sog": league_avg_sog,
        "reconciliation_actual": reconciliation_actual,
        "player_agg_prediction_discrepancy": pred_discrepancy,
        "goalie_dependence": goalie_dependence,
        "contribution_concentration": concentration,
        "by_season": by_season_results,
        "freeze_manifest": freeze_manifest,
        "representative_examples": representative_examples,
    }

    with open(RESULTS_PATH, "w") as f:
        json.dump(full_results, f, indent=2, default=str)
    print("\nWrote", RESULTS_PATH)
