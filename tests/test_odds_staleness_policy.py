"""
v2.1 spec item 15: odds staleness must scale with time-to-puck-drop
rather than use one static window -- a quote that's fine a day out is
dangerously stale 10 minutes before puck drop. Unit-tests
pricing/odds_math.py's pure `dynamic_max_staleness_minutes` /
`hours_between` against config.ODDS_STALENESS_TIERS, then proves the
policy is actually wired into pricing/engine.py::evaluate_moneyline_for_game
end to end at several different horizons: a quote just inside the
horizon's allowance is accepted, one just outside is rejected as
DATA_UNAVAILABLE.
"""
import datetime as dt
import unittest

import config
from models.combined_model import CombinedMoneylineModel
from pricing import engine as pricing_engine
from pricing import odds_math
from tests.helpers import Fixture, make_test_db


def _shift(iso: str, minutes: float) -> str:
    return (dt.datetime.fromisoformat(iso) + dt.timedelta(minutes=minutes)).isoformat()


class TestHoursBetween(unittest.TestCase):
    def test_positive_gap(self):
        self.assertAlmostEqual(
            odds_math.hours_between("2025-01-01T12:00:00", "2025-01-01T18:00:00"), 6.0)

    def test_negative_gap_when_later_arg_is_actually_earlier(self):
        self.assertAlmostEqual(
            odds_math.hours_between("2025-01-01T18:00:00", "2025-01-01T12:00:00"), -6.0)

    def test_zero_gap(self):
        self.assertAlmostEqual(
            odds_math.hours_between("2025-01-01T12:00:00", "2025-01-01T12:00:00"), 0.0)

    def test_fractional_hours(self):
        self.assertAlmostEqual(
            odds_math.hours_between("2025-01-01T12:00:00", "2025-01-01T12:30:00"), 0.5)


class TestDynamicMaxStalenessMinutes(unittest.TestCase):
    """Matches config.ODDS_STALENESS_TIERS exactly:
    >=6h -> 60min, >=2h -> 30min, >=30min -> 10min, >=10min -> 3min,
    else -> 1min (also the fallback for AFTER puck drop, i.e. negative
    hours_to_puck_drop -- there is no tier below 0.0)."""

    def test_far_out_gets_60_minutes(self):
        self.assertEqual(odds_math.dynamic_max_staleness_minutes(24.0), 60.0)

    def test_exactly_6_hours_gets_60_minutes(self):
        self.assertEqual(odds_math.dynamic_max_staleness_minutes(6.0), 60.0)

    def test_just_under_6_hours_gets_30_minutes(self):
        self.assertEqual(odds_math.dynamic_max_staleness_minutes(5.999), 30.0)

    def test_exactly_2_hours_gets_30_minutes(self):
        self.assertEqual(odds_math.dynamic_max_staleness_minutes(2.0), 30.0)

    def test_just_under_2_hours_gets_10_minutes(self):
        self.assertEqual(odds_math.dynamic_max_staleness_minutes(1.999), 10.0)

    def test_exactly_30_minutes_gets_10_minutes(self):
        self.assertEqual(odds_math.dynamic_max_staleness_minutes(0.5), 10.0)

    def test_just_under_30_minutes_gets_3_minutes(self):
        self.assertEqual(odds_math.dynamic_max_staleness_minutes(0.49), 3.0)

    def test_exactly_10_minutes_gets_3_minutes(self):
        self.assertEqual(odds_math.dynamic_max_staleness_minutes(10.0 / 60.0), 3.0)

    def test_just_under_10_minutes_gets_1_minute(self):
        self.assertEqual(odds_math.dynamic_max_staleness_minutes(0.16), 1.0)

    def test_at_puck_drop_gets_1_minute(self):
        self.assertEqual(odds_math.dynamic_max_staleness_minutes(0.0), 1.0)

    def test_after_puck_drop_still_falls_back_to_1_minute(self):
        self.assertEqual(odds_math.dynamic_max_staleness_minutes(-0.5), 1.0)

    def test_tiers_are_all_configurable_not_hardcoded(self):
        # prove the function actually reads config.ODDS_STALENESS_TIERS
        # rather than duplicating the numbers internally
        original = config.ODDS_STALENESS_TIERS
        try:
            config.ODDS_STALENESS_TIERS = [(6.0, 999.0), (0.0, 1.0)]
            self.assertEqual(odds_math.dynamic_max_staleness_minutes(24.0), 999.0)
        finally:
            config.ODDS_STALENESS_TIERS = original


class TestDynamicStalenessIntegration(unittest.TestCase):
    """pricing/engine.py::evaluate_moneyline_for_game must apply the
    DYNAMIC policy by default (max_staleness_minutes=None), computed from
    pred.scheduled_start_utc -- not the old flat
    config.MAX_ODDS_STALENESS_MINUTES window."""

    def setUp(self):
        self.conn, self.path = make_test_db()
        self.fx = Fixture(self.conn)   # game 1: TOR vs BOS, day 10, 19:00
        self.model = CombinedMoneylineModel(teams=["TOR", "BOS"])

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def _evaluate(self, prediction_time, quote_age_minutes):
        captured_at = _shift(prediction_time, -quote_age_minutes)
        self.fx.add_odds(1, "TOR", -150, captured_at=captured_at,
                          label=f"age{quote_age_minutes}")
        self.fx.add_odds(1, "BOS", +130, captured_at=captured_at,
                          label=f"age{quote_age_minutes}")
        pred = self.model.predict(self.conn, 1, prediction_time)
        return pricing_engine.evaluate_moneyline_for_game(self.conn, pred, "TOR @ BOS")

    def test_more_than_6h_out_accepts_a_50_minute_old_quote(self):
        prediction_time = _shift(self.fx.scheduled_start, -24 * 60)
        reports = self._evaluate(prediction_time, 50)
        self.assertNotEqual(reports[0].action, "DATA_UNAVAILABLE")

    def test_more_than_6h_out_rejects_a_70_minute_old_quote(self):
        prediction_time = _shift(self.fx.scheduled_start, -24 * 60)
        reports = self._evaluate(prediction_time, 70)
        self.assertEqual(reports[0].action, "DATA_UNAVAILABLE")

    def test_2_to_6h_out_accepts_a_20_minute_old_quote(self):
        prediction_time = _shift(self.fx.scheduled_start, -4 * 60)
        reports = self._evaluate(prediction_time, 20)
        self.assertNotEqual(reports[0].action, "DATA_UNAVAILABLE")

    def test_2_to_6h_out_rejects_a_40_minute_old_quote(self):
        prediction_time = _shift(self.fx.scheduled_start, -4 * 60)
        reports = self._evaluate(prediction_time, 40)
        self.assertEqual(reports[0].action, "DATA_UNAVAILABLE")

    def test_30min_to_2h_out_accepts_an_8_minute_old_quote(self):
        prediction_time = _shift(self.fx.scheduled_start, -60)
        reports = self._evaluate(prediction_time, 8)
        self.assertNotEqual(reports[0].action, "DATA_UNAVAILABLE")

    def test_30min_to_2h_out_rejects_a_15_minute_old_quote(self):
        prediction_time = _shift(self.fx.scheduled_start, -60)
        reports = self._evaluate(prediction_time, 15)
        self.assertEqual(reports[0].action, "DATA_UNAVAILABLE")

    def test_10_to_30min_out_accepts_a_2_minute_old_quote(self):
        prediction_time = _shift(self.fx.scheduled_start, -20)
        reports = self._evaluate(prediction_time, 2)
        self.assertNotEqual(reports[0].action, "DATA_UNAVAILABLE")

    def test_10_to_30min_out_rejects_a_5_minute_old_quote(self):
        prediction_time = _shift(self.fx.scheduled_start, -20)
        reports = self._evaluate(prediction_time, 5)
        self.assertEqual(reports[0].action, "DATA_UNAVAILABLE")

    def test_under_10min_out_accepts_a_half_minute_old_quote(self):
        prediction_time = _shift(self.fx.scheduled_start, -5)
        reports = self._evaluate(prediction_time, 0.5)
        self.assertNotEqual(reports[0].action, "DATA_UNAVAILABLE")

    def test_under_10min_out_rejects_a_2_minute_old_quote(self):
        prediction_time = _shift(self.fx.scheduled_start, -5)
        reports = self._evaluate(prediction_time, 2)
        self.assertEqual(reports[0].action, "DATA_UNAVAILABLE")

    def test_explicit_override_bypasses_the_dynamic_policy(self):
        # a caller-supplied fixed window (e.g. a test that doesn't care
        # about staleness policy) must still be honored
        prediction_time = _shift(self.fx.scheduled_start, -24 * 60)
        captured_at = _shift(prediction_time, -90)   # would fail the dynamic 60-min tier
        self.fx.add_odds(1, "TOR", -150, captured_at=captured_at, label="override")
        self.fx.add_odds(1, "BOS", +130, captured_at=captured_at, label="override")
        pred = self.model.predict(self.conn, 1, prediction_time)
        reports = pricing_engine.evaluate_moneyline_for_game(
            self.conn, pred, "TOR @ BOS", max_staleness_minutes=120.0)
        self.assertNotEqual(reports[0].action, "DATA_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
