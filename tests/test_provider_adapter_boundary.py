"""
Preseason Operational Readiness Closure sprint (2026-08-30), Track 5 Part
40: tests for the provider-adapter boundary. The load-bearing assertion
is the negative one -- VERIFIED_CONTRACTS must be empty, matching the
real, twice-confirmed fact that no market's payload has ever been
observed live (see NHL_ENGINE_STATE_OF_THE_UNION_2026_08_30.md).
"""
from __future__ import annotations

import unittest

from research.generic_prop_pricing import provider_adapter as pa
from research.generic_prop_pricing.evaluator import CONTRACT_NOT_VERIFIED


class Test01NoContractsVerifiedYet(unittest.TestCase):
    def test_verified_contracts_is_currently_empty(self):
        self.assertEqual(pa.VERIFIED_CONTRACTS, frozenset(),
                          "no real DraftKings payload has ever been observed for any market -- "
                          "this must stay empty until Part 41's real workflow adds a real entry")

    def test_sog_is_not_verified_despite_being_the_reference_implementation(self):
        self.assertFalse(pa.is_contract_verified("draftkings", "PLAYER_SOG_3PLUS"))

    def test_no_market_family_is_verified(self):
        for market_id in ("PLAYER_SOG_3PLUS", "PLAYER_GOALS_1PLUS", "PLAYER_ASSISTS_1PLUS",
                           "PLAYER_POINTS_1PLUS", "GOALIE_SAVES_25PLUS"):
            self.assertFalse(pa.is_contract_verified("draftkings", market_id))


class Test02UnverifiedMarketNeverParses(unittest.TestCase):
    def test_unverified_market_returns_contract_not_verified_never_a_guess(self):
        result = pa.parse_the_odds_api_market(
            {"outcomes": [{"name": "Over", "price": -115}]}, sportsbook="draftkings",
            canonical_market_id="PLAYER_SOG_3PLUS", event_id="evt-real", player_id="P1")
        self.assertEqual(result["status"], CONTRACT_NOT_VERIFIED)
        self.assertNotIn("market", result)


if __name__ == "__main__":
    unittest.main()
