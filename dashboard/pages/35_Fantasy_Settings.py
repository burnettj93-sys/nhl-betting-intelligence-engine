"""Page 35 — Fantasy Connection Settings (Yahoo Fantasy Hockey sprint,
2026-09-01, Part 118). Private per-authenticated-user connection state
-- shows honestly whether Yahoo is connected, never fakes a connection
that doesn't exist (Part 151)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import components as comp
from fantasy.yahoo import oauth as yoauth
from fantasy.storage import fantasy_store

st.title("Fantasy Connection Settings")
comp.render_model_status_header()

st.caption("Private to this session. Never displays token contents (Part 118's own explicit rule) -- "
           "only connection status, selected league/team, and last sync time.")

creds = yoauth.load_app_credentials()

st.markdown("### Yahoo App Credentials")
if creds is None:
    st.markdown(comp.label_badge("OWNER_AUTH_REQUIRED", "unavailable"), unsafe_allow_html=True)
    st.caption(
        "YAHOO_CLIENT_ID / YAHOO_CLIENT_SECRET / YAHOO_REDIRECT_URI are not configured. This is the "
        "expected state until Yahoo approves API access (application submitted, pending review) and "
        "a Yahoo Developer Network app is registered with those credentials -- see "
        "YAHOO_FANTASY_INTEGRATION_GUIDE.md's 'Owner Auth Steps' section for the exact minimum steps.")
else:
    st.markdown(comp.label_badge("CREDENTIALS CONFIGURED", "input"), unsafe_allow_html=True)
    st.caption(f"redirect_uri: {creds.redirect_uri}")

st.divider()
st.markdown("### Connection Status")

# A stable per-browser-session identity string -- NOT a real Yahoo
# identity (there is none without a real OAuth flow completing). Once
# real OAuth exists, user_key becomes the real xoauth_yahoo_guid /
# OpenID sub claim -- never a session-local placeholder, per Part 7's
# multi-user-safe design requirement.
_session_user_key = st.session_state.get("_fantasy_session_user_key", "local-dev-session")

conn = fantasy_store.get_connection()
token_row = fantasy_store.load_token_row(conn, _session_user_key)
selection_row = fantasy_store.load_selection(conn, _session_user_key)

if token_row is None:
    st.markdown(comp.label_badge("NOT CONNECTED", "unavailable"), unsafe_allow_html=True)
    st.caption("No Yahoo account connected in this session.")
    if creds is not None:
        state = yoauth.new_state_token()
        st.session_state["_fantasy_oauth_state"] = state
        auth_url = yoauth.build_authorization_url(creds, state)
        st.link_button("CONNECT YAHOO FANTASY", auth_url)
    else:
        st.button("CONNECT YAHOO FANTASY", disabled=True,
                   help="Requires YAHOO_CLIENT_ID/SECRET/REDIRECT_URI to be configured first.")
else:
    st.markdown(comp.label_badge("CONNECTED", "input"), unsafe_allow_html=True)
    st.caption(f"Token last refreshed: {token_row['updated_at_utc']}")
    if selection_row is not None:
        st.caption(f"Selected league: {selection_row['league_key'] or '—'} · "
                   f"team: {selection_row['team_key'] or '—'}")
    if st.button("DISCONNECT YAHOO"):
        fantasy_store.disconnect(conn, _session_user_key)
        st.rerun()

st.divider()
st.markdown("### Fantasy System Health")
health_rows = [
    ("YAHOO_AUTH", "CONNECTED" if token_row is not None else "OWNER_AUTH_REQUIRED"),
    ("YAHOO_LEAGUE", "SELECTED" if selection_row and selection_row["league_key"] else "NOT_SELECTED"),
    ("YAHOO_ROSTER", "NOT_IMPLEMENTED_THIS_SPRINT"),
    ("YAHOO_MATCHUP", "NOT_IMPLEMENTED_THIS_SPRINT"),
    ("YAHOO_AVAILABLE_PLAYERS", "NOT_IMPLEMENTED_THIS_SPRINT"),
    ("FANTASY_PROJECTIONS", "DEMO_MODE_ONLY"),
    ("FANTASY_RECOMMENDATIONS", "DEMO_MODE_ONLY"),
]
for label, status in health_rows:
    st.caption(f"**{label}**: {status}")

comp.render_provenance_panel()
