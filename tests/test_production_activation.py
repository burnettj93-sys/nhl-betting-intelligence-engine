"""Production Activation block (2026-09-25): odds-freshness analysis, cloud preflight (fast checks), and the
scheduler-facing publish gating. Read-only tests; no network, no Odds API credit."""
from __future__ import annotations

import datetime as dt
import unittest

from operational import cloud_preflight as pf
from operational import odds_freshness_analysis as ofa

D = lambda h, m=0, day=26: dt.datetime(2026, 9, day, h, m, tzinfo=dt.timezone.utc)


class TestDecisionPolicyWindow(unittest.TestCase):
    """The real pipeline prices at start-30 min and accepts a quote <= 10 min older than that."""

    def test_pull_must_land_between_start_minus_40_and_start_minus_30(self):
        start = D(23, 0)
        for pull, expected in ((D(22, 20), True), (D(22, 30), True), (D(22, 25), True),
                               (D(22, 19), False), (D(22, 31), False), (D(17, 0), False)):
            self.assertEqual(ofa.decision_policy_eligible([pull], start), expected, pull)

    def test_the_four_fixed_daily_pulls_almost_never_satisfy_it(self):
        starts = [D(23, 0), D(23, 30), D(0, 0, 27), D(2, 0, 27), D(19, 0), D(1, 0, 27)]
        pulls = ofa.pulls_for([dt.date(2026, 9, 25), dt.date(2026, 9, 26), dt.date(2026, 9, 27)])
        self.assertLessEqual(ofa.evaluate(starts, pulls)["decision_policy_quote_available_pct"], 20.0)

    def test_targeted_pulls_cover_the_games_and_share_a_call_within_a_cluster(self):
        starts = [D(23, 0), D(23, 5), D(23, 8), D(2, 0, 27)]
        base = ofa.pulls_for([dt.date(2026, 9, 26)])
        with_targeted = ofa.add_targeted_pulls(starts, base)
        self.assertEqual(ofa.evaluate(starts, with_targeted)["decision_policy_quote_available_pct"], 100.0)
        added = [p for p in with_targeted if p not in base]
        self.assertLessEqual(len(added), 3)                       # the 23:00/23:05/23:08 cluster shares one credit

    def test_display_freshness_metrics_use_the_shared_market_rule(self):
        # a single 21:00Z pull, game at 22:30Z: CURRENT at start-30 (age 60 min <= 90)
        start = D(22, 30)
        res = ofa.evaluate([start], [D(21, 0)])
        self.assertEqual(res["current_at_T-30_pct"], 100.0)
        # same pull, game at 23:30Z: age at start-30 = 120 min > 90 -> STALE
        self.assertEqual(ofa.evaluate([D(23, 30)], [D(21, 0)])["current_at_T-30_pct"], 0.0)


class TestCloudPreflightFastChecks(unittest.TestCase):
    def test_entrypoint_and_requirements_pass_and_the_streamlit_app_settings_are_recorded(self):
        rows = {r["check"]: r for r in pf.check_entrypoint_and_requirements()}
        self.assertEqual(rows["entrypoint file"]["status"], pf.PASS)
        self.assertEqual(rows["Streamlit repo / branch / main file"]["status"], pf.PASS)
        self.assertEqual(rows["dashboard/requirements.txt (Cloud dependency file)"]["status"], pf.PASS)

    def test_cloud_pages_import_no_scheduler_api_client_or_yahoo(self):
        rows = pf.check_page_registry()
        self.assertEqual(rows[0]["status"], pf.PASS, rows[0]["detail"])

    def test_overall_is_fail_when_any_check_fails(self):
        from unittest import mock
        fail = [pf._row("x", pf.FAIL, "boom")]
        with mock.patch.object(pf, "check_entrypoint_and_requirements", return_value=fail), \
             mock.patch.object(pf, "check_mode_and_auth", return_value=[]), \
             mock.patch.object(pf, "check_page_registry", return_value=[]), \
             mock.patch.object(pf, "check_render_probe", return_value=[]):
            self.assertEqual(pf.run(offline=True)["overall"], "FAIL")


if __name__ == "__main__":
    unittest.main()
