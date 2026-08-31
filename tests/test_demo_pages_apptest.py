"""
Same-Day Demo Experience sprint (2026-08-31), Part 62/64: AppTest QA as
real regression tests, not just a one-off manual check. Loads each of
the 8 pages the sprint requires be demoable and asserts zero exceptions.
Kept independent of any live network/odds credits -- pure Streamlit
AppTest against the real demo/simulated data path.
"""
from __future__ import annotations

import os
import unittest

from streamlit.testing.v1 import AppTest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _page(name: str) -> str:
    return os.path.join(REPO_ROOT, "dashboard", "pages", name)


class TestDemoPagesLoadWithoutExceptions(unittest.TestCase):
    def _assert_clean(self, at: AppTest) -> None:
        at.run()
        self.assertEqual(len(at.exception), 0,
                          f"page raised: {[str(e) for e in at.exception]}")

    def test_today(self):
        self._assert_clean(AppTest.from_file(_page("21_Today.py"), default_timeout=60))

    def test_team_intelligence_default(self):
        self._assert_clean(AppTest.from_file(_page("31_Team_Intelligence.py"), default_timeout=60))

    def test_team_intelligence_specific_team(self):
        at = AppTest.from_file(_page("31_Team_Intelligence.py"), default_timeout=60)
        at.session_state["selected_team"] = "EDM"
        self._assert_clean(at)

    def test_game_detail_demo_game(self):
        at = AppTest.from_file(_page("2_Game_Detail.py"), default_timeout=60)
        at.session_state["selected_game_id"] = "demo-EDM-COL"
        self._assert_clean(at)

    def test_game_detail_no_selection(self):
        self._assert_clean(AppTest.from_file(_page("2_Game_Detail.py"), default_timeout=60))

    def test_player_intelligence(self):
        at = AppTest.from_file(_page("25_Player_Intelligence.py"), default_timeout=60)
        at.session_state["selected_player_id"] = "8478402"
        self._assert_clean(at)

    def test_player_props(self):
        self._assert_clean(AppTest.from_file(_page("26_Player_Props.py"), default_timeout=60))

    def test_goalies(self):
        self._assert_clean(AppTest.from_file(_page("27_Goalies.py"), default_timeout=60))

    def test_combinations(self):
        self._assert_clean(AppTest.from_file(_page("28_Combinations.py"), default_timeout=60))

    def test_model_learning_waiting_state(self):
        at = AppTest.from_file(_page("32_Model_Learning.py"), default_timeout=60)
        at.run()
        self.assertEqual(len(at.exception), 0)
        markdown_text = " ".join(m.value for m in at.markdown)
        self.assertIn("WAITING FOR 2026-27 RESULTS", markdown_text)


if __name__ == "__main__":
    unittest.main()
