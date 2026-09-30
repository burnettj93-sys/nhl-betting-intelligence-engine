"""Page 25 — Player Intelligence (Platform Recovery block, 2026-09-30,
superseding the earlier demo-default Preseason Interactive Product
sprint). Default view uses the selected REAL NHL player: real identity,
real current team, today's real opponent if their team plays, real
market state (the same eligible PLAYER_SOG_ALTERNATE leg Player Props
shows, or an honest MARKET UNAVAILABLE), and real special-teams role
intelligence computed as of today's real date. The prior "McDavid demo
journey" (simulated near-future matchup, full market/tab exploration) is
retained for illustrating the product's depth, but lives behind its own
explicit, collapsed, clearly-labeled expander."""
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


@st.cache_data(show_spinner="Loading real player state...", ttl=300, max_entries=16)
def _cached_local_real_player_state(player_id: str):
    from operational import real_today_bridge
    return real_today_bridge.open_real_player_state(player_id)


st.title("Player Intelligence")
comp.render_model_status_header()
comp.render_global_search(key_prefix="pi")

st.markdown(
    """
    <div style="border:1px solid #1f4d2e; border-radius:6px; padding:8px 12px;
                background:#0f2417; color:#7fd99a; font-size:0.85rem; margin-bottom:12px;">
      <b>LIVE — REAL PLAYER, REAL DATA.</b> Identity, current team, today's opponent, and
      market state below are real. A Demo / Model Showcase (simulated near-future matchup, full
      market exploration) is available in its own collapsed section further down.
    </div>
    """,
    unsafe_allow_html=True,
)

_real_player_id = st.session_state.get("selected_player_id")

try:
    if _real_player_id:
        if runtime_mode.is_community_cloud():
            _all_players = {p["player_id"]: p for p in cloud_snapshot.real_all_players()}
            _identity = _all_players.get(_real_player_id)
            if _identity is None:
                _real_state = {"player": None}
            else:
                _market_rows = cloud_snapshot.real_player_props()["rows"]
                _market = next((r for r in _market_rows if r["player_id"] == _real_player_id), None)
                _teams = cloud_snapshot.real_team_intelligence()
                _team_state = _teams.get(_identity.get("team")) if _identity.get("team") else None
                _real_state = {
                    "player": _identity, "market_state": _market or "MARKET_UNAVAILABLE",
                    "today_opponent": _team_state["today_opponent"] if _team_state else None,
                }
        else:
            _real_state = _cached_local_real_player_state(_real_player_id)
    else:
        _real_state = None
except cloud_snapshot.SnapshotUnavailable as _exc:
    _real_state = None
    st.caption(f"Real player data is not available in this snapshot ({_exc}).")

if not _real_player_id:
    st.caption("No player selected — open a player from the Players page or Today's real slate "
               "to see their real state here.")
elif _real_state is None or _real_state.get("player") is None:
    comp.render_empty_state("ERROR", "Real player not found.")
else:
    _identity = _real_state["player"]
    h1, h2, h3 = st.columns(3)
    h1.markdown(f"### {_identity['full_name']}")
    h1.caption(f"{_identity.get('team') or '—'} · {_identity.get('position') or '—'}")
    if _identity.get("team") and h1.button(f"Open {_identity['team']} — Team Intelligence", key="real_pi_team_link"):
        st.session_state["selected_team"] = _identity["team"]
        st.switch_page("pages/31_Team_Intelligence.py")
    _opp = _real_state.get("today_opponent")
    if _opp:
        h2.markdown("**Today's Real Opponent**")
        h2.markdown(f"{'vs' if _opp['is_home'] else '@'} {_opp['opponent']} — CURRENT")
    else:
        h2.markdown("**Today's Real Opponent**")
        h2.caption("No real game scheduled today.")
    h3.markdown("**Real Market State**")
    _market = _real_state.get("market_state")
    if isinstance(_market, dict):
        from dashboard import formatting as fmt
        h3.markdown(f"{_market['market']} {_market['threshold']} — MODEL")
        h3.caption(f"Conservative P {fmt.format_probability(_market['conservative_probability'])} · "
                   f"DK {fmt.format_american_odds(_market['dk_price'])} · Edge {fmt.format_edge(_market['edge'])}")
    else:
        h3.markdown("MARKET UNAVAILABLE")
        h3.caption("No real, currently eligible leg for this player right now.")

    if not runtime_mode.is_community_cloud():
        with st.expander("Real Power Play Role (live special-teams role intelligence)", expanded=False):
            st.caption("Real, PIT-safe role inference from this player's own real recent ice-time "
                       "history (operational/special_teams_roles_live.py), as of today's real date "
                       "— never a lineup confirmation.")
            try:
                from operational import eastern_time as et
                from operational import special_teams_history_store as sths
                from operational import special_teams_roles_live as srl
                _sth_conn = sths.open_readonly()
                _role_state = srl.compute_pp_role_state(_sth_conn, _identity["player_id"],
                                                         _identity.get("team") or "", et.eastern_today())
                comp.render_pp_role_badge(_role_state)
            except Exception as exc:
                st.caption(f"Role intelligence unavailable: {exc}")

st.divider()

with st.expander("Demo / Model Showcase — SIMULATED, not the real product (click to expand)"):
    st.caption("Everything below uses a fixed SIMULATED near-future matchup (real player identity, "
               "real frozen-model probabilities, simulated schedule/prices) to illustrate the "
               "product's full market/decision depth. For the real product, see the section above.")

    from dashboard import demo_data as dd
    from dashboard import eligible_bets as eb
    from dashboard import formatting as fmt
    from dashboard import player_intelligence_view as piv

    st.markdown("### Demo Player Journey — SIMULATED")
    demo_player_id = st.session_state.get("selected_player_id")
    roster = dd.build_demo_roster()
    name_to_id = {p.name: p.player_id for p in roster}
    if not demo_player_id or demo_player_id not in name_to_id.values():
        demo_chosen_name = st.selectbox("Choose a demo player", sorted(name_to_id), key="demo_pi_player_select")
        demo_player_id = name_to_id[demo_chosen_name]

    player = piv.find_player(demo_player_id)
    if player is None:
        comp.render_empty_state("ERROR", "Player not found in the demo roster.")
    else:
        opps = piv.player_opportunities(demo_player_id)

        h1, h2, h3, h4 = st.columns(4)
        h1.markdown(f"### {player.name}")
        h1.caption(f"{player.team} · {player.position}")
        if h1.button(f"Open {player.team} — Team Intelligence Hub", key="pi_team_link"):
            st.session_state["selected_demo_team"] = player.team
            st.switch_page("pages/31_Team_Intelligence.py")
        h2.metric("Next Opponent", player.opponent)
        h2.caption(f"{dd.SIMULATED_DATE} (simulated)")
        if h2.button(f"Open {player.opponent} — Team Intelligence Hub", key="pi_opponent_link"):
            st.session_state["selected_demo_team"] = player.opponent
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

        st.divider()
        tab_next, tab_next5, tab_markets, tab_all_eligible = st.tabs(
            ["Next Game", "Next 5 Games", "Markets", "All Eligible Bets"])

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

        with tab_all_eligible:
            st.caption("Every model-supported threshold for this player, across the full validated range "
                       "(e.g. SOG 2+/3+/4+/5+, not just one) -- extends the single-threshold view above "
                       "without changing it.")
            all_player_opps = [o for o in eb.all_opportunities() if o["player_id"] == demo_player_id]
            if not all_player_opps:
                comp.render_empty_state("NO_QUALIFYING_OPPORTUNITIES")
            else:
                for o in sorted(all_player_opps, key=lambda o: (o["market"], o["threshold"])):
                    ec1, ec2, ec3, ec4 = st.columns([2, 1, 1, 1])
                    ec1.markdown(f"**{o['market']} {o['threshold']}**")
                    ec2.caption(f"Model {fmt.format_probability(o['coherent_probability'])}")
                    ec3.caption(f"Edge {fmt.format_edge(o['conservative_edge'])}")
                    ec4.markdown(comp.label_badge(o["decision"], "input"), unsafe_allow_html=True)

        st.divider()
        st.markdown("#### Performance & Context")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Actual vs. Expected (last 5, real history)**")
            ave = piv.actual_vs_expected(demo_player_id, "sog", 5)
            if ave:
                st.metric("Last 5 SOG — Actual", ave["actual"])
                st.metric("Expected", ave["expected"], delta=f"{ave['residual']:+.1f}")
            else:
                st.caption("Insufficient history.")
        with c2:
            st.markdown("**Role Trend (real history)**")
            trend = piv.multi_window_trend(demo_player_id, "toi")
            if trend["last_5"]:
                st.metric("TOI last 5 (min/g)", f"{trend['last_5'] / 60:.1f}" if trend["last_5"] else "—")
                st.caption(f"Last 10: {trend['last_10'] / 60:.1f} min | Season: {trend['season'] / 60:.1f} min"
                           if trend["last_10"] and trend["season"] else "")
            else:
                st.caption("Insufficient history.")

        context_state = next((o["context_state"] for o in opps if o["context_state"] == "COLD_AND_TOI_DECLINE"), None)
        st.markdown("**Context State**")
        if context_state:
            plain = comp.CONTEXT_STATE_PLAIN_LABEL.get(context_state, context_state)
            st.markdown(f"{comp.label_badge(plain, 'research')} `{context_state}` <span style='color:#8b93a7;'>"
                        f"SIMULATED CONTEXT (real overlay logic, simulated matchup)</span>", unsafe_allow_html=True)
        else:
            st.markdown(comp.label_badge("NORMAL", "input"), unsafe_allow_html=True)

        with st.expander("Context evidence (technical detail)"):
            evidence = piv.context_evidence(demo_player_id, player.team, player.opponent)
            if evidence:
                st.json(evidence)
            else:
                st.caption("Insufficient history for context evidence.")
            st.caption("Media sentiment: NOT BUILT — no legitimate historical corpus exists.")

comp.render_provenance_panel()
