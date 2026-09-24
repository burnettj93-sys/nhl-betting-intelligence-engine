"""
Yahoo compliance rebuild (2026-09-24): tests for fantasy/yahoo/crypto.py
and fantasy/yahoo/token_store.py, which together replace
fantasy_store.py's old plaintext yahoo_tokens table. The signed API
agreement permits persisting OAuth tokens securely (unlike Yahoo Fantasy
Information itself, which must never be persisted) -- "securely" means
encrypted at rest, which is what these tests actually prove, not just
that a round-trip works.
"""
from __future__ import annotations

import base64
import secrets
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fantasy.yahoo import crypto
from fantasy.yahoo.oauth import TokenResponse
from fantasy.yahoo.token_store import EncryptedFileTokenStore


def _test_key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()


class TestCrypto(unittest.TestCase):
    def test_round_trip(self):
        key = _test_key()
        payload = {"access_token": "AT", "refresh_token": "RT"}
        ciphertext = crypto.encrypt_json(key, payload)
        self.assertEqual(crypto.decrypt_json(key, ciphertext), payload)

    def test_ciphertext_never_contains_the_plaintext_token_value(self):
        key = _test_key()
        ciphertext = crypto.encrypt_json(key, {"access_token": "SUPER-SECRET-VALUE-12345"})
        self.assertNotIn("SUPER-SECRET-VALUE-12345", ciphertext)

    def test_wrong_key_returns_none_not_a_crash(self):
        ciphertext = crypto.encrypt_json(_test_key(), {"access_token": "AT"})
        self.assertIsNone(crypto.decrypt_json(_test_key(), ciphertext))

    def test_tampered_ciphertext_returns_none(self):
        key = _test_key()
        ciphertext = crypto.encrypt_json(key, {"access_token": "AT"})
        tampered = ciphertext[:-4] + ("A" * 4 if ciphertext[-4:] != "AAAA" else "BBBB")
        self.assertIsNone(crypto.decrypt_json(key, tampered))

    def test_empty_token_returns_none(self):
        self.assertIsNone(crypto.decrypt_json(_test_key(), ""))

    def test_key_not_32_bytes_raises_clearly(self):
        with self.assertRaises(crypto.TokenCryptoError):
            crypto.encrypt_json("too-short", {"a": 1})


class TestEncryptedFileTokenStore(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self._tmpdir.name) / "yahoo_token.enc"
        self.key = _test_key()

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_get_returns_none_when_no_file_exists(self):
        store = EncryptedFileTokenStore(self.path)
        with mock.patch("fantasy.yahoo.token_store.get_encryption_key", return_value=self.key):
            self.assertIsNone(store.get())

    def test_save_then_get_round_trips_the_token(self):
        store = EncryptedFileTokenStore(self.path)
        token = TokenResponse(access_token="AT1", refresh_token="RT1", token_type="bearer",
                               expires_at_epoch=time.time() + 3600)
        with mock.patch("fantasy.yahoo.token_store.get_encryption_key", return_value=self.key):
            store.save(token)
            loaded = store.get()
        self.assertEqual(loaded.access_token, "AT1")
        self.assertEqual(loaded.refresh_token, "RT1")

    def test_file_on_disk_is_never_plaintext(self):
        store = EncryptedFileTokenStore(self.path)
        token = TokenResponse(access_token="PLAINTEXT-CANARY-VALUE", refresh_token="RT1",
                               token_type="bearer", expires_at_epoch=time.time() + 3600)
        with mock.patch("fantasy.yahoo.token_store.get_encryption_key", return_value=self.key):
            store.save(token)
        raw_bytes = self.path.read_bytes()
        self.assertNotIn(b"PLAINTEXT-CANARY-VALUE", raw_bytes)

    def test_save_without_configured_key_raises_rather_than_writing_plaintext(self):
        store = EncryptedFileTokenStore(self.path)
        token = TokenResponse(access_token="AT", refresh_token="RT", token_type="bearer",
                               expires_at_epoch=time.time() + 3600)
        with mock.patch("fantasy.yahoo.token_store.get_encryption_key", return_value=None):
            with self.assertRaises(crypto.TokenCryptoError):
                store.save(token)
        self.assertFalse(self.path.exists())

    def test_clear_removes_the_file(self):
        store = EncryptedFileTokenStore(self.path)
        token = TokenResponse(access_token="AT", refresh_token="RT", token_type="bearer",
                               expires_at_epoch=time.time() + 3600)
        with mock.patch("fantasy.yahoo.token_store.get_encryption_key", return_value=self.key):
            store.save(token)
        self.assertTrue(self.path.exists())
        store.clear()
        self.assertFalse(self.path.exists())

    def test_clear_on_nonexistent_file_does_not_raise(self):
        EncryptedFileTokenStore(self.path).clear()

    def test_default_path_is_resolved_fresh_not_bound_at_import_time(self):
        """This project's own well-documented footgun: a default bound
        directly into __init__'s signature would make mock.patch on the
        module constant silently do nothing. EncryptedFileTokenStore()
        with no explicit path must actually pick up a patched
        DEFAULT_TOKEN_PATH."""
        import fantasy.yahoo.token_store as ts
        with mock.patch.object(ts, "DEFAULT_TOKEN_PATH", self.path):
            store = ts.EncryptedFileTokenStore()
            token = TokenResponse(access_token="AT", refresh_token="RT", token_type="bearer",
                                   expires_at_epoch=time.time() + 3600)
            with mock.patch.object(ts, "get_encryption_key", return_value=self.key):
                store.save(token)
        self.assertTrue(self.path.exists())


if __name__ == "__main__":
    unittest.main()
