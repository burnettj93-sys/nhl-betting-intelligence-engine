"""
Manual 'Add to paper book -- $10': options per person, order parsing, revalidation, acceptance of changes, exact-$10
recording as MANUALLY_ADDED, duplicate/concurrency safety, shared exposure, separate origin reporting, automatic slots untouched,
and the v4 ledger migration that leaves existing tickets AUTOMATIC and unchanged.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from dashboard import order_client
from operational import daily_tickets as dtk
from operational import manual_orders as mo
from operational import paper_bankroll as pb
from operational import player_options as po
from research.real_market_parlay import engine as rmp
from tests.test_daily_tickets import FUTURE, NOW, board, collected, fresh_ledger, leg

DATE = "2026-10-15"


def accepted_from(opt: dict) -> dict:
    return order_client.build_order(opt, order_id="ord_" + "a" * 12, page_generated_at=NOW.isoformat())["accepted"]


def order_for(opt: dict, oid: str, **kw) -> dict:
    return order_client.build_order(opt, order_id=oid, page_generated_at=NOW.isoformat(), **kw)


class TestOptions(unittest.TestCase):
    def test_single_preferred_when_it_qualifies(self):
        legs = [leg(1, "P1", price=130, p=0.55), leg(2, "P2", price=-110, p=0.60)]
        doc = po.build_options(legs, DATE)
        p1 = doc["persons"]["P1"]
        opt = next(o for o in doc["options"] if o["option_id"] == p1["option_id"])
        self.assertEqual(opt["kind"], po.SINGLE)
        self.assertEqual(opt["price_basis"], po.PRICE_QUOTED)
        self.assertGreaterEqual(opt["combined_decimal"], 2.0)

    def test_parlay_when_no_single_reaches_plus_100(self):
        doc = po.build_options(board(6, price=-120, p=0.62), DATE)
        kinds = {o["kind"] for o in doc["options"]}
        self.assertEqual(kinds, {po.PARLAY})
        for o in doc["options"]:
            self.assertEqual(len(o["legs"]), 2)
            self.assertEqual(len({l["game_id"] for l in o["legs"]}), 2)
            self.assertEqual(o["price_basis"], po.PRICE_ESTIMATED)

    def test_shared_options_are_listed_once_with_everyone_they_serve(self):
        doc = po.build_options(board(2, price=-105, p=0.60), DATE)
        self.assertEqual(len(doc["options"]), 1)
        self.assertEqual(len(doc["options"][0]["best_for"]), 2)

    def test_no_value_means_no_option_and_a_stated_reason(self):
        doc = po.build_options(board(4, price=-110, p=0.45), DATE)
        self.assertEqual(doc["options"], [])
        self.assertTrue(all(p["reason_no_option"] for p in doc["persons"].values()))

    def test_stale_or_started_legs_never_make_options(self):
        legs = [dataclasses.replace(l, price_fresh=False) for l in board(4, price=-105, p=0.62)]
        self.assertEqual(po.build_options(legs, DATE)["options"], [])


class TestRetiredModelBookOrders(unittest.TestCase):
    """The old "add to the paper book" order is retired: it is answered with a stored rejection and the model ledger is never written.
    (Personal logs are tested in tests/test_personal_logs.py; the model book's isolation is proven there.)"""

    def setUp(self):
        self.path, self.conn = fresh_ledger()
        self.legs = board(4, price=-105, p=0.62)
        self.doc = po.build_options(self.legs, DATE)
        self.opt = self.doc["options"][0]

    def process(self, order, legs=None, now=NOW):
        return mo.process_order(self.conn, order, current_legs=legs or self.legs, now=now, source="test:1")

    def test_a_valid_old_order_is_rejected_with_the_pointer_to_personal_logs_and_writes_no_ticket(self):
        row = self.process(order_for(self.opt, "ord_" + "a" * 12))
        self.assertEqual(row["status"], mo.REJECTED)
        self.assertIn("My Bets", row["reason"])
        self.assertEqual(pb.query_paper_bets(self.conn, track="REAL_MARKET_PAPER", origin=None), [])
        self.assertEqual(pb.account_state(self.conn, "REAL_MARKET_PAPER")["tickets"], 0)

    def test_repeating_the_order_reads_the_stored_answer(self):
        o = order_for(self.opt, "ord_" + "b" * 12)
        self.assertEqual(self.process(o), self.process(o))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM manual_orders").fetchone()[0], 1)

    def test_invalid_orders_are_rejected_and_write_nothing(self):
        for bad in ({"type": "PAPER_ORDER"}, "not json", {"schema": 1, "type": "PAPER_ORDER", "order_id": "x"},
                    dict(order_for(self.opt, "ord_" + "c" * 12), accepted={"legs": [], "stake": 10})):
            self.assertEqual(self.process(bad)["status"], mo.REJECTED)
        o = order_for(self.opt, "ord_" + "d" * 12)
        o["accepted"]["stake"] = 25.0
        self.assertEqual(self.process(o)["status"], mo.REJECTED)
        self.assertEqual(pb.query_paper_bets(self.conn, track="REAL_MARKET_PAPER", origin=None), [])

    def test_browsing_writes_nothing(self):
        before = self.conn.execute("SELECT COUNT(*) FROM paper_bets").fetchone()[0]
        po.build_options(self.legs, DATE)
        dtk.build_state(self.conn, NOW, recommended=[], singles=[], empty_reason=None, diagnostics={}, record_results=[], options=self.doc)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM paper_bets").fetchone()[0], before)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM manual_orders").fetchone()[0], 0)

    def test_check_accepted_still_validates_shapes_for_personal_orders(self):
        o = order_for(self.opt, "ord_" + "e" * 12)
        self.assertIsNone(mo.check_accepted(o["accepted"]))
        o["accepted"]["legs"][0]["american_price"] = 50
        self.assertIsNotNone(mo.check_accepted(o["accepted"]))
        same = order_for(self.opt, "ord_" + "f" * 12)
        if len(same["accepted"]["legs"]) == 1:
            same["accepted"]["legs"].append(dict(same["accepted"]["legs"][0], participant_id="Z"))
        else:
            same["accepted"]["legs"][1]["game_id"] = same["accepted"]["legs"][0]["game_id"]
        self.assertIn("same game", mo.check_accepted(same["accepted"]))


class TestLegacyManualRowIsOutsideTheModelBook(unittest.TestCase):
    """A MANUALLY_ADDED row left in the ledger by the earlier feature is kept for audit but counted nowhere in the model book."""

    def setUp(self):
        self.path, self.conn = fresh_ledger()
        self.legs = board(4, price=-105, p=0.62)
        self.combo = rmp._evaluate_combo(self.legs[:2])
        self.res = pb.create_manual_paper_bet(self.conn, self.combo, provenance={"order_id": "ord_legacy0001"}, event_start_utc="2026-10-15T23:30:00Z",
                                              created_at_utc=NOW.isoformat())
        self.assertEqual(self.res["status"], "INSERTED")

    def test_account_exposure_slots_and_performance_ignore_it(self):
        self.assertEqual(pb.account_state(self.conn, "REAL_MARKET_PAPER")["tickets"], 0)
        self.assertEqual(pb.account_state(self.conn, "REAL_MARKET_PAPER")["available_cash"], 500.0)
        self.assertEqual(pb.origin_performance(self.conn)["ALL"]["tickets"], 0)
        self.assertEqual(pb.query_paper_bets(self.conn, track="REAL_MARKET_PAPER"), [])
        self.assertEqual(len(pb.query_paper_bets(self.conn, track="REAL_MARKET_PAPER", origin=None)), 1)      # still there for audit
        self.assertEqual(pb.todays_real_parlay_usage(self.conn, "2026-10-15")["count"], 0)
        self.assertEqual(dtk.recorded_today(self.conn, DATE), [])

    def test_the_settlement_driver_does_not_touch_it(self):
        self.assertEqual(pb.find_unresolved_past_event_bets(self.conn, track="REAL_MARKET_PAPER", include_unresolved=True), [])
        self.assertEqual(pb.find_pending_future_event_bets(self.conn, track="REAL_MARKET_PAPER"), [])

    def test_the_model_book_state_has_no_manual_section(self):
        state = dtk.build_state(self.conn, NOW, recommended=[], singles=[], empty_reason=None, diagnostics={}, record_results=[], options=None)
        self.assertNotIn("manual_tickets", state)
        self.assertEqual(state["account"]["tickets"], 0)

    def test_production_code_has_no_caller_of_the_retired_writer(self):
        import re
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        hits = [str(f.relative_to(root)) for d in ("operational", "dashboard", "research", "deploy") for f in (root / d).rglob("*.py")
                if "create_manual_paper_bet(" in f.read_text() and f.name != "paper_bankroll.py"]
        self.assertEqual(hits, [])


class TestQueue(unittest.TestCase):
    def test_only_owner_issues_are_orders(self):
        import subprocess
        from unittest import mock
        issues = [{"number": 1, "user": {"login": mo.OWNER_LOGIN}, "body": "x\n```json\n{\"a\": 1}\n```\n", "created_at": "t"},
                  {"number": 2, "user": {"login": "stranger"}, "body": "{}", "created_at": "t"},
                  {"number": 3, "user": {"login": mo.OWNER_LOGIN}, "pull_request": {}, "body": "", "created_at": "t"}]
        with mock.patch.object(mo, "_gh", return_value=json.dumps(issues)):
            orders, ignored = mo.fetch_github_orders()
        self.assertEqual([o["issue"] for o in orders], [1])
        self.assertEqual([i["issue"] for i in ignored], [2])


class TestOrderClient(unittest.TestCase):
    def test_prefilled_issue_roundtrips_the_order(self):
        opt = po.build_options(board(2, price=-105, p=0.60), DATE)["options"][0]
        order = order_for(opt, "ord_" + "z" * 12)
        url = order_client.prefilled_issue_url(order)
        self.assertTrue(url.startswith("https://github.com/burnettj93-sys/nhl-betting-intelligence-engine/issues/new?"))
        body = order_client.issue_body(order)
        parsed, err = mo.parse_order(json.loads(body.split("```json\n")[1].split("\n```")[0]))
        self.assertIsNone(err)
        self.assertEqual(parsed["order_id"], order["order_id"])

    def test_direct_write_needs_token_and_listed_email(self):
        class S(dict):
            pass
        ok, tok = order_client.configured_write_access(S(PAPER_ORDER_TOKEN="t", ORDER_ALLOWED_EMAILS="a@x.com, b@x.com"), "B@x.com")
        self.assertTrue(ok and tok == "t")
        self.assertFalse(order_client.configured_write_access(S(PAPER_ORDER_TOKEN="t", ORDER_ALLOWED_EMAILS="a@x.com"), "c@x.com")[0])
        self.assertFalse(order_client.configured_write_access(S(PAPER_ORDER_TOKEN="t"), "a@x.com")[0])
        self.assertFalse(order_client.configured_write_access(S(), None)[0])


class TestOrderPathCheck(unittest.TestCase):
    def doc(self, **kw):
        return order_client.build_path_check(check_id=kw.pop("check_id", "chk_" + "a" * 12), sent_at_utc="2026-10-08T20:00:00Z", via="direct",
                                             signed_in=True, viewer_allowed=True, write_path_configured=True) | kw

    def test_check_is_accepted_once_and_touches_no_ledger(self):
        res = mo.process_path_check(self.doc(), now=NOW, source="github-issue:7", author=mo.OWNER_LOGIN)
        self.assertEqual(res["status"], "ACCEPTED")
        self.assertEqual(mo.process_path_check(self.doc(), now=NOW, source="github-issue:7")["status"], "ALREADY_RECORDED")
        rows = mo.load_path_checks()
        self.assertEqual([r["check_id"] for r in rows], ["chk_" + "a" * 12])
        self.assertTrue(rows[0]["viewer_allowed"] and "No order, ticket or stake" in rows[0]["note"])

    def test_malformed_or_foreign_documents_are_rejected(self):
        for bad in ("{", json.dumps({"type": "PAPER_ORDER", "schema": 1}), json.dumps(self.doc(check_id="x")), json.dumps(self.doc(schema=9))):
            self.assertEqual(mo.process_path_check(bad, now=NOW, source="s")["status"], "REJECTED", bad)

    def test_published_rows_pass_the_snapshot_secret_name_guard(self):
        """A key containing 'token' made the cloud publish fail on 2026-10-08; every key of every stored row must pass the publication validator."""
        from operational import cloud_snapshot_schema as sch
        mo.process_path_check(self.doc(check_id="chk_" + "b" * 12), now=NOW, source="github-issue:9", author=mo.OWNER_LOGIN)
        mo.note_ignored_path_checks([{"issue": 41, "author": "stranger"}], NOW)
        for row in mo.load_path_checks():
            for key in row:
                self.assertIsNone(sch._FORBIDDEN_KEY_RE.search(key), key)

    def test_rows_written_with_the_old_key_are_migrated_on_read(self):
        mo._save_path_checks([{"check_id": "chk_old", "status": "ACCEPTED", "token_configured": True}])
        self.assertEqual(mo.load_path_checks()[0]["write_path_configured"], True)
        self.assertNotIn("token_configured", mo.load_path_checks()[0])

    def test_other_authors_are_listed_not_processed(self):
        mo.note_ignored_path_checks([{"issue": 41, "author": "stranger"}], NOW)
        mo.note_ignored_path_checks([{"issue": 41, "author": "stranger"}], NOW)
        ign = [r for r in mo.load_path_checks() if r["status"] == "IGNORED_AUTHOR"]
        self.assertEqual(len(ign), 1)
        self.assertEqual(ign[0]["author"], "stranger")

    def test_path_status_reports_booleans_and_masks_the_address(self):
        class S(dict):
            pass
        st = order_client.path_status(S(PAPER_ORDER_TOKEN="ghp_SECRETVALUE9", ORDER_ALLOWED_EMAILS="me@x.com"), "Me@X.com")
        self.assertTrue(st["direct_ready"] and st["viewer_allowed"] and st["signed_in"])
        self.assertEqual(st["signed_in_masked"], "m*@x.com")
        self.assertNotIn("SECRETVALUE9", json.dumps(st))
        none = order_client.path_status(S(), None)
        self.assertFalse(none["direct_ready"] or none["signed_in"] or none["write_path_configured"] or none["login_configured"])
        self.assertFalse(order_client.path_status(S(PAPER_ORDER_TOKEN="t", ORDER_ALLOWED_EMAILS="a@x.com"), None)["direct_ready"])

    def test_path_check_uses_its_own_label_and_title(self):
        d = self.doc()
        self.assertEqual(order_client.issue_label(d), "order-path-check")
        self.assertTrue(order_client.issue_title(d).startswith("order-path-check chk_"))


class TestSupportedSignInOnly(unittest.TestCase):
    """Writes are authorised only by a supported sign-in (st.login / OIDC). Streamlit staff: the platform's X-Streamlit-User header is "not a documented or
    stable public API ... should not be relied upon for authentication or user identification"."""

    def test_the_platform_header_never_authorises_a_write(self):
        from unittest import mock
        from dashboard import ui
        headers = mock.Mock()
        headers.get.return_value = "an-opaque-platform-viewer-id"
        user = mock.Mock(is_logged_in=False, email=None)
        with mock.patch.object(ui.st, "context", mock.Mock(headers=headers)), mock.patch.object(ui.st, "user", user):
            self.assertIsNone(ui.signed_in_email())
        self.assertFalse(hasattr(order_client, "fingerprint"))
        self.assertFalse(hasattr(ui, "viewer_id"))
        import inspect
        self.assertNotIn("X-Streamlit-User", inspect.getsource(ui.signed_in_email).split('"""')[2])      # the function body never reads the header

    def test_a_signed_in_viewer_is_identified_by_the_verified_oidc_email(self):
        from unittest import mock
        from dashboard import ui
        with mock.patch.object(ui.st, "user", mock.Mock(is_logged_in=True, email="Me@X.com", email_verified=True)):
            self.assertEqual(ui.signed_in_email(), "me@x.com")
        with mock.patch.object(ui.st, "user", mock.Mock(is_logged_in=True, email="me@x.com", email_verified=False)):
            self.assertIsNone(ui.signed_in_email())
        with mock.patch.object(ui.st, "user", mock.Mock(is_logged_in=True, email=None)):
            self.assertIsNone(ui.signed_in_email())

    def test_login_configuration_is_detected_without_reading_values_out(self):
        full = {"auth": {"redirect_uri": "u", "cookie_secret": "c", "client_id": "i", "client_secret": "s", "server_metadata_url": "m"}}
        self.assertTrue(order_client.login_configured(full))
        self.assertFalse(order_client.login_configured({"auth": {"client_id": "i"}}))
        self.assertFalse(order_client.login_configured({}))
        st = order_client.path_status({**full, "PAPER_ORDER_TOKEN": "ghp_X", "ORDER_ALLOWED_EMAILS": "me@x.com"}, "me@x.com")
        self.assertTrue(st["login_configured"] and st["direct_ready"])
        self.assertNotIn("ghp_X", json.dumps(st))
        self.assertNotIn('"s"', json.dumps(st))

    def test_write_access_needs_both_the_token_and_an_allow_listed_signed_in_email(self):
        class S(dict):
            pass
        self.assertEqual(order_client.configured_write_access(S(PAPER_ORDER_TOKEN="t", ORDER_ALLOWED_EMAILS="a@x.com"), "a@x.com"), (True, "t"))
        self.assertEqual(order_client.configured_write_access(S(PAPER_ORDER_TOKEN="t", ORDER_ALLOWED_EMAILS="a@x.com"), "b@x.com"), (False, None))
        self.assertEqual(order_client.configured_write_access(S(PAPER_ORDER_TOKEN="t", ORDER_ALLOWED_EMAILS="a@x.com"), None), (False, None))
        self.assertEqual(order_client.configured_write_access(S(ORDER_ALLOWED_EMAILS="a@x.com"), "a@x.com"), (False, None))
        self.assertEqual(order_client.configured_write_access(S(PAPER_ORDER_TOKEN="t", ORDER_ALLOWED_VIEWER_IDS="abc"), None), (False, None))   # the retired viewer-id list is ignored


class TestMigration(unittest.TestCase):
    def test_v3_ledger_migrates_without_rewriting_tickets(self):
        tmp = Path(tempfile.mkdtemp()) / "old.db"
        # a v3 ledger: the current table definition minus the two v4 columns, the old trigger, version 3
        ref = pb.init_db(Path(tempfile.mkdtemp()) / "ref.db")
        ddl = ref.execute("SELECT sql FROM sqlite_master WHERE name = 'paper_bets'").fetchone()[0]
        ref.close()
        ddl = ddl[:ddl.index("settlement_json")] + "settlement_json TEXT\n)"
        self.assertNotIn("origin", ddl)
        raw = sqlite3.connect(tmp)
        raw.executescript(ddl + ";CREATE TABLE schema_version (version INTEGER PRIMARY KEY);INSERT INTO schema_version VALUES (3);"
                          "CREATE TABLE paper_audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp_utc TEXT NOT NULL, paper_bet_id TEXT NOT NULL, action TEXT NOT NULL);")
        for i in range(3):
            raw.execute("INSERT INTO paper_bets (paper_bet_id, idempotency_key, track, market_id, price_source, entry_odds, stake, created_at_utc) "
                        "VALUES (?, ?, 'REAL_MARKET_PAPER', ?, 'LIVE_DRAFTKINGS', ?, 10, '2026-10-01T00:00:00Z')", (f"T{i}", f"k{i}", f"REAL_MARKET_PARLAY:{DATE}:{i}", 120 + i))
        raw.commit()
        raw.row_factory = sqlite3.Row
        before = [dict(r) for r in raw.execute("SELECT * FROM paper_bets ORDER BY paper_bet_id")]
        raw.close()
        conn = pb.init_db(tmp)
        after = [dict(r) for r in conn.execute("SELECT * FROM paper_bets ORDER BY paper_bet_id")]
        self.assertEqual(len(after), 3)
        for b, a in zip(before, after):
            self.assertEqual((a.pop("origin"), a.pop("provenance_json")), ("AUTOMATIC", None))
            self.assertEqual(a, b)
        self.assertEqual(conn.execute("SELECT version FROM schema_version").fetchone()[0], pb.SCHEMA_VERSION)
        with self.assertRaises(sqlite3.DatabaseError):
            conn.execute("UPDATE paper_bets SET origin = 'MANUALLY_ADDED' WHERE paper_bet_id = 'T0'")


if __name__ == "__main__":
    unittest.main()
