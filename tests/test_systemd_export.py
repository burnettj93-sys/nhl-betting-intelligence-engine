"""The systemd export copies each launchd schedule exactly and exports only the jobs the watchdog expects (nothing retired)."""
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "deploy"))
import export_systemd_units as ex  # noqa: E402


class TestExport(unittest.TestCase):
    def test_every_expected_job_gets_a_service_and_a_timer_and_a_retired_one_does_not(self):
        from operational import watchdog
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(ex.main(["--out", tmp]), 0)
            names = {p.name for p in Path(tmp).iterdir()}
        for label in watchdog.EXPECTED_JOBS:
            short = label.removeprefix("com.nhlengine.")
            self.assertIn(f"nhl-{short}.service", names, label)
            self.assertIn(f"nhl-{short}.timer", names, label)
        self.assertNotIn("nhl-manual-order-job.timer", names)
        self.assertNotIn("nhl-odds-daily-pull.timer", names)

    def test_intervals_and_calendar_times_are_copied_exactly_and_nothing_runs_late(self):
        defs = ex.definitions()
        kw = dict(release_dir="/r", python="/py", user="u", log_dir="/l")
        trader = ex.units("com.nhlengine.real-parlay-paper-trader", defs["com.nhlengine.real-parlay-paper-trader"], **kw)["nhl-real-parlay-paper-trader.timer"]
        self.assertIn("OnUnitActiveSec=600", trader)
        snap = ex.units("com.nhlengine.moneyline-snapshot", defs["com.nhlengine.moneyline-snapshot"], **kw)["nhl-moneyline-snapshot.timer"]
        for hh in ("08", "13", "17", "20"):
            self.assertIn(f"OnCalendar=*-*-* {hh}:00:00", snap)
        self.assertIn("Persistent=false", snap)                       # a missed window is reported, not run late
        svc = ex.units("com.nhlengine.morning-update", defs["com.nhlengine.morning-update"], **kw)["nhl-morning-update.service"]
        self.assertIn("ExecStart=/py -m operational.morning_update", svc)
        self.assertIn("WorkingDirectory=/r", svc)


if __name__ == "__main__":
    unittest.main()
