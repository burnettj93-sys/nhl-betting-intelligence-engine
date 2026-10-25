"""The learning parlays continue as the production engine after the 14-day window, and the calibration evidence is refreshed by a controlled, auditable process instead of a cliff."""
from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from operational import eggy_intelligence as ei
from operational import evidence_refresh as er
from operational import learning_cohort as lc
from tests.test_learning_cohort import Base, leg

NOV10 = dt.datetime(2026, 11, 10, 20, 0, tzinfo=dt.timezone.utc)                  # 15:00 ET, a week after the window ended


class TestProductionAfterTheWindow(Base):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(lc, "UNIT", "PARLAY")
        p.start()
        self.addCleanup(p.stop)
        for g, hh in (("11", "23"), ("12", "23"), ("13", "23")):
            self.nhl.execute("INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team, away_team, schedule_observed_at_utc, game_state, source) VALUES (?,?,?,?,?,?,?,?,?)",
                             (g, "20262027", "2026-11-10", f"2026-11-10T{hh}:00:00", "TOR", "MTL", "2026-11-10T00:00:00", "SCHEDULED", "test"))
        self.nhl.commit()

    def legs(self):
        return [leg(pid=str(8470001 + i), game=g, p=0.70 - 0.02 * i, price=-130.0, game_start_utc="2026-11-10T23:00:00", provider_start_utc="2026-11-10T23:00:00Z")
                for i, g in enumerate(("11", "12", "13", "11", "12", "13"))]

    def test_after_the_window_the_engine_still_records_parlays_and_the_ledger_keeps_one_history(self):
        before = self.conn.execute("SELECT COUNT(*) FROM parlays").fetchone()[0]
        out = self.cycle(self.legs(), now=NOV10)
        self.assertEqual(out["recording"]["unit"], "PARLAY")
        self.assertNotEqual(out["recording"].get("status"), "NO_NEW_BETS")
        rows = [dict(r) for r in self.conn.execute("SELECT * FROM parlays")]
        self.assertGreater(len(rows), before)
        self.assertEqual({r["cohort"] for r in rows}, {lc.COHORT})                               # the same ledger and cohort: no duplicate system, no reset
        self.assertTrue(all(r["experiment_day"] == 22 for r in rows))                            # the window's day numbering simply continues
        self.assertEqual(lc.status_doc(NOV10, conn=self.conn)["window"], "PRODUCTION")

    def test_settlement_and_the_postmortem_refresh_run_in_the_same_cycle_after_the_window(self):
        import inspect
        run = inspect.getsource(lc.run_cycle)
        self.assertIn("settle_parlays", run)
        self.assertIn("_refresh_postmortem", run)
        self.assertNotIn("ENDED", inspect.getsource(lc._record_parlays))


class TestTheEvidenceRefresh(unittest.TestCase):
    def evidence(self, built, rows=50000, coef=0.4, cell=0.50, through="2026-10-20"):
        return {"built_at_utc": built, "data_through": through, "matchup": {"coefficients": {"gd": coef, "xgd": 0.97}, "features": ["gd", "xgd"]},
                "skater": {"rows": rows, "cells": {"shots>=2": [{"dimension": "toi", "label": "t", "n": 1000, "obs": cell, "pred": 0.5}]}}}

    def test_a_candidate_is_prepared_only_when_the_live_evidence_is_old_enough_and_never_touches_the_live_file(self):
        live = self.evidence("2026-10-10T00:00:00Z")
        now = dt.datetime(2026, 10, 30, 12, 0, tzinfo=dt.timezone.utc)                           # 20 days old
        with tempfile.TemporaryDirectory() as d, mock.patch.object(er, "CANDIDATES", Path(d)), mock.patch("research.product_models.matchup.load_evidence", return_value=live), \
                mock.patch.object(er, "REPO", Path(d).parent):
            cand = self.evidence("2026-10-30T00:00:00Z", rows=52000, through="2026-10-29")
            out = er.maybe_prepare(now, build=lambda: cand)
            self.assertEqual(out["status"], "PROPOSAL_PREPARED")
            self.assertTrue(out["recommended"])
            prop = json.loads(next(Path(d).glob("PROPOSAL_*.json")).read_text())
            self.assertIn("deploy.promote_evidence", prop["to_approve"][0])
            self.assertEqual(len(prop["candidate_sha256"]), 64)
            again = er.maybe_prepare(now, build=lambda: cand)
            self.assertEqual(again["status"], "PROPOSAL_ALREADY_PREPARED")                        # once, until the live evidence changes
        young = self.evidence("2026-10-28T00:00:00Z")
        with tempfile.TemporaryDirectory() as d, mock.patch.object(er, "CANDIDATES", Path(d)), mock.patch("research.product_models.matchup.load_evidence", return_value=young):
            self.assertEqual(er.maybe_prepare(now, build=lambda: self.fail("must not build"))["status"], "NOT_DUE")

    def test_the_comparison_flags_a_candidate_that_moved_too_far_or_rests_on_less_data(self):
        live = self.evidence("2026-10-10T00:00:00Z")
        ok = er.compare(live, self.evidence("2026-10-30T00:00:00Z", rows=51000, coef=0.42, cell=0.51, through="2026-10-29"))
        self.assertTrue(ok["recommended"])
        bad = er.compare(live, self.evidence("2026-10-30T00:00:00Z", rows=40000, coef=0.9, cell=0.60, through="2026-10-05"))
        self.assertFalse(bad["recommended"])
        self.assertGreaterEqual(len(bad["problems"]), 3)

    def test_the_clock_gives_a_month_to_approve_a_prepared_candidate(self):
        self.assertEqual(ei.EVIDENCE_MAX_AGE_DAYS - er.PREPARE_AT_DAYS, 31)

    def test_promotion_refuses_a_candidate_that_does_not_match_its_proposal_or_is_not_recommended(self):
        from deploy import promote_evidence as pe
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "docs/validation/candidates").mkdir(parents=True)
            cand = root / "docs/validation/candidates/eggy_evidence_x.json"
            cand.write_text(json.dumps(self.evidence("2026-10-30T00:00:00Z")))
            live = root / "docs/validation/eggy_evidence.json"
            live.write_text("LIVE")
            prop = root / "docs/validation/candidates/PROPOSAL_x.json"
            import hashlib
            good = {"candidate_file": "docs/validation/candidates/eggy_evidence_x.json", "candidate_sha256": hashlib.sha256(cand.read_bytes()).hexdigest(), "recommended": False, "comparison": {"problems": ["p"]}}
            prop.write_text(json.dumps(good))
            with mock.patch.object(er, "CANDIDATES", prop.parent), mock.patch.object(er, "LIVE", live), mock.patch.object(pe, "REPO", root):
                self.assertEqual(pe.main([]), 1)                                              # not recommended: refused
                self.assertEqual(live.read_text(), "LIVE")
                prop.write_text(json.dumps({**good, "candidate_sha256": "0" * 64, "recommended": True}))
                self.assertEqual(pe.main([]), 1)                                              # checksum mismatch: refused
                prop.write_text(json.dumps({**good, "recommended": True}))
                self.assertEqual(pe.main([]), 0)
                self.assertEqual(live.read_text(), cand.read_text())


if __name__ == "__main__":
    unittest.main()
