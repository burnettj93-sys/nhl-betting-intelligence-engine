"""
Spec item 7 (deliberate choice): EloModel.update() must use the BASE Elo
expectation (team ratings + home-ice only) — never the fully
player+goalie+rest-adjusted pregame probability the pricing engine actually
prices against. Pins that rule down so a future refactor can't quietly
start double-counting player/goalie signal into the team rating.
"""
import unittest

import config
from models.elo_model import EloModel


class TestEloUpdateRule(unittest.TestCase):
    def setUp(self):
        self.model = EloModel(teams=["TOR", "BOS"])

    def test_update_ignores_extra_adjustments_present_in_win_probability(self):
        # win_probability() DOES accept extra_home_adj/extra_away_adj (used
        # elsewhere for what-if / display purposes) but update() must never
        # pass them through -- verify the actual rating delta matches the
        # BASE expectation, not a hand-computed adjusted one.
        base_p_home = self.model.win_probability("TOR", "BOS")
        adjusted_p_home = self.model.win_probability(
            "TOR", "BOS", extra_home_adj=80.0, extra_away_adj=-40.0
        )
        self.assertNotAlmostEqual(base_p_home, adjusted_p_home, places=3)

        before_tor = self.model.ratings["TOR"]
        before_bos = self.model.ratings["BOS"]
        self.model.update("TOR", "BOS", home_won=True)

        expected_delta = config.ELO_K_FACTOR * (1.0 - base_p_home)
        self.assertAlmostEqual(self.model.ratings["TOR"] - before_tor, expected_delta, places=6)
        self.assertAlmostEqual(before_bos - self.model.ratings["BOS"], expected_delta, places=6)

        # and explicitly NOT the adjusted-probability delta
        wrong_delta = config.ELO_K_FACTOR * (1.0 - adjusted_p_home)
        self.assertNotAlmostEqual(self.model.ratings["TOR"] - before_tor, wrong_delta, places=6)

    def test_update_requires_the_base_expectation_config_flag(self):
        # the assert inside update() is the enforcement mechanism -- confirm
        # the flag it checks is actually set True in the shipped config.
        self.assertTrue(config.ELO_UPDATES_ON_BASE_EXPECTATION)

    def test_upset_win_raises_underdog_rating_more_than_expected_win_would(self):
        # sanity check on the direction of the update, independent of the
        # exact K-factor math above
        model_a = EloModel(teams=["TOR", "BOS"])
        model_a.ratings["BOS"] = 1650.0   # BOS heavily favored on the road... well, home-ice aside
        before = model_a.ratings["TOR"]
        model_a.update("TOR", "BOS", home_won=True)   # underdog TOR wins
        upset_delta = model_a.ratings["TOR"] - before

        model_b = EloModel(teams=["TOR", "BOS"])   # evenly matched
        before_b = model_b.ratings["TOR"]
        model_b.update("TOR", "BOS", home_won=True)
        expected_delta = model_b.ratings["TOR"] - before_b

        self.assertGreater(upset_delta, expected_delta)

    def test_home_and_away_rating_deltas_are_symmetric(self):
        before_tor = self.model.ratings["TOR"]
        before_bos = self.model.ratings["BOS"]
        self.model.update("TOR", "BOS", home_won=False)
        gained = self.model.ratings["BOS"] - before_bos
        lost = before_tor - self.model.ratings["TOR"]
        self.assertAlmostEqual(gained, lost, places=9)


if __name__ == "__main__":
    unittest.main()
