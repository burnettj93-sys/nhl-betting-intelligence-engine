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


def cand(side="DAL", market="MONEYLINE", price=105.0, model=0.52, mkt=0.47, trust="DISAGREEMENT_HAS_SIGNAL", line=None, fresh=FRESH, calib="HIGH", comp=0.9, moved=None, drivers=2, rest=None, opponent="WPG"):
    c = {"game_id": "1", "matchup": f"{opponent} @ {side}", "side": side, "opponent": opponent, "home": side, "away": opponent, "start_utc": "2026-10-26T23:00:00Z", "capture_at_utc": "2026-10-26T21:40:00Z",
         "price_freshness": fresh, "model_win_probability": model, "market_win_probability": mkt, "calibration": calib, "data_completeness": comp, "flags": [], "card_thesis": "MODEL_SEES_VALUE", "market": market, "line": line, "price": price,
         "implied_with_margin": round(ei.implied_from_american(price), 4), "market_probability_market": mkt, "model_probability": model, "gap": round(model - mkt, 4), "trust_state": trust, "movement_toward_side": moved,
         "rest_days": rest or {side: 2, opponent: 2}, "hockey_view": {"favourite": side, "p_favourite": 0.55, "gap": "CLEAR", "drivers": [{"label": f"driver {i}", "favours": side, "log_odds_toward_home": 0.2} for i in range(drivers)]},
         "card": {"case_for": [], "case_against": []}}
    ep._qualify(c)
    return c


class TestThePolicy(unittest.TestCase):
    def test_only_a_moneyline_or_a_puck_line_one_a_day_and_never_a_prop_or_a_parlay(self):
        self.assertEqual(ep.POLICY["markets"], ["MONEYLINE", "PUCK_LINE"])
        self.assertEqual(ep.POLICY["max_outputs_per_day"], 1)
        for banned in ("player props", "player points", "shots", "parlays"):
            self.assertIn(banned, ep.POLICY["no"])
        self.assertEqual(ep.POLICY["price_policy"]["preferred_region_american"], [-130, 130])

    def test_the_eggy_digest_covers_the_decision_policy_and_the_puck_line_calibration_and_moved(self):
        self.assertEqual(ec.OFFICIAL_POLICY["policy_id"], "EGGY_SIDE_PICK_V1")
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


class TestThePriceAndTheMarket(unittest.TestCase):
    def test_price_regions(self):
        for price, want in ((-130, "IN_REGION"), (130, "IN_REGION"), (-105, "IN_REGION"), (-150, "SHOULDER"), (160, "SHOULDER"), (-190, "OUTSIDE"), (210, "OUTSIDE")):
            self.assertEqual(ep.price_class(price), want, price)
        self.assertEqual(ep.required_edge("IN_REGION"), ei.EDGE_MIN)
        self.assertEqual(ep.required_edge("SHOULDER"), 2 * ei.EDGE_MIN)
        self.assertIsNone(ep.required_edge("OUTSIDE"))

    def test_the_no_vig_probability_removes_the_margin(self):
        self.assertAlmostEqual(ep._no_vig(-110, -110), 0.5, places=6)
        self.assertAlmostEqual(ep._no_vig(-135, 114) + ep._no_vig(114, -135), 1.0, places=6)

    def test_the_puck_line_probability_comes_from_the_validated_margin_calibration(self):
        cal = ep.calibration()
        self.assertIn("out_of_time_2025", cal)
        self.assertGreater(cal["out_of_time_2025"]["rows_test"] if "rows_test" in cal["out_of_time_2025"] else cal["rows_test"], 1000)
        fav, dog = ep.puck_line_cover(0.60, -1.5), ep.puck_line_cover(0.40, 1.5)
        self.assertLess(fav, 0.60)                                                                    # winning by two is harder than winning
        self.assertGreater(dog, 0.40)                                                                 # +1.5 covers more than the underdog wins
        self.assertGreater(ep.puck_line_cover(0.65, -1.5), ep.puck_line_cover(0.55, -1.5))

    def test_league_captures_reads_prices_up_to_now_and_names_the_teams(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            ev = {"id": "e1", "home_team": "New York Rangers", "away_team": "Los Angeles Kings", "commence_time": "2026-10-26T23:10:00Z",
                  "bookmakers": [{"key": "draftkings", "markets": [{"key": "h2h", "outcomes": [{"name": "Los Angeles Kings", "price": 114}, {"name": "New York Rangers", "price": -135}]},
                                                                  {"key": "spreads", "outcomes": [{"name": "Los Angeles Kings", "price": -230, "point": 1.5}, {"name": "New York Rangers", "price": 190, "point": -1.5}]}]}]}
            for stamp in ("20261026T120005Z", "20261026T213900Z"):
                (base / f"{stamp}_sports-icehockey_nhl-odds_na_h2h+spreads.json").write_text(json.dumps({"meta": {"retrieved_at_utc": stamp[:4] + "-" + stamp[4:6] + "-" + stamp[6:8] + "T" + stamp[9:11] + ":" + stamp[11:13] + ":" + stamp[13:15] + "Z"}, "response": [ev]}))
            caps = ep.league_captures("2026-10-26", dt.datetime(2026, 10, 26, 21, 0, tzinfo=Z), base)
            self.assertEqual(len(caps), 1)                                                            # the 21:39 pull had not happened yet
            e = caps[0]["events"]["e1"]
            self.assertEqual((e["home"], e["away"]), ("NYR", "LAK"))
            self.assertEqual(e["ml"], {"NYR": -135, "LAK": 114})
            self.assertEqual(e["pl"]["NYR"], (190, -1.5))


class TestTheStandard(unittest.TestCase):
    def test_a_proven_disagreement_at_a_fresh_price_in_the_region_qualifies(self):
        c = cand(price=105, model=0.55, mkt=0.47, trust="DISAGREEMENT_HAS_SIGNAL")
        self.assertEqual(c["probability_basis"].split(" (")[0], "HOCKEY MODEL")
        self.assertTrue(c["qualifies_before_the_case"], c["not_qualifying_because"])
        self.assertGreaterEqual(c["edge"], ei.EDGE_MIN)

    def test_an_unproven_model_gap_is_never_trusted_so_there_is_no_edge_to_claim(self):
        c = cand(price=105, model=0.60, mkt=0.47, trust="INSUFFICIENT_EVIDENCE")
        self.assertEqual(c["supported_probability"], c["market_probability_market"])
        self.assertLess(c["edge"], 0)                                                                 # the market's own probability cannot beat the bookmaker's margin
        self.assertFalse(c["qualifies_before_the_case"])
        self.assertTrue(any("no supported edge" in w and "unproven" in w for w in c["not_qualifying_because"]), c["not_qualifying_because"])

    def test_a_side_the_model_likes_less_never_borrows_the_models_probability(self):
        c = cand(price=105, model=0.40, mkt=0.47, trust="DISAGREEMENT_HAS_SIGNAL")
        self.assertEqual(c["supported_probability"], c["market_probability_market"])

    def test_a_stale_price_is_a_market_data_reason_and_blocks_the_pick(self):
        c = cand(fresh={"status": "STALE", "reason": "442 minutes old", "category": "MARKET_DATA_QUALITY"}, model=0.55, mkt=0.47)
        self.assertFalse(c["qualifies_before_the_case"])
        self.assertTrue(any(w.startswith("MARKET DATA QUALITY") for w in c["not_qualifying_because"]))
        cs = ep.cases(c)
        self.assertTrue(any(x["kind"] == "STALE_PRICE" and x["strength"] == "STRONG" for x in cs["case_against"]))

    def test_a_short_favourite_is_not_what_eggy_hunts_and_a_shoulder_needs_twice_the_edge(self):
        self.assertFalse(cand(price=-300, model=0.80, mkt=0.74)["qualifies_before_the_case"])
        shoulder = cand(price=-150, model=0.62, mkt=0.58)                                             # a proven 4-point edge: enough in the region, not on the shoulder
        self.assertEqual(shoulder["price_class"], "SHOULDER")
        self.assertFalse(shoulder["qualifies_before_the_case"])
        self.assertTrue(cand(price=-150, model=0.68, mkt=0.58)["qualifies_before_the_case"])

    def test_weak_data_or_calibration_blocks_it(self):
        self.assertFalse(cand(model=0.56, mkt=0.47, calib="LOW")["qualifies_before_the_case"])
        self.assertFalse(cand(model=0.56, mkt=0.47, comp=0.4)["qualifies_before_the_case"])


class TestTheCaseForAndAgainst(unittest.TestCase):
    def test_a_proven_edge_with_hockey_support_outweighs_the_case_against(self):
        c = cand(model=0.56, mkt=0.47, drivers=3)
        cs = ep.cases(c)
        self.assertTrue(any(x["kind"] == "MODEL_EDGE" and x["strength"] == "STRONG" for x in cs["case_for"]))
        self.assertTrue(ep.balance(cs["case_for"], cs["case_against"])["materially_outweighs"])

    def test_an_unproven_gap_is_shown_but_not_counted_and_it_is_itself_a_reason_against(self):
        cs = ep.cases(cand(model=0.56, mkt=0.47, trust="INSUFFICIENT_EVIDENCE"))
        self.assertTrue(any(x["kind"] == "MODEL_EDGE" and x["strength"] == "UNPROVEN" for x in cs["case_for"]))
        self.assertTrue(any(x["kind"] == "UNPROVEN_EDGE" for x in cs["case_against"]))
        self.assertFalse(ep.balance(cs["case_for"], cs["case_against"])["materially_outweighs"])

    def test_a_strong_reason_against_blocks_a_pick(self):
        self.assertEqual(ep.balance([{"strength": "STRONG"}, {"strength": "MODERATE"}, {"strength": "MODERATE"}], [{"strength": "STRONG"}])["materially_outweighs"], False)
        self.assertEqual(ep.balance([{"strength": "STRONG"}, {"strength": "MODERATE"}], [{"strength": "WEAK"}])["materially_outweighs"], True)
        self.assertEqual(ep.balance([{"strength": "MODERATE"}, {"strength": "MODERATE"}], [])["materially_outweighs"], False)       # nothing strong in favour

    def test_line_movement_is_listed_as_unproven_not_as_evidence(self):
        cs = ep.cases(cand(moved=0.02))
        mv = [x for x in cs["case_for"] if x["kind"] == "LINE_MOVEMENT"]
        self.assertEqual(mv[0]["strength"], "UNPROVEN")
        self.assertIn("has not yet been shown to predict anything", mv[0]["text"])

    def test_the_goalie_and_the_margin_are_stated_as_limits(self):
        txt = " ".join(x["text"] for x in ep.cases(cand())["case_against"])
        self.assertIn("not confirmed by an approved source", txt)
        self.assertIn("bookmaker's margin", txt)


class TestTheDecision(unittest.TestCase):
    NOW = dt.datetime(2026, 10, 26, 21, 40, tzinfo=Z)

    def test_pass_is_the_answer_without_a_supported_edge_and_says_why(self):
        d = ep.decide([cand(model=0.60, mkt=0.47, trust="INSUFFICIENT_EVIDENCE"), cand(side="WPG", opponent="DAL", price=-125, model=0.5, mkt=0.53, trust="INSUFFICIENT_EVIDENCE")], now=self.NOW)
        self.assertEqual(d["decision"], "PASS")
        self.assertIn("no side meets the standard", d["pass_reason"])
        self.assertIn("unproven", d["pass_reason"])
        self.assertIn("what_would_change_it", d)
        self.assertNotIn("pick", d)

    def test_exactly_one_pick_the_best_supported_edge_never_a_list(self):
        a, b = cand(side="DAL", model=0.55, mkt=0.47, price=105), cand(side="FLA", opponent="CHI", price=-110, model=0.60, mkt=0.50)
        d = ep.decide([a, b], now=self.NOW)
        self.assertEqual(d["decision"], "PICK")
        self.assertEqual(d["pick"]["side"], "FLA")                                                    # the larger supported edge
        self.assertNotIsInstance(d["pick"], list)
        self.assertIn(d["pick"]["confidence"], ("HIGH", "MEDIUM", "LOW"))

    def test_the_moneyline_wins_a_tie_with_the_puck_line_and_a_payout_is_never_a_reason(self):
        ml = cand(market="MONEYLINE", price=105, model=0.56, mkt=0.47)
        pl = cand(market="PUCK_LINE", line=-1.5, price=105, model=0.56, mkt=0.47)
        self.assertEqual(ep.decide([pl, ml], now=self.NOW)["pick"]["market"], "MONEYLINE")
        bigger_payout_smaller_edge = cand(market="PUCK_LINE", line=-1.5, price=130, model=0.50, mkt=0.43)
        self.assertEqual(ep.decide([ml, bigger_payout_smaller_edge], now=self.NOW)["pick"]["market"], "MONEYLINE")

    def test_no_current_price_is_a_pass_with_that_reason(self):
        d = ep.decide([], now=self.NOW, why_no_prices="no current league pull")
        self.assertEqual((d["decision"], d["pass_reason"]), ("PASS", "no current league pull"))

    def test_confidence_is_an_evidence_category_not_a_percentage(self):
        hi = cand(model=0.58, mkt=0.46, drivers=3)
        d = ep.decide([hi], now=self.NOW)
        self.assertEqual(d["pick"]["confidence"], "HIGH")
        low = cand(model=0.505, mkt=0.47, drivers=3, trust="DISAGREEMENT_HAS_SIGNAL", calib="MEDIUM")
        self.assertIn(ep.confidence(low, {"against_moderate": 0}), ("MEDIUM", "LOW"))
        self.assertEqual(ep.confidence(cand(trust="INSUFFICIENT_EVIDENCE"), {"against_moderate": 0}), "LOW")


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
            return {"type": "OBSERVATION", "date": f"d{i}", "game_id": str(i), "home": "AAA", "away": "BBB", "ml": {"AAA": {"market": 0.5, "model": 0.56, "moved": 0.0}, "BBB": {"market": 0.5, "model": 0.44, "moved": 0.0}}, "pl": {"AAA": {}, "BBB": {}}, "rest_days": {"AAA": 2, "BBB": 2}}, \
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
        self.assertEqual(s["policy"]["policy_id"], "EGGY_SIDE_PICK_V1")
        self.assertEqual(s["observed_games"], 1)
        self.assertIn("ticket percentages", s["availability"]["NOT AVAILABLE"])
        self.assertIn("handle / money percentages", s["availability"]["NOT AVAILABLE"])
        self.assertEqual(set(s["signals"]), set(epl.SIGNALS))


if __name__ == "__main__":
    unittest.main()
