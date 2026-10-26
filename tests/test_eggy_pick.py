"""EGGY PICK OF THE DAY (operational/eggy_pick.py, eggy_pick_ledger.py): one NHL side (moneyline or puck line) or PASS, a supported edge or nothing, a written case for and against, its own record and prospective
signal trust, and the learning-parlay engine untouched."""
from __future__ import annotations

import datetime as dt
import inspect
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from operational import eggy_candidates as ec
from operational import eggy_intelligence as ei
from operational import eggy_pick as ep
from operational import eggy_pick_ledger as epl
from operational import learning_cohort as lc
from operational import price_freshness as pf

Z = dt.timezone.utc
FRESH = {"status": "FRESH", "reason": "62 minutes old", "category": "MARKET_DATA_QUALITY"}


def mv(change):
    d = "TOWARD" if change >= 0.01 else ("AWAY" if change <= -0.01 else "FLAT")
    return {"earliest_price": -110, "earliest_at_utc": "2026-10-26T12:00:00Z", "current_price": -118, "current_at_utc": "2026-10-26T21:35:00Z", "earliest_no_vig": 0.5, "current_no_vig": 0.5 + change, "change": change,
            "direction": d, "magnitude_points": round(abs(change) * 100, 1), "largest_step_at_utc": "2026-10-26T21:35:00Z", "captures": 2}


def cand(side="DAL", market="MONEYLINE", price=105.0, model=0.52, mkt=0.47, trust="DISAGREEMENT_HAS_SIGNAL", line=None, fresh=FRESH, calib="HIGH", comp=0.9, moved=None, drivers=2, rest=None, opponent="WPG", blend=None, unc=0.0104,
         structure_state=None):
    c = {"game_id": "1", "matchup": f"{opponent} @ {side}", "side": side, "opponent": opponent, "home": side, "away": opponent, "start_utc": "2026-10-26T23:00:00Z", "capture_at_utc": "2026-10-26T21:40:00Z",
         "price_freshness": fresh, "model_win_probability": model, "calibration": calib, "data_completeness": comp, "flags": [], "card_thesis": "MODEL_SEES_VALUE", "market": market, "line": line, "price": price,
         "implied_with_margin": round(ei.implied_from_american(price), 4), "market_probability_market": mkt, "model_probability": model, "model_probability_raw": model, "probability_uncertainty": unc, "calibration_note": None,
         "trust_state": trust, "movement": None if moved is None else mv(moved), "rest_days": rest or {side: 2, opponent: 2}, "blend": blend,
         "hockey_view": {"favourite": side if model >= 0.5 else opponent, "p_favourite": max(model, 1 - model), "gap": "CLEAR", "drivers": [{"label": f"driver {i}", "favours": side, "log_odds_toward_home": 0.2} for i in range(drivers)]}}
    ep._qualify(c, structure_state)
    return c


class TestThePolicy(unittest.TestCase):
    def test_only_a_moneyline_or_a_puck_line_one_a_day_and_never_a_prop_or_a_parlay(self):
        self.assertEqual(ep.POLICY["markets"], ["MONEYLINE", "PUCK_LINE"])
        self.assertEqual(ep.POLICY["max_outputs_per_day"], 1)
        for banned in ("player props", "player points", "shots", "parlays"):
            self.assertIn(banned, ep.POLICY["no"])
        self.assertEqual(ep.POLICY["price_policy"]["preferred_region_american"], [-130, 130])

    def test_the_hierarchy_has_the_market_first_and_the_model_market_gap_is_one_input_not_a_prerequisite(self):
        h = ep.POLICY["decision_hierarchy"]
        self.assertTrue(h[0].startswith("A"), h)
        self.assertEqual(set(ep.STRUCTURES), {"SUPPORTED_MARKET_GAP", "CONSENSUS_QUALITY", "MARKET_SITUATIONAL_OPPORTUNITY"})
        self.assertNotIn("DISAGREEMENT_HAS_SIGNAL", inspect.getsource(ep._qualify))                   # the gate is no longer a precondition of qualifying

    def test_every_price_and_balance_threshold_that_is_not_measured_is_labelled_a_heuristic(self):
        self.assertIn("HEURISTIC", json.dumps(ep.POLICY["price_policy"]))
        self.assertIn("HEURISTIC", json.dumps(ep.POLICY["balance"]))
        self.assertIn("HEURISTIC", json.dumps(ep.POLICY["thresholds"]))

    def test_the_eggy_digest_covers_the_decision_policy_and_the_puck_line_calibration(self):
        self.assertEqual(ec.OFFICIAL_POLICY["policy_id"], ep.POLICY["policy_id"])
        self.assertEqual(ec.OFFICIAL_POLICY["decision_policy_digest"], ep.policy_digest())
        self.assertEqual(len(ec.OFFICIAL_POLICY["puck_line_calibration_sha256"]), 16)
        self.assertNotEqual(ec.official_policy_digest(), "af5ed8907d646212")                       # the flagship-parlay digest is retired as Eggy's output

    def test_the_learning_engine_is_untouched(self):
        self.assertEqual(lc.policy_digest(), "aea7cf0fbcbc632f")                                     # the cohort policy digest the owner approved: not changed by this product
        self.assertEqual(lc.PARLAY_DAILY_MAX, 10)
        from operational import real_parlay_paper_trader as t
        src = inspect.getsource(t)
        self.assertIn("learning_cohort.run_cycle", src)
        self.assertIn("eggy_pick_ledger.run_cycle", src)                                             # both run, side by side, each failure isolated from the other

    def test_no_outcome_is_an_input_to_the_selection(self):
        for fn in (ep.research_order, ep.classify_structure, ep._qualify, ep.balance, ep.cases, ep.decide):
            src = inspect.getsource(fn).lower()
            for banned in ("home_score", "away_score", "result", "outcome", " won"):
                self.assertNotIn(banned, src.replace("not_qualifying_because", ""), (fn.__name__, banned))


class TestThePriceAndTheMarket(unittest.TestCase):
    def test_price_classes_preferred_region_is_a_preference_and_the_extreme_guard_a_labelled_heuristic(self):
        for price, want in ((-130, "IN_PREFERRED_REGION"), (130, "IN_PREFERRED_REGION"), (-115, "IN_PREFERRED_REGION"), (105, "IN_PREFERRED_REGION"), (-150, "OUTSIDE_PREFERRED"), (160, "OUTSIDE_PREFERRED"),
                            (-200, "OUTSIDE_PREFERRED"), (-230, "EXTREME"), (210, "EXTREME")):
            self.assertEqual(ep.price_class(price), want, price)
        self.assertFalse(hasattr(ep, "required_edge"))                                                 # the two-times-the-edge shoulder rule is retired

    def test_the_no_vig_probability_removes_the_margin(self):
        self.assertAlmostEqual(ep._no_vig(-110, -110), 0.5, places=6)
        self.assertAlmostEqual(ep._no_vig(-135, 114) + ep._no_vig(114, -135), 1.0, places=6)

    def test_the_puck_line_probability_comes_from_the_validated_margin_calibration(self):
        cal = ep.calibration()
        self.assertIn("out_of_time_2025", cal)
        fav, dog = ep.puck_line_cover(0.60, -1.5), ep.puck_line_cover(0.40, 1.5)
        self.assertLess(fav, 0.60)
        self.assertGreater(dog, 0.40)
        self.assertGreater(ep.puck_line_cover(0.65, -1.5), ep.puck_line_cover(0.55, -1.5))

    def test_a_puck_line_bucket_that_over_predicted_lowers_the_cover_probability_and_widens_its_uncertainty(self):
        u = ep.pl_uncertainty(0.57, -1.5)                                                              # the 0.55-0.60 bucket over-predicted out of time by about 4.7 points
        self.assertGreater(u["bias_in_bucket"], 0.03)
        self.assertAlmostEqual(u["adjusted"], u["raw"] - u["bias_in_bucket"], places=4)
        self.assertGreaterEqual(u["uncertainty"], u["bias_in_bucket"])

    def test_a_puck_line_is_not_preferred_for_a_larger_nominal_edge(self):
        ml = cand(market="MONEYLINE", price=-115, model=0.56, mkt=0.53, moved=0.02, drivers=3, unc=0.0104)
        pl = cand(market="PUCK_LINE", line=-1.5, price=120, model=0.48, mkt=0.40, moved=0.02, drivers=3, unc=0.05)
        self.assertGreater(pl["gap"], ml["gap"])                                                       # the puck line shows the larger nominal gap
        self.assertLess(pl["conservative_edge"] - pl["edge"], ml["conservative_edge"] - ml["edge"])  # and the larger uncertainty
        self.assertLess(ep.research_order(ml), ep.research_order(pl)) if ml["qualifies"] else None

    def test_league_captures_reads_prices_up_to_now_and_names_the_teams(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            ev = {"id": "e1", "home_team": "New York Rangers", "away_team": "Los Angeles Kings", "commence_time": "2026-10-26T23:10:00Z",
                  "bookmakers": [{"key": "draftkings", "markets": [{"key": "h2h", "outcomes": [{"name": "Los Angeles Kings", "price": 114}, {"name": "New York Rangers", "price": -135}]},
                                                                  {"key": "spreads", "outcomes": [{"name": "Los Angeles Kings", "price": -230, "point": 1.5}, {"name": "New York Rangers", "price": 190, "point": -1.5}]}]}]}
            for stamp in ("20261026T120005Z", "20261026T213900Z"):
                (base / f"{stamp}_sports-icehockey_nhl-odds_na_h2h+spreads.json").write_text(json.dumps({"meta": {"retrieved_at_utc": stamp[:4] + "-" + stamp[4:6] + "-" + stamp[6:8] + "T" + stamp[9:11] + ":" + stamp[11:13] + ":" + stamp[13:15] + "Z"}, "response": [ev]}))
            caps = ep.league_captures("2026-10-26", dt.datetime(2026, 10, 26, 21, 0, tzinfo=Z), base)
            self.assertEqual(len(caps), 1)
            e = caps[0]["events"]["e1"]
            self.assertEqual((e["home"], e["away"]), ("NYR", "LAK"))
            self.assertEqual(e["ml"], {"NYR": -135, "LAK": 114})
            self.assertEqual(e["pl"]["NYR"], (190, -1.5))

    def test_movement_records_earliest_current_change_direction_magnitude_and_when(self):
        def capture(h, home_price, away_price):
            ev = {"home": "DAL", "away": "WPG", "ml": {"DAL": home_price, "WPG": away_price}, "pl": {}}
            return {"at": dt.datetime(2026, 10, 26, h, 0, tzinfo=Z), "events": {"e": ev}}
        m = ep.movement([capture(12, -110, -110), capture(18, -125, 105), capture(21, -135, 115)], "DAL", "WPG", "DAL", "MONEYLINE")
        self.assertEqual((m["earliest_price"], m["current_price"], m["direction"], m["captures"]), (-110, -135, "TOWARD", 3))
        self.assertGreater(m["magnitude_points"], 5)
        self.assertEqual(m["largest_step_at_utc"], "2026-10-26T18:00:00Z")
        self.assertIsNone(ep.movement([capture(12, -110, -110)], "DAL", "WPG", "DAL", "MONEYLINE"))      # one capture is not a movement


class TestTrustChangesTheWeightNeverTheEligibility(unittest.TestCase):
    def test_proven_gets_full_weight_failing_none_and_in_between_the_records_own_bounded_estimate(self):
        self.assertEqual(ep.weight_for("DISAGREEMENT_HAS_SIGNAL", 0.06, None)[0], 1.0)
        self.assertEqual(ep.weight_for("DISAGREEMENT_IS_OVERCONFIDENCE", 0.06, {"k_hat": 0.9})[0], 0.0)
        self.assertEqual(ep.weight_for("INSUFFICIENT_EVIDENCE", 0.06, {"k_hat": 0.2, "games": 120, "ci95": [-1, 1.5]})[0], 0.2)
        self.assertEqual(ep.weight_for("INSUFFICIENT_EVIDENCE", 0.06, {"k_hat": 1.4, "games": 120, "ci95": [0, 2]})[0], 0.5)       # never more than half until proven
        self.assertEqual(ep.weight_for("INSUFFICIENT_EVIDENCE", 0.06, {"k_hat": -0.4, "games": 120, "ci95": [-1, 1]})[0], 0.0)
        self.assertEqual(ep.weight_for("ACCUMULATING", 0.06, None)[0], 0.0)

    def test_the_blend_estimate_is_fit_to_the_record_not_assumed(self):
        rows = [{"market_home": 0.5, "hockey_home": 0.6, "home_won": 1 if i % 2 else 0} for i in range(100)]
        self.assertAlmostEqual(ep.blend_estimate(rows)["k_hat"], ep.blend_estimate(rows)["k_hat"])
        b = ep.blend_estimate(rows)
        self.assertEqual(b["games"], 100)
        self.assertLessEqual(b["k_hat"], 0.05)                                                         # the model said 60% and won 50%: the record gives it no weight
        self.assertIsNone(ep.blend_estimate([])["k_hat"])

    def test_an_unproven_gap_is_weighted_shown_and_can_never_carry_a_pick_alone(self):
        c = cand(price=-110, model=0.60, mkt=0.50, trust="INSUFFICIENT_EVIDENCE", drivers=3, blend={"k_hat": 0.2, "games": 120, "ci95": [-1, 1.5]})
        self.assertEqual(c["model_weight"], 0.2)
        self.assertAlmostEqual(c["supported_probability"], 0.52, places=3)
        item = [x for x in c["case_for"] if x["kind"] == "MODEL_EDGE"][0]
        self.assertEqual(item["strength"], "UNPROVEN")
        self.assertEqual(c["structure"], "NONE")                                                       # no movement, no rest, no price edge beyond the margin: the gap alone is not enough
        self.assertFalse(c["qualifies"])

    def test_a_failing_model_is_a_reason_against_trusting_it_not_for_the_side(self):
        c = cand(price=-110, model=0.60, mkt=0.50, trust="DISAGREEMENT_IS_OVERCONFIDENCE", drivers=3)
        self.assertEqual(c["model_weight"], 0.0)
        self.assertTrue(any(x["kind"] == "MODEL_OVERCONFIDENT" for x in c["case_against"]))
        self.assertFalse(any(x["kind"] == "MODEL_EDGE" and x["strength"] == "STRONG" for x in c["case_for"]))

    def test_a_proven_gap_is_a_strong_reason(self):
        c = cand(price=105, model=0.56, mkt=0.47, trust="DISAGREEMENT_HAS_SIGNAL", drivers=3)
        self.assertTrue(any(x["kind"] == "MODEL_EDGE" and x["strength"] == "STRONG" for x in c["case_for"]))
        self.assertEqual(c["structure"], "SUPPORTED_MARKET_GAP")


class TestTheThreeEvidenceStructures(unittest.TestCase):
    def test_type_b_a_supported_market_gap_needs_a_proven_material_gap_and_a_positive_edge(self):
        c = cand(price=105, model=0.55, mkt=0.47, trust="DISAGREEMENT_HAS_SIGNAL", drivers=3)
        self.assertEqual((c["structure"], c["qualifies"]), ("SUPPORTED_MARKET_GAP", True))
        self.assertGreaterEqual(c["edge"], ei.EDGE_MIN)

    def test_type_a_consensus_quality_needs_no_model_edge_and_claims_no_positive_expected_value(self):
        c = cand(price=-115, model=0.55, mkt=0.52, trust="INSUFFICIENT_EVIDENCE", drivers=3)
        self.assertEqual((c["structure"], c["qualifies"]), ("CONSENSUS_QUALITY", True), c["not_qualifying_because"])
        self.assertLess(c["edge"], 0)                                                                  # the bookmaker's margin is the cost; the pick does not pretend otherwise
        self.assertTrue(any(x["kind"] == "INDEPENDENT_READS_AGREE" for x in c["case_for"]))

    def test_type_a_does_not_apply_outside_the_preferred_price_or_with_thin_hockey_support(self):
        self.assertEqual(cand(price=-165, model=0.62, mkt=0.60, trust="INSUFFICIENT_EVIDENCE", drivers=3)["structure"], "NONE")
        self.assertEqual(cand(price=-115, model=0.56, mkt=0.535, trust="INSUFFICIENT_EVIDENCE", drivers=1)["structure"], "NONE")

    def test_type_c_needs_a_sound_hockey_picture_and_two_measured_facts_and_a_defensible_price(self):
        c = cand(price=-130, model=0.62, mkt=0.55, trust="INSUFFICIENT_EVIDENCE", drivers=3, moved=0.02, blend={"k_hat": 0.3, "games": 120, "ci95": [-1, 1.5]})
        self.assertEqual((c["structure"], c["qualifies"]), ("MARKET_SITUATIONAL_OPPORTUNITY", True), c["not_qualifying_because"])
        no_move = cand(price=-130, model=0.62, mkt=0.55, trust="INSUFFICIENT_EVIDENCE", drivers=3)
        self.assertEqual(no_move["structure"], "NONE")                                                  # a story without a measured fact is not enough
        self.assertEqual(cand(price=-130, model=0.62, mkt=0.55, trust="INSUFFICIENT_EVIDENCE", drivers=3, moved=-0.02)["structure"], "NONE")   # the market moved against the side

    def test_a_structure_the_record_has_shown_to_fail_does_not_qualify(self):
        st = {"CONSENSUS_QUALITY": {"decision_state": "DISAGREEMENT_IS_OVERCONFIDENCE"}}
        self.assertEqual(cand(price=-115, model=0.56, mkt=0.535, trust="INSUFFICIENT_EVIDENCE", drivers=3, structure_state=st)["structure"], "NONE")

    def test_the_strongest_applicable_structure_wins(self):
        b = cand(price=105, model=0.55, mkt=0.47, drivers=3)
        a = cand(side="FLA", opponent="CHI", price=-115, model=0.56, mkt=0.535, trust="INSUFFICIENT_EVIDENCE", drivers=3)
        self.assertEqual(ep.decide([a, b], now=dt.datetime(2026, 10, 26, 21, 40, tzinfo=Z))["pick"]["structure"], "SUPPORTED_MARKET_GAP")


class TestTheBlockers(unittest.TestCase):
    def test_a_stale_price_is_a_market_data_reason_and_blocks_the_pick(self):
        c = cand(fresh={"status": "STALE", "reason": "442 minutes old", "category": "MARKET_DATA_QUALITY"}, model=0.56, mkt=0.47, drivers=3)
        self.assertFalse(c["qualifies"])
        self.assertTrue(any(w.startswith("MARKET DATA QUALITY") for w in c["not_qualifying_because"]))
        self.assertTrue(any(x["kind"] == "STALE_PRICE" and x["strength"] == "STRONG" for x in c["case_against"]))

    def test_an_extreme_price_blocks_it_and_is_labelled_a_heuristic(self):
        c = cand(price=-260, model=0.78, mkt=0.70, drivers=3)
        self.assertFalse(c["qualifies"])
        self.assertTrue(any("extreme" in w and "heuristic" in w for w in c["not_qualifying_because"]))

    def test_weak_data_or_calibration_blocks_it(self):
        self.assertFalse(cand(model=0.56, mkt=0.47, drivers=3, calib="LOW")["qualifies"])
        self.assertFalse(cand(model=0.56, mkt=0.47, drivers=3, comp=0.4)["qualifies"])


class TestTheCaseForAndAgainst(unittest.TestCase):
    def test_every_item_has_a_category_and_a_strength(self):
        c = cand(model=0.56, mkt=0.47, drivers=3, moved=0.03)
        for x in c["case_for"] + c["case_against"]:
            self.assertIn(x["category"], {"MARKET", "HOCKEY", "PRICE", "MARKET_BEHAVIOUR", "MODEL", "SITUATION", "DATA"})
            self.assertIn(x["strength"], {"STRONG", "MODERATE", "WEAK", "UNPROVEN"})

    def test_the_opposing_hockey_picture_is_a_reason_against(self):
        c = cand(price=105, model=0.40, mkt=0.47, drivers=0)
        self.assertTrue(any(x["kind"] == "HOCKEY_FAVOURS_OPPONENT" for x in c["case_against"]))

    def test_balance_needs_two_supporting_categories_and_no_strong_reason_against(self):
        S = lambda k, cat="HOCKEY": {"strength": k, "category": cat}
        self.assertFalse(ep.balance([S("STRONG"), S("MODERATE")], [S("STRONG")])["materially_outweighs"])
        self.assertTrue(ep.balance([S("STRONG"), S("MODERATE", "MARKET")], [S("WEAK")])["materially_outweighs"])
        self.assertFalse(ep.balance([S("MODERATE"), S("MODERATE")], [])["materially_outweighs"])       # both from one category
        self.assertFalse(ep.balance([S("UNPROVEN", "MODEL"), S("WEAK", "PRICE")], [])["materially_outweighs"])

    def test_movement_is_market_information_never_called_sharp_money(self):
        c = cand(moved=0.03)
        mvi = [x for x in c["case_for"] if x["kind"] == "PRICE_MOVEMENT"][0]
        self.assertIn("not called sharp money", mvi["text"])
        self.assertIn("not yet been validated", mvi["text"])

    def test_the_goalie_and_the_margin_are_stated_as_limits(self):
        txt = " ".join(x["text"] for x in cand()["case_against"])
        self.assertIn("not confirmed by an approved source", txt)
        self.assertIn("bookmaker's margin", txt)

    def test_the_puck_line_calibration_is_a_reason_against_with_its_uncertainty(self):
        c = cand(market="PUCK_LINE", line=-1.5, price=120, model=0.40, mkt=0.38, unc=0.047)
        self.assertTrue(any(x["kind"] == "PUCK_LINE_CALIBRATION" and "4.7 points" in x["text"] for x in c["case_against"]))


class TestTheDecision(unittest.TestCase):
    NOW = dt.datetime(2026, 10, 26, 21, 40, tzinfo=Z)

    def test_pass_is_legitimate_when_no_side_has_the_evidence_and_says_why_with_the_top_three(self):
        d = ep.decide([cand(model=0.60, mkt=0.47, trust="INSUFFICIENT_EVIDENCE", drivers=1), cand(side="WPG", opponent="DAL", price=-125, model=0.5, mkt=0.53, trust="INSUFFICIENT_EVIDENCE", drivers=1)], now=self.NOW)
        self.assertEqual(d["decision"], "PASS")
        self.assertIn("no side meets the standard", d["pass_reason"])
        self.assertEqual(len(d["top_three"]), 2)
        for t in d["top_three"]:
            for k in ("side", "market", "price", "market_probability_market", "model_probability", "gap", "trust_state", "hockey_case", "market_price_case", "case_against", "structure", "qualifies"):
                self.assertIn(k, t)
        self.assertIn("what_would_change_it", d)
        self.assertNotIn("pick", d)

    def test_exactly_one_pick_never_a_list_and_the_best_structure_and_price_come_first(self):
        a, b = cand(side="DAL", model=0.55, mkt=0.47, price=105, drivers=3), cand(side="FLA", opponent="CHI", price=-190, model=0.64, mkt=0.55, drivers=3)
        d = ep.decide([b, a], now=self.NOW)
        self.assertEqual(d["decision"], "PICK")
        self.assertEqual(d["pick"]["side"], "DAL")                                                      # the preferred price region ranks ahead of the larger nominal edge
        self.assertNotIsInstance(d["pick"], list)
        self.assertIn(d["pick"]["confidence"], ("HIGH", "MEDIUM", "LOW"))
        self.assertEqual(len(d["top_three"]), 2)

    def test_the_moneyline_wins_a_tie_with_the_puck_line(self):
        ml = cand(market="MONEYLINE", price=105, model=0.56, mkt=0.47, drivers=3)
        pl = cand(market="PUCK_LINE", line=-1.5, price=105, model=0.56, mkt=0.47, drivers=3)
        self.assertEqual(ep.decide([pl, ml], now=self.NOW)["pick"]["market"], "MONEYLINE")

    def test_a_side_the_evidence_opposes_is_not_the_most_researched_on_a_pass_day(self):
        opposed = cand(side="TBL", opponent="COL", price=105, model=0.40, mkt=0.47, drivers=0)
        neutral = cand(side="COL", opponent="TBL", price=-125, model=0.52, mkt=0.53, trust="INSUFFICIENT_EVIDENCE", drivers=1)
        d = ep.decide([opposed, neutral], now=self.NOW)
        self.assertEqual(d["decision"], "PASS")
        self.assertEqual(d["top_three"][0]["side"], "COL")

    def test_no_current_price_is_a_pass_with_that_reason(self):
        d = ep.decide([], now=self.NOW, why_no_prices="no current league pull")
        self.assertEqual((d["decision"], d["pass_reason"]), ("PASS", "no current league pull"))

    def test_confidence_is_an_evidence_category_not_a_percentage(self):
        hi = cand(model=0.58, mkt=0.46, drivers=3)
        self.assertEqual(ep.decide([hi], now=self.NOW)["pick"]["confidence"], "HIGH")
        cq = cand(price=-115, model=0.55, mkt=0.52, trust="INSUFFICIENT_EVIDENCE", drivers=3)
        self.assertIn(ep.confidence(cq), ("LOW", "MEDIUM"))
        self.assertEqual(ep.confidence(cq, {"CONSENSUS_QUALITY": {"decision_state": "DISAGREEMENT_HAS_SIGNAL"}}), "MEDIUM" if ep.confidence(cq) == "LOW" else "HIGH")


def game_db():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE games (game_id INTEGER, game_date TEXT, scheduled_start_utc TEXT, home_team TEXT, away_team TEXT, game_state TEXT, home_score INTEGER, away_score INTEGER, final_period_type TEXT)")
    return c


class TestTheRecordAndPostmortem(unittest.TestCase):
    def pick_doc(self, **kw):
        c = cand(model=0.56, mkt=0.47, drivers=3, **kw)
        return ep.decide([c], now=dt.datetime(2026, 10, 26, 21, 40, tzinfo=Z))

    def test_the_decision_quality_is_graded_from_the_decision_and_never_from_the_result(self):
        self.assertEqual(list(inspect.signature(epl.grade).parameters), ["decision_doc"])
        d = self.pick_doc()
        self.assertEqual(epl.grade(d)["label"], "GOOD_DECISION")
        shoulder = ep.decide([cand(price=-150, model=0.70, mkt=0.58, drivers=3)], now=dt.datetime(2026, 10, 26, 21, 40, tzinfo=Z))
        self.assertEqual(epl.grade(shoulder)["label"], "QUESTIONABLE_DECISION")
        bad = json.loads(json.dumps(d, default=str))
        bad["pick"]["price_freshness"] = {"status": "STALE"}
        self.assertEqual(epl.grade(bad)["label"], "BAD_DECISION")
        self.assertEqual(epl.grade(ep.decide([], now=dt.datetime(2026, 10, 26, tzinfo=Z), why_no_prices="x"))["label"], "NO_BET")

    def test_all_six_pairings_are_possible_and_a_bad_decision_can_win(self):
        with tempfile.TemporaryDirectory() as dd, mock.patch.dict("os.environ", {"NHL_ENGINE_STATE_DIR": dd}):
            grid = {}
            for label, doc in (("GOOD_DECISION", self.pick_doc()), ("QUESTIONABLE_DECISION", ep.decide([cand(price=-150, model=0.70, mkt=0.58, drivers=3)], now=dt.datetime(2026, 10, 26, 21, 40, tzinfo=Z)))):
                for res in ("WIN", "LOSS"):
                    ev = [{"type": "DECISION", "date": "2026-10-26", "decision_doc": doc}, {"type": "OUTCOME", "kind": "PICK", "date": "2026-10-26", "result": res, "home_score": 3, "away_score": 2}]
                    r = epl.record(ev)
                    grid[(r["decision_quality_vs_result"][0]["decision"], r["decision_quality_vs_result"][0]["result"])] = 1
            self.assertEqual(set(grid), {("GOOD_DECISION", "WIN"), ("GOOD_DECISION", "LOSS"), ("QUESTIONABLE_DECISION", "WIN"), ("QUESTIONABLE_DECISION", "LOSS")})
            bad = json.loads(json.dumps(self.pick_doc(), default=str))
            bad["pick"]["price_freshness"] = {"status": "STALE"}
            r = epl.record([{"type": "DECISION", "date": "2026-10-26", "decision_doc": bad}, {"type": "OUTCOME", "kind": "PICK", "date": "2026-10-26", "result": "WIN", "home_score": 3, "away_score": 1}])
            self.assertEqual((r["decision_quality_vs_result"][0]["decision"], r["decision_quality_vs_result"][0]["result"]), ("BAD_DECISION", "WIN"))

    def test_settlement_by_market_and_the_puck_line_margin(self):
        db = game_db()
        db.execute("INSERT INTO games VALUES (1, '2026-10-26', '2026-10-26T23:00:00Z', 'DAL', 'WPG', 'FINAL', 3, 2, 'REG')")
        db.execute("INSERT INTO games VALUES (2, '2026-10-26', '2026-10-26T23:00:00Z', 'FLA', 'CHI', 'FINAL', 4, 1, 'REG')")
        db.execute("INSERT INTO games VALUES (3, '2026-10-26', '2026-10-26T23:00:00Z', 'NYR', 'LAK', 'FINAL', 3, 2, 'SO')")
        cases_ = (("DAL", "MONEYLINE", None, 1, "WIN"), ("WPG", "MONEYLINE", None, 1, "LOSS"), ("DAL", "PUCK_LINE", -1.5, 1, "LOSS"), ("WPG", "PUCK_LINE", 1.5, 1, "WIN"), ("FLA", "PUCK_LINE", -1.5, 2, "WIN"),
                  ("CHI", "PUCK_LINE", 1.5, 2, "LOSS"), ("NYR", "PUCK_LINE", -1.5, 3, "LOSS"), ("LAK", "PUCK_LINE", 1.5, 3, "WIN"))
        for side, mk, line, gid, want in cases_:
            with tempfile.TemporaryDirectory() as dd, mock.patch.dict("os.environ", {"NHL_ENGINE_STATE_DIR": dd}):
                epl._append([{"type": "DECISION", "date": "2026-10-26", "decision_doc": {"decision": "PICK", "pick": {"side": side, "market": mk, "line": line, "game_id": str(gid)}}}])
                epl.settle(db, dt.datetime(2026, 10, 27, tzinfo=Z))
                out = [e for e in epl.read() if e["type"] == "OUTCOME" and e["kind"] == "PICK"]
                self.assertEqual(out[0]["result"], want, (side, mk, line))

    def test_signals_accumulate_and_only_drive_a_decision_after_one_hundred_independent_games(self):
        def obs(i, win):
            side = {"side": "AAA", "market": "MONEYLINE", "line": None, "price": 100, "market_p": 0.5, "model_p": 0.56, "gap": 0.06, "supported_p": 0.5, "edge": -0.02, "conservative_edge": -0.03, "structure": "NONE",
                    "qualifies": False, "price_class": "IN_PREFERRED_REGION", "confidence": None, "moved": 0.0, "rest_advantage": False, "trust_state": "INSUFFICIENT_EVIDENCE", "calibration": "HIGH"}
            return {"type": "OBSERVATION", "date": f"d{i}", "game_id": str(i), "home": "AAA", "away": "BBB", "sides": [side, {**side, "side": "BBB", "model_p": 0.44, "gap": -0.06}]}, \
                   {"type": "OUTCOME", "kind": "OBSERVATION", "date": f"d{i}", "game_id": str(i), "home_score": 3 if win else 1, "away_score": 1 if win else 3, "home_margin": 2 if win else -2}
        few = [e for i in range(20) for e in obs(i, True)]
        s = epl.signal_trust(few)["ML_MODEL_GAP_3PLUS"]
        self.assertEqual((s["games"], s["state"], s["decision_state"]), (20, "ACCUMULATING", "INSUFFICIENT_EVIDENCE"))
        many = [e for i in range(150) for e in obs(i, i % 10 != 0)]                                  # the flagged side wins 90% against a 50% market
        s = epl.signal_trust(many)["ML_MODEL_GAP_3PLUS"]
        self.assertEqual((s["games"], s["state"], s["decision_state"]), (150, "HAS_SIGNAL", "DISAGREEMENT_HAS_SIGNAL"))
        bad = [e for i in range(150) for e in obs(i, i % 10 == 0)]
        self.assertEqual(epl.signal_trust(bad)["ML_MODEL_GAP_3PLUS"]["state"], "FAILS")
        self.assertEqual(epl.puck_line_trust(few)["state"], "INSUFFICIENT_EVIDENCE")

    def test_the_observation_stores_every_side_with_its_structure_price_movement_and_confidence_for_prospective_learning(self):
        a = cand(model=0.56, mkt=0.47, drivers=3, moved=0.02)
        b = cand(side="WPG", opponent="DAL", price=-125, model=0.44, mkt=0.53, drivers=0)
        o = epl.observation([a, b], {"date": "2026-10-26", "game_id": "1", "home": "DAL", "away": "WPG", "start_utc": "x"}, "2026-10-26T21:40:00Z")
        self.assertEqual(len(o["sides"]), 2)
        s0 = o["sides"][0]
        for k in ("structure", "qualifies", "price_class", "confidence", "moved", "gap", "supported_p", "market", "calibration"):
            self.assertIn(k, s0)
        self.assertEqual((s0["structure"], s0["qualifies"], s0["confidence"] is not None), ("SUPPORTED_MARKET_GAP", True, True))

    def test_every_evidence_structure_price_range_market_and_confidence_category_is_tracked_as_a_signal(self):
        for k in ("SUPPORTED_MARKET_GAP", "CONSENSUS_QUALITY", "MARKET_SITUATIONAL_OPPORTUNITY", "PRICE_IN_PREFERRED", "PRICE_OUTSIDE_PREFERRED", "QUALIFYING_MONEYLINE", "QUALIFYING_PUCK_LINE", "ML_MODEL_GAP_3PLUS",
                  "PL_MODEL_GAP_3PLUS", "ML_MOVED_TOWARD_1PT", "ML_MOVED_AWAY_1PT", "REST_ADVANTAGE", "CONFIDENCE_HIGH", "CONFIDENCE_MEDIUM", "CONFIDENCE_LOW"):
            self.assertIn(k, epl.SIGNALS)
        a = cand(model=0.56, mkt=0.47, drivers=3)
        a["confidence"] = "HIGH"
        o = epl.observation([a], {"date": "d", "game_id": "1", "home": "DAL", "away": "WPG"}, "t")
        names = {n for n, _, _ in epl.signal_bets({**o, "home_margin": 2})}
        self.assertTrue({"SUPPORTED_MARKET_GAP", "PRICE_IN_PREFERRED", "QUALIFYING_MONEYLINE", "ML_MODEL_GAP_3PLUS"} <= names, names)

    def test_the_record_is_its_own_file_and_never_reads_the_learning_ledger(self):
        src = inspect.getsource(epl)
        self.assertNotIn("learning_cohort.db", src)
        self.assertNotIn("parlays", src.replace("learning-parlay", ""))
        self.assertEqual(epl.LEDGER, "eggy_pick_ledger.jsonl")


class TestTheDailyCycle(unittest.TestCase):
    TODAY = "2026-10-26"

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        p = mock.patch.dict("os.environ", {"NHL_ENGINE_STATE_DIR": self.dir.name})
        p.start()
        self.addCleanup(p.stop)
        self.db = game_db()
        self.db.execute("INSERT INTO games VALUES (1, ?, '2026-10-26T23:00:00Z', 'DAL', 'WPG', 'SCHEDULED', NULL, NULL, NULL)", (self.TODAY,))
        self.first = dt.datetime(2026, 10, 26, 23, 0, tzinfo=Z)

    def capture(self, at):
        ev = {"event_id": "e1", "home": "DAL", "away": "WPG", "commence": "2026-10-26T23:00:00Z", "ml": {"DAL": -105, "WPG": -105}, "pl": {"DAL": (190, -1.5), "WPG": (-230, 1.5)}}
        return {"at": at, "events": {"e1": ev}}

    def run_at(self, now, captures):
        view = {"available": True, "p_home": 0.52, "p_away": 0.48, "favourite": "DAL", "p_favourite": 0.52, "gap": "SMALL", "drivers": [], "indicators": {"DAL": {"rest_days": 2}, "WPG": {"rest_days": 2}}}
        card = {"market_trust": {"disagreement_state": "INSUFFICIENT_EVIDENCE"}, "calibration_confidence": {"level": "HIGH"}, "data_completeness": {"fraction": 0.7}, "contradiction": {"flags": []}, "thesis": {"type": "FAIR"},
                "case_for": [], "case_against": []}
        with mock.patch.object(ep, "league_captures", return_value=captures), mock.patch.object(ei, "build_context", return_value=SimpleNamespace(moneyline_trust={})), mock.patch.object(ei, "usable", return_value=(True, "")), \
                mock.patch.object(ei, "team_view", return_value=view), mock.patch.object(ei, "review_moneyline", return_value=card), mock.patch.object(lc, "_capture_context", return_value={"by_game": {"1": self.first}, "first_start": self.first, "funded": False}):
            return epl.run_cycle(self.db, now)

    def test_it_waits_for_the_pregame_pull_decides_once_and_never_again(self):
        early = dt.datetime(2026, 10, 26, 14, 0, tzinfo=Z)
        self.assertEqual(self.run_at(early, [self.capture(dt.datetime(2026, 10, 26, 12, 0, tzinfo=Z))])["status"], "WAITING_FOR_THE_PREGAME_PULL")           # only the morning pull exists
        now = dt.datetime(2026, 10, 26, 21, 40, tzinfo=Z)
        caps = [self.capture(dt.datetime(2026, 10, 26, 12, 0, tzinfo=Z)), self.capture(dt.datetime(2026, 10, 26, 21, 35, tzinfo=Z))]
        self.assertEqual(self.run_at(now, caps)["status"], "PASS")
        events = epl.read()
        self.assertEqual([e["type"] for e in events].count("DECISION"), 1)
        self.assertEqual([e["type"] for e in events].count("OBSERVATION"), 1)                          # every game is observed for the prospective record
        self.assertEqual(self.run_at(now + dt.timedelta(minutes=10), caps)["status"], "DECIDED")      # one decision a day
        self.assertEqual([e["type"] for e in epl.read()].count("DECISION"), 1)

    def test_with_no_current_pull_before_the_first_game_it_passes_for_want_of_prices(self):
        late = dt.datetime(2026, 10, 26, 22, 50, tzinfo=Z)                                           # past the last chance, only the morning pull exists
        out = self.run_at(late, [self.capture(dt.datetime(2026, 10, 26, 12, 0, tzinfo=Z))])
        self.assertEqual(out["status"], "PASS_NO_PRICES")
        d = [e for e in epl.read() if e["type"] == "DECISION"][0]["decision_doc"]
        self.assertEqual(d["decision"], "PASS")
        self.assertIn("no current league pull", d["pass_reason"])

    def test_the_snapshot_section_carries_the_decision_the_record_the_signals_and_what_is_not_available(self):
        self.run_at(dt.datetime(2026, 10, 26, 21, 40, tzinfo=Z), [self.capture(dt.datetime(2026, 10, 26, 21, 35, tzinfo=Z))])
        s = epl.snapshot_section(self.TODAY)
        self.assertEqual(s["today"]["decision"], "PASS")
        self.assertEqual(s["policy"]["policy_id"], "EGGY_SIDE_PICK_V2")
        self.assertEqual(s["observed_games"], 1)
        self.assertIn("ticket percentages", s["availability"]["NOT AVAILABLE"])
        self.assertIn("handle / money percentages", s["availability"]["NOT AVAILABLE"])
        self.assertEqual(set(s["signals"]), set(epl.SIGNALS))


if __name__ == "__main__":
    unittest.main()
