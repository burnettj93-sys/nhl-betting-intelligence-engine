"""Daily Faceoff reader: the confirmation policy (basis, freshness, naming, conflicts), failure handling and caching, the off switch, and
reported lineups held to the same source standard."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import unittest
from pathlib import Path
from unittest import mock

from operational import dailyfaceoff as df
from operational import goalie_confirmations as gc

FIX = Path(__file__).parent / "fixtures"
NOW = dt.datetime(2026, 10, 8, 20, 0, tzinfo=dt.timezone.utc)
REC = {"x_handles": {"andrewwilimek": {}, "jackstudley13": {}}, "suspended": {}}


def page(name):
    return json.loads((FIX / name).read_text())


def html(pageprops):
    return '<html><script id="__NEXT_DATA__" type="application/json">' + json.dumps({"props": {"pageProps": pageprops}}) + "</script></html>"


def raw_game(**kw):
    base = {"dateGmt": "2026-10-08T23:00:00.000Z", "homeGoalieName": "Joseph Woll", "homeTeamName": "Philadelphia Flyers",
            "homeNewsStrengthName": "Confirmed", "homeNewsSourceName": "Philadelphia Flyers", "homeNewsSourceUrl": "https://x.com/NHLFlyers/status/1",
            "homeNewsCreatedAt": "2026-10-08T17:00:00Z", "homeNewsDetails": "Woll will start vs. Ottawa on Thursday."}
    base.update(kw)
    return {"date": "2026-10-08", "data": [base]}


def side(raw):
    return df.parse_starting_goalies(raw, NOW, recognized=REC)["games"][0]["sides"]["home"]


class TestPolicy(unittest.TestCase):
    def test_team_post_confirmed(self):
        s = side(raw_game())
        self.assertEqual((s["basis"], s["status"], s["rejected_because"]), (df.TEAM_POST, "CONFIRMED", None))

    def test_recognized_beat_reporter_confirmed(self):
        g = df.parse_starting_goalies(page("dailyfaceoff_starting_goalies.json"), NOW, recognized=REC)["games"]
        ott = g[0]["sides"]["home"]
        self.assertEqual((ott["basis"], ott["status"]), (df.RECOGNIZED_REPORTER, "CONFIRMED"))
        self.assertEqual(ott["source_name"], "Andrew Wilmek")
        self.assertTrue(ott["source_url"].startswith("https://x.com/"))

    def test_likely_stays_distinct_from_confirmed(self):
        g = df.parse_starting_goalies(page("dailyfaceoff_starting_goalies.json"), NOW, recognized=REC)["games"]
        car = g[2]["sides"]["home"]
        self.assertEqual((car["status_word"], car["status"]), ("Likely", "EXPECTED"))

    def test_unrecognized_or_missing_source_is_not_confirmation(self):
        for kw, why in ((dict(homeNewsSourceName="Some Blogger", homeNewsSourceUrl="https://x.com/someblogger/status/9"), df.UNRECOGNIZED_SOURCE),
                        (dict(homeNewsSourceName="", homeNewsSourceUrl=None), df.NO_SOURCE),
                        (dict(homeNewsSourceName="Jack Studley", homeNewsSourceUrl=None), df.NO_SOURCE)):
            s = side(raw_game(**kw))
            self.assertEqual((s["status"], s["rejected_because"]), ("EXPECTED", why), kw)

    def test_a_suspended_reporter_no_longer_counts(self):
        rec = {"x_handles": {"jackstudley13": {}}, "suspended": {"jackstudley13": {}}}
        g = raw_game(homeNewsSourceName="Jack Studley", homeNewsSourceUrl="https://x.com/jackstudley13/status/5", homeGoalieName="Joseph Woll")
        s = df.parse_starting_goalies(g, NOW, recognized=rec)["games"][0]["sides"]["home"]
        self.assertEqual(s["status"], "EXPECTED")

    def test_stale_and_future_items_are_rejected(self):
        self.assertEqual(side(raw_game(homeNewsCreatedAt="2026-10-07T01:00:00Z"))["rejected_because"], "STALE")
        self.assertEqual(side(raw_game(homeNewsCreatedAt="2026-10-08T21:00:00Z"))["rejected_because"], "TIMESTAMP_IN_FUTURE")
        self.assertEqual(side(raw_game(homeNewsCreatedAt=None))["rejected_because"], "NO_TIMESTAMP")

    def test_text_must_name_the_goalie(self):
        s = side(raw_game(homeNewsDetails="Flyers morning skate recap."))
        self.assertEqual((s["status"], s["rejected_because"]), ("EXPECTED", "TEXT_DOES_NOT_NAME_GOALIE"))

    def test_a_confirmation_ages_out_between_fetches(self):
        state = {"goalies": df.parse_starting_goalies(raw_game(), NOW, recognized=REC)}
        self.assertEqual(state["goalies"]["games"][0]["sides"]["home"]["status"], "CONFIRMED")
        later = NOW + dt.timedelta(hours=40)
        with mock.patch.object(df, "load_recognized", return_value=REC):
            df.rejudge(state, later)
        self.assertEqual(state["goalies"]["games"][0]["sides"]["home"]["rejected_because"], "STALE")

    def test_handle_extraction(self):
        self.assertEqual(df.handle_of("https://x.com/JackStudley13/status/1?s=20"), "jackstudley13")
        self.assertIsNone(df.handle_of("https://example.com/a"))
        self.assertIsNone(df.handle_of(None))

    def test_the_shipped_recognized_list_is_nonempty_and_documents_exclusions(self):
        rec = df.load_recognized()
        self.assertGreater(len(rec["x_handles"]), 10)
        self.assertIn("ericengels", rec["excluded_at_seeding"])
        self.assertNotIn("ericengels", rec["x_handles"])


class TestParsing(unittest.TestCase):
    def test_line_combinations_groups_and_reporter(self):
        t = df.parse_line_combinations(page("dailyfaceoff_line_combinations.json"))
        self.assertEqual(t["team"], "OTT")
        for g in ("f1", "f4", "d1", "d3", "pp1", "pp2"):
            self.assertIn(g, t["groups"])
        self.assertTrue(t["reported_by"] and t["updated_at_utc"])

    def test_layout_change_is_an_error_not_silence(self):
        with self.assertRaises(ValueError):
            df.next_data("<html>no data</html>")


class TestLineupStandard(unittest.TestCase):
    def report(self, **kw):
        return {"team_name": "Ottawa Senators", "reported_by": "Andrew Wilimek", "source_url": "https://x.com/AndrewWilimek/status/2",
                "updated_at_utc": "2026-10-08T15:05:00Z", **kw}

    def test_identifiable_fresh_report_is_reported(self):
        self.assertEqual(df.lineup_basis(self.report(), NOW, recognized=REC)["status"], "REPORTED")

    def test_automatic_last_game_lineup_is_unsourced(self):
        q = df.lineup_basis(self.report(reported_by="Last Game (2026-10-07)", source_url=None), NOW, recognized=REC)
        self.assertEqual((q["status"], q["basis"]), ("UNSOURCED", df.NO_SOURCE))

    def test_old_report_is_stale(self):
        self.assertEqual(df.lineup_basis(self.report(updated_at_utc="2026-10-05T12:00:00Z"), NOW, recognized=REC)["status"], "STALE")

    def test_product_data_blanks_assignments_that_do_not_meet_the_standard(self):
        from operational import product_data
        t = df.parse_line_combinations(page("dailyfaceoff_line_combinations.json"))
        names = [p["name"] for p in t["groups"]["f1"]]
        skaters = {f"P{i}": {"name": n, "team": "OTT"} for i, n in enumerate(names)}
        good = {"lines": {"fetched_at_utc": "2026-10-08T19:00:00Z", "teams": {"OTT": {**t, "reported_by": "Andrew Wilimek", "source_url": "https://x.com/AndrewWilimek/status/2",
                                                                                    "updated_at_utc": "2026-10-08T15:05:00Z"}}}}
        with mock.patch.object(df, "load_recognized", return_value=REC):
            ok = product_data.reported_lineups(good, skaters, NOW)
            bad_state = {"lines": {"fetched_at_utc": "x", "teams": {"OTT": {**t, "reported_by": "Last Game (2026-10-07)", "source_url": None}}}}
            bad = product_data.reported_lineups(bad_state, skaters, NOW)
        self.assertEqual(ok["P0"]["status"], "REPORTED")
        self.assertEqual(ok["P0"]["line"], "F1")
        self.assertEqual(bad["P0"]["status"], "UNSOURCED")
        self.assertIsNone(bad["P0"]["line"])
        self.assertIsNone(bad["P0"]["source_url"])


class TestRefresh(unittest.TestCase):
    def setUp(self):
        self.enabled = mock.patch.object(df, "enabled", return_value=True)
        self.enabled.start()
        self.addCleanup(self.enabled.stop)

    def test_failure_keeps_last_good_marks_feed_not_ok_and_records_cause(self):
        prior = {"goalies": {"fetched_at_utc": "2026-10-08T18:00:00Z", "games": [{"start_utc": "x", "sides": {}}]}, "goalies_ok": True}

        def fetch(path):
            raise OSError("boom")
        with mock.patch.object(df, "load_state", return_value=prior), mock.patch.object(df, "_save") as save:
            st = df.refresh(NOW, fetch=fetch, force=True)
        self.assertEqual(st["status"], "ERROR")
        self.assertIn("starting-goalies", st["last_error"])
        self.assertEqual(len(st["goalies"]["games"]), 1)                 # the cached copy survives with its own fetch time
        self.assertEqual(st["goalies"]["fetched_at_utc"], "2026-10-08T18:00:00Z")
        self.assertFalse(df.feed_is_fresh(st, NOW))
        save.assert_called_once()

    def test_successful_fetch_stamps_the_fetch_time_and_feed_is_fresh(self):
        def fetch(path):
            if path.startswith("/starting-goalies"):
                return html(raw_game())
            raise OSError("lines down")
        with mock.patch.object(df, "load_state", return_value={}), mock.patch.object(df, "_save"):
            st = df.refresh(NOW, fetch=fetch, force=True)
        self.assertEqual(st["goalies"]["fetched_at_utc"], "2026-10-08T20:00:00Z")
        self.assertTrue(df.feed_is_fresh(st, NOW))
        self.assertFalse(df.feed_is_fresh(st, NOW + dt.timedelta(minutes=120)))

    def test_not_due_means_no_request(self):
        prior = {"goalies": {"fetched_at_utc": "2026-10-08T19:55:00Z", "games": []}, "lines": {"fetched_at_utc": "2026-10-08T19:00:00Z", "teams": {}}}
        fetch = mock.Mock()
        with mock.patch.object(df, "load_state", return_value=prior), mock.patch.object(df, "_save"):
            df.refresh(NOW, fetch=fetch)
        fetch.assert_not_called()

    def test_off_switch_makes_no_request_even_when_forced(self):
        fetch = mock.Mock()
        with mock.patch.object(df, "enabled", return_value=False), mock.patch.object(df, "load_state", return_value={}):
            st = df.refresh(NOW, fetch=fetch, force=True)
        self.assertEqual(st["status"], "DISABLED")
        self.assertIn("terms", st["disabled_reason"])
        fetch.assert_not_called()

    def test_default_is_off_until_the_owner_opts_in(self):
        self.enabled.stop()
        with mock.patch.dict("os.environ", {}, clear=False), mock.patch.object(df, "_env_value", return_value=None):
            self.assertFalse(df.enabled())
        with mock.patch.object(df, "_env_value", return_value="ON"):
            self.assertTrue(df.enabled())
        self.enabled.start()


def make_db():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript((Path(__file__).parent.parent / "schema.sql").read_text())
    c.execute("INSERT INTO games (game_id, scheduled_start_utc, home_team, away_team, game_state) VALUES (1, '2026-10-08T23:00:00', 'PHI', 'OTT', 'SCHEDULED')")
    c.execute("INSERT INTO players VALUES ('100', 'Joseph Woll', 'G'), ('101', 'Linus Ullmark', 'G'), ('102', 'Dan Vladar', 'G')")
    return c


class TestIngestAndGate(unittest.TestCase):
    def state(self, home_extra=None, away_extra=None):
        home = {"team_name": "Philadelphia Flyers", "goalie": "Joseph Woll", "status_word": "Confirmed", "details": "Woll will start tonight.",
                "source_name": "Philadelphia Flyers", "source_url": "https://x.com/NHLFlyers/status/1", "news_at_utc": "2026-10-08T17:00:00Z", **(home_extra or {})}
        away = {"team_name": "Ottawa Senators", "goalie": "Linus Ullmark", "status_word": "Confirmed", "details": "Ullmark will start.",
                "source_name": "Some Blogger", "source_url": "https://x.com/someblogger/status/2", "news_at_utc": "2026-10-08T17:05:00Z", **(away_extra or {})}
        return {"teams": {"Philadelphia Flyers": "PHI", "Ottawa Senators": "OTT"}, "goalies_ok": True,
                "goalies": {"fetched_at_utc": "2026-10-08T19:50:00Z", "games": [{"start_utc": "2026-10-08T23:00:00.000Z", "sides": {"home": home, "away": away}}]}}

    def setUp(self):
        self.c = make_db()
        for target, rv in ((df, "load_recognized"), (gc, "_auto_feed_ok")):
            patcher = mock.patch.object(target, rv, return_value=REC if rv == "load_recognized" else True)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_ingest_writes_source_link_time_and_basis_once(self):
        st = self.state()
        self.assertEqual(df.ingest_goalie_status(self.c, st, NOW), {"written": 2, "unmatched": 0})
        self.assertEqual(df.ingest_goalie_status(self.c, self.state(), NOW)["written"], 0)
        row = self.c.execute("SELECT * FROM goalie_status_events WHERE team_id = 'PHI'").fetchone()
        self.assertEqual(row["status"], "CONFIRMED")
        self.assertIn("|TEAM_POST|https://x.com/NHLFlyers/status/1|Philadelphia Flyers", row["source"])
        self.assertEqual(row["effective_at_utc"], "2026-10-08T17:00:00")
        found = gc.lookup(self.c, 1, "PHI", "100", NOW)
        self.assertEqual((found["status"], found["basis"], found["automated"]), ("CONFIRMED", "TEAM_POST", True))
        self.assertEqual(found["source_url"], "https://x.com/NHLFlyers/status/1")
        self.assertIsNone(gc.lookup(self.c, 1, "OTT", "101", NOW))              # unrecognized source: not a confirmation
        self.assertEqual(len(gc.confirmed_observations(self.c, 1, "PHI", NOW)), 1)
        self.assertEqual(gc.confirmed_observations(self.c, 1, "OTT", NOW), [])

    def test_recognized_reporter_confirmation_opens_the_gate(self):
        st = self.state(away_extra={"source_name": "Andrew Wilimek", "source_url": "https://x.com/AndrewWilimek/status/2"})
        df.ingest_goalie_status(self.c, st, NOW)
        found = gc.lookup(self.c, 1, "OTT", "101", NOW)
        self.assertEqual((found["status"], found["basis"]), ("CONFIRMED", "RECOGNIZED_REPORTER"))
        self.assertEqual(len(gc.confirmed_observations(self.c, 1, "OTT", NOW)), 1)

    def test_a_dead_feed_withdraws_automated_confirmations(self):
        df.ingest_goalie_status(self.c, self.state(), NOW)
        with mock.patch.object(gc, "_auto_feed_ok", return_value=False):
            self.assertIsNone(gc.lookup(self.c, 1, "PHI", "100", NOW))
        stale = self.state()
        stale["goalies"]["fetched_at_utc"] = "2026-10-08T15:00:00Z"
        self.assertEqual(df.ingest_goalie_status(self.c, stale, NOW)["skipped"], "FEED_NOT_FRESH")

    def test_stale_confirmation_stops_counting(self):
        df.ingest_goalie_status(self.c, self.state(), NOW)
        with mock.patch.object(gc, "_auto_feed_ok", return_value=True):
            self.assertIsNotNone(gc.lookup(self.c, 1, "PHI", "100", NOW))
            self.assertIsNone(gc.lookup(self.c, 1, "PHI", "100", NOW + dt.timedelta(hours=40)))

    def test_a_later_report_naming_another_goalie_is_a_conflict(self):
        df.ingest_goalie_status(self.c, self.state(), NOW)
        later = self.state(home_extra={"goalie": "Dan Vladar", "status_word": "Likely", "details": "Vladar in the crease.",
                                       "source_name": "Someone", "source_url": "https://x.com/someone/status/3"})
        later["goalies"]["fetched_at_utc"] = "2026-10-08T20:00:00Z"
        later_now = NOW + dt.timedelta(minutes=10)
        df.ingest_goalie_status(self.c, later, later_now)
        with mock.patch.object(gc, "_auto_feed_ok", return_value=True):
            found = gc.lookup(self.c, 1, "PHI", "100", later_now)
            self.assertEqual(found["status"], "CONFLICT")
            self.assertEqual(gc.confirmed_observations(self.c, 1, "PHI", later_now), [])
            self.assertEqual(gc.confirmed_games(self.c, later_now), {})

    def test_an_expectation_naming_the_same_goalie_does_not_walk_back_a_confirmation(self):
        df.ingest_goalie_status(self.c, self.state(), NOW)
        again = self.state(home_extra={"status_word": "Likely", "source_url": "https://x.com/NHLFlyers/status/9"})
        df.ingest_goalie_status(self.c, again, NOW + dt.timedelta(minutes=30))
        with mock.patch.object(gc, "_auto_feed_ok", return_value=True):
            self.assertEqual(gc.lookup(self.c, 1, "PHI", "100", NOW + dt.timedelta(minutes=30))["status"], "CONFIRMED")

    def test_started_game_is_never_written(self):
        self.c.execute("UPDATE games SET game_state = 'LIVE'")
        self.assertEqual(df.ingest_goalie_status(self.c, self.state(), NOW)["written"], 0)

    def test_confirmed_games_lists_games_with_a_usable_confirmation(self):
        df.ingest_goalie_status(self.c, self.state(), NOW)
        with mock.patch.object(gc, "_auto_feed_ok", return_value=True):
            self.assertEqual(gc.confirmed_games(self.c, NOW), {"1": ["PHI"]})

    def test_source_record_scores_a_reporters_confirmations_against_the_actual_starter(self):
        st = self.state(away_extra={"source_name": "Andrew Wilimek", "source_url": "https://x.com/AndrewWilimek/status/2"})
        df.ingest_goalie_status(self.c, st, NOW)
        self.c.execute("INSERT INTO goalie_game_stats (game_id, player_id, team_id, started) VALUES (1, '102', 'OTT', 1)")   # a different goalie started
        rec = df.source_record(self.c)
        self.assertEqual(rec["andrewwilimek"]["resolved"], 1)
        self.assertEqual(rec["andrewwilimek"]["right"], 0)
        self.assertEqual(rec["nhlflyers"]["resolved"], 0) if "nhlflyers" in rec else None


class TestObservedLineup(unittest.TestCase):
    def test_maps_by_name_and_keeps_groups_separate(self):
        t = df.parse_line_combinations(page("dailyfaceoff_line_combinations.json"))
        state = {"lines": {"teams": {"OTT": t}}}
        players = {n["name"]: {"name": n["name"], "team": "OTT"} for g in ("f1", "pp1", "d1") for n in t["groups"][g]}
        got = df.observed_lineup(state, "OTT", players, NOW)
        first_f1 = t["groups"]["f1"][0]["name"]
        self.assertEqual(got[first_f1]["line"], "F1")
        self.assertTrue(any(r["pp"] == "PP1" for r in got.values()))
        self.assertIn("status", got[first_f1]["report"])


if __name__ == "__main__":
    unittest.main()
