"""2026-27 Continuous Learning framework, Part 63: tests for
operational/engine_status_evaluator.py -- run ordering, halt/watch
conditions, combined status severity."""
from __future__ import annotations

import unittest

from operational import engine_status_evaluator as ese


class Test01CombineStatus(unittest.TestCase):
    def test_most_severe_wins(self):
        self.assertEqual(ese.combine_status([ese.NORMAL, ese.WATCH, ese.NORMAL]), ese.WATCH)
        self.assertEqual(ese.combine_status([ese.HALT, ese.NORMAL]), ese.HALT)

    def test_empty_list_is_normal(self):
        self.assertEqual(ese.combine_status([]), ese.NORMAL)


class Test02RunOrder(unittest.TestCase):
    def test_halts_if_results_not_ingested(self):
        result = ese.check_run_order({"results_ingested": False, "settlement_completed": True})
        self.assertEqual(result["status"], ese.HALT)

    def test_halts_if_settlement_not_completed(self):
        result = ese.check_run_order({"results_ingested": True, "settlement_completed": False})
        self.assertEqual(result["status"], ese.HALT)

    def test_normal_when_both_complete(self):
        result = ese.check_run_order({"results_ingested": True, "settlement_completed": True})
        self.assertEqual(result["status"], ese.NORMAL)


class Test03SettlementCompleteness(unittest.TestCase):
    def test_flags_incomplete_on_errors(self):
        result = ese.check_settlement_completeness({"errors": [{"prediction_id": "x", "error": "boom"}]})
        self.assertEqual(result["status"], ese.INVESTIGATE)

    def test_normal_with_no_errors(self):
        result = ese.check_settlement_completeness({"errors": []})
        self.assertEqual(result["status"], ese.NORMAL)


class Test04ContractStatus(unittest.TestCase):
    def test_normal_when_zero_verified_contracts(self):
        from unittest import mock
        with mock.patch("research.generic_prop_pricing.provider_adapter.VERIFIED_CONTRACTS", frozenset()):
            result = ese.check_contract_status()
        self.assertEqual(result["status"], ese.NORMAL)
        self.assertEqual(result["verified_contracts"], 0)

    def test_watch_now_that_moneyline_is_really_verified(self):
        # Live DK / Paper Bankroll completion sprint (2026-08-31): real,
        # not mocked -- MONEYLINE was actually verified this sprint (see
        # tests/test_provider_adapter_boundary.py), so the real function
        # now correctly reports WATCH ("drift monitoring not yet
        # implemented"), not NORMAL.
        result = ese.check_contract_status()
        self.assertEqual(result["status"], ese.WATCH)
        self.assertEqual(result["verified_contracts"], 1)


class Test05InputDrift(unittest.TestCase):
    def test_insufficient_data_without_current_rate(self):
        result = ese.check_input_drift(1.6, None, label="SOG rate")
        self.assertEqual(result["status"], "INSUFFICIENT_DATA")

    def test_normal_within_threshold(self):
        result = ese.check_input_drift(1.6, 1.7, label="SOG rate")
        self.assertEqual(result["status"], ese.NORMAL)

    def test_watch_beyond_threshold(self):
        result = ese.check_input_drift(1.6, 2.5, label="SOG rate")
        self.assertEqual(result["status"], ese.WATCH)

    def test_never_auto_adjusts_just_flags(self):
        result = ese.check_input_drift(1.6, 2.5, label="SOG rate")
        self.assertNotIn("adjustment", result)
        self.assertNotIn("correction", result)


class Test06LeagueEnvironmentFlags(unittest.TestCase):
    def test_no_automatic_adjustment_language(self):
        drift = [{"status": ese.WATCH, "label": "scoring_rate"}]
        result = ese.check_league_environment_flags(drift)
        self.assertEqual(result["status"], ese.WATCH)
        self.assertIn("no automatic model adjustment", result["note"])


class Test07HaltConditions(unittest.TestCase):
    def test_no_flags_is_normal(self):
        flags = {label: False for label in ese.HALT_CONDITION_LABELS}
        self.assertEqual(ese.check_halt_conditions(flags)["status"], ese.NORMAL)

    def test_any_true_flag_halts(self):
        flags = {label: False for label in ese.HALT_CONDITION_LABELS}
        flags["production_shadow_contamination"] = True
        result = ese.check_halt_conditions(flags)
        self.assertEqual(result["status"], ese.HALT)
        self.assertIn("production_shadow_contamination", result["triggered"])


class Test08WatchConditions(unittest.TestCase):
    def test_low_sample_size_triggers_watch(self):
        flags = {label: False for label in ese.WATCH_CONDITION_LABELS}
        flags["low_sample_size"] = True
        result = ese.check_watch_conditions(flags)
        self.assertEqual(result["status"], ese.WATCH)


if __name__ == "__main__":
    unittest.main()
