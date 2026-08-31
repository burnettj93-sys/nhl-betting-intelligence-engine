"""
Driver for the Goalie Quality x Starter-Probability Integration Experiment.
Combines the already-fitted Stage 1 projected-starter model
(research/goalie_intelligence/model.py + fitted weights in
research/goalie_intelligence_results.json -- NOT refit here) with two
goalie-quality candidate metrics (research/goalie_intelligence/quality.py)
through the scenario-weighted probability mixture in
research/goalie_quality_integration.py, and compares against the frozen
production-equivalent Elo-only baseline (research.elo_comparison.
run_walkforward(games, weight_fn=None) -- proven mathematically identical
to the full production combined model on this real corpus by
tests/test_elo_comparison.py::TestProductionEloEquivalence, same
precedent used by every prior research experiment in this project).

Writes:
  - research/goalie_quality_integration_results.json
  - GOALIE_QUALITY_INTEGRATION_REPORT.md (written by a separate step)

Read-only against nhl.db, models/, config.py, pricing/. Does not change
any production win probability -- see the report's "Final questions"
section.
"""
from __future__ import annotations

import bisect
import json
import math
import random
import statistics
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from research import elo_comparison as ec
from research import xg_model_comparison as xgc
from research import goalie_quality_integration as gqi
from research.goalie_intelligence import features as gf
from research.goalie_intelligence import model as gm
from research.goalie_intelligence import quality as gq
from research.run_goalie_intelligence import (
    build_example, MIN_HISTORY_GAMES, CANDIDATE_WINDOW,
)

WARMUP_SEASON = 20222023
TUNING_SEASON = 20232024
EVAL_SEASONS = [20242025, 20252026]

# Elo points -> natural-logit units, using production's OWN probability
# formula (research/elo_comparison.py::ResearchEloState.win_probability,
# which mirrors config.py's Elo-to-probability convention:
# p = 1/(1+10^(-diff/400))). This is a unit CONVERSION, not a new fitted
# coefficient -- Candidate A's Elo delta is applied through the exact
# same math production already uses elsewhere, per Part 18's freeze.
ELO_POINTS_TO_LOGIT = math.log(10) / 400.0

GSAX_WINDOW_GRID = [5, 15, None]  # None == cumulative all-history (Part 15)

STARTER_RESULTS_PATH = REPO_ROOT / "research" / "goalie_intelligence_results.json"
NHL_CORPUS_PATH = REPO_ROOT / "research" / "real_nhl_results" / "normalized_regular_season_games.jsonl"


def load_fitted_starter_weights() -> list[float]:
    """Reuses the ALREADY-FITTED Stage 1 starter-model weights -- this
    experiment does not refit the starter model (Part 18 freezes the
    game model; the starter model was independently validated in the
    prior slice and is reused here as a black-box input, same as the
    Elo baseline is reused as a black-box input)."""
    with open(STARTER_RESULTS_PATH) as f:
        results = json.load(f)
    weights_by_name = results["fitted_weights"]
    return [weights_by_name[name] for name in gm.FEATURE_NAMES]


def build_starter_pair_examples(all_rows: list[dict], baseline_records: list[dict]) -> tuple[list[dict], dict]:
    """Joins the team-scoped starter corpus into GAME-level pairs (home
    example + away example for the same real game_id), keyed off the
    baseline Elo walk-forward's own game list -- this IS the "common
    evaluation set" gate (Part 20): a game only enters this experiment
    if BOTH teams have a valid projected-starter distribution."""
    rows_by_game_team = {(r["game_id"], r["team"]): r for r in all_rows}
    excluded = {"no_starter_row_for_a_side": 0, "insufficient_starter_history_or_actual_not_candidate": 0}
    pairs = []
    for b in baseline_records:
        if b["season"] not in (TUNING_SEASON, *EVAL_SEASONS):
            continue
        home_row = rows_by_game_team.get((b["game_id"], b["home_team"]))
        away_row = rows_by_game_team.get((b["game_id"], b["away_team"]))
        if home_row is None or away_row is None:
            excluded["no_starter_row_for_a_side"] += 1
            continue
        home_ex = build_example(all_rows, home_row)
        away_ex = build_example(all_rows, away_row)
        if home_ex is None or away_ex is None:
            excluded["insufficient_starter_history_or_actual_not_candidate"] += 1
            continue
        pairs.append({
            "game_id": b["game_id"], "season": b["season"], "game_date": b["game_date"],
            "home_team": b["home_team"], "away_team": b["away_team"],
            "p_baseline": b["p_home"], "actual_home_win": b["actual_home_win"],
            "home": home_ex, "away": away_ex,
            "is_back_to_back": home_ex["is_back_to_back"] or away_ex["is_back_to_back"],
        })
    return pairs, excluded


class QualityIndex:
    """Performance-only re-expression of
    research.goalie_intelligence.quality.goalie_history_as_of() as a
    per-goalie, date-sorted list with bisection instead of a full O(corpus)
    scan per lookup -- this experiment calls it tens of thousands of times
    (every candidate goalie, every window, every game). Produces IDENTICAL
    results to the canonical gate function (STRICT prior-game-date, same
    goalie-identity scoping); tests/test_goalie_quality_integration.py
    cross-checks the two directly rather than trusting this by
    construction alone."""

    def __init__(self, rows: list[dict]):
        by_goalie = defaultdict(list)
        for r in rows:
            by_goalie[r["goalie_id"]].append(r)
        self._by_goalie: dict[str, tuple[list[str], list[dict]]] = {}
        for goalie_id, appearance_rows in by_goalie.items():
            ordered = sorted(appearance_rows, key=lambda r: (r["game_date"], r["game_id"]))
            self._by_goalie[goalie_id] = ([r["game_date"] for r in ordered], ordered)

    def history_as_of(self, goalie_id: str, prediction_game_date: str) -> list[dict]:
        entry = self._by_goalie.get(goalie_id)
        if entry is None:
            return []
        dates, ordered = entry
        cut = bisect.bisect_left(dates, prediction_game_date)
        return ordered[:cut]


def quality_a_adj_logit(index: QualityIndex, goalie_id: str, game_date: str) -> tuple[float, float]:
    """Candidate A (existing production save%-shrinkage formula, unchanged
    -- Part 6-B), converted from Elo points into the SAME natural-logit
    units the scenario-mixture formula operates in. Returns
    (adj_logit, cumulative_shots_against_sample)."""
    hist = index.history_as_of(goalie_id, game_date)
    delta_elo, shots = gq.shrunk_save_pct_production(hist)
    return delta_elo * ELO_POINTS_TO_LOGIT, shots


def quality_b_raw(index: QualityIndex, goalie_id: str, game_date: str, window: int | None) -> tuple[float, float]:
    """Candidate B (MoneyPuck shot-quality-style metric -- Part 6-C), RAW
    (not yet scaled) rolling GSAx-style per-60 value. The scale
    (raw-value -> logit-additive units) is a NEW candidate and is fit on
    TUNING_SEASON only, below."""
    hist = index.history_as_of(goalie_id, game_date)
    val, shots = gq.rolling_gsax_per60(hist, window)
    return (val if val is not None else 0.0), shots


def top1_index(probs: list[float]) -> int:
    return max(range(len(probs)), key=lambda i: probs[i])


def confidence_label(p_top1_home: float, p_top1_away: float) -> str:
    """Weakest-link confidence for the GAME: the lower of the two teams'
    top-1 starter probabilities, since the scenario mixture's uncertainty
    is driven by whichever side is least predictable (Part 10)."""
    weakest = min(p_top1_home, p_top1_away)
    if weakest >= 0.70:
        return "HIGH"
    if weakest >= 0.50:
        return "MEDIUM"
    return "LOW"


def brier(p: float, y: float) -> float:
    return (p - y) ** 2


def logloss(p: float, y: float, eps: float = 1e-12) -> float:
    p = min(max(p, eps), 1 - eps)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def evaluate_series(probs: list[float], actuals: list[float]) -> dict:
    n = len(probs)
    briers = [brier(p, y) for p, y in zip(probs, actuals)]
    loglosses = [logloss(p, y) for p, y in zip(probs, actuals)]
    return {
        "n": n,
        "brier": statistics.fmean(briers) if n else None,
        "log_loss": statistics.fmean(loglosses) if n else None,
        "mean_pred": statistics.fmean(probs) if n else None,
        "actual_rate": statistics.fmean(actuals) if n else None,
        "_briers": briers, "_loglosses": loglosses,
    }


def paired_bootstrap(baseline_scores: list[float], candidate_scores: list[float],
                      n_resamples: int = 2000, seed: int = 1337) -> dict:
    assert len(baseline_scores) == len(candidate_scores)
    n = len(baseline_scores)
    point_delta = sum(candidate_scores) / n - sum(baseline_scores) / n
    rng = random.Random(seed)
    deltas = []
    for _ in range(n_resamples):
        idx = [rng.randrange(n) for _ in range(n)]
        b = sum(baseline_scores[i] for i in idx) / n
        c = sum(candidate_scores[i] for i in idx) / n
        deltas.append(c - b)
    deltas.sort()
    lo_i = int(0.025 * n_resamples)
    hi_i = min(int(0.975 * n_resamples), n_resamples - 1)
    return {
        "point_delta": point_delta, "ci_low": deltas[lo_i], "ci_high": deltas[hi_i],
        "frac_resamples_improved": sum(1 for d in deltas if d < 0) / n_resamples,
        "n_resamples": n_resamples,
    }


def calibration_table(probs: list[float], actuals: list[float],
                       edges=(0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75)) -> list[dict]:
    out = []
    for lo, hi in zip(edges, edges[1:]):
        idx = [i for i, p in enumerate(probs) if lo <= p < hi]
        if not idx:
            out.append({"lo": lo, "hi": hi, "n": 0, "mean_pred": None, "actual_rate": None, "low_n": True})
            continue
        mp = statistics.fmean(probs[i] for i in idx)
        ar = statistics.fmean(actuals[i] for i in idx)
        out.append({"lo": lo, "hi": hi, "n": len(idx), "mean_pred": mp, "actual_rate": ar,
                     "calibration_error": abs(mp - ar), "low_n": len(idx) < 30})
    return out


def prob_distribution_summary(probs: list[float]) -> dict:
    s = sorted(probs)
    n = len(s)
    def pct(p):
        idx = min(int(p * n), n - 1)
        return s[idx]
    return {"n": n, "min": s[0], "p10": pct(0.10), "p50": pct(0.50), "p90": pct(0.90), "max": s[-1],
            "frac_above_0_70": sum(1 for p in s if p > 0.70) / n,
            "frac_below_0_30": sum(1 for p in s if p < 0.30) / n}


def classify_hierarchy(all_starter_rows: list[dict]) -> dict:
    """Team-season -> WORKHORSE / TANDEM / UNCERTAIN, from prior START
    SHARE only (Part 12) -- reuses the exact thresholds already validated
    in the Stage 1 slice's own hierarchy classification
    (research/run_goalie_intelligence.py), renamed here per Part 12's own
    vocabulary (that slice called the top bucket "PRIMARY STARTER" and
    the bottom "BACKUP-HEAVY / UNCLEAR"; this slice's prompt calls them
    WORKHORSE / TANDEM / UNCERTAIN -- same 0.65 / 0.35 split, same >=10
    games-in-season minimum, no behavior change)."""
    by_team = defaultdict(list)
    for r in all_starter_rows:
        by_team[r["team"]].append(r)
    hierarchy = {}
    for team, rows in by_team.items():
        for season in [TUNING_SEASON] + EVAL_SEASONS:
            scoped = [r for r in rows if r["season"] == season]
            if len(scoped) < 10:
                continue
            shares = defaultdict(int)
            for r in scoped:
                shares[r["starter_goalie_id"]] += 1
            _, top_count = max(shares.items(), key=lambda kv: kv[1])
            top_share = top_count / len(scoped)
            if top_share >= 0.65:
                role = "WORKHORSE"
            elif top_share >= 0.35:
                role = "TANDEM"
            else:
                role = "UNCERTAIN"
            hierarchy[(team, season)] = role
    return hierarchy


def run_all() -> dict:
    games = ec.load_corpus(str(NHL_CORPUS_PATH))
    baseline_records, _ = ec.run_walkforward(games, weight_fn=None)

    starter_rows = gf.load_starter_corpus()
    pairs, join_excluded = build_starter_pair_examples(starter_rows, baseline_records)

    weights = load_fitted_starter_weights()
    quality_rows = gq.load_appearance_corpus()
    index = QualityIndex(quality_rows)
    hierarchy = classify_hierarchy(starter_rows)

    # ---- Pass 1: starter probabilities + raw quality lookups per pair ----
    for pair in pairs:
        home, away = pair["home"], pair["away"]
        pair["home_probs"] = gm.score_candidates(weights, home["feature_vectors"])
        pair["away_probs"] = gm.score_candidates(weights, away["feature_vectors"])
        pair["home_top_i"] = top1_index(pair["home_probs"])
        pair["away_top_i"] = top1_index(pair["away_probs"])
        pair["confidence"] = confidence_label(pair["home_probs"][pair["home_top_i"]],
                                               pair["away_probs"][pair["away_top_i"]])

        game_date = pair["game_date"]
        pair["home_adj_a"], pair["home_shots_a"] = {}, {}
        for goalie_id in home["candidates"]:
            adj, shots = quality_a_adj_logit(index, goalie_id, game_date)
            pair["home_adj_a"][goalie_id] = adj
            pair["home_shots_a"][goalie_id] = shots
        pair["away_adj_a"], pair["away_shots_a"] = {}, {}
        for goalie_id in away["candidates"]:
            adj, shots = quality_a_adj_logit(index, goalie_id, game_date)
            pair["away_adj_a"][goalie_id] = adj
            pair["away_shots_a"][goalie_id] = shots

        pair["home_raw_b"] = {w: {} for w in GSAX_WINDOW_GRID}
        for w in GSAX_WINDOW_GRID:
            for goalie_id in home["candidates"]:
                val, _ = quality_b_raw(index, goalie_id, game_date, w)
                pair["home_raw_b"][w][goalie_id] = val
        pair["away_raw_b"] = {w: {} for w in GSAX_WINDOW_GRID}
        for w in GSAX_WINDOW_GRID:
            for goalie_id in away["candidates"]:
                val, _ = quality_b_raw(index, goalie_id, game_date, w)
                pair["away_raw_b"][w][goalie_id] = val

        pair["hierarchy_home"] = hierarchy.get((pair["home_team"], pair["season"]), "UNCLASSIFIED")
        pair["hierarchy_away"] = hierarchy.get((pair["away_team"], pair["season"]), "UNCLASSIFIED")
        pair["tandem_game"] = "TANDEM" in (pair["hierarchy_home"], pair["hierarchy_away"])
        pair["home_mature"] = pair["home_shots_a"][home["actual_goalie_id"]] > 0
        pair["away_mature"] = pair["away_shots_a"][away["actual_goalie_id"]] > 0

    tuning_pairs = [p for p in pairs if p["season"] == TUNING_SEASON]
    eval_pairs = [p for p in pairs if p["season"] in EVAL_SEASONS]

    # ---- Candidate B (Part 15): window + logit-scale beta, TOP-1 basis, TUNING ONLY ----
    def top1_raw_diff(pair_list: list[dict], window) -> list[float]:
        diffs = []
        for p in pair_list:
            hg = p["home"]["candidates"][p["home_top_i"]]
            ag = p["away"]["candidates"][p["away_top_i"]]
            diffs.append(p["home_raw_b"][window][hg] - p["away_raw_b"][window][ag])
        return diffs

    def unconditional_correlation(pair_list: list[dict], window) -> float | None:
        """Diagnostic only (Part 21's "don't call microscopic changes
        meaningful"): the RAW correlation between the top-1 candidate
        quality differential and the actual outcome, computed WITHOUT
        conditioning on baseline Elo. Used in the report to sanity-check
        whether the tuning-Brier-based window selection below picked a
        window with real unconditional signal, or one that merely
        interacts favorably with baseline calibration noise."""
        diffs = top1_raw_diff(pair_list, window)
        actuals = [p["actual_home_win"] for p in pair_list]
        n = len(diffs)
        mx, my = statistics.fmean(diffs), statistics.fmean(actuals)
        sx, sy = statistics.pstdev(diffs), statistics.pstdev(actuals)
        if sx == 0 or sy == 0:
            return None
        cov = sum((x - mx) * (y - my) for x, y in zip(diffs, actuals)) / n
        return cov / (sx * sy)

    window_selection = {}
    best_window = best_beta = best_mean = best_stdev = best_tuning_brier = None
    tuning_actuals = [p["actual_home_win"] for p in tuning_pairs]
    tuning_base_logits = [xgc.logit(p["p_baseline"]) for p in tuning_pairs]
    for w in GSAX_WINDOW_GRID:
        raw_diffs = top1_raw_diff(tuning_pairs, w)
        mean, stdev = xgc.standardize_fit(raw_diffs)
        z = [xgc.standardize_apply(v, mean, stdev) for v in raw_diffs]
        beta = xgc.fit_logistic_weights(tuning_base_logits, [[zi] for zi in z], tuning_actuals)[0]
        preds = [xgc.sigmoid(bl + beta * zi) for bl, zi in zip(tuning_base_logits, z)]
        tuning_brier = statistics.fmean((pr - a) ** 2 for pr, a in zip(preds, tuning_actuals))
        window_selection[str(w)] = {
            "beta": beta, "mean": mean, "stdev": stdev, "tuning_top1_brier": tuning_brier,
            "tuning_raw_unconditional_correlation": unconditional_correlation(tuning_pairs, w),
        }
        if best_tuning_brier is None or tuning_brier < best_tuning_brier:
            best_window, best_beta, best_mean, best_stdev, best_tuning_brier = w, beta, mean, stdev, tuning_brier

    def adj_b(raw_value: float) -> float:
        return best_beta * xgc.standardize_apply(raw_value, best_mean, best_stdev)

    # ---- Pass 2: candidate probabilities (mixture / top-1 / oracle) per pair ----
    for pair in pairs:
        home, away = pair["home"], pair["away"]
        pb = pair["p_baseline"]

        home_pairs_a = [(pr, pair["home_adj_a"][g]) for g, pr in zip(home["candidates"], pair["home_probs"])]
        away_pairs_a = [(pr, pair["away_adj_a"][g]) for g, pr in zip(away["candidates"], pair["away_probs"])]
        pair["p_mix_a"] = gqi.scenario_weighted_probability(pb, home_pairs_a, away_pairs_a)
        pair["p_top1_a"] = gqi.top1_probability(pb, home_pairs_a[pair["home_top_i"]], away_pairs_a[pair["away_top_i"]])
        pair["p_oracle_a"] = gqi.oracle_probability(
            pb, pair["home_adj_a"][home["actual_goalie_id"]], pair["away_adj_a"][away["actual_goalie_id"]])

        home_pairs_b = [(pr, adj_b(pair["home_raw_b"][best_window][g]))
                         for g, pr in zip(home["candidates"], pair["home_probs"])]
        away_pairs_b = [(pr, adj_b(pair["away_raw_b"][best_window][g]))
                         for g, pr in zip(away["candidates"], pair["away_probs"])]
        pair["p_mix_b"] = gqi.scenario_weighted_probability(pb, home_pairs_b, away_pairs_b)
        pair["p_top1_b"] = gqi.top1_probability(pb, home_pairs_b[pair["home_top_i"]], away_pairs_b[pair["away_top_i"]])
        pair["p_oracle_b"] = gqi.oracle_probability(
            pb, adj_b(pair["home_raw_b"][best_window][home["actual_goalie_id"]]),
            adj_b(pair["away_raw_b"][best_window][away["actual_goalie_id"]]))

        home_pairs_d = [(pr, pair["home_adj_a"][g] + adj_b(pair["home_raw_b"][best_window][g]))
                         for g, pr in zip(home["candidates"], pair["home_probs"])]
        away_pairs_d = [(pr, pair["away_adj_a"][g] + adj_b(pair["away_raw_b"][best_window][g]))
                         for g, pr in zip(away["candidates"], pair["away_probs"])]
        pair["p_mix_d"] = gqi.scenario_weighted_probability(pb, home_pairs_d, away_pairs_d)

    # ---- per-window EVAL diagnostic table (Part 15/25): for EVERY window
    # in the grid (not just the tuning-selected one), compute mixture /
    # top-1 / oracle Brier on the true holdout and the resulting oracle
    # gap -- lets the report show whether the tuning-selected window's
    # apparent improvement is corroborated by the oracle sanity check or
    # is an artifact of that window's own noise (see the report's
    # discussion of Candidate B). ----
    per_window_eval = {}
    for w_str, spec in window_selection.items():
        w = None if w_str == "None" else int(w_str)
        beta, mean, stdev = spec["beta"], spec["mean"], spec["stdev"]
        def adj_w(raw, beta=beta, mean=mean, stdev=stdev):
            return beta * xgc.standardize_apply(raw, mean, stdev)
        mix_probs, top1_probs, oracle_probs = [], [], []
        for pair in eval_pairs:
            home, away = pair["home"], pair["away"]
            pb = pair["p_baseline"]
            home_pairs_w = [(pr, adj_w(pair["home_raw_b"][w][g])) for g, pr in zip(home["candidates"], pair["home_probs"])]
            away_pairs_w = [(pr, adj_w(pair["away_raw_b"][w][g])) for g, pr in zip(away["candidates"], pair["away_probs"])]
            mix_probs.append(gqi.scenario_weighted_probability(pb, home_pairs_w, away_pairs_w))
            top1_probs.append(gqi.top1_probability(pb, home_pairs_w[pair["home_top_i"]], away_pairs_w[pair["away_top_i"]]))
            oracle_probs.append(gqi.oracle_probability(
                pb, adj_w(pair["home_raw_b"][w][home["actual_goalie_id"]]),
                adj_w(pair["away_raw_b"][w][away["actual_goalie_id"]])))
        eval_actuals = [p["actual_home_win"] for p in eval_pairs]
        mix_e = evaluate_series(mix_probs, eval_actuals)
        top1_e = evaluate_series(top1_probs, eval_actuals)
        oracle_e = evaluate_series(oracle_probs, eval_actuals)
        per_window_eval[w_str] = {
            "mix_brier": mix_e["brier"], "top1_brier": top1_e["brier"], "oracle_brier": oracle_e["brier"],
            "oracle_gap_brier": oracle_e["brier"] - mix_e["brier"],
        }

    return {
        "baseline_records": baseline_records, "pairs": pairs, "tuning_pairs": tuning_pairs,
        "eval_pairs": eval_pairs, "join_excluded": join_excluded, "window_selection": window_selection,
        "best_window": best_window, "best_beta": best_beta, "best_mean": best_mean, "best_stdev": best_stdev,
        "starter_weights": weights, "per_window_eval": per_window_eval,
    }


def relative_delta(candidate: float, baseline: float) -> float:
    return (candidate - baseline) / baseline if baseline else None


def segment_report(pairs: list[dict], key_fn, actuals_key: str = "actual_home_win") -> dict:
    """Groups `pairs` by key_fn(pair) and reports baseline/mix_a/mix_b
    Brier + log loss + n within each group -- the shared helper behind
    the confidence / B2B / tandem breakdowns (Parts 10/11/12)."""
    groups = defaultdict(list)
    for p in pairs:
        groups[key_fn(p)].append(p)
    out = {}
    for key, group in groups.items():
        actuals = [p[actuals_key] for p in group]
        out[str(key)] = {
            "n": len(group),
            "baseline": evaluate_series([p["p_baseline"] for p in group], actuals),
            "mix_a": evaluate_series([p["p_mix_a"] for p in group], actuals),
            "mix_b": evaluate_series([p["p_mix_b"] for p in group], actuals),
        }
        for sub in out[key if isinstance(key, str) else str(key)].values():
            if isinstance(sub, dict):
                sub.pop("_briers", None)
                sub.pop("_loglosses", None)
    return out


def strip_internal(e: dict) -> dict:
    return {k: v for k, v in e.items() if not k.startswith("_")}


def pick_example(pairs: list[dict], predicate, fallback=None) -> dict | None:
    for p in pairs:
        if predicate(p):
            return p
    return fallback


def summarize_example(p: dict) -> dict:
    home, away = p["home"], p["away"]
    return {
        "game_id": p["game_id"], "game_date": p["game_date"], "season": p["season"],
        "home_team": p["home_team"], "away_team": p["away_team"],
        "home_candidates": home["candidates"], "home_probs": [round(x, 4) for x in p["home_probs"]],
        "away_candidates": away["candidates"], "away_probs": [round(x, 4) for x in p["away_probs"]],
        "home_actual_starter": home["actual_goalie_id"], "away_actual_starter": away["actual_goalie_id"],
        "home_projected_top1": home["candidates"][p["home_top_i"]],
        "away_projected_top1": away["candidates"][p["away_top_i"]],
        "top1_correct": (home["candidates"][p["home_top_i"]] == home["actual_goalie_id"] and
                          away["candidates"][p["away_top_i"]] == away["actual_goalie_id"]),
        "is_back_to_back": p["is_back_to_back"], "confidence": p["confidence"],
        "hierarchy_home": p["hierarchy_home"], "hierarchy_away": p["hierarchy_away"],
        "tandem_game": p["tandem_game"], "actual_home_win": p["actual_home_win"],
        "p_baseline": round(p["p_baseline"], 4), "p_mix_a": round(p["p_mix_a"], 4),
        "p_top1_a": round(p["p_top1_a"], 4), "p_oracle_a": round(p["p_oracle_a"], 4),
        "p_mix_b": round(p["p_mix_b"], 4), "p_top1_b": round(p["p_top1_b"], 4),
        "brier_baseline": round(brier(p["p_baseline"], p["actual_home_win"]), 4),
        "brier_mix_a": round(brier(p["p_mix_a"], p["actual_home_win"]), 4),
        "brier_mix_b": round(brier(p["p_mix_b"], p["actual_home_win"]), 4),
        "brier_top1_a": round(brier(p["p_top1_a"], p["actual_home_win"]), 4),
    }


def build_representative_examples(eval_pairs: list[dict]) -> dict:
    workhorse = pick_example(eval_pairs, lambda p: p["hierarchy_home"] == "WORKHORSE" and p["confidence"] == "HIGH")
    tandem = pick_example(eval_pairs, lambda p: p["tandem_game"] and p["confidence"] != "HIGH")
    b2b = pick_example(eval_pairs, lambda p: p["is_back_to_back"])
    wrong_home = pick_example(eval_pairs, lambda p: p["home"]["candidates"][p["home_top_i"]] != p["home"]["actual_goalie_id"])
    right_both = pick_example(eval_pairs, lambda p:
                               p["home"]["candidates"][p["home_top_i"]] == p["home"]["actual_goalie_id"] and
                               p["away"]["candidates"][p["away_top_i"]] == p["away"]["actual_goalie_id"] and
                               p["confidence"] == "HIGH")
    mixture_beats_top1 = pick_example(eval_pairs, lambda p:
                                       brier(p["p_mix_a"], p["actual_home_win"]) <
                                       brier(p["p_top1_a"], p["actual_home_win"]) - 0.02)
    adjustment_helps = pick_example(eval_pairs, lambda p:
                                     brier(p["p_mix_b"], p["actual_home_win"]) <
                                     brier(p["p_baseline"], p["actual_home_win"]) - 0.05)
    adjustment_hurts = pick_example(eval_pairs, lambda p:
                                     brier(p["p_mix_b"], p["actual_home_win"]) >
                                     brier(p["p_baseline"], p["actual_home_win"]) + 0.05)

    named = {
        "clear_workhorse_team": workhorse, "tandem_team_low_confidence": tandem,
        "back_to_back_situation": b2b, "projected_starter_wrong": wrong_home,
        "projected_starter_right_high_confidence": right_both,
        "distribution_outperforms_top1": mixture_beats_top1,
        "goalie_adjustment_helps": adjustment_helps, "goalie_adjustment_hurts": adjustment_hurts,
    }
    return {name: (summarize_example(p) if p else None) for name, p in named.items()}


def build_full_results() -> dict:
    r = run_all()
    eval_pairs, tuning_pairs, pairs = r["eval_pairs"], r["tuning_pairs"], r["pairs"]
    actuals = [p["actual_home_win"] for p in eval_pairs]

    candidate_probs = {
        "baseline": [p["p_baseline"] for p in eval_pairs],
        "mix_a": [p["p_mix_a"] for p in eval_pairs], "top1_a": [p["p_top1_a"] for p in eval_pairs],
        "oracle_a": [p["p_oracle_a"] for p in eval_pairs],
        "mix_b": [p["p_mix_b"] for p in eval_pairs], "top1_b": [p["p_top1_b"] for p in eval_pairs],
        "oracle_b": [p["p_oracle_b"] for p in eval_pairs],
        "mix_d": [p["p_mix_d"] for p in eval_pairs],
    }
    evals = {name: evaluate_series(probs, actuals) for name, probs in candidate_probs.items()}

    baseline_e = evals["baseline"]
    deltas = {}
    for name in ("mix_a", "top1_a", "oracle_a", "mix_b", "top1_b", "oracle_b", "mix_d"):
        e = evals[name]
        deltas[name] = {
            "brier_abs_delta": e["brier"] - baseline_e["brier"],
            "brier_rel_delta": relative_delta(e["brier"], baseline_e["brier"]),
            "log_loss_abs_delta": e["log_loss"] - baseline_e["log_loss"],
            "log_loss_rel_delta": relative_delta(e["log_loss"], baseline_e["log_loss"]),
        }

    bootstrap = {
        "mix_a_brier": paired_bootstrap(baseline_e["_briers"], evals["mix_a"]["_briers"]),
        "mix_a_logloss": paired_bootstrap(baseline_e["_loglosses"], evals["mix_a"]["_loglosses"]),
        "mix_b_brier": paired_bootstrap(baseline_e["_briers"], evals["mix_b"]["_briers"]),
        "mix_b_logloss": paired_bootstrap(baseline_e["_loglosses"], evals["mix_b"]["_loglosses"]),
        "mix_d_brier": paired_bootstrap(baseline_e["_briers"], evals["mix_d"]["_briers"]),
    }

    calibration = {
        "baseline": calibration_table(candidate_probs["baseline"], actuals),
        "mix_a": calibration_table(candidate_probs["mix_a"], actuals),
        "mix_b": calibration_table(candidate_probs["mix_b"], actuals),
    }
    prob_dist = {name: prob_distribution_summary(probs) for name, probs in candidate_probs.items()}

    season_breakdown = {}
    for season in sorted({p["season"] for p in eval_pairs}):
        subset = [p for p in eval_pairs if p["season"] == season]
        sub_actuals = [p["actual_home_win"] for p in subset]
        season_breakdown[str(season)] = {
            "n": len(subset),
            "baseline": strip_internal(evaluate_series([p["p_baseline"] for p in subset], sub_actuals)),
            "mix_a": strip_internal(evaluate_series([p["p_mix_a"] for p in subset], sub_actuals)),
            "mix_b": strip_internal(evaluate_series([p["p_mix_b"] for p in subset], sub_actuals)),
        }

    confidence_breakdown = segment_report(eval_pairs, lambda p: p["confidence"])
    b2b_breakdown = segment_report(eval_pairs, lambda p: "b2b" if p["is_back_to_back"] else "non_b2b")
    tandem_breakdown = segment_report(eval_pairs, lambda p: "tandem_game" if p["tandem_game"] else "clear_hierarchy_game")

    common_eval_set = {
        "total_baseline_games_eval_seasons": len([b for b in r["baseline_records"] if b["season"] in EVAL_SEASONS]),
        "total_baseline_games_tuning_season": len([b for b in r["baseline_records"] if b["season"] == TUNING_SEASON]),
        "games_with_valid_starter_pair_eval": len(eval_pairs),
        "games_with_valid_starter_pair_tuning": len(tuning_pairs),
        "join_excluded": r["join_excluded"],
        "coverage_pct_eval": round(100.0 * len(eval_pairs) /
                                    max(1, len([b for b in r["baseline_records"] if b["season"] in EVAL_SEASONS])), 2),
        "both_actual_starters_have_prior_quality_appearance_eval": sum(
            1 for p in eval_pairs if p["home_mature"] and p["away_mature"]),
        "both_actual_starters_mature_pct_eval": round(100.0 * sum(
            1 for p in eval_pairs if p["home_mature"] and p["away_mature"]) / max(1, len(eval_pairs)), 2),
    }

    oracle_gap = {
        "candidate_a": {"eval_brier_mix": evals["mix_a"]["brier"], "eval_brier_oracle": evals["oracle_a"]["brier"],
                         "gap_brier": evals["oracle_a"]["brier"] - evals["mix_a"]["brier"],
                         "eval_logloss_mix": evals["mix_a"]["log_loss"], "eval_logloss_oracle": evals["oracle_a"]["log_loss"],
                         "gap_logloss": evals["oracle_a"]["log_loss"] - evals["mix_a"]["log_loss"]},
        "candidate_b": {"eval_brier_mix": evals["mix_b"]["brier"], "eval_brier_oracle": evals["oracle_b"]["brier"],
                         "gap_brier": evals["oracle_b"]["brier"] - evals["mix_b"]["brier"],
                         "eval_logloss_mix": evals["mix_b"]["log_loss"], "eval_logloss_oracle": evals["oracle_b"]["log_loss"],
                         "gap_logloss": evals["oracle_b"]["log_loss"] - evals["mix_b"]["log_loss"]},
        "per_window_eval_detail": r["per_window_eval"],
    }

    examples = build_representative_examples(eval_pairs)

    results = {
        "config": {
            "warmup_season": WARMUP_SEASON, "tuning_season": TUNING_SEASON, "eval_seasons": EVAL_SEASONS,
            "min_history_games": MIN_HISTORY_GAMES, "candidate_window": CANDIDATE_WINDOW,
            "gsax_window_grid": [str(w) for w in GSAX_WINDOW_GRID],
            "elo_points_to_logit_conversion": ELO_POINTS_TO_LOGIT,
        },
        "starter_model_fitted_weights_reused": dict(zip(gm.FEATURE_NAMES, r["starter_weights"])),
        "gsax_window_selection": r["window_selection"],
        "gsax_selected_window": str(r["best_window"]),
        "common_evaluation_set": common_eval_set,
        "headline_metrics": {name: strip_internal(e) for name, e in evals.items()},
        "deltas_vs_baseline": deltas,
        "paired_bootstrap": bootstrap,
        "calibration": calibration,
        "probability_distribution": prob_dist,
        "season_breakdown": season_breakdown,
        "confidence_bucket_breakdown": confidence_breakdown,
        "back_to_back_breakdown": b2b_breakdown,
        "tandem_hierarchy_breakdown": tandem_breakdown,
        "oracle_gap_analysis": oracle_gap,
        "representative_examples": examples,
    }
    return results


if __name__ == "__main__":
    out = build_full_results()
    proc = subprocess.run([sys.executable, "-m", "unittest", "discover", "tests"],
                           cwd=str(REPO_ROOT), capture_output=True, text=True)
    out["test_suite_stderr_tail"] = "\n".join(proc.stderr.strip().splitlines()[-8:])
    out["test_suite_returncode"] = proc.returncode

    out_path = REPO_ROOT / "research" / "goalie_quality_integration_results.json"
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, sort_keys=True, default=str)
    print(f"wrote {out_path}")
    print("test suite returncode:", out["test_suite_returncode"])
    print(out["test_suite_stderr_tail"])
