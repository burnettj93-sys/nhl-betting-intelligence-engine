"""
Real Recommendation Pipeline block (2026-09-24), Parts 2/12/13: tests
for dashboard/real_recommendations_view.py -- the real, non-demo
counterpart to dashboard/eligible_bets.py. All isolated temp databases;
never the real prospective_observations.db or paper_bankroll.db.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dashboard import eligible_bets
from dashboard import real_recommendations_view as rrv
from dashboard.live_dk import LIVE_SOURCE_LABEL, SIMULATED_SOURCE_LABEL
from operational import paper_bankroll as pb
from operational import prospective_ledger as pl

EVENT_START = "2026-10-15T23:00:00.000000Z"
CUTOFF = "2026-10-15T18:00:00.000000Z"


def _tmp_pl_conn():
    fd, path = tempfile.mkstemp(suffix=".db")
    Path(path).unlink()
    return pl.init_db(Path(path)), Path(path)


def _tmp_bankroll_conn():
    fd, path = tempfile.mkstemp(suffix=".db")
    Path(path).unlink()
    return pb.init_db(Path(path)), Path(path)


class TestRealMoneylineRecommendations(unittest.TestCase):
    def setUp(self):
        self.pl_conn, self.pl_path = _tmp_pl_conn()
        self.bankroll_conn, self.bankroll_path = _tmp_bankroll_conn()

    def tearDown(self):
        self.pl_conn.close()
        self.pl_path.unlink(missing_ok=True)
        self.bankroll_conn.close()
        self.bankroll_path.unlink(missing_ok=True)

    def _record(self, side, prospective_status):
        return pl.record_model_observation(
            self.pl_conn, event_start_utc=EVENT_START, created_at_utc=CUTOFF,
            prediction_cutoff_utc=CUTOFF, game_id="1", game_date="2026-10-15", team=side,
            opponent="CHI" if side == "EDM" else "EDM", market_id="MONEYLINE", side=side,
            raw_probability=0.6, conservative_probability=0.55, odds_american=-150,
            sportsbook="DraftKings", prospective_status=prospective_status)

    def test_a_bet_row_is_labeled_with_the_canonical_live_draftkings_label(self):
        self._record("EDM", "BET")
        pb.record_paper_bet(self.bankroll_conn, track="REAL_MARKET_PAPER", price_source="LIVE_DRAFTKINGS",
                             market_id="MONEYLINE", entry_odds=-150, event_id="1", team="EDM")
        rows = rrv.real_moneyline_recommendations(pl_conn=self.pl_conn, bankroll_conn=self.bankroll_conn)
        edm = next(r for r in rows if r["team"] == "EDM")
        self.assertEqual(edm["source"], LIVE_SOURCE_LABEL)
        self.assertFalse(edm["is_demo"])
        self.assertTrue(edm["has_real_paper_bet"])

    def test_a_pass_row_never_gets_the_live_label(self):
        self._record("CHI", "PASS")
        rows = rrv.real_moneyline_recommendations(pl_conn=self.pl_conn, bankroll_conn=self.bankroll_conn)
        chi = next(r for r in rows if r["team"] == "CHI")
        self.assertNotEqual(chi["source"], LIVE_SOURCE_LABEL)
        self.assertIn("REAL MARKET", chi["source"])
        self.assertIn("PASS", chi["source"])
        self.assertFalse(chi["has_real_paper_bet"])

    def test_never_returns_a_demo_row(self):
        """dashboard/eligible_bets.py rows must never appear in this
        module's output, and vice versa -- distinct provenance, Part 12."""
        self._record("EDM", "BET")
        rows = rrv.real_moneyline_recommendations(pl_conn=self.pl_conn, bankroll_conn=self.bankroll_conn)
        for r in rows:
            self.assertFalse(r["is_demo"])
            self.assertNotEqual(r["source"], SIMULATED_SOURCE_LABEL)

    def test_filters_by_team_and_by_game(self):
        self._record("EDM", "BET")
        self._record("CHI", "PASS")
        rows = rrv.real_moneyline_recommendations(pl_conn=self.pl_conn, bankroll_conn=self.bankroll_conn)
        edm_only = rrv.real_moneyline_recommendations_for_team("EDM", rows)
        self.assertEqual([r["team"] for r in edm_only], ["EDM"])
        game_rows = rrv.real_moneyline_recommendations_for_game("EDM", "CHI", rows)
        self.assertEqual({r["team"] for r in game_rows}, {"EDM", "CHI"})


class TestZeroQualifyingBetsState(unittest.TestCase):
    def test_no_rows_at_all_is_no_qualifying_bets(self):
        self.assertEqual(rrv.real_market_summary_label([]), rrv.NO_QUALIFYING_BETS_LABEL)

    def test_rows_with_no_paper_bet_is_still_no_qualifying_bets(self):
        rows = [{"has_real_paper_bet": False}, {"has_real_paper_bet": False}]
        self.assertEqual(rrv.real_market_summary_label(rows), rrv.NO_QUALIFYING_BETS_LABEL)

    def test_a_real_qualifying_bet_is_reported_honestly(self):
        rows = [{"has_real_paper_bet": True}, {"has_real_paper_bet": False}]
        self.assertIn("1", rrv.real_market_summary_label(rows))
        self.assertNotEqual(rrv.real_market_summary_label(rows), rrv.NO_QUALIFYING_BETS_LABEL)


class TestDemoRowsAreLabeledSimulated(unittest.TestCase):
    """dashboard/eligible_bets.py's own existing demo rows -- confirms
    Part 2's labeling was added without breaking the demo path."""

    def test_player_prop_rows_carry_the_simulated_label(self):
        rows = eligible_bets.build_all_player_prop_opportunities()
        self.assertTrue(rows)
        for r in rows:
            self.assertEqual(r["source"], SIMULATED_SOURCE_LABEL)
            self.assertTrue(r["is_demo"])

    def test_goalie_saves_rows_carry_the_simulated_label(self):
        rows = eligible_bets.build_goalie_saves_opportunities()
        self.assertTrue(rows)
        for r in rows:
            self.assertEqual(r["source"], SIMULATED_SOURCE_LABEL)
            self.assertTrue(r["is_demo"])


if __name__ == "__main__":
    unittest.main()
