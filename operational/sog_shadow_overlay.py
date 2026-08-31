"""
Part 14-17: the SOG special-teams role SHADOW overlay -- a SEPARATE
probability path alongside (never replacing) the frozen production SOG
model. Reuses the FROZEN, versioned coefficients fit and validated in
the historical research sprint (research/special_teams_role_overlay_sog_results.json,
architecture "C": absolute role + direction-separated transition, both
certainty-shrunk) and the exact same math functions
(research.special_teams_role_overlay.core.adjusted_mu / decay_fn_for_name /
role_certainty / adjusted_threshold_probs) -- never refit here, never a
second implementation of the overlay math.

Only SOG 1+/2+/3+ are SHADOW_VALIDATED (Part 15) -- probabilities for
4+/5+/6+ are still computed (for display completeness) but explicitly
tagged NOT VALIDATED so nothing downstream can present them as
research-backed.
"""
from __future__ import annotations

import json
from pathlib import Path

from research.special_teams_role_overlay import core as ov_core

REPO_ROOT = Path(__file__).resolve().parent.parent
OVERLAY_RESULTS_PATH = REPO_ROOT / "research" / "special_teams_role_overlay_sog_results.json"

VALIDATED_THRESHOLDS = (1, 2, 3)
ALL_THRESHOLDS = (1, 2, 3, 4, 5, 6)
OVERLAY_VERSION = "sog_pp_role_overlay_v1_2026"  # bump if the frozen coefficients below ever change


class OverlayCoefficientsUnavailable(Exception):
    pass


def load_frozen_coefficients(path: Path = OVERLAY_RESULTS_PATH) -> dict:
    """Loads the FROZEN beta_role / beta_transition_positive / negative
    coefficients exactly as fit and reported in
    SPECIAL_TEAMS_ROLE_OVERLAY_VALIDATION_REPORT.md -- never re-fit by
    this module. Raises rather than fabricating defaults if the results
    file is missing."""
    if not path.exists():
        raise OverlayCoefficientsUnavailable(f"{path} not found -- run the historical validation sprint first")
    with open(path) as f:
        results = json.load(f)
    return {
        "beta_role": results["beta_role"],
        "beta_transition_positive": results["transition_fit_positive"]["beta_transition"],
        "beta_transition_negative": results["transition_fit_negative"]["beta_transition"],
        "decay_name_positive": results["transition_fit_positive"]["decay_name"],
        "decay_name_negative": results["transition_fit_negative"]["decay_name"],
    }


def compute_shadow_sog(mu_frozen: float, alpha: float | None, role_state: dict,
                        coefficients: dict) -> dict:
    """`role_state`: the dict returned by
    operational.special_teams_roles_live.compute_pp_role_state (must
    carry recent_role, n_recent, n_baseline, and -- when a transition is
    in effect -- games-since-onset/direction; a caller with no
    transition info should pass those as None, which cleanly disables
    the transition term).

    Returns {"shadow_mu", "shadow_probs" (all 6 thresholds, but only
    1/2/3 are SHADOW_VALIDATED -- see `validated_thresholds` in the
    return dict), "certainty", "role_used", "overlay_version"}."""
    role = role_state.get("recent_role")
    n_recent = role_state.get("n_recent") or 0
    n_baseline = role_state.get("n_baseline") or 0
    certainty = ov_core.role_certainty(n_recent, n_baseline)

    beta_role = coefficients["beta_role"].get(role, 0.0) if role else 0.0

    games_since = role_state.get("games_since_onset")
    direction = role_state.get("direction")
    beta_transition, decay_val = 0.0, 0.0
    if games_since is not None and direction is not None:
        if direction == 1:
            beta_transition = coefficients["beta_transition_positive"]
            decay_fn = ov_core.decay_fn_for_name(coefficients["decay_name_positive"])
        else:
            beta_transition = coefficients["beta_transition_negative"]
            decay_fn = ov_core.decay_fn_for_name(coefficients["decay_name_negative"])
        decay_val = decay_fn(games_since)

    shadow_mu = ov_core.adjusted_mu(mu_frozen, beta_role, beta_transition, decay_val, direction, certainty)
    shadow_probs = ov_core.adjusted_threshold_probs(shadow_mu, alpha, ALL_THRESHOLDS)

    return {
        "shadow_mu": shadow_mu, "shadow_probs": shadow_probs, "certainty": certainty,
        "role_used": role, "beta_role_applied": beta_role * certainty,
        "beta_transition_applied": beta_transition * decay_val * certainty,
        "overlay_version": OVERLAY_VERSION, "validated_thresholds": VALIDATED_THRESHOLDS,
    }
