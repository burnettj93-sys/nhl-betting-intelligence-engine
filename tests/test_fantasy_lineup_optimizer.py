"""Tests for fantasy/recommendations/lineup_optimizer.py (Part 141)."""
from __future__ import annotations

import unittest

from fantasy.league.scoring import CategoryProjection
from fantasy.league.settings import LeagueSettings, RosterPositionSlot, StatCategory
from fantasy.recommendations.lineup_optimizer import (
    RosterPlayer, optimize_lineup, player_value, start_sit_recommendation,
)


def _settings(roster_positions):
    return LeagueSettings(
        league_key="test.l.1", scoring_type="point", is_points_league=True,
        roster_positions=roster_positions,
        stat_categories=(StatCategory("1", "Goals", "Goals (G)", "P", True, 4.5),
                          StatCategory("2", "Saves", "Saves (SV)", "G", True, 0.3)),
    )


def _skater(pid, name, value, position="C"):
    return RosterPlayer(player_id=pid, name=name, eligible_positions=(position,),
                         projections={"goals": CategoryProjection("goals", value, "HIGH", "PROJECTED")})


def _goalie(pid, name, value):
    return RosterPlayer(player_id=pid, name=name, eligible_positions=("G",),
                         projections={"goalie_saves": CategoryProjection("goalie_saves", value, "HIGH", "PROJECTED")},
                         is_goalie=True)


class TestGoalieNeverGetsUtilSlot(unittest.TestCase):
    """Regression test for a real bug found this sprint: a goalie was
    being assigned to a Util slot, which no real Yahoo hockey league
    permits."""

    def test_goalie_excluded_from_util_even_when_it_would_maximize_value(self):
        settings = _settings((RosterPositionSlot("Util", "P", 1), RosterPositionSlot("G", "G", 1)))
        skater = _skater("1", "Low Value Skater", 0.05)
        goalie = _goalie("2", "High Value Goalie", 50.0)  # deliberately huge value
        assignments = optimize_lineup(settings, [skater, goalie])
        goalie_assignment = next(a for a in assignments if a.player_id == "2")
        self.assertEqual(goalie_assignment.assigned_slot, "G")
        self.assertNotEqual(goalie_assignment.assigned_slot, "Util")

    def test_skater_can_use_util_slot(self):
        settings = _settings((RosterPositionSlot("Util", "P", 1),))
        skater = _skater("1", "A Skater", 1.0, position="LW")
        assignments = optimize_lineup(settings, [skater])
        self.assertEqual(assignments[0].assigned_slot, "Util")


class TestLineupPositionConstraints(unittest.TestCase):
    def test_respects_real_roster_position_counts(self):
        settings = _settings((RosterPositionSlot("C", "P", 1),))
        p1 = _skater("1", "Best Center", 1.0)
        p2 = _skater("2", "Second Center", 0.5)
        assignments = optimize_lineup(settings, [p1, p2])
        started = [a for a in assignments if a.assigned_slot != "BN"]
        benched = [a for a in assignments if a.assigned_slot == "BN"]
        self.assertEqual(len(started), 1)
        self.assertEqual(started[0].player_id, "1")  # higher value gets the single C slot
        self.assertEqual(len(benched), 1)
        self.assertEqual(benched[0].player_id, "2")

    def test_ir_slots_never_auto_filled(self):
        settings = _settings((RosterPositionSlot("IR", None, 2),))
        p1 = _skater("1", "A Player", 1.0)
        assignments = optimize_lineup(settings, [p1])
        # No open non-IR slots exist, so this player is benched, not
        # assigned to IR (Part 34: IR is never auto-assigned by value).
        self.assertEqual(assignments[0].assigned_slot, "BN")

    def test_zero_roster_positions_benches_everyone_deterministically(self):
        settings = _settings(())
        players = [_skater(str(i), f"P{i}", float(i)) for i in range(5)]
        assignments = optimize_lineup(settings, players)
        self.assertTrue(all(a.assigned_slot == "BN" for a in assignments))

    def test_deterministic_across_repeated_calls(self):
        settings = _settings((RosterPositionSlot("C", "P", 1),))
        players = [_skater("1", "A", 1.0), _skater("2", "B", 1.0)]  # tie
        a1 = optimize_lineup(settings, players)
        a2 = optimize_lineup(settings, players)
        self.assertEqual([(a.player_id, a.assigned_slot) for a in a1],
                          [(a.player_id, a.assigned_slot) for a in a2])


class TestStartSitRecommendation(unittest.TestCase):
    def test_started_player_is_start(self):
        settings = _settings((RosterPositionSlot("C", "P", 1),))
        assignments = optimize_lineup(settings, [_skater("1", "A", 1.0)])
        self.assertEqual(start_sit_recommendation(assignments[0]), "START")

    def test_benched_positive_value_is_borderline(self):
        settings = _settings(())
        assignments = optimize_lineup(settings, [_skater("1", "A", 1.0)])
        self.assertEqual(start_sit_recommendation(assignments[0]), "BORDERLINE")

    def test_benched_zero_value_is_bench(self):
        settings = _settings(())
        assignments = optimize_lineup(settings, [_skater("1", "A", 0.0)])
        self.assertEqual(start_sit_recommendation(assignments[0]), "BENCH")


class TestPlayerValue(unittest.TestCase):
    def test_missing_projection_never_crashes_returns_zero_contribution(self):
        settings = _settings((RosterPositionSlot("C", "P", 1),))
        value = player_value(settings, {})
        self.assertEqual(value, 0.0)


if __name__ == "__main__":
    unittest.main()
