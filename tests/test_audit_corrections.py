"""The corrections from the read-only audit: the parlay-leg decision rubric, machine-readable lessons, versioned decision artifacts, the parlay product's own daily maximum, Player Points 1+ as the actionable
threshold, the goalie validation's rule, and the retired primary slip never shown as an active recording system."""
from __future__ import annotations

import hashlib
import json
import unittest
from unittest import mock

from operational import decision_artifacts as da
from operational import eggy_decisions as ed
from operational import eggy_intelligence as ei
from operational import learning_cohort as lc
from operational import learning_parlays as lp
from operational import lesson_state as ls
from tests.test_learning_cohort import D1, leg
from tests.test_learning_parlays import cand


def card(p=0.66, contradiction="LOW", cal="HIGH", flags=None, action="RECORD", comp=0.9, thesis="FAIR_PRICED"):
    return cand(p=p, contradiction=contradiction, cal=cal, flags=flags or [], action=action, thesis=thesis)["card"] | {"data_completeness": {"fraction": comp}}


class TestParlayLegRubric(unittest.TestCase):
    def test_a_likely_calibrated_role_supported_leg_is_good_without_any_value_thesis(self):
        q = ed.parlay_leg_quality(card(p=0.66, thesis="FAIR_PRICED"))
        self.assertEqual(q["label"], ed.GOOD)
        self.assertEqual(q["basis"], "PARLAY_LEG")
        self.assertEqual(ed.decision_quality(card(p=0.66, thesis="FAIR_PRICED"))["label"], ed.QUESTIONABLE)         # the straight-bet rubric never called such a leg good

    def test_a_leg_with_a_counted_moderate_doubt_or_a_thin_margin_is_questionable(self):
        self.assertEqual(ed.parlay_leg_quality(card(p=0.60))["label"], ed.QUESTIONABLE)
        self.assertEqual(ed.parlay_leg_quality(card(p=0.70, contradiction="MODERATE", flags=[{"name": "TOI_DROP", "level": "MODERATE", "text": "x"}]))["label"], ed.QUESTIONABLE)
        self.assertEqual(ed.parlay_leg_quality(card(p=0.70, cal="MEDIUM"))["label"], ed.GOOD)

    def test_a_leg_the_builders_own_gate_refuses_is_bad(self):
        high = [{"name": "ROLE_ASSUMPTION", "level": "HIGH", "text": "x"}]
        self.assertEqual(ed.parlay_leg_quality(card(p=0.7, contradiction="HIGH", flags=high))["label"], ed.BAD)
        self.assertEqual(ed.parlay_leg_quality(card(p=0.50))["label"], ed.BAD)
        self.assertEqual(ed.parlay_leg_quality(card(p=0.7, cal="LOW"))["label"], ed.BAD)
        self.assertEqual(ed.parlay_leg_quality(card(p=0.7, action="PASS", thesis="MODEL_SAYS_OVERPRICED"))["label"], ed.BAD)

    def test_a_price_only_pass_is_not_a_bad_parlay_leg(self):
        self.assertEqual(ed.parlay_leg_quality(card(p=0.7, action="PASS", thesis="PRICE_ABOVE_ESTIMATE"))["label"], ed.GOOD)

    def test_price_flags_and_a_model_that_likes_the_side_more_do_not_degrade_a_leg(self):
        flags = [{"name": "PRICE_ABOVE_ESTIMATE", "level": "HIGH", "text": "x"}, {"name": "MODEL_FAR_ABOVE_MARKET", "level": "HIGH", "text": "x"}]
        self.assertEqual(ed.parlay_leg_quality(card(p=0.7, contradiction="HIGH", flags=flags))["label"], ed.GOOD)

    def test_insufficient_evidence_is_not_judged(self):
        self.assertEqual(ed.parlay_leg_quality(card(cal="INSUFFICIENT"))["label"], ed.INSUFFICIENT)
        self.assertEqual(ed.parlay_leg_quality(card(comp=0.3))["label"], ed.INSUFFICIENT)
        self.assertEqual(ed.parlay_leg_quality({})["label"], ed.INSUFFICIENT)

    def test_the_quality_never_reads_the_result_and_a_win_is_not_called_luck_for_lacking_a_value_thesis(self):
        c = card(p=0.66)
        win = ed.assess({"result_status": "WIN", "market_family": "PLAYER_SOG_ALTERNATE", "threshold": 2}, c, None, parlay_leg=True)
        loss = ed.assess({"result_status": "LOSS", "market_family": "PLAYER_SOG_ALTERNATE", "threshold": 2}, c, None, parlay_leg=True)
        self.assertEqual(win["decision_quality"], loss["decision_quality"])
        self.assertEqual(win["lesson"]["signal"], "GOOD_DECISION_WON")
        self.assertNotIn("BAD_DECISION_WON_LUCK", win["lesson"]["signal"])
        straight = ed.assess({"result_status": "WIN", "market_family": "PLAYER_SOG_ALTERNATE", "threshold": 2}, card(p=0.66, thesis="NONE", action="PASS"), None)
        self.assertEqual(straight["lesson"]["signal"], "BAD_DECISION_WON_LUCK")                                      # the straight-bet rubric is unchanged for straight bets

    def test_all_six_outcomes_exist(self):
        seen = set()
        for c in (card(p=0.66), card(p=0.60), card(p=0.5)):
            for o in ("WIN", "LOSS"):
                a = ed.assess({"result_status": o, "market_family": "PLAYER_SOG_ALTERNATE", "threshold": 2}, c, None, parlay_leg=True)
                seen.add((a["decision_quality"]["label"], o))
        self.assertEqual(seen, {(g, o) for g in (ed.GOOD, ed.QUESTIONABLE, ed.BAD) for o in ("WIN", "LOSS")})
        self.assertEqual(ed.ASSESS_VERSION, 4)                                                                       # stored assessments of the older rubric are rebuilt, never reused

    def test_the_postmortem_grades_parlay_legs_with_the_parlay_rubric(self):
        import inspect
        from operational import learning_postmortem as lpm
        self.assertIn("parlay_leg=True", inspect.getsource(lpm.parlay_summaries))


class TestTheRubricCanFindABadDecisionTheGateAdmitted(unittest.TestCase):
    """The rubric is independent of the admission gate: every leg below passes leg_ok, and several are still BAD decisions on pregame information. No outcome is an input."""

    def admitted(self, c):
        self.assertTrue(lp.leg_ok({"leg": None, "card": c, "action": c["action"]["decision"]})[0], "the fixture must be a leg the gate lets in")
        return c

    def test_no_parameter_of_the_rubric_is_an_outcome(self):
        import inspect
        self.assertEqual(list(inspect.signature(ed.parlay_leg_quality).parameters), ["card", "context"])
        for k in ("outcome", "result", "result_status", "won"):
            self.assertNotIn(k, str(inspect.getsource(ed.parlay_leg_quality)).split('"""')[2])

    def test_a_moneyline_leg_whose_estimate_is_a_stale_quote_is_bad_although_the_gate_admitted_it(self):
        c = self.admitted(cand(p=0.64, fam="MONEYLINE", k=None, team="TOR")["card"])
        c["data_completeness"] = {"fraction": 0.9, "missing": ["confirmed starting goalies", "injuries and lineup"]}
        stale, fresh = {"leg": {"freshness_status": "STALE_QUOTE", "quote_age_min": 433.0}}, {"leg": {"freshness_status": "FRESH", "quote_age_min": 12.0}}
        self.assertEqual(ed.parlay_leg_quality(c, stale)["label"], ed.BAD)
        self.assertIn("STALE_MARKET_ESTIMATE", ed.parlay_leg_quality(c, stale)["criteria"])
        self.assertEqual(ed.parlay_leg_quality(c, fresh)["label"], ed.GOOD)
        p = card(p=0.66)
        self.assertEqual(ed.parlay_leg_quality(p, {"leg": {"freshness_status": "STALE_QUOTE"}})["label"], ed.GOOD)          # a prop's PRICE does not enter its hit probability: not held against it

    def test_an_estimate_two_standard_errors_above_what_its_own_analogues_did_is_bad(self):
        c = self.admitted(card(p=0.70))
        c["calibration_confidence"]["evidence"] = {"n": 1500, "z": -3.1, "obs": 0.64, "pred": 0.72, "se": 0.011}
        self.assertEqual(ed.parlay_leg_quality(c)["label"], ed.BAD)
        self.assertIn("CONTRADICTS_ITS_OWN_CALIBRATION", ed.parlay_leg_quality(c)["criteria"])
        c["calibration_confidence"]["evidence"] = {"n": 1500, "z": -3.1, "obs": 0.69, "pred": 0.72, "se": 0.011}              # observed within two standard errors of the claim
        self.assertEqual(ed.parlay_leg_quality(c)["label"], ed.GOOD)
        c["calibration_confidence"]["evidence"] = {"n": 120, "z": -3.1, "obs": 0.60, "pred": 0.72, "se": 0.04}                # too few cases to adjust anything: the engine's own rule
        self.assertEqual(ed.parlay_leg_quality(c)["label"], ed.GOOD)

    def test_validated_evidence_that_was_not_applied_makes_a_leg_bad_only_if_applying_it_puts_the_estimate_under_the_floor(self):
        ev = {"ROLE_ASSUMPTION": {"shots>=2": {"verdict": "BEYOND_MODEL_LOWER", "live_adjustment": -0.10}}}
        flag = [{"name": "ROLE_ASSUMPTION", "level": "MODERATE", "text": "x"}]
        leg_ = {"leg": {"market_family": "PLAYER_SOG_ALTERNATE", "threshold": 2}, "date": "2026-10-22"}
        with mock.patch.object(ei, "signal_evidence", return_value=ev):
            low = self.admitted(card(p=0.62, contradiction="MODERATE", flags=flag))
            self.assertEqual(ed.parlay_leg_quality(low, leg_)["label"], ed.BAD)                                               # 62% less 10 points is 52%
            self.assertIn("EVIDENCE_NOT_APPLIED", ed.parlay_leg_quality(low, leg_)["criteria"])
            high = self.admitted(card(p=0.75, contradiction="MODERATE", flags=flag))
            self.assertEqual(ed.parlay_leg_quality(high, leg_)["label"], ed.QUESTIONABLE)                                      # 65% still clears the floor: a doubt, not a defect
            self.assertEqual(ed.parlay_leg_quality(low, {**leg_, "date": "2026-10-19"})["label"], ed.QUESTIONABLE)             # the evidence did not exist before 2026-10-20: it cannot judge an earlier decision
            applied = self.admitted(card(p=0.62, contradiction="MODERATE", flags=flag))
            applied["signal_adjustment"] = {"signal": "ROLE_ASSUMPTION", "delta": -0.10}
            self.assertEqual(ed.parlay_leg_quality(applied, leg_)["label"], ed.QUESTIONABLE)                                   # a card that already took it off is not charged twice

    def test_a_player_with_fewer_prior_games_than_the_models_validation_required_is_bad_and_a_thin_record_is_a_doubt(self):
        for games, want in ((3, ed.BAD), (30, ed.QUESTIONABLE), (120, ed.GOOD)):
            c = self.admitted(card(p=0.66))
            c["player_view"] = {"games_total": games}
            self.assertEqual(ed.parlay_leg_quality(c)["label"], want, games)

    def test_missing_data_that_a_source_could_have_supplied_counts_but_what_no_source_supplies_does_not(self):
        base = lambda missing: dict(card(p=0.66), data_completeness={"fraction": 0.7, "missing": missing})
        self.assertEqual(ed.parlay_leg_quality(base(["confirmed lineup", "injury news"]))["label"], ed.GOOD)
        self.assertEqual(ed.parlay_leg_quality(base(["confirmed lineup", "hit-rate record"]))["label"], ed.QUESTIONABLE)
        self.assertEqual(ed.parlay_leg_quality(base(["hit-rate record", "price observation"]))["label"], ed.BAD)

    def test_a_claim_of_75_percent_or_more_on_calibration_below_high_is_a_doubt(self):
        self.assertEqual(ed.parlay_leg_quality(card(p=0.78, cal="MEDIUM"))["label"], ed.QUESTIONABLE)
        self.assertEqual(ed.parlay_leg_quality(card(p=0.78, cal="HIGH"))["label"], ed.GOOD)

    def test_the_same_role_or_model_uncertainty_staked_on_two_tickets_is_a_doubt(self):
        risky = card(p=0.70, cal="MEDIUM")
        self.assertEqual(ed.parlay_leg_quality(risky, {"reuse_count": 1})["label"], ed.GOOD)
        self.assertEqual(ed.parlay_leg_quality(risky, {"reuse_count": 2})["label"], ed.QUESTIONABLE)
        self.assertEqual(ed.parlay_leg_quality(card(p=0.70), {"reuse_count": 2})["label"], ed.GOOD)                            # a reused leg with no uncertainty is not a doubt

    def test_a_bad_decision_can_win_and_a_good_one_can_lose(self):
        stale = self.admitted(cand(p=0.64, fam="MONEYLINE", k=None, team="TOR")["card"])
        ctx = {"leg": {"freshness_status": "STALE_QUOTE", "quote_age_min": 400.0}}
        win = ed.assess({"result_status": "WIN", "market_family": "MONEYLINE"}, stale, None, parlay_leg=True, context=ctx)
        good_loss = ed.assess({"result_status": "LOSS", "market_family": "PLAYER_SOG_ALTERNATE", "threshold": 2}, card(p=0.70), None, parlay_leg=True, context={})
        self.assertEqual((win["decision_quality"]["label"], win["outcome"]), (ed.BAD, "WIN"))
        self.assertEqual(win["lesson"]["signal"], "BAD_DECISION_WON_LUCK")
        self.assertEqual((good_loss["decision_quality"]["label"], good_loss["outcome"]), (ed.GOOD, "LOSS"))
        loss = ed.assess({"result_status": "LOSS", "market_family": "MONEYLINE"}, stale, None, parlay_leg=True, context=ctx)
        self.assertEqual(loss["decision_quality"], win["decision_quality"])                                                      # the label is the same whichever way it ended


class TestTheSimilarityBandIsEvidenceNotAConstant(unittest.TestCase):
    def tick(self, est, se):
        c = cand(p=est)
        if se is not None:
            c["card"]["calibration_confidence"]["evidence"] = {"se": se}
        return {"cands": [c], "weakest_leg": {"index": 0, "estimate": est}}

    def test_two_estimates_are_similar_only_when_their_difference_is_inside_two_standard_errors_of_the_difference(self):
        a = self.tick(0.70, 0.011)
        self.assertTrue(lp.similar_strength(a, self.tick(0.675, 0.011)))                 # 2.5 points < 2 * sqrt(2) * 1.1 = 3.1 points
        self.assertFalse(lp.similar_strength(a, self.tick(0.66, 0.011)))                 # 4 points: distinguishable
        self.assertTrue(lp.similar_strength(a, self.tick(0.66, 0.030)))                  # a poorly known leg is similar to more: the band follows the uncertainty
        self.assertFalse(lp.similar_strength(a, self.tick(0.69, 0.001)) and lp.similar_strength(self.tick(0.70, 0.001), self.tick(0.69, 0.001)))     # precisely known legs: 1 point already differs

    def test_with_no_calibration_evidence_there_is_no_tolerance_at_all(self):
        self.assertFalse(lp.similar_strength(self.tick(0.70, None), self.tick(0.699, None)))
        self.assertTrue(lp.similar_strength(self.tick(0.70, None), self.tick(0.70, None)))                                            # only an exact tie: strict ranking

    def test_the_standard_error_is_the_one_of_the_weakest_leg_and_moneylines_carry_theirs_too(self):
        c = cand(p=0.64, fam="MONEYLINE", k=None)
        c["card"]["calibration_confidence"]["evidence"] = {"standard_error": 0.0104, "n": 2177}
        self.assertEqual(lp.weakest_se({"cands": [c], "weakest_leg": {"index": 0}}), 0.0104)

    def test_no_numeric_constant_is_borrowed_from_the_points_rule(self):
        import inspect
        self.assertFalse(hasattr(lp, "SIMILAR_STRENGTH"))
        self.assertEqual(lp.SIMILARITY_Z, ei.Z_SIGNIFICANT)
        self.assertNotIn("0.02", inspect.getsource(lp.similar_strength))


class TestLessonStates(unittest.TestCase):
    METRICS = {"legs": {"calibration_by_market_and_band": {"PLAYER_SOG_ALTERNATE 2+": {"60%-70%": {"n": 40, "hit": 22, "rate": 0.55, "expected_rate": 0.66, "z": -1.5, "sufficient": True},
                                                                                     "70%-80%": {"n": 12, "hit": 8, "rate": 0.67, "expected_rate": 0.74, "z": -0.5, "sufficient": False}},
                       "PLAYER_POINTS 1+": {"60%-70%": {"n": 35, "hit": 15, "rate": 0.43, "expected_rate": 0.65, "z": -2.7, "sufficient": True}}},
                       "reused_vs_not": {"used on 2+ tickets": {"n": 28, "hit": 20, "rate": 0.71, "expected_rate": 0.66, "z": 0.6}}, "rubric": {"GOOD_DECISION": {"n": 4, "hit": 3}}},
               "tickets": {"good_vs_questionable": {"GOOD": {"n": 16, "hit": 7, "rate": 0.44, "expected_rate": 0.34, "z": 0.8}, "QUESTIONABLE": {"n": 15, "hit": 2, "rate": 0.13, "expected_rate": 0.24, "z": -1.0}},
                           "weakest_leg_vs_other_legs": {"weakest leg": {"n": 29, "hit": 19, "rate": 0.66, "expected_rate": 0.63, "z": 0.3}}},
               "flagship": {"settled": 4, "hit": 1}, "postmortem": {"cause_counts": {"UNKNOWN": 4, "OUTCOME_WITHIN_ESTIMATE": 8, "MODEL_CALIBRATION": 2}, "unknown_rate": 0.22}}
    EV = {"POSTMORTEM_ROLE_CAUTION": {"shots>=2": {"verdict": "BEYOND_MODEL_LOWER", "live_adjustment": -0.018}}}

    def states(self, caut=None, ev=None):
        cal = [{"market": "PLAYER_POINTS 1+", "band": "60%-70%", "lo": 0.6, "hi": 0.7, "n": 35, "z": -2.7}]
        return ls.build(self.METRICS, [{"player": "P", "team": "T", "classification": "TOI_PROJECTION_ERROR", "date": "2026-10-24"}], caut if caut is not None else cal, ev if ev is not None else self.EV, {"by_gap_band": {}})

    def test_every_lesson_has_exactly_one_state_and_the_three_states_are_all_used(self):
        s = self.states()
        self.assertTrue(all(x["state"] in (ls.ACTIONABLE, ls.ACCUMULATING, ls.REPORT_ONLY) for x in s))
        self.assertEqual({x["state"] for x in s}, {ls.ACTIONABLE, ls.ACCUMULATING, ls.REPORT_ONLY})

    def test_an_actionable_lesson_names_the_code_that_reads_it(self):
        for x in self.states():
            if x["state"] == ls.ACTIONABLE:
                self.assertIn(x["id"], ls.CONSUMERS, x["id"])
                self.assertTrue(x["consumed_by"])

    def test_a_role_caution_is_actionable_only_because_the_evidence_file_says_it_carries_information(self):
        self.assertEqual(next(x for x in self.states() if x["id"] == "ROLE_CAUTION")["state"], ls.ACTIONABLE)
        self.assertEqual(next(x for x in self.states(ev={"POSTMORTEM_ROLE_CAUTION": {"shots>=2": {"verdict": "ALREADY_IN_MODEL"}}}) if x["id"] == "ROLE_CAUTION")["state"], ls.REPORT_ONLY)

    def test_a_calibration_band_becomes_actionable_at_the_sample_bar_and_accumulates_before_it(self):
        s = self.states()
        act = [x for x in s if x["id"] == "CALIBRATION_BAND_OVERCONFIDENCE" and x["state"] == ls.ACTIONABLE]
        acc = [x for x in s if x["id"] == "CALIBRATION_BAND_OVERCONFIDENCE" and x["state"] == ls.ACCUMULATING]
        self.assertEqual([x["name"] for x in act], ["PLAYER_POINTS 1+ estimates in the 60%-70% band have run too high"])
        self.assertTrue(any("70%-80%" in x["name"] for x in acc))
        self.assertTrue(all(x["threshold"]["n_required"] == 30 and x["threshold"]["abs_z_required"] == 2.0 for x in acc))

    def test_rule_and_policy_lessons_accumulate_and_only_ever_become_a_proposal_to_the_owner(self):
        for lid in ("REUSED_LEG_PERFORMANCE", "WEAKEST_LEG_PERFORMANCE", "GOOD_TICKET_PERFORMANCE", "QUESTIONABLE_TICKET_PERFORMANCE", "FLAGSHIP_PERFORMANCE"):
            x = next(x for x in self.states() if x["id"] == lid)
            self.assertEqual(x["state"], ls.ACCUMULATING, lid)
            self.assertFalse(x["can_change_decisions_automatically"], lid)
            self.assertTrue(x["owner_decision_required"], lid)
            self.assertIn("PROPOSAL", x["on_threshold"], lid)

    def test_a_player_who_killed_two_tickets_is_not_penalised(self):
        x = next(x for x in self.states() if x["id"] == "REPEATED_KILLER_LEG")
        self.assertEqual(x["state"], ls.REPORT_ONLY)
        self.assertFalse(x["can_change_decisions_automatically"])

    def test_variance_and_unknown_losses_are_report_only(self):
        for x in self.states():
            if x["id"] in ("LOSS_NORMAL_VARIANCE", "LOSS_OUTCOME_WITHIN_ESTIMATE", "LOSS_UNKNOWN"):
                self.assertEqual(x["state"], ls.REPORT_ONLY)

    def test_the_review_consumes_only_the_actionable_lessons(self):
        from operational import eggy_intelligence as ei_
        from operational import learning_postmortem as lpm
        doc = lambda states: {"lessons": {"cautions": [{"player": "Test Player", "team": "AAA", "classification": "TOI_PROJECTION_ERROR", "date": "2026-10-24"}],
                                           "calibration_cautions": [{"market": "PLAYER_POINTS 1+", "band": "60%-70%", "lo": 0.6, "hi": 0.7, "n": 35, "z": -2.7, "predicted": 0.65, "observed": 0.43}], "lesson_state": states}}
        ctx = mock.MagicMock()
        ctx.as_of, ctx.role_cautions, ctx.calibration_cautions, ctx.recent_box, ctx.skater_state = "2026-10-25", {}, [], {}, {}
        for states, want_role, want_cal in (([{"id": "ROLE_CAUTION", "state": "ACTIONABLE"}, {"id": "CALIBRATION_BAND_OVERCONFIDENCE", "state": "ACTIONABLE"}], True, True),
                                             ([{"id": "ROLE_CAUTION", "state": "REPORT_ONLY"}, {"id": "CALIBRATION_BAND_OVERCONFIDENCE", "state": "ACCUMULATING"}], False, False),
                                             (None, True, True)):                                                          # a document from before lesson states: read as before
            ctx.role_cautions, ctx.calibration_cautions = {}, []
            d = doc(states)
            if states is None:
                del d["lessons"]["lesson_state"]
            with mock.patch.object(lpm, "load", return_value=d):
                ei_._load_overlay_inputs(ctx, None)
            self.assertEqual(bool(ctx.role_cautions), want_role, states)
            self.assertEqual(bool(ctx.calibration_cautions), want_cal, states)


class TestVersionedDecisionArtifacts(unittest.TestCase):
    def test_the_policy_pins_every_decision_artifact_and_a_changed_one_changes_the_digest(self):
        pinned = lc.POLICY["decision_architecture"]["artifacts"]
        self.assertEqual(set(pinned), {"player_signals", "points_thresholds", "dependence_lift"})
        before = lc.policy_digest()
        with mock.patch.dict(lc.POLICY["decision_architecture"]["artifacts"], {"dependence_lift": "0" * 16}):
            self.assertNotEqual(lc.policy_digest(), before)                                                              # a policy change: the cohort fails closed until it is approved

    def test_the_pinned_checksums_are_the_files_checksums(self):
        for n, short in lc.POLICY["decision_architecture"]["artifacts"].items():
            self.assertEqual(short, hashlib.sha256((da.VALIDATION / da.ARTIFACTS[n]).read_bytes()).hexdigest()[:16])

    def test_the_manifest_describes_exactly_the_pinned_artifacts(self):
        self.assertEqual(da.manifest_problems(), [])
        m = da.manifest()["artifacts"]
        for n in da.ARTIFACTS:
            self.assertTrue(m[n]["decides"] and m[n]["read_by"] and m[n]["regenerate"], n)

    def test_the_evidence_is_pinned_by_identity_not_by_bytes(self):
        ev = lc.POLICY["decision_architecture"]["evidence"]
        self.assertNotIn("sha256", ev)
        self.assertIn("identity", ev)
        self.assertEqual(ev["max_unverified_days"], 45)

    def test_new_game_data_never_changes_the_digest(self):
        from research.product_models import matchup
        base = json.loads((da.VALIDATION / "eggy_evidence.json").read_text())
        changed = json.loads(json.dumps(base))
        changed["built_at_utc"], changed["data_through"] = "2026-12-01T00:00:00Z", "2026-11-30"
        changed["matchup"]["coefficients"]["gd"] += 0.01
        changed["skater"]["rows"] += 5000
        self.assertEqual(matchup.evidence_identity(base), matchup.evidence_identity(changed))


class TestTheProductOwnsItsDailyMaximum(unittest.TestCase):
    def test_the_parlay_maximum_does_not_follow_the_retired_straight_bet_target(self):
        self.assertEqual(lc.PARLAY_DAILY_MAX, lp.MAX_PARLAYS_PER_DAY)
        with mock.patch.object(lc, "DAILY_TARGET", 3), mock.patch.object(lc, "UNIT", "PARLAY"):
            self.assertEqual(lc.daily_cap(), 10)
            caps = lc.cumulative_caps({"1": 0, "2": 1}, 2)
            self.assertEqual(max(caps.values()), 10)
        with mock.patch.object(lc, "DAILY_TARGET", 3), mock.patch.object(lc, "UNIT", "STRAIGHT"):
            self.assertEqual(lc.daily_cap(), 3)
        with mock.patch.object(lp, "MAX_PARLAYS_PER_DAY", 7):
            self.assertEqual(lc.PARLAY_DAILY_MAX, 10)                                                                     # a import-time constant of the product, named in the policy
        self.assertEqual(lc.POLICY["daily_parlay_max"], 10)


class TestPlayerPointsOneIsTheActionableThreshold(unittest.TestCase):
    def test_one_plus_is_actionable_and_two_and_three_are_research_only(self):
        pol = ei.points_policy()
        self.assertEqual(pol["actionable"], [1])
        self.assertEqual(pol["research_only"], [2, 3])
        self.assertGreaterEqual(pol["cases_at_or_above_the_leg_floor"][1], 200)
        self.assertEqual(pol["cases_at_or_above_the_leg_floor"][2], 0)

    def test_a_one_plus_points_leg_enters_a_parlay_and_a_two_plus_one_is_refused_without_lowering_the_floor(self):
        one = cand(pid="1", game="1", fam="PLAYER_POINTS", k=1, p=0.62)
        two = cand(pid="2", game="2", fam="PLAYER_POINTS", k=2, p=0.60)
        self.assertEqual(lp.leg_ok(one), (True, ""))
        ok, why = lp.leg_ok(two)
        self.assertFalse(ok)
        self.assertIn("RESEARCH ONLY", why)
        self.assertEqual(lp.MIN_LEG_P, 0.55)
        out = lp.build([one, cand(pid="3", game="3", p=0.66), cand(pid="4", game="4", p=0.64), cand(pid="5", game="5", p=0.63), cand(pid="6", game="6", p=0.62)], slots=3)
        self.assertTrue(any(c["leg"].market_family == "PLAYER_POINTS" for p in out["parlays"] for c in p["cands"]))

    def test_the_cohort_names_the_reason_a_research_only_threshold_is_not_a_candidate(self):
        why = lc.eligibility_reasons(leg(fam="PLAYER_POINTS", k=2, price=-250.0), D1)
        self.assertTrue(any("MODEL_THRESHOLD_NOT_ACTIONABLE (points 2+" in w and "research only" in w for w in why), why)
        self.assertFalse(any("MODEL_THRESHOLD_NOT_ACTIONABLE" in w for w in lc.eligibility_reasons(leg(fam="PLAYER_POINTS", k=1, price=-250.0), D1)))

    def test_same_game_points_legs_get_a_measured_dependence_not_an_assumed_one(self):
        a, b = cand(pid="1", game="1", fam="PLAYER_POINTS", k=1, p=0.62, team="AAA"), cand(pid="2", game="1", fam="PLAYER_POINTS", k=1, p=0.60, team="AAA")
        c = cand(pid="3", game="2", p=0.66)
        t = lp.construct([a, b, c])
        self.assertEqual(t["dependence_level"], "MEASURED_DEPENDENCE")
        self.assertIn("PTS_PTS_TEAMMATES", t["probability_basis"])


class TestTheGoalieRule(unittest.TestCase):
    def test_the_ratings_use_only_games_before_the_game(self):
        from deploy import validate_goalie_signal as g
        starts = {1: {"AAA": {"pid": "g1", "gsax": 5.0, "date": "2026-01-01", "ice": 3600}, "BBB": {"pid": "g2", "gsax": -1.0, "date": "2026-01-01", "ice": 3600}},
                  2: {"AAA": {"pid": "g1", "gsax": 0.0, "date": "2026-01-03", "ice": 3600}, "BBB": {"pid": "g2", "gsax": 0.0, "date": "2026-01-03", "ice": 3600}}}
        R = g.ratings(starts)
        self.assertEqual(R[1]["AAA"]["actual"], 0.0)                                                                      # nothing is known before his first start
        self.assertGreater(R[2]["AAA"]["actual"], 0.0)
        self.assertLess(R[2]["BBB"]["actual"], 0.0)
        self.assertEqual(R[1]["AAA"]["expected"], 0.0)
        self.assertGreater(R[2]["AAA"]["expected"], 0.0)

    def test_a_signal_is_integrated_only_when_its_interval_is_entirely_above_zero(self):
        from deploy import validate_goalie_signal as g
        self.assertEqual(g._verdict({"ci_low": 0.0001}), "INTEGRATE")
        self.assertTrue(g._verdict({"ci_low": -0.0001}).startswith("REJECT"))

    def test_the_recorded_result_is_a_rejection_and_nothing_goalie_enters_the_probabilities(self):
        d = json.loads((da.VALIDATION / "goalie_signal.json").read_text())
        self.assertEqual(d["integrated"], [])
        self.assertTrue(d["decision"].startswith("REJECT"))
        import inspect
        for fn in (ei.review_player, ei.review_moneyline):
            self.assertNotIn("goalie_signal", inspect.getsource(fn))


class TestTheRetiredPrimarySlipIsNotAnActiveRecordingSystem(unittest.TestCase):
    def test_the_health_check_calls_it_retired_and_never_approved_or_active(self):
        import datetime as dt
        from operational import daily_tickets, watchdog
        with mock.patch.object(daily_tickets, "LEGACY_PRIMARY_SLIP_RECORDING", False):                                  # production: the tests' package switches it on only to exercise old machinery
            c = watchdog.check_ticket_policy()
        self.assertEqual(c["status"], watchdog.OK)
        self.assertIn("RETIRED", c["detail"])
        self.assertIn("records nothing", c["detail"])
        self.assertNotIn(", approved", c["detail"])
        with mock.patch("operational.recording_pause.status", return_value={"paused": False}):
            d = watchdog.check_recording_pause(dt.datetime.now(dt.timezone.utc))["detail"]
        self.assertIn("learning parlays", d)
        self.assertNotEqual(d, "automatic recording is on")

    def test_the_legacy_switch_is_off_in_production_and_only_an_environment_variable_turns_it_on(self):
        import inspect
        from operational import daily_tickets
        src = inspect.getsource(daily_tickets).split("LEGACY_PRIMARY_SLIP_RECORDING = ")[1].split("\n")[0]
        self.assertIn('os.environ.get("NHL_ENGINE_LEGACY_PRIMARY_RECORDING") == "1"', src)

    def test_the_live_recording_system_is_the_cohort_and_its_digest_is_shown(self):
        from operational import watchdog
        with mock.patch.object(lc, "is_authorised", return_value=True):
            c = watchdog.check_learning_cohort()
        self.assertEqual(c["status"], watchdog.OK)
        self.assertIn(lc.policy_digest(), c["detail"])
        with mock.patch.object(lc, "is_authorised", return_value=False):
            self.assertEqual(watchdog.check_learning_cohort()["status"], watchdog.FAIL)                                 # a digest nobody approved no longer stops recording silently


if __name__ == "__main__":
    unittest.main()
