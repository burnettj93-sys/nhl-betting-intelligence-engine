"""Page 33 — Paper Performance (Live DK / Paper Bankroll completion
sprint, 2026-08-31, Parts 24-49). Answers the owner's own question:
"If this program had actually put $10 on every BET recommendation, what
would the bankroll be?" -- from immutable stored paper-bet entries and
settlements only, never recomputed from today's current odds.

Three economic tracks, never mixed: REAL_MARKET_PAPER (real, verified
DraftKings prices only), DEMO_PAPER (deterministic simulated demo
prices), and REAL_BET (the existing operational/prospective_ledger.py
-- untouched here, currently and correctly empty)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import components as comp
from dashboard import formatting as fmt
from dashboard import paper_performance_view as ppv
from operational import paper_bankroll as pb

st.title("Paper Performance")
comp.render_model_status_header()
st.caption("Theoretical bankroll tracking: what would have happened if this engine's own BET "
           "recommendations had each received a flat $10 paper wager. Never a real-money bet.")

state = ppv.full_dashboard_state()

TRACK_LABEL = {"REAL_MARKET_PAPER": "Real-Market Paper (real DraftKings prices)",
               "DEMO_PAPER": "Demo Paper (simulated prices)"}

tab_real, tab_demo = st.tabs([TRACK_LABEL["REAL_MARKET_PAPER"], TRACK_LABEL["DEMO_PAPER"]])

for tab, track in ((tab_real, "REAL_MARKET_PAPER"), (tab_demo, "DEMO_PAPER")):
    with tab:
        data = state[track]
        summary, breakdowns, bets = data["summary"], data["breakdowns"], data["bets"]

        # st.markdown treats a "$...$" pair as inline LaTeX -- escape every
        # literal dollar sign so "$500.00 ... $0.00" renders as plain
        # text instead of being parsed as math notation.
        st.markdown(f"#### {data['answer'].replace('$', chr(92) + '$')}")

        h1, h2, h3, h4 = st.columns(4)
        h1.metric("Starting Bankroll", f"${summary['starting_bankroll']:,.2f}")
        h2.metric("Current Bankroll", f"${summary['current_bankroll']:,.2f}",
                   delta=f"{summary['net_profit']:+,.2f}")
        h3.metric("ROI", fmt.format_probability(summary["roi"]) if summary["roi"] is not None else "—")
        h4.metric("Record", f"{summary['wins']}-{summary['losses']}-{summary['voids']}"
                             f" ({summary['pending']} pending)")

        h5, h6, h7, h8 = st.columns(4)
        h5.metric("Hit Rate", fmt.format_probability(summary["hit_rate"]) if summary["hit_rate"] is not None else "—")
        h6.metric("Total Staked", f"${summary['total_staked']:,.2f}")
        h7.metric("Max Drawdown", f"${summary['max_drawdown']:,.2f}")
        h8.metric("Streak", f"{summary['current_streak_length']} {summary['current_streak_type'] or '—'}")

        if bets and summary["bets"] > 0 and (summary["wins"] + summary["losses"] + summary["voids"]) == 0:
            st.caption("Every paper bet recorded so far is still PENDING -- no real 2026-27 game has "
                       "been played yet, so nothing has settled. This is the expected pre-season state, "
                       "not an error.")
        elif summary["bets"] == 0:
            comp.render_empty_state(
                "NO_QUALIFYING_OPPORTUNITIES",
                "No BET-grade opportunity has existed on this track yet to paper-bet -- WAITING FOR "
                "SETTLED REAL RECOMMENDATIONS." if track == "REAL_MARKET_PAPER" else
                "No BET-grade opportunity on today's simulated slate to paper-bet.")

        if summary["bankroll_history"]:
            st.markdown("#### Bankroll History")
            chart_rows = [{"Bet #": i, "Bankroll": h["bankroll"]}
                          for i, h in enumerate(summary["bankroll_history"])]
            st.line_chart(chart_rows, x="Bet #", y="Bankroll")

        st.markdown("#### Performance Breakdowns")
        bd1, bd2 = st.columns(2)
        with bd1:
            st.markdown("**By Market Family**")
            st.dataframe([{"Market": k, **v} for k, v in breakdowns["by_market_family"].items()], width="stretch")
            st.markdown("**By Confidence**")
            st.dataframe([{"Confidence": k, **v} for k, v in breakdowns["by_confidence"].items()], width="stretch")
            st.markdown("**By Edge Bucket**")
            st.dataframe([{"Edge": k, **v} for k, v in breakdowns["by_edge_bucket"].items()], width="stretch")
        with bd2:
            st.markdown("**By Odds Range**")
            st.dataframe([{"Odds Range": k, **v} for k, v in breakdowns["by_odds_range"].items()], width="stretch")
            st.markdown("**Top Conviction vs Other**")
            st.dataframe([{"Group": k, **v} for k, v in breakdowns["by_top_conviction"].items()], width="stretch")
            st.markdown("**Straight vs Combo**")
            st.dataframe([{"Group": k, **v} for k, v in breakdowns["by_straight_vs_combo"].items()], width="stretch")

        with st.expander(f"Full paper bet log ({len(bets)})"):
            table = [{
                "Date": b.get("game_date"), "Player/Team": b.get("player_name_snapshot") or b.get("team"),
                "Market": f"{b.get('market_family') or b.get('market_id')} {b.get('threshold') or ''}".strip(),
                "Price Source": b.get("price_source"), "Model P": fmt.format_probability(b.get("model_probability")),
                "Odds": fmt.format_american_odds(b.get("entry_odds")), "Stake": f"${b['stake']:.2f}",
                "Result": b.get("result_status"),
                "P/L": f"${b['profit_loss']:.2f}" if b.get("profit_loss") is not None else "—",
                "Closing Odds": fmt.format_american_odds(b.get("closing_odds")) if b.get("closing_odds") else "—",
                "CLV": fmt.format_pp_delta(b.get("clv")) if b.get("clv") is not None else "WAITING",
                "Bankroll After": "—",
            } for b in bets]
            if table:
                st.dataframe(table, width="stretch")
            else:
                st.caption("No paper bets recorded yet.")

st.divider()
st.markdown("#### Real Bets")
st.caption("REAL_BET is tracked entirely separately in operational/prospective_ledger.py and is "
           "currently and correctly empty -- no real money has been wagered by this engine.")

comp.render_provenance_panel()
