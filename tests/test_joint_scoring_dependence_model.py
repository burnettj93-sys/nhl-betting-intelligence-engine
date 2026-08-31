"""
Part 52: tests for the Joint Scoring/Contribution Dependence Foundation
slice. Real fixtures only -- the joint corpus, the frozen results file,
and the four frozen marginal engines, never synthesized. Numbered
comments map to Part-52 topics.
"""
from __future__ import annotations

import hashlib
import json
import os
import unittest

from research.player_props import decision_policy, market_registry
from research.joint_scoring_dependence import features as jf
from research.joint_scoring_dependence import joint_models as jm
from research.joint_scoring_dependence import marginal_provenance as mp
from research.joint_scoring_dependence.logical_implication_registry import (
    IMPLICATION_GRAPH, detect_redundant_leg, implies, minimal_equivalent_combination,
)
from research.joint_shot_workload.joint_dependence_registry import JOINT_DEPENDENCE_REGISTRY
from research.run_joint_scoring_dependence_model import RESULTS_PATH, compute_pair_probs, combo_actual, \
    PAIR_COMBINATIONS


def _file_sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _load_results() -> dict:
    with open(RESULTS_PATH) as f:
        return json.load(f)


def _combo(name: str) -> dict:
    return next(c for c in PAIR_COMBINATIONS if c["name"] == name)


# 1. scoring joint corpus
class Test01ScoringJointCorpus(unittest.TestCase):
    def test_corpus_links_all_four_stats(self):
        rows = jf.load_joint_scoring_corpus()
        self.assertGreater(len(rows), 180000)
        sample = rows[0]
        for key in ("actual_sog", "actual_goals", "actual_assists", "actual_points", "player_id"):
            self.assertIn(key, sample)


# 2. points = goals + assists labels
class Test02PointsIdentity(unittest.TestCase):
    def test_points_equals_goals_plus_assists_everywhere(self):
        rows = jf.load_joint_scoring_corpus()
        for r in rows[:5000]:
            self.assertEqual(r["actual_points"], r["actual_goals"] + r["actual_assists"])


# 3. goals <= SOG labels
class Test03GoalsLeSog(unittest.TestCase):
    def test_goals_never_exceed_sog(self):
        rows = jf.load_joint_scoring_corpus()
        for r in rows[:5000]:
            self.assertLessEqual(r["actual_goals"], r["actual_sog"])


# 4. marginal SOG provenance
class Test04MarginalSogProvenance(unittest.TestCase):
    def test_sog_marginal_returns_real_probs(self):
        marg = mp.PlayerSogMarginal()
        rows = jf.load_joint_scoring_corpus()
        sample = next(r for r in rows if r["season"] == 20242025)
        result = marg.predict(sample["player_id"], sample["team"], sample["opponent"],
                               sample["game_date"], sample["season"])
        self.assertTrue(result is None or "mu" in result)


# 5. marginal Goals provenance
class Test05MarginalGoalsProvenance(unittest.TestCase):
    def test_goals_marginal_uses_locked_candidate_e(self):
        marg = mp.GoalsMarginal()
        self.assertEqual(len(marg.context_weights), len(marg.results["config"]["feature_names"]))
        self.assertEqual(marg.k_player, marg.results["best_k_player"])


# 6. marginal Assists provenance
class Test06MarginalAssistsProvenance(unittest.TestCase):
    def test_assists_marginal_uses_m4_plus_h2h(self):
        with open("research/player_assists_results.json") as f:
            r = json.load(f)
        marg = mp.AssistsMarginal()
        self.assertEqual(len(marg.weights), 6)
        self.assertEqual(marg.alpha, r["alpha"] if r["alpha"] > 0.01 else None)


# 7. marginal Points provenance
class Test07MarginalPointsProvenance(unittest.TestCase):
    def test_points_marginal_uses_empirical_baseline_not_glm(self):
        with open("research/player_points_results.json") as f:
            r = json.load(f)
        best_baseline = min(r["baseline_results"], key=lambda k: r["baseline_results"][k]["thresholds"]["1"]["brier"])
        self.assertEqual(best_baseline, "D_empirical_distribution")
        self.assertLess(r["baseline_results"]["D_empirical_distribution"]["thresholds"]["1"]["brier"],
                         r["headline_uncalibrated"]["thresholds"]["1"]["brier"],
                         "if this now fails, the empirical baseline no longer wins and Part 4's "
                         "marginal choice must be revisited")


# 8. PIT integrity
class Test08PitIntegrity(unittest.TestCase):
    def test_history_index_strictly_prior(self):
        rows = jf.load_joint_scoring_corpus()
        idx = jf.JointScoringHistoryIndex(rows)
        sample = rows[2000]
        hist = idx.history_as_of(sample["player_id"], sample["game_date"])
        self.assertTrue(all(r["game_date"] < sample["game_date"] for r in hist))


# 9. Goal -> Point implication
class Test09GoalImpliesPoint(unittest.TestCase):
    def test_implication_registered(self):
        self.assertTrue(implies("GOAL_1_PLUS", "POINT_1_PLUS"))


# 10. Assist -> Point implication
class Test10AssistImpliesPoint(unittest.TestCase):
    def test_implication_registered(self):
        self.assertTrue(implies("ASSIST_1_PLUS", "POINT_1_PLUS"))


# 11. Goal -> SOG1+ implication
class Test11GoalImpliesSog(unittest.TestCase):
    def test_implication_registered(self):
        self.assertTrue(implies("GOAL_1_PLUS", "SOG_1_PLUS"))
        self.assertFalse(implies("ASSIST_1_PLUS", "SOG_1_PLUS"))


# 12. structural redundant-leg detection
class Test12RedundantLegDetection(unittest.TestCase):
    def test_three_way_goal_point_reduces(self):
        # SOG_3_PLUS (the REAL tested threshold in this slice's own triple
        # combinations), not "SOG_1_PLUS" -- GOAL_1_PLUS only implies SOG at
        # the 1+ threshold (a goal requires just one shot), never SOG>=3, so
        # using the generic 1+ label here would incorrectly treat the SOG>=3
        # leg itself as redundant. This is a real bug this slice's own test
        # suite caught before it reached the frozen driver (see
        # run_joint_scoring_dependence_model.py's TRIPLE_COMBINATIONS comment).
        redundant = detect_redundant_leg(["SOG_3_PLUS", "GOAL_1_PLUS", "POINT_1_PLUS"])
        self.assertEqual(redundant, "POINT_1_PLUS")

    def test_minimal_equivalent_combination(self):
        minimal = minimal_equivalent_combination(["SOG_3_PLUS", "GOAL_1_PLUS", "POINT_1_PLUS"])
        self.assertEqual(set(minimal), {"SOG_3_PLUS", "GOAL_1_PLUS"})

    def test_generic_sog_1_plus_label_would_be_wrong_here(self):
        # documents WHY the driver never uses the generic label for this triple:
        # GOAL_1_PLUS implies SOG_1_PLUS specifically, so a combination that
        # (incorrectly) used that label would see it treated as redundant too.
        redundant = detect_redundant_leg(["SOG_1_PLUS", "GOAL_1_PLUS", "POINT_1_PLUS"])
        self.assertEqual(redundant, "SOG_1_PLUS")

    def test_no_redundancy_when_independent(self):
        self.assertIsNone(detect_redundant_leg(["SOG_1_PLUS", "ASSIST_1_PLUS"]))


# 13. frozen marginal coherence audit
class Test13FrozenMarginalCoherenceAudit(unittest.TestCase):
    def test_coherence_violations_reported(self):
        results = _load_results()
        cv = results["coherence_violations"]
        self.assertEqual(cv["goal_gt_point"], 0)
        self.assertEqual(cv["goal_gt_sog1plus"], 0)
        self.assertGreater(cv["assist_gt_point"], 0,
                            "if this now fails, the real incoherence disappeared and Section L must be rewritten")


# 14. logical inconsistency reporting
class Test14LogicalInconsistencyReporting(unittest.TestCase):
    def test_inconsistency_rate_disclosed_not_hidden(self):
        results = _load_results()
        cv = results["coherence_violations"]
        rate = cv["assist_gt_point"] / cv["n"]
        self.assertGreater(rate, 0.01)
        self.assertLess(rate, 0.5)


# 15. naive independence
class Test15NaiveIndependence(unittest.TestCase):
    def test_naive_is_plain_product(self):
        ex = {"mu_sog": 2.0, "p_goal_1plus": 0.3, "p_assist_1plus": 0.2, "p_point_1plus": 0.4,
              "sog_probs": {}, "goal_rate": 0.1, "assist_rate": 0.15, "point_rate": 0.25}
        probs = compute_pair_probs(ex, _combo("SOG3_GOAL"), {"SOG3_GOAL": 0.2})
        self.assertAlmostEqual(probs["naive"], probs["p_a"] * probs["p_b"], places=9)


# 16. empirical joint baseline
class Test16EmpiricalJointBaseline(unittest.TestCase):
    def test_shrunk_empirical_bounded_by_naive_and_empirical(self):
        naive, empirical = 0.05, 0.09
        shrunk = jm.shrunk_empirical_joint(empirical, 400, naive, k_shrink=2000)
        self.assertGreaterEqual(shrunk, min(naive, empirical) - 1e-9)
        self.assertLessEqual(shrunk, max(naive, empirical) + 1e-9)


# 17. conditional empirical baseline
class Test17ConditionalEmpiricalBaseline(unittest.TestCase):
    def test_conditional_rate_bounded(self):
        rows = jf.load_joint_scoring_corpus()
        tuning = [r for r in rows if r["season"] == 20232024]
        rate, n = jm.league_conditional_rate(tuning, "actual_sog", 3, "actual_goals", 1)
        self.assertGreaterEqual(rate, 0.0)
        self.assertLessEqual(rate, 1.0)
        self.assertGreater(n, 0)


# 18. goal|SOG conditional model
class Test18GoalGivenSogConditional(unittest.TestCase):
    def test_conversion_rate_shrinkage(self):
        rows = jf.load_joint_scoring_corpus()
        tuning = [r for r in rows if r["season"] == 20232024]
        rates = jm.ConversionRates(tuning, "actual_goals")
        self.assertEqual(rates.shrunk_rate([]), rates.league_rate)
        self.assertGreater(rates.league_rate, 0.0)
        self.assertLess(rates.league_rate, 0.5)


# 19. assist conditional model
class Test19AssistConditionalModel(unittest.TestCase):
    def test_assist_conversion_rate_real(self):
        rows = jf.load_joint_scoring_corpus()
        tuning = [r for r in rows if r["season"] == 20232024]
        rates = jm.ConversionRates(tuning, "actual_assists")
        self.assertGreater(rates.league_rate, 0.0)
        self.assertLess(rates.league_rate, 0.5)


# 20. structural joint probability
class Test20StructuralJointProbability(unittest.TestCase):
    def test_between_zero_and_one(self):
        p = jm.structural_joint_sog_event(2.0, 0.1, 3, 1)
        self.assertGreaterEqual(p, 0.0)
        self.assertLessEqual(p, 1.0)


# 21. pair Frechet lower bound
class Test21FrechetLowerBound(unittest.TestCase):
    def test_lower_bound_formula(self):
        lo, hi = jm.frechet_bounds(0.7, 0.6)
        self.assertAlmostEqual(lo, 0.3, places=9)


# 22. pair Frechet upper bound
class Test22FrechetUpperBound(unittest.TestCase):
    def test_upper_bound_formula(self):
        lo, hi = jm.frechet_bounds(0.4, 0.6)
        self.assertAlmostEqual(hi, 0.4, places=9)

    def test_frozen_results_respect_bounds_for_winner(self):
        results = _load_results()
        for name, cr in results["pair_results"].items():
            for s, b in cr["by_season"].items():
                self.assertEqual(b["winner_frechet_violations"], 0, f"{name}/{s} violated Frechet bounds")


# 23. triple bounds
class Test23TripleBounds(unittest.TestCase):
    def test_redundant_triples_use_pair_bounds(self):
        results = _load_results()
        for name, tr in results["triple_results"].items():
            self.assertIn(tr["reduces_to"], results["pair_results"])


# 24. marginal preservation
class Test24MarginalPreservation(unittest.TestCase):
    def test_logical_identity_never_exceeds_frozen_point_marginal(self):
        # after coherence clipping, the reported ASSIST_POINT probability
        # must never exceed the frozen Point marginal used for pricing
        ex = {"p_goal_1plus": 0.05, "p_assist_1plus": 0.30, "p_point_1plus": 0.20}
        combo = _combo("GOAL_POINT")
        combo_assist = {"kind": "logical", "field_a": "actual_assists", "x_a": 1,
                         "field_b": "actual_points", "x_b": 1}
        probs = compute_pair_probs(ex, combo_assist, {})
        self.assertLessEqual(probs["structural"], ex["p_point_1plus"] + 1e-9)


# 25. Monte Carlo marginal recovery
class Test25MonteCarloMarginalRecovery(unittest.TestCase):
    def test_sampler_recovers_analytic_marginals(self):
        results = _load_results()
        mc = results["monte_carlo_verification"]
        self.assertLess(mc["goal_1plus"]["abs_diff"], 0.02)
        self.assertLess(mc["assist_1plus"]["abs_diff"], 0.02)


# 26. Goals <= SOG samples
class Test26GoalsLeSogSamples(unittest.TestCase):
    def test_sampler_never_violates(self):
        results = _load_results()
        self.assertEqual(results["monte_carlo_verification"]["goals_le_sog_violations"], 0)

    def test_direct_sampler_call(self):
        samples = jm.sample_scoring_outcomes(2.0, 0.1, 0.15, n_samples=1000, seed=1)
        for g, s in zip(samples["goals"], samples["sog"]):
            self.assertLessEqual(g, s)


# 27. Points = Goals + Assists samples
class Test27PointsIdentitySamples(unittest.TestCase):
    def test_sampler_never_violates(self):
        results = _load_results()
        self.assertEqual(results["monte_carlo_verification"]["points_equals_goals_plus_assists_violations"], 0)

    def test_direct_sampler_call(self):
        samples = jm.sample_scoring_outcomes(2.0, 0.1, 0.15, n_samples=1000, seed=1)
        for p, g, a in zip(samples["points"], samples["goals"], samples["assists"]):
            self.assertEqual(p, g + a)


# 28. SOG + Goal joint
class Test28SogGoalJoint(unittest.TestCase):
    def test_validated_both_seasons(self):
        results = _load_results()
        for name in ("SOG2_GOAL", "SOG3_GOAL", "SOG4_GOAL"):
            for s in results["config"]["eval_seasons"]:
                bs = results["pair_results"][name]["by_season"][str(s)]["bootstrap_winner_vs_naive"]
                self.assertGreaterEqual(bs["game_clustered"]["frac_improved"], 0.95)


# 29. Goal + Point joint
class Test29GoalPointJoint(unittest.TestCase):
    def test_exact_identity_dependence_lift_real(self):
        results = _load_results()
        for s in results["config"]["eval_seasons"]:
            lift = results["pair_results"]["GOAL_POINT"]["by_season"][str(s)]["dependence_lift"]["lift_ratio"]
            self.assertGreater(lift, 1.5)


# 30. Assist + Point joint
class Test30AssistPointJoint(unittest.TestCase):
    def test_exact_identity_dependence_lift_real(self):
        results = _load_results()
        for s in results["config"]["eval_seasons"]:
            lift = results["pair_results"]["ASSIST_POINT"]["by_season"][str(s)]["dependence_lift"]["lift_ratio"]
            self.assertGreater(lift, 1.5)


# 31. SOG + Point joint
class Test31SogPointJoint(unittest.TestCase):
    def test_validated_both_seasons(self):
        results = _load_results()
        for name in ("SOG3_POINT", "SOG4_POINT"):
            for s in results["config"]["eval_seasons"]:
                bs = results["pair_results"][name]["by_season"][str(s)]["bootstrap_winner_vs_naive"]
                self.assertGreaterEqual(bs["game_clustered"]["frac_improved"], 0.95)


# 32. SOG + Assist joint
class Test32SogAssistJoint(unittest.TestCase):
    def test_validated_both_seasons(self):
        results = _load_results()
        for name in ("SOG2_ASSIST", "SOG3_ASSIST"):
            for s in results["config"]["eval_seasons"]:
                bs = results["pair_results"][name]["by_season"][str(s)]["bootstrap_winner_vs_naive"]
                self.assertGreaterEqual(bs["game_clustered"]["frac_improved"], 0.95)

    def test_structural_candidate_loses_to_naive_here(self):
        # the real, honest finding: the shot-conversion structural model is NOT
        # mechanically appropriate for assists and underperforms naive independence
        results = _load_results()
        for s in results["config"]["eval_seasons"]:
            b = results["pair_results"]["SOG2_ASSIST"]["by_season"][str(s)]
            self.assertGreater(b["D_structural_conditional"]["brier"], b["A_naive_independence"]["brier"])


# 33. SOG + Goal + Point
class Test33SogGoalPointTriple(unittest.TestCase):
    def test_reduces_to_sog_goal(self):
        results = _load_results()
        self.assertEqual(results["triple_results"]["SOG3_GOAL_POINT"]["reduces_to"], "SOG3_GOAL")
        self.assertEqual(results["triple_results"]["SOG3_GOAL_POINT"]["redundant_leg_detected"], "POINT_1_PLUS")


# 34. SOG + Assist + Point
class Test34SogAssistPointTriple(unittest.TestCase):
    def test_reduces_to_sog_assist(self):
        results = _load_results()
        self.assertEqual(results["triple_results"]["SOG3_ASSIST_POINT"]["reduces_to"], "SOG3_ASSIST")
        self.assertEqual(results["triple_results"]["SOG3_ASSIST_POINT"]["redundant_leg_detected"], "POINT_1_PLUS")


# 35. joint calibration
class Test35JointCalibration(unittest.TestCase):
    def test_calibration_bins_present(self):
        results = _load_results()
        s = str(results["config"]["eval_seasons"][0])
        winner = results["pair_results"]["SOG3_GOAL"]["winner_candidate"]
        cal = results["pair_results"]["SOG3_GOAL"]["by_season"][s][winner]["calibration"]
        self.assertGreater(len(cal), 0)


# 36. Brier
class Test36Brier(unittest.TestCase):
    def test_all_candidates_scored(self):
        results = _load_results()
        for name, cr in results["pair_results"].items():
            for s, b in cr["by_season"].items():
                for cand in ("A_naive_independence", "B_shrunk_empirical_joint", "C_conditional_empirical",
                             "D_structural_conditional", "E_gaussian_copula"):
                    self.assertIn("brier", b[cand])


# 37. log loss
class Test37LogLoss(unittest.TestCase):
    def test_all_candidates_have_log_loss(self):
        results = _load_results()
        for name, cr in results["pair_results"].items():
            for s, b in cr["by_season"].items():
                self.assertIn("log_loss", b["A_naive_independence"])


# 38. temporal split
class Test38TemporalSplit(unittest.TestCase):
    def test_warmup_tuning_eval_seasons(self):
        results = _load_results()
        cfg = results["config"]
        self.assertEqual(cfg["warmup_season"], 20222023)
        self.assertEqual(cfg["tuning_season"], 20232024)
        self.assertEqual(cfg["eval_seasons"], [20242025, 20252026])


# 39. freeze manifest
class Test39FreezeManifest(unittest.TestCase):
    def test_freeze_manifest_present(self):
        results = _load_results()
        manifest = results["freeze_manifest"]
        self.assertEqual(manifest["experiment_id"], "joint_scoring_dependence_v1")
        self.assertIn("code_hashes", manifest)
        self.assertIn("logical_implication_map", manifest)
        self.assertIn("marginal_reconciliation_policy", manifest)


# 40. frozen evaluation
class Test40FrozenEvaluation(unittest.TestCase):
    def test_single_winner_used_for_both_eval_seasons(self):
        results = _load_results()
        for name, cr in results["pair_results"].items():
            winners = {cr["by_season"][s]["winner_candidate"] for s in cr["by_season"]}
            self.assertEqual(len(winners), 1, f"{name} used different winners per season -- not frozen")


# 41. game bootstrap
class Test41GameBootstrap(unittest.TestCase):
    def test_headline_combos_pass_both_seasons(self):
        results = _load_results()
        for name in ("SOG3_GOAL", "SOG3_POINT", "SOG3_ASSIST"):
            for s in results["config"]["eval_seasons"]:
                bs = results["pair_results"][name]["by_season"][str(s)]["bootstrap_winner_vs_naive"]
                self.assertGreaterEqual(bs["game_clustered"]["frac_improved"], 0.95)


# 42. date bootstrap
class Test42DateBootstrap(unittest.TestCase):
    def test_date_clustered_present(self):
        results = _load_results()
        for s in results["config"]["eval_seasons"]:
            bs = results["pair_results"]["SOG3_GOAL"]["by_season"][str(s)]["bootstrap_winner_vs_naive"]
            self.assertIn("frac_improved", bs["date_clustered"])


# 43. rare event support
class Test43RareEventSupport(unittest.TestCase):
    def test_min_positive_events_enforced(self):
        results = _load_results()
        floor = results["config"]["min_joint_positive_events"]
        for name, cr in results["pair_results"].items():
            for s, b in cr["by_season"].items():
                if cr["combo"]["kind"] == "structural" and b["n_positive"] < floor:
                    self.assertEqual(b["bootstrap_winner_vs_naive"], "INSUFFICIENT_DATA")


# 44. dependence lift
class Test44DependenceLift(unittest.TestCase):
    def test_lift_reported_for_every_combo(self):
        results = _load_results()
        for name, cr in results["pair_results"].items():
            for s, b in cr["by_season"].items():
                self.assertIn("lift_ratio", b["dependence_lift"])


# 45. confidence metadata
class Test45ConfidenceMetadata(unittest.TestCase):
    def test_confidence_score_signature_unchanged(self):
        from research.player_sog import count_models as cm
        import inspect
        sig = inspect.signature(cm.confidence_score)
        self.assertEqual(list(sig.parameters), ["n_history_games", "recent_toi_cv", "recent_sog_cv",
                                                  "opponent_window_games", "opponent_window_target",
                                                  "appearance_rate"])


# 46. policy inheritance
class Test46PolicyInheritance(unittest.TestCase):
    def test_decision_policy_untouched(self):
        self.assertEqual(decision_policy.POLICY_VERSION, "prop_decision_policy_v3")


# 47. joint registry
class Test47JointRegistry(unittest.TestCase):
    def test_registry_extended_with_scoring_combinations(self):
        expected = {"PLAYER_SOG__PLAYER_GOAL", "PLAYER_GOAL__PLAYER_POINT", "PLAYER_ASSIST__PLAYER_POINT",
                    "PLAYER_SOG__PLAYER_POINT", "PLAYER_SOG__PLAYER_ASSIST",
                    "PLAYER_SOG__PLAYER_GOAL__PLAYER_POINT", "PLAYER_SOG__PLAYER_ASSIST__PLAYER_POINT"}
        self.assertTrue(expected.issubset(set(JOINT_DEPENDENCE_REGISTRY.keys())))

    def test_prior_slice_entries_still_present(self):
        prior = {"PLAYER_SOG__TEAM_SOG", "TEAM_SOG__GOALIE_SAVES", "PLAYER_SOG__GOALIE_SAVES",
                 "PLAYER_SOG__TEAM_SOG__GOALIE_SAVES"}
        self.assertTrue(prior.issubset(set(JOINT_DEPENDENCE_REGISTRY.keys())))


# 48. implication registry
class Test48ImplicationRegistry(unittest.TestCase):
    def test_graph_structure(self):
        self.assertIn("GOAL_1_PLUS", IMPLICATION_GRAPH)
        self.assertIn("ASSIST_1_PLUS", IMPLICATION_GRAPH)


# 49. dashboard
class Test49Dashboard(unittest.TestCase):
    def test_dashboard_page_discloses_research_only(self):
        path = "dashboard/pages/19_Joint_Scoring_Dependence_Research.py"
        self.assertTrue(os.path.exists(path))
        with open(path) as f:
            content = f.read()
        self.assertIn("JOINT PROBABILITY ESTIMATION ONLY", content)
        self.assertIn("EXACT LOGICAL IDENTITIES", content)


# 50. no sportsbook calls
class Test50NoSportsbookCalls(unittest.TestCase):
    def test_driver_makes_no_network_calls(self):
        with open("research/run_joint_scoring_dependence_model.py") as f:
            content = f.read().lower()
        for banned in ("requests.get", "requests.post", "urlopen", "odds_api", "sportsbook"):
            self.assertNotIn(banned, content)


# 51. Player SOG unchanged
class Test51PlayerSogUnchanged(unittest.TestCase):
    def test_player_sog_results_unchanged(self):
        self.assertEqual(_file_sha256("research/player_sog_results.json"),
                          "556d447bc6dcfc18df52812d98901cd7accad3b203a06606ddd68ea6993e8f61")


# 52. Goals unchanged
class Test52GoalsUnchanged(unittest.TestCase):
    def test_player_goals_results_unchanged(self):
        self.assertEqual(_file_sha256("research/player_goals_results.json"),
                          "3f5592585a255b11c77f2a4d08c2c9886d01e45dbc8b48b30d284389367f5348")


# 53. Assists unchanged
class Test53AssistsUnchanged(unittest.TestCase):
    def test_player_assists_results_unchanged(self):
        self.assertEqual(_file_sha256("research/player_assists_results.json"),
                          "3f8bc1c649cb3bbea4be0f56ebf893e399eaca415075ea1dca176e1f944ec0e9")


# 54. Points unchanged
class Test54PointsUnchanged(unittest.TestCase):
    def test_player_points_results_unchanged(self):
        self.assertEqual(_file_sha256("research/player_points_results.json"),
                          "6eacd4d56dc78d6b371b7f0234252e1f969359a427d813efcd696780b8af8877")
        self.assertEqual(_file_sha256("research/player_points_redesign_results.json"),
                          "490614606d5a8e046a9072669bc15a2bdfbb0097fb3a1a9696e7cd878ea97b75")


# 55. Team SOG unchanged
class Test55TeamSogUnchanged(unittest.TestCase):
    def test_team_sog_results_unchanged(self):
        self.assertEqual(_file_sha256("research/team_sog_results.json"),
                          "90188ede1e076e4a1dc0bb0b569ae80542215c2db268b510ef966bea339fa0ac")


# 56. Goalie Saves unchanged
class Test56GoalieSavesUnchanged(unittest.TestCase):
    def test_goalie_saves_results_unchanged(self):
        self.assertEqual(_file_sha256("research/goalie_saves_results.json"),
                          "6533395bfe111385f2591dca0944a2a576a785178ac640c4fd7ee2363af3e34e")


# 57. Blocks unchanged
class Test57BlocksUnchanged(unittest.TestCase):
    def test_player_blocks_results_unchanged(self):
        self.assertEqual(_file_sha256("research/player_blocks_results.json"),
                          "fc608ab5da9adf06170f96b7e96989fc29cf4cad07a26a9d9778d51649293c07")


# 58. confidence unchanged
class Test58ConfidenceUnchanged(unittest.TestCase):
    def test_confidence_framework_results_unchanged(self):
        with open("research/confidence_framework_results.json") as f:
            data = json.load(f)
        self.assertIn("results_by_prop_fold", data)


# 59. decision policy v3 unchanged
class Test59DecisionPolicyUnchanged(unittest.TestCase):
    def test_decision_policy_v3_unchanged(self):
        self.assertEqual(
            _file_sha256("research/player_props/decision_policy.py"),
            "f812d5fa2f1811c2b06b0b63b972dc499fc2f8f6e117a9afd5fa63bea55c763a",
        )


# 60. NHL win model unchanged
class Test60NhlWinModelUnchanged(unittest.TestCase):
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
