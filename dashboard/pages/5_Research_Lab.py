"""Page 5 — Research Lab: summarizes the four completed real-data
feature experiments, parsed programmatically from
research/*_comparison_results.json (see research_view.py) — nothing
hand-typed. Status labels reflect each experiment's own report
conclusion, not a new judgment made here."""
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
from dashboard import research_view as rv

STATUS_COLORS = {
    "ADOPTED": "#3fae6a",
    "PROMISING BUT NOT ADOPTED": "#e0a83f",
    "INCONCLUSIVE": "#8b93a7",
    "REJECTED": "#e05c5c",
}

st.title("Research Lab")
comp.render_model_status_header()
st.caption("Four independent real-data experiments have been run against this engine's production "
           "Elo model. None has been adopted — see each experiment below for why.")

experiment_results = da.load_experiment_results()
summaries = rv.build_all_summaries(experiment_results)

all_delta_rows = []

for name, summary in summaries.items():
    st.divider()
    st.markdown(f"## {name}")
    if summary is None:
        st.warning(f"Result file not found: `{da.EXPERIMENT_RESULT_FILES[name]}`")
        continue

    st.markdown(f"**Final decision:** `{summary['final_decision']}`")
    baseline = summary["baseline"]
    st.caption(f"Baseline (current production model) on this experiment's evaluation set: "
               f"Brier {baseline['brier']:.5f}, log loss {baseline['log_loss']:.5f}, "
               f"n={baseline['n']} games")

    rows = []
    for label, cand in summary["candidates"].items():
        rows.append({
            "Candidate": label, "Status": cand["status"],
            "Brier": round(cand["brier"], 5) if cand["brier"] is not None else None,
            "Brier delta": round(cand["brier_delta_abs"], 6) if cand["brier_delta_abs"] is not None else None,
            "Brier delta %": round(cand["brier_delta_rel_pct"], 3) if cand["brier_delta_rel_pct"] is not None else None,
            "Log loss delta": round(cand["log_loss_delta_abs"], 6) if cand["log_loss_delta_abs"] is not None else None,
            "Bootstrap % favoring candidate": round(cand["frac_improved_brier"] * 100, 1)
                if cand["frac_improved_brier"] is not None else None,
        })
        all_delta_rows.append({
            "experiment": name, "candidate": label, "status": cand["status"],
            "brier_delta_pct": cand["brier_delta_rel_pct"],
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    for label, cand in summary["candidates"].items():
        with st.expander(f"{label} — why: {cand['status']}"):
            st.markdown(f"{comp.label_badge(cand['status'], 'research' if cand['status'] != 'REJECTED' else 'unavailable')}",
                        unsafe_allow_html=True)
            st.markdown(cand["status_reason"])
            if cand["season_deltas"]:
                st.markdown("**Season-by-season Brier delta:**")
                for season, delta in sorted(cand["season_deltas"].items()):
                    arrow = "improved" if (delta or 0) < 0 else "worse"
                    st.markdown(f"- {da.format_season(int(season))}: {delta:+.6f} ({arrow})")

st.divider()
st.markdown("## Experiment comparison")
if all_delta_rows:
    df = pd.DataFrame([r for r in all_delta_rows if r["brier_delta_pct"] is not None])
    if not df.empty:
        chart = alt.Chart(df).mark_bar().encode(
            x=alt.X("brier_delta_pct:Q", title="Brier delta vs. baseline (%) — negative = improvement"),
            y=alt.Y("candidate:N", title=None, sort="-x"),
            color=alt.Color("status:N", scale=alt.Scale(
                domain=list(STATUS_COLORS.keys()), range=list(STATUS_COLORS.values())), title="Status"),
            row=alt.Row("experiment:N", title=None),
            tooltip=["experiment", "candidate", "status", "brier_delta_pct"],
        ).properties(height=26 * df.groupby("experiment").size().max()).resolve_scale(y="independent")
        st.altair_chart(chart, use_container_width=True)
        st.caption("Zero = no change vs. the current production model. Bars scaled to the actual "
                   "percentages — deliberately not exaggerated. Negative = improvement, positive = worse.")

comp.render_provenance_panel()
