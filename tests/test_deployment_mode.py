"""P0.9 (2026-09-24 hardening block): tests for
operational/deployment_mode.py -- the LOCAL_MODE/PRODUCTION_MODE
safeguard against two schedulers (a local Mac and a future VPS) writing
at the same time."""
from __future__ import annotations

import unittest
from unittest import mock

from operational import deployment_mode as dm


class TestCurrentMode(unittest.TestCase):
    def test_defaults_to_active_when_unset(self):
        with mock.patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("NHL_ENGINE_DEPLOYMENT_MODE", None)
            with mock.patch.object(dm, "_load_dotenv_into_os_environ"):
                self.assertEqual(dm.current_mode(), dm.ACTIVE)

    def test_standby_when_explicitly_set(self):
        with mock.patch.dict("os.environ", {"NHL_ENGINE_DEPLOYMENT_MODE": "STANDBY"}):
            with mock.patch.object(dm, "_load_dotenv_into_os_environ"):
                self.assertEqual(dm.current_mode(), dm.STANDBY)

    def test_case_insensitive(self):
        with mock.patch.dict("os.environ", {"NHL_ENGINE_DEPLOYMENT_MODE": "standby"}):
            with mock.patch.object(dm, "_load_dotenv_into_os_environ"):
                self.assertEqual(dm.current_mode(), dm.STANDBY)

    def test_unrecognized_value_defaults_to_active_not_standby(self):
        """A typo in the env var must fail SAFE (still runs) rather than
        silently going quiet -- STANDBY is opt-in, never accidental."""
        with mock.patch.dict("os.environ", {"NHL_ENGINE_DEPLOYMENT_MODE": "prod"}):
            with mock.patch.object(dm, "_load_dotenv_into_os_environ"):
                self.assertEqual(dm.current_mode(), dm.ACTIVE)


class TestIsActiveScheduler(unittest.TestCase):
    def test_true_when_active(self):
        with mock.patch.object(dm, "current_mode", return_value=dm.ACTIVE):
            self.assertTrue(dm.is_active_scheduler())

    def test_false_when_standby(self):
        with mock.patch.object(dm, "current_mode", return_value=dm.STANDBY):
            self.assertFalse(dm.is_active_scheduler())


class TestRequireActiveSchedulerOrExit(unittest.TestCase):
    def test_returns_true_and_prints_nothing_extra_when_active(self):
        with mock.patch.object(dm, "is_active_scheduler", return_value=True):
            self.assertTrue(dm.require_active_scheduler_or_exit("test_job"))

    def test_returns_false_when_standby(self):
        with mock.patch.object(dm, "is_active_scheduler", return_value=False):
            self.assertFalse(dm.require_active_scheduler_or_exit("test_job"))


class TestJobEntryPointsRespectStandby(unittest.TestCase):
    """Confirms the actual wiring, not just the guard function in
    isolation -- each real scheduled job's entry point must genuinely
    skip real work when this machine is in STANDBY."""

    def test_nhl_sync_main_skips_when_standby(self):
        from operational import nhl_sync
        with mock.patch.object(dm, "is_active_scheduler", return_value=False), \
             mock.patch.object(nhl_sync, "run_nhl_sync") as mock_run, \
             mock.patch("sys.argv", ["nhl_sync"]):
            nhl_sync._main()
        mock_run.assert_not_called()

    def test_live_odds_daily_pull_main_skips_when_standby(self):
        from operational import live_odds_daily_pull as lop
        with mock.patch.object(dm, "is_active_scheduler", return_value=False), \
             mock.patch.object(lop, "run_daily_pull") as mock_run, \
             mock.patch("sys.argv", ["live_odds_daily_pull"]):
            lop._main()
        mock_run.assert_not_called()

    def test_settle_daily_observations_main_skips_when_standby(self):
        from operational import settle_daily_observations as sdo
        with mock.patch.object(dm, "is_active_scheduler", return_value=False), \
             mock.patch.object(sdo, "run_settlement_batch") as mock_run:
            sdo.main()
        mock_run.assert_not_called()

    def test_daily_postmortem_main_skips_when_standby(self):
        from operational import daily_postmortem as dpm
        with mock.patch.object(dm, "is_active_scheduler", return_value=False), \
             mock.patch.object(dpm, "run_daily_postmortem") as mock_run:
            dpm.main()
        mock_run.assert_not_called()

    def test_backup_databases_main_skips_when_standby(self):
        from operational import backup_databases as bd
        with mock.patch.object(dm, "is_active_scheduler", return_value=False), \
             mock.patch.object(bd, "run_all_backups") as mock_run:
            bd.main()
        mock_run.assert_not_called()

    def test_sync_daily_main_skips_when_standby(self):
        """VPS Production Deployment block (2026-09-24), Part 3: real gap
        found -- sync_daily.py (the daily-nhl-sync launchd job's actual
        entry point, 07:00 daily) called nhl_sync.run_nhl_sync() directly
        and had NO deployment-mode guard at all, unlike every other
        scheduled job's entry point. A local Mac put in STANDBY during a
        VPS cutover would still have run this job for real every
        morning, writing to nhl.db in parallel with the VPS -- exactly
        the double-write hazard this module exists to prevent."""
        import sync_daily
        with mock.patch.object(dm, "is_active_scheduler", return_value=False), \
             mock.patch.object(sync_daily, "run") as mock_run:
            code = sync_daily.main()
        mock_run.assert_not_called()
        self.assertEqual(code, 0)

    def test_sync_daily_main_runs_for_real_when_active(self):
        import sync_daily
        with mock.patch.object(dm, "is_active_scheduler", return_value=True), \
             mock.patch.object(sync_daily, "run", return_value=0) as mock_run:
            code = sync_daily.main()
        mock_run.assert_called_once()
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
