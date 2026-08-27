"""
v2.1.1a spec item 2: CombinedMoneylineModel.learn() supports an explicit
`learn_time_utc` override that legitimately lets a caller consume a LATER
result/player-stat/goalie-stat correction (see learn()'s own docstring --
this is a deliberate, existing policy, not something this slice changes).
The bug: after consuming that later information, the model updated
`trained_through_observed_at` using the game's FIRST result-observed
timestamp instead of the latest observation actually consumed --
understating how far forward the model's knowledge extends and letting a
model that has genuinely absorbed a Tuesday-morning correction still be
used to predict Monday night, undetected.

These tests exercise the exact spec scenario for each of the three
correctable fact types (result, player stat, goalie stat): consume a
later revision via an explicit learn_time_utc, then prove the model
refuses to predict backward across the correction's own observation
time. A companion test proves a FRESH build_model_state_as_of()
reconstruction is entirely unaffected -- this is a per-instance
watermark, not a global rewrite of history (see spec item 6: "do not
implement automatic correction propagation as a new global policy").
"""
import unittest

from models.combined_model import (
    CombinedMoneylineModel,
    ContaminatedModelStateError,
    build_model_state_as_of,
)
from tests.helpers import Fixture, make_test_db, t


def _insert_scheduled_game(conn, game_id, home, away, date_offset):
    """A second, still-SCHEDULED game to serve as the subject of the
    backward prediction attempt -- must exist in both `games` and the
    point-in-time schedule history."""
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


class TestCorrectedResultContaminatesTheWatermark(unittest.TestCase):
    """Test A (spec item 2): a result correction consumed via an explicit
    learn_time_utc must move the watermark forward, so a backward
    prediction at the game's ORIGINAL (pre-correction) result time is
    correctly refused."""

    def setUp(self):
        self.conn, self.path = make_test_db()
        self.fx = Fixture(self.conn)   # game 1: TOR vs BOS, day 10, 19:00
        # Monday 22:00 -- original result first observed
        self.fx.finalize_game(1, home_score=4, away_score=2,
                               result_observed_at=t(10, hour=22))
        # Tuesday 08:00 -- corrected result observed
        self.fx.correct_result(1, home_score=3, away_score=2, observed_at=t(11, hour=8))
        _insert_scheduled_game(self.conn, 2, "TOR", "BOS", date_offset=12)

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_explicitly_consuming_the_correction_raises_on_a_prediction_before_it(self):
        model = CombinedMoneylineModel(teams=["TOR", "BOS"])
        # Tuesday 09:00 -- explicit learn_time_utc consumes the correction
        model.learn(self.conn, 1, learn_time_utc=t(11, hour=9))
        # the watermark must now reflect Tuesday 08:00 (the correction's
        # own observed_at_utc), not Monday 22:00 (the original).
        self.assertEqual(model.trained_through_observed_at, t(11, hour=8))
        # Monday 23:00 -- BEFORE the correction was ever observed
        with self.assertRaises(ContaminatedModelStateError):
            model.predict(self.conn, 2, t(10, hour=23))

    def test_without_the_explicit_override_the_watermark_is_unaffected(self):
        # sanity check / non-regression: ordinary default-learn_time_utc
        # behavior (the common case, exercised by 200+ other tests) must
        # be completely unchanged by this fix -- the watermark still
        # equals the game's first-observed result time when no explicit
        # later learn_time_utc is ever passed, and a Monday-night
        # prediction remains valid.
        model = CombinedMoneylineModel(teams=["TOR", "BOS"])
        model.learn(self.conn, 1)   # default learn_time_utc
        self.assertEqual(model.trained_through_observed_at, t(10, hour=22))
        pred = model.predict(self.conn, 2, t(10, hour=23))
        self.assertIsNotNone(pred)


class TestCorrectedPlayerStatContaminatesTheWatermark(unittest.TestCase):
    """Test B (spec item 2): same requirement, for a player-stat
    revision consumed via an explicit learn_time_utc."""

    def setUp(self):
        self.conn, self.path = make_test_db()
        self.fx = Fixture(self.conn)
        self.fx.finalize_game(1, home_score=4, away_score=2,
                               result_observed_at=t(10, hour=22))
        self.fx.add_player_stat(1, "TOR_F1", "TOR", goals=2, assists=1,
                                 observed_at=t(10, hour=22))
        # Tuesday 08:00 -- corrected player stat observed
        self.fx.add_player_stat(1, "TOR_F1", "TOR", goals=2, assists=3,
                                 observed_at=t(11, hour=8), revision_number=2)
        _insert_scheduled_game(self.conn, 2, "TOR", "BOS", date_offset=12)

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_explicitly_consuming_the_stat_correction_raises_on_a_prediction_before_it(self):
        model = CombinedMoneylineModel(teams=["TOR", "BOS"])
        model.learn(self.conn, 1, learn_time_utc=t(11, hour=9))
        self.assertEqual(model.trained_through_observed_at, t(11, hour=8))
        with self.assertRaises(ContaminatedModelStateError):
            model.predict(self.conn, 2, t(10, hour=23))

    def test_default_learn_time_utc_only_sees_the_original_stat_and_is_unaffected(self):
        model = CombinedMoneylineModel(teams=["TOR", "BOS"])
        model.learn(self.conn, 1)   # default -- must not see the Tuesday revision
        self.assertEqual(model.trained_through_observed_at, t(10, hour=22))
        pred = model.predict(self.conn, 2, t(10, hour=23))
        self.assertIsNotNone(pred)


class TestCorrectedGoalieStatContaminatesTheWatermark(unittest.TestCase):
    """Test C (spec item 2): same requirement, for a goalie-stat
    revision consumed via an explicit learn_time_utc."""

    def setUp(self):
        self.conn, self.path = make_test_db()
        self.fx = Fixture(self.conn)
        self.fx.finalize_game(1, home_score=4, away_score=2,
                               result_observed_at=t(10, hour=22))
        self.fx.add_goalie_stat(1, "TOR_G1", "TOR", saves=30, shots_against=32,
                                 observed_at=t(10, hour=22))
        # Tuesday 08:00 -- corrected goalie stat observed
        self.fx.add_goalie_stat(1, "TOR_G1", "TOR", saves=28, shots_against=32,
                                 observed_at=t(11, hour=8), revision_number=2)
        _insert_scheduled_game(self.conn, 2, "TOR", "BOS", date_offset=12)

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_explicitly_consuming_the_goalie_correction_raises_on_a_prediction_before_it(self):
        model = CombinedMoneylineModel(teams=["TOR", "BOS"])
        model.learn(self.conn, 1, learn_time_utc=t(11, hour=9))
        self.assertEqual(model.trained_through_observed_at, t(11, hour=8))
        with self.assertRaises(ContaminatedModelStateError):
            model.predict(self.conn, 2, t(10, hour=23))

    def test_default_learn_time_utc_only_sees_the_original_goalie_stat_and_is_unaffected(self):
        model = CombinedMoneylineModel(teams=["TOR", "BOS"])
        model.learn(self.conn, 1)
        self.assertEqual(model.trained_through_observed_at, t(10, hour=22))
        pred = model.predict(self.conn, 2, t(10, hour=23))
        self.assertIsNotNone(pred)


class TestScheduleRevisionContaminatesTheWatermark(unittest.TestCase):
    """v2.1.2 spec item 3: learn() also consumes `sched` (via
    pit.game_schedule_as_of(), for home/away resolution -- spec v2.1.1a
    item 4/Policy A), but the v2.1.1a watermark fix (item 2, above) never
    folded sched["observed_at_utc"] into knowledge_through_utc -- leaving
    exactly the same leakage class item 2 was supposed to close: a model
    that explicitly consumed a Tuesday home/away schedule correction
    could still (incorrectly) be allowed to predict Monday night."""

    def setUp(self):
        self.conn, self.path = make_test_db()
        self.fx = Fixture(self.conn)   # game 1: TOR home vs BOS away, day 10, 19:00
        self.fx.finalize_game(1, home_score=4, away_score=2,
                               result_observed_at=t(10, hour=22))
        # Tuesday 08:00 -- a home/away schedule correction observed
        # (swap): BOS becomes home, TOR becomes away.
        self.fx.revise_schedule(1, effective_at=t(11, hour=8),
                                 home_team="BOS", away_team="TOR")
        _insert_scheduled_game(self.conn, 2, "TOR", "BOS", date_offset=12)

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_explicitly_consuming_the_schedule_revision_raises_on_a_prediction_before_it(self):
        model = CombinedMoneylineModel(teams=["TOR", "BOS"])
        # Tuesday 09:00 -- explicit learn_time_utc consumes the revised
        # schedule (Tuesday 08:00 <= Tuesday 09:00).
        model.learn(self.conn, 1, learn_time_utc=t(11, hour=9))
        # the watermark must now reflect Tuesday 08:00 (the schedule
        # revision's own observed_at_utc) -- LATER than the result's
        # first-observed time (Monday 22:00) -- not Monday 22:00.
        self.assertEqual(model.trained_through_observed_at, t(11, hour=8))
        # Monday 23:00 -- BEFORE the schedule revision was ever observed
        with self.assertRaises(ContaminatedModelStateError):
            model.predict(self.conn, 2, t(10, hour=23))

    def test_without_the_explicit_override_the_watermark_is_unaffected(self):
        # default learn_time_utc = game_result_first_observed_at() =
        # Monday 22:00, which is BEFORE the Tuesday schedule revision --
        # so sched (resolved as of Monday 22:00) is still the fixture's
        # ORIGINAL TOR-home/BOS-away assignment, and the watermark is
        # exactly the game's first-observed result time, same as before
        # this fix, for the ordinary (no override) case.
        model = CombinedMoneylineModel(teams=["TOR", "BOS"])
        model.learn(self.conn, 1)
        self.assertEqual(model.trained_through_observed_at, t(10, hour=22))
        pred = model.predict(self.conn, 2, t(10, hour=23))
        self.assertIsNotNone(pred)

    def test_fresh_reconstruction_before_the_revision_remains_valid(self):
        # a separate instance may have already explicitly consumed
        # Tuesday's schedule correction (see the first test above) --
        # that must have zero effect on an independently-reconstructed
        # instance here, scoped to Monday 23:00.
        contaminated = CombinedMoneylineModel(teams=["TOR", "BOS"])
        contaminated.learn(self.conn, 1, learn_time_utc=t(11, hour=9))
        with self.assertRaises(ContaminatedModelStateError):
            contaminated.predict(self.conn, 2, t(10, hour=23))

        fresh = build_model_state_as_of(self.conn, t(10, hour=23), teams=["TOR", "BOS"])
        pred = fresh.predict(self.conn, 2, t(10, hour=23))
        self.assertIsNotNone(pred)
        self.assertEqual(pred.feature_snapshot["season_maturity_games"], 1)


class TestFreshReconstructionIsUnaffectedByAnotherInstancesWatermark(unittest.TestCase):
    """Test D (spec item 2): this is a per-model-instance watermark, not
    a global rewrite of history -- a FRESH build_model_state_as_of() must
    remain entirely valid and use only Monday-known state, regardless of
    what any other (explicitly correction-consuming) model instance has
    done. Explicitly proves spec item 6 ("do not implement automatic
    correction propagation as a new global policy") holds: corrections
    are consumed only when a caller opts in via an explicit
    learn_time_utc on that one instance, never automatically."""

    def setUp(self):
        self.conn, self.path = make_test_db()
        self.fx = Fixture(self.conn)
        self.fx.finalize_game(1, home_score=4, away_score=2,
                               result_observed_at=t(10, hour=22))
        self.fx.correct_result(1, home_score=3, away_score=2, observed_at=t(11, hour=8))
        _insert_scheduled_game(self.conn, 2, "TOR", "BOS", date_offset=12)

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_fresh_build_model_state_as_of_monday_night_remains_valid(self):
        # a separate instance elsewhere may have already explicitly
        # consumed Tuesday's correction (see the tests above) -- that
        # must have zero effect on an independently-reconstructed
        # instance here.
        contaminated = CombinedMoneylineModel(teams=["TOR", "BOS"])
        contaminated.learn(self.conn, 1, learn_time_utc=t(11, hour=9))
        with self.assertRaises(ContaminatedModelStateError):
            contaminated.predict(self.conn, 2, t(10, hour=23))

        fresh = build_model_state_as_of(self.conn, t(10, hour=23), teams=["TOR", "BOS"])
        pred = fresh.predict(self.conn, 2, t(10, hour=23))
        self.assertIsNotNone(pred)
        # Monday-known state only: exactly one game learned (game 1's
        # ORIGINAL result, first observed Monday 22:00 -- strictly before
        # the correction ever existed).
        self.assertEqual(pred.feature_snapshot["season_maturity_games"], 1)


if __name__ == "__main__":
    unittest.main()
