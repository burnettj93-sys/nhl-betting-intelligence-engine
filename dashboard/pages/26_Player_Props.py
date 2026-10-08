"""Best Options — for each player or goalie with a fresh DraftKings price, the best qualifying +100 option: a single when one
exists, otherwise a two-leg cross-game parlay. Each distinct option appears once with everyone it is best for. "Add to paper
book — $10" is the only action here, and it acts only when pressed."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import product_source as ps
from dashboard import ui

ui.header("Best Options", "The best +100 option per person today, with prices, quote age, chances and returns.")
tk = ui.load(ps.tickets, "The ticket board")
opts_doc = tk.get("options")
if not opts_doc:
    ui.unavailable("No best-option document has been built for today yet; it is written with the ticket board by the paper-trader job.", "Best options")
    st.stop()
options = opts_doc["options"]
cash = tk["account"]["available_cash"]
pol = opts_doc.get("policy") or {}
st.caption(f"Options built {ui.et_time(opts_doc.get('generated_at_utc') or tk['generated_at_utc'], True)} for Eastern date {opts_doc.get('date_et')}. "
           f"Rule: +100 or better, estimated edge at least {pol.get('min_estimated_ev', 0.05):.0%}, still positive after lowering each leg's probability by "
           f"{pol.get('leg_probability_margin', 0.03) * 100:.0f} points. A single is a DraftKings quote; a parlay price is the product of its legs' prices (an estimate). "
           "Prices are US-feed quotes, not verified for Ontario.")
if not options:
    ui.banner(ui.esc(tk.get("empty_slot_reason") or "No person has a fresh price that qualifies right now."), "muted")
    st.caption("DraftKings player prices are captured within about five hours of puck drop, so options appear in the afternoon. Nothing is recorded by looking at this page.")
    st.stop()

f = st.columns([3, 2, 2])
q = f[0].text_input("Find a player", key="bo_q")
kind = f[1].selectbox("Type", ["All", "Singles", "Parlays"], key="bo_kind")
order = f[2].selectbox("Sort by", ["Best value", "Highest chance", "Biggest return"], key="bo_sort")
show = [o for o in options if (not q or any(q.lower() in p["name"].lower() for p in o["best_for"]) or any(q.lower() in l["label"].lower() for l in o["legs"]))
        and (kind == "All" or (kind == "Singles") == (o["kind"] == "SINGLE"))]
show.sort(key={"Best value": lambda o: -o["ev_after_haircut"], "Highest chance": lambda o: -o["hit_probability"], "Biggest return": lambda o: -o["potential_return"]}[order])
d = opts_doc.get("diagnostics") or {}
st.caption(f"{len(show)} of {len(options)} distinct option(s) · {d.get('people_with_option', '?')} of {d.get('people_with_priced_props', '?')} people with a fresh price have an option.")
for o in show[:60]:
    ui.option_card(o, key=f"bo_{o['option_id']}", cash=cash, page_generated_at=tk["generated_at_utc"])
if len(show) > 60:
    st.caption(f"Showing the best 60 of {len(show)}; use the search box to find others.")
