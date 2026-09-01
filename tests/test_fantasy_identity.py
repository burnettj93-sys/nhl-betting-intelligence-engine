"""Tests for fantasy/yahoo/identity.py (Part 139)."""
from __future__ import annotations

import unittest

from fantasy.yahoo.identity import PlayerIdentityIndex, normalize_name


class TestNormalizeName(unittest.TestCase):
    def test_strips_accents(self):
        self.assertEqual(normalize_name("Alexis Lafrenière"), normalize_name("Alexis Lafreniere"))

    def test_strips_suffix(self):
        self.assertEqual(normalize_name("Trevor Zegras Jr."), normalize_name("Trevor Zegras"))

    def test_case_insensitive_and_whitespace_collapsed(self):
        self.assertEqual(normalize_name("  Connor   McDavid "), normalize_name("connor mcdavid"))


class TestPlayerIdentityIndex(unittest.TestCase):
    def setUp(self):
        self.index = PlayerIdentityIndex([
            ("1", "Connor McDavid", "EDM"),
            ("2", "Sebastian Aho", "NYI"),
            ("3", "Sebastian Aho", "CAR"),
        ])

    def test_matched_unique_name(self):
        result = self.index.match("Connor McDavid")
        self.assertEqual(result.status, "MATCHED")
        self.assertEqual(result.player_id, "1")

    def test_duplicate_name_disambiguated_by_team(self):
        result = self.index.match("Sebastian Aho", yahoo_team="CAR")
        self.assertEqual(result.status, "MATCHED")
        self.assertEqual(result.player_id, "3")

    def test_duplicate_name_ambiguous_when_team_does_not_disambiguate(self):
        result = self.index.match("Sebastian Aho")
        self.assertEqual(result.status, "AMBIGUOUS")
        self.assertIsNone(result.player_id)
        self.assertEqual(set(result.candidates), {"2", "3"})

    def test_duplicate_name_ambiguous_when_team_does_not_match_either(self):
        result = self.index.match("Sebastian Aho", yahoo_team="TOR")
        self.assertEqual(result.status, "AMBIGUOUS")

    def test_unknown_name_unmatched(self):
        result = self.index.match("Nobody Real")
        self.assertEqual(result.status, "UNMATCHED")
        self.assertIsNone(result.player_id)

    def test_never_matches_on_last_name_alone(self):
        result = self.index.match("McDavid")
        self.assertEqual(result.status, "UNMATCHED")


if __name__ == "__main__":
    unittest.main()
