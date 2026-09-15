"""Tests for research/game_edge_parlay/engine.py. All opportunity dicts
below are synthetic, matching the real field vocabulary used across this
project's dashboard/conviction.py -- never real player/game data (no
2026-27 games have been played yet)."""
import unittest
from unittest import mock

from dashboard import conviction as conv
from research.game_edge_parlay import engine


def _leg(**overrides):
    base = dict(
        decision="BET", actionable=True, raw_edge=0.06, conservative_edge=0.05,
        confidence="HIGH", current_odds=-150, conservative_probability=0.65,
        coherent_probability=0.66, player_id="p1", team="EDM", opponent="VAN",
        prop="sog", threshold="3+", market="PLAYER_SOG", starter_certainty=None,
    )
    base.update(overrides)
    return base


class TestGameEligibleLegs(unittest.TestCase):
    def test_filters_to_one_game_only(self):
        opps = [_leg(team="EDM"), _leg(team="TOR", opponent="MTL", player_id="p2")]
        legs = engine.game_eligible_legs(opps, "EDM", "VAN")
        self.assertEqual(len(legs), 1)
        self.assertEqual(legs[0]["team"], "EDM")

    def test_excludes_non_bet_or_watch_decisions(self):
        opps = [_leg(decision="PASS")]
        self.assertEqual(engine.game_eligible_legs(opps, "EDM", "VAN"), [])


class TestLegPriorityTier(unittest.TestCase):
    def test_sog_and_saves_are_tier_1(self):
        self.assertEqual(engine.leg_priority_tier(_leg(prop="sog")), 1)
        self.assertEqual(engine.leg_priority_tier(_leg(prop="saves")), 1)

    def test_other_props_are_tier_2(self):
        self.assertEqual(engine.leg_priority_tier(_leg(prop="points")), 2)


class TestLegPairDependence(unittest.TestCase):
    def test_same_player_delegates_to_conviction_module(self):
        a = _leg(player_id="p1", prop="sog", threshold="3+")
        b = _leg(player_id="p1", prop="goals", threshold="1+")
        with mock.patch.object(conv, "_RHO_LOOKUP", {("sog", 3, "goals", 1): 0.30}):
            result = engine.leg_pair_dependence(a, b)
        self.assertEqual(result["status"], "VALIDATED")
        self.assertIsNotNone(result["rho"])

    def test_cross_player_shooter_vs_opposing_goalie_saves_is_conservative_bound(self):
        shooter = _leg(player_id="p1", prop="sog", team="EDM", conservative_probability=0.6)
        goalie = _leg(player_id="p2", prop="saves", team="VAN", conservative_probability=0.5)
        result = engine.leg_pair_dependence(shooter, goalie)
        self.assertEqual(result["status"], "CONSERVATIVE_BOUND")
        lo, hi = jm_bounds(0.6, 0.5)
        self.assertGreaterEqual(result["joint_probability"], lo - 1e-9)
        self.assertLessEqual(result["joint_probability"], hi + 1e-9)

    def test_unrelated_cross_player_pair_is_independent(self):
        a = _leg(player_id="p1", prop="points", team="EDM")
        b = _leg(player_id="p2", prop="assists", team="EDM")
        result = engine.leg_pair_dependence(a, b)
        self.assertEqual(result["status"], "INDEPENDENT_ASSUMED")
        self.assertAlmostEqual(result["joint_probability"], a["conservative_probability"] * b["conservative_probability"])


def jm_bounds(p_a, p_b):
    return max(0.0, p_a + p_b - 1.0), min(p_a, p_b)


class TestBuildGameEdgeParlay(unittest.TestCase):
    def test_fewer_than_three_eligible_legs_never_qualifies(self):
        opps = [_leg(player_id="p1"), _leg(player_id="p2")]
        result = engine.build_game_edge_parlay(opps, "EDM", "VAN")
        self.assertEqual(result["status"], "NO_QUALIFYING_GAME_EDGE_PARLAY")

    def test_never_manufactures_when_nothing_clears_the_bar(self):
        # Three legs, but every pairwise combo is independent with low
        # enough individual probabilities that no 3-leg combo clears
        # MIN_ACCEPTABLE_JOINT_PROBABILITY.
        opps = [
            _leg(player_id="p1", prop="sog", conservative_probability=0.4, current_odds=-120),
            _leg(player_id="p2", prop="points", conservative_probability=0.4, current_odds=-120),
            _leg(player_id="p3", prop="assists", conservative_probability=0.4, current_odds=-120),
        ]
        result = engine.build_game_edge_parlay(opps, "EDM", "VAN")
        self.assertEqual(result["status"], "NO_QUALIFYING_GAME_EDGE_PARLAY")

    def test_qualifying_3leg_combo_is_returned(self):
        opps = [
            _leg(player_id="p1", prop="sog", conservative_probability=0.85, current_odds=-400, coherent_probability=0.85),
            _leg(player_id="p2", prop="points", conservative_probability=0.85, current_odds=-400, coherent_probability=0.85),
            _leg(player_id="p3", prop="assists", conservative_probability=0.85, current_odds=-400, coherent_probability=0.85),
        ]
        result = engine.build_game_edge_parlay(opps, "EDM", "VAN")
        self.assertEqual(result["status"], "QUALIFIED")
        self.assertEqual(result["recommended_legs"], 3)
        self.assertGreaterEqual(result["combo"].joint_probability, engine.MIN_ACCEPTABLE_JOINT_PROBABILITY)

    def test_redundant_pair_is_excluded_from_combo_search(self):
        # goals 1+ and points 1+ for the SAME player are a logical
        # identity (Part 20/40) -- must never form a "combo" together.
        opps = [
            _leg(player_id="p1", prop="goals", threshold="1+", conservative_probability=0.85, current_odds=-400, coherent_probability=0.85),
            _leg(player_id="p1", prop="points", threshold="1+", conservative_probability=0.85, current_odds=-400, coherent_probability=0.85),
            _leg(player_id="p2", prop="sog", conservative_probability=0.85, current_odds=-400, coherent_probability=0.85),
            _leg(player_id="p3", prop="assists", conservative_probability=0.85, current_odds=-400, coherent_probability=0.85),
        ]
        result = engine.build_game_edge_parlay(opps, "EDM", "VAN")
        # A valid 3-leg combo must still be found using the non-redundant legs.
        self.assertEqual(result["status"], "QUALIFIED")
        combo_player_ids = sorted(l["player_id"] for l in result["combo"].legs)
        # The redundant goals/points pair for p1 should never BOTH appear together.
        self.assertFalse({"p1"} <= set(combo_player_ids) and combo_player_ids.count("p1") > 1)

    def test_fourth_leg_added_only_when_it_keeps_the_combo_above_the_floor(self):
        strong_legs = [
            _leg(player_id="p1", prop="sog", conservative_probability=0.9, current_odds=-500, coherent_probability=0.9),
            _leg(player_id="p2", prop="points", conservative_probability=0.9, current_odds=-500, coherent_probability=0.9),
            _leg(player_id="p3", prop="assists", conservative_probability=0.9, current_odds=-500, coherent_probability=0.9),
            _leg(player_id="p4", prop="goals", threshold="1+", conservative_probability=0.9, current_odds=-500, coherent_probability=0.9),
        ]
        result = engine.build_game_edge_parlay(strong_legs, "EDM", "VAN")
        self.assertEqual(result["status"], "QUALIFIED")
        self.assertIn(result["recommended_legs"], (3, 4))

    def test_weak_fourth_leg_is_rejected_and_3leg_recommended(self):
        legs = [
            _leg(player_id="p1", prop="sog", conservative_probability=0.85, current_odds=-400, coherent_probability=0.85),
            _leg(player_id="p2", prop="points", conservative_probability=0.85, current_odds=-400, coherent_probability=0.85),
            _leg(player_id="p3", prop="assists", conservative_probability=0.85, current_odds=-400, coherent_probability=0.85),
            # A weak 4th leg that would drag joint probability below the floor.
            _leg(player_id="p4", prop="goals", threshold="1+", conservative_probability=0.3, current_odds=-120, coherent_probability=0.3),
        ]
        result = engine.build_game_edge_parlay(legs, "EDM", "VAN")
        self.assertEqual(result["status"], "QUALIFIED")
        self.assertEqual(result["recommended_legs"], 3)


class TestCalibrationSnapshot(unittest.TestCase):
    def test_reports_gap_to_target(self):
        combo = engine.ComboResult(legs=[], status="VALIDATED", joint_probability=0.6, pairwise=[],
                                    estimated_combo_price=150, fair_combo_price=140, combo_edge=0.05)
        snap = engine.calibration_snapshot(combo)
        self.assertAlmostEqual(snap["gap_to_target"], 0.6 - engine.TARGET_JOINT_PROBABILITY)


if __name__ == "__main__":
    unittest.main()
