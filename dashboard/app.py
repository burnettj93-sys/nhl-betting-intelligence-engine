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

from PIL import Image

from dashboard import theme

st.set_page_config(
    page_title="NHL Betting Intelligence",
    page_icon=Image.open(theme.EGGY_ICON),
    layout="wide",
    initial_sidebar_state="auto",
)

# P0.7 (2026-09-24 hardening block): the auth gate runs BEFORE any
# navigation or page content is built. A logged-out session (or a
# fresh install with zero accounts yet) only ever sees a login/
# bootstrap form -- st.stop() below means none of the real navigation,
# page registration, or per-page content below this point executes.
theme.inject()
st.logo(str(theme.EGGY_ICON), size="large")        # Eggy at the top of the sidebar, above the navigation
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
_landing: dict = {}


def _page(spec):
    if spec.default:
        # Streamlit gives the default page the bare root URL, so "/Today" would answer "page not found". The landing page is therefore an ordinary
        # page at /Today, and a hidden default page at "/" forwards to it: both addresses work and the navigation shows one Today.
        _landing["page"] = st.Page(str(PAGES_DIR / spec.file), title=spec.title, icon=spec.icon, url_path="Today")
        return _landing["page"]
    return st.Page(str(PAGES_DIR / spec.file), title=spec.title, icon=spec.icon)


def _go_landing() -> None:
    st.switch_page(_landing["page"])


_nav_sections = {section: [_page(spec) for spec in specs] for section, specs in page_registry.pages_for(_user["role"], _MODE).items()}
_first = next(iter(_nav_sections))
_nav_sections[_first] = _nav_sections[_first] + [st.Page(_go_landing, title="Home", url_path="home", default=True, visibility="hidden")]

pg = st.navigation(_nav_sections)

with st.sidebar:
    theme.brand("NHL Intelligence", "Games, players, goalies and paper tickets")
    if _MODE == runtime_mode.COMMUNITY_CLOUD_MODE:
        st.caption("☁️ Hosted view — data is published by the engine every ~15 minutes")
    st.divider()
    if _MODE == runtime_mode.COMMUNITY_CLOUD_MODE:
        st.caption("🔒 Access is controlled by Streamlit private sharing")
    else:
        st.caption(f"Signed in as **{_user['username']}** ({_user['role']})")
        if st.button("Log out"):
            auth.logout()
            st.rerun()

pg.run()
