"""Tests for fantasy/yahoo/client.py -- error states (Part 115) and
token refresh (Part 4), all against mocked HTTP, never a real Yahoo call."""
from __future__ import annotations

import time
import unittest
from unittest import mock

from fantasy.yahoo.client import ApiResult, TokenStore, YahooFantasyClient
from fantasy.yahoo.oauth import TokenResponse, YahooAppCredentials, YahooOAuthError


class _FakeTokenStore(TokenStore):
    def __init__(self, token: TokenResponse | None):
        self._token = token
        self.saved = []

    def get(self):
        return self._token

    def save(self, token):
        self._token = token
        self.saved.append(token)


def _fresh_token():
    return TokenResponse(access_token="AT", refresh_token="RT", token_type="bearer",
                          expires_at_epoch=time.time() + 3600)


def _expired_token():
    return TokenResponse(access_token="OLD", refresh_token="RT_OLD", token_type="bearer",
                          expires_at_epoch=time.time() - 100)


def _creds():
    return YahooAppCredentials(client_id="id", client_secret="secret", redirect_uri="https://example.com/cb")


class TestNoValidToken(unittest.TestCase):
    def test_returns_unauthorized_when_no_token_stored(self):
        client = YahooFantasyClient(_creds(), _FakeTokenStore(None))
        result = client.get_resource("/league/nhl.l.1/settings")
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "UNAUTHORIZED")


class TestTokenRefreshOnExpiry(unittest.TestCase):
    def test_expired_token_triggers_refresh_before_request(self):
        store = _FakeTokenStore(_expired_token())
        client = YahooFantasyClient(_creds(), store)
        new_token = _fresh_token()
        fake_resp = mock.Mock(status_code=200, text="<fantasy_content><league><league_key>x</league_key>"
                                                      "</league></fantasy_content>")
        with mock.patch("fantasy.yahoo.client.refresh_access_token", return_value=new_token) as mock_refresh:
            with mock.patch.object(client._session, "get", return_value=fake_resp) as mock_get:
                result = client.get_resource("/league/nhl.l.1/settings")
        mock_refresh.assert_called_once()
        self.assertTrue(result.ok)
        self.assertIn("Bearer AT", mock_get.call_args.kwargs["headers"]["Authorization"])
        self.assertEqual(store.saved[-1].access_token, "AT")

    def test_failed_refresh_returns_unauthorized_not_an_exception(self):
        store = _FakeTokenStore(_expired_token())
        client = YahooFantasyClient(_creds(), store)
        with mock.patch("fantasy.yahoo.client.refresh_access_token", side_effect=YahooOAuthError("refresh failed")):
            result = client.get_resource("/league/nhl.l.1/settings")
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "UNAUTHORIZED")


class TestErrorStates(unittest.TestCase):
    def _client(self):
        return YahooFantasyClient(_creds(), _FakeTokenStore(_fresh_token()))

    def test_401_maps_to_unauthorized(self):
        client = self._client()
        fake_resp = mock.Mock(status_code=401, text="")
        with mock.patch.object(client._session, "get", return_value=fake_resp):
            result = client.get_resource("/league/nhl.l.1/settings")
        self.assertEqual(result.status, "UNAUTHORIZED")

    def test_999_maps_to_rate_limited(self):
        client = self._client()
        fake_resp = mock.Mock(status_code=999, text="")
        with mock.patch.object(client._session, "get", return_value=fake_resp):
            result = client.get_resource("/league/nhl.l.1/settings")
        self.assertEqual(result.status, "RATE_LIMITED")

    def test_404_maps_to_not_found(self):
        client = self._client()
        fake_resp = mock.Mock(status_code=404, text="")
        with mock.patch.object(client._session, "get", return_value=fake_resp):
            result = client.get_resource("/league/nhl.l.999/settings")
        self.assertEqual(result.status, "NOT_FOUND")

    def test_malformed_xml_maps_to_malformed_response_not_a_crash(self):
        client = self._client()
        fake_resp = mock.Mock(status_code=200, text="<not><valid<xml")
        with mock.patch.object(client._session, "get", return_value=fake_resp):
            result = client.get_resource("/league/nhl.l.1/settings")
        self.assertEqual(result.status, "MALFORMED_RESPONSE")
        self.assertFalse(result.ok)

    def test_network_error_maps_to_network_error_not_an_uncaught_exception(self):
        import requests
        client = self._client()
        with mock.patch.object(client._session, "get", side_effect=requests.ConnectionError("boom")):
            result = client.get_resource("/league/nhl.l.1/settings")
        self.assertEqual(result.status, "NETWORK_ERROR")

    def test_successful_response_parses_and_returns_ok(self):
        client = self._client()
        fake_resp = mock.Mock(status_code=200,
                               text="<fantasy_content><league><league_key>nhl.l.1</league_key></league>"
                                    "</fantasy_content>")
        with mock.patch.object(client._session, "get", return_value=fake_resp):
            result = client.get_resource("/league/nhl.l.1/settings")
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "OK")
        self.assertEqual(result.data["league"]["league_key"], "nhl.l.1")


class TestReadOnlyByConstruction(unittest.TestCase):
    """Part 128/80: this client has no write method at all -- verified
    structurally, not just by convention."""

    def test_client_has_no_post_put_delete_methods(self):
        for forbidden in ("post_resource", "put_resource", "delete_resource", "add_player", "drop_player",
                           "propose_trade", "accept_trade", "set_lineup"):
            self.assertFalse(hasattr(YahooFantasyClient, forbidden),
                              f"YahooFantasyClient must never define {forbidden}")


if __name__ == "__main__":
    unittest.main()
