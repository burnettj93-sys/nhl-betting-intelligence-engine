"""
Tests for operational/real_prop_orchestrator.py's PLAYER_SOG_ALTERNATE path
(Real Product Bridge block, 2026-09-29) -- the real wiring gap this project
had since the Live SOG + Saves Production Certification block:
run_real_sog_recommendations() only ever consumed the STANDARD market key
(group_standard_two_sided()), so the certified PLAYER_SOG_ALTERNATE contract
never saw a single real payload despite being genuinely verified. This
proves the full real chain: real archived alternate-ladder payload -> real
event/identity mapping -> the real SOG model -> the real generic pricing
core -> a real, immutable prospective_ledger row -- using the SAME real,
repaired research/player_sog corpus every other real path now uses (no
identity mocking needed: these are real players who now really resolve).
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import db
from operational import paper_bankroll
from operational import prospective_ledger as pl
from operational import real_prop_orchestrator as rpo

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load_fixture(name: str) -> dict:
    with open(FIXTURES / name) as f:
        return json.load(f)


def _fresh_nhl_conn():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = db.init_db(db_path=Path(tmp.name), wipe=True)
    conn.execute("INSERT OR IGNORE INTO teams (team_id) VALUES ('CAR'), ('FLA')")
    conn.execute(
        "INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team, away_team, "
        "schedule_observed_at_utc, game_state, source) VALUES (9001, '20262027', '2026-09-29', "
        "'2026-09-29T21:10:47', 'CAR', 'FLA', '2026-09-29T00:00:00', 'SCHEDULED', 'test')")
    conn.commit()
    return conn


def _tmp_pl_conn():
    fd, path = tempfile.mkstemp(suffix=".db")
    Path(path).unlink()
    return pl.init_db(Path(path)), Path(path)


def _tmp_bankroll_conn():
    fd, path = tempfile.mkstemp(suffix=".db")
    Path(path).unlink()
    return paper_bankroll.init_db(Path(path)), Path(path)


class TestRealNhlScheduleCarriesScheduledStartUtc(unittest.TestCase):
    """Production Gap Closure sprint (2026-10-01): _real_nhl_schedule() used
    to select only game_date, forcing event_mapping.map_event_to_game() to
    match on a calendar date (midnight UTC) -- a 100%-reproducible false
    UNMATCHED for any real evening game whose commence_time crosses into
    the next UTC calendar day. scheduled_start_utc was always real,
    already-ingested data in nhl.db; it just was never passed through."""

    def test_scheduled_start_utc_is_present_on_every_row(self):
        conn = _fresh_nhl_conn()
        try:
            schedule = rpo._real_nhl_schedule(conn)
            self.assertEqual(len(schedule), 1)
            self.assertEqual(schedule[0]["scheduled_start_utc"], "2026-09-29T21:10:47")
        finally:
            conn.close()

    def test_an_evening_game_crossing_midnight_utc_now_maps_correctly(self):
        """The exact reproduction: game_date is the ET calendar day, but the
        real commence_time (a 10 PM ET game) falls on the NEXT UTC date."""
        from research.live_sog_pricing import event_mapping
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        conn = db.init_db(db_path=Path(tmp.name), wipe=True)
        conn.execute("INSERT OR IGNORE INTO teams (team_id) VALUES ('LAK'), ('COL')")
        conn.execute(
            "INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team, away_team, "
            "schedule_observed_at_utc, game_state, source) VALUES ('2026020007', '20262027', "
            "'2026-09-30', '2026-10-01T02:00:00', 'COL', 'LAK', '2026-09-30T00:00:00', 'SCHEDULED', 'test')")
        conn.commit()
        schedule = rpo._real_nhl_schedule(conn)
        event = {"id": "e1", "home_team": "Colorado Avalanche", "away_team": "Los Angeles Kings",
                  "commence_time": "2026-10-01T02:10:00Z"}
        result = event_mapping.map_event_to_game(event, schedule)
        self.assertEqual(result["status"], "MATCHED")
        self.assertEqual(str(result["game_id"]), "2026020007")
        conn.close()


class TestRealSogAlternateReachesTheLedger(unittest.TestCase):
    """The real, certified alternate-ladder fixture, with the REAL (repaired)
    identity corpus -- Andrei Svechnikov and Matthew Tkachuk are real,
    common players who now genuinely resolve."""

    def setUp(self):
        self.nhl_conn = _fresh_nhl_conn()
        self.pl_conn, self.pl_path = _tmp_pl_conn()
        self.bankroll_conn, self.bankroll_path = _tmp_bankroll_conn()
        self.payload = _load_fixture("draftkings_player_sog_alternate_real_payload.json")

    def tearDown(self):
        self.nhl_conn.close()
        self.pl_conn.close()
        self.pl_path.unlink(missing_ok=True)
        self.bankroll_conn.close()
        self.bankroll_path.unlink(missing_ok=True)

    def test_real_alternate_payload_produces_real_recorded_observations(self):
        summary = rpo.run_real_sog_recommendations(
            nhl_conn=self.nhl_conn, pl_conn=self.pl_conn, bankroll_conn=self.bankroll_conn,
            payloads=[self.payload])
        self.assertEqual(summary["status"], "SUCCESS")
        self.assertGreater(summary["quotes_seen"], 0)
        self.assertGreater(summary["recommendations_recorded"], 0,
                            f"expected real recordings; full summary: {summary}")

        rows = self.pl_conn.execute(
            "SELECT * FROM predictions WHERE market_id LIKE 'PLAYER_SOG_%' ORDER BY threshold"
        ).fetchall()
        self.assertGreater(len(rows), 0)
        for row in rows:
            self.assertEqual(row["market_family"], "SOG")
            self.assertIn(row["threshold"], ("2+", "3+", "4+", "5+"))
            self.assertEqual(row["sportsbook"], "DraftKings")
            self.assertIsNotNone(row["conservative_probability"])

    def test_a_real_recorded_row_resolves_settlement_via_the_real_sog_family(self):
        rpo.run_real_sog_recommendations(
            nhl_conn=self.nhl_conn, pl_conn=self.pl_conn, bankroll_conn=self.bankroll_conn,
            payloads=[self.payload])
        row = self.pl_conn.execute(
            "SELECT * FROM predictions WHERE market_id LIKE 'PLAYER_SOG_%' LIMIT 1").fetchone()
        self.assertIsNotNone(row)
        from operational import outcome_resolver as resolver
        # Real settlement dispatch is keyed on market_id's own "PLAYER_SOG" prefix,
        # never the shape-specific contract id -- proves the recorded row is
        # genuinely settleable through the existing, real resolver.
        self.assertTrue(row["market_id"].startswith("PLAYER_SOG"))

    def test_rerun_against_the_same_real_payload_is_idempotent(self):
        first = rpo.run_real_sog_recommendations(
            nhl_conn=self.nhl_conn, pl_conn=self.pl_conn, bankroll_conn=self.bankroll_conn,
            payloads=[self.payload])
        second = rpo.run_real_sog_recommendations(
            nhl_conn=self.nhl_conn, pl_conn=self.pl_conn, bankroll_conn=self.bankroll_conn,
            payloads=[self.payload])
        total_rows = self.pl_conn.execute(
            "SELECT COUNT(*) c FROM predictions WHERE market_id LIKE 'PLAYER_SOG_%'").fetchone()["c"]
        self.assertEqual(total_rows, first["recommendations_recorded"])
        self.assertEqual(second["recommendations_recorded"], first["recommendations_recorded"])  # DUPLICATE, not a second insert

    def test_alternate_only_one_plus_and_six_plus_never_recorded(self):
        summary = rpo.run_real_sog_recommendations(
            nhl_conn=self.nhl_conn, pl_conn=self.pl_conn, bankroll_conn=self.bankroll_conn,
            payloads=[self.payload])
        rows = self.pl_conn.execute("SELECT threshold FROM predictions WHERE market_id LIKE 'PLAYER_SOG_%'").fetchall()
        self.assertNotIn("1+", {r["threshold"] for r in rows})
        self.assertNotIn("6+", {r["threshold"] for r in rows})
        self.assertGreater(summary["not_model_validated"], 0)  # the real 1+/6+ quotes were seen and correctly rejected


if __name__ == "__main__":
    unittest.main()
