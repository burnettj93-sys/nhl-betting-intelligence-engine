"""2026-27 Continuous Learning framework, Part 63: tests for
operational/challenger_registry.py -- evidence validation, lifecycle
transitions, and the hard rule that NOTHING here can auto-promote a
production model."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from operational import challenger_registry as cr

GOOD_EVIDENCE = {"occurrences": 10, "unique_game_dates": 5, "mean_residual": 0.4,
                  "explanation": "repeated underprediction for recently-promoted PP1 players"}


class _TempRegistry(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        tmp.close()
        self.path = Path(tmp.name)
        self.path.unlink()  # start from "does not exist yet"


class Test01EvidenceValidation(unittest.TestCase):
    def test_too_few_occurrences_rejected(self):
        with self.assertRaises(cr.ChallengerValidationError):
            cr.validate_evidence({"occurrences": 2, "unique_game_dates": 5, "explanation": "x"})

    def test_too_few_unique_dates_rejected(self):
        with self.assertRaises(cr.ChallengerValidationError):
            cr.validate_evidence({"occurrences": 10, "unique_game_dates": 1, "explanation": "x"})

    def test_missing_explanation_rejected(self):
        with self.assertRaises(cr.ChallengerValidationError):
            cr.validate_evidence({"occurrences": 10, "unique_game_dates": 5, "explanation": ""})

    def test_one_outlier_game_never_creates_a_challenger(self):
        # Part 24's literal example: one big game is not evidence.
        with self.assertRaises(cr.ChallengerValidationError):
            cr.validate_evidence({"occurrences": 1, "unique_game_dates": 1, "explanation": "one huge game"})

    def test_good_evidence_passes(self):
        cr.validate_evidence(GOOD_EVIDENCE)  # must not raise


class Test02ProposeChallenger(_TempRegistry):
    def test_propose_creates_hypothesis_status_entry(self):
        entry = cr.propose_challenger(target_model="PLAYER_SOG", hypothesis="test hypothesis",
                                       evidence=GOOD_EVIDENCE, training_window="2026-10 to 2026-11",
                                       validation_plan="shadow re-score for 30 days",
                                       registry_path=self.path)
        self.assertEqual(entry["status"], "HYPOTHESIS")
        self.assertIn("challenger_id", entry)

    def test_weak_evidence_never_reaches_the_registry(self):
        with self.assertRaises(cr.ChallengerValidationError):
            cr.propose_challenger(target_model="PLAYER_SOG", hypothesis="x",
                                   evidence={"occurrences": 1, "unique_game_dates": 1, "explanation": "x"},
                                   training_window="x", validation_plan="x", registry_path=self.path)
        self.assertEqual(cr.load_registry(self.path), [])

    def test_duplicate_challenger_id_rejected(self):
        cr.propose_challenger(target_model="PLAYER_SOG", hypothesis="x", evidence=GOOD_EVIDENCE,
                               training_window="x", validation_plan="x", challenger_id="dupe",
                               registry_path=self.path)
        with self.assertRaises(cr.ChallengerValidationError):
            cr.propose_challenger(target_model="GOALS", hypothesis="y", evidence=GOOD_EVIDENCE,
                                   training_window="y", validation_plan="y", challenger_id="dupe",
                                   registry_path=self.path)


_VERSION_FIELDS = dict(feature_version="v2", training_cutoff="2026-11-01", evaluation_cutoff="2026-11-15",
                       code_commit="abc1234", reason_for_change="repeated PP1 underprediction")


class Test03LifecycleTransitions(_TempRegistry):
    def setUp(self):
        super().setUp()
        self.entry = cr.propose_challenger(target_model="PLAYER_SOG", hypothesis="x", evidence=GOOD_EVIDENCE,
                                            training_window="x", validation_plan="x", challenger_id="c1",
                                            registry_path=self.path, **_VERSION_FIELDS)

    def test_valid_transition_hypothesis_to_testing(self):
        updated = cr.update_status("c1", "TESTING", registry_path=self.path)
        self.assertEqual(updated["status"], "TESTING")

    def test_invalid_transition_hypothesis_to_promotion_candidate_rejected(self):
        with self.assertRaises(cr.ChallengerValidationError):
            cr.update_status("c1", "PROMOTION_CANDIDATE", registry_path=self.path)

    def test_full_valid_lifecycle_to_promotion_candidate(self):
        cr.update_status("c1", "TESTING", registry_path=self.path)
        cr.update_status("c1", "SHADOW", registry_path=self.path)
        final = cr.update_status("c1", "PROMOTION_CANDIDATE", registry_path=self.path)
        self.assertEqual(final["status"], "PROMOTION_CANDIDATE")

    def test_rejected_is_terminal(self):
        cr.update_status("c1", "TESTING", registry_path=self.path)
        cr.update_status("c1", "REJECTED", registry_path=self.path)
        with self.assertRaises(cr.ChallengerValidationError):
            cr.update_status("c1", "TESTING", registry_path=self.path)

    def test_status_history_is_preserved(self):
        cr.update_status("c1", "TESTING", registry_path=self.path)
        entries = cr.load_registry(self.path)
        self.assertEqual(len(entries[0]["status_history"]), 2)


class Test05VersionControlFields(_TempRegistry):
    def test_testing_requires_version_control_fields(self):
        cr.propose_challenger(target_model="PLAYER_SOG", hypothesis="x", evidence=GOOD_EVIDENCE,
                               training_window="x", validation_plan="x", challenger_id="c1",
                               registry_path=self.path)
        with self.assertRaises(cr.ChallengerValidationError):
            cr.update_status("c1", "TESTING", registry_path=self.path)

    def test_testing_succeeds_once_version_control_fields_are_present(self):
        cr.propose_challenger(target_model="PLAYER_SOG", hypothesis="x", evidence=GOOD_EVIDENCE,
                               training_window="x", validation_plan="x", challenger_id="c1",
                               feature_version="v2", training_cutoff="2026-11-01",
                               evaluation_cutoff="2026-11-15", code_commit="abc1234",
                               reason_for_change="repeated PP1 underprediction", registry_path=self.path)
        updated = cr.update_status("c1", "TESTING", registry_path=self.path)
        self.assertEqual(updated["status"], "TESTING")


class Test04NoAutoPromotion(_TempRegistry):
    def test_reaching_promotion_candidate_status_never_touches_model_registry(self):
        """The hard rule: this module has NO import of, or call into,
        research.model_registry, decision_policy, or any production
        model file -- reaching PROMOTION_CANDIDATE status is purely a
        label in this JSON file, never a production mutation."""
        import inspect
        src = inspect.getsource(cr)
        code_lines = [line for line in src.splitlines() if "import" in line and not line.strip().startswith("#")]
        self.assertFalse(any("model_registry" in line for line in code_lines))
        self.assertFalse(any("decision_policy" in line for line in code_lines))

    def test_promotion_candidates_is_read_only_reporting(self):
        cr.propose_challenger(target_model="PLAYER_SOG", hypothesis="x", evidence=GOOD_EVIDENCE,
                               training_window="x", validation_plan="x", challenger_id="c1",
                               registry_path=self.path, **_VERSION_FIELDS)
        cr.update_status("c1", "TESTING", registry_path=self.path)
        cr.update_status("c1", "SHADOW", registry_path=self.path)
        cr.update_status("c1", "PROMOTION_CANDIDATE", registry_path=self.path)
        candidates = cr.promotion_candidates(registry_path=self.path)
        self.assertEqual(len(candidates), 1)
        # Still just a label -- confirm no side effect changed the underlying model registry.
        from research.model_registry import MODEL_REGISTRY
        sog_entry = next(e for e in MODEL_REGISTRY if e.model_id == "PLAYER_SOG")
        self.assertEqual(sog_entry.status, "VALIDATED")


if __name__ == "__main__":
    unittest.main()
