"""Page 29 — Market Movement (Preseason Interactive Product sprint,
Parts 82-86). DEMO mode: deterministic simulated movement snapshots
around the real frozen model's fair price. LIVE mode would use only
real captured provider snapshots -- none exist yet, so LIVE mode here
shows the honest waiting state, never invented history."""
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

st.title("Market Movement")
comp.render_model_status_header()
comp.render_global_search(key_prefix="movement")
st.markdown(
    f"""
    <div style="border:1px solid #5a4420; border-radius:6px; padding:8px 12px;
                background:#241c10; color:#e8c46a; font-size:0.85rem; margin-bottom:12px;">
      {dd.DEMO_MODE_LABEL} — SIMULATED MARKET HISTORY. No real DraftKings line-movement history
      exists yet; every row below previews the intended layout using deterministic simulated
      snapshots around the real model's fair price.
    </div>
    """,
    unsafe_allow_html=True,
)

opportunities = dd.build_demo_opportunities()
movement = dd.build_demo_market_movement(opportunities)

if not movement:
    comp.render_empty_state("NO_LIVE_MARKETS", "WAITING FOR LIVE NHL MARKET HISTORY")
else:
    rows = []
    for m in movement:
        rows.append({
            "Player / Market": f"{m['player']} — {m['market']}",
            "Opening": fmt.format_american_odds(m["opening"]),
            "Current": fmt.format_american_odds(m["current"]),
            "Model Fair": fmt.format_american_odds(m["model_fair"]),
            "Direction": m["direction"],
            "CLV": "n/a — no closing line yet",
        })
    st.dataframe(rows, width="stretch")
    st.caption("SIMULATED MARKET HISTORY — every row above is a deterministic demo snapshot, not a "
               "real captured price movement.")

comp.render_provenance_panel()
