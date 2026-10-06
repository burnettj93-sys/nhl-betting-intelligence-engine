"""Best Bets (+100 target) -- operational/best_bets.py + dashboard/best_bets_view.py.
Pure pick logic, capture cadence/budget, payload matching, state refresh, formatting."""
from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import db
from dashboard import best_bets_view as view
from operational import best_bets as bb

NOW = dt.datetime(2026, 10, 6, 20, 0, tzinfo=dt.timezone.utc)


def _leg(player="A", game="g1", price=-150, p=0.62, hours=3.0, age=20.0, label="2+ shots on goal", market="SOG2",
         team="PHI", opp="TBL", home=False):
    dec = bb.american_to_decimal(price)
    start = (NOW + dt.timedelta(hours=hours)).isoformat()
    return {"player": player, "team": team, "opp": opp, "home": home, "game_key": game, "start_utc": start,
            "label": label, "market": market, "price": price, "decimal": dec, "p": p, "implied": 1 / dec,
            "edge": p - 1 / dec, "captured_at_utc": (NOW - dt.timedelta(minutes=age)).isoformat()}


class TestPriceMath(unittest.TestCase):
    def test_round_trips(self):
        self.assertAlmostEqual(bb.american_to_decimal(100), 2.0)
        self.assertAlmostEqual(bb.american_to_decimal(-200), 1.5)
        self.assertEqual(bb.decimal_to_american(2.0), 100)
        self.assertEqual(bb.decimal_to_american(3.0), 200)
        self.assertEqual(bb.decimal_to_american(1.5), -200)

    def test_norm_name_strips_accents_and_punctuation(self):
        self.assertEqual(bb.norm_name("Jesper Bräynd-Jr."), "jesper braynd jr")
        self.assertEqual(bb.norm_name("Nick Suzuki"), "nick suzuki")
        self.assertEqual(bb.norm_name("Alexis Lafrenière"), "alexis lafreniere")


class TestCaptureCadence(unittest.TestCase):
    def test_nothing_outside_the_horizon_or_after_start(self):
        self.assertIsNone(bb.capture_decision(bb.CAPTURE_HORIZON_H + 0.1, None))
        self.assertIsNone(bb.capture_decision(0.0, None))
        self.assertIsNone(bb.capture_decision(-1.0, None))

    def test_first_capture_inside_the_horizon(self):
        self.assertEqual(bb.capture_decision(4.0, None), "FIRST")

    def test_refresh_only_near_puck_drop_and_only_when_aged(self):
        self.assertIsNone(bb.capture_decision(4.0, 200.0))                  # aged, but far from puck drop
        self.assertIsNone(bb.capture_decision(1.0, 30.0))                   # near, but fresh
        self.assertEqual(bb.capture_decision(1.0, 120.0), "REFRESH")


class TestBuildPicks(unittest.TestCase):
    def test_single_must_be_plus_money_with_real_edge(self):
        good = _leg("good", price=110, p=0.56)
        no_edge = _leg("noedge", price=110, p=0.50)          # edge 0.02 < 0.04
        short_price = _leg("short", price=-150, p=0.80)      # not +100
        low_p = _leg("lowp", price=300, p=0.30)              # below MIN_SINGLE_P
        out = bb.build_picks([good, no_edge, short_price, low_p], NOW)
        self.assertEqual([s["player"] for s in out["singles"]], ["good"])

    def test_singles_ranked_by_hit_chance(self):
        a, b = _leg("a", price=105, p=0.56), _leg("b", price=120, p=0.52)
        out = bb.build_picks([b, a], NOW)
        self.assertEqual([s["player"] for s in out["singles"]], ["a", "b"])

    def test_parlay_needs_plus_100_combined_cross_game_and_positive_ev(self):
        a = _leg("a", game="g1", price=-150, p=0.70)
        b = _leg("b", game="g2", price=-120, p=0.62)         # 1.667 * 1.833 = 3.06 -> +206
        same_game = _leg("c", game="g1", price=-120, p=0.62)
        out = bb.build_picks([a, b, same_game], NOW)
        pairs = [frozenset(l["player"] for l in p["legs"]) for p in out["parlays"]]
        self.assertIn(frozenset({"a", "b"}), pairs)
        self.assertNotIn(frozenset({"a", "c"}), pairs)       # never two legs from the same game
        top = next(p for p in out["parlays"] if {l["player"] for l in p["legs"]} == {"a", "b"})
        self.assertGreaterEqual(top["american"], 100)
        self.assertAlmostEqual(top["p"], 0.70 * 0.62)

    def test_parlay_below_plus_100_is_rejected(self):
        a = _leg("a", game="g1", price=-400, p=0.90)
        b = _leg("b", game="g2", price=-400, p=0.90)         # 1.25*1.25 = 1.56 -> -178
        self.assertEqual(bb.build_picks([a, b], NOW)["parlays"], [])

    def test_a_junk_short_price_leg_is_not_a_parlay_leg(self):
        junk = _leg("junk", game="g1", price=-3500, p=0.98)
        real = _leg("real", game="g2", price=200, p=0.55)
        out = bb.build_picks([junk, real], NOW)
        self.assertEqual(out["parlays"], [])

    def test_stale_and_started_prices_are_excluded(self):
        stale = _leg("stale", price=110, p=0.56, age=500.0)
        started = _leg("started", price=110, p=0.56, hours=-0.5)
        near_old = _leg("nearold", price=110, p=0.56, hours=1.0, age=120.0)   # near tier allows 100 min
        fresh = _leg("fresh", price=110, p=0.56)
        out = bb.build_picks([stale, started, near_old, fresh], NOW)
        self.assertEqual([s["player"] for s in out["singles"]], ["fresh"])
        self.assertEqual(out["priced_legs_considered"], 1)

    def test_a_player_is_not_reused_more_than_twice(self):
        star = [_leg("star", game="g1", price=-150, p=0.70, label=f"{k}+ x") for k in (1,)]
        others = [_leg(f"o{i}", game=f"g{i + 2}", price=-130, p=0.62) for i in range(5)]
        out = bb.build_picks(star + others, NOW)
        uses = sum(1 for p in out["parlays"] for l in p["legs"] if l["player"] == "star")
        self.assertLessEqual(uses, bb.MAX_PLAYER_REUSE)


def _payload(home="Philadelphia Flyers", away="Tampa Bay Lightning", players=None, shots_point=0.5, price=-300):
    outs = [{"name": "Over", "description": n, "point": shots_point, "price": price} for n in (players or ["Tyler Toffoli"])]
    pts = [{"name": "Over", "description": n, "point": 1.5, "price": 250} for n in (players or ["Tyler Toffoli"])]
    pts.append({"name": "Under", "description": "Tyler Toffoli", "point": 1.5, "price": -400})
    return {"home_team": home, "away_team": away, "bookmakers": [{"key": "draftkings", "markets": [
        {"key": "player_shots_on_goal_alternate", "outcomes": outs}, {"key": "player_points", "outcomes": pts}]}]}


class TestLegsFromPayload(unittest.TestCase):
    MODEL = {"tyler toffoli|PHI": {"name": "Tyler Toffoli", "team": "PHI", "opp": "TBL", "home": True,
                                    "probs": {"SOG1": 0.87, "PTS2": 0.18}}}
    PAIR = {("PHI", "TBL"): {"game_key": "7", "start_utc": "2026-10-06T23:00:00Z"}}

    def test_over_thresholds_map_to_k_plus_and_unders_are_ignored(self):
        legs = bb._legs_from_payload(_payload(), NOW, self.MODEL, self.PAIR)
        by_market = {l["market"]: l for l in legs}
        self.assertEqual(set(by_market), {"SOG1", "PTS2"})            # 0.5 -> 1+, 1.5 -> 2+; Under dropped
        self.assertEqual(by_market["SOG1"]["label"], "1+ shots on goal")
        self.assertEqual(by_market["PTS2"]["label"], "2+ points")
        self.assertAlmostEqual(by_market["SOG1"]["edge"], 0.87 - 0.75)

    def test_unmodelled_player_or_unknown_game_is_skipped(self):
        self.assertEqual(bb._legs_from_payload(_payload(players=["Nobody Real"]), NOW, self.MODEL, self.PAIR), [])
        self.assertEqual(bb._legs_from_payload(_payload(home="Boston Bruins", away="Ottawa Senators"),
                                                NOW, self.MODEL, self.PAIR), [])

    def test_a_threshold_the_model_has_no_probability_for_is_skipped(self):
        legs = bb._legs_from_payload(_payload(shots_point=7.5), NOW, self.MODEL, self.PAIR)   # 8+ shots: no SOG8
        self.assertEqual({l["market"] for l in legs}, {"PTS2"})


class TestArchiveHelpers(unittest.TestCase):
    def _write(self, d, name, market_filter, retrieved, event="e" * 32, cost="3"):
        (d / name).write_text(json.dumps({
            "meta": {"market_filter": market_filter, "retrieved_at_utc": retrieved, "requests_last_header": cost},
            "response": {"id": event, "marker": name}}))

    def test_latest_capture_takes_the_newest_payload_that_has_both_markets(self):
        d = Path(tempfile.mkdtemp())
        ev = "a" * 32
        both = "player_shots_on_goal_alternate,player_points"
        self._write(d, f"20261006T150000Z_x-events-{ev}-odds_1.json", both, "2026-10-06T15:00:00Z")
        self._write(d, f"20261006T170000Z_x-events-{ev}-odds_2.json", both, "2026-10-06T17:00:00Z")
        self._write(d, f"20261006T180000Z_x-events-{ev}-odds_3.json", "player_total_saves", "2026-10-06T18:00:00Z")
        got = bb.latest_capture(ev, d)
        self.assertEqual(got[0], dt.datetime(2026, 10, 6, 17, 0, tzinfo=dt.timezone.utc))
        self.assertEqual(got[1]["marker"], f"20261006T170000Z_x-events-{ev}-odds_2.json")

    def test_latest_capture_none_when_nothing_matches(self):
        self.assertIsNone(bb.latest_capture("b" * 32, Path(tempfile.mkdtemp())))

    def test_credits_spent_counts_only_this_jobs_markets_today(self):
        d = Path(tempfile.mkdtemp())
        both = "player_shots_on_goal_alternate,player_points"
        self._write(d, "20261006T150000Z_x-events-" + "a" * 32 + "-odds_1.json", both, "2026-10-06T15:00:00Z", cost="3")
        self._write(d, "20261006T160000Z_x-events-" + "b" * 32 + "-odds_1.json", both, "2026-10-06T16:00:00Z", cost="2")
        self._write(d, "20261006T161000Z_x-events-" + "c" * 32 + "-odds_1.json", "player_total_saves", "2026-10-06T16:10:00Z", cost="9")
        self._write(d, "20261005T150000Z_x-events-" + "a" * 32 + "-odds_1.json", both, "2026-10-05T15:00:00Z", cost="4")
        self.assertEqual(bb.credits_spent_today_by_this_job(NOW, d), 5)


class _Resp:
    def __init__(self, ok=True, data=None, cost=2, error=None):
        self.ok, self.data, self.requests_last, self.error = ok, data, cost, error


class _FakeClient:
    def __init__(self, events, odds_ok=True):
        self._events, self.odds_calls, self.odds_ok = events, [], odds_ok

    def get_nhl_events(self):
        return _Resp(data=self._events)

    def get_event_odds(self, event_id, markets=None):
        self.odds_calls.append((event_id, markets))
        return _Resp(ok=self.odds_ok, data={"id": event_id}, cost=2, error=None if self.odds_ok else "boom")


class _FakeArchive:
    def __init__(self):
        self.written = []

    def archive_result(self, r, **kw):
        self.written.append(kw)


class TestCapturePrices(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())
        p = mock.patch.object(bb, "_archive_dir", return_value=self._tmp)
        p.start()
        self.addCleanup(p.stop)
        self.allow = lambda planned: {"allow": True}

    def _event(self, i, hours):
        return {"id": f"{i:032x}", "commence_time": (NOW + dt.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")}

    def test_only_events_inside_the_horizon_are_pulled_with_the_right_markets(self):
        client, archive = _FakeClient([self._event(1, 3.0), self._event(2, 9.0), self._event(3, -1.0)]), _FakeArchive()
        out = bb.capture_prices(NOW, client=client, archive_mod=archive, guard=self.allow)
        self.assertEqual(out["events_captured"], 1)
        self.assertEqual(client.odds_calls, [(f"{1:032x}", bb.MARKETS)])
        self.assertEqual(archive.written[0]["market_filter"], bb.MARKETS)
        self.assertEqual(out["credits_spent"], 2)

    def test_the_global_quota_guard_stops_the_run(self):
        client = _FakeClient([self._event(1, 3.0), self._event(2, 3.5)])
        out = bb.capture_prices(NOW, client=client, archive_mod=_FakeArchive(),
                                guard=lambda planned: {"allow": False, "reason": "DAILY_SOFT_BUDGET"})
        self.assertEqual(client.odds_calls, [])
        self.assertEqual(out["skipped"][0]["reason"], "DAILY_SOFT_BUDGET")

    def test_the_jobs_own_daily_credit_cap_is_enforced(self):
        client = _FakeClient([self._event(1, 3.0)])
        with mock.patch.object(bb, "credits_spent_today_by_this_job", return_value=bb.DAILY_CREDIT_CAP - 1):
            out = bb.capture_prices(NOW, client=client, archive_mod=_FakeArchive(), guard=self.allow)
        self.assertEqual(client.odds_calls, [])
        self.assertEqual(out["skipped"][0]["reason"], "BEST_BETS_DAILY_CREDIT_CAP")

    def test_an_api_error_is_reported_and_never_archived(self):
        client, archive = _FakeClient([self._event(1, 3.0)], odds_ok=False), _FakeArchive()
        out = bb.capture_prices(NOW, client=client, archive_mod=archive, guard=self.allow)
        self.assertEqual(archive.written, [])
        self.assertIn("API_ERROR", out["skipped"][0]["reason"])


class TestRefresh(unittest.TestCase):
    def test_under_test_without_injection_never_touches_the_network_or_state(self):
        out = bb.refresh(NOW)
        self.assertEqual(out["status"], "SKIPPED")
        self.assertFalse(out["changed"])

    def test_end_to_end_writes_state_and_reports_changed_once(self):
        tmp = Path(tempfile.mkdtemp())
        conn = db.init_db(db_path=tmp / "nhl.db", wipe=True)
        for t in ("PHI", "TBL"):
            conn.execute("INSERT OR IGNORE INTO teams (team_id, full_name) VALUES (?, ?)", (t, t))
        conn.execute(
            "INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team, away_team, "
            "schedule_observed_at_utc, game_state, source) VALUES (7,'20262027','2026-10-06','2026-10-06T23:00:00',"
            "'PHI','TBL','2026-10-06T00:00:00','SCHEDULED','test')")
        conn.commit()
        model = {"tyler toffoli|PHI": {"name": "Tyler Toffoli", "team": "PHI", "opp": "TBL", "home": True,
                                        "probs": {"SOG2": 0.56, "SOG1": 0.87}}}
        payload = _payload(players=["Tyler Toffoli"], shots_point=1.5, price=105)
        capture = (NOW - dt.timedelta(minutes=10), payload)
        with mock.patch.object(bb, "_load_or_build_model", return_value=model), \
             mock.patch.object(bb, "latest_capture", return_value=capture), \
             mock.patch.object(bb, "_archive_dir", return_value=tmp), \
             mock.patch.object(bb, "_state_path", return_value=tmp / "state.json"):
            (tmp / f"{NOW.strftime('%Y%m%d')}T190000Z_x-events-{'a' * 32}-odds_1.json").write_text("{}")
            first = bb.refresh(NOW, conn=conn, capture=False)
            second = bb.refresh(NOW, conn=conn, capture=False)
            state = json.loads((tmp / "state.json").read_text())
        self.assertEqual(first["status"], "OK")
        self.assertTrue(first["changed"])
        self.assertFalse(second["changed"])                 # identical picks -> no republish churn
        self.assertEqual(state["singles"][0]["player"], "Tyler Toffoli")
        self.assertEqual(state["singles"][0]["price"], 105)
        self.assertEqual(state["events_priced"], 1)
        conn.close()


class TestView(unittest.TestCase):
    def test_formatting_single_and_parlay(self):
        single = bb.build_picks([_leg("Dvorak", price=105, p=0.56, team="PHI", opp="TBL", home=False)], NOW)["singles"][0]
        out = view.format_single(single)
        self.assertEqual(out["Price"], "+105")
        self.assertEqual(out["Model hit chance"], "56%")
        self.assertIn("PHI @ TBL", out["Game"])
        parlay = bb.build_picks([_leg("a", game="g1", price=-150, p=0.70), _leg("b", game="g2", price=-120, p=0.62)], NOW)["parlays"][0]
        row = view.format_parlay(parlay)
        self.assertTrue(row["Combined price"].startswith("+"))
        self.assertIn("a 2+ shots on goal (-150)", row["Leg 1"] + row["Leg 2"])

    def test_empty_states_say_why(self):
        self.assertEqual(view.format_state(None)["status"], "NOT_RUN")
        no_games = view.format_state({"status": "NO_QUALIFYING_PICKS", "games_today_upcoming": 0, "events_priced": 0})
        self.assertIn("No upcoming games", no_games["message"])
        unpriced = view.format_state({"status": "NO_QUALIFYING_PICKS", "games_today_upcoming": 9, "events_priced": 0})
        self.assertIn("none have been captured", unpriced["message"])
        priced = view.format_state({"status": "NO_QUALIFYING_PICKS", "games_today_upcoming": 9, "events_priced": 4})
        self.assertIn("honest result", priced["message"])


class TestSnapshotAndPage(unittest.TestCase):
    def test_snapshot_section_returns_state_or_an_honest_not_run(self):
        from operational import cloud_snapshot_builder as builder
        self.assertIn("best_bets", builder._SECTION_BUILDERS)
        with mock.patch.object(bb, "read_state", return_value={"status": "OK", "singles": []}):
            self.assertEqual(builder._best_bets()["status"], "OK")
        with mock.patch.object(bb, "read_state", return_value=None):
            self.assertEqual(builder._best_bets(), {"status": "NOT_RUN"})

    def _today(self, state):
        import os
        from streamlit.testing.v1 import AppTest
        page = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dashboard", "pages", "21_Today.py")
        with mock.patch.object(bb, "read_state", return_value=state):
            at = AppTest.from_file(page, default_timeout=120)
            at.run()
        self.assertEqual(len(at.exception), 0)
        return at

    def test_today_page_shows_the_picks_tables(self):
        picks = bb.build_picks([_leg("Dvorak", price=105, p=0.56, game="g1"),
                                _leg("a", game="g2", price=-150, p=0.70), _leg("b", game="g3", price=-120, p=0.62)], NOW)
        state = {"status": "OK", "generated_at_utc": NOW.isoformat(), "games_today_upcoming": 3, "events_priced": 3,
                 "limits": "x", **picks}
        at = self._today(state)
        self.assertTrue(any("Best Bets" in m.value for m in at.markdown))
        self.assertGreaterEqual(len(at.dataframe), 2)            # singles + two-leg tables

    def test_today_page_says_why_when_there_are_no_picks(self):
        at = self._today({"status": "NO_QUALIFYING_PICKS", "games_today_upcoming": 9, "events_priced": 0,
                          "singles": [], "parlays": [], "limits": "x"})
        self.assertTrue(any("NO_QUALIFYING_PICKS" in m.value for m in at.markdown))
        self.assertTrue(any("none have been captured" in m.value for m in at.markdown))


class TestPointInTimeLineupHelpers(unittest.TestCase):
    def setUp(self):
        from features import point_in_time as pit
        self.pit = pit
        tmp = Path(tempfile.mkdtemp())
        self.conn = db.init_db(db_path=tmp / "nhl.db", wipe=True)
        for t in ("PHI", "TBL"):
            self.conn.execute("INSERT OR IGNORE INTO teams (team_id, full_name) VALUES (?, ?)", (t, t))
        self.conn.execute(
            "INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team, away_team, "
            "schedule_observed_at_utc, game_state, source) VALUES (1,'20262027','2026-10-04','2026-10-04T23:00:00',"
            "'PHI','TBL','2026-10-01T00:00:00','FINAL','test')")

    def _stat(self, pid, team, played, observed, rev=1):
        self.conn.execute(
            "INSERT INTO player_game_stats (game_id, player_id, team_id, toi_minutes, goals, assists, shots, played, "
            "revision_number, effective_at_utc, observed_at_utc, source) VALUES (1,?,?,18.0,0,0,2,?,?,?,?,'test')",
            (pid, team, played, rev, observed, observed))

    def tearDown(self):
        self.conn.close()

    def test_only_players_who_played_for_that_team_by_the_cutoff_count(self):
        self._stat("p1", "PHI", 1, "2026-10-05T01:00:00")
        self._stat("p2", "PHI", 0, "2026-10-05T01:00:00")            # scratched
        self._stat("p3", "TBL", 1, "2026-10-05T01:00:00")            # other team
        self._stat("p4", "PHI", 1, "2026-10-09T01:00:00")            # observed after the cutoff
        got = self.pit.team_players_who_played(self.conn, 1, "PHI", "2026-10-06T00:00:00")
        self.assertEqual(got, {"p1"})

    def test_a_later_revision_that_flips_played_wins(self):
        self._stat("p1", "PHI", 1, "2026-10-05T01:00:00", rev=1)
        self._stat("p1", "PHI", 0, "2026-10-05T02:00:00", rev=2)
        self.assertEqual(self.pit.team_players_who_played(self.conn, 1, "PHI", "2026-10-06T00:00:00"), set())

    def test_latest_team_reflects_the_most_recent_game(self):
        self._stat("p1", "PHI", 1, "2026-10-05T01:00:00")
        self.conn.execute(
            "INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team, away_team, "
            "schedule_observed_at_utc, game_state, source) VALUES (2,'20262027','2026-10-05','2026-10-05T23:00:00',"
            "'TBL','PHI','2026-10-01T00:00:00','FINAL','test')")
        self.conn.execute(
            "INSERT INTO player_game_stats (game_id, player_id, team_id, toi_minutes, goals, assists, shots, played, "
            "revision_number, effective_at_utc, observed_at_utc, source) VALUES (2,'p1','TBL',18.0,0,0,2,1,1,"
            "'2026-10-06T01:00:00','2026-10-06T01:00:00','test')")
        self.assertEqual(self.pit.latest_team_by_player_since(self.conn, "2026-09-01", "2026-10-07T00:00:00"),
                         {"p1": "TBL"})


if __name__ == "__main__":
    unittest.main()
