"""
v2.1.1 spec item 1: run_slate.py used to determine training eligibility
with `[g for g in all_final if g < gid]` (a game-ID comparison) and
`all_final[:-5]` (a list-position split) -- both explicitly prohibited
proxies for "what a historical model is allowed to know." These tests
exercise run_slate.py's own pricing path directly (not just the underlying
build_model_state_as_of(), which tests/test_model_state_integrity.py and
tests/test_game_id_independence.py already cover) to prove the CLI/helper
functions themselves never reintroduce the anti-pattern.
"""
import unittest

from run_slate import build_prediction_for_game
from tests.helpers import Fixture, make_test_db, t


def _insert_final_game(conn, game_id, home, away, game_date_offset, start_hour,
                        result_hours_after_start, home_score, away_score,
                        schedule_observed_offset=-90):
    """Same construction as tests/test_game_id_independence.py's helper --
    one FINAL game (games row, game_schedule_events row, game_result_events
    row) with fully explicit timestamps, independent of game_id value."""
    import datetime as dt

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
    conn.execute(
        """INSERT INTO game_result_events
           (game_id, home_score, away_score, final_period_type, game_state,
            effective_at_utc, observed_at_utc, revision_number, source, data_provider)
           VALUES (?,?,?,'REG','FINAL',?,?,1,?,?)""",
        (game_id, home_score, away_score, result_observed, result_observed, "test", "test"),
    )
    conn.commit()
    return result_observed


class TestRunSlateDoesNotUseGameIdAsATrainingProxy(unittest.TestCase):
    """The exact spec scenario: game 100 finishes first, game 90 finishes
    later, target game 95 is predicted between them. run_slate.py's own
    pricing helper must learn 100 and must not learn 90, regardless of the
    numeric IDs disagreeing with chronological order."""

    def setUp(self):
        self.conn, self.path = make_test_db()
        self.conn.execute("INSERT INTO teams (team_id) VALUES ('TOR')")
        self.conn.execute("INSERT INTO teams (team_id) VALUES ('BOS')")
        self.conn.commit()
        # game 100: chronologically FIRST (day 10), result known day 10
        _insert_final_game(self.conn, 100, "TOR", "BOS", game_date_offset=10,
                            start_hour=19, result_hours_after_start=3,
                            home_score=4, away_score=2)
        # game 90: chronologically LATER (day 30), result known day 30 --
        # despite numeric id 90 < 100
        _insert_final_game(self.conn, 90, "TOR", "BOS", game_date_offset=30,
                            start_hour=19, result_hours_after_start=3,
                            home_score=1, away_score=5)
        # target: game 95, scheduled/predicted BETWEEN the two (day 20) --
        # its own game_id (95) is also numerically between 90 and 100
        _insert_final_game(self.conn, 95, "TOR", "BOS", game_date_offset=20,
                            start_hour=19, result_hours_after_start=3,
                            home_score=3, away_score=3 - 1)

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_target_game_learns_100_not_90(self):
        pred = build_prediction_for_game(self.conn, 95, teams=["TOR", "BOS"])
        # season_maturity_games is the min of both teams' games-played
        # counters at prediction time -- 1 if only game 100 was learned,
        # 2 if game 90 leaked in too, 0 if neither.
        self.assertEqual(pred.feature_snapshot["season_maturity_games"], 1)

    def test_target_game_elo_reflects_only_game_100(self):
        import config
        pred = build_prediction_for_game(self.conn, 95, teams=["TOR", "BOS"])
        # TOR won game 100 (4-2) as home team -- Elo must have moved up
        # from the 1500 start; game 90 (TOR lost 1-5) must NOT have been
        # learned, or this would instead be pulled back down/mixed.
        self.assertGreater(pred.feature_snapshot["elo_home"], config.ELO_START)


class TestRunSlateEachPredictionReflectsItsOwnTimestamp(unittest.TestCase):
    """Multiple held-out games where an earlier held-out result becomes
    known before a later held-out prediction: the later prediction MUST
    contain that earlier result in its learned model state -- proving
    run_slate.py's pricing path never freezes one shared model across
    several games (the old all_final[:-5]/all_final[-5:] split's actual
    bug: one model trained once, then reused unchanged across 5 predictions
    without incorporating each other's results as they became available)."""

    def setUp(self):
        self.conn, self.path = make_test_db()
        self.conn.execute("INSERT INTO teams (team_id) VALUES ('TOR')")
        self.conn.execute("INSERT INTO teams (team_id) VALUES ('BOS')")
        self.conn.commit()
        # three "held-out" games in genuine chronological order
        _insert_final_game(self.conn, 201, "TOR", "BOS", game_date_offset=10,
                            start_hour=19, result_hours_after_start=3,
                            home_score=5, away_score=1)
        _insert_final_game(self.conn, 202, "TOR", "BOS", game_date_offset=12,
                            start_hour=19, result_hours_after_start=3,
                            home_score=4, away_score=2)
        _insert_final_game(self.conn, 203, "TOR", "BOS", game_date_offset=14,
                            start_hour=19, result_hours_after_start=3,
                            home_score=3, away_score=1)

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_later_held_out_prediction_learns_earlier_held_out_results(self):
        pred_201 = build_prediction_for_game(self.conn, 201, teams=["TOR", "BOS"])
        pred_202 = build_prediction_for_game(self.conn, 202, teams=["TOR", "BOS"])
        pred_203 = build_prediction_for_game(self.conn, 203, teams=["TOR", "BOS"])

        # game 201 is chronologically first: nothing learned yet
        self.assertEqual(pred_201.feature_snapshot["season_maturity_games"], 0)
        # game 202's own prediction_time is after game 201's result was
        # observed -- it MUST reflect having learned game 201
        self.assertEqual(pred_202.feature_snapshot["season_maturity_games"], 1)
        # game 203's prediction_time is after BOTH 201 and 202's results
        self.assertEqual(pred_203.feature_snapshot["season_maturity_games"], 2)

    def test_predictions_are_not_all_priced_from_one_frozen_state(self):
        # the old bug: one model built once (e.g. up through game 201),
        # then reused unmodified to price 202 and 203 -- which would give
        # 202 and 203 the SAME season_maturity_games as 201 (0), and the
        # SAME elo ratings. Prove that does not happen.
        pred_201 = build_prediction_for_game(self.conn, 201, teams=["TOR", "BOS"])
        pred_203 = build_prediction_for_game(self.conn, 203, teams=["TOR", "BOS"])
        self.assertNotEqual(pred_201.feature_snapshot["season_maturity_games"],
                             pred_203.feature_snapshot["season_maturity_games"])
        self.assertNotEqual(pred_201.feature_snapshot["elo_home"],
                             pred_203.feature_snapshot["elo_home"])


if __name__ == "__main__":
    unittest.main()
