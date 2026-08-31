"""Page 2 — Game Detail: full breakdown of one game's model output, plus
team context. MODEL INPUT (Elo + home ice) is visually separated from
RESEARCH METRIC (MoneyPuck) values throughout — this distinction is
mandatory, not cosmetic."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import altair as alt
import pandas as pd
import streamlit as st

from dashboard import components as comp
from dashboard import data_access as da
from dashboard import model_view as mv


@st.cache_data(show_spinner="Loading real NHL corpus and computing baseline predictions...")
def _load_predictions() -> list[dict]:
    return da.compute_baseline_predictions()


def _moneypuck_conn():
    # Deliberately NOT @st.cache_resource: a cached sqlite3.Connection can
    # get reused from a different script-run thread than the one that
    # created it (sqlite3 connections aren't thread-safe by default),
    # which raised "SQLite objects created in a thread can only be used
    # in that same thread" under Streamlit AppTest's per-run threading.
    # This call is cheap (one sqlite3.connect + schema check) and only
    # happens once per page load, so recomputing it is free.
    try:
        return da.get_moneypuck_connection()
    except da.DataAvailabilityError:
        return None


st.title("Game Detail")
comp.render_model_status_header()
comp.render_data_mode_badge()

# Preseason Closing sprint (Track 2): DEMO-mode game-intelligence branch.
# Every demo game_id is prefixed "demo-" (see dashboard/demo_data.py) --
# this early branch renders the full enriched intelligence view and
# stops, leaving 100% of the existing REAL historical Game Detail logic
# below completely untouched for real game_ids.
_selected_game_id = st.session_state.get("selected_game_id")
if _selected_game_id and str(_selected_game_id).startswith("demo-"):
    from dashboard import demo_data as dd
    from dashboard import game_detail_view as gdv
    from dashboard import player_intelligence_view as piv
    from dashboard import formatting as fmt

    comp.render_global_search(key_prefix="gamedetail")
    st.markdown(
        f"""
        <div style="border:1px solid #5a4420; border-radius:6px; padding:8px 12px;
                    background:#241c10; color:#e8c46a; font-size:0.85rem; margin-bottom:12px;">
          {dd.DEMO_MODE_LABEL}
        </div>
        """,
        unsafe_allow_html=True,
    )

    game = gdv.find_demo_game(_selected_game_id)
    if game is None:
        comp.render_empty_state("ERROR", "Demo game not found.")
        st.stop()

    # Header (Section 26)
    h1, h2, h3 = st.columns(3)
    h1.markdown(f"### {game.away} @ {game.home}")
    h1.caption(f"{game.date} · {game.start_time}")
    h2.markdown("**Mode**")
    h2.markdown(comp.label_badge("DEMO", "research"), unsafe_allow_html=True)
    h3.markdown("**Game Status**")
    h3.markdown(comp.label_badge("SIMULATED", "research"), unsafe_allow_html=True)

    # Readiness strip (Section 27)
    st.markdown("#### Readiness")
    r1, r2, r3, r4 = st.columns(4)
    r1.markdown(f"Model: {comp.label_badge(game.model_ready, 'input' if game.model_ready == 'READY' else 'unavailable')}", unsafe_allow_html=True)
    r2.markdown(f"Starters: {comp.label_badge(game.starter_ready, 'input' if game.starter_ready in ('CONFIRMED', 'PROJECTED') else 'unavailable')}", unsafe_allow_html=True)
    r3.markdown(f"Markets: {comp.label_badge(game.market_ready, 'input' if game.market_ready == 'READY' else 'unavailable')}", unsafe_allow_html=True)
    r4.markdown(f"Data Health: {comp.label_badge('OK' if not game.warnings else 'STALE', 'input' if not game.warnings else 'unavailable')}", unsafe_allow_html=True)

    st.divider()

    from dashboard import eligible_bets as eb
    from dashboard import conviction as cv

    game_all_opps = eb.eligible_bets_for_game(game.away, game.home)
    game_actionable = game_all_opps["actionable"]
    game_research = game_all_opps["research_only"]
    legacy_game_opps = [o for o in dd.build_demo_opportunities() if o["team"] in (game.away, game.home)]

    tab_preview, tab_bets, tab_props, tab_stats, tab_trends, tab_model = st.tabs(
        ["PREVIEW", "BETS", "PLAYER PROPS", "STATS", "BETTING TRENDS", "MODEL"]
    )

    # ---- PREVIEW ------------------------------------------------------
    with tab_preview:
        st.markdown("#### Win Model")
        win = gdv.demo_win_model(game.away, game.home)
        if win is None:
            st.caption("Real Elo ratings unavailable for one or both teams in the historical corpus.")
        else:
            wc1, wc2 = st.columns(2)
            wc1.metric(f"{game.away} Win P", fmt.format_probability(win["away_win_p"]))
            wc2.metric(f"{game.home} Win P", fmt.format_probability(win["home_win_p"]))
            st.caption("Real Elo ratings (as of the end of the real historical corpus) applied to this "
                       "simulated matchup via the unmodified logistic win-probability formula. Fair "
                       "moneyline intentionally not shown -- no real sportsbook moneyline exists for this game.")

        st.markdown("#### Team SOG")
        tc1, tc2 = st.columns(2)
        for col, team, is_home in ((tc1, game.away, False), (tc2, game.home, True)):
            proj = gdv.team_sog_projection(team, is_home)
            with col:
                st.markdown(f"**{team}**")
                if proj:
                    st.metric("Expected SOG", f"{proj.get('expected_sog', 0):.1f}" if proj.get("expected_sog") else "—")
                else:
                    st.caption("Not available.")
        if win and all(gdv.team_sog_projection(t, h) for t, h in ((game.away, False), (game.home, True))):
            away_sog = gdv.team_sog_projection(game.away, False).get("expected_sog")
            home_sog = gdv.team_sog_projection(game.home, True).get("expected_sog")
            if away_sog and home_sog:
                st.caption(f"DERIVED DEMO INSIGHT (sum of two individually-projected team SOG values, "
                           f"NOT a validated betting market): combined expected SOG ≈ {away_sog + home_sog:.1f}. "
                           f"There is no validated GAME_TOTAL_SHOTS market in this engine.")

        st.markdown("#### Top Conviction — this game")
        game_conviction = [o for o in cv.top_conviction(eb.all_opportunities())
                            if o["team"] in (game.away, game.home)]
        if not game_conviction:
            st.caption("No Top Conviction opportunity for this game on today's simulated slate.")
        else:
            for o in game_conviction:
                st.caption(f"{o['player']} ({o['team']}) — {o['market']} {o['threshold']}: "
                           f"{fmt.format_probability(o['coherent_probability'])} model, "
                           f"edge {fmt.format_edge(o['conservative_edge'])}")

        context_players = gdv.game_context_players(_selected_game_id)
        if context_players:
            st.markdown("#### Context Active")
            for o in context_players:
                plain = comp.CONTEXT_STATE_PLAIN_LABEL.get(o["context_state"], o["context_state"])
                st.caption(f"{o['player']} ({o['team']}) — {plain}")

        st.markdown("#### Waiting On")
        reasons = gdv.game_wait_reasons(_selected_game_id)
        if not reasons:
            st.caption("Nothing outstanding.")
        else:
            for r in reasons:
                st.caption(f"- {r}")

    # ---- BETS -----------------------------------------------------------
    with tab_bets:
        st.markdown(f"#### All eligible bets — {game.away} @ {game.home}")
        if not game_actionable:
            comp.render_empty_state("NO_QUALIFYING_OPPORTUNITIES")
        else:
            sorted_rows = sorted(game_actionable, key=lambda o: -cv.conviction_score(o))
            for o in sorted_rows:
                with st.container(border=True):
                    r1, r2, r3, r4 = st.columns([2, 1, 1, 1])
                    if r1.button(f"{o['player']} ({o['team']}) — {o['market']} {o['threshold']}",
                                 key=f"gdbets_{o['market_id']}_{o['player_id']}"):
                        st.session_state["selected_player_id"] = o["player_id"]
                        st.switch_page("pages/25_Player_Intelligence.py")
                    r2.caption(f"Model {fmt.format_probability(o['coherent_probability'])}")
                    r3.caption(f"Edge {fmt.format_edge(o['conservative_edge'])}")
                    r4.markdown(comp.label_badge(o["decision"], "input"), unsafe_allow_html=True)

        # Combinations (Track 2): same-game combos scoped to this game's rosters,
        # reusing the real joint-dependence logic from the Combinations page.
        st.markdown("#### Combinations")
        _joint_results = da.load_json_safely("research/joint_scoring_dependence_results.json")
        if _joint_results is None:
            st.caption("Joint scoring dependence results not found.")
        else:
            combos = gdv.game_combinations(_selected_game_id, _joint_results["rho_by_name"])
            if not combos:
                st.caption("No qualifying same-game combinations for this matchup's demo roster.")
            for c in combos:
                oa, ob = c["leg_a"], c["leg_b"]
                legs_label = (f"{c['player']} {oa['market']} {oa['threshold']} + "
                              f"{c['player']} {ob['market']} {ob['threshold']}")
                with st.container(border=True):
                    st.markdown(f"**{legs_label}**")
                    if c["redundant"]:
                        st.warning(f"{oa['market']} 1+ already implies {ob['market']} 1+ — the joint "
                                   f"probability equals the smaller leg's own probability exactly, not "
                                   f"the product of the two legs.")
                    else:
                        comp.render_status_banner("VALIDATED", f"Joint model: {c['dependence_name']}")
                    cc1, cc2, cc3 = st.columns(3)
                    cc1.metric("Naive Independent P", fmt.format_probability(c["naive"]) if not c["redundant"] else "n/a")
                    cc2.metric("Validated Joint P", fmt.format_probability(c["validated"]))
                    cc3.metric("Dependence Effect", fmt.format_pp_delta(c["validated"] - c["naive"]) if not c["redundant"] else "—")
            st.caption("PROBABILITY MODEL: VALIDATED &nbsp;|&nbsp; PRICE: SIMULATED (UX review only) "
                       "&nbsp;|&nbsp; POLICY: DEMO ONLY — NOT OPERATIONAL", unsafe_allow_html=False)

        if game_research:
            with st.expander(f"Research-only / not actionable — {len(game_research)}"):
                for o in game_research:
                    st.caption(f"{o['player']} — {o['market']} {o['threshold']}: "
                               f"{o.get('decision_reason', 'not actionable')}")

    # ---- PLAYER PROPS -----------------------------------------------------
    with tab_props:
        st.markdown("#### Starters & Goalie Saves")
        goalies = [g_ for g_ in dd.build_demo_goalies() if g_["team"] in (game.away, game.home)]
        if not goalies:
            st.caption("No real goalie identity mapped for either team in the demo roster.")
        for g_ in goalies:
            gc1, gc2, gc3 = st.columns(3)
            gc1.markdown(f"**{g_['name']}** ({g_['team']})")
            gc1.caption(f"Starter: {g_['starter_status'].replace('_', ' ')} ({g_['starter_probability']*100:.0f}%) "
                        f"— Model Confidence: {g_['confidence']} (separate dimension)")
            gc2.metric("Expected Saves", f"{g_['expected_saves']:.1f}" if g_["expected_saves"] else "—")
            with gc3:
                for k, v in g_["thresholds"].items():
                    st.markdown(comp.label_badge(f"{k} {v.replace('_', ' ')}", "input" if v == "VALIDATED" else "unavailable"),
                                unsafe_allow_html=True)

        st.markdown("#### Top Player Opportunities")
        top = sorted(legacy_game_opps, key=lambda o: (-{"BET": 3, "WATCH": 2, "WAIT": 1, "PASS": 0}[o["decision"]],
                                                        -o["conservative_edge"]))[:6]
        for o in top:
            if st.button(f"Open {o['player']} — Player Intelligence", key=f"gd_player_{o['player_id']}_{o['prop']}"):
                st.session_state["selected_player_id"] = o["player_id"]
                st.switch_page("pages/25_Player_Intelligence.py")
            comp.render_opportunity_card({
                "player": o["player"], "team": o["team"], "opponent": o["opponent"], "market": o["market"],
                "threshold": o["threshold"], "decision": o["decision"], "confidence": o["confidence"],
                "raw_probability": o["raw_probability"], "context_adjusted_probability": o["context_adjusted_probability"],
                "conservative_probability": o["conservative_probability"],
                "market_no_vig_probability": o["market_no_vig_probability"], "fair_odds": o["fair_odds"],
                "current_odds": o["current_odds"], "max_acceptable_price": o["max_acceptable_price"],
                "conservative_edge": o["conservative_edge"], "ev": o["ev"], "context_state": o["context_state"],
                "context_raw": o["raw_probability"], "context_adjusted": o["context_adjusted_probability"],
                "context_delta": o["context_adjusted_probability"] - o["raw_probability"],
                "drivers": [], "risks": [o["decision_reason"]],
            })

        with st.expander(f"Full game prop table ({len(game_actionable) + len(game_research)} rows)"):
            all_game_rows = game_actionable + game_research
            gf1, gf2, gf3 = st.columns(3)
            team_f = gf1.selectbox("Team", ["ALL", game.away, game.home], key="gd_team_filter")
            decision_f = gf2.selectbox("Decision", ["ALL", "BET", "WATCH", "WAIT", "PASS", "RESEARCH_ONLY"], key="gd_decision_filter")
            conf_f = gf3.selectbox("Confidence", ["ALL", "HIGH", "MEDIUM", "LOW"], key="gd_conf_filter")
            filtered = all_game_rows
            if team_f != "ALL":
                filtered = [o for o in filtered if o["team"] == team_f]
            if decision_f != "ALL":
                filtered = [o for o in filtered if o["decision"] == decision_f]
            if conf_f != "ALL":
                filtered = [o for o in filtered if o["confidence"] == conf_f]
            table = [{"Decision": o["decision"], "Player": o["player"], "Market": o["market"],
                      "Threshold": o["threshold"], "Conservative P": fmt.format_probability(o["conservative_probability"]),
                      "Current": fmt.format_american_odds(o.get("current_odds")), "Confidence": o["confidence"]}
                     for o in filtered]
            st.dataframe(table, width="stretch")

    # ---- STATS --------------------------------------------------------------
    with tab_stats:
        st.markdown("#### Player Availability")
        for team_name in (game.away, game.home):
            st.markdown(f"**{team_name}**")
            team_players = {o["player_id"]: o["player"] for o in legacy_game_opps if o["team"] == team_name}
            if not team_players:
                st.caption("No roster mapped for this team in the demo roster.")
            for pid, name in team_players.items():
                activity = dd.player_activity_status(pid, team_name, game.home if team_name == game.away else game.away)
                st.caption(f"{name}: {activity.get('status', 'UNKNOWN')} ({activity.get('reason', 'UNKNOWN')})")
        st.caption("No verified injury feed exists in this engine — availability is PROJECTED_ACTIVE / "
                   "PROJECTED_INACTIVE / UNKNOWN only, never a fabricated diagnosis.")

    # ---- BETTING TRENDS -------------------------------------------------------
    with tab_trends:
        st.markdown("#### Simulated Market Movement")
        movement = dd.build_demo_market_movement(legacy_game_opps)
        if not movement:
            st.caption("No movement snapshots for this game's demo roster.")
        else:
            st.dataframe([{"Player": m["player"], "Market": m["market"],
                           "Opening (sim)": fmt.format_american_odds(m["opening"]),
                           "Current (sim)": fmt.format_american_odds(m["current"]),
                           "Model Fair": fmt.format_american_odds(m["model_fair"]),
                           "Direction": m["direction"]} for m in movement], width="stretch")
        st.caption("SIMULATED MARKET (DEMO ONLY) — deterministic synthetic movement, never a real "
                   "sportsbook line history.")

    # ---- MODEL --------------------------------------------------------------
    with tab_model:
        st.markdown("#### Readiness detail")
        st.caption(f"Model: {game.model_ready} · Starters: {game.starter_ready} · Markets: {game.market_ready}")
        if game.warnings:
            for w in game.warnings:
                st.caption(f"- {w}")
        st.markdown("#### Data Freshness")
        st.caption("Schedule: SIMULATED · Roster: real (demo roster) · Starter: SIMULATED · "
                   "Model: real frozen output · Odds: SIMULATED")
        st.caption("Every model probability shown for this game is the real, frozen production "
                   "model's own output for real NHL player identities on a simulated near-future "
                   "schedule. Prices are SIMULATED MARKET (DEMO ONLY).")

    comp.render_provenance_panel()
    st.stop()

try:
    records = _load_predictions()
except da.DataAvailabilityError as exc:
    comp.render_missing_data_page(exc)
    st.stop()

dates = da.available_dates(records)
default_game_id = st.session_state.get("selected_game_id")

col1, col2 = st.columns([1, 2])
with col1:
    default_date_idx = len(dates) - 1
    if default_game_id is not None:
        found = da.game_by_id(records, default_game_id)
        if found is not None and found["game_date"] in dates:
            default_date_idx = dates.index(found["game_date"])
    selected_date = st.selectbox("Date", dates, index=default_date_idx)
with col2:
    day_games = da.games_on_date(records, selected_date)
    labels = [f"{g['away_team']} @ {g['home_team']}" for g in day_games]
    default_game_idx = 0
    if default_game_id is not None:
        ids = [g["game_id"] for g in day_games]
        if default_game_id in ids:
            default_game_idx = ids.index(default_game_id)
    chosen_label = st.selectbox("Game", labels, index=default_game_idx if labels else 0) if labels else None

if not day_games or chosen_label is None:
    st.warning("No games on this date.")
    st.stop()

game = day_games[labels.index(chosen_label)]
st.session_state["selected_game_id"] = game["game_id"]

st.divider()
st.subheader(f"{game['away_team']} @ {game['home_team']}")
st.caption(f"{game['game_date']} · {game['period_type']} · Result: "
           f"{game['home_team']} {game['home_score']} – {game['away_team']} {game['away_score']}")

st.markdown("### Model output")
c1, c2, c3 = st.columns(3)
c1.metric(f"{game['home_team']} win probability", f"{game['p_home'] * 100:.1f}%")
c2.metric(f"{game['away_team']} win probability", f"{(1 - game['p_home']) * 100:.1f}%")
c3.metric("Confidence (display heuristic)", mv.confidence_label(game["p_home"]))
st.caption(
    "Confidence is a simple distance-from-50% heuristic for display, NOT the production "
    "uncertainty/CI band — that requires goalie-confirmation data unavailable in historical "
    "research mode. See README.md's dashboard section."
)

st.markdown("### Model contribution breakdown")
driver = mv.elo_diff_driver(game)
waterfall = pd.DataFrame([
    {"component": "Base team strength (Elo)", "value": driver["home_elo"] - driver["away_elo"]},
    {"component": "Home ice", "value": driver["home_advantage"]},
])
chart = alt.Chart(waterfall).mark_bar().encode(
    x=alt.X("component:N", title=None, sort=None),
    y=alt.Y("value:Q", title="Elo points (toward home)"),
    color=alt.condition(alt.datum.value > 0, alt.value("#5b8def"), alt.value("#e05c5c")),
)
st.altair_chart(chart, use_container_width=True)
st.caption(
    "PROBABILITY DRIVERS, not causal attribution. Player / goalie / rest contributions: "
    + comp.NOT_AVAILABLE + " — no real roster or schedule-event data exists for this "
    "historical game in research mode."
)

st.markdown("### Team context")
conn = _moneypuck_conn()
tab1, tab2 = st.tabs([game["home_team"], game["away_team"]])
for tab, team in ((tab1, game["home_team"]), (tab2, game["away_team"])):
    with tab:
        team_history = [r for r in da.games_for_team(records, team) if r["game_date"] < game["game_date"]]
        recent5 = team_history[-5:]
        recent10 = team_history[-10:]

        def _record(games):
            wins = sum(1 for r in games if (r["home_team"] == team and r["actual_home_win"] == 1.0)
                       or (r["away_team"] == team and r["actual_home_win"] == 0.0))
            return f"{wins}-{len(games) - wins}"

        st.markdown(f"{comp.label_badge('MODEL INPUT', 'input')}", unsafe_allow_html=True)
        cc1, cc2 = st.columns(2)
        cc1.metric("Last 5 record", _record(recent5) if recent5 else "N/A")
        cc2.metric("Last 10 record", _record(recent10) if recent10 else "N/A")

        if recent10:
            df = pd.DataFrame([
                {"game_date": r["game_date"],
                 "elo": r["rating_home_pregame"] if r["home_team"] == team else r["rating_away_pregame"]}
                for r in recent10
            ])
            st.altair_chart(
                alt.Chart(df).mark_line(point=True).encode(
                    x="game_date:T", y=alt.Y("elo:Q", title="Elo rating", scale=alt.Scale(zero=False)),
                ),
                use_container_width=True,
            )

        st.markdown(f"{comp.label_badge(comp.RESEARCH_METRIC, 'research')}", unsafe_allow_html=True)
        if conn is None:
            st.caption("MoneyPuck research DB not found — research context unavailable.")
        else:
            ctx = mv.moneypuck_context(conn, team, game["game_date"], game["season"], window=25)
            rc1, rc2, rc3 = st.columns(3)
            rc1.metric("5v5 xG share (25g)", f"{ctx['xg_share_5v5']:.3f}" if ctx["xg_share_5v5"] is not None else "N/A")
            rc2.metric("Offense xGF/60 (25g)", f"{ctx['offense_xgf60']:.2f}" if ctx["offense_xgf60"] is not None else "N/A")
            rc3.metric("Defense xGA/60 (25g)", f"{ctx['defense_xga60']:.2f}" if ctx["defense_xga60"] is not None else "N/A")
            rc4, rc5 = st.columns(2)
            rc4.metric("PP xGF/60 (25g)", f"{ctx['pp_xgf60']:.2f}" if ctx["pp_xgf60"] is not None else "N/A")
            rc5.metric("PK xGA/60 (25g)", f"{ctx['pk_xga60']:.2f}" if ctx["pk_xga60"] is not None else "N/A")
            st.caption("Tested in research — none of these are currently used by the production model. "
                       "See Research Lab for why.")

comp.render_provenance_panel()
