"""The Paper Parlay Builder page: pick a player, a market and a line, keep the slip while browsing other players, edit and remove legs, see the real prices, freshness, the estimated or
multiplied combined price, the possible return and the destination account, and submit explicitly. Browsing never files anything."""
from __future__ import annotations

import copy
import datetime as dt
import os
import unittest
from contextlib import ExitStack
from unittest import mock

from streamlit.testing.v1 import AppTest

from dashboard import order_client
from dashboard import snapshot_source as ss
from dashboard import ui
from operational import log_signing
from operational import personal_logs as pl
from operational import runtime_mode as rm
from tests.product_fixture import ACCT_PASS, builder_pool_doc, snapshot

PAGE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dashboard", "pages", "40_Parlay_Builder.py")
NOW = dt.datetime(2026, 10, 8, 22, 0, tzinfo=dt.timezone.utc)            # a minute after the fixture quotes, an hour before the fixture game
GAME = "2026020900"
HASH = pl.account_key("burnett")


def state_for(snap):
    blank = {f: None for f in ss.SnapshotState._fields}
    if snap is None:
        return ss.SnapshotState(**blank)._replace(data=None, source="NONE", fetch_status="FAILED", last_error="network error: URLError", freshness="UNAVAILABLE")
    return ss.SnapshotState(**blank)._replace(data=snap, source="REMOTE", fetch_status="OK", freshness="CURRENT", schema_version=2)


class Runner:
    """One AppTest across several reruns, with the hosted-mode patches held for the whole test."""
    def __init__(self, snap, *, token=True, account=True, now=NOW, file_order=None):
        self.stack = ExitStack()
        self.file_order = file_order or mock.Mock(return_value={"ok": True, "via": "direct", "issue": 1, "error": None})
        st_ = state_for(snap)
        for p in (mock.patch.object(rm, "current_mode", return_value=rm.COMMUNITY_CLOUD_MODE), mock.patch.object(ss, "remote_enabled", return_value=True),
                  mock.patch.object(ss, "current", return_value=st_), mock.patch.object(ui, "_utcnow", return_value=now), mock.patch.object(ui, "file_order", self.file_order)):
            self.stack.enter_context(p)
        self.at = AppTest.from_file(PAGE, default_timeout=120)
        if token:
            self.at.secrets["LOG_WRITE_TOKEN"] = "tk"
        if account:
            self.at.session_state["_personal_log"] = {"hash": HASH, "name": "Burnett", "creation": None, "write_key": ACCT_PASS}
        self.at.run()
        self.addCleanup = self.stack.close

    def close(self):
        self.stack.close()

    def text(self):
        a = self.at
        return " ".join([m.value for m in a.markdown] + [c.value for c in a.caption] + [s.value for s in a.subheader] + [w.value for w in a.warning] + [i.value for i in a.info]
                        + [e.value for e in a.error])

    def press(self, key):
        self.at.button(key=key).click()
        self.at.run()
        return self

    def add(self, pid, code, k, gid=GAME):
        return self.press(f"pb_add_{gid}_{pid}_{code}_{k}")

    def submit(self):
        return self.press("pb_submit")


def snap_with(pool=None):
    s = copy.deepcopy(snapshot())
    s["builder_pool"] = pool if pool is not None else builder_pool_doc()
    return s


def aged_pool(hours):
    p = builder_pool_doc()
    q = (dt.datetime(2026, 10, 8, 21, 58, tzinfo=dt.timezone.utc) - dt.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for g in p["games"].values():
        for pl_ in g["players"].values():
            for mk in pl_["m"].values():
                mk["q"] = mk["r"] = q
    return p


class Case(unittest.TestCase):
    def run_(self, snap=None, **kw):
        r = Runner(snap_with() if snap is None else snap, **kw)
        self.addCleanup(r.close)
        self.assertEqual(len(r.at.exception), 0, [str(e.value)[:300] for e in r.at.exception])
        return r


class TestBrowsing(Case):
    def test_it_shows_players_markets_lines_with_real_prices_and_freshness_and_files_nothing(self):
        r = self.run_()
        t = r.text()
        for expect in ("Pick a player", "Choose a market", "Choose a line", "Test Skater One 2+ shots on goal", "-110", "fresh"):
            self.assertIn(expect, t)
        self.assertIn("Nothing on your slip yet", t)
        r.file_order.assert_not_called()

    def test_no_price_list_is_an_honest_empty_state_not_a_blank_page(self):
        empty = builder_pool_doc()
        empty["games"] = {}
        r = self.run_(snap_with(empty))
        self.assertIn("No player prices are on file", " ".join(m.value for m in r.at.markdown))

    def test_a_stale_price_can_be_seen_but_not_added(self):
        r = self.run_(snap_with(aged_pool(6)))
        self.assertIn("STALE", r.text())
        self.assertTrue(r.at.button(key=f"pb_add_{GAME}_P1_SOG_2").disabled)


class TestSlip(Case):
    def test_adding_a_line_puts_it_on_the_slip_with_the_price_and_the_destination(self):
        r = self.run_().add("P1", "SOG", 2)
        t = r.text()
        self.assertIn("Your slip", t)
        self.assertIn("Quoted price", t)
        self.assertIn("-110", t)
        self.assertIn("Burnett", t)
        self.assertIn("separate from the model book", t)
        r.file_order.assert_not_called()                                                   # adding to a slip files nothing

    def test_selections_survive_browsing_another_player(self):
        r = self.run_().add("P1", "SOG", 2)
        r.at.selectbox(key="pb_player").set_value(("P2", GAME))
        r.at.run()
        r.add("P2", "SOG", 2)
        names = " ".join(m.value for m in r.at.markdown)
        self.assertIn("Test Skater One 2+ shots on goal", names)
        self.assertIn("Test Skater Two 2+ shots on goal", names)
        self.assertEqual(len(r.at.session_state["_slip"]), 2)

    def test_a_leg_can_be_replaced_with_another_line_and_removed(self):
        r = self.run_().add("P1", "SOG", 2).add("P1", "SOG", 3)
        self.assertEqual([l["threshold"] for l in r.at.session_state["_slip"]], [3])      # one bet per player and market: the new line replaces the old
        r.press(f"pb_rm_0_P1_PLAYER_SOG_ALTERNATE")
        self.assertEqual(r.at.session_state["_slip"], [])

    def test_clear_slip(self):
        r = self.run_().add("P1", "SOG", 2).press("pb_clear")
        self.assertEqual(r.at.session_state["_slip"], [])

    def test_legs_from_the_same_game_are_labelled_multiplied_and_need_an_acknowledgement(self):
        r = self.run_().add("P1", "SOG", 2)
        r.at.selectbox(key="pb_player").set_value(("P2", GAME))
        r.at.run()
        r.add("P2", "SOG", 2)
        t = r.text()
        self.assertIn("Multiplied price", t)
        self.assertIn("NOT a DraftKings quote", t)
        self.assertTrue(r.at.button(key="pb_submit").disabled)
        r.at.checkbox(key="_slip_ack").check()
        r.at.run()
        self.assertFalse(r.at.button(key="pb_submit").disabled)

    def test_the_possible_return_is_computed_from_the_leg_price_and_stake(self):
        r = self.run_().add("P1", "SOG", 2)
        r.at.number_input(key="_slip_stake").set_value(55.0)
        r.at.run()
        self.assertIn("$105.00", " ".join(m.value for m in r.at.markdown))                # 55 stake at -110 returns 55 + 50


class TestSubmit(Case):
    def test_submit_files_one_signed_order_for_the_chosen_account_and_never_includes_the_passcode(self):
        r = self.run_().add("P1", "SOG", 2)
        r.at.number_input(key="_slip_stake").set_value(25.0)
        r.at.run()
        r.submit()
        self.assertEqual(r.file_order.call_count, 1)
        order = r.file_order.call_args[0][0]
        self.assertEqual((order["type"], order["log"]["hash"], order["accepted"]["kind"], order["accepted"]["stake"]), ("PERSONAL_BET", HASH, "BUILDER", 25.0))
        self.assertTrue(log_signing.verify(order, log_signing.public_key_hex(ACCT_PASS, HASH)))
        self.assertNotIn(ACCT_PASS, repr(order))
        self.assertEqual(order["accepted"]["legs"][0]["american_price"], -110.0)

    def test_pressing_submit_twice_does_not_file_a_second_order(self):
        r = self.run_().add("P1", "SOG", 2).submit()
        self.assertEqual(r.file_order.call_count, 1)
        self.assertEqual(r.at.session_state["_builder_order"]["log"], HASH)
        self.assertEqual([b.key for b in r.at.button if b.key == "pb_submit"], [])         # the submit control is gone while the order is pending

    def test_without_the_write_credential_the_slip_is_kept_and_nothing_can_be_submitted(self):
        r = self.run_(token=False).add("P1", "SOG", 2)
        self.assertIn("not switched on yet", r.text())
        self.assertTrue(r.at.button(key="pb_submit").disabled)
        r.file_order.assert_not_called()

    def test_without_an_account_it_points_to_my_bets_and_cannot_submit(self):
        r = self.run_(account=False).add("P1", "SOG", 2)
        self.assertIn("Open or create your account", r.text())
        self.assertTrue(r.at.button(key="pb_submit").disabled)

    def test_a_view_only_account_cannot_submit(self):
        r = self.run_(account=False)
        r.at.session_state["_personal_log"] = {"hash": HASH, "name": "Burnett", "creation": None, "write_key": None}
        r.at.run()
        r.add("P1", "SOG", 2)
        self.assertIn("view-only", r.text())
        self.assertTrue(r.at.button(key="pb_submit").disabled)

    def test_the_stake_cannot_exceed_the_accounts_cash(self):
        snap = snap_with()
        snap["personal_logs"]["logs"][HASH]["bankroll"]["available_cash"] = 30.0
        r = self.run_(snap).add("P1", "SOG", 2)
        self.assertEqual(r.at.number_input(key="_slip_stake").max, 30.0)

    def test_a_price_that_moved_after_adding_blocks_submit_until_the_current_price_is_accepted(self):
        r = self.run_().add("P1", "SOG", 2)
        r.at.session_state["_slip"][0]["american_price"] = -130.0                           # the slip was built at -130; the list now says -110
        r.at.run()
        self.assertIn("Price moved", r.text())
        self.assertTrue(r.at.button(key="pb_submit").disabled)
        r.press("pb_update")
        self.assertEqual(r.at.session_state["_slip"][0]["american_price"], -110.0)
        self.assertFalse(r.at.button(key="pb_submit").disabled)

    def test_the_engines_answer_is_shown_recorded_or_refused(self):
        r = self.run_().add("P1", "SOG", 2).submit()
        oid = r.at.session_state["_builder_order"]["order_id"]
        snap = snap_with()
        snap["personal_logs"]["logs"][HASH]["orders"] = [{"order_id": oid, "status": "REJECTED", "reason": "INSUFFICIENT_FUNDS: this account has $5.00 available", "bet_id": None}]
        r2 = Runner(snap)
        self.addCleanup(r2.close)
        r2.at.session_state["_slip"] = r.at.session_state["_slip"]
        r2.at.session_state["_builder_order"] = {"order_id": oid, "log": HASH}
        r2.at.run()
        self.assertIn("Nothing was added: INSUFFICIENT_FUNDS", " ".join(m.value for m in r2.at.markdown))


if __name__ == "__main__":
    unittest.main()
