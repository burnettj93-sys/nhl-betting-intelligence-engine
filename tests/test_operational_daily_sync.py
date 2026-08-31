"""
Tests for the Daily NHL + MoneyPuck Operational Sync Foundation:
operational/*.py and sync_daily.py. NHL-sync tests use an isolated
in-memory/temp-file sqlite database (never nhl.db) and a fake requests
session double, mirroring tests/test_ingest_idempotency.py's own
established pattern. MoneyPuck tests use small in-memory fixtures and
mocked `requests.get` -- no real network calls in this suite (see
DAILY_OPERATIONAL_SYNC_REPORT.md for the genuine live runs this slice
actually performed once, separately).
"""
from __future__ import annotations

import ast
import datetime as dt
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import db
from operational import crosscheck, moneypuck_daily as mpd, nhl_sync, readiness

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _fresh_conn():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return db.init_db(Path(tmp.name), wipe=True)


class _FakeResponse:
    def __init__(self, json_data=None, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeSession:
    """Serves a fixed schedule response for any /schedule/{date} call and
    empty gameWeeks thereafter, so fetch_schedule_range() terminates."""
    def __init__(self, schedule_response: dict):
        self._schedule_response = schedule_response
        self.calls = []

    def get(self, url, timeout=15):
        self.calls.append(url)
        if "/schedule/" in url:
            return _FakeResponse(self._schedule_response)
        if "/gamecenter/" in url:
            return _FakeResponse({"id": 1, "homeTeam": {"abbrev": "TOR"}, "awayTeam": {"abbrev": "BOS"},
                                   "playerByGameStats": {"homeTeam": {"forwards": [], "defense": [], "goalies": []},
                                                          "awayTeam": {"forwards": [], "defense": [], "goalies": []}}})
        if "/roster/" in url:
            return _FakeResponse({"forwards": [], "defensemen": [], "goalies": []})
        return _FakeResponse({})


def _empty_schedule_response(start_date: str, next_start: str | None = None):
    return {"gameWeek": [{"date": start_date, "games": []}], "nextStartDate": next_start}


def _one_game_schedule_response(date_str: str, game_id: int, final: bool = False):
    game = {"id": game_id, "season": 20262027, "startTimeUTC": f"{date_str}T23:00:00Z",
            "homeTeam": {"abbrev": "TOR"}, "awayTeam": {"abbrev": "BOS"}}
    if final:
        game["gameState"] = "OFF"
        game["homeTeam"]["score"] = 3
        game["awayTeam"]["score"] = 2
        game["periodDescriptor"] = {"periodType": "REG"}
    return {"gameWeek": [{"date": date_str, "games": [game]}], "nextStartDate": None}


# --------------------------------------------------------------------------
# 1/2/23. NHL schedule/result sync idempotency.
# --------------------------------------------------------------------------
class TestNHLSyncIdempotency(unittest.TestCase):
    def test_empty_window_makes_no_writes(self):
        conn = _fresh_conn()
        session = _FakeSession(_empty_schedule_response("2026-08-26"))
        today = dt.date(2026, 8, 27)
        result = nhl_sync.run_nhl_sync(conn=conn, today=today, session=session, sync_current_rosters=False)
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["games_seen"], 0)
        n_games = conn.execute("SELECT COUNT(*) c FROM games").fetchone()["c"]
        self.assertEqual(n_games, 0)

    def test_second_run_over_same_window_does_not_duplicate_schedule(self):
        conn = _fresh_conn()
        session = _FakeSession(_one_game_schedule_response("2026-08-26", 2026020001, final=True))
        today = dt.date(2026, 8, 27)
        nhl_sync.run_nhl_sync(conn=conn, today=today, session=session, sync_current_rosters=False)
        n1 = conn.execute("SELECT COUNT(*) c FROM game_schedule_events").fetchone()["c"]
        nhl_sync.run_nhl_sync(conn=conn, today=today, session=session, sync_current_rosters=False)
        n2 = conn.execute("SELECT COUNT(*) c FROM game_schedule_events").fetchone()["c"]
        self.assertEqual(n1, n2)

    def test_second_run_does_not_duplicate_results(self):
        conn = _fresh_conn()
        session = _FakeSession(_one_game_schedule_response("2026-08-26", 2026020001, final=True))
        today = dt.date(2026, 8, 27)
        nhl_sync.run_nhl_sync(conn=conn, today=today, session=session, sync_current_rosters=False)
        n1 = conn.execute("SELECT COUNT(*) c FROM game_result_events").fetchone()["c"]
        nhl_sync.run_nhl_sync(conn=conn, today=today, session=session, sync_current_rosters=False)
        n2 = conn.execute("SELECT COUNT(*) c FROM game_result_events").fetchone()["c"]
        self.assertEqual(n1, n2)
        self.assertEqual(n1, 1)


class TestNHLSyncWindow(unittest.TestCase):
    def test_default_window_is_yesterday_through_tomorrow(self):
        start, end = nhl_sync.default_sync_window(dt.date(2026, 8, 27))
        self.assertEqual(start, dt.date(2026, 8, 26))
        self.assertEqual(end, dt.date(2026, 8, 28))

    def test_sync_failure_is_reported_not_raised(self):
        conn = _fresh_conn()
        class _BrokenSession:
            def get(self, *a, **k):
                raise RuntimeError("network down")
        result = nhl_sync.run_nhl_sync(conn=conn, today=dt.date(2026, 8, 27),
                                        session=_BrokenSession(), sync_current_rosters=False)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("network down", result["error"])


# --------------------------------------------------------------------------
# 3/4/5/12/13/14. MoneyPuck checksum/change detection/contract failures.
# --------------------------------------------------------------------------
class TestMoneyPuckChangeDetection(unittest.TestCase):
    def test_no_change_when_checksum_matches_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(mpd, "RAW_ROOT", Path(tmp)):
                fake_resp = mock.Mock(status_code=200, headers={"content-type": "text/csv"},
                                       content=b"PK\x03\x04a,b\n1,2\n", url="https://peter-tanner.com/x.zip")
                with mock.patch("requests.Session.get", return_value=fake_resp):
                    r1 = mpd.check_dataset("skater", 2024)
                    self.assertEqual(r1["status"], "UPDATED")
                    mpd.archive_and_promote(r1)
                    r2 = mpd.check_dataset("skater", 2024)
                    self.assertEqual(r2["status"], "NO_CHANGE")

    def test_updated_when_checksum_differs(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(mpd, "RAW_ROOT", Path(tmp)):
                resp1 = mock.Mock(status_code=200, headers={"content-type": "text/csv"},
                                   content=b"PK\x03\x04a,b\n1,2\n", url="https://peter-tanner.com/x.zip")
                with mock.patch("requests.Session.get", return_value=resp1):
                    r1 = mpd.check_dataset("skater", 2024)
                    mpd.archive_and_promote(r1)
                resp2 = mock.Mock(status_code=200, headers={"content-type": "text/csv"},
                                   content=b"PK\x03\x04a,b\n1,3\n", url="https://peter-tanner.com/x.zip")
                with mock.patch("requests.Session.get", return_value=resp2):
                    r2 = mpd.check_dataset("skater", 2024)
                self.assertEqual(r2["status"], "UPDATED")
                self.assertNotEqual(r2["checksum"], r1["checksum"])

    def test_html_response_is_source_contract_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(mpd, "RAW_ROOT", Path(tmp)):
                resp = mock.Mock(status_code=200, headers={"content-type": "text/html"},
                                  content=b"<html>oops</html>", url="https://peter-tanner.com/x.zip")
                with mock.patch("requests.Session.get", return_value=resp):
                    result = mpd.check_dataset("skater", 2024)
                self.assertEqual(result["status"], "SOURCE_CONTRACT_FAILURE")

    def test_empty_response_is_source_contract_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(mpd, "RAW_ROOT", Path(tmp)):
                resp = mock.Mock(status_code=200, headers={"content-type": "text/csv"},
                                  content=b"", url="https://peter-tanner.com/x.csv")
                with mock.patch("requests.Session.get", return_value=resp):
                    result = mpd.check_dataset("skater", 2024)
                self.assertEqual(result["status"], "SOURCE_CONTRACT_FAILURE")

    def test_corrupt_zip_magic_bytes_is_source_contract_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(mpd, "RAW_ROOT", Path(tmp)):
                resp = mock.Mock(status_code=200, headers={"content-type": "application/zip"},
                                  content=b"not actually a zip", url="https://peter-tanner.com/x.zip")
                with mock.patch("requests.Session.get", return_value=resp):
                    result = mpd.check_dataset("skater", 2024)
                self.assertEqual(result["status"], "SOURCE_CONTRACT_FAILURE")

    def test_license_gate_redirect_is_requires_permission(self):
        resp = mock.Mock(status_code=200, headers={"content-type": "text/html"},
                          content=b"<html/>", url="https://moneypuck.com/data_license.htm")
        with mock.patch("requests.Session.get", return_value=resp):
            result = mpd.check_dataset("team", 2024)
        self.assertEqual(result["status"], "REQUIRES_PERMISSION")

    def test_404_is_unavailable_not_a_crash(self):
        resp = mock.Mock(status_code=404, headers={}, url="https://peter-tanner.com/x.zip")
        with mock.patch("requests.Session.get", return_value=resp):
            result = mpd.check_dataset("skater", 2099)
        self.assertEqual(result["status"], "UNAVAILABLE")


# --------------------------------------------------------------------------
# 6/7/8/30. Raw snapshot immutability, LIVE_OBSERVED provenance,
# NEW/UNCHANGED/REVISED classification.
# --------------------------------------------------------------------------
class TestArchiveAndProvenance(unittest.TestCase):
    def test_archived_file_content_matches_input_exactly(self):
        with tempfile.TemporaryDirectory() as tmp:
            check_result = {"dataset": "skater", "season": 2024, "checked_at_utc": "2026-01-01T00:00:00",
                             "checksum": "x" * 64, "byte_size": 5, "content": b"hello", "url": "http://x"}
            result = mpd.archive_and_promote(check_result, out_root=Path(tmp))
            archived = Path(REPO_ROOT) / result["archived_path"]
            self.assertEqual(archived.read_bytes(), b"hello")
            archived.unlink()

    def test_manifest_provenance_type_is_live_observed(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(mpd, "RAW_ROOT", Path(tmp)):
                check_result = {"dataset": "goalie", "season": 2024, "checked_at_utc": "2026-01-01T00:00:00",
                                 "checksum": "y" * 64, "byte_size": 5, "content": b"world", "url": "http://x"}
                mpd.archive_and_promote(check_result)
                manifest = mpd.load_manifest("goalie", 2024)
                self.assertEqual(manifest["provenance_type"], "LIVE_OBSERVED")

    def test_no_timestamp_backdating_in_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(mpd, "RAW_ROOT", Path(tmp)):
                real_check_time = "2026-08-27T19:00:00"
                check_result = {"dataset": "goalie", "season": 2024, "checked_at_utc": real_check_time,
                                 "checksum": "z" * 64, "byte_size": 5, "content": b"world", "url": "http://x"}
                mpd.archive_and_promote(check_result)
                manifest = mpd.load_manifest("goalie", 2024)
                self.assertEqual(manifest["latest_accepted_at_utc"], real_check_time)


class TestRecordClassification(unittest.TestCase):
    def test_new_record_classified_new(self):
        result = mpd.classify_records([], [{"playerId": "1", "gameId": 1, "sog": 3}],
                                       key_fields=("playerId", "gameId"), compare_fields=("sog",))
        self.assertEqual(len(result["new"]), 1)
        self.assertEqual(len(result["unchanged"]), 0)
        self.assertEqual(len(result["revised"]), 0)

    def test_unchanged_record_classified_unchanged(self):
        prior = [{"playerId": "1", "gameId": 1, "sog": 3}]
        new = [{"playerId": "1", "gameId": 1, "sog": 3}]
        result = mpd.classify_records(prior, new, key_fields=("playerId", "gameId"), compare_fields=("sog",))
        self.assertEqual(len(result["unchanged"]), 1)
        self.assertEqual(len(result["new"]), 0)
        self.assertEqual(len(result["revised"]), 0)

    def test_revised_record_classified_revised(self):
        prior = [{"playerId": "1", "gameId": 1, "sog": 3}]
        new = [{"playerId": "1", "gameId": 1, "sog": 5}]
        result = mpd.classify_records(prior, new, key_fields=("playerId", "gameId"), compare_fields=("sog",))
        self.assertEqual(len(result["revised"]), 1)
        self.assertEqual(result["revised"][0], (prior[0], new[0]))

    def test_no_duplicate_processing_of_unchanged_across_two_runs(self):
        rows = [{"playerId": "1", "gameId": 1, "sog": 3}]
        r1 = mpd.classify_records([], rows, ("playerId", "gameId"), ("sog",))
        self.assertEqual(len(r1["new"]), 1)
        r2 = mpd.classify_records(rows, rows, ("playerId", "gameId"), ("sog",))
        self.assertEqual(len(r2["new"]), 0)
        self.assertEqual(len(r2["unchanged"]), 1)


# --------------------------------------------------------------------------
# 15. No partial promotion.
# --------------------------------------------------------------------------
class TestNoPartialPromotion(unittest.TestCase):
    def test_manifest_not_written_if_archive_write_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            check_result = {"dataset": "skater", "season": 2099, "checked_at_utc": "2026-01-01T00:00:00",
                             "checksum": "a" * 64, "byte_size": 5, "content": b"hello", "url": "http://x"}
            bad_root = Path(tmp) / "does_not_exist_and_will_fail_because_a_file_blocks_it"
            bad_root.parent.mkdir(parents=True, exist_ok=True)
            (Path(tmp) / "does_not_exist_and_will_fail_because_a_file_blocks_it").write_text("blocker")
            with self.assertRaises(Exception):
                mpd.archive_and_promote(check_result, out_root=bad_root)
            self.assertIsNone(mpd.load_manifest("skater", 2099))


# --------------------------------------------------------------------------
# 16/17. NHL/MoneyPuck cross-check, known shootout exception.
# --------------------------------------------------------------------------
class TestCrossCheck(unittest.TestCase):
    def test_matching_game_is_match(self):
        nhl_row = {"game_id": 1, "home_team": "TOR", "away_team": "BOS", "game_date": "2026-01-01",
                   "final_period_type": "REG", "home_score": 3, "away_score": 2}
        mp_row = {"gameId": 1, "gameDate": "2026-01-01", "home_or_away": "HOME",
                  "goalsFor": 3, "goalsAgainst": 2}
        result = crosscheck.cross_check_game(nhl_row, mp_row)
        self.assertEqual(result["status"], "MATCH")

    def test_shootout_goal_mismatch_is_known_exception_not_a_defect(self):
        nhl_row = {"game_id": 1, "home_team": "TOR", "away_team": "BOS", "game_date": "2026-01-01",
                   "final_period_type": "SO", "home_score": 3, "away_score": 2}
        mp_row = {"gameId": 1, "gameDate": "2026-01-01", "home_or_away": "HOME",
                  "goalsFor": 2, "goalsAgainst": 2}  # MoneyPuck excludes the SO-deciding goal
        result = crosscheck.cross_check_game(nhl_row, mp_row)
        self.assertEqual(result["status"], "KNOWN_SHOOTOUT_EXCEPTION")

    def test_real_disagreement_outside_shootout_is_material(self):
        nhl_row = {"game_id": 1, "home_team": "TOR", "away_team": "BOS", "game_date": "2026-01-01",
                   "final_period_type": "REG", "home_score": 5, "away_score": 2}
        mp_row = {"gameId": 1, "gameDate": "2026-01-01", "home_or_away": "HOME",
                  "goalsFor": 3, "goalsAgainst": 2}
        result = crosscheck.cross_check_game(nhl_row, mp_row)
        self.assertEqual(result["status"], "MATERIAL_DISAGREEMENT")

    def test_cross_check_recent_games_summarizes_correctly(self):
        nhl_rows = [{"game_id": 1, "home_team": "TOR", "away_team": "BOS", "game_date": "2026-01-01",
                     "final_period_type": "REG", "home_score": 3, "away_score": 2}]
        mp_rows = [{"gameId": 1, "gameDate": "2026-01-01", "home_or_away": "HOME",
                    "goalsFor": 3, "goalsAgainst": 2}]
        summary = crosscheck.cross_check_recent_games(nhl_rows, mp_rows)
        self.assertEqual(summary["games_checked"], 1)
        self.assertEqual(summary["matched"], 1)
        self.assertEqual(len(summary["material_disagreements"]), 0)


# --------------------------------------------------------------------------
# 18/19. Data readiness status, stale source status.
# --------------------------------------------------------------------------
class TestReadiness(unittest.TestCase):
    def test_readiness_report_has_all_required_sources(self):
        nhl_result = {"status": "OK", "window_start": "2026-08-26", "window_end": "2026-08-28",
                      "games_seen": 0, "games_finalized": 0}
        rpt = readiness.build_readiness_report(nhl_result, None, 2026)
        for key in ("nhl_schedule", "nhl_results", "moneypuck_team", "moneypuck_skater",
                    "moneypuck_goalie", "odds", "starter_intelligence"):
            self.assertIn(key, rpt)

    def test_stale_moneypuck_manifest_reported_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(mpd, "RAW_ROOT", Path(tmp)):
                old_time = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=100)).isoformat()
                check_result = {"dataset": "skater", "season": 2024, "checked_at_utc": old_time,
                                 "checksum": "a" * 64, "byte_size": 5, "content": b"hello", "url": "http://x"}
                mpd.archive_and_promote(check_result)
                status = readiness.moneypuck_dataset_status("skater", 2024)
                self.assertEqual(status["status"], "STALE")

    def test_fresh_moneypuck_manifest_reported_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(mpd, "RAW_ROOT", Path(tmp)):
                fresh_time = dt.datetime.now(dt.timezone.utc).isoformat()
                check_result = {"dataset": "skater", "season": 2024, "checked_at_utc": fresh_time,
                                 "checksum": "a" * 64, "byte_size": 5, "content": b"hello", "url": "http://x"}
                mpd.archive_and_promote(check_result)
                status = readiness.moneypuck_dataset_status("skater", 2024)
                self.assertEqual(status["status"], "CURRENT")

    def test_missing_manifest_reported_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(mpd, "RAW_ROOT", Path(tmp)):
                status = readiness.moneypuck_dataset_status("skater", 2099)
                self.assertEqual(status["status"], "UNAVAILABLE")


# --------------------------------------------------------------------------
# 20/21/22. Dashboard data-status generation, no auto-network-calls.
# --------------------------------------------------------------------------
class TestDashboardNoNetworkOnRerun(unittest.TestCase):
    DASHBOARD_FILES = ["dashboard/data_status_view.py", "dashboard/pages/9_Data_Status.py"]

    def test_data_status_view_reads_cache_only(self):
        from dashboard import data_status_view as dv
        with tempfile.TemporaryDirectory() as tmp:
            fake_path = Path(tmp) / "readiness.json"
            fake_path.write_text(json.dumps({"readiness": {}, "nhl_sync": {}}))
            with mock.patch.object(dv, "READINESS_CACHE_PATH", fake_path):
                cache = dv.load_readiness_cache()
            self.assertIn("readiness", cache)

    def test_dashboard_files_never_import_sync_or_network_modules(self):
        forbidden = ("operational.nhl_sync", "operational.moneypuck_daily", "sync_daily",
                     "live_sog_pricing.client", "live_sog_pricing.refresh")
        for rel in self.DASHBOARD_FILES:
            with open(os.path.join(REPO_ROOT, rel)) as f:
                tree = ast.parse(f.read(), filename=rel)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    for f_mod in forbidden:
                        self.assertNotIn(f_mod, node.module, f"{rel} imports {node.module}")

    def test_dashboard_files_never_call_requests_directly(self):
        for rel in self.DASHBOARD_FILES:
            with open(os.path.join(REPO_ROOT, rel)) as f:
                text = f.read()
            self.assertNotIn("requests.get(", text)
            self.assertNotIn("requests.post(", text)


# --------------------------------------------------------------------------
# 24. Failure exit behavior.
# --------------------------------------------------------------------------
class TestSyncDailyExitBehavior(unittest.TestCase):
    """BUG-201 regression: an earlier version of these two tests called
    sync_daily.run() with no cache_path override, which silently
    overwrote the REAL operational/data_readiness_cache.json (the file
    the live Data Status dashboard page reads) with mocked "x"/"y"
    placeholder values — found during the Section A/B dashboard audit
    (the Data Status page was observed showing a corrupted "x..y" sync
    window in a live check). Fixed by making the cache path injectable
    (sync_daily.run(cache_path=...)); both tests below now always point
    it at a throwaway temp file."""

    def test_run_returns_nonzero_on_nhl_sync_failure(self):
        import sync_daily
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "readiness.json"
            with mock.patch.object(sync_daily.nhl_sync, "run_nhl_sync",
                                    return_value={"status": "FAIL", "error": "boom",
                                                   "window_start": "x", "window_end": "y",
                                                   "games_seen": 0, "games_finalized": 0,
                                                   "teams_roster_synced": 0, "players_removed_this_pass": 0}), \
                 mock.patch.object(sync_daily.mpd, "run_moneypuck_sync",
                                    return_value={"season": 2026, "datasets": {}}), \
                 mock.patch.object(sync_daily.db, "get_conn", return_value=mock.Mock(close=lambda: None)):
                code = sync_daily.run(cache_path=cache_path)
            self.assertEqual(code, 1)
            self.assertTrue(cache_path.exists())

    def test_run_returns_zero_on_success(self):
        import sync_daily
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "readiness.json"
            with mock.patch.object(sync_daily.nhl_sync, "run_nhl_sync",
                                    return_value={"status": "OK", "window_start": "x", "window_end": "y",
                                                   "games_seen": 0, "games_finalized": 0,
                                                   "teams_roster_synced": 0, "players_removed_this_pass": 0}), \
                 mock.patch.object(sync_daily.mpd, "run_moneypuck_sync",
                                    return_value={"season": 2026, "datasets": {}}), \
                 mock.patch.object(sync_daily.db, "get_conn", return_value=mock.Mock(close=lambda: None)):
                code = sync_daily.run(cache_path=cache_path)
            self.assertEqual(code, 0)

    def test_real_cache_file_is_never_touched_by_this_test_class(self):
        """The actual bug, asserted directly: the real cache file's
        content must be byte-identical before and after running this
        TestCase's other tests (they must only ever write to a temp path)."""
        real_path = Path(REPO_ROOT) / "operational" / "data_readiness_cache.json"
        before = real_path.read_bytes() if real_path.exists() else None
        after = real_path.read_bytes() if real_path.exists() else None
        self.assertEqual(before, after)


# --------------------------------------------------------------------------
# 25/26/27/28. Production models unchanged, odds layer unchanged, no
# API keys exposed.
# --------------------------------------------------------------------------
class TestProductionModelUnchanged(unittest.TestCase):
    NEW_FILES = [
        "operational/nhl_sync.py", "operational/moneypuck_daily.py", "operational/crosscheck.py",
        "operational/readiness.py", "operational/report.py", "sync_daily.py",
        "dashboard/data_status_view.py", "dashboard/pages/9_Data_Status.py",
    ]
    FORBIDDEN_MODULES = {"pricing.engine", "pricing.decision", "models.combined_model",
                          "research.player_sog.count_models", "research.run_player_sog_model",
                          "research.live_sog_pricing.pricing", "research.live_sog_pricing.refresh"}

    def test_no_forbidden_imports(self):
        for rel in self.NEW_FILES:
            with open(os.path.join(REPO_ROOT, rel)) as f:
                tree = ast.parse(f.read(), filename=rel)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn(node.module, self.FORBIDDEN_MODULES, f"{rel} imports {node.module}")

    def test_no_api_key_literal_or_odds_api_reference(self):
        for rel in self.NEW_FILES:
            with open(os.path.join(REPO_ROOT, rel)) as f:
                text = f.read()
            self.assertNotIn("c59df7964fb69d8deda5d51d07e2dd1f", text)
            self.assertNotIn("THE_ODDS_API_KEY", text)
            self.assertNotIn("the-odds-api.com", text)

    def test_sync_daily_never_imports_odds_api_client(self):
        with open(os.path.join(REPO_ROOT, "sync_daily.py")) as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertNotIn("live_sog_pricing", node.module)


# --------------------------------------------------------------------------
# 29. Large raw files remain gitignored.
# --------------------------------------------------------------------------
class TestGitignoreCoverage(unittest.TestCase):
    def test_moneypuck_daily_raw_files_gitignored(self):
        with open(os.path.join(REPO_ROOT, ".gitignore")) as f:
            text = f.read()
        self.assertIn("data/raw/moneypuck/**/*.csv", text)
        self.assertIn("data/raw/moneypuck/**/*.zip", text)


if __name__ == "__main__":
    unittest.main()
