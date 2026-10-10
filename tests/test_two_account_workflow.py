"""
The whole personal workflow with the REAL page code and the REAL engine, for two separate people: create an account on My Bets -> build a slip on the Paper Parlay Builder -> submit -> the engine
records it -> it persists (a fresh session sees it) -> the game settles into the right account -> and at every step the model's $500 book is byte-identical.

Only the transport is simulated: a submitted order goes straight to the engine's order processor instead of through a GitHub issue, and the published snapshot is rebuilt from the personal database
(`personal_logs.section`) instead of travelling through the public data branch. That is the part the hosted test needs `LOG_WRITE_TOKEN` for; this proves everything else.
"""
from __future__ import annotations

import copy
import datetime as dt
import unittest
from unittest import mock

from operational import paper_bankroll as pb
from operational import paper_bet_settlement_driver as drv
from operational import personal_logs as pl
from tests.product_fixture import builder_pool_doc, snapshot
from tests.test_daily_tickets import fresh_ledger
from tests.test_parlay_builder_page import GAME, NOW, Runner
from tests.test_personal_logs import ledger_fingerprint
from tests.test_product_pages import rerun, run_page, text, with_token
from dashboard import ui

POOL = builder_pool_doc()


class Platform:
    """The 'server': a personal database and a model ledger, plus the snapshot the hosted pages read."""

    def __init__(self):
        import tempfile
        self.path = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
        self.logs = pl.connect(self.path)
        _, self.ledger = fresh_ledger()
        self.orders = []

    def file_order(self, order):                                   # what the hosted page's order transport does, collapsed into the engine call
        self.orders.append(order)
        conn = pl.connect(self.path)                                # the page runs in its own thread: it gets its own connection to the same database, as the engine job would
        try:
            pl.process_order(conn, order, current_legs=[], now=NOW, source="hosted-test", builder_pool=POOL)
        finally:
            conn.close()
        return {"ok": True, "via": "direct", "issue": len(self.orders), "error": None}

    def snap(self):
        s = copy.deepcopy(snapshot())
        s["personal_logs"] = pl.section(self.logs, NOW)
        s["builder_pool"] = copy.deepcopy(POOL)
        return s


def create_account(platform, surname):
    """Drive My Bets exactly as a visitor would; returns (name, passcode)."""
    with mock.patch.object(ui, "file_order", side_effect=platform.file_order):
        at = run_page("38_My_Bets.py", platform.snap(), setup=with_token)
        at.text_input(key="mb_new_name").set_value(surname)
        rerun(at, platform.snap())
        at.button(key="mb_create").click()
        rerun(at, platform.snap())
    pending = at.session_state["_personal_pending_creation"]
    return pending["name"], pending["passcode"], at


def build_and_submit(platform, acct_hash, name, passcode, legs, stake, ack=False):
    sel = {"hash": acct_hash, "name": name, "creation": None, "write_key": passcode.replace("-", "")}
    r = Runner(platform.snap(), account=False, file_order=mock.Mock(side_effect=platform.file_order))
    r.at.session_state["_personal_log"] = sel
    r.at.run()
    for pid, code, k in legs:
        if pid != "P1":
            r.at.selectbox(key="pb_player").set_value((pid, GAME))
            r.at.run()
        r.add(pid, code, k)
    r.at.number_input(key="_slip_stake").set_value(float(stake))
    r.at.run()
    if ack:
        r.at.checkbox(key="_slip_ack").check()
        r.at.run()
    r.submit()
    return r


class TestTwoPeopleOnTheRealPages(unittest.TestCase):
    def setUp(self):
        self.p = Platform()
        self.addCleanup(self.p.ledger.close)
        self.model_before = ledger_fingerprint(self.p.ledger)
        self.model_account_before = pb.account_state(self.p.ledger, "REAL_MARKET_PAPER")

    def test_create_build_submit_persist_settle_for_two_separate_accounts_and_the_model_book_never_moves(self):
        # 1. two people create their own accounts
        name_a, pass_a, at_a = create_account(self.p, "Qaone")
        name_b, pass_b, _ = create_account(self.p, "Qatwo")
        self.assertEqual((name_a, name_b), ("Qaone", "Qatwo"))
        self.assertNotEqual(pass_a, pass_b)
        ha, hb = pl.account_key("qaone"), pl.account_key("qatwo")
        for h in (ha, hb):
            self.assertEqual(pl.account_state(self.p.logs, h)["available_cash"], 500.0)                  # each starts with its own $500
        self.assertEqual(ledger_fingerprint(self.p.ledger), self.model_before)

        # 2. each builds and submits a different slip on the Parlay Builder (A: one leg $40; B: a same-game slip, acknowledged, $25)
        ra = build_and_submit(self.p, ha, name_a, pass_a, [("P1", "SOG", 2)], 40)
        rb = build_and_submit(self.p, hb, name_b, pass_b, [("P1", "SOG", 3), ("P2", "SOG", 2)], 25, ack=True)
        for r in (ra, rb):
            r.close()
        orders = [o for o in self.p.orders if o["type"] == "PERSONAL_BET"]
        self.assertEqual(len(orders), 2)
        a, b = pl.account_state(self.p.logs, ha), pl.account_state(self.p.logs, hb)
        self.assertEqual((a["available_cash"], a["open_stakes"]), (460.0, 40.0))
        self.assertEqual((b["available_cash"], b["open_stakes"]), (475.0, 25.0))
        self.assertEqual(self.p.logs.execute("SELECT COUNT(*) FROM bets WHERE log_hash = ?", (ha,)).fetchone()[0], 1)         # nobody's bet landed in the other account
        self.assertEqual(self.p.logs.execute("SELECT COUNT(*) FROM bets WHERE log_hash = ?", (hb,)).fetchone()[0], 1)
        self.assertEqual(ledger_fingerprint(self.p.ledger), self.model_before)

        # 3. persistence: a fresh session, with only the name and the passcode, sees the account and its open bet
        fresh = run_page("38_My_Bets.py", self.p.snap(), setup=lambda t: t.session_state.__setitem__("_personal_log", {"hash": ha, "name": name_a, "creation": None, "write_key": pass_a.replace("-", "")}))
        labels = {m.label: m.value for m in fresh.metric}
        self.assertEqual((labels["Available cash"], labels["Open stakes"]), ("$460.00", "$40.00"))
        self.assertIn("Test Skater One 2+ shots on goal", text(fresh) + " ".join(m.value for m in fresh.markdown))

        # 4. settlement: the game finishes; A wins, B loses; each result lands in its own account only
        with mock.patch.object(drv, "resolve_combo_bet", side_effect=[{"status": "WIN", "leg_results": []}, {"status": "LOSS", "leg_results": []}]):
            out = pl.settle_open(self.p.logs, None, NOW + dt.timedelta(days=1))
        self.assertEqual(out["settled"], 2)
        a, b = pl.account_state(self.p.logs, ha), pl.account_state(self.p.logs, hb)
        self.assertEqual((a["open_stakes"], a["settled_pnl"], a["available_cash"]), (0.0, round(40 * 100 / 110, 2), round(500 + 40 * 100 / 110, 2)))
        self.assertEqual((b["open_stakes"], b["settled_pnl"], b["available_cash"]), (0.0, -25.0, 475.0))
        settled = run_page("38_My_Bets.py", self.p.snap(), setup=lambda t: t.session_state.__setitem__("_personal_log", {"hash": hb, "name": name_b, "creation": None, "write_key": pass_b.replace("-", "")}))
        self.assertEqual({m.label: m.value for m in settled.metric}["Available cash"], "$475.00")

        # 5. every account reconciles on its own, and the model's $500 book is byte-identical to before anyone did anything
        self.assertTrue(all(r["agrees"] for r in pl.reconcile(self.p.logs)))
        self.assertEqual(ledger_fingerprint(self.p.ledger), self.model_before)
        self.assertEqual(pb.account_state(self.p.ledger, "REAL_MARKET_PAPER"), self.model_account_before)

    def test_one_person_cannot_spend_another_persons_account_or_overdraw_their_own(self):
        name_a, pass_a, _ = create_account(self.p, "Qaone")
        name_b, pass_b, _ = create_account(self.p, "Qatwo")
        ha, hb = pl.account_key("qaone"), pl.account_key("qatwo")
        # A tries to submit a slip to B's account with A's passcode: the page refuses (view-only), and a forged order is refused by the engine
        r = Runner(self.p.snap(), account=False)
        r.at.session_state["_personal_log"] = {"hash": hb, "name": name_b, "creation": None, "write_key": pass_a.replace("-", "")}
        r.at.run()
        r.add("P1", "SOG", 2)
        self.assertTrue(r.at.button(key="pb_submit").disabled)
        r.close()
        self.assertEqual(self.p.logs.execute("SELECT COUNT(*) FROM bets").fetchone()[0], 0)
        # a stake above the account's cash is refused
        big = build_and_submit(self.p, ha, name_a, pass_a, [("P1", "SOG", 2)], 500)
        big.close()
        self.assertEqual(pl.account_state(self.p.logs, ha)["available_cash"], 0.0)                   # the whole $500 may be staked once
        again = build_and_submit(self.p, ha, name_a, pass_a, [("P2", "SOG", 2)], 10)
        again.close()
        self.assertEqual(self.p.logs.execute("SELECT COUNT(*) FROM bets WHERE log_hash = ?", (ha,)).fetchone()[0], 1)
        self.assertTrue(any("INSUFFICIENT_FUNDS" in (o["reason"] or "") for o in self.p.logs.execute("SELECT reason FROM orders")))
        self.assertEqual(ledger_fingerprint(self.p.ledger), self.model_before)


if __name__ == "__main__":
    unittest.main()
