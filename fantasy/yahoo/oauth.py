"""
Yahoo OAuth 2.0 authorization-code flow (server-side app), built against
the VERIFIED flow documented in fantasy/yahoo/contracts.py (fetched live
from developer.yahoo.com/oauth2/guide/flows_authcode/ this sprint).

Credentials: YAHOO_CLIENT_ID / YAHOO_CLIENT_SECRET / YAHOO_REDIRECT_URI,
read from the environment, a local .env file, or Streamlit's st.secrets
-- in that priority order, mirroring the exact pattern already
established for the Odds API key (research/live_sog_pricing/env_config.py)
but reimplemented locally here, never imported from there: fantasy/ must
stay fully isolated from the betting pricing pipeline (see this
package's __init__.py and tests/test_fantasy_betting_isolation.py).

SECURITY (Part 2 of this sprint, verified by
tests/test_fantasy_oauth.py::TestNoTokenLeakage):
  - YAHOO_CLIENT_SECRET is NEVER returned to, stored in, or read from
    Streamlit session_state or any other browser-visible state -- it is
    read fresh from the server-side credential source on every call.
  - No function in this module ever logs, prints, or f-strings an
    access_token, refresh_token, or client_secret into an exception
    message, a log line, or a return value's repr.
  - Token exchange/refresh always sends the client secret via the
    Authorization: Basic header (base64(client_id:client_secret)),
    exactly as Yahoo's docs specify -- never as a URL query parameter,
    which risks it being logged by an intermediary.
"""
from __future__ import annotations

import base64
import os
import secrets as _secrets
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

import requests

from fantasy.yahoo.contracts import VERIFIED_ENDPOINTS

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATH = REPO_ROOT / ".env"

AUTHORIZE_URL = VERIFIED_ENDPOINTS["oauth_authorize"]
TOKEN_URL = VERIFIED_ENDPOINTS["oauth_token"]
REQUEST_TIMEOUT_SECONDS = 15


def _load_dotenv_into_os_environ() -> None:
    if not ENV_PATH.exists():
        return
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if key and key not in os.environ:
                os.environ[key] = value


def _from_streamlit_secrets(name: str) -> str | None:
    try:
        import streamlit as st
        return st.secrets.get(name) or None
    except Exception:
        return None


def _credential(name: str) -> str | None:
    _load_dotenv_into_os_environ()
    return os.environ.get(name) or _from_streamlit_secrets(name)


@dataclass(frozen=True)
class YahooAppCredentials:
    client_id: str
    client_secret: str
    redirect_uri: str


def load_app_credentials() -> YahooAppCredentials | None:
    """Returns None (never raises) if any credential is missing -- a
    missing credential is a normal pre-connection state, handled by
    callers as OWNER_AUTH_REQUIRED, never an exception."""
    client_id = _credential("YAHOO_CLIENT_ID")
    client_secret = _credential("YAHOO_CLIENT_SECRET")
    redirect_uri = _credential("YAHOO_REDIRECT_URI")
    if not (client_id and client_secret and redirect_uri):
        return None
    return YahooAppCredentials(client_id=client_id, client_secret=client_secret, redirect_uri=redirect_uri)


@dataclass(frozen=True)
class TokenResponse:
    access_token: str
    refresh_token: str
    token_type: str
    expires_at_epoch: float  # computed from expires_in at receipt time -- never trust a client clock elsewhere

    def is_expired(self, *, skew_seconds: float = 60.0) -> bool:
        return time.time() >= (self.expires_at_epoch - skew_seconds)

    def __repr__(self) -> str:
        # Never let a token value leak into a log line via an accidental
        # print(response) or exception traceback that reprs this object.
        return "TokenResponse(access_token=<redacted>, refresh_token=<redacted>, ...)"


def new_state_token() -> str:
    """A random, unguessable state value for CSRF protection on the
    authorization redirect -- callers must store this server-side
    (never trust a client-supplied state) and verify it matches on
    callback."""
    return _secrets.token_urlsafe(32)


def build_authorization_url(creds: YahooAppCredentials, state: str) -> str:
    params = {
        "client_id": creds.client_id,
        "redirect_uri": creds.redirect_uri,
        "response_type": "code",
        "state": state,
        "language": "en-us",
    }
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def _basic_auth_header(creds: YahooAppCredentials) -> dict:
    raw = f"{creds.client_id}:{creds.client_secret}".encode("utf-8")
    return {"Authorization": f"Basic {base64.b64encode(raw).decode('ascii')}",
            "Content-Type": "application/x-www-form-urlencoded"}


class YahooOAuthError(Exception):
    """Raised on a real OAuth failure. Message is built from the HTTP
    status and Yahoo's error field ONLY -- never includes the request
    body/headers (which could carry the client secret or a token)."""


def _post_token_request(creds: YahooAppCredentials, body: dict) -> TokenResponse:
    try:
        resp = requests.post(TOKEN_URL, headers=_basic_auth_header(creds), data=body,
                              timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise YahooOAuthError(f"network error contacting Yahoo token endpoint: {exc.__class__.__name__}") from None
    if resp.status_code != 200:
        raise YahooOAuthError(f"Yahoo token endpoint returned HTTP {resp.status_code}")
    try:
        data = resp.json()
    except ValueError:
        raise YahooOAuthError("malformed JSON from Yahoo token endpoint") from None
    received_at = time.time()
    try:
        return TokenResponse(
            access_token=data["access_token"],
            # Reliability fix (Yahoo compliance/hardening pass, 2026-09-24):
            # a refresh_token IS guaranteed present on the initial
            # authorization_code exchange, but Yahoo's own docs say a
            # /get_token call with grant_type=refresh_token "may" issue a
            # new one without guaranteeing it every time. This used to be
            # a hard data["refresh_token"] access, which would raise
            # YahooOAuthError on any refresh call where Yahoo omitted it --
            # a real, previously-uncaught correctness bug (this exact
            # field-omission behavior is why yahoo-fantasy-cockpit's own
            # proven implementation never does a bare dict access here
            # either). An empty string here means "no new refresh token
            # was issued this call" -- the caller must keep using the
            # previous one, never treat "" as a real token.
            refresh_token=data.get("refresh_token") or "",
            token_type=data.get("token_type", "bearer"),
            expires_at_epoch=received_at + float(data.get("expires_in", 3600)),
        )
    except KeyError as exc:
        raise YahooOAuthError(f"Yahoo token response missing expected field: {exc}") from None


def exchange_code_for_token(creds: YahooAppCredentials, authorization_code: str) -> TokenResponse:
    return _post_token_request(creds, {
        "grant_type": "authorization_code",
        "redirect_uri": creds.redirect_uri,
        "code": authorization_code,
    })


def refresh_access_token(creds: YahooAppCredentials, refresh_token: str) -> TokenResponse:
    return _post_token_request(creds, {
        "grant_type": "refresh_token",
        "redirect_uri": creds.redirect_uri,
        "refresh_token": refresh_token,
    })
