"""Page 13 — Play-by-Play Data Status (Part 36): a small, read-only panel
showing the real state of the play-by-play ingestion foundation built in
this slice. Reads cached manifests only -- never makes a network call.
See NHL_PLAY_BY_PLAY_FOUNDATION_REPORT.md for the full narrative."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import components as comp
from dashboard import pbp_status_view as pv
from research.real_nhl_pbp import readiness

st.title("Play-by-Play Data Status")
comp.render_model_status_header()
st.markdown(
    """
    <div style="border:1px solid #232d38; border-radius:6px; padding:8px 12px;
                background:#10151c; color:#8c99a8; font-size:0.85rem; margin-bottom:12px;">
      Data foundation only -- no period/event-time betting markets are built on this yet.
      Reads a cached snapshot only; this page never makes a network call.
    </div>
    """,
    unsafe_allow_html=True,
)

status = pv.load_status()
pilot_manifest = status["pilot_manifest"]
pilot_validation = status["pilot_validation"]
season_manifest = status["season_manifest"]
four_season_manifest = status["four_season_manifest"]

if pilot_manifest is None:
    st.info("No pilot has been run yet. Run `python3 -m research.real_nhl_pbp.build_pbp_pilot`.")
    st.stop()

st.subheader("4-season corpus")
corpus_manifest = pv.load_corpus_manifest()
fs1, fs2, fs3, fs4 = st.columns(4)
fs1.metric("Corpus", "4 seasons")
fs1.caption("2022-23 through 2025-26")
fs2.metric("Games", f"{status['total_games']:,} / {status['expected_total_games']:,}")
if corpus_manifest:
    fs2.caption(f"{corpus_manifest['total_events']:,} events")
fs3.metric("Coverage", f"{status['coverage_pct']}%")
contract_pass = status["total_games"] == status["expected_total_games"]
fs4.metric("Contract", "PASS" if contract_pass else "IN PROGRESS")
foundation_status = "READY" if contract_pass else "PARTIAL"
fs4.caption(f"Data foundation: {foundation_status}")

with st.expander("Games archived by season"):
    for s, n in status["archived_games_by_season"].items():
        st.write(f"{s}: {n} / 1,312")

st.divider()
st.subheader("Event-timing utility closure")
_period_entries = {e.market_label: e for e in readiness.PERIOD_MARKET_READINESS}
_event_time_entries = {e.market_label: e for e in readiness.EVENT_TIME_MARKET_READINESS}
_goalie_entries = {e.market_label: e for e in readiness.GOALIE_MARKET_READINESS}
_readiness_color = {"READY": "#3ecf8e", "PARTIAL": "#e8b84f", "NOT READY": "#f0654f"}
util1, util2 = st.columns(2)
_period_saves_status = _period_entries["GOALIE SAVES BY PERIOD"].readiness
util1.markdown(
    f"**PERIOD GOALIE SAVES**: "
    f"<span style='color:{_readiness_color[_period_saves_status]}; font-weight:600;'>{_period_saves_status}</span>",
    unsafe_allow_html=True,
)
_gwg_status = _event_time_entries["GAME-WINNING GOAL"].readiness
util2.markdown(
    f"**GWG**: <span style='color:{_readiness_color[_gwg_status]}; font-weight:600;'>{_gwg_status}</span>",
    unsafe_allow_html=True,
)

st.divider()
col1, col2, col3 = st.columns(3)
col1.metric("Pilot season", pilot_manifest["pilot_season"])
col1.metric("Pilot games archived", pilot_manifest["games_fetched"])

if pilot_validation:
    col2.metric("Pilot games passed", f"{pilot_validation['games_passed']}/{pilot_validation['games_validated']}")
    gate = "OPEN" if pilot_validation["pilot_passed"] else "CLOSED"
    gate_color = "#3ecf8e" if pilot_validation["pilot_passed"] else "#f0654f"
    col2.markdown(f"Expansion gate: <span style='color:{gate_color}; font-weight:600;'>{gate}</span>",
                  unsafe_allow_html=True)

if season_manifest:
    col3.metric("Season games retrieved", season_manifest["games_retrieved_total"])
    col3.metric("Season games missing", len(season_manifest["games_missing"]))
else:
    col3.info("One-season ingestion not yet run.")

st.divider()
st.subheader("Market data-readiness (Parts 28-33)")
for section_name, entries in readiness.ALL_SECTIONS.items():
    with st.expander(section_name.replace("_", " ").title()):
        for e in entries:
            color = {"READY": "#3ecf8e", "PARTIAL": "#e8b84f", "NOT READY": "#f0654f"}[e.readiness]
            st.markdown(
                f"**{e.market_label}** — <span style='color:{color}; font-weight:600;'>{e.readiness}</span>"
                f"<br><span style='color:#8c99a8; font-size:0.85rem;'>{e.evidence}</span>",
                unsafe_allow_html=True,
            )

st.divider()
st.caption(
    "Full narrative, evidence, and the pilot/one-season acceptance decisions: "
    "see NHL_PLAY_BY_PLAY_FOUNDATION_REPORT.md at the repo root."
)
