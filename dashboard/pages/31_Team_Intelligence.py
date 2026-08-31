"""Page 31 — Team Intelligence (Preseason Interactive Product sprint,
Parts 93-94). Basic team view reusing existing validated/current outputs
only -- no new team-level model was built."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import components as comp
from dashboard import demo_data as dd
from dashboard import formatting as fmt
from dashboard import player_intelligence_view as piv

st.title("Team Intelligence")
comp.render_model_status_header()
comp.render_global_search(key_prefix="team")
st.markdown(
    f"""
    <div style="border:1px solid #5a4420; border-radius:6px; padding:8px 12px;
                background:#241c10; color:#e8c46a; font-size:0.85rem; margin-bottom:12px;">
      {dd.DEMO_MODE_LABEL}
    </div>
    """,
    unsafe_allow_html=True,
)

teams = sorted(dd.DEMO_TEAMS)
default_team = st.session_state.get("selected_team", teams[0])
team = st.selectbox("Team", teams, index=teams.index(default_team) if default_team in teams else 0)

opponent = dd._opponent_for(team)
if opponent is None:
    comp.render_empty_state("NO_GAMES", "No demo game scheduled for this team.")
    st.stop()

gh1, gh2 = st.columns([3, 1])
gh1.markdown(f"### {team} vs {opponent}")
gh1.caption(f"{dd.SIMULATED_DATE} (simulated)")
_game = next((g for g in dd.build_demo_games() if team in (g.away, g.home)), None)
if _game and gh2.button("Open Game Detail"):
    st.session_state["selected_game_id"] = _game.game_id
    st.switch_page("pages/2_Game_Detail.py")

goalies = [g for g in dd.build_demo_goalies() if g["team"] == team]
c1, c2 = st.columns(2)
with c1:
    st.markdown("**Goalie**")
    if goalies:
        g = goalies[0]
        st.markdown(f"{g['name']} — {g['starter_status'].replace('_', ' ')} ({g['starter_probability'] * 100:.0f}%)")
        st.caption(f"Expected saves: {g['expected_saves']:.1f}" if g["expected_saves"] else "—")
    else:
        st.caption("No real starter goalie identity mapped for this team in the demo roster.")
with c2:
    st.markdown("**Team SOG projection**")
    st.caption("Not yet wired to a per-team demo projection this sprint — see Team SOG Research "
               "for the validated model.")

st.markdown("#### Top Player Opportunities")
opportunities = [o for o in dd.build_demo_opportunities() if o["team"] == team]
by_player: dict[str, dict] = {}
for o in opportunities:
    cur = by_player.get(o["player_id"])
    if cur is None or o["conservative_edge"] > cur["conservative_edge"]:
        by_player[o["player_id"]] = o
top = sorted(by_player.values(), key=lambda o: -o["conservative_edge"])[:5]
if not top:
    comp.render_empty_state("NO_QUALIFYING_OPPORTUNITIES")
else:
    for o in top:
        c1, c2, c3 = st.columns([2, 1, 1])
        if c1.button(o["player"], key=f"team_{o['player_id']}"):
            st.session_state["selected_player_id"] = o["player_id"]
            st.switch_page("pages/25_Player_Intelligence.py")
        c2.caption(f"{o['market']} {o['threshold']}")
        c3.markdown(comp.label_badge(o["decision"], "input"), unsafe_allow_html=True)

waiting = [o for o in opportunities if o["decision"] == "WAIT"]
if waiting:
    st.markdown("#### WAIT Reasons")
    for o in waiting[:5]:
        st.caption(f"{o['player']} — {o['market']}: {o['decision_reason']}")

comp.render_provenance_panel()
