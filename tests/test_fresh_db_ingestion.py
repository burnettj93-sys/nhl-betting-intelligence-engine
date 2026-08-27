"""
v2.1.2 spec item 1: db.py enables `PRAGMA foreign_keys = ON` and
schema.sql defines games.home_team/away_team as
`REFERENCES teams(team_id)`. ingest_schedule() used to attempt the
`games` INSERT before either team existed in `teams` -- on a completely
fresh, freshly-initialized database with NO teams pre-seeded, that raised
`sqlite3.IntegrityError: FOREIGN KEY constraint failed`. Every existing
test fixture manually pre-inserted TOR/BOS before calling
ingest_schedule(), which concealed this from the whole suite until an
independent review reproduced it against a genuinely clean database.

ingest_schedule() now ensures both teams exist (INSERT OR IGNORE INTO
teams) before the games INSERT -- see
ingest/nhl_api.py::_ensure_teams_exist(). This file proves the fix, and
deliberately uses NON-DEMO teams (EDM, VGK) to prove it isn't
accidentally dependent on the synthetic 12-team demo list ever having
been loaded.
"""
import unittest

import db
from ingest import nhl_api
from tests.helpers import make_test_db


def _fake_schedule_game(game_id, home, away, start="2025-01-10T19:00:00",
                         season="20252026", venue="Rogers Place", game_date="2025-01-10"):
    return {
        "id": game_id,
        "season": season,
        "gameDate": game_date,
        "startTimeUTC": start,
        "homeTeam": {"abbrev": home},
        "awayTeam": {"abbrev": away},
        "venue": {"default": venue},
    }


class TestIngestScheduleAgainstACompletelyCleanDatabase(unittest.TestCase):
    """The exact spec scenario: db.init_db() with NOTHING pre-seeded,
    then straight into ingest_schedule() -- no manual team pre-insertion
    by the caller, ever."""

    def setUp(self):
        self.conn, self.path = make_test_db()   # make_test_db already calls
                                                  # db.init_db() -- NOT wiped
                                                  # with any team rows.

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_no_teams_preexist_before_ingestion(self):
        # sanity check on the fixture itself -- if this ever fails, the
        # rest of this test class is not actually testing what it claims.
        count = self.conn.execute("SELECT COUNT(*) c FROM teams").fetchone()["c"]
        self.assertEqual(count, 0)

    def test_ingest_schedule_does_not_raise_fk_error_on_a_clean_db(self):
        game = _fake_schedule_game(9000001, "EDM", "VGK")
        try:
            nhl_api.ingest_schedule(self.conn, game, observed_at_utc="2025-01-01T00:00:00")
            self.conn.commit()
        except Exception as e:   # pragma: no cover -- failure path
            self.fail(f"ingest_schedule() raised against a clean DB: {e!r}")

    def test_both_teams_exist_after_ingestion(self):
        game = _fake_schedule_game(9000002, "EDM", "VGK")
        nhl_api.ingest_schedule(self.conn, game, observed_at_utc="2025-01-01T00:00:00")
        self.conn.commit()
        rows = {r["team_id"] for r in self.conn.execute("SELECT team_id FROM teams").fetchall()}
        self.assertEqual(rows, {"EDM", "VGK"})

    def test_game_exists_after_ingestion(self):
        game = _fake_schedule_game(9000003, "EDM", "VGK")
        nhl_api.ingest_schedule(self.conn, game, observed_at_utc="2025-01-01T00:00:00")
        self.conn.commit()
        row = self.conn.execute(
            "SELECT home_team, away_team FROM games WHERE game_id=?", (9000003,)
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual((row["home_team"], row["away_team"]), ("EDM", "VGK"))

    def test_schedule_history_event_exists_after_ingestion(self):
        game = _fake_schedule_game(9000004, "EDM", "VGK")
        nhl_api.ingest_schedule(self.conn, game, observed_at_utc="2025-01-01T00:00:00")
        self.conn.commit()
        row = self.conn.execute(
            "SELECT home_team, away_team FROM game_schedule_events WHERE game_id=?", (9000004,)
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual((row["home_team"], row["away_team"]), ("EDM", "VGK"))

    def test_a_second_unrelated_matchup_with_different_non_demo_teams_also_works(self):
        # proves this isn't accidentally scoped to one particular pair --
        # a completely different set of real, non-demo teams must work
        # too, still against the same clean, never-pre-seeded database.
        game1 = _fake_schedule_game(9000005, "EDM", "VGK")
        game2 = _fake_schedule_game(9000006, "COL", "DAL", game_date="2025-01-11",
                                     start="2025-01-11T20:00:00")
        nhl_api.ingest_schedule(self.conn, game1, observed_at_utc="2025-01-01T00:00:00")
        nhl_api.ingest_schedule(self.conn, game2, observed_at_utc="2025-01-01T00:00:00")
        self.conn.commit()
        rows = {r["team_id"] for r in self.conn.execute("SELECT team_id FROM teams").fetchall()}
        self.assertEqual(rows, {"EDM", "VGK", "COL", "DAL"})


class TestIngestScheduleStillWorksWhenTeamsAlreadyExist(unittest.TestCase):
    """Regression guard: the bootstrap must be a genuine no-op (INSERT OR
    IGNORE) when a team already exists -- must never overwrite or
    duplicate an existing teams row."""

    def setUp(self):
        self.conn, self.path = make_test_db()
        self.conn.execute("INSERT INTO teams (team_id) VALUES ('EDM')")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_preexisting_team_row_is_not_duplicated(self):
        game = _fake_schedule_game(9000007, "EDM", "VGK")
        nhl_api.ingest_schedule(self.conn, game, observed_at_utc="2025-01-01T00:00:00")
        self.conn.commit()
        rows = self.conn.execute("SELECT COUNT(*) c FROM teams WHERE team_id='EDM'").fetchone()["c"]
        self.assertEqual(rows, 1)


if __name__ == "__main__":
    unittest.main()
