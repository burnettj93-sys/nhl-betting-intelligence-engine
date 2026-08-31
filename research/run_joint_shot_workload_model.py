"""
Driver for the Joint Shot/Workload Dependence Foundation slice (Parts
1-56). Builds a real joint corpus linking Player SOG + Team SOG +
opposing Goalie Saves (research/joint_shot_workload/), reuses the three
FROZEN marginal engines UNCHANGED (research/joint_shot_workload/
marginal_provenance.py), and tests whether an explicit dependence model
beats naive multiplication of marginals for 7 pair combinations and 1
three-way combination (Part 24's own controlled matrix -- no more).

CRITICAL: this driver never refits Player SOG, Team SOG, or Goalie Saves.
Every marginal probability comes from each model's own frozen weights,
recomputed PIT-safely per row via each model's own build_example()/
compute_candidates() (or, for Player SOG, the existing shared
live_projection.project_player_sog helper) -- never a second, re-fit copy.

Read-only against nhl.db, models/, config.py, pricing/. Does not change
Player SOG, Player SOG by Period, Team SOG, Goalie Saves, the confidence
framework, or decision policy.
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

from research.joint_shot_workload import features as jf
from research.joint_shot_workload import joint_models as jm
from research.joint_shot_workload import marginal_provenance as mp
from research.goalie_saves import hierarchy as gh
from research.goalie_saves import features as gf

WARMUP_SEASON = 20222023
TUNING_SEASON = 20232024
EVAL_SEASONS = [20242025, 20252026]

RESULTS_PATH = REPO_ROOT / "research" / "joint_shot_workload_results.json"

# Part 24's own controlled combination matrix -- exactly these 7 pairs + 1 triple.
PAIR_COMBINATIONS = [
    {"name": "PLAYER2_TEAM25", "family": "PLAYER_TEAM", "x_player": 2, "y_team": 25},
    {"name": "PLAYER3_TEAM30", "family": "PLAYER_TEAM", "x_player": 3, "y_team": 30},
    {"name": "PLAYER4_TEAM30", "family": "PLAYER_TEAM", "x_player": 4, "y_team": 30},
    {"name": "TEAM25_GOALIE20", "family": "TEAM_GOALIE", "y_team": 25, "z_saves": 20},
    {"name": "TEAM30_GOALIE25", "family": "TEAM_GOALIE", "y_team": 30, "z_saves": 25},
    {"name": "PLAYER3_GOALIE20", "family": "PLAYER_GOALIE", "x_player": 3, "z_saves": 20},
    {"name": "PLAYER4_GOALIE25", "family": "PLAYER_GOALIE", "x_player": 4, "z_saves": 25},
]
TRIPLE_COMBINATION = {"name": "PLAYER3_TEAM30_GOALIE20", "family": "THREE_WAY",
                       "x_player": 3, "y_team": 30, "z_saves": 20}

ALL_CANDIDATE_NAMES = ("A_naive_independence", "B_shrunk_empirical_joint", "C_conditional_empirical",
                        "D_structural_factorization", "E_gaussian_copula")

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


def build_augmented_examples(rows: list[dict], ctx: mp.MarginalContext,
                              player_index: jf.JointPlayerHistoryIndex,
                              share_rates: "jm.PlayerShareRates") -> list[dict]:
    out = []
    for r in rows:
        pred = ctx.predict_row(r)
        if pred is None:
            continue
        player_hist = player_index.history_as_of(r["player_id"], r["game_date"])
        if len(player_hist) < 5:
            continue
        share = share_rates.shrunk_share(player_hist)
        out.append({
            "game_id": r["game_id"], "game_date": r["game_date"], "season": r["season"],
            "player_id": r["player_id"], "player_team": r["player_team"], "opponent_team": r["opponent_team"],
            "actual_player_sog": r["actual_player_sog"], "actual_team_sog": r["actual_team_sog"],
            "actual_goalie_saves": r["actual_goalie_saves"], "multi_goalie_game": r["multi_goalie_game"],
            "mu_player": pred["player"]["mu"], "player_probs": pred["player"]["probs"],
            "mu_team": pred["team"]["mu"], "mu_goalie": pred["goalie"]["mu"],
            "player_share": share,
        })
    return out


def compute_pair_probs(ex: dict, combo: dict, params: "jm.StructuralParams") -> dict[str, float]:
    """Returns p_a/p_b from each quantity's own FROZEN marginal (the
    number actually used elsewhere for that quantity), and a structural
    joint probability. CRITICAL COHERENCE STEP (Part 25/26, discovered
    during this slice's own development): the structural model's
    internal player-SOG marginal (integrated out of the Binomial-share
    allocation) does NOT exactly equal the frozen Player SOG model's own
    marginal for the same player-game (Part 47's marginal-recovery check
    quantifies the real, honest gap: ~0.05 mean absolute difference at
    3+). Left unclipped, that gap let the structural joint probability
    nominally exceed the FROZEN marginal's own Frechet upper bound on
    thousands of real rows -- not a logic error in the joint math itself
    (which is internally coherent against its OWN implied marginal), but
    an incoherence between two independently-fit models of the same
    real quantity. Fixed here by clipping the reported structural
    probability to the Frechet bounds of the FROZEN marginals actually
    used for p_a/p_b -- the numbers callers would actually see and act
    on. See report Section AB for the full, disclosed account."""
    family = combo["family"]
    if family == "PLAYER_TEAM":
        x, y = combo["x_player"], combo["y_team"]
        p_a = ex["player_probs"].get(x, jm.structural_marginal_player_sf(ex["mu_team"], ex["player_share"], x))
        p_b = jm.structural_marginal_team_sf(ex["mu_team"], y)
        naive = p_a * p_b
        structural = jm.clip_to_frechet(jm.structural_joint_player_team(ex["mu_team"], ex["player_share"], x, y),
                                         p_a, p_b)
        return {"p_a": p_a, "p_b": p_b, "naive": naive, "structural": structural,
                "field_a": "actual_player_sog", "x_a": x, "field_b": "actual_team_sog", "x_b": y}
    if family == "TEAM_GOALIE":
        y, z = combo["y_team"], combo["z_saves"]
        p_a = jm.structural_marginal_team_sf(ex["mu_team"], y)
        p_b = jm.structural_marginal_goalie_sf(ex["mu_team"], params, z)
        naive = p_a * p_b
        structural = jm.clip_to_frechet(jm.structural_joint_team_goalie(ex["mu_team"], params, y, z), p_a, p_b)
        return {"p_a": p_a, "p_b": p_b, "naive": naive, "structural": structural,
                "field_a": "actual_team_sog", "x_a": y, "field_b": "actual_goalie_saves", "x_b": z}
    if family == "PLAYER_GOALIE":
        x, z = combo["x_player"], combo["z_saves"]
        p_a = ex["player_probs"].get(x, jm.structural_marginal_player_sf(ex["mu_team"], ex["player_share"], x))
        p_b = jm.structural_marginal_goalie_sf(ex["mu_team"], params, z)
        naive = p_a * p_b
        structural = jm.clip_to_frechet(
            jm.structural_joint_player_goalie(ex["mu_team"], ex["player_share"], params, x, z), p_a, p_b)
        return {"p_a": p_a, "p_b": p_b, "naive": naive, "structural": structural,
                "field_a": "actual_player_sog", "x_a": x, "field_b": "actual_goalie_saves", "x_b": z}
    raise ValueError(family)


def combo_actual(ex: dict, combo: dict) -> float:
    family = combo["family"]
    if family == "PLAYER_TEAM":
        return 1.0 if (ex["actual_player_sog"] >= combo["x_player"] and ex["actual_team_sog"] >= combo["y_team"]) else 0.0
    if family == "TEAM_GOALIE":
        return 1.0 if (ex["actual_team_sog"] >= combo["y_team"] and ex["actual_goalie_saves"] >= combo["z_saves"]) else 0.0
    if family == "PLAYER_GOALIE":
        return 1.0 if (ex["actual_player_sog"] >= combo["x_player"] and ex["actual_goalie_saves"] >= combo["z_saves"]) else 0.0
    if family == "THREE_WAY":
        return 1.0 if (ex["actual_player_sog"] >= combo["x_player"] and ex["actual_team_sog"] >= combo["y_team"]
                        and ex["actual_goalie_saves"] >= combo["z_saves"]) else 0.0
    raise ValueError(family)


def compute_triple_probs(ex: dict, combo: dict, params: "jm.StructuralParams") -> dict[str, float]:
    x, y, z = combo["x_player"], combo["y_team"], combo["z_saves"]
    p_player = ex["player_probs"].get(x, jm.structural_marginal_player_sf(ex["mu_team"], ex["player_share"], x))
    p_team = jm.structural_marginal_team_sf(ex["mu_team"], y)
    p_goalie = jm.structural_marginal_goalie_sf(ex["mu_team"], params, z)
    naive = p_player * p_team * p_goalie
    structural_raw = jm.structural_joint_three_way(ex["mu_team"], ex["player_share"], params, x, y, z)
    # Three-way Frechet bound: P(A n B n C) <= min(P(A),P(B),P(C)); lower bound
    # left at 0 (the general three-way lower Frechet bound is looser and less
    # useful here than the pairwise case) -- same coherence fix as compute_pair_probs.
    structural = max(0.0, min(structural_raw, p_player, p_team, p_goalie))
    return {"p_player": p_player, "p_team": p_team, "p_goalie": p_goalie, "naive": naive, "structural": structural}


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
    print("Loading joint corpus...")
    rows = jf.load_joint_corpus()
    player_index = jf.JointPlayerHistoryIndex(rows)
    tuning_rows = [r for r in rows if r["season"] == TUNING_SEASON]
    share_rates = jm.PlayerShareRates(tuning_rows)
    print(f"  {len(rows)} joint rows; league_avg_player_share(TUNING)={share_rates.league_avg_share:.4f}")

    goalie_rows = gf.load_goalie_corpus()
    tuning_starts = [r for r in goalie_rows if r["actual_started"] and r["season"] == TUNING_SEASON]
    tuning_league_save_pct = gh.GoalieSavePctRates(tuning_starts).league_save_pct
    params = jm.StructuralParams(tuning_rows, tuning_league_save_pct)
    print(f"  empty_net_dist(TUNING)={params.empty_net_dist} league_save_pct(TUNING)={params.league_save_pct:.4f}")

    print("Loading frozen marginal engines (Player SOG, Team SOG, Goalie Saves)...")
    ctx = mp.MarginalContext()

    print("Building augmented examples (TUNING + EVAL seasons) -- this recomputes 3 frozen "
          "marginal predictions per row, may take a few minutes...")
    scored_seasons = [TUNING_SEASON] + EVAL_SEASONS
    examples_by_season: dict[int, list[dict]] = {}
    for s in scored_seasons:
        season_rows = [r for r in rows if r["season"] == s]
        examples_by_season[s] = build_augmented_examples(season_rows, ctx, player_index, share_rates)
        print(f"  season {s}: {len(examples_by_season[s])} / {len(season_rows)} rows have all 3 frozen marginals")

    tuning_examples = examples_by_season[TUNING_SEASON]

    print("Fitting Gaussian copula rho per combination family on TUNING only...")
    resid_player = [(ex["actual_player_sog"] - ex["mu_player"]) / math.sqrt(max(ex["mu_player"], 1e-6))
                     for ex in tuning_examples]
    resid_team = [(ex["actual_team_sog"] - ex["mu_team"]) / math.sqrt(max(ex["mu_team"], 1e-6))
                  for ex in tuning_examples]
    resid_goalie = [(ex["actual_goalie_saves"] - ex["mu_goalie"]) / math.sqrt(max(ex["mu_goalie"], 1e-6))
                     for ex in tuning_examples]
    rho_player_team = jm.fit_gaussian_copula_rho(resid_player, resid_team)
    rho_team_goalie = jm.fit_gaussian_copula_rho(resid_team, resid_goalie)
    rho_player_goalie = jm.fit_gaussian_copula_rho(resid_player, resid_goalie)
    print(f"  rho(player,team)={rho_player_team:.4f} rho(team,goalie)={rho_team_goalie:.4f} "
          f"rho(player,goalie)={rho_player_goalie:.4f}")
    rho_by_family = {"PLAYER_TEAM": rho_player_team, "TEAM_GOALIE": rho_team_goalie,
                      "PLAYER_GOALIE": rho_player_goalie}

    print("\n=== Part 7: raw + residual dependence (Player SOG <-> Team SOG) ===")
    raw_corr_pt = _pearson([ex["actual_player_sog"] for ex in tuning_examples],
                            [ex["actual_team_sog"] for ex in tuning_examples])
    resid_corr_pt = _pearson(resid_player, resid_team)
    print(f"  raw={raw_corr_pt:.4f} residual={resid_corr_pt:.4f}")

    print("Scoring pair combinations on EVAL seasons (TUNING already consumed by rho/share fitting)...")
    pair_results = {}
    for combo in PAIR_COMBINATIONS:
        rho = rho_by_family[combo["family"]]
        family = combo["family"]
        field_a = "actual_team_sog" if family == "TEAM_GOALIE" else "actual_player_sog"
        field_b = "actual_goalie_saves" if family != "PLAYER_TEAM" else "actual_team_sog"
        x_a = combo["y_team"] if family == "TEAM_GOALIE" else combo["x_player"]
        x_b = combo["z_saves"] if family != "PLAYER_TEAM" else combo["y_team"]
        # TUNING-fit, computed ONCE per combo (not per example -- these are single
        # league-wide constants from the frozen TUNING corpus, Part 18/19).
        emp_rate, emp_n = jm.league_empirical_joint_rate(tuning_rows, field_a, x_a, field_b, x_b)
        cond_rate, cond_n = jm.league_conditional_rate(tuning_rows, field_a, x_a, field_b, x_b)

        for s in EVAL_SEASONS:
            for ex in examples_by_season[s]:
                probs = compute_pair_probs(ex, combo, params)
                naive = probs["naive"]
                structural = probs["structural"]
                shrunk_emp = jm.shrunk_empirical_joint(emp_rate, emp_n, naive)
                cond_emp = cond_rate * probs["p_b"]
                copula = jm.gaussian_copula_joint_upper_tail(probs["p_a"], probs["p_b"], rho)
                ex.setdefault("pair_candidates", {})[combo["name"]] = {
                    "A_naive_independence": naive, "B_shrunk_empirical_joint": shrunk_emp,
                    "C_conditional_empirical": cond_emp, "D_structural_factorization": structural,
                    "E_gaussian_copula": copula, "p_a": probs["p_a"], "p_b": probs["p_b"],
                }

        # Evaluate per-season
        season_blocks = {}
        for s in EVAL_SEASONS:
            exs = examples_by_season[s]
            actuals = [combo_actual(ex, combo) for ex in exs]
            n_positive = sum(actuals)
            block = {"n": len(exs), "n_positive": n_positive}
            for name in ALL_CANDIDATE_NAMES:
                probs_list = [ex["pair_candidates"][combo["name"]][name] for ex in exs]
                b = statistics.fmean(brier(p, y) for p, y in zip(probs_list, actuals))
                ll = statistics.fmean(log_loss(p, y) for p, y in zip(probs_list, actuals))
                block[name] = {"brier": b, "log_loss": ll,
                               "calibration": calibration_bins(probs_list, actuals)}
            if n_positive >= MIN_JOINT_POSITIVE_EVENTS:
                naive_probs = [ex["pair_candidates"][combo["name"]]["A_naive_independence"] for ex in exs]
                struct_probs = [ex["pair_candidates"][combo["name"]]["D_structural_factorization"] for ex in exs]
                naive_briers = [brier(p, y) for p, y in zip(naive_probs, actuals)]
                struct_briers = [brier(p, y) for p, y in zip(struct_probs, actuals)]
                block["bootstrap_structural_vs_naive"] = {
                    "game_clustered": game_clustered_bootstrap(exs, naive_briers, struct_briers),
                    "date_clustered": date_clustered_bootstrap(exs, naive_briers, struct_briers),
                }
            else:
                block["bootstrap_structural_vs_naive"] = "INSUFFICIENT_DATA"
            mean_naive = statistics.fmean(ex["pair_candidates"][combo["name"]]["A_naive_independence"] for ex in exs)
            mean_struct = statistics.fmean(ex["pair_candidates"][combo["name"]]["D_structural_factorization"] for ex in exs)
            block["dependence_lift"] = {"mean_naive_p": mean_naive, "mean_structural_p": mean_struct,
                                          "lift_ratio": (mean_struct / mean_naive) if mean_naive > 0 else None,
                                          "lift_diff": mean_struct - mean_naive}
            frechet_violations = 0
            for ex in exs:
                c = ex["pair_candidates"][combo["name"]]
                lo, hi = jm.frechet_bounds(c["p_a"], c["p_b"])
                if not (lo - 1e-9 <= c["D_structural_factorization"] <= hi + 1e-9):
                    frechet_violations += 1
            block["frechet_violations"] = frechet_violations
            season_blocks[s] = block
        pair_results[combo["name"]] = {"combo": combo, "field_a": field_a, "x_a": x_a,
                                         "field_b": field_b, "x_b": x_b, "by_season": season_blocks}
        print(f"  {combo['name']}: done")

    print("Scoring three-way combination...")
    triple = TRIPLE_COMBINATION
    for s in EVAL_SEASONS:
        for ex in examples_by_season[s]:
            probs = compute_triple_probs(ex, triple, params)
            naive = probs["naive"]
            structural = probs["structural"]
            ex["triple_candidates"] = {"A_naive_independence": naive, "D_structural_factorization": structural,
                                        "p_player": probs["p_player"], "p_team": probs["p_team"],
                                        "p_goalie": probs["p_goalie"]}
    triple_season_blocks = {}
    for s in EVAL_SEASONS:
        exs = examples_by_season[s]
        actuals = [combo_actual(ex, triple) for ex in exs]
        n_positive = sum(actuals)
        block = {"n": len(exs), "n_positive": n_positive}
        for name in ("A_naive_independence", "D_structural_factorization"):
            probs_list = [ex["triple_candidates"][name] for ex in exs]
            b = statistics.fmean(brier(p, y) for p, y in zip(probs_list, actuals))
            ll = statistics.fmean(log_loss(p, y) for p, y in zip(probs_list, actuals))
            block[name] = {"brier": b, "log_loss": ll, "calibration": calibration_bins(probs_list, actuals)}
        if n_positive >= MIN_JOINT_POSITIVE_EVENTS:
            naive_probs = [ex["triple_candidates"]["A_naive_independence"] for ex in exs]
            struct_probs = [ex["triple_candidates"]["D_structural_factorization"] for ex in exs]
            naive_briers = [brier(p, y) for p, y in zip(naive_probs, actuals)]
            struct_briers = [brier(p, y) for p, y in zip(struct_probs, actuals)]
            block["bootstrap_structural_vs_naive"] = {
                "game_clustered": game_clustered_bootstrap(exs, naive_briers, struct_briers),
                "date_clustered": date_clustered_bootstrap(exs, naive_briers, struct_briers),
            }
            block["status"] = "SCORED"
        else:
            block["bootstrap_structural_vs_naive"] = "INSUFFICIENT_DATA"
            block["status"] = "INSUFFICIENT_DATA"
        mean_naive = statistics.fmean(ex["triple_candidates"]["A_naive_independence"] for ex in exs)
        mean_struct = statistics.fmean(ex["triple_candidates"]["D_structural_factorization"] for ex in exs)
        block["dependence_lift"] = {"mean_naive_p": mean_naive, "mean_structural_p": mean_struct,
                                      "lift_ratio": (mean_struct / mean_naive) if mean_naive > 0 else None}
        triple_season_blocks[s] = block
    print("  triple:", {s: b["n_positive"] for s, b in triple_season_blocks.items()})

    print("\n=== Part 2/13: FULL_GAME-only sensitivity population ===")
    full_game_sensitivity = {}
    for combo in PAIR_COMBINATIONS:
        combo_sens = {}
        for s in EVAL_SEASONS:
            exs = [ex for ex in examples_by_season[s] if not ex["multi_goalie_game"]]
            actuals = [combo_actual(ex, combo) for ex in exs]
            n_positive = sum(actuals)
            if n_positive < MIN_JOINT_POSITIVE_EVENTS:
                combo_sens[s] = {"n": len(exs), "n_positive": n_positive, "status": "INSUFFICIENT_DATA"}
                continue
            naive_probs = [ex["pair_candidates"][combo["name"]]["A_naive_independence"] for ex in exs]
            struct_probs = [ex["pair_candidates"][combo["name"]]["D_structural_factorization"] for ex in exs]
            naive_briers = [brier(p, y) for p, y in zip(naive_probs, actuals)]
            struct_briers = [brier(p, y) for p, y in zip(struct_probs, actuals)]
            combo_sens[s] = {
                "n": len(exs), "n_positive": n_positive,
                "naive_brier": statistics.fmean(naive_briers), "structural_brier": statistics.fmean(struct_briers),
                "game_clustered": game_clustered_bootstrap(exs, naive_briers, struct_briers),
            }
        full_game_sensitivity[combo["name"]] = combo_sens
    print("  done:", list(full_game_sensitivity.keys()))

    print("\n=== Part 47: marginal recovery check (structural model vs frozen marginals, EVAL pooled) ===")
    # Team SOG and Goalie Saves marginals are NOT separately re-derived here --
    # structural_marginal_team_sf() IS cm.poisson_sf_at_least() on the same frozen
    # mu_team, and structural_marginal_goalie_sf() integrates the SAME frozen
    # mu_team through the accounting identity, so they recover their own inputs
    # exactly BY CONSTRUCTION (not a real check). The only genuine recovery
    # question is whether the BINOMIAL PLAYER-SHARE ALLOCATION (a real, separate
    # modeling choice, Part 8) reproduces the player's own independently-fitted
    # frozen marginal -- that comparison is real and reported below.
    marginal_recovery = {}
    for s in EVAL_SEASONS:
        exs = examples_by_season[s]
        player_diffs = []
        for ex in exs:
            player_frozen = ex["player_probs"].get(3)
            player_structural = jm.structural_marginal_player_sf(ex["mu_team"], ex["player_share"], 3)
            if player_frozen is not None:
                player_diffs.append(player_structural - player_frozen)
        marginal_recovery[s] = {
            "n": len(player_diffs),
            "player_3plus_mean_diff_structural_minus_frozen": statistics.fmean(player_diffs) if player_diffs else None,
            "player_3plus_abs_mean_diff": statistics.fmean(abs(d) for d in player_diffs) if player_diffs else None,
        }
        print(s, marginal_recovery[s])

    print("\n=== Part 49: representative examples ===")

    def summarize_pair(ex, combo_name):
        c = ex["pair_candidates"][combo_name]
        return {"game_id": ex["game_id"], "game_date": ex["game_date"], "player_id": ex["player_id"],
                "player_team": ex["player_team"], "opponent_team": ex["opponent_team"],
                "p_a": c["p_a"], "p_b": c["p_b"], "naive": c["A_naive_independence"],
                "structural": c["D_structural_factorization"],
                "dependence_lift": (c["D_structural_factorization"] / c["A_naive_independence"]
                                     if c["A_naive_independence"] > 0 else None),
                "actual_player_sog": ex["actual_player_sog"], "actual_team_sog": ex["actual_team_sog"],
                "actual_goalie_saves": ex["actual_goalie_saves"]}

    latest = examples_by_season[EVAL_SEASONS[-1]]
    tg_name = "TEAM25_GOALIE20"
    pg_name = "PLAYER3_GOALIE20"
    pt_name = "PLAYER3_TEAM30"
    by_lift_pt = sorted(latest, key=lambda e: -(e["pair_candidates"][pt_name]["D_structural_factorization"] /
                                                 max(e["pair_candidates"][pt_name]["A_naive_independence"], 1e-9)))
    high_dep_lift = by_lift_pt[0]
    low_dep_lift = by_lift_pt[-1]

    def hit(e, combo):
        return combo_actual(e, combo) == 1.0

    tg_combo = next(c for c in PAIR_COMBINATIONS if c["name"] == tg_name)
    pg_combo = next(c for c in PAIR_COMBINATIONS if c["name"] == pg_name)
    model_hit = next((e for e in latest if hit(e, tg_combo)), latest[0])
    model_miss = next((e for e in latest if not hit(e, tg_combo)), latest[0])
    high_vol_player = max(latest, key=lambda e: e["mu_player"])
    low_vol_player = min(latest, key=lambda e: e["mu_player"])

    representative_examples = {
        "high_volume_player_high_team_env": summarize_pair(high_vol_player, pt_name),
        "low_volume_player_high_team_env": summarize_pair(low_vol_player, pt_name),
        "team_sog_over_and_goalie_saves_over": summarize_pair(model_hit, tg_name),
        "player_sog_over_and_goalie_saves_over": summarize_pair(model_hit, pg_name),
        "high_dependence_lift": summarize_pair(high_dep_lift, pt_name),
        "low_dependence_lift": summarize_pair(low_dep_lift, pt_name),
        "model_hit": summarize_pair(model_hit, tg_name),
        "model_miss": summarize_pair(model_miss, tg_name),
    }

    def _sha(path):
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()

    freeze_manifest = {
        "experiment_id": "joint_shot_workload_v1",
        "marginal_model_versions": {
            "player_sog": "headline_stage from research/player_sog_results.json (frozen, unchanged)",
            "team_sog": "winner from research/team_sog_results.json (frozen, unchanged)",
            "goalie_saves": "full_game_winner from research/goalie_saves_results.json (frozen, unchanged)",
        },
        "threshold_matrix": [c["name"] for c in PAIR_COMBINATIONS] + [triple["name"]],
        "joint_factorization": "P(Team SOG) x P(Player SOG | Team SOG ~ Binomial(n, shrunk_share)) x "
                                "P(Goalie Saves | Team SOG, empty-net dist, league save%)",
        "player_share_methodology": "research/joint_shot_workload/joint_models.py::PlayerShareRates -- "
                                     "pooled sum(player_sog)/sum(team_sog) over PIT-safe history, shrunk "
                                     "toward TUNING league-average share by game count (k=20)",
        "goalie_conditional_methodology": "Saves | shots_faced ~ Binomial(shots_faced, LEAGUE-average save%, "
                                           "never goalie-specific -- per the Goalie Saves slice's own finding "
                                           "that save-rate talent does not add value)",
        "multi_goalie_policy": "HEADLINE population = all real starter games (multi-goalie included); "
                                "see report Section B for the FULL_GAME-only sensitivity split",
        "empty_net_policy": "TUNING-season empirical empty-net-SOG-count distribution, applied as a "
                             "probability-weighted mixture over shots-faced",
        "dependence_parameters": {"rho_player_team": rho_player_team, "rho_team_goalie": rho_team_goalie,
                                   "rho_player_goalie": rho_player_goalie},
        "shrinkage": {"player_share_k_games": 20, "empirical_joint_k": 2000},
        "monte_carlo": "Gaussian copula upper-tail probability uses a fixed-seed (20232024) 20,000-draw "
                        "Monte Carlo integral (bivariate normal CDF has no closed form)",
        "calibration": "6-band calibration table per combination/season/candidate",
        "joint_conservative_methodology": "RESEARCH -- not yet operationalized (Part 39)",
        "code_hashes": {
            "run_joint_shot_workload_model.py": _sha(str(REPO_ROOT / "research" / "run_joint_shot_workload_model.py")),
            "joint_shot_workload/joint_models.py": _sha(
                str(REPO_ROOT / "research" / "joint_shot_workload" / "joint_models.py")),
            "joint_shot_workload/marginal_provenance.py": _sha(
                str(REPO_ROOT / "research" / "joint_shot_workload" / "marginal_provenance.py")),
            "joint_shot_workload/build_joint_corpus.py": _sha(
                str(REPO_ROOT / "research" / "joint_shot_workload" / "build_joint_corpus.py")),
        },
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    full_results = {
        "config": {"warmup_season": WARMUP_SEASON, "tuning_season": TUNING_SEASON, "eval_seasons": EVAL_SEASONS,
                   "min_joint_positive_events": MIN_JOINT_POSITIVE_EVENTS},
        "corpus_size": {"joint_rows": len(rows)},
        "examples_by_season_n": {s: len(exs) for s, exs in examples_by_season.items()},
        "league_avg_player_share": share_rates.league_avg_share,
        "empty_net_dist": params.empty_net_dist, "league_save_pct": params.league_save_pct,
        "raw_dependence": {
            "player_team_raw_corr": raw_corr_pt, "player_team_residual_corr": resid_corr_pt,
        },
        "rho_by_family": rho_by_family,
        "pair_results": pair_results,
        "full_game_sensitivity": full_game_sensitivity,
        "triple_result": {"combo": triple, "by_season": triple_season_blocks},
        "marginal_recovery": marginal_recovery,
        "freeze_manifest": freeze_manifest,
        "representative_examples": representative_examples,
    }

    with open(RESULTS_PATH, "w") as f:
        json.dump(full_results, f, indent=2, default=str)
    print("\nWrote", RESULTS_PATH)
