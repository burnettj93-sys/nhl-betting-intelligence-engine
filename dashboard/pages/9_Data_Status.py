"""Page 9 — Data Status: one coherent status per source — how far its data runs, when it was last fetched, how old it is right now, when it refreshes next, and
why it is not current if it is not. States are derived from the sources' own evidence timestamps at the moment the page is opened, so a badge ages by itself.
Technical detail (the evidence, the once-a-day readiness cache, job health) is on Diagnostics. This page never makes a network call or spends a credit."""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import auth
from dashboard import cloud_snapshot
from dashboard import components as comp
from dashboard import ui
from operational import runtime_mode
from operational import source_status as ss

if runtime_mode.is_community_cloud():
    auth.require_admin()

st.title("Data Status")
# One coherent status on this page: the generic snapshot banner (a different policy, in hours) is not shown here; Diagnostics carries the technical detail.

if runtime_mode.is_community_cloud():
    try:
        doc = cloud_snapshot.data_status_section().get("sources")
    except cloud_snapshot.SnapshotUnavailable as _exc:
        st.warning(f"Data status is not available in the snapshot currently being served ({_exc}).")
        st.stop()
else:
    doc = ss.build()
if not doc:
    st.info("The status evidence has not been published yet. It is rebuilt every time the engine publishes (about every 25 minutes).")
    st.stop()

now = dt.datetime.now(dt.timezone.utc)
preview = ss.parse(st.query_params.get("as_of")) if st.query_params.get("as_of") else None
if preview is not None:
    now = preview
view = ss.evaluate(doc, now)

if preview is not None:
    st.warning(f"PREVIEW: these states are evaluated as of {view['as_of_utc']}, not now, using the evidence the engine last published. Remove `as_of` from the address to return to now.")
if view["snapshot_stale"]:
    st.error(f"STATUS SNAPSHOT IS STALE: the engine last published this status {ss._fmt_age(view['snapshot_age_min'])} ago (it normally publishes about every {int(doc.get('publish_interval_min', 25))} minutes). "
             "The ages below are still calculated from the last evidence, so anything that has aged out shows as stale; refresh times shown may not have happened.")
else:
    st.caption(f"Status published {ss._fmt_age(view['snapshot_age_min'])} ago · evaluated {view['as_of_utc']} · ages are calculated when you open this page.")

TONE = {ss.CURRENT: "good", ss.STALE: "bad", ss.NOT_DUE: "info", ss.DISABLED: "muted", ss.BUDGET_LIMITED: "warn", ss.BLOCKED: "bad", ss.UNAVAILABLE: "bad", ss.ESTIMATE: "muted"}
LABEL = {ss.CURRENT: "Current", ss.STALE: "Stale", ss.NOT_DUE: "Not due", ss.DISABLED: "Disabled", ss.BUDGET_LIMITED: "Budget-limited", ss.BLOCKED: "Blocked",
         ss.UNAVAILABLE: "Unavailable", ss.ESTIMATE: "Estimate"}


def when(stamp: str | None) -> str:
    t = ss.parse(stamp)
    return "—" if t is None else ui.et_time(ss._iso(t), True)


rows = []
for r in view["rows"]:
    nxt = "—"
    if r["next_refresh_utc"]:
        nxt = when(r["next_refresh_utc"]) + (" (overdue)" if r["next_overdue"] else "")
    rows.append({"Source": r["label"], "Status": LABEL[r["state"]], "Data through": r["data_through"] or "—", "Last successful fetch": when(r["last_success_utc"]),
                 "Age now": ss._fmt_age(r["age_min"]) if r["age_min"] is not None else "—", "Policy": f"≤ {ss._fmt_age(r['limit_min'])}" if r["limit_min"] else "—",
                 "Next refresh": nxt, "Why": r["reason"] or ""})
st.dataframe(rows, hide_index=True, width="stretch")
st.markdown(" ".join(ui.chip(f"{LABEL[s]}: {sum(1 for r in view['rows'] if r['state'] == s)}", TONE[s]) for s in TONE if any(r["state"] == s for r in view["rows"])), unsafe_allow_html=True)
st.caption("Current = inside the source's own freshness policy · Stale = past it · Not due = nothing needs it yet · Disabled = a feed that is switched off · "
           "Budget-limited = the credit allowance or reserve does not allow a refresh · Estimate = a model estimate, not a source. "
           "Policies differ by source on purpose: prices are judged in minutes (tighter near puck drop), daily files in hours. Technical detail is on Diagnostics.")
comp.render_provenance_panel()
