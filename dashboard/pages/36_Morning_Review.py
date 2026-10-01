"""Page 36 — Morning Review (Live Odds/Parlay/Post-Mortem activation
sprint, 2026-09-15, Parts 54-69). The daily quantitative post-mortem:
what worked, what didn't, why, normal variance vs. systematic issue,
what to investigate, and whether there's a real software bug or a
model/challenger hypothesis worth pursuing.

Read-only against operational/paper_bankroll.db -- never writes a
report file itself (that's operational.daily_postmortem.write_report_
markdown's job, run from the actual daily job/scheduler, not on every
page view) and never touches decision_policy, research/model_registry.py,
or challenger_registry.json (Part 66)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import auth
from dashboard import cloud_snapshot
from dashboard import components as comp
from dashboard import formatting as fmt
from operational import daily_postmortem as dpm
from operational import paper_bankroll as pb
from operational import runtime_mode

# Community Cloud: an operational surface, ADMIN-only (server-side; the nav omission is only UX).
if runtime_mode.is_community_cloud():
    auth.require_admin()

st.title("Morning Review")
comp.render_model_status_header()
st.caption("Answers every morning: what worked, what didn't, why, whether it's normal variance or a "
           "systematic issue, and what (if anything) should be investigated. Never auto-changes a "
           "production model, threshold, or decision policy -- see PRIVACY_AND_COMPLIANCE-style "
           "boundary in this module's own docstring.")

if runtime_mode.is_community_cloud():
    # Community Cloud never computes a post-mortem: it displays the one the local
    # engine produced and published (Cloud live-data sprint).
    try:
        report = cloud_snapshot.morning_review_report()
    except cloud_snapshot.SnapshotUnavailable as _exc:
        st.warning(f"The Morning Review is not available in the snapshot currently being served ({_exc}).")
        st.stop()
    st.caption(f"Report computed by the local engine at {report.get('generated_at_utc', 'an unknown time')}.")
else:
    conn = pb.open_for_dashboard()
    # dashboard/*.py must never import db.py directly -- see
    # operational/daily_postmortem.py::open_daily_postmortem_report()'s own
    # docstring (same rationale as operational/real_today_bridge.py).
    report = dpm.open_daily_postmortem_report(conn)

st.markdown("## Yesterday's Scoreboard")
if report["scoreboard"]["tracks"]:
    cols = st.columns(len(report["scoreboard"]["tracks"]))
    for col, (track, data) in zip(cols, report["scoreboard"]["tracks"].items()):
        s = data["bankroll_summary"]
        with col:
            st.markdown(f"**{track}**")
            st.metric("Bankroll", f"${s['current_bankroll']:,.2f}", delta=f"{s['net_profit']:+,.2f}")
            st.caption(f"{s['wins']}W-{s['losses']}L-{s['voids']}V ({s['pending']} pending)")
            st.caption(f"ROI {fmt.format_probability(s['roi']) if s['roi'] is not None else '—'}")

st.divider()
st.markdown("## Game Edge Parlay Health")
health = report["parlay_health"]
if health["status"] == "WAITING_FOR_SETTLED_DATA":
    comp.render_empty_state("WAITING_FOR_ODDS", "No Game Edge Parlay has settled yet -- expected until "
                                                  "real 2026-27 games are played, not an error.")
else:
    hc1, hc2, hc3, hc4 = st.columns(4)
    hc1.metric("Settled parlays", health["settled_parlays"])
    hc2.metric("Actual hit rate", fmt.format_probability(health["actual_hit_rate"]))
    hc3.metric("Avg modeled joint P", fmt.format_probability(health["avg_modeled_joint_probability"]))
    hc4.metric("Calibration gap", f"{health['calibration_gap']:+.1%}")
    from research.game_edge_parlay.engine import TARGET_JOINT_PROBABILITY
    st.caption(f"Target ≈{TARGET_JOINT_PROBABILITY:.0%} modeled joint probability (a preference for "
               f"ranking and this tracking, never a hard qualification cutoff or something this "
               f"engine games to look right).")

st.divider()
st.markdown("## Real-Market Parlay Health")
rm_health = report["real_market_parlay_health"]
if rm_health["status"] == "WAITING_FOR_SETTLED_DATA":
    comp.render_empty_state("WAITING_FOR_ODDS", "No real-market parlay has settled yet -- expected "
                                                  "until real 2026-27 games are played, not an error.")
else:
    rc1, rc2, rc3, rc4 = st.columns(4)
    rc1.metric("Settled parlays", rm_health["settled_parlays"])
    rc2.metric("Actual hit rate", fmt.format_probability(rm_health["actual_hit_rate"]))
    rc3.metric("Avg modeled joint P", fmt.format_probability(rm_health["avg_modeled_joint_probability"]))
    rc4.metric("Calibration gap", f"{rm_health['calibration_gap']:+.1%}")

st.markdown("### Real-Market Parlay Post-Mortems")
st.caption("Every settled real-money-adjacent parlay loss, re-resolved against the official boxscore to "
           "show exactly which leg(s) missed and by how much -- never a guessed cause.")
rm_postmortems = report.get("real_market_parlay_postmortems")
if not isinstance(rm_postmortems, list):
    st.caption("Not available on this report.")
elif not rm_postmortems:
    st.caption("No real-market parlay losses yet.")
else:
    for pm in rm_postmortems:
        with st.expander(f"paper_bet_id {pm['paper_bet_id']} -- stake ${pm['stake']:.2f}, "
                          f"P&L {pm['profit_loss']:+.2f}" if pm.get("profit_loss") is not None
                          else f"paper_bet_id {pm['paper_bet_id']} -- stake ${pm['stake']:.2f}"):
            st.markdown(f"**Why:** {pm['why']}")
            if pm["hit_legs"]:
                st.caption("Legs that hit: " + "; ".join(
                    f"{h['participant_name']} ({h['market_family']})" for h in pm["hit_legs"]))

    rm_patterns = report.get("real_market_parlay_leg_miss_patterns")
    if isinstance(rm_patterns, list) and rm_patterns:
        st.markdown("**Miss pattern by market family:**")
        for p in rm_patterns:
            st.caption(f"{p['market_family']}: {p['occurrences']} miss(es) across "
                       f"{p['unique_game_dates']} game date(s) -- {p['recommended_action']}")

st.divider()
st.markdown("## SOG / Saves Thesis Tracking")
st.caption("The owner's hypothesis: SOG and Saves should produce smaller, more repeatable edges than "
           "other markets.")
_demo_breakdown = report["scoreboard"]["tracks"].get("DEMO_PAPER", {}).get("breakdowns", {}).get("by_market_family", {})
thesis_cols = st.columns(3)
for col, family in zip(thesis_cols, ("PLAYER_SOG", "GOALIE_SAVES", "GAME_EDGE_PARLAY")):
    row = _demo_breakdown.get(family)
    with col:
        st.markdown(f"**{family}**")
        if not row:
            st.caption("No settled bets yet.")
        else:
            st.metric("ROI", fmt.format_probability(row["roi"]) if row["roi"] is not None else "—")
            st.caption(f"Hit rate {fmt.format_probability(row['hit_rate']) if row['hit_rate'] is not None else '—'} "
                       f"({row['bets']} bets)")

st.divider()
st.markdown("## What Worked / What Didn't")
w1, w2 = st.columns(2)
w1.markdown("**What worked**")
w1.caption(report["what_worked"])
w2.markdown("**What didn't**")
w2.caption(str(report["what_didnt"]))

st.markdown("### Normal Variance vs. Systematic")
st.caption(report["normal_variance_vs_systematic"])

st.divider()
st.markdown("## Recommended Actions")
if not report["investigate"]:
    comp.render_empty_state("NO_QUALIFYING_OPPORTUNITIES", "No pattern this cycle cleared even the WATCH bar.")
else:
    for issue in report["investigate"]:
        badge_tone = "unavailable" if issue["recommended_action"] in ("BUG_FIX", "HALT_MARKET") else "input"
        st.markdown(
            f"{comp.label_badge(issue['recommended_action'], badge_tone)} **{issue['category']}** "
            f"— {issue['occurrences']} occurrence(s), {issue['unique_game_dates']} unique date(s)",
            unsafe_allow_html=True)
        st.caption(issue.get("explanation", ""))

if report["software_bug_candidates"]:
    with st.expander(f"Bug-fix candidates ({len(report['software_bug_candidates'])})"):
        for c in report["software_bug_candidates"]:
            st.caption(f"{c['category']}: {c.get('explanation', '')}")

if report["challenger_ideas"]:
    with st.expander(f"Challenger ideas ({len(report['challenger_ideas'])})"):
        st.caption("A challenger idea is a research recommendation only -- promoting one requires a "
                   "separate, explicit human action (operational.daily_postmortem.submit_challenger_idea), "
                   "never automatic.")
        for c in report["challenger_ideas"]:
            st.caption(f"{c['category']}: {c.get('explanation', '')}")

st.divider()
st.markdown("## Failure Taxonomy Distribution")
st.dataframe([{"Category": k, "Count": v} for k, v in report["failure_summary"].items() if v > 0]
             or [{"Category": "—", "Count": 0}], width="stretch")

comp.render_provenance_panel()
