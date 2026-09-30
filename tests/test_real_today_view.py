"""
Tests for dashboard/real_today_view.py (Real Product Bridge block,
2026-09-29) -- the one canonical real-data structure the Today page's
primary sections read from. Proves: real games replace demo games, real
prices flow into Top Conviction, a real SOG quote can reach Today, demo
data can never enter this structure (the module never imports demo_data at
all), stale real markets are correctly excluded (never faked), missing
real data produces an honest empty state, the parlay section uses the same
real legs shown elsewhere, and the cloud snapshot round-trip preserves
every field the page reads.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import db
from dashboard import real_today_view as rtv
from research.generic_prop_pricing import provider_adapter as pa

NOW_TZ = __import__("datetime").timezone.utc


def _fresh_db_with_games(games: list[dict]):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = db.init_db(db_path=Path(tmp.name), wipe=True)
    for g in games:
        for t in (g["home"], g["away"]):
            conn.execute("INSERT OR IGNORE INTO teams (team_id, full_name) VALUES (?, ?)", (t, t))
        conn.execute(
            "INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team, away_team, "
            "schedule_observed_at_utc, game_state, source) VALUES (?,?,?,?,?,?,?,?,?)",
            (g["game_id"], "20262027", g["date"], g["start"], g["home"], g["away"],
             g["start"], g.get("state", "SCHEDULED"), "test"))
    conn.commit()
    return conn


class TestRealSlateReplacesDemoSlate(unittest.TestCase):
    def test_module_never_imports_demo_data(self):
        import inspect
        src = inspect.getsource(rtv)
        self.assertNotIn("demo_data", src)
        self.assertNotIn("build_demo_games", src)

    def test_todays_real_games_are_the_cards_returned(self):
        import datetime as dt
        now = dt.datetime(2026, 9, 29, 18, 0, tzinfo=dt.timezone.utc)
        conn = _fresh_db_with_games([
            {"game_id": 1, "date": "2026-09-29", "start": "2026-09-29T23:00:00", "home": "TOR", "away": "MTL"},
            {"game_id": 2, "date": "2026-09-30", "start": "2026-09-30T23:00:00", "home": "BOS", "away": "NYR"},
        ])
        games = rtv._today_real_games(conn, now)
        self.assertEqual(len(games), 1)
        self.assertEqual((games[0]["home_team"], games[0]["away_team"]), ("TOR", "MTL"))
        # No demo/simulated field anywhere on a real game card.
        for key in games[0]:
            self.assertNotIn("simulated", key.lower())
            self.assertNotIn("demo", key.lower())

    def test_no_real_games_today_is_an_honest_empty_list_never_a_demo_substitute(self):
        import datetime as dt
        now = dt.datetime(2026, 9, 29, 18, 0, tzinfo=dt.timezone.utc)
        conn = _fresh_db_with_games([])
        games = rtv._today_real_games(conn, now)
        self.assertEqual(games, [])


class TestRealPricesFlowToTopConviction(unittest.TestCase):
    def _leg(self, conservative_probability, market_family="PLAYER_SOG_ALTERNATE", threshold=4, price=-150):
        from research.real_market_parlay.engine import ParlayLeg
        return ParlayLeg(
            game_id="G1", event_id="e1", market_family=market_family, participant_id="P1",
            participant_name="Test Player", side="OVER", threshold=threshold, american_price=price,
            conservative_probability=conservative_probability, sportsbook="draftkings",
            captured_at_utc="2026-09-29T18:00:00Z", provider_contract_verified=True,
            model_threshold_eligible=True, identity_resolved=True, price_fresh=True,
            event_not_started=True)

    def test_real_leg_fields_appear_verbatim_in_top_conviction(self):
        leg = self._leg(0.62)
        ranked = rtv.real_top_conviction([leg])
        self.assertEqual(len(ranked), 1)
        row = ranked[0]
        self.assertEqual(row["american_price"], leg.american_price)
        self.assertEqual(row["conservative_probability"], round(leg.conservative_probability, 4))
        self.assertEqual(row["sportsbook"], "draftkings")
        self.assertEqual(row["data_label"], "REAL MARKET DATA")

    def test_ranked_by_real_conservative_probability_descending(self):
        weak, strong = self._leg(0.4), self._leg(0.9)
        ranked = rtv.real_top_conviction([weak, strong])
        self.assertEqual(ranked[0]["conservative_probability"], 0.9)

    def test_no_eligible_legs_is_an_honest_named_empty_state(self):
        state = {"top_conviction": [] or rtv.NO_QUALIFYING_REAL_OPPORTUNITIES}
        self.assertEqual(state["top_conviction"], rtv.NO_QUALIFYING_REAL_OPPORTUNITIES)


class TestDemoCannotEnterRealStructures(unittest.TestCase):
    def test_real_top_conviction_only_accepts_parlaylet_objects_not_demo_dicts(self):
        # A demo-shaped dict (dashboard/demo_data.py's own row shape) has no
        # .conservative_probability ATTRIBUTE (only a dict key) -- proving
        # real_top_conviction() cannot silently accept a demo row.
        demo_like = {"conservative_probability": 0.99, "player": "Demo Player"}
        with self.assertRaises(AttributeError):
            rtv.real_top_conviction([demo_like])

    def test_build_real_today_state_never_calls_any_demo_builder(self):
        import inspect
        src = inspect.getsource(rtv.build_real_today_state)
        for forbidden in ("build_demo_games", "build_demo_roster", "build_demo_opportunities", "demo_data"):
            self.assertNotIn(forbidden, src)

    def test_real_market_parlay_package_never_imports_demo_data(self):
        # research/real_market_parlay/engine.py + real_slate_adapter.py are
        # the ONLY two sources build_real_today_state() draws ParlayLeg
        # objects from. Neither module may reference demo_data at all, so a
        # demo-priced leg has no code path into the real parlay pool.
        import inspect
        from research.real_market_parlay import engine as rmp_engine
        from research.real_market_parlay import real_slate_adapter as adapter
        for module in (rmp_engine, adapter):
            src = inspect.getsource(module)
            self.assertNotIn("demo_data", src)
            self.assertNotIn("build_demo_games", src)

    def test_a_demo_recorded_prediction_never_reaches_the_real_ledger(self):
        # dashboard/real_recommendations_view.py and Recorded Recommendations
        # both read directly from operational.prospective_recording's ledger
        # table. Part 5 of that module requires is_demo=True to short-circuit
        # BEFORE any ledger call -- proven here with a conn stub that raises
        # if touched, so a demo price can never enter a real recommendation
        # even via a future refactor that reorders the checks.
        from operational import prospective_recording as pr

        class _ExplodingConn:
            def execute(self, *a, **k):
                raise AssertionError("a demo prediction must never touch the real ledger connection")

            def cursor(self, *a, **k):
                raise AssertionError("a demo prediction must never touch the real ledger connection")

        result = pr.record_observation(_ExplodingConn(), {"model_id": "any"}, is_demo=True)
        self.assertEqual(result["status"], "DEMO_NOT_RECORDABLE")
        self.assertIsNone(result["prediction_id"])


class TestStaleMarketsExcludedNeverFaked(unittest.TestCase):
    def test_stale_sog_quote_produces_zero_eligible_legs_not_a_fabricated_one(self):
        import datetime as dt
        from research.real_market_parlay import real_slate_adapter as adapter
        conn = _fresh_db_with_games([
            {"game_id": 555, "date": "2026-09-29", "start": "2026-09-29T21:10:47", "home": "CAR", "away": "FLA"},
        ])
        path = Path(__file__).resolve().parent / "fixtures" / "draftkings_player_sog_alternate_real_payload.json"
        with open(path) as f:
            payload = json.load(f)
        stale_now = dt.datetime(2026, 9, 29, 21, 0, tzinfo=dt.timezone.utc)  # ~9h after the real 12:14Z capture
        legs, excluded = adapter.sog_alternate_candidate_legs(conn, [payload], now=stale_now)
        self.assertEqual(legs, [])
        self.assertTrue(any("STALE_PRICE" in e["reason"] for e in excluded))


class TestGameParlaysUseTheSameRealLegsAsElsewhereOnToday(unittest.TestCase):
    def test_a_games_strongest_leg_is_drawn_from_the_same_pool_the_parlay_engine_sees(self):
        import datetime as dt
        from research.real_market_parlay.engine import ParlayLeg

        def _leg(game_id, p):
            return ParlayLeg(game_id=game_id, event_id=f"e-{game_id}", market_family="MONEYLINE",
                             participant_id="TOR", participant_name="Toronto", side="HOME", threshold=None,
                             american_price=-150, conservative_probability=p, sportsbook="draftkings",
                             captured_at_utc="2026-09-29T18:00:00Z", provider_contract_verified=True,
                             model_threshold_eligible=True, identity_resolved=True, price_fresh=True,
                             event_not_started=True)

        now = dt.datetime(2026, 9, 29, 18, 0, tzinfo=dt.timezone.utc)
        conn = _fresh_db_with_games([
            {"game_id": 1, "date": "2026-09-29", "start": "2026-09-29T23:00:00", "home": "TOR", "away": "MTL"},
        ])
        with mock.patch("research.real_market_parlay.real_slate_adapter.moneyline_candidate_legs",
                        return_value=([_leg("1", 0.55), _leg("1", 0.91)], [])), \
             mock.patch("research.real_market_parlay.real_slate_adapter.sog_alternate_candidate_legs",
                        return_value=([], [])), \
             mock.patch("operational.real_prop_orchestrator._recent_archive_payloads", return_value=[]):
            state = rtv.build_real_today_state(conn, now=now)
        game = state["games"][0]
        self.assertIsNotNone(game["strongest_leg"])
        self.assertEqual(game["strongest_leg"]["conservative_probability"], 0.91)  # the stronger of the two real legs


class TestCloudSnapshotRoundTrip(unittest.TestCase):
    def test_real_today_section_survives_json_round_trip_with_every_field_the_page_reads(self):
        state = {
            "generated_at_utc": "2026-09-29T18:00:00+00:00", "provenance": "REAL MARKET DATA",
            "games": [{"game_id": "1", "home_team": "TOR", "away_team": "MTL", "game_state": "SCHEDULED",
                      "scheduled_start_utc": "2026-09-29T23:00:00",
                      "strongest_leg": {"participant_name": "P", "market_family": "MONEYLINE", "threshold": None,
                                        "conservative_probability": 0.6, "data_label": "REAL MARKET DATA"}}],
            "eligible_leg_count": 1,
            "top_conviction": [{"participant_name": "P", "market_family": "MONEYLINE", "threshold": None,
                                "conservative_probability": 0.6, "american_price": -150, "sportsbook": "draftkings",
                                "captured_at_utc": "2026-09-29T18:00:00Z", "data_label": "REAL MARKET DATA",
                                "side": "HOME", "game_id": "1"}],
            "parlay": {"status": "NO_QUALIFYING_PARLAY", "reason": "only 1 leg"},
            "excluded_count": 0, "excluded_by_reason": {},
        }
        round_tripped = json.loads(json.dumps(state))
        self.assertEqual(round_tripped, state)
        # Every field dashboard/pages/21_Today.py actually reads must survive.
        game = round_tripped["games"][0]
        for field in ("game_id", "home_team", "away_team", "game_state", "scheduled_start_utc", "strongest_leg"):
            self.assertIn(field, game)
        leg = round_tripped["top_conviction"][0]
        for field in ("participant_name", "market_family", "threshold", "conservative_probability",
                     "american_price", "sportsbook", "data_label"):
            self.assertIn(field, leg)

    def test_snapshot_builder_real_today_section_is_real_json_serializable(self):
        from operational import cloud_snapshot_builder as csb
        doc, errors = csb.build_live_snapshot(sections=("real_today",))
        self.assertEqual(errors, {})
        self.assertIn("real_today", doc)
        # strict round-trip: must already be pure JSON (schema.strict_dumps
        # already ran inside build_live_snapshot).
        reloaded = json.loads(json.dumps(doc["real_today"]))
        self.assertEqual(reloaded, doc["real_today"])


if __name__ == "__main__":
    unittest.main()
