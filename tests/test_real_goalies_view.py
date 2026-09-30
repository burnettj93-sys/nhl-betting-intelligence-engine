"""
Platform Recovery block (2026-09-30): proves dashboard/pages/27_Goalies.py's
new real default view -- real current goalies, real starter status via
the sanctioned features.point_in_time.goalie_status() accessor (never a
direct, unjustified read of the restricted goalie_status_events table),
and an honest MARKET_UNAVAILABLE for GOALIE_SAVES (no certified real
DraftKings payload contract exists today).
"""
from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path

import db
from dashboard import real_goalies_view as rgv


def _fresh_db(goalie_status_row=None):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = db.init_db(db_path=Path(tmp.name), wipe=True)
    for t in ("TOR", "MTL"):
        conn.execute("INSERT OR IGNORE INTO teams (team_id, full_name) VALUES (?, ?)", (t, t))
    conn.execute("INSERT INTO players (player_id, full_name, position) VALUES (?, ?, ?)",
                 ("G1", "Test Goalie", "G"))
    conn.execute("INSERT INTO team_membership_events (player_id, team_id, effective_at_utc, observed_at_utc, "
                 "event_type, source) VALUES (?,?,?,?,?,?)",
                 ("G1", "TOR", "2026-07-01T00:00:00", "2026-07-01T00:00:00", "SIGNING", "test"))
    conn.execute(
        "INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team, away_team, "
        "schedule_observed_at_utc, game_state, source) VALUES (?,?,?,?,?,?,?,?,?)",
        (1, "20262027", "2026-09-29", "2026-09-29T23:00:00", "TOR", "MTL", "2026-09-29T12:00:00", "SCHEDULED", "test"))
    if goalie_status_row:
        conn.execute("INSERT INTO goalie_status_events (game_id, team_id, player_id, status, effective_at_utc, "
                     "observed_at_utc, source) VALUES (?,?,?,?,?,?,?)",
                     (1, "TOR", "G1", goalie_status_row, "2026-09-29T17:00:00", "2026-09-29T17:00:00", "test"))
    conn.commit()
    return conn


class TestNeverImportsDemoData(unittest.TestCase):
    def test_module_never_imports_the_demo_data_module(self):
        import inspect
        self.assertNotIn("demo_data", inspect.getsource(rgv))


class TestRealCurrentGoalies(unittest.TestCase):
    def test_only_position_g_players_are_returned(self):
        conn = _fresh_db()
        conn.execute("INSERT INTO players (player_id, full_name, position) VALUES (?, ?, ?)",
                     ("F1", "Test Forward", "F"))
        goalies = rgv.real_current_goalies(conn)
        self.assertEqual([g["player_id"] for g in goalies], ["G1"])


class TestRealStarterStatus(unittest.TestCase):
    def test_no_status_event_is_honestly_unconfirmed_never_a_guessed_starter(self):
        conn = _fresh_db()
        now = dt.datetime(2026, 9, 29, 18, 0, tzinfo=dt.timezone.utc)
        state = rgv.build_real_goalies_state(conn, now=now)
        self.assertEqual(state["goalies"][0]["starter_status"], rgv.STARTER_UNCONFIRMED)
        self.assertFalse(state["goalies"][0]["is_confirmed_starter"])

    def test_a_real_confirmed_status_is_reported_confirmed(self):
        conn = _fresh_db(goalie_status_row="CONFIRMED")
        now = dt.datetime(2026, 9, 29, 18, 0, tzinfo=dt.timezone.utc)
        state = rgv.build_real_goalies_state(conn, now=now)
        self.assertEqual(state["goalies"][0]["starter_status"], rgv.STARTER_CONFIRMED)
        self.assertTrue(state["goalies"][0]["is_confirmed_starter"])

    def test_expected_status_is_still_reported_unconfirmed_not_promoted_to_confirmed(self):
        conn = _fresh_db(goalie_status_row="EXPECTED")
        now = dt.datetime(2026, 9, 29, 18, 0, tzinfo=dt.timezone.utc)
        state = rgv.build_real_goalies_state(conn, now=now)
        self.assertEqual(state["goalies"][0]["starter_status"], rgv.STARTER_UNCONFIRMED)

    def test_a_backup_goalie_on_the_same_team_is_not_labeled_confirmed(self):
        """Production Gap Closure sprint (2026-09-30): starter_status used to be
        derived at TEAM level -- every goalie on a team with a confirmed starter
        got the literal text "CONFIRMED" stamped on their own row, not just the
        one real, specifically-named starter."""
        conn = _fresh_db(goalie_status_row="CONFIRMED")  # confirms G1 as TOR's starter
        conn.execute("INSERT INTO players (player_id, full_name, position) VALUES (?, ?, ?)",
                     ("G2", "Backup Goalie", "G"))
        conn.execute("INSERT INTO team_membership_events (player_id, team_id, effective_at_utc, "
                     "observed_at_utc, event_type, source) VALUES (?,?,?,?,?,?)",
                     ("G2", "TOR", "2026-07-01T00:00:00", "2026-07-01T00:00:00", "SIGNING", "test"))
        conn.commit()
        now = dt.datetime(2026, 9, 29, 18, 0, tzinfo=dt.timezone.utc)
        state = rgv.build_real_goalies_state(conn, now=now)
        by_id = {g["player_id"]: g for g in state["goalies"]}

        self.assertEqual(by_id["G1"]["starter_status"], rgv.STARTER_CONFIRMED)
        self.assertTrue(by_id["G1"]["is_confirmed_starter"])
        self.assertEqual(by_id["G2"]["starter_status"], rgv.STARTER_UNCONFIRMED,
                          "the backup must show UNCONFIRMED for himself, not the team's CONFIRMED starter")
        self.assertFalse(by_id["G2"]["is_confirmed_starter"])


class TestMarketStateIsHonestlyUnavailable(unittest.TestCase):
    def test_goalie_saves_market_is_reported_unavailable_never_a_demo_line(self):
        conn = _fresh_db()
        now = dt.datetime(2026, 9, 29, 18, 0, tzinfo=dt.timezone.utc)
        state = rgv.build_real_goalies_state(conn, now=now)
        self.assertEqual(state["goalies"][0]["market_state"], rgv.MARKET_UNAVAILABLE)


class TestJsonSerializable(unittest.TestCase):
    def test_state_is_json_serializable(self):
        import json
        conn = _fresh_db(goalie_status_row="CONFIRMED")
        now = dt.datetime(2026, 9, 29, 18, 0, tzinfo=dt.timezone.utc)
        state = rgv.build_real_goalies_state(conn, now=now)
        reloaded = json.loads(json.dumps(state))
        self.assertEqual(reloaded, state)


if __name__ == "__main__":
    unittest.main()
