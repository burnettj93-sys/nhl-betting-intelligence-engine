"""Page 15 — Team Goals by Period Research. STATUS: RESEARCH — NOT
VALIDATED. Unlike Player SOG by Period, this model did not clear this
project's adoption bar under strict walk-forward evaluation. No
sportsbook odds are read or shown here. See
TEAM_GOALS_BY_PERIOD_VALIDATION_REPORT.md."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import components as comp
from dashboard import data_access as da
from dashboard import team_goals_period_view as tv

st.title("Team Goals by Period Research")
comp.render_model_status_header()
comp.render_data_mode_badge()
st.markdown(
    """
    <div style="border:1px solid #6b2a2a; border-radius:6px; padding:8px 12px;
                background:#241010; color:#e88; font-size:0.85rem; margin-bottom:12px;">
      RESEARCH — <b>NOT VALIDATED</b>. Unlike Player SOG by Period, this model did NOT beat the
      strongest PIT-safe baseline with bootstrap evidence clearing this project's usual bar, at any
      period or threshold, in both evaluation seasons. Numbers below are real, PIT-safe
      re-derivations — informational only, not a validated probability. No sportsbook odds are
      read or shown anywhere on this page. See TEAM_GOALS_BY_PERIOD_VALIDATION_REPORT.md.
    </div>
    """,
    unsafe_allow_html=True,
)

results = tv.load_results()
if results is None:
    st.warning("research/team_goals_period_results.json not found — run "
               "`python3 -m research.run_team_goals_period_model` first.")
    st.stop()

st.markdown("### Evaluation summary (real historical evaluation, 2024-25 + 2025-26)")
p1c, p2c, p3c = st.columns(3)
for col, k, label in ((p1c, "1", "Period 1"), (p2c, "2", "Period 2"), (p3c, "3", "Period 3")):
    winner = results["winner_by_period"][k]
    col.markdown(f"**{label}**")
    col.caption(f"Best point-estimate model: {winner}")
    col.markdown(":red[NOT VALIDATED]")
    col.caption("Bootstrap evidence did not clear the adoption bar in both eval seasons.")

st.divider()
st.markdown("### Project a team's three periods (real historical date) — informational only")

try:
    predictions = da.compute_baseline_predictions()
except da.DataAvailabilityError as exc:
    comp.render_missing_data_page(exc)
    st.stop()


@st.cache_resource(show_spinner="Loading team goals period model (first load takes a few seconds)...")
def _engine(_results):
    return tv.TeamGoalsPeriodEngine(_results)


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
    m1, m2 = st.columns(2)
    m1.metric("Full-game expected goals", f"{view['full_game_expected']:.2f}")
    m2.caption(f"Based on {view['history_games']} prior real games")

    st.markdown("**Period-by-period comparison (NOT VALIDATED)**")
    pcols = st.columns(3)
    for i, k in enumerate((1, 2, 3)):
        pdata = view["periods"].get(k)
        with pcols[i]:
            st.markdown(f"##### Period {k}")
            if pdata is None:
                st.caption("unavailable")
                continue
            st.metric("Expected goals", f"{pdata['expected_goals']:.2f}")
            st.caption(f"Conservative: {pdata['conservative_goals']:.2f}")
            st.markdown(f"P(1+): {pdata['prob_1plus']*100:.1f}%")
            st.markdown(f"P(2+): {pdata['prob_2plus']*100:.1f}%")
            st.markdown(f"P(3+): {pdata['prob_3plus']*100:.1f}%")
            comp.render_confidence_badge(pdata["confidence"], market_type=f"TEAM_PERIOD_{k}_TOTAL")
            for d in pdata["confidence_drivers"]:
                st.caption(f"+ {d}")
            for r in pdata["confidence_risks"]:
                st.caption(f"- {r}")

st.divider()
st.markdown("### Representative examples (real historical rows, 2025-26)")
examples = results.get("representative_examples", {})
if examples:
    name = st.selectbox("Example", list(examples.keys()))
    st.json(examples[name])

st.divider()
st.caption(
    "Full narrative, per-threshold results, bootstrap evidence, and the honest NOT VALIDATED "
    "verdict: see TEAM_GOALS_BY_PERIOD_VALIDATION_REPORT.md at the repo root."
)
