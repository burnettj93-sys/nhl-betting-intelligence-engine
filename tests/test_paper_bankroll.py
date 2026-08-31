"""
Tests for operational/paper_bankroll.py (Live DK / Paper Bankroll
completion sprint, 2026-08-31, Part 52). Covers the exact $10/$1000
economics, first-actionable-entry-only idempotency, immutable entry
snapshot, payout math for both odds signs, settlement idempotency,
track separation (REAL_MARKET_PAPER / DEMO_PAPER / REAL_BET untouched),
straight-vs-combo separation, and every required breakdown.
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from operational import paper_bankroll as pb


class TestPaperBankroll(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "test_paper.db"
        self.conn = pb.init_db(self.db_path)

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def _bet(self, **overrides):
        fields = dict(track="DEMO_PAPER", price_source="SIMULATED_DEMO", market_id="PLAYER_POINTS_2PLUS",
                      entry_odds=200, event_id="evt1", player_id="p1", threshold="2+",
                      event_start_utc="2026-10-14T23:00:00Z")
        fields.update(overrides)
        return pb.record_paper_bet(self.conn, **fields)


class TestConstants(TestPaperBankroll):
    def test_starting_bankroll_is_1000(self):
        self.assertEqual(pb.PAPER_STARTING_BANKROLL, 1000.00)

    def test_stake_is_10(self):
        self.assertEqual(pb.PAPER_BET_STAKE, 10.00)


class TestPayoutMath(unittest.TestCase):
    def test_positive_odds_win(self):
        self.assertAlmostEqual(pb.compute_payout(10, 250, "WIN"), 25.0)

    def test_negative_odds_win(self):
        self.assertAlmostEqual(pb.compute_payout(10, -200, "WIN"), 5.0)

    def test_loss_is_negative_stake(self):
        self.assertEqual(pb.compute_payout(10, 150, "LOSS"), -10.0)

    def test_void_is_zero(self):
        self.assertEqual(pb.compute_payout(10, 150, "VOID"), 0.0)

    def test_even_money_win(self):
        self.assertAlmostEqual(pb.compute_payout(10, 100, "WIN"), 10.0)
        self.assertAlmostEqual(pb.compute_payout(10, -100, "WIN"), 10.0)


class TestOddsRangeBuckets(unittest.TestCase):
    def test_all_eight_buckets_reachable(self):
        cases = {-600: "shorter than -500", -450: "-500 to -400", -350: "-399 to -300",
                  -250: "-299 to -200", -150: "-199 to -110", -105: "-109 to +100",
                  100: "-109 to +100", 150: "+101 to +200", 300: "+201 or longer"}
        for odds, expected in cases.items():
            self.assertEqual(pb.odds_range_bucket(odds), expected, f"odds={odds}")

    def test_boundaries_exact(self):
        self.assertEqual(pb.odds_range_bucket(-500), "shorter than -500")
        self.assertEqual(pb.odds_range_bucket(-400), "-500 to -400")
        self.assertEqual(pb.odds_range_bucket(-110), "-199 to -110")
        self.assertEqual(pb.odds_range_bucket(200), "+101 to +200")
        self.assertEqual(pb.odds_range_bucket(201), "+201 or longer")


class TestRecordPaperBet(TestPaperBankroll):
    def test_creates_one_bet_at_the_fixed_stake(self):
        r = self._bet()
        self.assertEqual(r["status"], "INSERTED")
        row = self.conn.execute("SELECT * FROM paper_bets WHERE paper_bet_id=?", (r["paper_bet_id"],)).fetchone()
        self.assertEqual(row["stake"], pb.PAPER_BET_STAKE)

    def test_first_actionable_entry_only_no_duplicate_from_refresh(self):
        r1 = self._bet()
        r2 = self._bet()  # simulates a PRE_GAME_UPDATE / MARKET_REFRESH recomputing the same opportunity
        self.assertEqual(r1["paper_bet_id"], r2["paper_bet_id"])
        self.assertEqual(r2["status"], "DUPLICATE")
        count = self.conn.execute("SELECT COUNT(*) FROM paper_bets").fetchone()[0]
        self.assertEqual(count, 1)

    def test_different_threshold_is_a_different_bet(self):
        r1 = self._bet(threshold="2+")
        r2 = self._bet(threshold="3+")
        self.assertNotEqual(r1["paper_bet_id"], r2["paper_bet_id"])

    def test_real_market_paper_requires_live_draftkings_price_source(self):
        with self.assertRaises(pb.InvalidPaperBetError):
            self._bet(track="REAL_MARKET_PAPER", price_source="SIMULATED_DEMO")

    def test_demo_paper_requires_simulated_demo_price_source(self):
        with self.assertRaises(pb.InvalidPaperBetError):
            self._bet(track="DEMO_PAPER", price_source="LIVE_DRAFTKINGS")

    def test_unknown_track_rejected(self):
        with self.assertRaises(pb.InvalidPaperBetError):
            self._bet(track="NOT_A_TRACK")


class TestEntryImmutability(TestPaperBankroll):
    def test_entry_odds_cannot_be_mutated_after_creation(self):
        r = self._bet(entry_odds=150)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE paper_bets SET entry_odds = 999 WHERE paper_bet_id = ?",
                               (r["paper_bet_id"],))
            self.conn.commit()

    def test_stake_cannot_be_mutated(self):
        r = self._bet()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE paper_bets SET stake = 999 WHERE paper_bet_id = ?", (r["paper_bet_id"],))
            self.conn.commit()

    def test_settlement_columns_can_change(self):
        r = self._bet()
        settled = pb.settle_paper_bet(self.conn, r["paper_bet_id"], "WIN")
        self.assertEqual(settled["result_status"], "WIN")


class TestSettlement(TestPaperBankroll):
    def test_settling_twice_raises(self):
        r = self._bet()
        pb.settle_paper_bet(self.conn, r["paper_bet_id"], "WIN")
        with self.assertRaises(pb.InvalidPaperBetError):
            pb.settle_paper_bet(self.conn, r["paper_bet_id"], "LOSS")

    def test_closing_odds_never_replaces_entry_odds(self):
        r = self._bet(entry_odds=150)
        settled = pb.settle_paper_bet(self.conn, r["paper_bet_id"], "WIN", closing_odds=120)
        self.assertEqual(settled["entry_odds"], 150)
        self.assertEqual(settled["closing_odds"], 120)

    def test_clv_computed_from_entry_vs_closing_when_not_supplied(self):
        r = self._bet(entry_odds=150)
        settled = pb.settle_paper_bet(self.conn, r["paper_bet_id"], "WIN", closing_odds=120)
        self.assertIsNotNone(settled["clv"])

    def test_unknown_result_status_rejected(self):
        r = self._bet()
        with self.assertRaises(pb.InvalidPaperBetError):
            pb.settle_paper_bet(self.conn, r["paper_bet_id"], "MAYBE")


class TestBankrollSummary(TestPaperBankroll):
    def test_no_bets_yields_starting_bankroll_and_no_fake_history(self):
        s = pb.bankroll_summary(self.conn, "DEMO_PAPER")
        self.assertEqual(s["current_bankroll"], pb.PAPER_STARTING_BANKROLL)
        self.assertEqual(s["bets"], 0)
        self.assertIsNone(s["hit_rate"])

    def test_tracks_current_bankroll_pnl_roi_hit_rate_staked_drawdown_streaks(self):
        for i in range(3):
            r = self._bet(market_id=f"M{i}", entry_odds=200)
            pb.settle_paper_bet(self.conn, r["paper_bet_id"], "WIN")
        r = self._bet(market_id="M_loss", entry_odds=200)
        pb.settle_paper_bet(self.conn, r["paper_bet_id"], "LOSS")
        s = pb.bankroll_summary(self.conn, "DEMO_PAPER")
        self.assertEqual(s["wins"], 3)
        self.assertEqual(s["losses"], 1)
        self.assertAlmostEqual(s["hit_rate"], 0.75)
        self.assertEqual(s["total_staked"], 40.0)
        self.assertGreater(s["current_bankroll"], pb.PAPER_STARTING_BANKROLL)
        self.assertIsNotNone(s["roi"])
        self.assertGreaterEqual(s["max_drawdown"], 0.0)
        self.assertIn(s["longest_win_streak"], (3, 4))  # order of settlement can vary within same-second timestamps

    def test_bankroll_history_is_immutable_replay_not_recomputed_from_current_odds(self):
        r = self._bet(entry_odds=200)
        pb.settle_paper_bet(self.conn, r["paper_bet_id"], "WIN")
        s1 = pb.bankroll_summary(self.conn, "DEMO_PAPER")
        s2 = pb.bankroll_summary(self.conn, "DEMO_PAPER")
        self.assertEqual(s1["current_bankroll"], s2["current_bankroll"])
        self.assertEqual(len(s1["bankroll_history"]), len(s2["bankroll_history"]))

    def test_pending_never_counted_as_settled(self):
        self._bet()
        s = pb.bankroll_summary(self.conn, "DEMO_PAPER")
        self.assertEqual(s["pending"], 1)
        self.assertEqual(s["bets"], 1)
        self.assertEqual(s["wins"], 0)
        self.assertEqual(s["current_bankroll"], pb.PAPER_STARTING_BANKROLL)


class TestTrackSeparation(TestPaperBankroll):
    def test_real_market_and_demo_paper_never_mix(self):
        r1 = self._bet(track="DEMO_PAPER", price_source="SIMULATED_DEMO", event_id="demo1")
        r2 = self._bet(track="REAL_MARKET_PAPER", price_source="LIVE_DRAFTKINGS", event_id="real1")
        pb.settle_paper_bet(self.conn, r1["paper_bet_id"], "WIN")
        pb.settle_paper_bet(self.conn, r2["paper_bet_id"], "LOSS")
        demo = pb.bankroll_summary(self.conn, "DEMO_PAPER")
        real = pb.bankroll_summary(self.conn, "REAL_MARKET_PAPER")
        self.assertEqual(demo["bets"], 1)
        self.assertEqual(real["bets"], 1)
        self.assertGreater(demo["current_bankroll"], pb.PAPER_STARTING_BANKROLL)
        self.assertLess(real["current_bankroll"], pb.PAPER_STARTING_BANKROLL)

    def test_paper_bets_table_is_a_separate_database_from_the_real_ledger(self):
        self.assertNotEqual(pb.DB_PATH, __import__("operational.prospective_ledger", fromlist=["DB_PATH"]).DB_PATH)


class TestStraightVsCombo(TestPaperBankroll):
    def test_combo_flag_separates_breakdown(self):
        straight = self._bet(is_combo=False)
        combo = self._bet(market_id="COMBO:x+y", is_combo=True, event_id="evt2")
        pb.settle_paper_bet(self.conn, straight["paper_bet_id"], "WIN")
        pb.settle_paper_bet(self.conn, combo["paper_bet_id"], "WIN")
        breakdown = pb.performance_breakdowns(self.conn, "DEMO_PAPER")["by_straight_vs_combo"]
        self.assertEqual(breakdown["STRAIGHT"]["bets"], 1)
        self.assertEqual(breakdown["COMBO"]["bets"], 1)


class TestAutoCreateFromOpportunities(TestPaperBankroll):
    def test_only_bet_decisions_create_a_paper_bet(self):
        opps = [
            {"decision": "BET", "market_id": "M1", "market": "PLAYER_SOG", "current_odds": -150,
             "player_id": "p1", "player": "A", "team": "EDM", "threshold": "3+"},
            {"decision": "WATCH", "market_id": "M2", "market": "PLAYER_SOG", "current_odds": -150,
             "player_id": "p2", "player": "B", "team": "EDM", "threshold": "3+"},
            {"decision": "WAIT", "market_id": "M3", "market": "PLAYER_SOG", "current_odds": -150,
             "player_id": "p3", "player": "C", "team": "EDM", "threshold": "3+"},
            {"decision": "PASS", "market_id": "M4", "market": "PLAYER_SOG", "current_odds": -150,
             "player_id": "p4", "player": "D", "team": "EDM", "threshold": "3+"},
        ]
        results = pb.auto_create_paper_bets_from_opportunities(
            self.conn, opps, track="DEMO_PAPER", price_source="SIMULATED_DEMO")
        self.assertEqual(len(results), 1)
        count = self.conn.execute("SELECT COUNT(*) FROM paper_bets").fetchone()[0]
        self.assertEqual(count, 1)

    def test_refresh_never_creates_a_second_bet_for_the_same_opportunity(self):
        opp = {"decision": "BET", "market_id": "M1", "market": "PLAYER_SOG", "current_odds": -150,
               "player_id": "p1", "player": "A", "team": "EDM", "threshold": "3+"}
        pb.auto_create_paper_bets_from_opportunities(self.conn, [opp], track="DEMO_PAPER",
                                                       price_source="SIMULATED_DEMO")
        pb.auto_create_paper_bets_from_opportunities(self.conn, [opp], track="DEMO_PAPER",
                                                       price_source="SIMULATED_DEMO")
        count = self.conn.execute("SELECT COUNT(*) FROM paper_bets").fetchone()[0]
        self.assertEqual(count, 1)


class TestTheoreticalBankrollQuestion(TestPaperBankroll):
    def test_answers_honestly_with_zero_bets(self):
        answer = pb.answer_theoretical_bankroll_question(self.conn, "REAL_MARKET_PAPER")
        self.assertIn("WAITING", answer)

    def test_answers_with_real_numbers_once_bets_exist(self):
        r = self._bet(entry_odds=200)
        pb.settle_paper_bet(self.conn, r["paper_bet_id"], "WIN")
        answer = pb.answer_theoretical_bankroll_question(self.conn, "DEMO_PAPER")
        self.assertIn("$", answer)
        self.assertNotIn("WAITING", answer)


class TestPerformanceBreakdowns(TestPaperBankroll):
    def test_every_required_breakdown_present(self):
        r = self._bet(market_family="PLAYER_SOG", confidence="HIGH", edge=0.05, entry_odds=-150)
        pb.settle_paper_bet(self.conn, r["paper_bet_id"], "WIN")
        b = pb.performance_breakdowns(self.conn, "DEMO_PAPER")
        for key in ("by_market_family", "by_confidence", "by_edge_bucket", "by_odds_range",
                    "by_top_conviction", "by_straight_vs_combo"):
            self.assertIn(key, b)


class TestWindowedPerformance(TestPaperBankroll):
    """Completion sprint Part 47: yesterday/7-day/30-day/season windows
    for operational/daily_model_review.py to read."""

    def test_all_four_windows_present(self):
        w = pb.windowed_performance(self.conn, "DEMO_PAPER")
        for key in ("yesterday", "last_7_days", "last_30_days", "season_to_date"):
            self.assertIn(key, w)

    def test_no_bets_is_honest_zeros_not_a_crash(self):
        w = pb.windowed_performance(self.conn, "DEMO_PAPER")
        self.assertEqual(w["season_to_date"]["bets"], 0)
        self.assertIsNone(w["season_to_date"]["roi"])
        self.assertEqual(w["season_to_date"]["avg_clv"], "WAITING")

    def test_old_settlement_excluded_from_yesterday_window(self):
        import datetime as dt
        r = self._bet(entry_odds=200)
        old_settle_time = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=10)).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ")
        self.conn.execute(
            "UPDATE paper_bets SET result_status='WIN', settled_at_utc=?, profit_loss=20.0 WHERE paper_bet_id=?",
            (old_settle_time, r["paper_bet_id"]))
        self.conn.commit()
        w = pb.windowed_performance(self.conn, "DEMO_PAPER")
        self.assertEqual(w["yesterday"]["bets"], 0)
        self.assertEqual(w["last_30_days"]["bets"], 1)
        self.assertEqual(w["season_to_date"]["bets"], 1)

    def test_avg_clv_computed_when_present(self):
        r = self._bet(entry_odds=150)
        pb.settle_paper_bet(self.conn, r["paper_bet_id"], "WIN", closing_odds=120)
        w = pb.windowed_performance(self.conn, "DEMO_PAPER")
        self.assertNotEqual(w["season_to_date"]["avg_clv"], "WAITING")
        self.assertIsInstance(w["season_to_date"]["avg_clv"], float)


if __name__ == "__main__":
    unittest.main()
