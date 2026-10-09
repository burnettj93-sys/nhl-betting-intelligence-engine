import unittest

from operational import leg_context as lc
from operational.pricing_policy import MIN_GAMES_FOR_PRICING


def skater(games, tier=3, pp=0, recent=None):
    recent = recent if recent is not None else [{"goals": 0, "assists": 0, "shots": 1}] * 5
    return {"position": "F", "usage_tier": tier, "pp_usage": pp, "games_total": games, "reported": None,
            "season": {"games": 3, "goals": 0, "assists": 0, "shots": 3}, "recent_games": recent,
            "projection": {"games_observed": games, "expected": {"points": 0.31, "shots": 1.4, "goals": 0.1, "toi": 14.2}}}


LEG = {"market_family": "PLAYER_POINTS", "participant_id": "1", "label": "X 1+ point"}


class TestLegContext(unittest.TestCase):
    def test_floor_is_forty_games_and_documented(self):
        self.assertEqual(MIN_GAMES_FOR_PRICING, 40)

    def test_small_sample_is_flagged_as_not_confident(self):
        c = lc.for_leg(LEG, {"1": skater(28)}, {})
        self.assertTrue(c["low_sample"])
        self.assertIn("not a confident recommendation", c["uncertainty"])
        self.assertTrue(any("28 NHL games" in x for x in c["lines"]))

    def test_established_player_is_not_flagged(self):
        c = lc.for_leg(LEG, {"1": skater(159, tier=2, pp=2)}, {})
        self.assertFalse(c["low_sample"])
        self.assertTrue(any("estimated power-play usage: some" in x for x in c["lines"]))
        self.assertTrue(any("No reported line or PP unit" in x for x in c["lines"]))
        self.assertIn("variance", c["uncertainty"])

    def test_inferred_role_is_labelled_inferred(self):
        c = lc.for_leg(LEG, {"1": skater(100)}, {})
        self.assertIn("not an assigned line or PP unit", c["lines"][0])

    def test_a_reported_pp_unit_is_shown_separately_with_its_source(self):
        p = skater(100, pp=1)
        p["reported"] = {"status": "REPORTED", "line": "L2", "pp": "PP1", "reported_by": "Test Reporter"}
        c = lc.for_leg(LEG, {"1": p}, {})
        self.assertTrue(any("Reported by Test Reporter: line L2, PP unit PP1" in x for x in c["lines"]))

    def test_unknown_player_and_moneyline_give_nothing(self):
        self.assertIsNone(lc.for_leg(LEG, {}, {}))
        self.assertIsNone(lc.for_leg({"market_family": "MONEYLINE", "participant_id": "T"}, {}, {}))

    def test_unconfirmed_goalie_risk_is_stated(self):
        g = {"season": {"games": 5, "starts": 5, "save_pct": 0.91, "gaa": 2.8}, "start": {"probability": 0.6},
             "confirmation": {"status": "UNCONFIRMED"}, "projection": {"expected_saves": 26.1, "expected_shots_against": 28.6, "sample": {"games": 40}}}
        c = lc.for_leg({"market_family": "GOALIE_SAVES", "participant_id": "9", "label": "G 25+ saves"}, {}, {"9": g})
        self.assertIn("NOT confirmed", " ".join(c["lines"]))
        self.assertIn("not confirmed", c["uncertainty"])


if __name__ == "__main__":
    unittest.main()


class TestChanceRange(unittest.TestCase):
    def test_range_shifts_every_leg_by_the_measured_error(self):
        from dashboard import ui
        lo, hi = ui.chance_range([{"probability": 0.6, "market_family": "PLAYER_SOG_ALTERNATE"}, {"probability": 0.5, "market_family": "PLAYER_POINTS"}])
        self.assertAlmostEqual(lo, 0.52 * 0.42)
        self.assertAlmostEqual(hi, 0.68 * 0.58)

    def test_no_range_for_team_markets_or_missing_probability(self):
        from dashboard import ui
        self.assertIsNone(ui.chance_range([{"probability": 0.6, "market_family": "MONEYLINE"}]))
        self.assertIsNone(ui.chance_range([{"probability": None, "market_family": "PLAYER_POINTS"}]))
