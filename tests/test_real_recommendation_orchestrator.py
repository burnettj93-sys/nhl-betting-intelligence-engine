"""
Real Recommendation Pipeline block (2026-09-24): tests for
operational/real_recommendation_orchestrator.py -- the module that
actually runs REAL market data through the EXISTING model
(run_slate.build_prediction_for_game) and EXISTING decision engine
(pricing/engine.py::evaluate_moneyline_for_game) and records the result
into the NEW ledger / paper bankroll.

All tests use tests.helpers.Fixture's isolated on-disk nhl-shaped DB plus
freshly-created temp prospective-ledger and paper-bankroll databases --
never the real nhl.db, prospective_observations.db, or paper_bankroll.db.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from operational import paper_bankroll
from operational import prospective_ledger as pl
from operational import real_recommendation_orchestrator as orch
from tests.helpers import Fixture, make_test_db, t


def _tmp_pl_conn():
    fd, path = tempfile.mkstemp(suffix=".db")
    Path(path).unlink()
    conn = pl.init_db(Path(path))
    return conn, Path(path)


def _tmp_bankroll_conn():
    fd, path = tempfile.mkstemp(suffix=".db")
    Path(path).unlink()
    conn = paper_bankroll.init_db(Path(path))
    return conn, Path(path)


class OrchestratorTestBase(unittest.TestCase):
    def setUp(self):
        self.conn, self.db_path = make_test_db()
        self.fx = Fixture(self.conn)
        self.pl_conn, self.pl_path = _tmp_pl_conn()
        self.bankroll_conn, self.bankroll_path = _tmp_bankroll_conn()

    def tearDown(self):
        self.conn.close()
        self.db_path.unlink(missing_ok=True)
        self.pl_conn.close()
        self.pl_path.unlink(missing_ok=True)
        self.bankroll_conn.close()
        self.bankroll_path.unlink(missing_ok=True)

    def _confirm_goalies(self):
        self.fx.set_goalie_status(1, "TOR", "TOR_G1", "CONFIRMED", t(-30))
        self.fx.set_goalie_status(1, "BOS", "BOS_G1", "CONFIRMED", t(-30))

    def _run(self, game_ids=(1,)):
        return orch.run_real_moneyline_recommendations(
            conn=self.conn, game_ids=list(game_ids), pl_conn=self.pl_conn, bankroll_conn=self.bankroll_conn)


class TestBetRecommendation(OrchestratorTestBase):
    """TOR (home) at +150 / BOS (away) at -170 against this fixture's
    real (unmodified) conservative_prob_home ~0.46 clears BET for TOR and
    stays PASS for BOS -- both computed above empirically against the
    real evaluate_moneyline_for_game(), never hand-picked to fake a BET."""

    def setUp(self):
        super().setUp()
        self._confirm_goalies()
        self.fx.add_odds(1, "TOR", 150, captured_at=t(10, hour=18, minute=25), label="T-5")
        self.fx.add_odds(1, "BOS", -170, captured_at=t(10, hour=18, minute=25), label="T-5")

    def test_bet_side_is_recorded_as_immutable_snapshot(self):
        summary = self._run()
        self.assertEqual(summary["status"], "SUCCESS")
        self.assertEqual(summary["recommendations_recorded"], 2)  # both TOR (BET) and BOS (PASS)
        rows = self.pl_conn.execute("SELECT * FROM predictions WHERE side='TOR'").fetchall()
        self.assertEqual(len(rows), 1)
        row = dict(rows[0])
        self.assertEqual(row["market_id"], "MONEYLINE")
        self.assertEqual(row["team"], "TOR")
        self.assertEqual(row["opponent"], "BOS")
        self.assertEqual(row["prediction_checkpoint"], "PRIMARY_DAILY")
        self.assertIsNotNone(row["conservative_probability"])
        self.assertEqual(row["odds_american"], 150)

    def test_bet_side_creates_a_real_market_paper_bet(self):
        summary = self._run()
        self.assertEqual(summary["paper_bets_created"], 1)
        bets = paper_bankroll.query_paper_bets(self.bankroll_conn, track="REAL_MARKET_PAPER")
        self.assertEqual(len(bets), 1)
        bet = bets[0]
        self.assertEqual(bet["team"], "TOR")
        self.assertEqual(bet["price_source"], "LIVE_DRAFTKINGS")
        self.assertEqual(bet["stake"], paper_bankroll.PAPER_BET_STAKE)

    def test_pass_side_is_recorded_but_creates_no_paper_bet(self):
        self._run()
        row = self.pl_conn.execute("SELECT * FROM predictions WHERE side='BOS'").fetchone()
        self.assertIsNotNone(row)
        bets = paper_bankroll.query_paper_bets(self.bankroll_conn, track="REAL_MARKET_PAPER")
        self.assertEqual([b for b in bets if b["team"] == "BOS"], [])

    def test_rerun_against_the_same_market_snapshot_is_idempotent(self):
        first = self._run()
        second = self._run()
        self.assertEqual(first["paper_bets_created"], 1)
        self.assertEqual(second["paper_bets_created"], 0)  # duplicate, not a second $10 bet
        total_predictions = self.pl_conn.execute("SELECT COUNT(*) c FROM predictions").fetchone()["c"]
        self.assertEqual(total_predictions, 2)  # TOR + BOS, never duplicated
        total_bets = self.bankroll_conn.execute("SELECT COUNT(*) c FROM paper_bets").fetchone()["c"]
        self.assertEqual(total_bets, 1)

    def test_a_later_real_price_change_creates_a_market_refresh_row_not_a_duplicate(self):
        self._run()
        # A real, later DraftKings snapshot at a new captured_at_utc.
        # run_slate's own prediction_time_for_game is fixed at exactly 30
        # minutes before puck drop (never "now") -- a genuinely later
        # real snapshot must still be captured AT OR BEFORE that fixed
        # cutoff to become the new latest-known price at that same
        # prediction time, exactly like a real intraday odds refresh
        # that lands before the model's own pricing anchor.
        self.fx.add_odds(1, "TOR", 145, captured_at=t(10, hour=18, minute=28), label="T-2")
        self.fx.add_odds(1, "BOS", -165, captured_at=t(10, hour=18, minute=28), label="T-2")
        second = self._run()
        self.assertGreaterEqual(second["recommendations_recorded"], 1)
        rows = self.pl_conn.execute(
            "SELECT prediction_checkpoint FROM predictions WHERE side='TOR' ORDER BY created_at_utc"
        ).fetchall()
        self.assertEqual([r["prediction_checkpoint"] for r in rows], ["PRIMARY_DAILY", "MARKET_REFRESH"])
        # still exactly one real paper bet -- the second snapshot's BET
        # recommendation is the SAME logical opportunity, not a new one
        bets = paper_bankroll.query_paper_bets(self.bankroll_conn, track="REAL_MARKET_PAPER")
        self.assertEqual(len(bets), 1)


class TestNoActionableBets(OrchestratorTestBase):
    def setUp(self):
        super().setUp()
        self._confirm_goalies()
        self.fx.add_odds(1, "TOR", -110, captured_at=t(10, hour=18, minute=25), label="T-5")
        self.fx.add_odds(1, "BOS", -110, captured_at=t(10, hour=18, minute=25), label="T-5")

    def test_zero_bets_is_a_valid_healthy_result(self):
        summary = self._run()
        self.assertEqual(summary["status"], "SUCCESS")
        self.assertEqual(summary["paper_bets_created"], 0)
        self.assertEqual(summary["recommendations_recorded"], 2)  # both PASS, still recorded


class TestUnverifiedContract(OrchestratorTestBase):
    """No DraftKings odds at all for this game -- Part 4: fail closed,
    no real recommendation is ever produced or recorded."""

    def setUp(self):
        super().setUp()
        self._confirm_goalies()

    def test_no_market_data_produces_data_unavailable_and_records_nothing(self):
        summary = self._run()
        self.assertEqual(summary["data_unavailable"], 2)
        self.assertEqual(summary["recommendations_recorded"], 0)
        self.assertEqual(summary["paper_bets_created"], 0)
        total_predictions = self.pl_conn.execute("SELECT COUNT(*) c FROM predictions").fetchone()["c"]
        self.assertEqual(total_predictions, 0)


class TestGoalieNotConfirmed(OrchestratorTestBase):
    """No goalie confirmation events at all -- the existing decision
    engine's own goalie gate (pricing/engine.py) forces WAIT. WAIT still
    has a real market price, so it IS recorded as a real recommendation
    (context/readiness gap is honestly disclosed via the action itself),
    but never becomes a paper bet."""

    def setUp(self):
        super().setUp()
        self.fx.add_odds(1, "TOR", 150, captured_at=t(10, hour=18, minute=25), label="T-5")
        self.fx.add_odds(1, "BOS", -170, captured_at=t(10, hour=18, minute=25), label="T-5")

    def test_unconfirmed_goalies_wait_is_recorded_without_a_paper_bet(self):
        summary = self._run()
        self.assertEqual(summary["recommendations_recorded"], 2)
        self.assertEqual(summary["paper_bets_created"], 0)
        rows = self.pl_conn.execute("SELECT prospective_status FROM predictions").fetchall()
        self.assertTrue(all(r["prospective_status"] == "WAIT" for r in rows))


if __name__ == "__main__":
    unittest.main()
