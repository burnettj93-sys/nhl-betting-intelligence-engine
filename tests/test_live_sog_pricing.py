"""
Tests for the Live DraftKings SOG Pricing slice:
research/live_sog_pricing/*.py. No real network calls are made by this
suite (client internals are exercised via mocked `requests.get`, per
standard practice) -- see PLAYER_SOG_LIVE_PRICING_REPORT.md for the
genuine live API smoke test this slice actually ran once
(research/run_live_sog_phase_a_smoke.py), separate from this offline
suite.
"""
import ast
import copy
import os
import unittest
from unittest import mock

from research.live_sog_pricing import (
    archive, client, event_mapping, market_parser, observation_ledger, player_mapping, pricing,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

STANDARD_EVENT_ODDS_FIXTURE = {
    "id": "fixture_event_1", "sport_key": "icehockey_nhl", "commence_time": "2026-10-15T23:10:00Z",
    "home_team": "Toronto Maple Leafs", "away_team": "Buffalo Sabres",
    "bookmakers": [{
        "key": "draftkings", "title": "DraftKings", "last_update": "2026-10-15T20:00:00Z",
        "markets": [{
            "key": "player_shots_on_goal", "last_update": "2026-10-15T20:00:00Z",
            "outcomes": [
                {"name": "Over", "description": "William Nylander", "price": -115, "point": 3.5},
                {"name": "Under", "description": "William Nylander", "price": -105, "point": 3.5},
            ],
        }],
    }],
}

ALTERNATE_OVER_UNDER_FIXTURE = {
    "key": "player_shots_on_goal_alternate", "last_update": "2026-10-15T20:00:00Z",
    "outcomes": [
        {"name": "Over", "description": "William Nylander", "price": -250, "point": 1.5},
        {"name": "Under", "description": "William Nylander", "price": 190, "point": 1.5},
        {"name": "Over", "description": "William Nylander", "price": 120, "point": 4.5},
    ],
}

ALTERNATE_MILESTONE_FIXTURE = {
    "key": "player_shots_on_goal_alternate", "last_update": "2026-10-15T20:00:00Z",
    "outcomes": [
        {"name": "2+", "description": "William Nylander", "price": -250},
        {"name": "3+", "description": "William Nylander", "price": -110},
        {"name": "4+", "description": "William Nylander", "price": 180},
    ],
}


# --------------------------------------------------------------------------
# 1/2. API key never hardcoded, never logged.
# --------------------------------------------------------------------------
class TestApiKeyHandling(unittest.TestCase):
    FILES = ["research/live_sog_pricing/client.py", "research/live_sog_pricing/archive.py",
             "research/live_sog_pricing/env_config.py", "research/live_sog_pricing/refresh.py",
             "research/run_live_sog_phase_a_smoke.py"]

    def test_no_hardcoded_api_key_literal(self):
        """AST-based, not a literal substring match (a literal match would
        require writing the real key's value into THIS test file, which
        is itself exactly the mistake this test exists to prevent -- a
        committed test file must never carry the real secret it's
        checking for). Flags any bare string constant that looks like a
        raw API key (32 lowercase hex chars, The Odds API's own key
        format) assigned or passed anywhere outside of documentation."""
        hex32 = __import__("re").compile(r"^[0-9a-f]{32}$")
        for rel in self.FILES:
            with open(os.path.join(REPO_ROOT, rel)) as f:
                tree = ast.parse(f.read(), filename=rel)
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    self.assertFalse(hex32.match(node.value),
                                      f"{rel} contains a string that looks like a raw API key literal")

    def test_key_is_only_ever_read_via_env_config(self):
        """The only place THE_ODDS_API_KEY is ever read from os.environ
        or a parsed .env file is env_config.py -- checked via AST (looks
        for an `os.environ` Attribute access), not a text search, so a
        docstring mentioning ".env" as documentation (as client.py's
        error message does) can never trip this."""
        for rel in self.FILES:
            if rel.endswith("env_config.py"):
                continue
            with open(os.path.join(REPO_ROOT, rel)) as f:
                tree = ast.parse(f.read(), filename=rel)
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == "environ":
                    self.fail(f"{rel} accesses os.environ directly outside env_config.py")

    def test_api_result_never_carries_the_key(self):
        result = client.ApiResult(ok=True, status_code=200, data={}, error=None,
                                   endpoint="/sports", retrieved_at_utc="2026-01-01T00:00:00Z")
        fields = vars(result)
        for value in fields.values():
            if isinstance(value, str):
                self.assertNotIn("apiKey", value)

    def test_client_never_string_formats_key_into_logged_endpoint(self):
        """ApiResult.endpoint stores only the path, never the query
        string (where the key actually lives) -- verified structurally:
        client._get's `endpoint=path` argument is the bare path variable,
        never an f-string built from `params`."""
        import inspect
        src = inspect.getsource(client._get)
        # the ApiResult(...) construction lines must pass `endpoint=path`,
        # never something built from `params` (which contains apiKey).
        for line in src.splitlines():
            if "endpoint=path" in line:
                self.assertNotIn("params", line)

    def test_missing_key_returns_data_unavailable_not_exception(self):
        with mock.patch.object(client, "get_the_odds_api_key", return_value=None):
            result = client._get("/sports", {})
        self.assertFalse(result.ok)
        self.assertIn("not configured", result.error)


# --------------------------------------------------------------------------
# 3/4. NHL events parsing, DraftKings bookmaker filtering.
# --------------------------------------------------------------------------
class TestClientRequests(unittest.TestCase):
    def test_get_nhl_events_hits_correct_path(self):
        fake_resp = mock.Mock(status_code=200, headers={})
        fake_resp.json.return_value = [{"id": "e1", "home_team": "A", "away_team": "B",
                                         "commence_time": "2026-10-01T00:00:00Z"}]
        with mock.patch.object(client, "get_the_odds_api_key", return_value="fake"), \
             mock.patch("requests.get", return_value=fake_resp) as mock_get:
            result = client.get_nhl_events()
        self.assertTrue(result.ok)
        called_url = mock_get.call_args[0][0]
        self.assertIn("/sports/icehockey_nhl/events", called_url)

    def test_get_event_odds_defaults_to_draftkings_bookmaker_filter(self):
        fake_resp = mock.Mock(status_code=200, headers={})
        fake_resp.json.return_value = {"id": "e1", "bookmakers": []}
        with mock.patch.object(client, "get_the_odds_api_key", return_value="fake"), \
             mock.patch("requests.get", return_value=fake_resp) as mock_get:
            client.get_event_odds("e1")
        params = mock_get.call_args[1]["params"]
        self.assertEqual(params["bookmakers"], "draftkings")
        self.assertIn("player_shots_on_goal", params["markets"])


# --------------------------------------------------------------------------
# 5/6. Standard + alternate market parsing (real documented contract
# shape; no live non-empty payload was available this slice -- see
# market_parser.py's module docstring and the report's Section G/H).
# --------------------------------------------------------------------------
class TestMarketParsing(unittest.TestCase):
    def test_standard_market_parses_both_sides(self):
        quotes = market_parser.parse_event_odds_response(STANDARD_EVENT_ODDS_FIXTURE)
        self.assertEqual(len(quotes), 2)
        sides = {q["side"] for q in quotes}
        self.assertEqual(sides, {"OVER", "UNDER"})
        over = next(q for q in quotes if q["side"] == "OVER")
        self.assertEqual(over["price_american"], -115)
        self.assertEqual(over["point"], 3.5)
        self.assertEqual(over["player_name_raw"], "William Nylander")

    def test_alternate_market_over_under_shape_parses(self):
        event = dict(STANDARD_EVENT_ODDS_FIXTURE)
        event["bookmakers"] = [{"key": "draftkings", "title": "DraftKings", "last_update": "x",
                                 "markets": [ALTERNATE_OVER_UNDER_FIXTURE]}]
        quotes = market_parser.parse_event_odds_response(event)
        self.assertEqual(len(quotes), 3)
        self.assertTrue(all(q["shape"] == "over_under" for q in quotes))

    def test_alternate_market_milestone_shape_parses(self):
        event = dict(STANDARD_EVENT_ODDS_FIXTURE)
        event["bookmakers"] = [{"key": "draftkings", "title": "DraftKings", "last_update": "x",
                                 "markets": [ALTERNATE_MILESTONE_FIXTURE]}]
        quotes = market_parser.parse_event_odds_response(event)
        self.assertEqual(len(quotes), 3)
        self.assertTrue(all(q["shape"] == "milestone" for q in quotes))
        thresholds = sorted(q["milestone_threshold"] for q in quotes)
        self.assertEqual(thresholds, [2, 3, 4])

    def test_unrecognized_outcome_shape_raises_rather_than_guesses(self):
        bad_market = {"key": "player_shots_on_goal_alternate", "last_update": "x",
                       "outcomes": [{"name": "Yes", "description": "William Nylander", "price": -110}]}
        with self.assertRaises(market_parser.UnrecognizedOutcomeShapeError):
            market_parser.parse_alternate_market("e1", "TOR", "BUF",
                                                   {"key": "draftkings"}, bad_market)

    def test_group_standard_two_sided_pairs_same_market_snapshot_only(self):
        quotes = market_parser.parse_event_odds_response(STANDARD_EVENT_ODDS_FIXTURE)
        pairs = market_parser.group_standard_two_sided(quotes)
        self.assertEqual(len(pairs), 1)
        (key, pair), = pairs.items()
        self.assertIsNotNone(pair["over"])
        self.assertIsNotNone(pair["under"])


# --------------------------------------------------------------------------
# 7/8. Event mapping: MATCHED and AMBIGUOUS/UNMATCHED rejection.
# --------------------------------------------------------------------------
class TestEventMapping(unittest.TestCase):
    def test_matched_event(self):
        event = {"id": "e1", "home_team": "Toronto Maple Leafs", "away_team": "Buffalo Sabres",
                  "commence_time": "2026-10-15T23:10:00Z"}
        schedule = [{"game_id": 1, "home_team": "TOR", "away_team": "BUF", "game_date": "2026-10-15"}]
        result = event_mapping.map_event_to_game(event, schedule)
        self.assertEqual(result["status"], "MATCHED")
        self.assertEqual(result["game_id"], 1)

    def test_ambiguous_event_two_candidates(self):
        event = {"id": "e1", "home_team": "Toronto Maple Leafs", "away_team": "Buffalo Sabres",
                  "commence_time": "2026-10-15T23:10:00Z"}
        schedule = [
            {"game_id": 1, "home_team": "TOR", "away_team": "BUF", "game_date": "2026-10-15"},
            {"game_id": 2, "home_team": "TOR", "away_team": "BUF", "game_date": "2026-10-15"},
        ]
        result = event_mapping.map_event_to_game(event, schedule)
        self.assertEqual(result["status"], "AMBIGUOUS")
        self.assertIsNone(result["game_id"])

    def test_unmatched_event_no_schedule_row(self):
        event = {"id": "e1", "home_team": "Toronto Maple Leafs", "away_team": "Buffalo Sabres",
                  "commence_time": "2026-10-15T23:10:00Z"}
        result = event_mapping.map_event_to_game(event, [])
        self.assertEqual(result["status"], "UNMATCHED")

    def test_unrecognized_team_name_is_unmatched_not_a_crash(self):
        event = {"id": "e1", "home_team": "Some Fictional Team", "away_team": "Buffalo Sabres",
                  "commence_time": "2026-10-15T23:10:00Z"}
        result = event_mapping.map_event_to_game(event, [])
        self.assertEqual(result["status"], "UNMATCHED")


# --------------------------------------------------------------------------
# 9/10. Player mapping: MATCHED and AMBIGUOUS rejection (never last-name-only).
# --------------------------------------------------------------------------
class TestPlayerMapping(unittest.TestCase):
    def _corpus(self):
        return [
            {"player_id": "1", "player_name": "William Nylander", "team": "TOR", "game_date": "2026-04-01"},
            {"player_id": "2", "player_name": "Sebastian Aho", "team": "CAR", "game_date": "2026-04-01"},
            {"player_id": "3", "player_name": "Sebastian Aho", "team": "NYI", "game_date": "2026-04-01"},
        ]

    def test_matched_unique_name(self):
        idx = player_mapping.build_player_index(self._corpus())
        result = player_mapping.map_player("William Nylander", "TOR", "BUF", idx)
        self.assertEqual(result["status"], "MATCHED")
        self.assertEqual(result["player_id"], "1")

    def test_duplicate_name_disambiguated_by_team(self):
        idx = player_mapping.build_player_index(self._corpus())
        result = player_mapping.map_player("Sebastian Aho", "CAR", "BOS", idx)
        self.assertEqual(result["status"], "MATCHED")
        self.assertEqual(result["player_id"], "2")

    def test_duplicate_name_ambiguous_when_team_does_not_disambiguate(self):
        idx = player_mapping.build_player_index(self._corpus())
        result = player_mapping.map_player("Sebastian Aho", "CAR", "NYI", idx)
        self.assertEqual(result["status"], "AMBIGUOUS")
        self.assertIsNone(result["player_id"])

    def test_unknown_name_unmatched(self):
        idx = player_mapping.build_player_index(self._corpus())
        result = player_mapping.map_player("Nobody Real", "TOR", "BUF", idx)
        self.assertEqual(result["status"], "UNMATCHED")

    def test_never_matches_on_last_name_alone(self):
        idx = player_mapping.build_player_index(self._corpus())
        result = player_mapping.map_player("Aho", "TOR", "BUF", idx)  # last name only, wrong team too
        self.assertEqual(result["status"], "UNMATCHED")

    def test_normalize_name_strips_accents_and_suffixes(self):
        self.assertEqual(player_mapping.normalize_name("Tim Stützle"), "tim stutzle")
        self.assertEqual(player_mapping.normalize_name("John Smith Jr."), "john smith")


# --------------------------------------------------------------------------
# 11/12/13. Threshold mapping to the validated SOG distribution.
# --------------------------------------------------------------------------
class TestThresholdMapping(unittest.TestCase):
    def test_over_threshold_from_point(self):
        self.assertEqual(pricing.threshold_from_point(3.5), 4)
        self.assertEqual(pricing.threshold_from_point(1.5), 2)

    def test_over_maps_to_p_at_least(self):
        probs = {4: 0.35}
        self.assertEqual(pricing.model_prob_for_side("OVER", 4, probs), 0.35)

    def test_under_maps_to_complement(self):
        probs = {4: 0.35}
        self.assertAlmostEqual(pricing.model_prob_for_side("UNDER", 4, probs), 0.65)

    def test_milestone_uses_threshold_directly(self):
        probs = {3: 0.5}
        self.assertEqual(pricing.model_prob_for_side("OVER_MILESTONE", 3, probs), 0.5)


# --------------------------------------------------------------------------
# 14-22. American odds conversion, no-vig, fair price, edge, EV (all via
# pricing.price_observation, which reuses pricing/odds_math.py's real,
# unmodified production functions).
# --------------------------------------------------------------------------
class TestExtremeProbabilityDoesNotCrash(unittest.TestCase):
    """BUG-204 regression (preseason product audit): an exact 0.0 or 1.0
    model probability -- real and reproducible for an extreme enough
    threshold/mu combination -- must never raise out of price_observation()
    and crash an entire live refresh batch."""

    def test_exact_zero_probability_prices_cleanly(self):
        report = pricing.price_observation(
            side="OVER", point=5.5, milestone_threshold=None, price_american=+2000,
            opposing_price_american=-5000, probs={6: 0.0}, conservative_probs={6: 0.0},
            confidence="HIGH", lineup_status="PROJECTED/UNCONFIRMED",
            quote_age_minutes=5.0, hours_to_puck_drop=72.0)
        self.assertEqual(report["status"], "PRICED")
        self.assertIsInstance(report["model_fair_price"], float)

    def test_exact_one_probability_prices_cleanly(self):
        report = pricing.price_observation(
            side="OVER", point=0.5, milestone_threshold=None, price_american=-5000,
            opposing_price_american=+2000, probs={1: 1.0}, conservative_probs={1: 1.0},
            confidence="HIGH", lineup_status="PROJECTED/UNCONFIRMED",
            quote_age_minutes=5.0, hours_to_puck_drop=72.0)
        self.assertEqual(report["status"], "PRICED")
        self.assertIsInstance(report["model_fair_price"], float)

    def test_clip_helper_stays_strictly_inside_open_interval(self):
        self.assertGreater(pricing._clip_to_priceable_range(0.0), 0.0)
        self.assertLess(pricing._clip_to_priceable_range(1.0), 1.0)
        self.assertEqual(pricing._clip_to_priceable_range(0.5), 0.5)


class TestPricingMath(unittest.TestCase):
    def setUp(self):
        self.probs = {4: 0.40}
        self.cprobs = {4: 0.32}

    def test_two_way_no_vig_matches_odds_math_directly(self):
        from pricing import odds_math
        report = pricing.price_observation(
            side="OVER", point=3.5, milestone_threshold=None, price_american=-115,
            opposing_price_american=-105, probs=self.probs, conservative_probs=self.cprobs,
            confidence="HIGH", lineup_status="PROJECTED/UNCONFIRMED",
            quote_age_minutes=5.0, hours_to_puck_drop=72.0)
        expected_no_vig, _ = odds_math.no_vig_two_way(-115, -105)
        self.assertAlmostEqual(report["market_no_vig_probability"], expected_no_vig, places=9)

    def test_one_sided_market_has_no_vig_unavailable(self):
        report = pricing.price_observation(
            side="OVER_MILESTONE", point=None, milestone_threshold=4, price_american=+220,
            opposing_price_american=None, probs=self.probs, conservative_probs=self.cprobs,
            confidence="MEDIUM", lineup_status="PROJECTED/UNCONFIRMED",
            quote_age_minutes=5.0, hours_to_puck_drop=72.0)
        self.assertFalse(report["no_vig_available"])
        self.assertIsNone(report["market_no_vig_probability"])
        self.assertIsNone(report["maximum_acceptable_price"])

    def test_fair_price_matches_odds_math(self):
        from pricing import odds_math
        report = pricing.price_observation(
            side="OVER", point=3.5, milestone_threshold=None, price_american=-115,
            opposing_price_american=-105, probs=self.probs, conservative_probs=self.cprobs,
            confidence="HIGH", lineup_status="PROJECTED/UNCONFIRMED",
            quote_age_minutes=5.0, hours_to_puck_drop=72.0)
        self.assertAlmostEqual(report["model_fair_price"], odds_math.prob_to_american(0.40), places=6)
        self.assertAlmostEqual(report["conservative_fair_price"], odds_math.prob_to_american(0.32), places=6)

    def test_raw_and_conservative_edge_formula(self):
        report = pricing.price_observation(
            side="OVER", point=3.5, milestone_threshold=None, price_american=-115,
            opposing_price_american=-105, probs=self.probs, conservative_probs=self.cprobs,
            confidence="HIGH", lineup_status="PROJECTED/UNCONFIRMED",
            quote_age_minutes=5.0, hours_to_puck_drop=72.0)
        self.assertAlmostEqual(report["raw_edge"], 0.40 - report["market_no_vig_probability"], places=9)
        self.assertAlmostEqual(report["conservative_edge"], 0.32 - report["market_no_vig_probability"], places=9)

    def test_raw_and_conservative_ev_use_actual_offered_price(self):
        from pricing import odds_math
        report = pricing.price_observation(
            side="OVER", point=3.5, milestone_threshold=None, price_american=-115,
            opposing_price_american=-105, probs=self.probs, conservative_probs=self.cprobs,
            confidence="HIGH", lineup_status="PROJECTED/UNCONFIRMED",
            quote_age_minutes=5.0, hours_to_puck_drop=72.0)
        self.assertAlmostEqual(report["raw_ev"], odds_math.expected_value(0.40, -115), places=9)
        self.assertAlmostEqual(report["conservative_ev"], odds_math.expected_value(0.32, -115), places=9)

    def test_edge_is_probability_points_not_ev_percent(self):
        """Regression guard against the exact confusion the prompt warns
        about: "do not describe this as 7.2% ROI" -- edge and EV must be
        independently computable and not silently equal."""
        report = pricing.price_observation(
            side="OVER", point=3.5, milestone_threshold=None, price_american=+300,
            opposing_price_american=-250, probs={4: 0.35}, conservative_probs={4: 0.30},
            confidence="HIGH", lineup_status="PROJECTED/UNCONFIRMED",
            quote_age_minutes=5.0, hours_to_puck_drop=72.0)
        self.assertNotAlmostEqual(report["conservative_edge"], report["conservative_ev"], places=3)


# --------------------------------------------------------------------------
# 23. Maximum acceptable price (reuses pricing/odds_math.py unchanged).
# --------------------------------------------------------------------------
class TestMaxAcceptablePrice(unittest.TestCase):
    def test_max_price_none_when_edge_requirement_exceeds_probability(self):
        from pricing import odds_math
        result = odds_math.max_acceptable_price(0.05, 0.10, -150)
        self.assertIsNone(result)

    def test_max_price_computed_for_a_normal_case(self):
        report = pricing.price_observation(
            side="OVER", point=3.5, milestone_threshold=None, price_american=+150,
            opposing_price_american=-180, probs={4: 0.45}, conservative_probs={4: 0.40},
            confidence="HIGH", lineup_status="PROJECTED/UNCONFIRMED",
            quote_age_minutes=5.0, hours_to_puck_drop=72.0)
        self.assertIsNotNone(report["maximum_acceptable_price"])


# --------------------------------------------------------------------------
# 24. Stale-quote behavior (dynamic staleness policy, reused from
# pricing/odds_math.py / config.ODDS_STALENESS_TIERS unchanged).
# --------------------------------------------------------------------------
class TestStaleness(unittest.TestCase):
    def test_quote_older_than_policy_window_is_data_unavailable(self):
        report = pricing.price_observation(
            side="OVER", point=3.5, milestone_threshold=None, price_american=-115,
            opposing_price_american=-105, probs={4: 0.4}, conservative_probs={4: 0.32},
            confidence="HIGH", lineup_status="PROJECTED/UNCONFIRMED",
            quote_age_minutes=500.0, hours_to_puck_drop=72.0)
        self.assertEqual(report["status"], "DATA_UNAVAILABLE")

    def test_fresh_quote_within_window_is_priced(self):
        report = pricing.price_observation(
            side="OVER", point=3.5, milestone_threshold=None, price_american=-115,
            opposing_price_american=-105, probs={4: 0.4}, conservative_probs={4: 0.32},
            confidence="HIGH", lineup_status="PROJECTED/UNCONFIRMED",
            quote_age_minutes=5.0, hours_to_puck_drop=72.0)
        self.assertEqual(report["status"], "PRICED")

    def test_staleness_window_tightens_closer_to_puck_drop(self):
        from pricing import odds_math
        far = odds_math.dynamic_max_staleness_minutes(24.0)
        near = odds_math.dynamic_max_staleness_minutes(0.1)
        self.assertGreater(far, near)


# --------------------------------------------------------------------------
# 25. Missing opposing side behavior.
# --------------------------------------------------------------------------
class TestMissingOpposingSide(unittest.TestCase):
    def test_group_standard_two_sided_leaves_under_none_if_absent(self):
        # Root cause fix (test-order-dependency investigation, 2026-08-31,
        # LIVE_DK_PAPER_BANKROLL_COMPLETION_REPORT.md Section F): the old
        # `dict(STANDARD_EVENT_ODDS_FIXTURE)` was only a SHALLOW copy, so
        # `event["bookmakers"][0]` stayed the SAME dict object as the
        # module-level fixture's -- the old
        # `event["bookmakers"][0]["markets"] = [market]` line therefore
        # mutated the shared fixture's nested "markets" list in place,
        # permanently shrinking its outcomes from 2 to 1 for every test
        # that ran later in the same process (reproduced: passes alone,
        # corrupts STANDARD_EVENT_ODDS_FIXTURE once it runs before other
        # tests reading that fixture). A full deep copy makes every
        # nested list/dict independent, so nothing here can reach the
        # shared original -- confirmed by TestFixtureImmutability below.
        event = copy.deepcopy(STANDARD_EVENT_ODDS_FIXTURE)
        market = event["bookmakers"][0]["markets"][0]
        market["outcomes"] = [market["outcomes"][0]]  # Over only
        event["bookmakers"][0]["markets"] = [market]
        quotes = market_parser.parse_event_odds_response(event)
        pairs = market_parser.group_standard_two_sided(quotes)
        (key, pair), = pairs.items()
        self.assertIsNotNone(pair["over"])
        self.assertIsNone(pair["under"])


class TestFixtureImmutability(unittest.TestCase):
    """Regression guard for the exact test-order-dependency bug fixed
    above: proves market_parser.py's real functions treat their input as
    read-only (never mutate it), AND that this test file's own fixture
    manipulation (deepcopy, not shallow copy) can never corrupt the
    shared module-level STANDARD_EVENT_ODDS_FIXTURE for a later test."""

    def test_parse_event_odds_response_never_mutates_its_input(self):
        before = copy.deepcopy(STANDARD_EVENT_ODDS_FIXTURE)
        market_parser.parse_event_odds_response(STANDARD_EVENT_ODDS_FIXTURE)
        self.assertEqual(STANDARD_EVENT_ODDS_FIXTURE, before,
                          "parse_event_odds_response mutated its input -- it must be read-only")

    def test_group_standard_two_sided_never_mutates_its_input(self):
        quotes = market_parser.parse_event_odds_response(STANDARD_EVENT_ODDS_FIXTURE)
        before = copy.deepcopy(quotes)
        market_parser.group_standard_two_sided(quotes)
        self.assertEqual(quotes, before, "group_standard_two_sided mutated its input quotes")

    def test_fixture_survives_the_missing_opposing_side_test_pattern(self):
        # Directly re-exercises TestMissingOpposingSide's own mutation
        # pattern and proves the shared fixture is untouched afterward --
        # this is the exact scenario that used to corrupt
        # STANDARD_EVENT_ODDS_FIXTURE for every later test in the process.
        before = copy.deepcopy(STANDARD_EVENT_ODDS_FIXTURE)
        event = copy.deepcopy(STANDARD_EVENT_ODDS_FIXTURE)
        market = event["bookmakers"][0]["markets"][0]
        market["outcomes"] = [market["outcomes"][0]]
        event["bookmakers"][0]["markets"] = [market]
        market_parser.parse_event_odds_response(event)
        self.assertEqual(STANDARD_EVENT_ODDS_FIXTURE, before,
                          "the shared module-level fixture must never be mutated by a test "
                          "that only intended to modify its own local deep copy")
        self.assertEqual(len(STANDARD_EVENT_ODDS_FIXTURE["bookmakers"][0]["markets"][0]["outcomes"]), 2,
                          "the real fixture must still have both Over and Under outcomes")


# --------------------------------------------------------------------------
# 26. Lineup status is always PROJECTED/UNCONFIRMED, never CONFIRMED.
# --------------------------------------------------------------------------
class TestLineupStatusHonesty(unittest.TestCase):
    def test_refresh_never_produces_a_confirmed_lineup_status(self):
        with open(os.path.join(REPO_ROOT, "research/live_sog_pricing/refresh.py")) as f:
            tree = ast.parse(f.read())
        docstring_ids = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                body = node.body
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                        and isinstance(body[0].value.value, str):
                    docstring_ids.add(id(body[0].value))
        strings = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
                   and isinstance(n.value, str) and id(n) not in docstring_ids]
        self.assertFalse(any(s.strip() == "CONFIRMED" or s.strip() == "CONFIRMED ACTIVE" for s in strings))


# --------------------------------------------------------------------------
# 27. Confidence as a decision-quality gate.
# --------------------------------------------------------------------------
class TestConfidenceGating(unittest.TestCase):
    def _clear_edge_kwargs(self):
        return dict(side="OVER", point=3.5, milestone_threshold=None, price_american=+300,
                    opposing_price_american=-250, probs={4: 0.35}, conservative_probs={4: 0.30},
                    lineup_status="PROJECTED/UNCONFIRMED", quote_age_minutes=5.0, hours_to_puck_drop=72.0)

    def test_high_confidence_clear_edge_bets(self):
        report = pricing.price_observation(confidence="HIGH", **self._clear_edge_kwargs())
        self.assertEqual(report["action"], "BET")

    def test_low_confidence_same_numbers_downgrades_to_wait(self):
        report = pricing.price_observation(confidence="LOW", **self._clear_edge_kwargs())
        self.assertEqual(report["action"], "WAIT")

    def test_low_confidence_never_produces_bet(self):
        for edge_case_probs in ({4: 0.9}, {4: 0.6}, {4: 0.35}):
            report = pricing.price_observation(
                side="OVER", point=3.5, milestone_threshold=None, price_american=+300,
                opposing_price_american=-250, probs=edge_case_probs, conservative_probs=edge_case_probs,
                confidence="LOW", lineup_status="PROJECTED/UNCONFIRMED",
                quote_age_minutes=5.0, hours_to_puck_drop=72.0)
            self.assertNotEqual(report["action"], "BET")

    def test_no_edge_low_confidence_stays_pass_not_wait(self):
        report = pricing.price_observation(
            side="OVER", point=3.5, milestone_threshold=None, price_american=-500,
            opposing_price_american=+400, probs={4: 0.05}, conservative_probs={4: 0.03},
            confidence="LOW", lineup_status="PROJECTED/UNCONFIRMED",
            quote_age_minutes=5.0, hours_to_puck_drop=72.0)
        self.assertEqual(report["action"], "PASS")


# --------------------------------------------------------------------------
# 28/39. API failure handling (network error, non-200, malformed JSON,
# rate-limited) -- never raises past the caller.
# --------------------------------------------------------------------------
class TestApiFailureHandling(unittest.TestCase):
    def test_network_error_returns_ok_false(self):
        import requests
        with mock.patch.object(client, "get_the_odds_api_key", return_value="fake"), \
             mock.patch("requests.get", side_effect=requests.exceptions.ConnectionError("boom")):
            result = client._get("/sports", {})
        self.assertFalse(result.ok)
        self.assertIn("network error", result.error)

    def test_unauthorized_status_handled(self):
        fake_resp = mock.Mock(status_code=401, headers={})
        with mock.patch.object(client, "get_the_odds_api_key", return_value="fake"), \
             mock.patch("requests.get", return_value=fake_resp):
            result = client._get("/sports", {})
        self.assertFalse(result.ok)
        self.assertIn("unauthorized", result.error)

    def test_rate_limited_status_handled(self):
        fake_resp = mock.Mock(status_code=429, headers={})
        with mock.patch.object(client, "get_the_odds_api_key", return_value="fake"), \
             mock.patch("requests.get", return_value=fake_resp):
            result = client._get("/sports", {})
        self.assertFalse(result.ok)
        self.assertIn("rate limited", result.error)

    def test_malformed_json_handled(self):
        fake_resp = mock.Mock(status_code=200, headers={})
        fake_resp.json.side_effect = ValueError("bad json")
        with mock.patch.object(client, "get_the_odds_api_key", return_value="fake"), \
             mock.patch("requests.get", return_value=fake_resp):
            result = client._get("/sports", {})
        self.assertFalse(result.ok)
        self.assertIn("malformed JSON", result.error)


# --------------------------------------------------------------------------
# 29/30. Raw payload preservation, no fabricated timestamps.
# --------------------------------------------------------------------------
class TestArchive(unittest.TestCase):
    def test_archived_response_is_byte_identical(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            result = client.ApiResult(ok=True, status_code=200, data={"a": 1, "b": [1, 2, 3]}, error=None,
                                       endpoint="/sports", retrieved_at_utc="2026-01-01T00:00:00Z",
                                       requests_used="1", requests_remaining="99")
            path = archive.archive_result(result, event_id=None, market_filter=None,
                                           bookmaker_filter=None, out_dir=__import__("pathlib").Path(tmp))
            saved = archive.load_archived(path)
            self.assertEqual(saved["response"], {"a": 1, "b": [1, 2, 3]})

    def test_archive_never_stores_the_api_key(self):
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as tmp:
            result = client.ApiResult(ok=True, status_code=200, data={"x": 1}, error=None,
                                       endpoint="/sports", retrieved_at_utc="2026-01-01T00:00:00Z")
            path = archive.archive_result(result, event_id=None, market_filter=None,
                                           bookmaker_filter=None, out_dir=pathlib.Path(tmp))
            with open(path) as f:
                text = f.read()
            self.assertNotIn("apiKey", text)

    def test_retrieved_at_utc_comes_from_the_real_call_not_hardcoded(self):
        import time
        r1 = client.ApiResult(ok=True, status_code=200, data={}, error=None, endpoint="/sports",
                               retrieved_at_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        self.assertRegex(r1.retrieved_at_utc, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


# --------------------------------------------------------------------------
# 31/32/40. Observation ledger: append-only, idempotent, provenance.
# --------------------------------------------------------------------------
class TestObservationLedger(unittest.TestCase):
    def _obs(self, obs_id="abc123"):
        return {f: (obs_id if f == "observation_id" else "x") for f in observation_ledger.REQUIRED_FIELDS}

    def test_append_adds_a_row(self):
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "ledger.jsonl"
            appended = observation_ledger.append_observation(self._obs("id1"), path=path)
            self.assertTrue(appended)
            self.assertEqual(len(observation_ledger.load_all_observations(path)), 1)

    def test_duplicate_observation_id_is_not_appended_twice(self):
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "ledger.jsonl"
            observation_ledger.append_observation(self._obs("id1"), path=path)
            appended_again = observation_ledger.append_observation(self._obs("id1"), path=path)
            self.assertFalse(appended_again)
            self.assertEqual(len(observation_ledger.load_all_observations(path)), 1)

    def test_missing_required_field_raises(self):
        bad = {k: "x" for k in observation_ledger.REQUIRED_FIELDS[:-1]}  # drop one field
        with self.assertRaises(ValueError):
            observation_ledger.append_observation(bad, path=__import__("pathlib").Path("/tmp/never_written.jsonl"))

    def test_deterministic_observation_id(self):
        id1 = observation_ledger.make_observation_id("2026-01-01T00:00:00Z", "ev1", "p1", "market", "OVER", 3.5)
        id2 = observation_ledger.make_observation_id("2026-01-01T00:00:00Z", "ev1", "p1", "market", "OVER", 3.5)
        self.assertEqual(id1, id2)

    def test_ledger_never_labeled_as_a_bet_ledger(self):
        with open(os.path.join(REPO_ROOT, "research/live_sog_pricing/observation_ledger.py")) as f:
            text = f.read()
        self.assertIn("NOT a bet ledger", text)


# --------------------------------------------------------------------------
# 33/34. Dashboard never makes a network call; refresh is a separate,
# explicitly-invoked action.
# --------------------------------------------------------------------------
class TestDashboardNoNetworkOnRerun(unittest.TestCase):
    DASHBOARD_FILES = ["dashboard/live_sog_pricing_view.py", "dashboard/pages/8_Live_SOG_Markets.py"]

    def test_dashboard_files_never_import_the_client_or_refresh_module(self):
        for rel in self.DASHBOARD_FILES:
            with open(os.path.join(REPO_ROOT, rel)) as f:
                tree = ast.parse(f.read(), filename=rel)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn("live_sog_pricing.client", node.module, f"{rel} imports the network client")
                    self.assertNotIn("live_sog_pricing.refresh", node.module, f"{rel} imports the refresh action")

    def test_dashboard_files_never_call_requests_directly(self):
        for rel in self.DASHBOARD_FILES:
            with open(os.path.join(REPO_ROOT, rel)) as f:
                text = f.read()
            self.assertNotIn("requests.get(", text)
            self.assertNotIn("requests.post(", text)

    def test_refresh_module_is_never_imported_by_any_dashboard_file(self):
        import glob
        for path in glob.glob(os.path.join(REPO_ROOT, "dashboard", "**", "*.py"), recursive=True):
            with open(path) as f:
                text = f.read()
            self.assertNotIn("import refresh", text.replace("from research.live_sog_pricing import refresh", ""))


# --------------------------------------------------------------------------
# 35/36. No sportsbook odds used in SOG model fitting; production
# NHL win-probability model unchanged; no forbidden imports/nhl.db use.
# --------------------------------------------------------------------------
class TestProductionModelUnchanged(unittest.TestCase):
    NEW_FILES = [
        "research/live_sog_pricing/client.py", "research/live_sog_pricing/archive.py",
        "research/live_sog_pricing/env_config.py", "research/live_sog_pricing/event_mapping.py",
        "research/live_sog_pricing/market_parser.py", "research/live_sog_pricing/player_mapping.py",
        "research/live_sog_pricing/pricing.py", "research/live_sog_pricing/observation_ledger.py",
        "research/live_sog_pricing/refresh.py", "research/run_live_sog_phase_a_smoke.py",
        "research/player_sog/live_projection.py", "dashboard/live_sog_pricing_view.py",
        "dashboard/pages/8_Live_SOG_Markets.py",
    ]
    FORBIDDEN_MODULES = {"pricing.engine", "pricing.decision", "models.combined_model"}

    def test_no_forbidden_imports(self):
        for rel in self.NEW_FILES:
            with open(os.path.join(REPO_ROOT, rel)) as f:
                tree = ast.parse(f.read(), filename=rel)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn(node.module, self.FORBIDDEN_MODULES, f"{rel} imports {node.module}")

    def test_no_nhl_db_path_used_in_any_call(self):
        for rel in self.NEW_FILES:
            with open(os.path.join(REPO_ROOT, rel)) as f:
                tree = ast.parse(f.read(), filename=rel)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    for arg in node.args:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            self.assertNotIn("nhl.db", arg.value, f"{rel} passes nhl.db to a call")

    def test_sog_model_fitting_never_reads_odds_or_price_terms(self):
        for rel in ("research/player_sog/count_models.py", "research/run_player_sog_model.py"):
            with open(os.path.join(REPO_ROOT, rel)) as f:
                text = f.read().lower()
            for token in ("draftkings", "the_odds_api", "sportsbook_price", "market_price", "no_vig"):
                self.assertNotIn(token, text, f"{rel} references {token}")


# --------------------------------------------------------------------------
# 37/38. No auto-betting, no DraftKings credentials/account automation.
# --------------------------------------------------------------------------
class TestNoAutoBettingOrCredentials(unittest.TestCase):
    FORBIDDEN_TOKENS = ("place_bet", "placebet", "submit_order", "login(", "password", "credential",
                         "session_token", "checkout", "add_to_cart", "confirm_wager")

    def test_no_auto_betting_or_credential_terms_anywhere_in_the_slice(self):
        import glob
        paths = (glob.glob(os.path.join(REPO_ROOT, "research", "live_sog_pricing", "*.py"))
                  + glob.glob(os.path.join(REPO_ROOT, "dashboard", "*sog_pricing*.py"))
                  + glob.glob(os.path.join(REPO_ROOT, "dashboard", "pages", "8_*.py")))
        for path in paths:
            with open(path) as f:
                text = f.read().lower()
            for token in self.FORBIDDEN_TOKENS:
                self.assertNotIn(token, text, f"{path} references forbidden token {token!r}")


if __name__ == "__main__":
    unittest.main()
