"""
Preseason Operational Readiness Closure sprint (2026-08-30), Track 3 Part
22-24: end-to-end tests for the upgraded operational/settle_daily_
observations.py batch, wiring the real operational/outcome_resolver.py
against a real (temp-file) copy of both the prospective ledger schema and
the frozen nhl.db schema.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
import tempfile
import unittest
from pathlib import Path

import db
from operational import prospective_ledger as pl
from operational import settle_daily_observations as sdo


def _fresh_official_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = db.init_db(db_path=Path(tmp.name), wipe=True)
    for team in ("EDM", "CHI"):
        conn.execute("INSERT OR IGNORE INTO teams (team_id, full_name) VALUES (?, ?)", (team, team))
    conn.commit()
    return conn


def _insert_game(conn, game_id, game_state="FINAL", home_score=4, away_score=2):
    conn.execute(
        """INSERT INTO games (game_id, season, game_date, home_team, away_team, game_state,
           home_score, away_score, final_period_type, source)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (game_id, "20262027", "2026-10-15", "EDM", "CHI", game_state, home_score, away_score,
         "REG", "test_fixture"))
    conn.commit()


def _insert_player_stat(conn, game_id, player_id, shots=0, goals=0, assists=0):
    conn.execute(
        """INSERT INTO player_game_stats (game_id, player_id, team_id, toi_minutes, goals,
           assists, shots, played, revision_number, effective_at_utc, observed_at_utc, source)
           VALUES (?,?,?,?,?,?,?,1,1,?,?,?)""",
        (game_id, player_id, "EDM", 18.0, goals, assists, shots,
         "2026-10-15T23:00:00Z", "2026-10-15T23:00:00Z", "test_fixture"))
    conn.commit()


_YESTERDAY = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)
PAST_EVENT = _YESTERDAY.strftime("%Y-%m-%dT23:00:00.000000Z")
PAST_CUTOFF = _YESTERDAY.strftime("%Y-%m-%dT18:00:00.000000Z")
PAST_GAME_DATE = _YESTERDAY.strftime("%Y-%m-%d")


def _record_model_observation(ledger_conn, prediction_id, threshold="3+", player_id="P1", game_id=1):
    return pl.record_model_observation(
        ledger_conn, prediction_id=prediction_id, event_start_utc=PAST_EVENT,
        created_at_utc=PAST_CUTOFF, prediction_cutoff_utc=PAST_CUTOFF, game_id=game_id,
        game_date="2026-10-15", player_id=player_id, team="EDM", opponent="CHI",
        market_id="PLAYER_SOG", threshold=threshold, raw_probability=0.5)


class Test01BasicSettlement(unittest.TestCase):
    def setUp(self):
        self.ledger = pl.init_db(db_path=":memory:")
        self.official = _fresh_official_db()

    def test_win_settled_from_real_data(self):
        _insert_game(self.official, 1)
        _insert_player_stat(self.official, 1, "P1", shots=5)
        _record_model_observation(self.ledger, "pred-1")
        summary = sdo.run_settlement_batch(self.ledger, self.official)
        self.assertEqual(summary["settled_win"], 1)
        row = pl.get_observation(self.ledger, "pred-1")
        self.assertEqual(row["result_status"], "WIN")
        self.assertEqual(row["actual_outcome"], "5")

    def test_loss_settled_from_real_data(self):
        _insert_game(self.official, 1)
        _insert_player_stat(self.official, 1, "P1", shots=1)
        _record_model_observation(self.ledger, "pred-1")
        summary = sdo.run_settlement_batch(self.ledger, self.official)
        self.assertEqual(summary["settled_loss"], 1)

    def test_game_not_final_stays_pending(self):
        _insert_game(self.official, 1, game_state="LIVE")
        _record_model_observation(self.ledger, "pred-1")
        summary = sdo.run_settlement_batch(self.ledger, self.official)
        self.assertEqual(summary["still_pending_game_not_final"], 1)
        row = pl.get_observation(self.ledger, "pred-1")
        self.assertEqual(row["result_status"], "PENDING")


class Test02Idempotency(unittest.TestCase):
    def test_running_twice_does_not_double_settle(self):
        ledger = pl.init_db(db_path=":memory:")
        official = _fresh_official_db()
        _insert_game(official, 1)
        _insert_player_stat(official, 1, "P1", shots=5)
        _record_model_observation(ledger, "pred-1")

        first = sdo.run_settlement_batch(ledger, official)
        second = sdo.run_settlement_batch(ledger, official)

        self.assertEqual(first["settled_win"], 1)
        self.assertEqual(second["total_candidates"], 0,
                          "a settled row must never be re-selected as a candidate")
        row = pl.get_observation(ledger, "pred-1")
        self.assertEqual(row["result_status"], "WIN")

    def test_rerunning_after_game_goes_final_settles_exactly_once(self):
        ledger = pl.init_db(db_path=":memory:")
        official = _fresh_official_db()
        _insert_game(official, 1, game_state="LIVE")
        _record_model_observation(ledger, "pred-1")

        first = sdo.run_settlement_batch(ledger, official)
        self.assertEqual(first["still_pending_game_not_final"], 1)

        official.execute("UPDATE games SET game_state='FINAL' WHERE game_id=1")
        official.commit()
        _insert_player_stat(official, 1, "P1", shots=5)
        second = sdo.run_settlement_batch(ledger, official)
        self.assertEqual(second["settled_win"], 1)

        third = sdo.run_settlement_batch(ledger, official)
        self.assertEqual(third["total_candidates"], 0)


class Test03ImmutablePredictionFieldsAfterSettlement(unittest.TestCase):
    def test_settlement_never_mutates_prediction_time_fields(self):
        ledger = pl.init_db(db_path=":memory:")
        official = _fresh_official_db()
        _insert_game(official, 1)
        _insert_player_stat(official, 1, "P1", shots=5)
        _record_model_observation(ledger, "pred-1")
        before = pl.get_observation(ledger, "pred-1")

        sdo.run_settlement_batch(ledger, official)
        after = pl.get_observation(ledger, "pred-1")

        for field in ("raw_probability", "market_id", "threshold", "player_id", "game_id",
                      "event_start_utc", "created_at_utc"):
            self.assertEqual(before[field], after[field], f"{field} must never change on settlement")
        self.assertNotEqual(before["result_status"], after["result_status"])

    def test_direct_update_of_a_prediction_field_after_settlement_still_raises(self):
        ledger = pl.init_db(db_path=":memory:")
        official = _fresh_official_db()
        _insert_game(official, 1)
        _insert_player_stat(official, 1, "P1", shots=5)
        _record_model_observation(ledger, "pred-1")
        sdo.run_settlement_batch(ledger, official)
        with self.assertRaises(sqlite3.IntegrityError):
            ledger.execute("UPDATE predictions SET raw_probability=0.99 WHERE prediction_id='pred-1'")


class Test04VoidVsUnresolvedEligibility(unittest.TestCase):
    """Part 21: a REAL_BET/SHADOW record on a player who never appeared
    in the real stats is VOID (standard industry convention); the SAME
    real-world fact for a MODEL_OBSERVATION (no real money) is a distinct
    UNRESOLVED eligibility state, never silently WIN/LOSS, never labeled
    VOID as if money had been on it."""

    def test_model_observation_player_did_not_dress_is_unresolved_not_void(self):
        ledger = pl.init_db(db_path=":memory:")
        official = _fresh_official_db()
        _insert_game(official, 1)  # no player_game_stats row for P1 at all
        _record_model_observation(ledger, "pred-1")
        summary = sdo.run_settlement_batch(ledger, official)
        self.assertEqual(summary["settled_unresolved"], 1)
        row = pl.get_observation(ledger, "pred-1")
        self.assertEqual(row["result_status"], "UNRESOLVED")
        self.assertIn("PLAYER_DID_NOT_DRESS", row["notes"])

    def test_real_bet_player_did_not_dress_is_void(self):
        ledger = pl.init_db(db_path=":memory:")
        official = _fresh_official_db()
        _insert_game(official, 1)
        pl.record_real_bet(
            ledger, prediction_id="bet-1", event_start_utc=PAST_EVENT, created_at_utc=PAST_CUTOFF,
            prediction_cutoff_utc=PAST_CUTOFF, game_id=1, game_date="2026-10-15", player_id="P1",
            team="EDM", opponent="CHI", market_id="PLAYER_SOG", threshold="3+", raw_probability=0.5,
            stake=10.0, placed_odds=-110, placed_at_utc=PAST_CUTOFF, sportsbook="draftkings")
        summary = sdo.run_settlement_batch(ledger, official)
        self.assertEqual(summary["settled_void"], 1)
        row = pl.get_observation(ledger, "bet-1")
        self.assertEqual(row["result_status"], "VOID")


class Test05UnsupportedMarketBacklog(unittest.TestCase):
    def test_unsupported_market_goes_unresolved_with_reason_in_notes(self):
        ledger = pl.init_db(db_path=":memory:")
        official = _fresh_official_db()
        _insert_game(official, 1)
        pl.record_model_observation(
            ledger, prediction_id="pred-blocks", event_start_utc=PAST_EVENT, created_at_utc=PAST_CUTOFF,
            prediction_cutoff_utc=PAST_CUTOFF, game_id=1, game_date="2026-10-15", player_id="P1",
            team="EDM", opponent="CHI", market_id="PLAYER_BLOCKS", threshold="2+", raw_probability=0.5)
        summary = sdo.run_settlement_batch(ledger, official)
        self.assertEqual(summary["settled_unresolved"], 1)
        row = pl.get_observation(ledger, "pred-blocks")
        self.assertIn("BLOCKS_NOT_INGESTED", row["notes"])


class Test06BatchErrorResilience(unittest.TestCase):
    def test_one_bad_row_does_not_abort_the_whole_batch(self):
        ledger = pl.init_db(db_path=":memory:")
        official = _fresh_official_db()
        _insert_game(official, 1)
        _insert_player_stat(official, 1, "P1", shots=5)
        _record_model_observation(ledger, "pred-good", threshold="3+", player_id="P1")
        _record_model_observation(ledger, "pred-bad", threshold="not-a-number", player_id="P1")
        summary = sdo.run_settlement_batch(ledger, official)
        self.assertEqual(summary["settled_win"], 1)
        good = pl.get_observation(ledger, "pred-good")
        bad = pl.get_observation(ledger, "pred-bad")
        self.assertEqual(good["result_status"], "WIN")
        # "not-a-number" still parses via int("not-a-number".rstrip("+")) failing ->
        # UNSUPPORTED_SETTLEMENT_MARKET (fails closed, not a batch-aborting exception).
        self.assertEqual(bad["result_status"], "UNRESOLVED")


if __name__ == "__main__":
    unittest.main()
