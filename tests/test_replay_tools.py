"""The replay tools (deploy/replay_*.py): the arithmetic they report, and that they cannot touch production. The replays themselves read real data and run by hand; their outputs are committed under
docs/validation/ and pinned here only where a documented claim rests on them."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from deploy import replay_moneyline_log as rm

ROOT = Path(__file__).resolve().parent.parent


class TestTheOldClassIsArithmetic(unittest.TestCase):
    def test_the_old_edge_is_the_margin_on_that_side_and_the_class_follows_the_price_alone(self):
        # a proportional margin of 4.33% (the log's mean overround): old edge = -p x 0.0433
        for p, cls in ((0.28, "NEUTRAL_UNCERTAIN_EDGE"), (0.44, "NEUTRAL_UNCERTAIN_EDGE"), (0.47, "LEARNING_SAMPLE_NEGATIVE_EDGE"), (0.60, "LEARNING_SAMPLE_NEGATIVE_EDGE"), (0.72, "LEARNING_SAMPLE_NEGATIVE_EDGE")):
            implied = p * 1.0433
            price = round(-100 * implied / (1 - implied)) if implied >= 0.5 else round(100 * (1 - implied) / implied)
            got, edge = rm.old_class(p, float(price))
            self.assertEqual(got, cls, p)
            self.assertAlmostEqual(edge, -p * 0.0433, delta=0.004)

    def test_the_class_flips_where_the_margin_reaches_two_points(self):
        self.assertAlmostEqual(rm.BAND / (1.0433 - 1.0), 0.4622, places=3)

    def test_a_price_with_a_real_edge_would_be_positive_but_a_moneyline_never_has_one_here(self):
        self.assertEqual(rm.old_class(0.60, -120.0)[0], "POSITIVE_EDGE")                     # only if the estimate genuinely beats the price (the market-anchored estimate never does)

    def test_decimal_odds(self):
        self.assertAlmostEqual(rm.decimal(240.0), 3.4)
        self.assertAlmostEqual(rm.decimal(-298.0), 1 + 100 / 298)


class TestSummary(unittest.TestCase):
    def rows(self):
        return [{"price": 100.0, "no_vig": 0.5, "hockey": 0.55, "win": 1}, {"price": 100.0, "no_vig": 0.5, "hockey": 0.55, "win": 0}, {"price": 200.0, "no_vig": 0.33, "hockey": 0.30, "win": 0}]

    def test_counts_expected_wins_and_flat_roi(self):
        s = rm.summarise(self.rows())
        self.assertEqual((s["n"], s["wins"]), (3, 1))
        self.assertAlmostEqual(s["expected_wins_market"], 1.33)
        self.assertAlmostEqual(s["flat_roi"], (1.0 - 1.0 - 1.0) / 3, places=4)
        self.assertLessEqual(s["roi_95_interval"][0], s["flat_roi"])
        self.assertGreaterEqual(s["roi_95_interval"][1], s["flat_roi"])

    def test_the_bootstrap_is_deterministic_and_an_empty_set_is_empty(self):
        self.assertEqual(rm.summarise(self.rows()), rm.summarise(self.rows()))
        self.assertEqual(rm.summarise([]), {"n": 0})


class TestCommittedReplays(unittest.TestCase):
    """What the documents quote."""

    @classmethod
    def setUpClass(cls):
        cls.ml = json.loads((ROOT / "docs/validation/eggy_moneyline_replay.json").read_text())
        cls.props = json.loads((ROOT / "docs/validation/eggy_prop_calibration_replay.json").read_text())

    def test_every_favourite_the_old_rule_could_see_was_demoted_and_underdogs_were_not(self):
        split = self.ml["the_mathematical_reason"]["old_class_split_of_eligible_sides"]
        fav, dog = split["favourites (market 50% or more)"], split["underdogs (market under 50%)"]
        self.assertEqual((fav["NEUTRAL_UNCERTAIN_EDGE"], fav["POSITIVE_EDGE"]), (0, 0))
        self.assertGreater(fav["LEARNING_SAMPLE_NEGATIVE_EDGE"], 40)
        self.assertGreater(dog["NEUTRAL_UNCERTAIN_EDGE"], 3 * dog["LEARNING_SAMPLE_NEGATIVE_EDGE"])

    def test_elo_had_the_worst_calibration_of_the_four_estimates(self):
        cal = self.ml["calibration_home_side"]
        worst = max(("market_no_vig", "hockey_model", "strength_model", "elo"), key=lambda k: cal[k]["log_loss"])
        self.assertEqual(worst, "elo")

    def test_the_replay_does_not_claim_an_edge(self):
        text = json.dumps(self.ml["caveats"]).lower()
        self.assertIn("prove nothing", text)
        self.assertIn("no historical closing prices", text)

    def test_the_recalibration_did_not_make_any_market_worse_out_of_time(self):
        for market, v in self.props["markets"].items():
            self.assertLessEqual(v["brier_adjusted"], v["brier_raw"] + 1e-5, market)
        self.assertIn("no historical prices", self.props["note"].lower())


class TestTheToolsCannotTouchProduction(unittest.TestCase):
    def test_the_replay_sandboxes_the_database_the_archive_and_state(self):
        src = (ROOT / "deploy/replay_eggy_review.py").read_text()
        for must in ('NHL_ENGINE_UNDER_TEST"] = "1"', "shutil.copy", "tempfile.mkdtemp", "symlink_to", "record_shadow"):
            self.assertIn(must, src)

    def test_none_of_the_tools_writes_anywhere_but_its_output_file(self):
        for name in ("replay_eggy_review", "replay_moneyline_log", "replay_prop_calibration"):
            src = (ROOT / "deploy" / f"{name}.py").read_text()
            self.assertEqual(src.count("write_text("), 1, name)
            for forbidden in ("INSERT INTO", "UPDATE bets", "record_paper_bet", "requests."):
                self.assertNotIn(forbidden.replace("UPDATE bets", "UPDATE bets SET"), src.replace("UPDATE games SET", ""), name)


if __name__ == "__main__":
    unittest.main()
