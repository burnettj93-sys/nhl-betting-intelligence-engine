"""
Tests for operational/paper_bankroll.py (Live DK / Paper Bankroll
completion sprint, 2026-08-31, Part 52; default bankroll updated to
$500 the same day, see TestFiveHundredDollarBaseline). Covers the exact
$10/$500 economics, first-actionable-entry-only idempotency, immutable
entry snapshot, payout math for both odds signs, settlement idempotency,
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
    def test_starting_bankroll_is_500(self):
        self.assertEqual(pb.PAPER_STARTING_BANKROLL, 500.00)

    def test_stake_is_10(self):
        self.assertEqual(pb.PAPER_BET_STAKE, 10.00)

    def test_stake_is_exactly_2_percent_of_the_default_starting_bankroll(self):
        # Owner directive (2026-08-31 baseline update): $500 bankroll,
        # $10 fixed stake -- a fixed dollar amount, never a dynamic
        # percentage or Kelly stake. This test only documents the
        # resulting ratio; it must never be read as license to derive
        # PAPER_BET_STAKE FROM PAPER_STARTING_BANKROLL programmatically.
        self.assertAlmostEqual(pb.PAPER_BET_STAKE / pb.PAPER_STARTING_BANKROLL, 0.02)


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


class TestFiveHundredDollarBaseline(TestPaperBankroll):
    """Owner directive (2026-08-31): default starting bankroll is now
    $500 (was $1,000). Fixed $10 stake unchanged. These tests use the
    literal 500.00 (not the pb.PAPER_STARTING_BANKROLL symbol) so a
    future accidental edit of the constant would be caught here too,
    not just silently pass because both sides moved together."""

    def test_pending_stake_does_not_change_bankroll_until_settlement(self):
        # This architecture tracks pending stake separately from
        # realized bankroll -- current_bankroll only ever reflects
        # SETTLED (WIN/LOSS/VOID) profit_loss, never an "at risk" stake
        # deduction for a still-PENDING bet.
        self._bet(entry_odds=150)
        s = pb.bankroll_summary(self.conn, "DEMO_PAPER")
        self.assertEqual(s["pending"], 1)
        self.assertEqual(s["current_bankroll"], 500.00)
        self.assertEqual(s["net_profit"], 0.0)

    def test_win_bankroll_arithmetic_starts_from_500(self):
        r = self._bet(entry_odds=200)  # $10 win at +200 = $20 profit
        pb.settle_paper_bet(self.conn, r["paper_bet_id"], "WIN")
        s = pb.bankroll_summary(self.conn, "DEMO_PAPER")
        self.assertEqual(s["current_bankroll"], 520.00)

    def test_loss_bankroll_arithmetic_starts_from_500(self):
        r = self._bet(entry_odds=200)
        pb.settle_paper_bet(self.conn, r["paper_bet_id"], "LOSS")
        s = pb.bankroll_summary(self.conn, "DEMO_PAPER")
        self.assertEqual(s["current_bankroll"], 490.00)

    def test_void_bankroll_arithmetic_returns_to_exactly_500(self):
        r = self._bet(entry_odds=200)
        pb.settle_paper_bet(self.conn, r["paper_bet_id"], "VOID")
        s = pb.bankroll_summary(self.conn, "DEMO_PAPER")
        self.assertEqual(s["current_bankroll"], 500.00)

    def test_roi_uses_amount_staked_not_starting_bankroll(self):
        r = self._bet(entry_odds=200)
        pb.settle_paper_bet(self.conn, r["paper_bet_id"], "WIN")
        s = pb.bankroll_summary(self.conn, "DEMO_PAPER")
        # $20 profit on $10 staked = 200% ROI, NOT 20/500 = 4% -- proves
        # the denominator is total_staked, unaffected by the $500 vs
        # the old $1,000 bankroll value.
        self.assertAlmostEqual(s["roi"], 2.0)

    def test_drawdown_uses_the_500_baseline(self):
        r1 = self._bet(entry_odds=200, market_id="M1")
        pb.settle_paper_bet(self.conn, r1["paper_bet_id"], "WIN")  # 500 -> 520 (new peak)
        r2 = self._bet(entry_odds=-200, market_id="M2")
        pb.settle_paper_bet(self.conn, r2["paper_bet_id"], "LOSS")  # 520 -> 510
        s = pb.bankroll_summary(self.conn, "DEMO_PAPER")
        self.assertEqual(s["peak_bankroll"], 520.00)
        self.assertAlmostEqual(s["max_drawdown"], 10.00)
        self.assertAlmostEqual(s["current_drawdown"], 10.00)

    def test_existing_immutable_rows_are_unaffected_by_the_constant_change(self):
        # PAPER_STARTING_BANKROLL is a pure Python constant, never
        # persisted per-row -- confirms changing it cannot mutate any
        # already-recorded paper_bets row's entry fields.
        r = self._bet(entry_odds=175, market_id="M_frozen")
        before = dict(self.conn.execute(
            "SELECT * FROM paper_bets WHERE paper_bet_id = ?", (r["paper_bet_id"],)).fetchone())
        pb.bankroll_summary(self.conn, "DEMO_PAPER")  # reads PAPER_STARTING_BANKROLL
        after = dict(self.conn.execute(
            "SELECT * FROM paper_bets WHERE paper_bet_id = ?", (r["paper_bet_id"],)).fetchone())
        self.assertEqual(before, after)
        self.assertEqual(after["entry_odds"], 175)
        self.assertEqual(after["stake"], pb.PAPER_BET_STAKE)


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


class TestGameParlayPaperTrack(TestPaperBankroll):
    """Live Odds/Parlay/Post-Mortem activation sprint, Part 49: a third,
    separate track for Game Edge Parlay paper bets."""

    def test_game_parlay_paper_is_a_valid_track(self):
        r = self._bet(track="GAME_PARLAY_PAPER", price_source="SIMULATED_DEMO", is_combo=True)
        self.assertEqual(r["status"], "INSERTED")

    def test_game_parlay_paper_accepts_either_price_source(self):
        r1 = self._bet(track="GAME_PARLAY_PAPER", price_source="SIMULATED_DEMO", event_id="gp1")
        r2 = self._bet(track="GAME_PARLAY_PAPER", price_source="LIVE_DRAFTKINGS", event_id="gp2")
        self.assertEqual(r1["status"], "INSERTED")
        self.assertEqual(r2["status"], "INSERTED")

    def test_game_parlay_paper_bankroll_kept_separate_from_demo_and_real(self):
        self._bet(track="DEMO_PAPER", price_source="SIMULATED_DEMO", event_id="d1")
        r = self._bet(track="GAME_PARLAY_PAPER", price_source="SIMULATED_DEMO", event_id="gp1")
        pb.settle_paper_bet(self.conn, r["paper_bet_id"], "WIN")
        demo = pb.bankroll_summary(self.conn, "DEMO_PAPER")
        parlay = pb.bankroll_summary(self.conn, "GAME_PARLAY_PAPER")
        self.assertEqual(demo["bets"], 1)
        self.assertEqual(parlay["bets"], 1)
        self.assertGreater(parlay["current_bankroll"], pb.PAPER_STARTING_BANKROLL)
        self.assertEqual(demo["current_bankroll"], pb.PAPER_STARTING_BANKROLL)  # untouched, still PENDING

    def test_still_rejects_a_genuinely_unknown_track(self):
        with self.assertRaises(pb.InvalidPaperBetError):
            self._bet(track="NOT_A_REAL_TRACK", price_source="SIMULATED_DEMO")


class TestGameEdgeParlayPaperBet(TestPaperBankroll):
    """Part 49/50: create_game_edge_parlay_paper_bet()."""

    def _qualified_result(self):
        from research.game_edge_parlay.engine import ComboResult
        legs = [
            {"player_id": "p1", "player": "A", "market": "PLAYER_SOG", "market_id": "M1",
             "threshold": "3+", "current_odds": -150, "conservative_probability": 0.7},
            {"player_id": "p2", "player": "B", "market": "PLAYER_POINTS", "market_id": "M2",
             "threshold": "1+", "current_odds": -150, "conservative_probability": 0.7},
            {"player_id": "p3", "player": "C", "market": "PLAYER_ASSISTS", "market_id": "M3",
             "threshold": "1+", "current_odds": -150, "conservative_probability": 0.7},
        ]
        combo = ComboResult(legs=legs, status="VALIDATED", joint_probability=0.6, pairwise=[],
                             estimated_combo_price=180.0, fair_combo_price=165.0, combo_edge=0.04)
        return {"status": "QUALIFIED", "recommended_legs": 3, "combo": combo, "alternative_3leg": None}

    def test_qualified_result_creates_a_bet_in_the_game_parlay_track(self):
        result = pb.create_game_edge_parlay_paper_bet(self.conn, self._qualified_result(), event_id="evt-1")
        self.assertEqual(result["status"], "INSERTED")
        summary = pb.bankroll_summary(self.conn, "GAME_PARLAY_PAPER")
        self.assertEqual(summary["bets"], 1)
        self.assertEqual(summary["pending"], 1)  # correctly still PENDING, no game has been played

    def test_non_qualifying_result_is_refused(self):
        with self.assertRaises(pb.InvalidPaperBetError):
            pb.create_game_edge_parlay_paper_bet(
                self.conn, {"status": "NO_QUALIFYING_GAME_EDGE_PARLAY", "reason": "x"}, event_id="evt-1")

    def test_never_mixed_with_demo_paper_track(self):
        self._bet(track="DEMO_PAPER")
        pb.create_game_edge_parlay_paper_bet(self.conn, self._qualified_result(), event_id="evt-1")
        demo_rows = pb.query_paper_bets(self.conn, track="DEMO_PAPER")
        parlay_rows = pb.query_paper_bets(self.conn, track="GAME_PARLAY_PAPER")
        self.assertEqual(len(demo_rows), 1)
        self.assertEqual(len(parlay_rows), 1)

    def test_is_combo_flag_set(self):
        pb.create_game_edge_parlay_paper_bet(self.conn, self._qualified_result(), event_id="evt-1")
        rows = pb.query_paper_bets(self.conn, track="GAME_PARLAY_PAPER")
        self.assertEqual(rows[0]["is_combo"], 1)

    def test_idempotent_on_the_same_qualifying_result(self):
        r1 = pb.create_game_edge_parlay_paper_bet(self.conn, self._qualified_result(), event_id="evt-1")
        r2 = pb.create_game_edge_parlay_paper_bet(self.conn, self._qualified_result(), event_id="evt-1")
        self.assertEqual(r1["status"], "INSERTED")
        self.assertEqual(r2["status"], "DUPLICATE")


class TestSchemaV1ToV2Migration(unittest.TestCase):
    """A real pre-sprint database (schema v1, `track` CHECK constraint
    without GAME_PARLAY_PAPER) must upgrade in place, keeping every
    existing row, when opened by the new init_db()."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "v1.db"

    def tearDown(self):
        self._tmp.cleanup()

    def _create_v1_db(self):
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        conn.executescript("""
            CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
            INSERT INTO schema_version (version) VALUES (1);
            CREATE TABLE paper_bets (
                paper_bet_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
                track TEXT NOT NULL CHECK (track IN ('REAL_MARKET_PAPER', 'DEMO_PAPER')),
                is_combo INTEGER NOT NULL DEFAULT 0, top_conviction INTEGER NOT NULL DEFAULT 0,
                event_id TEXT, game_date TEXT, player_id TEXT, player_name_snapshot TEXT,
                team TEXT, opponent TEXT, market_id TEXT NOT NULL, market_family TEXT,
                threshold TEXT, side TEXT,
                price_source TEXT NOT NULL CHECK (price_source IN ('LIVE_DRAFTKINGS', 'SIMULATED_DEMO')),
                legs_json TEXT, entry_odds REAL NOT NULL, model_probability REAL,
                conservative_probability REAL, market_no_vig_probability REAL, edge REAL, ev REAL,
                confidence TEXT, model_version TEXT, prediction_checkpoint TEXT,
                stake REAL NOT NULL, created_at_utc TEXT NOT NULL, event_start_utc TEXT,
                result_status TEXT NOT NULL DEFAULT 'PENDING'
                    CHECK (result_status IN ('PENDING', 'WIN', 'LOSS', 'VOID', 'UNRESOLVED')),
                settled_at_utc TEXT, profit_loss REAL, closing_odds REAL,
                closing_captured_at_utc REAL, clv REAL, notes TEXT
            );
            INSERT INTO paper_bets (paper_bet_id, idempotency_key, track, market_id, price_source,
                entry_odds, stake, created_at_utc)
            VALUES ('pre-existing-1', 'idem-1', 'DEMO_PAPER', 'PLAYER_POINTS_2PLUS',
                'SIMULATED_DEMO', 150, 10.0, '2026-09-01T00:00:00Z');
        """)
        conn.commit()
        conn.close()

    def test_existing_row_survives_migration(self):
        self._create_v1_db()
        conn = pb.init_db(self.db_path)
        row = conn.execute("SELECT * FROM paper_bets WHERE paper_bet_id = 'pre-existing-1'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["track"], "DEMO_PAPER")
        conn.close()

    def test_schema_version_bumped_to_2(self):
        self._create_v1_db()
        conn = pb.init_db(self.db_path)
        version = conn.execute("SELECT version FROM schema_version").fetchone()["version"]
        self.assertEqual(version, 2)
        conn.close()

    def test_game_parlay_paper_insertable_after_migration(self):
        self._create_v1_db()
        conn = pb.init_db(self.db_path)
        result = pb.record_paper_bet(conn, track="GAME_PARLAY_PAPER", price_source="SIMULATED_DEMO",
                                      market_id="GAME_EDGE_PARLAY:x", entry_odds=250, event_id="evt-new",
                                      created_at_utc="2026-09-15T00:00:00Z")
        self.assertEqual(result["status"], "INSERTED")
        conn.close()

    def test_immutability_trigger_still_enforced_after_migration(self):
        self._create_v1_db()
        conn = pb.init_db(self.db_path)
        with self.assertRaises(Exception):
            conn.execute("UPDATE paper_bets SET entry_odds = 999 WHERE paper_bet_id = 'pre-existing-1'")
        conn.close()

    def test_running_init_db_twice_on_an_already_migrated_db_is_a_no_op(self):
        self._create_v1_db()
        pb.init_db(self.db_path).close()
        conn = pb.init_db(self.db_path)  # second call must not re-migrate or error
        row = conn.execute("SELECT * FROM paper_bets WHERE paper_bet_id = 'pre-existing-1'").fetchone()
        self.assertIsNotNone(row)
        conn.close()


if __name__ == "__main__":
    unittest.main()
