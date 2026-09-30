"""
Platform Recovery block (2026-09-29): proves dashboard/pages/31_Team_Intelligence.py's
new real default view -- real current roster (team_membership_events, a
dated real feed), real today's opponent (Eastern-day, never a simulated
one when the team has no real game today), and that team-strength model
context is honestly reported unavailable rather than fabricated.
"""
from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path

import db
from dashboard import real_team_intelligence_view as rtiv


def _fresh_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = db.init_db(db_path=Path(tmp.name), wipe=True)
    for t in ("TOR", "MTL", "BOS"):
        conn.execute("INSERT OR IGNORE INTO teams (team_id, full_name) VALUES (?, ?)", (t, t))
    conn.execute("INSERT INTO players (player_id, full_name, position) VALUES (?, ?, ?)",
                 ("P1", "Test Forward", "F"))
    conn.execute("INSERT INTO players (player_id, full_name, position) VALUES (?, ?, ?)",
                 ("P2", "Traded Player", "D"))
    # P1: current TOR roster. P2: was on TOR, later traded to MTL -- must NOT appear on TOR's roster.
    conn.execute("INSERT INTO team_membership_events (player_id, team_id, effective_at_utc, observed_at_utc, "
                 "event_type, source) VALUES (?,?,?,?,?,?)", ("P1", "TOR", "2026-07-01T00:00:00", "2026-07-01T00:00:00", "SIGNING", "test"))
    conn.execute("INSERT INTO team_membership_events (player_id, team_id, effective_at_utc, observed_at_utc, "
                 "event_type, source) VALUES (?,?,?,?,?,?)", ("P2", "TOR", "2026-07-01T00:00:00", "2026-07-01T00:00:00", "SIGNING", "test"))
    conn.execute("INSERT INTO team_membership_events (player_id, team_id, effective_at_utc, observed_at_utc, "
                 "event_type, source) VALUES (?,?,?,?,?,?)", ("P2", "MTL", "2026-09-15T00:00:00", "2026-09-15T00:00:00", "TRADE", "test"))
    conn.execute(
        "INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team, away_team, "
        "schedule_observed_at_utc, game_state, source) VALUES (?,?,?,?,?,?,?,?,?)",
        (1, "20262027", "2026-09-29", "2026-09-29T23:00:00", "TOR", "MTL", "2026-09-29T12:00:00", "SCHEDULED", "test"))
    conn.commit()
    return conn


class TestNeverImportsDemoData(unittest.TestCase):
    def test_module_never_imports_the_demo_data_module(self):
        import inspect
        src = inspect.getsource(rtiv)
        self.assertNotIn("demo_data", src)


class TestRealCurrentRoster(unittest.TestCase):
    def test_only_the_players_current_team_membership_is_returned(self):
        conn = _fresh_db()
        roster = rtiv.real_current_roster(conn, "TOR")
        self.assertEqual([p["player_id"] for p in roster], ["P1"])

    def test_a_traded_player_appears_on_the_new_team_not_the_old_one(self):
        conn = _fresh_db()
        mtl_roster = rtiv.real_current_roster(conn, "MTL")
        self.assertEqual([p["player_id"] for p in mtl_roster], ["P2"])

    def test_a_team_with_no_real_players_is_an_honest_empty_list(self):
        conn = _fresh_db()
        self.assertEqual(rtiv.real_current_roster(conn, "BOS"), [])


class TestRealCurrentOpponent(unittest.TestCase):
    def test_a_real_game_today_produces_the_real_opponent(self):
        conn = _fresh_db()
        now = dt.datetime(2026, 9, 29, 18, 0, tzinfo=dt.timezone.utc)
        opp = rtiv.real_current_opponent(conn, "TOR", now)
        self.assertEqual(opp["opponent"], "MTL")
        self.assertTrue(opp["is_home"])

    def test_no_real_game_today_is_none_never_a_simulated_opponent(self):
        conn = _fresh_db()
        now = dt.datetime(2026, 9, 29, 18, 0, tzinfo=dt.timezone.utc)
        self.assertIsNone(rtiv.real_current_opponent(conn, "BOS", now))

    def test_eastern_day_boundary_is_respected(self):
        # 8:30 PM EDT on Sept 29 == 00:30 UTC on Sept 30 -- the real game
        # (game_date '2026-09-29') must still be found, not missed by a
        # naive UTC-date comparison.
        conn = _fresh_db()
        now = dt.datetime(2026, 9, 30, 0, 30, tzinfo=dt.timezone.utc)
        opp = rtiv.real_current_opponent(conn, "TOR", now)
        self.assertIsNotNone(opp)
        self.assertEqual(opp["opponent"], "MTL")


class TestTeamStrengthIsHonestlyUnavailable(unittest.TestCase):
    def test_team_strength_model_is_reported_unavailable_not_fabricated(self):
        conn = _fresh_db()
        state = rtiv.build_real_team_intelligence_state(conn, "TOR",
                                                          now=dt.datetime(2026, 9, 29, 18, 0, tzinfo=dt.timezone.utc))
        self.assertIn("UNAVAILABLE", state["team_strength_model"])


class TestAllTeamsStateForCloudPublishing(unittest.TestCase):
    def test_every_real_team_gets_an_entry(self):
        conn = _fresh_db()
        state = rtiv.build_all_teams_state(conn, now=dt.datetime(2026, 9, 29, 18, 0, tzinfo=dt.timezone.utc))
        self.assertEqual(set(state.keys()), {"TOR", "MTL", "BOS"})

    def test_the_state_is_json_serializable(self):
        import json
        conn = _fresh_db()
        state = rtiv.build_all_teams_state(conn, now=dt.datetime(2026, 9, 29, 18, 0, tzinfo=dt.timezone.utc))
        reloaded = json.loads(json.dumps(state))
        self.assertEqual(reloaded, state)


if __name__ == "__main__":
    unittest.main()
