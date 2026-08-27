import unittest

from pricing import odds_math
from pricing.odds_math import InvalidOddsError


class TestOddsConversion(unittest.TestCase):
    def test_american_to_prob_favorite(self):
        self.assertAlmostEqual(odds_math.american_to_prob(-200), 200 / 300)

    def test_american_to_prob_underdog(self):
        self.assertAlmostEqual(odds_math.american_to_prob(150), 100 / 250)

    def test_prob_to_american_roundtrip_favorite(self):
        price = odds_math.prob_to_american(0.65)
        self.assertLess(price, -100)
        self.assertAlmostEqual(odds_math.american_to_prob(price), 0.65, places=6)

    def test_prob_to_american_roundtrip_underdog(self):
        price = odds_math.prob_to_american(0.35)
        self.assertGreater(price, 100)
        self.assertAlmostEqual(odds_math.american_to_prob(price), 0.35, places=6)

    def test_prob_to_american_rejects_out_of_range(self):
        with self.assertRaises(ValueError):
            odds_math.prob_to_american(0.0)
        with self.assertRaises(ValueError):
            odds_math.prob_to_american(1.0)


class TestInvalidOdds(unittest.TestCase):
    def test_zero_rejected(self):
        with self.assertRaises(InvalidOddsError):
            odds_math.american_to_prob(0)

    def test_between_negative_100_and_100_rejected(self):
        for bad in (-99, -1, 1, 50, 99.9):
            with self.assertRaises(InvalidOddsError):
                odds_math.american_to_prob(bad)

    def test_none_rejected(self):
        with self.assertRaises(InvalidOddsError):
            odds_math.american_to_prob(None)

    def test_nan_rejected(self):
        with self.assertRaises(InvalidOddsError):
            odds_math.american_to_prob(float("nan"))

    def test_boundary_values_accepted(self):
        odds_math.american_to_prob(-100)   # allowed
        odds_math.american_to_prob(100)    # allowed


class TestNoVig(unittest.TestCase):
    def test_no_vig_two_way_sums_to_one(self):
        p_a, p_b = odds_math.no_vig_two_way(-150, 130)
        self.assertAlmostEqual(p_a + p_b, 1.0, places=9)

    def test_no_vig_removes_the_juice(self):
        # a symmetric -110/-110 market implies 52.4%/52.4% raw (sums >100%);
        # no-vig should push both back to exactly 50%
        p_a, p_b = odds_math.no_vig_two_way(-110, -110)
        self.assertAlmostEqual(p_a, 0.5, places=6)
        self.assertAlmostEqual(p_b, 0.5, places=6)


class TestExpectedValue(unittest.TestCase):
    def test_positive_ev_when_model_beats_market(self):
        # fair price for 65% is about -186; getting -150 is generous
        ev = odds_math.expected_value(0.65, -150)
        self.assertGreater(ev, 0)

    def test_zero_ev_at_the_fair_price(self):
        fair_price = odds_math.prob_to_american(0.65)
        ev = odds_math.expected_value(0.65, fair_price)
        self.assertAlmostEqual(ev, 0.0, places=6)

    def test_negative_ev_at_a_worse_price(self):
        ev = odds_math.expected_value(0.55, -300)
        self.assertLess(ev, 0)


class TestKelly(unittest.TestCase):
    def test_kelly_zero_at_fair_price(self):
        fair_price = odds_math.prob_to_american(0.60)
        self.assertAlmostEqual(odds_math.kelly_fraction(0.60, fair_price), 0.0, places=6)

    def test_kelly_positive_with_edge(self):
        self.assertGreater(odds_math.kelly_fraction(0.60, -110), 0)

    def test_kelly_zero_with_negative_edge(self):
        self.assertEqual(odds_math.kelly_fraction(0.40, -110), 0.0)


class TestMaxAcceptablePrice(unittest.TestCase):
    """v2.1.1a spec item 3: max_acceptable_price() must be verified
    against the engine's ACTUAL two-sided no-vig edge definition
    (odds_math.no_vig_two_way), not an approximation from the target
    side's own raw implied probability -- that approximation is exactly
    the bug this fix closes, so these tests must not silently reintroduce
    it by using american_to_prob() as the check."""

    def _edge_at(self, conservative_prob, target_price, opponent_price):
        no_vig_target, _ = odds_math.no_vig_two_way(target_price, opponent_price)
        return conservative_prob - no_vig_target

    def test_price_at_threshold_approximately_satisfies_min_edge(self):
        conservative_prob, min_edge, opponent_price = 0.60, 0.03, -110
        max_price = odds_math.max_acceptable_price(conservative_prob, min_edge, opponent_price)
        self.assertIsNotNone(max_price)
        edge = self._edge_at(conservative_prob, max_price, opponent_price)
        self.assertAlmostEqual(edge, min_edge, places=6)

    def test_worse_than_max_price_fails_min_edge_two_sided(self):
        conservative_prob, min_edge, opponent_price = 0.60, 0.03, -110
        max_price = odds_math.max_acceptable_price(conservative_prob, min_edge, opponent_price)
        # "worse" for the bettor always means numerically lower (more
        # negative, or a smaller positive number) -- a higher implied
        # probability paid for the same edge requirement.
        worse_price = max_price - 20
        edge_at_worse = self._edge_at(conservative_prob, worse_price, opponent_price)
        self.assertLess(edge_at_worse, min_edge)

    def test_better_than_max_price_clears_min_edge_two_sided(self):
        conservative_prob, min_edge, opponent_price = 0.60, 0.03, -110
        max_price = odds_math.max_acceptable_price(conservative_prob, min_edge, opponent_price)
        better_price = max_price + 20
        edge_at_better = self._edge_at(conservative_prob, better_price, opponent_price)
        self.assertGreaterEqual(edge_at_better, min_edge - 1e-6)

    def test_favorite_target_vs_underdog_opponent(self):
        conservative_prob, min_edge, opponent_price = 0.70, 0.03, 150
        max_price = odds_math.max_acceptable_price(conservative_prob, min_edge, opponent_price)
        self.assertIsNotNone(max_price)
        self.assertAlmostEqual(
            self._edge_at(conservative_prob, max_price, opponent_price), min_edge, places=6)
        self.assertLess(self._edge_at(conservative_prob, max_price - 20, opponent_price), min_edge)
        self.assertGreaterEqual(
            self._edge_at(conservative_prob, max_price + 20, opponent_price), min_edge - 1e-6)

    def test_favorite_target_vs_favorite_opponent(self):
        conservative_prob, min_edge, opponent_price = 0.65, 0.03, -140
        max_price = odds_math.max_acceptable_price(conservative_prob, min_edge, opponent_price)
        self.assertIsNotNone(max_price)
        self.assertAlmostEqual(
            self._edge_at(conservative_prob, max_price, opponent_price), min_edge, places=6)
        self.assertLess(self._edge_at(conservative_prob, max_price - 20, opponent_price), min_edge)
        self.assertGreaterEqual(
            self._edge_at(conservative_prob, max_price + 20, opponent_price), min_edge - 1e-6)

    def test_underdog_target_vs_favorite_opponent(self):
        conservative_prob, min_edge, opponent_price = 0.40, 0.03, -160
        max_price = odds_math.max_acceptable_price(conservative_prob, min_edge, opponent_price)
        self.assertIsNotNone(max_price)
        self.assertAlmostEqual(
            self._edge_at(conservative_prob, max_price, opponent_price), min_edge, places=6)
        self.assertLess(self._edge_at(conservative_prob, max_price - 20, opponent_price), min_edge)
        self.assertGreaterEqual(
            self._edge_at(conservative_prob, max_price + 20, opponent_price), min_edge - 1e-6)

    def test_symmetric_market(self):
        conservative_prob, min_edge, opponent_price = 0.55, 0.03, -110
        max_price = odds_math.max_acceptable_price(conservative_prob, min_edge, opponent_price)
        self.assertIsNotNone(max_price)
        self.assertAlmostEqual(
            self._edge_at(conservative_prob, max_price, opponent_price), min_edge, places=6)
        self.assertLess(self._edge_at(conservative_prob, max_price - 20, opponent_price), min_edge)
        self.assertGreaterEqual(
            self._edge_at(conservative_prob, max_price + 20, opponent_price), min_edge - 1e-6)

    def test_uses_the_real_no_vig_two_way_function_not_an_approximation(self):
        # regression guard: the OLD buggy formula (conservative_prob /
        # (1 + min_edge), ignoring the opponent price) would give a
        # materially different answer than the real two-sided edge
        # definition whenever the opponent price carries meaningful vig
        # asymmetry -- prove the two disagree, so a future accidental
        # revert to the old approximation would be caught by the
        # threshold tests above even without this test.
        conservative_prob, min_edge, opponent_price = 0.60, 0.03, -110
        old_buggy_breakeven = conservative_prob / (1.0 + min_edge)
        old_buggy_price = odds_math.prob_to_american(old_buggy_breakeven)
        correct_price = odds_math.max_acceptable_price(conservative_prob, min_edge, opponent_price)
        self.assertNotAlmostEqual(old_buggy_price, correct_price, places=0)

    def test_returns_none_when_min_edge_exceeds_conservative_probability(self):
        # no price, however long, could ever clear a 60pp edge requirement
        # against a 50% conservative probability.
        self.assertIsNone(odds_math.max_acceptable_price(0.50, 0.60, -110))

    def test_returns_none_at_the_exact_unreachable_boundary(self):
        self.assertIsNone(odds_math.max_acceptable_price(0.03, 0.03, -110))


if __name__ == "__main__":
    unittest.main()
