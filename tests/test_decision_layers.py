"""One coherent decision hierarchy (operational/eggy_intelligence.py FLAG_LAYER / decision_layers) and the player-points policy: a market disagreement is not a hockey contradiction, a price warning is not role risk,
every flag the review can raise has a layer, and a points threshold is a live ingredient only where the out-of-time validation allows it."""
from __future__ import annotations

import copy
import inspect
import json
import re
import unittest
from unittest import mock

from operational import eggy_intelligence as ei
from operational import learning_parlays as lp
from research.product_models.validate import apply_platt
from tests import test_eggy_intelligence as tei


class TestTheRegistry(unittest.TestCase):
    def test_every_flag_name_the_review_can_raise_is_registered(self):
        src = inspect.getsource(ei)
        named = set(re.findall(r'"name": "([A-Z][A-Z_0-9]+)"', src))
        named.discard("")
        missing = sorted(n for n in named if n not in ei.FLAG_LAYER and n.isupper() and "_" in n and not n.startswith(("EXPECTED", "TEAM_WIN")))
        self.assertEqual(missing, [], "a flag with no layer would silently count: register it")

    def test_the_informational_layers_are_exactly_market_above_model_and_price_risk(self):
        self.assertEqual(set(ei.INFORMATIONAL_LAYERS), {ei.MODEL_ABOVE_MARKET, ei.PRICE_RISK})
        self.assertEqual({n for n, l in ei.FLAG_LAYER.items() if l in ei.INFORMATIONAL_LAYERS}, set(lp.SET_ASIDE_FLAGS))

    def test_hockey_role_data_trust_and_model_below_market_count_but_value_flags_do_not(self):
        flags = [{"name": n, "level": "HIGH", "text": ""} for n in ("HOCKEY_PICTURE_AGAINST", "ROLE_ASSUMPTION", "THIN_HISTORY", "MODEL_ABOVE_RECORD", "MODEL_SAYS_OVERPRICED")]
        self.assertEqual(len(ei.counted_flags(flags)), 5)
        value = [{"name": n, "level": "HIGH", "text": ""} for n in ("MARKET_MAY_KNOW_MORE", "MODEL_NOT_SHOWN_TO_BEAT_MARKET", "MODEL_FAR_ABOVE_MARKET", "PRICE_ABOVE_ESTIMATE", "LONG_PRICE", "LONGSHOT_TAIL")]
        self.assertEqual(ei.counted_flags(value), [])

    def test_an_unregistered_flag_counts_because_unknown_is_not_harmless(self):
        self.assertEqual(len(ei.counted_flags([{"name": "SOMETHING_NEW", "level": "HIGH", "text": ""}])), 1)


class TestTheLayersOnACard(unittest.TestCase):
    def setUp(self):
        self.t = tei.TestMoneylineReview()
        self.t.setUp()

    def test_a_moneyline_card_keeps_hockey_market_edge_trust_and_price_apart(self):
        c = self.t.review("AAA", shift=-0.05)                                       # the model 5 points above the market
        L = c["decision_layers"]
        self.assertAlmostEqual(L["C_model_edge_vs_market"], L["A_hockey_probability"] - L["B_market_probability"], places=3)
        self.assertGreater(L["C_model_edge_vs_market"], 0.03)
        self.assertEqual(c["contradiction"]["level"], "HIGH")                           # the card's own count is unchanged
        self.assertEqual(L["F_hockey_and_role_contradictions"]["level"], "LOW")         # the hockey/role layer is clean: the disagreement is not a hockey contradiction
        self.assertTrue({"MODEL_NOT_SHOWN_TO_BEAT_MARKET:MODERATE", "MARKET_MAY_KNOW_MORE:MODERATE"} <= set(L["G_price_and_value"]["informational_flags"]))
        self.assertEqual(L["D_model_trust"]["calibration"], c["calibration_confidence"]["level"])
        self.assertIn("E_data_quality", L)

    def test_the_underdog_the_model_says_is_overpriced_keeps_its_contradiction_in_the_counted_layer(self):
        c = self.t.review("AAA", shift=0.05)                                         # the model says the side is overpriced
        L = c["decision_layers"]
        self.assertTrue(L["F_hockey_and_role_contradictions"]["model_below_market"])
        self.assertNotEqual(L["F_hockey_and_role_contradictions"]["level"], "LOW")

    def test_every_review_carries_the_layers_and_a_failed_review_still_does_not_raise(self):
        self.assertIn("decision_layers", self.t.review("BBB", shift=0.0))
        card = ei.review_leg(tei.make_ctx(), tei.base_leg(market_family="PUCK_LINE", participant_id="9"), "X", 0.5)
        self.assertEqual(card["kind"], "ERROR")


class TestPlayerPointsPolicy(unittest.TestCase):
    def test_the_validation_file_enables_the_thresholds_that_passed_and_names_the_recalibration(self):
        pol = ei.points_policy()
        self.assertEqual(sorted(pol["enabled"]), [1, 2, 3])
        self.assertEqual(sorted(pol["recalibration"]), [2])                           # only the threshold whose live calibration failed out of time and whose refit passed
        d = json.loads(ei.POINTS_VALIDATION_PATH.read_text())
        for k in ("1", "2", "3"):
            lv = d["thresholds"][k]["variants"][d["thresholds"][k]["live_variant"]]
            self.assertTrue(lv["passes_rule"])
            self.assertGreaterEqual(lv["rows"], d["rule_declared_before_the_numbers"]["min_test_rows"])

    def test_a_missing_validation_file_enables_nothing(self):
        with mock.patch.object(ei, "POINTS_VALIDATION_PATH", ei.POINTS_VALIDATION_PATH.with_name("does_not_exist.json")):
            self.assertEqual(ei.points_policy()["enabled"], {})

    def test_a_points_threshold_that_is_not_validated_is_not_a_parlay_leg(self):
        t = tei.TestPlayerReview()
        t.setUp()
        leg = tei.prop_leg(fam="PLAYER_POINTS", k=4, price=-150.0, p=0.7)
        c = t.review(leg, 0.7, market="points>=4")
        ok, why = lp.leg_ok({"leg": leg, "card": c, "action": c["action"]["decision"]})
        self.assertFalse(ok)
        self.assertIn("not validated", why)

    def test_a_validated_points_threshold_goes_through_the_same_review_as_any_leg(self):
        t = tei.TestPlayerReview()
        t.setUp()
        leg = tei.prop_leg(fam="PLAYER_POINTS", k=1, price=-200.0, p=0.66)
        c = t.review(leg, 0.66, market="points>=1")
        self.assertEqual(c["bet"]["label"], "Test Player — POINTS 1+ — -200")
        self.assertIn("decision_layers", c)
        ok, why = lp.leg_ok({"leg": leg, "card": c, "action": c["action"]["decision"]})
        self.assertNotIn("not validated", why)

    def test_the_recalibrated_threshold_uses_the_validated_refit_on_the_raw_probability(self):
        t = tei.TestPlayerReview()
        t.setUp()
        a, b = ei.points_policy()["recalibration"][2]
        leg = tei.prop_leg(fam="PLAYER_POINTS", k=2, price=300.0, p=0.30)
        c = t.review(leg, 0.30, market="points>=2")
        self.assertAlmostEqual(c["probabilities"]["model"], round(apply_platt(0.28, a, b), 4), places=4)       # the fixture's raw probability is calibrated - 0.02
        self.assertNotEqual(c["probabilities"]["model"], 0.30)


if __name__ == "__main__":
    unittest.main()


class TestNoRetiredLogicInTheLiveParlayPath(unittest.TestCase):
    """The parlay product must not inherit active behaviour from the straight-bet floor, the primary slip, the old ticket targets or a stake: scanned, not assumed."""
    BANNED = ("ODDS_FLOOR", "primary", "ticket_policy", "PAPER_BET_STAKE", "STAKE", "-150", "DAILY_TARGET_STRAIGHT")

    def test_the_parlay_builder_module_contains_none_of_them(self):
        from operational import learning_parlays
        src = inspect.getsource(learning_parlays)
        code = "\n".join(l.split("#")[0] for l in src.splitlines())             # comments may explain what was retired; code may not use it
        for word in self.BANNED:
            self.assertNotIn(word, code.replace('"""', ""), word)

    def test_the_parlay_recording_function_reads_no_straight_bet_gate(self):
        from operational import learning_cohort as lc
        code = inspect.getsource(lc._record_parlays)
        for word in ("ODDS_FLOOR", "_eggy_legs", "daily_tickets", "ticket_policy", "STAKE"):
            self.assertNotIn(word, code, word)

    def test_a_long_priced_leg_passes_the_parlay_eligibility_gate_the_floor_exists_only_in_straight_mode(self):
        import inspect as _i
        from operational import learning_cohort as lc
        src = _i.getsource(lc.eligibility_reasons)
        self.assertIn('if UNIT == "PARLAY"', src)
        self.assertIn("elif leg.american_price < ODDS_FLOOR", src)
