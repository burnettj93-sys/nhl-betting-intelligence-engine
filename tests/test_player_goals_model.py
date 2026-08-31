"""
Tests for the Player GOALS / ANYTIME GOAL model: research/player_goals/
features.py, hierarchy.py, and research/run_player_goals_model.py. Covers
Part 43's 39 required test areas. Shared PIT/count-distribution/
confidence/conservative-probability math is already exhaustively covered
elsewhere (tests/test_player_sog_model.py) against the SAME unmodified
research/player_sog/count_models.py this module reuses directly.
"""
import ast
import json
import os
import unittest

from research.player_goals import features as gf
from research.player_goals import hierarchy as gh
from research.player_sog import count_models as cm

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRIVER_PATH = os.path.join(REPO_ROOT, "research", "run_player_goals_model.py")


def grow(player_id, game_id, game_date, season, team, opponent, goals, sog=0.0, position="C", pp=None, icetime=1000.0):
    return {"player_id": player_id, "player_name": player_id, "game_id": game_id, "season": season,
            "game_date": game_date, "team": team, "opponent": opponent, "home_or_away": "HOME",
            "position": position, "icetime_seconds": icetime, "toi_5v5_seconds": icetime * 0.7,
            "goals": goals, "sog": sog, "shot_attempts": sog + 2.0, "unblocked_attempts": sog + 1.0,
            "individual_xg": 0.1, "high_danger_shots": 0.0, "medium_danger_shots": 0.0, "low_danger_shots": 0.0,
            "high_danger_xg": 0.0, "rebounds": 0.0, "rebound_goals": 0.0, "points": goals, "assists": 0.0,
            "pp": pp, "provenance_type": "ARCHIVAL_RESEARCH"}


# --------------------------------------------------------------------------
# Tests 1-6: no target-game/future/same-day leakage.
# --------------------------------------------------------------------------
class TestGoalsHistoryPIT(unittest.TestCase):
    def test_1_target_game_goals_excluded(self):
        rows = [grow("P1", 1, "2024-10-01", 20242025, "TOR", "MTL", goals=2.0),
                grow("P1", 2, "2024-10-05", 20242025, "TOR", "BOS", goals=0.0)]
        history = gf.player_history_as_of(rows, "P1", "2024-10-05")
        self.assertEqual([r["goals"] for r in history], [2.0])

    def test_2_target_game_sog_excluded(self):
        rows = [grow("P1", 1, "2024-10-01", 20242025, "TOR", "MTL", goals=0.0, sog=5.0),
                grow("P1", 2, "2024-10-05", 20242025, "TOR", "BOS", goals=0.0, sog=8.0)]
        history = gf.player_history_as_of(rows, "P1", "2024-10-05")
        self.assertEqual([r["sog"] for r in history], [5.0])

    def test_3_target_game_xg_excluded(self):
        rows = [grow("P1", 1, "2024-10-01", 20242025, "TOR", "MTL", goals=0.0)]
        history = gf.player_history_as_of(rows, "P1", "2024-10-01")
        self.assertEqual(history, [])  # same-day covers xG/TOI/everything on that row

    def test_4_target_game_toi_excluded(self):
        rows = [grow("P1", 1, "2024-10-01", 20242025, "TOR", "MTL", goals=0.0, icetime=900.0),
                grow("P1", 2, "2024-10-05", 20242025, "TOR", "BOS", goals=0.0, icetime=1500.0)]
        history = gf.player_history_as_of(rows, "P1", "2024-10-05")
        self.assertEqual([r["icetime_seconds"] for r in history], [900.0])

    def test_5_same_day_exclusion(self):
        rows = [grow("P1", 1, "2024-10-05", 20242025, "TOR", "MTL", goals=1.0)]
        self.assertEqual(gf.player_history_as_of(rows, "P1", "2024-10-05"), [])

    def test_6_future_exclusion(self):
        rows = [grow("P1", 1, "2024-11-01", 20242025, "TOR", "MTL", goals=1.0)]
        self.assertEqual(gf.player_history_as_of(rows, "P1", "2024-10-05"), [])


# --------------------------------------------------------------------------
# Tests 7-8: projected-active, actually-played not an input.
# --------------------------------------------------------------------------
class TestEligibility(unittest.TestCase):
    def test_7_projected_active_is_the_same_reused_function(self):
        from research.player_sog.features import projected_active as sog_fn
        self.assertIs(gf.projected_active, sog_fn)

    def test_8_actually_played_not_used_as_feature(self):
        import inspect
        import research.run_player_goals_model as rgm
        src = inspect.getsource(rgm.build_example)
        start = src.index("fv = build_feature_vector(")
        end = src.index(")", src.index("h2h_goals_delta)"))
        fv_call_text = src[start:end]
        self.assertNotIn('row["', fv_call_text)


# --------------------------------------------------------------------------
# Test 9: goal label correctness (direct MoneyPuck field, spot-checked).
# --------------------------------------------------------------------------
class TestGoalLabel(unittest.TestCase):
    CORPUS_PATH = os.path.join(REPO_ROOT, "research", "player_goals", "player_game_goals.jsonl")

    def test_9_goals_field_is_non_negative_real_values(self):
        if not os.path.exists(self.CORPUS_PATH):
            self.skipTest("player_game_goals.jsonl not built in this environment")
        with open(self.CORPUS_PATH) as f:
            for i, line in enumerate(f):
                if i >= 2000:
                    break
                r = json.loads(line)
                self.assertGreaterEqual(r["goals"], 0.0)
                self.assertGreaterEqual(r["sog"], r["goals"])  # can't score more goals than shots on goal


# --------------------------------------------------------------------------
# Test 10: empirical goal baseline.
# --------------------------------------------------------------------------
class TestEmpiricalBaseline(unittest.TestCase):
    def test_10_empirical_threshold_probs_shrinks_toward_league(self):
        import research.run_player_goals_model as rgm
        history = [grow("P1", i, f"2024-10-0{i}", 20242025, "TOR", "MTL", goals=1.0) for i in range(1, 3)]
        out = rgm.empirical_threshold_probs(history, {1: 0.15, 2: 0.02})
        self.assertGreater(out[1], 0.15)
        self.assertLess(out[1], 1.0)


# --------------------------------------------------------------------------
# Tests 11-12: shooting-talent shrinkage, low-sample shrinkage.
# --------------------------------------------------------------------------
class TestShootingTalentShrinkage(unittest.TestCase):
    def test_11_small_sample_shrunk_heavily_toward_league(self):
        history = [grow("P1", 1, "2024-10-01", 20242025, "TOR", "MTL", goals=3.0, sog=10.0)]  # 30% raw
        shrunk, shots = gf.career_shooting_pct_shrunk(history, league_shooting_pct=0.09, shrinkage_shots=150)
        self.assertEqual(shots, 10)
        self.assertLess(shrunk, 0.30)
        self.assertGreater(shrunk, 0.09)
        self.assertAlmostEqual(shrunk, 0.09 + (10 / 160) * (0.30 - 0.09), places=6)

    def test_12_large_sample_barely_shrunk(self):
        history = [grow(f"P{i}", i, "2024-10-01", 20242025, "TOR", "MTL", goals=0.15, sog=1.0) for i in range(300)]
        shrunk, shots = gf.career_shooting_pct_shrunk(history, league_shooting_pct=0.09, shrinkage_shots=150)
        self.assertEqual(shots, 300)
        self.assertAlmostEqual(shrunk, 0.09 + (300 / 450) * (0.15 - 0.09), places=6)

    def test_12b_zero_shots_returns_league_rate(self):
        shrunk, shots = gf.career_shooting_pct_shrunk([], league_shooting_pct=0.091, shrinkage_shots=150)
        self.assertEqual(shrunk, 0.091)
        self.assertEqual(shots, 0)


# --------------------------------------------------------------------------
# Tests 13-14: season-boundary behavior, player-trade behavior.
# --------------------------------------------------------------------------
class TestSeasonBoundaryAndTrade(unittest.TestCase):
    def test_13_history_carries_across_season_boundary(self):
        rows = [grow("P1", 1, "2024-04-01", 20232024, "TOR", "MTL", goals=1.0),
                grow("P1", 2, "2024-10-10", 20242025, "TOR", "BOS", goals=0.0)]
        history = gf.player_history_as_of(rows, "P1", "2024-10-15")
        self.assertEqual(len(history), 2)

    def test_14_history_survives_team_change(self):
        rows = [grow("P1", 1, "2024-10-01", 20242025, "TOR", "MTL", goals=1.0),
                grow("P1", 2, "2024-12-15", 20242025, "NYR", "BOS", goals=0.0)]
        history = gf.player_history_as_of(rows, "P1", "2024-12-20")
        self.assertEqual(len(history), 2)


# --------------------------------------------------------------------------
# Test 15: upstream SOG OOF integrity or exclusion.
# --------------------------------------------------------------------------
class TestUpstreamSogEligibility(unittest.TestCase):
    def test_15_upstream_sog_model_not_imported(self):
        for rel in ("research/player_goals/features.py", "research/run_player_goals_model.py"):
            with open(os.path.join(REPO_ROOT, rel)) as f:
                text = f.read()
            self.assertNotIn("player_sog.live_projection", text)
            self.assertNotIn("player_sog_results", text)

    def test_15b_manifest_declares_sog_ineligible(self):
        path = os.path.join(REPO_ROOT, "research", "player_goals_freeze_manifest.json")
        if not os.path.exists(path):
            self.skipTest("player_goals_freeze_manifest.json not built in this environment")
        with open(path) as f:
            manifest = json.load(f)
        self.assertIn("NOT ELIGIBLE", manifest["upstream_sog_eligibility"])


# --------------------------------------------------------------------------
# Test 16: shot-quality feature -- honestly not part of the locked
# feature set this cycle (disclosed scope decision, not silently skipped).
# --------------------------------------------------------------------------
class TestShotQualityScopeDisclosure(unittest.TestCase):
    def test_16_xg_and_danger_fields_are_captured_in_corpus_even_though_unused_as_features(self):
        path = os.path.join(REPO_ROOT, "research", "player_goals", "player_game_goals.jsonl")
        if not os.path.exists(path):
            self.skipTest("player_game_goals.jsonl not built in this environment")
        with open(path) as f:
            row = json.loads(f.readline())
        for key in ("individual_xg", "high_danger_shots", "medium_danger_shots", "low_danger_shots", "high_danger_xg"):
            self.assertIn(key, row)
        # not part of the locked GLM feature vector this cycle -- honestly scoped, not silently dropped
        self.assertNotIn("high_danger", " ".join(__import__("research.run_player_goals_model", fromlist=["FEATURE_NAMES"]).FEATURE_NAMES))


# --------------------------------------------------------------------------
# Test 17: PP role feature construction.
# --------------------------------------------------------------------------
class TestPpRole(unittest.TestCase):
    def test_17_pp_mean_treats_none_as_zero(self):
        history = [
            grow("P1", 1, "2024-10-01", 20242025, "TOR", "X", goals=1.0, pp={"icetime_seconds": 90.0, "goals": 1.0, "sog": 2.0, "individual_xg": 0.2}),
            grow("P1", 2, "2024-10-03", 20242025, "TOR", "X", goals=0.0, pp=None),
        ]
        self.assertAlmostEqual(gf.rolling_pp_mean(history, "goals", None), 0.5)


# --------------------------------------------------------------------------
# Test 18: H2H shrinkage (both goals AND SOG, independently).
# --------------------------------------------------------------------------
class TestH2HShrinkage(unittest.TestCase):
    def test_18_h2h_goals_small_sample_shrunk(self):
        history = [grow("P1", 1, "2024-10-01", 20242025, "TOR", "MTL", goals=3.0)]
        rate, n = gf.h2h_shrunk_goals_rate(history, "MTL", baseline_rate=0.2)
        self.assertEqual(n, 1)
        self.assertLess(rate, 3.0)
        self.assertGreater(rate, 0.2)

    def test_18b_h2h_sog_small_sample_shrunk(self):
        history = [grow("P1", 1, "2024-10-01", 20242025, "TOR", "MTL", goals=0.0, sog=8.0)]
        rate, n = gf.h2h_shrunk_sog_rate(history, "MTL", baseline_sog_rate=2.0)
        self.assertEqual(n, 1)
        self.assertLess(rate, 8.0)
        self.assertGreater(rate, 2.0)

    def test_18c_zero_h2h_games_returns_baseline(self):
        history = [grow("P1", 1, "2024-10-01", 20242025, "TOR", "BOS", goals=1.0)]
        rate, n = gf.h2h_shrunk_goals_rate(history, "MTL", baseline_rate=0.15)
        self.assertEqual(n, 0)
        self.assertEqual(rate, 0.15)


# --------------------------------------------------------------------------
# Tests 19-20: coherent count distribution, P(1+) >= P(2+).
# --------------------------------------------------------------------------
class TestDistributionCoherence(unittest.TestCase):
    def test_19_uses_shared_count_models_functions(self):
        import research.run_player_goals_model as rgm
        self.assertIs(rgm.cm.negbinom_sf_at_least, cm.negbinom_sf_at_least)
        self.assertIs(rgm.cm.poisson_sf_at_least, cm.poisson_sf_at_least)
        self.assertIs(rgm.cm.fit_poisson_glm, cm.fit_poisson_glm)

    def test_20_p1_geq_p2(self):
        probs = cm.threshold_probabilities(0.3, 0.15, thresholds=(1, 2))
        self.assertGreaterEqual(probs[1] + 1e-9, probs[2])


# --------------------------------------------------------------------------
# Tests 21-24: calibration pre-eval, freeze manifest, frozen config used,
# common evaluation set.
# --------------------------------------------------------------------------
class TestTuningLockDiscipline(unittest.TestCase):
    def setUp(self):
        with open(DRIVER_PATH) as f:
            self.src = f.read()

    def test_21_grid_search_and_stage_fitting_restricted_to_tuning_rows(self):
        self.assertIn('bucket = "tuning_fit" if row["game_date"] < tuning_split_date else "tuning_validate"', self.src)
        self.assertIn("fit_pool = tuning_fit", self.src)

    def test_22_freeze_manifest_written_before_eval_seasons_scored(self):
        freeze_write_idx = self.src.index('with open(manifest_path, "w") as f:')
        complete_idx = self.src.index("FREEZE COMPLETE")
        self.assertLess(freeze_write_idx, complete_idx)

    def test_23_manifest_exists_and_has_required_fields(self):
        path = os.path.join(REPO_ROOT, "research", "player_goals_freeze_manifest.json")
        if not os.path.exists(path):
            self.skipTest("player_goals_freeze_manifest.json not built in this environment")
        with open(path) as f:
            manifest = json.load(f)
        for key in ("experiment_id", "freeze_timestamp_utc", "target_definition", "feature_set",
                    "shrinkage_parameters", "shooting_talent_methodology", "two_plus_support_standard",
                    "source_code_hashes", "locked_stage"):
            self.assertIn(key, manifest)

    def test_24_common_evaluation_set_shared_across_candidates(self):
        path = os.path.join(REPO_ROOT, "research", "player_goals_results.json")
        if not os.path.exists(path):
            self.skipTest("player_goals_results.json not built in this environment")
        with open(path) as f:
            results = json.load(f)
        n = results["eval_examples_n"]
        for name, res in results["candidate_results"].items():
            self.assertEqual(res["n"], n, f"candidate {name} used a different eval-set size")
        for name, res in results["baseline_results"].items():
            self.assertEqual(res["n"], n, f"baseline {name} used a different eval-set size")


# --------------------------------------------------------------------------
# Test 25: game-cluster bootstrap.
# --------------------------------------------------------------------------
class TestGameClusteredBootstrap(unittest.TestCase):
    def test_25_resamples_by_game_id_not_by_row(self):
        import research.run_player_goals_model as rgm
        examples = [{"game_id": 1}, {"game_id": 1}, {"game_id": 2}, {"game_id": 3}]
        result = rgm.game_clustered_bootstrap(examples, [0.1, 0.1, 0.2, 0.3], [0.05, 0.05, 0.2, 0.3], n_resamples=50)
        self.assertEqual(result["n_games_resampled"], 3)


# --------------------------------------------------------------------------
# Tests 26-27: confidence, conservative probability (unchanged, reused).
# --------------------------------------------------------------------------
class TestConfidenceAndConservative(unittest.TestCase):
    def test_26_confidence_score_is_the_unchanged_shared_function(self):
        import research.run_player_goals_model as rgm
        self.assertIs(rgm.cm.confidence_score, cm.confidence_score)

    def test_27_conservative_mu_is_the_unchanged_shared_function(self):
        import research.run_player_goals_model as rgm
        self.assertIs(rgm.cm.conservative_mu, cm.conservative_mu)


# --------------------------------------------------------------------------
# Tests 28-29: anytime-goal mapping, no first-goal equivalence claimed.
# --------------------------------------------------------------------------
class TestAnytimeGoalMapping(unittest.TestCase):
    def test_28_anytime_goal_registry_entry_is_market_key_mapped(self):
        from research.player_props import registry
        entry = registry.get("ANYTIME_GOAL")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.odds_api_market_key, "player_goal_scorer_anytime")

    def test_29_first_goal_not_marked_supported(self):
        from research.player_props import registry
        entry = registry.get("FIRST_GOAL")
        self.assertIsNotNone(entry)
        self.assertNotEqual(entry.model_status, "SUPPORTED_BY_GOALS_MODEL")
        self.assertFalse(os.path.exists(os.path.join(REPO_ROOT, "research", "run_player_first_goal_model.py")))


# --------------------------------------------------------------------------
# Test 30: registry status.
# --------------------------------------------------------------------------
class TestRegistryStatus(unittest.TestCase):
    def test_30_goals_registry_status_is_real(self):
        from research.player_props import registry
        entry = registry.get("GOALS")
        self.assertIn(entry.model_status, ("VALIDATED", "PARTIAL", "RESEARCH", "REJECTED"))


# --------------------------------------------------------------------------
# Test 31: no fake odds.
# --------------------------------------------------------------------------
class TestNoFakeOdds(unittest.TestCase):
    def test_31_no_odds_api_calls_in_new_files(self):
        for rel in ("research/player_goals/features.py", "research/player_goals/hierarchy.py",
                    "research/run_player_goals_model.py"):
            with open(os.path.join(REPO_ROOT, rel)) as f:
                text = f.read()
            self.assertNotIn("requests.", text)
            self.assertNotIn("the_odds_api", text)
            self.assertNotIn("DraftKings", text)


# --------------------------------------------------------------------------
# Test 32: dashboard labeling.
# --------------------------------------------------------------------------
class TestDashboardLabeling(unittest.TestCase):
    def test_32_goals_page_reads_status_from_registry(self):
        page_path = os.path.join(REPO_ROOT, "dashboard", "pages", "12_Player_Goals_Research.py")
        if not os.path.exists(page_path):
            self.skipTest("dashboard page not built in this environment")
        with open(page_path) as f:
            text = f.read()
        self.assertIn("registry", text)


# --------------------------------------------------------------------------
# Test 33: decision policy unchanged.
# --------------------------------------------------------------------------
class TestDecisionPolicyUnchanged(unittest.TestCase):
    def test_33_decision_policy_module_not_modified_by_this_slice(self):
        import subprocess
        proc = subprocess.run(["git", "status", "--porcelain", "research/player_props/decision_policy.py"],
                               cwd=REPO_ROOT, capture_output=True, text=True)
        if proc.returncode != 0:
            self.skipTest("not a git repository in this environment")
        modified = [l for l in proc.stdout.splitlines() if l[:2].strip().upper().startswith("M")]
        self.assertEqual(modified, [])


# --------------------------------------------------------------------------
# Tests 34-39: other prop models / confidence framework / production
# model all unchanged.
# --------------------------------------------------------------------------
class TestOtherModelsUnchanged(unittest.TestCase):
    NEW_FILES = ["research/player_goals/features.py", "research/player_goals/hierarchy.py",
                 "research/player_goals/build_goals_corpus.py", "research/run_player_goals_model.py"]
    FORBIDDEN_MODULES = {"pricing.engine", "pricing.decision", "models.combined_model"}

    def test_34_sog_model_unchanged(self):
        import subprocess
        proc = subprocess.run(["git", "status", "--porcelain", "research/run_player_sog_model.py"],
                               cwd=REPO_ROOT, capture_output=True, text=True)
        if proc.returncode != 0:
            self.skipTest("not a git repository in this environment")
        modified = [l for l in proc.stdout.splitlines() if l[:2].strip().upper().startswith("M")]
        self.assertEqual(modified, [])

    def test_35_blocks_model_unchanged(self):
        import subprocess
        proc = subprocess.run(["git", "status", "--porcelain", "research/run_player_blocks_model.py"],
                               cwd=REPO_ROOT, capture_output=True, text=True)
        if proc.returncode != 0:
            self.skipTest("not a git repository in this environment")
        modified = [l for l in proc.stdout.splitlines() if l[:2].strip().upper().startswith("M")]
        self.assertEqual(modified, [])

    def test_36_assists_model_unchanged(self):
        import subprocess
        proc = subprocess.run(["git", "status", "--porcelain", "research/run_player_assists_model.py"],
                               cwd=REPO_ROOT, capture_output=True, text=True)
        if proc.returncode != 0:
            self.skipTest("not a git repository in this environment")
        modified = [l for l in proc.stdout.splitlines() if l[:2].strip().upper().startswith("M")]
        self.assertEqual(modified, [])

    def test_37_points_baseline_unchanged(self):
        import subprocess
        proc = subprocess.run(["git", "status", "--porcelain", "research/run_player_points_model.py",
                                "research/player_points_results.json"],
                               cwd=REPO_ROOT, capture_output=True, text=True)
        if proc.returncode != 0:
            self.skipTest("not a git repository in this environment")
        modified = [l for l in proc.stdout.splitlines() if l[:2].strip().upper().startswith("M")]
        self.assertEqual(modified, [])

    def test_38_confidence_framework_unchanged(self):
        import subprocess
        proc = subprocess.run(["git", "status", "--porcelain", "research/player_sog/count_models.py",
                                "research/confidence_lab/reliability.py"],
                               cwd=REPO_ROOT, capture_output=True, text=True)
        if proc.returncode != 0:
            self.skipTest("not a git repository in this environment")
        modified = [l for l in proc.stdout.splitlines() if l[:2].strip().upper().startswith("M")]
        self.assertEqual(modified, [])

    def test_39_no_forbidden_imports_and_no_nhl_db_reference(self):
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
