"""
Driver for the controlled, real-data Elo candidate comparison (Parts 1-27
of the instruction this implements). Loads the real NHL results corpus,
runs the baseline and every tuning-grid candidate as ONE continuous
walk-forward trajectory each across all 4 real seasons (warm-up:
2022-23; tuning-selection: 2023-24; true untouched evaluation: 2024-25 +
2025-26 -- see research/elo_comparison.py's module docstring and
research/real_nhl_results/README.md for the STRICT PRIOR-GAME-DATE
policy this all runs under), selects the winning B/C parameterizations
using ONLY the tuning season, builds Candidate D from those winners, and
writes:

  - research/elo_comparison_results.json  (every computed number, for
    independent audit -- nothing in the .md report should disagree with
    this file)
  - ELO_REAL_DATA_COMPARISON_REPORT.md    (the human-readable report)

This script does not modify nhl.db, models/, config.py, or any other
production file. It is read-only against the research corpus and
write-only to the two output files named above.
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import config
from research import elo_comparison as ec

CORPUS_PATH = str(REPO_ROOT / "research" / "real_nhl_results" / "normalized_regular_season_games.jsonl")

WARMUP_SEASON = 20222023
TUNING_SEASON = 20232024
EVAL_SEASONS = [20242025, 20252026]

OTSO_GRID = [0.75, 0.67, 0.50]
MOV_CAP_GRID = [2, 3, 4]


def season_slice(records, seasons):
    seasons = set(seasons) if not isinstance(seasons, int) else {seasons}
    return [r for r in records if r["season"] in seasons]


def run_all():
    games = ec.load_corpus(CORPUS_PATH)
    all_seasons_present = {g["season"] for g in games}
    assert all_seasons_present == {WARMUP_SEASON, TUNING_SEASON, *EVAL_SEASONS}, all_seasons_present

    results = {"config": {}, "tuning": {}, "final_candidates": {}}

    # ---- Part 1: audit (values pulled directly from config.py / models/elo_model.py,
    # not retyped by hand) ----
    results["config"] = {
        "ELO_START": config.ELO_START,
        "ELO_K_FACTOR": config.ELO_K_FACTOR,
        "ELO_HOME_ADVANTAGE": config.ELO_HOME_ADVANTAGE,
        "ELO_SEASON_REGRESSION": config.ELO_SEASON_REGRESSION,
        "ELO_UPDATES_ON_BASE_EXPECTATION": config.ELO_UPDATES_ON_BASE_EXPECTATION,
    }

    # ---- baseline (Candidate A) ----
    baseline_records, baseline_state = ec.run_walkforward(games, weight_fn=None)

    # ---- tuning grid: B (OT/SO-aware) ----
    tuning_table_b = []
    for w in OTSO_GRID:
        recs, _ = ec.run_walkforward(games, weight_fn=ec.make_otso_weight_fn(w))
        tuning_season_recs = season_slice(recs, TUNING_SEASON)
        tuning_table_b.append({
            "otso_weight": w,
            "n": len(tuning_season_recs),
            "brier": ec.brier_score(tuning_season_recs),
            "log_loss": ec.log_loss(tuning_season_recs),
        })
    best_b = min(tuning_table_b, key=lambda r: r["brier"])

    # ---- tuning grid: C (capped MOV) ----
    tuning_table_c = []
    for cap in MOV_CAP_GRID:
        recs, _ = ec.run_walkforward(games, weight_fn=ec.make_mov_weight_fn(cap))
        tuning_season_recs = season_slice(recs, TUNING_SEASON)
        tuning_table_c.append({
            "mov_cap": cap,
            "n": len(tuning_season_recs),
            "brier": ec.brier_score(tuning_season_recs),
            "log_loss": ec.log_loss(tuning_season_recs),
        })
    best_c = min(tuning_table_c, key=lambda r: r["brier"])

    baseline_tuning = season_slice(baseline_records, TUNING_SEASON)
    results["tuning"] = {
        "tuning_season": TUNING_SEASON,
        "baseline": {"n": len(baseline_tuning), "brier": ec.brier_score(baseline_tuning),
                     "log_loss": ec.log_loss(baseline_tuning)},
        "candidate_b_grid": tuning_table_b,
        "candidate_c_grid": tuning_table_c,
        "selected_b_otso_weight": best_b["otso_weight"],
        "selected_c_mov_cap": best_c["mov_cap"],
    }

    # ---- final 4 candidates, each run ONCE more as its own full,
    # independent continuous trajectory (identical to how it was already
    # run above for B*/C*; re-run explicitly here for D and for clarity/
    # auditability of the "final candidates" section as a self-contained
    # block) ----
    final_weight_fns = {
        "A_baseline": None,
        f"B_otso_{best_b['otso_weight']}": ec.make_otso_weight_fn(best_b["otso_weight"]),
        f"C_mov_cap_{best_c['mov_cap']}": ec.make_mov_weight_fn(best_c["mov_cap"]),
        f"D_combined_otso_{best_b['otso_weight']}_mov_cap_{best_c['mov_cap']}":
            ec.make_combined_weight_fn(best_b["otso_weight"], best_c["mov_cap"]),
    }

    candidate_runs = {}
    for label, fn in final_weight_fns.items():
        recs, state = ec.run_walkforward(games, weight_fn=fn)
        candidate_runs[label] = {"records": recs, "state": state}

    baseline_label = "A_baseline"
    eval_game_ids_baseline = [r["game_id"] for r in season_slice(candidate_runs[baseline_label]["records"], EVAL_SEASONS)]

    for label, run in candidate_runs.items():
        recs = run["records"]
        full_eval = season_slice(recs, EVAL_SEASONS)
        eval_ids = [r["game_id"] for r in full_eval]
        assert eval_ids == eval_game_ids_baseline, f"{label} evaluated a different game set than baseline"

        season_table = ec.season_breakdown(recs)
        entry = {
            "label": label,
            "n_total": len(recs),
            "season_breakdown": {str(s): v for s, v in season_table.items()},
            "true_eval_n": len(full_eval),
            "true_eval_brier": ec.brier_score(full_eval),
            "true_eval_log_loss": ec.log_loss(full_eval),
            "true_eval_mean_pred": ec.mean_predicted_prob(full_eval),
            "true_eval_actual_rate": ec.actual_home_win_rate(full_eval),
            "true_eval_calibration_error": ec.calibration_error(full_eval),
            "true_eval_calibration_table": ec.calibration_table(full_eval),
            "true_eval_prob_distribution": ec.probability_distribution_summary(full_eval),
        }
        results["final_candidates"][label] = entry
        candidate_runs[label]["true_eval_records"] = full_eval

    # ---- deltas + paired bootstrap vs baseline, on the TRUE eval set only ----
    baseline_eval = candidate_runs[baseline_label]["true_eval_records"]
    base_brier_scores = ec.per_game_brier(baseline_eval)
    base_logloss_scores = ec.per_game_log_loss(baseline_eval)

    deltas = {}
    for label, run in candidate_runs.items():
        if label == baseline_label:
            continue
        cand_eval = run["true_eval_records"]
        cand_brier_scores = ec.per_game_brier(cand_eval)
        cand_logloss_scores = ec.per_game_log_loss(cand_eval)

        brier_boot = ec.paired_bootstrap_delta(base_brier_scores, cand_brier_scores)
        logloss_boot = ec.paired_bootstrap_delta(base_logloss_scores, cand_logloss_scores)

        base_brier = results["final_candidates"][baseline_label]["true_eval_brier"]
        cand_brier = results["final_candidates"][label]["true_eval_brier"]
        base_ll = results["final_candidates"][baseline_label]["true_eval_log_loss"]
        cand_ll = results["final_candidates"][label]["true_eval_log_loss"]

        deltas[label] = {
            "brier_abs_delta": cand_brier - base_brier,
            "brier_rel_delta_pct": (cand_brier - base_brier) / base_brier * 100.0,
            "log_loss_abs_delta": cand_ll - base_ll,
            "log_loss_rel_delta_pct": (cand_ll - base_ll) / base_ll * 100.0,
            "brier_bootstrap": brier_boot,
            "log_loss_bootstrap": logloss_boot,
        }
    results["deltas_vs_baseline_true_eval"] = deltas

    # ---- season-by-season consistency (true eval seasons only) ----
    consistency = {}
    for label in candidate_runs:
        if label == baseline_label:
            continue
        per_season = {}
        for s in EVAL_SEASONS:
            base_s = season_slice(baseline_eval, [s])
            cand_s = season_slice(candidate_runs[label]["true_eval_records"], [s])
            per_season[str(s)] = {
                "baseline_brier": ec.brier_score(base_s),
                "candidate_brier": ec.brier_score(cand_s),
                "brier_delta": ec.brier_score(cand_s) - ec.brier_score(base_s),
                "baseline_log_loss": ec.log_loss(base_s),
                "candidate_log_loss": ec.log_loss(cand_s),
                "log_loss_delta": ec.log_loss(cand_s) - ec.log_loss(base_s),
            }
        consistency[label] = per_season
    results["season_consistency"] = consistency

    # ---- rating stability: 3 illustrative teams, baseline vs the D candidate ----
    d_label = [l for l in candidate_runs if l.startswith("D_combined")][0]
    all_final_ratings_baseline = candidate_runs[baseline_label]["state"].ratings
    highest_team = max(all_final_ratings_baseline, key=all_final_ratings_baseline.get)
    lowest_team = min(all_final_ratings_baseline, key=all_final_ratings_baseline.get)
    illustrative_teams = sorted({highest_team, lowest_team, "TOR"})
    stability = {}
    for team in illustrative_teams:
        stability[team] = {
            "baseline": ec.rating_stability_summary(candidate_runs[baseline_label]["records"], team),
            d_label: ec.rating_stability_summary(candidate_runs[d_label]["records"], team),
        }
    results["rating_stability"] = stability

    # ---- representative examples (from the true eval seasons, first match by date order) ----
    def first_match(records, predicate):
        for r in sorted(records, key=lambda r: r["game_date"]):
            if predicate(r):
                return r
        return None

    eval_recs_baseline = {r["game_id"]: r for r in baseline_eval}
    eval_recs_d = {r["game_id"]: r for r in candidate_runs[d_label]["true_eval_records"]}

    examples_specs = [
        ("regulation_blowout", lambda r: r["period_type"] == "REG" and abs(r["home_score"] - r["away_score"]) >= 5),
        ("one_goal_regulation_win", lambda r: r["period_type"] == "REG" and abs(r["home_score"] - r["away_score"]) == 1),
        ("overtime_game", lambda r: r["period_type"] == "OT"),
        ("shootout_game", lambda r: r["period_type"] == "SO"),
    ]
    examples = {}
    for name, predicate in examples_specs:
        rec = first_match(baseline_eval, predicate)
        if rec is None:
            examples[name] = None
            continue
        base = eval_recs_baseline[rec["game_id"]]
        cand = eval_recs_d[rec["game_id"]]
        examples[name] = {
            "game_id": rec["game_id"], "game_date": rec["game_date"],
            "home_team": rec["home_team"], "away_team": rec["away_team"],
            "home_score": rec["home_score"], "away_score": rec["away_score"],
            "period_type": rec["period_type"],
            "rating_home_pregame": base["rating_home_pregame"],
            "rating_away_pregame": base["rating_away_pregame"],
            "p_home": base["p_home"],
            "baseline_home_elo_delta": base["home_elo_delta"],
            f"{d_label}_home_elo_delta": cand["home_elo_delta"],
            f"{d_label}_weight_applied": cand["weight_applied"],
        }
    results["representative_examples"] = examples
    results["d_label"] = d_label
    results["b_label"] = [l for l in candidate_runs if l.startswith("B_otso")][0]
    results["c_label"] = [l for l in candidate_runs if l.startswith("C_mov")][0]

    # ---- full test-suite re-run, captured verbatim ----
    proc = subprocess.run([sys.executable, "-m", "unittest", "discover", "tests"],
                           cwd=str(REPO_ROOT), capture_output=True, text=True)
    results["test_suite_stderr_tail"] = "\n".join(proc.stderr.strip().splitlines()[-5:])
    results["test_suite_returncode"] = proc.returncode

    return results, candidate_runs


if __name__ == "__main__":
    results, _ = run_all()
    out_path = REPO_ROOT / "research" / "elo_comparison_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, sort_keys=True, default=str)
    print(f"wrote {out_path}")
    print(f"test suite returncode: {results['test_suite_returncode']}")
    print(results["test_suite_stderr_tail"])
