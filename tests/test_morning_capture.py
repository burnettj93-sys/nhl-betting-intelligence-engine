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


    def test_tomorrow_is_checked_twice_a_day_each_time_once(self):
        self.assertFalse(sched.tomorrow_check_due(at(7, 0), None))
        self.assertTrue(sched.tomorrow_check_due(at(8, 12), None))
        self.assertFalse(sched.tomorrow_check_due(at(9, 0), at(8, 13)))
        self.assertFalse(sched.tomorrow_check_due(at(19, 0), at(8, 13)))
        self.assertTrue(sched.tomorrow_check_due(at(20, 20), at(8, 13)))
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

    def test_repeating_the_same_plan_statement_keeps_the_time_it_was_first_made(self):
        t1, t2 = dt.datetime(2026, 10, 9, 13, 0, tzinfo=U), dt.datetime(2026, 10, 9, 19, 0, tzinfo=U)
        pa.note_unfetched("2099-01-02", "g1", "m", pa.BUDGET_BLOCKED, t1, detail="no credits")
        pa.note_unfetched("2099-01-02", "g1", "m", pa.BUDGET_BLOCKED, t2, detail="no credits")
        self.assertEqual(pa.read("2099-01-02")["games"]["g1"]["markets"]["m"]["checked_utc"], "2026-10-09T13:00:00Z")

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


class TestTomorrowCheck(IsolatedState):
    EVENT = {"id": "t1", "home_team": "Boston Bruins", "away_team": "Utah Mammoth", "commence_time": "2026-10-10T23:10:00Z"}

    def check(self, now, client, spent=None, allowance=0.4):
        plan = {"day": "2026-10-09", "allowance": {cp.TOMORROW: allowance}, "budget": {"D": 11.4}}
        with mock.patch.object(cp, "record") as rec, mock.patch.object(cp, "spent_today", return_value=spent or {}), mock.patch.object(cp, "enforced", return_value=True), \
                mock.patch.object(cp.odds_quota, "latest_remaining", return_value=280):
            out = bb.check_tomorrow(now, client=client, archive_mod=mock.Mock(), plan=plan, games={"9": {"home": "BOS", "away": "UTA", "start_utc": "2026-10-10T23:10:00Z"}})
        return out, rec

    def test_not_due_before_the_first_check_of_the_day(self):
        out, _ = self.check(at(7, 0), FakeClient(self.EVENT, {"bookmakers": []}, 0))
        self.assertEqual(out["reason"], "NOT_DUE")

    def test_nothing_posted_is_recorded_as_not_posted_and_costs_nothing_even_when_the_day_has_no_tomorrow_allowance(self):
        client = FakeClient(self.EVENT, {"bookmakers": []}, 0)
        out, rec = self.check(at(20, 20), client)
        self.assertEqual((out["ran"], out["checked"], out["posted"]), (True, 1, 0))
        self.assertEqual(client.calls, [",".join([bb.SOG_MARKET_KEY, bb.POINTS_MARKET_KEY, bb.GOALS_MARKET_KEY, "player_total_saves"])])      # the earliest game: every market we price
        mk = pa.read("2026-10-10")["games"]["9"]["markets"]
        self.assertEqual({v["status"] for v in mk.values()}, {pa.NOT_POSTED})
        self.assertEqual(len(mk), 4)                                                    # each market has its own answer and time
        obs = pa.observations("2026-10-10")
        self.assertEqual(len(obs), 4)
        self.assertTrue(all(o["status"] == "NOT_POSTED" and o["hours_to_start"] is not None for o in obs))
        rec.assert_not_called()
        self.assertIsNotNone(pa.tomorrow_last_check())
        out2, _ = self.check(at(21, 0), FakeClient(self.EVENT, {"bookmakers": []}, 0))            # not asked twice in the same slot
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
