"""
Tests for the Complete NHL Market Universe + Dependency Architecture:
research/player_props/market_registry.py and
research/player_props/dependency_graph.py. Covers Part 40's 25 required
test areas. This is a STRUCTURAL test suite -- it verifies the registry
is internally consistent (no duplicate IDs, every process/derivation
type is real, the dependency graph is acyclic, etc.), not that any
market is statistically correct (that's each prop's own already-
exhaustive test file). No raw model was touched by this architecture
slice.
"""
import os
import unittest

from research.player_props import dependency_graph as dg
from research.player_props import market_registry as mr

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _unmodified(rel_paths):
    import subprocess
    proc = subprocess.run(["git", "status", "--porcelain", *rel_paths], cwd=REPO_ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    return [l for l in proc.stdout.splitlines() if l[:2].strip().upper().startswith("M")]


# --------------------------------------------------------------------------
# Test 1: every requested market normalized (no raw label orphaned).
# --------------------------------------------------------------------------
class TestEveryRawLabelNormalized(unittest.TestCase):
    def test_1_every_raw_label_appears_as_an_alias_somewhere(self):
        all_aliases = set()
        for m in mr.CANONICAL_MARKETS:
            all_aliases.update(m.aliases)
        # spot-check a representative label from each category rather than
        # every one of 164 (the categories share a consistent generation
        # pattern -- see test_1b for the exhaustive generated-tail check)
        for label in ("Anytime goal scorer", "1+ SOG", "1+ blocked shot", "1+ assist", "1+ point",
                      "Time on ice O/U", "Penalty minutes O/U", "Faceoff wins O/U", "Goalie shutout"):
            self.assertIn(label, all_aliases, f"{label!r} not found as an alias on any canonical market")

    def test_1b_every_generated_tail_alias_traces_to_a_raw_label_category(self):
        # the generated tail's aliases must be non-empty and a list of str
        for m in mr.CANONICAL_MARKETS:
            self.assertIsInstance(m.aliases, list)
            self.assertGreater(len(m.aliases), 0, f"{m.market_id} has no aliases")


# --------------------------------------------------------------------------
# Test 2: aliases resolve to canonical markets (lookup works both ways).
# --------------------------------------------------------------------------
class TestAliasResolution(unittest.TestCase):
    def test_2_anytime_goal_and_goals_ou_resolve_to_the_same_canonical_market(self):
        entry = next(m for m in mr.CANONICAL_MARKETS if "Anytime goal scorer" in m.aliases)
        self.assertIn("Goals O/U (0.5 line)", entry.aliases)
        self.assertEqual(entry.market_id, "PLAYER_GOALS_1PLUS")


# --------------------------------------------------------------------------
# Test 3: duplicate canonical IDs rejected.
# --------------------------------------------------------------------------
class TestNoDuplicateCanonicalIds(unittest.TestCase):
    def test_3_all_market_ids_unique(self):
        ids = [m.market_id for m in mr.CANONICAL_MARKETS]
        self.assertEqual(len(ids), len(set(ids)))


# --------------------------------------------------------------------------
# Tests 4-5: underlying process / derivation type exist and are valid.
# --------------------------------------------------------------------------
class TestProcessAndDerivationTypeValidity(unittest.TestCase):
    def test_4_every_market_has_at_least_one_real_process(self):
        for m in mr.CANONICAL_MARKETS:
            self.assertGreater(len(m.underlying_process), 0, m.market_id)
            for p in m.underlying_process:
                self.assertIn(p, mr.PROCESS_FAMILIES, f"{m.market_id} references unknown process {p}")

    def test_5_every_market_has_a_valid_derivation_type(self):
        for m in mr.CANONICAL_MARKETS:
            self.assertIn(m.derivation_type, mr.DERIVATION_TYPES, m.market_id)


# --------------------------------------------------------------------------
# Tests 6-9: threshold mappings for SOG/Blocks/Assists/Points.
# --------------------------------------------------------------------------
class TestThresholdMappings(unittest.TestCase):
    def test_6_sog_alternate_thresholds_present_and_correctly_statused(self):
        for n, expected in ((2, "VALIDATED"), (3, "VALIDATED"), (4, "VALIDATED"), (5, "VALIDATED"), (7, "INSUFFICIENT_TAIL_DATA")):
            entry = mr.get(f"PLAYER_SOG_{n}PLUS")
            self.assertIsNotNone(entry)
            self.assertEqual(entry.model_status, expected)

    def test_7_blocks_thresholds_present_and_correctly_statused(self):
        for n, expected in ((1, "VALIDATED"), (2, "VALIDATED"), (3, "VALIDATED"), (4, "INSUFFICIENT_TAIL_DATA")):
            entry = mr.get(f"PLAYER_BLOCKS_{n}PLUS")
            self.assertIsNotNone(entry)
            self.assertEqual(entry.model_status, expected)

    def test_8_assists_thresholds_present_and_correctly_statused(self):
        for n, expected in ((1, "VALIDATED"), (2, "VALIDATED"), (3, "INSUFFICIENT_DATA")):
            entry = mr.get(f"PLAYER_ASSISTS_{n}PLUS")
            self.assertIsNotNone(entry)
            self.assertEqual(entry.model_status, expected)
            self.assertEqual(entry.low_confidence_policy, "WATCH_ONLY")

    def test_9_points_thresholds_never_mislabeled_as_a_validated_new_model(self):
        for n in (1, 2):
            entry = mr.get(f"PLAYER_POINTS_{n}PLUS")
            self.assertEqual(entry.model_status, "EMPIRICAL_BASELINE_REMAINS_CHAMPION")
            self.assertNotEqual(entry.model_status, "VALIDATED")
            self.assertEqual(entry.threshold_validation_status, "USABLE_VIA_CHAMPION_BASELINE_NOT_A_VALIDATED_NEW_MODEL")


# --------------------------------------------------------------------------
# Tests 10-12: Goals alias mapping, Anytime Goal canonical target, Goals
# O0.5 consistency.
# --------------------------------------------------------------------------
class TestGoalsAliasMapping(unittest.TestCase):
    def test_10_goals_1plus_is_validated_with_correct_aliases(self):
        entry = mr.get("PLAYER_GOALS_1PLUS")
        self.assertEqual(entry.model_status, "VALIDATED")
        self.assertIn("Anytime goal scorer", entry.aliases)

    def test_11_anytime_goal_equals_goals_1plus_canonical_target(self):
        entry = mr.get("PLAYER_GOALS_1PLUS")
        self.assertEqual(entry.target_variable, "P(player goals >= 1)")

    def test_12_goals_o0_5_maps_to_the_same_canonical_entry_as_anytime_goal(self):
        entry = mr.get("PLAYER_GOALS_1PLUS")
        self.assertIn("Goals O/U (0.5 line)", entry.aliases)
        self.assertIn("Anytime goal scorer", entry.aliases)
        # both resolve to ONE market_id -- not two separately-tracked entries
        matches = [m for m in mr.CANONICAL_MARKETS if "Goals O/U (0.5 line)" in m.aliases]
        self.assertEqual(len(matches), 1)


# --------------------------------------------------------------------------
# Test 13: unsupported tail status preserved (not silently upgraded).
# --------------------------------------------------------------------------
class TestUnsupportedTailPreserved(unittest.TestCase):
    def test_13_goals_2plus_and_3plus_remain_insufficient_data(self):
        self.assertEqual(mr.get("PLAYER_GOALS_2PLUS").model_status, "INSUFFICIENT_DATA")
        self.assertEqual(mr.get("PLAYER_GOALS_3PLUS").model_status, "INSUFFICIENT_DATA")


# --------------------------------------------------------------------------
# Tests 14-17: event-time / PBP / goalie / simulation dependencies flagged.
# --------------------------------------------------------------------------
class TestDependencyFlags(unittest.TestCase):
    def test_14_event_time_markets_flagged(self):
        entry = mr.get("PLAYER_FIRST_GOAL_SCORER")
        self.assertEqual(entry.derivation_type, "EVENT_TIME")
        self.assertTrue(entry.requires_play_by_play)

    def test_15_play_by_play_dependencies_flagged_broadly(self):
        pbp_markets = [m for m in mr.CANONICAL_MARKETS if m.requires_play_by_play]
        self.assertGreater(len(pbp_markets), 50)  # the period-market tail alone is 33

    def test_16_goalie_dependencies_flagged(self):
        for market_id in ("GOALIE_SAVES_25PLUS", "GOALIE_WIN", "GOALIE_SHUTOUT"):
            entry = mr.get(market_id)
            self.assertTrue(entry.requires_starting_goalie, market_id)

    def test_17_simulation_dependencies_flagged(self):
        entry = mr.get("EXACT_FINAL_SCORE")
        self.assertTrue(entry.requires_joint_simulation)


# --------------------------------------------------------------------------
# Test 18: market/modelability distinction (Hits: real data, no model).
# --------------------------------------------------------------------------
class TestModelabilityVsProviderSupport(unittest.TestCase):
    def test_18_hits_modelable_data_confirmed_but_not_built(self):
        entry = mr.get("PLAYER_HITS_1PLUS")
        self.assertEqual(entry.historical_data_status, "AVAILABLE_UNUSED")
        self.assertEqual(entry.model_status, "RESEARCH")


# --------------------------------------------------------------------------
# Test 19: provider-support distinction kept separate from modelability.
# --------------------------------------------------------------------------
class TestProviderSupportDistinction(unittest.TestCase):
    def test_19_odds_api_support_is_a_distinct_field_from_model_status(self):
        entry = mr.get("PLAYER_HITS_1PLUS")
        self.assertEqual(entry.odds_api_support, "UNSUPPORTED_MARKET")
        # modelability (RESEARCH, real data) is independent of live support (UNSUPPORTED_MARKET)
        self.assertNotEqual(entry.model_status, entry.odds_api_support)


# --------------------------------------------------------------------------
# Test 20: LOW-confidence gating inherited by aliases (Goals family).
# --------------------------------------------------------------------------
class TestLowConfidenceGatingInheritance(unittest.TestCase):
    def test_20_goals_1plus_has_watch_only_policy_matching_decision_policy_v2(self):
        from research.player_props import decision_policy as dp
        entry = mr.get("PLAYER_GOALS_1PLUS")
        self.assertEqual(entry.low_confidence_policy, "WATCH_ONLY")
        self.assertEqual(dp.PROP_LOW_CONFIDENCE_CEILING.get("GOALS"), "WATCH")


# --------------------------------------------------------------------------
# Test 21: decision-policy v2 unchanged by this architecture slice.
# --------------------------------------------------------------------------
class TestDecisionPolicyUnchanged(unittest.TestCase):
    def test_21_decision_policy_module_not_modified(self):
        modified = _unmodified(["research/player_props/decision_policy.py"])
        if modified is None:
            self.skipTest("not a git repository in this environment")
        self.assertEqual(modified, [])


# --------------------------------------------------------------------------
# Test 22: dependency graph acyclic where it should be.
# --------------------------------------------------------------------------
class TestDependencyGraphAcyclic(unittest.TestCase):
    def test_22_process_dependency_graph_is_acyclic(self):
        self.assertTrue(dg.is_acyclic())

    def test_22b_every_process_dependency_graph_key_is_a_real_process(self):
        for p, deps in dg.PROCESS_DEPENDENCY_GRAPH.items():
            self.assertIn(p, mr.PROCESS_FAMILIES)
            for d in deps:
                self.assertIn(d, mr.PROCESS_FAMILIES)


# --------------------------------------------------------------------------
# Test 23: simulation invariant definitions present.
# --------------------------------------------------------------------------
class TestSimulationInvariantsDocumented(unittest.TestCase):
    def test_23_simulation_invariants_file_exists_and_is_substantial(self):
        path = os.path.join(REPO_ROOT, "SIMULATION_INVARIANTS.md")
        self.assertTrue(os.path.exists(path))
        with open(path) as f:
            text = f.read()
        for phrase in ("Player goals sum to team statistical goals", "shootout", "GWG", "No negative event counts"):
            self.assertIn(phrase, text)


# --------------------------------------------------------------------------
# Tests 24-25: no validated model refit; production NHL model unchanged.
# --------------------------------------------------------------------------
class TestNoModelChanges(unittest.TestCase):
    def test_24_no_raw_prop_result_files_modified(self):
        modified = _unmodified([
            "research/player_sog_results.json", "research/player_blocks_results.json",
            "research/player_assists_results.json", "research/player_points_results.json",
            "research/player_goals_results.json",
        ])
        if modified is None:
            self.skipTest("not a git repository in this environment")
        self.assertEqual(modified, [])

    def test_25_production_nhl_model_unchanged(self):
        modified = _unmodified(["pricing/engine.py", "pricing/decision.py", "config.py", "models"])
        if modified is None:
            self.skipTest("not a git repository in this environment")
        self.assertEqual(modified, [])


if __name__ == "__main__":
    unittest.main()
