"""
v2.1.1 spec items 2/3/4: final game results (home_score/away_score/
final_period_type) are now append-only/revision-versioned via
game_result_events, exactly like schedule facts (test_schedule_revision.py)
and player/goalie postgame stats (test_stat_revision.py). A later score
correction must never retroactively change when a prediction claims a
result first became known, nor silently rewrite what a historical model
already learned from it.

Case A (identical reingestion) and Case B (genuine correction) are both
covered, at both the point-in-time-function level and the full
ingest_result()/learn() level -- "equivalent in strength to the existing
player/goalie-stat revision tests" per the spec.
"""
import unittest

from features import point_in_time as pit
from ingest import nhl_api
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


class TestGameResultAsOf(unittest.TestCase):
    def setUp(self):
        self.conn, self.path = make_test_db()
        self.fx = Fixture(self.conn)   # game_id=1, TOR vs BOS, day 10, 19:00

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_original_result_visible_before_any_correction(self):
        self.fx.finalize_game(1, home_score=4, away_score=2,
                               result_observed_at=t(10, hour=22, minute=15))
        result = pit.game_result_as_of(self.conn, 1, t(11))
        self.assertEqual((result["home_score"], result["away_score"]), (4, 2))

    def test_prediction_made_before_a_later_correction_still_sees_the_original(self):
        self.fx.finalize_game(1, home_score=4, away_score=2,
                               result_observed_at=t(10, hour=22, minute=15))
        prediction_time = t(10, hour=23)
        original_as_seen = pit.game_result_as_of(self.conn, 1, prediction_time)

        # next morning: the score is corrected, observed AFTER prediction_time
        self.fx.correct_result(1, home_score=3, away_score=2, observed_at=t(11, hour=8))

        reconstructed = pit.game_result_as_of(self.conn, 1, prediction_time)
        self.assertEqual(dict(reconstructed), dict(original_as_seen))
        self.assertEqual((reconstructed["home_score"], reconstructed["away_score"]), (4, 2))

    def test_prediction_made_after_the_correction_sees_the_new_result(self):
        self.fx.finalize_game(1, home_score=4, away_score=2,
                               result_observed_at=t(10, hour=22, minute=15))
        self.fx.correct_result(1, home_score=3, away_score=2, observed_at=t(11, hour=8))
        result = pit.game_result_as_of(self.conn, 1, t(11, hour=9))
        self.assertEqual((result["home_score"], result["away_score"]), (3, 2))

    def test_no_result_observed_yet_returns_none(self):
        self.assertIsNone(pit.game_result_as_of(self.conn, 1, t(9)))

    def test_first_observed_at_is_immutable_across_corrections(self):
        self.fx.finalize_game(1, home_score=4, away_score=2,
                               result_observed_at=t(10, hour=22, minute=15))
        first_before = pit.game_result_first_observed_at(self.conn, 1)
        self.fx.correct_result(1, home_score=3, away_score=2, observed_at=t(11, hour=8))
        first_after = pit.game_result_first_observed_at(self.conn, 1)
        self.assertEqual(first_before, first_after)
        self.assertEqual(first_before, t(10, hour=22, minute=15))

    def test_multiple_corrections_do_not_move_first_observed_at(self):
        self.fx.finalize_game(1, home_score=4, away_score=2,
                               result_observed_at=t(10, hour=22, minute=15))
        for day, hs, aws in ((11, 3, 2), (12, 5, 2), (13, 4, 2)):
            self.fx.correct_result(1, home_score=hs, away_score=aws, observed_at=t(day, hour=8))
        self.assertEqual(pit.game_result_first_observed_at(self.conn, 1),
                          t(10, hour=22, minute=15))


class TestResultCorrectionLeakageIntoPredictions(unittest.TestCase):
    """The mandated Prediction-B scenario (item 3, Case B), equivalent in
    strength to test_stat_revision.py's: game 1 completes, result observed
    at 22:15; Prediction B (for a later game 2) is generated at 23:00; the
    following morning the score is corrected. Reconstructing Prediction B
    must still learn the ORIGINAL result -- version B did not exist yet."""

    def setUp(self):
        self.conn, self.path = make_test_db()
        self.fx = Fixture(self.conn)   # game 1: TOR vs BOS, day 10, 19:00
        self.fx.finalize_game(1, home_score=4, away_score=2,
                               result_observed_at=t(10, hour=22, minute=15))
        _insert_scheduled_game(self.conn, 2, "TOR", "BOS", date_offset=12)
        self.prediction_b_time = t(10, hour=23)

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def _predict_b(self):
        model = build_model_state_as_of(self.conn, self.prediction_b_time, teams=["TOR", "BOS"])
        return model.predict(self.conn, 2, self.prediction_b_time)

    def test_prediction_b_unchanged_after_result_correction(self):
        original = self._predict_b()

        # 08:00 next morning: provider posts corrected result version B
        self.fx.correct_result(1, home_score=1, away_score=6, observed_at=t(11, hour=8))

        recomputed = self._predict_b()
        self.assertEqual(original.feature_snapshot, recomputed.feature_snapshot)
        self.assertEqual(original.model_prob_home, recomputed.model_prob_home)

    def test_prediction_b_elo_reflects_original_home_win_not_corrected_away_win(self):
        # original: TOR (home) won 4-2. A correction flipping the winner
        # entirely (e.g. 1-6, an away win) must NOT retroactively flip
        # which team Prediction B's Elo update favored.
        import config
        original = self._predict_b()
        self.assertGreater(original.feature_snapshot["elo_home"], config.ELO_START)

        self.fx.correct_result(1, home_score=1, away_score=6, observed_at=t(11, hour=8))
        recomputed = self._predict_b()
        self.assertGreater(recomputed.feature_snapshot["elo_home"], config.ELO_START)
        self.assertEqual(original.feature_snapshot["elo_home"],
                          recomputed.feature_snapshot["elo_home"])

    def test_a_prediction_generated_after_the_correction_uses_the_corrected_result(self):
        # the flip side: a prediction made after 08:00 MAY use version B --
        # proves this isn't "corrections are silently ignored forever",
        # only that they can't leak backward in time. Matches the same
        # "defined revision policy" already established for stat
        # corrections (test_stat_revision.py): the STANDARD walk-forward
        # default (build_model_state_as_of / learn()'s default
        # learn_time_utc) always learns from the version known at the
        # game's own first-observed time -- by design, corrections are
        # never picked up automatically, only via an EXPLICIT later
        # learn_time_utc, which is the "defined revision policy" a caller
        # opts into deliberately (e.g. a scheduled model-state rebuild).
        import config
        self.fx.correct_result(1, home_score=1, away_score=6, observed_at=t(11, hour=8))
        later_time = t(11, hour=9)
        model = CombinedMoneylineModel(teams=["TOR", "BOS"])
        model.learn(self.conn, 1, learn_time_utc=later_time)
        pred = model.predict(self.conn, 2, later_time)
        # TOR (home) LOST 1-6 per the corrected result -- Elo should have
        # moved DOWN, not up, once this correction is legitimately visible
        # to an explicit learn_time_utc at/after when it was observed.
        self.assertLess(pred.feature_snapshot["elo_home"], config.ELO_START)


class TestResultIngestionIdempotency(unittest.TestCase):
    """Exercises ingest/nhl_api.py::ingest_result()'s write path directly
    (test_ingest_idempotency.py covers the `games` cache side of this)."""

    def setUp(self):
        self.conn, self.path = make_test_db()
        self.conn.execute("INSERT OR IGNORE INTO teams (team_id) VALUES ('TOR')")
        self.conn.execute("INSERT OR IGNORE INTO teams (team_id) VALUES ('BOS')")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def _game(self, home_score=4, away_score=2, period_type="REG"):
        return {
            "id": 7000001,
            "homeTeam": {"abbrev": "TOR", "score": home_score},
            "awayTeam": {"abbrev": "BOS", "score": away_score},
            "periodDescriptor": {"periodType": period_type},
        }

    def test_reingesting_identical_result_appends_no_new_revision(self):
        # Case A: 22:15 result observed, following morning the SAME final
        # result is pulled again -- must be an idempotent no-op.
        nhl_api.ingest_result(self.conn, self._game(), observed_at_utc=t(10, hour=22, minute=15))
        nhl_api.ingest_result(self.conn, self._game(), observed_at_utc=t(11, hour=8))
        self.conn.commit()
        rows = self.conn.execute(
            "SELECT * FROM game_result_events WHERE game_id=?", (7000001,)
        ).fetchall()
        self.assertEqual(len(rows), 1)

    def test_reingesting_identical_result_does_not_move_first_known_time(self):
        nhl_api.ingest_result(self.conn, self._game(), observed_at_utc=t(10, hour=22, minute=15))
        nhl_api.ingest_result(self.conn, self._game(), observed_at_utc=t(11, hour=8))
        self.conn.commit()
        self.assertEqual(pit.game_result_first_observed_at(self.conn, 7000001),
                          t(10, hour=22, minute=15))

    def test_genuine_correction_appends_a_new_revision_not_an_overwrite(self):
        # Case B
        nhl_api.ingest_result(self.conn, self._game(home_score=4, away_score=2),
                               observed_at_utc=t(10, hour=22, minute=15))
        nhl_api.ingest_result(self.conn, self._game(home_score=3, away_score=2),
                               observed_at_utc=t(11, hour=8))
        self.conn.commit()
        rows = self.conn.execute(
            "SELECT home_score, away_score, observed_at_utc FROM game_result_events "
            "WHERE game_id=? ORDER BY id", (7000001,)
        ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual((rows[0]["home_score"], rows[0]["away_score"]), (4, 2))
        self.assertEqual((rows[1]["home_score"], rows[1]["away_score"]), (3, 2))

    def test_earlier_prediction_time_still_sees_original_result_after_the_correction(self):
        nhl_api.ingest_result(self.conn, self._game(home_score=4, away_score=2),
                               observed_at_utc=t(10, hour=22, minute=15))
        nhl_api.ingest_result(self.conn, self._game(home_score=3, away_score=2),
                               observed_at_utc=t(11, hour=8))
        self.conn.commit()
        early = pit.game_result_as_of(self.conn, 7000001, t(10, hour=23))
        late = pit.game_result_as_of(self.conn, 7000001, t(11, hour=9))
        self.assertEqual((early["home_score"], early["away_score"]), (4, 2))
        self.assertEqual((late["home_score"], late["away_score"]), (3, 2))

    def test_ingest_result_keeps_updating_the_games_cache_for_convenience(self):
        # the `games` cache row may still reflect the LATEST known result
        # (schema.sql: "current-state cache only") -- only historical
        # reconstruction must avoid it, not the cache itself. Real usage
        # always calls ingest_schedule() first (which creates the `games`
        # row); replicate that minimally here.
        self.conn.execute(
            """INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team,
                                   away_team, schedule_observed_at_utc, game_state, source)
               VALUES (7000001,'2025-DEMO',?,?,?,?,?,?,?)""",
            (t(10)[:10], t(10, hour=19), "TOR", "BOS", t(-30), "SCHEDULED", "test"),
        )
        self.conn.commit()
        nhl_api.ingest_result(self.conn, self._game(home_score=4, away_score=2),
                               observed_at_utc=t(10, hour=22, minute=15))
        nhl_api.ingest_result(self.conn, self._game(home_score=3, away_score=2),
                               observed_at_utc=t(11, hour=8))
        self.conn.commit()
        row = self.conn.execute(
            "SELECT home_score, away_score FROM games WHERE game_id=?", (7000001,)
        ).fetchone()
        self.assertEqual((row["home_score"], row["away_score"]), (3, 2))


class TestCompletedGamesKnownBeforeAcrossCorrections(unittest.TestCase):
    """completed_games_known_before()'s eligibility and ordering must be
    entirely unaffected by a later score correction -- it is derived from
    game_result_events' first-observed time, never a mutable cache
    column or a later revision's timestamp."""

    def setUp(self):
        self.conn, self.path = make_test_db()
        self.fx = Fixture(self.conn)   # game 1
        self.fx.finalize_game(1, home_score=4, away_score=2,
                               result_observed_at=t(10, hour=22, minute=15))

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_eligibility_at_a_fixed_prediction_time_unaffected_by_later_correction(self):
        prediction_time = t(10, hour=23)
        before = pit.completed_games_known_before(self.conn, prediction_time)
        self.fx.correct_result(1, home_score=1, away_score=6, observed_at=t(11, hour=8))
        after = pit.completed_games_known_before(self.conn, prediction_time)
        self.assertEqual(before, after)
        self.assertIn(1, before)

    def test_all_final_game_ids_ordering_unaffected_by_later_correction(self):
        before = CombinedMoneylineModel.all_final_game_ids(self.conn)
        self.fx.correct_result(1, home_score=1, away_score=6, observed_at=t(11, hour=8))
        after = CombinedMoneylineModel.all_final_game_ids(self.conn)
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
