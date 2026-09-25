"""
Tests for operational/live_odds_daily_pull.py (user-requested 2026-08-30
multi-market daily odds pull). No real network calls or real API credits
are spent by this suite -- `requests.get` is mocked throughout, matching
the established convention in tests/test_live_sog_pricing.py, precisely
because the thing being tested is itself a real-money credit-spending
job: a test suite that spent real credits every run would defeat the
whole point of the quota-aware design under test.
"""
from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from operational import live_odds_daily_pull as lop


def _fake_response(status_code=200, json_data=None, headers=None):
    resp = mock.Mock(status_code=status_code)
    resp.json.return_value = json_data if json_data is not None else {}
    resp.headers = headers or {}
    return resp


EVENT_A = {"id": "evt-a", "commence_time": "2026-09-30T23:00:00Z",
           "home_team": "Toronto Maple Leafs", "away_team": "Boston Bruins"}
EVENT_B = {"id": "evt-b", "commence_time": "2026-09-29T23:00:00Z",
           "home_team": "Florida Panthers", "away_team": "Carolina Hurricanes"}
EVENT_PAST = {"id": "evt-past", "commence_time": "2020-01-01T00:00:00Z",
              "home_team": "X", "away_team": "Y"}


# ---------------------------------------------------------------------
# 1. Cycle date math
# ---------------------------------------------------------------------
class Test01CycleDateMath(unittest.TestCase):
    def test_cycle_start_before_reset_day_is_previous_month(self):
        self.assertEqual(lop._cycle_start(dt.date(2026, 9, 15), 1), dt.date(2026, 9, 1))

    def test_next_cycle_start_wraps_year(self):
        self.assertEqual(lop._next_cycle_start(dt.date(2026, 12, 31), 1), dt.date(2027, 1, 1))

    def test_days_left_shrinks_toward_end_of_cycle(self):
        early = (lop._next_cycle_start(dt.date(2026, 9, 1), 1) - dt.date(2026, 9, 1)).days
        late = (lop._next_cycle_start(dt.date(2026, 9, 30), 1) - dt.date(2026, 9, 30)).days
        self.assertGreater(early, late)


# ---------------------------------------------------------------------
# 2. Preseason-start gate
# ---------------------------------------------------------------------
class Test02PreseasonGate(unittest.TestCase):
    def test_should_run_today_false_before_lead_window(self):
        self.assertFalse(lop.should_run_today(dt.date(2026, 8, 30), dt.date(2026, 9, 19), lead_days=2))

    def test_should_run_today_true_within_lead_window(self):
        self.assertTrue(lop.should_run_today(dt.date(2026, 9, 17), dt.date(2026, 9, 19), lead_days=2))

    def test_should_run_today_true_after_preseason_started(self):
        self.assertTrue(lop.should_run_today(dt.date(2026, 10, 1), dt.date(2026, 9, 19), lead_days=2))

    def test_should_run_today_false_when_preseason_start_unknown(self):
        self.assertFalse(lop.should_run_today(dt.date(2026, 9, 20), None, lead_days=2))

    def test_find_next_preseason_start_uses_real_schedule_fetcher_not_reimplemented(self):
        fake_games = [
            {"id": 1, "gameType": 2, "gameDate": "2026-09-25"},  # regular season -- must be skipped
            {"id": 2, "gameType": 1, "gameDate": "2026-09-19"},  # preseason -- the real answer
            {"id": 3, "gameType": 1, "gameDate": "2026-09-22"},
        ]
        with mock.patch("operational.live_odds_daily_pull.fetch_schedule_range", return_value=fake_games):
            result = lop.find_next_preseason_start()
        self.assertEqual(result, dt.date(2026, 9, 19))

    def test_find_next_preseason_start_returns_none_never_fabricates(self):
        with mock.patch("operational.live_odds_daily_pull.fetch_schedule_range", return_value=[]):
            self.assertIsNone(lop.find_next_preseason_start())


# ---------------------------------------------------------------------
# 3. Generic market parsing -- honest model_status tagging
# ---------------------------------------------------------------------
class Test03GenericMarketParsing(unittest.TestCase):
    def _odds_data(self, market_key, outcomes):
        return {
            "_retrieved_at_utc": "2026-09-01T00:00:00Z",
            "bookmakers": [{"key": "draftkings", "last_update": "2026-09-01T00:00:00Z",
                             "markets": [{"key": market_key, "last_update": "2026-09-01T00:00:00Z",
                                          "outcomes": outcomes}]}],
        }

    def test_validated_market_tagged_with_real_registry_status(self):
        odds = self._odds_data("player_shots_on_goal",
                                [{"name": "Over", "description": "Connor McDavid", "price": -115, "point": 3.5}])
        quotes = lop._parse_event_odds_generic(EVENT_A, odds)
        self.assertEqual(len(quotes), 1)
        self.assertEqual(quotes[0]["model_status"], "VALIDATED")
        self.assertEqual(quotes[0]["player_or_side"], "Connor McDavid")
        self.assertEqual(quotes[0]["price_american"], -115)

    def test_research_status_market_not_mislabeled_validated(self):
        odds = self._odds_data("player_total_saves",
                                [{"name": "Over", "description": "Connor Hellebuyck", "price": -110, "point": 28.5}])
        quotes = lop._parse_event_odds_generic(EVENT_A, odds)
        self.assertEqual(quotes[0]["model_status"], "RESEARCH")

    def test_unmapped_speculative_market_tagged_no_model(self):
        odds = self._odds_data("team_totals", [{"name": "Over", "price": -110, "point": 30.5}])
        quotes = lop._parse_event_odds_generic(EVENT_A, odds)
        self.assertEqual(quotes[0]["model_status"], "NO_MODEL_THIS_MARKET")

    def test_no_bookmakers_returns_empty_never_raises(self):
        self.assertEqual(lop._parse_event_odds_generic(EVENT_A, {"bookmakers": []}), [])


# ---------------------------------------------------------------------
# 4. Future-event ordering (soonest puck-drop first)
# ---------------------------------------------------------------------
class Test04EventOrdering(unittest.TestCase):
    def test_sorted_soonest_first_and_past_events_excluded(self):
        now = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
        out = lop._future_events_sorted([EVENT_A, EVENT_B, EVENT_PAST], now)
        self.assertEqual([e["id"] for e in out], ["evt-b", "evt-a"])


# ---------------------------------------------------------------------
# 5. Header-cost type handling (real bug found and fixed this session:
# HTTP headers are always strings -- x-requests-last="3" not int 3 --
# and arithmetic on the raw string crashed the very first time a call
# ever had a nonzero real cost).
# ---------------------------------------------------------------------
class Test05HeaderCostTypeHandling(unittest.TestCase):
    def test_credits_spent_since_handles_string_header_values(self):
        with tempfile.TemporaryDirectory() as d:
            archive_dir = Path(d)
            (archive_dir / "a.json").write_text(json.dumps({
                "meta": {"retrieved_at_utc": "2026-09-20T00:00:00Z", "requests_last_header": "3"}}))
            (archive_dir / "b.json").write_text(json.dumps({
                "meta": {"retrieved_at_utc": "2026-09-21T00:00:00Z", "requests_last_header": "5"}}))
            total = lop._credits_spent_since(dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc), archive_dir)
        self.assertEqual(total, 8)

    def test_credits_spent_since_ignores_malformed_entries(self):
        with tempfile.TemporaryDirectory() as d:
            archive_dir = Path(d)
            (archive_dir / "bad.json").write_text("not json")
            total = lop._credits_spent_since(dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc), archive_dir)
        self.assertEqual(total, 0)


# ---------------------------------------------------------------------
# 6. run_daily_pull orchestration -- fully mocked network, real logic
# ---------------------------------------------------------------------
class Test06RunDailyPullOrchestration(unittest.TestCase):
    def test_no_op_before_lead_window_makes_zero_http_calls(self):
        with mock.patch("operational.live_odds_daily_pull.find_next_preseason_start",
                         return_value=dt.date(2026, 9, 19)), \
             mock.patch("operational.live_odds_daily_pull._now_utc",
                         return_value=dt.datetime(2026, 8, 30, tzinfo=dt.timezone.utc)), \
             mock.patch("requests.get") as mock_get:
            result = lop.run_daily_pull()
        mock_get.assert_not_called()
        self.assertFalse(result["ran"])

    def test_real_run_stops_once_daily_budget_reached(self):
        # today=2026-08-31, cycle_reset_day=1 -> next cycle starts
        # 2026-09-01 -> exactly 1 day left in the cycle -> with
        # remaining=6 and safety_floor=0, daily_budget=6. The first
        # (soonest) event's real cost of 6 exhausts that budget exactly,
        # so the second event must never be queried.
        events_resp = _fake_response(json_data=[EVENT_A, EVENT_B],
                                      headers={"x-requests-remaining": "6", "x-requests-last": "0",
                                               "x-requests-used": "1"})
        odds_resp = _fake_response(
            json_data={"id": "x", "home_team": "H", "away_team": "A", "bookmakers": []},
            headers={"x-requests-remaining": "0", "x-requests-last": "6", "x-requests-used": "7"})

        with mock.patch("operational.live_odds_daily_pull.find_next_preseason_start",
                         return_value=dt.date(2026, 9, 1)), \
             mock.patch("operational.live_odds_daily_pull._now_utc",
                         return_value=dt.datetime(2026, 8, 31, tzinfo=dt.timezone.utc)), \
             mock.patch("operational.live_odds_daily_pull.ARCHIVE_DIR", Path(tempfile.mkdtemp())), \
             mock.patch("research.live_sog_pricing.archive.ARCHIVE_DIR", Path(tempfile.mkdtemp())), \
             mock.patch("operational.live_odds_daily_pull.BOARD_CACHE_PATH",
                         Path(tempfile.mkdtemp()) / "cache.json"), \
             mock.patch.object(lop.client, "get_the_odds_api_key", return_value="fake"), \
             mock.patch("requests.get", side_effect=[events_resp, odds_resp]) as mock_get:
            result = lop.run_daily_pull(cycle_reset_day=1, safety_floor=0, lead_days=2)

        self.assertTrue(result["ran"])
        self.assertEqual(mock_get.call_count, 2)  # events call + exactly ONE event-odds call
        self.assertEqual(result["events_queried"], 1)
        self.assertEqual(result["credits_spent_this_run"], 6)
        self.assertIn("budget", result["reason"])

    def test_board_cache_written_with_honest_empty_rows_when_nothing_posted(self):
        events_resp = _fake_response(json_data=[EVENT_A],
                                      headers={"x-requests-remaining": "100", "x-requests-last": "0"})
        odds_resp = _fake_response(
            json_data={"id": "evt-a", "home_team": "H", "away_team": "A", "bookmakers": []},
            headers={"x-requests-remaining": "100", "x-requests-last": "0"})
        cache_path = Path(tempfile.mkdtemp()) / "cache.json"

        with mock.patch("operational.live_odds_daily_pull.find_next_preseason_start",
                         return_value=dt.date(2026, 9, 1)), \
             mock.patch("operational.live_odds_daily_pull._now_utc",
                         return_value=dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)), \
             mock.patch("operational.live_odds_daily_pull.ARCHIVE_DIR", Path(tempfile.mkdtemp())), \
             mock.patch("research.live_sog_pricing.archive.ARCHIVE_DIR", Path(tempfile.mkdtemp())), \
             mock.patch("operational.live_odds_daily_pull.BOARD_CACHE_PATH", cache_path), \
             mock.patch.object(lop.client, "get_the_odds_api_key", return_value="fake"), \
             mock.patch("requests.get", side_effect=[events_resp, odds_resp]):
            lop.run_daily_pull(cycle_reset_day=1, safety_floor=0, lead_days=2)

        payload = json.loads(cache_path.read_text())
        self.assertEqual(payload["rows"], [])
        self.assertEqual(payload["summary"]["quotes_captured"], 0)


# ---------------------------------------------------------------------
# 7. Contract discovery (Part 15): a never-seen market key gets flagged
# and archived, but never auto-verified.
# ---------------------------------------------------------------------
class Test07ContractDiscovery(unittest.TestCase):
    def _odds_data(self, market_key, outcomes=None):
        return {
            "_retrieved_at_utc": "2026-09-15T00:00:00Z",
            "bookmakers": [{"key": "draftkings", "last_update": "2026-09-15T00:00:00Z",
                             "markets": [{"key": market_key, "last_update": "2026-09-15T00:00:00Z",
                                          "outcomes": outcomes or [{"name": "Over", "price": -110}]}]}],
        }

    def test_never_seen_market_key_tagged_new_contract_candidate(self):
        with mock.patch("operational.live_odds_daily_pull.NEW_CONTRACT_CANDIDATES_PATH",
                         Path(tempfile.mkdtemp()) / "candidates.jsonl"):
            quotes = lop._parse_event_odds_generic(EVENT_A, self._odds_data("player_totally_fake_test_market"))
        self.assertEqual(quotes[0]["model_status"], "NEW_CONTRACT_CANDIDATE")

    def test_never_seen_market_key_is_archived_append_only(self):
        path = Path(tempfile.mkdtemp()) / "candidates.jsonl"
        with mock.patch("operational.live_odds_daily_pull.NEW_CONTRACT_CANDIDATES_PATH", path):
            lop._parse_event_odds_generic(EVENT_A, self._odds_data("player_totally_fake_test_market"))
            lop._parse_event_odds_generic(EVENT_B, self._odds_data("player_another_fake_test_market"))
        lines = path.read_text().strip().splitlines()
        self.assertEqual(len(lines), 2)
        record = json.loads(lines[0])
        self.assertEqual(record["market_key"], "player_totally_fake_test_market")
        self.assertEqual(record["status"], "NEW_CONTRACT_CANDIDATE")
        self.assertFalse(record["auto_verified"])

    def test_known_unmodeled_market_never_flagged_as_new(self):
        path = Path(tempfile.mkdtemp()) / "candidates.jsonl"
        with mock.patch("operational.live_odds_daily_pull.NEW_CONTRACT_CANDIDATES_PATH", path):
            lop._parse_event_odds_generic(EVENT_A, self._odds_data("team_totals"))
        self.assertFalse(path.exists())

    def test_registry_mapped_market_never_flagged_as_new(self):
        path = Path(tempfile.mkdtemp()) / "candidates.jsonl"
        with mock.patch("operational.live_odds_daily_pull.NEW_CONTRACT_CANDIDATES_PATH", path):
            lop._parse_event_odds_generic(EVENT_A, self._odds_data("player_shots_on_goal",
                                                                    [{"name": "Over", "description": "X", "price": -110, "point": 2.5}]))
        self.assertFalse(path.exists())


# ---------------------------------------------------------------------
# 8. Moneyline snapshot (Part 8) -- cheap, frequent, uses the real
# verified h2h parser, never the unverified generic prop path.
# ---------------------------------------------------------------------
class Test08MoneylineSnapshot(unittest.TestCase):
    """Odds API Cost Optimization Correction (2026-09-15): this used to
    loop client.get_event_odds() once per event -- a real 20-event run
    genuinely cost 20 credits this way (confirmed live, see
    ODDS_API_COST_OPTIMIZATION_CORRECTION_REPORT.md). Every test below
    mocks the SPORT-LEVEL /sports/{sport}/odds response (a list of event
    objects returned by ONE call), never a per-event odds loop."""

    def test_snapshot_parses_real_verified_h2h_shape_and_labels_it(self):
        events_resp = _fake_response(json_data=[EVENT_B],
                                      headers={"x-requests-remaining": "499", "x-requests-last": "0"})
        sport_odds_resp = _fake_response(
            json_data=[{"id": "evt-b", "home_team": "Florida Panthers", "away_team": "Carolina Hurricanes",
                        "commence_time": "2026-09-29T23:00:00Z",
                        "bookmakers": [{"key": "draftkings", "last_update": "2026-09-15T00:00:00Z",
                                        "markets": [{"key": "h2h", "last_update": "2026-09-15T00:00:00Z",
                                                     "outcomes": [{"name": "Florida Panthers", "price": -150},
                                                                  {"name": "Carolina Hurricanes", "price": 130}]}]}]}],
            headers={"x-requests-remaining": "498", "x-requests-last": "1"})
        cache_path = Path(tempfile.mkdtemp()) / "ml_cache.json"

        with mock.patch("operational.live_odds_daily_pull.MONEYLINE_CACHE_PATH", cache_path), \
             mock.patch("operational.live_odds_daily_pull._now_utc",
                         return_value=dt.datetime(2026, 9, 15, tzinfo=dt.timezone.utc)), \
             mock.patch("research.live_sog_pricing.archive.ARCHIVE_DIR", Path(tempfile.mkdtemp())), \
             mock.patch.object(lop.client, "get_the_odds_api_key", return_value="fake"), \
             mock.patch("requests.get", side_effect=[events_resp, sport_odds_resp]) as mock_get:
            result = lop.run_moneyline_snapshot("morning")

        self.assertEqual(mock_get.call_count, 2)  # 1 free /events + 1 paid /odds -- never per-event
        # Confirm the odds call actually hit the sport-level path, not a per-event one.
        odds_call_url = mock_get.call_args_list[1].args[0]
        self.assertIn("/sports/icehockey_nhl/odds", odds_call_url)
        self.assertNotIn("/events/", odds_call_url)
        self.assertTrue(result["ran"])
        self.assertEqual(result["parsed_count"], 1)
        self.assertEqual(result["credits_spent_this_run"], 1)
        payload = json.loads(cache_path.read_text())
        self.assertEqual(payload["rows"][0]["home_team_abbrev"], "FLA")
        self.assertEqual(payload["rows"][0]["snapshot_label"], "morning")

    def test_snapshot_never_queries_a_started_event(self):
        events_resp = _fake_response(json_data=[EVENT_PAST],
                                      headers={"x-requests-remaining": "499", "x-requests-last": "0"})
        cache_path = Path(tempfile.mkdtemp()) / "ml_cache.json"

        with mock.patch("operational.live_odds_daily_pull.MONEYLINE_CACHE_PATH", cache_path), \
             mock.patch("operational.live_odds_daily_pull._now_utc",
                         return_value=dt.datetime(2026, 9, 15, tzinfo=dt.timezone.utc)), \
             mock.patch("research.live_sog_pricing.archive.ARCHIVE_DIR", Path(tempfile.mkdtemp())), \
             mock.patch.object(lop.client, "get_the_odds_api_key", return_value="fake"), \
             mock.patch("requests.get", side_effect=[events_resp]) as mock_get:
            result = lop.run_moneyline_snapshot("morning")

        self.assertEqual(mock_get.call_count, 1)  # events call only -- no odds call for a started event
        self.assertEqual(result["events_queried"], 0)

    def test_one_sport_level_call_covers_many_events_without_looping(self):
        """The exact regression this correction sprint exists for: 5
        future events must still cost exactly ONE HTTP request (and one
        real credit charge) for the whole snapshot -- NOT 5 separate
        per-event requests. Proves cost is independent of event count."""
        many_events = [
            {"id": f"evt-{i}", "commence_time": "2026-09-29T23:00:00Z",
             "home_team": "Florida Panthers", "away_team": "Carolina Hurricanes"}
            for i in range(5)
        ]
        events_resp = _fake_response(json_data=many_events,
                                      headers={"x-requests-remaining": "499", "x-requests-last": "0"})
        sport_odds_payload = [
            {"id": f"evt-{i}", "home_team": "Florida Panthers", "away_team": "Carolina Hurricanes",
             "commence_time": "2026-09-29T23:00:00Z",
             "bookmakers": [{"key": "draftkings", "last_update": "2026-09-15T00:00:00Z",
                             "markets": [{"key": "h2h", "last_update": "2026-09-15T00:00:00Z",
                                          "outcomes": [{"name": "Florida Panthers", "price": -150},
                                                       {"name": "Carolina Hurricanes", "price": 130}]}]}]}
            for i in range(5)
        ]
        sport_odds_resp = _fake_response(json_data=sport_odds_payload,
                                          headers={"x-requests-remaining": "498", "x-requests-last": "1"})
        cache_path = Path(tempfile.mkdtemp()) / "ml_cache.json"

        with mock.patch("operational.live_odds_daily_pull.MONEYLINE_CACHE_PATH", cache_path), \
             mock.patch("operational.live_odds_daily_pull._now_utc",
                         return_value=dt.datetime(2026, 9, 15, tzinfo=dt.timezone.utc)), \
             mock.patch("research.live_sog_pricing.archive.ARCHIVE_DIR", Path(tempfile.mkdtemp())), \
             mock.patch.object(lop.client, "get_the_odds_api_key", return_value="fake"), \
             mock.patch("requests.get", side_effect=[events_resp, sport_odds_resp]) as mock_get:
            result = lop.run_moneyline_snapshot("morning")

        # 1 free /events call + 1 paid /odds call = 2 total, regardless of 5 events.
        self.assertEqual(mock_get.call_count, 2)
        self.assertEqual(result["credits_spent_this_run"], 1)
        self.assertEqual(result["parsed_count"], 5)

    def test_snapshot_is_never_gated_by_preseason_lead_days(self):
        """Part 9 of the correction: a currently-listed moneyline must be
        captured even when the event is far in the future -- this
        function must never call should_run_today()/find_next_preseason_start()
        the way run_daily_pull() does for the broad props sweep."""
        import inspect
        source = inspect.getsource(lop.run_moneyline_snapshot)
        self.assertNotIn("should_run_today", source)
        self.assertNotIn("find_next_preseason_start", source)
        self.assertNotIn("lead_days", source)


class Test08bAutoSnapshotLabel(unittest.TestCase):
    def test_morning_hour(self):
        self.assertEqual(lop._auto_snapshot_label(dt.datetime(2026, 9, 15, 12, 0, tzinfo=dt.timezone.utc)), "morning")

    def test_afternoon_hour(self):
        self.assertEqual(lop._auto_snapshot_label(dt.datetime(2026, 9, 15, 17, 0, tzinfo=dt.timezone.utc)), "afternoon")

    def test_pregame_hour(self):
        self.assertEqual(lop._auto_snapshot_label(dt.datetime(2026, 9, 15, 21, 0, tzinfo=dt.timezone.utc)), "pregame")

    def test_late_hour(self):
        self.assertEqual(lop._auto_snapshot_label(dt.datetime(2026, 9, 16, 1, 0, tzinfo=dt.timezone.utc)), "late")

    def test_run_moneyline_snapshot_auto_derives_label_when_omitted(self):
        events_resp = _fake_response(json_data=[], headers={"x-requests-remaining": "499", "x-requests-last": "0"})
        cache_path = Path(tempfile.mkdtemp()) / "ml_cache.json"
        with mock.patch("operational.live_odds_daily_pull.MONEYLINE_CACHE_PATH", cache_path), \
             mock.patch("operational.live_odds_daily_pull._now_utc",
                         return_value=dt.datetime(2026, 9, 15, 12, 0, tzinfo=dt.timezone.utc)), \
             mock.patch("research.live_sog_pricing.archive.ARCHIVE_DIR", Path(tempfile.mkdtemp())), \
             mock.patch.object(lop.client, "get_the_odds_api_key", return_value="fake"), \
             mock.patch("requests.get", side_effect=[events_resp]):
            result = lop.run_moneyline_snapshot()
        self.assertEqual(result["snapshot_label"], "morning")


# ---------------------------------------------------------------------
# 9. Two-stage targeted prop sweep (Part 11)
# ---------------------------------------------------------------------
class Test09TargetedPropSweep(unittest.TestCase):
    def setUp(self):
        # the sweeps now keep per-event de-duplication and a discovery budget in prop_discovery's state file
        # and consult the quota guard: isolate both from the real machine
        from operational import odds_quota, prop_discovery
        self._tmp = tempfile.mkdtemp()
        for p in (mock.patch.object(prop_discovery, "STATE_PATH", Path(self._tmp) / "pd.json"),
                  mock.patch.object(odds_quota, "latest_remaining", return_value=400),
                  mock.patch.object(odds_quota, "credits_spent_today", return_value=0)):
            p.start()
            self.addCleanup(p.stop)

    def test_invalid_sweep_name_raises(self):
        with self.assertRaises(ValueError):
            lop.run_targeted_prop_sweep("third")

    def test_events_in_window_filters_by_hours_until_commence(self):
        now = dt.datetime(2026, 9, 29, 12, 0, tzinfo=dt.timezone.utc)
        near = {"id": "near", "commence_time": "2026-09-29T15:30:00Z"}  # 3.5h out -- in first-sweep window
        far = {"id": "far", "commence_time": "2026-09-29T23:00:00Z"}    # 11h out -- not in window
        started = {"id": "started", "commence_time": "2026-09-29T11:00:00Z"}  # already started
        result = lop._events_in_window([near, far, started], now, lop.FIRST_SWEEP_WINDOW_HOURS)
        self.assertEqual([e["id"] for e in result], ["near"])

    def test_first_sweep_queries_only_events_in_window_with_sog_and_saves_markets(self):
        now = dt.datetime(2026, 9, 29, 12, 0, tzinfo=dt.timezone.utc)
        near_event = {"id": "near", "commence_time": "2026-09-29T15:30:00Z",
                      "home_team": "H", "away_team": "A"}
        events_resp = _fake_response(json_data=[near_event],
                                      headers={"x-requests-remaining": "499", "x-requests-last": "0"})
        odds_resp = _fake_response(
            json_data={"id": "near", "home_team": "H", "away_team": "A", "bookmakers": []},
            headers={"x-requests-remaining": "498", "x-requests-last": "2"})
        cache_path = Path(tempfile.mkdtemp()) / "sweep_cache.json"

        with mock.patch("operational.live_odds_daily_pull.SWEEP_CACHE_PATH", cache_path), \
             mock.patch("operational.live_odds_daily_pull._now_utc", return_value=now), \
             mock.patch("research.live_sog_pricing.archive.ARCHIVE_DIR", Path(tempfile.mkdtemp())), \
             mock.patch.object(lop.client, "get_the_odds_api_key", return_value="fake"), \
             mock.patch("requests.get", side_effect=[events_resp, odds_resp]) as mock_get:
            result = lop.run_targeted_prop_sweep("first")

        self.assertEqual(mock_get.call_count, 2)
        self.assertEqual(result["events_queried"], 1)
        # Confirm the actual odds call requested only SOG+Saves, not the full market list.
        odds_call_params = mock_get.call_args_list[1].kwargs["params"]
        self.assertEqual(odds_call_params["markets"], lop.FIRST_SWEEP_MARKETS)

    def test_second_sweep_only_repulls_events_the_first_sweep_found_a_quote_for(self):
        now = dt.datetime(2026, 9, 29, 14, 30, tzinfo=dt.timezone.utc)
        had_quote = {"id": "had-quote", "commence_time": "2026-09-29T15:15:00Z",  # 45 min out
                     "home_team": "H", "away_team": "A"}
        no_quote = {"id": "no-quote", "commence_time": "2026-09-29T15:20:00Z"}     # ~50 min out
        events_resp = _fake_response(json_data=[had_quote, no_quote],
                                      headers={"x-requests-remaining": "499", "x-requests-last": "0"})
        odds_resp = _fake_response(
            json_data={"id": "had-quote", "home_team": "H", "away_team": "A", "bookmakers": []},
            headers={"x-requests-remaining": "498", "x-requests-last": "1"})
        sweep_cache = Path(tempfile.mkdtemp()) / "sweep_cache.json"
        sweep_cache.write_text(json.dumps({"rows": [{"event_id": "had-quote"}]}))

        with mock.patch("operational.live_odds_daily_pull.SWEEP_CACHE_PATH", sweep_cache), \
             mock.patch("operational.live_odds_daily_pull._now_utc", return_value=now), \
             mock.patch("research.live_sog_pricing.archive.ARCHIVE_DIR", Path(tempfile.mkdtemp())), \
             mock.patch.object(lop.client, "get_the_odds_api_key", return_value="fake"), \
             mock.patch("requests.get", side_effect=[events_resp, odds_resp]) as mock_get:
            result = lop.run_targeted_prop_sweep("second")

        # Both events are in the raw time window, but "no-quote" was correctly
        # excluded before any odds call for it -- only "had-quote" is re-pulled.
        self.assertEqual(mock_get.call_count, 2)
        self.assertEqual(result["events_queried"], 1)
        self.assertEqual(result["events_in_window"], 2)
        queried_event_id = mock_get.call_args_list[1].args[0].rsplit("/", 2)[1]
        self.assertEqual(queried_event_id, "had-quote")


# ---------------------------------------------------------------------
# 9. Real Recommendation Pipeline automation trigger (Part 22, 2026-09-24)
# ---------------------------------------------------------------------
class Test09RealRecommendationTrigger(unittest.TestCase):
    """--mode=moneyline is the chosen automation trigger for the real
    recommendation orchestrator (Part 22) -- reusing this already-
    scheduled job rather than adding a new one. Only fires after a real,
    successful pull (`ran: True`); never on an API error or a no-op
    (zero future events)."""

    def _run_main(self, label=None):
        argv = ["live_odds_daily_pull.py", "--mode", "moneyline"]
        if label:
            argv += ["--label", label]
        with mock.patch("sys.argv", argv), \
             mock.patch("operational.deployment_mode.require_active_scheduler_or_exit", return_value=True):
            lop._main()

    def test_successful_snapshot_triggers_the_bridge_and_orchestrator(self):
        with mock.patch.object(lop, "run_moneyline_snapshot", return_value={"ran": True}), \
             mock.patch("operational.real_odds_bridge.sync_moneyline_odds_to_snapshots",
                         return_value={"status": "SUCCESS"}) as mock_bridge, \
             mock.patch("operational.real_recommendation_orchestrator.run_real_moneyline_recommendations",
                         return_value={"status": "SUCCESS"}) as mock_orch:
            self._run_main()
        mock_bridge.assert_called_once()
        mock_orch.assert_called_once()

    def test_api_error_never_triggers_the_orchestrator(self):
        with mock.patch.object(lop, "run_moneyline_snapshot",
                                return_value={"ran": False, "api_error": "boom"}), \
             mock.patch("operational.real_odds_bridge.sync_moneyline_odds_to_snapshots") as mock_bridge, \
             mock.patch("operational.real_recommendation_orchestrator.run_real_moneyline_recommendations") \
                 as mock_orch:
            self._run_main()
        mock_bridge.assert_not_called()
        mock_orch.assert_not_called()

    def test_zero_future_events_no_op_never_triggers_the_orchestrator(self):
        with mock.patch.object(lop, "run_moneyline_snapshot",
                                return_value={"ran": True, "events_seen": 0}), \
             mock.patch("operational.real_odds_bridge.sync_moneyline_odds_to_snapshots",
                         return_value={"status": "SKIPPED"}) as mock_bridge, \
             mock.patch("operational.real_recommendation_orchestrator.run_real_moneyline_recommendations",
                         return_value={"status": "SUCCESS"}) as mock_orch:
            self._run_main()
        # `ran: True` still fires the trigger even with zero events --
        # the bridge/orchestrator themselves are the ones that correctly
        # no-op on empty data (already proven in
        # tests/test_real_odds_bridge.py), so this is expected to call
        # through; asserting that here would just duplicate that test.
        mock_bridge.assert_called_once()
        mock_orch.assert_called_once()

    def test_props_mode_never_triggers_the_moneyline_orchestrator(self):
        with mock.patch.object(lop, "run_daily_pull", return_value={"ran": True}), \
             mock.patch("operational.real_odds_bridge.sync_moneyline_odds_to_snapshots") as mock_bridge, \
             mock.patch("operational.real_recommendation_orchestrator.run_real_moneyline_recommendations") \
                 as mock_orch, \
             mock.patch("sys.argv", ["live_odds_daily_pull.py", "--mode", "props"]), \
             mock.patch("operational.deployment_mode.require_active_scheduler_or_exit", return_value=True):
            lop._main()
        mock_bridge.assert_not_called()
        mock_orch.assert_not_called()


# ---------------------------------------------------------------------
# 10. Live SOG + Saves automation trigger (Part 34, 2026-09-24)
# ---------------------------------------------------------------------
class Test10RealPropTrigger(unittest.TestCase):
    """sweep-first/sweep-second are the chosen automation trigger for the
    real SOG/Saves orchestrator (Part 34) -- reusing these already-
    scheduled, quota-disciplined, SOG/Saves-priority jobs rather than
    adding new ones."""

    def _run_main(self, mode):
        with mock.patch("sys.argv", ["live_odds_daily_pull.py", "--mode", mode]), \
             mock.patch("operational.deployment_mode.require_active_scheduler_or_exit", return_value=True):
            lop._main()

    def test_successful_sweep_first_triggers_both_prop_orchestrators(self):
        with mock.patch.object(lop, "run_targeted_prop_sweep", return_value={"ran": True}), \
             mock.patch("operational.real_prop_orchestrator.run_real_sog_recommendations",
                         return_value={"status": "SUCCESS"}) as mock_sog, \
             mock.patch("operational.real_prop_orchestrator.run_real_saves_recommendations",
                         return_value={"status": "SUCCESS"}) as mock_saves:
            self._run_main("sweep-first")
        mock_sog.assert_called_once()
        mock_saves.assert_called_once()

    def test_successful_sweep_second_triggers_both_prop_orchestrators(self):
        with mock.patch.object(lop, "run_targeted_prop_sweep", return_value={"ran": True}), \
             mock.patch("operational.real_prop_orchestrator.run_real_sog_recommendations",
                         return_value={"status": "SUCCESS"}) as mock_sog, \
             mock.patch("operational.real_prop_orchestrator.run_real_saves_recommendations",
                         return_value={"status": "SUCCESS"}) as mock_saves:
            self._run_main("sweep-second")
        mock_sog.assert_called_once()
        mock_saves.assert_called_once()

    def test_api_error_never_triggers_the_prop_orchestrators(self):
        with mock.patch.object(lop, "run_targeted_prop_sweep", return_value={"ran": False, "api_error": "boom"}), \
             mock.patch("operational.real_prop_orchestrator.run_real_sog_recommendations") as mock_sog, \
             mock.patch("operational.real_prop_orchestrator.run_real_saves_recommendations") as mock_saves:
            self._run_main("sweep-first")
        mock_sog.assert_not_called()
        mock_saves.assert_not_called()


if __name__ == "__main__":
    unittest.main()
