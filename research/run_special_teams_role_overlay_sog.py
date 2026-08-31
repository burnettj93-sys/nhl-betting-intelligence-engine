"""
Special-teams role-overlay validation, Player SOG (primary target).
Builds three challengers against the REAL, UNMODIFIED frozen SOG marginal
(research.player_context_state.marginal_provenance.ContextMarginalContext
-- never re-fit):

  A. ABSOLUTE PP ROLE ONLY   (beta_role[PP1]/beta_role[PP2])
  B. ROLE TRANSITION ONLY    (beta_transition * decay(games_since_onset))
  C. BOTH, JOINTLY

Fit on TUNING data (2022-23 + 2023-24) only. Evaluated OUT OF SAMPLE on
2024-25 and 2025-26 SEPARATELY (both must show real improvement to be
anything more than PARTIAL/REJECTED -- Part 28's acceptance bar), via
per-threshold Brier/log-loss/calibration/MAE plus game/date/player
-clustered bootstrap. Post-overlay residuals are recomputed by role state
(Part 31/32) to check whether the systematic pattern actually goes away,
not just whether one aggregate metric improves.

Run manually (~2-3 minutes):
    python3 -m research.run_special_teams_role_overlay_sog
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

RESULTS_PATH = REPO_ROOT / "research" / "special_teams_role_overlay_sog_results.json"
SOG_THRESHOLDS = (1, 2, 3, 4, 5, 6)
SOG_ALPHA_RESULTS_PATH = REPO_ROOT / "research" / "player_sog_results.json"


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in open(path)]


def _threshold_prob_fn(mu, alpha, t):
    return cm.threshold_probabilities(mu, alpha, thresholds=(t,))[t]


def build_dataset() -> list[dict]:
    transitions = _load_jsonl(REPO_ROOT / "research" / "special_teams_role_transitions_table.jsonl")
    by_player = defaultdict(list)
    for r in transitions:
        by_player[r["player_id"]].append(r)
    for pid in by_player:
        by_player[pid].sort(key=lambda r: r["game_date"])
    core.add_games_since_onset(by_player, "pp_state", "pp_games_since_onset", "pp_direction")

    sog_rows = _load_jsonl(REPO_ROOT / "research" / "player_sog" / "player_game_sog.jsonl")
    sog_actual = {(r["player_id"], r["game_id"]): r["sog"] for r in sog_rows}

    with open(SOG_ALPHA_RESULTS_PATH) as f:
        sog_results = json.load(f)
    alpha = sog_results["negbinom_alpha_fitted"] if sog_results["negbinom_alpha_fitted"] > 0.01 else None

    ctx = ContextMarginalContext()
    dataset = []
    for pid, games in by_player.items():
        for g in games:
            key = (pid, g["game_id"])
            actual = sog_actual.get(key)
            if actual is None or g.get("pp_recent_role") is None:
                continue
            pred = ctx.predict("sog", pid, g["team"], g.get("opponent"), g["game_date"], g.get("season"))
            if pred is None:
                continue
            dataset.append({
                "player_id": pid, "game_id": g["game_id"], "game_date": g["game_date"], "season": g.get("season"),
                "role": g["pp_recent_role"], "state": g["pp_state"],
                "games_since_onset": g.get("pp_games_since_onset"), "direction": g.get("pp_direction"),
                "n_recent": g.get("pp_n_recent"), "n_baseline": g.get("pp_n_baseline"),
                "mu_frozen": pred["mu"], "actual": actual,
            })
    return dataset, alpha


def _split(dataset: list[dict]) -> dict:
    tuning = [r for r in dataset if r["season"] in ft.TUNING_SEASONS]
    evals = {name: [r for r in dataset if r["season"] == season] for name, season in ft.EVAL_SEASONS.items()}
    return {"tuning": tuning, **evals}


def _apply_challenger(rows: list[dict], beta_role: dict, beta_pos: float, decay_fn_pos,
                       beta_neg: float, decay_fn_neg, use_role: bool, use_transition: bool) -> list[float]:
    """Direction-separated transition term (see run()'s own note on why
    a single shared beta_transition across mixed directions is wrong):
    a positive-direction row uses beta_pos/decay_fn_pos, a negative-
    direction row uses beta_neg/decay_fn_neg -- never combined."""
    import math as _math
    mus = []
    for r in rows:
        role = r["role"] if use_role else None
        cert = core.role_certainty(r["n_recent"] or 0, r["n_baseline"] or 0)
        log_mu = _math.log(max(r["mu_frozen"], 1e-9))
        if use_role:
            log_mu += beta_role.get(role, 0.0) * cert
        if use_transition and r["games_since_onset"] is not None:
            if r["direction"] == 1:
                log_mu += beta_pos * decay_fn_pos(r["games_since_onset"]) * cert
            elif r["direction"] == -1:
                log_mu += beta_neg * decay_fn_neg(r["games_since_onset"]) * cert
        mus.append(_math.exp(log_mu))
    return mus


def _residuals_by_state(rows: list[dict], mus: list[float]) -> dict:
    by_state = defaultdict(list)
    for r, mu in zip(rows, mus):
        by_state[r["state"]].append(r["actual"] - mu)
    return {s: {"n": len(v), "mean": statistics.fmean(v)} for s, v in by_state.items() if v}


def run() -> dict:
    dataset, alpha = build_dataset()
    splits = _split(dataset)
    tuning = splits["tuning"]

    beta_role = ft.fit_beta_role(tuning, "role", "mu_frozen", "actual")

    # Fit the promotion/addition (direction=+1) and demotion/removal
    # (direction=-1) transition effects SEPARATELY (Part 17: "Do not
    # assume symmetric promotion/demotion effects") -- a real bug found
    # during this sprint: fitting one shared beta_transition across both
    # signs mixed together let the two real, opposite-signed effects
    # partially cancel in the aggregate sum, producing a small,
    # wrongly-signed combined estimate that reflected neither direction's
    # real effect.
    tuning_pos = [r for r in tuning if r.get("direction") == 1]
    tuning_neg = [r for r in tuning if r.get("direction") == -1]
    transition_fit_pos = ft.fit_beta_transition(tuning_pos, "role", "mu_frozen", "actual", beta_role,
                                                 "games_since_onset", "direction")
    transition_fit_neg = ft.fit_beta_transition(tuning_neg, "role", "mu_frozen", "actual", beta_role,
                                                 "games_since_onset", "direction")
    decay_fn_pos = core.decay_fn_for_name(transition_fit_pos["decay_name"])
    decay_fn_neg = core.decay_fn_for_name(transition_fit_neg["decay_name"])
    beta_transition_pos = transition_fit_pos["beta_transition"]
    beta_transition_neg = transition_fit_neg["beta_transition"]

    challengers = {
        "A_absolute_role": {"use_role": True, "use_transition": False},
        "B_transition_only": {"use_role": False, "use_transition": True},
        "C_both": {"use_role": True, "use_transition": True},
    }

    results = {
        "beta_role": beta_role, "transition_fit_positive": transition_fit_pos,
        "transition_fit_negative": transition_fit_neg,
        "n_tuning_rows": len(tuning), "eval_seasons": {},
    }

    for eval_name in ft.EVAL_SEASONS:
        eval_rows = splits[eval_name]
        frozen_mus = [r["mu_frozen"] for r in eval_rows]
        actuals = [r["actual"] for r in eval_rows]
        frozen_eval = ev.evaluate_thresholds(frozen_mus, actuals, alpha, SOG_THRESHOLDS, _threshold_prob_fn)

        season_result = {"n": len(eval_rows), "frozen": {
            t: {"brier": frozen_eval["by_threshold"][t]["brier"], "log_loss": frozen_eval["by_threshold"][t]["log_loss"]}
            for t in SOG_THRESHOLDS}, "frozen_mae": frozen_eval["mae_count"], "challengers": {}}

        for cname, opts in challengers.items():
            cand_mus = _apply_challenger(eval_rows, beta_role, beta_transition_pos, decay_fn_pos,
                                          beta_transition_neg, decay_fn_neg,
                                          opts["use_role"], opts["use_transition"])
            cand_eval = ev.evaluate_thresholds(cand_mus, actuals, alpha, SOG_THRESHOLDS, _threshold_prob_fn)

            per_threshold_compare = {}
            bootstrap_by_threshold = {}
            for t in SOG_THRESHOLDS:
                baseline_scores = [ev.brier(_threshold_prob_fn(mu, alpha, t), 1.0 if y >= t else 0.0)
                                    for mu, y in zip(frozen_mus, actuals)]
                candidate_scores = [ev.brier(_threshold_prob_fn(mu, alpha, t), 1.0 if y >= t else 0.0)
                                     for mu, y in zip(cand_mus, actuals)]
                per_threshold_compare[t] = {
                    "frozen_brier": frozen_eval["by_threshold"][t]["brier"],
                    "challenger_brier": cand_eval["by_threshold"][t]["brier"],
                    "frozen_log_loss": frozen_eval["by_threshold"][t]["log_loss"],
                    "challenger_log_loss": cand_eval["by_threshold"][t]["log_loss"],
                }
                if t in (1, 2, 3):  # bootstrap only the headline thresholds -- keep runtime bounded
                    bootstrap_by_threshold[t] = ev.game_clustered_bootstrap(
                        eval_rows, baseline_scores, candidate_scores, n_resamples=500)

            season_result["challengers"][cname] = {
                "mae_count": cand_eval["mae_count"], "per_threshold": per_threshold_compare,
                "game_clustered_bootstrap_brier_delta": bootstrap_by_threshold,
                "post_overlay_residual_by_state": _residuals_by_state(eval_rows, cand_mus),
            }

        season_result["frozen_residual_by_state"] = _residuals_by_state(eval_rows, frozen_mus)
        results["eval_seasons"][eval_name] = season_result

    return results


if __name__ == "__main__":
    result = run()
    with open(RESULTS_PATH, "w") as f:
        json.dump(result, f, indent=2, sort_keys=True, default=str)
    print(json.dumps({k: v for k, v in result.items() if k != "eval_seasons"}, indent=2, sort_keys=True))
    for season, r in result["eval_seasons"].items():
        print(f"\n=== {season} (n={r['n']}) ===")
        print("frozen MAE:", r["frozen_mae"])
        for cname, cr in r["challengers"].items():
            print(f"  {cname}: MAE={cr['mae_count']:.4f}")
            for t in (1, 2, 3):
                pt = cr["per_threshold"][t]
                bs = cr["game_clustered_bootstrap_brier_delta"].get(t, {})
                print(f"    t={t}: frozen_brier={pt['frozen_brier']:.5f} challenger_brier={pt['challenger_brier']:.5f} "
                      f"bootstrap_frac_improved={bs.get('frac_improved')}")
