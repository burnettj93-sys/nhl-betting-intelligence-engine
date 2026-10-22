"""Review cards on Eggy's own legs (operational/eggy_reviews.py): display only. Eggy's selection, ranking and policy are untouched; the legs of the official pick and of the best candidate gain a `review` key."""
from __future__ import annotations

import copy
import datetime as dt
import inspect
import unittest
from unittest import mock

from operational import eggy_intelligence as ei
from operational import eggy_reviews as er
from tests import test_eggy_intelligence as t

NOW = dt.datetime(2026, 10, 20, 21, 0, tzinfo=dt.timezone.utc)
GAMES = {"1": {"home": "AAA", "away": "BBB", "start_utc": "2026-10-20T23:00:00"}}


def ticket_prop_leg(**kw):
    base = {"label": "Test Player 2+ shots on goal", "market_family": "PLAYER_SOG_ALTERNATE", "game_id": "1", "participant_id": "9", "participant_name": "Test Player", "threshold": 2, "side": "OVER", "team": "AAA",
            "opponent": "BBB", "american_price": -130.0, "probability": 0.72, "model_version": "m", "quote_updated_utc": "2026-10-20T20:50:00Z", "price_captured_at_utc": "2026-10-20T20:51:00Z",
            "evidence": {"kind": "player_record", "hits": 25, "games": 30, "model": 0.7, "recent_hit_rate": 0.8}}
    base.update(kw)
    return base


def ticket_ml_leg(**kw):
    base = {"label": "AAA to win", "market_family": "MONEYLINE", "game_id": "1", "participant_id": "AAA", "participant_name": "AAA", "team": "AAA", "opponent": "BBB", "american_price": -250.0, "probability": 0.70,
            "evidence": {"kind": "market"}}
    base.update(kw)
    return base


def candidate_leg(**kw):
    base = {"kind": "PLAYER", "code": "PTS", "market_key": "player_points", "label": "Test Player 1+ point", "participant": "Test Player", "player_id": "9", "team": "AAA", "threshold": 1, "game_id": "1",
            "price": {"american": 150.0, "quote_updated_utc": "2026-10-20T20:50:00Z"}, "model_probability": 0.5, "estimate": 0.5, "estimate_basis": "MODEL_PROBABILITY_UNANCHORED"}
    base.update(kw)
    return base


class TestLegFromDict(unittest.TestCase):
    def test_a_frozen_ticket_leg_of_each_kind_becomes_a_reviewable_leg(self):
        leg, est, basis = er.leg_from_dict(ticket_prop_leg(), GAMES)
        self.assertEqual((leg.market_family, leg.participant_id, leg.threshold, leg.american_price, leg.team, leg.opponent, est, basis), ("PLAYER_SOG_ALTERNATE", "9", 2, -130.0, "AAA", "BBB", 0.72, "TICKET_ESTIMATE"))
        self.assertEqual((leg.model_probability, leg.empirical_probability, leg.empirical_hits, leg.empirical_games), (0.7, 0.8, 25, 30))
        ml, est, basis = er.leg_from_dict(ticket_ml_leg(), GAMES)
        self.assertEqual((ml.side, ml.market_probability, est, basis), ("HOME", 0.70, 0.70, "MARKET_NO_VIG"))
        away, _, _ = er.leg_from_dict(ticket_ml_leg(participant_id="BBB", participant_name="BBB", team="BBB", opponent="AAA"), GAMES)
        self.assertEqual(away.side, "AWAY")

    def test_a_leg_frozen_before_thresholds_were_stored_is_read_from_its_label(self):
        old = ticket_prop_leg()
        del old["threshold"], old["side"]
        leg, _, _ = er.leg_from_dict(old, GAMES)
        self.assertEqual((leg.threshold, leg.side), (2, "OVER"))
        goal = ticket_prop_leg(market_family="PLAYER_GOALS", label="X to score a goal (anytime)")
        del goal["threshold"]
        self.assertEqual(er.leg_from_dict(goal, GAMES)[0].threshold, 1)

    def test_a_candidate_leg_is_read_through_its_code_market_key_and_price_block(self):
        leg, est, basis = er.leg_from_dict(candidate_leg(), GAMES)
        self.assertEqual((leg.market_family, leg.participant_name, leg.american_price, leg.opponent, est, basis), ("PLAYER_POINTS", "Test Player", 150.0, "BBB", 0.5, "MODEL_PROBABILITY_UNANCHORED"))
        by_key = candidate_leg()
        del by_key["code"]
        self.assertEqual(er.leg_from_dict(by_key, GAMES)[0].market_family, "PLAYER_POINTS")

    def test_a_leg_with_no_price_market_game_or_estimate_is_not_reviewed_and_never_guessed(self):
        self.assertIsNone(er.leg_from_dict(candidate_leg(price=None), GAMES))
        self.assertIsNone(er.leg_from_dict(ticket_prop_leg(game_id="99"), GAMES))
        self.assertIsNone(er.leg_from_dict(ticket_prop_leg(market_family=None, code=None, market_key=None), GAMES))
        self.assertIsNone(er.leg_from_dict(candidate_leg(estimate=None), GAMES))


class TestReviewLegs(unittest.TestCase):
    def ctx(self):
        c = t.make_ctx()
        c.skater_state = {"players": {"9": t.skater()}, "newest_game_date": "2026-10-19"}
        c.by_player = {"9": t.history_rows()}
        c.games = GAMES
        return c

    def test_each_reviewable_leg_gains_a_card_and_nothing_else_changes(self):
        legs = [ticket_prop_leg(), ticket_ml_leg(), ticket_prop_leg(game_id="99")]
        before = copy.deepcopy(legs)
        with mock.patch("research.product_models.live.project_matchup", return_value=t.projection(0.72)):
            n = er.review_legs(self.ctx(), legs)
        self.assertEqual(n, 2)
        for after, orig in zip(legs, before):
            self.assertEqual({k: v for k, v in after.items() if k != "review"}, orig)                    # the leg is exactly as it was
        self.assertIn("review", legs[0])
        self.assertIn("review", legs[1])
        self.assertNotIn("review", legs[2])
        self.assertEqual(legs[0]["review"]["bet"]["label"], "Test Player — SHOTS ON GOAL 2+ — -130")
        self.assertEqual(legs[1]["review"]["bet"]["label"], "AAA — MONEYLINE — -250")
        self.assertNotIn("answers", legs[0]["review"])                                                    # the published copy is slim: the answers are rebuilt from the card
        self.assertEqual(set(ei.standard_answers(legs[0]["review"])), set(t.ANSWER_KEYS))

    def test_a_pass_on_an_eggy_leg_is_shown_not_hidden(self):
        legs = [ticket_ml_leg(participant_id="BBB", participant_name="BBB", team="BBB", opponent="AAA", american_price=300.0, probability=0.25)]
        er.review_legs(self.ctx(), legs)
        self.assertEqual(legs[0]["review"]["action"]["decision"], "PASS")                                 # a fair-priced underdog: the review passes, and says so on the card


class TestAttach(unittest.TestCase):
    PRODUCT = {"games": {"games": [{"game_id": "1", "home": "AAA", "away": "BBB", "start_utc": "2026-10-20T23:00:00"}]}}

    def test_nothing_is_built_when_there_are_no_legs(self):
        with mock.patch.object(ei, "build_context", side_effect=AssertionError("built a context")):
            self.assertEqual(er.attach(NOW, self.PRODUCT, {"ticket": None}, {"best_candidate": None})["reviewed"], 0)
            self.assertEqual(er.attach(NOW, self.PRODUCT, None, None)["reviewed"], 0)

    def test_the_official_ticket_and_the_best_candidate_are_both_reviewed(self):
        primary = {"ticket": {"legs": [ticket_ml_leg()]}}
        eggy = {"best_candidate": {"legs": [candidate_leg()]}, "other_candidates": [{"legs": [candidate_leg()]}]}
        ctx = TestReviewLegs().ctx()
        with mock.patch.object(ei, "build_context", return_value=ctx), mock.patch("research.product_models.live.project_matchup", return_value=t.projection(0.5, market="points>=1")):
            out = er.attach(NOW, self.PRODUCT, primary, eggy)
        self.assertEqual(out["reviewed"], 2)
        self.assertIn("review", primary["ticket"]["legs"][0])
        self.assertIn("review", eggy["best_candidate"]["legs"][0])
        self.assertNotIn("review", eggy["other_candidates"][0]["legs"][0])                               # the board stays small: only the pick and the best candidate

    def test_an_unusable_review_context_leaves_the_legs_alone_and_says_why(self):
        primary = {"ticket": {"legs": [ticket_ml_leg()]}}
        with mock.patch.object(ei, "build_context", return_value=t.make_ctx(evidence=None)):
            out = er.attach(NOW, self.PRODUCT, primary, None)
        self.assertEqual(out["reviewed"], 0)
        self.assertIn("could not run", out["reason"])
        self.assertNotIn("review", primary["ticket"]["legs"][0])

    def test_attach_never_raises(self):
        with mock.patch.object(ei, "build_context", side_effect=RuntimeError("boom")):
            out = er.attach(NOW, self.PRODUCT, {"ticket": {"legs": [ticket_ml_leg()]}}, None)
        self.assertEqual(out["reviewed"], 0)
        self.assertIn("boom", out["reason"])

    def test_it_never_touches_eggys_selection_policy_or_ledger(self):
        src = inspect.getsource(er)
        for forbidden in ("daily_tickets", "ticket_policy", "paper_bankroll", "record_paper_bet", "policy_digest", "sqlite3"):
            self.assertNotIn(forbidden, src)


class TestSlateBoardRefreshAttaches(unittest.TestCase):
    def test_refresh_calls_attach_with_the_primary_and_the_candidate_and_survives_its_failure(self):
        import json
        import tempfile
        from pathlib import Path
        from operational import slate_board as sb
        from operational import state_paths
        from tests.test_slate_board import NOW as SB_NOW, avail, pool, product
        prod = product()
        for attach, expect in ((lambda now, p, primary, eggy: {"reviewed": 3}, {"reviewed": 3}), (mock.Mock(side_effect=RuntimeError("x")), None)):
            with tempfile.TemporaryDirectory() as d:
                with mock.patch.object(state_paths, "path", lambda name, **kw: Path(d) / name), mock.patch("operational.product_data.read_state", return_value=prod), \
                        mock.patch("operational.builder_pool.load", return_value=pool()), mock.patch("operational.daily_tickets.read_state", return_value={"primary": {"status": "NONE", "ticket": None, "reason": "r"}}), \
                        mock.patch("operational.price_availability.read", return_value=avail()), mock.patch("operational.best_bets.latest_capture", return_value=None), \
                        mock.patch.object(er, "attach", attach):
                    res = sb.refresh(SB_NOW)
                    doc = json.loads((Path(d) / "slate_board.json").read_text())
            self.assertEqual(res["status"], "OK")
            self.assertEqual(doc.get("eggy_reviews"), expect)


class TestThePageShowsTheReviews(unittest.TestCase):
    def test_opening_an_eggy_leg_shows_the_card_and_says_the_pick_policy_is_unchanged(self):
        from tests.product_fixture import snapshot
        from tests.test_product_pages import run_page
        from tests.test_slate_board import doc
        ctx = TestReviewLegs().ctx()
        legs = [ticket_ml_leg(), ticket_prop_leg()]
        with mock.patch("research.product_models.live.project_matchup", return_value=t.projection(0.72)):
            er.review_legs(ctx, legs)
        tk = {"ticket_id": "T1", "status": "RECOMMENDED", "recorded": False, "legs": legs, "combined_decimal": 3.1, "combined_american": 210.0, "stake": 10.0, "potential_return": 31.0, "potential_profit": 21.0,
              "hit_probability": 0.5, "rationale": "r", "proposed": True, "anchor": {"hit_low": 0.4, "hit_high": 0.55, "rationale": "x"}}
        s = snapshot()
        s["slate_board"] = doc(tickets={"primary": {"status": "FRESH", "ticket": tk, "reason": "ok", "price_kind": "CALCULATED", "explanation": "e", "strongest_singles": []}})
        at = run_page("41_Slate_Board.py", s)
        self.assertFalse(at.exception, at.exception)
        blob = " ".join(x.value for x in at.markdown) + " " + " ".join(x.value for x in at.caption) + " " + " ".join(str(e.label) for e in at.expander)
        for needle in ("Open a leg to see why", "AAA — MONEYLINE — -250", "WHY THIS PRICE", "WHAT WOULD MAKE EGGY PASS", "chosen by its own policy, which this does not change"):
            self.assertIn(needle, blob)


if __name__ == "__main__":
    unittest.main()
