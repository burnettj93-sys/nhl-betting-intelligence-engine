"""
Reliability fix (Production Readiness Audit, 2026-09-24 -> hardening
pass): a real dry run of operational.nhl_sync hit a genuine 429 from
/v1/roster/{team}/current partway through a roster batch. There was
previously NO retry/backoff anywhere in ingest/nhl_api.py, so that one
transient rate-limit response failed the ENTIRE daily sync's reported
status even though schedule/boxscore data for every game in the window
had already committed successfully. This file reproduces that exact
failure mode and proves the fix: retries with exponential backoff,
Retry-After support, a maximum retry ceiling, per-team commit
durability (a later team's failure can't roll back an earlier team's
already-synced roster), and an explicit SUCCESS/PARTIAL_SUCCESS/FAILED
distinction instead of suppressing the error. See
docs/RELIABILITY_429_FIX.md for the full writeup.
"""
from __future__ import annotations

import unittest
from unittest import mock

from ingest import nhl_api
from ingest.nhl_api import ingest_current_roster_identities
from tests.helpers import make_test_db


class _FakeResponse:
    def __init__(self, json_data=None, status_code=200, headers=None):
        self._json = json_data
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _ScriptedSession:
    """Returns one response per call, in order, regardless of URL --
    enough to exercise _get_json()'s own retry loop against a single
    endpoint without needing real URL routing."""

    def __init__(self, responses: list[_FakeResponse]):
        self._responses = list(responses)
        self.calls = 0

    def get(self, url, timeout=15):
        self.calls += 1
        if not self._responses:
            raise AssertionError("no more scripted responses -- retried more times than expected")
        return self._responses.pop(0)


# ---------------------------------------------------------------------
# 1. _get_json retry/backoff behavior
# ---------------------------------------------------------------------
class TestGetJsonRetry(unittest.TestCase):
    def test_429_then_200_succeeds_after_one_retry(self):
        session = _ScriptedSession([
            _FakeResponse(status_code=429),
            _FakeResponse(json_data={"ok": True}, status_code=200),
        ])
        with mock.patch("ingest.nhl_api.time.sleep") as mock_sleep, \
             mock.patch("ingest.nhl_api._log_retry_event"):
            result = nhl_api._get_json(session, "https://api-web.nhle.com/v1/roster/EDM/current")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(session.calls, 2)
        mock_sleep.assert_called_once()

    def test_retry_after_header_is_honored_over_exponential_backoff(self):
        session = _ScriptedSession([
            _FakeResponse(status_code=429, headers={"Retry-After": "7"}),
            _FakeResponse(json_data={"ok": True}, status_code=200),
        ])
        with mock.patch("ingest.nhl_api.time.sleep") as mock_sleep, \
             mock.patch("ingest.nhl_api._log_retry_event"):
            nhl_api._get_json(session, "https://api-web.nhle.com/v1/roster/EDM/current")
        mock_sleep.assert_called_once_with(7.0)

    def test_malformed_retry_after_falls_back_to_exponential_backoff(self):
        session = _ScriptedSession([
            _FakeResponse(status_code=429, headers={"Retry-After": "not-a-number"}),
            _FakeResponse(json_data={"ok": True}, status_code=200),
        ])
        with mock.patch("ingest.nhl_api.time.sleep") as mock_sleep, \
             mock.patch("ingest.nhl_api._log_retry_event"):
            nhl_api._get_json(session, "https://api-web.nhle.com/v1/roster/EDM/current")
        # base_delay(1.0) * 2**0 == 1.0 for the first attempt's backoff.
        mock_sleep.assert_called_once_with(1.0)

    def test_exponential_backoff_doubles_each_attempt(self):
        session = _ScriptedSession([
            _FakeResponse(status_code=503), _FakeResponse(status_code=503),
            _FakeResponse(json_data={"ok": True}, status_code=200),
        ])
        with mock.patch("ingest.nhl_api.time.sleep") as mock_sleep, \
             mock.patch("ingest.nhl_api._log_retry_event"):
            nhl_api._get_json(session, "https://x", base_delay=1.0)
        self.assertEqual([c.args[0] for c in mock_sleep.call_args_list], [1.0, 2.0])

    def test_retries_exhausted_raises_and_logs(self):
        session = _ScriptedSession([_FakeResponse(status_code=429) for _ in range(10)])
        with mock.patch("ingest.nhl_api.time.sleep"), \
             mock.patch("ingest.nhl_api._log_retry_event") as mock_log:
            with self.assertRaises(RuntimeError):
                nhl_api._get_json(session, "https://x", max_retries=2)
        # 2 retries -> 3 total attempts -> calls == 3
        self.assertEqual(session.calls, 3)
        outcomes = [c.kwargs["outcome"] for c in mock_log.call_args_list]
        self.assertEqual(outcomes, ["RETRYING", "RETRYING", "RETRIES_EXHAUSTED"])

    def test_non_retryable_4xx_raises_immediately_without_retry(self):
        session = _ScriptedSession([_FakeResponse(status_code=404)])
        with mock.patch("ingest.nhl_api.time.sleep") as mock_sleep:
            with self.assertRaises(RuntimeError):
                nhl_api._get_json(session, "https://x")
        self.assertEqual(session.calls, 1)  # never retried
        mock_sleep.assert_not_called()

    def test_response_without_status_code_attribute_is_treated_as_success(self):
        """Several existing test doubles across this suite model
        raise_for_status()/json() but never set status_code at all --
        this must keep working exactly as before, not start raising
        AttributeError."""
        class _BareResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"ok": True}

        class _BareSession:
            def get(self, url, timeout=15):
                return _BareResponse()

        result = nhl_api._get_json(_BareSession(), "https://x")
        self.assertEqual(result, {"ok": True})

    def test_structured_log_actually_writes_a_jsonl_record(self):
        import json
        import tempfile
        from pathlib import Path

        session = _ScriptedSession([
            _FakeResponse(status_code=429),
            _FakeResponse(json_data={"ok": True}, status_code=200),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "retry.jsonl"
            with mock.patch("ingest.nhl_api.time.sleep"):
                nhl_api._get_json(session, "https://x", log_path=log_path)
            lines = log_path.read_text().strip().splitlines()
            self.assertEqual(len(lines), 1)
            record = json.loads(lines[0])
            self.assertEqual(record["status_code"], 429)
            self.assertEqual(record["outcome"], "RETRYING")


# ---------------------------------------------------------------------
# 2. ingest_current_roster_identities: partial-success + durability
# ---------------------------------------------------------------------
class TestRosterBatchPartialSuccess(unittest.TestCase):
    def setUp(self):
        self.conn, self.path = make_test_db()
        for team in ("EDM", "TOR", "BOS"):
            self.conn.execute("INSERT OR IGNORE INTO teams (team_id) VALUES (?)", (team,))
        self.conn.commit()

    def _roster(self, entries):
        out = {"forwards": [], "defensemen": [], "goalies": []}
        for player_id, first, last, group in entries:
            out[group].append({"id": player_id, "firstName": {"default": first},
                                "lastName": {"default": last}})
        return out

    def test_one_team_failing_after_retries_does_not_block_the_others(self):
        good_roster = self._roster([(1, "Connor", "McDavid", "forwards")])

        class _PartlyBrokenSession:
            def __init__(self):
                self.calls = []

            def get(self, url, timeout=15):
                self.calls.append(url)
                if "/roster/TOR/" in url:
                    return _FakeResponse(status_code=429)
                return _FakeResponse(json_data=good_roster, status_code=200)

        session = _PartlyBrokenSession()
        with mock.patch("ingest.nhl_api.time.sleep"), \
             mock.patch("ingest.nhl_api._log_retry_event"), \
             mock.patch.object(nhl_api, "MAX_RETRIES", 0):
            result = ingest_current_roster_identities(self.conn, session, ["EDM", "TOR", "BOS"])

        self.assertEqual(result["status"], "PARTIAL_SUCCESS")
        self.assertEqual(result["teams_processed"], 2)
        self.assertEqual(result["teams_failed"], 1)
        self.assertEqual(result["failed_teams"][0]["team"], "TOR")
        # With MAX_RETRIES patched to 0, TOR's single 429 gets exactly one
        # attempt (no retry) before failing -- proves the module-constant
        # patch actually reaches _get_json's retry loop, not just that the
        # batch happens to tolerate however many retries the real default
        # would have caused.
        self.assertEqual(session.calls.count("https://api-web.nhle.com/v1/roster/TOR/current"), 1)

    def test_earlier_teams_successful_writes_survive_a_later_teams_failure(self):
        """The exact durability bug this fix closes: previously a single
        conn.commit() at the end of the whole batch meant one team's
        failure discarded every earlier team's in-progress transaction."""
        good_roster = self._roster([(1, "Connor", "McDavid", "forwards")])

        class _FailsOnSecondTeam:
            def get(self, url, timeout=15):
                if "/roster/TOR/" in url:
                    return _FakeResponse(status_code=500)
                return _FakeResponse(json_data=good_roster, status_code=200)

        with mock.patch("ingest.nhl_api.time.sleep"), \
             mock.patch("ingest.nhl_api._log_retry_event"), \
             mock.patch.object(nhl_api, "MAX_RETRIES", 0):
            ingest_current_roster_identities(self.conn, _FailsOnSecondTeam(), ["EDM", "TOR"])

        # EDM's write (processed before TOR failed) must be durable.
        row = self.conn.execute(
            "SELECT team_id FROM team_membership_events WHERE player_id=1 "
            "ORDER BY id DESC LIMIT 1").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["team_id"], "EDM")

    def test_all_teams_failing_reports_failed_not_partial(self):
        with mock.patch("ingest.nhl_api.time.sleep"), \
             mock.patch("ingest.nhl_api._log_retry_event"), \
             mock.patch.object(nhl_api, "MAX_RETRIES", 0):
            class _AlwaysBroken:
                def get(self, url, timeout=15):
                    return _FakeResponse(status_code=429)
            result = ingest_current_roster_identities(self.conn, _AlwaysBroken(), ["EDM", "TOR"])
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["teams_processed"], 0)


if __name__ == "__main__":
    unittest.main()
