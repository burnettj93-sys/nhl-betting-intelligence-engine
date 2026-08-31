"""2026-27 Continuous Learning framework, Part 63: tests for
operational/rejected_research_check.py -- consults REAL, existing
registries, never a separate fabricated list."""
from __future__ import annotations

import unittest

from operational import rejected_research_check as rrc


class Test01AllRejectedEntries(unittest.TestCase):
    def test_includes_the_real_known_rejected_blocks_pk_overlay(self):
        entries = rrc.all_rejected_entries()
        overlay_ids = [e.get("overlay_id") for e in entries]
        self.assertIn("PLAYER_BLOCKS_PK_REMOVAL_OVERLAY", overlay_ids)

    def test_includes_the_real_known_rejected_goalie_saves_35plus(self):
        entries = rrc.all_rejected_entries()
        model_ids = [e.get("model_id") for e in entries]
        self.assertIn("GOALIE_SAVES", model_ids)

    def test_never_includes_a_validated_model(self):
        entries = rrc.all_rejected_entries()
        model_ids = [e.get("model_id") for e in entries]
        self.assertNotIn("PLAYER_SOG", model_ids)


class Test02MatchesRejectedIdea(unittest.TestCase):
    def test_exact_id_match_found(self):
        match = rrc.matches_a_rejected_idea("PLAYER_BLOCKS_PK_REMOVAL_OVERLAY", "pk removal for blocks")
        self.assertIsNotNone(match)

    def test_case_insensitive_match(self):
        match = rrc.matches_a_rejected_idea("player_blocks_pk_removal_overlay", "x")
        self.assertIsNotNone(match)

    def test_unrelated_target_returns_none(self):
        match = rrc.matches_a_rejected_idea("PLAYER_SOG_PP_ROLE_OVERLAY", "x")
        self.assertIsNone(match)

    def test_no_fuzzy_text_matching_of_unrelated_hypothesis(self):
        # A hypothesis mentioning "blocks" in passing must not
        # false-positive match the rejected Blocks overlay by text alone.
        match = rrc.matches_a_rejected_idea("SOME_NEW_MODEL", "this is about blocks and PK removal too")
        self.assertIsNone(match)


if __name__ == "__main__":
    unittest.main()
