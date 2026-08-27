"""
v2.1 spec items 6/7/8: player_game_stats / goalie_game_stats are
revision-versioned (see schema.sql) so a later box-score correction can
never retroactively change a historical prediction. These tests prove
the exact scenario from the spec: a stat correction observed the
morning after a game must not alter a prediction ("Prediction B")
already generated the night before, for both player stats
(goals/assists) and goalie stats (saves/shots_against).
"""
import unittest

from features import point_in_time as pit
from models.combined_model import CombinedMoneylineModel, build_model_state_as_of
from tests.helpers import Fixture, make_test_db, t


def _insert_scheduled_game(conn, game_id, home, away, date_offset):
    """A second, still-SCHEDULED game to serve as the subject of
    "Prediction B" -- must exist in both `games` and the point-in-time
    schedule history."""
    conn.execute(
        """INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team,
                               away_team, venue, schedule_observed_at_utc, game_state, source)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (game_id, "2025-DEMO", t(date_offset)[:10], t(date_offset, hour=19), home, away,
         "TOR Arena", t(-30), "SCHEDULED", "test"),
    )
    conn.execute(
        """INSERT INTO game_schedule_events
           (game_id, game_date, scheduled_start_utc, home_team, away_team, venue,
            effective_at_utc, observed_at_utc, source, data_provider)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (game_id, t(date_offset)[:10], t(date_offset, hour=19), home, away, "TOR Arena",
         t(-30), t(-30), "test", "test"),
    )
    conn.commit()


class TestPlayerStatRevisionLeakage(unittest.TestCase):
    """Game 1 (TOR 4, BOS 2) completes; TOR_F1's stat line is observed at
    22:30. Prediction B (for a later game 2) is generated at 23:00. The
    following morning the assist total is corrected. Recomputing
    Prediction B must reproduce it exactly."""

    def setUp(self):
        self.conn, self.path = make_test_db()
        self.fx = Fixture(self.conn)   # game 1: TOR vs BOS, day 10, 19:00
        self.fx.finalize_game(1, home_score=4, away_score=2,
                               result_observed_at=t(10, hour=22, minute=30))
        self.fx.add_player_stat(1, "TOR_F1", "TOR", goals=2, assists=1,
                                 observed_at=t(10, hour=22, minute=30))
        _insert_scheduled_game(self.conn, 2, "TOR", "BOS", date_offset=12)
        self.prediction_b_time = t(10, hour=23)

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def _predict_b(self):
        model = build_model_state_as_of(self.conn, self.prediction_b_time, teams=["TOR", "BOS"])
        return model.predict(self.conn, 2, self.prediction_b_time)

    def test_player_game_stats_as_of_ignores_a_later_correction(self):
        before = [dict(r) for r in
                  pit.player_game_stats_as_of(self.conn, 1, t(10, hour=22, minute=30))]
        self.fx.add_player_stat(1, "TOR_F1", "TOR", goals=2, assists=3,
                                 observed_at=t(11, hour=8), revision_number=2)
        after = [dict(r) for r in
                 pit.player_game_stats_as_of(self.conn, 1, t(10, hour=22, minute=30))]
        self.assertEqual(before, after)
        self.assertEqual(before[0]["assists"], 1)

    def test_player_game_stats_as_of_does_see_the_correction_at_a_later_learn_time(self):
        # sanity check that the gating is genuinely time-based, not a
        # blanket "revisions are never read" bug
        self.fx.add_player_stat(1, "TOR_F1", "TOR", goals=2, assists=3,
                                 observed_at=t(11, hour=8), revision_number=2)
        after_correction_known = pit.player_game_stats_as_of(self.conn, 1, t(11, hour=9))
        self.assertEqual(dict(after_correction_known[0])["assists"], 3)

    def test_prediction_b_feature_snapshot_and_probability_unchanged_after_correction(self):
        original = self._predict_b()

        # the following morning, the assist total is corrected (revision 2)
        self.fx.add_player_stat(1, "TOR_F1", "TOR", goals=2, assists=3,
                                 observed_at=t(11, hour=8), revision_number=2)

        recomputed = self._predict_b()
        self.assertEqual(original.feature_snapshot, recomputed.feature_snapshot)
        self.assertEqual(original.model_prob_home, recomputed.model_prob_home)
        self.assertEqual(original.conservative_prob_home, recomputed.conservative_prob_home)

    def test_prediction_b_survives_multiple_later_revisions(self):
        original = self._predict_b()
        for day_offset, assists in ((11, 3), (12, 0), (13, 5)):
            self.fx.add_player_stat(1, "TOR_F1", "TOR", goals=2, assists=assists,
                                     observed_at=t(day_offset, hour=8),
                                     revision_number=day_offset)
        recomputed = self._predict_b()
        self.assertEqual(original.feature_snapshot, recomputed.feature_snapshot)


class TestGoalieStatRevisionLeakage(unittest.TestCase):
    """Same scenario, for goalie saves/shots_against/goals_against."""

    def setUp(self):
        self.conn, self.path = make_test_db()
        self.fx = Fixture(self.conn)
        self.fx.finalize_game(1, home_score=4, away_score=2,
                               result_observed_at=t(10, hour=22, minute=30))
        self.fx.add_goalie_stat(1, "TOR_G1", "TOR", saves=30, shots_against=32,
                                 observed_at=t(10, hour=22, minute=30))
        _insert_scheduled_game(self.conn, 2, "TOR", "BOS", date_offset=12)
        self.prediction_b_time = t(10, hour=23)

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def _predict_b(self):
        model = build_model_state_as_of(self.conn, self.prediction_b_time, teams=["TOR", "BOS"])
        return model.predict(self.conn, 2, self.prediction_b_time)

    def test_goalie_game_stats_as_of_ignores_a_later_correction(self):
        before = [dict(r) for r in
                  pit.goalie_game_stats_as_of(self.conn, 1, t(10, hour=22, minute=30))]
        self.fx.add_goalie_stat(1, "TOR_G1", "TOR", saves=28, shots_against=32,
                                 observed_at=t(11, hour=8), revision_number=2)
        after = [dict(r) for r in
                 pit.goalie_game_stats_as_of(self.conn, 1, t(10, hour=22, minute=30))]
        self.assertEqual(before, after)
        self.assertEqual(before[0]["saves"], 30)

    def test_goalie_game_stats_as_of_does_see_the_correction_at_a_later_learn_time(self):
        self.fx.add_goalie_stat(1, "TOR_G1", "TOR", saves=28, shots_against=32,
                                 observed_at=t(11, hour=8), revision_number=2)
        after_correction_known = pit.goalie_game_stats_as_of(self.conn, 1, t(11, hour=9))
        self.assertEqual(dict(after_correction_known[0])["saves"], 28)

    def test_prediction_b_unchanged_after_saves_and_shots_against_correction(self):
        original = self._predict_b()

        self.fx.add_goalie_stat(1, "TOR_G1", "TOR", saves=25, shots_against=40,
                                 observed_at=t(11, hour=8), revision_number=2)

        recomputed = self._predict_b()
        self.assertEqual(original.feature_snapshot, recomputed.feature_snapshot)
        self.assertEqual(original.model_prob_home, recomputed.model_prob_home)

    def test_prediction_b_unchanged_after_goals_against_only_correction(self):
        original = self._predict_b()

        self.fx.add_goalie_stat(1, "TOR_G1", "TOR", saves=30, shots_against=32,
                                 goals_against=5, observed_at=t(11, hour=8), revision_number=2)

        recomputed = self._predict_b()
        self.assertEqual(original.feature_snapshot, recomputed.feature_snapshot)


class TestStatRevisionEndToEndViaProcessGames(unittest.TestCase):
    """The same guarantee, but exercised through the ordinary
    process_games() walk-forward path rather than a direct
    build_model_state_as_of() call, since that's how real backtests
    actually generate predictions."""

    def setUp(self):
        self.conn, self.path = make_test_db()
        self.fx = Fixture(self.conn)
        self.fx.finalize_game(1, home_score=4, away_score=2,
                               result_observed_at=t(10, hour=22, minute=30))
        self.fx.add_player_stat(1, "TOR_F1", "TOR", goals=2, assists=1,
                                 observed_at=t(10, hour=22, minute=30))
        self.conn.execute(
            """INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team,
                                   away_team, venue, schedule_observed_at_utc, game_state,
                                   home_score, away_score, final_period_type,
                                   result_observed_at_utc, source)
               VALUES (2,'2025-DEMO',?,?,?,?,?,?,?,?,?,?,?,?)""",
            (t(12)[:10], t(12, hour=19), "TOR", "BOS", "TOR Arena", t(-30), "FINAL",
             3, 1, "REG", t(12, hour=22), "test"),
        )
        self.conn.execute(
            """INSERT INTO game_schedule_events
               (game_id, game_date, scheduled_start_utc, home_team, away_team, venue,
                effective_at_utc, observed_at_utc, source, data_provider)
               VALUES (2,?,?,?,?,?,?,?,?,?)""",
            (t(12)[:10], t(12, hour=19), "TOR", "BOS", "TOR Arena", t(-30), t(-30), "test", "test"),
        )
        # v2.1.1: the append-only result-history row -- authoritative
        # source for features.point_in_time.game_result_as_of() /
        # completed_games_known_before().
        self.conn.execute(
            """INSERT INTO game_result_events
               (game_id, home_score, away_score, final_period_type, game_state,
                effective_at_utc, observed_at_utc, revision_number, source, data_provider)
               VALUES (2,3,1,'REG','FINAL',?,?,1,?,?)""",
            (t(12, hour=22), t(12, hour=22), "test", "test"),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_backtest_predictions_unaffected_by_a_correction_made_mid_backtest(self):
        model = CombinedMoneylineModel(teams=["TOR", "BOS"])
        original_preds = model.process_games(self.conn, [1, 2], learn=True,
                                              store_predictions=False)

        # a correction becomes known well after this entire backtest ran
        self.fx.add_player_stat(1, "TOR_F1", "TOR", goals=2, assists=9,
                                 observed_at=t(20), revision_number=2)

        model2 = CombinedMoneylineModel(teams=["TOR", "BOS"])
        rerun_preds = model2.process_games(self.conn, [1, 2], learn=True,
                                            store_predictions=False)

        self.assertEqual(len(original_preds), 2)
        for orig, rerun in zip(original_preds, rerun_preds):
            self.assertEqual(orig.feature_snapshot, rerun.feature_snapshot)
            self.assertEqual(orig.model_prob_home, rerun.model_prob_home)


if __name__ == "__main__":
    unittest.main()
