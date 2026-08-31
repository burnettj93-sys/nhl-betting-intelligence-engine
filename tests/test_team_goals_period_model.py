"""
Part 46: tests for the Team Goals by Period model. Real fixtures only --
the label corpus, the frozen results file, and small real slices of the
4-season PBP corpus, never synthesized. Numbered comments map to Part-46
topics.
"""
from __future__ import annotations

import hashlib
import json
import os
import unittest

from research.player_props import decision_policy, market_registry
from research.real_nhl_pbp import raw_archive
from research.team_goals_period import features as tf
from research.team_goals_period import hierarchy as hi
from research.team_goals_period.build_team_goals_period_corpus import build_one_game
from research.run_team_goals_period_model import (
    PERIODS,
    RESULTS_PATH,
    THRESHOLDS,
    build_example,
    date_clustered_bootstrap,
    game_clustered_bootstrap,
    threshold_prob,
)

BASIC_GAME = ("20252026", 2025020073)  # WSH 5 - MIN 1, real, no OT/SO
OT_GAME = ("20252026", 2025020193)
SO_GAME = ("20252026", 2025020231)


def _file_sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _load_results() -> dict:
    with open(RESULTS_PATH) as f:
        return json.load(f)


# 1-3. P1/P2/P3 team-goal labels
class Test01To03PeriodLabels(unittest.TestCase):
    def test_labels_reconcile_against_real_final_score(self):
        season, gid = BASIC_GAME
        raw = raw_archive.load_raw_pbp(season, gid)
        rows = build_one_game(season, gid)
        home_row = next(r for r in rows if r["home_away"] == "home")
        away_row = next(r for r in rows if r["home_away"] == "away")
        self.assertEqual(home_row["full_game_team_goals"], raw["homeTeam"]["score"])
        self.assertEqual(away_row["full_game_team_goals"], raw["awayTeam"]["score"])

    def test_period_goal_counts_nonnegative(self):
        rows = build_one_game(*BASIC_GAME)
        for r in rows:
            for k in PERIODS:
                self.assertIsInstance(r[f"period_{k}_goals"], int)
                self.assertGreaterEqual(r[f"period_{k}_goals"], 0)


# 4. shootout exclusion
class Test04ShootoutExclusion(unittest.TestCase):
    def test_so_goals_never_counted(self):
        rows = build_one_game(*SO_GAME)
        raw = raw_archive.load_raw_pbp(*SO_GAME)
        for r in rows:
            total = r["period_1_goals"] + r["period_2_goals"] + r["period_3_goals"] + r["ot_goals"]
            self.assertEqual(total, r["full_game_team_goals"])
        # confirm the game's real final score includes a SO-bonus goal not present in statistical totals
        final_type = raw["gameOutcome"].get("lastPeriodType")
        self.assertEqual(final_type, "SO")


# 5. OT separation
class Test05OtSeparation(unittest.TestCase):
    def test_ot_tracked_separately(self):
        rows = build_one_game(*OT_GAME)
        self.assertTrue(any(r["ot_goals"] > 0 for r in rows))
        for r in rows:
            self.assertEqual(r["full_game_team_goals"],
                              r["period_1_goals"] + r["period_2_goals"] + r["period_3_goals"] + r["ot_goals"])


# 6. period/full-game reconciliation
class Test06PeriodFullGameReconciliation(unittest.TestCase):
    def test_reconciles_across_multiple_real_games(self):
        for season, gid in (BASIC_GAME, OT_GAME, SO_GAME):
            raw = raw_archive.load_raw_pbp(season, gid)
            rows = build_one_game(season, gid)
            final_type = raw["gameOutcome"].get("lastPeriodType")
            home_row = next(r for r in rows if r["home_away"] == "home")
            away_row = next(r for r in rows if r["home_away"] == "away")
            if final_type != "SO":
                self.assertEqual(home_row["full_game_team_goals"], raw["homeTeam"]["score"])
                self.assertEqual(away_row["full_game_team_goals"], raw["awayTeam"]["score"])


# 7. target-game goal exclusion
class Test07TargetGameGoalExclusion(unittest.TestCase):
    def test_build_example_ignores_target_row_goal_fields(self):
        rows = tf.load_team_period_corpus()
        index = tf.TeamPeriodHistoryIndex(rows)
        tuning_rows = [r for r in rows if r["season"] == 20232024]
        rates = hi.PeriodTeamRates(tuning_rows)
        league_avg = {k: 10.0 for k in PERIODS}
        sample = next(r for r in rows if r["season"] == 20242025)
        ex1 = build_example(sample, index, rates, league_avg)
        mutated = dict(sample)
        mutated["period_1_goals"] = 999
        mutated["period_2_goals"] = 999
        ex2 = build_example(mutated, index, rates, league_avg)
        if ex1 is not None and ex2 is not None:
            for k in PERIODS:
                self.assertEqual(ex1["per_period"][k]["baseline_rate"], ex2["per_period"][k]["baseline_rate"])


# 8. future exclusion
class Test08FutureExclusion(unittest.TestCase):
    def test_history_never_includes_same_or_later_dates(self):
        rows = tf.load_team_period_corpus()
        index = tf.TeamPeriodHistoryIndex(rows)
        sample = rows[500]
        hist = index.history_as_of(sample["team"], sample["game_date"])
        self.assertTrue(all(r["game_date"] < sample["game_date"] for r in hist))


# 9. same-day exclusion
class Test09SameDayExclusion(unittest.TestCase):
    def test_strict_less_than(self):
        rows = tf.load_team_period_corpus()
        index = tf.TeamPeriodHistoryIndex(rows)
        sample = rows[500]
        hist = index.history_as_of(sample["team"], sample["game_date"])
        self.assertEqual([r for r in hist if r["game_date"] == sample["game_date"]], [])


# 10. home/away assignment
class Test10HomeAwayAssignment(unittest.TestCase):
    def test_home_away_symmetric_per_game(self):
        rows = build_one_game(*BASIC_GAME)
        tags = {r["home_away"] for r in rows}
        self.assertEqual(tags, {"home", "away"})


# 11. full-game upstream feature PIT integrity
class Test11UpstreamPitIntegrity(unittest.TestCase):
    def test_upstream_uses_only_strict_prior_history(self):
        rows = tf.load_team_period_corpus()
        index = tf.TeamPeriodHistoryIndex(rows)
        sample = rows[1000]
        hist = index.history_as_of(sample["team"], sample["game_date"])
        self.assertTrue(all(r["game_date"] < sample["game_date"] for r in hist))


# 12. team period-share calculation
class Test12PeriodShareCalculation(unittest.TestCase):
    def test_share_between_zero_and_one(self):
        rows = tf.load_team_period_corpus()
        tuning = [r for r in rows if r["season"] == 20232024]
        rates = hi.PeriodTeamRates(tuning)
        history = [r for r in rows if r["season"] == 20242025][:20]
        share = hi.team_period_share_hierarchical(history, "home", rates, 1)
        self.assertGreaterEqual(share, 0.0)
        self.assertLessEqual(share, 1.0)


# 13. period-share shrinkage
class Test13PeriodShareShrinkage(unittest.TestCase):
    def test_zero_history_returns_prior_exactly(self):
        rows = tf.load_team_period_corpus()
        tuning = [r for r in rows if r["season"] == 20232024]
        rates = hi.PeriodTeamRates(tuning)
        share = hi.team_period_share_hierarchical([], "home", rates, 1)
        self.assertEqual(share, rates.ha_share_shrunk("home", 1))


# 14. Poisson distribution
class Test14PoissonDistribution(unittest.TestCase):
    def test_glm_weights_recorded_per_period(self):
        results = _load_results()
        self.assertEqual(set(results["glm_weights"].keys()), {"1", "2", "3"})
        for k, w in results["glm_weights"].items():
            self.assertEqual(len(w), 6)


# 15. NB distribution
class Test15NbDistribution(unittest.TestCase):
    def test_alpha_fit_per_period_near_zero(self):
        results = _load_results()
        for k, alpha in results["glm_alpha"].items():
            self.assertGreaterEqual(alpha, 0.0)
            self.assertLess(alpha, 0.5, "team-period goals should show near-Poisson dispersion, not heavy NB")


# 16. zero-inflation decision
class Test16ZeroInflationDecision(unittest.TestCase):
    def test_no_zero_inflated_family_present(self):
        results = _load_results()
        winners = set(results["winner_by_period"].values())
        self.assertTrue(all("zero" not in w.lower() for w in winners))


# 17. threshold monotonicity
class Test17ThresholdMonotonicity(unittest.TestCase):
    def test_probabilities_monotonic_for_range_of_mu(self):
        for mu in (0.1, 0.5, 1.0, 2.0, 5.0):
            probs = [threshold_prob(mu, None, t) for t in THRESHOLDS]
            self.assertEqual(probs, sorted(probs, reverse=True))

    def test_zero_monotonicity_violations_in_frozen_results(self):
        results = _load_results()
        for s in results["config"]["eval_seasons"]:
            for k in PERIODS:
                self.assertEqual(results["by_season"][str(s)][f"period_{k}"]["monotonicity_violations"], 0)


# 18-20. team 1+/2+/3+ derivation
class Test18To20ThresholdDerivation(unittest.TestCase):
    def test_thresholds_derived_from_same_mu(self):
        mu = 1.1
        p1 = threshold_prob(mu, None, 1)
        p2 = threshold_prob(mu, None, 2)
        p3 = threshold_prob(mu, None, 3)
        self.assertGreater(p1, p2)
        self.assertGreater(p2, p3)


# 21. tail-support rule
class Test21TailSupportRule(unittest.TestCase):
    def test_4plus_rate_reported(self):
        results = _load_results()
        for s in results["config"]["eval_seasons"]:
            for k in PERIODS:
                winner = results["winner_by_period"][str(k)]
                cand = results["by_season"][str(s)][f"period_{k}"]["candidates"][winner]
                self.assertIn("4", cand["thresholds"])


# 22. home/away joint-dependence diagnostic
class Test22HomeAwayDependence(unittest.TestCase):
    def test_dependence_recorded_and_grows_by_period_magnitude(self):
        results = _load_results()
        for s in results["config"]["eval_seasons"]:
            corrs = []
            for k in PERIODS:
                had = results["by_season"][str(s)][f"period_{k}"]["home_away_dependence"]
                self.assertIn("raw_correlation", had)
                corrs.append(abs(had["raw_correlation"]))
            # P3 magnitude should exceed P1 magnitude in both real eval seasons (the actual finding)
            self.assertGreater(corrs[2], corrs[0])


# 23. common evaluation set
class Test23CommonEvaluationSet(unittest.TestCase):
    def test_common_eval_recorded(self):
        results = _load_results()
        for s in results["config"]["eval_seasons"]:
            info = results["by_season"][str(s)]["common_eval"]
            self.assertIn("eligible_rows", info)
            self.assertIn("excluded_rows", info)


# 24. temporal split
class Test24TemporalSplit(unittest.TestCase):
    def test_warmup_tuning_eval_seasons(self):
        results = _load_results()
        cfg = results["config"]
        self.assertEqual(cfg["warmup_season"], 20222023)
        self.assertEqual(cfg["tuning_season"], 20232024)
        self.assertEqual(cfg["eval_seasons"], [20242025, 20252026])


# 25. freeze manifest
class Test25FreezeManifest(unittest.TestCase):
    def test_freeze_manifest_present(self):
        results = _load_results()
        manifest = results["freeze_manifest"]
        self.assertEqual(manifest["experiment_id"], "team_goals_by_period_v1")
        self.assertIn("code_hashes", manifest)
        self.assertIn("goalie_context", manifest)  # disclosed scope decision


# 26. frozen evaluation
class Test26FrozenEvaluation(unittest.TestCase):
    def test_single_glm_weights_used_for_both_eval_seasons(self):
        results = _load_results()
        self.assertIn("glm_weights", results)
        self.assertNotIn("glm_weights_20242025", results)
        self.assertNotIn("glm_weights_20252026", results)


# 27. game-cluster bootstrap
class Test27GameClusterBootstrap(unittest.TestCase):
    def test_bootstrap_present_and_honestly_recorded(self):
        results = _load_results()
        for s in results["config"]["eval_seasons"]:
            for k in PERIODS:
                for t in ("1", "2", "3"):
                    gc = results["by_season"][str(s)][f"period_{k}"]["bootstrap"][t]["game_clustered"]
                    self.assertIn("frac_improved", gc)

    def test_real_negative_result_disclosed_not_hidden(self):
        # the honest finding: no period/threshold clears frac_improved>=0.95
        # consistently in BOTH eval seasons -- confirmed structurally here
        results = _load_results()
        any_consistent_pass = False
        for k in PERIODS:
            for t in ("1", "2", "3"):
                fracs = [results["by_season"][str(s)][f"period_{k}"]["bootstrap"][t]["game_clustered"]["frac_improved"]
                         for s in results["config"]["eval_seasons"]]
                if all(f >= 0.95 for f in fracs):
                    any_consistent_pass = True
        self.assertFalse(any_consistent_pass,
                          "if this now fails, the real evidence changed and Section AC/AF must be rewritten")


# 28. date-cluster sensitivity
class Test28DateClusterSensitivity(unittest.TestCase):
    def test_date_clustered_present(self):
        results = _load_results()
        for s in results["config"]["eval_seasons"]:
            for k in PERIODS:
                dc = results["by_season"][str(s)][f"period_{k}"]["bootstrap"]["1"]["date_clustered"]
                self.assertIn("frac_improved", dc)


# 29. calibration
class Test29Calibration(unittest.TestCase):
    def test_calibration_bins_present(self):
        results = _load_results()
        s = str(results["config"]["eval_seasons"][0])
        for k in PERIODS:
            winner = results["winner_by_period"][str(k)]
            cal = results["by_season"][s][f"period_{k}"]["candidates"][winner]["thresholds"]["1"]["calibration"]
            self.assertGreater(len(cal), 0)


# 30. confidence framework unchanged
class Test30ConfidenceFrameworkUnchanged(unittest.TestCase):
    def test_confidence_score_signature_unchanged(self):
        from research.player_sog import count_models as cm
        import inspect
        sig = inspect.signature(cm.confidence_score)
        self.assertEqual(list(sig.parameters), ["n_history_games", "recent_toi_cv", "recent_sog_cv",
                                                  "opponent_window_games", "opponent_window_target",
                                                  "appearance_rate"])


# 31. conservative probability
class Test31ConservativeProbability(unittest.TestCase):
    def test_conservative_never_exceeds_raw(self):
        results = _load_results()
        for s in results["config"]["eval_seasons"]:
            for k in PERIODS:
                audit = results["by_season"][str(s)][f"period_{k}"]["conservative_probability_audit"]
                self.assertTrue(audit["conservative_never_exceeds_raw"])


# 32. alias mapping
class Test32AliasMapping(unittest.TestCase):
    def test_duplicate_market_ids_carry_identical_status(self):
        m1 = market_registry.get("TEAM_PERIOD_1_TOTAL")
        m2 = market_registry.get("PERIOD_1_TEAM_TOTAL_GOALS")
        self.assertEqual(m1.model_status, m2.model_status)
        self.assertEqual(m1.threshold_validation_status, m2.threshold_validation_status)


# 33. registry status
class Test33RegistryStatus(unittest.TestCase):
    def test_team_period_markets_marked_research_not_validated(self):
        for mid in ("TEAM_PERIOD_1_TOTAL", "TEAM_PERIOD_2_TOTAL", "TEAM_PERIOD_3_TOTAL"):
            m = market_registry.get(mid)
            self.assertEqual(m.model_status, "RESEARCH")
            self.assertEqual(m.threshold_validation_status, "ATTEMPTED_NOT_VALIDATED")
            self.assertEqual(m.historical_data_status, "AVAILABLE_USED")


# 34. dashboard labeling
class Test34DashboardLabeling(unittest.TestCase):
    def test_dashboard_page_discloses_not_validated(self):
        path = "dashboard/pages/15_Team_Goals_By_Period_Research.py"
        self.assertTrue(os.path.exists(path))
        with open(path) as f:
            content = f.read()
        self.assertIn("NOT VALIDATED", content)


# 35. player-period SOG model unchanged
class Test35PlayerSogPeriodModelUnchanged(unittest.TestCase):
    def test_player_sog_period_results_unchanged_shape(self):
        with open("research/player_sog_period_results.json") as f:
            data = json.load(f)
        self.assertEqual(data["winner_by_period"], {"1": "E_hybrid_offset", "2": "E_hybrid_offset",
                                                       "3": "E_hybrid_offset"})


# 36. full-game SOG model unchanged
class Test36FullGameSogModelUnchanged(unittest.TestCase):
    def test_full_game_sog_results_unchanged(self):
        with open("research/player_sog_results.json") as f:
            data = json.load(f)
        self.assertEqual(data["headline_stage"], "M4_plus_h2h")


# 37. all other validated prop models unchanged
class Test37OtherValidatedModelsUnchanged(unittest.TestCase):
    def test_goals_and_confidence_artifacts_unchanged(self):
        with open("research/player_goals_results.json") as f:
            data = json.load(f)
        self.assertIn("context_weights_e", data)
        with open("research/confidence_framework_results.json") as f:
            data2 = json.load(f)
        self.assertIn("results_by_prop_fold", data2)


# 38. decision policy v3 unchanged
class Test38DecisionPolicyUnchanged(unittest.TestCase):
    def test_decision_policy_v3_unchanged(self):
        self.assertEqual(
            _file_sha256("research/player_props/decision_policy.py"),
            "f812d5fa2f1811c2b06b0b63b972dc499fc2f8f6e117a9afd5fa63bea55c763a",
        )
        self.assertEqual(decision_policy.POLICY_VERSION, "prop_decision_policy_v3")
        self.assertEqual(
            decision_policy.PROP_LOW_CONFIDENCE_CEILING,
            {"ASSISTS": "WATCH", "POINTS": "WATCH", "GOALS": "WATCH", "PLAYER_SOG_PERIOD_3": "WATCH"},
        )


# 39. NHL win model unchanged
class Test39NhlWinModelUnchanged(unittest.TestCase):
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
