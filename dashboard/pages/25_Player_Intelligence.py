"""Page 25 — Player Intelligence (Preseason Interactive Product sprint,
Parts 38-63). Real NHL player identities; real frozen-model probabilities
computed for a SIMULATED near-future matchup (schedule/price simulated,
model output real). This page is the flagship "McDavid demo journey"
deliverable -- see PRESEASON_INTERACTIVE_PRODUCT_REPORT.md Section W."""
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

st.title("Player Intelligence")
comp.render_model_status_header()
comp.render_global_search(key_prefix="pi")
st.markdown(
    f"""
    <div style="border:1px solid #5a4420; border-radius:6px; padding:8px 12px;
                background:#241c10; color:#e8c46a; font-size:0.85rem; margin-bottom:12px;">
      {dd.DEMO_MODE_LABEL} — schedule and sportsbook prices below are simulated for a plausible
      future NHL game night ({dd.SIMULATED_DATE}); every displayed probability is the real, frozen
      model's own output for this real player.
    </div>
    """,
    unsafe_allow_html=True,
)

player_id = st.session_state.get("selected_player_id")
roster = dd.build_demo_roster()
name_to_id = {p.name: p.player_id for p in roster}
if not player_id:
    chosen_name = st.selectbox("Choose a player", sorted(name_to_id))
    player_id = name_to_id[chosen_name]

player = piv.find_player(player_id)
if player is None:
    comp.render_empty_state("ERROR", "Player not found in the demo roster.")
    st.stop()

opps = piv.player_opportunities(player_id)

# --- Header (Part 38) ---
h1, h2, h3, h4 = st.columns(4)
h1.markdown(f"### {player.name}")
h1.caption(f"{player.team} · {player.position}")
h2.metric("Next Opponent", player.opponent)
h2.caption(f"{dd.SIMULATED_DATE} (simulated)")
if h2.button(f"Open {player.opponent} — Team Intelligence", key="pi_opponent_link"):
    st.session_state["selected_team"] = player.opponent
    st.switch_page("pages/31_Team_Intelligence.py")
_activity = dd.player_activity_status(player.player_id, player.team, player.opponent)
h3.markdown("**Active Status**")
h3.markdown(comp.label_badge(_activity["status"] or "UNKNOWN",
                              "research" if _activity["status"] == "PROJECTED_ACTIVE" else "unavailable"),
            unsafe_allow_html=True)
h4.markdown("**Mode**")
h4.markdown(comp.label_badge("DEMO", "research"), unsafe_allow_html=True)
with st.expander("Technical detail"):
    st.code(f"player_id: {player.player_id}", language=None)

# --- Hero summary (Part 39) ---
st.divider()
best = piv.hero_summary(opps)
st.markdown("#### Best Available Market")
if best is None:
    st.markdown("**BEST AVAILABLE MARKET: NONE**")
    if _activity["status"] and _activity["status"] != "PROJECTED_ACTIVE":
        st.caption(f"Real model status: `{_activity['status']}` — {_activity['note']}")
    else:
        st.caption("No qualifying market for this player under current demo conditions.")
else:
    comp.render_opportunity_card({
        "player": player.name, "team": player.team, "opponent": player.opponent,
        "market": best["market"], "threshold": best["threshold"], "decision": best["decision"],
        "confidence": best["confidence"], "raw_probability": best["raw_probability"],
        "context_adjusted_probability": best["context_adjusted_probability"],
        "conservative_probability": best["conservative_probability"],
        "market_no_vig_probability": best["market_no_vig_probability"], "fair_odds": best["fair_odds"],
        "current_odds": best["current_odds"], "max_acceptable_price": best["max_acceptable_price"],
        "conservative_edge": best["conservative_edge"], "ev": best["ev"],
        "context_state": best["context_state"],
        "context_raw": best["raw_probability"], "context_adjusted": best["context_adjusted_probability"],
        "context_delta": best["context_adjusted_probability"] - best["raw_probability"],
        "drivers": [], "risks": [best["decision_reason"]],
    })

# --- Top player metrics (Part 40) ---
st.markdown("#### Top Metrics")
m1, m2, m3, m4 = st.columns(4)
sog_o = next((o for o in opps if o["prop"] == "sog"), None)
goals_o = next((o for o in opps if o["prop"] == "goals"), None)
assists_o = next((o for o in opps if o["prop"] == "assists"), None)
points_o = next((o for o in opps if o["prop"] == "points"), None)
m1.metric("Expected SOG (3+)", fmt.format_probability(sog_o["raw_probability"]) if sog_o else "—")
m2.metric("Goal 1+ P", fmt.format_probability(goals_o["raw_probability"]) if goals_o else "—")
m3.metric("Assist 1+ P", fmt.format_probability(assists_o["raw_probability"]) if assists_o else "—")
m4.metric("Point 1+ P", fmt.format_probability(points_o["raw_probability"]) if points_o else "—")

# --- Tabs (Part 41/42/44) ---
st.divider()
tab_next, tab_next5, tab_markets = st.tabs(["Next Game", "Next 5 Games", "Markets"])

with tab_next:
    st.caption(f"vs {player.opponent} · {dd.SIMULATED_DATE} (simulated)")
    _next_game = next(
        (g for g in dd.build_demo_games() if {g.away, g.home} == {player.team, player.opponent}), None)
    if _next_game is not None and st.button("Open Game Detail", key="pi_next_game_detail"):
        st.session_state["selected_game_id"] = _next_game.game_id
        st.switch_page("pages/2_Game_Detail.py")
    groups = piv.group_opportunities(opps)
    for label, key in [("Best Opportunities", "BEST"), ("Watchlist", "WATCHLIST"),
                        ("Waiting on Data", "WAITING"), ("Passes / Too Expensive", "PASSES")]:
        st.markdown(f"**{label}** ({len(groups[key])})")
        if not groups[key]:
            st.caption("None.")
            continue
        for o in groups[key]:
            price_status = "PRICE OK" if o["conservative_edge"] >= 0 else "TOO EXPENSIVE"
            st.markdown(
                f"- {o['market']} {o['threshold']} — {fmt.format_american_odds(o['current_odds'])} "
                f"(max buy {fmt.format_american_odds(o['max_acceptable_price'])}, {price_status}) "
                f"— edge {fmt.format_edge(o['conservative_edge'])} — {o['decision']}")

with tab_next5:
    st.caption("SIMULATED schedule — real future opponents are not yet known. Market prices for "
               "games this far out are never fabricated.")
    for g in piv.next_five_games(player):
        c1, c2, c3, c4 = st.columns(4)
        c1.markdown(f"**{g['date']}**")
        c2.markdown(f"vs {g['opponent']} ({g['home_away']})")
        c3.markdown("Readiness: `SIMULATED`")
        c4.markdown(f"Price: `{g['market_price']}`")

with tab_markets:
    st.caption("All markets this engine currently understands for this player.")
    rows = []
    for o in opps:
        rows.append({
            "Market": o["market"], "Threshold": o["threshold"], "Raw P": fmt.format_probability(o["raw_probability"]),
            "Adjusted P": fmt.format_probability(o["context_adjusted_probability"]),
            "Conservative P": fmt.format_probability(o["conservative_probability"]),
            "No-Vig P": fmt.format_probability(o["market_no_vig_probability"]),
            "Fair Odds": fmt.format_american_odds(o["fair_odds"]),
            "Current Odds": fmt.format_american_odds(o["current_odds"]),
            "Max Buy": fmt.format_american_odds(o["max_acceptable_price"]),
            "Edge": fmt.format_edge(o["conservative_edge"]), "EV": fmt.format_ev(o["ev"]),
            "Confidence": o["confidence"], "Decision": o["decision"],
        })
    if rows:
        st.dataframe(rows, width='stretch')
    else:
        comp.render_empty_state("MODEL_NOT_OPERATIONAL", "No supported markets found for this player.")

# --- Performance / Context (Parts 51-57) ---
st.divider()
st.markdown("#### Performance & Context")
c1, c2 = st.columns(2)
with c1:
    st.markdown("**Actual vs. Expected (last 5, real history)**")
    ave = piv.actual_vs_expected(player_id, "sog", 5)
    if ave:
        st.metric("Last 5 SOG — Actual", ave["actual"])
        st.metric("Expected", ave["expected"], delta=f"{ave['residual']:+.1f}")
    else:
        st.caption("Insufficient history.")
with c2:
    st.markdown("**Role Trend (real history)**")
    trend = piv.multi_window_trend(player_id, "toi")
    if trend["last_5"]:
        st.metric("TOI last 5 (min/g)", f"{trend['last_5'] / 60:.1f}" if trend["last_5"] else "—")
        st.caption(f"Last 10: {trend['last_10'] / 60:.1f} min | Season: {trend['season'] / 60:.1f} min"
                   if trend["last_10"] and trend["season"] else "")
    else:
        st.caption("Insufficient history.")

with st.expander("Power Play Role (live special-teams role intelligence)", expanded=False):
    st.caption("Real, PIT-safe role inference from this player's own real recent ice-time history "
               "(operational/special_teams_roles_live.py) -- never a lineup confirmation.")
    try:
        from operational import special_teams_history_store as sths
        from operational import special_teams_roles_live as srl
        _sth_conn = sths.get_connection()
        _role_state = srl.compute_pp_role_state(_sth_conn, player.player_id, player.team, dd.SIMULATED_DATE)
        comp.render_pp_role_badge(_role_state)
    except Exception as exc:
        st.caption(f"Role intelligence unavailable: {exc}")

context_state = next((o["context_state"] for o in opps if o["context_state"] == "COLD_AND_TOI_DECLINE"), None)
st.markdown("**Context State**")
if context_state:
    plain = comp.CONTEXT_STATE_PLAIN_LABEL.get(context_state, context_state)
    st.markdown(f"{comp.label_badge(plain, 'research')} `{context_state}` <span style='color:#8b93a7;'>"
                f"SIMULATED CONTEXT (real overlay logic, simulated matchup)</span>", unsafe_allow_html=True)
else:
    st.markdown(comp.label_badge("NORMAL", "input"), unsafe_allow_html=True)

with st.expander("Context evidence (technical detail)"):
    evidence = piv.context_evidence(player_id, player.team, player.opponent)
    if evidence:
        st.json(evidence)
    else:
        st.caption("Insufficient history for context evidence.")
    st.caption("Media sentiment: NOT BUILT — no legitimate historical corpus exists.")

comp.render_provenance_panel()
