"""Page 18 -- Joint Shot/Workload Research. STATUS: RESEARCH --
JOINT PROBABILITY ESTIMATION ONLY. Not sportsbook pricing, not a parlay
optimizer, not a game simulator. No sportsbook odds are read or shown
here. See JOINT_SHOT_WORKLOAD_VALIDATION_REPORT.md."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import components as comp
from dashboard import data_access as da
from dashboard import joint_shot_workload_view as jv

st.title("Joint Shot / Workload Research")
comp.render_model_status_header()
comp.render_data_mode_badge()
st.markdown(
    """
    <div style="border:1px solid #1a3a5a; border-radius:6px; padding:8px 12px;
                background:#0f1a24; color:#8ce; font-size:0.85rem; margin-bottom:12px;">
      RESEARCH -- JOINT PROBABILITY ESTIMATION ONLY. This is NOT sportsbook pricing, NOT a
      parlay optimizer, and NOT a game simulator. Every joint probability here is derived from
      three FROZEN, unmodified marginal models (Player SOG, Team SOG, Goalie Saves) plus an
      explicit dependence layer sitting above them. No sportsbook odds are read or shown
      anywhere on this page. See JOINT_SHOT_WORKLOAD_VALIDATION_REPORT.md.
    </div>
    """,
    unsafe_allow_html=True,
)

results = jv.load_results()
if results is None:
    st.warning("research/joint_shot_workload_results.json not found -- run "
               "`python3 -m research.run_joint_shot_workload_model` first.")
    st.stop()

st.markdown("### Combination family status")
from research.joint_shot_workload.joint_dependence_registry import JOINT_DEPENDENCE_REGISTRY
color_by_status = {"VALIDATED": ":green", "PARTIAL": ":orange", "RESEARCH": ":gray",
                    "INSUFFICIENT_DATA": ":red"}
cols = st.columns(4)
for col, (cid, entry) in zip(cols, JOINT_DEPENDENCE_REGISTRY.items()):
    col.markdown(f"**{cid.replace('__', ' + ')}**")
    col.markdown(f"{color_by_status.get(entry.status, ':gray')}[{entry.status}]")

st.divider()
st.markdown("### Dependence lift by combination (real historical evaluation)")
for combo_name, combo_result in results.get("pair_results", {}).items():
    with st.expander(combo_name):
        st.json({s: b.get("dependence_lift") for s, b in combo_result["by_season"].items()})

st.divider()
st.markdown("### Representative examples (real historical rows, 2025-26)")
examples = results.get("representative_examples", {})
if examples:
    name = st.selectbox("Example", list(examples.keys()))
    st.json(examples[name])

st.divider()
st.caption(
    "Full narrative, per-combination Brier/log-loss, bootstrap evidence, Frechet bounds "
    "verification, and marginal-recovery checks: see JOINT_SHOT_WORKLOAD_VALIDATION_REPORT.md "
    "at the repo root."
)
