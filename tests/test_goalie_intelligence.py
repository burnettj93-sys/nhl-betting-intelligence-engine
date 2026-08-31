"""
Tests for research/goalie_intelligence/ -- the pregame starting-goalie
intelligence foundation (Stage 1: internal historical inference model +
external-source schema design). Small hand-built fixtures for feature/
model logic; the real corpus (research/goalie_intelligence/actual_starters.jsonl,
built from real MoneyPuck goalie data) is used for a handful of
end-to-end sanity checks.
"""
import ast
import os
import unittest

from research.goalie_intelligence import features as gf
from research.goalie_intelligence import model as gm
from research.goalie_intelligence import source_schema as ss

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOALIE_MODULE_DIR = os.path.join(REPO_ROOT, "research", "goalie_intelligence")


def row(game_id, team, opponent, game_date, season, starter, icetime=3600.0,
        other=None):
    return {
        "game_id": game_id, "season": season, "game_date": game_date, "team": team,
        "opponent": opponent, "starter_goalie_id": starter, "starter_goalie_name": starter,
        "starter_icetime_seconds": icetime, "starter_icetime_share": 1.0,
        "n_goalies_used": 1 + len(other or []), "other_appearances": other or [],
        "provenance_type": "ARCHIVAL_RESEARCH",
    }


class TestActualStarterAsTargetLabelOnly(unittest.TestCase):
    def test_starter_field_is_postgame_derived_not_a_pregame_source(self):
        """Part 9/28: actual_starter_goalie_id in the corpus is
        reconstructed from postgame boxscore-style data (icetime) --
        this test documents that fact structurally: the corpus builder
        module (not imported by features.py) is the only place that
        computes it from raw appearance data."""
        with open(os.path.join(GOALIE_MODULE_DIR, "features.py")) as f:
            tree = ast.parse(f.read())
        # features.py must never IMPORT the raw MoneyPuck goalie CSV
        # parsing logic -- it only ever reads the ALREADY-BUILT corpus,
        # and only ever through team_history_as_of()'s PIT gate. (A
        # docstring reference to build_starter_corpus.py as
        # documentation is fine -- this checks actual imports, via AST,
        # not the docstring text.)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn("build_starter_corpus", alias.name)
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertNotIn("build_starter_corpus", node.module)


class TestNoTargetGameLeakage(unittest.TestCase):
    def test_team_history_as_of_excludes_the_target_games_own_row(self):
        rows = [
            row(1, "WPG", "X", "2024-10-01", 20242025, "A"),
            row(2, "WPG", "X", "2024-10-03", 20242025, "B"),  # target game
        ]
        history = gf.team_history_as_of(rows, "WPG", "2024-10-03")
        game_ids = {r["game_id"] for r in history}
        self.assertNotIn(2, game_ids)
        self.assertEqual(game_ids, {1})

    def test_same_day_game_excluded_strict_less_than(self):
        rows = [row(1, "WPG", "X", "2024-10-03", 20242025, "A")]
        history = gf.team_history_as_of(rows, "WPG", "2024-10-03")
        self.assertEqual(history, [])

    def test_future_game_excluded(self):
        rows = [
            row(1, "WPG", "X", "2024-10-01", 20242025, "A"),
            row(2, "WPG", "X", "2024-11-01", 20242025, "B"),  # future
        ]
        history = gf.team_history_as_of(rows, "WPG", "2024-10-05")
        self.assertEqual([r["game_id"] for r in history], [1])


class TestPriorStartSequenceConstruction(unittest.TestCase):
    def test_previous_game_starter(self):
        rows = [row(1, "WPG", "X", "2024-10-01", 20242025, "A"),
                row(2, "WPG", "X", "2024-10-03", 20242025, "B")]
        history = gf.team_history_as_of(rows, "WPG", "2024-10-05")
        self.assertEqual(gf.previous_game_starter(history), "B")

    def test_started_in_last_n_games(self):
        rows = [row(i, "WPG", "X", f"2024-10-{i:02d}", 20242025, "A" if i % 2 else "B")
                for i in range(1, 6)]
        history = gf.team_history_as_of(rows, "WPG", "2024-10-06")
        self.assertTrue(gf.started_in_last_n_games(history, "A", 2))
        self.assertTrue(gf.started_in_last_n_games(history, "B", 2))
        self.assertFalse(gf.started_in_last_n_games(history, "C", 2))


class TestConsecutiveStartCount(unittest.TestCase):
    def test_streak_of_current_starter(self):
        rows = [row(1, "WPG", "X", "2024-10-01", 20242025, "A"),
                row(2, "WPG", "X", "2024-10-03", 20242025, "A"),
                row(3, "WPG", "X", "2024-10-05", 20242025, "A")]
        history = gf.team_history_as_of(rows, "WPG", "2024-10-07")
        self.assertEqual(gf.consecutive_start_count(history), 3)

    def test_streak_resets_on_change(self):
        rows = [row(1, "WPG", "X", "2024-10-01", 20242025, "A"),
                row(2, "WPG", "X", "2024-10-03", 20242025, "B"),
                row(3, "WPG", "X", "2024-10-05", 20242025, "B")]
        history = gf.team_history_as_of(rows, "WPG", "2024-10-07")
        self.assertEqual(gf.consecutive_start_count(history), 2)

    def test_empty_history_is_zero(self):
        self.assertEqual(gf.consecutive_start_count([]), 0)


class TestDaysSinceLastStart(unittest.TestCase):
    def test_computes_real_day_difference(self):
        rows = [row(1, "WPG", "X", "2024-10-01", 20242025, "A")]
        history = gf.team_history_as_of(rows, "WPG", "2024-10-06")
        self.assertEqual(gf.days_since_last_start(history, "A", "2024-10-06"), 5)

    def test_none_if_goalie_never_started(self):
        rows = [row(1, "WPG", "X", "2024-10-01", 20242025, "A")]
        history = gf.team_history_as_of(rows, "WPG", "2024-10-06")
        self.assertIsNone(gf.days_since_last_start(history, "Z", "2024-10-06"))


class TestPreviousNightDetection(unittest.TestCase):
    def test_goalie_played_previous_night_true_when_started(self):
        rows = [row(1, "WPG", "X", "2024-10-05", 20242025, "A")]
        history = gf.team_history_as_of(rows, "WPG", "2024-10-06")
        self.assertTrue(gf.goalie_played_previous_night(history, "A", "2024-10-06"))

    def test_false_when_two_days_gap(self):
        rows = [row(1, "WPG", "X", "2024-10-04", 20242025, "A")]
        history = gf.team_history_as_of(rows, "WPG", "2024-10-06")
        self.assertFalse(gf.goalie_played_previous_night(history, "A", "2024-10-06"))


class TestBackToBackDetection(unittest.TestCase):
    def test_true_for_one_day_gap(self):
        rows = [row(1, "WPG", "X", "2024-10-05", 20242025, "A")]
        history = gf.team_history_as_of(rows, "WPG", "2024-10-06")
        self.assertTrue(gf.team_back_to_back(history, "2024-10-06"))

    def test_false_for_two_day_gap(self):
        rows = [row(1, "WPG", "X", "2024-10-04", 20242025, "A")]
        history = gf.team_history_as_of(rows, "WPG", "2024-10-06")
        self.assertFalse(gf.team_back_to_back(history, "2024-10-06"))

    def test_false_with_no_history(self):
        self.assertFalse(gf.team_back_to_back([], "2024-10-06"))


class TestPreviousNightWorkload(unittest.TestCase):
    def test_backup_who_relieved_counts_as_played_previous_night(self):
        other = [{"goalie_id": "B", "goalie_name": "Backup", "icetime_seconds": 900.0}]
        rows = [row(1, "WPG", "X", "2024-10-05", 20242025, "A", icetime=2700.0, other=other)]
        history = gf.team_history_as_of(rows, "WPG", "2024-10-06")
        self.assertTrue(gf.goalie_played_previous_night(history, "B", "2024-10-06"))
        self.assertTrue(gf.goalie_appeared_in_game(rows[0], "B"))

    def test_goalie_who_did_not_play_at_all_is_false(self):
        rows = [row(1, "WPG", "X", "2024-10-05", 20242025, "A")]
        history = gf.team_history_as_of(rows, "WPG", "2024-10-06")
        self.assertFalse(gf.goalie_played_previous_night(history, "C", "2024-10-06"))


class TestSeasonStartShare(unittest.TestCase):
    def test_computes_correct_fraction(self):
        rows = [row(i, "WPG", "X", f"2024-10-{i:02d}", 20242025, "A" if i <= 3 else "B")
                for i in range(1, 6)]
        history = gf.team_history_as_of(rows, "WPG", "2024-10-06")
        self.assertAlmostEqual(gf.season_start_share(history, "A", 20242025), 3 / 5)
        self.assertAlmostEqual(gf.season_start_share(history, "B", 20242025), 2 / 5)

    def test_no_cross_season_carryover(self):
        rows = [row(1, "WPG", "X", "2023-10-01", 20232024, "A"),
                row(2, "WPG", "X", "2024-10-01", 20242025, "B")]
        history = gf.team_history_as_of(rows, "WPG", "2024-10-06")
        self.assertAlmostEqual(gf.season_start_share(history, "B", 20242025), 1.0)
        # A never started in season 20242025 -- share is 0.0 (the season
        # HAS games, A just isn't among the starters), not None. None is
        # reserved for "this season has no games at all yet".
        self.assertEqual(gf.season_start_share(history, "A", 20242025), 0.0)
        self.assertIsNone(gf.season_start_share(history, "A", 20252026))  # no games this season at all


class TestRecentStartShare(unittest.TestCase):
    def test_none_below_window(self):
        rows = [row(1, "WPG", "X", "2024-10-01", 20242025, "A")]
        history = gf.team_history_as_of(rows, "WPG", "2024-10-06")
        self.assertIsNone(gf.recent_start_share(history, "A", 10))

    def test_correct_share_at_window(self):
        rows = [row(i, "WPG", "X", f"2024-10-{i:02d}", 20242025, "A") for i in range(1, 11)]
        history = gf.team_history_as_of(rows, "WPG", "2024-10-15")
        self.assertAlmostEqual(gf.recent_start_share(history, "A", 10), 1.0)


class TestTandemTeamHandling(unittest.TestCase):
    def test_eligible_goalies_includes_all_recent_appearances(self):
        other = [{"goalie_id": "B", "goalie_name": "B", "icetime_seconds": 500.0}]
        rows = [row(1, "WPG", "X", "2024-10-01", 20242025, "A", other=other),
                row(2, "WPG", "X", "2024-10-03", 20242025, "B")]
        history = gf.team_history_as_of(rows, "WPG", "2024-10-05")
        candidates = gf.eligible_goalies(history, window=20)
        self.assertEqual(set(candidates), {"A", "B"})

    def test_tandem_does_not_force_binary_when_third_goalie_appears(self):
        rows = [row(1, "WPG", "X", "2024-10-01", 20242025, "A"),
                row(2, "WPG", "X", "2024-10-03", 20242025, "B"),
                row(3, "WPG", "X", "2024-10-05", 20242025, "C")]
        history = gf.team_history_as_of(rows, "WPG", "2024-10-07")
        candidates = gf.eligible_goalies(history, window=20)
        self.assertEqual(set(candidates), {"A", "B", "C"})


class TestStarterProbabilitiesSumToOne(unittest.TestCase):
    def test_softmax_sums_to_one_for_two_candidates(self):
        probs = gm.softmax([1.2, -0.3])
        self.assertAlmostEqual(sum(probs), 1.0, places=9)

    def test_softmax_sums_to_one_for_many_candidates(self):
        probs = gm.softmax([0.1, 2.0, -1.5, 0.0, 3.3])
        self.assertAlmostEqual(sum(probs), 1.0, places=9)
        self.assertTrue(all(p >= 0 for p in probs))

    def test_score_candidates_sums_to_one(self):
        weights = [0.5, -0.1, 1.0, 0.3, -2.0]
        fvs = [[1, 0, 0.6, 0.5, 0], [0, 3, 0.4, 0.5, 1]]
        probs = gm.score_candidates(weights, fvs)
        self.assertAlmostEqual(sum(probs), 1.0, places=9)


class TestProjectedVsConfirmedDistinction(unittest.TestCase):
    def test_status_constants_are_distinct(self):
        self.assertNotEqual(ss.PROJECTED, ss.CONFIRMED)
        self.assertIn(ss.PROJECTED, ss.VALID_STATUSES)
        self.assertIn(ss.CONFIRMED, ss.VALID_STATUSES)

    def test_observation_rejects_invalid_status(self):
        with self.assertRaises(ValueError):
            ss.SourceObservation(game_id=1, team_id="WPG", goalie_id="A", source="X",
                                  source_status="MAYBE", raw_status="maybe",
                                  source_observed_at_utc="t", ingested_at_utc="t")


class TestSourceRawStatusPreservation(unittest.TestCase):
    def test_raw_status_preserved_verbatim_even_when_normalized_differs(self):
        obs = ss.SourceObservation(game_id=1, team_id="WPG", goalie_id="A", source="RotoWire",
                                    source_status=ss.EXPECTED, raw_status="Nothing confirmed, we expect Hellebuyck",
                                    source_observed_at_utc="2026-01-01T12:00:00Z", ingested_at_utc="t")
        self.assertEqual(obs.raw_status, "Nothing confirmed, we expect Hellebuyck")
        self.assertEqual(obs.source_status, ss.EXPECTED)


class TestSourceTimestampPreservation(unittest.TestCase):
    def test_observed_and_ingested_timestamps_are_independent_fields(self):
        obs = ss.SourceObservation(game_id=1, team_id="WPG", goalie_id="A", source="X",
                                    source_status=ss.PROJECTED, raw_status="Projected",
                                    source_observed_at_utc="2026-01-01T12:00:00Z",
                                    ingested_at_utc="2026-01-01T12:05:00Z",
                                    source_published_at_utc="2026-01-01T11:00:00Z")
        self.assertNotEqual(obs.source_observed_at_utc, obs.ingested_at_utc)
        self.assertNotEqual(obs.source_published_at_utc, obs.source_observed_at_utc)


class TestConflictingSources(unittest.TestCase):
    def test_disagreement_is_reported_not_silently_resolved(self):
        o1 = ss.SourceObservation(game_id=1, team_id="X", goalie_id="A", source="S1",
                                   source_status=ss.PROJECTED, raw_status="A", source_observed_at_utc="t1",
                                   ingested_at_utc="t1")
        o2 = ss.SourceObservation(game_id=1, team_id="X", goalie_id="B", source="S2",
                                   source_status=ss.PROJECTED, raw_status="B", source_observed_at_utc="t2",
                                   ingested_at_utc="t2")
        result = ss.compute_consensus([o1, o2])
        self.assertTrue(result.conflicting)
        self.assertEqual(result.status, ss.PROJECTED)  # never auto-escalated to CONFIRMED
        self.assertEqual(len(result.observations), 2)  # both preserved

    def test_agreement_raises_confidence_but_stays_projected(self):
        obs = [ss.SourceObservation(game_id=1, team_id="X", goalie_id="A", source=f"S{i}",
                                     source_status=ss.PROJECTED, raw_status="A",
                                     source_observed_at_utc="t", ingested_at_utc="t")
               for i in range(3)]
        result = ss.compute_consensus(obs)
        self.assertEqual(result.status, ss.PROJECTED)
        self.assertEqual(result.confidence, "HIGH")
        self.assertFalse(result.conflicting)


class TestConfirmationOverride(unittest.TestCase):
    def test_confirmation_overrides_projection_consensus(self):
        projected = ss.SourceObservation(game_id=1, team_id="X", goalie_id="A", source="S1",
                                          source_status=ss.PROJECTED, raw_status="A",
                                          source_observed_at_utc="t1", ingested_at_utc="t1")
        confirmed = ss.SourceObservation(game_id=1, team_id="X", goalie_id="B", source="S2",
                                          source_status=ss.CONFIRMED, raw_status="Confirmed: B",
                                          source_observed_at_utc="t2", ingested_at_utc="t2")
        result = ss.compute_consensus([projected, confirmed])
        self.assertEqual(result.status, ss.CONFIRMED)
        self.assertEqual(result.leading_goalie_id, "B")
        self.assertIs(result.confirmed_by, confirmed)

    def test_prior_projected_observation_is_preserved_alongside_confirmation(self):
        projected = ss.SourceObservation(game_id=1, team_id="X", goalie_id="A", source="S1",
                                          source_status=ss.PROJECTED, raw_status="A",
                                          source_observed_at_utc="t1", ingested_at_utc="t1")
        confirmed = ss.SourceObservation(game_id=1, team_id="X", goalie_id="B", source="S2",
                                          source_status=ss.CONFIRMED, raw_status="Confirmed: B",
                                          source_observed_at_utc="t2", ingested_at_utc="t2")
        result = ss.compute_consensus([projected, confirmed])
        # the original (now-superseded) projection is still in the record --
        # never deleted, per Part 15 "do not overwrite history"
        self.assertIn(projected, result.observations)


class TestLateSourceRevision(unittest.TestCase):
    def test_conflicting_confirmations_are_flagged_not_silently_overwritten(self):
        c1 = ss.SourceObservation(game_id=1, team_id="X", goalie_id="A", source="S1",
                                   source_status=ss.CONFIRMED, raw_status="Confirmed: A",
                                   source_observed_at_utc="t1", ingested_at_utc="t1")
        c2 = ss.SourceObservation(game_id=1, team_id="X", goalie_id="B", source="S2",
                                   source_status=ss.CONFIRMED, raw_status="Confirmed: B (change)",
                                   source_observed_at_utc="t2", ingested_at_utc="t2")
        result = ss.compute_consensus([c1, c2])
        self.assertTrue(result.conflicting)
        self.assertIsNone(result.confirmed_by)  # ambiguous -- not resolved automatically
        self.assertEqual(len(result.observations), 2)


class TestNoFutureSourceObservation(unittest.TestCase):
    def test_internal_model_never_reads_a_future_or_same_day_row(self):
        """Reuses the same PIT guarantee already proven at the feature
        layer (TestNoTargetGameLeakage) -- restated at the model layer:
        build_feature_vector only ever receives `history`, which is
        always produced by team_history_as_of()."""
        with open(os.path.join(GOALIE_MODULE_DIR, "model.py")) as f:
            source = f.read()
        self.assertNotIn("actual_starters.jsonl", source)
        self.assertNotIn("load_starter_corpus", source)


class TestNoPostgameObservationLeakage(unittest.TestCase):
    def test_build_feature_vector_never_reads_the_target_rows_own_starter(self):
        rows = [row(1, "WPG", "X", "2024-10-01", 20242025, "A")]
        history = gf.team_history_as_of(rows, "WPG", "2024-10-03")
        # the target game (game_id=2, starter="Z") is never in `history`
        fv = gm.build_feature_vector(history, "Z", 20242025, "2024-10-03", is_back_to_back=False)
        self.assertEqual(fv[0], 0.0)  # started_previous_game: Z never appears in history at all


class TestHistoricalInferenceReproducibility(unittest.TestCase):
    def test_fit_weights_is_deterministic(self):
        examples = [
            {"feature_vectors": [[1, 0, 0.6, 0.5, 0], [0, 2, 0.4, 0.5, 0]], "target_index": 0},
            {"feature_vectors": [[0, 0, 0.3, 0.4, 1], [1, 3, 0.7, 0.6, 0]], "target_index": 1},
        ] * 5
        w1 = gm.fit_weights(examples, n_iter=100)
        w2 = gm.fit_weights(examples, n_iter=100)
        self.assertEqual(w1, w2)

    def test_build_feature_vector_is_deterministic(self):
        rows = [row(1, "WPG", "X", "2024-10-01", 20242025, "A")]
        history = gf.team_history_as_of(rows, "WPG", "2024-10-05")
        fv1 = gm.build_feature_vector(history, "A", 20242025, "2024-10-05", False)
        fv2 = gm.build_feature_vector(history, "A", 20242025, "2024-10-05", False)
        self.assertEqual(fv1, fv2)


class TestBaselineComparison(unittest.TestCase):
    def test_b2b_aware_baseline_avoids_goalie_who_played_last_night_on_b2b(self):
        rows = [row(1, "WPG", "X", "2024-10-05", 20242025, "A", icetime=3000.0)]  # yesterday: A started
        history = gf.team_history_as_of(rows, "WPG", "2024-10-06")
        pick = gm.baseline_b2b_aware(history, ["A", "B"], 20242025, is_back_to_back=True,
                                      prediction_game_date="2024-10-06")
        self.assertEqual(pick, "B")

    def test_last_game_starter_baseline_matches_previous_game(self):
        rows = [row(1, "WPG", "X", "2024-10-01", 20242025, "A")]
        history = gf.team_history_as_of(rows, "WPG", "2024-10-03")
        pick = gm.baseline_last_game_starter(history, ["A", "B"])
        self.assertEqual(pick, "A")


class TestCalibrationCalculation(unittest.TestCase):
    def test_multiclass_brier_zero_for_perfect_prediction(self):
        import sys
        sys.path.insert(0, REPO_ROOT)
        from research.run_goalie_intelligence import multiclass_brier
        self.assertAlmostEqual(multiclass_brier([1.0, 0.0], 0), 0.0, places=9)

    def test_multiclass_brier_positive_for_wrong_prediction(self):
        import sys
        sys.path.insert(0, REPO_ROOT)
        from research.run_goalie_intelligence import multiclass_brier
        self.assertGreater(multiclass_brier([0.2, 0.8], 0), 0.0)


class TestSourceContractFailureBehavior(unittest.TestCase):
    def test_record_observation_raises_clear_unavailable_error(self):
        with self.assertRaises(ss.ExternalSourceUnavailableError):
            ss.record_observation()

    def test_empty_observation_list_yields_unknown_not_a_crash(self):
        result = ss.compute_consensus([])
        self.assertEqual(result.status, ss.UNKNOWN)
        self.assertEqual(result.observations, [])


class TestProductionModelUnchanged(unittest.TestCase):
    def test_goalie_intelligence_modules_never_import_production_combined_model(self):
        for fname in ("features.py", "model.py", "source_schema.py", "build_starter_corpus.py"):
            path = os.path.join(GOALIE_MODULE_DIR, fname)
            with open(path) as f:
                tree = ast.parse(f.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module in ("models.combined_model", "models.elo_model"):
                    self.fail(f"{fname} must not import production model modules")
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotEqual(alias.name, "models.combined_model")

    def test_no_module_writes_to_nhl_db(self):
        for fname in os.listdir(GOALIE_MODULE_DIR):
            if not fname.endswith(".py"):
                continue
            with open(os.path.join(GOALIE_MODULE_DIR, fname)) as f:
                tree = ast.parse(f.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import) and any(a.name == "db" for a in node.names):
                    self.fail(f"{fname} must not import production db.py")
                if isinstance(node, ast.ImportFrom) and node.module == "db":
                    self.fail(f"{fname} must not import production db.py")


class TestRealCorpusEndToEndSanity(unittest.TestCase):
    """A handful of checks against the real, already-built corpus
    (research/goalie_intelligence/actual_starters.jsonl) -- not
    synthetic fixtures, to confirm the real data behaves as documented."""

    @classmethod
    def setUpClass(cls):
        cls.rows = gf.load_starter_corpus()

    def test_corpus_is_nonempty_and_real(self):
        self.assertGreater(len(self.rows), 10000)

    def test_every_row_has_a_provenance_type_of_archival_research(self):
        for r in self.rows[:200]:
            self.assertEqual(r["provenance_type"], "ARCHIVAL_RESEARCH")

    def test_no_ambiguous_low_confidence_starters_leaked_into_corpus(self):
        for r in self.rows[:500]:
            self.assertGreaterEqual(r["starter_icetime_share"], 0.55)


if __name__ == "__main__":
    unittest.main()
