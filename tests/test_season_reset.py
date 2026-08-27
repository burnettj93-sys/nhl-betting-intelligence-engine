"""
Spec item 7: season-specific counters (games-played-this-season, which
feeds the CI-maturity narrowing in models/combined_model.py) must reset at
a season boundary, not accumulate forever across seasons. Also covers the
paired Elo season-regression behavior (models/elo_model.py's
maybe_regress_new_season), which fires on the same boundary.
"""
import unittest

import config
from models.combined_model import CombinedMoneylineModel
from models.elo_model import EloModel
from tests.helpers import Fixture, make_test_db, t


class TestSeasonReset(unittest.TestCase):
    def setUp(self):
        self.conn, self.path = make_test_db()
        self.fx = Fixture(self.conn)  # game_id=1, season "2025-DEMO"
        self.fx.set_goalie_status(1, "TOR", "TOR_G1", "CONFIRMED", effective_at=t(9, hour=17))
        self.fx.set_goalie_status(1, "BOS", "BOS_G1", "CONFIRMED", effective_at=t(9, hour=17))

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_games_played_this_season_resets_on_new_season_label(self):
        model = CombinedMoneylineModel(teams=["TOR", "BOS"])
        model._games_played_this_season["TOR"] = 25
        model._games_played_this_season["BOS"] = 25
        model._current_season = "2025-DEMO"

        model._maybe_new_season("2025-DEMO")   # same season -> no reset
        self.assertEqual(model._games_played_this_season["TOR"], 25)

        model._maybe_new_season("2026-DEMO")   # new season -> reset
        self.assertEqual(model._games_played_this_season["TOR"], 0)
        self.assertEqual(model._games_played_this_season["BOS"], 0)

    def test_first_call_sets_season_without_resetting(self):
        # a brand-new model has never seen a season yet; the very first
        # _maybe_new_season call must not spuriously "reset" (there was
        # nothing to reset from -- _current_season starts as None).
        model = CombinedMoneylineModel(teams=["TOR", "BOS"])
        model._games_played_this_season["TOR"] = 5   # shouldn't happen pre-season, but confirm no wipe
        model._maybe_new_season("2025-DEMO")
        self.assertEqual(model._games_played_this_season["TOR"], 5)
        self.assertEqual(model._current_season, "2025-DEMO")

    def test_ci_half_width_reflects_reset_maturity_after_new_season(self):
        model = CombinedMoneylineModel(teams=["TOR", "BOS"])
        model._current_season = "2025-DEMO"
        model._games_played_this_season["TOR"] = config.UNCERTAINTY_BAND_GAMES_TO_MATURITY
        model._games_played_this_season["BOS"] = config.UNCERTAINTY_BAND_GAMES_TO_MATURITY

        prediction_time = t(9, hour=18, minute=30)
        # same season: matured -> narrow CI
        fs_same_season = model._build_feature_snapshot(self.conn, 1, prediction_time)
        self.assertAlmostEqual(fs_same_season["ci_half_width_base"], config.MIN_UNCERTAINTY_BAND_HALF_WIDTH, places=6)

        # simulate rolling into a new season by resetting counters directly
        # (mirrors what _maybe_new_season does) and rebuilding the snapshot
        model._games_played_this_season["TOR"] = 0
        model._games_played_this_season["BOS"] = 0
        fs_new_season = model._build_feature_snapshot(self.conn, 1, prediction_time)
        self.assertAlmostEqual(fs_new_season["ci_half_width_base"], config.BASE_UNCERTAINTY_BAND_HALF_WIDTH, places=6)


class TestEloSeasonRegression(unittest.TestCase):
    def test_ratings_regress_toward_start_on_new_season(self):
        model = EloModel(teams=["TOR", "BOS"])
        model.ratings["TOR"] = 1700.0
        model.ratings["BOS"] = 1300.0
        model.maybe_regress_new_season("2025-DEMO")   # first call: just sets season, no regression
        self.assertEqual(model.ratings["TOR"], 1700.0)

        model.maybe_regress_new_season("2026-DEMO")   # new season: regress toward ELO_START
        expected_tor = 1700.0 + (config.ELO_START - 1700.0) * config.ELO_SEASON_REGRESSION
        expected_bos = 1300.0 + (config.ELO_START - 1300.0) * config.ELO_SEASON_REGRESSION
        self.assertAlmostEqual(model.ratings["TOR"], expected_tor, places=6)
        self.assertAlmostEqual(model.ratings["BOS"], expected_bos, places=6)

    def test_same_season_label_does_not_regress(self):
        model = EloModel(teams=["TOR", "BOS"])
        model.ratings["TOR"] = 1650.0
        model.maybe_regress_new_season("2025-DEMO")
        model.maybe_regress_new_season("2025-DEMO")
        self.assertEqual(model.ratings["TOR"], 1650.0)


if __name__ == "__main__":
    unittest.main()
