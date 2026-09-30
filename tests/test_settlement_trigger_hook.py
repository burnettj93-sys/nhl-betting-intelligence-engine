"""
Production Gap Closure sprint (2026-09-30): operational/settlement_trigger_hook.py
is what lets a same-day midday/pregame refresh retrigger settlement instead of
waiting for tomorrow's 07:15 clock slot. These tests cover the hook's own
dispatch/locking logic; the actual settlement behavior it delegates to is
covered by tests/test_settle_daily_observations.py.
"""
from __future__ import annotations

import unittest
from unittest import mock

from operational import settlement_trigger_hook as hook


class Test01UnderTestNeverRunsRealSettlement(unittest.TestCase):
    def test_default_test_environment_short_circuits(self):
        # This test file itself runs under unittest -- state_paths.under_test()
        # is True by construction, so the hook must refuse to do real work.
        result = hook.trigger_after("nhl_sync_full")
        self.assertEqual(result["status"], "SKIPPED")
        self.assertEqual(result["reason"], "UNDER_TEST")


class Test02StandbyNeverSettles(unittest.TestCase):
    def test_standby_machine_is_skipped(self):
        with mock.patch("operational.state_paths.under_test", return_value=False), \
             mock.patch("operational.deployment_mode.is_active_scheduler", return_value=False):
            result = hook.trigger_after("nhl_sync_full")
        self.assertEqual(result["status"], "SKIPPED")
        self.assertEqual(result["reason"], "STANDBY")


class Test03LockPreventsConcurrentSettlement(unittest.TestCase):
    def test_lock_already_held_is_reported_not_raised(self):
        import fcntl
        hook.LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(hook.LOCK_PATH, "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with mock.patch("operational.state_paths.under_test", return_value=False), \
                 mock.patch("operational.deployment_mode.is_active_scheduler", return_value=True):
                result = hook.trigger_after("nhl_sync_full")
        self.assertEqual(result["status"], "SKIPPED")
        self.assertEqual(result["reason"], "ANOTHER_SETTLEMENT_RUNNING")


class Test04DispatchesToGenerationAwareSettlement(unittest.TestCase):
    def test_delegates_to_run_if_new_generation_and_never_raises_on_its_own_error(self):
        with mock.patch("operational.state_paths.under_test", return_value=False), \
             mock.patch("operational.deployment_mode.is_active_scheduler", return_value=True), \
             mock.patch("operational.settle_daily_observations.run_if_new_generation",
                        side_effect=RuntimeError("boom")):
            result = hook.trigger_after("nhl_sync_full")
        self.assertEqual(result["status"], "FAILED")
        self.assertIn("RuntimeError", result["reason"])

    def test_successful_settlement_with_candidates_publishes_downstream(self):
        with mock.patch("operational.state_paths.under_test", return_value=False), \
             mock.patch("operational.deployment_mode.is_active_scheduler", return_value=True), \
             mock.patch("operational.settle_daily_observations.run_if_new_generation",
                        return_value={"status": "SUCCESS", "total_candidates": 1, "settled_win": 1,
                                      "settled_loss": 0, "settled_void": 0, "settled_unresolved": 0}), \
             mock.patch("operational.cloud_publish_hook.publish_after",
                        return_value={"status": "SUCCESS"}) as publish:
            result = hook.trigger_after("nhl_midday_schedule_refresh")
        self.assertEqual(result["status"], "SUCCESS")
        publish.assert_called_once_with("settlement")
        self.assertEqual(result["cloud_publish"], {"status": "SUCCESS"})

    def test_skip_with_no_new_generation_never_publishes(self):
        with mock.patch("operational.state_paths.under_test", return_value=False), \
             mock.patch("operational.deployment_mode.is_active_scheduler", return_value=True), \
             mock.patch("operational.settle_daily_observations.run_if_new_generation",
                        return_value={"status": "SKIPPED", "reason": "NO_NEW_GENERATION"}), \
             mock.patch("operational.cloud_publish_hook.publish_after") as publish:
            result = hook.trigger_after("nhl_pregame_targeted_refresh")
        self.assertEqual(result["status"], "SKIPPED")
        publish.assert_not_called()


if __name__ == "__main__":
    unittest.main()
