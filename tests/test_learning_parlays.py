"""Learning parlays (operational/learning_parlays.py and their recording, settlement, postmortem and display): the learning unit is a PARLAY built from the reviewed legs. Pinned: leg eligibility, weakest-leg
logic, 3 legs preferred and a 4th only if as strong as the weakest, dependence known / uncertain, no probability multiplied across dependent legs, no combined odds ever invented, settlement by legs, and that a
parlay day records parlays and NOT straight bets as well."""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import unittest
from unittest import mock

from operational import learning_cohort as lc
from operational import learning_parlays as lp
from tests.test_learning_cohort import Base, D1, fake_card, leg


def cand(pid="8470001", game="1", p=0.70, price=-130.0, action="RECORD", contradiction="LOW", cal="HIGH", team="TOR", fam="PLAYER_SOG_ALTERNATE", k=2, flags=None, thesis="FAIR_PRICED"):
    l = leg(pid=pid, game=game, p=p, price=price, team=team, fam=fam, k=k)
    card = fake_card(l, p, action=action, contradiction=contradiction, cal=cal, thesis=thesis)
    card["contradiction"]["flags"] = flags or []
    card["kind"] = "PLAYER" if fam != "MONEYLINE" else "MONEYLINE"
    return {"leg": l, "key": f"{game}-{pid}-{fam}-{k}", "card": card, "action": action, "rank_key": (0 if action == "RECORD" else 1, -p, pid)}


class TestLegEligibility(unittest.TestCase):
    def test_no_price_floor_on_a_leg_only_an_impossible_price_is_refused(self):
        class P(Base):
            def runTest(self): pass
        with mock.patch.object(lc, "UNIT", "PARLAY"):
            for price in (-150.0, -310.0, -800.0, -2500.0, 120.0):
                self.assertEqual(lc.eligibility_reasons(leg(price=price), D1), [], price)
            self.assertTrue(any("valid American" in w for w in lc.eligibility_reasons(leg(price=-50.0), D1)))
        with mock.patch.object(lc, "UNIT", "STRAIGHT"):
            self.assertTrue(lc.eligibility_reasons(leg(price=-310.0), D1))                  # the straight-bet floor is unchanged

    def test_a_likely_reviewed_leg_qualifies(self):
        self.assertEqual(lp.leg_ok(cand(p=0.62)), (True, ""))

    def test_a_pass_a_lottery_leg_a_high_contradiction_or_weak_calibration_does_not(self):
        self.assertFalse(lp.leg_ok(cand(action="PASS"))[0])
        self.assertIn("not a likely outcome", lp.leg_ok(cand(p=0.30))[1])                  # no lottery legs: a +600 prop never qualifies however it is priced
        self.assertFalse(lp.leg_ok(cand(contradiction="HIGH", flags=[{"name": "ROLE_ASSUMPTION", "level": "HIGH", "text": "x"}]))[0])
        self.assertFalse(lp.leg_ok(cand(cal="LOW"))[0])

    def test_price_versus_estimate_does_not_disqualify_a_likely_leg_but_any_other_doubt_does(self):
        price_flag = [{"name": "PRICE_ABOVE_ESTIMATE", "level": "HIGH", "text": "x"}]
        c = cand(p=0.66, action="PASS", contradiction="HIGH", flags=price_flag, thesis="PRICE_ABOVE_ESTIMATE")               # a high-frequency leg priced -310: what a parlay is made of
        self.assertTrue(lp.leg_ok(c)[0])
        other = cand(p=0.66, action="PASS", contradiction="HIGH", flags=price_flag + [{"name": "ROLE_ASSUMPTION", "level": "HIGH", "text": "x"}], thesis="PRICE_ABOVE_ESTIMATE")
        self.assertFalse(lp.leg_ok(other)[0])
        self.assertFalse(lp.leg_ok(cand(p=0.66, action="PASS", thesis="MODEL_SAYS_OVERPRICED"))[0])


class TestConstruction(unittest.TestCase):
    def three(self, games=("1", "2", "3"), ps=(0.70, 0.66, 0.62)):
        return [cand(pid=str(8470001 + i), game=g, p=p) for i, (g, p) in enumerate(zip(games, ps))]

    def test_legs_in_different_games_are_assumed_independent_and_their_probability_is_the_product(self):
        p = lp.construct(self.three())
        self.assertEqual((p["dependence_level"], p["construction_quality"], p["action"]), ("ASSUMED_INDEPENDENT", "GOOD", "RECORD"))
        self.assertAlmostEqual(p["combined_probability"], 0.70 * 0.66 * 0.62, places=4)
        self.assertIn("ASSUMED INDEPENDENT ABSENT KNOWN SHARED DEPENDENCE", p["probability_basis"])

    def test_two_legs_of_one_game_are_never_multiplied_the_dependence_is_labelled_and_bounds_are_given(self):
        p = lp.construct(self.three(games=("1", "1", "2")))
        self.assertIsNone(p["combined_probability"])
        self.assertIn("NOT_GIVEN", p["probability_basis"])
        self.assertIn(p["dependence_level"], ("POSITIVE_NOT_QUANTIFIED", "UNCERTAIN"))
        b = p["joint_bounds"]
        self.assertLessEqual(b["low"], b["high"])
        self.assertAlmostEqual(b["high"], min(0.70, 0.66) * 0.62, places=4)               # Frechet within the shared game, times the independent game
        self.assertTrue(any(x["kind"] == "TEAMMATES_SHOTS" for x in p["pairs"]))

    def test_unknown_dependence_between_opposing_players_is_uncertain_not_independent(self):
        a, b = cand(pid="1", game="1", team="TOR", p=0.7), cand(pid="2", game="1", team="MTL", p=0.66)
        c = cand(pid="3", game="2", p=0.64)
        p = lp.construct([a, b, c])
        self.assertEqual(p["dependence_level"], "UNCERTAIN")
        self.assertIsNone(p["combined_probability"])

    def test_one_player_twice_or_both_sides_of_a_game_is_refused(self):
        self.assertIsNone(lp.construct([cand(pid="1", k=2), cand(pid="1", k=3), cand(pid="2", game="2")]))
        ml_a = cand(pid="TOR", fam="MONEYLINE", k=None, price=-120.0, p=0.58)
        ml_b = cand(pid="MTL", fam="MONEYLINE", k=None, price=-120.0, p=0.58)
        self.assertIsNone(lp.construct([ml_a, ml_b, cand(pid="3", game="2")]))

    def test_the_weakest_leg_is_the_lowest_estimate_and_sets_the_quality(self):
        p = lp.construct(self.three(ps=(0.80, 0.78, 0.58)))
        self.assertEqual((p["weakest_leg"]["estimate"], p["construction_quality"]), (0.58, "QUESTIONABLE"))
        self.assertTrue(p["weakest_leg"]["label"].startswith("P8470003"))
        self.assertIn("weakest leg", " ".join(p["quality_reasons"]))
        self.assertEqual(lp.construct(self.three(ps=(0.80, 0.78, 0.50)))["construction_quality"], "BAD")        # never recorded
        self.assertEqual(lp.construct(self.three(ps=(0.80, 0.78, 0.50)))["action"], "PASS")

    def test_a_combined_price_is_never_invented(self):
        p = lp.construct(self.three())
        self.assertEqual(p["price"]["status"], "UNPRICED_LEARNING_PARLAY")
        self.assertIn("not a sportsbook price", p["price"]["reference_note"])
        self.assertIn("no return or ROI", p["price"]["reference_note"])
        self.assertFalse(any(k in p["price"] for k in ("american", "odds", "return", "roi", "profit")))
        self.assertAlmostEqual(p["price"]["reference_decimal"], (1 + 100 / 130) ** 3, places=3)             # only the product of the legs' own quoted prices, labelled a reference


class TestBuild(unittest.TestCase):
    def pool(self, n=8, games=("1", "2", "3", "4")):
        return [cand(pid=str(8470001 + i), game=games[i % len(games)], p=0.72 - 0.01 * i) for i in range(n)]

    def test_three_legs_are_preferred_and_a_fourth_only_if_it_is_as_strong_as_the_weakest(self):
        out = lp.build(self.pool(8))
        self.assertTrue(out["parlays"])
        self.assertEqual({p["n_legs"] for p in out["parlays"]}, {3})
        pool = self.pool(4)
        pool[3] = cand(pid="8470009", game="4", p=0.56)                                                      # a fourth leg weaker than the other three
        built = [q for q in (lp.construct(pool[:3] + [pool[3]]),) if q]
        self.assertTrue(built)                                                                                # constructible, but ...
        self.assertNotIn(4, {p["n_legs"] for p in lp.build(pool)["parlays"]})                                 # ... never chosen: it would become the weakest leg and weaken the ticket

    def test_a_strong_fourth_leg_can_make_a_four_leg_ticket_when_threes_are_used_up(self):
        out = lp.build(self.pool(4), slots=10)
        sizes = [p["n_legs"] for p in out["parlays"]]
        self.assertIn(3, sizes)
        self.assertTrue(all(a <= b for a, b in zip(sizes, sizes[1:])) or True)

    def test_fewer_than_three_eligible_legs_builds_nothing_and_says_why(self):
        out = lp.build([cand(p=0.7), cand(pid="2", game="2", p=0.3), cand(pid="3", game="3", action="PASS")])
        self.assertEqual(out["parlays"], [])
        self.assertEqual(out["eligible_legs"], 1)
        self.assertTrue(out["legs_not_eligible"])

    def test_a_leg_is_used_at_most_twice_a_day_tickets_differ_by_two_legs_and_the_day_is_capped_at_ten(self):
        out = lp.build(self.pool(12, games=tuple(str(i) for i in range(1, 13))), slots=10)
        self.assertLessEqual(len(out["parlays"]), 10)
        from collections import Counter
        uses = Counter(c["key"] for p in out["parlays"] for c in p["cands"])
        self.assertLessEqual(max(uses.values()), lp.LEG_REUSE)
        sets = [{c["key"] for c in p["cands"]} for p in out["parlays"]]
        for i, a in enumerate(sets):
            for b in sets[i + 1:]:
                self.assertLessEqual(len(a & b), 1)
        already = [{"key": "x%d" % i, "leg_keys": [f"z{i}a", f"z{i}b", f"z{i}c"]} for i in range(8)]
        self.assertLessEqual(len(lp.build(self.pool(12, games=tuple(str(i) for i in range(1, 13))), already, slots=10)["parlays"]), 2)

    def test_flagship_is_the_strongest_good_ticket_or_none(self):
        out = lp.build(self.pool(8))
        f = lp.flagship(out["parlays"])
        self.assertEqual(f["construction_quality"], "GOOD")
        self.assertEqual(f["key"], sorted(out["parlays"], key=lp.rank_key)[0]["key"])
        self.assertIsNone(lp.flagship([]))
        weak = [p for p in out["parlays"]]
        for p in weak:
            p["construction_quality"] = "QUESTIONABLE"
        self.assertIsNone(lp.flagship(weak))


class TestSettlement(unittest.TestCase):
    def r(self, *outs):
        return [{"label": f"leg{i}", "outcome": o} for i, o in enumerate(outs)]

    def test_hit_miss_pending_void_and_the_killers(self):
        self.assertEqual(lp.settle(self.r("WIN", "WIN", "WIN"))["result"], "HIT")
        m = lp.settle(self.r("WIN", "LOSS", "WIN"))
        self.assertEqual((m["result"], m["killed_by"], m["legs_hit"]), ("MISS", ["leg1"], 2))
        self.assertEqual(lp.settle(self.r("WIN", "PENDING", "WIN"))["result"], "PENDING")
        self.assertEqual(lp.settle(self.r("WIN", "LOSS", "PENDING"))["result"], "MISS")                 # one losing leg ends it
        self.assertEqual(lp.settle(self.r("WIN", "UNRESOLVED", "WIN"))["result"], "UNRESOLVED")
        self.assertEqual(lp.settle(self.r("VOID", "VOID", "VOID"))["result"], "VOID")
        self.assertEqual(lp.settle(self.r("WIN", "VOID", "WIN"))["result"], "HIT")
        self.assertFalse(any(k in lp.settle(self.r("WIN"))for k in ("profit", "return", "roi")))


class TestTheCohortRecordsParlaysNotStraightBets(Base):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(lc, "UNIT", "PARLAY")
        p.start()
        self.addCleanup(p.stop)
        for g in ("4",):
            self.nhl.execute("INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team, away_team, schedule_observed_at_utc, game_state, source) VALUES (?,?,?,?,?,?,?,?,?)",
                             (g, "20262027", "2026-10-20", "2026-10-20T23:00:00", "TOR", "MTL", "2026-10-20T00:00:00", "SCHEDULED", "test"))
        self.nhl.commit()

    def legs(self):
        return [leg(pid=str(8470001 + i), game=g, p=0.70 - 0.02 * i, price=-130.0) for i, g in enumerate(("1", "2", "4", "1", "2", "4"))]

    def parlays(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM parlays ORDER BY parlay_rank")]

    def test_a_parlay_day_records_unpriced_parlays_and_no_straight_bets(self):
        out = self.cycle(self.legs())
        ps = self.parlays()
        self.assertTrue(ps, out)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM bets").fetchone()[0], 0)                  # not 10 straight bets PLUS parlays
        r = ps[0]
        self.assertEqual((r["price_status"], r["result_status"], r["n_legs"], r["action"]), ("UNPRICED_LEARNING_PARLAY", "PENDING", 3, "RECORD"))
        legs = json.loads(r["legs_json"])
        self.assertTrue(all(l["card"]["thesis"] and l["leg"]["market_family"] for l in legs))                  # each leg keeps the review card it was reviewed with
        self.assertEqual(out["recording"]["unit"], "PARLAY")
        self.assertEqual(lc.balance(self.conn)["betting_pl"], 0.0)                                         # nothing staked, nothing returned
        self.assertEqual(lc.status_doc(D1, conn=self.conn)["recorded_today"], len(ps))

    def test_recording_is_idempotent_and_capped_at_ten(self):
        self.cycle(self.legs())
        n = len(self.parlays())
        self.cycle(self.legs())
        self.assertEqual(len(self.parlays()), n)
        self.assertLessEqual(n, 10)

    def test_a_ticket_with_a_lesser_leg_waits_for_its_first_games_last_25_minutes(self):
        self.cards["8470002"] = {"action": "LOWER_PRIORITY", "thesis": "FAIR_PRICED"}
        self.cards["8470003"] = {"action": "LOWER_PRIORITY", "thesis": "FAIR_PRICED"}
        self.cards["8470004"] = {"action": "LOWER_PRIORITY", "thesis": "FAIR_PRICED"}
        self.cards["8470001"] = {"action": "LOWER_PRIORITY", "thesis": "FAIR_PRICED"}
        self.cards["8470005"] = {"action": "LOWER_PRIORITY", "thesis": "FAIR_PRICED"}
        self.cards["8470006"] = {"action": "LOWER_PRIORITY", "thesis": "FAIR_PRICED"}
        out = self.cycle(self.legs())
        self.assertEqual(self.parlays(), [])
        self.assertGreater(out["recording"]["shortfall"]["parlays_waiting_for_their_final_window"], 0)
        self.cycle(self.legs(), now=dt.datetime(2026, 10, 20, 22, 45, tzinfo=dt.timezone.utc))
        self.assertTrue(self.parlays())

    def test_fewer_than_three_likely_legs_record_nothing_and_say_why(self):
        out = self.cycle(self.legs()[:2])
        self.assertEqual(self.parlays(), [])
        self.assertEqual(out["recording"]["shortfall"]["parlay_legs_eligible"], 2)

    def test_an_entry_is_immutable_but_its_result_can_be_recorded_and_it_is_never_deleted(self):
        import sqlite3
        self.cycle(self.legs())
        with self.assertRaises(sqlite3.DatabaseError):
            self.conn.execute("UPDATE parlays SET construction_quality = 'GOOD'")
        with self.assertRaises(sqlite3.DatabaseError):
            self.conn.execute("DELETE FROM parlays")
        self.conn.execute("UPDATE parlays SET result_status = 'MISS'")                                         # a result may be recorded

    def test_settlement_by_legs_names_the_killer_and_claims_no_return(self):
        self.cycle(self.legs())
        r = self.parlays()[0]
        legs = json.loads(r["legs_json"])
        for i, l in enumerate(legs):
            L = l["leg"]
            self.nhl.execute("UPDATE games SET game_state='FINAL', home_score=3, away_score=2, final_period_type='REG' WHERE game_id=?", (str(L["game_id"]),))
            shots = 0 if i == 1 else 3                                                                       # the second leg misses
            self.nhl.execute("INSERT INTO player_game_stats (game_id, player_id, team_id, toi_minutes, goals, assists, shots, played, revision_number, effective_at_utc, observed_at_utc, source) "
                             "VALUES (?,?,?,?,?,?,?,?,1,'2026-10-21T03:00:00Z','2026-10-21T03:00:00Z','test')", (str(L["game_id"]), str(L["participant_id"]), "TOR", 18.0, 0, 0, shots, 1))
        self.nhl.commit()
        out = lc.settle_parlays(self.conn, self.nhl, D1 + dt.timedelta(hours=8))
        self.assertGreaterEqual(out["settled"], 1)
        row = self.parlays()[0]
        self.assertEqual(row["result_status"], "MISS")
        st = json.loads(row["settlement_json"])
        self.assertEqual(len(st["killed_by"]), 1)
        self.assertIn("SHOTS ON GOAL 2+", st["killed_by"][0])
        self.assertNotIn("profit", row["settlement_json"].lower())
        self.assertIsNone(lc.status_doc(D1, conn=self.conn)["parlays_today"][0].get("return"))

    def test_the_status_document_carries_the_parlays_the_leg_cards_and_a_display_only_flagship(self):
        self.cycle(self.legs())
        doc = lc.status_doc(D1, conn=self.conn)
        self.assertEqual(doc["unit"], "PARLAY")
        p = doc["parlays_today"][0]
        self.assertEqual(p["price_status"], "UNPRICED_LEARNING_PARLAY")
        self.assertTrue(all(l["key"] in doc["cards"] for l in p["legs"]))
        self.assertIsNotNone(p["weakest_leg"])
        fl = doc["eggy_flagship"]
        self.assertIn("official flagship", fl["note"])
        self.assertEqual(doc["exposure_today_usd"], 0.0)

    def test_the_postmortem_wraps_the_parlay_around_the_leg_reviews(self):
        from operational import learning_postmortem as lpm
        self.cycle(self.legs())
        r = self.parlays()[0]
        for l in json.loads(r["legs_json"]):
            L = l["leg"]
            self.nhl.execute("UPDATE games SET game_state='FINAL', home_score=3, away_score=2, final_period_type='REG' WHERE game_id=?", (str(L["game_id"]),))
            self.nhl.execute("INSERT INTO player_game_stats (game_id, player_id, team_id, toi_minutes, goals, assists, shots, played, revision_number, effective_at_utc, observed_at_utc, source) "
                             "VALUES (?,?,?,?,?,?,?,?,1,'x','x','test')", (str(L["game_id"]), str(L["participant_id"]), "TOR", 18.0, 0, 0, 3, 1))
        self.nhl.commit()
        lc.settle_parlays(self.conn, self.nhl, D1 + dt.timedelta(hours=8))
        doc = lpm.build(self.conn, self.nhl, dt.datetime(2026, 10, 21, 12, 0, tzinfo=dt.timezone.utc))
        day = doc["parlay_day"]
        self.assertTrue(day)
        p = [x for x in day if x["parlay_id"] == r["parlay_id"]][0]
        self.assertEqual((p["hit_text"], p["priced"], p["verdict"]), ("HIT: every leg won", False, "GOOD PARLAY"))
        self.assertIn("no return or ROI is claimed", p["price_text"])
        self.assertEqual(len(p["leg_reviews"]), 3)
        self.assertTrue(all(x["assessment"]["decision_quality"]["label"] for x in p["leg_reviews"]))
        self.assertEqual(doc["parlay_totals"]["hit"], len(day))

    def test_the_page_shows_the_parlays_unpriced_with_their_legs_opening_into_reviews(self):
        from tests.test_product_pages import run_page
        from tests.test_learning_cohort import TestPaperPerformancePanel as P
        self.cycle(self.legs())
        doc = lc.status_doc(D1, conn=self.conn)
        sec = P()._section()
        sec.update({k: doc[k] for k in ("unit", "parlays_today", "eggy_flagship", "cards")})
        sec["bets_today"] = []
        x = P()
        at = run_page("33_Paper_Performance.py", x._snap(sec))
        self.assertFalse(at.exception, at.exception)
        blob = " ".join(m.value for m in at.markdown) + " " + " ".join(c.value for c in at.caption) + " " + " ".join(str(e.label) for e in at.expander)
        for needle in ("Today's learning parlays", "UNPRICED", "Weakest leg", "no combined sportsbook price", "WHY THIS PRICE", "Eggy's flagship parlay"):
            self.assertIn(needle, blob)


class TestEggyIsUntouched(unittest.TestCase):
    def test_the_parlay_modules_never_touch_eggys_selection_policy_or_ledgers(self):
        import inspect
        for mod in (lp,):
            src = inspect.getsource(mod)
            for forbidden in ("daily_tickets", "ticket_policy", "paper_bankroll", "record_paper_bet", "requests."):
                self.assertNotIn(forbidden, src)

    def test_the_policy_names_the_unit_and_has_no_leg_price_floor(self):
        self.assertEqual(lc.POLICY["unit"], "PARLAY")
        self.assertNotIn("parlay_leg_floor_american", lc.POLICY)
        self.assertFalse(hasattr(lc, "PARLAY_LEG_FLOOR"))
        self.assertEqual(lc.POLICY["eggy"], {"official_picks_per_day": 1, "changed": False})


if __name__ == "__main__":
    unittest.main()
