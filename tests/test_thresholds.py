"""
Spec item 9: conservative_edge (probability points) and expected_value
(% return at the actual price) are DIFFERENT quantities, and BET requires
BOTH to clear their own minimum. These tests exercise pricing/engine.py
directly with constructed scenarios (a real conservative_prob_home plus a
hand-picked DraftKings price) so each of the four edge/EV quadrants is
provable without depending on the model's own calibration.
"""
import unittest

import config
from pricing import engine as pricing_engine
from pricing import odds_math
from tests.helpers import Fixture, make_test_db, t


class _FakePrediction:
    """A minimal stand-in for models.combined_model.GamePrediction — only
    the fields pricing.engine.evaluate_moneyline_for_game actually reads."""

    def __init__(self, game_id, home_team, away_team, prediction_time_utc,
                 model_prob_home, conservative_prob_home, ci_high,
                 home_goalie_status="CONFIRMED", away_goalie_status="CONFIRMED",
                 scheduled_start_utc=None):
        self.game_id = game_id
        self.home_team = home_team
        self.away_team = away_team
        self.prediction_time_utc = prediction_time_utc
        self.model_prob_home = model_prob_home
        self.conservative_prob_home = conservative_prob_home
        self.ci_high = ci_high
        self.home_goalie_status = home_goalie_status
        self.away_goalie_status = away_goalie_status
        self.scheduled_start_utc = scheduled_start_utc


class TestThresholds(unittest.TestCase):
    def setUp(self):
        self.conn, self.path = make_test_db()
        self.fx = Fixture(self.conn)
        self.prediction_time = t(9, hour=18, minute=30)

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def _pred(self, conservative_prob_home, model_prob_home=None):
        if model_prob_home is None:
            model_prob_home = conservative_prob_home + 0.05
        return _FakePrediction(
            game_id=1, home_team="TOR", away_team="BOS",
            prediction_time_utc=self.prediction_time,
            model_prob_home=model_prob_home,
            conservative_prob_home=conservative_prob_home,
            ci_high=min(model_prob_home + 0.09, 0.99),
            scheduled_start_utc=self.fx.scheduled_start,
        )

    def _price_for_prob(self, prob):
        return odds_math.prob_to_american(prob)

    def test_bet_requires_both_edge_and_ev_generous_price_clears_both(self):
        # conservative prob well above a generous market price -> both
        # conservative_edge and EV clear their minimums
        conservative_prob = 0.62
        self.fx.add_odds(1, "TOR", -110, captured_at=t(9, hour=18), label="T-30")
        self.fx.add_odds(1, "BOS", -110, captured_at=t(9, hour=18), label="T-30")
        pred = self._pred(conservative_prob)
        reports = pricing_engine.evaluate_moneyline_for_game(self.conn, pred, "TOR @ BOS")
        home = next(r for r in reports if r.selection == "TOR")
        self.assertGreaterEqual(home.conservative_edge, config.MIN_CONSERVATIVE_EDGE)
        self.assertGreaterEqual(home.expected_value, config.MIN_EV)
        self.assertEqual(home.action, "BET")

    def test_big_edge_but_thin_ev_at_a_heavy_favorite_price_is_not_bet(self):
        # a big probability-point edge can still come with thin % EV once
        # the price is already a heavy favorite (diminishing payout) —
        # construct a price close enough to the no-vig line that EV is
        # tiny even though the raw edge is large.
        conservative_prob = 0.93
        # true fair no-vig price for 0.93 is far more negative than -110;
        # pick a market price only slightly better than fair, so edge in
        # probability points is large-ish but EV stays under 2%.
        fair_price = odds_math.prob_to_american(0.93)
        # nudge the market price toward the fair price (worse for the bettor)
        market_price = fair_price + 5  # e.g. -1329 -> -1324ish; still steep
        self.fx.add_odds(1, "TOR", market_price, captured_at=t(9, hour=18), label="T-30")
        self.fx.add_odds(1, "BOS", 900, captured_at=t(9, hour=18), label="T-30")
        pred = self._pred(conservative_prob, model_prob_home=0.95)
        reports = pricing_engine.evaluate_moneyline_for_game(self.conn, pred, "TOR @ BOS")
        home = next(r for r in reports if r.selection == "TOR")
        self.assertLess(home.expected_value, config.MIN_EV)
        self.assertNotEqual(home.action, "BET")
        self.assertEqual(home.action, "PASS")

    def test_negative_edge_is_pass_not_bet_even_with_good_price(self):
        # the market already implies a HIGHER home win probability than our
        # own conservative estimate -- no edge, regardless of price shape.
        conservative_prob = 0.35
        self.fx.add_odds(1, "TOR", -200, captured_at=t(9, hour=18), label="T-30")
        self.fx.add_odds(1, "BOS", +170, captured_at=t(9, hour=18), label="T-30")
        pred = self._pred(conservative_prob, model_prob_home=0.40)
        reports = pricing_engine.evaluate_moneyline_for_game(self.conn, pred, "TOR @ BOS")
        home = next(r for r in reports if r.selection == "TOR")
        self.assertLess(home.conservative_edge, config.MIN_CONSERVATIVE_EDGE)
        self.assertEqual(home.action, "PASS")

    def test_max_acceptable_price_is_reported_but_not_the_gate_itself(self):
        # A price strictly better than max_acceptable_price should always
        # clear the edge threshold; the actual gate re-derives edge/EV at
        # the REAL price rather than comparing price-vs-max-price directly.
        # v2.1.1a spec item 3: max_acceptable_price now needs the
        # opponent's price too (the engine's edge is a two-sided no-vig
        # comparison) -- BOS is fixed at -110 below, matching the price
        # inserted for BOS a few lines down.
        conservative_prob = 0.60
        max_price = odds_math.max_acceptable_price(
            conservative_prob, config.MIN_CONSERVATIVE_EDGE, -110)
        self.assertIsNotNone(max_price)
        better_price = max_price + 20 if max_price < 0 else max_price - 20
        self.fx.add_odds(1, "TOR", better_price, captured_at=t(9, hour=18), label="T-30")
        self.fx.add_odds(1, "BOS", -110, captured_at=t(9, hour=18), label="T-30")
        pred = self._pred(conservative_prob, model_prob_home=0.65)
        reports = pricing_engine.evaluate_moneyline_for_game(self.conn, pred, "TOR @ BOS")
        home = next(r for r in reports if r.selection == "TOR")
        self.assertIsNotNone(home.maximum_acceptable_draftkings_price)
        self.assertGreaterEqual(home.conservative_edge, config.MIN_CONSERVATIVE_EDGE - 1e-6)


class TestGoalieWaitPolicy(unittest.TestCase):
    def setUp(self):
        self.conn, self.path = make_test_db()
        self.fx = Fixture(self.conn)
        self.fx.add_odds(1, "TOR", -150, captured_at=t(9, hour=18), label="T-30")
        self.fx.add_odds(1, "BOS", +130, captured_at=t(9, hour=18), label="T-30")
        self.prediction_time = t(9, hour=18, minute=30)

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def _pred(self, home_status, away_status):
        return _FakePrediction(
            game_id=1, home_team="TOR", away_team="BOS",
            prediction_time_utc=self.prediction_time,
            model_prob_home=0.65, conservative_prob_home=0.60, ci_high=0.70,
            home_goalie_status=home_status, away_goalie_status=away_status,
            scheduled_start_utc=self.fx.scheduled_start,
        )

    def test_both_unconfirmed_is_wait(self):
        pred = self._pred("EXPECTED", "EXPECTED")
        reports = pricing_engine.evaluate_moneyline_for_game(self.conn, pred, "TOR @ BOS")
        for r in reports:
            self.assertEqual(r.action, "WAIT")
            self.assertIn("goalie", r.action_reason.lower())

    def test_home_confirmed_away_unconfirmed_still_waits(self):
        # a moneyline bet on EITHER side depends on BOTH goalies
        pred = self._pred("CONFIRMED", "EXPECTED")
        reports = pricing_engine.evaluate_moneyline_for_game(self.conn, pred, "TOR @ BOS")
        for r in reports:
            self.assertEqual(r.action, "WAIT")

    def test_both_confirmed_proceeds_to_normal_threshold_check(self):
        pred = self._pred("CONFIRMED", "CONFIRMED")
        reports = pricing_engine.evaluate_moneyline_for_game(self.conn, pred, "TOR @ BOS")
        for r in reports:
            self.assertNotEqual(r.action, "WAIT")

    def test_wait_report_still_has_full_market_numbers_populated(self):
        # regression test for the WAIT-path crash bug: a WAIT report must
        # still carry the model's view, not None fields that would crash
        # BetReport.format()'s percentage formatting.
        pred = self._pred("EXPECTED", "CONFIRMED")
        reports = pricing_engine.evaluate_moneyline_for_game(self.conn, pred, "TOR @ BOS")
        home = next(r for r in reports if r.selection == "TOR")
        self.assertEqual(home.action, "WAIT")
        self.assertIsNotNone(home.model_true_probability)
        self.assertIsNotNone(home.conservative_edge)
        self.assertIsNotNone(home.expected_value)
        formatted = home.format()   # must not raise
        self.assertIn("WAIT", formatted)

    def test_expected_starter_allowed_by_explicit_policy_override(self):
        pred = self._pred("EXPECTED", "CONFIRMED")
        reports = pricing_engine.evaluate_moneyline_for_game(
            self.conn, pred, "TOR @ BOS", allow_expected_starter=True
        )
        for r in reports:
            self.assertNotEqual(r.action, "WAIT")

    def test_default_policy_does_not_allow_expected_starter(self):
        self.assertFalse(config.ALLOW_BETTING_ON_EXPECTED_STARTER)


if __name__ == "__main__":
    unittest.main()
