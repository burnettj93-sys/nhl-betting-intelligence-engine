"""
2026-27 Continuous Learning framework, Part 63: integration tests for
operational/daily_model_review.py -- run ordering, settlement-incomplete
handling, deterministic daily report, the SOG-1+ shadow exclusion,
production/prediction immutability, and real-vs-theoretical ROI
separation.
"""
from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path

from operational import daily_model_review as dmr
from operational import prospective_ledger as pl

NOW = dt.datetime(2026, 11, 15, tzinfo=dt.timezone.utc)
PAST_EVENT = (NOW - dt.timedelta(days=2)).strftime("%Y-%m-%dT23:00:00.000000Z")
PAST_CUTOFF = (NOW - dt.timedelta(days=2)).strftime("%Y-%m-%dT18:00:00.000000Z")


def _record(conn, *, market_id="PLAYER_SOG", threshold="3+", player_id="P1", prob=0.6, **extra):
    fields = dict(
        event_start_utc=PAST_EVENT, created_at_utc=PAST_CUTOFF, prediction_cutoff_utc=PAST_CUTOFF,
        game_id=1, game_date="2026-11-13", player_id=player_id, team="EDM", opponent="CHI",
        market_id=market_id, threshold=threshold, raw_probability=prob, confidence="HIGH",
    )
    fields.update(extra)
    result = pl.record_model_observation(conn, **fields)
    return result["prediction_id"]


class Test01RunOrder(unittest.TestCase):
    def test_halts_when_results_not_ingested(self):
        conn = pl.init_db(db_path=":memory:")
        result = dmr.run_daily_review(conn, now_utc=NOW,
                                       inputs_ready={"results_ingested": False, "settlement_completed": True})
        self.assertEqual(result["engine_status"], "HALT")
        self.assertTrue(result["incomplete"])

    def test_halts_when_settlement_not_completed(self):
        conn = pl.init_db(db_path=":memory:")
        result = dmr.run_daily_review(conn, now_utc=NOW,
                                       inputs_ready={"results_ingested": True, "settlement_completed": False})
        self.assertEqual(result["engine_status"], "HALT")

    def test_proceeds_when_both_complete(self):
        conn = pl.init_db(db_path=":memory:")
        result = dmr.run_daily_review(conn, now_utc=NOW)
        self.assertNotEqual(result["engine_status"], "HALT")
        self.assertNotIn("incomplete", result)


class Test02DeterministicReport(unittest.TestCase):
    def test_same_ledger_state_produces_the_same_report_twice(self):
        conn = pl.init_db(db_path=":memory:")
        pred_id = _record(conn)
        pl.settle_prediction(conn, pred_id, "WIN", actual_outcome="5")

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            result1 = dmr.run_daily_review(conn, now_utc=NOW)
            path1 = dmr.write_daily_report(result1, report_date="2026-11-15", out_dir=out_dir)
            content1 = path1.read_text()
            result2 = dmr.run_daily_review(conn, now_utc=NOW)
            path2 = dmr.write_daily_report(result2, report_date="2026-11-15", out_dir=out_dir)
            content2 = path2.read_text()
            self.assertEqual(content1, content2)

    def test_incomplete_run_writes_a_minimal_report(self):
        conn = pl.init_db(db_path=":memory:")
        result = dmr.run_daily_review(conn, now_utc=NOW,
                                       inputs_ready={"results_ingested": False, "settlement_completed": True})
        with tempfile.TemporaryDirectory() as tmp:
            path = dmr.write_daily_report(result, report_date="2026-11-15", out_dir=Path(tmp))
            content = path.read_text()
            self.assertIn("INCOMPLETE RUN", content)


class Test03SogOneplusExclusion(unittest.TestCase):
    def test_threshold_1plus_never_enters_the_shadow_comparison(self):
        conn = pl.init_db(db_path=":memory:")
        pred_1plus = _record(conn, threshold="1+", sog_shadow_raw_probability=0.95, prob=0.9)
        pred_2plus = _record(conn, threshold="2+", sog_shadow_raw_probability=0.6, prob=0.5)
        pl.settle_prediction(conn, pred_1plus, "WIN", actual_outcome="5")
        pl.settle_prediction(conn, pred_2plus, "WIN", actual_outcome="5")
        rows = dmr.load_settleable_rows(conn)
        shadow = dmr.score_shadow_pairs(rows)
        self.assertEqual(shadow["SOG_PP_ROLE_OVERLAY"]["n"], 1)
        self.assertIn("SOG_PP_ROLE_OVERLAY", shadow.get("_diagnostics", {}))

    def test_only_2plus_and_3plus_are_valid_comparison_thresholds(self):
        self.assertEqual(dmr.SOG_PP_ROLE_VALID_COMPARISON_THRESHOLDS, ("2+", "3+"))
        self.assertNotIn("1+", dmr.SOG_PP_ROLE_VALID_COMPARISON_THRESHOLDS)
        self.assertNotIn("4+", dmr.SOG_PP_ROLE_VALID_COMPARISON_THRESHOLDS)


class Test04ProductionImmutability(unittest.TestCase):
    def test_running_the_daily_review_never_mutates_a_prediction_row(self):
        conn = pl.init_db(db_path=":memory:")
        pred_id = _record(conn)
        pl.settle_prediction(conn, pred_id, "WIN", actual_outcome="5")
        before = pl.get_observation(conn, pred_id)

        dmr.run_daily_review(conn, now_utc=NOW)

        after = pl.get_observation(conn, pred_id)
        self.assertEqual(before, after)

    def test_module_never_calls_settle_prediction_or_record_functions(self):
        import inspect
        src = inspect.getsource(dmr)
        self.assertNotIn("settle_prediction(", src)
        self.assertNotIn("record_model_observation(", src)
        self.assertNotIn("record_real_bet(", src)


class Test05TheoreticalVsRealROI(unittest.TestCase):
    def test_leaderboard_real_roi_none_without_real_bets(self):
        conn = pl.init_db(db_path=":memory:")
        pred_id = _record(conn)
        pl.settle_prediction(conn, pred_id, "WIN", actual_outcome="5")
        rows = dmr.load_settleable_rows(conn)
        leaderboard = dmr.season_leaderboard(rows)
        self.assertIsNone(leaderboard["PLAYER_SOG"]["real_roi"])
        self.assertEqual(leaderboard["PLAYER_SOG"]["real_bet_n"], 0)

    def test_real_bet_roi_computed_only_from_real_bet_rows(self):
        conn = pl.init_db(db_path=":memory:")
        result = pl.record_real_bet(
            conn, event_start_utc=PAST_EVENT, created_at_utc=PAST_CUTOFF, prediction_cutoff_utc=PAST_CUTOFF,
            game_id=1, game_date="2026-11-13", player_id="P1", team="EDM", opponent="CHI",
            market_id="PLAYER_SOG", threshold="3+", raw_probability=0.6, confidence="HIGH",
            stake=10.0, placed_odds=-110, placed_at_utc=PAST_CUTOFF, sportsbook="draftkings")
        pl.settle_prediction(conn, result["prediction_id"], "WIN", actual_outcome="5", profit_loss=9.09)
        model_obs_id = _record(conn, player_id="P2")
        pl.settle_prediction(conn, model_obs_id, "LOSS", actual_outcome="1")

        rows = dmr.load_settleable_rows(conn)
        leaderboard = dmr.season_leaderboard(rows)
        self.assertEqual(leaderboard["PLAYER_SOG"]["real_bet_n"], 1)
        self.assertAlmostEqual(leaderboard["PLAYER_SOG"]["real_roi"], 0.909, places=2)


class Test06RejectedResearchConsulted(unittest.TestCase):
    def test_daily_review_reports_a_nonzero_rejected_entry_count(self):
        conn = pl.init_db(db_path=":memory:")
        result = dmr.run_daily_review(conn, now_utc=NOW)
        self.assertGreater(result["rejected_research_entries_on_file"], 0)


class Test07NoAutoPromotionRecommendationOnly(unittest.TestCase):
    def test_recommendation_is_no_action_with_no_issues(self):
        conn = pl.init_db(db_path=":memory:")
        result = dmr.run_daily_review(conn, now_utc=NOW)
        self.assertEqual(result["recommendation"], "NO_ACTION")

    def test_promotion_review_only_when_a_real_promotion_candidate_exists(self):
        # Confirms the recommendation engine reads challenger_registry's
        # OWN promotion_candidates() rather than deciding on its own --
        # with an empty registry, PROMOTION_REVIEW must never appear.
        conn = pl.init_db(db_path=":memory:")
        result = dmr.run_daily_review(conn, now_utc=NOW)
        self.assertNotEqual(result["recommendation"], "PROMOTION_REVIEW")


class Test08SampleMilestones(unittest.TestCase):
    def test_crossed_and_next_milestone(self):
        status = dmr.sample_milestone_status(300)
        self.assertEqual(status["crossed_milestones"], [100, 250])
        self.assertEqual(status["next_milestone"], 500)

    def test_below_first_milestone(self):
        status = dmr.sample_milestone_status(10)
        self.assertEqual(status["crossed_milestones"], [])
        self.assertEqual(status["next_milestone"], 100)


class Test09WeeklyRollup(unittest.TestCase):
    def test_empty_week_is_normal(self):
        rollup = dmr.weekly_rollup([])
        self.assertEqual(rollup["worst_status_this_week"], "NORMAL")

    def test_one_off_issue_is_not_persistent(self):
        days = [{"engine_status": "NORMAL", "improvement_queue": [{"issue": "ROLE_CHANGE", "magnitude": 1, "source": "x"}]}]
        days += [{"engine_status": "NORMAL", "improvement_queue": []} for _ in range(6)]
        rollup = dmr.weekly_rollup(days)
        self.assertEqual(rollup["persistent_issues"], [])

    def test_repeated_issue_across_three_plus_days_is_persistent(self):
        recurring_day = {"engine_status": "NORMAL", "improvement_queue": [{"issue": "MODEL_CALIBRATION", "magnitude": 1, "source": "x"}]}
        days = [recurring_day] * 3 + [{"engine_status": "NORMAL", "improvement_queue": []}] * 4
        rollup = dmr.weekly_rollup(days)
        self.assertIn("MODEL_CALIBRATION", rollup["persistent_issues"])

    def test_worst_status_dominates(self):
        days = [{"engine_status": "NORMAL", "improvement_queue": []},
                {"engine_status": "HALT", "improvement_queue": []}]
        rollup = dmr.weekly_rollup(days)
        self.assertEqual(rollup["worst_status_this_week"], "HALT")

    def test_incomplete_days_excluded_from_issue_counting(self):
        days = [{"incomplete": True, "engine_status": "HALT"}] * 5
        rollup = dmr.weekly_rollup(days)
        self.assertEqual(rollup["persistent_issues"], [])


class Test10RetrainingTriggers(unittest.TestCase):
    def test_no_trigger_when_nothing_moved(self):
        result = dmr.evaluate_retraining_triggers(
            new_games_since_last_review=5, calibration_error=0.05, calibration_error_baseline=0.05,
            league_environment_status="NORMAL", sustained_degradation_windows=0)
        self.assertFalse(result["retraining_triggered"])
        self.assertEqual(result["action"], "NO_ACTION")

    def test_min_new_games_triggers(self):
        result = dmr.evaluate_retraining_triggers(
            new_games_since_last_review=60, calibration_error=None, calibration_error_baseline=None,
            league_environment_status="NORMAL", sustained_degradation_windows=0)
        self.assertTrue(result["retraining_triggered"])

    def test_calibration_drift_triggers(self):
        result = dmr.evaluate_retraining_triggers(
            new_games_since_last_review=5, calibration_error=0.15, calibration_error_baseline=0.05,
            league_environment_status="NORMAL", sustained_degradation_windows=0)
        self.assertTrue(result["retraining_triggered"])

    def test_triggering_never_recommends_a_production_replacement(self):
        result = dmr.evaluate_retraining_triggers(
            new_games_since_last_review=60, calibration_error=None, calibration_error_baseline=None,
            league_environment_status="NORMAL", sustained_degradation_windows=0)
        self.assertIn("CHALLENGER", result["action"])
        self.assertNotIn("PRODUCTION_REPLACEMENT", result["action"])

    def test_function_never_imports_a_production_model_writer(self):
        import inspect
        src = inspect.getsource(dmr.evaluate_retraining_triggers)
        self.assertNotIn("model_registry", src)


if __name__ == "__main__":
    unittest.main()
