"""Coordinated wave captures: when, what, how many credits (reserved, then settled to the actual charge), and what a missed window records."""
from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from operational import best_bets as bb
from operational import credit_planner as cp
from operational import price_availability as pa

U = dt.timezone.utc
ET = dt.timezone(dt.timedelta(hours=-4))
DAY = "2026-10-17"


def at(h, m=0):
    return dt.datetime(2026, 10, 17, h, m, tzinfo=ET).astimezone(U)


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


EVENTS = [{"id": "e1", "home_team": "Boston Bruins", "away_team": "Utah Mammoth", "start": (13,)},
          {"id": "e2", "home_team": "Toronto Maple Leafs", "away_team": "Montréal Canadiens", "start": (19,)},
          {"id": "e3", "home_team": "Edmonton Oilers", "away_team": "Calgary Flames", "start": (19, 20)}]
GAMES = {"1": {"home": "BOS", "away": "UTA", "start_utc": iso(at(13))}, "2": {"home": "TOR", "away": "MTL", "start_utc": iso(at(19))}, "3": {"home": "EDM", "away": "CGY", "start_utc": iso(at(19, 20))}}


def payload(**markets):
    """{market key: number of outcomes}"""
    return {"bookmakers": [{"key": "draftkings", "markets": [{"key": k, "outcomes": [{"name": "Over"}] * n} for k, n in markets.items() if n]}]}


FULL = payload(**{cp.SHOTS_KEY: 40, cp.GOALS_KEY: 30, cp.SAVES_KEY: 2})


class FakeClient:
    def __init__(self, answers=None, cost=None, ok=True):
        self.calls, self.answers, self.cost, self.ok = [], answers or {}, cost, ok

    def get_nhl_events(self):
        return mock.Mock(ok=True, data=[{"id": e["id"], "home_team": e["home_team"], "away_team": e["away_team"], "commence_time": iso(at(*e["start"]))} for e in EVENTS])

    def get_event_odds(self, eid, markets):
        self.calls.append((eid, markets))
        if not self.ok:
            return mock.Mock(ok=False, error="boom", data=None, requests_last=0)
        wanted = markets.split(",")
        data = self.answers.get(eid) or FULL
        got = [m for m in wanted if any(x["key"] == m and x.get("outcomes") for b in data["bookmakers"] for x in b["markets"])]
        last = self.cost if self.cost is not None else len(got)
        return mock.Mock(ok=True, data={"bookmakers": [{"key": "draftkings", "markets": [x for b in data["bookmakers"] for x in b["markets"] if x["key"] in wanted]}]},
                         requests_last=last, error=None)


class Isolated(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        self.dir = d
        for p in (mock.patch.object(cp.state_paths, "path", side_effect=lambda name, **kw: d / name),
                  mock.patch.object(cp.odds_quota, "reset_status", return_value={"status": "OWNER_VERIFICATION_REQUIRED", "reset_day": 1, "assumed": True, "invalid": False}),
                  mock.patch.object(cp.odds_quota, "latest_remaining", return_value=190),
                  mock.patch.object(bb, "_archive_dir", return_value=d / "archive")):
            p.start()
            self.addCleanup(p.stop)
        (d / "archive").mkdir()
        starts = {"1": at(13), "2": at(19), "3": at(19, 20)}
        self.plan = cp.build_day_plan(at(8), DAY, starts, remaining=52, counts_ahead={"2026-10-18": 4}, quality={"1": 5, "2": 7, "3": 4})

    def capture(self, now, client):
        with mock.patch.object(cp, "latest_remaining", create=True):
            return bb.capture_wave_prices(now, client=client, archive_mod=mock.Mock(), games=GAMES, plan=self.plan)


class TestTheCapture(Isolated):
    def test_the_plan_selected_the_first_game_and_one_evening_game(self):
        self.assertIn("1", self.plan["games_priced"])
        self.assertTrue(set(self.plan["games_priced"]) & {"2", "3"})

    def test_nothing_is_bought_before_the_waves_target(self):
        c = FakeClient()
        out = self.capture(at(11, 20), c)                             # the first wave's target is 11:30
        self.assertEqual(c.calls, [])
        self.assertEqual(out["credits_spent"], 0)

    def test_at_the_target_each_selected_game_gets_one_call_with_its_whole_bundle(self):
        c = FakeClient()
        out = self.capture(at(11, 35), c)
        self.assertEqual(c.calls, [("e1", f"{cp.SHOTS_KEY},{cp.GOALS_KEY},{cp.SAVES_KEY}")])      # one coordinated capture; the evening wave is not due yet
        self.assertEqual(out["credits_spent"], 3)
        rows = [r for r in cp.read_ledger(DAY) if r.get("game_id") == "1"]
        self.assertEqual(sorted((r["class"], r["credits"], r["planned"]) for r in rows), sorted([(cp.PROPS, 1.0, 1.0), (cp.GOALS, 1.0, 1.0), (cp.SAVES, 1.0, 1.0)]))
        st = pa.read(DAY)["games"]["1"]["markets"]
        self.assertEqual({st[k]["status"] for k in (cp.SHOTS_KEY, cp.GOALS_KEY, cp.SAVES_KEY)}, {pa.POSTED})
        self.assertTrue(out["wave_state"]["0"]["on_time"])
        self.assertEqual(cp.pending_reserved(at(11, 36)), {})          # every reservation was settled

    def test_a_market_already_captured_is_never_bought_again(self):
        c = FakeClient()
        self.capture(at(11, 35), c)
        c2 = FakeClient()
        out = self.capture(at(11, 50), c2)
        self.assertEqual(c2.calls, [])
        self.assertEqual(out["credits_spent"], 0)

    def test_a_late_capture_after_the_publish_deadline_is_made_and_marked_late(self):
        c = FakeClient()
        out = self.capture(at(12, 10), c)                             # the machine woke after the 12:00 deadline but before the 12:45 last chance
        self.assertEqual(len(c.calls), 1)
        self.assertFalse(out["wave_state"]["0"]["on_time"])
        self.assertTrue(out["slots"]["e1"]["late"])

    def test_nothing_is_bought_after_the_last_chance(self):
        c = FakeClient()
        out = self.capture(at(12, 50), c)
        self.assertEqual(c.calls, [])
        self.assertEqual(out["slots"]["wave0"]["state"], "CLOSED")

    def test_a_game_the_plan_did_not_select_is_never_captured(self):
        unselected = [g for g in ("2", "3") if g not in self.plan["games_priced"]]
        self.assertTrue(unselected)
        c = FakeClient()
        self.capture(at(17, 40), c)                                   # the evening wave's window
        bought = {eid for eid, _ in c.calls}
        for g in unselected:
            self.assertNotIn({"2": "e2", "3": "e3"}[g], bought)

    def test_a_market_not_posted_is_asked_again_after_twenty_minutes_and_only_for_that_market(self):
        c = FakeClient(answers={"e1": payload(**{cp.SHOTS_KEY: 40, cp.GOALS_KEY: 30})})            # saves not posted yet
        out = self.capture(at(11, 35), c)
        self.assertEqual(out["credits_spent"], 2)
        self.assertEqual(pa.read(DAY)["games"]["1"]["markets"][cp.SAVES_KEY]["status"], pa.NOT_POSTED)
        c2 = FakeClient(answers={"e1": payload(**{cp.SAVES_KEY: 2})})
        self.capture(at(11, 45), c2)
        self.assertEqual(c2.calls, [])                                                                 # ten minutes later: too soon
        out3 = self.capture(at(11, 58), c2)
        self.assertEqual(c2.calls, [("e1", cp.SAVES_KEY)])                                             # only the missing market, not the fresh ones
        self.assertEqual(out3["credits_spent"], 1)

    def test_a_denied_reservation_buys_nothing_and_says_budget(self):
        with mock.patch.object(cp.odds_quota, "latest_remaining", return_value=20):                    # at the hard reserve
            c = FakeClient()
            out = self.capture(at(11, 35), c)
        self.assertEqual(c.calls, [])
        self.assertTrue(out["skipped"])
        self.assertEqual(pa.read(DAY)["games"]["1"]["markets"][cp.SHOTS_KEY]["status"], pa.BUDGET_BLOCKED)

    def test_a_failed_request_releases_its_reservations_and_records_the_failure(self):
        c = FakeClient(ok=False)
        out = self.capture(at(11, 35), c)
        self.assertEqual(out["credits_spent"], 0)
        self.assertEqual(cp.pending_reserved(at(11, 36)), {})
        self.assertEqual(pa.read(DAY)["games"]["1"]["markets"][cp.SHOTS_KEY]["status"], pa.FETCH_ERROR)

    def test_the_actual_charge_not_the_assumed_one_lands_in_the_ledger(self):
        c = FakeClient(cost=4)                                                                         # the provider charged four credits for three markets
        out = self.capture(at(11, 35), c)
        self.assertEqual(out["credits_spent"], 4)
        rows = [r for r in cp.read_ledger(DAY) if r.get("game_id") == "1"]
        self.assertEqual(sum(r["credits"] for r in rows), 4.0)
        self.assertEqual(sum(r["planned"] for r in rows), 3.0)
        rec = cp.reconciliation(DAY)
        self.assertEqual(sum(v["actual"] for v in rec.values()) - sum(v["planned"] for v in rec.values()), 1.0)

    def test_two_cycles_at_once_do_not_spend_one_allocation_twice(self):
        # a concurrent job has already reserved the goals credit for this game's class allowance
        held = cp.reserve(cp.GOALS, float(self.plan["allowance"][cp.GOALS]), at(11, 34), plan=self.plan, remaining=190)
        self.assertTrue(held["allow"])
        c = FakeClient()
        self.capture(at(11, 35), c)
        asked = c.calls[0][1].split(",") if c.calls else []
        self.assertNotIn(cp.GOALS_KEY, asked)                                                          # that credit was not spent twice (the recovery pool is not for fresh markets either)


class TestMissedWindows(Isolated):
    def note(self, now, *, asleep=False, ran=None):
        ev = ({"evidence": "ASLEEP", "slept_minutes": 70.0, "intervals": [("11:30Z", "12:40Z")]} if asleep else {"evidence": "AWAKE", "slept_minutes": 0.0, "intervals": []})
        return bb.note_missed_pregame_windows(now, self.plan, GAMES, sleep_fn=lambda a, b: ev, ran_fn=lambda a, b: ran)

    def test_sleep_is_blamed_only_with_sleep_evidence(self):
        out = self.note(at(12, 50), asleep=True)
        self.assertEqual(out["recorded"], 1)
        mw = pa.missed_windows(DAY)
        self.assertEqual(mw[0]["tags"], ["MISSED_WINDOW", "MACHINE_ASLEEP"])
        self.assertIn("power log", mw[0]["detail"])
        self.assertIn("Nothing is bought after the window", mw[0]["detail"])

    def test_no_sleep_evidence_and_no_trader_run_is_an_observed_engine_outage(self):
        self.note(at(12, 50), asleep=False, ran=False)
        self.assertEqual(pa.missed_windows(DAY)[0]["tags"], ["MISSED_WINDOW", "ENGINE_OUTAGE_OBSERVED"])

    def test_a_running_trader_with_no_capture_is_unattributed_not_sleep(self):
        self.note(at(12, 50), asleep=False, ran=True)
        self.assertEqual(pa.missed_windows(DAY)[0]["tags"], ["MISSED_WINDOW"])

    def test_it_is_recorded_once_and_never_while_the_window_is_open_or_after_a_capture(self):
        self.assertEqual(self.note(at(12, 30), asleep=True)["recorded"], 0)                           # the window closes at 12:45
        self.note(at(12, 50), asleep=True)
        self.assertEqual(self.note(at(13, 5), asleep=True)["recorded"], 0)
        cp.record(cp.PROPS, 1, at(12, 55), game_id="2", markets=cp.SHOTS_KEY)
        self.assertEqual(pa.missed_windows(DAY)[0]["game_id"], "1")


if __name__ == "__main__":
    unittest.main()
