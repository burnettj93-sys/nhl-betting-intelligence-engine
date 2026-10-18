"""The activation command: safeguards read the live policy and book, nothing changes without --apply, and --apply approves exactly the named digest, ends the pause once and writes the log."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from deploy import activate_recording as ar
from operational import paper_bankroll as pb
from operational import recording_pause, state_paths, ticket_policy
from operational import watchdog as wd


class TestSafeguards(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, {"NHL_ENGINE_TICKET_POLICY": "proposed"})      # the policy the code ships (tests default to the legacy rules)
        p.start()
        self.addCleanup(p.stop)

    def test_the_live_policy_is_the_one_the_owner_named(self):
        pol = ticket_policy.checked_active()
        self.assertEqual((pol.policy_id, pol.digest()), (ar.EXPECTED_POLICY_ID, ar.EXPECTED_DIGEST))
        for k, v in ar.EXPECTED_PARAMETERS.items():
            self.assertEqual(pol.as_dict()[k], v, k)
        self.assertEqual(pb.PAPER_BET_STAKE, 10.0)

    def test_a_changed_policy_number_fails_the_check_and_nothing_is_activated(self):
        import dataclasses
        from research.real_market_parlay import policy as P
        changed = dataclasses.replace(P.PROPOSED, anchor_cap=0.05)
        with mock.patch.object(ticket_policy, "checked_active", return_value=changed):
            res = ar.checks()
        bad = {c["name"] for c in res if not c["ok"]}
        self.assertIn("policy_is_the_one_the_owner_named", bad)
        self.assertIn("shots_cap_is_the_corrected_plus_0_02", bad)


class TestApply(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, {"NHL_ENGINE_TICKET_POLICY": "proposed"})
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(lambda: state_paths.path(recording_pause.NAME).unlink(missing_ok=True))
        self.addCleanup(lambda: state_paths.path(ar.LOG_NAME).unlink(missing_ok=True))
        self.addCleanup(lambda: state_paths.path(ticket_policy.APPROVAL_NAME).unlink(missing_ok=True))
        recording_pause.pause("owner pause for the test", set_by="test")
        state_paths.path(ar.LOG_NAME).unlink(missing_ok=True)

    def passing(self):
        return mock.patch.object(ar, "checks", return_value=[{"name": "x", "ok": True, "detail": "ok"}])

    def test_without_apply_nothing_changes(self):
        with self.passing(), mock.patch("builtins.print"):
            self.assertEqual(ar.main([]), 0)
        self.assertTrue(recording_pause.is_paused())
        self.assertFalse(ticket_policy.is_approved())
        self.assertFalse(state_paths.path(ar.LOG_NAME).exists())

    def test_apply_approves_the_named_digest_ends_the_pause_once_and_logs_it(self):
        with self.passing(), mock.patch("builtins.print"):
            self.assertEqual(ar.main(["--apply", "--evidence", "tests passed"]), 0)
        self.assertFalse(recording_pause.is_paused())
        self.assertTrue(ticket_policy.is_approved())
        self.assertEqual(ticket_policy.approval()["digest"], ar.EXPECTED_DIGEST)
        lines = state_paths.path(ar.LOG_NAME).read_text().splitlines()
        self.assertEqual(len(lines), 1)
        entry = json.loads(lines[0])
        self.assertEqual(entry["approved_policy_digest"], ar.EXPECTED_DIGEST)
        self.assertEqual(entry["limits"]["tickets_per_eastern_day"], 1)
        self.assertEqual(entry["limits"]["real_money"], "NOT authorised")
        self.assertEqual(entry["evidence"], "tests passed")
        with self.passing(), mock.patch("builtins.print"):
            self.assertEqual(ar.main(["--apply"]), 0)                                   # a second run: already on, nothing more is logged
        self.assertEqual(len(state_paths.path(ar.LOG_NAME).read_text().splitlines()), 1)

    def test_a_failing_check_blocks_activation(self):
        with mock.patch.object(ar, "checks", return_value=[{"name": "reconciliation_agrees_three_ways", "ok": False, "detail": "x"}]), mock.patch("builtins.print"), mock.patch("sys.stderr"):
            self.assertEqual(ar.main(["--apply"]), 1)
        self.assertTrue(recording_pause.is_paused())
        self.assertFalse(state_paths.path(ar.LOG_NAME).exists())


if __name__ == "__main__":
    unittest.main()
