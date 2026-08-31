"""Page 11 -- Player Points Research: the fourth player-prop model
(total points), and the first built under this project's explicit
tuning/lock/freeze/true-evaluation discipline. STATUS is read from the
shared prop registry below, never hand-typed on this page -- see
research/player_props/registry.py, PLAYER_POINTS_VALIDATION_REPORT.md
(Cycle 1), and PLAYER_POINTS_REDESIGN_REPORT.md (Cycle 2, the empirical-
baseline challenge) for the full evidence."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import components as comp
from dashboard import data_access as da
from dashboard import player_points_view as pv
from research import elo_comparison as ec
from research.player_points import features as ptf
from research.player_props import registry
from research.run_player_sog_model import build_team_schedules, NHL_CORPUS_PATH

STATUS_BADGE_KIND = {"VALIDATED": "input", "PARTIAL": "research", "RESEARCH": "research",
                      "REJECTED": "unavailable", "BLOCKED": "unavailable", "UNSUPPORTED_MARKET": "unavailable",
                      "EMPIRICAL_BASELINE_REMAINS_CHAMPION": "research"}

REDESIGN_RESULTS_PATH = REPO_ROOT / "research" / "player_points_redesign_results.json"


@st.cache_data(show_spinner=False)
def _load_redesign_results():
    return da.load_json_safely(REDESIGN_RESULTS_PATH)


@st.cache_data(show_spinner=False)
def _load_results():
    return pv.load_results()


@st.cache_data(show_spinner=False)
def _load_manifest():
    return pv.load_manifest()


@st.cache_data(show_spinner="Loading real player-game points corpus (this can take a few seconds)...")
def _load_points_rows():
    return ptf.load_points_corpus()


@st.cache_resource(show_spinner=False)
def _build_index(_rows):
    return ptf.PlayerHistoryIndex(_rows)


@st.cache_resource(show_spinner=False)
def _build_team_context(_rows):
    totals = ptf.build_team_game_points_totals(_rows)
    team_offense_hist = ptf.build_team_offense_history(totals)
    opponent_env = ptf.build_opponent_points_allowed(totals)
    import statistics
    league_avg = statistics.fmean(v["points_for"] for v in totals.values())
    return team_offense_hist, opponent_env, league_avg


@st.cache_resource(show_spinner=False)
def _load_team_schedules():
    games = ec.load_corpus(str(NHL_CORPUS_PATH))
    return build_team_schedules(games)


st.title("Player Points Research")
comp.render_model_status_header()
comp.render_data_mode_badge()

entry = registry.get("POINTS")
status = entry.model_status if entry else "RESEARCH"
st.markdown(
    f"""
    <div style="border:1px solid #5c4a1a; border-radius:6px; padding:8px 12px;
                background:#211a08; color:#e0c060; font-size:0.85rem; margin-bottom:12px;">
      STATUS: {comp.label_badge(status, STATUS_BADGE_KIND.get(status, "unavailable"))} -- NOT A BETTING
      RECOMMENDATION. No sportsbook odds are read or shown anywhere on this page.<br><br>
      {entry.summary if entry else ""}
    </div>
    """,
    unsafe_allow_html=True,
)

results = _load_results()
if results is None:
    st.warning("research/player_points_results.json not found -- run "
               "`python3 research/run_player_points_model.py` first.")
    st.stop()

manifest = _load_manifest()

st.markdown("### Locked model vs. baselines (real true-evaluation, 2024-25 + 2025-26)")
headline = results["headline_uncalibrated"]["thresholds"]["1"]
best_baseline_name = min(results["baseline_results"], key=lambda k: results["baseline_results"][k]["thresholds"]["1"]["brier"])
best_baseline_brier = results["baseline_results"][best_baseline_name]["thresholds"]["1"]["brier"]
c1, c2, c3, c4 = st.columns(4)
c1.metric("Locked model Brier (1+)", f"{headline['brier']:.4f}")
c2.metric("Locked model log loss (1+)", f"{headline['log_loss']:.4f}")
c3.metric("Best baseline Brier (1+)", f"{best_baseline_brier:.4f}", help=best_baseline_name)
c4.metric("Eval player-games", f"{results['eval_examples_n']:,}")

if best_baseline_brier < headline["brier"]:
    st.warning(f"The locked model does **not** beat `{best_baseline_name}` on 1+ Brier in this true "
               f"evaluation -- reported plainly, not smoothed over. See PLAYER_POINTS_VALIDATION_REPORT.md "
               f"Section AB/AC for the full breakdown across all three thresholds.")

st.caption(f"Locked stage: `{results['config']['locked_stage']}`. Distribution: "
           f"{'Negative Binomial (alpha=' + format(results['alpha'], '.4f') + ')' if results['alpha'] > 0.01 else 'Poisson'}. "
           f"Freeze manifest: `{results.get('freeze_manifest_path', 'research/player_points_freeze_manifest.json')}`.")

st.divider()
st.markdown("### Project a player (real historical date)")

try:
    predictions = da.compute_baseline_predictions()
except da.DataAvailabilityError as exc:
    comp.render_missing_data_page(exc)
    st.stop()

rows = _load_points_rows()
index = _build_index(rows)
team_offense_hist, opponent_env, league_avg_points_for = _build_team_context(rows)
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

labels = [f"{g['game_date']} -- vs {g['away_team'] if g['home_team']==team else g['home_team']}"
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

locked_weights = [results["stage_weights"][results["config"]["locked_stage"]][name] for name in results["config"]["feature_names"]]
alpha_val = results["alpha"] if results["alpha"] > 0.01 else None
calibration_scales = {int(k): v for k, v in results.get("calibration_scales", {}).items()} if results.get("calibration_needed") else {}

view = pv.project_player_points(
    rows, index, team_schedules, team_offense_hist, opponent_env, league_avg_points_for,
    locked_weights, alpha_val, calibration_scales, player_id, team, opponent_team, game["game_date"], season)

three_plus_supported = results["three_plus_support_checks"]["three_plus_status"] == "SUPPORTED"

if view["status"] == "INSUFFICIENT_HISTORY":
    st.info("Not enough real historical data for this player before this date.")
elif view["status"] == "PROJECTED_INACTIVE":
    st.info(view["note"])
else:
    st.markdown(f"#### {dict(team_players)[player_id]} -- {team} vs {opponent_team}")
    m1, m2, m3 = st.columns(3)
    m1.metric("Model expected points", f"{view['expected_points']:.2f}")
    m2.metric("Conservative expected points", f"{view['conservative_points']:.2f}")
    m3.markdown(f"**LINEUP STATUS:** {comp.label_badge('PROJECTED ACTIVE', 'research')}", unsafe_allow_html=True)

    st.markdown("**MARKET PROBABILITIES**")
    thresholds_to_show = [1, 2, 3] if three_plus_supported else [1, 2]
    if not three_plus_supported:
        st.caption("3+ points omitted: fails the pre-specified support standard (INSUFFICIENT_DATA -- "
                   "see PLAYER_POINTS_VALIDATION_REPORT.md Section AF).")
    pcols = st.columns(len(thresholds_to_show))
    for i, n in enumerate(thresholds_to_show):
        pcols[i].metric(f"P({n}+ points)", f"{view['probs'][str(n)]*100:.1f}%",
                         help=f"Conservative: {view['conservative_probs'][str(n)]*100:.1f}%")

    cc1, cc2 = st.columns(2)
    with cc1:
        comp.render_confidence_badge(view["confidence"], low_confidence_negative_skill=True, market_type="POINTS")
        for d in view["confidence_drivers"]:
            st.markdown(f"+ {d}")
        for r in view["confidence_risks"]:
            st.markdown(f"- {r}")
    with cc2:
        st.markdown("**KEY INPUTS**")
        st.markdown(f"- Season/rolling-20 baseline: {view['baseline_rate']:.2f} points/game")
        if view["recent_rate5"] is not None:
            st.markdown(f"- Last-5 average: {view['recent_rate5']:.2f} points/game")
        if view["recent_toi_minutes"] is not None:
            st.markdown(f"- Recent (last-10) TOI: {view['recent_toi_minutes']:.1f} min/game")
        if view["pp_rate_recent"] is not None:
            st.markdown(f"- Recent PP points/game: {view['pp_rate_recent']:.2f}")
        if view["opponent_factor"] is not None:
            st.markdown(f"- Opponent points-allowed environment: {view['opponent_factor']*100:.0f}% of league average")
        if view["team_factor"] is not None:
            st.markdown(f"- Team offensive environment: {view['team_factor']*100:.0f}% of league average")
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
        st.markdown(f"**{name.replace('_', ' ').title()}** -- {e['player']} ({e['team']} vs {e['opponent']}, {e['game_date']})")
        p3 = f", P(3+)={e['p_3plus']*100:.0f}%" if e.get("p_3plus") is not None else ""
        st.caption(f"Expected {e['expected_points']} points · P(1+)={e['p_1plus']*100:.0f}% · "
                   f"P(2+)={e['p_2plus']*100:.0f}%{p3} · confidence {e['confidence']} · "
                   f"actual {int(e['actual_points'])} points")

st.divider()
st.markdown("## Redesign Cycle 2 -- Empirical Baseline Challenge")
redesign = _load_redesign_results()
if redesign is None:
    st.info("research/player_points_redesign_results.json not found -- run "
            "`python3 research/run_player_points_redesign.py` first.")
else:
    st.caption(f"**EVALUATION STATUS: {redesign['evaluation_status']}** -- 2024-25/2025-26 were "
               f"Cycle 1's true-evaluation seasons and are reused here as rolling-fold validation "
               f"seasons, not pristine holdout. See PLAYER_POINTS_REDESIGN_REPORT.md.")
    st.markdown(
        "Five interpretable candidates compared across 3 rolling walk-forward folds: **C1** existing "
        "flat-shrunk empirical baseline, **C2** role-hierarchical empirical baseline, **C3** hierarchical "
        "mean + opponent-context adjustment, **C4** hierarchical mean read parametrically (isolates shape "
        "vs. mean-quality), **C5** Cycle 1's locked GLM (reused unchanged, reference only)."
    )
    rows_tbl = []
    for fold in redesign["folds"]:
        for c in ("C1", "C2", "C3", "C4", "C5"):
            t1 = fold["candidate_results"][c]["thresholds"]["1"]
            rows_tbl.append({"Fold": fold["fold_name"].replace("_", " "), "Candidate": c,
                              "1+ Brier": round(t1["brier"], 5), "1+ Skill": round(t1["brier_skill_score"], 4),
                              "% beats C1 (bootstrap)": (f"{fold['bootstrap_vs_c1'][c]['frac_improved']*100:.1f}%"
                                                          if c != "C1" else "--")})
    st.dataframe(rows_tbl, use_container_width=True, hide_index=True)
    st.caption("C5 (the old GLM) loses to the empirical baseline in all 3 folds (0% bootstrap credibility "
               "every time) -- the strongest confirmation yet of Cycle 1's finding, now under an entirely "
               "different validation design. C3 (context-adjusted) shows real but INCONSISTENT improvement "
               "at the primary 1+ threshold: it wins convincingly in one fold and loses in another, which "
               "is why the registry status is EMPIRICAL_BASELINE_REMAINS_CHAMPION rather than VALIDATED.")

    st.markdown("### Empirical vs. redesigned candidate -- representative examples")
    reps = redesign["representative_examples"]
    rcols = st.columns(2)
    for i, (name, e) in enumerate(reps.items()):
        with rcols[i % 2]:
            if e is None:
                st.caption(f"{name.replace('_', ' ').title()}: no matching example found.")
                continue
            st.markdown(f"**{name.replace('_', ' ').title()}** -- {e['player']} ({e['team']} vs {e['opponent']}, {e['game_date']})")
            st.caption(f"Empirical P(1+)={e['empirical_p_1plus']*100:.0f}% · Redesign P(1+)={e['redesign_p_1plus']*100:.0f}% "
                       f"· actual {int(e['actual_points'])} points")

comp.render_provenance_panel()
