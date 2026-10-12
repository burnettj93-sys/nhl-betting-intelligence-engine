"""Keeping the Mac awake through the 8:00 AM look and each wave's pregame window: windows from the schedule, one hold per window, never in a test run, never fatal."""
from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import db
from operational import keep_awake as ka
from operational import state_paths

DAY = "2026-10-12"


def conn_with(starts):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = db.init_db(db_path=Path(tmp.name), wipe=True)
    for t in ("AAA", "BBB"):
        conn.execute("INSERT OR IGNORE INTO teams (team_id, full_name) VALUES (?, ?)", (t, t))
    for i, s in enumerate(starts):
        conn.execute("INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team, away_team, schedule_observed_at_utc, game_state, source) "
                     "VALUES (?, '20262027', ?, ?, 'AAA', 'BBB', ?, 'SCHEDULED', 'test')", (100 + i, DAY, s, s))
    conn.commit()
    return conn


def utc(h, m=0):
    return dt.datetime(2026, 10, 12, h, m, tzinfo=dt.timezone.utc)


class TestWindows(unittest.TestCase):
    def test_the_morning_window_and_one_pregame_window_per_wave(self):
        conn = conn_with(["2026-10-12T17:00:00", "2026-10-12T23:00:00", "2026-10-13T00:00:00"])      # a 1 PM game, then two games an hour apart (one wave)
        w = ka.windows(utc(11), conn)
        self.assertEqual(len(w), 3)
        self.assertEqual((w[0][0], w[0][1]), (utc(11, 50), utc(12, 35)))                              # 7:50-8:35 AM ET
        self.assertEqual((w[1][0], w[1][1]), (utc(17) - dt.timedelta(minutes=125), utc(17)))          # the lone 1 PM game: capture opens 105 min before, held from 20 min earlier
        self.assertEqual((w[2][0], w[2][1]), (utc(23) - dt.timedelta(minutes=125), utc(23)))          # the wave of two is one window, ending at its FIRST puck drop

    def test_overlapping_windows_are_merged(self):
        conn = conn_with(["2026-10-12T14:00:00", "2026-10-12T14:30:00"])                               # a wave whose window runs back into the morning window
        w = ka.windows(utc(11), conn)
        self.assertEqual(len(w), 1)
        self.assertIn("the 8:00 AM look", w[0][2])


class TestHold(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(state_paths, "path", lambda n, **kw: Path(self.tmp.name) / n)
        p.start()
        self.addCleanup(p.stop)
        self.conn = conn_with(["2026-10-12T23:00:00", "2026-10-13T00:00:00"])
        self.spawned = []

    def spawn(self, secs):
        self.spawned.append(secs)
        return 4242

    def test_nothing_is_started_long_before_a_window(self):
        out = ka.hold(utc(13), self.conn, spawn=self.spawn)
        self.assertEqual(out["status"], "NOT_NEEDED_YET")
        self.assertEqual(self.spawned, [])

    def test_the_hold_starts_just_before_the_window_for_the_rest_of_it_and_only_once(self):
        now = utc(23) - dt.timedelta(minutes=140)                                                       # 20 minutes before the window opens (125 minutes ahead)
        with mock.patch.object(ka, "_alive", return_value=True):
            first = ka.hold(now, self.conn, spawn=self.spawn)
            again = ka.hold(now + dt.timedelta(minutes=15), self.conn, spawn=self.spawn)
        self.assertEqual(first["status"], "STARTED")
        self.assertAlmostEqual(self.spawned[0], 140 * 60 + 300, delta=1)                                 # through the first puck drop, plus five minutes
        self.assertEqual(again["status"], "HOLDING")
        self.assertEqual(len(self.spawned), 1)

    def test_a_dead_hold_is_restarted(self):
        now = utc(23) - dt.timedelta(minutes=100)
        with mock.patch.object(ka, "_alive", return_value=False):
            ka.hold(now, self.conn, spawn=self.spawn)
            ka.hold(now + dt.timedelta(minutes=15), self.conn, spawn=self.spawn)
        self.assertEqual(len(self.spawned), 2)

    def test_after_the_last_window_there_is_nothing_to_hold(self):
        self.assertEqual(ka.hold(utc(23, 30), self.conn, spawn=self.spawn)["status"], "NO_MORE_WINDOWS_TODAY")

    def test_a_test_run_never_starts_a_real_process_and_a_failure_is_not_fatal(self):
        self.assertEqual(ka.hold(utc(22), self.conn)["status"], "SKIPPED_UNDER_TEST")
        broken = ka.hold(utc(22), object(), spawn=self.spawn)
        self.assertIn(broken["status"], ("STARTED", "NOT_NEEDED_YET", "NO_MORE_WINDOWS_TODAY", "ERROR"))


if __name__ == "__main__":
    unittest.main()
