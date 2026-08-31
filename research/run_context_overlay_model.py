"""
Driver for the Context-State Probability Overlay slice (Goals 1+ and
Points 1+ only, COLD_AND_TOI_DECLINE state only).

Reuses the EXACT frozen context-state definition from the completed
Player Context State Validation slice -- imports
research.run_player_context_state_model.build_prop_examples directly
(not a reimplementation) so the cold/hot/TOI-decline cutoffs are
byte-identical to that slice's own frozen output, per this slice's Part
1 ("do not change ... after seeing evaluation results").

Architecture: RAW MARGINAL (frozen, untouched) -> CONTEXT ADJUSTMENT
(fit on DEVELOPMENT/TUNING data only, frozen before EVAL scoring) ->
reported ADJUSTED PROBABILITY, always alongside the RAW probability
(Part 24: never overwrite raw). No decision_policy change. No
sportsbook odds. No refit of any frozen marginal.
"""
from __future__ import annotations

import hashlib
import datetime as dt
import json
import random
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from research.context_overlay import confidence_helpers as ch
from research.context_overlay import overlay_models as om
from research.player_context_state import context_state as pcs_cs
from research.player_context_state import marginal_provenance as pcs_mp
from research.player_points import features as ptf
from research.player_props import decision_policy
from research.run_player_context_state_model import (
    build_prop_examples, TUNING_SEASON, EVAL_SEASONS,
)

RESULTS_PATH = REPO_ROOT / "research" / "context_overlay_results.json"

PROPS = ["goals", "points"]
OVERLAY_MIN_DEV_N = 300
OVERLAY_MIN_EVAL_N = 150
N_RESAMPLES = 1000
BOOTSTRAP_SEED = 20242025
PROB_REGION_BINS = [(0.0, 0.1), (0.1, 0.2), (0.2, 0.3), (0.3, 0.4), (0.4, 0.5), (0.5, 1.01)]
MIN_CONFIDENCE_BUCKET_N = 30


def _apply_fn_for(winner_name: str, params: dict | None):
    if winner_name == "A_NO_ADJUSTMENT":
        return lambda p: om.candidate_a_apply(p)
    if winner_name == "B_FIXED_LOGIT_OFFSET":
        offset = params["offset"]
        return lambda p: om.candidate_b_apply(p, offset)
    if winner_name == "C_SHRUNK_LOGIT_OFFSET":
        offset = params["offset"]
        return lambda p: om.candidate_c_apply(p, offset)
    if winner_name == "D_BAYESIAN_CONTEXT_BLEND":
        shift = params["shift"]
        return lambda p: om.candidate_d_apply(p, shift)
    if winner_name == "E_ISOTONIC_BIN_RECAL":
        return lambda p: om.candidate_e_apply(p, params)
    raise ValueError(winner_name)


def paired_clustered_bootstrap(examples: list[dict], cluster_key: str, diffs: list[float],
                                n_resamples: int = N_RESAMPLES, seed: int = BOOTSTRAP_SEED) -> dict:
    by_cluster = defaultdict(list)
    for i, e in enumerate(examples):
        by_cluster[e[cluster_key]].append(i)
    clusters = list(by_cluster.keys())
    point_delta = statistics.fmean(diffs)
    rng = random.Random(seed)
    deltas = []
    for _ in range(n_resamples):
        idx = []
        for _ in range(len(clusters)):
            idx.extend(by_cluster[clusters[rng.randrange(len(clusters))]])
        deltas.append(statistics.fmean(diffs[i] for i in idx))
    deltas.sort()
    lo_i = int(0.025 * n_resamples)
    hi_i = min(int(0.975 * n_resamples), n_resamples - 1)
    frac_improved = sum(1 for d in deltas if d < 0) / n_resamples
    return {"point_delta": point_delta, "ci_low": deltas[lo_i], "ci_high": deltas[hi_i],
            "frac_improved": frac_improved, "n_resamples": n_resamples, "n_clusters": len(clusters)}


def classify_examples(examples_by_season: dict[int, list[dict]]) -> tuple[pcs_cs.StateThresholds, float]:
    tuning_examples = examples_by_season[TUNING_SEASON]
    thresholds = pcs_cs.StateThresholds([e["form_ratio"] for e in tuning_examples], pct=0.20)
    tuning_toi_ratios = sorted(v for v in (e["toi_ratio"] for e in tuning_examples) if v is not None)
    toi_decline_cutoff = tuning_toi_ratios[int(len(tuning_toi_ratios) * 0.20)] if tuning_toi_ratios else -0.3
    for season, exs in examples_by_season.items():
        for e in exs:
            e["state"] = thresholds.classify(e["form_ratio"])
            e["multi_state"] = pcs_cs.classify_multi_signal(e["state"], e["toi_ratio"], toi_decline_cutoff)
    return thresholds, toi_decline_cutoff


def confidence_labels_for(prop: str, ctx: pcs_mp.ContextMarginalContext, points_opponent_env: dict,
                           examples: list[dict]) -> list[str | None]:
    labels = []
    for e in examples:
        if prop == "goals":
            label = ch.goals_confidence_label(ctx.goals, e["player_id"], e["team"], e["opponent"], e["game_date"])
        else:
            label = ch.points_confidence_label(ctx.points, points_opponent_env, e["player_id"], e["team"],
                                                 e["opponent"], e["game_date"])
        labels.append(label)
    return labels


def persistence_diagnostic(examples_by_season: dict[int, list[dict]], cold_toi_examples: list[dict],
                            season: int) -> dict:
    by_player = defaultdict(list)
    for e in examples_by_season[season]:
        by_player[e["player_id"]].append(e)
    for lst in by_player.values():
        lst.sort(key=lambda e: e["game_date"])
    index_lookup = {}
    for pid, lst in by_player.items():
        for i, e in enumerate(lst):
            index_lookup[(pid, e["game_id"])] = (lst, i)

    offsets_resid = {1: [], 2: [], 3: []}
    for flagged in cold_toi_examples:
        key = (flagged["player_id"], flagged["game_id"])
        if key not in index_lookup:
            continue
        lst, i = index_lookup[key]
        for k in (1, 2, 3):
            if i + k < len(lst):
                nxt = lst[i + k]
                offsets_resid[k].append(nxt["prob_residual"])
    return {f"t_plus_{k}_mean_residual": (statistics.fmean(v) if v else None) for k, v in offsets_resid.items()} | \
           {f"t_plus_{k}_n": len(v) for k, v in offsets_resid.items()}


def role_recovery_duration(examples_by_season: dict[int, list[dict]], season: int) -> dict:
    by_player = defaultdict(list)
    for e in examples_by_season[season]:
        by_player[e["player_id"]].append(e)
    run_lengths = []
    for lst in by_player.values():
        lst.sort(key=lambda e: e["game_date"])
        current_run = 0
        for e in lst:
            if e["multi_state"] == "COLD_AND_TOI_DECLINE":
                current_run += 1
            else:
                if current_run > 0:
                    run_lengths.append(current_run)
                current_run = 0
        if current_run > 0:
            run_lengths.append(current_run)
    if not run_lengths:
        return {"n_runs": 0}
    run_lengths.sort()
    n = len(run_lengths)
    return {"n_runs": n, "mean_run_length": statistics.fmean(run_lengths),
            "median_run_length": run_lengths[n // 2], "max_run_length": run_lengths[-1]}


def player_concentration(examples: list[dict]) -> dict:
    counts = Counter(e["player_id"] for e in examples)
    total = sum(counts.values())
    top = counts.most_common(10)
    top10_n = sum(c for _, c in top)
    top1_n = top[0][1] if top else 0
    return {"unique_players": len(counts), "total_rows": total,
            "top10_share": (top10_n / total) if total else None,
            "top1_share": (top1_n / total) if total else None}


def calibration(pairs: list[tuple[float, float]]) -> dict:
    mean_pred = statistics.fmean(p for p, _ in pairs)
    mean_actual = statistics.fmean(y for _, y in pairs)
    return {"mean_predicted": mean_pred, "mean_actual": mean_actual, "residual": mean_actual - mean_pred}


if __name__ == "__main__":
    print("Loading frozen marginal engines...")
    ctx = pcs_mp.ContextMarginalContext()
    points_totals = ptf.build_team_game_points_totals(ctx.points.rows)
    points_opponent_env = ptf.build_opponent_points_allowed(points_totals)

    all_results = {}
    joint_check_examples = {}

    for prop in PROPS:
        print(f"\n=== {prop.upper()} ===")
        examples_by_season = build_prop_examples(prop, ctx, [TUNING_SEASON] + EVAL_SEASONS)
        thresholds, toi_decline_cutoff = classify_examples(examples_by_season)
        print(f"  cold_cutoff={thresholds.cold_cutoff:.4f} hot_cutoff={thresholds.hot_cutoff:.4f} "
              f"toi_decline_cutoff={toi_decline_cutoff:.4f}")

        cohorts_by_season = {}
        for season, exs in examples_by_season.items():
            cold_toi = [e for e in exs if e["multi_state"] == "COLD_AND_TOI_DECLINE"]
            cold_without_toi = [e for e in exs if e["state"] == "COLD" and e["multi_state"] != "COLD_AND_TOI_DECLINE"]
            normal = [e for e in exs if e["state"] == "NORMAL"]
            cohorts_by_season[season] = {"cold_toi_decline": cold_toi, "cold_without_toi_decline": cold_without_toi,
                                          "normal": normal, "total_eligible": len(exs)}
            joint_check_examples.setdefault(season, {})[prop] = {(e["player_id"], e["game_id"]): e for e in exs}

        dev_cohort = cohorts_by_season[TUNING_SEASON]["cold_toi_decline"]
        dev_pairs = [(e["prob_1plus"], e["actual_1plus"]) for e in dev_cohort]
        print(f"  DEV (TUNING {TUNING_SEASON}) COLD_AND_TOI_DECLINE n={len(dev_pairs)}")

        if len(dev_pairs) < OVERLAY_MIN_DEV_N:
            all_results[prop] = {"status": "INSUFFICIENT_DATA", "dev_n": len(dev_pairs),
                                  "min_dev_n_required": OVERLAY_MIN_DEV_N}
            continue

        baseline_dev_loss = om.mean_log_loss(dev_pairs)
        baseline_dev_brier = om.mean_brier(dev_pairs)
        fit_b = om.fit_fixed_logit_offset(dev_pairs)
        fit_c = om.fit_shrunk_logit_offset(dev_pairs, fit_b["offset"])
        fit_d = om.fit_bayesian_blend(dev_pairs)
        fit_e = om.fit_isotonic_bins(dev_pairs)

        candidates = {
            "A_NO_ADJUSTMENT": {"dev_log_loss": baseline_dev_loss, "dev_brier": baseline_dev_brier, "params": None},
            "B_FIXED_LOGIT_OFFSET": {"dev_log_loss": fit_b["dev_log_loss"], "params": fit_b},
            "C_SHRUNK_LOGIT_OFFSET": {"dev_log_loss": fit_c["dev_log_loss"], "params": fit_c},
            "D_BAYESIAN_CONTEXT_BLEND": {"dev_log_loss": fit_d["dev_log_loss"], "params": fit_d},
        }
        if fit_e is not None:
            candidates["E_ISOTONIC_BIN_RECAL"] = {"dev_log_loss": fit_e["dev_log_loss"], "params": fit_e}
        else:
            candidates["E_ISOTONIC_BIN_RECAL"] = "INSUFFICIENT_DATA"

        winner_name = min((k for k in candidates if candidates[k] != "INSUFFICIENT_DATA"),
                           key=lambda k: candidates[k]["dev_log_loss"])
        winner_params = candidates[winner_name]["params"]
        apply_fn = _apply_fn_for(winner_name, winner_params)
        print(f"  winner: {winner_name} (dev_log_loss={candidates[winner_name]['dev_log_loss']:.5f} "
              f"vs baseline {baseline_dev_loss:.5f})")

        # Part 21: probability-region diagnostic, DEV-only
        region_table = []
        for lo, hi in PROB_REGION_BINS:
            bucket = [(p, y) for p, y in dev_pairs if lo <= p < hi]
            if not bucket:
                continue
            adj_bucket = [(apply_fn(p), y) for p, y in bucket]
            region_table.append({"lo": lo, "hi": hi, "n": len(bucket),
                                  "raw_brier": om.mean_brier(bucket), "adjusted_brier": om.mean_brier(adj_bucket)})

        eval_block = {}
        for season in EVAL_SEASONS:
            cold_toi = cohorts_by_season[season]["cold_toi_decline"]
            cold_without = cohorts_by_season[season]["cold_without_toi_decline"]
            normal = cohorts_by_season[season]["normal"]
            n_eval = len(cold_toi)

            if n_eval < OVERLAY_MIN_EVAL_N:
                eval_block[season] = {"status": "INSUFFICIENT_DATA", "n": n_eval}
                continue

            raw_pairs = [(e["prob_1plus"], e["actual_1plus"]) for e in cold_toi]
            adjusted_pairs = [(apply_fn(p), y) for p, y in raw_pairs]
            for e, (adj_p, _) in zip(cold_toi, adjusted_pairs):
                e["adjusted_prob_1plus"] = adj_p

            raw_brier_vals = [om.brier(p, y) for p, y in raw_pairs]
            adj_brier_vals = [om.brier(p, y) for p, y in adjusted_pairs]
            raw_ll_vals = [om.log_loss(p, y) for p, y in raw_pairs]
            adj_ll_vals = [om.log_loss(p, y) for p, y in adjusted_pairs]
            brier_diffs = [a - r for a, r in zip(adj_brier_vals, raw_brier_vals)]
            ll_diffs = [a - r for a, r in zip(adj_ll_vals, raw_ll_vals)]

            game_boot_brier = paired_clustered_bootstrap(cold_toi, "game_id", brier_diffs)
            date_boot_brier = paired_clustered_bootstrap(cold_toi, "game_date", brier_diffs)
            player_boot_brier = paired_clustered_bootstrap(cold_toi, "player_id", brier_diffs)
            game_boot_ll = paired_clustered_bootstrap(cold_toi, "game_id", ll_diffs)
            date_boot_ll = paired_clustered_bootstrap(cold_toi, "game_date", ll_diffs)
            player_boot_ll = paired_clustered_bootstrap(cold_toi, "player_id", ll_diffs)

            labels = confidence_labels_for(prop, ctx, points_opponent_env, cold_toi)
            by_conf = defaultdict(list)
            for lbl, (rp, y), (ap, _) in zip(labels, raw_pairs, adjusted_pairs):
                if lbl is not None:
                    by_conf[lbl].append((rp, ap, y))
            confidence_block = {}
            for lbl, rows in by_conf.items():
                if len(rows) < MIN_CONFIDENCE_BUCKET_N:
                    confidence_block[lbl] = {"n": len(rows), "status": "INSUFFICIENT_DATA"}
                    continue
                raw_b = om.mean_brier([(rp, y) for rp, ap, y in rows])
                adj_b = om.mean_brier([(ap, y) for rp, ap, y in rows])
                confidence_block[lbl] = {"n": len(rows), "raw_brier": raw_b, "adjusted_brier": adj_b}

            eval_block[season] = {
                "n": n_eval,
                "raw_brier": statistics.fmean(raw_brier_vals), "adjusted_brier": statistics.fmean(adj_brier_vals),
                "raw_log_loss": statistics.fmean(raw_ll_vals), "adjusted_log_loss": statistics.fmean(adj_ll_vals),
                "raw_calibration": calibration(raw_pairs), "adjusted_calibration": calibration(adjusted_pairs),
                "game_bootstrap_brier": game_boot_brier, "date_bootstrap_brier": date_boot_brier,
                "player_bootstrap_brier": player_boot_brier,
                "game_bootstrap_log_loss": game_boot_ll, "date_bootstrap_log_loss": date_boot_ll,
                "player_bootstrap_log_loss": player_boot_ll,
                "player_concentration": player_concentration(cold_toi),
                "state_frequency_pct": n_eval / cohorts_by_season[season]["total_eligible"],
                "confidence_interaction": confidence_block,
                "control_cohorts_raw_only": {
                    "cold_without_toi_decline": {"n": len(cold_without),
                                                  "mean_prob_residual": (statistics.fmean(
                                                      e["prob_residual"] for e in cold_without) if cold_without
                                                                          else None)},
                    "normal": {"n": len(normal),
                               "mean_prob_residual": (statistics.fmean(e["prob_residual"] for e in normal)
                                                       if normal else None)},
                },
                "persistence_diagnostic": persistence_diagnostic(examples_by_season, cold_toi, season),
                "role_recovery_duration": role_recovery_duration(examples_by_season, season),
            }

        abs_changes = [abs(apply_fn(p) - p) for p, _ in dev_pairs]
        abs_changes.sort()
        n_ac = len(abs_changes)
        adjustment_magnitude = {
            "mean_abs_change": statistics.fmean(abs_changes),
            "median_abs_change": abs_changes[n_ac // 2],
            "p05_change": abs_changes[int(n_ac * 0.05)], "p95_change": abs_changes[min(int(n_ac * 0.95), n_ac - 1)],
        }

        all_results[prop] = {
            "status": "FITTED",
            "cold_cutoff": thresholds.cold_cutoff, "hot_cutoff": thresholds.hot_cutoff,
            "toi_decline_cutoff": toi_decline_cutoff,
            "dev_n": len(dev_pairs), "dev_log_loss_baseline": baseline_dev_loss, "dev_brier_baseline": baseline_dev_brier,
            "candidates": candidates,
            "winner": winner_name, "winner_params": winner_params,
            "adjustment_magnitude": adjustment_magnitude,
            "probability_region_table": region_table,
            "eval": eval_block,
            "dev_player_concentration": player_concentration(dev_cohort),
        }

    # Part 29/30: logical coherence -- P(Goal>=1) <= P(Point>=1), raw and adjusted
    coherence_by_season = {}
    for season in EVAL_SEASONS:
        goal_rows = joint_check_examples.get(season, {}).get("goals", {})
        point_rows = joint_check_examples.get(season, {}).get("points", {})
        shared_ids = set(goal_rows) & set(point_rows)
        raw_violations = 0
        adjusted_violations = 0
        adjusted_pairs_checked = 0
        for gid in shared_ids:
            g, pt = goal_rows[gid], point_rows[gid]
            if g["prob_1plus"] > pt["prob_1plus"] + 1e-9:
                raw_violations += 1
            g_adj = g.get("adjusted_prob_1plus", g["prob_1plus"])
            p_adj = pt.get("adjusted_prob_1plus", pt["prob_1plus"])
            if "adjusted_prob_1plus" in g or "adjusted_prob_1plus" in pt:
                adjusted_pairs_checked += 1
                if g_adj > p_adj + 1e-9:
                    adjusted_violations += 1
        coherence_by_season[season] = {"n_shared_player_games": len(shared_ids), "raw_violations": raw_violations,
                                        "adjusted_violations": adjusted_violations,
                                        "adjusted_pairs_checked": adjusted_pairs_checked}

    # Part 29 fix: non-destructive coherence enforcement. GOAL_1_PLUS implies
    # POINT_1_PLUS (research/joint_scoring_dependence/logical_implication_registry.py),
    # so adjusted P(Point) must never fall below adjusted P(Goal). Mirrors the
    # existing Frechet-style clip pattern from that slice -- never edits the
    # underlying adjusted probability distribution, only the reported combination
    # for the small number of rows where the two independently-fit overlays
    # (different candidate families, different offsets) disagree.
    coherence_fix_by_season = {}
    for season in EVAL_SEASONS:
        goal_rows = joint_check_examples.get(season, {}).get("goals", {})
        point_rows = joint_check_examples.get(season, {}).get("points", {})
        shared_ids = set(goal_rows) & set(point_rows)
        fixed_rows = []
        for key in shared_ids:
            g, pt = goal_rows[key], point_rows[key]
            if "adjusted_prob_1plus" not in g and "adjusted_prob_1plus" not in pt:
                continue
            g_adj = g.get("adjusted_prob_1plus", g["prob_1plus"])
            p_adj = pt.get("adjusted_prob_1plus", pt["prob_1plus"])
            if g_adj > p_adj + 1e-9:
                fixed_p_adj = g_adj
                fixed_rows.append({
                    "player_id": key[0], "game_id": key[1], "goal_adjusted": g_adj,
                    "point_adjusted_before": p_adj, "point_adjusted_after": fixed_p_adj,
                    "point_actual": pt["actual_1plus"],
                    "brier_before": om.brier(p_adj, pt["actual_1plus"]),
                    "brier_after": om.brier(fixed_p_adj, pt["actual_1plus"]),
                })
        fixed_lookup = {(r["player_id"], r["game_id"]): r["point_adjusted_after"] for r in fixed_rows}
        remaining = 0
        for key in shared_ids:
            g, pt = goal_rows[key], point_rows[key]
            if "adjusted_prob_1plus" not in g and "adjusted_prob_1plus" not in pt:
                continue
            g_adj = g.get("adjusted_prob_1plus", g["prob_1plus"])
            p_adj = fixed_lookup.get(key, pt.get("adjusted_prob_1plus", pt["prob_1plus"]))
            if g_adj > p_adj + 1e-9:
                remaining += 1
        coherence_fix_by_season[season] = {
            "violations_found_and_fixed": len(fixed_rows),
            "mean_brier_delta_from_fix": (statistics.fmean(r["brier_after"] - r["brier_before"] for r in fixed_rows)
                                           if fixed_rows else 0.0),
            "post_fix_violations_remaining": remaining,
            "sample_affected_rows": fixed_rows[:10],
        }

    def _sha(path):
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()

    freeze_manifest = {
        "experiment_id": "context_overlay_v1",
        "context_state_source": "research.run_player_context_state_model.build_prop_examples (reused directly, "
                                 "not reimplemented) -- cold/hot/TOI-decline cutoffs are byte-identical to the "
                                 "completed Player Context State Validation slice",
        "props": PROPS,
        "context_state_used": "COLD_AND_TOI_DECLINE only -- pure COLD_STATE and HOT_STATE receive no adjustment",
        "adjustment_family": ["A_NO_ADJUSTMENT", "B_FIXED_LOGIT_OFFSET", "C_SHRUNK_LOGIT_OFFSET",
                               "D_BAYESIAN_CONTEXT_BLEND", "E_ISOTONIC_BIN_RECAL (if sample supports)"],
        "winner_by_prop": {p: all_results[p].get("winner") for p in PROPS if all_results[p].get("status") == "FITTED"},
        "development_season": TUNING_SEASON, "eval_seasons": EVAL_SEASONS,
        "sample_floors": {"dev": OVERLAY_MIN_DEV_N, "eval": OVERLAY_MIN_EVAL_N,
                           "confidence_bucket": MIN_CONFIDENCE_BUCKET_N},
        "no_media_used": True, "no_arena_adjustment": True, "no_sportsbook_calls": True,
        "no_marginal_refit": True, "no_decision_policy_change": True,
        "conservative_probability_architecture": "RAW MARGINAL -> CONTEXT ADJUSTMENT -> (if a conservative "
                                                   "probability is also required for display, apply "
                                                   "count_models.conservative_mu upstream of the context "
                                                   "adjustment, i.e. on the raw mu, for Goals where a mu exists; "
                                                   "Points has no mu under the empirical-baseline champion, so a "
                                                   "probability-domain conservative treatment would need its own "
                                                   "separate design, not built this slice). NOT operationalized.",
        "logical_coherence_by_season": coherence_by_season,
        "coherence_fix_by_season": coherence_fix_by_season,
        "code_hashes": {
            "run_context_overlay_model.py": _sha(str(REPO_ROOT / "research" / "run_context_overlay_model.py")),
            "context_overlay/overlay_models.py": _sha(
                str(REPO_ROOT / "research" / "context_overlay" / "overlay_models.py")),
            "context_overlay/confidence_helpers.py": _sha(
                str(REPO_ROOT / "research" / "context_overlay" / "confidence_helpers.py")),
        },
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    full_results = {
        "config": {"tuning_season": TUNING_SEASON, "eval_seasons": EVAL_SEASONS,
                   "min_dev_n": OVERLAY_MIN_DEV_N, "min_eval_n": OVERLAY_MIN_EVAL_N},
        "props": all_results,
        "logical_coherence_by_season": coherence_by_season,
        "coherence_fix_by_season": coherence_fix_by_season,
        "freeze_manifest": freeze_manifest,
    }

    with open(RESULTS_PATH, "w") as f:
        json.dump(full_results, f, indent=2, default=str)
    print("\nWrote", RESULTS_PATH)
