"""
Driver for the MoneyPuck team xG/shot-quality feature experiment. Ties
together research/elo_comparison.py (baseline trajectory + generic
metrics), research/xg_model_comparison.py (feature standardization +
logistic integration), and research/moneypuck_team_features.py (the
actual PIT-safe rolling features) into one controlled comparison, and
writes:

  - research/xg_comparison_results.json   (every computed number)
  - XG_TEAM_FEATURE_EXPERIMENT_REPORT.md  (the human-readable report)

Read-only against research/real_nhl_results/ and
research/moneypuck_ingestion/research_moneypuck.db. Writes nothing to
either. Does not touch nhl.db, models/, or config.py.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from research import elo_comparison as ec
from research import xg_model_comparison as xgc
from research import moneypuck_team_features as mpf
from research.moneypuck_ingestion.ingest_moneypuck_team import get_connection as get_moneypuck_conn

CORRELATION_THRESHOLD = 0.75  # |r| at/above this => treated as redundant, not combined into E


def pearson_r(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x == 0 or var_y == 0:
        return 0.0
    return cov / (var_x ** 0.5 * var_y ** 0.5)


def fit_single_feature_candidate(baseline_by_id: dict, tuning_ids_mature: list[int],
                                  feature_values: dict[int, float | None]) -> dict:
    """Standardize + fit a 1-D logistic coefficient on the tuning-mature
    subset only. Returns the fit parameters plus the resulting tuning-season
    Brier/log-loss on that same mature subset (used purely for
    candidate/window SELECTION, per the Elo experiment's precedent)."""
    raw_values = [feature_values[gid] for gid in tuning_ids_mature]
    mean, stdev = xgc.standardize_fit(raw_values)
    z_values = [xgc.standardize_apply(v, mean, stdev) for v in raw_values]
    base_logits = [xgc.logit(baseline_by_id[gid]["p_home"]) for gid in tuning_ids_mature]
    actual = [baseline_by_id[gid]["actual_home_win"] for gid in tuning_ids_mature]

    weights = xgc.fit_logistic_weights(base_logits, [[z] for z in z_values], actual)
    beta = weights[0]

    tuning_records = []
    for gid, bl, z in zip(tuning_ids_mature, base_logits, z_values):
        p = xgc.sigmoid(bl + beta * z)
        tuning_records.append({"p_home": p, "actual_home_win": baseline_by_id[gid]["actual_home_win"],
                                "season": baseline_by_id[gid]["season"]})

    return {
        "mean": mean, "stdev": stdev, "beta": beta,
        "tuning_n": len(tuning_records),
        "tuning_brier": ec.brier_score(tuning_records),
        "tuning_log_loss": ec.log_loss(tuning_records),
    }


def apply_single_feature_candidate(baseline_by_id: dict, game_ids: list[int],
                                    feature_values: dict[int, float | None],
                                    fit: dict) -> list[dict]:
    records = []
    for gid in game_ids:
        v = feature_values[gid]
        if v is None:
            continue
        z = xgc.standardize_apply(v, fit["mean"], fit["stdev"])
        bl = xgc.logit(baseline_by_id[gid]["p_home"])
        p = xgc.sigmoid(bl + fit["beta"] * z)
        base = baseline_by_id[gid]
        records.append({
            "game_id": gid, "season": base["season"], "game_date": base["game_date"],
            "home_team": base["home_team"], "away_team": base["away_team"],
            "home_score": base["home_score"], "away_score": base["away_score"],
            "period_type": base["period_type"],
            "p_home": p, "actual_home_win": base["actual_home_win"],
            "p_home_baseline": base["p_home"], "feature_diff": v, "z": z,
        })
    return records


def run_all():
    games = ec.load_corpus(xgc.NHL_CORPUS_PATH)
    baseline_records, _ = ec.run_walkforward(games, weight_fn=None)
    baseline_by_id = {r["game_id"]: r for r in baseline_records}

    tuning_records = xgc.season_slice(baseline_records, xgc.TUNING_SEASON)
    eval_records = xgc.season_slice(baseline_records, xgc.EVAL_SEASONS)
    tuning_ids_all = [r["game_id"] for r in tuning_records]
    eval_ids_all = [r["game_id"] for r in eval_records]

    conn = get_moneypuck_conn()
    tuning_features = xgc.compute_all_features(conn, tuning_records)
    eval_features = xgc.compute_all_features(conn, eval_records)

    results = {"config": {
        "warmup_season": xgc.WARMUP_SEASON, "tuning_season": xgc.TUNING_SEASON,
        "eval_seasons": xgc.EVAL_SEASONS, "window_grid": xgc.WINDOW_GRID,
        "form_short_window": xgc.FORM_SHORT_WINDOW, "form_long_window": xgc.FORM_LONG_WINDOW,
    }}

    # ---- coverage report (Part 17), before any selection ----
    coverage = {}
    for name in xgc.FEATURE_SPECS:
        tuning_mature = [gid for gid in tuning_ids_all if tuning_features[name][gid] is not None]
        eval_mature = [gid for gid in eval_ids_all if eval_features[name][gid] is not None]
        coverage[name] = {
            "tuning_n": len(tuning_ids_all), "tuning_mature": len(tuning_mature),
            "tuning_coverage_pct": round(100.0 * len(tuning_mature) / len(tuning_ids_all), 3),
            "eval_n": len(eval_ids_all), "eval_mature": len(eval_mature),
            "eval_coverage_pct": round(100.0 * len(eval_mature) / len(eval_ids_all), 3),
        }
    results["feature_coverage"] = coverage

    # ---- tuning: fit + select window for B (5v5 xg share) and C (all-situation xg diff) ----
    tuning_fits = {}
    for name in xgc.FEATURE_SPECS:
        mature_ids = [gid for gid in tuning_ids_all if tuning_features[name][gid] is not None]
        tuning_fits[name] = fit_single_feature_candidate(baseline_by_id, mature_ids, tuning_features[name])
        tuning_fits[name]["mature_ids"] = mature_ids
    results["tuning_fits"] = {k: {kk: vv for kk, vv in v.items() if kk != "mature_ids"}
                               for k, v in tuning_fits.items()}

    b_candidates = {w: tuning_fits[f"5v5_xg_share_{w}"] for w in xgc.WINDOW_GRID}
    c_candidates = {w: tuning_fits[f"all_xg_diff_{w}"] for w in xgc.WINDOW_GRID}
    best_b_window = min(b_candidates, key=lambda w: b_candidates[w]["tuning_brier"])
    best_c_window = min(c_candidates, key=lambda w: c_candidates[w]["tuning_brier"])
    d_name = f"xg_form_delta_{xgc.FORM_SHORT_WINDOW}_{xgc.FORM_LONG_WINDOW}"

    b_name = f"5v5_xg_share_{best_b_window}"
    c_name = f"all_xg_diff_{best_c_window}"
    results["selected"] = {"B": b_name, "C": c_name, "D": d_name}

    # ---- correlation analysis (Part 23), on tuning data, intersection of mature games ----
    common_tuning_mature = (set(tuning_fits[b_name]["mature_ids"])
                             & set(tuning_fits[c_name]["mature_ids"])
                             & set(tuning_fits[d_name]["mature_ids"]))
    common_tuning_mature = sorted(common_tuning_mature)
    correlations = {}
    names_for_corr = [b_name, c_name, d_name]
    for i in range(len(names_for_corr)):
        for j in range(i + 1, len(names_for_corr)):
            n1, n2 = names_for_corr[i], names_for_corr[j]
            xs = [tuning_features[n1][gid] for gid in common_tuning_mature]
            ys = [tuning_features[n2][gid] for gid in common_tuning_mature]
            correlations[f"{n1} vs {n2}"] = round(pearson_r(xs, ys), 4)
    results["feature_correlations_tuning"] = correlations
    results["correlation_n"] = len(common_tuning_mature)

    # ---- decide E: least-correlated pair among B*/C*/D, if below threshold ----
    least_correlated_pair = min(correlations, key=lambda k: abs(correlations[k]))
    e_pair_names = least_correlated_pair.split(" vs ")
    e_built = abs(correlations[least_correlated_pair]) < CORRELATION_THRESHOLD
    results["candidate_e_built"] = e_built
    results["candidate_e_pair"] = e_pair_names if e_built else None

    fit_e = None
    if e_built:
        n1, n2 = e_pair_names
        mature_e = sorted(set(tuning_fits[n1]["mature_ids"]) & set(tuning_fits[n2]["mature_ids"]))
        raw1 = [tuning_features[n1][gid] for gid in mature_e]
        raw2 = [tuning_features[n2][gid] for gid in mature_e]
        m1, s1 = xgc.standardize_fit(raw1)
        m2, s2 = xgc.standardize_fit(raw2)
        z1 = [xgc.standardize_apply(v, m1, s1) for v in raw1]
        z2 = [xgc.standardize_apply(v, m2, s2) for v in raw2]
        base_logits = [xgc.logit(baseline_by_id[gid]["p_home"]) for gid in mature_e]
        actual = [baseline_by_id[gid]["actual_home_win"] for gid in mature_e]
        weights = xgc.fit_logistic_weights(base_logits, list(zip(z1, z2)), actual)
        e_tuning_records = []
        for gid, bl, zz1, zz2 in zip(mature_e, base_logits, z1, z2):
            p = xgc.sigmoid(bl + weights[0] * zz1 + weights[1] * zz2)
            e_tuning_records.append({"p_home": p, "actual_home_win": baseline_by_id[gid]["actual_home_win"],
                                      "season": baseline_by_id[gid]["season"]})
        fit_e = {
            "feature_1": n1, "feature_2": n2, "mean_1": m1, "stdev_1": s1, "mean_2": m2, "stdev_2": s2,
            "beta_1": weights[0], "beta_2": weights[1], "mature_ids": mature_e,
            "tuning_n": len(mature_e), "tuning_brier": ec.brier_score(e_tuning_records),
            "tuning_log_loss": ec.log_loss(e_tuning_records),
        }
        results["fit_e"] = {k: v for k, v in fit_e.items() if k != "mature_ids"}

    # ---- true evaluation: common intersection of B*, C*, D (and E's pair) mature games ----
    b_eval_mature = {gid for gid in eval_ids_all if eval_features[b_name][gid] is not None}
    c_eval_mature = {gid for gid in eval_ids_all if eval_features[c_name][gid] is not None}
    d_eval_mature = {gid for gid in eval_ids_all if eval_features[d_name][gid] is not None}
    common_eval = b_eval_mature & c_eval_mature & d_eval_mature
    if e_built:
        n1, n2 = e_pair_names
        e_eval_mature = ({gid for gid in eval_ids_all if eval_features[n1][gid] is not None}
                          & {gid for gid in eval_ids_all if eval_features[n2][gid] is not None})
        common_eval = common_eval & e_eval_mature
    common_eval = sorted(common_eval)
    results["common_eval_n"] = len(common_eval)
    results["common_eval_coverage_pct"] = round(100.0 * len(common_eval) / len(eval_ids_all), 3)

    # ---- apply candidates to the common eval set ----
    candidate_records = {}
    candidate_records["A_baseline"] = [
        {"game_id": gid, "season": baseline_by_id[gid]["season"], "game_date": baseline_by_id[gid]["game_date"],
         "p_home": baseline_by_id[gid]["p_home"], "actual_home_win": baseline_by_id[gid]["actual_home_win"]}
        for gid in common_eval
    ]
    candidate_records[f"B_{b_name}"] = apply_single_feature_candidate(
        baseline_by_id, common_eval, eval_features[b_name], tuning_fits[b_name])
    candidate_records[f"C_{c_name}"] = apply_single_feature_candidate(
        baseline_by_id, common_eval, eval_features[c_name], tuning_fits[c_name])
    candidate_records[f"D_{d_name}"] = apply_single_feature_candidate(
        baseline_by_id, common_eval, eval_features[d_name], tuning_fits[d_name])

    if e_built:
        n1, n2 = e_pair_names
        e_records = []
        for gid in common_eval:
            v1 = eval_features[n1][gid]
            v2 = eval_features[n2][gid]
            zz1 = xgc.standardize_apply(v1, fit_e["mean_1"], fit_e["stdev_1"])
            zz2 = xgc.standardize_apply(v2, fit_e["mean_2"], fit_e["stdev_2"])
            bl = xgc.logit(baseline_by_id[gid]["p_home"])
            p = xgc.sigmoid(bl + fit_e["beta_1"] * zz1 + fit_e["beta_2"] * zz2)
            base = baseline_by_id[gid]
            e_records.append({
                "game_id": gid, "season": base["season"], "game_date": base["game_date"],
                "home_team": base["home_team"], "away_team": base["away_team"],
                "home_score": base["home_score"], "away_score": base["away_score"],
                "period_type": base["period_type"],
                "p_home": p, "actual_home_win": base["actual_home_win"], "p_home_baseline": base["p_home"],
            })
        candidate_records[f"E_{n1}_plus_{n2}"] = e_records

    # ---- metrics per candidate ----
    metrics = {}
    for label, recs in candidate_records.items():
        metrics[label] = {
            "n": len(recs),
            "brier": ec.brier_score(recs), "log_loss": ec.log_loss(recs),
            "mean_pred": ec.mean_predicted_prob(recs), "actual_rate": ec.actual_home_win_rate(recs),
            "calibration_error": ec.calibration_error(recs),
            "calibration_table": ec.calibration_table(recs),
            "season_breakdown": {str(s): v for s, v in ec.season_breakdown(recs).items()},
            "prob_distribution": ec.probability_distribution_summary(recs),
        }
    results["metrics"] = metrics

    # ---- deltas + bootstrap vs baseline ----
    baseline_recs = candidate_records["A_baseline"]
    base_brier_scores = ec.per_game_brier(baseline_recs)
    base_ll_scores = ec.per_game_log_loss(baseline_recs)

    deltas = {}
    for label, recs in candidate_records.items():
        if label == "A_baseline":
            continue
        assert [r["game_id"] for r in recs] == common_eval
        cand_brier_scores = ec.per_game_brier(recs)
        cand_ll_scores = ec.per_game_log_loss(recs)
        brier_boot = ec.paired_bootstrap_delta(base_brier_scores, cand_brier_scores)
        ll_boot = ec.paired_bootstrap_delta(base_ll_scores, cand_ll_scores)
        deltas[label] = {
            "brier_abs_delta": metrics[label]["brier"] - metrics["A_baseline"]["brier"],
            "brier_rel_delta_pct": (metrics[label]["brier"] - metrics["A_baseline"]["brier"])
                                    / metrics["A_baseline"]["brier"] * 100.0,
            "log_loss_abs_delta": metrics[label]["log_loss"] - metrics["A_baseline"]["log_loss"],
            "log_loss_rel_delta_pct": (metrics[label]["log_loss"] - metrics["A_baseline"]["log_loss"])
                                       / metrics["A_baseline"]["log_loss"] * 100.0,
            "brier_bootstrap": brier_boot, "log_loss_bootstrap": ll_boot,
        }
    results["deltas_vs_baseline"] = deltas

    # ---- season-by-season consistency on the common eval set ----
    consistency = {}
    for label, recs in candidate_records.items():
        if label == "A_baseline":
            continue
        per_season = {}
        for s in xgc.EVAL_SEASONS:
            base_s = xgc.season_slice(baseline_recs, [s])
            cand_s = xgc.season_slice(recs, [s])
            per_season[str(s)] = {
                "baseline_brier": ec.brier_score(base_s), "candidate_brier": ec.brier_score(cand_s),
                "brier_delta": ec.brier_score(cand_s) - ec.brier_score(base_s),
                "baseline_log_loss": ec.log_loss(base_s), "candidate_log_loss": ec.log_loss(cand_s),
                "log_loss_delta": ec.log_loss(cand_s) - ec.log_loss(base_s),
            }
        consistency[label] = per_season
    results["season_consistency"] = consistency

    # ---- representative examples for the BEST candidate (lowest eval Brier among B/C/D/E) ----
    non_baseline_labels = [l for l in candidate_records if l != "A_baseline"]
    best_label = min(non_baseline_labels, key=lambda l: metrics[l]["brier"])
    results["best_candidate"] = best_label

    best_recs_by_id = {r["game_id"]: r for r in candidate_records[best_label]}
    seen = {"agrees": None, "disagrees": None, "improves": None, "hurts": None}
    for r in sorted(candidate_records[best_label], key=lambda r: r["game_date"]):
        base_p = r.get("p_home_baseline")
        if base_p is None:
            continue
        cand_p = r["p_home"]
        actual = r["actual_home_win"]
        elo_favors_home = base_p > 0.5
        # infer xG direction from whether candidate moved p toward or away from 0.5 in the home direction
        xg_pushes_home = cand_p > base_p
        agrees = (xg_pushes_home == elo_favors_home)
        if seen["agrees"] is None and agrees:
            seen["agrees"] = r
        if seen["disagrees"] is None and not agrees:
            seen["disagrees"] = r
        base_err = abs(base_p - actual)
        cand_err = abs(cand_p - actual)
        if seen["improves"] is None and cand_err < base_err:
            seen["improves"] = r
        if seen["hurts"] is None and cand_err > base_err:
            seen["hurts"] = r
        if all(v is not None for v in seen.values()):
            break
    results["representative_examples"] = {k: v for k, v in seen.items()}

    # ---- full test suite re-run ----
    proc = subprocess.run([sys.executable, "-m", "unittest", "discover", "tests"],
                           cwd=str(REPO_ROOT), capture_output=True, text=True)
    results["test_suite_stderr_tail"] = "\n".join(proc.stderr.strip().splitlines()[-5:])
    results["test_suite_returncode"] = proc.returncode

    return results


if __name__ == "__main__":
    results = run_all()
    out_path = REPO_ROOT / "research" / "xg_comparison_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, sort_keys=True, default=str)
    print(f"wrote {out_path}")
    print(f"test suite returncode: {results['test_suite_returncode']}")
    print(results["test_suite_stderr_tail"])
