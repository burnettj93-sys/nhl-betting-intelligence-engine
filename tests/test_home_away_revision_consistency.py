"""
v2.1.1a spec item 4 (Policy A -- game home/away is revision-capable):
schema.sql explicitly documents `games.home_team`/`away_team` as a
latest-known CACHE ONLY, and `game_schedule_events` (the append-only
schedule history) is revision-capable for exactly these fields, same as
venue/start time -- the schema already contemplates a home/away change.
CombinedMoneylineModel.learn() used to read home/away straight from the
mutable `games` cache row, on the stated assumption that "home/away
identity is stable and never revised." That assumption conflicted with
the schema: a schedule revision changing home/away could leave the
point-in-time PREDICTION using one team assignment
(pit.game_schedule_as_of, via _build_feature_snapshot) while LEARNING
used a different one (the stale/updated `games` cache), letting Elo
credit the final result to the wrong team.

learn() now resolves home/away the same way predict() does -- through
pit.game_schedule_as_of(conn, game_id, learn_time_utc) -- so schedule
history and learned model state can never disagree.
"""
import unittest

from features import point_in_time as pit
from models.combined_model import CombinedMoneylineModel
from tests.helpers import make_test_db, t


def _insert_game_with_schedule(conn, game_id, home, away, scheduled_start,
                                schedule_observed_at, game_date=None):
    game_date = game_date or scheduled_start[:10]
    conn.execute(
        """INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team,
                               away_team, venue, schedule_observed_at_utc, game_state, source)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (game_id, "2025-DEMO", game_date, scheduled_start, home, away,
         "Arena", schedule_observed_at, "SCHEDULED", "test"),
    )
    conn.execute(
        """INSERT INTO game_schedule_events
           (game_id, game_date, scheduled_start_utc, home_team, away_team, venue,
            effective_at_utc, observed_at_utc, source, data_provider)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (game_id, game_date, scheduled_start, home, away, "Arena",
         schedule_observed_at, schedule_observed_at, "test", "test"),
    )
    conn.commit()


def _revise_schedule(conn, game_id, home, away, scheduled_start, observed_at,
                      game_date=None, update_cache=True):
    """Appends a NEW game_schedule_events revision. `update_cache=True`
    also updates the mutable `games` cache row to match -- exactly what a
    real re-ingestion (ingest/nhl_api.py::ingest_schedule) does; pass
    False to simulate the OLD, now-fixed bug where the cache silently
    disagreed with the append-only history."""
    game_date = game_date or scheduled_start[:10]
    conn.execute(
        """INSERT INTO game_schedule_events
           (game_id, game_date, scheduled_start_utc, home_team, away_team, venue,
            effective_at_utc, observed_at_utc, source, data_provider)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (game_id, game_date, scheduled_start, home, away, "Arena",
         observed_at, observed_at, "test", "test"),
    )
    if update_cache:
        conn.execute(
            """UPDATE games SET home_team=?, away_team=?, scheduled_start_utc=?, game_date=?
               WHERE game_id=?""",
            (home, away, scheduled_start, game_date, game_id),
        )
    conn.commit()


def _finalize(conn, game_id, home_score, away_score, result_observed_at):
    conn.execute(
        """UPDATE games SET home_score=?, away_score=?, final_period_type='REG',
                             game_state='FINAL', result_observed_at_utc=? WHERE game_id=?""",
        (home_score, away_score, result_observed_at, game_id),
    )
    conn.execute(
        """INSERT INTO game_result_events
           (game_id, home_score, away_score, final_period_type, game_state,
            effective_at_utc, observed_at_utc, revision_number, source, data_provider)
           VALUES (?,?,?,'REG','FINAL',?,?,1,?,?)""",
        (game_id, home_score, away_score, result_observed_at, result_observed_at, "test", "test"),
    )
    conn.commit()


class TestHomeAwaySwapRevisionIsLearnedConsistently(unittest.TestCase):
    """The exact spec scenario: initial schedule has TOR home / BOS away;
    a later payload swaps it to BOS home / TOR away, with the mutable
    `games` cache updated to match (the real, correctly-functioning
    ingestion path -- ingest/nhl_api.py's re-ingest always keeps the
    cache in sync with the latest schedule_events row). learn() must
    resolve home/away from the SAME schedule history predict() used, not
    silently disagree with it."""

    def setUp(self):
        self.conn, self.path = make_test_db()
        self.conn.execute("INSERT INTO teams (team_id) VALUES ('TOR')")
        self.conn.execute("INSERT INTO teams (team_id) VALUES ('BOS')")
        self.conn.commit()
        # initial: TOR home, BOS away, observed well in advance
        _insert_game_with_schedule(self.conn, 1, "TOR", "BOS",
                                    scheduled_start=t(10, hour=19),
                                    schedule_observed_at=t(-30))
        self.model = CombinedMoneylineModel(teams=["TOR", "BOS"])

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_prediction_before_the_swap_sees_the_original_assignment(self):
        pred = self.model.predict(self.conn, 1, t(10, hour=18, minute=30))
        self.assertEqual(pred.home_team, "TOR")
        self.assertEqual(pred.away_team, "BOS")

    def test_learn_after_the_swap_uses_the_swapped_assignment_not_the_stale_one(self):
        # later: the payload swaps home/away -- BOS is now home, TOR away
        # -- observed AFTER prediction time but before the result.
        _revise_schedule(self.conn, 1, "BOS", "TOR", scheduled_start=t(10, hour=19),
                          observed_at=t(10, hour=17))
        _finalize(self.conn, 1, home_score=5, away_score=2,
                  result_observed_at=t(10, hour=22))

        elo_before = dict(self.model.elo.ratings)
        self.model.learn(self.conn, 1)   # default learn_time_utc

        # BOS (now home per the revision) won 5-2 -- BOS's Elo must have
        # gone UP and TOR's DOWN. If learn() had instead used the STALE
        # games-cache identity (impossible here since the cache was kept
        # in sync, but this assertion is the actual behavioral proof),
        # the direction would be reversed.
        self.assertGreater(self.model.elo.ratings["BOS"], elo_before["BOS"])
        self.assertLess(self.model.elo.ratings["TOR"], elo_before["TOR"])

    def test_schedule_history_and_learned_identity_never_disagree_even_if_the_cache_lagged(self):
        # simulate the OLD bug's precondition directly: the append-only
        # history has the swap, but (hypothetically) the mutable cache
        # was never updated to match -- update_cache=False reproduces
        # exactly the inconsistency the spec describes. learn() must
        # still follow the append-only history (via game_schedule_as_of),
        # never the stale cache, because it no longer reads the cache
        # for this at all.
        _revise_schedule(self.conn, 1, "BOS", "TOR", scheduled_start=t(10, hour=19),
                          observed_at=t(10, hour=17), update_cache=False)
        _finalize(self.conn, 1, home_score=5, away_score=2,
                  result_observed_at=t(10, hour=22))

        cache_row = self.conn.execute(
            "SELECT home_team, away_team FROM games WHERE game_id=1").fetchone()
        self.assertEqual(cache_row["home_team"], "TOR")   # cache is stale, as constructed

        elo_before = dict(self.model.elo.ratings)
        self.model.learn(self.conn, 1)
        # despite the stale cache still saying TOR is home, learn() must
        # have followed the append-only schedule history (BOS home) --
        # BOS's Elo goes up, not TOR's.
        self.assertGreater(self.model.elo.ratings["BOS"], elo_before["BOS"])
        self.assertLess(self.model.elo.ratings["TOR"], elo_before["TOR"])


class TestVenueOrStartTimeOnlyRevisionStillWorksNormally(unittest.TestCase):
    """A normal, non-home/away schedule revision (venue/start-time-only)
    must continue to work exactly as before -- this fix must not disturb
    the unrelated, already-correct venue/start-time revision path."""

    def setUp(self):
        self.conn, self.path = make_test_db()
        self.conn.execute("INSERT INTO teams (team_id) VALUES ('TOR')")
        self.conn.execute("INSERT INTO teams (team_id) VALUES ('BOS')")
        self.conn.commit()
        _insert_game_with_schedule(self.conn, 1, "TOR", "BOS",
                                    scheduled_start=t(10, hour=19),
                                    schedule_observed_at=t(-30))
        self.model = CombinedMoneylineModel(teams=["TOR", "BOS"])

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_start_time_revision_is_learned_correctly_home_away_unchanged(self):
        # game pushed back an hour -- home/away identity is untouched.
        _revise_schedule(self.conn, 1, "TOR", "BOS", scheduled_start=t(10, hour=20),
                          observed_at=t(10, hour=15))
        _finalize(self.conn, 1, home_score=4, away_score=1,
                  result_observed_at=t(10, hour=23))
        elo_before = dict(self.model.elo.ratings)
        self.model.learn(self.conn, 1)
        self.assertGreater(self.model.elo.ratings["TOR"], elo_before["TOR"])
        self.assertLess(self.model.elo.ratings["BOS"], elo_before["BOS"])


if __name__ == "__main__":
    unittest.main()
