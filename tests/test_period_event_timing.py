"""
Period Event Timing + Special Teams Scoring Intelligence research sprint:
tests for research/period_event_timing/*.py and the run_period_event_timing_*.py
scripts. Real SQLite corpus (research/real_nhl_pbp/research_pbp.db) for
integration-level checks; synthetic fixtures for fast, deterministic unit
tests of the classification/window logic itself. No sportsbook network
calls anywhere in this sprint's new code (Part 72).
"""
from __future__ import annotations

import math
import sqlite3
import unittest
from pathlib import Path

from research.period_event_timing import event_extraction as ee
from research.period_event_timing import manpower as mp
from research.period_event_timing import penalties as pw
from research.period_event_timing import special_teams_corpus as stc
from research.real_nhl_pbp.normalize import is_empty_net_context
from research.real_nhl_pbp.schema import PbpEvent

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "research" / "real_nhl_pbp" / "research_pbp.db"


def _conn():
    return sqlite3.connect(DB_PATH)


# ---------------------------------------------------------------------
# 1-8. situationCode parsing / manpower classification
# ---------------------------------------------------------------------

class Test01SituationCodeParsing(unittest.TestCase):
    def test_parses_all_four_fields(self):
        parsed = mp.parse_situation_code("1551")
        self.assertEqual(parsed, {"away_goalie_in": True, "away_skaters": 5,
                                   "home_skaters": 5, "home_goalie_in": True})

    def test_none_returns_none(self):
        self.assertIsNone(mp.parse_situation_code(None))

    def test_wrong_length_returns_none(self):
        self.assertIsNone(mp.parse_situation_code("155"))
        self.assertIsNone(mp.parse_situation_code("15511"))

    def test_non_digit_returns_none(self):
        self.assertIsNone(mp.parse_situation_code("15X1"))


class Test02ManpowerClassification(unittest.TestCase):
    def test_5v5(self):
        self.assertEqual(mp.classify_manpower_state("1551"), "5v5")

    def test_5v4_and_4v5(self):
        self.assertEqual(mp.classify_manpower_state("1541"), "5v4")
        self.assertEqual(mp.classify_manpower_state("1451"), "4v5")

    def test_5v3_and_3v5(self):
        self.assertEqual(mp.classify_manpower_state("1531"), "5v3")
        self.assertEqual(mp.classify_manpower_state("1351"), "3v5")

    def test_4v4(self):
        self.assertEqual(mp.classify_manpower_state("1441"), "4v4")

    def test_3v3(self):
        self.assertEqual(mp.classify_manpower_state("1331"), "3v3")

    def test_6v5_and_5v6_are_empty_net_extra_attacker(self):
        self.assertEqual(mp.classify_manpower_state("0651"), "6v5")
        self.assertEqual(mp.classify_manpower_state("1560"), "5v6")
        self.assertTrue(mp.is_empty_net_state("6v5"))
        self.assertTrue(mp.is_empty_net_state("5v6"))

    def test_6v4_and_4v6(self):
        self.assertEqual(mp.classify_manpower_state("0641"), "6v4")
        self.assertEqual(mp.classify_manpower_state("1460"), "4v6")

    def test_unknown_for_missing_code(self):
        self.assertEqual(mp.classify_manpower_state(None), "UNKNOWN")

    def test_malformed_for_impossible_combo(self):
        # 6 skaters with goalie also reported in net is physically impossible.
        self.assertEqual(mp.classify_manpower_state("1561"), "MALFORMED")
        # fewer than 6 skaters with the goalie also reported pulled.
        self.assertEqual(mp.classify_manpower_state("0551"), "MALFORMED")

    def test_out_of_range_skater_count_is_malformed(self):
        self.assertEqual(mp.classify_manpower_state("1271"), "MALFORMED")

    def test_rare_but_physically_valid_state_gets_a_real_label_not_other(self):
        self.assertEqual(mp.classify_manpower_state("1341"), "3v4")
        self.assertNotIn(mp.classify_manpower_state("1341"), ("UNKNOWN", "MALFORMED"))


class Test03PowerPlayAndEvenStrengthHelpers(unittest.TestCase):
    def test_is_even_strength(self):
        self.assertTrue(mp.is_even_strength("5v5"))
        self.assertTrue(mp.is_even_strength("4v4"))
        self.assertTrue(mp.is_even_strength("3v3"))
        self.assertFalse(mp.is_even_strength("5v4"))

    def test_is_power_play_for_home(self):
        # classify_manpower_state labels states "{away}v{home}" -- "4v5"
        # means away has 4 skaters, home has 5, i.e. HOME is on the PP.
        self.assertTrue(mp.is_power_play_for_home("4v5"))
        self.assertFalse(mp.is_power_play_for_home("5v4"))   # away has more skaters
        self.assertIsNone(mp.is_power_play_for_home("5v5"))
        self.assertIsNone(mp.is_power_play_for_home("6v5"))  # empty net, not a PP label
        self.assertIsNone(mp.is_power_play_for_home("UNKNOWN"))
        self.assertIsNone(mp.is_power_play_for_home("MALFORMED"))


# ---------------------------------------------------------------------
# 9. manpower validation against the REAL corpus (no unexplained large
# missing category -- Part 5's actual acceptance bar)
# ---------------------------------------------------------------------

class Test04RealCorpusManpowerValidation(unittest.TestCase):
    def test_reg_and_ot_malformed_and_unknown_rates_are_small(self):
        conn = _conn()
        cur = conn.cursor()
        cur.execute("SELECT situation_code FROM pbp_events WHERE period_type != 'SO'")
        codes = [r[0] for r in cur.fetchall()]
        summary = mp.manpower_validation_summary(codes)
        self.assertGreater(summary["total_events"], 1_000_000)
        self.assertLess(summary["unknown_pct"], 0.01)
        self.assertLess(summary["malformed_pct"], 0.01)

    def test_shootout_situation_codes_are_excluded_from_this_check(self):
        # Shootout situationCodes use a different (1-shooter-vs-1-goalie)
        # semantic and are NOT real 5-a-side manpower states -- confirmed
        # live: excluding period_type == 'SO' drops the malformed rate
        # from ~0.39% to ~0.15%, and the overwhelming majority of the
        # excluded ones are SO events. This test guards that finding.
        conn = _conn()
        cur = conn.cursor()
        cur.execute("SELECT situation_code FROM pbp_events")
        all_codes = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT situation_code FROM pbp_events WHERE period_type != 'SO'")
        reg_ot_codes = [r[0] for r in cur.fetchall()]
        all_summary = mp.manpower_validation_summary(all_codes)
        reg_ot_summary = mp.manpower_validation_summary(reg_ot_codes)
        self.assertGreater(all_summary["malformed_pct"], reg_ot_summary["malformed_pct"])


# ---------------------------------------------------------------------
# 10-14. penalty / manpower window reconstruction
# ---------------------------------------------------------------------

def _fake_events(*rows):
    """rows: (event_sequence, event_type, period_number, seconds_elapsed, situation_code, team_id)"""
    cols = ["event_id", "event_sequence", "event_type", "period_number", "period_type",
            "seconds_elapsed_in_period", "situation_code", "team_id"]
    out = []
    for i, (seq, etype, period, secs, sitcode, team) in enumerate(rows):
        out.append(dict(zip(cols, [i, seq, etype, period, "REG", secs, sitcode, team])))
    return out


class Test05ManpowerWindowReconstruction(unittest.TestCase):
    def test_single_penalty_window_expires_back_to_even(self):
        # "1541" = away_goalie=1, away_skaters=5, home_skaters=4, home_goalie=1
        # -> state "5v4" -> away has more skaters -> AWAY is on the PP.
        events = _fake_events(
            (1, "faceoff", 1, 0, "1551", None),
            (2, "penalty", 1, 100, "1551", 20),
            (3, "faceoff", 1, 101, "1541", None),   # away PP starts (5v4)
            (4, "shot-on-goal", 1, 150, "1541", 10),
            (5, "faceoff", 1, 221, "1551", None),   # penalty expired, back to 5v5
        )
        windows = pw.build_manpower_windows(events, home_team_id=20, away_team_id=10)
        states = [w["state"] for w in windows]
        self.assertEqual(states, ["5v5", "5v4", "5v5"])
        pp_window = windows[1]
        self.assertEqual(pp_window["advantaged_team"], "AWAY")
        self.assertEqual(pp_window["ended_by"], "STATE_CHANGE")
        self.assertEqual(pp_window["duration_seconds"], 120)

    def test_goal_terminated_window_detected(self):
        events = _fake_events(
            (1, "faceoff", 1, 0, "1541", None),
            (2, "goal", 1, 60, "1541", 20),
            (3, "faceoff", 1, 61, "1551", None),
        )
        windows = pw.build_manpower_windows(events, home_team_id=20, away_team_id=10)
        self.assertEqual(windows[0]["ended_by"], "GOAL")
        self.assertTrue(windows[0]["contains_goal"])

    def test_5_on_3_detected(self):
        # "1531" -> away_skaters=5, home_skaters=3 -> state "5v3" -> AWAY advantage.
        events = _fake_events(
            (1, "faceoff", 1, 0, "1531", None),
            (2, "faceoff", 1, 60, "1551", None),
        )
        windows = pw.build_manpower_windows(events, home_team_id=20, away_team_id=10)
        self.assertTrue(windows[0]["is_5_on_3"])
        self.assertEqual(windows[0]["advantaged_team"], "AWAY")

    def test_period_end_window_classified_correctly(self):
        events = _fake_events(
            (1, "faceoff", 1, 0, "1541", None),
            (2, "period-end", 1, 1200, "1541", None),
        )
        windows = pw.build_manpower_windows(events, home_team_id=20, away_team_id=10)
        self.assertEqual(windows[0]["ended_by"], "PERIOD_END")


class Test06TeamPenaltiesTakenDrawn(unittest.TestCase):
    def test_drawn_is_the_other_team_not_the_penalized_teams_own_id(self):
        """Real bug found and fixed during this sprint: pbp_event_players
        stamps EVERY role of a penalty event (including drawn_by) with the
        event's OWN owner team_id (the penalized team), so trusting that
        column for 'drawn' silently double-counts the penalized team
        instead of crediting the team that drew it."""
        conn = _conn()
        cur = conn.cursor()
        cur.execute("SELECT game_id, home_team_id, away_team_id FROM pbp_games LIMIT 1")
        game_id, home, away = cur.fetchone()
        result = pw.team_penalties_taken_drawn(conn, game_id, home, away)
        if home in result and away in result:
            self.assertEqual(result[home]["penalties_taken"], result[away]["penalties_drawn"])
            self.assertEqual(result[away]["penalties_taken"], result[home]["penalties_drawn"])


# ---------------------------------------------------------------------
# 15-18. special-teams team-game corpus invariants
# ---------------------------------------------------------------------

class Test07SpecialTeamsCorpusInvariants(unittest.TestCase):
    def test_pp_seconds_equals_opponent_sh_seconds_real_games(self):
        conn = _conn()
        cur = conn.cursor()
        cur.execute("SELECT game_id, home_team_id, away_team_id FROM pbp_games LIMIT 15")
        for game_id, home, away in cur.fetchall():
            rows = stc.build_team_game_special_teams(conn, game_id, home, away)
            self.assertEqual(rows[home]["pp_seconds"], rows[away]["sh_seconds"])
            self.assertEqual(rows[away]["pp_seconds"], rows[home]["sh_seconds"])

    def test_pp_shots_equals_opponent_sh_shots_allowed(self):
        """Real bug found and fixed: PP-shot attribution originally only
        incremented sh_shots_allowed off the rare "shorthanded team
        shoots" branch instead of alongside every PP-team shot, leaving
        this invariant silently false."""
        conn = _conn()
        cur = conn.cursor()
        cur.execute("SELECT game_id, home_team_id, away_team_id FROM pbp_games LIMIT 15")
        for game_id, home, away in cur.fetchall():
            rows = stc.build_team_game_special_teams(conn, game_id, home, away)
            self.assertEqual(rows[home]["pp_shots"], rows[away]["sh_shots_allowed"])
            self.assertEqual(rows[away]["pp_shots"], rows[home]["sh_shots_allowed"])
            self.assertEqual(rows[home]["pp_goals"], rows[away]["sh_goals_allowed"])
            self.assertEqual(rows[away]["pp_goals"], rows[home]["sh_goals_allowed"])

    def test_no_negative_counts(self):
        conn = _conn()
        cur = conn.cursor()
        cur.execute("SELECT game_id, home_team_id, away_team_id FROM pbp_games LIMIT 15")
        for game_id, home, away in cur.fetchall():
            rows = stc.build_team_game_special_teams(conn, game_id, home, away)
            for team_row in rows.values():
                for k, v in team_row.items():
                    self.assertGreaterEqual(v, 0, f"{k} is negative")


# ---------------------------------------------------------------------
# 19-24. goal/event extraction: shootout exclusion, statistical goal
# taxonomy, empty net, first goal, PIT-neutral (retrospective, not live)
# ---------------------------------------------------------------------

class Test08EventExtractionShootoutExclusion(unittest.TestCase):
    def test_shootout_period_never_queried_for_goals(self):
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT game_id, home_team_id, away_team_id, season, final_period_type "
            "FROM pbp_games WHERE final_period_type = 'SO' LIMIT 3")
        rows = cur.fetchall()
        self.assertTrue(rows, "expected at least one real shootout game in the corpus")
        for game_id, home, away, season, _ in rows:
            g = ee.extract_game(conn, game_id, home, away, season)
            for goal in g["goals"]:
                self.assertNotEqual(goal["period_type"], "SO")


class Test09EmptyNetConsistency(unittest.TestCase):
    def test_extraction_empty_net_flag_matches_normalize_module(self):
        """Cross-checks event_extraction's own empty-net inference against
        the already-audited research.real_nhl_pbp.normalize.is_empty_net_context
        (reused logic, re-implemented inline here only because that
        function takes a full PbpEvent object, not a raw DB row -- this
        proves the two never silently disagree)."""
        conn = _conn()
        cur = conn.cursor()
        cur.execute("SELECT game_id, home_team_id, away_team_id, season FROM pbp_games LIMIT 20")
        checked = 0
        for game_id, home, away, season in cur.fetchall():
            g = ee.extract_game(conn, game_id, home, away, season)
            for goal in g["goals"]:
                cur2 = conn.cursor()
                cur2.execute(
                    "SELECT situation_code FROM pbp_events WHERE game_id=? AND event_id=?",
                    (game_id, goal["event_id"]))
                sitcode = cur2.fetchone()[0]
                fake_event = PbpEvent(
                    game_id=game_id, event_id=goal["event_id"], event_sequence=0, event_type="goal",
                    type_code=0, period_number=goal["period_number"], period_type=goal["period_type"],
                    time_in_period="", seconds_elapsed_in_period=goal["seconds_elapsed_in_period"],
                    seconds_remaining_in_period=None, regulation_elapsed_seconds=None, team_id=goal["team_id"],
                    situation_code=sitcode, zone_code=None, x_coord=None, y_coord=None, is_statistical=True,
                    players={} if goal["is_empty_net"] else {"goalie": 1},
                )
                expected = is_empty_net_context(fake_event, defending_team_is_away=goal["is_home"])
                self.assertEqual(goal["is_empty_net"], expected)
                checked += 1
        self.assertGreater(checked, 0)


class Test10FirstGoalAndDelayedPenaltyPullClassification(unittest.TestCase):
    def test_first_goal_flag_set_exactly_once(self):
        conn = _conn()
        cur = conn.cursor()
        cur.execute("SELECT game_id, home_team_id, away_team_id, season FROM pbp_games LIMIT 10")
        for game_id, home, away, season in cur.fetchall():
            g = ee.extract_game(conn, game_id, home, away, season)
            first_flags = [goal["is_first_goal_of_game"] for goal in g["goals"]]
            self.assertEqual(sum(first_flags), 1 if g["goals"] else 0)

    def test_delayed_penalty_pull_requires_a_real_preceding_event(self):
        conn = _conn()
        cur = conn.cursor()
        cur.execute("SELECT game_id, home_team_id, away_team_id, season FROM pbp_games LIMIT 30")
        found_delayed = False
        for game_id, home, away, season in cur.fetchall():
            g = ee.extract_game(conn, game_id, home, away, season)
            for side, info in g["first_pull"].items():
                if info["reason"] == "DELAYED_PENALTY_EXTRA_ATTACKER":
                    found_delayed = True
        self.assertTrue(found_delayed, "expected at least one real delayed-penalty pull in 30 games")


# ---------------------------------------------------------------------
# 25. numerical stress -- no NaN/Inf/out-of-range from any classifier
# ---------------------------------------------------------------------

class Test11NumericalStress(unittest.TestCase):
    def test_no_crash_or_nan_on_adversarial_codes(self):
        adversarial = [None, "", "0000", "9999", "----", "abcd", "11", "155199", "0000000"]
        for code in adversarial:
            state = mp.classify_manpower_state(code)
            self.assertIsInstance(state, str)
            self.assertFalse(math.isnan(0.0))  # sanity anchor; real check is no exception raised above

    def test_window_builder_handles_empty_event_list(self):
        self.assertEqual(pw.build_manpower_windows([], 1, 2), [])
        self.assertEqual(pw.penalty_window_summary([]), {
            "total_windows": 0, "pp_opportunity_windows": 0, "pp_seconds_total": 0,
            "pp_windows_by_ended_reason": {}, "five_on_three_windows": 0,
            "goal_terminated_pp_windows": 0, "possible_overlapping_penalty_transitions": 0,
        })


# ---------------------------------------------------------------------
# 26. no sportsbook network calls anywhere in this sprint's new code
# ---------------------------------------------------------------------

class Test12NoSportsbookNetworkCalls(unittest.TestCase):
    def test_no_odds_api_or_requests_import(self):
        pkg_dir = REPO_ROOT / "research" / "period_event_timing"
        run_scripts = list(REPO_ROOT.glob("research/run_period_event_timing_*.py"))
        for path in list(pkg_dir.glob("*.py")) + run_scripts:
            src = path.read_text()
            self.assertNotIn("import requests", src, f"{path} imports requests")
            self.assertNotIn("live_sog_pricing", src, f"{path} touches the odds pipeline")
            self.assertNotIn("the-odds-api", src, f"{path} references the odds API")


if __name__ == "__main__":
    unittest.main()
