"""Page 30 — Players (Preseason Interactive Product sprint, Parts
87-90). Real NHL entities; click a row to open Player Intelligence."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import components as comp
from dashboard import demo_data as dd
from dashboard import player_intelligence_view as piv

st.title("Players")
comp.render_model_status_header()
comp.render_global_search(key_prefix="players")
st.markdown(
    f"""
    <div style="border:1px solid #5a4420; border-radius:6px; padding:8px 12px;
                background:#241c10; color:#e8c46a; font-size:0.85rem; margin-bottom:12px;">
      {dd.DEMO_MODE_LABEL}
    </div>
    """,
    unsafe_allow_html=True,
)

roster = dd.build_demo_roster()
opportunities = dd.build_demo_opportunities()
search_q = st.text_input("Filter by name", key="players_local_filter")

for player in roster:
    if search_q and search_q.lower() not in player.name.lower():
        continue
    opps = [o for o in opportunities if o["player_id"] == player.player_id]
    best = piv.hero_summary(opps)
    cols = st.columns([2, 1, 1, 1, 1, 1])
    if cols[0].button(player.name, key=f"players_row_{player.player_id}"):
        st.session_state["selected_player_id"] = player.player_id
        st.switch_page("pages/25_Player_Intelligence.py")
    cols[1].caption(f"{player.team} · {player.position}")
    cols[2].caption(f"vs {player.opponent}")
    cols[3].markdown(comp.label_badge("PROJECTED ACTIVE", "research"), unsafe_allow_html=True)
    cols[4].caption(best["market"] + " " + best["threshold"] if best else "MODEL ONLY")
    cols[5].markdown(comp.label_badge(best["decision"], "input") if best else "—", unsafe_allow_html=True)

comp.render_provenance_panel()
