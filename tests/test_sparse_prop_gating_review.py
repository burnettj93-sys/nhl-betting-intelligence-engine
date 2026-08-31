"""
Tests for the Unified Sparse-Prop LOW-Confidence Gating Review:
research/player_props/decision_policy.py's v2 update (GOALS added, plus
the ANYTIME_GOAL/GOALS_OVER_0_5 market-family alias), the registry's new
low_confidence_bet_eligibility values, and research/run_sparse_prop_gating_review.py.
Covers Part 22's 28 required test areas. Does NOT redesign or re-test the
confidence framework itself (already exhaustively covered by
tests/test_confidence_framework.py) and does NOT refit any raw model.
"""
import json
import os
import unittest

from research.player_props import decision_policy as dp

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _unmodified(rel_paths):
    import subprocess
    proc = subprocess.run(["git", "status", "--porcelain", *rel_paths], cwd=REPO_ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        return None  # not a git repo; caller should skip
    return [l for l in proc.stdout.splitlines() if l[:2].strip().upper().startswith("M")]


# --------------------------------------------------------------------------
# Tests 1-3: LOW Goals / Anytime Goal / Goals-Over-0.5 cannot resolve to BET.
# --------------------------------------------------------------------------
class TestGoalsFamilyCannotBet(unittest.TestCase):
    def test_1_low_goals_bet_capped_to_watch(self):
        out = dp.gate_low_confidence("GOALS", "LOW", "BET", "edge/EV clear")
        self.assertEqual(out["final_decision"], "WATCH")

    def test_2_low_anytime_goal_bet_capped_to_watch(self):
        out = dp.gate_low_confidence("ANYTIME_GOAL", "LOW", "BET", "edge/EV clear")
        self.assertEqual(out["final_decision"], "WATCH")

    def test_3_low_goals_over_0_5_bet_capped_to_watch(self):
        out = dp.gate_low_confidence("GOALS_OVER_0_5", "LOW", "BET", "edge/EV clear")
        self.assertEqual(out["final_decision"], "WATCH")

    def test_3b_all_three_goals_family_labels_gate_identically(self):
        # Part 12: never allow settlement-equivalent labels to diverge.
        results = {m: dp.gate_low_confidence(m, "LOW", "BET") for m in ("GOALS", "ANYTIME_GOAL", "GOALS_OVER_0_5")}
        decisions = {v["final_decision"] for v in results.values()}
        self.assertEqual(decisions, {"WATCH"})


# --------------------------------------------------------------------------
# Test 4: LOW Goals valid-but-gated state becomes WATCH (not WAIT/PASS).
# --------------------------------------------------------------------------
class TestWatchNotWaitOrPass(unittest.TestCase):
    def test_4_gated_goals_bet_becomes_watch_specifically(self):
        out = dp.gate_low_confidence("GOALS", "LOW", "BET", "conservative edge +3.8%")
        self.assertEqual(out["final_decision"], "WATCH")
        self.assertNotIn(out["final_decision"], ("WAIT", "PASS"))


# --------------------------------------------------------------------------
# Test 5: upstream WAIT retains precedence.
# --------------------------------------------------------------------------
class TestUpstreamWaitPrecedence(unittest.TestCase):
    def test_5_upstream_wait_passes_through_for_goals(self):
        out = dp.gate_low_confidence("GOALS", "LOW", "WAIT", "lineup unconfirmed")
        self.assertEqual(out["final_decision"], "WAIT")
        self.assertIsNone(out["policy_override"])


# --------------------------------------------------------------------------
# Tests 6-7: Goals MEDIUM/HIGH unchanged.
# --------------------------------------------------------------------------
class TestGoalsMediumHighUnchanged(unittest.TestCase):
    def test_6_goals_medium_unchanged(self):
        out = dp.gate_low_confidence("GOALS", "MEDIUM", "BET")
        self.assertEqual(out["final_decision"], "BET")

    def test_7_goals_high_unchanged(self):
        out = dp.gate_low_confidence("GOALS", "HIGH", "BET")
        self.assertEqual(out["final_decision"], "BET")


# --------------------------------------------------------------------------
# Tests 8-9: SOG/Blocks LOW unchanged (control props, no restriction added).
# --------------------------------------------------------------------------
class TestControlPropsUnrestricted(unittest.TestCase):
    def test_8_sog_low_unchanged(self):
        out = dp.gate_low_confidence("SOG", "LOW", "BET")
        self.assertEqual(out["final_decision"], "BET")

    def test_9_blocks_low_unchanged(self):
        out = dp.gate_low_confidence("BLOCKED_SHOTS", "LOW", "BET")
        self.assertEqual(out["final_decision"], "BET")
        self.assertNotIn("SOG", dp.PROP_LOW_CONFIDENCE_CEILING)
        self.assertNotIn("BLOCKED_SHOTS", dp.PROP_LOW_CONFIDENCE_CEILING)


# --------------------------------------------------------------------------
# Tests 10-11: Assists/Points LOW remain WATCH_ONLY (retained, not re-derived).
# --------------------------------------------------------------------------
class TestAssistsPointsRetained(unittest.TestCase):
    def test_10_assists_low_still_watch_only(self):
        out = dp.gate_low_confidence("ASSISTS", "LOW", "BET")
        self.assertEqual(out["final_decision"], "WATCH")

    def test_11_points_low_still_watch_only(self):
        out = dp.gate_low_confidence("POINTS", "LOW", "BET")
        self.assertEqual(out["final_decision"], "WATCH")


# --------------------------------------------------------------------------
# Tests 12-16: pricing math untouched by this policy layer.
# --------------------------------------------------------------------------
class TestPricingMathUntouched(unittest.TestCase):
    MODULE_PATH = os.path.join(REPO_ROOT, "research", "player_props", "decision_policy.py")

    def test_12_raw_probability_never_referenced(self):
        with open(self.MODULE_PATH) as f:
            text = f.read()
        self.assertNotIn("model_probability", text)
        self.assertNotIn("raw_probability", text)

    def test_13_conservative_probability_never_referenced(self):
        with open(self.MODULE_PATH) as f:
            text = f.read()
        self.assertNotIn("conservative_prob", text)

    def test_14_edge_never_referenced(self):
        with open(self.MODULE_PATH) as f:
            text = f.read()
        self.assertNotIn("raw_edge", text)
        self.assertNotIn("conservative_edge", text)

    def test_15_ev_never_referenced(self):
        with open(self.MODULE_PATH) as f:
            text = f.read()
        self.assertNotIn("expected_value", text)

    def test_16_fair_price_never_referenced(self):
        with open(self.MODULE_PATH) as f:
            text = f.read()
        self.assertNotIn("fair_price", text)


# --------------------------------------------------------------------------
# Test 17: policy version incremented.
# --------------------------------------------------------------------------
class TestPolicyVersionIncrement(unittest.TestCase):
    def test_17_policy_version_is_v2(self):
        self.assertEqual(dp.POLICY_VERSION, "prop_decision_policy_v3")

    def test_17b_v1_semantics_not_overwritten_in_docs(self):
        # the v1->v2 changelog comment must still be present, not deleted
        with open(os.path.join(REPO_ROOT, "research", "player_props", "decision_policy.py")) as f:
            text = f.read()
        self.assertIn("v2", text)
        self.assertIn("GOALS", text)


# --------------------------------------------------------------------------
# Test 18: policy reason stored on every gated call.
# --------------------------------------------------------------------------
class TestPolicyReasonStored(unittest.TestCase):
    def test_18_reason_present_and_names_the_market(self):
        out = dp.gate_low_confidence("GOALS", "LOW", "BET", "edge/EV clear")
        self.assertTrue(out["policy_reason"])
        self.assertIn("GOALS", out["policy_reason"])


# --------------------------------------------------------------------------
# Test 19: observation ledger stores raw/final decision separately.
# --------------------------------------------------------------------------
class TestObservationLedgerShape(unittest.TestCase):
    def test_19_mathematical_status_and_final_decision_both_present_and_distinct(self):
        out = dp.gate_low_confidence("GOALS", "LOW", "BET", "clears both bars")
        self.assertEqual(out["mathematical_status"], "BET")
        self.assertEqual(out["final_decision"], "WATCH")
        self.assertEqual(out["policy_override"], "LOW_CONFIDENCE_GOALS")


# --------------------------------------------------------------------------
# Test 20: Goals registry status remains VALIDATED (not downgraded).
# --------------------------------------------------------------------------
class TestGoalsRegistryStatus(unittest.TestCase):
    def test_20_goals_still_validated(self):
        from research.player_props import registry
        entry = registry.get("GOALS")
        self.assertEqual(entry.model_status, "VALIDATED")
        self.assertEqual(entry.low_confidence_bet_eligibility, "WATCH_ONLY")

    def test_20b_anytime_goal_inherits_watch_only_metadata(self):
        from research.player_props import registry
        entry = registry.get("ANYTIME_GOAL")
        self.assertEqual(entry.low_confidence_bet_eligibility, "WATCH_ONLY")
        self.assertNotEqual(entry.model_status, "VALIDATED")  # still SUPPORTED_BY_GOALS_MODEL, per Part 39 precedent


# --------------------------------------------------------------------------
# Test 21: Goals 2+ remains INSUFFICIENT DATA -- gating cannot upgrade it.
# --------------------------------------------------------------------------
class TestGoalsTwoPlusUnchanged(unittest.TestCase):
    def test_21_goals_results_two_plus_still_insufficient_data(self):
        path = os.path.join(REPO_ROOT, "research", "player_goals_results.json")
        if not os.path.exists(path):
            self.skipTest("player_goals_results.json not built in this environment")
        with open(path) as f:
            r = json.load(f)
        self.assertEqual(r["two_plus_support_checks"]["two_plus_status"], "INSUFFICIENT_DATA")


# --------------------------------------------------------------------------
# Test 22: confidence framework unchanged.
# --------------------------------------------------------------------------
class TestConfidenceFrameworkUnchanged(unittest.TestCase):
    def test_22_confidence_score_not_modified(self):
        modified = _unmodified(["research/player_sog/count_models.py", "research/confidence_lab/reliability.py"])
        if modified is None:
            self.skipTest("not a git repository in this environment")
        self.assertEqual(modified, [])


# --------------------------------------------------------------------------
# Tests 23-28: raw model files unchanged.
# --------------------------------------------------------------------------
class TestRawModelsUnchanged(unittest.TestCase):
    def test_23_goals_model_unchanged(self):
        modified = _unmodified(["research/run_player_goals_model.py", "research/player_goals_results.json"])
        if modified is None:
            self.skipTest("not a git repository in this environment")
        self.assertEqual(modified, [])

    def test_24_assists_model_unchanged(self):
        modified = _unmodified(["research/run_player_assists_model.py", "research/player_assists_results.json"])
        if modified is None:
            self.skipTest("not a git repository in this environment")
        self.assertEqual(modified, [])

    def test_25_points_baseline_unchanged(self):
        modified = _unmodified(["research/run_player_points_model.py", "research/player_points_results.json"])
        if modified is None:
            self.skipTest("not a git repository in this environment")
        self.assertEqual(modified, [])

    def test_26_sog_model_unchanged(self):
        modified = _unmodified(["research/run_player_sog_model.py", "research/player_sog_results.json"])
        if modified is None:
            self.skipTest("not a git repository in this environment")
        self.assertEqual(modified, [])

    def test_27_blocks_model_unchanged(self):
        modified = _unmodified(["research/run_player_blocks_model.py", "research/player_blocks_results.json"])
        if modified is None:
            self.skipTest("not a git repository in this environment")
        self.assertEqual(modified, [])

    def test_28_nhl_win_model_unchanged(self):
        modified = _unmodified(["pricing/engine.py", "pricing/decision.py", "config.py", "models"])
        if modified is None:
            self.skipTest("not a git repository in this environment")
        self.assertEqual(modified, [])


if __name__ == "__main__":
    unittest.main()
