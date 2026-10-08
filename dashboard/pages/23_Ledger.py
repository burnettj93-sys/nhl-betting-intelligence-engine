"""Ticket History — every ticket in the paper account, both origins, with its legs, frozen prices and result."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import product_source as ps
from dashboard import ui

ui.header("Ticket History", "Every recorded ticket with the prices and probabilities frozen when it was recorded.")
perf = ui.load(ps.performance, "Ticket history")
bets = list(reversed(perf["bets"]))
f = st.columns(3)
origin = f[0].selectbox("Origin", ["All", "Automatic", "Manually added"], key="th_origin")
status = f[1].selectbox("Status", ["All", "Open", "Won", "Lost", "Void", "Unresolved"], key="th_status")
q = f[2].text_input("Find a player", key="th_q")
want_origin = {"Automatic": "AUTOMATIC", "Manually added": "MANUALLY_ADDED"}.get(origin)
want_status = {"Open": {"PENDING"}, "Won": {"WIN"}, "Lost": {"LOSS"}, "Void": {"VOID"}, "Unresolved": {"UNRESOLVED"}}.get(status)


def legs_of(b):
    return json.loads(b.get("legs_json") or "[]")


rows = [b for b in bets if (not want_origin or (b.get("origin") or "AUTOMATIC") == want_origin) and (not want_status or b["result_status"] in want_status)
        and (not q or any(q.lower() in (l.get("participant_name") or "").lower() for l in legs_of(b)))]
st.caption(f"{len(rows)} of {len(bets)} ticket(s).")
if not rows:
    st.info("No tickets match.")
for b in rows:
    legs = legs_of(b)
    names = " + ".join(f"{l.get('participant_name')} {l.get('threshold')}+ ({l.get('market_family', '').replace('PLAYER_', '').replace('_ALTERNATE', '').lower()})" for l in legs)
    res = {"WIN": "WON", "LOSS": "LOST"}.get(b["result_status"], b["result_status"])
    with st.expander(f"{b['paper_bet_id']} · {names} · {res.title()}" + (f" {ui.signed_money(b['profit_loss'])}" if b.get("profit_loss") is not None else "")):
        st.markdown(ui.status_chip(res) + ui.status_chip(b.get("origin") or "AUTOMATIC"), unsafe_allow_html=True)
        st.dataframe([{"Leg": f"{l.get('participant_name')} {l.get('threshold')}+", "Game": l.get("game_id"), "Price": ui.american(l.get("american_price")),
                       "Quote updated": ui.et_time(l.get("quote_updated_utc") or l.get("captured_at_utc"), True), "Model chance": ui.pct(l.get("conservative_probability")),
                       "Model": l.get("model_version", "")} for l in legs], hide_index=True, width="stretch")
        st.caption(f"Recorded {ui.et_time(b['created_at_utc'], True)} · stake {ui.money(b['stake'])} · {'estimated combined' if len(legs) > 1 else 'quoted'} price {ui.american(b['entry_odds'])} · "
                   f"model hit chance {ui.pct(b.get('model_probability'))} · settled {ui.et_time(b.get('settled_at_utc'), True) if b.get('settled_at_utc') else 'not yet'}")
        if b.get("provenance_json"):
            pv = json.loads(b["provenance_json"])
            st.caption(f"Manually added: order {pv.get('order_id')} from {pv.get('source')}, revalidated {ui.et_time(pv.get('revalidated_at_utc'), True)}. {pv.get('jurisdiction_note', '')}")
        if b["result_status"] == "LOSS":
            st.caption("The postmortem for this loss (what each leg did against its price and probability, closing prices, defect checks) is on Morning Review.")
        if b.get("settlement_json"):
            sj = json.loads(b["settlement_json"])
            st.json(sj, expanded=False)
st.caption("Daily reviews and postmortems for settled tickets are on Morning Review.")
