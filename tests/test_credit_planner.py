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

    def test_tight_budget_prices_few_games_that_can_be_compared_at_the_same_time(self):
        a = cp.allocate(11.5, SEVEN)
        self.assertEqual(a["allowance"][cp.MONEYLINE_DECISION], 3)
        self.assertEqual(a["allowance"][cp.MONEYLINE_UI], 1)
        # 11.5 - 3 (decisions) - 1 (display) = 7.5: two pregame captures (4) are funded first, then the morning first look (shots, 1 each) for three games
        self.assertEqual(len(a["games_priced"]), 2)
        self.assertEqual(a["morning_games"], cp.rank_games(SEVEN)[:3])
        self.assertEqual(a["morning_markets"], [cp.SHOTS_KEY])
        self.assertEqual(a["games_priced"], ["a", "b"])                    # two games of ONE wave: their pregame prices can be compared at one evaluation time (one game from each wave never can)
        self.assertEqual(a["goals_games"], [])                             # nothing is left for goals
        self.assertGreater(a["shortfall_per_day_required_only"], 0)

    def test_goals_come_after_every_game_is_priced(self):
        a = cp.allocate(30.0, starts(("a", 0), ("b", 0), ("c", 15)), confirmed_games={"a"})
        self.assertEqual(len(a["games_priced"]), 3)
        self.assertEqual(a["morning_markets"], [cp.SHOTS_KEY, cp.POINTS_KEY])   # affordable for every game: shots and points both
        self.assertEqual(a["saves_games"], ["a"])
        self.assertEqual(len(a["goals_games"]), 3)
        self.assertEqual(a["allowance"][cp.MONEYLINE_UI], 2)

    def test_saves_only_for_confirmed_games_and_only_after_games_are_priced(self):
        a = cp.allocate(16.0, SEVEN, confirmed_games={"a"})
        self.assertEqual(len(a["games_priced"]), 2)                        # pregame for two games and the morning look come first
        self.assertEqual(a["saves_games"], ["a"])                          # the leftover credit goes to saves before goals
        self.assertEqual(a["goals_games"], [])
        self.assertEqual(cp.allocate(16.0, SEVEN)["saves_games"], [])      # no confirmed starter, no saves purchase
        self.assertEqual(cp.allocate(11.5, SEVEN, confirmed_games={"a"})["saves_games"], [])   # and none when the morning look has used the credit

    def test_decision_pulls_are_never_starved(self):
        a = cp.allocate(2.0, SEVEN)
        self.assertEqual(a["allowance"][cp.MONEYLINE_DECISION], 2.0)
        self.assertEqual(a["games_priced"], [])


class TestMorningSwitch(unittest.TestCase):
    def test_turning_the_morning_look_off_restores_the_pregame_only_allocation(self):
        with mock.patch.object(cp, "morning_enabled", return_value=False):
            a = cp.allocate(11.5, SEVEN)
        self.assertEqual(a["morning_games"], [])
        self.assertEqual(len(a["games_priced"]), 3)                        # the allocation that applied before the morning look existed
        self.assertEqual(a["allowance"][cp.MORNING], 0.0)

    def test_the_switch_reads_the_environment(self):
        with mock.patch.dict("os.environ", {cp.MORNING_ENV: "off"}):
            self.assertFalse(cp.morning_enabled())
        with mock.patch.dict("os.environ", {cp.MORNING_ENV: "on"}):
            self.assertTrue(cp.morning_enabled())


class TestTransitionDay(unittest.TestCase):
    def test_a_plan_saved_before_the_morning_look_cannot_be_pushed_over_its_budget_by_it(self):
        old = {"day": "2026-10-09", "made_at_utc": "x", "D": 11.43, "games_today": 4, "games_priced": ["a", "b", "c"], "games_not_priced": ["d"], "goals_games": ["a"], "saves_games": [],
               "allowance": {cp.MONEYLINE_DECISION: 3, cp.MONEYLINE_UI: 1.0, cp.PROPS: 6.0, cp.SAVES: 0.0, cp.GOALS: 1.0, cp.REFRESH: 0.43},
               "budget": {"D": 11.43}}
        starts4 = starts(("a", 0), ("b", 0), ("c", 15), ("d", 30))
        with mock.patch.object(cp, "load_plan", return_value=old), mock.patch.object(cp, "_persist"), mock.patch.object(cp, "spent_today", return_value={}), \
                mock.patch.object(cp, "day_budget", return_value={"D": 11.43}):
            plan = cp.day_plan(NOW, "2026-10-09", starts4, remaining=282)
        self.assertEqual(plan["morning_games"], [])                                   # 11.43 is fully committed: no credits are left for a morning look today
        self.assertEqual(plan["allowance"][cp.MORNING], 0.0)
        total = sum(v for k, v in plan["allowance"].items())
        self.assertLessEqual(total, 11.43 + 1.0)                                      # the tomorrow check's one credit is the only addition
        richer = {**old, "allowance": {**old["allowance"], cp.PROPS: 2.0}, "budget": {"D": 11.43}}
        with mock.patch.object(cp, "load_plan", return_value=richer), mock.patch.object(cp, "_persist"), mock.patch.object(cp, "spent_today", return_value={}), \
                mock.patch.object(cp, "day_budget", return_value={"D": 11.43}):
            plan2 = cp.day_plan(NOW, "2026-10-09", starts4, remaining=282)
        self.assertGreater(len(plan2["morning_games"]), 0)                            # credits that really are free fund a morning look


class TestGoalsComeBeforeTheDisplayRefreshAndTheTomorrowCheck(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict("os.environ", {cp.PROP_MARKETS_ENV: cp.SHOTS_KEY})       # production's shots-only default (the test package's default is the two-market capture)
        p.start()
        self.addCleanup(p.stop)

    def test_a_two_game_day_funds_goals_for_both_games(self):
        two = starts(("a", 0), ("b", 60))
        a = cp.allocate(10.94, two, set(), {})
        self.assertEqual(a["goals_games"], ["a", "b"])                                  # the board's section 1 has prices for both games
        self.assertEqual(a["allowance"][cp.GOALS], 2.0)
        self.assertGreater(a["allowance"][cp.TOMORROW], 0.0)                            # the Tomorrow page's check keeps what is left, ahead of the second display refresh
        self.assertEqual(a["allowance"][cp.MONEYLINE_UI], 1.0)                          # (the first, morning display pull is unaffected)
        self.assertLessEqual(sum(a["allowance"].values()), 10.94 + 1e-9)
        self.assertEqual(a["morning_games"], ["a", "b"])                                # the 8 AM look is still bought first

    def test_a_plan_saved_with_the_old_order_is_retaken_for_those_classes_only_and_never_overspends(self):
        old = {"day": "2026-10-14", "made_at_utc": "x", "D": 10.94, "games_today": 2, "games_priced": ["a", "b"], "games_not_priced": [], "goals_games": [], "points_games": ["a", "b"],
               "morning_games": ["a", "b"], "morning_markets": [cp.SHOTS_KEY], "saves_games": [],
               "allowance": {cp.MONEYLINE_DECISION: 3, cp.MONEYLINE_UI: 1.94, cp.PROPS: 2.0, cp.MORNING: 2.0, cp.SAVES: 0.0, cp.GOALS: 0.0, cp.POINTS_ADDON: 2.0, cp.REFRESH: 0.0, cp.TOMORROW: 0.0},
               "budget": {"D": 10.94}}
        two = starts(("a", 0), ("b", 60))
        with mock.patch.object(cp, "load_plan", return_value=old), mock.patch.object(cp, "_persist"), mock.patch.object(cp, "spent_today", return_value={}), \
                mock.patch.object(cp, "day_budget", return_value={"D": 10.94}):
            plan = cp.day_plan(NOW, "2026-10-14", two, remaining=200)
        self.assertEqual(plan["goals_games"], ["a", "b"])
        self.assertEqual(plan["points_games"], [])
        self.assertEqual(plan["games_priced"], ["a", "b"])                              # the priced set is not reshuffled
        self.assertEqual(plan["morning_games"], ["a", "b"])
        self.assertLessEqual(sum(plan["allowance"].values()), 10.94 + 1e-6)
        self.assertEqual(plan["alloc_version"], cp.ALLOC_VERSION)

    def test_a_game_whose_pregame_capture_already_ran_gets_no_goals_reservation(self):
        old = {"day": "2026-10-14", "made_at_utc": "x", "D": 10.94, "games_today": 2, "games_priced": ["a", "b"], "games_not_priced": [], "goals_games": [], "points_games": [],
               "morning_games": ["a", "b"], "morning_markets": [cp.SHOTS_KEY], "saves_games": [],
               "allowance": {cp.MONEYLINE_DECISION: 3, cp.MONEYLINE_UI: 1.94, cp.PROPS: 2.0, cp.MORNING: 2.0, cp.SAVES: 0.0, cp.GOALS: 0.0, cp.POINTS_ADDON: 0.0, cp.REFRESH: 0.0, cp.TOMORROW: 0.0},
               "budget": {"D": 10.94}}
        two = starts(("a", 0), ("b", 60))
        ledger = [{"at": "2026-10-14T20:00:00Z", "class": cp.PROPS, "credits": 1.0, "game_id": "a", "markets": cp.SHOTS_KEY}]
        with mock.patch.object(cp, "load_plan", return_value=old), mock.patch.object(cp, "_persist"), mock.patch.object(cp, "spent_today", return_value={}), \
                mock.patch.object(cp, "day_budget", return_value={"D": 10.94}), mock.patch.object(cp, "read_ledger", return_value=ledger):
            plan = cp.day_plan(NOW, "2026-10-14", two, remaining=200)
        self.assertEqual(plan["goals_games"], ["b"])                                    # "a" was captured without goals; its goals cannot be added later
        self.assertEqual(plan["allowance"][cp.GOALS], 1.0)
        self.assertGreater(plan["allowance"][cp.TOMORROW], 0.0)


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
        self.assertEqual(v["games_priced_per_day"], 2)
        self.assertGreater(v["shortfall_month_required_only"], 100)
        self.assertGreater(v["shortfall_month_everything"], v["shortfall_month_required_only"])


PLAN = {"day": "2026-10-08", "games_priced": ["1"], "games_not_priced": ["2"], "goals_games": ["1"],
        "allowance": {cp.PROPS: 4.0, cp.GOALS: 1.0, cp.REFRESH: 0.0}, "budget": {"D": 11.5}}


class TestCaptureDecisions(unittest.TestCase):
    def test_only_priced_games_inside_the_actionable_window_get_a_first_capture(self):
        self.assertEqual(bb.planned_decision(PLAN, "1", 1.5, None, NOW), ("FIRST", "OK"))
        self.assertEqual(bb.planned_decision(PLAN, "1", 3.0, None, NOW), (None, "BEFORE_ACTIONABLE_WINDOW"))
        self.assertEqual(bb.planned_decision(PLAN, "2", 1.0, None, NOW), (None, "NOT_IN_CREDIT_PLAN"))
        self.assertEqual(bb.planned_decision(PLAN, "1", 6.0, None, NOW), (None, "BEFORE_ACTIONABLE_WINDOW"))      # no five-hour cut-off any more: the morning slot buys earlier

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

    def _capture(self, plan, requests_last):
        class Client:
            def __init__(self):
                self.calls = []
            def get_nhl_events(self):
                return mock.Mock(ok=True, data=[{"id": "e1", "home_team": "Boston Bruins", "away_team": "Utah Mammoth", "commence_time": "2026-10-08T20:15:00Z"}])
            def get_event_odds(self, eid, markets):
                self.calls.append(markets)
                return mock.Mock(ok=True, requests_last=requests_last)
        games = {"1": {"home": "BOS", "away": "UTA", "start_utc": "2026-10-08T20:15:00Z"}}
        client = Client()
        with mock.patch.dict("os.environ", {cp.PROP_MARKETS_ENV: cp.SHOTS_KEY}), mock.patch.object(cp, "record") as rec, mock.patch.object(cp, "spent_today", return_value={}), \
                mock.patch.object(bb, "decision_age_min", return_value=None), mock.patch.object(cp.odds_quota, "latest_remaining", return_value=297):
            out = bb.capture_prices(NOW, client=client, archive_mod=mock.Mock(), games=games, plan=plan)
        return client, rec, out

    def test_points_ride_on_the_pregame_shots_capture_for_planned_games_and_are_accounted_apart(self):
        plan = {**PLAN, "goals_games": [], "points_games": ["1"], "allowance": {cp.PROPS: 4.0, cp.POINTS_ADDON: 1.0, cp.GOALS: 0.0, cp.REFRESH: 0.0}}
        client, rec, out = self._capture(plan, requests_last=2)
        self.assertEqual(client.calls, [f"{cp.SHOTS_KEY},{cp.POINTS_KEY}"])
        self.assertEqual([(c.args[0], c.args[1]) for c in rec.call_args_list], [(cp.PROPS, 1), (cp.POINTS_ADDON, 1)])

    def test_a_game_without_a_points_credit_buys_shots_alone_and_says_points_were_not_fetched(self):
        plan = {**PLAN, "goals_games": [], "points_games": [], "allowance": {cp.PROPS: 4.0, cp.POINTS_ADDON: 0.0, cp.GOALS: 0.0, cp.REFRESH: 0.0}}
        with mock.patch("operational.price_availability.note_unfetched") as note:
            client, rec, _ = self._capture(plan, requests_last=1)
        self.assertEqual(client.calls, [cp.SHOTS_KEY])
        self.assertEqual([(c.args[0], c.args[1]) for c in rec.call_args_list], [(cp.PROPS, 1)])
        self.assertTrue(any(c.args[2] == cp.POINTS_KEY and "BUDGET" in str(c.args[3]) for c in note.call_args_list))

    def test_a_denied_points_credit_drops_points_not_the_game(self):
        plan = {**PLAN, "goals_games": [], "points_games": ["1"], "allowance": {cp.PROPS: 4.0, cp.POINTS_ADDON: 0.0, cp.GOALS: 0.0, cp.REFRESH: 0.0}}
        client, _, out = self._capture(plan, requests_last=1)
        self.assertEqual((client.calls, out["events_captured"]), ([cp.SHOTS_KEY], 1))

    def _two_game_run(self, now, second_offset_min, plan):
        t1 = now + dt.timedelta(minutes=90)
        t2 = t1 + dt.timedelta(minutes=second_offset_min)
        iso = lambda t: t.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731

        class Client:
            def __init__(self):
                self.calls = []
            def get_nhl_events(self):
                return mock.Mock(ok=True, data=[{"id": "e1", "home_team": "Boston Bruins", "away_team": "Utah Mammoth", "commence_time": iso(t1)},
                                                {"id": "e2", "home_team": "Toronto Maple Leafs", "away_team": "Montréal Canadiens", "commence_time": iso(t2)}])
            def get_event_odds(self, eid, markets):
                self.calls.append(eid)
                return mock.Mock(ok=True, requests_last=1)
        games = {"1": {"home": "BOS", "away": "UTA", "start_utc": iso(t1)}, "2": {"home": "TOR", "away": "MTL", "start_utc": iso(t2)}}
        client = Client()
        with mock.patch.dict("os.environ", {cp.PROP_MARKETS_ENV: cp.SHOTS_KEY}), mock.patch.object(cp, "record"), mock.patch.object(cp, "spent_today", return_value={}), \
                mock.patch.object(bb, "decision_age_min", return_value=None), mock.patch.object(bb, "_captured_times", return_value={cp.SHOTS_KEY: None}), mock.patch.object(cp.odds_quota, "latest_remaining", return_value=297):
            bb.capture_prices(now, client=client, archive_mod=mock.Mock(), games=games, plan=plan)
        return client.calls

    def test_games_of_one_wave_are_captured_together_when_the_first_game_reaches_its_window(self):
        now = dt.datetime(2026, 10, 8, 19, 0, tzinfo=dt.timezone.utc)                               # game 1 in 90 minutes (inside 105), game 2 an hour after it
        plan = {**PLAN, "games_priced": ["1", "2"], "games_not_priced": [], "goals_games": [], "points_games": [], "allowance": {cp.PROPS: 4.0, cp.GOALS: 0.0, cp.REFRESH: 0.0}}
        self.assertEqual(self._two_game_run(now, 60, plan), ["e1", "e2"])                          # game 2 is 150 minutes away: on its own it would wait another 45 minutes

    def test_a_game_in_a_later_wave_waits_for_its_own_window(self):
        now = dt.datetime(2026, 10, 8, 19, 0, tzinfo=dt.timezone.utc)
        plan = {**PLAN, "games_priced": ["1", "2"], "games_not_priced": [], "goals_games": [], "points_games": [], "allowance": {cp.PROPS: 4.0, cp.GOALS: 0.0, cp.REFRESH: 0.0}}
        self.assertEqual(self._two_game_run(now, 150, plan), ["e1"])                               # 150 minutes later is a different wave

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


class TestConnectedCaptures(unittest.TestCase):
    def test_games_are_bought_a_whole_wave_at_a_time_biggest_wave_first(self):
        starts_ = starts(("a", 0), ("b", 30), ("c", 200), ("d", 215), ("e", 230), ("f", 400))
        self.assertEqual(cp.connected_order(starts_), ["c", "d", "e", "a", "b", "f"])                # the three-game wave, then the two-game wave, then the lone game

    def test_an_isolated_tail_is_not_bought_when_other_games_are_chosen(self):
        starts_ = starts(("a", 0), ("b", 30), ("c", 200), ("d", 215))
        self.assertEqual(cp.connected_prefix(starts_, 3), ["a", "b"])                                  # one game of the next wave would be alone: not bought
        self.assertEqual(cp.connected_prefix(starts_, 4), ["a", "b", "c", "d"])
        self.assertEqual(cp.connected_prefix(starts_, 1), ["a"])                                       # a single affordable game is still the best use of the credit

    def test_the_credit_of_a_dropped_isolated_capture_stays_in_the_day(self):
        starts_ = starts(("a", 0), ("b", 30), ("c", 200))
        with mock.patch.dict("os.environ", {cp.PROP_MARKETS_ENV: "player_shots_on_goal_alternate"}):
            a = cp.allocate(2.0 + 3 + 1 + 3.0, starts_)                                                # decisions 3 + display 1, then 3 credits: pregame for two games (2) ...
        self.assertEqual(a["games_priced"], ["a", "b"])


class TestPointsAddOn(unittest.TestCase):
    """Shots are bought first; points ride on the pregame capture as an add-on, lead games first, so shots never lose a game to points and points are not silently dropped."""
    SHOTS_ONLY = {cp.PROP_MARKETS_ENV: "player_shots_on_goal_alternate"}

    def test_goals_come_before_points_and_points_get_what_is_left(self):
        with mock.patch.dict("os.environ", {cp.PROP_MARKETS_ENV: "player_shots_on_goal_alternate", cp.POINTS_ADDON_ENV: ""}):
            self.assertTrue(cp.points_addon_enabled())                      # the add-on is on by default: the builder keeps its Points market whenever credits allow
            tight = cp.allocate(11.5, SEVEN)
            roomy = cp.allocate(30.0, SEVEN)
        self.assertEqual(tight["points_games"], [])                           # a tight day: shots, the morning look and goals first; points are not funded and are reported as such
        self.assertEqual(roomy["goals_games"], roomy["games_priced"][:len(roomy["goals_games"])])
        self.assertGreaterEqual(len(roomy["goals_games"]), 1)
        self.assertGreaterEqual(len(roomy["points_games"]), 1)
        self.assertTrue(set(roomy["points_games"]) <= set(roomy["games_priced"]))
        self.assertGreaterEqual(len(roomy["goals_games"]), len(roomy["points_games"]))   # goals are funded first
        with mock.patch.dict("os.environ", {cp.PROP_MARKETS_ENV: "player_shots_on_goal_alternate", cp.POINTS_ADDON_ENV: "off"}):
            self.assertFalse(cp.points_addon_enabled())
            self.assertEqual(cp.allocate(30.0, SEVEN)["points_games"], [])

    def test_the_morning_look_is_still_bought_before_goals_or_points(self):
        with mock.patch.dict("os.environ", self.SHOTS_ONLY):
            a = cp.allocate(11.5, SEVEN)
        self.assertGreaterEqual(a["allowance"][cp.MORNING], 1.0)
        self.assertEqual(a["morning_markets"], [cp.SHOTS_KEY])

    def test_a_tight_day_buys_shots_not_points(self):
        with mock.patch.dict("os.environ", self.SHOTS_ONLY):
            a = cp.allocate(5.0, SEVEN)                                   # decision 3 + display 1 leave one credit: one game's shots, no points
        self.assertEqual((len(a["games_priced"]), a["points_games"]), (1, []))

    def test_a_generous_day_adds_points_to_every_priced_game(self):
        with mock.patch.dict("os.environ", self.SHOTS_ONLY):
            a = cp.allocate(40.0, SEVEN)
        self.assertEqual(sorted(a["points_games"]), sorted(a["games_priced"]))

    def test_no_add_on_when_the_owner_already_buys_points_with_every_game(self):
        with mock.patch.dict("os.environ", {cp.PROP_MARKETS_ENV: f"{cp.SHOTS_KEY},{cp.POINTS_KEY}"}):
            a = cp.allocate(11.5, SEVEN)
        self.assertEqual(a["points_games"], [])

    def test_the_add_on_has_its_own_allowance_in_authorize(self):
        plan = {"allowance": {cp.POINTS_ADDON: 2.0}}
        with mock.patch.object(cp, "spent_today", return_value={cp.POINTS_ADDON: 2.0}):
            self.assertFalse(cp.authorize(cp.POINTS_ADDON, 1, NOW, remaining=400, plan=plan)["allow"])
        with mock.patch.object(cp, "spent_today", return_value={cp.POINTS_ADDON: 1.0}):
            self.assertTrue(cp.authorize(cp.POINTS_ADDON, 1, NOW, remaining=400, plan=plan)["allow"])
        self.assertFalse(cp.authorize(cp.POINTS_ADDON, 1, NOW, remaining=400, plan={"allowance": {}})["allow"])    # a plan without the class buys none


class TestOwnerMarketChoice(unittest.TestCase):
    def test_default_pair_and_an_owner_override_that_trades_depth_for_coverage(self):
        with mock.patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop(cp.PROP_MARKETS_ENV, None)
            self.assertEqual((cp.prop_markets(), cp.base_cost()), ((cp.SHOTS_KEY,), 1))        # the default buys each game's shots ladder only
        with mock.patch.dict("os.environ", {cp.PROP_MARKETS_ENV: f"{cp.SHOTS_KEY},{cp.POINTS_KEY}"}):
            self.assertEqual((cp.prop_markets(), cp.base_cost()), ((cp.SHOTS_KEY, cp.POINTS_KEY), 2))   # points are an owner opt-in
        with mock.patch.dict("os.environ", {cp.PROP_MARKETS_ENV: "player_points"}):
            self.assertEqual((cp.prop_markets(), cp.base_cost()), (("player_points",), 1))
        with mock.patch.dict("os.environ", {cp.PROP_MARKETS_ENV: "player_points,not_a_market"}):
            self.assertEqual(cp.prop_markets(), ("player_points",))                  # unknown names are ignored
        with mock.patch.dict("os.environ", {cp.PROP_MARKETS_ENV: "not_a_market"}):
            self.assertEqual(cp.prop_markets(), cp.DEFAULT_PROP_MARKETS)             # nothing valid: back to the default

    def test_one_market_prices_twice_the_games_for_the_same_credits(self):
        two = cp.allocate(11.5, SEVEN, base=2)
        one = cp.allocate(11.5, SEVEN, base=1)
        # one market halves every capture's price, so the same credits fund twice the morning breadth (the pregame minimum is two games either way)
        self.assertEqual((len(two["morning_games"]), len(one["morning_games"])), (3, 5))
        self.assertEqual(one["morning_markets"], [cp.SHOTS_KEY])

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


class TestLegacyLedgerRows(unittest.TestCase):
    def test_a_three_credit_props_row_with_goals_is_split_into_base_and_goals(self):
        cp.record(cp.PROPS, 3, NOW, game_id="1", markets="player_shots_on_goal_alternate,player_points,player_goal_scorer_anytime")
        cp.record(cp.PROPS, 2, NOW, game_id="2", markets="player_shots_on_goal_alternate,player_points")
        spent = cp.spent_today(NOW)
        self.assertEqual((spent[cp.PROPS], spent[cp.GOALS]), (4.0, 1.0))
        cp._ledger().unlink()


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
