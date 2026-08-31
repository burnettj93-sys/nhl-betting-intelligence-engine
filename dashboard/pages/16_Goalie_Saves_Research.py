"""Page 16 -- Goalie Saves Research. STATUS: MIXED. Full-game 20+/25+
saves and Period-2 saves are VALIDATED. 30+ and Periods 1/3 are PARTIAL
(season-inconsistent). 35+ is REJECTED. 40+ is INSUFFICIENT_DATA. All
projections are CONDITIONAL_ON_ACTUAL_START. No sportsbook odds are read
or shown here. See GOALIE_SAVES_VALIDATION_REPORT.md."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import components as comp
from dashboard import data_access as da
from dashboard import goalie_saves_view as gv

st.title("Goalie Saves Research")
comp.render_model_status_header()
comp.render_data_mode_badge()
st.markdown(
    """
    <div style="border:1px solid #6b5a1a; border-radius:6px; padding:8px 12px;
                background:#241f10; color:#e8c; font-size:0.85rem; margin-bottom:12px;">
      MIXED RESULT. Full-game <b>20+/25+ saves</b> and <b>Period-2 saves</b> are VALIDATED (bootstrap
      frac_improved &ge; 0.95, both eval seasons). <b>30+</b> and <b>Periods 1/3</b> are PARTIAL
      (season-inconsistent). <b>35+</b> is REJECTED. <b>40+</b> is INSUFFICIENT_DATA. Every number
      below is CONDITIONAL_ON_ACTUAL_START -- the separate starter-probability panel is a genuinely
      different source of uncertainty, never folded into the saves numbers. No sportsbook odds are
      read or shown anywhere on this page. See GOALIE_SAVES_VALIDATION_REPORT.md.
    </div>
    """,
    unsafe_allow_html=True,
)

results = gv.load_results()
if results is None:
    st.warning("research/goalie_saves_results.json not found -- run "
               "`python3 -m research.run_goalie_saves_model` first.")
    st.stop()

st.markdown("### Full-game threshold status (real historical evaluation, 2024-25 + 2025-26)")
cols = st.columns(5)
status_by_threshold = {20: "VALIDATED", 25: "VALIDATED", 30: "PARTIAL", 35: "REJECTED", 40: "INSUFFICIENT_DATA"}
color_by_status = {"VALIDATED": ":green", "PARTIAL": ":orange", "REJECTED": ":red", "INSUFFICIENT_DATA": ":gray"}
for col, t in zip(cols, (20, 25, 30, 35, 40)):
    status = status_by_threshold[t]
    color = color_by_status[status]
    col.markdown(f"**{t}+ saves**")
    col.markdown(f"{color}[{status}]")

st.markdown("### Period saves status")
pcols = st.columns(3)
period_status = {1: "PARTIAL", 2: "VALIDATED", 3: "PARTIAL"}
for col, k in zip(pcols, (1, 2, 3)):
    status = period_status[k]
    color = color_by_status[status]
    col.markdown(f"**Period {k}**")
    col.markdown(f"{color}[{status}]")

st.divider()
st.markdown("### Project a goalie's start (real historical date) -- CONDITIONAL ON ACTUAL START")

try:
    predictions = da.compute_baseline_predictions()
except da.DataAvailabilityError as exc:
    comp.render_missing_data_page(exc)
    st.stop()


@st.cache_resource(show_spinner="Loading goalie saves model (first load takes a few seconds)...")
def _engine(_results):
    return gv.GoalieSavesEngine(_results)


@st.cache_resource(show_spinner="Loading starter-probability model...")
def _starter_engine():
    sr = gv.load_starter_results()
    return gv.StarterProbabilityEngine(sr) if sr is not None else None


engine = _engine(results)
starter_engine = _starter_engine()

teams = da.all_teams(predictions)
seasons = da.available_seasons(predictions)
col1, col2 = st.columns(2)
with col1:
    team = st.selectbox("Goalie's team", teams, index=teams.index("TOR") if "TOR" in teams else 0)
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

if starter_engine is not None:
    starter_proj = starter_engine.project(team, game["game_date"])
    st.markdown("**Starter probability (separate, existing, audited model -- true-holdout accuracy ~67-69%)**")
    if starter_proj is None:
        st.caption("Not enough real historical data for a starter projection.")
    else:
        for goalie_id, prob in sorted(starter_proj["candidates"], key=lambda x: -x[1]):
            st.markdown(f"- Goalie `{goalie_id}`: {prob*100:.1f}%")
        if starter_proj["is_back_to_back"]:
            st.caption("Team is on a back-to-back.")

st.markdown("**Project a specific goalie's saves, assuming they start**")
goalie_id_input = st.text_input("Goalie ID (NHL player ID)", value="")
if goalie_id_input:
    try:
        goalie_id = int(goalie_id_input)
    except ValueError:
        st.error("Enter a numeric NHL goalie player ID.")
        st.stop()
    view = engine.project(goalie_id, team, opponent_team, home_away, game["game_id"], game["game_date"], season)
    if view is None:
        st.info("Not enough real historical data for this goalie before this date (or no player-agg roster "
                "coverage).")
    else:
        m1, m2, m3 = st.columns(3)
        m1.metric("Expected shots faced", f"{view['expected_shots_faced']:.1f}" if view['expected_shots_faced'] else "n/a")
        m2.metric("Expected saves", f"{view['expected_saves']:.2f}")
        m3.metric("Conservative saves", f"{view['conservative_saves']:.2f}")
        st.caption(f"Shrunk save%: {view['shrunk_save_pct']*100:.1f}% | based on {view['history_games']} prior real starts")

        st.markdown("**Full-game thresholds**")
        tcols = st.columns(5)
        for col, t, key in zip(tcols, (20, 25, 30, 35, 40),
                                ("prob_20plus", "prob_25plus", "prob_30plus", "prob_35plus", "prob_40plus")):
            col.metric(f"{t}+", f"{view[key]*100:.1f}%")
            col.caption(status_by_threshold[t])

        comp.render_confidence_badge(view["confidence"], market_type="GOALIE_SAVES_25PLUS")
        for d in view["confidence_drivers"]:
            st.caption(f"+ {d}")
        for r in view["confidence_risks"]:
            st.caption(f"- {r}")

        st.markdown("**Period-by-period (Period 2 VALIDATED; Periods 1/3 PARTIAL)**")
        pcols2 = st.columns(3)
        for col, k in zip(pcols2, (1, 2, 3)):
            pdata = view["periods"][k]
            with col:
                st.markdown(f"##### Period {k} ({period_status[k]})")
                st.metric("Expected saves", f"{pdata['expected_saves']:.2f}")
                st.markdown(f"P(5+): {pdata['prob_5plus']*100:.1f}%")
                st.markdown(f"P(8+): {pdata['prob_8plus']*100:.1f}%")

st.divider()
st.markdown("### Representative examples (real historical rows, 2025-26)")
examples = results.get("representative_examples", {})
if examples:
    name = st.selectbox("Example", list(examples.keys()))
    st.json(examples[name])

st.divider()
st.caption(
    "Full narrative, per-threshold results, bootstrap evidence, and the honest mixed-validation "
    "verdict: see GOALIE_SAVES_VALIDATION_REPORT.md at the repo root."
)
