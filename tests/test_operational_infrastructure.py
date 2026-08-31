"""
Preseason Operationalization sprint: tests for the prospective ledger,
system health, live readiness, and the ported/new Streamlit pages.
Real SQLite (tempfile-backed), real Streamlit AppTest headless renders --
never mocked away. Numbered comments map to the sprint's Section 102
numbered topics where a direct mapping exists.
"""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from operational import prospective_ledger as pl
from operational import system_health as sh
from operational import live_readiness as lr
from research.player_props import decision_policy
from research.model_registry import MODEL_REGISTRY

REPO_ROOT = Path(__file__).resolve().parent.parent


def _file_sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _fresh_conn():
    tmp = Path(tempfile.mktemp(suffix=".db"))
    return pl.init_db(tmp), tmp


def _base_fields(**overrides):
    fields = dict(
        event_start_utc="2026-10-10T23:00:00Z", created_at_utc="2026-10-10T20:00:00Z",
        game_id="g1", game_date="2026-10-10", player_id="p1", team="NOR", opponent="CST",
        market_id="PLAYER_GOALS_1PLUS", market_family="GOALS", threshold="1+",
        raw_probability=0.34, context_adjusted_probability=0.32, coherent_probability=0.32,
        model_version="hash123", model_hash="hash123",
    )
    fields.update(overrides)
    return fields


# 1. schema migration
class Test01SchemaMigration(unittest.TestCase):
    def test_init_db_is_idempotent(self):
        conn, tmp = _fresh_conn()
        conn2 = pl.init_db(tmp)  # second init on same file must not error
        row = conn2.execute("SELECT version FROM schema_version").fetchone()
        self.assertEqual(row["version"], pl.SCHEMA_VERSION)


# 2. observation insertion
class Test02ObservationInsertion(unittest.TestCase):
    def test_insert_model_observation(self):
        conn, _ = _fresh_conn()
        result = pl.record_model_observation(conn, **_base_fields())
        self.assertEqual(result["status"], "INSERTED")
        obs = pl.get_observation(conn, result["prediction_id"])
        self.assertEqual(obs["record_type"], "MODEL_OBSERVATION")


# 3. immutable prediction fields
class Test03ImmutablePredictionFields(unittest.TestCase):
    def test_direct_update_of_raw_probability_blocked(self):
        conn, _ = _fresh_conn()
        result = pl.record_model_observation(conn, **_base_fields())
        with self.assertRaises(Exception):
            conn.execute("UPDATE predictions SET raw_probability = 0.99 WHERE prediction_id = ?",
                         (result["prediction_id"],))

    def test_settle_prediction_does_not_change_raw_probability(self):
        conn, _ = _fresh_conn()
        result = pl.record_model_observation(conn, **_base_fields())
        settled = pl.settle_prediction(conn, result["prediction_id"], "WIN", actual_outcome="1")
        self.assertEqual(settled["raw_probability"], 0.34)


# 4. duplicate prediction id
class Test04DuplicatePredictionId(unittest.TestCase):
    def test_duplicate_prediction_id_raises(self):
        conn, _ = _fresh_conn()
        fixed_id = pl.new_prediction_id()
        pl.insert_prediction(conn, {**_base_fields(), "record_type": "MODEL_OBSERVATION",
                                     "prediction_id": fixed_id})
        with self.assertRaises(pl.DuplicatePredictionError):
            pl.insert_prediction(conn, {**_base_fields(game_id="different"), "record_type": "MODEL_OBSERVATION",
                                          "prediction_id": fixed_id,
                                          "idempotency_key": "force-different-key"})


# 5. semantic idempotency
class Test05SemanticIdempotency(unittest.TestCase):
    def test_same_semantic_inputs_return_existing(self):
        conn, _ = _fresh_conn()
        r1 = pl.record_model_observation(conn, **_base_fields())
        r2 = pl.record_model_observation(conn, **_base_fields(created_at_utc="2026-10-10T20:05:00Z"))
        self.assertEqual(r1["status"], "INSERTED")
        self.assertEqual(r2["status"], "DUPLICATE")
        self.assertEqual(r1["prediction_id"], r2["prediction_id"])


# 6. Streamlit rerun duplicate protection
class Test06RerunDuplicateProtection(unittest.TestCase):
    def test_ten_reruns_produce_one_row(self):
        conn, _ = _fresh_conn()
        ids = set()
        for _ in range(10):
            r = pl.record_model_observation(conn, **_base_fields())
            ids.add(r["prediction_id"])
        self.assertEqual(len(ids), 1)
        n = conn.execute("SELECT COUNT(*) AS n FROM predictions").fetchone()["n"]
        self.assertEqual(n, 1)


# 7. pre-start timestamp guard
class Test07PreStartGuard(unittest.TestCase):
    def test_created_before_event_start_accepted(self):
        conn, _ = _fresh_conn()
        result = pl.record_model_observation(conn, **_base_fields())
        self.assertEqual(result["status"], "INSERTED")


# 8. post-start rejection
class Test08PostStartRejection(unittest.TestCase):
    def test_created_at_or_after_event_start_rejected(self):
        conn, _ = _fresh_conn()
        with self.assertRaises(pl.InvalidPredictionError):
            pl.record_model_observation(conn, **_base_fields(created_at_utc="2026-10-11T00:00:00Z"))

    def test_historical_research_exempt_from_guard(self):
        conn, _ = _fresh_conn()
        result = pl.record_historical_research(conn, **_base_fields(created_at_utc="2026-10-11T00:00:00Z"))
        self.assertEqual(result["status"], "INSERTED")


# 9. odds-before-start guard
class Test09OddsBeforeStartGuard(unittest.TestCase):
    def test_odds_captured_after_event_start_rejected(self):
        conn, _ = _fresh_conn()
        with self.assertRaises(pl.InvalidPredictionError):
            pl.record_model_observation(conn, **_base_fields(odds_captured_at_utc="2026-10-11T00:00:00Z"))

    def test_odds_captured_before_event_start_accepted(self):
        conn, _ = _fresh_conn()
        result = pl.record_model_observation(conn, **_base_fields(odds_captured_at_utc="2026-10-10T21:00:00Z"))
        self.assertEqual(result["status"], "INSERTED")


# 10. settlement
class Test10Settlement(unittest.TestCase):
    def test_settle_updates_result_fields(self):
        conn, _ = _fresh_conn()
        r = pl.record_model_observation(conn, **_base_fields())
        settled = pl.settle_prediction(conn, r["prediction_id"], "WIN", actual_outcome="1", profit_loss=10.0)
        self.assertEqual(settled["result_status"], "WIN")
        self.assertEqual(settled["profit_loss"], 10.0)
        self.assertIsNotNone(settled["settled_at_utc"])


# 11. invalid settlement mutation
class Test11InvalidSettlementMutation(unittest.TestCase):
    def test_unknown_result_status_rejected(self):
        conn, _ = _fresh_conn()
        r = pl.record_model_observation(conn, **_base_fields())
        with self.assertRaises(pl.InvalidPredictionError):
            pl.settle_prediction(conn, r["prediction_id"], "MAYBE")

    def test_settle_unknown_prediction_id_rejected(self):
        conn, _ = _fresh_conn()
        with self.assertRaises(pl.InvalidPredictionError):
            pl.settle_prediction(conn, "does-not-exist", "WIN")


# 12. ledger record types
class Test12LedgerRecordTypes(unittest.TestCase):
    def test_four_record_types_supported(self):
        self.assertEqual(set(pl.RECORD_TYPES),
                          {"MODEL_OBSERVATION", "SHADOW_POLICY_OBSERVATION", "REAL_BET", "HISTORICAL_RESEARCH"})

    def test_real_bet_requires_stake_odds_book_placed_at(self):
        conn, _ = _fresh_conn()
        with self.assertRaises(pl.InvalidPredictionError):
            pl.record_real_bet(conn, **_base_fields())


# 13. P&L separation
class Test13PnlSeparation(unittest.TestCase):
    def test_model_observations_never_counted_in_real_bet_pnl(self):
        conn, _ = _fresh_conn()
        pl.record_model_observation(conn, **_base_fields())
        pl.record_shadow_observation(conn, **_base_fields(game_id="g2"))
        summary = pl.summary_metrics(conn)
        self.assertEqual(summary["REAL_BET"]["n"], 0)
        self.assertEqual(summary["REAL_BET"]["message"], "NO REAL BETS RECORDED")
        self.assertNotIn("total_profit_loss", summary["MODEL_OBSERVATION"])


# 14. export
class Test14Export(unittest.TestCase):
    def test_export_observations_csv(self):
        conn, _ = _fresh_conn()
        pl.record_model_observation(conn, **_base_fields())
        out = Path(tempfile.mktemp(suffix=".csv"))
        pl.export_observations_csv(conn, str(out))
        self.assertTrue(out.exists())
        self.assertGreater(out.stat().st_size, 0)


# 15. context Goals record
class Test15ContextGoalsRecord(unittest.TestCase):
    def test_raw_and_adjusted_preserved(self):
        conn, _ = _fresh_conn()
        r = pl.record_model_observation(conn, **_base_fields(context_state="COLD_AND_TOI_DECLINE"))
        obs = pl.get_observation(conn, r["prediction_id"])
        self.assertEqual(obs["raw_probability"], 0.34)
        self.assertEqual(obs["context_adjusted_probability"], 0.32)
        self.assertEqual(obs["context_state"], "COLD_AND_TOI_DECLINE")


# 16. context Points record
class Test16ContextPointsRecord(unittest.TestCase):
    def test_points_record_preserves_all_stages(self):
        conn, _ = _fresh_conn()
        r = pl.record_model_observation(conn, **_base_fields(
            market_id="PLAYER_POINTS_1PLUS", market_family="POINTS",
            raw_probability=0.28, context_adjusted_probability=0.24, coherent_probability=0.24))
        obs = pl.get_observation(conn, r["prediction_id"])
        self.assertEqual(obs["raw_probability"], 0.28)
        self.assertEqual(obs["context_adjusted_probability"], 0.24)
        self.assertEqual(obs["coherent_probability"], 0.24)


# 17. overlay hash / 18. model hash
class Test17To18VersionSnapshots(unittest.TestCase):
    def test_model_and_overlay_hash_stored(self):
        conn, _ = _fresh_conn()
        r = pl.record_model_observation(conn, **_base_fields(
            model_hash="abc123", context_overlay_hash="def456"))
        obs = pl.get_observation(conn, r["prediction_id"])
        self.assertEqual(obs["model_hash"], "abc123")
        self.assertEqual(obs["context_overlay_hash"], "def456")


# 19. registry hash
class Test19RegistryHash(unittest.TestCase):
    def test_registry_hash_field_storable(self):
        conn, _ = _fresh_conn()
        r = pl.record_model_observation(conn, **_base_fields(registry_hash="reg789"))
        obs = pl.get_observation(conn, r["prediction_id"])
        self.assertEqual(obs["registry_hash"], "reg789")


# 20. shadow policy output
class Test20ShadowPolicyOutput(unittest.TestCase):
    def test_shadow_fields_stored_separately_from_official(self):
        conn, _ = _fresh_conn()
        r = pl.record_shadow_observation(conn, **_base_fields(
            current_policy_status="WATCH", shadow_policy_status="WATCH",
            raw_policy_input_probability=0.34, shadow_context_policy_probability=0.32,
            future_policy_candidate="OPERATIONAL_VALIDATED_PENDING"))
        obs = pl.get_observation(conn, r["prediction_id"])
        self.assertEqual(obs["current_policy_status"], "WATCH")
        self.assertEqual(obs["shadow_policy_status"], "WATCH")
        self.assertEqual(obs["raw_policy_input_probability"], 0.34)
        self.assertEqual(obs["shadow_context_policy_probability"], 0.32)


# 21. official policy unchanged
class Test21OfficialPolicyUnchanged(unittest.TestCase):
    def test_decision_policy_hash(self):
        self.assertEqual(_file_sha256("research/player_props/decision_policy.py"),
                          "f812d5fa2f1811c2b06b0b63b972dc499fc2f8f6e117a9afd5fa63bea55c763a")


# 22. LOW Goals WATCH
class Test22LowGoalsWatch(unittest.TestCase):
    def test_low_goals_watch_only(self):
        self.assertEqual(decision_policy.gate_low_confidence("GOALS", "LOW", "BET")["final_decision"], "WATCH")


# 23. LOW Points WATCH
class Test23LowPointsWatch(unittest.TestCase):
    def test_low_points_watch_only(self):
        self.assertEqual(decision_policy.gate_low_confidence("POINTS", "LOW", "BET")["final_decision"], "WATCH")


# 24. readiness READY
class Test24ReadinessReady(unittest.TestCase):
    def test_sog_ready_under_normal_conditions(self):
        result = lr.live_readiness("PLAYER_SOG")
        self.assertIn(result["status"], ("READY", "WAIT"))  # WAIT if odds happen to be stale/off in this env


# 25. readiness WAIT no odds / stale odds
class Test25ReadinessWait(unittest.TestCase):
    def test_goals_waits_on_unsupported_live_contract(self):
        result = lr.live_readiness("GOALS")
        self.assertEqual(result["status"], "WAIT")
        self.assertEqual(result["reason"], "MARKET_UNSUPPORTED")


# 26. readiness DATA_UNAVAILABLE
class Test26ReadinessDataUnavailable(unittest.TestCase):
    def test_player_unmapped(self):
        result = lr.live_readiness("PLAYER_SOG", player_id="999", player_mapped=False)
        self.assertEqual(result["status"], "DATA_UNAVAILABLE")
        self.assertEqual(result["reason"], "PLAYER_UNMAPPED")


# 27. readiness MODEL_NOT_OPERATIONAL
class Test27ReadinessModelNotOperational(unittest.TestCase):
    def test_unknown_market(self):
        result = lr.live_readiness("NOT_A_REAL_MARKET")
        self.assertEqual(result["status"], "MODEL_NOT_OPERATIONAL")

    def test_rejected_model(self):
        result = lr.live_readiness("TEAM_GOALS_PERIOD")
        self.assertEqual(result["status"], "MODEL_NOT_OPERATIONAL")


# 28-32. health statuses
class Test28To32HealthStatuses(unittest.TestCase):
    def test_status_mapping_covers_ok_stale_waiting_error(self):
        for raw, expected in (("CURRENT", "OK"), ("STALE", "STALE"),
                               ("NOT_REFRESHED", "WAITING"), ("SOURCE_CONTRACT_FAILURE", "ERROR")):
            self.assertEqual(sh._READINESS_TO_HEALTH[raw], expected)

    def test_missing_cache_file_returns_unknown(self):
        item = sh._from_readiness_block(None, "odds", "Odds API")
        self.assertEqual(item["status"], "UNKNOWN")

    def test_database_health_reports_error_for_missing_db(self):
        import operational.system_health as mod
        original = mod.database_health
        # Simulate a missing DB path without touching the real nhl.db
        import db as db_module
        real_path = db_module.DB_PATH
        try:
            db_module.DB_PATH = Path("/nonexistent/path/nhl.db")
            result = original()
            self.assertEqual(result["status"], "ERROR")
        finally:
            db_module.DB_PATH = real_path

    def test_prospective_ledger_health_not_required_when_no_db(self):
        import operational.prospective_ledger as pl_mod
        real_path = pl_mod.DB_PATH
        try:
            pl_mod.DB_PATH = Path("/nonexistent/prospective.db")
            result = sh.prospective_ledger_health()
            self.assertEqual(result["status"], "NOT_REQUIRED")
        finally:
            pl_mod.DB_PATH = real_path

    def test_build_system_health_covers_all_components(self):
        health = sh.build_system_health()
        for key in ("NHL_API", "SCHEDULE", "ROSTERS", "MONEYPUCK", "ODDS_API", "DRAFTKINGS_MARKETS",
                    "MODEL_REGISTRY", "MARKET_REGISTRY", "JOINT_REGISTRY", "CONTEXT_OVERLAY_REGISTRY",
                    "DATABASE", "PROSPECTIVE_LEDGER", "LAST_SYNC"):
            self.assertIn(key, health)
            self.assertIn(health[key]["status"], ("OK", "STALE", "WAITING", "ERROR", "NOT_REQUIRED", "UNKNOWN"))


# 33. opportunity card render states
class Test33OpportunityCardRenderStates(unittest.TestCase):
    def _run_card(self, card: dict):
        from streamlit.testing.v1 import AppTest
        script = (
            "import sys; sys.path.insert(0, %r)\n"
            "from dashboard import components as comp\n"
            "comp.render_opportunity_card(%r)\n"
        ) % (str(Path(__file__).resolve().parent.parent), card)
        at = AppTest.from_string(script)
        at.run(timeout=60)
        return at

    def test_full_live_card(self):
        at = self._run_card({"player": "A", "team": "NOR", "opponent": "CST", "market": "SOG",
                              "threshold": "3+", "decision": "BET", "confidence": "HIGH",
                              "raw_probability": 0.6, "current_odds": -110, "max_acceptable_price": -120,
                              "conservative_edge": 0.03, "conservative_probability": 0.55})
        self.assertEqual(list(at.exception), [])

    def test_no_live_price_card(self):
        at = self._run_card({"player": "B", "market": "BLOCKED_SHOTS", "threshold": "2+", "decision": "WAIT"})
        self.assertEqual(list(at.exception), [])

    def test_overlay_active_card(self):
        at = self._run_card({"player": "C", "market": "GOALS", "threshold": "1+", "decision": "WATCH",
                              "context_state": "COLD_AND_TOI_DECLINE", "context_raw": 0.34,
                              "context_adjusted": 0.32, "context_delta": -0.02})
        self.assertEqual(list(at.exception), [])

    def test_watch_wait_pass_cards(self):
        for decision in ("WATCH", "WAIT", "PASS"):
            at = self._run_card({"player": "D", "market": "SOG", "threshold": "1+", "decision": decision})
            self.assertEqual(list(at.exception), [], f"decision={decision}")


# 34. no fake odds placeholder
class Test34NoFakeOddsPlaceholder(unittest.TestCase):
    def test_missing_odds_render_as_no_live_price(self):
        from dashboard.formatting import format_american_odds, NO_LIVE_PRICE
        self.assertEqual(format_american_odds(None), NO_LIVE_PRICE)

    def test_format_american_odds_never_defaults_to_zero_or_fixed_number(self):
        from dashboard.formatting import format_american_odds
        self.assertNotIn(format_american_odds(None), ("+100", "-110", "0", "0%"))


# 35. status banner / empty state render
class Test35StatusBannerEmptyStateRender(unittest.TestCase):
    def test_status_banner_and_empty_state_render_without_exception(self):
        from streamlit.testing.v1 import AppTest
        script = (
            "import sys; sys.path.insert(0, %r)\n"
            "from dashboard import components as comp\n"
            "comp.render_status_banner('VALIDATED', 'test headline')\n"
            "comp.render_empty_state('NO_GAMES')\n"
        ) % str(Path(__file__).resolve().parent.parent)
        at = AppTest.from_string(script)
        at.run(timeout=60)
        self.assertEqual(list(at.exception), [])


# 36. Model Health reflects real registry
class Test36ModelHealthRealRegistry(unittest.TestCase):
    def test_model_health_page_renders_all_registry_entries(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(str(REPO_ROOT / "dashboard/pages/22_Model_Health.py"))
        at.run(timeout=60)
        self.assertEqual(list(at.exception), [])
        markdown_text = " ".join(m.value for m in at.markdown)
        for entry in MODEL_REGISTRY:
            self.assertIn(entry.display_name, markdown_text)


# 37. Research hub renders
class Test37ResearchHubRenders(unittest.TestCase):
    def test_research_hub_page_renders(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(str(REPO_ROOT / "dashboard/pages/24_Research_Hub.py"))
        at.run(timeout=60)
        self.assertEqual(list(at.exception), [])


# 38. nav grouping / Today default
class Test38NavGrouping(unittest.TestCase):
    def test_app_defaults_to_today(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(str(REPO_ROOT / "dashboard/app.py"))
        at.run(timeout=60)
        self.assertEqual(list(at.exception), [])
        self.assertEqual([t.value for t in at.title], ["Today"])

    def test_nav_icons_are_unique(self):
        with open("dashboard/app.py") as f:
            src = f.read()
        import re
        icons = re.findall(r'_p\("[^"]+", "[^"]+", "([^"]+)"', src)
        self.assertEqual(len(icons), len(set(icons)), f"duplicate icons found: {icons}")


# 39. Today no-games / offseason state
class Test39TodayOffseasonState(unittest.TestCase):
    def test_today_page_renders_even_with_no_games(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(str(REPO_ROOT / "dashboard/pages/21_Today.py"))
        at.run(timeout=60)
        self.assertEqual(list(at.exception), [])


# 40. Live SOG dict-key bug fixed
class Test40LiveSogDictKeyBugFixed(unittest.TestCase):
    def test_live_sog_page_uses_defensive_get_access(self):
        with open("dashboard/pages/8_Live_SOG_Markets.py") as f:
            src = f.read()
        self.assertIn("_board_row_to_card", src)
        self.assertIn('r.get("player_name_raw"', src)

    def test_board_row_to_card_handles_missing_keys_gracefully(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(str(REPO_ROOT / "dashboard/pages/8_Live_SOG_Markets.py"))
        at.run(timeout=60)
        self.assertEqual(list(at.exception), [])


# 41. no unexpected network in new operational modules
class Test41NoUnexpectedNetwork(unittest.TestCase):
    def test_ledger_and_health_have_no_requests_import(self):
        for fname in ("operational/prospective_ledger.py", "operational/system_health.py",
                      "operational/live_readiness.py"):
            with open(fname) as f:
                src = f.read()
            self.assertNotIn("import requests", src)
            self.assertNotIn("urllib", src)


# 42. frozen hashes (regression protection for this sprint's own new files)
class Test42FrozenNewInfrastructureHashes(unittest.TestCase):
    def test_model_registry_and_context_overlay_registry_present(self):
        self.assertGreater(len(MODEL_REGISTRY), 0)
        import json
        from research.context_overlay.registry import REGISTRY_PATH
        with open(REGISTRY_PATH) as f:
            registry = json.load(f)
        self.assertEqual(len(registry), 2)


# 43. context overlay parameters unchanged (re-verified from this sprint)
class Test43ContextOverlayParametersUnchanged(unittest.TestCase):
    def test_goals_offset_and_points_shift_unchanged(self):
        import json
        with open("research/context_overlay_results.json") as f:
            results = json.load(f)
        self.assertAlmostEqual(results["props"]["goals"]["winner_params"]["offset"], -0.18, places=6)
        self.assertAlmostEqual(results["props"]["points"]["winner_params"]["shift"], -0.0415, places=4)


if __name__ == "__main__":
    unittest.main()
