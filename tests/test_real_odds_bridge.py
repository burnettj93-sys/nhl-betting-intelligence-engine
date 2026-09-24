"""
Real Recommendation Pipeline block (2026-09-24): tests for
operational/real_odds_bridge.py -- entirely isolated temp databases and
temp cache files, never the real nhl.db or moneyline_snapshot_cache.json.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import db
from operational import real_odds_bridge as rob


def _fresh_conn():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return db.init_db(Path(tmp.name), wipe=True)


def _seed_game(conn, game_id, home, away, game_date):
    conn.execute("INSERT OR IGNORE INTO teams (team_id) VALUES (?), (?)", (home, away))
    conn.execute(
        "INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team, away_team, "
        "schedule_observed_at_utc, game_state, source) VALUES (?, '20262027', ?, ?, ?, ?, ?, 'SCHEDULED', 'test')",
        (game_id, game_date, f"{game_date}T23:00:00", home, away, f"{game_date}T00:00:00Z"))
    conn.commit()


def _write_cache(path: Path, rows: list[dict]) -> None:
    path.write_text(json.dumps({"rows": rows}))


class TestFindRealGameId(unittest.TestCase):
    def test_matches_a_unique_real_game(self):
        conn = _fresh_conn()
        _seed_game(conn, 1, "EDM", "CGY", "2026-09-29")
        game_id = rob._find_real_game_id(conn, "EDM", "CGY", "2026-09-29T23:00:00Z")
        self.assertEqual(game_id, 1)

    def test_no_match_returns_none(self):
        conn = _fresh_conn()
        game_id = rob._find_real_game_id(conn, "EDM", "CGY", "2026-09-29T23:00:00Z")
        self.assertIsNone(game_id)

    def test_ambiguous_match_returns_none_never_guesses(self):
        """Two real games, same team pair, both within the matching
        window (e.g. a real home-and-home) -- must never silently pick
        one over the other."""
        conn = _fresh_conn()
        _seed_game(conn, 1, "EDM", "CGY", "2026-09-29")
        _seed_game(conn, 2, "EDM", "CGY", "2026-09-30")
        game_id = rob._find_real_game_id(conn, "EDM", "CGY", "2026-09-29T23:00:00Z")
        self.assertIsNone(game_id)

    def test_malformed_commence_time_returns_none(self):
        conn = _fresh_conn()
        _seed_game(conn, 1, "EDM", "CGY", "2026-09-29")
        self.assertIsNone(rob._find_real_game_id(conn, "EDM", "CGY", "not-a-real-timestamp"))


class TestSyncMoneylineOddsToSnapshots(unittest.TestCase):
    def test_matched_row_writes_both_sides(self):
        conn = _fresh_conn()
        _seed_game(conn, 1, "EDM", "CGY", "2026-09-29")
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "cache.json"
            _write_cache(cache_path, [{
                "home_team_abbrev": "EDM", "away_team_abbrev": "CGY",
                "commence_time_utc": "2026-09-29T23:00:00Z", "home_price": -150.0, "away_price": 130.0,
                "captured_at_utc": "2026-09-24T12:00:00Z", "snapshot_label": "morning",
            }])
            summary = rob.sync_moneyline_odds_to_snapshots(conn=conn, cache_path=cache_path)
        self.assertEqual(summary["status"], "SUCCESS")
        self.assertEqual(summary["rows_written"], 2)
        rows = conn.execute("SELECT selection, price_american FROM odds_snapshots WHERE game_id=1").fetchall()
        prices = {r["selection"]: r["price_american"] for r in rows}
        self.assertEqual(prices["EDM"], -150.0)
        self.assertEqual(prices["CGY"], 130.0)

    def test_z_suffixed_timestamps_are_normalized_to_the_naive_canonical_form(self):
        """Real bug caught only by running the orchestrator against real
        production data (Real Recommendation Pipeline block, 2026-09-24,
        Part 20): a real Odds-API captured_at_utc/commence_time_utc
        arrives "Z"-suffixed, but every other timestamp in this project
        (scheduled_start_utc, prediction_time_utc, etc.) is the naive,
        no-suffix canonical form ingest/timestamps.py::
        normalize_utc_timestamp() defines. The original code stripped
        "Z" from commence_time_utc only and left captured_at_utc/
        received_at_utc untouched (or tz-aware via now.isoformat()) --
        `dt.datetime.fromisoformat()` then parsed one side of a
        subtraction as timezone-aware and the other as naive, raising
        TypeError deep inside features/point_in_time.py. Every stored
        timestamp must come back in the identical naive form regardless
        of the incoming timestamp's own suffix style."""
        conn = _fresh_conn()
        _seed_game(conn, 1, "EDM", "CGY", "2026-09-29")
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "cache.json"
            _write_cache(cache_path, [{
                "home_team_abbrev": "EDM", "away_team_abbrev": "CGY",
                "commence_time_utc": "2026-09-29T23:00:00Z", "home_price": -150.0, "away_price": 130.0,
                "captured_at_utc": "2026-09-24T12:00:52Z", "snapshot_label": "morning",
            }])
            rob.sync_moneyline_odds_to_snapshots(conn=conn, cache_path=cache_path)
        row = conn.execute(
            "SELECT event_start_utc, captured_at_utc, received_at_utc FROM odds_snapshots WHERE game_id=1 LIMIT 1"
        ).fetchone()
        for field in ("event_start_utc", "captured_at_utc", "received_at_utc"):
            value = row[field]
            self.assertNotIn("Z", value, f"{field}={value!r} still Z-suffixed")
            self.assertNotIn("+00:00", value, f"{field}={value!r} still carries an explicit UTC offset")
        # both sides of a later subtraction must now be safely comparable
        import datetime as dt
        as_of = dt.datetime.fromisoformat("2026-09-29T22:30:00")  # naive, matches this project's convention
        captured = dt.datetime.fromisoformat(row["captured_at_utc"])
        (as_of - captured)  # must not raise TypeError

    def test_unmatched_row_is_skipped_not_an_error(self):
        conn = _fresh_conn()
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "cache.json"
            _write_cache(cache_path, [{
                "home_team_abbrev": "EDM", "away_team_abbrev": "CGY",
                "commence_time_utc": "2026-09-29T23:00:00Z", "home_price": -150.0, "away_price": 130.0,
                "captured_at_utc": "2026-09-24T12:00:00Z", "snapshot_label": "morning",
            }])
            summary = rob.sync_moneyline_odds_to_snapshots(conn=conn, cache_path=cache_path)
        self.assertEqual(summary["status"], "SUCCESS")
        self.assertEqual(summary["rows_skipped_unmatched"], 1)
        self.assertEqual(summary["rows_written"], 0)

    def test_missing_cache_file_is_skipped_not_a_failure(self):
        conn = _fresh_conn()
        with tempfile.TemporaryDirectory() as tmp:
            summary = rob.sync_moneyline_odds_to_snapshots(conn=conn, cache_path=Path(tmp) / "nope.json")
        self.assertEqual(summary["status"], "SKIPPED")

    def test_rerun_with_same_data_is_idempotent(self):
        conn = _fresh_conn()
        _seed_game(conn, 1, "EDM", "CGY", "2026-09-29")
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "cache.json"
            rows = [{
                "home_team_abbrev": "EDM", "away_team_abbrev": "CGY",
                "commence_time_utc": "2026-09-29T23:00:00Z", "home_price": -150.0, "away_price": 130.0,
                "captured_at_utc": "2026-09-24T12:00:00Z", "snapshot_label": "morning",
            }]
            _write_cache(cache_path, rows)
            first = rob.sync_moneyline_odds_to_snapshots(conn=conn, cache_path=cache_path)
            second = rob.sync_moneyline_odds_to_snapshots(conn=conn, cache_path=cache_path)
        self.assertEqual(first["rows_written"], 2)
        self.assertEqual(second["rows_written"], 0)
        total = conn.execute("SELECT COUNT(*) c FROM odds_snapshots").fetchone()["c"]
        self.assertEqual(total, 2)  # never duplicated

    def test_a_later_different_snapshot_adds_new_rows_not_a_duplicate(self):
        """Two real captures at different times for the same game (e.g.
        the 08:00 and 13:00 moneyline runs) must both be preserved as
        real price history -- this is exactly what CLV needs later."""
        conn = _fresh_conn()
        _seed_game(conn, 1, "EDM", "CGY", "2026-09-29")
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "cache.json"
            _write_cache(cache_path, [{
                "home_team_abbrev": "EDM", "away_team_abbrev": "CGY",
                "commence_time_utc": "2026-09-29T23:00:00Z", "home_price": -150.0, "away_price": 130.0,
                "captured_at_utc": "2026-09-24T12:00:00Z", "snapshot_label": "morning",
            }])
            rob.sync_moneyline_odds_to_snapshots(conn=conn, cache_path=cache_path)
            _write_cache(cache_path, [{
                "home_team_abbrev": "EDM", "away_team_abbrev": "CGY",
                "commence_time_utc": "2026-09-29T23:00:00Z", "home_price": -160.0, "away_price": 140.0,
                "captured_at_utc": "2026-09-24T17:00:00Z", "snapshot_label": "afternoon",
            }])
            rob.sync_moneyline_odds_to_snapshots(conn=conn, cache_path=cache_path)
        total = conn.execute("SELECT COUNT(*) c FROM odds_snapshots WHERE game_id=1").fetchall()[0]["c"]
        self.assertEqual(total, 4)  # 2 captures x 2 sides


if __name__ == "__main__":
    unittest.main()
