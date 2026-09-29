"""
DraftKings-Ontario-Aligned Parlay Roadmap block (2026-09-29): tests for
research/dk_ontario_market_registry.py -- the target-book map derived from
the owner's own manually-verified live DK Ontario screenshots. Pure data
registry; no network, no model dependency.
"""
from __future__ import annotations

import unittest

from research import dk_ontario_market_registry as dk


class TestRegistryIntegrity(unittest.TestCase):
    def test_no_duplicate_market_families(self):
        ids = [m.market_family for m in dk.ALL_DK_MARKETS]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_entry_has_a_known_target_book_status(self):
        for m in dk.ALL_DK_MARKETS:
            self.assertIn(m.target_book_available, dk.TARGET_BOOK_STATUSES)

    def test_every_entry_has_a_valid_tier(self):
        for m in dk.ALL_DK_MARKETS:
            self.assertIn(m.tier, (1, 2, 3))


class TestHitsBlocksExclusion(unittest.TestCase):
    """Part: HITS / BLOCKS CORRECTION -- research preserved, but explicitly excluded
    from the DK Ontario parlay allowlist."""

    def test_hits_is_not_currently_offered(self):
        entry = dk.get("PLAYER_HITS")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.target_book_available, dk.NOT_CURRENTLY_OFFERED_DK_ON)

    def test_blocks_is_not_currently_offered(self):
        entry = dk.get("PLAYER_BLOCKS")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.target_book_available, dk.NOT_CURRENTLY_OFFERED_DK_ON)

    def test_excluded_from_dk_parlay_contains_exactly_hits_and_blocks(self):
        excluded_ids = {m.market_family for m in dk.excluded_from_dk_parlay()}
        self.assertEqual(excluded_ids, {"PLAYER_HITS", "PLAYER_BLOCKS"})


class TestVerifiedFamilies(unittest.TestCase):
    """Spot-check a representative sample of families the owner's screenshots confirmed."""

    def test_core_tier1_families_are_verified_on_dk(self):
        for market_family in ("MONEYLINE", "PLAYER_SOG", "GOALIE_SAVES", "PLAYER_POINTS",
                              "PLAYER_ASSISTS", "TEAM_TOTAL", "ALTERNATE_TEAM_TOTAL",
                              "GAME_TOTAL", "ALTERNATE_GAME_TOTAL"):
            entry = dk.get(market_family)
            self.assertIsNotNone(entry, f"{market_family} missing from registry")
            self.assertEqual(entry.target_book_available, dk.VERIFIED_DK_ON, market_family)
            self.assertEqual(entry.tier, 1, market_family)

    def test_sog_ladder_matches_observed_screenshots(self):
        entry = dk.get("PLAYER_SOG")
        self.assertEqual(entry.observed_thresholds, (1, 2, 3, 4, 5))

    def test_saves_milestone_ladder_matches_observed_screenshots(self):
        entry = dk.get("GOALIE_SAVES")
        self.assertEqual(entry.observed_thresholds, (24, 26, 28, 30, 32, 34))

    def test_alternate_team_total_ladder_matches_observed_screenshots(self):
        entry = dk.get("ALTERNATE_TEAM_TOTAL")
        self.assertEqual(entry.observed_thresholds, (0.5, 1.5, 2.5, 3.5, 4.5, 5.5))


class TestQueryHelpers(unittest.TestCase):
    def test_by_target_book_status_partitions_correctly(self):
        verified = dk.by_target_book_status(dk.VERIFIED_DK_ON)
        excluded = dk.by_target_book_status(dk.NOT_CURRENTLY_OFFERED_DK_ON)
        self.assertEqual(len(verified) + len(excluded), len(dk.ALL_DK_MARKETS))

    def test_by_tier_one_contains_the_documented_build_priority_list(self):
        tier1_ids = {m.market_family for m in dk.by_tier(1)}
        for expected in ("MONEYLINE", "PLAYER_SOG", "GOALIE_SAVES", "PLAYER_POINTS",
                        "PLAYER_ASSISTS", "TEAM_TOTAL", "ALTERNATE_TEAM_TOTAL",
                        "GAME_TOTAL", "ALTERNATE_GAME_TOTAL"):
            self.assertIn(expected, tier1_ids)


if __name__ == "__main__":
    unittest.main()
