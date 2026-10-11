"""Anchor tickets: parlays from well-supported legs (a shooter's own record, a moneyline at the market's chance), ticket-level value at the real price, ranked by hit chance."""
from __future__ import annotations

import dataclasses
import datetime as dt
import os
import unittest
from unittest import mock

from operational import best_bets
from research.product_models import live
from research.real_market_parlay import anchor
from research.real_market_parlay import engine as rmp
from research.real_market_parlay import policy as P

NOW = dt.datetime(2026, 10, 11, 16, 0, tzinfo=dt.timezone.utc)
POL = P.PROPOSED


def shots(game, pid, hits, price, model, *, games=30, k=2, name=None):
    emp = round((hits + 8 * 0.45) / (games + 8), 4)
    return rmp.ParlayLeg(game_id=str(game), event_id=None, market_family="PLAYER_SOG_ALTERNATE", participant_id=str(pid), participant_name=name or f"Shooter{pid}", side="OVER",
                         threshold=k, american_price=price, conservative_probability=model, sportsbook="draftkings", captured_at_utc=NOW.isoformat(),
                         provider_contract_verified=True, model_threshold_eligible=True, identity_resolved=True, price_fresh=True, event_not_started=True,
                         team="T", opponent="O", game_start_utc="2026-10-11T23:00:00Z", model_probability=model, empirical_probability=emp, empirical_hits=hits, empirical_games=games)


def moneyline(game, team, price, market_p, elo=None):
    return rmp.ParlayLeg(game_id=str(game), event_id=None, market_family="MONEYLINE", participant_id=team, participant_name=team, side="HOME", threshold=None, american_price=price,
                         conservative_probability=elo if elo is not None else market_p, sportsbook="draftkings", captured_at_utc=NOW.isoformat(), provider_contract_verified=True,
                         model_threshold_eligible=True, identity_resolved=True, price_fresh=True, event_not_started=True, team=team, opponent="O",
                         game_start_utc="2026-10-11T23:00:00Z", model_probability=elo, market_probability=market_p)


class TestLegEstimate(unittest.TestCase):
    def test_a_shots_leg_uses_the_players_record_not_only_the_model(self):
        leg = shots(1, 1, hits=26, price=-260, model=0.695)
        est = anchor.leg_estimate(leg)
        self.assertAlmostEqual(est.p, leg.empirical_probability)
        self.assertGreater(est.p, leg.model_probability)
        self.assertIn("26 of his last 30 games", est.basis)

    def test_the_record_cannot_run_more_than_ten_points_above_the_model(self):
        leg = shots(1, 1, hits=29, price=-390, model=0.60)
        self.assertAlmostEqual(anchor.leg_estimate(leg).p, 0.70)
        self.assertIn("held to the model", anchor.leg_estimate(leg).basis)

    def test_thin_samples_and_weak_legs_are_not_well_supported(self):
        self.assertIsNone(anchor.leg_estimate(shots(1, 1, hits=14, price=-120, model=0.5, games=15)))     # 15 games behind it
        self.assertIsNone(anchor.leg_estimate(shots(1, 1, hits=18, price=-110, model=0.55)))             # about 58%: under the 60% a "strong" leg needs
        self.assertIsNone(anchor.leg_estimate(dataclasses.replace(shots(1, 1, 26, -260, 0.7), empirical_probability=None)))

    def test_a_moneyline_is_estimated_at_the_markets_own_chance_and_only_if_elo_agrees(self):
        ml = moneyline(1, "TOR", -150, 0.58, elo=0.57)
        self.assertEqual(anchor.leg_estimate(ml).p, 0.58)
        self.assertEqual(anchor.leg_estimate(moneyline(1, "TOR", -150, 0.62, elo=0.54)).p, 0.62)       # Elo is less confident than the market (it always is): not a disagreement
        self.assertIsNone(anchor.leg_estimate(moneyline(1, "TOR", -150, 0.58, elo=0.46)))              # Elo has the other side as the favourite
        self.assertIsNone(anchor.leg_estimate(moneyline(1, "TOR", 120, 0.45, elo=0.45)))               # an underdog is not a supported leg

    def test_points_and_goal_legs_are_never_used(self):
        for fam in ("PLAYER_POINTS", "PLAYER_GOALS"):
            leg = dataclasses.replace(shots(1, 1, 28, -150, 0.8), market_family=fam, threshold=1)
            self.assertIsNone(anchor.leg_estimate(leg))

    def test_the_pool_carries_the_estimate_and_keeps_the_models_number_beside_it(self):
        pool = anchor.prepare_pool([shots(1, 1, 26, -260, 0.695)])
        self.assertEqual(len(pool), 1)
        self.assertAlmostEqual(pool[0].conservative_probability, round(pool[0].empirical_probability, 4))
        self.assertEqual(pool[0].model_probability, 0.695)
        self.assertIn("anchor-last30", pool[0].model_version)

    def test_a_leg_that_gives_up_more_than_a_bookmakers_margin_is_left_out(self):
        self.assertEqual(anchor.prepare_pool([shots(1, 1, 22, -500, 0.7)]), [])                          # about 73% at a price implying 83%


class TestSelection(unittest.TestCase):
    def pick(self, legs, **kw):
        return rmp.select_tickets(legs, max_tickets=5, policy=POL, **kw)

    def test_two_consistent_shooters_make_a_plus_100_ticket_ranked_with_its_evidence(self):
        out = self.pick([shots(1, 1, 24, -185, 0.63, name="Ehlers"), shots(2, 2, 26, -260, 0.695, name="McMann")])
        self.assertEqual(len(out["tickets"]), 1)
        t = out["tickets"][0]
        self.assertGreaterEqual(t.combined_decimal, 2.0)
        self.assertGreater(t.joint_probability, 0.5)
        self.assertGreaterEqual(t.ev_estimated, POL.min_estimated_ev)

    def test_legs_from_one_game_are_never_combined(self):
        out = self.pick([shots(1, 1, 26, -150, 0.7), shots(1, 2, 26, -150, 0.7)])
        self.assertEqual(out["tickets"], [])

    def test_value_must_exist_at_the_real_price(self):
        # the player's record equals what the price implies: no value, so no ticket
        out = self.pick([shots(1, 1, 22, -200, 0.6), shots(2, 2, 22, -200, 0.6)])
        self.assertEqual(out["tickets"], [])
        self.assertIn("No ticket today", out["reason"])

    def test_the_empty_board_says_why_with_the_best_near_miss(self):
        out = self.pick([shots(1, 1, 24, -185, 0.70), shots(2, 2, 24, -185, 0.70), shots(3, 3, 22, -150, 0.6)])
        if not out["tickets"]:
            self.assertIn("reach +100", out["reason"])

    def test_the_midpoint_test_sits_between_the_record_and_the_price(self):
        leg = anchor.prepare_pool([shots(1, 1, 30, +100, 0.70)])[0]                                 # estimate 0.80 (held to model +10 points), price implies 0.50
        self.assertAlmostEqual(anchor.midpoint_probability(leg), (0.80 + 0.50) / 2, places=2)
        ml = moneyline(2, "BOS", -130, 0.56, elo=0.56)
        self.assertEqual(anchor.midpoint_probability(anchor.prepare_pool([ml])[0]), 0.56)           # a moneyline's estimate already is the market's
        two = anchor.prepare_pool([shots(1, 1, 30, +100, 0.70), shots(2, 2, 30, +100, 0.70)])
        self.assertLess(anchor.midpoint_ev(two), (0.80 * 0.80) * 4.0 - 1.0)

    def test_a_moneyline_can_complete_a_ticket_but_not_make_one_alone(self):
        legs = [shots(1, 1, 26, -190, 0.65), moneyline(2, "BOS", -120, 0.53, elo=0.54), moneyline(3, "NYR", -140, 0.56, elo=0.56)]
        out = self.pick(legs)
        for t in out["tickets"]:
            self.assertTrue(any(l.market_family != "MONEYLINE" for l in t.legs))
        self.assertEqual(self.pick([moneyline(2, "BOS", -120, 0.55, elo=0.56), moneyline(3, "NYR", -125, 0.56, elo=0.56)])["tickets"], [])

    def test_three_legs_are_used_only_when_two_cannot_reach_plus_100(self):
        legs = [shots(g, g, 27, -300, 0.70) for g in (1, 2, 3)]                                   # 1.33 x 1.33 = 1.78 (not +100); three legs = 2.37
        out = self.pick(legs)
        self.assertTrue(out["tickets"])
        self.assertTrue(all(len(t.legs) == 3 for t in out["tickets"]))

    def test_an_extra_leg_is_dropped_when_the_shorter_ticket_already_passes(self):
        legs = [shots(1, 1, 26, -185, 0.65), shots(2, 2, 26, -185, 0.65), shots(3, 3, 26, -185, 0.65)]
        out = self.pick(legs)
        self.assertTrue(all(len(t.legs) == 2 for t in out["tickets"]))

    def test_up_to_five_legs_and_never_more(self):
        legs = [shots(g, g, 25, -400, 0.68) for g in range(1, 9)]
        out = rmp.select_tickets(legs, max_tickets=5, policy=dataclasses.replace(POL, floor=0.0, min_value_after_shade=-1.0))
        self.assertTrue(all(2 <= len(t.legs) <= 5 for t in out["tickets"]))

    def test_tickets_are_ranked_by_estimated_hit_chance_and_a_player_is_used_once(self):
        legs = [shots(g, g, 26 - g, -185, 0.60) for g in range(1, 7)]
        out = self.pick(legs)
        chances = [t.joint_probability for t in out["tickets"]]
        self.assertEqual(chances, sorted(chances, reverse=True))
        players = [l.participant_id for t in out["tickets"] for l in t.legs]
        self.assertEqual(len(players), len(set(players)))

    def test_it_never_pads_with_speculative_legs(self):
        weak = [dataclasses.replace(shots(g, g, 12, 250, 0.4), market_family="PLAYER_POINTS", threshold=1) for g in range(1, 5)]
        self.assertEqual(self.pick(weak)["tickets"], [])

    def test_describe_states_the_evidence_range_and_value_at_three_assumptions(self):
        t = self.pick([shots(1, 1, 24, -185, 0.63, name="Ehlers"), shots(2, 2, 26, -260, 0.695, name="McMann")])["tickets"][0]
        info = anchor.describe(t, POL)
        self.assertIn("Ehlers 2+ shots: recent hit rate 24 of 30 games (80%); model 63%; final estimate 73%", info["rationale"])
        self.assertIn("price implies", info["rationale"])
        self.assertIn("not verified for Ontario", info["rationale"])
        self.assertLess(info["hit_low"], t.joint_probability)
        self.assertGreater(info["hit_high"], t.joint_probability)
        self.assertLess(info["midpoint_ev"], t.ev_estimated)


class TestFourNumbersKeptApart(unittest.TestCase):
    def test_recent_rate_model_final_estimate_and_price_are_four_different_numbers(self):
        leg = anchor.prepare_pool([shots(1, 1, 24, -185, 0.63)])[0]
        ev = anchor.leg_evidence(leg)
        self.assertEqual((ev["hits"], ev["games"], ev["recent_hit_rate"]), (24, 30, 0.8))               # unshrunk: 24 / 30
        self.assertEqual(ev["model"], 0.63)
        self.assertAlmostEqual(ev["record_shrunk"], 0.7263, places=3)                                    # shrunk toward the league rate
        self.assertAlmostEqual(ev["estimate"], 0.7263, places=3)                                         # what the ticket uses (not capped here)
        self.assertAlmostEqual(ev["price_implied"], 0.6491, places=3)
        self.assertEqual(len({ev["recent_hit_rate"], ev["model"], round(ev["estimate"], 3), round(ev["price_implied"], 3)}), 4)


class TestShotFrequencies(unittest.TestCase):
    def rows(self, n, shots_each, pid="p1", start=0):
        return [{"player_id": pid, "date": f"2026-01-{(start + i) % 28 + 1:02d}-{start + i:03d}", "shots": shots_each[i % len(shots_each)]} for i in range(n)]

    def test_shrunk_toward_the_league_rate_over_the_last_thirty_games(self):
        rows = self.rows(40, [3]) + self.rows(100, [0, 1], pid="other", start=100)
        freq = live.shot_frequencies(rows)["p1"]
        self.assertEqual((freq[2]["hits"], freq[2]["n"]), (30, 30))
        base = (40 + 0) / 140                       # league rate of 2+ shots: p1's 40 games out of 140 rows
        self.assertAlmostEqual(freq[2]["p"], round((30 + 8 * base) / 38, 4), places=3)
        self.assertLess(freq[2]["p"], 1.0)

    def test_players_with_fewer_than_five_games_have_no_frequency(self):
        self.assertNotIn("p1", live.shot_frequencies(self.rows(4, [3])))

    def test_evidence_fields_reach_the_leg(self):
        entry = {"probs": {"SOG2": 0.7}, "emp": {"SOG2": {"p": 0.78, "hits": 26, "n": 30}}}
        ev = best_bets._evidence(entry, "PLAYER_SOG_ALTERNATE", "SOG", 2)
        self.assertEqual((ev["model_probability"], ev["empirical_probability"], ev["empirical_hits"], ev["empirical_games"]), (0.7, 0.78, 26, 30))
        self.assertNotIn("empirical_probability", best_bets._evidence(entry, "PLAYER_POINTS", "PTS", 1))


class TestPausedBoard(unittest.TestCase):
    """While recording is paused the board shows the anchor tickets as proposed, with their evidence, and nothing is staked."""
    def setUp(self):
        import tempfile
        from pathlib import Path
        from operational import state_paths
        from tests.test_daily_tickets import fresh_ledger
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        for patcher in (mock.patch.object(state_paths, "path", lambda name, **kw: d / name), mock.patch.dict(os.environ, {"NHL_ENGINE_TICKET_POLICY": "proposed"})):
            patcher.start()
            self.addCleanup(patcher.stop)
        _path, self.conn = fresh_ledger()
        self.addCleanup(self.conn.close)

    def test_proposed_tickets_carry_the_evidence_and_nothing_is_recorded(self):
        from operational import daily_tickets as dtk
        from operational import recording_pause as rp
        legs = [dataclasses.replace(l, game_start_utc="2026-10-11T23:00:00Z", team="T", opponent="O") for l in
                (shots(1, 1, 24, -185, 0.63, name="Ehlers"), shots(2, 2, 26, -260, 0.695, name="McMann"))]
        rp.pause("test", now=NOW)
        res = dtk.run_cycle(None, self.conn, NOW, collected={"legs": legs, "sources": {}, "second_opinion": {}})
        self.assertEqual(res["newly_recorded"], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM paper_bets").fetchone()[0], 0)
        st = dtk.read_state()
        self.assertEqual(len(st["tickets"]), 1)
        t = st["tickets"][0]
        self.assertTrue(t["proposed"] and not t["recorded"])
        self.assertEqual([l["evidence"]["hits"] for l in t["legs"]], [24, 26])
        self.assertIn("recent hit rate 24 of 30 games (80%)", t["rationale"])
        self.assertLess(t["anchor"]["hit_low"], t["hit_probability"])
        self.assertIn("proposed, not recorded", st["empty_slot_reason"] or "proposed, not recorded")

    def test_the_provisional_board_uses_the_same_anchor_rules_on_earlier_prices(self):
        from operational import daily_tickets as dtk
        legs = [dataclasses.replace(l, game_start_utc="2026-10-11T23:00:00Z", provisional=True, price_fresh=False, freshness_status="STALE_QUOTE", quote_age_min=200.0) for l in
                (shots(1, 1, 24, -185, 0.63, name="Ehlers"), shots(2, 2, 26, -260, 0.695, name="McMann"))]
        sec = dtk._provisional_section(legs, [], NOW, "2026-10-11")
        self.assertEqual(len(sec["tickets"]), 1)
        card = sec["tickets"][0]
        self.assertTrue(card["provisional"])
        self.assertEqual([l["evidence"]["hits"] for l in card["legs"]], [24, 26])
        self.assertIn("anchor", card)


if __name__ == "__main__":
    unittest.main()
