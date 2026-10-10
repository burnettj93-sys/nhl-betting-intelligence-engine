"""The ticket policy that enforces the objective (a strong estimated chance and meaningful value, up to five tickets, empty slots allowed), kept as a PROPOSAL: it cannot go live
without the owner approving its exact digest, and the selector's earlier behaviour is unchanged when no policy is passed."""
from __future__ import annotations

import datetime as dt
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from operational import recording_pause as rp
from operational import state_paths
from operational import ticket_policy as tp
from research.real_market_parlay import engine as rmp
from research.real_market_parlay import policy as P

NOW = dt.datetime(2026, 10, 10, 16, 0, tzinfo=dt.timezone.utc)


def leg(game, pid, p, price, name=None, family="PLAYER_SOG_ALTERNATE", k=2):
    return rmp.ParlayLeg(game_id=str(game), event_id=None, market_family=family, participant_id=str(pid), participant_name=name or f"P{pid}", side="OVER", threshold=k, american_price=price,
                         conservative_probability=p, sportsbook="draftkings", captured_at_utc=NOW.isoformat(), provider_contract_verified=True, model_threshold_eligible=True,
                         identity_resolved=True, price_fresh=True, event_not_started=True, team="T", opponent="O", game_start_utc="2026-10-10T23:00:00Z")


def strong_pool(n_games=10):
    """Ten games, one strong leg each (about 65% at a price that leaves value after a 5-point shading)."""
    return [leg(g, g * 10, 0.65, -105, f"Strong{g}") for g in range(1, n_games + 1)]


class TestPolicyObject(unittest.TestCase):
    def test_the_digest_changes_when_any_number_changes(self):
        a = P.PROPOSED
        self.assertNotEqual(a.digest(), P.LEGACY.digest())
        import dataclasses
        self.assertNotEqual(a.digest(), dataclasses.replace(a, floor=0.30).digest())
        self.assertEqual(a.digest(), dataclasses.replace(a).digest())

    def test_the_proposal_keeps_five_slots_and_allows_none(self):
        self.assertEqual((P.PROPOSED.slots, P.LEGACY.slots), (5, 5))
        self.assertIn("up to 5 tickets a day (zero is allowed)", P.PROPOSED.summary())

    def test_it_is_labelled_as_a_proposal_not_a_result(self):
        self.assertIn("NOT_VALIDATED", P.PROPOSED.status)
        self.assertIn("AWAITING_OWNER_APPROVAL", P.PROPOSED.status)


class TestSelectorEnforcesTheObjective(unittest.TestCase):
    def test_without_a_policy_the_selector_is_unchanged(self):
        pool = strong_pool()
        a = rmp.select_tickets(pool, max_tickets=5)
        b = rmp.select_tickets(pool, max_tickets=5, policy=None)
        self.assertEqual([[l.participant_id for l in t.legs] for t in a["tickets"]], [[l.participant_id for l in t.legs] for t in b["tickets"]])

    def test_a_weak_ticket_that_the_old_rules_accept_is_refused_with_the_reason(self):
        weak = [leg(1, 10, 0.45, 150, "A"), leg(2, 20, 0.45, 150, "B")]                 # 20% hit chance at +525: clears the old EV tests
        old = rmp.select_tickets(weak, max_tickets=5)
        new = rmp.select_tickets(weak, max_tickets=5, policy=P.PROPOSED)
        self.assertEqual(len(old["tickets"]), 1)
        self.assertEqual(new["tickets"], [])
        self.assertEqual(new["considered"][0]["status"], "BELOW_HIT_CHANCE_FLOOR")
        self.assertIn("under the 25%", new["considered"][0]["reason"])
        self.assertIn("No ticket is worthwhile today", new["reason"])
        self.assertIn("A slot left empty is a correct answer", new["reason"])

    def test_a_strong_ticket_with_no_margin_is_refused_for_value(self):
        # 62% x 62% = 38% hit chance (above the floor) but priced so that, after the 3-point haircut, less than +3% edge is left (EV +1.5%)
        pool = [leg(1, 10, 0.62, -142, "A"), leg(2, 20, 0.62, -142, "B")]
        old = rmp.select_tickets(pool, max_tickets=5)
        new = rmp.select_tickets(pool, max_tickets=5, policy=P.PROPOSED)
        self.assertEqual(len(old["tickets"]), 1)
        self.assertEqual(new["tickets"], [])
        self.assertEqual(new["considered"][0]["status"], "VALUE_NOT_MEANINGFUL")

    def test_up_to_five_tickets_are_allowed_when_five_worthwhile_ones_exist(self):
        pool = strong_pool(10)                                                      # 10 games, each leg usable once: five disjoint pairs
        new = rmp.select_tickets(pool, max_tickets=P.PROPOSED.slots, policy=P.PROPOSED)
        self.assertEqual(len(new["tickets"]), 5)
        players = [l.participant_id for t in new["tickets"] for l in t.legs]
        self.assertEqual(len(players), len(set(players)))                           # one ticket per player

    def test_the_proposed_policy_limits_a_player_to_one_ticket(self):
        pool = strong_pool(3)                                                       # three players: only one disjoint pair exists
        new = rmp.select_tickets(pool, max_tickets=5, policy=P.PROPOSED)
        self.assertEqual(len(new["tickets"]), 1)
        old = rmp.select_tickets(pool, max_tickets=5, policy=P.LEGACY)
        self.assertGreater(len(old["tickets"]), 1)

    def test_every_ticket_the_proposal_takes_meets_its_own_floor_and_value_rule(self):
        for t in rmp.select_tickets(strong_pool(10), max_tickets=5, policy=P.PROPOSED)["tickets"]:
            self.assertGreaterEqual(t.joint_probability, P.PROPOSED.floor)
            shaded = 1.0
            for l in t.legs:
                shaded *= l.conservative_probability - P.PROPOSED.value_shade
            self.assertGreaterEqual(shaded * t.combined_decimal - 1.0, P.PROPOSED.min_value_after_shade - 1e-12)
            self.assertGreaterEqual(t.combined_decimal, 2.0)


class TestApprovalGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        patch = mock.patch.object(state_paths, "path", lambda name, **kw: d / name)
        patch.start()
        self.addCleanup(patch.stop)
        env = mock.patch.dict(os.environ, {"NHL_ENGINE_TICKET_POLICY": "proposed"})
        env.start()
        self.addCleanup(env.stop)

    def test_the_active_policy_is_the_proposal_and_unapproved(self):
        self.assertEqual(tp.active().policy_id, P.PROPOSED.policy_id)
        self.assertFalse(tp.is_approved())
        self.assertFalse(tp.describe()["approved"])

    def test_resume_is_refused_until_the_owner_approves_the_exact_policy(self):
        rp.pause("postmortem", now=NOW)
        with self.assertRaises(rp.ResumeRefused):
            rp.resume()
        self.assertTrue(rp.is_paused())

    def test_approving_a_different_digest_does_not_unlock_it(self):
        rp.pause("postmortem", now=NOW)
        with self.assertRaises(ValueError):
            tp.approve("deadbeefdeadbeef")
        with self.assertRaises(rp.ResumeRefused):
            rp.resume()

    def test_after_the_owner_approves_the_digest_resume_works_and_is_recorded(self):
        rp.pause("postmortem", now=NOW)
        doc = tp.approve(P.PROPOSED.digest(), approved_by="owner", now=NOW)
        self.assertEqual(doc["digest"], P.PROPOSED.digest())
        self.assertTrue(tp.is_approved())
        self.assertTrue(rp.resume()["paused"])
        self.assertFalse(rp.is_paused())

    def test_changing_the_policy_voids_an_earlier_approval(self):
        tp.approve(P.PROPOSED.digest(), now=NOW)
        import dataclasses
        changed = dataclasses.replace(P.PROPOSED, floor=0.30)
        self.assertFalse(tp.is_approved(changed))

    def test_the_legacy_selector_is_only_reachable_inside_a_test_run(self):
        with mock.patch.dict(os.environ, {"NHL_ENGINE_TICKET_POLICY": "legacy"}):
            self.assertEqual(tp.active().policy_id, P.LEGACY.policy_id)                  # under test
            with mock.patch.object(state_paths, "under_test", return_value=False):
                self.assertEqual(tp.active().policy_id, P.PROPOSED.policy_id)            # never in production


if __name__ == "__main__":
    unittest.main()
