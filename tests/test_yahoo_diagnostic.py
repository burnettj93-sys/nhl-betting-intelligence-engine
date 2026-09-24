"""
Tests for fantasy/yahoo/diagnostic.py (Phase 9 "First Test" /
YAHOO_CONNECTION_CERTIFIED). A fake YahooFantasyClient double is used
throughout -- no real network access, no real Yahoo credentials needed
to prove the diagnostic's own logic is correct.
"""
from __future__ import annotations

import unittest
from unittest import mock

from fantasy.yahoo.client import ApiResult
from fantasy.yahoo.diagnostic import run_connection_diagnostic, sanitized_summary


def _ok(data: dict) -> ApiResult:
    return ApiResult(ok=True, status="OK", data=data, error=None)


def _fail(status: str, error: str) -> ApiResult:
    return ApiResult(ok=False, status=status, data=None, error=error)


class _FakeClient:
    """Maps a resource path substring to a canned ApiResult, in the
    order the diagnostic is expected to call them."""

    def __init__(self, responses: dict[str, ApiResult]):
        self._responses = responses

    def get_resource(self, path: str) -> ApiResult:
        for key, result in self._responses.items():
            if key in path:
                return result
        raise AssertionError(f"no fake response registered for path containing any of "
                              f"{list(self._responses)}: {path}")


IDENTITY_OK = {"users": {"user": {"guid": "ABC123GUID"}}}
GAME_OK = {"users": {"user": {"games": {"game": {"game_key": "465", "code": "nhl"}}}}}
LEAGUES_OK = {"users": {"user": {"games": {"game": {
    "game_key": "465",
    "leagues": {"league": [{"league_key": "465.l.1000"}, {"league_key": "465.l.2000"}]},
}}}}}
SETTINGS_XML_PARSED = {
    "league": {"settings": {"name": "My Real League", "scoring_type": "head",
                             "roster_positions": {"roster_position": [{"position": "C"}, {"position": "G"}]},
                             "stat_categories": {"stats": {"stat": [{"stat_id": "1"}, {"stat_id": "2"}]}}}}}
TEAMS_OK = {"users": {"user": {"games": {"game": {
    "teams": {"team": [{"team_key": "465.l.1000.t.7", "name": "My Real Team"}]},
}}}}}


class TestFullSuccessPath(unittest.TestCase):
    def setUp(self):
        self.client = _FakeClient({
            "users;use_login=1/games;game_keys=nhl/leagues": _ok(LEAGUES_OK),
            "users;use_login=1/games;game_keys=nhl/teams": _ok(TEAMS_OK),
            "users;use_login=1/games": _ok(GAME_OK),
            "users;use_login=1": _ok(IDENTITY_OK),
            "/settings": _ok(SETTINGS_XML_PARSED),
        })

    def test_certified_true_when_every_step_succeeds(self):
        with mock.patch("fantasy.yahoo.diagnostic.extract_league_settings",
                         return_value={"name": "My Real League", "scoring_type": "head",
                                       "roster_positions": [1, 2], "stat_categories": [1, 2, 3]}):
            report = run_connection_diagnostic(self.client)
        self.assertTrue(report.certified)
        self.assertEqual(report.identity_guid, "ABC123GUID")
        self.assertEqual(report.game_key, "465")
        self.assertEqual(report.league_keys, ["465.l.1000", "465.l.2000"])
        self.assertEqual(report.selected_league_key, "465.l.1000")
        self.assertEqual(report.team_key, "465.l.1000.t.7")
        self.assertEqual(report.team_name, "My Real Team")

    def test_sanitized_summary_never_includes_raw_client_secret_or_token_fields(self):
        with mock.patch("fantasy.yahoo.diagnostic.extract_league_settings",
                         return_value={"name": "My Real League", "scoring_type": "head",
                                       "roster_positions": [1, 2], "stat_categories": [1, 2, 3]}):
            report = run_connection_diagnostic(self.client)
        summary = sanitized_summary(report)
        dumped = str(summary)
        for forbidden in ("access_token", "refresh_token", "client_secret"):
            self.assertNotIn(forbidden, dumped)


class TestFailurePaths(unittest.TestCase):
    def test_identity_failure_stops_immediately_and_is_not_certified(self):
        client = _FakeClient({"users;use_login=1": _fail("UNAUTHORIZED", "no valid access token")})
        report = run_connection_diagnostic(client)
        self.assertFalse(report.certified)
        self.assertEqual(len(report.steps), 1)
        self.assertFalse(report.steps[0].ok)

    def test_no_leagues_found_stops_before_settings_or_team_calls(self):
        client = _FakeClient({
            "users;use_login=1/games;game_keys=nhl/leagues": _ok(
                {"users": {"user": {"games": {"game": {"game_key": "465", "leagues": {}}}}}}),
            "users;use_login=1/games": _ok(GAME_OK),
            "users;use_login=1": _ok(IDENTITY_OK),
        })
        report = run_connection_diagnostic(client)
        self.assertFalse(report.certified)
        self.assertEqual(report.league_keys, [])
        step_names = [s.step for s in report.steps]
        self.assertNotIn("league_settings", step_names)
        self.assertNotIn("my_team", step_names)

    def test_no_nhl_game_found_is_reported_honestly(self):
        client = _FakeClient({
            "users;use_login=1/games": _ok({"users": {"user": {"games": {}}}}),
            "users;use_login=1": _ok(IDENTITY_OK),
        })
        report = run_connection_diagnostic(client)
        self.assertFalse(report.certified)
        self.assertIsNone(report.game_key)


if __name__ == "__main__":
    unittest.main()
