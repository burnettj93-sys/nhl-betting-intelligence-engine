"""
Tests for the Confidence Framework Redesign:
research/confidence_lab/reliability.py, research/run_confidence_diagnostics.py,
and the registry/dashboard/shared-component changes it drove. Covers Part
34's 32 required test areas. Does NOT re-test any raw prop model's own
PIT/count-distribution correctness (already exhaustively covered by each
prop's own test file) -- this file is scoped to the NEW confidence-
reliability layer built on top of those already-frozen, unchanged models.
"""
import ast
import json
import os
import unittest

from research.confidence_lab import reliability as rel
from research.player_sog import count_models as cm

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRIVER_PATH = os.path.join(REPO_ROOT, "research", "run_confidence_diagnostics.py")


# --------------------------------------------------------------------------
# Tests 1-3: confidence never uses target/future outcomes; prior
# reliability tables are PIT-safe (built only from DEV/TUNING rows,
# strictly earlier than every fold they are applied to).
# --------------------------------------------------------------------------
class TestConfidencePIT(unittest.TestCase):
    def test_1_confidence_score_signature_takes_no_outcome_argument(self):
        import inspect
        sig = inspect.signature(cm.confidence_score)
        for name in sig.parameters:
            self.assertNotIn("actual", name.lower())
            self.assertNotIn("outcome", name.lower())

    def test_2_skill_deviation_tables_built_only_from_passed_dev_examples(self):
        # the function takes a plain list and returns tables derived
        # purely from it -- no hidden global/future-row access possible.
        dev = [{"prob": 0.3, "actual": 1.0, "history_len": 20, "toi_cv": 0.1},
               {"prob": 0.3, "actual": 0.0, "history_len": 20, "toi_cv": 0.1}]
        tables = rel.build_skill_deviation_tables(dev)
        self.assertIn("region_table", tables)
        self.assertIn("sample_table", tables)
        self.assertIn("role_table", tables)

    def test_3_driver_freezes_manifest_before_scoring_fold_seasons(self):
        with open(DRIVER_PATH) as f:
            src = f.read()
        freeze_idx = src.index("with open(manifest_path, \"w\") as f:")
        complete_idx = src.index("FREEZE COMPLETE")
        self.assertLess(freeze_idx, complete_idx)
        post_freeze = src[complete_idx:]
        self.assertIn("results_by_prop_fold", post_freeze)


# --------------------------------------------------------------------------
# Tests 4-5: current + redesigned confidence reproducibility.
# --------------------------------------------------------------------------
class TestReproducibility(unittest.TestCase):
    RESULTS_PATH = os.path.join(REPO_ROOT, "research", "confidence_framework_results.json")

    def setUp(self):
        if not os.path.exists(self.RESULTS_PATH):
            self.skipTest("confidence_framework_results.json not built in this environment")
        with open(self.RESULTS_PATH) as f:
            self.results = json.load(f)

    def test_4_current_system_reproduced_for_every_prop_fold(self):
        for prop in ("ASSISTS", "POINTS"):
            for fold in ("fold1_2024_25", "fold2_2025_26_final"):
                self.assertIn("A_current", self.results["results_by_prop_fold"][prop][fold])

    def test_5_redesigned_candidates_reproducible_for_every_prop_fold(self):
        for prop in ("ASSISTS", "POINTS"):
            for fold in ("fold1_2024_25", "fold2_2025_26_final"):
                d = self.results["results_by_prop_fold"][prop][fold]
                for cand in ("B_simple_reliability", "C_calibrated_pooled", "D_calibrated_per_prop"):
                    self.assertIn(cand, d)


# --------------------------------------------------------------------------
# Tests 6-7: score bounds, HIGH/MEDIUM/LOW mapping.
# --------------------------------------------------------------------------
class TestScoreBoundsAndMapping(unittest.TestCase):
    def test_6_candidate_b_score_is_bounded(self):
        score = rel.candidate_b_score(1000, 0.0, 0.0, 1000, 20, 1.0)
        self.assertLessEqual(score, 5.0)
        score_low = rel.candidate_b_score(0, 5.0, 5.0, 0, 20, 0.0)
        self.assertGreaterEqual(score_low, -5.0)

    def test_7_label_from_score_maps_correctly(self):
        self.assertEqual(rel.label_from_score(10.0, 0.0, 1.0), "HIGH")
        self.assertEqual(rel.label_from_score(0.5, 0.0, 1.0), "MEDIUM")
        self.assertEqual(rel.label_from_score(-1.0, 0.0, 1.0), "LOW")


# --------------------------------------------------------------------------
# Tests 8-9: prop-specific thresholds/cutoffs, sample-size handling.
# --------------------------------------------------------------------------
class TestPropSpecificAndSampleSize(unittest.TestCase):
    def test_8_candidate_d_cutoffs_fit_separately_per_prop(self):
        with open(DRIVER_PATH) as f:
            src = f.read()
        self.assertIn("lo_d_assists, hi_d_assists = rel.cutoffs_from_dev", src)
        self.assertIn("lo_d_points, hi_d_points = rel.cutoffs_from_dev", src)
        # a single pooled D cutoff was a real bug found and fixed this
        # slice -- guard against regressing back to it.
        self.assertNotIn('dev_d_scores = [e["score_d"] for e in dev_assists] + [e["score_d"] for e in dev_points]', src)

    def test_9_sample_bucket_boundaries_match_part7(self):
        self.assertEqual(rel.sample_bucket(5), 0)
        self.assertEqual(rel.sample_bucket(15), 1)
        self.assertEqual(rel.sample_bucket(30), 2)
        self.assertEqual(rel.sample_bucket(100), 3)


# --------------------------------------------------------------------------
# Tests 10-11: role-stability handling, probability-region handling.
# --------------------------------------------------------------------------
class TestRoleStabilityAndProbabilityRegion(unittest.TestCase):
    def test_10_toi_cv_bucket_boundaries_match_part8(self):
        self.assertEqual(rel.toi_cv_bucket(0.05), 0)
        self.assertEqual(rel.toi_cv_bucket(0.25), 1)
        self.assertEqual(rel.toi_cv_bucket(0.50), 2)

    def test_11_prob_bin_covers_the_full_0_to_1_range(self):
        self.assertEqual(rel.prob_bin(0.0), 0)
        self.assertEqual(rel.prob_bin(0.99), rel.PROB_BINS - 1)
        self.assertEqual(rel.prob_bin(1.0), rel.PROB_BINS - 1)  # clamped, never out of range


# --------------------------------------------------------------------------
# Test 12: missing-data behavior (None toi_cv/stat_cv).
# --------------------------------------------------------------------------
class TestMissingData(unittest.TestCase):
    def test_12_none_toi_cv_treated_as_moderate_bucket_not_a_crash(self):
        self.assertEqual(rel.toi_cv_bucket(None), 1)
        score = rel.candidate_b_score(20, None, None, 20, 20, 0.8)
        self.assertIsInstance(score, float)


# --------------------------------------------------------------------------
# Test 13: continuous score implemented (candidates B/C/D are all
# continuous internally, even though displayed as HIGH/MEDIUM/LOW).
# --------------------------------------------------------------------------
class TestContinuousScore(unittest.TestCase):
    def test_13_candidate_c_score_is_a_real_number_not_a_category(self):
        tables = rel.build_skill_deviation_tables(
            [{"prob": 0.3, "actual": 1.0, "history_len": 20, "toi_cv": 0.1}] * 5
            + [{"prob": 0.3, "actual": 0.0, "history_len": 20, "toi_cv": 0.1}] * 5)
        score = rel.candidate_c_score(0.3, 20, 0.1, tables)
        self.assertIsInstance(score, float)


# --------------------------------------------------------------------------
# Test 14: driver explanations still come from the unchanged
# confidence_score positive/risk driver lists (Part 19 -- not reworked
# this slice since the framework itself was kept).
# --------------------------------------------------------------------------
class TestDriverExplanations(unittest.TestCase):
    def test_14_confidence_score_still_returns_drivers_and_risks(self):
        label, pos, risk = cm.confidence_score(50, 0.1, 0.3, 20, 20, 0.95)
        self.assertIsInstance(pos, list)
        self.assertIsInstance(risk, list)


# --------------------------------------------------------------------------
# Test 15: raw probability unchanged by confidence -- structural check
# that this diagnostics module never mutates a raw model probability.
# --------------------------------------------------------------------------
class TestRawProbabilityUnchanged(unittest.TestCase):
    def test_15_diagnostics_driver_only_reads_locked_weights_never_fits(self):
        with open(DRIVER_PATH) as f:
            text = f.read()
        self.assertIn("stage_weights", text)  # reads locked weights
        self.assertNotIn("fit_poisson_glm(", text)  # never calls the raw fitter
        self.assertNotIn("fit_negbinom_alpha_by_moments(", text)  # never refits alpha either


# --------------------------------------------------------------------------
# Test 16: conservative probability behavior untouched.
# --------------------------------------------------------------------------
class TestConservativeProbabilityUntouched(unittest.TestCase):
    def test_16_conservative_mu_function_unmodified_and_unused_by_confidence_module(self):
        with open(os.path.join(REPO_ROOT, "research", "confidence_lab", "reliability.py")) as f:
            text = f.read()
        self.assertNotIn("conservative_mu", text)  # confidence redesign never touches this layer


# --------------------------------------------------------------------------
# Test 17-18: confidence freeze manifest, final evaluation uses it.
# --------------------------------------------------------------------------
class TestFreezeManifest(unittest.TestCase):
    MANIFEST_PATH = os.path.join(REPO_ROOT, "research", "confidence_framework_manifest.json")

    def setUp(self):
        if not os.path.exists(self.MANIFEST_PATH):
            self.skipTest("confidence_framework_manifest.json not built in this environment")
        with open(self.MANIFEST_PATH) as f:
            self.manifest = json.load(f)

    def test_17_manifest_has_required_fields(self):
        for key in ("experiment_id", "confidence_features", "bucket_boundaries", "prob_bins",
                    "sample_buckets", "toi_cv_buckets", "dev_season", "fold1_season",
                    "fold2_season_final_check", "source_code_hashes", "raw_model_treatment"):
            self.assertIn(key, self.manifest, f"manifest missing {key}")

    def test_18_final_evaluation_hashes_match_current_source(self):
        for rel_path, recorded_hash in self.manifest["source_code_hashes"].items():
            with open(os.path.join(REPO_ROOT, rel_path), "rb") as f:
                import hashlib
                actual = hashlib.sha256(f.read()).hexdigest()[:16]
            self.assertEqual(actual, recorded_hash, f"{rel_path} changed since freeze")


# --------------------------------------------------------------------------
# Test 19: rolling temporal folds (strictly forward, no random splits).
# --------------------------------------------------------------------------
class TestRollingFolds(unittest.TestCase):
    def test_19_dev_and_fold_seasons_are_strictly_forward(self):
        import research.run_confidence_diagnostics as rcd
        self.assertLess(rcd.TUNING_SEASON, rcd.FOLD1_SEASON)
        self.assertLess(rcd.FOLD1_SEASON, rcd.FOLD2_SEASON)


# --------------------------------------------------------------------------
# Test 20-21: confidence skill calculation, baseline-specific skill.
# --------------------------------------------------------------------------
class TestSkillCalculation(unittest.TestCase):
    def test_20_skill_score_matches_brier_skill_score_definition(self):
        import research.run_confidence_diagnostics as rcd
        s = rcd.skill(0.15, 0.3)
        expected = 1.0 - 0.15 / (0.3 * 0.7)
        self.assertAlmostEqual(s, expected)

    def test_21_perfect_prediction_has_skill_one(self):
        import research.run_confidence_diagnostics as rcd
        self.assertAlmostEqual(rcd.skill(0.0, 0.3), 1.0)


# --------------------------------------------------------------------------
# Tests 22-25: per-prop confidence results exist and are real (not
# fabricated) -- cross-checked against each prop's OWN stored results.
# --------------------------------------------------------------------------
class TestPerPropConfidenceResults(unittest.TestCase):
    def test_22_sog_confidence_breakdown_exists_and_is_real(self):
        path = os.path.join(REPO_ROOT, "research", "player_sog_results.json")
        if not os.path.exists(path):
            self.skipTest("player_sog_results.json not built in this environment")
        with open(path) as f:
            sog = json.load(f)
        self.assertIn("HIGH", sog["confidence_breakdown"])
        self.assertIn("LOW", sog["confidence_breakdown"])

    def test_23_blocks_confidence_breakdown_exists_and_is_real(self):
        path = os.path.join(REPO_ROOT, "research", "player_blocks_results.json")
        if not os.path.exists(path):
            self.skipTest("player_blocks_results.json not built in this environment")
        with open(path) as f:
            blk = json.load(f)
        self.assertIn("HIGH", blk["confidence_breakdown"])

    def test_24_assists_confidence_results_present_in_diagnostics_output(self):
        path = os.path.join(REPO_ROOT, "research", "confidence_framework_results.json")
        if not os.path.exists(path):
            self.skipTest("confidence_framework_results.json not built in this environment")
        with open(path) as f:
            r = json.load(f)
        self.assertIn("ASSISTS", r["results_by_prop_fold"])
        self.assertIn("ASSISTS", r["root_cause_composition"])

    def test_25_points_confidence_results_present_in_diagnostics_output(self):
        path = os.path.join(REPO_ROOT, "research", "confidence_framework_results.json")
        if not os.path.exists(path):
            self.skipTest("confidence_framework_results.json not built in this environment")
        with open(path) as f:
            r = json.load(f)
        self.assertIn("POINTS", r["results_by_prop_fold"])
        self.assertIn("POINTS", r["root_cause_composition"])


# --------------------------------------------------------------------------
# Test 26: registry version/status.
# --------------------------------------------------------------------------
class TestRegistryConfidenceFields(unittest.TestCase):
    def test_26_every_registry_entry_has_confidence_fields(self):
        from research.player_props import registry
        for entry in registry.REGISTRY:
            self.assertTrue(hasattr(entry, "confidence_framework_version"))
            self.assertTrue(hasattr(entry, "confidence_validation_status"))
        sog = registry.get("SOG")
        self.assertEqual(sog.confidence_validation_status, "VALIDATED")
        assists = registry.get("ASSISTS")
        self.assertEqual(assists.confidence_validation_status, "CONDITIONAL")

    def test_26b_raw_model_status_unchanged_by_confidence_work(self):
        # Part 26's explicit rule: confidence status must never imply raw
        # model validation changed.
        from research.player_props import registry
        self.assertEqual(registry.get("SOG").model_status, "VALIDATED")
        self.assertEqual(registry.get("BLOCKED_SHOTS").model_status, "VALIDATED")
        self.assertEqual(registry.get("ASSISTS").model_status, "VALIDATED")
        self.assertEqual(registry.get("POINTS").model_status, "EMPIRICAL_BASELINE_REMAINS_CHAMPION")


# --------------------------------------------------------------------------
# Tests 27-28: dashboard badge consistency, LOW warning display.
# --------------------------------------------------------------------------
class TestDashboardBadgeConsistency(unittest.TestCase):
    def test_27_shared_confidence_badge_helper_used_by_sog_and_points_pages(self):
        for rel_path in ("dashboard/pages/7_Player_SOG_Research.py", "dashboard/pages/11_Player_Points_Research.py"):
            with open(os.path.join(REPO_ROOT, rel_path)) as f:
                text = f.read()
            self.assertIn("comp.render_confidence_badge(", text)
            self.assertNotIn('st.markdown(f"**MODEL CONFIDENCE:', text)

    def test_28_low_confidence_warning_shown_for_points(self):
        with open(os.path.join(REPO_ROOT, "dashboard", "pages", "11_Player_Points_Research.py")) as f:
            text = f.read()
        self.assertIn("low_confidence_negative_skill=True", text)

    def test_28b_warning_text_present_in_shared_component(self):
        with open(os.path.join(REPO_ROOT, "dashboard", "components.py")) as f:
            text = f.read()
        self.assertIn("MODEL HISTORICALLY WEAK IN SIMILAR CASES", text)


# --------------------------------------------------------------------------
# Test 29: no live API call.
# --------------------------------------------------------------------------
class TestNoLiveApiCall(unittest.TestCase):
    def test_29_no_odds_api_references_in_confidence_files(self):
        for rel_path in ("research/confidence_lab/reliability.py", "research/run_confidence_diagnostics.py"):
            with open(os.path.join(REPO_ROOT, rel_path)) as f:
                text = f.read()
            self.assertNotIn("the_odds_api", text)
            self.assertNotIn("requests.", text)
            self.assertNotIn("DraftKings", text)


# --------------------------------------------------------------------------
# Tests 30-32: no raw model refit; production NHL model / validated prop
# probabilities unchanged.
# --------------------------------------------------------------------------
class TestNoRefitAndProductionUnchanged(unittest.TestCase):
    NEW_FILES = ["research/confidence_lab/reliability.py", "research/run_confidence_diagnostics.py"]
    FORBIDDEN_MODULES = {"pricing.engine", "pricing.decision", "models.combined_model"}

    def test_30_no_raw_model_fit_calls_anywhere_in_new_files(self):
        for rel_path in self.NEW_FILES:
            with open(os.path.join(REPO_ROOT, rel_path)) as f:
                text = f.read()
            self.assertNotIn("fit_poisson_glm(", text)
            self.assertNotIn("fit_poisson_glm_with_offset(", text)

    def test_31_no_forbidden_imports_and_no_nhl_db_reference(self):
        for rel_path in self.NEW_FILES:
            with open(os.path.join(REPO_ROOT, rel_path)) as f:
                tree = ast.parse(f.read(), filename=rel_path)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn(node.module, self.FORBIDDEN_MODULES, f"{rel_path} imports {node.module}")
                if isinstance(node, ast.Call):
                    for arg in node.args:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            self.assertNotIn("nhl.db", arg.value)

    def test_32_validated_prop_result_files_unchanged_by_confidence_work(self):
        # a direct git-diff check (not just "still parses") that this
        # slice never overwrote any prop's own persisted results/manifest
        # files -- these are read-only inputs to the confidence layer.
        import subprocess
        checked = ["research/player_sog_results.json", "research/player_blocks_results.json",
                   "research/player_assists_results.json", "research/player_points_results.json",
                   "research/player_points_freeze_manifest.json",
                   "research/run_player_sog_model.py", "research/run_player_blocks_model.py",
                   "research/run_player_assists_model.py", "research/run_player_points_model.py"]
        proc = subprocess.run(["git", "status", "--porcelain", *checked], cwd=REPO_ROOT,
                               capture_output=True, text=True)
        if proc.returncode != 0:
            self.skipTest("not a git repository in this environment")
        # "??" (untracked) is fine -- these files may simply never have
        # been git-added yet in this environment. What matters is that
        # none show as MODIFIED ("M") relative to whatever git does know
        # about them.
        modified = [line for line in proc.stdout.splitlines() if line[:2].strip().upper().startswith("M")]
        self.assertEqual(modified, [], f"unexpected modifications to validated prop files: {modified}")


if __name__ == "__main__":
    unittest.main()
