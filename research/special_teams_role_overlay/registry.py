"""
Part 56/57: challenger/overlay registry entries -- research bookkeeping
only. Never touches research.player_props.registry.REGISTRY (the real
production prop registry) or MODEL_REGISTRY (Part 58: no production
champion replacement this sprint). Statuses reflect the REAL, historical
2024-25/2025-26 out-of-sample evaluation in
research/special_teams_role_overlay_{sog,blocks,scoring}_results.json.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class OverlayRegistryEntry:
    overlay_id: str
    target_prop: str
    status: str  # VALIDATED_OVERLAY / PARTIAL / REJECTED / INSUFFICIENT_DATA
    architecture: str  # A_absolute_role / B_transition_only / C_both / D_none / narrow_removal
    summary: str
    thresholds_meeting_bar: tuple = ()
    recommended_operational_status: str = "RESEARCH"  # RESEARCH or SHADOW_VALIDATED, never higher this sprint


OVERLAY_REGISTRY = [
    OverlayRegistryEntry(
        overlay_id="PLAYER_SOG_PP_ROLE_OVERLAY", target_prop="SOG", status="PARTIAL",
        architecture="C_both",
        summary="Absolute PP role (PP1/PP2) plus a short (~2-game, exponential-shaped) direction-"
                "separated transition term improves Brier at thresholds 1+/2+/3+ in BOTH 2024-25 and "
                "2025-26, with strong game-clustered bootstrap support (mostly 0.9-1.0 frac_improved "
                "for the combined challenger). Thresholds 4+/5+/6+ do NOT clear the bar (mixed sign "
                "across seasons). Absolute magnitude is small (~0.05-0.15% relative Brier improvement).",
        thresholds_meeting_bar=(1, 2, 3),
        recommended_operational_status="SHADOW_VALIDATED",
    ),
    OverlayRegistryEntry(
        overlay_id="PLAYER_SOG_PP_TRANSITION_OVERLAY", target_prop="SOG", status="PARTIAL",
        architecture="B_transition_only",
        summary="Transition-only (no absolute role term) also improves 1+/2+/3+ in both seasons once "
                "correctly fit direction-by-direction, though slightly less consistently than the "
                "combined (C) architecture -- see PLAYER_SOG_PP_ROLE_OVERLAY. Not recommended alone; "
                "C already captures its value and more.",
        thresholds_meeting_bar=(1, 2, 3),
        recommended_operational_status="RESEARCH",
    ),
    OverlayRegistryEntry(
        overlay_id="PLAYER_BLOCKS_PK_REMOVAL_OVERLAY", target_prop="BLOCKED_SHOTS", status="REJECTED",
        architecture="narrow_removal",
        summary="A narrow REMOVED_FROM_PK-only overlay (beta=-0.050, 2-game step window, fit via a "
                "stable aggregate ratio after an earlier per-row regression produced an implausible "
                "+1.3 to +1.44 coefficient) does NOT improve OOS Brier -- frac_improved 0.0-0.016 "
                "across every threshold and both seasons. The raw residual finding from the prior "
                "sprint was real but does not survive a proper fit-and-validate cycle.",
        recommended_operational_status="RESEARCH",
    ),
    OverlayRegistryEntry(
        overlay_id="PLAYER_GOALS_PP_ROLE_OVERLAY", target_prop="GOALS", status="PARTIAL",
        architecture="A_absolute_role",
        summary="Absolute PP role (beta_PP1=+0.048, beta_PP2=-0.074 -- PP2 goal-scorers actually "
                "UNDERPERFORM the frozen model, an asymmetry SOG/Assists don't show) improves the 1+ "
                "threshold in both seasons (frac_improved 0.96, 0.98) but not the 2+ threshold "
                "(0.0025, 0.26 -- fails badly in 2024-25).",
        thresholds_meeting_bar=(1,),
        recommended_operational_status="RESEARCH",
    ),
    OverlayRegistryEntry(
        overlay_id="PLAYER_ASSISTS_PP_ROLE_OVERLAY", target_prop="ASSISTS", status="PARTIAL",
        architecture="A_absolute_role",
        summary="Absolute PP role (beta_PP1=+0.157) improves the 1+ threshold strongly in both "
                "seasons (frac_improved 1.0, 1.0) and the 2+ threshold well (0.92, 0.995); the 3+ "
                "threshold fails in 2024-25 (0.0125).",
        thresholds_meeting_bar=(1, 2),
        recommended_operational_status="RESEARCH",
    ),
    OverlayRegistryEntry(
        overlay_id="PLAYER_POINTS_PP_ROLE_OVERLAY", target_prop="POINTS", status="PARTIAL",
        architecture="A_absolute_role",
        summary="Logit-scale PP role offset (beta_PP1=+0.191, the largest of any prop) on the frozen "
                "empirical-baseline P(points>=1+) improves both seasons (frac_improved 0.68, 0.98) -- "
                "real but the weakest-margin PARTIAL of the three exploratory props in the earlier "
                "eval season. Only the 1+ threshold exists in this project's Points empirical model "
                "at all, so no cross-threshold comparison is possible.",
        thresholds_meeting_bar=(1,),
        recommended_operational_status="RESEARCH",
    ),
]
