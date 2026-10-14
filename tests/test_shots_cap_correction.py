"""The shots-estimate correction of 2026-10-13: the cap on a leg's final estimate over the model goes from +0.10 to +0.02 (docs/PROPOSAL_ANCHOR_CAP.md). One number changes; the old policy, its digest, the
entries recorded under it and the way they are labelled stay as they were."""
import dataclasses
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))
from operational import state_paths, ticket_policy as tp  # noqa: E402
from research.real_market_parlay import anchor  # noqa: E402
from research.real_market_parlay import engine as rmp  # noqa: E402
from research.real_market_parlay import policy as P  # noqa: E402
from tests.test_anchor_tickets import oct12_legs, shots  # noqa: E402


class TestThePolicies(unittest.TestCase):
    def test_the_approved_policy_keeps_its_digest_and_the_new_one_differs_only_by_the_cap(self):
        self.assertEqual(P.ANCHOR_V1.digest(), "c236c526e00b9f04")                                    # the digest the owner approved on 2026-10-11 and every earlier ticket was recorded under
        self.assertEqual(P.ANCHOR_V2.digest(), "9f5e3448e10ff62e")                                    # the cap-correction policy keeps the digest shown for review on 2026-10-13
        self.assertNotEqual(P.ANCHOR_V2.digest(), P.ANCHOR_V1.digest())
        a, b = P.ANCHOR_V1.as_dict(), P.ANCHOR_V2.as_dict()
        self.assertNotIn("anchor_cap", a)                                                              # absent when None, which is why the old digest did not move
        self.assertEqual(b["anchor_cap"], 0.02)
        self.assertEqual({k for k in set(a) | set(b) if a.get(k) != b.get(k)}, {"policy_id", "anchor_cap"})   # nothing else loosened or tightened
        self.assertEqual(P.SUPERSEDED_BY[P.ANCHOR_V1.digest()], P.ANCHOR_V2.policy_id)
        self.assertEqual(P.SUPERSEDED_BY[P.ANCHOR_V2.digest()], P.PROPOSED.policy_id)

    def test_the_policy_and_the_estimate_code_state_the_same_cap(self):
        self.assertEqual(anchor.cap_for(P.PROPOSED), anchor.ANCHOR_CAP)
        self.assertEqual(anchor.cap_for(P.ANCHOR_V2), anchor.ANCHOR_CAP)
        self.assertEqual(anchor.cap_for(P.ANCHOR_V1), anchor.ANCHOR_CAP_V1)
        self.assertEqual((anchor.ANCHOR_CAP_V1, anchor.ANCHOR_CAP), (0.10, 0.02))

    def test_the_earlier_approval_does_not_carry_to_the_new_digest(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(state_paths, "path", lambda name, **kw: Path(d) / name), mock.patch.dict(os.environ, {"NHL_ENGINE_TICKET_POLICY": "anchor_v1"}):
                tp.approve(P.ANCHOR_V1.digest())
                self.assertTrue(tp.is_approved(P.ANCHOR_V1))
            with mock.patch.object(state_paths, "path", lambda name, **kw: Path(d) / name), mock.patch.dict(os.environ, {"NHL_ENGINE_TICKET_POLICY": "proposed"}):
                self.assertEqual(tp.active().policy_id, "primary-slip-2026-10-14")
                self.assertFalse(tp.is_approved())                                                      # the new policy waits for the owner's own approval
                with self.assertRaises(ValueError):
                    tp.approve(P.ANCHOR_V1.digest())                                                    # approving the old digest does not approve the active one


class TestTheEstimate(unittest.TestCase):
    def leg(self):
        return shots(1, 1, 30, +100, 0.60)                    # record 30 of 30 (shrunk about 0.86), model 0.60

    def test_a_record_far_above_the_model_is_held_to_the_model_plus_two_points(self):
        self.assertAlmostEqual(anchor.leg_estimate(self.leg()).p, 0.62)
        self.assertAlmostEqual(anchor.leg_estimate(self.leg(), anchor.ANCHOR_CAP_V1).p, 0.70)       # what the superseded rule said
        self.assertIn("+2 points", anchor.leg_estimate(self.leg()).basis)
        self.assertIn("+10 points", anchor.leg_estimate(self.leg(), anchor.ANCHOR_CAP_V1).basis)

    def test_a_record_close_to_the_model_is_unchanged(self):
        leg = shots(1, 1, 22, -150, 0.68)                      # shrunk record about 0.69
        self.assertAlmostEqual(anchor.leg_estimate(leg).p, anchor.leg_estimate(leg, anchor.ANCHOR_CAP_V1).p, places=4)

    def test_entries_carry_the_rule_they_were_made_under(self):
        new = anchor.prepare_pool([shots(1, 1, 26, -260, 0.695)])[0]
        old = anchor.prepare_pool([shots(1, 1, 26, -260, 0.695)], anchor.ANCHOR_CAP_V1)[0]
        self.assertTrue(new.model_version.endswith("+anchor-last30-cap02"))
        self.assertTrue(old.model_version.endswith("+anchor-last30"))
        self.assertIsNone(anchor.estimate_rule_note("player-rate-toi-v2+platt-2026-10+anchor-last30-cap02,code:abc"))
        self.assertIn("over-stated", anchor.estimate_rule_note("player-rate-toi-v2+platt-2026-10+anchor-last30,code:6b77fd6e99"))   # the two tickets of 2026-10-12
        self.assertIsNone(anchor.estimate_rule_note("player-rate-toi-v2+platt-2026-10"))                                              # a moneyline-only or earlier-engine entry

    def test_a_recorded_ticket_from_the_old_rule_is_labelled_and_its_numbers_are_untouched(self):
        from operational import daily_tickets as dtk
        legs = [{"participant_name": "A", "participant_id": "1", "game_id": "g1", "threshold": 2, "american_price": -105.0, "model_probability": 0.5457, "conservative_probability": 0.6207,
                 "market_family": "PLAYER_SOG_ALTERNATE", "model_version": "player-rate-toi-v2+platt-2026-10+anchor-last30"},
                {"participant_name": "B", "participant_id": "2", "game_id": "g2", "threshold": 2, "american_price": -450.0, "model_probability": 0.7789, "conservative_probability": 0.8312,
                 "market_family": "PLAYER_SOG_ALTERNATE", "model_version": "player-rate-toi-v2+platt-2026-10+anchor-last30"}]
        row = {"paper_bet_id": "T1", "legs_json": json.dumps(legs), "entry_odds": 138.6243386243387, "model_probability": 0.5159258400000001, "ev": 0.231, "stake": 10.0,
               "created_at_utc": "2026-10-12T21:18:55+00:00", "event_start_utc": "2026-10-12T23:00:00Z", "result_status": "WIN", "profit_loss": 13.862, "settled_at_utc": "2026-10-13T03:11:05Z",
               "model_version": "player-rate-toi-v2+platt-2026-10+anchor-last30,code:6b77fd6e99", "origin": "AUTOMATIC", "settlement_json": None, "provenance_json": None, "is_combo": 1, "market_id": "x"}
        import datetime as dt
        card = dtk.ticket_from_row(row, dt.datetime(2026, 10, 13, 12, 0, tzinfo=dt.timezone.utc))
        self.assertIn("earlier shots rule", card["estimate_note"])
        self.assertEqual(card["hit_probability"], 0.5159258400000001)                                  # the entry probability is exactly what was recorded
        self.assertEqual([l["probability"] for l in card["legs"]], [0.6207, 0.8312])


class TestTheSelectionUnderTheCorrection(unittest.TestCase):
    def test_the_2026_10_12_pool_no_longer_produces_those_tickets(self):
        legs = oct12_legs()
        v1 = rmp.select_tickets(legs, max_tickets=5, policy=P.ANCHOR_V1)
        v2 = rmp.select_tickets(legs, max_tickets=5, policy=P.ANCHOR_V2)
        self.assertTrue(v1["tickets"])                                       # the superseded rule still reproduces the recorded behaviour
        self.assertEqual(v2["tickets"], [])
        self.assertLess(v2["pool_size"], v1["pool_size"])                    # fewer legs are "well supported": the legs whose record ran far above the model drop out
        self.assertIn("A slot left empty is a correct answer", v2["reason"])

    def test_no_other_threshold_is_changed_to_compensate(self):
        for field in ("floor", "min_estimated_ev", "min_value_after_shade", "value_shade", "max_per_leg", "max_per_game", "max_per_player", "slots", "min_combined_decimal"):
            self.assertEqual(getattr(P.ANCHOR_V2, field), getattr(P.ANCHOR_V1, field), field)
        for field in ("floor", "min_estimated_ev", "min_value_after_shade", "value_shade", "max_per_leg", "max_per_game", "max_per_player", "min_combined_decimal", "anchor_cap"):
            self.assertEqual(getattr(P.PROPOSED, field), getattr(P.ANCHOR_V2, field), field)      # the primary slip changes only the number of slips and the +500 ceiling
        self.assertEqual((anchor.SHOTS_LEG_FLOOR, anchor.HIGH_LINE_CAP, anchor.MIN_SAMPLE_GAMES), (0.60, 0.70, 20))

    def test_the_builder_shows_the_estimate_a_ticket_would_use(self):
        leg = shots(1, 1, 30, +100, 0.60)
        with mock.patch.dict(os.environ, {"NHL_ENGINE_TICKET_POLICY": "proposed"}):
            self.assertAlmostEqual(anchor.shots_final_estimate(0.60, leg.empirical_probability, 2, anchor.cap_for(tp.active())), 0.62)


class TestScoringKeepsTheRulesApart(unittest.TestCase):
    def test_legs_logged_under_each_rule_are_reported_separately(self):
        import score_selection_shadow as sss
        from operational import shadow_selection as sh

        def row(game, pid, final, in_ticket):
            base = [game, pid, "PLAYER_SOG_ALTERNATE", 2, -150, 0.62, f"P{pid}", "T", "2026-10-12T23:00:00Z", "2026-10-12T12:00:00Z", True]
            return base + [22, 30, 0.58, 0.65, final, final, "SUPPORTED", in_ticket, "2026-10-12T12:00:00Z"]
        old = {"et_date": "2026-10-12", "pool_fields": sh.POOL_FIELDS, "variants": {}, "selector": {"digest": "c236c526e00b9f04"}, "pool": [row("g1", "a", 0.68, True)]}
        new = {"et_date": "2026-10-13", "pool_fields": sh.POOL_FIELDS, "variants": {}, "selector": {"digest": "NEWDIGEST"}, "pool": [row("g2", "b", 0.60, True)]}
        rep = sss.score([old, new], lambda g, p, fam, k: {"status": "FINAL", "hit": True})["anchor_estimates"]["by_policy"]
        self.assertEqual(set(rep), {"c236c526e00b9f04", "NEWDIGEST"})
        self.assertAlmostEqual(rep["c236c526e00b9f04"]["supported_pool"]["final_estimate"], 0.68)
        self.assertAlmostEqual(rep["NEWDIGEST"]["supported_pool"]["final_estimate"], 0.60)
        self.assertEqual(rep["NEWDIGEST"]["on_selected_tickets"]["legs"], 1)


if __name__ == "__main__":
    unittest.main()
