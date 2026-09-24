"""AES-256-GCM encryption for OAuth token storage.

Yahoo compliance/hardening pass (2026-09-24): adapted from
~/yahoo-fantasy-cockpit's app/security/crypto.py -- that implementation
completed a real, live-verified Yahoo OAuth connection on 2026-09-14, so
its encryption primitive (not just its OAuth-flow logic) is reused here
rather than writing a new one from scratch. The one deliberate
difference: the cockpit encrypts a token payload for a BROWSER COOKIE
and never touches disk at all; this module encrypts the same kind of
payload for a small file on local disk instead (see token_store.py) --
the encryption itself, and the "never plaintext, never logged" discipline,
are identical. This is the ONLY place OAuth tokens are ever persisted,
and only as ciphertext -- see docs/YAHOO_COMPLIANCE_REBUILD.md.
"""
from __future__ import annotations

import base64
import json
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_LEN = 12


class TokenCryptoError(Exception):
    pass


def _key_from_secret(secret: str) -> bytes:
    try:
        key = base64.urlsafe_b64decode(secret + "=" * (-len(secret) % 4))
    except Exception:  # noqa: BLE001
        raise TokenCryptoError("token encryption key is not valid base64") from None
    if len(key) != 32:
        raise TokenCryptoError(
            "token encryption key must decode to exactly 32 bytes "
            "(generate one with: python3 -c \"import secrets,base64; "
            "print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())\")")
    return key


def encrypt_json(secret: str, payload: dict[str, Any]) -> str:
    """Encrypts a JSON-serializable dict into an opaque, urlsafe string."""
    key = _key_from_secret(secret)
    nonce = os.urandom(_NONCE_LEN)
    plaintext = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, associated_data=None)
    return base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")


def decrypt_json(secret: str, token: str) -> dict[str, Any] | None:
    """Returns None (never raises) for a missing, tampered, or
    key-mismatched value -- callers must treat that as "not connected,"
    not as a crash."""
    if not token:
        return None
    try:
        key = _key_from_secret(secret)
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        nonce, ciphertext = raw[:_NONCE_LEN], raw[_NONCE_LEN:]
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, associated_data=None)
        return json.loads(plaintext)
    except Exception:  # noqa: BLE001
        return None
