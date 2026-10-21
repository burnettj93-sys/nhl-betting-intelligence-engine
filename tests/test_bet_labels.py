"""The complete name of a bet (operational/bet_labels.py): PLAYER — MARKET THRESHOLD — PRICE, the same everywhere a bet is shown. Never 'Chytil 2+'."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from operational import bet_labels as bl
from research.real_market_parlay import engine

ROOT = Path(__file__).resolve().parent.parent


class TestCompleteLabels(unittest.TestCase):
    def test_the_owners_examples_exactly(self):
        self.assertEqual(bl.bet_label("Filip Chytil", "PLAYER_SOG_ALTERNATE", 2, -130.0), "Filip Chytil — SHOTS ON GOAL 2+ — -130")
        self.assertEqual(bl.bet_label("Jamie Oleksiak", "PLAYER_GOALS", 1, 2500.0), "Jamie Oleksiak — GOALS 1+ — +2500")
        self.assertEqual(bl.bet_label("VAN", "MONEYLINE", None, 240.0), "VAN — MONEYLINE — +240")
        self.assertEqual(bl.bet_label("Quinn Hughes", "PLAYER_POINTS", 2, 350.0), "Quinn Hughes — POINTS 2+ — +350")
        self.assertEqual(bl.bet_label("Some Goalie", "GOALIE_SAVES", 25, -125.0), "Some Goalie — SAVES 25+ — -125")

    def test_both_shots_families_are_shots_on_goal_and_the_market_key_is_the_archived_one(self):
        self.assertEqual(bl.market_name("PLAYER_SOG", 3), "SHOTS ON GOAL 3+")
        self.assertEqual(bl.market_name("PLAYER_SOG_ALTERNATE", 3), "SHOTS ON GOAL 3+")
        self.assertEqual(bl.MARKET_KEYS["PLAYER_SOG_ALTERNATE"], "player_shots_on_goal_alternate")
        self.assertEqual(bl.MARKET_KEYS["PLAYER_GOALS"], "player_goal_scorer_anytime")
        self.assertEqual(bl.MARKET_KEYS["MONEYLINE"], "h2h")

    def test_the_price_is_left_off_when_not_given_and_an_unknown_family_is_shown_as_itself(self):
        self.assertEqual(bl.bet_label("X", "PLAYER_POINTS", 1), "X — POINTS 1+")
        self.assertEqual(bl.market_name("PLAYER_HITS", 3), "PLAYER HITS 3+")                         # never guessed
        self.assertEqual(bl.market_name("PUCK_LINE", -1.5), "PUCK LINE -1.5")

    def test_the_market_is_never_inferred_from_the_price_or_the_threshold(self):
        # the same threshold and price under two families read differently: the family is the only thing that names the market
        self.assertNotEqual(bl.bet_label("P", "PLAYER_GOALS", 1, 150.0), bl.bet_label("P", "PLAYER_POINTS", 1, 150.0))

    def test_a_leg_dict_of_any_surface_is_named_completely(self):
        self.assertEqual(bl.leg_dict_label({"label": "Filip Chytil 2+ shots on goal", "market_family": "PLAYER_SOG_ALTERNATE", "participant_name": "Filip Chytil", "american_price": -130}),
                         "Filip Chytil — SHOTS ON GOAL 2+ — -130")                                        # an older frozen ticket leg: the threshold is read from its own label
        self.assertEqual(bl.leg_dict_label({"label": "x", "market_family": "PLAYER_SOG_ALTERNATE", "participant_name": "Filip Chytil", "threshold": 3, "american_price": 225}), "Filip Chytil — SHOTS ON GOAL 3+ — +225")
        self.assertEqual(bl.leg_dict_label({"label": "Max Sasson to score a goal (anytime)", "market_family": "PLAYER_GOALS", "participant_name": "Max Sasson", "american_price": 800}), "Max Sasson — GOALS 1+ — +800")
        self.assertEqual(bl.leg_dict_label({"label": "VAN to win", "market_family": "MONEYLINE", "participant_name": "VAN", "american_price": 240}), "VAN — MONEYLINE — +240")
        self.assertEqual(bl.leg_dict_label({"label": "x", "participant": "Quinn Hughes", "code": "PTS", "threshold": 1, "price": {"american": 155}}), "Quinn Hughes — POINTS 1+ — +155")        # an Eggy candidate leg
        self.assertEqual(bl.leg_dict_label({"label": "x", "participant": "A", "market_key": "player_shots_on_goal_alternate", "threshold": "2+", "price": {"american": -120}}), "A — SHOTS ON GOAL 2+ — -120")

    def test_a_leg_that_names_no_market_keeps_its_own_label_rather_than_a_guess(self):
        self.assertEqual(bl.leg_dict_label({"label": "mystery"}), "mystery")
        self.assertEqual(bl.leg_dict_label({"label": "A thing", "participant_name": "A"}), "A thing")

    def test_the_engine_re_exports_the_same_functions(self):
        self.assertIs(engine.bet_label, bl.bet_label)
        self.assertIs(engine.market_name, bl.market_name)
        self.assertIs(engine.MARKET_KEYS, bl.MARKET_KEYS)

    def test_the_module_is_standard_library_only_because_the_hosted_app_imports_it(self):
        src = (ROOT / "operational" / "bet_labels.py").read_text()
        imports = re.findall(r"^(?:from|import) (\S+)", src, re.M)
        self.assertEqual(sorted(set(imports)), ["__future__", "re"])


class TestNoShortLabelsLeft(unittest.TestCase):
    """The short form ('Chytil 2+', 'Name 2+ (sog)') is what the owner banned; the surfaces that used to build it now call the label module."""
    SURFACES = ("dashboard/pages/23_Ledger.py", "dashboard/ui.py", "dashboard/tickets_view.py", "operational/daily_review.py", "operational/daily_postmortem.py", "operational/learning_postmortem.py",
                "operational/learning_cohort.py")
    SHORT = re.compile(r"\{[^{}]*(?:participant_name|name)[^{}]*\}\s*\{[^{}]*threshold[^{}]*\}\+")

    def test_no_surface_builds_name_threshold_plus(self):
        for rel in self.SURFACES:
            src = (ROOT / rel).read_text()
            self.assertFalse(self.SHORT.search(src), rel)

    def test_the_surfaces_use_the_label_module(self):
        for rel in ("dashboard/pages/23_Ledger.py", "dashboard/tickets_view.py", "operational/daily_review.py", "operational/daily_postmortem.py", "dashboard/ui.py"):
            self.assertIn("bet_labels", (ROOT / rel).read_text(), rel)
        self.assertIn("label_of", (ROOT / "operational/learning_postmortem.py").read_text())


if __name__ == "__main__":
    unittest.main()
