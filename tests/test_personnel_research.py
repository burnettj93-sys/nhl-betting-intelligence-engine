"""PERSONNEL RESEARCH (operational/personnel_research.py): what is due and when, what the validator accepts and rejects, how a searched-but-empty team is stated, and that a failed search is never read as health."""
from __future__ import annotations

import datetime as dt
import inspect
import os
import tempfile
import unittest
from unittest import mock

from operational import eggy_pick_ledger as epl
from operational import personnel as pe
from operational import personnel_research as pr
from tests.test_personnel import nhl_db

Z = dt.timezone.utc
START = dt.datetime(2026, 10, 28, 23, 30, tzinfo=Z)                    # first game 7:30 PM ET: decision at 22:00Z (6:00 PM ET), MAIN due 21:15Z (5:15 PM ET)
AT = lambda h, m=0: dt.datetime(2026, 10, 28, h, m, tzinfo=Z)


def slate_db():
    db = nhl_db()
    db.execute("CREATE TABLE games (game_id INTEGER, game_date TEXT, scheduled_start_utc TEXT, home_team TEXT, away_team TEXT)")
    db.execute("CREATE TABLE goalie_game_stats (player_id TEXT, team_id TEXT)")
    db.execute("INSERT INTO games VALUES (1, '2026-10-28', '2026-10-28T23:30:00', 'DAL', 'WPG')")
    db.execute("INSERT INTO games VALUES (2, '2026-10-28', '2026-10-29T01:00:00', 'COL', 'NJD')")
    db.execute("INSERT INTO players VALUES ('g1', 'Some Goalie', 'G')")
    db.execute("INSERT INTO goalie_game_stats VALUES ('g1', 'DAL')")
    return db


def item(player="Skater Fwdone", team="DAL", status="OUT", source_at="2026-10-28", url="https://www.example-hockey-news.ca/a", statement=None, **kw):
    return {"player": player, "team": team, "status": status, "source_name": "Example Hockey News", "source_url": url, "source_at_utc": source_at,
            "statement": statement if statement is not None else f"{player} will not play tonight, the coach said.", **kw}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"NHL_ENGINE_STATE_DIR": self.tmp.name})
        self.env.start()
        self.db = slate_db()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def ingest(self, items, now=AT(21, 20), phase="MAIN", teams=("DAL", "WPG", "COL", "NJD"), failed=()):
        return pr.ingest(self.db, {"phase": phase, "teams": list(teams), "search_failed": list(failed), "reports": items}, now)


class TestPlan(Base):
    def action(self, now, reports=None):
        return pr.plan(self.db, now, reports if reports is not None else pe.read_reports())

    def test_the_phases_follow_the_decision_time(self):
        self.assertEqual(self.action(AT(19, 0))["action"], "INITIAL")                               # early: one sweep of the slate
        p = self.action(AT(21, 20))
        self.assertEqual((p["action"], p["teams"]), ("MAIN", ["COL", "DAL", "NJD", "WPG"]))         # about 5:15 PM ET: every team on the slate
        self.assertEqual(p["decision_due_utc"], "2026-10-28T22:00:00Z")
        self.assertEqual(p["main_due_utc"], "2026-10-28T21:15:00Z")

    def test_the_main_research_is_due_forty_five_minutes_before_an_early_decision(self):
        self.db.execute("UPDATE games SET scheduled_start_utc = '2026-10-28T17:00:00' WHERE game_id = 1")  # noon-ish ET game: decision 15:30Z, main 14:45Z
        self.assertEqual(self.action(AT(14, 50))["action"], "MAIN")

    def test_nothing_is_researched_after_the_decision_window_or_without_games(self):
        self.assertIn("PAST_THE_DECISION_WINDOW", self.action(AT(22, 30))["reason"])
        self.assertEqual(self.action(AT(22, 30))["action"], "SKIP")
        self.db.execute("DELETE FROM games")
        self.assertEqual(self.action(AT(19, 0))["reason"], "NO_NHL_GAMES_TODAY")

    def test_a_done_phase_is_not_repeated_and_the_recheck_covers_only_unresolved_material_players(self):
        self.ingest([item("Skater Fwdone", status="GAME_TIME_DECISION", source_at="2026-10-28T20:50:00Z")], now=AT(21, 20))
        p = self.action(AT(21, 30))
        self.assertEqual(p["action"], "SKIP")                                                       # MAIN done, re-check not yet due
        self.assertIn("re-check is not due yet", p["reason"])
        r = self.action(AT(21, 36))
        self.assertEqual(r["action"], "RECHECK")                                                    # 22:00 - 25 min
        self.assertEqual(r["teams"], ["DAL"])
        self.assertEqual([u["name"] for u in r["unresolved"]], ["Skater Fwdone"] if False else [u["name"] for u in r["unresolved"]])
        self.ingest([], now=AT(21, 40), phase="RECHECK", teams=("DAL",))
        self.assertEqual(self.action(AT(21, 45))["action"], "SKIP")

    def test_no_unresolved_player_means_no_recheck(self):
        self.ingest([item("Skater Fwdone", status="OUT", source_at="2026-10-28T20:50:00Z")], now=AT(21, 20))
        self.assertIn("NOTHING_DUE", self.action(AT(21, 40))["reason"])


class TestIngestValidation(Base):
    def rejected(self, bad):
        out = self.ingest([bad])
        self.assertEqual(out["accepted"], [], out)
        return out["rejected"][0]["reason"]

    def test_a_complete_statement_is_stored_with_everything_the_brief_requires(self):
        out = self.ingest([item("Skater Fwdone", designation="lower body", expected_return="day-to-day")])
        self.assertEqual(out["accepted"], [{"player": "Skater Fwdone", "team": "DAL", "status": "OUT"}])
        r = [x for x in pe.read_reports() if x["type"] == "REPORT"][0]
        for k in ("player_id", "player_name", "team", "position", "status", "designation", "expected_return", "source_name", "source_url", "source_at_utc", "recorded_at_utc", "statement", "origin", "phase"):
            self.assertIn(k, r)
        self.assertEqual((r["player_id"], r["position"], r["origin"], r["phase"], r["time_precision"]), ("f1", "F", "RESEARCH", "MAIN", "DATE"))
        self.assertEqual(r["source_at_utc"], "2026-10-28T04:00:00Z")                                # a bare date is the start of that Eastern day, never flattered

    def test_everything_that_is_not_an_explicit_attributable_current_statement_is_rejected(self):
        self.assertIn("statement", self.rejected(item(statement="")))
        self.assertIn("name the player", self.rejected(item(statement="Somebody else will not play tonight, the coach said.")))
        self.assertIn("prohibits automated reading", self.rejected(item(url="https://www.nhl.com/news/x")))
        self.assertIn("prohibits automated reading", self.rejected(item(url="https://www.cbssports.com/nhl/injuries/")))
        self.assertIn("prohibits automated reading", self.rejected(item(url="https://www.espn.com/nhl/x")))
        self.assertIn("link", self.rejected(item(url="")))
        self.assertIn("named source", self.rejected({**item(), "source_name": ""}))
        self.assertIn("not current enough", self.rejected(item(source_at="2026-10-20")))             # an old note is history
        self.assertIn("timestamp", self.rejected(item(source_at="")))
        self.assertIn("status must be one of", self.rejected(item(status="HURT")))
        self.assertIn("not playing today", self.rejected(item(team="BOS")))
        self.assertIn("could not identify", self.rejected(item(player="Nobody Atall")))
        self.assertEqual(pe.read_reports()[-1]["type"], "RESEARCH")                                  # nothing but the run record was stored

    def test_a_goalie_is_refused_here_because_the_goalie_feed_owns_goalies(self):
        self.assertIn("goalie feed", self.rejected(item(player="Some Goalie", statement="Some Goalie is a game-time decision.")))

    def test_a_long_term_absence_stays_current_but_a_day_to_day_note_from_last_week_does_not(self):
        ok = self.ingest([item("Skater Fwdtwo", status="IR", source_at="2026-10-23", statement="Skater Fwdtwo was placed on injured reserve.")])
        self.assertEqual(len(ok["accepted"]), 1)
        self.assertIn("not current enough", self.rejected(item("Skater Fwdthree", status="DAY_TO_DAY", source_at="2026-10-23", statement="Skater Fwdthree is day-to-day.")))


class TestCoverageAndFailure(Base):
    def view(self, team, now=AT(21, 30)):
        return pe.team_view(pe.resolve_all(self.db, now), team)

    def test_a_searched_team_with_nothing_found_says_so_and_is_not_called_healthy(self):
        self.ingest([item("Skater Fwdone")])
        v = self.view("WPG")
        self.assertEqual(v["coverage"], "RESEARCHED")
        self.assertIn("a search result, not a medical clearance", pe.describe(v))
        self.assertNotIn("healthy", pe.describe(v).lower())

    def test_a_failed_search_or_no_run_leaves_the_team_not_available(self):
        self.ingest([], failed=["WPG"])
        self.assertEqual(self.view("WPG")["coverage"], "NONE")
        self.assertIn("NOT AVAILABLE", pe.describe(self.view("WPG")))
        self.assertEqual(self.view("BOS")["coverage"], "NONE")                                      # never searched

    def test_the_research_expires_like_a_sweep_and_an_expected_return_goes_stale_sooner_than_an_active_one(self):
        self.ingest([item("Skater Fwdone", status="EXPECTED_TO_RETURN", source_at="2026-10-28T20:00:00Z", statement="Skater Fwdone is expected to return tonight.")])
        self.assertEqual(self.view("DAL", AT(21, 30))["coverage"], "RESEARCHED")
        self.assertNotEqual(self.view("DAL", AT(21, 30) + dt.timedelta(hours=13))["coverage"], "RESEARCHED")       # the run itself is only good for 12 hours
        r = [x for x in pe.read_reports() if x["type"] == "REPORT"][0]
        self.assertEqual(pe.freshness(r, AT(20, 0) + dt.timedelta(hours=11)), "RECENT")
        self.assertEqual(pe.freshness(r, AT(20, 0) + dt.timedelta(hours=21)), "STALE")

    def test_two_outlets_that_disagree_are_a_conflict_not_a_choice(self):
        self.ingest([item("Skater Fwdone", status="OUT", source_at="2026-10-28T20:00:00Z"),
                     {**item("Skater Fwdone", status="EXPECTED_TO_PLAY", source_at="2026-10-28T20:05:00Z", statement="Skater Fwdone is expected to play tonight."), "source_name": "Other Outlet", "source_url": "https://other.example.org/b"}])
        v = self.view("DAL")
        self.assertEqual([p["conflict"] is not None for p in v["relevant"]], [True])

    def test_eggy_stores_the_statement_and_phase_with_each_item_at_decision_time(self):
        self.ingest([item("Skater Fwdone", status="GAME_TIME_DECISION", source_at="2026-10-28T20:50:00Z")])
        for f in ("statement", "origin", "phase", "time_precision"):
            self.assertIn(f, epl._PERSON_FIELDS)


class TestDecisionRecord(Base):
    def test_the_decision_record_lists_the_research_runs_that_preceded_the_pick(self):
        self.ingest([], now=AT(19, 0), phase="INITIAL")
        self.ingest([item("Skater Fwdone", status="GAME_TIME_DECISION", source_at="2026-10-28T20:50:00Z")], now=AT(21, 20))
        runs = pe.research_runs(pe.read_reports(), AT(21, 40))
        self.assertEqual([r["phase"] for r in runs], ["INITIAL", "MAIN"])
        self.assertIn("DAL", runs[1]["teams"])
        self.assertIn("PICK_UNRESOLVED_GAME_TIME_DECISION", epl.SIGNALS)


class TestNoProhibitedScraper(unittest.TestCase):
    def test_the_research_code_fetches_nothing_and_names_the_prohibited_sites_only_as_exclusions(self):
        src = inspect.getsource(pr) + inspect.getsource(pe)
        for banned in ("import requests", "urlopen", "http.client", "BeautifulSoup", "WebFetch"):
            self.assertNotIn(banned, src)
        for d in ("nhl.com", "espn.com", "dailyfaceoff.com", "rotowire.com", "cbssports.com", "foxsports.com"):
            self.assertIn(d, pr.BLOCKED_DOMAINS)
            self.assertIn(d, pe.PROHIBITED_HOSTS)


if __name__ == "__main__":
    unittest.main()
