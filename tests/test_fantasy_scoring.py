"""Tests for fantasy/league/settings.py and fantasy/league/scoring.py
(Part 138/140)."""
from __future__ import annotations

import unittest

from fantasy.league.scoring import (
    CategoryProjection, category_contributions, map_stat_category, points_value,
)
from fantasy.league.settings import LeagueSettings, RosterPositionSlot, StatCategory, parse_league_settings


def _points_league():
    return LeagueSettings(
        league_key="test.l.1", scoring_type="point", is_points_league=True,
        roster_positions=(RosterPositionSlot("C", "P", 2),),
        stat_categories=(
            StatCategory("1", "Goals", "Goals (G)", "P", True, 4.5),
            StatCategory("2", "Assists", "Assists (A)", "P", True, 3.0),
            StatCategory("3", "Hits", "Hits (HIT)", "P", True, 0.5),
        ),
    )


def _category_league():
    return LeagueSettings(
        league_key="test.l.2", scoring_type="head", is_points_league=False,
        roster_positions=(RosterPositionSlot("C", "P", 2),),
        stat_categories=(
            StatCategory("1", "Goals", "Goals (G)", "P", True, None),
            StatCategory("2", "Shots on Goal", "SOG", "P", True, None),
        ),
    )


class TestParseLeagueSettings(unittest.TestCase):
    def test_detects_points_league_from_modifiers(self):
        raw = {
            "scoring_type": "head",
            "uses_fractional_points": "1",
            "roster_positions": [{"position": "C", "position_type": "P", "count": "2"}],
            "stat_categories": [{"stat_id": "1", "name": "Goals", "display_name": "Goals (G)",
                                  "enabled": "1", "position_type": "P"}],
            "stat_modifiers": [{"stat_id": "1", "value": "4.5"}],
        }
        settings = parse_league_settings(raw, "test.l.1")
        self.assertTrue(settings.is_points_league)
        self.assertEqual(settings.stat_categories[0].modifier, 4.5)
        self.assertEqual(settings.roster_positions[0].count, 2)

    def test_no_default_category_assumption_empty_settings_yields_empty_categories(self):
        # Part 12: no hard-coded default category list -- an empty real
        # settings response yields zero categories, never an invented
        # standard set.
        settings = parse_league_settings({}, "test.l.2")
        self.assertEqual(settings.stat_categories, ())
        self.assertEqual(settings.roster_positions, ())

    def test_disabled_category_is_recorded_as_disabled(self):
        raw = {"stat_categories": [{"stat_id": "9", "name": "Hits", "enabled": "0"}]}
        settings = parse_league_settings(raw, "test.l.3")
        self.assertFalse(settings.stat_categories[0].enabled)
        self.assertEqual(settings.enabled_categories(), ())

    def test_settings_hash_changes_when_settings_change(self):
        s1 = parse_league_settings({"scoring_type": "head"}, "L")
        s2 = parse_league_settings({"scoring_type": "point"}, "L")
        self.assertNotEqual(s1.settings_hash, s2.settings_hash)

    def test_settings_hash_stable_for_identical_settings(self):
        raw = {"scoring_type": "head", "roster_positions": [{"position": "C", "count": "1"}]}
        s1 = parse_league_settings(raw, "L")
        s2 = parse_league_settings(raw, "L")
        self.assertEqual(s1.settings_hash, s2.settings_hash)


class TestStatCategoryMapping(unittest.TestCase):
    def test_validated_stat_maps_correctly(self):
        cat = StatCategory("1", "Shots on Goal", "SOG", "P", True, None)
        internal, support = map_stat_category(cat)
        self.assertEqual(internal, "sog")
        self.assertEqual(support, "VALIDATED")

    def test_unavailable_stat_maps_correctly(self):
        cat = StatCategory("2", "Hits", "HIT", "P", True, None)
        internal, support = map_stat_category(cat)
        self.assertEqual(internal, "hits")
        self.assertEqual(support, "NOT_AVAILABLE")

    def test_unknown_stat_name_returns_none_never_a_guess(self):
        cat = StatCategory("99", "Some Brand New Stat Yahoo Just Added", "SBNS", "P", True, None)
        internal, support = map_stat_category(cat)
        self.assertIsNone(internal)
        self.assertEqual(support, "NOT_AVAILABLE")


class TestPointsLeagueScoring(unittest.TestCase):
    def test_weighted_sum_over_available_categories(self):
        settings = _points_league()
        projections = {
            "goals": CategoryProjection("goals", 0.4, "HIGH", "PROJECTED"),
            "assists": CategoryProjection("assists", 0.3, "HIGH", "PROJECTED"),
        }
        result = points_value(settings, projections)
        self.assertTrue(result["is_points_league"])
        self.assertAlmostEqual(result["total"], 0.4 * 4.5 + 0.3 * 3.0)
        self.assertEqual(len(result["missing_categories"]), 1)  # Hits unavailable
        self.assertEqual(result["missing_categories"][0]["internal_stat"], "hits")

    def test_no_categories_available_yields_zero_not_a_crash(self):
        settings = _points_league()
        result = points_value(settings, {})
        self.assertEqual(result["total"], 0.0)
        self.assertEqual(len(result["missing_categories"]), 3)

    def test_category_league_returns_not_a_points_league(self):
        settings = _category_league()
        result = points_value(settings, {"goals": CategoryProjection("goals", 0.4, "HIGH", "PROJECTED")})
        self.assertFalse(result["is_points_league"])
        self.assertIsNone(result["total"])


class TestCategoryLeagueScoring(unittest.TestCase):
    def test_per_category_contributions_never_collapsed_to_scalar(self):
        settings = _category_league()
        projections = {
            "goals": CategoryProjection("goals", 0.4, "HIGH", "PROJECTED"),
            "sog": CategoryProjection("sog", 3.0, "HIGH", "PROJECTED"),
        }
        result = category_contributions(settings, projections)
        self.assertFalse(result["is_points_league"])
        self.assertEqual(len(result["contributions"]), 2)
        self.assertEqual(result["missing_categories"], [])


if __name__ == "__main__":
    unittest.main()
