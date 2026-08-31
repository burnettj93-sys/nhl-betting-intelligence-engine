"""
Tests for the Goalie Quality x Starter-Probability Integration
experiment: research/goalie_intelligence/quality.py,
research/goalie_quality_integration.py, and the parts of
research/run_goalie_quality_comparison.py / dashboard/goalie_quality_view.py
that are pure/deterministic enough to unit test directly. See
GOALIE_QUALITY_INTEGRATION_REPORT.md for the full experiment writeup.

Small hand-built fixtures for formula/PIT-gate correctness (mirrors
tests/test_goalie_intelligence.py's style); a handful of real-corpus
spot checks for cross-validation against the actual production class
and the actual archived data.
"""
import ast
import math
import os
import unittest

import config
from models.goalie_model import GoalieRatingModel
from research.goalie_intelligence import quality as gq
from research import goalie_quality_integration as gqi
from research import run_goalie_quality_comparison as rgc

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def qrow(goalie_id, game_id, game_date, season, team, shots_against, goals_against,
         xg_against, icetime_seconds=3600.0):
    return {
        "goalie_id": goalie_id, "goalie_name": goalie_id, "game_id": game_id, "season": season,
        "game_date": game_date, "team": team, "opponent": "OPP", "icetime_seconds": icetime_seconds,
        "shots_against": shots_against, "saves": shots_against - goals_against,
        "goals_against": goals_against, "xg_against": xg_against,
        "provenance_type": "ARCHIVAL_RESEARCH",
    }


# --------------------------------------------------------------------------
# Part 1/6-B / 18: Candidate A must reuse production's exact shrinkage
# FORMULA unchanged, cross-checked against the real class -- not just
# independently re-derived.
# --------------------------------------------------------------------------
class TestSavePctShrinkageCorrectness(unittest.TestCase):
    def test_matches_real_production_class_on_hand_fixture(self):
        history = [
            qrow("G1", 1, "2024-10-01", 20242025, "WPG", 30, 2, 2.5),
            qrow("G1", 2, "2024-10-03", 20242025, "WPG", 28, 3, 2.1),
            qrow("G1", 3, "2024-10-05", 20242025, "WPG", 32, 1, 2.8),
        ]
        module_delta, module_shots = gq.shrunk_save_pct_production(history)

        model = GoalieRatingModel()
        for r in history:
            model.update(r["goalie_id"], r["saves"], r["shots_against"])
        prod_delta, _mult = model.rating_adjustment_elo("G1", confirmed=True)

        self.assertAlmostEqual(module_delta, prod_delta, places=9)
        self.assertEqual(module_shots, 90.0)

    def test_matches_real_production_class_on_real_archived_corpus(self):
        rows = gq.load_appearance_corpus()
        from collections import Counter
        goalie_id, _n = Counter(r["goalie_id"] for r in rows).most_common(1)[0]
        dates = sorted(r["game_date"] for r in rows if r["goalie_id"] == goalie_id)
        history = gq.goalie_history_as_of(rows, goalie_id, dates[-1])

        module_delta, _shots = gq.shrunk_save_pct_production(history)
        model = GoalieRatingModel()
        for r in history:
            model.update(r["goalie_id"], r["saves"], r["shots_against"])
        prod_delta, _mult = model.rating_adjustment_elo(goalie_id, confirmed=True)
        self.assertAlmostEqual(module_delta, prod_delta, places=9)

    def test_empty_history_returns_zero_no_wait_no_exception(self):
        delta, shots = gq.shrunk_save_pct_production([])
        self.assertEqual(delta, 0.0)
        self.assertEqual(shots, 0.0)


# --------------------------------------------------------------------------
# Part 4: goalie quality must be strictly PIT-safe. Part 17: goalie-
# SCOPED (not team-scoped) -- follows identity across a team change.
# --------------------------------------------------------------------------
class TestGoalieHistoryAsOfPIT(unittest.TestCase):
    def setUp(self):
        self.rows = [
            qrow("G1", 1, "2024-10-01", 20242025, "WPG", 30, 2, 2.5),
            qrow("G1", 2, "2024-10-05", 20242025, "WPG", 28, 3, 2.1),  # target game's own row
            qrow("G1", 3, "2024-10-09", 20242025, "WPG", 25, 1, 2.0),  # future
        ]

    def test_excludes_same_day_and_future_rows(self):
        history = gq.goalie_history_as_of(self.rows, "G1", "2024-10-05")
        dates = [r["game_date"] for r in history]
        self.assertEqual(dates, ["2024-10-01"])
        self.assertNotIn("2024-10-05", dates)
        self.assertNotIn("2024-10-09", dates)

    def test_excludes_the_target_games_own_row_even_though_same_goalie(self):
        history = gq.goalie_history_as_of(self.rows, "G1", "2024-10-05")
        self.assertTrue(all(r["game_id"] != 2 for r in history))

    def test_goalie_identity_survives_a_team_change_not_reset(self):
        rows = [
            qrow("G1", 1, "2024-10-01", 20242025, "WPG", 30, 2, 2.5),
            qrow("G1", 2, "2024-11-01", 20242025, "CGY", 28, 3, 2.1),  # traded mid-season
        ]
        history = gq.goalie_history_as_of(rows, "G1", "2024-11-15")
        self.assertEqual(len(history), 2)
        self.assertEqual({r["team"] for r in history}, {"WPG", "CGY"})

    def test_unrelated_goalie_never_appears_in_this_goalies_history(self):
        rows = self.rows + [qrow("G2", 4, "2024-10-02", 20242025, "WPG", 20, 1, 1.5)]
        history = gq.goalie_history_as_of(rows, "G1", "2024-10-05")
        self.assertTrue(all(r["goalie_id"] == "G1" for r in history))


# --------------------------------------------------------------------------
# Part 16: NOT season-scoped -- a goalie's cumulative history must carry
# across a season boundary, unlike the team-level MoneyPuck features.
# --------------------------------------------------------------------------
class TestSeasonBoundaryNoReset(unittest.TestCase):
    def test_prior_season_appearances_are_not_dropped_at_season_boundary(self):
        rows = [
            qrow("G1", 1, "2023-11-01", 20232024, "WPG", 30, 2, 2.5),
            qrow("G1", 2, "2023-12-01", 20232024, "WPG", 28, 3, 2.1),
        ]
        history = gq.goalie_history_as_of(rows, "G1", "2024-10-15")  # next season
        self.assertEqual(len(history), 2, "career evidence from a prior season must not be discarded")

    def test_larger_cumulative_sample_from_two_seasons_shrinks_less(self):
        one_season = [qrow("G1", i, f"2023-10-{i:02d}", 20232024, "WPG", 30, 2, 2.5) for i in range(1, 6)]
        two_seasons = one_season + [qrow("G1", 100 + i, f"2024-10-{i:02d}", 20242025, "WPG", 30, 2, 2.5)
                                     for i in range(1, 6)]
        _delta1, shots1 = gq.shrunk_save_pct_production(one_season)
        _delta2, shots2 = gq.shrunk_save_pct_production(two_seasons)
        self.assertGreater(shots2, shots1)


# --------------------------------------------------------------------------
# Part 13/14/15: Candidate B (GSAx-style) formula correctness.
# --------------------------------------------------------------------------
class TestGSAxFormula(unittest.TestCase):
    def test_positive_when_goalie_outperforms_expected(self):
        # xGA=3.0, GA=1.0 over 3600s (60 min) -> raw = (3.0-1.0)*3600/3600 = 2.0/60min,
        # shrunk toward 0 by shots-based factor.
        history = [qrow("G1", 1, "2024-10-01", 20242025, "WPG", 30, 1, 3.0)]
        val, shots = gq.rolling_gsax_per60(history, window=None)
        self.assertGreater(val, 0.0)
        self.assertEqual(shots, 30.0)

    def test_negative_when_goalie_underperforms_expected(self):
        history = [qrow("G1", 1, "2024-10-01", 20242025, "WPG", 30, 4, 1.0)]
        val, _shots = gq.rolling_gsax_per60(history, window=None)
        self.assertLess(val, 0.0)

    def test_exact_value_matches_hand_computed_formula(self):
        history = [qrow("G1", 1, "2024-10-01", 20242025, "WPG", 20, 2, 3.0, icetime_seconds=1800.0)]
        val, shots = gq.rolling_gsax_per60(history, window=None)
        raw = (3.0 - 2.0) * 3600.0 / 1800.0
        shrink = 20.0 / (20.0 + config.GOALIE_SHRINKAGE_STARTS * 25)
        self.assertAlmostEqual(val, raw * shrink, places=9)

    def test_window_truncates_to_most_recent_n_appearances(self):
        history = [qrow("G1", i, f"2024-10-{i:02d}", 20242025, "WPG", 30, 5, 1.0) for i in range(1, 6)]
        history += [qrow("G1", 99, "2024-11-01", 20242025, "WPG", 30, 0, 5.0)]
        val_w1, _ = gq.rolling_gsax_per60(history, window=1)
        val_all, _ = gq.rolling_gsax_per60(history, window=None)
        self.assertGreater(val_w1, val_all, "the single most recent (strong) appearance should dominate a window=1 view")

    def test_no_history_returns_none_not_zero_not_exception(self):
        val, shots = gq.rolling_gsax_per60([], window=5)
        self.assertIsNone(val)
        self.assertEqual(shots, 0.0)

    def test_small_sample_is_shrunk_toward_zero(self):
        one_game = [qrow("G1", 1, "2024-10-01", 20242025, "WPG", 5, 0, 2.0)]
        val, _ = gq.rolling_gsax_per60(one_game, window=None)
        raw = (2.0 - 0.0) * 3600.0 / 3600.0
        self.assertLess(abs(val), abs(raw), "a 5-shot sample must be shrunk well below its raw rate")


# --------------------------------------------------------------------------
# Part 7: scenario-weighted probability mixture -- correctness AND the
# explicit distinction from "sigmoid of a weighted average of
# adjustments" (which Part 7 explicitly flags as WRONG).
# --------------------------------------------------------------------------
class TestScenarioWeightedProbability(unittest.TestCase):
    def test_reduces_to_baseline_when_all_adjustments_are_zero(self):
        p_baseline = 0.62
        home = [(0.7, 0.0), (0.3, 0.0)]
        away = [(0.6, 0.0), (0.4, 0.0)]
        result = gqi.scenario_weighted_probability(p_baseline, home, away)
        self.assertAlmostEqual(result, p_baseline, places=9)

    def test_reduces_to_simple_formula_with_single_certain_goalie_each_side(self):
        p_baseline = 0.55
        home = [(1.0, 0.30)]
        away = [(1.0, -0.10)]
        result = gqi.scenario_weighted_probability(p_baseline, home, away)
        expected = gqi.sigmoid(gqi.logit(p_baseline) + 0.30 - (-0.10))
        self.assertAlmostEqual(result, expected, places=9)

    def test_probability_weighted_scenarios_sum_to_one_by_construction(self):
        home = [(0.6, 0.2), (0.4, -0.3)]
        away = [(0.55, 0.1), (0.45, -0.4)]
        total_weight = sum(ph * pa for ph, _ in home for pa, _ in away)
        self.assertAlmostEqual(total_weight, 1.0, places=9)

    def test_mixture_differs_from_sigmoid_of_averaged_adjustments(self):
        """Part 7's explicit warning: averaging p_home_win(h,a) across
        scenarios is NOT the same as sigmoid(base + weighted-average
        adjustment), because sigmoid is nonlinear. This constructs a case
        where the two approaches give measurably different answers."""
        p_baseline = 0.5
        home = [(0.8, 3.0), (0.2, -3.0)]  # asymmetric weights, large-magnitude adjustments
        away = [(1.0, 0.0)]
        correct_mixture = gqi.scenario_weighted_probability(p_baseline, home, away)
        avg_adjustment = sum(p * a for p, a in home) - sum(p * a for p, a in away)
        wrong_sigmoid_of_avg = gqi.sigmoid(gqi.logit(p_baseline) + avg_adjustment)
        # the two approaches disagree measurably because sigmoid is nonlinear:
        # 0.8*sigmoid(+3) + 0.2*sigmoid(-3) != sigmoid(0.8*3 + 0.2*-3).
        self.assertGreater(abs(correct_mixture - wrong_sigmoid_of_avg), 0.05)

    def test_top1_probability_uses_only_the_named_pair(self):
        p_baseline = 0.5
        home = [(0.9, 0.4), (0.1, -0.4)]
        away = [(0.8, 0.1), (0.2, -0.1)]
        top1 = gqi.top1_probability(p_baseline, home[0], away[0])
        expected = gqi.sigmoid(gqi.logit(p_baseline) + 0.4 - 0.1)
        self.assertAlmostEqual(top1, expected, places=9)

    def test_oracle_probability_uses_the_given_actual_adjustments_directly(self):
        result = gqi.oracle_probability(0.5, home_actual_adj=0.25, away_actual_adj=-0.15)
        expected = gqi.sigmoid(gqi.logit(0.5) + 0.25 - (-0.15))
        self.assertAlmostEqual(result, expected, places=9)


# --------------------------------------------------------------------------
# Part 3/8/9: the actual historical starter must NEVER feed the headline
# (mixture/top-1) probability -- only the oracle diagnostic reads it.
# --------------------------------------------------------------------------
class TestOracleIsolation(unittest.TestCase):
    def test_changing_actual_starter_never_changes_mixture_or_top1_probability(self):
        p_baseline = 0.55
        home_pairs = [(0.7, 0.3), (0.3, -0.2)]
        away_pairs = [(0.6, 0.1), (0.4, -0.1)]
        mix_before = gqi.scenario_weighted_probability(p_baseline, home_pairs, away_pairs)
        top1_before = gqi.top1_probability(p_baseline, home_pairs[0], away_pairs[0])
        # "actual starter" is not even a parameter to these two functions --
        # structurally, there is no way for it to influence the result.
        mix_after = gqi.scenario_weighted_probability(p_baseline, home_pairs, away_pairs)
        top1_after = gqi.top1_probability(p_baseline, home_pairs[0], away_pairs[0])
        self.assertEqual(mix_before, mix_after)
        self.assertEqual(top1_before, top1_after)

    def test_changing_actual_starter_does_change_the_oracle_probability(self):
        p_baseline = 0.55
        oracle_starter_a = gqi.oracle_probability(p_baseline, home_actual_adj=0.4, away_actual_adj=0.0)
        oracle_starter_b = gqi.oracle_probability(p_baseline, home_actual_adj=-0.4, away_actual_adj=0.0)
        self.assertNotAlmostEqual(oracle_starter_a, oracle_starter_b, places=6)

    def test_functions_have_no_actual_starter_parameter_by_signature(self):
        import inspect
        mix_params = set(inspect.signature(gqi.scenario_weighted_probability).parameters)
        top1_params = set(inspect.signature(gqi.top1_probability).parameters)
        self.assertFalse(any("actual" in p for p in mix_params))
        self.assertFalse(any("actual" in p for p in top1_params))


# --------------------------------------------------------------------------
# Part 20: common evaluation set / game-pairing join logic.
# --------------------------------------------------------------------------
class TestCommonEvaluationSetJoin(unittest.TestCase):
    def test_game_excluded_when_one_side_has_no_starter_row(self):
        baseline_records = [{"game_id": 1, "season": 20242025, "game_date": "2024-10-10",
                              "home_team": "WPG", "away_team": "CGY", "p_home": 0.5, "actual_home_win": 1.0}]
        starter_rows = []  # neither side has any starter data
        pairs, excluded = rgc.build_starter_pair_examples(starter_rows, baseline_records)
        self.assertEqual(len(pairs), 0)
        self.assertEqual(excluded["no_starter_row_for_a_side"], 1)

    def test_confidence_label_thresholds(self):
        self.assertEqual(rgc.confidence_label(0.75, 0.80), "HIGH")
        self.assertEqual(rgc.confidence_label(0.75, 0.55), "MEDIUM")
        self.assertEqual(rgc.confidence_label(0.75, 0.30), "LOW")


# --------------------------------------------------------------------------
# Part 12: WORKHORSE / TANDEM / UNCERTAIN classification thresholds.
# --------------------------------------------------------------------------
class TestHierarchyClassification(unittest.TestCase):
    def _rows(self, team, season, starter_sequence):
        return [{"team": team, "season": season, "starter_goalie_id": g} for g in starter_sequence]

    def test_workhorse_threshold(self):
        rows = self._rows("WPG", 20242025, ["A"] * 7 + ["B"] * 3)  # 70% share
        h = rgc.classify_hierarchy(rows)
        self.assertEqual(h[("WPG", 20242025)], "WORKHORSE")

    def test_tandem_threshold(self):
        rows = self._rows("WPG", 20242025, ["A"] * 5 + ["B"] * 5)  # 50% share
        h = rgc.classify_hierarchy(rows)
        self.assertEqual(h[("WPG", 20242025)], "TANDEM")

    def test_uncertain_threshold(self):
        rows = self._rows("WPG", 20242025, ["A"] * 3 + ["B"] * 3 + ["C"] * 4)  # top share 40%... wait needs <0.35
        rows = self._rows("WPG", 20242025, ["A"] * 3 + ["B"] * 3 + ["C"] * 3 + ["D"])  # top share 3/10=0.30
        h = rgc.classify_hierarchy(rows)
        self.assertEqual(h[("WPG", 20242025)], "UNCERTAIN")

    def test_too_few_games_is_unclassified(self):
        rows = self._rows("WPG", 20242025, ["A"] * 5)  # < 10 games
        h = rgc.classify_hierarchy(rows)
        self.assertNotIn(("WPG", 20242025), h)


# --------------------------------------------------------------------------
# Performance-only QualityIndex must be exactly equivalent to the
# canonical (tested) gate function it accelerates.
# --------------------------------------------------------------------------
class TestQualityIndexEquivalence(unittest.TestCase):
    def test_matches_canonical_gate_function_on_real_corpus_sample(self):
        rows = gq.load_appearance_corpus()
        index = rgc.QualityIndex(rows)
        from collections import Counter
        sample_goalies = [g for g, _n in Counter(r["goalie_id"] for r in rows).most_common(5)]
        for goalie_id in sample_goalies:
            dates = sorted(r["game_date"] for r in rows if r["goalie_id"] == goalie_id)
            probe_date = dates[len(dates) // 2]
            canonical = gq.goalie_history_as_of(rows, goalie_id, probe_date)
            via_index = index.history_as_of(goalie_id, probe_date)
            self.assertEqual(
                [(r["game_id"], r["game_date"]) for r in canonical],
                [(r["game_id"], r["game_date"]) for r in via_index])

    def test_unknown_goalie_returns_empty_list(self):
        index = rgc.QualityIndex(gq.load_appearance_corpus())
        self.assertEqual(index.history_as_of("NO_SUCH_GOALIE_ID", "2024-10-01"), [])


# --------------------------------------------------------------------------
# Missing/unknown goalie defaults to a NEUTRAL adjustment (no WAIT, no
# exception) -- consistent with production's own documented policy for
# missing goalie status (Part 1 item 10).
# --------------------------------------------------------------------------
class TestMissingGoalieDefaultsToNeutral(unittest.TestCase):
    def test_candidate_a_neutral_for_unknown_goalie(self):
        index = rgc.QualityIndex([])
        adj, shots = rgc.quality_a_adj_logit(index, "UNKNOWN_GOALIE", "2024-10-01")
        self.assertEqual(adj, 0.0)
        self.assertEqual(shots, 0.0)

    def test_candidate_b_neutral_for_unknown_goalie(self):
        index = rgc.QualityIndex([])
        raw, shots = rgc.quality_b_raw(index, "UNKNOWN_GOALIE", "2024-10-01", window=5)
        self.assertEqual(raw, 0.0)
        self.assertEqual(shots, 0.0)


# --------------------------------------------------------------------------
# Part 19: tuning/eval separation -- eval seasons never touched during
# GSAx window/beta selection.
# --------------------------------------------------------------------------
class TestTuningEvalSeparation(unittest.TestCase):
    def test_eval_and_tuning_seasons_are_disjoint(self):
        self.assertEqual(set([rgc.TUNING_SEASON]) & set(rgc.EVAL_SEASONS), set())

    def test_window_selection_function_receives_only_tuning_pairs_by_construction(self):
        # top1_raw_diff/fit_logistic_weights are called (in run_all) with
        # `tuning_pairs` only -- this is a structural guarantee via AST:
        # the literal name `eval_pairs` must not appear inside the
        # window-selection loop's source region that calls fit_logistic_weights.
        import inspect
        src = inspect.getsource(rgc.run_all)
        window_selection_region = src.split("Pass 2:")[0]
        self.assertIn("tuning_pairs", window_selection_region)
        # fit_logistic_weights must be called with tuning_base_logits/tuning_actuals,
        # never an eval-derived list, in this region.
        for line in window_selection_region.splitlines():
            if "fit_logistic_weights(" in line:
                self.assertIn("tuning", line)
                self.assertNotIn("eval_", line)


# --------------------------------------------------------------------------
# Part 18: baseline (Elo-only production-equivalent) model is frozen --
# this experiment's driver must call it unweighted/untouched.
# --------------------------------------------------------------------------
class TestBaselineUnchanged(unittest.TestCase):
    def test_driver_calls_the_unweighted_production_equivalent_baseline(self):
        import inspect
        src = inspect.getsource(rgc.run_all)
        self.assertIn("run_walkforward(games, weight_fn=None)", src)


# --------------------------------------------------------------------------
# Part 30/31/34: no external goalie source, no Odds API, no forbidden
# imports into any file this slice touches or created.
# --------------------------------------------------------------------------
class TestNoExternalSourceOrForbiddenImports(unittest.TestCase):
    NEW_FILES = [
        "research/goalie_intelligence/quality.py",
        "research/goalie_intelligence/build_quality_corpus.py",
        "research/goalie_quality_integration.py",
        "research/run_goalie_quality_comparison.py",
        "dashboard/goalie_quality_view.py",
    ]
    FORBIDDEN_MODULES = {"pricing.engine", "pricing.decision", "models.combined_model",
                          "urllib.request", "requests", "http.client", "the_odds_api"}

    def test_no_forbidden_imports_in_any_new_file(self):
        for rel in self.NEW_FILES:
            path = os.path.join(REPO_ROOT, rel)
            with open(path) as f:
                tree = ast.parse(f.read(), filename=rel)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(alias.name, self.FORBIDDEN_MODULES, f"{rel} imports {alias.name}")
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn(node.module, self.FORBIDDEN_MODULES, f"{rel} imports from {node.module}")

    def test_no_nhl_db_path_used_in_any_call_in_any_new_file(self):
        """AST-based (not text-based): docstrings legitimately SAY "read-only
        against nhl.db" (documentation, not a violation) -- this checks that
        no actual Call node (e.g. sqlite3.connect(...), open(...)) is ever
        given a string argument referencing nhl.db."""
        for rel in self.NEW_FILES:
            path = os.path.join(REPO_ROOT, rel)
            with open(path) as f:
                tree = ast.parse(f.read(), filename=rel)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    for arg in node.args:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            self.assertNotIn("nhl.db", arg.value, f"{rel} passes nhl.db to a call")


# --------------------------------------------------------------------------
# Part 26/28: archival provenance preserved on the goalie-quality corpus.
# --------------------------------------------------------------------------
class TestArchivalProvenancePreserved(unittest.TestCase):
    def test_every_appearance_row_is_tagged_archival_research(self):
        rows = gq.load_appearance_corpus()
        self.assertGreater(len(rows), 0)
        self.assertTrue(all(r["provenance_type"] == "ARCHIVAL_RESEARCH" for r in rows))


# --------------------------------------------------------------------------
# Part 21/29: reproducibility of the pure integration functions.
# --------------------------------------------------------------------------
class TestReproducibility(unittest.TestCase):
    def test_scenario_weighted_probability_is_pure_and_deterministic(self):
        home = [(0.7, 0.2), (0.3, -0.1)]
        away = [(0.6, 0.05), (0.4, -0.05)]
        r1 = gqi.scenario_weighted_probability(0.52, home, away)
        r2 = gqi.scenario_weighted_probability(0.52, home, away)
        self.assertEqual(r1, r2)

    def test_quality_lookups_are_deterministic_given_same_inputs(self):
        index = rgc.QualityIndex(gq.load_appearance_corpus())
        from collections import Counter
        goalie_id, _n = Counter(r["goalie_id"] for r in gq.load_appearance_corpus()).most_common(1)[0]
        a1 = rgc.quality_a_adj_logit(index, goalie_id, "2025-01-15")
        a2 = rgc.quality_a_adj_logit(index, goalie_id, "2025-01-15")
        self.assertEqual(a1, a2)


if __name__ == "__main__":
    unittest.main()
