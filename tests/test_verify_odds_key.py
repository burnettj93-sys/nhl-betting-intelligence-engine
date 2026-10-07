"""deploy/verify_odds_key.py: reports credential state without ever printing a key."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import unittest
from pathlib import Path
from unittest import mock

SPEC = importlib.util.spec_from_file_location("verify_odds_key", Path(__file__).resolve().parent.parent / "deploy" / "verify_odds_key.py")
vk = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vk)

NEW, OLD = "a" * 32, "b" * 32


def run(configured, exposed, probe_results):
    out = io.StringIO()
    with mock.patch.object(vk, "configured_key", return_value=configured), \
         mock.patch.object(vk, "exposed_candidates", return_value=set(exposed)), \
         mock.patch.object(vk, "probe", side_effect=lambda k: probe_results[k]), \
         contextlib.redirect_stdout(out):
        code = vk.main()
    return code, out.getvalue()


class TestVerifyOddsKey(unittest.TestCase):
    def test_exposed_key_still_active_is_not_resolved(self):
        code, text = run(OLD, [OLD], {OLD: {"status": 200, "requests_remaining": "300"}})
        self.assertEqual(code, 1)
        self.assertIn("STILL ACTIVE", text)
        self.assertIn("NOT RESOLVED", text)

    def test_rotation_verified_only_when_new_works_and_old_is_rejected(self):
        code, text = run(NEW, [OLD], {NEW: {"status": 200, "requests_remaining": "500"}, OLD: {"status": 401}})
        self.assertEqual(code, 0)
        self.assertIn("rotation verified", text)

    def test_a_new_key_that_fails_or_an_inconclusive_probe_is_not_resolved(self):
        self.assertEqual(run(NEW, [OLD], {NEW: {"status": 401}, OLD: {"status": 401}})[0], 1)
        self.assertEqual(run(NEW, [OLD], {NEW: {"status": 200}, OLD: {"status": None}})[0], 1)

    def test_no_key_value_is_ever_printed(self):
        _code, text = run(NEW, [OLD], {NEW: {"status": 200}, OLD: {"status": 401}})
        self.assertNotIn(NEW, text)
        self.assertNotIn(OLD, text)
        self.assertIn(vk.fingerprint(NEW), text)

    def test_candidates_come_only_from_secret_looking_lines_and_skip_the_dummy(self):
        text = f'x = "{OLD}"\nsecret = "{NEW}"\nprefix "{"deadbeef" * 4}" suffix\n'
        with mock.patch.object(vk.subprocess, "run", return_value=mock.Mock(stdout=text)):
            self.assertEqual(vk.exposed_candidates(), {NEW})


if __name__ == "__main__":
    unittest.main()
