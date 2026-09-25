"""Page 32 — Model Learning / Health: the compact operational surface
for the 2026-27 Continuous Learning + Daily Model Audit framework
(owner directive, 2026-08-30). Reads operational/daily_model_review.py
directly -- this page never computes a metric itself, it only displays
what the real daily job already produced. Answers the owner's own
stated question: "IS THE ENGINE GETTING BETTER OR WORSE?" (Part 57).

No production model, decision_policy, or shadow-overlay parameter can
be changed from this page -- it is read-only, exactly like Model Health
and System Health."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import components as comp
from operational import challenger_registry as cr
from operational import daily_model_review as dmr
from operational import prospective_ledger as pl

st.title("Model Learning / Health")
comp.render_model_status_header()
st.caption("Prospective self-audit: real settled predictions only. Never mutates a production "
           "model, decision_policy, or shadow-overlay coefficient from this page.")

# Reliability fix (2026-09-24, docs/MODEL_REVIEW_ZERO_DATA_FIX.md): this
# used to special-case "the ledger file doesn't exist yet" separately
# from everything else -- which meant the moment the file existed (even
# with zero rows, e.g. after a settlement job runs once with nothing to
# settle), this branch was skipped and run_daily_review() had no
# sample-size check of its own, so the page fell through to a
# populated-looking "ENGINE STATUS: WATCH". pl.init_db() is idempotent
# and safe to call whether or not the file exists yet; run_daily_review()
# itself now returns an honest NO_DATA/INSUFFICIENT_SAMPLE engine_status
# (with incomplete=True) whenever there isn't enough real settled data,
# so there is no longer a separate case to special-case here.
conn = pl.open_for_dashboard()
result = dmr.run_daily_review(conn)

status = result["engine_status"]
comp.render_status_banner(status, f"ENGINE STATUS: {status}", f"Recommendation: {result.get('recommendation', 'N/A')}")

if result.get("incomplete"):
    st.caption(result["reason"])
    st.caption(
        "How this works: once enough real results exist, this page re-scores the production model "
        "and every registered challenger daily against those settled outcomes (Brier score, "
        "shadow-vs-production comparison, improvement queue). That daily re-scoring is read-only "
        "reporting -- it NEVER automatically changes the production model, decision_policy, or any "
        "shadow-overlay coefficient. Promoting a challenger or adjusting production logic always "
        "requires a separate, explicit, human-authorized change.")
    st.stop()

st.markdown("#### Trend (Part 57)")
cols = st.columns(4)
for col, window in zip(cols, ("LAST_1_DAY", "LAST_7_DAYS", "LAST_30_DAYS", "SEASON_TO_DATE")):
    window_scores = result["scores_by_window"].get(window, {})
    with col:
        st.markdown(f"**{window.replace('_', ' ').title()}**")
        if not window_scores:
            st.caption("No settled observations")
            continue
        for key, sc in window_scores.items():
            n = sc["event_count"]
            if n < 20:
                st.caption(f"{key}: LOW SAMPLE (n={n})")
            else:
                st.metric(key, f"Brier {sc['brier_score']:.3f}" if sc["brier_score"] is not None else "—",
                          f"n={n}")

st.markdown("#### Shadow vs. production")
for name, comparison in result["shadow_vs_production"].items():
    if name == "_diagnostics":
        continue
    with st.expander(f"{name} (n={comparison['n']})"):
        if comparison["n"] == 0:
            st.caption("No paired observations yet.")
        else:
            st.write(f"Mean shadow − base: {comparison['mean_shadow_minus_base']:+.4f}")
diagnostics = result["shadow_vs_production"].get("_diagnostics", {})
for name, note in diagnostics.items():
    st.caption(f"{name}: {note}")

st.markdown("#### Alerts / improvement queue")
if not result["improvement_queue"]:
    st.caption("No action needed today.")
else:
    for item in result["improvement_queue"]:
        st.markdown(f"- **{item['issue']}** (magnitude {item['magnitude']}, source {item['source']})")

st.markdown("#### Challengers")
registry = cr.load_registry()
if not registry:
    st.caption("No challengers proposed yet.")
else:
    for entry in registry:
        st.markdown(f"- `{entry['challenger_id']}` ({entry['status']}): {entry['hypothesis']}")

with st.expander("Season leaderboard (technical detail)"):
    st.json(result["season_leaderboard"])

comp.render_provenance_panel()
