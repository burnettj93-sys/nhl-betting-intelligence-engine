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

MARKET_TONE = {"PRICING_ACTIVE": "good", "DISPLAY_ONLY": "muted", "MODEL_READY_PRICES_NOT_CAPTURED": "warn", "MODEL_READY_PRICES_GATED_BY_CREDIT_BUDGET": "warn",
               "PRICED_ONLY_WITH_CONFIRMED_STARTER": "warn", "PRICING_ACTIVE_ELO_NOT_THIS_MODEL": "warn"}
def _summ(model_id):
    m = next((x for x in mh["models"] if x["id"] == model_id), None)
    return ((m or {}).get("validation") or {}).get("summary") or {}


_sk, _gl = _summ("skater-projection"), _summ("goalie-saves")
_active = sum(1 for m in mh["models"] for v in (m.get("markets") or {}).values() if v == "PRICING_ACTIVE")
_gated = sum(1 for m in mh["models"] for v in (m.get("markets") or {}).values() if v != "PRICING_ACTIVE")
st.subheader("What the evidence does and does not say")
_rows = [
    ("Data freshness", "see Data Status", "Each source's data-through date, last fetch, age and next refresh are on Data Status; the pipelines below show how far each feed runs."),
    ("Market support", f"{_active} priced · {_gated} gated or display-only", "Whether a market has real DraftKings prices captured and a model allowed to price it (per-model chips below). Limited by the credit allowance and by starter confirmation for saves."),
    ("Predictive validation", f"skaters {_sk.get('beating_baselines', '?')}/{_sk.get('markets_scored', '?')} · goalie saves {_gl.get('beating_baselines', '?')}/{_gl.get('markets_scored', '?')} beat baselines",
     "Chronological walk-forward on seasons the model never saw: better than simple base-rate and rolling-average baselines on log loss. It says the probabilities are informative, not that prices are wrong."),
    ("Calibration", "calibrated on average; caveats", "Probabilities are Platt-calibrated on 2024-25 and tested on 2025-26. With fewer than 40 prior NHL games the model still over-predicts, so those players are not priced (docs/validation/low_sample_calibration.json). "
     "For veterans in the 50–62% band it under-predicted by about 3 points, and an individual player's probability was off by about 8–10 points (1 SD): an edge smaller than that is an estimate, not a finding (docs/SELECTOR_AUDIT.md)."),
    ("Betting-value evidence", "none yet", "There are no historical DraftKings prices to test against and the live paper book has only a handful of tickets. A positive 'edge' on a card is a model estimate after a 3-point haircut, not proof of profit."),
]
st.markdown("| Kind of evidence | Status | What it says |\n|---|---|---|\n" + "\n".join(f"| **{a}** | {ui.esc(b)} | {ui.esc(c)} |" for a, b, c in _rows))

st.subheader("Data pipelines")
st.dataframe([{"Pipeline": p["name"], "Source": p["source"], "Data through": (p["through"] or "n/a")[:16].replace("T", " "), "Detail": p.get("detail") or ""} for p in mh["pipelines"]], hide_index=True, width="stretch")

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
            if "puck_line" in v:
                pl = v["puck_line"]
                st.write(f"Puck line (home −1.5 covers): model log loss {pl['model']['log_loss']:.4f} vs {pl['base_rate_baseline']['log_loss']:.4f} for the base rate on {pl['games_scored']} held-out games — "
                         "it does not beat the base rate, so it is neither shown nor priced.")
            st.caption(f"{r['games_scored']} held-out games scored; {r['shootouts_excluded']} shootout games excluded (no winner in the source). Lower is better.")
        st.markdown("**Markets and ticket status**")
        st.markdown(" ".join(ui.chip(f"{k}: {val.replace('_', ' ').title()}", MARKET_TONE.get(val, "bad" if "BLOCKED" in val else "muted")) for k, val in m["markets"].items()),
                    unsafe_allow_html=True)
        st.markdown("**Limits**")
        for lim in m["limits"]:
            st.markdown("- " + ui.esc(lim))

pro = mh.get("prospective") or {}
if pro:
    st.subheader("Evidence collecting on live games")
    st.caption("Backtests cannot show whether a model beats a sportsbook price, so two checks accumulate on games as they are played. Neither changes any ticket.")
    ml = pro.get("moneyline") or {}
    if "error" not in ml and ml:
        ll = ml.get("log_loss") or {}
        st.markdown(f"**Moneyline: Elo (prices tickets) vs strength model vs market.** {ml.get('scored', 0)} finished game-sides scored of {ml.get('logged', 0)} logged "
                    f"(a verdict needs {ml.get('min_games_for_a_claim')}).")
        if ll:
            st.dataframe([{"Source": {"elo": "Elo (current ticket pricing)", "strength": "Strength model (goalie-team-v1)", "market_no_vig": "Sportsbook price, vig removed"}[k],
                           "Log loss (lower is better)": v} for k, v in ll.items()], hide_index=True, width="stretch")
    pl = pro.get("puck_line") or {}
    if pl and "error" not in pl:
        st.markdown(f"**Puck line alternative `{pl.get('version')}`** (parameters frozen, untouched 2026-27 games only): {pl.get('scored', 0)} finished of {pl.get('logged', 0)} logged "
                    f"(a review needs {pl.get('min_games')}). Verdict: {str(pl.get('verdict', '')).replace('_', ' ').lower()}.")

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
