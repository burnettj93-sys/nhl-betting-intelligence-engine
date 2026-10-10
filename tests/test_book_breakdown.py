"""The model book is a parlay experiment: automatic single bets (moneyline, props) are not recorded in it any more, the earlier ones stay on the record, and the account is reported as
parlay tickets + single bets = the whole, so nothing is reset and the pieces can be checked against the total."""
from __future__ import annotations

import os
import unittest
from unittest import mock

from operational import paper_bankroll as pb
from operational import real_prop_orchestrator as rpo
from operational import watchdog as wd
from tests.test_daily_tickets import board, fresh_ledger
from operational import daily_tickets as dtk
from tests.test_daily_tickets import NOW, collected

SINGLE = dict(track="REAL_MARKET_PAPER", price_source="LIVE_DRAFTKINGS", market_id="MONEYLINE", entry_odds=195.0, event_id="g1", game_date="2026-10-10", team="CGY", opponent="COL",
              market_family="MONEYLINE", side="CGY", model_probability=0.49, conservative_probability=0.37, market_no_vig_probability=0.32, edge=0.05, ev=0.1, model_version="t",
              prediction_checkpoint="PRIMARY_DAILY", event_start_utc="2026-10-10T23:00:00Z")


class Base(unittest.TestCase):
    def setUp(self):
        self.path, self.conn = fresh_ledger()
        self.addCleanup(self.conn.close)


class TestSinglesAreOutOfTheExperiment(Base):
    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, {pb.ENV_ALLOW_SINGLES: "0"})
        env.start()
        self.addCleanup(env.stop)

    def test_an_automatic_single_bet_is_not_recorded(self):
        res = pb.record_paper_bet(self.conn, **SINGLE)
        self.assertEqual(res["status"], "EXCLUDED_SINGLE")
        self.assertIn("parlay experiment", res["reason"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM paper_bets").fetchone()[0], 0)

    def test_parlay_tickets_are_still_recorded(self):
        res = dtk.run_cycle(None, self.conn, NOW, collected=collected(board(8)))
        self.assertEqual(res["newly_recorded"], 5)

    def test_the_prop_job_path_records_its_observation_but_no_bet(self):
        with mock.patch.object(rpo.pr, "record_observation", return_value={"status": "INSERTED", "prediction_id": "p1"}):
            out = rpo._record_and_maybe_paper_bet(None, self.conn, prediction={"market_id": "SOG", "odds_american": 120.0, "game_id": "g", "game_date": "2026-10-10", "market_family": "PLAYER_SOG_ALTERNATE",
                                                                                "threshold": 2, "side": "OVER", "raw_probability": .5, "conservative_probability": .4, "event_start_utc": "2026-10-10T23:00:00Z"},
                                                  priced={"action": "BET", "conservative_edge": .05, "conservative_ev": .1}, checkpoint="PRIMARY_DAILY")
        self.assertEqual((out["status"], out["paper_bet_created"]), ("INSERTED", False))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM paper_bets").fetchone()[0], 0)

    def test_an_earlier_single_bet_already_in_the_ledger_is_untouched_by_the_rule(self):
        with mock.patch.dict(os.environ, {pb.ENV_ALLOW_SINGLES: "1"}):
            self.assertEqual(pb.record_paper_bet(self.conn, **SINGLE)["status"], "INSERTED")      # how it got there, before the rule
        before = [tuple(r) for r in self.conn.execute("SELECT * FROM paper_bets")]
        dtk.run_cycle(None, self.conn, NOW, collected=collected(board(8)))
        self.assertEqual([tuple(r) for r in self.conn.execute("SELECT * FROM paper_bets WHERE is_combo = 0")], before)

    def test_the_exclusion_cannot_be_switched_off_outside_a_test_run(self):
        with mock.patch.dict(os.environ, {pb.ENV_ALLOW_SINGLES: "1"}):
            with mock.patch("operational.state_paths.under_test", return_value=False):
                self.assertTrue(pb.singles_excluded())


class TestTheAccountAddsUp(Base):
    def setUp(self):
        super().setUp()
        with mock.patch.dict(os.environ, {pb.ENV_ALLOW_SINGLES: "1"}):
            dtk.run_cycle(None, self.conn, NOW, collected=collected(board(4)))                 # parlay tickets
            self.assertEqual(pb.record_paper_bet(self.conn, **SINGLE)["status"], "INSERTED")   # one earlier single
        rows = self.conn.execute("SELECT paper_bet_id, is_combo FROM paper_bets ORDER BY created_at_utc").fetchall()
        outcomes = iter(["WIN", "LOSS", "LOSS", "LOSS"])
        for r in rows:
            if r["is_combo"]:
                st = next(outcomes, "LOSS")
                self.conn.execute("UPDATE paper_bets SET result_status = ?, profit_loss = ?, settled_at_utc = '2026-10-11T00:00:00Z' WHERE paper_bet_id = ?", (st, 20.5 if st == "WIN" else -10.0, r["paper_bet_id"]))
            else:
                self.conn.execute("UPDATE paper_bets SET result_status = 'LOSS', profit_loss = -10.0, settled_at_utc = '2026-10-11T00:00:00Z' WHERE paper_bet_id = ?", (r["paper_bet_id"],))
        self.conn.commit()

    def test_the_parlay_tickets_and_the_single_bets_add_up_to_the_whole_and_to_cash(self):
        b = pb.book_breakdown(self.conn)
        c, p, s = b["combined"], b["parlay_tickets"], b["single_bets"]
        self.assertEqual((p["bets"], s["bets"], c["bets"]), (len(list(self.conn.execute("SELECT 1 FROM paper_bets WHERE is_combo = 1"))), 1, p["bets"] + 1))
        self.assertAlmostEqual(p["settled_pnl"] + s["settled_pnl"], c["settled_pnl"], places=2)
        self.assertTrue(b["reconciliation"]["all_agree"], b["reconciliation"]["checks"])
        self.assertEqual(b["reconciliation"]["available_cash"], pb.account_state(self.conn, "REAL_MARKET_PAPER")["available_cash"])
        self.assertEqual(s["settled_pnl"], -10.0)
        self.assertEqual(b["single_bets_by_kind"]["moneyline"]["bets"], 1)
        self.assertEqual(b["single_bets_by_kind"]["props"]["bets"], 0)

    def test_origin_performance_publishes_the_split_without_changing_the_whole(self):
        o = pb.origin_performance(self.conn)
        self.assertEqual(o["ALL"]["tickets"], o["PARLAY_TICKETS"]["tickets"] + o["SINGLE_BETS"]["tickets"])
        self.assertEqual(o["ALL"], o["AUTOMATIC"])
        self.assertIn("parlay tickets", o["RECONCILIATION"]["sentence"])

    def test_nothing_is_reset_the_account_is_still_the_combined_one(self):
        acct = pb.account_state(self.conn, "REAL_MARKET_PAPER")
        b = pb.book_breakdown(self.conn)["combined"]
        self.assertEqual(acct["settled_pnl"], b["settled_pnl"])
        self.assertEqual(acct["tickets"], b["bets"])

    def test_the_watchdog_flags_a_split_that_does_not_add_up(self):
        import sqlite3
        import tempfile
        from operational import personal_logs as pl
        pconn = pl.connect(tempfile.NamedTemporaryFile(suffix=".db", delete=False).name)
        ok = wd.check_reconciliation(self.conn, pconn)
        self.assertEqual(ok["status"], wd.OK, ok["detail"])
        self.assertIn("single bets", ok["detail"])
        with mock.patch.object(pb, "book_breakdown", return_value={"reconciliation": {"all_agree": False, "checks": {"pnl": False}, "parlay_pnl": 0, "single_bets_pnl": 0}}):
            bad = wd.check_reconciliation(self.conn, pconn)
        self.assertEqual(bad["status"], wd.FAIL)
        self.assertIn("does not add up", bad["detail"])


if __name__ == "__main__":
    unittest.main()
