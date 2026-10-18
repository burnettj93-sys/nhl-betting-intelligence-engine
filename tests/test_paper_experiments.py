"""Bankroll experiments: the reset to $500 archives the previous experiment intact, starts a clean one, is idempotent, and every account, performance, reconciliation and slot read follows the active
experiment while archived tickets stay readable on their own."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import operational.recording_pause  # noqa: F401
from operational import paper_bankroll as pb
from operational import watchdog as wd

RESET_ID = "2026-10-18-corrected-workflow"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.db = self.dir / "book.db"
        self.conn = pb.init_db(self.db)
        self.addCleanup(self.conn.close)
        p = mock.patch("operational.recording_pause.is_paused", return_value=False)
        p.start()
        self.addCleanup(p.stop)

    def ticket(self, n, odds=200, day="2026-10-12", **kw):
        r = pb.record_paper_bet(self.conn, track="REAL_MARKET_PAPER", price_source="LIVE_DRAFTKINGS", market_id=f"REAL_MARKET_PARLAY:{day}:{n}", entry_odds=odds, is_combo=True,
                                legs_json=json.dumps([{"game_id": f"g{n}", "participant_id": f"p{n}", "market_family": "PLAYER_SOG_ALTERNATE", "threshold": "2"}]), stake=10.0,
                                event_start_utc=f"{day}T23:00:00Z", idempotency_key=f"k{n}-{day}", paper_bet_id=f"T{n}", **kw)
        self.assertEqual(r["status"], "INSERTED", r)
        return r["paper_bet_id"]

    def settle(self, tid, status):
        pb.settle_paper_bet(self.conn, tid, status)

    def reset(self, **kw):
        return pb.start_new_experiment(self.conn, reset_id=RESET_ID, label="Experiment 2: corrected workflow", reason="owner-authorised reset",
                                       archive_dir=self.dir / "archive", backup_dir=self.dir / "backups", policies={"policies": {"PROPOSED": {"digest": "abc"}}}, **kw)

    def old_book(self):
        """Experiment 1 as it ran: 3 losses and 1 win."""
        for i, res in enumerate(("LOSS", "LOSS", "WIN", "LOSS")):
            self.settle(self.ticket(i), res)


class TestFirstExperiment(Base):
    def test_a_fresh_database_starts_experiment_one_active_with_500(self):
        exp = pb.active_experiment(self.conn)
        self.assertEqual((exp["experiment_id"], exp["number"], exp["starting_bankroll"], exp["status"]), ("EXP-1", 1, 500.0, "ACTIVE"))
        self.assertEqual(pb.account_state(self.conn, "REAL_MARKET_PAPER")["available_cash"], 500.0)

    def test_new_tickets_are_stamped_with_the_active_experiment_and_it_is_immutable(self):
        tid = self.ticket(1)
        row = self.conn.execute("SELECT experiment_id FROM paper_bets WHERE paper_bet_id = ?", (tid,)).fetchone()
        self.assertEqual(row["experiment_id"], "EXP-1")
        with self.assertRaises(sqlite3.DatabaseError):
            self.conn.execute("UPDATE paper_bets SET experiment_id = 'EXP-9' WHERE paper_bet_id = ?", (tid,))

    def test_only_one_experiment_can_be_active(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO paper_experiments (experiment_id, number, label, status, starting_bankroll, started_at_utc) VALUES ('EXP-X', 9, 'x', 'ACTIVE', 500, 'now')")


class TestMigration(unittest.TestCase):
    def test_an_older_database_keeps_every_row_and_gets_experiment_one(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "old.db"
        con = pb.init_db(path)
        with mock.patch("operational.recording_pause.is_paused", return_value=False):
            pb.record_paper_bet(con, track="REAL_MARKET_PAPER", price_source="LIVE_DRAFTKINGS", market_id="REAL_MARKET_PARLAY:2026-10-12:a", entry_odds=250, is_combo=True,
                                legs_json="[]", idempotency_key="old1", paper_bet_id="OLD1", created_at_utc="2026-10-07T18:00:00.000000Z")
        before = dict(con.execute("SELECT * FROM paper_bets WHERE paper_bet_id='OLD1'").fetchone())
        # simulate a v4 file: no experiment column, no experiment tables, schema_version 4
        cols = ", ".join(r["name"] for r in con.execute("PRAGMA table_info(paper_bets)") if r["name"] != "experiment_id")
        con.executescript("DROP TRIGGER IF EXISTS paper_bets_immutability; DROP TRIGGER IF EXISTS paper_bets_valid_stake_and_odds; DROP INDEX IF EXISTS paper_experiments_one_active;"
                          f"DROP TABLE paper_experiments; DROP TABLE paper_experiment_resets; ALTER TABLE paper_bets RENAME TO paper_bets_v5; CREATE TABLE paper_bets AS SELECT {cols} FROM paper_bets_v5;"
                          "DROP TABLE paper_bets_v5; UPDATE schema_version SET version = 4;")
        con.commit()
        con.close()
        con = pb.init_db(path)
        self.addCleanup(con.close)
        after = dict(con.execute("SELECT * FROM paper_bets WHERE paper_bet_id='OLD1'").fetchone())
        self.assertEqual(after.pop("experiment_id"), "EXP-1")
        before.pop("experiment_id", None)
        self.assertEqual(after, before)                                                           # not one stored field changed
        self.assertEqual(pb.active_experiment(con)["started_at_utc"], "2026-10-07T18:00:00.000000Z")
        con.close()
        again = pb.init_db(path)                                                                  # a second open (a restart) changes nothing
        self.addCleanup(again.close)
        self.assertEqual(len(pb.experiments(again)), 1)


class TestReset(Base):
    def test_the_reset_starts_a_clean_500_experiment_and_archives_the_old_one_intact(self):
        self.old_book()
        before = pb.account_state(self.conn, "REAL_MARKET_PAPER")
        self.assertEqual(before["available_cash"], 490.0)                                         # three $10 losses and one $10 win at +200 (+$20)
        rows_before = [dict(r) for r in self.conn.execute("SELECT * FROM paper_bets ORDER BY paper_bet_id")]
        out = self.reset()
        self.assertEqual(out["status"], "RESET")
        self.assertEqual((out["from_experiment_id"], out["to_experiment_id"], out["open_tickets_in_previous"]), ("EXP-1", "EXP-2", 0))
        # the new experiment: $500, nothing open, no P&L, no tickets
        now = pb.account_state(self.conn, "REAL_MARKET_PAPER")
        self.assertEqual((now["available_cash"], now["open_stakes"], now["settled_pnl"], now["tickets"], now["starting_bankroll"]), (500.0, 0.0, 0.0, 0, 500.0))
        self.assertEqual(now["experiment"]["experiment_id"], "EXP-2")
        for fn in (pb.bankroll_summary, pb.origin_performance, pb.book_breakdown):
            res = fn(self.conn, "REAL_MARKET_PAPER")
            self.assertTrue(json.dumps(res, default=str))
        self.assertEqual(pb.bankroll_summary(self.conn, "REAL_MARKET_PAPER")["bets"], 0)
        self.assertEqual(pb.origin_performance(self.conn, "REAL_MARKET_PAPER")["ALL"]["tickets"], 0)
        self.assertEqual(pb.query_paper_bets(self.conn, track="REAL_MARKET_PAPER"), [])
        self.assertTrue(pb.book_breakdown(self.conn, "REAL_MARKET_PAPER")["reconciliation"]["all_agree"])
        # the old one is untouched and readable on its own
        rep = pb.experiment_report(self.conn, "EXP-1")
        self.assertEqual(rep["account"]["available_cash"], before["available_cash"])
        self.assertEqual(rep["account"]["tickets"], 4)
        self.assertEqual(rep["experiment"]["status"], "ARCHIVED")
        self.assertEqual(rep["frozen_at_close"]["account"]["available_cash"], before["available_cash"])
        rows_after = [dict(r) for r in self.conn.execute("SELECT * FROM paper_bets ORDER BY paper_bet_id")]
        self.assertEqual(rows_after, rows_before)                                                  # no ticket deleted or rewritten
        self.assertEqual(len(pb.query_paper_bets(self.conn, experiment="ALL")), 4)

    def test_the_archive_file_and_the_database_copy_are_written_before_the_change(self):
        self.old_book()
        out = self.reset()
        archive = json.loads(Path(out["archive_path"]).read_text())
        self.assertEqual(len(archive["tickets"]), 4)
        self.assertEqual(archive["frozen_summary"]["account"]["tickets"], 4)
        self.assertEqual(archive["policy_versions"]["policies"]["PROPOSED"]["digest"], "abc")
        self.assertTrue(archive["audit_log"])
        copy = sqlite3.connect(out["backup_path"])
        self.addCleanup(copy.close)
        self.assertEqual(copy.execute("SELECT COUNT(*) FROM paper_bets").fetchone()[0], 4)
        self.assertEqual(copy.execute("SELECT status FROM paper_experiments").fetchall(), [("ACTIVE",)])      # the copy is the book as it was BEFORE the reset

    def test_applying_the_same_reset_again_changes_nothing(self):
        self.old_book()
        first = self.reset()
        second = self.reset()
        self.assertEqual(second["status"], "ALREADY_APPLIED")
        self.assertEqual(second["to_experiment_id"], first["to_experiment_id"])
        self.assertEqual(len(pb.experiments(self.conn)), 2)
        self.assertEqual(len(list((self.dir / "archive").glob("*.json"))), 1)                      # no second archive
        self.ticket(50, day="2026-10-19")                                                          # a ticket recorded in the new experiment survives a repeated reset
        self.reset()
        self.assertEqual(pb.account_state(self.conn, "REAL_MARKET_PAPER")["tickets"], 1)
        reopened = pb.init_db(self.db)                                                              # a restart / redeploy opens the database again
        self.addCleanup(reopened.close)
        self.assertEqual(pb.active_experiment(reopened)["experiment_id"], "EXP-2")
        self.assertEqual(len(pb.experiments(reopened)), 2)

    def test_open_tickets_stay_open_in_the_old_experiment_and_settle_there(self):
        self.old_book()
        open_id = self.ticket(9, day="2026-10-17")                                                 # still open at the reset
        out = self.reset()
        self.assertEqual(out["open_tickets_in_previous"], 1)
        now = pb.account_state(self.conn, "REAL_MARKET_PAPER")
        self.assertEqual((now["available_cash"], now["open_stakes"], now["open_tickets"]), (500.0, 0.0, 0))      # the open stake is NOT carried into the new bankroll
        self.assertEqual([r["paper_bet_id"] for r in pb.find_unresolved_past_event_bets(self.conn, "REAL_MARKET_PAPER")], [open_id])   # settlement still sees it
        pb.settle_paper_bet(self.conn, open_id, "WIN")
        self.assertEqual(pb.account_state(self.conn, "REAL_MARKET_PAPER")["settled_pnl"], 0.0)       # and its result never lands in the new experiment
        old = pb.experiment_report(self.conn, "EXP-1")
        self.assertEqual(old["account"]["open_tickets"], 0)
        self.assertEqual(old["account"]["tickets"], 5)
        self.assertEqual(old["frozen_at_close"]["open_tickets"], 1)                                # what was open when it closed stays on the record

    def test_new_tickets_go_to_the_new_experiment_with_its_own_funds_and_slots(self):
        self.old_book()
        self.ticket(7, day="2026-10-20")                                                           # an EXP-1 ticket dated the same day as the new one below
        self.reset()
        self.ticket(8, day="2026-10-20")
        self.assertEqual(self.conn.execute("SELECT experiment_id FROM paper_bets WHERE paper_bet_id='T8'").fetchone()[0], "EXP-2")
        acct = pb.account_state(self.conn, "REAL_MARKET_PAPER")
        self.assertEqual((acct["tickets"], acct["open_stakes"], acct["available_cash"]), (1, 10.0, 490.0))
        self.assertEqual(pb.todays_real_parlay_usage(self.conn, "2026-10-20")["count"], 1)         # slots count the active experiment only
        self.assertEqual(pb.query_paper_bets(self.conn, track="REAL_MARKET_PAPER", experiment="EXP-1")[-1]["paper_bet_id"], "T7")

    def test_the_watchdog_reconciliation_follows_the_active_experiment(self):
        self.old_book()
        self.assertEqual(wd.check_reconciliation(self.conn)["status"], wd.OK)
        self.reset()
        self.ticket(8, day="2026-10-20")
        r = wd.check_reconciliation(self.conn)
        self.assertEqual(r["status"], wd.OK, r["detail"])
        self.assertIn("Experiment 2", r["detail"])
        self.assertIn("$490.00", r["detail"])
        self.settle("T8", "LOSS")
        self.assertEqual(wd.check_reconciliation(self.conn)["status"], wd.OK)

    def test_a_ticket_with_an_unknown_experiment_fails_reconciliation(self):
        self.conn.execute("PRAGMA foreign_keys = OFF")
        self.ticket(1)
        self.conn.execute("DROP TRIGGER paper_bets_immutability")
        self.conn.execute("UPDATE paper_bets SET experiment_id = 'EXP-404'")
        self.conn.commit()
        r = wd.check_reconciliation(self.conn)
        self.assertEqual(r["status"], wd.FAIL)
        self.assertIn("unknown experiment", r["detail"])


class TestHostedPages(unittest.TestCase):
    """Today, Paper Performance and Ticket History on the hosted snapshot after the reset: the new experiment's numbers, the archive apart."""

    def snap(self):
        from tests.product_fixture import snapshot
        snap = snapshot()
        exp2 = {"experiment_id": "EXP-2", "number": 2, "label": "Experiment 2: corrected workflow", "status": "ACTIVE", "starting_bankroll": 500.0, "started_at_utc": "2026-10-18T02:00:00Z", "ended_at_utc": None}
        exp1 = {"experiment_id": "EXP-1", "number": 1, "label": "Experiment 1: the original model book", "status": "ARCHIVED", "starting_bankroll": 500.0, "started_at_utc": "2026-10-07T18:00:00Z",
                "ended_at_utc": "2026-10-18T02:00:00Z"}
        fresh = {"track": "REAL_MARKET_PAPER", "starting_bankroll": 500.0, "available_cash": 500.0, "open_stakes": 0.0, "open_tickets": 0, "equity": 500.0, "settled_pnl": 0.0, "tickets": 0, "experiment": exp2}
        old_acct = {**fresh, "available_cash": 439.62, "equity": 439.62, "settled_pnl": -60.38, "tickets": 14, "experiment": exp1}
        snap["tickets"]["account"] = fresh
        perf = snap["performance"]
        perf["account"], perf["experiment"] = fresh, exp2
        perf["bets"] = []
        perf["previous_experiments"] = [{"experiment": exp1, "account": old_acct, "summary": {"roi": -0.43}, "origins": perf["origins"], "breakdowns": {}, "frozen_at_close": None,
                                         "bets": [{"paper_bet_id": "OLD-1", "result_status": "LOSS", "profit_loss": -10.0, "legs_json": "[]", "created_at_utc": "2026-10-08T00:00:00Z", "stake": 10.0,
                                                   "entry_odds": 300, "origin": "AUTOMATIC"}]}]
        return snap

    def test_the_new_experiment_is_what_today_and_paper_performance_show_and_the_old_one_is_apart(self):
        from tests.test_product_pages import run_page, text
        for page in ("21_Today.py", "33_Paper_Performance.py"):
            at = run_page(page, self.snap())
            self.assertEqual(len(at.exception), 0, f"{page}: {[str(e.value)[:200] for e in at.exception]}")
            blob = text(at) + " ".join(m.value for m in at.metric.__iter__()) if hasattr(at, "metric") else text(at)
            self.assertIn("Experiment 2: corrected workflow", blob, page)
            self.assertNotIn("439.62", blob.replace("Final cash", "") if page == "21_Today.py" else "", page)
        at = run_page("33_Paper_Performance.py", self.snap())
        blob = text(at) + " ".join(str(e.label) for e in at.expander)
        self.assertIn("Previous experiments (archived, not included above)", blob)
        self.assertIn("Experiment 1: the original model book", blob)
        self.assertIn("439.62", blob)                                              # the archive's own final cash, shown apart

    def test_ticket_history_offers_the_archived_experiment_without_mixing_it_in(self):
        from tests.test_product_pages import rerun, run_page
        snap = self.snap()
        at = run_page("23_Ledger.py", snap)
        self.assertEqual(len(at.exception), 0, [str(e.value)[:200] for e in at.exception])
        sel = [s for s in at.selectbox if s.key == "th_experiment"]
        self.assertTrue(sel)
        self.assertTrue(sel[0].value.startswith("Current"))
        self.assertTrue(any(o.startswith("Archived") for o in sel[0].options))
        self.assertEqual(len(at.expander), 0)                                      # the current experiment has no tickets yet
        sel[0].select(next(o for o in sel[0].options if o.startswith("Archived")))
        rerun(at, snap)
        self.assertEqual(len(at.expander), 1)                                      # the archived ticket, only after it is chosen


class TestPages(Base):
    def test_the_dashboard_state_shows_only_the_current_experiment_and_lists_the_archive_apart(self):
        from dashboard import paper_performance_view as ppv
        self.old_book()
        self.reset()
        state = ppv.read_dashboard_state(self.conn)
        self.assertEqual(state["account"]["available_cash"], 500.0)
        self.assertEqual(state["bets"], [])
        self.assertEqual(state["experiment"]["experiment_id"], "EXP-2")
        self.assertEqual([p["experiment"]["experiment_id"] for p in state["previous_experiments"]], ["EXP-1"])
        self.assertEqual(len(state["previous_experiments"][0]["bets"]), 4)
        self.assertEqual(state["previous_experiments"][0]["account"]["tickets"], 4)


if __name__ == "__main__":
    unittest.main()
