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

    def test_saves_starter_data_and_actionability_report_separately_from_market_contract(self):
        """Starting-Goalie Certainty + Prop Contract Watch block
        (2026-09-24), Part 11: these are three DIFFERENT questions and
        must never collapse into one status."""
        result = odr.check_real_prop_pipeline()
        self.assertEqual(result["saves_starter_data_status"], "PARTIAL")
        self.assertEqual(result["saves_actionability_status"], "WAIT_ONLY")

    def test_orchestration_import_failure_marks_saves_starter_fields_not_ready_too(self):
        with mock.patch("builtins.__import__", side_effect=ImportError("boom")):
            result = odr.check_real_prop_pipeline()
        self.assertEqual(result["saves_starter_data_status"], "NOT_READY")
        self.assertEqual(result["saves_actionability_status"], "NOT_READY")

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


class TestLaunchctlLoadedCountIsCrossPlatform(unittest.TestCase):
    """VPS Production Deployment block (2026-09-24), Part 14: a second,
    independent launchctl-only gap (distinct from the one already found
    and fixed in operational/system_health.py) -- this one feeds
    build_readiness_report()'s own hard-failure/warning logic directly."""

    def test_counts_via_systemctl_on_linux(self):
        class _FakeResult:
            stdout = "\n".join(odr.sh._SYSTEMD_TIMER_UNITS)

        with mock.patch("platform.system", return_value="Linux"), \
             mock.patch("opening_day_readiness.subprocess.run", return_value=_FakeResult()):
            self.assertEqual(odr._launchctl_loaded_count(), len(odr.sh._SYSTEMD_TIMER_UNITS))

    def test_counts_via_launchctl_on_macos(self):
        class _FakeResult:
            stdout = "\n".join(odr.sh._SCHEDULER_LABELS)

        with mock.patch("platform.system", return_value="Darwin"), \
             mock.patch("opening_day_readiness.subprocess.run", return_value=_FakeResult()):
            self.assertEqual(odr._launchctl_loaded_count(), len(odr.sh._SCHEDULER_LABELS))

    def test_returns_none_not_a_crash_when_the_tool_is_missing(self):
        with mock.patch("platform.system", return_value="Linux"), \
             mock.patch("opening_day_readiness.subprocess.run", side_effect=FileNotFoundError("no systemctl")):
            self.assertIsNone(odr._launchctl_loaded_count())


_REAL_BUILD_COMPONENT_STATES = odr.build_component_states


def setUpModule():
    """The per-component table reads this machine's real state (launchd, caches, network-free but live);
    the verdict-logic tests below inject their own checks, so it is stubbed out for them and exercised
    directly (with injected checks) in TestComponentStates."""
    global _patcher
    _patcher = mock.patch.object(odr, "build_component_states", return_value={})
    _patcher.start()


def tearDownModule():
    _patcher.stop()


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


class TestComponentStates(unittest.TestCase):
    """The per-component states requested by the Production Activation block: independent, precise,
    never a bare READY / NOT_READY."""

    REQUIRED = ("NHL DATA", "MONEYLINE MARKET CONTRACT", "MONEYLINE RECOMMENDATION PIPELINE", "SOG MARKET CONTRACT",
                "SOG ACTIONABILITY", "SAVES MARKET CONTRACT", "SAVES STARTER DATA", "SAVES ACTIONABILITY",
                "GAME EDGE PARLAY", "PAPER BETTING", "SETTLEMENT", "CLV", "POSTMORTEM", "CLOUD SNAPSHOT",
                "CLOUD PUBLICATION", "CLOUD FRESHNESS", "ODDS API QUOTA", "SCHEDULERS", "BACKUPS", "AUTH", "YAHOO")

    def checks(self, **over):
        c = {"nhl": {"last_sync_status": "SUCCESS", "last_sync_success_age_hours": 2.0, "most_recent_game_date_on_file": "2026-10-09"},
             "odds": {"collection_status": {"status": "OK", "credits_remaining": 368, "tracked_events": 33}},
             "real_recommendation_pipeline": {"orchestration_status": "HEALTHY", "real_moneyline_recommendations_recorded": 0,
                                              "real_market_paper_bets_placed": 0},
             "real_prop_pipeline": {"sog_status": "PENDING_LIVE_CONTRACT", "saves_status": "PENDING_LIVE_CONTRACT",
                                    "saves_starter_data_status": "PARTIAL", "saves_actionability_status": "WAIT_ONLY",
                                    "game_edge_parlay_status": "PARTIAL", "real_sog_recommendations_recorded": 0,
                                    "real_saves_recommendations_recorded": 0},
             "predictions": {}, "yahoo": {"status": "OWNER_AUTH_REQUIRED"}}
        c.update(over)
        return c

    def build(self, checks=None, eligible=1.2, loaded=10):
        from operational import ingestion_health, publish_cloud_snapshot as pcs
        healthy = {k: {"last_status": "SUCCESS", "last_success_utc": "2999-01-01T00:00:00+00:00"} for k in
                   ("settlement", "postmortem", "database_backups")}
        fake_ofa = mock.Mock(upcoming_starts=mock.Mock(return_value=[1]), pulls_for=mock.Mock(return_value=[]),
                             evaluate=mock.Mock(return_value={"decision_policy_quote_available_pct": eligible}))
        with mock.patch.object(ingestion_health, "load_health", return_value=healthy), \
             mock.patch.object(ingestion_health, "component_age_hours", return_value=1.0), \
             mock.patch.dict("sys.modules", {"operational.odds_freshness_analysis": fake_ofa}), \
             mock.patch.object(__import__("operational"), "odds_freshness_analysis", fake_ofa, create=True), \
             mock.patch.object(odr.sh, "cloud_snapshot_publish_health", return_value={"status": "OK", "message": "ok"}), \
             mock.patch.object(pcs, "publishing_enabled", return_value=True), \
             mock.patch.object(odr, "_launchctl_loaded_count", return_value=loaded), \
             mock.patch.object(odr.db, "get_conn", side_effect=RuntimeError("no db in test")):
            return _REAL_BUILD_COMPONENT_STATES(checks or self.checks())

    def test_every_named_component_is_reported_with_a_precise_state(self):
        out = self.build()
        for name in self.REQUIRED:
            self.assertIn(name, out)
            self.assertIn(out[name]["state"], odr.COMPONENT_STATES, name)

    def test_expected_day_one_states(self):
        out = self.build()
        self.assertEqual(out["SOG MARKET CONTRACT"]["state"], "WAITING_FOR_LIVE_MARKET")
        self.assertEqual(out["SAVES ACTIONABILITY"]["state"], "WAIT_ONLY")
        self.assertEqual(out["PAPER BETTING"]["state"], "NO_REAL_SAMPLE_YET")
        self.assertEqual(out["YAHOO"]["state"], "OWNER_AUTH_REQUIRED")
        self.assertEqual(out["AUTH"]["state"], "OWNER_ACTION_REQUIRED")
        self.assertEqual(out["SCHEDULERS"]["state"], "READY")

    def test_moneyline_pipeline_is_partial_when_the_polling_cadence_cannot_feed_it(self):
        self.assertEqual(self.build(eligible=1.2)["MONEYLINE RECOMMENDATION PIPELINE"]["state"], "PARTIAL")
        self.assertEqual(self.build(eligible=97.6)["MONEYLINE RECOMMENDATION PIPELINE"]["state"], "NO_REAL_SAMPLE_YET")

    def test_scheduler_and_quota_and_nhl_failures_are_surfaced(self):
        self.assertEqual(self.build(loaded=0)["SCHEDULERS"]["state"], "FAILED")
        self.assertEqual(self.build(loaded=9)["SCHEDULERS"]["state"], "PARTIAL")
        low = self.checks(odds={"collection_status": {"status": "OK", "credits_remaining": 15, "tracked_events": 1}})
        self.assertEqual(self.build(low)["ODDS API QUOTA"]["state"], "FAILED")
        stale = self.checks(nhl={"last_sync_status": "SUCCESS", "last_sync_success_age_hours": 40.0, "most_recent_game_date_on_file": "x"})
        self.assertEqual(self.build(stale)["NHL DATA"]["state"], "STALE")

    def test_a_broken_component_builder_never_breaks_the_verdict(self):
        with mock.patch.object(odr, "build_component_states", side_effect=RuntimeError("boom")):
            report = odr.build_readiness_report()
        self.assertEqual(report["components"], {})
        self.assertTrue(any("per-component states unavailable" in w for w in report["warnings"]))


if __name__ == "__main__":
    unittest.main()
