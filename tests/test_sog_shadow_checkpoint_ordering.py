"""
Preseason Engine Freeze sprint (2026-08-30), Part 1: regression tests
proving operational/record_sog_shadow_observation.py now routes through
the canonical operational/prospective_recording.py::record_observation()
entry point, and therefore respects the PRIMARY_DAILY -> PRE_GAME_UPDATE/
MARKET_REFRESH checkpoint-ordering guard the Preseason Operational
Readiness Closure sprint added. Before this fix, this module called
operational.prospective_ledger.record_model_observation() directly,
silently bypassing that guard.

Reuses tests/test_special_teams_roles_live.py's own fixtures
(_mem_history_conn, _seed_games, DATES_11, _FakeFrozenSogModel) rather
than re-deriving a second, parallel fixture shape.
"""
from __future__ import annotations

from unittest import mock

from operational import prospective_ledger as pl
from operational import prospective_recording as pr
from operational import record_sog_shadow_observation as rsso
from operational import sog_shadow_overlay as shadow
from tests.test_special_teams_roles_live import (
    DATES_11, _FakeFrozenSogModel, _mem_history_conn, _seed_games,
)
import unittest

EVENT_START = "2026-01-12T23:00:00Z"
PRIMARY_CUTOFF = "2026-01-12T14:00:00Z"
PRE_GAME_CUTOFF = "2026-01-12T18:00:00Z"


class _Base(unittest.TestCase):
    def setUp(self):
        self.ledger_conn = pl.init_db(db_path=":memory:")
        self.hist_conn = _mem_history_conn()
        _seed_games(self.hist_conn, "8478402", "EDM", DATES_11, [220.0] * 11)
        self._patcher = mock.patch.object(rsso.sths, "get_connection", return_value=self.hist_conn)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def _record(self, checkpoint, cutoff, model=None):
        return rsso.record_sog_observation(
            model or _FakeFrozenSogModel(), self.ledger_conn, player_id="8478402", team="EDM",
            opponent="CHI", game_id="G1", game_date="2026-01-12", event_start_utc=EVENT_START,
            prediction_cutoff_utc=cutoff, season=20252026, prediction_checkpoint=checkpoint)


class Test01PrimaryDailyRecordsCorrectly(_Base):
    def test_primary_daily_records_successfully(self):
        result = self._record("PRIMARY_DAILY", PRIMARY_CUTOFF)
        self.assertEqual(result["status"], "RECORDED")
        self.assertEqual(result["ledger_result"]["status"], "INSERTED")
        row = pl.get_observation(self.ledger_conn, result["ledger_result"]["prediction_id"])
        self.assertEqual(row["prediction_checkpoint"], "PRIMARY_DAILY")


class Test02PreGameUpdateWithoutPrimaryIsRejected(_Base):
    def test_pre_game_update_without_primary_daily_raises(self):
        with self.assertRaises(pr.CheckpointOrderingError):
            self._record("PRE_GAME_UPDATE", PRE_GAME_CUTOFF)


class Test03PreGameUpdateAfterPrimarySucceeds(_Base):
    def test_pre_game_update_after_primary_daily_succeeds(self):
        self._record("PRIMARY_DAILY", PRIMARY_CUTOFF)
        result = self._record("PRE_GAME_UPDATE", PRE_GAME_CUTOFF)
        self.assertEqual(result["status"], "RECORDED")
        self.assertEqual(result["ledger_result"]["status"], "INSERTED")


class Test04MarketRefreshFollowsSameOrdering(_Base):
    def test_market_refresh_without_primary_daily_raises(self):
        with self.assertRaises(pr.CheckpointOrderingError):
            self._record("MARKET_REFRESH", PRE_GAME_CUTOFF)

    def test_market_refresh_after_primary_daily_succeeds(self):
        self._record("PRIMARY_DAILY", PRIMARY_CUTOFF)
        result = self._record("MARKET_REFRESH", PRE_GAME_CUTOFF)
        self.assertEqual(result["status"], "RECORDED")


class Test05PrimaryDailyRemainsUnchanged(_Base):
    def test_primary_daily_row_is_untouched_by_a_later_checkpoint(self):
        primary = self._record("PRIMARY_DAILY", PRIMARY_CUTOFF)
        primary_id = primary["ledger_result"]["prediction_id"]
        before = pl.get_observation(self.ledger_conn, primary_id)

        self._record("PRE_GAME_UPDATE", PRE_GAME_CUTOFF)

        after = pl.get_observation(self.ledger_conn, primary_id)
        self.assertEqual(before["raw_probability"], after["raw_probability"])
        self.assertEqual(before["sog_shadow_raw_probability"], after["sog_shadow_raw_probability"])
        self.assertEqual(after["prediction_checkpoint"], "PRIMARY_DAILY",
                          "the original PRIMARY_DAILY row must still say PRIMARY_DAILY, never overwritten")


class Test06ShadowSeparateFromProduction(_Base):
    def test_shadow_probability_is_a_distinct_field_from_raw_probability(self):
        result = self._record("PRIMARY_DAILY", PRIMARY_CUTOFF)
        row = pl.get_observation(self.ledger_conn, result["ledger_result"]["prediction_id"])
        self.assertNotEqual(row["raw_probability"], row["sog_shadow_raw_probability"])
        self.assertEqual(row["raw_probability"], result["raw_probability"])
        self.assertEqual(row["sog_shadow_raw_probability"], result["shadow_probability"])


class Test07NeverBecomesRealBet(_Base):
    def test_recorded_row_is_a_model_observation_never_a_real_bet(self):
        result = self._record("PRIMARY_DAILY", PRIMARY_CUTOFF)
        row = pl.get_observation(self.ledger_conn, result["ledger_result"]["prediction_id"])
        self.assertEqual(row["record_type"], "MODEL_OBSERVATION")

    def test_module_never_calls_record_real_bet(self):
        import inspect
        self.assertNotIn("record_real_bet(", inspect.getsource(rsso))


class Test08NeverCreatesRealPnL(_Base):
    def test_no_stake_placed_odds_or_profit_loss_ever_populated(self):
        result = self._record("PRIMARY_DAILY", PRIMARY_CUTOFF)
        row = pl.get_observation(self.ledger_conn, result["ledger_result"]["prediction_id"])
        self.assertIsNone(row["stake"])
        self.assertIsNone(row["placed_odds"])
        self.assertIsNone(row["profit_loss"])


class Test09PPRoleFeatureSnapshotSurvives(_Base):
    def test_role_state_certainty_transition_and_overlay_version_all_recorded(self):
        result = self._record("PRIMARY_DAILY", PRIMARY_CUTOFF)
        row = pl.get_observation(self.ledger_conn, result["ledger_result"]["prediction_id"])
        self.assertEqual(row["pp_role_state"], "STABLE_PP1")
        self.assertIsNotNone(row["pp_role_certainty"])
        self.assertEqual(row["role_overlay_version"], shadow.OVERLAY_VERSION)
        # games_since_onset/transition_state are legitimately None for a
        # long-STABLE role (no transition on record) -- presence of the
        # columns themselves, not a specific non-null value, is the thing
        # this test guards.
        self.assertIn("pp_transition_state", row)
        self.assertIn("pp_games_since_transition", row)


class Test10DuplicateCheckpointIsIdempotent(_Base):
    def test_recording_the_identical_checkpoint_twice_does_not_duplicate(self):
        first = self._record("PRIMARY_DAILY", PRIMARY_CUTOFF)
        second = self._record("PRIMARY_DAILY", PRIMARY_CUTOFF)
        self.assertEqual(first["ledger_result"]["prediction_id"], second["ledger_result"]["prediction_id"])
        self.assertEqual(second["ledger_result"]["status"], "DUPLICATE")
        n = self.ledger_conn.execute(
            "SELECT COUNT(*) FROM predictions WHERE player_id='8478402'").fetchone()[0]
        self.assertEqual(n, 1)


if __name__ == "__main__":
    unittest.main()
