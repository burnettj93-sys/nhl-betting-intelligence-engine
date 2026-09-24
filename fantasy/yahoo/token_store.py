"""Encrypted-at-rest OAuth token storage.

Yahoo compliance/hardening pass (2026-09-24): REPLACES
fantasy/storage/fantasy_store.py's `yahoo_tokens` table, which stored
access_token/refresh_token as plain TEXT columns in a SQLite database --
not encrypted, and (worse) that table's write path was never actually
reached by any real flow (see docs/YAHOO_COMPLIANCE_REBUILD.md's audit
section: the old dashboard page built an authorization URL but never
implemented the callback that exchanges a code for a token).

The signed Yahoo API Access and Use Agreement (2026-09-21) explicitly
distinguishes the two: OAuth tokens/credentials MAY be persisted
securely; Yahoo Fantasy Information (league/team/roster/scoring data)
must NEVER be persisted, cached, or indexed (Section 2.c.vii). This
module stores ONLY the four token fields (access_token, refresh_token,
token_type, expires_at_epoch) -- never a league/team/roster payload --
encrypted with AES-256-GCM (fantasy/yahoo/crypto.py, itself adapted from
~/yahoo-fantasy-cockpit's own proven, live-verified implementation).

Implements the `TokenStore` interface fantasy/yahoo/client.py already
defines (get()/save()) -- YahooFantasyClient was already written against
that abstract interface and needs no changes to use this.
"""
from __future__ import annotations

import os
from pathlib import Path

from fantasy.yahoo.crypto import TokenCryptoError, decrypt_json, encrypt_json
from fantasy.yahoo.oauth import TokenResponse

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATH = REPO_ROOT / ".env"
DEFAULT_TOKEN_PATH = REPO_ROOT / "fantasy" / "storage" / "yahoo_token.enc"


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
    except Exception:  # noqa: BLE001
        return None


def get_encryption_key() -> str | None:
    """Returns None (never raises) if unconfigured -- callers must treat
    that as OWNER_AUTH_REQUIRED / cannot persist a connection, exactly
    like a missing YAHOO_CLIENT_ID today. Generate one with:
    python3 -c "import secrets,base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())" """
    _load_dotenv_into_os_environ()
    return os.environ.get("YAHOO_TOKEN_ENCRYPTION_KEY") or _from_streamlit_secrets("YAHOO_TOKEN_ENCRYPTION_KEY")


class EncryptedFileTokenStore:
    """One encrypted file holding exactly one token set -- correct for
    this app's single-owner (ADMIN-only) Yahoo connection; this is
    deliberately NOT a multi-user table. `path` is injectable for tests;
    defaults to None (not DEFAULT_TOKEN_PATH directly) and resolved
    inside __init__, matching this project's own well-documented
    default-binding footgun fix elsewhere (mock.patch on
    DEFAULT_TOKEN_PATH would otherwise silently not reach an
    already-bound default)."""

    def __init__(self, path: Path | None = None):
        self._path = path if path is not None else DEFAULT_TOKEN_PATH

    def get(self) -> TokenResponse | None:
        key = get_encryption_key()
        if key is None or not self._path.exists():
            return None
        try:
            ciphertext = self._path.read_text()
        except OSError:
            return None
        payload = decrypt_json(key, ciphertext)
        if payload is None:
            return None
        try:
            return TokenResponse(
                access_token=payload["access_token"],
                refresh_token=payload["refresh_token"],
                token_type=payload.get("token_type", "bearer"),
                expires_at_epoch=float(payload["expires_at_epoch"]),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def save(self, token: TokenResponse) -> None:
        key = get_encryption_key()
        if key is None:
            raise TokenCryptoError(
                "YAHOO_TOKEN_ENCRYPTION_KEY is not configured -- cannot persist a Yahoo connection "
                "without it. See this module's get_encryption_key() docstring to generate one.")
        payload = {
            "access_token": token.access_token, "refresh_token": token.refresh_token,
            "token_type": token.token_type, "expires_at_epoch": token.expires_at_epoch,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(encrypt_json(key, payload))

    def clear(self) -> None:
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass
