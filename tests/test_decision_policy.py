"""
Tests for the Low-Confidence Assists/Points Gating Policy:
research/player_props/decision_policy.py, the registry's new
low_confidence_bet_eligibility field, and the dashboard's merged
policy-note warning. Covers Part 18's 32 required test areas. This is a
decision-ELIGIBILITY layer only -- it never touches raw probability,
conservative probability, edge, EV, or any raw prop model, all of which
already have their own exhaustive test coverage elsewhere in this
project; those are not re-tested here beyond confirming this new layer
never calls into them.
"""
import ast
import os
import unittest

from research.player_props import decision_policy as dp

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --------------------------------------------------------------------------
# Tests 1-2: LOW Assists/Points cannot return BET.
# --------------------------------------------------------------------------
class TestLowConfidenceCannotBet(unittest.TestCase):
    def test_1_low_assists_bet_is_capped_to_watch(self):
        out = dp.gate_low_confidence("ASSISTS", "LOW", "BET", "edge/EV clear")
        self.assertEqual(out["final_decision"], "WATCH")
        self.assertNotEqual(out["final_decision"], "BET")

    def test_2_low_points_bet_is_capped_to_watch(self):
        out = dp.gate_low_confidence("POINTS", "LOW", "BET", "edge/EV clear")
        self.assertEqual(out["final_decision"], "WATCH")
        self.assertNotEqual(out["final_decision"], "BET")


# --------------------------------------------------------------------------
# Tests 3-6: MEDIUM/HIGH Assists/Points unchanged.
# --------------------------------------------------------------------------
class TestOtherConfidenceUnchanged(unittest.TestCase):
    def test_3_medium_assists_unchanged(self):
        out = dp.gate_low_confidence("ASSISTS", "MEDIUM", "BET")
        self.assertEqual(out["final_decision"], "BET")
        self.assertIsNone(out["policy_override"])

    def test_4_high_assists_unchanged(self):
        out = dp.gate_low_confidence("ASSISTS", "HIGH", "BET")
        self.assertEqual(out["final_decision"], "BET")

    def test_5_medium_points_unchanged(self):
        out = dp.gate_low_confidence("POINTS", "MEDIUM", "BET")
        self.assertEqual(out["final_decision"], "BET")

    def test_6_high_points_unchanged(self):
        out = dp.gate_low_confidence("POINTS", "HIGH", "BET")
        self.assertEqual(out["final_decision"], "BET")


# --------------------------------------------------------------------------
# Tests 7-8: SOG/Blocks LOW unchanged by this policy (no entry in the
# central ceiling table -- the correct generic default).
# --------------------------------------------------------------------------
class TestUnrestrictedProps(unittest.TestCase):
    def test_7_sog_low_unchanged(self):
        out = dp.gate_low_confidence("SOG", "LOW", "BET")
        self.assertEqual(out["final_decision"], "BET")
        self.assertIsNone(out["policy_override"])

    def test_8_blocks_low_unchanged(self):
        out = dp.gate_low_confidence("BLOCKED_SHOTS", "LOW", "BET")
        self.assertEqual(out["final_decision"], "BET")
        self.assertIsNone(out["policy_override"])

    def test_no_sog_entry_in_ceiling_table(self):
        self.assertNotIn("SOG", dp.PROP_LOW_CONFIDENCE_CEILING)
        self.assertNotIn("BLOCKED_SHOTS", dp.PROP_LOW_CONFIDENCE_CEILING)


# --------------------------------------------------------------------------
# Test 9: WATCH used for a valid-but-gated opportunity.
# --------------------------------------------------------------------------
class TestWatchForGatedOpportunity(unittest.TestCase):
    def test_9_gated_bet_becomes_watch_not_wait_or_pass(self):
        out = dp.gate_low_confidence("ASSISTS", "LOW", "BET", "conservative edge +4.2%, EV +3.1%")
        self.assertEqual(out["final_decision"], "WATCH")
        self.assertNotEqual(out["final_decision"], "WAIT")
        self.assertNotEqual(out["final_decision"], "PASS")


# --------------------------------------------------------------------------
# Test 10: WAIT precedence for unresolved lineup/data -- an upstream WAIT
# is never touched or "un-gated" by this layer.
# --------------------------------------------------------------------------
class TestWaitPrecedence(unittest.TestCase):
    def test_10_upstream_wait_passes_through_unchanged(self):
        out = dp.gate_low_confidence("ASSISTS", "LOW", "WAIT", "lineup unconfirmed")
        self.assertEqual(out["final_decision"], "WAIT")
        self.assertEqual(out["policy_reason"], "lineup unconfirmed")
        self.assertIsNone(out["policy_override"])


# --------------------------------------------------------------------------
# Tests 11-12: PASS / DATA_UNAVAILABLE behavior unchanged.
# --------------------------------------------------------------------------
class TestTerminalStatusesUnchanged(unittest.TestCase):
    def test_11_pass_stays_pass_regardless_of_confidence(self):
        for confidence in ("LOW", "MEDIUM", "HIGH"):
            out = dp.gate_low_confidence("ASSISTS", confidence, "PASS", "no positive raw edge")
            self.assertEqual(out["final_decision"], "PASS")
            self.assertIsNone(out["policy_override"])

    def test_12_data_unavailable_stays_data_unavailable(self):
        out = dp.gate_low_confidence("POINTS", "LOW", "DATA_UNAVAILABLE", "stale quote")
        self.assertEqual(out["final_decision"], "DATA_UNAVAILABLE")
        self.assertIsNone(out["policy_override"])


# --------------------------------------------------------------------------
# Tests 13-19: pricing math (raw/conservative probability, edge, EV, fair
# price) is never computed, read, or mutated by this module -- structural.
# --------------------------------------------------------------------------
class TestPricingMathUntouched(unittest.TestCase):
    MODULE_PATH = os.path.join(REPO_ROOT, "research", "player_props", "decision_policy.py")

    def test_13_raw_probability_never_referenced(self):
        with open(self.MODULE_PATH) as f:
            text = f.read()
        for forbidden in ("model_probability", "raw_probability", "conservative_probability",
                           "raw_edge", "conservative_edge", "raw_ev", "conservative_ev", "fair_price"):
            self.assertNotIn(forbidden, text)

    def test_14_conservative_probability_never_referenced(self):
        # duplicate-named per Part 18's list item 14, same structural check
        with open(self.MODULE_PATH) as f:
            text = f.read()
        self.assertNotIn("conservative_prob", text)

    def test_15_no_edge_math_in_module(self):
        with open(self.MODULE_PATH) as f:
            tree = ast.parse(f.read())
        # string concatenation (Add on f-strings, for reason-building) is
        # fine; multiplication/division/subtraction -- the operators any
        # real edge/EV/price calculation would need -- must never appear.
        for node in ast.walk(tree):
            if isinstance(node, ast.BinOp):
                self.assertIsInstance(node.op, ast.Add)

    def test_16_never_imports_odds_math_or_pricing_engine(self):
        with open(self.MODULE_PATH) as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [node.module] if isinstance(node, ast.ImportFrom) else [a.name for a in node.names]
                for n in names:
                    if n:
                        self.assertNotIn("odds_math", n)
                        self.assertNotIn("pricing.engine", n)
                        self.assertNotIn("pricing.decision", n)

    def test_17_never_imports_config(self):
        with open(self.MODULE_PATH) as f:
            text = f.read()
        self.assertNotIn("import config", text)

    def test_18_gate_function_takes_status_strings_only(self):
        import inspect
        sig = inspect.signature(dp.gate_low_confidence)
        for name, param in sig.parameters.items():
            self.assertNotIn("prob", name.lower())
            self.assertNotIn("edge", name.lower())
            self.assertNotIn("ev", name.lower())

    def test_19_output_never_contains_a_numeric_probability_field(self):
        out = dp.gate_low_confidence("ASSISTS", "LOW", "BET")
        for key in out:
            self.assertNotIn("prob", key.lower())
            self.assertNotIn("edge", key.lower())


# --------------------------------------------------------------------------
# Tests 20-21: policy reason + version emitted on every call.
# --------------------------------------------------------------------------
class TestPolicyMetadataEmitted(unittest.TestCase):
    def test_20_policy_reason_present_for_gated_decision(self):
        out = dp.gate_low_confidence("POINTS", "LOW", "BET", "edge/EV clear")
        self.assertTrue(out["policy_reason"])
        self.assertIn("POINTS", out["policy_reason"])

    def test_21_policy_version_present_on_every_call(self):
        for status in ("BET", "WATCH", "PASS", "WAIT", "DATA_UNAVAILABLE"):
            out = dp.gate_low_confidence("ASSISTS", "LOW", status)
            self.assertEqual(out["policy_version"], dp.POLICY_VERSION)


# --------------------------------------------------------------------------
# Tests 22-23: observation-ledger shape (mathematical vs. final decision
# stored separately, underlying result never overwritten).
# --------------------------------------------------------------------------
class TestObservationLedgerShape(unittest.TestCase):
    def test_22_mathematical_status_preserved_alongside_final_decision(self):
        out = dp.gate_low_confidence("ASSISTS", "LOW", "BET", "clears both edge and EV bars")
        self.assertEqual(out["mathematical_status"], "BET")
        self.assertEqual(out["final_decision"], "WATCH")
        self.assertNotEqual(out["mathematical_status"], out["final_decision"])

    def test_23_final_policy_decision_stored_as_a_distinct_field(self):
        out = dp.gate_low_confidence("ASSISTS", "LOW", "BET")
        self.assertIn("final_decision", out)
        self.assertIn("mathematical_status", out)
        self.assertIn("policy_override", out)
        self.assertEqual(out["policy_override"], "LOW_CONFIDENCE_ASSISTS")


# --------------------------------------------------------------------------
# Test 24: prop registry metadata.
# --------------------------------------------------------------------------
class TestRegistryMetadata(unittest.TestCase):
    def test_24_registry_reflects_watch_only_for_assists_and_points(self):
        from research.player_props import registry
        self.assertEqual(registry.get("ASSISTS").low_confidence_bet_eligibility, "WATCH_ONLY")
        self.assertEqual(registry.get("POINTS").low_confidence_bet_eligibility, "WATCH_ONLY")
        self.assertEqual(registry.get("SOG").low_confidence_bet_eligibility, "NORMAL")
        self.assertEqual(registry.get("BLOCKED_SHOTS").low_confidence_bet_eligibility, "NORMAL")

    def test_24b_points_status_not_upgraded_by_this_slice(self):
        from research.player_props import registry
        self.assertEqual(registry.get("POINTS").model_status, "EMPIRICAL_BASELINE_REMAINS_CHAMPION")


# --------------------------------------------------------------------------
# Test 25: dashboard explanation -- merged into the existing warning, not
# a duplicate box (Part 13).
# --------------------------------------------------------------------------
class TestDashboardExplanation(unittest.TestCase):
    def test_25_points_page_passes_market_type_for_the_policy_note(self):
        path = os.path.join(REPO_ROOT, "dashboard", "pages", "11_Player_Points_Research.py")
        with open(path) as f:
            text = f.read()
        self.assertIn('market_type="POINTS"', text)

    def test_25b_single_caption_call_not_two_separate_warning_boxes(self):
        path = os.path.join(REPO_ROOT, "dashboard", "components.py")
        with open(path) as f:
            src = f.read()
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "render_confidence_badge")
        caption_calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
                          and isinstance(n.func, ast.Attribute) and n.func.attr == "caption"]
        self.assertEqual(len(caption_calls), 1)


# --------------------------------------------------------------------------
# Tests 26-32: no model refit, confidence formula unchanged, production
# models unchanged.
# --------------------------------------------------------------------------
class TestNoModelChanges(unittest.TestCase):
    def test_26_no_fit_calls_anywhere_in_new_policy_module(self):
        with open(os.path.join(REPO_ROOT, "research", "player_props", "decision_policy.py")) as f:
            text = f.read()
        self.assertNotIn("fit_poisson_glm", text)
        self.assertNotIn("fit_negbinom_alpha", text)

    def test_27_confidence_score_function_unmodified_and_unimported_here(self):
        with open(os.path.join(REPO_ROOT, "research", "player_props", "decision_policy.py")) as f:
            text = f.read()
        self.assertNotIn("confidence_score", text)  # this module takes a confidence LABEL, never computes one

    def test_28_no_forbidden_nhl_win_model_imports(self):
        with open(os.path.join(REPO_ROOT, "research", "player_props", "decision_policy.py")) as f:
            tree = ast.parse(f.read())
        forbidden = {"pricing.engine", "pricing.decision", "models.combined_model"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertNotIn(node.module, forbidden)

    def test_29_sog_pricing_module_unmodified_by_this_slice(self):
        import subprocess
        proc = subprocess.run(["git", "status", "--porcelain", "research/live_sog_pricing/pricing.py"],
                               cwd=REPO_ROOT, capture_output=True, text=True)
        if proc.returncode != 0:
            self.skipTest("not a git repository in this environment")
        modified = [l for l in proc.stdout.splitlines() if l[:2].strip().upper().startswith("M")]
        self.assertEqual(modified, [])

    def test_30_blocks_model_unchanged(self):
        import subprocess
        proc = subprocess.run(["git", "status", "--porcelain", "research/run_player_blocks_model.py"],
                               cwd=REPO_ROOT, capture_output=True, text=True)
        if proc.returncode != 0:
            self.skipTest("not a git repository in this environment")
        modified = [l for l in proc.stdout.splitlines() if l[:2].strip().upper().startswith("M")]
        self.assertEqual(modified, [])

    def test_31_assists_model_unchanged(self):
        import subprocess
        proc = subprocess.run(["git", "status", "--porcelain", "research/run_player_assists_model.py"],
                               cwd=REPO_ROOT, capture_output=True, text=True)
        if proc.returncode != 0:
            self.skipTest("not a git repository in this environment")
        modified = [l for l in proc.stdout.splitlines() if l[:2].strip().upper().startswith("M")]
        self.assertEqual(modified, [])

    def test_32_points_baseline_unchanged(self):
        import subprocess
        proc = subprocess.run(["git", "status", "--porcelain", "research/run_player_points_model.py",
                                "research/player_points_results.json"],
                               cwd=REPO_ROOT, capture_output=True, text=True)
        if proc.returncode != 0:
            self.skipTest("not a git repository in this environment")
        modified = [l for l in proc.stdout.splitlines() if l[:2].strip().upper().startswith("M")]
        self.assertEqual(modified, [])


# --------------------------------------------------------------------------
# Part 17: parlay-eligibility metadata.
# --------------------------------------------------------------------------
class TestParlayEligibilityMetadata(unittest.TestCase):
    def test_low_confidence_assists_not_parlay_eligible(self):
        self.assertFalse(dp.parlay_eligible("ASSISTS", "LOW"))
        self.assertFalse(dp.parlay_eligible("POINTS", "LOW"))

    def test_medium_high_confidence_still_parlay_eligible(self):
        self.assertTrue(dp.parlay_eligible("ASSISTS", "MEDIUM"))
        self.assertTrue(dp.parlay_eligible("ASSISTS", "HIGH"))

    def test_sog_low_confidence_unaffected(self):
        self.assertTrue(dp.parlay_eligible("SOG", "LOW"))


if __name__ == "__main__":
    unittest.main()
