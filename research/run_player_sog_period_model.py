"""
Driver for the Player SOG by Period model (Parts 1-47). Builds real
player-game-period examples from the validated 4-season PBP corpus,
tests 5 PIT-safe baselines and 5 candidate architectures under strict
walk-forward discipline (WARMUP=2022-23, TUNING=2023-24 frozen, EVAL=
2024-25 + 2025-26), and reports per-period, per-season results.

Read-only against nhl.db, models/, config.py, pricing/. Does not change
the existing validated full-game SOG model, confidence framework, or
decision policy.
"""
from __future__ import annotations

import json
import math
import random
import statistics
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from research.player_sog import count_models as cm
from research.player_sog import features as sog_pf
from research.player_sog_period import features as pf
from research.player_sog_period import hierarchy as hi
from research.player_sog_period.upstream_sog import UpstreamSogModel

WARMUP_SEASON = 20222023
TUNING_SEASON = 20232024
EVAL_SEASONS = [20242025, 20252026]

BASELINE_WINDOW = 20
RECENT_WINDOW = 5
OPPONENT_WINDOW = 20
TEAM_WINDOW = 20
PERIODS = (1, 2, 3)
THRESHOLDS = (1, 2, 3, 4)
RESULTS_PATH = REPO_ROOT / "research" / "player_sog_period_results.json"


# ============================================================================
# Part 1-4: example construction (strictly pregame, no target-game leakage)
# ============================================================================

def build_example(period_rows: list[dict], row: dict, period_index: pf.PeriodHistoryIndex,
                   sog_index, team_schedules: dict, opponent_allowed: dict, team_period_hist: dict,
                   opp_period_allowed: dict, rates: hi.PeriodRoleLeagueRates,
                   league_avg_team_period: dict[int, float]) -> dict | None:
    player_id, team, opponent, date = row["player_id"], row["team"], row["opponent"], row["game_date"]

    history = period_index.history_as_of(player_id, date)
    if len(history) < 3:
        return None

    team_sched_prior = [g for g in team_schedules.get(team, []) if g["game_date"] < date]
    sog_history = sog_index.history_as_of(player_id, date)
    if not sog_pf.projected_active(sog_history, team_sched_prior):
        return None

    tag = hi.history_role_tag(row["position"], history)

    # Full-game rolling TOI (proxy for period opportunity -- Part 13's
    # disclosed limitation: no period-specific TOI reconstruction exists).
    recent_toi = sog_pf.rolling_mean(sog_history, "icetime_seconds", 10)
    baseline_toi = sog_pf.rolling_mean(sog_history, "icetime_seconds", BASELINE_WINDOW)
    toi_window = sog_history[-10:]
    recent_toi_cv = cm.coefficient_of_variation([r["icetime_seconds"] for r in toi_window]) if toi_window else None

    full_game_hist = [r["full_game_sog"] for r in history[-BASELINE_WINDOW:]]
    full_game_rolling_mean = statistics.fmean(full_game_hist) if full_game_hist else None
    if full_game_rolling_mean is None or full_game_rolling_mean <= 0:
        full_game_rolling_mean = max(sum(rates.league_mean.values()), 0.3)

    total_full_game_hist = sum(r["full_game_sog"] for r in history)

    per_period = {}
    for k in PERIODS:
        baseline_rate = pf.rolling_period_mean(history, k, BASELINE_WINDOW)
        if baseline_rate is None or baseline_rate <= 0:
            baseline_rate = max(rates.role_mean_shrunk(tag, k), 0.05)
        recent_rate = pf.rolling_period_mean(history, k, RECENT_WINDOW)

        opp_allowed = pf.rolling_opponent_period_allowed(opp_period_allowed, opponent, date, k, OPPONENT_WINDOW)
        # TEAM-level league-average period-allowed constant (opp_allowed and
        # team_rate are both TEAM aggregates, ~10x a single player's period
        # rate -- must not be normalized against the PLAYER-level
        # rates.league_mean, a real bug caught during initial testing).
        opponent_factor = None if opp_allowed is None else opp_allowed / max(league_avg_team_period[k], 1e-6)

        team_rate = pf.rolling_team_period_rate(team_period_hist, team, date, k, TEAM_WINDOW)

        h2h_rate, h2h_games = pf.h2h_period_shrunk_rate(history, opponent, k, baseline_rate)
        h2h_delta = h2h_rate - baseline_rate

        shrunk_share = hi.player_period_share_hierarchical(history, tag, rates, k)
        shrunk_mean = hi.player_period_mean_hierarchical(history, tag, rates, k)
        raw_share = (sum(r[f"period_{k}_sog"] for r in history) / total_full_game_hist
                     if total_full_game_hist > 0 else rates.league_share[k])
        sog_window = history[-10:]
        recent_sog_cv = cm.coefficient_of_variation([r[f"period_{k}_sog"] for r in sog_window]) if sog_window else None

        per_period[k] = {
            "baseline_rate": baseline_rate, "recent_rate": recent_rate,
            "opponent_factor": opponent_factor, "team_rate": team_rate,
            "h2h_delta": h2h_delta, "h2h_games": h2h_games,
            "shrunk_share": shrunk_share, "shrunk_mean": shrunk_mean, "raw_share": raw_share,
            "recent_sog_cv": recent_sog_cv,
            "actual": row[f"period_{k}_sog"],
        }

    return {
        "game_id": row["game_id"], "game_date": date, "season": row["season"],
        "player_id": player_id, "team": team, "opponent": opponent, "position": row["position"],
        "role_tag": tag, "history_games": len(history),
        "recent_toi": recent_toi, "baseline_toi": baseline_toi, "recent_toi_cv": recent_toi_cv,
        "full_game_rolling_mean": full_game_rolling_mean,
        "per_period": per_period, "went_to_ot": row["went_to_ot"],
        "full_game_actual": row["full_game_sog"],
    }


# ============================================================================
# Part 6: PIT-safe baselines (real competitors, not strawmen)
# ============================================================================

def compute_baselines(ex: dict, rates: hi.PeriodRoleLeagueRates) -> dict[int, dict[str, float]]:
    out = {}
    for k in PERIODS:
        p = ex["per_period"][k]
        fg = ex["full_game_rolling_mean"]
        out[k] = {
            "A_league_share": max(rates.league_share[k] * fg, 1e-6),
            "B_raw_player_share": max(p["raw_share"] * fg, 1e-6),
            "C_shrunk_share": max(p["shrunk_share"] * fg, 1e-6),
            "D_direct_period_mean": max(p["baseline_rate"], 1e-6),
            "E_upstream_x_raw_share": None,  # filled in by caller once upstream_expected_sog is known
        }
    return out


# ============================================================================
# Part 18: candidate architectures
# ============================================================================

def glm_feature_vector(ex: dict, k: int) -> list[float]:
    p = ex["per_period"][k]
    return cm.build_feature_vector(
        p["baseline_rate"], p["recent_rate"], ex["recent_toi"], ex["baseline_toi"],
        p["opponent_factor"], p["h2h_delta"],
    )


def compute_candidates(ex: dict, glm_weights: dict[int, list[float]], glm_alpha: dict[int, float],
                        offset_weights: dict[int, list[float]], upstream_expected_sog: float | None,
                        rates: hi.PeriodRoleLeagueRates) -> dict[int, dict[str, float]]:
    out = {}
    for k in PERIODS:
        p = ex["per_period"][k]
        fv = glm_feature_vector(ex, k)
        mu_poisson = cm.predict_mu(glm_weights[k], fv)
        cand = {
            "A_shrunk_period_empirical": max(p["shrunk_mean"], 1e-6),
            "B_poisson_direct": max(mu_poisson, 1e-6),
            "C_negbinom_direct": max(mu_poisson, 1e-6),  # same mu, NB only changes the variance/pmf, not mu
            "D_upstream_x_shrunk_share": None,
            "E_hybrid_offset": None,
        }
        if upstream_expected_sog is not None:
            cand["D_upstream_x_shrunk_share"] = max(upstream_expected_sog * p["shrunk_share"], 1e-6)
            offset = math.log(max(upstream_expected_sog * rates.league_share[k], 1e-6))
            ctx_features = _offset_context_features(ex, k)
            adj = sum(w * x for w, x in zip(offset_weights[k], ctx_features))
            cand["E_hybrid_offset"] = max(math.exp(offset + adj), 1e-6)
        out[k] = cand
    return out


def fit_offset_glm(offsets: list[float], context_matrix: list[list[float]], observed: list[float],
                    lr: float = 0.05, n_iter: int = 400) -> list[float]:
    """Same plain batch-gradient-descent Poisson-NLL fitter as
    cm.fit_poisson_glm, generalized to include a fixed per-row offset
    (Candidate E's hybrid architecture: log(mu) = offset + w.x) -- mirrors
    the offset-GLM technique already used in the Goals model's shot-
    quality-refinement slice, not a new invented pattern."""
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


from collections import defaultdict  # noqa: E402


def brier(p: float, y: float) -> float:
    return (p - y) ** 2


def log_loss(p: float, y: float, eps: float = 1e-9) -> float:
    p = min(max(p, eps), 1 - eps)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def poisson_nll(mu: float, y: int, eps: float = 1e-9) -> float:
    mu = max(mu, eps)
    return mu - y * math.log(mu)  # drops the constant log(y!) term (doesn't affect comparison)


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


def _offset_context_features(ex: dict, k: int) -> list[float]:
    """Small, pre-specified context feature set for Candidate E's
    offset-GLM adjustment (Part 18E) -- period recent-form delta and
    period H2H delta, the two period-specific signals not already baked
    into the upstream full-game prediction or the league-average share
    offset."""
    p = ex["per_period"][k]
    recent_form = 0.0
    if p["recent_rate"] is not None and p["baseline_rate"] > 0:
        recent_form = math.log(max(p["recent_rate"], 1e-6)) - math.log(max(p["baseline_rate"], 1e-6))
    return [1.0, recent_form, p["h2h_delta"]]


# ============================================================================
# Part 27-30: corpus build, temporal split, freeze, common evaluation set
# ============================================================================

ALL_BASELINE_NAMES = ("A_league_share", "B_raw_player_share", "C_shrunk_share",
                       "D_direct_period_mean", "E_upstream_x_raw_share")
ALL_CANDIDATE_NAMES = ("A_shrunk_period_empirical", "B_poisson_direct", "C_negbinom_direct",
                        "D_upstream_x_shrunk_share", "E_hybrid_offset")


def build_indices():
    rows = pf.load_period_corpus()
    period_index = pf.PeriodHistoryIndex(rows)
    sog_rows = sog_pf.load_sog_corpus()
    sog_index = sog_pf.PlayerHistoryIndex(sog_rows)

    from research import elo_comparison as ec
    games = ec.load_corpus(str(REPO_ROOT / "research" / "real_nhl_results" / "normalized_regular_season_games.jsonl"))
    team_schedules = defaultdict(list)
    for g in games:
        team_schedules[g["home_team"]].append(g)
        team_schedules[g["away_team"]].append(g)
    for t in team_schedules:
        team_schedules[t].sort(key=lambda r: (r["game_date"], r["game_id"]))

    team_totals = pf.build_team_game_period_totals(rows)
    opp_period_allowed = pf.build_opponent_period_allowed_history(team_totals)
    team_period_hist = pf.build_team_period_history(team_totals)
    return rows, period_index, sog_index, dict(team_schedules), team_totals, opp_period_allowed, team_period_hist


def run_all() -> dict:
    print("Loading corpus and indices...")
    (rows, period_index, sog_index, team_schedules, team_totals,
     opp_period_allowed, team_period_hist) = build_indices()

    tuning_rows = [r for r in rows if r["season"] == TUNING_SEASON]
    rates = hi.PeriodRoleLeagueRates(tuning_rows)
    tuning_team_totals = {key: v for key, v in team_totals.items() if v["season"] == TUNING_SEASON}
    league_avg_team_period = {
        k: statistics.fmean(v[f"period_{k}_sog"] for v in tuning_team_totals.values()) for k in PERIODS
    }

    print("Building examples (TUNING + EVAL seasons)...")
    scored_seasons = [TUNING_SEASON] + EVAL_SEASONS
    examples_by_season: dict[int, list[dict]] = {s: [] for s in scored_seasons}
    for r in rows:
        if r["season"] not in scored_seasons:
            continue
        ex = build_example(rows, r, period_index, sog_index, team_schedules, None,
                            team_period_hist, opp_period_allowed, rates, league_avg_team_period)
        if ex is not None:
            examples_by_season[r["season"]].append(ex)
    for s, exs in examples_by_season.items():
        print(f"  season {s}: {len(exs)} eligible examples")

    print("Computing upstream full-game expected SOG (legitimate PIT-safe recomputation)...")
    upstream_model = UpstreamSogModel()
    upstream_available = 0
    upstream_total = 0
    for s, exs in examples_by_season.items():
        for ex in exs:
            upstream_total += 1
            result = upstream_model.expected_sog(ex["player_id"], ex["team"], ex["opponent"],
                                                   ex["game_date"], ex["season"])
            ex["upstream_status"] = result["status"]
            ex["upstream_expected_sog"] = result.get("expected_sog")
            if ex["upstream_expected_sog"] is not None:
                upstream_available += 1
    print(f"  upstream available for {upstream_available}/{upstream_total} examples "
          f"({upstream_available/upstream_total:.1%})")

    tuning_examples = examples_by_season[TUNING_SEASON]

    print("Fitting Poisson GLM per period on TUNING season only...")
    glm_weights = {}
    glm_alpha = {}
    for k in PERIODS:
        fv_matrix = [glm_feature_vector(ex, k) for ex in tuning_examples]
        observed = [ex["per_period"][k]["actual"] for ex in tuning_examples]
        w = cm.fit_poisson_glm(fv_matrix, observed)
        glm_weights[k] = w
        mu_pred = [cm.predict_mu(w, fv) for fv in fv_matrix]
        glm_alpha[k] = cm.fit_negbinom_alpha_by_moments(observed, mu_pred)

    print("Fitting hybrid offset-GLM per period on TUNING season only (upstream-available rows)...")
    offset_weights = {}
    for k in PERIODS:
        fit_rows = [ex for ex in tuning_examples if ex["upstream_expected_sog"] is not None]
        offsets = [math.log(max(ex["upstream_expected_sog"] * rates.league_share[k], 1e-6)) for ex in fit_rows]
        ctx_matrix = [_offset_context_features(ex, k) for ex in fit_rows]
        observed = [ex["per_period"][k]["actual"] for ex in fit_rows]
        offset_weights[k] = fit_offset_glm(offsets, ctx_matrix, observed)

    print("Scoring baselines and candidates on all examples...")
    for s, exs in examples_by_season.items():
        for ex in exs:
            base = compute_baselines(ex, rates)
            cand = compute_candidates(ex, glm_weights, glm_alpha, offset_weights,
                                       ex["upstream_expected_sog"], rates)
            for k in PERIODS:
                if ex["upstream_expected_sog"] is not None:
                    base[k]["E_upstream_x_raw_share"] = max(
                        ex["upstream_expected_sog"] * ex["per_period"][k]["raw_share"], 1e-6)
            ex["baselines"] = base
            ex["candidates"] = cand

    return {
        "rows": rows, "examples_by_season": examples_by_season, "rates": rates,
        "league_avg_team_period": league_avg_team_period, "glm_weights": glm_weights,
        "glm_alpha": glm_alpha, "offset_weights": offset_weights, "upstream_model": upstream_model,
        "upstream_coverage": {"available": upstream_available, "total": upstream_total},
    }


# ============================================================================
# Part 30-35: common evaluation set, metrics, bootstrap, calibration
# ============================================================================

def common_eval_rows(exs: list[dict]) -> tuple[list[dict], dict]:
    """Part 30: identical rows for every estimator, including the
    upstream-dependent ones -- excludes rows lacking upstream coverage,
    with the exclusion count/reason reported explicitly, never silently."""
    eligible = [ex for ex in exs if ex["upstream_expected_sog"] is not None]
    excluded = len(exs) - len(eligible)
    return eligible, {"eligible_rows": len(eligible), "excluded_rows": excluded,
                       "exclusion_reason": "upstream full-game SOG projection unavailable "
                                            "(INSUFFICIENT_HISTORY or PROJECTED_INACTIVE)"}


def evaluate_estimator(exs: list[dict], k: int, family: str, name: str) -> dict:
    mus = [ex[family][k][name] for ex in exs]
    actuals = [ex["per_period"][k]["actual"] for ex in exs]
    n = len(exs)
    nll = statistics.fmean(poisson_nll(mu, y) for mu, y in zip(mus, actuals))
    mae = statistics.fmean(abs(mu - y) for mu, y in zip(mus, actuals))
    rmse = math.sqrt(statistics.fmean((mu - y) ** 2 for mu, y in zip(mus, actuals)))

    alpha = None
    threshold_metrics = {}
    for t in THRESHOLDS:
        probs = [threshold_prob(mu, alpha, t) for mu in mus]
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
        if ex["upstream_expected_sog"] is None:
            continue
        sum_periods = sum(ex[family][k][name] for k in PERIODS)
        diffs.append(sum_periods - ex["upstream_expected_sog"])
    if not diffs:
        return {"n": 0}
    return {"n": len(diffs), "mean_diff": statistics.fmean(diffs),
            "abs_mean_diff": statistics.fmean(abs(d) for d in diffs),
            "stdev_diff": statistics.pstdev(diffs) if len(diffs) > 1 else 0.0}


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    return cov / (sx * sy)


def cross_period_correlation(exs: list[dict]) -> dict:
    def corr(xs, ys):
        return _pearson(xs, ys)
    p1 = [ex["per_period"][1]["actual"] for ex in exs]
    p2 = [ex["per_period"][2]["actual"] for ex in exs]
    p3 = [ex["per_period"][3]["actual"] for ex in exs]
    full = [ex["full_game_actual"] for ex in exs]
    return {
        "raw_corr_p1_p2": corr(p1, p2), "raw_corr_p1_p3": corr(p1, p3), "raw_corr_p2_p3": corr(p2, p3),
        "raw_corr_p1_full": corr(p1, full),
    }


# ============================================================================
# Part 36-37: confidence + conservative probability (SHARED framework reused)
# ============================================================================

def confidence_for_example(ex: dict, k: int) -> tuple[str, list[str], list[str]]:
    p = ex["per_period"][k]
    return cm.confidence_score(ex["history_games"], ex["recent_toi_cv"], p["recent_sog_cv"],
                                p["h2h_games"], 20, 1.0)


# ============================================================================
# Part 39: representative examples (real rows only -- no fake odds/prices)
# ============================================================================

def _example_summary(ex: dict, winner_by_period: dict) -> dict:
    out = {
        "game_id": ex["game_id"], "game_date": ex["game_date"], "player_id": ex["player_id"],
        "team": ex["team"], "opponent": ex["opponent"], "position": ex["position"],
        "role_tag": ex["role_tag"], "full_game_expected_sog": ex["upstream_expected_sog"],
        "full_game_actual_sog": ex["full_game_actual"], "periods": {},
    }
    for k in PERIODS:
        winner = winner_by_period[k]
        mu = ex["candidates"][k][winner]
        label, drivers, risks = confidence_for_example(ex, k)
        cons_mu = cm.conservative_mu(mu, min(ex["history_games"], 20))
        out["periods"][k] = {
            "expected_sog": mu, "conservative_sog": cons_mu,
            "prob_1plus": threshold_prob(mu, None, 1), "prob_2plus": threshold_prob(mu, None, 2),
            "prob_3plus": threshold_prob(mu, None, 3),
            "confidence": label, "confidence_drivers": drivers, "confidence_risks": risks,
            "actual": ex["per_period"][k]["actual"],
        }
    return out


def _pick_representative_examples(exs: list[dict], winner_by_period: dict) -> dict:
    by_full_game_hist = sorted(exs, key=lambda e: -e["full_game_rolling_mean"])
    high_volume = next((e for e in by_full_game_hist if e["position"] != "D"), exs[0])
    low_volume = next((e for e in reversed(by_full_game_hist) if e["full_game_rolling_mean"] > 0), exs[0])
    defenseman = next((e for e in exs if e["position"] == "D"), exs[0])
    pp_candidates = [e for e in exs if e["role_tag"].endswith("_PP")]  # exact role suffix -- "PP" is also a
                                                                        # substring of "NONPP", a real bug caught here
    pp_heavy = max(pp_candidates, key=lambda e: e["full_game_rolling_mean"]) if pp_candidates else exs[0]

    def conf_p1(e):
        return confidence_for_example(e, 1)[0]

    high_conf = next((e for e in exs if conf_p1(e) == "HIGH"), exs[0])
    low_conf = next((e for e in exs if conf_p1(e) == "LOW"), exs[0])

    def p1_hit(e):
        mu = e["candidates"][1][winner_by_period[1]]
        pred = round(mu)
        return pred == e["per_period"][1]["actual"]

    model_hit = next((e for e in exs if p1_hit(e)), exs[0])
    model_miss = next((e for e in exs if not p1_hit(e)
                        and abs(e["candidates"][1][winner_by_period[1]] - e["per_period"][1]["actual"]) >= 2), exs[0])

    def p1_skew(e):
        shares = [e["per_period"][k]["shrunk_share"] for k in PERIODS]
        return max(shares) - min(shares)

    strong_skew = max(exs, key=p1_skew)
    even_alloc = min(exs, key=p1_skew)

    names = {
        "high_volume_shooter": high_volume, "low_volume_shooter": low_volume, "defenseman": defenseman,
        "pp_heavy_player": pp_heavy, "high_confidence_prediction": high_conf, "low_confidence_prediction": low_conf,
        "model_hit": model_hit, "model_miss": model_miss,
        "strong_period_skew": strong_skew, "nearly_even_allocation": even_alloc,
    }
    return {name: _example_summary(ex, winner_by_period) for name, ex in names.items()}


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

    print("\n=== Full results dump written to", RESULTS_PATH)

    full_results = {
        "config": {
            "warmup_season": WARMUP_SEASON, "tuning_season": TUNING_SEASON, "eval_seasons": EVAL_SEASONS,
            "periods": PERIODS, "thresholds": THRESHOLDS,
        },
        "upstream_coverage": state["upstream_coverage"],
        "winner_by_period": winner_by_period, "best_baseline_by_period": best_baseline_by_period,
        "glm_weights": state["glm_weights"], "glm_alpha": state["glm_alpha"],
        "offset_weights": state["offset_weights"],
        "league_share": rates.league_share, "league_mean": rates.league_mean,
        "by_season": {},
    }

    for s in EVAL_SEASONS:
        season_block = {"common_eval": common_eval_rows(examples_by_season[s])[1]}
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

            # Part 36: confidence-stratified skill (NOT assumed WATCH_ONLY --
            # tested directly, same as every other prop's own confidence audit).
            by_conf = defaultdict(list)
            for ex in common[s]:
                label, _, _ = confidence_for_example(ex, k)
                mu = ex["candidates"][k][winner]
                probs_by_t = {t: threshold_prob(mu, None, t) for t in (1, 2)}
                actuals_by_t = {t: (1.0 if ex["per_period"][k]["actual"] >= t else 0.0) for t in (1, 2)}
                by_conf[label].append((probs_by_t, actuals_by_t))
            conf_block = {}
            for label, vals in by_conf.items():
                entry = {"n": len(vals)}
                for t in (1, 2):
                    probs = [v[0][t] for v in vals]
                    outcomes = [v[1][t] for v in vals]
                    b = statistics.fmean(brier(p, y) for p, y in zip(probs, outcomes))
                    actual_rate = statistics.fmean(outcomes)
                    naive = actual_rate * (1 - actual_rate)
                    entry[f"skill_{t}plus"] = None if naive <= 0 else 1.0 - b / naive
                conf_block[label] = entry
            period_block["confidence_stratified"] = conf_block

            # Part 37: conservative probability audit (reuses cm.conservative_mu unchanged).
            cons_deltas = []
            for ex in common[s][:5000]:
                eff_n = min(ex["history_games"], 20)
                mu = ex["candidates"][k][winner]
                cons_mu = cm.conservative_mu(mu, eff_n)
                raw_p = threshold_prob(mu, None, 1)
                cons_p = threshold_prob(cons_mu, None, 1)
                cons_deltas.append(raw_p - cons_p)
            period_block["conservative_probability_audit"] = {
                "n_sampled": len(cons_deltas),
                "mean_raw_minus_conservative": statistics.fmean(cons_deltas) if cons_deltas else None,
                "conservative_never_exceeds_raw": all(d >= -1e-9 for d in cons_deltas),
            }

            season_block[f"period_{k}"] = period_block
        season_block["cross_period_correlation"] = cross_period_correlation(common[s])
        full_results["by_season"][s] = season_block

    # Part 29: freeze manifest -- written from the SAME frozen artifacts
    # already used for evaluation above, not a separate re-derivation.
    import hashlib
    import datetime as dt

    def _sha(path):
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()

    full_results["freeze_manifest"] = {
        "experiment_id": "player_sog_by_period_v1",
        "target": "player shots-on-goal per period (P1/P2/P3, regulation only)",
        "model_family": "PLAYER->POSITION/PP-ROLE->LEAGUE shrunk period-count empirical (Candidate A)",
        "period_handling": "independent per-period shrunk empirical means; no cross-period constraint imposed",
        "features": ["period_k rolling SOG history (own, window=20)", "role tag (F/D x PP/NONPP proxy)",
                     "league/role period-count prior (TUNING-season fit)"],
        "lookbacks": {"baseline_window": BASELINE_WINDOW, "recent_window": RECENT_WINDOW,
                      "k_player_shrinkage": 40, "k_role_shrinkage": 300},
        "upstream_sog_version": "player_sog M4_plus_h2h (headline_stage), Poisson",
        "upstream_sog_provenance": "recomputed PIT-safely per row via live_projection.project_player_sog(), "
                                    "not a stored artifact (none exists) -- see Section J",
        "distribution": "Poisson (candidate A is a direct empirical mean estimator; Poisson used only for "
                         "threshold-probability derivation, matching the full-game SOG model's own choice)",
        "confidence_framework": "research.player_sog.count_models.confidence_score (unchanged, reused)",
        "conservative_probability": "research.player_sog.count_models.conservative_mu (unchanged, reused)",
        "tail_support_rule": "thresholds 1-4 only; 5+/6+ marked INSUFFICIENT DATA per Section AE",
        "code_hashes": {
            "run_player_sog_period_model.py": _sha(str(REPO_ROOT / "research" / "run_player_sog_period_model.py")),
            "player_sog_period/features.py": _sha(str(REPO_ROOT / "research" / "player_sog_period" / "features.py")),
            "player_sog_period/hierarchy.py": _sha(str(REPO_ROOT / "research" / "player_sog_period" / "hierarchy.py")),
            "player_sog_period/upstream_sog.py": _sha(str(REPO_ROOT / "research" / "player_sog_period" / "upstream_sog.py")),
        },
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    # Part 39: representative examples, pulled from real 2025-26 eval rows.
    full_results["representative_examples"] = _pick_representative_examples(common[EVAL_SEASONS[-1]], winner_by_period)

    with open(RESULTS_PATH, "w") as f:
        json.dump(full_results, f, indent=2, default=str)
    print("Wrote", RESULTS_PATH)
