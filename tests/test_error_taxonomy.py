"""2026-27 Continuous Learning framework, Part 63: tests for
operational/error_taxonomy.py."""
from __future__ import annotations

import unittest

from operational import error_taxonomy as et


class Test01SmallResidualIsRandomVariance(unittest.TestCase):
    def test_small_residual_never_classified_as_a_real_issue(self):
        self.assertEqual(et.classify_miss({}, 0.1), et.RANDOM_VARIANCE)


class Test02RoleChange(unittest.TestCase):
    def test_transition_state_flags_role_change(self):
        row = {"pp_transition_state": "PROMOTED_PP2_TO_PP1"}
        self.assertEqual(et.classify_miss(row, 0.6), et.ROLE_CHANGE)

    def test_recent_transition_by_games_since_flags_role_change(self):
        row = {"pp_games_since_transition": 1}
        self.assertEqual(et.classify_miss(row, 0.6), et.ROLE_CHANGE)


class Test03StarterAndActiveStatusErrors(unittest.TestCase):
    def test_goalie_did_not_play_maps_to_starter_error(self):
        row = {"result_status": "UNRESOLVED", "notes": '{"resolver_status": "GOALIE_DID_NOT_PLAY"}'}
        self.assertEqual(et.classify_miss(row, 0.6), et.STARTER_ERROR)

    def test_player_did_not_dress_maps_to_active_status_error(self):
        row = {"result_status": "UNRESOLVED", "notes": '{"resolver_status": "PLAYER_DID_NOT_DRESS"}'}
        self.assertEqual(et.classify_miss(row, 0.6), et.ACTIVE_STATUS_ERROR)


class Test04MarketMappingError(unittest.TestCase):
    def test_unsupported_settlement_market_maps_correctly(self):
        row = {"result_status": "UNRESOLVED", "notes": '{"resolver_status": "UNSUPPORTED_SETTLEMENT_MARKET"}'}
        self.assertEqual(et.classify_miss(row, 0.6), et.MARKET_MAPPING_ERROR)

    def test_not_ingested_maps_correctly(self):
        row = {"result_status": "UNRESOLVED", "notes": '{"resolver_status": "TEAM_SOG_NOT_INGESTED"}'}
        self.assertEqual(et.classify_miss(row, 0.6), et.MARKET_MAPPING_ERROR)


class Test05DataError(unittest.TestCase):
    def test_thin_role_certainty_flags_data_error(self):
        row = {"pp_role_certainty": 0.1}
        self.assertEqual(et.classify_miss(row, 0.6), et.DATA_ERROR)


class Test06ModelCalibration(unittest.TestCase):
    def test_high_confidence_large_miss_flags_calibration(self):
        row = {"confidence": "HIGH"}
        self.assertEqual(et.classify_miss(row, 0.7), et.MODEL_CALIBRATION)


class Test07Unknown(unittest.TestCase):
    def test_no_explaining_field_is_unknown(self):
        row = {"confidence": "MEDIUM"}
        self.assertEqual(et.classify_miss(row, 0.6), et.UNKNOWN)


class Test08SummarizeMisses(unittest.TestCase):
    def test_counts_every_taxonomy_category(self):
        summary = et.summarize_misses([et.RANDOM_VARIANCE, et.ROLE_CHANGE, et.ROLE_CHANGE])
        self.assertEqual(summary[et.ROLE_CHANGE], 2)
        self.assertEqual(summary[et.RANDOM_VARIANCE], 1)
        self.assertEqual(summary[et.UNKNOWN], 0)


if __name__ == "__main__":
    unittest.main()
