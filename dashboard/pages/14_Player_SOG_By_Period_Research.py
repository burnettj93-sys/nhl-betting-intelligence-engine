"""Page 14 — Player SOG by Period Research: the first predictive
PERIOD-market model. STATUS: RESEARCH — NOT YET A BETTING RECOMMENDATION.
No sportsbook odds are read or shown here. See
PLAYER_SOG_BY_PERIOD_VALIDATION_REPORT.md."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import components as comp
from dashboard import data_access as da
from dashboard import player_sog_period_view as pv

st.title("Player SOG by Period Research")
comp.render_model_status_header()
comp.render_data_mode_badge()
st.markdown(
    """
    <div style="border:1px solid #5c4a1a; border-radius:6px; padding:8px 12px;
                background:#211a08; color:#e0c060; font-size:0.85rem; margin-bottom:12px;">
      RESEARCH — NOT YET A BETTING RECOMMENDATION. The first predictive period-market model
      in the engine (P1/P2/P3 shots on goal). No sportsbook odds are read or shown anywhere
      on this page — see PLAYER_SOG_BY_PERIOD_VALIDATION_REPORT.md for the full validation
      writeup. Lineup status is always <b>PROJECTED ACTIVE</b>, reconstructed from real prior
      appearance history — never a claim of confirmed target-game lineup knowledge.
      Period 3 LOW-confidence predictions are gated WATCH_ONLY (decision_policy.py v3) —
      real, repeated negative skill in both eval seasons.
    </div>
    """,
    unsafe_allow_html=True,
)

results = pv.load_results()
if results is None:
    st.warning("research/player_sog_period_results.json not found — run "
               "`python3 -m research.run_player_sog_period_model` first.")
    st.stop()

st.markdown("### Validation summary (real historical evaluation, 2024-25 + 2025-26)")
p1c, p2c, p3c = st.columns(3)
for col, k, label in ((p1c, "1", "Period 1"), (p2c, "2", "Period 2"), (p3c, "3", "Period 3")):
    winner = results["winner_by_period"][k]
    col.markdown(f"**{label}**")
    col.caption(f"Winning model: {winner}")
    thr = "1+/2+/3+" if k == "1" else "1+/2+ only"
    col.markdown(f"Validated thresholds: **{thr}**")
    if k == "3":
        col.markdown(":orange[LOW confidence: WATCH_ONLY]")

st.divider()
st.markdown("### Compare a player's three periods (real historical date)")

try:
    predictions = da.compute_baseline_predictions()
except da.DataAvailabilityError as exc:
    comp.render_missing_data_page(exc)
    st.stop()


@st.cache_resource(show_spinner="Loading period SOG model (first load takes a few seconds)...")
def _engine(_results):
    return pv.PeriodSogEngine(_results)


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

team_players = sorted({r["player_id"]: r["player_name"] for r in engine.sog_rows
                        if r["team"] == team and r["game_date"] < game["game_date"]}.items(),
                       key=lambda kv: kv[1])
if not team_players:
    st.info(f"No prior real player-game data for {team} before {game['game_date']}.")
    st.stop()

player_id = st.selectbox("Player", [pid for pid, _name in team_players],
                          format_func=lambda pid: dict(team_players)[pid])
position = next((r["position"] for r in engine.rows if r["player_id"] == player_id
                  and r["game_date"] < game["game_date"]), "C")
home_away = "home" if game["home_team"] == team else "away"

view = engine.project(player_id, team, opponent_team, position, home_away,
                       game["game_id"], game["game_date"], season)

if view is None or view["status"] != "PROJECTED_ACTIVE":
    st.info("Not enough real historical data (period or full-game) for this player before this date.")
else:
    st.markdown(f"#### {dict(team_players)[player_id]} — {team} vs {opponent_team}")
    m1, m2, m3 = st.columns(3)
    m1.metric("Full-game expected SOG", f"{view['full_game_expected_sog']:.2f}"
              if view["full_game_expected_sog"] is not None else "n/a")
    m2.metric("Role tag", view["role_tag"])
    m3.markdown(f"**LINEUP STATUS:** {comp.label_badge('PROJECTED ACTIVE', 'research')}", unsafe_allow_html=True)

    st.markdown("**Period-by-period comparison**")
    pcols = st.columns(3)
    for i, k in enumerate((1, 2, 3)):
        pdata = view["periods"].get(k)
        with pcols[i]:
            st.markdown(f"##### Period {k}")
            if pdata is None:
                st.caption("unavailable")
                continue
            st.metric("Expected SOG", f"{pdata['expected_sog']:.2f}")
            st.caption(f"Conservative: {pdata['conservative_sog']:.2f}")
            st.markdown(f"P(1+): {pdata['prob_1plus']*100:.1f}%")
            st.markdown(f"P(2+): {pdata['prob_2plus']*100:.1f}%")
            st.markdown(f"P(3+): {pdata['prob_3plus']*100:.1f}%")
            comp.render_confidence_badge(
                pdata["confidence"],
                low_confidence_negative_skill=(k == 3),
                market_type=f"PLAYER_SOG_PERIOD_{k}",
            )
            for d in pdata["confidence_drivers"]:
                st.caption(f"+ {d}")
            for r in pdata["confidence_risks"]:
                st.caption(f"- {r}")

st.divider()
st.markdown("### Representative examples (real historical rows, 2025-26)")
examples = results.get("representative_examples", {})
if examples:
    name = st.selectbox("Example", list(examples.keys()))
    ex = examples[name]
    st.json(ex)

st.divider()
st.caption(
    "Full narrative, per-threshold validation, bootstrap results, and calibration: "
    "see PLAYER_SOG_BY_PERIOD_VALIDATION_REPORT.md at the repo root."
)
