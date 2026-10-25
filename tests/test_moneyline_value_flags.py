"""A moneyline leg is not rejected from a parlay merely because the hockey model likes the side MORE than the market does: MODEL_NOT_SHOWN_TO_BEAT_MARKET and MARKET_MAY_KNOW_MORE stay on the review card as
caution but, like the prop path's MODEL_FAR_ABOVE_MARKET, they are value-versus-price flags and do not enter the parlay leg's non-price contradiction. Independent evidence still rejects."""
from __future__ import annotations

import copy
import unittest

from operational import eggy_intelligence as ei
from operational import learning_parlays as lp
from tests import test_eggy_intelligence as tei
from tests import test_learning_parlays as tlp

BOTH = ("MODEL_NOT_SHOWN_TO_BEAT_MARKET", "MARKET_MAY_KNOW_MORE")


class TestMoneylineModelAboveMarket(unittest.TestCase):
    def setUp(self):
        self.t = tei.TestMoneylineReview()
        self.t.setUp()

    def card(self, team, shift):
        return self.t.review(team, shift=shift)

    def ok(self, card):
        return lp.leg_ok({"leg": None, "card": card, "action": card["action"]["decision"]})

    def names(self, card):
        return {f["name"]: f["level"] for f in card["contradiction"]["flags"]}

    def test_the_two_warnings_stay_on_the_card_and_the_card_still_reads_high(self):
        c = self.card("AAA", -0.05)                                                  # the model 5 points above the market
        self.assertEqual(self.names(c)["MODEL_NOT_SHOWN_TO_BEAT_MARKET"], "MODERATE")
        self.assertEqual(self.names(c)["MARKET_MAY_KNOW_MORE"], "MODERATE")
        self.assertEqual(c["contradiction"]["level"], "HIGH")                        # the review is unchanged
        self.assertEqual(c["action"]["decision"], "LOWER_PRIORITY")
        self.assertEqual(c["thesis"]["type"], "MODEL_SEES_VALUE")

    def test_a_favourite_the_model_likes_more_than_the_market_is_eligible_when_nothing_else_is_against_it(self):
        for shift in (-0.03, -0.05, -0.06, -0.10):                                  # includes the HIGH form of MARKET_MAY_KNOW_MORE (a large gap)
            c = self.card("AAA", shift)
            self.assertTrue(set(BOTH) <= set(self.names(c)), shift)
            self.assertEqual(self.ok(c), (True, ""), shift)

    def test_a_favourite_the_model_and_market_agree_on_is_unchanged(self):
        self.assertEqual(self.ok(self.card("AAA", 0.0)), (True, ""))

    def test_the_model_saying_the_side_is_overpriced_still_rejects(self):
        c = self.card("AAA", 0.05)
        self.assertIn("MODEL_SAYS_OVERPRICED", self.names(c))
        self.assertFalse(self.ok(c)[0])

    def test_poor_calibration_still_rejects(self):
        c = copy.deepcopy(self.card("AAA", -0.05))
        c["calibration_confidence"]["level"] = "LOW"
        ok, why = self.ok(c)
        self.assertFalse(ok)
        self.assertIn("calibration", why)

    def test_an_estimate_under_the_55_percent_floor_still_rejects(self):
        c = copy.deepcopy(self.card("AAA", -0.05))
        c["estimate_used"] = 0.52
        ok, why = self.ok(c)
        self.assertFalse(ok)
        self.assertIn("not a likely outcome", why)

    def test_an_independent_high_flag_beside_the_two_still_rejects(self):
        c = copy.deepcopy(self.card("AAA", -0.05))
        c["contradiction"]["flags"].append({"name": "HOCKEY_PICTURE_AGAINST", "level": "HIGH", "text": "x"})
        ok, why = self.ok(c)
        self.assertFalse(ok)
        self.assertIn("contradiction HIGH", why)

    def test_the_underdog_with_the_two_flags_is_still_rejected_for_its_hockey_picture_and_estimate(self):
        c = self.card("BBB", -0.05)
        self.assertTrue(set(BOTH) <= set(self.names(c)))
        self.assertFalse(self.ok(c)[0])

    def test_the_set_aside_list_is_the_price_flags_plus_exactly_these_two(self):
        self.assertEqual(lp.PRICE_FLAGS, ("PRICE_ABOVE_ESTIMATE", "MODEL_FAR_ABOVE_MARKET", "LONG_PRICE", "LONGSHOT_TAIL"))
        self.assertEqual(lp.MODEL_ABOVE_MARKET_FLAGS, BOTH)
        self.assertEqual(lp.SET_ASIDE_FLAGS, lp.PRICE_FLAGS + BOTH)

    def test_the_ticket_view_of_the_weakest_leg_ignores_them_the_same_way(self):
        flags = [{"name": "MARKET_MAY_KNOW_MORE", "level": "HIGH", "text": "x"}, {"name": "MODEL_NOT_SHOWN_TO_BEAT_MARKET", "level": "MODERATE", "text": "x"}]
        cands = [tlp.cand(pid="FLA", game="1", p=0.62, price=-185.0, fam="MONEYLINE", k=None, team="FLA", flags=flags), tlp.cand(pid="A", game="2", p=0.70), tlp.cand(pid="B", game="3", p=0.71)]
        for c in cands:
            self.assertTrue(lp.leg_ok(c)[0])
        p = lp.construct(cands)
        self.assertIsNotNone(p)
        self.assertEqual(p["weakest_leg"]["contradiction"], "LOW")
        self.assertEqual(p["construction_quality"], "GOOD")

    def test_the_review_module_is_unchanged_by_this(self):
        import inspect
        src = inspect.getsource(ei.review_leg)
        self.assertNotIn("SET_ASIDE", src)


if __name__ == "__main__":
    unittest.main()
