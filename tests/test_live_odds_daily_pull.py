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


if __name__ == "__main__":
    unittest.main()
