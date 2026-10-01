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


class TestRealMarketParlayHealth(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "test.db"
        self.conn = pb.init_db(self.db_path)

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def test_waiting_state_with_no_settled_data(self):
        health = dpm.build_real_market_parlay_health(self.conn)
        self.assertEqual(health["status"], "WAITING_FOR_SETTLED_DATA")
        self.assertEqual(health["settled_parlays"], 0)

    def test_computes_real_numbers_once_settled(self):
        import json
        pb.record_paper_bet(self.conn, track="REAL_MARKET_PAPER", price_source="LIVE_DRAFTKINGS",
                             market_id="REAL_MARKET_PARLAY:x", entry_odds=180, is_combo=True,
                             legs_json=json.dumps([{"market_family": "MONEYLINE"},
                                                    {"market_family": "PLAYER_SOG"},
                                                    {"market_family": "PLAYER_SOG"}]),
                             conservative_probability=0.75, edge=0.05, event_id="evt-1")
        row = pb.query_paper_bets(self.conn, track="REAL_MARKET_PAPER")[0]
        pb.settle_paper_bet(self.conn, row["paper_bet_id"], "WIN")
        health = dpm.build_real_market_parlay_health(self.conn)
        self.assertEqual(health["status"], "OK")
        self.assertEqual(health["settled_parlays"], 1)
        self.assertEqual(health["actual_hit_count"], 1)
        self.assertAlmostEqual(health["calibration_gap"], 1.0 - 0.75)
        self.assertEqual(health["by_leg_count"][3]["wins"], 1)
        self.assertEqual(health["by_market_family_combo"]["MONEYLINE+PLAYER_SOG"]["wins"], 1)


class TestRealMarketParlayLossPostmortems(unittest.TestCase):
    def setUp(self):
        import db
        import json
        self._tmp = tempfile.TemporaryDirectory()
        self.bankroll_conn = pb.init_db(Path(self._tmp.name) / "bankroll.db")
        self.nhl_conn = db.init_db(db_path=Path(self._tmp.name) / "nhl.db", wipe=True)
        self.json = json
        for t in ("TOR", "MTL"):
            self.nhl_conn.execute("INSERT OR IGNORE INTO teams (team_id, full_name) VALUES (?, ?)", (t, t))
        self.nhl_conn.execute(
            "INSERT INTO games (game_id, season, game_date, home_team, away_team, game_state, "
            "home_score, away_score, final_period_type, source) VALUES (1,'20262027','2026-10-15', "
            "'TOR','MTL','FINAL',4,2,'REG','test')")
        self.nhl_conn.execute(
            "INSERT INTO player_game_stats (game_id, player_id, team_id, toi_minutes, goals, assists, "
            "shots, hits, blocked_shots, played, revision_number, effective_at_utc, observed_at_utc, source) "
            "VALUES (1,'P1','TOR',18.0,0,0,1,NULL,NULL,1,1,'2026-10-15T23:30:00Z','2026-10-15T23:30:00Z','test')")
        self.nhl_conn.commit()

    def tearDown(self):
        self.bankroll_conn.close()
        self.nhl_conn.close()
        self._tmp.cleanup()

    def _leg(self, participant_id="P1", market_family="PLAYER_SOG", threshold=4, side="OVER", game_id="1"):
        return {"game_id": game_id, "market_family": market_family, "threshold": threshold, "side": side,
                "participant_id": participant_id, "participant_name": participant_id}

    def test_a_real_loss_identifies_the_missed_leg_with_its_actual_value(self):
        legs = [self._leg(participant_id="P1", threshold=4)]  # P1 only got 1 real shot -- a real miss
        pb.record_paper_bet(self.bankroll_conn, track="REAL_MARKET_PAPER", price_source="LIVE_DRAFTKINGS",
                             market_id="REAL_MARKET_PARLAY:x", entry_odds=150, is_combo=True,
                             legs_json=self.json.dumps(legs), conservative_probability=0.75, edge=0.05,
                             event_id="evt-1")
        row = pb.query_paper_bets(self.bankroll_conn, track="REAL_MARKET_PAPER")[0]
        pb.settle_paper_bet(self.bankroll_conn, row["paper_bet_id"], "LOSS")

        postmortems = dpm.real_market_parlay_loss_postmortems(self.bankroll_conn, self.nhl_conn)
        self.assertEqual(len(postmortems), 1)
        pm = postmortems[0]
        self.assertEqual(len(pm["missed_legs"]), 1)
        self.assertEqual(pm["missed_legs"][0]["actual_value"], 1)
        self.assertIn("P1", pm["why"])
        self.assertIn("actual=1", pm["why"])

    def test_a_win_row_is_never_included(self):
        legs = [self._leg(participant_id="P1", threshold=1)]  # P1's real 1 shot clears 1+
        pb.record_paper_bet(self.bankroll_conn, track="REAL_MARKET_PAPER", price_source="LIVE_DRAFTKINGS",
                             market_id="REAL_MARKET_PARLAY:x", entry_odds=150, is_combo=True,
                             legs_json=self.json.dumps(legs), conservative_probability=0.9, edge=0.05,
                             event_id="evt-1")
        row = pb.query_paper_bets(self.bankroll_conn, track="REAL_MARKET_PAPER")[0]
        pb.settle_paper_bet(self.bankroll_conn, row["paper_bet_id"], "WIN")
        postmortems = dpm.real_market_parlay_loss_postmortems(self.bankroll_conn, self.nhl_conn)
        self.assertEqual(postmortems, [])

    def test_no_losses_is_an_empty_list_not_an_error(self):
        postmortems = dpm.real_market_parlay_loss_postmortems(self.bankroll_conn, self.nhl_conn)
        self.assertEqual(postmortems, [])


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
                    "scoreboard", "parlay_health", "real_market_parlay_health",
                    "real_market_parlay_postmortems", "real_market_parlay_leg_miss_patterns",
                    "failure_summary"):
            self.assertIn(key, report)

    def test_real_market_sections_say_so_honestly_when_nhl_conn_omitted(self):
        report = dpm.run_daily_postmortem(self.conn)
        self.assertIn("NHL_CONN_NOT_PROVIDED", report["real_market_parlay_postmortems"])
        self.assertIn("NHL_CONN_NOT_PROVIDED", report["real_market_parlay_leg_miss_patterns"])

    def test_real_market_postmortems_populate_when_nhl_conn_provided(self):
        import db
        import json
        import tempfile as _tempfile
        with _tempfile.TemporaryDirectory() as d:
            nhl_conn = db.init_db(db_path=Path(d) / "nhl.db", wipe=True)
            for t in ("TOR", "MTL"):
                nhl_conn.execute("INSERT OR IGNORE INTO teams (team_id, full_name) VALUES (?, ?)", (t, t))
            nhl_conn.execute(
                "INSERT INTO games (game_id, season, game_date, home_team, away_team, game_state, "
                "home_score, away_score, final_period_type, source) VALUES (1,'20262027','2026-10-15', "
                "'TOR','MTL','FINAL',4,2,'REG','test')")
            nhl_conn.execute(
                "INSERT INTO player_game_stats (game_id, player_id, team_id, toi_minutes, goals, assists, "
                "shots, hits, blocked_shots, played, revision_number, effective_at_utc, observed_at_utc, "
                "source) VALUES (1,'P1','TOR',18.0,0,0,1,NULL,NULL,1,1,'2026-10-15T23:30:00Z',"
                "'2026-10-15T23:30:00Z','test')")
            nhl_conn.commit()
            legs = [{"game_id": "1", "market_family": "PLAYER_SOG", "threshold": 4, "side": "OVER",
                     "participant_id": "P1", "participant_name": "P1"}]
            pb.record_paper_bet(self.conn, track="REAL_MARKET_PAPER", price_source="LIVE_DRAFTKINGS",
                                 market_id="REAL_MARKET_PARLAY:x", entry_odds=150, is_combo=True,
                                 legs_json=json.dumps(legs), conservative_probability=0.75, edge=0.05,
                                 event_id="evt-1")
            row = pb.query_paper_bets(self.conn, track="REAL_MARKET_PAPER")[0]
            pb.settle_paper_bet(self.conn, row["paper_bet_id"], "LOSS")

            report = dpm.run_daily_postmortem(self.conn, nhl_conn=nhl_conn)
            self.assertEqual(len(report["real_market_parlay_postmortems"]), 1)
            self.assertEqual(report["real_market_parlay_leg_miss_patterns"][0]["market_family"], "PLAYER_SOG")
            nhl_conn.close()


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
