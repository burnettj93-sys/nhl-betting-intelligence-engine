"""The balance the spending guard believes: newest provider header minus every charge recorded after it, never counted twice."""
from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from operational import credit_planner as cp
from operational import odds_quota as q

U = dt.timezone.utc


def t(h, m=0, s=0, day=17):
    return dt.datetime(2026, 10, day, h, m, s, tzinfo=U)


class Balance(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        for p in (mock.patch.object(cp.state_paths, "path", side_effect=lambda name, **kw: d / name),):
            p.start()
            self.addCleanup(p.stop)
        self.cache = {"last_updated_utc": "2026-10-17T23:21:42.465434+00:00", "credits_remaining": 191}
        p = mock.patch("operational.system_health.odds_collection_status", side_effect=lambda: self.cache)
        p.start()
        self.addCleanup(p.stop)
        self.now = t(23, 59)

    def detail(self, now=None):
        return q.balance_detail(now or self.now)

    def test_a_charge_after_the_cached_header_is_deducted_tonights_case(self):
        for _ in range(4):
            cp.record(cp.PROPS, 1, t(23, 30))                                  # four settled credits, no header recorded
        d = self.detail()
        self.assertEqual((d["remaining"], d["baseline_value"], d["deducted"]), (187, 191, 4.0))

    def test_a_charge_stamped_at_or_before_the_baseline_is_already_inside_it(self):
        cp.record(cp.MONEYLINE_DECISION, 1, t(23, 21, 42))                    # the pull that produced the cached 191
        self.assertEqual(self.detail()["remaining"], 191)

    def test_the_next_response_replaces_every_deduction_without_double_counting(self):
        cp.record(cp.PROPS, 1, t(23, 30))
        cp.record(cp.PROPS, 1, t(23, 30))
        self.assertEqual(self.detail()["remaining"], 189)
        cp.record(cp.GOALS, 1, t(23, 50), remaining_header="188")               # the next call reports 188: the two earlier charges were already counted by the provider
        d = self.detail()
        self.assertEqual((d["remaining"], d["deducted"], d["source"]), (188, 0.0, "ledger header"))
        cp.record(cp.GOALS, 1, t(23, 55))                                      # a later charge with no header is deducted from the new baseline
        self.assertEqual(self.detail()["remaining"], 187)

    def test_string_headers_are_parsed_and_bad_ones_ignored(self):
        cp.record(cp.PROPS, 1, t(23, 30), remaining_header="190")
        cp.record(cp.PROPS, 1, t(23, 40), remaining_header="nan")
        cp.record(cp.PROPS, 1, t(23, 45), remaining_header="-5")
        d = self.detail()
        self.assertEqual(d["baseline_value"], 190)
        self.assertEqual(d["remaining"], 188)                                  # the two unreadable rows still cost a credit each, after the baseline

    def test_responses_that_arrive_out_of_order_use_the_lower_balance(self):
        cp.record(cp.PROPS, 1, t(23, 40, 0), remaining_header="189")           # the later-finishing call reports 189 ...
        cp.record(cp.PROPS, 1, t(23, 40, 5), remaining_header="190")           # ... but a row stamped later carries 190 (older response)
        self.assertEqual(self.detail()["remaining"], 189)

    def test_a_much_older_higher_header_does_not_beat_a_newer_lower_one(self):
        cp.record(cp.PROPS, 1, t(20, 0), remaining_header="195")
        self.assertEqual(self.detail()["baseline_value"], 191)                 # the newer cache wins; the 20:00 value is outside the concurrent window

    def test_future_dated_rows_never_count(self):
        cp.record(cp.PROPS, 1, dt.datetime(2099, 1, 1, tzinfo=U), remaining_header="3")
        cp.record(cp.PROPS, 5, dt.datetime(2099, 1, 1, tzinfo=U))
        self.assertEqual(self.detail()["remaining"], 191)

    def test_nothing_recorded_means_unknown_not_a_guess(self):
        self.cache = {"last_updated_utc": None, "credits_remaining": None}
        self.assertIsNone(self.detail()["remaining"])

    def test_the_guard_refuses_what_only_the_stale_header_could_afford_and_pending_reservations_count(self):
        for _ in range(4):
            cp.record(cp.PROPS, 1, t(23, 30))
        plan = {"allowance": {cp.PROPS: 500.0}}
        est = self.detail()["remaining"]
        self.assertEqual(est, 187)
        refused = cp.reserve(cp.PROPS, 170, self.now, plan=plan, remaining=est, key="fresh")                   # 187-170 = 17 < the 20-credit reserve
        self.assertEqual(refused["reason"], "HARD_RESERVE")
        self.assertTrue(cp.reserve(cp.PROPS, 170, self.now, plan=plan, remaining=191, key="stale")["allow"])    # the stale 191 would have allowed it: 191-170 = 21
        second = cp.reserve(cp.PROPS, 5, self.now, plan=plan, remaining=est, key="second")                     # a concurrent job: 187-170 pending-5 = 12
        self.assertEqual(second["reason"], "HARD_RESERVE")
