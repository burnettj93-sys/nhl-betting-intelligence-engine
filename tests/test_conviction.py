"""
Tests for dashboard/conviction.py (Same-Day Demo Experience sprint,
2026-08-31, extended by the Live DK / Paper Bankroll completion sprint).
Covers the Conviction Score's value-weighting behavior (never probability
alone) and real-registry maturity discount, Top Conviction's real
filters, and the joint-dependence combo builder's redundancy detection,
never-independence-assumption rule, and the HIGH_CONFIDENCE /
VALUE_COMBINATION / RESEARCH_COMBINATION three-way classification.
"""
from __future__ import annotations

import unittest

from dashboard import conviction as cv
from dashboard import eligible_bets as eb


def _leg(player_id="P1", prop="sog", threshold="3+", market="PLAYER_SOG", conservative_p=0.5,
         conservative_edge=0.05, ev=0.05, raw_edge=0.05, confidence="HIGH", decision="BET",
         current_odds=-120, coherent_probability=0.5, starter_certainty=None, fair_odds=None):
    from pricing import odds_math as pm
    return {
        "player_id": player_id, "player": "Test Player", "prop": prop, "threshold": threshold,
        "market": market, "conservative_probability": conservative_p,
        "conservative_edge": conservative_edge, "ev": ev, "raw_edge": raw_edge,
        "confidence": confidence, "decision": decision, "current_odds": current_odds,
        "coherent_probability": coherent_probability, "actionable": True,
        "starter_certainty": starter_certainty,
        "fair_odds": fair_odds if fair_odds is not None else pm.prob_to_american(coherent_probability),
    }


class TestConvictionScoreWeighting(unittest.TestCase):
    """Part 14: high probability alone must never be sufficient -- price
    value (edge/EV) has to matter as much or more than raw probability."""

    def test_high_probability_bad_price_scores_below_lower_probability_good_price(self):
        high_p_bad_price = _leg(conservative_p=0.90, conservative_edge=0.0, ev=-0.05)
        lower_p_good_price = _leg(conservative_p=0.55, conservative_edge=0.10, ev=0.15)
        self.assertLess(cv.conviction_score(high_p_bad_price), cv.conviction_score(lower_p_good_price))

    def test_score_is_bounded_and_never_negative_for_valid_inputs(self):
        leg = _leg(conservative_p=0.8, conservative_edge=0.2, ev=0.4, confidence="HIGH")
        score = cv.conviction_score(leg)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.01)

    def test_zero_edge_and_zero_ev_still_scores_from_probability_and_confidence_alone(self):
        leg = _leg(conservative_p=0.6, conservative_edge=0.0, ev=0.0, confidence="LOW")
        score = cv.conviction_score(leg)
        self.assertAlmostEqual(score, 0.30 * 0.6, places=6)


class TestTopConviction(unittest.TestCase):
    def test_never_includes_non_bet_decisions(self):
        legs = [_leg(decision="WATCH"), _leg(decision="WAIT"), _leg(decision="PASS")]
        self.assertEqual(cv.top_conviction(legs), [])

    def test_never_includes_low_confidence(self):
        legs = [_leg(confidence="LOW", conservative_edge=0.10, ev=0.10)]
        self.assertEqual(cv.top_conviction(legs), [])

    def test_respects_min_edge(self):
        legs = [_leg(conservative_edge=0.01)]
        self.assertEqual(cv.top_conviction(legs, min_edge=0.02), [])

    def test_excludes_uncertain_starters(self):
        legs = [_leg(starter_certainty=0.4, conservative_edge=0.1, ev=0.1)]
        self.assertEqual(cv.top_conviction(legs), [])

    def test_qualifying_leg_is_returned_with_a_score(self):
        legs = [_leg(conservative_edge=0.1, ev=0.1)]
        result = cv.top_conviction(legs)
        self.assertEqual(len(result), 1)
        self.assertIn("conviction_score", result[0])

    def test_never_pads_beyond_genuinely_qualifying_legs(self):
        legs = [_leg(decision="WAIT") for _ in range(10)]
        self.assertEqual(cv.top_conviction(legs, max_n=5), [])

    def test_sorted_descending_by_conviction_score(self):
        weak = _leg(player_id="A", conservative_p=0.5, conservative_edge=0.02, ev=0.02)
        strong = _leg(player_id="B", conservative_p=0.8, conservative_edge=0.14, ev=0.28)
        result = cv.top_conviction([weak, strong])
        self.assertEqual([r["player_id"] for r in result], ["B", "A"])

    def test_against_real_demo_data_never_raises(self):
        opps = eb.all_opportunities()
        result = cv.top_conviction(opps)
        self.assertIsInstance(result, list)
        self.assertTrue(all(o["decision"] == "BET" for o in result))


class TestJointProbabilityForPair(unittest.TestCase):
    def test_different_players_are_not_validated(self):
        a = _leg(player_id="P1")
        b = _leg(player_id="P2")
        result = cv.joint_probability_for_pair(a, b)
        self.assertEqual(result["status"], "JOINT_DEPENDENCE_NOT_VALIDATED")

    def test_same_family_nested_threshold_is_redundant(self):
        a = _leg(player_id="P1", prop="sog", threshold="2+", conservative_p=0.6)
        b = _leg(player_id="P1", prop="sog", threshold="4+", conservative_p=0.3)
        result = cv.joint_probability_for_pair(a, b)
        self.assertEqual(result["status"], "REDUNDANT")
        self.assertIsNotNone(result["joint_probability"])

    def test_points_1plus_and_points_2plus_same_player_is_redundant(self):
        a = _leg(player_id="P1", prop="points", threshold="1+", conservative_p=0.7)
        b = _leg(player_id="P1", prop="points", threshold="2+", conservative_p=0.2)
        result = cv.joint_probability_for_pair(a, b)
        self.assertEqual(result["status"], "REDUNDANT")

    def test_cross_family_goal_and_point_is_redundant(self):
        a = _leg(player_id="P1", prop="goals", threshold="1+", market="PLAYER_GOALS", conservative_p=0.3)
        b = _leg(player_id="P1", prop="points", threshold="1+", market="PLAYER_POINTS", conservative_p=0.6)
        result = cv.joint_probability_for_pair(a, b)
        self.assertEqual(result["status"], "REDUNDANT")

    def test_cross_family_assist_and_point_is_redundant(self):
        a = _leg(player_id="P1", prop="assists", threshold="1+", market="PLAYER_ASSISTS", conservative_p=0.25)
        b = _leg(player_id="P1", prop="points", threshold="1+", market="PLAYER_POINTS", conservative_p=0.6)
        result = cv.joint_probability_for_pair(a, b)
        self.assertEqual(result["status"], "REDUNDANT")

    def test_validated_rho_pair_uses_gaussian_copula(self):
        a = _leg(player_id="P1", prop="sog", threshold="3+", conservative_p=0.5)
        b = _leg(player_id="P1", prop="goals", threshold="1+", conservative_p=0.3)
        result = cv.joint_probability_for_pair(a, b)
        self.assertEqual(result["status"], "VALIDATED")
        self.assertIsNotNone(result["rho"])
        self.assertGreater(result["joint_probability"], 0.0)
        self.assertLessEqual(result["joint_probability"], min(0.5, 0.3))

    def test_unknown_sog_threshold_pair_is_not_validated(self):
        a = _leg(player_id="P1", prop="sog", threshold="5+", conservative_p=0.4)
        b = _leg(player_id="P1", prop="assists", threshold="2+", conservative_p=0.2)
        result = cv.joint_probability_for_pair(a, b)
        self.assertEqual(result["status"], "JOINT_DEPENDENCE_NOT_VALIDATED")


class TestComboEligibleLegs(unittest.TestCase):
    def test_uses_raw_edge_not_conservative_edge(self):
        leg = _leg(decision="WATCH", raw_edge=0.05, conservative_edge=-0.05, ev=-0.1)
        self.assertEqual(cv.combo_eligible_legs([leg]), [leg])

    def test_excludes_non_positive_raw_edge(self):
        leg = _leg(decision="WATCH", raw_edge=0.0)
        self.assertEqual(cv.combo_eligible_legs([leg]), [])

    def test_excludes_pass_and_wait_and_research_only(self):
        legs = [_leg(decision="PASS", raw_edge=0.1), _leg(decision="WAIT", raw_edge=0.1),
                _leg(decision="RESEARCH_ONLY", raw_edge=0.1)]
        self.assertEqual(cv.combo_eligible_legs(legs), [])

    def test_excludes_missing_market_price(self):
        leg = _leg(raw_edge=0.1, current_odds=None)
        self.assertEqual(cv.combo_eligible_legs([leg]), [])


class TestMaturityWeight(unittest.TestCase):
    """Completion sprint Part 2: a generic, real-registry-sourced
    discount, never a special case on any one prop name."""

    def test_validated_status_gets_full_weight(self):
        self.assertEqual(cv.maturity_weight("sog"), 1.0)
        self.assertEqual(cv.maturity_weight("goals"), 1.0)
        self.assertEqual(cv.maturity_weight("assists"), 1.0)
        self.assertEqual(cv.maturity_weight("blocks"), 1.0)

    def test_empirical_baseline_status_gets_a_real_discount_never_zero(self):
        w = cv.maturity_weight("points")
        self.assertLess(w, 1.0)
        self.assertGreater(w, 0.0)

    def test_unknown_prop_gets_the_conservative_default_not_full_trust(self):
        self.assertEqual(cv.maturity_weight("nonexistent_prop"), cv._DEFAULT_MATURITY_WEIGHT)
        self.assertLess(cv._DEFAULT_MATURITY_WEIGHT, 1.0)

    def test_conviction_score_applies_the_maturity_discount(self):
        sog_leg = _leg(prop="sog", conservative_p=0.6, conservative_edge=0.1, ev=0.1, confidence="HIGH")
        points_leg = _leg(prop="points", conservative_p=0.6, conservative_edge=0.1, ev=0.1, confidence="HIGH")
        self.assertGreater(cv.conviction_score(sog_leg), cv.conviction_score(points_leg))
        # And it's a pure multiplicative discount of the identical base score.
        self.assertAlmostEqual(cv.conviction_score(points_leg),
                                cv.conviction_score(sog_leg) * cv.maturity_weight("points"), places=6)


class TestBuildComboBoard(unittest.TestCase):
    """Completion sprint Parts 3-8: HIGH_CONFIDENCE vs VALUE_COMBINATION
    vs RESEARCH_COMBINATION -- three classes, never mixed, and a combo
    clearing the joint-dependence math is not automatically
    HIGH_CONFIDENCE (the owner's own explicit 5.9%-joint-probability
    counterexample)."""

    def test_never_mixes_classes_and_redundant_legs_never_appear_at_all(self):
        redundant_pair = [
            _leg(player_id="P1", prop="goals", threshold="1+", market="PLAYER_GOALS",
                 conservative_p=0.3, raw_edge=0.05, current_odds=-110),
            _leg(player_id="P1", prop="points", threshold="1+", market="PLAYER_POINTS",
                 conservative_p=0.6, raw_edge=0.05, current_odds=-150),
        ]
        result = cv.build_combo_board(redundant_pair)
        self.assertEqual(result["high_confidence"], [])
        self.assertEqual(result["value"], [])
        self.assertEqual(result["research"], [])

    def test_low_joint_probability_longshot_pair_lands_in_value_never_high_confidence(self):
        # The owner's own example: SOG 3+ (longshot) + Points 1+, joint
        # probability far below any reasonable high-confidence bar.
        legs = [
            _leg(player_id="P1", prop="sog", threshold="3+", market="PLAYER_SOG",
                 conservative_p=0.109, raw_edge=0.05, current_odds=790, coherent_probability=0.109),
            _leg(player_id="P1", prop="points", threshold="1+", market="PLAYER_POINTS",
                 conservative_p=0.51, raw_edge=0.05, current_odds=-102, coherent_probability=0.51),
        ]
        result = cv.build_combo_board(legs)
        self.assertEqual(result["high_confidence"], [])
        self.assertEqual(len(result["value"]), 1)
        combo = result["value"][0]
        self.assertEqual(combo["status"], "VALIDATED")
        self.assertEqual(combo["combo_class"], "VALUE_COMBINATION")
        self.assertLess(combo["joint_probability"], cv.HIGH_CONFIDENCE_COMBO_MIN_JOINT_PROBABILITY)

    def test_two_genuine_favorites_with_positive_value_reach_high_confidence(self):
        legs = [
            _leg(player_id="P1", prop="sog", threshold="3+", market="PLAYER_SOG",
                 conservative_p=0.70, raw_edge=0.05, current_odds=-250, coherent_probability=0.70),
            _leg(player_id="P1", prop="goals", threshold="1+", market="PLAYER_GOALS",
                 conservative_p=0.70, raw_edge=0.05, current_odds=-250, coherent_probability=0.70),
        ]
        result = cv.build_combo_board(legs)
        self.assertEqual(len(result["high_confidence"]), 1)
        combo = result["high_confidence"][0]
        self.assertEqual(combo["combo_class"], "HIGH_CONFIDENCE")
        self.assertGreaterEqual(combo["joint_probability"], cv.HIGH_CONFIDENCE_COMBO_MIN_JOINT_PROBABILITY)
        self.assertGreater(combo["combo_edge"], 0.0)
        for leg in combo["legs"]:
            self.assertGreaterEqual(leg["conservative_probability"],
                                     cv.HIGH_CONFIDENCE_LEG_MIN_CONSERVATIVE_PROBABILITY)

    def test_high_probability_legs_with_negative_combo_edge_do_not_qualify(self):
        # Favorites priced so the simulated combined price is MORE
        # confident than the model's own joint estimate -- real
        # probability, real dependence, but no aggregate value.
        legs = [
            _leg(player_id="P1", prop="sog", threshold="3+", market="PLAYER_SOG",
                 conservative_p=0.70, raw_edge=0.002, current_odds=-400, coherent_probability=0.70),
            _leg(player_id="P1", prop="points", threshold="1+", market="PLAYER_POINTS",
                 conservative_p=0.80, raw_edge=0.002, current_odds=-500, coherent_probability=0.80),
        ]
        result = cv.build_combo_board(legs)
        self.assertEqual(result["high_confidence"], [])
        self.assertEqual(len(result["value"]), 1)
        self.assertLessEqual(result["value"][0]["combo_edge"], 0.0)

    def test_never_pads_any_class(self):
        result = cv.build_combo_board([])
        self.assertEqual(result["high_confidence"], [])
        self.assertEqual(result["value"], [])
        self.assertEqual(result["research"], [])

    def test_display_fields_present_on_every_combo(self):
        legs = [
            _leg(player_id="P1", prop="sog", threshold="3+", market="PLAYER_SOG",
                 conservative_p=0.70, raw_edge=0.05, current_odds=-250, coherent_probability=0.70),
            _leg(player_id="P1", prop="goals", threshold="1+", market="PLAYER_GOALS",
                 conservative_p=0.70, raw_edge=0.05, current_odds=-250, coherent_probability=0.70),
        ]
        combo = cv.build_combo_board(legs)["high_confidence"][0]
        required = {"legs", "status", "joint_probability", "pairwise", "simulated_combo_price",
                    "fair_combo_price", "naive_independent_fair_price", "combo_edge", "combo_class"}
        self.assertTrue(required.issubset(combo.keys()))
        for leg in combo["legs"]:
            self.assertIn("conservative_probability", leg)
            self.assertIn("coherent_probability", leg)
            self.assertIn("fair_odds", leg)
            self.assertIn("current_odds", leg)
            self.assertIn("raw_edge", leg)

    def test_against_real_demo_data_never_raises_and_classes_are_disjoint(self):
        opps = eb.all_opportunities()
        result = cv.build_combo_board(opps)
        self.assertIn("high_confidence", result)
        self.assertIn("value", result)
        self.assertIn("research", result)
        for combo in result["high_confidence"]:
            self.assertEqual(combo["status"], "VALIDATED")
            self.assertEqual(combo["combo_class"], "HIGH_CONFIDENCE")
            self.assertGreaterEqual(combo["joint_probability"], cv.HIGH_CONFIDENCE_COMBO_MIN_JOINT_PROBABILITY)
            self.assertGreater(combo["combo_edge"], 0.0)
        for combo in result["value"]:
            self.assertEqual(combo["status"], "VALIDATED")
            self.assertEqual(combo["combo_class"], "VALUE_COMBINATION")
        for combo in result["research"]:
            self.assertEqual(combo["status"], "JOINT_DEPENDENCE_NOT_VALIDATED")
            self.assertEqual(combo["combo_class"], "RESEARCH_COMBINATION")

    def test_todays_real_slate_has_no_high_confidence_combo_and_that_is_correct(self):
        # Documents today's real, honest result: every BET-grade opportunity
        # today is a POINTS-market longshot, so no combo can clear the
        # individually-high-probability bar. This is the expected "NONE
        # QUALIFY TODAY" outcome (Part 7), not a bug.
        opps = eb.all_opportunities()
        result = cv.build_combo_board(opps)
        self.assertEqual(result["high_confidence"], [])


if __name__ == "__main__":
    unittest.main()
