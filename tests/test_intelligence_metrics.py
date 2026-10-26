"""The instrument that shows whether the engine is getting smarter (operational/intelligence_metrics.py), the cause logic that keeps the postmortem's UNKNOWN honest (operational/eggy_decisions.py outcome_cause),
the evidence clock, and a PLAYER POINTS leg carried through recording, settlement and the postmortem."""
from __future__ import annotations

import datetime as dt
import json
import unittest
from unittest import mock

from operational import eggy_decisions as ed
from operational import eggy_intelligence as ei
from operational import intelligence_metrics as im
from operational import learning_cohort as lc
from operational import learning_postmortem as lpm
from tests.test_learning_cohort import Base, D1, leg


class TestTheArithmetic(unittest.TestCase):
    def test_a_cell_reports_hits_against_expected_with_a_z_score_and_says_when_n_is_too_small(self):
        a = im._Acc()
        for p, hit in ((0.6, True), (0.6, False), (0.6, True)):
            a.add(p, hit)
        c = a.cell()
        self.assertEqual((c["n"], c["hit"], c["expected_hit"], c["sufficient"]), (3, 2, 1.8, False))
        self.assertAlmostEqual(c["z"], (2 - 1.8) / (3 * 0.6 * 0.4) ** 0.5, places=2)
        big = im._Acc()
        for i in range(40):
            big.add(0.5, i % 2 == 0)
        self.assertTrue(big.cell()["sufficient"])
        self.assertEqual(big.cell()["z"], 0.0)

    def test_probability_bands_and_an_empty_cell_never_divide_by_zero(self):
        self.assertEqual(im._band(0.65), "60%-70%")
        self.assertEqual(im._band(0.95), "80%-100%")
        self.assertIsNone(im._Acc().cell()["rate"])


class TestEvidenceClock(unittest.TestCase):
    NOW = dt.datetime(2026, 11, 1, 12, 0, tzinfo=dt.timezone.utc)

    def health(self, built):
        return ei.evidence_health({"built_at_utc": built, "data_through": "x"}, self.NOW)

    def test_ok_expiring_expired_and_missing_are_told_apart_before_the_layer_stops(self):
        self.assertEqual(self.health("2026-10-21T12:00:00Z")["state"], "OK")
        self.assertEqual(self.health("2026-10-14T12:00:00Z")["state"], "OK")              # 18 days old of 45
        self.assertEqual(self.health("2026-09-24T12:00:00Z")["state"], "EXPIRING")        # 38 days old: 7 left
        self.assertEqual(self.health("2026-09-10T12:00:00Z")["state"], "EXPIRED")
        self.assertEqual(ei.evidence_health(None, self.NOW)["state"], "MISSING")
        self.assertEqual(self.health("2026-09-24T12:00:00Z")["days_remaining"], 7.0)
        self.assertIn("prepared automatically", self.health("2026-10-21T12:00:00Z")["controlled_refresh"])


def claim(cid, verdict, text="c"):
    return {"id": cid, "claim": text, "stated": 1, "actual": 1, "note": "n", "verdict": verdict}


class TestWhatCausedTheLoss(unittest.TestCase):
    CARD = {"kind": "PLAYER", "contradiction": {"level": "HIGH", "flags": []}, "thesis": {"type": "FAIR_PRICED"}}

    def test_a_loss_the_estimate_allowed_with_the_role_facts_held_is_chance_and_the_decision_label_stays_separate(self):
        cat, why = ed.outcome_cause(self.CARD, [claim("expected_toi", "HELD", "expected 17 minutes on ice")], 0.66)
        self.assertEqual(cat, "OUTCOME_WITHIN_ESTIMATE")
        self.assertIn("judged separately", why)

    def test_a_specific_cause_is_never_overridden_by_chance(self):
        cat, _ = ed.outcome_cause(self.CARD, [claim("expected_toi", "FAILED")], 0.66)
        self.assertEqual(cat, "TOI_PROJECTION_ERROR")
        cat, _ = ed.outcome_cause(self.CARD, [claim("expected_pp", "FAILED")], 0.66)
        self.assertEqual(cat, "ROLE_CHANGE_MISSED")

    def test_no_testable_fact_is_insufficient_facts_not_variance(self):
        self.assertEqual(ed.outcome_cause(self.CARD, [claim("data", "NOT_TESTABLE")], 0.66)[0], "INSUFFICIENT_FACTS")
        self.assertEqual(ed.outcome_cause(self.CARD, [], 0.66)[0], "INSUFFICIENT_FACTS")

    def test_a_loss_rarer_than_the_estimate_allowed_with_the_facts_held_stays_unknown_and_is_investigated(self):
        cat, why = ed.outcome_cause(self.CARD, [claim("expected_toi", "HELD")], 0.97)
        self.assertEqual(cat, "UNKNOWN")
        self.assertIn("investigate", why)

    def test_assess_carries_a_version_so_an_older_stored_review_is_rebuilt(self):
        a = ed.assess({"result_status": "LOSS", "market_family": "PLAYER_SOG_ALTERNATE", "participant_id": "9", "game_id": "1", "threshold": 2}, None, None)
        self.assertEqual(a["version"], ed.ASSESS_VERSION)


class TestPlayerPointsEndToEnd(Base):
    """A points leg is recorded in a parlay with the same review, settled automatically from goals and assists, and reviewed by the postmortem."""
    def setUp(self):
        super().setUp()
        p = mock.patch.object(lc, "UNIT", "PARLAY")
        p.start()
        self.addCleanup(p.stop)
        self.nhl.execute("INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team, away_team, schedule_observed_at_utc, game_state, source) VALUES (?,?,?,?,?,?,?,?,?)",
                         ("4", "20262027", "2026-10-20", "2026-10-20T23:00:00", "TOR", "MTL", "2026-10-20T00:00:00", "SCHEDULED", "test"))
        self.nhl.commit()

    def legs(self):
        pts = [leg(pid="8470001", game="1", fam="PLAYER_POINTS", k=1, price=-200.0, p=0.70)]
        sog = [leg(pid=str(8470002 + i), game=g, p=0.68 - 0.02 * i, price=-150.0) for i, g in enumerate(("2", "4", "1", "2"))]
        return pts + sog

    def test_the_points_leg_is_recorded_settled_by_goals_plus_assists_and_postmortemed(self):
        self.cycle(self.legs())
        rows = [dict(r) for r in self.conn.execute("SELECT * FROM parlays")]
        self.assertTrue(rows)
        with_pts = [r for r in rows if any(l["leg"]["market_family"] == "PLAYER_POINTS" for l in json.loads(r["legs_json"]))]
        self.assertTrue(with_pts, "a validated points leg must be able to enter a parlay")
        for r in rows:
            for l in json.loads(r["legs_json"]):
                L = l["leg"]
                self.nhl.execute("UPDATE games SET game_state='FINAL', home_score=3, away_score=2, final_period_type='REG' WHERE game_id=?", (str(L["game_id"]),))
                goals, assists = (1, 1) if L["market_family"] == "PLAYER_POINTS" else (0, 0)
                self.nhl.execute("INSERT OR IGNORE INTO player_game_stats (game_id, player_id, team_id, toi_minutes, goals, assists, shots, played, revision_number, effective_at_utc, observed_at_utc, source) "
                                 "VALUES (?,?,?,?,?,?,?,?,1,'x','x','test')", (str(L["game_id"]), str(L["participant_id"]), "TOR", 18.0, goals, assists, 3, 1))
        self.nhl.commit()
        lc.settle_parlays(self.conn, self.nhl, D1 + dt.timedelta(hours=8))
        r = [dict(x) for x in self.conn.execute("SELECT * FROM parlays WHERE parlay_id=?", (with_pts[0]["parlay_id"],))][0]
        res = {x["label"].split(" — ")[1]: x["outcome"] for x in json.loads(r["settlement_json"])["leg_results"]}
        self.assertEqual(res.get("POINTS 1+"), "WIN")                                       # one goal and one assist is two points: a 1+ points leg wins
        doc = lpm.build(self.conn, self.nhl, dt.datetime(2026, 10, 21, 12, 0, tzinfo=dt.timezone.utc))
        pts_reviews = [lr for p in doc["parlays"] for lr in p["leg_reviews"] if "POINTS 1+" in lr["label"]]
        self.assertTrue(pts_reviews)
        self.assertTrue(all(x["assessment"]["version"] == ed.ASSESS_VERSION for x in pts_reviews))
        m = doc["intelligence_metrics"]
        self.assertIn("PLAYER_POINTS 1+", m["legs"]["by_market_and_threshold"])
        self.assertGreaterEqual(m["points"]["live_points_legs_settled"], 1)
        self.assertEqual(m["priced_metrics"]["status"], "NOT AVAILABLE")                   # no combined price exists: no ROI is invented


class TestTheMetricsOnARealLedger(Base):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(lc, "UNIT", "PARLAY")
        p.start()
        self.addCleanup(p.stop)
        self.nhl.execute("INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team, away_team, schedule_observed_at_utc, game_state, source) VALUES (?,?,?,?,?,?,?,?,?)",
                         ("4", "20262027", "2026-10-20", "2026-10-20T23:00:00", "TOR", "MTL", "2026-10-20T00:00:00", "SCHEDULED", "test"))
        self.nhl.commit()

    def test_the_document_carries_every_tracked_measure_and_never_invents_a_priced_one(self):
        self.cycle([leg(pid=str(8470001 + i), game=g, p=0.70 - 0.02 * i, price=-130.0) for i, g in enumerate(("1", "2", "4", "1", "2", "4"))])
        for r in self.conn.execute("SELECT legs_json FROM parlays").fetchall():
            for l in json.loads(r["legs_json"]):
                L = l["leg"]
                self.nhl.execute("UPDATE games SET game_state='FINAL', home_score=3, away_score=2, final_period_type='REG' WHERE game_id=?", (str(L["game_id"]),))
                self.nhl.execute("INSERT OR IGNORE INTO player_game_stats (game_id, player_id, team_id, toi_minutes, goals, assists, shots, played, revision_number, effective_at_utc, observed_at_utc, source) "
                                 "VALUES (?,?,?,?,?,?,?,?,1,'x','x','test')", (str(L["game_id"]), str(L["participant_id"]), "TOR", 18.0, 0, 0, 3, 1))
        self.nhl.commit()
        lc.settle_parlays(self.conn, self.nhl, D1 + dt.timedelta(hours=8))
        doc = lpm.build(self.conn, self.nhl, dt.datetime(2026, 10, 21, 12, 0, tzinfo=dt.timezone.utc))
        m = doc["intelligence_metrics"]
        for k in ("legs", "moneyline_model_vs_market", "tickets", "flagship", "postmortem", "priced_metrics", "evidence_health", "points", "calibration_cautions"):
            self.assertIn(k, m, k)
        self.assertEqual(set(m["legs"]), {"all", "calibration_by_probability_band", "by_market_and_threshold", "calibration_by_market_and_band", "flagged_vs_unflagged", "reused_vs_not"})
        self.assertEqual(set(m["tickets"]), {"by_day", "all", "good_vs_questionable", "ticket_probability_bands", "probability_basis", "weakest_leg_vs_other_legs", "why_missed_tickets_missed"})
        self.assertEqual(m["legs"]["all"]["hit"], m["legs"]["all"]["n"])                    # every leg got 3 shots: all won
        self.assertEqual(sum(c["n"] for c in m["legs"]["calibration_by_probability_band"].values()), m["legs"]["all"]["n"])
        self.assertIn("unknown_rate", m["postmortem"])
        self.assertNotIn("roi", json.dumps(m["tickets"]).lower())
        with mock.patch.object(lpm, "load", return_value=doc), mock.patch.object(lc, "load_status", return_value={"unit": "PARLAY", "authorised": True}):
            self.assertEqual(lpm.snapshot_section()["postmortem"]["intelligence_metrics"]["legs"]["all"], m["legs"]["all"])


if __name__ == "__main__":
    unittest.main()


class TestTheLoopFromSettledLegsBackToTheNextReview(unittest.TestCase):
    """A band the engine's own settled legs show it over-predicts (enough cases, 2+ standard errors) becomes a standing caution in the next review: evidence, never three bad days, never a probability change."""
    CAUTION = {"market": "PLAYER_SOG_ALTERNATE 2+", "band": "60%-70%", "lo": 0.6, "hi": 0.7, "n": 40, "predicted": 0.65, "observed": 0.45, "z": -2.6}

    def ctx_with(self, caution, live=True):
        from tests import test_eggy_intelligence as tei
        t = tei.TestPlayerReview()
        t.setUp()
        c = t.ctx()
        c.live_overlay = live
        c.calibration_cautions = [caution] if caution else []
        return t, c, tei

    def review(self, caution, p=0.65, live=True):
        t, c, tei = self.ctx_with(caution, live)
        return t.review(tei.prop_leg(price=-110.0, p=p), p, ctx=c)

    def test_a_leg_in_an_over_predicting_band_is_flagged_as_model_trust_and_the_probability_is_untouched(self):
        c = self.review(self.CAUTION)
        names = {f["name"]: f["level"] for f in c["contradiction"]["flags"]}
        self.assertEqual(names["LIVE_CALIBRATION_OVERCONFIDENT"], "MODERATE")
        self.assertEqual(ei.flag_layer("LIVE_CALIBRATION_OVERCONFIDENT"), ei.MODEL_TRUST)
        self.assertEqual(c["probabilities"]["estimate_used"], self.review(None)["probabilities"]["estimate_used"])

    def test_a_leg_outside_the_band_or_another_market_or_the_overlay_off_is_not_flagged(self):
        for kw in ({"p": 0.75}, {"live": False}):
            self.assertNotIn("LIVE_CALIBRATION_OVERCONFIDENT", {f["name"] for f in self.review(self.CAUTION, **kw)["contradiction"]["flags"]})
        other = dict(self.CAUTION, market="MONEYLINE")
        self.assertNotIn("LIVE_CALIBRATION_OVERCONFIDENT", {f["name"] for f in self.review(other)["contradiction"]["flags"]})

    def test_the_metrics_raise_a_caution_only_when_the_sample_bar_is_cleared(self):
        small, big = im._Acc(), im._Acc()
        for i in range(10):
            small.add(0.65, False)                                   # 10 straight misses at 65%: not enough cases
        for i in range(40):
            big.add(0.65, i < 18)                                    # 40 legs at 65%, 18 hit: z well below -2
        self.assertFalse(small.cell()["sufficient"])
        self.assertTrue(big.cell()["sufficient"])
        self.assertLessEqual(big.cell()["z"], -2.0)

    def test_the_caution_travels_in_the_lessons_and_is_loaded_by_the_review_context(self):
        from operational import eggy_intelligence as e
        doc = {"lessons": {"cautions": [], "calibration_cautions": [self.CAUTION]}}
        ctx = ei.Context(now=None, as_of="2026-10-26", evidence=None)
        with mock.patch("operational.learning_postmortem.load", return_value=doc):
            e._load_overlay_inputs(ctx, None)
        self.assertEqual(ctx.calibration_cautions, [self.CAUTION])
        lessons = lpm.build_lessons(None, [], {}, "2026-10-26", {"calibration_cautions": [self.CAUTION]})
        self.assertEqual(lessons["calibration_cautions"], [self.CAUTION])


class TestAPartlyHeldFactIsNotChance(unittest.TestCase):
    def test_a_fact_that_held_only_in_part_stays_unknown_and_is_investigated(self):
        card = {"kind": "PLAYER", "contradiction": {"level": "LOW", "flags": []}, "thesis": {"type": "FAIR_PRICED"}}
        cat, why = ed.outcome_cause(card, [claim("expected_toi", "PARTLY_HELD", "expected 17 minutes")], 0.60)
        self.assertEqual(cat, "UNKNOWN")
        self.assertIn("only in part", why)


class TestThePortfolio(unittest.TestCase):
    from operational import learning_parlays as _lp

    def leg(self, key, p=0.7, team="AAA", game="1"):
        return {"key": key, "label": key, "estimate": p, "team": team, "game_id": game, "role_risk": False}

    def test_shared_legs_widen_the_days_spread_and_distinct_legs_do_not(self):
        from operational import learning_parlays as lp
        shared = [{"probability": 0.343, "legs": [self.leg("a"), self.leg("b"), self.leg("c")]}, {"probability": 0.343, "legs": [self.leg("a"), self.leg("d"), self.leg("e")]},
                  {"probability": 0.343, "legs": [self.leg("a"), self.leg("f"), self.leg("g")]}]
        distinct = [{"probability": 0.343, "legs": [self.leg(f"{i}{j}") for j in "abc"]} for i in range(3)]
        s, d = lp.portfolio_summary(shared), lp.portfolio_summary(distinct)
        self.assertEqual((s["tickets"], s["distinct_legs"], s["leg_slots"]), (3, 7, 9))
        self.assertEqual(s["most_reused_leg"]["tickets"], 3)
        self.assertGreater(s["spread_inflation_from_shared_legs"], 1.2)
        self.assertAlmostEqual(d["spread_inflation_from_shared_legs"], 1.0, delta=0.12)
        self.assertEqual(s["expected_tickets_hit"], round(3 * 0.343, 2))
        self.assertGreaterEqual(s["chance_no_ticket_hits"], d["chance_no_ticket_hits"] - 0.05)

    def test_an_empty_day_and_the_ledger_form_work(self):
        from operational import learning_parlays as lp
        self.assertEqual(lp.portfolio_summary([]), {"tickets": 0})
        self.assertEqual(lp.portfolio_from_rows([]), {"tickets": 0})


class TestMeasuredDependenceInTheTicket(unittest.TestCase):
    def cand(self, pid, game, fam="PLAYER_SOG_ALTERNATE", k=2, team="AAA", p=0.68):
        from tests import test_learning_parlays as tlp
        return tlp.cand(pid=pid, game=game, p=p, fam=fam, k=k, team=team)

    def test_two_shots_legs_in_one_game_are_measured_independent_and_get_a_combined_probability(self):
        from operational import learning_parlays as lp
        t = lp.construct([self.cand("1", "1", team="AAA"), self.cand("2", "1", team="BBB"), self.cand("3", "2", team="CCC")])
        self.assertEqual(t["dependence_level"], "MEASURED_DEPENDENCE")
        self.assertIsNotNone(t["combined_probability"])
        self.assertIn("EVIDENCE_ADJUSTED_PRODUCT", t["probability_basis"])
        self.assertAlmostEqual(t["combined_probability"], 0.68 ** 3 * lp.dependence_lifts()["SOG_SOG_OPPOSING"]["dependence_lift"], places=3)
        self.assertLessEqual(t["combined_probability"], t["joint_bounds"]["high"])
        self.assertGreaterEqual(t["combined_probability"], t["joint_bounds"]["low"])

    def test_teammates_points_legs_are_measured_positive_so_the_ticket_is_more_likely_than_the_product(self):
        from operational import learning_parlays as lp
        t = lp.construct([self.cand("1", "1", fam="PLAYER_POINTS", k=1, team="AAA"), self.cand("2", "1", fam="PLAYER_POINTS", k=1, team="AAA"), self.cand("3", "2", team="CCC")])
        self.assertGreater(t["combined_probability"], 0.68 ** 3)
        self.assertEqual(lp.pair_type(t["legs"][0], t["legs"][1]), "PTS_PTS_TEAMMATES")

    def test_a_moneyline_with_the_opposing_sides_points_is_measured_negative(self):
        from operational import learning_parlays as lp
        ml = self.cand("AAA", "1", fam="MONEYLINE", k=None, team="AAA", p=0.66)
        pts = self.cand("9", "1", fam="PLAYER_POINTS", k=1, team="BBB")
        self.assertEqual(lp.pair_type(ml["leg"], pts["leg"]), "ML_PTS_OPPOSING")
        t = lp.construct([ml, pts, self.cand("3", "2", team="CCC")])
        self.assertLess(t["combined_probability"], 0.66 * 0.68 * 0.68)

    def test_a_pair_that_was_never_measured_leaves_the_combined_probability_unquantified(self):
        from operational import learning_parlays as lp
        with mock.patch.object(lp, "dependence_lifts", return_value={}):
            t = lp.construct([self.cand("1", "1", team="AAA"), self.cand("2", "1", team="AAA"), self.cand("3", "2", team="CCC")])
        self.assertIsNone(t["combined_probability"])
        self.assertIn(t["dependence_level"], ("UNCERTAIN", "POSITIVE_NOT_QUANTIFIED"))
