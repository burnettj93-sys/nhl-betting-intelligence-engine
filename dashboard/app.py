"""
NHL Betting Intelligence Engine — Model Research + Intelligence Dashboard

Entry point. Run with:

    streamlit run dashboard/app.py

See README.md's "Dashboard" section for setup and page-by-page details.
This app is READ-ONLY with respect to model behavior and every database
it touches — see data_access.py's module docstring for the exact
guarantee and how it's enforced/tested.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import auth

st.set_page_config(
    page_title="NHL Model Research Dashboard",
    page_icon="🏒",
    layout="wide",
    initial_sidebar_state="expanded",
)

# P0.7 (2026-09-24 hardening block): the auth gate runs BEFORE any
# navigation or page content is built. A logged-out session (or a
# fresh install with zero accounts yet) only ever sees a login/
# bootstrap form -- st.stop() below means none of the real navigation,
# page registration, or per-page content below this point executes.
_user = auth.render_auth_gate()
if _user is None:
    st.stop()

PAGES_DIR = Path(__file__).resolve().parent / "pages"

from dashboard import page_registry
from operational import runtime_mode  # noqa: E402  (after the auth gate on purpose)

# Navigation is built from dashboard/page_registry.py -- the single source of
# truth for page order, titles, icons, classification and per-mode
# availability. Streamlit's st.Page(path) does NOT import a page until it is
# selected, so registration itself is cheap; what COMMUNITY_CLOUD_MODE changes
# is WHICH pages are registered at all (heavy research pages are omitted, so
# they can never be executed there). Nothing is deleted: LOCAL_MODE and
# PRODUCTION_MODE register every page exactly as before.
#
# The Fantasy section (and everything Yahoo-related) and the Admin section
# are only ever registered for an ADMIN session -- a USER session has no
# route to them via the sidebar. That is a UX nicety, not the security
# boundary: each of those pages also calls auth.require_admin() at the top of
# its own script (see tests/test_auth.py's route-level tests).
_MODE = runtime_mode.current_mode()
_nav_sections = {
    section: [st.Page(str(PAGES_DIR / spec.file), title=spec.title, icon=spec.icon, default=spec.default)
              for spec in specs]
    for section, specs in page_registry.pages_for(_user["role"], _MODE).items()
}

pg = st.navigation(_nav_sections)

with st.sidebar:
    st.markdown("### 🏒 NHL Intelligence Engine")
    st.caption("Model Research + Intelligence Dashboard")
    st.caption("Read-only research view — v1")
    if _MODE == runtime_mode.COMMUNITY_CLOUD_MODE:
        st.caption("☁️ Community Cloud mode — read-only snapshot view")
    st.divider()
    st.caption(f"Signed in as **{_user['username']}** ({_user['role']})")
    if st.button("Log out"):
        auth.logout()
        st.rerun()

pg.run()
