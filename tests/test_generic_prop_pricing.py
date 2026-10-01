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

import json
import unittest

from research.generic_prop_pricing import evaluator as ge
from research.generic_prop_pricing import provider_adapter as pa
from research.generic_prop_pricing import team_totals_parser as ttp
from research.generic_prop_pricing.normalized_market import NormalizedPropMarket
from research.live_sog_pricing import market_parser as sog_market_parser
from research.live_sog_pricing import normalized_market_adapter as nma
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


class TestMoneylineContractParity(unittest.TestCase):
    """Live DK / Paper Bankroll completion sprint, Parts 9-17: the
    real-payload regression test required before dk_contract_verified
    can be True for MONEYLINE. Loads a sanitized, real, live-captured
    DraftKings h2h payload (tests/fixtures/draftkings_h2h_real_payload.json)
    -- not a synthetic guess."""

    @staticmethod
    def _load_fixture():
        import json
        from pathlib import Path
        path = Path(__file__).resolve().parent / "fixtures" / "draftkings_h2h_real_payload.json"
        with open(path) as f:
            return json.load(f)

    def test_verified_contracts_is_exactly_these_five_real_observed_payloads(self):
        """SOG Contract Certification block (2026-09-29): two more real, archived payloads
        certified (PLAYER_SOG_ALTERNATE via player_shots_on_goal_alternate, ALTERNATE_TEAM_TOTAL
        via alternate_team_totals) -- see TestPlayerSogAlternateContractParity /
        TestAlternateTeamTotalContractParity below.

        Standard SOG/Saves Certification block (2026-10-01): two more real, archived
        payloads certified (bare PLAYER_SOG via the standard, two-sided
        player_shots_on_goal shape; GOALIE_SAVES via player_total_saves) -- see
        TestPlayerSogStandardContractParity / TestGoalieSavesStandardContractParity
        below. This bare PLAYER_SOG entry is legitimate, not the overgeneralization
        test_other_prop_families_remain_unverified's docstring once warned against:
        that warning was correct when written (no standard-shape payload had been
        observed yet); one has since been captured and directly inspected."""
        self.assertEqual(pa.VERIFIED_CONTRACTS, frozenset({
            ("draftkings", "MONEYLINE"),
            ("draftkings", "PLAYER_SOG_ALTERNATE"),
            ("draftkings", "ALTERNATE_TEAM_TOTAL"),
            ("draftkings", "PLAYER_SOG"),
            ("draftkings", "GOALIE_SAVES"),
        }))

    def test_parses_the_real_payload_correctly(self):
        payload = self._load_fixture()
        result = pa.parse_the_odds_api_h2h_market(payload)
        self.assertEqual(result["status"], "PARSED")
        market = result["market"]
        self.assertEqual(market.event_id, "9de33ce1013f2a3375dbded2c9fbc7d6")
        self.assertEqual(market.sportsbook, "draftkings")
        self.assertEqual(market.home_team_abbrev, "CAR")
        self.assertEqual(market.away_team_abbrev, "FLA")
        self.assertEqual(market.home_price, -130.0)
        self.assertEqual(market.away_price, 110.0)
        self.assertEqual(market.captured_at_utc, "2026-08-31T12:37:46Z")
        self.assertEqual(market.provenance, "THE_ODDS_API")
        self.assertEqual(market.commence_time_utc, "2026-09-29T21:10:00Z")

    def test_unrecognized_team_name_is_data_unavailable_not_a_crash(self):
        payload = dict(self._load_fixture())
        payload["home_team"] = "Some Made Up Team"
        result = pa.parse_the_odds_api_h2h_market(payload)
        self.assertEqual(result["status"], "DATA_UNAVAILABLE")

    def test_missing_bookmaker_block_is_data_unavailable(self):
        payload = dict(self._load_fixture())
        payload["bookmakers"] = []
        result = pa.parse_the_odds_api_h2h_market(payload)
        self.assertEqual(result["status"], "DATA_UNAVAILABLE")

    def test_missing_h2h_market_is_data_unavailable(self):
        payload = self._load_fixture()
        payload = json.loads(json.dumps(payload))  # deep copy
        payload["bookmakers"][0]["markets"] = [
            m for m in payload["bookmakers"][0]["markets"] if m["key"] != "h2h"]
        result = pa.parse_the_odds_api_h2h_market(payload)
        self.assertEqual(result["status"], "DATA_UNAVAILABLE")

    def test_unverified_sportsbook_returns_contract_not_verified(self):
        payload = self._load_fixture()
        result = pa.parse_the_odds_api_h2h_market(payload, sportsbook="fanduel")
        self.assertEqual(result["status"], ge.CONTRACT_NOT_VERIFIED)

    def test_other_prop_families_remain_unverified(self):
        # Part 16: one observed payload must never overgeneralize to families
        # that were never actually parsed against a real payload. ALTERNATE_TEAM_TOTAL
        # was removed from this list by the SOG Contract Certification block (2026-09-29)
        # -- it now has its own real, archived payload and regression test (see
        # TestAlternateTeamTotalContractParity below).
        #
        # PLAYER_SOG (bare) and GOALIE_SAVES were ALSO removed from this list by the
        # Standard SOG/Saves Certification block (2026-10-01): both now have their own
        # real, archived standard (two-sided) payloads and regression tests (see
        # TestPlayerSogStandardContractParity / TestGoalieSavesStandardContractParity
        # below). Everything else here still has zero observed payload evidence and
        # must stay unverified.
        for market_id in ("PLAYER_GOALS", "PLAYER_ASSISTS", "PLAYER_POINTS", "SPREADS", "TOTALS"):
            self.assertFalse(pa.is_contract_verified("draftkings", market_id),
                              f"{market_id} must remain CONTRACT_NOT_VERIFIED")


class TestPlayerSogAlternateContractParity(unittest.TestCase):
    """SOG Contract Certification block (2026-09-29): the real-payload regression test
    required before (draftkings, PLAYER_SOG_ALTERNATE) can sit in VERIFIED_CONTRACTS. Loads a
    sanitized, real, archived DraftKings player_shots_on_goal_alternate payload
    (tests/fixtures/draftkings_player_sog_alternate_real_payload.json, FLA@CAR,
    2026-09-29T12:14:34Z) -- not a synthetic guess -- and drives it through the REAL
    pipeline (market_parser.parse_event_odds_response -> group_alternate_ladder ->
    provider_adapter._parse_player_sog_alternate_quote), exactly as a live caller would.

    Named PLAYER_SOG_ALTERNATE, not bare PLAYER_SOG -- see VERIFIED_CONTRACTS's own comment
    and test_other_prop_families_remain_unverified above: bare PLAYER_SOG stays
    CONTRACT_NOT_VERIFIED because the standard player_shots_on_goal shape was never observed."""

    @staticmethod
    def _load_fixture():
        from pathlib import Path
        path = Path(__file__).resolve().parent / "fixtures" / "draftkings_player_sog_alternate_real_payload.json"
        with open(path) as f:
            return json.load(f)

    def _quotes_for(self, payload, player_name="Brady Tkachuk"):
        quotes = sog_market_parser.parse_event_odds_response(
            payload, standard_market_keys=(sog_market_parser.STANDARD_MARKET_KEY,))
        ladder = sog_market_parser.group_alternate_ladder(quotes)
        key = next(k for k in ladder if k[2] == player_name)
        return ladder[key]

    def test_parses_the_real_payload_correctly_at_the_3plus_threshold(self):
        payload = self._load_fixture()
        by_point = self._quotes_for(payload, "Brady Tkachuk")
        quote = by_point[3.5]  # sportsbook "Over 3.5" == model "4+"
        result = pa.parse_the_odds_api_market(
            quote, sportsbook="draftkings", canonical_market_id="PLAYER_SOG_ALTERNATE",
            event_id=payload["id"], player_id="P_TKACHUK_BRADY")
        self.assertEqual(result["status"], "PARSED")
        market = result["market"]
        self.assertEqual(market.event_id, "9de33ce1013f2a3375dbded2c9fbc7d6")
        self.assertEqual(market.sportsbook, "draftkings")
        self.assertEqual(market.canonical_market_id, "PLAYER_SOG_ALTERNATE")
        self.assertEqual(market.threshold, 4)  # Over 3.5 == 4+, never confused with 3+
        self.assertEqual(market.side, "OVER")
        self.assertEqual(market.american_price, 195)
        self.assertIsNone(market.opposing_side_price)  # one-sided: DK never posts an Under here
        self.assertEqual(market.player_id, "P_TKACHUK_BRADY")
        self.assertEqual(market.captured_at_utc, "2026-09-29T12:14:34Z")
        self.assertEqual(market.provenance, "THE_ODDS_API")

    def test_over_2_5_maps_to_3plus_not_2plus(self):
        payload = self._load_fixture()
        by_point = self._quotes_for(payload, "Brady Tkachuk")
        result = pa.parse_the_odds_api_market(
            by_point[2.5], sportsbook="draftkings", canonical_market_id="PLAYER_SOG_ALTERNATE",
            event_id=payload["id"], player_id="P_TKACHUK_BRADY")
        self.assertEqual(result["market"].threshold, 3)

    def test_malformed_shape_fails_closed(self):
        bad_quote = {"market_key": sog_market_parser.ALTERNATE_MARKET_KEY,
                     "shape": "milestone", "side": "OVER_MILESTONE", "point": None,
                     "price_american": 100, "market_last_update_utc": "2026-09-29T12:14:34Z",
                     "bookmaker_last_update_utc": "2026-09-29T12:14:34Z"}
        result = pa.parse_the_odds_api_market(
            bad_quote, sportsbook="draftkings", canonical_market_id="PLAYER_SOG_ALTERNATE",
            event_id="evt", player_id="P1")
        self.assertEqual(result["status"], pa.MALFORMED_QUOTE_SHAPE)

    def test_non_half_point_line_fails_closed(self):
        bad_quote = {"market_key": sog_market_parser.ALTERNATE_MARKET_KEY,
                     "shape": "over_under", "side": "OVER", "point": 3,
                     "price_american": 100, "market_last_update_utc": "2026-09-29T12:14:34Z",
                     "bookmaker_last_update_utc": "2026-09-29T12:14:34Z"}
        result = pa.parse_the_odds_api_market(
            bad_quote, sportsbook="draftkings", canonical_market_id="PLAYER_SOG_ALTERNATE",
            event_id="evt", player_id="P1")
        self.assertEqual(result["status"], pa.MALFORMED_QUOTE_SHAPE)

    def test_standard_market_key_quote_fails_closed_even_with_over_under_shape(self):
        # The exact production-risk case this block found: a quote from the NEVER-OBSERVED
        # standard player_shots_on_goal key is otherwise byte-for-byte indistinguishable from
        # a real alternate-ladder quote (same shape="over_under", same side values) -- only
        # market_key tells them apart, and this must fail closed rather than silently
        # certifying the standard shape too.
        bad_quote = {"market_key": sog_market_parser.STANDARD_MARKET_KEY,
                     "shape": "over_under", "side": "OVER", "point": 3.5,
                     "price_american": 100, "market_last_update_utc": "2026-09-29T12:14:34Z",
                     "bookmaker_last_update_utc": "2026-09-29T12:14:34Z"}
        result = pa.parse_the_odds_api_market(
            bad_quote, sportsbook="draftkings", canonical_market_id="PLAYER_SOG_ALTERNATE",
            event_id="evt", player_id="P1")
        self.assertEqual(result["status"], pa.MALFORMED_QUOTE_SHAPE)

    def test_unverified_sportsbook_returns_contract_not_verified(self):
        payload = self._load_fixture()
        by_point = self._quotes_for(payload, "Brady Tkachuk")
        result = pa.parse_the_odds_api_market(
            by_point[3.5], sportsbook="fanduel", canonical_market_id="PLAYER_SOG_ALTERNATE",
            event_id=payload["id"], player_id="P1")
        self.assertEqual(result["status"], ge.CONTRACT_NOT_VERIFIED)


class TestAlternateTeamTotalContractParity(unittest.TestCase):
    """SOG Contract Certification block (2026-09-29): the real-payload regression test
    required before (draftkings, ALTERNATE_TEAM_TOTAL) can sit in VERIFIED_CONTRACTS.
    Loads a sanitized, real, archived DraftKings alternate_team_totals payload
    (tests/fixtures/draftkings_alternate_team_totals_real_payload.json, PIT@WSH,
    2026-09-25T12:14:30Z, both teams, 24 outcomes) and drives it through the REAL
    pipeline (team_totals_parser.parse_event_odds_response ->
    group_alternate_team_total_ladder -> provider_adapter._parse_alternate_team_total_pair).
    Contract-verified here does NOT mean model-ready -- the team-total model itself
    remains research-only (flat league-mean Poisson REJECTED, see
    research/run_alternate_totals_model.py)."""

    @staticmethod
    def _load_fixture():
        from pathlib import Path
        path = Path(__file__).resolve().parent / "fixtures" / "draftkings_alternate_team_totals_real_payload.json"
        with open(path) as f:
            return json.load(f)

    def _ladder(self, payload):
        quotes = ttp.parse_event_odds_response(payload)
        return ttp.group_alternate_team_total_ladder(quotes)

    def test_parses_the_real_two_sided_payload_correctly(self):
        payload = self._load_fixture()
        ladder = self._ladder(payload)
        key = next(k for k in ladder if k[2] == "Pittsburgh Penguins" and k[3] == 2.5)
        pair = ladder[key]
        result = pa.parse_the_odds_api_market(
            pair, sportsbook="draftkings", canonical_market_id="ALTERNATE_TEAM_TOTAL",
            event_id=payload["id"], team_id="T_PIT")
        self.assertEqual(result["status"], "PARSED")
        market = result["market"]
        self.assertEqual(market.event_id, "8112572f236320d4d4c99b6eb0989b94")
        self.assertEqual(market.canonical_market_id, "ALTERNATE_TEAM_TOTAL")
        self.assertEqual(market.threshold, 3)  # Over 2.5 == 3+ goals
        self.assertEqual(market.side, "OVER")
        self.assertEqual(market.american_price, -140)
        self.assertEqual(market.opposing_side_price, 100)  # real two-sided Under
        self.assertEqual(market.team_id, "T_PIT")
        self.assertEqual(market.captured_at_utc, "2026-09-25T12:14:30Z")

    def test_neither_side_present_fails_closed(self):
        result = pa.parse_the_odds_api_market(
            {"over": None, "under": None}, sportsbook="draftkings",
            canonical_market_id="ALTERNATE_TEAM_TOTAL", event_id="evt", team_id="T1")
        self.assertEqual(result["status"], pa.MALFORMED_QUOTE_SHAPE)

    def test_unverified_sportsbook_returns_contract_not_verified(self):
        payload = self._load_fixture()
        ladder = self._ladder(payload)
        key = next(k for k in ladder if k[2] == "Pittsburgh Penguins" and k[3] == 2.5)
        result = pa.parse_the_odds_api_market(
            ladder[key], sportsbook="fanduel", canonical_market_id="ALTERNATE_TEAM_TOTAL",
            event_id=payload["id"], team_id="T_PIT")
        self.assertEqual(result["status"], ge.CONTRACT_NOT_VERIFIED)


class TestPlayerSogStandardContractParity(unittest.TestCase):
    """Standard SOG/Saves Certification block (2026-10-01): the real-payload regression
    test required before (draftkings, PLAYER_SOG) can sit in VERIFIED_CONTRACTS. Loads a
    sanitized, real, archived DraftKings player_shots_on_goal payload
    (tests/fixtures/draftkings_player_shots_on_goal_real_payload.json, TOR@MTL,
    2026-09-29T18:54:00Z, 15 real players, 30 two-sided Over/Under outcomes) -- not a
    synthetic guess -- and drives it through the REAL pipeline
    (market_parser.parse_event_odds_response -> group_standard_two_sided ->
    normalized_market_adapter.quote_to_normalized_market), exactly as
    operational/real_prop_orchestrator.py::_price_and_record_sog_pair() and
    research/real_market_parlay/real_slate_adapter.py::sog_standard_candidate_legs()
    both actually do. Deliberately NOT provider_adapter.parse_the_odds_api_market() --
    that narrower, shape-dispatch function has no case for PLAYER_SOG/GOALIE_SAVES
    and is never the real route for this family (see provider_adapter.
    VERIFIED_CONTRACTS's own comment)."""

    @staticmethod
    def _load_fixture():
        from pathlib import Path
        path = Path(__file__).resolve().parent / "fixtures" / "draftkings_player_shots_on_goal_real_payload.json"
        with open(path) as f:
            return json.load(f)

    def _pair_for(self, payload, player_name="Auston Matthews"):
        quotes = sog_market_parser.parse_event_odds_response(
            payload, standard_market_keys=(sog_market_parser.STANDARD_MARKET_KEY,))
        pairs = sog_market_parser.group_standard_two_sided(quotes)
        key = next(k for k in pairs if k[2] == player_name)
        return pairs[key]

    def test_parses_the_real_two_sided_payload_correctly(self):
        payload = self._load_fixture()
        pair = self._pair_for(payload, "Auston Matthews")
        over_q, under_q = pair["over"], pair["under"]
        self.assertIsNotNone(over_q)
        self.assertIsNotNone(under_q)
        threshold = sog_pricing.threshold_from_point(over_q["point"])
        self.assertEqual(threshold, 4)  # real line is Over 3.5 == 4+
        market, verified = nma.quote_to_normalized_market(
            over_q, market_family="PLAYER_SOG", canonical_market_id="PLAYER_SOG_4PLUS",
            threshold=threshold, side="OVER", opposing_price=under_q["price_american"],
            player_id="P_MATTHEWS_AUSTON", sportsbook="draftkings")
        self.assertTrue(verified)
        self.assertEqual(market.event_id, "485b295347cb22f002e014cb87813ed7")
        self.assertEqual(market.sportsbook, "draftkings")
        self.assertEqual(market.canonical_market_id, "PLAYER_SOG_4PLUS")
        self.assertEqual(market.threshold, 4)
        self.assertEqual(market.side, "OVER")
        self.assertEqual(market.american_price, -105)
        self.assertEqual(market.opposing_side_price, -125)  # real two-sided Under
        self.assertTrue(market.has_two_sided_market())
        self.assertEqual(market.player_id, "P_MATTHEWS_AUSTON")
        self.assertEqual(market.market_last_update_utc, "2026-09-29T18:53:48Z")
        self.assertEqual(market.provenance, "THE_ODDS_API")

    def test_unverified_sportsbook_is_not_verified(self):
        payload = self._load_fixture()
        pair = self._pair_for(payload, "Auston Matthews")
        over_q, under_q = pair["over"], pair["under"]
        threshold = sog_pricing.threshold_from_point(over_q["point"])
        _market, verified = nma.quote_to_normalized_market(
            over_q, market_family="PLAYER_SOG", canonical_market_id="PLAYER_SOG_4PLUS",
            threshold=threshold, side="OVER", opposing_price=under_q["price_american"],
            player_id="P_MATTHEWS_AUSTON", sportsbook="fanduel")
        self.assertFalse(verified)

    def test_bare_player_sog_family_is_now_verified_for_draftkings(self):
        self.assertTrue(pa.is_contract_verified("draftkings", "PLAYER_SOG"))


class TestGoalieSavesStandardContractParity(unittest.TestCase):
    """Standard SOG/Saves Certification block (2026-10-01): the real-payload regression
    test required before (draftkings, GOALIE_SAVES) can sit in VERIFIED_CONTRACTS. Loads a
    sanitized, real, archived DraftKings player_total_saves payload
    (tests/fixtures/draftkings_player_total_saves_real_payload.json, TOR@MTL, the SAME
    real event/capture as the SOG fixture above, 2 real goalies -- Sergei Bobrovsky,
    Jakub Dobes -- 4 two-sided outcomes). Driven through the identical real pipeline
    operational/real_prop_orchestrator.py::_price_and_record_saves_pair() and
    research/real_market_parlay/real_slate_adapter.py::goalie_saves_candidate_legs()
    both actually use."""

    @staticmethod
    def _load_fixture():
        from pathlib import Path
        path = Path(__file__).resolve().parent / "fixtures" / "draftkings_player_total_saves_real_payload.json"
        with open(path) as f:
            return json.load(f)

    def _pair_for(self, payload, player_name="Jakub Dobes"):
        quotes = sog_market_parser.parse_event_odds_response(
            payload, standard_market_keys=(sog_market_parser.SAVES_MARKET_KEY,))
        pairs = sog_market_parser.group_standard_two_sided(quotes, market_key=sog_market_parser.SAVES_MARKET_KEY)
        key = next(k for k in pairs if k[2] == player_name)
        return pairs[key]

    def test_parses_the_real_two_sided_payload_correctly(self):
        payload = self._load_fixture()
        pair = self._pair_for(payload, "Jakub Dobes")
        over_q, under_q = pair["over"], pair["under"]
        self.assertIsNotNone(over_q)
        self.assertIsNotNone(under_q)
        threshold = sog_pricing.threshold_from_point(over_q["point"])
        self.assertEqual(threshold, 25)  # real line is Over 24.5 == 25+
        market, verified = nma.quote_to_normalized_market(
            over_q, market_family="GOALIE_SAVES", canonical_market_id="GOALIE_SAVES_25PLUS",
            threshold=threshold, side="OVER", opposing_price=under_q["price_american"],
            goalie_id="P_DOBES_JAKUB", sportsbook="draftkings")
        self.assertTrue(verified)
        self.assertEqual(market.event_id, "485b295347cb22f002e014cb87813ed7")
        self.assertEqual(market.canonical_market_id, "GOALIE_SAVES_25PLUS")
        self.assertEqual(market.threshold, 25)
        self.assertEqual(market.side, "OVER")
        self.assertEqual(market.american_price, -115)
        self.assertEqual(market.opposing_side_price, -120)  # real two-sided Under
        self.assertTrue(market.has_two_sided_market())
        self.assertEqual(market.goalie_id, "P_DOBES_JAKUB")
        self.assertEqual(market.market_last_update_utc, "2026-09-29T18:53:48Z")

    def test_bobrovsky_line_is_a_real_non_validated_threshold(self):
        """Confirms the test fixture's OTHER real goalie (Bobrovsky, Over 26.5)
        parses correctly even though 27+ falls outside GOALIE_SAVES's validated
        threshold set (20, 25) -- parsing (this contract) and model-threshold
        validation (evaluate_prop's own separate gate) are different concerns,
        never conflated here."""
        payload = self._load_fixture()
        pair = self._pair_for(payload, "Sergei Bobrovsky")
        over_q, under_q = pair["over"], pair["under"]
        threshold = sog_pricing.threshold_from_point(over_q["point"])
        self.assertEqual(threshold, 27)
        market, verified = nma.quote_to_normalized_market(
            over_q, market_family="GOALIE_SAVES", canonical_market_id="GOALIE_SAVES_27PLUS",
            threshold=threshold, side="OVER", opposing_price=under_q["price_american"],
            goalie_id="P_BOBROVSKY_SERGEI", sportsbook="draftkings")
        self.assertTrue(verified)
        self.assertEqual(market.american_price, 100)

    def test_unverified_sportsbook_is_not_verified(self):
        payload = self._load_fixture()
        pair = self._pair_for(payload, "Jakub Dobes")
        over_q, under_q = pair["over"], pair["under"]
        threshold = sog_pricing.threshold_from_point(over_q["point"])
        _market, verified = nma.quote_to_normalized_market(
            over_q, market_family="GOALIE_SAVES", canonical_market_id="GOALIE_SAVES_25PLUS",
            threshold=threshold, side="OVER", opposing_price=under_q["price_american"],
            goalie_id="P_DOBES_JAKUB", sportsbook="fanduel")
        self.assertFalse(verified)

    def test_bare_goalie_saves_family_is_now_verified_for_draftkings(self):
        self.assertTrue(pa.is_contract_verified("draftkings", "GOALIE_SAVES"))


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
