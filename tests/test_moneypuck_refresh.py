"""The afternoon MoneyPuck re-check: it asks the source again, reports what changed, never raises into the scheduler, and is a loaded job the watchdog expects."""
from __future__ import annotations

import datetime as dt
import plistlib
import unittest
from pathlib import Path

from operational import moneypuck_refresh as mr
from operational import watchdog as wd

REPO = Path(__file__).resolve().parent.parent


class TestRefresh(unittest.TestCase):
    def test_it_reports_which_datasets_the_source_had_updated(self):
        rec = []
        out = mr.run(sync=lambda season, ds: {"datasets": {"skater": {"status": "UPDATED"}, "goalie": {"status": "UPDATED"}, "team": {"status": "NO_CHANGE"}}},
                     today=dt.date(2026, 10, 10), record=lambda c, s: rec.append((c, s)))
        self.assertEqual((out["status"], out["season"], out["updated"]), ("SUCCESS", 2026, ["goalie", "skater"]))
        self.assertEqual(rec[0][0], "moneypuck_afternoon_refresh")

    def test_a_source_that_is_down_is_a_reported_failure_not_an_exception(self):
        def boom(season, ds):
            raise ConnectionError("mirror unreachable")
        out = mr.run(sync=boom, today=dt.date(2026, 10, 10), record=lambda c, s: None)
        self.assertEqual(out["status"], "FAILED")
        self.assertIn("mirror unreachable", out["error"])

    def test_the_season_rolls_over_in_july(self):
        self.assertEqual((mr.season_start_year(dt.date(2026, 6, 30)), mr.season_start_year(dt.date(2026, 7, 1))), (2025, 2026))

    def test_a_recording_failure_does_not_break_the_run(self):
        def bad(c, s):
            raise OSError("disk")
        self.assertEqual(mr.run(sync=lambda s, d: {"datasets": {}}, today=dt.date(2026, 10, 10), record=bad)["status"], "SUCCESS")


class TestTheJob(unittest.TestCase):
    def test_the_launchd_job_runs_twice_a_day_and_the_watchdog_expects_it(self):
        plist = plistlib.loads((REPO / "deploy" / "launchd" / "com.nhlengine.moneypuck-refresh.plist").read_bytes())
        self.assertEqual(plist["Label"], "com.nhlengine.moneypuck-refresh")
        self.assertEqual([(d["Hour"], d["Minute"]) for d in plist["StartCalendarInterval"]], [(13, 30), (17, 30)])
        self.assertIn("com.nhlengine.moneypuck-refresh", wd.EXPECTED_JOBS)


if __name__ == "__main__":
    unittest.main()
