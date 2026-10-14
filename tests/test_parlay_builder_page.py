"""The Parlay Builder page: a read-only tool. Pick a player, a market and a line, keep the slip while browsing other players, edit and remove legs, enter a hypothetical stake, and see the real prices,
freshness, the estimated combined price and the potential return. There are no accounts and nothing can be submitted or recorded."""
from __future__ import annotations

import copy
import datetime as dt
import os
import unittest
from contextlib import ExitStack
from unittest import mock

from streamlit.testing.v1 import AppTest

from dashboard import snapshot_source as ss
from dashboard import ui
from operational import runtime_mode as rm
from tests.product_fixture import builder_pool_doc, snapshot

PAGE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dashboard", "pages", "40_Parlay_Builder.py")
NOW = dt.datetime(2026, 10, 8, 22, 0, tzinfo=dt.timezone.utc)            # a minute after the fixture quotes, an hour before the fixture game
GAME = "2026020900"


def state_for(snap):
    blank = {f: None for f in ss.SnapshotState._fields}
    if snap is None:
        return ss.SnapshotState(**blank)._replace(data=None, source="NONE", fetch_status="FAILED", last_error="network error: URLError", freshness="UNAVAILABLE")
    return ss.SnapshotState(**blank)._replace(data=snap, source="REMOTE", fetch_status="OK", freshness="CURRENT", schema_version=2)


class Runner:
    """One AppTest across several reruns, with the hosted-mode patches held for the whole test."""
    def __init__(self, snap, *, now=NOW):
        self.stack = ExitStack()
        st_ = state_for(snap)
        for p in (mock.patch.object(rm, "current_mode", return_value=rm.COMMUNITY_CLOUD_MODE), mock.patch.object(ss, "remote_enabled", return_value=True),
                  mock.patch.object(ss, "current", return_value=st_), mock.patch.object(ui, "_utcnow", return_value=now)):
            self.stack.enter_context(p)
        self.at = AppTest.from_file(PAGE, default_timeout=120)
        self.at.run()

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


class TestAMissingMarketIsExplainedNotHidden(unittest.TestCase):
    """Points are bought with whatever credits the day has left after shots and anytime goals; the builder names the gap and its reason instead of silently dropping the market."""
    def snap(self, status):
        s = snapshot()
        s["builder_pool"] = builder_pool_doc()
        s["builder_pool"]["games"][GAME]["players"]["P1"]["m"].pop("PTS", None)
        s["tickets"]["diagnostics"] = {"price_availability": {"today": {"games": {GAME: {"matchup": "BBB at AAA", "start_utc": "2026-10-08T23:00:00Z", "markets": {
            "player_points": {"status": status, "checked_utc": "2026-10-08T21:58:00Z", "outcomes": 0},
            "player_goal_scorer_anytime": {"status": "NOT_POSTED", "checked_utc": "2026-10-08T21:58:00Z", "outcomes": 0}}}}}}}
        return s

    def test_a_budget_blocked_points_market_says_so_on_the_page(self):
        r = Runner(self.snap("BUDGET_BLOCKED"))
        try:
            txt = r.text()
        finally:
            r.close()
        self.assertIn("Points: no prices on file for this game", txt)
        self.assertIn("credit budget", txt)
        self.assertIn("Anytime goal: no prices on file for this game", txt)
        self.assertIn("Not posted by DraftKings yet", txt)

    def test_a_posted_but_absent_market_is_not_blamed_on_the_budget(self):
        r = Runner(self.snap("NOT_POSTED"))
        try:
            txt = r.text()
        finally:
            r.close()
        self.assertIn("Points: no prices on file for this game — Not posted by DraftKings yet", txt)


class TestBrowsing(Case):
    def test_it_shows_players_markets_lines_with_real_prices_and_freshness(self):
        r = self.run_()
        t = r.text()
        for expect in ("Pick a player", "Choose a market", "Choose a line", "Test Skater One 2+ shots on goal", "-110", "fresh"):
            self.assertIn(expect, t)
        self.assertIn("Nothing on your slip yet", t)

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
    def test_adding_a_line_puts_it_on_the_slip_with_the_price_and_a_hypothetical_stake(self):
        r = self.run_().add("P1", "SOG", 2)
        t = r.text()
        self.assertIn("Your slip", t)
        self.assertIn("Quoted price", t)
        self.assertIn("-110", t)
        self.assertIn("Hypothetical stake", t)
        self.assertIn("Potential return", t)

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
        self.assertEqual([l["threshold"] for l in r.at.session_state["_slip"]], [3])      # one leg per player and market: the new line replaces the old
        r.press("pb_rm_0_P1_PLAYER_SOG_ALTERNATE")
        self.assertEqual(r.at.session_state["_slip"], [])

    def test_a_shots_line_shows_the_recent_record_the_model_the_estimate_and_the_price_apart(self):
        pool = builder_pool_doc()
        pool["games"][GAME]["players"]["P1"]["m"]["SOG"]["rec"] = {"2": [24, 30, 0.7263]}
        r = self.run_(snap_with(pool))
        t = r.text()
        self.assertIn("Recent 24/30 (80%)", t)
        self.assertIn("Model 56%", t)
        self.assertIn("Estimate 73%", t)
        self.assertIn("Price implies 52%", t)                                                  # -110 implies 52.4%
        self.assertIn("Model 31%", t)                                                          # a line without a record keeps the plain model caption

    def test_clear_slip(self):
        r = self.run_().add("P1", "SOG", 2).press("pb_clear")
        self.assertEqual(r.at.session_state["_slip"], [])

    def test_legs_from_the_same_game_are_an_unpriced_draft_with_no_payout(self):
        r = self.run_().add("P1", "SOG", 2)
        r.at.selectbox(key="pb_player").set_value(("P2", GAME))
        r.at.run()
        r.add("P2", "SOG", 2)
        md = " ".join(m.value for m in r.at.markdown)
        self.assertIn("Unpriced draft", md)
        self.assertIn("no price", r.text())
        self.assertNotIn("Potential return", md)                                                 # no payout is invented for a same-game draft
        self.assertNotIn("Estimated price", md)
        r.press("pb_rm_1_P2_PLAYER_SOG_ALTERNATE")
        self.assertIn("Quoted price", " ".join(m.value for m in r.at.markdown))                   # back to one leg: a quote again

    def test_the_potential_return_is_computed_from_the_leg_price_and_the_hypothetical_stake(self):
        r = self.run_().add("P1", "SOG", 2)
        r.at.number_input(key="_slip_stake").set_value(55.0)
        r.at.run()
        self.assertIn("$105.00", " ".join(m.value for m in r.at.markdown))                # 55 stake at -110 returns 55 + 50


class TestItIsReadOnly(Case):
    """No accounts, no passcodes, no submission: the page cannot record a bet anywhere."""
    def test_there_is_no_submit_control_no_account_prompt_and_no_write_path(self):
        r = self.run_().add("P1", "SOG", 2)
        labels = [b.label.lower() for b in r.at.button]
        self.assertFalse(any("submit" in l or "add to" in l and "book" in l for l in labels), labels)
        txt = r.text().lower()
        for word in ("passcode", "my bets", "destination", "log_write_token", "create an account"):
            self.assertNotIn(word, txt)
        self.assertIn("nothing here is placed", txt)
        self.assertEqual([b.key for b in r.at.button if (b.key or "").startswith("pb_submit")], [])

    def test_the_page_source_has_no_order_client_or_personal_account_import(self):
        import pathlib
        src = pathlib.Path(PAGE).read_text()
        for needle in ("order_client", "personal_logs", "log_signing", "file_order", "selected_log"):
            self.assertNotIn(needle, src)


if __name__ == "__main__":
    unittest.main()
