"""P0.5 (2026-09-24 hardening block): tests for opening_day_readiness.py's
verdict logic. Real function calls against real local state where safe
(database reachability, model registry loading); mocked where a real
failure would be needed to prove NOT_READY without actually breaking
anything."""
from __future__ import annotations

import unittest
from unittest import mock

import opening_day_readiness as odr


class TestDatabaseChecks(unittest.TestCase):
    def test_real_databases_are_reachable_and_writable(self):
        checks = odr.check_databases()
        for name, row in checks.items():
            self.assertTrue(row["reachable"], f"{name}: {row['error']}")
            self.assertTrue(row["writable"], f"{name}: {row['error']}")

    def test_probe_writes_are_never_committed(self):
        """The readiness probe must never leave real state behind."""
        import db
        conn = db.get_conn()
        before = conn.execute(
            "SELECT COUNT(*) c FROM sqlite_master WHERE type='table' AND name='_readiness_probe'"
        ).fetchone()["c"]
        odr.check_databases()
        after = conn.execute(
            "SELECT COUNT(*) c FROM sqlite_master WHERE type='table' AND name='_readiness_probe'"
        ).fetchone()["c"]
        conn.close()
        # A TEMP TABLE only ever exists for the life of its own connection
        # anyway, but this locks in that no PERMANENT trace is left.
        self.assertEqual(before, after)


class TestModelChecks(unittest.TestCase):
    def test_real_model_registry_loads(self):
        result = odr.check_models()
        self.assertTrue(result["registry_loads"])
        self.assertGreater(result["entry_count"], 0)


class TestPredictionIntegrity(unittest.TestCase):
    def test_real_ledger_has_no_duplicate_or_invalid_state_predictions(self):
        result = odr.check_predictions()
        self.assertIsNone(result["error"])
        self.assertEqual(result["duplicate_idempotency_keys"], 0)
        self.assertEqual(result["invalid_result_states"], 0)


class TestRealRecommendationPipelineCheck(unittest.TestCase):
    """Real Recommendation Pipeline block (2026-09-24), Part 26."""

    def test_real_orchestration_modules_import_cleanly_against_real_state(self):
        result = odr.check_real_recommendation_pipeline()
        self.assertEqual(result["orchestration_status"], "HEALTHY")
        self.assertIsNone(result["orchestration_import_error"])
        self.assertIsNone(result["query_error"])

    def test_zero_real_market_paper_bets_is_not_a_failure_by_itself(self):
        """Part 26's exact requirement: 'REAL RECOMMENDATION PIPELINE:
        HEALTHY / QUALIFYING BETS: 0' must be a valid, reportable state,
        never conflated with a broken pipeline."""
        with mock.patch("db.get_conn") as mock_db_conn:
            mock_db_conn.return_value.execute.return_value.fetchone.return_value = {"c": 0}
            with mock.patch("operational.prospective_ledger.init_db") as mock_pl, \
                 mock.patch("operational.paper_bankroll.init_db") as mock_pb:
                mock_pl.return_value.execute.return_value.fetchone.return_value = {"c": 0}
                mock_pb.return_value.execute.return_value.fetchone.return_value = {"c": 0}
                result = odr.check_real_recommendation_pipeline()
        self.assertEqual(result["orchestration_status"], "HEALTHY")
        self.assertEqual(result["real_market_paper_bets_placed"], 0)

    def test_orchestration_import_failure_is_not_operational(self):
        with mock.patch("builtins.__import__", side_effect=ImportError("boom")):
            result = odr.check_real_recommendation_pipeline()
        self.assertEqual(result["orchestration_status"], "NOT_OPERATIONAL")
        self.assertIn("boom", result["orchestration_import_error"])

    def test_not_operational_pipeline_forces_readiness_hard_failure(self):
        with mock.patch.object(odr, "check_databases",
                                return_value={"nhl.db": {"reachable": True, "writable": True, "error": None}}), \
             mock.patch.object(odr, "check_models", return_value={"registry_loads": True, "entry_count": 1,
                                                                    "active_versions": {}, "error": None}), \
             mock.patch.object(odr, "check_predictions",
                                return_value={"duplicate_idempotency_keys": 0, "invalid_result_states": 0,
                                              "stale_pending_over_24h": 0, "error": None}), \
             mock.patch.object(odr, "check_odds", return_value={"collection_status": {"status": "OK"}, "scheduler": {}}), \
             mock.patch.object(odr, "check_pipeline", return_value={"components": {}, "scheduler_loaded": {}}), \
             mock.patch.object(odr, "check_nhl", return_value={}), \
             mock.patch.object(odr, "check_context", return_value={}), \
             mock.patch.object(odr, "check_yahoo", return_value={"status": "CONNECTED"}), \
             mock.patch.object(odr, "check_real_recommendation_pipeline",
                                return_value={"orchestration_status": "NOT_OPERATIONAL",
                                              "orchestration_import_error": "boom", "query_error": None,
                                              "real_odds_snapshot_rows": None,
                                              "real_moneyline_recommendations_recorded": None,
                                              "real_market_paper_bets_placed": None}), \
             mock.patch.object(odr, "_launchctl_loaded_count", return_value=len(odr.sh._SCHEDULER_LABELS)):
            report = odr.build_readiness_report()
        self.assertEqual(report["verdict"], odr.NOT_READY)
        self.assertTrue(any("real recommendation pipeline" in f for f in report["hard_failures"]))


class TestRealPropPipelineCheck(unittest.TestCase):
    """Live SOG + Saves Production Certification block (2026-09-24), Part
    39."""

    def test_real_state_reports_pending_live_contract_for_both_markets(self):
        """The real, current, honest state: DraftKings has never posted
        either market (docs/LIVE_SOG_SAVES_CERTIFICATION.md) -- this must
        read as PENDING_LIVE_CONTRACT, never READY (fabricated) or
        NOT_READY (this is not a failure)."""
        result = odr.check_real_prop_pipeline()
        self.assertEqual(result["orchestration_status"], "HEALTHY")
        self.assertEqual(result["sog_status"], "PENDING_LIVE_CONTRACT")
        self.assertEqual(result["saves_status"], "PENDING_LIVE_CONTRACT")
        self.assertEqual(result["game_edge_parlay_status"], "PARTIAL")

    def test_a_verified_contract_reports_ready_not_pending(self):
        from research.generic_prop_pricing import provider_adapter as pa
        with mock.patch.object(pa, "VERIFIED_CONTRACTS",
                               frozenset({("draftkings", "MONEYLINE"), ("draftkings", "PLAYER_SOG")})):
            result = odr.check_real_prop_pipeline()
        self.assertEqual(result["sog_status"], "READY")
        self.assertEqual(result["saves_status"], "PENDING_LIVE_CONTRACT")

    def test_orchestration_import_failure_marks_both_markets_not_ready(self):
        with mock.patch("builtins.__import__", side_effect=ImportError("boom")):
            result = odr.check_real_prop_pipeline()
        self.assertEqual(result["orchestration_status"], "NOT_OPERATIONAL")
        self.assertEqual(result["sog_status"], "NOT_READY")
        self.assertEqual(result["saves_status"], "NOT_READY")
        self.assertEqual(result["game_edge_parlay_status"], "NOT_READY")

    def test_pending_live_contract_never_forces_a_hard_failure(self):
        with mock.patch.object(odr, "check_databases",
                                return_value={"nhl.db": {"reachable": True, "writable": True, "error": None}}), \
             mock.patch.object(odr, "check_models", return_value={"registry_loads": True, "entry_count": 1,
                                                                    "active_versions": {}, "error": None}), \
             mock.patch.object(odr, "check_predictions",
                                return_value={"duplicate_idempotency_keys": 0, "invalid_result_states": 0,
                                              "stale_pending_over_24h": 0, "error": None}), \
             mock.patch.object(odr, "check_odds", return_value={"collection_status": {"status": "OK"}, "scheduler": {}}), \
             mock.patch.object(odr, "check_pipeline", return_value={"components": {}, "scheduler_loaded": {}}), \
             mock.patch.object(odr, "check_nhl", return_value={}), \
             mock.patch.object(odr, "check_context", return_value={}), \
             mock.patch.object(odr, "check_yahoo", return_value={"status": "CONNECTED"}), \
             mock.patch.object(odr, "check_real_recommendation_pipeline",
                                return_value={"orchestration_status": "HEALTHY", "orchestration_import_error": None,
                                              "query_error": None, "real_odds_snapshot_rows": 0,
                                              "real_moneyline_recommendations_recorded": 0,
                                              "real_market_paper_bets_placed": 0}), \
             mock.patch.object(odr, "check_real_prop_pipeline",
                                return_value={"orchestration_status": "HEALTHY", "orchestration_import_error": None,
                                              "query_error": None, "sog_status": "PENDING_LIVE_CONTRACT",
                                              "saves_status": "PENDING_LIVE_CONTRACT",
                                              "game_edge_parlay_status": "PARTIAL"}), \
             mock.patch.object(odr, "_launchctl_loaded_count", return_value=len(odr.sh._SCHEDULER_LABELS)):
            report = odr.build_readiness_report()
        self.assertEqual(report["verdict"], odr.READY)
        self.assertEqual(report["hard_failures"], [])


class TestVerdictLogic(unittest.TestCase):
    def test_unreachable_database_forces_not_ready(self):
        with mock.patch.object(odr, "check_databases",
                                return_value={"nhl.db": {"reachable": False, "writable": False, "error": "boom"}}), \
             mock.patch.object(odr, "check_models", return_value={"registry_loads": True, "entry_count": 1,
                                                                    "active_versions": {}, "error": None}), \
             mock.patch.object(odr, "check_predictions",
                                return_value={"duplicate_idempotency_keys": 0, "invalid_result_states": 0,
                                              "stale_pending_over_24h": 0, "error": None}), \
             mock.patch.object(odr, "check_odds", return_value={"collection_status": {"status": "OK"}, "scheduler": {}}), \
             mock.patch.object(odr, "check_pipeline", return_value={"components": {}, "scheduler_loaded": {}}), \
             mock.patch.object(odr, "check_nhl", return_value={}), \
             mock.patch.object(odr, "check_context", return_value={}), \
             mock.patch.object(odr, "check_yahoo", return_value={"status": "CONNECTED"}), \
             mock.patch.object(odr, "check_real_recommendation_pipeline",
                                return_value={"orchestration_status": "HEALTHY", "orchestration_import_error": None,
                                              "query_error": None, "real_odds_snapshot_rows": 0,
                                              "real_moneyline_recommendations_recorded": 0,
                                              "real_market_paper_bets_placed": 0}), \
             mock.patch.object(odr, "_launchctl_loaded_count", return_value=len(odr.sh._SCHEDULER_LABELS)):
            report = odr.build_readiness_report()
        self.assertEqual(report["verdict"], odr.NOT_READY)
        self.assertTrue(any("nhl.db" in f for f in report["hard_failures"]))

    def test_duplicate_idempotency_keys_force_not_ready(self):
        with mock.patch.object(odr, "check_databases",
                                return_value={"nhl.db": {"reachable": True, "writable": True, "error": None}}), \
             mock.patch.object(odr, "check_models", return_value={"registry_loads": True, "entry_count": 1,
                                                                    "active_versions": {}, "error": None}), \
             mock.patch.object(odr, "check_predictions",
                                return_value={"duplicate_idempotency_keys": 2, "invalid_result_states": 0,
                                              "stale_pending_over_24h": 0, "error": None}), \
             mock.patch.object(odr, "check_odds", return_value={"collection_status": {"status": "OK"}, "scheduler": {}}), \
             mock.patch.object(odr, "check_pipeline", return_value={"components": {}, "scheduler_loaded": {}}), \
             mock.patch.object(odr, "check_nhl", return_value={}), \
             mock.patch.object(odr, "check_context", return_value={}), \
             mock.patch.object(odr, "check_yahoo", return_value={"status": "CONNECTED"}), \
             mock.patch.object(odr, "check_real_recommendation_pipeline",
                                return_value={"orchestration_status": "HEALTHY", "orchestration_import_error": None,
                                              "query_error": None, "real_odds_snapshot_rows": 0,
                                              "real_moneyline_recommendations_recorded": 0,
                                              "real_market_paper_bets_placed": 0}), \
             mock.patch.object(odr, "_launchctl_loaded_count", return_value=len(odr.sh._SCHEDULER_LABELS)):
            report = odr.build_readiness_report()
        self.assertEqual(report["verdict"], odr.NOT_READY)

    def test_stale_pipeline_component_is_a_warning_not_a_hard_failure(self):
        with mock.patch.object(odr, "check_databases",
                                return_value={"nhl.db": {"reachable": True, "writable": True, "error": None}}), \
             mock.patch.object(odr, "check_models", return_value={"registry_loads": True, "entry_count": 1,
                                                                    "active_versions": {}, "error": None}), \
             mock.patch.object(odr, "check_predictions",
                                return_value={"duplicate_idempotency_keys": 0, "invalid_result_states": 0,
                                              "stale_pending_over_24h": 0, "error": None}), \
             mock.patch.object(odr, "check_odds", return_value={"collection_status": {"status": "OK"}, "scheduler": {}}), \
             mock.patch.object(odr, "check_pipeline",
                                return_value={"components": {"settlement": {"status": "STALE", "last_status": "SUCCESS",
                                                                            "age_hours": 40.0}},
                                              "scheduler_loaded": {}}), \
             mock.patch.object(odr, "check_nhl", return_value={}), \
             mock.patch.object(odr, "check_context", return_value={}), \
             mock.patch.object(odr, "check_yahoo", return_value={"status": "CONNECTED"}), \
             mock.patch.object(odr, "check_real_recommendation_pipeline",
                                return_value={"orchestration_status": "HEALTHY", "orchestration_import_error": None,
                                              "query_error": None, "real_odds_snapshot_rows": 0,
                                              "real_moneyline_recommendations_recorded": 0,
                                              "real_market_paper_bets_placed": 0}), \
             mock.patch.object(odr, "_launchctl_loaded_count", return_value=len(odr.sh._SCHEDULER_LABELS)):
            report = odr.build_readiness_report()
        self.assertEqual(report["verdict"], odr.READY_WITH_WARNINGS)
        self.assertTrue(any("settlement" in w for w in report["warnings"]))

    def test_yahoo_not_connected_never_blocks_readiness(self):
        with mock.patch.object(odr, "check_databases",
                                return_value={"nhl.db": {"reachable": True, "writable": True, "error": None}}), \
             mock.patch.object(odr, "check_models", return_value={"registry_loads": True, "entry_count": 1,
                                                                    "active_versions": {}, "error": None}), \
             mock.patch.object(odr, "check_predictions",
                                return_value={"duplicate_idempotency_keys": 0, "invalid_result_states": 0,
                                              "stale_pending_over_24h": 0, "error": None}), \
             mock.patch.object(odr, "check_odds", return_value={"collection_status": {"status": "OK"}, "scheduler": {}}), \
             mock.patch.object(odr, "check_pipeline", return_value={"components": {}, "scheduler_loaded": {}}), \
             mock.patch.object(odr, "check_nhl", return_value={}), \
             mock.patch.object(odr, "check_context", return_value={}), \
             mock.patch.object(odr, "check_yahoo", return_value={"status": "OWNER_AUTH_REQUIRED"}), \
             mock.patch.object(odr, "check_real_recommendation_pipeline",
                                return_value={"orchestration_status": "HEALTHY", "orchestration_import_error": None,
                                              "query_error": None, "real_odds_snapshot_rows": 0,
                                              "real_moneyline_recommendations_recorded": 0,
                                              "real_market_paper_bets_placed": 0}), \
             mock.patch.object(odr, "_launchctl_loaded_count", return_value=len(odr.sh._SCHEDULER_LABELS)):
            report = odr.build_readiness_report()
        self.assertEqual(report["verdict"], odr.READY_WITH_WARNINGS)  # never NOT_READY

    def test_zero_scheduler_jobs_loaded_forces_not_ready(self):
        with mock.patch.object(odr, "check_databases",
                                return_value={"nhl.db": {"reachable": True, "writable": True, "error": None}}), \
             mock.patch.object(odr, "check_models", return_value={"registry_loads": True, "entry_count": 1,
                                                                    "active_versions": {}, "error": None}), \
             mock.patch.object(odr, "check_predictions",
                                return_value={"duplicate_idempotency_keys": 0, "invalid_result_states": 0,
                                              "stale_pending_over_24h": 0, "error": None}), \
             mock.patch.object(odr, "check_odds", return_value={"collection_status": {"status": "OK"}, "scheduler": {}}), \
             mock.patch.object(odr, "check_pipeline", return_value={"components": {}, "scheduler_loaded": {}}), \
             mock.patch.object(odr, "check_nhl", return_value={}), \
             mock.patch.object(odr, "check_context", return_value={}), \
             mock.patch.object(odr, "check_yahoo", return_value={"status": "CONNECTED"}), \
             mock.patch.object(odr, "check_real_recommendation_pipeline",
                                return_value={"orchestration_status": "HEALTHY", "orchestration_import_error": None,
                                              "query_error": None, "real_odds_snapshot_rows": 0,
                                              "real_moneyline_recommendations_recorded": 0,
                                              "real_market_paper_bets_placed": 0}), \
             mock.patch.object(odr, "_launchctl_loaded_count", return_value=0):
            report = odr.build_readiness_report()
        self.assertEqual(report["verdict"], odr.NOT_READY)

    def test_all_clear_is_ready_not_ready_with_warnings(self):
        with mock.patch.object(odr, "check_databases",
                                return_value={"nhl.db": {"reachable": True, "writable": True, "error": None}}), \
             mock.patch.object(odr, "check_models", return_value={"registry_loads": True, "entry_count": 1,
                                                                    "active_versions": {}, "error": None}), \
             mock.patch.object(odr, "check_predictions",
                                return_value={"duplicate_idempotency_keys": 0, "invalid_result_states": 0,
                                              "stale_pending_over_24h": 0, "error": None}), \
             mock.patch.object(odr, "check_odds", return_value={"collection_status": {"status": "OK"}, "scheduler": {}}), \
             mock.patch.object(odr, "check_pipeline", return_value={"components": {}, "scheduler_loaded": {}}), \
             mock.patch.object(odr, "check_nhl", return_value={}), \
             mock.patch.object(odr, "check_context", return_value={}), \
             mock.patch.object(odr, "check_yahoo", return_value={"status": "CONNECTED"}), \
             mock.patch.object(odr, "check_real_recommendation_pipeline",
                                return_value={"orchestration_status": "HEALTHY", "orchestration_import_error": None,
                                              "query_error": None, "real_odds_snapshot_rows": 0,
                                              "real_moneyline_recommendations_recorded": 0,
                                              "real_market_paper_bets_placed": 0}), \
             mock.patch.object(odr, "_launchctl_loaded_count", return_value=len(odr.sh._SCHEDULER_LABELS)):
            report = odr.build_readiness_report()
        self.assertEqual(report["verdict"], odr.READY)
        self.assertEqual(report["hard_failures"], [])
        self.assertEqual(report["warnings"], [])


if __name__ == "__main__":
    unittest.main()
