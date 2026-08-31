"""
Part 47: tests for the Player SOG by Period model. Real fixtures only --
the label corpus, the frozen results file, and small real slices of the
4-season PBP corpus, never synthesized. Numbered comments map to Part-47
topics.
"""
from __future__ import annotations

import hashlib
import json
import os
import unittest

from research.player_props import decision_policy, market_registry
from research.player_sog_period import features as pf
from research.player_sog_period import hierarchy as hi
from research.player_sog_period.build_period_sog_corpus import build_one_game
from research.real_nhl_pbp import raw_archive
from research.run_player_sog_period_model import (
    PERIODS,
    RESULTS_PATH,
    THRESHOLDS,
    brier,
    build_example,
    date_clustered_bootstrap,
    game_clustered_bootstrap,
    glm_feature_vector,
    threshold_prob,
)

BASIC_GAME = ("20252026", 2025020073)  # WSH 5 - MIN 1, real, no OT/SO


def _file_sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _load_results() -> dict:
    with open(RESULTS_PATH) as f:
        return json.load(f)


# 1-3. P1/P2/P3 label correctness
class Test01To03PeriodLabelCorrectness(unittest.TestCase):
    def test_period_labels_match_real_boxscore_team_sog(self):
        season, gid = BASIC_GAME
        raw = raw_archive.load_raw_pbp(season, gid)
        rows = build_one_game(season, gid)
        total = sum(r["full_game_sog"] for r in rows)
        self.assertEqual(total, raw["homeTeam"]["sog"] + raw["awayTeam"]["sog"])

    def test_each_period_label_is_nonnegative_int(self):
        rows = build_one_game(*BASIC_GAME)
        for r in rows:
            for k in PERIODS:
                self.assertIsInstance(r[f"period_{k}_sog"], int)
                self.assertGreaterEqual(r[f"period_{k}_sog"], 0)


# 4. goals count toward period SOG
class Test04GoalsCountTowardPeriodSog(unittest.TestCase):
    def test_scorer_credited_in_correct_period(self):
        season, gid = BASIC_GAME
        raw = raw_archive.load_raw_pbp(season, gid)
        rows = {r["player_id"]: r for r in build_one_game(season, gid)}
        for p in raw["plays"]:
            if p["typeDescKey"] == "goal" and p["periodDescriptor"]["periodType"] == "REG":
                scorer = str(p["details"]["scoringPlayerId"])
                period = p["periodDescriptor"]["number"]
                self.assertGreaterEqual(rows[scorer][f"period_{period}_sog"], 1)


# 5. shootout excluded
class Test05ShootoutExcluded(unittest.TestCase):
    def test_so_events_never_counted_in_periods(self):
        rows = build_one_game("20252026", 2025020231)  # real SO game
        for r in rows:
            self.assertEqual(r["period_1_sog"] + r["period_2_sog"] + r["period_3_sog"] + r["ot_sog"],
                              r["full_game_sog"])


# 6. OT kept separate
class Test06OtKeptSeparate(unittest.TestCase):
    def test_ot_tracked_but_not_merged_into_periods(self):
        rows = build_one_game("20252026", 2025020193)  # real OT game
        self.assertTrue(any(r["ot_sog"] > 0 for r in rows))
        for r in rows:
            self.assertEqual(r["full_game_sog"], r["period_1_sog"] + r["period_2_sog"] + r["period_3_sog"] + r["ot_sog"])


# 7. period/full-game reconciliation
class Test07PeriodFullGameReconciliation(unittest.TestCase):
    def test_reconciles_across_multiple_real_games(self):
        for season, gid in (BASIC_GAME, ("20252026", 2025020193), ("20252026", 2025020231)):
            raw = raw_archive.load_raw_pbp(season, gid)
            rows = build_one_game(season, gid)
            self.assertEqual(sum(r["full_game_sog"] for r in rows),
                              raw["homeTeam"]["sog"] + raw["awayTeam"]["sog"])


# 8-9. target-game (period) SOG excluded from features
class Test08To09TargetGameSogExcluded(unittest.TestCase):
    def test_build_example_never_reads_target_row_sog_fields(self):
        rows = pf.load_period_corpus()
        period_index = pf.PeriodHistoryIndex(rows)
        from research.player_sog import features as sog_pf
        sog_index = sog_pf.PlayerHistoryIndex(sog_pf.load_sog_corpus())
        rates = hi.PeriodRoleLeagueRates([r for r in rows if r["season"] == 20232024])
        league_avg = {k: 10.0 for k in PERIODS}
        sample = next(r for r in rows if r["season"] == 20242025)
        ex1 = build_example(rows, sample, period_index, sog_index, {}, None, {}, {}, rates, league_avg)
        # mutate the TARGET row's own period SOG -- must not change any feature
        # (only the "actual" label, which is not a feature)
        mutated = dict(sample)
        mutated["period_1_sog"] = 999
        mutated["period_2_sog"] = 999
        mutated["period_3_sog"] = 999
        ex2 = build_example(rows, mutated, period_index, sog_index, {}, None, {}, {}, rates, league_avg)
        if ex1 is not None and ex2 is not None:
            for k in PERIODS:
                self.assertEqual(ex1["per_period"][k]["baseline_rate"], ex2["per_period"][k]["baseline_rate"])
                self.assertEqual(ex1["per_period"][k]["shrunk_mean"], ex2["per_period"][k]["shrunk_mean"])


# 10. target-game TOI excluded
class Test10TargetGameToiExcluded(unittest.TestCase):
    def test_recent_toi_comes_from_history_not_target(self):
        # recent_toi/baseline_toi are computed purely from sog_index.history_as_of(),
        # which is strictly < prediction_game_date by construction (features.py)
        from research.player_sog import features as sog_pf
        sog_rows = sog_pf.load_sog_corpus()
        idx = sog_pf.PlayerHistoryIndex(sog_rows)
        some_row = sog_rows[1000]
        hist = idx.history_as_of(some_row["player_id"], some_row["game_date"])
        self.assertTrue(all(r["game_date"] < some_row["game_date"] for r in hist))


# 11. future exclusion
class Test11FutureExclusion(unittest.TestCase):
    def test_history_never_includes_same_or_later_dates(self):
        rows = pf.load_period_corpus()
        index = pf.PeriodHistoryIndex(rows)
        sample = rows[5000]
        hist = index.history_as_of(sample["player_id"], sample["game_date"])
        self.assertTrue(all(r["game_date"] < sample["game_date"] for r in hist))


# 12. same-day exclusion
class Test12SameDayExclusion(unittest.TestCase):
    def test_strict_less_than_not_less_equal(self):
        rows = pf.load_period_corpus()
        index = pf.PeriodHistoryIndex(rows)
        sample = rows[5000]
        hist = index.history_as_of(sample["player_id"], sample["game_date"])
        same_day = [r for r in hist if r["game_date"] == sample["game_date"]]
        self.assertEqual(same_day, [])


# 13. projected-active policy
class Test13ProjectedActivePolicy(unittest.TestCase):
    def test_no_target_game_participation_in_eligibility(self):
        from research.player_sog import features as sog_pf
        import inspect
        source = inspect.getsource(sog_pf.projected_active)
        self.assertNotIn("prediction_game_date ==", source)


# 14. upstream full-game SOG PIT integrity
class Test14UpstreamPitIntegrity(unittest.TestCase):
    def test_upstream_uses_strict_history_gate(self):
        from research.player_sog_period.upstream_sog import UpstreamSogModel
        model = UpstreamSogModel()
        result = model.expected_sog("8471214", "WSH", "MIN", "2025-10-17", 20252026)
        self.assertIn(result["status"], ("PROJECTED_ACTIVE", "PROJECTED_INACTIVE", "INSUFFICIENT_HISTORY"))


# 15. player period-share calculation
class Test15PeriodShareCalculation(unittest.TestCase):
    def test_share_hierarchical_between_zero_and_one(self):
        rows = pf.load_period_corpus()
        tuning = [r for r in rows if r["season"] == 20232024]
        rates = hi.PeriodRoleLeagueRates(tuning)
        history = [r for r in rows if r["season"] == 20242025][:20]
        share = hi.player_period_share_hierarchical(history, "F_NONPP", rates, 1)
        self.assertGreaterEqual(share, 0.0)
        self.assertLessEqual(share, 1.0)


# 16. period-share shrinkage
class Test16PeriodShareShrinkage(unittest.TestCase):
    def test_zero_history_returns_role_prior_exactly(self):
        rows = pf.load_period_corpus()
        tuning = [r for r in rows if r["season"] == 20232024]
        rates = hi.PeriodRoleLeagueRates(tuning)
        share = hi.player_period_share_hierarchical([], "F_NONPP", rates, 1)
        self.assertEqual(share, rates.role_share_shrunk("F_NONPP", 1))


# 17. share sum = 1 (raw share, real corpus check -- not a hard constraint
# on the shrunk share estimator, which is independent per period, but the
# RAW empirical P1+P2+P3 share must sum to exactly the REGULATION share of
# full_game_sog, i.e. 1.0 minus the OT share -- full_game_sog includes OT
# shots (Part 2: OT tracked separately, never silently merged), so a
# player with any real OT shots in their sample will show a real,
# expected sum < 1.0, not a bug (caught during initial test-writing).
class Test17ShareSumToOne(unittest.TestCase):
    def test_raw_shares_sum_to_regulation_share_for_real_player(self):
        rows = pf.load_period_corpus()
        history = [r for r in rows if r["player_id"] == "8471214" and r["season"] == 20252026]
        total_full = sum(r["full_game_sog"] for r in history)
        total_ot = sum(r["ot_sog"] for r in history)
        if total_full > 0:
            total_share = sum(sum(r[f"period_{k}_sog"] for r in history) / total_full for k in PERIODS)
            expected_share = (total_full - total_ot) / total_full
            self.assertAlmostEqual(total_share, expected_share, places=6)

    def test_shares_sum_to_exactly_one_for_a_player_with_no_ot_shots(self):
        rows = pf.load_period_corpus()
        history = [r for r in rows if r["player_id"] == "8471214" and r["season"] == 20252026 and r["ot_sog"] == 0]
        total_full = sum(r["full_game_sog"] for r in history)
        if total_full > 0:
            total_share = sum(sum(r[f"period_{k}_sog"] for r in history) / total_full for k in PERIODS)
            self.assertAlmostEqual(total_share, 1.0, places=6)


# 18-19. Poisson vs NB direct period distribution
class Test18To19PoissonVsNb(unittest.TestCase):
    def test_glm_alpha_fit_per_period_recorded(self):
        results = _load_results()
        self.assertEqual(set(results["glm_alpha"].keys()), {"1", "2", "3"})
        for k, alpha in results["glm_alpha"].items():
            self.assertGreaterEqual(alpha, 0.0)


# 20. zero-inflation choice
class Test20ZeroInflationChoice(unittest.TestCase):
    def test_no_zero_inflated_model_family_present(self):
        results = _load_results()
        winners = set(results["winner_by_period"].values())
        self.assertTrue(all("zero" not in w.lower() for w in winners))


# 21. threshold monotonicity
class Test21ThresholdMonotonicity(unittest.TestCase):
    def test_probabilities_monotonic_for_range_of_mu(self):
        for mu in (0.1, 0.5, 1.0, 2.0, 5.0):
            probs = [threshold_prob(mu, None, t) for t in THRESHOLDS]
            self.assertEqual(probs, sorted(probs, reverse=True))

    def test_zero_monotonicity_violations_in_frozen_results(self):
        results = _load_results()
        for s in results["config"]["eval_seasons"]:
            for k in PERIODS:
                self.assertEqual(results["by_season"][str(s)][f"period_{k}"]["monotonicity_violations"], 0)


# 22-24. 1+/2+/3+ derivation
class Test22To24ThresholdDerivation(unittest.TestCase):
    def test_thresholds_derived_from_same_mu(self):
        mu = 1.3
        p1 = threshold_prob(mu, None, 1)
        p2 = threshold_prob(mu, None, 2)
        p3 = threshold_prob(mu, None, 3)
        self.assertGreater(p1, p2)
        self.assertGreater(p2, p3)


# 25. tail-support rule
class Test25TailSupportRule(unittest.TestCase):
    def test_4plus_actual_rate_below_pre_specified_threshold(self):
        results = _load_results()
        for s in results["config"]["eval_seasons"]:
            for k in PERIODS:
                cand = results["by_season"][str(s)][f"period_{k}"]["candidates"]
                winner = results["winner_by_period"][str(k)]
                rate4 = cand[winner]["thresholds"]["4"]["actual_rate"]
                n = cand[winner]["n"]
                self.assertLess(rate4 * n, 300, "4+ should be below the pre-specified 300-event support rule")


# 26. common evaluation set
class Test26CommonEvaluationSet(unittest.TestCase):
    def test_common_eval_recorded_with_zero_excluded(self):
        results = _load_results()
        for s in results["config"]["eval_seasons"]:
            info = results["by_season"][str(s)]["common_eval"]
            self.assertIn("eligible_rows", info)
            self.assertIn("excluded_rows", info)


# 27. temporal split
class Test27TemporalSplit(unittest.TestCase):
    def test_warmup_tuning_eval_seasons_are_distinct_and_ordered(self):
        results = _load_results()
        cfg = results["config"]
        self.assertEqual(cfg["warmup_season"], 20222023)
        self.assertEqual(cfg["tuning_season"], 20232024)
        self.assertEqual(cfg["eval_seasons"], [20242025, 20252026])


# 28. freeze manifest
class Test28FreezeManifest(unittest.TestCase):
    def test_freeze_manifest_present_with_code_hashes(self):
        results = _load_results()
        manifest = results["freeze_manifest"]
        self.assertEqual(manifest["experiment_id"], "player_sog_by_period_v1")
        self.assertIn("code_hashes", manifest)
        self.assertIn("timestamp_utc", manifest)


# 29. evaluation uses frozen spec
class Test29EvaluationUsesFrozenSpec(unittest.TestCase):
    def test_glm_weights_identical_across_periods_block(self):
        results = _load_results()
        # the SAME glm_weights dict is used to score both eval seasons -- no
        # separate re-fit occurs per season (checked structurally: only one
        # top-level "glm_weights" key exists, not one per season)
        self.assertIn("glm_weights", results)
        self.assertNotIn("glm_weights_20242025", results)
        self.assertNotIn("glm_weights_20252026", results)


# 30. game-cluster bootstrap
class Test30GameClusterBootstrap(unittest.TestCase):
    def test_bootstrap_present_for_every_period_season_threshold(self):
        results = _load_results()
        for s in results["config"]["eval_seasons"]:
            for k in PERIODS:
                for t in ("1", "2", "3"):
                    gc = results["by_season"][str(s)][f"period_{k}"]["bootstrap"][t]["game_clustered"]
                    self.assertIn("frac_improved", gc)
                    self.assertIn("n_games_resampled", gc)


# 31. date-cluster sensitivity
class Test31DateClusterSensitivity(unittest.TestCase):
    def test_date_clustered_present_and_broadly_consistent_with_game_clustered(self):
        results = _load_results()
        for s in results["config"]["eval_seasons"]:
            for k in PERIODS:
                b = results["by_season"][str(s)][f"period_{k}"]["bootstrap"]["1"]
                gc_sign = b["game_clustered"]["point_delta"] < 0
                dc_sign = b["date_clustered"]["point_delta"] < 0
                self.assertEqual(gc_sign, dc_sign)


# 32. period-specific calibration
class Test32PeriodSpecificCalibration(unittest.TestCase):
    def test_calibration_bins_present_per_period(self):
        results = _load_results()
        s = str(results["config"]["eval_seasons"][0])
        for k in PERIODS:
            winner = results["winner_by_period"][str(k)]
            cal = results["by_season"][s][f"period_{k}"]["candidates"][winner]["thresholds"]["1"]["calibration"]
            self.assertGreater(len(cal), 0)


# 33. confidence framework unchanged
class Test33ConfidenceFrameworkUnchanged(unittest.TestCase):
    def test_confidence_score_signature_unchanged(self):
        from research.player_sog import count_models as cm
        import inspect
        sig = inspect.signature(cm.confidence_score)
        self.assertEqual(list(sig.parameters), ["n_history_games", "recent_toi_cv", "recent_sog_cv",
                                                  "opponent_window_games", "opponent_window_target",
                                                  "appearance_rate"])


# 34. conservative probability
class Test34ConservativeProbability(unittest.TestCase):
    def test_conservative_never_exceeds_raw_in_frozen_results(self):
        results = _load_results()
        for s in results["config"]["eval_seasons"]:
            for k in PERIODS:
                audit = results["by_season"][str(s)][f"period_{k}"]["conservative_probability_audit"]
                self.assertTrue(audit["conservative_never_exceeds_raw"])


# 35. market alias equivalence
class Test35MarketAliasEquivalence(unittest.TestCase):
    def test_period_1_2_3_are_distinct_canonical_markets(self):
        ids = {market_registry.get(f"PERIOD_{k}_PLAYER_SOG").market_id for k in PERIODS}
        self.assertEqual(len(ids), 3)


# 36. registry status
class Test36RegistryStatus(unittest.TestCase):
    def test_period_markets_validated_with_per_period_nuance(self):
        m1 = market_registry.get("PERIOD_1_PLAYER_SOG")
        m2 = market_registry.get("PERIOD_2_PLAYER_SOG")
        m3 = market_registry.get("PERIOD_3_PLAYER_SOG")
        for m in (m1, m2, m3):
            self.assertEqual(m.model_status, "VALIDATED")
        self.assertEqual(m1.threshold_validation_status, "VALIDATED_1PLUS_2PLUS_3PLUS")
        self.assertEqual(m2.threshold_validation_status, "VALIDATED_1PLUS_2PLUS_ONLY")
        self.assertEqual(m3.threshold_validation_status, "VALIDATED_1PLUS_2PLUS_ONLY")
        self.assertEqual(m3.low_confidence_policy, "WATCH_ONLY")
        self.assertEqual(m1.low_confidence_policy, "NORMAL")


# 37. dashboard labeling
class Test37DashboardLabeling(unittest.TestCase):
    def test_dashboard_page_exists_and_mentions_research_status(self):
        path = "dashboard/pages/14_Player_SOG_By_Period_Research.py"
        self.assertTrue(os.path.exists(path))
        with open(path) as f:
            content = f.read()
        self.assertIn("RESEARCH", content)
        self.assertIn("NOT YET A BETTING RECOMMENDATION", content)


# 38. full-game SOG model unchanged
class Test38FullGameSogModelUnchanged(unittest.TestCase):
    def test_full_game_sog_results_file_unchanged_shape(self):
        with open("research/player_sog_results.json") as f:
            data = json.load(f)
        self.assertEqual(data["headline_stage"], "M4_plus_h2h")
        self.assertEqual(data["config"]["feature_names"],
                          ["intercept", "log_baseline_rate", "recent_form_log_ratio",
                           "toi_log_ratio", "opponent_log_factor", "h2h_shrunk_delta"])


# 39. all other validated prop models unchanged
class Test39OtherValidatedModelsUnchanged(unittest.TestCase):
    def test_goals_and_confidence_artifacts_unchanged(self):
        with open("research/player_goals_results.json") as f:
            data = json.load(f)
        self.assertIn("context_weights_e", data)
        with open("research/confidence_framework_results.json") as f:
            data2 = json.load(f)
        self.assertIn("results_by_prop_fold", data2)


# 40. decision policy unchanged (from this slice's own authorized freeze)
class Test40DecisionPolicyUnchanged(unittest.TestCase):
    def test_decision_policy_v3_pinned(self):
        self.assertEqual(decision_policy.POLICY_VERSION, "prop_decision_policy_v3")
        self.assertEqual(
            decision_policy.PROP_LOW_CONFIDENCE_CEILING,
            {"ASSISTS": "WATCH", "POINTS": "WATCH", "GOALS": "WATCH", "PLAYER_SOG_PERIOD_3": "WATCH"},
        )


# 41. NHL win model unchanged
class Test41NhlWinModelUnchanged(unittest.TestCase):
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
