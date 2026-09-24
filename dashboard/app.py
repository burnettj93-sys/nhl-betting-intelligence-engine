"""
NHL Betting Intelligence Engine — Model Research + Intelligence Dashboard

Entry point. Run with:

    streamlit run dashboard/app.py

See README.md's "Dashboard" section for setup and page-by-page details.
This app is READ-ONLY with respect to model behavior and every database
it touches — see data_access.py's module docstring for the exact
guarantee and how it's enforced/tested.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import auth

st.set_page_config(
    page_title="NHL Model Research Dashboard",
    page_icon="🏒",
    layout="wide",
    initial_sidebar_state="expanded",
)

# P0.7 (2026-09-24 hardening block): the auth gate runs BEFORE any
# navigation or page content is built. A logged-out session (or a
# fresh install with zero accounts yet) only ever sees a login/
# bootstrap form -- st.stop() below means none of the real navigation,
# page registration, or per-page content below this point executes.
_user = auth.render_auth_gate()
if _user is None:
    st.stop()

PAGES_DIR = Path(__file__).resolve().parent / "pages"


def _p(filename: str, title: str, icon: str, default: bool = False) -> "st.Page":
    return st.Page(str(PAGES_DIR / filename), title=title, icon=icon, default=default)


# Preseason Operationalization sprint: navigation reorganized into
# Operate / Track & Monitor / Research (Section 35-37) using Streamlit's
# native sectioned st.navigation. No existing page was deleted -- every
# one of the original 20 pages is still reachable, just regrouped. Icons
# de-duplicated across the WHOLE nav (the dashboard audit found 🏒 and 🥅
# each used twice) -- every icon below now appears exactly once.
_nav_sections = {
    "Operate": [
        _p("21_Today.py", "Today", "☀️", default=True),
        _p("8_Live_SOG_Markets.py", "Live SOG Markets", "📡"),
        _p("26_Player_Props.py", "Player Props", "🎫"),
        _p("27_Goalies.py", "Goalies", "🛡️"),
        _p("28_Combinations.py", "Combinations", "🧮"),
        _p("1_Game_Slate.py", "Games", "🗓️"),
        _p("2_Game_Detail.py", "Game Detail", "🔍"),
    ],
    "Track & Monitor": [
        _p("29_Market_Movement.py", "Market Movement", "📉"),
        _p("30_Players.py", "Players", "🧑‍🤝‍🧑"),
        _p("31_Team_Intelligence.py", "Team Intelligence", "🏟️"),
        _p("25_Player_Intelligence.py", "Player Intelligence", "🧠"),
        _p("22_Model_Health.py", "Model Health", "🩺"),
        _p("32_Model_Learning.py", "Model Learning", "🔄"),
        _p("33_Paper_Performance.py", "Paper Performance", "💰"),
        _p("36_Morning_Review.py", "Morning Review", "🧭"),
        _p("23_Ledger.py", "Ledger", "📒"),
        _p("9_Data_Status.py", "Data Status", "🗂️"),
        _p("13_Play_By_Play_Status.py", "Play-by-Play Status", "🧩"),
        _p("3_Team_Ratings.py", "Team Ratings", "📊"),
    ],
    "Research": [
        _p("24_Research_Hub.py", "Research Hub", "🧪"),
        _p("10_Prop_Registry.py", "Prop Registry", "📋"),
        _p("7_Player_SOG_Research.py", "Player SOG Research", "🎯"),
        _p("14_Player_SOG_By_Period_Research.py", "Player SOG by Period", "⏱️"),
        _p("12_Player_Goals_Research.py", "Player Goals Research", "🚨"),
        _p("11_Player_Points_Research.py", "Player Points Research", "🏒"),
        _p("17_Team_SOG_Research.py", "Team SOG Research", "📐"),
        _p("16_Goalie_Saves_Research.py", "Goalie Saves Research", "🧤"),
        _p("6_Goalie_Intelligence.py", "Goalie Intelligence", "🥅"),
        _p("18_Joint_Shot_Workload_Research.py", "Joint Shot/Workload Research", "🔗"),
        _p("19_Joint_Scoring_Dependence_Research.py", "Joint Scoring Dependence", "🎲"),
        _p("15_Team_Goals_By_Period_Research.py", "Team Goals by Period", "🧊"),
        _p("20_Player_Context_State_Research.py", "Player Context State", "🌡️"),
        _p("4_Model_Performance.py", "Model Performance", "📈"),
        _p("5_Research_Lab.py", "Research Lab", "🔬"),
    ],
}

# P0.7: the Fantasy section (and everything Yahoo-related inside it) is
# only ever REGISTERED in the navigation for an ADMIN session -- a USER
# session has no route to it at all via the sidebar. This is a UX
# nicety, not the real security boundary: 34_Fantasy_HQ.py and
# 35_Fantasy_Settings.py each also call auth.require_admin() at the top
# of their own script, so even a direct/unexpected way to reach that
# page's file still fails server-side, never just relying on this nav
# omission (see tests/test_auth.py's explicit route-level tests).
if _user["role"] == "ADMIN":
    _nav_sections["Fantasy"] = [
        _p("34_Fantasy_HQ.py", "Fantasy HQ", "🏆"),
        _p("35_Fantasy_Settings.py", "Fantasy Settings", "🔌"),
    ]

pg = st.navigation(_nav_sections)

with st.sidebar:
    st.markdown("### 🏒 NHL Intelligence Engine")
    st.caption("Model Research + Intelligence Dashboard")
    st.caption("Read-only research view — v1")
    st.divider()
    st.caption(f"Signed in as **{_user['username']}** ({_user['role']})")
    if st.button("Log out"):
        auth.logout()
        st.rerun()

pg.run()
