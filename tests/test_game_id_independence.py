"""
v2.1 spec items 2/3: no historical training-eligibility logic may use
game_id ordering (or game_date ordering) as a proxy for "when a result
became known." The only correct rule is result_observed_at_utc. These
tests construct scenarios where game_id order and chronological order
DISAGREE, proving features.point_in_time.completed_games_known_before()
(and everything built on it: CombinedMoneylineModel.all_final_game_ids,
build_model_state_as_of, process_games) gets it right anyway.
"""
import datetime as dt
import unittest

from features import point_in_time as pit
from models.combined_model import CombinedMoneylineModel, build_model_state_as_of
from tests.helpers import Fixture, make_test_db, t


def _insert_final_game(conn, game_id, home, away, game_date_offset, start_hour,
                        result_hours_after_start, home_score, away_score,
                        schedule_observed_offset=-90):
    """Insert one FINAL game (games row + matching game_schedule_events
    row) with fully explicit timestamps, independent of game_id value."""
    scheduled_start = t(game_date_offset, hour=start_hour)
    result_observed = (dt.datetime.fromisoformat(scheduled_start)
                        + dt.timedelta(hours=result_hours_after_start)).isoformat()
    schedule_observed = t(game_date_offset + schedule_observed_offset)
    game_date = scheduled_start[:10]
    conn.execute(
        """INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team,
                               away_team, venue, schedule_observed_at_utc, game_state,
                               home_score, away_score, final_period_type,
                               result_observed_at_utc, source)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (game_id, "2025-DEMO", game_date, scheduled_start, home, away, "Arena",
         schedule_observed, "FINAL", home_score, away_score, "REG", result_observed, "test"),
    )
    conn.execute(
        """INSERT INTO game_schedule_events
           (game_id, game_date, scheduled_start_utc, home_team, away_team, venue,
            effective_at_utc, observed_at_utc, source, data_provider)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (game_id, game_date, scheduled_start, home, away, "Arena",
         schedule_observed, schedule_observed, "test", "test"),
    )
    # v2.1.1: the append-only result-history row -- authoritative source
    # for features.point_in_time.game_result_as_of() /
    # completed_games_known_before(). The `games` row above is only a
    # current-state convenience cache.
    conn.execute(
        """INSERT INTO game_result_events
           (game_id, home_score, away_score, final_period_type, game_state,
            effective_at_utc, observed_at_utc, revision_number, source, data_provider)
           VALUES (?,?,?,'REG','FINAL',?,?,1,?,?)""",
        (game_id, home_score, away_score, result_observed, result_observed, "test", "test"),
    )
    conn.commit()
    return result_observed


class TestOutOfOrderGameIds(unittest.TestCase):
    """The user's exact scenario: game_id 100 scheduled/completed
    chronologically FIRST, game_id 90 scheduled/completed chronologically
    LATER -- despite 100 > 90."""

    def setUp(self):
        self.conn, self.path = make_test_db()
        self.conn.execute("INSERT INTO teams (team_id) VALUES ('TOR')")
        self.conn.execute("INSERT INTO teams (team_id) VALUES ('BOS')")
        self.conn.commit()
        # Game 100: date offset 10 (chronologically EARLIER)
        self.result_100 = _insert_final_game(self.conn, 100, "TOR", "BOS",
                                              game_date_offset=10, start_hour=19,
                                              result_hours_after_start=3,
                                              home_score=4, away_score=2)
        # Game 90: date offset 20 (chronologically LATER), despite id < 100
        self.result_90 = _insert_final_game(self.conn, 90, "TOR", "BOS",
                                             game_date_offset=20, start_hour=19,
                                             result_hours_after_start=3,
                                             home_score=1, away_score=5)

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_a_january_15_equivalent_prediction_learns_100_not_90(self):
        # predicting "mid-way" (offset 15) between the two games' dates
        mid_prediction_time = t(15)
        eligible = pit.completed_games_known_before(self.conn, mid_prediction_time)
        self.assertIn(100, eligible)
        self.assertNotIn(90, eligible)

    def test_completed_games_known_before_orders_by_result_time_not_id(self):
        # ask for everything -> game 100 (earlier result) must come before
        # game 90 (later result) despite 100 > 90 numerically
        all_eligible = pit.completed_games_known_before(self.conn)
        self.assertEqual(all_eligible, [100, 90])

    def test_all_final_game_ids_delegates_to_the_same_ordering(self):
        # CombinedMoneylineModel.all_final_game_ids must not reimplement
        # its own (wrong) ordering -- it delegates to the function above
        self.assertEqual(CombinedMoneylineModel.all_final_game_ids(self.conn), [100, 90])

    def test_build_model_state_as_of_mid_point_reflects_only_game_100(self):
        mid_prediction_time = t(15)
        model = build_model_state_as_of(self.conn, mid_prediction_time, teams=["TOR", "BOS"])
        # exactly one game learned (100); its home team (TOR) won 4-2, so
        # TOR's Elo must have moved up from the 1500 start
        import config
        self.assertGreater(model.elo.ratings["TOR"], config.ELO_START)
        self.assertEqual(model._games_played_this_season["TOR"], 1)

    def test_build_model_state_as_of_after_both_reflects_both_games(self):
        after_both = t(25)
        model = build_model_state_as_of(self.conn, after_both, teams=["TOR", "BOS"])
        self.assertEqual(model._games_played_this_season["TOR"], 2)

    def test_process_games_in_id_order_still_produces_correct_predictions(self):
        # feed process_games the game_ids in the "wrong" (id) order --
        # [90, 100] -- and confirm it still processes them by ACTUAL
        # chronological (prediction_time / result_observed_at) order
        # internally (spec item 20's chronological merge), not input order.
        model = CombinedMoneylineModel(teams=["TOR", "BOS"])
        preds = model.process_games(self.conn, [90, 100], learn=True, store_predictions=False)
        # game 100's prediction must reflect ZERO games learned yet (it's
        # chronologically first); game 90's must reflect game 100 already learned
        pred_100 = next(p for p in preds if p.game_id == 100)
        pred_90 = next(p for p in preds if p.game_id == 90)
        self.assertEqual(pred_100.feature_snapshot["season_maturity_games"], 0)
        self.assertEqual(pred_90.feature_snapshot["season_maturity_games"], 1)


class TestPostponedAndRescheduledGames(unittest.TestCase):
    """Postponed/rescheduled/late-finished games must always resolve by
    result_observed_at_utc, never by when they were originally supposed
    to happen or by insertion/game_id order."""

    def setUp(self):
        self.conn, self.path = make_test_db()
        self.conn.execute("INSERT INTO teams (team_id) VALUES ('TOR')")
        self.conn.execute("INSERT INTO teams (team_id) VALUES ('BOS')")
        self.conn.execute("INSERT INTO teams (team_id) VALUES ('OTT')")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_postponed_game_is_ineligible_until_its_actual_late_result(self):
        # game_id 5 was ORIGINALLY going to be day 10, but got postponed;
        # it actually completes on day 40 (result_observed accordingly).
        # A prediction on day 20 (after the original date, before the
        # real one) must NOT see it as a completed game.
        _insert_final_game(self.conn, 5, "TOR", "OTT", game_date_offset=40,
                            start_hour=19, result_hours_after_start=3,
                            home_score=3, away_score=1)
        eligible_day_20 = pit.completed_games_known_before(self.conn, t(20))
        self.assertNotIn(5, eligible_day_20)
        eligible_day_41 = pit.completed_games_known_before(self.conn, t(41))
        self.assertIn(5, eligible_day_41)

    def test_late_finishing_game_reported_after_a_higher_id_game(self):
        # game_id 10 (lower id) finishes LATE (result observed day 30);
        # game_id 20 (higher id) finishes EARLY and is reported first
        # (result observed day 12). Eligibility must track the reported
        # time, not id, not scheduled date.
        result_10 = _insert_final_game(self.conn, 10, "TOR", "BOS", game_date_offset=25,
                                        start_hour=19, result_hours_after_start=5,
                                        home_score=2, away_score=2 + 1)
        result_20 = _insert_final_game(self.conn, 20, "TOR", "OTT", game_date_offset=11,
                                        start_hour=19, result_hours_after_start=3,
                                        home_score=5, away_score=1)
        # sanity: game 20's result really is known before game 10's
        self.assertLess(result_20, result_10)

        as_of_day_13 = pit.completed_games_known_before(self.conn, t(13))
        self.assertIn(20, as_of_day_13)
        self.assertNotIn(10, as_of_day_13)

        ordering = pit.completed_games_known_before(self.conn)
        self.assertEqual(ordering, [20, 10])   # by result time, not by game_id


if __name__ == "__main__":
    unittest.main()
