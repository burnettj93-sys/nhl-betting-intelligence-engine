"""
Game-Day Moneyline Freshness block (2026-09-29): the ORDINARY (UI-facing) moneyline refresh due-check
and its gated side-effecting wrapper. No network, no Odds API credit, no real state files (every path
is a temp path or a fake) -- see operational/moneyline_freshness.py's module docstring for the design.
"""
from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from operational import moneyline_freshness as mf

D = lambda h, m=0, s=0, day=29: dt.datetime(2026, 9, day, h, m, s, tzinfo=dt.timezone.utc)


# ------------------------------------------------------------------------------------- target_max_age_minutes
class TestTargetMaxAge(unittest.TestCase):
    def test_no_unstarted_game_today_means_no_target(self):
        self.assertIsNone(mf.target_max_age_minutes(D(12), []))

    def test_more_than_4h_before_the_next_game_uses_the_far_target(self):
        self.assertEqual(mf.target_max_age_minutes(D(12), [D(17)]), mf.FAR_TARGET_MAX_AGE_MIN)
        self.assertEqual(mf.FAR_TARGET_MAX_AGE_MIN, 150.0)  # 30 min margin under the real 180-min limit

    def test_within_4h_of_the_next_game_uses_the_near_target(self):
        self.assertEqual(mf.target_max_age_minutes(D(14, 1), [D(17)]), mf.NEAR_TARGET_MAX_AGE_MIN)
        self.assertEqual(mf.NEAR_TARGET_MAX_AGE_MIN, 75.0)  # 15 min margin under the real 90-min limit

    def test_exactly_4h_before_counts_as_within_the_near_window(self):
        self.assertEqual(mf.target_max_age_minutes(D(13), [D(17)]), mf.NEAR_TARGET_MAX_AGE_MIN)

    def test_uses_the_soonest_unstarted_game_when_several_remain(self):
        self.assertEqual(mf.target_max_age_minutes(D(12), [D(22), D(17)]), mf.FAR_TARGET_MAX_AGE_MIN)
        self.assertEqual(mf.target_max_age_minutes(D(14, 1), [D(22), D(17)]), mf.NEAR_TARGET_MAX_AGE_MIN)


# ------------------------------------------------------------------------------------- due()
class TestDue(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "cache.json"

    def _cache(self, generated_at_utc: str | None):
        self.tmp.write_text(json.dumps({"generated_at_utc": generated_at_utc}))

    def test_no_game_day_never_due(self):
        self._cache(None)
        out = mf.due(D(12), unstarted_starts_fn=lambda now: [], cache_path=self.tmp)
        self.assertFalse(out["due"])
        self.assertEqual(out["reason"], "NO_UNSTARTED_GAME_TODAY")

    def test_never_captured_is_immediately_due_on_a_game_day(self):
        out = mf.due(D(12), unstarted_starts_fn=lambda now: [D(17)],
                      cache_path=self.tmp)  # file doesn't exist yet
        self.assertTrue(out["due"])
        self.assertEqual(out["reason"], "NEVER_CAPTURED")

    def test_more_than_4h_out_keeps_max_expected_age_at_or_under_150_minutes(self):
        # captured exactly 150 min ago -> not yet due; one minute later -> due
        self._cache((D(12) - dt.timedelta(minutes=150)).isoformat())
        not_due = mf.due(D(12), unstarted_starts_fn=lambda now: [D(17)], cache_path=self.tmp)
        self.assertFalse(not_due["due"])
        self.assertEqual(not_due["target_max_age_min"], 150.0)
        self._cache((D(12) - dt.timedelta(minutes=151)).isoformat())
        now_due = mf.due(D(12), unstarted_starts_fn=lambda now: [D(17)], cache_path=self.tmp)
        self.assertTrue(now_due["due"])

    def test_within_4h_of_a_game_keeps_max_expected_age_at_or_under_75_minutes(self):
        self._cache((D(14, 1) - dt.timedelta(minutes=75)).isoformat())
        not_due = mf.due(D(14, 1), unstarted_starts_fn=lambda now: [D(17)], cache_path=self.tmp)
        self.assertFalse(not_due["due"])
        self.assertEqual(not_due["target_max_age_min"], 75.0)
        self._cache((D(14, 1) - dt.timedelta(minutes=76)).isoformat())
        now_due = mf.due(D(14, 1), unstarted_starts_fn=lambda now: [D(17)], cache_path=self.tmp)
        self.assertTrue(now_due["due"])


# ------------------------------------------------------------------------------------- run_if_due()
class TestRunIfDue(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.state_path = self.tmp / "state.json"
        self.lock_path = self.tmp / "lock"

    def _run(self, due_result, pull_fn=None, guard_fn=None, active=True):
        return mf.run_if_due(
            D(12), due_fn=lambda now: due_result, pull_fn=pull_fn,
            guard_fn=guard_fn or (lambda now: {"allow": True, "reason": "OK"}),
            state_path=self.state_path, lock_path=self.lock_path, active_fn=lambda: active,
        )

    def test_not_due_makes_no_pull(self):
        pull = mock.Mock()
        out = self._run({"due": False, "reason": "WITHIN_TARGET"}, pull_fn=pull)
        self.assertEqual(out["action"], "NONE")
        self.assertFalse(out["ran"])
        pull.assert_not_called()

    def test_standby_machine_never_pulls_even_if_due(self):
        pull = mock.Mock()
        out = self._run({"due": True, "reason": "NEVER_CAPTURED"}, pull_fn=pull, active=False)
        self.assertEqual(out["action"], "SKIPPED")
        pull.assert_not_called()

    def test_hard_reserve_prevents_the_request(self):
        pull = mock.Mock()
        out = self._run({"due": True, "reason": "NEVER_CAPTURED"}, pull_fn=pull,
                         guard_fn=lambda now: {"allow": False, "reason": "HARD_RESERVE"})
        self.assertEqual(out["action"], "DEFERRED")
        self.assertEqual(out["reason"], "QUOTA_HARD_RESERVE")
        pull.assert_not_called()

    def test_a_due_and_allowed_refresh_calls_the_pull_exactly_once(self):
        pull = mock.Mock(return_value={"ran": True, "credits_spent_this_run": 1, "api_error": None})
        out = self._run({"due": True, "reason": "NEVER_CAPTURED"}, pull_fn=pull)
        self.assertEqual(out["action"], "RAN")
        self.assertTrue(out["ran"])
        self.assertEqual(out["credits_spent_this_run"], 1)
        pull.assert_called_once()

    def test_a_second_instance_holding_the_lock_is_skipped_not_double_spent(self):
        pull = mock.Mock(return_value={"ran": True, "credits_spent_this_run": 1})
        import fcntl
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        held = open(self.lock_path, "w")
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            out = self._run({"due": True, "reason": "NEVER_CAPTURED"}, pull_fn=pull)
        finally:
            held.close()
        self.assertEqual(out["action"], "SKIPPED")
        self.assertEqual(out["reason"], "ANOTHER_INSTANCE_RUNNING")
        pull.assert_not_called()

    def test_retry_too_soon_after_a_recent_attempt_is_skipped(self):
        pull = mock.Mock(return_value={"ran": True, "credits_spent_this_run": 1})
        self.state_path.write_text(json.dumps({"last_attempt_utc": (D(12) - dt.timedelta(minutes=1)).isoformat()}))
        out = self._run({"due": True, "reason": "NEVER_CAPTURED"}, pull_fn=pull)
        self.assertEqual(out["action"], "SKIPPED")
        self.assertEqual(out["reason"], "RETRY_TOO_SOON")
        pull.assert_not_called()

    def test_a_failed_pull_leaves_a_truthful_result_never_faking_success(self):
        pull = mock.Mock(return_value={"ran": False, "api_error": "network error"})
        out = self._run({"due": True, "reason": "NEVER_CAPTURED"}, pull_fn=pull)
        self.assertEqual(out["action"], "RAN")   # the ATTEMPT ran -- its outcome is truthfully False
        self.assertFalse(out["ran"])
        self.assertEqual(out["api_error"], "network error")

    def test_never_raises_on_an_unexpected_exception(self):
        out = self._run({"due": True, "reason": "NEVER_CAPTURED"},
                         pull_fn=mock.Mock(side_effect=RuntimeError("boom")))
        self.assertEqual(out["action"], "ERROR")
        self.assertIn("boom", out["reason"])


# ------------------------------------------------------------------------------------- T-35 non-interference
class TestT35PolicyUnaffected(unittest.TestCase):
    """The ordinary refresh must never gate, delay, or otherwise affect the T-35 decision-policy pull --
    it is a separate call with its own lock/state, made ALONGSIDE moneyline_pregame.run_pregame(), never
    inside it."""

    def test_moneyline_pregame_module_itself_is_untouched_by_this_block(self):
        from operational import moneyline_pregame as mp
        self.assertNotIn("moneyline_freshness", mp.__file__)  # sanity: distinct module
        import inspect
        src = inspect.getsource(mp)
        self.assertNotIn("moneyline_freshness", src)  # run_pregame()'s own logic never imports this module

    def test_the_cli_handler_calls_ordinary_refresh_only_after_the_t35_pull_completes(self):
        from operational import live_odds_daily_pull as lop
        import inspect
        src = inspect.getsource(lop)
        pregame_block = src[src.index('elif args.mode == "moneyline-pregame"'):src.index('elif args.mode == "moneyline"')]
        self.assertLess(pregame_block.index("moneyline_pregame.run_pregame()"),
                         pregame_block.index("moneyline_freshness"))


# ------------------------------------------------------------------------------------- system-level integration
class TestGatedCLIIntegration(unittest.TestCase):
    def test_moneyline_pregame_firing_runs_ordinary_refresh_when_due_and_publishes(self):
        from operational import live_odds_daily_pull as lop
        t35_idle = {"mode": "moneyline-pregame", "ran": False, "status": "IDLE", "reason": "NO_CLUSTER_DUE"}
        with mock.patch("operational.moneyline_pregame.run_pregame", return_value=dict(t35_idle)), \
             mock.patch.object(lop, "_moneyline_downstream"), \
             mock.patch("operational.moneyline_pregame.record_audit", return_value=[]), \
             mock.patch("operational.keep_awake.ensure_holding", return_value={"action": "NONE"}), \
             mock.patch("operational.keep_awake.ensure_guard", return_value={"guard": "NONE"}), \
             mock.patch("operational.moneyline_freshness.run_if_due",
                         return_value={"action": "RAN", "ran": True, "credits_spent_this_run": 1}) as mock_ord, \
             mock.patch("operational.cloud_publish_hook.publish_after", return_value={"status": "SUCCESS"}) as mock_pub, \
             mock.patch("operational.deployment_mode.require_active_scheduler_or_exit", return_value=True), \
             mock.patch("sys.argv", ["lop", "--mode=moneyline-pregame"]), mock.patch("builtins.print") as printed:
            lop._main()
        mock_ord.assert_called_once()
        mock_pub.assert_called_once()   # T-35 was IDLE, but the ordinary refresh ran -> still publishes
        out = json.loads(printed.call_args[0][0])
        self.assertTrue(out["ordinary_refresh"]["ran"])

    def test_moneyline_pregame_firing_does_not_publish_when_neither_t35_nor_ordinary_ran(self):
        from operational import live_odds_daily_pull as lop
        t35_idle = {"mode": "moneyline-pregame", "ran": False, "status": "IDLE", "reason": "NO_CLUSTER_DUE"}
        with mock.patch("operational.moneyline_pregame.run_pregame", return_value=dict(t35_idle)), \
             mock.patch.object(lop, "_moneyline_downstream"), \
             mock.patch("operational.moneyline_pregame.record_audit", return_value=[]), \
             mock.patch("operational.keep_awake.ensure_holding", return_value={"action": "NONE"}), \
             mock.patch("operational.keep_awake.ensure_guard", return_value={"guard": "NONE"}), \
             mock.patch("operational.moneyline_freshness.run_if_due",
                         return_value={"action": "NONE", "ran": False, "reason": "WITHIN_TARGET"}), \
             mock.patch("operational.cloud_publish_hook.publish_after", return_value={"status": "SUCCESS"}) as mock_pub, \
             mock.patch("operational.deployment_mode.require_active_scheduler_or_exit", return_value=True), \
             mock.patch("sys.argv", ["lop", "--mode=moneyline-pregame"]), mock.patch("builtins.print"):
            lop._main()
        mock_pub.assert_not_called()   # zero-credit dashboard-load-equivalent firing -- nothing to publish

    def test_legacy_fixed_moneyline_mode_is_completely_unchanged(self):
        """The pre-existing --mode=moneyline automation-trigger contract (Part 22) is untouched by this
        block -- it still calls run_moneyline_snapshot() directly and unconditionally."""
        from operational import live_odds_daily_pull as lop
        with mock.patch.object(lop, "run_moneyline_snapshot", return_value={"ran": True}) as mock_snap, \
             mock.patch("operational.real_odds_bridge.sync_moneyline_odds_to_snapshots",
                         return_value={"status": "SUCCESS"}) as mock_bridge, \
             mock.patch("operational.real_recommendation_orchestrator.run_real_moneyline_recommendations",
                         return_value={"status": "SUCCESS"}) as mock_orch, \
             mock.patch("operational.deployment_mode.require_active_scheduler_or_exit", return_value=True), \
             mock.patch("sys.argv", ["lop", "--mode", "moneyline"]), mock.patch("builtins.print"):
            lop._main()
        mock_snap.assert_called_once()
        mock_bridge.assert_called_once()
        mock_orch.assert_called_once()


if __name__ == "__main__":
    unittest.main()
