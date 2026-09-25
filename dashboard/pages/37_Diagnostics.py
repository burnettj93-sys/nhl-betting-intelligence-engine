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
