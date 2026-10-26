"""Price freshness is admission intelligence (operational/price_freshness.py): market-specific, from the capture schedule and from how prices move; a stale price is refused for a MARKET-DATA reason and the hockey
review is untouched; a fresh price behaves exactly as before."""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from operational import capture_timeline as ct
from operational import decision_artifacts as da
from operational import eggy_intelligence as ei
from operational import learning_cohort as lc
from operational import learning_parlays as lp
from operational import price_freshness as pf
from tests.test_learning_cohort import Base, leg
from tests.test_learning_parlays import cand

Z = dt.timezone.utc
NOW = dt.datetime(2026, 10, 24, 22, 54, tzinfo=Z)


def L(fam="MONEYLINE", age_min=60.0, now=NOW, **kw):
    obs = (now - dt.timedelta(minutes=age_min)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return SimpleNamespace(market_family=fam, retrieved_at_utc=obs, quote_updated_utc=obs, captured_at_utc=obs, game_start_utc="2026-10-25T02:00:00Z", provider_start_utc=None, **kw)


class TestTheStandardComesFromEvidence(unittest.TestCase):
    def test_the_fresh_limit_is_what_the_capture_schedule_leaves_at_puck_drop(self):
        std = pf.standard()
        self.assertEqual(std["cadence"]["fresh_max_min"], ct.CAPTURE_TARGET_MIN + ct.WAVE_MINUTES)
        for m, x in std["markets"].items():
            self.assertEqual(x["fresh_max_min"], 150.0, m)

    def test_moneyline_has_an_aging_band_from_its_own_price_movement_and_props_have_none(self):
        m = pf.standard()["markets"]
        self.assertEqual(m["MONEYLINE"]["aging_max_min"], 240.0)
        for fam in ("PLAYER_SOG_ALTERNATE", "PLAYER_POINTS", "GOALIE_SAVES"):
            self.assertEqual(m[fam]["aging_max_min"], 150.0, fam)                                       # no history shows a band in which their price stays inside the estimate's precision
        self.assertTrue(m["MONEYLINE"]["drift_of_the_price_between_captures"])
        self.assertIn("90th-percentile", m["MONEYLINE"]["aging_limit_reason"])

    def test_the_standard_is_a_pinned_decision_artifact(self):
        self.assertIn("price_freshness", da.ARTIFACTS)
        self.assertIn("price_freshness", lc.POLICY["decision_architecture"]["artifacts"])
        self.assertEqual(da.manifest_problems(), [])

    def test_an_unreadable_standard_falls_back_to_the_schedule_not_to_a_guess(self):
        with mock.patch.object(pf, "standard", return_value={}):
            self.assertEqual(pf.classify(L(age_min=100), NOW)["status"], pf.FRESH)
            self.assertEqual(pf.classify(L(age_min=200), NOW)["status"], pf.STALE)


class TestClassification(unittest.TestCase):
    def test_a_moneyline_is_fresh_aging_or_stale_by_its_own_limits(self):
        self.assertEqual(pf.classify(L(age_min=62), NOW)["status"], pf.FRESH)
        self.assertEqual(pf.classify(L(age_min=221), NOW)["status"], pf.AGING)
        c = pf.classify(L(age_min=443.6), NOW)
        self.assertEqual(c["status"], pf.STALE)
        self.assertEqual(c["category"], "MARKET_DATA_QUALITY")
        self.assertIn("outside the 240 minutes", c["reason"])

    def test_a_prop_is_fresh_to_the_schedules_limit_and_stale_after_it(self):
        for fam in ("PLAYER_SOG_ALTERNATE", "PLAYER_POINTS", "GOALIE_SAVES"):
            self.assertEqual(pf.classify(L(fam, 136), NOW)["status"], pf.FRESH, fam)                  # 126 to 136 minutes: a game late in its wave, captured exactly as scheduled
            self.assertEqual(pf.classify(L(fam, 200), NOW)["status"], pf.STALE, fam)

    def test_a_missing_or_future_observation_is_not_a_current_price(self):
        self.assertEqual(pf.classify(SimpleNamespace(market_family="MONEYLINE", retrieved_at_utc=None, quote_updated_utc=None, captured_at_utc=None, game_start_utc=None, provider_start_utc=None), NOW)["status"], pf.MISSING)
        self.assertEqual(pf.classify(L(age_min=-30), NOW)["status"], pf.MISSING)
        self.assertEqual(pf.classify(SimpleNamespace(market_family="MONEYLINE", retrieved_at_utc="garbage", quote_updated_utc="garbage", captured_at_utc=None, game_start_utc=None, provider_start_utc=None), NOW)["status"], pf.MISSING)

    def test_a_price_taken_before_a_capture_that_was_due_and_never_made_is_stale_whatever_its_age(self):
        first = dt.datetime(2026, 10, 24, 23, 0, tzinfo=Z)                                             # first wave: capture due 21:30, complete by 22:00
        now = dt.datetime(2026, 10, 24, 22, 10, tzinfo=Z)
        old = L("MONEYLINE", 150, now=now)                                                              # taken 19:40, young enough by age (150 minutes) ...
        c = pf.classify(old, now, wave_first_start=first, first_wave_start=first)
        self.assertEqual(c["status"], pf.STALE)                                                         # ... but the 21:30 pull was due and does not exist
        self.assertTrue(c["newer_capture_due"])
        self.assertIn("was due at 2026-10-24T21:30:00Z", c["reason"])
        ok = L("MONEYLINE", 35, now=now)                                                                # taken 21:35: the due pull exists
        self.assertEqual(pf.classify(ok, now, wave_first_start=first, first_wave_start=first)["status"], pf.FRESH)
        self.assertEqual(pf.classify(old, now - dt.timedelta(minutes=40), wave_first_start=first, first_wave_start=first)["status"], pf.FRESH)   # at 21:30 the pull is due but its window has not closed

    def test_a_later_waves_moneyline_is_overdue_only_when_the_plan_funds_a_pull_for_it(self):
        first, later = dt.datetime(2026, 10, 24, 19, 0, tzinfo=Z), dt.datetime(2026, 10, 25, 2, 0, tzinfo=Z)
        now = dt.datetime(2026, 10, 25, 1, 5, tzinfo=Z)                                                 # its capture was due 00:30, complete by 01:00
        px = L("MONEYLINE", 120, now=now)                                                               # taken 23:05, after the first wave's pregame pull
        self.assertEqual(pf.classify(px, now, wave_first_start=later, first_wave_start=first, wave_pulls_funded=False)["status"], pf.FRESH)
        c = pf.classify(px, now, wave_first_start=later, first_wave_start=first, wave_pulls_funded=True)
        self.assertEqual(c["status"], pf.STALE)
        self.assertEqual(c["newer_capture_due_at_utc"], "2026-10-25T00:30:00Z")

    def test_the_report_says_whether_a_newer_capture_was_due(self):
        first = dt.datetime(2026, 10, 24, 23, 0, tzinfo=Z)
        c = pf.classify(L("MONEYLINE", 443), NOW, wave_first_start=dt.datetime(2026, 10, 25, 2, 0, tzinfo=Z), first_wave_start=dt.datetime(2026, 10, 24, 15, 30, tzinfo=Z) + dt.timedelta(minutes=90))
        self.assertIn("observed_at_utc", c)
        self.assertIn("game_start_utc", c)
        self.assertEqual(c["game_start_utc"], "2026-10-25T02:00:00Z")
        self.assertIn(c["newer_capture_due"], (None, False, True))


class TestTheAdmissionGate(unittest.TestCase):
    def stale(self, c):
        c["card"]["market_data_quality"] = {"status": "STALE", "category": "MARKET_DATA_QUALITY", "reason": "x", "age_min": 443.0, "market": "MONEYLINE"}
        return c

    def test_a_stale_price_refuses_the_leg_with_a_market_data_reason(self):
        ok, why = lp.leg_ok(self.stale(cand(p=0.66)))
        self.assertFalse(ok)
        self.assertEqual(why, "STALE MARKET PRICE / MARKET DATA QUALITY")
        self.assertNotIn("contradiction", why.lower())

    def test_the_market_data_reason_comes_first_even_when_the_hockey_review_also_objects(self):
        c = self.stale(cand(p=0.66, contradiction="HIGH", flags=[{"name": "ROLE_ASSUMPTION", "level": "HIGH", "text": "x"}]))
        self.assertEqual(lp.leg_ok(c)[1], "STALE MARKET PRICE / MARKET DATA QUALITY")

    def test_the_hockey_probability_and_review_are_untouched_by_the_price_verdict(self):
        c = cand(p=0.66)
        before = json.dumps({k: c["card"][k] for k in ("estimate_used", "probabilities", "contradiction", "calibration_confidence", "action")}, sort_keys=True)
        self.stale(c)
        after = json.dumps({k: c["card"][k] for k in ("estimate_used", "probabilities", "contradiction", "calibration_confidence", "action")}, sort_keys=True)
        self.assertEqual(before, after)

    def test_a_fresh_or_unmarked_leg_behaves_exactly_as_before(self):
        plain = cand(p=0.66)
        fresh = cand(p=0.66)
        fresh["card"]["market_data_quality"] = {"status": "FRESH", "category": "MARKET_DATA_QUALITY"}
        self.assertEqual(lp.leg_ok(plain), lp.leg_ok(fresh))
        self.assertEqual(lp.leg_ok(plain), (True, ""))

    def test_an_aging_price_is_admitted_with_a_note_that_counts_toward_no_contradiction(self):
        c = cand(p=0.66)
        c["card"]["contradiction"]["flags"].append({"name": "PRICE_AGING", "level": "LOW", "text": "PRICE AGING"})
        c["card"]["market_data_quality"] = {"status": "AGING", "category": "MARKET_DATA_QUALITY"}
        self.assertEqual(lp.leg_ok(c), (True, ""))
        self.assertEqual(ei.flag_layer("PRICE_AGING"), ei.PRICE_RISK)
        self.assertEqual(ei.counted_flags(c["card"]["contradiction"]["flags"]), [])

    def test_a_missing_price_cannot_claim_current_value(self):
        c = cand(p=0.66)
        c["card"]["market_data_quality"] = {"status": "MISSING", "category": "MARKET_DATA_QUALITY"}
        self.assertEqual(lp.leg_ok(c)[1], "MISSING CURRENT PRICE / MARKET DATA QUALITY")


NOV10 = dt.datetime(2026, 11, 10, 20, 0, tzinfo=Z)


class TestInTheCohortCycle(Base):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(lc, "UNIT", "PARLAY")
        p.start()
        self.addCleanup(p.stop)
        for g in ("11", "12", "13"):
            self.nhl.execute("INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team, away_team, schedule_observed_at_utc, game_state, source) VALUES (?,?,?,?,?,?,?,?,?)",
                             (g, "20262027", "2026-11-10", "2026-11-10T23:00:00", "TOR", "MTL", "2026-11-10T00:00:00", "SCHEDULED", "test"))
        self.nhl.commit()

    def legs(self, ages):
        out = []
        for i, age in enumerate(ages):
            stamp = lambda m: (NOV10 - dt.timedelta(minutes=m)).strftime("%Y-%m-%dT%H:%M:%SZ")
            out.append(leg(pid=str(8470001 + i), game=("11", "12", "13")[i % 3], p=0.70 - 0.002 * i, price=-130.0, team=f"T{i}", game_start_utc="2026-11-10T23:00:00", provider_start_utc="2026-11-10T23:00:00Z",
                           quote_updated_utc=stamp(age), retrieved_at_utc=stamp(age), captured_at_utc=stamp(age), quote_age_min=float(age)))
        return out

    def test_stale_priced_legs_are_reviewed_reported_and_never_recorded_and_fresh_ones_are_unaffected(self):
        mixed = self.legs([30, 40, 50, 60, 70, 80, 420, 430, 440, 450, 460, 470])
        out = self.cycle(mixed, now=NOV10, fresh=False)
        rows = self.conn.execute("SELECT legs_json FROM parlays").fetchall()
        recorded = {json.loads(r["legs_json"])[i]["leg"]["participant_id"] for r in rows for i in range(len(json.loads(r["legs_json"])))}
        stale_ids = {str(8470001 + i) for i in range(6, 12)}
        self.assertTrue(rows)
        self.assertFalse(recorded & stale_ids)                                                          # no stale-priced leg is on any ticket
        sp = out["recording"]["shortfall"]["stale_price_legs"]
        self.assertEqual(sp["count"], 6)
        self.assertEqual(sp["reason"], "STALE MARKET PRICE / MARKET DATA QUALITY")
        self.assertTrue(all(0.5 < x["hockey_estimate"] < 1.0 for x in sp["legs"]))                      # the hockey estimate is still there
        reasons = out["recording"]["shortfall"]["parlay_legs_not_eligible"]
        self.assertEqual(reasons.get("STALE MARKET PRICE / MARKET DATA QUALITY"), 6)
        self.assertNotIn("contradiction", " ".join(reasons).lower())

    def test_the_same_legs_all_fresh_are_recorded_as_they_always_were(self):
        fresh = self.legs([30, 40, 50, 60, 70, 80, 35, 45, 55, 65, 75, 85])
        self.cycle(fresh, now=NOV10, fresh=False)
        n = self.conn.execute("SELECT COUNT(*) FROM parlays").fetchone()[0]
        self.assertGreater(n, 0)
        with mock.patch.object(pf, "classify", side_effect=lambda l, now, **k: {"status": "FRESH", "category": "MARKET_DATA_QUALITY", "market": l.market_family, "age_min": 1.0, "reason": "off"}):
            conn2 = lc.open_ledger(":memory:")
            lc.run_cycle(self.nhl, NOV10, {"legs": fresh, "sources": {}}, conn=conn2)
            self.assertEqual(conn2.execute("SELECT COUNT(*) FROM parlays").fetchone()[0], n)             # with the rule switched off the result is identical: fresh prices are not changed by it
            self.assertEqual([r["idempotency_key"] for r in conn2.execute("SELECT idempotency_key FROM parlays ORDER BY parlay_rank")],
                             [r["idempotency_key"] for r in self.conn.execute("SELECT idempotency_key FROM parlays ORDER BY parlay_rank")])


if __name__ == "__main__":
    unittest.main()
