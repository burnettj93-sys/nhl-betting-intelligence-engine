"""The credit-cost audit compares the provider's reported charge with the cost model, and the account counter with the sum of charges."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from deploy import audit_credit_costs as A


def call(d, name, endpoint, markets, charged, used, response, at="2026-10-09T12:00:00Z"):
    (d / f"{name}.json").write_text(json.dumps({"meta": {"endpoint": endpoint, "market_filter": markets, "requests_last_header": str(charged), "requests_used_header": str(used),
                                                        "retrieved_at_utc": at}, "response": response}))


def posted(*keys):
    return {"commence_time": "2026-10-09T23:00:00Z", "bookmakers": [{"key": "draftkings", "markets": [{"key": k, "outcomes": [{"name": "Over"}]} for k in keys]}]}


class TestAudit(unittest.TestCase):
    def run_audit(self, setup):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            setup(d)
            with mock.patch("operational.best_bets._archive_dir", return_value=d):
                return A.run()

    def test_props_cost_equals_markets_returned_and_a_mismatch_is_listed(self):
        def setup(d):
            call(d, "a", "/sports/x/events/e1/odds", "m1,m2,m3", 2, 2, posted("m1", "m2"), "2026-10-09T12:00:00Z")
            call(d, "b", "/sports/x/events/e2/odds", "m1,m2", 0, 2, {"bookmakers": []}, "2026-10-09T12:01:00Z")
            call(d, "c", "/sports/x/events/e3/odds", "m1,m2", 1, 4, posted("m1", "m2"), "2026-10-09T12:02:00Z")      # charged 1 but two markets returned
        r = self.run_audit(setup)
        k = r["by_kind"]["event_props"]
        self.assertEqual((k["calls"], k["model_matches_charge"]), (3, 2))
        self.assertEqual(k["mismatches"][0]["charged"], 1)
        self.assertEqual(k["mismatches"][0]["model"], 2)

    def test_events_listing_and_league_moneyline(self):
        def setup(d):
            call(d, "a", "/sports/x/events", "", 0, 5, [], "2026-10-09T12:00:00Z")
            call(d, "b", "/sports/x/odds", "h2h", 1, 6, [], "2026-10-09T12:01:00Z")
        r = self.run_audit(setup)
        self.assertEqual(r["by_kind"]["events_listing"]["model_matches_charge"], 1)
        self.assertEqual(r["by_kind"]["league_moneyline"]["model_matches_charge"], 1)

    def test_counter_reconciliation_and_reset_detection(self):
        def setup(d):
            call(d, "a", "/sports/x/odds", "h2h", 1, 400, [], "2026-09-30T12:00:00Z")
            call(d, "b", "/sports/x/odds", "h2h", 1, 1, [], "2026-10-01T12:00:00Z")                       # the counter fell: a reset
            call(d, "c", "/sports/x/events/e/odds", "m1", 1, 2, posted("m1"), "2026-10-01T12:05:00Z")
        r = self.run_audit(setup)
        cy = r["cycle_reconciliation"]
        self.assertEqual((cy["counter_now"], cy["sum_of_header_charges"]), (2, 2))
        self.assertTrue(cy["every_credit_accounted"])

    def test_posted_markets_by_hours_before_puck_drop(self):
        def setup(d):
            call(d, "a", "/sports/x/events/e/odds", "player_shots_on_goal_alternate,player_points", 1, 1, posted("player_shots_on_goal_alternate"), "2026-10-09T13:00:00Z")   # 10 h before
        r = self.run_audit(setup)
        b = r["posted_by_hours_before_puck_drop"]["6-24h before"]
        self.assertEqual((b["markets_requested"], b["markets_posted"]), (2, 1))


if __name__ == "__main__":
    unittest.main()
