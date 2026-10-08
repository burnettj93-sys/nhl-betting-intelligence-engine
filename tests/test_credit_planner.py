"""The daily credit plan: priority waterfall, wave-fair game choice, persistence, authorization, and the jobs that obey it."""
from __future__ import annotations

import datetime as dt
import unittest
from unittest import mock

from operational import best_bets as bb
from operational import credit_planner as cp
from operational import live_odds_daily_pull as lop

NOW = dt.datetime(2026, 10, 8, 19, 0, tzinfo=dt.timezone.utc)


def starts(*pairs):
    base = dt.datetime(2026, 10, 8, 22, 0, tzinfo=dt.timezone.utc)
    return {gid: base + dt.timedelta(minutes=m) for gid, m in pairs}


SEVEN = starts(("a", 0), ("b", 0), ("c", 15), ("d", 30), ("e", 180), ("f", 180), ("g", 200))


class TestWaterfall(unittest.TestCase):
    def test_waves_group_puck_drops_within_90_minutes(self):
        self.assertEqual(cp.waves(SEVEN), [["a", "b", "c", "d"], ["e", "f", "g"]])

    def test_ranking_takes_one_game_from_each_wave_in_turn(self):
        self.assertEqual(cp.rank_games(SEVEN)[:4], ["a", "e", "b", "f"])

    def test_tight_budget_prices_few_games_but_every_wave_is_represented(self):
        a = cp.allocate(11.5, SEVEN)
        self.assertEqual(a["allowance"][cp.MONEYLINE_DECISION], 3)
        self.assertEqual(a["allowance"][cp.MONEYLINE_UI], 1)
        self.assertEqual(len(a["games_priced"]), 3)                       # (11.5 - 3 - 1) // 2
        self.assertIn("a", a["games_priced"])
        self.assertIn("e", a["games_priced"])                              # the later wave is not starved
        self.assertEqual(len(a["goals_games"]), 1)                         # the 1.5 credits left cannot buy another game; they buy one goals market
        self.assertGreater(a["shortfall_per_day_required_only"], 0)

    def test_goals_come_after_every_game_is_priced(self):
        a = cp.allocate(20.0, starts(("a", 0), ("b", 0), ("c", 15)), confirmed_games={"a"})
        self.assertEqual(len(a["games_priced"]), 3)
        self.assertEqual(a["saves_games"], ["a"])
        self.assertEqual(len(a["goals_games"]), 3)
        self.assertEqual(a["allowance"][cp.MONEYLINE_UI], 2)

    def test_saves_only_for_confirmed_games_and_only_after_games_are_priced(self):
        a = cp.allocate(11.5, SEVEN, confirmed_games={"a"})
        self.assertEqual(len(a["games_priced"]), 3)                        # breadth of pricing comes first
        self.assertEqual(a["saves_games"], ["a"])                          # the leftover credit goes to saves before goals
        self.assertEqual(a["goals_games"], [])
        self.assertEqual(cp.allocate(11.5, SEVEN)["saves_games"], [])      # no confirmed starter, no saves purchase

    def test_decision_pulls_are_never_starved(self):
        a = cp.allocate(2.0, SEVEN)
        self.assertEqual(a["allowance"][cp.MONEYLINE_DECISION], 2.0)
        self.assertEqual(a["games_priced"], [])


class TestPlanState(unittest.TestCase):
    def test_the_priced_set_is_saved_for_the_day_and_not_reshuffled(self):
        with mock.patch.object(cp, "_persist") as persist, mock.patch.object(cp, "load_plan", return_value=None), mock.patch.object(cp, "spent_today", return_value={}):
            first = cp.day_plan(NOW, "2026-10-08", SEVEN, remaining=297)
        self.assertEqual(persist.call_count, 1)
        with mock.patch.object(cp, "_persist"), mock.patch.object(cp, "load_plan", return_value=first), mock.patch.object(cp, "spent_today", return_value={cp.PROPS: 6.0}):
            later = cp.day_plan(NOW + dt.timedelta(hours=3), "2026-10-08", SEVEN, remaining=290, confirmed_games={"a"})
        self.assertEqual(later["games_priced"], first["games_priced"])

    def test_budget_is_anchored_to_the_start_of_the_day(self):
        before = cp.day_budget(NOW, 297, 0.0)["D"]
        after = cp.day_budget(NOW + dt.timedelta(hours=3), 297 - 8, 8.0)["D"]
        self.assertAlmostEqual(before, after, places=2)

    def test_unknown_quota_gives_no_budget(self):
        self.assertEqual(cp.day_budget(NOW, None, 0.0)["D"], 0.0)


class TestAuthorize(unittest.TestCase):
    def test_hard_reserve_always_wins(self):
        self.assertFalse(cp.authorize(cp.MONEYLINE_DECISION, 1, NOW, remaining=20)["allow"])
        self.assertTrue(cp.authorize(cp.MONEYLINE_DECISION, 1, NOW, remaining=21)["allow"])

    def test_class_allowance_is_enforced_from_the_ledger(self):
        plan = {"allowance": {cp.PROPS: 4.0}}
        with mock.patch.object(cp, "spent_today", return_value={cp.PROPS: 2.0}):
            self.assertTrue(cp.authorize(cp.PROPS, 2, NOW, remaining=200, plan=plan)["allow"])
        with mock.patch.object(cp, "spent_today", return_value={cp.PROPS: 3.0}):
            self.assertEqual(cp.authorize(cp.PROPS, 2, NOW, remaining=200, plan=plan)["reason"], "PROPS_PREGAME_DAILY_ALLOWANCE")

    def test_unknown_quota_denies(self):
        with mock.patch.object(cp.odds_quota, "latest_remaining", return_value=None):
            self.assertEqual(cp.authorize(cp.PROPS, 2, NOW)["reason"], "QUOTA_UNKNOWN")

    def test_month_view_states_the_shortfall(self):
        v = cp.month_view(NOW, 297, 7.0)
        self.assertEqual(v["games_priced_per_day"], 3)
        self.assertGreater(v["shortfall_month_required_only"], 100)
        self.assertGreater(v["shortfall_month_everything"], v["shortfall_month_required_only"])


PLAN = {"day": "2026-10-08", "games_priced": ["1"], "games_not_priced": ["2"], "goals_games": ["1"],
        "allowance": {cp.PROPS: 4.0, cp.GOALS: 1.0, cp.REFRESH: 0.0}, "budget": {"D": 11.5}}


class TestCaptureDecisions(unittest.TestCase):
    def test_only_priced_games_inside_the_actionable_window_get_a_first_capture(self):
        self.assertEqual(bb.planned_decision(PLAN, "1", 1.5, None, NOW), ("FIRST", "OK"))
        self.assertEqual(bb.planned_decision(PLAN, "1", 3.0, None, NOW), (None, "BEFORE_ACTIONABLE_WINDOW"))
        self.assertEqual(bb.planned_decision(PLAN, "2", 1.0, None, NOW), (None, "NOT_IN_CREDIT_PLAN"))
        self.assertEqual(bb.planned_decision(PLAN, "1", 6.0, None, NOW), (None, "OUTSIDE_HORIZON"))

    def test_an_earlier_capture_before_the_window_does_not_count_as_the_planned_capture(self):
        # captured 3.5 hours ago when the game was 5.1 hours away; now 1.6 hours out: that price will be stale at puck drop
        self.assertEqual(bb.planned_decision(PLAN, "1", 1.6, 210.0, NOW), ("FIRST", "OK"))
        # captured 20 minutes ago inside the window: it is the planned capture, so no second purchase
        self.assertEqual(bb.planned_decision(PLAN, "1", 1.2, 20.0, NOW)[0], None)

    def test_a_captured_game_is_not_refreshed_without_leftover_credits(self):
        with mock.patch.object(cp, "spent_today", return_value={}):
            self.assertEqual(bb.planned_decision(PLAN, "1", 0.5, 70.0, NOW)[0], None)
            richer = {**PLAN, "allowance": {**PLAN["allowance"], cp.REFRESH: 4.0}}
            self.assertEqual(bb.planned_decision(richer, "1", 0.5, 70.0, NOW)[0], "REFRESH")

    def test_capture_prices_buys_goals_only_for_planned_games_and_records_the_spend(self):
        class Client:
            def __init__(self):
                self.calls = []
            def get_nhl_events(self):
                return mock.Mock(ok=True, data=[{"id": "e1", "home_team": "Boston Bruins", "away_team": "Utah Mammoth", "commence_time": "2026-10-08T20:15:00Z"}])
            def get_event_odds(self, eid, markets):
                self.calls.append(markets)
                return mock.Mock(ok=True, requests_last=3)
        class Archive:
            written = []
            def archive_result(self, r, **kw):
                self.written.append(kw)
        games = {"1": {"home": "BOS", "away": "UTA", "start_utc": "2026-10-08T20:15:00Z"}}
        client, archive = Client(), Archive()
        with mock.patch.object(cp, "record") as rec, mock.patch.object(cp, "spent_today", return_value={}), mock.patch.object(bb, "decision_age_min", return_value=None), \
                mock.patch.object(cp.odds_quota, "latest_remaining", return_value=297):
            out = bb.capture_prices(NOW, client=client, archive_mod=archive, games=games, plan=PLAN)
        self.assertEqual(client.calls, [f"{bb.MARKETS},{bb.GOALS_MARKET_KEY}"])
        self.assertEqual([(c.args[0], c.args[1]) for c in rec.call_args_list], [(cp.PROPS, 2), (cp.GOALS, 1)])      # goals are accounted in their own class
        self.assertEqual(out["events_captured"], 1)

    def test_a_goals_capture_does_not_eat_the_next_games_base_allowance(self):
        """2026-10-08: BOS's 3-credit capture was recorded as PROPS, so the third planned game (2 more credits) was denied against an allowance of 6."""
        plan = {**PLAN, "allowance": {cp.PROPS: 4.0, cp.GOALS: 1.0, cp.REFRESH: 0.0}}
        spent = {cp.PROPS: 2.0, cp.GOALS: 1.0}
        with mock.patch.object(cp, "spent_today", return_value=spent), mock.patch.object(cp.odds_quota, "latest_remaining", return_value=200):
            self.assertTrue(cp.authorize(cp.PROPS, 2, NOW, plan=plan)["allow"])                      # the second planned game is still allowed
            self.assertFalse(cp.authorize(cp.GOALS, 1, NOW, plan=plan)["allow"])                     # goals allowance is used up, separately

    def test_an_unplanned_game_is_skipped_with_its_reason(self):
        class Client:
            def get_nhl_events(self):
                return mock.Mock(ok=True, data=[{"id": "e2", "home_team": "Toronto Maple Leafs", "away_team": "Montréal Canadiens", "commence_time": "2026-10-08T20:15:00Z"}])
            def get_event_odds(self, *a, **k):
                raise AssertionError("must not buy")
        games = {"2": {"home": "TOR", "away": "MTL", "start_utc": "2026-10-08T20:15:00Z"}}
        with mock.patch.object(bb, "decision_age_min", return_value=None):
            out = bb.capture_prices(NOW, client=Client(), archive_mod=mock.Mock(), games=games, plan=PLAN)
        self.assertEqual(out["skipped"][0]["reason"], "NOT_IN_CREDIT_PLAN")


class TestSweeps(unittest.TestCase):
    def test_first_sweep_is_retired_under_the_plan_and_makes_no_request(self):
        with mock.patch.object(cp, "enforced", return_value=True), mock.patch.object(lop.client, "get_nhl_events", side_effect=AssertionError("no network")):
            out = lop.run_targeted_prop_sweep("first")
        self.assertIn("RETIRED_BY_CREDIT_PLAN", out["reason"])
        self.assertFalse(out["ran"])

    def test_planned_sweeps_never_buy_shots(self):
        self.assertNotIn("shots", lop.PLANNED_SWEEP_MARKETS)
        self.assertEqual(lop.PLANNED_SWEEP_MARKETS, "player_total_saves")


class TestOwnerMarketChoice(unittest.TestCase):
    def test_default_pair_and_an_owner_override_that_trades_depth_for_coverage(self):
        with mock.patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop(cp.PROP_MARKETS_ENV, None)
            self.assertEqual((cp.prop_markets(), cp.base_cost()), (cp.DEFAULT_PROP_MARKETS, 2))
        with mock.patch.dict("os.environ", {cp.PROP_MARKETS_ENV: "player_points"}):
            self.assertEqual((cp.prop_markets(), cp.base_cost()), (("player_points",), 1))
        with mock.patch.dict("os.environ", {cp.PROP_MARKETS_ENV: "player_points,not_a_market"}):
            self.assertEqual(cp.prop_markets(), ("player_points",))                  # unknown names are ignored
        with mock.patch.dict("os.environ", {cp.PROP_MARKETS_ENV: "not_a_market"}):
            self.assertEqual(cp.prop_markets(), cp.DEFAULT_PROP_MARKETS)             # nothing valid: back to the default

    def test_one_market_prices_twice_the_games_for_the_same_credits(self):
        two = cp.allocate(11.5, SEVEN, base=2)
        one = cp.allocate(11.5, SEVEN, base=1)
        self.assertEqual((len(two["games_priced"]), len(one["games_priced"])), (3, 7))

    def test_the_age_check_follows_the_markets_actually_bought(self):
        calls = []

        def fake(event_id, market=None, **kw):
            calls.append(market)
            return (NOW - dt.timedelta(minutes=10), {}) if market == bb.POINTS_MARKET_KEY else None
        with mock.patch.object(bb, "latest_capture", side_effect=fake):
            self.assertEqual(round(bb.decision_age_min("e", NOW, (bb.POINTS_MARKET_KEY,))), 10)   # points only: no shots capture is needed
            self.assertIsNone(bb.decision_age_min("e", NOW))                                       # the default pair still needs both


class TestScenarioCoverageArithmetic(unittest.TestCase):
    def test_budget_scaling_reproduces_the_documented_configurations(self):
        """docs/ODDS_BUDGET_CONFIGURATIONS.md: with a large allowance the same waterfall prices every game and funds goals, saves and refreshes."""
        rich = cp.allocate(800.0, SEVEN, confirmed_games={"a", "b", "c"})
        self.assertEqual(len(rich["games_priced"]), 7)
        self.assertEqual(len(rich["goals_games"]), 7)
        self.assertEqual(len(rich["saves_games"]), 3)
        self.assertGreater(rich["allowance"][cp.REFRESH], 100)


class TestDecisionPullGuard(unittest.TestCase):
    def test_the_t35_pull_ignores_the_soft_daily_budget_and_obeys_the_hard_reserve(self):
        from operational import moneyline_pregame as mp
        with mock.patch.object(cp, "enforced", return_value=True), mock.patch.object(cp.odds_quota, "latest_remaining", return_value=289):
            self.assertTrue(mp._default_guard()["allow"])           # was DEFERRED on 2026-10-08 with 44 spent against a 33.6 soft budget
        with mock.patch.object(cp, "enforced", return_value=True), mock.patch.object(cp.odds_quota, "latest_remaining", return_value=20):
            self.assertEqual(mp._default_guard()["reason"], "HARD_RESERVE")


class TestLedger(unittest.TestCase):
    def test_record_and_read_back_by_et_day(self):
        cp.record(cp.PROPS, 3, NOW, event="e")
        cp.record(cp.MONEYLINE_UI, 1, NOW + dt.timedelta(minutes=5))
        spent = cp.spent_today(NOW)
        self.assertEqual(spent.get(cp.PROPS), 3.0)
        self.assertEqual(spent.get(cp.MONEYLINE_UI), 1.0)
        cp._ledger().unlink()


if __name__ == "__main__":
    unittest.main()
