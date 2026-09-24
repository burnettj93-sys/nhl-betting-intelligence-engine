"""Page 35 — Fantasy Connection Settings (Yahoo Fantasy Hockey sprint,
2026-09-01, Part 118; rebuilt 2026-09-24 for compliance with the signed
Yahoo API Access and Use Agreement — see docs/YAHOO_COMPLIANCE_REBUILD.md).

ADMIN-ONLY by design (Phase 11 of the Production Readiness Audit;
enforced 2026-09-24, P0.7): this page is the entire Yahoo surface area
of the app. auth.require_admin() below stops a USER-role or logged-out
session's script execution before any of this page's real logic runs --
server-side, not merely a hidden nav link.

Private to this session. Never displays token contents -- only
connection status, a sanitized connection-diagnostic summary, and
selected league/team identifiers. Yahoo Fantasy Information itself
(league settings, rosters, standings) is fetched transiently by the
diagnostic below and is never written to disk -- see
fantasy/yahoo/diagnostic.py's own docstring."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import auth
from dashboard import components as comp
from fantasy.storage import fantasy_store
from fantasy.yahoo import oauth as yoauth
from fantasy.yahoo.client import YahooFantasyClient
from fantasy.yahoo.diagnostic import run_connection_diagnostic, sanitized_summary
from fantasy.yahoo.token_store import EncryptedFileTokenStore, get_encryption_key

auth.require_admin()

st.title("Fantasy Connection Settings")
comp.render_model_status_header()

st.caption("Private to this session, ADMIN-only. Never displays token contents. Yahoo Fantasy "
           "Information (league/roster/standings data) is never persisted -- see "
           "docs/YAHOO_COMPLIANCE_REBUILD.md.")

creds = yoauth.load_app_credentials()
token_store = EncryptedFileTokenStore()

st.markdown("### Yahoo App Credentials")
if creds is None:
    st.markdown(comp.label_badge("OWNER_AUTH_REQUIRED", "unavailable"), unsafe_allow_html=True)
    st.caption(
        "YAHOO_CLIENT_ID / YAHOO_CLIENT_SECRET / YAHOO_REDIRECT_URI are not configured. See "
        "YAHOO_FANTASY_INTEGRATION_GUIDE.md's 'Owner Auth Steps' section.")
elif token_store.get() is None and get_encryption_key() is None:
    st.markdown(comp.label_badge("OWNER_AUTH_REQUIRED", "unavailable"), unsafe_allow_html=True)
    st.caption("YAHOO_TOKEN_ENCRYPTION_KEY is not configured -- a connection can't be persisted "
               "without it. Generate one with: `python3 -c \"import secrets,base64; "
               "print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())\"` and set it "
               "alongside the Yahoo credentials above.")
else:
    st.markdown(comp.label_badge("CREDENTIALS CONFIGURED", "input"), unsafe_allow_html=True)
    st.caption(f"redirect_uri: {creds.redirect_uri}")

st.divider()
st.markdown("### Connection Status")

# OAuth callback handler (2026-09-24: this was the missing piece --
# the page could START the authorization redirect but never completed
# it). Yahoo redirects back here with ?code=...&state=...; state is
# checked against the one this session generated before ever exchanging
# the code, to guard against CSRF.
query_params = st.query_params
if "code" in query_params and creds is not None:
    returned_state = query_params.get("state")
    expected_state = st.session_state.get("_fantasy_oauth_state")
    if not expected_state or returned_state != expected_state:
        st.error("OAuth state mismatch -- possible CSRF or a stale/reused authorization link. "
                 "Click CONNECT YAHOO FANTASY again to restart.")
    else:
        try:
            token = yoauth.exchange_code_for_token(creds, query_params["code"])
            token_store.save(token)
            st.session_state.pop("_fantasy_oauth_state", None)
            st.query_params.clear()
            st.success("Yahoo account connected.")
            st.rerun()
        except yoauth.YahooOAuthError as exc:
            st.error(f"Yahoo rejected the authorization code: {exc}")

_token = token_store.get()

if _token is None:
    st.markdown(comp.label_badge("NOT CONNECTED", "unavailable"), unsafe_allow_html=True)
    st.caption("No Yahoo account connected.")
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
    st.caption(f"Token expires: {'expired, will refresh on next use' if _token.is_expired() else 'valid'}")
    if st.button("DISCONNECT YAHOO"):
        token_store.clear()
        st.rerun()

    st.divider()
    st.markdown("### Connection Diagnostic (Phase 9 — YAHOO_CONNECTION_CERTIFIED)")
    st.caption("Fetches your identity, NHL fantasy game, leagues, league settings, and team "
               "LIVE from Yahoo -- transiently, for display only. Nothing below is ever saved.")
    if st.button("Run connection diagnostic"):
        client = YahooFantasyClient(creds, token_store)
        with st.spinner("Contacting Yahoo..."):
            report = run_connection_diagnostic(client)
        summary = sanitized_summary(report)
        if summary["certified"]:
            st.markdown(comp.label_badge("YAHOO_CONNECTION_CERTIFIED", "input"), unsafe_allow_html=True)
        else:
            st.markdown(comp.label_badge("NOT_CERTIFIED", "unavailable"), unsafe_allow_html=True)
        for step in summary["steps"]:
            icon = "✅" if step["ok"] else "❌"
            st.caption(f"{icon} **{step['step']}**: {step['detail']}")
        if summary["certified"]:
            st.markdown(f"**League:** {summary['league_name']} ({summary['selected_league_key']}) — "
                        f"scoring: {summary['scoring_type']}, "
                        f"{summary['roster_position_count']} roster position(s), "
                        f"{summary['stat_category_count']} stat categor(y/ies)")
            st.markdown(f"**Team:** {summary['team_name']} ({summary['team_key']})")

st.divider()
st.markdown("### Fantasy System Health")
health_rows = [
    ("YAHOO_AUTH", "CONNECTED" if _token is not None else "OWNER_AUTH_REQUIRED"),
    ("YAHOO_CONNECTION_DIAGNOSTIC", "Run it above to check" if _token is not None else "REQUIRES_CONNECTION"),
    ("YAHOO_ROSTER", "NOT_IMPLEMENTED_THIS_SPRINT (Phase 10, pending)"),
    ("YAHOO_MATCHUP", "NOT_IMPLEMENTED_THIS_SPRINT (Phase 10, pending)"),
    ("YAHOO_AVAILABLE_PLAYERS", "NOT_IMPLEMENTED_THIS_SPRINT (Phase 10, pending)"),
    ("FANTASY_PROJECTIONS", "DEMO_MODE_ONLY"),
    ("FANTASY_RECOMMENDATIONS", "DEMO_MODE_ONLY"),
]
for label, status in health_rows:
    st.caption(f"**{label}**: {status}")

comp.render_provenance_panel()
