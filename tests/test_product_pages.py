"""Every product page renders from a published snapshot (hosted mode) without an exception, shows what it should, and shows a truthful
unavailable state when the data is missing. Browsing never writes."""
from __future__ import annotations

import copy
import os
import re
import unittest
from unittest import mock

from streamlit.testing.v1 import AppTest

from dashboard import order_client
from dashboard import page_registry
from dashboard import snapshot_source as ss
from operational import runtime_mode as rm
import json

from operational import log_signing
from operational import personal_logs as pl
from tests.product_fixture import GEN, LOG_CODE, LOG_KEY, snapshot

PAGES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dashboard", "pages")
BANNED = re.compile(r"(?i)\b(simulated|demo|fixture|sample data|lorem)\b")


def run_page(file, snap, *, setup=None, timeout=120):
    state = ss.SnapshotState(**{f: None for f in ss.SnapshotState._fields})._replace(
        data=snap, source="REMOTE", fetch_status="OK", freshness="CURRENT", schema_version=2) if snap is not None else \
        ss.SnapshotState(**{f: None for f in ss.SnapshotState._fields})._replace(data=None, source="NONE", fetch_status="FAILED", last_error="network error: URLError",
                                                                                  last_success_utc=None, freshness="UNAVAILABLE")
    with mock.patch.object(rm, "current_mode", return_value=rm.COMMUNITY_CLOUD_MODE), mock.patch.object(ss, "remote_enabled", return_value=True), \
            mock.patch.object(ss, "current", return_value=state):
        at = AppTest.from_file(os.path.join(PAGES, file), default_timeout=timeout)
        if setup:
            setup(at)
        at.run()
    return at


def rerun(at, snap):
    """at.run() inside the same hosted-mode patches run_page uses (a bare at.run() would fall back to local mode)."""
    state = ss.SnapshotState(**{f: None for f in ss.SnapshotState._fields})._replace(data=snap, source="REMOTE", fetch_status="OK", freshness="CURRENT", schema_version=2)
    with mock.patch.object(rm, "current_mode", return_value=rm.COMMUNITY_CLOUD_MODE), mock.patch.object(ss, "remote_enabled", return_value=True), \
            mock.patch.object(ss, "current", return_value=state):
        at.run()
    return at


def choose_log(at):
    at.session_state["_personal_log"] = {"hash": pl.code_hash(LOG_CODE), "name": "Casey", "creation": None, "write_key": LOG_KEY}


def choose_log_view_only(at, key=None):
    at.session_state["_personal_log"] = {"hash": pl.code_hash(LOG_CODE), "name": "Casey", "creation": None, "write_key": key}


def text(at):
    return " ".join([m.value for m in at.markdown] + [c.value for c in at.caption] + [t.value for t in at.title] + [h.value for h in at.subheader]
                    + [w.value for w in at.warning] + [i.value for i in at.info] + [e.value for e in at.error])


CORE = ["38_My_Bets.py", "21_Today.py", "1_Game_Slate.py", "2_Game_Detail.py", "26_Player_Props.py", "30_Players.py", "27_Goalies.py", "31_Team_Intelligence.py",
        "22_Model_Health.py", "33_Paper_Performance.py", "23_Ledger.py"]


class TestPagesRender(unittest.TestCase):
    def test_every_core_page_renders_and_has_no_simulated_wording(self):
        snap = snapshot()
        for f in CORE:
            at = run_page(f, snap)
            self.assertEqual(len(at.exception), 0, f"{f}: {[str(e.value)[:200] for e in at.exception]}")
            self.assertFalse(BANNED.search(text(at)), f)

    def test_every_core_page_is_unavailable_not_blank_or_fake_when_there_is_no_data(self):
        for f in CORE:
            at = run_page(f, None)
            self.assertEqual(len(at.exception), 0, f"{f}: {[str(e.value)[:200] for e in at.exception]}")
            t = text(at)
            self.assertIn("unavailable", t.lower(), f)
            self.assertIn("network error", t, f)
            self.assertEqual(len(at.dataframe), 0, f)

    def test_games_defaults_to_the_current_date_and_season_and_lists_that_days_games(self):
        at = run_page("1_Game_Slate.py", snapshot())
        self.assertIn("October 8, 2026", text(at))
        self.assertIn("BBB @ AAA", text(at))
        self.assertNotIn("2025-11-25", text(at))
        season = next(s for s in at.selectbox if s.label == "Season")
        self.assertEqual(season.value, "2026-27")

    def test_games_date_picker_reaches_a_past_final_with_its_score(self):
        at = run_page("1_Game_Slate.py", snapshot(), setup=lambda a: a.session_state.__setitem__("games_date", "2026-10-07"))
        self.assertIn("AAA 2 – BBB 3 (OT)", " ".join(m.value for m in at.metric))
        self.assertIn("in the past", text(at))

    def test_game_detail_opens_exactly_the_requested_game(self):
        at = run_page("2_Game_Detail.py", snapshot(), setup=lambda a: a.query_params.__setitem__("game", "2026020899"))
        self.assertIn("AAA @ BBB", text(at))
        self.assertEqual(next(s for s in at.selectbox if s.label == "Game").value, "2026020899")
        self.assertIn("Final score", " ".join(m.label for m in at.metric))

    def test_game_detail_for_an_unknown_game_does_not_substitute_another(self):
        at = run_page("2_Game_Detail.py", snapshot(), setup=lambda a: a.query_params.__setitem__("game", "1999999999"))
        self.assertIn("is not in the schedule on file", text(at))

    def test_game_detail_current_game_shows_goalies_players_and_unconfirmed_status(self):
        at = run_page("2_Game_Detail.py", snapshot())
        t = text(at)
        cells = " ".join(str(d.value.to_dict()) for d in at.dataframe)
        self.assertIn("Test Goalie One", cells)
        self.assertIn("Unconfirmed", cells)
        self.assertIn("inferred from recent ice time", t)
        self.assertIn("Est. usage tier", cells)
        self.assertIn("Reported line", cells)

    def test_goalies_page_shows_the_required_fields(self):
        at = run_page("27_Goalies.py", snapshot(), setup=lambda a: a.query_params.__setitem__("goalie", "G1"))
        labels = " ".join(m.label for m in at.metric)
        for want in ("Save %", "GAA", "Shutouts", "W-L-OTL", "Start chance (estimate)", "Start status", "Expected saves", "Expected goals against", "AAA win chance"):
            self.assertIn(want, labels)
        t = text(at)
        self.assertIn("Unconfirmed", " ".join(m.value for m in at.metric))
        self.assertIn("NHL.com", t)
        self.assertIn("Recent appearances", t)

    def test_players_page_shows_usage_role_source_and_projection(self):
        at = run_page("30_Players.py", snapshot(), setup=lambda a: a.query_params.__setitem__("player", "P1"))
        t = text(at)
        self.assertIn("Estimated usage (inferred from ice time", t)
        self.assertIn("not an assigned line or power-play unit", t)
        self.assertIn("Reported lineup (from a lineup source)", t)
        self.assertIn("Test Reporter", t)
        self.assertNotIn("Role (inferred)", t)
        self.assertIn("Next game (projection)", t)
        self.assertIn("Expected shots", " ".join(m.label for m in at.metric))
        self.assertIn("Best qualifying +100 option", t)

    def test_diagnostics_order_path_panel_shows_booleans_and_a_check_button(self):
        at = run_page("37_Diagnostics.py", snapshot())
        t = text(at) + " " + " ".join(str(d.value.to_dict()) for d in at.dataframe)
        self.assertIn("Order path (one-click add) check", t)
        self.assertIn("One-click (direct) path ready", t)
        self.assertIn("never displayed", t)
        self.assertIn("Run non-staking order-path check", [b.label for b in at.button])

    def test_best_options_page_labels_quotes_and_estimates(self):
        at = run_page("26_Player_Props.py", snapshot())
        t = text(at)
        self.assertNotIn("redundant", t.lower())
        flat = " ".join(m.value for m in at.markdown)
        self.assertIn("<span class='k'>Estimated price</span>", flat)
        self.assertIn("class='odds'", flat)

    def test_without_a_chosen_log_no_add_button_is_offered_and_the_page_says_where_to_go(self):
        at = run_page("26_Player_Props.py", snapshot())
        self.assertFalse(any(b.label.startswith("Add to ") for b in at.button))
        self.assertIn("open or create your own log on My Bets", text(at).replace("**", ""))

    def test_with_a_log_chosen_the_destination_is_named_and_browsing_writes_nothing(self):
        at = run_page("26_Player_Props.py", snapshot(), setup=choose_log)
        t = text(at)
        self.assertIn("Adding to your personal log Casey", t.replace("<b>", "").replace("</b>", ""))
        self.assertIn("separate from the model book", t)
        self.assertTrue(any(b.label == "Add to Casey — $10.00" for b in at.button))        # offered, not pressed
        self.assertEqual(at.session_state.filtered_state.get("_personal_orders") or {}, {})

    def test_knowing_the_code_without_the_write_key_is_view_only_and_offers_no_add_button(self):
        for key in (None, "ZZZZYYYYXXXXWWWWVVVV"):
            at = run_page("26_Player_Props.py", snapshot(), setup=lambda a, k=key: choose_log_view_only(a, k))
            self.assertFalse(any(b.label.startswith("Add to ") for b in at.button), key)
            self.assertIn("view-only", text(at))

    def test_add_click_files_exactly_one_outstanding_order_for_that_log_and_a_second_click_does_nothing(self):
        at = run_page("26_Player_Props.py", snapshot(), setup=choose_log)
        btn = next(b for b in at.button if b.label == "Add to Casey — $10.00")
        btn.click()
        with mock.patch.object(rm, "current_mode", return_value=rm.COMMUNITY_CLOUD_MODE), mock.patch.object(ss, "remote_enabled", return_value=True), \
                mock.patch.object(ss, "current", return_value=ss.SnapshotState(**{f: None for f in ss.SnapshotState._fields})._replace(data=snapshot(), source="REMOTE", fetch_status="OK", freshness="CURRENT")):
            at.run()
        orders = at.session_state["_personal_orders"]
        self.assertEqual(len(orders), 1)
        only = next(iter(orders.values()))
        self.assertEqual(only["log"], pl.code_hash(LOG_CODE))
        self.assertIn("issues/new?labels=personal-bet", only["url"])
        self.assertNotIn("otter", only["url"])                                         # the code is never in an order
        outstanding = [b for b in at.button if b.label == "Add to Casey — $10.00"]
        self.assertEqual(len(outstanding), len(snapshot()["tickets"]["options"]["options"]) - 1)

    def test_direct_write_uses_the_app_credential_once_and_never_shows_it(self):
        calls = []

        def fake_submit(order, token, **kw):
            calls.append((order, token))
            return {"ok": True, "issue": 77}
        with mock.patch.object(order_client, "submit_direct", side_effect=fake_submit):
            at = run_page("26_Player_Props.py", snapshot(), setup=lambda a: (choose_log(a), a.secrets.__setitem__("LOG_WRITE_TOKEN", "t0ken-value")))
            next(b for b in at.button if b.label == "Add to Casey — $10.00").click()
            with mock.patch.object(rm, "current_mode", return_value=rm.COMMUNITY_CLOUD_MODE), mock.patch.object(ss, "remote_enabled", return_value=True), \
                    mock.patch.object(ss, "current", return_value=ss.SnapshotState(**{f: None for f in ss.SnapshotState._fields})._replace(data=snapshot(), source="REMOTE", fetch_status="OK", freshness="CURRENT")):
                at.run()
        self.assertEqual(len(calls), 1)
        order, token = calls[0]
        self.assertTrue(log_signing.verify(order, log_signing.public_key_hex(LOG_KEY, pl.code_hash(LOG_CODE))), "the filed order must carry a valid signature")
        self.assertEqual((order["type"], order["log"]["hash"], token), ("PERSONAL_BET", pl.code_hash(LOG_CODE), "t0ken-value"))
        self.assertNotIn("t0ken-value", text(at))
        self.assertNotIn("otter-maple", json.dumps(order))
        self.assertEqual(next(iter(at.session_state["_personal_orders"].values()))["issue"], 77)

    def test_engine_answers_are_shown_on_the_card(self):
        snap = snapshot()
        h = pl.code_hash(LOG_CODE)
        at = run_page("26_Player_Props.py", snap, setup=choose_log)
        oid = next(iter(snap["tickets"]["options"]["options"]))["option_id"]
        next(b for b in at.button if b.label == "Add to Casey — $10.00").click()
        rerun(at, snap)
        order_id = next(iter(at.session_state["_personal_orders"].values()))["order_id"]
        snap2 = snapshot()
        snap2["personal_logs"]["logs"][h]["orders"] = [{"order_id": order_id, "kind": "PERSONAL_BET", "status": "REJECTED", "reason": "stale quote", "bet_id": None, "processed_at_utc": GEN}]
        at2 = run_page("26_Player_Props.py", snap2, setup=lambda a: (choose_log(a), a.session_state.__setitem__("_personal_orders", {oid: {"order_id": order_id, "log": h}})))
        self.assertIn("stale quote", text(at2))


class TestMyBetsPage(unittest.TestCase):
    def test_renders_with_the_code_warning_and_no_simulated_wording(self):
        at = run_page("38_My_Bets.py", snapshot())
        self.assertEqual(len(at.exception), 0, [str(e.value)[:200] for e in at.exception])
        t = text(at)
        flat = t.replace("<b>", "").replace("</b>", "")
        self.assertIn("What is public, and what is protected", flat)
        self.assertIn("Reading is therefore not private. Writing is protected", flat)
        self.assertIn("public repository", t)
        self.assertFalse(BANNED.search(t))

    def test_opening_a_published_code_selects_the_log_and_an_unknown_code_is_refused(self):
        at = run_page("38_My_Bets.py", snapshot())
        at.text_input(key="mb_open_code").set_value(LOG_CODE); rerun(at, snapshot())
        next(b for b in at.button if b.key == "mb_open").click(); rerun(at, snapshot())
        self.assertEqual(at.session_state["_personal_log"]["hash"], pl.code_hash(LOG_CODE))
        self.assertIn("Casey", " ".join(m.value for m in at.markdown))
        at2 = run_page("38_My_Bets.py", snapshot())
        at2.text_input(key="mb_open_code").set_value("no-such-code-123"); rerun(at2, snapshot())
        next(b for b in at2.button if b.key == "mb_open").click(); rerun(at2, snapshot())
        self.assertTrue(any("No log uses that code" in e.value for e in at2.error))
        self.assertNotIn("_personal_log", at2.session_state)

    def test_creating_a_log_shows_the_code_and_write_key_once_and_files_a_signed_order_without_them(self):
        filed = []
        with mock.patch.object(order_client, "submit_direct", side_effect=lambda o, t, **kw: filed.append(o) or {"ok": True, "issue": 5}):
            at = run_page("38_My_Bets.py", snapshot(), setup=lambda a: a.secrets.__setitem__("LOG_WRITE_TOKEN", "tk"))
            at.text_input(key="mb_new_name").set_value("Dana"); rerun(at, snapshot())
            at.text_input(key="mb_new_code").set_value("brand-new-code-2468"); rerun(at, snapshot())
            next(b for b in at.button if b.key == "mb_create").click(); rerun(at, snapshot())
        self.assertEqual(len(filed), 1)
        doc = filed[0]
        h = pl.code_hash("brand-new-code-2468")
        self.assertEqual((doc["type"], doc["log"]["hash"]), ("PERSONAL_LOG_CREATE", h))
        self.assertTrue(log_signing.verify(doc, doc["log"]["create"]["write_pub"]))
        blob = json.dumps(doc)
        self.assertNotIn("brand-new-code", blob)
        shown = " ".join(c.value for c in at.code)
        self.assertIn("brand-new-code-2468", shown)
        key = shown.split("Write key: ")[1].strip()
        self.assertTrue(log_signing.valid_key_shape(key))
        self.assertNotIn(log_signing.normalize_key(key), blob.replace("-", ""))
        self.assertEqual(log_signing.public_key_hex(log_signing.normalize_key(key), h), doc["log"]["create"]["write_pub"])
        self.assertIn("only time they are shown", " ".join(m.value for m in at.markdown))
        next(b for b in at.button if b.key == "mb_saved").click(); rerun(at, snapshot())
        self.assertEqual(len(at.code), 0)                                              # gone once acknowledged

    def test_creating_with_a_code_already_in_use_is_refused_without_filing_anything(self):
        with mock.patch.object(order_client, "submit_direct") as sub:
            at = run_page("38_My_Bets.py", snapshot())
            at.text_input(key="mb_new_name").set_value("Dana"); rerun(at, snapshot())
            at.text_input(key="mb_new_code").set_value(LOG_CODE); rerun(at, snapshot())
            next(b for b in at.button if b.key == "mb_create").click(); rerun(at, snapshot())
        self.assertTrue(any("already in use" in e.value for e in at.error))
        sub.assert_not_called()

    def test_my_model_and_both_views_keep_the_two_books_apart(self):
        at = run_page("38_My_Bets.py", snapshot(), setup=choose_log)
        self.assertEqual(at.radio(key="mb_view").options, ["My bets", "Model bets", "Both"])
        at.radio(key="mb_view").set_value("Both"); rerun(at, snapshot())
        md = " ".join(m.value for m in at.markdown)
        self.assertIn("My log", md)
        self.assertIn("Model book", md)
        self.assertIn("kept separate on purpose", " ".join(c.value for c in at.caption))
        at.radio(key="mb_view").set_value("Model bets"); rerun(at, snapshot())
        self.assertEqual(len(at.exception), 0)

    def test_page_without_the_section_is_unavailable_not_an_error(self):
        snap = snapshot()
        del snap["personal_logs"]
        at = run_page("38_My_Bets.py", snap)
        self.assertEqual(len(at.exception), 0)
        self.assertIn("not published yet", " ".join(w.value for w in at.warning) + text(at))


    def test_goalie_page_has_the_best_option_section_and_says_why_when_there_is_none(self):
        at = run_page("27_Goalies.py", snapshot(), setup=lambda a: a.query_params.__setitem__("goalie", "G1"))
        self.assertEqual(len(at.exception), 0, [str(e.value)[:200] for e in at.exception])
        t = text(at)
        self.assertIn("Best qualifying +100 option", t)
        self.assertIn("No qualifying option for this goalie right now", t)
        self.assertIn("not a substitute for his own line", t)

    def test_model_health_states_validation_and_blocked_markets_without_relabelling(self):
        at = run_page("22_Model_Health.py", snapshot())
        t = text(at)
        self.assertIn("13 of 14 threshold markets beat both simple baselines", t)
        self.assertIn("hits>=1", t)
        self.assertIn("claims profitability", t)

    def test_today_shows_account_games_and_empty_slot_explanation(self):
        at = run_page("21_Today.py", snapshot())
        t = text(at)
        self.assertIn("Today's games — 2026-10-08", t)
        self.assertIn("5 slot(s) empty", t)
        self.assertEqual({m.label: m.value for m in at.metric}["Available cash"], "$500.00")

    def test_a_missing_section_in_an_older_snapshot_is_unavailable_not_an_error(self):
        snap = snapshot()
        del snap["product_goalies"]
        at = run_page("27_Goalies.py", snap)
        self.assertEqual(len(at.exception), 0)
        self.assertIn("older engine version", text(at))

    def test_registered_pages_all_exist_and_core_ones_are_cloud_pages(self):
        for spec in page_registry.PAGES:
            self.assertTrue(os.path.exists(os.path.join(PAGES, spec.file)), spec.file)


if __name__ == "__main__":
    unittest.main()
