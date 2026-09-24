"""
Real Recommendation Pipeline block (2026-09-24), Part 24: the morning
workflow (07:00 NHL sync -> 07:15 settlement -> 07:30 postmortem -> 07:45
backup) was previously only clock-scheduled -- each job ran regardless of
whether the one before it actually succeeded. These tests cover the new
dependency-awareness wiring: operational.ingestion_health.dependency_ready()
itself, and the two real call sites (settle_daily_observations.main(),
daily_postmortem.main()) that now defer rather than run against an
unconfirmed upstream, or (for postmortem) report a misleadingly complete
day. Never a retry loop -- a DEFERRED run is a single skip, picked up by
the next scheduled invocation.
"""
from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from operational import ingestion_health as ih


def _tmp_cache_path():
    fd, path = tempfile.mkstemp(suffix=".json")
    Path(path).unlink()
    return Path(path)


class TestDependencyReady(unittest.TestCase):
    def setUp(self):
        self.cache_path = _tmp_cache_path()

    def tearDown(self):
        self.cache_path.unlink(missing_ok=True)

    def test_component_never_run_is_not_ready(self):
        ready, reason = ih.dependency_ready("nhl_sync_full", max_age_hours=30.0, cache_path=self.cache_path)
        self.assertFalse(ready)
        self.assertIn("never", reason)

    def test_recent_success_is_ready(self):
        ih.record_run("nhl_sync_full", {"status": "SUCCESS"}, cache_path=self.cache_path)
        ready, reason = ih.dependency_ready("nhl_sync_full", max_age_hours=30.0, cache_path=self.cache_path)
        self.assertTrue(ready)

    def test_partial_success_status_still_counts_as_ready(self):
        """PARTIAL_SUCCESS (the real status nhl_sync.py reports on an
        isolated per-team 429, per docs/RELIABILITY_429_FIX.md) is not a
        _FAILURE_STATUSES entry -- it's a real, working run, just not a
        clean one, and must not block settlement."""
        ih.record_run("nhl_sync_full", {"status": "PARTIAL_SUCCESS"}, cache_path=self.cache_path)
        ready, reason = ih.dependency_ready("nhl_sync_full", max_age_hours=30.0, cache_path=self.cache_path)
        self.assertTrue(ready)

    def test_failed_status_is_not_ready(self):
        ih.record_run("nhl_sync_full", {"status": "FAILED", "error": "boom"}, cache_path=self.cache_path)
        ready, reason = ih.dependency_ready("nhl_sync_full", max_age_hours=30.0, cache_path=self.cache_path)
        self.assertFalse(ready)
        self.assertIn("boom", reason)

    def test_deferred_status_is_not_ready_and_never_counted_as_success(self):
        ih.record_run("settlement", {"status": "DEFERRED", "reason": "upstream not ready"},
                       cache_path=self.cache_path)
        ready, reason = ih.dependency_ready("settlement", max_age_hours=30.0, cache_path=self.cache_path)
        self.assertFalse(ready)
        row = ih.load_health(cache_path=self.cache_path)["settlement"]
        self.assertNotIn("last_success_utc", row)  # a deferred run is never a success

    def test_stale_success_beyond_max_age_is_not_ready(self):
        stale_time = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=40)).isoformat()
        ih.record_run("nhl_sync_full", {"status": "SUCCESS"}, cache_path=self.cache_path)
        health = ih.load_health(cache_path=self.cache_path)
        health["nhl_sync_full"]["last_success_utc"] = stale_time
        self.cache_path.write_text(json.dumps(health))
        ready, reason = ih.dependency_ready("nhl_sync_full", max_age_hours=30.0, cache_path=self.cache_path)
        self.assertFalse(ready)
        self.assertIn("ago", reason)


class TestSettlementDefersOnUnhealthyUpstream(unittest.TestCase):
    def setUp(self):
        self.cache_path = _tmp_cache_path()

    def tearDown(self):
        self.cache_path.unlink(missing_ok=True)

    def _patch_cache(self):
        return mock.patch.object(ih, "DEFAULT_CACHE_PATH", self.cache_path)

    def test_settlement_defers_when_nhl_sync_never_ran(self):
        from operational import settle_daily_observations as sdo
        with self._patch_cache(), \
             mock.patch("operational.deployment_mode.require_active_scheduler_or_exit", return_value=True), \
             mock.patch.object(sdo, "run_settlement_batch") as mock_batch:
            sdo.main()
        mock_batch.assert_not_called()
        health = ih.load_health(cache_path=self.cache_path)
        self.assertEqual(health["settlement"]["last_status"], "DEFERRED")

    def test_settlement_proceeds_when_nhl_sync_recently_succeeded(self):
        from operational import settle_daily_observations as sdo
        ih.record_run("nhl_sync_full", {"status": "SUCCESS"}, cache_path=self.cache_path)
        with self._patch_cache(), \
             mock.patch("operational.deployment_mode.require_active_scheduler_or_exit", return_value=True), \
             mock.patch.object(sdo, "pl") as mock_pl, \
             mock.patch.object(sdo, "run_settlement_batch",
                                return_value={"total_candidates": 0, "settled_win": 0, "settled_loss": 0,
                                              "settled_void": 0, "settled_unresolved": 0,
                                              "still_pending_game_not_final": 0, "errors": []}) as mock_batch:
            sdo.main()
        mock_batch.assert_called_once()


class TestPostmortemDefersOnUnhealthyUpstream(unittest.TestCase):
    def setUp(self):
        self.cache_path = _tmp_cache_path()

    def tearDown(self):
        self.cache_path.unlink(missing_ok=True)

    def _patch_cache(self):
        return mock.patch.object(ih, "DEFAULT_CACHE_PATH", self.cache_path)

    def test_postmortem_defers_when_settlement_never_ran(self):
        from operational import daily_postmortem as dpm
        with self._patch_cache(), \
             mock.patch("operational.deployment_mode.require_active_scheduler_or_exit", return_value=True), \
             mock.patch.object(dpm, "run_daily_postmortem") as mock_review:
            dpm.main()
        mock_review.assert_not_called()
        health = ih.load_health(cache_path=self.cache_path)
        self.assertEqual(health["postmortem"]["last_status"], "DEFERRED")

    def test_postmortem_defers_when_settlement_last_run_failed(self):
        from operational import daily_postmortem as dpm
        ih.record_run("settlement", {"status": "FAILED", "error": "db locked"}, cache_path=self.cache_path)
        with self._patch_cache(), \
             mock.patch("operational.deployment_mode.require_active_scheduler_or_exit", return_value=True), \
             mock.patch.object(dpm, "run_daily_postmortem") as mock_review:
            dpm.main()
        mock_review.assert_not_called()

    def test_postmortem_proceeds_when_settlement_recently_succeeded(self):
        from operational import daily_postmortem as dpm
        ih.record_run("settlement", {"status": "SUCCESS"}, cache_path=self.cache_path)
        with self._patch_cache(), \
             mock.patch("operational.deployment_mode.require_active_scheduler_or_exit", return_value=True), \
             mock.patch.object(dpm, "pb") as mock_pb, \
             mock.patch.object(dpm, "run_daily_postmortem", return_value={
                 "scoreboard": {"tracks": {}}, "investigate": [], "software_bug_candidates": [],
                 "challenger_ideas": []}) as mock_review, \
             mock.patch.object(dpm, "write_report_markdown", return_value=Path("/tmp/fake_report.md")):
            dpm.main()
        mock_review.assert_called_once()


if __name__ == "__main__":
    unittest.main()
