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


class TestOrderFlow(unittest.TestCase):
    def setUp(self):
        self.path, self.conn = fresh_ledger()
        self.legs = board(4, price=-105, p=0.62)
        self.doc = po.build_options(self.legs, DATE)
        self.opt = self.doc["options"][0]

    def process(self, order, legs=None, now=NOW):
        return mo.process_order(self.conn, order, current_legs=legs or self.legs, now=now, source="test:1")

    def test_records_exactly_one_manual_ten_dollar_ticket(self):
        row = self.process(order_for(self.opt, "ord_" + "1" * 12))
        self.assertEqual(row["status"], mo.RECORDED)
        bets = pb.query_paper_bets(self.conn, track="REAL_MARKET_PAPER")
        self.assertEqual(len(bets), 1)
        b = bets[0]
        self.assertEqual((b["origin"], b["stake"]), ("MANUALLY_ADDED", 10.0))
        self.assertTrue(b["paper_bet_id"].startswith("M"))
        prov = json.loads(b["provenance_json"])
        self.assertEqual(prov["order_id"], "ord_" + "1" * 12)
        self.assertEqual(prov["accepted"]["legs"][0]["american_price"], self.opt["legs"][0]["american_price"])
        self.assertEqual(pb.account_state(self.conn, "REAL_MARKET_PAPER")["available_cash"], 490.0)

    def test_repeat_click_same_order_id_is_answered_from_storage(self):
        order = order_for(self.opt, "ord_" + "2" * 12)
        a, b = self.process(order), self.process(order)
        self.assertEqual((a["status"], b["status"]), (mo.RECORDED, mo.RECORDED))
        self.assertEqual(len(pb.query_paper_bets(self.conn, track="REAL_MARKET_PAPER")), 1)

    def test_second_click_with_a_new_order_id_is_a_duplicate_not_a_second_stake(self):
        self.process(order_for(self.opt, "ord_" + "3" * 12))
        again = self.process(order_for(self.opt, "ord_" + "4" * 12))
        self.assertEqual(again["status"], mo.ALREADY)
        self.assertEqual(len(pb.query_paper_bets(self.conn, track="REAL_MARKET_PAPER")), 1)

    def test_concurrent_orders_for_the_same_bet_record_once(self):
        results = []

        def work(i):
            conn = pb.get_conn(self.path)
            results.append(mo.process_order(conn, order_for(self.opt, f"ord_c{i:011d}"), current_legs=self.legs, now=NOW, source="t"))
            conn.close()
        threads = [threading.Thread(target=work, args=(i,)) for i in range(6)]
        [t.start() for t in threads]; [t.join() for t in threads]
        self.assertEqual(sum(1 for r in results if r["status"] == mo.RECORDED), 1)
        self.assertEqual(len(pb.query_paper_bets(self.conn, track="REAL_MARKET_PAPER")), 1)

    def test_a_bet_the_automatic_tickets_hold_cannot_be_added_again(self):
        combo = rmp._evaluate_combo(self.legs[:2])
        dtk.record_tickets(self.conn, [combo], NOW)
        opt = po.build_options(self.legs, DATE)["options"]
        match = next(o for o in opt if {l["participant_id"] for l in o["legs"]} == {"P1", "P2"})
        row = self.process(order_for(match, "ord_" + "5" * 12))
        self.assertEqual(row["status"], mo.ALREADY)
        self.assertIn("automatic", row["reason"].lower())
        self.assertEqual(len(pb.query_paper_bets(self.conn, track="REAL_MARKET_PAPER")), 1)

    def test_price_change_needs_explicit_acceptance_then_records_at_the_new_price(self):
        moved = [dataclasses.replace(l, american_price=-115) if l.participant_id in {x["participant_id"] for x in self.opt["legs"]} else l for l in self.legs]
        row = self.process(order_for(self.opt, "ord_" + "6" * 12), legs=moved)
        self.assertEqual(row["status"], mo.NEEDS_ACCEPTANCE)
        self.assertEqual(pb.query_paper_bets(self.conn, track="REAL_MARKET_PAPER"), [])
        detail = json.loads(row["detail_json"])
        self.assertTrue(detail["changes"])
        accepted = dict(self.opt, legs=detail["legs"], combined_american=detail["combined_american"], hit_probability=detail["hit_probability"])
        row2 = self.process(order_for(accepted, "ord_" + "7" * 12, supersedes="ord_" + "6" * 12), legs=moved)
        self.assertEqual(row2["status"], mo.RECORDED)
        b = pb.query_paper_bets(self.conn, track="REAL_MARKET_PAPER")[0]
        self.assertEqual(json.loads(b["provenance_json"])["supersedes_order_id"], "ord_" + "6" * 12)
        self.assertIn(-115, [l["american_price"] for l in json.loads(b["legs_json"])])

    def test_stale_or_withdrawn_legs_reject_without_writing(self):
        stale = [dataclasses.replace(l, price_fresh=False) for l in self.legs]
        row = self.process(order_for(self.opt, "ord_" + "8" * 12), legs=stale)
        self.assertEqual(row["status"], mo.REJECTED)
        self.assertEqual(pb.query_paper_bets(self.conn, track="REAL_MARKET_PAPER"), [])

    def test_no_longer_qualifying_is_rejected_not_offered_for_acceptance(self):
        worse = [dataclasses.replace(l, conservative_probability=0.40) for l in self.legs]
        row = self.process(order_for(self.opt, "ord_" + "9" * 12), legs=worse)
        self.assertEqual(row["status"], mo.REJECTED)

    def test_invalid_orders_write_nothing(self):
        for bad in ({"type": "PAPER_ORDER"}, "not json", {"schema": 1, "type": "PAPER_ORDER", "order_id": "x"},
                    dict(order_for(self.opt, "ord_" + "b" * 12), accepted={"legs": [], "stake": 10})):
            row = self.process(bad)
            self.assertEqual(row["status"], mo.REJECTED)
        o = order_for(self.opt, "ord_" + "c" * 12)
        o["accepted"]["stake"] = 25.0
        self.assertEqual(self.process(o)["status"], mo.REJECTED)
        o = order_for(self.opt, "ord_" + "d" * 12)
        o["accepted"]["legs"][0]["american_price"] = 50
        self.assertEqual(self.process(o)["status"], mo.REJECTED)
        self.assertEqual(pb.query_paper_bets(self.conn, track="REAL_MARKET_PAPER"), [])

    def test_same_game_legs_are_not_supported(self):
        o = order_for(self.opt, "ord_" + "e" * 12)
        if len(o["accepted"]["legs"]) == 1:
            o["accepted"]["legs"].append(dict(o["accepted"]["legs"][0], participant_id="Z"))
        else:
            o["accepted"]["legs"][1]["game_id"] = o["accepted"]["legs"][0]["game_id"]
        self.assertEqual(self.process(o)["status"], mo.REJECTED)

    def test_insufficient_cash_is_refused_and_nothing_is_written(self):
        for i in range(50):                                                    # drain the account to < $10
            pb.record_paper_bet(self.conn, track="REAL_MARKET_PAPER", price_source="LIVE_DRAFTKINGS", market_id=f"X{i}", entry_odds=100,
                                idempotency_key=f"drain{i}")
        row = self.process(order_for(self.opt, "ord_" + "f" * 12))
        self.assertEqual(row["status"], mo.REJECTED)
        self.assertIn("cash", row["reason"].lower())
        self.assertEqual(len(pb.query_paper_bets(self.conn, track="REAL_MARKET_PAPER")), 50)

    def test_manual_tickets_do_not_take_automatic_slots_and_origins_report_separately(self):
        self.process(order_for(self.opt, "ord_" + "g" * 12))
        today = dtk.recorded_today(self.conn, DATE)
        self.assertEqual(today, [])                                            # no automatic ticket
        self.assertEqual(len(dtk.recorded_today(self.conn, DATE, origin="MANUALLY_ADDED")), 1)
        state = dtk.build_state(self.conn, NOW, recommended=[], singles=[], empty_reason="x", diagnostics={}, record_results=[], options=self.doc)
        self.assertEqual(state["slots"]["used"], 0)
        self.assertEqual(len(state["manual_tickets"]), 1)
        self.assertEqual(state["manual_tickets"][0]["origin"], "MANUALLY_ADDED")
        self.assertEqual(state["origins"]["MANUALLY_ADDED"]["tickets"], 1)
        self.assertEqual(state["origins"]["AUTOMATIC"]["tickets"], 0)
        self.assertEqual(state["exposure"]["tickets_by_origin"], {"MANUALLY_ADDED": 1})
        on_book = [o for o in state["options"]["options"] if o.get("on_book")]
        self.assertEqual(len(on_book), 1)

    def test_browsing_writes_nothing(self):
        before = self.conn.execute("SELECT COUNT(*) FROM paper_bets").fetchone()[0]
        po.build_options(self.legs, DATE)
        dtk.build_state(self.conn, NOW, recommended=[], singles=[], empty_reason=None, diagnostics={}, record_results=[], options=self.doc)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM paper_bets").fetchone()[0], before)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM manual_orders").fetchone()[0], 0)

    def test_manual_ticket_settles_like_any_other(self):
        self.process(order_for(self.opt, "ord_" + "h" * 12))
        b = pb.query_paper_bets(self.conn, track="REAL_MARKET_PAPER")[0]
        pb.settle_paper_bet(self.conn, b["paper_bet_id"], "WIN")
        acct = pb.account_state(self.conn, "REAL_MARKET_PAPER")
        self.assertGreater(acct["settled_pnl"], 0)
        self.assertEqual(pb.origin_performance(self.conn)["MANUALLY_ADDED"]["wins"], 1)

    def test_origin_and_provenance_are_immutable(self):
        self.process(order_for(self.opt, "ord_" + "i" * 12))
        pid = pb.query_paper_bets(self.conn, track="REAL_MARKET_PAPER")[0]["paper_bet_id"]
        for col, val in (("origin", "AUTOMATIC"), ("provenance_json", "{}"), ("legs_json", "[]")):
            with self.assertRaises(sqlite3.DatabaseError):
                self.conn.execute(f"UPDATE paper_bets SET {col} = ? WHERE paper_bet_id = ?", (val, pid))


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
                                             viewer_email_present=True, viewer_allowed=True, token_configured=True) | kw

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
        self.assertTrue(st["direct_ready"] and st["viewer_allowed"])
        self.assertEqual(st["viewer_email_masked"], "m*@x.com")
        self.assertNotIn("SECRETVALUE9", json.dumps(st))
        none = order_client.path_status(S(), None)
        self.assertFalse(none["direct_ready"] or none["viewer_email_present"] or none["token_configured"])
        self.assertFalse(order_client.path_status(S(PAPER_ORDER_TOKEN="t", ORDER_ALLOWED_EMAILS="a@x.com"), None)["direct_ready"])

    def test_path_check_uses_its_own_label_and_title(self):
        d = self.doc()
        self.assertEqual(order_client.issue_label(d), "order-path-check")
        self.assertTrue(order_client.issue_title(d).startswith("order-path-check chk_"))


class TestViewerIdAllowList(unittest.TestCase):
    RAW = "a-76-character-opaque-platform-viewer-id-0123456789abcdef0123456789abcdef01234"

    def test_an_opaque_viewer_id_is_matched_by_fingerprint_only(self):
        class S(dict):
            pass
        fp = order_client.fingerprint(self.RAW)
        self.assertEqual(len(fp), 12)
        ok = order_client.path_status(S(PAPER_ORDER_TOKEN="ghp_SECRET", ORDER_ALLOWED_VIEWER_IDS=fp), None, self.RAW)
        self.assertTrue(ok["direct_ready"] and ok["allowed_by"] == "viewer id")
        self.assertNotIn(self.RAW, json.dumps(ok))
        self.assertNotIn("ghp_SECRET", json.dumps(ok))
        no = order_client.path_status(S(PAPER_ORDER_TOKEN="t", ORDER_ALLOWED_VIEWER_IDS="000000000000"), None, self.RAW)
        self.assertFalse(no["direct_ready"])
        self.assertFalse(order_client.path_status(S(ORDER_ALLOWED_VIEWER_IDS=fp), None, self.RAW)["direct_ready"])      # no token, no one-click

    def test_write_access_accepts_either_identity(self):
        class S(dict):
            pass
        fp = order_client.fingerprint(self.RAW)
        self.assertEqual(order_client.configured_write_access(S(PAPER_ORDER_TOKEN="t", ORDER_ALLOWED_VIEWER_IDS=fp), None, self.RAW), (True, "t"))
        self.assertEqual(order_client.configured_write_access(S(PAPER_ORDER_TOKEN="t", ORDER_ALLOWED_EMAILS="a@x.com"), "a@x.com", None), (True, "t"))
        self.assertEqual(order_client.configured_write_access(S(PAPER_ORDER_TOKEN="t"), None, self.RAW), (False, None))

    def test_a_platform_viewer_id_is_not_mistaken_for_an_email(self):
        from unittest import mock
        from dashboard import ui
        headers = mock.Mock()
        headers.get.return_value = self.RAW
        with mock.patch.object(ui.st, "context", mock.Mock(headers=headers)):
            self.assertEqual(ui.viewer_id(), self.RAW)
            self.assertIsNone(ui.viewer_email())


class TestViewerIdentity(unittest.TestCase):
    def test_header_formats_yield_an_email_and_a_names_only_shape(self):
        import base64
        from dashboard import ui
        tok = lambda d: "h." + base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=") + ".s"   # noqa: E731
        cases = [("Me@Example.com", "me@example.com", []), (json.dumps({"email": "me@example.com", "id": 1}), "me@example.com", ["email", "id"]),
                 (tok({"email": "me@example.com", "exp": 5}), "me@example.com", ["email", "exp"]), (tok({"sub": "123"}), None, ["sub"]),
                 ("opaque-value", None, []), (None, None, [])]
        for value, want, claims in cases:
            email, shape = ui._email_from_header(value)
            self.assertEqual(email, want, value)
            self.assertEqual(shape["claims"], claims)
            self.assertNotIn("me@example.com", json.dumps(shape))                # the shape never carries the value

    def test_streamlit_user_email_wins_over_the_header(self):
        from unittest import mock
        from dashboard import ui
        with mock.patch.object(ui.st, "user", mock.Mock(email="Direct@X.com")):
            self.assertEqual(ui.viewer_email(), "direct@x.com")


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
