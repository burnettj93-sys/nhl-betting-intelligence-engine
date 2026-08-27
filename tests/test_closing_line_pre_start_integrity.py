"""
Phase 1.5 Part A: regression tests for the closing_draftkings_snapshot()
pre-start integrity fix.

Previously this function had NO `captured_at_utc < event_start_utc`
predicate at all -- it simply returned the most-recently-captured ACTIVE
DraftKings row for a (game, market, selection), which could be a live or
post-puck-drop quote if one had been ingested. Fixed in
features/point_in_time.py to require the returned row's own
captured_at_utc be STRICTLY earlier than its own event_start_utc.

Required scenario (per spec): a quote captured at 18:59 (pre-start), puck
drop at 19:00, and a quote captured at 19:05 (post-start) -- the required
closing quote is the 18:59 one, never the 19:05 one. A quote captured
exactly AT 19:00 (== event_start_utc) must also be excluded -- puck drop
itself is not "before" puck drop.

Deliberately does NOT touch received_at_utc semantics -- that remains
scoped to the still-under-evaluation historical-provider knowledge-time
question (Phase 1.5 Part H), not this pre-start correctness fix.
"""
import unittest

from features import point_in_time as pit
from tests.helpers import Fixture, make_test_db, t


PUCK_DROP = t(10, hour=19, minute=0)   # matches Fixture.scheduled_start


class TestClosingLinePreStartIntegrity(unittest.TestCase):
    def setUp(self):
        self.conn, self.path = make_test_db()
        self.fx = Fixture(self.conn)
        self.assertEqual(self.fx.scheduled_start, PUCK_DROP)

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_1859_quote_is_the_closing_line(self):
        # required spec scenario: 18:59 quote, 19:00 puck drop, 19:05 quote
        self.fx.add_odds(1, "TOR", -150, captured_at=t(10, hour=8), label="MORNING")
        self.fx.add_odds(1, "TOR", -180, captured_at=t(10, hour=18, minute=59), label="T-1")
        self.fx.add_odds(1, "TOR", -400, captured_at=t(10, hour=19, minute=5), label="LIVE")

        closing = pit.closing_draftkings_snapshot(self.conn, 1, "MONEYLINE", "TOR")
        self.assertIsNotNone(closing)
        self.assertEqual(closing["price_american"], -180)
        self.assertEqual(closing["captured_at_utc"], t(10, hour=18, minute=59))

    def test_1905_post_start_quote_is_never_returned_as_closing(self):
        self.fx.add_odds(1, "TOR", -180, captured_at=t(10, hour=18, minute=59), label="T-1")
        self.fx.add_odds(1, "TOR", -400, captured_at=t(10, hour=19, minute=5), label="LIVE")

        closing = pit.closing_draftkings_snapshot(self.conn, 1, "MONEYLINE", "TOR")
        self.assertIsNotNone(closing)
        self.assertNotEqual(closing["price_american"], -400)
        self.assertLess(closing["captured_at_utc"], closing["event_start_utc"])

    def test_quote_exactly_at_puck_drop_is_excluded(self):
        # captured_at_utc == event_start_utc must be excluded (strict <, not <=)
        self.fx.add_odds(1, "TOR", -180, captured_at=t(10, hour=18, minute=59), label="T-1")
        self.fx.add_odds(1, "TOR", -190, captured_at=PUCK_DROP, label="AT_PUCK_DROP")

        closing = pit.closing_draftkings_snapshot(self.conn, 1, "MONEYLINE", "TOR")
        self.assertIsNotNone(closing)
        self.assertEqual(closing["price_american"], -180)   # not the -190 at-puck-drop row
        self.assertNotEqual(closing["captured_at_utc"], PUCK_DROP)

    def test_only_at_or_after_start_quotes_available_returns_none(self):
        # no genuinely pre-start quote exists at all -- must be None, never
        # a fabricated price and never the at-or-after-start row.
        self.fx.add_odds(1, "TOR", -190, captured_at=PUCK_DROP, label="AT_PUCK_DROP")
        self.fx.add_odds(1, "TOR", -400, captured_at=t(10, hour=19, minute=5), label="LIVE")

        closing = pit.closing_draftkings_snapshot(self.conn, 1, "MONEYLINE", "TOR")
        self.assertIsNone(closing)

    def test_null_event_start_utc_row_is_not_treated_as_a_closing_candidate(self):
        # a "closing" price is meaningless without an event_start_utc to
        # close against -- a NULL-dated row must never be selected, even
        # if it's otherwise the most recently captured ACTIVE row.
        self.fx.add_odds(1, "TOR", -180, captured_at=t(10, hour=18, minute=59), label="T-1")
        self.conn.execute(
            """INSERT INTO odds_snapshots
               (game_id, sportsbook, data_provider, market, selection, event_start_utc,
                price_american, status, captured_at_utc, received_at_utc, snapshot_label)
               VALUES (1,'DraftKings','test','MONEYLINE','TOR',NULL,-999,'ACTIVE',?,?,'NO_DATE')""",
            (t(10, hour=19, minute=30), t(10, hour=19, minute=30)),
        )
        self.conn.commit()

        closing = pit.closing_draftkings_snapshot(self.conn, 1, "MONEYLINE", "TOR")
        self.assertIsNotNone(closing)
        self.assertEqual(closing["price_american"], -180)   # not the NULL-dated -999 row

    def test_no_data_at_all_returns_none_not_an_exception(self):
        closing = pit.closing_draftkings_snapshot(self.conn, 999, "MONEYLINE", "TOR")
        self.assertIsNone(closing)


if __name__ == "__main__":
    unittest.main()
