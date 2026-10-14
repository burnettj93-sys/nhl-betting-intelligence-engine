"""Watchdog: reconciliation of the model book, settlement backlog, source freshness, and product readiness kept apart from operational health."""
import datetime as dt
import json
import unittest
from unittest import mock
from pathlib import Path

from operational import paper_bankroll as pb
from operational import watchdog as wd
from research.real_market_parlay import engine as rmp
from tests.test_daily_tickets import NOW, board, fresh_ledger


class TestReconciliation(unittest.TestCase):
    def setUp(self):
        self.path, self.ledger = fresh_ledger()

    def test_empty_books_agree(self):
        self.assertEqual(wd.check_reconciliation(self.ledger)["status"], wd.OK)

    def test_the_model_account_disagreeing_with_its_rows_fails(self):
        self.assertEqual(wd.check_reconciliation(self.ledger)["status"], wd.OK)
        orig = pb.account_state
        pb.account_state = lambda conn, track: {**orig(conn, track), "available_cash": 1.0}
        try:
            r = wd.check_reconciliation(self.ledger)
        finally:
            pb.account_state = orig
        self.assertEqual(r["status"], wd.FAIL)
        self.assertIn("available_cash", r["detail"])




class TestSettlementBacklog(unittest.TestCase):
    def setUp(self):
        self.path, self.ledger = fresh_ledger()
        self.n = 0

    def add(self, hours_ago):
        """One open automatic model ticket whose game started `hours_ago` hours before NOW."""
        self.n += 1
        start = (NOW - dt.timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
        combo = rmp._evaluate_combo(board(2 + self.n, price=-105, p=0.62))
        res = pb.create_manual_paper_bet(self.ledger, combo, provenance={"order_id": f"ord_wd{self.n:08d}"}, event_start_utc=start, created_at_utc=NOW.isoformat())
        self.ledger.execute("DROP TRIGGER IF EXISTS paper_bets_immutability")                  # test ledger only: relabel the row as an automatic ticket
        self.ledger.execute("UPDATE paper_bets SET origin='AUTOMATIC' WHERE paper_bet_id = ?", (res["paper_bet_id"],))      # the helper writes the retired origin; the check only counts automatic rows
        self.ledger.commit()

    def test_levels(self):
        self.assertEqual(wd.check_settlement_backlog(self.ledger, NOW)["status"], wd.OK)
        self.add(2)                                              # a game still being played is not a backlog
        self.assertEqual(wd.check_settlement_backlog(self.ledger, NOW)["status"], wd.OK)
        self.add(8)
        self.assertEqual(wd.check_settlement_backlog(self.ledger, NOW)["status"], wd.WARN)
        self.add(20)
        r = wd.check_settlement_backlog(self.ledger, NOW)
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


class TestPublishingAndQuotes(unittest.TestCase):
    def test_publishing_switched_off_is_a_failure_not_a_quiet_gap(self):
        with mock.patch("operational.publish_cloud_snapshot.publishing_enabled", return_value=False):
            r = wd.check_publishing_enabled()
        self.assertEqual(r["status"], wd.FAIL)
        self.assertIn("OFF", r["detail"])
        with mock.patch("operational.publish_cloud_snapshot.publishing_enabled", return_value=True):
            self.assertEqual(wd.check_publishing_enabled()["status"], wd.OK)

    def test_stale_quotes_are_a_warning_only_when_a_refresh_was_allowed(self):
        view = lambda state: {"rows": [{"key": "odds_props", "state": state, "age_min": 400.0, "limit_min": 180.0}, {"key": "odds_moneyline", "state": "CURRENT", "age_min": 5, "limit_min": 180}]}
        self.assertEqual(wd.check_quotes(NOW, view("STALE"))["status"], wd.WARN)
        r = wd.check_quotes(NOW, view("BUDGET_LIMITED"))
        self.assertEqual(r["status"], wd.OK)
        self.assertIn("credit budget forbids", r["detail"])
        self.assertEqual(wd.check_quotes(NOW, view("CURRENT"))["status"], wd.OK)


class TestReadinessIsSeparateFromHealth(unittest.TestCase):
    def view(self):
        return {"rows": [{"key": "odds_props", "state": "NOT_DUE", "reason": "3 of 4 games planned", "age_min": None, "limit_min": None}]}

    def test_blocked_features_stay_blocked_whatever_the_machinery_says(self):
        r = wd.readiness(NOW, self.view())
        by = {f["feature"]: f for f in r["features"]}
        self.assertEqual(by["Automatic starting-goalie confirmation"]["status"], "BLOCKED")
        self.assertEqual(by["Goalie-saves tickets"]["status"], "BLOCKED")
        self.assertEqual(by["Puck-line / spread selection"]["status"], "BLOCKED")
        self.assertEqual(by["Odds API key rotation"]["status"], "OWNER_ACTION")
        self.assertIn("does not mean", r["note"])
        self.assertTrue(all(f["owner_action"] for f in r["features"] if f["status"] in ("BLOCKED", "NOT_VERIFIED", "OWNER_ACTION")))


    def test_the_board_is_limited_while_empty_working_when_populated_and_the_evidence_is_logged_once_per_change(self):
        from operational import state_paths
        path = state_paths.path("tickets_state.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        feat = lambda: next(f for f in wd.readiness(NOW, self.view())["features"] if f["feature"].startswith("Best Options board"))
        path.write_text(json.dumps({"options": {"options": [], "generated_at_utc": "2026-10-15T16:00:00Z"}, "tickets": []}))
        self.assertEqual(feat()["status"], "LIMITED")
        path.write_text(json.dumps({"options": {"options": [{"option_id": "O1"}], "generated_at_utc": "2026-10-15T17:00:00Z"}, "tickets": []}))
        self.assertEqual(feat()["status"], "WORKING")
        feat()
        feat()
        log = [json.loads(l) for l in state_paths.path(wd.BOARD_LOG).read_text().splitlines()]
        self.assertEqual([r["options"] for r in log][-2:], [0, 1])               # one line per change, not per run
        self.assertEqual(len([r for r in log if r["options"] == 1]), 1)

    def test_going_from_empty_to_populated_notifies_exactly_once(self):
        sent = []
        with mock.patch("operational.notify.send", side_effect=lambda *a, **k: sent.append(a)):
            self.assertFalse(wd._log_board_evidence(0, 0, None, NOW))
            self.assertTrue(wd._log_board_evidence(3, 0, "t", NOW))
            self.assertFalse(wd._log_board_evidence(3, 0, "t", NOW))
            self.assertFalse(wd._log_board_evidence(4, 0, "t", NOW))                 # more options later: no second notification
        self.assertEqual(len(sent), 1)

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
