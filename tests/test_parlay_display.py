"""The live pages show the CURRENT product (unpriced learning parlays + Eggy's one flagship parlay or PASS), not the retired primary slip / straight-bet experiment: the legacy recording banner,
the primary-slip policy text, the closest-combination block and the straight-bet tiles and scorecard are history, and the parlay scorecard and persisted lessons are shown."""
from __future__ import annotations

import datetime as dt
import json
import unittest
from unittest import mock

from operational import eggy_candidates as ec
from operational import learning_cohort as lc
from operational import learning_postmortem as lpm
from tests.product_fixture import snapshot
from tests.test_learning_cohort import Base, D1, TestPaperPerformancePanel as P, leg
from tests.test_product_pages import run_page
from tests.test_slate_board import _dated_for_the_page, doc

FL = {"parlay_id": "p1", "rank": 1, "n_legs": 3, "legs": [{"label": f"Player {i} — SHOTS ON GOAL 2+ — -250", "key": f"k{i}", "estimate": 0.68, "action": "RECORD"} for i in range(3)],
      "price_status": "UNPRICED_LEARNING_PARLAY", "reference_decimal": 2.0, "combined_probability": None, "probability_basis": "x", "dependence_level": "ASSUMED_INDEPENDENT", "dependence": {"joint_bounds": {"low": 0.3, "high": 0.3, "basis": "x"}, "pairs": []},
      "weakest_leg": {"label": "Player 0 — SHOTS ON GOAL 2+ — -250", "estimate": 0.68, "contradiction": "LOW"}, "construction_quality": "GOOD", "quality_reasons": [], "action": "RECORD", "result_status": "PENDING",
      "legs_hit": 0, "leg_results": None, "killed_by": None, "hit_text": "open"}


def parlay_section(recorded=1, flagship=True):
    sec = P()._section()
    sec.update({"unit": "PARLAY", "recorded_today": recorded, "eggy_flagship": FL if flagship else None, "parlays_today": [FL] * recorded, "cards": {}, "bets_today": [],
                "shortfall_today": {"legs_in_pool": 24, "parlay_legs_eligible": 9, "recorded": recorded, "target": 10, "why": "fewer than 10 defensible parlays exist so far today"}})
    sec["postmortem"] = {**(sec.get("postmortem") or {}),
                         "parlay_scorecard": [{"date": "2026-10-23", "recorded": 8, "target": 10, "hit": 2, "miss": 6, "open": 0, "good": {"n": 3, "hit": 1}, "questionable": {"n": 5, "hit": 1},
                                               "flagship": {"legs": ["Martin Necas — SHOTS ON GOAL 2+ — -210"], "result": "MISS"}}],
                         "parlay_totals": {"recorded": 8, "hit": 2, "miss": 6, "open": 0},
                         "lessons": {"note": "facts only", "cautions": [{"player": "Martin Necas", "team": None, "classification": "TOI_PROJECTION_ERROR", "date": "2026-10-23", "source": "PARLAY_LEG"}],
                                     "by_date": {"2026-10-23": {"tickets": 8, "hit": 2, "miss": 6, "repeated_killer_legs": ["Connor Bedard"], "weakest_legs_that_failed": []}},
                                     "lesson_state_summary": {"ACTIONABLE": 1, "ACCUMULATING": 1, "REPORT_ONLY": 1},
                                     "lesson_state": [{"id": "ROLE_CAUTION", "name": "a role miss becomes a short caution", "state": "ACTIONABLE", "evidence": {"active_cautions": 1}, "consumed_by": "x -> recency_overlay"},
                                                      {"id": "REUSED_LEG_PERFORMANCE", "name": "reused legs", "state": "ACCUMULATING", "evidence": {"n": 28, "z": 0.6}, "threshold": {"n_required": 30, "abs_z_required": 2.0}},
                                                      {"id": "REPEATED_KILLER_LEG", "name": "a repeated killer leg", "state": "REPORT_ONLY"}]}}
    return sec


def text(at):
    return " ".join(x.value for x in at.markdown) + " " + " ".join(x.value for x in at.caption) + " " + " ".join(str(e.label) for e in at.expander) + " " + " ".join(m.label for m in at.metric) + " " + " ".join(d.value.to_string() for d in at.dataframe)


class TestPaperPerformance(unittest.TestCase):
    def page(self, **kw):
        at = run_page("33_Paper_Performance.py", P()._snap(parlay_section(**kw)))
        self.assertFalse(at.exception, at.exception)
        return at

    def test_the_panel_leads_with_the_parlay_product_and_not_the_straight_bet_tiles(self):
        at = self.page()
        t = text(at)
        for want in ("Learning parlays today", "Candidate legs eligible", "Eggy flagship today", "Pricing", "Parlay scorecard", "What yesterday taught today's review", "Martin Necas", "Earlier straight-bet experiment"):
            self.assertIn(want, t, want)
        hist = [e for e in at.expander if str(e.label).startswith("Earlier straight-bet experiment")]
        self.assertEqual(len(hist), 1)
        self.assertIn("Stake each", [m.label for m in hist[0].metric])                      # the retired tiles are kept, inside the history expander
        top = [m.label for m in at.metric if m.label in ("Stake each", "Odds requirement", "Today's target")]
        self.assertEqual(sorted(top), sorted(["Stake each", "Odds requirement", "Today's target"]))          # present once each (inside the expander), not twice
        self.assertEqual(len([m for m in at.metric if m.label == "Stake each"]), 1)

    def test_the_lessons_show_which_are_actionable_which_accumulate_and_which_are_report_only(self):
        t = text(self.page())
        for want in ("Lesson states", "1 actionable now", "1 accumulating evidence", "1 report only", "a role miss becomes a short caution", "reused legs"):
            self.assertIn(want, t, want)

    def test_a_day_with_no_flagship_says_pass_and_zero_parlays(self):
        t = text(self.page(recorded=0, flagship=False))
        self.assertIn("PASS", t)
        self.assertIn("0 parlays recorded of up to 10", t)


class TestBoardAndToday(unittest.TestCase):
    STATUS = {"generated_at_utc": "2026-10-15T17:00:00Z", "eggy_flagship": None}

    def snap(self, flagship=None):
        s = snapshot()
        board = _dated_for_the_page(doc(status=self.STATUS if flagship is None else {**self.STATUS, "eggy_flagship": flagship}))
        s["slate_board"] = board
        s["learning_experiment"] = parlay_section(recorded=0, flagship=False)
        return s

    def test_the_slate_board_shows_the_current_product_and_none_of_the_retired_primary_slip_text(self):
        at = run_page("41_Slate_Board.py", self.snap())
        self.assertFalse(at.exception, at.exception)
        t = text(at)
        self.assertIn("CURRENT PRODUCT: UNPRICED LEARNING PARLAYS", t)
        self.assertIn("PASS", t)
        for gone in ("AUTOMATIC PAPER RECORDING", "Production recording policy", "Closest combination", "primary-slip", "Today's single primary ticket"):
            self.assertNotIn(gone, t, gone)

    def test_the_today_page_shows_the_current_product_not_the_primary_slip_policy(self):
        at = run_page("21_Today.py", self.snap())
        self.assertFalse(at.exception, at.exception)
        t = text(at)
        self.assertIn("CURRENT PRODUCT: UNPRICED LEARNING PARLAYS", t)
        self.assertIn("ONE flagship learning parlay", t)
        for gone in ("Ticket policy (", "AUTOMATIC PAPER RECORDING", "priced +100 to +500, the fewest legs", "proposed, not recorded"):
            self.assertNotIn(gone, t, gone)

    def test_an_old_document_without_the_official_policy_still_renders_the_earlier_view(self):
        s = snapshot()
        s["slate_board"] = _dated_for_the_page(doc())                                      # built without a flagship argument: the legacy output
        at = run_page("41_Slate_Board.py", s)
        self.assertFalse(at.exception, at.exception)
        self.assertNotIn("CURRENT PRODUCT", text(at))


class TestParlayScorecardData(Base):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(lc, "UNIT", "PARLAY")
        p.start()
        self.addCleanup(p.stop)
        self.nhl.execute("INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team, away_team, schedule_observed_at_utc, game_state, source) VALUES (?,?,?,?,?,?,?,?,?)",
                         ("4", "20262027", "2026-10-20", "2026-10-20T23:00:00", "TOR", "MTL", "2026-10-20T00:00:00", "SCHEDULED", "test"))
        self.nhl.commit()

    def test_the_postmortem_carries_the_parlay_scorecard_and_the_snapshot_keeps_it_with_the_lessons(self):
        self.cycle([leg(pid=str(8470001 + i), game=g, p=0.70 - 0.02 * i, price=-130.0) for i, g in enumerate(("1", "2", "4", "1", "2", "4"))])
        d = lpm.build(self.conn, self.nhl, dt.datetime(2026, 10, 21, 12, 0, tzinfo=dt.timezone.utc))
        self.assertTrue(d["parlay_scorecard"])
        row = d["parlay_scorecard"][0]
        self.assertEqual((row["target"], row["recorded"]), (10, len(d["parlays"])))
        self.assertEqual(row["hit"] + row["miss"] + row["open"], row["recorded"])
        with mock.patch.object(lpm, "load", return_value=d), mock.patch.object(lc, "load_status", return_value={"unit": "PARLAY", "authorised": True}):
            snap = lpm.snapshot_section()
        self.assertEqual(snap["postmortem"]["parlay_scorecard"], d["parlay_scorecard"])
        self.assertEqual(snap["postmortem"]["lessons"], d["lessons"])


if __name__ == "__main__":
    unittest.main()
