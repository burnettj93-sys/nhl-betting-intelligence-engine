"""
Preseason Operational Readiness Closure sprint (2026-08-30), Track 5 Part
40: tests for the provider-adapter boundary. The load-bearing assertion
was originally negative -- VERIFIED_CONTRACTS must be empty, matching
the (then-true) fact that no market's payload had ever been observed
live (see NHL_ENGINE_STATE_OF_THE_UNION_2026_08_30.md).

Live DK / Paper Bankroll completion sprint (2026-08-31), Parts 9-17:
Part 41's real workflow was actually completed for MONEYLINE this
sprint -- a real DraftKings h2h payload was captured, archived, and
regression-tested (see tests/test_generic_prop_pricing.py::
TestMoneylineContractParity). The guard here now asserts the new real
fact precisely (exactly MONEYLINE, nothing else) rather than the old
"always empty" fact -- the spirit (never let an unverified contract
appear without a real, tested payload behind it) is unchanged.

SOG Contract Certification block (2026-09-29): two more real, archived
payloads certified -- (draftkings, PLAYER_SOG_ALTERNATE) via
player_shots_on_goal_alternate (FLA@CAR) and (draftkings,
ALTERNATE_TEAM_TOTAL) via alternate_team_totals (PIT@WSH). See
tests/test_generic_prop_pricing.py::TestPlayerSogAlternateContractParity /
TestAlternateTeamTotalContractParity. PLAYER_SOG_ALTERNATE, not bare
PLAYER_SOG: only the alternate ladder shape was ever observed -- the
standard player_shots_on_goal Over/Under shape remains genuinely
unverified (test_sog_is_not_verified_despite_being_the_reference_implementation
below still holds for the same reason it always did).
"""
from __future__ import annotations

import unittest

from research.generic_prop_pricing import provider_adapter as pa
from research.generic_prop_pricing.evaluator import CONTRACT_NOT_VERIFIED


class Test01NoContractsVerifiedYet(unittest.TestCase):
    def test_verified_contracts_is_exactly_these_six_real_observed_payloads(self):
        self.assertEqual(pa.VERIFIED_CONTRACTS, frozenset({
            ("draftkings", "MONEYLINE"),
            ("draftkings", "PLAYER_SOG_ALTERNATE"),
            ("draftkings", "ALTERNATE_TEAM_TOTAL"),
            ("draftkings", "PLAYER_SOG"),
            ("draftkings", "GOALIE_SAVES"),
            ("draftkings", "PLAYER_POINTS"),
        }), "Unified ticket workflow (2026-10-07) added PLAYER_POINTS (real archived payload + parity test). Standard SOG/Saves Certification block (2026-10-01): MONEYLINE, PLAYER_SOG_ALTERNATE, "
            "ALTERNATE_TEAM_TOTAL, PLAYER_SOG, and GOALIE_SAVES are the five real, live-observed "
            "DraftKings payload contracts as of this block -- every other market family must stay "
            "unverified until its own real payload is observed")

    def test_sog_with_threshold_suffix_is_not_verified_despite_the_bare_family_being_verified(self):
        # provider_adapter.is_contract_verified() does exact tuple matching on
        # canonical_market_id -- the bare "PLAYER_SOG" family (now verified) is a
        # DIFFERENT string from a threshold-suffixed id like "PLAYER_SOG_3PLUS"
        # (never verified, and never meant to be -- the suffix form is purely
        # descriptive on NormalizedPropMarket, not a lookup key provider_adapter
        # ever checks against).
        self.assertFalse(pa.is_contract_verified("draftkings", "PLAYER_SOG_3PLUS"))

    def test_other_prop_market_families_remain_unverified(self):
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
