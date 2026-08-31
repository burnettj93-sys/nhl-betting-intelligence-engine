"""Page 1 — Game Slate: browse real historical NHL dates and see the
current (Elo-only) production model's win probability for every game on
that date. No fake odds, no fake BET/PASS recommendation — see
components.render_odds_not_connected()."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import components as comp
from dashboard import data_access as da
from dashboard import model_view as mv


@st.cache_data(show_spinner="Loading real NHL corpus and computing baseline predictions...")
def _load_predictions() -> list[dict]:
    return da.compute_baseline_predictions()


st.title("Game Slate")
comp.render_model_status_header()
comp.render_data_mode_badge()

try:
    records = _load_predictions()
except da.DataAvailabilityError as exc:
    comp.render_missing_data_page(exc)
    st.stop()

dates = da.available_dates(records)
if not dates:
    st.warning("No games found in the real NHL corpus.")
    st.stop()

default_idx = len(dates) - 1
col1, col2 = st.columns([2, 1])
with col1:
    selected_date = st.selectbox("Select a real NHL game date", dates, index=default_idx)
with col2:
    seasons = da.available_seasons(records)
    st.caption(f"Corpus covers {len(records)} real regular-season games across seasons: "
               f"{', '.join(da.format_season(s) for s in seasons)}")

games = da.games_on_date(records, selected_date)
st.markdown(f"**{len(games)} game(s) on {selected_date}**")
comp.render_odds_not_connected()
st.divider()

for g in games:
    drivers = mv.model_drivers(g)
    label = mv.confidence_label(g["p_home"])
    with st.container(border=True):
        c1, c2, c3 = st.columns([3, 2, 2])
        with c1:
            st.markdown(f"#### {g['away_team']} @ {g['home_team']}")
            st.caption(f"{g['game_date']} · {g['period_type']}")
        with c2:
            st.metric(g["away_team"], f"{(1 - g['p_home']) * 100:.1f}%")
            st.metric(g["home_team"], f"{g['p_home'] * 100:.1f}%")
        with c3:
            st.markdown(f"**Confidence:** {label}")
            st.markdown("**Model drivers:**")
            for d in drivers:
                st.markdown(f"{d['sign']} {d['label']}")
            st.caption("Player / goalie / rest inputs: " + comp.NOT_AVAILABLE)
        if st.button("View game detail →", key=f"detail_{g['game_id']}"):
            st.session_state["selected_game_id"] = g["game_id"]
            st.switch_page(str(REPO_ROOT / "dashboard" / "pages" / "2_Game_Detail.py"))

comp.render_provenance_panel()
