"""Page 22 — Model Health: one canonical status board driven entirely by
research/model_registry.py (Preseason Operationalization sprint, Section
51-53). No demo rows -- every row here is a real, current MODEL_REGISTRY
entry. Distinct from the pre-existing "Prop Registry" page (page 10,
research.player_props.registry), which tracks player-prop-level
market/confidence support specifically; this page is the broader,
cross-family status board requested this sprint, including team-level
and joint-dependence families Prop Registry doesn't cover."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import components as comp
from research.model_registry import MODEL_REGISTRY

st.title("Model Health")
comp.render_model_status_header()
st.caption("Driven live from research/model_registry.py — not a static demo table.")

status_map = {"VALIDATED": "VALIDATED", "PARTIAL": "PARTIAL", "REJECTED": "REJECTED",
              "ATTEMPTED_NOT_VALIDATED": "REJECTED", "EMPIRICAL_BASELINE_REMAINS_CHAMPION": "PARTIAL",
              "VALIDATED_OVERLAY": "SHADOW_VALIDATED", "VALIDATION_COMPLETE (MIXED, see registry)": "PARTIAL"}

for entry in MODEL_REGISTRY:
    with st.container(border=True):
        cols = st.columns([2, 1, 1])
        with cols[0]:
            st.markdown(f"**{entry.display_name}**")
            st.caption(entry.model_id)
        with cols[1]:
            badge_status = status_map.get(entry.status, "RESEARCH")
            comp.render_status_banner(badge_status, entry.status)
        with cols[2]:
            comp.render_status_banner(
                entry.operational_status if entry.operational_status in comp.STATUS_BANNER_STYLES
                else "RESEARCH", entry.operational_status.replace("_", " "))

        threshold_bits = []
        if entry.validated_thresholds:
            threshold_bits.append(f"Validated: {', '.join(entry.validated_thresholds)}")
        if entry.partial_thresholds:
            threshold_bits.append(f"Partial: {', '.join(entry.partial_thresholds)}")
        if entry.rejected_thresholds:
            threshold_bits.append(f"Rejected: {', '.join(entry.rejected_thresholds)}")
        if entry.insufficient_thresholds:
            threshold_bits.append(f"Insufficient data: {', '.join(entry.insufficient_thresholds)}")
        if threshold_bits:
            st.caption(" | ".join(threshold_bits))

        with st.expander("Technical detail"):
            st.markdown(f"- **Confidence behavior:** {entry.confidence_behavior}")
            st.markdown(f"- **LOW policy:** {entry.low_policy}")
            st.markdown(f"- **PIT status:** {entry.pit_status}")
            if entry.upstream_dependencies:
                st.markdown(f"- **Upstream:** {', '.join(entry.upstream_dependencies)}")
            if entry.downstream_consumers:
                st.markdown(f"- **Downstream:** {', '.join(entry.downstream_consumers)}")
            if entry.validation_report:
                st.markdown(f"- **Report:** `{entry.validation_report}`")
            if entry.results_file:
                st.markdown(f"- **Freeze file:** `{entry.results_file}`")
                st.code(entry.code_hash or "hash unavailable", language=None)

comp.render_provenance_panel()
