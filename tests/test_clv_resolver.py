"""
Preseason Operational Readiness Closure sprint (2026-08-30), Track 6:
tests for operational/clv_resolver.py.
"""
from __future__ import annotations

import unittest

from operational import clv_resolver as clv

EVENT_START = "2026-10-15T23:00:00.000000Z"


def _real_obs(captured_at, price):
    return {"captured_at_utc": captured_at, "american_price": price, "source": "THE_ODDS_API"}


def _demo_obs(captured_at, price):
    return {"captured_at_utc": captured_at, "american_price": price, "source": "DEMO_SIMULATED"}


class Test01ClosingSnapshot(unittest.TestCase):
    """Part 44: strictly before event_start_utc, exact equality excluded."""

    def test_picks_the_latest_valid_observation(self):
        history = [_real_obs("2026-10-15T12:00:00.000000Z", -105),
                   _real_obs("2026-10-15T18:00:00.000000Z", -115),
                   _real_obs("2026-10-15T20:00:00.000000Z", -120)]
        result = clv.find_closing_price(history, EVENT_START)
        self.assertEqual(result["status"], clv.RESOLVED)
        self.assertEqual(result["closing_odds"], -120)
        self.assertEqual(result["closing_captured_at_utc"], "2026-10-15T20:00:00.000000Z")

    def test_observation_exactly_at_event_start_excluded(self):
        history = [_real_obs("2026-10-15T20:00:00.000000Z", -120),
                   _real_obs(EVENT_START, -999)]
        result = clv.find_closing_price(history, EVENT_START)
        self.assertEqual(result["closing_odds"], -120, "the exact-start observation must be excluded")

    def test_observation_after_event_start_excluded(self):
        history = [_real_obs("2026-10-16T00:00:00.000000Z", -999)]
        result = clv.find_closing_price(history, EVENT_START)
        self.assertEqual(result["status"], clv.CLV_NOT_AVAILABLE)


class Test02NoRealHistoryAvailable(unittest.TestCase):
    def test_empty_history_is_not_available(self):
        self.assertEqual(clv.find_closing_price([], EVENT_START)["status"], clv.CLV_NOT_AVAILABLE)

    def test_no_pregame_observations_is_not_available(self):
        history = [_real_obs("2026-10-16T00:00:00.000000Z", -120)]
        self.assertEqual(clv.find_closing_price(history, EVENT_START)["status"], clv.CLV_NOT_AVAILABLE)


class Test03NoSyntheticCLV(unittest.TestCase):
    """Part 46: demo/research/shadow prices can never produce real CLV,
    even if they'd otherwise qualify on timing alone."""

    def test_demo_only_history_never_resolves(self):
        history = [_demo_obs("2026-10-15T18:00:00.000000Z", -115)]
        result = clv.find_closing_price(history, EVENT_START)
        self.assertEqual(result["status"], clv.CLV_NOT_AVAILABLE)

    def test_mixed_history_uses_only_the_real_observations(self):
        history = [_demo_obs("2026-10-15T21:00:00.000000Z", -999),  # later but fake -- must be ignored
                   _real_obs("2026-10-15T18:00:00.000000Z", -115)]
        result = clv.find_closing_price(history, EVENT_START)
        self.assertEqual(result["status"], clv.RESOLVED)
        self.assertEqual(result["closing_odds"], -115)


class Test04ComputeCLV(unittest.TestCase):
    def test_clv_is_implied_probability_delta(self):
        from pricing import odds_math
        entry, close = -110, -130
        expected = odds_math.american_to_prob(close) - odds_math.american_to_prob(entry)
        self.assertAlmostEqual(clv.compute_clv(entry, close), expected)


class Test05ModelObservationCLVNeverImpliesRealPnL(unittest.TestCase):
    """Part 47: attaching a CLV number to a MODEL_OBSERVATION via
    settle_completed_observation must never populate profit_loss."""

    def test_settle_completed_observation_never_sets_profit_loss(self):
        from operational import prospective_ledger as pl
        from operational import prospective_recording as pr
        conn = pl.init_db(db_path=":memory:")
        inserted = pl.record_model_observation(
            conn, event_start_utc=EVENT_START, created_at_utc="2026-10-15T18:00:00.000000Z",
            prediction_cutoff_utc="2026-10-15T18:00:00.000000Z", game_id=1, game_date="2026-10-15",
            player_id="P1", team="EDM", opponent="CHI", market_id="PLAYER_SOG", threshold="3+",
            raw_probability=0.5, odds_american=-110, odds_captured_at_utc="2026-10-15T18:00:00.000000Z")
        pr.settle_completed_observation(
            conn, inserted["prediction_id"], actual_outcome="5", result_status="WIN",
            closing_odds=-130, closing_captured_at_utc="2026-10-15T20:00:00.000000Z")
        row = pl.get_observation(conn, inserted["prediction_id"])
        self.assertIsNotNone(row["clv"])
        self.assertIsNone(row["profit_loss"], "a MODEL_OBSERVATION's CLV must never imply real P&L")


if __name__ == "__main__":
    unittest.main()
