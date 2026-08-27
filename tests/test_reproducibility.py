"""
Spec completion criterion 7: "Historical predictions are reproducible."
Proves the DB-reads/pure-function split (models/combined_model.py +
pricing/decision.py) actually delivers that: persisting a decision, then
replaying it from nothing but its stored feature_snapshot_json, must
reproduce the exact original probabilities.
"""
import unittest

import config
from features import point_in_time as pit
from models.combined_model import CombinedMoneylineModel, compute_probability_from_features
from pricing import decision as decision_mod
from pricing import engine as pricing_engine
from tests.helpers import Fixture, make_test_db, t


class TestReproducibility(unittest.TestCase):
    def setUp(self):
        self.conn, self.path = make_test_db()
        self.fx = Fixture(self.conn)
        self.fx.set_goalie_status(1, "TOR", "TOR_G1", "CONFIRMED", effective_at=t(9, hour=17))
        self.fx.set_goalie_status(1, "BOS", "BOS_G1", "CONFIRMED", effective_at=t(9, hour=17))
        self.prediction_time = t(9, hour=18, minute=30)

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_bare_prediction_reproduces_exactly(self):
        model = CombinedMoneylineModel(teams=["TOR", "BOS"])
        pred = model.predict(self.conn, 1, self.prediction_time)
        pred_id = decision_mod.persist_bare_prediction(self.conn, pred)
        self.conn.commit()

        result = decision_mod.reproduce(self.conn, pred_id)
        self.assertEqual(result["original"]["model_prob_home"], result["recomputed"]["model_prob_home"])
        self.assertEqual(result["original"]["conservative_prob_home"],
                          result["recomputed"]["conservative_prob_home"])
        self.assertEqual(result["original"]["ci_low"], result["recomputed"]["ci_low"])
        self.assertEqual(result["original"]["ci_high"], result["recomputed"]["ci_high"])
        self.assertEqual(result["model_version"], config.MODEL_VERSION)

    def test_model_prediction_reproduces_exactly_even_from_a_full_decision_row(self):
        # v2.1.2a spec item 10 (renamed from test_full_decision_reproduces_
        # exactly): reproduce() only ever recomputes/compares the MODEL-
        # PREDICTION level of a stored row (model_prob_home,
        # conservative_prob_home, ci_low, ci_high) -- see
        # pricing/decision.py::reproduce()'s docstring. It never recomputes
        # or compares the no-vig probability, edge, EV, max acceptable
        # price, or BET/WAIT/PASS action, so this test's old name implied
        # more than it actually proved. The assertions below are UNCHANGED
        # from before the rename -- this is still exactly what it always
        # tested: that persisting a FULL decision (which additionally
        # stores pricing fields) doesn't change what reproduce() recomputes
        # or how it compares. Full DECISION-level (pricing/action)
        # reproducibility/versioned replay is explicitly out of scope this
        # slice (spec item 11) -- not built, not tested here.
        self.fx.add_odds(1, "TOR", -150, captured_at=t(9, hour=18), label="T-30")
        self.fx.add_odds(1, "BOS", +130, captured_at=t(9, hour=18), label="T-30")

        model = CombinedMoneylineModel(teams=["TOR", "BOS"])
        pred = model.predict(self.conn, 1, self.prediction_time)
        reports = pricing_engine.evaluate_moneyline_for_game(self.conn, pred, "TOR @ BOS")
        home_report = next(r for r in reports if r.selection == "TOR")
        pred_id = decision_mod.persist_full_decision(self.conn, pred, home_report)

        result = decision_mod.reproduce(self.conn, pred_id)
        self.assertEqual(result["original"], result["recomputed"])

    def test_pure_function_is_deterministic_across_repeated_calls(self):
        model = CombinedMoneylineModel(teams=["TOR", "BOS"])
        pred = model.predict(self.conn, 1, self.prediction_time)
        fs = pred.feature_snapshot
        first = compute_probability_from_features(fs)
        second = compute_probability_from_features(fs)
        third = compute_probability_from_features(fs)
        self.assertEqual(first, second)
        self.assertEqual(second, third)

    def test_rerunning_ingestion_is_idempotent_for_reproducibility(self):
        # re-persisting the same feature snapshot through the pure function
        # after "re-ingesting" (i.e. nothing about the DB changed) must
        # still match — this is the reproducibility half of idempotency;
        # ingest-path idempotency itself is covered in
        # tests/test_ingest_idempotency.py.
        model = CombinedMoneylineModel(teams=["TOR", "BOS"])
        pred1 = model.predict(self.conn, 1, self.prediction_time)
        model2 = CombinedMoneylineModel(teams=["TOR", "BOS"])
        pred2 = model2.predict(self.conn, 1, self.prediction_time)
        self.assertEqual(pred1.feature_snapshot, pred2.feature_snapshot)
        self.assertEqual(pred1.model_prob_home, pred2.model_prob_home)


if __name__ == "__main__":
    unittest.main()
