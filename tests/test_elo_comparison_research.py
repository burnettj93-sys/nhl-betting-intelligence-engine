"""
Tests for research/elo_comparison.py -- the isolated, non-production Elo
candidate comparison (see that module's docstring and
ELO_REAL_DATA_COMPARISON_REPORT.md). This module never touches nhl.db or
any bitemporal table, so it is intentionally OUTSIDE the scope of
tests/test_structural_reads.py -- these tests instead pin down the
research module's own STRICT PRIOR-GAME-DATE walk-forward discipline and
its exact equivalence to unmodified production Elo (Part 21/22 of the
controlled-experiment instruction).

All fixture games below are small, hand-constructed, and exist only to
exercise this test file -- they are not part of the real corpus and are
not written anywhere.
"""
import math
import unittest

import config
from models.elo_model import EloModel
from research import elo_comparison as ec


def game(game_id, season, game_date, home, away, home_score, away_score, period_type="REG"):
    return {
        "game_id": game_id, "season": season, "game_date": game_date,
        "home_team": home, "away_team": away,
        "home_score": home_score, "away_score": away_score,
        "period_type": period_type,
    }


class TestStrictPriorGameDateEligibility(unittest.TestCase):
    def test_same_day_games_do_not_affect_each_others_predictions(self):
        # Two games on the SAME date, sharing team TOR is impossible (a
        # team can't play twice in one day) -- but two games on the same
        # date sharing NO teams must produce predictions identical to
        # running either one alone: neither can see the other's result.
        games = [
            game(1, 20222023, "2022-10-10", "TOR", "BOS", 3, 2),
            game(2, 20222023, "2022-10-10", "NYR", "MTL", 1, 4),
        ]
        records, _ = ec.run_walkforward(games)
        both = {r["game_id"]: r["p_home"] for r in records}

        solo_records, _ = ec.run_walkforward([games[0]])
        solo = {r["game_id"]: r["p_home"] for r in solo_records}
        self.assertAlmostEqual(both[1], solo[1], places=12)

    def test_a_later_dated_games_result_cannot_change_an_earlier_prediction(self):
        base_games = [
            game(1, 20222023, "2022-10-10", "TOR", "BOS", 3, 2),
            game(2, 20222023, "2022-10-12", "TOR", "NYR", 1, 0),
        ]
        records_a, _ = ec.run_walkforward(base_games)
        p_earlier_a = next(r["p_home"] for r in records_a if r["game_id"] == 2)

        # Mutate the FIRST game's result -- irrelevant to this test -- and
        # add a LATER game whose result must not leak backward.
        later_leaking = base_games + [game(3, 20222023, "2022-10-14", "TOR", "MTL", 9, 0)]
        records_b, _ = ec.run_walkforward(later_leaking)
        p_earlier_b = next(r["p_home"] for r in records_b if r["game_id"] == 2)

        self.assertAlmostEqual(p_earlier_a, p_earlier_b, places=12)

    def test_strictly_earlier_game_date_does_affect_a_later_prediction(self):
        # sanity check the opposite direction: an EARLIER result SHOULD
        # move a later prediction away from the always-1500 baseline.
        games = [
            game(1, 20222023, "2022-10-10", "TOR", "BOS", 5, 0),
            game(2, 20222023, "2022-10-12", "TOR", "MTL", 3, 2),
        ]
        records, _ = ec.run_walkforward(games)
        p_second = next(r["p_home"] for r in records if r["game_id"] == 2)
        neutral_p = 1.0 / (1.0 + 10 ** (-(config.ELO_HOME_ADVANTAGE) / 400.0))
        self.assertNotAlmostEqual(p_second, neutral_p, places=6)
        self.assertGreater(p_second, neutral_p)  # TOR's earlier blowout win should help it


class TestCandidateWeightFormulas(unittest.TestCase):
    def test_regulation_always_gets_full_otso_weight(self):
        fn = ec.make_otso_weight_fn(0.5)
        self.assertEqual(fn("REG", 5, 1), 1.0)
        self.assertEqual(fn("REG", 1, 0), 1.0)

    def test_overtime_gets_reduced_otso_weight(self):
        fn = ec.make_otso_weight_fn(0.5)
        self.assertEqual(fn("OT", 4, 3), 0.5)

    def test_shootout_gets_reduced_otso_weight(self):
        fn = ec.make_otso_weight_fn(0.5)
        self.assertEqual(fn("SO", 2, 1), 0.5)

    def test_mov_weight_is_neutral_at_one_goal_margin(self):
        fn = ec.make_mov_weight_fn(mov_cap=3)
        self.assertAlmostEqual(fn("REG", 3, 2), 1.0, places=12)
        # OT/SO margin is always exactly 1 by rule -- also neutral.
        self.assertAlmostEqual(fn("OT", 4, 3), 1.0, places=12)

    def test_mov_weight_grows_with_margin_up_to_the_cap(self):
        fn = ec.make_mov_weight_fn(mov_cap=4)
        w1 = fn("REG", 2, 1)   # margin 1
        w2 = fn("REG", 3, 1)   # margin 2
        w3 = fn("REG", 4, 1)   # margin 3
        w4 = fn("REG", 5, 1)   # margin 4 (at cap)
        self.assertLess(w1, w2)
        self.assertLess(w2, w3)
        self.assertLess(w3, w4)

    def test_mov_weight_saturates_beyond_the_cap_no_explosion_on_blowouts(self):
        fn = ec.make_mov_weight_fn(mov_cap=3)
        at_cap = fn("REG", 4, 1)      # margin 3, at cap
        way_beyond = fn("REG", 21, 0)  # margin 21, a blowout
        self.assertAlmostEqual(at_cap, way_beyond, places=12)

    def test_combined_weight_multiplies_both_components(self):
        fn = ec.make_combined_weight_fn(otso_weight=0.5, mov_cap=3)
        otso_fn = ec.make_otso_weight_fn(0.5)
        mov_fn = ec.make_mov_weight_fn(3)
        # regulation, margin 3: otso=1.0, mov=log(4)/log(2)=2.0
        self.assertAlmostEqual(fn("REG", 4, 1), otso_fn("REG", 4, 1) * mov_fn("REG", 4, 1), places=12)
        # OT game: otso=0.5, mov always neutral (margin 1) = 1.0
        self.assertAlmostEqual(fn("OT", 4, 3), 0.5 * 1.0, places=12)


class TestSeasonRegressionUnchanged(unittest.TestCase):
    def test_season_boundary_regresses_by_the_exact_production_fraction(self):
        state = ec.ResearchEloState()
        state.ratings["TOR"] = 1700.0
        state._current_season = 20222023
        state.maybe_regress_new_season(20232024)
        expected = 1700.0 + (config.ELO_START - 1700.0) * config.ELO_SEASON_REGRESSION
        self.assertAlmostEqual(state.ratings["TOR"], expected, places=9)

    def test_no_regression_within_the_same_season(self):
        state = ec.ResearchEloState()
        state.ratings["TOR"] = 1700.0
        state._current_season = 20222023
        state.maybe_regress_new_season(20222023)
        self.assertEqual(state.ratings["TOR"], 1700.0)


class TestReproducibilityAndSharedEvaluationSet(unittest.TestCase):
    def _sample_games(self):
        return [
            game(1, 20222023, "2022-10-10", "TOR", "BOS", 5, 0),
            game(2, 20222023, "2022-10-12", "TOR", "MTL", 3, 2, "OT"),
            game(3, 20222023, "2022-10-12", "NYR", "NYI", 2, 1, "SO"),
            game(4, 20232024, "2023-10-05", "TOR", "BOS", 1, 0),
        ]

    def test_running_the_same_candidate_twice_is_byte_identical(self):
        games = self._sample_games()
        records_a, state_a = ec.run_walkforward(games, weight_fn=ec.make_otso_weight_fn(0.5))
        records_b, state_b = ec.run_walkforward(games, weight_fn=ec.make_otso_weight_fn(0.5))
        self.assertEqual(records_a, records_b)
        self.assertEqual(state_a.ratings, state_b.ratings)

    def test_every_candidate_predicts_the_exact_same_game_set(self):
        games = self._sample_games()
        candidates = [None, ec.make_otso_weight_fn(0.5), ec.make_mov_weight_fn(3),
                      ec.make_combined_weight_fn(0.5, 3)]
        game_id_sets = []
        for fn in candidates:
            records, _ = ec.run_walkforward(games, weight_fn=fn)
            game_id_sets.append({r["game_id"] for r in records})
        self.assertTrue(all(s == game_id_sets[0] for s in game_id_sets))
        self.assertEqual(game_id_sets[0], {1, 2, 3, 4})


class TestProductionEloEquivalence(unittest.TestCase):
    """Part 21 item 12 / Part 22: production models/elo_model.py must
    remain unchanged. This proves ResearchEloState(weight_fn=None) --
    used as Candidate A, the control -- reduces to EXACTLY the same
    rating trajectory as directly driving the unmodified production
    EloModel, over an identical sequence of games. If this ever fails,
    either this research module or production elo_model.py has drifted
    from the other."""

    def test_baseline_candidate_matches_production_elo_model_step_by_step(self):
        games = [
            game(1, 20222023, "2022-10-10", "TOR", "BOS", 5, 0),
            game(2, 20222023, "2022-10-12", "TOR", "MTL", 3, 2, "OT"),
            game(3, 20222023, "2022-10-14", "BOS", "MTL", 1, 6, "SO"),
            game(4, 20232024, "2023-10-05", "TOR", "BOS", 1, 0),
        ]
        research_records, research_state = ec.run_walkforward(games, weight_fn=None)

        production = EloModel(teams=["TOR", "BOS", "MTL"])
        for g in sorted(games, key=lambda x: x["game_date"]):
            production.maybe_regress_new_season(g["season"])
            production.update(g["home_team"], g["away_team"], g["home_score"] > g["away_score"])

        for team in ["TOR", "BOS", "MTL"]:
            self.assertAlmostEqual(research_state.ratings[team], production.ratings[team], places=9)

        # and every recorded pregame probability must match what
        # production's win_probability() would have said at that point --
        # spot-check the first game, where both start at ELO_START.
        first = next(r for r in research_records if r["game_id"] == 1)
        fresh_production = EloModel(teams=["TOR", "BOS"])
        self.assertAlmostEqual(first["p_home"], fresh_production.win_probability("TOR", "BOS"), places=12)


class TestMetrics(unittest.TestCase):
    def _toy_records(self):
        return [
            {"p_home": 0.6, "actual_home_win": 1.0, "season": 1},
            {"p_home": 0.6, "actual_home_win": 0.0, "season": 1},
            {"p_home": 0.4, "actual_home_win": 0.0, "season": 2},
            {"p_home": 0.4, "actual_home_win": 1.0, "season": 2},
        ]

    def test_brier_score_matches_hand_computation(self):
        records = self._toy_records()
        expected = ((0.6 - 1.0) ** 2 + (0.6 - 0.0) ** 2 + (0.4 - 0.0) ** 2 + (0.4 - 1.0) ** 2) / 4
        self.assertAlmostEqual(ec.brier_score(records), expected, places=12)

    def test_log_loss_matches_hand_computation(self):
        records = self._toy_records()
        # (p=0.6,a=1)->-log(0.6); (p=0.6,a=0)->-log(0.4);
        # (p=0.4,a=0)->-log(0.6); (p=0.4,a=1)->-log(0.4)
        expected = -(math.log(0.6) + math.log(0.4) + math.log(0.6) + math.log(0.4)) / 4
        self.assertAlmostEqual(ec.log_loss(records), expected, places=9)

    def test_season_breakdown_splits_correctly(self):
        records = self._toy_records()
        breakdown = ec.season_breakdown(records)
        self.assertEqual(set(breakdown.keys()), {1, 2})
        self.assertEqual(breakdown[1]["n"], 2)
        self.assertEqual(breakdown[2]["n"], 2)

    def test_calibration_table_flags_low_n_buckets(self):
        records = [{"p_home": 0.42, "actual_home_win": 1.0}]
        table = ec.calibration_table(records, edges=[0.40, 0.45, 0.50])
        bucket = next(b for b in table if b["lo"] == 0.40)
        self.assertEqual(bucket["n"], 1)
        self.assertTrue(bucket["low_n"])

    def test_paired_bootstrap_is_deterministic_given_a_seed(self):
        baseline = [0.1, 0.2, 0.3, 0.4, 0.5]
        candidate = [0.05, 0.25, 0.2, 0.4, 0.45]
        r1 = ec.paired_bootstrap_delta(baseline, candidate, n_resamples=200, seed=7)
        r2 = ec.paired_bootstrap_delta(baseline, candidate, n_resamples=200, seed=7)
        self.assertEqual(r1, r2)

    def test_paired_bootstrap_requires_equal_length_paired_scores(self):
        with self.assertRaises(AssertionError):
            ec.paired_bootstrap_delta([0.1, 0.2], [0.1], n_resamples=10, seed=1)


class TestCorpusLoading(unittest.TestCase):
    def test_load_corpus_reads_the_real_normalized_file(self):
        games = ec.load_corpus("research/real_nhl_results/normalized_regular_season_games.jsonl")
        self.assertEqual(len(games), 5248)
        seasons = {g["season"] for g in games}
        self.assertEqual(seasons, {20222023, 20232024, 20242025, 20252026})

    def test_group_by_date_sorted_orders_by_calendar_date_not_game_id(self):
        games = [
            game(999, 20222023, "2022-10-08", "SJS", "NSH", 2, 3),
            game(1, 20222023, "2022-10-07", "NSH", "SJS", 4, 1),
        ]
        grouped = ec.group_by_date_sorted(games)
        self.assertEqual([d for d, _ in grouped], ["2022-10-07", "2022-10-08"])


if __name__ == "__main__":
    unittest.main()
