"""
Starting-Goalie Certainty + Prop Contract Watch block (2026-09-24), Part
9: tests for the odds-archive hygiene split (docs/
ODDS_ARCHIVE_STORAGE_RECOMMENDATION.md, implemented non-destructively).
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from operational import backup_databases as bd
from operational import real_prop_orchestrator as rpo
from research.live_sog_pricing import archive


class TestArchiveWritePathMovedToRuntimeStorage(unittest.TestCase):
    def test_archive_dir_now_points_to_the_gitignored_runtime_location(self):
        self.assertEqual(archive.ARCHIVE_DIR, Path(archive.REPO_ROOT) / "operational" / "odds_archive" / "live")

    def test_legacy_git_tracked_directory_is_a_distinct_untouched_path(self):
        legacy = Path(archive.REPO_ROOT) / "data" / "raw" / "the_odds_api" / "live"
        self.assertNotEqual(archive.ARCHIVE_DIR, legacy)
        # Real, historical evidence -- confirms nothing was moved/deleted.
        self.assertTrue(legacy.exists())
        self.assertGreater(len(list(legacy.glob("*.json"))), 0)

    def test_archive_result_with_no_out_dir_override_writes_to_the_new_runtime_location(self):
        from research.live_sog_pricing.client import ApiResult
        with tempfile.TemporaryDirectory() as tmp:
            fake_dir = Path(tmp) / "odds_archive" / "live"
            with mock.patch.object(archive, "ARCHIVE_DIR", fake_dir):
                result = ApiResult(ok=True, data={"id": "evt-x"}, status_code=200,
                                    retrieved_at_utc="2026-10-15T18:00:00Z", endpoint="/sports/icehockey_nhl/odds",
                                    requests_used=1, requests_remaining=99, requests_last=1, error=None)
                path = archive.archive_result(result, event_id="evt-x", market_filter="player_shots_on_goal",
                                               bookmaker_filter="draftkings")
            self.assertTrue(path.is_relative_to(fake_dir))
            self.assertTrue(path.exists())


class TestOrchestratorScansBothLocations(unittest.TestCase):
    def test_real_prop_orchestrator_constants_match_the_split(self):
        self.assertEqual(rpo.RUNTIME_ARCHIVE_DIR, archive.ARCHIVE_DIR)
        self.assertEqual(rpo.LEGACY_ARCHIVE_DIR,
                          Path(rpo.REPO_ROOT) / "data" / "raw" / "the_odds_api" / "live")

    def test_recent_archive_payloads_finds_a_capture_in_either_location(self):
        import json as _json
        payload_doc = {
            "meta": {"market_filter": "player_shots_on_goal", "retrieved_at_utc": "2026-09-24T12:00:00Z"},
            "response": {"id": "evt-legacy-test"},
        }
        with tempfile.TemporaryDirectory() as runtime_tmp, tempfile.TemporaryDirectory() as legacy_tmp:
            (Path(legacy_tmp) / "a.json").write_text(_json.dumps(payload_doc))
            with mock.patch.object(rpo, "RUNTIME_ARCHIVE_DIR", Path(runtime_tmp)), \
                 mock.patch.object(rpo, "LEGACY_ARCHIVE_DIR", Path(legacy_tmp)), \
                 mock.patch("operational.real_prop_orchestrator.dt") as mock_dt:
                import datetime as real_dt
                mock_dt.datetime.now.return_value = real_dt.datetime(2026, 9, 24, 12, 5, tzinfo=real_dt.timezone.utc)
                mock_dt.datetime.fromisoformat = real_dt.datetime.fromisoformat
                payloads = rpo._recent_archive_payloads("player_shots_on_goal")
        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]["id"], "evt-legacy-test")


class TestBackupCoversTheOddsArchive(unittest.TestCase):
    def test_backup_odds_archive_produces_a_real_tarball(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp) / "source"
            source_dir.mkdir()
            (source_dir / "a.json").write_text('{"meta": {}, "response": {}}')
            backup_root = Path(tmp) / "backups"
            result = bd.backup_odds_archive(source_dir=source_dir, backup_root=backup_root)
        self.assertEqual(result["status"], "SUCCESS")
        self.assertIsNotNone(result["backup_path"])

    def test_backup_odds_archive_skips_honestly_when_nothing_captured_yet(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp) / "empty_source"
            result = bd.backup_odds_archive(source_dir=source_dir, backup_root=Path(tmp) / "backups")
        self.assertEqual(result["status"], "SKIPPED")

    def test_run_all_backups_includes_the_odds_archive(self):
        # VPS Production Deployment block (2026-09-24), Part 13: real gap
        # found -- run_all_backups() calls ingestion_health.record_run()
        # unconditionally, and this test was silently overwriting the
        # REAL operational/ingestion_health_cache.json with a fake
        # "database_backups" entry on every run.
        from operational import ingestion_health
        with tempfile.TemporaryDirectory() as tmp:
            health_cache = Path(tmp) / "health.json"
            with mock.patch.object(ingestion_health, "DEFAULT_CACHE_PATH", health_cache):
                result = bd.run_all_backups(backup_root=Path(tmp) / "backups")
        self.assertIn("odds_archive", result["results"])


if __name__ == "__main__":
    unittest.main()
