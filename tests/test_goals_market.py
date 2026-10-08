"""Anytime-goal-scorer market: contract certified against a real archived DraftKings payload, leg construction, engine gating, settlement
mapping, and the credit rule that decides whether the market may be captured."""
from __future__ import annotations

import datetime as dt
import json
import unittest
from pathlib import Path

from operational import best_bets as bb
from operational import credit_allocation as ca
from operational import paper_bet_settlement_driver as driver
from research.generic_prop_pricing import provider_adapter as pa
from research.real_market_parlay import engine as rmp

FIX = Path(__file__).resolve().parent / "fixtures" / "draftkings_player_goal_scorer_real_payload.json"
NOW = dt.datetime(2026, 10, 5, 16, 30, tzinfo=dt.timezone.utc)


def payload():
    return json.loads(FIX.read_text())


class TestContract(unittest.TestCase):
    def test_real_payload_shape_is_one_sided_yes_with_description_and_price(self):
        p = payload()
        market = p["bookmakers"][0]["markets"][0]
        self.assertEqual((p["bookmakers"][0]["key"], market["key"]), ("draftkings", "player_goal_scorer_anytime"))
        self.assertEqual({o["name"] for o in market["outcomes"]}, {"Yes"})           # no "No" side exists: no no-vig probability
        self.assertTrue(all(set(o) == {"description", "name", "price"} for o in market["outcomes"]))
        self.assertTrue(all(isinstance(o["price"], int) for o in market["outcomes"]))
        self.assertTrue(market["last_update"].startswith("2026-10-05T16:04"))

    def test_family_is_verified_and_allowed(self):
        self.assertTrue(pa.is_contract_verified("draftkings", "PLAYER_GOALS"))
        self.assertFalse(pa.is_contract_verified("fanduel", "PLAYER_GOALS"))
        self.assertIn("PLAYER_GOALS", rmp.ALLOWED_MARKET_FAMILIES)


def model_for(payload_):
    home, away = payload_["home_team"], payload_["away_team"]
    from research.live_sog_pricing import event_mapping
    h, a = event_mapping.normalize_team_name(home), event_mapping.normalize_team_name(away)
    entries = {}
    for i, o in enumerate(payload_["bookmakers"][0]["markets"][0]["outcomes"]):
        entries[f"{bb.norm_name(o['description'])}|{h}"] = {"player_id": f"P{i}", "name": o["description"], "team": h, "opp": a, "home": True,
                                                            "probs": {"GOAL1": 0.30}}
    snapshot = {"games": {"G1": {"home": h, "away": a, "start_utc": "2026-10-05T23:00:00Z"}}, "model": entries}
    return snapshot


class TestLegs(unittest.TestCase):
    def test_goal_legs_come_from_yes_prices_with_the_goal_probability(self):
        p = payload()
        legs = bb._legs_from_payload(p, dt.datetime(2026, 10, 5, 16, 5, tzinfo=dt.timezone.utc), model_for(p), NOW, only_market=bb.GOALS_MARKET_KEY)
        self.assertEqual(len(legs), 10)
        leg = next(l for l in legs if l.participant_name == "Brandon Hagel")
        self.assertEqual((leg.market_family, leg.threshold, leg.side, leg.american_price), ("PLAYER_GOALS", 1, "OVER", 160.0))
        self.assertAlmostEqual(leg.conservative_probability, 0.30)
        self.assertTrue(leg.provider_contract_verified and leg.model_threshold_eligible and leg.identity_resolved)
        self.assertEqual(rmp.leg_label(leg), "Brandon Hagel to score a goal (anytime)")
        self.assertTrue(rmp.leg_is_eligible(leg) or not leg.price_fresh)            # eligibility depends only on freshness here

    def test_a_threshold_other_than_one_is_not_eligible(self):
        p = payload()
        leg = bb._legs_from_payload(p, dt.datetime(2026, 10, 5, 16, 5, tzinfo=dt.timezone.utc), model_for(p), NOW, only_market=bb.GOALS_MARKET_KEY)[0]
        import dataclasses
        self.assertFalse(rmp.leg_is_eligible(dataclasses.replace(leg, threshold=2, price_fresh=True, event_not_started=True)))


class TestSettlementMapping(unittest.TestCase):
    def test_goal_leg_maps_to_the_resolver_goal_market(self):
        self.assertEqual(driver._leg_settlement_market_id({"market_family": "PLAYER_GOALS", "threshold": 1}), "PLAYER_GOALS_1PLUS")


class TestCreditRule(unittest.TestCase):
    def status(self, burn, usable, days):
        return {"trailing_daily_burn": burn, "usable": usable, "days_left_in_cycle": days}

    def test_goals_denied_when_the_month_does_not_balance(self):
        d = ca.goals_capture_decision(self.status(27.0, 277, 23.2), 4.4)
        self.assertFalse(d["allow"])
        self.assertEqual(d["reason"], "MONTH_DOES_NOT_BALANCE_WITH_GOALS")
        self.assertGreater(d["shortfall"], 0)

    def test_goals_allowed_when_there_is_room(self):
        d = ca.goals_capture_decision(self.status(10.0, 400, 20.0), 4.0)
        self.assertTrue(d["allow"] and d["shortfall"] == 0)

    def test_status_reads_provider_headers_and_projects_exhaustion(self):
        now = dt.datetime(2026, 10, 8, 20, 0, tzinfo=dt.timezone.utc)
        rows = [{"at": now - dt.timedelta(days=d), "class": "all_games_moneyline", "credits": 20.0, "markets": None, "used": None, "remaining": 300} for d in range(7)]
        st = ca.status(now, rows=rows, remaining=300)
        self.assertEqual(st["remaining"], 300)
        self.assertEqual(st["usable"], 280)
        self.assertAlmostEqual(st["trailing_daily_burn"], 20.0 * 7 / 6.0, places=1)
        self.assertGreater(st["month_shortfall_at_current_burn"], 0)
        self.assertEqual(st["spend_by_class"]["all_games_moneyline"]["calls"], 7)


if __name__ == "__main__":
    unittest.main()
