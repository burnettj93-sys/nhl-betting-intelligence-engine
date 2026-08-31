"""Page 27 — Goalies (Preseason Interactive Product sprint, Parts
70-74). Real goalie identities; starter certainty, model confidence, and
market readiness are kept visually and semantically separate (Part 71)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import components as comp
from dashboard import demo_data as dd

st.title("Goalies")
comp.render_model_status_header()
comp.render_global_search(key_prefix="goalies")
st.markdown(
    f"""
    <div style="border:1px solid #5a4420; border-radius:6px; padding:8px 12px;
                background:#241c10; color:#e8c46a; font-size:0.85rem; margin-bottom:12px;">
      {dd.DEMO_MODE_LABEL} — starter status and market prices below are simulated; validation
      thresholds are the real, current registry statuses.
    </div>
    """,
    unsafe_allow_html=True,
)

goalies = dd.build_demo_goalies()
if not goalies:
    comp.render_empty_state("MODEL_NOT_OPERATIONAL", "No goalie projections available.")
    st.stop()

for g in goalies:
    with st.container(border=True):
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown(f"**{g['name']}**")
            if st.button(f"Open {g['team']} — Team Intelligence", key=f"goalie_{g['goalie_id']}"):
                st.session_state["selected_team"] = g["team"]
                st.switch_page("pages/31_Team_Intelligence.py")
            st.caption(f"{g['team']} vs {g['opponent']}")
        with c2:
            st.markdown("**Starter Status** (roster certainty)")
            st.markdown(comp.label_badge(f"{g['starter_status'].replace('_', ' ')} · "
                                          f"{g['starter_probability'] * 100:.0f}%", "research"),
                        unsafe_allow_html=True)
        with c3:
            st.markdown("**Model Confidence** (separate dimension)")
            st.markdown(f"**{g['confidence']}**")

        m1, m2 = st.columns(2)
        m1.metric("Expected Saves", f"{g['expected_saves']:.1f}" if g["expected_saves"] else "—")
        m2.caption("Simulated matchup — real frozen model output for this real goalie.")

        st.markdown("**Validated Thresholds** (unchanged, real registry statuses)")
        badge_cols = st.columns(len(g["thresholds"]))
        for col, (k, v) in zip(badge_cols, g["thresholds"].items()):
            col.markdown(comp.label_badge(f"{k} {v.replace('_', ' ')}",
                                           "input" if v == "VALIDATED" else "unavailable"),
                        unsafe_allow_html=True)
        st.caption("PARTIAL and REJECTED thresholds are shown for transparency — they are never "
                   "presented as actionable BET candidates regardless of demo mode.")

comp.render_provenance_panel()
