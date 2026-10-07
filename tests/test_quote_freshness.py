"""operational/quote_freshness.py: the one policy for how old a bookmaker's price is."""
from __future__ import annotations

import datetime as dt
import unittest

from operational import quote_freshness as qf

NOW = dt.datetime(2026, 10, 7, 20, 0, tzinfo=dt.timezone.utc)


class TestAssess(unittest.TestCase):
    def test_fresh_quote_keeps_both_timestamps_apart(self):
        v = qf.assess("2026-10-07T19:50:00Z", "2026-10-07T19:59:00Z", NOW, 150)
        self.assertTrue(v["fresh"])
        self.assertEqual((v["status"], v["quote_age_min"], v["retrieval_age_min"]), ("FRESH", 10.0, 1.0))
        self.assertEqual((v["quote_updated_utc"], v["retrieved_at_utc"]), ("2026-10-07T19:50:00Z", "2026-10-07T19:59:00Z"))

    def test_six_day_old_market_fetched_a_minute_ago_is_stale(self):
        v = qf.assess("2026-10-01T19:50:00Z", "2026-10-07T19:59:00Z", NOW, 150)
        self.assertEqual(v["status"], qf.STALE_QUOTE)
        self.assertFalse(v["fresh"])
        self.assertGreater(v["quote_age_min"], 8000)
        self.assertEqual(v["retrieval_age_min"], 1.0)

    def test_missing_malformed_and_future_never_qualify(self):
        cases = ((None, qf.MISSING_QUOTE_TIMESTAMP), ("   ", qf.MISSING_QUOTE_TIMESTAMP), (12345, qf.MALFORMED_QUOTE_TIMESTAMP),
                 ("yesterday", qf.MALFORMED_QUOTE_TIMESTAMP), ("2026-10-07T20:30:00Z", qf.FUTURE_QUOTE_TIMESTAMP))
        for value, status in cases:
            v = qf.assess(value, "2026-10-07T19:59:00Z", NOW, 150)
            self.assertEqual((v["fresh"], v["status"]), (False, status), value)

    def test_future_tolerance_is_explicit_and_small(self):
        inside = qf.assess("2026-10-07T19:59:30Z", "2026-10-07T19:59:00Z", NOW, 150)       # 30 s after retrieval
        self.assertTrue(inside["fresh"])
        outside = qf.assess("2026-10-07T20:00:30Z", "2026-10-07T19:59:00Z", NOW, 150)      # 90 s after retrieval
        self.assertEqual(outside["status"], qf.FUTURE_QUOTE_TIMESTAMP)

    def test_retrieval_checks(self):
        self.assertEqual(qf.assess("2026-10-07T19:50:00Z", None, NOW, 150)["status"], qf.MISSING_RETRIEVAL_TIMESTAMP)
        self.assertEqual(qf.assess("2026-10-07T19:50:00Z", "nope", NOW, 150)["status"], qf.MALFORMED_RETRIEVAL_TIMESTAMP)
        self.assertEqual(qf.assess("2026-10-07T19:50:00Z", "2026-10-07T21:00:00Z", NOW, 150)["status"],
                         qf.FUTURE_QUOTE_TIMESTAMP)
        self.assertTrue(qf.assess("2026-10-07T19:50:00Z", None, NOW, 150, require_retrieval=False)["fresh"])

    def test_limit_boundary_and_naive_inputs(self):
        self.assertTrue(qf.assess("2026-10-07T17:30:00Z", "2026-10-07T17:31:00Z", NOW, 150)["fresh"])     # exactly 150 min
        self.assertEqual(qf.assess("2026-10-07T17:29:59Z", "2026-10-07T17:31:00Z", NOW, 150)["status"], qf.STALE_QUOTE)
        self.assertTrue(qf.assess("2026-10-07T19:50:00", "2026-10-07T19:59:00", NOW.replace(tzinfo=None), 150)["fresh"])
        self.assertTrue(qf.assess("2026-10-07T15:50:00-04:00", "2026-10-07T19:59:00Z", NOW, 150)["fresh"])  # offsets honoured


if __name__ == "__main__":
    unittest.main()
