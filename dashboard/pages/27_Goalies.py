"""Page 27 — Goalies (Platform Recovery block, 2026-09-30, superseding
the earlier demo-default Preseason Interactive Product sprint). Default
view is REAL: real current goalies, today's real opponent if their team
plays, and real starter status via features/point_in_time.py's own
sanctioned goalie_status() accessor -- UNCONFIRMED unless a real
CONFIRMED status has actually been observed, never a guessed starter.
GOALIE_SAVES has no certified real DraftKings payload contract today, so
market state is honestly MARKET UNAVAILABLE, never a simulated save
line. The prior demo board is retained for illustrating the model/
decision machinery, but lives behind its own explicit, collapsed,
clearly-labeled expander."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import cloud_snapshot
from dashboard import components as comp
from operational import runtime_mode


@st.cache_data(show_spinner="Loading real goalies...", ttl=300, max_entries=1)
def _cached_local_real_goalies_state():
    from operational import real_today_bridge
    return real_today_bridge.open_real_goalies_state()


st.title("Goalies")
comp.render_model_status_header()
comp.render_global_search(key_prefix="goalies")

st.markdown(
    """
    <div style="border:1px solid #1f4d2e; border-radius:6px; padding:8px 12px;
                background:#0f2417; color:#7fd99a; font-size:0.85rem; margin-bottom:12px;">
      <b>LIVE — REAL GOALIES.</b> Real current goalies, real today's opponent, and real starter
      status (UNCONFIRMED unless actually confirmed — never a guessed starter). GOALIE_SAVES has
      no certified real DraftKings market yet, so market state is honestly MARKET UNAVAILABLE.
      A Demo / Model Showcase (simulated matchups and save lines) is available in its own
      collapsed section further down.
    </div>
    """,
    unsafe_allow_html=True,
)

try:
    if runtime_mode.is_community_cloud():
        _real_state = cloud_snapshot.real_goalies()
    else:
        _real_state = _cached_local_real_goalies_state()
except cloud_snapshot.SnapshotUnavailable as _exc:
    _real_state = None
    st.caption(f"Real goalie data is not available in this snapshot ({_exc}).")

if _real_state is not None:
    _goalies_with_games = [g for g in _real_state["goalies"] if g["today_opponent"]]
    st.caption(f"{len(_goalies_with_games)} real goalie(s) with a real game today "
               f"(of {len(_real_state['goalies'])} real current goalies league-wide).")
    if not _goalies_with_games:
        comp.render_empty_state("NO_GAMES", "No real goalies have a real game scheduled today.")
    else:
        for g in _goalies_with_games:
            with st.container(border=True):
                c1, c2, c3 = st.columns(3)
                with c1:
                    st.markdown(f"**{g['full_name']}**")
                    if st.button(f"Open {g['team']} — Team Intelligence", key=f"real_goalie_{g['player_id']}"):
                        st.session_state["selected_team"] = g["team"]
                        st.switch_page("pages/31_Team_Intelligence.py")
                    _opp = g["today_opponent"]
                    st.caption(f"{g['team']} {'vs' if _opp['is_home'] else '@'} {_opp['opponent']}")
                with c2:
                    st.markdown("**Starter Status** (real)")
                    st.markdown(comp.label_badge(g["starter_status"],
                                                 "input" if g["is_confirmed_starter"] else "unavailable"),
                                unsafe_allow_html=True)
                with c3:
                    st.markdown("**Market State**")
                    st.markdown(comp.label_badge(g["market_state"], "unavailable"), unsafe_allow_html=True)

st.divider()

with st.expander("Demo / Model Showcase — SIMULATED, not the real product (click to expand)"):
    st.caption("Everything below uses a fixed SIMULATED matchup and SIMULATED save lines to "
               "illustrate the model/decision machinery for real goalie identities. For the real "
               "product, see the section above.")

    from dashboard import demo_data as dd

    st.markdown("### Demo Goalies — SIMULATED")
    goalies = dd.build_demo_goalies()
    if not goalies:
        comp.render_empty_state("MODEL_NOT_OPERATIONAL", "No goalie projections available.")
    else:
        for g in goalies:
            with st.container(border=True):
                c1, c2, c3 = st.columns(3)
                with c1:
                    st.markdown(f"**{g['name']}**")
                    if st.button(f"Open {g['team']} — Team Intelligence", key=f"goalie_{g['goalie_id']}"):
                        st.session_state["selected_demo_team"] = g["team"]
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
