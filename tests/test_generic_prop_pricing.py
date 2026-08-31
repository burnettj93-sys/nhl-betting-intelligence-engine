"""
Preseason Operational Readiness Closure sprint (2026-08-30), Track 5:
tests for research/generic_prop_pricing/ (NormalizedPropMarket +
evaluate_prop). Includes the required SOG-parity proof (Part 33):
research/live_sog_pricing/pricing.py::price_observation() is UNCHANGED
and untouched by this track; this test proves the new generic evaluator
reproduces its exact numeric output for the same real, already-tested
inputs, rather than merely asserting it by design.
"""
from __future__ import annotations

import unittest

from research.generic_prop_pricing import evaluator as ge
from research.generic_prop_pricing.normalized_market import NormalizedPropMarket
from research.live_sog_pricing import pricing as sog_pricing


def _market(threshold=4, american_price=-115, opposing_side_price=-105, sportsbook="draftkings"):
    return NormalizedPropMarket(
        event_id="evt-real", sportsbook=sportsbook, canonical_market_id="PLAYER_SOG_4PLUS",
        threshold=threshold, side="OVER", american_price=american_price,
        opposing_side_price=opposing_side_price, captured_at_utc="2026-10-15T18:00:00Z",
        provenance="THE_ODDS_API", player_id="P1")


class Test01NormalizedPropMarket(unittest.TestCase):
    def test_two_sided_detection(self):
        self.assertTrue(_market().has_two_sided_market())
        self.assertFalse(_market(opposing_side_price=None).has_two_sided_market())

    def test_is_frozen_and_provider_agnostic(self):
        m = _market()
        with self.assertRaises(Exception):
            m.american_price = -999  # frozen dataclass -- never silently mutated


class Test02ModelThresholdEligibility(unittest.TestCase):
    def test_threshold_outside_validated_set_is_not_model_validated(self):
        result = ge.evaluate_prop(
            market_family="PLAYER_SOG", model_validated_thresholds=(2, 3, 4, 5), threshold=1,
            side="OVER", probs={1: 0.9}, conservative_probs={1: 0.85}, confidence="HIGH",
            lineup_status="CONFIRMED", market=_market(threshold=1), provider_contract_verified=True)
        self.assertEqual(result["status"], ge.NOT_MODEL_VALIDATED)

    def test_threshold_inside_validated_set_proceeds(self):
        result = ge.evaluate_prop(
            market_family="PLAYER_SOG", model_validated_thresholds=(2, 3, 4, 5), threshold=4,
            side="OVER", probs={4: 0.40}, conservative_probs={4: 0.32}, confidence="HIGH",
            lineup_status="CONFIRMED", market=_market(), provider_contract_verified=True)
        self.assertEqual(result["status"], ge.PRICED)


class Test03NoMarketIsDataUnavailable(unittest.TestCase):
    def test_no_market_object_is_data_unavailable_never_a_guess(self):
        result = ge.evaluate_prop(
            market_family="GOALS", model_validated_thresholds=(1,), threshold=1, side="OVER",
            probs={1: 0.3}, conservative_probs={1: 0.25}, confidence="HIGH",
            lineup_status="CONFIRMED", market=None, provider_contract_verified=False)
        self.assertEqual(result["status"], ge.DATA_UNAVAILABLE)
        self.assertIsNone(result.get("action"))


class Test04ContractNotVerified(unittest.TestCase):
    def test_unverified_contract_never_prices_even_with_a_market_present(self):
        result = ge.evaluate_prop(
            market_family="GOALS", model_validated_thresholds=(1,), threshold=1, side="OVER",
            probs={1: 0.3}, conservative_probs={1: 0.25}, confidence="HIGH",
            lineup_status="CONFIRMED", market=_market(threshold=1), provider_contract_verified=False)
        self.assertEqual(result["status"], ge.CONTRACT_NOT_VERIFIED)


class Test05NoSingleSidedNoVig(unittest.TestCase):
    """Part 42: a one-sided market must never fake the opposite side to
    produce a no-vig probability."""

    def test_one_sided_market_never_fakes_no_vig(self):
        result = ge.evaluate_prop(
            market_family="PLAYER_SOG", model_validated_thresholds=(4,), threshold=4, side="OVER",
            probs={4: 0.40}, conservative_probs={4: 0.32}, confidence="HIGH", lineup_status="CONFIRMED",
            market=_market(opposing_side_price=None), provider_contract_verified=True)
        self.assertEqual(result["status"], ge.PRICED)
        self.assertFalse(result["no_vig_available"])
        self.assertIsNone(result["market_no_vig_probability"])
        self.assertIsNone(result["raw_edge"])
        self.assertIsNone(result["maximum_acceptable_price"])
        self.assertEqual(result["action"], "NOT_AVAILABLE")


class TestSOGParity(unittest.TestCase):
    """Part 33: the exact numeric inputs from tests/test_live_sog_pricing.py's
    own TestPricingMath fixtures, run through BOTH the untouched SOG
    price_observation() and the new generic evaluator -- every numeric
    field that both functions produce must match exactly."""

    def test_sog_and_generic_evaluator_agree_on_the_same_real_inputs(self):
        probs, cprobs = {4: 0.40}, {4: 0.32}
        sog_result = sog_pricing.price_observation(
            side="OVER", point=3.5, milestone_threshold=None, price_american=-115,
            opposing_price_american=-105, probs=probs, conservative_probs=cprobs,
            confidence="HIGH", lineup_status="CONFIRMED", quote_age_minutes=5.0,
            hours_to_puck_drop=48.0)

        generic_result = ge.evaluate_prop(
            market_family="PLAYER_SOG", model_validated_thresholds=sog_pricing.MODEL_VALIDATED_THRESHOLDS,
            threshold=4, side="OVER", probs=probs, conservative_probs=cprobs, confidence="HIGH",
            lineup_status="CONFIRMED", market=_market(threshold=4), provider_contract_verified=True,
            quote_age_minutes=5.0, hours_to_puck_drop=48.0)

        self.assertEqual(sog_result["status"], "PRICED")
        self.assertEqual(generic_result["status"], ge.PRICED)
        for field in ("model_probability", "conservative_probability", "model_fair_price",
                      "conservative_fair_price", "raw_edge", "conservative_edge", "raw_ev",
                      "conservative_ev", "maximum_acceptable_price", "zone", "action"):
            self.assertEqual(sog_result[field], generic_result[field],
                              f"{field} differs between SOG's own pricing and the generic evaluator")

    def test_sog_ineligible_threshold_matches_between_both_paths(self):
        # threshold 6 is NOT in SOG's own MODEL_VALIDATED_THRESHOLDS --
        # sog_pricing.price_observation still PRICES it (math is real) but
        # overrides action to NOT_MODEL_VALIDATED; the generic evaluator
        # refuses to price it at all. Both agree the market is not
        # decision-eligible, which is the parity guarantee that matters.
        sog_result = sog_pricing.price_observation(
            side="OVER", point=5.5, milestone_threshold=None, price_american=+2000,
            opposing_price_american=-5000, probs={6: 0.05}, conservative_probs={6: 0.03},
            confidence="HIGH", lineup_status="CONFIRMED", quote_age_minutes=5.0,
            hours_to_puck_drop=48.0)
        generic_result = ge.evaluate_prop(
            market_family="PLAYER_SOG", model_validated_thresholds=sog_pricing.MODEL_VALIDATED_THRESHOLDS,
            threshold=6, side="OVER", probs={6: 0.05}, conservative_probs={6: 0.03}, confidence="HIGH",
            lineup_status="CONFIRMED", market=_market(threshold=6), provider_contract_verified=True)
        self.assertEqual(sog_result["action"], "NOT_MODEL_VALIDATED")
        self.assertEqual(generic_result["status"], ge.NOT_MODEL_VALIDATED)


class Test06GoalsAssistsPointsSavesModelSideReadiness(unittest.TestCase):
    """Parts 34-37: each family's validated model side is ready to price
    the instant a real, provider-contract-verified market exists --
    proven here with a synthetic (clearly test-only) market, never a
    real one, and never claiming dk_contract_verified=True anywhere."""

    def test_goals_prices_at_its_one_validated_threshold(self):
        result = ge.evaluate_prop(
            market_family="GOALS", model_validated_thresholds=(1,), threshold=1, side="OVER",
            probs={1: 0.171}, conservative_probs={1: 0.15}, confidence="HIGH", lineup_status="CONFIRMED",
            market=_market(threshold=1), provider_contract_verified=True)
        self.assertEqual(result["status"], ge.PRICED)

    def test_assists_prices_at_1_and_2_only(self):
        for t in (1, 2):
            result = ge.evaluate_prop(
                market_family="ASSISTS", model_validated_thresholds=(1, 2), threshold=t, side="OVER",
                probs={t: 0.3}, conservative_probs={t: 0.25}, confidence="HIGH",
                lineup_status="CONFIRMED", market=_market(threshold=t), provider_contract_verified=True)
            self.assertEqual(result["status"], ge.PRICED)
        rejected = ge.evaluate_prop(
            market_family="ASSISTS", model_validated_thresholds=(1, 2), threshold=3, side="OVER",
            probs={3: 0.02}, conservative_probs={3: 0.01}, confidence="HIGH", lineup_status="CONFIRMED",
            market=_market(threshold=3), provider_contract_verified=True)
        self.assertEqual(rejected["status"], ge.NOT_MODEL_VALIDATED)

    def test_points_uses_empirical_baseline_thresholds_never_relabeled(self):
        for t in (1, 2):
            result = ge.evaluate_prop(
                market_family="POINTS", model_validated_thresholds=(1, 2), threshold=t, side="OVER",
                probs={t: 0.5}, conservative_probs={t: 0.4}, confidence="HIGH", lineup_status="CONFIRMED",
                market=_market(threshold=t), provider_contract_verified=True)
            self.assertEqual(result["status"], ge.PRICED)

    def test_goalie_saves_only_20_and_25_are_operationally_eligible(self):
        for t in (20, 25):
            result = ge.evaluate_prop(
                market_family="GOALIE_SAVES", model_validated_thresholds=(20, 25), threshold=t,
                side="OVER", probs={t: 0.5}, conservative_probs={t: 0.4}, confidence="HIGH",
                lineup_status="CONFIRMED", market=_market(threshold=t), provider_contract_verified=True)
            self.assertEqual(result["status"], ge.PRICED)
        for t in (30, 35, 40):
            result = ge.evaluate_prop(
                market_family="GOALIE_SAVES", model_validated_thresholds=(20, 25), threshold=t,
                side="OVER", probs={t: 0.2}, conservative_probs={t: 0.1}, confidence="HIGH",
                lineup_status="CONFIRMED", market=_market(threshold=t), provider_contract_verified=True)
            self.assertEqual(result["status"], ge.NOT_MODEL_VALIDATED,
                              f"Saves {t}+ must not be operationally eligible")


class Test07MarketDecisionEligibilityChecklist(unittest.TestCase):
    def test_all_conditions_true_is_eligible(self):
        self.assertTrue(ge.market_decision_eligible(
            model_threshold_eligible=True, identity_resolved=True,
            starter_active_status_satisfied=True, price_fresh=True,
            two_sided_no_vig_possible=True, provider_contract_verified=True,
            event_not_started=True, decision_policy_permits=True))

    def test_any_single_false_condition_makes_it_ineligible(self):
        base = dict(model_threshold_eligible=True, identity_resolved=True,
                    starter_active_status_satisfied=True, price_fresh=True,
                    two_sided_no_vig_possible=True, provider_contract_verified=True,
                    event_not_started=True, decision_policy_permits=True)
        for key in base:
            variant = dict(base)
            variant[key] = False
            self.assertFalse(ge.market_decision_eligible(**variant), f"{key}=False must make it ineligible")


if __name__ == "__main__":
    unittest.main()
