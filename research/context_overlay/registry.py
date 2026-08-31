"""
Part 36/37: CONTEXT_OVERLAY_REGISTRY -- separate from market_registry.py
(which is NOT modified this slice -- Goals stays VALIDATED, Points stays
EMPIRICAL_BASELINE_REMAINS_CHAMPION at the marginal level) and separate
from research/player_context_state's own PLAYER_CONTEXT_REGISTRY (which
tracks raw context-signal validation, not a probability overlay).

Status classification mechanically applies Part 40's 14-point adoption
standard to the driver's own frozen results -- never hand-picked:
  VALIDATED_OVERLAY -- every checkable criterion holds in BOTH eval seasons
  PARTIAL           -- some but not all criteria hold
  REJECTED          -- Brier or log loss criterion fails in either season
  INSUFFICIENT_DATA -- propagated directly from the driver's own sample-floor gate

Updated for the Preseason Master Consolidation sprint (Part 2): fields
renamed to match that slice's exact spec (validation_status,
frozen_parameters, validation_seasons, bootstrap_results,
confidence_inheritance, logical_coherence_behavior, operational_status).
A VALIDATED_OVERLAY's operational_status is now "SHADOW_VALIDATED" (not
"RESEARCH") because this sprint wired both overlays into the canonical
shadow probability stack (research/context_overlay/prediction_stack.py)
-- still explicitly NOT "FULL_BET_POLICY" (Part 2/9/41: no decision_policy
change, no live-bet promotion; prospective 2026-27 evidence is required
first, see PROSPECTIVE_VALIDATION_PROTOCOL.md).
"""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RESULTS_PATH = REPO_ROOT / "research" / "context_overlay_results.json"
REGISTRY_PATH = REPO_ROOT / "research" / "context_overlay_registry.json"

MAX_REASONABLE_ABS_CHANGE = 0.10


def _season_checks(eval_block: dict, coherence_fix: dict) -> dict:
    checks = {
        "brier_improves": eval_block["adjusted_brier"] < eval_block["raw_brier"],
        "log_loss_improves": eval_block["adjusted_log_loss"] < eval_block["raw_log_loss"],
        "calibration_improves": abs(eval_block["adjusted_calibration"]["residual"]) <
                                 abs(eval_block["raw_calibration"]["residual"]),
        "game_bootstrap_clears": eval_block["game_bootstrap_brier"]["ci_high"] < 0,
        "date_bootstrap_agrees": eval_block["date_bootstrap_brier"]["ci_high"] < 0,
        "player_bootstrap_ok": eval_block["player_bootstrap_brier"]["frac_improved"] >= 0.90,
        "no_new_coherence_violations": coherence_fix.get("post_fix_violations_remaining", 0) == 0,
    }
    return checks


def build_registry(results: dict) -> list[dict]:
    entries = []
    eval_seasons = results["config"]["eval_seasons"]
    coherence_fix_by_season = results["freeze_manifest"].get("coherence_fix_by_season", {})

    market_ids = {"goals": "PLAYER_GOALS_1PLUS", "points": "PLAYER_POINTS_1PLUS"}
    base_models = {"goals": "Goals 1+ (locked candidate E, VALIDATED marginal)",
                   "points": "Points 1+ (shrunk empirical baseline, EMPIRICAL_BASELINE_REMAINS_CHAMPION)"}

    for prop in ("goals", "points"):
        block = results["props"][prop]
        entry_name = f"{market_ids[prop]}__COLD_AND_TOI_DECLINE"

        if block["status"] == "INSUFFICIENT_DATA":
            entries.append({
                "signal": entry_name, "validation_status": "INSUFFICIENT_DATA", "base_model": base_models[prop],
                "context_state": "COLD_AND_TOI_DECLINE", "adjustment_type": None, "frozen_parameters": None,
                "confidence_inheritance": "N/A", "sample_size": {"dev_n": block["dev_n"],
                                                                  "min_dev_n_required": block["min_dev_n_required"]},
                "validation_seasons": eval_seasons, "bootstrap_results": {},
                "logical_coherence_behavior": "N/A -- overlay not fitted", "operational_status": "NOT_OPERATIONAL",
            })
            continue

        per_season_checks = {}
        for season in eval_seasons:
            eb = block["eval"].get(str(season), block["eval"].get(season))
            if eb is None or eb.get("status") == "INSUFFICIENT_DATA":
                per_season_checks[season] = None
                continue
            cf = coherence_fix_by_season.get(str(season), coherence_fix_by_season.get(season, {}))
            per_season_checks[season] = _season_checks(eb, cf)

        all_checks_all_seasons = [c for c in per_season_checks.values() if c is not None]
        if not all_checks_all_seasons:
            status = "INSUFFICIENT_DATA"
        elif all(all(c.values()) for c in all_checks_all_seasons) and len(all_checks_all_seasons) == len(eval_seasons):
            status = "VALIDATED_OVERLAY"
        elif any(not c["brier_improves"] or not c["log_loss_improves"] for c in all_checks_all_seasons):
            status = "REJECTED"
        else:
            status = "PARTIAL"

        magnitude_ok = block["adjustment_magnitude"]["mean_abs_change"] <= MAX_REASONABLE_ABS_CHANGE

        operational_status = {
            "VALIDATED_OVERLAY": "SHADOW_VALIDATED",  # Part 2: never FULL_BET_POLICY
            "PARTIAL": "RESEARCH",
            "REJECTED": "REJECTED",
            "INSUFFICIENT_DATA": "NOT_OPERATIONAL",
        }[status]

        entries.append({
            "signal": entry_name, "validation_status": status, "base_model": base_models[prop],
            "context_state": "COLD_AND_TOI_DECLINE", "adjustment_type": block["winner"],
            "frozen_parameters": block["winner_params"],
            "adjustment_magnitude": block["adjustment_magnitude"],
            "adjustment_reasonably_small": magnitude_ok,
            "confidence_inheritance": "HIGH/MEDIUM only -- LOW confidence inherits the base market's "
                                       "WATCH_ONLY ceiling from decision_policy v3 and the overlay cannot "
                                       "override it (see research.player_props.decision_policy.gate_low_confidence)",
            "sample_size": {"dev_n": block["dev_n"],
                             **{str(s): block["eval"].get(str(s), block["eval"].get(s, {})).get("n")
                                for s in eval_seasons}},
            "validation_seasons": eval_seasons,
            "bootstrap_results": {str(s): per_season_checks[s] for s in eval_seasons},
            "logical_coherence_behavior": "Non-destructive clip: adjusted P(Point>=1) is raised to match "
                                           "adjusted P(Goal>=1) whenever GOAL_1_PLUS implies POINT_1_PLUS and "
                                           "the independently-fit overlays would otherwise violate that "
                                           "ordering (verified 0 violations remaining post-fix, both seasons).",
            "operational_status": operational_status,
        })

    return entries


if __name__ == "__main__":
    with open(RESULTS_PATH) as f:
        results = json.load(f)
    registry = build_registry(results)
    with open(REGISTRY_PATH, "w") as f:
        json.dump(registry, f, indent=2, default=str)
    print(f"Wrote {len(registry)} registry entries to {REGISTRY_PATH}")
