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
    """_SNAPSHOT game: starts 2026-10-06 23:00Z (7 PM ET)."""

    def setUp(self):
        p = mock.patch.object(bb, "_cp_enforced", return_value=True)
        p.start()
        self.addCleanup(p.stop)

    def legs(self, now_et_hour, minute=0, last_update_hours_before=0.0, captured_hours_before=0.0):
        now = dt.datetime(2026, 10, 6, now_et_hour + 4, minute, tzinfo=U)
        upd = iso(now - dt.timedelta(hours=last_update_hours_before))
        return bb._legs_from_payload(_payload(shots_point=1.5, last_update=upd), now - dt.timedelta(hours=captured_hours_before), _SNAPSHOT, now)

    def test_a_fresh_morning_price_is_fresh_and_early_not_provisional(self):
        for l in self.legs(8, 5, last_update_hours_before=0.1, captured_hours_before=0.05):          # 11 h before puck drop, 6 minutes old
            self.assertTrue(l.price_fresh)                       # actual freshness: usable for options, personal adds and (capped) automatic tickets
            self.assertTrue(l.early)
            self.assertFalse(l.provisional)

    def test_the_same_price_hours_later_is_provisional_not_fresh(self):
        for l in self.legs(13, 0, last_update_hours_before=5.0, captured_hours_before=4.9):          # quote 5 h old at 1 PM
            self.assertFalse(l.price_fresh)
            self.assertTrue(l.provisional)
            self.assertFalse(l.early)

    def test_a_price_inside_the_pregame_window_is_fresh_and_not_early(self):
        for l in self.legs(17, 30, last_update_hours_before=0.2):
            self.assertTrue(l.price_fresh)
            self.assertFalse(l.early)
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
        self.assertIn("older than the freshness limit", prov["note"])

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

    def test_a_fresh_early_price_can_be_recorded_but_only_up_to_the_early_cap(self):
        """A fresh 8:30 AM price is a usable price: with a 5-game board all on early prices, exactly EARLY_TICKET_CAP tickets are recorded and the rest wait."""
        early_legs = [dataclasses.replace(l, early=True) for l in board(8, start=self.start, captured=iso(self.now - dt.timedelta(minutes=5)))]
        result, state, conn = self.run_cycle(early_legs)
        self.assertEqual(result["newly_recorded"], dtk.EARLY_TICKET_CAP)
        self.assertEqual(state["slots"]["used"], dtk.EARLY_TICKET_CAP)
        held = [r for r in state["diagnostics"]["selection_audit"]["latest_cycle"]["considered"] if r["status"] == "BLOCKED_RECORDING_WINDOW"]
        self.assertTrue(held and "early-price tickets are taken" in held[0]["reason"])
        self.assertEqual(state["diagnostics"]["recording_policy"]["early_price_cap"], {"cap": dtk.EARLY_TICKET_CAP, "used": 0})

    def test_pregame_window_tickets_are_not_counted_against_the_cap(self):
        legs = board(8, start=self.start, captured=iso(self.now - dt.timedelta(minutes=5)))            # early=False: retrieved inside the window
        result, state, conn = self.run_cycle(legs)
        self.assertEqual(result["newly_recorded"], 5)

    def test_the_cap_counts_early_tickets_already_recorded_today(self):
        early_legs = [dataclasses.replace(l, early=True) for l in board(8, start=self.start, captured=iso(self.now - dt.timedelta(minutes=5)))]
        path, conn = fresh_ledger()
        from tests.test_daily_tickets import _fresh_nhl_conn
        nhl = _fresh_nhl_conn()
        first = dtk.run_cycle(nhl, conn, self.now, collected=collected(early_legs))
        self.assertEqual(first["newly_recorded"], dtk.EARLY_TICKET_CAP)
        again = dtk.run_cycle(nhl, conn, self.now + dt.timedelta(minutes=15), collected=collected(early_legs))
        self.assertEqual(again["newly_recorded"], 0)
        late = [dataclasses.replace(l, early=False) for l in board(8, start=self.start, captured=iso(self.now + dt.timedelta(minutes=10)))]
        third = dtk.run_cycle(nhl, conn, self.now + dt.timedelta(minutes=20), collected=collected(late))
        self.assertEqual(third["newly_recorded"], 5 - dtk.EARLY_TICKET_CAP)                                # the reserved slots go to later prices

    def test_a_provisional_leg_is_never_revalidated_into_a_recording(self):
        legs = [dataclasses.replace(l, provisional=True) for l in board(2, start=self.start, captured=iso(self.now))]
        combo = rmp.select_tickets(legs)["tickets"][0]
        self.assertTrue(any("provisional" in w for w in dtk.revalidate_before_recording(combo, self.now)))


class TestPersonalAddsUseActualFreshness(unittest.TestCase):
    """A fresh morning price is addable to a personal log; an aged one is not. The same revalidation the engine runs on every order."""

    def test_fresh_early_leg_is_available_and_an_aged_provisional_leg_is_not(self):
        from operational import manual_orders
        start = "2026-10-15T23:30:00Z"
        now = dt.datetime(2026, 10, 15, 12, 30, tzinfo=U)
        fresh = [dataclasses.replace(l, early=True) for l in board(2, start=start, captured=iso(now - dt.timedelta(minutes=10)))]
        accepted = {"legs": [{"game_id": l.game_id, "participant_id": l.participant_id, "market_family": l.market_family, "threshold": l.threshold, "side": l.side,
                              "american_price": l.american_price, "participant_name": l.participant_name} for l in fresh], "hit_probability": None}
        ok = manual_orders.revalidate(accepted, fresh, now)
        self.assertIn(ok["status"], ("OK", "CHANGED"))
        aged = [dataclasses.replace(l, price_fresh=False, provisional=True, early=False) for l in fresh]
        gone = manual_orders.revalidate(accepted, aged, now)
        self.assertEqual(gone["status"], "UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
