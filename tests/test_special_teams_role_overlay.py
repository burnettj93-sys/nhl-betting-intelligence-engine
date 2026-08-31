"""
Special-teams role OVERLAY validation sprint: tests for
research/special_teams_role_overlay/*.py. Fast, synthetic/deterministic
fixtures -- the real-corpus-scale fitting/evaluation lives in the
run_special_teams_role_overlay_*.py scripts (one-off research pipelines,
consistent with every other run_*.py in this project).
"""
from __future__ import annotations

import math
import unittest

from research.special_teams_role_overlay import core, evaluate as ev, fit as ft, registry as reg


class Test01DecayFunctions(unittest.TestCase):
    def test_step_active_then_zero(self):
        self.assertEqual(core.decay_step(0, active_games=4), 1.0)
        self.assertEqual(core.decay_step(4, active_games=4), 1.0)
        self.assertEqual(core.decay_step(5, active_games=4), 0.0)

    def test_step_none_is_zero(self):
        self.assertEqual(core.decay_step(None), 0.0)

    def test_linear_decays_to_zero_at_horizon(self):
        self.assertAlmostEqual(core.decay_linear(0, horizon=5), 1.0)
        self.assertAlmostEqual(core.decay_linear(5, horizon=5), 0.0)
        self.assertGreater(core.decay_linear(2, horizon=5), core.decay_linear(4, horizon=5))

    def test_exponential_monotonically_decreasing(self):
        vals = [core.decay_exponential(g, tau=2.0) for g in range(6)]
        self.assertTrue(all(vals[i] > vals[i + 1] for i in range(len(vals) - 1)))
        self.assertAlmostEqual(vals[0], 1.0)

    def test_decay_fn_for_name_resolves_arbitrary_step_window(self):
        fn = core.decay_fn_for_name("step_7")
        self.assertEqual(fn(7), 1.0)
        self.assertEqual(fn(8), 0.0)

    def test_decay_fn_for_name_falls_back_on_unknown(self):
        fn = core.decay_fn_for_name("not_a_real_name")
        self.assertEqual(fn, core.DECAY_CANDIDATES["step_4"])


class Test02RoleCertainty(unittest.TestCase):
    def test_zero_below_minimum_support(self):
        self.assertEqual(core.role_certainty(1, 8), 0.0)
        self.assertEqual(core.role_certainty(3, 2), 0.0)

    def test_full_certainty_at_target_window_size(self):
        self.assertEqual(core.role_certainty(3, 8), 1.0)

    def test_partial_certainty_between_minimum_and_target(self):
        c = core.role_certainty(2, 5)
        self.assertGreater(c, 0.0)
        self.assertLess(c, 1.0)


class Test03AdjustedMuAndThresholds(unittest.TestCase):
    def test_zero_beta_reproduces_frozen_mu(self):
        mu = core.adjusted_mu(2.0, 0.0, 0.0, 0.0, None, certainty=1.0)
        self.assertAlmostEqual(mu, 2.0)

    def test_positive_beta_role_increases_mu(self):
        mu = core.adjusted_mu(2.0, 0.1, 0.0, 0.0, None, certainty=1.0)
        self.assertGreater(mu, 2.0)

    def test_certainty_shrinks_toward_frozen(self):
        full = core.adjusted_mu(2.0, 0.2, 0.0, 0.0, None, certainty=1.0)
        half = core.adjusted_mu(2.0, 0.2, 0.0, 0.0, None, certainty=0.5)
        zero = core.adjusted_mu(2.0, 0.2, 0.0, 0.0, None, certainty=0.0)
        self.assertGreater(full, half)
        self.assertGreater(half, zero)
        self.assertAlmostEqual(zero, 2.0)

    def test_negative_direction_decreases_mu(self):
        mu_pos = core.adjusted_mu(2.0, 0.0, 0.1, 1.0, 1, certainty=1.0)
        mu_neg = core.adjusted_mu(2.0, 0.0, 0.1, 1.0, -1, certainty=1.0)
        self.assertGreater(mu_pos, 2.0)
        self.assertLess(mu_neg, 2.0)

    def test_threshold_probs_monotonically_decreasing(self):
        for mu in (0.1, 0.5, 1.0, 2.0, 5.0, 10.0):
            probs = core.adjusted_threshold_probs(mu, None, (1, 2, 3, 4, 5, 6))
            vals = [probs[t] for t in (1, 2, 3, 4, 5, 6)]
            self.assertTrue(all(vals[i] >= vals[i + 1] for i in range(len(vals) - 1)),
                             f"not monotonic at mu={mu}: {vals}")

    def test_no_nan_or_inf_across_extreme_inputs(self):
        extreme = [(1e-6, -5.0, -5.0, 1.0, -1), (1e6, 5.0, 5.0, 1.0, 1), (1.0, 0.0, 0.0, 0.0, None)]
        for mu, br, bt, d, direction in extreme:
            adj = core.adjusted_mu(mu, br, bt, d, direction)
            self.assertFalse(math.isnan(adj))
            self.assertFalse(math.isinf(adj))
            probs = core.adjusted_threshold_probs(max(adj, 1e-9), None, (1, 2, 3))
            for p in probs.values():
                self.assertGreaterEqual(p, 0.0)
                self.assertLessEqual(p, 1.0)
                self.assertFalse(math.isnan(p))


class Test04GamesSinceOnset(unittest.TestCase):
    def test_onset_starts_at_zero_and_increments(self):
        rows = [
            {"state": "STABLE_PP2"}, {"state": "PROMOTED_PP2_TO_PP1"},
            {"state": "STABLE_PP1"}, {"state": "STABLE_PP1"}, {"state": "STABLE_PP1"},
        ]
        core.add_games_since_onset({"p1": rows}, "state", "since", "direction")
        self.assertIsNone(rows[0]["since"])
        self.assertEqual([r["since"] for r in rows[1:]], [0, 1, 2, 3])
        self.assertTrue(all(r["direction"] == 1 for r in rows[1:]))

    def test_new_transition_resets_counter(self):
        rows = [{"state": "PROMOTED_PP2_TO_PP1"}, {"state": "STABLE_PP1"}, {"state": "REMOVED_FROM_PP"}]
        core.add_games_since_onset({"p1": rows}, "state", "since", "direction")
        self.assertEqual(rows[0]["since"], 0)
        self.assertEqual(rows[1]["since"], 1)
        self.assertEqual(rows[2]["since"], 0)  # a NEW transition resets, even mid-decay
        self.assertEqual(rows[2]["direction"], -1)

    def test_caps_at_max_games(self):
        rows = [{"state": "PROMOTED_PP2_TO_PP1"}] + [{"state": "STABLE_PP1"}] * 20
        core.add_games_since_onset({"p1": rows}, "state", "since", "direction")
        self.assertEqual(rows[-1]["since"], core.MAX_GAMES_SINCE_ONSET)

    def test_specific_state_tracker_ignores_other_transitions(self):
        """Part 33's narrow PK scope: REMOVED_FROM_PK must never be
        conflated with DEMOTED_PK1_TO_PK2, even though both are
        'negative direction' in the general tracker."""
        rows = [{"state": "DEMOTED_PK1_TO_PK2"}, {"state": "REMOVED_FROM_PK"}, {"state": "STABLE_PK2"}]
        core.add_games_since_specific_state({"p1": rows}, "state", "REMOVED_FROM_PK", "since_removal")
        self.assertIsNone(rows[0]["since_removal"])
        self.assertEqual(rows[1]["since_removal"], 0)
        self.assertEqual(rows[2]["since_removal"], 1)


class Test05FitBetaRole(unittest.TestCase):
    def test_recovers_a_known_multiplicative_effect(self):
        # actual is exactly 1.2x mu for PP1 rows -- beta should recover log(1.2).
        rows = ([{"role": "PP1", "mu_frozen": 2.0, "actual": 2.4}] * 40 +
                [{"role": "NONE", "mu_frozen": 1.0, "actual": 1.0}] * 40)
        betas = ft.fit_beta_role(rows, "role", "mu_frozen", "actual")
        self.assertAlmostEqual(betas["PP1"], math.log(1.2), places=6)
        self.assertNotIn("NONE", betas)  # NONE is the implicit zero baseline, never fit

    def test_requires_minimum_sample_per_role(self):
        rows = [{"role": "PP1", "mu_frozen": 2.0, "actual": 3.0}] * 5  # below the 30-row floor
        betas = ft.fit_beta_role(rows, "role", "mu_frozen", "actual")
        self.assertNotIn("PP1", betas)


class Test06FitBetaTransitionStability(unittest.TestCase):
    """Regression test for the real bug found and fixed this sprint: a
    per-row (even weighted) log-ratio regression was numerically
    unstable at low Poisson counts and produced an implausible ~+1.4
    coefficient for Blocked Shots. The current aggregate-ratio method
    must stay stable on a low-count, mostly-zero synthetic sample."""

    def test_stable_on_sparse_low_count_data(self):
        import random
        rng = random.Random(20242025)
        rows = []
        for i in range(400):
            # ~0.8 mean count, mostly 0/1, matching real Blocked Shots scale
            mu = 0.8
            actual = 0.0 if rng.random() < 0.55 else (1.0 if rng.random() < 0.85 else 2.0)
            since = i % 10  # spreads games_since_onset across [0,9]
            rows.append({"role": "NONE", "mu_frozen": mu, "actual": actual,
                         "games_since_onset": since, "direction": -1})
        result = ft.fit_beta_transition(rows, "role", "mu_frozen", "actual", {},
                                         "games_since_onset", "direction")
        # A real, modest effect at this scale should stay within a
        # plausible range -- nowhere near the ~+1.4 the buggy per-row
        # method produced on real data of this same rough scale.
        self.assertLess(abs(result["beta_transition"]), 0.5)

    def test_returns_no_fit_below_minimum_support(self):
        rows = [{"role": "NONE", "mu_frozen": 1.0, "actual": 1.0, "games_since_onset": 0, "direction": 1}] * 5
        result = ft.fit_beta_transition(rows, "role", "mu_frozen", "actual", {}, "games_since_onset", "direction")
        self.assertIsNone(result["decay_name"])
        self.assertEqual(result["beta_transition"], 0.0)


class Test07Evaluation(unittest.TestCase):
    def test_brier_is_squared_error(self):
        self.assertAlmostEqual(ev.brier(0.7, 1.0), 0.09)
        self.assertAlmostEqual(ev.brier(0.3, 0.0), 0.09)

    def test_log_loss_clips_extreme_probabilities(self):
        # p=0 with y=1 would be -inf without clipping
        val = ev.log_loss(0.0, 1.0)
        self.assertFalse(math.isinf(val))
        self.assertFalse(math.isnan(val))

    def test_evaluate_thresholds_perfect_predictions_zero_brier(self):
        mus = [3.0] * 50
        actuals = [3.0] * 50

        def prob_fn(mu, alpha, t):
            return 1.0 if mu >= t else 0.0

        result = ev.evaluate_thresholds(mus, actuals, None, (3,), prob_fn)
        self.assertAlmostEqual(result["by_threshold"][3]["brier"], 0.0)
        self.assertEqual(result["mae_count"], 0.0)

    def test_game_clustered_bootstrap_no_difference_gives_near_zero_delta(self):
        examples = [{"game_id": i // 2} for i in range(40)]
        scores = [0.2] * 40
        result = ev.game_clustered_bootstrap(examples, scores, scores, n_resamples=200)
        self.assertAlmostEqual(result["point_delta"], 0.0)

    def test_bootstrap_is_deterministic_given_a_seed(self):
        examples = [{"game_id": i} for i in range(30)]
        baseline = [0.2 + 0.01 * (i % 3) for i in range(30)]
        candidate = [0.18 + 0.01 * (i % 3) for i in range(30)]
        r1 = ev.game_clustered_bootstrap(examples, baseline, candidate, n_resamples=200, seed=42)
        r2 = ev.game_clustered_bootstrap(examples, baseline, candidate, n_resamples=200, seed=42)
        self.assertEqual(r1, r2)


class Test08NoSportsbookNetworkCalls(unittest.TestCase):
    def test_no_odds_api_or_requests_import(self):
        from pathlib import Path
        repo_root = Path(__file__).resolve().parent.parent
        pkg_dir = repo_root / "research" / "special_teams_role_overlay"
        run_scripts = list(repo_root.glob("research/run_special_teams_role_overlay_*.py"))
        for path in list(pkg_dir.glob("*.py")) + run_scripts:
            src = path.read_text()
            self.assertNotIn("import requests", src, f"{path} imports requests")
            self.assertNotIn("live_sog_pricing", src, f"{path} touches the odds pipeline")


class Test09Registry(unittest.TestCase):
    def test_no_entry_claims_full_validation(self):
        """Part 58: no production replacement this sprint -- nothing in
        this research registry may claim the full VALIDATED_OVERLAY
        status without a materially larger, longer evaluation than a
        single closing sprint can perform."""
        for e in reg.OVERLAY_REGISTRY:
            self.assertNotEqual(e.status, "VALIDATED_OVERLAY")

    def test_no_entry_recommends_above_shadow_validated(self):
        allowed = {"RESEARCH", "SHADOW_VALIDATED"}
        for e in reg.OVERLAY_REGISTRY:
            self.assertIn(e.recommended_operational_status, allowed)

    def test_rejected_blocks_overlay_recommends_research_only(self):
        blocks_entry = next(e for e in reg.OVERLAY_REGISTRY if e.overlay_id == "PLAYER_BLOCKS_PK_REMOVAL_OVERLAY")
        self.assertEqual(blocks_entry.status, "REJECTED")
        self.assertEqual(blocks_entry.recommended_operational_status, "RESEARCH")


if __name__ == "__main__":
    unittest.main()
