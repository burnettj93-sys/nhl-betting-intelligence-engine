import random
import unittest

from operational import prediction_evaluation as pe


def _rows(n_games, informative, market="PLAYER_SOG_2PLUS"):
    rng = random.Random(7)
    rows = []
    for g in range(n_games):
        for k in range(2):
            truth = rng.random() < 0.4
            p = (0.8 if truth else 0.2) if informative else 0.4
            rows.append({"market_id": market, "model_version": "v1", "raw_probability": p, "result_status": "WIN" if truth else "LOSS",
                         "game_id": f"g{g}", "player_id": f"p{g * 2 + k}", "market_no_vig_probability": 0.4})
    return rows


class PredictionEvaluationTests(unittest.TestCase):
    def test_informative_model_beats_climatology_with_enough_clusters(self):
        g = pe.evaluate(_rows(80, True))["groups"][0]
        self.assertEqual(g["vs_climatology"]["verdict"], "BETTER_THAN_BASELINE_EVIDENCE")
        self.assertLess(g["vs_climatology"]["brier_diff_model_minus_baseline"], 0)

    def test_uninformative_model_gets_no_credit(self):
        g = pe.evaluate(_rows(80, False))["groups"][0]
        self.assertNotEqual(g["vs_climatology"]["verdict"], "BETTER_THAN_BASELINE_EVIDENCE")
        self.assertNotEqual(g["vs_market"]["verdict"], "BETTER_THAN_BASELINE_EVIDENCE")

    def test_small_samples_never_get_a_verdict(self):
        g = pe.evaluate(_rows(5, True))["groups"][0]
        self.assertEqual(g["vs_climatology"]["verdict"], "INSUFFICIENT_SAMPLE")

    def test_shared_player_dependence_widens_the_interval(self):
        rows = _rows(80, True)
        for r in rows:
            r["player_id"] = "same_player"          # every observation is the same player: only one player cluster
        g = pe.evaluate(rows)["groups"][0]
        self.assertEqual(g["vs_climatology"]["verdict"], "INSUFFICIENT_SAMPLE")

    def test_pending_rows_are_counted_not_scored(self):
        rows = _rows(40, True) + [{"market_id": "PLAYER_SOG_2PLUS", "model_version": "v1", "raw_probability": 0.5, "result_status": "PENDING", "game_id": "gx", "player_id": "px"}]
        g = pe.evaluate(rows)["groups"][0]
        self.assertEqual(g["pending_or_unscored"], 1)
        self.assertEqual(g["settled"], 80)

    def test_market_baseline_absent_is_stated(self):
        rows = _rows(40, True)
        for r in rows:
            r["market_no_vig_probability"] = None
        self.assertEqual(pe.evaluate(rows)["groups"][0]["vs_market"]["verdict"], "NO_MARKET_PRICES_ON_THESE_ROWS")

    def test_markets_and_versions_are_separate_groups(self):
        rows = _rows(10, True, "A") + _rows(10, True, "B")
        self.assertEqual(len(pe.evaluate(rows)["groups"]), 2)


if __name__ == "__main__":
    unittest.main()
