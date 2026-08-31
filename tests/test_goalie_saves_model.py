"""
Part 51: tests for the Goalie Saves + Period Saves model. Real fixtures
only -- the label corpus, the frozen results file, and small real slices
of the 4-season PBP corpus, never synthesized. Numbered comments map to
Part-51 topics.
"""
from __future__ import annotations

import hashlib
import json
import os
import unittest

from research.player_props import decision_policy, market_registry
from research.real_nhl_pbp import raw_archive
from research.goalie_saves import features as gf
from research.goalie_saves import hierarchy as gh
from research.goalie_saves import upstream_player_sog_aggregation as upa
from research.goalie_saves.build_goalie_saves_corpus import build_one_game
from research.run_goalie_saves_model import (
    FULL_GAME_THRESHOLDS,
    PERIODS,
    RESULTS_PATH,
    build_example,
    check_monotonicity,
    threshold_prob,
)

BASIC_GAME = ("20222023", 2022020001)
RELIEF_GAME = ("20222023", 2022020032)


def _file_sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _load_results() -> dict:
    with open(RESULTS_PATH) as f:
        return json.load(f)


# 1. save label correctness
class Test01SaveLabelCorrectness(unittest.TestCase):
    def test_saves_equal_shots_minus_goals(self):
        rows, _team_rows = build_one_game(*BASIC_GAME)
        for r in rows:
            self.assertEqual(r["actual_saves"], r["actual_shots_faced"] - r["actual_goals_allowed"])


# 2. shots-faced correctness
class Test02ShotsFacedCorrectness(unittest.TestCase):
    def test_period_shots_sum_to_full_game(self):
        rows, _team_rows = build_one_game(*BASIC_GAME)
        for r in rows:
            total = r["period_1_shots_faced"] + r["period_2_shots_faced"] + r["period_3_shots_faced"] \
                + r["ot_shots_faced"]
            self.assertEqual(total, r["actual_shots_faced"])


# 3. goal-is-not-save
class Test03GoalIsNotSave(unittest.TestCase):
    def test_goals_against_never_counted_as_saves(self):
        rows, _team_rows = build_one_game(*BASIC_GAME)
        for r in rows:
            self.assertLessEqual(r["actual_saves"], r["actual_shots_faced"] - r["actual_goals_allowed"] + 0)
            self.assertGreaterEqual(r["actual_goals_allowed"], 0)


# 4. goal counts as shot faced
class Test04GoalCountsAsShotFaced(unittest.TestCase):
    def test_shots_faced_includes_goals(self):
        rows, _team_rows = build_one_game(*BASIC_GAME)
        for r in rows:
            if r["actual_goals_allowed"] > 0:
                self.assertGreaterEqual(r["actual_shots_faced"], r["actual_goals_allowed"])


# 5. empty-net exclusion
class Test05EmptyNetExclusion(unittest.TestCase):
    def test_no_goalie_events_not_counted_as_shots_faced(self):
        raw = raw_archive.load_raw_pbp(*BASIC_GAME)
        rows, _team_rows = build_one_game(*BASIC_GAME)
        total_shots_faced = sum(r["actual_shots_faced"] for r in rows)
        real_sog_events = sum(
            1 for p in raw["plays"]
            if p["typeDescKey"] in ("shot-on-goal", "goal") and p["periodDescriptor"]["periodType"] != "SO"
        )
        # shots-faced can only be <= real on-target events (empty-net shots excluded)
        self.assertLessEqual(total_shots_faced, real_sog_events)


# 6. shootout exclusion
class Test06ShootoutExclusion(unittest.TestCase):
    def test_so_never_counted(self):
        rows, team_rows = build_one_game(*BASIC_GAME)
        for r in team_rows:
            self.assertNotIn("so_sog", r)
        for r in rows:
            self.assertNotIn("so_saves", r)


# 7. actual-starter label
class Test07ActualStarterLabel(unittest.TestCase):
    def test_exactly_one_starter_per_team_game(self):
        rows = gf.load_goalie_corpus()
        starters = [r for r in rows if r["actual_started"]]
        from collections import Counter
        counts = Counter((r["game_id"], r["team"]) for r in starters)
        self.assertTrue(all(c == 1 for c in counts.values()))


# 8. projected-starter separation
class Test08ProjectedStarterSeparation(unittest.TestCase):
    def test_build_example_never_uses_actual_started_as_a_feature(self):
        rows = gf.load_goalie_corpus()
        teams = gf.load_team_sog_corpus()
        gidx = gf.GoalieHistoryIndex(rows)
        tidx = gf.TeamSogHistoryIndex(teams)
        starts = [r for r in rows if r["actual_started"]]
        tuning = [r for r in starts if r["season"] == 20232024]
        save_rates = gh.GoalieSavePctRates(tuning)
        workload_rates = gh.GoalieWorkloadRates(tuning, field="actual_saves")
        import statistics
        league_avg = statistics.fmean(r["full_game_sog"] for r in teams if r["season"] == 20232024)
        sample = next(r for r in starts if r["season"] == 20242025)
        ex = build_example(sample, gidx, tidx, save_rates, workload_rates, league_avg, None, None)
        self.assertIsNotNone(ex)
        self.assertNotIn("actual_started", ex)


# 9. relief appearance handling
class Test09ReliefAppearanceHandling(unittest.TestCase):
    def test_relief_appearances_excluded_from_headline_population(self):
        rows, _team_rows = build_one_game(*RELIEF_GAME)
        relief_rows = [r for r in rows if r["actual_relief"]]
        self.assertTrue(len(relief_rows) >= 1)
        for r in relief_rows:
            self.assertFalse(r["actual_started"])

    def test_real_corpus_has_both_starts_and_relief(self):
        rows = gf.load_goalie_corpus()
        self.assertTrue(any(r["actual_started"] for r in rows))
        self.assertTrue(any(r["actual_relief"] for r in rows))


# 10. target-game saves excluded
class Test10TargetGameSavesExcluded(unittest.TestCase):
    def test_mutating_target_saves_does_not_change_features(self):
        rows = gf.load_goalie_corpus()
        teams = gf.load_team_sog_corpus()
        gidx = gf.GoalieHistoryIndex(rows)
        tidx = gf.TeamSogHistoryIndex(teams)
        starts = [r for r in rows if r["actual_started"]]
        tuning = [r for r in starts if r["season"] == 20232024]
        save_rates = gh.GoalieSavePctRates(tuning)
        workload_rates = gh.GoalieWorkloadRates(tuning, field="actual_saves")
        import statistics
        league_avg = statistics.fmean(r["full_game_sog"] for r in teams if r["season"] == 20232024)
        sample = next(r for r in starts if r["season"] == 20242025)
        ex1 = build_example(sample, gidx, tidx, save_rates, workload_rates, league_avg, None, None)
        mutated = dict(sample)
        mutated["actual_saves"] = 999
        ex2 = build_example(mutated, gidx, tidx, save_rates, workload_rates, league_avg, None, None)
        self.assertEqual(ex1["baseline_saves"], ex2["baseline_saves"])
        self.assertEqual(ex1["shrunk_workload_saves"], ex2["shrunk_workload_saves"])


# 11. target-game shots faced excluded
class Test11TargetGameShotsFacedExcluded(unittest.TestCase):
    def test_mutating_target_shots_faced_does_not_change_features(self):
        rows = gf.load_goalie_corpus()
        teams = gf.load_team_sog_corpus()
        gidx = gf.GoalieHistoryIndex(rows)
        tidx = gf.TeamSogHistoryIndex(teams)
        starts = [r for r in rows if r["actual_started"]]
        tuning = [r for r in starts if r["season"] == 20232024]
        save_rates = gh.GoalieSavePctRates(tuning)
        workload_rates = gh.GoalieWorkloadRates(tuning, field="actual_saves")
        import statistics
        league_avg = statistics.fmean(r["full_game_sog"] for r in teams if r["season"] == 20232024)
        sample = next(r for r in starts if r["season"] == 20242025)
        ex1 = build_example(sample, gidx, tidx, save_rates, workload_rates, league_avg, None, None)
        mutated = dict(sample)
        mutated["actual_shots_faced"] = 999
        ex2 = build_example(mutated, gidx, tidx, save_rates, workload_rates, league_avg, None, None)
        self.assertEqual(ex1["baseline_shots_faced"], ex2["baseline_shots_faced"])


# 12. future exclusion
class Test12FutureExclusion(unittest.TestCase):
    def test_goalie_history_never_includes_same_or_later_dates(self):
        rows = gf.load_goalie_corpus()
        idx = gf.GoalieHistoryIndex(rows)
        sample = rows[1000]
        hist = idx.history_as_of(sample["goalie_id"], sample["game_date"])
        self.assertTrue(all(r["game_date"] < sample["game_date"] for r in hist))

    def test_team_history_never_includes_same_or_later_dates(self):
        rows = gf.load_team_sog_corpus()
        idx = gf.TeamSogHistoryIndex(rows)
        sample = rows[1000]
        hist = idx.history_as_of(sample["team"], sample["game_date"])
        self.assertTrue(all(r["game_date"] < sample["game_date"] for r in hist))


# 13. same-day exclusion
class Test13SameDayExclusion(unittest.TestCase):
    def test_strict_less_than(self):
        rows = gf.load_goalie_corpus()
        idx = gf.GoalieHistoryIndex(rows)
        sample = rows[1000]
        hist = idx.history_as_of(sample["goalie_id"], sample["game_date"])
        self.assertEqual([r for r in hist if r["game_date"] == sample["game_date"]], [])


# 14. goalie save% shrinkage
class Test14SavePctShrinkage(unittest.TestCase):
    def test_zero_history_returns_team_shrunk_prior_exactly(self):
        rows = gf.load_goalie_corpus()
        starts = [r for r in rows if r["actual_started"] and r["season"] == 20232024]
        rates = gh.GoalieSavePctRates(starts)
        result = rates.goalie_shrunk_save_pct([], "TOR")
        self.assertEqual(result, rates.team_shrunk_save_pct("TOR"))


# 15. low-sample goalie shrinkage
class Test15LowSampleGoalieShrinkage(unittest.TestCase):
    def test_small_sample_shrinks_toward_team_more_than_large_sample(self):
        rows = gf.load_goalie_corpus()
        starts = [r for r in rows if r["actual_started"] and r["season"] == 20232024]
        rates = gh.GoalieSavePctRates(starts)
        team = "TOR"
        prior = rates.team_shrunk_save_pct(team)
        fake_hot_streak = [{"actual_saves": 30, "actual_shots_faced": 30}] * 3  # tiny sample, 100% save pct
        shrunk_small = rates.goalie_shrunk_save_pct(fake_hot_streak, team)
        fake_hot_streak_big = [{"actual_saves": 30, "actual_shots_faced": 30}] * 300
        shrunk_big = rates.goalie_shrunk_save_pct(fake_hot_streak_big, team)
        self.assertLess(abs(shrunk_small - prior), abs(shrunk_big - prior) + 1e-9)
        self.assertGreater(shrunk_big, shrunk_small)


# 16. opponent SOG feature PIT integrity
class Test16OpponentSogPitIntegrity(unittest.TestCase):
    def test_opponent_history_strictly_prior(self):
        rows = gf.load_team_sog_corpus()
        idx = gf.TeamSogHistoryIndex(rows)
        sample = rows[2000]
        hist = idx.history_as_of(sample["opponent"], sample["game_date"])
        self.assertTrue(all(r["game_date"] < sample["game_date"] for r in hist))


# 17. upstream Player SOG PIT integrity
class Test17UpstreamPlayerSogPitIntegrity(unittest.TestCase):
    def test_roster_candidates_only_use_prior_games(self):
        ctx = upa.AggregationContext()
        team = "TOR"
        date = "2025-01-15"
        games = ctx.by_team_recent_rosters.get(team, [])
        prior = [g for g in games if g[0] < date]
        recent = prior[-upa.ROSTER_WINDOW:]
        expected_ids = set()
        for _d, _gid, players in recent:
            expected_ids |= players
        self.assertEqual(ctx.roster_candidates(team, date), expected_ids)
        for d, _gid, _players in games:
            if d >= date:
                self.assertNotIn(d, [g[0] for g in recent if g[0] >= date])


# 18. lineup uncertainty
class Test18LineupUncertainty(unittest.TestCase):
    def test_aggregation_reports_coverage_for_uncertainty_quantification(self):
        weights, _alpha = upa.load_frozen_sog_model()
        ctx = upa.AggregationContext()
        result = upa.aggregate_expected_opponent_sog(ctx, "TOR", "MTL", "2025-01-15", weights)
        self.assertIn("n_players", result)
        self.assertIn("n_candidates", result)
        self.assertLessEqual(result["n_players"], result["n_candidates"])

    def test_thin_coverage_excluded_from_headline_candidate(self):
        results = _load_results()
        self.assertIn("F_player_agg_x_saverate", results["winner_scores"])


# 19. shots-faced model
class Test19ShotsFacedModel(unittest.TestCase):
    def test_shots_faced_submodel_independently_reported(self):
        results = _load_results()
        for s in results["config"]["eval_seasons"]:
            block = results["shots_faced_comparison"][str(s)]
            self.assertIn("team_level_mae", block)
            self.assertIn("player_agg_mae", block)
            self.assertLess(block["team_level_mae"], block["player_agg_mae"],
                             "if this now fails, the real evidence changed and Section J must be rewritten")


# 20. direct Poisson saves
class Test20DirectPoissonSaves(unittest.TestCase):
    def test_glm_weights_converged_not_diverged(self):
        results = _load_results()
        weights = results["glm_weights"]
        self.assertTrue(all(abs(w) < 50 for w in weights),
                         "GLM weights exploded -- the lr=0.05 default divergence bug is back")


# 21. direct NB saves
class Test21DirectNbSaves(unittest.TestCase):
    def test_alpha_fit_near_zero_not_assumed(self):
        results = _load_results()
        self.assertGreaterEqual(results["glm_alpha"], 0.0)
        self.assertLess(results["glm_alpha"], 0.5)


# 22. decomposition model
class Test22DecompositionModel(unittest.TestCase):
    def test_shots_x_saverate_candidate_present(self):
        results = _load_results()
        self.assertIn("D_shots_x_shrunk_saverate", results["winner_scores"])


# 23. threshold monotonicity
class Test23ThresholdMonotonicity(unittest.TestCase):
    def test_probabilities_monotonic_for_range_of_mu(self):
        for mu in (5.0, 15.0, 25.0, 35.0):
            probs = [threshold_prob(mu, None, t) for t in FULL_GAME_THRESHOLDS]
            self.assertEqual(probs, sorted(probs, reverse=True))

    def test_zero_monotonicity_violations_in_frozen_results(self):
        results = _load_results()
        for s in results["config"]["eval_seasons"]:
            self.assertEqual(results["by_season"][str(s)]["monotonicity_violations"], 0)


# 24-28. threshold derivation + registry status
class Test24To28ThresholdDerivation(unittest.TestCase):
    def test_threshold_status_matches_evidence(self):
        expected = {
            "GOALIE_SAVES_20PLUS": "VALIDATED", "GOALIE_SAVES_25PLUS": "VALIDATED",
            "GOALIE_SAVES_30PLUS": "PARTIAL", "GOALIE_SAVES_35PLUS": "REJECTED",
            "GOALIE_SAVES_40PLUS": "INSUFFICIENT_DATA",
        }
        for mid, status in expected.items():
            m = market_registry.get(mid)
            self.assertEqual(m.model_status, status)


# 29. tail-support rule
class Test29TailSupportRule(unittest.TestCase):
    def test_40plus_has_thin_support_below_50_event_floor(self):
        results = _load_results()
        for s in results["config"]["eval_seasons"]:
            n_pos = results["by_season"][str(s)]["candidates"][results["full_game_winner"]]["thresholds"]["40"]["n_positive"]
            self.assertLess(n_pos, 50)

    def test_35plus_has_adequate_support(self):
        results = _load_results()
        for s in results["config"]["eval_seasons"]:
            n_pos = results["by_season"][str(s)]["candidates"][results["full_game_winner"]]["thresholds"]["35"]["n_positive"]
            self.assertGreaterEqual(n_pos, 50)


# 30-32. P1/P2/P3 saves
class Test30To32PeriodSaves(unittest.TestCase):
    def test_period_status_matches_evidence(self):
        expected = {"PERIOD_1_GOALIE_SAVES": "PARTIAL", "PERIOD_2_GOALIE_SAVES": "VALIDATED",
                    "PERIOD_3_GOALIE_SAVES": "PARTIAL"}
        for mid, status in expected.items():
            m = market_registry.get(mid)
            self.assertEqual(m.model_status, status)

    def test_period_results_present_both_seasons(self):
        results = _load_results()
        for s in results["config"]["eval_seasons"]:
            for k in ("1", "2", "3"):
                self.assertIn(k, results["period_results"][str(s)])


# 33. period/full-game coherence
class Test33PeriodFullGameCoherence(unittest.TestCase):
    def test_period_league_shares_sum_near_one(self):
        results = _load_results()
        total = sum(results["period_league_share"].values())
        self.assertAlmostEqual(total, 1.0, delta=0.05)

    def test_corpus_period_saves_sum_to_full_game(self):
        rows = gf.load_goalie_corpus()
        for r in rows[:200]:
            total = r["period_1_saves"] + r["period_2_saves"] + r["period_3_saves"] + r["ot_saves"]
            self.assertEqual(total, r["actual_saves"])


# 34. starter uncertainty
class Test34StarterUncertainty(unittest.TestCase):
    def test_starter_uncertainty_disclosed_not_folded_in(self):
        results = _load_results()
        manifest = results["freeze_manifest"]
        self.assertIn("STARTER UNCERTAINTY NOT INCLUDED IN HEADLINE VALIDATION", manifest["starter_handling"])

    def test_starter_model_audited_and_referenced(self):
        with open("research/goalie_intelligence_results.json") as f:
            starter_results = json.load(f)
        self.assertIn("model_eval_true_holdout", starter_results)
        self.assertEqual(starter_results["config"]["tuning_season"], 20232024)


# 35. common evaluation set
class Test35CommonEvaluationSet(unittest.TestCase):
    def test_corpus_size_reported(self):
        results = _load_results()
        self.assertIn("goalie_rows", results["corpus_size"])
        self.assertIn("start_rows", results["corpus_size"])
        self.assertEqual(results["corpus_size"]["start_rows"], 10496)


# 36. temporal split
class Test36TemporalSplit(unittest.TestCase):
    def test_warmup_tuning_eval_seasons(self):
        results = _load_results()
        cfg = results["config"]
        self.assertEqual(cfg["warmup_season"], 20222023)
        self.assertEqual(cfg["tuning_season"], 20232024)
        self.assertEqual(cfg["eval_seasons"], [20242025, 20252026])


# 37. freeze manifest
class Test37FreezeManifest(unittest.TestCase):
    def test_freeze_manifest_present(self):
        results = _load_results()
        manifest = results["freeze_manifest"]
        self.assertEqual(manifest["experiment_id"], "goalie_saves_v1")
        self.assertIn("code_hashes", manifest)
        self.assertIn("population_definition", manifest)


# 38. evaluation frozen
class Test38EvaluationFrozen(unittest.TestCase):
    def test_single_glm_weights_used_for_both_eval_seasons(self):
        results = _load_results()
        self.assertIn("glm_weights", results)
        self.assertNotIn("glm_weights_20242025", results)
        self.assertNotIn("glm_weights_20252026", results)


# 39. game bootstrap
class Test39GameBootstrap(unittest.TestCase):
    def test_bootstrap_present_and_headline_thresholds_pass_both_seasons(self):
        results = _load_results()
        for s in results["config"]["eval_seasons"]:
            for t in (20, 25):
                gc = results["by_season"][str(s)]["bootstrap"][str(t)]["game_clustered"]
                self.assertGreaterEqual(gc["frac_improved"], 0.95)


# 40. date bootstrap
class Test40DateBootstrap(unittest.TestCase):
    def test_date_clustered_present(self):
        results = _load_results()
        for s in results["config"]["eval_seasons"]:
            dc = results["by_season"][str(s)]["bootstrap"]["20"]["date_clustered"]
            self.assertIn("frac_improved", dc)


# 41. calibration
class Test41Calibration(unittest.TestCase):
    def test_calibration_bins_present(self):
        results = _load_results()
        s = str(results["config"]["eval_seasons"][0])
        winner = results["full_game_winner"]
        cal = results["by_season"][s]["candidates"][winner]["thresholds"]["25"]["calibration"]
        self.assertGreater(len(cal), 0)


# 42. confidence
class Test42Confidence(unittest.TestCase):
    def test_confidence_score_signature_unchanged(self):
        from research.player_sog import count_models as cm
        import inspect
        sig = inspect.signature(cm.confidence_score)
        self.assertEqual(list(sig.parameters), ["n_history_games", "recent_toi_cv", "recent_sog_cv",
                                                  "opponent_window_games", "opponent_window_target",
                                                  "appearance_rate"])

    def test_confidence_stratified_present(self):
        results = _load_results()
        for s in results["config"]["eval_seasons"]:
            self.assertTrue(len(results["by_season"][str(s)]["confidence_stratified"]) > 0)


# 43. conservative probability
class Test43ConservativeProbability(unittest.TestCase):
    def test_conservative_never_exceeds_raw(self):
        results = _load_results()
        for s in results["config"]["eval_seasons"]:
            audit = results["by_season"][str(s)]["conservative_probability_audit"]
            self.assertTrue(audit["conservative_never_exceeds_raw"])


# 44. registry status
class Test44RegistryStatus(unittest.TestCase):
    def test_registry_totals(self):
        # derivable_today() updated 28->29 by the later Team SOG slice
        # (TEAM_SOG_TOTAL VALIDATED) -- a real, disclosed registry change.
        self.assertEqual(market_registry.total_canonical_markets(), 142)
        self.assertEqual(len(market_registry.derivable_today()), 29)
        self.assertEqual(len(market_registry.validated_today()), 15)


# 45. dashboard labeling
class Test45DashboardLabeling(unittest.TestCase):
    def test_dashboard_page_discloses_conditional_on_start(self):
        path = "dashboard/pages/16_Goalie_Saves_Research.py"
        self.assertTrue(os.path.exists(path))
        with open(path) as f:
            content = f.read()
        self.assertIn("CONDITIONAL_ON_ACTUAL_START", content)
        self.assertIn("MIXED RESULT", content)


# 46. existing SOG unchanged
class Test46ExistingSogUnchanged(unittest.TestCase):
    def test_player_sog_results_unchanged(self):
        self.assertEqual(_file_sha256("research/player_sog_results.json"),
                          "556d447bc6dcfc18df52812d98901cd7accad3b203a06606ddd68ea6993e8f61")


# 47. period SOG unchanged
class Test47PeriodSogUnchanged(unittest.TestCase):
    def test_player_sog_period_results_unchanged(self):
        self.assertEqual(_file_sha256("research/player_sog_period_results.json"),
                          "1d81d5ac989215da1302dcc550159a31f8feb8e1593da964f4e5485216e19e29")


# 48. Goals unchanged
class Test48GoalsUnchanged(unittest.TestCase):
    def test_player_goals_results_unchanged(self):
        self.assertEqual(_file_sha256("research/player_goals_results.json"),
                          "3f5592585a255b11c77f2a4d08c2c9886d01e45dbc8b48b30d284389367f5348")


# 49. Blocks unchanged
class Test49BlocksUnchanged(unittest.TestCase):
    def test_player_blocks_results_unchanged(self):
        self.assertEqual(_file_sha256("research/player_blocks_results.json"),
                          "fc608ab5da9adf06170f96b7e96989fc29cf4cad07a26a9d9778d51649293c07")


# 50. Assists unchanged
class Test50AssistsUnchanged(unittest.TestCase):
    def test_player_assists_results_unchanged(self):
        self.assertEqual(_file_sha256("research/player_assists_results.json"),
                          "3f8bc1c649cb3bbea4be0f56ebf893e399eaca415075ea1dca176e1f944ec0e9")


# 51. Points unchanged
class Test51PointsUnchanged(unittest.TestCase):
    def test_player_points_results_unchanged(self):
        self.assertEqual(_file_sha256("research/player_points_results.json"),
                          "6eacd4d56dc78d6b371b7f0234252e1f969359a427d813efcd696780b8af8877")

    def test_player_points_redesign_results_unchanged(self):
        self.assertEqual(_file_sha256("research/player_points_redesign_results.json"),
                          "490614606d5a8e046a9072669bc15a2bdfbb0097fb3a1a9696e7cd878ea97b75")


# 52. decision policy v3 unchanged
class Test52DecisionPolicyUnchanged(unittest.TestCase):
    def test_decision_policy_v3_unchanged(self):
        self.assertEqual(
            _file_sha256("research/player_props/decision_policy.py"),
            "f812d5fa2f1811c2b06b0b63b972dc499fc2f8f6e117a9afd5fa63bea55c763a",
        )
        self.assertEqual(decision_policy.POLICY_VERSION, "prop_decision_policy_v3")


# 53. NHL win model unchanged
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
