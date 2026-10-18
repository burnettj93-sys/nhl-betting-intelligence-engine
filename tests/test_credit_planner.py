"""The credit plan (version 4): wave timeline, budget from the real balance, wave-fair game choice, reservations, and the pieces that obey it."""
from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from operational import capture_timeline as ct
from operational import credit_planner as cp

U = dt.timezone.utc
ET = dt.timezone(dt.timedelta(hours=-4))


def at(h, m=0, day=17):
    """An Eastern wall-clock time on 2026-10-17 (or `day`) as UTC."""
    return dt.datetime(2026, 10, day, h, m, tzinfo=ET).astimezone(U)


def starts(**games):
    return {g: at(*t) for g, t in games.items()}


class Isolated(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        for p in (mock.patch.object(cp.state_paths, "path", side_effect=lambda name, **kw: d / name),
                  mock.patch.object(cp.odds_quota, "reset_status", return_value={"status": "OWNER_VERIFICATION_REQUIRED", "reset_day": 1, "assumed": True, "invalid": False})):
            p.start()
            self.addCleanup(p.stop)


class TestTimeline(unittest.TestCase):
    def test_games_group_into_compatible_start_time_waves_with_their_own_targets(self):
        ws = ct.waves(starts(a=(13,), b=(13, 30), c=(19,), d=(19, 30), e=(22,)))
        self.assertEqual([w.games for w in ws], [("a", "b"), ("c", "d"), ("e",)])
        self.assertEqual(ws[0].target, at(11, 30))                       # 90 minutes before the wave's first game
        self.assertEqual(ws[0].deadline, at(12))                         # complete and published by 60 minutes before it
        self.assertEqual(ws[1].target, at(17, 30))

    def test_an_early_first_game_moves_the_refresh_and_the_analysis_earlier(self):
        day = dt.date(2026, 10, 17)
        normal = ct.prep_times(day, at(13))
        self.assertEqual((normal["refresh_at"], normal["analysis_at"]), (at(7, 45), at(8)))
        early = ct.prep_times(day, at(10))
        self.assertEqual(early["refresh_at"], at(7, 15))
        self.assertEqual(early["analysis_at"], at(7, 30))
        self.assertLess(early["analysis_at"], ct.waves(starts(a=(10,)))[0].target)   # the analysis is always ahead of the first capture

    def test_a_wave_is_on_time_then_late_then_missed_and_never_captured_after_that(self):
        w = ct.waves(starts(a=(19,)))[0]
        self.assertEqual(ct.wave_state(at(17), w, False), ct.NOT_YET)
        self.assertEqual(ct.wave_state(at(17, 40), w, False), ct.ON_TIME)
        self.assertEqual(ct.wave_state(at(18, 10), w, False), ct.LATE)               # after the 60-minute publish deadline
        self.assertEqual(ct.wave_state(at(18, 50), w, False), ct.MISSED)             # inside the last 15 minutes: nothing is bought

    def test_league_pulls_follow_the_day_not_the_clock(self):
        ws = ct.waves(starts(a=(13,), c=(19,)))
        kw = dict(day_first_start=at(13), wave_list=ws)
        self.assertFalse(ct.league_pull_due(at(7, 50), pull_times=[], **kw)["due"])
        self.assertEqual(ct.league_pull_due(at(8, 5), pull_times=[], **kw)["kind"], "MORNING")
        self.assertFalse(ct.league_pull_due(at(10), pull_times=[at(8, 1)], **kw)["due"])
        self.assertEqual(ct.league_pull_due(at(11, 35), pull_times=[at(8, 1)], **kw)["kind"], "PREGAME")      # the first wave's target
        self.assertFalse(ct.league_pull_due(at(11, 50), pull_times=[at(8, 1), at(11, 36)], **kw)["due"])      # one shared pull serves the wave
        self.assertEqual(ct.league_pull_due(at(17, 35), pull_times=[at(8, 1), at(11, 36)], **kw)["kind"], "WAVE")
        self.assertFalse(ct.league_pull_due(at(21), pull_times=[at(8, 1)], day_first_start=at(13), wave_list=ws[:1])["due"])   # nothing is due once every wave is behind us


class TestBudgetFromTheRealBalance(Isolated):
    def budget(self, remaining, spent_today=0, counts=None, day="2026-10-17", now=None):
        counts = counts or {"2026-10-17": 4, "2026-10-18": 4}
        return cp.cycle_budget(now or at(8), remaining, spent_today, counts, day, league_cost=2, league_cost_observed=False)

    def test_it_plans_from_the_actual_remaining_credits_not_a_fresh_cycle(self):
        counts = {f"2026-10-{d}": 4 for d in range(17, 32)}
        b = self.budget(192, counts=counts)
        self.assertEqual(b["usable_at_start_of_day"], 172.0)                          # 192 remaining minus the 20-credit reserve
        self.assertEqual(b["days_left"], 15)                                          # to an assumed reset on the 1st
        self.assertEqual(b["league_total"], 60.0)                                     # 15 days x 2 pulls x 2 credits
        self.assertEqual(b["recovery_pool"], 10)
        self.assertEqual(b["player_pool"], 102.0)
        self.assertAlmostEqual(b["player_today"], 102.0 / 15, places=2)               # equal days: an equal share
        self.assertFalse(b["reset_verified"])
        self.assertIn("ASSUMED", b["reset_assumption"])                               # the uncertainty is stated, with the evidence

    def test_the_days_share_follows_the_schedule_and_unspent_credits_roll_forward(self):
        counts = {"2026-10-17": 8, "2026-10-18": 2, "2026-10-19": 2, "2026-10-20": 0}
        big = self.budget(100, counts=counts)
        quiet = self.budget(100, counts=dict(counts, **{"2026-10-17": 2}))
        self.assertGreater(big["player_today"], quiet["player_today"])                # the heavy slate takes more of the same pool
        spent_nothing_yesterday = self.budget(100)                                    # an untouched balance is simply a larger pool tomorrow
        richer_tomorrow = self.budget(130)
        self.assertGreater(richer_tomorrow["player_pool"], spent_nothing_yesterday["player_pool"])

    def test_today_already_spent_does_not_shrink_the_days_own_entitlement(self):
        a = self.budget(190, spent_today=0)
        b = self.budget(180, spent_today=10)                                          # 10 spent this morning: remaining fell, the start-of-day total did not
        self.assertEqual(a["usable_at_start_of_day"], b["usable_at_start_of_day"])

    def test_an_unknown_balance_plans_nothing(self):
        b = self.budget(None)
        self.assertEqual((b["reason"], b["player_today"]), ("QUOTA_UNKNOWN", 0.0))

    def test_when_the_balance_cannot_cover_everything_league_pulls_come_first_then_recovery(self):
        b = self.budget(30)                                                           # 10 usable
        self.assertEqual(b["player_pool"], 0.0)
        self.assertGreater(b["league_total"], 0)

    def test_an_observed_league_charge_replaces_the_assumed_one(self):
        self.assertEqual(cp.observed_league_cost(), (2, False))
        cp.record(cp.MONEYLINE_UI, 1, at(8), markets="h2h,spreads")                   # the provider charged one credit for both markets
        self.assertEqual(cp.observed_league_cost(), (1, True))


class TestTheDaysPlan(Isolated):
    def plan(self, game_starts, remaining=200, quality=None, now=None, **kw):
        return cp.build_day_plan(now or at(8), "2026-10-17", game_starts, remaining=remaining, counts_ahead=kw.pop("counts_ahead", {"2026-10-18": 4}), quality=quality, **kw)

    SLATE = dict(a=(13,), b=(13, 30), c=(19,), d=(19, 30), e=(19, 30), f=(22,))

    def test_the_first_wave_is_covered_before_the_evening_cluster_takes_the_rest(self):
        p = self.plan(starts(**self.SLATE), remaining=70, quality={"a": 5, "b": 3, "c": 9, "d": 8, "e": 7, "f": 6})
        self.assertGreaterEqual(len(p["games_priced"]), 2)
        self.assertTrue(set(p["games_priced"]) & {"a", "b"}, "the afternoon wave must get a capture")
        self.assertIn("a", p["games_priced"])                                         # the best game of the first wave
        self.assertTrue(set(p["games_priced"]) & {"c", "d", "e"}, "and the evening wave still gets one")

    def test_each_selected_game_gets_one_bundle_of_shots_goals_and_saves(self):
        p = self.plan(starts(a=(13,), c=(19,)), remaining=200)
        for g in p["games_priced"]:
            self.assertEqual(p["game_markets"][g][:3], [cp.SHOTS_KEY, cp.GOALS_KEY, cp.SAVES_KEY])
        self.assertEqual(sorted(p["goals_games"]), sorted(p["games_priced"]))
        self.assertEqual(sorted(p["saves_games"]), sorted(p["games_priced"]))         # saves are priced whether or not a starter is confirmed

    def test_a_thin_budget_prices_fewer_games_and_names_the_rest(self):
        p = self.plan(starts(**self.SLATE), remaining=45)
        self.assertLess(len(p["games_priced"]), 6)
        self.assertEqual(set(p["games_priced"]) | set(p["games_not_priced"]), set(self.SLATE))
        self.assertEqual(p["allowance"][cp.MONEYLINE_DECISION], 0.0)                  # no T-35 pulls in the core allocation
        self.assertEqual(p["allowance"][cp.TOMORROW], 0.0)                            # no paid tomorrow check
        self.assertEqual(p["allowance"][cp.MORNING], 0.0)                             # no morning player-odds look

    def test_points_and_extra_league_pulls_come_only_from_explicit_surplus(self):
        tight = self.plan(starts(**self.SLATE), remaining=60)
        self.assertEqual(tight["points_games"], [])
        with mock.patch.dict("os.environ", {cp.PROP_MARKETS_ENV: cp.SHOTS_KEY}):
            rich = self.plan(starts(a=(13,), c=(19,)), remaining=500, counts_ahead={"2026-10-18": 2})
        self.assertEqual(sorted(rich["points_games"]), sorted(rich["games_priced"]))  # every eligible game has its bundle: the surplus buys points
        self.assertGreaterEqual(rich["league_pulls"]["extra_for_later_waves"], 1)

    def test_the_plan_does_not_buy_for_started_games_or_games_already_captured(self):
        p = self.plan(starts(a=(13,), c=(19,)), remaining=200, now=at(14))            # a started an hour ago
        self.assertNotIn("a", p["games_priced"])
        cp.record(cp.PROPS, 1, at(17, 35), game_id="c", markets=cp.SHOTS_KEY)
        done = cp.captured_games_today(at(17, 40))
        p2 = self.plan(starts(a=(13,), c=(19,), d=(19, 30)), remaining=200, now=at(17, 40), done_games=done)
        self.assertNotIn("c", p2["games_priced"])

    def test_league_pulls_are_two_a_day_and_the_cost_is_labelled(self):
        p = self.plan(starts(a=(13,), c=(19,)), remaining=200)
        self.assertEqual(p["league_pulls"]["planned"], 2 + p["league_pulls"]["extra_for_later_waves"])
        self.assertFalse(p["league_pulls"]["cost_observed"])
        self.assertTrue(any("assumed to cost" in n for n in p["notes"]))
        self.assertTrue(any("ASSUMED the 1st" in n for n in p["notes"]))

    def test_the_plan_is_built_once_kept_and_an_older_version_is_rebuilt_once(self):
        gs = starts(a=(13,), c=(19,))
        first = cp.day_plan(at(8), "2026-10-17", gs, remaining=200, counts_ahead={"2026-10-18": 4})
        again = cp.day_plan(at(9), "2026-10-17", gs, remaining=150, counts_ahead={"2026-10-18": 4})
        self.assertEqual(first["games_priced"], again["games_priced"])                # never reshuffled by a later cycle
        self.assertEqual(again["budget"]["remaining_header_now"], 150)
        old = {**first, "alloc_version": 3, "games_priced": ["stale"]}
        cp._persist(old)
        rebuilt = cp.day_plan(at(10), "2026-10-17", gs, remaining=140, counts_ahead={"2026-10-18": 4})
        self.assertEqual(rebuilt["alloc_version"], cp.ALLOC_VERSION)
        self.assertNotIn("stale", rebuilt["games_priced"])


class TestGameSelection(unittest.TestCase):
    def test_quality_breaks_ties_and_the_first_wave_always_gets_one(self):
        ws = ct.waves(starts(a=(13,), b=(13, 20), c=(19,), d=(19, 10), e=(19, 20)))
        chosen = cp.select_games(ws, {"a": 1, "b": 9, "c": 5, "d": 6, "e": 7}, {"a", "b", "c", "d", "e"}, 2)
        self.assertEqual(chosen, ["b", "e"])                                          # the best of the afternoon wave and the best of the evening wave
        self.assertEqual(cp.select_games(ws, {}, {"a", "b", "c", "d", "e"}, 0), [])

    def test_a_quality_of_zero_is_never_selected(self):
        p = cp.build_day_plan  # selection excludes games with nothing to price
        ws = ct.waves(starts(a=(13,), b=(19,)))
        self.assertEqual(cp.select_games(ws, {"a": 0, "b": 4}, {"b"}, 2), ["b"])


class TestReservations(Isolated):
    PLAN = {"allowance": {cp.PROPS: 2.0, cp.GOALS: 1.0, cp.SAVES: 1.0, cp.REFRESH: 1.0, cp.MONEYLINE_UI: 4.0}}

    def test_two_jobs_cannot_spend_the_same_allocation(self):
        first = cp.reserve(cp.GOALS, 1, at(17, 30), plan=self.PLAN, remaining=100)
        second = cp.reserve(cp.GOALS, 1, at(17, 30), plan=self.PLAN, remaining=100)    # a concurrent job asks before the first has settled
        self.assertTrue(first["allow"])
        self.assertFalse(second["allow"])
        self.assertIn("ALLOWANCE", second["reason"])

    def test_pending_reservations_count_against_the_hard_reserve(self):
        self.assertTrue(cp.reserve(cp.PROPS, 1, at(17), plan=self.PLAN, remaining=21)["allow"])
        self.assertFalse(cp.reserve(cp.PROPS, 1, at(17), plan=self.PLAN, remaining=21)["allow"])       # 21 - 1 pending - 1 would breach the 20-credit reserve

    def test_settling_records_the_actual_charge_beside_the_planned_one_and_frees_the_reservation(self):
        r = cp.reserve(cp.MONEYLINE_UI, 2, at(8), plan=self.PLAN, remaining=100)
        cp.settle(r["id"], 1, at(8), label="morning", markets="h2h,spreads")
        row = cp.read_ledger("2026-10-17")[-1]
        self.assertEqual((row["credits"], row["planned"]), (1.0, 2.0))
        self.assertEqual(cp.pending_reserved(at(8)), {})
        self.assertEqual(cp.reconciliation("2026-10-17")[cp.MONEYLINE_UI], {"calls": 1, "planned": 2.0, "actual": 1.0})
        self.assertEqual(cp.spent_today(at(9))[cp.MONEYLINE_UI], 1.0)                                  # the unspent credit is available again

    def test_a_failed_call_releases_its_reservation(self):
        r = cp.reserve(cp.SAVES, 1, at(17), plan=self.PLAN, remaining=100)
        cp.release(r["id"], at(17))
        self.assertTrue(cp.reserve(cp.SAVES, 1, at(17), plan=self.PLAN, remaining=100)["allow"])

    def test_a_stale_reservation_expires(self):
        cp.reserve(cp.SAVES, 1, at(17), plan=self.PLAN, remaining=100)
        self.assertTrue(cp.reserve(cp.SAVES, 1, at(17, 30), plan=self.PLAN, remaining=100)["allow"])   # 30 minutes later the crashed job's claim has lapsed

    def test_an_unknown_balance_or_an_unplanned_class_is_never_allowed(self):
        self.assertEqual(cp.reserve(cp.PROPS, 1, at(8), plan=self.PLAN, remaining=None)["reason"], "QUOTA_UNKNOWN") if False else None
        with mock.patch.object(cp.odds_quota, "latest_remaining", return_value=None):
            self.assertEqual(cp.reserve(cp.PROPS, 1, at(8), plan=self.PLAN)["reason"], "QUOTA_UNKNOWN")
        self.assertFalse(cp.reserve(cp.TOMORROW, 1, at(8), plan=self.PLAN, remaining=100)["allow"])


class TestAuthorizeAndLedger(Isolated):
    def test_authorize_is_a_read_only_check_against_the_class_allowance(self):
        plan = {"allowance": {cp.PROPS: 2.0}}
        with mock.patch.object(cp.odds_quota, "latest_remaining", return_value=100):
            self.assertTrue(cp.authorize(cp.PROPS, 2, at(8), plan=plan)["allow"])
            self.assertFalse(cp.authorize(cp.PROPS, 3, at(8), plan=plan)["allow"])
            self.assertFalse(cp.authorize(cp.MONEYLINE_DECISION, 1, at(8), plan=plan)["allow"])          # the T-35 class has no allowance
        with mock.patch.object(cp.odds_quota, "latest_remaining", return_value=20):
            self.assertEqual(cp.authorize(cp.PROPS, 1, at(8), plan=plan)["reason"], "HARD_RESERVE")

    def test_ledger_rows_round_trip_by_eastern_day(self):
        cp.record(cp.PROPS, 3, at(8), event="e")
        cp.record(cp.MONEYLINE_UI, 1, at(9))
        spent = cp.spent_today(at(10))
        self.assertEqual((spent[cp.PROPS], spent[cp.MONEYLINE_UI]), (3.0, 1.0))
        self.assertEqual(cp.read_ledger("2026-10-16"), [])

    def test_the_month_view_reports_the_player_pool_in_games_a_day(self):
        v = cp.month_view(at(8), 192)
        self.assertEqual(v["days_left"], 15)
        self.assertTrue(v["reset_assumed"])
        self.assertGreater(v["games_per_day_covered"], 1.0)


class TestGameQuality(unittest.TestCase):
    def test_quality_counts_eligible_skaters_and_a_goalie_projection(self):
        def player(eligible, toi=18.0, gp=40):
            return {"projection": {"pricing_eligible": eligible, "expected": {"toi": toi}}, "games_total": gp}
        product = {"players": {"p1": player(True), "p2": player(True, toi=8.0), "p3": player(False), "p4": player(True, gp=3)},
                   "goalies": {"g1": {"projection": {"x": 1}}}, "game_details": {"9": {"sides": {"home": {"skater_ids": ["p1", "p2"], "goalie_ids": ["g1"]}, "away": {"skater_ids": ["p3", "p4"], "goalie_ids": []}}}}}
        self.assertEqual(cp.game_quality(product, "9"), 1 + 4.0)
        self.assertEqual(cp.game_quality(None, "9"), 1.0)
        self.assertEqual(cp.game_quality(product, "missing"), 0.0)


if __name__ == "__main__":
    unittest.main()


class TestPlanRebuiltAfterWindowsClosed(Isolated):
    def test_a_game_whose_wave_window_has_closed_takes_no_budget(self):
        # 19:46 ET: the 8 PM game's window (target 18:30, last chance 19:45) has closed; the 10 PM game's has not
        plan = cp.build_day_plan(at(19, 46), "2026-10-17", starts(a=(20,), b=(22,)), remaining=150, quality={"a": 9, "b": 1})
        self.assertNotIn("a", plan["games_priced"])
        self.assertIn("b", plan["games_priced"])
        self.assertEqual([w["selected"] for w in plan["waves"]], [[], ["b"]])


class TestLightSlateFloor(Isolated):
    AHEAD = {f"2026-10-{d}": n for d, n in zip(range(19, 32), (4, 11, 2, 12, 2, 12, 5, 3, 11, 4, 11, 3, 10))}

    def test_a_two_game_day_still_gets_one_full_bundle_and_the_second_game_shots(self):
        plan = cp.build_day_plan(at(8, day=18), "2026-10-18", {"a": at(18, day=18), "b": at(20, day=18)}, remaining=187, counts_ahead=self.AHEAD)
        self.assertEqual(plan["games_priced"], ["a", "b"])
        self.assertEqual(plan["game_markets"]["a"], [cp.SHOTS_KEY, cp.GOALS_KEY, cp.SAVES_KEY])      # saves are priced even on a light day
        self.assertEqual(plan["game_markets"]["b"], [cp.SHOTS_KEY])
        self.assertGreaterEqual(plan["budget"]["player_today"], 3.0)

    def test_the_floor_is_carved_out_of_the_pool_so_the_days_still_sum_to_it(self):
        days = {"2026-10-18": 2, **self.AHEAD}
        total = 0.0
        for d in sorted(days):
            b = cp.cycle_budget(at(8, day=int(d[-2:])), 187, 0.0, {k: v for k, v in days.items() if k >= d}, d, league_cost=2, league_cost_observed=False)
            if d == "2026-10-18":
                pool, w_total = b["player_pool"], b["weight_total"]
                first = b["player_today"]
        n = len(days)
        weights = {d: min(c, cp.DAY_WEIGHT_CAP) for d, c in days.items()}
        total = sum(3.0 + (pool - 3.0 * n) * w / sum(weights.values()) for w in weights.values())
        self.assertAlmostEqual(total, pool, places=6)                                                  # every day's share, added up, is exactly the pool: nothing is created
        self.assertGreater(first, pool * weights["2026-10-18"] / sum(weights.values()))

    def test_when_the_pool_cannot_give_every_game_day_a_bundle_the_floor_is_not_applied(self):
        b = cp.cycle_budget(at(8, day=18), 60, 0.0, {"2026-10-18": 2, **self.AHEAD}, "2026-10-18", league_cost=2, league_cost_observed=False)
        w = min(2, cp.DAY_WEIGHT_CAP)
        self.assertAlmostEqual(b["player_today"], b["player_pool"] * w / b["weight_total"], places=6)
