"""
v2.1 spec item 19: the 12 temporal invariants, each tested individually
and by name so this file reads as a checklist against the spec text:

  future roster events        cannot change past predictions/pricing
  future injury events        cannot change past predictions/pricing
  future lineup events        cannot change past predictions/pricing
  future goalie-announcements cannot change past predictions/pricing
  future odds-snapshot events cannot change past predictions/pricing
  future schedule-revisions   cannot change past predictions/pricing
  future game results         cannot change past predictions
  future player-stat corrections cannot change past predictions
  future goalie-stat corrections cannot change past predictions
  future-trained Elo state    cannot change past predictions
  future-trained player-model state cannot change past predictions
  future-trained goalie-model state cannot change past predictions

Deeper, scenario-specific coverage of several of these already exists
elsewhere (test_temporal_integrity.py's mandated deliberate-leakage
test, test_stat_revision.py, test_model_state_integrity.py,
test_schedule_revision.py) -- this file's job is narrower: one cleanly
named, self-contained test per invariant, so "did we test X" has a
single obvious place to look.
"""
import unittest

from features import point_in_time as pit
from models.combined_model import (
    CombinedMoneylineModel,
    ContaminatedModelStateError,
    build_model_state_as_of,
)
from pricing import engine as pricing_engine
from tests.helpers import Fixture, make_test_db, t


def _insert_second_game(conn, game_id, home, away, date_offset, final=False,
                         result_hours_after_start=3, home_score=None, away_score=None,
                         schedule_observed_offset=-90):
    """A second TOR/BOS game, strictly in the future relative to the
    fixture's game 1 (day 10) prediction_time -- used as the source of
    "future" mutations in several invariant tests below."""
    scheduled_start = t(date_offset, hour=19)
    game_date = scheduled_start[:10]
    result_observed = t(date_offset, hour=19 + result_hours_after_start) if final else None
    conn.execute(
        """INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team,
                               away_team, venue, schedule_observed_at_utc, game_state,
                               home_score, away_score, final_period_type,
                               result_observed_at_utc, source)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (game_id, "2025-DEMO", game_date, scheduled_start, home, away, "Arena",
         t(date_offset + schedule_observed_offset), "FINAL" if final else "SCHEDULED",
         home_score, away_score, "REG" if final else None, result_observed, "test"),
    )
    conn.execute(
        """INSERT INTO game_schedule_events
           (game_id, game_date, scheduled_start_utc, home_team, away_team, venue,
            effective_at_utc, observed_at_utc, source, data_provider)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (game_id, game_date, scheduled_start, home, away, "Arena",
         t(date_offset + schedule_observed_offset), t(date_offset + schedule_observed_offset),
         "test", "test"),
    )
    if final:
        # v2.1.1: the append-only result-history row -- authoritative
        # source for features.point_in_time.game_result_as_of() /
        # completed_games_known_before().
        conn.execute(
            """INSERT INTO game_result_events
               (game_id, home_score, away_score, final_period_type, game_state,
                effective_at_utc, observed_at_utc, revision_number, source, data_provider)
               VALUES (?,?,?,'REG','FINAL',?,?,1,?,?)""",
            (game_id, home_score, away_score, result_observed, result_observed, "test", "test"),
        )
    conn.commit()


class TestTemporalInvariants(unittest.TestCase):
    def setUp(self):
        self.conn, self.path = make_test_db()
        self.fx = Fixture(self.conn)   # game 1: TOR vs BOS, day 10, 19:00
        self.fx.set_goalie_status(1, "TOR", "TOR_G1", "CONFIRMED", effective_at=t(9, hour=17))
        self.fx.set_goalie_status(1, "BOS", "BOS_G1", "CONFIRMED", effective_at=t(9, hour=17))
        self.fx.add_odds(1, "TOR", -150, captured_at=t(9, hour=18), label="T-30")
        self.fx.add_odds(1, "BOS", +130, captured_at=t(9, hour=18), label="T-30")
        self.prediction_time = t(9, hour=18, minute=30)   # 30 min before game 1's puck drop

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def _predict(self):
        model = CombinedMoneylineModel(teams=["TOR", "BOS"])
        return model.predict(self.conn, 1, self.prediction_time)

    # ------------------------------------------------------------- 1/2 --

    def test_future_roster_status_change_cannot_change_past_prediction(self):
        before = self._predict()
        # a roster move that becomes effective AND observed after prediction_time
        self.fx.set_roster_status("TOR_F2", "TOR", "OUT",
                                   effective_at=t(9, hour=19), observed_at=t(9, hour=19))
        after = self._predict()
        self.assertEqual(before.feature_snapshot["player_quality_home"],
                          after.feature_snapshot["player_quality_home"])

    def test_future_injury_ir_status_cannot_change_past_prediction(self):
        before = self._predict()
        self.fx.set_roster_status("TOR_F1", "TOR", "IR",
                                   effective_at=t(9, hour=20), observed_at=t(9, hour=20))
        after = self._predict()
        self.assertEqual(before.feature_snapshot, after.feature_snapshot)

    # ------------------------------------------------------------- 3 --

    def test_future_lineup_snapshot_cannot_change_past_point_in_time_read(self):
        # the model doesn't consume lineup_snapshots this slice (spec item
        # 21: no new predictive features), but the point-in-time function
        # itself must still honor the boundary for whenever it IS used.
        before = pit.lineup_for_game(self.conn, 1, "TOR", self.prediction_time)
        self.conn.execute(
            """INSERT INTO lineup_snapshots
               (game_id, team_id, player_id, role, status, effective_at_utc, observed_at_utc, source)
               VALUES (1,'TOR','TOR_F1','L1F','CONFIRMED',?,?,?)""",
            (t(9, hour=19), t(9, hour=19), "test"),
        )
        self.conn.commit()
        after = pit.lineup_for_game(self.conn, 1, "TOR", self.prediction_time)
        self.assertEqual(before, after)

    # ------------------------------------------------------------- 4 --

    def test_future_goalie_announcement_cannot_change_past_prediction(self):
        before = self._predict()
        # starter changes to a backup, announced AFTER prediction_time
        self.fx.set_goalie_status(1, "TOR", "TOR_G2", "CHANGED", effective_at=t(9, hour=19))
        after = self._predict()
        self.assertEqual(before.feature_snapshot["home_goalie_id"],
                          after.feature_snapshot["home_goalie_id"])
        self.assertEqual(before.home_goalie_status, after.home_goalie_status)

    # ------------------------------------------------------------- 5 --

    def test_future_odds_snapshot_cannot_change_past_pricing_decision(self):
        pred = self._predict()
        before_reports = pricing_engine.evaluate_moneyline_for_game(self.conn, pred, "TOR @ BOS")

        # a wildly different DraftKings price captured AFTER prediction_time
        self.fx.add_odds(1, "TOR", +5000, captured_at=t(9, hour=19), label="LATE")
        self.fx.add_odds(1, "BOS", -9000, captured_at=t(9, hour=19), label="LATE")

        after_reports = pricing_engine.evaluate_moneyline_for_game(self.conn, pred, "TOR @ BOS")
        self.assertEqual([r.current_draftkings_price for r in before_reports],
                          [r.current_draftkings_price for r in after_reports])
        self.assertEqual([r.action for r in before_reports], [r.action for r in after_reports])

    # ------------------------------------------------------------- 6 --

    def test_future_schedule_revision_cannot_change_past_prediction(self):
        before = self._predict()
        # game 1 itself gets rescheduled, but the correction is observed
        # AFTER prediction_time -- the earlier prediction must not see it
        self.fx.revise_schedule(1, effective_at=t(9, hour=20), observed_at=t(9, hour=20),
                                 scheduled_start_utc=t(10, hour=21), venue="New Arena")
        after = self._predict()
        self.assertEqual(before.scheduled_start_utc, after.scheduled_start_utc)
        self.assertEqual(before.feature_snapshot, after.feature_snapshot)

    # ------------------------------------------------------------- 7 --

    def test_future_game_result_cannot_change_past_prediction(self):
        _insert_second_game(self.conn, 2, "TOR", "BOS", date_offset=20, final=True,
                             home_score=2, away_score=1)
        model_before = build_model_state_as_of(self.conn, self.prediction_time, teams=["TOR", "BOS"])
        pred_before = model_before.predict(self.conn, 1, self.prediction_time)

        # the "future" game's result is corrected after the fact
        self.conn.execute("UPDATE games SET home_score=99, away_score=0 WHERE game_id=2")
        self.conn.commit()

        model_after = build_model_state_as_of(self.conn, self.prediction_time, teams=["TOR", "BOS"])
        pred_after = model_after.predict(self.conn, 1, self.prediction_time)

        self.assertEqual(model_before._games_played_this_season["TOR"], 0)
        self.assertEqual(model_after._games_played_this_season["TOR"], 0)
        self.assertEqual(pred_before.feature_snapshot, pred_after.feature_snapshot)

    # ------------------------------------------------------------- 8 --

    def test_future_player_stat_correction_cannot_change_past_prediction(self):
        self.fx.finalize_game(1, home_score=4, away_score=2,
                               result_observed_at=t(10, hour=22, minute=30))
        self.fx.add_player_stat(1, "TOR_F1", "TOR", goals=2, assists=1,
                                 observed_at=t(10, hour=22, minute=30))
        _insert_second_game(self.conn, 2, "TOR", "BOS", date_offset=12)  # SCHEDULED
        later_prediction_time = t(10, hour=23)

        before = build_model_state_as_of(self.conn, later_prediction_time,
                                          teams=["TOR", "BOS"]).predict(self.conn, 2, later_prediction_time)
        self.fx.add_player_stat(1, "TOR_F1", "TOR", goals=2, assists=9,
                                 observed_at=t(11, hour=8), revision_number=2)
        after = build_model_state_as_of(self.conn, later_prediction_time,
                                         teams=["TOR", "BOS"]).predict(self.conn, 2, later_prediction_time)

        self.assertEqual(before.feature_snapshot, after.feature_snapshot)

    # ------------------------------------------------------------- 9 --

    def test_future_goalie_stat_correction_cannot_change_past_prediction(self):
        self.fx.finalize_game(1, home_score=4, away_score=2,
                               result_observed_at=t(10, hour=22, minute=30))
        self.fx.add_goalie_stat(1, "TOR_G1", "TOR", saves=30, shots_against=32,
                                 observed_at=t(10, hour=22, minute=30))
        _insert_second_game(self.conn, 2, "TOR", "BOS", date_offset=12)
        later_prediction_time = t(10, hour=23)

        before = build_model_state_as_of(self.conn, later_prediction_time,
                                          teams=["TOR", "BOS"]).predict(self.conn, 2, later_prediction_time)
        self.fx.add_goalie_stat(1, "TOR_G1", "TOR", saves=10, shots_against=32,
                                 observed_at=t(11, hour=8), revision_number=2)
        after = build_model_state_as_of(self.conn, later_prediction_time,
                                         teams=["TOR", "BOS"]).predict(self.conn, 2, later_prediction_time)

        self.assertEqual(before.feature_snapshot, after.feature_snapshot)

    # ----------------------------------------------------------- 10/11/12 --

    def _model_trained_through_game_2(self):
        _insert_second_game(self.conn, 2, "TOR", "BOS", date_offset=20, final=True,
                             home_score=5, away_score=0)
        self.conn.execute(
            """INSERT INTO player_game_stats
               (game_id, player_id, team_id, toi_minutes, goals, assists, shots, played,
                revision_number, effective_at_utc, observed_at_utc, source)
               VALUES (2,'TOR_F1','TOR',18.0,3,2,6,1,1,?,?,?)""",
            (t(20, hour=22), t(20, hour=22), "test"),
        )
        self.conn.execute(
            """INSERT INTO goalie_game_stats
               (game_id, player_id, team_id, started, shots_against, saves, goals_against,
                revision_number, effective_at_utc, observed_at_utc, source)
               VALUES (2,'TOR_G1','TOR',1,30,30,0,1,?,?,?)""",
            (t(20, hour=22), t(20, hour=22), "test"),
        )
        self.conn.commit()
        model = CombinedMoneylineModel(teams=["TOR", "BOS"])
        model.learn(self.conn, 2)
        return model

    def test_future_trained_elo_state_cannot_change_past_prediction(self):
        model = self._model_trained_through_game_2()
        import config
        # proves the in-memory state really did move (otherwise this test
        # would prove nothing)
        self.assertNotEqual(model.elo.ratings["TOR"], config.ELO_START)
        with self.assertRaises(ContaminatedModelStateError):
            model.predict(self.conn, 1, self.prediction_time)

    def test_future_trained_player_model_state_cannot_change_past_prediction(self):
        model = self._model_trained_through_game_2()
        self.assertEqual(model.player_model.games_played("TOR_F1"), 1)
        with self.assertRaises(ContaminatedModelStateError):
            model.predict(self.conn, 1, self.prediction_time)

    def test_future_trained_goalie_model_state_cannot_change_past_prediction(self):
        model = self._model_trained_through_game_2()
        self.assertEqual(model.goalie_model.sample_size("TOR_G1"), 30)
        with self.assertRaises(ContaminatedModelStateError):
            model.predict(self.conn, 1, self.prediction_time)


if __name__ == "__main__":
    unittest.main()
