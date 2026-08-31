"""
Tests for dashboard/live_dk.py (Live DK / Paper Bankroll completion
sprint, 2026-08-31). Covers: this module never spends an Odds API
credit (reads only from archived evidence, never imports the paid-call
client), the real MONEYLINE contract is the only thing it ever prices,
and the Elo-staleness gate (Part 20: never present a real-but-untrusted
edge as an actionable BET).
"""
from __future__ import annotations

import ast
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dashboard import live_dk as ldk

REPO_ROOT = Path(__file__).resolve().parent.parent


def _write_archive(tmp_dir: Path, filename: str, *, event_id: str, home_team: str, away_team: str,
                    commence_time: str, home_price: float, away_price: float,
                    last_update: str, retrieved_at_utc: str, bookmakers=None) -> None:
    response = {
        "id": event_id, "home_team": home_team, "away_team": away_team,
        "commence_time": commence_time,
        "bookmakers": bookmakers if bookmakers is not None else [
            {"key": "draftkings", "markets": [
                {"key": "h2h", "last_update": last_update,
                 "outcomes": [{"name": home_team, "price": home_price},
                              {"name": away_team, "price": away_price}]}]}],
    }
    payload = {"meta": {"retrieved_at_utc": retrieved_at_utc}, "response": response}
    with open(tmp_dir / filename, "w") as f:
        json.dump(payload, f)


class TestNeverSpendsACredit(unittest.TestCase):
    def test_never_imports_the_paid_call_client_module(self):
        tree = ast.parse((REPO_ROOT / "dashboard" / "live_dk.py").read_text())
        modules = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)] + \
                  [alias.name for n in ast.walk(tree) if isinstance(n, ast.Import) for alias in n.names]
        self.assertNotIn("research.live_sog_pricing.client", modules)


class TestLoadLatestVerifiedMoneylineMarkets(unittest.TestCase):
    def test_reads_a_real_shaped_archive_and_dedupes_to_the_latest(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_archive(tmp_path, "a_older.json", event_id="evt1", home_team="Carolina Hurricanes",
                            away_team="Florida Panthers", commence_time="2026-09-29T21:10:00Z",
                            home_price=-130, away_price=110, last_update="2026-08-30T00:00:00Z",
                            retrieved_at_utc="2026-08-30T00:00:00Z")
            _write_archive(tmp_path, "b_newer.json", event_id="evt1", home_team="Carolina Hurricanes",
                            away_team="Florida Panthers", commence_time="2026-09-29T21:10:00Z",
                            home_price=-140, away_price=120, last_update="2026-08-31T00:00:00Z",
                            retrieved_at_utc="2026-08-31T00:00:00Z")
            with mock.patch.object(ldk, "ARCHIVE_DIR", tmp_path):
                markets = ldk.load_latest_verified_moneyline_markets()
            self.assertEqual(len(markets), 1)
            self.assertEqual(markets["evt1"]["market"].home_price, -140.0)

    def test_player_prop_only_archives_with_no_bookmakers_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_archive(tmp_path, "empty.json", event_id="evt2", home_team="Boston Bruins",
                            away_team="New York Rangers", commence_time="2026-09-30T00:10:00Z",
                            home_price=0, away_price=0, last_update="x", retrieved_at_utc="2026-08-30T00:00:00Z",
                            bookmakers=[])
            with mock.patch.object(ldk, "ARCHIVE_DIR", tmp_path):
                markets = ldk.load_latest_verified_moneyline_markets()
            self.assertEqual(markets, {})

    def test_missing_archive_dir_returns_empty_not_a_crash(self):
        with mock.patch.object(ldk, "ARCHIVE_DIR", Path("/nonexistent/path/xyz")):
            self.assertEqual(ldk.load_latest_verified_moneyline_markets(), {})


class TestBuildLiveMoneylineComparisons(unittest.TestCase):
    def test_every_row_is_labeled_live_draftkings_never_simulated(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_archive(tmp_path, "a.json", event_id="evt1", home_team="Carolina Hurricanes",
                            away_team="Florida Panthers", commence_time="2026-09-29T21:10:00Z",
                            home_price=-130, away_price=110, last_update="2026-08-31T12:37:46Z",
                            retrieved_at_utc="2026-08-31T12:38:09Z")
            with mock.patch.object(ldk, "ARCHIVE_DIR", tmp_path):
                rows = ldk.build_live_moneyline_comparisons()
            self.assertTrue(all(r["source"] == ldk.LIVE_SOURCE_LABEL for r in rows))
            self.assertTrue(all(r["source"] != ldk.SIMULATED_SOURCE_LABEL for r in rows))

    def test_real_archived_evidence_never_raises(self):
        # Exercises the actual archive this sprint's real probe wrote --
        # proves the whole path against real, not synthetic, data.
        rows = ldk.build_live_moneyline_comparisons()
        self.assertIsInstance(rows, list)

    def test_staleness_gate_forces_wait_never_bet_on_a_stale_rating(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # A game far beyond the corpus's last real date -- must never
            # produce a BET regardless of how large the raw edge is.
            _write_archive(tmp_path, "a.json", event_id="evt1", home_team="Carolina Hurricanes",
                            away_team="Florida Panthers", commence_time="2026-09-29T21:10:00Z",
                            home_price=-130, away_price=110, last_update="2026-08-31T12:37:46Z",
                            retrieved_at_utc="2026-08-31T12:38:09Z")
            with mock.patch.object(ldk, "ARCHIVE_DIR", tmp_path):
                rows = ldk.build_live_moneyline_comparisons()
            self.assertTrue(all(r["decision"] != "BET" for r in rows))
            self.assertTrue(all(r["decision"] == "WAIT" for r in rows if r["status"] == "PRICED"))
            self.assertTrue(all("stale" in r["decision_reason"].lower() for r in rows if r["status"] == "PRICED"))

    def test_staleness_gate_does_not_trip_for_a_fresh_rating(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with mock.patch.object(ldk, "_elo_corpus_staleness_days", return_value=1.0):
                _write_archive(tmp_path, "a.json", event_id="evt1", home_team="Carolina Hurricanes",
                                away_team="Florida Panthers", commence_time="2026-09-29T21:10:00Z",
                                home_price=-130, away_price=110, last_update="2026-08-31T12:37:46Z",
                                retrieved_at_utc="2026-08-31T12:38:09Z")
                with mock.patch.object(ldk, "ARCHIVE_DIR", tmp_path):
                    rows = ldk.build_live_moneyline_comparisons()
            for r in rows:
                self.assertNotIn("stale", (r.get("decision_reason") or "").lower())

    def test_unresolvable_team_produces_data_unavailable_never_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_archive(tmp_path, "a.json", event_id="evt1", home_team="Utah Mammoth",
                            away_team="Florida Panthers", commence_time="2026-09-29T21:10:00Z",
                            home_price=-130, away_price=110, last_update="2026-08-31T12:37:46Z",
                            retrieved_at_utc="2026-08-31T12:38:09Z")
            with mock.patch.object(ldk, "ARCHIVE_DIR", tmp_path), \
                 mock.patch("dashboard.game_detail_view.demo_win_model", return_value=None):
                rows = ldk.build_live_moneyline_comparisons()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["status"], "DATA_UNAVAILABLE")

    def test_two_sided_no_vig_used_never_fabricates_opposing_side(self):
        rows = ldk.build_live_moneyline_comparisons()
        for r in rows:
            if r["status"] == "PRICED":
                self.assertIsNotNone(r["market_no_vig_probability"])


if __name__ == "__main__":
    unittest.main()
