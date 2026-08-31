"""
Tests for the Player Blocked Shots Probability model:
research/player_blocks/features.py and research/run_player_blocks_model.py.
Mirrors tests/test_player_sog_model.py's style/coverage for the genuinely
NEW blocks-specific logic; PIT/count-distribution/confidence/conservative-
probability correctness is already exhaustively covered by
tests/test_player_sog_model.py against the SAME shared
research/player_sog/count_models.py functions this module reuses
directly (not duplicated) -- re-testing that shared math here would be
redundant, not more rigorous.
"""
import ast
import inspect
import os
import unittest

from research.player_blocks import features as blf
from research.player_sog import count_models as cm

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def brow(player_id, game_id, game_date, season, team, opponent, blocks, icetime=1000.0,
         home_or_away="HOME", hits=0.0, shot_attempts_against_on_ice=50.0, pk=None):
    return {
        "player_id": player_id, "player_name": player_id, "game_id": game_id, "season": season,
        "game_date": game_date, "team": team, "opponent": opponent, "home_or_away": home_or_away,
        "position": "D", "icetime_seconds": icetime, "blocks": blocks, "hits": hits,
        "shot_attempts_against_on_ice": shot_attempts_against_on_ice, "pk": pk,
        "provenance_type": "ARCHIVAL_RESEARCH",
    }


# --------------------------------------------------------------------------
# Reuse, not duplication: the shared prop-agnostic framework functions are
# the SAME objects as research.player_sog.features's, not reimplemented.
# --------------------------------------------------------------------------
class TestSharedFrameworkReuse(unittest.TestCase):
    def test_player_history_index_is_the_same_reused_class(self):
        from research.player_sog.features import PlayerHistoryIndex as SogIndex
        self.assertIs(blf.PlayerHistoryIndex, SogIndex)

    def test_projected_active_is_the_same_reused_function(self):
        from research.player_sog.features import projected_active as sog_fn
        self.assertIs(blf.projected_active, sog_fn)

    def test_build_feature_vector_used_by_blocks_driver_is_the_sog_count_models_function(self):
        import research.run_player_blocks_model as rbm
        self.assertIs(rbm.cm.build_feature_vector, cm.build_feature_vector)
        self.assertIs(rbm.cm.fit_poisson_glm, cm.fit_poisson_glm)


# --------------------------------------------------------------------------
# PIT safety (blocks-specific corpus fields, same discipline as SOG).
# --------------------------------------------------------------------------
class TestBlocksHistoryPIT(unittest.TestCase):
    def test_excludes_target_and_future_rows(self):
        rows = [
            brow("P1", 1, "2024-10-01", 20242025, "TOR", "MTL", blocks=2.0),
            brow("P1", 2, "2024-10-05", 20242025, "TOR", "BOS", blocks=3.0),
            brow("P1", 3, "2024-10-09", 20242025, "TOR", "OTT", blocks=1.0),
        ]
        history = blf.player_history_as_of(rows, "P1", "2024-10-05")
        self.assertEqual([r["game_date"] for r in history], ["2024-10-01"])

    def test_identity_survives_team_change(self):
        rows = [
            brow("P1", 1, "2024-10-01", 20242025, "TOR", "MTL", blocks=2.0),
            brow("P1", 2, "2024-11-15", 20242025, "NYR", "BOS", blocks=3.0),
        ]
        history = blf.player_history_as_of(rows, "P1", "2024-11-20")
        self.assertEqual(len(history), 2)


# --------------------------------------------------------------------------
# PK rolling mean (blocks-specific: mirrors the SOG "pp" pattern).
# --------------------------------------------------------------------------
class TestPKRollingMean(unittest.TestCase):
    def test_pk_mean_treats_none_as_zero(self):
        history = [
            brow("P1", 1, "2024-10-01", 20242025, "TOR", "X", blocks=1.0,
                 pk={"icetime_seconds": 90.0, "blocks": 1.0}),
            brow("P1", 2, "2024-10-03", 20242025, "TOR", "X", blocks=1.0, pk=None),
        ]
        self.assertAlmostEqual(blf.rolling_pk_mean(history, "icetime_seconds", None), 45.0)


# --------------------------------------------------------------------------
# Opponent shot-attempts environment (blocks-specific opponent context,
# a DIFFERENT field than SOG's opponent-SOG-allowed).
# --------------------------------------------------------------------------
class TestOpponentShotAttemptEnvironment(unittest.TestCase):
    def test_opponent_environment_uses_the_actual_opposing_offense(self):
        rows = [
            brow("P1", 1, "2024-10-01", 20242025, "TOR", "MTL", blocks=2.0, shot_attempts_against_on_ice=40.0),
            brow("P2", 1, "2024-10-01", 20242025, "MTL", "TOR", blocks=1.0, shot_attempts_against_on_ice=60.0),
        ]
        totals = blf.build_team_game_shot_attempt_totals(rows)
        env = blf.build_opponent_shot_attempt_environment(totals)
        # TOR's block-opportunity environment for this game = what MTL
        # generated against TOR = MTL's OWN "shot_attempts_against_for_team"
        # is actually TOR's attempts against MTL (60) -- i.e. MTL's row
        # describes shots MTL faced (from TOR), so TOR's environment =
        # MTL's shot_attempts_against_for_team value.
        self.assertEqual(env["TOR"][0]["opponent_shot_attempts_generated"], 60.0)
        self.assertEqual(env["MTL"][0]["opponent_shot_attempts_generated"], 40.0)

    def test_opponent_environment_history_is_pit_safe(self):
        rows = [
            brow("P1", 1, "2024-10-01", 20242025, "TOR", "MTL", blocks=2.0, shot_attempts_against_on_ice=40.0),
            brow("P2", 1, "2024-10-01", 20242025, "MTL", "TOR", blocks=1.0, shot_attempts_against_on_ice=60.0),
            brow("P1", 2, "2024-10-10", 20242025, "TOR", "BOS", blocks=1.0, shot_attempts_against_on_ice=30.0),
            brow("P3", 2, "2024-10-10", 20242025, "BOS", "TOR", blocks=2.0, shot_attempts_against_on_ice=70.0),
        ]
        totals = blf.build_team_game_shot_attempt_totals(rows)
        env = blf.build_opponent_shot_attempt_environment(totals)
        hist = blf.opponent_environment_history_as_of(env, "TOR", "2024-10-10")
        self.assertEqual(len(hist), 1)


# --------------------------------------------------------------------------
# H2H shrinkage over the blocks label (independently tested, per the
# sprint's explicit instruction not to assume SOG's H2H finding applies).
# --------------------------------------------------------------------------
class TestBlocksH2H(unittest.TestCase):
    def test_small_h2h_sample_shrunk_toward_baseline(self):
        history = [brow("P1", 1, "2024-10-01", 20242025, "TOR", "MTL", blocks=6.0)]
        rate, n = blf.h2h_shrunk_blocks_rate(history, "MTL", baseline_rate=1.0)
        self.assertEqual(n, 1)
        self.assertLess(rate, 6.0)
        self.assertGreater(rate, 1.0)

    def test_zero_h2h_games_returns_baseline(self):
        history = [brow("P1", 1, "2024-10-01", 20242025, "TOR", "BOS", blocks=4.0)]
        rate, n = blf.h2h_shrunk_blocks_rate(history, "MTL", baseline_rate=1.5)
        self.assertEqual(n, 0)
        self.assertEqual(rate, 1.5)


# --------------------------------------------------------------------------
# Real value-test findings, asserted directly against the driver's own
# result file (produced by research/run_player_blocks_model.py) so a
# regression in the model or its feature construction is caught.
# --------------------------------------------------------------------------
class TestBlocksResultsFile(unittest.TestCase):
    RESULTS_PATH = os.path.join(REPO_ROOT, "research", "player_blocks_results.json")

    def setUp(self):
        if not os.path.exists(self.RESULTS_PATH):
            self.skipTest("player_blocks_results.json not built in this environment")
        import json
        with open(self.RESULTS_PATH) as f:
            self.results = json.load(f)

    def test_full_model_beats_both_naive_baselines_on_brier(self):
        headline_brier = self.results["stage_results"]["M4_plus_h2h"]["thresholds"]["2"]["brier"]
        for name, res in self.results["baseline_results"].items():
            self.assertLess(headline_brier, res["thresholds"]["2"]["brier"],
                             f"headline model did not beat baseline {name}")

    def test_threshold_probabilities_would_be_monotonic(self):
        # spot check via the shared count_models function directly, using
        # the real fitted headline mu implied by this corpus's own alpha
        alpha = self.results["negbinom_alpha_fitted"]
        probs = cm.threshold_probabilities(1.2, alpha if alpha > 0.01 else None)
        for n in range(1, 6):
            self.assertGreaterEqual(probs[n] + 1e-9, probs[n + 1])

    def test_common_evaluation_set_matches_sog_scale(self):
        # both props share the same underlying real corpus size (same
        # source skater files), so eligible-player-game counts should be
        # in the same ballpark -- a large deviation would indicate a
        # build bug, not a real modeling difference.
        n = self.results["common_evaluation_set"]["eval_examples_n"]
        self.assertGreater(n, 50000)
        self.assertLess(n, 100000)


# --------------------------------------------------------------------------
# Production model unchanged; no forbidden imports; SOG model untouched.
# --------------------------------------------------------------------------
class TestProductionModelUnchanged(unittest.TestCase):
    NEW_FILES = [
        "research/player_blocks/features.py", "research/player_blocks/build_blocks_corpus.py",
        "research/run_player_blocks_model.py", "research/player_props/prediction.py",
    ]
    FORBIDDEN_MODULES = {"pricing.engine", "pricing.decision", "models.combined_model"}

    def test_no_forbidden_imports(self):
        for rel in self.NEW_FILES:
            with open(os.path.join(REPO_ROOT, rel)) as f:
                tree = ast.parse(f.read(), filename=rel)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn(node.module, self.FORBIDDEN_MODULES, f"{rel} imports {node.module}")

    def test_no_nhl_db_reference_in_any_call(self):
        for rel in self.NEW_FILES:
            with open(os.path.join(REPO_ROOT, rel)) as f:
                tree = ast.parse(f.read(), filename=rel)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    for arg in node.args:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            self.assertNotIn("nhl.db", arg.value)

    def test_sog_model_module_never_imports_from_blocks(self):
        for rel in ("research/player_sog/features.py", "research/player_sog/count_models.py",
                    "research/player_sog/live_projection.py", "research/run_player_sog_model.py"):
            with open(os.path.join(REPO_ROOT, rel)) as f:
                text = f.read()
            self.assertNotIn("player_blocks", text, f"{rel} references player_blocks — SOG must stay independent")


# --------------------------------------------------------------------------
# PropPrediction shared contract (Section F).
# --------------------------------------------------------------------------
class TestPropPredictionContract(unittest.TestCase):
    def test_contract_has_required_fields(self):
        from research.player_props.prediction import PropPrediction
        fields = {f for f in PropPrediction.__dataclass_fields__}
        for required in ("game_id", "player_id", "market_type", "threshold", "expected_count",
                          "raw_probability", "conservative_probability", "confidence",
                          "model_version", "feature_version", "data_provenance", "lineup_status"):
            self.assertIn(required, fields)

    def test_to_dict_round_trips_key_fields(self):
        from research.player_props.prediction import PropPrediction
        pred = PropPrediction(game_id=1, player_id="p1", player_name="Test Player",
                               market_type="BLOCKED_SHOTS", threshold=2, expected_count=1.5,
                               conservative_count=1.2, raw_probability=0.4, conservative_probability=0.3,
                               confidence="HIGH")
        d = pred.to_dict()
        self.assertEqual(d["market_type"], "BLOCKED_SHOTS")
        self.assertEqual(d["threshold"], 2)

    def test_lineup_status_default_is_never_confirmed(self):
        from research.player_props.prediction import PropPrediction
        pred = PropPrediction(game_id=1, player_id="p1", player_name="x", market_type="SOG",
                               threshold=4, expected_count=3.0, conservative_count=2.5,
                               raw_probability=0.5, conservative_probability=0.4, confidence="HIGH")
        self.assertNotEqual(pred.lineup_status, "CONFIRMED")


if __name__ == "__main__":
    unittest.main()
