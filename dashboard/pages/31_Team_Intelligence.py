"""Page 31 — Team Intelligence Hub (Same-Day Demo Experience sprint,
Parts 1-3, 30-34). P0: selecting a team must show ALL eligible bets
connected to that team, across every model-supported market family.
No new team-level model was built -- this page only aggregates and
presents outputs from already-real, already-tested components."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import components as comp
from dashboard import conviction as cv
from dashboard import demo_data as dd
from dashboard import eligible_bets as eb
from dashboard import formatting as fmt

st.title("Team Intelligence")
comp.render_model_status_header()
comp.render_global_search(key_prefix="team")
st.markdown(
    f"""
    <div style="border:1px solid #5a4420; border-radius:6px; padding:8px 12px;
                background:#241c10; color:#e8c46a; font-size:0.85rem; margin-bottom:12px;">
      {dd.DEMO_MODE_LABEL} — all prices below are SIMULATED MARKET (DEMO ONLY).
    </div>
    """,
    unsafe_allow_html=True,
)

teams = sorted(dd.DEMO_TEAMS)
default_team = st.session_state.get("selected_team", teams[0])
team = st.selectbox("Team", teams, index=teams.index(default_team) if default_team in teams else 0)
st.session_state["selected_team"] = team

opponent = dd._opponent_for(team)
if opponent is None:
    comp.render_empty_state("NO_GAMES", "No demo game scheduled for this team.")
    st.stop()

game = next((g for g in dd.build_demo_games() if team in (g.away, g.home)), None)
is_home = bool(game and game.home == team)
goalies = [g for g in dd.build_demo_goalies() if g["team"] == team]
starter = goalies[0] if goalies else None

# ---- Header ---------------------------------------------------------------
hcol1, hcol2 = st.columns([3, 1])
with hcol1:
    st.markdown(f"### {team} {'vs' if is_home else '@'} {opponent}")
    loc = "HOME" if is_home else "AWAY"
    st.caption(f"{dd.SIMULATED_DATE} (simulated) · {loc}")
    st.caption("Season record / recent form: NOT AVAILABLE — no live standings feed is wired "
               "this sprint (would require fabricating data; not done).")
    if game:
        st.caption(f"Model readiness: {game.model_ready} · Starter readiness: {game.starter_ready} · "
                   f"Market readiness: {game.market_ready}")
    if starter:
        st.caption(f"Goalie: {starter['name']} — {starter['starter_status'].replace('_', ' ')} "
                   f"({starter['starter_probability'] * 100:.0f}% starter probability)")
    else:
        st.caption("Goalie: no mapped starter identity for this team in the demo roster.")
with hcol2:
    if game and st.button("Open Game Detail", key="hub_open_game"):
        st.session_state["selected_game_id"] = game.game_id
        st.switch_page("pages/2_Game_Detail.py")
    if st.button(f"{opponent} Hub", key="hub_open_opponent"):
        st.session_state["selected_team"] = opponent
        st.rerun()

# ---- Data shared across tabs ----------------------------------------------
all_opps = eb.all_opportunities()
team_bets = eb.eligible_bets_for_team(team, all_opps)
actionable = team_bets["actionable"]
research_only = team_bets["research_only"]

tab_overview, tab_bets, tab_players, tab_matchup, tab_trends, tab_model = st.tabs(
    ["OVERVIEW", "BETS", "PLAYERS", "MATCHUP", "TRENDS", "MODEL"]
)

# ---- OVERVIEW ---------------------------------------------------------------
with tab_overview:
    o1, o2, o3 = st.columns(3)
    o1.metric("Eligible bets (actionable)", len(actionable))
    o2.metric("BET-grade", sum(1 for o in actionable if o["decision"] == "BET"))
    o3.metric("WATCH-grade", sum(1 for o in actionable if o["decision"] == "WATCH"))

    st.markdown("#### Top Conviction on this team")
    team_conviction = cv.top_conviction(all_opps)
    team_conviction = [o for o in team_conviction if o["team"] == team]
    if not team_conviction:
        st.caption("No Top Conviction opportunity for this team on today's simulated slate.")
    else:
        for o in team_conviction:
            cc1, cc2, cc3 = st.columns([2, 1, 1])
            if cc1.button(f"{o['player']} — {o['market']} {o['threshold']}", key=f"ov_{o['player_id']}_{o['prop']}_{o['threshold']}"):
                st.session_state["selected_player_id"] = o["player_id"]
                st.switch_page("pages/25_Player_Intelligence.py")
            cc2.caption(f"Model {fmt.format_probability(o['coherent_probability'])}")
            cc3.markdown(comp.label_badge(o["decision"], "input"), unsafe_allow_html=True)

    st.markdown("#### Team SOG (real historical context)")
    st.caption("Team SOG has a validated research model (see Team SOG Research) but no live "
               "per-team demo projection is wired this sprint — shown here only as a deferred, "
               "honestly-disclosed limitation, never a fabricated number.")

# ---- BETS (P0: all eligible bets connected to this team) ------------------
with tab_bets:
    st.markdown(f"#### All eligible bets — {team}")
    filt_col1, filt_col2 = st.columns([2, 2])
    decision_filter = filt_col1.multiselect("Decision", ["BET", "WATCH", "WAIT", "PASS"],
                                             default=["BET", "WATCH", "WAIT", "PASS"], key="bets_decision_filter")
    market_options = sorted({o["market"] for o in actionable})
    market_filter = filt_col2.multiselect("Market", market_options, default=market_options, key="bets_market_filter")
    sort_choice = st.selectbox(
        "Sort by", ["Best actionable", "Highest probability", "Biggest edge", "Best EV", "Highest confidence"],
        key="bets_sort")

    rows = [o for o in actionable if o["decision"] in decision_filter and o["market"] in market_filter]
    sort_key = {
        "Best actionable": lambda o: -cv.conviction_score(o),
        "Highest probability": lambda o: -(o.get("coherent_probability") or 0),
        "Biggest edge": lambda o: -(o.get("conservative_edge") or -1),
        "Best EV": lambda o: -(o.get("ev") or -1),
        "Highest confidence": lambda o: -{"HIGH": 2, "MEDIUM": 1, "LOW": 0}.get(o.get("confidence"), 0),
    }[sort_choice]
    rows = sorted(rows, key=sort_key)

    if not rows:
        comp.render_empty_state("NO_QUALIFYING_OPPORTUNITIES")
    else:
        for o in rows:
            with st.container(border=True):
                r1, r2, r3, r4 = st.columns([2, 1, 1, 1])
                label = f"{o['player']} ({o['entity_kind']})" if o["entity_kind"] != "TEAM" else o["player"]
                if r1.button(f"{label} — {o['market']} {o['threshold']}", key=f"bt_{o['market_id']}_{o['player_id']}"):
                    st.session_state["selected_player_id"] = o["player_id"]
                    st.switch_page("pages/25_Player_Intelligence.py")
                r2.caption(f"Model {fmt.format_probability(o['coherent_probability'])}")
                r3.caption(f"Edge {fmt.format_edge(o['conservative_edge'])}")
                r4.markdown(comp.label_badge(o["decision"], "input"), unsafe_allow_html=True)
                st.caption(f"Fair {fmt.format_american_odds(o['fair_odds'])} · "
                           f"Sim. Market {fmt.format_american_odds(o.get('current_odds'))} · "
                           f"EV {fmt.format_ev(o.get('ev'))} · Confidence {o['confidence']}")
                if o.get("decision_reason"):
                    st.caption(f"Reason: {o['decision_reason']}")

    if research_only:
        with st.expander(f"Research-only / not actionable — {len(research_only)}"):
            for o in research_only:
                st.caption(f"{o['player']} — {o['market']} {o['threshold']}: "
                           f"{o.get('decision_reason', 'not actionable')}")

# ---- PLAYERS ----------------------------------------------------------------
with tab_players:
    st.markdown(f"#### Roster — {team}")
    roster_ids = sorted({o["player_id"] for o in actionable + research_only if o["entity_kind"] == "PLAYER"})
    by_player_name = {o["player_id"]: o["player"] for o in actionable + research_only}
    if not roster_ids:
        st.caption("No player opportunities generated for this team on today's simulated slate.")
    for pid in roster_ids:
        legs = [o for o in actionable if o["player_id"] == pid]
        pcol1, pcol2 = st.columns([3, 1])
        pcol1.markdown(f"**{by_player_name.get(pid, pid)}** — {len(legs)} eligible bet(s)")
        if pcol2.button("Open Player Intelligence", key=f"pl_{pid}"):
            st.session_state["selected_player_id"] = pid
            st.switch_page("pages/25_Player_Intelligence.py")
        activity = dd.player_activity_status(pid, team, opponent)
        st.caption(f"Availability: {activity.get('status', 'UNKNOWN')} "
                   f"({activity.get('reason', 'UNKNOWN')})")

    if starter:
        st.markdown("#### Goalie")
        st.markdown(f"**{starter['name']}** — {starter['starter_status'].replace('_', ' ')}")
        gcol1, gcol2 = st.columns([3, 1])
        gcol1.caption(f"Expected saves: {starter['expected_saves']:.1f}" if starter["expected_saves"] else "—")
        if gcol2.button("Open Player Intelligence", key=f"pl_goalie_{starter['goalie_id']}"):
            st.session_state["selected_player_id"] = starter["goalie_id"]
            st.switch_page("pages/25_Player_Intelligence.py")

# ---- MATCHUP -----------------------------------------------------------------
with tab_matchup:
    st.markdown(f"#### {team} vs {opponent}")
    st.caption("Head-to-head history, line comparison, and market movement are not wired to a "
               "real data source for the simulated demo slate this sprint — shown honestly as "
               "unavailable rather than fabricated.")
    opp_opps = eb.eligible_bets_for_team(opponent, all_opps)["actionable"]
    st.markdown(f"#### {opponent} eligible bets (for comparison)")
    if not opp_opps:
        st.caption(f"No actionable opportunities for {opponent} on today's simulated slate.")
    else:
        st.dataframe([{"Player": o["player"], "Market": o["market"], "Threshold": o["threshold"],
                       "Model P": fmt.format_probability(o["coherent_probability"]),
                       "Decision": o["decision"]} for o in opp_opps[:10]], width='stretch')

# ---- TRENDS -------------------------------------------------------------------
with tab_trends:
    st.markdown("#### Betting Trends")
    st.caption("Line/market-movement trend history is not wired to a real market feed for the "
               "simulated demo slate this sprint (would require live Odds API history). "
               "Shown honestly as unavailable rather than fabricated.")

# ---- MODEL --------------------------------------------------------------------
with tab_model:
    st.markdown("#### Model view for this team")
    st.caption("Every probability shown for this team's players is the real, frozen production "
               "model's own output (ShadowContextStack / ContextMarginalContext), computed for "
               "real NHL player identities on a simulated near-future schedule. Prices are "
               "SIMULATED MARKET (DEMO ONLY), deterministically generated — never DraftKings, "
               "never a live book.")
    conf_counts: dict[str, int] = {}
    for o in actionable:
        conf_counts[o["confidence"]] = conf_counts.get(o["confidence"], 0) + 1
    if conf_counts:
        st.markdown("**Confidence distribution**")
        st.dataframe([{"Confidence": k, "Count": v} for k, v in sorted(conf_counts.items())], width='stretch')
    mc1, mc2 = st.columns(2)
    if mc1.button("Open Model Health", key="team_model_health"):
        st.switch_page("pages/22_Model_Health.py")
    if mc2.button("Open Model Learning", key="team_model_learning"):
        st.switch_page("pages/32_Model_Learning.py")

comp.render_provenance_panel()
