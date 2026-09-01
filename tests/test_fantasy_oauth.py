"""
Tests for fantasy/yahoo/oauth.py (Part 136). No real Yahoo account or
credentials are required to run this suite -- every HTTP call is
mocked; see NEVER require the real owner's Yahoo account (Part 135).
"""
from __future__ import annotations

import time
import unittest
from unittest import mock

from fantasy.yahoo import oauth


def _creds():
    return oauth.YahooAppCredentials(client_id="test-client-id", client_secret="test-client-secret",
                                      redirect_uri="https://example.com/callback")


class TestAuthorizationUrl(unittest.TestCase):
    def test_authorization_url_uses_verified_endpoint(self):
        url = oauth.build_authorization_url(_creds(), state="abc123")
        self.assertTrue(url.startswith(oauth.AUTHORIZE_URL))
        self.assertIn("client_id=test-client-id", url)
        self.assertIn("response_type=code", url)
        self.assertIn("state=abc123", url)

    def test_state_tokens_are_random_and_long(self):
        s1, s2 = oauth.new_state_token(), oauth.new_state_token()
        self.assertNotEqual(s1, s2)
        self.assertGreater(len(s1), 20)


class TestTokenExchange(unittest.TestCase):
    def _mock_response(self, status_code=200, json_data=None):
        resp = mock.Mock(status_code=status_code)
        resp.json.return_value = json_data or {}
        return resp

    def test_exchange_code_uses_verified_token_endpoint_and_grant_type(self):
        creds = _creds()
        fake_resp = self._mock_response(json_data={
            "access_token": "AT1", "refresh_token": "RT1", "token_type": "bearer", "expires_in": 3600,
        })
        with mock.patch("fantasy.yahoo.oauth.requests.post", return_value=fake_resp) as mock_post:
            token = oauth.exchange_code_for_token(creds, "auth-code-123")
        called_url = mock_post.call_args.args[0]
        called_body = mock_post.call_args.kwargs["data"]
        self.assertEqual(called_url, oauth.TOKEN_URL)
        self.assertEqual(called_body["grant_type"], "authorization_code")
        self.assertEqual(called_body["code"], "auth-code-123")
        self.assertEqual(token.access_token, "AT1")
        self.assertEqual(token.refresh_token, "RT1")

    def test_refresh_uses_refresh_token_grant_type(self):
        creds = _creds()
        fake_resp = self._mock_response(json_data={
            "access_token": "AT2", "refresh_token": "RT2", "token_type": "bearer", "expires_in": 3600,
        })
        with mock.patch("fantasy.yahoo.oauth.requests.post", return_value=fake_resp) as mock_post:
            token = oauth.refresh_access_token(creds, "old-refresh-token")
        called_body = mock_post.call_args.kwargs["data"]
        self.assertEqual(called_body["grant_type"], "refresh_token")
        self.assertEqual(called_body["refresh_token"], "old-refresh-token")
        self.assertEqual(token.access_token, "AT2")

    def test_client_secret_sent_via_basic_auth_header_not_url(self):
        creds = _creds()
        fake_resp = self._mock_response(json_data={
            "access_token": "AT3", "refresh_token": "RT3", "token_type": "bearer", "expires_in": 3600,
        })
        with mock.patch("fantasy.yahoo.oauth.requests.post", return_value=fake_resp) as mock_post:
            oauth.exchange_code_for_token(creds, "code")
        headers = mock_post.call_args.kwargs["headers"]
        self.assertIn("Authorization", headers)
        self.assertTrue(headers["Authorization"].startswith("Basic "))
        body = mock_post.call_args.kwargs["data"]
        self.assertNotIn("client_secret", body)
        called_url = mock_post.call_args.args[0]
        self.assertNotIn("test-client-secret", called_url)

    def test_expired_token_raises_no_token_logged_in_error_message(self):
        creds = _creds()
        fake_resp = self._mock_response(status_code=401)
        with mock.patch("fantasy.yahoo.oauth.requests.post", return_value=fake_resp):
            with self.assertRaises(oauth.YahooOAuthError) as ctx:
                oauth.refresh_access_token(creds, "expired-refresh-token")
        self.assertNotIn("expired-refresh-token", str(ctx.exception))
        self.assertNotIn(creds.client_secret, str(ctx.exception))

    def test_invalid_json_response_raises_cleanly(self):
        creds = _creds()
        fake_resp = mock.Mock(status_code=200)
        fake_resp.json.side_effect = ValueError("bad json")
        with mock.patch("fantasy.yahoo.oauth.requests.post", return_value=fake_resp):
            with self.assertRaises(oauth.YahooOAuthError):
                oauth.exchange_code_for_token(creds, "code")

    def test_missing_expected_field_raises_cleanly(self):
        creds = _creds()
        fake_resp = self._mock_response(json_data={"token_type": "bearer"})  # missing access_token
        with mock.patch("fantasy.yahoo.oauth.requests.post", return_value=fake_resp):
            with self.assertRaises(oauth.YahooOAuthError):
                oauth.exchange_code_for_token(creds, "code")


class TestTokenExpiry(unittest.TestCase):
    def test_is_expired_true_after_expiry(self):
        token = oauth.TokenResponse(access_token="a", refresh_token="b", token_type="bearer",
                                     expires_at_epoch=time.time() - 10)
        self.assertTrue(token.is_expired())

    def test_is_expired_false_when_fresh(self):
        token = oauth.TokenResponse(access_token="a", refresh_token="b", token_type="bearer",
                                     expires_at_epoch=time.time() + 3600)
        self.assertFalse(token.is_expired())

    def test_repr_never_leaks_token_values(self):
        token = oauth.TokenResponse(access_token="SUPER-SECRET-ACCESS", refresh_token="SUPER-SECRET-REFRESH",
                                     token_type="bearer", expires_at_epoch=time.time() + 3600)
        rendered = repr(token)
        self.assertNotIn("SUPER-SECRET-ACCESS", rendered)
        self.assertNotIn("SUPER-SECRET-REFRESH", rendered)


class TestNoTokenLeakage(unittest.TestCase):
    """Part 2/147: no function in this module logs/prints a token or
    client secret."""

    def test_no_print_statements_in_oauth_module(self):
        import ast
        import inspect
        from fantasy.yahoo import oauth as oauth_module
        tree = ast.parse(inspect.getsource(oauth_module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
                self.fail("oauth.py must never call print() -- a token/secret could leak into logs")

    def test_credentials_never_read_from_streamlit_session_state(self):
        # AST-based (not a text search): the module's own docstring
        # legitimately documents this guarantee using the words
        # "session_state" -- only a real attribute/subscript access
        # would violate it.
        import ast
        import inspect
        from fantasy.yahoo import oauth as oauth_module
        tree = ast.parse(inspect.getsource(oauth_module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "session_state":
                self.fail("oauth.py must never touch st.session_state")


class TestCredentialLoading(unittest.TestCase):
    def test_missing_credentials_returns_none_not_an_exception(self):
        with mock.patch.object(oauth, "_credential", return_value=None):
            self.assertIsNone(oauth.load_app_credentials())

    def test_all_three_credentials_present_returns_object(self):
        with mock.patch.object(oauth, "_credential", side_effect=lambda name: f"fake-{name}"):
            creds = oauth.load_app_credentials()
        self.assertIsNotNone(creds)
        self.assertEqual(creds.client_id, "fake-YAHOO_CLIENT_ID")

    def test_partial_credentials_returns_none(self):
        def _partial(name):
            return "value" if name == "YAHOO_CLIENT_ID" else None
        with mock.patch.object(oauth, "_credential", side_effect=_partial):
            self.assertIsNone(oauth.load_app_credentials())


if __name__ == "__main__":
    unittest.main()
