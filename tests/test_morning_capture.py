"""The morning first look, the midday look, the pregame capture; and the record of why a price is or is not on file."""
from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from operational import best_bets as bb
from operational import capture_schedule as sched
from operational import credit_planner as cp
from operational import price_availability as pa

U = dt.timezone.utc
START = dt.datetime(2026, 10, 9, 23, 10, tzinfo=U)          # 7:10 PM ET


def at(h, m=0):
    """An ET wall-clock time on 2026-10-09 as UTC."""
    return dt.datetime(2026, 10, 9, h, m, tzinfo=dt.timezone(dt.timedelta(hours=-4))).astimezone(U)


class TestSlots(unittest.TestCase):
    MK = ["shots", "points"]

    def test_nothing_before_the_morning_update_and_the_morning_slot_opens_at_8(self):
        self.assertEqual(sched.decide(at(7, 30), START, {}, self.MK), (None, "BEFORE_MORNING_UPDATE"))
        self.assertEqual(sched.decide(at(8, 1), START, {}, self.MK)[0], sched.MORNING)

    def test_an_eleven_hour_lead_is_allowed(self):
        """The old policy refused anything more than five hours before puck drop; 8 AM for a 7:10 PM game is eleven hours."""
        self.assertGreater((START - at(8, 1)).total_seconds() / 3600, 10.9)
        self.assertEqual(sched.decide(at(8, 1), START, {}, self.MK)[0], sched.MORNING)

    def test_a_morning_capture_satisfies_the_slot_until_midday(self):
        cap = {"shots": at(8, 5), "points": at(8, 5)}
        self.assertEqual(sched.decide(at(9, 0), START, cap, self.MK), (None, "MORNING_SLOT_SATISFIED"))
        self.assertEqual(sched.decide(at(12, 45), START, cap, self.MK)[0], sched.MIDDAY)          # the 12:30 look is due again
        cap2 = {"shots": at(12, 40), "points": at(12, 40)}
        self.assertEqual(sched.decide(at(13, 0), START, cap2, self.MK), (None, "MIDDAY_SLOT_SATISFIED"))

    def test_a_market_not_yet_posted_is_asked_again_after_an_hour(self):
        asked = {"shots": at(8, 5), "points": at(8, 5)}
        self.assertEqual(sched.decide(at(8, 40), START, {}, self.MK, asked), (None, "MORNING_SLOT_SATISFIED"))
        self.assertEqual(sched.decide(at(9, 10), START, {}, self.MK, asked)[0], sched.MORNING)

    def test_pregame_window_takes_over_and_morning_closes_three_hours_out(self):
        self.assertEqual(sched.decide(at(17, 30), START, {}, self.MK)[0], sched.PREGAME)         # 100 min before 7:10 PM
        self.assertEqual(sched.decide(at(16, 30), START, {}, self.MK), (None, "BETWEEN_SLOTS"))  # morning/midday closed at 4:10 PM, pregame opens 5:25 PM

    def test_an_early_game_has_no_morning_slot(self):
        early = at(10, 30)                       # a 10:30 AM start: the morning slot would close at 7:30 AM
        self.assertNotIn(sched.MORNING, sched.windows(at(8, 0), early))

    def test_started_game(self):
        self.assertEqual(sched.decide(at(19, 30), START, {}, self.MK), (None, "STARTED"))

    def test_pregame_hours_match_the_planner(self):
        self.assertEqual(sched.PREGAME_HOURS, cp.FIRST_CAPTURE_HOURS)

    def test_tomorrow_check_once_in_the_evening(self):
        self.assertFalse(sched.tomorrow_check_due(at(19, 0), None))
        self.assertTrue(sched.tomorrow_check_due(at(20, 20), None))
        self.assertFalse(sched.tomorrow_check_due(at(21, 0), at(20, 21)))


class IsolatedState(unittest.TestCase):
    """Every test gets its own state directory, so one test's availability record never satisfies another's slot."""
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(pa.state_paths, "path", side_effect=lambda name, **kw: Path(self._tmp.name) / name)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)


class TestAvailabilityRecord(IsolatedState):
    def test_posted_and_not_posted_are_told_apart_and_empty_costs_nothing_to_record(self):
        payload = {"bookmakers": [{"key": "draftkings", "markets": [{"key": "player_points", "last_update": "2026-10-09T12:00:00Z", "outcomes": [{"name": "Over"}] * 3}]}]}
        got = pa.classify_payload(payload, ["player_points", "player_shots_on_goal_alternate"])
        self.assertEqual(got["player_points"]["status"], pa.POSTED)
        self.assertEqual(got["player_points"]["outcomes"], 3)
        self.assertEqual(got["player_shots_on_goal_alternate"]["status"], pa.NOT_POSTED)
        self.assertEqual(pa.classify_payload({"bookmakers": []}, ["m"])["m"]["status"], pa.NOT_POSTED)
        self.assertEqual(pa.classify_payload(None, ["m"])["m"]["status"], pa.NOT_POSTED)

    def test_a_plan_statement_never_overwrites_a_real_provider_answer(self):
        now = dt.datetime(2026, 10, 9, 13, 0, tzinfo=U)
        pa.record("2099-01-01", "g1", "m", pa.NOT_POSTED, now, hours_to_start=11)
        pa.note_unfetched("2099-01-01", "g1", "m", pa.BUDGET_BLOCKED, now)
        self.assertEqual(pa.read("2099-01-01")["games"]["g1"]["markets"]["m"]["status"], pa.NOT_POSTED)
        pa.note_unfetched("2099-01-01", "g1", "other", pa.BUDGET_BLOCKED, now, detail="no credits")
        self.assertEqual(pa.read("2099-01-01")["games"]["g1"]["markets"]["other"]["status"], pa.BUDGET_BLOCKED)
        pa.record("2099-01-01", "g1", "m", pa.FETCH_ERROR, now)

    def test_lead_hours_summarise_how_early_a_market_was_seen(self):
        rows = [{"market": "a", "status": pa.POSTED, "hours_to_start": 9.0}, {"market": "a", "status": pa.POSTED, "hours_to_start": 5.0},
                {"market": "a", "status": pa.NOT_POSTED, "hours_to_start": 30.0}, {"market": "b", "status": pa.NOT_POSTED, "hours_to_start": 11.0}]
        lead = pa.posting_lead_hours(rows)
        self.assertEqual(lead["a"]["posted_earliest_h"], 9.0)
        self.assertEqual(lead["b"]["posted_earliest_h"], None)


class FakeClient:
    def __init__(self, event, payload, cost):
        self.event, self.payload, self.cost, self.calls = event, payload, cost, []

    def get_nhl_events(self):
        return mock.Mock(ok=True, data=[self.event])

    def get_event_odds(self, eid, markets):
        self.calls.append(markets)
        return mock.Mock(ok=True, requests_last=self.cost, data=self.payload, error=None)


class TestCaptureUnderThePlan(IsolatedState):
    EVENT = {"id": "e1", "home_team": "Boston Bruins", "away_team": "Utah Mammoth", "commence_time": "2026-10-09T23:10:00Z"}
    GAMES = {"1": {"home": "BOS", "away": "UTA", "start_utc": "2026-10-09T23:10:00Z"}}
    PLAN = {"day": "2026-10-09", "games_priced": ["1"], "games_not_priced": [], "goals_games": [], "morning_games": ["1"], "morning_markets": [cp.SHOTS_KEY],
            "allowance": {cp.PROPS: 2.0, cp.MORNING: 1.0, cp.GOALS: 0.0, cp.REFRESH: 0.0}, "budget": {"D": 11.5}}

    def run_capture(self, now, client, plan=None, captured=None):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(cp, "record") as rec, mock.patch.object(cp, "spent_today", return_value={}), \
                mock.patch.object(cp.odds_quota, "latest_remaining", return_value=280), mock.patch.object(bb, "_archive_dir", return_value=Path(tmp)), \
                mock.patch.object(bb, "latest_capture", return_value=None):
            out = bb.capture_prices(now, client=client, archive_mod=mock.Mock(), games=self.GAMES, plan=plan or self.PLAN)
        return out, rec

    def test_the_morning_look_buys_the_planned_morning_market_eleven_hours_before_puck_drop(self):
        client = FakeClient(self.EVENT, {"bookmakers": [{"key": "draftkings", "markets": [{"key": cp.SHOTS_KEY, "outcomes": [{"name": "Over"}] * 40}]}]}, 1)
        out, rec = self.run_capture(at(8, 5), client)
        self.assertEqual(client.calls, [cp.SHOTS_KEY])
        self.assertEqual([(c.args[0], c.args[1]) for c in rec.call_args_list], [(cp.MORNING, 1)])
        self.assertEqual(out["slots"]["e1"]["slot"], sched.MORNING)
        st = pa.read("2026-10-09")["games"]["1"]["markets"][cp.SHOTS_KEY]
        self.assertEqual((st["status"], st["outcomes"]), (pa.POSTED, 40))

    def test_nothing_posted_costs_nothing_and_is_recorded_as_not_posted_not_as_a_capture(self):
        client = FakeClient(self.EVENT, {"bookmakers": []}, 0)
        out, rec = self.run_capture(at(8, 5), client)
        self.assertEqual([(c.args[0], c.args[1]) for c in rec.call_args_list], [(cp.MORNING, 0)])
        self.assertEqual(pa.read("2026-10-09")["games"]["1"]["markets"][cp.SHOTS_KEY]["status"], pa.NOT_POSTED)

    def test_a_game_outside_the_morning_plan_is_budget_blocked_not_unposted(self):
        plan = {**self.PLAN, "morning_games": []}
        client = FakeClient(self.EVENT, {}, 0)
        out, rec = self.run_capture(at(8, 5), client, plan=plan)
        self.assertEqual(client.calls, [])
        st = pa.read("2026-10-09")["games"]["1"]["markets"]
        self.assertEqual({v["status"] for v in st.values()}, {pa.BUDGET_BLOCKED})

    def test_before_the_morning_update_the_game_is_just_not_fetched_yet(self):
        client = FakeClient(self.EVENT, {}, 0)
        self.run_capture(at(6, 0), client)
        self.assertEqual(client.calls, [])
        self.assertEqual({v["status"] for v in pa.read("2026-10-09")["games"]["1"]["markets"].values()}, {pa.NOT_FETCHED})

    def test_a_missed_first_look_is_made_late_as_the_first_look_not_a_second_one(self):
        self.assertEqual(sched.decide(at(13, 0), START, {}, ["shots"]), (sched.MORNING, "SLOT_OPEN_NOT_CAPTURED"))
        client = FakeClient(self.EVENT, {"bookmakers": [{"key": "draftkings", "markets": [{"key": cp.SHOTS_KEY, "outcomes": [{"name": "Over"}] * 40}]}]}, 1)
        out, rec = self.run_capture(at(13, 0), client)
        self.assertEqual([(c.args[0], c.args[1]) for c in rec.call_args_list], [(cp.MORNING, 1)])        # the morning allowance pays for it

    def test_midday_look_only_with_leftover_credits(self):
        morning = {cp.SHOTS_KEY: at(8, 5), cp.POINTS_KEY: at(8, 5)}
        client = FakeClient(self.EVENT, {"bookmakers": []}, 0)
        with mock.patch.object(bb, "_captured_times", return_value=morning):
            out, _ = self.run_capture(at(12, 45), client)
        self.assertEqual(client.calls, [])
        self.assertEqual(out["slots"]["e1"]["why"], "NO_CREDITS_FOR_MIDDAY")
        rich = {**self.PLAN, "allowance": {**self.PLAN["allowance"], cp.REFRESH: 4.0}}
        client2 = FakeClient(self.EVENT, {"bookmakers": []}, 0)
        with mock.patch.object(bb, "_captured_times", return_value=morning):
            self.run_capture(at(12, 45), client2, plan=rich)
        self.assertEqual(len(client2.calls), 1)

    def test_pregame_capture_still_happens_in_its_window_and_a_morning_price_does_not_count_as_it(self):
        client = FakeClient(self.EVENT, {"bookmakers": []}, 0)
        out, rec = self.run_capture(at(17, 30), client)
        self.assertEqual(out["slots"]["e1"]["slot"], sched.PREGAME)
        self.assertEqual(out["slots"]["e1"]["decision"], "FIRST")
        self.assertEqual(len(client.calls), 1)


class TestTomorrowCheck(IsolatedState):
    EVENT = {"id": "t1", "home_team": "Boston Bruins", "away_team": "Utah Mammoth", "commence_time": "2026-10-10T23:10:00Z"}

    def check(self, now, client, spent=None, allowance=0.4):
        plan = {"day": "2026-10-09", "allowance": {cp.TOMORROW: allowance}, "budget": {"D": 11.4}}
        with mock.patch.object(cp, "record") as rec, mock.patch.object(cp, "spent_today", return_value=spent or {}), mock.patch.object(cp, "enforced", return_value=True), \
                mock.patch.object(cp.odds_quota, "latest_remaining", return_value=280):
            out = bb.check_tomorrow(now, client=client, archive_mod=mock.Mock(), plan=plan, games={"9": {"home": "BOS", "away": "UTA", "start_utc": "2026-10-10T23:10:00Z"}})
        return out, rec

    def test_not_due_before_the_evening(self):
        out, _ = self.check(at(15, 0), FakeClient(self.EVENT, {"bookmakers": []}, 0))
        self.assertEqual(out["reason"], "NOT_DUE")

    def test_nothing_posted_is_recorded_as_not_posted_and_costs_nothing_even_when_the_day_has_no_tomorrow_allowance(self):
        client = FakeClient(self.EVENT, {"bookmakers": []}, 0)
        out, rec = self.check(at(20, 20), client)
        self.assertEqual((out["ran"], out["checked"], out["posted"]), (True, 1, 0))
        self.assertEqual(client.calls, [cp.SHOTS_KEY if hasattr(cp, "SHOTS_KEY") else bb.SOG_MARKET_KEY])
        st = pa.read("2026-10-10")["games"]["9"]["markets"][bb.SOG_MARKET_KEY]
        self.assertEqual(st["status"], pa.NOT_POSTED)
        rec.assert_not_called()
        self.assertIsNotNone(pa.tomorrow_last_check())
        out2, _ = self.check(at(21, 0), FakeClient(self.EVENT, {"bookmakers": []}, 0))            # not asked twice the same evening
        self.assertEqual(out2["reason"], "NOT_DUE")

    def test_a_posted_market_is_paid_for_once_from_the_tomorrow_class_then_later_games_are_budget_blocked(self):
        payload = {"bookmakers": [{"key": "draftkings", "markets": [{"key": bb.SOG_MARKET_KEY, "outcomes": [{"name": "Over"}] * 50}]}]}
        out, rec = self.check(at(20, 20), FakeClient(self.EVENT, payload, 1))
        self.assertEqual(out["posted"], 1)
        self.assertEqual([(c.args[0], c.args[1]) for c in rec.call_args_list], [(cp.TOMORROW, 1)])
        pa.mark_tomorrow_checked(at(0, 0))
        out2, rec2 = self.check(at(20, 30), FakeClient(self.EVENT, payload, 1), spent={cp.TOMORROW: 1.0})
        self.assertEqual(out2["checked"], 0)
        self.assertEqual(pa.read("2026-10-10")["games"]["9"]["markets"][bb.SOG_MARKET_KEY]["status"], pa.POSTED)    # a real answer is not overwritten by the budget note


class TestCapturedTimesIgnoreEmptyAnswers(unittest.TestCase):
    def test_an_empty_answer_is_not_a_capture_of_the_market(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for name, ts, resp in (("a", "2026-10-09T12:00:00Z", {"bookmakers": []}),
                                   ("b", "2026-10-09T13:00:00Z", {"bookmakers": [{"key": "draftkings", "markets": [{"key": "m1", "outcomes": [{"x": 1}]}]}]})):
                (d / f"20261009T{name}-events-abc-odds.json").write_text(json.dumps({"meta": {"market_filter": "m1,m2", "retrieved_at_utc": ts}, "response": resp}))
            got = bb._captured_times("abc", ["m1", "m2"], archive_dir=d)
        self.assertEqual(got["m1"], dt.datetime(2026, 10, 9, 13, 0, tzinfo=U))
        self.assertIsNone(got["m2"])


if __name__ == "__main__":
    unittest.main()
