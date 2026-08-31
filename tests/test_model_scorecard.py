"""
2026-27 Continuous Learning framework, Part 63: tests for
operational/model_scorecard.py -- daily scorecard, calibration,
Brier/log-loss, time windows, CLV, edge buckets, decision-state
separation, market movement.
"""
from __future__ import annotations

import datetime as dt
import unittest

from operational import model_scorecard as ms

NOW = dt.datetime(2026, 11, 15, tzinfo=dt.timezone.utc)


def _row(days_ago=0, prob=0.6, result="WIN", confidence="HIGH", player_id="P1", **extra):
    event = (NOW - dt.timedelta(days=days_ago)).strftime("%Y-%m-%dT23:00:00.000000Z")
    row = {"raw_probability": prob, "result_status": result, "confidence": confidence,
           "player_id": player_id, "event_start_utc": event, "record_type": "MODEL_OBSERVATION"}
    row.update(extra)
    return row


class Test01BrierLogLoss(unittest.TestCase):
    def test_brier_perfect_prediction_is_zero(self):
        self.assertEqual(ms.brier(1.0, 1.0), 0.0)
        self.assertEqual(ms.brier(0.0, 0.0), 0.0)

    def test_brier_worst_prediction_is_one(self):
        self.assertEqual(ms.brier(0.0, 1.0), 1.0)

    def test_log_loss_clips_extreme_probabilities(self):
        # p=1.0 with y=0.0 would be log(0) -- must not raise or return inf.
        result = ms.log_loss(1.0, 0.0)
        self.assertTrue(result > 0)
        self.assertTrue(result < float("inf"))


class Test02CalibrationBins(unittest.TestCase):
    def test_bins_group_by_predicted_probability_band(self):
        bins = ms.calibration_bins([0.05, 0.55, 0.95], [0.0, 1.0, 1.0])
        bands = {b["band"] for b in bins}
        self.assertIn("0%-10%", bands)
        self.assertIn("50%-60%", bands)
        self.assertIn("90%-100%", bands)

    def test_ece_none_when_no_data(self):
        self.assertIsNone(ms.expected_calibration_error([], []))


class Test03TimeWindows(unittest.TestCase):
    def test_last_1_day_excludes_older_rows(self):
        rows = [_row(days_ago=0), _row(days_ago=5)]
        windowed = ms.filter_by_window(rows, "LAST_1_DAY", NOW)
        self.assertEqual(len(windowed), 1)

    def test_last_30_days_includes_more(self):
        rows = [_row(days_ago=0), _row(days_ago=25), _row(days_ago=45)]
        windowed = ms.filter_by_window(rows, "LAST_30_DAYS", NOW)
        self.assertEqual(len(windowed), 2)

    def test_full_sample_includes_everything(self):
        rows = [_row(days_ago=0), _row(days_ago=500)]
        windowed = ms.filter_by_window(rows, "FULL_PROSPECTIVE_SAMPLE", NOW)
        self.assertEqual(len(windowed), 2)

    def test_season_to_date_uses_explicit_boundary_not_a_buggy_year_prefix(self):
        rows = [_row(days_ago=0), _row(days_ago=200)]  # 200 days ago crosses a calendar year
        season_start = (NOW - dt.timedelta(days=30)).strftime("%Y-%m-%dT00:00:00.000000Z")
        windowed = ms.filter_by_window(rows, "SEASON_TO_DATE", NOW, season_start_utc=season_start)
        self.assertEqual(len(windowed), 1)

    def test_unknown_window_raises(self):
        with self.assertRaises(ValueError):
            ms.filter_by_window([], "LAST_YEAR", NOW)


class Test04SettledRowsExcludeNonBinaryStatuses(unittest.TestCase):
    def test_pending_push_void_unresolved_excluded(self):
        rows = [_row(result="WIN"), _row(result="LOSS"), _row(result="PENDING"),
                _row(result="PUSH"), _row(result="VOID"), _row(result="UNRESOLVED")]
        scored = ms.settled_rows(rows)
        self.assertEqual(len(scored), 2)


class Test05ComputeScorecard(unittest.TestCase):
    def test_scorecard_on_perfectly_calibrated_data(self):
        rows = [_row(prob=0.5, result="WIN"), _row(prob=0.5, result="LOSS")]
        sc = ms.compute_scorecard(rows)
        self.assertEqual(sc["prediction_count"], 2)
        self.assertEqual(sc["event_count"], 2)
        self.assertAlmostEqual(sc["empirical_hit_rate"], 0.5)
        self.assertAlmostEqual(sc["mean_predicted_probability"], 0.5)

    def test_expected_count_mae_is_honestly_not_available(self):
        sc = ms.compute_scorecard([_row()])
        self.assertIn("NOT_AVAILABLE", sc["expected_count_mae"])

    def test_confidence_bucket_breakdown(self):
        rows = [_row(confidence="HIGH"), _row(confidence="LOW", result="LOSS")]
        sc = ms.compute_scorecard(rows)
        self.assertIn("HIGH", sc["confidence_bucket_performance"])
        self.assertIn("LOW", sc["confidence_bucket_performance"])

    def test_player_concentration(self):
        rows = [_row(player_id="STAR"), _row(player_id="STAR"), _row(player_id="OTHER")]
        sc = ms.compute_scorecard(rows)
        self.assertEqual(sc["player_concentration"]["top_player_id"], "STAR")
        self.assertAlmostEqual(sc["player_concentration"]["top_player_share"], 2 / 3)

    def test_empty_input_returns_none_metrics_not_zero(self):
        sc = ms.compute_scorecard([])
        self.assertEqual(sc["event_count"], 0)
        self.assertIsNone(sc["brier_score"])


class Test06BaseVsShadow(unittest.TestCase):
    def test_only_paired_rows_are_compared(self):
        rows = [_row(prob=0.5, sog_shadow_raw_probability=0.6),
                _row(prob=0.5, sog_shadow_raw_probability=None)]
        result = ms.compare_base_vs_shadow(rows)
        self.assertEqual(result["n"], 1)

    def test_mean_shadow_minus_base_sign(self):
        rows = [_row(prob=0.5, sog_shadow_raw_probability=0.6, result="WIN")]
        result = ms.compare_base_vs_shadow(rows)
        self.assertAlmostEqual(result["mean_shadow_minus_base"], 0.1)

    def test_no_pairs_returns_none_not_zero(self):
        result = ms.compare_base_vs_shadow([_row(sog_shadow_raw_probability=None)])
        self.assertEqual(result["n"], 0)
        self.assertIsNone(result["base"])


class Test07CLVSummary(unittest.TestCase):
    def test_excludes_rows_without_clv(self):
        rows = [_row(clv=0.02), _row(clv=None)]
        summary = ms.clv_summary(rows)
        self.assertEqual(summary["n"], 1)

    def test_group_by_confidence(self):
        rows = [_row(clv=0.02, confidence="HIGH"), _row(clv=-0.01, confidence="LOW")]
        summary = ms.clv_summary(rows, group_by="confidence")
        self.assertIn("HIGH", summary)
        self.assertIn("LOW", summary)


class Test08EdgeBuckets(unittest.TestCase):
    def test_bucket_boundaries(self):
        self.assertEqual(ms.edge_bucket(-0.01), "<0pp")
        self.assertEqual(ms.edge_bucket(0.01), "0-2pp")
        self.assertEqual(ms.edge_bucket(0.03), "2-4pp")
        self.assertEqual(ms.edge_bucket(0.05), "4-6pp")
        self.assertEqual(ms.edge_bucket(0.08), "6-10pp")
        self.assertEqual(ms.edge_bucket(0.15), ">10pp")

    def test_edge_bucket_performance_groups_settled_rows(self):
        rows = [_row(prob=0.6, conservative_probability=0.55, market_no_vig_probability=0.45, result="WIN")]
        perf = ms.edge_bucket_performance(rows)
        self.assertTrue(any(v["n"] == 1 for v in perf.values()))


class Test09Decision(unittest.TestCase):
    def test_not_available_without_market_fields(self):
        d = ms.recompute_decision(_row())
        self.assertEqual(d["decision"], "NOT_AVAILABLE")

    def test_bet_when_edge_and_ev_clear(self):
        d = ms.recompute_decision(_row(conservative_probability=0.6, market_no_vig_probability=0.4,
                                        odds_american=+150))
        self.assertEqual(d["decision"], "BET")

    def test_low_confidence_downgrades_bet_to_wait(self):
        d = ms.recompute_decision(_row(conservative_probability=0.6, market_no_vig_probability=0.4,
                                        odds_american=+150, confidence="LOW"))
        self.assertEqual(d["decision"], "WAIT")

    def test_pass_when_no_edge(self):
        d = ms.recompute_decision(_row(conservative_probability=0.3, market_no_vig_probability=0.5))
        self.assertEqual(d["decision"], "PASS")


class Test10DecisionStateBreakdown(unittest.TestCase):
    def test_real_bet_pnl_never_merged_into_watch_or_pass(self):
        bet_row = _row(conservative_probability=0.6, market_no_vig_probability=0.4, odds_american=+150,
                        record_type="REAL_BET", profit_loss=25.0, result="WIN")
        watch_row = _row(conservative_probability=0.44, market_no_vig_probability=0.4, result="WIN")
        breakdown = ms.decision_state_breakdown([bet_row, watch_row])
        self.assertIn("BET", breakdown)
        self.assertEqual(breakdown["BET"]["real_bet_pnl_sum"], 25.0)
        if "WATCH" in breakdown:
            self.assertNotIn("real_bet_pnl_sum", breakdown["WATCH"])


class Test11MarketMovement(unittest.TestCase):
    def test_toward_model_detected(self):
        from pricing import odds_math
        # model prob 0.6, observed no-vig 0.5 (gap .1), closing price implies 0.58 (gap .02) -> toward
        closing_price = odds_math.prob_to_american(0.58)
        rows = [_row(prob=0.6, market_no_vig_probability=0.5, closing_odds=closing_price)]
        result = ms.market_movement_summary(rows)
        self.assertEqual(result["toward_model"], 1)

    def test_not_available_without_closing_price(self):
        rows = [_row(prob=0.6, market_no_vig_probability=0.5, closing_odds=None)]
        result = ms.market_movement_summary(rows)
        self.assertEqual(result["not_available"], 1)


class Test12MaxBuyValidation(unittest.TestCase):
    def test_implied_max_price_moves_with_no_vig_baseline(self):
        low_novig = ms.implied_max_acceptable_price(0.5, 0.3)
        high_novig = ms.implied_max_acceptable_price(0.5, 0.45)
        self.assertIsNotNone(low_novig)
        self.assertIsNotNone(high_novig)

    def test_none_when_inputs_missing(self):
        self.assertIsNone(ms.implied_max_acceptable_price(None, 0.3))


if __name__ == "__main__":
    unittest.main()
