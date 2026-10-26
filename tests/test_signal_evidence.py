"""Signals are held to what the history says they are worth (deploy/validate_player_signals.py -> docs/validation/player_signals.json): a flag whose games fell short of the MODEL'S OWN probability keeps its weight and
a measured downward adjustment; a flag the model already accounts for is shown and no longer counted; the shots estimate is the model's validated probability where it beat the anchored record out of time."""
from __future__ import annotations

import json
import unittest
from unittest import mock

from operational import eggy_intelligence as ei
from operational import learning_cohort as lc
from tests import test_eggy_intelligence as tei
from tests.test_recency_overlay import box, validated


def evidence(verdict, adj, flags=ei.EVIDENCE_FLAGS, markets=("shots>=2", "points>=1")):
    return {f: {m: {"verdict": verdict, "z": -4.0 if verdict.startswith("BEYOND") else 0.4, "mean_residual_vs_model": -0.03 if verdict.startswith("BEYOND") else 0.004, "live_adjustment": adj, "n": 1000} for m in markets} for f in flags}


class TestEvidenceDecidesWhatCounts(unittest.TestCase):
    def setUp(self):
        self.t = tei.TestPlayerReview()
        self.t.setUp()

    def review(self, ev, market="shots>=2", fam="PLAYER_SOG_ALTERNATE", k=2, p=0.72, toi=18.5, **ctxkw):
        c = self.t.ctx(toi=14.0)                                                  # he has played 14 lately; the model expects 18.5: the ROLE_ASSUMPTION condition
        c.live_overlay = True
        c.recent_box = {}
        c.role_cautions = {}
        for key, v in ctxkw.items():
            setattr(c, key, v)
        with mock.patch.object(ei, "signal_evidence", return_value=ev):
            return self.t.review(tei.prop_leg(fam=fam, k=k, price=-110.0, p=p), p, market=market, ctx=c, toi=toi)

    def test_a_validated_flag_counts_and_lowers_the_estimate_by_the_measured_amount_only(self):
        base = self.review({}, p=0.72)                                              # no evidence file: the flag is left exactly as it was
        c = self.review(evidence("BEYOND_MODEL_LOWER", -0.0164))
        self.assertEqual({f["name"]: f["level"] for f in c["contradiction"]["flags"]}["ROLE_ASSUMPTION"], "MODERATE")
        self.assertAlmostEqual(c["probabilities"]["estimate_used"], base["probabilities"]["estimate_used"] - 0.0164, places=4)
        self.assertEqual(c["signal_adjustment"]["signal"], "ROLE_ASSUMPTION")
        self.assertEqual(c["signal_adjustment"]["market"], "shots>=2")
        self.assertIn("validated", " ".join(f["text"] for f in c["contradiction"]["flags"]))

    def test_a_flag_the_model_already_accounts_for_is_shown_but_no_longer_counts_and_moves_nothing(self):
        base = self.review({}, p=0.72)
        c = self.review(evidence("ALREADY_IN_MODEL", None))
        flag = [f for f in c["contradiction"]["flags"] if f["name"] == "ROLE_ASSUMPTION"][0]
        self.assertEqual(flag["level"], "LOW")
        self.assertIn("no demonstrated information beyond the model", flag["text"])
        self.assertEqual(c["probabilities"]["estimate_used"], base["probabilities"]["estimate_used"])
        self.assertNotIn("signal_adjustment", c)
        self.assertLess(["LOW", "MODERATE", "HIGH", "SEVERE"].index(c["contradiction"]["level"]), ["LOW", "MODERATE", "HIGH", "SEVERE"].index(base["contradiction"]["level"]) + 1)

    def test_only_the_single_most_negative_signal_is_applied_never_a_sum(self):
        flags = [{"name": "ROLE_ASSUMPTION", "level": "MODERATE", "text": "a"}, {"name": "POSTMORTEM_ROLE_CAUTION", "level": "MODERATE", "text": "b"}]
        ev = {"ROLE_ASSUMPTION": {"shots>=2": {"verdict": "BEYOND_MODEL_LOWER", "z": -3.6, "mean_residual_vs_model": -0.03, "live_adjustment": -0.0164}},
              "POSTMORTEM_ROLE_CAUTION": {"shots>=2": {"verdict": "BEYOND_MODEL_LOWER", "z": -3.7, "mean_residual_vs_model": -0.03, "live_adjustment": -0.0183}}}
        with mock.patch.object(ei, "signal_evidence", return_value=ev):
            out, delta, note = ei.apply_signal_evidence(flags, "shots", 2)
        self.assertEqual(delta, -0.0183)
        self.assertEqual(note["signal"], "POSTMORTEM_ROLE_CAUTION")
        self.assertTrue(all(f["level"] == "MODERATE" for f in out))

    def test_a_market_the_history_never_tested_is_left_alone(self):
        flags = [{"name": "ROLE_ASSUMPTION", "level": "MODERATE", "text": "a"}]
        with mock.patch.object(ei, "signal_evidence", return_value=evidence("ALREADY_IN_MODEL", None)):
            out, delta, note = ei.apply_signal_evidence(flags, "goals", 1)
        self.assertEqual((out[0]["level"], delta, note), ("MODERATE", 0.0, None))

    def test_an_adjustment_never_raises_an_estimate(self):
        ev = evidence("BEYOND_MODEL_LOWER", -0.02)
        self.assertLessEqual(self.review(ev)["probabilities"]["estimate_used"], self.review({})["probabilities"]["estimate_used"])


class TestTheRealValidationFile(unittest.TestCase):
    D = json.loads(ei.SIGNAL_VALIDATION_PATH.read_text())

    def test_every_flag_the_review_weighs_has_evidence_and_every_adjustment_is_downward_and_small(self):
        fe = self.D["flag_evidence"]
        for f in ("ROLE_ASSUMPTION", "TOI_DROP", "PP_ROLE_LOST", "RECENT_FORM_BELOW_MODEL", "POSTMORTEM_ROLE_CAUTION", "POWER_PLAY_ASSUMPTION", "MODEL_ABOVE_RECORD"):
            self.assertIn(f, fe, f)
        for f, ms in fe.items():
            for m, v in ms.items():
                if v["live_adjustment"] is not None:
                    self.assertLessEqual(v["live_adjustment"], 0.0)
                    self.assertGreaterEqual(v["live_adjustment"], -0.06)
                    self.assertTrue(v["verdict"].startswith("BEYOND_MODEL_LOWER"))
                    self.assertGreaterEqual(v["n"], 300)

    def test_what_the_history_found_about_recent_shot_form_and_the_anchored_record(self):
        fe = self.D["flag_evidence"]
        self.assertEqual(fe["RECENT_FORM_BELOW_MODEL"]["shots>=2"]["verdict"], "ALREADY_IN_MODEL")           # a cold last five adds nothing the model has not already priced
        self.assertTrue(fe["ROLE_ASSUMPTION"]["shots>=2"]["verdict"].startswith("BEYOND"))                  # but a model that expects 2+ more minutes than he has been playing is too high
        self.assertEqual(fe["MODEL_ABOVE_RECORD"]["shots>=2"]["verdict"], "ALREADY_IN_MODEL")

    def test_the_models_own_probability_beat_the_anchored_record_estimate_for_shots_2_and_3_out_of_time(self):
        for m in ("shots>=2", "shots>=3"):
            e = self.D["estimate_formulas"][m]
            self.assertGreater(e["ci95"][0], 0.0)                                                          # anchored minus model log loss: the whole interval is on the model's side
            self.assertGreater(e["rows"], 40000)
        self.assertEqual(ei.estimate_policy(), {"shots>=2": True, "shots>=3": True})


class TestTheShotsEstimateBasis(unittest.TestCase):
    def leg(self, k=2, model=0.66, record=0.50, base=0.52):
        return tei.prop_leg(k=k, price=-150.0, p=base).__class__(**{**tei.prop_leg(k=k, price=-150.0, p=base).__dict__, "model_probability": model, "empirical_probability": record, "empirical_games": 30})

    def test_the_model_probability_is_the_estimate_where_it_beat_the_record_and_the_record_is_no_ceiling(self):
        with mock.patch.object(lc.ei, "estimate_policy", return_value={"shots>=2": True, "shots>=3": True}):
            p, basis = lc.probability_basis(self.leg(model=0.66, record=0.50))
            self.assertEqual((round(p, 4), basis), (0.66, "VALIDATED_MODEL_PROBABILITY"))
            p3, _ = lc.probability_basis(self.leg(k=3, model=0.80, record=0.5))
            self.assertEqual(p3, 0.70)                                                                    # still held to 70% for 3+ shots

    def test_without_the_evidence_the_earlier_anchored_estimate_applies_unchanged(self):
        with mock.patch.object(lc.ei, "estimate_policy", return_value={}):
            p, basis = lc.probability_basis(self.leg(model=0.66, record=0.70, base=0.62))
            self.assertEqual((round(p, 4), basis), (0.62, "ANCHORED_RECORD_ESTIMATE"))


if __name__ == "__main__":
    unittest.main()
