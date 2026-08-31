"""Page 3 — Team Ratings: sortable table of current Elo ratings (MODEL
INPUT) plus optional MoneyPuck context metrics (RESEARCH METRIC — not
currently used by the model), as of a selected real date."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import altair as alt
import pandas as pd
import streamlit as st

from dashboard import components as comp
from dashboard import data_access as da
from dashboard import model_view as mv


@st.cache_data(show_spinner="Loading real NHL corpus and computing baseline predictions...")
def _load_predictions() -> list[dict]:
    return da.compute_baseline_predictions()


@st.cache_resource(show_spinner=False)
def _moneypuck_conn():
    try:
        return da.get_moneypuck_connection()
    except da.DataAvailabilityError:
        return None


st.title("Team Ratings")
comp.render_model_status_header()
comp.render_data_mode_badge()

try:
    records = _load_predictions()
except da.DataAvailabilityError as exc:
    comp.render_missing_data_page(exc)
    st.stop()

seasons = da.available_seasons(records)
col1, col2, col3 = st.columns([1, 1, 2])
with col1:
    season = st.selectbox("Season", seasons, index=len(seasons) - 1, format_func=da.format_season)
season_dates = sorted({r["game_date"] for r in records if r["season"] == season})
with col2:
    as_of = st.selectbox("As of date", season_dates, index=len(season_dates) - 1) if season_dates else None
with col3:
    include_mp = st.checkbox("Include MoneyPuck research context (25-game window)", value=True)

if as_of is None:
    st.warning("No games found for this season.")
    st.stop()

conn = _moneypuck_conn() if include_mp else None
rows = mv.team_ratings_table(records, conn, as_of, season, include_moneypuck=include_mp)

st.caption(f"Ratings as of games strictly before {as_of} ({da.format_season(season)}). "
           f"{comp.label_badge('MODEL INPUT', 'input')} columns come from the real production Elo model. "
           f"{comp.label_badge(comp.RESEARCH_METRIC, 'research')} columns are context only.",
           unsafe_allow_html=True)

table_data = []
for r in rows:
    row = {
        "Team": r["team"], "Elo (MODEL INPUT)": round(r["elo_rating"], 1),
        "GP": r["games_played"], "W": r["wins"], "L": r["losses"],
    }
    if include_mp and "research" in r:
        research = r["research"]
        row["5v5 xG share (research)"] = round(research["xg_share_5v5"], 3) if research["xg_share_5v5"] is not None else None
        row["Off xGF/60 (research)"] = round(research["offense_xgf60"], 2) if research["offense_xgf60"] is not None else None
        row["Def xGA/60 (research)"] = round(research["defense_xga60"], 2) if research["defense_xga60"] is not None else None
        row["PP xGF/60 (research)"] = round(research["pp_xgf60"], 2) if research["pp_xgf60"] is not None else None
        row["PK xGA/60 (research)"] = round(research["pk_xga60"], 2) if research["pk_xga60"] is not None else None
    table_data.append(row)

df = pd.DataFrame(table_data)
st.dataframe(df, use_container_width=True, hide_index=True)

st.divider()
st.markdown("### Team detail")
teams = [r["team"] for r in rows]
selected_team = st.selectbox("Select a team for Elo history", teams) if teams else None
if selected_team:
    team_games = [r for r in da.games_for_team(records, selected_team) if r["season"] == season and r["game_date"] <= as_of]
    if team_games:
        hist = pd.DataFrame([
            {"game_date": g["game_date"],
             "elo": g["rating_home_pregame"] if g["home_team"] == selected_team else g["rating_away_pregame"]}
            for g in team_games
        ])
        st.altair_chart(
            alt.Chart(hist).mark_line(point=True).encode(
                x="game_date:T", y=alt.Y("elo:Q", title="Elo rating", scale=alt.Scale(zero=False)),
            ).properties(title=f"{selected_team} Elo history, {da.format_season(season)}"),
            use_container_width=True,
        )

        def _team_won(g):
            return (g["actual_home_win"] == 1.0) if g["home_team"] == selected_team else (g["actual_home_win"] == 0.0)

        def _model_favored_team(g):
            return (g["p_home"] >= 0.5) if g["home_team"] == selected_team else (g["p_home"] < 0.5)

        wins = sum(1 for g in team_games if _team_won(g))
        correct = sum(1 for g in team_games if _model_favored_team(g) == _team_won(g))
        c1, c2, c3 = st.columns(3)
        c1.metric("Record", f"{wins}-{len(team_games) - wins}")
        c2.metric("Games", len(team_games))
        c3.metric("Model favorite-pick accuracy", f"{correct / len(team_games) * 100:.1f}%")
        st.caption("Favorite-pick accuracy: how often the model's >50% pick for this team's games was correct "
                   "— a simple accuracy check, not the calibration metric used on the Model Performance page.")

comp.render_provenance_panel()
