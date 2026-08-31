"""
Confidence Framework Redesign -- Cross-Prop Reliability Layer.

Does NOT refit or alter any raw prop model. Every raw probability used
here comes from re-scoring rows with each prop's ALREADY-LOCKED,
already-persisted weights (read from research/player_*_results.json),
exactly the same reuse pattern used for candidate C5 in
research/run_player_points_redesign.py. This script only builds and
evaluates CONFIDENCE (reliability) scoring on top of those frozen outputs.

EVALUATION STATUS: REUSED HISTORICAL DATA UNDER CONFIDENCE DEVELOPMENT
CYCLE. Row-level regeneration + candidate design is done for ASSISTS and
POINTS (Part 12 explicitly scopes the root-cause work to these two
sparse props); SOG and BLOCKS are cross-checked using their own
already-computed, already-stored confidence_breakdown aggregates
(research/player_sog_results.json, research/player_blocks_results.json)
-- both props' LOW-confidence bucket is NOT the negative-skill failure
this slice investigates, so a full row-level rebuild for them is out of
scope, and is stated as a scope decision rather than a hidden gap.

Rolling design (Part 14/15): DEV = TUNING_SEASON (2023-24, the same
tuning season each prop's own raw model was fit on) builds the
candidate-C/D skill-deviation tables and the B/C/D bucket cutoffs.
FOLD 1 = 2024-25 (first forward validation). FOLD 2 = 2025-26 (final,
strongest available check). This reuses exactly the season boundaries
already established for every prop this session.
"""
from __future__ import annotations

import hashlib
import json
import math
import statistics
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from research import elo_comparison as ec
from research.confidence_lab import reliability as rel
from research.player_assists import features as af
from research.player_points import features as ptf
from research.player_points import hierarchy as ph
from research.player_sog import count_models as cm
from research.run_player_sog_model import build_team_schedules, NHL_CORPUS_PATH
import research.run_player_assists_model as asm
import research.run_player_points_model as ppm

TUNING_SEASON = 20232024
FOLD1_SEASON = 20242025
FOLD2_SEASON = 20252026
OPPONENT_WINDOW_TARGET = 20


def file_sha256(rel_path: str) -> str:
    with open(REPO_ROOT / rel_path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


# ============================================================================
# Row-level re-derivation (read-only, reuses locked weights -- no refit).
# ============================================================================

def build_assists_examples():
    results = json.loads((REPO_ROOT / "research" / "player_assists_results.json").read_text())
    rows = af.load_assists_corpus()
    index = af.PlayerHistoryIndex(rows)
    totals = af.build_team_game_points_totals(rows)
    opponent_env = af.build_opponent_points_allowed(totals)
    league_avg = statistics.fmean(v["points_for"] for v in totals.values())
    games = ec.load_corpus(str(NHL_CORPUS_PATH))
    team_schedules = build_team_schedules(games)
    locked_weights_dict = results["stage_weights"]["M4_plus_h2h"]
    locked_weights = [locked_weights_dict[n] for n in cm.FEATURE_NAMES]
    alpha = results["alpha"] if results["alpha"] > 0.01 else None

    examples = {TUNING_SEASON: [], FOLD1_SEASON: [], FOLD2_SEASON: []}
    for row in rows:
        if row["season"] not in examples:
            continue
        ex = asm.build_example(row, index, team_schedules, opponent_env, league_avg)
        if ex is None:
            continue
        mu = cm.predict_mu(locked_weights, ex["feature_vector"])
        prob1 = cm.negbinom_sf_at_least(1, mu, alpha) if alpha else cm.poisson_sf_at_least(1, mu)
        actual = 1.0 if row["assists"] >= 1 else 0.0
        label, drivers, risks = cm.confidence_score(ex["history_len"], ex["recent_toi_cv"], ex["recent_assists_cv"],
                                                      ex["opponent_window_games"], OPPONENT_WINDOW_TARGET, ex["appearance_rate"])
        examples[row["season"]].append({
            "prop": "ASSISTS", "player_id": row["player_id"], "game_id": row["game_id"], "game_date": row["game_date"],
            "prob": prob1, "actual": actual, "history_len": ex["history_len"], "toi_cv": ex["recent_toi_cv"],
            "stat_cv": ex["recent_assists_cv"], "opponent_window_games": ex["opponent_window_games"],
            "appearance_rate": ex["appearance_rate"], "current_confidence": label,
            "position": row["position"], "is_forward": row["position"] in ph.FORWARD_POSITIONS,
        })
    return examples


def build_points_examples():
    results = json.loads((REPO_ROOT / "research" / "player_points_results.json").read_text())
    rows = ptf.load_points_corpus()
    index = ptf.PlayerHistoryIndex(rows)
    totals = ptf.build_team_game_points_totals(rows)
    team_offense_hist = ptf.build_team_offense_history(totals)
    opponent_env = ptf.build_opponent_points_allowed(totals)
    league_avg = statistics.fmean(v["points_for"] for v in totals.values())
    games = ec.load_corpus(str(NHL_CORPUS_PATH))
    team_schedules = build_team_schedules(games)
    pre_lock_rows = [r for r in rows if r["season"] in (ppm.WARMUP_SEASON, ppm.TUNING_SEASON) and r["game_date"] < ppm.TUNING_SPLIT_DATE]
    league_empirical_rates = {t: sum(1 for r in pre_lock_rows if r["points"] >= t) / len(pre_lock_rows) for t in ppm.THRESHOLDS}
    locked_weights_dict = results["stage_weights"][results["config"]["locked_stage"]]
    locked_weights = [locked_weights_dict[n] for n in results["config"]["feature_names"]]
    alpha = results["alpha"] if results["alpha"] > 0.01 else None

    examples = {TUNING_SEASON: [], FOLD1_SEASON: [], FOLD2_SEASON: []}
    for row in rows:
        if row["season"] not in examples:
            continue
        ex = ppm.build_example(row, index, team_schedules, team_offense_hist, opponent_env, league_avg, league_avg, league_empirical_rates)
        if ex is None:
            continue
        mu = cm.predict_mu(locked_weights, ex["feature_vector"])
        prob1 = cm.negbinom_sf_at_least(1, mu, alpha) if alpha else cm.poisson_sf_at_least(1, mu)
        actual = 1.0 if row["points"] >= 1 else 0.0
        label, drivers, risks = cm.confidence_score(ex["history_len"], ex["recent_toi_cv"], ex["recent_points_cv"],
                                                      ex["opponent_window_games"], OPPONENT_WINDOW_TARGET, ex["appearance_rate"])
        examples[row["season"]].append({
            "prop": "POINTS", "player_id": row["player_id"], "game_id": row["game_id"], "game_date": row["game_date"],
            "prob": prob1, "actual": actual, "history_len": ex["history_len"], "toi_cv": ex["recent_toi_cv"],
            "stat_cv": ex["recent_points_cv"], "opponent_window_games": ex["opponent_window_games"],
            "appearance_rate": ex["appearance_rate"], "current_confidence": label,
            "position": row["position"], "is_forward": ex["is_forward"],
        })
    return examples


def skill(brier, actual_rate):
    naive = actual_rate * (1 - actual_rate)
    return None if naive <= 0 else 1.0 - brier / naive


def bucket_metrics(exs):
    if not exs:
        return None
    briers = [(e["prob"] - e["actual"]) ** 2 for e in exs]
    loglosses = [-(e["actual"] * math.log(max(e["prob"], 1e-12))
                    + (1 - e["actual"]) * math.log(max(1 - e["prob"], 1e-12))) for e in exs]
    actual_rate = statistics.fmean(e["actual"] for e in exs)
    mean_pred = statistics.fmean(e["prob"] for e in exs)
    brier = statistics.fmean(briers)
    return {"n": len(exs), "actual_rate": actual_rate, "mean_pred": mean_pred, "brier": brier,
            "brier_skill_score": skill(brier, actual_rate), "log_loss": statistics.fmean(loglosses),
            "calibration_error": mean_pred - actual_rate, "abs_calibration_error": abs(mean_pred - actual_rate)}


def breakdown_by_label(exs, label_field):
    out = {}
    for lab in ("HIGH", "MEDIUM", "LOW"):
        sub = [e for e in exs if e[label_field] == lab]
        m = bucket_metrics(sub)
        if m is not None:
            out[lab] = m
    return out


def composition(exs):
    if not exs:
        return None
    return {
        "n": len(exs), "mean_history_len": statistics.fmean(e["history_len"] for e in exs),
        "mean_toi_cv": statistics.fmean(e["toi_cv"] for e in exs if e["toi_cv"] is not None),
        "mean_stat_cv": statistics.fmean(e["stat_cv"] for e in exs if e["stat_cv"] is not None),
        "mean_appearance_rate": statistics.fmean(e["appearance_rate"] for e in exs),
        "pct_defensemen": sum(1 for e in exs if not e["is_forward"]) / len(exs),
        "mean_opponent_window_games": statistics.fmean(e["opponent_window_games"] for e in exs),
        "prob_region_histogram": {f"{lo}-{lo+10}%": sum(1 for e in exs if lo / 100.0 <= e["prob"] < (lo + 10) / 100.0)
                                   for lo in range(0, 100, 10)},
    }


def run_all():
    assists_ex = build_assists_examples()
    points_ex = build_points_examples()

    dev = assists_ex[TUNING_SEASON] + points_ex[TUNING_SEASON]
    dev_assists = assists_ex[TUNING_SEASON]
    dev_points = points_ex[TUNING_SEASON]

    # ---- candidate B: continuous version of current inputs (per-prop, no DEV fit needed) ----
    for pool in (assists_ex, points_ex):
        for season, exs in pool.items():
            for e in exs:
                e["score_b"] = rel.candidate_b_score(e["history_len"], e["toi_cv"], e["stat_cv"],
                                                       e["opponent_window_games"], OPPONENT_WINDOW_TARGET, e["appearance_rate"])

    dev_b_scores = [e["score_b"] for e in dev]
    lo_b, hi_b = rel.cutoffs_from_dev(dev_b_scores)

    # ---- candidate C: pooled DEV tables (assists+points together) ----
    dev_c_examples = [{"prob": e["prob"], "actual": e["actual"], "history_len": e["history_len"], "toi_cv": e["toi_cv"]} for e in dev]
    tables_c_pooled = rel.build_skill_deviation_tables(dev_c_examples)

    # ---- candidate D: separate DEV tables per prop ----
    tables_d_assists = rel.build_skill_deviation_tables(
        [{"prob": e["prob"], "actual": e["actual"], "history_len": e["history_len"], "toi_cv": e["toi_cv"]} for e in dev_assists])
    tables_d_points = rel.build_skill_deviation_tables(
        [{"prob": e["prob"], "actual": e["actual"], "history_len": e["history_len"], "toi_cv": e["toi_cv"]} for e in dev_points])

    def apply_c_d(pool, tables_d):
        for season, exs in pool.items():
            for e in exs:
                e["score_c"] = rel.candidate_c_score(e["prob"], e["history_len"], e["toi_cv"], tables_c_pooled)
                e["score_d"] = rel.candidate_c_score(e["prob"], e["history_len"], e["toi_cv"], tables_d)

    apply_c_d(assists_ex, tables_d_assists)
    apply_c_d(points_ex, tables_d_points)

    dev_c_scores = [e["score_c"] for e in dev]
    lo_c, hi_c = rel.cutoffs_from_dev(dev_c_scores)
    # D is "prop-specific calibrated" (Part 11) -- both the skill-deviation
    # TABLES and the bucket CUTOFFS must be fit separately per prop, or a
    # shared cutoff computed across two differently-scaled per-prop score
    # distributions can silently starve one prop's HIGH/LOW bucket.
    lo_d_assists, hi_d_assists = rel.cutoffs_from_dev([e["score_d"] for e in dev_assists])
    lo_d_points, hi_d_points = rel.cutoffs_from_dev([e["score_d"] for e in dev_points])

    for pool, (lo_d, hi_d) in ((assists_ex, (lo_d_assists, hi_d_assists)), (points_ex, (lo_d_points, hi_d_points))):
        for season, exs in pool.items():
            for e in exs:
                e["label_a"] = e["current_confidence"]
                e["label_b"] = rel.label_from_score(e["score_b"], lo_b, hi_b)
                e["label_c"] = rel.label_from_score(e["score_c"], lo_c, hi_c)
                e["label_d"] = rel.label_from_score(e["score_d"], lo_d, hi_d)

    freeze_manifest = {
        "experiment_id": "confidence_framework_v1",
        "confidence_features": {
            "B": ["history_len (continuous)", "toi_cv (continuous)", "stat_cv (continuous)",
                  "opponent_window_games (continuous)", "appearance_rate (continuous)"],
            "C_and_D": ["probability_region_decile (Part 9)", "sample_size_bucket (Part 7)", "toi_cv_role_bucket (Part 8)"],
        },
        "bucket_boundaries": {"B": {"lo": lo_b, "hi": hi_b}, "C_pooled": {"lo": lo_c, "hi": hi_c},
                              "D_assists": {"lo": lo_d_assists, "hi": hi_d_assists},
                              "D_points": {"lo": lo_d_points, "hi": hi_d_points}},
        "prob_bins": rel.PROB_BINS, "sample_buckets": rel.SAMPLE_BUCKETS, "toi_cv_buckets": rel.TOI_CV_BUCKETS,
        "dev_season": TUNING_SEASON, "fold1_season": FOLD1_SEASON, "fold2_season_final_check": FOLD2_SEASON,
        "dev_n": len(dev), "raw_model_treatment": "read-only re-scoring of already-locked weights, no refitting",
        "source_code_hashes": {
            "research/confidence_lab/reliability.py": file_sha256("research/confidence_lab/reliability.py"),
            "research/run_confidence_diagnostics.py": file_sha256("research/run_confidence_diagnostics.py"),
        },
    }
    manifest_path = REPO_ROOT / "research" / "confidence_framework_manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(freeze_manifest, f, indent=2, sort_keys=True, default=str)

    # ==================================================================
    # FREEZE COMPLETE -- everything below scores FOLD1/FOLD2 for the
    # first time under the frozen candidate designs above.
    # ==================================================================

    results_by_prop_fold = {}
    for prop_name, pool in (("ASSISTS", assists_ex), ("POINTS", points_ex)):
        results_by_prop_fold[prop_name] = {}
        for fold_name, season in (("fold1_2024_25", FOLD1_SEASON), ("fold2_2025_26_final", FOLD2_SEASON)):
            exs = pool[season]
            results_by_prop_fold[prop_name][fold_name] = {
                "n": len(exs),
                "A_current": breakdown_by_label(exs, "label_a"),
                "B_simple_reliability": breakdown_by_label(exs, "label_b"),
                "C_calibrated_pooled": breakdown_by_label(exs, "label_c"),
                "D_calibrated_per_prop": breakdown_by_label(exs, "label_d"),
            }

    # ---- root cause composition (current system's LOW bucket, Part 12) ----
    root_cause = {}
    for prop_name, pool in (("ASSISTS", assists_ex), ("POINTS", points_ex)):
        all_eval = pool[FOLD1_SEASON] + pool[FOLD2_SEASON]
        low = [e for e in all_eval if e["label_a"] == "LOW"]
        high = [e for e in all_eval if e["label_a"] == "HIGH"]
        root_cause[prop_name] = {"LOW": composition(low), "HIGH_for_contrast": composition(high)}

    # ---- ordering consistency check across candidates/folds ----
    def is_monotonic(bd):
        if not all(k in bd for k in ("HIGH", "MEDIUM", "LOW")):
            return None
        return bd["HIGH"]["brier_skill_score"] >= bd["MEDIUM"]["brier_skill_score"] >= bd["LOW"]["brier_skill_score"]

    ordering_consistency = {}
    for prop_name in ("ASSISTS", "POINTS"):
        ordering_consistency[prop_name] = {}
        for fold_name in ("fold1_2024_25", "fold2_2025_26_final"):
            r = results_by_prop_fold[prop_name][fold_name]
            ordering_consistency[prop_name][fold_name] = {cand: is_monotonic(r[cand])
                                                            for cand in ("A_current", "B_simple_reliability", "C_calibrated_pooled", "D_calibrated_per_prop")}

    # ---- cross-check against SOG/Blocks' EXISTING stored aggregates (no rebuild) ----
    sog_results = json.loads((REPO_ROOT / "research" / "player_sog_results.json").read_text())
    blocks_results = json.loads((REPO_ROOT / "research" / "player_blocks_results.json").read_text())
    existing_breakdown_cross_check = {"SOG": sog_results["confidence_breakdown"], "BLOCKS": blocks_results["confidence_breakdown"]}

    # ---- bet-eligibility retrospective (Part 25, research only) ----
    bet_eligibility = {}
    for prop_name, pool in (("ASSISTS", assists_ex), ("POINTS", points_ex)):
        all_eval = pool[FOLD1_SEASON] + pool[FOLD2_SEASON]
        n_total = len(all_eval)
        n_low_current = sum(1 for e in all_eval if e["label_a"] == "LOW")
        low_brier = bucket_metrics([e for e in all_eval if e["label_a"] == "LOW"])
        bet_eligibility[prop_name] = {
            "n_total": n_total, "n_low_current_system": n_low_current,
            "pct_would_be_excluded_if_low_defaults_to_wait": n_low_current / n_total,
            "low_bucket_brier_skill": low_brier["brier_skill_score"] if low_brier else None,
        }

    test_proc = subprocess.run([sys.executable, "-m", "unittest", "discover", "tests"], cwd=str(REPO_ROOT), capture_output=True, text=True)

    out = {
        "evaluation_status": "REUSED HISTORICAL DATA UNDER CONFIDENCE DEVELOPMENT CYCLE",
        "freeze_manifest_path": str(manifest_path.relative_to(REPO_ROOT)),
        "dev_n": len(dev), "dev_n_assists": len(dev_assists), "dev_n_points": len(dev_points),
        "results_by_prop_fold": results_by_prop_fold,
        "root_cause_composition": root_cause,
        "ordering_consistency": ordering_consistency,
        "existing_breakdown_cross_check_sog_blocks": existing_breakdown_cross_check,
        "bet_eligibility_retrospective": bet_eligibility,
        "test_suite_returncode": test_proc.returncode,
        "test_suite_stderr_tail": "\n".join(test_proc.stderr.strip().splitlines()[-8:]),
    }
    return out


if __name__ == "__main__":
    out = run_all()
    out_path = REPO_ROOT / "research" / "confidence_framework_results.json"
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, sort_keys=True, default=str)
    print(f"wrote {out_path}")
    print("test suite returncode:", out["test_suite_returncode"])
