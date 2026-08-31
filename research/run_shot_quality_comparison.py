"""
Driver for the MoneyPuck offense/defense shot-quality decomposition
experiment. Mirrors research/run_special_teams_comparison.py's
structure and discipline exactly. Writes:

  - research/shot_quality_comparison_results.json
  - MONEYPUCK_SHOT_QUALITY_DECOMPOSITION_REPORT.md

Read-only against research/real_nhl_results/ and
research/moneypuck_ingestion/research_moneypuck.db. Does not touch
nhl.db, models/, or config.py.
"""
from __future__ import annotations

import json
import statistics
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from research import elo_comparison as ec
from research import xg_model_comparison as xgc
from research import shot_quality_model_comparison as sqc
from research import moneypuck_shot_quality_features as sqf
from research.moneypuck_ingestion.ingest_moneypuck_team import get_connection as get_moneypuck_conn
from research.run_xg_comparison import pearson_r

TUNING_SEASON = xgc.TUNING_SEASON
EVAL_SEASONS = xgc.EVAL_SEASONS


def fit_single(baseline_by_id, tuning_ids_mature, feature_values):
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
    return {"mean": mean, "stdev": stdev, "beta": beta, "mature_ids": tuning_ids_mature,
            "tuning_n": len(tuning_records), "tuning_brier": ec.brier_score(tuning_records),
            "tuning_log_loss": ec.log_loss(tuning_records)}


def apply_single(baseline_by_id, game_ids, feature_values, fit):
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


def fit_pair(baseline_by_id, tuning_ids_mature, pair_values):
    raw1 = [pair_values[gid][0] for gid in tuning_ids_mature]
    raw2 = [pair_values[gid][1] for gid in tuning_ids_mature]
    m1, s1 = xgc.standardize_fit(raw1)
    m2, s2 = xgc.standardize_fit(raw2)
    z1 = [xgc.standardize_apply(v, m1, s1) for v in raw1]
    z2 = [xgc.standardize_apply(v, m2, s2) for v in raw2]
    base_logits = [xgc.logit(baseline_by_id[gid]["p_home"]) for gid in tuning_ids_mature]
    actual = [baseline_by_id[gid]["actual_home_win"] for gid in tuning_ids_mature]
    weights = xgc.fit_logistic_weights(base_logits, list(zip(z1, z2)), actual)
    tuning_records = []
    for gid, bl, zz1, zz2 in zip(tuning_ids_mature, base_logits, z1, z2):
        p = xgc.sigmoid(bl + weights[0] * zz1 + weights[1] * zz2)
        tuning_records.append({"p_home": p, "actual_home_win": baseline_by_id[gid]["actual_home_win"],
                                "season": baseline_by_id[gid]["season"]})
    return {"mean_1": m1, "stdev_1": s1, "mean_2": m2, "stdev_2": s2,
            "beta_1": weights[0], "beta_2": weights[1], "mature_ids": tuning_ids_mature,
            "tuning_n": len(tuning_ids_mature), "tuning_brier": ec.brier_score(tuning_records),
            "tuning_log_loss": ec.log_loss(tuning_records)}


def apply_pair(baseline_by_id, game_ids, pair_values, fit):
    records = []
    for gid in game_ids:
        pair = pair_values[gid]
        if pair is None:
            continue
        v1, v2 = pair
        zz1 = xgc.standardize_apply(v1, fit["mean_1"], fit["stdev_1"])
        zz2 = xgc.standardize_apply(v2, fit["mean_2"], fit["stdev_2"])
        bl = xgc.logit(baseline_by_id[gid]["p_home"])
        p = xgc.sigmoid(bl + fit["beta_1"] * zz1 + fit["beta_2"] * zz2)
        base = baseline_by_id[gid]
        records.append({
            "game_id": gid, "season": base["season"], "game_date": base["game_date"],
            "home_team": base["home_team"], "away_team": base["away_team"],
            "home_score": base["home_score"], "away_score": base["away_score"],
            "period_type": base["period_type"], "p_home": p, "actual_home_win": base["actual_home_win"],
            "p_home_baseline": base["p_home"], "term_1": v1, "term_2": v2,
        })
    return records


def run_all():
    games = ec.load_corpus(xgc.NHL_CORPUS_PATH)
    baseline_records, _ = ec.run_walkforward(games, weight_fn=None)
    baseline_by_id = {r["game_id"]: r for r in baseline_records}

    tuning_records = xgc.season_slice(baseline_records, TUNING_SEASON)
    eval_records = xgc.season_slice(baseline_records, EVAL_SEASONS)
    tuning_ids_all = [r["game_id"] for r in tuning_records]
    eval_ids_all = [r["game_id"] for r in eval_records]

    conn = get_moneypuck_conn()
    tuning_features = sqc.compute_all_single_features(conn, tuning_records)
    eval_features = sqc.compute_all_single_features(conn, eval_records)

    results = {"config": {"tuning_season": TUNING_SEASON, "eval_seasons": EVAL_SEASONS,
                           "window_grid": sqc.WINDOW_GRID,
                           "min_total_toi_seconds": sqf.MIN_TOTAL_TOI_SECONDS}}

    results["field_audit"] = {
        "fields_used": ["xg_for", "xg_against", "ice_time_seconds"],
        "situation_used": "5on5",
        "ice_time_units_verified_5v5": "seconds (re-verified this turn, independent of the "
                                        "special-teams slice's verification)",
        "fields_available_but_not_used_this_slice": [
            "shots_for/against", "shot_attempts_for/against", "unblocked_shot_attempts_for/against",
            "high/medium/low_danger_shots_for/against", "high/medium/low_danger_xg_for/against",
            "rebounds_for/against", "score_adjusted_shot_attempts_for/against",
            "score_venue_adjusted_xg_for/against",
        ],
        "rush_attempts_available": False,
        "rush_attempts_note": "NOT present in research_moneypuck_team_game_stats at all -- "
                               "exists only per-shot in MoneyPuck's separate shots file, "
                               "confirmed in MONEYPUCK_DATA_CONTRACT_REVIEW.md.",
    }

    # ---- coverage ----
    coverage = {}
    for name in sqc.SINGLE_FEATURE_SPECS:
        tuning_mature = [gid for gid in tuning_ids_all if tuning_features[name][gid] is not None]
        eval_mature = [gid for gid in eval_ids_all if eval_features[name][gid] is not None]
        coverage[name] = {
            "tuning_n": len(tuning_ids_all), "tuning_mature": len(tuning_mature),
            "tuning_coverage_pct": round(100.0 * len(tuning_mature) / len(tuning_ids_all), 3),
            "eval_n": len(eval_ids_all), "eval_mature": len(eval_mature),
            "eval_coverage_pct": round(100.0 * len(eval_mature) / len(eval_ids_all), 3),
        }
    results["feature_coverage"] = coverage

    # ---- tuning: fit + select window for B (offense), C (defense) ----
    tuning_fits = {}
    for name in sqc.SINGLE_FEATURE_SPECS:
        mature_ids = [gid for gid in tuning_ids_all if tuning_features[name][gid] is not None]
        tuning_fits[name] = fit_single(baseline_by_id, mature_ids, tuning_features[name])
    results["tuning_fits"] = {k: {kk: vv for kk, vv in v.items() if kk != "mature_ids"}
                               for k, v in tuning_fits.items()}

    def best_window(prefix):
        candidates = {w: tuning_fits[f"{prefix}_{w}"] for w in sqc.WINDOW_GRID}
        return min(candidates, key=lambda w: candidates[w]["tuning_brier"])

    best_off_window = best_window("offense_diff")
    best_def_window = best_window("defense_diff")
    b_name = f"offense_diff_{best_off_window}"
    c_name = f"defense_diff_{best_def_window}"
    results["selected"] = {"B": b_name, "C": c_name,
                            "offense_defense_same_window": best_off_window == best_def_window}

    # ---- Candidate D: separate offense + defense, 2-feature joint fit
    # -- reuses each component's OWN best window (may differ). ----
    d_pair_tuning = {gid: (tuning_features[b_name][gid], tuning_features[c_name][gid])
                      for gid in tuning_ids_all}
    d_pair_tuning = {gid: (v if None not in v else None) for gid, v in d_pair_tuning.items()}
    d_mature_tuning = [gid for gid in tuning_ids_all if d_pair_tuning[gid] is not None]
    fit_d = fit_pair(baseline_by_id, d_mature_tuning, d_pair_tuning)
    fit_d["mature_ids"] = d_mature_tuning
    results["fit_d"] = {k: v for k, v in fit_d.items() if k != "mature_ids"}
    results["fit_d"]["offense_window"] = best_off_window
    results["fit_d"]["defense_window"] = best_def_window

    d_pair_eval = {gid: (eval_features[b_name][gid], eval_features[c_name][gid]) for gid in eval_ids_all}
    d_pair_eval = {gid: (v if None not in v else None) for gid, v in d_pair_eval.items()}

    # ---- Candidate E: matchup-aware, window selected via a 1-D scalar
    # tuning search (term_home - term_away), then the FINAL candidate is
    # the 2-feature joint fit at that window -- same procedure used for
    # the special-teams matchup candidate. ----
    matchup_window_fits = {}
    for w in sqc.WINDOW_GRID:
        pairs = sqc.compute_matchup_pair_features(conn, tuning_records, w)
        scalar = {gid: (v[0] - v[1] if v is not None else None) for gid, v in pairs.items()}
        mature_ids = [gid for gid in tuning_ids_all if scalar[gid] is not None]
        matchup_window_fits[w] = fit_single(baseline_by_id, mature_ids, scalar)
    best_matchup_window = min(matchup_window_fits, key=lambda w: matchup_window_fits[w]["tuning_brier"])
    results["matchup_window_tuning"] = {str(w): {kk: vv for kk, vv in v.items() if kk != "mature_ids"}
                                         for w, v in matchup_window_fits.items()}
    results["selected"]["E_window"] = best_matchup_window

    e_pair_tuning = sqc.compute_matchup_pair_features(conn, tuning_records, best_matchup_window)
    e_mature_tuning = [gid for gid in tuning_ids_all if e_pair_tuning[gid] is not None]
    fit_e = fit_pair(baseline_by_id, e_mature_tuning, e_pair_tuning)
    fit_e["mature_ids"] = e_mature_tuning
    results["fit_e"] = {k: v for k, v in fit_e.items() if k != "mature_ids"}
    e_pair_eval = sqc.compute_matchup_pair_features(conn, eval_records, best_matchup_window)

    # ---- correlation with existing signals (Part 23), tuning season ----
    xg_tuning_features = xgc.compute_all_features(conn, tuning_records)
    elo_logit_tuning = {gid: xgc.logit(baseline_by_id[gid]["p_home"]) for gid in tuning_ids_all}

    def corr_series(values_a, values_b):
        common = sorted(set(g for g, v in values_a.items() if v is not None)
                         & set(g for g, v in values_b.items() if v is not None))
        if len(common) < 20:
            return None
        xs = [values_a[g] for g in common]
        ys = [values_b[g] for g in common]
        return {"n": len(common), "r": round(pearson_r(xs, ys), 4)}

    correlations = {
        f"{b_name} vs elo_logit": corr_series(tuning_features[b_name], elo_logit_tuning),
        f"{b_name} vs 5v5_xg_share_25": corr_series(tuning_features[b_name], xg_tuning_features["5v5_xg_share_25"]),
        f"{b_name} vs baseline_p_home": corr_series(
            tuning_features[b_name], {gid: baseline_by_id[gid]["p_home"] for gid in tuning_ids_all}),
        f"{c_name} vs elo_logit": corr_series(tuning_features[c_name], elo_logit_tuning),
        f"{c_name} vs 5v5_xg_share_25": corr_series(tuning_features[c_name], xg_tuning_features["5v5_xg_share_25"]),
        f"{c_name} vs baseline_p_home": corr_series(
            tuning_features[c_name], {gid: baseline_by_id[gid]["p_home"] for gid in tuning_ids_all}),
        f"{b_name} vs {c_name}": corr_series(tuning_features[b_name], tuning_features[c_name]),
    }
    results["correlations_tuning"] = correlations

    # ---- stability (Part 22): offense AND defense, compared to special
    # teams' r=0.105 persistence result ----
    all_teams = sorted({r["home_team"] for r in baseline_records} | {r["away_team"] for r in baseline_records})

    def stability_for(metric_fn, window_for_volatility=10, window_for_persistence=25):
        volatilities = []
        for team in all_teams:
            series = []
            for rec in sorted(tuning_records, key=lambda r: r["game_date"]):
                if rec["home_team"] != team and rec["away_team"] != team:
                    continue
                v = metric_fn(conn, team, rec["game_date"], TUNING_SEASON, window_for_volatility)
                if v is not None:
                    series.append(v)
            if len(series) >= 10:
                mean_v = statistics.fmean(series)
                if mean_v != 0:
                    volatilities.append(abs(statistics.pstdev(series) / mean_v))
        mean_cv = statistics.fmean(volatilities) if volatilities else None

        pairs = []
        for team in all_teams:
            tuning_final = None
            for rec in sorted(tuning_records, key=lambda r: r["game_date"], reverse=True):
                if rec["home_team"] == team or rec["away_team"] == team:
                    v = metric_fn(conn, team, rec["game_date"], TUNING_SEASON, window_for_persistence)
                    if v is not None:
                        tuning_final = v
                        break
            eval_2425 = xgc.season_slice(baseline_records, 20242025)
            eval_final = None
            for rec in sorted(eval_2425, key=lambda r: r["game_date"], reverse=True):
                if rec["home_team"] == team or rec["away_team"] == team:
                    v = metric_fn(conn, team, rec["game_date"], 20242025, window_for_persistence)
                    if v is not None:
                        eval_final = v
                        break
            if tuning_final is not None and eval_final is not None:
                pairs.append((tuning_final, eval_final))

        persistence_r = None
        rank_persistence_r = None
        if len(pairs) >= 10:
            xs = [p[0] for p in pairs]
            ys = [p[1] for p in pairs]
            persistence_r = round(pearson_r(xs, ys), 4)
            rank_x = {v: i for i, v in enumerate(sorted(xs))}
            rank_y = {v: i for i, v in enumerate(sorted(ys))}
            rxs = [rank_x[v] for v in xs]
            rys = [rank_y[v] for v in ys]
            rank_persistence_r = round(pearson_r(rxs, rys), 4)

        return {
            "mean_coefficient_of_variation_window10": round(mean_cv, 4) if mean_cv else None,
            "n_teams_volatility_sample": len(volatilities),
            "season_to_season_persistence_r_window25_2023_24_vs_2024_25": persistence_r,
            "season_to_season_rank_persistence_r": rank_persistence_r,
            "n_teams_persistence_sample": len(pairs),
        }

    results["offense_stability"] = stability_for(sqf.offense_xgf_per60)
    results["defense_stability"] = stability_for(sqf.defense_xga_per60)
    results["special_teams_comparison_note"] = (
        "prior special-teams experiment found PP xGF/60 season-to-season "
        "persistence r=0.105 (window 25, 2023-24 vs 2024-25) -- compare "
        "offense_stability/defense_stability above against that reference point."
    )

    # ---- common evaluation set ----
    b_eval_mature = {gid for gid in eval_ids_all if eval_features[b_name][gid] is not None}
    c_eval_mature = {gid for gid in eval_ids_all if eval_features[c_name][gid] is not None}
    d_eval_mature = {gid for gid in eval_ids_all if d_pair_eval[gid] is not None}
    e_eval_mature = {gid for gid in eval_ids_all if e_pair_eval[gid] is not None}
    common_eval = sorted(b_eval_mature & c_eval_mature & d_eval_mature & e_eval_mature)
    results["common_eval_n"] = len(common_eval)
    results["common_eval_coverage_pct"] = round(100.0 * len(common_eval) / len(eval_ids_all), 3)

    candidate_records = {
        "A_baseline": [
            {"game_id": gid, "season": baseline_by_id[gid]["season"], "game_date": baseline_by_id[gid]["game_date"],
             "p_home": baseline_by_id[gid]["p_home"], "actual_home_win": baseline_by_id[gid]["actual_home_win"]}
            for gid in common_eval
        ],
        f"B_{b_name}": apply_single(baseline_by_id, common_eval, eval_features[b_name], tuning_fits[b_name]),
        f"C_{c_name}": apply_single(baseline_by_id, common_eval, eval_features[c_name], tuning_fits[c_name]),
        "D_offense_plus_defense": apply_pair(baseline_by_id, common_eval, d_pair_eval, fit_d),
        "E_matchup_composite": apply_pair(baseline_by_id, common_eval, e_pair_eval, fit_e),
    }

    metrics = {}
    for label, recs in candidate_records.items():
        metrics[label] = {
            "n": len(recs), "brier": ec.brier_score(recs), "log_loss": ec.log_loss(recs),
            "mean_pred": ec.mean_predicted_prob(recs), "actual_rate": ec.actual_home_win_rate(recs),
            "calibration_error": ec.calibration_error(recs),
            "calibration_table": ec.calibration_table(recs),
            "season_breakdown": {str(s): v for s, v in ec.season_breakdown(recs).items()},
            "prob_distribution": ec.probability_distribution_summary(recs),
        }
    results["metrics"] = metrics

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
        deltas[label] = {
            "brier_abs_delta": metrics[label]["brier"] - metrics["A_baseline"]["brier"],
            "brier_rel_delta_pct": (metrics[label]["brier"] - metrics["A_baseline"]["brier"])
                                    / metrics["A_baseline"]["brier"] * 100.0,
            "log_loss_abs_delta": metrics[label]["log_loss"] - metrics["A_baseline"]["log_loss"],
            "log_loss_rel_delta_pct": (metrics[label]["log_loss"] - metrics["A_baseline"]["log_loss"])
                                       / metrics["A_baseline"]["log_loss"] * 100.0,
            "brier_bootstrap": ec.paired_bootstrap_delta(base_brier_scores, cand_brier_scores),
            "log_loss_bootstrap": ec.paired_bootstrap_delta(base_ll_scores, cand_ll_scores),
        }
    results["deltas_vs_baseline"] = deltas

    consistency = {}
    for label, recs in candidate_records.items():
        if label == "A_baseline":
            continue
        per_season = {}
        for s in EVAL_SEASONS:
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

    # ---- representative examples ----
    non_baseline = [l for l in candidate_records if l != "A_baseline"]
    best_label = min(non_baseline, key=lambda l: metrics[l]["brier"])
    results["best_candidate"] = best_label

    all_off_diffs = sorted(v for v in tuning_features[b_name].values() if v is not None)
    elite_off = all_off_diffs[int(0.85 * len(all_off_diffs))]
    weak_off = all_off_diffs[int(0.15 * len(all_off_diffs))]
    all_def_diffs = sorted(v for v in tuning_features[c_name].values() if v is not None)
    elite_def = all_def_diffs[int(0.85 * len(all_def_diffs))]
    weak_def = all_def_diffs[int(0.15 * len(all_def_diffs))]

    examples = {"elite_offense_vs_weak_defense": None, "weak_offense_vs_elite_defense": None,
                "strong_off_weak_def_team": None, "weak_off_strong_def_team": None,
                "improves": None, "hurts": None}
    for r in sorted(candidate_records[best_label], key=lambda r: r["game_date"]):
        base_p = r.get("p_home_baseline")
        if base_p is None:
            continue
        cand_p = r["p_home"]
        actual = r["actual_home_win"]
        gid = r["game_id"]
        off_val = eval_features[b_name].get(gid)
        def_val = eval_features[c_name].get(gid)
        if examples["elite_offense_vs_weak_defense"] is None and off_val is not None and off_val >= elite_off:
            examples["elite_offense_vs_weak_defense"] = r
        if examples["weak_offense_vs_elite_defense"] is None and off_val is not None and off_val <= weak_off:
            examples["weak_offense_vs_elite_defense"] = r
        if examples["strong_off_weak_def_team"] is None and off_val is not None and def_val is not None \
                and off_val >= elite_off and def_val <= weak_def:
            examples["strong_off_weak_def_team"] = r
        if examples["weak_off_strong_def_team"] is None and off_val is not None and def_val is not None \
                and off_val <= weak_off and def_val >= elite_def:
            examples["weak_off_strong_def_team"] = r
        if examples["improves"] is None and abs(cand_p - actual) < abs(base_p - actual):
            examples["improves"] = r
        if examples["hurts"] is None and abs(cand_p - actual) > abs(base_p - actual):
            examples["hurts"] = r
    results["representative_examples"] = examples

    proc = subprocess.run([sys.executable, "-m", "unittest", "discover", "tests"],
                           cwd=str(REPO_ROOT), capture_output=True, text=True)
    results["test_suite_stderr_tail"] = "\n".join(proc.stderr.strip().splitlines()[-5:])
    results["test_suite_returncode"] = proc.returncode

    return results


if __name__ == "__main__":
    results = run_all()
    out_path = REPO_ROOT / "research" / "shot_quality_comparison_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, sort_keys=True, default=str)
    print(f"wrote {out_path}")
    print(f"test suite returncode: {results['test_suite_returncode']}")
    print(results["test_suite_stderr_tail"])
