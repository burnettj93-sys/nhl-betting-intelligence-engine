"""
Production Gap Closure sprint (2026-09-30): proves Game Detail's real path
resolves a real, current game_id to ITSELF from nhl.db's own `games` table
-- never the frozen historical research corpus, and never a silently
substituted, unrelated game.
"""
from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import db
from dashboard import real_game_detail_view as rgdv


def _fresh_db(game_id=1, home="TOR", away="MTL", start_utc="2026-09-29T23:00:00", state="SCHEDULED",
              home_score=None, away_score=None):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = db.init_db(db_path=Path(tmp.name), wipe=True)
    for t in (home, away):
        conn.execute("INSERT OR IGNORE INTO teams (team_id, full_name) VALUES (?, ?)", (t, t))
    conn.execute(
        "INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team, away_team, "
        "schedule_observed_at_utc, game_state, home_score, away_score, source) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (game_id, "20262027", start_utc[:10], start_utc, home, away, start_utc, state, home_score, away_score,
         "test"))
    conn.commit()
    return conn


NOW = dt.datetime(2026, 9, 29, 19, 0, tzinfo=dt.timezone.utc)


class TestRealGameLookup(unittest.TestCase):
    def test_unknown_game_id_is_honestly_not_found(self):
        conn = _fresh_db(game_id=1)
        state = rgdv.build_real_game_detail_state(conn, "999", now=NOW)
        self.assertEqual(state["status"], "NOT_FOUND")
        self.assertEqual(state["game_id"], "999")

    def test_a_real_current_game_resolves_to_itself_never_a_substitute(self):
        conn = _fresh_db(game_id=42, home="TOR", away="MTL")
        state = rgdv.build_real_game_detail_state(conn, "42", now=NOW)
        self.assertEqual(state["status"], "FOUND")
        self.assertEqual(state["game_id"], "42")
        self.assertEqual(state["home_team"], "TOR")
        self.assertEqual(state["away_team"], "MTL")

    def test_a_final_game_reports_its_real_score(self):
        conn = _fresh_db(game_id=1, state="FINAL", home_score=4, away_score=2)
        state = rgdv.build_real_game_detail_state(conn, "1", now=NOW)
        self.assertEqual(state["game_state"], "FINAL")
        self.assertEqual(state["home_score"], 4)
        self.assertEqual(state["away_score"], 2)


class TestRealMoneylineState(unittest.TestCase):
    def test_final_game_reports_game_not_scheduled_never_a_stale_model_call(self):
        conn = _fresh_db(game_id=1, state="FINAL")
        state = rgdv.build_real_game_detail_state(conn, "1", now=NOW)
        self.assertEqual(state["moneyline"]["status"], "GAME_NOT_SCHEDULED")

    def test_event_already_started_is_reported_honestly(self):
        conn = _fresh_db(game_id=1, start_utc="2026-09-29T18:00:00")  # before NOW
        state = rgdv.build_real_game_detail_state(conn, "1", now=NOW)
        self.assertEqual(state["moneyline"]["status"], "EVENT_ALREADY_STARTED")

    def test_model_error_is_reported_not_crashed(self):
        conn = _fresh_db(game_id=1, start_utc="2026-09-30T23:00:00")
        with mock.patch("run_slate.build_prediction_for_game", side_effect=RuntimeError("no elo data")):
            state = rgdv.build_real_game_detail_state(conn, "1", now=NOW)
        self.assertEqual(state["moneyline"]["status"], "MODEL_UNAVAILABLE")
        self.assertIn("RuntimeError", state["moneyline"]["reason"])

    def test_available_model_state_surfaces_real_reports(self):
        from types import SimpleNamespace
        conn = _fresh_db(game_id=1, start_utc="2026-09-30T23:00:00")
        pred = SimpleNamespace(game_id=1, home_team="TOR", away_team="MTL", game_date="2026-09-30")
        report = SimpleNamespace(selection="TOR", action="BET", action_reason=None,
                                  model_conservative_probability=0.62, current_draftkings_price=-150)
        with mock.patch("run_slate.build_prediction_for_game", return_value=pred), \
             mock.patch("pricing.engine.evaluate_moneyline_for_game", return_value=[report]):
            state = rgdv.build_real_game_detail_state(conn, "1", now=NOW)
        self.assertEqual(state["moneyline"]["status"], "AVAILABLE")
        self.assertEqual(state["moneyline"]["reports"][0]["selection"], "TOR")
        self.assertEqual(state["moneyline"]["reports"][0]["action"], "BET")


if __name__ == "__main__":
    unittest.main()
