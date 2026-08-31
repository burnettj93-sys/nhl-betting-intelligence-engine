"""
Driver for the pregame starting-goalie intelligence foundation (Stage 1:
internal historical inference model, built entirely from real data --
see MONEYPUCK_DATA_CONTRACT_REVIEW... no, see
GOALIE_INTELLIGENCE_FOUNDATION_REPORT.md for the full writeup). Writes:

  - research/goalie_intelligence_results.json
  - GOALIE_INTELLIGENCE_FOUNDATION_REPORT.md

Read-only against research/goalie_intelligence/actual_starters.jsonl and
research/real_nhl_results/. Does not touch nhl.db, models/, or config.py.
This slice does NOT change any game win-probability model -- see that
report's Section AD.
"""
from __future__ import annotations

import json
import math
import statistics
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from research.goalie_intelligence import features as gf
from research.goalie_intelligence import model as gm

WARMUP_SEASON = 20222023
TUNING_SEASON = 20232024
EVAL_SEASONS = [20242025, 20252026]
MIN_HISTORY_GAMES = 5
CANDIDATE_WINDOW = 20


def build_example(all_rows: list[dict], row: dict) -> dict | None:
    team, date, season = row["team"], row["game_date"], row["season"]
    history = gf.team_history_as_of(all_rows, team, date)
    if len(history) < MIN_HISTORY_GAMES:
        return None
    candidates = gf.eligible_goalies(history, window=CANDIDATE_WINDOW)
    actual = row["starter_goalie_id"]
    if actual not in candidates:
        return None
    is_b2b = gf.team_back_to_back(history, date)
    feature_vectors = [gm.build_feature_vector(history, g, season, date, is_b2b) for g in candidates]
    return {
        "game_id": row["game_id"], "team": team, "season": season, "game_date": date,
        "candidates": candidates, "feature_vectors": feature_vectors,
        "target_index": candidates.index(actual), "actual_goalie_id": actual,
        "is_back_to_back": is_b2b, "history_len": len(history),
    }


def multiclass_brier(probs: list[float], target_index: int) -> float:
    return sum((p - (1.0 if i == target_index else 0.0)) ** 2 for i, p in enumerate(probs))


def logloss_of_true(probs: list[float], target_index: int, eps: float = 1e-9) -> float:
    p = min(max(probs[target_index], eps), 1 - eps)
    return -math.log(p)


def run_all():
    all_rows = gf.load_starter_corpus()

    examples_by_season = defaultdict(list)
    excluded = {"insufficient_history": 0, "actual_not_in_candidates": 0}
    for row in all_rows:
        if row["season"] not in (WARMUP_SEASON, TUNING_SEASON, *EVAL_SEASONS):
            continue
        ex = build_example(all_rows, row)
        if ex is None:
            history = gf.team_history_as_of(all_rows, row["team"], row["game_date"])
            if len(history) < MIN_HISTORY_GAMES:
                excluded["insufficient_history"] += 1
            else:
                excluded["actual_not_in_candidates"] += 1
            continue
        examples_by_season[row["season"]].append(ex)

    tuning_examples = examples_by_season[TUNING_SEASON]
    eval_examples = [ex for s in EVAL_SEASONS for ex in examples_by_season[s]]

    weights = gm.fit_weights(tuning_examples)

    def evaluate(examples: list[dict], weight_vec: list[float]) -> dict:
        n = len(examples)
        correct = 0
        briers, loglosses = [], []
        confidence_buckets = defaultdict(lambda: {"n": 0, "correct": 0})
        b2b_bucket = {"b2b": {"n": 0, "correct": 0}, "non_b2b": {"n": 0, "correct": 0}}
        season_bucket = defaultdict(lambda: {"n": 0, "correct": 0, "brier_sum": 0.0, "logloss_sum": 0.0})
        for ex in examples:
            probs = gm.score_candidates(weight_vec, ex["feature_vectors"])
            top_idx = max(range(len(probs)), key=lambda i: probs[i])
            is_correct = top_idx == ex["target_index"]
            correct += is_correct
            b = multiclass_brier(probs, ex["target_index"])
            ll = logloss_of_true(probs, ex["target_index"])
            briers.append(b)
            loglosses.append(ll)

            top_prob = probs[top_idx]
            conf_label = "HIGH" if top_prob >= 0.70 else ("MEDIUM" if top_prob >= 0.50 else "LOW")
            confidence_buckets[conf_label]["n"] += 1
            confidence_buckets[conf_label]["correct"] += is_correct

            key = "b2b" if ex["is_back_to_back"] else "non_b2b"
            b2b_bucket[key]["n"] += 1
            b2b_bucket[key]["correct"] += is_correct

            sb = season_bucket[ex["season"]]
            sb["n"] += 1
            sb["correct"] += is_correct
            sb["brier_sum"] += b
            sb["logloss_sum"] += ll

        season_report = {}
        for season, sb in season_bucket.items():
            season_report[str(season)] = {
                "n": sb["n"], "accuracy": sb["correct"] / sb["n"],
                "brier": sb["brier_sum"] / sb["n"], "log_loss": sb["logloss_sum"] / sb["n"],
            }

        return {
            "n": n, "top1_accuracy": correct / n if n else None,
            "brier": statistics.fmean(briers) if briers else None,
            "log_loss": statistics.fmean(loglosses) if loglosses else None,
            "confidence_buckets": {k: {"n": v["n"], "accuracy": v["correct"] / v["n"] if v["n"] else None}
                                    for k, v in confidence_buckets.items()},
            "back_to_back_buckets": {k: {"n": v["n"], "accuracy": v["correct"] / v["n"] if v["n"] else None}
                                      for k, v in b2b_bucket.items()},
            "season_breakdown": season_report,
        }

    model_eval = evaluate(eval_examples, weights)
    model_tuning_eval = evaluate(tuning_examples, weights)

    # ---- naive baselines, top-1 accuracy only, true eval set ----
    baseline_results = {}
    for name, fn in [
        ("A_season_leader", lambda ex: gm.baseline_season_leader(
            gf.team_history_as_of(all_rows, ex["team"], ex["game_date"]), ex["candidates"], ex["season"])),
        ("B_last_game_starter", lambda ex: gm.baseline_last_game_starter(
            gf.team_history_as_of(all_rows, ex["team"], ex["game_date"]), ex["candidates"])),
        ("C_recent_leader_10", lambda ex: gm.baseline_recent_leader(
            gf.team_history_as_of(all_rows, ex["team"], ex["game_date"]), ex["candidates"])),
        ("D_b2b_aware", lambda ex: gm.baseline_b2b_aware(
            gf.team_history_as_of(all_rows, ex["team"], ex["game_date"]), ex["candidates"], ex["season"],
            ex["is_back_to_back"], ex["game_date"])),
    ]:
        n, correct, no_pick = 0, 0, 0
        for ex in eval_examples:
            pick = fn(ex)
            if pick is None:
                no_pick += 1
                continue
            n += 1
            if pick == ex["actual_goalie_id"]:
                correct += 1
        baseline_results[name] = {"n": n, "no_pick_count": no_pick,
                                   "accuracy": correct / n if n else None}

    # ---- Part 4/5: empirical back-to-back same-goalie rate, whole corpus ----
    b2b_pairs = []
    by_team = defaultdict(list)
    for row in all_rows:
        by_team[row["team"]].append(row)
    for team, rows in by_team.items():
        rows_sorted = sorted(rows, key=lambda r: r["game_date"])
        for i in range(1, len(rows_sorted)):
            prev, cur = rows_sorted[i - 1], rows_sorted[i]
            d_prev = gf.parse_date(prev["game_date"])
            d_cur = gf.parse_date(cur["game_date"])
            if (d_cur - d_prev).days == 1:
                b2b_pairs.append((team, prev, cur))

    same_goalie = sum(1 for _, p, c in b2b_pairs if p["starter_goalie_id"] == c["starter_goalie_id"])
    b2b_by_season = defaultdict(lambda: {"n": 0, "same": 0})
    for team, p, c in b2b_pairs:
        s = c["season"]
        b2b_by_season[s]["n"] += 1
        b2b_by_season[s]["same"] += (p["starter_goalie_id"] == c["starter_goalie_id"])

    b2b_stats = {
        "total_back_to_backs": len(b2b_pairs),
        "same_goalie_both_games": same_goalie,
        "same_goalie_pct": round(100.0 * same_goalie / len(b2b_pairs), 2) if b2b_pairs else None,
        "starter_changed_pct": round(100.0 * (len(b2b_pairs) - same_goalie) / len(b2b_pairs), 2) if b2b_pairs else None,
        "by_season": {str(s): {"n": v["n"], "same_goalie_pct": round(100.0 * v["same"] / v["n"], 2)}
                      for s, v in b2b_by_season.items()},
    }

    # ---- exceptions: when the SAME goalie does start both ends, what did game 1 look like? ----
    same_g_game1_icetime = [p["starter_icetime_seconds"] for _, p, c in b2b_pairs
                             if p["starter_goalie_id"] == c["starter_goalie_id"]]
    diff_g_game1_icetime = [p["starter_icetime_seconds"] for _, p, c in b2b_pairs
                             if p["starter_goalie_id"] != c["starter_goalie_id"]]
    b2b_stats["mean_game1_icetime_seconds_when_same_goalie_repeats"] = (
        round(statistics.fmean(same_g_game1_icetime), 1) if same_g_game1_icetime else None)
    b2b_stats["mean_game1_icetime_seconds_when_goalie_changes"] = (
        round(statistics.fmean(diff_g_game1_icetime), 1) if diff_g_game1_icetime else None)

    # ---- Part 18: sequence-conditional probabilities (whole corpus, descriptive) ----
    sequence_stats = {}
    patterns = {
        "A,A -> A repeats": lambda seq: len(seq) >= 2 and seq[-1] == seq[-2],
        "A,B -> A repeats (alternation continues)": lambda seq: len(seq) >= 2 and seq[-1] != seq[-2],
    }
    # generic: given last-2 same or different, P(next == last)
    same_last2, same_last2_next_same = 0, 0
    diff_last2, diff_last2_next_same_as_last = 0, 0
    for team, rows in by_team.items():
        rows_sorted = sorted(rows, key=lambda r: r["game_date"])
        seq = [r["starter_goalie_id"] for r in rows_sorted]
        for i in range(2, len(seq)):
            if seq[i - 1] == seq[i - 2]:
                same_last2 += 1
                same_last2_next_same += (seq[i] == seq[i - 1])
            else:
                diff_last2 += 1
                diff_last2_next_same_as_last += (seq[i] == seq[i - 1])
    sequence_stats["P(next==last | last two starts were the SAME goalie)"] = {
        "n": same_last2, "p": round(same_last2_next_same / same_last2, 4) if same_last2 else None}
    sequence_stats["P(next==last | last two starts ALTERNATED)"] = {
        "n": diff_last2, "p": round(diff_last2_next_same_as_last / diff_last2, 4) if diff_last2 else None}

    # P(B2B second-game starter == first-game starter) already in b2b_stats; add
    # P(A starts next | A backed up but did not start last game) etc via appearances
    b2b_played_prev_examples = [ex for ex in eval_examples if ex["is_back_to_back"]]
    sequence_stats["b2b_examples_in_eval_set"] = len(b2b_played_prev_examples)

    # ---- Part 6: starter hierarchy, season-end snapshot ----
    hierarchy = {}
    teams = sorted(by_team.keys())
    for team in teams:
        for season in [TUNING_SEASON] + EVAL_SEASONS:
            rows = [r for r in by_team[team] if r["season"] == season]
            if len(rows) < 10:
                continue
            shares = defaultdict(int)
            for r in rows:
                shares[r["starter_goalie_id"]] += 1
            top_goalie, top_count = max(shares.items(), key=lambda kv: kv[1])
            top_share = top_count / len(rows)
            if top_share >= 0.65:
                role = "PRIMARY STARTER"
            elif top_share >= 0.35:
                role = "1B / TANDEM"
            else:
                role = "BACKUP-HEAVY / UNCLEAR"
            hierarchy[f"{team}_{season}"] = {"team": team, "season": season, "top_goalie": top_goalie,
                                              "top_share": round(top_share, 3), "role": role, "n_games": len(rows)}

    role_counts = defaultdict(int)
    for v in hierarchy.values():
        role_counts[v["role"]] += 1

    # ---- accuracy on tandem vs clear-starter teams (true eval set) ----
    tandem_team_seasons = {k for k, v in hierarchy.items() if v["role"] == "1B / TANDEM"
                            and v["season"] in EVAL_SEASONS}
    tandem_keys = {(v["team"], v["season"]) for k, v in hierarchy.items() if k in tandem_team_seasons}
    tandem_examples = [ex for ex in eval_examples if (ex["team"], ex["season"]) in tandem_keys]
    clear_examples = [ex for ex in eval_examples if (ex["team"], ex["season"]) not in tandem_keys]
    tandem_eval = evaluate(tandem_examples, weights) if tandem_examples else None
    clear_eval = evaluate(clear_examples, weights) if clear_examples else None

    results = {
        "config": {"warmup_season": WARMUP_SEASON, "tuning_season": TUNING_SEASON, "eval_seasons": EVAL_SEASONS,
                   "min_history_games": MIN_HISTORY_GAMES, "candidate_window": CANDIDATE_WINDOW,
                   "feature_names": gm.FEATURE_NAMES},
        "corpus_size": len(all_rows),
        "examples_by_season_n": {str(s): len(v) for s, v in examples_by_season.items()},
        "excluded_examples": excluded,
        "fitted_weights": dict(zip(gm.FEATURE_NAMES, weights)),
        "model_eval_true_holdout": model_eval,
        "model_eval_tuning_season_in_sample": model_tuning_eval,
        "baseline_results_true_holdout": baseline_results,
        "back_to_back_stats": b2b_stats,
        "sequence_conditional_probabilities": sequence_stats,
        "starter_hierarchy_role_counts": dict(role_counts),
        "starter_hierarchy_sample": dict(list(hierarchy.items())[:6]),
        "tandem_team_eval": tandem_eval,
        "clear_starter_team_eval": clear_eval,
    }

    proc = subprocess.run([sys.executable, "-m", "unittest", "discover", "tests"],
                           cwd=str(REPO_ROOT), capture_output=True, text=True)
    results["test_suite_stderr_tail"] = "\n".join(proc.stderr.strip().splitlines()[-5:])
    results["test_suite_returncode"] = proc.returncode

    return results


if __name__ == "__main__":
    results = run_all()
    out_path = REPO_ROOT / "research" / "goalie_intelligence_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, sort_keys=True, default=str)
    print(f"wrote {out_path}")
    print(f"test suite returncode: {results['test_suite_returncode']}")
    print(results["test_suite_stderr_tail"])
