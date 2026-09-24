"""
Real Recommendation Pipeline block (2026-09-24), Parts 17-19: Closed-Loop
Certification v2 -- extends tests/test_closed_loop_certification.py (v1,
P0.4) to prove the piece v1 could not yet reach: a REAL-SHAPED market
payload actually flowing through the REAL orchestration path this block
built, start to finish:

    real Odds-API-shaped cache payload
      -> operational.real_odds_bridge (writes odds_snapshots)
      -> operational.real_recommendation_orchestrator
         (run_slate.build_prediction_for_game -- the EXISTING model --
          + pricing.engine.evaluate_moneyline_for_game -- the EXISTING
          decision engine)
      -> immutable prospective-ledger snapshot
      -> paper bet (REAL_MARKET_PAPER) when BET
      -> official game result
      -> operational.settle_daily_observations (now with the real
         closing-price lookup wired in, Parts 14-16)
      -> CLV populated from a REAL later archived snapshot
      -> operational.daily_model_review (post-mortem) runs against it

v1 certified every one of these components in isolation except the
bridge and the real orchestrator (they didn't exist yet) and reported
CLV as PARTIAL (computed correctly but never wired into the automated
job). This file proves the FULL chain now connects, using entirely
isolated temp/in-memory databases -- never the real nhl.db,
prospective_observations.db, or paper_bankroll.db.

Unit-level coverage for WAIT / PASS / DATA_UNAVAILABLE / unverified
contract / duplicate rerun / market-price-changed (MARKET_REFRESH) already
exists in tests/test_real_recommendation_orchestrator.py and
tests/test_real_odds_bridge.py and is not re-proven here -- this file's
job is the end-to-end chain and the settlement/CLV/postmortem tail v1
could not reach.
"""
from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from operational import daily_model_review as dmr
from operational import paper_bankroll
from operational import prospective_ledger as pl
from operational import real_odds_bridge as rob
from operational import real_recommendation_orchestrator as orch
from operational import settle_daily_observations as sdo
from tests.helpers import Fixture, make_test_db, t


def _write_cache(path: Path, rows: list[dict]) -> None:
    path.write_text(json.dumps({"rows": rows}))


class TestFullChainBetWinAndClv(unittest.TestCase):
    """The flagship v2 scenario: TOR (home, +150) is a real BET; BOS
    (away, -170) is a real PASS. TOR goes on to win. Both settle through
    the real chain, and TOR's real BET gets a real CLV number from a
    real, later archived DraftKings snapshot -- the exact gap v1 reported
    as PARTIAL."""

    def setUp(self):
        self.nhl_conn, self.nhl_path = make_test_db()
        self.fx = Fixture(self.nhl_conn)
        self.fx.set_goalie_status(1, "TOR", "TOR_G1", "CONFIRMED", t(-30))
        self.fx.set_goalie_status(1, "BOS", "BOS_G1", "CONFIRMED", t(-30))

        self.pl_conn = pl.init_db(db_path=":memory:")
        fd, bpath = tempfile.mkstemp(suffix=".db")
        Path(bpath).unlink()
        self.bankroll_path = Path(bpath)
        self.bankroll_conn = paper_bankroll.init_db(self.bankroll_path)

        fd2, cpath = tempfile.mkstemp(suffix=".json")
        Path(cpath).unlink()
        self.cache_path = Path(cpath)

    def tearDown(self):
        self.nhl_conn.close()
        self.nhl_path.unlink(missing_ok=True)
        self.pl_conn.close()
        self.bankroll_conn.close()
        self.bankroll_path.unlink(missing_ok=True)
        self.cache_path.unlink(missing_ok=True)

    def _sync_and_orchestrate(self, sync_now: str):
        # real_odds_bridge stamps received_at_utc from wall-clock "now"
        # by default; features/point_in_time.py's own receipt-time
        # integrity guard (tests/test_odds_receipt_time_integrity.py)
        # requires received_at_utc <= prediction_time_utc, which holds
        # naturally in real production (both are near real "now") but
        # never against this fixture's deliberately backdated 2025 game
        # against the REAL current wall clock (2026+) -- so `now` is
        # pinned to this fixture's own timeline instead.
        bridge_summary = rob.sync_moneyline_odds_to_snapshots(
            conn=self.nhl_conn, cache_path=self.cache_path,
            now=dt.datetime.fromisoformat(sync_now))
        orch_summary = orch.run_real_moneyline_recommendations(
            conn=self.nhl_conn, game_ids=[1], pl_conn=self.pl_conn, bankroll_conn=self.bankroll_conn)
        return bridge_summary, orch_summary

    def test_full_chain_produces_a_real_bet_settles_win_and_attaches_real_clv(self):
        # Step 1: a real-shaped opening DraftKings payload, captured
        # within the dynamic staleness window for this fixture's fixed
        # prediction_time_utc (18:30, i.e. 30 min before the 19:00 puck
        # drop -- see config.ODDS_STALENESS_TIERS).
        _write_cache(self.cache_path, [{
            "home_team_abbrev": "TOR", "away_team_abbrev": "BOS",
            "commence_time_utc": self.fx.scheduled_start + "Z",
            "home_price": 150.0, "away_price": -170.0,
            "captured_at_utc": t(10, hour=18, minute=25), "snapshot_label": "T-5",
        }])
        bridge_summary, orch_summary = self._sync_and_orchestrate(t(10, hour=18, minute=26))

        self.assertEqual(bridge_summary["status"], "SUCCESS")
        self.assertEqual(bridge_summary["rows_written"], 2)
        self.assertEqual(orch_summary["status"], "SUCCESS")
        self.assertEqual(orch_summary["recommendations_recorded"], 2)  # TOR (BET) + BOS (PASS)
        self.assertEqual(orch_summary["paper_bets_created"], 1)

        tor_pred = self.pl_conn.execute(
            "SELECT * FROM predictions WHERE game_id='1' AND side='TOR'").fetchone()
        self.assertIsNotNone(tor_pred)
        self.assertEqual(tor_pred["prospective_status"], "BET")
        before = dict(tor_pred)

        bets = paper_bankroll.query_paper_bets(self.bankroll_conn, track="REAL_MARKET_PAPER")
        self.assertEqual(len(bets), 1)
        self.assertEqual(bets[0]["team"], "TOR")

        # Step 2: a REAL, LATER DraftKings snapshot -- the closing line --
        # still strictly before the 19:00 puck drop. Real intraday line
        # movement: TOR shortens slightly as it draws closer to game time.
        _write_cache(self.cache_path, [{
            "home_team_abbrev": "TOR", "away_team_abbrev": "BOS",
            "commence_time_utc": self.fx.scheduled_start + "Z",
            "home_price": 140.0, "away_price": -160.0,
            "captured_at_utc": t(10, hour=18, minute=55), "snapshot_label": "close",
        }])
        rob.sync_moneyline_odds_to_snapshots(conn=self.nhl_conn, cache_path=self.cache_path)

        # Step 3: the real official result -- TOR (home) wins.
        self.fx.finalize_game(1, home_score=4, away_score=1)

        # Step 4: real settlement, now with the real closing-price lookup
        # wired in (Parts 14-16).
        settlement_summary = sdo.run_settlement_batch(self.pl_conn, official_conn=self.nhl_conn)
        self.assertEqual(settlement_summary["settled_win"], 1)   # TOR
        self.assertEqual(settlement_summary["settled_loss"], 1)  # BOS

        after = dict(self.pl_conn.execute(
            "SELECT * FROM predictions WHERE game_id='1' AND side='TOR'").fetchone())
        self.assertEqual(after["result_status"], "WIN")
        self.assertEqual(after["closing_odds"], 140.0)
        self.assertIsNotNone(after["clv"])
        # entry +150 (implied ~40.0%) -> close +140 (implied ~41.7%): the
        # market moved TOWARD the bettor's side (a shorter closing price
        # on the same team means the market came to agree more with the
        # bet) -- CLV = closing_implied_prob - entry_implied_prob must be
        # POSITIVE, the industry-standard definition of "beating the close".
        self.assertGreater(after["clv"], 0)

        # Part 19: every prediction-time field is byte-for-byte
        # unchanged across settlement; only the settlement columns differ.
        settlement_fields = {"result_status", "actual_outcome", "settled_at_utc", "profit_loss",
                              "closing_odds", "closing_captured_at_utc", "clv", "notes"}
        for key in before:
            if key in settlement_fields:
                continue
            self.assertEqual(before[key], after[key], f"prediction field {key!r} changed during settlement")

        # Step 5: the real daily post-mortem runs against this real,
        # settled data without error -- with only 2 real settled
        # observations (below MIN_SAMPLE_FOR_REVIEW), it honestly reports
        # an insufficient-sample state rather than a fabricated verdict.
        review = dmr.run_daily_review(self.pl_conn)
        self.assertIn(review["engine_status"], ("NO_DATA", "INSUFFICIENT_SAMPLE"))

    def test_rerunning_the_full_chain_is_idempotent_end_to_end(self):
        _write_cache(self.cache_path, [{
            "home_team_abbrev": "TOR", "away_team_abbrev": "BOS",
            "commence_time_utc": self.fx.scheduled_start + "Z",
            "home_price": 150.0, "away_price": -170.0,
            "captured_at_utc": t(10, hour=18, minute=25), "snapshot_label": "T-5",
        }])
        first_bridge, first_orch = self._sync_and_orchestrate(t(10, hour=18, minute=26))
        second_bridge, second_orch = self._sync_and_orchestrate(t(10, hour=18, minute=27))

        self.assertEqual(first_bridge["rows_written"], 2)
        self.assertEqual(second_bridge["rows_written"], 0)
        self.assertEqual(first_orch["paper_bets_created"], 1)
        self.assertEqual(second_orch["paper_bets_created"], 0)

        self.fx.finalize_game(1, home_score=4, away_score=1)
        first_settle = sdo.run_settlement_batch(self.pl_conn, official_conn=self.nhl_conn)
        second_settle = sdo.run_settlement_batch(self.pl_conn, official_conn=self.nhl_conn)
        self.assertEqual(first_settle["settled_win"], 1)
        self.assertEqual(second_settle["total_candidates"], 0)

        total_predictions = self.pl_conn.execute("SELECT COUNT(*) c FROM predictions").fetchone()["c"]
        self.assertEqual(total_predictions, 2)  # TOR + BOS, never duplicated across two full runs
        total_bets = self.bankroll_conn.execute("SELECT COUNT(*) c FROM paper_bets").fetchone()["c"]
        self.assertEqual(total_bets, 1)

    def test_a_single_synced_snapshot_honestly_serves_as_its_own_closing_price(self):
        """Real, honest edge case (not a bug): if only ONE real
        DraftKings snapshot was ever archived for this side (the entry
        snapshot itself), it IS the latest real observation strictly
        before puck drop -- and therefore genuinely IS the correct close,
        by the same "latest valid pre-game observation" rule Part 14
        specifies. CLV is then a real, computed 0.0 (entry == close),
        never a fabricated placeholder."""
        _write_cache(self.cache_path, [{
            "home_team_abbrev": "TOR", "away_team_abbrev": "BOS",
            "commence_time_utc": self.fx.scheduled_start + "Z",
            "home_price": 150.0, "away_price": -170.0,
            "captured_at_utc": t(10, hour=18, minute=25), "snapshot_label": "T-5",
        }])
        self._sync_and_orchestrate(t(10, hour=18, minute=26))
        self.fx.finalize_game(1, home_score=4, away_score=1)
        sdo.run_settlement_batch(self.pl_conn, official_conn=self.nhl_conn)

        after = dict(self.pl_conn.execute(
            "SELECT * FROM predictions WHERE game_id='1' AND side='TOR'").fetchone())
        self.assertEqual(after["result_status"], "WIN")
        self.assertEqual(after["closing_odds"], 150.0)
        self.assertEqual(after["clv"], 0.0)

    def test_no_archived_price_history_at_all_settles_with_clv_unavailable_not_fabricated(self):
        """Part 16: the genuinely no-real-close case -- the archived
        DraftKings history for this side is gone by settlement time (a
        real operational edge case: retention/purge, or a row that was
        never successfully archived). CLV must stay genuinely
        unavailable, never a fabricated 0.0 and never a silent reuse of
        the entry price as if it had been observed again at close."""
        _write_cache(self.cache_path, [{
            "home_team_abbrev": "TOR", "away_team_abbrev": "BOS",
            "commence_time_utc": self.fx.scheduled_start + "Z",
            "home_price": 150.0, "away_price": -170.0,
            "captured_at_utc": t(10, hour=18, minute=25), "snapshot_label": "T-5",
        }])
        self._sync_and_orchestrate(t(10, hour=18, minute=26))
        self.nhl_conn.execute("DELETE FROM odds_snapshots WHERE selection = 'TOR'")
        self.nhl_conn.commit()
        self.fx.finalize_game(1, home_score=4, away_score=1)
        sdo.run_settlement_batch(self.pl_conn, official_conn=self.nhl_conn)

        after = dict(self.pl_conn.execute(
            "SELECT * FROM predictions WHERE game_id='1' AND side='TOR'").fetchone())
        self.assertEqual(after["result_status"], "WIN")
        self.assertIsNone(after["closing_odds"])
        self.assertIsNone(after["clv"])  # never fabricated


if __name__ == "__main__":
    unittest.main()
