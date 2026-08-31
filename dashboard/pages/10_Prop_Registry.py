"""Page 10 — Prop Registry: one status per player-prop family, so the
dashboard never implies every prop is equally mature. See
MULTI_PROP_RESEARCH_REPORT.md."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import components as comp
from research.player_props import registry

STATUS_COLOR = {
    "VALIDATED": "#3ecf8e", "PARTIAL": "#e8b84f", "PROMISING": "#6ad0e8", "RESEARCH": "#e8b84f",
    "REJECTED": "#8c99a8", "BLOCKED": "#f0654f", "UNSUPPORTED_MARKET": "#8c99a8",
    "EMPIRICAL_BASELINE_REMAINS_CHAMPION": "#8ec4f5",
}
LIVE_COLOR = {"CONNECTED": "#3ecf8e", "WAITING_FOR_MARKET": "#e8b84f", "NOT_CURRENTLY_AVAILABLE": "#8c99a8"}

st.title("Prop Registry")
comp.render_model_status_header()
st.caption("Every player-prop family this engine has researched or built, with its real status. "
           "See MULTI_PROP_RESEARCH_REPORT.md for the full evidence behind each entry.")

validated = registry.validated_prop_families()
st.metric("Validated prop families", len(validated), help=", ".join(validated))

for entry in registry.REGISTRY:
    status_color = STATUS_COLOR.get(entry.model_status, "#8c99a8")
    live_color = LIVE_COLOR.get(entry.live_market_support, "#8c99a8")
    st.markdown(
        f"### {entry.market_type.replace('_', ' ').title()} "
        f"<span style='color:{status_color}; font-family:monospace; "
        f"font-size:0.8em; border:1px solid currentColor; border-radius:4px; padding:1px 6px;'>"
        f"{entry.model_status}</span>",
        unsafe_allow_html=True,
    )
    c1, c2 = st.columns([1, 3])
    with c1:
        st.markdown(f"**Live pricing:** <span style='color:{live_color}'>"
                    f"{entry.live_market_support}</span>", unsafe_allow_html=True)
        st.caption(f"Odds API key: `{entry.odds_api_market_key}`" if entry.odds_api_market_key else "No documented Odds API market key")
    with c2:
        st.caption(entry.summary)
    st.divider()

comp.render_provenance_panel()
