"""Model Health — what is actually running: each model's purpose, data freshness and season coverage, validation evidence, limits, and
whether it is supplying tickets, per market. Status is stated as it is; partial or unvalidated is never relabelled."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import product_source as ps
from dashboard import ui

ui.header("Model Health", "The models behind the numbers: freshness, validation evidence, limits, and which markets they price.")
mh = ui.load(ps.model_health, "Model health")

MARKET_TONE = {"PRICING_ACTIVE": "good", "DISPLAY_ONLY": "muted", "MODEL_READY_PRICES_NOT_CAPTURED": "warn"}
st.subheader("Data pipelines")
st.dataframe([{"Pipeline": p["name"], "Source": p["source"], "Data through": (p["through"] or "n/a")[:16].replace("T", " ")} for p in mh["pipelines"]], hide_index=True, width="stretch")

st.subheader("Models in use")
for m in mh["models"]:
    with st.expander(f"{m['name']} — {m['version']}  ·  {m['role'].replace('_', ' ').title()}", expanded=True):
        st.write(m["purpose"])
        d = m["data"]
        age = d.get("age_days")
        cols = st.columns(2)
        cols[0].metric("Data through", d["through"] or "n/a", f"{age} day(s) old" if age is not None else None, delta_color="off")
        extra = {k: v for k, v in d.items() if k not in ("source", "through", "age_days")}
        cols[1].metric("Season coverage", " · ".join(f"{v} {k.replace('_', ' ')}" for k, v in extra.items()) or "—")
        st.caption(f"Source: {d['source']}")
        v = m["validation"]
        st.markdown("**Validation evidence**")
        sp = v["split"]
        st.caption("Chronological split — " + ", ".join(f"{k.replace('_', ' ')}: {val}" for k, val in sp.items()) + f". Report: `{v['report']}`.")
        if "summary" in v:
            s = v["summary"]
            st.write(f"{s['beating_baselines']} of {s['markets_scored']} threshold markets beat both simple baselines on log loss in the held-out season."
                     + (f" Not beating: {', '.join(s['not_beating'])}." if s["not_beating"] else ""))
        if "method" in v:
            st.caption(v["method"])
        if "range_coverage" in v:
            rc = v["range_coverage"]
            st.write(f"80% saves range contained the result {rc['final_season']:.0%} of the time in the held-out season (nominal 80%).")
            e = v["error"]
            st.caption(f"Mean absolute error on {e['n']} held-out goalie games: saves {e['saves_model']:.2f} vs {e['saves_league_mean']:.2f} for the league-average guess; goals against {e['goals_against_model']:.2f} vs {e['goals_against_league']:.2f}.")
        if "result" in v:
            r = v["result"]
            st.dataframe([{"Win-probability variant": k.replace("_", " ").title(), "Log loss": r[k]["log_loss"], "Brier": r[k]["brier"]} for k in
                          ("home_rate_baseline", "strength_only", "strength_and_named_goalies", "strength_and_goalies_tied")], hide_index=True, width="stretch")
            st.caption(f"{r['games_scored']} held-out games scored; {r['shootouts_excluded']} shootout games excluded (no winner in the source). Lower is better.")
        st.markdown("**Markets and ticket status**")
        st.markdown(" ".join(ui.chip(f"{k}: {val.replace('_', ' ').title()}", MARKET_TONE.get(val, "bad" if "BLOCKED" in val else "muted")) for k, val in m["markets"].items()),
                    unsafe_allow_html=True)
        st.markdown("**Limits**")
        for lim in m["limits"]:
            st.markdown("- " + ui.esc(lim))

st.subheader("Market coverage for tickets")
cov = mh.get("market_coverage") or {}
rows = cov.get("rows") or []
if rows:
    st.dataframe([{"Market": r["market"], "In ticket allow-list": "yes" if r.get("in_ticket_allowlist") else "no",
                   "Projection": r["components"]["projection"]["status"].title(), "Prices": r["components"]["prices"]["status"].title(),
                   "Context confirmation": r["components"]["context_confirmation"]["status"].title(),
                   "Settlement": r["components"]["settlement"]["status"].title(), "Ontario menu": r.get("ontario_menu", "")} for r in rows], hide_index=True, width="stretch")
    if cov.get("price_feed_caveat"):
        st.caption(ui.esc(cov["price_feed_caveat"]))
else:
    st.caption("Market coverage has not been published yet.")
st.caption("Nothing on this page claims profitability: there are no historical sportsbook prices to test against, so evidence is calibration and accuracy on held-out games, plus the forward record in Paper Performance.")
