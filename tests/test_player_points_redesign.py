"""
Tests for the Player POINTS Redesign Cycle 2:
research/player_points/hierarchy.py, research/player_points/redesign.py,
and research/run_player_points_redesign.py. Covers Part 33's required
test areas. Shared PIT/count-distribution/confidence/conservative-
probability math is already exhaustively covered elsewhere in this
project (tests/test_player_sog_model.py, tests/test_player_points_model.py)
against the SAME unmodified research/player_sog/count_models.py -- this
file focuses on what is genuinely NEW: the empirical-baseline hierarchy,
the offset-GLM context adjustment, and the rolling-fold temporal design.
"""
import ast
import json
import os
import unittest

from research.player_points import features as ptf
from research.player_points import hierarchy as ph
from research.player_points import redesign as pr

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRIVER_PATH = os.path.join(REPO_ROOT, "research", "run_player_points_redesign.py")


def prow(player_id, game_id, game_date, season, team, opponent, points, position="C", pp=None, icetime=1000.0):
    return {"player_id": player_id, "player_name": player_id, "game_id": game_id, "season": season,
            "game_date": game_date, "team": team, "opponent": opponent, "home_or_away": "HOME",
            "position": position, "icetime_seconds": icetime, "toi_5v5_seconds": icetime * 0.7,
            "goals": 0.0, "primary_assists": 0.0, "secondary_assists": 0.0, "assists": 0.0,
            "points": points, "sog": 0.0, "shot_attempts": 2.0, "individual_xg": 0.1,
            "on_ice_xgf": 1.0, "on_ice_xga": 1.0, "pp": pp, "provenance_type": "ARCHIVAL_RESEARCH"}


# --------------------------------------------------------------------------
# Empirical baseline PIT integrity + correctness.
# --------------------------------------------------------------------------
class TestEmpiricalBaselinePIT(unittest.TestCase):
    def test_empirical_baseline_only_reads_prior_rows(self):
        rows = [prow("P1", 1, "2024-10-01", 20242025, "TOR", "MTL", points=3.0),
                prow("P1", 2, "2024-10-05", 20242025, "TOR", "BOS", points=0.0)]
        history = ptf.player_history_as_of(rows, "P1", "2024-10-05")
        self.assertEqual([r["points"] for r in history], [3.0])

    def test_flat_empirical_threshold_probs_uses_only_history(self):
        import research.run_player_points_redesign as rpr
        history = [prow("P1", i, f"2024-10-0{i}", 20242025, "TOR", "MTL", points=1.0) for i in range(1, 4)]
        league_rates = {1: 0.35, 2: 0.09, 3: 0.02}
        out = rpr.flat_empirical_threshold_probs(history, league_rates)
        self.assertGreater(out[1], league_rates[1])  # shrunk toward a 100%-history player rate, above league


# --------------------------------------------------------------------------
# Role hierarchy: tagging, shrinkage math, correctness.
# --------------------------------------------------------------------------
class TestRoleHierarchy(unittest.TestCase):
    def test_role_tag_forward_with_pp(self):
        row = prow("P1", 1, "2024-10-01", 20242025, "TOR", "MTL", points=1.0, position="C",
                    pp={"icetime_seconds": 90.0})
        self.assertEqual(ph.role_tag(row), "F_PP")

    def test_role_tag_defense_without_pp(self):
        row = prow("P1", 1, "2024-10-01", 20242025, "TOR", "MTL", points=1.0, position="D", pp=None)
        self.assertEqual(ph.role_tag(row), "D_NONPP")

    def test_target_role_tag_uses_prior_rolling_pp_not_target_game(self):
        self.assertEqual(ph.target_role_tag(True, 45.0), "F_PP")
        self.assertEqual(ph.target_role_tag(True, 0.0), "F_NONPP")

    def test_role_league_rates_built_from_train_rows_only(self):
        train_rows = [prow("P1", 1, "2023-10-01", 20222023, "TOR", "MTL", points=2.0, position="C"),
                      prow("P2", 1, "2023-10-01", 20222023, "MTL", "TOR", points=0.0, position="D")]
        rates = ph.RoleLeagueRates(train_rows)
        self.assertAlmostEqual(rates.league_mean, 1.0)
        self.assertIn("F_NONPP", rates.role_mean)
        self.assertIn("D_NONPP", rates.role_mean)

    def test_shrinkage_pulls_small_role_sample_toward_league(self):
        train_rows = [prow("P1", 1, "2023-10-01", 20222023, "TOR", "MTL", points=5.0, position="C")]
        rates = ph.RoleLeagueRates(train_rows)
        # a single-row role sample should be pulled heavily toward the
        # (in this toy case, identical) league mean by role_mean_shrunk
        shrunk = rates.role_mean_shrunk("F_NONPP", k_role=200)
        self.assertLessEqual(abs(shrunk - rates.league_mean), abs(rates.role_mean["F_NONPP"] - rates.league_mean) + 1e-9)

    def test_player_hierarchical_mean_shrinks_small_sample_toward_role(self):
        train_rows = [prow(f"OTHER{i}", i, "2023-10-01", 20222023, "TOR", "MTL", points=0.5, position="C")
                      for i in range(50)]
        rates = ph.RoleLeagueRates(train_rows)
        history = [prow("P1", 100, "2023-11-01", 20222023, "TOR", "MTL", points=4.0, position="C")]
        mean = ph.player_role_hierarchical_mean(history, "F_NONPP", rates, k_player=15)
        self.assertLess(mean, 4.0)
        self.assertGreater(mean, rates.role_mean_shrunk("F_NONPP"))

    def test_zero_history_returns_role_prior(self):
        train_rows = [prow("X", 1, "2023-10-01", 20222023, "TOR", "MTL", points=1.0, position="C")]
        rates = ph.RoleLeagueRates(train_rows)
        mean = ph.player_role_hierarchical_mean([], "F_NONPP", rates, k_player=15)
        self.assertEqual(mean, rates.role_mean_shrunk("F_NONPP"))


# --------------------------------------------------------------------------
# Season boundary / player-movement (Part 7/8): history is player-identity
# scoped, not team- or season-scoped -- carries across trades and season
# boundaries with no explicit reset, reusing the SAME shared gate as
# SOG/blocks/assists/Cycle-1-points.
# --------------------------------------------------------------------------
class TestSeasonBoundaryAndPlayerMovement(unittest.TestCase):
    def test_player_history_survives_team_change(self):
        rows = [prow("P1", 1, "2024-10-01", 20242025, "TOR", "MTL", points=2.0),
                prow("P1", 2, "2024-12-15", 20242025, "NYR", "BOS", points=1.0)]
        history = ptf.player_history_as_of(rows, "P1", "2024-12-20")
        self.assertEqual(len(history), 2)

    def test_player_history_carries_across_season_boundary(self):
        rows = [prow("P1", 1, "2024-04-01", 20232024, "TOR", "MTL", points=3.0),
                prow("P1", 2, "2024-10-10", 20242025, "TOR", "BOS", points=1.0)]
        history = ptf.player_history_as_of(rows, "P1", "2024-10-15")
        self.assertEqual(len(history), 2)  # no reset at the season boundary


# --------------------------------------------------------------------------
# Low-sample prior / coherent thresholds.
# --------------------------------------------------------------------------
class TestLowSamplePriorAndCoherence(unittest.TestCase):
    def test_low_sample_player_weighted_mostly_toward_prior(self):
        train_rows = [prow(f"O{i}", i, "2023-10-01", 20222023, "TOR", "MTL", points=0.4, position="D")
                      for i in range(50)]
        rates = ph.RoleLeagueRates(train_rows)
        history = [prow("P1", 100, "2023-10-05", 20222023, "TOR", "MTL", points=5.0, position="D")]
        mean_k15 = ph.player_role_hierarchical_mean(history, "D_NONPP", rates, k_player=15)
        role_prior = rates.role_mean_shrunk("D_NONPP")
        # with only 1 game, weight = 1/(1+15) ~ 6% toward the observed value
        self.assertAlmostEqual(mean_k15, role_prior + (1 / 16) * (5.0 - role_prior), places=6)

    def test_threshold_probabilities_from_hierarchical_mean_are_monotonic(self):
        from research.player_sog import count_models as cm
        probs = cm.threshold_probabilities(0.8, None, thresholds=(1, 2, 3))
        self.assertGreaterEqual(probs[1] + 1e-9, probs[2])
        self.assertGreaterEqual(probs[2] + 1e-9, probs[3])


# --------------------------------------------------------------------------
# Context adjustment (offset-GLM) correctness and coherence.
# --------------------------------------------------------------------------
class TestContextAdjustment(unittest.TestCase):
    def test_offset_glm_zero_weights_reduces_to_the_offset(self):
        weights = [0.0, 0.0, 0.0, 0.0]
        mu = pr.predict_mu_with_offset(weights, [1.0, 0.5, -0.2, 0.1], offset=0.0)
        self.assertAlmostEqual(mu, 1.0)

    def test_fit_offset_glm_recovers_a_simple_signal(self):
        # y = exp(offset) always in this toy case (feature has no real
        # effect) -- fitted weight should stay near zero, not diverge.
        fm = [[1.0], [1.0], [1.0], [1.0]]
        obs = [1.0, 1.0, 1.0, 1.0]
        offsets = [0.0, 0.0, 0.0, 0.0]
        w = pr.fit_poisson_glm_with_offset(fm, obs, offsets, n_iter=50)
        self.assertLess(abs(w[0]), 2.0)  # bounded, did not diverge

    def test_context_feature_vector_treats_missing_signals_as_neutral(self):
        fv = pr.context_feature_vector(None, None, None, 0.0)
        self.assertEqual(fv, [0.0, 0.0, 0.0, 0.0])

    def test_monotonicity_guaranteed_structurally_for_candidate3(self):
        # candidate 3 always derives P(1+)>=P(2+)>=P(3+) from ONE coherent
        # NegBin/Poisson shape at the adjusted mean -- never per-threshold
        # independent adjustments that could cross.
        from research.player_sog import count_models as cm
        mu_adjusted = pr.predict_mu_with_offset([0.5, 0.1, -0.05, 0.02], [0.3, 0.1, 0.0, 0.05], offset=-0.5)
        probs = cm.threshold_probabilities(mu_adjusted, None, thresholds=(1, 2, 3))
        self.assertGreaterEqual(probs[1] + 1e-9, probs[2])
        self.assertGreaterEqual(probs[2] + 1e-9, probs[3])


# --------------------------------------------------------------------------
# Common evaluation rows, rolling temporal folds.
# --------------------------------------------------------------------------
class TestRollingFoldDesign(unittest.TestCase):
    def setUp(self):
        import research.run_player_points_redesign as rpr
        self.rpr = rpr

    def test_folds_are_strictly_rolling_origin(self):
        folds = self.rpr.FOLDS
        self.assertEqual(len(folds), 3)
        for i, fold in enumerate(folds):
            self.assertNotIn(fold["val_season"], fold["train_seasons"])
            if i > 0:
                self.assertGreater(fold["val_season"], folds[i - 1]["val_season"])

    def test_real_season_date_ranges_never_overlap(self):
        rows = ptf.load_points_corpus() if os.path.exists(
            os.path.join(REPO_ROOT, "research", "player_points", "player_game_points.jsonl")) else None
        if rows is None:
            self.skipTest("player_game_points.jsonl not built in this environment")
        by_season_range = {}
        for r in rows:
            s = r["season"]
            if s not in by_season_range:
                by_season_range[s] = [r["game_date"], r["game_date"]]
            else:
                by_season_range[s][0] = min(by_season_range[s][0], r["game_date"])
                by_season_range[s][1] = max(by_season_range[s][1], r["game_date"])
        seasons_sorted = sorted(by_season_range)
        for a, b in zip(seasons_sorted, seasons_sorted[1:]):
            self.assertLess(by_season_range[a][1], by_season_range[b][0],
                             f"season {a} and {b} date ranges overlap -- fold-level PIT assumption would be violated")


# --------------------------------------------------------------------------
# Game-clustered bootstrap, date-clustered sensitivity (reused pattern).
# --------------------------------------------------------------------------
class TestClusteredResampling(unittest.TestCase):
    def test_game_clustered_bootstrap_clusters_by_game_id(self):
        import research.run_player_points_redesign as rpr
        examples = [{"game_id": 1, "game_date": "d1"}, {"game_id": 1, "game_date": "d1"}, {"game_id": 2, "game_date": "d2"}]
        result = rpr.game_clustered_bootstrap(examples, [0.1, 0.1, 0.2], [0.05, 0.05, 0.2], n_resamples=20)
        self.assertEqual(result["n_games_resampled"], 2)

    def test_date_clustered_bootstrap_clusters_by_date(self):
        import research.run_player_points_redesign as rpr
        examples = [{"game_id": 1, "game_date": "d1"}, {"game_id": 2, "game_date": "d1"}, {"game_id": 3, "game_date": "d2"}]
        result = rpr.date_clustered_bootstrap(examples, [0.1, 0.1, 0.2], [0.05, 0.05, 0.2], n_resamples=20)
        self.assertEqual(result["n_dates_resampled"], 2)

    def test_no_bare_row_level_bootstrap_in_driver(self):
        with open(DRIVER_PATH) as f:
            src = f.read()
        self.assertNotIn("def row_level_bootstrap", src)
        self.assertIn("def game_clustered_bootstrap", src)
        self.assertIn("def date_clustered_bootstrap", src)


# --------------------------------------------------------------------------
# No leakage: target-game / same-day / future rows never leak in.
# --------------------------------------------------------------------------
class TestNoLeakage(unittest.TestCase):
    def test_no_target_game_leakage(self):
        rows = [prow("P1", 1, "2024-10-05", 20242025, "TOR", "MTL", points=5.0)]
        history = ptf.player_history_as_of(rows, "P1", "2024-10-05")
        self.assertEqual(history, [])

    def test_no_same_day_leakage(self):
        rows = [prow("P1", 1, "2024-10-05", 20242025, "TOR", "MTL", points=5.0),
                prow("P2", 2, "2024-10-05", 20242025, "TOR", "MTL", points=5.0)]
        history = ptf.player_history_as_of(rows, "P1", "2024-10-05")
        self.assertEqual(history, [])

    def test_no_future_leakage(self):
        rows = [prow("P1", 1, "2024-11-01", 20242025, "TOR", "MTL", points=5.0)]
        history = ptf.player_history_as_of(rows, "P1", "2024-10-05")
        self.assertEqual(history, [])


# --------------------------------------------------------------------------
# Model registry status, honest evaluation-status labeling.
# --------------------------------------------------------------------------
class TestRegistryAndEvaluationStatus(unittest.TestCase):
    RESULTS_PATH = os.path.join(REPO_ROOT, "research", "player_points_redesign_results.json")

    def test_registry_has_valid_points_status(self):
        from research.player_props import registry
        entry = registry.get("POINTS")
        self.assertIsNotNone(entry)
        self.assertIn(entry.model_status,
                       ("PARTIAL", "VALIDATED", "RESEARCH", "REJECTED", "EMPIRICAL_BASELINE_REMAINS_CHAMPION"))

    def test_results_declare_reused_historical_data_status(self):
        if not os.path.exists(self.RESULTS_PATH):
            self.skipTest("player_points_redesign_results.json not built in this environment")
        with open(self.RESULTS_PATH) as f:
            results = json.load(f)
        self.assertEqual(results["evaluation_status"], "REUSED HISTORICAL DATA UNDER NEW DEVELOPMENT CYCLE")

    def test_driver_never_claims_pristine_holdout(self):
        with open(DRIVER_PATH) as f:
            text = f.read()
        self.assertNotIn("pristine holdout", text.lower().replace("not pristine holdout", ""))
        self.assertIn("REUSED HISTORICAL DATA", text)


# --------------------------------------------------------------------------
# Production model / other prop models unchanged.
# --------------------------------------------------------------------------
class TestOtherModelsUnchanged(unittest.TestCase):
    NEW_FILES = ["research/player_points/hierarchy.py", "research/player_points/redesign.py",
                 "research/run_player_points_redesign.py"]
    FORBIDDEN_MODULES = {"pricing.engine", "pricing.decision", "models.combined_model"}

    def test_no_forbidden_imports_and_no_nhl_db_reference(self):
        for rel in self.NEW_FILES:
            with open(os.path.join(REPO_ROOT, rel)) as f:
                tree = ast.parse(f.read(), filename=rel)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn(node.module, self.FORBIDDEN_MODULES, f"{rel} imports {node.module}")
                if isinstance(node, ast.Call):
                    for arg in node.args:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            self.assertNotIn("nhl.db", arg.value)

    def test_count_models_module_never_imports_from_redesign(self):
        with open(os.path.join(REPO_ROOT, "research", "player_sog", "count_models.py")) as f:
            text = f.read()
        self.assertNotIn("player_points.redesign", text)
        self.assertNotIn("run_player_points_redesign", text)

    def test_sog_blocks_assists_modules_unreferenced_by_redesign_files(self):
        for rel in self.NEW_FILES:
            path = os.path.join(REPO_ROOT, rel)
            if not os.path.exists(path):
                continue
            with open(path) as f:
                text = f.read()
            self.assertNotIn("player_blocks", text)
            self.assertNotIn("player_assists", text)

    def test_cycle1_locked_points_model_reused_not_refit(self):
        # the redesign driver must READ the Cycle-1 results file (for
        # candidate C5's reference weights) but never re-run/re-fit it.
        with open(DRIVER_PATH) as f:
            text = f.read()
        self.assertIn("player_points_results.json", text)
        self.assertNotIn("fit_poisson_glm(", text)  # only the offset variant is fit here, never the original


if __name__ == "__main__":
    unittest.main()
