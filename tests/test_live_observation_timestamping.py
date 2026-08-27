"""
v2.1.2a spec item 5: `observed_at_utc` must never predate the actual
receipt of the fact it's timestamping. Previously ingest_range() captured
a SINGLE `now` once, before the loop, and stamped every schedule/result/
boxscore row across the whole batch with it -- including boxscore
fetches, each its own separate, later-arriving HTTP response. A
boxscore that physically arrived minutes into a long batch could end up
stamped with a timestamp from before the batch even started.

Fixed: schedule+result share one timestamp captured right after
fetch_schedule_range() itself returns (they arrive in the same response,
no separate round trip for the result); each game's boxscore gets its
OWN fresh timestamp captured immediately after that game's own
fetch_boxscore() call returns, before it's persisted.

Uses a fake `session` (matching every other ingest/nhl_api.py test's
fake-session pattern -- a `.get(url, timeout=...)` -> object with
`.raise_for_status()`/`.json()`) injected via ingest_range()'s new
optional `session=` parameter (v2.1.2a spec item 5), with a real, short
`time.sleep()` inside the fake boxscore response so a later-arriving
response measurably produces a later `datetime.utcnow().isoformat()`
string -- no datetime monkeypatching needed.
"""
import datetime as dt
import inspect
import time
import unittest

from ingest import nhl_api
from tests.helpers import make_test_db


class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class _FakeSession:
    def __init__(self, schedule_response, boxscores_by_game, boxscore_delay_seconds=0.05):
        self._schedule_response = schedule_response
        self._boxscores_by_game = boxscores_by_game
        self._boxscore_delay_seconds = boxscore_delay_seconds
        self.get_calls = []

    def get(self, url, timeout=15):
        self.get_calls.append(url)
        if "/schedule/" in url:
            return _FakeResponse(self._schedule_response)
        if "/boxscore" in url:
            time.sleep(self._boxscore_delay_seconds)
            for game_id, box in self._boxscores_by_game.items():
                if f"/gamecenter/{game_id}/boxscore" in url:
                    return _FakeResponse(box)
            raise AssertionError(f"no fake boxscore registered for URL {url}")
        raise AssertionError(f"unexpected URL {url}")


def _schedule_response(games, date_str="2025-01-10"):
    return {"gameWeek": [{"date": date_str, "games": games}], "nextStartDate": None}


def _schedule_game(game_id, home, away, score_home=3, score_away=2):
    return {
        "id": game_id,
        "season": "20252026",
        "gameDate": "2025-01-10",
        "startTimeUTC": "2025-01-10T19:00:00",
        "homeTeam": {"abbrev": home, "score": score_home},
        "awayTeam": {"abbrev": away, "score": score_away},
        "venue": {"default": "Arena"},
        "gameState": "OFF",
        "periodDescriptor": {"periodType": "REG"},
    }


def _boxscore(game_id, home, away, home_player, away_player):
    return {
        "id": game_id,
        "homeTeam": {"abbrev": home, "sog": 30},
        "awayTeam": {"abbrev": away, "sog": 25},
        "playerByGameStats": {
            "homeTeam": {
                "forwards": [{"playerId": home_player, "sog": 4, "goals": 1, "assists": 0,
                               "toi": "15:00"}],
                "defense": [], "goalies": [],
            },
            "awayTeam": {
                "forwards": [{"playerId": away_player, "sog": 3, "goals": 0, "assists": 1,
                               "toi": "16:00"}],
                "defense": [], "goalies": [],
            },
        },
    }


class TestLiveObservationTimestamping(unittest.TestCase):
    def setUp(self):
        self.conn, self.path = make_test_db()

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_boxscore_timestamp_is_not_earlier_than_the_schedule_timestamp(self):
        game = _schedule_game(9001, "TOR", "BOS")
        box = _boxscore(9001, "TOR", "BOS", 1111, 2222)
        session = _FakeSession(_schedule_response([game]), {9001: box},
                                boxscore_delay_seconds=0.05)
        nhl_api.ingest_range(self.conn, dt.date(2025, 1, 10), dt.date(2025, 1, 10),
                              session=session)
        self.conn.commit()
        sched_ts = self.conn.execute(
            "SELECT observed_at_utc FROM game_schedule_events WHERE game_id=?",
            (9001,)).fetchone()["observed_at_utc"]
        stat_ts = self.conn.execute(
            "SELECT observed_at_utc FROM player_game_stats WHERE game_id=? AND player_id=?",
            (9001, "1111")).fetchone()["observed_at_utc"]
        self.assertGreaterEqual(stat_ts, sched_ts)
        self.assertGreater(stat_ts, sched_ts)   # the real 50ms delay makes this strict

    def test_a_later_arriving_boxscore_does_not_inherit_an_earlier_batch_start_time(self):
        # two games in the same batch; the boxscore fetch for the SECOND
        # game arrives measurably later. Its persisted timestamp must
        # reflect that later receipt, not the moment the batch (or the
        # first game's boxscore) began.
        g1 = _schedule_game(9001, "TOR", "BOS")
        g2 = _schedule_game(9002, "NYR", "NJD")
        b1 = _boxscore(9001, "TOR", "BOS", 1111, 2222)
        b2 = _boxscore(9002, "NYR", "NJD", 3333, 4444)
        session = _FakeSession(_schedule_response([g1, g2]), {9001: b1, 9002: b2},
                                boxscore_delay_seconds=0.05)
        nhl_api.ingest_range(self.conn, dt.date(2025, 1, 10), dt.date(2025, 1, 10),
                              session=session)
        self.conn.commit()
        ts1 = self.conn.execute(
            "SELECT observed_at_utc FROM player_game_stats WHERE game_id=?",
            (9001,)).fetchone()["observed_at_utc"]
        ts2 = self.conn.execute(
            "SELECT observed_at_utc FROM player_game_stats WHERE game_id=?",
            (9002,)).fetchone()["observed_at_utc"]
        self.assertLess(ts1, ts2)

    def test_schedule_and_result_share_one_timestamp_from_the_single_schedule_response(self):
        # schedule and result both arrive embedded in the same
        # schedule-range response -- no separate network round trip for
        # the result -- so they legitimately share one observed_at_utc.
        game = _schedule_game(9003, "TOR", "BOS")
        box = _boxscore(9003, "TOR", "BOS", 5555, 6666)
        session = _FakeSession(_schedule_response([game]), {9003: box})
        nhl_api.ingest_range(self.conn, dt.date(2025, 1, 10), dt.date(2025, 1, 10),
                              session=session)
        self.conn.commit()
        sched_ts = self.conn.execute(
            "SELECT observed_at_utc FROM game_schedule_events WHERE game_id=?",
            (9003,)).fetchone()["observed_at_utc"]
        result_ts = self.conn.execute(
            "SELECT observed_at_utc FROM game_result_events WHERE game_id=?",
            (9003,)).fetchone()["observed_at_utc"]
        self.assertEqual(sched_ts, result_ts)

    def test_boxscore_timestamp_still_strictly_later_than_shared_schedule_result_timestamp(self):
        game = _schedule_game(9004, "TOR", "BOS")
        box = _boxscore(9004, "TOR", "BOS", 7777, 8888)
        session = _FakeSession(_schedule_response([game]), {9004: box},
                                boxscore_delay_seconds=0.05)
        nhl_api.ingest_range(self.conn, dt.date(2025, 1, 10), dt.date(2025, 1, 10),
                              session=session)
        self.conn.commit()
        result_ts = self.conn.execute(
            "SELECT observed_at_utc FROM game_result_events WHERE game_id=?",
            (9004,)).fetchone()["observed_at_utc"]
        boxscore_ts = self.conn.execute(
            "SELECT observed_at_utc FROM player_game_stats WHERE game_id=?",
            (9004,)).fetchone()["observed_at_utc"]
        self.assertGreater(boxscore_ts, result_ts)

    def test_ingest_range_exposes_an_injectable_session_parameter(self):
        # signature-compatibility check (spec item 5): a caller/test must
        # be able to pass session= without needing real network access;
        # the default remains None (constructs a real requests.Session()
        # internally) so every pre-existing caller is unaffected.
        sig = inspect.signature(nhl_api.ingest_range)
        self.assertIn("session", sig.parameters)
        self.assertIsNone(sig.parameters["session"].default)


if __name__ == "__main__":
    unittest.main()
