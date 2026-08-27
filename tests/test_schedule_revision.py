"""
v2.1 spec items 3/4/5: schedule facts (game_date, scheduled_start_utc,
venue, home/away) are append-only via game_schedule_events -- a later
correction must never rewrite what an earlier prediction reconstructs,
and reingesting an unchanged schedule state must stay idempotent.
"""
import unittest

from features import point_in_time as pit
from ingest import nhl_api
from models.combined_model import CombinedMoneylineModel
from tests.helpers import Fixture, make_test_db, t


class TestScheduleAsOf(unittest.TestCase):
    def setUp(self):
        self.conn, self.path = make_test_db()
        self.fx = Fixture(self.conn)   # game_id=1, 19:00 day 10, TOR Arena

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_original_schedule_visible_before_any_revision(self):
        sched = pit.game_schedule_as_of(self.conn, 1, t(5))
        self.assertEqual(sched["scheduled_start_utc"], self.fx.scheduled_start)
        self.assertEqual(sched["venue"], "TOR Arena")

    def test_prediction_made_before_a_later_revision_still_sees_the_original(self):
        # October 1 equivalent: original schedule (already set up by
        # Fixture). October 5 equivalent: a prediction is generated.
        prediction_time = t(5)
        original_as_seen = pit.game_schedule_as_of(self.conn, 1, prediction_time)

        # October 7 equivalent: NHL revises the game to a new time/venue,
        # observed at day 7 -- AFTER the October-5 prediction.
        self.fx.revise_schedule(1, effective_at=t(7), observed_at=t(7),
                                 scheduled_start_utc=t(10, hour=20, minute=30),
                                 venue="Neutral Site Arena")

        # Reconstructing the October-5 prediction's view must be IDENTICAL
        # to before the revision existed.
        reconstructed = pit.game_schedule_as_of(self.conn, 1, prediction_time)
        self.assertEqual(reconstructed, original_as_seen)
        self.assertEqual(reconstructed["scheduled_start_utc"], self.fx.scheduled_start)
        self.assertEqual(reconstructed["venue"], "TOR Arena")

    def test_prediction_made_after_the_revision_sees_the_new_schedule(self):
        self.fx.revise_schedule(1, effective_at=t(7), observed_at=t(7),
                                 scheduled_start_utc=t(10, hour=20, minute=30),
                                 venue="Neutral Site Arena")
        sched = pit.game_schedule_as_of(self.conn, 1, t(8))
        self.assertEqual(sched["scheduled_start_utc"], t(10, hour=20, minute=30))
        self.assertEqual(sched["venue"], "Neutral Site Arena")

    def test_no_schedule_event_observed_yet_returns_none(self):
        self.conn.execute("INSERT INTO teams (team_id) VALUES ('XXX')")
        self.conn.execute(
            """INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team,
                                   away_team, schedule_observed_at_utc, game_state, source)
               VALUES (99,'2025-DEMO',?,?,?,?,?,?,?)""",
            (t(20)[:10], t(20, hour=19), "TOR", "XXX", t(15), "SCHEDULED", "test"),
        )
        self.conn.commit()
        # a schedule_events row was never inserted for game 99 -- as of a
        # time before it would have been observed, it must resolve to None
        self.assertIsNone(pit.game_schedule_as_of(self.conn, 99, t(5)))


class TestScheduleAffectsRestFeatures(unittest.TestCase):
    """Item 4's concrete downstream consequence: a schedule revision to
    an OTHER game must not change a rest-context calculation that had
    already been (or would have been) made before the revision."""

    def setUp(self):
        self.conn, self.path = make_test_db()
        self.fx = Fixture(self.conn)   # game 1: TOR vs BOS, day 10, 19:00

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_rescheduling_a_prior_game_later_does_not_change_an_earlier_rest_calc(self):
        # a second game for TOR, originally day 8 (close, back-to-back-ish)
        self.conn.execute("INSERT INTO teams (team_id) VALUES ('OTT')")
        self.conn.execute(
            """INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team,
                                   away_team, schedule_observed_at_utc, game_state, source)
               VALUES (2,'2025-DEMO',?,?,?,?,?,?,?)""",
            (t(8)[:10], t(8, hour=19), "TOR", "OTT", t(-90), "SCHEDULED", "test"),
        )
        self.conn.execute(
            """INSERT INTO game_schedule_events
               (game_id, game_date, scheduled_start_utc, home_team, away_team, venue,
                effective_at_utc, observed_at_utc, source, data_provider)
               VALUES (2,?,?,?,?,?,?,?,?,?)""",
            (t(8)[:10], t(8, hour=19), "TOR", "OTT", None, t(-90), t(-90), "test", "test"),
        )
        self.conn.commit()

        prediction_time = t(9, hour=18)
        before = pit.rest_context(self.conn, 1, "TOR", prediction_time)
        self.assertEqual(before["rest_days"], 2)   # day8 -> day10

        # game 2 gets POSTPONED to day 4 instead, observed AFTER our
        # prediction_time (day 12) -- must not retroactively change the
        # already-made rest calculation above
        self.conn.execute(
            """INSERT INTO game_schedule_events
               (game_id, game_date, scheduled_start_utc, home_team, away_team, venue,
                effective_at_utc, observed_at_utc, source, data_provider)
               VALUES (2,?,?,?,?,?,?,?,?,?)""",
            (t(4)[:10], t(4, hour=19), "TOR", "OTT", None, t(12), t(12), "test", "test"),
        )
        self.conn.commit()

        after = pit.rest_context(self.conn, 1, "TOR", prediction_time)
        self.assertEqual(after, before)


class TestScheduleIngestionIdempotency(unittest.TestCase):
    """Re-exercises ingest/nhl_api.py's schedule write path specifically
    for the append-only game_schedule_events history (test_ingest_idempotency.py
    already covers the `games` cache side of this)."""

    def setUp(self):
        self.conn, self.path = make_test_db()
        self.conn.execute("INSERT OR IGNORE INTO teams (team_id) VALUES ('TOR')")
        self.conn.execute("INSERT OR IGNORE INTO teams (team_id) VALUES ('BOS')")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def _game(self, start="2025-01-10T19:00:00", venue="TOR Arena"):
        return {
            "id": 7000001, "season": "20252026", "gameDate": "2025-01-10",
            "startTimeUTC": start, "homeTeam": {"abbrev": "TOR"},
            "awayTeam": {"abbrev": "BOS"}, "venue": {"default": venue},
        }

    def test_reingesting_identical_schedule_appends_no_new_event(self):
        game = self._game()
        nhl_api.ingest_schedule(self.conn, game, observed_at_utc=t(0))
        nhl_api.ingest_schedule(self.conn, game, observed_at_utc=t(1))
        self.conn.commit()
        rows = self.conn.execute(
            "SELECT * FROM game_schedule_events WHERE game_id=?", (game["id"],)
        ).fetchall()
        self.assertEqual(len(rows), 1)

    def test_genuine_change_appends_a_new_event_not_an_overwrite(self):
        game = self._game(venue="TOR Arena")
        nhl_api.ingest_schedule(self.conn, game, observed_at_utc=t(0))
        moved = self._game(venue="Neutral Site Arena")
        nhl_api.ingest_schedule(self.conn, moved, observed_at_utc=t(5))
        self.conn.commit()
        rows = self.conn.execute(
            "SELECT venue, observed_at_utc FROM game_schedule_events "
            "WHERE game_id=? ORDER BY id", (game["id"],)
        ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["venue"], "TOR Arena")
        self.assertEqual(rows[1]["venue"], "Neutral Site Arena")

    def test_earlier_prediction_time_still_sees_original_venue_after_the_change(self):
        game = self._game(venue="TOR Arena")
        nhl_api.ingest_schedule(self.conn, game, observed_at_utc=t(0))
        moved = self._game(venue="Neutral Site Arena")
        nhl_api.ingest_schedule(self.conn, moved, observed_at_utc=t(5))
        self.conn.commit()

        from features import point_in_time as pit_mod
        early = pit_mod.game_schedule_as_of(self.conn, game["id"], t(2))
        late = pit_mod.game_schedule_as_of(self.conn, game["id"], t(6))
        self.assertEqual(early["venue"], "TOR Arena")
        self.assertEqual(late["venue"], "Neutral Site Arena")


if __name__ == "__main__":
    unittest.main()
