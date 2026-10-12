"""Scoring the frozen prediction log: a moneyline is a TEAM outcome (never a player lookup), corrections keep an audit trail, gaps are stated not filled, and the log is written whether
automatic recording is paused, resumed or finds no ticket."""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from operational import daily_tickets as dtk
from operational import recording_pause as rp
from operational import shadow_selection as sh
from operational import state_paths
from tests.test_daily_tickets import NOW, board, collected, fresh_ledger
from tests.test_paper_bet_settlement_driver import _fresh_nhl_conn, _insert_game, _insert_player_stat

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))
import score_selection_shadow as sss  # noqa: E402


def path_of(conn) -> Path:
    return Path(conn.execute("PRAGMA database_list").fetchone()[2])


class TestMoneylineIsATeamOutcome(unittest.TestCase):
    def setUp(self):
        self.conn = _fresh_nhl_conn()
        _insert_game(self.conn, 1, home="TOR", away="MTL", state="FINAL", home_score=4, away_score=2)
        _insert_game(self.conn, 2, home="CAR", away="FLA", state="FINAL", home_score=2, away_score=3)          # an away win (could be a shootout)
        _insert_game(self.conn, 3, home="TOR", away="FLA", state="SCHEDULED", home_score=None, away_score=None)
        _insert_game(self.conn, 4, home="MTL", away="CAR", state="FINAL", home_score=None, away_score=None)       # final with no score recorded
        self.resolve = sss.db_resolver(path_of(self.conn))

    def test_the_winner_hits_the_loser_misses_and_nobody_is_called_did_not_play(self):
        self.assertEqual(self.resolve(1, "TOR", "MONEYLINE", None), {"status": "FINAL", "hit": True})
        self.assertEqual(self.resolve(1, "MTL", "MONEYLINE", None), {"status": "FINAL", "hit": False})
        self.assertEqual(self.resolve(2, "FLA", "MONEYLINE", None), {"status": "FINAL", "hit": True})           # the away side winning
        self.assertNotEqual(self.resolve(1, "TOR", "MONEYLINE", None)["status"], "NO_PARTICIPATION")

    def test_an_unfinished_game_is_pending_and_missing_evidence_stays_unresolved(self):
        self.assertEqual(self.resolve(3, "TOR", "MONEYLINE", None)["status"], "PENDING")
        self.assertEqual(self.resolve(4, "MTL", "MONEYLINE", None)["status"], "UNRESOLVED")                    # no official score: never guessed
        self.assertEqual(self.resolve(1, "BOS", "MONEYLINE", None)["status"], "UNRESOLVED")                    # a team that was not in the game

    def test_player_legs_are_still_graded_from_player_stats(self):
        _insert_player_stat(self.conn, 1, "p1", "TOR", shots=3)
        self.assertEqual(self.resolve(1, "p1", "PLAYER_SOG_ALTERNATE", 2), {"status": "FINAL", "hit": True})
        self.assertEqual(self.resolve(1, "p2", "PLAYER_SOG_ALTERNATE", 2)["status"], "NO_PARTICIPATION")

    def test_the_score_report_separates_moneylines_from_players_who_did_not_play(self):
        _insert_player_stat(self.conn, 1, "p1", "TOR", shots=3)
        row = lambda gid, pid, fam, k, price, p: [str(gid), pid, fam, k, price, p, pid, "T", "2026-10-15T23:00:00Z", "2026-10-15T19:00:00Z", True]  # noqa: E731
        rec = {"et_date": "2026-10-15", "pool_fields": [], "variants": {}, "pool": [row(1, "TOR", "MONEYLINE", None, -150, 0.55), row(1, "MTL", "MONEYLINE", None, 130, 0.42),
                                                                                    row(1, "p1", "PLAYER_SOG_ALTERNATE", 2, -150, 0.6), row(1, "p9", "PLAYER_SOG_ALTERNATE", 2, -150, 0.6),
                                                                                    row(1, "p9", "PLAYER_SOG_ALTERNATE", 3, 150, 0.3)]}
        rep = sss.score([rec], self.resolve)
        self.assertEqual(rep["scorer_revision"], 2)
        self.assertEqual(rep["moneyline_resolved"], 2)
        self.assertEqual(rep["moneyline_legs"]["happened"], 0.5)                                    # one side won, one lost
        self.assertEqual((rep["resolved"], rep["players_who_did_not_play"], rep["player_did_not_play"]), (1, 1, 2))   # p1 graded; p9's two lines are ONE player who did not play
        self.assertEqual(rep["all_priced_legs"]["all"]["legs"], 1)                                  # the prop tables keep their meaning


class TestGapsAreStatedNotFilled(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(state_paths, "path", lambda n, **kw: Path(self.tmp.name) / n)
        p.start()
        self.addCleanup(p.stop)

    def test_a_known_gap_is_written_down_and_shows_on_the_days_it_covers_without_any_prediction_being_added(self):
        sh.note_gap("2026-10-11T15:29:23Z", "2026-10-12T00:42:15Z", "LOGGER_NOT_WRITTEN_WHILE_RECORDING_ON", "the log was written only while paused", now=NOW)
        self.assertEqual([g["kind"] for g in sh.gaps_on("2026-10-11")], ["LOGGER_NOT_WRITTEN_WHILE_RECORDING_ON"])
        self.assertEqual(sh.gaps_on("2026-10-12"), [])                           # it ends at 8:42 PM ET on the 11th
        self.assertEqual(sh.gaps_on("2026-10-13"), [])
        self.assertEqual(sh.read_all(), [])                                      # nothing was manufactured into the prediction log

    def test_a_long_daytime_silence_is_noted_automatically_and_an_overnight_one_is_not(self):
        sp = state_paths.path(sh.STATE)
        sh._touch(sp, dt.datetime(2026, 10, 12, 12, 0, tzinfo=dt.timezone.utc))                                  # 08:00 ET
        sh._touch(sp, dt.datetime(2026, 10, 12, 12, 15, tzinfo=dt.timezone.utc))                                 # a normal 15-minute cycle: no gap
        self.assertEqual(sh.gaps(), [])
        sh._touch(sp, dt.datetime(2026, 10, 12, 16, 0, tzinfo=dt.timezone.utc))                                  # four hours of daytime silence
        self.assertEqual([g["kind"] for g in sh.gaps()], ["NO_CHECKS"])
        sh._touch(sp, dt.datetime(2026, 10, 13, 11, 0, tzinfo=dt.timezone.utc))                                  # 16:00Z -> next morning: mostly night, plus 12:00-23:00 ET of the 12th
        kinds = [g["kind"] for g in sh.gaps()]
        self.assertEqual(kinds.count("NO_CHECKS"), 2)                                                            # that stretch included daytime hours (12:00-23:00 ET)

    def test_a_pure_overnight_silence_is_not_a_gap(self):
        sp = state_paths.path(sh.STATE)
        sh._touch(sp, dt.datetime(2026, 10, 13, 3, 30, tzinfo=dt.timezone.utc))                                  # 23:30 ET
        sh._touch(sp, dt.datetime(2026, 10, 13, 11, 59, tzinfo=dt.timezone.utc))                                 # 07:59 ET
        self.assertEqual(sh.gaps(), [])


class TestLoggingContinuesInEveryRecordingState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        p = mock.patch.object(state_paths, "path", lambda n, **kw: d / n)
        p.start()
        self.addCleanup(p.stop)
        self.path, self.conn = fresh_ledger()
        self.addCleanup(self.conn.close)

    def run_(self, legs, minutes=0):
        return dtk.run_cycle(None, self.conn, NOW + dt.timedelta(minutes=minutes), collected=collected(legs))

    def test_paused_the_log_is_written_and_the_proposed_tickets_are_marked_unrecorded(self):
        rp.pause("test", now=NOW)
        res = self.run_(board(8))
        recs = sh.read_all()
        self.assertEqual((res["newly_recorded"], len(recs)), (0, 1))
        self.assertFalse(recs[0]["decisions"]["selected_tickets_recorded"])

    def test_resumed_the_log_is_written_and_the_recorded_tickets_are_marked_recorded(self):
        res = self.run_(board(8))
        recs = sh.read_all()
        self.assertGreater(res["newly_recorded"], 0)
        self.assertEqual(len(recs), 1)
        self.assertTrue(recs[0]["decisions"]["selected_tickets_recorded"])
        self.assertEqual(len(recs[0]["proposed_tickets"]), res["newly_recorded"])

    def test_resumed_with_no_ticket_the_pool_and_every_leg_decision_are_still_logged_and_every_look_is_stamped(self):
        legs = board(8, price=-300, p=0.55)                                                       # nothing passes: no ticket
        res = self.run_(legs)
        recs = sh.read_all()
        self.assertEqual(res["newly_recorded"], 0)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["proposed_tickets"], [])
        decisions = {row[recs[0]["pool_fields"].index("decision")] for row in recs[0]["pool"]}
        self.assertTrue(decisions)                                                                # every priced leg carries a decision, tickets or not
        self.run_(legs, minutes=15)                                                                # an unchanged pool is not rewritten ...
        self.assertEqual(len(sh.read_all()), 1)
        self.assertEqual(json.loads(state_paths.path(sh.STATE).read_text())["checked_at_utc"], (NOW + dt.timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ"))   # ... but the look is stamped

    def test_with_no_fresh_legs_at_all_the_look_is_stamped(self):
        self.run_([])
        self.assertEqual(sh.read_all(), [])
        self.assertIn("checked_at_utc", json.loads(state_paths.path(sh.STATE).read_text()))


if __name__ == "__main__":
    unittest.main()
