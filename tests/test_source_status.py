"""Data Status: states are derived from evidence timestamps at the time of viewing, so they age; stale data, a stale status snapshot, disabled feeds, exhausted
budgets and not-yet-due sources are different things; the odds evidence is the real quote evidence, not a retired research file."""
from __future__ import annotations

import datetime as dt
import json
import unittest
from unittest import mock

from operational import readiness
from operational import source_status as ss

T0 = dt.datetime(2026, 10, 8, 22, 0, tzinfo=dt.timezone.utc)


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def src(key, basis_minutes_ago, limit, **kw):
    basis = None if basis_minutes_ago is None else T0 - dt.timedelta(minutes=basis_minutes_ago)
    return {"key": key, "label": key, "age_basis_utc": None if basis is None else iso(basis), "data_through": None, "last_success_utc": None if basis is None else iso(basis),
            "last_attempt_utc": None, "max_age_min": limit, "next_refresh_utc": None, "next_refresh_note": None, "fixed_state": None, "limiter": None, "reason": "",
            "detail": {}, **kw}


def doc(*sources, generated_minutes_ago=5):
    return {"schema": 1, "generated_at_utc": iso(T0 - dt.timedelta(minutes=generated_minutes_ago)), "publish_interval_min": 25, "snapshot_stale_after_min": 60, "sources": list(sources)}


def state(d, key, now):
    return next(r for r in ss.evaluate(d, now)["rows"] if r["key"] == key)["state"]


class TestAging(unittest.TestCase):
    def test_a_morning_success_does_not_stay_current(self):
        d = doc(src("nhl_schedule", 15 * 60, ss.DAILY_MIN), src("moneypuck_skater", 11 * 60, ss.MONEYPUCK_MIN))
        self.assertEqual(state(d, "nhl_schedule", T0), ss.CURRENT)
        self.assertEqual(state(d, "nhl_schedule", T0 + dt.timedelta(hours=10)), ss.STALE)          # 25 h after the sync
        self.assertEqual(state(d, "moneypuck_skater", T0 + dt.timedelta(hours=24)), ss.CURRENT)
        self.assertEqual(state(d, "moneypuck_skater", T0 + dt.timedelta(hours=26)), ss.STALE)      # 37 h after the file was accepted

    def test_prices_use_a_tighter_limit_near_puck_drop_evaluated_now(self):
        starts = [iso(T0 + dt.timedelta(hours=2))]
        d = doc(src("odds_moneyline", 80, 90.0, game_starts=starts))
        self.assertEqual(state(d, "odds_moneyline", T0), ss.CURRENT)                                # 80 min, game within 4 h: limit 90
        self.assertEqual(state(d, "odds_moneyline", T0 + dt.timedelta(minutes=20)), ss.STALE)       # 100 min
        far = doc(src("odds_moneyline", 80, 90.0, game_starts=[iso(T0 + dt.timedelta(hours=9))]))
        self.assertEqual(state(far, "odds_moneyline", T0 + dt.timedelta(minutes=20)), ss.CURRENT)   # no game within 4 h: limit 180
        self.assertEqual(state(far, "odds_moneyline", T0 + dt.timedelta(minutes=110)), ss.STALE)

    def test_budget_limited_is_not_plain_stale(self):
        d = doc(src("odds_moneyline", 200, 180.0, limiter="BUDGET", reason="the display refresh is not allowed now", game_starts=[]))
        row = next(r for r in ss.evaluate(d, T0)["rows"] if r["key"] == "odds_moneyline")
        self.assertEqual(row["state"], ss.BUDGET_LIMITED)
        self.assertIn("not allowed", row["reason"])

    def test_disabled_not_due_and_estimate_are_distinct(self):
        d = doc(src("feed", None, 90.0, fixed_state=ss.DISABLED, reason="switched off", limiter="PERMISSION"),
                src("props", 54, None, fixed_state=ss.NOT_DUE, reason="next window opens later"),
                src("est", None, None, fixed_state=ss.ESTIMATE))
        self.assertEqual([r["state"] for r in ss.evaluate(d, T0 + dt.timedelta(days=3))["rows"]], [ss.DISABLED, ss.NOT_DUE, ss.ESTIMATE])

    def test_missing_evidence_is_unavailable_not_current(self):
        self.assertEqual(state(doc(src("x", None, 60.0, reason="no snapshot")), "x", T0), ss.UNAVAILABLE)

    def test_overdue_refresh_is_called_out(self):
        d = doc(src("nhl_results", 2000, ss.DAILY_MIN, next_refresh_utc=iso(T0 - dt.timedelta(hours=1))))
        row = ss.evaluate(d, T0)["rows"][0]
        self.assertEqual(row["state"], ss.STALE)
        self.assertTrue(row["next_overdue"])
        self.assertIn("overdue", row["reason"])


class TestStatusSnapshotStaleness(unittest.TestCase):
    def test_the_status_snapshot_ages_separately_from_the_data(self):
        d = doc(src("nhl_schedule", 10, ss.DAILY_MIN), generated_minutes_ago=5)
        self.assertFalse(ss.evaluate(d, T0)["snapshot_stale"])
        later = ss.evaluate(d, T0 + dt.timedelta(minutes=70))
        self.assertTrue(later["snapshot_stale"])
        self.assertEqual(later["rows"][0]["state"], ss.CURRENT)                                     # the data itself has not aged out; the publication has
        self.assertTrue(ss.evaluate({"sources": []}, T0)["snapshot_stale"])                          # no generation time at all is stale


class TestOddsEvidence(unittest.TestCase):
    def test_readiness_odds_reads_the_real_pull_not_the_retired_research_file(self):
        self.assertTrue(str(readiness.ODDS_CACHE_PATH).endswith("operational/moneyline_snapshot_cache.json"))
        fresh = {"generated_at_utc": iso(T0 - dt.timedelta(minutes=30)), "summary": {}}
        with mock.patch.object(readiness, "ODDS_CACHE_PATH") as p:
            p.exists.return_value = True
            with mock.patch("builtins.open", mock.mock_open(read_data=json.dumps(fresh))):
                out = readiness.odds_status(T0)
        self.assertEqual(out["status"], "CURRENT")
        self.assertLess(out["age_hours"], 1)


class TestBuild(unittest.TestCase):
    def test_build_records_timestamps_not_ages_and_marks_the_disabled_feeds(self):
        import sqlite3
        from pathlib import Path
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        c.executescript((Path(__file__).parent.parent / "schema.sql").read_text())
        health = {"nhl_sync_full": {"last_success_utc": iso(T0 - dt.timedelta(hours=11)), "last_attempt_utc": iso(T0 - dt.timedelta(hours=11)), "last_status": "SUCCESS"},
                  "nhl_pregame_targeted_refresh": {"last_success_utc": iso(T0 - dt.timedelta(minutes=10)), "last_attempt_utc": iso(T0 - dt.timedelta(minutes=10)), "last_status": "SUCCESS"}}
        out = ss.build(T0, nhl=c, health=health, readiness={"nhl_sync": {"window_end": "2026-10-22"}})
        keys = [s["key"] for s in out["sources"]]
        for k in ("nhl_schedule", "nhl_results", "moneypuck_skater", "odds_moneyline", "odds_props", "odds_credits", "starter_confirmations", "reported_lineups", "starter_estimates"):
            self.assertIn(k, keys)
        by = {s["key"]: s for s in out["sources"]}
        self.assertEqual(by["nhl_schedule"]["age_basis_utc"], iso(T0 - dt.timedelta(minutes=10)))      # the newest of the sync components
        self.assertEqual(by["reported_lineups"]["fixed_state"], ss.DISABLED)
        self.assertEqual(by["starter_confirmations"]["fixed_state"], ss.DISABLED)
        self.assertNotIn('"age_min"', json.dumps(out))                                                    # timestamps only; ages are derived at display time


class TestPage(unittest.TestCase):
    def test_hosted_page_renders_one_status_per_source_ages_at_view_time_and_flags_a_stale_snapshot(self):
        from tests.product_fixture import snapshot
        from tests.test_product_pages import run_page, text
        now = dt.datetime.now(dt.timezone.utc)
        fresh = {"schema": 1, "generated_at_utc": iso(now - dt.timedelta(minutes=4)), "publish_interval_min": 25, "snapshot_stale_after_min": 60, "sources": [
            {"key": "nhl_schedule", "label": "NHL schedule", "age_basis_utc": iso(now - dt.timedelta(minutes=20)), "data_through": "schedule through 2026-10-22",
             "last_success_utc": iso(now - dt.timedelta(minutes=20)), "last_attempt_utc": None, "max_age_min": 1440.0, "next_refresh_utc": iso(now + dt.timedelta(minutes=10)),
             "next_refresh_note": None, "fixed_state": None, "limiter": None, "reason": "", "detail": {}},
            {"key": "odds_moneyline", "label": "Moneyline prices (DraftKings)", "age_basis_utc": iso(now - dt.timedelta(minutes=200)), "data_through": None,
             "last_success_utc": iso(now - dt.timedelta(minutes=200)), "last_attempt_utc": None, "max_age_min": 180.0, "next_refresh_utc": None, "next_refresh_note": None,
             "fixed_state": None, "limiter": None, "reason": "", "detail": {}, "game_starts": []},
            {"key": "reported_lineups", "label": "Reported lines", "age_basis_utc": None, "data_through": None, "last_success_utc": None, "last_attempt_utc": None, "max_age_min": 2880.0,
             "next_refresh_utc": None, "next_refresh_note": None, "fixed_state": "DISABLED", "limiter": "PERMISSION", "reason": "switched off", "detail": {}}]}
        snap = snapshot()
        snap["data_status"] = {"sources": fresh}
        unlock = lambda a: a.session_state.__setitem__("_owner_unlocked", True)          # Data Status is an owner page on the public app
        at = run_page("9_Data_Status.py", snap, setup=unlock)
        rows = at.dataframe[0].value.to_dict("records")
        self.assertEqual({r["Source"]: r["Status"] for r in rows}, {"NHL schedule": "Current", "Moneyline prices (DraftKings)": "Stale", "Reported lines": "Disabled"})
        self.assertFalse(at.error)
        stale = dict(fresh, generated_at_utc=iso(now - dt.timedelta(hours=3)))
        snap["data_status"] = {"sources": stale}
        at2 = run_page("9_Data_Status.py", snap, setup=unlock)
        self.assertTrue(any("STATUS SNAPSHOT IS STALE" in e.value for e in at2.error))
        self.assertNotIn("different cache", text(at2))
        self.assertNotIn("SNAPSHOT CURRENT", " ".join(str(m.value) for m in at2.markdown) + text(at2))    # no second, disagreeing banner on this page                                               # no implementation-heavy cache explanation on the normal view


if __name__ == "__main__":
    unittest.main()
