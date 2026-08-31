"""
Tests for research/moneypuck_special_teams_features.py and
research/special_teams_model_comparison.py -- the isolated MoneyPuck
special-teams (PP/PK) feature experiment. Small hand-built fixtures
against an in-memory research_moneypuck.db-shaped connection, mirroring
tests/test_moneypuck_team_features.py's conventions.
"""
import ast
import os
import sqlite3
import unittest

from research import moneypuck_special_teams_features as stf
from research import special_teams_model_comparison as stc
from research.moneypuck_ingestion import ingest as mp_ingest

SCHEMA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "research", "moneypuck_ingestion", "schema.sql",
)
FEATURE_MODULE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "research", "moneypuck_special_teams_features.py",
)


def make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    return conn


def insert_row(conn, *, game_id, team, opponent, situation, season, game_date,
                xg_for=1.0, xg_against=0.8, ice_time_seconds=300.0,
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


def seed(conn, team, opponent, season, situation, n_games, *, start_day=1, month="10",
         xg_for=1.0, xg_against=0.8, ice_time_seconds=300.0):
    year = str(season)[:4]
    for i in range(n_games):
        day = start_day + i
        insert_row(conn, game_id=int(f"{season}{start_day:03d}{i:04d}{situation.replace('on','')}"),
                   team=team, opponent=opponent, situation=situation, season=season,
                   game_date=f"{year}-{month}-{day:02d}", xg_for=xg_for, xg_against=xg_against,
                   ice_time_seconds=ice_time_seconds)


class TestSituationSelection(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()

    def test_pp_reads_5on4_only(self):
        seed(self.conn, "TOR", "BOS", 20222023, "5on4", n_games=10, xg_for=2.0, ice_time_seconds=300.0)
        seed(self.conn, "TOR", "BOS", 20222023, "4on5", n_games=10, xg_for=99.0, ice_time_seconds=300.0)
        result = stf.pp_xgf_per60(self.conn, "TOR", "2022-10-25", 20222023, window=10)
        # 2.0 xGF over 300s (5 min) -> per-60 = 2.0 * 12 = 24.0, NOT influenced by the 4on5 rows
        self.assertAlmostEqual(result, 24.0, places=6)

    def test_pk_reads_4on5_only(self):
        seed(self.conn, "TOR", "BOS", 20222023, "5on4", n_games=10, xg_against=99.0, ice_time_seconds=300.0)
        seed(self.conn, "TOR", "BOS", 20222023, "4on5", n_games=10, xg_against=1.5, ice_time_seconds=300.0)
        result = stf.pk_xga_per60(self.conn, "TOR", "2022-10-25", 20222023, window=10)
        self.assertAlmostEqual(result, 18.0, places=6)  # 1.5 * 12


class TestPpPkFormulas(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()

    def test_pp_per60_hand_computation(self):
        # 10 games, each 200s (3.33 min) and 0.5 xGF -> total xGF=5.0, total TOI=2000s
        seed(self.conn, "TOR", "BOS", 20222023, "5on4", n_games=10, xg_for=0.5, ice_time_seconds=200.0)
        result = stf.pp_xgf_per60(self.conn, "TOR", "2022-10-25", 20222023, window=10)
        expected = (5.0 * 3600.0) / 2000.0
        self.assertAlmostEqual(result, expected, places=6)

    def test_pk_per60_hand_computation(self):
        seed(self.conn, "TOR", "BOS", 20222023, "4on5", n_games=10, xg_against=0.4, ice_time_seconds=250.0)
        result = stf.pk_xga_per60(self.conn, "TOR", "2022-10-25", 20222023, window=10)
        expected = (4.0 * 3600.0) / 2500.0
        self.assertAlmostEqual(result, expected, places=6)


class TestMinimumSampleAndMaturity(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()

    def test_insufficient_games_returns_none(self):
        seed(self.conn, "TOR", "BOS", 20222023, "5on4", n_games=5)
        self.assertIsNone(stf.pp_xgf_per60(self.conn, "TOR", "2022-10-20", 20222023, window=10))

    def test_insufficient_toi_returns_none_even_with_enough_games(self):
        # 10 games but only 60s each (10 minutes total, well under the
        # MIN_TOTAL_TOI_SECONDS floor) -- a low-penalty-rate stretch that
        # must NOT be treated as a mature PP sample.
        seed(self.conn, "TOR", "BOS", 20222023, "5on4", n_games=10, ice_time_seconds=60.0)
        total_toi = 10 * 60.0
        self.assertLess(total_toi, stf.MIN_TOTAL_TOI_SECONDS)
        self.assertIsNone(stf.pp_xgf_per60(self.conn, "TOR", "2022-10-25", 20222023, window=10))

    def test_sufficient_games_and_toi_returns_a_value(self):
        seed(self.conn, "TOR", "BOS", 20222023, "5on4", n_games=10, ice_time_seconds=300.0)
        self.assertGreaterEqual(10 * 300.0, stf.MIN_TOTAL_TOI_SECONDS)
        self.assertIsNotNone(stf.pp_xgf_per60(self.conn, "TOR", "2022-10-25", 20222023, window=10))


class TestSeasonBoundary(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()

    def test_no_cross_season_carryover(self):
        seed(self.conn, "TOR", "BOS", 20222023, "5on4", n_games=15, xg_for=99.0, ice_time_seconds=300.0)
        seed(self.conn, "TOR", "BOS", 20232024, "5on4", n_games=5, xg_for=1.0, ice_time_seconds=300.0)
        self.assertIsNone(stf.pp_xgf_per60(self.conn, "TOR", "2023-10-20", 20232024, window=10))


class TestStrictPriorGameDate(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()

    def test_same_day_excluded(self):
        seed(self.conn, "TOR", "BOS", 20222023, "5on4", n_games=9, ice_time_seconds=300.0)
        insert_row(self.conn, game_id=2022029999, team="TOR", opponent="MTL", situation="5on4",
                   season=20222023, game_date="2022-10-09", ice_time_seconds=300.0)
        result = stf.pp_xgf_per60(self.conn, "TOR", "2022-10-09", 20222023, window=10)
        self.assertIsNone(result)

    def test_future_game_excluded(self):
        seed(self.conn, "TOR", "BOS", 20222023, "5on4", n_games=10, xg_for=1.0, ice_time_seconds=300.0)
        insert_row(self.conn, game_id=2022020999, team="TOR", opponent="MTL", situation="5on4",
                   season=20222023, game_date="2022-11-05", xg_for=999.0, ice_time_seconds=300.0)
        result = stf.pp_xgf_per60(self.conn, "TOR", "2022-10-25", 20222023, window=10)
        self.assertAlmostEqual(result, 12.0, places=6)  # 1.0*3600/300, unaffected by the future row


class TestMatchupConstruction(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()

    def test_matchup_terms_formula(self):
        seed(self.conn, "TOR", "X", 20222023, "5on4", n_games=10, xg_for=1.0, ice_time_seconds=300.0)   # TOR PP xGF60=12
        seed(self.conn, "TOR", "X", 20222023, "4on5", n_games=10, xg_against=0.5, ice_time_seconds=300.0)  # TOR PK xGA60=6
        seed(self.conn, "BOS", "X", 20222023, "5on4", n_games=10, xg_for=0.6, ice_time_seconds=300.0)   # BOS PP xGF60=7.2
        seed(self.conn, "BOS", "X", 20222023, "4on5", n_games=10, xg_against=0.8, ice_time_seconds=300.0)  # BOS PK xGA60=9.6

        terms = stf.matchup_terms(self.conn, "TOR", "BOS", "2022-10-25", 20222023, window=10)
        self.assertIsNotNone(terms)
        term_home, term_away = terms
        # term_home = home(TOR)_PP - away(BOS)_PK = 12 - 9.6 = 2.4
        self.assertAlmostEqual(term_home, 12.0 - 9.6, places=6)
        # term_away = away(BOS)_PP - home(TOR)_PK = 7.2 - 6 = 1.2
        self.assertAlmostEqual(term_away, 7.2 - 6.0, places=6)

    def test_matchup_requires_all_four_rates_mature(self):
        seed(self.conn, "TOR", "X", 20222023, "5on4", n_games=10, ice_time_seconds=300.0)
        # BOS has no data at all -- matchup must be None, not partially computed
        result = stf.matchup_terms(self.conn, "TOR", "BOS", "2022-10-25", 20222023, window=10)
        self.assertIsNone(result)


class TestCandidateFeatureComposition(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        seed(self.conn, "TOR", "X", 20222023, "5on4", n_games=10, xg_for=1.0, ice_time_seconds=300.0)
        seed(self.conn, "TOR", "X", 20222023, "4on5", n_games=10, xg_against=0.5, ice_time_seconds=300.0)
        seed(self.conn, "BOS", "X", 20222023, "5on4", n_games=10, xg_for=0.6, ice_time_seconds=300.0)
        seed(self.conn, "BOS", "X", 20222023, "4on5", n_games=10, xg_against=0.8, ice_time_seconds=300.0)
        self.record = {"home_team": "TOR", "away_team": "BOS", "game_date": "2022-10-25", "season": 20222023}

    def test_pp_diff_is_home_minus_away(self):
        result = stc.compute_pp_diff(self.conn, self.record, window=10)
        self.assertAlmostEqual(result, 12.0 - 7.2, places=6)

    def test_pk_diff_favors_home_when_home_pk_is_tighter(self):
        # TOR (home) PK xGA60=6, BOS (away) PK xGA60=9.6 -> TOR's PK is
        # tighter (lower xGA) -> pk_diff should be POSITIVE (favors home)
        result = stc.compute_pk_diff(self.conn, self.record, window=10)
        self.assertAlmostEqual(result, 9.6 - 6.0, places=6)
        self.assertGreater(result, 0)

    def test_matchup_diff_is_term_home_minus_term_away(self):
        result = stc.compute_matchup_diff(self.conn, self.record, window=10)
        expected_term_home = 12.0 - 9.6
        expected_term_away = 7.2 - 6.0
        self.assertAlmostEqual(result, expected_term_home - expected_term_away, places=6)

    def test_reproducibility(self):
        r1 = stc.compute_matchup_diff(self.conn, self.record, window=10)
        r2 = stc.compute_matchup_diff(self.conn, self.record, window=10)
        self.assertEqual(r1, r2)


class TestNoDirectSqlBypass(unittest.TestCase):
    def test_special_teams_feature_module_never_issues_raw_sql(self):
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
        """Part 8: same guarantee as the xG experiment -- the special-teams
        driver reuses research.elo_comparison.run_walkforward()'s baseline
        records (research/xg_model_comparison.py / run_special_teams_comparison.py
        both build on it), which derive actual_home_win strictly from
        research/real_nhl_results, never MoneyPuck goals."""
        from research import elo_comparison as ec
        from research import xg_model_comparison as xgc
        games = ec.load_corpus(xgc.NHL_CORPUS_PATH)
        so_games = [g for g in games if g["period_type"] == "SO"]
        sample = so_games[0]
        records, _ = ec.run_walkforward([sample])
        official_home_win = 1.0 if sample["home_score"] > sample["away_score"] else 0.0
        self.assertEqual(records[0]["actual_home_win"], official_home_win)


class TestProductionModelUntouched(unittest.TestCase):
    def test_special_teams_modules_never_import_production_combined_model(self):
        import research.special_teams_model_comparison as stc_mod
        import research.moneypuck_special_teams_features as stf_mod
        for mod in (stc_mod, stf_mod):
            with open(mod.__file__) as f:
                source = f.read()
            self.assertNotIn("models.combined_model", source)
            self.assertNotIn("models.elo_model", source)


if __name__ == "__main__":
    unittest.main()
