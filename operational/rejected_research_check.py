"""
2026-27 Continuous Learning framework, Part 49: consults the REAL,
already-existing rejected-research entries across this project's own
registries -- never a new, separately-maintained duplicate list. A
pattern discovered by the daily/weekly review that matches a real
rejected entry's `target_model`/market family is flagged as
PREVIOUSLY_TESTED_REJECTED and requires materially new evidence
(see operational/challenger_registry.py::validate_evidence) before a
new challenger may be proposed for it.
"""
from __future__ import annotations


def _rejected_from_model_registry() -> list[dict]:
    from research.model_registry import MODEL_REGISTRY
    out = []
    for e in MODEL_REGISTRY:
        if e.rejected_thresholds:
            out.append({"source": "research/model_registry.py", "model_id": e.model_id,
                        "rejected": list(e.rejected_thresholds), "report": e.validation_report})
    return out


def _rejected_from_market_registry() -> list[dict]:
    from research.player_props.market_registry import CANONICAL_MARKETS
    return [{"source": "research/player_props/market_registry.py", "market_id": m.market_id,
             "rejected": m.model_status} for m in CANONICAL_MARKETS if m.model_status == "REJECTED"]


def _rejected_from_special_teams_overlay_registry() -> list[dict]:
    from research.special_teams_role_overlay.registry import OVERLAY_REGISTRY
    return [{"source": "research/special_teams_role_overlay/registry.py", "overlay_id": e.overlay_id,
             "status": e.status} for e in OVERLAY_REGISTRY if e.status == "REJECTED"]


def _rejected_from_joint_dependence_registry() -> list[dict]:
    from research.joint_shot_workload.joint_dependence_registry import JOINT_DEPENDENCE_REGISTRY
    return [{"source": "research/joint_shot_workload/joint_dependence_registry.py", "combination_id": k,
             "status": e.status} for k, e in JOINT_DEPENDENCE_REGISTRY.items() if e.status == "REJECTED"]


def all_rejected_entries() -> list[dict]:
    """The single, real, aggregated view across every registry that
    carries a REJECTED status -- built fresh each call from the live
    registries, never a stale cached copy."""
    out = []
    out.extend(_rejected_from_model_registry())
    out.extend(_rejected_from_market_registry())
    out.extend(_rejected_from_special_teams_overlay_registry())
    out.extend(_rejected_from_joint_dependence_registry())
    return out


def matches_a_rejected_idea(target_model: str, hypothesis_text: str) -> dict | None:
    """A conservative, real-field match (model_id/market_id/overlay_id/
    combination_id equals target_model, case-insensitive) -- never a
    fuzzy text match against `hypothesis_text` that could false-positive
    on unrelated wording. `hypothesis_text` is accepted but currently
    only used as an extension point (Part 49's own text says "if a
    pattern resembles" -- exact-id matching is the safe, honest
    implementation until real prospective evidence justifies more)."""
    target = target_model.strip().upper()
    for entry in all_rejected_entries():
        for key in ("model_id", "market_id", "overlay_id", "combination_id"):
            if entry.get(key, "").upper() == target:
                return entry
    return None
