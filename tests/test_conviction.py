"""
Tests for dashboard/conviction.py (Same-Day Demo Experience sprint,
2026-08-31). Covers the Conviction Score's value-weighting behavior
(never probability alone), Top Conviction's real filters, and the
joint-dependence combo builder's redundancy detection and
never-independence-assumption rule.
"""
from __future__ import annotations

import unittest

from dashboard import conviction as cv
from dashboard import eligible_bets as eb


def _leg(player_id="P1", prop="sog", threshold="3+", market="PLAYER_SOG", conservative_p=0.5,
         conservative_edge=0.05, ev=0.05, raw_edge=0.05, confidence="HIGH", decision="BET",
         current_odds=-120, coherent_probability=0.5, starter_certainty=None):
    return {
        "player_id": player_id, "player": "Test Player", "prop": prop, "threshold": threshold,
        "market": market, "conservative_probability": conservative_p,
        "conservative_edge": conservative_edge, "ev": ev, "raw_edge": raw_edge,
        "confidence": confidence, "decision": decision, "current_odds": current_odds,
        "coherent_probability": coherent_probability, "actionable": True,
        "starter_certainty": starter_certainty,
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


class TestBuildHighConfidenceCombos(unittest.TestCase):
    def test_never_mixes_validated_and_not_validated(self):
        redundant_pair = [
            _leg(player_id="P1", prop="goals", threshold="1+", market="PLAYER_GOALS",
                 conservative_p=0.3, raw_edge=0.05, current_odds=-110),
            _leg(player_id="P1", prop="points", threshold="1+", market="PLAYER_POINTS",
                 conservative_p=0.6, raw_edge=0.05, current_odds=-150),
        ]
        result = cv.build_high_confidence_combos(redundant_pair)
        # Redundant legs must collapse out entirely -- never appear as a combo at all.
        self.assertEqual(result["validated"], [])
        self.assertEqual(result["not_validated"], [])

    def test_validated_pair_produces_one_validated_combo(self):
        legs = [
            _leg(player_id="P1", prop="sog", threshold="3+", market="PLAYER_SOG",
                 conservative_p=0.5, raw_edge=0.05, current_odds=-120, coherent_probability=0.5),
            _leg(player_id="P1", prop="goals", threshold="1+", market="PLAYER_GOALS",
                 conservative_p=0.3, raw_edge=0.05, current_odds=140, coherent_probability=0.3),
        ]
        result = cv.build_high_confidence_combos(legs)
        self.assertEqual(len(result["validated"]), 1)
        combo = result["validated"][0]
        self.assertEqual(combo["status"], "VALIDATED")
        self.assertIsNotNone(combo["joint_probability"])

    def test_never_pads_below_max_combos(self):
        result = cv.build_high_confidence_combos([])
        self.assertEqual(result["validated"], [])
        self.assertEqual(result["not_validated"], [])

    def test_against_real_demo_data_never_raises(self):
        opps = eb.all_opportunities()
        result = cv.build_high_confidence_combos(opps)
        self.assertIn("validated", result)
        self.assertIn("not_validated", result)
        for combo in result["validated"]:
            self.assertEqual(combo["status"], "VALIDATED")
        for combo in result["not_validated"]:
            self.assertEqual(combo["status"], "JOINT_DEPENDENCE_NOT_VALIDATED")


if __name__ == "__main__":
    unittest.main()
