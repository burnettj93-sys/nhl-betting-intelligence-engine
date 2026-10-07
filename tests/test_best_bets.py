"""operational/best_bets.py -- price capture, second-opinion model helpers, state refresh.
Recommendations live in the unified ticket workflow (tests/test_daily_tickets.py)."""
from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import db
from operational import best_bets as bb

NOW = dt.datetime(2026, 10, 6, 20, 0, tzinfo=dt.timezone.utc)


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


def _payload(home="Philadelphia Flyers", away="Tampa Bay Lightning", players=None, shots_point=0.5, price=-300):
    outs = [{"name": "Over", "description": n, "point": shots_point, "price": price} for n in (players or ["Tyler Toffoli"])]
    pts = [{"name": "Over", "description": n, "point": 1.5, "price": 250} for n in (players or ["Tyler Toffoli"])]
    pts.append({"name": "Under", "description": "Tyler Toffoli", "point": 1.5, "price": -400})
    return {"id": "e" * 32, "home_team": home, "away_team": away, "bookmakers": [{"key": "draftkings", "markets": [
        {"key": "player_shots_on_goal_alternate", "outcomes": outs}, {"key": "player_points", "outcomes": pts}]}]}


_SNAPSHOT = {"date": "2026-10-06", "games": {"7": {"home": "PHI", "away": "TBL", "start_utc": "2026-10-06T23:00:00Z"}},
             "model": {"tyler toffoli|PHI": {"player_id": "8475000", "name": "Tyler Toffoli", "team": "PHI", "opp": "TBL",
                                              "home": True, "probs": {"SOG1": 0.87, "SOG2": 0.56, "SOG3": 0.30,
                                                                      "PTS1": 0.55, "PTS2": 0.20}}}}


class TestLegsFromPayload(unittest.TestCase):
    def test_over_thresholds_become_certified_family_parlay_legs(self):
        captured = NOW - dt.timedelta(minutes=10)
        legs = [l for l in bb._legs_from_payload(_payload(shots_point=1.5, price=105), captured, _SNAPSHOT, NOW)
                if l.market_family == "PLAYER_SOG_ALTERNATE"]
        self.assertEqual(len(legs), 1)
        leg = legs[0]
        self.assertEqual((leg.market_family, leg.threshold, leg.side), ("PLAYER_SOG_ALTERNATE", 2, "OVER"))
        self.assertEqual((leg.game_id, leg.participant_id, leg.american_price), ("7", "8475000", 105.0))
        self.assertAlmostEqual(leg.conservative_probability, 0.56)
        self.assertEqual(leg.captured_at_utc, captured.isoformat())     # the price timestamp travels with the leg
        self.assertTrue(leg.provider_contract_verified and leg.price_fresh and leg.event_not_started)
        self.assertEqual(leg.model_version, bb.MODEL_VERSION)

    def test_points_overs_become_certified_points_legs_and_unders_are_ignored(self):
        payload = _payload(shots_point=1.5)
        payload["bookmakers"][0]["markets"][1]["outcomes"].append(
            {"name": "Over", "description": "Tyler Toffoli", "point": 0.5, "price": -140})
        legs = bb._legs_from_payload(payload, NOW, _SNAPSHOT, NOW)
        points = sorted((l.threshold, l.american_price, l.conservative_probability) for l in legs
                        if l.market_family == "PLAYER_POINTS")
        self.assertEqual(points, [(1, -140.0, 0.55), (2, 250.0, 0.20)])      # Over 0.5 == 1+, Over 1.5 == 2+
        self.assertTrue(all(l.provider_contract_verified for l in legs))

    def test_a_stale_price_is_marked_not_fresh_so_the_selector_rejects_it(self):
        legs = bb._legs_from_payload(_payload(shots_point=1.5), NOW - dt.timedelta(hours=4), _SNAPSHOT, NOW)
        self.assertTrue(legs and all(not l.price_fresh for l in legs))

    def test_unmodelled_player_unknown_game_or_missing_threshold_probability_are_skipped(self):
        self.assertEqual(bb._legs_from_payload(_payload(players=["Nobody Known"]), NOW, _SNAPSHOT, NOW), [])
        self.assertEqual(bb._legs_from_payload(_payload(home="Boston Bruins"), NOW, _SNAPSHOT, NOW), [])
        no_shots = [l for l in bb._legs_from_payload(_payload(shots_point=4.5), NOW, _SNAPSHOT, NOW)
                    if l.market_family == "PLAYER_SOG_ALTERNATE"]
        self.assertEqual(no_shots, [])


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
        with mock.patch.object(bb, "_load_or_build_model", return_value=model), \
             mock.patch.object(bb, "_state_path", return_value=tmp / "state.json"):
            first = bb.refresh(NOW, conn=conn, capture=False)
            second = bb.refresh(NOW, conn=conn, capture=False)
            state = json.loads((tmp / "state.json").read_text())
            snapshot = bb.current_model(conn, NOW)
        self.assertEqual(first["status"], "OK")
        self.assertTrue(first["changed"])
        self.assertFalse(second["changed"])                 # identical state -> no republish churn
        self.assertEqual(state["games_today_upcoming"], 1)
        self.assertEqual(state["modelled_players"], 1)
        self.assertNotIn("singles", state)                  # best_bets no longer recommends anything
        self.assertEqual(snapshot["games"]["7"], {"home": "PHI", "away": "TBL", "start_utc": "2026-10-06T23:00:00Z"})
        conn.close()


class TestSnapshotSection(unittest.TestCase):
    def test_snapshot_exposes_the_ticket_state_not_a_second_recommendation_list(self):
        from operational import cloud_snapshot_builder as builder
        from operational import daily_tickets
        self.assertIn("tickets", builder._SECTION_BUILDERS)
        self.assertNotIn("best_bets", builder._SECTION_BUILDERS)
        with mock.patch.object(daily_tickets, "read_state", return_value={"account": {}, "tickets": []}):
            self.assertEqual(builder._tickets()["tickets"], [])
        with mock.patch.object(daily_tickets, "read_state", return_value=None):
            self.assertEqual(builder._tickets(), {"status": "NOT_RUN"})


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
