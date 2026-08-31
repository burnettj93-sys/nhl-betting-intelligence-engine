"""
Preseason Operational Readiness Closure sprint (2026-08-30), Track 4:
checkpoint semantics (PRIMARY_DAILY / PRE_GAME_UPDATE) and puck-drop lock
tests.

Parts 27 (exact-start guard), 28 (late price rejection), and 29 (late
stat revision never mutates a prediction-time snapshot) were ALREADY
correctly implemented by prior sprints (insert_prediction()'s own
created_at_utc/odds_captured_at_utc >= event_start_utc guards, and the
immutability trigger + revision-versioned nhl.db tables respectively) --
this file adds direct regression tests confirming that, rather than
rebuilding mechanism that already exists. Part 25/26 (checkpoint
ordering) is NEW this sprint: operational/prospective_recording.py's
record_observation() now requires an existing PRIMARY_DAILY row before
accepting a PRE_GAME_UPDATE/MARKET_REFRESH for the same logical bet.
"""
from __future__ import annotations

import unittest

from operational import prospective_ledger as pl
from operational import prospective_recording as pr

EVENT_START = "2026-10-15T23:00:00.000000Z"
BEFORE_START = "2026-10-15T18:00:00.000000Z"
LATER_BEFORE_START = "2026-10-15T20:00:00.000000Z"


def _base_prediction(**overrides):
    base = dict(
        model_id="PLAYER_SOG", game_id=1, game_date="2026-10-15", player_id="P1", team="EDM",
        opponent="CHI", market_id="PLAYER_SOG", threshold="3+", side="OVER",
        event_start_utc=EVENT_START, prediction_cutoff_utc=BEFORE_START,
        created_at_utc=BEFORE_START, raw_probability=0.5, context_adjusted_probability=0.5,
        coherent_probability=0.5, confidence="HIGH",
    )
    base.update(overrides)
    return base


class Test01CheckpointOrdering(unittest.TestCase):
    """Part 25/26."""

    def setUp(self):
        self.conn = pl.init_db(db_path=":memory:")

    def test_pre_game_update_without_a_prior_primary_daily_is_rejected(self):
        with self.assertRaises(pr.CheckpointOrderingError):
            pr.record_observation(self.conn, _base_prediction(), is_demo=False,
                                   checkpoint="PRE_GAME_UPDATE")

    def test_pre_game_update_after_a_real_primary_daily_succeeds(self):
        pr.record_observation(self.conn, _base_prediction(), is_demo=False, checkpoint="PRIMARY_DAILY")
        result = pr.record_observation(
            self.conn, _base_prediction(created_at_utc=LATER_BEFORE_START,
                                         prediction_cutoff_utc=LATER_BEFORE_START),
            is_demo=False, checkpoint="PRE_GAME_UPDATE")
        self.assertEqual(result["status"], "INSERTED")

    def test_primary_daily_and_pre_game_update_are_two_separate_rows_never_one_overwritten(self):
        first = pr.record_observation(self.conn, _base_prediction(), is_demo=False,
                                       checkpoint="PRIMARY_DAILY")
        second = pr.record_observation(
            self.conn, _base_prediction(created_at_utc=LATER_BEFORE_START,
                                         prediction_cutoff_utc=LATER_BEFORE_START,
                                         raw_probability=0.62),
            is_demo=False, checkpoint="PRE_GAME_UPDATE")
        self.assertNotEqual(first["prediction_id"], second["prediction_id"])
        primary_row = pl.get_observation(self.conn, first["prediction_id"])
        update_row = pl.get_observation(self.conn, second["prediction_id"])
        self.assertEqual(primary_row["prediction_checkpoint"], "PRIMARY_DAILY")
        self.assertEqual(update_row["prediction_checkpoint"], "PRE_GAME_UPDATE")
        self.assertEqual(primary_row["raw_probability"], 0.5, "PRIMARY_DAILY row must be untouched")
        self.assertEqual(update_row["raw_probability"], 0.62)

    def test_market_refresh_also_requires_a_prior_primary_daily(self):
        with self.assertRaises(pr.CheckpointOrderingError):
            pr.record_observation(self.conn, _base_prediction(), is_demo=False,
                                   checkpoint="MARKET_REFRESH")

    def test_latest_checkpoint_row_returns_the_specific_checkpoint_when_asked(self):
        pr.record_observation(self.conn, _base_prediction(), is_demo=False, checkpoint="PRIMARY_DAILY")
        pr.record_observation(
            self.conn, _base_prediction(created_at_utc=LATER_BEFORE_START,
                                         prediction_cutoff_utc=LATER_BEFORE_START, raw_probability=0.62),
            is_demo=False, checkpoint="PRE_GAME_UPDATE")
        primary = pr.latest_checkpoint_row(self.conn, game_id=1, player_id="P1", market_id="PLAYER_SOG",
                                            threshold="3+", side="OVER", checkpoint="PRIMARY_DAILY")
        self.assertEqual(primary["raw_probability"], 0.5)
        latest_any = pr.latest_checkpoint_row(self.conn, game_id=1, player_id="P1", market_id="PLAYER_SOG",
                                               threshold="3+", side="OVER")
        self.assertEqual(latest_any["prediction_checkpoint"], "PRE_GAME_UPDATE")

    def test_a_different_player_or_market_is_a_different_logical_bet_needs_its_own_primary_daily(self):
        pr.record_observation(self.conn, _base_prediction(), is_demo=False, checkpoint="PRIMARY_DAILY")
        with self.assertRaises(pr.CheckpointOrderingError):
            pr.record_observation(self.conn, _base_prediction(player_id="P2"), is_demo=False,
                                   checkpoint="PRE_GAME_UPDATE")


class Test02ExactStartGuard(unittest.TestCase):
    """Part 27: regression for existing insert_prediction() behavior --
    no new prediction may be created at observed_at >= event_start_utc,
    exact equality included."""

    def setUp(self):
        self.conn = pl.init_db(db_path=":memory:")

    def test_created_exactly_at_event_start_is_rejected(self):
        with self.assertRaises(pl.InvalidPredictionError):
            pl.record_model_observation(
                self.conn, event_start_utc=EVENT_START, created_at_utc=EVENT_START,
                prediction_cutoff_utc=EVENT_START, game_id=1, game_date="2026-10-15",
                player_id="P1", team="EDM", opponent="CHI", market_id="PLAYER_SOG",
                threshold="3+", raw_probability=0.5)

    def test_created_one_microsecond_before_event_start_is_accepted(self):
        result = pl.record_model_observation(
            self.conn, event_start_utc=EVENT_START, created_at_utc="2026-10-15T22:59:59.999999Z",
            prediction_cutoff_utc=BEFORE_START, game_id=1, game_date="2026-10-15",
            player_id="P1", team="EDM", opponent="CHI", market_id="PLAYER_SOG",
            threshold="3+", raw_probability=0.5)
        self.assertEqual(result["status"], "INSERTED")

    def test_created_after_event_start_is_rejected(self):
        with self.assertRaises(pl.InvalidPredictionError):
            pl.record_model_observation(
                self.conn, event_start_utc=EVENT_START, created_at_utc="2026-10-16T00:00:00.000000Z",
                prediction_cutoff_utc=BEFORE_START, game_id=1, game_date="2026-10-15",
                player_id="P1", team="EDM", opponent="CHI", market_id="PLAYER_SOG",
                threshold="3+", raw_probability=0.5)


class Test03LatePriceRejection(unittest.TestCase):
    """Part 28: regression for existing odds_captured_at_utc >=
    event_start_utc guard -- a late-arriving price after puck drop
    cannot become a valid pregame market observation."""

    def setUp(self):
        self.conn = pl.init_db(db_path=":memory:")

    def test_odds_captured_after_puck_drop_is_rejected(self):
        with self.assertRaises(pl.InvalidPredictionError):
            pl.record_model_observation(
                self.conn, event_start_utc=EVENT_START, created_at_utc=BEFORE_START,
                prediction_cutoff_utc=BEFORE_START, game_id=1, game_date="2026-10-15",
                player_id="P1", team="EDM", opponent="CHI", market_id="PLAYER_SOG",
                threshold="3+", raw_probability=0.5,
                odds_captured_at_utc="2026-10-16T00:00:00.000000Z", odds_american=-110)

    def test_odds_captured_exactly_at_puck_drop_is_rejected(self):
        with self.assertRaises(pl.InvalidPredictionError):
            pl.record_model_observation(
                self.conn, event_start_utc=EVENT_START, created_at_utc=BEFORE_START,
                prediction_cutoff_utc=BEFORE_START, game_id=1, game_date="2026-10-15",
                player_id="P1", team="EDM", opponent="CHI", market_id="PLAYER_SOG",
                threshold="3+", raw_probability=0.5,
                odds_captured_at_utc=EVENT_START, odds_american=-110)

    def test_odds_captured_well_before_puck_drop_is_accepted(self):
        result = pl.record_model_observation(
            self.conn, event_start_utc=EVENT_START, created_at_utc=BEFORE_START,
            prediction_cutoff_utc=BEFORE_START, game_id=1, game_date="2026-10-15",
            player_id="P1", team="EDM", opponent="CHI", market_id="PLAYER_SOG",
            threshold="3+", raw_probability=0.5,
            odds_captured_at_utc=BEFORE_START, odds_american=-110)
        self.assertEqual(result["status"], "INSERTED")


class Test04LateStatRevisionNeverMutatesSnapshot(unittest.TestCase):
    """Part 29: a later-arriving official stat revision (nhl.db's own
    revision-versioned player_game_stats/goalie_game_stats tables) must
    never mutate an already-recorded prediction's feature snapshot --
    the prospective ledger's immutability trigger is the enforcement
    mechanism, exercised here directly."""

    def test_settling_a_prediction_never_touches_its_prediction_time_probability(self):
        conn = pl.init_db(db_path=":memory:")
        result = pl.record_model_observation(
            conn, event_start_utc=EVENT_START, created_at_utc=BEFORE_START,
            prediction_cutoff_utc=BEFORE_START, game_id=1, game_date="2026-10-15",
            player_id="P1", team="EDM", opponent="CHI", market_id="PLAYER_SOG",
            threshold="3+", raw_probability=0.664)
        pred_id = result["prediction_id"]
        pl.settle_prediction(conn, pred_id, "WIN", actual_outcome="5")
        # Simulate a LATER official stat revision by settling again with a
        # DIFFERENT actual_outcome (as if a corrected boxscore arrived) --
        # settlement fields may change, but the prediction-time
        # raw_probability must never move.
        pl.settle_prediction(conn, pred_id, "WIN", actual_outcome="6")
        row = pl.get_observation(conn, pred_id)
        self.assertEqual(row["raw_probability"], 0.664)
        self.assertEqual(row["actual_outcome"], "6")

    def test_direct_attempt_to_mutate_raw_probability_after_settlement_raises(self):
        import sqlite3
        conn = pl.init_db(db_path=":memory:")
        result = pl.record_model_observation(
            conn, event_start_utc=EVENT_START, created_at_utc=BEFORE_START,
            prediction_cutoff_utc=BEFORE_START, game_id=1, game_date="2026-10-15",
            player_id="P1", team="EDM", opponent="CHI", market_id="PLAYER_SOG",
            threshold="3+", raw_probability=0.664)
        pl.settle_prediction(conn, result["prediction_id"], "WIN", actual_outcome="5")
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE predictions SET raw_probability=0.9 WHERE prediction_id=?",
                         (result["prediction_id"],))


if __name__ == "__main__":
    unittest.main()
