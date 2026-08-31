"""
Preseason Operational Readiness Closure sprint (2026-08-30), Track 2 Part 7:
regression guard against test runs mutating the real Odds API evidence
directory (data/raw/the_odds_api/live/).

Real bug found and fixed this sprint: research/live_sog_pricing/
archive.py::archive_result() and operational/live_odds_daily_pull.py::
_credits_spent_since() both had a mutable-default-bound-at-import-time
signature (`out_dir: Path = ARCHIVE_DIR` / `archive_dir: Path =
ARCHIVE_DIR`), which meant mock.patch("...ARCHIVE_DIR", tmp_dir) in
tests/test_live_odds_daily_pull.py had NO EFFECT on any call site that
didn't also pass the directory explicitly -- every real `run_daily_pull()`
call in that test suite was silently writing synthetic evt-a/evt-b
fixtures into the REAL evidence directory. 51 such files were found
(cleaned this sprint; 37 genuine captures retained). Both functions now
resolve ARCHIVE_DIR fresh, by name, inside the function body, which this
test file verifies stays true by literally running the full test suite
for the two modules involved and checking the real directory is
byte-for-byte unchanged.
"""
from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_EVIDENCE_DIR = REPO_ROOT / "data" / "raw" / "the_odds_api" / "live"


def _snapshot(directory: Path) -> set[str]:
    if not directory.exists():
        return set()
    return {p.name for p in directory.iterdir() if p.is_file()}


class Test01ArchiveResultDefaultIsNotBoundAtImportTime(unittest.TestCase):
    """Direct regression for the exact root-cause bug: a mutable default
    argument bound to a module constant at function-definition time is
    immune to mock.patch on that constant. Verifies the FIX (a None
    default resolved fresh inside the function body), not just the
    symptom."""

    def test_archive_result_out_dir_default_is_none_not_a_bound_path(self):
        from research.live_sog_pricing import archive
        import inspect
        sig = inspect.signature(archive.archive_result)
        self.assertIsNone(sig.parameters["out_dir"].default,
                           "archive_result's out_dir default must be None (resolved fresh "
                           "inside the function body), never a Path bound at import time -- "
                           "a bound default silently defeats mock.patch on ARCHIVE_DIR")

    def test_credits_spent_since_archive_dir_default_is_none_not_a_bound_path(self):
        from operational import live_odds_daily_pull as lop
        import inspect
        sig = inspect.signature(lop._credits_spent_since)
        self.assertIsNone(sig.parameters["archive_dir"].default,
                           "_credits_spent_since's archive_dir default must be None, same reason")

    def test_mock_patching_archive_dir_actually_redirects_a_real_write(self):
        import tempfile
        from unittest import mock
        from research.live_sog_pricing import archive, client
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("research.live_sog_pricing.archive.ARCHIVE_DIR", Path(tmp)):
                result = client.ApiResult(ok=True, status_code=200, data={"x": 1}, error=None,
                                           endpoint="/sports", retrieved_at_utc="2026-01-01T00:00:00Z")
                path = archive.archive_result(result, event_id=None, market_filter=None,
                                               bookmaker_filter=None)
            self.assertTrue(str(path).startswith(tmp),
                             f"archive_result wrote to {path}, not under the patched tmp dir {tmp} -- "
                             f"the mock.patch had no effect (the exact bug this sprint fixed)")


class Test02FullRelevantTestSuiteNeverTouchesRealEvidenceDir(unittest.TestCase):
    """The actual end-to-end guard: running every test in the two modules
    that write archive data must leave the real evidence directory
    byte-for-byte (filename-for-filename) identical."""

    def test_running_live_odds_and_live_sog_pricing_suites_leaves_real_dir_unchanged(self):
        import io
        import unittest as ut

        before = _snapshot(REAL_EVIDENCE_DIR)
        loader = ut.TestLoader()
        suite = ut.TestSuite()
        suite.addTests(loader.loadTestsFromName("tests.test_live_odds_daily_pull"))
        suite.addTests(loader.loadTestsFromName("tests.test_live_sog_pricing"))
        runner = ut.TextTestRunner(stream=io.StringIO(), verbosity=0)
        result = runner.run(suite)
        after = _snapshot(REAL_EVIDENCE_DIR)

        self.assertTrue(result.wasSuccessful(), "the inner test suites must themselves pass")
        self.assertEqual(before, after,
                          f"real evidence directory changed during test run -- added: "
                          f"{after - before}, removed: {before - after}")


class Test03NoKnownSyntheticFixtureIdsRemainInRealDir(unittest.TestCase):
    """Direct content-level check (not just a file-count check) that no
    file in the real evidence directory carries the known synthetic
    fixture markers (evt-a/evt-b event ids, or placeholder team names
    'A'/'H') used by tests/test_live_odds_daily_pull.py's EVENT_A/EVENT_B."""

    def test_no_evt_a_or_evt_b_ids_in_real_evidence_dir(self):
        import json
        for path in REAL_EVIDENCE_DIR.glob("*.json"):
            with open(path) as f:
                obj = json.load(f)
            meta = obj.get("meta", {})
            self.assertNotIn(meta.get("event_id"), ("evt-a", "evt-b"),
                              f"{path.name} carries a known synthetic test fixture event_id")
            resp = obj.get("response")
            if isinstance(resp, dict):
                self.assertFalse(resp.get("home_team") in ("A", "H") and resp.get("away_team") in ("A", "H"),
                                  f"{path.name} carries known synthetic placeholder team names")
            if isinstance(resp, list):
                for ev in resp:
                    if isinstance(ev, dict):
                        self.assertNotIn(ev.get("id"), ("evt-a", "evt-b"),
                                          f"{path.name} lists a known synthetic fixture event id")


if __name__ == "__main__":
    unittest.main()
