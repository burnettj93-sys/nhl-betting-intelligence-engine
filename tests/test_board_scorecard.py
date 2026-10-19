"""Board scorecard (operational/board_scorecard.py): frozen pregame cards graded against official results, read-only, idempotent, and honest about small samples."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from operational import board_scorecard as bs


def _db():
    c = sqlite3.connect(":memory:")
    c.executescript("""
        create table games(game_id integer primary key, game_state text, home_team text, away_team text, home_score integer, away_score integer);
        create table player_game_stats(game_id integer, player_id integer, goals integer, shots integer, played integer, revision_number integer);
        create table goalie_game_stats(game_id integer, player_id integer, started integer, shots_against integer, saves integer, revision_number integer);
    """)
    return c


def _card(gid="2026020135"):
    return {"game_id": gid, "away": "CBJ", "home": "MIN", "_stored_at_utc": "2026-10-18T21:56:18Z",
            "scorers": [{"player_id": 1, "name": "Scores", "probability": 0.40, "price": {"implied": 0.5, "quote_updated_utc": "2026-10-18T20:38:54Z"}},
                        {"player_id": 2, "name": "Blanked", "probability": 0.30, "price": {}},
                        {"player_id": 3, "name": "Scratched", "probability": 0.25, "price": {}}],
            "shots": {"picks": [{"player_id": 4, "name": "Shooter", "chosen": {"threshold": 2, "american": -175.0, "model": 0.63}, "quote_updated_utc": "2026-10-18T20:38:54Z"}]},
            "goalies": [{"starter": {"player_id": 9, "name": "Starter"}, "projection": {"expected_saves": 25.5}, "line": {"line": 23.5, "quote_updated_utc": "2026-10-18T20:38:54Z"}}],
            "side": {"sides": {"home": {"model": 0.6364, "no_vig": 0.6309, "quote_captured_at_utc": "2026-10-18T20:39:03Z"}, "away": {"model": 0.3636}}}}


def _final(conn, gid=2026020135, home=2, away=1):
    conn.execute("insert into games values(?, 'FINAL', 'MIN', 'CBJ', ?, ?)", (gid, home, away))
    conn.executemany("insert into player_game_stats values(?,?,?,?,?,?)", [(gid, 1, 1, 5, 1, 1), (gid, 2, 0, 3, 1, 1), (gid, 4, 0, 2, 1, 1)])   # player 3 has no row: did not play
    conn.executemany("insert into goalie_game_stats values(?,?,?,?,?,?)", [(gid, 9, 0, 14, 13, 1), (gid, 9, 1, 14, 13, 2)])                        # revision 2 is the one that counts


class GradeCard(unittest.TestCase):
    def test_grades_every_market_from_the_frozen_card(self):
        conn = _db()
        _final(conn)
        rows = {r["market"] + ":" + str(r.get("player")): r for r in bs.grade_card(_card(), conn)}
        self.assertEqual(rows["ANYTIME_GOAL:Scores"]["y"], 1)
        self.assertEqual(rows["ANYTIME_GOAL:Blanked"]["y"], 0)
        self.assertEqual(rows["ANYTIME_GOAL:Scratched"]["excluded"], "DID_NOT_PLAY")
        self.assertEqual(rows["SHOTS_2PLUS:Shooter"]["y"], 1)                       # 2 shots meets a 2+ pick
        self.assertAlmostEqual(rows["SHOTS_2PLUS:Shooter"]["market_p"], 175 / 275, places=4)
        s = rows["GOALIE_SAVES_LINE:Starter"]
        self.assertEqual((s["actual_saves"], s["went"], s["model_side"]), (13, "UNDER", "OVER"))
        self.assertEqual(rows["MONEYLINE_HOME:None"]["y"], 1)

    def test_a_game_that_is_not_final_is_not_graded(self):
        conn = _db()
        conn.execute("insert into games values(2026020135, 'LIVE', 'MIN', 'CBJ', 1, 0)")
        self.assertEqual(bs.grade_card(_card(), conn), [])

    def test_a_goalie_who_did_not_start_is_excluded_not_graded(self):
        conn = _db()
        _final(conn)
        conn.execute("delete from goalie_game_stats")
        conn.execute("insert into goalie_game_stats values(2026020135, 9, 0, 0, 0, 1)")
        s = [r for r in bs.grade_card(_card(), conn) if r["market"] == "GOALIE_SAVES_LINE"][0]
        self.assertEqual(s["excluded"], "CARD_STARTER_DID_NOT_START")


class ScoreFinalGames(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / bs.PREGAME_STATE).write_text(json.dumps({"2026-10-18": {"2026020135": _card()}, "_recovery_attempted": {"2026-10-17": True}}))
        patcher = mock.patch.object(bs.state_paths, "path", side_effect=lambda name: root / name)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.root = root

    def test_idempotent_and_never_revises_a_graded_selection(self):
        conn = _db()
        _final(conn)
        first = bs.score_final_games(conn)
        self.assertEqual(first["newly_graded"], 6)                                  # 3 scorers + 1 shots + saves + moneyline
        conn.execute("update player_game_stats set goals=0 where player_id=1")      # a later (wrong) change must not rewrite the graded row
        again = bs.score_final_games(conn)
        self.assertEqual(again["newly_graded"], 0)
        saved = json.loads((self.root / bs.OUT).read_text())["rows"]
        self.assertEqual(saved["2026020135:goal:1"]["y"], 1)

    def test_only_the_scorecard_file_is_written(self):
        conn = _db()
        _final(conn)
        bs.score_final_games(conn)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), sorted([bs.PREGAME_STATE, bs.OUT]))

    def test_small_samples_are_labelled_too_few(self):
        conn = _db()
        _final(conn)
        bs.score_final_games(conn)
        summary = bs.summarize()
        self.assertTrue(summary["ANYTIME_GOAL"]["reading"].startswith("TOO_FEW"))
        self.assertEqual(summary["ANYTIME_GOAL"]["excluded_did_not_play_or_start"], 1)
        self.assertEqual(summary["GOALIE_SAVES_LINE"]["mean_abs_error_saves"], 12.5)
        self.assertEqual(summary["GOALIE_SAVES_LINE"]["model_side_right"], "0 of 1")

    def test_a_brier_score_is_computed_against_the_market_on_the_same_rows(self):
        rows = {f"k{i}": {"market": "ANYTIME_GOAL", "p": 0.4, "y": i % 2, "market_p": 0.5} for i in range(40)}
        out = bs.summarize(rows)["ANYTIME_GOAL"]
        self.assertEqual(out["reading"], "ENOUGH_FOR_A_FIRST_READING")
        self.assertAlmostEqual(out["model_brier"], (0.36 + 0.16) / 2, places=4)
        self.assertAlmostEqual(out["market_brier_same_rows"], 0.25, places=4)


if __name__ == "__main__":
    unittest.main()
