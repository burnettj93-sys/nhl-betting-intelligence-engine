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
from dashboard import ui
from dashboard import page_registry
from dashboard import snapshot_source as ss
from operational import runtime_mode as rm
import json

from operational import log_signing
from operational import personal_logs as pl
from tests.product_fixture import ACCT_PASS, ACCT_SURNAME, GEN, LOG_CODE, LOG_KEY, snapshot

PAGES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dashboard", "pages")
FIXTURE_NOW = __import__("datetime").datetime(2026, 10, 15, 17, 5, tzinfo=__import__("datetime").timezone.utc)      # five minutes after the fixture option prices (tests.test_daily_tickets.NOW is 17:00 UTC on 2026-10-15)
BANNED = re.compile(r"(?i)\b(simulated|demo|fixture|sample data|lorem)\b")


def run_page(file, snap, *, setup=None, timeout=120):
    state = ss.SnapshotState(**{f: None for f in ss.SnapshotState._fields})._replace(
        data=snap, source="REMOTE", fetch_status="OK", freshness="CURRENT", schema_version=2) if snap is not None else \
        ss.SnapshotState(**{f: None for f in ss.SnapshotState._fields})._replace(data=None, source="NONE", fetch_status="FAILED", last_error="network error: URLError",
                                                                                  last_success_utc=None, freshness="UNAVAILABLE")
    with mock.patch.object(rm, "current_mode", return_value=rm.COMMUNITY_CLOUD_MODE), mock.patch.object(ss, "remote_enabled", return_value=True), \
            mock.patch.object(ss, "current", return_value=state), mock.patch.object(ui, "_utcnow", return_value=FIXTURE_NOW):
        at = AppTest.from_file(os.path.join(PAGES, file), default_timeout=timeout)
        if setup:
            setup(at)
        at.run()
    return at


def rerun(at, snap):
    """at.run() inside the same hosted-mode patches run_page uses (a bare at.run() would fall back to local mode)."""
    state = ss.SnapshotState(**{f: None for f in ss.SnapshotState._fields})._replace(data=snap, source="REMOTE", fetch_status="OK", freshness="CURRENT", schema_version=2)
    with mock.patch.object(rm, "current_mode", return_value=rm.COMMUNITY_CLOUD_MODE), mock.patch.object(ss, "remote_enabled", return_value=True), \
            mock.patch.object(ss, "current", return_value=state), mock.patch.object(ui, "_utcnow", return_value=FIXTURE_NOW):
        at.run()
    return at


def with_token(at):
    at.secrets["LOG_WRITE_TOKEN"] = "tk"


def choose_log(at):
    at.session_state["_personal_log"] = {"hash": pl.code_hash(LOG_CODE), "name": "Casey", "creation": None, "write_key": LOG_KEY}


def choose_log_view_only(at, key=None):
    at.session_state["_personal_log"] = {"hash": pl.code_hash(LOG_CODE), "name": "Casey", "creation": None, "write_key": key}


def text(at):
    return " ".join([m.value for m in at.markdown] + [c.value for c in at.caption] + [t.value for t in at.title] + [h.value for h in at.subheader]
                    + [w.value for w in at.warning] + [i.value for i in at.info] + [e.value for e in at.error])


CORE = ["38_My_Bets.py", "21_Today.py", "1_Game_Slate.py", "2_Game_Detail.py", "26_Player_Props.py", "30_Players.py", "27_Goalies.py", "31_Team_Intelligence.py",
        "22_Model_Health.py", "33_Paper_Performance.py", "23_Ledger.py", "39_Tomorrow.py"]


class TestPostmortemIsPublic(unittest.TestCase):
    def test_the_daily_review_is_readable_on_the_hosted_app_without_an_admin_login(self):
        at = run_page("36_Morning_Review.py", snapshot())
        self.assertEqual(len(at.exception), 0, [str(e.value)[:200] for e in at.exception])
        self.assertNotIn("restricted to the administrator", " ".join([e.value for e in at.error] + [m.value for m in at.markdown]))
        self.assertEqual(at.title[0].value, "Morning Review")


class TestReviewShowsGapsAndMoneylines(unittest.TestCase):
    def test_a_logging_gap_and_graded_moneylines_are_shown_on_the_review(self):
        import copy
        snap = copy.deepcopy(snapshot())
        acct = {"available_cash": 435.76, "open_stakes": 0.0, "equity": 435.76, "settled_pnl": -64.24}
        origin = {"tickets": 12, "wins": 2, "losses": 10, "voids": 0, "pending": 0, "unresolved": 0, "settled_pnl": -64.24}
        rv = {"review_date_et": "2026-10-11", "generated_at_utc": "2026-10-12T16:02:00Z", "account": acct, "origins": {"AUTOMATIC": origin}, "tickets": [], "open_tickets": [], "loss_postmortems": [],
              "defects": [], "notes": [], "variance": [], "calibration": {}, "proposals": [], "policy": {"min_legs_for_a_conclusion": 30, "z_bar": 2.0},
              "predictions": {"review_date_et": "2026-10-11", "snapshots_logged": 2, "supported_legs_logged": 0, "resolved": 51, "awaiting_games": 0, "did_not_play": 1, "anchor_estimates": {},
                              "moneyline_legs": {"legs": 24, "games": 12, "price_implied_before_margin": 0.522, "happened": 0.5}, "scorer_revision": 2, "selected_tickets": [],
                              "exposure": {"tickets_recorded_that_day": 0, "players_on_several_tickets": [], "games_on_several_tickets": []}, "participation_and_data": [], "corrective_actions": ["None."],
                              "logging_gaps": [{"from_utc": "2026-10-11T15:29:23Z", "to_utc": "2026-10-12T00:42:15Z", "kind": "LOGGER_NOT_WRITTEN_WHILE_RECORDING_ON", "reason": "the log was written only while paused"}]}}
        snap["morning_review"] = {"generated_at_utc": "2026-10-12T16:02:00Z", "daily_review": rv}
        at = run_page("36_Morning_Review.py", snap)
        self.assertEqual(len(at.exception), 0, [str(e.value)[:200] for e in at.exception])
        t = text(at)
        self.assertIn("Logging gap", t)
        self.assertIn("Moneylines graded as team outcomes: 24 leg(s) in 12 game(s)", t)
        self.assertIn("51 prop leg(s) resolved", t)


class TestTheAccountIsShownSplit(unittest.TestCase):
    def test_today_and_paper_performance_show_the_parlay_experiment_the_single_bets_and_the_whole_account(self):
        for page in ("21_Today.py", "33_Paper_Performance.py"):
            at = run_page(page, snapshot())
            self.assertEqual(len(at.exception), 0, f"{page}: {[str(e.value)[:200] for e in at.exception]}")
            blob = text(at) + " ".join(str(df.value.to_dict("records")) for df in at.dataframe)
            for expect in ("Parlay experiment (tickets)", "Single bets: moneyline and props", "Whole account", "$54.24", "$10.00", "$64.24"):
                self.assertIn(expect, blob, f"{page}: {expect}")
            self.assertIn("The parts add up to the account", blob, page)
            self.assertIn("Cash \\$435.76 = \\$500 start", blob, page)                 # dollar signs escaped, or the caption renders as maths

    def test_today_describes_the_ticket_policy_in_force_and_says_it_is_an_unapproved_proposal(self):
        snap = snapshot()
        snap["tickets"]["ticket_policy"] = {"policy_id": "restart-proposal-x", "status": "PROPOSED_NOT_VALIDATED_AWAITING_OWNER_APPROVAL", "digest": "abc", "approved": False,
                                           "summary": "up to 5 tickets a day (zero is allowed); each +100 or better; estimated hit chance of at least 25%"}
        blob = text(run_page("21_Today.py", snap))
        self.assertIn("up to 5 tickets a day (zero is allowed)", blob)
        self.assertIn("A proposal", blob)
        self.assertIn("automatic recording stays paused", blob)
        self.assertIn("an empty slot is a correct answer", blob)

    def test_today_shows_a_proposed_anchor_ticket_with_its_evidence_and_says_nothing_is_staked(self):
        snap = snapshot()
        leg = lambda name, price, hits, model, est, gid: {  # noqa: E731
            "label": f"{name} 2+ shots on goal", "market_family": "PLAYER_SOG_ALTERNATE", "game_id": gid, "participant_id": name, "participant_name": name, "team": "T", "opponent": "O",
            "game_start_utc": "2026-10-08T23:00:00Z", "american_price": price, "decimal_price": 1.0, "price_captured_at_utc": "2026-10-08T14:00:00Z", "probability": est, "model_version": "m",
            "outcome": None, "provisional": False, "evidence": {"kind": "player_record", "hits": hits, "games": 30, "recent_hit_rate": hits / 30, "model": model, "estimate": est, "price_implied": 0.65, "record_shrunk": est}}
        snap["tickets"]["tickets"] = [{
            "ticket_id": "TPROP1", "status": "RECOMMENDED", "recorded": False, "proposed": True, "origin": "AUTOMATIC", "provenance": None, "alerts": [], "result": None,
            "legs": [leg("Ehlers", -185, 24, 0.63, 0.726, "1"), leg("McMann", -260, 26, 0.695, 0.779, "2")], "combined_decimal": 2.13, "combined_american": 113.0, "price_label": "x",
            "stake": 10.0, "potential_return": 21.3, "potential_profit": 11.3, "hit_probability": 0.565, "ev_estimated": 0.2, "ev_after_haircut": 0.1, "haircut_margin": 0.03,
            "rationale": "Ehlers 2+ shots in 24 of his last 30 games.", "recorded_at_utc": None, "event_start_utc": "2026-10-08T23:00:00Z",
            "anchor": {"hit_low": 0.47, "hit_high": 0.66, "midpoint_ev": 0.1, "ev_after_shade": 0.11}}]
        snap["tickets"]["slots"] = {"total": 5, "used": 1, "empty": 4}
        at = run_page("21_Today.py", snap)
        self.assertEqual(len(at.exception), 0, [str(e.value)[:300] for e in at.exception])
        blob = text(at)
        self.assertIn("Proposed — not recorded", blob)
        self.assertIn("nothing is staked", blob)
        self.assertIn("recent 24 of last 30 games (80%)", blob)
        self.assertIn("Final estimate 73%", blob)
        self.assertIn("range 47–66%", blob)

    def test_a_split_that_does_not_reconcile_is_called_a_bookkeeping_fault(self):
        snap = snapshot()
        snap["tickets"]["origins"]["RECONCILIATION"]["all_agree"] = False
        blob = text(run_page("21_Today.py", snap))
        self.assertIn("DO NOT ADD UP", blob)


class TestADeepLinkBeforeTheEntryScriptStillLooksRight(unittest.TestCase):
    """After a restart the first visitor may open /Parlay_Builder directly: the page then runs without the entry script (no theme, raw page list)."""

    def test_a_page_run_on_its_own_applies_the_theme_and_hides_the_raw_page_list(self):
        at = run_page("21_Today.py", snapshot())
        blob = " ".join(m.value for m in at.markdown)
        self.assertIn("eggy-brand", blob)                                   # the theme is in
        self.assertIn("stSidebarNav", blob)                                 # and the raw page list is hidden

    def test_a_page_run_after_the_entry_script_does_not_inject_twice(self):
        at = run_page("21_Today.py", snapshot(), setup=lambda a: a.session_state.__setitem__("_frame_ran", True))
        blob = " ".join(m.value for m in at.markdown)
        self.assertNotIn("eggy-brand", blob)


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

    def test_a_box_score_row_without_power_play_time_shows_a_dash_and_says_why(self):
        import copy
        snap = copy.deepcopy(snapshot())
        p1 = snap["product_players"]["players"]["P1"] if "product_players" in snap and "players" in snap["product_players"] else None
        players = snap["product_players"] if p1 is None else snap["product_players"]["players"]
        row = {"date": "2026-10-11", "season": "2026-27", "opp": "MTL", "home": True, "toi": 17.0, "toi_pp": None, "shots": 3, "goals": 1, "assists": 0, "hits": None, "blocks": 0, "source": "NHL API box score"}
        players["P1"]["recent_games"] = [row] + list(players["P1"]["recent_games"])
        at = run_page("30_Players.py", snap, setup=lambda a: a.query_params.__setitem__("player", "P1"))
        self.assertEqual(len(at.exception), 0, [str(e.value)[:200] for e in at.exception])
        self.assertIn("box scores from the NHL API for games MoneyPuck has not published yet", text(at))
        frames = [d.value.to_dict("records") for d in at.dataframe]
        flat = [r for f in frames for r in f]
        self.assertTrue(any(r.get("Date") == "2026-10-11" and r.get("PP") == "—" and r.get("Hits") == "—" for r in flat), flat[:3])

    def test_diagnostics_order_path_panel_shows_booleans_and_a_check_button(self):
        at = run_page("37_Diagnostics.py", snapshot(), setup=lambda a: a.session_state.__setitem__("_owner_unlocked", True))      # administrative: needs the owner unlock
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
        at = run_page("26_Player_Props.py", snapshot(), setup=lambda a: (choose_log(a), with_token(a)))
        t = text(at)
        self.assertIn("Adding to your personal log Casey", t.replace("<b>", "").replace("</b>", ""))
        self.assertIn("Available cash in Casey: $500.00", t)
        self.assertIn("separate from the model book", t)
        self.assertTrue(any(b.label == "Add to Casey — $10.00" for b in at.button))        # offered, not pressed
        self.assertEqual(at.session_state.filtered_state.get("_personal_orders") or {}, {})

    def test_knowing_the_code_without_the_write_key_is_view_only_and_offers_no_add_button(self):
        for key in (None, "ZZZZYYYYXXXXWWWWVVVV"):
            at = run_page("26_Player_Props.py", snapshot(), setup=lambda a, k=key: choose_log_view_only(a, k))
            self.assertFalse(any(b.label.startswith("Add to ") for b in at.button), key)
            self.assertIn("view-only", text(at))

    def test_add_click_files_exactly_one_outstanding_order_for_that_log_and_a_second_click_does_nothing(self):
        filed = []
        with mock.patch.object(order_client, "submit_direct", side_effect=lambda o, t, **kw: filed.append(o) or {"ok": True, "issue": 3}):
            at = run_page("26_Player_Props.py", snapshot(), setup=lambda a: (choose_log(a), with_token(a)))
            btn = next(b for b in at.button if b.label == "Add to Casey — $10.00")
            btn.click()
            rerun(at, snapshot())
        self.assertEqual(len(filed), 1)
        orders = at.session_state["_personal_orders"]
        self.assertEqual(len(orders), 1)
        only = next(iter(orders.values()))
        self.assertEqual(only["log"], pl.code_hash(LOG_CODE))
        self.assertNotIn("otter", json.dumps(filed[0]))                                  # the code is never in an order
        outstanding = [b for b in at.button if b.label == "Add to Casey — $10.00"]
        self.assertEqual(len(outstanding), len(snapshot()["tickets"]["options"]["options"]) - 1)

    def test_without_a_write_credential_no_add_button_is_offered_to_anyone(self):
        at = run_page("26_Player_Props.py", snapshot(), setup=choose_log)
        self.assertFalse(any(b.label.startswith("Add to ") for b in at.button))
        self.assertIn("not switched on yet", text(at))

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
        with mock.patch.object(order_client, "submit_direct", return_value={"ok": True, "issue": 4}):
            at = run_page("26_Player_Props.py", snap, setup=lambda a: (choose_log(a), with_token(a)))
            oid = next(iter(snap["tickets"]["options"]["options"]))["option_id"]
            next(b for b in at.button if b.label == "Add to Casey — $10.00").click()
            rerun(at, snap)
        order_id = next(iter(at.session_state["_personal_orders"].values()))["order_id"]
        snap2 = snapshot()
        snap2["personal_logs"]["logs"][h]["orders"] = [{"order_id": order_id, "kind": "PERSONAL_BET", "status": "REJECTED", "reason": "stale quote", "bet_id": None, "processed_at_utc": GEN}]
        at2 = run_page("26_Player_Props.py", snap2, setup=lambda a: (choose_log(a), with_token(a), a.session_state.__setitem__("_personal_orders", {oid: {"order_id": order_id, "log": h}})))
        self.assertIn("stale quote", text(at2))


class TestMyBetsPage(unittest.TestCase):
    def test_the_address_reopens_an_account_view_only_and_the_passcode_unlocks_adding_without_being_kept_in_the_address(self):
        from tests.product_fixture import ACCT_PASS
        at = run_page("38_My_Bets.py", snapshot(), setup=lambda t: (with_token(t), t.query_params.__setitem__("account", "burnett  ")))     # case and stray spaces are normalised
        self.assertEqual(len(at.exception), 0, [str(e.value)[:200] for e in at.exception])
        self.assertIn("view-only", text(at))
        self.assertIn("Burnett", text(at))
        at.text_input(key="mb_unlock_key").set_value("WRONG-CODE")
        at.button(key="mb_unlock").click()
        rerun(at, snapshot())
        self.assertIn("does not belong", text(at))
        at.text_input(key="mb_unlock_key").set_value(ACCT_PASS)
        at.button(key="mb_unlock").click()
        rerun(at, snapshot())
        self.assertNotIn("view-only", text(at))
        self.assertNotIn(ACCT_PASS, str(dict(at.query_params)))                                   # the passcode is never in the address

    def test_renders_with_the_passcode_explanation_and_no_simulated_wording(self):
        at = run_page("38_My_Bets.py", snapshot())
        self.assertEqual(len(at.exception), 0, [str(e.value)[:200] for e in at.exception])
        t = text(at)
        flat = t.replace("<b>", "").replace("</b>", "")
        self.assertIn("Open your account with your last name", flat)
        self.assertIn("8-character passcode", flat)
        self.assertIn("its own $500", flat.replace("\\$", "$"))
        self.assertTrue(any("how it works" in e.label.lower() for e in at.expander))
        details = " ".join(m.value for m in at.markdown)
        self.assertIn("Reading is not private.", details)
        self.assertIn("Writing is protected.", details)
        self.assertIn("public GitHub repository", details)
        self.assertIn("Ed25519", details)
        self.assertIn("“Smith 2”", details)
        self.assertFalse(BANNED.search(t))
        self.assertFalse([w for w in at.text_input if w.label and "write key" in w.label.lower()])      # no 20-character key is asked for in everyday use

    def test_opening_by_last_name_selects_the_account_with_or_without_the_passcode(self):
        at = run_page("38_My_Bets.py", snapshot())
        at.text_input(key="mb_open_name").set_value("burnett"); rerun(at, snapshot())
        next(b for b in at.button if b.key == "mb_open").click(); rerun(at, snapshot())
        self.assertEqual(at.session_state["_personal_log"]["hash"], pl.account_key("burnett"))
        self.assertIsNone(at.session_state["_personal_log"]["write_key"])               # looked at, not writable
        self.assertIn("view-only", text(at))
        self.assertIn("Available cash", " ".join(m.label for m in at.metric))
        at2 = run_page("38_My_Bets.py", snapshot())
        at2.text_input(key="mb_open_name").set_value("Burnett"); at2.text_input(key="mb_open_key").set_value(ACCT_PASS.lower()); rerun(at2, snapshot())
        next(b for b in at2.button if b.key == "mb_open").click(); rerun(at2, snapshot())
        self.assertEqual(at2.session_state["_personal_log"]["write_key"], log_signing.normalize_key(ACCT_PASS))

    def test_a_wrong_passcode_or_an_unknown_name_is_refused(self):
        at = run_page("38_My_Bets.py", snapshot())
        at.text_input(key="mb_open_name").set_value("Burnett"); at.text_input(key="mb_open_key").set_value("ZZZZ-YYYY"); rerun(at, snapshot())
        next(b for b in at.button if b.key == "mb_open").click(); rerun(at, snapshot())
        self.assertTrue(any("does not belong to this account" in e.value for e in at.error))
        self.assertNotIn("_personal_log", at.session_state)
        at2 = run_page("38_My_Bets.py", snapshot())
        at2.text_input(key="mb_open_name").set_value("Burnett 2"); rerun(at2, snapshot())
        next(b for b in at2.button if b.key == "mb_open").click(); rerun(at2, snapshot())
        self.assertTrue(any("“Burnett 2”" in e.value for e in at2.error))

    def test_creating_an_account_shows_the_passcode_once_and_files_a_signed_order_without_it(self):
        filed = []
        with mock.patch.object(order_client, "submit_direct", side_effect=lambda o, t, **kw: filed.append(o) or {"ok": True, "issue": 5}):
            at = run_page("38_My_Bets.py", snapshot(), setup=with_token)
            at.text_input(key="mb_new_name").set_value("O'Brien"); rerun(at, snapshot())
            next(b for b in at.button if b.key == "mb_create").click(); rerun(at, snapshot())
        self.assertEqual(len(filed), 1)
        doc = filed[0]
        h = pl.account_key("obrien")
        self.assertEqual((doc["type"], doc["log"]["hash"], doc["log"]["create"]["slug"], doc["log"]["create"]["display_name"]), ("PERSONAL_LOG_CREATE", h, "obrien", "O'Brien"))
        self.assertTrue(log_signing.verify(doc, doc["log"]["create"]["write_pub"]))
        shown = " ".join(c.value for c in at.code)
        code = shown.split("Passcode: ")[1].strip()
        self.assertTrue(log_signing.valid_key_shape(code) and log_signing.is_passcode(code))
        self.assertNotIn(log_signing.normalize_key(code), json.dumps(doc).replace("-", ""))
        self.assertEqual(log_signing.public_key_hex(log_signing.normalize_key(code), h), doc["log"]["create"]["write_pub"])
        self.assertIn("only time it is shown", " ".join(m.value for m in at.markdown))
        next(b for b in at.button if b.key == "mb_saved").click(); rerun(at, snapshot())
        self.assertEqual(len(at.code), 0)                                              # gone once acknowledged

    def test_a_duplicate_last_name_is_explained_and_becomes_the_next_number(self):
        filed = []
        with mock.patch.object(order_client, "submit_direct", side_effect=lambda o, t, **kw: filed.append(o) or {"ok": True, "issue": 6}):
            at = run_page("38_My_Bets.py", snapshot(), setup=with_token)
            at.text_input(key="mb_new_name").set_value("Burnett"); rerun(at, snapshot())
            warn = " ".join(w.value for w in at.warning)
            self.assertIn("already an account for Burnett", warn)
            self.assertIn("If that is you, open it", warn)
            self.assertIn("Burnett 2", warn)
            self.assertIn("Create account “Burnett 2”", [b.label for b in at.button])
            next(b for b in at.button if b.key == "mb_create").click(); rerun(at, snapshot())
        doc = filed[0]
        self.assertEqual((doc["log"]["hash"], doc["log"]["create"]["slug"]), (pl.account_key("burnett-2"), "burnett-2"))
        self.assertNotEqual(doc["log"]["hash"], pl.account_key("burnett"))              # never the existing person's account

    def test_creating_is_not_offered_without_the_write_credential(self):
        at = run_page("38_My_Bets.py", snapshot())
        at.text_input(key="mb_new_name").set_value("Dana"); rerun(at, snapshot())
        self.assertFalse(any(b.key == "mb_create" for b in at.button))
        self.assertIn("not switched on yet", " ".join(c.value for c in at.caption))

    def test_my_model_and_both_views_keep_the_two_books_apart(self):
        at = run_page("38_My_Bets.py", snapshot(), setup=choose_log)
        self.assertEqual(at.radio(key="mb_view").options, ["My bets", "Model bets", "Both"])
        at.radio(key="mb_view").set_value("Both"); rerun(at, snapshot())
        md = " ".join(m.value for m in at.markdown)
        self.assertIn("My account", md)
        self.assertIn("Model book", md)
        self.assertIn("kept separate on purpose", " ".join(c.value for c in at.caption))
        at.radio(key="mb_view").set_value("Model bets"); rerun(at, snapshot())
        self.assertEqual(len(at.exception), 0)

    def test_the_account_shows_its_own_bankroll_figures(self):
        at = run_page("38_My_Bets.py", snapshot(), setup=choose_log)
        labels = {m.label: m.value for m in at.metric}
        for want in ("Available cash", "Open stakes", "Payouts received", "Settled P&L"):
            self.assertIn(want, labels)
        self.assertEqual(labels["Available cash"], "$500.00")

    def test_the_earlier_manual_ticket_is_explained_and_claimable_only_with_a_passcode(self):
        at = run_page("38_My_Bets.py", snapshot())
        self.assertTrue(any("earlier manual ticket" in e.label for e in at.expander))
        self.assertIn("moved out of the model", " ".join(m.value for m in at.markdown))
        self.assertFalse(any(b.key == "mb_claim" for b in at.button))                  # no account open: no claim box
        at2 = run_page("38_My_Bets.py", snapshot(), setup=choose_log)
        self.assertTrue(any(b.key == "mb_claim" for b in at2.button))
        at3 = run_page("38_My_Bets.py", snapshot(), setup=lambda a: choose_log_view_only(a, None))
        self.assertFalse(any(b.key == "mb_claim" for b in at3.button))
        self.assertIn("passcode first", " ".join(c.value for c in at3.caption))

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

    def test_model_health_has_the_market_by_market_audit_with_the_evidence_kinds_separated(self):
        at = run_page("22_Model_Health.py", snapshot())
        self.assertEqual(len(at.exception), 0, [str(e.value)[:200] for e in at.exception])
        labels = " ".join(e.label for e in at.expander)
        self.assertIn("Puck line / spread — BLOCKED", labels)
        body = " ".join(m.value for m in at.markdown)
        for kind in ("Provider contract", "Predictive validation", "Evidence of betting value", "Live performance"):
            self.assertIn(kind, body)

    def test_today_explains_the_selection_when_the_board_carries_an_audit(self):
        snap = snapshot()
        snap["tickets"].setdefault("diagnostics", {})["selection_audit"] = {"recording_cycles_today": [{
            "date_et": "2026-10-08", "at_utc": "2026-10-08T18:07:56Z", "slots_open": 5, "pool_legs": 27, "qualifying": 106, "selected": 2,
            "considered": [{"legs": ["A 2+ shots on goal", "B 2+ shots on goal"], "leg_prices": [-135.0, -140.0], "hit_probability": 0.387, "estimated_price": 198, "ev_estimated": 0.155,
                            "ev_after_haircut": 0.047, "status": "SELECTED", "reason": None},
                           {"legs": ["A 2+ shots on goal", "C 1+ point"], "leg_prices": [-135.0, 145.0], "hit_probability": 0.285, "estimated_price": 326, "ev_estimated": 0.214,
                            "ev_after_haircut": 0.079, "status": "BLOCKED_LEG_LIMIT", "reason": "A 2+ shots on goal is already on 2 tickets"}]}]}
        at = run_page("21_Today.py", snap)
        self.assertEqual(len(at.exception), 0)
        self.assertTrue(any("higher-hit alternatives" in e.label for e in at.expander))
        body = " ".join(m.value for m in at.markdown)
        self.assertIn("Skipped — leg limit", body)
        self.assertIn("already on 2 tickets", body)

    def test_a_stale_moneyline_is_marked_on_the_board_not_just_on_diagnostics(self):
        at = run_page("21_Today.py", snapshot())                       # the fixture's moneyline quotes are a week older than the page's clock
        body = " ".join(m.value for m in at.markdown)
        self.assertIn("moneyline prices below are stale", body.replace("<b>", "").replace("</b>", ""))
        cells = " ".join(str(d.value.to_dict()) for d in at.dataframe)
        self.assertIn("STALE", cells)
        self.assertIn("never used for a ticket", body)

    def test_a_fresh_moneyline_is_not_marked(self):
        snap = snapshot()
        for g in snap["product_games"]["games"]:
            for side in ("home", "away"):
                if (g.get("moneyline") or {}).get(side):
                    g["moneyline"][side]["quote_captured_at_utc"] = "2026-10-15T17:00:00Z"
        at = run_page("21_Today.py", snap)
        self.assertNotIn("STALE", " ".join(str(d.value.to_dict()) for d in at.dataframe))
        self.assertNotIn("moneyline prices below are stale", " ".join(m.value for m in at.markdown).replace("<b>", "").replace("</b>", ""))

    def test_an_option_card_whose_prices_have_aged_out_is_marked_and_cannot_be_added(self):
        late = FIXTURE_NOW.replace(hour=21, minute=0)                    # four hours later: still before the game, long past the 90/180-minute limits
        with mock.patch.object(ui, "_utcnow", return_value=late):
            state = ss.SnapshotState(**{f: None for f in ss.SnapshotState._fields})._replace(data=snapshot(), source="REMOTE", fetch_status="OK", freshness="CURRENT", schema_version=2)
            with mock.patch.object(rm, "current_mode", return_value=rm.COMMUNITY_CLOUD_MODE), mock.patch.object(ss, "remote_enabled", return_value=True), \
                    mock.patch.object(ss, "current", return_value=state):
                at = AppTest.from_file(os.path.join(PAGES, "26_Player_Props.py"), default_timeout=120)
                choose_log(at)
                at.run()
        body = " ".join(m.value for m in at.markdown)
        self.assertIn("STALE", body)
        self.assertIn("Stale price.", body)
        self.assertFalse(any(b.label.startswith("Add to ") for b in at.button))

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
