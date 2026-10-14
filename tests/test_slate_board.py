"""The per-game board: four analyses for every game, what is priced and what is missing and why, the primary parlay, and the page that shows them."""
from __future__ import annotations

import copy
import datetime as dt
import unittest

from operational import slate_board as sb
from tests.product_fixture import GEN, builder_pool_doc, product_state, snapshot
from tests.test_product_pages import run_page

NOW = dt.datetime(2026, 10, 8, 22, 5, tzinfo=dt.timezone.utc)
Q = "2026-10-08T21:58:00Z"


def product() -> dict:
    p = product_state()
    p["et_today"] = "2026-10-08"
    pl = p["players"]
    pl["P1"]["projection"]["probabilities"]["goals>=1"] = 0.31
    pl["P2"]["projection"]["probabilities"]["goals>=1"] = 0.12
    pl["P3"]["projection"] = copy.deepcopy(pl["P1"]["projection"])             # P3 has no projection in the fixture; give it a thin-sample one that must be excluded
    pl["P3"]["games_total"] = 12
    return p


def pool() -> dict:
    d = builder_pool_doc()
    p1 = d["games"]["2026020900"]["players"]["P1"]["m"]
    p1["SOG"]["l"] = [[1, -4000.0, 0.93], [2, -135.0, 0.64], [3, 190.0, 0.30], [4, 520.0, 0.12]]
    p1["SOG"]["rec"] = {"1": [30, 30, 0.95, 5], "2": [24, 30, 0.70, 5], "3": [14, 30, 0.40, 5], "4": [6, 30, 0.18, 5]}
    p1["GOAL"] = {"q": Q, "r": Q, "l": [[1, 260.0, 0.28]]}
    return d


def avail(shots="POSTED", goals="NOT_POSTED") -> dict:
    return {"day": "2026-10-08", "games": {"2026020900": {"markets": {
        "player_shots_on_goal_alternate": {"status": shots, "checked_utc": Q, "last_update": Q, "outcomes": 40},
        "player_goal_scorer_anytime": {"status": goals, "checked_utc": Q, "last_update": None, "outcomes": 0}}}}}


def doc(tickets=None, saves=None, availability=None) -> dict:
    return sb.build_doc(NOW, product(), pool(), tickets or {}, availability if availability is not None else avail(), saves or {}, None)


class TestTheBoard(unittest.TestCase):
    def test_every_scheduled_game_is_on_the_board_and_counted(self):
        d = doc()
        self.assertEqual([g["game_id"] for g in d["games"]], ["2026020900"])
        self.assertEqual((d["counts"]["scheduled"], d["counts"]["analysed"], d["counts"]["shots_priced"], d["counts"]["goals_priced"], d["counts"]["moneyline_priced"]), (1, 1, 1, 0, 1))

    def test_scorers_are_ranked_by_chance_exclude_thin_samples_and_show_the_price_apart_from_the_rank(self):
        g = doc()["games"][0]
        names = [r["name"] for r in g["scorers"]]
        self.assertEqual(names[:2], ["Test Skater One", "Test Skater Two"])
        self.assertNotIn("Test Skater Three", names)                                  # 12 NHL games: not a stable sample
        top = g["scorers"][0]
        self.assertEqual(top["price"]["american"], 260.0)
        self.assertAlmostEqual(top["price"]["ev_estimated"], 0.31 * 3.6 - 1.0, places=3)
        self.assertLess(top["price"]["ev_after_haircut"], top["price"]["ev_estimated"])
        self.assertIsNone(g["scorers"][1]["price"])                                    # no price: shown by chance, no value claimed

    def test_the_shots_pick_balances_chance_and_price_and_skips_a_heavy_one_plus(self):
        picks = doc()["games"][0]["shots"]["picks"]
        self.assertEqual(len(picks), 1)                                                # only one priced eligible skater in this fixture: the shortfall is stated, not invented
        chosen = picks[0]["chosen"]
        self.assertEqual(chosen["threshold"], 2)                                       # not the -4000 1+ and not the 4+ longshot
        self.assertAlmostEqual(chosen["ev_after_haircut"], (0.70 - 0.03) * (1 + 100 / 135) - 1.0, places=3)
        self.assertEqual([x["threshold"] for x in picks[0]["ladder"]], [1, 2, 3, 4])
        self.assertIn("shortfall", doc()["games"][0]["shots"])
        self.assertTrue(doc()["games"][0]["shots"]["shortfall"])

    def test_no_prices_means_no_pick_and_the_reason_says_not_posted_or_not_fetched(self):
        p = pool()
        p["games"]["2026020900"]["players"]["P1"]["m"].pop("SOG")
        d = sb.build_doc(NOW, product(), p, {}, avail(shots="NOT_POSTED"), {}, None)
        g = d["games"][0]
        self.assertEqual(g["shots"]["picks"], [])
        self.assertEqual(g["coverage"]["markets"]["player_shots_on_goal_alternate"]["word"], "not posted by DraftKings")
        missing = {m["item"]: m["reason"] for m in g["coverage"]["missing"]}
        self.assertIn("Shots prices", missing)
        self.assertEqual(sb._market_status({"markets": {}}, "player_total_saves")["status"], "NOT_FETCHED")
        self.assertEqual(sb._market_status({"markets": {"x": {"status": "BUDGET_BLOCKED"}}}, "x")["word"], "budget blocked")

    def test_an_unconfirmed_starter_gets_a_conditional_projection_and_no_saves_selection(self):
        saves = {"2026020900": {"G1": {"name": "Test Goalie One", "line": 26.5, "over": -115.0, "under": -105.0, "quote_updated_utc": Q}}}
        d = doc(saves=saves)
        b = [x for x in d["games"][0]["goalies"] if x["team"] == "AAA"][0]
        self.assertEqual(b["starter"]["status"], "UNCONFIRMED")
        self.assertIsNotNone(b["projection"])
        self.assertEqual(b["line"]["line"], 26.5)
        self.assertEqual(b["line"]["p_over"], 0.5)                                     # saves>=27 in the fixture ladder
        self.assertTrue(b["recommendation"]["conditional"])
        self.assertIsNone(b["recommendation"]["side"])
        self.assertEqual(sb._saves_over_probability({"saves_probabilities": {"saves>=27": 0.4}}, 26.5), 0.4)
        self.assertIsNone(sb._saves_over_probability({"saves_probabilities": {}}, 40.5))

    def test_a_confirmed_starter_with_value_gets_a_side_only_when_the_model_is_validated(self):
        p = product()
        p["goalies"]["G1"]["confirmation"] = {"status": "CONFIRMED", "source": "team post", "checked_at_utc": Q, "note": ""}
        p["goalies"]["G1"]["projection"]["saves_probabilities"]["saves>=27"] = 0.75
        saves = {"2026020900": {"G1": {"name": "Test Goalie One", "line": 26.5, "over": 110.0, "under": -130.0, "quote_updated_utc": Q}}}
        p["model_health"]["models"] = [{"id": "goalie-saves", "validation": {"summary": {"markets_scored": 16, "beating_baselines": 16}}}]
        d = sb.build_doc(NOW, p, pool(), {}, avail(), saves, None)
        b = [x for x in d["games"][0]["goalies"] if x["team"] == "AAA"][0]
        self.assertEqual(b["recommendation"]["side"], "OVER")
        p["model_health"]["models"] = [{"id": "goalie-saves", "validation": {"summary": {"markets_scored": 16, "beating_baselines": 10}}}]
        d2 = sb.build_doc(NOW, p, pool(), {}, avail(), saves, None)
        self.assertIsNone([x for x in d2["games"][0]["goalies"] if x["team"] == "AAA"][0]["recommendation"]["side"])

    def test_the_side_is_the_moneyline_with_the_exact_price_or_no_recommended_side(self):
        g = doc()["games"][0]["side"]
        self.assertIsNone(g["recommendation"])                                         # -120 / +100: the favourite is about 52% on its own no-vig price
        self.assertIn("coin flip", g["reason"])
        self.assertEqual(g["puck_line"]["status"], "NOT_ASSESSED")
        p = product()
        for game in p["games"]["games"]:
            if game["game_id"] == "2026020900":
                game["moneyline"]["home"]["american"], game["moneyline"]["away"]["american"] = -200.0, 170.0
                game["win_probability"] = {"home": 0.66, "away": 0.34, "model": "m"}
        rec = sb.build_doc(NOW, p, pool(), {}, avail(), {}, None)["games"][0]["side"]["recommendation"]
        self.assertEqual((rec["team"], rec["american"], rec["market"]), ("AAA", -200.0, "Moneyline"))
        for game in p["games"]["games"]:
            if game["game_id"] == "2026020900":
                game["win_probability"] = {"home": 0.45, "away": 0.55, "model": "m"}
        again = sb.build_doc(NOW, p, pool(), {}, avail(), {}, None)["games"][0]["side"]
        self.assertIsNone(again["recommendation"])                                      # the model disagrees with the market's favourite
        self.assertIn("does not", again["reason"])

    def test_the_primary_slip_is_passed_through_untouched(self):
        t = {"primary": {"status": "FRESH", "ticket": {"ticket_id": "T1"}, "reason": "x"}, "ticket_policy": {"policy_id": "primary-slip-2026-10-14"}, "recording_paused": True}
        d = doc(tickets=t)
        self.assertEqual(d["primary"]["ticket"]["ticket_id"], "T1")
        self.assertTrue(d["recording_paused"])


class TestThePage(unittest.TestCase):
    def snap(self, **kw) -> dict:
        s = snapshot()
        s["slate_board"] = doc(**kw)
        return s

    def test_renders_with_the_primary_slot_the_games_and_no_exception(self):
        at = run_page("41_Slate_Board.py", self.snap())
        self.assertFalse(at.exception, at.exception)
        blob = " ".join(x.value for x in at.markdown) + " " + " ".join(x.value for x in at.caption) + " " + " ".join(x.label for x in at.expander) + " " + " ".join(h.value for h in at.subheader)
        self.assertIn("Primary parlay", blob)
        self.assertIn("BBB @ AAA", blob)
        self.assertIn("No primary parlay qualifies", blob)
        self.assertIn("Test Skater One", blob)

    def test_a_missing_board_says_so_instead_of_failing(self):
        s = snapshot()
        at = run_page("41_Slate_Board.py", s)
        self.assertFalse(at.exception, at.exception)


if __name__ == "__main__":
    unittest.main()
