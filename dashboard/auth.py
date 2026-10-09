"""
P0.7 (2026-09-24 hardening block): Streamlit-session integration for
operational/auth_store.py's user store. This is the ONLY place a page
should check who's logged in -- every page that needs to restrict
access calls require_login() or require_admin() at the very top of its
script, BEFORE any real content (Yahoo credentials, real recommendation
data, etc.) is computed or rendered.

Server-side enforcement, not UI hiding: require_admin() calls st.stop()
immediately for a non-admin session -- the rest of the page's Python
code, including any Yahoo API call, simply never executes. Hiding a
page from the sidebar nav (dashboard/app.py does this too, for
belt-and-suspenders) is a UX nicety, not the actual security boundary.
"""
from __future__ import annotations

import hmac
import os

import streamlit as st

from operational import auth_store
from operational import runtime_mode

_SESSION_KEY_USERNAME = "_auth_username"
_SESSION_KEY_ROLE = "_auth_role"


# COMMUNITY_CLOUD_MODE has NO application-level accounts, and (from 2026-10-09) the hosted app is PUBLIC: anyone with the link may open it, signed out. Every visitor is
# therefore an anonymous read-only VISITOR. What a visitor can do is exactly what the pages offer: read the published snapshot, and (once the app's write credential exists)
# create their own personal account and add paper bets to it, each order signed with that account's own passcode (operational/log_signing.py), so one visitor cannot write
# to another's account. Nothing a visitor does can reach the model's $500 book, which no hosted page writes. Administrative pages (Diagnostics) need the OWNER unlock: a
# passphrase held only in the Streamlit secret OWNER_ACCESS_PASSPHRASE. Without that secret nobody can unlock them on the hosted app. LOCAL and PRODUCTION keep the full account
# system unchanged.
CLOUD_VIEWER = {"username": "Visitor", "role": "USER"}
CLOUD_OWNER = {"username": "Owner (unlocked)", "role": "ADMIN"}
OWNER_SECRET = "OWNER_ACCESS_PASSPHRASE"
_SESSION_KEY_OWNER = "_owner_unlocked"


def current_user() -> dict | None:
    """Returns {"username": ..., "role": ...} if logged in this
    session, else None. Never raises. In COMMUNITY_CLOUD_MODE every session is the anonymous visitor (role USER) unless the owner unlocked it this session."""
    if runtime_mode.is_community_cloud():
        return dict(CLOUD_OWNER) if st.session_state.get(_SESSION_KEY_OWNER) else dict(CLOUD_VIEWER)
    username = st.session_state.get(_SESSION_KEY_USERNAME)
    role = st.session_state.get(_SESSION_KEY_ROLE)
    if username is None or role is None:
        return None
    return {"username": username, "role": role}


def owner_unlock_available() -> bool:
    """True when the hosted app has an owner passphrase configured (otherwise there is nothing to unlock and no control is shown)."""
    return bool(_setting(OWNER_SECRET))


def try_owner_unlock(passphrase: str) -> bool:
    """Unlocks the administrative pages for THIS browser session only. Constant-time comparison; a wrong attempt waits a second."""
    import time
    configured = _setting(OWNER_SECRET)
    if configured and hmac.compare_digest(str(passphrase or "").encode(), configured.encode()):
        st.session_state[_SESSION_KEY_OWNER] = True
        return True
    time.sleep(1.0)
    return False


def owner_lock() -> None:
    st.session_state.pop(_SESSION_KEY_OWNER, None)


def _set_session(username: str, role: str) -> None:
    st.session_state[_SESSION_KEY_USERNAME] = username
    st.session_state[_SESSION_KEY_ROLE] = role


def logout() -> None:
    st.session_state.pop(_SESSION_KEY_USERNAME, None)
    st.session_state.pop(_SESSION_KEY_ROLE, None)


def _setting(name: str) -> str | None:
    value = (os.environ.get(name) or "").strip()
    if value:
        return value
    try:
        return (st.secrets.get(name) or "").strip() or None
    except Exception:
        return None


def render_bootstrap_admin_form() -> bool:
    """Shown instead of the login form when zero users exist yet (a
    fresh install) -- creates the first account, always as ADMIN (the
    owner). Every subsequent account is a deliberate ADMIN action via
    operational/auth_store.create_user(), never created through this
    page again once at least one user exists."""
    st.title("First-time setup: create the administrator account")
    st.caption("This form only appears once, before any account exists.")
    with st.form("bootstrap_admin_form"):
        username = st.text_input("Admin username")
        password = st.text_input("Password", type="password")
        confirm = st.text_input("Confirm password", type="password")
        submitted = st.form_submit_button("Create administrator account")
    if not submitted:
        return False
    if not username or not password:
        st.error("Username and password are required.")
        return False
    if password != confirm:
        st.error("Passwords do not match.")
        return False
    conn = auth_store.get_connection()
    try:
        auth_store.create_user(conn, username, password, "ADMIN")
    except auth_store.AuthError as exc:
        st.error(str(exc))
        return False
    finally:
        conn.close()
    return True


def render_login_form() -> bool:
    """Renders a username/password form. Returns True the moment a
    login succeeds this run (caller should st.rerun() so the rest of
    the app renders with the new session state); False otherwise (form
    not yet submitted, or a failed attempt was just shown)."""
    st.title("Sign in")
    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in")
    if not submitted:
        return False
    conn = auth_store.get_connection()
    role = auth_store.verify_login(conn, username, password)
    conn.close()
    if role is None:
        st.error("Invalid username or password.")
        return False
    _set_session(username, role)
    return True


def _hide_navigation_until_signed_in() -> None:
    """Before sign-in Streamlit's sidebar still lists every file in pages/ (st.navigation has not run yet
    because the gate stops first), which would show anonymous visitors the internal page names. Only the
    login / bootstrap form should be visible."""
    st.markdown("<style>[data-testid='stSidebar'], [data-testid='stSidebarCollapsedControl'], "
                "[data-testid='stSidebarNav'] {display: none !important;}</style>", unsafe_allow_html=True)


def render_auth_gate() -> dict | None:
    """The single call dashboard/app.py makes before building any real
    navigation/content. Returns the logged-in user dict once
    authenticated this run; returns None (having already rendered a
    bootstrap-admin or login form) otherwise -- the caller must stop
    rendering the rest of the app for that run."""
    user = current_user()
    if user is not None:
        return user                       # Community Cloud: always the implicit viewer -- no login, no bootstrap form

    _hide_navigation_until_signed_in()

    conn = auth_store.get_connection()
    no_users_yet = len(auth_store.list_users(conn)) == 0
    conn.close()

    if no_users_yet:
        if render_bootstrap_admin_form():
            st.success("Administrator account created. Signing you in...")
            st.rerun()
        return None

    if render_login_form():
        st.rerun()
    return None


def require_login() -> dict:
    """Call at the top of any page that requires SOME authenticated
    user (any role). Stops page execution immediately (st.stop()) if
    not logged in -- the caller's own subsequent code, including
    anything that would fetch or display real data, never runs."""
    user = current_user()
    if user is None:
        st.warning("Please sign in to view this page.")
        st.stop()
    return user


def require_admin() -> dict:
    """Call at the top of any ADMIN-only page (Yahoo pages, first and
    foremost). Stops immediately for a logged-out session OR a
    logged-in USER-role session -- attempting to load this page as a
    non-admin never executes any of the page's real logic, matching the
    explicit instruction that this must fail at the route/function
    level, not just via a hidden nav link."""
    user = require_login()
    if user["role"] != "ADMIN":
        st.error("This page is restricted to the administrator.")
        st.stop()
    return user
