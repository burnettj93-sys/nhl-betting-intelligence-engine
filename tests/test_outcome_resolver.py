"""
Preseason Operational Readiness Closure sprint (2026-08-30), Track 3:
tests for operational/outcome_resolver.py. Uses a real, freshly-initialized
copy of the FROZEN nhl.db schema (schema.sql, read-only reuse via
db.init_db) in a temp file per test -- never the real nhl.db, never a
synthetic schema shape invented separately from the real one.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import db
from operational import outcome_resolver as resolver


def _fresh_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = db.init_db(db_path=Path(tmp.name), wipe=True)
    for team in ("EDM", "CHI"):
        conn.execute("INSERT OR IGNORE INTO teams (team_id, full_name) VALUES (?, ?)", (team, team))
    conn.commit()
    return conn


def _insert_game(conn, game_id, home_team="EDM", away_team="CHI", game_state="FINAL",
                  home_score=4, away_score=2, final_period_type="REG"):
    conn.execute(
        """INSERT INTO games (game_id, season, game_date, home_team, away_team, game_state,
           home_score, away_score, final_period_type, source)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (game_id, "20262027", "2026-10-15", home_team, away_team, game_state,
         home_score, away_score, final_period_type, "test_fixture"))
    conn.commit()


def _insert_player_stat(conn, game_id, player_id, team_id, goals=0, assists=0, shots=0, played=1):
    conn.execute(
        """INSERT INTO player_game_stats (game_id, player_id, team_id, toi_minutes, goals,
           assists, shots, played, revision_number, effective_at_utc, observed_at_utc, source)
           VALUES (?,?,?,?,?,?,?,?,1,?,?,?)""",
        (game_id, player_id, team_id, 18.0, goals, assists, shots, played,
         "2026-10-15T23:00:00Z", "2026-10-15T23:00:00Z", "test_fixture"))
    conn.commit()


def _insert_goalie_stat(conn, game_id, player_id, team_id, started, saves, shots_against=0, goals_against=0):
    conn.execute(
        """INSERT INTO goalie_game_stats (game_id, player_id, team_id, started, shots_against,
           saves, goals_against, revision_number, effective_at_utc, observed_at_utc, source)
           VALUES (?,?,?,?,?,?,?,1,?,?,?)""",
        (game_id, player_id, team_id, 1 if started else 0, shots_against, saves, goals_against,
         "2026-10-15T23:00:00Z", "2026-10-15T23:00:00Z", "test_fixture"))
    conn.commit()


class Test01GameNotFinal(unittest.TestCase):
    def test_scheduled_game_never_settles(self):
        conn = _fresh_db()
        _insert_game(conn, 1, game_state="LIVE")
        result = resolver.resolve_player_stat_threshold(
            conn, market_family="SOG", game_id=1, player_id="P1", threshold=3)
        self.assertEqual(result["status"], resolver.GAME_NOT_FINAL)
        self.assertEqual(result["official_game_status"], "LIVE")

    def test_unknown_game_never_settles(self):
        conn = _fresh_db()
        result = resolver.resolve_player_stat_threshold(
            conn, market_family="SOG", game_id=999, player_id="P1", threshold=3)
        self.assertEqual(result["status"], resolver.GAME_NOT_FINAL)


class Test02PlayerSOGSettlement(unittest.TestCase):
    def test_over_hits(self):
        conn = _fresh_db()
        _insert_game(conn, 1)
        _insert_player_stat(conn, 1, "P1", "EDM", shots=4)
        result = resolver.resolve_player_stat_threshold(
            conn, market_family="SOG", game_id=1, player_id="P1", threshold=3, side="OVER")
        self.assertEqual(result["status"], resolver.RESOLVED)
        self.assertTrue(result["outcome_hit"])
        self.assertEqual(result["actual_value"], 4)
        self.assertEqual(result["resolution_source"], "OFFICIAL_NHL_BOXSCORE")

    def test_over_misses(self):
        conn = _fresh_db()
        _insert_game(conn, 1)
        _insert_player_stat(conn, 1, "P1", "EDM", shots=2)
        result = resolver.resolve_player_stat_threshold(
            conn, market_family="SOG", game_id=1, player_id="P1", threshold=3, side="OVER")
        self.assertFalse(result["outcome_hit"])

    def test_under_side(self):
        conn = _fresh_db()
        _insert_game(conn, 1)
        _insert_player_stat(conn, 1, "P1", "EDM", shots=2)
        result = resolver.resolve_player_stat_threshold(
            conn, market_family="SOG", game_id=1, player_id="P1", threshold=3, side="UNDER")
        self.assertTrue(result["outcome_hit"])


class Test03GoalsSettlementExcludesShootout(unittest.TestCase):
    def test_regulation_goals_only(self):
        # The real NHL boxscore's per-skater `goals` field is, by official
        # league convention, the player's regulation/OT goal total --
        # shootout goals are a wholly separate stat never folded in. This
        # test documents that reliance rather than re-deriving it (there is
        # no second, parallel goal-scoring source in this project to
        # cross-check against).
        conn = _fresh_db()
        _insert_game(conn, 1, final_period_type="SO")
        _insert_player_stat(conn, 1, "P1", "EDM", goals=1)  # 1 real goal; SO winner not counted here
        result = resolver.resolve_player_stat_threshold(
            conn, market_family="GOALS", game_id=1, player_id="P1", threshold=1, side="OVER")
        self.assertEqual(result["actual_value"], 1)
        self.assertTrue(result["outcome_hit"])


class Test04AssistsSettlement(unittest.TestCase):
    def test_assists_resolved_from_official_source(self):
        conn = _fresh_db()
        _insert_game(conn, 1)
        _insert_player_stat(conn, 1, "P1", "EDM", assists=2)
        result = resolver.resolve_player_stat_threshold(
            conn, market_family="ASSISTS", game_id=1, player_id="P1", threshold=2, side="OVER")
        self.assertEqual(result["actual_value"], 2)
        self.assertTrue(result["outcome_hit"])


class Test05PointsSettlementPreservesGoalPointCoherence(unittest.TestCase):
    def test_points_equals_goals_plus_assists(self):
        conn = _fresh_db()
        _insert_game(conn, 1)
        _insert_player_stat(conn, 1, "P1", "EDM", goals=2, assists=1)
        result = resolver.resolve_player_stat_threshold(
            conn, market_family="POINTS", game_id=1, player_id="P1", threshold=1, side="OVER")
        self.assertEqual(result["actual_value"], 3)

    def test_a_goal_always_implies_at_least_that_many_points(self):
        conn = _fresh_db()
        _insert_game(conn, 1)
        _insert_player_stat(conn, 1, "P1", "EDM", goals=1, assists=0)
        goals_result = resolver.resolve_player_stat_threshold(
            conn, market_family="GOALS", game_id=1, player_id="P1", threshold=1, side="OVER")
        points_result = resolver.resolve_player_stat_threshold(
            conn, market_family="POINTS", game_id=1, player_id="P1", threshold=1, side="OVER")
        self.assertTrue(goals_result["outcome_hit"])
        self.assertTrue(points_result["outcome_hit"])
        self.assertGreaterEqual(points_result["actual_value"], goals_result["actual_value"])


class Test06BlocksNotIngested(unittest.TestCase):
    def test_blocks_fails_closed_never_guesses(self):
        conn = _fresh_db()
        _insert_game(conn, 1)
        result = resolver.resolve_blocks(conn, game_id=1, player_id="P1", threshold=2)
        self.assertEqual(result["status"], resolver.BLOCKS_NOT_INGESTED)
        self.assertIsNone(result["actual_value"])
        self.assertIsNone(result["outcome_hit"])


class Test07TeamSOGNotIngested(unittest.TestCase):
    def test_team_sog_fails_closed_never_guesses(self):
        conn = _fresh_db()
        _insert_game(conn, 1)
        result = resolver.resolve_team_sog(conn, game_id=1, team_id="EDM", threshold=30)
        self.assertEqual(result["status"], resolver.TEAM_SOG_NOT_INGESTED)


class Test08GoalieSavesSettlement(unittest.TestCase):
    def test_named_starter_resolves(self):
        conn = _fresh_db()
        _insert_game(conn, 1)
        _insert_goalie_stat(conn, 1, "G1", "EDM", started=True, saves=28)
        result = resolver.resolve_goalie_saves(conn, game_id=1, goalie_player_id="G1", threshold=25)
        self.assertEqual(result["status"], resolver.RESOLVED)
        self.assertTrue(result["outcome_hit"])
        self.assertEqual(result["resolution_source"], "OFFICIAL_NHL_BOXSCORE_GOALIE_GAME_STATS")


class Test09MultiGoalieGame(unittest.TestCase):
    """Part 15: a prediction must resolve against the SPECIFIC goalie
    named, never the team's starter, even when a relief goalie also
    appears in the same game's real stats."""

    def test_resolves_against_the_specific_named_goalie_not_the_starter(self):
        conn = _fresh_db()
        _insert_game(conn, 1)
        _insert_goalie_stat(conn, 1, "STARTER", "EDM", started=True, saves=10)
        _insert_goalie_stat(conn, 1, "RELIEF", "EDM", started=False, saves=15)
        starter_result = resolver.resolve_goalie_saves(conn, game_id=1, goalie_player_id="STARTER", threshold=25)
        relief_result = resolver.resolve_goalie_saves(conn, game_id=1, goalie_player_id="RELIEF", threshold=12)
        self.assertEqual(starter_result["actual_value"], 10)
        self.assertFalse(starter_result["outcome_hit"])
        self.assertEqual(relief_result["actual_value"], 15)
        self.assertTrue(relief_result["outcome_hit"])


class Test10GoalieDidNotPlay(unittest.TestCase):
    """Part 21: a prediction conditioned on a projected starter who never
    actually appeared in the game's real stats must be distinguished from
    a real, resolved 0-save appearance."""

    def test_projected_starter_who_never_played_is_distinct_from_zero_saves(self):
        conn = _fresh_db()
        _insert_game(conn, 1)
        _insert_goalie_stat(conn, 1, "ACTUAL_STARTER", "EDM", started=True, saves=0)
        never_played = resolver.resolve_goalie_saves(conn, game_id=1, goalie_player_id="PROJECTED_STARTER",
                                                       threshold=20)
        zero_saves = resolver.resolve_goalie_saves(conn, game_id=1, goalie_player_id="ACTUAL_STARTER",
                                                     threshold=20)
        self.assertEqual(never_played["status"], resolver.GOALIE_DID_NOT_PLAY)
        self.assertEqual(zero_saves["status"], resolver.RESOLVED)
        self.assertEqual(zero_saves["actual_value"], 0)


class Test11PlayerDidNotDress(unittest.TestCase):
    def test_scratched_player_distinct_from_zero_stat_game(self):
        conn = _fresh_db()
        _insert_game(conn, 1)
        _insert_player_stat(conn, 1, "DRESSED_ZERO", "EDM", shots=0)
        scratched = resolver.resolve_player_stat_threshold(
            conn, market_family="SOG", game_id=1, player_id="SCRATCHED", threshold=1)
        dressed_zero = resolver.resolve_player_stat_threshold(
            conn, market_family="SOG", game_id=1, player_id="DRESSED_ZERO", threshold=1)
        self.assertEqual(scratched["status"], resolver.PLAYER_DID_NOT_DRESS)
        self.assertEqual(dressed_zero["status"], resolver.RESOLVED)
        self.assertEqual(dressed_zero["actual_value"], 0)
        self.assertFalse(dressed_zero["outcome_hit"])


class Test12MoneylineSettlement(unittest.TestCase):
    def test_home_win(self):
        conn = _fresh_db()
        _insert_game(conn, 1, home_team="EDM", away_team="CHI", home_score=4, away_score=2)
        result = resolver.resolve_moneyline(conn, game_id=1, side_team_id="EDM")
        self.assertEqual(result["status"], resolver.RESOLVED)
        self.assertTrue(result["outcome_hit"])
        self.assertEqual(result["actual_value"], "EDM")

    def test_away_win(self):
        conn = _fresh_db()
        _insert_game(conn, 1, home_team="EDM", away_team="CHI", home_score=1, away_score=5)
        result = resolver.resolve_moneyline(conn, game_id=1, side_team_id="EDM")
        self.assertFalse(result["outcome_hit"])

    def test_ot_and_so_still_produce_a_real_winner(self):
        conn = _fresh_db()
        _insert_game(conn, 1, home_team="EDM", away_team="CHI", home_score=3, away_score=2,
                      final_period_type="SO")
        result = resolver.resolve_moneyline(conn, game_id=1, side_team_id="EDM")
        self.assertTrue(result["outcome_hit"])


class Test13UnsupportedMarketFailsClosed(unittest.TestCase):
    def test_unknown_market_never_guesses(self):
        conn = _fresh_db()
        _insert_game(conn, 1)
        result = resolver.resolve_prediction(conn, {
            "market_id": "PLAYER_FACEOFFS_10PLUS", "threshold": "10+", "game_id": 1, "player_id": "P1"})
        self.assertEqual(result["status"], resolver.UNSUPPORTED_SETTLEMENT_MARKET)

    def test_period_market_not_yet_supported_fails_closed(self):
        conn = _fresh_db()
        _insert_game(conn, 1)
        result = resolver.resolve_prediction(conn, {
            "market_id": "PERIOD_1_PLAYER_SOG", "threshold": "2+", "game_id": 1, "player_id": "P1"})
        self.assertEqual(result["status"], resolver.UNSUPPORTED_SETTLEMENT_MARKET)


class Test14ResolvePredictionDispatch(unittest.TestCase):
    def test_dispatches_sog_by_market_id_prefix(self):
        conn = _fresh_db()
        _insert_game(conn, 1)
        _insert_player_stat(conn, 1, "P1", "EDM", shots=4)
        result = resolver.resolve_prediction(conn, {
            "market_id": "PLAYER_SOG", "threshold": "3+", "game_id": 1, "player_id": "P1"})
        self.assertEqual(result["status"], resolver.RESOLVED)
        self.assertTrue(result["outcome_hit"])

    def test_dispatches_goalie_saves_by_market_id_prefix(self):
        conn = _fresh_db()
        _insert_game(conn, 1)
        _insert_goalie_stat(conn, 1, "G1", "EDM", started=True, saves=28)
        result = resolver.resolve_prediction(conn, {
            "market_id": "GOALIE_SAVES_25PLUS", "threshold": "25+", "game_id": 1, "player_id": "G1"})
        self.assertEqual(result["status"], resolver.RESOLVED)

    def test_default_side_is_over_when_absent(self):
        conn = _fresh_db()
        _insert_game(conn, 1)
        _insert_player_stat(conn, 1, "P1", "EDM", shots=4)
        result = resolver.resolve_prediction(conn, {
            "market_id": "PLAYER_SOG", "threshold": "3+", "side": None, "game_id": 1, "player_id": "P1"})
        self.assertTrue(result["outcome_hit"])


class Test15ResolverVersionStamped(unittest.TestCase):
    def test_every_result_carries_resolver_version(self):
        conn = _fresh_db()
        _insert_game(conn, 1, game_state="LIVE")
        result = resolver.resolve_player_stat_threshold(
            conn, market_family="SOG", game_id=1, player_id="P1", threshold=3)
        self.assertEqual(result["resolver_version"], resolver.RESOLVER_VERSION)


if __name__ == "__main__":
    unittest.main()
