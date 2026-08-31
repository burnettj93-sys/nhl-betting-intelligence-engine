"""
Unified Sparse-Prop LOW-Confidence Gating Review -- Assists + Points +
Goals (with SOG + Blocks as controls).

This is a POLICY REVIEW, not a model-building script. It builds NO new
model and refits NOTHING -- every number here is read directly from each
prop's own already-persisted, already-frozen results file
(research/player_*_results.json). The only NEW computation is a small,
explicitly read-only re-scoring pass (reusing each prop's already-locked
weights, exactly the same pattern used in
research/run_confidence_diagnostics.py) to recover LOW-bucket root-cause
composition (mean history length, mean appearance rate) for GOALS, which
was not persisted in the original Goals validation cycle's result file.
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from research import elo_comparison as ec
from research.player_goals import features as gf
from research.player_sog import count_models as cm
from research.run_player_sog_model import build_team_schedules, NHL_CORPUS_PATH
import research.run_player_goals_model as gm

HEADLINE_THRESHOLD = {"SOG": "4", "BLOCKS": "2", "ASSISTS": "1", "POINTS": "1", "GOALS": "1"}
RESULT_FILES = {
    "SOG": "player_sog_results.json", "BLOCKS": "player_blocks_results.json",
    "ASSISTS": "player_assists_results.json", "POINTS": "player_points_results.json",
    "GOALS": "player_goals_results.json",
}


def naive_brier(actual_rate: float) -> float:
    return actual_rate * (1 - actual_rate)


def load_confidence_table():
    out = {}
    for prop, filename in RESULT_FILES.items():
        data = json.loads((REPO_ROOT / "research" / filename).read_text())
        threshold = HEADLINE_THRESHOLD[prop]
        cb = data["confidence_breakdown"]
        total_n = sum(cb[l]["n"] for l in ("HIGH", "MEDIUM", "LOW"))
        prop_out = {"headline_threshold": threshold, "total_n": total_n, "buckets": {}}
        for label in ("HIGH", "MEDIUM", "LOW"):
            n = cb[label]["n"]
            t = cb[label]["thresholds"][threshold]
            mean_pred = t.get("mean_pred")
            prop_out["buckets"][label] = {
                "n": n, "share_of_total": n / total_n,
                "brier": t["brier"], "brier_skill_score": t["brier_skill_score"],
                "baseline_naive_brier": naive_brier(t["actual_rate"]),
                "log_loss": t.get("log_loss"),
                "mean_pred": mean_pred,
                "actual_rate": t["actual_rate"],
                "calibration_error": (mean_pred - t["actual_rate"]) if mean_pred is not None else None,
            }
        out[prop] = prop_out
    return out


def load_sparsity_table():
    """Real event-frequency / zero-rate comparison, Part 3."""
    sog = json.loads((REPO_ROOT / "research" / "player_sog_results.json").read_text())
    blk = json.loads((REPO_ROOT / "research" / "player_blocks_results.json").read_text())
    ast_ = json.loads((REPO_ROOT / "research" / "player_assists_results.json").read_text())
    pts = json.loads((REPO_ROOT / "research" / "player_points_results.json").read_text())
    gls = json.loads((REPO_ROOT / "research" / "player_goals_results.json").read_text())

    def zero_rate(path, field):
        p = REPO_ROOT / "research" / path
        if not p.exists():
            return None
        rows = [json.loads(l) for l in open(p)]
        return sum(1 for r in rows if r[field] == 0) / len(rows)

    return {
        "SOG": {"mean": sog["overdispersion"]["mean"], "zero_rate": zero_rate("player_sog/player_game_sog.jsonl", "sog")},
        "BLOCKS": {"mean": blk["overdispersion"]["mean"], "zero_rate": zero_rate("player_blocks/player_game_blocks.jsonl", "blocks")},
        "ASSISTS": {"mean": ast_["overdispersion"]["mean"], "zero_rate": zero_rate("player_assists/player_game_assists.jsonl", "assists")},
        "POINTS": {"mean": pts["distribution_analysis"]["tuning_fit_mean"], "zero_rate": pts["distribution_analysis"]["observed_zero_rate"]},
        "GOALS": {"mean": gls["distribution_analysis"]["tuning_fit_mean"], "zero_rate": gls["distribution_analysis"]["observed_zero_rate"]},
    }


def goals_low_bucket_composition():
    """Read-only re-scoring (reuses the FROZEN locked Goals weights,
    exactly research.run_confidence_diagnostics.py's pattern) -- recovers
    LOW/HIGH bucket composition that the original Goals validation cycle
    did not persist. No refit, no new weights."""
    results = json.loads((REPO_ROOT / "research" / "player_goals_results.json").read_text())
    rows = gf.load_goals_corpus()
    index = gf.PlayerHistoryIndex(rows)
    totals = gf.build_team_game_goals_totals(rows)
    team_offense_hist = gf.build_team_offense_history(totals)
    opponent_env = gf.build_opponent_goals_allowed(totals)
    league_avg = statistics.fmean(v["goals_for"] for v in totals.values())
    all_sog = sum(r["sog"] for r in rows)
    league_shooting_pct = sum(r["goals"] for r in rows) / all_sog
    games = ec.load_corpus(str(NHL_CORPUS_PATH))
    team_schedules = build_team_schedules(games)

    by_label = {"LOW": {"hist": [], "appear": []}, "HIGH": {"hist": [], "appear": []}}
    for row in rows:
        if row["season"] not in (20242025, 20252026):
            continue
        ex = gm.build_example(row, index, team_schedules, team_offense_hist, opponent_env,
                               league_avg, league_shooting_pct, {1: 0.15, 2: 0.02})
        if ex is None:
            continue
        label, _, _ = cm.confidence_score(ex["history_len"], ex["recent_toi_cv"], ex["recent_goals_cv"],
                                           ex["opponent_window_games"], 20, ex["appearance_rate"])
        if label in by_label:
            by_label[label]["hist"].append(ex["history_len"])
            by_label[label]["appear"].append(ex["appearance_rate"])

    return {label: {"n": len(d["hist"]), "mean_history_len": statistics.fmean(d["hist"]),
                     "mean_appearance_rate": statistics.fmean(d["appear"])}
            for label, d in by_label.items()}


def run_all():
    confidence_table = load_confidence_table()
    sparsity_table = load_sparsity_table()
    goals_composition = goals_low_bucket_composition()

    # cross-reference against the already-established Assists/Points
    # composition (research/confidence_framework_results.json, from the
    # earlier Confidence Framework Redesign cycle) -- read, not recomputed.
    conf_framework_path = REPO_ROOT / "research" / "confidence_framework_results.json"
    prior_composition = {}
    if conf_framework_path.exists():
        cf = json.loads(conf_framework_path.read_text())
        for prop in ("ASSISTS", "POINTS"):
            low = cf["root_cause_composition"][prop]["LOW"]
            prior_composition[prop] = {"n": low["n"], "mean_history_len": low["mean_history_len"],
                                        "mean_appearance_rate": low["mean_appearance_rate"]}

    policy_decision = {
        "SOG": "NO_RESTRICTION (LOW skill +0.027, positive)",
        "BLOCKS": "NO_RESTRICTION (LOW skill +0.002, ~neutral, non-negative)",
        "ASSISTS": "WATCH_ONLY (already in place, RETAINED -- LOW skill -0.043, unchanged evidence)",
        "POINTS": "WATCH_ONLY (already in place, RETAINED -- LOW skill -0.036, unchanged evidence)",
        "GOALS": "WATCH_ONLY (NEW -- LOW skill -0.032, same root-cause signature as Assists/Points)",
    }

    out = {
        "confidence_table": confidence_table,
        "sparsity_table": sparsity_table,
        "goals_low_bucket_composition": goals_composition,
        "prior_assists_points_composition": prior_composition,
        "policy_decision": policy_decision,
        "policy_version_before": "prop_decision_policy_v1",
        "policy_version_after": "prop_decision_policy_v2",
    }
    return out


if __name__ == "__main__":
    out = run_all()
    out_path = REPO_ROOT / "research" / "sparse_prop_gating_review_results.json"
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, sort_keys=True, default=str)
    print(f"wrote {out_path}")
