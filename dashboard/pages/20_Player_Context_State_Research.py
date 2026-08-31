"""Page 20 -- Player Context State Research. STATUS: RESEARCH -- NOT YET
A BETTING ADJUSTMENT. No decision_policy change has been made this
slice, and nothing here feeds run_slate.py. No sportsbook odds are read
or shown. See PLAYER_CONTEXT_STATE_VALIDATION_REPORT.md."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import components as comp
from dashboard import context_overlay_view as cov
from dashboard import player_context_state_view as cv

st.title("Player Context State Research")
comp.render_model_status_header()
comp.render_data_mode_badge()
st.markdown(
    f"""
    <div style="border:1px solid #5a1a1a; border-radius:6px; padding:8px 12px;
                background:#241010; color:#f88; font-size:0.85rem; margin-bottom:12px;">
      {cv.RESEARCH_DISCLAIMER}
    </div>
    """,
    unsafe_allow_html=True,
)

results = cv.load_results()
registry = cv.load_registry()
if results is None or registry is None:
    st.warning("research/player_context_state_results.json or _registry.json not found -- run "
               "`python3 -m research.run_player_context_state_model` then "
               "`python3 -m research.player_context_state.registry` first.")
    st.stop()

st.markdown("### Signal status by prop (Part 44 registry, mechanically derived from bootstrap CIs)")
props = sorted({e["prop"] for e in registry if e["prop"] != "ALL"})
prop_choice = st.selectbox("Prop", props)
prop_entries = [e for e in registry if e["prop"] == prop_choice]
for e in prop_entries:
    status = e["status"]
    color = "green" if status == "VALIDATED" else ("orange" if status == "PARTIAL" else "gray")
    st.markdown(f"**{e['signal']}** — :{color}[{status}]")
    st.caption(f"{e['effect_direction']} — {e['source']}")
    st.json(e["effect_magnitude_by_season"])

st.divider()
st.markdown("### COLD vs NORMAL residual, by season (real, frozen bootstrap output)")
block = results["props"][prop_choice]
for season in results["config"]["eval_seasons"]:
    b = block["by_season"][str(season)]
    st.markdown(f"**{season}**")
    cols = st.columns(3)
    cols[0].metric("COLD n", b["by_state"]["COLD"]["n"])
    cols[1].metric("NORMAL n", b["by_state"]["NORMAL"]["n"])
    cols[2].metric("HOT n", b["by_state"]["HOT"]["n"])
    st.json(b["cold_vs_normal_bootstrap"])
    st.caption("Regression-to-mean check (Part 24 — reported even when it contradicts the UNDER hypothesis):")
    st.json(b["regression_to_mean_check"])
    st.caption("Role-change confounding split (Part 26):")
    st.json(b["role_change_confounding"])

st.divider()
st.markdown("### MEDIA_SENTIMENT_STATE — NOT BUILT")
media = next((e for e in registry if e["signal"] == "MEDIA_SENTIMENT_STATE"), None)
if media is None:
    st.warning("MEDIA_SENTIMENT_STATE entry not found in the context registry -- registry file may be stale.")
else:
    st.error(media["operational_status"])

st.divider()
st.markdown("## Context-State Probability Overlay (Goals 1+ / Points 1+, COLD_AND_TOI_DECLINE only)")
st.markdown(
    f"""
    <div style="border:1px solid #5a1a1a; border-radius:6px; padding:8px 12px;
                background:#241010; color:#f88; font-size:0.85rem; margin-bottom:12px;">
      {cov.RESEARCH_OVERLAY_DISCLAIMER}
    </div>
    """,
    unsafe_allow_html=True,
)
overlay_results = cov.load_results()
overlay_registry = cov.load_registry()
if overlay_results is None or overlay_registry is None:
    st.info("research/context_overlay_results.json or _registry.json not found -- run "
            "`python3 -m research.run_context_overlay_model` then "
            "`python3 -m research.context_overlay.registry` first.")
else:
    overlay_prop = st.selectbox("Overlay prop", ["goals", "points"], key="overlay_prop")
    entry = next((e for e in overlay_registry if overlay_prop.upper() in e["signal"]), None)
    if entry is None:
        st.warning(f"No registry entry found for {overlay_prop} -- registry file may be stale.")
        st.stop()
    status_color = "green" if entry["validation_status"] == "VALIDATED_OVERLAY" else (
        "orange" if entry["validation_status"] == "PARTIAL" else "gray")
    st.markdown(f"**{entry['signal']}** — :{status_color}[{entry['validation_status']}]  "
                f"(operational status: {entry['operational_status']})")
    st.caption(f"Adjustment: {entry['adjustment_type']} — {entry['base_model']}")
    st.json(entry["adjustment_magnitude"])

    block = overlay_results["props"][overlay_prop]
    for season in overlay_results["config"]["eval_seasons"]:
        eb = block["eval"].get(str(season), block["eval"].get(season))
        if eb is None or eb.get("status") == "INSUFFICIENT_DATA":
            continue
        st.markdown(f"**{season}** (n={eb['n']})")
        cols = st.columns(4)
        cols[0].metric("Raw Brier", f"{eb['raw_brier']:.4f}")
        cols[1].metric("Adjusted Brier", f"{eb['adjusted_brier']:.4f}", delta=f"{eb['adjusted_brier']-eb['raw_brier']:.4f}")
        cols[2].metric("Raw log loss", f"{eb['raw_log_loss']:.4f}")
        cols[3].metric("Adjusted log loss", f"{eb['adjusted_log_loss']:.4f}",
                        delta=f"{eb['adjusted_log_loss']-eb['raw_log_loss']:.4f}")
        st.caption("Calibration before / after (mean predicted vs mean actual):")
        st.json({"raw": eb["raw_calibration"], "adjusted": eb["adjusted_calibration"]})
        st.caption("Game-clustered bootstrap (Brier improvement):")
        st.json(eb["game_bootstrap_brier"])

st.divider()
st.caption(
    "Full narrative, arena-effect methodology, and freeze manifest: see "
    "PLAYER_CONTEXT_STATE_VALIDATION_REPORT.md and CONTEXT_STATE_PROBABILITY_OVERLAY_REPORT.md "
    "at the repo root."
)
