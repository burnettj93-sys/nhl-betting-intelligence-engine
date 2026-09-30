"""Page 30 — Players (Platform Recovery block, 2026-09-30, superseding
the earlier demo-default Preseason Interactive Product sprint). Default
view is REAL: real NHL player identity, real current team/position, and
(cross-referenced against the same rows Player Props shows) real market
state -- MARKET_UNAVAILABLE, honestly, when no real eligible leg exists
for a player. Click a row to open Player Intelligence with that real
player selected. The prior demo roster is retained for illustrating the
model/decision machinery, but lives behind its own explicit, collapsed,
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


@st.cache_data(show_spinner="Loading real players...", ttl=3600, max_entries=1)
def _cached_local_real_players():
    from operational import real_today_bridge
    return real_today_bridge.open_all_players_list()


@st.cache_data(show_spinner="Loading real player market state...", ttl=300, max_entries=1)
def _cached_local_real_player_props_rows():
    from operational import real_today_bridge
    return real_today_bridge.open_real_player_props_state()["rows"]


st.title("Players")
comp.render_model_status_header()
comp.render_global_search(key_prefix="players")

st.markdown(
    """
    <div style="border:1px solid #1f4d2e; border-radius:6px; padding:8px 12px;
                background:#0f2417; color:#7fd99a; font-size:0.85rem; margin-bottom:12px;">
      <b>LIVE — REAL NHL PLAYERS.</b> Every player below is a real, current NHL entity. Current
      team is shown when real roster membership is known — otherwise "—", never guessed. Market
      state (right column) is the same real eligible leg data Player Props shows, or an honest
      MARKET UNAVAILABLE. A Demo / Model Showcase illustrating the decision machinery (simulated
      matchups/prices) is available in its own collapsed section further down.
    </div>
    """,
    unsafe_allow_html=True,
)

try:
    if runtime_mode.is_community_cloud():
        _real_players = cloud_snapshot.real_all_players()
        _real_market_rows = cloud_snapshot.real_player_props()["rows"]
    else:
        _real_players = _cached_local_real_players()
        _real_market_rows = _cached_local_real_player_props_rows()
except cloud_snapshot.SnapshotUnavailable as _exc:
    _real_players = None
    st.caption(f"Real player data is not available in this snapshot ({_exc}).")

if _real_players is not None:
    _market_by_id = {r["player_id"]: r for r in _real_market_rows}
    search_q = st.text_input("Filter by name", key="real_players_filter")
    rows = [p for p in _real_players if not search_q or search_q.lower() in p["full_name"].lower()]
    st.caption(f"{len(rows)} real player(s) (of {len(_real_players)} total).")

    for p in rows[:200]:
        cols = st.columns([2, 1, 1, 2])
        if cols[0].button(p["full_name"], key=f"real_players_row_{p['player_id']}"):
            st.session_state["selected_player_id"] = p["player_id"]
            st.switch_page("pages/25_Player_Intelligence.py")
        cols[1].caption(p.get("team") or "—")
        cols[2].caption(p.get("position") or "—")
        market = _market_by_id.get(p["player_id"])
        cols[3].caption(f"{market['market']} {market['threshold']}" if market else "MARKET UNAVAILABLE")
    if len(rows) > 200:
        st.caption(f"Showing first 200 of {len(rows)} matches — refine your filter to narrow further.")

st.divider()

with st.expander("Demo / Model Showcase — SIMULATED, not the real product (click to expand)"):
    st.caption("Everything below uses a fixed SIMULATED slate and roster to illustrate the "
               "model/decision machinery. Never a real current matchup. For the real product, "
               "see the section above.")

    from dashboard import demo_data as dd
    from dashboard import player_intelligence_view as piv

    st.markdown("### Demo Players — SIMULATED")
    roster = dd.build_demo_roster()
    opportunities = dd.build_demo_opportunities()
    demo_search_q = st.text_input("Filter by name", key="players_local_filter")

    for player in roster:
        if demo_search_q and demo_search_q.lower() not in player.name.lower():
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
