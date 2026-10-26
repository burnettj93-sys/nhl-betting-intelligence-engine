"""The day's parlays are a PORTFOLIO and the portfolio decides (operational/learning_parlays.py): similarly strong tickets are chosen to be DIVERSIFIED (shared legs, how the tickets move together at the measured
lifts, repeated games and teams, shared role and model uncertainty) BEFORE anything is recorded; a clearly stronger ticket is never passed over; one assessment serves the selection and the display; the
simulation respects the measured same-game dependence."""
from __future__ import annotations

import inspect
import json
import unittest
from unittest import mock

from operational import eggy_intelligence as ei
from operational import learning_parlays as lp
from tests.test_learning_parlays import cand

LIFTS = {"SOG_SOG_TEAMMATES": {"usable": True, "dependence_lift": 1.0, "ci95": [0.95, 1.05]}, "PTS_PTS_TEAMMATES": {"usable": True, "dependence_lift": 1.109, "ci95": [1.057, 1.157]},
         "ML_PTS_SAMETEAM": {"usable": True, "dependence_lift": 1.17, "ci95": [1.103, 1.25]}, "ML_PTS_OPPOSING": {"usable": True, "dependence_lift": 0.745, "ci95": [0.691, 0.799]}}


def pool(ps):
    return [cand(pid=str(8470001 + i), game=str(i + 1), p=p, team=f"T{i}") for i, p in enumerate(ps)]


class TestSelectionIsPortfolioAware(unittest.TestCase):
    def picks(self, ps, neutral=False):
        with mock.patch.object(lp, "dependence_lifts", return_value=LIFTS):
            ctx = mock.patch.object(lp, "concentration_key", lambda v, k: (0, 0, 0, 0, 0, 0)) if neutral else mock.patch.object(lp, "SIMILAR_STRENGTH", lp.SIMILAR_STRENGTH)
            with ctx:
                out = lp.build(pool(ps), slots=4)
        return [tuple(sorted(c["key"] for c in p["cands"])) for p in out["parlays"]]

    def test_two_similarly_strong_tickets_are_chosen_to_share_no_leg_when_the_alternative_shares_one(self):
        ps = (0.70, 0.69, 0.68, 0.67, 0.66, 0.65, 0.64)
        rank_order, aware = self.picks(ps, neutral=True), self.picks(ps)
        self.assertEqual(aware[0], rank_order[0])                                                # the best ticket is the same
        shared = lambda a, b: len(set(a) & set(b))
        self.assertGreater(shared(rank_order[0], rank_order[1]), 0, "the pure rank order shares a leg between its first two tickets in this pool")
        self.assertLessEqual(shared(aware[0], aware[1]), shared(rank_order[0], rank_order[1]))
        self.assertEqual(shared(aware[0], aware[1]), 0)                                          # the diversified choice among similarly strong tickets

    def test_a_clearly_stronger_ticket_is_never_passed_over_for_diversification(self):
        ps = (0.72, 0.71, 0.70, 0.69, 0.68, 0.56, 0.56)                                          # the only disjoint tickets rest on 56% legs: clearly weaker (not within SIMILAR_STRENGTH)
        aware = self.picks(ps)
        self.assertEqual(len(aware[0]), 3)
        second = aware[1]
        weakest_second = min(ps[int(k.split("-")[1]) - 8470001] for k in second)
        self.assertGreaterEqual(weakest_second, 0.68 - 1e-9)                                     # the stronger overlapping ticket was kept

    def test_the_candidate_set_the_reuse_cap_and_the_ticket_gate_are_unchanged(self):
        ps = (0.70, 0.69, 0.68, 0.67, 0.66, 0.65, 0.64, 0.63)
        aware, plain = self.picks(ps), self.picks(ps, neutral=True)
        self.assertEqual(len(aware), len(plain))                                                 # the volume is not changed by the diversification
        for tickets in (aware, plain):
            uses = {}
            for t in tickets:
                for k in t:
                    uses[k] = uses.get(k, 0) + 1
            self.assertLessEqual(max(uses.values()), lp.LEG_REUSE)

    def test_same_game_points_legs_that_move_together_are_less_diversified_than_cross_game_ones(self):
        mk = lambda key, game, fam, team, est: {"key": key, "estimate": est, "game_id": game, "team": team, "market_family": fam, "threshold": 1, "player": key, "role_risk": False, "model_risk": False}
        kept = {"key": "K", "legs": [mk("a", "1", "PLAYER_POINTS", "AAA", 0.62), mk("b", "2", "PLAYER_POINTS", "BBB", 0.62)]}
        kept["probability"] = lp.ticket_probability(kept["legs"])
        same = {"key": "S", "legs": [mk("c", "1", "PLAYER_POINTS", "AAA", 0.62), mk("d", "3", "PLAYER_POINTS", "CCC", 0.62)]}
        cross = {"key": "X", "legs": [mk("e", "4", "PLAYER_POINTS", "DDD", 0.62), mk("f", "3", "PLAYER_POINTS", "CCC", 0.62)]}
        for v in (same, cross):
            v["probability"] = lp.ticket_probability(v["legs"])
        with mock.patch.object(lp, "dependence_lifts", return_value={"PTS_PTS_TEAMMATES": {"usable": True, "dependence_lift": 1.5, "ci95": [1.4, 1.6]}}):
            k_same, k_cross = lp.concentration_key(same, [kept]), lp.concentration_key(cross, [kept])
        self.assertGreater(k_same, k_cross)                                                      # the positively dependent pair concentrates the day (covariance), the cross-game ticket does not
        self.assertEqual(k_same[0], 0)                                                           # no shared leg: the difference is the dependence, measured

    def test_shared_role_uncertainty_on_one_team_is_a_concentration(self):
        base = {"market_family": "PLAYER_SOG_ALTERNATE", "threshold": 2, "model_risk": False}
        kept = {"key": "K", "probability": 0.4, "legs": [{**base, "key": "a", "estimate": 0.7, "game_id": "1", "team": "AAA", "player": "a", "role_risk": True}]}
        risky = {"key": "R", "probability": 0.4, "legs": [{**base, "key": "b", "estimate": 0.7, "game_id": "2", "team": "AAA", "player": "b", "role_risk": True}]}
        safe = {"key": "S", "probability": 0.4, "legs": [{**base, "key": "c", "estimate": 0.7, "game_id": "3", "team": "CCC", "player": "c", "role_risk": False}]}
        with mock.patch.object(lp, "dependence_lifts", return_value={}):
            self.assertGreater(lp.concentration_key(risky, [kept]), lp.concentration_key(safe, [kept]))


class TestOneAssessmentForSelectionAndDisplay(unittest.TestCase):
    def test_a_candidate_and_its_stored_row_give_the_same_portfolio_view(self):
        flags = [{"name": "ROLE_ASSUMPTION", "level": "MODERATE", "text": "x"}]
        cs = [cand(pid="1", game="1", p=0.70, flags=flags, contradiction="MODERATE"), cand(pid="2", game="2", p=0.66), cand(pid="3", game="3", p=0.64)]
        with mock.patch.object(lp, "dependence_lifts", return_value=LIFTS):
            t = lp.construct(cs)
            rec = lp.record_dict(t)
            row = {"idempotency_key": t["key"], "legs_json": json.dumps(rec["legs"], default=str)}
            a, b = lp.view_of_candidates(t), lp.view_of_row(row)
        self.assertEqual([(l["key"], l["estimate"], l["team"], l["role_risk"], l["model_risk"], l["game_id"]) for l in a["legs"]],
                         [(l["key"], l["estimate"], l["team"], l["role_risk"], l["model_risk"], l["game_id"]) for l in b["legs"]])
        self.assertTrue(a["legs"][0]["role_risk"])                                               # the build-time view knows the role risk (it was hard-coded False before)
        self.assertAlmostEqual(a["probability"], b["probability"], places=6)

    def test_nothing_hard_codes_role_risk(self):
        self.assertNotIn('"role_risk": False', inspect.getsource(lp.build))
        self.assertNotIn("role_risk\": False", inspect.getsource(lp.view_of_candidates))

    def test_the_selection_and_the_display_call_the_same_function(self):
        self.assertIn("portfolio_summary(kept_views)", inspect.getsource(lp.build))
        self.assertIn("portfolio_summary([view_of_row(r)", inspect.getsource(lp.portfolio_from_rows))
        self.assertIs(lp.assess_portfolio, lp.portfolio_summary)


class TestThePortfolioRespectsMeasuredDependence(unittest.TestCase):
    def two_same_game_legs(self):
        mk = lambda key, team: {"key": key, "label": key, "estimate": 0.6, "game_id": "1", "team": team, "market_family": "PLAYER_POINTS", "threshold": 1, "player": key, "role_risk": False, "model_risk": False}
        legs = [mk("a", "AAA"), mk("b", "AAA")]
        return [{"key": "T", "legs": legs, "probability": lp.ticket_probability(legs)}]

    def test_the_ticket_probability_and_the_simulation_use_the_same_measured_lift(self):
        t = self.two_same_game_legs()
        with mock.patch.object(lp, "dependence_lifts", return_value={"PTS_PTS_TEAMMATES": {"usable": True, "dependence_lift": 1.3, "ci95": [1.2, 1.4]}}):
            p = lp.ticket_probability(t[0]["legs"])
            hits = lp.simulate_tickets_hit(t, draws=40000)
        self.assertAlmostEqual(p, 0.36 * 1.3, places=4)
        self.assertAlmostEqual(sum(hits) / len(hits), p, delta=0.012)                              # the simulation reproduces the pair's measured joint chance (independence would give 0.36)

    def test_without_a_measurement_the_pair_is_independent_and_that_is_said(self):
        t = self.two_same_game_legs()
        with mock.patch.object(lp, "dependence_lifts", return_value={}):
            s = lp.portfolio_summary(t)
        self.assertAlmostEqual(s["simulated_expected_tickets_hit"], 0.36, delta=0.03)
        self.assertEqual(s["same_game_pairs"], {"total": 1, "without_a_measured_lift": 1})

    def test_chance_none_hit_moves_with_dependence_and_reports_the_range_at_the_interval_ends(self):
        mk = lambda key, game, team: {"key": key, "label": key, "estimate": 0.6, "game_id": game, "team": team, "market_family": "PLAYER_POINTS", "threshold": 1, "player": key, "role_risk": False, "model_risk": False}
        tickets = []
        for i, g in enumerate(("1", "2", "3")):
            legs = [mk(f"x{i}", g, "AAA"), mk(f"y{i}", g, "AAA")]
            tickets.append({"key": f"T{i}", "legs": legs, "probability": 0.36})
        with mock.patch.object(lp, "dependence_lifts", return_value={"PTS_PTS_TEAMMATES": {"usable": True, "dependence_lift": 1.3, "ci95": [1.0, 1.6]}}):
            s = lp.portfolio_summary(tickets, draws=8000)
        lo, hi = s["chance_no_ticket_hits_range"]
        self.assertLessEqual(lo, s["chance_no_ticket_hits"] + 1e-9)
        self.assertGreaterEqual(hi, s["chance_no_ticket_hits"] - 1e-9)
        self.assertGreater(hi, lo)                                                                 # the uncertainty of the measurement is exposed, not hidden

    def test_a_negative_dependence_is_reproduced_too(self):
        mk = lambda key, fam, team: {"key": key, "label": key, "estimate": 0.6, "game_id": "1", "team": team, "market_family": fam, "threshold": 1, "player": key, "role_risk": False, "model_risk": False}
        legs = [mk("ml", "MONEYLINE", "AAA"), mk("opp", "PLAYER_POINTS", "BBB")]
        t = [{"key": "T", "legs": legs, "probability": lp.ticket_probability(legs)}]
        with mock.patch.object(lp, "dependence_lifts", return_value=LIFTS):
            hits = lp.simulate_tickets_hit(t, draws=40000)
        self.assertAlmostEqual(sum(hits) / len(hits), 0.36 * 0.745, delta=0.012)


class TestRiskFacts(unittest.TestCase):
    def test_a_role_flag_makes_a_leg_role_risk_and_a_model_flag_makes_it_model_risk(self):
        c = cand(flags=[{"name": "TOI_DROP", "level": "MODERATE", "text": "x"}], contradiction="MODERATE")
        self.assertEqual(lp.leg_risks(c["card"]), {"role_risk": True, "model_risk": False})
        c = cand(flags=[{"name": "HISTORY_SAYS_MODEL_RUNS_HIGH", "level": "MODERATE", "text": "x"}], contradiction="MODERATE")
        self.assertTrue(lp.leg_risks(c["card"])["model_risk"])
        c = cand(cal="MEDIUM")
        self.assertTrue(lp.leg_risks(c["card"])["model_risk"])                                    # calibration below HIGH is model uncertainty
        self.assertEqual(lp.leg_risks(cand()["card"]), {"role_risk": False, "model_risk": False})

    def test_a_low_level_role_flag_is_not_role_risk(self):
        c = cand(flags=[{"name": "TOI_DROP", "level": "LOW", "text": "x"}])
        self.assertFalse(lp.leg_risks(c["card"])["role_risk"])


if __name__ == "__main__":
    unittest.main()
