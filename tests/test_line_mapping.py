"""Tests for research/generic_prop_pricing/line_mapping.py (Part 33-35)."""
import unittest

from research.generic_prop_pricing import line_mapping as lm


class TestLineToThreshold(unittest.TestCase):
    def test_over_1_5_maps_to_2_plus(self):
        self.assertEqual(lm.line_to_threshold(1.5), 2)

    def test_over_2_5_maps_to_3_plus(self):
        self.assertEqual(lm.line_to_threshold(2.5), 3)

    def test_over_3_5_maps_to_4_plus(self):
        self.assertEqual(lm.line_to_threshold(3.5), 4)

    def test_over_4_5_maps_to_5_plus(self):
        self.assertEqual(lm.line_to_threshold(4.5), 5)

    def test_over_19_5_saves_maps_to_20_plus(self):
        self.assertEqual(lm.line_to_threshold(19.5), 20)

    def test_over_24_5_saves_maps_to_25_plus(self):
        self.assertEqual(lm.line_to_threshold(24.5), 25)

    def test_whole_number_line_raises(self):
        with self.assertRaises(lm.NonHalfPointLineError):
            lm.line_to_threshold(3.0)

    def test_none_raises(self):
        with self.assertRaises(lm.NonHalfPointLineError):
            lm.line_to_threshold(None)

    def test_quarter_point_line_raises(self):
        with self.assertRaises(lm.NonHalfPointLineError):
            lm.line_to_threshold(2.25)

    def test_threshold_to_line_is_the_true_inverse(self):
        for t in range(2, 30):
            self.assertEqual(lm.line_to_threshold(lm.threshold_to_line(t)), t)


class TestSogClassification(unittest.TestCase):
    def test_2_3_4_5_are_actionable(self):
        for t in (2, 3, 4, 5):
            result = lm.classify_sog_threshold(t)
            self.assertTrue(result.eligible, f"{t}+ should be actionable")

    def test_1_6_7_8_are_not_actionable(self):
        for t in (1, 6, 7, 8):
            result = lm.classify_sog_threshold(t)
            self.assertFalse(result.eligible, f"{t}+ should NOT be actionable")

    def test_sog_line_is_actionable_end_to_end(self):
        self.assertTrue(lm.sog_line_is_actionable(2.5).eligible)   # -> 3+
        self.assertFalse(lm.sog_line_is_actionable(0.5).eligible)  # -> 1+


class TestSavesClassification(unittest.TestCase):
    """Actionable saves thresholds are the ones the goalie model's validation report shows beating both baselines
    (docs/validation/goalie_team_validation.json): 20+ to 35+. Outside that range there is no support."""

    def test_the_whole_validated_range_is_actionable(self):
        for t in range(20, 36):
            self.assertTrue(lm.classify_saves_threshold(t).eligible, t)

    def test_outside_the_validated_range_is_insufficient_not_actionable(self):
        for t in (10, 19, 36, 40):
            c = lm.classify_saves_threshold(t)
            self.assertFalse(c.eligible, t)
            self.assertIn("INSUFFICIENT", c.reason)

    def test_the_validated_set_comes_from_the_report(self):
        import json
        from pathlib import Path
        report = json.loads((Path(lm.__file__).resolve().parent.parent.parent / "docs" / "validation" / "goalie_team_validation.json").read_text())
        beating = {int(k.split(">=")[1]) for k, m in report["saves_markets"].items()
                   if m["model_raw"]["log_loss"] < min(m["baseline_goalie_last10_mean"]["log_loss"], m["baseline_league_mean"]["log_loss"])}
        self.assertEqual(set(lm.SAVES_VALIDATED_THRESHOLDS), beating)

    def test_saves_line_is_actionable_end_to_end(self):
        self.assertTrue(lm.saves_line_is_actionable(24.5).eligible)   # -> 25+
        self.assertTrue(lm.saves_line_is_actionable(34.5).eligible)   # -> 35+
        self.assertFalse(lm.saves_line_is_actionable(35.5).eligible)  # -> 36+ unsupported


if __name__ == "__main__":
    unittest.main()
