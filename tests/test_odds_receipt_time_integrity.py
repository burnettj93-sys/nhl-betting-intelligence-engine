"""
v2.1.1a spec item 1: features/point_in_time.py::latest_draftkings_snapshot()
used to gate a historical DraftKings quote on `captured_at_utc <=
prediction_time_utc` alone -- the sportsbook/provider's own timestamp for
when the quote existed. It never also required `received_at_utc <=
prediction_time_utc` -- when THIS SYSTEM actually ingested the row --
despite the schema deliberately storing both as separate concepts. That
is a real look-ahead leakage path: a historical prediction could use a
quote that existed at the book by prediction time but that this engine
had not actually received/learned yet, exactly the same class of bug the
whole point-in-time architecture exists to prevent everywhere else.

These are the four mandated scenarios from the spec, each using the
TOR-vs-BOS Fixture (scheduled 19:00) with everything happening well
before puck drop so staleness/post-start rejection never interferes.
"""
import unittest

from features import point_in_time as pit
from models.combined_model import CombinedMoneylineModel
from pricing import decision as decision_mod
from pricing import engine as pricing_engine
from tests.helpers import Fixture, make_test_db, t


class TestQuoteNotYetReceivedIsIneligible(unittest.TestCase):
    """Test A: captured 18:20, received 18:40, prediction 18:30 -- the
    book says the quote existed by 18:30, but this system had not yet
    ingested it. Must be unavailable, and DATA_UNAVAILABLE end-to-end if
    no other valid quote exists."""

    def setUp(self):
        self.conn, self.path = make_test_db()
        self.fx = Fixture(self.conn)   # game 1: TOR vs BOS, day 10, 19:00
        self.model = CombinedMoneylineModel(teams=["TOR", "BOS"])

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_point_in_time_layer_rejects_the_not_yet_received_quote(self):
        self.fx.add_odds(1, "TOR", -150, captured_at=t(10, hour=18, minute=20),
                          received_at=t(10, hour=18, minute=40))
        row = pit.latest_draftkings_snapshot(
            self.conn, 1, "MONEYLINE", "TOR", t(10, hour=18, minute=30))
        self.assertIsNone(row)

    def test_end_to_end_is_data_unavailable_when_no_other_quote_exists(self):
        self.fx.add_odds(1, "TOR", -150, captured_at=t(10, hour=18, minute=20),
                          received_at=t(10, hour=18, minute=40))
        self.fx.add_odds(1, "BOS", +130, captured_at=t(10, hour=18, minute=20),
                          received_at=t(10, hour=18, minute=40))
        pred = self.model.predict(self.conn, 1, t(10, hour=18, minute=30))
        reports = pricing_engine.evaluate_moneyline_for_game(
            self.conn, pred, "TOR @ BOS", max_staleness_minutes=120.0)
        self.assertTrue(all(r.action == "DATA_UNAVAILABLE" for r in reports))


class TestQuoteReceivedInTimeIsEligible(unittest.TestCase):
    """Test B: captured 18:20, received 18:25, prediction 18:30 -- both
    genuinely known by prediction time. Must be eligible."""

    def setUp(self):
        self.conn, self.path = make_test_db()
        self.fx = Fixture(self.conn)
        self.model = CombinedMoneylineModel(teams=["TOR", "BOS"])

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_point_in_time_layer_accepts_the_received_in_time_quote(self):
        self.fx.add_odds(1, "TOR", -150, captured_at=t(10, hour=18, minute=20),
                          received_at=t(10, hour=18, minute=25))
        row = pit.latest_draftkings_snapshot(
            self.conn, 1, "MONEYLINE", "TOR", t(10, hour=18, minute=30))
        self.assertIsNotNone(row)
        self.assertEqual(row["price_american"], -150)

    def test_end_to_end_prices_normally_when_both_sides_are_received_in_time(self):
        self.fx.add_odds(1, "TOR", -150, captured_at=t(10, hour=18, minute=20),
                          received_at=t(10, hour=18, minute=25))
        self.fx.add_odds(1, "BOS", +130, captured_at=t(10, hour=18, minute=20),
                          received_at=t(10, hour=18, minute=25))
        pred = self.model.predict(self.conn, 1, t(10, hour=18, minute=30))
        reports = pricing_engine.evaluate_moneyline_for_game(
            self.conn, pred, "TOR @ BOS", max_staleness_minutes=120.0)
        self.assertTrue(all(r.action != "DATA_UNAVAILABLE" for r in reports))


class TestOlderKnownQuoteUsedOverNewerUnreceivedQuote(unittest.TestCase):
    """Test C: an older quote (captured 18:00, received 18:01) genuinely
    known by 18:30 must be used in preference to a newer quote (captured
    18:20, received 18:40) that had not yet been received -- never
    silently falling back to "no data" when an older, actually-known
    quote exists."""

    def setUp(self):
        self.conn, self.path = make_test_db()
        self.fx = Fixture(self.conn)
        self.model = CombinedMoneylineModel(teams=["TOR", "BOS"])

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_the_older_actually_known_quote_is_returned(self):
        self.fx.add_odds(1, "TOR", -140, captured_at=t(10, hour=18, minute=0),
                          received_at=t(10, hour=18, minute=1), label="old")
        self.fx.add_odds(1, "TOR", -150, captured_at=t(10, hour=18, minute=20),
                          received_at=t(10, hour=18, minute=40), label="new")
        row = pit.latest_draftkings_snapshot(
            self.conn, 1, "MONEYLINE", "TOR", t(10, hour=18, minute=30))
        self.assertIsNotNone(row)
        self.assertEqual(row["price_american"], -140)
        self.assertEqual(row["snapshot_label"], "old")

    def test_end_to_end_prices_from_the_older_quote_not_the_newer_one(self):
        self.fx.add_odds(1, "TOR", -140, captured_at=t(10, hour=18, minute=0),
                          received_at=t(10, hour=18, minute=1), label="old")
        self.fx.add_odds(1, "TOR", -150, captured_at=t(10, hour=18, minute=20),
                          received_at=t(10, hour=18, minute=40), label="new")
        self.fx.add_odds(1, "BOS", +130, captured_at=t(10, hour=18, minute=0),
                          received_at=t(10, hour=18, minute=1), label="old")
        pred = self.model.predict(self.conn, 1, t(10, hour=18, minute=30))
        reports = pricing_engine.evaluate_moneyline_for_game(
            self.conn, pred, "TOR @ BOS", max_staleness_minutes=120.0)
        home = next(r for r in reports if r.selection == "TOR")
        self.assertEqual(home.current_draftkings_price, -140)


class TestLateReceivedOddsRowDoesNotAlterStoredHistoricalReproduction(unittest.TestCase):
    """Test D: an odds row this system did not receive until AFTER a
    historical prediction/decision was already made and persisted must
    not alter that decision's reproduction/repricing -- whether replayed
    from its stored feature snapshot (pricing/decision.py::reproduce,
    which never re-touches the DB) or re-decided at the exact same
    historical prediction_time_utc through the live pricing path."""

    def setUp(self):
        self.conn, self.path = make_test_db()
        self.fx = Fixture(self.conn)
        self.model = CombinedMoneylineModel(teams=["TOR", "BOS"])

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def _decide(self):
        pred = self.model.predict(self.conn, 1, t(10, hour=18, minute=30))
        reports = pricing_engine.evaluate_moneyline_for_game(
            self.conn, pred, "TOR @ BOS", max_staleness_minutes=120.0)
        return pred, reports

    def test_reprising_at_the_same_historical_moment_is_unaffected_by_a_later_received_row(self):
        self.fx.add_odds(1, "TOR", -140, captured_at=t(10, hour=18, minute=0),
                          received_at=t(10, hour=18, minute=1), label="original")
        self.fx.add_odds(1, "BOS", +130, captured_at=t(10, hour=18, minute=0),
                          received_at=t(10, hour=18, minute=1), label="original")
        _, original_reports = self._decide()
        original_home = next(r for r in original_reports if r.selection == "TOR")

        # a provider backfills/corrects the market picture well after the
        # fact -- captured_at_utc even claims to predate prediction time,
        # but received_at_utc (when THIS system actually got the row) is
        # long after prediction_time_utc.
        self.fx.add_odds(1, "TOR", -400, captured_at=t(10, hour=18, minute=15),
                          received_at=t(20, hour=9), label="late_backfill")

        _, rerun_reports = self._decide()
        rerun_home = next(r for r in rerun_reports if r.selection == "TOR")
        self.assertEqual(original_home.current_draftkings_price,
                          rerun_home.current_draftkings_price)
        self.assertEqual(original_home.action, rerun_home.action)

    def test_stored_prediction_reproduction_is_unaffected_by_a_later_received_row(self):
        self.fx.add_odds(1, "TOR", -140, captured_at=t(10, hour=18, minute=0),
                          received_at=t(10, hour=18, minute=1))
        self.fx.add_odds(1, "BOS", +130, captured_at=t(10, hour=18, minute=0),
                          received_at=t(10, hour=18, minute=1))
        pred, reports = self._decide()
        home_report = next(r for r in reports if r.selection == "TOR")
        prediction_id = decision_mod.persist_full_decision(self.conn, pred, home_report)

        self.fx.add_odds(1, "TOR", -400, captured_at=t(10, hour=18, minute=15),
                          received_at=t(20, hour=9), label="late_backfill")

        replayed = decision_mod.reproduce(self.conn, prediction_id)
        self.assertEqual(replayed["original"], replayed["recomputed"])


if __name__ == "__main__":
    unittest.main()
