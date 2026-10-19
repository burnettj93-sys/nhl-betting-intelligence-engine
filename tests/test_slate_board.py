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
    for game in p["games"]["games"]:
        if game.get("moneyline"):
            for side in ("home", "away"):
                game["moneyline"][side]["quote_captured_at_utc"] = Q              # five minutes before NOW: fresh
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
        self.assertEqual(g["coverage"]["markets"]["player_shots_on_goal_alternate"]["word"], "not posted by DraftKings (asked at 5:58 PM ET)")
        missing = {m["item"]: m["reason"] for m in g["coverage"]["missing"]}
        self.assertIn("Shots prices", missing)
        self.assertEqual(sb._market_status({"markets": {}}, "player_total_saves")["status"], "NOT_FETCHED")
        yesterday = {"markets": {"player_goal_scorer_anytime": {"status": "NOT_POSTED", "checked_utc": "2026-10-07T20:17:00Z"}}}
        word = sb._market_status(yesterday, "player_goal_scorer_anytime", NOW)["word"]                     # asked the evening before: not today's answer
        self.assertIn("earlier day", word)
        today_ = {"markets": {"player_goal_scorer_anytime": {"status": "NOT_POSTED", "checked_utc": "2026-10-08T17:00:00Z"}}}
        self.assertEqual(sb._market_status(today_, "player_goal_scorer_anytime", NOW)["word"], "not posted by DraftKings (asked at 1:00 PM ET)")
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
        self.assertEqual(g["puck_line"]["status"], "NOT_ON_FILE")                  # no spreads price: said separately from the model and the contract
        self.assertEqual(g["puck_line"]["model"]["status"], "UNVALIDATED")
        self.assertEqual(g["puck_line"]["contract"]["status"], "UNCERTIFIED")
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


    def test_a_moneyline_quote_older_than_the_freshness_limit_is_never_recommended(self):
        p = product()
        for game in p["games"]["games"]:
            if game["game_id"] == "2026020900":
                game["moneyline"]["home"].update(american=-200.0, quote_captured_at_utc="2026-10-08T17:00:00Z", status="ACTIVE")      # five hours old, still the latest row
                game["moneyline"]["away"].update(american=170.0, quote_captured_at_utc="2026-10-08T17:00:00Z", status="ACTIVE")
                game["win_probability"] = {"home": 0.66, "away": 0.34, "model": "m"}
        side = sb.build_doc(NOW, p, pool(), {}, avail(), {}, None)["games"][0]["side"]
        self.assertIsNone(side["recommendation"])
        self.assertIn("freshness limit", side["reason"])
        self.assertFalse(side["fresh"])

    def test_an_unloadable_policy_is_stated_on_the_board_and_the_fallback_haircut_is_labelled(self):
        from unittest import mock
        from operational import ticket_policy
        prod, pl_, av = product(), pool(), avail()
        with mock.patch.object(ticket_policy, "active", side_effect=RuntimeError("policy file damaged")):
            d = sb.build_doc(NOW, prod, pl_, {}, av, {}, None)
        self.assertIn("policy file damaged", d["policy_error"])
        self.assertEqual(d["haircut"], sb.FALLBACK_HAIRCUT)
        ok = sb.build_doc(NOW, prod, pl_, {}, av, {}, None)
        self.assertIsNone(ok["policy_error"])

    def test_a_goalie_projection_with_missing_numbers_does_not_break_the_board(self):
        p = product()
        p["goalies"]["G1"]["projection"].update(expected_shots_against=None, save_pct_used=None, expected_saves=None, expected_goals_against=None, saves_range_80=None)
        d = sb.build_doc(NOW, p, pool(), {}, avail(), {}, None)
        b = [x for x in d["games"][0]["goalies"] if x["team"] == "AAA"][0]
        self.assertIn("—", b["projection"]["explanation"])

    def test_one_failing_game_is_isolated_marked_and_counted_and_the_others_still_update(self):
        from unittest import mock
        p = product()
        second = copy.deepcopy(p["games"]["games"][0])
        second["game_id"] = "2026020911"
        p["games"]["games"].append(second)
        p["game_details"]["2026020911"] = {**p["game_details"]["2026020900"], "game_id": "2026020911"}
        real = sb.shots_picks

        def flaky(game, players, pool_game, haircut=None):
            if game["game_id"] == "2026020911":
                raise ValueError("bad ladder row")
            return real(game, players, pool_game, haircut)
        with mock.patch.object(sb, "shots_picks", flaky):
            d = sb.build_doc(NOW, p, pool(), {}, avail(), {}, None)
        by = {g["game_id"]: g for g in d["games"]}
        self.assertEqual(d["counts"]["failed_games"], 1)
        self.assertIn("bad ladder row", by["2026020911"]["error"])
        self.assertTrue(by["2026020900"]["scorers"])                                        # the healthy game is complete
        self.assertNotIn("error", by["2026020900"])
        self.assertEqual(d["counts"]["scheduled"], 2)
        s = snapshot()
        s["slate_board"] = d
        at = run_page("41_Slate_Board.py", s)
        self.assertFalse(at.exception, at.exception)
        self.assertIn("analysis of this game failed", " ".join(e.value for e in at.error))
        self.assertIn("could not be analysed", " ".join(w.value for w in at.warning))

    def test_a_goalie_without_a_projection_is_unavailable_not_estimated(self):
        p = product()
        p["goalies"]["G1"]["projection"] = None
        b = [x for x in sb.build_doc(NOW, p, pool(), {}, avail(), {}, None)["games"][0]["goalies"] if x["team"] == "AAA"][0]
        self.assertIsNone(b["projection"])
        self.assertIn("No projection is available", b["projection_unavailable"])
        self.assertIsNone(b["recommendation"]["side"])

    def test_the_primary_slip_is_passed_through_untouched(self):
        t = {"primary": {"status": "FRESH", "ticket": {"ticket_id": "T1"}, "reason": "x"}, "ticket_policy": {"policy_id": "primary-slip-2026-10-14"}, "recording_paused": True}
        d = doc(tickets=t)
        self.assertEqual(d["primary"]["ticket"]["ticket_id"], "T1")
        self.assertTrue(d["recording_paused"])


class TestTheLivePath(unittest.TestCase):
    def test_saves_lines_are_read_from_the_archived_capture_for_any_goalie_and_matched_by_name(self):
        from unittest import mock
        payload = {"bookmakers": [{"key": "draftkings", "last_update": Q, "markets": [{"key": "player_total_saves", "last_update": Q, "outcomes": [
            {"name": "Over", "description": "Test Goalie One", "point": 26.5, "price": -115},
            {"name": "Under", "description": "Test Goalie One", "point": 26.5, "price": -105},
            {"name": "Over", "description": "Someone Unknown", "point": 20.5, "price": 100}]}]}]}
        with mock.patch("operational.best_bets.latest_capture", return_value=(NOW, payload)):
            by_name = sb._saves_lines(pool(), NOW)
        self.assertEqual(by_name["2026020900"]["test goalie one"]["line"], 26.5)
        self.assertEqual((by_name["2026020900"]["test goalie one"]["over"], by_name["2026020900"]["test goalie one"]["under"]), (-115.0, -105.0))
        by_id = sb._by_goalie_id(by_name, product()["goalies"])
        self.assertEqual(set(by_id["2026020900"]), {"G1"})                                         # the unknown name is not matched to anyone


    def test_malformed_and_foreign_outcomes_are_skipped_and_never_matched_to_the_wrong_goalie(self):
        from unittest import mock
        payload = {"bookmakers": [
            {"key": "fanduel", "markets": [{"key": "player_total_saves", "last_update": Q, "outcomes": [{"name": "Over", "description": "Test Goalie One", "point": 99.5, "price": -110}]}]},
            {"key": "draftkings", "last_update": Q, "markets": [
                {"key": "player_total_saves", "last_update": Q, "outcomes": [
                    {"name": "Over", "description": "Test Goalie One", "point": 26.5},                                 # no price
                    {"name": "Over", "description": "Test Goalie One", "price": -110},                                  # no line
                    {"name": "Over", "point": 26.5, "price": -110},                                                    # no player
                    {"name": "Over", "description": "Test Goalie Two", "point": 25.5, "price": -120},                  # over only: no under on file
                    {"name": "Test Goalie One", "description": "Test Goalie One", "point": 26.5, "price": -110}]},      # neither side
                {"key": "player_points", "outcomes": [{"name": "Over", "description": "Test Goalie One", "point": 0.5, "price": 100}]}]}]}
        with mock.patch("operational.best_bets.latest_capture", return_value=(NOW, payload)):
            by_name = sb._saves_lines(pool(), NOW)
        self.assertEqual(set(by_name["2026020900"]), {"test goalie two"})                      # the other bookmaker, the incomplete rows and the other market are ignored
        self.assertEqual(by_name["2026020900"]["test goalie two"]["over"], -120.0)
        self.assertNotIn("under", by_name["2026020900"]["test goalie two"])
        with mock.patch("operational.best_bets.latest_capture", return_value=None):
            self.assertEqual(sb._saves_lines(pool(), NOW), {})                                  # no archived capture: no lines, no error
        errs: dict = {}
        with mock.patch("operational.best_bets.latest_capture", side_effect=OSError("archive unreadable")):
            self.assertEqual(sb._saves_lines(pool(), NOW, errs), {})
        self.assertIn("archive unreadable", errs["2026020900"])                                  # an unreadable archive is reported, not mistaken for "no line posted"
        d_err = sb.build_doc(NOW, product(), pool(), {}, avail(), {}, None, errs)
        self.assertIn("could not be read", [m for m in d_err["games"][0]["coverage"]["missing"] if m["item"] == "Saves line"][0]["reason"])
        self.assertIn("2026020900", d_err["input_errors"])
        d = sb.build_doc(NOW, product(), pool(), {}, avail(), sb._by_goalie_id(by_name, {"G2": {"name": "Test Goalie Two"}}), None)
        b = [x for x in d["games"][0]["goalies"] if x["team"] == "BBB"][0]
        self.assertEqual(b["line"]["line"], 25.5)
        self.assertIsNone(b["line"]["ev_under"])                                               # no under price: no under value is invented

    def test_a_line_posted_only_for_the_alternate_goalie_is_shown_as_a_conditional_view_with_the_note(self):
        """2026-10-17 PIT at VGK: DraftKings priced Adin Hill (our 36% alternate) and no one else; the board showed no saves line at all."""
        from tests.product_fixture import goalie
        p = product()
        game = p["games"]["games"][0]
        game["goalies"]["home"].append({"player_id": "G3", "name": "Test Goalie Three", "start_probability": 0.3, "status": "UNCONFIRMED"})
        p["goalies"]["G3"] = goalie("G3", "Test Goalie Three", "AAA", opp="BBB")
        saves = {"2026020900": {"G3": {"name": "Test Goalie Three", "line": 25.5, "over": -105.0, "under": -130.0, "quote_updated_utc": Q}}}
        d = sb.build_doc(NOW, p, pool(), {}, avail(), saves, None)
        b = [x for x in d["games"][0]["goalies"] if x["team"] == "AAA"][0]
        self.assertIsNone(b["line"])                                                           # the expected starter has no line
        alt = b["alternate"]
        self.assertEqual((alt["name"], alt["status"]), ("Test Goalie Three", "UNCONFIRMED"))
        self.assertEqual(alt["line"]["line"], 25.5)
        self.assertEqual(alt["line"]["p_over"], 0.5)                                           # his own projection ladder (saves>=26)
        self.assertEqual(alt["line"]["ev_under"], round(sb._ev_haircut(0.5, -130.0, d["haircut"]), 4))
        self.assertEqual(alt["projection"]["expected_saves"], 26.2)
        self.assertTrue(alt["recommendation"]["conditional"])
        self.assertIsNone(alt["recommendation"]["side"])                                       # no selection while no start is confirmed
        self.assertIn("DraftKings posted a saves line for Test Goalie Three", b["book_line_note"])
        self.assertIn("nothing is recommended until a start is confirmed", b["book_line_note"])

    def test_a_confirmed_alternate_still_gets_only_a_conditional_view(self):
        from tests.product_fixture import goalie
        p = product()
        p["games"]["games"][0]["goalies"]["home"].append({"player_id": "G3", "name": "Test Goalie Three", "start_probability": 0.3, "status": "CONFIRMED"})
        g3 = goalie("G3", "Test Goalie Three", "AAA", opp="BBB")
        g3["confirmation"] = {"status": "CONFIRMED", "source": "team", "checked_at_utc": Q, "note": "x"}
        g3["projection"]["saves_probabilities"]["saves>=26"] = 0.9
        p["goalies"]["G3"] = g3
        p["model_health"]["models"] = [{"id": "goalie-saves", "validation": {"summary": {"markets_scored": 16, "beating_baselines": 16}}}]
        saves = {"2026020900": {"G3": {"name": "Test Goalie Three", "line": 25.5, "over": 150.0, "under": -190.0, "quote_updated_utc": Q}}}
        d = sb.build_doc(NOW, p, pool(), {}, avail(), saves, None)
        alt = [x for x in d["games"][0]["goalies"] if x["team"] == "AAA"][0]["alternate"]
        self.assertIsNone(alt["recommendation"]["side"])                                       # the page cannot show a side that the slip does not use

    def test_the_alternate_line_renders_on_the_hosted_page(self):
        from tests.product_fixture import goalie
        p = product()
        p["games"]["games"][0]["goalies"]["home"].append({"player_id": "G3", "name": "Test Goalie Three", "start_probability": 0.3, "status": "UNCONFIRMED"})
        p["goalies"]["G3"] = goalie("G3", "Test Goalie Three", "AAA", opp="BBB")
        saves = {"2026020900": {"G3": {"name": "Test Goalie Three", "line": 25.5, "over": -105.0, "under": -130.0, "quote_updated_utc": Q}}}
        s = snapshot()
        s["slate_board"] = sb.build_doc(NOW, p, pool(), {}, avail(), saves, None)
        at = run_page("41_Slate_Board.py", s)
        self.assertEqual(len(at.exception), 0, [str(e.value)[:300] for e in at.exception])
        blob = " ".join([m.value for m in at.markdown] + [c.value for c in at.caption] + [i.value for i in at.info])
        self.assertIn("DraftKings line for Test Goalie Three", blob)
        self.assertIn("DraftKings posted a saves line for Test Goalie Three", blob)

    def test_refresh_reads_the_published_inputs_writes_the_board_and_reports_a_failed_game(self):
        import json, tempfile
        from pathlib import Path
        from unittest import mock
        from operational import state_paths
        payload = {"bookmakers": [{"key": "draftkings", "last_update": Q, "markets": [{"key": "player_total_saves", "last_update": Q, "outcomes": [
            {"name": "Over", "description": "Test Goalie One", "point": 26.5, "price": -115}, {"name": "Under", "description": "Test Goalie One", "point": 26.5, "price": -105}]}]}]}
        prod = product()
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(state_paths, "path", lambda name, **kw: Path(d) / name), \
                    mock.patch("operational.product_data.read_state", return_value=prod), mock.patch("operational.builder_pool.load", return_value=pool()), \
                    mock.patch("operational.daily_tickets.read_state", return_value={"primary": {"status": "NONE", "ticket": None, "reason": "r"}, "recording_paused": True}), \
                    mock.patch("operational.price_availability.read", return_value=avail()), mock.patch("operational.best_bets.latest_capture", return_value=(NOW, payload)):
                res = sb.refresh(NOW)
                doc = json.loads((Path(d) / "slate_board.json").read_text())
                self.assertEqual(sb.load()["date_et"], "2026-10-08")
        self.assertEqual(res["status"], "OK")
        self.assertEqual(doc["counts"]["failed_games"], 0)
        b = [x for x in doc["games"][0]["goalies"] if x["team"] == "AAA"][0]
        self.assertEqual(b["line"]["over"], -115.0)                                              # read from the archived capture and matched by name to G1
        self.assertTrue(b["recommendation"]["conditional"])                                      # the starter is unconfirmed: still no selection
        self.assertTrue(doc["recording_paused"])
        self.assertEqual(doc["primary"]["status"], "NONE")

    def test_the_boards_after_haircut_value_follows_the_policy_and_the_ticket_helper(self):
        import dataclasses
        from unittest import mock
        from operational import daily_tickets, ticket_policy
        from research.real_market_parlay import policy as P
        wider = dataclasses.replace(P.PROPOSED, value_shade=0.06)
        prod, pl_, av = product(), pool(), avail()                                          # (built before the patch: the fixture itself reads the active policy)
        base = sb.build_doc(NOW, prod, pl_, {}, av, {}, None)["games"][0]["scorers"][0]["price"]["ev_after_haircut"]
        with mock.patch.object(ticket_policy, "active", return_value=wider):
            shifted = sb.build_doc(NOW, prod, pl_, {}, av, {}, None)["games"][0]["scorers"][0]["price"]["ev_after_haircut"]
        self.assertLess(shifted, base)
        self.assertAlmostEqual(shifted, daily_tickets.haircut_ev([0.31], sb._dec(260.0), 0.06), places=6)

    def test_the_previous_day_scan_is_incremental_restart_safe_and_rescans_a_replaced_log(self):
        import json, tempfile
        from pathlib import Path
        from unittest import mock
        from operational import state_paths

        def rec(day, game, family):
            return json.dumps({"et_date": day, "pool": [[game, "P", family]]}, separators=(",", ":")) + "\n"
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "selection_shadow.jsonl"
            with mock.patch.object(state_paths, "path", lambda name, **kw: Path(d) / name):
                log.write_text(rec("2026-10-07", "g1", "PLAYER_SOG_ALTERNATE") + rec("2026-10-06", "gx", "MONEYLINE"))
                self.assertEqual(sb._day_games_in_log("2026-10-07"), ({"g1"}, set()))
                first_offset = json.loads((Path(d) / sb.SCAN_STATE).read_text())["offset"]
                self.assertEqual(first_offset, log.stat().st_size)
                with open(log, "a") as fh:                                                   # new records, then a half-written one
                    fh.write(rec("2026-10-07", "g2", "MONEYLINE"))
                    fh.write('{"et_date":"2026-10-07","pool":[["g3","P","PLAYER_SOG_ALT')
                with mock.patch("builtins.open", wraps=open) as opened:
                    shots, ml = sb._day_games_in_log("2026-10-07")
                self.assertEqual((shots, ml), ({"g1"}, {"g2"}))                               # the incomplete line waits for the next refresh
                self.assertTrue(any(c.args and str(c.args[0]).endswith("selection_shadow.jsonl") for c in opened.call_args_list))
                with open(log, "a") as fh:
                    fh.write('GA_ALTERNATE"]]}\n')
                self.assertEqual(sb._day_games_in_log("2026-10-07"), ({"g1", "g3"}, {"g2"}))  # the half-written record is picked up once complete, and not before
                log.write_text(rec("2026-10-07", "h9", "PLAYER_SOG_ALTERNATE"))              # a replaced, shorter log is rescanned from the start
                self.assertEqual(sb._day_games_in_log("2026-10-07"), ({"h9"}, set()))
                self.assertEqual(sb._day_games_in_log("2026-10-08"), (set(), set()))          # a new day starts clean

    def test_the_previous_day_check_reads_only_that_days_records(self):
        import json, tempfile
        from pathlib import Path
        from unittest import mock
        from operational import state_paths
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "selection_shadow.jsonl").write_text(
                json.dumps({"et_date": "2026-10-07", "pool": [["2026020899", "P1", "PLAYER_SOG_ALTERNATE"], ["2026020899", "TOR", "MONEYLINE"]]}, separators=(",", ":")) + "\n"
                + json.dumps({"et_date": "2026-10-06", "pool": [["x", "P", "PLAYER_SOG_ALTERNATE"]]}, separators=(",", ":")) + "\n")
            p = product()
            p["games"]["games"].append({**p["games"]["games"][0], "game_id": "2026020899", "date_et": "2026-10-07", "state": "FINAL"})
            with mock.patch.object(state_paths, "path", lambda name, **kw: Path(d) / name):
                r = sb._previous_day_check(p, "2026-10-08")
        self.assertEqual((r["scheduled"], r["shots_legs_evaluated_in_games"], r["moneyline_legs_evaluated_in_games"]), (1, 1, 1))


class TestArchivedRealPayloads(unittest.TestCase):
    """ARCHIVED verification only: real DraftKings payloads the engine archived on 2026-10-08 (saves) and 2026-10-10 (anytime goals), trimmed to the DraftKings market. This proves the parsers and the
    identity matching against the real shapes; it is NOT evidence of what is posted today (that is the live hosted check)."""
    def load(self, name):
        import json
        from pathlib import Path
        return json.loads((Path(__file__).parent / "fixtures" / "real_payloads" / name).read_text())

    def test_the_real_saves_payload_gives_both_goalies_their_line_and_prices_and_matches_by_name(self):
        from unittest import mock
        payload = self.load("saves_BOS_UTA_2026-10-08.json")
        with mock.patch("operational.best_bets.latest_capture", return_value=(NOW, payload)):
            by_name = sb._saves_lines({"games": {"G": {"event_id": payload["event"]["id"]}}}, NOW)
        self.assertEqual(set(by_name["G"]), {"jeremy swayman", "sebastian cossa"})
        sway = by_name["G"]["jeremy swayman"]
        self.assertEqual((sway["line"], sway["over"], sway["under"]), (24.5, -115.0, -115.0))
        self.assertEqual(by_name["G"]["sebastian cossa"]["line"], 23.5)
        by_id = sb._by_goalie_id(by_name, {"8480280": {"name": "Jeremy Swayman"}, "8482657": {"name": "Sebastian Cossa"}, "1": {"name": "Someone Else"}})
        self.assertEqual(set(by_id["G"]), {"8480280", "8482657"})

    def test_the_real_goals_payload_parses_with_the_builders_reader_and_prices_a_scorer_on_the_board(self):
        from operational import builder_pool as bp
        payload = self.load("goals_2026-10-10.json")
        rows = [(upd, o) for upd, outs in bp._outcomes({"bookmakers": payload["bookmakers"]}, "player_goal_scorer_anytime") for o in outs]
        self.assertEqual(len(rows), 35)
        self.assertTrue(all(o["name"] == "Yes" and o.get("price") is not None for _, o in rows))
        upd, pastrnak = [r for r in rows if r[1]["description"] == "David Pastrnak"][0]
        self.assertEqual(pastrnak["price"], 135)
        # the same price, as the pool stores it, is what the board shows beside the model's chance
        p = product()
        pl = pool()
        pl["games"]["2026020900"]["players"]["P1"]["m"]["GOAL"] = {"q": upd, "r": upd, "l": [[1, float(pastrnak["price"]), None]]}
        row = sb.build_doc(NOW, p, pl, {}, avail(goals="POSTED"), {}, None)["games"][0]["scorers"][0]
        self.assertEqual(row["price"]["american"], 135.0)
        self.assertAlmostEqual(row["price"]["ev_estimated"], 0.31 * 2.35 - 1.0, places=3)


class TestMissedWindowsAreShownOnTheBoard(unittest.TestCase):
    def test_a_missed_pregame_window_is_listed_as_missing_with_its_reason(self):
        a = avail()
        a["games"]["2026020900"]["missed_windows"] = [{"window": "PREGAME", "tags": ["MISSED_WINDOW", "MACHINE_ASLEEP"], "detail": "the machine was not running for the end of the capture window"}]
        d = sb.build_doc(NOW, product(), pool(), {}, a, {}, None)
        miss = [m for m in d["games"][0]["coverage"]["missing"] if m["item"] == "Pregame price capture"]
        self.assertEqual(len(miss), 1)
        self.assertIn("window missed", miss[0]["reason"])
        self.assertIn("MACHINE_ASLEEP", miss[0]["tags"])

    def test_a_saves_line_that_is_missing_says_prices_are_collected_without_a_confirmed_starter(self):
        d = doc()
        reason = [m for m in d["games"][0]["coverage"]["missing"] if m["item"] == "Saves line"][0]["reason"]
        self.assertIn("whether or not a starter is confirmed", reason)
        self.assertIn("still needs a confirmed starter", reason)


class TestAnalysisIsSeparatedFromPricedRecommendations(unittest.TestCase):
    def test_each_scheduled_game_says_whether_it_has_fresh_prices_stale_prices_or_none(self):
        d = doc()
        self.assertIn(d["games"][0]["coverage"]["price_state"], ("FRESH_PRICES", "STALE_PRICES"))     # the fixture game has a shots quote
        none = sb.build_doc(NOW, product(), pool(), {}, avail(shots="BUDGET_BLOCKED"), {}, None)
        p2 = pool()
        p2["games"]["2026020900"]["players"] = {}
        no_prices = sb.build_doc(NOW, product(), p2, {}, avail(shots="BUDGET_BLOCKED"), {}, None)["games"][0]["coverage"]
        self.assertEqual(no_prices["price_state"], "ANALYSIS_ONLY")
        self.assertIn("no player prices", no_prices["price_state_word"])

    def test_a_started_game_is_not_applicable(self):
        p = product()
        for game in p["games"]["games"]:
            game["state"] = "FINAL"
        d = sb.build_doc(NOW, p, pool(), {}, avail(), {}, None)
        self.assertEqual(d["games"][0]["coverage"]["price_state"], "NOT_APPLICABLE")


class TestShotsPicksDoNotFavourAnUnstableEdge(unittest.TestCase):
    def player(self, pid, name):
        return {"player_id": pid, "name": name, "team": "AAA", "position": "C", "projection": {"pricing_eligible": True, "expected": {"toi": 18.0, "shots": 2.4}, "probabilities": {}}, "games_total": 120,
                "recent_avg": {"shots": 2.3}, "season": {"games": 8}, "prior_season": {"games": 70, "goals_per_game": 0.3, "shots_per_game": 2.4}}

    def pool(self, ladders):
        return {"players": {pid: {"m": {"SOG": {"q": "2026-10-08T21:58:00Z", "r": "2026-10-08T21:59:00Z", "l": [[k, price, model] for k, price, model, _ in lad],
                                                  "rec": {str(k): rec for k, _, _, rec in lad}}}} for pid, lad in ladders.items()}}

    def test_a_short_noisy_record_with_a_big_apparent_edge_loses_to_a_stable_one(self):
        ps = [self.player("stable", "Stable Sam"), self.player("noisy", "Noisy Nate"), self.player("third", "Third Tom")]
        ladders = {"stable": [(2, -140, 0.60, [20, 30, 0.62, 8])],                       # 20 of 30, estimate 0.62 at -140 (implies 0.58): a small, real edge
                   "noisy": [(2, 150, 0.33, [5, 10, 0.55, 8])],                          # 5 of 10 games, estimate 0.55 at +150 (implies 0.40): a huge apparent edge from 10 games
                   "third": [(2, -120, 0.55, [17, 30, 0.56, 8])]}
        out = sb.shots_picks({"home": "AAA", "away": "BBB"}, ps, self.pool(ladders), 0.03)
        names = [c["name"] for c in out["picks"]]
        self.assertEqual(names[0], "Stable Sam")
        self.assertNotEqual(names[0], "Noisy Nate")
        noisy = next((c for c in out["picks"] if c["name"] == "Noisy Nate"), None)
        if noisy:
            self.assertTrue(noisy["unstable"])
            self.assertFalse(noisy["has_value"])                                         # never presented as value
            self.assertIn("unstable", noisy["explanation"])

    def test_value_must_survive_one_standard_error_to_count(self):
        ps = [self.player("a", "Alpha")]
        marginal = {"a": [(2, 105, 0.50, [15, 30, 0.50, 8])]}                            # +105 implies 0.488: an edge far inside the noise
        pick = sb.shots_picks({"home": "AAA", "away": "BBB"}, ps, self.pool(marginal), 0.0)["picks"][0]
        self.assertGreater(pick["chosen"]["ev_after_haircut"], -0.05)
        self.assertLessEqual(pick["chosen"]["ev_low"], pick["chosen"]["ev_after_haircut"])
        self.assertFalse(pick["has_value"])


class TestPuckLineIsAssessedInThreeSeparateParts(unittest.TestCase):
    def game(self, pl):
        return {"home": "AAA", "away": "BBB", "start_utc": "2026-10-08T23:00:00Z", "puck_line": pl, "moneyline": None, "win_probability": {}}

    def test_a_quoted_spread_is_shown_with_its_price_and_never_recommended(self):
        pl = {"status": "POSTED", "home_point": -1.5, "home_price": 210, "away_point": 1.5, "away_price": -265, "quote_updated_at_utc": "2026-10-08T21:50:00Z", "quote_captured_at_utc": "2026-10-08T21:51:00Z", "age_min": 9.0}
        b = sb.puck_line_block(self.game(pl), NOW)
        self.assertEqual(b["status"], "QUOTED_NOT_ASSESSED")
        self.assertEqual((b["price"]["home"]["point"], b["price"]["home"]["american"], b["price"]["away"]["american"]), (-1.5, 210, -265))
        self.assertIn("-1.5", b["text"])
        self.assertIn("no cover probability is used", b["text"])                            # the model fact
        self.assertIn("not yet certified", b["text"])                                       # the contract fact, separate from the price
        self.assertIn("moneyline", b["text"])

    def test_a_missing_price_is_a_price_fact_not_a_model_fact(self):
        for status, words in (("NOT_POSTED", "no spreads market"), ("NO_STANDARD_LINE", "non-standard"), ("MALFORMED", "could not be read")):
            b = sb.puck_line_block(self.game({"status": status, "reason": "x"}), NOW)
            self.assertIsNone(b["price"])
            self.assertIn(words, b["text"])
            self.assertEqual(b["model"]["status"], "UNVALIDATED")

    def test_no_pull_yet_says_the_pull_has_not_returned_one(self):
        b = sb.puck_line_block(self.game(None), NOW)
        self.assertEqual(b["status"], "NOT_ON_FILE")
        self.assertIn("has not returned one yet", b["text"])


class TestStartedGamesKeepTheirPregameAnalysis(unittest.TestCase):
    def test_a_game_that_has_started_keeps_its_frozen_pregame_block_and_is_not_a_recommendation(self):
        before = doc()
        self.assertTrue(before["games"][0]["coverage"]["analysis_available"])
        p = product()
        for game in p["games"]["games"]:
            if game["game_id"] == "2026020900":
                game["state"] = "LIVE"
        after = sb.build_doc(NOW + dt.timedelta(hours=3), p, pool(), {}, avail(), {}, None, previous_doc=before)
        g = after["games"][0]
        self.assertEqual(g["state"], "LIVE")
        self.assertEqual(g["frozen_at_utc"], before["generated_at_utc"])
        self.assertIn("frozen at puck drop", g["coverage"]["price_state_word"])                   # a started game never claims "fresh prices"
        self.assertTrue(g["coverage"]["analysis_available"])
        self.assertEqual(g["coverage"]["recommended"], [])
        self.assertEqual([r["name"] for r in g["scorers"]], [r["name"] for r in before["games"][0]["scorers"]])
        self.assertEqual(after["counts"]["analysed"], 1)
        again = sb.build_doc(NOW + dt.timedelta(hours=4), p, pool(), {}, avail(), {}, None, previous_doc=after)
        self.assertEqual(again["games"][0]["frozen_at_utc"], before["generated_at_utc"])         # the freeze time never moves

    def test_the_frozen_block_carries_no_live_recommendation_fields(self):
        before = doc()
        g0 = before["games"][0]
        g0["side"]["recommendation"] = {"team": "AAA", "american": -150}           # pretend the pregame board had a side pick and a saves pick
        g0["goalies"][0]["recommendation"] = {"side": "OVER", "text": "Over 24.5: value +8%", "conditional": False}
        if g0["scorers"]:
            g0["scorers"][0]["best_value_at_price"] = True
        p = product()
        for game in p["games"]["games"]:
            if game["game_id"] == "2026020900":
                game["state"] = "STARTED"
        frozen = sb.build_doc(NOW + dt.timedelta(hours=3), p, pool(), {}, avail(), {}, None, previous_doc=before)["games"][0]
        self.assertIsNone(frozen["side"]["recommendation"])
        self.assertIsNone(frozen["goalies"][0]["recommendation"]["side"])
        self.assertTrue(frozen["goalies"][0]["recommendation"]["conditional"])
        self.assertFalse(any(r.get("best_value_at_price") for r in frozen["scorers"]))
        self.assertFalse(any(pk.get("has_value") for pk in frozen["shots"]["picks"]))
        self.assertTrue(g0["goalies"][0]["recommendation"]["side"] == "OVER")       # the source document is not mutated

    def test_a_postponed_game_and_bad_or_foreign_previous_documents_never_freeze_or_crash(self):
        before = doc()
        p = product()
        for game in p["games"]["games"]:
            if game["game_id"] == "2026020900":
                game["state"] = "POSTPONED"
        d = sb.build_doc(NOW, p, pool(), {}, avail(), {}, None, previous_doc=before)
        self.assertNotIn("frozen_at_utc", d["games"][0])
        for state in ("STARTED",):
            for game in p["games"]["games"]:
                if game["game_id"] == "2026020900":
                    game["state"] = state
        other_day = {**before, "date_et": "2020-01-01"}
        for bad in (None, other_day, {"date_et": before["date_et"], "games": ["junk", 3, None]}, "not a dict"):
            d = sb.build_doc(NOW, p, pool(), {}, avail(), {}, None, previous_doc=bad)
            self.assertNotIn("frozen_at_utc", d["games"][0])

    def test_a_game_goes_scheduled_to_started_to_final_across_three_builds_with_one_freeze_time(self):
        first = doc()
        p = product()
        for game in p["games"]["games"]:
            if game["game_id"] == "2026020900":
                game["state"] = "STARTED"
        second = sb.build_doc(NOW + dt.timedelta(hours=3), p, pool(), {}, avail(), {}, None, previous_doc=first)
        for game in p["games"]["games"]:
            if game["game_id"] == "2026020900":
                game["state"] = "FINAL"
        third = sb.build_doc(NOW + dt.timedelta(hours=6), p, pool(), {}, avail(), {}, None, previous_doc=second)
        self.assertEqual(third["games"][0]["state"], "FINAL")
        self.assertEqual(third["games"][0]["frozen_at_utc"], first["generated_at_utc"])

    def test_without_a_saved_pregame_block_a_started_game_is_listed_with_the_reason(self):
        p = product()
        for game in p["games"]["games"]:
            if game["game_id"] == "2026020900":
                game["state"] = "FINAL"
        d = sb.build_doc(NOW, p, pool(), {}, avail(), {}, None)
        self.assertFalse(d["games"][0]["coverage"]["analysis_available"])
        self.assertNotIn("frozen_at_utc", d["games"][0])


def _dated_for_the_page(board: dict) -> dict:
    """The page tests open the board on 2026-10-15; the stage-A search only shows for the day it was built for (tests/test_eggy_candidates.py covers a mismatch)."""
    board["eggy"]["date_et"] = "2026-10-15"
    return board


class TestThePage(unittest.TestCase):
    def snap(self, **kw) -> dict:
        s = snapshot()
        s["slate_board"] = _dated_for_the_page(doc(**kw))
        return s

    def test_renders_with_the_primary_slot_the_games_and_no_exception(self):
        at = run_page("41_Slate_Board.py", self.snap())
        self.assertFalse(at.exception, at.exception)
        blob = " ".join(x.value for x in at.markdown) + " " + " ".join(x.value for x in at.caption) + " " + " ".join(x.label for x in at.expander) + " " + " ".join(h.value for h in at.subheader)
        self.assertIn("Eggy's Parlay Pick of the Day", blob)
        self.assertIn("BBB @ AAA", blob)
        self.assertIn("TRUE PASS", blob)                                                                # this one-game fixture has no thesis that reaches the support bar: the stage-A card replaced the bare "No Eggy pick today"
        self.assertNotIn("No Eggy pick today", blob)
        self.assertIn("Test Skater One", blob)

    def test_a_recommendation_that_has_aged_past_its_limit_is_withdrawn_when_the_page_is_opened(self):
        from tests.test_product_pages import FIXTURE_NOW
        p = product()
        for game in p["games"]["games"]:
            if game["game_id"] == "2026020900":
                game["moneyline"]["home"].update(american=-200.0, quote_captured_at_utc=(FIXTURE_NOW - dt.timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ"))
                game["moneyline"]["away"].update(american=170.0, quote_captured_at_utc=(FIXTURE_NOW - dt.timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ"))
                game["win_probability"] = {"home": 0.66, "away": 0.34, "model": "m"}
        built_at = FIXTURE_NOW - dt.timedelta(minutes=5)
        s = snapshot()
        s["slate_board"] = sb.build_doc(built_at, p, pool(), {}, avail(), {}, None)
        self.assertIsNotNone(s["slate_board"]["games"][0]["side"]["recommendation"])       # fresh when built
        at = run_page("41_Slate_Board.py", s)                                              # the page is "opened" at FIXTURE_NOW: still fresh
        self.assertFalse(at.exception, at.exception)
        self.assertNotIn("Not a current recommendation", " ".join(x.value for x in at.markdown))
        old = (FIXTURE_NOW - dt.timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        s["slate_board"]["games"][0]["side"]["sides"]["away"]["quote_captured_at_utc"] = old      # only the OTHER side aged out: the estimate rests on both quotes
        at = run_page("41_Slate_Board.py", s)
        self.assertIn("Not a current recommendation", " ".join(x.value for x in at.markdown))

    def _ticket(self, **kw):
        legs = [{"label": "Test Skater One 2+ shots", "market_family": "PLAYER_SOG_ALTERNATE", "game_id": "2026020900", "participant_id": "P1", "participant_name": "Test Skater One",
                 "american_price": -110.0, "decimal_price": 1.909, "probability": 0.64, "team": "AAA", "opponent": "BBB", "game_start_utc": "2026-10-08T23:00:00Z"},
                {"label": "COL to win", "market_family": "MONEYLINE", "game_id": "2026020901", "participant_id": "COL", "participant_name": "COL", "american_price": -130.0,
                 "decimal_price": 1.769, "probability": 0.58, "team": "COL", "opponent": "CGY", "game_start_utc": "2026-10-08T23:30:00Z"}]
        t = {"ticket_id": "T1", "status": "RECOMMENDED", "recorded": False, "legs": legs, "combined_decimal": 3.377, "combined_american": 238.0, "stake": 10.0, "potential_return": 33.77,
             "potential_profit": 23.77, "hit_probability": 0.37, "rationale": "r", "proposed": True, "anchor": {"hit_low": 0.31, "hit_high": 0.43, "rationale": "x"}}
        t.update(kw)
        return t

    def test_the_eggy_card_shows_the_single_primary_ticket_with_prices_chance_stake_and_return(self):
        s = snapshot()
        s["slate_board"] = doc(tickets={"primary": {"status": "FRESH", "ticket": self._ticket(), "reason": "Built on fresh prices.", "price_kind": "CALCULATED",
                                                    "explanation": "2 legs (moneyline, shots on goal) from 2 different games, priced at +238; the estimated hit chance is 37% against 30% implied by that price.",
                                                    "strongest_singles": []}})
        at = run_page("41_Slate_Board.py", s)
        self.assertFalse(at.exception, at.exception)
        blob = (" ".join(x.value for x in at.markdown) + " " + " ".join(x.value for x in at.caption)).replace("&#36;", "$").replace("&#x27;", "'")
        for want in ("Eggy's Parlay Pick of the Day", "+238", "$10.00", "$33.77", "37%", "Test Skater One 2+ shots", "COL to win", "same ticket as the primary parlay", "Proposed, not recorded"):
            self.assertIn(want, blob)
        self.assertNotIn("No Eggy pick today", blob)
        for banned in ("guarantee", "slam dunk", " lock", "sure thing"):
            self.assertNotIn(banned, blob.lower())
        self.assertIn("data:image/webp;base64", blob)                                                  # Eggy's artwork is on the card

    def test_a_data_limit_is_shown_beside_the_reason_when_price_coverage_is_thin(self):
        s = snapshot()
        s["slate_board"] = doc(tickets={"primary": {"status": "NONE", "ticket": None, "reason": "No combination qualifies.", "strongest_singles": [],
                                                    "data_limit": {"note": "Data limit: shots prices are on file for 1 of 4 games today (3 credit budget). An empty slip here may reflect thin price coverage as well as the rules; the rules were not loosened."}}})
        at = run_page("41_Slate_Board.py", s)
        self.assertFalse(at.exception, at.exception)
        self.assertIn("Data limit: shots prices are on file for 1 of 4 games", " ".join(w.value for w in at.warning))

    def test_with_no_ticket_the_card_says_no_eggy_pick_and_gives_the_reason_without_inventing_one(self):
        s = snapshot()
        s["slate_board"] = _dated_for_the_page(doc(tickets={"primary": {"status": "NONE", "ticket": None, "reason": "No combination reaches +100 to +500 with value after the haircut.", "strongest_singles": []}}))
        at = run_page("41_Slate_Board.py", s)
        blob = " ".join(x.value for x in at.markdown) + " " + " ".join(x.value for x in at.caption)
        self.assertIn("TRUE PASS", blob)                                                          # stage A replaced the bare "No Eggy pick today"; no ticket is invented either way
        self.assertIn("after the haircut", blob)                                                  # the production policy's own reason is still shown, labelled as such
        self.assertIn("Production recording policy", blob)
        self.assertNotIn("To return", blob)

    def test_the_closest_combination_is_displayed_with_its_label(self):
        s = snapshot()
        s["slate_board"] = doc(tickets={"primary": {"status": "NONE", "ticket": None, "reason": "r", "strongest_singles": [], "closest": {
            "legs": [{"participant": "Test Skater One", "threshold": 2, "market_family": "PLAYER_SOG_ALTERNATE", "american_price": -200}, {"participant": "COL", "threshold": None, "market_family": "MONEYLINE", "american_price": -148}],
            "combined_american": 238, "hit_probability": 0.45, "ev_estimated": 0.081, "ev_after_haircut": -0.013, "status": "FRESH", "note": "The closest combination ... It does NOT qualify: shown so the analysis is not hidden."}}})
        at = run_page("41_Slate_Board.py", s)
        self.assertFalse(at.exception, at.exception)
        blob = " ".join(x.value for x in at.markdown) + " " + " ".join(x.value for x in at.caption)
        self.assertIn("Closest combination (does not qualify)", blob)
        self.assertIn("Test Skater One 2+", blob)
        self.assertIn("COL to win", blob)

    def test_a_missing_board_says_so_instead_of_failing(self):
        s = snapshot()
        at = run_page("41_Slate_Board.py", s)
        self.assertFalse(at.exception, at.exception)


if __name__ == "__main__":
    unittest.main()
