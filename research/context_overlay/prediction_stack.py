"""
Part 3/4/7/8 (Preseason Master Consolidation): the canonical SHADOW
probability stack for context-eligible props (Goals 1+, Points 1+).

Documented pipeline (Part 3), enforced by construction, never bypassed:

    RAW FROZEN MARGINAL
    -> CONTEXT OVERLAY (only if COLD_AND_TOI_DECLINE; identity otherwise)
    -> LOGICAL COHERENCE LAYER (P(Point) >= P(Goal), non-destructive)
    -> CONSERVATIVE PROBABILITY (documented, NOT operationalized -- Part 27)

Every stage is PRESERVED, never overwritten (Part 4): a caller gets back
raw_probability, context_adjusted_probability, pre_coherence_probability,
coherent_probability, and conservative_probability (currently always
None -- see the module docstring in CONTEXT_STATE_PROBABILITY_OVERLAY_
REPORT.md Section AE for why) in one dict, side by side.

This module NEVER imports or calls research.player_props.decision_policy
and NEVER produces a BET/WATCH/PASS/WAIT decision -- Part 8/9/41: this is
SHADOW_VALIDATED probability plumbing only. A future, separate policy-
integration slice is what would wire this into decision_policy, and that
slice would be the one to add a shadow-vs-live decision comparison
(Part 10/11), not this module.

Reuses the frozen overlay winner/parameters and the frozen context-state
cutoffs exactly as recorded in research/context_overlay_results.json --
never recomputes thresholds from a corpus at call time (Part 6: hash-pin
the state builder used by overlays, no silent drift).
"""
from __future__ import annotations

import json
from pathlib import Path

from research.context_overlay.registry import RESULTS_PATH as OVERLAY_RESULTS_PATH
from research.player_context_state import context_state as cs
from research.player_context_state import marginal_provenance as pcs_mp
from research.player_goals import features as gf
from research.player_points import features as ptf
from research.run_context_overlay_model import _apply_fn_for

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

FEATURE_MODULE = {"goals": gf, "points": ptf}
MIN_HISTORY = 10


class ShadowContextStack:
    """One shared, reusable object holding the frozen marginal engines,
    the frozen overlay winner/parameters, and the frozen context-state
    cutoffs. Build once per process (expensive: loads five corpora),
    reuse for every prediction."""

    def __init__(self):
        self.ctx = pcs_mp.ContextMarginalContext()
        with open(OVERLAY_RESULTS_PATH) as f:
            overlay_results = json.load(f)
        self._apply_fns: dict[str, object] = {}
        self._cutoffs: dict[str, tuple[float, float]] = {}
        for prop in ("goals", "points"):
            block = overlay_results["props"][prop]
            if block["status"] != "FITTED":
                continue
            self._apply_fns[prop] = _apply_fn_for(block["winner"], block["winner_params"])
            self._cutoffs[prop] = (block["cold_cutoff"], block["toi_decline_cutoff"])

    def _stage_for(self, prop: str, player_id: str, team: str, opponent: str,
                    game_date: str, season: int) -> dict | None:
        engine = getattr(self.ctx, prop)
        history = engine.index.history_as_of(player_id, game_date)
        if len(history) < MIN_HISTORY:
            return None
        features_module = FEATURE_MODULE[prop]
        baseline_rate = features_module.rolling_mean(history, prop, 20)
        recent_rate = features_module.rolling_mean(history, prop, 5)
        baseline_toi = features_module.rolling_mean(history, "icetime_seconds", 20)
        recent_toi = features_module.rolling_mean(history, "icetime_seconds", 10)
        form_ratio = cs.form_log_ratio(recent_rate, baseline_rate)
        toi_ratio = cs.toi_log_ratio(recent_toi, baseline_toi)

        pred = self.ctx.predict(prop, player_id, team, opponent, game_date, season)
        if pred is None or pred["probs"].get(1) is None:
            return None
        raw_p = pred["probs"][1]

        eligible = False
        if prop in self._cutoffs:
            cold_cutoff, toi_decline_cutoff = self._cutoffs[prop]
            eligible = (form_ratio is not None and form_ratio <= cold_cutoff and
                        toi_ratio is not None and toi_ratio <= toi_decline_cutoff)
        context_state = "COLD_AND_TOI_DECLINE" if eligible else "NOT_ELIGIBLE"

        if eligible and prop in self._apply_fns:
            adjusted_p = self._apply_fns[prop](raw_p)
            overlay_tag = "SHADOW_VALIDATED"
        else:
            adjusted_p = raw_p  # Part 4: identity when no overlay applies
            overlay_tag = "NOT_APPLICABLE"

        return {
            "prop": prop, "player_id": player_id, "context_state": context_state,
            "raw_probability": raw_p,
            "context_adjusted_probability": adjusted_p,
            "pre_coherence_probability": adjusted_p,
            "coherent_probability": adjusted_p,  # overwritten by _apply_coherence if paired
            "conservative_probability": None,  # Part 27: documented, NOT operationalized
            "coherence_applied": False,
            "overlay_status": overlay_tag,
            "mu": pred.get("mu"),
        }

    @staticmethod
    def _apply_coherence(goal_stage: dict | None, point_stage: dict | None) -> None:
        """Part 7: P(Goal>=1) <= P(Point>=1) always (GOAL_1_PLUS implies
        POINT_1_PLUS). Non-destructive: raises the Point side up to match
        the Goal side only when needed, never edits the Goal side, and
        preserves pre_coherence_probability on both for auditability."""
        if goal_stage is None or point_stage is None:
            return
        g_adj = goal_stage["context_adjusted_probability"]
        p_adj = point_stage["context_adjusted_probability"]
        if g_adj > p_adj:
            point_stage["coherent_probability"] = g_adj
            point_stage["coherence_applied"] = True
        else:
            point_stage["coherent_probability"] = p_adj
        goal_stage["coherent_probability"] = g_adj

    def predict(self, player_id: str, team: str, opponent: str, game_date: str, season: int) -> dict:
        """Returns {"goals": stage_dict | None, "points": stage_dict | None}.
        Applies the Part 7 coherence pass across the pair whenever both
        sides are available."""
        goal_stage = self._stage_for("goals", player_id, team, opponent, game_date, season)
        point_stage = self._stage_for("points", player_id, team, opponent, game_date, season)
        self._apply_coherence(goal_stage, point_stage)
        return {"goals": goal_stage, "points": point_stage}
