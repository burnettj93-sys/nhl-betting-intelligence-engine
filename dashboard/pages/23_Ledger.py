"""Page 23 — Bet / Observation Ledger: real, persisted (SQLite via
operational/prospective_ledger.py). No synthetic P&L (Preseason
Operationalization sprint, Section 54-58). Separate tabs per record type
so REAL_BET performance can never be visually or numerically confused
with MODEL_OBSERVATION / SHADOW_POLICY_OBSERVATION / HISTORICAL_RESEARCH."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import components as comp
from operational import prospective_ledger as pl

st.title("Bet / Observation Ledger")
comp.render_model_status_header()

if not pl.DB_PATH.exists():
    comp.render_empty_state("NO_QUALIFYING_OPPORTUNITIES",
                             "No prospective observations have been recorded yet. The ledger database is "
                             "created automatically the first time a prediction is recorded.")
    st.stop()

conn = pl.init_db(pl.DB_PATH)
summary = pl.summary_metrics(conn)
op_summary = pl.operational_summary(conn)

st.markdown("#### Prospective Recording Status")
o1, o2, o3 = st.columns(3)
o1.metric("Total recorded", op_summary["total"])
o2.metric("Recorded today (UTC)", op_summary["recorded_today"])
o3.metric("Pending settlement (past event start)", op_summary["pending_settlement"])
st.caption("Last recorded: " + (op_summary["last_recorded_at_utc"] or "—"))
if op_summary["by_checkpoint"]:
    checkpoint_line = " · ".join(f"{cp}: {n}" for cp, n in sorted(op_summary["by_checkpoint"].items()))
    st.caption(f"By checkpoint — {checkpoint_line}")
st.divider()

tab_real, tab_model, tab_shadow, tab_hist = st.tabs(
    ["Real Bets", "Model Observations", "Shadow Observations", "Historical Research"])

with tab_real:
    s = summary["REAL_BET"]
    if s["n"] == 0:
        st.info("NO REAL BETS RECORDED")
    else:
        c1, c2 = st.columns(2)
        c1.metric("Real bets recorded", s["n"])
        c2.metric("Total profit/loss", f"{s['total_profit_loss']:.2f}" if s["total_profit_loss"] is not None else "—")
        rows = pl.query_observations(conn, record_type="REAL_BET")
        st.dataframe(rows, width='stretch')

with tab_model:
    s = summary["MODEL_OBSERVATION"]
    c1, c2 = st.columns(2)
    c1.metric("Observations", s["n"])
    c2.metric("With known outcome", s["n_with_outcome"])
    rows = pl.query_observations(conn, record_type="MODEL_OBSERVATION")
    if rows:
        st.dataframe(rows, width='stretch')
    else:
        comp.render_empty_state("NO_QUALIFYING_OPPORTUNITIES", "No model observations recorded yet.")

with tab_shadow:
    s = summary["SHADOW_POLICY_OBSERVATION"]
    c1, c2 = st.columns(2)
    c1.metric("Shadow observations", s["n"])
    c2.metric("With known outcome", s["n_with_outcome"])
    rows = pl.query_observations(conn, record_type="SHADOW_POLICY_OBSERVATION")
    if rows:
        for prop in ("GOALS", "POINTS"):
            cohort = pl.raw_vs_adjusted_summary(conn, prop)
            if cohort["n"]:
                st.markdown(f"**{prop}** — n={cohort['n']}, with outcome={cohort['n_with_outcome']}")
                if cohort.get("raw_brier") is not None:
                    st.caption(f"Raw Brier: {cohort['raw_brier']:.4f} | Adjusted Brier: {cohort['adjusted_brier']:.4f}")
        st.dataframe(rows, width='stretch')
    else:
        comp.render_empty_state("NO_QUALIFYING_OPPORTUNITIES", "No shadow-policy observations recorded yet.")

with tab_hist:
    s = summary["HISTORICAL_RESEARCH"]
    st.metric("Historical research examples", s["n"])
    rows = pl.query_observations(conn, record_type="HISTORICAL_RESEARCH")
    if rows:
        st.dataframe(rows, width='stretch')
    else:
        st.caption("No historical research examples recorded in the ledger.")

st.divider()
st.caption("Real betting performance can never include MODEL_OBSERVATION, SHADOW_POLICY_OBSERVATION, "
           "or HISTORICAL_RESEARCH records — record types are enforced distinct at the database level.")
comp.render_provenance_panel()
