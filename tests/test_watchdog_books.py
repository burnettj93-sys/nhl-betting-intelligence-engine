"""Watchdog: reconciliation of both books, settlement backlog, source freshness, and product readiness kept apart from operational health."""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from operational import log_signing
from operational import paper_bankroll as pb
from operational import personal_logs as pl
from operational import watchdog as wd
from research.real_market_parlay import engine as rmp
from tests.test_daily_tickets import NOW, board, fresh_ledger
from tests.test_manual_orders import DATE


def personal_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return pl.connect(tmp.name)


class TestReconciliation(unittest.TestCase):
    def setUp(self):
        self.path, self.ledger = fresh_ledger()
        self.personal = personal_db()

    def test_empty_books_agree(self):
        self.assertEqual(wd.check_reconciliation(self.ledger, self.personal)["status"], wd.OK)

    def test_the_model_account_disagreeing_with_its_rows_fails(self):
        self.assertEqual(wd.check_reconciliation(self.ledger, self.personal)["status"], wd.OK)
        orig = pb.account_state
        pb.account_state = lambda conn, track: {**orig(conn, track), "available_cash": 1.0}
        try:
            r = wd.check_reconciliation(self.ledger, self.personal)
        finally:
            pb.account_state = orig
        self.assertEqual(r["status"], wd.FAIL)
        self.assertIn("available_cash", r["detail"])

    def test_a_personal_log_summary_that_disagrees_with_its_rows_fails(self):
        h = pl.code_hash("qa-watch-code-1")
        self.personal.execute("INSERT INTO logs (log_hash, display_name, created_at_utc, created_by_order, write_pub) VALUES (?,?,?,?,?)", (h, "W", "2026-10-15T00:00:00Z", "crt_" + "a" * 10, "0" * 64))
        orig = pl.summarize
        pl.summarize = lambda bets: {**orig(bets), "bets": 99}
        try:
            self.assertEqual(wd.check_reconciliation(self.ledger, self.personal)["status"], wd.FAIL)
        finally:
            pl.summarize = orig

    def test_a_migrated_ticket_that_no_longer_equals_its_original_fails(self):
        combo = rmp._evaluate_combo(board(2, price=-105, p=0.62))
        res = pb.create_manual_paper_bet(self.ledger, combo, provenance={"order_id": "ord_wd000001"}, event_start_utc="2026-10-15T23:30:00Z", created_at_utc=NOW.isoformat())
        pb.settle_paper_bet(self.ledger, res["paper_bet_id"], "LOSS")
        pl.migrate_legacy_manual(self.personal, self.ledger, NOW)
        self.assertEqual(wd.check_reconciliation(self.ledger, self.personal)["status"], wd.OK)
        self.personal.execute("DROP TRIGGER bets_frozen_entry")
        self.personal.execute("UPDATE bets SET stake = 99")
        self.assertEqual(wd.check_reconciliation(self.ledger, self.personal)["status"], wd.FAIL)


class TestSettlementBacklog(unittest.TestCase):
    def setUp(self):
        self.path, self.ledger = fresh_ledger()
        self.personal = personal_db()
        self.h = pl.code_hash("qa-backlog-code-1")
        self.personal.execute("INSERT INTO logs (log_hash, display_name, created_at_utc, write_pub) VALUES (?,?,?,?)", (self.h, "B", "2026-10-15T00:00:00Z", "0" * 64))

    def add(self, hours_ago, n):
        start = (NOW - dt.timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.personal.execute("INSERT INTO bets (bet_id, log_hash, fingerprint, legs_json, entry_odds, stake, created_at_utc, event_start_utc) VALUES (?,?,?,?,?,?,?,?)",
                              (f"P{n:014d}", self.h, f"f{n}", "[]", 150.0, 10.0, "2026-10-14T00:00:00Z", start))

    def test_levels(self):
        self.assertEqual(wd.check_settlement_backlog(self.ledger, self.personal, NOW)["status"], wd.OK)
        self.add(2, 1)                                           # a game still being played is not a backlog
        self.assertEqual(wd.check_settlement_backlog(self.ledger, self.personal, NOW)["status"], wd.OK)
        self.add(8, 2)
        self.assertEqual(wd.check_settlement_backlog(self.ledger, self.personal, NOW)["status"], wd.WARN)
        self.add(20, 3)
        r = wd.check_settlement_backlog(self.ledger, self.personal, NOW)
        self.assertEqual(r["status"], wd.FAIL)
        self.assertIn("oldest", r["detail"])


class TestSources(unittest.TestCase):
    def view(self, state):
        return {"rows": [{"key": "nhl_schedule", "state": state, "age_min": 3000.0, "limit_min": 1440.0}, {"key": "moneypuck_skater", "state": "CURRENT", "age_min": 5, "limit_min": 2160}]}

    def test_stale_core_source_warns_and_unavailable_fails_but_disabled_feeds_do_not(self):
        self.assertEqual(wd.check_sources(NOW, self.view("CURRENT"))["status"], wd.OK)
        self.assertEqual(wd.check_sources(NOW, self.view("STALE"))["status"], wd.WARN)
        self.assertEqual(wd.check_sources(NOW, self.view("UNAVAILABLE"))["status"], wd.FAIL)
        self.assertEqual(wd.check_sources(NOW, self.view("DISABLED"))["status"], wd.OK)


class TestReadinessIsSeparateFromHealth(unittest.TestCase):
    def view(self):
        return {"rows": [{"key": "odds_props", "state": "NOT_DUE", "reason": "3 of 4 games planned", "age_min": None, "limit_min": None}]}

    def test_blocked_features_stay_blocked_whatever_the_machinery_says(self):
        r = wd.readiness(NOW, personal_db(), self.view())
        by = {f["feature"]: f for f in r["features"]}
        self.assertEqual(by["Automatic starting-goalie confirmation"]["status"], "BLOCKED")
        self.assertEqual(by["Goalie-saves tickets"]["status"], "BLOCKED")
        self.assertEqual(by["Puck-line / spread selection"]["status"], "BLOCKED")
        self.assertEqual(by["Odds API key rotation"]["status"], "OWNER_ACTION")
        self.assertIn("does not mean", r["note"])
        self.assertTrue(all(f["owner_action"] for f in r["features"] if f["status"] in ("BLOCKED", "NOT_VERIFIED", "OWNER_ACTION")))

    def test_one_click_add_is_not_verified_until_an_order_arrives_through_the_apps_own_path(self):
        p = personal_db()
        feat = lambda: next(f for f in wd.readiness(NOW, p, self.view())["features"] if f["feature"].startswith("Personal logs"))
        self.assertEqual(feat()["status"], "NOT_VERIFIED")
        p.execute("INSERT INTO orders (order_id, kind, status, request_json, processed_at_utc) VALUES ('o1','PERSONAL_BET','RECORDED',?,'2026-10-15T01:00:00Z')", (json.dumps({"via": "link"}),))
        self.assertEqual(feat()["status"], "NOT_VERIFIED")            # a link-filed order proves nothing about the app's write credential
        p.execute("INSERT INTO orders (order_id, kind, status, request_json, processed_at_utc) VALUES ('o2','PERSONAL_BET','RECORDED',?,'2026-10-15T02:00:00Z')", (json.dumps({"via": "direct"}),))
        self.assertEqual(feat()["status"], "WORKING")

    def test_run_reports_health_and_readiness_as_separate_parts(self):
        def runner(cmd, cwd=None, timeout=30):
            if cmd[0] == "launchctl":
                return "\n".join(f"-\t0\t{j}" for j in wd.EXPECTED_JOBS)
            if cmd[:2] == ["git", "ls-remote"]:
                return "c" * 40 + "\trefs/heads/master\n"
            return ("c" * 40 + "\n") if cmd[:2] == ["git", "rev-parse"] else ""
        state = wd.run(NOW, runner=runner, notify=False, deep=False)
        self.assertIn("checks", state)
        self.assertIsNone(state["readiness"])                       # deep=False skips the book checks entirely


if __name__ == "__main__":
    unittest.main()
