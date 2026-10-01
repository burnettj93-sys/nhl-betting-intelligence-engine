"""
Same-Day Demo Experience sprint (2026-08-31), Part 62/64: AppTest QA as
real regression tests, not just a one-off manual check. Loads each of
the 8 pages the sprint requires be demoable and asserts zero exceptions.
Kept independent of any live network/odds credits -- pure Streamlit
AppTest against the real demo/simulated data path.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
        """Production Readiness Audit (2026-09-24) found that this page's
        old "waiting" gate checked ledger file EXISTENCE, not row count
        -- once operational/prospective_observations.db existed (even
        with zero rows), the page fell through to a populated-looking
        "ENGINE STATUS: WATCH". Fixed at the source
        (operational.daily_model_review.run_daily_review() now returns
        an explicit NO_DATA engine_status whenever there are zero real
        settled predictions, regardless of whether the ledger file
        exists) -- see docs/MODEL_REVIEW_ZERO_DATA_FIX.md. This test
        points at a guaranteed-nonexistent path (rather than the real,
        permanent, accumulating repo ledger) so it tests the "no data"
        case deterministically regardless of what the real ledger
        currently contains."""
        with tempfile.TemporaryDirectory() as tmp:
            nonexistent_path = Path(tmp) / "does_not_exist.db"
            with mock.patch("operational.prospective_ledger.DB_PATH", nonexistent_path):
                at = AppTest.from_file(_page("32_Model_Learning.py"), default_timeout=60)
                at.run()
        self.assertEqual(len(at.exception), 0)
        markdown_text = " ".join(m.value for m in at.markdown)
        caption_text = " ".join(c.value for c in at.caption)
        self.assertIn("ENGINE STATUS: NO_DATA", markdown_text)
        self.assertIn("no real settled predictions exist yet", caption_text)

    def test_paper_performance(self):
        self._assert_clean(AppTest.from_file(_page("33_Paper_Performance.py"), default_timeout=90))

    def test_today_shows_historical_elo_comparison_section(self):
        """Real bug fix (2026-10-01): this section (real DK prices vs. a frozen,
        always-stale research Elo snapshot) was previously a top-level, always-
        expanded '## Live Model Edges' / '## Model Edges — ODDS STALE' heading --
        read repeatedly as "the engine is broken" despite its own disclaimer, since
        it's permanently stale by construction. Now collapsed behind the same
        explicit expander treatment the Demo/Model Showcase sections get, with one
        static label regardless of freshness state."""
        import datetime as dt
        from operational import cloud_snapshot_schema as schema
        real = schema.recommendation_freshness

        def aged(row, now=None, snapshot_generated_at=None):
            ts = schema.parse_utc(row.get("captured_at_utc") or row.get("odds_captured_at_utc") or row.get("created_at_utc"))
            return real(row, now=ts + dt.timedelta(minutes=30) if ts else now, snapshot_generated_at=None)

        with mock.patch.object(schema, "recommendation_freshness", aged):
            at = AppTest.from_file(_page("21_Today.py"), default_timeout=90)
            at.run()
        self.assertEqual(len(at.exception), 0)
        expander_labels = [e.label for e in at.expander]
        self.assertTrue(any("Historical Elo research comparison" in label for label in expander_labels))

    def test_demo_slate_section_is_labeled_simulated_not_todays_slate(self):
        """Real Morning Production Pull sprint (2026-09-29): the section powering Top Conviction/Combos
        used to be headed 'Today's Slate', which -- sitting directly under the REAL Live Model Edges /
        Recorded Recommendations sections -- read as a continuation of real content. It must now say
        SIMULATED/DEMO explicitly, and the old bare heading must be gone."""
        at = AppTest.from_file(_page("21_Today.py"), default_timeout=60)
        at.run()
        self.assertEqual(len(at.exception), 0)
        markdown_text = " ".join(m.value for m in at.markdown)
        self.assertNotIn("1 · Today's Slate", markdown_text)
        self.assertIn("Demo Slate", markdown_text)
        self.assertIn("SIMULATED", markdown_text)
        caption_text = " ".join(c.value for c in at.caption)
        self.assertIn("SIMULATED — DEMO ONLY (not today's real schedule)", caption_text)

    def test_game_slate_explicitly_labels_itself_historical_not_today(self):
        """Real Morning Production Pull sprint (2026-09-29): the 'Games' sidebar entry (1_Game_Slate.py)
        browses only the frozen historical corpus and used to silently default to the corpus's last date
        (2026-04-16) with no pointer to where today's real games live -- a user could reasonably mistake
        that for today's schedule. It must now say so explicitly and point to Today. The underlying
        historical browsing functionality itself must remain fully intact (not removed)."""
        at = AppTest.from_file(_page("1_Game_Slate.py"), default_timeout=60)
        at.run()
        self.assertEqual(len(at.exception), 0)
        info_text = " ".join(i.value for i in at.info)
        self.assertIn("frozen historical research corpus", info_text)
        self.assertIn("Today", info_text)
        caption_text = " ".join(c.value for c in at.caption)
        self.assertIn("is not part of this historical corpus", caption_text)
        # Historical browsing itself must still work -- games for the selected historical date still render.
        markdown_text = " ".join(m.value for m in at.markdown)
        self.assertIn("game(s) on", markdown_text)

    def test_game_detail_never_silently_substitutes_a_real_game_id(self):
        """Real Morning Production Pull sprint (2026-09-29): requesting Game Detail for a real, current
        game_id (never in the frozen historical corpus) used to silently fall back to the corpus's most
        recent historical date with zero indication -- exactly the 'silently routes to an April historical
        game' failure mode the sprint's block warned against.

        Production Gap Closure sprint (2026-09-30): Game Detail now looks a non-demo game_id up against
        nhl.db's own real, current `games` table FIRST (dashboard/real_game_detail_view.py) -- this id
        (2026020002) is a REAL game genuinely present in this Mac's production nhl.db (TOR @ MTL, FINAL),
        so it now resolves to ITSELF directly, never reaching -- let alone silently substituting from --
        the historical corpus at all. See test_real_game_detail_view.py for focused unit coverage of that
        lookup, and test_a_genuinely_unknown_game_id_falls_back_to_labeled_historical_browsing below for
        the "absent from both real sources" case this test used to (incorrectly) exercise."""
        at = AppTest.from_file(_page("2_Game_Detail.py"), default_timeout=60)
        at.session_state["selected_game_id"] = "2026020002"  # a real game in this Mac's own nhl.db
        at.run()
        self.assertEqual(len(at.exception), 0)
        self.assertEqual(len(at.warning), 0, "a real, found game_id must never show a substitution warning")
        all_text = " ".join(m.value for m in at.markdown) + " " + " ".join(s.value for s in at.subheader)
        self.assertIn("LIVE — REAL GAME", all_text)
        self.assertIn("TOR", all_text)
        self.assertIn("MTL", all_text)

    def test_a_genuinely_unknown_game_id_falls_back_to_labeled_historical_browsing(self):
        """A game_id absent from BOTH nhl.db's real schedule AND the frozen historical corpus must still
        fall back to historical browsing with an honest, non-substituting explanation -- never a bare
        crash, and never presented as if it answered the request."""
        at = AppTest.from_file(_page("2_Game_Detail.py"), default_timeout=60)
        at.session_state["selected_game_id"] = "nonexistent-game-id-999999"
        at.run()
        self.assertEqual(len(at.exception), 0)
        warning_text = " ".join(w.value for w in at.warning)
        self.assertIn("not part of this frozen historical research corpus", warning_text)
        self.assertIn("not a substitution for the game you requested", warning_text)


if __name__ == "__main__":
    unittest.main()
