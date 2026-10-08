"""Moneyline model path (default Elo, opt-in strength switch, shadow log and scoreboard) and the puck-line alternative's prospective log."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from operational import moneyline_model_path as mmp
from research.product_models import moneyline_comparison, puck_line_alternative as pla

NOW = dt.datetime(2026, 10, 8, 20, 0, tzinfo=dt.timezone.utc)
REPORT = types.SimpleNamespace(model_true_probability=0.60, model_conservative_probability=0.55)


def games_db():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript((Path(__file__).parent.parent / "schema.sql").read_text())
    return c


class IsolatedState(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict("os.environ", {"NHL_ENGINE_STATE_DIR": tempfile.mkdtemp()})
        patcher.start()
        self.addCleanup(patcher.stop)


class TestMoneylinePath(IsolatedState):
    def test_default_is_elo_and_changes_nothing(self):
        self.assertEqual(mmp.active_model(), mmp.ELO)
        self.assertEqual(mmp.version(), "moneyline-t35-v1")
        self.assertEqual(mmp.apply_switch(REPORT, True, "TOR", "MTL"), (0.60, 0.55))

    def test_asking_for_the_switch_is_not_enough_without_evidence(self):
        with mock.patch.dict("os.environ", {mmp.ENV: "strength-v1"}), mock.patch.object(mmp, "evidence", return_value={}):
            self.assertEqual(mmp.active_model(), mmp.ELO)
        with mock.patch.dict("os.environ", {mmp.ENV: "strength-v1"}), mock.patch.object(mmp, "evidence", return_value={"supports_strength_model": False}):
            self.assertEqual(mmp.active_model(), mmp.ELO)
        with mock.patch.dict("os.environ", {}), mock.patch.object(mmp, "evidence", return_value={"supports_strength_model": True}):
            self.assertEqual(mmp.active_model(), mmp.ELO)                  # evidence alone does not switch either; the owner must also ask

    def test_switch_uses_strength_probability_with_the_same_band(self):
        with mock.patch.dict("os.environ", {mmp.ENV: "strength-v1"}), mock.patch.object(mmp, "evidence", return_value={"supports_strength_model": True}), \
                mock.patch.object(mmp, "strength_probability", return_value=0.70):
            self.assertEqual(mmp.version(), "moneyline-strength-v1")
            true_home, cons_home = mmp.apply_switch(REPORT, True, "TOR", "MTL")
            true_away, cons_away = mmp.apply_switch(REPORT, False, "TOR", "MTL")
        self.assertAlmostEqual(true_home, 0.70)
        self.assertAlmostEqual(cons_home, 0.65)                       # 0.70 minus Elo's own 0.05 band
        self.assertAlmostEqual(true_away, 0.30)
        self.assertAlmostEqual(cons_away, 0.25)

    def test_unknown_team_falls_back_to_elo(self):
        with mock.patch.dict("os.environ", {mmp.ENV: "strength-v1"}), mock.patch.object(mmp, "evidence", return_value={"supports_strength_model": True}), \
                mock.patch.object(mmp, "strength_probability", return_value=None):
            self.assertEqual(mmp.apply_switch(REPORT, True, "XXX", "YYY"), (0.60, 0.55))

    def test_an_unknown_setting_is_ignored(self):
        with mock.patch.dict("os.environ", {mmp.ENV: "something-else"}):
            self.assertEqual(mmp.active_model(), mmp.ELO)

    def test_shadow_log_is_one_row_per_day_game_selection_and_scoreboard_scores_final_games(self):
        self.assertTrue(mmp.record_shadow(NOW, "1", "2026-10-08", "TOR", "MTL", "TOR", 0.60, 0.66, 0.58, -140))
        self.assertFalse(mmp.record_shadow(NOW, "1", "2026-10-08", "TOR", "MTL", "TOR", 0.61, 0.66, 0.58, -140))       # duplicate
        self.assertFalse(mmp.record_shadow(NOW, "2", "2026-10-08", "A", "B", "A", None, 0.5, None, 100))               # nothing to compare
        self.assertTrue(mmp.record_shadow(NOW, "1", "2026-10-08", "TOR", "MTL", "MTL", 0.40, 0.34, 0.42, 120))
        c = games_db()
        for t in ("TOR", "MTL"):
            c.execute("INSERT OR IGNORE INTO teams (team_id, full_name) VALUES (?, ?)", (t, t))
        c.execute("INSERT INTO games (game_id, home_team, away_team, game_state, home_score, away_score) VALUES (1, 'TOR', 'MTL', 'FINAL', 4, 2)")
        sb = mmp.scoreboard(c)
        self.assertEqual((sb["logged"], sb["scored"]), (2, 2))
        self.assertLess(sb["log_loss"]["strength"], sb["log_loss"]["elo"] + 1)       # computed, not asserted to win
        self.assertIn("market_no_vig", sb["log_loss"])
        self.assertEqual(sb["verdict"], "TOO_FEW_GAMES")
        self.assertFalse(sb["supports_strength_model"])

    def test_scoreboard_supports_the_strength_model_only_when_the_interval_is_below_zero(self):
        c = games_db()
        for t in ("TOR", "MTL"):
            c.execute("INSERT OR IGNORE INTO teams (team_id, full_name) VALUES (?, ?)", (t, t))
        for i in range(200):
            c.execute("INSERT INTO games (game_id, home_team, away_team, game_state, home_score, away_score) VALUES (?, 'TOR', 'MTL', 'FINAL', ?, ?)", (1000 + i, 3, 2 if i % 4 else 4))
            clearly_better = i % 4 != 0
            mmp.record_shadow(NOW, str(1000 + i), "2026-10-08", "TOR", "MTL", "TOR", 0.55, 0.80 if clearly_better else 0.30, 0.55, -120)
        sb = mmp.scoreboard(c)
        self.assertEqual(sb["scored"], 200)
        self.assertLess(sb["strength_minus_elo"]["ci_high"], 0)
        self.assertTrue(sb["supports_strength_model"])
        self.assertEqual(sb["verdict"], "STRENGTH_SUPPORTED")


class TestComparisonReport(unittest.TestCase):
    def test_saved_comparison_has_paired_intervals_for_both_folds_and_both_outcome_definitions(self):
        rep = json.loads(moneyline_comparison.OUT_PATH.read_text())
        self.assertEqual(rep["version"], "moneyline-compare-v1")
        self.assertEqual([f["scored_season"] for f in rep["folds"]], [2024, 2025])
        for f in rep["folds"]:
            for k in ("decided_in_play_only", "all_games_incl_shootouts"):
                d = f[k]["strength_vs_elo_log_loss_delta"]
                self.assertLess(d["ci_low"], d["ci_high"])
                self.assertEqual(set(f[k]["scores"]), {"home_rate_baseline", "elo_production", "strength_model"})
        self.assertTrue(any("market" in x for x in rep["limits"]))

    def test_default_remains_elo_because_no_interval_excludes_zero_in_the_latest_fold(self):
        rep = json.loads(moneyline_comparison.OUT_PATH.read_text())
        latest = rep["folds"][-1]
        for k in ("decided_in_play_only", "all_games_incl_shootouts"):
            d = latest[k]["strength_vs_elo_log_loss_delta"]
            self.assertLess(d["ci_low"], d["ci_high"])
            self.assertGreaterEqual(d["ci_high"], 0.0)                 # interval reaches zero: not a basis to promote
        self.assertEqual(mmp.active_model(), mmp.ELO)


class TestPuckLineAlternative(IsolatedState):
    def test_saved_report_keeps_the_failed_model_on_record_and_enables_nothing(self):
        rep = json.loads(pla.OUT_PATH.read_text())
        self.assertIn("failed", rep["record_kept"])
        self.assertEqual(rep["prospective"]["status"], "COLLECTING")
        self.assertTrue(any("prices" in x for x in rep["not_enabled"]))
        self.assertEqual([f["scored_season"] for f in rep["development"]["folds"]], [2023, 2024])        # 2025-26 is not used again
        goalie_validation = json.loads((pla.OUT_PATH.parent / "goalie_team_validation.json").read_text())
        self.assertGreater(goalie_validation["puck_line"]["model"]["log_loss"], goalie_validation["puck_line"]["base_rate_baseline"]["log_loss"])

    def test_probability_is_a_logistic_of_the_strength_difference(self):
        self.assertAlmostEqual(pla.probability([0.0, 1.0], 0.0), 0.5)
        self.assertGreater(pla.probability([-1.0, 1.0], 1.0), pla.probability([-1.0, 1.0], 0.0))

    def test_prospective_log_scores_extra_time_margins_as_one_goal(self):
        p = pla.state_paths.path(pla.LOG_NAME)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("".join(json.dumps({"game_id": str(i), "p_home_covers": 0.25}) + "\n" for i in (1, 2, 3)))
        c = games_db()
        for t in ("A", "B"):
            c.execute("INSERT OR IGNORE INTO teams (team_id, full_name) VALUES (?, ?)", (t, t))
        c.execute("INSERT INTO games (game_id, home_team, away_team, game_state, home_score, away_score, final_period_type) VALUES (1,'A','B','FINAL',5,2,'REG')")
        c.execute("INSERT INTO games (game_id, home_team, away_team, game_state, home_score, away_score, final_period_type) VALUES (2,'A','B','FINAL',3,2,'OT')")
        c.execute("INSERT INTO games (game_id, home_team, away_team, game_state, home_score, away_score, final_period_type) VALUES (3,'A','B','SCHEDULED',NULL,NULL,NULL)")
        out = pla.prospective_score(c)
        self.assertEqual((out["logged"], out["scored"]), (3, 2))
        self.assertEqual(out["verdict"], "TOO_FEW_GAMES")
        p.unlink()


if __name__ == "__main__":
    unittest.main()
