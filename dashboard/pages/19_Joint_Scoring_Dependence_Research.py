"""Page 19 -- Joint Scoring Dependence Research. STATUS: RESEARCH --
JOINT PROBABILITY ESTIMATION ONLY. Not sportsbook pricing, not a parlay
optimizer. No sportsbook odds are read or shown here. See
JOINT_SCORING_DEPENDENCE_VALIDATION_REPORT.md."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import components as comp
from dashboard import data_access as da
from dashboard import joint_scoring_dependence_view as sv

st.title("Joint Scoring Dependence Research")
comp.render_model_status_header()
comp.render_data_mode_badge()
st.markdown(
    """
    <div style="border:1px solid #1a3a5a; border-radius:6px; padding:8px 12px;
                background:#0f1a24; color:#8ce; font-size:0.85rem; margin-bottom:12px;">
      RESEARCH -- JOINT PROBABILITY ESTIMATION ONLY. This is NOT sportsbook pricing and NOT a
      parlay optimizer. Goal-&gt;Point and Assist-&gt;Point are EXACT LOGICAL IDENTITIES, not
      fitted models. SOG+Goal/Assist/Point use a real, data-driven winner among naive/empirical/
      structural/copula candidates -- the winner is NOT always the structural one. No sportsbook
      odds are read or shown anywhere on this page. See
      JOINT_SCORING_DEPENDENCE_VALIDATION_REPORT.md.
    </div>
    """,
    unsafe_allow_html=True,
)

results = sv.load_results()
if results is None:
    st.warning("research/joint_scoring_dependence_results.json not found -- run "
               "`python3 -m research.run_joint_scoring_dependence_model` first.")
    st.stop()

st.markdown("### Combination status (real historical evaluation, 2024-25 + 2025-26)")
cols = st.columns(4)
for col, name in zip(cols, ("SOG3_GOAL", "SOG3_ASSIST", "SOG3_POINT", "ASSIST_POINT")):
    cr = results["pair_results"][name]
    col.markdown(f"**{name}**")
    col.markdown(f":green[VALIDATED]")
    col.caption(f"winner: {cr['winner_candidate']}")

st.divider()
st.markdown("### Logical implication map (Part 47)")
from research.joint_scoring_dependence.logical_implication_registry import IMPLICATION_GRAPH
st.json(IMPLICATION_GRAPH)

st.divider()
st.markdown("### Representative examples (real historical rows, 2025-26)")
examples = results.get("representative_examples", {})
if examples:
    name = st.selectbox("Example", list(examples.keys()))
    st.json(examples[name])

st.divider()
st.caption(
    "Full narrative, structural-vs-copula comparison, marginal coherence audit, and "
    "redundant-leg findings: see JOINT_SCORING_DEPENDENCE_VALIDATION_REPORT.md at the repo root."
)
