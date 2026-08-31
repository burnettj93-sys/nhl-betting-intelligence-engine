"""
Driver for the MoneyPuck special-teams (PP/PK) feature experiment.
Mirrors research/run_xg_comparison.py's structure and discipline exactly
-- same baseline reuse, same standardize-on-tuning-only /
fit-beta-on-tuning-only integration, same common-intersection evaluation
set, same metrics/bootstrap machinery (all reused from
research/elo_comparison.py and research/xg_model_comparison.py, nothing
duplicated). Writes:

  - research/special_teams_comparison_results.json
  - MONEYPUCK_SPECIAL_TEAMS_EXPERIMENT_REPORT.md

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
from research import special_teams_model_comparison as stc
from research import moneypuck_special_teams_features as stf
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


def run_all():
    games = ec.load_corpus(xgc.NHL_CORPUS_PATH)
    baseline_records, _ = ec.run_walkforward(games, weight_fn=None)
    baseline_by_id = {r["game_id"]: r for r in baseline_records}

    tuning_records = xgc.season_slice(baseline_records, TUNING_SEASON)
    eval_records = xgc.season_slice(baseline_records, EVAL_SEASONS)
    tuning_ids_all = [r["game_id"] for r in tuning_records]
    eval_ids_all = [r["game_id"] for r in eval_records]

    conn = get_moneypuck_conn()
    tuning_features = stc.compute_all_single_features(conn, tuning_records)
    eval_features = stc.compute_all_single_features(conn, eval_records)

    results = {"config": {
        "tuning_season": TUNING_SEASON, "eval_seasons": EVAL_SEASONS,
        "window_grid": stc.WINDOW_GRID, "min_total_toi_seconds": stf.MIN_TOTAL_TOI_SECONDS,
    }}

    # ---- Part 1 audit note ----
    results["field_audit"] = {
        "fields_used": ["xg_for", "xg_against", "ice_time_seconds"],
        "situations_used": ["5on4", "4on5"],
        "ice_time_units_verified": "seconds",
        "fields_available_but_not_used_this_slice": [
            "goals_for/against", "shots_for/against", "shot_attempts_for/against",
            "unblocked_shot_attempts_for/against", "high/medium/low_danger_shots_for/against",
            "high/medium/low_danger_xg_for/against", "rebounds_for/against",
            "score_adjusted_shot_attempts_for/against", "score_venue_adjusted_xg_for/against",
        ],
        "pp_opportunity_count_field": "NOT reliably available as a direct MoneyPuck column "
                                       "(confirmed again this turn) -- TOI used instead, per instruction "
                                       "to prefer real TOI-based rates over fabricated opportunity counts.",
    }

    # ---- coverage ----
    coverage = {}
    for name in stc.SINGLE_FEATURE_SPECS:
        tuning_mature = [gid for gid in tuning_ids_all if tuning_features[name][gid] is not None]
        eval_mature = [gid for gid in eval_ids_all if eval_features[name][gid] is not None]
        coverage[name] = {
            "tuning_n": len(tuning_ids_all), "tuning_mature": len(tuning_mature),
            "tuning_coverage_pct": round(100.0 * len(tuning_mature) / len(tuning_ids_all), 3),
            "eval_n": len(eval_ids_all), "eval_mature": len(eval_mature),
            "eval_coverage_pct": round(100.0 * len(eval_mature) / len(eval_ids_all), 3),
        }
    results["feature_coverage"] = coverage

    # ---- tuning: fit + select window for B (pp_diff), C (pk_diff), D (matchup_diff) ----
    tuning_fits = {}
    for name in stc.SINGLE_FEATURE_SPECS:
        mature_ids = [gid for gid in tuning_ids_all if tuning_features[name][gid] is not None]
        tuning_fits[name] = fit_single(baseline_by_id, mature_ids, tuning_features[name])
    results["tuning_fits"] = {k: {kk: vv for kk, vv in v.items() if kk != "mature_ids"}
                               for k, v in tuning_fits.items()}

    def best_window(prefix):
        candidates = {w: tuning_fits[f"{prefix}_{w}"] for w in stc.WINDOW_GRID}
        return min(candidates, key=lambda w: candidates[w]["tuning_brier"])

    best_pp_window = best_window("pp_diff")
    best_pk_window = best_window("pk_diff")
    best_matchup_window = best_window("matchup_diff")

    b_name = f"pp_diff_{best_pp_window}"
    c_name = f"pk_diff_{best_pk_window}"
    d_name = f"matchup_diff_{best_matchup_window}"
    results["selected"] = {"B": b_name, "C": c_name, "D": d_name, "E_window": best_matchup_window}

    # ---- Candidate E: 2-feature composite at D's selected window ----
    tuning_matchup_pairs = stc.compute_matchup_pair_features(conn, tuning_records, best_matchup_window)
    eval_matchup_pairs = stc.compute_matchup_pair_features(conn, eval_records, best_matchup_window)
    e_mature_tuning = [gid for gid in tuning_ids_all if tuning_matchup_pairs[gid] is not None]
    raw1 = [tuning_matchup_pairs[gid][0] for gid in e_mature_tuning]
    raw2 = [tuning_matchup_pairs[gid][1] for gid in e_mature_tuning]
    m1, s1 = xgc.standardize_fit(raw1)
    m2, s2 = xgc.standardize_fit(raw2)
    z1 = [xgc.standardize_apply(v, m1, s1) for v in raw1]
    z2 = [xgc.standardize_apply(v, m2, s2) for v in raw2]
    base_logits_e = [xgc.logit(baseline_by_id[gid]["p_home"]) for gid in e_mature_tuning]
    actual_e = [baseline_by_id[gid]["actual_home_win"] for gid in e_mature_tuning]
    weights_e = xgc.fit_logistic_weights(base_logits_e, list(zip(z1, z2)), actual_e)
    e_tuning_records = []
    for gid, bl, zz1, zz2 in zip(e_mature_tuning, base_logits_e, z1, z2):
        p = xgc.sigmoid(bl + weights_e[0] * zz1 + weights_e[1] * zz2)
        e_tuning_records.append({"p_home": p, "actual_home_win": baseline_by_id[gid]["actual_home_win"],
                                  "season": baseline_by_id[gid]["season"]})
    fit_e = {"mean_1": m1, "stdev_1": s1, "mean_2": m2, "stdev_2": s2,
             "beta_1": weights_e[0], "beta_2": weights_e[1], "window": best_matchup_window,
             "tuning_n": len(e_mature_tuning), "tuning_brier": ec.brier_score(e_tuning_records),
             "tuning_log_loss": ec.log_loss(e_tuning_records)}
    results["fit_e"] = fit_e

    # ---- correlation with existing signals (Part 21), tuning season ----
    xg_conn = conn  # same DB serves both team-level and special-teams tables
    xg_tuning_features = xgc.compute_all_features(xg_conn, tuning_records)
    elo_logit_tuning = {gid: xgc.logit(baseline_by_id[gid]["p_home"]) for gid in tuning_ids_all}

    def corr_series(name_a, values_a, name_b, values_b):
        common = sorted(set(g for g, v in values_a.items() if v is not None)
                         & set(g for g, v in values_b.items() if v is not None))
        if len(common) < 20:
            return None
        xs = [values_a[g] for g in common]
        ys = [values_b[g] for g in common]
        return {"n": len(common), "r": round(pearson_r(xs, ys), 4)}

    correlations = {}
    for st_name in [b_name, c_name, d_name]:
        correlations[f"{st_name} vs elo_logit"] = corr_series(
            st_name, tuning_features[st_name], "elo_logit", elo_logit_tuning)
        correlations[f"{st_name} vs all_xg_diff_25"] = corr_series(
            st_name, tuning_features[st_name], "all_xg_diff_25", xg_tuning_features["all_xg_diff_25"])
        correlations[f"{st_name} vs 5v5_xg_share_25"] = corr_series(
            st_name, tuning_features[st_name], "5v5_xg_share_25", xg_tuning_features["5v5_xg_share_25"])
    # special-teams candidates against EACH OTHER
    correlations[f"{b_name} vs {c_name}"] = corr_series(
        b_name, tuning_features[b_name], c_name, tuning_features[c_name])
    correlations[f"{b_name} vs {d_name}"] = corr_series(
        b_name, tuning_features[b_name], d_name, tuning_features[d_name])
    correlations[f"{c_name} vs {d_name}"] = corr_series(
        c_name, tuning_features[c_name], d_name, tuning_features[d_name])
    results["correlations_tuning"] = correlations

    # ---- PP/PK stability (Part 22) ----
    all_teams = sorted({r["home_team"] for r in baseline_records} | {r["away_team"] for r in baseline_records})
    volatilities = []
    for team in all_teams:
        series = []
        for rec in sorted(tuning_records, key=lambda r: r["game_date"]):
            if rec["home_team"] != team and rec["away_team"] != team:
                continue
            v = stf.pp_xgf_per60(conn, team, rec["game_date"], TUNING_SEASON, window=10)
            if v is not None:
                series.append(v)
        if len(series) >= 10:
            mean_v = statistics.fmean(series)
            if mean_v > 0:
                cv = statistics.pstdev(series) / mean_v
                volatilities.append(cv)
    mean_cv = statistics.fmean(volatilities) if volatilities else None

    persistence_pairs = []
    for team in all_teams:
        tuning_final = None
        for rec in sorted(tuning_records, key=lambda r: r["game_date"], reverse=True):
            if rec["home_team"] == team or rec["away_team"] == team:
                v = stf.pp_xgf_per60(conn, team, rec["game_date"], TUNING_SEASON, window=25)
                if v is not None:
                    tuning_final = v
                    break
        eval_2425 = xgc.season_slice(baseline_records, 20242025)
        eval_final = None
        for rec in sorted(eval_2425, key=lambda r: r["game_date"], reverse=True):
            if rec["home_team"] == team or rec["away_team"] == team:
                v = stf.pp_xgf_per60(conn, team, rec["game_date"], 20242025, window=25)
                if v is not None:
                    eval_final = v
                    break
        if tuning_final is not None and eval_final is not None:
            persistence_pairs.append((tuning_final, eval_final))

    persistence_r = None
    if len(persistence_pairs) >= 10:
        xs = [p[0] for p in persistence_pairs]
        ys = [p[1] for p in persistence_pairs]
        persistence_r = round(pearson_r(xs, ys), 4)

    results["pp_pk_stability"] = {
        "rolling_window10_pp_xgf60_mean_coefficient_of_variation": round(mean_cv, 4) if mean_cv else None,
        "n_teams_in_volatility_sample": len(volatilities),
        "season_to_season_persistence_r_pp_xgf60_w25_2023_24_vs_2024_25": persistence_r,
        "n_teams_in_persistence_sample": len(persistence_pairs),
    }

    # ---- common evaluation set ----
    b_eval_mature = {gid for gid in eval_ids_all if eval_features[b_name][gid] is not None}
    c_eval_mature = {gid for gid in eval_ids_all if eval_features[c_name][gid] is not None}
    d_eval_mature = {gid for gid in eval_ids_all if eval_features[d_name][gid] is not None}
    e_eval_mature = {gid for gid in eval_ids_all if eval_matchup_pairs[gid] is not None}
    common_eval = sorted(b_eval_mature & c_eval_mature & d_eval_mature & e_eval_mature)
    results["common_eval_n"] = len(common_eval)
    results["common_eval_coverage_pct"] = round(100.0 * len(common_eval) / len(eval_ids_all), 3)

    candidate_records = {}
    candidate_records["A_baseline"] = [
        {"game_id": gid, "season": baseline_by_id[gid]["season"], "game_date": baseline_by_id[gid]["game_date"],
         "p_home": baseline_by_id[gid]["p_home"], "actual_home_win": baseline_by_id[gid]["actual_home_win"]}
        for gid in common_eval
    ]
    candidate_records[f"B_{b_name}"] = apply_single(baseline_by_id, common_eval, eval_features[b_name], tuning_fits[b_name])
    candidate_records[f"C_{c_name}"] = apply_single(baseline_by_id, common_eval, eval_features[c_name], tuning_fits[c_name])
    candidate_records[f"D_{d_name}"] = apply_single(baseline_by_id, common_eval, eval_features[d_name], tuning_fits[d_name])

    e_records = []
    for gid in common_eval:
        term_home, term_away = eval_matchup_pairs[gid]
        zz1 = xgc.standardize_apply(term_home, fit_e["mean_1"], fit_e["stdev_1"])
        zz2 = xgc.standardize_apply(term_away, fit_e["mean_2"], fit_e["stdev_2"])
        bl = xgc.logit(baseline_by_id[gid]["p_home"])
        p = xgc.sigmoid(bl + fit_e["beta_1"] * zz1 + fit_e["beta_2"] * zz2)
        base = baseline_by_id[gid]
        e_records.append({
            "game_id": gid, "season": base["season"], "game_date": base["game_date"],
            "home_team": base["home_team"], "away_team": base["away_team"],
            "home_score": base["home_score"], "away_score": base["away_score"],
            "period_type": base["period_type"], "p_home": p, "actual_home_win": base["actual_home_win"],
            "p_home_baseline": base["p_home"], "term_home": term_home, "term_away": term_away,
        })
    candidate_records["E_matchup_composite"] = e_records

    # ---- metrics ----
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

    # ---- representative examples (Part 23) ----
    non_baseline = [l for l in candidate_records if l != "A_baseline"]
    best_label = min(non_baseline, key=lambda l: metrics[l]["brier"])
    results["best_candidate"] = best_label

    all_pp_diffs = sorted(v for v in tuning_features[b_name].values() if v is not None)
    p75_idx, p25_idx = int(0.75 * len(all_pp_diffs)), int(0.25 * len(all_pp_diffs))
    elite_threshold = all_pp_diffs[p75_idx]
    weak_threshold = all_pp_diffs[p25_idx]

    examples = {"elite_pp_vs_weak_pk": None, "weak_pp_vs_elite_pk": None,
                "agrees_with_baseline": None, "disagrees_with_baseline": None,
                "improves": None, "hurts": None}
    best_recs_sorted = sorted(candidate_records[best_label], key=lambda r: r["game_date"])
    for r in best_recs_sorted:
        base_p = r.get("p_home_baseline")
        if base_p is None:
            continue
        cand_p = r["p_home"]
        actual = r["actual_home_win"]
        fdiff = r.get("feature_diff")
        if examples["elite_pp_vs_weak_pk"] is None and fdiff is not None and fdiff >= elite_threshold:
            examples["elite_pp_vs_weak_pk"] = r
        if examples["weak_pp_vs_elite_pk"] is None and fdiff is not None and fdiff <= weak_threshold:
            examples["weak_pp_vs_elite_pk"] = r
        agrees = (cand_p > base_p) == (base_p > 0.5)
        if examples["agrees_with_baseline"] is None and agrees:
            examples["agrees_with_baseline"] = r
        if examples["disagrees_with_baseline"] is None and not agrees:
            examples["disagrees_with_baseline"] = r
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
    out_path = REPO_ROOT / "research" / "special_teams_comparison_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, sort_keys=True, default=str)
    print(f"wrote {out_path}")
    print(f"test suite returncode: {results['test_suite_returncode']}")
    print(results["test_suite_stderr_tail"])
