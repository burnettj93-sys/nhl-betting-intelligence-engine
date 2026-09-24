"""
Intraday Refresh Architecture (P0.1, 2026-09-24 hardening block):
tests for operational/nhl_sync.py's midday/pregame refresh functions,
teams_playing_in_window()'s puck-drop-relative windowing, and
operational/ingestion_health.py's per-component status tracking.
"""
from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import db
from operational import ingestion_health, nhl_sync


def _fresh_conn():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return db.init_db(Path(tmp.name), wipe=True)


def _insert_game(conn, game_id, home, away, scheduled_start_utc, game_state="SCHEDULED"):
    conn.execute("INSERT OR IGNORE INTO teams (team_id) VALUES (?)", (home,))
    conn.execute("INSERT OR IGNORE INTO teams (team_id) VALUES (?)", (away,))
    conn.execute(
        "INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team, away_team, "
        "schedule_observed_at_utc, game_state, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (game_id, "20262027", "2026-09-24", scheduled_start_utc, home, away,
         "2026-09-24T00:00:00Z", game_state, "test"))
    conn.commit()


class _FakeResponse:
    def __init__(self, json_data=None, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _EmptyScheduleSession:
    """Serves an empty schedule response for any call -- enough to
    exercise ingest_range()'s free schedule check without a real
    network call or any games actually appearing."""

    def __init__(self):
        self.calls = []

    def get(self, url, timeout=15):
        self.calls.append(url)
        return _FakeResponse({"gameWeek": [{"date": "2026-09-24", "games": []}], "nextStartDate": None})


class TestTeamsPlayingInWindow(unittest.TestCase):
    def setUp(self):
        self.conn = _fresh_conn()
        self.now = dt.datetime(2026, 9, 24, 12, 0, tzinfo=dt.timezone.utc)

    def test_game_inside_window_is_included(self):
        _insert_game(self.conn, 1, "EDM", "CGY", "2026-09-24T15:30:00Z")  # 3.5h out
        teams = nhl_sync.teams_playing_in_window(self.conn, self.now, (3.0, 4.5))
        self.assertEqual(teams, ["CGY", "EDM"])

    def test_game_outside_window_is_excluded(self):
        _insert_game(self.conn, 1, "EDM", "CGY", "2026-09-25T00:00:00Z")  # 12h out
        teams = nhl_sync.teams_playing_in_window(self.conn, self.now, (3.0, 4.5))
        self.assertEqual(teams, [])

    def test_started_game_is_excluded_even_if_still_marked_scheduled(self):
        _insert_game(self.conn, 1, "EDM", "CGY", "2026-09-24T11:00:00Z")  # 1h in the past
        teams = nhl_sync.teams_playing_in_window(self.conn, self.now, (3.0, 4.5))
        self.assertEqual(teams, [])

    def test_final_game_is_never_included_regardless_of_start_time(self):
        _insert_game(self.conn, 1, "EDM", "CGY", "2026-09-24T15:30:00Z", game_state="FINAL")
        teams = nhl_sync.teams_playing_in_window(self.conn, self.now, (3.0, 4.5))
        self.assertEqual(teams, [])

    def test_no_games_at_all_returns_empty_list_not_an_error(self):
        teams = nhl_sync.teams_playing_in_window(self.conn, self.now, (3.0, 4.5))
        self.assertEqual(teams, [])

    def test_naive_timestamp_matching_real_storage_format_does_not_crash(self):
        """Real bug found via a manual dry run before this job was ever
        scheduled: nhl.db actually stores scheduled_start_utc as a NAIVE
        string with no 'Z'/offset at all (ingest/timestamps.py's own
        canonicalization) -- a real row looks like
        '2026-09-24T15:30:00', not '...Z'. This used to raise
        TypeError: can't subtract offset-naive and offset-aware
        datetimes the moment a real game existed."""
        _insert_game(self.conn, 1, "EDM", "CGY", "2026-09-24T15:30:00")  # no Z, matches real storage
        teams = nhl_sync.teams_playing_in_window(self.conn, self.now, (3.0, 4.5))
        self.assertEqual(teams, ["CGY", "EDM"])

    def test_multiple_games_only_in_window_teams_returned(self):
        _insert_game(self.conn, 1, "EDM", "CGY", "2026-09-24T15:30:00Z")  # in window
        _insert_game(self.conn, 2, "TOR", "MTL", "2026-09-25T00:00:00Z")  # out of window
        teams = nhl_sync.teams_playing_in_window(self.conn, self.now, (3.0, 4.5))
        self.assertEqual(teams, ["CGY", "EDM"])


class TestTeamsPlayingOn(unittest.TestCase):
    def test_returns_home_and_away_for_the_exact_date(self):
        conn = _fresh_conn()
        _insert_game(conn, 1, "EDM", "CGY", "2026-09-24T23:00:00Z")
        teams = nhl_sync.teams_playing_on(conn, dt.date(2026, 9, 24))
        self.assertEqual(teams, ["CGY", "EDM"])

    def test_different_date_returns_nothing(self):
        conn = _fresh_conn()
        _insert_game(conn, 1, "EDM", "CGY", "2026-09-24T23:00:00Z")
        teams = nhl_sync.teams_playing_on(conn, dt.date(2026, 9, 25))
        self.assertEqual(teams, [])


class TestMiddayRefresh(unittest.TestCase):
    def test_makes_no_roster_calls_ever(self):
        """The midday refresh is schedule-only by design -- confirms it
        never touches the roster endpoint at all, regardless of what
        games exist."""
        conn = _fresh_conn()
        session = _EmptyScheduleSession()
        result = nhl_sync.run_midday_refresh(conn=conn, today=dt.date(2026, 9, 24), session=session)
        self.assertEqual(result["status"], "SUCCESS")
        self.assertTrue(all("/roster/" not in url for url in session.calls))

    def test_failure_is_reported_not_raised(self):
        conn = _fresh_conn()

        class _Broken:
            def get(self, *a, **k):
                raise RuntimeError("network down")

        result = nhl_sync.run_midday_refresh(conn=conn, today=dt.date(2026, 9, 24), session=_Broken())
        self.assertEqual(result["status"], "FAILED")
        self.assertIn("network down", result["error"])

    def test_records_ingestion_health(self):
        conn = _fresh_conn()
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "health.json"
            with mock.patch.object(ingestion_health, "DEFAULT_CACHE_PATH", cache_path):
                nhl_sync.run_midday_refresh(conn=conn, today=dt.date(2026, 9, 24), session=_EmptyScheduleSession())
            health = ingestion_health.load_health(cache_path=cache_path)
        self.assertIn("nhl_midday_schedule_refresh", health)
        self.assertEqual(health["nhl_midday_schedule_refresh"]["last_status"], "SUCCESS")
        self.assertIsNotNone(health["nhl_midday_schedule_refresh"]["last_success_utc"])


class TestPregameTargetedRefresh(unittest.TestCase):
    def test_zero_games_in_window_makes_zero_roster_calls(self):
        conn = _fresh_conn()
        session = _EmptyScheduleSession()
        result = nhl_sync.run_targeted_pregame_refresh(
            conn=conn, today=dt.date(2026, 9, 24), session=session,
            now=dt.datetime(2026, 9, 24, 12, 0, tzinfo=dt.timezone.utc))
        self.assertEqual(result["teams_targeted"], 0)
        self.assertTrue(all("/roster/" not in url for url in session.calls))
        self.assertEqual(result["status"], "SUCCESS")

    def test_game_in_window_triggers_a_targeted_roster_call(self):
        conn = _fresh_conn()
        _insert_game(conn, 1, "EDM", "CGY", "2026-09-24T15:30:00Z")

        good_roster = {"forwards": [], "defensemen": [], "goalies": []}

        class _SessionWithRoster(_EmptyScheduleSession):
            def get(self, url, timeout=15):
                self.calls.append(url)
                if "/roster/" in url:
                    return _FakeResponse(good_roster)
                return super().get(url, timeout=timeout)

        session = _SessionWithRoster()
        result = nhl_sync.run_targeted_pregame_refresh(
            conn=conn, today=dt.date(2026, 9, 24), session=session,
            now=dt.datetime(2026, 9, 24, 12, 0, tzinfo=dt.timezone.utc))
        self.assertEqual(result["teams_targeted"], 2)
        self.assertEqual(result["roster_status"], "SUCCESS")
        roster_urls = [u for u in session.calls if "/roster/" in u]
        self.assertEqual(len(roster_urls), 2)  # exactly EDM + CGY, not all 32 teams

    def test_roster_failure_degrades_to_partial_success_not_failed(self):
        conn = _fresh_conn()
        _insert_game(conn, 1, "EDM", "CGY", "2026-09-24T15:30:00Z")

        class _AlwaysRateLimited(_EmptyScheduleSession):
            def get(self, url, timeout=15):
                self.calls.append(url)
                if "/roster/" in url:
                    return _FakeResponse(status_code=429)
                return super().get(url, timeout=timeout)

        with mock.patch("ingest.nhl_api.time.sleep"), mock.patch("ingest.nhl_api.MAX_RETRIES", 0):
            result = nhl_sync.run_targeted_pregame_refresh(
                conn=conn, today=dt.date(2026, 9, 24), session=_AlwaysRateLimited(),
                now=dt.datetime(2026, 9, 24, 12, 0, tzinfo=dt.timezone.utc))
        self.assertEqual(result["status"], "PARTIAL_SUCCESS")
        self.assertEqual(result["components"]["schedule"], "SUCCESS")


class TestIngestionHealth(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.cache_path = Path(self._tmpdir.name) / "health.json"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_record_run_creates_the_file(self):
        ingestion_health.record_run("test_component", {"status": "SUCCESS"}, cache_path=self.cache_path)
        self.assertTrue(self.cache_path.exists())

    def test_success_sets_last_success_utc(self):
        row = ingestion_health.record_run("test_component", {"status": "SUCCESS"}, cache_path=self.cache_path)
        self.assertIsNotNone(row["last_success_utc"])
        self.assertEqual(row["last_status"], "SUCCESS")

    def test_failure_does_not_set_last_success_utc(self):
        row = ingestion_health.record_run("test_component", {"status": "FAILED", "error": "boom"},
                                           cache_path=self.cache_path)
        self.assertNotIn("last_success_utc", row)
        self.assertEqual(row["last_detail"], "boom")

    def test_a_later_failure_does_not_erase_an_earlier_success_timestamp(self):
        ingestion_health.record_run("test_component", {"status": "SUCCESS"}, cache_path=self.cache_path)
        first = ingestion_health.load_health(cache_path=self.cache_path)["test_component"]["last_success_utc"]
        ingestion_health.record_run("test_component", {"status": "FAILED", "error": "boom"}, cache_path=self.cache_path)
        second = ingestion_health.load_health(cache_path=self.cache_path)["test_component"]["last_success_utc"]
        self.assertEqual(first, second)  # last KNOWN-GOOD success, never overwritten by a failure

    def test_multiple_components_tracked_independently(self):
        ingestion_health.record_run("a", {"status": "SUCCESS"}, cache_path=self.cache_path)
        ingestion_health.record_run("b", {"status": "FAILED"}, cache_path=self.cache_path)
        health = ingestion_health.load_health(cache_path=self.cache_path)
        self.assertEqual(set(health.keys()), {"a", "b"})

    def test_component_age_hours_none_when_never_succeeded(self):
        row = {"last_status": "FAILED"}
        self.assertIsNone(ingestion_health.component_age_hours(row))

    def test_component_age_hours_computed_correctly(self):
        now = dt.datetime(2026, 9, 24, 12, 0, tzinfo=dt.timezone.utc)
        row = {"last_success_utc": (now - dt.timedelta(hours=5)).isoformat()}
        self.assertAlmostEqual(ingestion_health.component_age_hours(row, now=now), 5.0, places=2)


if __name__ == "__main__":
    unittest.main()
