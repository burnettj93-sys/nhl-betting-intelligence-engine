"""
v2.1.1 spec item 6: before any live NHL API validation begins, every
incoming timestamp representation must normalize to one canonical UTC
string -- see ingest/timestamps.py's module docstring for the full
rationale (point-in-time eligibility depends entirely on timestamp
ordering/equality, and a real NHL API response mixes "Z"-suffixed
timestamps with whatever a manually-fed news/roster source supplies).

These tests prove: (1) the exact three equivalent-instant examples named
in the spec normalize to the identical string, (2) a handful of other
supported forms behave correctly, (3) normalization is idempotent and a
true no-op on strings already in canonical form (so it can never disturb
the 200+ pre-existing tests that construct raw naive timestamps directly
via tests/helpers.py::t()), (4) malformed input fails loudly rather than
silently persisting a bad string, and (5) ingest/nhl_api.py's real write
paths actually route incoming timestamps through the normalizer before
persistence -- not just that the utility function exists in isolation.
"""
import unittest

from ingest.nhl_api import ingest_result, ingest_schedule
from ingest.timestamps import UnsupportedTimestampError, normalize_utc_timestamp
from tests.helpers import make_test_db, t


class TestEquivalentInstantsNormalizeIdentically(unittest.TestCase):
    """The exact spec scenario: these three strings denote the same
    instant and MUST normalize to the identical canonical string."""

    def test_the_three_spec_examples_are_the_same_instant_after_normalization(self):
        z_form = normalize_utc_timestamp("2026-10-01T23:00:00Z")
        offset_form = normalize_utc_timestamp("2026-10-01T19:00:00-04:00")
        explicit_utc_offset_form = normalize_utc_timestamp("2026-10-01T23:00:00+00:00")
        self.assertEqual(z_form, offset_form)
        self.assertEqual(offset_form, explicit_utc_offset_form)
        self.assertEqual(z_form, "2026-10-01T23:00:00")

    def test_lowercase_z_suffix_also_normalizes_correctly(self):
        self.assertEqual(normalize_utc_timestamp("2026-10-01T23:00:00z"),
                          "2026-10-01T23:00:00")

    def test_positive_offset_normalizes_correctly(self):
        # +05:30 (e.g. IST) -- UTC is earlier than local wall-clock time.
        self.assertEqual(normalize_utc_timestamp("2026-10-02T04:30:00+05:30"),
                          "2026-10-01T23:00:00")

    def test_offset_that_crosses_a_day_boundary_normalizes_correctly(self):
        # -04:00 late at night rolls over into the next UTC calendar day.
        self.assertEqual(normalize_utc_timestamp("2026-10-01T21:00:00-04:00"),
                          "2026-10-02T01:00:00")


class TestNaiveInputIsTreatedAsAlreadyUtc(unittest.TestCase):
    """This codebase's documented convention (see ingest/timestamps.py's
    module docstring): a naive timestamp IS a UTC instant, never a local
    wall-clock time to be guessed at."""

    def test_naive_timestamp_passes_through_unchanged(self):
        self.assertEqual(normalize_utc_timestamp("2026-10-01T23:00:00"),
                          "2026-10-01T23:00:00")

    def test_naive_timestamp_is_the_same_instant_as_its_z_suffixed_form(self):
        self.assertEqual(normalize_utc_timestamp("2026-10-01T23:00:00"),
                          normalize_utc_timestamp("2026-10-01T23:00:00Z"))


class TestNormalizationIsIdempotentAndBackwardCompatible(unittest.TestCase):
    """Every one of the 200+ pre-existing tests constructs raw naive
    timestamps via tests/helpers.py::t() and writes/compares them
    directly. Normalization must be a true no-op on that exact form, or
    every one of those tests (and every already-stored timestamp
    throughout this codebase) would silently change shape."""

    def test_t_helper_output_is_already_canonical(self):
        for args in ((0,), (10, {"hour": 19}), (5, {"hour": 22, "minute": 15})):
            offset = args[0]
            kwargs = args[1] if len(args) > 1 else {}
            raw = t(offset, **kwargs)
            self.assertEqual(normalize_utc_timestamp(raw), raw)

    def test_normalizing_twice_is_the_same_as_normalizing_once(self):
        once = normalize_utc_timestamp("2026-10-01T19:00:00-04:00")
        twice = normalize_utc_timestamp(once)
        self.assertEqual(once, twice)

    def test_microseconds_are_preserved_when_present(self):
        self.assertEqual(normalize_utc_timestamp("2026-10-01T23:00:00.500000Z"),
                          "2026-10-01T23:00:00.500000")

    def test_none_passes_through_as_none(self):
        # several callers (e.g. record_roster_status's expected_return_at)
        # pass an optional, legitimately-absent timestamp.
        self.assertIsNone(normalize_utc_timestamp(None))


class TestMalformedInputFailsLoudly(unittest.TestCase):
    def test_garbage_string_raises(self):
        with self.assertRaises(UnsupportedTimestampError):
            normalize_utc_timestamp("not a timestamp")

    def test_empty_string_raises(self):
        with self.assertRaises(UnsupportedTimestampError):
            normalize_utc_timestamp("")

    def test_bare_date_with_no_time_is_treated_as_midnight_utc(self):
        # dt.datetime.fromisoformat() legitimately accepts a bare date;
        # this is not malformed input, just a coarse-grained one -- it
        # normalizes like any other naive (already-UTC-by-convention)
        # timestamp rather than being rejected.
        self.assertEqual(normalize_utc_timestamp("2026-10-01"), "2026-10-01T00:00:00")


class TestIngestionPathsActuallyNormalizeBeforePersisting(unittest.TestCase):
    """Not just that the utility exists -- prove ingest/nhl_api.py's real
    write paths route every incoming timestamp through it, so a live NHL
    API pull (which returns "Z"-suffixed timestamps) can never write a
    representation that disagrees with the rest of the database."""

    def setUp(self):
        self.conn, self.path = make_test_db()
        self.conn.execute("INSERT OR IGNORE INTO teams (team_id) VALUES ('TOR')")
        self.conn.execute("INSERT OR IGNORE INTO teams (team_id) VALUES ('BOS')")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def _game(self, game_id=555001, start="2026-10-01T23:00:00Z"):
        return {
            "id": game_id, "season": 20260001,
            "homeTeam": {"abbrev": "TOR"}, "awayTeam": {"abbrev": "BOS"},
            "gameDate": "2026-10-01", "startTimeUTC": start,
            "venue": {"default": "Scotiabank Arena"},
        }

    def test_ingest_schedule_stores_the_canonical_form_of_a_z_suffixed_start_time(self):
        ingest_schedule(self.conn, self._game(), observed_at_utc="2026-09-30T12:00:00Z")
        self.conn.commit()
        row = self.conn.execute(
            "SELECT scheduled_start_utc, schedule_observed_at_utc FROM games WHERE game_id=?",
            (555001,),
        ).fetchone()
        self.assertEqual(row["scheduled_start_utc"], "2026-10-01T23:00:00")
        self.assertEqual(row["schedule_observed_at_utc"], "2026-09-30T12:00:00")

    def test_ingest_schedule_stores_the_same_instant_regardless_of_incoming_representation(self):
        ingest_schedule(self.conn, self._game(game_id=555002, start="2026-10-01T19:00:00-04:00"),
                         observed_at_utc="2026-09-30T08:00:00-04:00")
        self.conn.commit()
        row = self.conn.execute(
            "SELECT scheduled_start_utc, schedule_observed_at_utc FROM games WHERE game_id=?",
            (555002,),
        ).fetchone()
        # -04:00 forms must normalize to the SAME canonical strings the
        # Z-suffixed test above produced for the equivalent instants.
        self.assertEqual(row["scheduled_start_utc"], "2026-10-01T23:00:00")
        self.assertEqual(row["schedule_observed_at_utc"], "2026-09-30T12:00:00")

    def test_ingest_result_stores_the_canonical_form_of_a_z_suffixed_observed_at(self):
        ingest_schedule(self.conn, self._game(game_id=555003), observed_at_utc="2026-09-30T12:00:00Z")
        self.conn.commit()
        finished = self._game(game_id=555003)
        finished["homeTeam"]["score"] = 4
        finished["awayTeam"]["score"] = 2
        finished["periodDescriptor"] = {"periodType": "REG"}
        ingest_result(self.conn, finished, observed_at_utc="2026-10-02T02:15:00Z")
        self.conn.commit()
        row = self.conn.execute(
            "SELECT result_observed_at_utc FROM games WHERE game_id=?", (555003,),
        ).fetchone()
        self.assertEqual(row["result_observed_at_utc"], "2026-10-02T02:15:00")
        event_row = self.conn.execute(
            "SELECT observed_at_utc FROM game_result_events WHERE game_id=?", (555003,),
        ).fetchone()
        self.assertEqual(event_row["observed_at_utc"], "2026-10-02T02:15:00")


if __name__ == "__main__":
    unittest.main()
