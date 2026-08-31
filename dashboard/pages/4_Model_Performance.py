"""Page 4 — Model Performance: current production (Elo-only) model's
real historical calibration, Brier/log-loss, season breakdown, and
probability distribution. All metrics are computed via
research.elo_comparison's already-tested functions — nothing
recalculated here."""
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
from research import elo_comparison as ec


@st.cache_data(show_spinner="Loading real NHL corpus and computing baseline predictions...")
def _load_predictions() -> list[dict]:
    return da.compute_baseline_predictions()


st.title("Model Performance")
comp.render_model_status_header()
comp.render_data_mode_badge()
st.caption("All metrics below use ONLY real NHL evaluation data (research/real_nhl_results/). "
           "No synthetic nhl.db data is mixed into these numbers.")

try:
    records = _load_predictions()
except da.DataAvailabilityError as exc:
    comp.render_missing_data_page(exc)
    st.stop()

seasons = da.available_seasons(records)
eval_seasons = st.multiselect("Seasons to include", seasons, default=seasons, format_func=da.format_season)
scoped = [r for r in records if r["season"] in eval_seasons] if eval_seasons else records

if not scoped:
    st.warning("No games selected.")
    st.stop()

st.markdown("### Headline metrics")
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Evaluated games", len(scoped))
c2.metric("Brier score", f"{ec.brier_score(scoped):.4f}")
c3.metric("Log loss", f"{ec.log_loss(scoped):.4f}")
c4.metric("Mean predicted P(home)", f"{ec.mean_predicted_prob(scoped):.3f}")
c5.metric("Actual home win rate", f"{ec.actual_home_win_rate(scoped):.3f}")

st.markdown("### Calibration")
cal_table = ec.calibration_table(scoped)
cal_rows = [row for row in cal_table if row["n"] > 0]
cal_df = pd.DataFrame([
    {"predicted": (row["lo"] + row["hi"]) / 2, "actual": row["actual_rate"], "n": row["n"]}
    for row in cal_rows
])
ref_line = pd.DataFrame({"predicted": [0.25, 0.80], "actual": [0.25, 0.80]})
base = alt.Chart(ref_line).mark_line(strokeDash=[4, 4], color="#5c6579").encode(x="predicted:Q", y="actual:Q")
points = alt.Chart(cal_df).mark_circle(size=120, color="#5b8def").encode(
    x=alt.X("predicted:Q", title="Predicted probability", scale=alt.Scale(domain=[0.25, 0.80])),
    y=alt.Y("actual:Q", title="Actual win rate", scale=alt.Scale(domain=[0.25, 0.80])),
    size=alt.Size("n:Q", title="Games in bucket"),
    tooltip=["predicted", "actual", "n"],
)
st.altair_chart((base + points).properties(height=400), use_container_width=True)
st.caption("Dashed line = perfect calibration. Point size = number of games in that probability bucket. "
           "Low-N buckets (fewer than 30 games) are excluded from this chart.")
low_n = [row for row in cal_table if 0 < row["n"] < 30]
if low_n:
    st.caption(f"{len(low_n)} bucket(s) excluded for low N (<30 games).")

st.markdown("### Performance by season")
season_rows = []
for s in sorted(eval_seasons):
    s_recs = [r for r in scoped if r["season"] == s]
    if not s_recs:
        continue
    season_rows.append({
        "Season": da.format_season(s), "Games": len(s_recs),
        "Brier": round(ec.brier_score(s_recs), 4), "Log loss": round(ec.log_loss(s_recs), 4),
        "Mean predicted": round(ec.mean_predicted_prob(s_recs), 3),
        "Actual home win rate": round(ec.actual_home_win_rate(s_recs), 3),
    })
st.dataframe(pd.DataFrame(season_rows), use_container_width=True, hide_index=True)

st.markdown("### Probability distribution")
dist = ec.probability_distribution_summary(scoped)
d1, d2, d3, d4 = st.columns(4)
d1.metric("Median prediction", f"{dist['p50']:.3f}")
d2.metric("Predictions > 60%", f"{sum(1 for r in scoped if r['p_home'] > 0.6) / len(scoped) * 100:.1f}%")
d3.metric("Predictions > 65%", f"{sum(1 for r in scoped if r['p_home'] > 0.65) / len(scoped) * 100:.1f}%")
d4.metric("Predictions > 70%", f"{dist['frac_above_0_70'] * 100:.1f}%")

hist_df = pd.DataFrame({"p_home": [r["p_home"] for r in scoped]})
st.altair_chart(
    alt.Chart(hist_df).mark_bar(color="#5b8def").encode(
        x=alt.X("p_home:Q", bin=alt.Bin(step=0.05), title="Predicted P(home win)"),
        y=alt.Y("count():Q", title="Games"),
    ).properties(height=300),
    use_container_width=True,
)
st.caption("This is model-behavior visibility only — not a betting-value claim.")

comp.render_provenance_panel()
