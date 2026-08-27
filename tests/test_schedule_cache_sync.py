"""
v2.1.2 spec item 4: Policy A (schedule identity is revision-capable) means
`game_schedule_events` is the point-in-time-safe source of truth, and
`games` is documented (schema.sql) as a latest-known CONVENIENCE CACHE
only. But the real ingest_schedule()'s ON CONFLICT UPDATE SET used to
only refresh `scheduled_start_utc`/`venue` -- NOT `game_date`/`home_team`/
`away_team` -- so a genuine home/away (or date) revision could leave the
`games` cache silently disagreeing with the latest game_schedule_events
row it's supposed to mirror. v2.1.1a's home/away model-learning fix
already protects PREDICTION correctness (learn()/predict() both read
through game_schedule_as_of(), never the cache) -- but the database
contract itself was still inconsistent, and anything that DID read the
cache directly (any future report, integration, or manual query) could
be silently wrong.

ingest_schedule()'s ON CONFLICT clause now also updates game_date/
home_team/away_team, so a real re-ingestion keeps the cache synchronized
with the latest observed schedule state. These tests exercise the change
through the REAL ingest/nhl_api.py::ingest_schedule() -- not a hand-built
fixture helper -- for both the home/away-swap case and an ordinary
venue/start-time-only revision.
"""
import unittest

from features import point_in_time as pit
from ingest import nhl_api
from tests.helpers import make_test_db


def _game(game_id, home, away, start, venue, game_date):
    return {
        "id": game_id,
        "season": "20252026",
        "gameDate": game_date,
        "startTimeUTC": start,
        "homeTeam": {"abbrev": home},
        "awayTeam": {"abbrev": away},
        "venue": {"default": venue},
    }


class TestRealIngestScheduleKeepsTheCacheSynchronizedOnAHomeAwaySwap(unittest.TestCase):
    def setUp(self):
        self.conn, self.path = make_test_db()
        self.game_id = 8100001
        original = _game(self.game_id, "TOR", "BOS", "2025-01-10T19:00:00",
                          "TOR Arena", "2025-01-10")
        nhl_api.ingest_schedule(self.conn, original, observed_at_utc="2025-01-01T00:00:00")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_pit_before_the_revision_sees_the_original_assignment(self):
        sched = pit.game_schedule_as_of(self.conn, self.game_id, "2025-01-05T00:00:00")
        self.assertEqual((sched["home_team"], sched["away_team"]), ("TOR", "BOS"))

    def test_reingesting_a_swapped_payload_updates_the_cache_to_match(self):
        swapped = _game(self.game_id, "BOS", "TOR", "2025-01-10T20:30:00",
                         "Neutral Site Arena", "2025-01-10")
        nhl_api.ingest_schedule(self.conn, swapped, observed_at_utc="2025-01-06T00:00:00")
        self.conn.commit()
        cache_row = self.conn.execute(
            "SELECT home_team, away_team, scheduled_start_utc, venue FROM games WHERE game_id=?",
            (self.game_id,),
        ).fetchone()
        self.assertEqual(cache_row["home_team"], "BOS")
        self.assertEqual(cache_row["away_team"], "TOR")
        self.assertEqual(cache_row["scheduled_start_utc"], "2025-01-10T20:30:00")
        self.assertEqual(cache_row["venue"], "Neutral Site Arena")

    def test_pit_after_the_revision_sees_the_swapped_assignment(self):
        swapped = _game(self.game_id, "BOS", "TOR", "2025-01-10T20:30:00",
                         "Neutral Site Arena", "2025-01-10")
        nhl_api.ingest_schedule(self.conn, swapped, observed_at_utc="2025-01-06T00:00:00")
        self.conn.commit()
        sched = pit.game_schedule_as_of(self.conn, self.game_id, "2025-01-07T00:00:00")
        self.assertEqual((sched["home_team"], sched["away_team"]), ("BOS", "TOR"))
        # and PIT still correctly reconstructs the ORIGINAL assignment for
        # any prediction time before the revision was observed.
        sched_before = pit.game_schedule_as_of(self.conn, self.game_id, "2025-01-05T00:00:00")
        self.assertEqual((sched_before["home_team"], sched_before["away_team"]), ("TOR", "BOS"))

    def test_schedule_observed_at_utc_remains_the_first_observed_time(self):
        swapped = _game(self.game_id, "BOS", "TOR", "2025-01-10T20:30:00",
                         "Neutral Site Arena", "2025-01-10")
        nhl_api.ingest_schedule(self.conn, swapped, observed_at_utc="2025-01-06T00:00:00")
        self.conn.commit()
        row = self.conn.execute(
            "SELECT schedule_observed_at_utc FROM games WHERE game_id=?", (self.game_id,)
        ).fetchone()
        self.assertEqual(row["schedule_observed_at_utc"], "2025-01-01T00:00:00")

    def test_cache_and_latest_schedule_history_row_never_disagree(self):
        swapped = _game(self.game_id, "BOS", "TOR", "2025-01-10T20:30:00",
                         "Neutral Site Arena", "2025-01-10")
        nhl_api.ingest_schedule(self.conn, swapped, observed_at_utc="2025-01-06T00:00:00")
        self.conn.commit()
        cache_row = self.conn.execute(
            "SELECT game_date, scheduled_start_utc, home_team, away_team, venue "
            "FROM games WHERE game_id=?", (self.game_id,),
        ).fetchone()
        latest_history_row = self.conn.execute(
            """SELECT game_date, scheduled_start_utc, home_team, away_team, venue
               FROM game_schedule_events WHERE game_id=?
               ORDER BY observed_at_utc DESC, id DESC LIMIT 1""",
            (self.game_id,),
        ).fetchone()
        self.assertEqual(tuple(cache_row), tuple(latest_history_row))


class TestNormalVenueOrStartTimeRevisionStillWorksThroughRealIngestSchedule(unittest.TestCase):
    """Regression guard: the unrelated, already-correct venue/start-time-
    only revision path must continue to work exactly as before -- this
    fix must not disturb it."""

    def setUp(self):
        self.conn, self.path = make_test_db()
        self.game_id = 8100002
        original = _game(self.game_id, "TOR", "BOS", "2025-01-10T19:00:00",
                          "TOR Arena", "2025-01-10")
        nhl_api.ingest_schedule(self.conn, original, observed_at_utc="2025-01-01T00:00:00")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_start_time_and_venue_only_revision_updates_cache_home_away_unchanged(self):
        pushed_back = _game(self.game_id, "TOR", "BOS", "2025-01-10T20:00:00",
                             "TOR Arena (renovated)", "2025-01-10")
        nhl_api.ingest_schedule(self.conn, pushed_back, observed_at_utc="2025-01-03T00:00:00")
        self.conn.commit()
        cache_row = self.conn.execute(
            "SELECT home_team, away_team, scheduled_start_utc, venue FROM games WHERE game_id=?",
            (self.game_id,),
        ).fetchone()
        self.assertEqual(cache_row["home_team"], "TOR")
        self.assertEqual(cache_row["away_team"], "BOS")
        self.assertEqual(cache_row["scheduled_start_utc"], "2025-01-10T20:00:00")
        self.assertEqual(cache_row["venue"], "TOR Arena (renovated)")


if __name__ == "__main__":
    unittest.main()
