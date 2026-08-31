"""Page 9 — Data Status: is today's hockey data current, and exactly
when did the engine obtain it? Reads a cached snapshot only — see
DAILY_OPERATIONAL_SYNC_REPORT.md."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import components as comp
from dashboard import data_status_view as dv

st.title("Data Status")
comp.render_model_status_header()
st.markdown(
    """
    <div style="border:1px solid #232d38; border-radius:6px; padding:8px 12px;
                background:#10151c; color:#8c99a8; font-size:0.85rem; margin-bottom:12px;">
      Answers "do I have all of today's required hockey data, and exactly when did I obtain it?"
      Reads a cached snapshot only — this page never makes a network call. Refresh explicitly with:
      <code>python3 sync_daily.py</code>
    </div>
    """,
    unsafe_allow_html=True,
)

cache = dv.load_readiness_cache()
if cache is None:
    st.info("No sync has been run yet. Run `python3 sync_daily.py` to populate this page.")
    st.stop()

readiness = cache["readiness"]
nhl = cache["nhl_sync"]
mp = cache["moneypuck_sync"]

st.caption(f"Last sync generated at (UTC): {readiness['generated_at_utc']}")

STATUS_COLOR = {"CURRENT": "#3ecf8e", "PROJECTED": "#3ecf8e", "NO_CHANGE": "#3ecf8e",
                 "STALE": "#e8b84f", "NOT_REFRESHED": "#e8b84f",
                 "UNAVAILABLE": "#f0654f", "REQUIRES_PERMISSION": "#f0654f"}


def badge(status: str) -> str:
    color = STATUS_COLOR.get(status, "#8c99a8")
    return f'<span style="color:{color}; font-family:monospace; font-weight:600;">{status}</span>'


rows = [
    ("NHL Schedule", readiness["nhl_schedule"]["status"], readiness["nhl_schedule"].get("window", "")),
    ("NHL Results", readiness["nhl_results"]["status"],
     f"{readiness['nhl_results'].get('games_finalized_this_run', 0)} finalized this run"),
    ("MoneyPuck Team", readiness["moneypuck_team"]["status"], readiness["moneypuck_team"].get("reason", "")),
    ("MoneyPuck Skater", readiness["moneypuck_skater"]["status"], readiness["moneypuck_skater"].get("reason", "")),
    ("MoneyPuck Goalie", readiness["moneypuck_goalie"]["status"], readiness["moneypuck_goalie"].get("reason", "")),
    ("Odds (The Odds API)", readiness["odds"]["status"], readiness["odds"].get("reason", "")),
    ("Starter Intelligence", readiness["starter_intelligence"]["status"],
     readiness["starter_intelligence"].get("reason", "")),
]
for label, status, detail in rows:
    c1, c2, c3 = st.columns([2, 1, 3])
    c1.markdown(f"**{label}**")
    c2.markdown(badge(status), unsafe_allow_html=True)
    c3.caption(detail)

st.divider()
st.markdown("### Last NHL sync detail")
st.json(nhl)
if mp:
    st.markdown("### Last MoneyPuck sync detail")
    st.json(mp)

comp.render_provenance_panel()
