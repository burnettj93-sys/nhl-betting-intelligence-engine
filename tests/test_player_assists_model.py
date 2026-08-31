"""
Tests for the Player Assists Probability model:
research/player_assists/features.py and research/run_player_assists_model.py.
Mirrors tests/test_player_blocks_model.py's coverage for the genuinely
NEW assists-specific logic (opponent points-allowed environment, H2H
over the assists label); shared count/confidence/PIT math is already
covered by tests/test_player_sog_model.py against the same reused
research/player_sog/count_models.py functions.
"""
import ast
import os
import unittest

from research.player_assists import features as asf

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def arow(player_id, game_id, game_date, season, team, opponent, assists, points=0.0, icetime=1000.0):
    return {"player_id": player_id, "player_name": player_id, "game_id": game_id, "season": season,
            "game_date": game_date, "team": team, "opponent": opponent, "home_or_away": "HOME",
            "position": "C", "icetime_seconds": icetime, "assists": assists, "points": points,
            "on_ice_xgf": 1.0, "individual_xg": 0.1, "pp": None, "provenance_type": "ARCHIVAL_RESEARCH"}


class TestSharedFrameworkReuse(unittest.TestCase):
    def test_player_history_index_is_the_same_reused_class(self):
        from research.player_sog.features import PlayerHistoryIndex as SogIndex
        self.assertIs(asf.PlayerHistoryIndex, SogIndex)


class TestAssistsHistoryPIT(unittest.TestCase):
    def test_excludes_target_and_future_rows(self):
        rows = [
            arow("P1", 1, "2024-10-01", 20242025, "TOR", "MTL", assists=1.0),
            arow("P1", 2, "2024-10-05", 20242025, "TOR", "BOS", assists=2.0),
            arow("P1", 3, "2024-10-09", 20242025, "TOR", "OTT", assists=0.0),
        ]
        history = asf.player_history_as_of(rows, "P1", "2024-10-05")
        self.assertEqual([r["game_date"] for r in history], ["2024-10-01"])


class TestOpponentPointsAllowed(unittest.TestCase):
    def test_environment_uses_actual_opposing_offense(self):
        rows = [
            arow("P1", 1, "2024-10-01", 20242025, "TOR", "MTL", assists=1.0, points=3.0),
            arow("P2", 1, "2024-10-01", 20242025, "MTL", "TOR", assists=0.0, points=1.0),
        ]
        totals = asf.build_team_game_points_totals(rows)
        env = asf.build_opponent_points_allowed(totals)
        self.assertEqual(env["MTL"][0]["points_allowed"], 3.0)
        self.assertEqual(env["TOR"][0]["points_allowed"], 1.0)

    def test_environment_history_is_pit_safe(self):
        rows = [
            arow("P1", 1, "2024-10-01", 20242025, "TOR", "MTL", assists=1.0, points=3.0),
            arow("P2", 1, "2024-10-01", 20242025, "MTL", "TOR", assists=0.0, points=1.0),
            arow("P1", 2, "2024-10-10", 20242025, "TOR", "BOS", assists=0.0, points=0.0),
            arow("P3", 2, "2024-10-10", 20242025, "BOS", "TOR", assists=1.0, points=2.0),
        ]
        totals = asf.build_team_game_points_totals(rows)
        env = asf.build_opponent_points_allowed(totals)
        hist = asf.opponent_history_as_of(env, "TOR", "2024-10-10")
        self.assertEqual(len(hist), 1)


class TestAssistsH2H(unittest.TestCase):
    def test_small_sample_shrunk_toward_baseline(self):
        history = [arow("P1", 1, "2024-10-01", 20242025, "TOR", "MTL", assists=3.0)]
        rate, n = asf.h2h_shrunk_assists_rate(history, "MTL", baseline_rate=0.3)
        self.assertEqual(n, 1)
        self.assertLess(rate, 3.0)
        self.assertGreater(rate, 0.3)

    def test_zero_games_returns_baseline(self):
        history = [arow("P1", 1, "2024-10-01", 20242025, "TOR", "BOS", assists=1.0)]
        rate, n = asf.h2h_shrunk_assists_rate(history, "MTL", baseline_rate=0.4)
        self.assertEqual(n, 0)
        self.assertEqual(rate, 0.4)


class TestAssistsResultsFile(unittest.TestCase):
    RESULTS_PATH = os.path.join(REPO_ROOT, "research", "player_assists_results.json")

    def setUp(self):
        if not os.path.exists(self.RESULTS_PATH):
            self.skipTest("player_assists_results.json not built in this environment")
        import json
        with open(self.RESULTS_PATH) as f:
            self.results = json.load(f)

    def test_full_model_beats_baseline_on_brier(self):
        headline_brier = self.results["stage_results"]["M4_plus_h2h"]["thresholds"]["1"]["brier"]
        for name, res in self.results["baseline_results"].items():
            self.assertLess(headline_brier, res["thresholds"]["1"]["brier"])

    def test_common_evaluation_set_reasonable_scale(self):
        n = self.results["eval_examples_n"]
        self.assertGreater(n, 50000)
        self.assertLess(n, 100000)

    def test_overdispersion_matches_the_real_low_value_found_in_audit(self):
        # real 2024-season audit found var/mean ~1.09-1.14 for goals/
        # assists/points -- much milder than SOG/blocks (~1.4-1.5). This
        # is a regression guard on that real finding, not an assumption.
        ratio = self.results["overdispersion"]["variance_to_mean_ratio"]
        self.assertLess(ratio, 1.3)
        self.assertGreater(ratio, 1.0)


class TestProductionModelUnchanged(unittest.TestCase):
    NEW_FILES = ["research/player_assists/features.py", "research/player_assists/build_assists_corpus.py",
                 "research/run_player_assists_model.py", "research/player_props/registry.py"]
    FORBIDDEN_MODULES = {"pricing.engine", "pricing.decision", "models.combined_model"}

    def test_no_forbidden_imports(self):
        for rel in self.NEW_FILES:
            with open(os.path.join(REPO_ROOT, rel)) as f:
                tree = ast.parse(f.read(), filename=rel)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn(node.module, self.FORBIDDEN_MODULES, f"{rel} imports {node.module}")

    def test_sog_and_blocks_modules_never_import_from_assists(self):
        for rel in ("research/player_sog/features.py", "research/player_blocks/features.py",
                    "research/run_player_sog_model.py", "research/run_player_blocks_model.py"):
            with open(os.path.join(REPO_ROOT, rel)) as f:
                text = f.read()
            self.assertNotIn("player_assists", text)


class TestPropRegistry(unittest.TestCase):
    def test_registry_has_an_entry_for_every_sprint_required_prop(self):
        from research.player_props import registry
        market_types = {e.market_type for e in registry.REGISTRY}
        for required in ("SOG", "BLOCKED_SHOTS", "ASSISTS", "POINTS", "GOALS", "PP_POINTS",
                          "GOALIE_SAVES", "HITS", "PLUS_MINUS", "ANYTIME_GOAL", "FIRST_GOAL"):
            self.assertIn(required, market_types)

    def test_validated_prop_families_are_actually_validated_status(self):
        from research.player_props import registry
        for market_type in registry.validated_prop_families():
            entry = registry.get(market_type)
            self.assertEqual(entry.model_status, "VALIDATED")

    def test_unsupported_market_props_have_no_odds_api_key(self):
        from research.player_props import registry
        for entry in registry.REGISTRY:
            if entry.live_market_support == "UNSUPPORTED_MARKET":
                self.assertIsNone(entry.odds_api_market_key)


if __name__ == "__main__":
    unittest.main()
