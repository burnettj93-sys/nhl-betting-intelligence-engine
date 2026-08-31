"""
Special-teams role-overlay validation, EXPLORATORY scoring props (Parts
40-43): ONE minimal challenger each for Goals, Assists, Points --
absolute PP role only, no transition term, no rescue attempts if it
doesn't clearly help. High bar, small investment, matching the task's
explicit "minimal challenger only" / "REJECT ... do not keep
redesigning" instruction.

Goals and Assists have a real frozen count model (mu) -- same log-mu
role offset as the SOG/Blocks scripts. Points has NO count model
("empirical baseline remains champion" -- a real, pre-existing fact);
its challenger instead adjusts the frozen P(points>=1) on the LOGIT
scale, the natural probability-only analog of a log-mu offset.

Run manually:
    python3 -m research.run_special_teams_role_overlay_scoring
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.player_context_state.marginal_provenance import ContextMarginalContext
from research.player_sog import count_models as cm
from research.special_teams_role_overlay import evaluate as ev, fit as ft

RESULTS_PATH = REPO_ROOT / "research" / "special_teams_role_overlay_scoring_results.json"


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in open(path)]


def _threshold_prob_fn(mu, alpha, t):
    return cm.threshold_probabilities(mu, alpha, thresholds=(t,))[t]


def _logit(p, eps=1e-6):
    p = min(max(p, eps), 1 - eps)
    return math.log(p / (1 - p))


def _sigmoid(x):
    return 1.0 / (1.0 + math.exp(-x))


def _build_role_labeled_rows():
    transitions = _load_jsonl(REPO_ROOT / "research" / "special_teams_role_transitions_table.jsonl")
    return {(r["player_id"], r["game_id"]): r for r in transitions}


def run_count_prop(prop: str, actual_field: str, results_file: str, thresholds: tuple[int, ...]) -> dict:
    role_by_key = _build_role_labeled_rows()
    rows = _load_jsonl(REPO_ROOT / "research" / f"player_{prop}" / f"player_game_{prop}.jsonl")
    actual_idx = {(r["player_id"], r["game_id"]): r[actual_field] for r in rows}

    with open(REPO_ROOT / "research" / results_file) as f:
        rr = json.load(f)
    # Matches each MarginalContext class's own real alpha selection exactly
    # (GoalsMarginal uses alpha_e specifically, not the bare "alpha" key
    # that also happens to exist in the same results file).
    alpha_key = "alpha_e" if prop == "goals" else "alpha"
    alpha = rr.get(alpha_key)
    alpha = alpha if alpha and alpha > 0.01 else None

    ctx = ContextMarginalContext()
    dataset = []
    for key, role_row in role_by_key.items():
        pid, gid = key
        actual = actual_idx.get(key)
        role = role_row.get("pp_recent_role")
        if actual is None or role is None:
            continue
        pred = ctx.predict(prop, pid, role_row["team"], role_row.get("opponent"),
                            role_row["game_date"], role_row.get("season"))
        if pred is None or "mu" not in pred:
            continue
        dataset.append({"player_id": pid, "game_id": gid, "game_date": role_row["game_date"],
                         "season": role_row.get("season"), "role": role, "mu_frozen": pred["mu"], "actual": actual})

    tuning = [r for r in dataset if r["season"] in ft.TUNING_SEASONS]
    beta_role = ft.fit_beta_role(tuning, "role", "mu_frozen", "actual")

    out = {"prop": prop, "beta_role": beta_role, "n_tuning": len(tuning), "eval_seasons": {}}
    for eval_name, season in ft.EVAL_SEASONS.items():
        eval_rows = [r for r in dataset if r["season"] == season]
        frozen_mus = [r["mu_frozen"] for r in eval_rows]
        actuals = [r["actual"] for r in eval_rows]
        cand_mus = [r["mu_frozen"] * math.exp(beta_role.get(r["role"], 0.0)) for r in eval_rows]

        per_threshold = {}
        for t in thresholds:
            baseline_scores = [ev.brier(_threshold_prob_fn(mu, alpha, t), 1.0 if y >= t else 0.0)
                                for mu, y in zip(frozen_mus, actuals)]
            candidate_scores = [ev.brier(_threshold_prob_fn(mu, alpha, t), 1.0 if y >= t else 0.0)
                                 for mu, y in zip(cand_mus, actuals)]
            boot = ev.game_clustered_bootstrap(eval_rows, baseline_scores, candidate_scores, n_resamples=400)
            per_threshold[t] = {"frozen_brier": statistics.fmean(baseline_scores),
                                 "challenger_brier": statistics.fmean(candidate_scores),
                                 "frac_improved": boot["frac_improved"]}
        out["eval_seasons"][eval_name] = {"n": len(eval_rows), "per_threshold": per_threshold}
    return out


def run_points() -> dict:
    role_by_key = _build_role_labeled_rows()
    rows = _load_jsonl(REPO_ROOT / "research" / "player_points" / "player_game_points.jsonl")
    actual_idx = {(r["player_id"], r["game_id"]): r["points"] for r in rows}

    ctx = ContextMarginalContext()
    dataset = []
    for key, role_row in role_by_key.items():
        pid, gid = key
        actual = actual_idx.get(key)
        role = role_row.get("pp_recent_role")
        if actual is None or role is None:
            continue
        pred = ctx.predict("points", pid, role_row["team"], role_row.get("opponent"),
                            role_row["game_date"], role_row.get("season"))
        if pred is None:
            continue
        p1 = pred["probs"].get(1)
        if p1 is None:
            continue
        dataset.append({"player_id": pid, "game_id": gid, "game_date": role_row["game_date"],
                         "season": role_row.get("season"), "role": role, "p_frozen": p1,
                         "outcome": 1.0 if actual >= 1 else 0.0})

    tuning = [r for r in dataset if r["season"] in ft.TUNING_SEASONS]
    # Logit-scale role offset: beta[role] = mean(logit(outcome-smoothed)) - mean(logit(p_frozen))
    # approximated via the standard logistic-offset MLE closed form for a
    # single categorical factor: beta = logit(actual_rate) - mean(logit(p_frozen)).
    beta_role = {}
    by_role = defaultdict(list)
    for r in tuning:
        by_role[r["role"]].append(r)
    for role, rs in by_role.items():
        if role == "NONE" or len(rs) < 30:
            continue
        actual_rate = statistics.fmean(r["outcome"] for r in rs)
        mean_logit_frozen = statistics.fmean(_logit(r["p_frozen"]) for r in rs)
        beta_role[role] = _logit(actual_rate) - mean_logit_frozen

    out = {"beta_role": beta_role, "n_tuning": len(tuning), "eval_seasons": {}}
    for eval_name, season in ft.EVAL_SEASONS.items():
        eval_rows = [r for r in dataset if r["season"] == season]
        p_frozen = [r["p_frozen"] for r in eval_rows]
        outcomes = [r["outcome"] for r in eval_rows]
        p_cand = [_sigmoid(_logit(r["p_frozen"]) + beta_role.get(r["role"], 0.0)) for r in eval_rows]

        baseline_scores = [ev.brier(p, y) for p, y in zip(p_frozen, outcomes)]
        candidate_scores = [ev.brier(p, y) for p, y in zip(p_cand, outcomes)]
        boot = ev.game_clustered_bootstrap(eval_rows, baseline_scores, candidate_scores, n_resamples=400)
        out["eval_seasons"][eval_name] = {
            "n": len(eval_rows), "frozen_brier": statistics.fmean(baseline_scores),
            "challenger_brier": statistics.fmean(candidate_scores), "frac_improved": boot["frac_improved"],
        }
    return out


if __name__ == "__main__":
    goals = run_count_prop("goals", "goals", "player_goals_results.json", (1, 2))
    assists = run_count_prop("assists", "assists", "player_assists_results.json", (1, 2, 3))
    points = run_points()
    result = {"goals": goals, "assists": assists, "points": points}
    with open(RESULTS_PATH, "w") as f:
        json.dump(result, f, indent=2, sort_keys=True, default=str)
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
