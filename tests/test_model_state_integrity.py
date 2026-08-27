"""
v2.1 spec items 9/10/11: in-memory learned model state (Elo, player
ratings, goalie ratings, season-maturity counters) is itself a temporal-
leakage vector that no amount of point-in-time SQL alone can close. These
tests implement the exact required scenario: games 1-100 exist; a
prediction is generated/persisted for game 75 using only pre-75
knowledge; a model is then trained through game 100; the system is asked
to reproduce/rebuild the game-75 prediction. Game 75 must never receive
information from games 76-100, tested along BOTH required paths:
  (A) a fresh model built to game 75 via build_model_state_as_of()
  (B) a model instance previously trained through game 100 -- which must
      NEVER silently produce a historical prediction from future-trained
      state; it must raise ContaminatedModelStateError instead.
"""
import datetime as dt
import unittest

import config
from features import point_in_time as pit
from models.combined_model import (
    CombinedMoneylineModel,
    ContaminatedModelStateError,
    build_model_state_as_of,
)
from tests.helpers import make_test_db, t


def _build_100_game_world(conn):
    """100 sequential games, TOR vs BOS, one every 6 hours starting at
    day 0 -- game_id N's result is observed 3 hours after its own
    scheduled start, and strictly before game_id N+1's own scheduled
    start, so "games known before game N's prediction_time" is exactly
    games 1..N-1."""
    conn.execute("INSERT INTO teams (team_id) VALUES ('TOR')")
    conn.execute("INSERT INTO teams (team_id) VALUES ('BOS')")
    for i in range(1, 4):
        conn.execute("INSERT INTO players (player_id, full_name, position) VALUES (?,?,?)",
                     (f"TOR_F{i}", f"TOR_F{i}", "F"))
        conn.execute("INSERT INTO players (player_id, full_name, position) VALUES (?,?,?)",
                     (f"BOS_F{i}", f"BOS_F{i}", "F"))
    for pid, team in (("TOR_F1", "TOR"), ("TOR_F2", "TOR"), ("TOR_F3", "TOR"),
                      ("BOS_F1", "BOS"), ("BOS_F2", "BOS"), ("BOS_F3", "BOS")):
        conn.execute(
            """INSERT INTO team_membership_events
               (player_id, team_id, effective_at_utc, observed_at_utc, event_type, source)
               VALUES (?,?,?,?,?,?)""",
            (pid, team, t(-30), t(-30), "INITIAL_ROSTER", "test"),
        )
    conn.commit()

    base = dt.datetime.fromisoformat(t(0))
    for game_id in range(1, 101):
        start = base + dt.timedelta(hours=6 * game_id)
        scheduled_start = start.isoformat()
        result_observed = (start + dt.timedelta(hours=3)).isoformat()
        game_date = scheduled_start[:10]
        home_score, away_score = (3, 1) if game_id % 2 == 0 else (1, 4)
        conn.execute(
            """INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team,
                                   away_team, venue, schedule_observed_at_utc, game_state,
                                   home_score, away_score, final_period_type,
                                   result_observed_at_utc, source)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (game_id, "2025-DEMO", game_date, scheduled_start, "TOR", "BOS", "Arena",
             t(-30), "FINAL", home_score, away_score, "REG", result_observed, "test"),
        )
        conn.execute(
            """INSERT INTO game_schedule_events
               (game_id, game_date, scheduled_start_utc, home_team, away_team, venue,
                effective_at_utc, observed_at_utc, source, data_provider)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (game_id, game_date, scheduled_start, "TOR", "BOS", "Arena",
             t(-30), t(-30), "test", "test"),
        )
        # v2.1.1: the append-only result-history row -- authoritative
        # source for features.point_in_time.game_result_as_of() /
        # completed_games_known_before(). The `games` row above is only a
        # current-state convenience cache.
        conn.execute(
            """INSERT INTO game_result_events
               (game_id, home_score, away_score, final_period_type, game_state,
                effective_at_utc, observed_at_utc, revision_number, source, data_provider)
               VALUES (?,?,?,'REG','FINAL',?,?,1,?,?)""",
            (game_id, home_score, away_score, result_observed, result_observed, "test", "test"),
        )
        conn.execute(
            """INSERT INTO player_game_stats
               (game_id, player_id, team_id, toi_minutes, goals, assists, shots, played,
                revision_number, effective_at_utc, observed_at_utc, source)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (game_id, "TOR_F1", "TOR", 18.0, 1 if home_score > away_score else 0, 1,
             4, 1, 1, result_observed, result_observed, "test"),
        )
        conn.commit()
    return [base + dt.timedelta(hours=6 * gid) for gid in range(0, 101)]


GAME_75_PREDICTION_TIME_OFFSET_MIN = 30   # matches prediction_time_for_game's default


def _game_75_prediction_time(conn):
    return CombinedMoneylineModel.prediction_time_for_game(conn, 75)


class TestGameStateContaminationGuard(unittest.TestCase):
    """Path (B): a model instance previously trained through game 100
    must refuse -- loudly -- to produce a game-75 prediction."""

    def setUp(self):
        self.conn, self.path = make_test_db()
        _build_100_game_world(self.conn)
        self.prediction_time_75 = _game_75_prediction_time(self.conn)

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_model_trained_through_100_raises_on_predict_for_game_75(self):
        model = CombinedMoneylineModel(teams=["TOR", "BOS"])
        for gid in pit.completed_games_known_before(self.conn):   # learns 1..100
            model.learn(self.conn, gid)
        self.assertEqual(model.trained_through_observed_at,
                          self.conn.execute(
                              "SELECT result_observed_at_utc FROM games WHERE game_id=100"
                          ).fetchone()[0])

        with self.assertRaises(ContaminatedModelStateError):
            model.predict(self.conn, 75, self.prediction_time_75)

    def test_contamination_error_names_the_offending_timestamps(self):
        model = CombinedMoneylineModel(teams=["TOR", "BOS"])
        for gid in pit.completed_games_known_before(self.conn):
            model.learn(self.conn, gid)
        try:
            model.predict(self.conn, 75, self.prediction_time_75)
            self.fail("expected ContaminatedModelStateError")
        except ContaminatedModelStateError as exc:
            self.assertIn(model.trained_through_observed_at, str(exc))
            self.assertIn(self.prediction_time_75, str(exc))

    def test_model_trained_only_through_74_can_predict_game_75(self):
        # sanity: the guard is time-based, not "any prior training at all"
        model = CombinedMoneylineModel(teams=["TOR", "BOS"])
        for gid in pit.completed_games_known_before(self.conn, self.prediction_time_75):
            model.learn(self.conn, gid)
        pred = model.predict(self.conn, 75, self.prediction_time_75)   # must not raise
        self.assertEqual(pred.game_id, 75)


class TestGame75ReconstructionIsClean(unittest.TestCase):
    """Path (A): build_model_state_as_of() gives a correctly-scoped fresh
    model for game 75, guaranteed uncontaminated by games 76-100."""

    def setUp(self):
        self.conn, self.path = make_test_db()
        _build_100_game_world(self.conn)
        self.prediction_time_75 = _game_75_prediction_time(self.conn)

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_build_model_state_as_of_75_learns_exactly_games_1_through_74(self):
        model = build_model_state_as_of(self.conn, self.prediction_time_75, teams=["TOR", "BOS"])
        self.assertEqual(model._games_played_this_season["TOR"], 74)
        self.assertEqual(model.player_model.games_played("TOR_F1"), 74)
        self.assertEqual(model.goalie_model.sample_size("TOR_F1"), 0)  # no goalie stats seeded

    def test_reconstructed_game_75_prediction_matches_the_originally_persisted_one(self):
        # "generate/persist a prediction for game 75 using only pre-75
        # knowledge" -- the ORIGINAL, uncontaminated way of getting it.
        original_model = build_model_state_as_of(self.conn, self.prediction_time_75,
                                                   teams=["TOR", "BOS"])
        original_pred = original_model.predict(self.conn, 75, self.prediction_time_75)

        # now train an entirely separate model through game 100 (the
        # "future" work happening elsewhere in the system)...
        trained_through_100 = CombinedMoneylineModel(teams=["TOR", "BOS"])
        for gid in pit.completed_games_known_before(self.conn):
            trained_through_100.learn(self.conn, gid)

        # ...and ask the system to reconstruct/rebuild game 75's
        # prediction using the SANCTIONED reconstruction path. It must
        # match the original bit-for-bit, and must NOT be influenced by
        # the fact that some other model instance has since seen games
        # 76-100.
        rebuilt_model = build_model_state_as_of(self.conn, self.prediction_time_75,
                                                  teams=["TOR", "BOS"])
        rebuilt_pred = rebuilt_model.predict(self.conn, 75, self.prediction_time_75)

        self.assertEqual(original_pred.feature_snapshot, rebuilt_pred.feature_snapshot)
        self.assertEqual(original_pred.model_prob_home, rebuilt_pred.model_prob_home)
        self.assertEqual(original_pred.conservative_prob_home, rebuilt_pred.conservative_prob_home)

    def test_game_75_feature_snapshot_excludes_any_trace_of_games_76_through_100(self):
        model = build_model_state_as_of(self.conn, self.prediction_time_75, teams=["TOR", "BOS"])
        pred = model.predict(self.conn, 75, self.prediction_time_75)
        # season_maturity_games is the min of both teams' games-played
        # counters -- if it were >=76 that would prove leakage from the
        # "future" 76-100 games into this reconstruction.
        self.assertEqual(pred.feature_snapshot["season_maturity_games"], 74)

    def test_build_model_state_as_of_100_learns_all_100_and_differs_from_75(self):
        prediction_time_101 = CombinedMoneylineModel.prediction_time_for_game(self.conn, 100)
        # anchor slightly after game 100's own result is observed so it's
        # eligible too
        row = self.conn.execute(
            "SELECT result_observed_at_utc FROM games WHERE game_id=100"
        ).fetchone()
        after_100 = (dt.datetime.fromisoformat(row[0]) + dt.timedelta(minutes=1)).isoformat()

        model_75 = build_model_state_as_of(self.conn, self.prediction_time_75, teams=["TOR", "BOS"])
        model_100 = build_model_state_as_of(self.conn, after_100, teams=["TOR", "BOS"])
        self.assertEqual(model_75._games_played_this_season["TOR"], 74)
        self.assertEqual(model_100._games_played_this_season["TOR"], 100)
        self.assertNotEqual(model_75.elo.ratings["TOR"], model_100.elo.ratings["TOR"])


class TestExactTimestampContaminationSemantics(unittest.TestCase):
    """v2.1.1 spec item 4: completed_games_known_before() (default
    strict=True) uses STRICT-BEFORE semantics -- a result observed at
    EXACTLY prediction_time_utc is NOT eligible. The in-memory
    contamination guard must be consistent with that: a model already
    trained through a result observed at exactly T must be treated as
    contaminated for a prediction AT T, not just after T."""

    def setUp(self):
        from tests.helpers import Fixture, make_test_db, t as _t
        self.t = _t
        self.conn, self.path = make_test_db()
        self.fx = Fixture(self.conn)   # game 1: TOR vs BOS, day 10, 19:00
        self.tie_instant = self.t(9, hour=18, minute=30)
        self.fx.finalize_game(1, home_score=4, away_score=2,
                               result_observed_at=self.tie_instant)
        # a second game, predicted at EXACTLY the same instant game 1's
        # result was first observed: scheduled_start is set 30 minutes
        # after tie_instant so prediction_time_for_game()'s default
        # "30 min before puck drop" anchor lands exactly on tie_instant.
        import datetime as _dt
        self.game_2_scheduled_start = (
            _dt.datetime.fromisoformat(self.tie_instant) + _dt.timedelta(minutes=30)
        ).isoformat()
        self.conn.execute(
            """INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team,
                                   away_team, venue, schedule_observed_at_utc, game_state, source)
               VALUES (2,'2025-DEMO',?,?,?,?,?,?,?,?)""",
            (self.game_2_scheduled_start[:10], self.game_2_scheduled_start, "TOR", "BOS",
             "TOR Arena", self.t(-30), "SCHEDULED", "test"),
        )
        self.conn.execute(
            """INSERT INTO game_schedule_events
               (game_id, game_date, scheduled_start_utc, home_team, away_team, venue,
                effective_at_utc, observed_at_utc, source, data_provider)
               VALUES (2,?,?,?,?,?,?,?,?,?)""",
            (self.game_2_scheduled_start[:10], self.game_2_scheduled_start, "TOR", "BOS",
             "TOR Arena", self.t(-30), self.t(-30), "test", "test"),
        )
        self.conn.commit()
        # game 2 also needs to itself be FINAL (with a result observed
        # comfortably AFTER its own predict time) purely so
        # process_games([2, 1], learn=True) -- which requires every game
        # in the list to be learnable -- can include it; this does not
        # affect the earlier direct predict()/build_model_state_as_of()
        # tests above, which predict game 2 at tie_instant regardless of
        # its game_state.
        self.game_2_result_observed_at = self.t(9, hour=22)
        self.fx.finalize_game(2, home_score=3, away_score=1,
                               result_observed_at=self.game_2_result_observed_at)

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_result_observed_at_exactly_prediction_time_is_not_eligible(self):
        eligible = pit.completed_games_known_before(self.conn, self.tie_instant)
        self.assertNotIn(1, eligible)

    def test_result_observed_strictly_before_prediction_time_is_eligible(self):
        just_after = pit.completed_games_known_before(
            self.conn,
            (dt.datetime.fromisoformat(self.tie_instant) + dt.timedelta(seconds=1)).isoformat(),
        )
        self.assertIn(1, just_after)

    def test_manually_pretrained_model_at_exact_tie_raises_contaminated_error(self):
        # a model instance that has ALREADY learned a result observed at
        # exactly T must refuse to predict AT T -- consistent with T
        # itself never having been eligible to learn that result via the
        # authoritative query above.
        model = CombinedMoneylineModel(teams=["TOR", "BOS"])
        model.learn(self.conn, 1, learn_time_utc=self.tie_instant)
        self.assertEqual(model.trained_through_observed_at, self.tie_instant)
        with self.assertRaises(ContaminatedModelStateError):
            model.predict(self.conn, 2, self.tie_instant)

    def test_build_model_state_as_of_at_the_exact_tie_does_not_learn_game_1(self):
        # the authoritative reconstruction path must independently agree:
        # a model built exactly AT the tie instant must NOT have learned
        # game 1 either (season_maturity_games stays 0, not 1).
        model = build_model_state_as_of(self.conn, self.tie_instant, teams=["TOR", "BOS"])
        pred = model.predict(self.conn, 2, self.tie_instant)
        self.assertEqual(pred.feature_snapshot["season_maturity_games"], 0)

    def test_predict_event_sorts_before_learn_event_at_an_exact_tie_in_process_games(self):
        # spec item 4's required scenario: "prediction event occurs before
        # learn event" at an exact timestamp tie, exercised through the
        # real chronological-merge path (process_games), not just the
        # lower-level guard.
        model = CombinedMoneylineModel(teams=["TOR", "BOS"])
        preds = model.process_games(self.conn, [2, 1], learn=True, store_predictions=False)
        pred_2 = next(p for p in preds if p.game_id == 2)
        # game 2 was predicted at the exact tie instant -- it must NOT
        # reflect game 1 (learned at that same instant) having gone first
        self.assertEqual(pred_2.feature_snapshot["season_maturity_games"], 0)


if __name__ == "__main__":
    unittest.main()
