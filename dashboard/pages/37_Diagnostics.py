"""Page 37 -- Diagnostics (Community Cloud memory sprint, 2026-09-25, Part 23).

ADMIN-ONLY, every runtime mode: auth.require_admin() below stops a USER-role
or logged-out session before any of this runs (server-side, not just a hidden
nav link). Shows only process/runtime facts -- never environment variables,
secrets, tokens or filesystem paths."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import auth
from dashboard import components as comp
from dashboard import diagnostics_view as dv

auth.require_admin()

st.title("Diagnostics")
comp.render_model_status_header()
st.caption("Process facts only -- no environment variables, secrets or paths are shown.")

st.markdown("### Versions and the unattended engine")
from dashboard import ui as _uiv
from operational import watchdog as _wd
_ver = _uiv.app_version()
try:
    from dashboard import cloud_snapshot as _cs0
    _sm = _cs0.snapshot_meta()
    _hs = _cs0.health_section()
except Exception:  # noqa: BLE001 - never fail the page
    _sm, _hs = {}, {}
_vc = st.columns(3)
_vc[0].metric("This hosted app is running commit", (_ver["commit"] or "unknown")[:10])
_vc[1].metric("Published data was built from commit", ((_sm or {}).get("source_master_commit") or "unknown")[:10])
_wv = _wd.view((_hs or {}).get("watchdog"))
_vc[2].metric("Operational health", _wv["status"], f"checked {(_wv['age_min'] or 0):.0f} min ago" if _wv["age_min"] is not None else None, delta_color="off")
if _wv["message"]:
    st.warning(_wv["message"])
st.markdown("**Operational health** — is the machinery running the right code on the right data, and do the books agree?")
if _wv["checks"]:
    st.dataframe([{"Check": c["name"].replace("_", " "), "Status": c["status"], "Evidence": c["detail"]} for c in _wv["checks"]], hide_index=True, width="stretch")
_ready = ((_hs or {}).get("watchdog") or {}).get("readiness")
st.markdown("**Product readiness** — which features actually work end to end? (Separate from the table above.)")
if _ready:
    st.dataframe([{"Feature": f["feature"], "Status": f["status"].replace("_", " "), "Why": f["detail"], "Owner action": f.get("owner_action") or "—"} for f in _ready["features"]],
                 hide_index=True, width="stretch")
    c = _ready["counts"]
    st.caption(f"{c['WORKING']} working · {c['LIMITED']} limited · {c['NOT_VERIFIED']} not verified · {c['BLOCKED']} blocked · {c['OWNER_ACTION']} waiting on an owner action. " + _ready["note"])
else:
    st.caption("The readiness list is published with the next watchdog run.")
st.caption("The watchdog runs on the engine's Mac every 30 minutes (launchd) and is re-aged here when the page opens: if it stops, this turns FAIL by itself. "
           "If the two commits differ, a deploy is pending or the hosted app has not been rebooted.")

st.markdown("### Owner daily check")
try:
    from dashboard import snapshot_source
    _doc = snapshot_source.current().data if snapshot_source.remote_enabled() else None
except Exception:  # noqa: BLE001 -- the diagnostics page must never fail because of the snapshot
    _doc = None
st.dataframe(dv.owner_daily_rows(_doc), width="stretch", hide_index=True)
st.caption("Derived from the published snapshot. Outside Community Cloud, run `python3 opening_day_readiness.py`.")

d = dv.process_diagnostics()
c1, c2, c3, c4 = st.columns(4)
c1.metric("Process RSS (MB)", d["rss_mb"] if d["rss_mb"] is not None else "n/a")
c2.metric("Peak RSS (MB)", d["peak_rss_mb"])
c3.metric("Runtime mode", d["runtime_mode"])
c4.metric("Model stack loaded", "YES" if d["model_stack_loaded"] else "no")

st.markdown("**Versions**")
st.caption(f"Python {d['python_version']} · Streamlit {d['streamlit_version']}")

st.markdown("**Snapshot source**")
_snap = d["snapshot"]
s1, s2, s3, s4 = st.columns(4)
s1.metric("Source", _snap.get("snapshot_source"))
s2.metric("Freshness", _snap.get("freshness") or "—")
s3.metric("Remote fetch", _snap.get("remote_fetch_status") or "—")
s4.metric("Schema", _snap.get("schema_version") if _snap.get("schema_version") is not None else "—")
st.caption(f"Data as of: {_snap.get('data_as_of') or '—'} · Snapshot generated: {_snap.get('snapshot_generated_at') or '—'} "
           f"· Data age: {_snap.get('snapshot_data_age_hours') if _snap.get('snapshot_data_age_hours') is not None else '—'} h "
           f"· Last successful fetch: {_snap.get('last_successful_fetch_utc') or '—'} "
           f"· Last attempt: {_snap.get('last_attempt_utc') or '—'}")
if _snap.get("remote_url"):
    st.caption(f"Remote: {_snap['remote_url']} · cache TTL {_snap.get('ttl_seconds')} s")
if _snap.get("last_error"):
    st.caption(f"Last fetch error: {_snap['last_error']}")
st.caption(f"Content hash: {_snap.get('content_hash') or '—'}")

st.markdown("**Loaded in this process**")
st.caption(" · ".join(f"{name}: {'yes' if on else 'no'}" for name, on in d["libraries_loaded"].items())
           + f" · research modules: {d['research_modules_loaded']}")

st.markdown("### Odds API credit position")
try:
    from dashboard import product_source as _ps2
    _cb = (_ps2.model_health() or {}).get("credit_budget")
except Exception:  # noqa: BLE001
    _cb = None
if _cb:
    _c1, _c2, _c3, _c4 = st.columns(4)
    _c1.metric("Credits remaining", _cb["remaining"], f"{_cb['usable']} usable after the {_cb['reserve']} reserve", delta_color="off")
    _c2.metric("Even daily pace", _cb["even_daily_pace"], f"{_cb['days_left_in_cycle']} days left", delta_color="off")
    _c3.metric("Trailing daily burn", _cb["trailing_daily_burn"], f"over {_cb['window_days']} days", delta_color="off")
    _c4.metric("Projected exhaustion", _cb["projected_exhaustion_utc"] or "—", f"short {_cb['month_shortfall_at_current_burn']} for the month", delta_color="off")
    _g = _cb["goals_decision"]
    st.caption(f"Anytime-goal prices would add about {_g['extra_credits_per_day']} credits/day (one per captured game). Decision: "
               f"{'capture' if _g['allow'] else 'do not capture'} — {_g['reason'].replace('_', ' ').lower()}"
               + (f" (month would need {_g['month_need']} against {_g['month_usable']} usable; short {_g['shortfall']})." if not _g["allow"] else "."))
    st.dataframe([{"Call class": k, "Calls (trailing window)": v["calls"], "Credits": v["credits"], "Share": f"{v['share']:.0%}"} for k, v in _cb["spend_by_class"].items()],
                 hide_index=True, width="stretch")
    st.caption("Computed from the provider's own response headers on every archived call (docs/ODDS_CREDIT_AUDIT.md).")
    _pl, _mv = _cb.get("plan"), _cb.get("month_view")
    if _pl:
        st.markdown(f"**Today's credit plan ({_pl['day']}).** Day budget {_pl['D']} credits for {_pl['games_today']} game(s): "
                    f"{_pl['games_priced']} priced (shots + points), {_pl['games_not_priced']} not priced, goals for {_pl['goals_games']}, saves for {_pl['saves_games']} (confirmed starters only). "
                    f"Short {_pl['shortfall_per_day_required_only']} credits today to price every game with the required markets.")
        st.dataframe([{"Class": k.replace("_", " ").title(), "Allowance": v, "Spent today": _cb["spent_today_by_class"].get(k, 0.0)} for k, v in _pl["allowance"].items()],
                     hide_index=True, width="stretch")
    if _mv:
        st.caption(f"Month view at {_mv['typical_games_per_day']} games a day: {_mv['games_priced_per_day']} priced each day; pricing every game with the required markets needs "
                   f"{_mv['need_required_only_per_day']} credits/day against a budget of {_mv['D']} — short about {_mv['shortfall_month_required_only']} credits through the cycle "
                   f"({_mv['shortfall_month_everything']} with saves, goals and a second display refresh).")
else:
    st.caption("Credit position is not in the published snapshot yet.")

st.markdown("### Data status evidence (technical)")
try:
    from dashboard import cloud_snapshot as _cs
    from operational import runtime_mode as _rm
    from operational import source_status as _ss
    _dsec = _cs.data_status_section() if _rm.is_community_cloud() else {"sources": _ss.build()}
except Exception as _exc:  # noqa: BLE001 - diagnostics must never fail the page
    _dsec = {}
    st.caption(f"Data status evidence unavailable: {_exc}")
if _dsec.get("sources"):
    _doc = _dsec["sources"]
    st.caption(f"The Data Status page is derived from the timestamps below (document built {_doc.get('generated_at_utc')}), re-aged when the page is opened. "
               "Ages are never stored; each source carries its own freshness policy.")
    st.dataframe([{"Source": s["label"], "Age basis (UTC)": s.get("age_basis_utc"), "Last attempt (UTC)": s.get("last_attempt_utc"), "Policy (min)": s.get("max_age_min"),
                   "Fixed state": s.get("fixed_state"), "Limiter": s.get("limiter"), "Next refresh (UTC)": s.get("next_refresh_utc")} for s in _doc["sources"]],
                 hide_index=True, width="stretch")
    with st.expander("Per-source detail, the once-a-day readiness cache and job health (raw)"):
        st.json({"sources": _doc["sources"]}, expanded=False)
        st.caption("The readiness cache below is written once a day by the 07:00 ET sync and is NOT the page's evidence: it freezes at that moment (this is why the page "
                   "used to show 07:01 ET badges all evening, and its odds entry once read a retired research file). It is kept for comparison only.")
        st.json({"readiness_cache": _dsec.get("readiness_cache"), "ingestion_health": _dsec.get("ingestion_health")}, expanded=False)

st.markdown("### Order path (one-click add) check")
from dashboard import ui as _ui
_ui.order_path_panel()

st.markdown("### Ticket and option diagnostics (admin)")
try:
    from dashboard import product_source as _ps
    _tk = _ps.tickets()
except Exception as _exc:  # noqa: BLE001 - diagnostics must never fail the page
    _tk = None
    st.caption(f"Ticket board unavailable: {_exc}")
if _tk:
    _diag = _tk.get("diagnostics") or {}
    _fun = _diag.get("funnel") or {}
    st.caption("Why tickets and options are or are not produced. These counts are for the operator and are not shown on product pages.")
    st.json({"legs_considered": _diag.get("legs_considered"), "pool_after_edge_filter": _diag.get("pool_after_edge_filter"),
             "qualifying_tickets": _diag.get("qualifying_tickets"), "funnel": {k: v for k, v in _fun.items() if k not in ("nearest_rejected_legs", "nearest_rejected_pairs")},
             "sources": _diag.get("sources"), "option_diagnostics": (_tk.get("options") or {}).get("diagnostics")}, expanded=False)
    if _fun.get("nearest_rejected_pairs"):
        st.markdown("**Nearest rejected pairs**")
        st.dataframe(_fun["nearest_rejected_pairs"], hide_index=True, width="stretch")
    _persons = (_tk.get("options") or {}).get("persons") or {}
    _none = [{"Person": p["name"], "Priced legs": p["priced_legs"], "Reason no option": p["reason_no_option"]} for p in _persons.values() if not p["option_id"]]
    if _none:
        st.markdown("**People with a fresh price but no qualifying option**")
        st.dataframe(_none, hide_index=True, width="stretch")
