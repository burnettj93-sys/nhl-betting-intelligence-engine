"""
v2.1.2 spec item 2: run_slate.py and backtest.py used to import
`from ingest.demo_data import TEAMS` and use it as the default/only model
team universe. `demo_data.TEAMS` is the synthetic 12-team demo league --
a real database containing EDM, COL, VAN, VGK, CAR, DAL etc. would never
be reflected in it, so Elo/player/model-state initialization against a
real NHL database could silently exclude real teams or behave in ways
never exercised against anything but the demo league.

The production team universe now comes from the database (db.team_ids()
-- SELECT team_id FROM teams), and run_slate.py / backtest.py both use it
as their default rather than any hardcoded synthetic list. This file
proves (a) the helper itself, (b) that model-state reconstruction and the
run_slate/backtest production paths work correctly for teams OUTSIDE the
synthetic demo list, and (c) mechanically, that no production module
still imports/uses ingest.demo_data.TEAMS at all (structural regression
guard, same style as tests/test_structural_reads.py).
"""
import ast
import datetime as dt
import pathlib
import unittest

import backtest
import db
import run_slate
from models.combined_model import CombinedMoneylineModel, build_model_state_as_of
from tests.helpers import make_test_db, t

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# files allowed to reference ingest.demo_data.TEAMS -- explicitly
# synthetic-only scaffolding, never a production prediction/pricing/
# backtest path.
EXEMPT_FILES = {
    "ingest/demo_data.py",   # defines it
    "demo_setup.py",         # explicit synthetic-data bootstrap script
}
EXEMPT_DIR_PREFIXES = ("tests/",)   # tests deliberately exercise demo data directly

def _references_demo_data_teams(py_path: pathlib.Path) -> bool:
    """AST-based (not text/regex) detection: real code usage only --
    `from ingest.demo_data import TEAMS`-style imports and `demo_data.TEAMS`
    attribute access -- never a docstring/comment merely mentioning the
    name in English prose (this module's own module docstring, and
    db.py's/run_slate.py's/backtest.py's docstrings and comments, all
    legitimately SAY "ingest.demo_data.TEAMS" while explaining what NOT
    to use -- a plain-text/regex scan would misfire on exactly those and
    was the first draft of this check; see git history if curious)."""
    try:
        tree = ast.parse(py_path.read_text())
    except SyntaxError:   # pragma: no cover
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and node.module.endswith("demo_data") and any(
                    alias.name == "TEAMS" for alias in node.names):
                return True
        if isinstance(node, ast.Attribute) and node.attr == "TEAMS":
            if isinstance(node.value, ast.Name) and node.value.id == "demo_data":
                return True
    return False


# production modules that must never be exempted, belt-and-suspenders
# (same idea as test_structural_reads.py's forbidden_modules check).
FORBIDDEN_MODULES = {
    "run_slate.py", "backtest.py", "models/combined_model.py",
    "pricing/engine.py", "pricing/decision.py", "validate_live_nhl.py",
}


def _insert_game(conn, game_id, home, away, scheduled_start, schedule_observed_at,
                  season="2025-DEMO"):
    game_date = scheduled_start[:10]
    conn.execute("INSERT OR IGNORE INTO teams (team_id) VALUES (?)", (home,))
    conn.execute("INSERT OR IGNORE INTO teams (team_id) VALUES (?)", (away,))
    conn.execute(
        """INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team,
                               away_team, venue, schedule_observed_at_utc, game_state, source)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (game_id, season, game_date, scheduled_start, home, away, "Arena",
         schedule_observed_at, "SCHEDULED", "test"),
    )
    conn.execute(
        """INSERT INTO game_schedule_events
           (game_id, game_date, scheduled_start_utc, home_team, away_team, venue,
            effective_at_utc, observed_at_utc, source, data_provider)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (game_id, game_date, scheduled_start, home, away, "Arena",
         schedule_observed_at, schedule_observed_at, "test", "test"),
    )
    conn.commit()


def _finalize(conn, game_id, home_score, away_score, result_observed_at):
    conn.execute(
        """UPDATE games SET home_score=?, away_score=?, final_period_type='REG',
                             game_state='FINAL', result_observed_at_utc=? WHERE game_id=?""",
        (home_score, away_score, result_observed_at, game_id),
    )
    conn.execute(
        """INSERT INTO game_result_events
           (game_id, home_score, away_score, final_period_type, game_state,
            effective_at_utc, observed_at_utc, revision_number, source, data_provider)
           VALUES (?,?,?,'REG','FINAL',?,?,1,?,?)""",
        (game_id, home_score, away_score, result_observed_at, result_observed_at, "test", "test"),
    )
    conn.commit()


class TestTeamIdsHelper(unittest.TestCase):
    def setUp(self):
        self.conn, self.path = make_test_db()

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_derives_team_universe_from_the_teams_table(self):
        for tid in ("EDM", "VGK", "COL"):
            self.conn.execute("INSERT INTO teams (team_id) VALUES (?)", (tid,))
        self.conn.commit()
        self.assertEqual(db.team_ids(self.conn), ["COL", "EDM", "VGK"])   # sorted

    def test_empty_db_returns_empty_list_not_a_synthetic_default(self):
        self.assertEqual(db.team_ids(self.conn), [])


class TestNonDemoTeamsCanBeModeled(unittest.TestCase):
    """EDM/VGK -- teams outside ingest.demo_data.TEAMS entirely -- must
    work through the real production paths without the caller manually
    passing a synthetic team list."""

    def setUp(self):
        self.conn, self.path = make_test_db()
        _insert_game(self.conn, 1, "EDM", "VGK",
                      scheduled_start=t(10, hour=19), schedule_observed_at=t(-30))
        _insert_game(self.conn, 2, "VGK", "EDM",
                      scheduled_start=t(12, hour=19), schedule_observed_at=t(-30))
        _finalize(self.conn, 1, home_score=4, away_score=2, result_observed_at=t(10, hour=22))
        _finalize(self.conn, 2, home_score=3, away_score=1, result_observed_at=t(12, hour=22))

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_build_model_state_as_of_works_with_db_derived_teams(self):
        teams = db.team_ids(self.conn)
        self.assertEqual(set(teams), {"EDM", "VGK"})
        model = build_model_state_as_of(self.conn, t(11), teams)
        self.assertIn("EDM", model.elo.ratings)
        self.assertIn("VGK", model.elo.ratings)

    def test_run_slate_build_prediction_for_game_works_without_passing_teams(self):
        # the production default (teams=None -> db.team_ids(conn)) must
        # work end to end for non-demo teams, with no caller-supplied
        # team list and no import of ingest.demo_data.TEAMS anywhere in
        # this test file's call path.
        pred = run_slate.build_prediction_for_game(self.conn, 2)
        self.assertIn(pred.home_team, {"EDM", "VGK"})
        self.assertIn(pred.away_team, {"EDM", "VGK"})

    def test_backtest_run_works_for_a_db_containing_only_non_demo_teams(self):
        results = backtest.run(self.conn)
        self.assertEqual(results["n_games"], 2)
        self.assertIn("combined_model", results)


class TestProductionModulesDoNotDependOnDemoDataTeams(unittest.TestCase):
    """Structural regression guard: no production module may import or
    reference ingest.demo_data.TEAMS -- that's the synthetic demo league
    only, never the production team universe."""

    def test_no_production_file_references_demo_data_teams(self):
        violations = []
        for py_path in sorted(REPO_ROOT.rglob("*.py")):
            rel = py_path.relative_to(REPO_ROOT).as_posix()
            if rel in EXEMPT_FILES or any(rel.startswith(p) for p in EXEMPT_DIR_PREFIXES):
                continue
            if _references_demo_data_teams(py_path):
                violations.append(rel)
        self.assertEqual(
            violations, [],
            f"production file(s) referencing ingest.demo_data.TEAMS: {violations} -- "
            f"the production team universe must come from db.team_ids(conn), never the "
            f"synthetic demo list. If this is genuinely a new demo-only script, add it "
            f"to EXEMPT_FILES here with a comment explaining why.")

    def test_forbidden_modules_are_never_accidentally_exempted(self):
        self.assertEqual(EXEMPT_FILES & FORBIDDEN_MODULES, set())


if __name__ == "__main__":
    unittest.main()
