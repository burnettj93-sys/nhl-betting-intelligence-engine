"""Page 17 -- Team Shots on Goal Research. STATUS: VALIDATED at
20+/25+/30+/35+ saves (both eval seasons); 40+ is PARTIAL
(season-inconsistent). No sportsbook odds are read or shown here. See
TEAM_SOG_VALIDATION_REPORT.md."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import components as comp
from dashboard import data_access as da
from dashboard import team_sog_view as sv

st.title("Team Shots on Goal Research")
comp.render_model_status_header()
comp.render_data_mode_badge()
st.markdown(
    """
    <div style="border:1px solid #1a5a2a; border-radius:6px; padding:8px 12px;
                background:#0f2416; color:#8e8; font-size:0.85rem; margin-bottom:12px;">
      VALIDATED at <b>20+/25+/30+/35+</b> team SOG (bootstrap frac_improved &ge; 0.95, both eval
      seasons). <b>40+</b> is PARTIAL (season-inconsistent: 0.999 in 2024-25, 0.693 in 2025-26).
      Direct Poisson GLM beats plain rolling team SOG; offense/defense decomposition and player-SOG
      roster aggregation were both tested and underperformed. No sportsbook odds are read or shown
      anywhere on this page. See TEAM_SOG_VALIDATION_REPORT.md.
    </div>
    """,
    unsafe_allow_html=True,
)

results = sv.load_results()
if results is None:
    st.warning("research/team_sog_results.json not found -- run "
               "`python3 -m research.run_team_sog_model` first.")
    st.stop()

st.markdown("### Threshold status (real historical evaluation, 2024-25 + 2025-26)")
status_by_threshold = {20: "VALIDATED", 25: "VALIDATED", 30: "VALIDATED", 35: "VALIDATED", 40: "PARTIAL"}
color_by_status = {"VALIDATED": ":green", "PARTIAL": ":orange"}
cols = st.columns(5)
for col, t in zip(cols, (20, 25, 30, 35, 40)):
    status = status_by_threshold[t]
    col.markdown(f"**{t}+ SOG**")
    col.markdown(f"{color_by_status[status]}[{status}]")

st.divider()
st.markdown("### Project a team's Team SOG (real historical date)")

try:
    predictions = da.compute_baseline_predictions()
except da.DataAvailabilityError as exc:
    comp.render_missing_data_page(exc)
    st.stop()


@st.cache_resource(show_spinner="Loading Team SOG model (first load takes a few seconds)...")
def _engine(_results):
    return sv.TeamSogEngine(_results)


engine = _engine(results)

teams = da.all_teams(predictions)
seasons = da.available_seasons(predictions)
col1, col2 = st.columns(2)
with col1:
    team = st.selectbox("Team", teams, index=teams.index("TOR") if "TOR" in teams else 0)
with col2:
    season = st.selectbox("Season", seasons, index=len(seasons) - 1, format_func=da.format_season)

team_dates_games = [g for g in da.games_for_team(predictions, team) if g["season"] == season]
if not team_dates_games:
    st.info(f"No real {da.format_season(season)} games found for {team}.")
    st.stop()

labels = [f"{g['game_date']} — vs {g['away_team'] if g['home_team']==team else g['home_team']}"
          for g in team_dates_games]
gidx = st.selectbox("Game", range(len(team_dates_games)), index=len(team_dates_games) - 1,
                     format_func=lambda i: labels[i])
game = team_dates_games[gidx]
opponent_team = game["away_team"] if game["home_team"] == team else game["home_team"]
home_away = "home" if game["home_team"] == team else "away"

view = engine.project(team, opponent_team, home_away, game["game_id"], game["game_date"], season)

if view is None:
    st.info("Not enough real historical data for this team before this date.")
else:
    st.markdown(f"#### {team} ({home_away}) vs {opponent_team}")
    m1, m2, m3 = st.columns(3)
    m1.metric("Expected Team SOG", f"{view['expected_sog']:.2f}")
    m2.metric("Conservative Team SOG", f"{view['conservative_sog']:.2f}")
    m3.caption(f"Based on {view['history_games']} prior real games")
    if view["opponent_factor"] is not None:
        st.caption(f"Opponent SOG-allowed factor (vs league avg): {view['opponent_factor']:.2f}x")

    st.markdown("**Threshold probabilities**")
    tcols = st.columns(5)
    for col, t, key in zip(tcols, (20, 25, 30, 35, 40),
                            ("prob_20plus", "prob_25plus", "prob_30plus", "prob_35plus", "prob_40plus")):
        col.metric(f"{t}+", f"{view[key]*100:.1f}%")
        col.caption(status_by_threshold[t])

    comp.render_confidence_badge(view["confidence"], market_type="TEAM_SOG_TOTAL")
    for d in view["confidence_drivers"]:
        st.caption(f"+ {d}")
    for r in view["confidence_risks"]:
        st.caption(f"- {r}")

st.divider()
st.markdown("### Representative examples (real historical rows, 2025-26)")
examples = results.get("representative_examples", {})
if examples:
    name = st.selectbox("Example", list(examples.keys()))
    st.json(examples[name])

st.divider()
st.caption(
    "Full narrative, per-threshold results, bootstrap evidence, player/team reconciliation, and "
    "goalie-saves dependence findings: see TEAM_SOG_VALIDATION_REPORT.md at the repo root."
)
