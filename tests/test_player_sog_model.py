"""
Tests for the Player Shots-on-Goal Probability + Confidence Foundation:
research/player_sog/features.py, research/player_sog/count_models.py,
and the structural/PIT/labeling guarantees of
research/run_player_sog_model.py and the dashboard SOG panel. See
PLAYER_SOG_FOUNDATION_REPORT.md for the full experiment writeup.

Small hand-built fixtures for formula/PIT-gate correctness (mirrors
tests/test_goalie_quality_integration.py's style); a handful of real-
corpus spot checks for cross-validation.
"""
import ast
import inspect
import math
import os
import unittest

from research.player_sog import features as pf
from research.player_sog import count_models as cm

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def prow(player_id, game_id, game_date, season, team, opponent, sog, icetime=1000.0,
         home_or_away="HOME", shot_attempts=None, x_goals=0.1, pp=None):
    return {
        "player_id": player_id, "player_name": player_id, "game_id": game_id, "season": season,
        "game_date": game_date, "team": team, "opponent": opponent, "home_or_away": home_or_away,
        "position": "C", "icetime_seconds": icetime, "sog": sog,
        "shot_attempts": shot_attempts if shot_attempts is not None else sog + 1.0,
        "unblocked_shot_attempts": sog + 0.5, "x_on_goal": sog * 0.9, "x_goals": x_goals,
        "rebounds": 0.0, "low_danger_shots": sog, "medium_danger_shots": 0.0, "high_danger_shots": 0.0,
        "on_ice_xgf": 1.0, "on_ice_xga": 1.0, "pp": pp, "provenance_type": "ARCHIVAL_RESEARCH",
    }


# --------------------------------------------------------------------------
# Parts 1/2/3: target-game / future exclusion, same-day exclusion.
# --------------------------------------------------------------------------
class TestPlayerHistoryAsOfPIT(unittest.TestCase):
    def setUp(self):
        self.rows = [
            prow("P1", 1, "2024-10-01", 20242025, "TOR", "MTL", sog=3.0),
            prow("P1", 2, "2024-10-05", 20242025, "TOR", "BOS", sog=4.0),  # target game's own row
            prow("P1", 3, "2024-10-09", 20242025, "TOR", "OTT", sog=2.0),  # future
        ]

    def test_excludes_target_games_own_row_and_future_rows(self):
        history = pf.player_history_as_of(self.rows, "P1", "2024-10-05")
        dates = [r["game_date"] for r in history]
        self.assertEqual(dates, ["2024-10-01"])

    def test_excludes_same_day_row_even_if_different_game_id(self):
        rows = self.rows + [prow("P1", 99, "2024-10-05", 20242025, "TOR", "BUF", sog=5.0)]
        history = pf.player_history_as_of(rows, "P1", "2024-10-05")
        self.assertTrue(all(r["game_date"] < "2024-10-05" for r in history))

    def test_player_identity_survives_a_team_change(self):
        rows = [
            prow("P1", 1, "2024-10-01", 20242025, "TOR", "MTL", sog=3.0),
            prow("P1", 2, "2024-11-15", 20242025, "NYR", "BOS", sog=4.0),  # traded mid-season
        ]
        history = pf.player_history_as_of(rows, "P1", "2024-11-20")
        self.assertEqual(len(history), 2)
        self.assertEqual({r["team"] for r in history}, {"TOR", "NYR"})


class TestPlayerHistoryIndexEquivalence(unittest.TestCase):
    def test_index_matches_canonical_gate_function(self):
        rows = [
            prow("P1", 1, "2024-10-01", 20242025, "TOR", "MTL", sog=3.0),
            prow("P1", 2, "2024-10-05", 20242025, "TOR", "BOS", sog=4.0),
            prow("P2", 3, "2024-10-03", 20242025, "OTT", "BUF", sog=1.0),
        ]
        index = pf.PlayerHistoryIndex(rows)
        for pid, date in [("P1", "2024-10-05"), ("P1", "2024-10-09"), ("P2", "2024-10-10")]:
            canonical = pf.player_history_as_of(rows, pid, date)
            via_index = index.history_as_of(pid, date)
            self.assertEqual([(r["game_id"], r["game_date"]) for r in canonical],
                              [(r["game_id"], r["game_date"]) for r in via_index])


# --------------------------------------------------------------------------
# Part 4/5: rolling baseline / recent-form calculations.
# --------------------------------------------------------------------------
class TestRollingMeans(unittest.TestCase):
    def test_rolling_mean_uses_most_recent_window(self):
        history = [prow("P1", i, f"2024-10-{i:02d}", 20242025, "TOR", "X", sog=float(i)) for i in range(1, 6)]
        self.assertAlmostEqual(pf.rolling_mean(history, "sog", 2), (4.0 + 5.0) / 2)
        self.assertAlmostEqual(pf.rolling_mean(history, "sog", None), sum(range(1, 6)) / 5)

    def test_rolling_mean_none_when_no_history(self):
        self.assertIsNone(pf.rolling_mean([], "sog", 5))

    def test_season_to_date_mean_scoped_to_season(self):
        history = [
            prow("P1", 1, "2023-10-01", 20232024, "TOR", "X", sog=1.0),
            prow("P1", 2, "2024-10-01", 20242025, "TOR", "X", sog=5.0),
        ]
        self.assertAlmostEqual(pf.season_to_date_mean(history, "sog", 20242025), 5.0)
        self.assertIsNone(pf.season_to_date_mean(history, "sog", 20252026))

    def test_on_target_conversion_rate(self):
        history = [prow("P1", 1, "2024-10-01", 20242025, "TOR", "X", sog=3.0, shot_attempts=6.0)]
        self.assertAlmostEqual(pf.on_target_conversion_rate(history, None), 0.5)


# --------------------------------------------------------------------------
# Part 9/10: TOI / PP TOI calculation.
# --------------------------------------------------------------------------
class TestTOIAndPPCalculation(unittest.TestCase):
    def test_pp_rolling_mean_treats_none_pp_as_zero(self):
        history = [
            prow("P1", 1, "2024-10-01", 20242025, "TOR", "X", sog=1.0,
                 pp={"icetime_seconds": 60.0, "shots_on_goal": 1.0, "shot_attempts": 2.0, "x_on_goal": 0.5}),
            prow("P1", 2, "2024-10-03", 20242025, "TOR", "X", sog=1.0, pp=None),
        ]
        self.assertAlmostEqual(pf.rolling_pp_mean(history, "icetime_seconds", None), 30.0)

    def test_toi_rolling_mean(self):
        history = [prow("P1", i, f"2024-10-{i:02d}", 20242025, "TOR", "X", sog=1.0, icetime=1000.0 + i)
                   for i in range(1, 4)]
        self.assertAlmostEqual(pf.rolling_mean(history, "icetime_seconds", None), statistics_mean([1001, 1002, 1003]))


def statistics_mean(vals):
    return sum(vals) / len(vals)


# --------------------------------------------------------------------------
# Part 6/27: opponent shot-environment temporal integrity.
# --------------------------------------------------------------------------
class TestOpponentContext(unittest.TestCase):
    def test_opponent_allowed_history_uses_offensive_output_of_the_team_that_scored_on_them(self):
        rows = [
            prow("P1", 1, "2024-10-01", 20242025, "TOR", "MTL", sog=5.0),
            prow("P2", 1, "2024-10-01", 20242025, "MTL", "TOR", sog=2.0),
        ]
        totals = pf.build_team_game_totals(rows)
        allowed = pf.build_opponent_allowed_history(totals)
        # MTL allowed TOR's 5 SOG; TOR allowed MTL's 2 SOG.
        self.assertEqual(allowed["MTL"][0]["sog_allowed"], 5.0)
        self.assertEqual(allowed["TOR"][0]["sog_allowed"], 2.0)

    def test_opponent_history_as_of_is_pit_safe(self):
        rows = [
            prow("P1", 1, "2024-10-01", 20242025, "TOR", "MTL", sog=5.0),
            prow("P2", 1, "2024-10-01", 20242025, "MTL", "TOR", sog=2.0),
            prow("P1", 2, "2024-10-10", 20242025, "TOR", "BOS", sog=1.0),
            prow("P3", 2, "2024-10-10", 20242025, "BOS", "TOR", sog=9.0),
        ]
        totals = pf.build_team_game_totals(rows)
        allowed = pf.build_opponent_allowed_history(totals)
        hist_before_second_game = pf.opponent_history_as_of(allowed, "TOR", "2024-10-10")
        self.assertEqual(len(hist_before_second_game), 1)
        self.assertEqual(hist_before_second_game[0]["sog_allowed"], 2.0)


# --------------------------------------------------------------------------
# Part 7/28: head-to-head, strictly prior-game, shrunk by sample size.
# --------------------------------------------------------------------------
class TestH2H(unittest.TestCase):
    def test_h2h_history_filters_to_the_named_opponent_only(self):
        history = [
            prow("P1", 1, "2024-10-01", 20242025, "TOR", "MTL", sog=5.0),
            prow("P1", 2, "2024-10-05", 20242025, "TOR", "BOS", sog=1.0),
        ]
        h2h = pf.h2h_history(history, "MTL")
        self.assertEqual(len(h2h), 1)
        self.assertEqual(h2h[0]["opponent"], "MTL")

    def test_small_h2h_sample_is_shrunk_toward_baseline(self):
        history = [prow("P1", 1, "2024-10-01", 20242025, "TOR", "MTL", sog=10.0)]  # one extreme H2H game
        baseline = 2.0
        shrunk_rate, n = pf.h2h_shrunk_sog_rate(history, "MTL", baseline)
        self.assertEqual(n, 1)
        self.assertLess(shrunk_rate, 10.0)
        self.assertGreater(shrunk_rate, baseline)  # pulled toward but not all the way to baseline

    def test_larger_h2h_sample_shrinks_less(self):
        history_small = [prow("P1", 1, "2024-10-01", 20242025, "TOR", "MTL", sog=10.0)]
        history_large = [prow("P1", i, f"2024-10-{i:02d}", 20242025, "TOR", "MTL", sog=10.0) for i in range(1, 9)]
        r_small, _ = pf.h2h_shrunk_sog_rate(history_small, "MTL", 2.0)
        r_large, _ = pf.h2h_shrunk_sog_rate(history_large, "MTL", 2.0)
        self.assertGreater(r_large, r_small)  # more H2H evidence pulls further from baseline toward 10.0

    def test_zero_h2h_games_returns_baseline_unchanged(self):
        history = [prow("P1", 1, "2024-10-01", 20242025, "TOR", "BOS", sog=10.0)]
        rate, n = pf.h2h_shrunk_sog_rate(history, "MTL", 2.5)
        self.assertEqual(n, 0)
        self.assertEqual(rate, 2.5)


# --------------------------------------------------------------------------
# Part 3: PROJECTED ACTIVE eligibility.
# --------------------------------------------------------------------------
class TestProjectedActiveEligibility(unittest.TestCase):
    def test_eligible_when_appeared_enough_of_recent_team_games(self):
        team_sched = [{"game_date": f"2024-10-{i:02d}"} for i in range(1, 11)]
        history = [prow("P1", i, f"2024-10-{i:02d}", 20242025, "TOR", "X", sog=1.0) for i in range(1, 8)]
        self.assertTrue(pf.projected_active(history, team_sched))

    def test_ineligible_when_mostly_absent_recently(self):
        team_sched = [{"game_date": f"2024-10-{i:02d}"} for i in range(1, 11)]
        history = [prow("P1", 1, "2024-10-01", 20242025, "TOR", "X", sog=1.0)]  # only 1 of 10 team games
        self.assertFalse(pf.projected_active(history, team_sched))

    def test_label_is_projected_not_confirmed(self):
        # Structural: the eligibility function's own name/vocabulary never
        # claims "confirmed" -- see also TestDashboardLabeling below.
        src = inspect.getsource(pf.projected_active)
        self.assertNotIn("CONFIRMED", src.upper().replace("PROJECTED_INACTIVE", ""))


# --------------------------------------------------------------------------
# Part 15/17: count distributions.
# --------------------------------------------------------------------------
class TestCountDistributions(unittest.TestCase):
    def test_poisson_pmf_table_sums_to_one(self):
        table = cm.full_pmf_table(3.2, None, max_k=6)
        self.assertAlmostEqual(sum(table.values()), 1.0, places=9)

    def test_negbinom_pmf_table_sums_to_one(self):
        table = cm.full_pmf_table(3.2, 0.4, max_k=6)
        self.assertAlmostEqual(sum(table.values()), 1.0, places=9)

    def test_negbinom_reduces_to_poisson_at_alpha_zero(self):
        for k in range(6):
            self.assertAlmostEqual(cm.negbinom_pmf(k, 3.0, 1e-9), cm.poisson_pmf(k, 3.0), places=5)

    def test_threshold_monotonicity_poisson(self):
        th = cm.threshold_probabilities(3.5, None)
        for n in range(1, 6):
            self.assertGreaterEqual(th[n] + 1e-9, th[n + 1])

    def test_threshold_monotonicity_negbinom(self):
        th = cm.threshold_probabilities(3.5, 0.5)
        for n in range(1, 6):
            self.assertGreaterEqual(th[n] + 1e-9, th[n + 1])

    def test_5plus_le_4plus_le_3plus_le_2plus(self):
        th = cm.threshold_probabilities(4.1, 0.3)
        self.assertLessEqual(th[5], th[4] + 1e-9)
        self.assertLessEqual(th[4], th[3] + 1e-9)
        self.assertLessEqual(th[3], th[2] + 1e-9)

    def test_overdispersion_stats_flags_variance_exceeding_mean(self):
        counts = [0, 0, 0, 1, 1, 2, 8, 9, 0, 0]  # deliberately overdispersed
        stats = cm.overdispersion_stats(counts)
        self.assertGreater(stats["variance"], stats["mean"])
        self.assertGreater(stats["variance_to_mean_ratio"], 1.0)


# --------------------------------------------------------------------------
# Part 16: the small interpretable Poisson GLM -- reproducibility and
# training data isolation (actual SOG used only as the fitting label,
# never smuggled into the feature vector itself).
# --------------------------------------------------------------------------
class TestPoissonGLM(unittest.TestCase):
    def test_expected_sog_reproducible(self):
        fm = [[1.0, math.log(2.0), 0.1, 0.0, 0.0, 0.0], [1.0, math.log(1.0), -0.2, 0.1, 0.0, 0.0]]
        obs = [2.0, 1.0]
        w1 = cm.fit_poisson_glm(fm, obs, n_iter=50)
        w2 = cm.fit_poisson_glm(fm, obs, n_iter=50)
        self.assertEqual(w1, w2)
        mu1 = cm.predict_mu(w1, fm[0])
        mu2 = cm.predict_mu(w1, fm[0])
        self.assertEqual(mu1, mu2)

    def test_build_feature_vector_signature_has_no_actual_sog_parameter(self):
        params = set(inspect.signature(cm.build_feature_vector).parameters)
        self.assertFalse(any("actual" in p or p == "sog" for p in params))

    def test_higher_baseline_rate_increases_expected_sog(self):
        fv_low = cm.build_feature_vector(1.0, None, None, None, None, 0.0)
        fv_high = cm.build_feature_vector(4.0, None, None, None, None, 0.0)
        w = [0.0, 1.0, 0.0, 0.0, 0.0, 0.0]  # weight 1.0 directly on log_baseline_rate
        self.assertGreater(cm.predict_mu(w, fv_high), cm.predict_mu(w, fv_low))


# --------------------------------------------------------------------------
# Part 18: confidence layer -- reproducibility and documented drivers.
# --------------------------------------------------------------------------
class TestConfidence(unittest.TestCase):
    def test_confidence_reproducible(self):
        args = (50, 0.1, 0.3, 25, 20, 0.95)
        r1 = cm.confidence_score(*args)
        r2 = cm.confidence_score(*args)
        self.assertEqual(r1, r2)

    def test_small_sample_and_volatility_never_scores_high(self):
        label, pos, risk = cm.confidence_score(
            n_history_games=5, recent_toi_cv=0.6, recent_sog_cv=1.5,
            opponent_window_games=2, opponent_window_target=20, appearance_rate=0.3)
        self.assertEqual(label, "LOW")
        self.assertTrue(len(risk) > 0)

    def test_large_stable_sample_scores_high(self):
        label, pos, risk = cm.confidence_score(
            n_history_games=80, recent_toi_cv=0.05, recent_sog_cv=0.2,
            opponent_window_games=20, opponent_window_target=20, appearance_rate=1.0)
        self.assertEqual(label, "HIGH")
        self.assertTrue(len(pos) >= 3)


# --------------------------------------------------------------------------
# Part 19: conservative probability -- never exceeds raw probability, and
# is not a flat percentage-point subtraction (varies with sample size).
# --------------------------------------------------------------------------
class TestConservativeProbability(unittest.TestCase):
    def test_conservative_mu_never_exceeds_raw_mu(self):
        for n in (1, 3, 10, 50):
            self.assertLessEqual(cm.conservative_mu(4.0, n), 4.0 + 1e-9)

    def test_conservative_mu_varies_with_sample_size_not_a_flat_subtraction(self):
        c_small_n = cm.conservative_mu(4.0, 3)
        c_large_n = cm.conservative_mu(4.0, 50)
        # a flat "subtract X" rule would give the same result regardless of
        # n; this must not.
        self.assertNotAlmostEqual(4.0 - c_small_n, 4.0 - c_large_n, places=6)
        self.assertGreater(c_large_n, c_small_n)  # more evidence -> tighter (higher) lower bound

    def test_conservative_threshold_probability_never_exceeds_raw(self):
        mu = 4.0
        cmu = cm.conservative_mu(mu, 10)
        for t in range(1, 7):
            self.assertLessEqual(cm.poisson_sf_at_least(t, cmu), cm.poisson_sf_at_least(t, mu) + 1e-9)


# --------------------------------------------------------------------------
# Part 26/30: calibration-table calculation correctness.
# --------------------------------------------------------------------------
class TestCalibrationCalculation(unittest.TestCase):
    def test_calibration_bucket_actual_rate_matches_hand_computed_value(self):
        import research.run_player_sog_model as rm
        examples = [{"actual_sog": 4.0}, {"actual_sog": 1.0}, {"actual_sog": 5.0}, {"actual_sog": 0.0}]
        mus = [3.5, 3.5, 3.5, 3.5]  # same mu -> same predicted prob for all four, single bucket
        table = rm.calibration_table(examples, mus, None, threshold=4, edges=(0.0, 1.0))
        self.assertEqual(table[0]["n"], 4)
        self.assertAlmostEqual(table[0]["actual_rate"], 2 / 4)  # two of four had actual_sog >= 4


# --------------------------------------------------------------------------
# Part 20/22: train/eval separation, no target-game leakage into the fit.
# --------------------------------------------------------------------------
class TestTuningEvalSeparation(unittest.TestCase):
    def test_eval_and_tuning_seasons_are_disjoint(self):
        import research.run_player_sog_model as rm
        self.assertEqual({rm.TUNING_SEASON} & set(rm.EVAL_SEASONS), set())

    def test_glm_fitting_region_of_run_all_never_references_eval_examples(self):
        import research.run_player_sog_model as rm
        src = inspect.getsource(rm.run_all)
        fit_region = src.split("Pass 2:")[0] if "Pass 2:" in src else src.split("stage_mus")[0]
        self.assertIn("tuning", fit_region)
        for line in fit_region.splitlines():
            if "fit_poisson_glm(" in line or "fit_negbinom_alpha" in line:
                self.assertNotIn("eval_", line)


# --------------------------------------------------------------------------
# Part 23: no sportsbook odds used as a model feature anywhere.
# --------------------------------------------------------------------------
class TestNoSportsbookOddsAsFeature(unittest.TestCase):
    FORBIDDEN_TOKENS = ("draftkings", "the_odds_api", "sportsbook_price", "moneyline_odds", "book_price")

    def test_no_odds_terms_in_feature_or_model_source(self):
        for rel in ("research/player_sog/features.py", "research/player_sog/count_models.py",
                    "research/run_player_sog_model.py"):
            with open(os.path.join(REPO_ROOT, rel)) as f:
                text = f.read().lower()
            for token in self.FORBIDDEN_TOKENS:
                self.assertNotIn(token, text, f"{rel} references {token}")


# --------------------------------------------------------------------------
# Part 24/29/43: dashboard research labeling -- lineup status must never
# claim CONFIRMED, and the RESEARCH banner must be present.
# --------------------------------------------------------------------------
class TestDashboardLabeling(unittest.TestCase):
    def test_dashboard_page_never_outputs_confirmed_active(self):
        path = os.path.join(REPO_ROOT, "dashboard", "pages", "7_Player_SOG_Research.py")
        with open(path) as f:
            text = f.read()
        self.assertNotIn("CONFIRMED ACTIVE", text)
        self.assertIn("PROJECTED ACTIVE", text)
        self.assertIn("RESEARCH", text)

    def test_player_sog_view_never_produces_confirmed_status(self):
        """AST-based, excluding docstrings: the module docstring
        legitimately explains that status is "PROJECTED ACTIVE, never
        CONFIRMED ACTIVE" -- that is documentation, not a violation. This
        checks only string literals that appear as actual dict/return
        VALUES in the code (e.g. a `"status": "..."` entry), never prose."""
        path = os.path.join(REPO_ROOT, "dashboard", "player_sog_view.py")
        with open(path) as f:
            tree = ast.parse(f.read(), filename="dashboard/player_sog_view.py")
        docstring_nodes = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                body = node.body
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                        and isinstance(body[0].value.value, str):
                    docstring_nodes.add(id(body[0].value))
        string_constants = [n.value for n in ast.walk(tree)
                             if isinstance(n, ast.Constant) and isinstance(n.value, str)
                             and id(n) not in docstring_nodes]
        self.assertFalse(any("CONFIRMED" in s.upper() for s in string_constants),
                          [s for s in string_constants if "CONFIRMED" in s.upper()])


# --------------------------------------------------------------------------
# Part 25: production NHL game model unchanged; no forbidden imports.
# --------------------------------------------------------------------------
class TestProductionModelUnchanged(unittest.TestCase):
    NEW_FILES = [
        "research/player_sog/features.py", "research/player_sog/count_models.py",
        "research/player_sog/build_sog_corpus.py", "research/run_player_sog_model.py",
        "dashboard/player_sog_view.py", "dashboard/pages/7_Player_SOG_Research.py",
    ]
    FORBIDDEN_MODULES = {"pricing.engine", "pricing.decision", "models.combined_model",
                          "urllib.request", "requests", "http.client"}

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

    def test_no_nhl_db_path_used_in_any_call(self):
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
# Part 21/26: archival provenance preserved on the real SOG corpus.
# --------------------------------------------------------------------------
class TestArchivalProvenance(unittest.TestCase):
    def test_corpus_rows_are_tagged_archival_research(self):
        path = os.path.join(REPO_ROOT, "research", "player_sog", "player_game_sog.jsonl")
        if not os.path.exists(path):
            self.skipTest("player_game_sog.jsonl not built in this environment")
        rows = pf.load_sog_corpus(path)
        self.assertGreater(len(rows), 0)
        self.assertTrue(all(r["provenance_type"] == "ARCHIVAL_RESEARCH" for r in rows[:2000]))


if __name__ == "__main__":
    unittest.main()
