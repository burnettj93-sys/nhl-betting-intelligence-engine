"""Daily Faceoff reader: parsing saved real pages, source-kind gating of CONFIRMED, idempotent ingest, reported lineups."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import unittest
from pathlib import Path
from unittest import mock

from operational import dailyfaceoff as df
from operational import goalie_confirmations

FIX = Path(__file__).parent / "fixtures"
NOW = dt.datetime(2026, 10, 8, 20, 0, tzinfo=dt.timezone.utc)


def page(name):
    return json.loads((FIX / name).read_text())


def html(pageprops):
    return '<html><script id="__NEXT_DATA__" type="application/json">' + json.dumps({"props": {"pageProps": pageprops}}) + "</script></html>"


class TestParsing(unittest.TestCase):
    def test_reporter_sourced_confirmed_is_only_an_expectation(self):
        g = df.parse_starting_goalies(page("dailyfaceoff_starting_goalies.json"))
        ott = g["games"][0]["sides"]["home"]
        self.assertEqual(ott["status_word"], "Confirmed")
        self.assertEqual(ott["source_kind"], df.SOURCE_REPORTER)
        self.assertEqual(ott["status"], "EXPECTED")

    def test_reporter_confirmations_are_accepted_only_by_owner_switch(self):
        with mock.patch.dict("os.environ", {df.REPORTER_ENV: "ON"}):
            ott = df.parse_starting_goalies(page("dailyfaceoff_starting_goalies.json"))["games"][0]["sides"]["home"]
        self.assertEqual((ott["source_kind"], ott["status"]), (df.SOURCE_REPORTER, "CONFIRMED"))

    def test_team_sourced_confirmed_counts(self):
        raw = {"date": "2026-10-08", "data": [{"dateGmt": "2026-10-08T23:00:00.000Z", "homeGoalieName": "A B", "homeTeamName": "Philadelphia Flyers",
                                               "homeNewsStrengthName": "Confirmed", "homeNewsSourceName": "Philadelphia Flyers",
                                               "homeNewsSourceUrl": "https://x.com/NHLFlyers/status/1", "homeNewsCreatedAt": "2026-10-08T17:00:00Z"}]}
        side = df.parse_starting_goalies(raw)["games"][0]["sides"]["home"]
        self.assertEqual((side["source_kind"], side["status"]), (df.SOURCE_TEAM, "CONFIRMED"))
        raw["data"][0]["homeNewsStrengthName"] = "Likely"
        self.assertEqual(df.parse_starting_goalies(raw)["games"][0]["sides"]["home"]["status"], "EXPECTED")

    def test_line_combinations_groups_and_reporter(self):
        t = df.parse_line_combinations(page("dailyfaceoff_line_combinations.json"))
        self.assertEqual(t["team"], "OTT")
        for g in ("f1", "f4", "d1", "d3", "pp1", "pp2"):
            self.assertIn(g, t["groups"])
        self.assertTrue(t["reported_by"] and t["updated_at_utc"])

    def test_layout_change_is_an_error_not_silence(self):
        with self.assertRaises(ValueError):
            df.next_data("<html>no data</html>")


class TestRefresh(unittest.TestCase):
    def test_failure_keeps_last_good_and_records_cause(self):
        calls = []

        def fetch(path):
            calls.append(path)
            if path.startswith("/starting-goalies"):
                return html(page("dailyfaceoff_starting_goalies.json"))
            raise OSError("boom")

        with mock.patch.object(df, "load_state", return_value={}), mock.patch.object(df, "_save") as save:
            st = df.refresh(NOW, fetch=fetch, force=True)
        self.assertEqual(st["status"], "ERROR")
        self.assertIn("line-combinations", st["last_error"])
        self.assertEqual(len(st["goalies"]["games"]), 4)
        save.assert_called_once()

    def test_not_due_means_no_request(self):
        prior = {"goalies": {"fetched_at_utc": "2026-10-08T19:55:00Z", "games": []}, "lines": {"fetched_at_utc": "2026-10-08T19:00:00Z", "teams": {}}}
        fetch = mock.Mock()
        with mock.patch.object(df, "load_state", return_value=prior), mock.patch.object(df, "_save"):
            df.refresh(NOW, fetch=fetch)
        fetch.assert_not_called()

    def test_kill_switch(self):
        fetch = mock.Mock()
        with mock.patch.dict("os.environ", {df.SWITCH_ENV: "OFF"}), mock.patch.object(df, "load_state", return_value={}):
            st = df.refresh(NOW, fetch=fetch, force=True)
        self.assertEqual(st["status"], "DISABLED")
        fetch.assert_not_called()


class TestIngestAndGate(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(":memory:")
        self.c.row_factory = sqlite3.Row
        self.c.executescript((Path(__file__).parent.parent / "schema.sql").read_text())
        self.c.execute("INSERT INTO games (game_id, scheduled_start_utc, home_team, away_team, game_state) VALUES (1, '2026-10-08T23:00:00', 'PHI', 'OTT', 'SCHEDULED')")
        self.c.execute("INSERT INTO players VALUES ('100', 'Joseph Woll', 'G'), ('101', 'Linus Ullmark', 'G')")
        self.state = {"teams": {"Philadelphia Flyers": "PHI", "Ottawa Senators": "OTT"},
                      "goalies": {"games": [{"start_utc": "2026-10-08T23:00:00.000Z", "sides": {
                          "home": {"team_name": "Philadelphia Flyers", "goalie": "Joseph Woll", "status_word": "Confirmed", "status": "CONFIRMED", "source_kind": "TEAM",
                                   "source_name": "Philadelphia Flyers", "source_url": "https://x.com/NHLFlyers/status/1", "news_at_utc": "2026-10-08T17:00:00Z"},
                          "away": {"team_name": "Ottawa Senators", "goalie": "Linus Ullmark", "status_word": "Confirmed", "status": "EXPECTED", "source_kind": "REPORTER",
                                   "source_name": "A Reporter", "source_url": "https://x.com/r/status/2", "news_at_utc": "2026-10-08T17:05:00Z"}}}]}}

    def test_ingest_writes_once_and_lookup_distinguishes_team_from_reporter(self):
        self.assertEqual(df.ingest_goalie_status(self.c, self.state, NOW), {"written": 2, "unmatched": 0})
        self.assertEqual(df.ingest_goalie_status(self.c, self.state, NOW)["written"], 0)
        found = goalie_confirmations.lookup(self.c, 1, "PHI", "100")
        self.assertEqual(found["status"], "CONFIRMED")
        self.assertTrue(found["automated"])
        self.assertIn("team post", found["source"])
        self.assertIsNone(goalie_confirmations.lookup(self.c, 1, "OTT", "101"))            # reporter-sourced: gate stays closed
        self.assertEqual(len(goalie_confirmations.confirmed_observations(self.c, 1, "PHI")), 1)
        self.assertEqual(goalie_confirmations.confirmed_observations(self.c, 1, "OTT"), [])

    def test_expectation_does_not_walk_back_a_confirmation(self):
        df.ingest_goalie_status(self.c, self.state, NOW)
        self.state["goalies"]["games"][0]["sides"]["home"].update(status="EXPECTED", status_word="Likely", source_url="https://x.com/other/3")
        df.ingest_goalie_status(self.c, self.state, NOW + dt.timedelta(minutes=30))
        self.assertEqual(goalie_confirmations.lookup(self.c, 1, "PHI", "100")["status"], "CONFIRMED")

    def test_started_game_is_never_written(self):
        self.c.execute("UPDATE games SET game_state = 'LIVE'")
        self.assertEqual(df.ingest_goalie_status(self.c, self.state, NOW)["written"], 0)


class TestObservedLineup(unittest.TestCase):
    def test_maps_by_name_and_keeps_groups_separate(self):
        state = {"lines": {"teams": {"OTT": df.parse_line_combinations(page("dailyfaceoff_line_combinations.json"))}}}
        players = {}
        for g in ("f1", "pp1", "d1"):
            for pl in state["lines"]["teams"]["OTT"]["groups"][g]:
                players[pl["name"]] = {"name": pl["name"], "team": "OTT"}
        got = df.observed_lineup(state, "OTT", {n: v for n, v in players.items()})
        self.assertTrue(got)
        first_f1 = state["lines"]["teams"]["OTT"]["groups"]["f1"][0]["name"]
        self.assertEqual(got[first_f1]["line"], "F1")
        self.assertTrue(any(r["pp"] == "PP1" for r in got.values()))
        self.assertTrue(all(r["line"] is None or r["line"][0] in "FD" for r in got.values()))


if __name__ == "__main__":
    unittest.main()
