"""
Tests for the Production Sweep Safety Cap block (2026-09-29). Covers the
real, confirmed gap: operational/live_odds_daily_pull.py's sweep loop
(prop-sweep-first/prop-sweep-second) previously only called
prop_discovery.may_spend() when mode() == DISCOVERY -- once >=1 contract was
VERIFIED, no quota check of any kind (not even the global odds_quota hard
reserve) ran in that loop. may_spend() now applies a real per-day budget in
BOTH modes (DISCOVERY_DAILY_BUDGET / VERIFIED_PRODUCTION_DAILY_BUDGET) and
always defers to odds_quota.evaluate_spend() for the hard reserve and
quota-unknown-fails-closed behavior.
"""
from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from operational import prop_discovery as pd
from research.generic_prop_pricing import provider_adapter as pa

D = lambda h=12, m=0: dt.datetime(2026, 9, 29, h, m, tzinfo=dt.timezone.utc)


def _verified_only():
    """Real, current certification state: MONEYLINE, PLAYER_SOG_ALTERNATE,
    ALTERNATE_TEAM_TOTAL -- forces mode() == VERIFIED_PRODUCTION."""
    return mock.patch.object(pa, "VERIFIED_CONTRACTS", frozenset({
        ("draftkings", "MONEYLINE"), ("draftkings", "PLAYER_SOG_ALTERNATE"),
        ("draftkings", "ALTERNATE_TEAM_TOTAL")}))


def _unverified():
    return mock.patch.object(pa, "VERIFIED_CONTRACTS", frozenset({("draftkings", "MONEYLINE")}))


class TestSweepRequestsTheRealWorkingSogShape(unittest.TestCase):
    """Platform Recovery block (2026-09-29): FIRST_SWEEP_MARKETS previously
    requested the standard SOG shape, which provider_adapter.VERIFIED_CONTRACTS'
    own evidence says DraftKings has never posted a real quote for -- meaning
    prop-sweep-first/second (every 15-30 min, all day) were spending real
    credits on a market that could never produce a real leg, while the one
    market that actually works (the certified alternate ladder) was only ever
    requested once daily by the unrelated --mode=props job. Confirmed against
    the real archive: the freshest real alternate-SOG quote was 615 minutes
    old against a real 10-minute policy at that time-to-puck-drop (61x over)."""

    def test_the_sweep_requests_the_certified_alternate_shape_not_the_dead_standard_one(self):
        from operational import live_odds_daily_pull as lop
        keys = set(lop.FIRST_SWEEP_MARKETS.split(","))
        self.assertIn("player_shots_on_goal_alternate", keys)
        self.assertNotIn("player_shots_on_goal", keys)

    def test_saves_candidate_discovery_is_unaffected_by_the_swap(self):
        from operational import live_odds_daily_pull as lop
        self.assertIn("player_total_saves", lop.FIRST_SWEEP_MARKETS.split(","))

    def test_the_swap_keeps_the_audited_two_key_cost_structure(self):
        # The Production Sweep Safety Cap block's own 32-credit/day worst-case
        # audit assumed exactly 2 markets per event -- this must still hold
        # regardless of which two keys they are.
        from operational import live_odds_daily_pull as lop
        self.assertEqual(len(lop.FIRST_SWEEP_MARKETS.split(",")), 2)

    def test_health_invariants_still_pass_after_the_swap(self):
        for check in pd.health_invariants():
            self.assertTrue(check["ok"], check["check"])


class TestShapeSpecificMapping(unittest.TestCase):
    def test_alternate_sog_maps_to_its_own_certified_contract(self):
        self.assertEqual(pd.MARKET_TO_CONTRACT["player_shots_on_goal_alternate"], "PLAYER_SOG_ALTERNATE")

    def test_standard_sog_still_maps_to_the_bare_unverified_family(self):
        self.assertEqual(pd.MARKET_TO_CONTRACT["player_shots_on_goal"], "PLAYER_SOG")

    def test_standard_sog_is_not_verified_by_the_alternate_certification(self):
        with _verified_only():
            self.assertFalse(pa.is_contract_verified("draftkings", "PLAYER_SOG"))
            self.assertTrue(pa.is_contract_verified("draftkings", "PLAYER_SOG_ALTERNATE"))

    def test_real_mode_is_verified_production_with_the_real_current_contracts(self):
        # This is the REAL, current, shipped state -- not a hypothetical.
        self.assertEqual(pd.mode(), pd.VERIFIED_PRODUCTION)


class TestDailyCapByMode(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "spend.json"

    def test_discovery_mode_uses_the_small_discovery_budget(self):
        with _unverified():
            pd.record_spend(pd.DISCOVERY_DAILY_BUDGET, D(), self.tmp)
            gate = pd.may_spend(D(), path=self.tmp)
        self.assertFalse(gate["allow"])
        self.assertEqual(gate["reason"], "DISCOVERY_DAILY_BUDGET")
        self.assertEqual(gate["budget"], pd.DISCOVERY_DAILY_BUDGET)

    def test_verified_production_uses_the_larger_but_still_hard_budget(self):
        with _verified_only():
            pd.record_spend(pd.DISCOVERY_DAILY_BUDGET, D(), self.tmp)  # 6 spent -- would exceed DISCOVERY's own cap
            gate = pd.may_spend(D(), path=self.tmp, remaining=999)
        # 6 spent < 40 VERIFIED_PRODUCTION budget -- allowed (the point: a
        # different, real, LARGER cap applies, not "no cap at all").
        self.assertTrue(gate["allow"])

    def test_verified_production_cap_is_a_real_hard_stop(self):
        with _verified_only():
            pd.record_spend(pd.VERIFIED_PRODUCTION_DAILY_BUDGET, D(), self.tmp)
            gate = pd.may_spend(D(), path=self.tmp, remaining=999)
        self.assertFalse(gate["allow"])
        self.assertEqual(gate["reason"], "VERIFIED_PRODUCTION_DAILY_BUDGET")
        self.assertEqual(gate["budget"], pd.VERIFIED_PRODUCTION_DAILY_BUDGET)

    def test_the_verified_production_budget_is_still_far_below_uncapped(self):
        # A real, evidence-derived ceiling (see prop_discovery.py's own
        # VERIFIED_PRODUCTION_DAILY_BUDGET comment) -- not "no limit".
        self.assertLess(pd.VERIFIED_PRODUCTION_DAILY_BUDGET, 100)
        self.assertGreater(pd.VERIFIED_PRODUCTION_DAILY_BUDGET, pd.DISCOVERY_DAILY_BUDGET)


class TestGlobalReserveAndUnknownQuota(unittest.TestCase):
    """Restored, real global behavior -- previously skipped entirely for the
    sweep loop once VERIFIED_PRODUCTION was reached."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "spend.json"

    def test_hard_reserve_still_applies_in_verified_production(self):
        with _verified_only():
            gate = pd.may_spend(D(), path=self.tmp, remaining=21, planned=5)  # 21-5=16 < RESERVE(20)
        self.assertFalse(gate["allow"])
        self.assertEqual(gate["reason"], "HARD_RESERVE")

    def test_unknown_quota_fails_closed_in_verified_production(self):
        with _verified_only(), mock.patch("operational.odds_quota.latest_remaining", return_value=None):
            gate = pd.may_spend(D(), path=self.tmp)
        self.assertFalse(gate["allow"])
        self.assertEqual(gate["reason"], "QUOTA_UNKNOWN")

    def test_unknown_quota_fails_closed_in_discovery_too(self):
        with _unverified(), mock.patch("operational.odds_quota.latest_remaining", return_value=None):
            gate = pd.may_spend(D(), path=self.tmp)
        self.assertFalse(gate["allow"])
        self.assertEqual(gate["reason"], "QUOTA_UNKNOWN")


class TestSharedDurableStateAcrossSweepJobs(unittest.TestCase):
    """prop-sweep-first and prop-sweep-second call the SAME may_spend()/
    record_spend() against the SAME state file -- proving the daily cap is
    naturally shared and survives an independent process restart of either
    job (the state is a durable JSON file, not in-memory)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "shared_spend.json"

    def test_spend_recorded_by_one_job_counts_against_the_others_budget(self):
        with _verified_only():
            # "prop-sweep-first" spends most of the day's budget.
            pd.record_spend(35, D(9, 0), self.tmp)
            # A fresh "prop-sweep-second" process (a brand-new call, same file)
            # must see that spend and refuse to exceed the shared cap.
            gate = pd.may_spend(D(9, 15), path=self.tmp, planned=10, remaining=999)
        self.assertFalse(gate["allow"])
        self.assertEqual(gate["reason"], "VERIFIED_PRODUCTION_DAILY_BUDGET")

    def test_state_survives_being_reloaded_from_disk_restart_safe(self):
        with _verified_only():
            pd.record_spend(12, D(9, 0), self.tmp)
        # Simulate a restarted process: nothing but the file on disk persists.
        reloaded = pd.load_state(self.tmp)
        self.assertEqual(reloaded["days"][D(9, 0).date().isoformat()]["credits"], 12)
        with _verified_only():
            self.assertEqual(pd.spent_today(D(9, 30), self.tmp), 12)


class TestDuplicateAndFreshQuoteSkip(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "swept.json"

    def test_an_event_already_swept_today_is_never_requeried(self):
        # This IS the project's real "don't re-spend on an already-sufficiently-
        # fresh quote" mechanism for the sweep's own once-per-stage-per-day
        # cadence -- SOG/Saves props don't need re-fetching multiple times
        # within a single day-stage by design (Quota + Moneyline Activation
        # block, 2026-09-25).
        pd.mark_swept("first", "evt-1", D(9, 0), self.tmp)
        self.assertTrue(pd.already_swept("first", "evt-1", D(9, 20), self.tmp))

    def test_a_different_stage_is_not_considered_already_swept(self):
        pd.mark_swept("first", "evt-1", D(9, 0), self.tmp)
        self.assertFalse(pd.already_swept("second", "evt-1", D(9, 20), self.tmp))

    def test_a_new_day_resets_the_already_swept_flag(self):
        pd.mark_swept("first", "evt-1", D(9, 0), self.tmp)
        self.assertFalse(pd.already_swept("first", "evt-1", D(9, 0) + dt.timedelta(days=1), self.tmp))


class TestSweepLoopCallsMaySpendInEveryMode(unittest.TestCase):
    """Integration-level proof that operational/live_odds_daily_pull.py's real
    sweep loop now calls may_spend() (and therefore the real global hard
    reserve / VERIFIED_PRODUCTION cap) even when mode() == VERIFIED_PRODUCTION
    -- the exact gap this block closes."""

    def test_verified_production_sweep_stops_at_the_new_daily_cap(self):
        from operational import live_odds_daily_pull as lop

        class _Resp:
            ok = True
            data = []
            error = None
            requests_remaining = 999
            requests_last = 2
            retrieved_at_utc = "2026-09-29T12:00:00Z"

        class _EventsResp(_Resp):
            data = [{"id": f"{i:032x}", "commence_time": (D(9, 0) + dt.timedelta(hours=4)).isoformat()}
                    for i in range(10)]

        tmp_state = Path(tempfile.mkdtemp()) / "sweep_spend.json"
        with _verified_only(), \
             mock.patch("operational.prop_discovery.STATE_PATH", tmp_state), \
             mock.patch.object(lop.client, "get_nhl_events", return_value=_EventsResp()), \
             mock.patch.object(lop.client, "get_event_odds", return_value=_Resp()), \
             mock.patch.object(lop.archive, "archive_result"), \
             mock.patch.object(lop, "_now_utc", return_value=D(9, 0)):
            # Pre-spend right up to the new cap so the very first event in the
            # loop must already be refused -- proving may_spend() is actually
            # consulted in VERIFIED_PRODUCTION mode now (it previously never
            # was, and this call would have sailed through unmetered).
            pd.record_spend(pd.VERIFIED_PRODUCTION_DAILY_BUDGET, D(9, 0), tmp_state)
            result = lop.run_targeted_prop_sweep("first")
        self.assertEqual(result["events_queried"], 0)
        self.assertIn("VERIFIED_PRODUCTION_DAILY_BUDGET", result["reason"])


class TestT35Untouched(unittest.TestCase):
    def test_no_t35_module_references_prop_discovery_or_sweep_budget(self):
        import inspect
        from operational import moneyline_pregame as t35
        src = inspect.getsource(t35)
        self.assertNotIn("prop_discovery", src)
        self.assertNotIn("VERIFIED_PRODUCTION_DAILY_BUDGET", src)


if __name__ == "__main__":
    unittest.main()
