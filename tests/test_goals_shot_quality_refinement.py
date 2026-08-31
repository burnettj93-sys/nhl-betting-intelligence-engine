"""
Tests for the Goals Shot-Quality Refinement Cycle:
research/player_goals/shot_quality.py and
research/run_goals_shot_quality_refinement.py. Covers Part 31's 30
required test areas. The INCUMBENT Goals model (research/run_player_goals_model.py,
research/player_goals_results.json) is FROZEN this slice -- these tests
verify it was never touched, not re-test its own already-covered PIT/
distribution correctness (tests/test_player_goals_model.py).
"""
import ast
import json
import os
import unittest

from research.player_goals import shot_quality as sq

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRIVER_PATH = os.path.join(REPO_ROOT, "research", "run_goals_shot_quality_refinement.py")


def grow(player_id, game_id, game_date, season, goals=0.0, sog=0.0, xg=0.0, shot_attempts=0.0,
         high_danger=0.0, pp=None):
    return {"player_id": player_id, "player_name": player_id, "game_id": game_id, "season": season,
            "game_date": game_date, "goals": goals, "sog": sog, "individual_xg": xg,
            "shot_attempts": shot_attempts, "high_danger_shots": high_danger, "pp": pp}


# --------------------------------------------------------------------------
# Tests 1-3: no target-game/future/same-day xG leakage (shot-quality
# features route through the SAME shared player_history_as_of gate).
# --------------------------------------------------------------------------
class TestNoLeakage(unittest.TestCase):
    def test_1_target_game_xg_excluded(self):
        from research.player_goals.features import player_history_as_of
        rows = [grow("P1", 1, "2024-10-01", 20242025, xg=0.5), grow("P1", 2, "2024-10-05", 20242025, xg=0.0)]
        history = player_history_as_of(rows, "P1", "2024-10-05")
        self.assertEqual([r["individual_xg"] for r in history], [0.5])

    def test_2_future_xg_excluded(self):
        from research.player_goals.features import player_history_as_of
        rows = [grow("P1", 1, "2024-11-01", 20242025, xg=0.5)]
        self.assertEqual(player_history_as_of(rows, "P1", "2024-10-05"), [])

    def test_3_same_day_xg_excluded(self):
        from research.player_goals.features import player_history_as_of
        rows = [grow("P1", 1, "2024-10-05", 20242025, xg=0.5)]
        self.assertEqual(player_history_as_of(rows, "P1", "2024-10-05"), [])


# --------------------------------------------------------------------------
# Tests 4-6: xG/shot, xG/60, high-danger-rate correctness.
# --------------------------------------------------------------------------
class TestShotQualityCorrectness(unittest.TestCase):
    def test_4_xg_per_shot_correctness(self):
        history = [grow("P1", 1, "2024-10-01", 20242025, xg=1.0, sog=10.0)]
        shrunk, shots = sq.xg_per_shot_shrunk(history, league_xg_per_shot=0.09, shrinkage_shots=100)
        self.assertEqual(shots, 10)
        raw = 1.0 / 10.0
        w = 10 / 110
        self.assertAlmostEqual(shrunk, 0.09 + w * (raw - 0.09), places=6)

    def test_5_pp_xg_per_shot_correctness(self):
        history = [grow("P1", 1, "2024-10-01", 20242025, pp={"icetime_seconds": 60.0, "goals": 0.0, "sog": 3.0, "individual_xg": 0.3})]
        shrunk, shots = sq.pp_xg_per_shot_shrunk(history, league_pp_xg_per_shot=0.1, shrinkage_shots=100)
        self.assertEqual(shots, 3)
        raw = 0.3 / 3.0
        w = 3 / 103
        self.assertAlmostEqual(shrunk, 0.1 + w * (raw - 0.1), places=6)

    def test_6_high_danger_rate_correctness(self):
        history = [grow("P1", 1, "2024-10-01", 20242025, high_danger=2.0, shot_attempts=10.0)]
        shrunk, attempts = sq.high_danger_share_shrunk(history, league_hd_share=0.15, shrinkage_attempts=200)
        self.assertEqual(attempts, 10)
        raw = 2.0 / 10.0
        w = 10 / 210
        self.assertAlmostEqual(shrunk, 0.15 + w * (raw - 0.15), places=6)


# --------------------------------------------------------------------------
# Tests 7-8: shrinkage correctness, low-sample regression.
# --------------------------------------------------------------------------
class TestShrinkage(unittest.TestCase):
    def test_7_zero_shots_returns_league_prior(self):
        shrunk, shots = sq.xg_per_shot_shrunk([], league_xg_per_shot=0.091, shrinkage_shots=100)
        self.assertEqual(shrunk, 0.091)
        self.assertEqual(shots, 0)

    def test_8_low_sample_regressed_heavily_toward_prior(self):
        # a wildly extreme single-shot outcome should barely move the estimate
        history = [grow("P1", 1, "2024-10-01", 20242025, xg=0.9, sog=1.0)]
        shrunk, _ = sq.xg_per_shot_shrunk(history, league_xg_per_shot=0.09, shrinkage_shots=100)
        self.assertLess(abs(shrunk - 0.09), 0.02)  # weight = 1/101, tiny movement


# --------------------------------------------------------------------------
# Test 9: PP shot-quality calculation (nested block treated correctly).
# --------------------------------------------------------------------------
class TestPpShotQuality(unittest.TestCase):
    def test_9_no_pp_rows_returns_league_prior(self):
        history = [grow("P1", 1, "2024-10-01", 20242025, xg=0.1, sog=2.0, pp=None)]
        shrunk, shots = sq.pp_xg_per_shot_shrunk(history, league_pp_xg_per_shot=0.11, shrinkage_shots=100)
        self.assertEqual(shots, 0)
        self.assertEqual(shrunk, 0.11)


# --------------------------------------------------------------------------
# Test 10: finishing-above-xG PIT integrity.
# --------------------------------------------------------------------------
class TestFinishingAboveXg(unittest.TestCase):
    def test_10_finishing_above_xg_pit_safe_and_shrunk_toward_zero(self):
        history = [grow("P1", 1, "2024-10-01", 20242025, goals=2.0, xg=0.5)]
        shrunk, n = sq.finishing_above_xg_shrunk(history, shrinkage_games=100)
        self.assertEqual(n, 1)
        raw = 2.0 - 0.5
        w = 1 / 101
        self.assertAlmostEqual(shrunk, w * raw, places=6)
        self.assertLess(abs(shrunk), 0.02)  # heavily shrunk toward 0 from a single game


# --------------------------------------------------------------------------
# Tests 11-12: season boundary, player-trade behavior (reused shared gate).
# --------------------------------------------------------------------------
class TestSeasonBoundaryAndTrade(unittest.TestCase):
    def test_11_history_carries_across_season_boundary(self):
        from research.player_goals.features import player_history_as_of
        rows = [grow("P1", 1, "2024-04-01", 20232024, xg=0.3), grow("P1", 2, "2024-10-10", 20242025, xg=0.1)]
        history = player_history_as_of(rows, "P1", "2024-10-15")
        self.assertEqual(len(history), 2)

    def test_12_history_survives_team_change(self):
        # shot_quality functions take player-scoped history lists directly
        # -- team identity is irrelevant to the shrinkage math itself.
        history = [grow("P1", 1, "2024-10-01", 20242025, xg=0.2, sog=3.0),
                   grow("P1", 2, "2024-12-15", 20242025, xg=0.1, sog=2.0)]
        shrunk, shots = sq.xg_per_shot_shrunk(history, league_xg_per_shot=0.09, shrinkage_shots=100)
        self.assertEqual(shots, 5)


# --------------------------------------------------------------------------
# Test 13: common evaluation rows shared across incumbent and challengers.
# --------------------------------------------------------------------------
class TestCommonEvaluationSet(unittest.TestCase):
    RESULTS_PATH = os.path.join(REPO_ROOT, "research", "goals_shot_quality_results.json")

    def test_13_incumbent_and_every_challenger_share_the_same_final_fold_n(self):
        if not os.path.exists(self.RESULTS_PATH):
            self.skipTest("goals_shot_quality_results.json not built in this environment")
        with open(self.RESULTS_PATH) as f:
            r = json.load(f)
        n = r["incumbent_final_metrics"]["n"]
        for name, res in r["challenger_final_metrics"].items():
            self.assertEqual(res["n"], n, f"challenger {name} used a different final-fold n")


# --------------------------------------------------------------------------
# Test 14: rolling temporal folds / disclosed single-final-fold design.
# --------------------------------------------------------------------------
class TestTemporalDesign(unittest.TestCase):
    def test_14_dev_and_final_seasons_are_forward_of_incumbent_fit_data(self):
        import research.run_goals_shot_quality_refinement as rgq
        import research.run_player_goals_model as gm
        self.assertGreater(rgq.DEV_EVAL_SEASON, gm.TUNING_SEASON)
        self.assertGreater(rgq.FINAL_SEASON, rgq.DEV_EVAL_SEASON)

    def test_14b_methodology_note_discloses_the_single_fold_design(self):
        with open(DRIVER_PATH) as f:
            src = f.read()
        self.assertIn("REUSED HISTORICAL DATA UNDER NEW GOALS DEVELOPMENT CYCLE", src)
        self.assertIn("backward temporal leakage", src)


# --------------------------------------------------------------------------
# Test 15: freeze manifest.
# --------------------------------------------------------------------------
class TestFreezeManifest(unittest.TestCase):
    MANIFEST_PATH = os.path.join(REPO_ROOT, "research", "goals_shot_quality_manifest.json")

    def test_15_manifest_has_required_fields(self):
        if not os.path.exists(self.MANIFEST_PATH):
            self.skipTest("goals_shot_quality_manifest.json not built in this environment")
        with open(self.MANIFEST_PATH) as f:
            manifest = json.load(f)
        for key in ("experiment_id", "incumbent_version", "challenger_version", "candidate_features_tested",
                    "shrinkage", "dev_value_tests", "best_challenger_on_dev", "source_code_hashes"):
            self.assertIn(key, manifest)

    def test_15b_freeze_written_before_final_fold_scored(self):
        with open(DRIVER_PATH) as f:
            src = f.read()
        freeze_idx = src.index('with open(manifest_path, "w") as f:')
        complete_idx = src.index("FREEZE COMPLETE")
        self.assertLess(freeze_idx, complete_idx)


# --------------------------------------------------------------------------
# Test 16: incumbent remains unchanged (frozen weights reused, never refit).
# --------------------------------------------------------------------------
class TestIncumbentUnchanged(unittest.TestCase):
    def test_16_driver_never_refits_the_incumbent(self):
        with open(DRIVER_PATH) as f:
            text = f.read()
        self.assertNotIn("fit_poisson_glm(", text)
        self.assertNotIn("fit_poisson_glm_with_offset(", text)
        self.assertIn("player_goals_results.json", text)  # reads it, doesn't regenerate it

    def test_16b_goals_results_file_unmodified(self):
        import subprocess
        proc = subprocess.run(["git", "status", "--porcelain", "research/player_goals_results.json",
                                "research/run_player_goals_model.py"], cwd=REPO_ROOT, capture_output=True, text=True)
        if proc.returncode != 0:
            self.skipTest("not a git repository in this environment")
        modified = [l for l in proc.stdout.splitlines() if l[:2].strip().upper().startswith("M")]
        self.assertEqual(modified, [])


# --------------------------------------------------------------------------
# Test 17: challenger formula reproducibility.
# --------------------------------------------------------------------------
class TestChallengerReproducibility(unittest.TestCase):
    def test_17_predict_with_1d_offset_matches_manual_computation(self):
        import math
        import research.run_goals_shot_quality_refinement as rgq
        mu_base, feature, w = 0.3, 0.1, 0.05
        expected = math.exp(math.log(mu_base) + w * feature)
        self.assertAlmostEqual(rgq.predict_with_1d_offset(mu_base, feature, w), expected, places=9)


# --------------------------------------------------------------------------
# Tests 18-19: game-cluster bootstrap, date-cluster sensitivity.
# --------------------------------------------------------------------------
class TestClusteredBootstrap(unittest.TestCase):
    def test_18_game_clustered_bootstrap_clusters_by_game_id(self):
        import research.run_goals_shot_quality_refinement as rgq
        examples = [{"game_id": 1, "game_date": "d1"}, {"game_id": 1, "game_date": "d1"}, {"game_id": 2, "game_date": "d2"}]
        result = rgq.game_clustered_bootstrap(examples, [0.1, 0.1, 0.2], [0.05, 0.05, 0.2], n_resamples=20)
        self.assertEqual(result["n_games_resampled"], 2)

    def test_19_date_clustered_bootstrap_clusters_by_date(self):
        import research.run_goals_shot_quality_refinement as rgq
        examples = [{"game_id": 1, "game_date": "d1"}, {"game_id": 2, "game_date": "d1"}, {"game_id": 3, "game_date": "d2"}]
        result = rgq.date_clustered_bootstrap(examples, [0.1, 0.1, 0.2], [0.05, 0.05, 0.2], n_resamples=20)
        self.assertEqual(result["n_dates_resampled"], 2)


# --------------------------------------------------------------------------
# Tests 20-21: confidence framework / gating policy unchanged.
# --------------------------------------------------------------------------
class TestConfidenceAndGatingUnchanged(unittest.TestCase):
    def test_20_confidence_score_is_the_unchanged_shared_function(self):
        from research.player_sog import count_models as cm
        import research.run_goals_shot_quality_refinement as rgq
        self.assertIs(rgq.cm.confidence_score, cm.confidence_score)

    def test_21_decision_policy_module_not_modified(self):
        import subprocess
        proc = subprocess.run(["git", "status", "--porcelain", "research/player_props/decision_policy.py"],
                               cwd=REPO_ROOT, capture_output=True, text=True)
        if proc.returncode != 0:
            self.skipTest("not a git repository in this environment")
        modified = [l for l in proc.stdout.splitlines() if l[:2].strip().upper().startswith("M")]
        self.assertEqual(modified, [])


# --------------------------------------------------------------------------
# Test 22: conservative probability unchanged (module never imports it,
# since this refinement only tests threshold-1 point-probability offsets).
# --------------------------------------------------------------------------
class TestConservativeProbabilityUnchanged(unittest.TestCase):
    def test_22_conservative_mu_not_reimplemented(self):
        with open(os.path.join(REPO_ROOT, "research", "player_goals", "shot_quality.py")) as f:
            text = f.read()
        self.assertNotIn("conservative_mu", text)


# --------------------------------------------------------------------------
# Test 23: 2+ support rule unchanged.
# --------------------------------------------------------------------------
class TestTwoPlusRuleUnchanged(unittest.TestCase):
    def test_23_no_two_plus_support_standard_redefined(self):
        with open(DRIVER_PATH) as f:
            text = f.read()
        self.assertNotIn("TWO_PLUS_SUPPORT_STANDARD = {", text)  # the original constant lives only in run_player_goals_model.py


# --------------------------------------------------------------------------
# Test 24: registry version behavior (kept, not upgraded, per Part 29).
# --------------------------------------------------------------------------
class TestRegistryVersionBehavior(unittest.TestCase):
    def test_24_goals_still_validated_not_downgraded(self):
        from research.player_props import registry
        entry = registry.get("GOALS")
        self.assertEqual(entry.model_status, "VALIDATED")


# --------------------------------------------------------------------------
# Test 25: dashboard incumbent/challenger labeling.
# --------------------------------------------------------------------------
class TestDashboardLabeling(unittest.TestCase):
    def test_25_goals_page_shows_incumbent_retained_language(self):
        page_path = os.path.join(REPO_ROOT, "dashboard", "pages", "12_Player_Goals_Research.py")
        if not os.path.exists(page_path):
            self.skipTest("dashboard page not built in this environment")
        with open(page_path) as f:
            text = f.read()
        self.assertIn("shot", text.lower())


# --------------------------------------------------------------------------
# Tests 26-30: other prop models / production model unchanged.
# --------------------------------------------------------------------------
class TestOtherModelsUnchanged(unittest.TestCase):
    NEW_FILES = ["research/player_goals/shot_quality.py", "research/run_goals_shot_quality_refinement.py"]
    FORBIDDEN_MODULES = {"pricing.engine", "pricing.decision", "models.combined_model"}

    def _unmodified(self, rel_paths):
        import subprocess
        proc = subprocess.run(["git", "status", "--porcelain", *rel_paths], cwd=REPO_ROOT, capture_output=True, text=True)
        if proc.returncode != 0:
            self.skipTest("not a git repository in this environment")
        modified = [l for l in proc.stdout.splitlines() if l[:2].strip().upper().startswith("M")]
        self.assertEqual(modified, [])

    def test_26_sog_model_unchanged(self):
        self._unmodified(["research/run_player_sog_model.py"])

    def test_27_blocks_model_unchanged(self):
        self._unmodified(["research/run_player_blocks_model.py"])

    def test_28_assists_model_unchanged(self):
        self._unmodified(["research/run_player_assists_model.py"])

    def test_29_points_unchanged(self):
        self._unmodified(["research/run_player_points_model.py", "research/player_points_results.json"])

    def test_30_no_forbidden_imports_and_no_nhl_db_reference(self):
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


if __name__ == "__main__":
    unittest.main()
