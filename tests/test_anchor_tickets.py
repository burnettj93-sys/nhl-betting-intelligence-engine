"""Anchor tickets: parlays from well-supported legs (a shooter's own record, a moneyline at the market's chance), ticket-level value at the real price, ranked by hit chance."""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import unittest
from unittest import mock

from operational import best_bets
from research.product_models import live
from research.real_market_parlay import anchor
from research.real_market_parlay import engine as rmp
from research.real_market_parlay import policy as P

NOW = dt.datetime(2026, 10, 11, 16, 0, tzinfo=dt.timezone.utc)
POL = P.ANCHOR_V1            # these fixtures and the replay of the 2026-10-12 tickets are written for the 2026-10-11 policy (shots cap +0.10); the corrected cap is tested in tests/test_shots_cap_correction.py


def setUpModule():
    global _saved_cap
    _saved_cap = anchor.ANCHOR_CAP
    anchor.ANCHOR_CAP = anchor.ANCHOR_CAP_V1


def tearDownModule():
    anchor.ANCHOR_CAP = _saved_cap


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

    def test_the_empty_board_separates_games_with_prop_prices_from_moneyline_only_games(self):
        """2026-10-13: "12 legs in 6 games" while prop prices existed for 3: games are counted once if any supported leg exists, so say where the legs come from."""
        legs = [shots(1, 1, 22, -200, 0.6), shots(2, 2, 22, -200, 0.6), moneyline(3, "BOS", -150, 0.58, elo=0.58), moneyline(4, "NYR", -140, 0.57, elo=0.57)]
        out = self.pick(legs)
        self.assertEqual(out["tickets"], [])
        self.assertIn("2 player-prop leg(s) in 2 game(s) with prop prices", out["reason"])
        self.assertIn("2 moneyline leg(s) in 2 game(s)", out["reason"])
        self.assertIn("in 4 game(s)", out["reason"])

    def test_the_primary_slip_ceiling_excludes_longer_prices_and_keeps_the_rest(self):
        legs = [shots(1, 1, 27, +120, 0.70), shots(2, 2, 27, +120, 0.70)]               # two legs at +120: combined 4.84, i.e. +384
        capped_low = dataclasses.replace(POL, max_combined_decimal=4.0)              # +300 ceiling: this ticket is too long
        capped_ok = dataclasses.replace(POL, max_combined_decimal=6.0)               # +500 ceiling: inside
        self.assertEqual(rmp.select_tickets(legs, max_tickets=1, policy=capped_low)["tickets"], [])
        out = rmp.select_tickets(legs, max_tickets=1, policy=capped_ok)
        self.assertEqual(len(out["tickets"]), 1)
        self.assertTrue(anchor.within_ceiling(out["tickets"][0].combined_decimal, capped_ok))
        self.assertTrue(anchor.within_ceiling(6.004, capped_ok))                       # a product that rounds to +500 is inside
        self.assertFalse(anchor.within_ceiling(6.02, capped_ok))
        self.assertTrue(anchor.within_ceiling(50.0, POL))                              # no ceiling in the earlier policies

    def test_the_reason_says_which_way_each_combination_was_rejected(self):
        capped = dataclasses.replace(POL, max_combined_decimal=6.0)
        # all pairs priced above +500: the old wording claimed nothing reaches +100
        above = rmp.select_tickets([shots(1, 1, 28, +450, 0.72), shots(2, 2, 28, +450, 0.72)], max_tickets=1, policy=capped)
        self.assertEqual(above["tickets"], [])
        self.assertIn("two-leg pair(s) were already priced above the +500 ceiling", above["reason"])
        self.assertIn("were not enumerated or counted", above["reason"])                          # the pair count is not passed off as a count of all slips
        self.assertIn("none is priced inside +100 to +500", above["reason"])
        self.assertNotIn("gets to +100", above["reason"])
        # all pairs below +100 (two -300 legs: 1.33 x 1.33 = 1.78)
        below = rmp.select_tickets([shots(1, 1, 27, -300, 0.72), shots(2, 2, 27, -300, 0.72)], max_tickets=1, policy=capped)
        self.assertIn("priced below +100", below["reason"])
        # priced inside the range but no value at the real prices
        novalue = rmp.select_tickets([shots(1, 1, 22, -200, 0.6), shots(2, 2, 22, -170, 0.6)], max_tickets=1, policy=capped)       # +8.1% before the haircut, -1.3% after: the policy needs +5% and +3%
        self.assertEqual(novalue["tickets"], [])
        self.assertIn("1 priced inside +100 to +500 (1 without enough value at the real prices)", novalue["reason"])
        self.assertNotIn("priced below +100", novalue["reason"])
        self.assertNotIn("ceiling", novalue["reason"])

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

    def test_the_record_says_how_many_of_its_games_are_from_this_season(self):
        """Early in a year the 30-game window reaches back into last season; the display must not present those as this season's games."""
        rows = [{"player_id": "p1", "date": f"2026-03-{i + 1:02d}", "shots": 3} for i in range(25)] + [{"player_id": "p1", "date": f"2026-10-{i + 1:02d}", "shots": 3} for i in range(5)]
        cell = live.shot_frequencies(rows)["p1"][2]
        self.assertEqual((cell["n"], cell["n_current"]), (30, 5))
        entry = {"probs": {"SOG2": 0.7}, "emp": {"SOG2": {"p": 0.78, "hits": 26, "n": 30, "n_current": 5}}}
        ev = best_bets._evidence(entry, "PLAYER_SOG_ALTERNATE", "SOG", 2)
        self.assertEqual(ev["empirical_games_current"], 5)
        leg = anchor.prepare_pool([dataclasses.replace(shots(1, 1, 26, -185, 0.7), empirical_games_current=5)])[0]
        self.assertEqual(anchor.leg_evidence(leg)["games_current"], 5)

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
        for patcher in (mock.patch.object(state_paths, "path", lambda name, **kw: d / name), mock.patch.dict(os.environ, {"NHL_ENGINE_TICKET_POLICY": "anchor_v1"})):
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

    def test_with_only_stale_prices_the_board_says_so_and_records_nothing(self):
        from operational import daily_tickets as dtk
        from operational import paper_bankroll as pb
        legs = [dataclasses.replace(l, game_start_utc="2026-10-11T23:00:00Z", provisional=True, price_fresh=False, freshness_status="STALE_QUOTE", quote_age_min=200.0) for l in
                (shots(1, 1, 24, -185, 0.63, name="Ehlers"), shots(2, 2, 26, -260, 0.695, name="McMann"))]
        res = dtk.run_cycle(None, self.conn, NOW, collected={"legs": legs, "sources": {}, "second_opinion": {}})        # recording is NOT paused here
        self.assertEqual((res["newly_recorded"], res["recording_paused"]), (0, False))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM paper_bets").fetchone()[0], 0)
        st = dtk.read_state()
        self.assertEqual(len(st["provisional"]["tickets"]), 1)                       # shown, never back-filled
        self.assertIn("No fresh prices right now", st["empty_slot_reason"])
        self.assertEqual(pb.account_state(self.conn, "REAL_MARKET_PAPER")["available_cash"], 500.0)


# 2026-10-12 14:23Z: the real candidate pool that exposed the selector defect (docs/validation/selector_cap_2026-10-12.md). (game, player id, name, line, price, model probability, hits in last 30)
OCT12 = [("88", "8481559", "Jack Hughes", 2, -550, 0.812, 27, 0.8049), ("88", "8481559", "Jack Hughes", 3, -195, 0.5966, 22, 0.6288), ("88", "8478414", "Timo Meier", 2, -450, 0.7729, 26, 0.7786),
         ("88", "8478414", "Timo Meier", 3, -155, 0.5399, 21, 0.6025), ("88", "8481528", "Dylan Cozens", 2, -240, 0.6547, 23, 0.6996), ("88", "8480002", "Nico Hischier", 2, -210, 0.6234, 22, 0.6733),
         ("88", "8479407", "Jesper Bratt", 2, -190, 0.5727, 21, 0.647), ("88", "8476462", "Dougie Hamilton", 2, -185, 0.6189, 22, 0.6733), ("88", "8481596", "Shane Pinto", 2, -165, 0.5617, 21, 0.647),
         ("88", "8480208", "Drake Batherson", 2, -145, 0.5599, 22, 0.6733), ("88", "8481721", "Arseny Gritsyuk", 2, -120, 0.5457, 20, 0.6207),
         ("87", "8480801", "Brady Tkachuk", 3, -155, 0.5432, 21, 0.6025), ("87", "8479314", "Matthew Tkachuk", 2, -390, 0.7192, 26, 0.7786), ("87", "8482097", "Jack Quinn", 2, -300, 0.6659, 25, 0.7522),
         ("87", "8477933", "Sam Reinhart", 2, -255, 0.6654, 23, 0.6996), ("89", "8478403", "Jack Eichel", 2, -475, 0.7789, 28, 0.8312), ("89", "8476881", "Tomas Hertl", 2, -175, 0.6108, 21, 0.647)]


def oct12_legs():
    """The 17 shots legs (last-30 record shrunk as recorded: the 8th column) and the NJD moneyline, exactly as the live pool held them."""
    legs = [dataclasses.replace(shots(g, pid, hits, price, model, k=k, name=name), empirical_probability=emp) for g, pid, name, k, price, model, hits, emp in OCT12]
    return legs + [moneyline("88", "NJD", -135, 0.5514, elo=0.5494)]


def brute_force(pool, policy):
    """Independent enumeration: every one-leg-per-game ticket of 2..MAX_LEGS legs, judged by the policy's own acceptance test, minus tickets with a passing shorter ticket."""
    import itertools
    by = {}
    for l in pool:
        by.setdefault(l.game_id, []).append(l)
    out = {}
    for size in range(2, anchor.MAX_LEGS + 1):
        for gs in itertools.combinations(sorted(by), size):
            for grp in itertools.product(*(by[g] for g in gs)):
                c = rmp._evaluate_combo(list(grp))
                if c is not None and anchor.ticket_passes(c, policy, rmp._policy_veto):
                    keys = frozenset((l.game_id, l.participant_id, l.market_family, l.threshold, l.side) for l in grp)
                    if size > 2 and any(frozenset(sub) in out for n in range(2, size) for sub in itertools.combinations(keys, n)):
                        continue
                    out[keys] = c
    return out


def key_of(combo):
    return frozenset((l.game_id, l.participant_id, l.market_family, l.threshold, l.side) for l in combo.legs)


class TestTheSearchHidesNothing(unittest.TestCase):
    """Regression for 2026-10-12: a per-game cap of 4 legs dropped Batherson and Gritsyuk, so five tickets that passed every policy test were never offered."""
    def setUp(self):
        p = mock.patch.dict(os.environ, {"NHL_ENGINE_TICKET_POLICY": "anchor_v1"})
        p.start()
        self.addCleanup(p.stop)

    def test_the_real_oct_12_pool_returns_every_ticket_that_passes_including_batherson_and_eichel(self):
        pool = anchor.prepare_pool(oct12_legs())
        self.assertEqual(len(pool), 18)                                                            # 17 shots legs and the NJD moneyline
        found, diag = anchor.qualifying(pool, POL, rmp._evaluate_combo, rmp._policy_veto)
        names = {frozenset(l.participant_name for l in c.legs) for c in found}
        self.assertIn(frozenset({"Drake Batherson", "Jack Eichel"}), names)
        self.assertEqual(len(found), 5)
        self.assertFalse(diag["truncated"])
        self.assertEqual({key_of(c) for c in found}, set(brute_force(pool, POL)))                 # identical to an independent enumeration

    def test_skipping_slips_covered_by_a_passing_prefix_changes_nothing_in_the_result(self):
        """Exactness of the prefix skip against an independent enumeration, on pools with many 3- and 4-leg tickets."""
        import random
        for seed in (11, 12, 13):
            rnd = random.Random(seed)
            legs = [shots(g, f"{g}{i}", rnd.randint(24, 29), -rnd.choice([120, 150, 185, 240]), rnd.uniform(0.62, 0.80), name=f"P{g}{i}") for g in range(1, 7) for i in range(rnd.randint(2, 4))]
            pool = anchor.prepare_pool(legs)
            found, diag = anchor.qualifying(pool, POL, rmp._evaluate_combo, rmp._policy_veto)
            self.assertEqual({key_of(c) for c in found}, set(brute_force(pool, POL)), f"seed {seed}")
            self.assertTrue(any(len(c.legs) >= 3 for c in found) or diag.get("covered_by_shorter", 0) >= 0)

    def test_the_selector_offers_them_and_the_exposure_limits_still_bind(self):
        out = rmp.select_tickets(oct12_legs(), max_tickets=5, policy=POL)
        self.assertEqual(out["qualifying"], 5)
        tickets = [{l.participant_name for l in t.legs} for t in out["tickets"]]
        self.assertEqual(tickets[0], {"Drake Batherson", "Jack Eichel"})                           # best hit chance first (54.9%, +105)
        players = [n for t in tickets for n in t]
        self.assertEqual(len(players), len(set(players)))                                          # one ticket per player
        self.assertEqual(len(out["tickets"]), 2)                                                   # Eichel/Hertl/Batherson/Gritsyuk can each be used once
        t0 = out["tickets"][0]
        self.assertGreaterEqual(t0.combined_decimal, 2.0)
        self.assertGreaterEqual(t0.ev_conservative, 0.03 - 1e-9)

    def test_random_pools_match_an_independent_enumeration(self):
        import random
        for seed in range(6):
            rnd = random.Random(seed)
            legs = []
            for g in range(1, 6):
                for i in range(rnd.randint(2, 6)):
                    hits = rnd.randint(19, 29)
                    model = rnd.uniform(0.52, 0.80)
                    legs.append(shots(g, f"{g}{i}", hits, -rnd.choice([110, 130, 150, 185, 240, 320, 450]), model, name=f"P{g}{i}"))
            pool = anchor.prepare_pool(legs)
            found, diag = anchor.qualifying(pool, POL, rmp._evaluate_combo, rmp._policy_veto)
            self.assertEqual({key_of(c) for c in found}, set(brute_force(pool, POL)), f"seed {seed}")

    def test_a_sixteen_game_night_is_searched_in_full_and_quickly(self):
        import random
        import time
        rnd = random.Random(20261013)
        legs = [shots(g, f"{g}-{i}", rnd.randint(22, 29), -rnd.choice([150, 190, 240, 320, 450, 600]), rnd.uniform(0.60, 0.80), name=f"P{g}-{i}") for g in range(1, 17) for i in range(8)]
        pool = anchor.prepare_pool(legs)
        t = time.time()
        found, diag = anchor.qualifying(pool, POL, rmp._evaluate_combo, rmp._policy_veto)
        self.assertLess(time.time() - t, 60.0)                                                      # (about 4 s on a quiet machine since the prefix skip; it was 58-73 s and flaky under load before)
        self.assertFalse(diag["truncated"])
        self.assertEqual(diag["games_with_supported_legs"], 16)                                     # no game was dropped before the search
        self.assertGreater(len(found), 1000)
        self.assertGreater(diag.get("covered_by_shorter", 0), 0)                                    # longer slips whose own prefix already passed are not evaluated again

    def test_today_best_options_the_provisional_board_and_recording_agree(self):
        from operational import daily_tickets as dtk
        from operational import player_options as po
        legs = oct12_legs()
        out = rmp.select_tickets(legs, max_tickets=5, policy=POL)
        qual, _ = anchor.qualifying(anchor.prepare_pool(legs), POL, rmp._evaluate_combo, rmp._policy_veto)
        qual_keys = {key_of(c) for c in qual}
        opts = po.build_options(legs, "2026-10-12")
        self.assertEqual(opts["diagnostics"]["qualifying_tickets_in_pool"], len(qual))
        for o in opts["options"]:
            ids = frozenset((l["game_id"], l["participant_id"], l["market_family"], l["threshold"], l["side"]) for l in o["legs"])
            self.assertIn(ids, qual_keys)                                                         # Best Options only offers tickets the selector also qualifies
        for t in out["tickets"]:
            self.assertIn(key_of(t), qual_keys)
            for leg in t.legs:                                                                    # every person on a selected ticket has an option
                self.assertIsNotNone(opts["persons"][leg.participant_id]["option_id"])
        prov = [dataclasses.replace(l, provisional=True, price_fresh=False) for l in legs]
        sec = dtk._provisional_section(prov, [], NOW, "2026-10-12")
        self.assertEqual({t["ticket_id"] for t in sec["tickets"]}, {dtk.pb.compute_ticket_id("2026-10-12", t.legs) for t in out["tickets"]})    # the provisional board is the same selection


class TestTheRealPoolIsRecordedThroughTheNormalCycle(unittest.TestCase):
    """Rehearsal of the live path with the real 2026-10-12 pool: an unpaused cycle on fresh prices records exactly the tickets the selector qualifies, once, at $10, and nothing else."""
    def setUp(self):
        import tempfile
        from pathlib import Path
        from operational import state_paths
        from tests.test_daily_tickets import fresh_ledger
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        for patcher in (mock.patch.object(state_paths, "path", lambda name, **kw: d / name), mock.patch.dict(os.environ, {"NHL_ENGINE_TICKET_POLICY": "anchor_v1"})):
            patcher.start()
            self.addCleanup(patcher.stop)
        _p, self.conn = fresh_ledger()
        self.addCleanup(self.conn.close)

    def cycle(self, minutes=20):
        from operational import daily_tickets as dtk
        return dtk.run_cycle(None, self.conn, NOW + dt.timedelta(minutes=minutes), collected={"legs": oct12_legs(), "sources": {}, "second_opinion": {}})

    def test_the_cycle_records_the_two_tickets_once_and_the_bankroll_follows(self):
        from operational import paper_bankroll as pb
        import json as _json
        res = self.cycle()
        self.assertEqual(res["newly_recorded"], 2)
        rows = [dict(r) for r in self.conn.execute("SELECT * FROM paper_bets WHERE origin = 'AUTOMATIC' ORDER BY created_at_utc, paper_bet_id")]
        sets = {frozenset(l["participant_name"] for l in _json.loads(r["legs_json"])) for r in rows}
        self.assertEqual(sets, {frozenset({"Drake Batherson", "Jack Eichel"}), frozenset({"Jack Quinn", "Arseny Gritsyuk"})})
        self.assertTrue(all(r["stake"] == 10.0 for r in rows))
        self.assertEqual(pb.account_state(self.conn, "REAL_MARKET_PAPER")["available_cash"], 480.0)
        again = self.cycle(minutes=35)                                                          # the next 15-minute cycle: nothing new, nothing doubled
        self.assertEqual(again["newly_recorded"], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM paper_bets").fetchone()[0], 2)

    def test_the_frozen_leg_keeps_the_four_numbers_and_the_log_marks_the_tickets_recorded(self):
        import json as _json
        from operational import shadow_selection as sh
        self.cycle()
        row = dict(self.conn.execute("SELECT * FROM paper_bets ORDER BY paper_bet_id LIMIT 1").fetchone())
        leg = _json.loads(row["legs_json"])[0]
        for k in ("model_probability", "empirical_probability", "empirical_hits", "empirical_games"):
            self.assertIn(k, leg)
        self.assertIsNotNone(leg["empirical_hits"])
        recs = sh.read_all()
        self.assertEqual(len(recs), 1)
        self.assertTrue(all(t["recorded"] for t in recs[0]["proposed_tickets"]))
        self.assertEqual(len(recs[0]["proposed_tickets"]), 2)


class TestThePrimarySlipThroughTheNormalCycle(unittest.TestCase):
    """The 2026-10-14 workflow end to end on isolated data: ONE slip a day, priced +100 to +500, recorded once at $10 on fresh prices, shown as the primary slip, and nothing else recorded."""
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
        _p, self.conn = fresh_ledger()
        self.addCleanup(self.conn.close)

    def cycle(self, legs, minutes=20):
        from operational import daily_tickets as dtk
        return dtk.run_cycle(None, self.conn, NOW + dt.timedelta(minutes=minutes), collected={"legs": legs, "sources": {}, "second_opinion": {}})

    def test_one_slip_is_recorded_even_when_several_combinations_qualify(self):
        from operational import daily_tickets as dtk, paper_bankroll as pb
        legs = [shots(1, 1, 27, +120, 0.70), shots(2, 2, 27, +125, 0.70), shots(3, 3, 28, +115, 0.72)]
        res = self.cycle(legs)
        self.assertEqual(res["newly_recorded"], 1)
        self.assertEqual(dtk.read_state()["primary"]["status"], "RECORDED")                           # already in the cycle that recorded it, not 15 minutes later
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM paper_bets WHERE origin = 'AUTOMATIC'").fetchone()[0], 1)
        self.assertEqual(pb.account_state(self.conn, "REAL_MARKET_PAPER")["available_cash"], 490.0)
        again = self.cycle(legs, minutes=35)
        self.assertEqual(again["newly_recorded"], 0)                                                   # one a day: nothing doubled, nothing added
        st = dtk.read_state()
        self.assertEqual(st["slots"], {"total": 1, "used": 1, "empty": 0})
        self.assertEqual(st["primary"]["status"], "RECORDED")
        self.assertFalse(st["primary"]["ticket"].get("proposed"))
        self.assertTrue(100 <= st["primary"]["ticket"]["combined_american"] <= 500)

    def test_a_combination_longer_than_plus_500_is_not_recorded_and_the_board_says_why(self):
        legs = [shots(1, 1, 28, +400, 0.72), shots(2, 2, 28, +400, 0.72)]                              # 5.0 x 5.0 = 25.0, i.e. +2400
        res = self.cycle(legs)
        self.assertIn("above the +500 ceiling", res["reason"])
        self.assertEqual(res["newly_recorded"], 0)
        from operational import daily_tickets as dtk
        st = dtk.read_state()
        self.assertEqual(st["primary"]["status"], "NONE")
        self.assertIsNone(st["primary"]["ticket"])
        self.assertTrue(st["primary"]["strongest_singles"])                                             # the strongest individual selections are shown instead
        self.assertEqual(st["slots"]["empty"], 1)

    def test_an_unloadable_policy_blocks_recording_and_is_reported_never_five_tickets(self):
        from operational import daily_tickets as dtk, ticket_policy, watchdog
        legs = [shots(1, 1, 27, +120, 0.70), shots(2, 2, 27, +125, 0.70), shots(3, 3, 28, +115, 0.72)]
        with mock.patch.object(ticket_policy, "active", side_effect=RuntimeError("approval file unreadable")):
            res = self.cycle(legs)
            with self.assertRaises(dtk.PolicyUnavailable):
                dtk.slot_count()
            self.assertEqual(dtk.slot_count_for_display(), 0)
            chk = watchdog.check_ticket_policy()
        self.assertEqual(res["newly_recorded"], 0)
        self.assertIn("could not be loaded", res["recording_blocked"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM paper_bets").fetchone()[0], 0)
        st = dtk.read_state()
        self.assertIn("approval file unreadable", st["policy_error"])
        self.assertEqual(st["slots"]["total"], 0)
        self.assertEqual(st["primary"]["status"], "ERROR")
        self.assertEqual(chk["status"], watchdog.FAIL)
        self.assertEqual(self.cycle(legs, minutes=35)["newly_recorded"], 1)                          # the policy loads again: the normal single slip resumes by itself

    def test_the_slip_the_ticket_list_and_the_account_agree_in_the_cycle_that_records_it(self):
        from operational import daily_tickets as dtk
        legs = [shots(1, 1, 27, +120, 0.70), shots(2, 2, 27, +125, 0.70)]
        self.cycle(legs)
        st = dtk.read_state()
        ids = [t["ticket_id"] for t in st["tickets"] if t["recorded"]]
        self.assertEqual(len(ids), 1)
        self.assertEqual(st["primary"]["ticket"]["ticket_id"], ids[0])
        self.assertEqual(st["primary"]["status"], "RECORDED")
        self.assertEqual((st["account"]["open_stakes"], st["account"]["available_cash"], st["account"]["open_tickets"]), (10.0, 490.0, 1))
        self.assertEqual(st["slots"], {"total": 1, "used": 1, "empty": 0})

    def test_when_nothing_qualifies_the_closest_combination_is_shown_and_labelled_not_qualifying(self):
        from operational import daily_tickets as dtk
        self.cycle([shots(1, 1, 22, -150, 0.6), shots(2, 2, 22, -140, 0.6)])                         # +9.8% before the haircut, -0.5% after: close, but under the policy's +3% bar
        st = dtk.read_state()
        self.assertEqual(st["primary"]["status"], "NONE")
        closest = st["primary"]["closest"]
        self.assertEqual(len(closest["legs"]), 2)
        self.assertTrue(100 <= closest["combined_american"] <= 500)
        self.assertLess(closest["ev_after_haircut"], 0.03)
        self.assertIn("does NOT qualify", closest["note"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM paper_bets").fetchone()[0], 0)

    def test_a_single_leg_pool_gives_no_slip_and_no_invented_second_leg(self):
        res = self.cycle([shots(1, 1, 27, +120, 0.70)])
        self.assertEqual(res["newly_recorded"], 0)
        from operational import daily_tickets as dtk
        st = dtk.read_state()
        self.assertEqual(st["primary"]["status"], "NONE")
        self.assertIn("at least 2 legs", st["primary"]["reason"])


class TestPersonOptions(unittest.TestCase):
    """For each player the best supported +100 option involving him, built from the same estimates and tests as the automatic tickets; none is made up."""
    def setUp(self):
        p = mock.patch.dict(os.environ, {"NHL_ENGINE_TICKET_POLICY": "anchor_v1"})
        p.start()
        self.addCleanup(p.stop)

    def build(self, legs):
        from operational import player_options as po
        return po.build_options(legs, "2026-10-11")

    def test_a_person_gets_the_fewest_legs_option_that_passes_the_anchor_tests(self):
        out = self.build([shots(1, 1, 24, -185, 0.63, name="Ehlers"), shots(2, 2, 26, -260, 0.695, name="McMann"), shots(3, 3, 25, -300, 0.70, name="Third")])
        self.assertEqual(out["policy"]["mode"], "anchor")
        e = out["persons"]["1"]
        self.assertIsNotNone(e["option_id"])
        opt = next(o for o in out["options"] if o["option_id"] == e["option_id"])
        self.assertEqual(opt["kind"], "PARLAY")
        self.assertTrue(any(l["participant_name"] == "Ehlers" for l in opt["legs"]))
        self.assertGreaterEqual(opt["combined_decimal"], 2.0)
        self.assertIn("recent hit rate 24 of 30", opt["rationale"])
        self.assertEqual(opt["legs"][0]["evidence"]["hits"] in (24, 26, 25), True)

    def test_a_person_with_no_supported_leg_gets_no_option(self):
        pts = dataclasses.replace(shots(4, 4, 28, -150, 0.8, name="PointsOnly"), market_family="PLAYER_POINTS", threshold=1)
        out = self.build([shots(1, 1, 24, -185, 0.63), shots(2, 2, 26, -260, 0.695), pts])
        self.assertIsNone(out["persons"]["4"]["option_id"])
        self.assertEqual(out["persons"]["4"]["reason_no_option"], "NO_LEG_SUPPORTED_FOR_AUTOMATIC_TICKETS")

    def test_nothing_is_fabricated_when_no_combination_has_value(self):
        out = self.build([shots(1, 1, 22, -200, 0.6), shots(2, 2, 22, -200, 0.6)])
        self.assertEqual(out["options"], [])
        self.assertEqual({p["reason_no_option"] for p in out["persons"].values()}, {"NO_COMBINATION_QUALIFIES_AT_TODAYS_PRICES"})


class TestFrozenPredictionScoring(unittest.TestCase):
    def test_anchor_estimates_are_scored_against_results_without_rewriting_the_entry_numbers(self):
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))
        import score_selection_shadow as sss
        from operational import shadow_selection as sh
        fields = sh.POOL_FIELDS

        def row(game, pid, hit_hits, final, in_ticket):
            base = [game, pid, "PLAYER_SOG_ALTERNATE", 2, -185, 0.63, f"P{pid}", "T", "2026-10-11T23:00:00Z", "2026-10-11T19:00:00Z", True]
            return base + [hit_hits, 30, 0.63, 0.65, final, final, "SUPPORTED", in_ticket, "2026-10-11T19:00:00Z"]
        rec = {"et_date": "2026-10-11", "pool_fields": fields, "variants": {}, "pool": [row("g1", "a", 24, 0.726, True), row("g2", "b", 26, 0.779, True), row("g1", "c", 20, 0.62, False)]}
        outcomes = {("g1", "a"): True, ("g2", "b"): False, ("g1", "c"): True}
        rep = sss.score([rec], lambda g, p, fam, k: {"status": "FINAL", "hit": outcomes[(g, p)]})
        a = rep["anchor_estimates"]
        self.assertEqual((a["supported_pool"]["legs"], a["on_selected_tickets"]["legs"]), (3, 2))
        self.assertAlmostEqual(a["on_selected_tickets"]["final_estimate"], (0.726 + 0.779) / 2, places=3)
        self.assertAlmostEqual(a["on_selected_tickets"]["happened"], 0.5)
        self.assertAlmostEqual(a["on_selected_tickets"]["recent_hit_rate"], (24 / 30 + 26 / 30) / 2, places=3)       # unshrunk, apart from the final estimate
        self.assertEqual(a["supported_pool"]["games"], 2)

    def test_a_leg_selected_in_a_later_snapshot_is_still_a_leg_on_a_selected_ticket(self):
        """2026-10-12: three of the four recorded legs were first logged (12:04Z) before any ticket used them; the evening snapshot selected them. They must count as selected, with the numbers frozen when they were selected."""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))
        import score_selection_shadow as sss
        from operational import shadow_selection as sh

        def row(game, pid, price, final, in_ticket):
            base = [game, pid, "PLAYER_SOG_ALTERNATE", 2, price, 0.63, f"P{pid}", "T", "2026-10-12T23:00:00Z", "2026-10-12T12:00:00Z", True]
            return base + [20, 30, 0.63, 0.65, final, final, "SUPPORTED", in_ticket, "2026-10-12T12:00:00Z"]
        morning = {"et_date": "2026-10-12", "pool_fields": sh.POOL_FIELDS, "variants": {}, "pool": [row("g1", "a", -150, 0.70, False), row("g2", "b", -150, 0.70, False)]}
        evening = {"et_date": "2026-10-12", "pool_fields": sh.POOL_FIELDS, "variants": {}, "pool": [row("g1", "a", -105, 0.62, True), row("g2", "b", -450, 0.83, True)]}
        outcomes = {("g1", "a"): True, ("g2", "b"): False}
        rep = sss.score([morning, evening], lambda g, p, fam, k: {"status": "FINAL", "hit": outcomes[(g, p)]})["anchor_estimates"]
        self.assertEqual(rep["supported_pool"]["legs"], 2)
        self.assertEqual(rep["supported_pool"]["final_estimate"], 0.70)             # the first look still defines the candidate pool
        sel = rep["on_selected_tickets"]
        self.assertEqual(sel["legs"], 2)
        self.assertAlmostEqual(sel["final_estimate"], (0.62 + 0.83) / 2, places=3)   # frozen when selected
        self.assertAlmostEqual(sel["happened"], 0.5)


class TestReviewOfALostTicket(unittest.TestCase):
    def test_a_leg_with_no_result_on_a_ticket_already_lost_is_a_note_not_a_defect(self):
        from operational import daily_review as dr
        legs = [{"game_id": "g1", "participant_id": "a", "participant_name": "A", "market_family": "PLAYER_SOG_ALTERNATE", "threshold": 2, "american_price": -150,
                 "quote_updated_utc": "2026-10-08T19:00:00Z", "game_start_utc": "2026-10-08T23:00:00Z"},
                {"game_id": "g2", "participant_id": "b", "participant_name": "B", "market_family": "PLAYER_POINTS", "threshold": 1, "american_price": 120,
                 "quote_updated_utc": "2026-10-08T19:00:00Z", "game_start_utc": "2026-10-08T23:00:00Z"}]
        settlement = {"leg_results": [{"leg": {"game_id": "g1", "participant_id": "a", "market_family": "PLAYER_SOG_ALTERNATE", "threshold": 2}, "outcome_hit": False, "status": "RESOLVED"},
                                      {"leg": {"game_id": "g2", "participant_id": "b", "market_family": "PLAYER_POINTS", "threshold": 1}, "status": "PENDING"}]}
        bet = {"paper_bet_id": "T1", "legs_json": json.dumps(legs), "settlement_json": json.dumps(settlement), "created_at_utc": "2026-10-08T20:00:00+00:00", "result_status": "LOSS",
               "stake": 10.0, "entry_odds": 200.0, "conservative_probability": 0.3}
        with mock.patch.object(dr, "closing_price", return_value={"american": None, "reason": "n/a"}):
            t = dr.ticket_review(bet, None, None)
        self.assertEqual([d["severity"] for d in t["defects"]], ["NOTE"])
        self.assertEqual(t["reading"], "VARIANCE")


class TestDailyPredictionsBlock(unittest.TestCase):
    def test_the_review_scores_the_frozen_day_and_reports_exposure_participation_and_actions(self):
        from operational import daily_review as dr
        from operational import shadow_selection as sh
        base = ["2026-10-11T23:00:00Z", "2026-10-11T19:00:00Z", True]

        def row(game, pid, hits, final, in_ticket):
            return [game, pid, "PLAYER_SOG_ALTERNATE", 2, -185, 0.63, f"P{pid}", "T"] + base + [hits, 30, 0.63, 0.65, final, final, "SUPPORTED", in_ticket, "2026-10-11T19:00:00Z"]
        rec = {"et_date": "2026-10-11", "pool_fields": sh.POOL_FIELDS, "variants": {}, "pool": [row("g1", "a", 24, 0.726, True), row("g2", "b", 26, 0.779, True), row("g2", "c", 20, 0.62, False)],
               "proposed_tickets": [{"legs": [["g1", "a", "PLAYER_SOG_ALTERNATE", 2, -185, 0.726, "Pa"], ["g2", "b", "PLAYER_SOG_ALTERNATE", 2, -260, 0.779, "Pb"]], "hit_probability": 0.565, "price": 113, "ev": 0.2, "recorded": True}]}
        outcomes = {("g1", "a"): {"status": "FINAL", "hit": True}, ("g2", "b"): {"status": "NO_PARTICIPATION"}, ("g2", "c"): {"status": "FINAL", "hit": True}}

        class FakeScore:
            @staticmethod
            def db_resolver(path):
                return lambda g, p, fam, k: outcomes[(g, p)]
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))
        import score_selection_shadow as real
        FakeScore.score = staticmethod(real.score)
        conn = mock.Mock()
        conn.execute.return_value.fetchone.return_value = ["x", "main", "/tmp/nhl.db"]
        bets = [{"paper_bet_id": f"T{i}", "created_at_utc": "2026-10-11T20:00:00+00:00", "origin": "AUTOMATIC",
                 "legs_json": json.dumps([{"market_family": "PLAYER_SOG_ALTERNATE", "participant_name": "Shared Player", "game_id": f"G{i}"}, {"market_family": "MONEYLINE", "participant_name": "TOR", "game_id": "G9"}])}
                for i in (1, 2)]
        with mock.patch.object(dr, "_score_module", return_value=FakeScore):
            out = dr.predictions_block("2026-10-11", bets, conn, records=[rec])
        self.assertEqual((out["resolved"], out["did_not_play"]), (2, 1))
        self.assertEqual(out["selected_tickets"][0]["result"], "DID_NOT_PLAY")
        self.assertEqual(out["exposure"]["players_on_several_tickets"][0]["player"], "Shared Player")          # shared exposure is named
        self.assertEqual([g["game_id"] for g in out["exposure"]["games_on_several_tickets"]], ["G9"])
        self.assertTrue(any("did not play" in a for a in out["corrective_actions"]))
        self.assertTrue(any("did not play" in x for x in out["participation_and_data"]))


if __name__ == "__main__":
    unittest.main()
