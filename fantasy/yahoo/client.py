"""
Minimal Yahoo Fantasy Sports API resource client -- authenticated GET
requests against the VERIFIED base URL (contracts.py), with automatic
access-token refresh on expiry (Part 4) and honest, typed error states
(Part 115) rather than raw exceptions escaping to callers.

Read-only (Part 128 -- READ-ONLY YAHOO): this client only ever issues
GET requests. No POST/PUT/DELETE against a Yahoo fantasy resource is
implemented anywhere in this module, by construction -- there is no
write path to accidentally call.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import requests

from fantasy.yahoo.contracts import VERIFIED_ENDPOINTS
from fantasy.yahoo.oauth import TokenResponse, YahooAppCredentials, YahooOAuthError, refresh_access_token
from fantasy.yahoo.parser import parse_fantasy_content

BASE_URL = VERIFIED_ENDPOINTS["base_url"]
REQUEST_TIMEOUT_SECONDS = 15


@dataclass
class ApiResult:
    ok: bool
    status: str  # "OK" | "UNAUTHORIZED" | "RATE_LIMITED" | "NOT_FOUND" | "MALFORMED_RESPONSE" | "NETWORK_ERROR"
    data: dict | None
    error: str | None
    raw_xml: str | None = None


class TokenStore:
    """Minimal interface a caller supplies: how to read/write the
    current token for the authenticated user. Kept abstract here so the
    real persistent-storage implementation (fantasy/storage/fantasy_store.py)
    is a separate, swappable concern -- this client never assumes a
    specific storage backend."""

    def get(self) -> TokenResponse | None:
        raise NotImplementedError

    def save(self, token: TokenResponse) -> None:
        raise NotImplementedError


class YahooFantasyClient:
    """One client per authenticated user session. Never shared across
    users (Part 7/127 -- multi-user isolation) -- callers must construct
    a fresh client (or one scoped per-user) rather than a module-level
    singleton."""

    def __init__(self, creds: YahooAppCredentials, token_store: TokenStore,
                 session: requests.Session | None = None):
        self._creds = creds
        self._tokens = token_store
        self._session = session or requests.Session()

    def _ensure_fresh_token(self) -> TokenResponse | None:
        token = self._tokens.get()
        if token is None:
            return None
        if token.is_expired():
            try:
                token = refresh_access_token(self._creds, token.refresh_token)
            except YahooOAuthError:
                return None
            self._tokens.save(token)
        return token

    def get_resource(self, path: str) -> ApiResult:
        """`path` is the resource path AFTER the base URL, e.g.
        "/league/461.l.1000/settings" -- see contracts.py for verified
        path shapes."""
        token = self._ensure_fresh_token()
        if token is None:
            return ApiResult(ok=False, status="UNAUTHORIZED", data=None,
                              error="no valid access token (reconnect required)")
        url = f"{BASE_URL}{path}"
        params = {"format": "xml"}
        try:
            resp = self._session.get(url, headers={"Authorization": f"Bearer {token.access_token}"},
                                      params=params, timeout=REQUEST_TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            return ApiResult(ok=False, status="NETWORK_ERROR", data=None,
                              error=f"network error: {exc.__class__.__name__}")

        if resp.status_code == 401:
            return ApiResult(ok=False, status="UNAUTHORIZED", data=None,
                              error="Yahoo rejected the access token (401) -- reconnect required")
        if resp.status_code == 999 or resp.status_code == 429:
            return ApiResult(ok=False, status="RATE_LIMITED", data=None,
                              error=f"Yahoo rate limit (HTTP {resp.status_code})")
        if resp.status_code == 404:
            return ApiResult(ok=False, status="NOT_FOUND", data=None,
                              error="resource not found (league/team not found, or not accessible to this user)")
        if resp.status_code != 200:
            return ApiResult(ok=False, status="NETWORK_ERROR", data=None,
                              error=f"unexpected HTTP {resp.status_code} from Yahoo")

        try:
            parsed = parse_fantasy_content(resp.text)
        except ValueError as exc:
            return ApiResult(ok=False, status="MALFORMED_RESPONSE", data=None, error=str(exc), raw_xml=resp.text)
        return ApiResult(ok=True, status="OK", data=parsed, error=None)
