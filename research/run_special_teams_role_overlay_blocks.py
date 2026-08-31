"""
Special-teams role-overlay validation, Blocked Shots (secondary target,
PK REMOVAL ONLY -- Part 33 explicitly says not to waste time on a
generic PK promotion overlay, since the prior sprint found ~zero
residual effect there). Same architecture as the SOG script (log-mu
offset, decay-weighted, fit on tuning, evaluated OOS on both seasons
separately) but with a single beta_removal term instead of a full
role+transition model.

Run manually:
    python3 -m research.run_special_teams_role_overlay_blocks
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.period_event_timing import special_teams_roles as sr
from research.player_context_state.marginal_provenance import ContextMarginalContext
from research.player_sog import count_models as cm
from research.special_teams_role_overlay import core, evaluate as ev, fit as ft

RESULTS_PATH = REPO_ROOT / "research" / "special_teams_role_overlay_blocks_results.json"
BLOCKS_THRESHOLDS = (1, 2, 3)


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in open(path)]


def _threshold_prob_fn(mu, alpha, t):
    return cm.threshold_probabilities(mu, alpha, thresholds=(t,))[t]


def build_dataset():
    transitions = _load_jsonl(REPO_ROOT / "research" / "special_teams_role_transitions_table.jsonl")
    by_player = defaultdict(list)
    for r in transitions:
        by_player[r["player_id"]].append(r)
    for pid in by_player:
        by_player[pid].sort(key=lambda r: r["game_date"])
    core.add_games_since_specific_state(by_player, "pk_state", "REMOVED_FROM_PK", "pk_games_since_removal")

    blocks_rows = _load_jsonl(REPO_ROOT / "research" / "player_blocks" / "player_game_blocks.jsonl")
    blocks_actual = {(r["player_id"], r["game_id"]): r["blocks"] for r in blocks_rows}

    with open(REPO_ROOT / "research" / "player_blocks_results.json") as f:
        blocks_results = json.load(f)
    alpha = (blocks_results["negbinom_alpha_fitted"]
             if blocks_results.get("negbinom_alpha_fitted", 0) > 0.01 else None)

    ctx = ContextMarginalContext()
    dataset = []
    for pid, games in by_player.items():
        for g in games:
            key = (pid, g["game_id"])
            actual = blocks_actual.get(key)
            if actual is None or g.get("pk_state") is None:
                continue
            pred = ctx.predict("blocks", pid, g["team"], g.get("opponent"), g["game_date"], g.get("season"))
            if pred is None:
                continue
            dataset.append({
                "player_id": pid, "game_id": g["game_id"], "game_date": g["game_date"], "season": g.get("season"),
                "state": g["pk_state"], "games_since_onset": g.get("pk_games_since_removal"),
                "direction": -1 if g.get("pk_games_since_removal") is not None else None,
                "mu_frozen": pred["mu"], "actual": actual,
            })
    return dataset, alpha


def run() -> dict:
    dataset, alpha = build_dataset()
    tuning = [r for r in dataset if r["season"] in ft.TUNING_SEASONS]

    # Fit a single beta_removal on tuning data: only rows currently within
    # the decay window of a NEGATIVE PK transition (removal/demotion)
    # contribute -- Part 33's narrow scope, never a positive-side term.
    candidates = []
    for r in tuning:
        if r["direction"] != -1 or r["games_since_onset"] is None:
            continue
        candidates.append(r)

    # Reuses fit.fit_beta_transition's own stable aggregate-ratio method
    # (Part 36: categorical removal vs continuous PK loss both reduce to
    # this same single-direction fit) -- role_field is unused here (no
    # PP role applies to a PK-only overlay), so beta_role is empty.
    fit_result = ft.fit_beta_transition(candidates, "state", "mu_frozen", "actual", {},
                                         "games_since_onset", "direction")
    best = {"decay_name": fit_result["decay_name"], "beta_removal": fit_result["beta_transition"],
            "n_active_rows": fit_result["n_active_rows"], "declines": fit_result.get("declines")}
    decay_fn = core.decay_fn_for_name(best["decay_name"])

    results = {"fit": best, "n_tuning_candidates": len(candidates), "eval_seasons": {}}

    for eval_name, season in ft.EVAL_SEASONS.items():
        eval_rows = [r for r in dataset if r["season"] == season]
        frozen_mus = [r["mu_frozen"] for r in eval_rows]
        actuals = [r["actual"] for r in eval_rows]
        cand_mus = []
        for r in eval_rows:
            active = r["direction"] == -1 and r["games_since_onset"] is not None
            d = decay_fn(r["games_since_onset"]) if active else 0.0
            mu_adj = core.adjusted_mu(r["mu_frozen"], 0.0, best["beta_removal"], d, -1 if active else 0)
            cand_mus.append(mu_adj)

        frozen_eval = ev.evaluate_thresholds(frozen_mus, actuals, alpha, BLOCKS_THRESHOLDS, _threshold_prob_fn)
        cand_eval = ev.evaluate_thresholds(cand_mus, actuals, alpha, BLOCKS_THRESHOLDS, _threshold_prob_fn)

        per_threshold = {}
        bootstrap = {}
        for t in BLOCKS_THRESHOLDS:
            per_threshold[t] = {
                "frozen_brier": frozen_eval["by_threshold"][t]["brier"],
                "challenger_brier": cand_eval["by_threshold"][t]["brier"],
                "frozen_log_loss": frozen_eval["by_threshold"][t]["log_loss"],
                "challenger_log_loss": cand_eval["by_threshold"][t]["log_loss"],
            }
            baseline_scores = [ev.brier(_threshold_prob_fn(mu, alpha, t), 1.0 if y >= t else 0.0)
                                for mu, y in zip(frozen_mus, actuals)]
            candidate_scores = [ev.brier(_threshold_prob_fn(mu, alpha, t), 1.0 if y >= t else 0.0)
                                 for mu, y in zip(cand_mus, actuals)]
            bootstrap[t] = ev.game_clustered_bootstrap(eval_rows, baseline_scores, candidate_scores, n_resamples=500)

        by_state_frozen = defaultdict(list)
        by_state_post = defaultdict(list)
        for r, fmu, cmu in zip(eval_rows, frozen_mus, cand_mus):
            by_state_frozen[r["state"]].append(r["actual"] - fmu)
            by_state_post[r["state"]].append(r["actual"] - cmu)

        results["eval_seasons"][eval_name] = {
            "n": len(eval_rows), "frozen_mae": frozen_eval["mae_count"], "challenger_mae": cand_eval["mae_count"],
            "per_threshold": per_threshold, "game_clustered_bootstrap_brier_delta": bootstrap,
            "frozen_residual_by_state": {s: {"n": len(v), "mean": statistics.fmean(v)} for s, v in by_state_frozen.items()},
            "post_overlay_residual_by_state": {s: {"n": len(v), "mean": statistics.fmean(v)} for s, v in by_state_post.items()},
        }

    return results


if __name__ == "__main__":
    result = run()
    with open(RESULTS_PATH, "w") as f:
        json.dump(result, f, indent=2, sort_keys=True, default=str)
    print(json.dumps({k: v for k, v in result.items() if k != "eval_seasons"}, indent=2, sort_keys=True))
    for season, r in result["eval_seasons"].items():
        print(f"\n=== {season} (n={r['n']}) ===")
        print("frozen MAE:", r["frozen_mae"], "challenger MAE:", r["challenger_mae"])
        for t in BLOCKS_THRESHOLDS:
            pt = r["per_threshold"][t]
            bs = r["game_clustered_bootstrap_brier_delta"][t]
            print(f"  t={t}: frozen={pt['frozen_brier']:.5f} challenger={pt['challenger_brier']:.5f} "
                  f"frac_improved={bs['frac_improved']}")
