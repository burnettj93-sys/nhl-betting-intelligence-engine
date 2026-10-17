"""The league-wide moneyline + standard-spread pull: it is due on the day's timeline, asks for both markets in ONE call, reserves before and settles to the actual charge, and stores the spread."""
from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from operational import credit_planner as cp
from operational import live_odds_daily_pull as lop
from operational import moneyline_freshness as mf
from tests.test_live_odds_daily_pull import EVENT_B, _fake_response

U = dt.timezone.utc
ET = dt.timezone(dt.timedelta(hours=-4))
NOW = dt.datetime(2026, 9, 29, 15, 0, tzinfo=U)                 # 11:00 AM ET: inside the first wave's pull window for a 12:30 PM game


def at(h, m=0, day=29):
    return dt.datetime(2026, 9, day, h, m, tzinfo=ET).astimezone(U)


def event(spreads=True, home_point=-1.5):
    markets = [{"key": "h2h", "last_update": "2026-09-29T14:55:00Z", "outcomes": [{"name": "Florida Panthers", "price": -150}, {"name": "Carolina Hurricanes", "price": 130}]}]
    if spreads:
        markets.append({"key": "spreads", "last_update": "2026-09-29T14:56:00Z", "outcomes": [{"name": "Florida Panthers", "price": 190, "point": home_point},
                                                                                              {"name": "Carolina Hurricanes", "price": -235, "point": -home_point}]})
    return {"id": "evt-b", "home_team": "Florida Panthers", "away_team": "Carolina Hurricanes", "commence_time": "2026-09-29T23:00:00Z",
            "bookmakers": [{"key": "draftkings", "last_update": "2026-09-29T14:55:00Z", "markets": markets}]}


class Isolated(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        self.dir = d
        for p in (mock.patch.object(cp.state_paths, "path", side_effect=lambda name, **kw: d / name),
                  mock.patch.object(cp.odds_quota, "reset_status", return_value={"status": "OWNER_VERIFICATION_REQUIRED", "reset_day": 1, "assumed": True, "invalid": False}),
                  mock.patch.object(cp.odds_quota, "latest_remaining", return_value=100),
                  mock.patch.object(cp, "enforced", return_value=True)):
            p.start()
            self.addCleanup(p.stop)
        starts = {"1": at(12, 30), "2": at(19)}
        self.plan = cp.build_day_plan(at(8), "2026-09-29", starts, remaining=100, counts_ahead={"2026-09-30": 4})
        cp._persist(self.plan)

    def pull(self, event_payload, cost="2", now=NOW):
        events_resp = _fake_response(json_data=[EVENT_B], headers={"x-requests-remaining": "99", "x-requests-last": "0"})
        odds_resp = _fake_response(json_data=[event_payload], headers={"x-requests-remaining": str(100 - int(cost)), "x-requests-last": cost})
        cache = self.dir / "ml_cache.json"
        with mock.patch("operational.live_odds_daily_pull.MONEYLINE_CACHE_PATH", cache), mock.patch("operational.live_odds_daily_pull._now_utc", return_value=now), \
                mock.patch("research.live_sog_pricing.archive.ARCHIVE_DIR", self.dir / "arch"), mock.patch.object(lop.client, "get_the_odds_api_key", return_value="fake"), \
                mock.patch("requests.get", side_effect=[events_resp, odds_resp]) as get:
            res = lop.run_moneyline_snapshot("morning")
        return res, get, cache


class TestThePull(Isolated):
    def test_one_call_asks_for_moneylines_and_spreads_and_stores_the_standard_line(self):
        res, get, cache = self.pull(event())
        self.assertEqual(get.call_args_list[1].kwargs["params"]["markets"], "h2h,spreads")
        row = json.loads(cache.read_text())["rows"][0]
        self.assertEqual(row["spreads"], {"status": "POSTED", "home_point": -1.5, "home_price": 190, "away_point": 1.5, "away_price": -235, "last_update": "2026-09-29T14:56:00Z"})
        self.assertEqual(row["home_price"], -150)                                          # the moneyline is unchanged

    def test_the_reservation_is_settled_to_the_actual_charge_and_the_cost_becomes_observed(self):
        res, _, _ = self.pull(event(), cost="1")                                          # the provider charged ONE credit for both markets
        self.assertEqual(res["credits_spent_this_run"], 1)
        row = cp.read_ledger("2026-09-29")[-1]
        self.assertEqual((row["class"], row["credits"], row["planned"]), (cp.MONEYLINE_UI, 1.0, 2.0))
        self.assertEqual(cp.pending_reserved(NOW), {})
        self.assertEqual(cp.observed_league_cost(), (1, True))

    def test_a_game_without_spreads_or_with_only_other_lines_is_labelled_not_invented(self):
        res, _, cache = self.pull(event(spreads=False), cost="1")
        self.assertEqual(json.loads(cache.read_text())["rows"][0]["spreads"]["status"], "NOT_POSTED")
        res, _, cache = self.pull(event(home_point=-2.5), cost="2")
        self.assertEqual(json.loads(cache.read_text())["rows"][0]["spreads"]["status"], "NO_STANDARD_LINE")

    def test_an_unfunded_pull_makes_no_paid_call(self):
        with mock.patch.object(cp.odds_quota, "latest_remaining", return_value=20):         # at the hard reserve
            events_resp = _fake_response(json_data=[EVENT_B], headers={"x-requests-remaining": "20", "x-requests-last": "0"})
            with mock.patch("operational.live_odds_daily_pull.MONEYLINE_CACHE_PATH", self.dir / "c.json"), mock.patch("operational.live_odds_daily_pull._now_utc", return_value=NOW), \
                    mock.patch("research.live_sog_pricing.archive.ARCHIVE_DIR", self.dir / "arch"), mock.patch.object(lop.client, "get_the_odds_api_key", return_value="fake"), \
                    mock.patch("requests.get", side_effect=[events_resp]) as get:
                res = lop.run_moneyline_snapshot("morning")
        self.assertEqual(get.call_count, 1)                                                 # only the free events list
        self.assertIn("NOT_FUNDED", res["reason"])


class TestWhenAPullIsDue(Isolated):
    def test_the_morning_pull_the_one_shared_pregame_pull_and_no_unfunded_wave_pull(self):
        self.assertEqual(mf.plan_due(at(7, 50))["due"], False)
        self.assertEqual(mf.plan_due(at(8, 5))["kind"], "MORNING")
        cp.record(cp.MONEYLINE_UI, 2, at(8, 5), markets="h2h,spreads")
        self.assertFalse(mf.plan_due(at(10, 0))["due"])
        d = mf.plan_due(at(11, 5))                                                          # the first wave (12:30) targets 11:00
        self.assertEqual((d["due"], d["kind"]), (True, "PREGAME"))
        cp.record(cp.MONEYLINE_UI, 2, at(11, 6), markets="h2h,spreads")
        self.assertFalse(mf.plan_due(at(11, 30))["due"])                                    # one shared pull serves the wave
        d2 = mf.plan_due(at(17, 35))                                                        # the evening wave's target
        if self.plan["league_pulls"]["extra_for_later_waves"] > 0:
            self.assertEqual((d2["due"], d2["kind"]), (True, "WAVE"))                       # funded from explicit surplus
        else:
            self.assertFalse(d2["due"])
            self.assertIn("NOT_FUNDED", d2["reason"])

    def test_an_unfunded_wave_pull_is_not_due(self):
        plan = {**self.plan, "league_pulls": {**self.plan["league_pulls"], "extra_for_later_waves": 0}}
        cp._persist(plan)
        cp.record(cp.MONEYLINE_UI, 2, at(8, 5), markets="h2h,spreads")
        cp.record(cp.MONEYLINE_UI, 2, at(11, 6), markets="h2h,spreads")
        d = mf.plan_due(at(17, 35))
        self.assertFalse(d["due"])
        self.assertIn("NOT_FUNDED", d["reason"])

    def test_with_no_plan_nothing_is_due(self):
        cp._plan_path().unlink()
        self.assertEqual(mf.plan_due(at(9)), {"due": False, "reason": "NO_PLAN_FOR_TODAY", "target_max_age_min": None, "age_min": None})


if __name__ == "__main__":
    unittest.main()
