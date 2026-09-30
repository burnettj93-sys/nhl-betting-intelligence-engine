"""Page 26 — Player Props (Platform Recovery block, 2026-09-29,
superseding the earlier demo-first Preseason Interactive Product sprint).
Default view is REAL: every currently-eligible PLAYER_SOG_ALTERNATE leg
(real DraftKings price, real model probability, real freshness) --
today's only real production prop family (research/real_market_parlay/
real_slate_adapter.py's own certified path, the same one Today's real
bridge uses). REAL OR EMPTY: an honest empty state is shown when nothing
qualifies -- never backfilled with a simulated substitute. The prior
demo board (every market family, simulated prices) is retained for
illustrating the model/decision machinery, but lives behind its own
explicit, collapsed, clearly-labeled expander -- it does not occupy the
normal Player Props workflow."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import cloud_snapshot
from dashboard import components as comp
from dashboard import formatting as fmt
from operational import runtime_mode


@st.cache_data(show_spinner="Building real player props (real SOG legs)...", ttl=300, max_entries=4)
def _cached_local_real_player_props_state():
    # Mirrors dashboard/pages/21_Today.py's own _cached_local_real_today_state --
    # real_player_props_view.build_real_player_props_state() re-runs the real
    # model projection for every real archived SOG payload, which is too slow
    # to redo on every page rerun.
    from operational import real_today_bridge
    return real_today_bridge.open_real_player_props_state()


st.title("Player Props")
comp.render_model_status_header()
comp.render_global_search(key_prefix="props")

st.markdown(
    """
    <div style="border:1px solid #1f4d2e; border-radius:6px; padding:8px 12px;
                background:#0f2417; color:#7fd99a; font-size:0.85rem; margin-bottom:12px;">
      <b>LIVE — REAL DRAFTKINGS DATA.</b> Every row below is a real, currently-eligible
      PLAYER_SOG_ALTERNATE leg (real price, real model probability, real freshness) —
      the only market family with a live-tested real payload contract today. A
      Demo / Model Showcase illustrating every other market family (simulated prices)
      is available in its own collapsed section further down.
    </div>
    """,
    unsafe_allow_html=True,
)

try:
    if runtime_mode.is_community_cloud():
        _real_props = cloud_snapshot.real_player_props()
    else:
        _real_props = _cached_local_real_player_props_state()
except cloud_snapshot.SnapshotUnavailable as _exc:
    _real_props = None
    st.caption(f"Real Player Props data is not available in this snapshot ({_exc}).")

if _real_props is not None:
    _rows = _real_props["rows"]
    st.caption(f"{_real_props['provenance']} — {len(_rows)} real eligible leg(s). "
               f"Generated {_real_props['generated_at_utc'][:16]}.")
    if not _rows:
        comp.render_empty_state("NO_QUALIFYING_OPPORTUNITIES",
                                 "No real, currently eligible PLAYER_SOG_ALTERNATE leg qualifies right now — "
                                 "that is a real, honest result, not an error.")
    else:
        table = [{
            "Player": r["player"], "Team": r["team"] or "—", "Opponent": r["opponent"] or "—",
            "Market": r["market"], "Threshold": r["threshold"], "Side": r["side"],
            "DK Price": fmt.format_american_odds(r["dk_price"]),
            "Conservative P": fmt.format_probability(r["conservative_probability"]),
            "Fair Odds": fmt.format_american_odds(r["fair_odds"]),
            "Implied P": fmt.format_probability(r["implied_probability"]),
            "Edge": fmt.format_edge(r["edge"]),
            "Captured": (r["captured_at_utc"] or "—")[:19],
            "Sportsbook": r["sportsbook"], "Status": r["status"],
        } for r in _rows]
        st.dataframe(table, width='stretch')

    if _real_props["excluded_count"]:
        with st.expander(f"Why {_real_props['excluded_count']} real candidate(s) were excluded"):
            for reason, count in sorted(_real_props["excluded_by_reason"].items(), key=lambda kv: -kv[1]):
                st.caption(f"{count} — {reason}")

st.divider()

with st.expander("Demo / Model Showcase — SIMULATED, not the real product (click to expand)"):
    st.caption("Everything below uses a fixed SIMULATED slate and SIMULATED sportsbook prices to illustrate "
               "the model/decision machinery across every market family this engine has modeled. Never "
               "DraftKings, never live/verified. For the real product, see the section above.")

    from dashboard import demo_data as dd
    from research.model_registry import get as get_model_registry_entry

    st.markdown("### Demo Player Props — SIMULATED")
    opportunities = dd.build_demo_opportunities()

    _MODEL_ID_BY_MARKET_FAMILY = {"SOG": "PLAYER_SOG", "GOALS": "GOALS", "ASSISTS": "ASSISTS",
                                  "POINTS": "POINTS", "BLOCKED_SHOTS": "BLOCKED_SHOTS"}

    def _validation_status(market_family: str) -> str:
        entry = get_model_registry_entry(_MODEL_ID_BY_MARKET_FAMILY.get(market_family, ""))
        return entry.status if entry else "RESEARCH"

    f1, f2, f3, f4 = st.columns(4)
    markets = sorted({o["market"] for o in opportunities})
    market_filter = f1.selectbox("Market", ["ALL"] + markets,
                                  index=(["ALL"] + markets).index(st.session_state.get("selected_market_filter", "ALL"))
                                  if st.session_state.get("selected_market_filter") in markets else 0)
    decision_filter = f2.selectbox("Decision", ["ALL", "BET", "WATCH", "WAIT", "PASS"])
    confidence_filter = f3.selectbox("Confidence", ["ALL", "HIGH", "MEDIUM", "LOW"])
    sort_by = f4.selectbox("Sort", ["Best Actionable", "Conservative Edge", "EV", "Confidence"])

    f5, f6, f7, f8, f9 = st.columns(5)
    players = sorted({o["player"] for o in opportunities})
    player_filter = f5.selectbox("Player", ["ALL"] + players)
    teams = sorted({o["team"] for o in opportunities})
    team_filter = f6.selectbox("Team", ["ALL"] + teams)
    validation_filter = f7.selectbox("Validation Status", ["ALL", "VALIDATED", "PARTIAL", "RESEARCH",
                                                            "EMPIRICAL_BASELINE_REMAINS_CHAMPION"])
    context_filter = f8.selectbox("Context", ["ALL", "CONTEXT ACTIVE", "NO CONTEXT"])
    price_filter = f9.selectbox("Price", ["ALL", "PRICE AVAILABLE", "NO PRICE", "STALE"])

    rows = opportunities
    if market_filter != "ALL":
        rows = [o for o in rows if o["market"] == market_filter]
    if decision_filter != "ALL":
        rows = [o for o in rows if o["decision"] == decision_filter]
    if confidence_filter != "ALL":
        rows = [o for o in rows if o["confidence"] == confidence_filter]
    if player_filter != "ALL":
        rows = [o for o in rows if o["player"] == player_filter]
    if team_filter != "ALL":
        rows = [o for o in rows if o["team"] == team_filter]
    if validation_filter != "ALL":
        rows = [o for o in rows if _validation_status(o["market"]) == validation_filter]
    if context_filter == "CONTEXT ACTIVE":
        rows = [o for o in rows if o["context_state"]]
    elif context_filter == "NO CONTEXT":
        rows = [o for o in rows if not o["context_state"]]
    if price_filter == "PRICE AVAILABLE":
        rows = [o for o in rows if o["current_odds"] is not None]
    elif price_filter == "NO PRICE":
        rows = [o for o in rows if o["current_odds"] is None]
    elif price_filter == "STALE":
        rows = []  # demo prices carry no real staleness signal -- honestly empty, never fabricated

    _decision_rank = {"BET": 0, "WATCH": 1, "WAIT": 2, "PASS": 3}
    if sort_by == "Best Actionable":
        rows = sorted(rows, key=lambda o: (_decision_rank.get(o["decision"], 9), -o["conservative_edge"]))
    elif sort_by == "Conservative Edge":
        rows = sorted(rows, key=lambda o: -o["conservative_edge"])
    elif sort_by == "EV":
        rows = sorted(rows, key=lambda o: -(o["ev"] or -99))
    else:
        rows = sorted(rows, key=lambda o: {"HIGH": 0, "MEDIUM": 1, "LOW": 2}.get(o["confidence"], 9))

    st.caption(f"{len(rows)} opportunities (of {len(opportunities)} total demo props)")

    demo_view = st.radio("View", ["Table", "Cards"], horizontal=True, key="demo_props_view")
    if not rows:
        comp.render_empty_state("NO_QUALIFYING_OPPORTUNITIES")
    elif demo_view == "Table":
        demo_table = [{
            "Decision": o["decision"], "Player": o["player"], "Opponent": o["opponent"], "Market": o["market"],
            "Threshold": o["threshold"], "Conservative P": fmt.format_probability(o["conservative_probability"]),
            "Market P": fmt.format_probability(o["market_no_vig_probability"]),
            "Current": fmt.format_american_odds(o["current_odds"]), "Max Buy": fmt.format_american_odds(o["max_acceptable_price"]),
            "Edge": fmt.format_edge(o["conservative_edge"]), "Confidence": o["confidence"],
            "Context": o["context_state"] or "—",
        } for o in rows]
        st.dataframe(demo_table, width='stretch')
        st.caption("Select Cards view, then 'Odds detail', to inspect a single opportunity's full pricing breakdown.")
    else:
        for o in rows[:60]:
            with st.container(border=True):
                bc1, bc2 = st.columns([1, 1])
                if bc1.button(o["player"], key=f"pp_{o['player_id']}_{o['prop']}"):
                    st.session_state["selected_player_id"] = o["player_id"]
                    st.switch_page("pages/25_Player_Intelligence.py")
                odds_key = f"pp_odds_{o['player_id']}_{o['prop']}"
                show_odds = bc2.button("Odds detail", key=odds_key)
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
                if show_odds:
                    comp.render_odds_detail_panel(o)

comp.render_provenance_panel()
