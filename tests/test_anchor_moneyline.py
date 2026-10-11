"""A moneyline quote is a ticket leg for as long as it is FRESH by the same limit as a shots price (morning and daytime pulls count), instead of only inside the T-35 tiers."""
from __future__ import annotations

import datetime as dt
import unittest
from unittest import mock

from operational import best_bets
from pricing.engine import BetReport
from research.real_market_parlay import real_slate_adapter as adapter
from tests.test_real_slate_adapter import _fresh_db_with_game, _pred

START = "2026-10-11T23:10:00"
NOW = dt.datetime(2026, 10, 11, 17, 0, tzinfo=dt.timezone.utc)          # 6 h 10 m before puck drop


def snapshot(conn, captured):
    cur = conn.execute("INSERT INTO odds_snapshots (game_id, sportsbook, data_provider, market, selection, event_start_utc, price_american, status, captured_at_utc, received_at_utc, snapshot_label) "
                       "VALUES (1, 'draftkings', 'test', 'MONEYLINE', 'TOR', ?, -150, 'ACTIVE', ?, ?, 'morning')", (START, captured, captured))
    conn.commit()
    return cur.lastrowid


def legs_for(captured, now=NOW, limit=best_bets.price_age_limit_min):
    conn = _fresh_db_with_game(start_utc=START)
    report = BetReport(game_label="MTL @ TOR", market="MONEYLINE", selection="TOR", action="PASS", model_true_probability=0.56, model_conservative_probability=0.50,
                       market_no_vig_probability=0.58, current_draftkings_price=-150, odds_snapshot_id_selection=snapshot(conn, captured))
    pred = _pred(start_utc=START)
    with mock.patch("run_slate.build_prediction_for_game", return_value=pred), mock.patch("pricing.engine.evaluate_moneyline_for_game", return_value=[report]) as ev, \
            mock.patch("operational.eastern_time.eastern_today", return_value="2026-10-11"):
        out = adapter.moneyline_candidate_legs(conn, now=now, freshness_limit_min=limit)
    return out, ev


class TestMoneylineFreshness(unittest.TestCase):
    def test_a_morning_quote_inside_the_shots_limit_is_a_fresh_early_leg(self):
        (legs, excluded), ev = legs_for("2026-10-11T15:30:00")                  # 90 minutes old, 7+ hours out: limit 150
        self.assertEqual((len(legs), excluded), (1, []))
        leg = legs[0]
        self.assertTrue(leg.price_fresh and not leg.provisional)
        self.assertTrue(leg.early)                                              # retrieved more than three hours before puck drop
        self.assertEqual((leg.freshness_status, leg.quote_age_min), ("FRESH", 90.0))
        self.assertEqual((leg.market_probability, leg.model_probability), (0.58, 0.56))        # the market's chance and Elo's point estimate, kept apart
        self.assertEqual(ev.call_args.kwargs["max_staleness_minutes"], 840.0)   # the engine is asked for any of today's quotes; freshness is judged here against the limit

    def test_the_same_quote_without_the_policy_keeps_the_t35_tiers(self):
        (_, _), ev = legs_for("2026-10-11T15:30:00", limit=None)
        self.assertIsNone(ev.call_args.kwargs["max_staleness_minutes"])

    def test_an_older_quote_from_today_is_provisional_not_fresh(self):
        (legs, _), _ = legs_for("2026-10-11T12:00:00")                          # 5 hours old
        self.assertFalse(legs[0].price_fresh)
        self.assertTrue(legs[0].provisional)
        self.assertEqual(legs[0].freshness_status, "STALE_QUOTE")

    def test_a_quote_from_another_day_is_excluded(self):
        (legs, excluded), _ = legs_for("2026-10-10T21:00:00", now=NOW)
        self.assertEqual(legs, [])
        self.assertIn("STALE_PRICE", excluded[0]["reason"])

    def test_the_limit_tightens_inside_two_hours_of_puck_drop(self):
        near = dt.datetime(2026, 10, 11, 21, 40, tzinfo=dt.timezone.utc)        # 90 minutes out: limit 100
        (fresh, _), _ = legs_for("2026-10-11T20:20:00", now=near)               # 80 minutes old
        (stale, _), _ = legs_for("2026-10-11T19:40:00", now=near)               # 120 minutes old
        self.assertTrue(fresh[0].price_fresh)
        self.assertFalse(stale[0].price_fresh)


if __name__ == "__main__":
    unittest.main()
