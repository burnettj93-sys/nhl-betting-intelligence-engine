"""
Driver for the Joint Scoring/Contribution Dependence Foundation slice
(Parts 1-51). Builds a real joint corpus linking Player SOG + Goals +
Assists + Points for the same real player-game, reuses the four FROZEN
marginal engines UNCHANGED (research/joint_scoring_dependence/
marginal_provenance.py), separates EXACT LOGICAL IDENTITIES (Goal->Point,
Assist->Point) from GENUINE STATISTICAL DEPENDENCE (SOG+Goal, SOG+Point,
SOG+Assist), and automatically detects structurally redundant legs in
three-way combinations rather than double-scoring them.

CRITICAL: never refits Player SOG, Goals, Assists, or Points. Every
marginal probability comes from each model's own frozen weights (or, for
Points, the frozen EMPIRICAL BASELINE that actually won -- Part 4).

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

from research.joint_scoring_dependence import features as jf
from research.joint_scoring_dependence import joint_models as jm
from research.joint_scoring_dependence import marginal_provenance as mp

WARMUP_SEASON = 20222023
TUNING_SEASON = 20232024
EVAL_SEASONS = [20242025, 20252026]

RESULTS_PATH = REPO_ROOT / "research" / "joint_scoring_dependence_results.json"

# Part 8's own controlled threshold matrix.
PAIR_COMBINATIONS = [
    {"name": "SOG2_GOAL", "kind": "structural", "field_sog": "actual_sog", "x_sog": 2,
     "event": "goals", "x_event": 1, "conv_field": "actual_goals", "label_b": "GOAL_1_PLUS"},
    {"name": "SOG3_GOAL", "kind": "structural", "field_sog": "actual_sog", "x_sog": 3,
     "event": "goals", "x_event": 1, "conv_field": "actual_goals", "label_b": "GOAL_1_PLUS"},
    {"name": "SOG4_GOAL", "kind": "structural", "field_sog": "actual_sog", "x_sog": 4,
     "event": "goals", "x_event": 1, "conv_field": "actual_goals", "label_b": "GOAL_1_PLUS"},
    {"name": "SOG2_ASSIST", "kind": "structural", "field_sog": "actual_sog", "x_sog": 2,
     "event": "assists", "x_event": 1, "conv_field": "actual_assists", "label_b": "ASSIST_1_PLUS"},
    {"name": "SOG3_ASSIST", "kind": "structural", "field_sog": "actual_sog", "x_sog": 3,
     "event": "assists", "x_event": 1, "conv_field": "actual_assists", "label_b": "ASSIST_1_PLUS"},
    {"name": "SOG3_POINT", "kind": "structural", "field_sog": "actual_sog", "x_sog": 3,
     "event": "points", "x_event": 1, "conv_field": "actual_points", "label_b": "POINT_1_PLUS"},
    {"name": "SOG4_POINT", "kind": "structural", "field_sog": "actual_sog", "x_sog": 4,
     "event": "points", "x_event": 1, "conv_field": "actual_points", "label_b": "POINT_1_PLUS"},
    {"name": "GOAL_POINT", "kind": "logical", "field_a": "actual_goals", "x_a": 1,
     "field_b": "actual_points", "x_b": 1, "label_a": "GOAL_1_PLUS", "label_b": "POINT_1_PLUS"},
    {"name": "ASSIST_POINT", "kind": "logical", "field_a": "actual_assists", "x_a": 1,
     "field_b": "actual_points", "x_b": 1, "label_a": "ASSIST_1_PLUS", "label_b": "POINT_1_PLUS"},
]
# NOTE: the SOG leg is labeled "SOG_3_PLUS" (its REAL tested threshold), never
# the generic "SOG_1_PLUS" -- GOAL_1_PLUS only logically implies SOG at the 1+
# threshold (a goal requires only ONE shot), never SOG>=3, so using "SOG_1_PLUS"
# here would make detect_redundant_leg falsely treat the SOG>=3 leg as implied
# by GOAL>=1 and drop it -- a real bug caught by this slice's own test suite
# before it ever reached the frozen results.
TRIPLE_COMBINATIONS = [
    {"name": "SOG3_GOAL_POINT", "x_sog": 3, "labels": ["SOG_3_PLUS", "GOAL_1_PLUS", "POINT_1_PLUS"],
     "reduces_to": "SOG3_GOAL"},
    {"name": "SOG3_ASSIST_POINT", "x_sog": 3, "labels": ["SOG_3_PLUS", "ASSIST_1_PLUS", "POINT_1_PLUS"],
     "reduces_to": "SOG3_ASSIST"},
]

ALL_CANDIDATE_NAMES = ("A_naive_independence", "B_shrunk_empirical_joint", "C_conditional_empirical",
                        "D_structural_conditional", "E_gaussian_copula")

MIN_JOINT_POSITIVE_EVENTS = 30


def brier(p: float, y: float) -> float:
    return (p - y) ** 2


def log_loss(p: float, y: float, eps: float = 1e-9) -> float:
    p = min(max(p, eps), 1 - eps)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return cov / (sx * sy) if sx > 0 and sy > 0 else None


def build_augmented_examples(rows: list[dict], ctx: mp.ScoringMarginalContext,
                              history_index: jf.JointScoringHistoryIndex,
                              goal_rates: jm.ConversionRates, point_rates: jm.ConversionRates,
                              assist_rates: jm.ConversionRates) -> list[dict]:
    out = []
    for r in rows:
        pred = ctx.predict_row(r)
        if pred is None:
            continue
        history = history_index.history_as_of(r["player_id"], r["game_date"])
        if len(history) < 5:
            continue
        out.append({
            "game_id": r["game_id"], "game_date": r["game_date"], "season": r["season"],
            "player_id": r["player_id"],
            "actual_sog": r["actual_sog"], "actual_goals": r["actual_goals"],
            "actual_assists": r["actual_assists"], "actual_points": r["actual_points"],
            "mu_sog": pred["sog"]["mu"], "sog_probs": pred["sog"]["probs"],
            "p_goal_1plus": pred["goals"]["probs"].get(1),
            "p_assist_1plus": pred["assists"]["probs"].get(1),
            "p_point_1plus": pred["points"]["probs"].get(1),
            "goal_rate": goal_rates.shrunk_rate(history), "point_rate": point_rates.shrunk_rate(history),
            "assist_rate": assist_rates.shrunk_rate(history),
        })
    return out


def compute_pair_probs(ex: dict, combo: dict, rho_by_name: dict[str, float]) -> dict:
    if combo["kind"] == "logical":
        p_a = ex["p_goal_1plus"] if combo["field_a"] == "actual_goals" else ex["p_assist_1plus"]
        p_b = ex["p_point_1plus"]
        exact = jm.logical_control_probability(p_a)
        # Part 24/25: the exact logical identity P(A subset B) = P(A) is only
        # Frechet-consistent when the two FROZEN marginals themselves agree
        # that P(A) <= P(B) (as the true logical relationship requires). Real
        # frozen-marginal incoherence exists here (Section L/coherence_violations:
        # p_assist_1plus > p_point_1plus on ~8% of real rows) -- clipping to the
        # Frechet bound is the disclosed, non-destructive reconciliation (Part 25),
        # never a silent edit to either RAW marginal.
        coherent = jm.clip_to_frechet(exact, p_a, p_b)
        naive = p_a * p_b
        return {"p_a": p_a, "p_b": p_b, "naive": naive, "structural": coherent, "exact": exact,
                "coherent_differs_from_exact": abs(coherent - exact) > 1e-9}

    x_sog = combo["x_sog"]
    p_sog = ex["sog_probs"].get(x_sog, cm_poisson_sf(ex["mu_sog"], x_sog))
    if combo["event"] == "goals":
        p_event, rate = ex["p_goal_1plus"], ex["goal_rate"]
    elif combo["event"] == "assists":
        p_event, rate = ex["p_assist_1plus"], ex["assist_rate"]
    else:
        p_event, rate = ex["p_point_1plus"], ex["point_rate"]

    naive = p_sog * p_event
    structural_raw = jm.structural_joint_sog_event(ex["mu_sog"], rate, x_sog, combo["x_event"])
    structural = jm.clip_to_frechet(structural_raw, p_sog, p_event)
    rho = rho_by_name[combo["name"]]
    copula = jm.gaussian_copula_joint_upper_tail(p_sog, p_event, rho)
    return {"p_a": p_sog, "p_b": p_event, "naive": naive, "structural": structural, "copula": copula}


def cm_poisson_sf(mu, k):
    from research.player_sog import count_models as cm
    return cm.poisson_sf_at_least(k, mu)


def combo_actual(ex: dict, combo: dict) -> float:
    if combo["kind"] == "logical":
        return 1.0 if (ex[combo["field_a"]] >= combo["x_a"] and ex[combo["field_b"]] >= combo["x_b"]) else 0.0
    field = combo["conv_field"]
    return 1.0 if (ex["actual_sog"] >= combo["x_sog"] and ex[field] >= combo["x_event"]) else 0.0


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
    bands = [(0.0, .05), (.05, .1), (.1, .2), (.2, .3), (.3, .5), (.5, 1.01)]
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


if __name__ == "__main__":
    print("Loading joint scoring corpus...")
    rows = jf.load_joint_scoring_corpus()
    history_index = jf.JointScoringHistoryIndex(rows)
    tuning_rows = [r for r in rows if r["season"] == TUNING_SEASON]
    goal_rates = jm.ConversionRates(tuning_rows, "actual_goals")
    point_rates = jm.ConversionRates(tuning_rows, "actual_points")
    assist_rates = jm.ConversionRates(tuning_rows, "actual_assists")
    print(f"  {len(rows)} joint rows; league goal/shot={goal_rates.league_rate:.4f} "
          f"point/shot={point_rates.league_rate:.4f} assist/shot={assist_rates.league_rate:.4f}")

    print("\n=== Part 24: marginal logical-coherence audit (TUNING, real frozen marginals) ===")
    ctx = mp.ScoringMarginalContext()

    print("Building augmented examples (TUNING + EVAL seasons)...")
    scored_seasons = [TUNING_SEASON] + EVAL_SEASONS
    examples_by_season: dict[int, list[dict]] = {}
    for s in scored_seasons:
        season_rows = [r for r in rows if r["season"] == s]
        examples_by_season[s] = build_augmented_examples(season_rows, ctx, history_index,
                                                           goal_rates, point_rates, assist_rates)
        print(f"  season {s}: {len(examples_by_season[s])} / {len(season_rows)} rows have all 4 frozen marginals")

    tuning_examples = examples_by_season[TUNING_SEASON]

    coherence_violations = {"goal_gt_point": 0, "assist_gt_point": 0, "goal_gt_sog1plus": 0, "n": 0}
    for ex in tuning_examples:
        coherence_violations["n"] += 1
        p_sog1 = ex["sog_probs"].get(1, cm_poisson_sf(ex["mu_sog"], 1))
        if ex["p_goal_1plus"] > ex["p_point_1plus"] + 1e-9:
            coherence_violations["goal_gt_point"] += 1
        if ex["p_assist_1plus"] > ex["p_point_1plus"] + 1e-9:
            coherence_violations["assist_gt_point"] += 1
        if ex["p_goal_1plus"] > p_sog1 + 1e-9:
            coherence_violations["goal_gt_sog1plus"] += 1
    print(coherence_violations)

    print("\n=== Part 7/17: raw dependence (SOG-Goal, SOG-Assist, SOG-Point, Goal-Assist) ===")
    raw_corr = {
        "sog_goal": _pearson([e["actual_sog"] for e in tuning_examples], [e["actual_goals"] for e in tuning_examples]),
        "sog_assist": _pearson([e["actual_sog"] for e in tuning_examples], [e["actual_assists"] for e in tuning_examples]),
        "sog_point": _pearson([e["actual_sog"] for e in tuning_examples], [e["actual_points"] for e in tuning_examples]),
        "goal_assist": _pearson([e["actual_goals"] for e in tuning_examples], [e["actual_assists"] for e in tuning_examples]),
    }
    print(raw_corr)

    print("Fitting Gaussian copula rho per structural combination on TUNING...")
    resid_sog = [(e["actual_sog"] - e["mu_sog"]) / math.sqrt(max(e["mu_sog"], 1e-6)) for e in tuning_examples]
    rho_by_name = {}
    for combo in PAIR_COMBINATIONS:
        if combo["kind"] != "structural":
            continue
        event_field = "actual_" + combo["event"]
        rate_field = {"goals": "goal_rate", "assists": "assist_rate", "points": "point_rate"}[combo["event"]]
        resid_event = [(e[event_field] - e[rate_field] * e["mu_sog"]) /
                       math.sqrt(max(e[rate_field] * e["mu_sog"], 1e-6)) for e in tuning_examples]
        rho_by_name[combo["name"]] = jm.fit_gaussian_copula_rho(resid_sog, resid_event)
    print(rho_by_name)

    print("Scoring pair combinations on EVAL seasons...")
    pair_results = {}
    for combo in PAIR_COMBINATIONS:
        if combo["kind"] == "logical":
            field_a, x_a, field_b, x_b = combo["field_a"], combo["x_a"], combo["field_b"], combo["x_b"]
        else:
            field_a, x_a = combo["field_sog"], combo["x_sog"]
            field_b, x_b = combo["conv_field"], combo["x_event"]
        emp_rate, emp_n = jm.league_empirical_joint_rate(tuning_rows, field_a, x_a, field_b, x_b)
        cond_rate, cond_n = jm.league_conditional_rate(tuning_rows, field_a, x_a, field_b, x_b)

        season_blocks = {}
        for s in EVAL_SEASONS:
            exs = examples_by_season[s]
            actuals = [combo_actual(ex, combo) for ex in exs]
            n_positive = sum(actuals)
            block = {"n": len(exs), "n_positive": n_positive, "kind": combo["kind"]}
            frechet_violations = 0
            coherence_clipped_count = 0
            for ex in exs:
                probs = compute_pair_probs(ex, combo, rho_by_name)
                naive = probs["naive"]
                if combo["kind"] == "logical":
                    if probs["coherent_differs_from_exact"]:
                        coherence_clipped_count += 1
                    shrunk_emp = jm.shrunk_empirical_joint(emp_rate, emp_n, naive)
                    cond_emp = cond_rate * probs["p_b"]
                    cand = {"A_naive_independence": naive, "B_shrunk_empirical_joint": shrunk_emp,
                            "C_conditional_empirical": cond_emp, "D_structural_conditional": probs["structural"],
                            "E_gaussian_copula": probs["structural"]}
                else:
                    shrunk_emp = jm.shrunk_empirical_joint(emp_rate, emp_n, naive)
                    cond_emp = cond_rate * probs["p_b"]
                    cand = {"A_naive_independence": naive, "B_shrunk_empirical_joint": shrunk_emp,
                            "C_conditional_empirical": cond_emp, "D_structural_conditional": probs["structural"],
                            "E_gaussian_copula": probs["copula"]}
                    lo, hi = jm.frechet_bounds(probs["p_a"], probs["p_b"])
                    if not (lo - 1e-9 <= probs["structural"] <= hi + 1e-9):
                        frechet_violations += 1
                ex.setdefault("pair_candidates", {})[combo["name"]] = cand
            block["frechet_violations"] = frechet_violations
            block["coherence_clipped_count"] = coherence_clipped_count
            for name in ALL_CANDIDATE_NAMES:
                probs_list = [ex["pair_candidates"][combo["name"]][name] for ex in exs]
                b = statistics.fmean(brier(p, y) for p, y in zip(probs_list, actuals))
                ll = statistics.fmean(log_loss(p, y) for p, y in zip(probs_list, actuals))
                block[name] = {"brier": b, "log_loss": ll, "calibration": calibration_bins(probs_list, actuals)}
            season_blocks[s] = block

        # Winner selection is DATA-DRIVEN, never hardcoded to D_structural_conditional:
        # some combos (Section: SOG+Assist, SOG+Point) show the structural shot-conversion
        # architecture losing to the Gaussian copula benchmark -- a real, honest finding
        # (assists/points are not generated from the player's OWN shots the way goals are),
        # so the candidate actually tested against naive is whichever of B/C/D/E has the
        # best mean Brier pooled across both eval seasons.
        if combo["kind"] == "structural":
            non_naive = ("B_shrunk_empirical_joint", "C_conditional_empirical",
                         "D_structural_conditional", "E_gaussian_copula")
            pooled_brier = {name: statistics.fmean(season_blocks[s][name]["brier"] for s in EVAL_SEASONS)
                             for name in non_naive}
            winner_name = min(pooled_brier, key=pooled_brier.get)
        else:
            winner_name = "D_structural_conditional"  # the coherent logical-identity answer

        for s in EVAL_SEASONS:
            exs = examples_by_season[s]
            block = season_blocks[s]
            block["winner_candidate"] = winner_name
            actuals = [combo_actual(ex, combo) for ex in exs]
            n_positive = block["n_positive"]
            if combo["kind"] == "structural" and n_positive >= MIN_JOINT_POSITIVE_EVENTS:
                naive_probs = [ex["pair_candidates"][combo["name"]]["A_naive_independence"] for ex in exs]
                winner_probs = [ex["pair_candidates"][combo["name"]][winner_name] for ex in exs]
                naive_briers = [brier(p, y) for p, y in zip(naive_probs, actuals)]
                winner_briers = [brier(p, y) for p, y in zip(winner_probs, actuals)]
                block["bootstrap_winner_vs_naive"] = {
                    "game_clustered": game_clustered_bootstrap(exs, naive_briers, winner_briers),
                    "date_clustered": date_clustered_bootstrap(exs, naive_briers, winner_briers),
                }
            elif combo["kind"] == "structural":
                block["bootstrap_winner_vs_naive"] = "INSUFFICIENT_DATA"
            else:
                block["bootstrap_winner_vs_naive"] = "NOT_APPLICABLE_LOGICAL_IDENTITY"

            mean_naive = statistics.fmean(ex["pair_candidates"][combo["name"]]["A_naive_independence"] for ex in exs)
            mean_winner = statistics.fmean(ex["pair_candidates"][combo["name"]][winner_name] for ex in exs)
            block["dependence_lift"] = {"mean_naive_p": mean_naive, "mean_winner_p": mean_winner,
                                          "lift_ratio": (mean_winner / mean_naive) if mean_naive > 0 else None}

            # Post-hoc Frechet check against the ACTUAL WINNING candidate (Part 23
            # applies to whichever probability is reported, not only D_structural).
            winner_violations = 0
            for ex in exs:
                probs = compute_pair_probs(ex, combo, rho_by_name)
                winner_p = ex["pair_candidates"][combo["name"]][winner_name]
                lo, hi = jm.frechet_bounds(probs["p_a"], probs["p_b"])
                if not (lo - 1e-9 <= winner_p <= hi + 1e-9):
                    winner_violations += 1
            block["winner_frechet_violations"] = winner_violations
        pair_results[combo["name"]] = {"combo": combo, "winner_candidate": winner_name, "by_season": season_blocks}
        print(f"  {combo['name']}: winner={winner_name}")

    print("\n=== Part 30/31: redundant-leg detection for three-way combinations ===")
    triple_results = {}
    for triple in TRIPLE_COMBINATIONS:
        redundant = jm.detect_redundant_leg(triple["labels"])
        triple_results[triple["name"]] = {
            "labels": triple["labels"], "redundant_leg_detected": redundant,
            "reduces_to": triple["reduces_to"],
            "note": f"Detected {redundant} as fully redundant (implied by another leg) -- "
                    f"this three-way combination carries NO additional information beyond "
                    f"{triple['reduces_to']}, and is scored identically to it, not double-counted."
        }
        print(triple["name"], triple_results[triple["name"]])

    print("\n=== Part 26/27: Points/Assists coherence vs jointly-implied Goals+Assists ===")
    points_coherence = {}
    for s in EVAL_SEASONS:
        exs = examples_by_season[s]
        diffs = [ex["p_point_1plus"] - min(1.0, (ex["p_goal_1plus"] or 0) + (ex["p_assist_1plus"] or 0))
                 for ex in exs]
        points_coherence[s] = {"n": len(diffs), "mean_diff": statistics.fmean(diffs),
                                 "abs_mean_diff": statistics.fmean(abs(d) for d in diffs)}
        print(s, points_coherence[s])

    def _sha(path):
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()

    freeze_manifest = {
        "experiment_id": "joint_scoring_dependence_v1",
        "marginal_model_versions": {
            "player_sog": "headline_stage from research/player_sog_results.json (frozen, unchanged)",
            "goals": "locked candidate E from research/player_goals_results.json (frozen, unchanged)",
            "assists": "M4_plus_h2h from research/player_assists_results.json (frozen, unchanged)",
            "points": "D_empirical_distribution (shrunk empirical baseline) from "
                      "research/player_points_results.json -- NOT the GLM, per Part 4",
        },
        "threshold_matrix": [c["name"] for c in PAIR_COMBINATIONS] + [c["name"] for c in TRIPLE_COMBINATIONS],
        "logical_implication_map": jm.LOGICAL_IMPLICATIONS,
        "conditional_goal_model": "Goals | SOG=n ~ Binomial(n, shrunk goal-per-shot rate), "
                                   "TUNING-fit, shrunk toward league rate by shot volume (k=150)",
        "assist_point_conditional_methodology": "Assists/Points | SOG=n ~ Binomial(n, shrunk "
                                                 "per-shot conversion rate) -- same architecture "
                                                 "as Goals, tested fresh (not assumed to transfer)",
        "marginal_reconciliation_policy": "Frechet clipping against FROZEN marginals applied to "
                                           "every structural probability from the start (Part 11/23) "
                                           "-- the coherence bug found in the prior joint slice is not "
                                           "repeated here",
        "frechet_policy": "clip_to_frechet(structural, p_a, p_b) using the frozen marginals actually "
                          "used for pricing, never the structural model's own internal marginal",
        "shrinkage": {"conversion_rate_k_shots": 150, "empirical_joint_k": 2000},
        "confidence_methodology": "NOT redesigned -- marginal confidence labels untouched (Part 41)",
        "joint_conservative_methodology": "RESEARCH -- not yet operationalized (Part 43)",
        "code_hashes": {
            "run_joint_scoring_dependence_model.py": _sha(
                str(REPO_ROOT / "research" / "run_joint_scoring_dependence_model.py")),
            "joint_scoring_dependence/joint_models.py": _sha(
                str(REPO_ROOT / "research" / "joint_scoring_dependence" / "joint_models.py")),
            "joint_scoring_dependence/marginal_provenance.py": _sha(
                str(REPO_ROOT / "research" / "joint_scoring_dependence" / "marginal_provenance.py")),
            "joint_scoring_dependence/build_joint_scoring_corpus.py": _sha(
                str(REPO_ROOT / "research" / "joint_scoring_dependence" / "build_joint_scoring_corpus.py")),
        },
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    print("\n=== Part 25/28/29: Monte Carlo sampler verification ===")
    repr_mu_sog = statistics.fmean(e["mu_sog"] for e in tuning_examples)
    repr_goal_rate = goal_rates.league_rate
    repr_assist_rate = assist_rates.league_rate
    mc_samples = jm.sample_scoring_outcomes(repr_mu_sog, repr_goal_rate, repr_assist_rate, n_samples=20000)
    mc_p_goal1 = sum(1 for g in mc_samples["goals"] if g >= 1) / 20000
    mc_p_assist1 = sum(1 for a in mc_samples["assists"] if a >= 1) / 20000
    analytic_p_goal1 = jm.structural_marginal_event_sf(repr_mu_sog, repr_goal_rate, 1)
    analytic_p_assist1 = jm.structural_marginal_event_sf(repr_mu_sog, repr_assist_rate, 1)
    goals_le_sog_violations = sum(1 for g, s in zip(mc_samples["goals"], mc_samples["sog"]) if g > s)
    points_identity_violations = sum(1 for p, g, a in zip(mc_samples["points"], mc_samples["goals"],
                                                            mc_samples["assists"]) if p != g + a)
    monte_carlo_verification = {
        "mu_sog_used": repr_mu_sog, "goal_rate_used": repr_goal_rate, "assist_rate_used": repr_assist_rate,
        "n_samples": 20000,
        "goal_1plus": {"monte_carlo": mc_p_goal1, "analytic": analytic_p_goal1,
                       "abs_diff": abs(mc_p_goal1 - analytic_p_goal1)},
        "assist_1plus": {"monte_carlo": mc_p_assist1, "analytic": analytic_p_assist1,
                         "abs_diff": abs(mc_p_assist1 - analytic_p_assist1)},
        "goals_le_sog_violations": goals_le_sog_violations,
        "points_equals_goals_plus_assists_violations": points_identity_violations,
    }
    print(monte_carlo_verification)

    print("\n=== Part 45: representative examples ===")
    latest = examples_by_season[EVAL_SEASONS[-1]]

    def summarize(e, combo_name):
        c = e["pair_candidates"][combo_name]
        winner_name = pair_results[combo_name]["winner_candidate"]
        combo = next(pc for pc in PAIR_COMBINATIONS if pc["name"] == combo_name)
        p_marginal_lookup = {"goals": e["p_goal_1plus"], "assists": e["p_assist_1plus"], "points": e["p_point_1plus"]}
        p_a = p_marginal_lookup.get(combo.get("event"))  # None only for a "logical"-kind combo (see p_b)
        p_b = e["p_point_1plus"] if combo["kind"] == "logical" else None
        return {"game_id": e["game_id"], "game_date": e["game_date"], "player_id": e["player_id"],
                "p_sog_marginal": e["sog_probs"].get(combo.get("x_sog")), "p_event_marginal": p_a,
                "p_point_marginal_if_logical": p_b, "naive": c["A_naive_independence"],
                "winner_candidate": winner_name, "winner_probability": c[winner_name],
                "dependence_lift": (c[winner_name] / c["A_naive_independence"]
                                     if c["A_naive_independence"] > 0 else None),
                "actual_sog": e["actual_sog"], "actual_goals": e["actual_goals"],
                "actual_assists": e["actual_assists"], "actual_points": e["actual_points"]}

    by_vol = sorted(latest, key=lambda e: -e["mu_sog"])
    high_vol, low_vol = by_vol[0], by_vol[-1]
    _sog3_goal_winner = pair_results["SOG3_GOAL"]["winner_candidate"]
    by_lift = sorted(latest, key=lambda e: -(e["pair_candidates"]["SOG3_GOAL"][_sog3_goal_winner] /
                                              max(e["pair_candidates"]["SOG3_GOAL"]["A_naive_independence"], 1e-9)))
    high_lift, low_lift = by_lift[0], by_lift[-1]

    def hit(e):
        return combo_actual(e, PAIR_COMBINATIONS[1]) == 1.0  # SOG3_GOAL

    model_hit = next((e for e in latest if hit(e)), latest[0])
    model_miss = next((e for e in latest if not hit(e)), latest[0])

    representative_examples = {
        "high_volume_shooter_anytime_goal": summarize(high_vol, "SOG3_GOAL"),
        "low_volume_shooter_anytime_goal": summarize(low_vol, "SOG3_GOAL"),
        "sog_and_assist": summarize(high_vol, "SOG3_ASSIST"),
        "sog_and_point": summarize(high_vol, "SOG3_POINT"),
        "goal_and_point_structural_identity": {
            "note": "P(Goal>=1 AND Point>=1) = P(Goal>=1) exactly",
            "p_goal": latest[0]["p_goal_1plus"], "p_point": latest[0]["p_point_1plus"],
        },
        "assist_and_point_structural_identity": {
            "note": "P(Assist>=1 AND Point>=1) = P(Assist>=1) exactly",
            "p_assist": latest[0]["p_assist_1plus"], "p_point": latest[0]["p_point_1plus"],
        },
        "high_dependence_lift": summarize(high_lift, "SOG3_GOAL"),
        "low_dependence_lift": summarize(low_lift, "SOG3_GOAL"),
        "model_hit": summarize(model_hit, "SOG3_GOAL"),
        "model_miss": summarize(model_miss, "SOG3_GOAL"),
    }

    full_results = {
        "config": {"warmup_season": WARMUP_SEASON, "tuning_season": TUNING_SEASON, "eval_seasons": EVAL_SEASONS,
                   "min_joint_positive_events": MIN_JOINT_POSITIVE_EVENTS},
        "corpus_size": {"joint_rows": len(rows)},
        "examples_by_season_n": {s: len(exs) for s, exs in examples_by_season.items()},
        "conversion_rates": {"goal_per_shot": goal_rates.league_rate, "point_per_shot": point_rates.league_rate,
                              "assist_per_shot": assist_rates.league_rate},
        "coherence_violations": coherence_violations,
        "raw_dependence": raw_corr,
        "rho_by_name": rho_by_name,
        "pair_results": pair_results,
        "triple_results": triple_results,
        "points_coherence": points_coherence,
        "monte_carlo_verification": monte_carlo_verification,
        "freeze_manifest": freeze_manifest,
        "representative_examples": representative_examples,
    }

    with open(RESULTS_PATH, "w") as f:
        json.dump(full_results, f, indent=2, default=str)
    print("\nWrote", RESULTS_PATH)
