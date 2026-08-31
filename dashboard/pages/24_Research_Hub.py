"""Page 24 — Research Hub: one landing page grouping the existing 17
detailed research pages and their reports (Preseason Operationalization
sprint, Section 66/Q). Preserves every existing research page and report
-- this is an index, not a replacement. Failed/partial research (e.g.
Team Goals by Period) is shown as prominently as validated work, per
this project's own standing discipline of not hiding negative results."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import components as comp

st.title("Research")
comp.render_model_status_header()
st.caption("Technical / validation material, separated from daily operational pages.")

GROUPS = {
    "Data Foundation": [
        ("Play-by-Play Data Status", "13_Play_By_Play_Status.py", "NHL_PBP_FOUR_SEASON_CORPUS_REPORT.md"),
    ],
    "Marginal Models": [
        ("Player SOG Research", "7_Player_SOG_Research.py", "PLAYER_SOG_FOUNDATION_REPORT.md"),
        ("Player SOG by Period", "14_Player_SOG_By_Period_Research.py", "PLAYER_SOG_BY_PERIOD_VALIDATION_REPORT.md"),
        ("Player Goals Research", "12_Player_Goals_Research.py", "PLAYER_GOALS_VALIDATION_REPORT.md"),
        ("Player Points Research", "11_Player_Points_Research.py", "PLAYER_POINTS_REDESIGN_REPORT.md"),
        ("Team SOG Research", "17_Team_SOG_Research.py", "TEAM_SOG_VALIDATION_REPORT.md"),
        ("Goalie Saves Research", "16_Goalie_Saves_Research.py", "GOALIE_SAVES_VALIDATION_REPORT.md"),
        ("Goalie Intelligence", "6_Goalie_Intelligence.py", "GOALIE_INTELLIGENCE_FOUNDATION_REPORT.md"),
    ],
    "Joint Dependence": [
        ("Joint Shot/Workload Research", "18_Joint_Shot_Workload_Research.py", "JOINT_SHOT_WORKLOAD_VALIDATION_REPORT.md"),
        ("Joint Scoring Dependence", "19_Joint_Scoring_Dependence_Research.py", "JOINT_SCORING_DEPENDENCE_VALIDATION_REPORT.md"),
    ],
    "Context / Overlays": [
        ("Player Context State", "20_Player_Context_State_Research.py", "PLAYER_CONTEXT_STATE_VALIDATION_REPORT.md"),
    ],
    "Confidence": [
        ("Prop Registry", "10_Prop_Registry.py", "CONFIDENCE_FRAMEWORK_REDESIGN_REPORT.md"),
    ],
    "Failed / Partial Research": [
        ("Team Goals by Period (NOT VALIDATED)", "15_Team_Goals_By_Period_Research.py",
         "TEAM_GOALS_BY_PERIOD_VALIDATION_REPORT.md"),
    ],
    "Architecture": [
        ("Model Performance", "4_Model_Performance.py", "ELO_REAL_DATA_COMPARISON_REPORT.md"),
        ("Research Lab", "5_Research_Lab.py", "MULTI_PROP_RESEARCH_REPORT.md"),
    ],
}

for group, items in GROUPS.items():
    st.markdown(f"### {group}")
    cols = st.columns(min(3, len(items)) or 1)
    for i, (title, page_file, report_file) in enumerate(items):
        with cols[i % len(cols)]:
            with st.container(border=True):
                st.markdown(f"**{title}**")
                st.caption(f"Report: `{report_file}`" if report_file else "")
                try:
                    st.page_link(f"pages/{page_file}", label="Open page")
                except Exception:
                    st.caption(f"pages/{page_file}")
    st.divider()

comp.render_provenance_panel()
