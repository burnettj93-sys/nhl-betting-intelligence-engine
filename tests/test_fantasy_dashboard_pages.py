"""
AppTest QA for the two new Fantasy pages (Part 148/149), plus a
regression check that adding the Fantasy section never broke the
existing betting pages (Part 116/148 -- Yahoo failure must not crash
the public dashboard).
"""
from __future__ import annotations

import os

from streamlit.testing.v1 import AppTest
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _page(name: str) -> str:
    return os.path.join(REPO_ROOT, "dashboard", "pages", name)


def _as_admin(at: AppTest) -> AppTest:
    """P0.7 (2026-09-24 hardening block): both Fantasy pages now call
    auth.require_admin() at the top of their script -- every test in
    this class is about the pages' REAL content, so it must authenticate
    as ADMIN first; tests/test_auth.py covers the non-admin-is-blocked
    side of this on its own."""
    at.session_state["_auth_username"] = "owner"
    at.session_state["_auth_role"] = "ADMIN"
    return at


class TestFantasyPagesLoadCleanly(unittest.TestCase):
    def test_fantasy_hq_renders_without_exception(self):
        at = _as_admin(AppTest.from_file(_page("34_Fantasy_HQ.py"), default_timeout=90))
        at.run()
        self.assertEqual(len(at.exception), 0)

    def test_fantasy_hq_shows_demo_mode_when_not_connected(self):
        at = _as_admin(AppTest.from_file(_page("34_Fantasy_HQ.py"), default_timeout=90))
        at.run()
        markdown_text = " ".join(m.value for m in at.markdown)
        self.assertIn("FANTASY DEMO MODE", markdown_text)

    def test_fantasy_hq_never_assigns_a_goalie_to_util(self):
        # End-to-end regression guard for the real bug found this
        # sprint (fixed in fantasy/recommendations/lineup_optimizer.py).
        at = _as_admin(AppTest.from_file(_page("34_Fantasy_HQ.py"), default_timeout=90))
        at.run()
        markdown_text = " ".join(m.value for m in at.markdown)
        self.assertNotEqual(markdown_text, "")  # confirms real content actually rendered, not an empty stop
        for goalie_name in ("Andrei Vasilevskiy", "Connor Hellebuyck", "Igor Shesterkin", "Jake Oettinger"):
            marker = f"**{goalie_name}** — Util"
            self.assertNotIn(marker, markdown_text)

    def test_fantasy_settings_renders_without_exception(self):
        at = _as_admin(AppTest.from_file(_page("35_Fantasy_Settings.py"), default_timeout=90))
        at.run()
        self.assertEqual(len(at.exception), 0)

    def test_fantasy_settings_shows_owner_auth_required_without_credentials(self):
        at = _as_admin(AppTest.from_file(_page("35_Fantasy_Settings.py"), default_timeout=90))
        at.run()
        markdown_text = " ".join(m.value for m in at.markdown)
        self.assertIn("OWNER_AUTH_REQUIRED", markdown_text)


class TestBettingPagesStillWorkAfterFantasyAddition(unittest.TestCase):
    """Part 148: adding the Fantasy section must never break the
    existing betting dashboard."""

    def test_today_page_still_renders(self):
        at = AppTest.from_file(_page("21_Today.py"), default_timeout=90)
        at.run()
        self.assertEqual(len(at.exception), 0)

    def test_team_intelligence_still_renders(self):
        at = AppTest.from_file(_page("31_Team_Intelligence.py"), default_timeout=90)
        at.run()
        self.assertEqual(len(at.exception), 0)

    def test_paper_performance_still_renders(self):
        at = AppTest.from_file(_page("33_Paper_Performance.py"), default_timeout=90)
        at.run()
        self.assertEqual(len(at.exception), 0)


class TestAppNavigationIncludesFantasy(unittest.TestCase):
    """P0.7 (2026-09-24 hardening block): the Fantasy nav section is
    ADMIN-only -- both tests here authenticate as ADMIN to exercise the
    section this class is actually about; tests/test_auth.py covers the
    USER-role-is-blocked side separately."""

    def test_app_py_loads_with_fantasy_section(self):
        at = AppTest.from_file(os.path.join(REPO_ROOT, "dashboard", "app.py"), default_timeout=90)
        at.session_state["_auth_username"] = "owner"
        at.session_state["_auth_role"] = "ADMIN"
        at.run()
        self.assertEqual(len(at.exception), 0)

    def test_switch_page_to_fantasy_hq_works(self):
        at = AppTest.from_file(os.path.join(REPO_ROOT, "dashboard", "app.py"), default_timeout=90)
        at.session_state["_auth_username"] = "owner"
        at.session_state["_auth_role"] = "ADMIN"
        at.run()
        at.switch_page("pages/34_Fantasy_HQ.py").run()
        self.assertEqual(len(at.exception), 0)
        titles = [t.value for t in at.title]
        self.assertEqual(titles[0], "Fantasy HQ")


if __name__ == "__main__":
    unittest.main()
