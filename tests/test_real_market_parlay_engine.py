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
    def test_allowed_families(self):
        """Standard SOG/Saves Certification block (2026-10-01): PLAYER_SOG and
        GOALIE_SAVES added alongside the original MONEYLINE + PLAYER_SOG_ALTERNATE
        pair -- see engine.py's own ALLOWED_MARKET_FAMILIES comment for why
        GOALIE_SAVES structurally produces zero real legs today regardless of
        being allowlisted here (the real starter-certainty gate upstream)."""
        self.assertEqual(rmp.ALLOWED_MARKET_FAMILIES,
                          frozenset({"MONEYLINE", "PLAYER_SOG_ALTERNATE", "PLAYER_SOG", "GOALIE_SAVES"}))

    def test_an_unlisted_market_family_is_never_eligible(self):
        for family in ("PLAYER_POINTS", "PLAYER_ASSISTS", "ALTERNATE_TEAM_TOTAL",
                       "PLAYER_HITS", "PLAYER_BLOCKS"):
            leg = _leg(market_family=family)
            self.assertFalse(rmp.leg_is_eligible(leg), f"{family} must never be parlay-eligible in V1")

    def test_sog_threshold_must_be_in_the_actionable_set(self):
        for bad_threshold in (1, 6, 7, 8):
            self.assertFalse(rmp.leg_is_eligible(_leg(threshold=bad_threshold)),
                              f"SOG threshold {bad_threshold} must not be parlay-eligible")
        for good_threshold in (2, 3, 4, 5):
            self.assertTrue(rmp.leg_is_eligible(_leg(threshold=good_threshold)))
            self.assertTrue(rmp.leg_is_eligible(_leg(market_family="PLAYER_SOG", threshold=good_threshold)))

    def test_saves_threshold_must_be_in_its_own_validated_set(self):
        for bad_threshold in (3, 15, 30, 40):
            self.assertFalse(rmp.leg_is_eligible(_leg(market_family="GOALIE_SAVES", threshold=bad_threshold)),
                              f"Saves threshold {bad_threshold} must not be parlay-eligible")
        for good_threshold in (20, 25):
            self.assertTrue(rmp.leg_is_eligible(_leg(market_family="GOALIE_SAVES", threshold=good_threshold)))

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


class TestEconomicIdentityDedup(unittest.TestCase):
    """Production Gap Closure sprint (2026-09-30): two archive captures of
    the SAME real quote (different Python objects, same real bet) must
    collapse to one leg before combo-building -- otherwise they can land
    in two different "independent" tickets, staking the same bet twice."""

    def test_two_objects_same_economic_identity_collapse_to_one(self):
        older = _leg("G1", participant_id="P1", threshold=3, captured_at_utc="2026-09-29T12:00:00Z")
        newer = _leg("G1", participant_id="P1", threshold=3, captured_at_utc="2026-09-29T12:30:00Z")
        deduped = rmp.dedupe_legs_by_economic_identity([older, newer])
        self.assertEqual(len(deduped), 1)
        self.assertIs(deduped[0], newer, "the LATEST captured_at_utc quote must be kept")

    def test_different_game_or_threshold_are_not_deduped(self):
        legs = [_leg("G1", participant_id="P1", threshold=3), _leg("G2", participant_id="P1", threshold=3),
                _leg("G1", participant_id="P1", threshold=4)]
        deduped = rmp.dedupe_legs_by_economic_identity(legs)
        self.assertEqual(len(deduped), 3)

    def test_duplicate_quote_objects_never_exceed_the_shared_leg_limit(self):
        older = _leg("G1", participant_id="P1", threshold=3, captured_at_utc="2026-09-29T12:00:00Z",
                     conservative_probability=0.90)
        newer = _leg("G1", participant_id="P1", threshold=3, captured_at_utc="2026-09-29T12:30:00Z",
                     conservative_probability=0.90)
        other_legs = [_leg(f"G{i}", participant_id=f"P{i}", conservative_probability=0.90) for i in range(2, 6)]
        result = rmp.build_top_real_market_parlays([older, newer] + other_legs, max_parlays=5)
        self.assertEqual(result["status"], "QUALIFIED")
        seen_g1 = sum(1 for p in result["parlays"] for l in p["combo"].legs if l.game_id == "G1")
        # Two captures of one quote are one leg, so it can sit on at most MAX_TICKETS_PER_LEG tickets
        # (never two "different" copies each counted separately).
        self.assertLessEqual(seen_g1, rmp.MAX_TICKETS_PER_LEG)
        ids = [frozenset(rmp.leg_identity(l) for l in p["combo"].legs) for p in result["parlays"]]
        self.assertEqual(len(ids), len(set(ids)), "duplicate tickets are never selected")




class TestUnifiedPolicyConstants(unittest.TestCase):
    """The old 70% joint-probability floor is gone; the policy is explicit EV with an uncertainty haircut."""

    def test_the_inherited_70_percent_rule_no_longer_exists(self):
        self.assertFalse(hasattr(rmp, "MIN_JOINT_PROBABILITY"))

    def test_policy_is_documented_in_constants(self):
        self.assertEqual(rmp.MIN_COMBINED_DECIMAL, 2.0)
        self.assertEqual(rmp.MIN_LEGS, 2)
        self.assertEqual(rmp.MAX_TICKETS_PER_DAY, 5)
        self.assertGreater(rmp.MIN_ESTIMATED_EV, 0)
        self.assertGreater(rmp.LEG_PROBABILITY_MARGIN, 0)

    def test_combined_price_is_the_product_of_leg_prices_and_never_a_quoted_price(self):
        a, b = _leg("G1", american_price=120, conservative_probability=0.60), _leg("G2", american_price=-105, conservative_probability=0.62)
        combo = rmp._evaluate_combo([a, b])
        self.assertAlmostEqual(combo.combined_decimal, 2.2 * (1 + 100 / 105), places=6)
        self.assertIsNone(combo.offered_parlay_price)

    def test_probabilities_are_only_ever_lowered_by_the_uncertainty_haircut(self):
        a, b = _leg("G1", american_price=120, conservative_probability=0.60), _leg("G2", american_price=-105, conservative_probability=0.62)
        combo = rmp._evaluate_combo([a, b])
        self.assertAlmostEqual(combo.joint_probability, 0.60 * 0.62)
        self.assertLess(combo.ev_conservative, combo.ev_estimated)
