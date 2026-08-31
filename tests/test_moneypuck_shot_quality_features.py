"""
Tests for research/moneypuck_shot_quality_features.py and
research/shot_quality_model_comparison.py -- the isolated MoneyPuck
offense/defense shot-quality decomposition experiment. Small hand-built
fixtures against an in-memory research_moneypuck.db-shaped connection,
mirroring the conventions of the two prior MoneyPuck feature test files.
"""
import ast
import os
import sqlite3
import unittest

from research import moneypuck_shot_quality_features as sqf
from research import shot_quality_model_comparison as sqc
from research.moneypuck_ingestion import ingest as mp_ingest

SCHEMA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "research", "moneypuck_ingestion", "schema.sql",
)
FEATURE_MODULE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "research", "moneypuck_shot_quality_features.py",
)


def make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    return conn


def insert_row(conn, *, game_id, team, opponent, situation, season, game_date,
                xg_for=2.0, xg_against=1.5, ice_time_seconds=3000.0,
                downloaded_at_utc="2026-08-27T13:23:32+00:00", source_sha256="a" * 64):
    cols = mp_ingest._INSERT_COLUMNS
    row = {c: None for c in cols}
    row.update({
        "game_id": game_id, "season": season, "game_date": game_date, "team": team,
        "opponent": opponent, "situation": situation, "home_or_away": "HOME",
        "ice_time_seconds": ice_time_seconds,
        "goals_for": 0, "goals_against": 0, "shots_for": 5, "shots_against": 4,
        "xg_for": xg_for, "xg_against": xg_against,
        "provenance_type": "ARCHIVAL_RESEARCH", "source": "MoneyPuck",
        "source_file": "test", "source_sha256": source_sha256,
        "downloaded_at_utc": downloaded_at_utc, "ingested_at_utc": downloaded_at_utc,
        "xg_model_version_semantics": "UNKNOWN",
    })
    placeholders = ",".join("?" * len(cols))
    conn.execute(f"INSERT INTO research_moneypuck_team_game_stats ({','.join(cols)}) VALUES ({placeholders})",
                 [row[c] for c in cols])


def seed(conn, team, opponent, season, n_games, *, situation="5on5", start_day=1, month="10",
         xg_for=2.0, xg_against=1.5, ice_time_seconds=3000.0):
    year = str(season)[:4]
    for i in range(n_games):
        day = start_day + i
        insert_row(conn, game_id=int(f"{season}{start_day:03d}{i:04d}5"), team=team, opponent=opponent,
                   situation=situation, season=season, game_date=f"{year}-{month}-{day:02d}",
                   xg_for=xg_for, xg_against=xg_against, ice_time_seconds=ice_time_seconds)


class TestSituationSelection(unittest.TestCase):
    def test_offense_reads_5on5_only(self):
        conn = make_db()
        seed(conn, "TOR", "BOS", 20222023, 10, situation="5on5", xg_for=2.0, ice_time_seconds=3000.0)
        seed(conn, "TOR", "BOS", 20222023, 10, situation="5on4", xg_for=999.0, ice_time_seconds=300.0)
        result = sqf.offense_xgf_per60(conn, "TOR", "2022-10-25", 20222023, window=10)
        expected = (20.0 * 3600.0) / 30000.0
        self.assertAlmostEqual(result, expected, places=6)


class TestPooledRateFormulas(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()

    def test_xgf60_pooled_not_averaged(self):
        # two games with very different per-game rates: game1 has small
        # TOI and huge xG (would dominate a naive per-game average),
        # game2 has large TOI and small xG. Pooled rate must weight by
        # actual TOI, not treat both games equally.
        insert_row(self.conn, game_id=2022020001, team="TOR", opponent="BOS", situation="5on5",
                   season=20222023, game_date="2022-10-01", xg_for=5.0, ice_time_seconds=600.0)
        insert_row(self.conn, game_id=2022020002, team="TOR", opponent="BOS", situation="5on5",
                   season=20222023, game_date="2022-10-02", xg_for=1.0, ice_time_seconds=3000.0)
        result = sqf.offense_xgf_per60(self.conn, "TOR", "2022-10-05", 20222023, window=2)
        pooled_expected = (5.0 + 1.0) * 3600.0 / (600.0 + 3000.0)
        naive_average_of_per_game_rates = ((5.0 * 3600 / 600) + (1.0 * 3600 / 3000)) / 2
        self.assertAlmostEqual(result, pooled_expected, places=6)
        self.assertNotAlmostEqual(result, naive_average_of_per_game_rates, places=2)

    def test_xga60_hand_computation(self):
        seed(self.conn, "TOR", "BOS", 20222023, 10, xg_against=0.6, ice_time_seconds=3600.0)
        result = sqf.defense_xga_per60(self.conn, "TOR", "2022-10-25", 20222023, window=10)
        expected = (6.0 * 3600.0) / 36000.0
        self.assertAlmostEqual(result, expected, places=6)


class TestMaturityAndSeasonBoundary(unittest.TestCase):
    def test_insufficient_games_returns_none(self):
        conn = make_db()
        seed(conn, "TOR", "BOS", 20222023, 9)
        self.assertIsNone(sqf.offense_xgf_per60(conn, "TOR", "2022-10-20", 20222023, window=10))

    def test_no_cross_season_carryover(self):
        conn = make_db()
        seed(conn, "TOR", "BOS", 20222023, 15, xg_for=999.0)
        seed(conn, "TOR", "BOS", 20232024, 5, xg_for=2.0)
        self.assertIsNone(sqf.offense_xgf_per60(conn, "TOR", "2023-10-20", 20232024, window=10))

    def test_5v5_toi_floor_is_essentially_never_binding(self):
        # 5v5 TOI is abundant (~49 min/game in the real corpus) -- even a
        # deliberately low per-game TOI still clears the 10-minute floor
        # over a full window.
        conn = make_db()
        seed(conn, "TOR", "BOS", 20222023, 10, ice_time_seconds=120.0)  # 2 min/game -> 20 min total
        self.assertGreaterEqual(10 * 120.0, sqf.MIN_TOTAL_TOI_SECONDS)
        self.assertIsNotNone(sqf.offense_xgf_per60(conn, "TOR", "2022-10-25", 20222023, window=10))


class TestStrictPriorGameDate(unittest.TestCase):
    def test_same_day_excluded(self):
        conn = make_db()
        seed(conn, "TOR", "BOS", 20222023, 9)
        insert_row(conn, game_id=2022029999, team="TOR", opponent="MTL", situation="5on5",
                   season=20222023, game_date="2022-10-09")
        self.assertIsNone(sqf.offense_xgf_per60(conn, "TOR", "2022-10-09", 20222023, window=10))

    def test_future_game_excluded(self):
        conn = make_db()
        seed(conn, "TOR", "BOS", 20222023, 10, xg_for=2.0, ice_time_seconds=3000.0)
        insert_row(conn, game_id=2022020999, team="TOR", opponent="MTL", situation="5on5",
                   season=20222023, game_date="2022-11-05", xg_for=999.0)
        result = sqf.offense_xgf_per60(conn, "TOR", "2022-10-25", 20222023, window=10)
        expected = (20.0 * 3600.0) / 30000.0
        self.assertAlmostEqual(result, expected, places=6)


class TestMatchupConstruction(unittest.TestCase):
    def test_matchup_terms_formula(self):
        conn = make_db()
        seed(conn, "TOR", "X", 20222023, 10, xg_for=2.0, xg_against=1.0, ice_time_seconds=3600.0)  # off=7.2*10=72? recompute
        seed(conn, "BOS", "X", 20222023, 10, xg_for=1.0, xg_against=2.0, ice_time_seconds=3600.0)
        terms = sqf.matchup_terms(conn, "TOR", "BOS", "2022-10-25", 20222023, window=10)
        self.assertIsNotNone(terms)
        tor_off = sqf.offense_xgf_per60(conn, "TOR", "2022-10-25", 20222023, window=10)
        tor_def = sqf.defense_xga_per60(conn, "TOR", "2022-10-25", 20222023, window=10)
        bos_off = sqf.offense_xgf_per60(conn, "BOS", "2022-10-25", 20222023, window=10)
        bos_def = sqf.defense_xga_per60(conn, "BOS", "2022-10-25", 20222023, window=10)
        term_home, term_away = terms
        self.assertAlmostEqual(term_home, tor_off - bos_def, places=6)
        self.assertAlmostEqual(term_away, bos_off - tor_def, places=6)

    def test_matchup_requires_all_four_mature(self):
        conn = make_db()
        seed(conn, "TOR", "X", 20222023, 10)
        result = sqf.matchup_terms(conn, "TOR", "BOS", "2022-10-25", 20222023, window=10)
        self.assertIsNone(result)


class TestCandidateFeatureComposition(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        seed(self.conn, "TOR", "X", 20222023, 10, xg_for=2.0, xg_against=1.0, ice_time_seconds=3600.0)
        seed(self.conn, "BOS", "X", 20222023, 10, xg_for=1.0, xg_against=2.0, ice_time_seconds=3600.0)
        self.record = {"home_team": "TOR", "away_team": "BOS", "game_date": "2022-10-25", "season": 20222023}

    def test_offense_diff_is_home_minus_away(self):
        result = sqc.compute_offense_diff(self.conn, self.record, window=10)
        tor_off = sqf.offense_xgf_per60(self.conn, "TOR", "2022-10-25", 20222023, window=10)
        bos_off = sqf.offense_xgf_per60(self.conn, "BOS", "2022-10-25", 20222023, window=10)
        self.assertAlmostEqual(result, tor_off - bos_off, places=6)

    def test_defense_diff_favors_home_when_home_defense_tighter(self):
        result = sqc.compute_defense_diff(self.conn, self.record, window=10)
        self.assertGreater(result, 0)  # TOR's xGA is lower (tighter D) than BOS's

    def test_offense_defense_pair_matches_individual_components(self):
        pair = sqc.compute_offense_defense_pair(self.conn, self.record, window=10)
        off = sqc.compute_offense_diff(self.conn, self.record, window=10)
        dfn = sqc.compute_defense_diff(self.conn, self.record, window=10)
        self.assertEqual(pair, (off, dfn))

    def test_reproducibility(self):
        r1 = sqc.compute_matchup(self.conn, self.record, window=10)
        r2 = sqc.compute_matchup(self.conn, self.record, window=10)
        self.assertEqual(r1, r2)


class TestNoDirectSqlBypass(unittest.TestCase):
    def test_feature_module_never_issues_raw_sql(self):
        with open(FEATURE_MODULE_PATH) as f:
            tree = ast.parse(f.read())
        violations = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in ("execute", "executescript", "executemany"):
                    violations.append(node.lineno)
        self.assertEqual(violations, [], f"direct SQL execute() found at lines {violations}")


class TestOfficialOutcomeSourceReused(unittest.TestCase):
    def test_baseline_records_come_from_real_corpus_not_moneypuck(self):
        from research import elo_comparison as ec
        from research import xg_model_comparison as xgc
        games = ec.load_corpus(xgc.NHL_CORPUS_PATH)
        so_games = [g for g in games if g["period_type"] == "SO"]
        sample = so_games[0]
        records, _ = ec.run_walkforward([sample])
        official_home_win = 1.0 if sample["home_score"] > sample["away_score"] else 0.0
        self.assertEqual(records[0]["actual_home_win"], official_home_win)


class TestStandardizationTrainingOnly(unittest.TestCase):
    def test_standardize_fit_is_independent_of_data_it_is_later_applied_to(self):
        from research import xg_model_comparison as xgc
        tuning_values = [1.0, 2.0, 3.0, 4.0, 5.0]
        mean, stdev = xgc.standardize_fit(tuning_values)
        # applying to wildly different "evaluation" values must not
        # refit mean/stdev -- they were fixed entirely by tuning_values.
        eval_values = [1000.0, -1000.0, 500.0]
        z_eval = [xgc.standardize_apply(v, mean, stdev) for v in eval_values]
        self.assertAlmostEqual(mean, 3.0, places=9)
        # z scores for the eval values should reflect the TUNING mean/std,
        # not be renormalized around the eval set's own distribution
        self.assertAlmostEqual(z_eval[0], (1000.0 - 3.0) / stdev, places=6)


class TestProductionModelUntouched(unittest.TestCase):
    def test_shot_quality_modules_never_import_production_combined_model(self):
        import research.shot_quality_model_comparison as sqc_mod
        import research.moneypuck_shot_quality_features as sqf_mod
        for mod in (sqc_mod, sqf_mod):
            with open(mod.__file__) as f:
                source = f.read()
            self.assertNotIn("models.combined_model", source)
            self.assertNotIn("models.elo_model", source)


if __name__ == "__main__":
    unittest.main()
