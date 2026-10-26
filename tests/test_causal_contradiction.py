"""One underlying hockey fact seen by several detectors is ONE contradiction; independent facts still compound (operational/eggy_intelligence.py CAUSE_FAMILY, combine_contradiction)."""
from __future__ import annotations

import unittest

from operational import eggy_intelligence as ei


def F(*pairs):
    return [{"name": n, "level": lv, "text": n} for n, lv in pairs]


class TestOneFactCountsOnce(unittest.TestCase):
    def test_the_same_role_change_seen_twice_is_not_high(self):
        self.assertEqual(ei.combine_contradiction(F(("ROLE_ASSUMPTION", "MODERATE"), ("POSTMORTEM_ROLE_CAUTION", "MODERATE"))), "MODERATE")
        self.assertEqual(ei.combine_contradiction(F(("TOI_DROP", "MODERATE"), ("POSTMORTEM_ROLE_CAUTION", "MODERATE"), ("LOW_USAGE", "MODERATE"))), "MODERATE")

    def test_the_same_power_play_loss_seen_twice_is_not_high(self):
        self.assertEqual(ei.combine_contradiction(F(("POWER_PLAY_ASSUMPTION", "MODERATE"), ("PP_ROLE_LOST", "MODERATE"))), "MODERATE")

    def test_production_below_the_model_seen_twice_is_not_high(self):
        self.assertEqual(ei.combine_contradiction(F(("MODEL_ABOVE_RECORD", "MODERATE"), ("RECENT_FORM_BELOW_MODEL", "MODERATE"))), "MODERATE")

    def test_the_same_team_picture_seen_three_ways_is_one_doubt(self):
        self.assertEqual(ei.combine_contradiction(F(("HOCKEY_PICTURE_AGAINST", "MODERATE"), ("INDICATORS_FAVOUR_OPPONENT", "MODERATE"), ("GAME_ENVIRONMENT", "MODERATE"))), "MODERATE")

    def test_a_family_takes_its_strongest_flag_not_a_sum(self):
        self.assertEqual(ei.combine_contradiction(F(("ROLE_ASSUMPTION", "MODERATE"), ("POSTMORTEM_ROLE_CAUTION", "HIGH"))), "HIGH")
        self.assertEqual(ei.combine_contradiction(F(("ROLE_ASSUMPTION", "HIGH"), ("TOI_DROP", "HIGH"))), "HIGH")            # one fact, not two HIGH doubts


class TestIndependentFactsStillCompound(unittest.TestCase):
    def test_a_role_problem_and_a_different_hockey_problem_are_high(self):
        self.assertEqual(ei.combine_contradiction(F(("ROLE_ASSUMPTION", "MODERATE"), ("BACK_TO_BACK", "MODERATE"))), "HIGH")
        self.assertEqual(ei.combine_contradiction(F(("TOI_DROP", "MODERATE"), ("HOCKEY_PICTURE_AGAINST", "MODERATE"))), "HIGH")

    def test_even_strength_minutes_and_power_play_deployment_are_different_evidence(self):
        self.assertEqual(ei.combine_contradiction(F(("TOI_DROP", "MODERATE"), ("PP_ROLE_LOST", "MODERATE"))), "HIGH")

    def test_two_high_independent_problems_are_severe(self):
        self.assertEqual(ei.combine_contradiction(F(("ROLE_ASSUMPTION", "HIGH"), ("HOCKEY_PICTURE_AGAINST", "HIGH"))), "SEVERE")

    def test_an_unregistered_flag_is_its_own_family(self):
        self.assertEqual(ei.combine_contradiction(F(("SOMETHING_NEW", "MODERATE"), ("SOMETHING_ELSE", "MODERATE"))), "HIGH")

    def test_a_postmortem_power_play_caution_is_a_power_play_fact(self):
        pp = {"name": "POSTMORTEM_ROLE_CAUTION", "level": "MODERATE", "family": "PP_ROLE", "text": ""}
        self.assertEqual(ei.combine_contradiction([pp, {"name": "TOI_DROP", "level": "MODERATE", "text": ""}]), "HIGH")
        self.assertEqual(ei.combine_contradiction([pp, {"name": "PP_ROLE_LOST", "level": "MODERATE", "text": ""}]), "MODERATE")


if __name__ == "__main__":
    unittest.main()
