"""The hosted pages tell the truth about the morning workflow: provisional picks are labelled and cannot be added, the morning strip reports what happened, and Tomorrow
shows early prices with their age (never yesterday's as fresh) and says why a market has no price."""
from __future__ import annotations

import copy
import datetime as dt
import unittest

from dashboard import ui
from operational import price_availability as pa
from tests.product_fixture import GEN, snapshot
from tests.test_product_pages import FIXTURE_NOW, choose_log, run_page, text

U = dt.timezone.utc


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def availability(day, matchup, start, statuses, checked):
    return {"day": day, "games": {"2026020900": {"matchup": matchup, "start_utc": start, "markets": {
        k: {"status": v, "checked_utc": checked, "outcomes": 40 if v == pa.POSTED else 0, "detail": None} for k, v in statuses.items()}}}, "counts": {}, "lead_hours": {}}


def morning_snapshot(*, provisional=True):
    snap = snapshot()
    tk = snap["tickets"]
    tk["date_et"] = "2026-10-15"
    snap["product_meta"]["et_today"] = snap["product_games"]["et_today"] = "2026-10-15"
    for g in snap["product_games"]["games"]:
        if g["date_et"] == "2026-10-08":                                 # move the fixture's scheduled game to "today" (2026-10-15) and add one for "tomorrow"
            g["date_et"] = "2026-10-15"
            g["start_utc"] = "2026-10-15T23:00:00Z"
            tomorrow = copy.deepcopy(g)
            tomorrow.update(game_id="2026020901", date_et="2026-10-16", start_utc="2026-10-16T23:00:00Z", moneyline={
                "home": {"american": -130.0, "quote_captured_at_utc": iso(FIXTURE_NOW - dt.timedelta(hours=2)), "age_min": 120.0, "status": "ACTIVE"},
                "away": {"american": 110.0, "quote_captured_at_utc": iso(FIXTURE_NOW - dt.timedelta(hours=2)), "age_min": 120.0, "status": "ACTIVE"}, "provider": "DraftKings"})
    snap["product_games"]["games"].append(tomorrow)
    snap["product_games"]["dates"] = sorted({x["date_et"] for x in snap["product_games"]["games"]})
    snap["product_games"]["default_date"] = "2026-10-15"
    checked = iso(dt.datetime(2026, 10, 15, 12, 6, tzinfo=U))             # 8:06 AM ET
    tk["diagnostics"]["price_availability"] = {
        "schedule": {"morning_from_et": "08:00", "midday_from_et": "12:30", "pregame_minutes_before_puck_drop": 105, "tomorrow_checks_et": ["08:10", "20:15"]},
        "today": {**availability("2026-10-15", "BBB at AAA", "2026-10-15T23:00:00Z", {"player_shots_on_goal_alternate": pa.POSTED, "player_points": pa.NOT_POSTED}, checked),
                  "sentence": "DraftKings player prices are on file for 1 of 1 games."},
        "tomorrow": {**availability("2026-10-16", "BBB at AAA", "2026-10-16T23:00:00Z", {"player_shots_on_goal_alternate": pa.NOT_POSTED}, iso(dt.datetime(2026, 10, 15, 0, 30, tzinfo=U))),
                     "sentence": "DraftKings player prices are on file for 0 of 1 games; 1 not posted by DraftKings yet."},
        "tomorrow_last_check_utc": iso(dt.datetime(2026, 10, 15, 0, 30, tzinfo=U)),
        "tomorrow_observations": [{"at": iso(dt.datetime(2026, 10, 15, 0, 30, tzinfo=U)), "game": "BBB at AAA", "market": "player_shots_on_goal_alternate", "status": "NOT_POSTED", "outcomes": 0, "hours_to_start": 22.5}]}
    if provisional:
        for o in tk["options"]["options"]:
            o["provisional"] = True
            for l in o["legs"]:
                l["provisional"] = True
                l["quote_updated_utc"] = iso(dt.datetime(2026, 10, 15, 12, 5, tzinfo=U))
                l["retrieved_at_utc"] = iso(dt.datetime(2026, 10, 15, 12, 6, tzinfo=U))
        card = {"ticket_id": "TPROV1", "status": "PROVISIONAL", "recorded": False, "provisional": True, "origin": "AUTOMATIC", "stake": 10.0, "potential_return": 45.0, "potential_profit": 35.0,
                "combined_decimal": 4.5, "combined_american": 350, "hit_probability": 0.31, "rationale": "r", "recorded_at_utc": None, "event_start_utc": "2026-10-15T23:00:00Z", "result": None, "alerts": [],
                "legs": [{"label": "Test Skater One 2+ shots", "market_family": "PLAYER_SOG_ALTERNATE", "game_id": "2026020900", "participant_id": "P1", "participant_name": "Test Skater One",
                          "american_price": 150.0, "decimal_price": 2.5, "probability": 0.5, "team": "AAA", "opponent": "BBB", "game_start_utc": "2026-10-15T23:00:00Z", "provisional": True,
                          "quote_updated_utc": iso(dt.datetime(2026, 10, 15, 12, 5, tzinfo=U)), "retrieved_at_utc": iso(dt.datetime(2026, 10, 15, 12, 6, tzinfo=U))}]}
        tk["provisional"] = {"as_of_utc": GEN, "note": "Provisional: built from today's earlier look. Nothing here is recorded and no slot is used.", "provisional_legs": 2, "tickets": [card]}
    return snap


class TestMorningPages(unittest.TestCase):
    def test_today_reports_the_morning_update_and_lists_provisional_picks_as_not_recorded(self):
        at = run_page("21_Today.py", morning_snapshot())
        self.assertEqual(len(at.exception), 0, [str(e.value)[:300] for e in at.exception])
        t = text(at)
        self.assertIn("Morning update done.", t)
        self.assertIn("1 of 1 games looked at", t)
        self.assertIn("Provisional recommendations", t)
        self.assertIn("Provisional — not recorded.", t)
        self.assertIn("Not recorded", t)
        self.assertIn("PROVISIONAL — quoted", t)
        self.assertNotIn("STALE — quoted 5.0 h ago", t)            # a provisional price is not mislabelled stale inside its provisional limit

    def test_best_options_marks_aged_provisional_cards_and_offers_no_add_control(self):
        at = run_page("26_Player_Props.py", morning_snapshot(), setup=choose_log)
        self.assertEqual(len(at.exception), 0, [str(e.value)[:300] for e in at.exception])
        t = text(at)
        self.assertIn("Provisional.", t)
        self.assertIn("older than the freshness limit", t)
        self.assertFalse([b for b in at.button if (b.label or "").startswith("Add to ")], "no add control on an aged provisional option")

    def test_a_fresh_morning_option_can_be_added_to_a_personal_log(self):
        snap = morning_snapshot(provisional=False)
        for o in snap["tickets"]["options"]["options"]:
            o["early"] = True
            for l in o["legs"]:
                l["early"] = True
        at = run_page("26_Player_Props.py", snap, setup=choose_log)
        self.assertEqual(len(at.exception), 0, [str(e.value)[:300] for e in at.exception])
        t = text(at)
        self.assertIn("Morning price.", t)
        self.assertNotIn("Provisional.", t)
        self.assertTrue([b for b in at.button if b.label == "Add to Casey — $10.00"], "a fresh morning option is addable")

    def test_a_non_provisional_option_still_offers_its_add_control(self):
        at = run_page("26_Player_Props.py", morning_snapshot(provisional=False), setup=choose_log)
        self.assertEqual(len(at.exception), 0)
        self.assertNotIn("Provisional.", text(at))
        self.assertTrue([b for b in at.button if b.label == "Add to Casey — $10.00"])

    def test_a_provisional_price_past_its_provisional_limit_is_stale(self):
        old = ui.provisional_staleness("2026-10-14T20:00:00Z", FIXTURE_NOW)             # 21 hours old
        self.assertTrue(old["stale"])
        fresh = ui.provisional_staleness("2026-10-15T12:05:00Z", FIXTURE_NOW)
        self.assertFalse(fresh["stale"])

    def test_tomorrow_says_why_props_have_no_price_and_ages_the_early_moneyline(self):
        at = run_page("39_Tomorrow.py", morning_snapshot())
        self.assertEqual(len(at.exception), 0, [str(e.value)[:300] for e in at.exception])
        t = text(at)
        self.assertIn("1 not posted by DraftKings yet", t)
        self.assertIn("not a rule about DraftKings", t)
        rows = [r for df in at.dataframe for r in df.value.to_dict("records")]
        flat = " ".join(str(v) for r in rows for v in r.values())
        self.assertIn("Not posted by DraftKings yet", flat)
        self.assertIn("early price, quoted 2.0 h ago", flat)

    def test_an_early_price_from_yesterday_is_never_fresh(self):
        yesterday = ui.early_staleness("2026-10-14T23:30:00Z", FIXTURE_NOW)               # 17.6 h earlier and an earlier Eastern day
        self.assertTrue(yesterday["stale"])
        late_last_night = ui.early_staleness("2026-10-15T03:30:00Z", dt.datetime(2026, 10, 15, 8, 0, tzinfo=U))   # 11:30 PM ET the night before, only 4.5 h old
        self.assertTrue(late_last_night["stale"] and late_last_night["previous_day"])
        recent = ui.early_staleness("2026-10-15T15:00:00Z", FIXTURE_NOW)
        self.assertFalse(recent["stale"])

    def test_before_8_am_the_state_is_not_yet_and_late_with_nothing_looked_at_is_missed(self):
        games = morning_snapshot()["tickets"]["diagnostics"]["price_availability"]["today"]["games"]
        self.assertEqual(pa.morning_status_from(games, dt.datetime(2026, 10, 15, 11, 0, tzinfo=U), "2026-10-15")["state"], pa.MORNING_NOT_YET)
        self.assertEqual(pa.morning_status_from(games, dt.datetime(2026, 10, 15, 13, 0, tzinfo=U), "2026-10-15")["state"], pa.MORNING_DONE)
        late = {"2026020900": {"markets": {"m": {"status": pa.POSTED, "checked_utc": "2026-10-15T19:44:00Z"}}, "start_utc": "2026-10-15T23:00:00Z"}}
        self.assertEqual(pa.morning_status_from(late, dt.datetime(2026, 10, 15, 21, 0, tzinfo=U), "2026-10-15")["state"], pa.MORNING_LATE)       # a 3:44 PM first look is not the morning update
        blocked = {"2026020900": {"markets": {"m": {"status": pa.BUDGET_BLOCKED, "checked_utc": "2026-10-15T12:30:00Z"}}, "start_utc": "2026-10-15T23:00:00Z"}}
        self.assertEqual(pa.morning_status_from(blocked, dt.datetime(2026, 10, 15, 13, 0, tzinfo=U), "2026-10-15")["state"], pa.MORNING_BUDGET_ONLY)      # not asking is not a done morning
        empty = {"2026020900": {"markets": {"m": {"status": pa.NOT_FETCHED, "checked_utc": "2026-10-15T10:00:00Z"}}, "start_utc": "2026-10-15T23:00:00Z"}}
        self.assertEqual(pa.morning_status_from(empty, dt.datetime(2026, 10, 15, 13, 0, tzinfo=U), "2026-10-15")["state"], pa.MORNING_MISSED)


if __name__ == "__main__":
    unittest.main()
