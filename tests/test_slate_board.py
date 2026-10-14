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

    def test_a_missing_board_says_so_instead_of_failing(self):
        s = snapshot()
        at = run_page("41_Slate_Board.py", s)
        self.assertFalse(at.exception, at.exception)


if __name__ == "__main__":
    unittest.main()
