"""Page 6 — Goalie Intelligence (Research): Stage 1 pregame starting-
goalie projection, built entirely from real historical rotation data.
STARTER INTELLIGENCE: RESEARCH / HISTORICAL INFERENCE -- no live source
is integrated, so every probability shown is PROJECTED, never
CONFIRMED. See GOALIE_INTELLIGENCE_FOUNDATION_REPORT.md."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import components as comp
from dashboard import data_access as da
from dashboard import goalie_view as gv
from dashboard import goalie_quality_view as gqv
from research.goalie_intelligence import features as gf
from research.goalie_intelligence import quality as gq


@st.cache_data(show_spinner="Loading real NHL corpus...")
def _load_predictions():
    return da.compute_baseline_predictions()


@st.cache_data(show_spinner="Loading real historical starter corpus...")
def _load_starter_rows():
    return gf.load_starter_corpus()


@st.cache_data(show_spinner=False)
def _load_goalie_results():
    return gv.load_results()


@st.cache_data(show_spinner=False)
def _load_quality_results():
    return gqv.load_results()


@st.cache_data(show_spinner="Loading real goalie-quality appearance corpus...")
def _load_quality_rows():
    return gq.load_appearance_corpus()


@st.cache_data(show_spinner=False)
def _goalie_name_lookup(_all_rows):
    """BUG-203 (preseason product audit, Section AE performance check):
    leading underscore skips Streamlit's per-call hashing of the ~10.4k-row
    starter corpus -- same class of issue as the 189k-row SOG corpus bug
    found in an earlier slice (dashboard/pages/7_Player_SOG_Research.py),
    smaller in magnitude here but the identical anti-pattern, worth fixing
    for consistency now that it's been named."""
    lookup = {}
    for r in _all_rows:
        lookup[r["starter_goalie_id"]] = r["starter_goalie_name"]
        for a in r["other_appearances"]:
            lookup.setdefault(a["goalie_id"], a["goalie_name"])
    return lookup


st.title("Goalie Intelligence (Research)")
comp.render_model_status_header()
comp.render_data_mode_badge()
st.markdown(
    """
    <div style="border:1px solid #5c4a1a; border-radius:6px; padding:8px 12px;
                background:#211a08; color:#e0c060; font-size:0.85rem; margin-bottom:12px;">
      STARTER INTELLIGENCE: RESEARCH / HISTORICAL INFERENCE — Stage 1 foundation only.
      No live external source is integrated (see GOALIE_INTELLIGENCE_FOUNDATION_REPORT.md).
      Every probability below is <b>PROJECTED</b> from real historical rotation data —
      never CONFIRMED, and never a claim about who will actually start a future game.
    </div>
    """,
    unsafe_allow_html=True,
)

goalie_results = _load_goalie_results()
if goalie_results is None:
    st.warning("research/goalie_intelligence_results.json not found — run "
               "`python3 research/run_goalie_intelligence.py` first.")
    st.stop()

st.markdown("### Model vs. naive baselines (real historical evaluation)")
me = goalie_results["model_eval_true_holdout"]
c1, c2, c3 = st.columns(3)
c1.metric("Model top-1 accuracy", f"{me['top1_accuracy']*100:.1f}%")
c2.metric("Model Brier score", f"{me['brier']:.4f}")
c3.metric("Model log loss", f"{me['log_loss']:.4f}")

baselines = goalie_results["baseline_results_true_holdout"]
cols = st.columns(len(baselines))
for col, (name, b) in zip(cols, baselines.items()):
    col.metric(name.replace("_", " "), f"{b['accuracy']*100:.1f}%" if b["accuracy"] else "N/A")
st.caption(f"Evaluated on {me['n']} real held-out team-games (2024-25 + 2025-26). "
           f"The fitted model beats every naive baseline — see Research Lab / "
           f"GOALIE_INTELLIGENCE_FOUNDATION_REPORT.md for full detail.")

st.markdown("### Empirical back-to-back finding")
b2b = goalie_results["back_to_back_stats"]
st.markdown(f"Across **{b2b['total_back_to_backs']}** real team back-to-backs in the corpus, "
            f"the **same goalie started both games only {b2b['same_goalie_pct']}%** of the time "
            f"({b2b['same_goalie_both_games']} / {b2b['total_back_to_backs']}) — the starter "
            f"changed **{b2b['starter_changed_pct']}%** of the time. This is the real measured "
            f"rate, not an assumption.")

st.divider()
st.markdown("### Try a projection (real historical date)")

try:
    predictions = _load_predictions()
    starter_rows = _load_starter_rows()
except da.DataAvailabilityError as exc:
    comp.render_missing_data_page(exc)
    st.stop()

name_lookup = _goalie_name_lookup(starter_rows)
weights_dict = goalie_results["fitted_weights"]
from research.goalie_intelligence.model import FEATURE_NAMES
weights = [weights_dict[name] for name in FEATURE_NAMES]

teams = da.all_teams(predictions)
seasons = da.available_seasons(predictions)
col1, col2, col3 = st.columns(3)
with col1:
    team = st.selectbox("Team", teams, index=teams.index("WPG") if "WPG" in teams else 0)
with col2:
    season = st.selectbox("Season", seasons, index=len(seasons) - 1, format_func=da.format_season)
with col3:
    team_dates = sorted({r["game_date"] for r in starter_rows if r["team"] == team and r["season"] == season})
    as_of = st.selectbox("Project as of date", team_dates, index=len(team_dates) - 1) if team_dates else None

if as_of:
    projection = gv.project_starters_for_team_date(starter_rows, weights, team, as_of, season)
    if projection["status"] == "INSUFFICIENT_HISTORY":
        st.info(f"Not enough real historical data for {team} before {as_of} to project a starter.")
    else:
        st.markdown(f"#### TEAM: {team}")
        for c in projection["candidates"]:
            name = name_lookup.get(c["goalie_id"], c["goalie_id"])
            st.metric(name, f"{c['probability']*100:.1f}%")
        cc1, cc2 = st.columns(2)
        cc1.markdown(f"**STATUS:** {comp.label_badge('PROJECTED', 'research')}", unsafe_allow_html=True)
        cc2.markdown(f"**CONFIDENCE:** {projection['confidence']}")
        if projection["is_back_to_back"]:
            st.caption("Team is on a back-to-back as of this date.")
        st.markdown("**DRIVERS:**")
        for d in projection["drivers"]:
            st.markdown(f"- {d}")
        st.caption(f"Based on {projection['history_games']} real prior team games. "
                   "This is a research projection reconstructed from historical rotation "
                   "data as of a past date — NOT a live pregame confirmation.")

st.divider()
st.markdown("### Goalie Quality x Starter Probability Integration (Experiment)")
st.markdown(
    """
    <div style="border:1px solid #5c4a1a; border-radius:6px; padding:8px 12px;
                background:#211a08; color:#e0c060; font-size:0.85rem; margin-bottom:12px;">
      STATUS: RESEARCH — NOT PRODUCTION. Combines the PROJECTED starter distribution above
      with two goalie-quality candidate metrics via a scenario-weighted probability mixture.
      See GOALIE_QUALITY_INTEGRATION_REPORT.md for the full evaluation — this panel is for
      inspecting individual real games only, and does not replace the production win
      probability shown elsewhere in this dashboard.
    </div>
    """,
    unsafe_allow_html=True,
)

quality_results = _load_quality_results()
if quality_results is None:
    st.info("research/goalie_quality_integration_results.json not found — run "
            "`python3 research/run_goalie_quality_comparison.py` first.")
else:
    c1, c2, c3 = st.columns(3)
    c1.metric("Eval Brier: baseline", f"{quality_results['headline_metrics']['baseline']['brier']:.4f}")
    c2.metric("Eval Brier: + save% quality (mixture)", f"{quality_results['headline_metrics']['mix_a']['brier']:.4f}")
    c3.metric("Eval Brier: + GSAx-style quality (mixture)", f"{quality_results['headline_metrics']['mix_b']['brier']:.4f}")
    st.caption("Recommendation: **KEEP CURRENT MODEL** — see GOALIE_QUALITY_INTEGRATION_REPORT.md's "
               "Final Questions section. Neither candidate cleared the full adoption gate this slice.")

    quality_rows = _load_quality_rows()
    st.markdown("#### Inspect a real game")
    qteam = st.selectbox("Team", teams, index=teams.index("WPG") if "WPG" in teams else 0, key="qteam")
    qseason = st.selectbox("Season", seasons, index=len(seasons) - 1, format_func=da.format_season, key="qseason")
    team_games = [g for g in da.games_for_team(predictions, qteam) if g["season"] == qseason]
    if team_games:
        labels = [f"{g['game_date']} — {g['home_team']} vs {g['away_team']}" for g in team_games]
        idx = st.selectbox("Real game", range(len(team_games)), index=len(team_games) - 1,
                            format_func=lambda i: labels[i], key="qgame")
        game = team_games[idx]
        view = gqv.compute_matchup_quality_view(
            starter_rows, quality_rows, weights, game["home_team"], game["away_team"],
            game["game_date"], game["season"], game["p_home"], quality_results)
        if view["status"] == "INSUFFICIENT_HISTORY":
            st.info("Not enough real historical data for one of these teams before this date.")
        else:
            for label, side in [("HOME: " + game["home_team"], view["home"]), ("AWAY: " + game["away_team"], view["away"])]:
                st.markdown(f"**{label}**" + (" (back-to-back)" if side["is_back_to_back"] else ""))
                for r in sorted(side["candidates"], key=lambda x: -x["probability"]):
                    name = name_lookup.get(r["goalie_id"], r["goalie_id"])
                    st.markdown(
                        f"- {name}: P(starts)={r['probability']*100:.1f}%  |  "
                        f"save% quality Δ={r['save_pct_quality_elo_delta']:+.1f} Elo pts "
                        f"(n={r['save_pct_quality_sample_shots']:.0f} shots)  |  "
                        f"GSAx-style logit adj={r['gsax_quality_adj_logit']:+.4f} "
                        f"(n={r['gsax_quality_sample_shots']:.0f} shots)")
            st.markdown("#### Scenario-weighted research probability (P(home wins))")
            r1, r2, r3, r4 = st.columns(4)
            r1.metric("Baseline (production-equivalent)", f"{view['p_baseline']*100:.1f}%")
            r2.metric("+ save% quality (mixture)", f"{view['p_mix_a_save_pct_quality']*100:.1f}%")
            r3.metric("+ GSAx-style quality (mixture)", f"{view['p_mix_b_gsax_quality']*100:.1f}%")
            r4.metric("+ save% quality (top-1 only)", f"{view['p_top1_a_save_pct_quality']*100:.1f}%")
            st.markdown(f"**STATUS:** {comp.label_badge('RESEARCH — NOT PRODUCTION', 'research')}",
                        unsafe_allow_html=True)
            st.caption("Mixture probabilities use the full P(h)×P(a) scenario weighting over every "
                       "candidate goalie on both sides (Σ_h Σ_a), not just the most likely starter. "
                       f"GSAx-style window used: {view['gsax_window_used']}.")
    else:
        st.info(f"No real {da.format_season(qseason)} games found for {qteam}.")

comp.render_provenance_panel()
