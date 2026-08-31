"""Page 7 — Player SOG Research: the first player-prop probability
model (shots on goal). STATUS: RESEARCH — NOT YET A BETTING
RECOMMENDATION — no sportsbook odds are read or shown here. See
PLAYER_SOG_FOUNDATION_REPORT.md."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import components as comp
from dashboard import data_access as da
from dashboard import player_sog_view as sv
from research import elo_comparison as ec
from research.player_sog import features as pf
from research.run_player_sog_model import build_team_schedules, NHL_CORPUS_PATH


@st.cache_data(show_spinner=False)
def _load_results():
    return sv.load_results()


@st.cache_data(show_spinner="Loading real player-game SOG corpus (this can take a few seconds)...")
def _load_sog_rows():
    return pf.load_sog_corpus()


@st.cache_resource(show_spinner=False)
def _build_index(_rows):
    """Leading underscore tells Streamlit to skip hashing this argument
    for the cache key -- hashing a ~189k-row list of dicts on every
    rerun (cache_resource still hashes hashable-looking args by default)
    was the actual page-load bottleneck; the corpus is static for the
    life of this server process, so caching on no-arg identity is
    correct here."""
    return pf.PlayerHistoryIndex(_rows)


@st.cache_resource(show_spinner=False)
def _build_opponent_history(_rows):
    totals = pf.build_team_game_totals(_rows)
    allowed = pf.build_opponent_allowed_history(totals)
    import statistics
    league_avg = statistics.fmean(v["sog_for"] for v in totals.values())
    return allowed, league_avg


@st.cache_resource(show_spinner=False)
def _load_team_schedules():
    games = ec.load_corpus(str(NHL_CORPUS_PATH))
    return build_team_schedules(games)


st.title("Player SOG Research")
comp.render_model_status_header()
comp.render_data_mode_badge()
st.markdown(
    """
    <div style="border:1px solid #5c4a1a; border-radius:6px; padding:8px 12px;
                background:#211a08; color:#e0c060; font-size:0.85rem; margin-bottom:12px;">
      RESEARCH — NOT YET A BETTING RECOMMENDATION. This is the first player-prop probability
      model in the engine (shots on goal). No sportsbook odds are read or shown anywhere on
      this page — see PLAYER_SOG_FOUNDATION_REPORT.md for the full validation writeup.
      Lineup status below is always <b>PROJECTED ACTIVE</b>, reconstructed from real prior
      appearance history — never a claim of confirmed target-game lineup knowledge.
    </div>
    """,
    unsafe_allow_html=True,
)

results = _load_results()
if results is None:
    st.warning("research/player_sog_results.json not found — run "
               "`python3 research/run_player_sog_model.py` first.")
    st.stop()

st.markdown("### Model vs. naive baselines (real historical evaluation, 4+ SOG)")
headline = results["headline_full_thresholds_poisson"]["thresholds"]["4"]
c1, c2, c3, c4 = st.columns(4)
c1.metric("Full model Brier (4+)", f"{headline['brier']:.4f}")
c2.metric("Full model log loss (4+)", f"{headline['log_loss']:.4f}")
best_baseline_name = min(results["baseline_results"], key=lambda k: results["baseline_results"][k]["thresholds"]["4"]["brier"])
best_baseline_brier = results["baseline_results"][best_baseline_name]["thresholds"]["4"]["brier"]
c3.metric("Best naive baseline Brier (4+)", f"{best_baseline_brier:.4f}", help=best_baseline_name)
c4.metric("Eval player-games", f"{results['common_evaluation_set']['eval_examples_n']:,}")
st.caption(f"Selected count distribution: {'Negative Binomial (alpha=' + format(results['negbinom_alpha_fitted'], '.3f') + ')' if results['negbinom_alpha_fitted'] > 0.01 else 'Poisson (negligible overdispersion detected)'}. "
           f"Evaluated on {results['config']['eval_seasons']}. See PLAYER_SOG_FOUNDATION_REPORT.md for full "
           f"calibration, value-test, and segment detail.")

st.divider()
st.markdown("### Project a player (real historical date)")

try:
    predictions = da.compute_baseline_predictions()
except da.DataAvailabilityError as exc:
    comp.render_missing_data_page(exc)
    st.stop()

rows = _load_sog_rows()
index = _build_index(rows)
opponent_allowed_history, league_avg_sog_allowed = _build_opponent_history(rows)
team_schedules = _load_team_schedules()

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

team_players = sorted({r["player_id"]: r["player_name"] for r in rows
                        if r["team"] == team and r["game_date"] < game["game_date"]}.items(),
                       key=lambda kv: kv[1])
if not team_players:
    st.info(f"No prior real player-game data for {team} before {game['game_date']}.")
    st.stop()

player_id = st.selectbox("Player", [pid for pid, _name in team_players],
                          format_func=lambda pid: dict(team_players)[pid])

view = sv.project_player_sog(
    rows, index, team_schedules, opponent_allowed_history, league_avg_sog_allowed,
    [results["stage_weights"][results["headline_stage"]][name] for name in results["config"]["feature_names"]],
    results["negbinom_alpha_fitted"] if results["negbinom_alpha_fitted"] > 0.01 else None,
    player_id, team, opponent_team, game["game_date"], season)

if view["status"] == "INSUFFICIENT_HISTORY":
    st.info("Not enough real historical data for this player before this date.")
elif view["status"] == "PROJECTED_INACTIVE":
    st.info(view["note"])
else:
    st.markdown(f"#### {dict(team_players)[player_id]} — {team} vs {opponent_team}")
    m1, m2, m3 = st.columns(3)
    m1.metric("Model expected SOG", f"{view['expected_sog']:.2f}")
    m2.metric("Conservative expected SOG", f"{view['conservative_sog']:.2f}")
    m3.markdown(f"**LINEUP STATUS:** {comp.label_badge('PROJECTED ACTIVE', 'research')}", unsafe_allow_html=True)

    st.markdown("**MARKET PROBABILITIES**")
    pcols = st.columns(6)
    for i, n in enumerate([1, 2, 3, 4, 5, 6]):
        label = f"{n}+" if n < 6 else "6+"
        pcols[i].metric(f"P({label} SOG)", f"{view['probs'][str(n)]*100:.1f}%",
                         help=f"Conservative: {view['conservative_probs'][str(n)]*100:.1f}%")

    cc1, cc2 = st.columns(2)
    with cc1:
        comp.render_confidence_badge(view["confidence"])  # LOW here is only marginally weak (negative
        # skill isolated to the sparsest 5+ threshold) -- not the broad failure seen on ASSISTS/POINTS,
        # so no warning banner; see CONFIDENCE_FRAMEWORK_REDESIGN_REPORT.md Section S.
        for d in view["confidence_drivers"]:
            st.markdown(f"+ {d}")
        for r in view["confidence_risks"]:
            st.markdown(f"- {r}")
    with cc2:
        st.markdown("**KEY INPUTS**")
        st.markdown(f"- Season/rolling-20 baseline: {view['baseline_rate']:.2f} SOG/game")
        if view["recent_rate5"] is not None:
            st.markdown(f"- Last-5 average: {view['recent_rate5']:.2f} SOG/game")
        if view["recent_toi_minutes"] is not None:
            st.markdown(f"- Recent (last-10) TOI: {view['recent_toi_minutes']:.1f} min/game")
        if view["opponent_factor"] is not None:
            st.markdown(f"- Opponent shot environment: {view['opponent_factor']*100:.0f}% of league-average SOG allowed")
        st.markdown(f"- Head-to-head: {view['h2h_games']} prior game(s) vs {opponent_team}"
                     + (f" (shrunk H2H-adjusted rate: {view['h2h_rate']:.2f})" if view["h2h_games"] else ""))
        st.markdown(f"- Real prior game sample: {view['history_games']} games")
        st.caption(f"Distribution used: {view['distribution']}.")

st.divider()
st.markdown("### Representative real examples")
ex = results["representative_examples"]
cols = st.columns(2)
for i, (name, e) in enumerate(ex.items()):
    with cols[i % 2]:
        if e is None:
            st.caption(f"{name.replace('_', ' ').title()}: no matching example found in eval set.")
            continue
        st.markdown(f"**{name.replace('_', ' ').title()}** — {e['player']} ({e['team']} vs {e['opponent']}, {e['game_date']})")
        st.caption(f"Expected {e['expected_sog']} SOG · P(4+)={e['p_4plus']*100:.0f}% "
                   f"(conservative {e['conservative_p_4plus']*100:.0f}%) · confidence {e['confidence']} · "
                   f"actual {int(e['actual_sog'])} SOG")

comp.render_provenance_panel()
