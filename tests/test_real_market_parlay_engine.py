"""
Tests for research/real_market_parlay/engine.py (Production Hardening +
Parlay Build block, 2026-09-29). Covers: the MONEYLINE + PLAYER_SOG_ALTERNATE
allowlist, the SOG actionable-threshold restriction, the full PARLAY_ELIGIBLE
gate, same-game exclusion (proving legs are never "blindly multiplied" when
they share a game), the >=70% hard joint-probability floor, positive
combo-edge requirement, 3-vs-4-leg selection, never-forced 0-qualifying-parlay
outcomes, and that offered_parlay_price is never fabricated. An integration
test at the end proves a ParlayLeg built from the REAL, certified
provider_adapter.PLAYER_SOG_ALTERNATE output (the same fixture
TestPlayerSogAlternateContractParity uses) is genuinely compatible with this
engine, not just a hand-rolled synthetic shape.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from research.real_market_parlay import engine as rmp


def _leg(game_id="G1", market_family="PLAYER_SOG_ALTERNATE", threshold=3,
         conservative_probability=0.90, american_price=-150, **overrides):
    fields = dict(
        game_id=game_id, event_id=f"evt-{game_id}", market_family=market_family,
        participant_id="P1", participant_name="Test Player", side="OVER", threshold=threshold,
        american_price=american_price, conservative_probability=conservative_probability,
        sportsbook="draftkings", captured_at_utc="2026-09-29T12:00:00Z",
        provider_contract_verified=True, model_threshold_eligible=True, identity_resolved=True,
        price_fresh=True, event_not_started=True,
    )
    fields.update(overrides)
    return rmp.ParlayLeg(**fields)


def _moneyline_leg(game_id="G1", conservative_probability=0.90, american_price=-150, **overrides):
    return _leg(game_id=game_id, market_family="MONEYLINE", threshold=None, side="HOME",
                conservative_probability=conservative_probability, american_price=american_price,
                participant_id="TOR", participant_name="Toronto Maple Leafs", **overrides)


class TestAllowlist(unittest.TestCase):
    def test_moneyline_and_sog_alternate_are_allowed(self):
        self.assertEqual(rmp.ALLOWED_MARKET_FAMILIES, frozenset({"MONEYLINE", "PLAYER_SOG_ALTERNATE"}))

    def test_an_unlisted_market_family_is_never_eligible(self):
        for family in ("GOALIE_SAVES", "PLAYER_POINTS", "PLAYER_ASSISTS", "ALTERNATE_TEAM_TOTAL",
                       "PLAYER_HITS", "PLAYER_BLOCKS"):
            leg = _leg(market_family=family)
            self.assertFalse(rmp.leg_is_eligible(leg), f"{family} must never be parlay-eligible in V1")

    def test_sog_threshold_must_be_in_the_actionable_set(self):
        for bad_threshold in (1, 6, 7, 8):
            self.assertFalse(rmp.leg_is_eligible(_leg(threshold=bad_threshold)),
                              f"SOG threshold {bad_threshold} must not be parlay-eligible")
        for good_threshold in (2, 3, 4, 5):
            self.assertTrue(rmp.leg_is_eligible(_leg(threshold=good_threshold)))

    def test_moneyline_has_no_threshold_requirement(self):
        self.assertTrue(rmp.leg_is_eligible(_moneyline_leg()))


class TestFullGateRequired(unittest.TestCase):
    def test_each_gate_individually_blocks_eligibility(self):
        for flag in ("provider_contract_verified", "model_threshold_eligible", "identity_resolved",
                     "price_fresh", "event_not_started"):
            leg = _leg(**{flag: False})
            self.assertFalse(rmp.leg_is_eligible(leg), f"{flag}=False must block eligibility")

    def test_probability_must_be_a_real_open_interval_value(self):
        self.assertFalse(rmp.leg_is_eligible(_leg(conservative_probability=0.0)))
        self.assertFalse(rmp.leg_is_eligible(_leg(conservative_probability=1.0)))


class TestSameGameExclusion(unittest.TestCase):
    def test_legs_share_a_game_detects_duplicates(self):
        legs = [_leg(game_id="G1"), _leg(game_id="G1"), _leg(game_id="G2")]
        self.assertTrue(rmp.legs_share_a_game(legs))

    def test_cross_game_legs_do_not_share_a_game(self):
        legs = [_leg(game_id="G1"), _leg(game_id="G2"), _leg(game_id="G3")]
        self.assertFalse(rmp.legs_share_a_game(legs))

    def test_same_game_pair_is_never_used_even_if_it_would_otherwise_qualify(self):
        # Two legs from G1 alone would trivially clear 70% (0.95*0.95=0.9025) with
        # positive edge, but sharing a game means they must NEVER be combined --
        # proving this engine never "blindly multiplies same-game legs".
        same_game_a = _leg(game_id="G1", participant_id="P1", conservative_probability=0.95, american_price=-500)
        same_game_b = _leg(game_id="G1", participant_id="P2", conservative_probability=0.95, american_price=-500)
        # A real third, cross-game leg that is too weak on its own to reach 70%
        # together with only one of the G1 legs.
        weak_leg = _leg(game_id="G2", participant_id="P3", conservative_probability=0.60, american_price=-150)
        result = rmp.build_real_market_parlay([same_game_a, same_game_b, weak_leg])
        self.assertEqual(result["status"], "NO_QUALIFYING_PARLAY")


class TestJointProbabilityFloor(unittest.TestCase):
    def _three_cross_game_legs(self, conservative_probability, american_price=-150):
        return [_leg(game_id=f"G{i}", participant_id=f"P{i}",
                     conservative_probability=conservative_probability, american_price=american_price)
                for i in range(3)]

    def test_below_70_percent_is_rejected(self):
        # 0.85^3 = 0.614125, well under the 0.70 hard floor.
        legs = self._three_cross_game_legs(0.85)
        result = rmp.build_real_market_parlay(legs)
        self.assertEqual(result["status"], "NO_QUALIFYING_PARLAY")

    def test_at_or_above_70_percent_with_positive_edge_qualifies(self):
        # 0.90^3 = 0.729 >= 0.70; price -150 implies 0.60 -> 0.60^3 = 0.216 << 0.729,
        # a large positive combo edge.
        legs = self._three_cross_game_legs(0.90)
        result = rmp.build_real_market_parlay(legs)
        self.assertEqual(result["status"], "QUALIFIED")
        self.assertEqual(result["recommended_legs"], 3)
        self.assertAlmostEqual(result["combo"].joint_probability, 0.729, places=6)
        self.assertGreater(result["combo"].combo_edge, 0.0)

    def test_high_joint_probability_but_negative_combo_edge_is_rejected(self):
        # 0.90^3 = 0.729 clears the floor, but pricing each leg at -2000
        # (implied 0.952381) means the market already prices these legs
        # MORE confidently than the model does -- 0.952381^3 = 0.86366 > 0.729,
        # a genuinely negative combo edge that must still be rejected.
        legs = self._three_cross_game_legs(0.90, american_price=-2000)
        result = rmp.build_real_market_parlay(legs)
        self.assertEqual(result["status"], "NO_QUALIFYING_PARLAY")


class TestLegCountNeverForced(unittest.TestCase):
    def test_zero_legs_is_a_valid_pass(self):
        result = rmp.build_real_market_parlay([])
        self.assertEqual(result["status"], "NO_QUALIFYING_PARLAY")

    def test_one_or_two_eligible_legs_is_a_valid_pass(self):
        self.assertEqual(rmp.build_real_market_parlay([_leg(game_id="G1")])["status"], "NO_QUALIFYING_PARLAY")
        self.assertEqual(
            rmp.build_real_market_parlay([_leg(game_id="G1"), _leg(game_id="G2")])["status"],
            "NO_QUALIFYING_PARLAY")

    def test_ineligible_legs_are_never_counted_toward_the_minimum(self):
        legs = [_leg(game_id="G1", market_family="GOALIE_SAVES"),
                _leg(game_id="G2", market_family="GOALIE_SAVES"),
                _leg(game_id="G3", conservative_probability=0.90)]
        result = rmp.build_real_market_parlay(legs)
        self.assertEqual(result["status"], "NO_QUALIFYING_PARLAY")


class TestInformational2LegFallback(unittest.TestCase):
    """Platform Recovery block (2026-09-29): when the monitored 3/4-leg
    cohort doesn't qualify but a real, quality-gated 2-leg combo exists,
    it is surfaced as real information -- never as a monitored bet."""

    def test_two_strong_eligible_legs_surface_an_informational_2leg_combo(self):
        legs = [_leg(game_id="G1", conservative_probability=0.90),
                _leg(game_id="G2", conservative_probability=0.90)]
        result = rmp.build_real_market_parlay(legs)
        self.assertEqual(result["status"], "NO_QUALIFYING_PARLAY")
        self.assertIsNotNone(result["informational_2leg"])
        self.assertEqual(len(result["informational_2leg"].legs), 2)

    def test_zero_or_one_eligible_legs_has_no_informational_2leg(self):
        self.assertIsNone(rmp.build_real_market_parlay([])["informational_2leg"])
        self.assertIsNone(rmp.build_real_market_parlay([_leg(game_id="G1")])["informational_2leg"])

    def test_weak_2leg_below_the_joint_probability_floor_has_no_informational_2leg(self):
        legs = [_leg(game_id="G1", conservative_probability=0.5),
                _leg(game_id="G2", conservative_probability=0.5)]
        result = rmp.build_real_market_parlay(legs)
        self.assertIsNone(result["informational_2leg"])

    def test_informational_2leg_key_is_absent_once_a_monitored_parlay_qualifies(self):
        legs = [_leg(game_id=f"G{i}", conservative_probability=0.90) for i in range(3)]
        result = rmp.build_real_market_parlay(legs)
        self.assertEqual(result["status"], "QUALIFIED")
        self.assertNotIn("informational_2leg", result)

    def test_informational_2leg_still_respects_the_same_game_exclusion(self):
        legs = [_leg(game_id="G1", conservative_probability=0.90),
                _leg(game_id="G1", conservative_probability=0.90, participant_id="P2")]
        result = rmp.build_real_market_parlay(legs)
        self.assertIsNone(result["informational_2leg"])

    def test_informational_2leg_surfaces_when_three_eligible_legs_exist_but_no_3leg_qualifies(self):
        legs = [_leg(game_id="G1", conservative_probability=0.95),
                _leg(game_id="G2", conservative_probability=0.95),
                _leg(game_id="G3", conservative_probability=0.3)]
        result = rmp.build_real_market_parlay(legs)
        self.assertEqual(result["status"], "NO_QUALIFYING_PARLAY")
        self.assertIsNotNone(result["informational_2leg"])
        self.assertEqual({l.game_id for l in result["informational_2leg"].legs}, {"G1", "G2"})


class TestOfferedParlayPriceNeverFabricated(unittest.TestCase):
    def test_offered_parlay_price_is_always_none(self):
        legs = [_leg(game_id=f"G{i}", conservative_probability=0.90) for i in range(3)]
        result = rmp.build_real_market_parlay(legs)
        self.assertEqual(result["status"], "QUALIFIED")
        self.assertIsNone(result["combo"].offered_parlay_price)

    def test_estimated_combo_price_is_derived_only_from_real_leg_prices(self):
        legs = [_leg(game_id=f"G{i}", conservative_probability=0.90, american_price=-150) for i in range(3)]
        result = rmp.build_real_market_parlay(legs)
        # estimated_combo_price must reflect the (worse) market-implied
        # probability product, not the model's own fair price.
        self.assertNotAlmostEqual(result["combo"].estimated_combo_price, result["combo"].fair_combo_price, places=2)


class TestFourLegSelection(unittest.TestCase):
    def test_prefers_4_legs_when_the_4th_still_clears_every_gate(self):
        legs = [_leg(game_id=f"G{i}", conservative_probability=0.95, american_price=-300) for i in range(3)]
        legs.append(_leg(game_id="G3", conservative_probability=0.90, american_price=-300))
        result = rmp.build_real_market_parlay(legs)
        self.assertEqual(result["status"], "QUALIFIED")
        self.assertEqual(result["recommended_legs"], 4)
        self.assertEqual(len(result["combo"].legs), 4)
        self.assertIsNotNone(result["alternative_3leg"])

    def test_falls_back_to_3_legs_when_the_4th_drags_below_the_floor(self):
        legs = [_leg(game_id=f"G{i}", conservative_probability=0.95, american_price=-300) for i in range(3)]
        legs.append(_leg(game_id="G3", conservative_probability=0.50, american_price=-300))
        result = rmp.build_real_market_parlay(legs)
        self.assertEqual(result["status"], "QUALIFIED")
        self.assertEqual(result["recommended_legs"], 3)
        self.assertEqual(len(result["combo"].legs), 3)
        self.assertIsNone(result["alternative_3leg"])


class TestRealCertifiedPayloadCompatibility(unittest.TestCase):
    """Proves a ParlayLeg built from the REAL, certified
    provider_adapter.PLAYER_SOG_ALTERNATE output (the same fixture
    TestPlayerSogAlternateContractParity uses) is genuinely compatible with
    this engine -- not a disconnected, hand-rolled leg shape."""

    def test_real_certified_sog_quote_builds_an_eligible_leg(self):
        from research.generic_prop_pricing import provider_adapter as pa
        from research.live_sog_pricing import market_parser

        path = Path(__file__).resolve().parent / "fixtures" / "draftkings_player_sog_alternate_real_payload.json"
        with open(path) as f:
            payload = json.load(f)
        quotes = market_parser.parse_event_odds_response(payload)
        ladder = market_parser.group_alternate_ladder(quotes)
        key = next(k for k in ladder if k[2] == "Brady Tkachuk")
        quote = ladder[key][3.5]  # Over 3.5 -> model threshold 4+

        parsed = pa.parse_the_odds_api_market(
            quote, sportsbook="draftkings", canonical_market_id="PLAYER_SOG_ALTERNATE",
            event_id=payload["id"], player_id="P_TKACHUK_BRADY")
        self.assertEqual(parsed["status"], "PARSED")
        market = parsed["market"]

        leg = rmp.ParlayLeg(
            game_id="real-game-1", event_id=market.event_id, market_family=market.canonical_market_id,
            participant_id=market.player_id, participant_name="Brady Tkachuk", side=market.side,
            threshold=market.threshold, american_price=market.american_price,
            conservative_probability=0.55, sportsbook=market.sportsbook,
            captured_at_utc=market.captured_at_utc, provider_contract_verified=True,
            model_threshold_eligible=True, identity_resolved=True, price_fresh=True,
            event_not_started=True,
        )
        self.assertTrue(rmp.leg_is_eligible(leg))
        self.assertEqual(leg.threshold, 4)
        self.assertEqual(leg.american_price, 195)


if __name__ == "__main__":
    unittest.main()
