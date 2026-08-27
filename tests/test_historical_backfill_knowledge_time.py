"""
v2.1.2 spec item 6: ingest_range() (and ingest_schedule()/ingest_result()
individually) stamp observed_at_utc as the moment THIS SYSTEM actually
ingests a fact -- never the moment the historical event itself happened.
That is the deliberately honest LIVE_OBSERVATION/HISTORICAL_BACKFILL
semantic (see ingest/nhl_api.py::ingest_range()'s docstring and README's
"LIVE_OBSERVATION vs. HISTORICAL_BACKFILL" section), not a simplification
to "fix" later by backdating observed_at_utc to game/schedule date --
doing that would FABRICATE historical knowledge availability and quietly
break the "what was genuinely known at T" guarantee every point-in-time
read in this codebase depends on.

This is a REGRESSION GUARD, not a bug fix: it proves, through the REAL
ingest/nhl_api.py functions, that backfilling a historical game "today"
does NOT make it visible to a point-in-time read anchored before today's
own ingestion moment -- even though the game itself happened long before
that anchor. This protects against someone later "fixing" a backtest by
weakening this exact discipline.
"""
import unittest

from features import point_in_time as pit
from ingest import nhl_api
from tests.helpers import make_test_db


class TestBackfillDoesNotFabricateHistoricalKnowledge(unittest.TestCase):
    def setUp(self):
        self.conn, self.path = make_test_db()
        # a REAL past NHL game -- played nearly three years before "today"
        self.game_id = 8200001
        self.historical_game = {
            "id": self.game_id,
            "season": "20222023",
            "gameDate": "2022-11-01",
            "startTimeUTC": "2022-11-01T19:00:00",
            "homeTeam": {"abbrev": "EDM", "score": 4},
            "awayTeam": {"abbrev": "VGK", "score": 2},
            "venue": {"default": "Rogers Place"},
            "periodDescriptor": {"periodType": "REG"},
            "gameState": "FINAL",
        }
        # "today" -- the moment THIS backfill run actually happens,
        # entirely unrelated to when the game was played.
        self.ingestion_time = "2025-08-26T00:00:00"

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_backfilled_schedule_is_not_visible_before_the_ingestion_moment(self):
        nhl_api.ingest_schedule(self.conn, self.historical_game,
                                 observed_at_utc=self.ingestion_time)
        self.conn.commit()
        # a "prediction" anchored the day before the backfill ran --
        # chronologically AFTER the 2022 game itself, but before this
        # system ever learned about it -- must see nothing.
        before_ingestion = "2025-08-25T00:00:00"
        self.assertIsNone(pit.game_schedule_as_of(self.conn, self.game_id, before_ingestion))

    def test_backfilled_result_is_not_visible_before_the_ingestion_moment(self):
        nhl_api.ingest_schedule(self.conn, self.historical_game,
                                 observed_at_utc=self.ingestion_time)
        nhl_api.ingest_result(self.conn, self.historical_game,
                               observed_at_utc=self.ingestion_time)
        self.conn.commit()
        before_ingestion = "2025-08-25T00:00:00"
        self.assertIsNone(pit.game_result_as_of(self.conn, self.game_id, before_ingestion))

    def test_backfilled_result_first_observed_at_is_the_ingestion_time_not_the_game_date(self):
        nhl_api.ingest_schedule(self.conn, self.historical_game,
                                 observed_at_utc=self.ingestion_time)
        nhl_api.ingest_result(self.conn, self.historical_game,
                               observed_at_utc=self.ingestion_time)
        self.conn.commit()
        first_observed = pit.game_result_first_observed_at(self.conn, self.game_id)
        self.assertEqual(first_observed, self.ingestion_time)
        self.assertNotEqual(first_observed, "2022-11-01T19:00:00")

    def test_backfilled_facts_ARE_visible_at_or_after_the_ingestion_moment(self):
        nhl_api.ingest_schedule(self.conn, self.historical_game,
                                 observed_at_utc=self.ingestion_time)
        nhl_api.ingest_result(self.conn, self.historical_game,
                               observed_at_utc=self.ingestion_time)
        self.conn.commit()
        self.assertIsNotNone(
            pit.game_schedule_as_of(self.conn, self.game_id, self.ingestion_time))
        self.assertIsNotNone(
            pit.game_result_as_of(self.conn, self.game_id, self.ingestion_time))

    def test_this_game_would_be_excluded_from_a_pre_ingestion_training_eligibility_query(self):
        nhl_api.ingest_schedule(self.conn, self.historical_game,
                                 observed_at_utc=self.ingestion_time)
        nhl_api.ingest_result(self.conn, self.historical_game,
                               observed_at_utc=self.ingestion_time)
        self.conn.commit()
        before_ingestion = "2025-08-25T00:00:00"
        eligible = pit.completed_games_known_before(self.conn, before_ingestion, strict=True)
        self.assertNotIn(self.game_id, eligible)
        eligible_after = pit.completed_games_known_before(self.conn, self.ingestion_time,
                                                            strict=False)
        self.assertIn(self.game_id, eligible_after)


if __name__ == "__main__":
    unittest.main()
