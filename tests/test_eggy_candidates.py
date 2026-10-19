"""Eggy stage A (operational/eggy_candidates.py): the whole-slate candidate search, same-game theses, honest dependence and availability, with COL @ PHI (2026-10-19) as the regression case.
Stage B (the production recording policy) is not touched here; these tests also pin that stage A can never record, price or certify anything on its own."""
from __future__ import annotations

import copy
import datetime as dt
import inspect
import unittest
from unittest import mock

from operational import eggy_candidates as ec
from operational import slate_board as sb
from tests.product_fixture import snapshot
from tests.test_product_pages import run_page

NOW = dt.datetime(2026, 10, 19, 12, 10, tzinfo=dt.timezone.utc)          # 08:10 ET, before the 17:30 ET wave
Q_ML = "2026-10-19T11:58:19Z"
GID = "2026020138"
START = "2026-10-19T23:00:00Z"


def skater(pid, name, team, pos, **p):
    prob = {"points>=1": p.get("pts1", 0.3), "points>=2": 0.05, "shots>=1": 0.9, "shots>=2": p.get("sog2", 0.4), "shots>=3": p.get("sog3", 0.15), "goals>=1": p.get("g1", 0.1)}
    return {"player_id": pid, "name": name, "team": team, "position": pos, "games_total": 300,
            "projection": {"pricing_eligible": True, "expected": {"points": p.get("xpts", 0.6), "shots": p.get("xsog", 2.0), "goals": 0.2}, "probabilities": prob}}


def col_players():
    return {"away": [skater("8477492", "Nathan MacKinnon", "COL", "C", pts1=0.7372, sog2=0.8659, sog3=0.6842, g1=0.3838, xpts=1.257, xsog=3.551),
                     skater("8480069", "Cale Makar", "COL", "D", pts1=0.5805, sog2=0.6186, sog3=0.3558, g1=0.1909, xpts=0.9, xsog=3.0),
                     skater("8480039", "Martin Necas", "COL", "R", pts1=0.66, sog2=0.6936, g1=0.25, xpts=0.95, xsog=2.7),
                     skater("8475167", "Nazem Kadri", "COL", "C", pts1=0.45, sog2=0.69, g1=0.2, xpts=0.7, xsog=2.6)],
            "home": [skater("8470001", "Philly One", "PHI", "C", pts1=0.35, sog2=0.45, g1=0.15), skater("8470002", "Philly Two", "PHI", "W", pts1=0.3, sog2=0.4, g1=0.12)]}


def game(*, ml_quote=Q_ML):
    return {"game_id": GID, "away": "COL", "home": "PHI", "start_utc": START, "start_et": "7:00 PM ET", "state": "SCHEDULED",
            "side": {"sides": {"home": {"team": "PHI", "american": 195.0, "no_vig": 0.325, "model": 0.356, "quote_captured_at_utc": ml_quote},
                               "away": {"team": "COL", "american": -238.0, "no_vig": 0.675, "model": 0.6439, "quote_captured_at_utc": ml_quote}}},
            "goalies": [{"side": "home", "team": "PHI", "projection": {"save_pct_used": 0.9027}}, {"side": "away", "team": "COL", "projection": {"save_pct_used": 0.91}}],
            "coverage": {"integrity": {"withdrawn": []}}}


def product():
    return {"goalies": {}, "model_health": {
        "skater_verdicts": {k: {"verdict": "BEATS_BASELINES"} for k in ("shots>=2", "shots>=3", "points>=1", "goals>=1")},
        "market_matrix": [{"market": "Shots on goal (alternate ladder)", "contract": "VERIFIED", "validation": "beats baselines on 5 of 5 thresholds"},
                          {"market": "Points (1+, 2+)", "contract": "VERIFIED", "validation": "beats baselines on 2 of 2 thresholds"},
                          {"market": "Anytime goal", "contract": "VERIFIED", "validation": "beats baselines on 1 of 1 thresholds"}]}}


def plan():
    return {"games_today": 4, "games_priced": [GID, "2026020140"], "games_not_priced": ["2026020137", "2026020139"], "surplus_credits": 0.32,
            "game_markets": {GID: ["player_shots_on_goal_alternate", "player_goal_scorer_anytime", "player_total_saves"]},
            "waves": [{"index": 0, "first_start_utc": START, "games": ["2026020137", GID, "2026020139"], "selected": [GID]}]}


def priced_pool(points=False, q="2026-10-19T21:31:00Z"):
    p = {"MacKinnon": ("8477492", [[2, -300.0, 0.8659], [3, -105.0, 0.6842]]), "Makar": ("8480069", [[2, -135.0, 0.6186]]), "Necas": ("8480039", [[2, -165.0, 0.6936]])}
    players = {}
    for name, (pid, lines) in p.items():
        players[pid] = {"n": name, "t": "COL", "m": {"SOG": {"q": q, "r": q, "l": lines, "rec": {"2": [26, 30, 0.80, 8]}}}}
    if points:
        players["8477492"]["m"]["PTS"] = {"q": q, "r": q, "l": [[1, -240.0, 0.7372]]}
    return {"games": {GID: {"away": "COL", "home": "PHI", "start_utc": START, "players": players}}}


def avail(**markets):
    return {"games": {GID: {"markets": markets}}}


def specs():
    return [{"code": "ML", "team": "COL"}, {"code": "PTS", "player": "Nathan MacKinnon", "k": 1}, {"code": "SOG", "player": "Nathan MacKinnon", "k": 2}, {"code": "SOG", "player": "Cale Makar", "k": 2}]


def evaluate(now=NOW, pool=None, availability=None, pl=None, ml_quote=Q_ML):
    return ec.evaluate_combination(now, game=game(ml_quote=ml_quote), sides_players=col_players(), specs=specs(), pool_game=((pool or {}).get("games") or {}).get(GID), avail_game=((availability or {}).get("games") or {}).get(GID),
                                   plan=pl if pl is not None else plan(), product=product())


def leg(c, label):
    return next(l for l in c["legs"] if l["label"] == label)


class TestColoradoRegressionBeforeTheWave(unittest.TestCase):
    """08:10 ET: the moneyline is priced, the funded game's shots and goals are captured with the 17:30 wave, and points are not part of the plan at all."""

    def setUp(self):
        self.c = evaluate()

    def test_all_four_legs_resolve_and_carry_their_own_probabilities(self):
        self.assertEqual([l["label"] for l in self.c["legs"]], ["COL to win", "Nathan MacKinnon 1+ point", "Nathan MacKinnon 2+ shots", "Cale Makar 2+ shots"])
        self.assertAlmostEqual(leg(self.c, "Nathan MacKinnon 1+ point")["model_probability"], 0.7372, places=4)
        self.assertAlmostEqual(leg(self.c, "Nathan MacKinnon 2+ shots")["model_probability"], 0.8659, places=4)
        self.assertAlmostEqual(leg(self.c, "Cale Makar 2+ shots")["model_probability"], 0.6186, places=4)
        self.assertAlmostEqual(leg(self.c, "COL to win")["estimate"], 0.675, places=3)                         # the market's own no-vig probability

    def test_the_moneyline_is_priced_and_fresh_with_its_capture_time(self):
        ml = leg(self.c, "COL to win")
        self.assertEqual((ml["price"]["american"], ml["price"]["quote_updated_utc"], ml["price"]["fresh"], ml["price_outlook"]), (-238.0, Q_ML, True, "PRICED"))

    def test_points_are_not_priced_because_the_plan_does_not_buy_them_and_the_reason_says_so(self):
        pts = leg(self.c, "Nathan MacKinnon 1+ point")
        self.assertFalse(pts["priced"])
        self.assertEqual(pts["price_outlook"], "NOT_IN_CREDIT_PLAN")
        self.assertIn("surplus", " ".join(pts["exclusions"]))
        self.assertIn("0.32", " ".join(pts["exclusions"]))
        self.assertFalse(pts["gates"]["recording_policy_uses_market"])
        self.assertIn("recording policy does not use this market", " ".join(pts["exclusions"]))

    def test_the_shots_lines_await_the_wave_with_its_time(self):
        for label in ("Nathan MacKinnon 2+ shots", "Cale Makar 2+ shots"):
            l = leg(self.c, label)
            self.assertEqual(l["price_outlook"], "AWAITING_WAVE")
            self.assertIn("5:30 PM ET", " ".join(l["exclusions"]))
            self.assertTrue(l["gates"]["model_support"] and l["gates"]["model_validated_held_out"])
            self.assertIsNone(l["gates"]["offered_in_feed"])                                                   # unknown until the market is captured, never asserted either way

    def test_the_combination_is_surfaced_but_never_certified_and_nothing_is_fabricated(self):
        c = self.c
        self.assertEqual(c["status"], "AWAITING_PRICES")
        self.assertEqual(c["label"], "EGGY SAME-GAME CANDIDATE — JOINT PROBABILITY NOT YET CERTIFIED")
        self.assertEqual(c["dependence"]["joint_model"], "NOT_CERTIFIED")
        self.assertIsNone(c["dependence"]["joint_probability"])
        self.assertFalse(c["combined_price"]["available"])
        self.assertIsNone(c["combined_price"]["american"])
        self.assertIsNone(c["expected_value"])
        self.assertFalse(c["monitored_paper_bet_eligible"])
        self.assertTrue(all(l["monitored_eligible"] is False for l in c["legs"]))
        text = " ".join(c["blockers"])
        self.assertIn("SAME-GAME DEPENDENCE", text)
        self.assertIn("NO COMBINED PRICE", text)
        self.assertIn("PRODUCTION POLICY", text)

    def test_the_only_joint_figures_are_assumption_free_frechet_bounds(self):
        b = self.c["dependence"]["joint_bounds"]
        est = [l["estimate"] for l in self.c["legs"]]
        self.assertAlmostEqual(b["low"], max(0.0, sum(est) - 3), places=3)
        self.assertAlmostEqual(b["high"], min(est), places=3)
        self.assertIn("not an estimate", b["basis"])

    def test_every_pair_is_classified_with_a_direction_and_the_same_player_pair_is_flagged_strong(self):
        pairs = {(p["a"], p["b"]): p for p in self.c["dependence"]["pairs"]}
        self.assertEqual(len(pairs), 6)
        same = pairs[("Nathan MacKinnon 1+ point", "Nathan MacKinnon 2+ shots")]
        self.assertEqual((same["kind"], same["direction"], same["strength"]), ("SAME_PLAYER_SHOTS_AND_POINTS", "POSITIVE", "strong"))
        self.assertEqual(pairs[("COL to win", "Nathan MacKinnon 1+ point")]["kind"], "TEAM_RESULT_AND_PLAYER_OFFENCE")
        self.assertEqual(pairs[("Nathan MacKinnon 2+ shots", "Cale Makar 2+ shots")]["kind"], "TEAMMATES_SHOTS")

    def test_ontario_is_stated_as_unverified_on_every_leg(self):
        self.assertTrue(all("UNVERIFIED" in l["ontario"] for l in self.c["legs"]))


class TestColoradoRegressionAfterTheWave(unittest.TestCase):
    def test_priced_legs_carry_price_time_freshness_record_and_the_ticket_estimate(self):
        c = evaluate(now=dt.datetime(2026, 10, 19, 21, 40, tzinfo=dt.timezone.utc), pool=priced_pool(), ml_quote="2026-10-19T21:30:00Z",
                     availability=avail(player_shots_on_goal_alternate={"status": "POSTED", "checked_utc": "2026-10-19T21:31:00Z", "last_update": "2026-10-19T21:31:00Z", "outcomes": 40}))
        mk = leg(c, "Nathan MacKinnon 2+ shots")
        self.assertEqual((mk["price"]["american"], mk["price"]["quote_updated_utc"], mk["price"]["fresh"]), (-300.0, "2026-10-19T21:31:00Z", True))
        self.assertEqual((mk["recent_record"], mk["estimate"]), ("26 of 30", 0.8))                                  # the ticket estimate from the pool, not the raw model
        self.assertIn("recent record", mk["estimate_basis"])
        self.assertAlmostEqual(mk["implied_probability"], 300 / 400, places=3)
        self.assertEqual(c["priced_legs"], 3)                                                                        # a fresh moneyline + two shots lines; points still unpriced
        self.assertEqual(leg(c, "Nathan MacKinnon 1+ point")["price_outlook"], "NOT_IN_CREDIT_PLAN")

    def test_a_stale_price_is_not_counted_as_a_price_for_the_combination(self):
        c = evaluate(now=dt.datetime(2026, 10, 19, 21, 40, tzinfo=dt.timezone.utc), pool=priced_pool())            # the 07:58 ET moneyline is stale by now, the shots lines are fresh
        self.assertEqual(c["priced_legs"], 2)
        self.assertEqual(c["stale_priced_legs"], ["COL to win"])
        ml = leg(c, "COL to win")
        self.assertTrue(ml["priced"] and not ml["usable_price"])
        self.assertEqual(ml["price_outlook"], "STALE")
        self.assertEqual(c["status"], "AWAITING_PRICES")

    def test_even_with_every_leg_priced_the_same_game_combination_stays_not_certified(self):
        c = evaluate(now=dt.datetime(2026, 10, 19, 21, 40, tzinfo=dt.timezone.utc), pool=priced_pool(points=True), ml_quote="2026-10-19T21:30:00Z")
        self.assertEqual(c["priced_legs"], 4)
        self.assertTrue(c["complete_prices"])
        self.assertEqual(c["status"], "NOT_CERTIFIED")
        self.assertIsNone(c["dependence"]["joint_probability"])
        self.assertFalse(c["combined_price"]["available"])
        self.assertFalse(c["monitored_paper_bet_eligible"])
        self.assertIn("SAME-GAME DEPENDENCE", " ".join(c["blockers"]))

    def test_the_other_sides_stale_quote_makes_the_moneyline_stale_too(self):
        g = game(ml_quote="2026-10-19T21:30:00Z")
        g["side"]["sides"]["home"]["quote_captured_at_utc"] = "2026-10-19T15:00:00Z"                                # the estimate rests on both sides' quotes
        ml = ec.moneyline_leg(g, "away", dt.datetime(2026, 10, 19, 21, 40, tzinfo=dt.timezone.utc))
        self.assertFalse(ml["price"]["fresh"])
        g2 = game(ml_quote="2026-10-19T21:30:00Z")
        g2["side"]["fresh"] = False                                                                                 # the board's own verdict (status not ACTIVE, or a side aged out)
        self.assertFalse(ec.moneyline_leg(g2, "away", dt.datetime(2026, 10, 19, 21, 40, tzinfo=dt.timezone.utc))["price"]["fresh"])

    def test_a_player_without_a_line_in_a_priced_market_is_not_reported_as_priced_or_as_not_offered(self):
        c = evaluate(now=dt.datetime(2026, 10, 19, 21, 40, tzinfo=dt.timezone.utc), pool=priced_pool(), ml_quote="2026-10-19T21:30:00Z")
        pool = priced_pool()
        pool["games"][GID]["players"].pop("8480069")                                                                # Makar has no line, the market is priced for others
        c = ec.evaluate_combination(dt.datetime(2026, 10, 19, 21, 40, tzinfo=dt.timezone.utc), game=game(ml_quote="2026-10-19T21:30:00Z"), sides_players=col_players(), specs=specs(), pool_game=pool["games"][GID],
                                    avail_game={"markets": {"player_shots_on_goal_alternate": {"status": "POSTED"}}}, plan=plan(), product=product())
        mk = leg(c, "Cale Makar 2+ shots")
        self.assertEqual(mk["price_outlook"], "NO_LINE_FOR_THIS_PLAYER")
        self.assertIsNone(mk["gates"]["offered_in_feed"])                                                           # not asserted either way
        self.assertNotIn("PRICED", " ".join(mk["exclusions"]).replace("NO_LINE", ""))
        self.assertIn("not offered, or removed", " ".join(mk["exclusions"]))

    def test_a_stale_quote_is_flagged_not_hidden(self):
        c = evaluate(now=dt.datetime(2026, 10, 19, 22, 30, tzinfo=dt.timezone.utc), pool=priced_pool(q="2026-10-19T20:30:00Z"))   # priced 20:30Z, opened 22:30Z (120 minutes), game at 23:00Z: past the 100-minute limit near puck drop
        mk = leg(c, "Nathan MacKinnon 2+ shots")
        self.assertFalse(mk["price"]["fresh"])
        self.assertIn("freshness limit", " ".join(mk["exclusions"]))

    def test_the_moneyline_ages_out_and_says_so(self):
        c = evaluate(now=dt.datetime(2026, 10, 19, 18, 30, tzinfo=dt.timezone.utc))                             # 6.5 hours after the 07:58 ET quote
        ml = leg(c, "COL to win")
        self.assertFalse(ml["price"]["fresh"])
        self.assertIn("freshness limit", " ".join(ml["exclusions"]))


class TestAvailabilityExplainerSaysWhy(unittest.TestCase):
    def rows(self, availability=None, pool=None, pl=None, now=NOW):
        g = game()
        return {r["market"]: r for r in ec.explain_markets(g, ((availability or {}).get("games") or {}).get(GID), pl if pl is not None else plan(), ((pool or {}).get("games") or {}).get(GID), product()["model_health"]["market_matrix"], now)}

    def test_awaiting_the_wave_and_not_in_the_plan_are_different_reasons(self):
        r = self.rows()
        self.assertEqual(r["player_shots_on_goal_alternate"]["state"], "AWAITING_WAVE")
        self.assertIn("5:30 PM ET", r["player_shots_on_goal_alternate"]["why"])
        self.assertEqual(r["player_points"]["state"], "NOT_IN_CREDIT_PLAN")
        self.assertEqual(r["player_goal_scorer_anytime"]["state"], "AWAITING_WAVE")

    def test_a_game_outside_the_credit_plan_is_excluded_by_the_plan(self):
        p = plan()
        p["games_priced"], p["games_not_priced"] = ["2026020140"], [GID]
        r = self.rows(pl=p)
        self.assertTrue(all(x["state"] == "EXCLUDED_BY_CREDIT_PLAN" for x in r.values()))
        self.assertIn("1 of 4 games", r["player_points"]["why"])

    def test_provider_outcomes_are_told_apart(self):
        cases = {"NOT_POSTED": "NOT_POSTED_BY_BOOK", "FETCH_ERROR": "FETCH_FAILED", "BUDGET_BLOCKED": "EXCLUDED_BY_CREDIT_BUDGET", "POSTED": "POSTED_NOT_USABLE"}
        for status, want in cases.items():
            r = self.rows(avail(player_shots_on_goal_alternate={"status": status, "checked_utc": "2026-10-19T12:00:00Z", "detail": "x"}))
            self.assertEqual(r["player_shots_on_goal_alternate"]["state"], want, status)

    def test_priced_and_stale_and_the_contract_and_policy_columns(self):
        priced = self.rows(pool=priced_pool(), now=dt.datetime(2026, 10, 19, 21, 40, tzinfo=dt.timezone.utc))
        self.assertEqual(priced["player_shots_on_goal_alternate"]["state"], "PRICED")
        stale = self.rows(pool=priced_pool(q="2026-10-19T20:30:00Z"), now=dt.datetime(2026, 10, 19, 22, 30, tzinfo=dt.timezone.utc))
        self.assertEqual(stale["player_shots_on_goal_alternate"]["state"], "STALE")
        self.assertEqual(priced["player_points"]["contract"], "VERIFIED")
        self.assertTrue(priced["player_shots_on_goal_alternate"]["recording_policy"].startswith("USED"))
        self.assertTrue(priced["player_points"]["recording_policy"].startswith("NOT USED"))

    def test_a_due_capture_that_has_not_landed_says_so(self):
        r = self.rows(now=dt.datetime(2026, 10, 19, 21, 50, tzinfo=dt.timezone.utc))
        self.assertEqual(r["player_shots_on_goal_alternate"]["state"], "CAPTURE_DUE")


class TestTheDay(unittest.TestCase):
    def build(self, primary=None, games=None, pl=None):
        g = game()
        return ec.build(NOW, games=games if games is not None else [g], players_by_game={GID: col_players()}, product=product(), pool=None, availability=None, plan=pl if pl is not None else plan(), primary=primary or {})

    def test_with_a_strong_thesis_and_no_official_ticket_the_output_is_the_best_candidate_not_a_pass(self):
        d = self.build(primary={"status": "NONE", "ticket": None, "reason": "0 player-prop leg(s) in 0 game(s) with prop prices"})
        self.assertEqual((d["daily_output"], d["headline"]), (ec.BEST, "NO CERTIFIED EGGY PICK TODAY"))
        best = d["best_candidate"]
        self.assertEqual((best["game_id"], best["thesis"]["key"]), (GID, "STRONG_FAVORITE_STAR"))
        self.assertEqual([l["label"] for l in best["legs"]][:3], ["COL to win", "Nathan MacKinnon 1+ point", "Nathan MacKinnon 2+ shots"])
        self.assertIn("Cale Makar 2+ shots (model 62%)", best["other_compatible_legs"])                         # Makar is on the bench the search considered
        self.assertIn("COL controls the matchup", best["thesis"]["sentence"])

    def test_an_official_ticket_makes_the_output_official_and_stage_b_is_unchanged(self):
        d = self.build(primary={"status": "SELECTED", "ticket": {"ticket_id": "T1", "legs": []}})
        self.assertEqual(d["daily_output"], ec.OFFICIAL)
        self.assertTrue(d["stage_b"]["official_ticket"])

    def test_a_true_pass_happens_only_when_no_thesis_reaches_the_support_bar(self):
        g = game()
        g["side"]["sides"]["away"].update(american=-110.0, no_vig=0.5, model=0.5)
        g["side"]["sides"]["home"].update(american=-110.0, no_vig=0.5, model=0.5)
        weak = {"away": [skater(f"A{i}", f"Away {i}", "COL", "C", pts1=0.3, sog2=0.4, g1=0.1) for i in range(5)], "home": [skater(f"H{i}", f"Home {i}", "PHI", "C", pts1=0.3, sog2=0.4, g1=0.1) for i in range(5)]}
        d = ec.build(NOW, games=[g], players_by_game={GID: weak}, product=product(), pool=None, availability=None, plan=plan(), primary={})
        self.assertEqual(d["daily_output"], ec.PASS)
        self.assertIsNone(d["best_candidate"])
        self.assertEqual(d["examined"]["games"], 1)
        self.assertTrue(any(t.get("why_not") for t in d["examined"]["by_game"][0]["theses"]))                  # what was examined and why each thesis failed is on the record

    def test_a_game_the_plan_excludes_ranks_below_one_whose_prices_can_still_arrive(self):
        other = game()
        other["game_id"] = "2026020137"
        other["away"], other["home"] = "ANA", "NYR"
        d = ec.build(NOW, games=[other, game()], players_by_game={GID: col_players(), "2026020137": copy.deepcopy(col_players())}, product=product(), pool=None, availability=None, plan=plan(), primary={})
        ids = [d["best_candidate"]["id"]] + [c["id"] for c in d["other_candidates"]]
        funded = [c for c in ([d["best_candidate"]] + d["other_candidates"]) if c["game_funded"]]
        unfunded = [c for c in ([d["best_candidate"]] + d["other_candidates"]) if not c["game_funded"]]
        self.assertTrue(funded and unfunded)
        self.assertLess(ids.index(funded[0]["id"]), ids.index(unfunded[0]["id"]))

    def test_one_failing_game_does_not_remove_the_rest_of_the_search(self):
        broken = {"game_id": "2026020199", "away": "A", "home": "B", "state": "SCHEDULED", "side": None, "goalies": None, "coverage": {}}
        real = ec.candidates_for_game

        def flaky(game, *a, **k):
            if game["game_id"] == "2026020199":
                raise ValueError("bad row")
            return real(game, *a, **k)
        with mock.patch.object(ec, "candidates_for_game", flaky):
            d = ec.build(NOW, games=[broken, game()], players_by_game={GID: col_players()}, product=product(), pool=None, availability=None, plan=plan(), primary={})
        self.assertEqual(d["daily_output"], ec.BEST)
        self.assertEqual(d["best_candidate"]["game_id"], GID)
        self.assertIn("bad row", next(e for e in d["examined"]["by_game"] if e.get("game_id") == "2026020199")["error"])               # reported, not silent


class TestMathAndRules(unittest.TestCase):
    def test_frechet_bounds(self):
        b = ec.frechet([0.7, 0.8, 0.9])
        self.assertAlmostEqual(b["low"], 0.4)
        self.assertAlmostEqual(b["high"], 0.7)
        self.assertEqual(ec.frechet([0.5, 0.5, 0.5])["low"], 0.0)
        self.assertIsNone(ec.frechet([0.5, None]))

    def test_the_support_score_is_a_geometric_mean_and_is_not_labelled_a_probability(self):
        legs = [{"estimate": 0.5}, {"estimate": 0.8}]
        self.assertAlmostEqual(ec.support_score(legs), (0.5 * 0.8) ** 0.5, places=4)
        self.assertIsNone(ec.support_score([{"estimate": 0.5}, {"estimate": None}]))
        self.assertIn("NOT a probability", evaluate()["support_note"])

    def test_nested_and_opposing_pairs_are_classified_honestly(self):
        a = {"label": "A points", "code": "PTS", "player_id": "1", "team": "COL"}
        b = {"label": "A goal", "code": "GOAL", "player_id": "1", "team": "COL"}
        self.assertEqual(ec.classify_pair(a, b)["kind"], "NESTED")
        c = {"label": "PHI shots", "code": "SOG", "player_id": "9", "team": "PHI"}
        self.assertEqual(ec.classify_pair({"label": "COL shots", "code": "SOG", "player_id": "2", "team": "COL"}, c)["direction"], "UNCLEAR")

    def test_stage_a_can_never_record_anything(self):
        import re
        src = inspect.getsource(ec)
        imports = "\n".join(l for l in src.splitlines() if re.match(r"\s*(from|import)\s", l))
        for banned in ("paper_bankroll", "daily_tickets", "prospective_ledger", "recording_pause", "ticket_policy", "paper_bet_settlement"):
            self.assertNotIn(banned, imports, banned)
        self.assertNotIn("record_paper_bet", src)

    def test_a_goal_leg_is_never_monitored_eligible_and_says_the_policy_does_not_use_it(self):
        c = ec.evaluate_combination(NOW, game=game(), sides_players=col_players(), pool_game=None, avail_game=None, plan=plan(), product=product(),
                                    specs=[{"code": "ML", "team": "COL"}, {"code": "GOAL", "player": "Nathan MacKinnon", "k": 1}])
        g = leg(c, "Nathan MacKinnon anytime goal")
        self.assertFalse(g["gates"]["recording_policy_uses_market"])
        self.assertFalse(g["monitored_eligible"])

    def test_a_leg_that_cannot_be_found_is_reported_not_dropped(self):
        c = ec.evaluate_combination(NOW, game=game(), sides_players=col_players(), pool_game=None, avail_game=None, plan=plan(), product=product(),
                                    specs=[{"code": "ML", "team": "COL"}, {"code": "SOG", "player": "Nobody Here", "k": 2}, {"code": "SOG", "player": "Cale Makar", "k": 2}])
        self.assertTrue(c["unresolved_legs"])
        self.assertIn("LEGS NOT FOUND", c["blockers"][0])


class TestBoardIntegration(unittest.TestCase):
    def test_a_failure_in_the_candidate_search_never_breaks_the_board(self):
        from tests.test_slate_board import doc
        with mock.patch.object(ec, "build", side_effect=RuntimeError("boom")):
            d = doc()
        self.assertEqual(d["eggy"]["daily_output"], "UNAVAILABLE")
        self.assertIn("boom", d["eggy"]["error"])
        self.assertTrue(d["games"])                                                                              # the board itself is complete

    def test_the_board_document_carries_the_stage_a_section(self):
        from tests.test_slate_board import doc
        d = doc()
        self.assertIn("eggy", d)
        self.assertIn(d["eggy"]["daily_output"], (ec.BEST, ec.PASS, ec.OFFICIAL))
        self.assertIn("availability", d["eggy"])


class TestThePageShowsStageA(unittest.TestCase):
    def page(self, eggy, primary=None):
        from tests.test_slate_board import doc
        s = snapshot()
        s["slate_board"] = doc()
        s["slate_board"]["eggy"] = dict(eggy, date_et="2026-10-15")              # the page tests open the board on 2026-10-15
        s["slate_board"]["primary"] = primary if primary is not None else {"status": "NONE", "ticket": None, "reason": "0 player-prop leg(s) in 0 game(s) with prop prices", "strongest_singles": []}
        at = run_page("41_Slate_Board.py", s)
        self.assertFalse(at.exception, at.exception)
        return " ".join(x.value for x in at.markdown) + " " + " ".join(x.value for x in at.caption) + " " + " ".join(x.label for x in at.expander)

    def eggy(self, primary=None):
        return ec.build(NOW, games=[game()], players_by_game={GID: col_players()}, product=product(), pool=None, availability=None, plan=plan(), primary=primary or {"status": "NONE", "ticket": None, "reason": "no ticket"})

    def test_the_best_candidate_card_names_the_thesis_the_legs_and_what_stops_certification(self):
        blob = self.page(self.eggy())
        for needle in ("NO CERTIFIED EGGY PICK TODAY", "COL controls the matchup", "COL to win", "Nathan MacKinnon 1+ point", "Nathan MacKinnon 2+ shots", "JOINT PROBABILITY NOT YET CERTIFIED",
                       "not available", "not certified", "Why it is not certified", "Why prices are missing", "Production recording policy"):
            self.assertIn(needle, blob, needle)
        self.assertNotIn("No Eggy pick today", blob)

    def test_the_availability_panel_explains_each_market_instead_of_saying_zero(self):
        blob = self.page(self.eggy())
        self.assertIn("not in credit plan", blob.lower())
        self.assertIn("awaiting wave", blob.lower())

    def test_an_official_pick_is_still_shown_by_the_original_card(self):
        e = self.eggy(primary={"status": "SELECTED", "ticket": {"ticket_id": "T1"}})
        self.assertEqual(e["daily_output"], ec.OFFICIAL)
        from tests.test_slate_board import doc
        s = snapshot()
        s["slate_board"] = doc()
        s["slate_board"]["eggy"] = dict(e, date_et="2026-10-15")
        with mock.patch("dashboard.ui.eggy_pick_card") as original:
            at = run_page("41_Slate_Board.py", s)
        self.assertFalse(at.exception, at.exception)
        original.assert_called()                                                                                 # the unchanged official card handles the official case

    def test_a_true_pass_names_what_was_examined(self):
        e = self.eggy()
        e.update(daily_output=ec.PASS, headline="TRUE PASS", best_candidate=None)
        blob = self.page(e)
        self.assertIn("TRUE PASS", blob)
        self.assertIn("none reaches the support bar", blob)


class TestReviewFixesDay(unittest.TestCase):
    def build(self, games=None, players=None, primary=None, now=NOW, prod=None):
        gs = games if games is not None else [game()]
        return ec.build(now, games=gs, players_by_game=players if players is not None else {g["game_id"]: col_players() for g in gs}, product=prod or product(), pool=None, availability=None, plan=plan(), primary=primary or {})

    def test_best_is_chosen_from_the_candidates_that_reach_the_bar_not_just_the_first_ranked(self):
        """A funded game below the bar must not turn the day into TRUE PASS while an unfunded candidate above it exists."""
        low, high = game(), game()
        low["game_id"], high["game_id"] = "2026020138", "2026020137"
        weak = copy.deepcopy(col_players())
        for p in weak["away"]:
            p["projection"]["probabilities"].update({"points>=1": 0.61, "shots>=2": 0.61})
        d = self.build(games=[low, high], players={"2026020138": weak, "2026020137": col_players()})
        self.assertEqual(d["daily_output"], ec.BEST)
        self.assertTrue(d["best_candidate"]["support_score"] >= ec.MIN_SUPPORT)

    def test_a_search_error_is_never_reported_as_a_true_pass(self):
        broken_prod = product()
        d = self.build(games=[game()], players={GID: {"away": [skater("X1", "No Stats", "COL", "C")], "home": []}})
        d["examined"]["by_game"]                                                                                  # a game without usable data simply has no thesis: that is a pass
        self.assertEqual(d["daily_output"], ec.PASS)
        real = ec.candidates_for_game
        with mock.patch.object(ec, "candidates_for_game", side_effect=RuntimeError("search failed")):
            d2 = self.build(games=[game()])
        self.assertEqual(d2["daily_output"], "UNAVAILABLE")
        self.assertIn("search failed", d2["error"])
        self.assertEqual(d2["examined"]["games_failed"], 1)
        del broken_prod, real

    def test_missing_expected_values_do_not_crash_the_search(self):
        pl = col_players()
        pl["away"][0]["projection"]["expected"] = {"points": None, "shots": None}
        d = self.build(players={GID: pl})
        self.assertIn(d["daily_output"], (ec.BEST, ec.PASS))
        self.assertFalse(d["examined"]["games_failed"])

    def test_a_candidate_with_an_unvalidated_leg_cannot_headline(self):
        prod = product()
        prod["model_health"]["skater_verdicts"]["points>=1"] = {"verdict": "DOES_NOT_BEAT_BASELINES"}
        d = self.build(prod=prod)
        self.assertTrue(all(c["all_legs_validated"] for c in ([d["best_candidate"]] if d["best_candidate"] else [])))
        self.assertNotEqual((d["best_candidate"] or {}).get("thesis", {}).get("key"), "STRONG_FAVORITE_STAR")

    def test_a_game_that_has_started_is_not_examined(self):
        d = self.build(now=dt.datetime(2026, 10, 19, 23, 5, tzinfo=dt.timezone.utc))
        self.assertEqual(d["examined"]["games"], 0)
        self.assertEqual(d["daily_output"], ec.PASS)
        self.assertEqual(d["availability"], {})

    def test_one_malformed_availability_row_does_not_remove_the_whole_search(self):
        real = ec.explain_markets

        def flaky(g, *a, **k):
            if g["game_id"] == "2026020199":
                raise ValueError("bad avail row")
            return real(g, *a, **k)
        broken = dict(game(), game_id="2026020199")
        with mock.patch.object(ec, "explain_markets", flaky):
            d = self.build(games=[broken, game()], players={"2026020199": col_players(), GID: col_players()})
        self.assertEqual(d["daily_output"], ec.BEST)
        self.assertEqual(d["examined"]["games_failed"], 1)

    def test_the_wave_window_states_after_the_last_chance_are_missed_not_due(self):
        rows = {r["market"]: r for r in ec.explain_markets(game(), None, plan(), None, None, dt.datetime(2026, 10, 19, 22, 50, tzinfo=dt.timezone.utc))}
        p = plan()
        p["waves"][0]["last_chance_utc"] = "2026-10-19T22:45:00Z"
        rows = {r["market"]: r for r in ec.explain_markets(game(), None, p, None, None, dt.datetime(2026, 10, 19, 22, 50, tzinfo=dt.timezone.utc))}
        self.assertEqual(rows["player_shots_on_goal_alternate"]["state"], "MISSED_WINDOW")
        self.assertEqual(rows["player_points"]["state"], "NOT_IN_CREDIT_PLAN")
        self.assertIn("add-on", rows["player_points"]["why"])


class TestReviewFixesPage(unittest.TestCase):
    def snap(self, eggy, primary):
        from tests.test_slate_board import doc
        s = snapshot()
        s["slate_board"] = doc()
        s["slate_board"]["eggy"] = eggy
        s["slate_board"]["primary"] = primary
        return s

    def eggy(self, **kw):
        e = ec.build(NOW, games=[game()], players_by_game={GID: col_players()}, product=product(), pool=None, availability=None, plan=plan(), primary={"status": "NONE", "ticket": None, "reason": "r"})
        e.update(kw)
        return e

    def test_an_official_ticket_in_the_primary_slot_is_never_hidden_by_a_candidate_document(self):
        e = self.eggy(date_et="2026-10-15")                                                                       # the candidate document is out of step with the tickets section
        with mock.patch("dashboard.ui.eggy_pick_card") as original:
            at = run_page("41_Slate_Board.py", self.snap(e, {"status": "SELECTED", "ticket": {"ticket_id": "T9", "legs": []}}))
        self.assertFalse(at.exception, at.exception)
        original.assert_called()
        blob = " ".join(x.value for x in at.markdown)
        self.assertNotIn("NO CERTIFIED EGGY PICK TODAY", blob)

    def test_a_candidate_document_from_another_day_is_not_shown_as_todays(self):
        e = self.eggy(date_et="2026-10-14")
        with mock.patch("dashboard.ui.eggy_pick_card") as original:
            at = run_page("41_Slate_Board.py", self.snap(e, {"status": "NONE", "ticket": None, "reason": "r"}))
        original.assert_called()
        self.assertIn("is for 2026-10-14, not today", " ".join(c.value for c in at.caption))
        self.assertNotIn("COL controls the matchup", " ".join(x.value for x in at.markdown))

    def test_prices_are_re_judged_when_the_page_is_opened_and_the_card_says_nothing_is_staked(self):
        e = self.eggy(date_et="2026-10-15")
        at = run_page("41_Slate_Board.py", self.snap(e, {"status": "NONE", "ticket": None, "reason": "r"}))
        blob = " ".join(x.value for x in at.markdown)
        self.assertIn("(quoted Oct 19, 7:58 AM ET; STALE)", blob)                                                  # opened at 2026-10-15 17:05Z, game on the 19th: far outside the limit
        self.assertIn("Nothing here is recorded or staked", " ".join(c.value for c in at.caption))

    def test_the_today_page_shows_the_stage_a_card_and_never_hides_the_official_ticket(self):
        from tests.test_slate_board import doc
        snap = snapshot()
        primary = (snap["tickets"].get("primary") or {})
        snap["slate_board"] = doc()
        snap["slate_board"]["eggy"] = self.eggy(date_et="2026-10-15")
        with mock.patch("dashboard.ui.eggy_candidate_card") as card:
            at = run_page("21_Today.py", snap)
        self.assertFalse(at.exception, at.exception)
        if primary:
            card.assert_called()
            args = card.call_args
            self.assertEqual(args[0][0]["daily_output"], ec.BEST)                                                # the board's stage-A document reaches the Today page's card
            self.assertEqual(args[0][1], primary)                                                                # together with the tickets section's own primary slot (the official ticket wins inside the card)

    def test_an_old_format_snapshot_still_renders_with_the_original_card(self):
        at = run_page("41_Slate_Board.py", self.snap({}, {"status": "NONE", "ticket": None, "reason": "r"}))
        self.assertFalse(at.exception, at.exception)
        self.assertIn("not in this snapshot yet", " ".join(c.value for c in at.caption))


if __name__ == "__main__":
    unittest.main()
