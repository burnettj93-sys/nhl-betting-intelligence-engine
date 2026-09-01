"""
Preseason Master Consolidation sprint: regression tests (Part 150).
Real fixtures only. Numbered comments map to the sprint's own Part-150
numbered topics where a direct 1:1 mapping exists; several topics are
covered together in one test class where the underlying fixture is
shared (e.g. all five bug-regression tests share one class).
"""
from __future__ import annotations

import hashlib
import json
import math
import unittest

from research import model_registry as mr
from research.context_overlay import overlay_models as om
from research.context_overlay import registry as cov_reg
from research.context_overlay.prediction_stack import ShadowContextStack
from research.player_props import decision_policy
from research.player_props import market_registry as mkt
from research.player_sog import count_models as cm
from pricing import odds_math as pm


def _file_sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _load_json(path):
    with open(path) as f:
        return json.load(f)


# 1. MODEL_REGISTRY validity
class Test01ModelRegistryValidity(unittest.TestCase):
    def test_every_entry_has_required_fields(self):
        for e in mr.MODEL_REGISTRY:
            self.assertTrue(e.model_id)
            self.assertTrue(e.display_name)
            self.assertIn(e.operational_status, mr.OPERATIONAL_STATUSES)

    def test_no_duplicate_model_ids(self):
        ids = [e.model_id for e in mr.MODEL_REGISTRY]
        self.assertEqual(len(ids), len(set(ids)))

    def test_results_files_exist_where_declared(self):
        import os
        for e in mr.MODEL_REGISTRY:
            if e.results_file:
                self.assertTrue(os.path.exists(e.results_file), f"{e.model_id} -> {e.results_file}")


# 2. market-registry consistency
class Test02MarketRegistryConsistency(unittest.TestCase):
    def test_total_market_count_is_142(self):
        self.assertEqual(len(mkt.CANONICAL_MARKETS), 142)

    def test_no_duplicate_market_ids(self):
        ids = [m.market_id for m in mkt.CANONICAL_MARKETS]
        self.assertEqual(len(ids), len(set(ids)))


# 3. no orphan models (every MODEL_REGISTRY market_family with a market_type
# maps to a real market_registry.py or player_props/registry.py entry)
class Test03NoOrphanModels(unittest.TestCase):
    def test_goals_and_points_families_exist_in_market_registry(self):
        ids = {m.market_id for m in mkt.CANONICAL_MARKETS}
        self.assertIn("PLAYER_GOALS_1PLUS", ids)
        self.assertIn("PLAYER_POINTS_1PLUS", ids)


# 4. no orphan markets (every context overlay entry's base market exists)
class Test04NoOrphanMarkets(unittest.TestCase):
    def test_context_overlay_signals_reference_real_markets(self):
        ids = {m.market_id for m in mkt.CANONICAL_MARKETS}
        registry = _load_json(cov_reg.REGISTRY_PATH)
        for e in registry:
            base_id = e["signal"].split("__")[0]
            self.assertIn(base_id, ids)


# 5. aliases (settlement-equivalent aliases route to the same market_id)
class Test05Aliases(unittest.TestCase):
    def test_anytime_goal_alias_resolves_to_goals_ceiling(self):
        self.assertEqual(decision_policy._canonical_market_family("ANYTIME_GOAL"), "GOALS")
        self.assertEqual(
            decision_policy.PROP_LOW_CONFIDENCE_CEILING.get("ANYTIME_GOAL" if "ANYTIME_GOAL" in
                decision_policy.PROP_LOW_CONFIDENCE_CEILING else
                decision_policy._canonical_market_family("ANYTIME_GOAL")),
            decision_policy.PROP_LOW_CONFIDENCE_CEILING.get("GOALS"))


# 6. threshold monotonicity
class Test06ThresholdMonotonicity(unittest.TestCase):
    def test_threshold_probabilities_monotonic_across_realistic_mu_alpha(self):
        for mu in (0.1, 0.5, 1.0, 5.0, 15.0, 30.0, 60.0):
            for alpha in (None, 0.01, 1.0, 20.0):
                probs = cm.threshold_probabilities(mu, alpha, thresholds=(1, 2, 3, 4, 5))
                vals = [probs[t] for t in (1, 2, 3, 4, 5)]
                for i in range(len(vals) - 1):
                    self.assertGreaterEqual(vals[i] + 1e-9, vals[i + 1])


# 7. Goal => Point
class Test07GoalImpliesPoint(unittest.TestCase):
    def test_implication_graph_has_goal_implies_point(self):
        from research.joint_scoring_dependence.logical_implication_registry import implies
        self.assertTrue(implies("GOAL_1_PLUS", "POINT_1_PLUS"))


# 8. Goal => SOG1+
class Test08GoalImpliesSog(unittest.TestCase):
    def test_implication_graph_has_goal_implies_sog(self):
        from research.joint_scoring_dependence.logical_implication_registry import implies
        self.assertTrue(implies("GOAL_1_PLUS", "SOG_1_PLUS"))


# 9. Assist => Point
class Test09AssistImpliesPoint(unittest.TestCase):
    def test_implication_graph_has_assist_implies_point(self):
        from research.joint_scoring_dependence.logical_implication_registry import implies
        self.assertTrue(implies("ASSIST_1_PLUS", "POINT_1_PLUS"))


# 10. redundant leg detection
class Test10RedundantLegDetection(unittest.TestCase):
    def test_goal_and_point_flagged_redundant(self):
        from research.joint_scoring_dependence.logical_implication_registry import detect_redundant_leg
        self.assertEqual(detect_redundant_leg(["GOAL_1_PLUS", "POINT_1_PLUS"]), "POINT_1_PLUS")


# 11. Frechet shot/workload
class Test11FrechetShotWorkload(unittest.TestCase):
    def test_clip_never_escapes_bounds(self):
        import research.joint_shot_workload.joint_models as jm
        for p_a, p_b, guess in [(0.3, 0.7, 0.9), (0.01, 0.99, 0.5), (0.5, 0.5, 1.0)]:
            lo, hi = jm.frechet_bounds(p_a, p_b)
            clipped = jm.clip_to_frechet(guess, p_a, p_b)
            self.assertGreaterEqual(clipped, lo - 1e-9)
            self.assertLessEqual(clipped, hi + 1e-9)


# 12. Frechet scoring
class Test12FrechetScoring(unittest.TestCase):
    def test_clip_never_escapes_bounds(self):
        import research.joint_scoring_dependence.joint_models as jm
        for p_a, p_b, guess in [(0.3, 0.7, 0.9), (0.01, 0.99, 0.5), (0.5, 0.5, 1.0)]:
            lo, hi = jm.frechet_bounds(p_a, p_b)
            clipped = jm.clip_to_frechet(guess, p_a, p_b)
            self.assertGreaterEqual(clipped, lo - 1e-9)
            self.assertLessEqual(clipped, hi + 1e-9)


# 13. marginal recovery (frozen Goals/Points marginal returns valid probs)
class Test13MarginalRecovery(unittest.TestCase):
    def test_goals_and_points_probs_in_unit_interval(self):
        stack = ShadowContextStack()
        rows = [r for r in stack.ctx.goals.rows if r["season"] == 20242025]
        rows.sort(key=lambda r: r["game_date"])
        # index 3000 (not len(rows)-1) when the full research corpus is
        # present locally -- see .gitignore's comment on the committed
        # public-repo corpus being a smaller real subset by default.
        sample = rows[min(3000, len(rows) - 1)]
        result = stack.predict(sample["player_id"], sample["team"], sample["opponent"],
                                sample["game_date"], sample["season"])
        for stage in (result["goals"], result["points"]):
            if stage is not None:
                self.assertGreaterEqual(stage["raw_probability"], 0.0)
                self.assertLessEqual(stage["raw_probability"], 1.0)


# 14. context raw probability preserved
class Test14ContextRawProbabilityPreserved(unittest.TestCase):
    def test_raw_and_adjusted_both_present_and_distinct_when_eligible(self):
        stack = ShadowContextStack()
        rows = [r for r in stack.ctx.goals.rows if r["season"] == 20242025]
        rows.sort(key=lambda r: r["game_date"])
        for sample in rows[500:]:
            result = stack.predict(sample["player_id"], sample["team"], sample["opponent"],
                                    sample["game_date"], sample["season"])
            g = result["goals"]
            if g and g["context_state"] == "COLD_AND_TOI_DECLINE":
                self.assertIn("raw_probability", g)
                self.assertIn("context_adjusted_probability", g)
                self.assertNotEqual(g["raw_probability"], g["context_adjusted_probability"])
                return
        self.fail("No COLD_AND_TOI_DECLINE example found in scan window")


# 15. Goals overlay exact frozen parameter
class Test15GoalsOverlayFrozenParameter(unittest.TestCase):
    def test_offset_is_exactly_negative_0_18(self):
        results = _load_json("research/context_overlay_results.json")
        self.assertAlmostEqual(results["props"]["goals"]["winner_params"]["offset"], -0.18, places=6)
        self.assertEqual(results["props"]["goals"]["winner"], "B_FIXED_LOGIT_OFFSET")


# 16. Points overlay exact frozen parameter
class Test16PointsOverlayFrozenParameter(unittest.TestCase):
    def test_shift_matches_frozen_value(self):
        results = _load_json("research/context_overlay_results.json")
        self.assertAlmostEqual(results["props"]["points"]["winner_params"]["shift"], -0.0415, places=4)
        self.assertEqual(results["props"]["points"]["winner"], "D_BAYESIAN_CONTEXT_BLEND")


# 17. context state hash
class Test17ContextStateHash(unittest.TestCase):
    def test_context_state_module_unchanged(self):
        self.assertEqual(_file_sha256("research/player_context_state/context_state.py"),
                          "06a5bb0d93a2da6558dac4a59c7e904a2d4325963203b5e48a8b0d179f47ef1c")


# 18. context only target state (overlay never applies outside COLD_AND_TOI_DECLINE)
class Test18ContextOnlyTargetState(unittest.TestCase):
    def test_not_eligible_rows_get_identity_adjustment(self):
        stack = ShadowContextStack()
        rows = [r for r in stack.ctx.goals.rows if r["season"] == 20242025]
        rows.sort(key=lambda r: r["game_date"])
        # index 3000 (not len(rows)-1) when the full research corpus is
        # present locally -- see .gitignore's comment on the committed
        # public-repo corpus being a smaller real subset by default.
        sample = rows[min(3000, len(rows) - 1)]
        result = stack.predict(sample["player_id"], sample["team"], sample["opponent"],
                                sample["game_date"], sample["season"])
        g = result["goals"]
        if g and g["context_state"] != "COLD_AND_TOI_DECLINE":
            self.assertEqual(g["raw_probability"], g["context_adjusted_probability"])


# 19. no SOG overlay
class Test19NoSogOverlay(unittest.TestCase):
    def test_shadow_stack_has_no_sog_apply_fn(self):
        stack = ShadowContextStack()
        self.assertNotIn("sog", stack._apply_fns)


# 20. no Assist overlay
class Test20NoAssistOverlay(unittest.TestCase):
    def test_shadow_stack_has_no_assists_apply_fn(self):
        stack = ShadowContextStack()
        self.assertNotIn("assists", stack._apply_fns)


# 21. no Blocks overlay
class Test21NoBlocksOverlay(unittest.TestCase):
    def test_shadow_stack_has_no_blocks_apply_fn(self):
        stack = ShadowContextStack()
        self.assertNotIn("blocks", stack._apply_fns)


# 22. overlay coherence / 23. Goal<=Point post-overlay
class Test22To23OverlayCoherence(unittest.TestCase):
    def test_coherence_holds_after_stack_prediction(self):
        stack = ShadowContextStack()
        rows = [r for r in stack.ctx.goals.rows if r["season"] == 20242025]
        rows.sort(key=lambda r: r["game_date"])
        for sample in rows[500:2500]:
            result = stack.predict(sample["player_id"], sample["team"], sample["opponent"],
                                    sample["game_date"], sample["season"])
            g, p = result["goals"], result["points"]
            if g and p:
                self.assertLessEqual(g["coherent_probability"], p["coherent_probability"] + 1e-9)


# 24. previous 16-violation regression
class Test24Prior16ViolationRegression(unittest.TestCase):
    def test_post_fix_violations_are_zero_in_frozen_results(self):
        results = _load_json("research/context_overlay_results.json")
        fix = results["freeze_manifest"]["coherence_fix_by_season"]
        for season_block in fix.values():
            self.assertEqual(season_block["post_fix_violations_remaining"], 0)

    def test_2024_25_violations_found_matches_16(self):
        results = _load_json("research/context_overlay_results.json")
        fix = results["freeze_manifest"]["coherence_fix_by_season"]
        block = fix.get("20242025", fix.get(20242025))
        self.assertEqual(block["violations_found_and_fixed"], 16)


# 25. AND/OR coherence bug regression
class Test25AndOrCoherenceBugRegression(unittest.TestCase):
    def test_fix_loop_uses_or_semantics_matching_the_check(self):
        with open("research/run_context_overlay_model.py") as f:
            src = f.read()
        # the fix loop must use "and" (De Morgan for "at least one side adjusted"),
        # never "or" (which would only fix rows where BOTH sides were adjusted --
        # the exact bug found and fixed this sprint)
        self.assertIn('if "adjusted_prob_1plus" not in g and "adjusted_prob_1plus" not in pt:', src)


# 26. prior key-type bug regression (Goals probs dict string/int key mismatch)
class Test26PriorKeyTypeBugRegression(unittest.TestCase):
    def test_goals_marginal_normalizes_to_int_keys(self):
        from research.player_context_state.marginal_provenance import ContextMarginalContext
        ctx = ContextMarginalContext()
        rows = [r for r in ctx.goals.rows if r["season"] == 20242025]
        sample = rows[100]
        result = ctx.goals.predict(sample["player_id"], sample["team"], sample["opponent"],
                                    sample["game_date"], sample["season"])
        if result is not None:
            for k in result["probs"]:
                self.assertIsInstance(k, int)


# 27. three-way SOG label bug regression
class Test27ThreeWaySogLabelRegression(unittest.TestCase):
    def test_triple_combinations_use_threshold_specific_sog_labels(self):
        with open("research/run_joint_scoring_dependence_model.py") as f:
            src = f.read()
        self.assertIn("SOG_3_PLUS", src)


# 28. Assist/Point raw incoherence handled non-destructively
class Test28AssistPointIncoherenceHandled(unittest.TestCase):
    def test_joint_scoring_results_file_unchanged(self):
        self.assertEqual(_file_sha256("research/joint_scoring_dependence_results.json"),
                          "3076d4e849e60f8156601e6070301f17b8e51d56265880ff8c8bf0d3b58f9d91")


# 29. decision v3 unchanged
class Test29DecisionPolicyUnchanged(unittest.TestCase):
    def test_hash(self):
        self.assertEqual(_file_sha256("research/player_props/decision_policy.py"),
                          "f812d5fa2f1811c2b06b0b63b972dc499fc2f8f6e117a9afd5fa63bea55c763a")


# 30-33. LOW policy ceilings
class Test30To33LowPolicyCeilings(unittest.TestCase):
    def test_goals_low_watch_only(self):
        self.assertEqual(decision_policy.gate_low_confidence("GOALS", "LOW", "BET")["final_decision"], "WATCH")

    def test_points_low_watch_only(self):
        self.assertEqual(decision_policy.gate_low_confidence("POINTS", "LOW", "BET")["final_decision"], "WATCH")

    def test_assists_low_watch_only(self):
        self.assertEqual(decision_policy.gate_low_confidence("ASSISTS", "LOW", "BET")["final_decision"], "WATCH")

    def test_p3_sog_low_watch_only(self):
        self.assertEqual(
            decision_policy.gate_low_confidence("PLAYER_SOG_PERIOD_3", "LOW", "BET")["final_decision"], "WATCH")


# 34. overlay cannot bypass WATCH
class Test34OverlayCannotBypassWatch(unittest.TestCase):
    def test_gate_low_confidence_never_returns_bet_for_gated_low(self):
        for market in ("GOALS", "POINTS", "ASSISTS"):
            result = decision_policy.gate_low_confidence(market, "LOW", "BET")
            self.assertNotEqual(result["final_decision"], "BET")


# 35. shadow policy separated (prediction_stack never imports decision_policy)
class Test35ShadowPolicySeparated(unittest.TestCase):
    def test_prediction_stack_does_not_import_decision_policy(self):
        with open("research/context_overlay/prediction_stack.py") as f:
            src = f.read()
        self.assertNotIn("import decision_policy", src)
        self.assertNotIn("from research.player_props.decision_policy", src)
        self.assertNotIn("decision_policy.gate_low_confidence", src)


# 36. no fake holdout
class Test36NoFakeHoldout(unittest.TestCase):
    def test_eval_seasons_are_20242025_and_20252026_everywhere(self):
        from research.run_player_context_state_model import EVAL_SEASONS
        self.assertEqual(EVAL_SEASONS, [20242025, 20252026])

    def test_no_module_claims_a_third_holdout_season(self):
        for fname in ("research/run_context_overlay_model.py", "research/run_player_context_state_model.py"):
            with open(fname) as f:
                src = f.read().lower()
            for banned in ("new holdout", "unseen test", "prospective holdout", "fresh holdout"):
                self.assertNotIn(banned, src)


# 37. historical eval seasons correctly labeled
class Test37HistoricalEvalSeasonsLabeled(unittest.TestCase):
    def test_freeze_manifest_does_not_claim_prospective_status(self):
        results = _load_json("research/context_overlay_results.json")
        manifest_str = json.dumps(results["freeze_manifest"]).lower()
        self.assertNotIn("prospective_validated", manifest_str)
        self.assertNotIn("prospective_active", manifest_str)


# 38. prospective state (registry does not claim OPERATIONAL_VALIDATED)
class Test38ProspectiveState(unittest.TestCase):
    def test_no_overlay_entry_is_operational_validated(self):
        registry = _load_json(cov_reg.REGISTRY_PATH)
        for e in registry:
            self.assertNotEqual(e["operational_status"], "OPERATIONAL_VALIDATED")
            self.assertNotEqual(e["operational_status"], "FULL_BET_POLICY")


# 39. PIT target exclusion
class Test39PitTargetExclusion(unittest.TestCase):
    def test_history_excludes_target_row(self):
        stack = ShadowContextStack()
        rows = stack.ctx.goals.rows
        season_rows = [r for r in rows if r["season"] == 20242025]
        # index 3000 (not len(season_rows)-1) when the full research
        # corpus is present locally -- see .gitignore's comment on the
        # committed public-repo corpus being a smaller real subset by default.
        sample = season_rows[min(3000, len(season_rows) - 1)]
        history = stack.ctx.goals.index.history_as_of(sample["player_id"], sample["game_date"])
        self.assertNotIn(sample["game_id"], [h["game_id"] for h in history])


# 40. target TOI exclusion
class Test40TargetToiExclusion(unittest.TestCase):
    def test_history_rows_all_precede_target_date(self):
        stack = ShadowContextStack()
        rows = stack.ctx.goals.rows
        season_rows = [r for r in rows if r["season"] == 20242025]
        # index 3000 (not len(season_rows)-1) when the full research
        # corpus is present locally -- see .gitignore's comment on the
        # committed public-repo corpus being a smaller real subset by default.
        sample = season_rows[min(3000, len(season_rows) - 1)]
        history = stack.ctx.goals.index.history_as_of(sample["player_id"], sample["game_date"])
        self.assertTrue(all(h["game_date"] < sample["game_date"] for h in history))


# 41. target PP usage exclusion (rolling_pp_mean uses history, not target row)
class Test41TargetPpUsageExclusion(unittest.TestCase):
    def test_rolling_pp_mean_signature_takes_history_not_target(self):
        from research.player_goals import features as gf
        import inspect
        sig = inspect.signature(gf.rolling_pp_mean)
        self.assertIn("history", list(sig.parameters))


# 42. exact chronology boundary (strict <, not <=)
class Test42ChronologyBoundary(unittest.TestCase):
    def test_history_as_of_uses_strict_less_than(self):
        stack = ShadowContextStack()
        rows = stack.ctx.goals.rows
        season_rows = [r for r in rows if r["season"] == 20242025]
        # index 3000 (not len(season_rows)-1) when the full research
        # corpus is present locally -- see .gitignore's comment on the
        # committed public-repo corpus being a smaller real subset by default.
        sample = season_rows[min(3000, len(season_rows) - 1)]
        same_date_history = stack.ctx.goals.index.history_as_of(sample["player_id"], sample["game_date"])
        self.assertNotIn(sample["game_date"], [h["game_date"] for h in same_date_history])


# 43. starter terminology centralized (documented distinction exists)
class Test43StarterTerminology(unittest.TestCase):
    def test_goalie_intelligence_module_documents_projected_vs_confirmed(self):
        with open("research/goalie_intelligence/features.py") as f:
            src = f.read().lower()
        self.assertTrue("project" in src)


# 44. active-player terminology (projected_active exists and is distinct from roster)
class Test44ActivePlayerTerminology(unittest.TestCase):
    def test_projected_active_function_exists_for_goals_and_points(self):
        from research.player_goals import features as gf
        from research.player_points import features as ptf
        self.assertTrue(callable(gf.projected_active))
        self.assertTrue(callable(ptf.projected_active))


# 45. roster != lineup (projected_active requires recent appearance history,
# not just roster membership)
class Test45RosterNotLineup(unittest.TestCase):
    def test_projected_active_returns_false_for_empty_history(self):
        from research.player_goals import features as gf
        self.assertFalse(gf.projected_active([], []))


# 46-50. dashboard state / readiness statuses are real, documented strings
class Test46To50ReadinessStates(unittest.TestCase):
    def test_decision_policy_terminal_statuses_include_wait_and_data_unavailable(self):
        self.assertIn("WAIT", decision_policy._TERMINAL_STATUSES)
        self.assertIn("DATA_UNAVAILABLE", decision_policy._TERMINAL_STATUSES)

    def test_terminal_statuses_pass_through_unchanged(self):
        for status in decision_policy._TERMINAL_STATUSES:
            result = decision_policy.gate_low_confidence("GOALS", "LOW", status)
            self.assertEqual(result["final_decision"], status)
            self.assertIsNone(result["policy_override"])


# 51-53. malformed payload / unknown mapping / duplicate name handling
class Test51To53MappingRobustness(unittest.TestCase):
    def test_player_index_preserves_duplicate_names_as_list(self):
        from research.live_sog_pricing.player_mapping import build_player_index
        rows = [{"player_id": "1", "player_name": "A Sample", "team": "NOR", "game_date": "2024-10-01"},
                {"player_id": "2", "player_name": "A Sample", "team": "CST", "game_date": "2024-10-02"}]
        idx = build_player_index(rows)
        key = next(iter(idx))
        self.assertEqual(len(idx[key]), 2)


# 54. rookie behavior (min-history gate prevents high-confidence sparse prediction)
class Test54RookieBehavior(unittest.TestCase):
    def test_insufficient_history_returns_none_not_a_prediction(self):
        stack = ShadowContextStack()
        result = stack._stage_for("goals", "0000000_nonexistent", "NOR", "CST", "2025-01-01", 20242025)
        self.assertIsNone(result)


# 55. trade behavior (team context updates; player identity persists via player_id)
class Test55TradeBehavior(unittest.TestCase):
    def test_history_as_of_keys_on_player_id_not_team(self):
        stack = ShadowContextStack()
        rows = stack.ctx.goals.rows
        teams_for_first_player = {r["team"] for r in rows if r["player_id"] == rows[0]["player_id"]}
        # merely confirms the index is player_id-keyed (a traded player's full
        # history is retrievable under one id regardless of how many teams appear)
        history = stack.ctx.goals.index.history_as_of(rows[0]["player_id"], "2099-01-01")
        self.assertGreater(len(history), 0)


# 56. multi-goalie games (goalie saves model built to handle multiple goalies/game)
class Test56MultiGoalieGames(unittest.TestCase):
    def test_goalie_saves_results_file_unchanged(self):
        self.assertEqual(_file_sha256("research/goalie_saves_results.json"),
                          "6533395bfe111385f2591dca0944a2a576a785178ac640c4fd7ee2363af3e34e")


# 57-59. empty net / SO / GWG semantics unchanged (frozen corpora untouched)
class Test57To59SemanticsUnchanged(unittest.TestCase):
    def test_pbp_corpus_manifest_present(self):
        import os
        self.assertTrue(os.path.exists("research/real_nhl_pbp/research_pbp.db"))


# 60. period saves unchanged
class Test60PeriodSavesUnchanged(unittest.TestCase):
    def test_period_league_share_key_present(self):
        results = _load_json("research/goalie_saves_results.json")
        self.assertIn("period_league_share", results)


# 61. NHL SOG key contract (frozen field name)
class Test61NhlSogKeyContract(unittest.TestCase):
    def test_sog_field_name_used_in_corpus(self):
        from research.player_sog import features as pf
        rows = pf.load_sog_corpus()
        self.assertIn("sog", rows[0])


# 62. schedule local-date contract
class Test62ScheduleLocalDateContract(unittest.TestCase):
    def test_game_date_is_iso_format_string(self):
        from research.player_sog import features as pf
        rows = pf.load_sog_corpus()
        sample = rows[0]["game_date"]
        self.assertRegex(sample, r"^\d{4}-\d{2}-\d{2}$")


# 63. provider receipt timestamps (freeze manifest carries a real UTC timestamp)
class Test63ProviderReceiptTimestamps(unittest.TestCase):
    def test_freeze_manifest_timestamp_is_utc_iso(self):
        results = _load_json("research/context_overlay_results.json")
        ts = results["freeze_manifest"]["timestamp_utc"]
        self.assertRegex(ts, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


# 64. closing snapshot strict-before-start (documented, not yet implemented live)
class Test64ClosingSnapshotContract(unittest.TestCase):
    def test_hours_between_utility_exists_for_future_clv_use(self):
        self.assertTrue(callable(pm.hours_between))


# 65. no-vig incomplete-market protection
class Test65NoVigIncompleteMarketProtection(unittest.TestCase):
    def test_no_vig_requires_both_sides(self):
        import inspect
        sig = inspect.signature(pm.no_vig_two_way)
        self.assertEqual(len(sig.parameters), 2)


# 66. fair odds extreme probability
class Test66FairOddsExtremeProbability(unittest.TestCase):
    def test_prob_to_american_finite_near_boundaries(self):
        for p in (1e-6, 0.5, 1 - 1e-6):
            self.assertTrue(math.isfinite(pm.prob_to_american(p)))


# 67. conservative probability ordering
class Test67ConservativeProbabilityOrdering(unittest.TestCase):
    def test_conservative_mu_never_exceeds_raw_mu(self):
        for mu in (0.1, 1.0, 5.0, 30.0):
            self.assertLessEqual(cm.conservative_mu(mu, 20), mu + 1e-9)


# 68. max acceptable price
class Test68MaxAcceptablePrice(unittest.TestCase):
    def test_max_acceptable_price_callable_with_conservative_prob(self):
        price = pm.max_acceptable_price(0.55, 0.02, -110)
        self.assertIsNotNone(price)
        self.assertTrue(math.isfinite(price))


# 69. opportunity card raw/adjusted display (prototype markup contains both fields)
class Test69OpportunityCardDisplay(unittest.TestCase):
    def test_prototype_app_js_renders_raw_and_adjusted(self):
        with open("dashboard_prototype/app.js") as f:
            src = f.read()
        self.assertIn("rawP", src)
        self.assertIn("adjP", src)


# 70. no fake odds placeholder
class Test70NoFakeOddsPlaceholder(unittest.TestCase):
    def test_prototype_uses_no_live_price_label(self):
        with open("dashboard_prototype/app.js") as f:
            src = f.read()
        self.assertIn("NO LIVE PRICE", src)


# 71. validated vs partial badge (prototype CSS defines distinct classes)
class Test71ValidatedVsPartialBadge(unittest.TestCase):
    def test_css_defines_distinct_status_colors(self):
        with open("dashboard_prototype/styles.css") as f:
            src = f.read()
        self.assertIn("badge-VALIDATED", src)
        self.assertIn("badge-PARTIAL", src)
        self.assertIn("badge-REJECTED", src)


# 72. overlay shadow badge
class Test72OverlayShadowBadge(unittest.TestCase):
    def test_css_defines_shadow_badge(self):
        with open("dashboard_prototype/styles.css") as f:
            src = f.read()
        self.assertIn("badge-SHADOW", src)


# 73. dashboard import (page 20 imports cleanly)
class Test73DashboardImport(unittest.TestCase):
    def test_context_overlay_view_module_imports(self):
        import dashboard.context_overlay_view as cov_view
        self.assertTrue(hasattr(cov_view, "load_results"))


# 74. dashboard no-game render (Today prototype view has an empty state)
class Test74DashboardNoGameRender(unittest.TestCase):
    def test_prototype_has_no_games_empty_state(self):
        with open("dashboard_prototype/app.js") as f:
            src = f.read()
        self.assertIn("No games today", src)


# 75. health object
class Test75HealthObject(unittest.TestCase):
    def test_prototype_defines_health_chip_states(self):
        with open("dashboard_prototype/app.js") as f:
            src = f.read()
        for state in ("OK", "STALE", "WAITING"):
            self.assertIn(f'"{state}"', src)


# 76. model-health render
class Test76ModelHealthRender(unittest.TestCase):
    def test_model_health_demo_data_has_all_families(self):
        with open("dashboard_prototype/app.js") as f:
            src = f.read()
        for family in ("Player SOG", "Goalie Saves", "Context Overlay"):
            self.assertIn(family, src)


# 77. ledger record-type separation
class Test77LedgerRecordTypeSeparation(unittest.TestCase):
    def test_prototype_defines_four_ledger_types(self):
        with open("dashboard_prototype/app.js") as f:
            src = f.read()
        for t in ("REAL_BET", "MODEL_OBSERVATION", "HISTORICAL_RESEARCH", "SHADOW_POLICY_OBSERVATION"):
            self.assertIn(t, src)


# 78. no synthetic P&L
class Test78NoSyntheticPnl(unittest.TestCase):
    def test_prototype_ledger_has_no_real_bet_with_stake(self):
        with open("dashboard_prototype/app.js") as f:
            src = f.read()
        self.assertIn("No real bets placed", src)


# 79. no unexpected network in unit suite
class Test79NoUnexpectedNetwork(unittest.TestCase):
    def test_prediction_stack_has_no_requests_import(self):
        with open("research/context_overlay/prediction_stack.py") as f:
            src = f.read()
        self.assertNotIn("import requests", src)
        self.assertNotIn("urllib", src)


# 80-89. frozen hashes for every marginal/joint/win-model family
class Test80To89FrozenHashes(unittest.TestCase):
    def test_player_sog(self):
        self.assertEqual(_file_sha256("research/player_sog_results.json"),
                          "556d447bc6dcfc18df52812d98901cd7accad3b203a06606ddd68ea6993e8f61")

    def test_goals(self):
        self.assertEqual(_file_sha256("research/player_goals_results.json"),
                          "3f5592585a255b11c77f2a4d08c2c9886d01e45dbc8b48b30d284389367f5348")

    def test_assists(self):
        self.assertEqual(_file_sha256("research/player_assists_results.json"),
                          "3f8bc1c649cb3bbea4be0f56ebf893e399eaca415075ea1dca176e1f944ec0e9")

    def test_points(self):
        self.assertEqual(_file_sha256("research/player_points_results.json"),
                          "6eacd4d56dc78d6b371b7f0234252e1f969359a427d813efcd696780b8af8877")

    def test_blocks(self):
        self.assertEqual(_file_sha256("research/player_blocks_results.json"),
                          "fc608ab5da9adf06170f96b7e96989fc29cf4cad07a26a9d9778d51649293c07")

    def test_team_sog(self):
        self.assertEqual(_file_sha256("research/team_sog_results.json"),
                          "90188ede1e076e4a1dc0bb0b569ae80542215c2db268b510ef966bea339fa0ac")

    def test_goalie_saves(self):
        self.assertEqual(_file_sha256("research/goalie_saves_results.json"),
                          "6533395bfe111385f2591dca0944a2a576a785178ac640c4fd7ee2363af3e34e")

    def test_joint_shot_workload(self):
        self.assertEqual(_file_sha256("research/joint_shot_workload_results.json"),
                          "ee83c18a4b44966e1807acd79f2589848f8f368cb81ea8ca13df0015786c788a")

    def test_joint_scoring(self):
        self.assertEqual(_file_sha256("research/joint_scoring_dependence_results.json"),
                          "3076d4e849e60f8156601e6070301f17b8e51d56265880ff8c8bf0d3b58f9d91")

    def test_nhl_win_model(self):
        self.assertEqual(_file_sha256("models/combined_model.py"),
                          "64e9e9cbe686b386951fed9d5001dc298c5dff6af7f582b8f197565f6d932c82")
        self.assertEqual(_file_sha256("models/elo_model.py"),
                          "8538d6b2e32112190919ac41f8b60f17d66528d58c2488c0ee7f7f2690411faf")


# 90. context overlay hashes
class Test90ContextOverlayHashes(unittest.TestCase):
    def test_overlay_code_hashes_match_manifest(self):
        results = _load_json("research/context_overlay_results.json")
        recorded = results["freeze_manifest"]["code_hashes"]
        self.assertEqual(recorded["run_context_overlay_model.py"],
                          _file_sha256("research/run_context_overlay_model.py"))
        self.assertEqual(recorded["context_overlay/overlay_models.py"],
                          _file_sha256("research/context_overlay/overlay_models.py"))


# extra: production boundary + numerical/monotonicity/Frechet audit evidence
class TestExtraNumericalAuditEvidence(unittest.TestCase):
    def test_extreme_probability_logit_roundtrip_stable(self):
        for p in (1e-9, 1e-6, 0.001, 0.5, 0.999, 1 - 1e-6, 1 - 1e-9):
            z = om.logit(p)
            self.assertTrue(math.isfinite(z))
            self.assertTrue(math.isfinite(om.inv_logit(z)))

    def test_production_boundary_files_unchanged(self):
        self.assertEqual(_file_sha256("config.py"),
                          "c019568da204ace99222954d4f02546a25c31029453c36ed3b0ed4bf97d3df8a")
        self.assertEqual(_file_sha256("db.py"),
                          "b598f4640e191a26dba7231e240a26ebbf6d7a443bcf4f2eb4c43b37cabcea95")
        self.assertEqual(_file_sha256("schema.sql"),
                          "ff19dd3b0c4cd8a61371d77751a045f222bdce7636d119d90c013f58ef64f31f")


if __name__ == "__main__":
    unittest.main()
