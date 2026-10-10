"""The postmortem's arithmetic and tooling: exact joint probabilities for tickets that share legs, streak detection that separates tickets from single bets and personal bets,
the independent settlement check, the replay reproducing what was recorded (so the replay is faithful), and the shadow scorer."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "deploy"))

import postmortem_losing_streak as pms  # noqa: E402
import score_selection_shadow as sss  # noqa: E402
import estimate_dependence as ed  # noqa: E402

from operational import postmortem_math as pm  # noqa: E402


def L(i, p, player=None, market="SOG", game="g1"):
    return {"id": i, "game": game, "player": player or i, "market": market, "p": p}


class TestSharedLegMath(unittest.TestCase):
    def test_two_tickets_sharing_a_leg_are_not_independent(self):
        a, b, c = L("A", .5), L("B", .5, game="g2"), L("C", .4, game="g3")
        exact = pm.prob_all_lose([[a, b], [a, c]])
        self.assertAlmostEqual(exact, 0.5 + 0.5 * 0.5 * 0.6, places=12)          # A misses (both lose) or A hits and both partners miss
        self.assertGreater(exact, pm.naive_product_all_lose([.25, .2]))              # the product of ticket-loss chances understates it

    def test_tickets_with_no_shared_leg_are_independent(self):
        t1, t2 = [L("A", .5), L("B", .5, game="g2")], [L("C", .4, game="g3"), L("D", .3, game="g4")]
        self.assertAlmostEqual(pm.prob_all_lose([t1, t2]), (1 - .25) * (1 - .12), places=12)

    def test_nested_lines_of_one_player_hit_together_or_not_at_all(self):
        a2, a3, b, c = L("A2", .6, player="A"), L("A3", .3, player="A"), L("B", .5, game="g2"), L("C", .4, game="g3")
        # U<.3: 2+ and 3+ both hit; .3<=U<.6: only 2+; else neither
        expected = .3 * (1 - .5) * (1 - .4) + .3 * (1 - .5) * 1 + .4 * 1 * 1
        self.assertAlmostEqual(pm.prob_all_lose([[a2, b], [a3, c]]), expected, places=12)

    def test_a_single_ticket_loses_with_one_minus_the_product(self):
        t = [L("A", .5), L("B", .4, game="g2")]
        self.assertAlmostEqual(pm.prob_all_lose([t]), 1 - .2, places=12)

    def test_shading_every_leg_makes_losing_more_likely(self):
        t = [[L("A", .5), L("B", .4, game="g2")], [L("A", .5), L("C", .4, game="g3")]]
        self.assertGreater(pm.prob_all_lose(t, shade=0.05), pm.prob_all_lose(t))

    def test_same_player_in_two_markets_bounds(self):
        pts, sog = L("pts", .45, player="P", market="POINTS"), L("sog", .55, player="P", market="SOG")
        t = [[pts, L("X", .5, game="g2")], [sog, L("Y", .5, game="g3")]]
        indep = pm.prob_all_lose(t, scope=pm.INDEPENDENT_MARKETS)
        together = pm.prob_all_lose(t, scope=pm.SAME_PLAYER_TOGETHER)
        self.assertNotEqual(round(indep, 6), round(together, 6))
        self.assertGreater(together, indep)                                       # a quiet night in both markets at once: more all-lose, not less

    def test_an_independent_single_bet_is_multiplied_in(self):
        t = [[L("A", .5), L("B", .5, game="g2")]]
        self.assertAlmostEqual(pm.prob_all_lose(t, extra_independent=[.4]), (1 - .25) * (1 - .4), places=12)

    def test_the_simulation_agrees_with_the_exact_answer(self):
        t = [[L("A", .5), L("B", .5, game="g2")], [L("A", .5), L("C", .4, game="g3")]]
        exact = pm.prob_all_lose(t)
        sim = pm.simulate([{"legs": t[0]}, {"legs": t[1]}], tail=2, wins_seen=0, n=60_000)
        self.assertAlmostEqual(sim["tail_losses_ending_the_record"], exact, delta=0.01)
        self.assertAlmostEqual(sim["expected_wins"], .25 + .2, delta=0.01)

    def test_a_streak_named_after_the_fact_is_likelier_than_one_named_in_advance(self):
        bets = [{"p": .5} for _ in range(12)]
        sim = pm.simulate(bets, tail=6, wins_seen=3, n=60_000)
        self.assertGreater(sim["a_run_of_that_length_anywhere"], sim["tail_losses_ending_the_record"])


class TestEvidenceStrength(unittest.TestCase):
    def test_likelihood_ratio_is_one_when_the_overstatement_is_zero(self):
        self.assertAlmostEqual(pm.likelihood_ratio([{"p": .5, "hit": True}, {"p": .4, "hit": False}], 0.0), 1.0)

    def test_a_few_results_barely_discriminate(self):
        legs = [{"p": .55, "hit": h} for h in (True, False, False, True, False, True, False, False, True, False, True, False, False, True, False)]
        for d in (0.03, 0.05, 0.08):
            self.assertTrue(1 / 3 < pm.likelihood_ratio(legs, d) < 3)

    def test_legs_needed_for_a_five_point_bias(self):
        self.assertEqual(pm.legs_needed_to_detect(0.05), 619)
        self.assertGreater(pm.legs_needed_to_detect(0.03), pm.legs_needed_to_detect(0.05))

    def test_poisson_binomial(self):
        d = pm.poisson_binomial([.5, .5])
        self.assertAlmostEqual(d["cdf"](0), .25)
        self.assertAlmostEqual(d["cdf"](1), .75)
        self.assertAlmostEqual(d["mean"], 1.0)


def row(i, origin="AUTOMATIC", combo=1, status="LOSS", pnl=-10.0, t="2026-10-08T18:00:00+00:00", legs=None, p=.3, odds=200.0, stake=10.0):
    return {"paper_bet_id": i, "origin": origin, "is_combo": combo, "result_status": status, "profit_loss": pnl, "created_at_utc": t, "legs_json": json.dumps(legs or []), "model_probability": p,
            "entry_odds": odds, "stake": stake, "ev": .1, "event_id": None, "team": None, "conservative_probability": None, "market_no_vig_probability": None}


class TestStreak(unittest.TestCase):
    def test_streak_counts_only_the_model_book_and_ends_at_the_last_win(self):
        rows = [row("a", status="WIN", pnl=15.0, t="1"), row("b", t="2"), row("c", origin="MANUALLY_ADDED", t="3"), row("d", combo=0, t="4"), row("e", t="5")]
        s = pms.losing_streak(rows)
        self.assertEqual((s["streak_length"], s["streak_parlay_tickets"], s["streak_single_bets"]), (3, 2, 1))      # b, d, e: the personal bet c is not in the model book
        self.assertEqual((s["wins"], s["losses"], s["model_book_settled"]), (1, 3, 4))

    def test_an_open_bet_does_not_extend_or_break_the_streak(self):
        rows = [row("a", t="1"), row("b", status="PENDING", pnl=None, t="2"), row("c", t="3")]
        self.assertEqual(pms.losing_streak(rows)["streak_length"], 2)

    def test_bankroll_impact_excludes_personal_bets_and_measures_drawdown(self):
        rows = [row("a", status="WIN", pnl=20.0, t="1"), row("b", t="2"), row("p", origin="MANUALLY_ADDED", t="3"), row("c", t="4")]
        b = pms.bankroll_impact(rows)
        self.assertEqual((b["settled_pnl"], b["cash_now"], b["peak_equity"], b["largest_drawdown"]), (0.0, 500.0, 520.0, 20.0))
        self.assertEqual(b["personal_bets_excluded"][0]["id"], "p")


class TestSettlementCheck(unittest.TestCase):
    games = {"g": {"state": "OFF", "away": "AAA", "home": "BBB", "away_score": 1, "home_score": 3, "last_period": "REG", "skaters": {"9": {"name": "J. Doe", "team": "AAA", "toi": "15:00", "sog": 1, "goals": 0, "assists": 1, "points": 1}}}}

    def test_a_shots_leg_is_judged_on_the_official_shot_count(self):
        miss = pms.leg_result({"game_id": "g", "participant_id": "9", "market_family": "PLAYER_SOG_ALTERNATE", "threshold": 2}, self.games)
        hit = pms.leg_result({"game_id": "g", "participant_id": "9", "market_family": "PLAYER_POINTS", "threshold": 1}, self.games)
        self.assertEqual((miss["hit"], hit["hit"]), (False, True))

    def test_a_missing_player_is_unverified_not_assumed(self):
        self.assertFalse(pms.leg_result({"game_id": "g", "participant_id": "404", "market_family": "PLAYER_POINTS", "threshold": 1}, self.games)["verified"])

    def test_the_ticket_check_flags_a_disagreement_with_the_ledger(self):
        legs = [{"game_id": "g", "participant_id": "9", "participant_name": "J. Doe", "market_family": "PLAYER_POINTS", "threshold": 1, "american_price": 150, "conservative_probability": .4}]
        ok = pms.ticket_check(row("t", legs=legs, status="WIN", pnl=15.0), self.games)
        bad = pms.ticket_check(row("t", legs=legs, status="LOSS"), self.games)
        self.assertEqual((ok["agrees"], bad["agrees"]), (True, False))

    def test_moneyline_is_checked_against_the_official_score(self):
        r = {**row("m", combo=0, status="LOSS"), "event_id": "g", "team": "AAA"}
        self.assertTrue(pms.moneyline_check(r, self.games)["agrees"])             # AAA lost 1-3
        r["result_status"] = "WIN"
        self.assertFalse(pms.moneyline_check(r, self.games)["agrees"])


class TestTheFrozenPostmortemIsReproducible(unittest.TestCase):
    """Runs on the committed extracts (ledger, official box scores, audited candidate pools), so it holds on any machine and in the audit ZIP."""

    @classmethod
    def setUpClass(cls):
        cls.rep = pms.run(pms.load_ledger(use_extract=True))

    def test_nine_consecutive_losses_are_eight_parlay_tickets_and_one_single_bet(self):
        s = self.rep["streak"]
        self.assertEqual((s["streak_length"], s["streak_parlay_tickets"], s["streak_single_bets"]), (9, 8, 1))
        self.assertEqual((s["wins"], s["losses"]), (2, 10))

    def test_the_personal_bet_is_not_in_the_model_results(self):
        ids = {x["id"] for x in self.rep["streak"]["streak"]}
        self.assertNotIn("ME3D7C508EBFDD3", ids)
        self.assertEqual(self.rep["bankroll"]["settled_pnl"], -64.24)
        self.assertEqual(self.rep["bankroll"]["cash_now"], 435.76)

    def test_every_settlement_agrees_with_the_official_box_scores(self):
        c = self.rep["independent_settlement_check"]
        self.assertTrue(c["all_agree"])
        self.assertEqual((c["checked"], c["not_verifiable"]), (12, []))

    def test_shared_legs_make_the_streak_likelier_than_the_independent_product_says(self):
        sp = self.rep["streak_probability"]
        self.assertGreater(sp["legs_shared_players_independent_across_markets"]["all_tickets_lose"], sp["product_of_ticket_loss_chances_WRONG_independent"])
        self.assertAlmostEqual(sp["legs_shared_players_independent_across_markets"]["all_tickets_lose"], 0.171, places=3)

    def test_the_results_cannot_tell_a_calibrated_model_from_an_overstated_one(self):
        lr = self.rep["recorded_legs"]["evidence_strength"]["likelihood_ratio_if_probabilities_were_too_high_by"]
        for v in lr.values():
            self.assertTrue(1 / 3 < v < 3)
        self.assertEqual(self.rep["recorded_legs"]["unique_legs"], 15)

    def test_the_replay_with_each_days_own_rules_reproduces_the_recorded_tickets(self):
        for day, d in self.rep["replay_of_the_original_decisions"]["days"].items():
            self.assertTrue(d["the_replay_with_that_day's_rules_reproduces_the_tickets_that_were_recorded"], day)

    def test_a_hit_chance_floor_can_leave_a_thin_day_empty(self):
        d = self.rep["replay_of_the_original_decisions"]["days"]["2026-10-09"]["variants"]
        self.assertEqual(d["floor_25"]["tickets"], 0)
        self.assertGreater(d["current_code"]["tickets"], 0)

    def test_the_variants_were_fixed_in_advance_and_include_the_rules_that_ran(self):
        v = self.rep["replay_of_the_original_decisions"]["variants_defined_before_any_replacement_result_was_viewed"]
        self.assertIn("rules_on_oct_8", v)
        self.assertIn("proposed_restart", v)

    def test_the_streak_probability_is_labelled_conditional_and_lists_what_each_version_includes(self):
        d = self.rep["streak_probability_dependence_sensitivity"]
        self.assertIn("CONDITIONAL", d["label"])
        self.assertIn("not the probability", d["label"].lower())
        for row in d["rows"]:
            self.assertTrue(row["includes"] and row["assumptions"])
        self.assertIn("not_modelled", d)

    def test_extra_same_game_and_player_dependence_changes_the_figure_only_slightly(self):
        rows = self.rep["streak_probability_dependence_sensitivity"]["rows"]
        base = rows[0]["p_all_lose"]
        self.assertAlmostEqual(base, 0.171, places=3)
        for r in rows[1:]:
            self.assertGreaterEqual(r["p_all_lose"] + 0.004, base)              # more dependence never makes all-lose rarer (beyond Monte Carlo error)
            self.assertLess(r["p_all_lose"], base + 0.02)                        # and here it adds under two points
        self.assertGreater(base, self.rep["streak_probability"]["product_of_ticket_loss_chances_WRONG_independent"] + 0.07)

    def test_the_historical_dependence_estimates_say_opponents_are_unrelated_and_teammates_share_points(self):
        h = self.rep["streak_probability_dependence_sensitivity"]["historical_estimates"]
        self.assertLess(abs(h["opposite_teams_same_game_points"]["correlation"]), 0.02)
        self.assertGreater(h["same_team_same_game_points"]["correlation"], 0.05)

    def test_the_620_and_150_targets_state_their_assumptions_and_what_they_do_not_test(self):
        e = self.rep["evidence_targets"]
        self.assertEqual(e["the_620_figure"]["what_it_is"].split(" ")[0], "619")
        sc = e["the_620_figure"]["dependence_inflates_it"]
        self.assertGreater(sc[0]["independent_units_needed"], 619)
        self.assertGreater(sc[-1]["independent_units_needed"], sc[0]["independent_units_needed"])
        self.assertIn("NOT the general player pool", e["the_620_figure"]["what_it_evaluates"])
        self.assertIn("NOT a leg row", e["the_620_figure"]["unit"])
        f = e["the_150_figure"]
        self.assertIn("not derived from an effect size", f["it_is_a_floor_not_a_power_calculation"])
        self.assertGreater(f["what_it_can_detect"]["games_needed_to_detect_that_gap"], 2000)
        self.assertGreater(f["what_it_can_detect"]["smallest_gap_150_games_can_detect"], 0.02)
        self.assertIn("BETS", f["selected_bets_are_a_different_and_harder_test"])

    def test_the_replay_uses_no_result_and_the_proposal_may_leave_a_day_empty(self):
        days = self.rep["replay_of_the_original_decisions"]["days"]
        blob = json.dumps(days)
        self.assertNotIn("what_happened", blob)
        self.assertNotIn("for_completeness", blob)
        self.assertLessEqual(days["2026-10-08"]["variants"]["proposed_restart"]["tickets"], 5)
        self.assertEqual(days["2026-10-09"]["variants"]["proposed_restart"]["tickets"], 0)

    def test_policy_tradeoffs_are_counted_from_entry_information_and_name_the_side_effects(self):
        t = self.rep["policy_tradeoffs_entry_information_only"]
        self.assertIn("ENTRY INFORMATION ONLY", pms.policy_tradeoffs.__doc__)
        self.assertGreater(t["days"]["2026-10-08"]["qualified_under_the_old_rules"]["tickets"], t["days"]["2026-10-08"]["hit_chance_at_least_30pct"]["tickets"])
        self.assertIn("points or goals", t["why_each_element"]["hit_chance_floor"]["side_effect"])
        self.assertIn("long shots", t["why_each_element"]["value_after_a_haircut"]["side_effect"])
        self.assertIn("Not validated", t["why_each_element"]["hit_chance_floor"]["what_it_is_not"])

    def test_the_live_ledger_if_present_matches_the_frozen_extract(self):
        if not pms.LEDGER.exists():
            self.skipTest("the live ledger is not on this machine")
        live = {r["paper_bet_id"]: (r["result_status"], r["profit_loss"]) for r in pms.load_ledger() if r["paper_bet_id"] in {x["paper_bet_id"] for x in pms.load_ledger(use_extract=True)}}
        frozen = {r["paper_bet_id"]: (r["result_status"], r["profit_loss"]) for r in pms.load_ledger(use_extract=True)}
        self.assertEqual(live, frozen)


class TestDependenceEstimate(unittest.TestCase):
    """On synthetic games where teammates share a team-level shock and nothing else is shared, the estimator finds teammate dependence and none between opponents or across games."""

    @classmethod
    def setUpClass(cls):
        import random
        rng = random.Random(11)
        rows = []
        gid = 0
        for day in range(70):
            date = f"2025-{1 + day // 28:02d}-{1 + day % 28:02d}"
            for pair in range(4):
                gid += 1
                for team in ("AAA", "BBB"):
                    shock = rng.gauss(0, 1)                                         # a team-level shock for this game
                    for pl in range(6):
                        pid = f"{team}{pair}{pl}"
                        base = 0.45 + 0.04 * pl
                        p = min(max(base + 0.12 * shock, 0.02), 0.98)
                        hit = rng.random() < p
                        rows.append({"player_id": pid, "game_id": gid, "date": date, "team": team, "pos": "F", "toi": 15.0, "shots": 2 if hit else 0, "goals": 1 if (hit and rng.random() < 0.3) else 0, "assists": 1 if hit else 0, "points": 1 if hit else 0})
        cls.rep = ed.estimate(sorted(rows, key=lambda r: (r["player_id"], r["date"], r["game_id"])), since="2025-01-25")

    def test_teammates_who_share_a_shock_are_positively_dependent(self):
        self.assertGreater(self.rep["markets"]["points>=1"]["same_team"]["correlation"], 0.04)

    def test_opponents_and_other_games_are_not(self):
        self.assertLess(abs(self.rep["markets"]["points>=1"]["opposite_teams"]["correlation"]), 0.04)
        self.assertLess(abs(self.rep["markets"]["points>=1"]["different_games_same_date"]["correlation"]), 0.04)

    def test_the_same_player_across_markets_is_strongly_related_here_because_one_event_drives_both(self):
        self.assertGreater(self.rep["same_player_two_markets"]["shots>=2 with points>=1"]["correlation"], 0.9)

    def test_it_reports_how_many_player_games_it_used(self):
        self.assertGreater(self.rep["player_games"], 500)
        self.assertGreater(self.rep["markets"]["points>=1"]["same_team"]["pairs"], 1000)


class TestShadowScorer(unittest.TestCase):
    def rec(self, date, pool, variants=None):
        return {"et_date": date, "pool": pool, "variants": variants or {}}

    def test_a_leg_logged_three_times_counts_once_at_its_first_value(self):
        row1 = ["g1", "p1", "PLAYER_SOG_ALTERNATE", 2, -110, .55, "A", "AAA", "s", "q", True]
        row2 = ["g1", "p1", "PLAYER_SOG_ALTERNATE", 2, -125, .60, "A", "AAA", "s", "q", True]
        out = sss.score([self.rec("2026-10-10", [row1]), self.rec("2026-10-10", [row2]), self.rec("2026-10-10", [row2])], lambda *a: {"status": "FINAL", "hit": True})
        self.assertEqual(out["resolved"], 1)
        self.assertEqual(out["all_priced_legs"]["all"]["model_said"], .55)

    def test_unfinished_games_and_non_participants_are_not_scored(self):
        pool = [["g1", "p1", "PLAYER_POINTS", 1, 150, .4, "A", "AAA", "s", "q", True], ["g2", "p2", "PLAYER_POINTS", 1, 150, .4, "B", "BBB", "s", "q", True], ["g3", "p3", "PLAYER_POINTS", 1, 150, .4, "C", "CCC", "s", "q", False]]
        res = {"g1": {"status": "PENDING"}, "g2": {"status": "NO_PARTICIPATION"}, "g3": {"status": "FINAL", "hit": False}}
        out = sss.score([self.rec("d", pool)], lambda g, *a: res[g])
        self.assertEqual((out["resolved"], out["awaiting_games"], out["player_did_not_play"]), (1, 1, 1))
        self.assertEqual(out["legs_that_passed_the_edge_filter"]["all"]["legs"], 0)         # the only resolved leg failed the edge filter

    def test_the_edge_filtered_subset_is_reported_apart_and_the_sample_gate_is_stated(self):
        pool = [["g%d" % i, "p", "PLAYER_SOG_ALTERNATE", 2, -110, .6, "A", "AAA", "s", "q", i % 2 == 0] for i in range(10)]
        out = sss.score([self.rec("d", pool)], lambda *a: {"status": "FINAL", "hit": False})
        self.assertEqual((out["all_priced_legs"]["all"]["legs"], out["legs_that_passed_the_edge_filter"]["all"]["legs"]), (10, 5))
        self.assertEqual(out["enough_to_judge"]["verdict"], "NOT ENOUGH YET")
        self.assertEqual(out["enough_to_judge"]["needed_to_see_a_5_point_overstatement"], 619)
        self.assertGreater(out["enough_to_judge"]["needed_allowing_for_clustering"]["heavy"], out["enough_to_judge"]["needed_allowing_for_clustering"]["light"])

    def test_variant_tickets_are_counted_once_per_day_and_resolved_on_all_legs(self):
        t = {"legs": [["g1:p1:PLAYER_POINTS:1", "A 1+ point", 150, .4], ["g2:p2:PLAYER_POINTS:1", "B 1+ point", 150, .4]], "hit_probability": .16, "price": 525, "ev": .1, "ev_after_haircut": .02}
        recs = [self.rec("d", [], {"floor_25": {"label": "x", "tickets": [t]}}), self.rec("d", [], {"floor_25": {"label": "x", "tickets": [t]}})]
        out = sss.score(recs, lambda g, *a: {"status": "FINAL", "hit": g == "g1"})
        v = out["policy_variants"]["floor_25"]
        self.assertEqual((v["tickets_logged"], v["tickets_resolved"], v["wins"]), (1, 1, 0))

    def test_a_players_nested_lines_are_one_independent_unit(self):
        pool = [["g1", "p1", "PLAYER_SOG_ALTERNATE", k, 100 + 50 * k, .5 - .1 * k, "A", "AAA", "s", "q", True] for k in (2, 3, 4, 5)] + [["g1", "p1", "PLAYER_POINTS", 1, 150, .4, "A", "AAA", "s", "q", True]]
        out = sss.score([self.rec("d", pool)], lambda *a: {"status": "FINAL", "hit": True})
        self.assertEqual(out["enough_to_judge"]["resolved_edge_legs"], 5)
        self.assertEqual(out["enough_to_judge"]["independent_units_player_market_nights"], 2)          # his shots (four lines) and his points

    def test_the_blend_fit_recovers_a_price_that_carries_the_information(self):
        import random
        rng = random.Random(7)
        legs = []
        for night in range(40):
            for i in range(30):
                true = rng.uniform(0.2, 0.8)
                model = min(max(true + rng.gauss(0, 0.12), 0.03), 0.97)          # a noisy model
                price = min(max(true + rng.gauss(0, 0.02), 0.03), 0.97)           # a price that nearly knows the truth
                legs.append({"p": model, "implied": price, "hit": rng.random() < true, "date": f"d{night}", "game": f"g{night}{i % 5}", "player": f"p{i}", "family": "PLAYER_POINTS", "edge": True})
        b = sss.logistic_blend(legs)
        self.assertLess(b[1], 0.5)                                                # little weight on the noisy model
        self.assertGreater(b[2], 0.6)                                              # most of it on the price
        self.assertGreater(b[2], 2 * b[1])

    def test_the_blend_report_says_not_enough_data_for_a_handful_of_legs(self):
        legs = [{"p": .5, "implied": .5, "hit": True, "date": "d", "game": "g", "player": "p", "family": "PLAYER_POINTS", "edge": True}] * 5
        self.assertEqual(sss.blend_report(legs)["status"], "NOT ENOUGH DATA")

    def test_implied_probability(self):
        self.assertAlmostEqual(sss.implied(100), .5)
        self.assertAlmostEqual(sss.implied(-200), 2 / 3)
        self.assertAlmostEqual(sss.implied(300), .25)


if __name__ == "__main__":
    unittest.main()
