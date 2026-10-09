"""
Personal bet logs: codes, log creation and collisions, order handling (idempotent, validated, revalidated against current prices),
settlement, the published view, the legacy migration -- and the central guarantee: a personal bet can never change the $500 model book.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dashboard import order_client
from operational import cloud_snapshot_schema as schema
from operational import paper_bankroll as pb
from operational import paper_bet_settlement_driver as drv
from operational import personal_logs as pl
from operational import player_options as po
from tests.test_daily_tickets import NOW, board, fresh_ledger
from tests.test_manual_orders import DATE

CODE_A, CODE_B = "otter-maple-puck-4821", "blue-line-crease-7310"


def fresh_logs():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return pl.connect(tmp.name)


def ledger_fingerprint(conn) -> str:
    """A hash of EVERY table of the model ledger: if any row anywhere changes, this changes."""
    h = hashlib.sha256()
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall():
        h.update(name.encode())
        rows = sorted(repr(tuple(row)) for row in conn.execute(f"SELECT * FROM {name}").fetchall())
        h.update("\n".join(rows).encode())
    h.update(json.dumps(pb.account_state(conn, "REAL_MARKET_PAPER"), sort_keys=True).encode())
    return h.hexdigest()


def order(opt: dict, oid: str, code: str, *, create: str | None = None, stake: float = 10.0, kind=pl.TYPE_BET) -> dict:
    return order_client.build_personal_order(opt, order_id=oid, log_hash=pl.code_hash(code), page_generated_at=NOW.isoformat(), stake=stake,
                                             create={"creation_id": create, "display_name": "Casey"} if create else None, kind=kind)


class Base(unittest.TestCase):
    def setUp(self):
        self.logs = fresh_logs()
        self.legs = board(4, price=-105, p=0.62)
        self.doc = po.build_options(self.legs, DATE)
        self.opt = self.doc["options"][0]

    def go(self, doc, legs=None, now=NOW):
        return pl.process_order(self.logs, doc, current_legs=legs or self.legs, now=now, source="test:1")

    def create(self, code=CODE_A, cid="crt_" + "a" * 10, oid=None):
        doc = {"schema": 1, "type": pl.TYPE_CREATE, "order_id": oid or "ord_" + "c" * 12, "log": {"hash": pl.code_hash(code), "create": {"creation_id": cid, "display_name": "Casey"}}}
        return self.go(doc)


class TestCodes(unittest.TestCase):
    def test_hash_ignores_case_and_spacing_and_never_contains_the_code(self):
        a, b = pl.code_hash("Otter-Maple-Puck-4821"), pl.code_hash("  otter-maple-puck-4821 ")
        self.assertEqual(a, b)
        self.assertNotEqual(a, pl.code_hash(CODE_B))
        self.assertRegex(a, r"^[0-9a-f]{32}$")
        self.assertNotIn("otter", a)

    def test_weak_codes_are_refused_with_a_reason(self):
        for bad in ("", "short", "aaaaaaaaaaaa", "x" * 70):
            self.assertIsNotNone(pl.validate_code(bad), bad)
        self.assertIsNone(pl.validate_code(CODE_A))

    def test_suggested_codes_are_valid_and_vary(self):
        seen = {pl.suggest_code() for _ in range(20)}
        self.assertGreater(len(seen), 15)
        self.assertTrue(all(pl.validate_code(c) is None for c in seen))

    def test_display_names_cannot_smuggle_content_the_snapshot_forbids(self):
        self.assertEqual(pl.validate_name("Casey's picks")[0], "Casey's picks")
        for bad in ("", "x" * 40, "a@b.com", "my yahoo league", "/Users/me", "<b>x</b>"):
            self.assertIsNotNone(pl.validate_name(bad)[1], bad)


class TestCreationAndCollisions(Base):
    def test_create_then_second_creator_is_refused(self):
        self.assertEqual(self.create()["status"], pl.CREATED)
        other = self.create(cid="crt_" + "b" * 10, oid="ord_" + "d" * 12)
        self.assertEqual(other["status"], pl.REJECTED)
        self.assertIn("CODE_IN_USE", other["reason"])
        self.assertEqual(self.logs.execute("SELECT COUNT(*) FROM logs").fetchone()[0], 1)

    def test_the_creator_can_repeat_creation_with_a_new_order(self):
        self.create()
        again = self.create(oid="ord_" + "e" * 12)
        self.assertEqual(again["status"], pl.CREATED)

    def test_a_bet_cannot_go_into_a_log_that_does_not_exist(self):
        res = self.go(order(self.opt, "ord_" + "f" * 12, CODE_A))
        self.assertEqual(res["status"], pl.REJECTED)
        self.assertIn("LOG_NOT_FOUND", res["reason"])
        self.assertEqual(self.logs.execute("SELECT COUNT(*) FROM bets").fetchone()[0], 0)

    def test_first_bet_may_create_the_log_and_a_stranger_cannot_use_the_creation_to_join(self):
        res = self.go(order(self.opt, "ord_" + "g" * 12, CODE_A, create="crt_" + "a" * 10))
        self.assertEqual(res["status"], pl.RECORDED)
        stranger = self.go(order(self.opt, "ord_" + "h" * 12, CODE_A, create="crt_" + "z" * 10))
        self.assertEqual(stranger["status"], pl.REJECTED)
        self.assertIn("CODE_IN_USE", stranger["reason"])

    def test_new_log_rate_limit(self):
        with mock.patch.object(pl, "MAX_NEW_LOGS_PER_DAY", 2):
            for i in range(3):
                r = self.create(code=f"code-number-{i}-abcdef", cid=f"crt_{i}" + "x" * 8, oid=f"ord_{i}" + "y" * 8)
            self.assertEqual(r["status"], pl.REJECTED)
            self.assertIn("RATE_LIMIT", r["reason"])


class TestBets(Base):
    def setUp(self):
        super().setUp()
        self.create()

    def test_records_one_bet_with_the_chosen_stake_frozen_and_labelled_manually_added(self):
        res = self.go(order(self.opt, "ord_" + "1" * 12, CODE_A, stake=25))
        self.assertEqual(res["status"], pl.RECORDED)
        b = dict(self.logs.execute("SELECT * FROM bets").fetchone())
        self.assertEqual((b["stake"], b["origin"], b["result_status"], b["log_hash"]), (25.0, "MANUALLY_ADDED", "PENDING", pl.code_hash(CODE_A)))
        self.assertEqual(json.loads(b["provenance_json"])["order_id"], "ord_" + "1" * 12)
        import sqlite3
        with self.assertRaises(sqlite3.DatabaseError):
            self.logs.execute("UPDATE bets SET stake = 1 WHERE bet_id = ?", (b["bet_id"],))
        with self.assertRaises(sqlite3.DatabaseError):
            self.logs.execute("DELETE FROM bets WHERE bet_id = ?", (b["bet_id"],))

    def test_the_same_order_twice_is_one_bet(self):
        doc = order(self.opt, "ord_" + "2" * 12, CODE_A)
        first, second = self.go(doc), self.go(doc)
        self.assertEqual(first, second)
        self.assertEqual(self.logs.execute("SELECT COUNT(*) FROM bets").fetchone()[0], 1)

    def test_the_same_bet_again_the_same_day_is_not_stakes_twice_but_another_log_may_hold_it(self):
        self.go(order(self.opt, "ord_" + "3" * 12, CODE_A))
        again = self.go(order(self.opt, "ord_" + "4" * 12, CODE_A))
        self.assertEqual(again["status"], pl.ALREADY)
        self.assertEqual(self.logs.execute("SELECT COUNT(*) FROM bets").fetchone()[0], 1)
        self.go(order(self.opt, "ord_" + "5" * 12, CODE_B, create="crt_" + "q" * 10))
        self.assertEqual(self.logs.execute("SELECT COUNT(DISTINCT log_hash) FROM bets").fetchone()[0], 2)

    def test_invalid_stakes_are_rejected_and_write_nothing(self):
        for i, stake in enumerate((0, -5, 0.5, 5000, True, "10", None)):
            doc = order(self.opt, f"ord_s{i}" + "z" * 8, CODE_A)
            doc["accepted"]["stake"] = stake
            res = self.go(doc)
            self.assertEqual(res["status"], pl.REJECTED, stake)
        self.assertEqual(self.logs.execute("SELECT COUNT(*) FROM bets").fetchone()[0], 0)

    def test_a_moved_price_needs_acceptance_and_writes_nothing(self):
        moved = [dataclasses.replace(l, american_price=l.american_price + 20) for l in self.legs]
        res = self.go(order(self.opt, "ord_" + "6" * 12, CODE_A), legs=moved)
        self.assertEqual(res["status"], pl.NEEDS_ACCEPTANCE)
        self.assertEqual(self.logs.execute("SELECT COUNT(*) FROM bets").fetchone()[0], 0)

    def test_stale_or_started_legs_are_rejected(self):
        stale = [dataclasses.replace(l, price_fresh=False) for l in self.legs]
        res = self.go(order(self.opt, "ord_" + "7" * 12, CODE_A), legs=stale)
        self.assertEqual(res["status"], pl.REJECTED)
        self.assertEqual(self.logs.execute("SELECT COUNT(*) FROM bets").fetchone()[0], 0)

    def test_malformed_orders_are_stored_as_rejected_never_raised(self):
        for raw in ("not json", {"type": "PERSONAL_BET"}, {"schema": 1, "type": pl.TYPE_BET, "order_id": "ord_" + "8" * 12, "log": {"hash": "zz"}}):
            self.assertEqual(self.go(raw)["status"], pl.REJECTED)

    def test_daily_order_limit(self):
        with mock.patch.object(pl, "MAX_BET_ORDERS_PER_LOG_PER_DAY", 2):
            last = None
            for i in range(3):
                opt = self.doc["options"][min(i, len(self.doc["options"]) - 1)]
                last = self.go(order(opt, f"ord_l{i}" + "m" * 8, CODE_A, stake=10 + i))
            self.assertIn("RATE_LIMIT", last["reason"] or "")


class TestSettlement(Base):
    def setUp(self):
        super().setUp()
        self.go(order(self.opt, "ord_" + "9" * 12, CODE_A, create="crt_" + "a" * 10, stake=20))
        self.after = NOW + dt.timedelta(days=1)

    def settle(self, result):
        with mock.patch.object(drv, "resolve_combo_bet", return_value=result):
            return pl.settle_open(self.logs, None, self.after)

    def test_win_pays_the_stake_at_the_frozen_price(self):
        bet = dict(self.logs.execute("SELECT * FROM bets").fetchone())
        self.assertEqual(self.settle({"status": "WIN", "leg_results": []})["settled"], 1)
        row = dict(self.logs.execute("SELECT * FROM bets").fetchone())
        dec = 1 + bet["entry_odds"] / 100 if bet["entry_odds"] > 0 else 1 + 100 / abs(bet["entry_odds"])
        self.assertEqual(row["result_status"], "WIN")
        self.assertAlmostEqual(row["profit_loss"], round(20 * (dec - 1), 2), places=2)

    def test_loss_costs_the_stake_and_is_final(self):
        self.settle({"status": "LOSS", "leg_results": []})
        row = dict(self.logs.execute("SELECT * FROM bets").fetchone())
        self.assertEqual((row["result_status"], row["profit_loss"]), ("LOSS", -20.0))
        self.assertEqual(self.settle({"status": "WIN", "leg_results": []})["scanned"], 0)          # settled bets are never touched again

    def test_unfinished_game_stays_open(self):
        out = self.settle({"status": drv.PENDING_STILL_WAITING, "leg_results": []})
        self.assertEqual(out["settled"], 0)
        self.assertEqual(self.logs.execute("SELECT result_status FROM bets").fetchone()[0], "PENDING")

    def test_not_started_bets_are_not_scanned(self):
        with mock.patch.object(drv, "resolve_combo_bet") as m:
            out = pl.settle_open(self.logs, None, NOW - dt.timedelta(hours=6))
        self.assertEqual(out["scanned"], 0)
        m.assert_not_called()

    def test_unverified_void_rule_leaves_the_bet_open_and_visible(self):
        self.settle({"status": "UNRESOLVED", "reason": "VOID_RULE_UNVERIFIED", "provisional": {"status": "VOID"}, "leg_results": []})
        row = dict(self.logs.execute("SELECT * FROM bets").fetchone())
        self.assertEqual(row["result_status"], "UNRESOLVED")
        self.assertIsNone(row["profit_loss"])


class TestPublishedView(Base):
    def test_section_is_keyed_by_hash_has_no_code_and_passes_the_publication_rules(self):
        self.go(order(self.opt, "ord_" + "a" * 12, CODE_A, create="crt_" + "a" * 10))
        sec = pl.section(self.logs, NOW)
        blob = json.dumps(sec)
        self.assertNotIn("otter", blob)
        self.assertEqual(list(sec["logs"]), [pl.code_hash(CODE_A)])
        log = sec["logs"][pl.code_hash(CODE_A)]
        self.assertEqual(log["summary"]["bets"], 1)
        self.assertEqual(log["bets"][0]["origin"], "MANUALLY_ADDED")
        self.assertEqual(log["orders"][0]["status"], pl.RECORDED)
        def walk(n):
            if isinstance(n, dict):
                for k, v in n.items():
                    self.assertIsNone(schema._FORBIDDEN_KEY_RE.search(k), k)
                    walk(v)
            elif isinstance(n, list):
                for v in n:
                    walk(v)
            elif isinstance(n, str):
                for rx in schema._FORBIDDEN_VALUE_RES:
                    self.assertIsNone(rx.search(n))
                self.assertIsNone(schema._YAHOO_RE.search(n))
                self.assertIsNone(schema._ABSOLUTE_PATH_RE.search(n))
        walk(sec)

    def test_summary_arithmetic(self):
        rows = [{"result_status": "WIN", "profit_loss": 15.0, "stake": 10.0}, {"result_status": "LOSS", "profit_loss": -10.0, "stake": 10.0},
                {"result_status": "VOID", "profit_loss": 0.0, "stake": 10.0}, {"result_status": "PENDING", "profit_loss": None, "stake": 7.0}]
        s = pl.summarize(rows)
        self.assertEqual((s["wins"], s["losses"], s["voids"], s["open"], s["open_stake"], s["settled_pnl"]), (1, 1, 1, 1, 7.0, 5.0))
        self.assertEqual(s["win_rate"], 0.5)
        self.assertEqual(s["staked_settled"], 20.0)
        self.assertAlmostEqual(s["roi"], 0.25)


class TestModelBookIsUntouched(Base):
    """The requirement: personal bets NEVER affect the original $500 model book."""

    def test_two_logs_adding_and_settling_leave_every_ledger_table_identical(self):
        path, ledger = fresh_ledger()
        before = ledger_fingerprint(ledger)
        acct_before = pb.account_state(ledger, "REAL_MARKET_PAPER")
        for i, (code, name_cid) in enumerate(((CODE_A, "crt_" + "a" * 10), (CODE_B, "crt_" + "b" * 10))):
            for j, opt in enumerate(self.doc["options"][:2]):
                res = self.go(order(opt, f"ord_{i}{j}" + "p" * 8, code, create=name_cid, stake=10 + 5 * j))
                self.assertEqual(res["status"], pl.RECORDED)
        with mock.patch.object(drv, "resolve_combo_bet", side_effect=[{"status": "WIN", "leg_results": []}, {"status": "LOSS", "leg_results": []}] * 4):
            out = pl.settle_open(self.logs, None, NOW + dt.timedelta(days=1))
        self.assertEqual(out["settled"], 4)
        self.assertEqual(ledger_fingerprint(ledger), before)
        self.assertEqual(pb.account_state(ledger, "REAL_MARKET_PAPER"), acct_before)
        sec = pl.section(self.logs, NOW)
        self.assertEqual(len(sec["logs"]), 2)
        for log in sec["logs"].values():                                         # each log reconciles on its own
            self.assertEqual(log["summary"]["bets"], 2)
            self.assertEqual(round(sum(b["result"]["profit_loss"] for b in log["bets"]), 2), log["summary"]["settled_pnl"])
        h = [pl.code_hash(CODE_A), pl.code_hash(CODE_B)]
        self.assertTrue(all(b["ticket_id"] for b in sec["logs"][h[0]]["bets"]))
        self.assertTrue({b["ticket_id"] for b in sec["logs"][h[0]]["bets"]}.isdisjoint({b["ticket_id"] for b in sec["logs"][h[1]]["bets"]}))

    def test_this_module_has_no_way_to_write_the_model_ledger(self):
        src = Path(pl.__file__).read_text()
        self.assertNotIn("record_paper_bet", src)
        self.assertNotIn("create_manual_paper_bet", src)
        self.assertNotIn("settle_paper_bet", src)
        self.assertNotRegex(src, r"(INSERT INTO|UPDATE|DELETE FROM)\s+paper_bets")


class TestLegacyMigration(Base):
    def make_ledger_with_manual_ticket(self):
        from operational import manual_orders as mo
        from tests.test_daily_tickets import fresh_ledger
        path, ledger = fresh_ledger()
        combo = __import__("research.real_market_parlay.engine", fromlist=["x"])._evaluate_combo(self.legs[:2])
        res = pb.create_manual_paper_bet(ledger, combo, provenance={"order_id": "ord_legacy0001"}, event_start_utc="2026-10-15T23:30:00Z", created_at_utc=NOW.isoformat())
        self.assertEqual(res["status"], "INSERTED")
        pb.settle_paper_bet(ledger, res["paper_bet_id"], "LOSS")
        return ledger, res["paper_bet_id"]

    def test_copy_once_leave_ledger_untouched_then_claim(self):
        ledger, tid = self.make_ledger_with_manual_ticket()
        before = ledger_fingerprint(ledger)
        out = pl.migrate_legacy_manual(self.logs, ledger, NOW)
        self.assertEqual(out["migrated"], 1)
        self.assertEqual(pl.migrate_legacy_manual(self.logs, ledger, NOW)["migrated"], 0)            # exactly once
        self.assertEqual(ledger_fingerprint(ledger), before)                                       # original record preserved
        moved = dict(self.logs.execute("SELECT * FROM bets").fetchone())
        self.assertEqual((moved["result_status"], moved["profit_loss"], moved["stake"]), ("LOSS", -10.0, 10.0))
        self.assertEqual(json.loads(moved["provenance_json"])["migrated_from"]["paper_bet_id"], tid)
        self.assertEqual(pl.section(self.logs, NOW)["logs"], {})                                    # not visible in any log until claimed
        claimed = pl.claim_legacy(self.logs, CODE_A, "Owner", NOW)
        self.assertEqual((claimed["status"], claimed["moved"]), ("CLAIMED", 1))
        self.assertEqual(pl.section(self.logs, NOW)["logs"][pl.code_hash(CODE_A)]["summary"]["settled_pnl"], -10.0)
        import sqlite3
        with self.assertRaises(sqlite3.DatabaseError):                                            # a claimed bet cannot be moved again
            self.logs.execute("UPDATE bets SET log_hash = ? WHERE bet_id = ?", ("0" * 32, moved["bet_id"]))
        audit = [r["kind"] for r in self.logs.execute("SELECT kind FROM audit ORDER BY id")]
        self.assertEqual(audit, ["LEGACY_MIGRATED", "LEGACY_CLAIMED"])


class TestQueueAndClient(Base):
    def test_poll_and_process_answers_personal_orders_and_leaves_the_model_ledger_alone(self):
        from operational import manual_orders as mo
        path, ledger = fresh_ledger()
        before = ledger_fingerprint(ledger)
        doc = order(self.opt, "ord_" + "q" * 12, CODE_A, create="crt_" + "a" * 10)
        body = json.dumps(doc)
        out = mo.poll_and_process(ledger, None, NOW, current_legs=self.legs, fetch=lambda repo, owner: ([], []),
                                  personal_conn=self.logs, personal_fetch=lambda repo, owner, label: ([{"issue": 5, "body": body, "created_at": "2026-10-15T16:59:00Z"}], []))
        self.assertEqual(out["processed"], 1)
        self.assertEqual(out["personal"][0]["status"], pl.RECORDED)
        self.assertEqual(self.logs.execute("SELECT COUNT(*) FROM bets").fetchone()[0], 1)
        self.assertEqual(ledger_fingerprint(ledger), before)
        again = mo.poll_and_process(ledger, None, NOW, current_legs=self.legs, fetch=lambda repo, owner: ([], []),
                                    personal_conn=self.logs, personal_fetch=lambda repo, owner, label: ([{"issue": 5, "body": body, "created_at": "x"}], []))
        self.assertEqual(again["personal"][0]["status"], pl.RECORDED)
        self.assertEqual(self.logs.execute("SELECT COUNT(*) FROM bets").fetchone()[0], 1)

    def test_orders_never_contain_the_code_and_use_their_own_label(self):
        doc = order(self.opt, "ord_" + "r" * 12, CODE_A)
        self.assertNotIn("otter", json.dumps(doc))
        self.assertEqual(order_client.issue_label(doc), "personal-bet")
        self.assertTrue(order_client.issue_title(doc).startswith("personal-bet "))
        self.assertEqual(order_client.build_personal_order(None, order_id="ord_" + "s" * 12, log_hash=pl.code_hash(CODE_A), page_generated_at=None, stake=10,
                                                           create={"creation_id": "crt_" + "a" * 10, "display_name": "Casey"}, kind=pl.TYPE_CREATE)["type"], pl.TYPE_CREATE)

    def test_write_credential_lookup_prefers_the_new_name_and_reads_nothing_else(self):
        self.assertEqual(order_client.personal_write_token({"LOG_WRITE_TOKEN": " a ", "PAPER_ORDER_TOKEN": "b"}), "a")
        self.assertEqual(order_client.personal_write_token({"PAPER_ORDER_TOKEN": "b"}), "b")
        self.assertIsNone(order_client.personal_write_token({}))
        self.assertFalse(order_client.path_status({}, None)["personal_log_writes_ready"])
        self.assertTrue(order_client.path_status({"LOG_WRITE_TOKEN": "x"}, None)["personal_log_writes_ready"])


if __name__ == "__main__":
    unittest.main()
