"""Dashboard regression tests for the Live Odds/Parlay/Post-Mortem
activation sprint (Parts 25, 45, 46, 51, 67, 75). Confirms every page
this sprint touched or added still renders with zero exceptions, and
that the new sections actually contain real content (not just "didn't
crash")."""
from __future__ import annotations

import os
import unittest

from streamlit.testing.v1 import AppTest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _page(name: str) -> str:
    return os.path.join(REPO_ROOT, "dashboard", "pages", name)


class TestTodayPageGameParlaysSection(unittest.TestCase):
    def test_renders_without_exception(self):
        at = AppTest.from_file(_page("21_Today.py"), default_timeout=90)
        at.run()
        self.assertEqual(len(at.exception), 0)

    def test_game_parlays_section_present(self):
        at = AppTest.from_file(_page("21_Today.py"), default_timeout=90)
        at.run()
        markdown_text = " ".join(m.value for m in at.markdown)
        self.assertIn("Game Parlays", markdown_text)

    def test_odds_collection_metrics_present(self):
        at = AppTest.from_file(_page("21_Today.py"), default_timeout=90)
        at.run()
        labels = [m.label for m in at.metric]
        for expected in ("Odds last updated", "Credits remaining", "Verified DK contracts", "Tracked events"):
            self.assertIn(expected, labels)

    def test_never_claims_a_live_draftkings_parlay_price(self):
        at = AppTest.from_file(_page("21_Today.py"), default_timeout=90)
        at.run()
        markdown_text = " ".join(m.value for m in at.markdown)
        caption_text = " ".join(c.value for c in at.caption)
        self.assertNotIn("LIVE DRAFTKINGS PARLAY PRICE", markdown_text + caption_text)


class TestGameDetailPageParlaySection(unittest.TestCase):
    """The new Game Edge Parlay section lives in the DEMO-mode branch
    (session_state["selected_game_id"] starting with "demo-") -- see
    2_Game_Detail.py's own early-branch comment. Matches the existing
    project convention in tests/test_demo_pages_apptest.py."""

    def test_renders_without_exception(self):
        at = AppTest.from_file(_page("2_Game_Detail.py"), default_timeout=90)
        at.session_state["selected_game_id"] = "demo-EDM-COL"
        at.run()
        self.assertEqual(len(at.exception), 0)

    def test_game_edge_parlay_section_present(self):
        at = AppTest.from_file(_page("2_Game_Detail.py"), default_timeout=90)
        at.session_state["selected_game_id"] = "demo-EDM-COL"
        at.run()
        markdown_text = " ".join(m.value for m in at.markdown)
        self.assertIn("Game Edge Parlay", markdown_text)

    def test_real_historical_branch_unaffected_when_no_demo_game_selected(self):
        at = AppTest.from_file(_page("2_Game_Detail.py"), default_timeout=90)
        at.run()
        self.assertEqual(len(at.exception), 0)
        markdown_text = " ".join(m.value for m in at.markdown)
        self.assertNotIn("Game Edge Parlay", markdown_text)


class TestPaperPerformancePageParlayTrack(unittest.TestCase):
    def test_renders_without_exception(self):
        at = AppTest.from_file(_page("33_Paper_Performance.py"), default_timeout=90)
        at.run()
        self.assertEqual(len(at.exception), 0)

    def test_game_parlay_paper_tab_label_present_in_source(self):
        # AppTest doesn't reliably expose tab labels across Streamlit
        # versions -- the real, checkable fact is that the page source
        # actually declares the third track's tab, not just that the
        # page happens not to crash.
        with open(_page("33_Paper_Performance.py")) as f:
            src = f.read()
        self.assertIn("GAME_PARLAY_PAPER", src)
        self.assertIn("tab_parlay", src)

    def test_game_parlay_track_summary_reachable_from_the_page(self):
        from dashboard import paper_performance_view as ppv
        state = ppv.full_dashboard_state()
        self.assertIn("GAME_PARLAY_PAPER", state)
        self.assertIn("summary", state["GAME_PARLAY_PAPER"])


class TestMorningReviewPage(unittest.TestCase):
    def test_renders_without_exception(self):
        at = AppTest.from_file(_page("36_Morning_Review.py"), default_timeout=90)
        at.run()
        self.assertEqual(len(at.exception), 0)

    def test_title_is_morning_review(self):
        at = AppTest.from_file(_page("36_Morning_Review.py"), default_timeout=90)
        at.run()
        titles = [t.value for t in at.title]
        self.assertEqual(titles[0], "Morning Review")

    def test_honest_waiting_state_when_nothing_settled(self):
        at = AppTest.from_file(_page("36_Morning_Review.py"), default_timeout=90)
        at.run()
        combined_text = " ".join(m.value for m in at.markdown) + " ".join(c.value for c in at.caption)
        # No 2026-27 games have been played yet -- this must say so honestly
        # somewhere on the page, not claim real settled results exist.
        self.assertTrue(any(phrase in combined_text for phrase in
                             ("No Game Edge Parlay has settled yet", "WAITING_FOR_SETTLED_DATA",
                              "no settled bets yet", "No settled bets", "No pattern this cycle")))

    def test_never_auto_writes_a_report_file_on_page_load(self):
        with open(_page("36_Morning_Review.py")) as f:
            src = f.read()
        self.assertNotIn("write_report_markdown", src)


class TestAppNavigationIncludesMorningReview(unittest.TestCase):
    def test_app_py_loads_with_morning_review(self):
        at = AppTest.from_file(os.path.join(REPO_ROOT, "dashboard", "app.py"), default_timeout=90)
        at.run()
        self.assertEqual(len(at.exception), 0)

    def test_switch_page_to_morning_review_works(self):
        at = AppTest.from_file(os.path.join(REPO_ROOT, "dashboard", "app.py"), default_timeout=90)
        at.run()
        at.switch_page("pages/36_Morning_Review.py").run()
        self.assertEqual(len(at.exception), 0)
        titles = [t.value for t in at.title]
        self.assertEqual(titles[0], "Morning Review")


class TestBettingPagesStillWorkAfterSprint(unittest.TestCase):
    """Regression guard: this sprint must never break existing betting
    pages that don't relate to it."""

    def test_today_page_still_renders(self):
        at = AppTest.from_file(_page("21_Today.py"), default_timeout=90)
        at.run()
        self.assertEqual(len(at.exception), 0)

    def test_paper_performance_still_renders(self):
        at = AppTest.from_file(_page("33_Paper_Performance.py"), default_timeout=90)
        at.run()
        self.assertEqual(len(at.exception), 0)

    def test_team_intelligence_still_renders(self):
        at = AppTest.from_file(_page("31_Team_Intelligence.py"), default_timeout=90)
        at.run()
        self.assertEqual(len(at.exception), 0)


if __name__ == "__main__":
    unittest.main()
