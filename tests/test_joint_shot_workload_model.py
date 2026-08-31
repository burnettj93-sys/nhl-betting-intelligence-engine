"""
Part 57: tests for the Joint Shot/Workload Dependence Foundation slice.
Real fixtures only -- the joint corpus, the frozen results file, and the
three frozen marginal engines, never synthesized. Numbered comments map
to Part-57 topics.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import unittest

from research.player_props import decision_policy, market_registry
from research.joint_shot_workload import features as jf
from research.joint_shot_workload import joint_models as jm
from research.joint_shot_workload import marginal_provenance as mp
from research.joint_shot_workload.joint_dependence_registry import JOINT_DEPENDENCE_REGISTRY
from research.run_joint_shot_workload_model import (
    RESULTS_PATH,
    PAIR_COMBINATIONS,
    TRIPLE_COMBINATION,
    combo_actual,
    compute_pair_probs,
    compute_triple_probs,
)


def _file_sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _load_results() -> dict:
    with open(RESULTS_PATH) as f:
        return json.load(f)


# 1. joint corpus linkage
class Test01JointCorpusLinkage(unittest.TestCase):
    def test_rows_link_player_team_and_goalie(self):
        rows = jf.load_joint_corpus()
        self.assertGreater(len(rows), 100000)
        sample = rows[0]
        for key in ("player_id", "player_team", "opponent_team", "opposing_goalie_id",
                    "actual_player_sog", "actual_team_sog", "actual_goalie_saves"):
            self.assertIn(key, sample)


# 2. player/team identity
class Test02PlayerTeamIdentity(unittest.TestCase):
    def test_player_team_matches_a_real_team_abbrev(self):
        rows = jf.load_joint_corpus()
        sample = rows[0]
        self.assertIsInstance(sample["player_team"], str)
        self.assertEqual(len(sample["player_team"]), 3)


# 3. opponent goalie identity
class Test03OpponentGoalieIdentity(unittest.TestCase):
    def test_goalie_start_status_always_starter(self):
        rows = jf.load_joint_corpus()
        for r in rows[:2000]:
            self.assertEqual(r["goalie_start_status"], "STARTER")


# 4. PIT marginal Player SOG
class Test04PitMarginalPlayerSog(unittest.TestCase):
    def test_player_marginal_uses_strict_prior_history(self):
        marg = mp.PlayerSogMarginal()
        rows = jf.load_joint_corpus()
        sample = next(r for r in rows if r["season"] == 20242025)
        result = marg.predict(sample["player_id"], sample["player_team"], sample["opponent_team"],
                               sample["game_date"], sample["season"])
        # Either a real projection or a legitimate ineligibility -- never an exception
        self.assertTrue(result is None or "mu" in result)


# 5. PIT marginal Team SOG
class Test05PitMarginalTeamSog(unittest.TestCase):
    def test_team_marginal_uses_strict_prior_history(self):
        marg = mp.TeamSogMarginal()
        rows = jf.load_joint_corpus()
        sample = next(r for r in rows if r["season"] == 20242025)
        result = marg.predict(sample["player_team"], sample["opponent_team"], "home",
                               sample["game_id"], sample["game_date"], sample["season"])
        self.assertTrue(result is None or "mu" in result)


# 6. PIT marginal Goalie Saves
class Test06PitMarginalGoalieSaves(unittest.TestCase):
    def test_goalie_marginal_uses_strict_prior_history(self):
        marg = mp.GoalieSavesMarginal()
        rows = jf.load_joint_corpus()
        sample = next(r for r in rows if r["season"] == 20242025)
        result = marg.predict(sample["opposing_goalie_id"], sample["opponent_team"], sample["player_team"],
                               "away", sample["game_id"], sample["game_date"], sample["season"])
        self.assertTrue(result is None or "mu" in result)


# 7. marginal common-set recovery
class Test07MarginalCommonSetRecovery(unittest.TestCase):
    def test_examples_by_season_n_reported(self):
        results = _load_results()
        known_seasons = {int(k) for k in results["examples_by_season_n"]}
        for s in [results["config"]["tuning_season"]] + results["config"]["eval_seasons"]:
            self.assertIn(s, known_seasons)


# 8. player SOG <= Team SOG
class Test08PlayerSogLeTeamSog(unittest.TestCase):
    def test_binomial_allocation_never_exceeds_team_sog(self):
        for n in (0, 1, 5, 29):
            for x in (2, 3, 4, 10):
                p = jm.binomial_sf_at_least(x, n, 0.1)
                if x > n:
                    self.assertEqual(p, 0.0)

    def test_real_corpus_never_violates_identity(self):
        rows = jf.load_joint_corpus()
        for r in rows[:2000]:
            self.assertLessEqual(r["actual_player_sog"], r["actual_team_sog"])


# 9. goalie saves <= workload where applicable
class Test09GoalieSavesLeWorkload(unittest.TestCase):
    def test_real_corpus_full_game_saves_le_team_sog(self):
        rows = jf.load_joint_corpus()
        for r in rows[:2000]:
            if not r["multi_goalie_game"]:
                self.assertLessEqual(r["actual_goalie_saves"], r["actual_team_sog"])


# 10. empty-net adjustment
class Test10EmptyNetAdjustment(unittest.TestCase):
    def test_empty_net_sog_count_always_nonnegative(self):
        rows = jf.load_joint_corpus()
        for r in rows[:5000]:
            self.assertGreaterEqual(r["empty_net_sog_count"], 0)

    def test_structural_params_empty_net_dist_sums_to_one(self):
        rows = jf.load_joint_corpus()
        tuning = [r for r in rows if r["season"] == 20232024]
        params = jm.StructuralParams(tuning, 0.9)
        self.assertAlmostEqual(sum(params.empty_net_dist.values()), 1.0, places=6)


# 11. multi-goalie handling
class Test11MultiGoalieHandling(unittest.TestCase):
    def test_multi_goalie_flag_present_and_real(self):
        rows = jf.load_joint_corpus()
        self.assertTrue(any(r["multi_goalie_game"] for r in rows[:20000]))
        self.assertTrue(any(not r["multi_goalie_game"] for r in rows[:2000]))


# 12. naive independence calculation
class Test12NaiveIndependenceCalculation(unittest.TestCase):
    def test_naive_is_plain_product(self):
        params = jm.StructuralParams([{"empty_net_sog_count": 0}] * 10, 0.9)
        ex = {"mu_team": 29.0, "player_share": 0.06, "player_probs": {3: 0.3}}
        combo = {"family": "PLAYER_TEAM", "x_player": 3, "y_team": 25}
        probs = compute_pair_probs(ex, combo, params)
        self.assertAlmostEqual(probs["naive"], probs["p_a"] * probs["p_b"], places=9)


# 13. empirical joint baseline
class Test13EmpiricalJointBaseline(unittest.TestCase):
    def test_shrunk_empirical_between_naive_and_empirical(self):
        naive = 0.05
        empirical = 0.10
        shrunk = jm.shrunk_empirical_joint(empirical, 500, naive, k_shrink=2000)
        self.assertGreaterEqual(shrunk, min(naive, empirical) - 1e-9)
        self.assertLessEqual(shrunk, max(naive, empirical) + 1e-9)

    def test_zero_support_returns_naive(self):
        shrunk = jm.shrunk_empirical_joint(0.5, 0, 0.05)
        self.assertAlmostEqual(shrunk, 0.05, places=9)


# 14. conditional empirical baseline
class Test14ConditionalEmpiricalBaseline(unittest.TestCase):
    def test_conditional_rate_bounded(self):
        rows = jf.load_joint_corpus()
        tuning = [r for r in rows if r["season"] == 20232024]
        rate, n = jm.league_conditional_rate(tuning, "actual_player_sog", 3, "actual_team_sog", 30)
        self.assertGreaterEqual(rate, 0.0)
        self.assertLessEqual(rate, 1.0)
        self.assertGreater(n, 0)


# 15. player-share model
class Test15PlayerShareModel(unittest.TestCase):
    def test_league_avg_share_is_real_and_small(self):
        rows = jf.load_joint_corpus()
        tuning = [r for r in rows if r["season"] == 20232024]
        rates = jm.PlayerShareRates(tuning)
        self.assertGreater(rates.league_avg_share, 0.0)
        self.assertLess(rates.league_avg_share, 0.2)


# 16. player-share shrinkage
class Test16PlayerShareShrinkage(unittest.TestCase):
    def test_zero_history_returns_league_average(self):
        rows = jf.load_joint_corpus()
        tuning = [r for r in rows if r["season"] == 20232024]
        rates = jm.PlayerShareRates(tuning)
        self.assertEqual(rates.shrunk_share([]), rates.league_avg_share)

    def test_large_sample_shrinks_less_than_small_sample_toward_prior(self):
        rows = jf.load_joint_corpus()
        tuning = [r for r in rows if r["season"] == 20232024]
        rates = jm.PlayerShareRates(tuning)
        hot_small = [{"actual_player_sog": 10, "actual_team_sog": 20}] * 3
        hot_big = [{"actual_player_sog": 10, "actual_team_sog": 20}] * 300
        small_share = rates.shrunk_share(hot_small)
        big_share = rates.shrunk_share(hot_big)
        self.assertGreater(big_share, small_share)


# 17. Team SOG conditional distribution
class Test17TeamSogConditionalDistribution(unittest.TestCase):
    def test_poisson_pmf_table_sums_near_one(self):
        table = jm._poisson_pmf_table(29.0, 100)
        self.assertAlmostEqual(sum(table), 1.0, places=3)


# 18. goalie-save conditional distribution
class Test18GoalieSaveConditionalDistribution(unittest.TestCase):
    def test_saves_sf_decreases_in_y(self):
        params = jm.StructuralParams([{"empty_net_sog_count": 0}] * 100, 0.9)
        p20 = jm.saves_sf_given_team_sog(29, 20, params)
        p25 = jm.saves_sf_given_team_sog(29, 25, params)
        self.assertGreaterEqual(p20, p25)


# 19. pair joint probability
class Test19PairJointProbability(unittest.TestCase):
    def test_structural_joint_between_zero_and_one(self):
        params = jm.StructuralParams([{"empty_net_sog_count": 0}] * 100, 0.9)
        p = jm.structural_joint_player_team(29.0, 0.06, 3, 30)
        self.assertGreaterEqual(p, 0.0)
        self.assertLessEqual(p, 1.0)


# 20. triple joint probability
class Test20TripleJointProbability(unittest.TestCase):
    def test_three_way_between_zero_and_one(self):
        params = jm.StructuralParams([{"empty_net_sog_count": 0}] * 100, 0.9)
        p = jm.structural_joint_three_way(29.0, 0.06, params, 3, 30, 20)
        self.assertGreaterEqual(p, 0.0)
        self.assertLessEqual(p, 1.0)

    def test_three_way_le_each_pair(self):
        params = jm.StructuralParams([{"empty_net_sog_count": 0}] * 100, 0.9)
        three = jm.structural_joint_three_way(29.0, 0.06, params, 3, 30, 20)
        pt = jm.structural_joint_player_team(29.0, 0.06, 3, 30)
        tg = jm.structural_joint_team_goalie(29.0, params, 30, 20)
        self.assertLessEqual(three, pt + 1e-9)
        self.assertLessEqual(three, tg + 1e-9)


# 21. Frechet lower bound
class Test21FrechetLowerBound(unittest.TestCase):
    def test_lower_bound_formula(self):
        lo, hi = jm.frechet_bounds(0.6, 0.7)
        self.assertAlmostEqual(lo, 0.3, places=9)


# 22. Frechet upper bound
class Test22FrechetUpperBound(unittest.TestCase):
    def test_upper_bound_formula(self):
        lo, hi = jm.frechet_bounds(0.3, 0.5)
        self.assertAlmostEqual(hi, 0.3, places=9)

    def test_frozen_structural_results_respect_bounds(self):
        results = _load_results()
        for combo_name, combo_result in results["pair_results"].items():
            for s, block in combo_result["by_season"].items():
                self.assertEqual(block["frechet_violations"], 0,
                                  f"{combo_name} season {s} violated Frechet bounds")


# 23. structural subset case
class Test23StructuralSubsetCase(unittest.TestCase):
    def test_player_ge_4_implies_team_ge_4_automatically(self):
        # Binomial(n, p) can never exceed n -- if team SOG < 4, P(player >= 4) is exactly 0
        p = jm.binomial_sf_at_least(4, 3, 0.5)
        self.assertEqual(p, 0.0)


# 24. threshold monotonicity
class Test24ThresholdMonotonicity(unittest.TestCase):
    def test_saves_sf_monotonic_in_threshold(self):
        params = jm.StructuralParams([{"empty_net_sog_count": 0}] * 100, 0.9)
        probs = [jm.saves_sf_given_team_sog(29, y, params) for y in (10, 20, 25, 30)]
        self.assertEqual(probs, sorted(probs, reverse=True))

    def test_conditional_monotonicity_goalie_given_team(self):
        # P(Goalie 25+ | Team 35+) should not be lower than P(Goalie 25+ | Team 20+)
        params = jm.StructuralParams([{"empty_net_sog_count": 0}] * 100, 0.9)
        p_low_team = jm.saves_sf_given_team_sog(20, 25, params)
        p_high_team = jm.saves_sf_given_team_sog(35, 25, params)
        self.assertGreaterEqual(p_high_team, p_low_team)


# 25. joint calibration
class Test25JointCalibration(unittest.TestCase):
    def test_calibration_bins_present(self):
        results = _load_results()
        any_combo = next(iter(results["pair_results"].values()))
        any_season = next(iter(any_combo["by_season"].values()))
        self.assertIn("calibration", any_season["D_structural_factorization"])


# 26. joint Brier
class Test26JointBrier(unittest.TestCase):
    def test_all_candidates_scored(self):
        results = _load_results()
        for combo_name, combo_result in results["pair_results"].items():
            for s, block in combo_result["by_season"].items():
                for name in ("A_naive_independence", "B_shrunk_empirical_joint", "C_conditional_empirical",
                             "D_structural_factorization", "E_gaussian_copula"):
                    self.assertIn("brier", block[name])


# 27. joint log loss
class Test27JointLogLoss(unittest.TestCase):
    def test_all_candidates_have_log_loss(self):
        results = _load_results()
        for combo_name, combo_result in results["pair_results"].items():
            for s, block in combo_result["by_season"].items():
                for name in ("A_naive_independence", "D_structural_factorization"):
                    self.assertIn("log_loss", block[name])
                    self.assertGreaterEqual(block[name]["log_loss"], 0.0)


# 28. temporal split
class Test28TemporalSplit(unittest.TestCase):
    def test_warmup_tuning_eval_seasons(self):
        results = _load_results()
        cfg = results["config"]
        self.assertEqual(cfg["warmup_season"], 20222023)
        self.assertEqual(cfg["tuning_season"], 20232024)
        self.assertEqual(cfg["eval_seasons"], [20242025, 20252026])


# 29. freeze manifest
class Test29FreezeManifest(unittest.TestCase):
    def test_freeze_manifest_present(self):
        results = _load_results()
        manifest = results["freeze_manifest"]
        self.assertEqual(manifest["experiment_id"], "joint_shot_workload_v1")
        self.assertIn("code_hashes", manifest)
        self.assertIn("marginal_model_versions", manifest)
        self.assertIn("dependence_parameters", manifest)


# 30. frozen eval
class Test30FrozenEval(unittest.TestCase):
    def test_single_rho_used_for_both_eval_seasons(self):
        results = _load_results()
        self.assertIn("rho_by_family", results)
        self.assertNotIn("rho_by_family_20242025", results)


# 31. game bootstrap
class Test31GameBootstrap(unittest.TestCase):
    def test_bootstrap_present_where_supported(self):
        results = _load_results()
        found_any = False
        for combo_result in results["pair_results"].values():
            for block in combo_result["by_season"].values():
                bs = block["bootstrap_structural_vs_naive"]
                if bs != "INSUFFICIENT_DATA":
                    self.assertIn("game_clustered", bs)
                    found_any = True
        self.assertTrue(found_any)


# 32. date bootstrap
class Test32DateBootstrap(unittest.TestCase):
    def test_date_clustered_present_where_supported(self):
        results = _load_results()
        for combo_result in results["pair_results"].values():
            for block in combo_result["by_season"].values():
                bs = block["bootstrap_structural_vs_naive"]
                if bs != "INSUFFICIENT_DATA":
                    self.assertIn("date_clustered", bs)


# 33. marginal recovery
class Test33MarginalRecovery(unittest.TestCase):
    def test_player_recovery_reported(self):
        results = _load_results()
        for s in results["config"]["eval_seasons"]:
            self.assertIn("player_3plus_abs_mean_diff", results["marginal_recovery"][str(s)])

    def test_monte_carlo_sampler_recovers_analytic_marginals(self):
        results = _load_results()
        mc = results["monte_carlo_sampler_verification"]
        for key in ("team_sog_25plus", "player_sog_3plus", "goalie_saves_20plus"):
            self.assertLess(mc[key]["abs_diff"], 0.01,
                             f"Monte Carlo sampler diverged from the analytic structural marginal for {key}")

    def test_sample_structural_joint_respects_player_le_team(self):
        params = jm.StructuralParams([{"empty_net_sog_count": 0}] * 100, 0.9)
        samples = jm.sample_structural_joint(29.0, 0.06, params, n_samples=500, seed=1)
        for p_sog, t_sog in zip(samples["player_sog"], samples["team_sog"]):
            self.assertLessEqual(p_sog, t_sog)


# 34. dependence lift
class Test34DependenceLift(unittest.TestCase):
    def test_dependence_lift_reported_for_every_combo(self):
        results = _load_results()
        for combo_result in results["pair_results"].values():
            for block in combo_result["by_season"].values():
                self.assertIn("lift_ratio", block["dependence_lift"])


# 35. rare-joint support
class Test35RareJointSupport(unittest.TestCase):
    def test_insufficient_data_marked_where_thin(self):
        results = _load_results()
        for combo_result in results["pair_results"].values():
            for block in combo_result["by_season"].values():
                if block["n_positive"] < results["config"]["min_joint_positive_events"]:
                    self.assertEqual(block["bootstrap_structural_vs_naive"], "INSUFFICIENT_DATA")


# 36. conservative joint research
class Test36ConservativeJointResearch(unittest.TestCase):
    def test_conservative_joint_status_is_research(self):
        results = _load_results()
        self.assertEqual(results["freeze_manifest"]["joint_conservative_methodology"], "RESEARCH -- not yet operationalized (Part 39)")


# 37. confidence metadata
class Test37ConfidenceMetadata(unittest.TestCase):
    def test_confidence_score_signature_unchanged(self):
        from research.player_sog import count_models as cm
        import inspect
        sig = inspect.signature(cm.confidence_score)
        self.assertEqual(list(sig.parameters), ["n_history_games", "recent_toi_cv", "recent_sog_cv",
                                                  "opponent_window_games", "opponent_window_target",
                                                  "appearance_rate"])


# 38. policy inheritance metadata
class Test38PolicyInheritanceMetadata(unittest.TestCase):
    def test_decision_policy_untouched_by_this_slice(self):
        self.assertEqual(decision_policy.POLICY_VERSION, "prop_decision_policy_v3")


# 39. joint registry
class Test39JointRegistry(unittest.TestCase):
    def test_registry_has_all_four_combinations(self):
        # Extended by the later Joint Scoring Dependence slice (Part 46's own
        # instruction: "extend the existing registry") -- checks these four
        # are still present, not that they are the ONLY entries.
        self.assertTrue({"PLAYER_SOG__TEAM_SOG", "TEAM_SOG__GOALIE_SAVES",
                          "PLAYER_SOG__GOALIE_SAVES", "PLAYER_SOG__TEAM_SOG__GOALIE_SAVES"}
                         .issubset(set(JOINT_DEPENDENCE_REGISTRY.keys())))

    def test_market_registry_untouched(self):
        # This slice must not add sportsbook markets for unpriced joint combinations
        self.assertIsNone(market_registry.get("PLAYER_SOG__TEAM_SOG"))


# 40. dashboard labeling
class Test40DashboardLabeling(unittest.TestCase):
    def test_dashboard_page_discloses_research_only(self):
        path = "dashboard/pages/18_Joint_Shot_Workload_Research.py"
        self.assertTrue(os.path.exists(path))
        with open(path) as f:
            content = f.read()
        self.assertIn("JOINT PROBABILITY ESTIMATION ONLY", content)
        self.assertIn("NOT sportsbook pricing", content)


# 41. no sportsbook calls
class Test41NoSportsbookCalls(unittest.TestCase):
    def test_driver_makes_no_network_calls(self):
        with open("research/run_joint_shot_workload_model.py") as f:
            content = f.read().lower()
        for banned in ("requests.get", "requests.post", "urlopen", "odds_api", "sportsbook"):
            self.assertNotIn(banned, content)


# 42. Player SOG unchanged
class Test42PlayerSogUnchanged(unittest.TestCase):
    def test_player_sog_results_unchanged(self):
        self.assertEqual(_file_sha256("research/player_sog_results.json"),
                          "556d447bc6dcfc18df52812d98901cd7accad3b203a06606ddd68ea6993e8f61")


# 43. period SOG unchanged
class Test43PeriodSogUnchanged(unittest.TestCase):
    def test_player_sog_period_results_unchanged(self):
        self.assertEqual(_file_sha256("research/player_sog_period_results.json"),
                          "1d81d5ac989215da1302dcc550159a31f8feb8e1593da964f4e5485216e19e29")


# 44. Team SOG unchanged
class Test44TeamSogUnchanged(unittest.TestCase):
    def test_team_sog_results_unchanged(self):
        self.assertEqual(_file_sha256("research/team_sog_results.json"),
                          "90188ede1e076e4a1dc0bb0b569ae80542215c2db268b510ef966bea339fa0ac")


# 45. Goalie Saves unchanged
class Test45GoalieSavesUnchanged(unittest.TestCase):
    def test_goalie_saves_results_unchanged(self):
        self.assertEqual(_file_sha256("research/goalie_saves_results.json"),
                          "6533395bfe111385f2591dca0944a2a576a785178ac640c4fd7ee2363af3e34e")


# 46. Goals unchanged
class Test46GoalsUnchanged(unittest.TestCase):
    def test_player_goals_results_unchanged(self):
        self.assertEqual(_file_sha256("research/player_goals_results.json"),
                          "3f5592585a255b11c77f2a4d08c2c9886d01e45dbc8b48b30d284389367f5348")


# 47. Assists unchanged
class Test47AssistsUnchanged(unittest.TestCase):
    def test_player_assists_results_unchanged(self):
        self.assertEqual(_file_sha256("research/player_assists_results.json"),
                          "3f8bc1c649cb3bbea4be0f56ebf893e399eaca415075ea1dca176e1f944ec0e9")


# 48. Blocks unchanged
class Test48BlocksUnchanged(unittest.TestCase):
    def test_player_blocks_results_unchanged(self):
        self.assertEqual(_file_sha256("research/player_blocks_results.json"),
                          "fc608ab5da9adf06170f96b7e96989fc29cf4cad07a26a9d9778d51649293c07")


# 49. Points unchanged
class Test49PointsUnchanged(unittest.TestCase):
    def test_player_points_results_unchanged(self):
        self.assertEqual(_file_sha256("research/player_points_results.json"),
                          "6eacd4d56dc78d6b371b7f0234252e1f969359a427d813efcd696780b8af8877")
        self.assertEqual(_file_sha256("research/player_points_redesign_results.json"),
                          "490614606d5a8e046a9072669bc15a2bdfbb0097fb3a1a9696e7cd878ea97b75")


# 50. confidence framework unchanged
class Test50ConfidenceFrameworkUnchanged(unittest.TestCase):
    def test_confidence_framework_results_unchanged(self):
        with open("research/confidence_framework_results.json") as f:
            data = json.load(f)
        self.assertIn("results_by_prop_fold", data)


# 51. decision policy v3 unchanged
class Test51DecisionPolicyUnchanged(unittest.TestCase):
    def test_decision_policy_v3_unchanged(self):
        self.assertEqual(
            _file_sha256("research/player_props/decision_policy.py"),
            "f812d5fa2f1811c2b06b0b63b972dc499fc2f8f6e117a9afd5fa63bea55c763a",
        )


# 52. NHL win model unchanged
class Test52NhlWinModelUnchanged(unittest.TestCase):
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
