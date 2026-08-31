"""Page 28 — Combinations (Preseason Interactive Product sprint, Parts
75-81). Uses the REAL frozen joint-dependence parameters
(rho_by_name in research/joint_scoring_dependence_results.json) and the
REAL gaussian_copula_joint_upper_tail / logical_control_probability
functions -- never reimplemented -- applied to real demo players' real
marginal probabilities. Only the underlying sportsbook combination price
is simulated (and no combination here is ever priced from a real book)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import components as comp
from dashboard import data_access as da
from dashboard import demo_data as dd
from dashboard import formatting as fmt
from pricing import odds_math as pm
from research.joint_scoring_dependence.joint_models import (
    gaussian_copula_joint_upper_tail, logical_control_probability,
)

st.title("Combinations")
comp.render_model_status_header()
comp.render_global_search(key_prefix="combos")
st.markdown(
    f"""
    <div style="border:1px solid #5a4420; border-radius:6px; padding:8px 12px;
                background:#241c10; color:#e8c46a; font-size:0.85rem; margin-bottom:12px;">
      {dd.DEMO_MODE_LABEL} — joint probabilities below use the REAL, frozen, validated dependence
      parameters (Gaussian copula ρ, or exact logical identity where applicable) applied to real
      players' real marginal probabilities. No real DraftKings same-game-parlay price exists for
      any of these — any price shown would be a SIMULATED PARLAY PRICE, for UX review only.
    </div>
    """,
    unsafe_allow_html=True,
)

_joint_results = da.load_json_safely("research/joint_scoring_dependence_results.json")
if _joint_results is None:
    comp.render_empty_state("MODEL_NOT_OPERATIONAL", "Joint scoring dependence results not found.")
    st.stop()
RHO = _joint_results["rho_by_name"]

opportunities = {(o["player_id"], o["prop"]): o for o in dd.build_demo_opportunities()}
roster = dd.build_demo_roster()

COMBO_SPECS = [
    ("sog", "goals", "SOG3_GOAL", "structural (Gaussian copula)", False),
    ("sog", "assists", "SOG3_ASSIST", "structural (Gaussian copula)", False),
    ("sog", "points", "SOG3_POINT", "structural (Gaussian copula)", False),
    ("goals", "points", None, "exact logical identity", True),
    ("assists", "points", None, "exact logical identity", True),
]

shown = 0
for player in roster:
    if shown >= 12:
        break
    for leg_a, leg_b, rho_key, dependence_name, redundant in COMBO_SPECS:
        oa = opportunities.get((player.player_id, leg_a))
        ob = opportunities.get((player.player_id, leg_b))
        if oa is None or ob is None:
            continue
        p_a, p_b = oa["raw_probability"], ob["raw_probability"]
        naive = p_a * p_b
        if redundant:
            validated = logical_control_probability(min(p_a, p_b))
        else:
            rho = RHO.get(rho_key, 0.0)
            validated = gaussian_copula_joint_upper_tail(p_a, p_b, rho)

        with st.container(border=True):
            legs_label = f"{player.name} {oa['market']} {oa['threshold']} + {player.name} {ob['market']} {ob['threshold']}"
            if redundant:
                st.markdown(f"**{legs_label}**")
                st.markdown('<span class="redundant-badge">⚠ REDUNDANT / LOGICALLY CONTAINED</span>',
                            unsafe_allow_html=True)
                st.warning(f"{oa['market']} 1+ already implies {ob['market']} 1+ — the joint probability "
                           f"equals the smaller leg's own probability exactly, not the product of the two legs.")
            else:
                st.markdown(f"**{legs_label}**")
                comp.render_status_banner("VALIDATED", f"Joint model: {dependence_name}")

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Naive Independent P", fmt.format_probability(naive) if not redundant else "n/a")
            c2.metric("Validated Joint P", fmt.format_probability(validated))
            c3.metric("Dependence Effect", fmt.format_pp_delta(validated - naive) if not redundant else "—")
            c4.metric("Fair Odds", fmt.format_american_odds(pm.prob_to_american(validated)))

            st.caption("PROBABILITY MODEL: VALIDATED &nbsp;|&nbsp; PRICE: SIMULATED (UX review only) "
                       "&nbsp;|&nbsp; POLICY: DEMO ONLY — NOT OPERATIONAL", unsafe_allow_html=False)
        shown += 1
        if shown >= 12:
            break

comp.render_provenance_panel()
