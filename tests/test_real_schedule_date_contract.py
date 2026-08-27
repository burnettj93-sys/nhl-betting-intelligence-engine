"""
v2.1.2a correctness patch (real-payload contract closure): regression
tests for the REAL NHL schedule wire format, discovered via a genuine
browser-replayed live API response (games 2025030412/2025030413/
2025030414, /v1/schedule/2026-06-03, captured 2026-08-26).

The real API nests each game inside a `gameWeek[]` entry that carries the
calendar date (`gameWeek[].date`) -- the individual game object itself has
NO `gameDate` field. Previously `ingest_schedule()` required
`game["gameDate"]` directly and raised NHLApiSchemaError on every single
real game, because that field simply doesn't exist in the real payload.

Fixed at the ONE correct boundary: `fetch_schedule_range()` -- the only
place the real wire shape is ever seen -- via a new
`_normalize_schedule_game(game, week_date)` helper that returns a COPY of
the game object with the parent week's date attached as the canonical
`gameDate`. `ingest_schedule()` itself is UNCHANGED: it still requires
`gameDate` on whatever game object it's handed, so every existing direct
caller (unit-test fixtures, validate_live_nhl.py's direct ingest_schedule()
loop) continues to work exactly as before.

Critically: the parent `gameWeek.date` is authoritative, NEVER
`startTimeUTC[:10]` -- a game's real NHL calendar date and its UTC start
instant can legitimately differ by a day (a real observed example:
gameWeek.date "2026-06-04" vs startTimeUTC "2026-06-05T00:00:00Z", a late
Thursday-night Eastern puck drop that is already Friday in UTC).
"""
import unittest

from ingest.nhl_api import NHLApiSchemaError, fetch_schedule_range, ingest_schedule
import datetime as dt

from tests.helpers import make_test_db


class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class _FakeSession:
    """One fixed schedule response per test, regardless of URL requested --
    these tests only exercise a single /schedule/{date} call."""

    def __init__(self, schedule_response):
        self._schedule_response = schedule_response

    def get(self, url, timeout=15):
        assert "/schedule/" in url
        return _FakeResponse(self._schedule_response)


def _real_shape_game(game_id=2025030412, home="CAR", away="VGK",
                      start_time_utc="2026-06-05T00:00:00Z", extra_game_date=None):
    """Shaped exactly like the REAL NHL schedule API's individual game
    object -- no `gameDate` key at all, matching the genuine captured
    payload (unless extra_game_date is explicitly passed, to test the
    conflict-detection path)."""
    game = {
        "id": game_id,
        "season": 20252026,
        "gameType": 3,
        "venue": {"default": "Lenovo Center"},
        "startTimeUTC": start_time_utc,
        "gameState": "OFF",
        "homeTeam": {"abbrev": home, "score": 4},
        "awayTeam": {"abbrev": away, "score": 3},
        "periodDescriptor": {"periodType": "OT"},
    }
    if extra_game_date is not None:
        game["gameDate"] = extra_game_date
    return game


def _real_shape_schedule_response(week_date, games, next_start_date=None):
    return {"gameWeek": [{"date": week_date, "games": games}],
            "nextStartDate": next_start_date}


class TestRealScheduleDateContract(unittest.TestCase):
    def setUp(self):
        self.conn, self.path = make_test_db()

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    # -- Test A -----------------------------------------------------------
    def test_a_no_gamedate_on_the_game_object_still_ingests_using_parent_week_date(self):
        game = _real_shape_game(game_id=2025030412, start_time_utc="2026-06-05T00:00:00Z")
        self.assertNotIn("gameDate", game)   # real payload shape: confirmed absent
        response = _real_shape_schedule_response("2026-06-04", [game])
        session = _FakeSession(response)

        games = fetch_schedule_range(session, dt.date(2026, 6, 4), dt.date(2026, 6, 4))
        self.assertEqual(len(games), 1)
        self.assertEqual(games[0]["gameDate"], "2026-06-04")

        ingest_schedule(self.conn, games[0], observed_at_utc="2026-06-04T12:00:00")
        self.conn.commit()
        row = self.conn.execute(
            "SELECT game_date FROM games WHERE game_id=?", (2025030412,)).fetchone()
        self.assertEqual(row["game_date"], "2026-06-04")

    # -- Test B -----------------------------------------------------------
    def test_b_does_not_derive_game_date_from_start_time_utc(self):
        # gameWeek.date is a day EARLIER than startTimeUTC's own calendar
        # date -- proves the system uses the parent week date, not
        # startTimeUTC[:10] (which would wrongly produce "2026-06-05").
        game = _real_shape_game(game_id=2025030412, start_time_utc="2026-06-05T00:00:00Z")
        response = _real_shape_schedule_response("2026-06-04", [game])
        session = _FakeSession(response)

        games = fetch_schedule_range(session, dt.date(2026, 6, 4), dt.date(2026, 6, 4))
        self.assertEqual(games[0]["gameDate"], "2026-06-04")
        self.assertNotEqual(games[0]["gameDate"], "2026-06-05")

        ingest_schedule(self.conn, games[0], observed_at_utc="2026-06-04T12:00:00")
        self.conn.commit()
        row = self.conn.execute(
            "SELECT game_date FROM games WHERE game_id=?", (2025030412,)).fetchone()
        self.assertEqual(row["game_date"], "2026-06-04")
        self.assertNotEqual(row["game_date"], "2026-06-05")

    # -- Test C -----------------------------------------------------------
    def test_c_week_with_games_but_no_date_raises_schema_error(self):
        game = _real_shape_game(game_id=2025030412)
        response = {"gameWeek": [{"games": [game]}], "nextStartDate": None}   # no "date" key
        session = _FakeSession(response)
        with self.assertRaises(NHLApiSchemaError):
            fetch_schedule_range(session, dt.date(2026, 6, 4), dt.date(2026, 6, 4))

    # -- Test D -----------------------------------------------------------
    def test_d_conflicting_parent_and_embedded_game_date_raises_schema_error(self):
        game = _real_shape_game(game_id=2025030412, extra_game_date="2026-06-05")
        response = _real_shape_schedule_response("2026-06-04", [game])
        session = _FakeSession(response)
        with self.assertRaises(NHLApiSchemaError):
            fetch_schedule_range(session, dt.date(2026, 6, 4), dt.date(2026, 6, 4))

    # -- Test E -----------------------------------------------------------
    def test_e_agreeing_parent_and_embedded_game_date_ingests_normally(self):
        game = _real_shape_game(game_id=2025030412, extra_game_date="2026-06-04")
        response = _real_shape_schedule_response("2026-06-04", [game])
        session = _FakeSession(response)

        games = fetch_schedule_range(session, dt.date(2026, 6, 4), dt.date(2026, 6, 4))
        self.assertEqual(games[0]["gameDate"], "2026-06-04")
        ingest_schedule(self.conn, games[0], observed_at_utc="2026-06-04T12:00:00")
        self.conn.commit()
        row = self.conn.execute(
            "SELECT game_date FROM games WHERE game_id=?", (2025030412,)).fetchone()
        self.assertEqual(row["game_date"], "2026-06-04")

    # -- Test F -----------------------------------------------------------
    def test_f_direct_ingest_schedule_canonical_fixture_still_works_unchanged(self):
        # ingest_schedule() itself is untouched -- a canonical internal
        # object built directly (as every pre-existing test/caller does,
        # never going through fetch_schedule_range()) must keep working
        # exactly as before this patch.
        canonical_game = {
            "id": 555,
            "season": "2025-DEMO",
            "gameDate": "2025-01-10",
            "startTimeUTC": "2025-01-10T19:00:00",
            "homeTeam": {"abbrev": "TOR"},
            "awayTeam": {"abbrev": "BOS"},
            "venue": {"default": "Arena"},
        }
        ingest_schedule(self.conn, canonical_game, observed_at_utc="2025-01-10T12:00:00")
        self.conn.commit()
        row = self.conn.execute(
            "SELECT game_date FROM games WHERE game_id=?", (555,)).fetchone()
        self.assertEqual(row["game_date"], "2025-01-10")

    # -- extra: raw response is never mutated ------------------------------
    def test_raw_game_object_is_not_mutated_in_place(self):
        game = _real_shape_game(game_id=2025030412)
        response = _real_shape_schedule_response("2026-06-04", [game])
        session = _FakeSession(response)
        fetch_schedule_range(session, dt.date(2026, 6, 4), dt.date(2026, 6, 4))
        # the original dict inside the fake response must remain untouched
        self.assertNotIn("gameDate", game)


if __name__ == "__main__":
    unittest.main()
