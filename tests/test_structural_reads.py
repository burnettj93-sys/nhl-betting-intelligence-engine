"""
v2.1 spec item 18: features/point_in_time.py must be the ONLY module
permitted to issue a raw SELECT (or JOIN) against a mutable/bitemporal
table -- anything else reading roster/lineup/goalie/odds/schedule/stat
history directly risks reconstructing "what was known" from something
other than observed_at_utc. This test mechanically scans every .py
source file (excluding tests/, which construct scenarios directly by
design -- see tests/helpers.py's Fixture and features/point_in_time.py's
own module docstring) for SELECT/JOIN references to the restricted
tables, and fails loudly if one turns up anywhere not on the small,
individually-justified exception list below.

Detection deliberately requires a SELECT keyword within a bounded
distance before the FROM/JOIN + table-name match (not just the bare
table name anywhere in the file), so English-prose mentions of a table
name in a docstring/comment (e.g. run_slate.py's "every price here now
comes from odds_snapshots") don't produce false positives -- this is a
structural SQL-usage audit, not a string search.
"""
import pathlib
import re
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

RESTRICTED_TABLES = [
    "team_membership_events",
    "roster_status_events",
    "lineup_snapshots",
    "pp_unit_snapshots",
    "goalie_status_events",
    "odds_snapshots",
    "game_schedule_events",
    "player_game_stats",
    "goalie_game_stats",
    # v2.1.1a spec item 5: features/point_in_time.py's own module
    # docstring has named game_result_events as one of its exclusively-
    # owned restricted tables since v2.1.1 -- it was simply never added
    # here, leaving this audit unable to catch a future production module
    # bypassing game_result_as_of()/game_result_first_observed_at() with
    # a direct read of game result history.
    "game_result_events",
]

# directories/files entirely exempt from this audit.
EXEMPT_FILES = {"features/point_in_time.py"}
EXEMPT_DIR_PREFIXES = (
    # test scaffolding builds its own precise temporal scenarios by
    # writing directly to these tables -- that's expected and safe; this
    # test guards the PRODUCTION prediction/backtest/ingestion path, not
    # test fixtures deliberately constructing history to test against.
    "tests/",
)

# (relative file path, table name) pairs that read a restricted table
# directly outside features/point_in_time.py, each individually
# justified below. Anything found that is NOT on this list is a
# temporal-integrity bug (see the failure message for what to do).
JUSTIFIED_EXCEPTIONS = {
    # ingest/nhl_api.py's SELECTs are idempotency checks in the WRITE
    # path (spec item 5: "reingesting identical state stays idempotent")
    # -- they compare a candidate new row to the latest existing one to
    # decide whether to append a new event, never to answer "what was
    # known at time T" for a prediction. See ingest/nhl_api.py's module
    # docstring and _append_schedule_event_if_changed /
    # _append_player_stat_revision / _append_goalie_stat_revision.
    ("ingest/nhl_api.py", "team_membership_events"),
    ("ingest/nhl_api.py", "game_schedule_events"),
    ("ingest/nhl_api.py", "player_game_stats"),
    ("ingest/nhl_api.py", "goalie_game_stats"),
    # v2.1.1a: _append_result_revision_if_changed()'s SELECT is the same
    # write-path idempotency pattern as the other _append_*_if_changed
    # helpers above -- compares a candidate new result to the latest
    # existing revision to decide whether to append, never to answer
    # "what result was known at time T" for a prediction.
    ("ingest/nhl_api.py", "game_result_events"),
    # demo_setup.py / validate.py only ever run COUNT(*) diagnostics for
    # a human-facing summary printout at the end of a script -- never
    # feed a prediction, a backtest decision, or any point-in-time
    # reconstruction.
    ("demo_setup.py", "roster_status_events"),
    ("demo_setup.py", "odds_snapshots"),
    ("validate.py", "team_membership_events"),
    ("validate.py", "roster_status_events"),
    ("validate.py", "goalie_status_events"),
    ("validate.py", "lineup_snapshots"),
    ("validate.py", "odds_snapshots"),
    # v2.1.2a: validate_live_nhl.py's post-pull structural-sanity checks
    # (spec item 6) -- e.g. "did every finalized game's boxscore produce
    # non-empty player_game_stats/goalie_game_stats rows for both teams",
    # "does a FINAL game have a complete game_result_events row", "is
    # current-roster-sync idempotency reflected in a stable
    # team_membership_events row count" -- run only against a FRESH
    # TEMPORARY sqlite database created and destroyed within a single
    # run() call, purely for a human-facing PASS/FAIL smoke-test report.
    # Never used to answer "what was known at prediction_time_utc" for
    # any prediction, decision, or backtest -- this script never calls
    # into models/combined_model.py, pricing/engine.py, or backtest.py at
    # all.
    ("validate_live_nhl.py", "player_game_stats"),
    ("validate_live_nhl.py", "goalie_game_stats"),
    ("validate_live_nhl.py", "game_result_events"),
    ("validate_live_nhl.py", "team_membership_events"),
}

# a SELECT keyword must appear within this many characters before the
# FROM/JOIN + table match for it to count as a real SQL reference,
# rather than a docstring/comment that merely happens to say "from
# <table>" in English prose.
_MAX_SELECT_TO_FROM_DISTANCE = 400


def _find_direct_reads(py_path: pathlib.Path) -> set[str]:
    text = py_path.read_text()
    found = set()
    for table in RESTRICTED_TABLES:
        pattern = (rf"SELECT\b[\s\S]{{0,{_MAX_SELECT_TO_FROM_DISTANCE}}}?"
                   rf"\b(FROM|JOIN)\s+{table}\b")
        if re.search(pattern, text, re.IGNORECASE):
            found.add(table)
    return found


def _all_scanned_py_files():
    for py_path in sorted(REPO_ROOT.rglob("*.py")):
        rel = py_path.relative_to(REPO_ROOT).as_posix()
        if rel in EXEMPT_FILES or any(rel.startswith(p) for p in EXEMPT_DIR_PREFIXES):
            continue
        yield rel, py_path


class TestNoDirectReadsOutsidePointInTime(unittest.TestCase):
    def test_only_point_in_time_py_and_justified_exceptions_read_restricted_tables(self):
        violations = []
        for rel, py_path in _all_scanned_py_files():
            for table in _find_direct_reads(py_path):
                if (rel, table) not in JUSTIFIED_EXCEPTIONS:
                    violations.append((rel, table))
        self.assertEqual(
            violations, [],
            f"direct, unjustified SELECT/JOIN of a restricted temporal table found "
            f"outside features/point_in_time.py: {violations}. Either route this "
            f"through features/point_in_time.py, or add a specifically-justified "
            f"entry to JUSTIFIED_EXCEPTIONS in this test with a comment explaining "
            f"why it's safe (it must never be used to answer 'what was known at "
            f"prediction_time_utc').")

    def test_every_justified_exception_still_actually_exists_in_source(self):
        # catches a stale exception entry (e.g. after a refactor moved the
        # read elsewhere or removed it) so the exception list stays an
        # accurate, trustworthy audit trail rather than silently growing
        # permissive over time.
        for rel, table in JUSTIFIED_EXCEPTIONS:
            py_path = REPO_ROOT / rel
            self.assertTrue(py_path.exists(), f"{rel} no longer exists")
            self.assertIn(
                table, _find_direct_reads(py_path),
                f"{rel} no longer reads {table} directly -- remove this stale "
                f"entry from JUSTIFIED_EXCEPTIONS")

    def test_no_exception_is_granted_to_a_predictive_or_backtest_module(self):
        # belt-and-suspenders: the exception list itself must never cover
        # the actual prediction/decision/backtest path -- only ingestion
        # (write-path idempotency) and reporting scripts are allowed on it.
        forbidden_modules = {"models/combined_model.py", "pricing/engine.py",
                             "pricing/decision.py", "backtest.py",
                             "features/feature_engine.py"}
        exempted_files = {rel for rel, _table in JUSTIFIED_EXCEPTIONS}
        self.assertEqual(exempted_files & forbidden_modules, set())

    def test_run_slate_py_prose_mention_of_odds_snapshots_is_not_a_false_positive(self):
        # regression guard for the detector itself: run_slate.py's
        # docstring says "every price here now comes from odds_snapshots"
        # in English, with no SELECT nearby -- must not be flagged.
        run_slate = REPO_ROOT / "run_slate.py"
        self.assertTrue(run_slate.exists())
        self.assertNotIn("odds_snapshots", _find_direct_reads(run_slate))

    def test_restricted_table_list_matches_point_in_time_pys_own_docstring(self):
        pit_text = (REPO_ROOT / "features" / "point_in_time.py").read_text()
        for table in RESTRICTED_TABLES:
            self.assertIn(
                table, pit_text,
                f"{table} is in RESTRICTED_TABLES here but not mentioned in "
                f"features/point_in_time.py's own docstring of what it "
                f"exclusively owns -- keep these two lists in sync")


class TestGameResultEventsIsNowGuardedByThisAudit(unittest.TestCase):
    """v2.1.1a spec item 5: game_result_events was named in
    features/point_in_time.py's own docstring as one of its exclusively-
    owned restricted tables since v2.1.1, but was never actually added to
    RESTRICTED_TABLES here -- so a future production module could read
    result history directly and this audit would never notice. Proves
    the gap is closed: a fake production module reading game_result_events
    outside the sanctioned PIT/exception path is caught."""

    def test_a_fake_production_module_reading_game_result_events_is_flagged(self):
        import tempfile

        fake_source = (
            "def leak_result(conn, game_id):\n"
            "    return conn.execute(\n"
            "        'SELECT home_score, away_score FROM game_result_events '\n"
            "        'WHERE game_id=?', (game_id,)\n"
            "    ).fetchone()\n"
        )
        with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False) as f:
            f.write(fake_source)
            fake_path = pathlib.Path(f.name)
        try:
            found = _find_direct_reads(fake_path)
            self.assertIn("game_result_events", found)
            # and it would NOT be excused by the current exception list --
            # a fake path never matches a real (file, table) entry.
            fake_rel = "some_new_module.py"
            self.assertNotIn((fake_rel, "game_result_events"), JUSTIFIED_EXCEPTIONS)
        finally:
            fake_path.unlink(missing_ok=True)

    def test_only_the_two_justified_readers_are_exempted_for_game_result_events(self):
        # v2.1.2a: validate_live_nhl.py's post-pull structural-sanity check
        # (spec item 6) is a second, separately-justified exception (see
        # JUSTIFIED_EXCEPTIONS's comment) -- a diagnostic-only read against
        # a fresh temporary DB, never touching the prediction/backtest
        # path. Still belt-and-suspenders against a THIRD, unreviewed
        # reader quietly showing up.
        exceptions_for_this_table = {rel for rel, table in JUSTIFIED_EXCEPTIONS
                                      if table == "game_result_events"}
        self.assertEqual(exceptions_for_this_table,
                          {"ingest/nhl_api.py", "validate_live_nhl.py"})


if __name__ == "__main__":
    unittest.main()
