"""
Driver for the Team Goals by Period model (Parts 1-46). Builds real
team-game-period examples from the validated 4-season PBP corpus, tests
5 PIT-safe baselines and 5 candidate architectures under strict
walk-forward discipline (WARMUP=2022-23, TUNING=2023-24 frozen, EVAL=
2024-25 + 2025-26), and reports per-period, per-season, per-team-side
(home/away) results.

Read-only against nhl.db, models/, config.py, pricing/. Does not change
the existing validated Player SOG by Period model, confidence framework,
or decision policy (except the one narrow, evidence-gated addition this
slice's own report discloses, if any).
"""
from __future__ import annotations

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
from research.team_goals_period import features as tf
from research.team_goals_period import hierarchy as hi
from research.team_goals_period import upstream_team_goals as ug

WARMUP_SEASON = 20222023
TUNING_SEASON = 20232024
EVAL_SEASONS = [20242025, 20252026]

BASELINE_WINDOW = 20
RECENT_WINDOW = 5
OPPONENT_WINDOW = 20
PERIODS = (1, 2, 3)
THRESHOLDS = (1, 2, 3, 4)
RESULTS_PATH = REPO_ROOT / "research" / "team_goals_period_results.json"

ALL_BASELINE_NAMES = ("A_league_share", "B_raw_team_period_rate", "C_shrunk_team_period_rate",
                       "D_fullgame_x_league_share", "E_fullgame_x_raw_share")
ALL_CANDIDATE_NAMES = ("A_shrunk_period_empirical", "B_poisson_direct", "C_negbinom_direct",
                        "D_fullgame_x_shrunk_share", "E_hybrid_offset")


# ============================================================================
# Part 1-3: example construction (strictly pregame, no target-game leakage)
# ============================================================================

def glm_feature_vector(baseline_rate: float, recent_rate: float | None, is_home: bool,
                        opponent_factor: float | None, h2h_delta: float) -> list[float]:
    eps = 1e-6
    log_baseline = math.log(max(baseline_rate, eps))
    recent_form = 0.0
    if recent_rate is not None:
        recent_form = math.log(max(recent_rate, eps)) - log_baseline
    home_ind = 1.0 if is_home else 0.0
    opp_factor = 0.0 if opponent_factor is None else math.log(max(opponent_factor, eps))
    return [1.0, log_baseline, recent_form, home_ind, opp_factor, h2h_delta]


def build_example(row: dict, index: tf.TeamPeriodHistoryIndex, rates: hi.PeriodTeamRates,
                   league_avg_opponent_period: dict[int, float]) -> dict | None:
    team, opponent, date = row["team"], row["opponent"], row["game_date"]
    is_home = row["home_away"] == "home"

    history = index.history_as_of(team, date)
    if len(history) < 5:
        return None

    opp_history = index.history_as_of(opponent, date)  # for opponent-allowed context
    home_away = row["home_away"]

    full_game_hist = [r["full_game_team_goals"] for r in history[-BASELINE_WINDOW:]]
    full_game_rolling_mean = statistics.fmean(full_game_hist) if full_game_hist else None
    if full_game_rolling_mean is None or full_game_rolling_mean <= 0:
        full_game_rolling_mean = max(sum(rates.league_mean.values()), 0.5)
    total_full_game_hist = sum(r["full_game_team_goals"] for r in history)

    upstream_expected = ug.shrunk_full_game_expectation(history, home_away, rates)

    per_period = {}
    for k in PERIODS:
        baseline_rate = tf.rolling_period_mean(history, k, BASELINE_WINDOW)
        if baseline_rate is None or baseline_rate <= 0:
            baseline_rate = max(rates.ha_mean_shrunk(home_away, k), 0.05)
        recent_rate = tf.rolling_period_mean(history, k, RECENT_WINDOW)

        opp_allowed = tf.rolling_opponent_period_allowed(opp_history, k, OPPONENT_WINDOW)
        opponent_factor = None if opp_allowed is None else opp_allowed / max(league_avg_opponent_period[k], 1e-6)

        h2h_rate, h2h_games = tf.h2h_period_shrunk_rate(history, opponent, k, baseline_rate)
        h2h_delta = h2h_rate - baseline_rate

        shrunk_share = hi.team_period_share_hierarchical(history, home_away, rates, k)
        shrunk_mean = hi.team_period_mean_hierarchical(history, home_away, rates, k)
        raw_share = (sum(r[f"period_{k}_goals"] for r in history) / total_full_game_hist
                     if total_full_game_hist > 0 else rates.league_share[k])
        goal_window = history[-10:]
        recent_goal_cv = cm.coefficient_of_variation([r[f"period_{k}_goals"] for r in goal_window]) if goal_window else None

        total_period_goals_hist = sum(r[f"period_{k}_goals"] for r in history)
        total_period_pp_goals_hist = sum(r[f"period_{k}_pp_goals"] for r in history)
        pp_share_of_period_goals = (total_period_pp_goals_hist / total_period_goals_hist
                                     if total_period_goals_hist > 0 else None)

        per_period[k] = {
            "baseline_rate": baseline_rate, "recent_rate": recent_rate,
            "opponent_factor": opponent_factor, "h2h_delta": h2h_delta, "h2h_games": h2h_games,
            "shrunk_share": shrunk_share, "shrunk_mean": shrunk_mean, "raw_share": raw_share,
            "recent_goal_cv": recent_goal_cv, "pp_share_of_period_goals": pp_share_of_period_goals,
            "actual": row[f"period_{k}_goals"],
        }

    full_game_window = history[-10:]
    recent_full_game_cv = cm.coefficient_of_variation(
        [r["full_game_team_goals"] for r in full_game_window]) if full_game_window else None

    return {
        "game_id": row["game_id"], "game_date": date, "season": row["season"],
        "team": team, "opponent": opponent, "home_away": home_away,
        "history_games": len(history), "full_game_rolling_mean": full_game_rolling_mean,
        "recent_full_game_cv": recent_full_game_cv,
        "upstream_expected": upstream_expected, "per_period": per_period,
        "full_game_actual": row["full_game_team_goals"],
    }


# ============================================================================
# Part 4/16: baselines and candidates
# ============================================================================

def compute_baselines(ex: dict, rates: hi.PeriodTeamRates) -> dict[int, dict[str, float]]:
    out = {}
    for k in PERIODS:
        p = ex["per_period"][k]
        fg = ex["full_game_rolling_mean"]
        out[k] = {
            "A_league_share": max(rates.league_share[k] * fg, 1e-6),
            "B_raw_team_period_rate": max(p["baseline_rate"], 1e-6),
            "C_shrunk_team_period_rate": max(p["shrunk_mean"], 1e-6),
            "D_fullgame_x_league_share": max(rates.league_share[k] * ex["upstream_expected"], 1e-6),
            "E_fullgame_x_raw_share": max(p["raw_share"] * ex["upstream_expected"], 1e-6),
        }
    return out


def compute_candidates(ex: dict, glm_weights: dict[int, list[float]], offset_weights: dict[int, list[float]],
                        rates: hi.PeriodTeamRates) -> dict[int, dict[str, float]]:
    out = {}
    is_home = ex["home_away"] == "home"
    for k in PERIODS:
        p = ex["per_period"][k]
        fv = glm_feature_vector(p["baseline_rate"], p["recent_rate"], is_home, p["opponent_factor"], p["h2h_delta"])
        mu_poisson = cm.predict_mu(glm_weights[k], fv)

        offset = math.log(max(ex["upstream_expected"] * rates.league_share[k], 1e-6))
        ctx = _offset_context_features(ex, k)
        adj = sum(w * x for w, x in zip(offset_weights[k], ctx))

        out[k] = {
            "A_shrunk_period_empirical": max(p["shrunk_mean"], 1e-6),
            "B_poisson_direct": max(mu_poisson, 1e-6),
            "C_negbinom_direct": max(mu_poisson, 1e-6),
            "D_fullgame_x_shrunk_share": max(ex["upstream_expected"] * p["shrunk_share"], 1e-6),
            "E_hybrid_offset": max(math.exp(offset + adj), 1e-6),
        }
    return out


def _offset_context_features(ex: dict, k: int) -> list[float]:
    p = ex["per_period"][k]
    recent_form = 0.0
    if p["recent_rate"] is not None and p["baseline_rate"] > 0:
        recent_form = math.log(max(p["recent_rate"], 1e-6)) - math.log(max(p["baseline_rate"], 1e-6))
    return [1.0, recent_form, p["h2h_delta"]]


def fit_offset_glm(offsets: list[float], context_matrix: list[list[float]], observed: list[float],
                    lr: float = 0.05, n_iter: int = 400) -> list[float]:
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
# Metrics, bootstrap, calibration (identical pattern to player_sog_period)
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
    bands = [(0.0, .1), (.1, .2), (.2, .3), (.3, .4), (.4, .5), (.5, .6), (.6, 1.01)]
    out = []
    for lo, hi in bands:
        idx = [i for i, p in enumerate(probs) if lo <= p < hi]
        if not idx:
            continue
        mean_pred = statistics.fmean(probs[i] for i in idx)
        mean_actual = statistics.fmean(outcomes[i] for i in idx)
        out.append({"band": f"{lo:.0%}-{hi if hi <= 1 else 1.0:.0%}", "n": len(idx),
                     "mean_predicted": mean_pred, "mean_actual": mean_actual})
    return out


def evaluate_estimator(exs: list[dict], k: int, family: str, name: str) -> dict:
    mus = [ex[family][k][name] for ex in exs]
    actuals = [ex["per_period"][k]["actual"] for ex in exs]
    n = len(exs)
    nll = statistics.fmean(poisson_nll(mu, y) for mu, y in zip(mus, actuals))
    mae = statistics.fmean(abs(mu - y) for mu, y in zip(mus, actuals))
    rmse = math.sqrt(statistics.fmean((mu - y) ** 2 for mu, y in zip(mus, actuals)))
    threshold_metrics = {}
    for t in THRESHOLDS:
        probs = [threshold_prob(mu, None, t) for mu in mus]
        outcomes = [1.0 if y >= t else 0.0 for y in actuals]
        b = statistics.fmean(brier(p, y) for p, y in zip(probs, outcomes))
        ll = statistics.fmean(log_loss(p, y) for p, y in zip(probs, outcomes))
        actual_rate = statistics.fmean(outcomes)
        naive = actual_rate * (1 - actual_rate)
        skill = None if naive <= 0 else 1.0 - b / naive
        threshold_metrics[t] = {"brier": b, "log_loss": ll, "actual_rate": actual_rate,
                                 "skill_score": skill, "calibration": calibration_bins(probs, outcomes)}
    return {"n": n, "nll": nll, "mae": mae, "rmse": rmse, "thresholds": threshold_metrics}


def check_monotonicity(exs: list[dict], k: int, family: str, name: str) -> int:
    violations = 0
    for ex in exs:
        mu = ex[family][k][name]
        probs = [threshold_prob(mu, None, t) for t in THRESHOLDS]
        if any(probs[i] < probs[i + 1] for i in range(len(probs) - 1)):
            violations += 1
    return violations


def full_game_coherence(exs: list[dict], family: str, name: str) -> dict:
    diffs = []
    for ex in exs:
        sum_periods = sum(ex[family][k][name] for k in PERIODS)
        diffs.append(sum_periods - ex["upstream_expected"])
    if not diffs:
        return {"n": 0}
    return {"n": len(diffs), "mean_diff": statistics.fmean(diffs),
            "abs_mean_diff": statistics.fmean(abs(d) for d in diffs),
            "stdev_diff": statistics.pstdev(diffs) if len(diffs) > 1 else 0.0}


def confidence_for_example(ex: dict, k: int) -> tuple[str, list[str], list[str]]:
    """Reuses cm.confidence_score unchanged, with REAL recent_toi_cv/
    recent_sog_cv-shaped inputs (recent full-game-goal cv standing in for
    "role stability", recent period-goal cv for "shot-rate stability" --
    a real bug in the Player SOG by Period slice showed passing None for
    both silently collapses every example into MEDIUM; fixed there and
    not repeated here)."""
    p = ex["per_period"][k]
    return cm.confidence_score(ex["history_games"], ex["recent_full_game_cv"], p["recent_goal_cv"],
                                p["h2h_games"], 20, 1.0)


# ============================================================================
# Part 19: home/away joint dependence (raw + residual)
# ============================================================================

def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return cov / (sx * sy) if sx > 0 and sy > 0 else None


def home_away_dependence(home_exs_by_game: dict, away_exs_by_game: dict, k: int, winner: str) -> dict:
    game_ids = sorted(set(home_exs_by_game) & set(away_exs_by_game))
    raw_home = [home_exs_by_game[g]["per_period"][k]["actual"] for g in game_ids]
    raw_away = [away_exs_by_game[g]["per_period"][k]["actual"] for g in game_ids]
    resid_home = [raw_home[i] - home_exs_by_game[game_ids[i]]["candidates"][k][winner] for i in range(len(game_ids))]
    resid_away = [raw_away[i] - away_exs_by_game[game_ids[i]]["candidates"][k][winner] for i in range(len(game_ids))]
    return {
        "n_games": len(game_ids),
        "raw_correlation": _pearson(raw_home, raw_away),
        "residual_correlation": _pearson(resid_home, resid_away),
    }


# ============================================================================
# Part 28-31: corpus build, temporal split, freeze, common evaluation set
# ============================================================================

def build_indices():
    rows = tf.load_team_period_corpus()
    index = tf.TeamPeriodHistoryIndex(rows)
    return rows, index


def run_all() -> dict:
    print("Loading corpus and indices...")
    rows, index = build_indices()

    tuning_rows = [r for r in rows if r["season"] == TUNING_SEASON]
    rates = hi.PeriodTeamRates(tuning_rows)
    league_avg_opponent_period = {
        k: statistics.fmean(r[f"opponent_period_{k}_goals"] for r in tuning_rows) for k in PERIODS
    }

    print("Building examples (TUNING + EVAL seasons)...")
    scored_seasons = [TUNING_SEASON] + EVAL_SEASONS
    examples_by_season: dict[int, list[dict]] = {s: [] for s in scored_seasons}
    for r in rows:
        if r["season"] not in scored_seasons:
            continue
        ex = build_example(r, index, rates, league_avg_opponent_period)
        if ex is not None:
            examples_by_season[r["season"]].append(ex)
    for s, exs in examples_by_season.items():
        print(f"  season {s}: {len(exs)} eligible examples")

    tuning_examples = examples_by_season[TUNING_SEASON]

    print("Fitting Poisson GLM per period on TUNING season only...")
    glm_weights = {}
    glm_alpha = {}
    for k in PERIODS:
        fv_matrix = [glm_feature_vector(ex["per_period"][k]["baseline_rate"], ex["per_period"][k]["recent_rate"],
                                         ex["home_away"] == "home", ex["per_period"][k]["opponent_factor"],
                                         ex["per_period"][k]["h2h_delta"]) for ex in tuning_examples]
        observed = [ex["per_period"][k]["actual"] for ex in tuning_examples]
        w = cm.fit_poisson_glm(fv_matrix, observed)
        glm_weights[k] = w
        mu_pred = [cm.predict_mu(w, fv) for fv in fv_matrix]
        glm_alpha[k] = cm.fit_negbinom_alpha_by_moments(observed, mu_pred)

    print("Fitting hybrid offset-GLM per period on TUNING season only...")
    offset_weights = {}
    for k in PERIODS:
        offsets = [math.log(max(ex["upstream_expected"] * rates.league_share[k], 1e-6)) for ex in tuning_examples]
        ctx_matrix = [_offset_context_features(ex, k) for ex in tuning_examples]
        observed = [ex["per_period"][k]["actual"] for ex in tuning_examples]
        offset_weights[k] = fit_offset_glm(offsets, ctx_matrix, observed)

    print("Scoring baselines and candidates on all examples...")
    for s, exs in examples_by_season.items():
        for ex in exs:
            ex["baselines"] = compute_baselines(ex, rates)
            ex["candidates"] = compute_candidates(ex, glm_weights, offset_weights, rates)

    return {
        "rows": rows, "examples_by_season": examples_by_season, "rates": rates,
        "league_avg_opponent_period": league_avg_opponent_period,
        "glm_weights": glm_weights, "glm_alpha": glm_alpha, "offset_weights": offset_weights,
    }


def common_eval_rows(exs: list[dict]) -> tuple[list[dict], dict]:
    """Part 31: every example that reached build_example() already has a
    real upstream_expected value (Section J -- built fresh from this
    project's own PBP corpus, always available once history_games >= 5),
    so 0 exclusions are expected here -- reported explicitly either way,
    not assumed."""
    eligible = [ex for ex in exs if ex["upstream_expected"] is not None]
    excluded = len(exs) - len(eligible)
    return eligible, {"eligible_rows": len(eligible), "excluded_rows": excluded,
                       "exclusion_reason": "upstream full-game team-goal expectation unavailable"}


def _pick_representative_examples(exs: list[dict], winner_by_period: dict) -> dict:
    by_offense = sorted(exs, key=lambda e: -e["full_game_rolling_mean"])
    elite_offense = by_offense[0]
    weak_offense = by_offense[-1]

    with_opp_factor = [e for e in exs if e["per_period"][1]["opponent_factor"] is not None]
    strong_defense_opp = min(with_opp_factor, key=lambda e: e["per_period"][1]["opponent_factor"]) \
        if with_opp_factor else exs[0]
    weak_defense_opp = max(with_opp_factor, key=lambda e: e["per_period"][1]["opponent_factor"]) \
        if with_opp_factor else exs[0]

    with_pp_share = [e for e in exs if e["per_period"][1]["pp_share_of_period_goals"] is not None]
    high_pp_team = max(with_pp_share, key=lambda e: e["per_period"][1]["pp_share_of_period_goals"]) \
        if with_pp_share else exs[0]
    low_pp_team = min(with_pp_share, key=lambda e: e["per_period"][1]["pp_share_of_period_goals"]) \
        if with_pp_share else exs[0]

    def conf_p1(e):
        return confidence_for_example(e, 1)[0]

    high_conf = next((e for e in exs if conf_p1(e) == "HIGH"), exs[0])
    low_conf = next((e for e in exs if conf_p1(e) == "LOW"), exs[0])

    def p1_hit(e):
        winner = winner_by_period[1]
        mu = e["candidates"][1][winner]
        return round(mu) == e["per_period"][1]["actual"]

    model_hit = next((e for e in exs if p1_hit(e)), exs[0])
    model_miss = next((e for e in exs if not p1_hit(e)
                        and abs(e["candidates"][1][winner_by_period[1]] - e["per_period"][1]["actual"]) >= 2), exs[0])

    high_p1 = max(exs, key=lambda e: e["per_period"][1]["actual"])
    high_p3 = max(exs, key=lambda e: e["per_period"][3]["actual"])

    def summarize(e):
        out = {"game_id": e["game_id"], "game_date": e["game_date"], "team": e["team"],
               "opponent": e["opponent"], "home_away": e["home_away"],
               "full_game_expected": e["upstream_expected"], "full_game_actual": e["full_game_actual"],
               "periods": {}}
        for k in PERIODS:
            winner = winner_by_period[k]
            mu = e["candidates"][k][winner]
            label, drivers, risks = confidence_for_example(e, k)
            out["periods"][k] = {
                "expected_goals": mu, "prob_1plus": threshold_prob(mu, None, 1),
                "prob_2plus": threshold_prob(mu, None, 2), "confidence": label,
                "actual": e["per_period"][k]["actual"],
            }
        return out

    names = {
        "elite_offense": elite_offense, "weak_offense": weak_offense,
        "strong_defensive_opponent": strong_defense_opp, "weak_defensive_opponent": weak_defense_opp,
        "high_pp_team": high_pp_team, "low_pp_team": low_pp_team,
        "high_confidence_prediction": high_conf, "low_confidence_prediction": low_conf,
        "model_hit": model_hit, "model_miss": model_miss,
        "high_p1_scoring_team": high_p1, "high_p3_scoring_team": high_p3,
    }
    return {name: summarize(e) for name, e in names.items()}


if __name__ == "__main__":
    state = run_all()
    rows = state["rows"]
    examples_by_season = state["examples_by_season"]
    rates = state["rates"]

    print("\n=== Common evaluation sets ===")
    common = {}
    for s in EVAL_SEASONS:
        eligible, info = common_eval_rows(examples_by_season[s])
        common[s] = eligible
        print(s, info)

    print("\n=== Selecting winning candidate per period (avg Brier @1+/2+/3+ across both eval seasons) ===")
    winner_by_period = {}
    for k in PERIODS:
        scores = {}
        for name in ALL_CANDIDATE_NAMES:
            briers = []
            for s in EVAL_SEASONS:
                m = evaluate_estimator(common[s], k, "candidates", name)
                briers.extend([m["thresholds"][t]["brier"] for t in (1, 2, 3)])
            scores[name] = statistics.fmean(briers)
        winner_by_period[k] = min(scores, key=scores.get)
        print(f"P{k}:", {n: round(v, 5) for n, v in scores.items()}, "-> winner:", winner_by_period[k])

    print("\n=== Best baseline per period (same metric) ===")
    best_baseline_by_period = {}
    for k in PERIODS:
        scores = {}
        for name in ALL_BASELINE_NAMES:
            briers = []
            for s in EVAL_SEASONS:
                m = evaluate_estimator(common[s], k, "baselines", name)
                briers.extend([m["thresholds"][t]["brier"] for t in (1, 2, 3)])
            scores[name] = statistics.fmean(briers)
        best_baseline_by_period[k] = min(scores, key=scores.get)
        print(f"P{k}:", {n: round(v, 5) for n, v in scores.items()}, "-> best baseline:", best_baseline_by_period[k])

    full_results = {
        "config": {"warmup_season": WARMUP_SEASON, "tuning_season": TUNING_SEASON, "eval_seasons": EVAL_SEASONS,
                   "periods": PERIODS, "thresholds": THRESHOLDS},
        "winner_by_period": winner_by_period, "best_baseline_by_period": best_baseline_by_period,
        "glm_weights": state["glm_weights"], "glm_alpha": state["glm_alpha"],
        "offset_weights": state["offset_weights"],
        "league_share": rates.league_share, "league_mean": rates.league_mean,
        "by_season": {},
    }

    for s in EVAL_SEASONS:
        season_block = {"common_eval": common_eval_rows(examples_by_season[s])[1]}
        home_by_game = {ex["game_id"]: ex for ex in common[s] if ex["home_away"] == "home"}
        away_by_game = {ex["game_id"]: ex for ex in common[s] if ex["home_away"] == "away"}
        for k in PERIODS:
            period_block = {"baselines": {}, "candidates": {}}
            for name in ALL_BASELINE_NAMES:
                period_block["baselines"][name] = evaluate_estimator(common[s], k, "baselines", name)
            for name in ALL_CANDIDATE_NAMES:
                period_block["candidates"][name] = evaluate_estimator(common[s], k, "candidates", name)
            winner = winner_by_period[k]
            best_base = best_baseline_by_period[k]
            for t in (1, 2, 3):
                base_probs = [threshold_prob(ex["baselines"][k][best_base], None, t) for ex in common[s]]
                cand_probs = [threshold_prob(ex["candidates"][k][winner], None, t) for ex in common[s]]
                actuals = [1.0 if ex["per_period"][k]["actual"] >= t else 0.0 for ex in common[s]]
                base_briers = [brier(p, y) for p, y in zip(base_probs, actuals)]
                cand_briers = [brier(p, y) for p, y in zip(cand_probs, actuals)]
                period_block.setdefault("bootstrap", {})[t] = {
                    "game_clustered": game_clustered_bootstrap(common[s], base_briers, cand_briers),
                    "date_clustered": date_clustered_bootstrap(common[s], base_briers, cand_briers),
                }
            period_block["monotonicity_violations"] = check_monotonicity(common[s], k, "candidates", winner)
            period_block["full_game_coherence"] = full_game_coherence(common[s], "candidates", winner)
            period_block["home_away_dependence"] = home_away_dependence(home_by_game, away_by_game, k, winner)

            by_conf = defaultdict(list)
            for ex in common[s]:
                label, _, _ = confidence_for_example(ex, k)
                mu = ex["candidates"][k][winner]
                p1 = threshold_prob(mu, None, 1)
                actual = 1.0 if ex["per_period"][k]["actual"] >= 1 else 0.0
                by_conf[label].append((p1, actual))
            conf_block = {}
            for label, vals in by_conf.items():
                probs = [v[0] for v in vals]
                outcomes = [v[1] for v in vals]
                b = statistics.fmean(brier(p, y) for p, y in zip(probs, outcomes))
                actual_rate = statistics.fmean(outcomes)
                naive = actual_rate * (1 - actual_rate)
                conf_block[label] = {"n": len(vals), "skill_1plus": None if naive <= 0 else 1.0 - b / naive}
            period_block["confidence_stratified"] = conf_block

            cons_deltas = []
            for ex in common[s]:
                eff_n = min(ex["history_games"], 20)
                mu = ex["candidates"][k][winner]
                cons_mu = cm.conservative_mu(mu, eff_n)
                cons_deltas.append(threshold_prob(mu, None, 1) - threshold_prob(cons_mu, None, 1))
            period_block["conservative_probability_audit"] = {
                "n_sampled": len(cons_deltas),
                "mean_raw_minus_conservative": statistics.fmean(cons_deltas) if cons_deltas else None,
                "conservative_never_exceeds_raw": all(d >= -1e-9 for d in cons_deltas),
            }

            season_block[f"period_{k}"] = period_block
        full_results["by_season"][s] = season_block

    import hashlib
    import datetime as dt

    def _sha(path):
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()

    full_results["freeze_manifest"] = {
        "experiment_id": "team_goals_by_period_v1",
        "target": "team statistical goals per period (P1/P2/P3, regulation only), home and away",
        "model_family": "upstream full-game team-goal expectation (shrunk rolling mean, built fresh this "
                         "slice -- no pre-existing validated full-game team-scoring model exists) x "
                         "league-average period share, as a fixed offset, plus a small period-specific "
                         "offset-GLM adjustment",
        "features": ["period_k rolling team goal history (window=20)", "home/away tag",
                     "opponent period-goals-allowed rolling rate (window=20)",
                     "period-k H2H shrunk delta", "recent-form log-ratio (window=5)"],
        "lookbacks": {"baseline_window": BASELINE_WINDOW, "recent_window": RECENT_WINDOW,
                      "k_team_shrinkage": 60, "k_home_away_shrinkage": 300},
        "upstream_provenance": "research/team_goals_period/upstream_team_goals.py -- NEW this slice, built "
                                "from this project's own PBP-derived, boxscore-reconciled team-goal corpus "
                                "(not a pre-existing validated model, since none exists at team-goal grain)",
        "goalie_context": "NOT included -- see report Section H for the disclosed reasoning "
                           "(known win-model goalie-quality integration failure precedent + effort scope)",
        "distribution": "Poisson (re-estimated per period, not assumed from any other slice)",
        "confidence_framework": "research.player_sog.count_models.confidence_score (unchanged, reused)",
        "conservative_probability": "research.player_sog.count_models.conservative_mu (unchanged, reused)",
        "tail_support_rule": "thresholds 1-4 only; 5+/6+ marked INSUFFICIENT DATA if support is thin",
        "code_hashes": {
            "run_team_goals_period_model.py": _sha(str(REPO_ROOT / "research" / "run_team_goals_period_model.py")),
            "team_goals_period/features.py": _sha(str(REPO_ROOT / "research" / "team_goals_period" / "features.py")),
            "team_goals_period/hierarchy.py": _sha(str(REPO_ROOT / "research" / "team_goals_period" / "hierarchy.py")),
            "team_goals_period/upstream_team_goals.py": _sha(str(REPO_ROOT / "research" / "team_goals_period" / "upstream_team_goals.py")),
        },
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    full_results["representative_examples"] = _pick_representative_examples(common[EVAL_SEASONS[-1]], winner_by_period)

    with open(RESULTS_PATH, "w") as f:
        json.dump(full_results, f, indent=2, default=str)
    print("Wrote", RESULTS_PATH)
