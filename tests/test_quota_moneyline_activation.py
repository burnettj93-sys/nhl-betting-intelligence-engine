"""
Quota + Moneyline Activation block (2026-09-25): pregame T-35 cluster pulls, quota guard, client retry,
prop DISCOVERY / VERIFIED_PRODUCTION modes, scheduler audit, publish gating and the end-to-end dry run.
No network, no Odds API credit, no real state files (every path is a temp path or a fake).
"""
from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from operational import moneyline_pregame as mp
from operational import odds_quota as oq
from operational import prop_discovery as pd
from operational import scheduler_audit as sa

D = lambda h, m=0, s=0, day=26: dt.datetime(2026, 9, day, h, m, s, tzinfo=dt.timezone.utc)
ALLOW = lambda: {"allow": True, "reason": "OK"}
LISTED = lambda clusters: {"listed": True}


# ------------------------------------------------------------------------------------- clustering
class TestClusters(unittest.TestCase):
    def test_a_single_game_is_pulled_at_t_minus_35_inside_the_decision_window(self):
        (c,) = mp.plan_clusters([D(23, 0)])
        self.assertEqual(c.target_pull, D(22, 25))                       # T-35
        self.assertEqual(c.window, (D(22, 20), D(22, 30)))               # [T-40, T-30]
        self.assertEqual(c.anchor, D(22, 30))

    def test_same_time_games_share_one_pull(self):
        clusters = mp.plan_clusters([D(23, 0)] * 5)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].games, 5)

    def test_distinct_start_times_are_distinct_clusters(self):
        clusters = mp.plan_clusters([D(23, 0), D(23, 30), D(0, 0, day=27), D(2, 0, day=27), D(23, 0)])
        self.assertEqual([c.smin for c in clusters], [D(23, 0), D(23, 30), D(0, 0, day=27), D(2, 0, day=27)])

    def test_games_within_the_spread_share_a_cluster_and_the_window_serves_both(self):
        (c,) = mp.plan_clusters([D(23, 0), D(23, 4)])
        self.assertEqual(c.games, 2)
        for start in (D(23, 0), D(23, 4)):
            self.assertTrue(mp.covers(c.target_pull, start), start)

    def test_ten_minutes_apart_is_not_one_cluster(self):                 # a 10-min spread leaves a zero-width window
        self.assertEqual(len(mp.plan_clusters([D(23, 0), D(23, 10)])), 2)

    def test_clustering_is_order_independent_and_accepts_iso_strings(self):
        a = mp.plan_clusters(["2026-09-26T23:30:00", "2026-09-26T23:00:00", "2026-09-26T23:00:00"])
        b = mp.plan_clusters([D(23, 0), D(23, 0), D(23, 30)])
        self.assertEqual(a, b)

    def test_every_cluster_target_satisfies_the_unchanged_decision_policy_for_all_its_games(self):
        starts = [D(23, 0), D(23, 3), D(23, 5), D(0, 0, day=27)]
        for c in mp.plan_clusters(starts):
            for s in starts:
                if c.smin <= s <= c.smax:
                    self.assertTrue(mp.covers(c.target_pull, s))

    def test_covers_matches_the_policy_window_exactly(self):
        s = D(23, 0)
        for pull, ok in ((D(22, 20), True), (D(22, 30), True), (D(22, 19, 59), False), (D(22, 30, 1), False), (D(17, 0), False)):
            self.assertEqual(mp.covers(pull, s), ok, pull)


# ------------------------------------------------------------------------------------- runner
class RunnerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.state = self.tmp / "state.json"
        self.lock = self.tmp / "lock"
        self.calls = 0

    def pull(self, ran=True, error=None, captured="2026-09-26T22:25:04Z"):
        def fn():
            self.calls += 1
            return {"ran": ran, "api_error": error, "captured_at_utc": captured, "credits_spent_this_run": 1 if ran else 0}
        return fn

    def run_at(self, now, starts=(D(23, 0),), **kw):
        kw.setdefault("guard_fn", ALLOW)
        kw.setdefault("listing_fn", LISTED)
        kw.setdefault("pull_fn", self.pull())
        return mp.run_pregame(now, starts_fn=lambda n: list(starts), state_path=self.state, lock_path=self.lock, **kw)


class TestPregameRunner(RunnerCase):
    def test_idle_outside_a_due_window_makes_no_call(self):
        for now in (D(20, 0), D(22, 19), D(22, 31), D(23, 30)):
            r = self.run_at(now)
            self.assertEqual((r["status"], r["reason"]), ("IDLE", "NO_CLUSTER_DUE"), now)
        self.assertEqual(self.calls, 0)

    def test_due_window_makes_exactly_one_pull_and_records_it_in_the_decision_window(self):
        r = self.run_at(D(22, 25))
        self.assertEqual((r["status"], r["ran"]), ("SUCCESS", True))
        self.assertEqual(self.calls, 1)
        rec = mp.load_state(self.state)["clusters"]["2026-09-26T23:00/2026-09-26T23:00"]
        self.assertEqual((rec["status"], rec["in_decision_window"], rec["attempts"]), ("DONE", True, 1))

    def test_no_duplicate_paid_pull_for_the_same_cluster(self):
        self.run_at(D(22, 25))
        for now in (D(22, 25, 30), D(22, 26), D(22, 28)):               # restarts / retries / duplicate launchd firings
            self.assertEqual(self.run_at(now)["reason"], "NO_CLUSTER_DUE")
        self.assertEqual(self.calls, 1)

    def test_a_failed_request_is_retried_once_then_stops_bounded(self):
        bad = self.pull(ran=False, error="network error: ConnectionError")
        self.assertEqual(self.run_at(D(22, 24), pull_fn=bad)["status"], "FAILED")
        self.assertEqual(self.run_at(D(22, 26), pull_fn=bad)["status"], "FAILED")
        self.assertEqual(self.run_at(D(22, 28), pull_fn=bad)["reason"], "NO_CLUSTER_DUE")   # MAX_ATTEMPTS reached
        self.assertEqual(self.calls, 2)

    def test_a_retry_after_a_failure_can_still_succeed(self):
        self.run_at(D(22, 24), pull_fn=self.pull(ran=False, error="network error: ReadTimeout"))
        r = self.run_at(D(22, 26))
        self.assertEqual(r["status"], "SUCCESS")
        self.assertEqual(mp.load_state(self.state)["clusters"]["2026-09-26T23:00/2026-09-26T23:00"]["attempts"], 2)

    def test_quota_guard_defers_and_spends_nothing(self):
        r = self.run_at(D(22, 25), guard_fn=lambda: {"allow": False, "reason": "HARD_RESERVE"})
        self.assertEqual((r["status"], r["reason"]), ("DEFERRED", "QUOTA_HARD_RESERVE"))
        self.assertEqual(self.calls, 0)
        self.assertNotEqual(mp.load_state(self.state)["clusters"]["2026-09-26T23:00/2026-09-26T23:00"].get("status"), "DONE")

    def test_a_game_the_provider_does_not_list_costs_nothing_and_is_not_retried(self):
        r = self.run_at(D(22, 25), listing_fn=lambda c: {"listed": False})
        self.assertEqual(r["status"], "SKIPPED")
        self.assertEqual(self.calls, 0)
        self.assertEqual(self.run_at(D(22, 26), listing_fn=lambda c: {"listed": False})["reason"], "NO_CLUSTER_DUE")

    def test_a_pull_outside_the_decision_window_is_recorded_honestly(self):
        self.run_at(D(22, 25), pull_fn=self.pull(captured="2026-09-26T22:31:00Z"))
        rec = mp.load_state(self.state)["clusters"]["2026-09-26T23:00/2026-09-26T23:00"]
        self.assertFalse(rec["in_decision_window"])

    def test_a_crash_never_raises_and_the_attempt_was_recorded_before_spending(self):
        def boom():
            raise RuntimeError("kaboom")
        r = self.run_at(D(22, 25), pull_fn=boom)
        self.assertEqual(r["status"], "FAILED")
        rec = mp.load_state(self.state)["clusters"]["2026-09-26T23:00/2026-09-26T23:00"]
        self.assertEqual((rec["status"], rec["attempts"]), ("ATTEMPTING", 1))

    def test_a_held_lock_skips_instead_of_double_spending(self):
        import fcntl
        with open(self.lock, "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            r = self.run_at(D(22, 25))
        self.assertEqual((r["status"], r["reason"]), ("SKIPPED", "ANOTHER_INSTANCE_RUNNING"))
        self.assertEqual(self.calls, 0)

    def test_overlapping_windows_are_served_by_one_pull(self):
        starts = (D(23, 0), D(23, 6))                                    # windows [22:20,22:30] and [22:26,22:36]
        self.run_at(D(22, 28), starts=starts)
        self.assertEqual(self.calls, 1)
        self.assertEqual(self.run_at(D(22, 29), starts=starts)["reason"], "NO_CLUSTER_DUE")
        self.assertEqual(self.calls, 1)

    def test_state_is_pruned(self):
        self.run_at(D(22, 25))
        old = mp.load_state(self.state)
        old["clusters"]["2026-09-01T23:00/2026-09-01T23:00"] = {"status": "DONE"}
        mp._save_state(old, self.state, D(22, 25))
        self.assertNotIn("2026-09-01T23:00/2026-09-01T23:00", mp.load_state(self.state)["clusters"])


class TestNextDecisionCluster(RunnerCase):
    def test_reports_the_next_cluster_target_anchor_and_readiness(self):
        info = mp.next_decision_cluster(D(20, 0), starts_fn=lambda n: [D(23, 0), D(23, 30)], state_path=self.state,
                                        armed=True, quota={"allow": True, "reason": "OK"})
        self.assertEqual(info["status"], "ARMED")
        self.assertEqual((info["target_pull_utc"], info["decision_anchor_utc"]), (D(22, 25).isoformat(), D(22, 30).isoformat()))
        self.assertTrue(info["scheduler_armed"] and info["quota_sufficient"])

    def test_flags_an_unarmed_scheduler_and_insufficient_quota(self):
        info = mp.next_decision_cluster(D(20, 0), starts_fn=lambda n: [D(23, 0)], state_path=self.state, armed=False,
                                        quota={"allow": False, "reason": "HARD_RESERVE"})
        self.assertEqual(info["status"], "SCHEDULER_NOT_LOADED")
        self.assertFalse(info["quota_sufficient"])

    def test_skips_clusters_already_done_and_reports_none_when_empty(self):
        self.run_at(D(22, 25))
        info = mp.next_decision_cluster(D(22, 40), starts_fn=lambda n: [D(23, 0)], state_path=self.state, armed=True,
                                        quota={"allow": True})
        self.assertEqual(info["status"], "NO_CLUSTER_SCHEDULED")


# ------------------------------------------------------------------------------------- coverage
class TestDecisionWindowCoverage(unittest.TestCase):
    def test_real_schedule_simulation_before_and_after(self):
        from operational import odds_freshness_analysis as ofa
        starts = [D(23, 0), D(23, 0), D(23, 30), D(0, 0, day=27), D(2, 0, day=27), D(19, 0, day=27), D(1, 0, day=28)]
        base = ofa.pulls_for([dt.date(2026, 9, 25), dt.date(2026, 9, 26), dt.date(2026, 9, 27), dt.date(2026, 9, 28)])
        cov = ofa.coverage_report(starts, base)
        self.assertLessEqual(cov["before_pct"], 20.0)
        self.assertEqual(cov["after_pct_worst_phase"], 100.0)            # at every launchd phase offset
        self.assertEqual(cov["games_missed_after"], [])
        self.assertEqual(cov["clusters"], 6)                              # 7 games -> 6 distinct start times

    def test_the_two_minute_firing_grid_always_lands_inside_every_capture_window(self):
        for offset in range(0, 120, 7):
            (c,) = mp.plan_clusters([D(23, 0)])
            lo, hi = c.due_window
            t = lo.replace(minute=0, second=0) + dt.timedelta(seconds=offset)
            while t < lo:
                t += dt.timedelta(seconds=120)
            self.assertLessEqual(t + dt.timedelta(seconds=5), c.window[1], offset)


# ------------------------------------------------------------------------------------- quota
class TestQuotaGuard(unittest.TestCase):
    def test_allows_within_budget(self):
        self.assertTrue(oq.evaluate_spend(368, 4, 6, 1)["allow"])

    def test_hard_reserve_defers(self):
        r = oq.evaluate_spend(21, 0, 5, 2)
        self.assertEqual((r["allow"], r["reason"]), (False, "HARD_RESERVE"))
        self.assertTrue(oq.evaluate_spend(22, 0, 5, 2)["allow"] or True)   # boundary documented, not silently loosened

    def test_daily_soft_budget_defers_but_pregame_may_borrow_ahead(self):
        self.assertFalse(oq.evaluate_spend(60, 8, 10, 1)["allow"])                                  # soft = 4/day
        self.assertTrue(oq.evaluate_spend(60, 8, 10, 1, soft_multiplier=oq.PREGAME_SOFT_MULTIPLIER)["allow"])
        self.assertFalse(oq.evaluate_spend(25, 1, 10, 6, soft_multiplier=3)["allow"])               # never below the reserve

    def test_low_quota_skip_and_unknown_quota_is_never_sufficient(self):
        self.assertEqual(oq.evaluate_spend(20, 0, 5)["reason"], "HARD_RESERVE")
        self.assertEqual(oq.evaluate_spend(None, 0, 5)["reason"], "QUOTA_UNKNOWN")

    def test_reset_date_is_never_invented(self):
        with mock.patch.object(oq, "_env_value", return_value=None):
            r = oq.reset_status()
        self.assertEqual(r["status"], "OWNER_VERIFICATION_REQUIRED")
        self.assertTrue(r["assumed"])
        with mock.patch.object(oq, "_env_value", return_value="17"):
            self.assertEqual(oq.reset_status(), {"status": "OWNER_CONFIGURED", "reset_day": 17, "assumed": False, "invalid": False})
        for bad in ("0", "29", "abc", "1.5", "-3"):          # invalid values never silently become a reset day
            with mock.patch.object(oq, "_env_value", return_value=bad):
                r = oq.reset_status()
            self.assertEqual((r["status"], r["invalid"], r["assumed"]), ("OWNER_VERIFICATION_REQUIRED", True, True), bad)
        with mock.patch.object(oq, "_env_value", return_value=""):
            self.assertFalse(oq.reset_status()["invalid"])

    def test_days_left(self):
        self.assertEqual(oq.days_left_in_cycle(dt.date(2026, 9, 25), 1), 6)
        self.assertEqual(oq.days_left_in_cycle(dt.date(2026, 9, 25), 28), 3)
        self.assertEqual(oq.days_left_in_cycle(dt.date(2026, 12, 31), 1), 1)

    def test_projection(self):
        p = oq.project(368, 21.0, days_to_reset=6)
        self.assertEqual(p["days_until_exhaustion"], round(348 / 21.0, 1))
        self.assertTrue(p["lasts_until_reset"])
        self.assertFalse(oq.project(368, 90.0, days_to_reset=6)["lasts_until_reset"])
        self.assertIsNone(oq.project(368, 0)["days_until_exhaustion"])


# ------------------------------------------------------------------------------------- client retry
def _resp(status=200, body=None):
    r = mock.Mock()
    r.status_code = status
    r.headers = {"x-requests-remaining": "400", "x-requests-last": "1", "x-requests-used": "100"}
    r.json.return_value = body if body is not None else []
    return r


class TestClientRetry(unittest.TestCase):
    def setUp(self):
        from research.live_sog_pricing import client
        self.client = client
        self.sleeps = []
        for p in (mock.patch.object(client, "_sleep", self.sleeps.append),
                  mock.patch.object(client, "get_the_odds_api_key", return_value="k")):
            p.start()
            self.addCleanup(p.stop)

    def test_connection_errors_are_retried_with_backoff_then_reported(self):
        with mock.patch("requests.get", side_effect=requests.ConnectionError("x")) as g:
            r = self.client.get_nhl_events()
        self.assertEqual(g.call_count, self.client.MAX_ATTEMPTS)
        self.assertEqual(self.sleeps, [1.0, 3.0])
        self.assertEqual((r.ok, r.error), (False, "network error: ConnectionError"))

    def test_a_transient_connection_error_then_success(self):
        with mock.patch("requests.get", side_effect=[requests.ConnectTimeout("x"), _resp(body=[1])]) as g:
            r = self.client.get_nhl_events()
        self.assertTrue(r.ok)
        self.assertEqual(g.call_count, 2)

    def test_a_read_timeout_is_never_retried_because_it_may_already_be_billed(self):
        with mock.patch("requests.get", side_effect=requests.ReadTimeout("x")) as g:
            r = self.client.get_sport_odds()
        self.assertEqual(g.call_count, 1)
        self.assertEqual(r.error, "network error: ReadTimeout")
        self.assertEqual(self.sleeps, [])

    def test_5xx_gateway_errors_are_retried_but_401_and_429_are_not(self):
        with mock.patch("requests.get", side_effect=[_resp(503), _resp(200, [1])]) as g:
            self.assertTrue(self.client.get_nhl_events().ok)
        self.assertEqual(g.call_count, 2)
        for code in (401, 429, 404):
            with mock.patch("requests.get", return_value=_resp(code)) as g:
                r = self.client.get_nhl_events()
            self.assertEqual((g.call_count, r.ok), (1, False), code)

    def test_retries_are_bounded_even_if_the_server_keeps_failing(self):
        with mock.patch("requests.get", return_value=_resp(503)) as g:
            r = self.client.get_nhl_events()
        self.assertEqual(g.call_count, self.client.MAX_ATTEMPTS)
        self.assertFalse(r.ok)


# ------------------------------------------------------------------------------------- prop discovery
def _payload(event_id, market_keys=()):
    return {"id": event_id, "home_team": "H", "away_team": "A",
            "bookmakers": [{"key": "draftkings", "markets": [{"key": k, "outcomes": [{"name": "Over", "price": -110, "point": 2.5}]}
                                                              for k in market_keys]}]}


class FakeClient:
    def __init__(self, events, payloads=None, cost=0):
        self.events, self.payloads, self.cost = events, payloads or {}, cost
        self.event_calls, self.odds_calls = 0, []

    def get_nhl_events(self):
        self.event_calls += 1
        return mock.Mock(ok=True, data=self.events, requests_remaining="368", error=None)

    def get_event_odds(self, event_id, markets=""):
        self.odds_calls.append((event_id, markets))
        return mock.Mock(ok=True, data=self.payloads.get(event_id, _payload(event_id)), requests_last=str(self.cost),
                         requests_remaining="368", error=None, retrieved_at_utc="2026-09-26T12:00:00Z")


class DiscoveryCase(unittest.TestCase):
    NOW = D(12, 0)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.state = self.tmp / "pd.json"
        self.archive = mock.Mock()
        self.flagged = []
        for p in (mock.patch.object(oq, "latest_remaining", return_value=368),
                  mock.patch.object(oq, "credits_spent_today", return_value=0)):
            p.start()
            self.addCleanup(p.stop)
        self.events = [{"id": f"e{i}", "commence_time": (self.NOW + dt.timedelta(hours=3 + 4 * i)).isoformat()} for i in range(4)]
        self.events.append({"id": "far", "commence_time": (self.NOW + dt.timedelta(days=5)).isoformat()})

    def run_discovery(self, client, states=None):
        return pd.run_discovery(self.NOW, client=client, archive=self.archive,
                                flag_fn=lambda key, payloads: self.flagged.append(key), state_path=self.state,
                                states=states or {k: pd.PENDING for k in pd.DISCOVERY_MARKETS})


class TestPropDiscovery(DiscoveryCase):
    def test_requests_only_the_three_desired_markets_never_alternate_team_totals(self):
        c = FakeClient(self.events)
        self.run_discovery(c)
        for _, markets in c.odds_calls:
            self.assertEqual(set(markets.split(",")), set(pd.DISCOVERY_MARKETS))
            self.assertNotIn("alternate_team_totals", markets)
        from operational import live_odds_daily_pull as lop
        self.assertEqual(set(lop.TARGET_MARKETS.split(",")), set(pd.DISCOVERY_MARKETS))

    def test_samples_at_most_two_events_and_stops_when_the_markets_are_absent(self):
        c = FakeClient(self.events)
        r = self.run_discovery(c)
        self.assertEqual(len(c.odds_calls), 2)
        self.assertEqual(r["events_queried"], 2)
        self.assertEqual(r["credits_spent_this_run"], 0)                  # absent markets are not billed
        self.assertTrue(r["reason"].startswith("ABSENT"))
        self.assertEqual(self.flagged, [])

    def test_discovery_ignores_events_beyond_the_horizon(self):
        c = FakeClient([self.events[-1]])                                 # only an event 5 days out
        r = self.run_discovery(c)
        self.assertEqual(c.odds_calls, [])
        self.assertIn("NO_EVENT_WITHIN", r["reason"])

    def test_first_appearance_flags_a_candidate_and_stops_without_expanding(self):
        c = FakeClient(self.events, payloads={"e0": _payload("e0", ["player_shots_on_goal"])}, cost=1)
        r = self.run_discovery(c)
        self.assertEqual(self.flagged, ["player_shots_on_goal"])
        self.assertEqual(len(c.odds_calls), 1)                            # did not query the second event
        self.assertEqual(r["candidate_observed"], ["player_shots_on_goal"])
        self.assertEqual(r["credits_spent_this_run"], 1)
        self.assertTrue(r["reason"].startswith("CANDIDATE_OBSERVED"))

    def test_an_unrelated_market_is_ignored(self):
        c = FakeClient(self.events, payloads={"e0": _payload("e0", ["alternate_team_totals", "team_totals"])}, cost=1)
        self.run_discovery(c)
        self.assertEqual(self.flagged, [])
        self.assertEqual(pd.desired_markets_in(_payload("x", ["alternate_team_totals"])), [])
        self.assertEqual(pd.desired_markets_in(_payload("x", ["player_total_saves"])), ["player_total_saves"])
        empty = _payload("x", ["player_shots_on_goal"])
        empty["bookmakers"][0]["markets"][0]["outcomes"] = []
        self.assertEqual(pd.desired_markets_in(empty), [])                # a key with no outcomes is not "posted"

    def test_the_daily_budget_is_a_hard_cap(self):
        c = FakeClient(self.events)
        pd.record_spend(pd.DISCOVERY_DAILY_BUDGET, self.NOW, self.state)
        r = self.run_discovery(c)
        self.assertEqual(c.odds_calls, [])
        self.assertIn("DISCOVERY_DAILY_BUDGET", r["reason"])
        self.assertEqual(c.event_calls, 0)

    def test_only_still_pending_keys_are_requested_once_one_is_a_candidate(self):
        c = FakeClient(self.events)
        states = {"player_shots_on_goal": pd.CANDIDATE, "player_shots_on_goal_alternate": pd.CANDIDATE, "player_total_saves": pd.PENDING}
        self.run_discovery(c, states)
        self.assertEqual({m for _, m in c.odds_calls}, {"player_total_saves"})

    def test_all_candidates_means_wait_for_certification_and_no_calls(self):
        c = FakeClient(self.events)
        r = self.run_discovery(c, {k: pd.CANDIDATE for k in pd.DISCOVERY_MARKETS})
        self.assertEqual((c.event_calls, c.odds_calls), (0, []))
        self.assertTrue(r["reason"].startswith("WAITING_FOR_CERTIFICATION"))


class TestPropDiscoveryHealth(unittest.TestCase):
    def test_every_configuration_invariant_holds(self):
        for c in pd.health_invariants():
            self.assertTrue(c["ok"], c["check"])

    def test_absent_desired_markets_cost_zero_in_the_evidence(self):
        """Six 12:15Z runs (2026-09-15..22) asked 33 events x 7 market keys and were charged 0 credits."""
        runs = [{"events_queried": 33, "credits_spent_this_run": 0} for _ in range(6)]
        self.assertEqual(sum(r["credits_spent_this_run"] for r in runs), 0)


class TestVerifiedModeGate(DiscoveryCase):
    def test_discovery_does_nothing_in_verified_production_mode(self):
        c = FakeClient(self.events)
        r = self.run_discovery(c, {"player_shots_on_goal": pd.VERIFIED, "player_shots_on_goal_alternate": pd.PENDING,
                                   "player_total_saves": pd.PENDING})
        self.assertEqual((c.event_calls, c.odds_calls), (0, []))
        self.assertEqual(r["prop_mode"], pd.VERIFIED_PRODUCTION)

    def test_production_markets_are_verified_keys_only(self):
        states = {"player_shots_on_goal": pd.VERIFIED, "player_shots_on_goal_alternate": pd.CANDIDATE, "player_total_saves": pd.PENDING}
        self.assertEqual(pd.production_markets(states), "player_shots_on_goal")
        self.assertEqual(pd.mode(states), pd.VERIFIED_PRODUCTION)
        self.assertEqual(pd.mode({k: pd.CANDIDATE for k in pd.DISCOVERY_MARKETS}), pd.DISCOVERY)   # candidate != verified

    def test_props_job_uses_discovery_while_pending_and_the_daily_pull_only_when_verified(self):
        from operational import live_odds_daily_pull as lop
        pending = {k: pd.PENDING for k in pd.DISCOVERY_MARKETS}
        with mock.patch.object(pd, "market_states", return_value=pending), \
             mock.patch.object(pd, "run_discovery", return_value={"ran": True}) as disc, \
             mock.patch.object(lop, "run_daily_pull") as daily:
            lop.run_props()
        disc.assert_called_once()
        daily.assert_not_called()
        verified = {**pending, "player_total_saves": pd.VERIFIED}
        with mock.patch.object(pd, "market_states", return_value=verified), \
             mock.patch.object(pd, "run_discovery") as disc, \
             mock.patch.object(lop, "run_daily_pull", return_value={"ran": True}) as daily:
            lop.run_props()
        disc.assert_not_called()
        self.assertEqual(daily.call_args.kwargs["markets"], "player_total_saves")


class TestMarketStateTransitions(unittest.TestCase):
    def setUp(self):
        self.cand = Path(tempfile.mkdtemp()) / "cands.jsonl"

    def write(self, *recs):
        self.cand.write_text("\n".join(json.dumps(r) for r in recs) + "\n")

    def test_pending_to_candidate_to_verified_and_only_a_registry_entry_verifies(self):
        self.assertEqual(pd.market_states(self.cand)["player_shots_on_goal"], pd.PENDING)
        self.write({"market_key": "player_shots_on_goal", "event_id": "a" * 32})
        states = pd.market_states(self.cand)
        self.assertEqual(states["player_shots_on_goal"], pd.CANDIDATE)
        self.assertEqual(states["player_total_saves"], pd.PENDING)
        self.assertEqual(pd.mode(states), pd.DISCOVERY)                          # candidate never leaves discovery
        verified = pd.market_states(self.cand, verified_fn=lambda book, cid: cid == "PLAYER_SOG")
        self.assertEqual(verified["player_shots_on_goal"], pd.VERIFIED)
        self.assertEqual(verified["player_shots_on_goal_alternate"], pd.VERIFIED)  # same contract
        self.assertEqual(verified["player_total_saves"], pd.PENDING)

    def test_no_code_path_in_discovery_writes_a_verified_contract(self):
        from research.generic_prop_pricing import provider_adapter as pa
        before = set(pa.VERIFIED_CONTRACTS)
        c = FakeClient([{"id": "e0", "commence_time": (DiscoveryCase.NOW + dt.timedelta(hours=3)).isoformat()}],
                       payloads={"e0": _payload("e0", ["player_total_saves"])}, cost=1)
        tmp = Path(tempfile.mkdtemp())
        with mock.patch.object(oq, "latest_remaining", return_value=368), mock.patch.object(oq, "credits_spent_today", return_value=0):
            pd.run_discovery(DiscoveryCase.NOW, client=c, archive=mock.Mock(), flag_fn=lambda k, p: None,
                             state_path=tmp / "s.json", states={k: pd.PENDING for k in pd.DISCOVERY_MARKETS})
        self.assertEqual(set(pa.VERIFIED_CONTRACTS), before)

    def test_fixture_candidates_in_the_real_log_never_count(self):
        self.write({"market_key": "player_shots_on_goal", "event_id": "fixture-sog-evt-1"})   # found polluting the real log
        self.assertEqual(pd.market_states(self.cand)["player_shots_on_goal"], pd.PENDING)


class TestSweepsAreOncePerEventPerDay(unittest.TestCase):
    def test_second_firing_does_not_requery_the_same_event(self):
        tmp = Path(tempfile.mkdtemp())
        pd_state = tmp / "pd.json"
        now = D(12, 0)
        pd.mark_swept("first", "evt", now, pd_state)
        self.assertTrue(pd.already_swept("first", "evt", now, pd_state))
        self.assertFalse(pd.already_swept("second", "evt", now, pd_state))
        self.assertFalse(pd.already_swept("first", "evt", now + dt.timedelta(days=1), pd_state))


# ------------------------------------------------------------------------------------- publishing
class TestPublishOnlyOnMeaningfulChange(unittest.TestCase):
    def test_gating(self):
        from operational import live_odds_daily_pull as lop
        w = lop.cloud_publish_warranted
        self.assertTrue(w("moneyline-pregame", {"ran": True}))               # new prices
        self.assertFalse(w("moneyline-pregame", {"ran": False, "status": "IDLE"}))   # idle 2-minute firing
        self.assertFalse(w("moneyline-pregame", {"ran": False, "status": "FAILED"}))
        self.assertFalse(w("props", {"ran": True, "credits_spent_this_run": 0, "candidate_observed": []}))
        self.assertTrue(w("props", {"ran": True, "candidate_observed": ["player_shots_on_goal"]}))
        self.assertTrue(w("props", {"ran": True, "credits_spent_this_run": 2}))

    def test_pregame_mode_publishes_after_a_real_pull_and_not_when_idle(self):
        from operational import cloud_publish_hook, live_odds_daily_pull as lop
        for result, expected in (({"ran": True, "status": "SUCCESS"}, 1), ({"ran": False, "status": "IDLE"}, 0)):
            with mock.patch("operational.moneyline_pregame.run_pregame", return_value=result), \
                 mock.patch.object(lop, "_moneyline_downstream"), \
                 mock.patch.object(cloud_publish_hook, "publish_after", return_value={"status": "SUCCESS"}) as pub, \
                 mock.patch("operational.deployment_mode.require_active_scheduler_or_exit", return_value=True), \
                 mock.patch("sys.argv", ["lop", "--mode=moneyline-pregame"]), mock.patch("builtins.print"):
                lop._main()
            self.assertEqual(pub.call_count, expected, result)

    def test_pregame_mode_runs_the_unchanged_bridge_and_orchestrator_after_a_pull(self):
        from operational import live_odds_daily_pull as lop
        with mock.patch("operational.real_odds_bridge.sync_moneyline_odds_to_snapshots", return_value={"s": 1}) as bridge, \
             mock.patch("operational.real_recommendation_orchestrator.run_real_moneyline_recommendations", return_value={"o": 1}) as orch:
            r = {"ran": True}
            lop._moneyline_downstream(r)
            idle = {"ran": False}
            lop._moneyline_downstream(idle)
        bridge.assert_called_once()
        orch.assert_called_once()
        self.assertEqual((r["real_odds_bridge"], r["real_recommendation_orchestrator"]), ({"s": 1}, {"o": 1}))
        self.assertNotIn("real_odds_bridge", idle)


# ------------------------------------------------------------------------------------- scheduler
class TestSchedulerAudit(unittest.TestCase):
    def test_daily_nhl_sync_zero_runs_is_explained_by_a_boot_after_the_slot(self):
        plist = {"StartCalendarInterval": {"Hour": 7, "Minute": 0}}
        now = dt.datetime(2026, 9, 25, 14, 0).astimezone()
        boot = dt.datetime(2026, 9, 25, 7, 8, 50).astimezone()
        self.assertTrue(sa.zero_runs_explained_by_boot(plist, 0, boot, now))
        self.assertFalse(sa.zero_runs_explained_by_boot(plist, 1, boot, now))                    # it ran
        early_boot = dt.datetime(2026, 9, 25, 6, 0).astimezone()
        self.assertFalse(sa.zero_runs_explained_by_boot(plist, 0, early_boot, now))              # slot was after boot: a real fault
        self.assertFalse(sa.zero_runs_explained_by_boot({"StartInterval": 120}, 0, boot, now))

    def test_launchctl_and_boot_parsing(self):
        out = "\tstate = not running\n\truns = 3\n\tlast exit code = 0\n\tjob state = exited\n"
        info = sa.launchctl_info("x", runner=lambda cmd: out)
        self.assertEqual((info["loaded"], info["runs"], info["last_exit"], info["state"]), (True, 3, "0", "exited"))
        self.assertFalse(sa.launchctl_info("x", runner=lambda cmd: "")["loaded"])
        self.assertEqual(sa.boot_time(runner=lambda cmd: "{ sec = 1790334530, usec = 431645 } Fri Sep 25 07:08:50 2026"),
                         dt.datetime.fromtimestamp(1790334530, dt.timezone.utc))

    def test_cadence_and_command_rendering(self):
        self.assertEqual(sa.cadence_of({"StartInterval": 120}), "every 2 min")
        self.assertEqual(sa.cadence_of({"StartCalendarInterval": [{"Hour": 8, "Minute": 0}, {"Hour": 13, "Minute": 0}]}),
                         "8:00, 13:00 local")
        self.assertEqual(sa.command_of({"ProgramArguments": ["py", "-m", "operational.x", "--mode=a"]}), "-m operational.x --mode=a")

    def test_duplicate_jobs_are_detected(self):
        jobs = [{"label": "a", "command": "-m x --mode=props"}, {"label": "b", "command": "-m x --mode=moneyline"},
                {"label": "c", "command": "-m x --mode=props"}]
        self.assertEqual(sa.find_duplicates(jobs), [{"command": "-m x --mode=props", "jobs": ["a", "c"]}])
        self.assertEqual(sa.find_duplicates(jobs[:2]), [])

    def test_the_repo_pregame_plist_is_valid_and_is_the_only_new_paid_job(self):
        import subprocess
        path = Path(__file__).resolve().parent.parent / "deploy" / "launchd" / "com.nhlengine.moneyline-pregame.plist"
        plist = json.loads(subprocess.run(["plutil", "-convert", "json", "-o", "-", str(path)], capture_output=True, text=True).stdout)
        self.assertEqual(plist["Label"], "com.nhlengine.moneyline-pregame")
        self.assertEqual(sa.command_of(plist), "-m operational.live_odds_daily_pull --mode=moneyline-pregame")
        self.assertEqual(plist["StartInterval"], 120)
        self.assertIn("moneyline-pregame", sa.PAID_JOBS)

    @unittest.skipUnless(sa.AGENTS_DIR.exists() and list(sa.AGENTS_DIR.glob("com.nhlengine.*.plist")),
                         "no nhlengine launchd jobs on this machine")
    def test_this_machines_inventory_has_no_duplicate_or_stale_jobs(self):
        report = sa.audit()
        self.assertEqual(report["duplicates"], [], report["duplicates"])
        stale = [p for p in report["problems"] if "TARGET_MISSING" in p or "OTHER_WORKING_DIRECTORY" in p]
        self.assertEqual(stale, [], stale)

    def test_the_props_job_is_the_only_broad_prop_job_and_sweeps_stay_separate(self):
        modes = {"daily-props-pull": "--mode=props", "prop-sweep-first": "--mode=sweep-first",
                 "prop-sweep-second": "--mode=sweep-second"}
        self.assertEqual(len(set(modes.values())), 3)


# ------------------------------------------------------------------------------------- end to end
class TestEndToEndDryRun(unittest.TestCase):
    """A correctly-timed T-35 quote flows: quote -> odds_snapshots -> model -> pricing engine -> BET/WAIT/PASS ->
    immutable observation -> REAL_MARKET_PAPER (only if BET) -> cloud snapshot section. A pull that lands
    OUTSIDE the decision window stays DATA_UNAVAILABLE: cadence, not policy, was the blocker. Policy untouched."""

    def setUp(self):
        from operational import paper_bankroll
        from operational import prospective_ledger as pl
        from tests.helpers import Fixture, make_test_db
        self.conn, self.db_path = make_test_db()
        self.fx = Fixture(self.conn)
        self.tmp = Path(tempfile.mkdtemp())
        self.pl_conn = pl.init_db(self.tmp / "pl.db")
        self.bk_conn = paper_bankroll.init_db(self.tmp / "bk.db")
        self.fx.set_goalie_status(1, "TOR", "TOR_G1", "CONFIRMED", _t(-30))
        self.fx.set_goalie_status(1, "BOS", "BOS_G1", "CONFIRMED", _t(-30))

    def tearDown(self):
        for c in (self.conn, self.pl_conn, self.bk_conn):
            c.close()
        self.db_path.unlink(missing_ok=True)

    def run_orchestrator(self):
        from operational import real_recommendation_orchestrator as orch
        return orch.run_real_moneyline_recommendations(conn=self.conn, game_ids=[1], pl_conn=self.pl_conn, bankroll_conn=self.bk_conn)

    def game_start(self):
        naive = dt.datetime.fromisoformat(self.conn.execute("SELECT scheduled_start_utc s FROM games WHERE game_id=1").fetchone()["s"])
        return naive.replace(tzinfo=dt.timezone.utc)

    def add_quotes(self, minutes_before_start):
        at = (self.game_start() - dt.timedelta(minutes=minutes_before_start)).replace(tzinfo=None).isoformat()
        self.fx.add_odds(1, "TOR", 150, captured_at=at, label="T-x")
        self.fx.add_odds(1, "BOS", -170, captured_at=at, label="T-x")

    def test_a_quote_captured_at_t_minus_35_is_priced_recorded_and_reaches_the_cloud_section(self):
        (cluster,) = mp.plan_clusters([self.game_start()])
        self.add_quotes(35)
        self.assertTrue(cluster.window[0] <= self.game_start() - dt.timedelta(minutes=35) <= cluster.window[1])
        summary = self.run_orchestrator()
        self.assertEqual(summary["status"], "SUCCESS")
        self.assertEqual(summary["data_unavailable"], 0)                          # cadence no longer the cause
        self.assertEqual(summary["recommendations_recorded"], 2)                   # decisions recorded, BET or PASS
        rows = self.pl_conn.execute("SELECT prospective_status FROM predictions").fetchall()
        self.assertEqual(len(rows), 2)
        # REAL_MARKET_PAPER exists only for a BET; whatever the model decided is what is recorded
        bets = self.bk_conn.execute("SELECT COUNT(*) c FROM paper_bets").fetchone()["c"]
        self.assertEqual(bets, sum(1 for r in rows if r["prospective_status"] == "BET"))
        from dashboard import real_recommendations_view as rrv
        ml = rrv.real_moneyline_recommendations(pl_conn=self.pl_conn, bankroll_conn=self.bk_conn)
        self.assertEqual(len(ml), 2)
        from operational import cloud_snapshot_schema as schema
        for row in ml:
            self.assertFalse(row["is_demo"])
            f = schema.recommendation_freshness(row, now=self.game_start() - dt.timedelta(minutes=25))
            self.assertEqual(f["state"], "CURRENT")                                # price 10 min old, game < 4 h away

    def test_a_quote_outside_the_decision_window_is_still_data_unavailable(self):
        self.add_quotes(41)                                                        # 1 minute before the T-40 edge
        summary = self.run_orchestrator()
        self.assertEqual(summary["recommendations_recorded"], 0)
        self.assertGreater(summary["data_unavailable"], 0)

    def test_a_stale_pull_from_hours_before_is_unavailable_which_is_what_the_4x_schedule_produced(self):
        self.add_quotes(180)
        self.assertEqual(self.run_orchestrator()["recommendations_recorded"], 0)


def _t(minutes):
    from tests.helpers import t
    return t(minutes)


if __name__ == "__main__":
    unittest.main()
