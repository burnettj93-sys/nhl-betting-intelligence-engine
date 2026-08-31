"""Page 12 -- Player Goals Research: the fifth player-prop model
(goals / anytime goal scorer). STATUS is read from the shared prop
registry below, never hand-typed on this page -- see
research/player_props/registry.py and PLAYER_GOALS_VALIDATION_REPORT.md
for the full evidence."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import components as comp
from dashboard import data_access as da
from dashboard import player_goals_view as gv
from research import elo_comparison as ec
from research.player_goals import features as gf
from research.player_goals import hierarchy as gh
from research.player_props import registry
from research.run_player_sog_model import build_team_schedules, NHL_CORPUS_PATH

STATUS_BADGE_KIND = {"VALIDATED": "input", "PARTIAL": "research", "RESEARCH": "research",
                      "REJECTED": "unavailable", "BLOCKED": "unavailable", "UNSUPPORTED_MARKET": "unavailable",
                      "EMPIRICAL_BASELINE_REMAINS_CHAMPION": "research", "SUPPORTED_BY_GOALS_MODEL": "input"}


@st.cache_data(show_spinner=False)
def _load_results():
    return gv.load_results()


@st.cache_data(show_spinner="Loading real player-game goals corpus (this can take a few seconds)...")
def _load_goals_rows():
    return gf.load_goals_corpus()


@st.cache_resource(show_spinner=False)
def _build_index(_rows):
    return gf.PlayerHistoryIndex(_rows)


@st.cache_resource(show_spinner=False)
def _build_team_context(_rows):
    totals = gf.build_team_game_goals_totals(_rows)
    team_offense_hist = gf.build_team_offense_history(totals)
    opponent_env = gf.build_opponent_goals_allowed(totals)
    import statistics
    league_avg = statistics.fmean(v["goals_for"] for v in totals.values())
    all_sog = sum(r["sog"] for r in _rows)
    league_shooting_pct = sum(r["goals"] for r in _rows) / all_sog if all_sog > 0 else 0.09
    return team_offense_hist, opponent_env, league_avg, league_shooting_pct


@st.cache_resource(show_spinner=False)
def _load_team_schedules():
    games = ec.load_corpus(str(NHL_CORPUS_PATH))
    return build_team_schedules(games)


st.title("Player Goals Research")
comp.render_model_status_header()
comp.render_data_mode_badge()

entry = registry.get("GOALS")
status = entry.model_status if entry else "RESEARCH"
st.markdown(
    f"""
    <div style="border:1px solid #1a5c2e; border-radius:6px; padding:8px 12px;
                background:#0a2110; color:#6ad089; font-size:0.85rem; margin-bottom:12px;">
      STATUS: {comp.label_badge(status, STATUS_BADGE_KIND.get(status, "unavailable"))} -- NOT A BETTING
      RECOMMENDATION. No sportsbook odds are read or shown anywhere on this page.<br><br>
      {entry.summary if entry else ""}
    </div>
    """,
    unsafe_allow_html=True,
)

results = _load_results()
if results is None:
    st.warning("research/player_goals_results.json not found -- run "
               "`python3 research/run_player_goals_model.py` first.")
    st.stop()

manifest = gv.load_manifest()

st.markdown("### Locked model vs. baselines (real true-evaluation, 2024-25 + 2025-26)")
best_candidate_name = results["best_candidate_name"]
headline = results["candidate_results"][best_candidate_name]["thresholds"]["1"]
best_baseline_name = results["best_baseline_name"]
best_baseline_brier = results["baseline_results"][best_baseline_name]["thresholds"]["1"]["brier"]
c1, c2, c3, c4 = st.columns(4)
c1.metric("Locked model Brier (1+)", f"{headline['brier']:.5f}")
c2.metric("Locked model log loss (1+)", f"{headline['log_loss']:.4f}")
c3.metric("Best baseline Brier (1+)", f"{best_baseline_brier:.5f}", help=best_baseline_name)
c4.metric("Eval player-games", f"{results['eval_examples_n']:,}")

bootstrap = results["bootstrap_vs_best_baseline"][best_candidate_name]
st.caption(f"Winning candidate: `{best_candidate_name}` -- beats `{best_baseline_name}` with "
           f"{bootstrap['frac_improved']*100:.1f}% game-clustered bootstrap credibility "
           f"({bootstrap['n_resamples']} resamples). Freeze manifest: "
           f"`{results.get('freeze_manifest_path', 'research/player_goals_freeze_manifest.json')}`.")

two_plus_supported = results["two_plus_support_checks"]["two_plus_status"] == "SUPPORTED"
if not two_plus_supported:
    st.caption("2+ GOALS: INSUFFICIENT DATA -- fails the pre-specified per-confidence-bucket support "
               "standard (the LOW-confidence bucket has too few real 2+ events). See "
               "PLAYER_GOALS_VALIDATION_REPORT.md Section Z.")

st.divider()
st.markdown("### Project a player (real historical date)")

try:
    predictions = da.compute_baseline_predictions()
except da.DataAvailabilityError as exc:
    comp.render_missing_data_page(exc)
    st.stop()

rows = _load_goals_rows()
index = _build_index(rows)
team_offense_hist, opponent_env, league_avg_goals_for, league_shooting_pct = _build_team_context(rows)
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

rates = gh.RoleLeagueRates(rows)  # frozen, full-corpus aggregate for live "project a player" use
locked_context_idx = set(results["locked_context_idx_for_candidate_e"])
context_weights = [results["context_weights_e"][n] for n in results["config"]["feature_names"]]
alpha_val = results["alpha_e"] if results["alpha_e"] > 0.01 else None

view = gv.project_player_goals(
    rows, index, team_schedules, team_offense_hist, opponent_env, league_avg_goals_for, league_shooting_pct,
    context_weights, alpha_val, rates, results["best_k_player"], locked_context_idx,
    player_id, team, opponent_team, game["game_date"], season)

if view["status"] == "INSUFFICIENT_HISTORY":
    st.info("Not enough real historical data for this player before this date.")
elif view["status"] == "PROJECTED_INACTIVE":
    st.info(view["note"])
else:
    st.markdown(f"#### {dict(team_players)[player_id]} -- {team} vs {opponent_team}")
    m1, m2, m3 = st.columns(3)
    m1.metric("Model expected goals", f"{view['expected_goals']:.3f}")
    m2.metric("Conservative expected goals", f"{view['conservative_goals']:.3f}")
    m3.markdown(f"**LINEUP STATUS:** {comp.label_badge('PROJECTED ACTIVE', 'research')}", unsafe_allow_html=True)

    st.markdown("**MARKET PROBABILITIES**")
    thresholds_to_show = [1, 2] if two_plus_supported else [1]
    if not two_plus_supported:
        st.caption("2+ goals omitted: fails the pre-specified support standard (INSUFFICIENT_DATA).")
    pcols = st.columns(len(thresholds_to_show))
    for i, n in enumerate(thresholds_to_show):
        pcols[i].metric(f"P({n}+ goals)", f"{view['probs'][str(n)]*100:.1f}%",
                         help=f"Conservative: {view['conservative_probs'][str(n)]*100:.1f}%")

    cc1, cc2 = st.columns(2)
    with cc1:
        comp.render_confidence_badge(view["confidence"], low_confidence_negative_skill=True, market_type="GOALS")
        for d in view["confidence_drivers"]:
            st.markdown(f"+ {d}")
        for r in view["confidence_risks"]:
            st.markdown(f"- {r}")
    with cc2:
        st.markdown("**SHOOTING TALENT & KEY INPUTS**")
        st.markdown(f"- Season/rolling-20 baseline: {view['baseline_rate']:.3f} goals/game")
        if view["recent_sog_rate"] is not None:
            st.markdown(f"- Recent (last-10) SOG rate: {view['recent_sog_rate']:.2f} shots/game")
        if view["raw_shooting_pct"] is not None:
            st.markdown(f"- Raw career shooting%: {view['raw_shooting_pct']*100:.1f}% ({view['career_shots']} career shots)")
        st.markdown(f"- **Shrunk shooting talent: {view['shrunk_shooting_pct']*100:.1f}%** "
                    f"(heavier shrinkage for low-volume shooters)")
        if view["opponent_factor"] is not None:
            st.markdown(f"- Opponent goals-allowed environment: {view['opponent_factor']*100:.0f}% of league average")
        st.markdown(f"- Head-to-head: {view['h2h_goals_games']} prior game(s) vs {opponent_team} (goals), "
                     f"{view['h2h_sog_games']} (SOG)")
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
        st.markdown(f"**{name.replace('_', ' ').title()}** -- {e.get('player', '?')}")
        parts = []
        if "p_1plus" in e:
            parts.append(f"P(1+)={e['p_1plus']*100:.0f}%")
        if e.get("raw_shooting_pct") is not None:
            parts.append(f"raw shooting%={e['raw_shooting_pct']*100:.1f}%")
        if e.get("shrunk_shooting_pct") is not None:
            parts.append(f"shrunk={e['shrunk_shooting_pct']*100:.1f}%")
        if "confidence" in e:
            parts.append(f"confidence {e['confidence']}")
        if "actual_goals" in e:
            parts.append(f"actual {int(e['actual_goals'])} goals")
        st.caption(" · ".join(parts))

st.divider()
st.markdown("## Shot-Quality Refinement Cycle -- Incumbent vs. Challenger")
sq_results = da.load_json_safely(REPO_ROOT / "research" / "goals_shot_quality_results.json")
if sq_results is None:
    st.info("research/goals_shot_quality_results.json not found -- run "
            "`python3 research/run_goals_shot_quality_refinement.py` first.")
else:
    st.caption(f"**EVALUATION STATUS: {sq_results['evaluation_status']}** -- {sq_results['methodology_note']}")
    best = sq_results["best_final_candidate"]
    beats = sq_results["final_best_beats_incumbent_95pct_bar"]
    dev_cleared = sq_results["challenger_cleared_95pct_bar_on_dev"]
    inc_brier = sq_results["incumbent_final_metrics"]["brier"]
    chal_brier = sq_results["challenger_final_metrics"][best]["brier"]
    c1, c2, c3 = st.columns(3)
    c1.metric("Incumbent Brier (1+, final fold)", f"{inc_brier:.6f}")
    c2.metric("Best challenger Brier", f"{chal_brier:.6f}", delta=f"{chal_brier - inc_brier:+.7f}")
    c3.metric("Cleared 95% bar on DEV first?", "YES" if dev_cleared else "NO")

    if not dev_cleared:
        st.warning(
            "**KEEP CURRENT GOALS MODEL.** No shot-quality challenger cleared the pre-registered "
            "95% bootstrap bar on development data (best was "
            f"{max(v['frac_improved'] for v in sq_results['dev_value_tests'].values())*100:.1f}%). "
            "The final-fold Brier deltas are real but microscopic (parts-per-million) -- consistent "
            "with shot-quality metrics being highly redundant with the shooting-talent signal "
            "already in the incumbent model (see PLAYER_GOALS_SHOT_QUALITY_REPORT.md Section E). "
            "The incumbent model is retained unchanged; nothing below is used in the live "
            "projection above."
        )

    rows_tbl = []
    for name, v in sq_results["challenger_final_metrics"].items():
        rows_tbl.append({"Challenger": name, "Final-fold Brier": round(v["brier"], 6),
                          "Delta vs incumbent": round(v["brier"] - inc_brier, 8),
                          "% beats incumbent (game-clustered)": f"{v['game_bootstrap_vs_incumbent']['frac_improved']*100:.1f}%",
                          "DEV frac_improved": f"{sq_results['dev_value_tests'][name]['frac_improved']*100:.1f}%"})
    st.dataframe(rows_tbl, use_container_width=True, hide_index=True)
    st.caption("DEV frac_improved is the pre-registered decision gate (95% required); the final-fold "
               "column is reported for completeness but was NOT used to decide adoption, per the "
               "frozen manifest's own rule.")

comp.render_provenance_panel()
