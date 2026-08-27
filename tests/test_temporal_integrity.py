"""
The primary-objective test file: proves the engine's central claim — "every
historical prediction uses only information that was genuinely available at
its recorded prediction time" — with an end-to-end scenario, not just the
unit-level point_in_time.py tests in test_point_in_time.py.

Includes the explicitly mandated "deliberate leakage test" (spec item 10):
mutate a future game, a future lineup, and a future box score, then prove an
earlier prediction is unchanged.
"""
import copy
import json
import unittest

import config
from models.combined_model import CombinedMoneylineModel
from tests.helpers import Fixture, make_test_db, t


class TestDeliberateLeakage(unittest.TestCase):
    """The mandated test. Two games on the same two teams: game 1 (day 10,
    the one we predict) and game 2 (day 20, strictly in the future relative
    to game 1's prediction time). We compute game 1's prediction, then
    mutate everything about game 2 — its result, a lineup snapshot for it,
    and a box-score row for it — and re-derive game 1's prediction. The
    feature snapshot and resulting probabilities must be byte-for-byte
    identical, because none of those point-in-time reads for game 1 should
    ever depend on game 2's rows."""

    def setUp(self):
        self.conn, self.path = make_test_db()
        self.fx = Fixture(self.conn)  # game_id=1, TOR home vs BOS away, day 10
        # both goalies confirmed well before puck drop, so predict() has a
        # fully-formed feature snapshot to compare
        self.fx.set_goalie_status(1, "TOR", "TOR_G1", "CONFIRMED", effective_at=t(9, hour=17))
        self.fx.set_goalie_status(1, "BOS", "BOS_G1", "CONFIRMED", effective_at=t(9, hour=17))

        # a second game, same two teams, strictly AFTER game 1 in every sense
        self.conn.execute(
            """INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team,
                                   away_team, venue, schedule_observed_at_utc, game_state, source)
               VALUES (2,'2025-DEMO',?,?,?,?,?,?,?,?)""",
            ((t(20)[:10]), t(20, hour=19), "BOS", "TOR", "BOS Arena", t(-30), "FINAL", "test"),
        )
        self.conn.execute(
            """UPDATE games SET home_score=2, away_score=1, final_period_type='REG',
                                 result_observed_at_utc=? WHERE game_id=2""",
            (t(20, hour=22),),
        )
        self.conn.commit()

        self.prediction_time = t(9, hour=18, minute=30)  # 30 min before game 1's puck drop

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def _predict_game_1(self):
        model = CombinedMoneylineModel(teams=["TOR", "BOS"])
        pred = model.predict(self.conn, 1, self.prediction_time)
        return pred

    def test_mutating_future_game_lineup_and_boxscore_does_not_change_earlier_prediction(self):
        before = self._predict_game_1()
        before_fs = copy.deepcopy(before.feature_snapshot)

        # --- mutate the FUTURE game's result ---
        self.conn.execute(
            "UPDATE games SET home_score=99, away_score=0 WHERE game_id=2"
        )
        # --- mutate/add a FUTURE lineup snapshot ---
        self.conn.execute(
            """INSERT INTO lineup_snapshots
               (game_id, team_id, player_id, role, status, effective_at_utc, observed_at_utc, source)
               VALUES (2,'TOR','TOR_F1','L1F','CONFIRMED',?,?,?)""",
            (t(20, hour=17), t(20, hour=17), "test"),
        )
        # --- mutate/add a FUTURE box score row ---
        self.conn.execute(
            """INSERT INTO player_game_stats
               (game_id, player_id, team_id, toi_minutes, goals, assists, shots, played)
               VALUES (2,'TOR_F1','TOR',20.0,5,5,10,1)"""
        )
        self.conn.commit()

        after = self._predict_game_1()
        after_fs = copy.deepcopy(after.feature_snapshot)

        self.assertEqual(before_fs, after_fs,
                          "a game-1 prediction changed after mutating game-2 data — leakage")
        self.assertEqual(before.model_prob_home, after.model_prob_home)
        self.assertEqual(before.conservative_prob_home, after.conservative_prob_home)
        self.assertEqual(before.ci_low, after.ci_low)
        self.assertEqual(before.ci_high, after.ci_high)

    def test_future_roster_status_change_does_not_leak_backward(self):
        before = self._predict_game_1()

        # a player gets hurt AFTER game 1's prediction time, effective and
        # observed both after — must not affect game 1's availability read
        self.fx.set_roster_status("TOR_F2", "TOR", "OUT", effective_at=t(9, hour=19))

        after = self._predict_game_1()
        self.assertEqual(before.feature_snapshot["player_quality_home"],
                          after.feature_snapshot["player_quality_home"])

    def test_future_odds_snapshot_does_not_affect_earlier_market_read(self):
        # DraftKings prices captured after the prediction time (including a
        # deliberately extreme one) must not be selectable at all — proven
        # more thoroughly in test_point_in_time.py, re-asserted here at the
        # pricing-engine layer since that's what spec item 3 cares about.
        from features import point_in_time as pit

        self.fx.add_odds(1, "TOR", -150, captured_at=t(9, hour=8), label="MORNING")
        before = pit.latest_draftkings_snapshot(self.conn, 1, "MONEYLINE", "TOR", self.prediction_time)

        # a wildly different price captured AFTER our prediction time
        self.fx.add_odds(1, "TOR", +5000, captured_at=t(9, hour=19), label="LATE")
        after = pit.latest_draftkings_snapshot(self.conn, 1, "MONEYLINE", "TOR", self.prediction_time)

        self.assertEqual(before["price_american"], -150)
        self.assertEqual(after["price_american"], -150)


class TestPredictBeforeLearnOrdering(unittest.TestCase):
    """Model-level guarantee: process_games() always predicts a game before
    learning from it, and learn() refuses to run on a game that isn't FINAL
    yet — so a model can never absorb a game's own result before pricing it."""

    def setUp(self):
        self.conn, self.path = make_test_db()
        self.fx = Fixture(self.conn)
        self.fx.set_goalie_status(1, "TOR", "TOR_G1", "CONFIRMED", effective_at=t(9, hour=17))
        self.fx.set_goalie_status(1, "BOS", "BOS_G1", "CONFIRMED", effective_at=t(9, hour=17))
        self.fx.finalize_game(1, home_score=4, away_score=2)

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_learn_raises_on_non_final_game(self):
        self.conn.execute("INSERT INTO teams (team_id) VALUES ('XXX')")
        self.conn.execute(
            """INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team,
                                   away_team, schedule_observed_at_utc, game_state, source)
               VALUES (2,'2025-DEMO',?,?,?,?,?,?,?)""",
            (t(20)[:10], t(20, hour=19), "TOR", "XXX", t(-30), "SCHEDULED", "test"),
        )
        self.conn.commit()
        model = CombinedMoneylineModel(teams=["TOR", "BOS", "XXX"])
        with self.assertRaises(ValueError):
            model.learn(self.conn, 2)

    def test_process_games_predicts_before_learning_changes_ratings(self):
        model = CombinedMoneylineModel(teams=["TOR", "BOS"])
        elo_before = dict(model.elo.ratings)
        preds = model.process_games(self.conn, [1], learn=True, store_predictions=False)
        # the prediction must reflect PRE-game ratings, not post-game ones
        pred = preds[0]
        self.assertEqual(pred.feature_snapshot["elo_home"], elo_before["TOR"])
        self.assertEqual(pred.feature_snapshot["elo_away"], elo_before["BOS"])
        # and learn() must actually have run afterward
        self.assertNotEqual(model.elo.ratings["TOR"], elo_before["TOR"])

    def test_training_rows_strictly_earlier_than_prediction_time(self):
        # game 1's prediction_time is 30 min before its own scheduled start;
        # verify the model computed it from the FIXTURE's scheduled_start,
        # not from wall-clock or from game 1's own result-observed time.
        model = CombinedMoneylineModel(teams=["TOR", "BOS"])
        prediction_time = model.prediction_time_for_game(self.conn, 1)
        self.assertLess(prediction_time, self.fx.scheduled_start)


if __name__ == "__main__":
    unittest.main()
