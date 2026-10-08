"""
Platform Recovery block (2026-09-29): proves the real Today slate uses
America/Toronto calendar-day semantics, not a UTC date that rolls over to
"tomorrow" hours before tonight's real games have started.
"""
from __future__ import annotations

import datetime as dt
import unittest

from operational import eastern_time as et


class TestEasternToday(unittest.TestCase):
    def test_8pm_edt_is_still_the_same_eastern_day_even_though_utc_has_rolled_over(self):
        # 8:30 PM EDT on Sept 29 == 00:30 UTC on Sept 30 (EDT is UTC-4 in September).
        now_utc = dt.datetime(2026, 9, 30, 0, 30, tzinfo=dt.timezone.utc)
        self.assertEqual(now_utc.date().isoformat(), "2026-09-30")  # the naive-UTC bug this guards against
        self.assertEqual(et.eastern_today(now_utc), "2026-09-29")

    def test_after_eastern_midnight_the_day_has_genuinely_advanced(self):
        # 12:30 AM EDT on Sept 30 == 04:30 UTC on Sept 30.
        now_utc = dt.datetime(2026, 9, 30, 4, 30, tzinfo=dt.timezone.utc)
        self.assertEqual(et.eastern_today(now_utc), "2026-09-30")

    def test_est_offset_in_winter_is_also_correct(self):
        # 11:30 PM EST on Jan 14 == 04:30 UTC on Jan 15 (EST is UTC-5 in January).
        now_utc = dt.datetime(2026, 1, 15, 4, 30, tzinfo=dt.timezone.utc)
        self.assertEqual(et.eastern_today(now_utc), "2026-01-14")


class TestScheduleUsesEasternDay(unittest.TestCase):
    def test_a_game_scheduled_tonight_is_on_todays_eastern_date_at_8pm_eastern(self):
        import tempfile
        from pathlib import Path
        import db
        from operational import product_data

        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        conn = db.init_db(db_path=Path(tmp.name), wipe=True)
        for t in ("TOR", "MTL"):
            conn.execute("INSERT OR IGNORE INTO teams (team_id, full_name) VALUES (?, ?)", (t, t))
        conn.execute(
            "INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team, away_team, "
            "schedule_observed_at_utc, game_state, source) VALUES (?,?,?,?,?,?,?,?,?)",
            (2026020001, "20262027", "2026-09-29", "2026-09-30T01:30:00", "TOR", "MTL",
             "2026-09-29T12:00:00", "SCHEDULED", "test"))
        conn.commit()

        # 8:30 PM EDT -- UTC has already rolled to Sept 30, but it's still Sept 29 in Toronto.
        now_utc = dt.datetime(2026, 9, 30, 0, 30, tzinfo=dt.timezone.utc)
        games = product_data.load_games(conn, now_utc)
        today = et.eastern_today(now_utc)
        todays = [g for g in games if g["date_et"] == today]
        self.assertEqual(today, "2026-09-29")
        self.assertEqual(len(todays), 1, "the naive-UTC bug would put this game on tomorrow")
        self.assertEqual((todays[0]["home"], todays[0]["away"], todays[0]["state"]), ("TOR", "MTL", "SCHEDULED"))


if __name__ == "__main__":
    unittest.main()
