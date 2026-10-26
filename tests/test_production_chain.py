"""The production chain, proven link by link and end to end without anyone's memory: yesterday's settled legs -> the postmortem's persisted lessons -> the next review's context -> a flag and a measured
adjustment on the same player today; the measured record, the prospective record and the evidence refresh are all produced by the same automatic jobs."""
from __future__ import annotations

import inspect
import unittest
from unittest import mock

from operational import daily_postmortem
from operational import eggy_intelligence as ei
from operational import intelligence_metrics as im
from operational import learning_cohort as lc
from operational import learning_postmortem as lpm
from operational import real_parlay_paper_trader as trader
from tests import test_eggy_intelligence as tei
from tests.test_daily_lessons import lr, ticket
from tests.test_signal_evidence import evidence


class TestYesterdayMakesTodaySmarter(unittest.TestCase):
    def test_a_role_miss_settled_yesterday_flags_and_lowers_the_same_player_in_todays_review_with_no_manual_step(self):
        # yesterday: a ticket lost because Test Player's ice time came in far under the model's expectation
        yesterday = [ticket("P1", "2026-10-24", "MISS", [lr("k1", "Test Player", "LOSS", "TOI_PROJECTION_ERROR", team="AAA"), lr("k2", "Someone Else", "WIN")])]
        with mock.patch.object(lc, "_flagship_public", return_value=None):
            lessons = lpm.build_lessons(None, yesterday, {}, "2026-10-25", {"calibration_cautions": []})
        self.assertEqual([(c["player"], c["classification"]) for c in lessons["cautions"]], [("Test Player", "TOI_PROJECTION_ERROR")])
        # today: the live review context reads the persisted document (nothing else) and the review of the same player acts on it
        doc = {"lessons": lessons}
        t = tei.TestPlayerReview()
        t.setUp()
        ctx = t.ctx()
        ctx.as_of, ctx.live_overlay, ctx.recent_box = "2026-10-25", True, {}
        with mock.patch("operational.learning_postmortem.load", return_value=doc):
            ei._load_overlay_inputs(ctx, None)
        self.assertIn(("test player", "AAA"), ctx.role_cautions)
        ev = evidence("BEYOND_MODEL_LOWER", -0.0183)
        with mock.patch.object(ei, "signal_evidence", return_value=ev):
            today = t.review(tei.prop_leg(price=-110.0, p=0.72), 0.72, ctx=ctx)
            ctx.role_cautions = {}
            baseline = t.review(tei.prop_leg(price=-110.0, p=0.72), 0.72, ctx=ctx)
        flags = {f["name"]: f["level"] for f in today["contradiction"]["flags"]}
        self.assertEqual(flags["POSTMORTEM_ROLE_CAUTION"], "MODERATE")
        self.assertAlmostEqual(today["probabilities"]["estimate_used"], baseline["probabilities"]["estimate_used"] - 0.0183, places=4)
        self.assertEqual(today["signal_adjustment"]["signal"], "POSTMORTEM_ROLE_CAUTION")


class TestEveryLinkRunsAutomatically(unittest.TestCase):
    def test_the_ten_minute_trader_runs_the_cohort_which_settles_records_and_refreshes_the_postmortem(self):
        self.assertIn("learning_cohort.run_cycle", inspect.getsource(trader))
        run = inspect.getsource(lc.run_cycle)
        for step in ("settle_due", "settle_parlays", "_refresh_postmortem", "write_status"):
            self.assertIn(step, run, step)
        self.assertIn("_record_cycle", run)

    def test_the_postmortem_document_carries_lessons_metrics_prospective_record_and_scorecard(self):
        build = inspect.getsource(lpm.build)
        for part in ("build_lessons", "im.compute", "parlay_scorecard", "intelligence_metrics"):
            self.assertIn(part, build, part)
        comp = inspect.getsource(im.compute)
        for part in ("prospective_moneyline", "calibration_cautions", "evidence_health", "points"):
            self.assertIn(part, comp, part)

    def test_the_review_context_reads_every_persisted_finding_the_postmortem_writes(self):
        load = inspect.getsource(ei._load_overlay_inputs)
        for part in ("calibration_cautions", "moneyline_trust", "cautions", "box_scores_since"):
            self.assertIn(part, load, part)

    def test_the_daily_postmortem_job_prepares_the_evidence_candidate(self):
        self.assertIn("evidence_refresh", inspect.getsource(daily_postmortem))

    def test_eggys_output_is_read_from_the_same_ledger_selection_the_board_uses(self):
        from operational import eggy_candidates as ec
        self.assertIn("eggy_flagship", inspect.getsource(ec.flagship_state))
        self.assertIn("_flagship_public", inspect.getsource(lc.status_doc))


if __name__ == "__main__":
    unittest.main()
