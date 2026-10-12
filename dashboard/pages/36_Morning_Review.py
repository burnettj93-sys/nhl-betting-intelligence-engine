"""Morning Review — the daily review of the paper account: for each ticket settled on the review date, the frozen prices and
probabilities, each leg's result, the effect on the account, closing prices where captured, defect checks, and whether the evidence
supports changing anything. Automatic and manually added tickets are shown separately. Nothing here changes a model or a policy."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import cloud_snapshot
from dashboard import ui
from operational import paper_bankroll as pb
from operational import runtime_mode

# Read-only, built from the snapshot the engine already publishes to the public data branch: a visitor may read it. (It was admin-gated on the hosted app by mistake; the original design says USER-accessible.)
ui.header("Morning Review", "What was predicted and priced, what happened, what it did to the account, and what the evidence supports.")

if runtime_mode.is_community_cloud():
    try:
        report = cloud_snapshot.morning_review_report()
    except cloud_snapshot.SnapshotUnavailable as exc:
        ui.unavailable(str(exc), "The Morning Review")
        st.stop()
    st.caption(f"Report computed by the engine at {report.get('generated_at_utc', 'an unknown time')}.")
else:
    from operational import daily_postmortem as dpm
    conn = pb.open_for_dashboard()
    report = dpm.open_daily_postmortem_report(conn)

rv = report.get("daily_review")
if not rv:
    ui.unavailable("This report was produced without the daily-review section.", "The daily review")
    st.stop()

st.subheader(f"Review of {rv['review_date_et']} (Eastern)")
a = rv["account"]
m = st.columns(4)
m[0].metric("Available cash", ui.money(a["available_cash"]))
m[1].metric("Open stakes", ui.money(a["open_stakes"]))
m[2].metric("Equity", ui.money(a["equity"]))
m[3].metric("Settled P&L (all time)", ui.signed_money(a["settled_pnl"]))
for origin, label in (("AUTOMATIC", "Model book (automatic tickets)"),):
    o = rv["origins"][origin]
    st.caption(f"{label}: {o['tickets']} total · {o['wins']}W-{o['losses']}L-{o['voids']}V · {o['pending'] + o['unresolved']} open · settled P&L {ui.signed_money(o['settled_pnl'])}")

pr = rv.get("predictions")
if pr:
    st.subheader("Frozen predictions against official results")
    st.caption("Logged before the games and never rewritten. This is predictive accuracy; software correctness, estimated value and realised profit are separate questions.")
    ae = pr.get("anchor_estimates") or {}
    rows = []
    for label, key in (("Supported legs priced that day", "supported_pool"), ("Legs on selected tickets", "on_selected_tickets")):
        r = ae.get(key) or {}
        if r.get("legs"):
            rows.append({"Group": label, "Player-games": r["legs"], "Games": r["games"], "Recent hit rate": ui.pct(r["recent_hit_rate"]), "Model": ui.pct(r["model_calibrated"]), "Final estimate": ui.pct(r["final_estimate"]),
                         "Price implies": ui.pct(r["price_implied_before_margin"]), "Happened": ui.pct(r["happened"])})
    if rows:
        st.dataframe(rows, hide_index=True, width="stretch")
        st.caption(ae.get("reading", ""))
    else:
        st.caption(f"{pr['snapshots_logged']} snapshot(s) logged; {pr.get('resolved', 0)} prop leg(s) resolved, {pr['awaiting_games']} awaiting games, {pr['did_not_play']} player(s) did not play. "
                   "Nothing to compare for the anchor estimates yet (they are frozen from 2026-10-12 12:04 UTC on; earlier snapshots carry the model's number only).")
    ml = pr.get("moneyline_legs") or {}
    if ml.get("legs"):
        st.caption(f"Moneylines graded as team outcomes: {ml['legs']} leg(s) in {ml.get('games', '?')} game(s); the price implied {ui.pct(ml.get('price_implied_before_margin'))} before margin, "
                   f"{ui.pct(ml.get('happened'))} won.")
    for g in pr.get("logging_gaps") or []:
        st.warning(f"Logging gap {ui.et_time(g['from_utc'], True)} → {ui.et_time(g['to_utc'], True)}: {g['reason']}")
    if pr.get("selected_tickets"):
        st.dataframe([{"Ticket": " + ".join(t["legs"]), "Estimated hit chance": ui.pct(t["estimated_hit_probability"]), "Recorded": "yes" if t["recorded"] else "proposed only", "Result": t["result"].replace("_", " ").title()}
                      for t in pr["selected_tickets"]], hide_index=True, width="stretch")
    ex = pr.get("exposure") or {}
    shared = ex.get("players_on_several_tickets") or []
    st.markdown("**Shared exposure:** " + (", ".join(f"{x['player']} on {len(x['tickets'])} tickets" for x in shared) if shared else f"no player on more than one of the {ex.get('tickets_recorded_that_day', 0)} ticket(s) recorded that day."))
    for line in pr.get("participation_and_data") or []:
        st.caption(ui.esc(line))
    st.markdown("**Corrective actions:** " + " ".join(pr.get("corrective_actions") or ["None."]))

tickets = rv["tickets"]
st.subheader("Tickets settled that day")
if not tickets:
    st.info(f"No ticket settled on {rv['review_date_et']}. Open tickets: {len(rv['open_tickets'])}.")
for origin in ("AUTOMATIC",):
    mine = [t for t in tickets if t["origin"] == origin]
    if not mine:
        continue
    st.markdown(f"#### {ui.STATUS_TEXT[origin]}")
    for t in mine:
        res = {"WIN": "WON", "LOSS": "LOST"}.get(t["status"], t["status"])
        with st.expander(f"{t['ticket_id']} — {res.title()} {ui.signed_money(t['profit_loss'])} · reading: {t['reading'].title()}", expanded=t["status"] == "LOSS"):
            st.dataframe([{"Leg": l["label"], "Recorded price": ui.american(l["recorded_price"]), "Quote updated": ui.et_time(l["quote_updated_utc"], True) if l["quote_updated_utc"] else "not frozen",
                           "Model chance": ui.pct(l["model_probability"]), "Actual": l["actual_value"], "Result": l["outcome"].title(),
                           "Closing price": (ui.american(l["closing"]["american"]) if l["closing"].get("american") is not None else "n/a"),
                           "Vs close": l["price_vs_close"] or "—"} for l in t["legs"]], hide_index=True, width="stretch")
            missing = [l["closing"].get("reason") for l in t["legs"] if l["closing"].get("american") is None]
            if missing:
                st.caption("Closing price unavailable for some legs: " + "; ".join(sorted(set(missing))))
            st.caption(f"Recorded {ui.et_time(t['recorded_at_utc'], True)} · settled {ui.et_time(t['settled_at_utc'], True)} · stake {ui.money(t['stake'])} · estimated combined price {ui.american(t['combined_american'])} · "
                       f"model hit chance {ui.pct(t['model_hit_probability'])}.")
            if t.get("variance_note"):
                st.write(ui.esc(t["variance_note"]))
            for d in t["defects"]:
                (st.error if d["severity"] == "DEFECT" else st.caption)(ui.esc(f"{d['kind']}: {d['detail']}"))
            if not t["defects"]:
                st.caption("No system defect found in this ticket: prices were timestamped and fresh at entry, it was recorded before puck drop, and its outcome agrees with its legs.")

st.subheader("Postmortems for losing tickets")
losses = rv.get("loss_postmortems") or []
if not losses:
    st.caption("No ticket has lost yet.")
for t in losses:
    with st.expander(f"{t['ticket_id']} — lost {ui.signed_money(t['profit_loss'])} on {ui.et_time(t['settled_at_utc'], True)} · reading: {t['reading'].title()}"):
        st.dataframe([{"Leg": l["label"], "Recorded price": ui.american(l["recorded_price"]), "Model chance": ui.pct(l["model_probability"]), "Actual": l["actual_value"],
                       "Result": l["outcome"].title(), "Closing price": ui.american(l["closing"]["american"]) if l["closing"].get("american") is not None else "n/a",
                       "Vs close": l["price_vs_close"] or "—"} for l in t["legs"]], hide_index=True, width="stretch")
        st.write(ui.esc(t.get("variance_note") or ""))
        for d in t["defects"]:
            (st.error if d["severity"] == "DEFECT" else st.caption)(ui.esc(f"{d['kind']}: {d['detail']}"))
        if not [d for d in t["defects"] if d["severity"] == "DEFECT"]:
            st.caption("No system defect found: the ticket was priced from a timestamped, fresh quote, recorded before puck drop, and its outcome agrees with its legs.")

st.subheader("Defects and variance")
if rv["defects"]:
    for d in rv["defects"]:
        st.error(ui.esc(f"{d['ticket_id']} · {d['kind']}: {d['detail']}"))
else:
    st.write("No system defects were found in the tickets settled that day.")
if rv.get("notes"):
    with st.expander(f"{len(rv['notes'])} data note(s) on older tickets"):
        for d in rv["notes"]:
            st.caption(ui.esc(f"{d['ticket_id']} · {d['detail']}"))
for v in rv["variance"]:
    st.caption(ui.esc(f"{v['ticket_id']}: {v['note']}"))

st.subheader("Calibration so far (settled legs)")
rows = []
for origin, markets in rv["calibration"].items():
    for market, b in markets.items():
        rows.append({"Origin": ui.STATUS_TEXT.get(origin, origin), "Market": market, "Legs": b["legs"], "Expected hits": b["expected"], "Actual hits": b["hits"],
                     "Brier": round(b["brier"], 3) if b["brier"] is not None else "—", "z": f"{b['z']:+.1f}" if b["z"] is not None else "—"})
st.dataframe(rows, hide_index=True, width="stretch") if rows else st.caption("No legs have settled yet.")

st.subheader("What the evidence supports")
pol = rv["policy"]
for p in rv["proposals"]:
    (st.warning if p["action"] != "NONE" else st.caption)(ui.esc(f"{ui.STATUS_TEXT.get(p['origin'], p['origin'])} · {p['market']}: {p['evidence']}"))
st.caption(f"A change is only suggested after {pol['min_legs_for_a_conclusion']} settled legs in a market and a gap of {pol['z_bar']:.0f} standard errors. Nothing is retuned automatically.")
