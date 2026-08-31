"""
Tests for research/moneypuck_ingestion/ -- the isolated MoneyPuck team
game-by-game research ingestion pipeline. This package is entirely
separate from production nhl.db/schema.sql; these tests use small
hand-built CSV fixtures (never the real ~126MB MoneyPuck file, which
isn't part of the repo) and an in-memory or tempfile sqlite database for
the research storage layer.
"""
import csv
import os
import sqlite3
import tempfile
import unittest

from research.moneypuck_ingestion import ingest as mp_ingest
from research.moneypuck_ingestion.checksums import (
    ChecksumError, is_valid_sha256_hex, sha256_hex_of_bytes, sha256_hex_of_file,
)
from research.moneypuck_ingestion.query import team_stats_as_of, team_stats_for_game, unique_game_coverage
from research.moneypuck_ingestion.raw_archive import RawFileProvenance

SCHEMA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "research", "moneypuck_ingestion", "schema.sql",
)

FIELDS = mp_ingest.REQUIRED_SOURCE_COLUMNS + mp_ingest.OPTIONAL_SOURCE_COLUMNS


def _row(game_id, team, opponent, home_or_away, season, game_date, situation,
         goals_for, goals_against, shots_for=30.0, shots_against=28.0,
         xg_for=2.0, xg_against=1.5, playoff="0", **extra):
    row = {c: "" for c in FIELDS}
    row.update({
        "team": team, "season": str(season), "gameId": str(game_id),
        "opposingTeam": opponent, "home_or_away": home_or_away, "gameDate": game_date,
        "situation": situation, "goalsFor": str(float(goals_for)), "goalsAgainst": str(float(goals_against)),
        "shotsOnGoalFor": str(shots_for), "shotsOnGoalAgainst": str(shots_against),
        "xGoalsFor": str(xg_for), "xGoalsAgainst": str(xg_against),
        "playoffGame": playoff,
    })
    row.update(extra)
    return row


def write_csv(rows, path, fieldnames=FIELDS):
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    return conn


def make_provenance(sha256="a" * 64, downloaded_at_utc="2026-08-27T13:23:32+00:00"):
    return RawFileProvenance(
        original_filename="all_teams.csv", source_url="https://moneypuck.com/x/all_teams.csv",
        dataset="team_gamebygame", downloaded_at_utc=downloaded_at_utc, season_coverage="all",
        byte_size=1234, sha256=sha256, validation_status="PENDING",
    )


class TestChecksums(unittest.TestCase):
    def test_sha256_of_bytes_is_exactly_64_hex_chars(self):
        digest = sha256_hex_of_bytes(b"hello moneypuck")
        self.assertEqual(len(digest), 64)
        self.assertTrue(is_valid_sha256_hex(digest))

    def test_sha256_of_file_matches_shasum_style_digest(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("a,b\n1,2\n")
            path = f.name
        try:
            digest = sha256_hex_of_file(path)
            self.assertTrue(is_valid_sha256_hex(digest))
            import hashlib
            with open(path, "rb") as fh:
                expected = hashlib.sha256(fh.read()).hexdigest()
            self.assertEqual(digest, expected)
        finally:
            os.unlink(path)

    def test_is_valid_sha256_hex_rejects_the_prior_off_by_one_bug_shape(self):
        # the prior review's bug produced a 65-char string (one extra
        # trailing hex char) -- this must be rejected, not silently accepted.
        sixty_five_chars = "a" * 65
        self.assertFalse(is_valid_sha256_hex(sixty_five_chars))
        sixty_four_chars = "a" * 64
        self.assertTrue(is_valid_sha256_hex(sixty_four_chars))

    def test_is_valid_sha256_hex_rejects_uppercase_and_non_hex(self):
        self.assertFalse(is_valid_sha256_hex("A" * 64))
        self.assertFalse(is_valid_sha256_hex("g" * 64))
        self.assertFalse(is_valid_sha256_hex(""))
        self.assertFalse(is_valid_sha256_hex(None))


class TestSeasonAndGameTypeDerivation(unittest.TestCase):
    def test_derive_nhl_season_matches_yyyyzzzz_convention(self):
        self.assertEqual(mp_ingest.derive_nhl_season(2022), 20222023)
        self.assertEqual(mp_ingest.derive_nhl_season(2025), 20252026)

    def test_derive_game_type_reads_digits_five_and_six(self):
        self.assertEqual(mp_ingest.derive_game_type(2022020001), 2)   # regular season
        self.assertEqual(mp_ingest.derive_game_type(2025030412), 3)   # playoff


class TestSchemaValidation(unittest.TestCase):
    def test_valid_header_passes(self):
        mp_ingest.validate_schema(FIELDS)  # must not raise

    def test_missing_required_column_fails_loudly(self):
        broken_header = [c for c in FIELDS if c != "goalsFor"]
        with self.assertRaises(mp_ingest.MoneyPuckSchemaError):
            mp_ingest.validate_schema(broken_header)

    def test_html_instead_of_csv_is_rejected_as_missing_columns(self):
        # an HTML "you're scraping" page has none of the expected columns
        html_like_header = ["<html>", "<head>"]
        with self.assertRaises(mp_ingest.MoneyPuckSchemaError):
            mp_ingest.validate_schema(html_like_header)


class TestNormalizeRow(unittest.TestCase):
    def _kwargs(self):
        return dict(provenance_type="ARCHIVAL_RESEARCH", source_file="raw/x.csv",
                    source_sha256="a" * 64, downloaded_at_utc="2026-08-27T13:23:32+00:00",
                    ingested_at_utc="2026-08-27T14:00:00+00:00")

    def test_valid_row_normalizes_correctly(self):
        raw = _row(2022020001, "NSH", "SJS", "HOME", 2022, "20221007", "all", 4, 1)
        normalized = mp_ingest.normalize_row(raw, **self._kwargs())
        self.assertEqual(normalized["game_id"], 2022020001)
        self.assertEqual(normalized["season"], 20222023)
        self.assertEqual(normalized["game_date"], "2022-10-07")
        self.assertEqual(normalized["team"], "NSH")
        self.assertEqual(normalized["opponent"], "SJS")
        self.assertEqual(normalized["goals_for"], 4)
        self.assertEqual(normalized["goals_against"], 1)
        self.assertEqual(normalized["situation"], "all")
        self.assertEqual(normalized["provenance_type"], "ARCHIVAL_RESEARCH")
        self.assertEqual(normalized["xg_model_version_semantics"], "UNKNOWN")

    def test_missing_game_id_raises(self):
        raw = _row(2022020001, "NSH", "SJS", "HOME", 2022, "20221007", "all", 4, 1)
        raw["gameId"] = ""
        with self.assertRaises(mp_ingest.MoneyPuckRowError):
            mp_ingest.normalize_row(raw, **self._kwargs())

    def test_missing_team_raises(self):
        raw = _row(2022020001, "NSH", "SJS", "HOME", 2022, "20221007", "all", 4, 1)
        raw["team"] = ""
        with self.assertRaises(mp_ingest.MoneyPuckRowError):
            mp_ingest.normalize_row(raw, **self._kwargs())

    def test_invalid_numeric_field_raises(self):
        raw = _row(2022020001, "NSH", "SJS", "HOME", 2022, "20221007", "all", 4, 1)
        raw["goalsFor"] = "not-a-number"
        with self.assertRaises(mp_ingest.MoneyPuckRowError):
            mp_ingest.normalize_row(raw, **self._kwargs())

    def test_malformed_game_date_raises(self):
        raw = _row(2022020001, "NSH", "SJS", "HOME", 2022, "2022-10-07", "all", 4, 1)  # wrong format
        with self.assertRaises(mp_ingest.MoneyPuckRowError):
            mp_ingest.normalize_row(raw, **self._kwargs())

    def test_situation_is_preserved_verbatim(self):
        for situation in ["all", "5on5", "5on4", "4on5", "other"]:
            raw = _row(2022020001, "NSH", "SJS", "HOME", 2022, "20221007", situation, 4, 1)
            normalized = mp_ingest.normalize_row(raw, **self._kwargs())
            self.assertEqual(normalized["situation"], situation)


class TestIngestFileEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.conn = make_db()

    def _write_and_ingest(self, rows, provenance_type="ARCHIVAL_RESEARCH", target_seasons=None):
        path = os.path.join(self.tmpdir, "fixture.csv")
        write_csv(rows, path)
        provenance = make_provenance()
        report = mp_ingest.ingest_file(self.conn, path, provenance,
                                        provenance_type=provenance_type, target_seasons=target_seasons)
        return report

    def test_valid_file_ingests_regular_season_rows_only(self):
        rows = [
            _row(2022020001, "NSH", "SJS", "HOME", 2022, "20221007", "all", 4, 1, playoff="0"),
            _row(2022020001, "SJS", "NSH", "AWAY", 2022, "20221007", "all", 1, 4, playoff="0"),
            _row(2025030412, "CAR", "VGK", "HOME", 2025, "20260301", "all", 4, 3, playoff="1"),  # playoff, excluded
            _row(2025030412, "VGK", "CAR", "AWAY", 2025, "20260301", "all", 3, 4, playoff="1"),
        ]
        report = self._write_and_ingest(rows)
        self.assertEqual(report.rows_read, 4)
        self.assertEqual(report.rows_regular_season_target, 2)
        self.assertEqual(report.rows_new, 2)
        self.assertEqual(report.rows_rejected, 0)
        coverage = unique_game_coverage(self.conn, situation="all")
        self.assertEqual(coverage, {2022020001})

    def test_exact_row_grain_one_row_per_team_game_situation(self):
        rows = []
        for situation in ["all", "5on5", "5on4", "4on5", "other"]:
            rows.append(_row(2022020001, "NSH", "SJS", "HOME", 2022, "20221007", situation, 4, 1))
            rows.append(_row(2022020001, "SJS", "NSH", "AWAY", 2022, "20221007", situation, 1, 4))
        self._write_and_ingest(rows)
        n = self.conn.execute("SELECT COUNT(*) AS n FROM research_moneypuck_team_game_stats").fetchone()["n"]
        self.assertEqual(n, 10)  # 2 teams x 5 situations

    def test_canonical_key_uniqueness_enforced(self):
        rows = [_row(2022020001, "NSH", "SJS", "HOME", 2022, "20221007", "all", 4, 1)]
        self._write_and_ingest(rows)
        # attempting a raw duplicate INSERT (bypassing classify_and_ingest_row)
        # for the identical natural key + source_sha256 must violate the
        # unique index.
        cols = mp_ingest._INSERT_COLUMNS
        existing = dict(self.conn.execute("SELECT * FROM research_moneypuck_team_game_stats").fetchone())
        values = [existing[c] for c in cols]
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                f"INSERT INTO research_moneypuck_team_game_stats ({','.join(cols)}) "
                f"VALUES ({','.join('?' * len(cols))})", values,
            )

    def test_idempotent_second_ingestion_produces_zero_new(self):
        rows = [
            _row(2022020001, "NSH", "SJS", "HOME", 2022, "20221007", "all", 4, 1),
            _row(2022020001, "SJS", "NSH", "AWAY", 2022, "20221007", "all", 1, 4),
        ]
        report1 = self._write_and_ingest(rows)
        self.assertEqual(report1.rows_new, 2)
        report2 = self._write_and_ingest(rows)
        self.assertEqual(report2.rows_new, 0)
        self.assertEqual(report2.rows_revised, 0)
        self.assertEqual(report2.rows_unchanged, 2)
        total = self.conn.execute("SELECT COUNT(*) AS n FROM research_moneypuck_team_game_stats").fetchone()["n"]
        self.assertEqual(total, 2)  # no duplicate rows written

    def test_revised_row_is_appended_not_overwritten(self):
        row_v1 = _row(2022020001, "NSH", "SJS", "HOME", 2022, "20221007", "all", 4, 1)
        self._write_and_ingest([row_v1])
        row_v2 = _row(2022020001, "NSH", "SJS", "HOME", 2022, "20221007", "all", 5, 1)  # corrected goalsFor
        path2 = os.path.join(self.tmpdir, "fixture2.csv")
        write_csv([row_v2], path2)
        provenance2 = make_provenance(sha256="b" * 64)  # a DIFFERENT raw snapshot
        report2 = mp_ingest.ingest_file(self.conn, path2, provenance2, provenance_type="ARCHIVAL_RESEARCH")
        self.assertEqual(report2.rows_revised, 1)
        all_rows = self.conn.execute(
            "SELECT goals_for, source_sha256 FROM research_moneypuck_team_game_stats "
            "WHERE game_id=2022020001 AND team='NSH' AND situation='all' ORDER BY ingested_at_utc"
        ).fetchall()
        self.assertEqual(len(all_rows), 2)  # both revisions preserved, nothing overwritten
        self.assertEqual([r["goals_for"] for r in all_rows], [4, 5])

    def test_missing_required_column_file_fails_loudly_and_promotes_nothing(self):
        path = os.path.join(self.tmpdir, "broken.csv")
        broken_fields = [c for c in FIELDS if c != "goalsFor"]
        write_csv([_row(1, "NSH", "SJS", "HOME", 2022, "20221007", "all", 4, 1)], path, fieldnames=broken_fields)
        provenance = make_provenance()
        with self.assertRaises(mp_ingest.MoneyPuckSchemaError):
            mp_ingest.ingest_file(self.conn, path, provenance, provenance_type="ARCHIVAL_RESEARCH")
        n = self.conn.execute("SELECT COUNT(*) AS n FROM research_moneypuck_team_game_stats").fetchone()["n"]
        self.assertEqual(n, 0)

    def test_corrupt_file_fails_loudly(self):
        path = os.path.join(self.tmpdir, "corrupt.csv")
        with open(path, "w") as f:
            f.write("")  # empty file -- no header at all
        provenance = make_provenance()
        with self.assertRaises(mp_ingest.MoneyPuckSchemaError):
            mp_ingest.ingest_file(self.conn, path, provenance, provenance_type="ARCHIVAL_RESEARCH")

    def test_invalid_numeric_field_is_rejected_not_promoted(self):
        row = _row(2022020001, "NSH", "SJS", "HOME", 2022, "20221007", "all", 4, 1)
        row["goalsFor"] = "NaN-ish-garbage"
        report = self._write_and_ingest([row])
        self.assertEqual(report.rows_rejected, 1)
        n = self.conn.execute("SELECT COUNT(*) AS n FROM research_moneypuck_team_game_stats").fetchone()["n"]
        self.assertEqual(n, 0)

    def test_gameid_type_disagreeing_with_playoff_flag_is_rejected(self):
        # a regular-season game_id (type 02) claiming playoffGame=1 is
        # an internal inconsistency -- must be flagged, not silently trusted.
        row = _row(2022020001, "NSH", "SJS", "HOME", 2022, "20221007", "all", 4, 1, playoff="1")
        report = self._write_and_ingest([row])
        self.assertEqual(report.rows_rejected, 1)
        self.assertEqual(report.rows_new, 0)

    def test_provenance_classification_is_stored_per_row(self):
        rows = [_row(2022020001, "NSH", "SJS", "HOME", 2022, "20221007", "all", 4, 1)]
        self._write_and_ingest(rows, provenance_type="LIVE_OBSERVED")
        row = self.conn.execute("SELECT provenance_type FROM research_moneypuck_team_game_stats").fetchone()
        self.assertEqual(row["provenance_type"], "LIVE_OBSERVED")

    def test_no_fabricated_historical_observed_at_timestamp(self):
        # downloaded_at_utc on every normalized row must equal exactly what
        # the (real) provenance record says -- never a timestamp backdated
        # to the game's own date.
        rows = [_row(2022020001, "NSH", "SJS", "HOME", 2022, "20221007", "all", 4, 1)]
        report = self._write_and_ingest(rows)
        row = self.conn.execute(
            "SELECT downloaded_at_utc, game_date FROM research_moneypuck_team_game_stats"
        ).fetchone()
        self.assertEqual(row["downloaded_at_utc"], "2026-08-27T13:23:32+00:00")
        self.assertNotEqual(row["downloaded_at_utc"][:10], row["game_date"])  # not backdated to game_date

    def test_target_seasons_filter_excludes_other_seasons(self):
        rows = [
            _row(2021020001, "NSH", "SJS", "HOME", 2021, "20211007", "all", 4, 1),  # outside target
            _row(2022020001, "NSH", "SJS", "HOME", 2022, "20221007", "all", 4, 1),  # inside target
        ]
        report = self._write_and_ingest(rows, target_seasons={20222023})
        self.assertEqual(report.rows_regular_season_target, 1)
        coverage = unique_game_coverage(self.conn, situation="all")
        self.assertEqual(coverage, {2022020001})


class TestResearchQueryApiStrictPriorGameDate(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()
        self.tmpdir = tempfile.mkdtemp()

    def _ingest(self, rows):
        path = os.path.join(self.tmpdir, "fixture.csv")
        write_csv(rows, path)
        provenance = make_provenance()
        mp_ingest.ingest_file(self.conn, path, provenance, provenance_type="ARCHIVAL_RESEARCH")

    def test_strict_prior_game_date_excludes_same_day_and_future(self):
        rows = [
            _row(2022020001, "NSH", "SJS", "HOME", 2022, "20221007", "all", 4, 1),
            _row(2022020002, "NSH", "BOS", "HOME", 2022, "20221008", "all", 2, 3),  # same day as target
            _row(2022020003, "NSH", "TOR", "HOME", 2022, "20221009", "all", 1, 0),  # future
        ]
        rows[1]["gameDate"] = "20221008"
        self._ingest(rows)

        eligible = team_stats_as_of(self.conn, "NSH", "2022-10-08", situation="all")
        eligible_game_ids = {r["game_id"] for r in eligible}
        self.assertEqual(eligible_game_ids, {2022020001})  # only strictly-earlier

    def test_future_game_is_excluded(self):
        rows = [
            _row(2022020001, "NSH", "SJS", "HOME", 2022, "20221007", "all", 4, 1),
            _row(2022020002, "NSH", "BOS", "HOME", 2022, "20221101", "all", 2, 3),
        ]
        self._ingest(rows)
        eligible = team_stats_as_of(self.conn, "NSH", "2022-10-08", situation="all")
        self.assertEqual({r["game_id"] for r in eligible}, {2022020001})

    def test_returns_only_matching_situation(self):
        rows = [
            _row(2022020001, "NSH", "SJS", "HOME", 2022, "20221007", "all", 4, 1),
            _row(2022020001, "NSH", "SJS", "HOME", 2022, "20221007", "5on5", 3, 1),
        ]
        self._ingest(rows)
        eligible_all = team_stats_as_of(self.conn, "NSH", "2022-10-08", situation="all")
        eligible_5on5 = team_stats_as_of(self.conn, "NSH", "2022-10-08", situation="5on5")
        self.assertEqual(len(eligible_all), 1)
        self.assertEqual(len(eligible_5on5), 1)
        self.assertEqual(eligible_all[0]["goals_for"], 4)
        self.assertEqual(eligible_5on5[0]["goals_for"], 3)

    def test_team_stats_for_game_is_an_identity_lookup_not_pit_gated(self):
        rows = [_row(2022020001, "NSH", "SJS", "HOME", 2022, "20221007", "all", 4, 1)]
        self._ingest(rows)
        row = team_stats_for_game(self.conn, 2022020001, "NSH", situation="all")
        self.assertIsNotNone(row)
        self.assertEqual(row["goals_for"], 4)
        missing = team_stats_for_game(self.conn, 9999999999, "NSH", situation="all")
        self.assertIsNone(missing)


class TestUniqueGameCoverage(unittest.TestCase):
    def test_coverage_counts_distinct_game_ids_not_rows(self):
        conn = make_db()
        tmpdir = tempfile.mkdtemp()
        rows = []
        for gid in (2022020001, 2022020002):
            for situation in ["all", "5on5"]:
                rows.append(_row(gid, "NSH", "SJS", "HOME", 2022, "20221007", situation, 4, 1))
        path = os.path.join(tmpdir, "fixture.csv")
        write_csv(rows, path)
        mp_ingest.ingest_file(conn, path, make_provenance(), provenance_type="ARCHIVAL_RESEARCH")
        coverage_all = unique_game_coverage(conn, situation="all")
        self.assertEqual(coverage_all, {2022020001, 2022020002})
        self.assertEqual(len(coverage_all), 2)


class TestProductionTablesUntouched(unittest.TestCase):
    def test_ingesting_moneypuck_data_never_touches_nhl_db_or_pit_tables(self):
        # this research db is a completely separate sqlite file/connection
        # from nhl.db -- assert the production tables simply don't exist
        # in this research schema at all, so no code path here could ever
        # write to them even by accident.
        conn = make_db()
        production_tables = {
            "games", "game_schedule_events", "game_result_events",
            "player_game_stats", "goalie_game_stats", "team_membership_events",
            "roster_status_events", "goalie_status_events", "odds_snapshots",
        }
        existing_tables = {
            r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        self.assertEqual(existing_tables & production_tables, set())
        self.assertIn("research_moneypuck_team_game_stats", existing_tables)


if __name__ == "__main__":
    unittest.main()
