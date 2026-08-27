"""
Tests for the synthetic dataset generator (ingest/demo_data.py) — the
honesty fixes called out in its own docstring: full determinism, the
one-game-per-team-per-date scheduling invariant, OT/SO games always having
exactly one winner, and a player being able to get hurt, recover, and get
hurt again (the recur-after-recovery bug fix).

Runs a small synthetic season rather than the full multi-season default, to
keep this fast — the generator's behavior doesn't depend on season count.
"""
import collections
import datetime as dt
import hashlib
import unittest

from ingest import demo_data
from tests.helpers import make_test_db


SMALL_SEASONS = [("2025-DEMO", dt.date(2025, 10, 1))]


def _dump_db(path) -> bytes:
    with open(path, "rb") as f:
        return f.read()


class TestDeterminism(unittest.TestCase):
    def test_same_seed_produces_identical_database(self):
        conn1, path1 = make_test_db()
        demo_data.generate(conn1, seasons=SMALL_SEASONS, seed=42, upcoming_scheduled_games=2)
        conn1.close()

        conn2, path2 = make_test_db()
        demo_data.generate(conn2, seasons=SMALL_SEASONS, seed=42, upcoming_scheduled_games=2)
        conn2.close()

        # Compare logical content (row-by-row) rather than raw bytes, since
        # SQLite file layout isn't guaranteed byte-identical even for
        # identical logical content (e.g. vacuum/page-order nondeterminism).
        def snapshot(path):
            import sqlite3
            c = sqlite3.connect(path)
            c.row_factory = sqlite3.Row
            out = {}
            for table in ("teams", "players", "team_membership_events", "games",
                          "roster_status_events", "goalie_status_events",
                          "lineup_snapshots", "player_game_stats", "goalie_game_stats",
                          "odds_snapshots"):
                rows = c.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
                out[table] = [tuple(r) for r in rows]
            c.close()
            return out

        snap1 = snapshot(path1)
        snap2 = snapshot(path2)
        path1.unlink(missing_ok=True)
        path2.unlink(missing_ok=True)

        self.assertEqual(snap1.keys(), snap2.keys())
        for table in snap1:
            self.assertEqual(snap1[table], snap2[table], f"table {table} differs between identical-seed runs")

    def test_different_seed_produces_different_results(self):
        conn1, path1 = make_test_db()
        demo_data.generate(conn1, seasons=SMALL_SEASONS, seed=42, upcoming_scheduled_games=2)
        scores1 = [tuple(r) for r in conn1.execute(
            "SELECT game_id, home_score, away_score FROM games WHERE game_state='FINAL' ORDER BY game_id"
        )]
        conn1.close()
        path1.unlink(missing_ok=True)

        conn2, path2 = make_test_db()
        demo_data.generate(conn2, seasons=SMALL_SEASONS, seed=43, upcoming_scheduled_games=2)
        scores2 = [tuple(r) for r in conn2.execute(
            "SELECT game_id, home_score, away_score FROM games WHERE game_state='FINAL' ORDER BY game_id"
        )]
        conn2.close()
        path2.unlink(missing_ok=True)

        self.assertNotEqual(scores1, scores2)


class TestScheduleInvariants(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn, cls.path = make_test_db()
        demo_data.generate(cls.conn, seasons=SMALL_SEASONS, seed=7, upcoming_scheduled_games=4)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.path.unlink(missing_ok=True)

    def test_no_team_plays_twice_on_the_same_date(self):
        rows = self.conn.execute(
            "SELECT game_date, home_team, away_team FROM games WHERE game_state='FINAL'"
        ).fetchall()
        per_date = collections.defaultdict(list)
        for r in rows:
            per_date[r["game_date"]].append(r["home_team"])
            per_date[r["game_date"]].append(r["away_team"])
        for date, teams_playing in per_date.items():
            self.assertEqual(len(teams_playing), len(set(teams_playing)),
                              f"a team appears twice on {date}: {teams_playing}")

    def test_games_per_team_matches_documented_note(self):
        expected_games = demo_data.GAMES_PER_TEAM_PER_SEASON
        counts = collections.Counter()
        rows = self.conn.execute(
            "SELECT home_team, away_team FROM games WHERE game_state='FINAL'"
        ).fetchall()
        for r in rows:
            counts[r["home_team"]] += 1
            counts[r["away_team"]] += 1
        for team in demo_data.TEAMS:
            self.assertEqual(counts[team], expected_games,
                              f"{team} played {counts[team]} games, expected {expected_games}")

    def test_season_games_note_is_accurate(self):
        self.assertIn(str(demo_data.GAMES_PER_TEAM_PER_SEASON), demo_data.SEASON_GAMES_NOTE)
        self.assertIn("NOT a full 82-game", demo_data.SEASON_GAMES_NOTE)


class TestOTAlwaysHasAWinner(unittest.TestCase):
    def test_every_final_game_has_a_distinct_score(self):
        conn, path = make_test_db()
        demo_data.generate(conn, seasons=SMALL_SEASONS, seed=99, upcoming_scheduled_games=0)
        rows = conn.execute(
            "SELECT game_id, home_score, away_score, final_period_type FROM games WHERE game_state='FINAL'"
        ).fetchall()
        conn.close()
        path.unlink(missing_ok=True)

        self.assertGreater(len(rows), 0)
        for r in rows:
            self.assertNotEqual(r["home_score"], r["away_score"],
                                 f"game {r['game_id']} ({r['final_period_type']}) tied "
                                 f"{r['home_score']}-{r['away_score']}")
        ot_so_games = [r for r in rows if r["final_period_type"] in ("OT", "SO")]
        # with ~23% OT rate over a full round-robin this should be non-empty;
        # if it's ever empty the test is at least still valid (checked above)
        for r in ot_so_games:
            self.assertEqual(abs(r["home_score"] - r["away_score"]), 1)


class TestInjuryRecurrence(unittest.TestCase):
    def test_a_player_can_be_injured_recover_and_be_injured_again(self):
        # run a full multi-season generation (higher game count -> higher
        # chance of catching the recur-after-recovery bug) with a seed
        # chosen because it's known to produce at least one such player at
        # this game count; if this ever goes flaky, widen the seasons list.
        conn, path = make_test_db()
        seasons = [("2025-DEMO", dt.date(2025, 10, 1)), ("2026-DEMO", dt.date(2026, 10, 1))]
        demo_data.generate(conn, seasons=seasons, seed=42, upcoming_scheduled_games=0)

        rows = conn.execute(
            """SELECT player_id, status, effective_at_utc FROM roster_status_events
               ORDER BY player_id, effective_at_utc"""
        ).fetchall()
        conn.close()
        path.unlink(missing_ok=True)

        per_player = collections.defaultdict(list)
        for r in rows:
            per_player[r["player_id"]].append(r["status"])

        players_with_multiple_out_events = [
            pid for pid, statuses in per_player.items() if statuses.count("OUT") >= 2
        ]
        self.assertGreater(
            len(players_with_multiple_out_events), 0,
            "no player had >=2 separate OUT events across two seasons -- "
            "the recur-after-recovery path may not be firing"
        )
        # and for at least one such player, statuses must alternate
        # OUT/ACTIVE/OUT (not two OUTs in a row, which would mean the
        # ACTIVE recovery row never got inserted between them)
        found_alternating = False
        for pid in players_with_multiple_out_events:
            statuses = per_player[pid]
            first_out = statuses.index("OUT")
            second_out = statuses.index("OUT", first_out + 1)
            if "ACTIVE" in statuses[first_out + 1:second_out]:
                found_alternating = True
                break
        self.assertTrue(found_alternating,
                         "expected OUT -> ACTIVE -> OUT for at least one player")


class TestGeneratorPopulatesFullTemporalSchema(unittest.TestCase):
    """A lighter smoke check that generate() actually exercises every table
    the point-in-time layer reads from, not just `games`."""

    @classmethod
    def setUpClass(cls):
        cls.conn, cls.path = make_test_db()
        demo_data.generate(cls.conn, seasons=SMALL_SEASONS, seed=11, upcoming_scheduled_games=3)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.path.unlink(missing_ok=True)

    def test_all_temporal_tables_have_rows(self):
        for table in ("team_membership_events", "goalie_status_events",
                      "lineup_snapshots", "odds_snapshots", "player_game_stats",
                      "goalie_game_stats"):
            count = self.conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            self.assertGreater(count, 0, f"{table} has no rows")

    def test_upcoming_scheduled_games_are_incomplete_by_design(self):
        rows = self.conn.execute(
            "SELECT game_id FROM games WHERE game_state='SCHEDULED'"
        ).fetchall()
        self.assertEqual(len(rows), 3)
        for r in rows:
            confirmed = self.conn.execute(
                "SELECT COUNT(*) AS n FROM goalie_status_events WHERE game_id=? AND status='CONFIRMED'",
                (r["game_id"],),
            ).fetchone()["n"]
            self.assertEqual(confirmed, 0, "an upcoming game should have no CONFIRMED goalie yet")


if __name__ == "__main__":
    unittest.main()
