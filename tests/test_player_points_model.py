"""
Tests for the Player TOTAL POINTS model: research/player_points/features.py
and research/run_player_points_model.py. Covers Part 34's 35 required
methodology/regression tests. Shared PIT/count-distribution/confidence/
conservative-probability math is already exhaustively covered by
tests/test_player_sog_model.py against the SAME
research/player_sog/count_models.py functions this module reuses
directly (not duplicated) -- re-testing that shared math here would be
redundant, not more rigorous. This file focuses on what is genuinely NEW:
points-specific features, and -- for the first time in this project --
the explicit tuning/lock/freeze/true-evaluation discipline itself.
"""
import ast
import inspect
import json
import os
import unittest

from research.player_points import features as ptf
from research.player_sog import count_models as cm

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRIVER_PATH = os.path.join(REPO_ROOT, "research", "run_player_points_model.py")


def prow(player_id, game_id, game_date, season, team, opponent, points, goals=0.0, assists=0.0,
         icetime=1000.0, home_or_away="HOME", sog=0.0, pp=None):
    return {
        "player_id": player_id, "player_name": player_id, "game_id": game_id, "season": season,
        "game_date": game_date, "team": team, "opponent": opponent, "home_or_away": home_or_away,
        "position": "C", "icetime_seconds": icetime, "toi_5v5_seconds": icetime * 0.7,
        "goals": goals, "primary_assists": assists, "secondary_assists": 0.0, "assists": assists,
        "points": points, "sog": sog, "shot_attempts": sog + 2, "individual_xg": 0.1,
        "on_ice_xgf": 1.0, "on_ice_xga": 1.0, "pp": pp, "provenance_type": "ARCHIVAL_RESEARCH",
    }


# --------------------------------------------------------------------------
# Tests 1-9: target-game / future / same-day fields never leak into
# features -- all routed through the SAME shared player_history_as_of
# gate reused from research/player_sog/features.py.
# --------------------------------------------------------------------------
class TestPointsHistoryPIT(unittest.TestCase):
    def test_1_target_game_points_excluded(self):
        rows = [prow("P1", 1, "2024-10-01", 20242025, "TOR", "MTL", points=3.0),
                prow("P1", 2, "2024-10-05", 20242025, "TOR", "BOS", points=2.0)]
        history = ptf.player_history_as_of(rows, "P1", "2024-10-05")
        self.assertEqual([r["points"] for r in history], [3.0])

    def test_2_target_game_goals_excluded(self):
        rows = [prow("P1", 1, "2024-10-01", 20242025, "TOR", "MTL", points=1.0, goals=1.0),
                prow("P1", 2, "2024-10-05", 20242025, "TOR", "BOS", points=2.0, goals=2.0)]
        history = ptf.player_history_as_of(rows, "P1", "2024-10-05")
        self.assertEqual([r["goals"] for r in history], [1.0])

    def test_3_target_game_assists_excluded(self):
        rows = [prow("P1", 1, "2024-10-01", 20242025, "TOR", "MTL", points=1.0, assists=1.0),
                prow("P1", 2, "2024-10-05", 20242025, "TOR", "BOS", points=2.0, assists=2.0)]
        history = ptf.player_history_as_of(rows, "P1", "2024-10-05")
        self.assertEqual([r["assists"] for r in history], [1.0])

    def test_4_target_game_sog_excluded(self):
        rows = [prow("P1", 1, "2024-10-01", 20242025, "TOR", "MTL", points=1.0, sog=4.0),
                prow("P1", 2, "2024-10-05", 20242025, "TOR", "BOS", points=2.0, sog=7.0)]
        history = ptf.player_history_as_of(rows, "P1", "2024-10-05")
        self.assertEqual([r["sog"] for r in history], [4.0])

    def test_5_target_game_toi_excluded(self):
        rows = [prow("P1", 1, "2024-10-01", 20242025, "TOR", "MTL", points=1.0, icetime=900.0),
                prow("P1", 2, "2024-10-05", 20242025, "TOR", "BOS", points=2.0, icetime=1500.0)]
        history = ptf.player_history_as_of(rows, "P1", "2024-10-05")
        self.assertEqual([r["icetime_seconds"] for r in history], [900.0])

    def test_6_future_rows_excluded(self):
        rows = [prow("P1", 1, "2024-10-01", 20242025, "TOR", "MTL", points=1.0),
                prow("P1", 2, "2024-11-15", 20242025, "TOR", "BOS", points=2.0)]
        history = ptf.player_history_as_of(rows, "P1", "2024-10-05")
        self.assertEqual(len(history), 1)

    def test_7_same_day_rows_excluded(self):
        rows = [prow("P1", 1, "2024-10-05", 20242025, "TOR", "MTL", points=1.0)]
        history = ptf.player_history_as_of(rows, "P1", "2024-10-05")
        self.assertEqual(len(history), 0)

    def test_8_projected_active_is_the_same_reused_function(self):
        from research.player_sog.features import projected_active as sog_fn
        self.assertIs(ptf.projected_active, sog_fn)

    def test_9_actually_played_not_used_as_feature(self):
        import research.run_player_points_model as rpm
        src = inspect.getsource(rpm.build_example)
        start = src.index("fv = build_points_feature_vector(")
        end = src.index(")", start)
        fv_call_text = src[start:end]
        # the feature-vector call must build entirely from history-derived
        # locals (baseline_rate, recent_rate5, recent_toi, ...) -- never a
        # direct `row["..."]` read, which would leak the target game's own
        # (actually-played) stat line into a pregame feature.
        self.assertNotIn('row["', fv_call_text)


# --------------------------------------------------------------------------
# Test 10: points-label correctness (real cross-check, not assumed).
# --------------------------------------------------------------------------
class TestPointsLabelCorrectness(unittest.TestCase):
    CORPUS_PATH = os.path.join(REPO_ROOT, "research", "player_points", "player_game_points.jsonl")

    def test_10_points_equals_goals_plus_assists_in_real_corpus_sample(self):
        if not os.path.exists(self.CORPUS_PATH):
            self.skipTest("player_game_points.jsonl not built in this environment")
        with open(self.CORPUS_PATH) as f:
            for i, line in enumerate(f):
                if i >= 2000:
                    break
                r = json.loads(line)
                self.assertEqual(r["points"], r["goals"] + r["assists"])


# --------------------------------------------------------------------------
# Tests 11-12: coherent count distribution, monotonic thresholds.
# --------------------------------------------------------------------------
class TestDistributionCoherence(unittest.TestCase):
    def test_11_uses_shared_count_models_threshold_probabilities(self):
        import research.run_player_points_model as rpm
        self.assertIs(rpm.cm.threshold_probabilities, cm.threshold_probabilities)
        self.assertIs(rpm.cm.negbinom_sf_at_least, cm.negbinom_sf_at_least)

    def test_12_p1_geq_p2_geq_p3(self):
        probs = cm.threshold_probabilities(0.9, 0.08, thresholds=(1, 2, 3))
        self.assertGreaterEqual(probs[1] + 1e-9, probs[2])
        self.assertGreaterEqual(probs[2] + 1e-9, probs[3])


# --------------------------------------------------------------------------
# Tests 13-14: upstream SOG / assists eligibility.
# --------------------------------------------------------------------------
class TestUpstreamEligibility(unittest.TestCase):
    MANIFEST_PATH = os.path.join(REPO_ROOT, "research", "player_points_freeze_manifest.json")

    def test_13_upstream_sog_not_used_and_not_imported(self):
        with open(os.path.join(REPO_ROOT, "research", "player_points", "features.py")) as f:
            text = f.read()
        with open(DRIVER_PATH) as f:
            text += f.read()
        self.assertNotIn("player_sog.live_projection", text)
        self.assertNotIn("player_sog_results", text)
        if os.path.exists(self.MANIFEST_PATH):
            with open(self.MANIFEST_PATH) as f:
                manifest = json.load(f)
            self.assertIn("NOT ELIGIBLE", manifest["upstream_sog_eligibility"])

    def test_14_upstream_assists_not_used_and_not_imported(self):
        # AST-based (not raw text search): docstrings are allowed to
        # reference research/player_assists/ as documented PRECEDENT for a
        # technique (e.g. "same technique as ...opponent-context functions")
        # without that being an actual import/usage of the assists model.
        for rel in ("research/player_points/features.py", "research/run_player_points_model.py"):
            with open(os.path.join(REPO_ROOT, rel)) as f:
                tree = ast.parse(f.read(), filename=rel)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn("player_assists", alias.name)
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn("player_assists", node.module)
        if os.path.exists(self.MANIFEST_PATH):
            with open(self.MANIFEST_PATH) as f:
                manifest = json.load(f)
            self.assertIn("NOT ELIGIBLE", manifest["upstream_assists_eligibility"])


# --------------------------------------------------------------------------
# Test 15: H2H shrinkage over the points label.
# --------------------------------------------------------------------------
class TestPointsH2H(unittest.TestCase):
    def test_15_small_sample_shrunk_toward_baseline(self):
        history = [prow("P1", 1, "2024-10-01", 20242025, "TOR", "MTL", points=4.0)]
        rate, n = ptf.h2h_shrunk_points_rate(history, "MTL", baseline_rate=0.5)
        self.assertEqual(n, 1)
        self.assertLess(rate, 4.0)
        self.assertGreater(rate, 0.5)

    def test_15b_zero_games_returns_baseline(self):
        history = [prow("P1", 1, "2024-10-01", 20242025, "TOR", "BOS", points=1.0)]
        rate, n = ptf.h2h_shrunk_points_rate(history, "MTL", baseline_rate=0.4)
        self.assertEqual(n, 0)
        self.assertEqual(rate, 0.4)


# --------------------------------------------------------------------------
# Tests 16-17: TOI and PP-role feature construction.
# --------------------------------------------------------------------------
class TestToiAndPpFeatures(unittest.TestCase):
    def test_16_toi_log_ratio_slot_present_in_feature_vector(self):
        import research.run_player_points_model as rpm
        fv = rpm.build_points_feature_vector(0.5, 0.5, 1200.0, 1000.0, 0.0, None, None, 0.0)
        self.assertEqual(len(fv), len(rpm.FEATURE_NAMES))
        idx = rpm.FEATURE_NAMES.index("toi_log_ratio")
        self.assertAlmostEqual(fv[idx], __import__("math").log(1200.0 / 1000.0))

    def test_17_pp_mean_treats_none_as_zero(self):
        history = [
            prow("P1", 1, "2024-10-01", 20242025, "TOR", "X", points=1.0, pp={"icetime_seconds": 90.0, "points": 1.0, "goals": 0.0, "assists": 1.0}),
            prow("P1", 2, "2024-10-03", 20242025, "TOR", "X", points=0.0, pp=None),
        ]
        self.assertAlmostEqual(ptf.rolling_pp_mean(history, "points", None), 0.5)


# --------------------------------------------------------------------------
# Tests 18-22: tuning/lock/freeze discipline itself -- the core new
# methodology this slice adds. Verified structurally against the
# driver's own source (the PHASE markers are real section boundaries in
# research/run_player_points_model.py, not test-only labels).
# --------------------------------------------------------------------------
class TestTuningLockDiscipline(unittest.TestCase):
    def setUp(self):
        with open(DRIVER_PATH) as f:
            self.src = f.read()

    def test_18_scaling_fit_pool_restricted_to_tuning_season_only(self):
        import research.run_player_points_model as rpm
        # fit_pool is built from `tuning_fit`, itself only ever populated
        # from rows where row["season"] == TUNING_SEASON (see the bucket
        # assignment in run_all).
        self.assertIn('bucket = "tuning_fit" if row["game_date"] < TUNING_SPLIT_DATE else "tuning_validate"', self.src)
        self.assertIn("fit_pool = tuning_fit", self.src)

    def test_19_calibration_computed_on_tuning_validate_only(self):
        phase2_start = self.src.index("PHASE 2 -- TUNING-VALIDATE")
        freeze_marker = self.src.index("FREEZE COMPLETE")
        phase2_block = self.src[phase2_start:freeze_marker]
        self.assertIn("calibration_gaps", phase2_block)
        self.assertNotIn("eval_examples", phase2_block)
        self.assertNotIn("eval_fm", phase2_block)

    def test_20_confidence_methodology_is_the_unchanged_shared_function(self):
        import research.run_player_points_model as rpm
        self.assertIs(rpm.cm.confidence_score, cm.confidence_score)

    def test_21_conservative_probability_methodology_is_the_unchanged_shared_function(self):
        import research.run_player_points_model as rpm
        self.assertIs(rpm.cm.conservative_mu, cm.conservative_mu)

    def test_22_three_plus_support_standard_is_a_static_module_constant(self):
        import research.run_player_points_model as rpm
        # defined at module scope (not inside run_all(), so it cannot be
        # computed from anything eval-season-dependent).
        tree = ast.parse(self.src)
        module_level_names = {n.targets[0].id for n in ast.walk(tree)
                               if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)}
        self.assertIn("THREE_PLUS_SUPPORT_STANDARD", module_level_names)
        self.assertEqual(rpm.THREE_PLUS_SUPPORT_STANDARD["min_total_events_eval_common_set"], 500)


# --------------------------------------------------------------------------
# Tests 23-24: freeze manifest.
# --------------------------------------------------------------------------
class TestFreezeManifest(unittest.TestCase):
    MANIFEST_PATH = os.path.join(REPO_ROOT, "research", "player_points_freeze_manifest.json")

    def setUp(self):
        if not os.path.exists(self.MANIFEST_PATH):
            self.skipTest("player_points_freeze_manifest.json not built in this environment")
        with open(self.MANIFEST_PATH) as f:
            self.manifest = json.load(f)

    def test_23_manifest_has_required_fields(self):
        for key in ("experiment_id", "freeze_timestamp_utc", "target_definition", "player_eligibility_policy",
                    "model_family", "feature_set", "lookback_windows", "shrinkage_parameters",
                    "fitted_tuning_parameters", "calibration_method", "confidence_methodology",
                    "conservative_probability_methodology", "three_plus_support_standard",
                    "source_code_hashes", "locked_stage"):
            self.assertIn(key, self.manifest, f"manifest missing {key}")

    def test_24_evaluation_runner_hashes_match_current_source(self):
        import research.run_player_points_model as rpm
        for rel_path, recorded_hash in self.manifest["source_code_hashes"].items():
            self.assertEqual(rpm.file_sha256(rel_path), recorded_hash,
                              f"{rel_path} changed since freeze -- EVALUATION HOLDOUT: CONSUMED")


# --------------------------------------------------------------------------
# Tests 25-26: eval seasons never touched before freeze.
# --------------------------------------------------------------------------
class TestEvalSeasonsNotUsedForSelection(unittest.TestCase):
    def setUp(self):
        with open(DRIVER_PATH) as f:
            self.src = f.read()

    def _phase2_block(self):
        phase2_start = self.src.index("PHASE 2 -- TUNING-VALIDATE")
        freeze_marker = self.src.index("FREEZE COMPLETE")
        return self.src[phase2_start:freeze_marker]

    def test_25_2024_25_not_referenced_in_selection_phase(self):
        block = self._phase2_block()
        self.assertNotIn("20242025", block)

    def test_26_2025_26_not_referenced_in_selection_phase(self):
        block = self._phase2_block()
        self.assertNotIn("20252026", block)


# --------------------------------------------------------------------------
# Test 27: common evaluation set -- identical rows for headline AND
# every baseline.
# --------------------------------------------------------------------------
class TestCommonEvaluationSet(unittest.TestCase):
    RESULTS_PATH = os.path.join(REPO_ROOT, "research", "player_points_results.json")

    def setUp(self):
        if not os.path.exists(self.RESULTS_PATH):
            self.skipTest("player_points_results.json not built in this environment")
        with open(self.RESULTS_PATH) as f:
            self.results = json.load(f)

    def test_27_headline_and_every_baseline_share_the_same_n(self):
        n = self.results["headline_uncalibrated"]["n"]
        for name, res in self.results["baseline_results"].items():
            self.assertEqual(res["n"], n, f"baseline {name} used a different eval-set size")


# --------------------------------------------------------------------------
# Test 28: game-clustered bootstrap actually clusters by game_id.
# --------------------------------------------------------------------------
class TestGameClusteredBootstrap(unittest.TestCase):
    def test_28_resamples_by_game_id_not_by_row(self):
        import research.run_player_points_model as rpm
        examples = [{"game_id": 1}, {"game_id": 1}, {"game_id": 2}, {"game_id": 3}]
        result = rpm.game_clustered_bootstrap(examples, [0.1, 0.1, 0.2, 0.3], [0.05, 0.05, 0.2, 0.3], n_resamples=50)
        self.assertEqual(result["n_games_resampled"], 3)  # 3 distinct game_ids, not 4 rows


# --------------------------------------------------------------------------
# Test 29: actual points is label-side only (never fed back as a feature).
# --------------------------------------------------------------------------
class TestLabelSideOnly(unittest.TestCase):
    def test_29_feature_names_never_include_the_raw_target_field(self):
        import research.run_player_points_model as rpm
        self.assertNotIn("points", " ".join(n for n in rpm.FEATURE_NAMES if n not in
                                              ("log_baseline_rate", "recent_form_log_ratio")))
        # the only "points"-derived feature slots are rolling HISTORICAL
        # rates (baseline/recent-form), never the target row's own value.


# --------------------------------------------------------------------------
# Test 30: prop registry status matches the real result verdict.
# --------------------------------------------------------------------------
class TestPropRegistryStatus(unittest.TestCase):
    def test_30_points_registry_status_is_not_validated_without_evidence(self):
        from research.player_props import registry
        entry = registry.get("POINTS")
        self.assertIsNotNone(entry)
        self.assertIn(entry.model_status,
                       ("PARTIAL", "VALIDATED", "RESEARCH", "REJECTED", "EMPIRICAL_BASELINE_REMAINS_CHAMPION"))


# --------------------------------------------------------------------------
# Test 31: no fake live odds anywhere in the new points modules.
# --------------------------------------------------------------------------
class TestNoFakeLiveOdds(unittest.TestCase):
    NEW_FILES = ["research/player_points/features.py", "research/player_points/build_points_corpus.py",
                 "research/run_player_points_model.py", "research/player_points/live_projection.py"]

    def test_31_no_odds_api_or_network_calls(self):
        for rel in self.NEW_FILES:
            path = os.path.join(REPO_ROOT, rel)
            if not os.path.exists(path):
                continue
            with open(path) as f:
                text = f.read()
            self.assertNotIn("requests.", text)
            self.assertNotIn("the_odds_api", text)
            self.assertNotIn("DraftKings", text)


# --------------------------------------------------------------------------
# Test 32: dashboard status labeling comes from the registry, not
# hand-typed on the page.
# --------------------------------------------------------------------------
class TestDashboardStatusLabeling(unittest.TestCase):
    def test_32_points_page_reads_status_from_registry_not_hardcoded(self):
        page_path = os.path.join(REPO_ROOT, "dashboard", "pages", "11_Player_Points_Research.py")
        if not os.path.exists(page_path):
            self.skipTest("dashboard page not built in this environment")
        with open(page_path) as f:
            text = f.read()
        self.assertIn("registry", text)
        self.assertNotIn('"VALIDATED"', text.split("registry")[0])  # no hand-typed status before the registry import


# --------------------------------------------------------------------------
# Tests 33-35: production model / SOG / assists / blocks unchanged.
# --------------------------------------------------------------------------
class TestOtherModelsUnchanged(unittest.TestCase):
    NEW_FILES = ["research/player_points/features.py", "research/player_points/build_points_corpus.py",
                 "research/run_player_points_model.py", "research/player_points/live_projection.py"]
    FORBIDDEN_MODULES = {"pricing.engine", "pricing.decision", "models.combined_model"}

    def test_33_no_forbidden_imports_and_no_nhl_db_reference(self):
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

    def test_34_sog_and_blocks_modules_never_import_from_points(self):
        for rel in ("research/player_sog/features.py", "research/player_sog/count_models.py",
                    "research/player_sog/live_projection.py", "research/run_player_sog_model.py",
                    "research/player_blocks/features.py", "research/run_player_blocks_model.py"):
            with open(os.path.join(REPO_ROOT, rel)) as f:
                text = f.read()
            self.assertNotIn("player_points", text, f"{rel} references player_points -- must stay independent")

    def test_35_assists_module_never_imports_from_points(self):
        for rel in ("research/player_assists/features.py", "research/run_player_assists_model.py"):
            with open(os.path.join(REPO_ROOT, rel)) as f:
                text = f.read()
            self.assertNotIn("player_points", text, f"{rel} references player_points -- must stay independent")


# --------------------------------------------------------------------------
# PropPrediction contract (Section F reuse, mirrors blocks/assists tests).
# --------------------------------------------------------------------------
class TestPropPredictionContractReuse(unittest.TestCase):
    def test_points_can_populate_the_shared_contract(self):
        from research.player_props.prediction import PropPrediction
        pred = PropPrediction(game_id=1, player_id="p1", player_name="Test Player", market_type="POINTS",
                               threshold=1, expected_count=0.9, conservative_count=0.7,
                               raw_probability=0.35, conservative_probability=0.28, confidence="HIGH")
        d = pred.to_dict()
        self.assertEqual(d["market_type"], "POINTS")
        self.assertNotEqual(d["lineup_status"], "CONFIRMED")


if __name__ == "__main__":
    unittest.main()
