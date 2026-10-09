"""The 08:00 ET morning update: ordered, independent steps, always written down; and the older 08:15 pull is retired under the credit plan."""
from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from operational import credit_planner as cp
from operational import live_odds_daily_pull as lop
from operational import morning_update as mu
from operational import price_availability as pa

U = dt.timezone.utc
NOW = dt.datetime(2026, 10, 9, 12, 2, tzinfo=U)       # 8:02 AM ET


class TestMorningUpdate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        p = mock.patch.object(pa.state_paths, "path", side_effect=lambda n, **kw: Path(self.tmp.name) / n)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def run_it(self, *, trader=None, moneyline=None, active=True):
        trader = trader or {"price_refresh": {"capture": {"events_captured": 3, "credits_spent": 3, "slots": {"e1": {"slot": "MORNING"}}, "tomorrow": {"ran": False}}},
                            "stake_result": {"newly_recorded": 0}, "cloud_publish": {"ok": True}}
        with mock.patch("operational.deployment_mode.is_active_scheduler", return_value=active), \
                mock.patch("operational.real_parlay_paper_trader.run", return_value=trader) as t, \
                mock.patch("operational.moneyline_freshness.run_if_due", return_value=moneyline or {"action": "RAN", "ran": True, "credits_spent_this_run": 1}) as m:
            out = mu.run(NOW)
        return out, t, m

    def test_runs_statistics_moneyline_then_one_cycle_and_records_no_ticket_from_the_morning(self):
        out, trader, moneyline = self.run_it()
        self.assertEqual(list(out["steps"]), ["statistics", "moneyline", "cycle"])
        self.assertEqual(out["status"], "DONE")
        self.assertEqual(out["steps"]["cycle"]["newly_recorded"], 0)
        self.assertTrue(out["steps"]["cycle"]["published"])
        self.assertEqual(moneyline.call_args.kwargs.get("label") or moneyline.call_args.args[1], "morning")
        self.assertEqual(mu.read()["day"], "2026-10-09")

    def test_a_failing_step_does_not_stop_the_next_and_is_reported(self):
        with mock.patch("operational.deployment_mode.is_active_scheduler", return_value=True), \
                mock.patch("operational.moneyline_freshness.run_if_due", side_effect=RuntimeError("boom")), \
                mock.patch("operational.real_parlay_paper_trader.run", return_value={"price_refresh": {}, "stake_result": {"newly_recorded": 0}}):
            out = mu.run(NOW)
        self.assertEqual(out["steps"]["moneyline"]["action"], "ERROR")
        self.assertEqual(out["steps"]["cycle"]["status"], "RAN")
        self.assertEqual(out["status"], "PARTIAL")

    def test_a_standby_machine_does_nothing(self):
        out, trader, moneyline = self.run_it(active=False)
        self.assertEqual(out["status"], "SKIPPED")
        trader.assert_not_called()
        moneyline.assert_not_called()

    def test_the_old_0815_pull_is_retired_under_the_credit_plan_and_makes_no_request(self):
        with mock.patch.object(cp, "enforced", return_value=True), mock.patch.object(lop, "client") as client:
            out = lop.run_props(NOW)
        self.assertFalse(out["ran"])
        self.assertIn("RETIRED_BY_CREDIT_PLAN", out["reason"])
        client.get_nhl_events.assert_not_called()

    def test_the_launchd_job_is_expected_by_the_watchdog_and_scheduled_for_8(self):
        import plistlib
        from operational import watchdog as wd
        root = Path(__file__).resolve().parent.parent
        d = plistlib.load(open(root / "deploy" / "launchd" / "com.nhlengine.morning-update.plist", "rb"))
        self.assertEqual(d["StartCalendarInterval"], {"Hour": 8, "Minute": 0})
        self.assertIn("operational.morning_update", d["ProgramArguments"])
        self.assertIn("com.nhlengine.morning-update", wd.EXPECTED_JOBS)


if __name__ == "__main__":
    unittest.main()
