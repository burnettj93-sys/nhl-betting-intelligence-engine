"""Paper Performance — the one $500 paper account, with automatic and manually added tickets reported separately and together."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import product_source as ps
from dashboard import ui

ui.header("Paper Performance", "What the paper account has done: shared cash, results by origin, and every settled ticket.")
perf = ui.load(ps.performance, "Paper performance")
acct, summ, origins = perf["account"], perf["summary"], perf["origins"]

a = st.columns(5)
a[0].metric("Available cash", ui.money(acct["available_cash"]))
a[1].metric("Open stakes", ui.money(acct["open_stakes"]), f"{acct['open_tickets']} open", delta_color="off")
a[2].metric("Equity", ui.money(acct["equity"]))
a[3].metric("Settled P&L", ui.signed_money(acct["settled_pnl"]))
a[4].metric("ROI on settled", ui.pct(summ["roi"], 1) if summ.get("roi") is not None else "—")
st.caption("One account: \\$500 start, \\$10 per ticket, no top-ups. Cash returns as tickets settle. Hit rates and ROI on a handful of tickets say almost nothing about skill.")

st.subheader("By origin")
rows = []
for key, label in (("AUTOMATIC", "Automatic (engine)"), ("MANUALLY_ADDED", "Manually added"), ("ALL", "All tickets")):
    o = origins[key]
    rows.append({"Origin": label, "Tickets": o["tickets"], "Settled": o["settled"], "Won": o["wins"], "Lost": o["losses"], "Void": o["voids"],
                 "Open": o["pending"] + o["unresolved"], "Open stake": ui.money(o["open_stake"]), "Settled stake": ui.money(o["settled_stake"]),
                 "Settled P&L": ui.signed_money(o["settled_pnl"]), "ROI": ui.pct(o["roi"], 1) if o["roi"] is not None else "—",
                 "Hit rate": ui.pct(o["hit_rate"]) if o["hit_rate"] is not None else "—"})
st.dataframe(rows, hide_index=True, width="stretch")
st.caption("Manually added tickets never take one of the five daily automatic slots and are never mixed into the engine's own record. Unresolved tickets stay open until their settlement rules can be applied.")

hist = summ.get("bankroll_history") or []
if hist:
    st.subheader("Bankroll after each settled ticket")
    st.line_chart([{"Settled ticket #": i, "Bankroll": h["bankroll"]} for i, h in enumerate(hist)], x="Settled ticket #", y="Bankroll")

tab_all, tab_auto, tab_manual = st.tabs(["All", "Automatic", "Manually added"])
for tab, key in ((tab_all, "ALL"), (tab_auto, "AUTOMATIC"), (tab_manual, "MANUALLY_ADDED")):
    with tab:
        bd = perf["breakdowns"].get(key) or {}
        if not origins[key]["tickets"]:
            st.caption("No tickets yet.")
            continue
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**By market family**")
            st.dataframe([{"Market": k, **v} for k, v in bd.get("by_market_family", {}).items()], hide_index=True, width="stretch")
            st.markdown("**By straight vs combo**")
            st.dataframe([{"Type": k, **v} for k, v in bd.get("by_straight_vs_combo", {}).items()], hide_index=True, width="stretch")
        with c2:
            st.markdown("**By price range**")
            st.dataframe([{"Odds range": k, **v} for k, v in bd.get("by_odds_range", {}).items()], hide_index=True, width="stretch")
st.caption("Full ticket-by-ticket detail, including legs, prices and postmortems, is on Ticket History.")
