"""Tests for operational/daily_postmortem.py (Parts 54-69)."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from operational import challenger_registry as cr
from operational import daily_postmortem as dpm
from operational import paper_bankroll as pb


def _loss_row(**overrides):
    row = {"result_status": "LOSS", "is_combo": 0, "market_family": "PLAYER_SOG", "confidence": "HIGH"}
    row.update(overrides)
    return row


class TestClassifyFailure(unittest.TestCase):
    def test_win_row_raises(self):
        with self.assertRaises(ValueError):
            dpm.classify_failure({"result_status": "WIN"})

    def test_small_residual_is_normal_variance(self):
        result = dpm.classify_failure(_loss_row(), context={"residual": 0.1})
        self.assertEqual(result, dpm.NORMAL_VARIANCE)

    def test_data_pipeline_error_flagged_first(self):
        result = dpm.classify_failure(_loss_row(), context={"residual": 2.0, "data_pipeline_error": True})
        self.assertEqual(result, dpm.DATA_PIPELINE_ERROR)

    def test_ambiguous_identity_match(self):
        result = dpm.classify_failure(_loss_row(), context={"residual": 2.0, "identity_match_status": "AMBIGUOUS"})
        self.assertEqual(result, dpm.IDENTITY_LINEUP_ERROR)

    def test_stale_data(self):
        result = dpm.classify_failure(_loss_row(), context={"residual": 2.0, "data_freshness_hours": 10.0})
        self.assertEqual(result, dpm.STALE_DATA)

    def test_market_moved(self):
        result = dpm.classify_failure(_loss_row(), context={"residual": 2.0, "market_price_moved_significantly": True})
        self.assertEqual(result, dpm.MARKET_MOVED)

    def test_dependence_error_only_for_combo_bets(self):
        straight = dpm.classify_failure(_loss_row(is_combo=0), context={"residual": 2.0, "dependence_assumption_violated": True})
        combo = dpm.classify_failure(_loss_row(is_combo=1), context={"residual": 2.0, "dependence_assumption_violated": True})
        self.assertNotEqual(straight, dpm.DEPENDENCE_ERROR)
        self.assertEqual(combo, dpm.DEPENDENCE_ERROR)

    def test_role_change_missed(self):
        result = dpm.classify_failure(_loss_row(), context={"residual": 2.0, "role_transition": True})
        self.assertEqual(result, dpm.ROLE_CHANGE_MISSED)

    def test_starter_error(self):
        result = dpm.classify_failure(_loss_row(), context={"residual": 2.0, "starter_uncertain_at_lock": True})
        self.assertEqual(result, dpm.STARTER_ERROR)

    def test_goalie_workload_error_only_for_saves_market(self):
        result = dpm.classify_failure(_loss_row(market_family="GOALIE_SAVES"),
                                       context={"residual": 2.0, "team_sog_actual_vs_projected_gap": -5.0})
        self.assertEqual(result, dpm.GOALIE_WORKLOAD_ERROR)

    def test_toi_projection_error(self):
        result = dpm.classify_failure(_loss_row(), context={"residual": 2.0, "toi_actual_vs_projected_gap": -180})
        self.assertEqual(result, dpm.TOI_PROJECTION_ERROR)

    def test_team_shot_environment_error(self):
        result = dpm.classify_failure(_loss_row(), context={"residual": 2.0, "team_shot_environment_gap": -8.0})
        self.assertEqual(result, dpm.TEAM_SHOT_ENVIRONMENT_ERROR)

    def test_model_calibration_when_high_confidence_and_large_residual(self):
        result = dpm.classify_failure(_loss_row(confidence="HIGH"), context={"residual": 3.0})
        self.assertEqual(result, dpm.MODEL_CALIBRATION)

    def test_unknown_when_nothing_explains_it(self):
        result = dpm.classify_failure(_loss_row(confidence="MEDIUM"), context={"residual": 3.0})
        self.assertEqual(result, dpm.UNKNOWN)

    def test_no_context_at_all_is_unknown_or_normal_variance_never_crashes(self):
        result = dpm.classify_failure(_loss_row())
        self.assertIn(result, dpm.FAILURE_TAXONOMY)


class TestSummarizeFailures(unittest.TestCase):
    def test_counts_every_category(self):
        summary = dpm.summarize_failures([dpm.NORMAL_VARIANCE, dpm.NORMAL_VARIANCE, dpm.UNKNOWN])
        self.assertEqual(summary[dpm.NORMAL_VARIANCE], 2)
        self.assertEqual(summary[dpm.UNKNOWN], 1)
        self.assertEqual(summary[dpm.STARTER_ERROR], 0)


class TestRecommendedActionForPattern(unittest.TestCase):
    def test_normal_variance_is_always_no_action(self):
        action = dpm.recommended_action_for_pattern(dpm.NORMAL_VARIANCE, occurrences=100, unique_game_dates=50)
        self.assertEqual(action, dpm.NO_ACTION)

    def test_single_occurrence_is_no_action(self):
        action = dpm.recommended_action_for_pattern(dpm.MODEL_CALIBRATION, occurrences=1, unique_game_dates=1)
        self.assertEqual(action, dpm.NO_ACTION)

    def test_small_pattern_is_watch(self):
        action = dpm.recommended_action_for_pattern(dpm.MODEL_CALIBRATION, occurrences=2, unique_game_dates=2)
        self.assertEqual(action, dpm.WATCH)

    def test_moderate_pattern_below_challenger_bar_is_investigate(self):
        action = dpm.recommended_action_for_pattern(dpm.MODEL_CALIBRATION, occurrences=4, unique_game_dates=2,
                                                      explanation="real pattern")
        self.assertEqual(action, dpm.INVESTIGATE)

    def test_data_pipeline_error_is_always_bug_fix_once_repeated(self):
        action = dpm.recommended_action_for_pattern(dpm.DATA_PIPELINE_ERROR, occurrences=2, unique_game_dates=1)
        self.assertEqual(action, dpm.BUG_FIX)

    def test_full_challenger_evidence_bar_yields_challenger_idea(self):
        action = dpm.recommended_action_for_pattern(
            dpm.MODEL_CALIBRATION, occurrences=cr.MIN_REPEATED_OCCURRENCES,
            unique_game_dates=cr.MIN_UNIQUE_GAME_DATES, explanation="a real, repeated pattern")
        self.assertEqual(action, dpm.CHALLENGER_IDEA)

    def test_one_bad_game_can_never_reach_challenger_idea(self):
        # Part 63: "one bad game cannot promote a challenger."
        action = dpm.recommended_action_for_pattern(dpm.MODEL_CALIBRATION, occurrences=1, unique_game_dates=1,
                                                      explanation="looks real but is a single game")
        self.assertNotEqual(action, dpm.CHALLENGER_IDEA)


class TestBugFixAndChallengerGeneration(unittest.TestCase):
    def test_generate_bug_fix_candidate_has_required_fields(self):
        candidate = dpm.generate_bug_fix_candidate(
            symptom="x", evidence="y", likely_root_cause="z",
            files_involved=["a.py"], reproduction="steps", suggested_fix_scope="small")
        self.assertEqual(candidate["type"], "BUG_FIX_CANDIDATE")
        self.assertIn("generated_at_utc", candidate)

    def test_generate_challenger_idea_draft_never_touches_registry(self):
        with tempfile.TemporaryDirectory() as d:
            registry_path = Path(d) / "registry.json"
            draft = dpm.generate_challenger_idea_draft(
                target_model="PLAYER_SOG", hypothesis="h", affected_market="PLAYER_SOG",
                occurrences=5, unique_game_dates=3, mean_residual=1.2, explanation="e",
                required_minimum_sample="10 games", proposed_evaluation="backtest",
                promotion_criteria="beats champion by X")
            self.assertFalse(registry_path.exists())
            self.assertEqual(draft["type"], "CHALLENGER_IDEA_DRAFT")

    def test_submit_challenger_idea_writes_to_the_real_registry_when_evidence_clears(self):
        with tempfile.TemporaryDirectory() as d:
            registry_path = Path(d) / "registry.json"
            draft = dpm.generate_challenger_idea_draft(
                target_model="PLAYER_SOG", hypothesis="h", affected_market="PLAYER_SOG",
                occurrences=cr.MIN_REPEATED_OCCURRENCES, unique_game_dates=cr.MIN_UNIQUE_GAME_DATES,
                mean_residual=1.2, explanation="a real repeated pattern",
                required_minimum_sample="10 games", proposed_evaluation="backtest",
                promotion_criteria="beats champion")
            result = dpm.submit_challenger_idea(draft, training_window="2026-27", validation_plan="backtest",
                                                 registry_path=registry_path)
            self.assertEqual(result["status"], "HYPOTHESIS")
            self.assertTrue(registry_path.exists())

    def test_submit_challenger_idea_refuses_insufficient_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            registry_path = Path(d) / "registry.json"
            draft = dpm.generate_challenger_idea_draft(
                target_model="PLAYER_SOG", hypothesis="h", affected_market="PLAYER_SOG",
                occurrences=1, unique_game_dates=1, mean_residual=1.2, explanation="e",
                required_minimum_sample="10 games", proposed_evaluation="backtest",
                promotion_criteria="beats champion")
            with self.assertRaises(cr.ChallengerValidationError):
                dpm.submit_challenger_idea(draft, training_window="2026-27", validation_plan="backtest",
                                            registry_path=registry_path)


class TestScoreboardAndParlayHealth(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "test.db"
        self.conn = pb.init_db(self.db_path)

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def test_scoreboard_covers_every_track(self):
        scoreboard = dpm.build_daily_scoreboard(self.conn)
        self.assertEqual(set(scoreboard["tracks"].keys()), set(pb.TRACKS))

    def test_parlay_health_honest_when_nothing_settled(self):
        health = dpm.build_parlay_health(self.conn)
        self.assertEqual(health["status"], "WAITING_FOR_SETTLED_DATA")
        self.assertIsNone(health["actual_hit_rate"])

    def test_parlay_health_computes_real_numbers_once_settled(self):
        import json
        pb.record_paper_bet(self.conn, track="GAME_PARLAY_PAPER", price_source="SIMULATED_DEMO",
                             market_id="GAME_EDGE_PARLAY:x", entry_odds=180, is_combo=True,
                             legs_json=json.dumps([{}, {}, {}]), conservative_probability=0.6, edge=0.05,
                             event_id="evt-1")
        row = pb.query_paper_bets(self.conn, track="GAME_PARLAY_PAPER")[0]
        pb.settle_paper_bet(self.conn, row["paper_bet_id"], "WIN")
        health = dpm.build_parlay_health(self.conn)
        self.assertEqual(health["status"], "OK")
        self.assertEqual(health["settled_parlays"], 1)
        self.assertEqual(health["actual_hit_count"], 1)
        self.assertAlmostEqual(health["calibration_gap"], 1.0 - 0.6)
        self.assertEqual(health["by_leg_count"][3]["wins"], 1)


class TestRunDailyPostmortem(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "test.db"
        self.conn = pb.init_db(self.db_path)

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def test_honest_waiting_state_with_no_data(self):
        report = dpm.run_daily_postmortem(self.conn)
        self.assertEqual(report["what_worked"], "WAITING_FOR_SETTLED_DATA")
        self.assertEqual(report["parlay_health"]["status"], "WAITING_FOR_SETTLED_DATA")

    def test_never_writes_to_challenger_registry_by_itself(self):
        issues = [{"category": dpm.MODEL_CALIBRATION, "occurrences": 10, "unique_game_dates": 5,
                   "explanation": "real pattern"}]
        with tempfile.TemporaryDirectory() as d:
            registry_path = Path(d) / "registry.json"
            with mock.patch.object(cr, "REGISTRY_PATH", registry_path):
                report = dpm.run_daily_postmortem(self.conn, classified_failures=issues)
            self.assertFalse(registry_path.exists())  # never auto-written
        self.assertEqual(len(report["challenger_ideas"]), 1)

    def test_zero_settled_results_is_no_data_not_a_variance_conclusion(self):
        report = dpm.run_daily_postmortem(self.conn)
        self.assertTrue(report["normal_variance_vs_systematic"].startswith("NO_DATA"))
        self.assertNotIn("NORMAL_VARIANCE", report["normal_variance_vs_systematic"])

    def test_report_has_all_required_sections(self):
        report = dpm.run_daily_postmortem(self.conn)
        for key in ("what_worked", "what_didnt", "why", "normal_variance_vs_systematic",
                    "investigate", "software_bug_candidates", "challenger_ideas",
                    "scoreboard", "parlay_health", "failure_summary"):
            self.assertIn(key, report)


class TestWriteReportMarkdown(unittest.TestCase):
    def test_writes_a_real_file(self):
        with tempfile.TemporaryDirectory() as d:
            out_dir = Path(d)
            report = {
                "what_worked": "x", "what_didnt": "y", "why": ["z"],
                "normal_variance_vs_systematic": "NORMAL_VARIANCE", "investigate": [],
            }
            path = dpm.write_report_markdown(report, out_dir=out_dir)
            self.assertTrue(path.exists())
            self.assertIn("Daily Post-Mortem", path.read_text())


if __name__ == "__main__":
    unittest.main()
