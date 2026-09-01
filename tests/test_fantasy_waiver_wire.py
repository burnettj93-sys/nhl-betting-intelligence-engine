"""Tests for fantasy/recommendations/waiver_wire.py (Part 142)."""
from __future__ import annotations

import unittest

from fantasy.league.scoring import CategoryProjection
from fantasy.league.settings import LeagueSettings, RosterPositionSlot, StatCategory
from fantasy.recommendations.waiver_wire import AvailablePlayer, rank_waiver_wire, suggest_drop_pairing


def _settings():
    return LeagueSettings(
        league_key="test.l.1", scoring_type="point", is_points_league=True,
        roster_positions=(RosterPositionSlot("C", "P", 1),),
        stat_categories=(StatCategory("1", "Goals", "Goals (G)", "P", True, 4.5),),
    )


def _available(pid, name, value, startable=3, role="MEDIUM"):
    return AvailablePlayer(player_id=pid, name=name, eligible_positions=("C",),
                            projections={"goals": CategoryProjection("goals", value, "HIGH", "PROJECTED")},
                            startable_games_next_7=startable, role_confidence=role)


class TestRankWaiverWire(unittest.TestCase):
    def test_higher_value_ranks_first(self):
        cards = rank_waiver_wire(_settings(), [_available("1", "Weak", 0.1), _available("2", "Strong", 0.5)])
        self.assertEqual(cards[0].player_id, "2")

    def test_empty_pool_returns_empty_never_pads(self):
        self.assertEqual(rank_waiver_wire(_settings(), []), [])

    def test_add_priority_is_bounded_0_to_100(self):
        cards = rank_waiver_wire(_settings(), [_available("1", "A", 0.5)])
        self.assertGreaterEqual(cards[0].add_priority, 0.0)
        self.assertLessEqual(cards[0].add_priority, 100.0)

    def test_higher_role_confidence_ranks_above_equal_value_lower_confidence(self):
        cards = rank_waiver_wire(_settings(), [
            _available("1", "Low Role", 0.4, role="LOW"),
            _available("2", "High Role", 0.4, role="HIGH"),
        ])
        self.assertEqual(cards[0].player_id, "2")

    def test_more_startable_games_ranks_above_equal_value_fewer_games(self):
        cards = rank_waiver_wire(_settings(), [
            _available("1", "Fewer Games", 0.4, startable=1),
            _available("2", "More Games", 0.4, startable=4),
        ])
        self.assertEqual(cards[0].player_id, "2")

    def test_top_n_respected(self):
        players = [_available(str(i), f"P{i}", float(i)) for i in range(10)]
        cards = rank_waiver_wire(_settings(), players, top_n=3)
        self.assertEqual(len(cards), 3)

    def test_why_field_never_empty(self):
        cards = rank_waiver_wire(_settings(), [_available("1", "A", 0.5)])
        self.assertTrue(cards[0].why)


class TestSuggestDropPairing(unittest.TestCase):
    def test_pairs_with_weakest_roster_player_when_add_is_better(self):
        card = _make_card(value=1.0)
        pairing = suggest_drop_pairing(card, {"weak_id": 0.1, "strong_id": 2.0})
        self.assertEqual(pairing, "weak_id")

    def test_no_pairing_when_no_roster_player_is_weaker(self):
        card = _make_card(value=0.05)
        pairing = suggest_drop_pairing(card, {"strong_id": 2.0})
        self.assertIsNone(pairing)

    def test_no_pairing_with_empty_roster(self):
        card = _make_card(value=1.0)
        self.assertIsNone(suggest_drop_pairing(card, {}))


def _make_card(value):
    from fantasy.recommendations.waiver_wire import WaiverCard
    return WaiverCard(player_id="x", name="X", add_priority=50.0, projected_value=value,
                       startable_games_next_7=3, role_confidence="MEDIUM", why="test")


if __name__ == "__main__":
    unittest.main()
