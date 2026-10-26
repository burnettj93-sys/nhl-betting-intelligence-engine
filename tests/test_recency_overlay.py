"""The recency / role-risk overlay (operational/eggy_intelligence.py recency_overlay): the latest already-stored games against the model's older assumption. It only adds contradiction flags (so confidence
and the RECORD / LOWER_PRIORITY / PASS decision can move); it never changes a probability, never raises one, and is off outside the live cohort."""
from __future__ import annotations

import inspect
import unittest
from unittest import mock

from operational import eggy_intelligence as ei
from operational import learning_cohort as lc
from operational import learning_parlays as lp
from tests import test_eggy_intelligence as tei
from tests.test_eggy_intelligence import prop_leg

BASE = {"model": 0.72}


def box(date, toi, shots=3):
    return {"date": date, "toi": toi, "toi_pp": None, "shots": shots, "goals": 0, "assists": 0, "source": "NHL API box score"}


def validated(adj=0.0, verdict="BEYOND_MODEL_LOWER"):
    """Evidence in which every flag carries information beyond the model, for the markets the tests use (a zero adjustment unless one is given)."""
    return {f: {m: {"verdict": verdict, "z": -4.0, "mean_residual_vs_model": -0.03, "live_adjustment": adj, "n": 1000} for m in ("shots>=2", "shots>=3", "points>=1", "points>=2")} for f in ei.EVIDENCE_FLAGS}


class Overlay(unittest.TestCase):
    pid = "9"

    def setUp(self):
        p = mock.patch.object(ei, "signal_evidence", return_value=validated())
        p.start()
        self.addCleanup(p.stop)
    ctx = tei.TestPlayerReview.ctx                       # the review fixtures are borrowed, not inherited, so the earlier review tests are not run again
    review = tei.TestPlayerReview.review

    def ov_ctx(self, box_games=(), cautions=None, live=True, **player_kw):
        c = self.ctx(**player_kw)
        c.live_overlay = live
        c.recent_box = {self.pid: list(box_games)} if box_games else {}
        c.role_cautions = cautions or {}
        return c

    def names(self, card):
        return {f["name"]: f["level"] for f in card["contradiction"]["flags"]}

    def run_review(self, ctx, market="shots>=2", fam="PLAYER_SOG_ALTERNATE", k=2, p=0.72, **proj):
        return self.review(prop_leg(fam=fam, k=k, price=-110.0, p=p), p, market=market, ctx=ctx, **proj)


class TestWhatItFlags(Overlay):
    def test_a_big_ice_time_drop_in_the_newest_games_is_flagged_and_counted_when_the_history_says_it_carries_information(self):
        ctx = self.ov_ctx([box("2026-10-21", 12.0), box("2026-10-20", 12.5)])                       # the model expects 18.5; the stored latest games say ~12
        c = self.run_review(ctx, toi=18.5)
        self.assertEqual(self.names(c)["TOI_DROP"], "MODERATE")                                      # one level: the validation shows no stronger tier
        self.assertIn("validated", " ".join(f["text"] for f in c["contradiction"]["flags"] if f["name"] == "TOI_DROP"))
        self.assertEqual(c["contradiction"]["level"], "MODERATE")
        self.assertEqual(c["recency_overlay"]["box_score_games"], 2)

    def test_the_probability_is_never_changed_by_the_overlay(self):
        off = self.run_review(self.ov_ctx([box("2026-10-21", 12.0), box("2026-10-20", 12.5)], live=False), toi=18.5)
        on = self.run_review(self.ov_ctx([box("2026-10-21", 12.0), box("2026-10-20", 12.5)]), toi=18.5)
        self.assertEqual(off["probabilities"]["estimate_used"], on["probabilities"]["estimate_used"])
        self.assertEqual(off["probabilities"]["model"], on["probabilities"]["model"])
        self.assertNotIn("recency_overlay", off)
        self.assertNotIn("TOI_DROP", self.names(off))

    def test_a_moderate_drop_is_a_moderate_flag_and_lowers_confidence(self):
        base = self.run_review(self.ov_ctx(), toi=18.5)
        c = self.run_review(self.ov_ctx([box("2026-10-21", 13.5), box("2026-10-20", 14.0)]), toi=18.5)       # (13.5, 14.0, 18.0) = 15.2, gap 3.3
        self.assertEqual(self.names(c)["TOI_DROP"], "MODERATE")
        self.assertEqual(c["contradiction"]["level"], "MODERATE")
        self.assertLessEqual(["LOW", "MEDIUM", "HIGH"].index(c["decision_confidence"]), ["LOW", "MEDIUM", "HIGH"].index(base["decision_confidence"]))

    def test_a_rise_is_context_only_and_never_inflates_anything(self):
        base = self.run_review(self.ov_ctx(live=False), toi=18.5)
        c = self.run_review(self.ov_ctx([box("2026-10-21", 24.0), box("2026-10-20", 24.0)]), toi=18.5)
        self.assertEqual(self.names(c)["TOI_RISE"], "LOW")
        self.assertEqual(c["contradiction"]["level"], base["contradiction"]["level"])
        self.assertEqual(c["probabilities"]["estimate_used"], base["probabilities"]["estimate_used"])

    def test_a_lost_power_play_role_is_flagged_for_a_shots_leg_where_the_data_exists(self):
        ctx = self.ov_ctx()
        for g in ctx.skater_state["players"][self.pid]["recent_games"][:3]:
            g["toi_pp"] = 0.2
        c = self.run_review(ctx, pp=2.4)
        self.assertEqual(self.names(c)["PP_ROLE_LOST"], "MODERATE")
        self.assertIn("carry no power-play time", " ".join(f["text"] for f in c["contradiction"]["flags"]))

    def test_the_existing_power_play_flag_is_not_counted_twice(self):
        ctx = self.ov_ctx(pp=0.2, ppu=None)
        c = self.run_review(ctx, market="points>=1", fam="PLAYER_POINTS", k=1, p=0.5, pp=2.0)
        n = self.names(c)
        self.assertIn("POWER_PLAY_ASSUMPTION", n)
        self.assertNotIn("PP_ROLE_LOST", n)

    def test_recent_threshold_form_far_below_the_estimate_is_flagged_for_shots(self):
        ctx = self.ov_ctx()
        for g, s in zip(ctx.skater_state["players"][self.pid]["recent_games"], (0, 1, 0, 3, 1)):
            g["shots"] = s
        c = self.run_review(ctx)
        self.assertEqual(self.names(c)["RECENT_FORM_BELOW_MODEL"], "MODERATE")
        self.assertEqual(c["recency_overlay"]["last5_hits"], 1)
        ok = self.ov_ctx()                                                                         # three or more of five: no flag
        self.assertNotIn("RECENT_FORM_BELOW_MODEL", self.names(self.run_review(ok)))

    def test_nothing_is_flagged_for_a_player_whose_latest_games_match_the_model(self):
        c = self.run_review(self.ov_ctx([box("2026-10-21", 18.2), box("2026-10-20", 17.8)]), toi=18.5)
        n = self.names(c)
        self.assertFalse({"TOI_DROP", "TOI_RISE", "PP_ROLE_LOST", "RECENT_FORM_BELOW_MODEL", "POSTMORTEM_ROLE_CAUTION"} & set(n))

    def test_the_overlay_is_off_unless_the_live_cohort_turns_it_on(self):
        c = self.run_review(self.ov_ctx([box("2026-10-21", 12.0), box("2026-10-20", 12.0)], live=False), toi=18.5)
        self.assertNotIn("TOI_DROP", self.names(c))
        self.assertIn("live_overlay=True", inspect.getsource(lc))
        self.assertFalse(ei.Context(now=None, as_of="x", evidence=None).live_overlay)


class TestPostmortemCaution(Overlay):
    KEY = ("test player", "AAA")

    def test_a_recent_role_finding_is_a_temporary_caution_on_that_player(self):
        c = self.run_review(self.ov_ctx(cautions={self.KEY: {"classification": "ROLE_CHANGE_MISSED", "date": "2026-10-19"}}), toi=18.5)
        self.assertEqual(self.names(c)["POSTMORTEM_ROLE_CAUTION"], "MODERATE")
        self.assertIn("ROLE_CHANGE_MISSED", " ".join(c["case_against"]))

    def test_the_caution_is_context_only_once_his_latest_game_is_back_at_the_modelled_ice_time(self):
        ctx = self.ov_ctx([box("2026-10-20", 18.0)], cautions={self.KEY: {"classification": "TOI_PROJECTION_ERROR", "date": "2026-10-19"}})
        c = self.run_review(ctx, toi=18.5)
        self.assertEqual(self.names(c)["POSTMORTEM_ROLE_CAUTION"], "LOW")

    def test_the_loader_reads_only_role_and_toi_findings_from_the_last_three_days_and_never_today(self):
        reviews = {"a": {"et_date": "2026-10-19", "assessment": {"classification": "ROLE_CHANGE_MISSED"}, "card": {"bet": {"player_or_team": "Noah Cates", "team": "PHI"}}},
                   "b": {"et_date": "2026-10-19", "assessment": {"classification": "MODEL_CALIBRATION"}, "card": {"bet": {"player_or_team": "Other Guy", "team": "PHI"}}},
                   "c": {"et_date": "2026-10-10", "assessment": {"classification": "TOI_PROJECTION_ERROR"}, "card": {"bet": {"player_or_team": "Old Guy", "team": "PHI"}}},
                   "d": {"et_date": "2026-10-21", "assessment": {"classification": "ROLE_CHANGE_MISSED"}, "card": {"bet": {"player_or_team": "Today Guy", "team": "PHI"}}},
                   "e": {"et_date": "2026-10-20", "assessment": {"classification": "TOI_PROJECTION_ERROR"}, "card": {"bet": {"player_or_team": "Nick Schmaltz", "team": "UTA"}}}}
        ctx = self.ov_ctx()
        ctx.as_of = "2026-10-21"
        with mock.patch("operational.learning_postmortem.load", return_value={"bet_reviews": reviews}):
            ei._load_overlay_inputs(ctx, None)
        self.assertEqual(set(ctx.role_cautions), {("noah cates", "PHI"), ("nick schmaltz", "UTA")})

    def test_the_loader_reads_stored_box_scores_before_today_and_a_failure_never_raises(self):
        ctx = self.ov_ctx()
        ctx.as_of = "2026-10-21"
        ctx.skater_state = {"players": {}, "newest_game_date": "2026-10-19"}
        rows = [{"player_id": 9, "date": "2026-10-20", "toi_minutes": 12.0, "shots": 1, "goals": 0, "assists": 0}, {"player_id": 9, "date": "2026-10-21", "toi_minutes": 10.0, "shots": 0, "goals": 0, "assists": 0}]
        with mock.patch("features.point_in_time.box_scores_since", return_value=rows), mock.patch("operational.learning_postmortem.load", return_value=None):
            ei._load_overlay_inputs(ctx, object())
        self.assertEqual([g["date"] for g in ctx.recent_box["9"]], ["2026-10-20"])
        with mock.patch("features.point_in_time.box_scores_since", side_effect=RuntimeError("x")), mock.patch("operational.learning_postmortem.load", side_effect=RuntimeError("y")):
            ei._load_overlay_inputs(ctx, object())
        self.assertTrue(any("unavailable" in n for n in ctx.notes))


class TestTheParlayBuilderRespectsIt(Overlay):
    def test_two_detectors_of_one_role_change_are_one_contradiction(self):
        ctx = self.ov_ctx([box("2026-10-21", 12.0), box("2026-10-20", 12.5)], cautions={("test player", "AAA"): {"classification": "TOI_PROJECTION_ERROR", "date": "2026-10-19"}})
        c = self.run_review(ctx, toi=18.5)
        self.assertEqual(c["contradiction"]["level"], "MODERATE")                                    # a drop and yesterday's postmortem finding describe ONE role change: counted once
        self.assertEqual({"TOI_DROP", "POSTMORTEM_ROLE_CAUTION"}, {f["name"] for f in c["contradiction"]["flags"]} & {"TOI_DROP", "POSTMORTEM_ROLE_CAUTION"})      # both traces stay on the card
        fam = {f["family"]: f for f in c["contradiction"]["families"]}
        self.assertEqual(sorted(fam["TOI_ROLE"]["flags"]), ["POSTMORTEM_ROLE_CAUTION", "TOI_DROP"])
        ok, why = lp.leg_ok({"leg": None, "card": c, "action": c["action"]["decision"]})
        self.assertTrue(ok or "contradiction" not in why, why)                                       # the contradiction no longer excludes it

    def test_a_validated_adjustment_is_still_applied_once(self):
        with mock.patch.object(ei, "signal_evidence", return_value=validated(adj=-0.02)):
            ctx = self.ov_ctx([box("2026-10-21", 12.0), box("2026-10-20", 12.5)], cautions={("test player", "AAA"): {"classification": "TOI_PROJECTION_ERROR", "date": "2026-10-19"}})
            base = self.run_review(self.ov_ctx(live=False), toi=18.5)
            c = self.run_review(ctx, toi=18.5)
        self.assertAlmostEqual(base["probabilities"]["estimate_used"] - c["probabilities"]["estimate_used"], 0.02, places=3)


if __name__ == "__main__":
    unittest.main()
