"""The owner pause on NEW automatic tickets: nothing is selected or recorded while it is on, existing tickets and the account are untouched, the page says so,
and ending the pause restores normal recording."""
from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from operational import daily_tickets as dtk
from operational import paper_bankroll as pb
from operational import recording_pause as rp
from operational import state_paths
from tests.test_daily_tickets import NOW, board, collected, fresh_ledger


class Isolated(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        self.patch = mock.patch.object(state_paths, "path", lambda name, **kw: d / name)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.file = d / rp.NAME
        self.path, self.conn = fresh_ledger()
        self.addCleanup(self.conn.close)


class TestPause(Isolated):
    def test_not_paused_by_default(self):
        self.assertFalse(rp.is_paused())
        self.assertIsNone(rp.notice())

    def test_a_paused_cycle_records_nothing_and_changes_no_ledger_row(self):
        rp.pause("postmortem", now=NOW)
        before = [tuple(r) for r in self.conn.execute("SELECT * FROM paper_bets ORDER BY paper_bet_id")]
        res = dtk.run_cycle(None, self.conn, NOW, collected=collected(board(8)))
        self.assertEqual((res["newly_recorded"], res["recording_paused"]), (0, True))
        self.assertEqual([tuple(r) for r in self.conn.execute("SELECT * FROM paper_bets ORDER BY paper_bet_id")], before)
        acct = pb.account_state(self.conn, "REAL_MARKET_PAPER")
        self.assertEqual((acct["available_cash"], acct["open_stakes"]), (500.0, 0.0))

    def test_the_published_state_says_it_is_paused_and_shows_no_recommended_tickets(self):
        rp.pause("postmortem", now=NOW)
        dtk.run_cycle(None, self.conn, NOW, collected=collected(board(8)))
        st = dtk.read_state()
        self.assertIn("PAUSED", st["notice"])
        self.assertTrue(st["recording_paused"]["paused"])
        self.assertEqual(st["tickets"], [])
        self.assertIn("PAUSED", st["empty_slot_reason"])

    def test_ending_the_pause_restores_normal_recording(self):
        rp.pause("postmortem", now=NOW)
        dtk.run_cycle(None, self.conn, NOW, collected=collected(board(8)))
        was = rp.resume()
        self.assertTrue(was["paused"])
        res = dtk.run_cycle(None, self.conn, NOW + dt.timedelta(minutes=15), collected=collected(board(8)))
        self.assertEqual(res["newly_recorded"], 5)

    def test_existing_tickets_are_untouched_and_still_count_when_paused(self):
        dtk.run_cycle(None, self.conn, NOW, collected=collected(board(8)))
        rows = [tuple(r) for r in self.conn.execute("SELECT * FROM paper_bets ORDER BY paper_bet_id")]
        rp.pause("postmortem", now=NOW)
        dtk.run_cycle(None, self.conn, NOW + dt.timedelta(minutes=15), collected=collected(board(8)))
        self.assertEqual([tuple(r) for r in self.conn.execute("SELECT * FROM paper_bets ORDER BY paper_bet_id")], rows)
        self.assertEqual(len(dtk.read_state()["earlier_open_tickets"]) + len(dtk.read_state()["tickets"]), 5)

    def test_record_tickets_itself_refuses_when_paused(self):
        rp.pause("postmortem", now=NOW)
        out = dtk.record_tickets(self.conn, [object()], NOW)
        self.assertEqual(out[0]["status"], "PAUSED")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM paper_bets").fetchone()[0], 0)

    def test_an_unreadable_pause_file_fails_safe_paused(self):
        self.file.write_text("{not json")
        self.assertTrue(rp.is_paused())

    def test_pause_file_records_who_when_and_why(self):
        doc = rp.pause("losing-streak postmortem", now=NOW)
        on_disk = json.loads(self.file.read_text())
        self.assertEqual((on_disk["paused"], on_disk["reason"], on_disk["since_utc"]), (True, "losing-streak postmortem", NOW.strftime("%Y-%m-%dT%H:%M:%SZ")))
        self.assertEqual(doc, on_disk)


class TestEveryAutomaticWriterIsCovered(Isolated):
    """The moneyline pre-game job and the prop jobs write through paper_bankroll.record_paper_bet, not through the ticket selector: the pause must stop those too."""

    def single(self, **kw):
        return pb.record_paper_bet(self.conn, track="REAL_MARKET_PAPER", price_source="LIVE_DRAFTKINGS", market_id="MONEYLINE", entry_odds=195.0, event_id="g1", game_date="2026-10-10",
                                   team="CGY", opponent="COL", market_family="MONEYLINE", side="CGY", model_probability=0.49, conservative_probability=0.37,
                                   market_no_vig_probability=0.32, edge=0.05, ev=0.1, model_version="t", prediction_checkpoint="PRIMARY_DAILY", event_start_utc="2026-10-10T23:00:00Z", **kw)

    def test_a_paused_single_leg_automatic_bet_is_refused_and_nothing_is_written(self):
        rp.pause("postmortem", now=NOW)
        res = self.single()
        self.assertEqual(res["status"], "PAUSED")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM paper_bets").fetchone()[0], 0)

    def test_the_same_bet_is_recorded_when_not_paused(self):
        self.assertEqual(self.single()["status"], "INSERTED")
        rp.pause("postmortem", now=NOW)
        self.assertEqual(self.single()["status"], "PAUSED")                      # the same key is refused as paused, never re-staked
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM paper_bets").fetchone()[0], 1)

    def test_the_pause_does_not_touch_the_demo_track_or_personal_accounts(self):
        rp.pause("postmortem", now=NOW)
        res = pb.record_paper_bet(self.conn, track="DEMO_PAPER", price_source="SIMULATED_DEMO", market_id="DEMO", entry_odds=150.0, event_id="d1", game_date="2026-10-10",
                                  team="A", opponent="B", market_family="MONEYLINE", side="A", model_probability=0.5, model_version="t", event_start_utc="2026-10-10T23:00:00Z")
        self.assertNotEqual(res["status"], "PAUSED")


if __name__ == "__main__":
    unittest.main()
