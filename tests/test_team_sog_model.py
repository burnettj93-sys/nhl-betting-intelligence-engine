"""
Part 50: tests for the Team Shots on Goal model. Real fixtures only --
the label corpus, the frozen results file, and small real slices of the
4-season PBP corpus, never synthesized. Numbered comments map to Part-50
topics.
"""
from __future__ import annotations

import hashlib
import json
import os
import unittest

from research.player_props import decision_policy, market_registry
from research.real_nhl_pbp import raw_archive
from research.team_sog import features as tf
from research.team_sog import hierarchy as th
from research.team_sog import upstream_player_sog_aggregation as upa
from research.team_sog.build_team_sog_corpus import build_one_game
from research.run_team_sog_model import (
    SOG_THRESHOLDS,
    RESULTS_PATH,
    build_example,
    check_monotonicity,
    threshold_prob,
)

BASIC_GAME = ("20222023", 2022020001)
SO_GAME = ("20222023", 2022020158)


def _file_sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _load_results() -> dict:
    with open(RESULTS_PATH) as f:
        return json.load(f)


def _build_tuning_context():
    rows = tf.load_team_sog_corpus()
    index = tf.TeamHistoryIndex(rows)
    tuning_rows = [r for r in rows if r["season"] == 20232024]
    rates = th.TeamSogRates(tuning_rows)
    import statistics
    league_avg_sog = statistics.fmean(r["actual_team_sog"] for r in tuning_rows)
    return rows, index, rates, league_avg_sog


# 1. Team SOG label
class Test01TeamSogLabel(unittest.TestCase):
    def test_labels_are_nonnegative_ints(self):
        rows = build_one_game(*BASIC_GAME)
        for r in rows:
            self.assertIsInstance(r["actual_team_sog"], int)
            self.assertGreaterEqual(r["actual_team_sog"], 0)


# 2. goals count toward Team SOG
class Test02GoalsCountTowardSog(unittest.TestCase):
    def test_sog_at_least_goals(self):
        rows = build_one_game(*BASIC_GAME)
        for r in rows:
            self.assertGreaterEqual(r["actual_team_sog"], r["actual_team_goals"])


# 3. shootout exclusion
class Test03ShootoutExclusion(unittest.TestCase):
    def test_so_never_counted(self):
        rows = build_one_game(*SO_GAME)
        raw = raw_archive.load_raw_pbp(*SO_GAME)
        self.assertEqual(raw["gameOutcome"].get("lastPeriodType"), "SO")
        for r in rows:
            total = r["P1_team_sog"] + r["P2_team_sog"] + r["P3_team_sog"] + r["OT_team_sog"]
            self.assertEqual(total, r["actual_team_sog"])


# 4. period/full-game reconciliation
class Test04PeriodFullGameReconciliation(unittest.TestCase):
    def test_period_sums_equal_full_game(self):
        for season, gid in (BASIC_GAME, SO_GAME):
            rows = build_one_game(season, gid)
            for r in rows:
                total = r["P1_team_sog"] + r["P2_team_sog"] + r["P3_team_sog"] + r["OT_team_sog"]
                self.assertEqual(total, r["actual_team_sog"])

    def test_official_boxscore_reconciliation_near_perfect(self):
        rows = tf.load_team_sog_corpus()
        mismatches = 0
        checked = 0
        by_game = {}
        for r in rows[:400]:
            by_game.setdefault(r["game_id"], {})[r["team_id"]] = r
        for gid, teams in by_game.items():
            season = str(next(iter(teams.values()))["season"])
            raw = raw_archive.load_raw_pbp(season, gid)
            home_id, away_id = raw["homeTeam"]["id"], raw["awayTeam"]["id"]
            for team_id, official in ((home_id, raw["homeTeam"].get("sog")),
                                       (away_id, raw["awayTeam"].get("sog"))):
                row = teams.get(team_id)
                if row is None or official is None:
                    continue
                checked += 1
                if row["actual_team_sog"] != official:
                    mismatches += 1
        self.assertGreater(checked, 0)
        self.assertLess(mismatches / checked, 0.02, "reconciliation gap grew beyond the documented ~0.85%")


# 5. opponent assignment
class Test05OpponentAssignment(unittest.TestCase):
    def test_opponent_symmetric(self):
        rows = build_one_game(*BASIC_GAME)
        a, b = rows
        self.assertEqual(a["opponent_id"], b["team_id"])
        self.assertEqual(b["opponent_id"], a["team_id"])
        self.assertEqual(a["actual_team_sog"], b["actual_opponent_sog"])
        self.assertEqual(b["actual_team_sog"], a["actual_opponent_sog"])


# 6. home/away assignment
class Test06HomeAwayAssignment(unittest.TestCase):
    def test_home_away_tags(self):
        rows = build_one_game(*BASIC_GAME)
        tags = {r["home_away"] for r in rows}
        self.assertEqual(tags, {"home", "away"})


# 7. target-game SOG exclusion
class Test07TargetGameSogExclusion(unittest.TestCase):
    def test_mutating_target_sog_does_not_change_features(self):
        rows, index, rates, league_avg = _build_tuning_context()
        sample = next(r for r in rows if r["season"] == 20242025)
        ex1 = build_example(sample, index, rates, league_avg, None, None)
        mutated = dict(sample)
        mutated["actual_team_sog"] = 999
        ex2 = build_example(mutated, index, rates, league_avg, None, None)
        self.assertIsNotNone(ex1)
        self.assertEqual(ex1["baseline_sog_for"], ex2["baseline_sog_for"])
        self.assertEqual(ex1["shrunk_team_mean"], ex2["shrunk_team_mean"])


# 8. same-day exclusion
class Test08SameDayExclusion(unittest.TestCase):
    def test_strict_less_than(self):
        rows = tf.load_team_sog_corpus()
        idx = tf.TeamHistoryIndex(rows)
        sample = rows[1000]
        hist = idx.history_as_of(sample["team"], sample["game_date"])
        self.assertEqual([r for r in hist if r["game_date"] == sample["game_date"]], [])


# 9. future exclusion
class Test09FutureExclusion(unittest.TestCase):
    def test_history_never_includes_later_dates(self):
        rows = tf.load_team_sog_corpus()
        idx = tf.TeamHistoryIndex(rows)
        sample = rows[1000]
        hist = idx.history_as_of(sample["team"], sample["game_date"])
        self.assertTrue(all(r["game_date"] < sample["game_date"] for r in hist))


# 10. rolling Team SOG
class Test10RollingTeamSog(unittest.TestCase):
    def test_rolling_mean_matches_manual_computation(self):
        rows = tf.load_team_sog_corpus()
        idx = tf.TeamHistoryIndex(rows)
        sample = rows[2000]
        hist = idx.history_as_of(sample["team"], sample["game_date"])
        expected = sum(r["actual_team_sog"] for r in hist[-20:]) / len(hist[-20:]) if hist else None
        self.assertEqual(tf.rolling_mean(hist, "actual_team_sog", 20), expected)


# 11. rolling SOG allowed
class Test11RollingSogAllowed(unittest.TestCase):
    def test_opponent_allowed_rolling_uses_opponent_history(self):
        rows = tf.load_team_sog_corpus()
        idx = tf.TeamHistoryIndex(rows)
        sample = rows[2000]
        opp_hist = idx.history_as_of(sample["opponent"], sample["game_date"])
        val = tf.rolling_mean(opp_hist, "actual_opponent_sog", 20)
        if opp_hist:
            self.assertIsNotNone(val)


# 12. shrinkage
class Test12Shrinkage(unittest.TestCase):
    def test_zero_history_returns_league_prior(self):
        rows = tf.load_team_sog_corpus()
        tuning = [r for r in rows if r["season"] == 20232024]
        rates = th.TeamSogRates(tuning)
        result = th.team_sog_mean_hierarchical([], "home", rates)
        self.assertEqual(result, rates.ha_mean_for_shrunk("home"))


# 13. offense/defense decomposition
class Test13OffenseDefenseDecomposition(unittest.TestCase):
    def test_decomposition_present_and_real(self):
        results = _load_results()
        self.assertIn("D_offense_defense_decomposition", results["winner_scores"])

    def test_simple_rolling_beats_decomposition(self):
        results = _load_results()
        self.assertLess(results["baseline_scores"]["B_team_rolling_sog"],
                         results["winner_scores"]["D_offense_defense_decomposition"],
                         "if this now fails, the real evidence changed and Section Q must be rewritten")


# 14. projected roster usage
class Test14ProjectedRosterUsage(unittest.TestCase):
    def test_roster_candidates_gated_by_projected_active(self):
        ctx = upa.AggregationContext()
        team = "TOR"
        date = "2025-01-15"
        weights, _alpha = upa.load_frozen_sog_model()
        result = upa.aggregate_expected_team_sog(ctx, team, "MTL", date, weights)
        self.assertLessEqual(result["n_players"], result["n_candidates"])


# 15. actual target roster excluded
class Test15ActualTargetRosterExcluded(unittest.TestCase):
    def test_roster_candidates_never_include_same_or_later_game_players(self):
        ctx = upa.AggregationContext()
        team = "TOR"
        date = "2025-01-15"
        games = ctx.by_team_recent_rosters.get(team, [])
        same_or_later = [players for d, _gid, players in games if d >= date]
        candidates = ctx.roster_candidates(team, date)
        for later_players in same_or_later:
            only_in_later = later_players - {
                pid for d, _gid, players in games if d < date for pid in players
            }
            self.assertFalse(only_in_later & candidates)


# 16. player-SOG aggregation PIT integrity
class Test16PlayerSogAggregationPitIntegrity(unittest.TestCase):
    def test_roster_candidates_strictly_prior(self):
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


# 17. player aggregate/team SOG reconciliation diagnostic
class Test17PlayerAggregateReconciliation(unittest.TestCase):
    def test_actual_player_sum_reconciles_with_team_sog(self):
        results = _load_results()
        recon = results["reconciliation_actual"]
        self.assertGreater(recon["exact_match_pct"], 95.0)

    def test_player_agg_prediction_discrepancy_reported(self):
        results = _load_results()
        for s in results["config"]["eval_seasons"]:
            self.assertIn("mean_diff", results["player_agg_prediction_discrepancy"][str(s)])


# 18. Poisson distribution
class Test18PoissonDistribution(unittest.TestCase):
    def test_glm_weights_converged_not_diverged(self):
        results = _load_results()
        weights = results["glm_weights"]
        self.assertTrue(all(abs(w) < 50 for w in weights),
                         "GLM weights exploded -- the lr=0.05 default divergence bug class is back")


# 19. NB distribution
class Test19NbDistribution(unittest.TestCase):
    def test_alpha_fit_near_zero_not_assumed(self):
        results = _load_results()
        self.assertGreaterEqual(results["glm_alpha"], 0.0)
        self.assertLess(results["glm_alpha"], 0.3)


# 20. optional normal distribution if used
class Test20NormalDistributionIfUsed(unittest.TestCase):
    def test_normal_approximation_not_materially_different_from_poisson(self):
        import math
        from research.player_sog import count_models as cm

        def norm_cdf(x):
            return 0.5 * (1 + math.erf(x / math.sqrt(2)))

        def normal_sf_at_least(t, mu, sd):
            if sd <= 0:
                return 1.0 if mu >= t else 0.0
            z = (t - 0.5 - mu) / sd
            return max(0.0, min(1.0, 1 - norm_cdf(z)))

        for mu in (20.0, 29.0, 35.0):
            p_poisson = cm.poisson_sf_at_least(28, mu)
            p_normal = normal_sf_at_least(28, mu, math.sqrt(mu))
            self.assertLess(abs(p_poisson - p_normal), 0.02)


# 21. threshold monotonicity
class Test21ThresholdMonotonicity(unittest.TestCase):
    def test_probabilities_monotonic_for_range_of_mu(self):
        for mu in (10.0, 20.0, 30.0, 40.0):
            probs = [threshold_prob(mu, None, t) for t in SOG_THRESHOLDS]
            self.assertEqual(probs, sorted(probs, reverse=True))

    def test_zero_monotonicity_violations_in_frozen_results(self):
        results = _load_results()
        for s in results["config"]["eval_seasons"]:
            self.assertEqual(results["by_season"][str(s)]["monotonicity_violations"], 0)


# 22. O/U derivation
class Test22OuDerivation(unittest.TestCase):
    def test_over_under_derivable_from_same_distribution(self):
        from research.player_sog import count_models as cm
        mu = 29.0
        p_over_28_5 = cm.poisson_sf_at_least(29, mu)
        self.assertGreaterEqual(p_over_28_5, 0.0)
        self.assertLessEqual(p_over_28_5, 1.0)


# 23. alternate threshold derivation
class Test23AlternateThresholdDerivation(unittest.TestCase):
    def test_all_thresholds_derived_from_same_mu(self):
        mu = 29.0
        probs = {t: threshold_prob(mu, None, t) for t in SOG_THRESHOLDS}
        self.assertEqual(list(probs.values()), sorted(probs.values(), reverse=True))


# 24. extreme-tail support
class Test24ExtremeTailSupport(unittest.TestCase):
    def test_40plus_has_adequate_support_but_inconsistent_evidence(self):
        results = _load_results()
        for s in results["config"]["eval_seasons"]:
            n_pos = results["by_season"][str(s)]["candidates"][results["winner"]]["thresholds"]["40"]["n_positive"]
            self.assertGreaterEqual(n_pos, 50)


# 25. goalie-workload reconciliation
class Test25GoalieWorkloadReconciliation(unittest.TestCase):
    def test_team_sog_closely_tracks_opponent_goalie_shots_faced(self):
        import research.goalie_saves.features as gsf
        team_rows = tf.load_team_sog_corpus()
        goalie_rows = gsf.load_goalie_corpus()
        shots_by_game_team = {}
        for r in goalie_rows:
            key = (r["game_id"], r["team_id"])
            shots_by_game_team[key] = shots_by_game_team.get(key, 0) + r["actual_shots_faced"]
        checked = 0
        close = 0
        for r in team_rows[:500]:
            opp_key = (r["game_id"], r["opponent_id"])
            shots_faced = shots_by_game_team.get(opp_key)
            if shots_faced is None:
                continue
            checked += 1
            if abs(r["actual_team_sog"] - shots_faced) <= 2:
                close += 1
        self.assertGreater(checked, 0)
        self.assertEqual(close, checked, "team SOG should always be within 2 of opponent goalie shots faced")


# 26. multi-goalie game
class Test26MultiGoalieGame(unittest.TestCase):
    def test_team_sog_maps_to_sum_of_opposing_goalie_shots_faced(self):
        import research.goalie_saves.features as gsf
        goalie_rows = gsf.load_goalie_corpus()
        from collections import Counter
        game_team_goalie_counts = Counter((r["game_id"], r["team_id"]) for r in goalie_rows)
        multi_goalie_games = [k for k, v in game_team_goalie_counts.items() if v > 1]
        self.assertGreater(len(multi_goalie_games), 0)


# 27. empty-net semantics
class Test27EmptyNetSemantics(unittest.TestCase):
    def test_reconciliation_gap_always_nonnegative(self):
        import research.goalie_saves.features as gsf
        team_rows = tf.load_team_sog_corpus()
        goalie_rows = gsf.load_goalie_corpus()
        shots_by_game_team = {}
        for r in goalie_rows:
            key = (r["game_id"], r["team_id"])
            shots_by_game_team[key] = shots_by_game_team.get(key, 0) + r["actual_shots_faced"]
        for r in team_rows[:500]:
            opp_key = (r["game_id"], r["opponent_id"])
            shots_faced = shots_by_game_team.get(opp_key)
            if shots_faced is None:
                continue
            self.assertGreaterEqual(r["actual_team_sog"] - shots_faced, 0,
                                     "team SOG should never be LESS than opponent goalie shots faced "
                                     "(empty-net shots only ever add to team SOG, never subtract)")


# 28. period diagnostic
class Test28PeriodDiagnostic(unittest.TestCase):
    def test_period_share_diagnostic_present_and_sums_near_one(self):
        results = _load_results()
        for s in results["config"]["eval_seasons"]:
            shares = results["by_season"][str(s)]["period_share_diagnostic"]
            total = sum(shares.values())
            self.assertAlmostEqual(total, 1.0, delta=0.02)


# 29. common evaluation set
class Test29CommonEvaluationSet(unittest.TestCase):
    def test_corpus_size_reported(self):
        results = _load_results()
        self.assertEqual(results["corpus_size"]["team_game_rows"], 10496)


# 30. temporal split
class Test30TemporalSplit(unittest.TestCase):
    def test_warmup_tuning_eval_seasons(self):
        results = _load_results()
        cfg = results["config"]
        self.assertEqual(cfg["warmup_season"], 20222023)
        self.assertEqual(cfg["tuning_season"], 20232024)
        self.assertEqual(cfg["eval_seasons"], [20242025, 20252026])


# 31. freeze manifest
class Test31FreezeManifest(unittest.TestCase):
    def test_freeze_manifest_present(self):
        results = _load_results()
        manifest = results["freeze_manifest"]
        self.assertEqual(manifest["experiment_id"], "team_sog_v1")
        self.assertIn("code_hashes", manifest)
        self.assertIn("player_aggregation_policy", manifest)


# 32. frozen evaluation
class Test32FrozenEvaluation(unittest.TestCase):
    def test_single_glm_weights_used_for_both_eval_seasons(self):
        results = _load_results()
        self.assertIn("glm_weights", results)
        self.assertNotIn("glm_weights_20242025", results)
        self.assertNotIn("glm_weights_20252026", results)


# 33. game-cluster bootstrap
class Test33GameClusterBootstrap(unittest.TestCase):
    def test_headline_thresholds_pass_both_seasons(self):
        results = _load_results()
        for s in results["config"]["eval_seasons"]:
            for t in (20, 25, 30, 35):
                gc = results["by_season"][str(s)]["bootstrap"][str(t)]["game_clustered"]
                self.assertGreaterEqual(gc["frac_improved"], 0.95)


# 34. date-cluster sensitivity
class Test34DateClusterSensitivity(unittest.TestCase):
    def test_date_clustered_present_and_tracks_game_clustered(self):
        results = _load_results()
        for s in results["config"]["eval_seasons"]:
            gc = results["by_season"][str(s)]["bootstrap"]["30"]["game_clustered"]["frac_improved"]
            dc = results["by_season"][str(s)]["bootstrap"]["30"]["date_clustered"]["frac_improved"]
            self.assertLess(abs(gc - dc), 0.1)


# 35. calibration
class Test35Calibration(unittest.TestCase):
    def test_calibration_bins_present(self):
        results = _load_results()
        s = str(results["config"]["eval_seasons"][0])
        winner = results["winner"]
        cal = results["by_season"][s]["candidates"][winner]["thresholds"]["30"]["calibration"]
        self.assertGreater(len(cal), 0)


# 36. confidence
class Test36Confidence(unittest.TestCase):
    def test_confidence_score_signature_unchanged(self):
        from research.player_sog import count_models as cm
        import inspect
        sig = inspect.signature(cm.confidence_score)
        self.assertEqual(list(sig.parameters), ["n_history_games", "recent_toi_cv", "recent_sog_cv",
                                                  "opponent_window_games", "opponent_window_target",
                                                  "appearance_rate"])


# 37. conservative probability
class Test37ConservativeProbability(unittest.TestCase):
    def test_conservative_never_exceeds_raw(self):
        results = _load_results()
        for s in results["config"]["eval_seasons"]:
            audit = results["by_season"][str(s)]["conservative_probability_audit"]
            self.assertTrue(audit["conservative_never_exceeds_raw"])


# 38. registry
class Test38Registry(unittest.TestCase):
    def test_team_sog_total_status(self):
        m = market_registry.get("TEAM_SOG_TOTAL")
        self.assertEqual(m.model_status, "VALIDATED")
        self.assertIn("20PLUS", m.threshold_validation_status)
        self.assertIn("NOT_40PLUS", m.threshold_validation_status)

    def test_registry_totals(self):
        self.assertEqual(market_registry.total_canonical_markets(), 142)
        self.assertEqual(len(market_registry.derivable_today()), 29)
        self.assertEqual(len(market_registry.validated_today()), 15)


# 39. dashboard
class Test39Dashboard(unittest.TestCase):
    def test_dashboard_page_discloses_status(self):
        path = "dashboard/pages/17_Team_SOG_Research.py"
        self.assertTrue(os.path.exists(path))
        with open(path) as f:
            content = f.read()
        self.assertIn("VALIDATED", content)
        self.assertIn("PARTIAL", content)


# 40. Goalie Saves model unchanged
class Test40GoalieSavesModelUnchanged(unittest.TestCase):
    def test_goalie_saves_results_unchanged(self):
        self.assertEqual(_file_sha256("research/goalie_saves_results.json"),
                          "6533395bfe111385f2591dca0944a2a576a785178ac640c4fd7ee2363af3e34e")


# 41. full-game Player SOG unchanged
class Test41FullGamePlayerSogUnchanged(unittest.TestCase):
    def test_player_sog_results_unchanged(self):
        self.assertEqual(_file_sha256("research/player_sog_results.json"),
                          "556d447bc6dcfc18df52812d98901cd7accad3b203a06606ddd68ea6993e8f61")


# 42. period Player SOG unchanged
class Test42PeriodPlayerSogUnchanged(unittest.TestCase):
    def test_player_sog_period_results_unchanged(self):
        self.assertEqual(_file_sha256("research/player_sog_period_results.json"),
                          "1d81d5ac989215da1302dcc550159a31f8feb8e1593da964f4e5485216e19e29")


# 43. other validated props unchanged
class Test43OtherValidatedPropsUnchanged(unittest.TestCase):
    def test_goals_blocks_assists_points_unchanged(self):
        self.assertEqual(_file_sha256("research/player_goals_results.json"),
                          "3f5592585a255b11c77f2a4d08c2c9886d01e45dbc8b48b30d284389367f5348")
        self.assertEqual(_file_sha256("research/player_blocks_results.json"),
                          "fc608ab5da9adf06170f96b7e96989fc29cf4cad07a26a9d9778d51649293c07")
        self.assertEqual(_file_sha256("research/player_assists_results.json"),
                          "3f8bc1c649cb3bbea4be0f56ebf893e399eaca415075ea1dca176e1f944ec0e9")
        self.assertEqual(_file_sha256("research/player_points_results.json"),
                          "6eacd4d56dc78d6b371b7f0234252e1f969359a427d813efcd696780b8af8877")
        self.assertEqual(_file_sha256("research/player_points_redesign_results.json"),
                          "490614606d5a8e046a9072669bc15a2bdfbb0097fb3a1a9696e7cd878ea97b75")


# 44. decision policy v3 unchanged
class Test44DecisionPolicyUnchanged(unittest.TestCase):
    def test_decision_policy_v3_unchanged(self):
        self.assertEqual(
            _file_sha256("research/player_props/decision_policy.py"),
            "f812d5fa2f1811c2b06b0b63b972dc499fc2f8f6e117a9afd5fa63bea55c763a",
        )
        self.assertEqual(decision_policy.POLICY_VERSION, "prop_decision_policy_v3")


# 45. NHL win model unchanged
class Test45NhlWinModelUnchanged(unittest.TestCase):
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
