"""Provisional (morning) recommendations: shown, never recorded; a recorded ticket is re-judged on fresh prices at the moment of recording."""
from __future__ import annotations

import dataclasses
import datetime as dt
import unittest
from unittest import mock

from operational import best_bets as bb
from operational import daily_tickets as dtk
from operational import player_options
from research.real_market_parlay import engine as rmp
from tests.test_best_bets import _SNAPSHOT, _payload
from tests.test_daily_tickets import board, collected, fresh_ledger, leg

U = dt.timezone.utc


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


class TestProvisionalLegs(unittest.TestCase):
    """_SNAPSHOT game: starts 2026-10-06 23:00Z (7 PM ET). Production mode (the credit plan governs): a price is recordable only if retrieved inside the pregame window."""

    def setUp(self):
        p = mock.patch.object(bb, "_cp_enforced", return_value=True)
        p.start()
        self.addCleanup(p.stop)

    def legs(self, now_et_hour, minute=0, last_update_hours_before=0.0, captured_hours_before=0.0):
        now = dt.datetime(2026, 10, 6, now_et_hour + 4, minute, tzinfo=U)
        upd = iso(now - dt.timedelta(hours=last_update_hours_before))
        return bb._legs_from_payload(_payload(shots_point=1.5, last_update=upd), now - dt.timedelta(hours=captured_hours_before), _SNAPSHOT, now)

    def test_a_morning_price_is_provisional_not_fresh_eleven_hours_out(self):
        for l in self.legs(8, 5, last_update_hours_before=0.1, captured_hours_before=0.05):      # fresh by AGE, but retrieved ~11 h before puck drop
            self.assertFalse(l.price_fresh)
            self.assertTrue(l.provisional)
            self.assertEqual(l.freshness_status, "BEFORE_PREGAME_WINDOW")

    def test_without_the_credit_plan_the_legacy_cadence_still_records_early_fresh_prices(self):
        with mock.patch.object(bb, "_cp_enforced", return_value=False):
            for l in self.legs(8, 5, last_update_hours_before=0.1, captured_hours_before=0.05):
                self.assertTrue(l.price_fresh)
                self.assertFalse(l.provisional)

    def test_a_price_inside_the_recording_limit_is_fresh_and_not_provisional(self):
        for l in self.legs(17, 30, last_update_hours_before=0.2):
            self.assertTrue(l.price_fresh)
            self.assertFalse(l.provisional)

    def test_a_price_older_than_the_provisional_limit_is_neither(self):
        for l in self.legs(19, 0, last_update_hours_before=15.0, captured_hours_before=0.0):
            self.assertFalse(l.price_fresh)
            self.assertFalse(l.provisional)

    def test_yesterdays_capture_is_never_provisional(self):
        now = dt.datetime(2026, 10, 6, 12, 30, tzinfo=U)            # 8:30 AM ET
        captured = dt.datetime(2026, 10, 5, 23, 0, tzinfo=U)         # 7:00 PM ET the day before, quote updated just before it
        for l in bb._legs_from_payload(_payload(shots_point=1.5, last_update=iso(captured - dt.timedelta(minutes=5))), captured, _SNAPSHOT, now):
            self.assertFalse(l.provisional)
            self.assertFalse(l.price_fresh)

    def test_missing_or_future_quote_times_are_never_provisional(self):
        now = dt.datetime(2026, 10, 6, 12, 30, tzinfo=U)
        for lu in (None, iso(now + dt.timedelta(hours=1))):
            for l in bb._legs_from_payload(_payload(shots_point=1.5, last_update=lu), now - dt.timedelta(minutes=2), _SNAPSHOT, now):
                self.assertFalse(l.provisional)


class TestProvisionalNeverRecords(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 10, 15, 12, 30, tzinfo=U)     # 8:30 AM ET
        self.stamp = iso(self.now - dt.timedelta(hours=1))
        self.start = "2026-10-15T23:30:00Z"

    def morning_legs(self, n=8):
        return [dataclasses.replace(l, price_fresh=False, provisional=True, freshness_status="STALE_QUOTE") for l in board(n, start=self.start, captured=self.stamp)]

    def run_cycle(self, legs, now=None):
        path, conn = fresh_ledger()
        from tests.test_daily_tickets import _fresh_nhl_conn
        nhl = _fresh_nhl_conn()
        result = dtk.run_cycle(nhl, conn, now or self.now, collected=collected(legs))
        return result, dtk.read_state() or {}, conn

    def test_morning_prices_produce_provisional_tickets_and_record_nothing(self):
        result, state, conn = self.run_cycle(self.morning_legs())
        self.assertEqual(result["newly_recorded"], 0)
        self.assertEqual(len(dtk.recorded_today(conn, "2026-10-15")), 0)
        self.assertEqual(state["slots"]["used"], 0)                      # no slot is consumed
        prov = state["provisional"]
        self.assertTrue(prov["tickets"])
        self.assertLessEqual(len(prov["tickets"]), 5)
        for t in prov["tickets"]:
            self.assertEqual(t["status"], "PROVISIONAL")
            self.assertFalse(t["recorded"])
        self.assertIn("re-fetched shortly before puck drop", prov["note"])

    def test_options_built_from_morning_prices_are_flagged_provisional(self):
        result, state, conn = self.run_cycle(self.morning_legs())
        opts = state["options"]["options"]
        self.assertTrue(opts)
        self.assertTrue(all(o["provisional"] for o in opts))

    def test_fresh_prices_later_the_same_day_record_normally(self):
        fresh = board(8, start=self.start, captured=iso(self.now + dt.timedelta(hours=9)))
        late = self.now + dt.timedelta(hours=9, minutes=5)
        result, state, conn = self.run_cycle(fresh, now=late)
        self.assertEqual(result["newly_recorded"], 5)
        self.assertEqual(state["provisional"]["tickets"], [])

    def test_recording_revalidates_prices_on_the_clock_at_the_moment_of_recording(self):
        legs = board(2, start=self.start, captured=iso(self.now - dt.timedelta(minutes=5)))
        combo = rmp.select_tickets(legs)["tickets"][0]
        self.assertEqual(dtk.revalidate_before_recording(combo, self.now), [])
        later = self.now + dt.timedelta(minutes=200)                     # the same prices, 205 minutes old: past the 150-minute limit
        why = dtk.revalidate_before_recording(combo, later)
        self.assertTrue(why and all("no longer fresh" in w for w in why))
        started = dt.datetime(2026, 10, 15, 23, 45, tzinfo=U)
        self.assertTrue(any("started" in w for w in dtk.revalidate_before_recording(combo, started)))

    def test_a_fresh_looking_price_retrieved_before_the_pregame_window_cannot_be_recorded(self):
        stamp = iso(dt.datetime(2026, 10, 15, 14, 0, tzinfo=U))          # retrieved 9.5 h before the 23:30Z start, 5 minutes old at 14:05Z
        legs = board(2, start=self.start, captured=stamp)
        combo = rmp.select_tickets(legs)["tickets"][0]
        now = dt.datetime(2026, 10, 15, 14, 5, tzinfo=U)
        self.assertEqual(dtk.revalidate_before_recording(combo, now), [])                        # legacy mode: allowed
        with mock.patch.object(bb, "_cp_enforced", return_value=True):
            why = dtk.revalidate_before_recording(combo, now)
        self.assertTrue(why and all("pregame window" in w for w in why))

    def test_a_provisional_leg_is_never_revalidated_into_a_recording(self):
        legs = [dataclasses.replace(l, provisional=True) for l in board(2, start=self.start, captured=iso(self.now))]
        combo = rmp.select_tickets(legs)["tickets"][0]
        self.assertTrue(any("provisional" in w for w in dtk.revalidate_before_recording(combo, self.now)))


if __name__ == "__main__":
    unittest.main()
