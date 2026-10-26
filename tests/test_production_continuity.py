"""The learning parlays continue as the production engine after the 14-day window, and the calibration evidence is refreshed by a controlled, auditable process instead of a cliff."""
from __future__ import annotations

import datetime as dt
import inspect
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
    def evidence(self, built, rows=50000, coef=0.4, cell=0.50, through="2026-10-20", features=("gd", "xgd"), ci_high=-0.004):
        return {"version": "eggy-evidence-v1", "built_at_utc": built, "data_through": through,
                "matchup": {"model_version": "eggy-matchup-v1", "half_life_games": 30.0, "shrink_games": 10.0, "coefficients": {"gd": coef, "xgd": 0.97}, "features": list(features),
                            "evaluation": {"delta_vs_home_rate": {"ci_high": ci_high}, "delta_vs_gd_only": {"ci_high": ci_high}}},
                "skater": {"rows": rows, "cells": {"shots>=2": [{"dimension": "toi", "label": "t", "n": 1000, "obs": cell, "pred": 0.5}]}}}

    def sandbox(self, d, live):
        root = Path(d)
        (root / "docs/validation").mkdir(parents=True)
        livef = root / "docs/validation/eggy_evidence.json"
        livef.write_text(json.dumps(live, sort_keys=True))
        patches = [mock.patch.object(er, "REPO", root), mock.patch.object(er, "LIVE", livef), mock.patch.object(er, "CANDIDATES", root / "docs/validation/candidates"),
                   mock.patch.object(er, "VERSIONS", root / "docs/validation/evidence_versions"), mock.patch.object(er, "HISTORY", root / "docs/validation/evidence_history.jsonl"),
                   mock.patch("research.product_models.matchup.load_evidence", side_effect=lambda: {**json.loads(livef.read_text()), "_sha256": "x"})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        er.init_baseline("test baseline", dt.datetime(2026, 10, 10, tzinfo=dt.timezone.utc))
        return root, livef

    NOW = dt.datetime(2026, 10, 30, 12, 0, tzinfo=dt.timezone.utc)                              # 20 days after the live evidence was built

    def test_a_routine_refresh_rolls_forward_by_itself_and_the_old_version_stays_auditable(self):
        with tempfile.TemporaryDirectory() as d:
            root, livef = self.sandbox(d, self.evidence("2026-10-10T00:00:00Z"))
            old_sha = er._sha(livef)
            cand = self.evidence("2026-10-30T00:00:00Z", rows=52000, coef=0.42, cell=0.51, through="2026-10-29")
            out = er.maybe_prepare(self.NOW, build=lambda: cand)
            self.assertEqual(out["status"], "ROLLED_FORWARD")
            self.assertEqual(json.loads(livef.read_text())["built_at_utc"], "2026-10-30T00:00:00Z")           # the live file is the new one
            h = er.verify_history()
            self.assertTrue(h["ok"], h)
            kinds = [e["kind"] for e in er.read_history()]
            self.assertEqual(kinds, ["BASELINE", "AUTO_ROLL_FORWARD"])
            self.assertTrue(any(old_sha[:8] in p.name for p in (root / "docs/validation/evidence_versions").glob("*.json")))      # the old version is kept
            self.assertFalse(list((root / "docs/validation/candidates").glob("PROPOSAL_*.json")))             # nothing waits for the owner
            self.assertEqual(er.maybe_prepare(self.NOW, build=lambda: self.fail("must not build"))["status"], "NOT_DUE")        # the clock restarted

    def test_a_material_change_waits_for_the_owner_and_the_live_evidence_stays_in_force(self):
        with tempfile.TemporaryDirectory() as d:
            root, livef = self.sandbox(d, self.evidence("2026-10-10T00:00:00Z"))
            before = livef.read_text()
            for label, cand in (("a coefficient moved beyond tolerance", self.evidence("2026-10-30T00:00:00Z", coef=0.9, through="2026-10-29")),
                                ("a different feature set", self.evidence("2026-10-30T00:00:00Z", features=("gd", "xgd", "b2b"), through="2026-10-29")),
                                ("less data", self.evidence("2026-10-30T00:00:00Z", rows=40000, through="2026-10-29")),
                                ("fails its own validation", self.evidence("2026-10-30T00:00:00Z", through="2026-10-29", ci_high=0.002))):
                for f in (root / "docs/validation/candidates").glob("PROPOSAL_*.json"):
                    f.unlink()
                out = er.maybe_prepare(self.NOW, build=lambda c=cand: c)
                self.assertEqual(out["status"], "PROPOSAL_PREPARED", label)
                self.assertTrue(out["material"], label)
                self.assertEqual(livef.read_text(), before, label)                                                  # the live file is untouched
            self.assertEqual([e["kind"] for e in er.read_history()], ["BASELINE"])

    def test_the_owner_approves_a_material_change_with_a_text_and_it_is_recorded(self):
        from deploy import promote_evidence as pe
        with tempfile.TemporaryDirectory() as d:
            root, livef = self.sandbox(d, self.evidence("2026-10-10T00:00:00Z"))
            er.maybe_prepare(self.NOW, build=lambda: self.evidence("2026-10-30T00:00:00Z", coef=0.9, through="2026-10-29"))
            with mock.patch.object(pe, "REPO", root):
                self.assertEqual(pe.main([]), 1)                                                                   # no approval text: refused
                self.assertEqual(pe.main(["--approve", "  "]), 1)
                self.assertEqual(json.loads(livef.read_text())["matchup"]["coefficients"]["gd"], 0.4)
                self.assertEqual(pe.main(["--approve", "owner reviewed the coefficient shift against 2025-26"]), 0)
            self.assertEqual(json.loads(livef.read_text())["matchup"]["coefficients"]["gd"], 0.9)
            last = er.read_history()[-1]
            self.assertEqual(last["kind"], "OWNER_APPROVED")
            self.assertIn("coefficient shift", last["approval"])
            self.assertTrue(er.verify_history()["ok"])

    def test_evidence_changed_outside_the_refresh_or_a_tampered_history_is_not_accounted_for(self):
        with tempfile.TemporaryDirectory() as d:
            root, livef = self.sandbox(d, self.evidence("2026-10-10T00:00:00Z"))
            self.assertTrue(er.verify_history()["ok"])
            livef.write_text(json.dumps(self.evidence("2026-10-11T00:00:00Z")))                                      # edited by hand
            self.assertFalse(er.verify_history()["ok"])
            self.assertEqual(er.maybe_prepare(self.NOW, build=lambda: self.fail("no roll-forward on unaccounted evidence"))["status"], "HISTORY_NOT_INTACT")
        with tempfile.TemporaryDirectory() as d:
            root, livef = self.sandbox(d, self.evidence("2026-10-10T00:00:00Z"))
            er.maybe_prepare(self.NOW, build=lambda: self.evidence("2026-10-30T00:00:00Z", through="2026-10-29"))
            lines = (root / "docs/validation/evidence_history.jsonl").read_text().splitlines()
            e = json.loads(lines[0])
            e["approval"] = "rewritten"
            (root / "docs/validation/evidence_history.jsonl").write_text("\n".join([json.dumps(e)] + lines[1:]) + "\n")
            self.assertFalse(er.verify_history()["ok"])

    def test_the_identity_not_the_numbers_is_what_the_policy_pins(self):
        from research.product_models import matchup
        a, b = self.evidence("2026-10-10T00:00:00Z"), self.evidence("2026-10-30T00:00:00Z", rows=99999, coef=0.5, cell=0.55, through="2026-10-29")
        self.assertEqual(matchup.evidence_identity(a), matchup.evidence_identity(b))                                  # numbers change: same policy
        self.assertNotEqual(matchup.evidence_identity(a), matchup.evidence_identity(self.evidence("2026-10-10T00:00:00Z", features=("gd",))))      # a different model: a different policy

    def test_the_engine_stops_only_when_the_evidence_is_unverified_not_on_a_calendar_day(self):
        self.assertEqual(ei.EVIDENCE_MAX_AGE_DAYS, 45)
        self.assertEqual(er.PREPARE_AT_DAYS, 14)                                                                      # a working refresh restarts the clock long before day 45
        self.assertIn("not a calendar cliff", inspect.getsource(ei).split("EVIDENCE_MAX_AGE_DAYS = 45")[1][:400])

    def test_the_comparison_flags_a_candidate_that_moved_too_far_or_rests_on_less_data(self):
        live = self.evidence("2026-10-10T00:00:00Z")
        ok = er.compare(live, self.evidence("2026-10-30T00:00:00Z", rows=51000, coef=0.42, cell=0.51, through="2026-10-29"))
        self.assertTrue(ok["recommended"])
        self.assertFalse(ok["material"])
        bad = er.compare(live, self.evidence("2026-10-30T00:00:00Z", rows=40000, coef=0.9, cell=0.60, through="2026-10-05"))
        self.assertFalse(bad["recommended"])
        self.assertTrue(bad["material"])
        self.assertGreaterEqual(len(bad["problems"]), 3)


if __name__ == "__main__":
    unittest.main()
