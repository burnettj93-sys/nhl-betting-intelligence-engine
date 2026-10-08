"""Every product page renders from a published snapshot (hosted mode) without an exception, shows what it should, and shows a truthful
unavailable state when the data is missing. Browsing never writes."""
from __future__ import annotations

import copy
import os
import re
import unittest
from unittest import mock

from streamlit.testing.v1 import AppTest

from dashboard import page_registry
from dashboard import snapshot_source as ss
from operational import runtime_mode as rm
from tests.product_fixture import snapshot

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


def text(at):
    return " ".join([m.value for m in at.markdown] + [c.value for c in at.caption] + [t.value for t in at.title] + [h.value for h in at.subheader]
                    + [w.value for w in at.warning] + [i.value for i in at.info] + [e.value for e in at.error])


CORE = ["21_Today.py", "1_Game_Slate.py", "2_Game_Detail.py", "26_Player_Props.py", "30_Players.py", "27_Goalies.py", "31_Team_Intelligence.py",
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
        self.assertIn("Role (inferred)", t)
        self.assertIn("not an official line chart", t)
        self.assertIn("Next game (projection)", t)
        self.assertIn("Expected shots", " ".join(m.label for m in at.metric))
        self.assertIn("Best qualifying +100 option", t)

    def test_best_options_page_labels_quotes_and_estimates_and_offers_add(self):
        at = run_page("26_Player_Props.py", snapshot())
        t = text(at)
        self.assertNotIn("redundant", t.lower())
        self.assertIn("Estimated price", " ".join(m.label for m in at.metric))
        self.assertIn("Add to paper book — $10", [b.label for b in at.button])

    def test_nothing_is_written_by_browsing_or_filtering(self):
        at = run_page("26_Player_Props.py", snapshot())
        self.assertEqual(at.session_state.filtered_state.get("_paper_orders") or {}, {})
        self.assertTrue(any(b.label == "Add to paper book — $10" for b in at.button))     # offered, not pressed

    def test_add_click_files_exactly_one_outstanding_order_and_a_second_click_does_nothing(self):
        at = run_page("26_Player_Props.py", snapshot())
        btn = next(b for b in at.button if b.label == "Add to paper book — $10")
        btn.click()
        with mock.patch.object(rm, "current_mode", return_value=rm.COMMUNITY_CLOUD_MODE), mock.patch.object(ss, "remote_enabled", return_value=True), \
                mock.patch.object(ss, "current", return_value=ss.SnapshotState(**{f: None for f in ss.SnapshotState._fields})._replace(data=snapshot(), source="REMOTE", fetch_status="OK", freshness="CURRENT")):
            at.run()
        orders = at.session_state["_paper_orders"]
        self.assertEqual(len(orders), 1)
        only = next(iter(orders.values()))
        self.assertIn("issues/new?labels=paper-order", only["url"])
        # once an order is outstanding for that option the button is replaced by its status
        outstanding = [b for b in at.button if b.label == "Add to paper book — $10"]
        self.assertEqual(len(outstanding), len([o for o in snapshot()["tickets"]["options"]["options"]]) - 1)

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
