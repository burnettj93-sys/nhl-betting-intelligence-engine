"""
Tests for dashboard/eligible_bets.py (Same-Day Demo Experience sprint,
2026-08-31). Covers Part 7's exact threshold-actionability rules, the
"all eligible bets connected to a team" aggregation (Part 4, P0), and
that this module never touches dashboard/demo_data.py's own already-
tested build_demo_opportunities() function.
"""
from __future__ import annotations

import unittest

from dashboard import demo_data as dd
from dashboard import eligible_bets as eb


class TestThresholdRulesMatchPart7(unittest.TestCase):
    """The exact threshold-actionability facts established in the
    Preseason Operational Readiness Closure sprint -- must never drift."""

    def test_sog_actionable_is_exactly_2_3_4_5(self):
        self.assertEqual(eb.PROP_VALID_THRESHOLDS["sog"], (2, 3, 4, 5))

    def test_sog_1_and_6_7_8_are_not_actionable(self):
        self.assertEqual(eb.PROP_NOT_ACTIONABLE_THRESHOLDS["sog"], (1, 6, 7, 8))

    def test_goals_actionable_is_exactly_1(self):
        self.assertEqual(eb.PROP_VALID_THRESHOLDS["goals"], (1,))

    def test_assists_actionable_is_exactly_1_2(self):
        self.assertEqual(eb.PROP_VALID_THRESHOLDS["assists"], (1, 2))

    def test_points_actionable_is_exactly_1_2(self):
        self.assertEqual(eb.PROP_VALID_THRESHOLDS["points"], (1, 2))

    def test_blocks_actionable_is_exactly_1_2_3(self):
        self.assertEqual(eb.PROP_VALID_THRESHOLDS["blocks"], (1, 2, 3))

    def test_goalie_actionable_is_exactly_20_and_25(self):
        self.assertEqual(eb.GOALIE_ACTIONABLE_THRESHOLDS, ("20+", "25+"))

    def test_goalie_30_35_40_are_not_actionable_with_correct_status(self):
        self.assertEqual(eb.GOALIE_NOT_ACTIONABLE_THRESHOLDS["30+"], "PARTIAL / RESEARCH")
        self.assertEqual(eb.GOALIE_NOT_ACTIONABLE_THRESHOLDS["35+"], "REJECTED")
        self.assertEqual(eb.GOALIE_NOT_ACTIONABLE_THRESHOLDS["40+"], "INSUFFICIENT_DATA")


class TestBuildAllPlayerPropOpportunities(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = eb.build_all_player_prop_opportunities()

    def test_produces_rows(self):
        self.assertGreater(len(self.rows), 0)

    def test_only_valid_thresholds_appear_per_prop(self):
        for row in self.rows:
            prop = row["prop"]
            threshold_n = int(row["threshold"].rstrip("+"))
            self.assertIn(threshold_n, eb.PROP_VALID_THRESHOLDS[prop],
                          f"{prop} {threshold_n}+ should never appear as a row")
            self.assertNotIn(threshold_n, eb.PROP_NOT_ACTIONABLE_THRESHOLDS.get(prop, ()))

    def test_every_row_has_required_display_fields(self):
        required = {"player", "player_id", "team", "opponent", "market", "threshold",
                    "raw_probability", "coherent_probability", "conservative_probability",
                    "market_no_vig_probability", "fair_odds", "current_odds",
                    "max_acceptable_price", "conservative_edge", "ev", "decision",
                    "confidence", "entity_kind"}
        row = self.rows[0]
        self.assertTrue(required.issubset(row.keys()))

    def test_player_rows_are_entity_kind_player(self):
        self.assertTrue(all(r["entity_kind"] == "PLAYER" for r in self.rows))

    def test_does_not_mutate_demo_data_single_threshold_output(self):
        # build_demo_opportunities() must remain the single-threshold-per-prop
        # function other pages already depend on -- eligible_bets.py only adds.
        legacy = dd.build_demo_opportunities()
        legacy_thresholds = {o["prop"]: o["threshold"] for o in legacy}
        self.assertEqual(legacy_thresholds.get("sog"), "3+")
        self.assertEqual(legacy_thresholds.get("goals"), "1+")

    def test_assists_2plus_rows_have_no_context_overlay(self):
        rows_2plus = [r for r in self.rows if r["prop"] == "assists" and r["threshold"] == "2+"]
        for r in rows_2plus:
            self.assertIsNone(r["context_state"])
            self.assertEqual(r["context_adjusted_probability"], r["raw_probability"])


class TestGoalieSavesOpportunities(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = eb.build_goalie_saves_opportunities()

    def test_produces_rows(self):
        self.assertGreater(len(self.rows), 0)

    def test_20_and_25_are_actionable(self):
        for r in self.rows:
            if r["threshold"] in ("20+", "25+"):
                self.assertTrue(r["actionable"])
                self.assertIn(r["decision"], ("BET", "WATCH", "WAIT", "PASS"))

    def test_30_35_40_are_never_actionable_and_never_priced(self):
        for r in self.rows:
            if r["threshold"] in ("30+", "35+", "40+"):
                self.assertFalse(r["actionable"])
                self.assertEqual(r["decision"], "RESEARCH_ONLY")
                self.assertIsNone(r["current_odds"])
                self.assertFalse(r["is_simulated_price"])

    def test_goalie_rows_are_entity_kind_goalie(self):
        self.assertTrue(all(r["entity_kind"] == "GOALIE" for r in self.rows))


class TestEligibleBetsForTeam(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.all_opps = eb.all_opportunities()

    def test_p0_every_row_for_the_team_appears_somewhere(self):
        # P0: selecting a team must show ALL eligible bets connected to that team.
        team = dd.DEMO_MATCHUPS[0][0]
        result = eb.eligible_bets_for_team(team, self.all_opps)
        combined = result["actionable"] + result["research_only"]
        expected = [o for o in self.all_opps if o["team"] == team]
        self.assertEqual(len(combined), len(expected))

    def test_actionable_and_research_only_never_overlap(self):
        team = dd.DEMO_MATCHUPS[0][0]
        result = eb.eligible_bets_for_team(team, self.all_opps)
        actionable_ids = {(o["player_id"], o["market_id"]) for o in result["actionable"]}
        research_ids = {(o["player_id"], o["market_id"]) for o in result["research_only"]}
        self.assertEqual(actionable_ids & research_ids, set())

    def test_actionable_never_contains_research_only_decision(self):
        team = dd.DEMO_MATCHUPS[0][0]
        result = eb.eligible_bets_for_team(team, self.all_opps)
        self.assertTrue(all(o["decision"] != "RESEARCH_ONLY" for o in result["actionable"]))

    def test_rows_for_other_teams_are_excluded(self):
        team = dd.DEMO_MATCHUPS[0][0]
        result = eb.eligible_bets_for_team(team, self.all_opps)
        self.assertTrue(all(o["team"] == team for o in result["actionable"] + result["research_only"]))


class TestEligibleBetsForGame(unittest.TestCase):
    def test_includes_both_teams_only(self):
        away, home = dd.DEMO_MATCHUPS[0]
        all_opps = eb.all_opportunities()
        result = eb.eligible_bets_for_game(away, home, all_opps)
        combined = result["actionable"] + result["research_only"]
        self.assertTrue(all(o["team"] in (away, home) for o in combined))
        expected = [o for o in all_opps if o["team"] in (away, home)]
        self.assertEqual(len(combined), len(expected))


class TestReadinessGate(unittest.TestCase):
    def test_no_bet_or_watch_survives_for_a_not_ready_game(self):
        games = dd.build_demo_games()
        not_ready = [g for g in games if g.market_ready != "READY"]
        if not not_ready:
            self.skipTest("no simulated game with a non-READY market on today's demo slate")
        game = not_ready[0]
        rows = [o for o in eb.all_opportunities()
                if o["team"] in (game.away, game.home) and o["entity_kind"] == "PLAYER"]
        self.assertTrue(all(o["decision"] != "BET" and o["decision"] != "WATCH" for o in rows))


if __name__ == "__main__":
    unittest.main()
