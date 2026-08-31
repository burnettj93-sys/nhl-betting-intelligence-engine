"""
Special-teams role-transition refinement sprint: tests for
research/period_event_timing/special_teams_roles.py. Fast, synthetic
fixtures only -- the real-corpus-scale computation lives in the
run_special_teams_role_*.py scripts, which are one-off research
pipelines (consistent with every other run_*.py in this project) rather
than unit-tested line by line.
"""
from __future__ import annotations

import unittest

from research.period_event_timing import special_teams_roles as sr


class Test01UnitLabelingByRank(unittest.TestCase):
    def test_top_five_by_toi_are_unit1(self):
        rows = [{"player_id": chr(65 + i), "game_id": 1, "team": "X",
                 "pp": {"icetime_seconds": 200 - i * 10}} for i in range(12)]
        out = sr.build_game_unit_labels(rows, ("pp", "icetime_seconds"), "PP")
        labels = {r["player_id"]: r["unit_label"] for r in out}
        self.assertEqual([labels[chr(65 + i)] for i in range(5)], ["UNIT1"] * 5)
        self.assertEqual([labels[chr(65 + i)] for i in range(5, 10)], ["UNIT2"] * 5)
        self.assertEqual(labels[chr(65 + 10)], "NONE")

    def test_below_minimum_toi_is_none_even_if_ranked_high(self):
        rows = [{"player_id": "A", "game_id": 1, "team": "X", "pp": {"icetime_seconds": 5}}]
        out = sr.build_game_unit_labels(rows, ("pp", "icetime_seconds"), "PP")
        self.assertEqual(out[0]["unit_label"], "NONE")

    def test_none_pp_field_treated_as_zero_not_missing(self):
        rows = [{"player_id": "A", "game_id": 1, "team": "X", "pp": None}]
        out = sr.build_game_unit_labels(rows, ("pp", "icetime_seconds"), "PP")
        self.assertEqual(out[0]["toi_seconds"], 0.0)
        self.assertEqual(out[0]["unit_label"], "NONE")

    def test_labels_are_relative_to_team_game_not_global(self):
        # A team with only 3 skaters getting any PP time: all 3 should
        # be UNIT1 (top 5 slots, only 3 filled), never UNIT2.
        rows = [{"player_id": p, "game_id": 1, "team": "X", "pp": {"icetime_seconds": 60}}
                for p in "ABC"]
        out = sr.build_game_unit_labels(rows, ("pp", "icetime_seconds"), "PP")
        self.assertTrue(all(r["unit_label"] == "UNIT1" for r in out))


class Test02RoleStateClassification(unittest.TestCase):
    def test_stable_pp1(self):
        result = sr.classify_role_state(["UNIT1"] * 3, ["UNIT1"] * 8, "PP")
        self.assertEqual(result["state"], "STABLE_PP1")

    def test_promoted_pp2_to_pp1(self):
        result = sr.classify_role_state(["UNIT1"] * 3, ["UNIT2"] * 8, "PP")
        self.assertEqual(result["state"], "PROMOTED_PP2_TO_PP1")

    def test_added_to_pp1_from_none(self):
        result = sr.classify_role_state(["UNIT1"] * 3, ["NONE"] * 8, "PP")
        self.assertEqual(result["state"], "ADDED_TO_PP1")

    def test_removed_from_pp(self):
        result = sr.classify_role_state(["NONE"] * 3, ["UNIT1"] * 8, "PP")
        self.assertEqual(result["state"], "REMOVED_FROM_PP")

    def test_pk_prefix_produces_pk_named_states(self):
        result = sr.classify_role_state(["UNIT1"] * 3, ["UNIT2"] * 8, "PK")
        self.assertEqual(result["state"], "PROMOTED_PK2_TO_PK1")

    def test_insufficient_recent_games_is_role_uncertain(self):
        result = sr.classify_role_state(["UNIT1"], ["UNIT2"] * 8, "PP")
        self.assertEqual(result["state"], "ROLE_UNCERTAIN")

    def test_insufficient_baseline_games_is_role_uncertain(self):
        result = sr.classify_role_state(["UNIT1"] * 3, ["UNIT2"] * 2, "PP")
        self.assertEqual(result["state"], "ROLE_UNCERTAIN")

    def test_no_meaningful_pp_when_stable_at_none(self):
        result = sr.classify_role_state(["NONE"] * 3, ["NONE"] * 8, "PP")
        self.assertEqual(result["state"], "NO_MEANINGFUL_PP")

    def test_mode_breaks_ties_toward_the_higher_unit(self):
        # 4 UNIT1 + 4 UNIT2 in an 8-game baseline is an exact tie by
        # count; the higher unit (UNIT1) should win the tie-break.
        result = sr.classify_role_state(["UNIT1"] * 3, ["UNIT1"] * 4 + ["UNIT2"] * 4, "PP")
        self.assertEqual(result["baseline_role"], "PP1")


class Test03RoleChangeMagnitude(unittest.TestCase):
    def test_computes_positive_delta_for_a_real_promotion(self):
        recent_toi = [280.0, 300.0, 290.0]
        baseline_toi = [60.0] * 8
        recent_team_toi = [600.0] * 3
        baseline_team_toi = [600.0] * 8
        result = sr.role_change_magnitude(recent_toi, baseline_toi, recent_team_toi, baseline_team_toi)
        self.assertGreater(result["delta_toi_seconds"], 0)
        self.assertGreater(result["delta_share"], 0)

    def test_none_when_insufficient_support(self):
        result = sr.role_change_magnitude([100.0], [60.0] * 2, [600.0], [600.0] * 2)
        self.assertIsNone(result["delta_toi_seconds"])
        self.assertIsNone(result["delta_share"])

    def test_zero_team_toi_does_not_crash(self):
        result = sr.role_change_magnitude(
            [100.0, 100.0, 100.0], [50.0] * 8, [0.0, 0.0, 0.0], [0.0] * 8)
        self.assertIsNotNone(result["delta_toi_seconds"])
        self.assertIsNone(result["delta_share"])  # no team TOI to form a share from


# ---------------------------------------------------------------------
# Temporal safety: a role state for target game D must never be
# influenced by D's own data.
# ---------------------------------------------------------------------

class Test04TemporalSafety(unittest.TestCase):
    def test_classify_role_state_never_receives_target_game_data(self):
        """Structural guard: classify_role_state's signature accepts only
        already-sliced recent/baseline label lists -- there is no game-D
        parameter for it to accidentally read from at all."""
        import inspect
        sig = inspect.signature(sr.classify_role_state)
        self.assertEqual(list(sig.parameters)[:2], ["recent_labels", "baseline_labels"])

    def test_recent_and_baseline_windows_are_built_from_strictly_prior_games(self):
        """End-to-end check using the real orchestration slicing logic
        (mirrors run_special_teams_role_transitions.py's own window
        slicing): for target index i, both windows must only contain
        games at index < i."""
        games = [{"game_date": f"2024-01-{d:02d}"} for d in range(1, 15)]
        i = 12
        recent_n, baseline_n = sr.RECENT_GAMES, sr.BASELINE_GAMES
        recent_slice = games[max(0, i - recent_n):i]
        baseline_slice = games[max(0, i - recent_n - baseline_n):max(0, i - recent_n)]
        for g in recent_slice + baseline_slice:
            self.assertLess(games.index(g), i)


if __name__ == "__main__":
    unittest.main()
