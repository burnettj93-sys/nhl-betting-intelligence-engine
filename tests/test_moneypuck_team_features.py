"""
Tests for research/moneypuck_team_features.py and the pure-logic parts
of research/xg_model_comparison.py -- the isolated MoneyPuck team
xG/shot-quality feature experiment. Uses small hand-built fixtures
against an in-memory research_moneypuck.db-shaped SQLite connection and
the production real-outcome corpus format -- never the full real
databases (too slow for unit tests; the real end-to-end run is captured
in research/xg_comparison_results.json and XG_TEAM_FEATURE_EXPERIMENT_REPORT.md).
"""
import ast
import math
import os
import sqlite3
import unittest

from research import moneypuck_team_features as mpf
from research import xg_model_comparison as xgc
from research.moneypuck_ingestion import ingest as mp_ingest

SCHEMA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "research", "moneypuck_ingestion", "schema.sql",
)

FEATURE_MODULE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "research", "moneypuck_team_features.py",
)


def make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    return conn


def insert_row(conn, *, game_id, team, opponent, situation, season, game_date,
                goals_for=2, goals_against=1, xg_for=2.0, xg_against=1.5,
                downloaded_at_utc="2026-08-27T13:23:32+00:00", source_sha256="a" * 64,
                provenance_type="ARCHIVAL_RESEARCH"):
    cols = mp_ingest._INSERT_COLUMNS
    row = {c: None for c in cols}
    row.update({
        "game_id": game_id, "season": season, "game_date": game_date, "team": team,
        "opponent": opponent, "situation": situation, "home_or_away": "HOME",
        "goals_for": goals_for, "goals_against": goals_against,
        "shots_for": 30, "shots_against": 28,
        "xg_for": xg_for, "xg_against": xg_against,
        "provenance_type": provenance_type, "source": "MoneyPuck",
        "source_file": "test", "source_sha256": source_sha256,
        "downloaded_at_utc": downloaded_at_utc, "ingested_at_utc": downloaded_at_utc,
        "xg_model_version_semantics": "UNKNOWN",
    })
    placeholders = ",".join("?" * len(cols))
    conn.execute(f"INSERT INTO research_moneypuck_team_game_stats ({','.join(cols)}) VALUES ({placeholders})",
                 [row[c] for c in cols])


def seed_team_season(conn, team, opponent, season, situation, n_games, start_day=1,
                      xg_for=2.0, xg_against=1.5, month="10"):
    year = str(season)[:4]  # season is the 8-digit YYYYZZZZ form (e.g. 20222023) -- NOT a calendar year
    for i in range(n_games):
        day = start_day + i
        insert_row(conn, game_id=int(f"{season}{start_day:03d}{i:04d}"), team=team, opponent=opponent,
                   situation=situation, season=season, game_date=f"{year}-{month}-{day:02d}",
                   xg_for=xg_for, xg_against=xg_against)


class TestRollingXgShare(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()

    def test_insufficient_games_returns_none(self):
        seed_team_season(self.conn, "TOR", "BOS", 20222023, "5on5", n_games=5)
        result = mpf.rolling_xg_share(self.conn, "TOR", "2022-10-20", 20222023, window=10)
        self.assertIsNone(result)

    def test_exact_window_matches_hand_computation(self):
        # 10 games, each xGF=2.0, xGA=1.0 -> share = 2/(2+1) = 0.6667 uniformly
        seed_team_season(self.conn, "TOR", "BOS", 20222023, "5on5", n_games=10,
                          xg_for=2.0, xg_against=1.0)
        result = mpf.rolling_xg_share(self.conn, "TOR", "2022-10-25", 20222023, window=10)
        self.assertAlmostEqual(result, 2.0 / 3.0, places=9)

    def test_target_games_own_row_is_excluded(self):
        # 10 prior games with share 0.6667, then a hypothetical 11th (the
        # target game itself, same date being predicted) with wildly
        # different numbers -- must NOT affect the rolling value computed
        # AS OF that same date.
        seed_team_season(self.conn, "TOR", "BOS", 20222023, "5on5", n_games=10,
                          xg_for=2.0, xg_against=1.0, start_day=1)
        insert_row(self.conn, game_id=2022029999, team="TOR", opponent="MTL", situation="5on5",
                   season=20222023, game_date="2022-10-11", xg_for=99.0, xg_against=0.1)
        result = mpf.rolling_xg_share(self.conn, "TOR", "2022-10-11", 20222023, window=10)
        self.assertAlmostEqual(result, 2.0 / 3.0, places=9)

    def test_default_situation_is_5v5(self):
        seed_team_season(self.conn, "TOR", "BOS", 20222023, "5on5", n_games=10,
                          xg_for=3.0, xg_against=1.0)
        seed_team_season(self.conn, "TOR", "BOS", 20222023, "all", n_games=10,
                          xg_for=1.0, xg_against=3.0)
        default_result = mpf.rolling_xg_share(self.conn, "TOR", "2022-10-25", 20222023, window=10)
        five_v_five_result = mpf.rolling_xg_share(self.conn, "TOR", "2022-10-25", 20222023,
                                                    window=10, situation="5on5")
        self.assertAlmostEqual(default_result, five_v_five_result, places=9)
        self.assertAlmostEqual(default_result, 0.75, places=9)  # 3/(3+1), not the 'all' numbers


class TestRollingXgDiffPerGame(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()

    def test_default_situation_is_all(self):
        seed_team_season(self.conn, "TOR", "BOS", 20222023, "all", n_games=10,
                          xg_for=2.5, xg_against=1.5)
        result = mpf.rolling_xg_diff_per_game(self.conn, "TOR", "2022-10-25", 20222023, window=10)
        self.assertAlmostEqual(result, 1.0, places=9)

    def test_insufficient_games_returns_none(self):
        seed_team_season(self.conn, "TOR", "BOS", 20222023, "all", n_games=9)
        self.assertIsNone(mpf.rolling_xg_diff_per_game(self.conn, "TOR", "2022-10-25", 20222023, window=10))


class TestSeasonBoundaryNoCarryover(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()

    def test_prior_season_games_do_not_count_toward_current_season_maturity(self):
        # 15 games in 2022-23 (season ends), then only 5 in 2023-24 --
        # a window=10 feature for the CURRENT season must be None, even
        # though the team has 20 total historical games across both seasons.
        seed_team_season(self.conn, "TOR", "BOS", 20222023, "5on5", n_games=15,
                          start_day=1, month="10")
        seed_team_season(self.conn, "TOR", "BOS", 20232024, "5on5", n_games=5,
                          start_day=1, month="10")
        result = mpf.rolling_xg_share(self.conn, "TOR", "2023-10-20", 20232024, window=10)
        self.assertIsNone(result)

    def test_current_season_games_alone_can_satisfy_the_window(self):
        seed_team_season(self.conn, "TOR", "BOS", 20222023, "5on5", n_games=15,
                          start_day=1, month="10", xg_for=99.0, xg_against=0.1)  # would badly skew if leaked
        seed_team_season(self.conn, "TOR", "BOS", 20232024, "5on5", n_games=10,
                          start_day=1, month="10", xg_for=2.0, xg_against=1.0)
        result = mpf.rolling_xg_share(self.conn, "TOR", "2023-10-20", 20232024, window=10)
        self.assertAlmostEqual(result, 2.0 / 3.0, places=9)  # unaffected by 2022-23's extreme numbers


class TestStrictPriorGameDateAtFeatureLayer(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()

    def test_same_day_game_is_excluded_from_its_own_feature(self):
        seed_team_season(self.conn, "TOR", "BOS", 20222023, "5on5", n_games=9, start_day=1)
        insert_row(self.conn, game_id=2022020500, team="TOR", opponent="MTL", situation="5on5",
                   season=20222023, game_date="2022-10-10", xg_for=1.0, xg_against=1.0)
        # exactly 10 games total, but the 10th shares the SAME date as the
        # target -- must not count toward the window (strict <, not <=)
        result = mpf.rolling_xg_share(self.conn, "TOR", "2022-10-10", 20222023, window=10)
        self.assertIsNone(result)

    def test_future_game_is_excluded(self):
        seed_team_season(self.conn, "TOR", "BOS", 20222023, "5on5", n_games=10, start_day=1)
        insert_row(self.conn, game_id=2022020999, team="TOR", opponent="MTL", situation="5on5",
                   season=20222023, game_date="2022-11-01", xg_for=50.0, xg_against=0.1)
        result = mpf.rolling_xg_share(self.conn, "TOR", "2022-10-25", 20222023, window=10)
        self.assertAlmostEqual(result, mpf.rolling_xg_share(
            self.conn, "TOR", "2022-10-25", 20222023, window=10), places=9)  # deterministic, no future leak
        # value must be exactly 2/(2+1.5) from seed_team_season's defaults,
        # NOT influenced by the 50.0 future row
        self.assertAlmostEqual(result, 2.0 / 3.5, places=9)


class TestXgFormDelta(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()

    def test_requires_long_window_games(self):
        seed_team_season(self.conn, "TOR", "BOS", 20222023, "5on5", n_games=20)
        result = mpf.xg_form_delta(self.conn, "TOR", "2022-11-15", 20222023,
                                    short_window=10, long_window=25)
        self.assertIsNone(result)

    def test_positive_when_recent_form_exceeds_medium_term(self):
        # first 15 games weak (share 0.4), most recent 10 strong (share 0.8)
        seed_team_season(self.conn, "TOR", "BOS", 20222023, "5on5", n_games=15,
                          start_day=1, xg_for=2.0, xg_against=3.0)  # share 0.4
        seed_team_season(self.conn, "TOR", "BOS", 20222023, "5on5", n_games=10,
                          start_day=16, xg_for=4.0, xg_against=1.0)  # share 0.8
        result = mpf.xg_form_delta(self.conn, "TOR", "2022-11-20", 20222023,
                                    short_window=10, long_window=25)
        self.assertIsNotNone(result)
        self.assertGreater(result, 0)

    def test_asserts_short_less_than_long(self):
        with self.assertRaises(AssertionError):
            mpf.xg_form_delta(self.conn, "TOR", "2022-11-20", 20222023,
                               short_window=25, long_window=10)


class TestReproducibility(unittest.TestCase):
    def test_same_inputs_produce_identical_output(self):
        conn = make_db()
        seed_team_season(conn, "TOR", "BOS", 20222023, "5on5", n_games=10)
        r1 = mpf.rolling_xg_share(conn, "TOR", "2022-10-25", 20222023, window=10)
        r2 = mpf.rolling_xg_share(conn, "TOR", "2022-10-25", 20222023, window=10)
        self.assertEqual(r1, r2)


class TestNoDirectSqlBypass(unittest.TestCase):
    def test_feature_module_never_issues_a_raw_sql_query(self):
        """Part 5: 'do not bypass [the query layer] with direct SQLite
        queries inside feature-generation code.' AST-scans the feature
        module for any conn.execute(...)-shaped call -- it must only ever
        call research.moneypuck_ingestion.query.team_stats_as_of()."""
        with open(FEATURE_MODULE_PATH) as f:
            tree = ast.parse(f.read())
        violations = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in ("execute", "executescript", "executemany"):
                    violations.append(node.lineno)
        self.assertEqual(violations, [], f"direct SQL execute() found at lines {violations}")


class TestLogisticIntegrationMath(unittest.TestCase):
    def test_sigmoid_logit_are_inverses(self):
        for p in [0.1, 0.3, 0.5, 0.7, 0.9]:
            self.assertAlmostEqual(xgc.sigmoid(xgc.logit(p)), p, places=9)

    def test_zero_weight_reduces_to_baseline_exactly(self):
        base_p = 0.62
        z = 1.7
        beta = 0.0
        candidate_p = xgc.sigmoid(xgc.logit(base_p) + beta * z)
        self.assertAlmostEqual(candidate_p, base_p, places=9)

    def test_standardize_fit_and_apply(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        mean, stdev = xgc.standardize_fit(values)
        self.assertAlmostEqual(mean, 3.0, places=9)
        z = [xgc.standardize_apply(v, mean, stdev) for v in values]
        self.assertAlmostEqual(sum(z) / len(z), 0.0, places=9)

    def test_standardize_handles_zero_variance(self):
        mean, stdev = xgc.standardize_fit([5.0, 5.0, 5.0])
        self.assertEqual(stdev, 1.0)  # falls back rather than dividing by zero

    def test_fit_logistic_weights_is_deterministic(self):
        base_logits = [0.1, -0.2, 0.3, -0.1, 0.05] * 4
        features = [[0.5], [-0.3], [0.8], [-0.6], [0.1]] * 4
        actual = [1.0, 0.0, 1.0, 0.0, 1.0] * 4
        w1 = xgc.fit_logistic_weights(base_logits, features, actual, n_iter=200)
        w2 = xgc.fit_logistic_weights(base_logits, features, actual, n_iter=200)
        self.assertEqual(w1, w2)

    def test_fit_logistic_weights_moves_in_the_correct_direction(self):
        # a feature perfectly correlated with the outcome should get a
        # positive weight (higher feature -> higher predicted probability)
        base_logits = [0.0] * 20
        features = [[1.0] if i % 2 == 0 else [-1.0] for i in range(20)]
        actual = [1.0 if i % 2 == 0 else 0.0 for i in range(20)]
        weights = xgc.fit_logistic_weights(base_logits, features, actual, n_iter=500)
        self.assertGreater(weights[0], 0)


class TestOfficialOutcomeSource(unittest.TestCase):
    def test_real_corpus_home_score_is_the_truth_label_not_moneypuck(self):
        """Part 15: the win/loss truth label must come from
        research/real_nhl_results, never from MoneyPuck's own
        goals_for/goals_against (which excludes the shootout-deciding
        goal -- see MONEYPUCK_TEAM_INGESTION_REPORT.md Section P). This
        experiment gets its outcome labels from
        research.elo_comparison.run_walkforward(), which derives
        actual_home_win strictly from the corpus's own
        home_score/away_score fields -- confirmed directly here for a
        real, hand-checked shootout game."""
        from research import elo_comparison as ec
        games = ec.load_corpus(xgc.NHL_CORPUS_PATH)
        # find any real SO game in the corpus and confirm actual_home_win
        # matches the OFFICIAL (shootout-inclusive) score, not a tied score.
        so_games = [g for g in games if g["period_type"] == "SO"]
        self.assertGreater(len(so_games), 0)
        sample = so_games[0]
        records, _ = ec.run_walkforward([sample])
        rec = records[0]
        official_home_win = 1.0 if sample["home_score"] > sample["away_score"] else 0.0
        self.assertEqual(rec["actual_home_win"], official_home_win)
        # and the two scores are NOT equal (a real SO game always has a
        # 1-goal official margin) -- sanity-checks this is genuinely a
        # decided-by-shootout game, not a data artifact
        self.assertNotEqual(sample["home_score"], sample["away_score"])


class TestCommonEvaluationSetConcept(unittest.TestCase):
    def test_intersection_excludes_any_game_missing_from_any_candidate(self):
        b_mature = {1, 2, 3, 4}
        c_mature = {2, 3, 4, 5}
        d_mature = {2, 3, 6}
        common = b_mature & c_mature & d_mature
        self.assertEqual(common, {2, 3})


class TestProductionModelUntouched(unittest.TestCase):
    def test_xg_comparison_module_never_imports_production_combined_model(self):
        """This experiment must never alter or even need to import the
        live combined model -- the baseline is reused, tested, isolated
        Elo-only research code (research/elo_comparison.py), never
        models/combined_model.py directly."""
        import research.xg_model_comparison as mod
        with open(mod.__file__) as f:
            source = f.read()
        self.assertNotIn("models.combined_model", source)
        self.assertNotIn("from models import combined_model", source)


if __name__ == "__main__":
    unittest.main()
