import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))
import anchor_cap_study as study  # noqa: E402


class TestCapStudy(unittest.TestCase):
    def test_the_estimator_is_the_live_rule_with_only_the_cap_changed(self):
        self.assertAlmostEqual(study._final(0.80, 0.60, 2, 0.10), 0.70)
        self.assertAlmostEqual(study._final(0.80, 0.60, 2, 0.02), 0.62)
        self.assertAlmostEqual(study._final(0.65, 0.60, 2, 0.10), 0.65)         # the record is below the cap: nothing changes
        self.assertAlmostEqual(study._final(0.90, 0.85, 3, 0.10), 0.70)         # the 3+ line ceiling stays

    def test_scores_report_where_the_cap_binds(self):
        sample = [{"emp": 0.80, "model": 0.60, "hit": 1}, {"emp": 0.80, "model": 0.60, "hit": 0}, {"emp": 0.62, "model": 0.60, "hit": 1}]
        loose, tight = study._scores(sample, 0.10, 2), study._scores(sample, 0.02, 2)
        self.assertEqual((loose["capped_n"], tight["capped_n"]), (2, 2))
        self.assertAlmostEqual(tight["capped_final"], 0.62)
        self.assertAlmostEqual(loose["capped_final"], 0.70)
        self.assertAlmostEqual(loose["capped_happened"], 0.5)


if __name__ == "__main__":
    unittest.main()
