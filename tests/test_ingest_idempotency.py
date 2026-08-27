"""
Spec item 3 / item 10: ingest/nhl_api.py's write paths must be idempotent —
re-ingesting the same schedule/roster payload must not create duplicate
rows or overwrite a fact's original observed_at_utc. Exercised with
constructed fake payloads shaped like the NHL API's documented response
shape (see ingest/nhl_api.py's docstring), since this sandbox cannot reach
the live API.
"""
import unittest

from ingest import nhl_api
from tests.helpers import make_test_db, t


def _fake_schedule_game(game_id=5000001, home="TOR", away="BOS", start="2025-01-10T19:00:00",
                         season="20252026", venue="TOR Arena", game_date="2025-01-10"):
    return {
        "id": game_id,
        "season": season,
        "gameDate": game_date,
        "startTimeUTC": start,
        "homeTeam": {"abbrev": home},
        "awayTeam": {"abbrev": away},
        "venue": {"default": venue},
    }


def _fake_roster(player_id=8478402, first="Connor", last="McDavid", group="forwards"):
    return {
        group: [
            {"id": player_id, "firstName": {"default": first}, "lastName": {"default": last}}
        ]
    }


class TestScheduleIdempotency(unittest.TestCase):
    def setUp(self):
        self.conn, self.path = make_test_db()
        self.conn.execute("INSERT OR IGNORE INTO teams (team_id) VALUES ('TOR')")
        self.conn.execute("INSERT OR IGNORE INTO teams (team_id) VALUES ('BOS')")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_reingesting_same_schedule_does_not_duplicate_row(self):
        game = _fake_schedule_game()
        nhl_api.ingest_schedule(self.conn, game, observed_at_utc=t(0))
        nhl_api.ingest_schedule(self.conn, game, observed_at_utc=t(1))
        self.conn.commit()
        rows = self.conn.execute(
            "SELECT * FROM games WHERE game_id=?", (game["id"],)
        ).fetchall()
        self.assertEqual(len(rows), 1)

    def test_reingesting_does_not_overwrite_first_observed_at(self):
        game = _fake_schedule_game()
        nhl_api.ingest_schedule(self.conn, game, observed_at_utc=t(0))
        nhl_api.ingest_schedule(self.conn, game, observed_at_utc=t(5))   # a later re-pull
        self.conn.commit()
        row = self.conn.execute(
            "SELECT schedule_observed_at_utc FROM games WHERE game_id=?", (game["id"],)
        ).fetchone()
        self.assertEqual(row["schedule_observed_at_utc"], t(0))

    def test_reingest_still_updates_mutable_schedule_fields(self):
        # a venue change (or a start-time correction) IS expected to update
        # on re-pull -- only schedule_observed_at_utc is frozen at first-seen.
        game = _fake_schedule_game(venue="TOR Arena")
        nhl_api.ingest_schedule(self.conn, game, observed_at_utc=t(0))
        moved = _fake_schedule_game(venue="Neutral Site Arena")
        nhl_api.ingest_schedule(self.conn, moved, observed_at_utc=t(1))
        self.conn.commit()
        row = self.conn.execute(
            "SELECT venue FROM games WHERE game_id=?", (game["id"],)
        ).fetchone()
        self.assertEqual(row["venue"], "Neutral Site Arena")

    def test_ingest_schedule_never_writes_a_result(self):
        game = _fake_schedule_game()
        nhl_api.ingest_schedule(self.conn, game, observed_at_utc=t(0))
        self.conn.commit()
        row = self.conn.execute(
            "SELECT game_state, home_score, result_observed_at_utc FROM games WHERE game_id=?",
            (game["id"],),
        ).fetchone()
        self.assertEqual(row["game_state"], "SCHEDULED")
        self.assertIsNone(row["home_score"])
        self.assertIsNone(row["result_observed_at_utc"])

    def test_ingest_schedule_missing_required_field_raises(self):
        bad_game = {"id": 999, "homeTeam": {"abbrev": "TOR"}}   # no awayTeam
        with self.assertRaises(nhl_api.NHLApiSchemaError):
            nhl_api.ingest_schedule(self.conn, bad_game, observed_at_utc=t(0))


class TestResultIngestion(unittest.TestCase):
    def setUp(self):
        self.conn, self.path = make_test_db()
        self.conn.execute("INSERT OR IGNORE INTO teams (team_id) VALUES ('TOR')")
        self.conn.execute("INSERT OR IGNORE INTO teams (team_id) VALUES ('BOS')")
        self.conn.commit()
        self.game = _fake_schedule_game()
        nhl_api.ingest_schedule(self.conn, self.game, observed_at_utc=t(0))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_ingest_result_sets_final_and_score(self):
        finished = dict(self.game)
        finished["homeTeam"] = {"abbrev": "TOR", "score": 4}
        finished["awayTeam"] = {"abbrev": "BOS", "score": 2}
        finished["periodDescriptor"] = {"periodType": "REG"}
        nhl_api.ingest_result(self.conn, finished, observed_at_utc=t(2))
        self.conn.commit()
        row = self.conn.execute(
            "SELECT game_state, home_score, away_score, result_observed_at_utc "
            "FROM games WHERE game_id=?", (self.game["id"],),
        ).fetchone()
        self.assertEqual(row["game_state"], "FINAL")
        self.assertEqual(row["home_score"], 4)
        self.assertEqual(row["away_score"], 2)
        self.assertEqual(row["result_observed_at_utc"], t(2))

    def test_schedule_observed_at_untouched_by_result_ingestion(self):
        finished = dict(self.game)
        finished["homeTeam"] = {"abbrev": "TOR", "score": 4}
        finished["awayTeam"] = {"abbrev": "BOS", "score": 2}
        finished["periodDescriptor"] = {"periodType": "REG"}
        nhl_api.ingest_result(self.conn, finished, observed_at_utc=t(2))
        self.conn.commit()
        row = self.conn.execute(
            "SELECT schedule_observed_at_utc FROM games WHERE game_id=?", (self.game["id"],)
        ).fetchone()
        self.assertEqual(row["schedule_observed_at_utc"], t(0))


class TestRosterMembershipIdempotency(unittest.TestCase):
    def setUp(self):
        self.conn, self.path = make_test_db()
        self.conn.execute("INSERT OR IGNORE INTO teams (team_id) VALUES ('TOR')")
        self.conn.execute("INSERT OR IGNORE INTO teams (team_id) VALUES ('BOS')")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_reingesting_unchanged_roster_writes_no_new_row(self):
        roster = _fake_roster()
        nhl_api.upsert_team_membership(self.conn, "TOR", roster, observed_at_utc=t(0))
        self.conn.commit()
        nhl_api.upsert_team_membership(self.conn, "TOR", roster, observed_at_utc=t(1))
        self.conn.commit()
        rows = self.conn.execute(
            "SELECT * FROM team_membership_events WHERE player_id=?", ("8478402",)
        ).fetchall()
        self.assertEqual(len(rows), 1)

    def test_player_moving_team_writes_exactly_one_new_row(self):
        roster = _fake_roster()
        nhl_api.upsert_team_membership(self.conn, "TOR", roster, observed_at_utc=t(0))
        self.conn.commit()
        nhl_api.upsert_team_membership(self.conn, "BOS", roster, observed_at_utc=t(5))
        self.conn.commit()
        rows = self.conn.execute(
            "SELECT team_id FROM team_membership_events WHERE player_id=? ORDER BY id",
            ("8478402",),
        ).fetchall()
        self.assertEqual([r["team_id"] for r in rows], ["TOR", "BOS"])

    def test_player_and_team_row_created_from_roster_payload(self):
        roster = _fake_roster()
        nhl_api.upsert_team_membership(self.conn, "TOR", roster, observed_at_utc=t(0))
        self.conn.commit()
        player = self.conn.execute(
            "SELECT * FROM players WHERE player_id=?", ("8478402",)
        ).fetchone()
        self.assertEqual(player["full_name"], "Connor McDavid")
        self.assertEqual(player["position"], "F")

    def test_upsert_team_membership_missing_id_raises(self):
        bad_roster = {"forwards": [{"firstName": {"default": "No"}, "lastName": {"default": "Id"}}]}
        with self.assertRaises(nhl_api.NHLApiSchemaError):
            nhl_api.upsert_team_membership(self.conn, "TOR", bad_roster, observed_at_utc=t(0))


if __name__ == "__main__":
    unittest.main()
