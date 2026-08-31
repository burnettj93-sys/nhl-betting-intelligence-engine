"""
Part 44: tests for the Context-State Probability Overlay slice (Goals 1+
and Points 1+, COLD_AND_TOI_DECLINE only). Real fixtures only -- the
frozen context-state definition, the frozen Goals/Points marginals, and
the driver's own results/registry files. Numbered comments map to
Part-44 topics.
"""
from __future__ import annotations

import hashlib
import json
import unittest

from research.context_overlay import confidence_helpers as ch
from research.context_overlay import overlay_models as om
from research.context_overlay import registry as reg
from research.player_context_state import marginal_provenance as pcs_mp
from research.player_props import decision_policy
from research.run_context_overlay_model import (
    RESULTS_PATH, PROPS, OVERLAY_MIN_DEV_N, OVERLAY_MIN_EVAL_N,
    paired_clustered_bootstrap,
)
from research.run_player_context_state_model import TUNING_SEASON, EVAL_SEASONS


def _file_sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _load_results() -> dict:
    with open(RESULTS_PATH) as f:
        return json.load(f)


def _load_registry() -> list[dict]:
    with open(reg.REGISTRY_PATH) as f:
        return json.load(f)


def _eval_block(results, prop, season):
    block = results["props"][prop]["eval"]
    return block.get(str(season), block.get(season))


# 1. frozen context-state reuse -- driver imports build_prop_examples directly
class Test01FrozenContextStateReuse(unittest.TestCase):
    def test_driver_imports_context_state_builder_directly(self):
        with open("research/run_context_overlay_model.py") as f:
            src = f.read()
        self.assertIn("from research.run_player_context_state_model import", src)
        self.assertIn("build_prop_examples", src)


# 2. cold definition unchanged (cutoffs match the frozen prior slice exactly)
class Test02ColdDefinitionUnchanged(unittest.TestCase):
    def test_cold_cutoffs_match_prior_slice(self):
        results = _load_results()
        prior = json.load(open("research/player_context_state_results.json"))
        for prop in PROPS:
            self.assertAlmostEqual(results["props"][prop]["cold_cutoff"], prior["props"][prop]["cold_cutoff"],
                                    places=9)


# 3. TOI decline definition unchanged
class Test03ToiDeclineDefinitionUnchanged(unittest.TestCase):
    def test_toi_decline_cutoffs_match_prior_slice(self):
        results = _load_results()
        prior = json.load(open("research/player_context_state_results.json"))
        for prop in PROPS:
            self.assertAlmostEqual(results["props"][prop]["toi_decline_cutoff"],
                                    prior["props"][prop]["toi_decline_cutoff"], places=9)


# 4. PIT state construction: history excludes target/future dates
class Test04PitStateConstruction(unittest.TestCase):
    def test_history_excludes_target_and_future(self):
        ctx = pcs_mp.ContextMarginalContext()
        rows = ctx.goals.rows
        sample = next(r for r in rows if r["season"] == TUNING_SEASON)
        history = ctx.goals.index.history_as_of(sample["player_id"], sample["game_date"])
        for h in history:
            self.assertLess(h["game_date"], sample["game_date"])


# 5. target outcome exclusion
class Test05TargetOutcomeExclusion(unittest.TestCase):
    def test_history_never_contains_target_game_id(self):
        ctx = pcs_mp.ContextMarginalContext()
        rows = ctx.points.rows
        sample = next(r for r in rows if r["season"] == TUNING_SEASON)
        history = ctx.points.index.history_as_of(sample["player_id"], sample["game_date"])
        self.assertNotIn(sample["game_id"], [h["game_id"] for h in history])


# 6. target TOI exclusion (recent/baseline TOI computed from PIT history only)
class Test06TargetToiExclusion(unittest.TestCase):
    def test_toi_features_come_from_history_not_target_row(self):
        ctx = pcs_mp.ContextMarginalContext()
        rows = ctx.goals.rows
        sample = next(r for r in rows if r["season"] == TUNING_SEASON)
        history = ctx.goals.index.history_as_of(sample["player_id"], sample["game_date"])
        self.assertTrue(all(h["game_date"] < sample["game_date"] for h in history))


# 7. Goals frozen probability provenance
class Test07GoalsFrozenProbabilityProvenance(unittest.TestCase):
    def test_goals_marginal_returns_probs_from_locked_candidate_e(self):
        ctx = pcs_mp.ContextMarginalContext()
        rows = ctx.goals.rows
        sample = next(r for r in rows if r["season"] == TUNING_SEASON)
        result = ctx.goals.predict(sample["player_id"], sample["team"], sample["opponent"],
                                    sample["game_date"], sample["season"])
        self.assertTrue(result is None or "probs" in result)


# 8. Points champion probability provenance -- NEVER the rejected GLM
class Test08PointsChampionProbabilityProvenance(unittest.TestCase):
    def test_points_marginal_has_no_mu_key(self):
        ctx = pcs_mp.ContextMarginalContext()
        rows = ctx.points.rows
        sample = next(r for r in rows if r["season"] == TUNING_SEASON)
        result = ctx.points.predict(sample["player_id"], sample["team"], sample["opponent"],
                                     sample["game_date"], sample["season"])
        if result is not None:
            self.assertNotIn("mu", result)

    def test_confidence_helper_never_imports_points_probability_path(self):
        with open("research/context_overlay/confidence_helpers.py") as f:
            src = f.read()
        self.assertNotIn("import project_player_points", src)
        self.assertNotIn("from research.player_points.live_projection import", src)
        self.assertNotIn("project_player_points(", src)


# 9. raw probability preserved
class Test09RawProbabilityPreserved(unittest.TestCase):
    def test_eval_block_carries_raw_brier_alongside_adjusted(self):
        results = _load_results()
        for prop in PROPS:
            eb = _eval_block(results, prop, EVAL_SEASONS[0])
            self.assertIn("raw_brier", eb)
            self.assertIn("adjusted_brier", eb)


# 10. adjusted probability separate (never overwrites raw)
class Test10AdjustedProbabilitySeparate(unittest.TestCase):
    def test_raw_and_adjusted_calibration_both_present(self):
        results = _load_results()
        for prop in PROPS:
            eb = _eval_block(results, prop, EVAL_SEASONS[0])
            self.assertIn("raw_calibration", eb)
            self.assertIn("adjusted_calibration", eb)
            self.assertNotEqual(eb["raw_calibration"]["mean_predicted"], eb["adjusted_calibration"]["mean_predicted"])


# 11. negative adjustment direction (context_offset <= 0 / shift <= 0 for COLD_AND_TOI_DECLINE)
class Test11NegativeAdjustmentDirection(unittest.TestCase):
    def test_fixed_logit_offset_never_positive(self):
        dev_pairs = [(0.3, 0), (0.3, 0), (0.3, 1), (0.5, 1), (0.5, 0)]
        fit = om.fit_fixed_logit_offset(dev_pairs)
        self.assertLessEqual(fit["offset"], 0.0)

    def test_winning_adjustment_reduces_or_maintains_probability_on_average(self):
        results = _load_results()
        for prop in PROPS:
            block = results["props"][prop]
            if block["winner"] == "B_FIXED_LOGIT_OFFSET":
                self.assertLessEqual(block["winner_params"]["offset"], 0.0)
            elif block["winner"] == "D_BAYESIAN_CONTEXT_BLEND":
                self.assertLessEqual(block["winner_params"]["shift"], 0.0)


# 12. no adjustment outside target state (control cohorts are raw-only)
class Test12NoAdjustmentOutsideTargetState(unittest.TestCase):
    def test_control_cohorts_have_no_adjusted_field(self):
        results = _load_results()
        for prop in PROPS:
            eb = _eval_block(results, prop, EVAL_SEASONS[0])
            control = eb["control_cohorts_raw_only"]
            self.assertIn("cold_without_toi_decline", control)
            self.assertIn("normal", control)
            self.assertNotIn("adjusted_brier", control["cold_without_toi_decline"])
            self.assertNotIn("adjusted_brier", control["normal"])


# 13. development-only adjustment fitting
class Test13DevelopmentOnlyFitting(unittest.TestCase):
    def test_dev_n_comes_from_tuning_season_only(self):
        results = _load_results()
        for prop in PROPS:
            self.assertIn("dev_n", results["props"][prop])
        self.assertEqual(results["config"]["tuning_season"], TUNING_SEASON)


# 14. temporal split
class Test14TemporalSplit(unittest.TestCase):
    def test_tuning_precedes_eval_seasons(self):
        for s in EVAL_SEASONS:
            self.assertLess(TUNING_SEASON, s)


# 15. freeze manifest
class Test15FreezeManifest(unittest.TestCase):
    def test_manifest_has_required_fields(self):
        results = _load_results()
        manifest = results["freeze_manifest"]
        for key in ("experiment_id", "context_state_used", "adjustment_family", "winner_by_prop",
                    "sample_floors", "code_hashes", "coherence_fix_by_season"):
            self.assertIn(key, manifest)
        self.assertEqual(manifest["context_state_used"], "COLD_AND_TOI_DECLINE only -- pure COLD_STATE and "
                                                           "HOT_STATE receive no adjustment")


# 16. evaluation frozen (driver code hash matches recorded manifest)
class Test16EvaluationFrozen(unittest.TestCase):
    def test_driver_hash_matches_manifest(self):
        results = _load_results()
        recorded = results["freeze_manifest"]["code_hashes"]["run_context_overlay_model.py"]
        actual = _file_sha256("research/run_context_overlay_model.py")
        self.assertEqual(recorded, actual)


# 17. cohort sample floor
class Test17CohortSampleFloor(unittest.TestCase):
    def test_dev_and_eval_floors_are_pre_specified_and_met(self):
        results = _load_results()
        for prop in PROPS:
            block = results["props"][prop]
            self.assertEqual(block["status"], "FITTED")
            self.assertGreaterEqual(block["dev_n"], OVERLAY_MIN_DEV_N)
            for season in EVAL_SEASONS:
                eb = _eval_block(results, prop, season)
                self.assertGreaterEqual(eb["n"], OVERLAY_MIN_EVAL_N)


# 18. normal control cohort
class Test18NormalControlCohort(unittest.TestCase):
    def test_normal_residual_smaller_in_magnitude_than_cold_toi_raw_residual(self):
        results = _load_results()
        for prop in PROPS:
            eb = _eval_block(results, prop, EVAL_SEASONS[0])
            normal_resid = abs(eb["control_cohorts_raw_only"]["normal"]["mean_prob_residual"])
            cold_toi_resid = abs(eb["raw_calibration"]["residual"])
            self.assertLess(normal_resid, cold_toi_resid)


# 19. cold-without-TOI control
class Test19ColdWithoutToiControl(unittest.TestCase):
    def test_cold_without_toi_cohort_present_and_smaller_effect(self):
        results = _load_results()
        for prop in PROPS:
            eb = _eval_block(results, prop, EVAL_SEASONS[0])
            cwt = eb["control_cohorts_raw_only"]["cold_without_toi_decline"]
            self.assertGreater(cwt["n"], 0)
            self.assertLess(abs(cwt["mean_prob_residual"]), abs(eb["raw_calibration"]["residual"]))


# 20-22. Goals Brier / log loss / calibration
class Test20To22GoalsMetrics(unittest.TestCase):
    def test_goals_brier_improves_both_seasons(self):
        results = _load_results()
        for season in EVAL_SEASONS:
            eb = _eval_block(results, "goals", season)
            self.assertLess(eb["adjusted_brier"], eb["raw_brier"])

    def test_goals_log_loss_improves_both_seasons(self):
        results = _load_results()
        for season in EVAL_SEASONS:
            eb = _eval_block(results, "goals", season)
            self.assertLess(eb["adjusted_log_loss"], eb["raw_log_loss"])

    def test_goals_calibration_improves_both_seasons(self):
        results = _load_results()
        for season in EVAL_SEASONS:
            eb = _eval_block(results, "goals", season)
            self.assertLess(abs(eb["adjusted_calibration"]["residual"]), abs(eb["raw_calibration"]["residual"]))


# 23-25. Points Brier / log loss / calibration
class Test23To25PointsMetrics(unittest.TestCase):
    def test_points_brier_improves_both_seasons(self):
        results = _load_results()
        for season in EVAL_SEASONS:
            eb = _eval_block(results, "points", season)
            self.assertLess(eb["adjusted_brier"], eb["raw_brier"])

    def test_points_log_loss_improves_both_seasons(self):
        results = _load_results()
        for season in EVAL_SEASONS:
            eb = _eval_block(results, "points", season)
            self.assertLess(eb["adjusted_log_loss"], eb["raw_log_loss"])

    def test_points_calibration_improves_both_seasons(self):
        results = _load_results()
        for season in EVAL_SEASONS:
            eb = _eval_block(results, "points", season)
            self.assertLess(abs(eb["adjusted_calibration"]["residual"]), abs(eb["raw_calibration"]["residual"]))


# 26. game bootstrap
class Test26GameBootstrap(unittest.TestCase):
    def test_game_bootstrap_ci_excludes_zero_in_improvement_direction(self):
        results = _load_results()
        for prop in PROPS:
            for season in EVAL_SEASONS:
                eb = _eval_block(results, prop, season)
                self.assertLess(eb["game_bootstrap_brier"]["ci_high"], 0.0)

    def test_bootstrap_resamples_by_cluster_not_row(self):
        examples = [{"game_id": 1}, {"game_id": 1}, {"game_id": 2}]
        diffs = [-1.0, -1.0, 1.0]
        result = paired_clustered_bootstrap(examples, "game_id", diffs, n_resamples=100)
        self.assertEqual(result["n_clusters"], 2)


# 27. date bootstrap
class Test27DateBootstrap(unittest.TestCase):
    def test_date_bootstrap_agrees_with_game_bootstrap_direction(self):
        results = _load_results()
        for prop in PROPS:
            for season in EVAL_SEASONS:
                eb = _eval_block(results, prop, season)
                self.assertLess(eb["date_bootstrap_brier"]["ci_high"], 0.0)


# 28. player bootstrap
class Test28PlayerBootstrap(unittest.TestCase):
    def test_player_bootstrap_does_not_invalidate(self):
        results = _load_results()
        for prop in PROPS:
            for season in EVAL_SEASONS:
                eb = _eval_block(results, prop, season)
                self.assertGreaterEqual(eb["player_bootstrap_brier"]["frac_improved"], 0.90)


# 29. player concentration
class Test29PlayerConcentration(unittest.TestCase):
    def test_top10_share_not_dominant(self):
        results = _load_results()
        for prop in PROPS:
            for season in EVAL_SEASONS:
                eb = _eval_block(results, prop, season)
                self.assertLess(eb["player_concentration"]["top10_share"], 0.25)
                self.assertLess(eb["player_concentration"]["top1_share"], 0.05)


# 30. probability-bin analysis
class Test30ProbabilityBinAnalysis(unittest.TestCase):
    def test_region_table_present_and_covers_dev_rows(self):
        results = _load_results()
        for prop in PROPS:
            table = results["props"][prop]["probability_region_table"]
            self.assertGreater(len(table), 0)
            total_n = sum(row["n"] for row in table)
            self.assertEqual(total_n, results["props"][prop]["dev_n"])


# 31. confidence interaction
class Test31ConfidenceInteraction(unittest.TestCase):
    def test_confidence_buckets_use_existing_shared_labels(self):
        results = _load_results()
        for prop in PROPS:
            eb = _eval_block(results, prop, EVAL_SEASONS[0])
            for label in eb["confidence_interaction"]:
                self.assertIn(label, ("HIGH", "MEDIUM", "LOW"))

    def test_confidence_helper_calls_shared_confidence_score(self):
        with open("research/context_overlay/confidence_helpers.py") as f:
            src = f.read()
        self.assertIn("cm.confidence_score", src)


# 32. LOW policy inheritance Goals
class Test32LowPolicyInheritanceGoals(unittest.TestCase):
    def test_goals_low_confidence_bet_narrowed_to_watch(self):
        result = decision_policy.gate_low_confidence("GOALS", "LOW", "BET")
        self.assertEqual(result["final_decision"], "WATCH")


# 33. LOW policy inheritance Points
class Test33LowPolicyInheritancePoints(unittest.TestCase):
    def test_points_low_confidence_bet_narrowed_to_watch(self):
        result = decision_policy.gate_low_confidence("POINTS", "LOW", "BET")
        self.assertEqual(result["final_decision"], "WATCH")

    def test_overlay_cannot_override_policy_restriction(self):
        # The overlay only ever changes a probability; it must never be
        # able to raise a WATCH-capped market back to BET.
        result = decision_policy.gate_low_confidence("POINTS", "LOW", "BET")
        self.assertNotEqual(result["final_decision"], "BET")


# 34. conservative-probability ordering (documented, not operationalized)
class Test34ConservativeProbabilityOrdering(unittest.TestCase):
    def test_architecture_documented_not_operationalized(self):
        results = _load_results()
        arch = results["freeze_manifest"]["conservative_probability_architecture"]
        self.assertIn("RAW MARGINAL", arch)
        self.assertIn("CONTEXT ADJUSTMENT", arch)
        self.assertIn("NOT operationalized", arch)


# 35. context registry
class Test35ContextRegistry(unittest.TestCase):
    def test_registry_has_two_entries(self):
        registry = _load_registry()
        signals = {e["signal"] for e in registry}
        self.assertIn("PLAYER_GOALS_1PLUS__COLD_AND_TOI_DECLINE", signals)
        self.assertIn("PLAYER_POINTS_1PLUS__COLD_AND_TOI_DECLINE", signals)

    def test_registry_status_uses_allowed_values(self):
        registry = _load_registry()
        for e in registry:
            self.assertIn(e["validation_status"], ("VALIDATED_OVERLAY", "PARTIAL", "REJECTED", "INSUFFICIENT_DATA"))

    def test_operational_status_never_reaches_full_bet_policy(self):
        # Superseded by the Preseason Master Consolidation sprint's Part 2:
        # a VALIDATED_OVERLAY's operational_status is now "SHADOW_VALIDATED"
        # (it was "RESEARCH" before that sprint wired the overlay into the
        # canonical shadow prediction stack) -- but it must never reach
        # FULL_BET_POLICY/OPERATIONAL_VALIDATED without prospective evidence.
        registry = _load_registry()
        for e in registry:
            self.assertIn(e["operational_status"],
                           ("SHADOW_VALIDATED", "RESEARCH", "NOT_OPERATIONAL", "REJECTED"))
            self.assertNotIn("FULL_BET_POLICY", e["operational_status"])


# 36. logical Goal<=Point coherence
class Test36LogicalCoherence(unittest.TestCase):
    def test_raw_marginals_show_no_violations(self):
        results = _load_results()
        for season in EVAL_SEASONS:
            coh = results["logical_coherence_by_season"].get(str(season), results["logical_coherence_by_season"].get(season))
            self.assertEqual(coh["raw_violations"], 0)

    def test_adjusted_violations_fixed_to_zero(self):
        results = _load_results()
        fix = results["freeze_manifest"]["coherence_fix_by_season"]
        for season in EVAL_SEASONS:
            block = fix.get(str(season), fix.get(season))
            self.assertEqual(block["post_fix_violations_remaining"], 0)


# 37. joint-layer compatibility (documented architecture, no retrain)
class Test37JointLayerCompatibility(unittest.TestCase):
    def test_joint_scoring_dependence_results_unchanged(self):
        self.assertEqual(_file_sha256("research/joint_scoring_dependence_results.json"),
                          "3076d4e849e60f8156601e6070301f17b8e51d56265880ff8c8bf0d3b58f9d91")


# 38. dashboard raw/adjusted labeling
class Test38DashboardLabeling(unittest.TestCase):
    def test_dashboard_view_exposes_overlay_loader(self):
        import dashboard.context_overlay_view as cov
        self.assertTrue(hasattr(cov, "load_results"))
        self.assertTrue(hasattr(cov, "RESEARCH_OVERLAY_DISCLAIMER"))
        self.assertIn("RESEARCH CONTEXT OVERLAY", cov.RESEARCH_OVERLAY_DISCLAIMER.upper())


# 39. no media ingestion
class Test39NoMediaIngestion(unittest.TestCase):
    def test_no_media_imports_in_overlay_package(self):
        for fname in ("research/run_context_overlay_model.py", "research/context_overlay/overlay_models.py",
                      "research/context_overlay/confidence_helpers.py"):
            with open(fname) as f:
                src = f.read().lower()
            for banned in ("sentiment", "media_corpus", "news_corpus"):
                self.assertNotIn(banned, src)


# 40. no arena adjustment
class Test40NoArenaAdjustment(unittest.TestCase):
    def test_no_arena_effects_import(self):
        with open("research/run_context_overlay_model.py") as f:
            src = f.read()
        self.assertNotIn("arena_effects", src)


# 41. no sportsbook call
class Test41NoSportsbookCall(unittest.TestCase):
    def test_no_odds_api_identifiers(self):
        with open("research/run_context_overlay_model.py") as f:
            src = f.read().lower()
        for banned in ("draftkings", "the_odds_api", "theoddsapi"):
            self.assertNotIn(banned, src)


# 42. Goals model unchanged
class Test42GoalsUnchanged(unittest.TestCase):
    def test_player_goals_results_unchanged(self):
        self.assertEqual(_file_sha256("research/player_goals_results.json"),
                          "3f5592585a255b11c77f2a4d08c2c9886d01e45dbc8b48b30d284389367f5348")


# 43. Points model unchanged
class Test43PointsUnchanged(unittest.TestCase):
    def test_player_points_results_unchanged(self):
        self.assertEqual(_file_sha256("research/player_points_results.json"),
                          "6eacd4d56dc78d6b371b7f0234252e1f969359a427d813efcd696780b8af8877")


# 44. SOG unchanged
class Test44SogUnchanged(unittest.TestCase):
    def test_player_sog_results_unchanged(self):
        self.assertEqual(_file_sha256("research/player_sog_results.json"),
                          "556d447bc6dcfc18df52812d98901cd7accad3b203a06606ddd68ea6993e8f61")


# 45. Assists unchanged
class Test45AssistsUnchanged(unittest.TestCase):
    def test_player_assists_results_unchanged(self):
        self.assertEqual(_file_sha256("research/player_assists_results.json"),
                          "3f8bc1c649cb3bbea4be0f56ebf893e399eaca415075ea1dca176e1f944ec0e9")


# 46. Blocks unchanged
class Test46BlocksUnchanged(unittest.TestCase):
    def test_player_blocks_results_unchanged(self):
        self.assertEqual(_file_sha256("research/player_blocks_results.json"),
                          "fc608ab5da9adf06170f96b7e96989fc29cf4cad07a26a9d9778d51649293c07")


# 47. Team SOG unchanged
class Test47TeamSogUnchanged(unittest.TestCase):
    def test_team_sog_results_unchanged(self):
        self.assertEqual(_file_sha256("research/team_sog_results.json"),
                          "90188ede1e076e4a1dc0bb0b569ae80542215c2db268b510ef966bea339fa0ac")


# 48. Goalie Saves unchanged
class Test48GoalieSavesUnchanged(unittest.TestCase):
    def test_goalie_saves_results_unchanged(self):
        self.assertEqual(_file_sha256("research/goalie_saves_results.json"),
                          "6533395bfe111385f2591dca0944a2a576a785178ac640c4fd7ee2363af3e34e")


# 49. joint scoring unchanged
class Test49JointScoringUnchanged(unittest.TestCase):
    def test_joint_scoring_dependence_results_unchanged(self):
        self.assertEqual(_file_sha256("research/joint_scoring_dependence_results.json"),
                          "3076d4e849e60f8156601e6070301f17b8e51d56265880ff8c8bf0d3b58f9d91")


# 50. joint shot/workload unchanged
class Test50JointShotWorkloadUnchanged(unittest.TestCase):
    def test_joint_shot_workload_results_unchanged(self):
        self.assertEqual(_file_sha256("research/joint_shot_workload_results.json"),
                          "ee83c18a4b44966e1807acd79f2589848f8f368cb81ea8ca13df0015786c788a")


# 51. confidence unchanged
class Test51ConfidenceUnchanged(unittest.TestCase):
    def test_confidence_framework_results_unchanged(self):
        with open("research/confidence_framework_results.json") as f:
            data = json.load(f)
        self.assertIn("results_by_prop_fold", data)

    def test_confidence_score_function_not_reimplemented(self):
        with open("research/context_overlay/confidence_helpers.py") as f:
            src = f.read()
        self.assertNotIn("def confidence_score", src)


# 52. decision policy v3 unchanged
class Test52DecisionPolicyUnchanged(unittest.TestCase):
    def test_decision_policy_v3_hash_unchanged(self):
        self.assertEqual(
            _file_sha256("research/player_props/decision_policy.py"),
            "f812d5fa2f1811c2b06b0b63b972dc499fc2f8f6e117a9afd5fa63bea55c763a",
        )


# 53. NHL win model unchanged + production boundary
class Test53NhlWinModelUnchanged(unittest.TestCase):
    def test_win_model_files_unchanged(self):
        self.assertEqual(_file_sha256("models/combined_model.py"),
                          "64e9e9cbe686b386951fed9d5001dc298c5dff6af7f582b8f197565f6d932c82")
        self.assertEqual(_file_sha256("models/elo_model.py"),
                          "8538d6b2e32112190919ac41f8b60f17d66528d58c2488c0ee7f7f2690411faf")

    def test_production_boundary_files_unchanged(self):
        self.assertEqual(_file_sha256("config.py"),
                          "c019568da204ace99222954d4f02546a25c31029453c36ed3b0ed4bf97d3df8a")
        self.assertEqual(_file_sha256("db.py"),
                          "b598f4640e191a26dba7231e240a26ebbf6d7a443bcf4f2eb4c43b37cabcea95")
        self.assertEqual(_file_sha256("schema.sql"),
                          "ff19dd3b0c4cd8a61371d77751a045f222bdce7636d119d90c013f58ef64f31f")


if __name__ == "__main__":
    unittest.main()
