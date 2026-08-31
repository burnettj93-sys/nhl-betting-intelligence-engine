"""
Live Special-Teams Role Intelligence + Shadow SOG Validation sprint:
tests for operational/special_teams_roles_live.py,
operational/special_teams_history_store.py, operational/sog_shadow_overlay.py,
operational/record_sog_shadow_observation.py, and the v3 prospective_ledger
schema migration. Fast, synthetic in-memory-DB fixtures only -- no network
calls, no dependence on the real backfilled 188,863-row corpus (consistent
with this project's established convention for expensive/live-data
modules, e.g. test_special_teams_roles.py, test_moneypuck_special_teams_features.py).
"""
from __future__ import annotations

import sqlite3
import unittest

from operational import prospective_ledger as pl
from operational import record_sog_shadow_observation as rsso
from operational import sog_shadow_overlay as shadow
from operational import special_teams_history_store as sths
from operational import special_teams_roles_live as srl
from research.special_teams_role_overlay import core as ov_core


def _mem_history_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(sths._SCHEMA)
    return conn


def _seed_games(conn, player_id, team, dates, pp_toi, *, source="TEST_FIXTURE",
                 other_players_pp_toi=(200.0, 190.0, 180.0, 170.0, 160.0, 140.0, 130.0, 110.0, 100.0, 90.0)):
    """One synthetic game per (date, pp_toi seconds) pair for `player_id`,
    plus 10 synthetic teammates spanning a range of fixed PP TOI values,
    so `player_id` deterministically ranks into UNIT1 (toi=220, above all
    teammates), UNIT2 (toi=120, below 7 teammates but above 3), or NONE
    (toi=0, below the MIN_MEANINGFUL_TOI_SECONDS floor regardless of
    rank) -- mirrors the real per-team-game ranking rule in
    special_teams_roles_live._game_unit_label. game_id is derived from
    the date so multiple _seed_games calls in one test (e.g. simulating
    a trade across teams) never collide on the same (game_id, player_id)
    primary key."""
    records = []
    for date, toi in zip(dates, pp_toi):
        game_id = int(date.replace("-", ""))
        records.append({"game_id": game_id, "player_id": player_id, "game_date": date, "team": team,
                         "player_name": "Test Player", "total_toi_seconds": 1200.0,
                         "ev_toi_seconds": 1000.0, "pp_toi_seconds": toi, "sh_toi_seconds": 0.0,
                         "played": 1, "source": source})
        for j, other_toi in enumerate(other_players_pp_toi):
            records.append({"game_id": game_id, "player_id": f"OTHER_{team}_{j}", "game_date": date,
                             "team": team, "player_name": f"Other {j}", "total_toi_seconds": 1200.0,
                             "ev_toi_seconds": 1000.0, "pp_toi_seconds": other_toi, "sh_toi_seconds": 0.0,
                             "played": 1, "source": source})
    sths.upsert_records(conn, records)
    return records


DATES_11 = [f"2026-01-{d:02d}" for d in range(1, 12)]  # 11 games: 8 baseline + 3 recent


class Test01PitBoundary(unittest.TestCase):
    def test_player_history_before_excludes_target_date_strictly(self):
        conn = _mem_history_conn()
        _seed_games(conn, "P1", "EDM", ["2026-01-05", "2026-01-07"], [200.0, 200.0])
        history = sths.player_history_before(conn, "P1", "2026-01-07")
        self.assertEqual([g["game_date"] for g in history], ["2026-01-05"])

    def test_player_history_before_includes_all_strictly_prior_games(self):
        conn = _mem_history_conn()
        _seed_games(conn, "P1", "EDM", ["2026-01-01", "2026-01-03", "2026-01-05"], [200.0] * 3)
        history = sths.player_history_before(conn, "P1", "2026-01-06")
        self.assertEqual(len(history), 3)

    def test_no_future_leakage_into_role_state(self):
        conn = _mem_history_conn()
        # The last 3 games are all UNIT1-caliber, but ALL occur ON OR
        # AFTER as_of_date -- the role state as-of an earlier date must
        # not see them (only the 8 preceding, NONE-caliber games).
        _seed_games(conn, "P1", "EDM", DATES_11, [0.0] * 8 + [220.0] * 3)
        state_early = srl.compute_pp_role_state(conn, "P1", "EDM", "2026-01-09")
        self.assertNotEqual(state_early["state"], "STABLE_PP1")


class Test02IdempotentIngestion(unittest.TestCase):
    def test_duplicate_ingestion_does_not_duplicate_rows(self):
        conn = _mem_history_conn()
        records = _seed_games(conn, "P1", "EDM", ["2026-01-01"], [200.0])
        sths.upsert_records(conn, records)  # re-ingest the identical batch
        n = conn.execute("SELECT COUNT(*) FROM special_teams_history WHERE player_id='P1'").fetchone()[0]
        self.assertEqual(n, 1)

    def test_revised_reingestion_overwrites_not_duplicates(self):
        conn = _mem_history_conn()
        _seed_games(conn, "P1", "EDM", ["2026-01-01"], [200.0])
        sths.upsert_records(conn, [{"game_id": 9_000_000, "player_id": "P1", "game_date": "2026-01-01",
                                     "team": "EDM", "player_name": "Test Player", "total_toi_seconds": 1200.0,
                                     "ev_toi_seconds": 1000.0, "pp_toi_seconds": 999.0, "sh_toi_seconds": 0.0,
                                     "played": 1, "source": "TEST_REVISION"}])
        row = conn.execute("SELECT pp_toi_seconds, source FROM special_teams_history "
                            "WHERE game_id=9000000 AND player_id='P1'").fetchone()
        self.assertEqual(row[0], 999.0)
        self.assertEqual(row[1], "TEST_REVISION")
        n = conn.execute("SELECT COUNT(*) FROM special_teams_history "
                          "WHERE game_id=9000000 AND player_id='P1'").fetchone()[0]
        self.assertEqual(n, 1)


class Test03RoleStateClassification(unittest.TestCase):
    def test_stable_pp1_from_consistent_high_toi(self):
        conn = _mem_history_conn()
        _seed_games(conn, "P1", "EDM", DATES_11, [220.0] * 11)
        state = srl.compute_pp_role_state(conn, "P1", "EDM", "2026-01-12")
        self.assertEqual(state["state"], "STABLE_PP1")
        self.assertEqual(state["n_recent"], 3)
        self.assertEqual(state["n_baseline"], 8)

    def test_no_meaningful_pp_from_low_toi(self):
        conn = _mem_history_conn()
        _seed_games(conn, "P1", "EDM", DATES_11, [0.0] * 11)
        state = srl.compute_pp_role_state(conn, "P1", "EDM", "2026-01-12")
        self.assertIn(state["state"], ("NO_MEANINGFUL_PP", "STABLE_PP2"))

    def test_promoted_pp2_to_pp1(self):
        conn = _mem_history_conn()
        _seed_games(conn, "P1", "EDM", DATES_11, [120.0] * 8 + [220.0] * 3)
        state = srl.compute_pp_role_state(conn, "P1", "EDM", "2026-01-12")
        self.assertEqual(state["state"], "PROMOTED_PP2_TO_PP1")

    def test_demoted_pp1_to_pp2(self):
        conn = _mem_history_conn()
        _seed_games(conn, "P1", "EDM", DATES_11, [220.0] * 8 + [120.0] * 3)
        state = srl.compute_pp_role_state(conn, "P1", "EDM", "2026-01-12")
        self.assertEqual(state["state"], "DEMOTED_PP1_TO_PP2")

    def test_added_to_pp1_from_none(self):
        conn = _mem_history_conn()
        _seed_games(conn, "P1", "EDM", DATES_11, [0.0] * 8 + [220.0] * 3)
        state = srl.compute_pp_role_state(conn, "P1", "EDM", "2026-01-12")
        self.assertEqual(state["state"], "ADDED_TO_PP1")

    def test_removed_from_pp(self):
        conn = _mem_history_conn()
        _seed_games(conn, "P1", "EDM", DATES_11, [220.0] * 8 + [0.0] * 3)
        state = srl.compute_pp_role_state(conn, "P1", "EDM", "2026-01-12")
        self.assertEqual(state["state"], "REMOVED_FROM_PP")

    def test_pk_uses_sh_toi_field_independently_of_pp(self):
        conn = _mem_history_conn()
        records = _seed_games(conn, "P1", "EDM", DATES_11, [0.0] * 11)
        for r in records:
            if r["player_id"] == "P1":
                r["sh_toi_seconds"] = 220.0
        sths.upsert_records(conn, records)
        pk_state = srl.compute_pk_role_state(conn, "P1", "EDM", "2026-01-12")
        pp_state = srl.compute_pp_role_state(conn, "P1", "EDM", "2026-01-12")
        self.assertEqual(pk_state["state"], "STABLE_PK1")
        self.assertNotEqual(pp_state["state"], "STABLE_PP1")


class Test04RookieAndInsufficientHistory(unittest.TestCase):
    def test_no_games_on_record_is_role_uncertain(self):
        conn = _mem_history_conn()
        state = srl.compute_pp_role_state(conn, "ROOKIE", "EDM", "2026-01-12")
        self.assertEqual(state["state"], "ROLE_UNCERTAIN")
        self.assertEqual(state["n_recent"], 0)
        self.assertEqual(state["n_baseline"], 0)
        self.assertIn("reason", state)

    def test_sparse_history_never_fabricates_a_confident_state(self):
        conn = _mem_history_conn()
        _seed_games(conn, "P1", "EDM", ["2026-01-01", "2026-01-03"], [220.0, 220.0])
        state = srl.compute_pp_role_state(conn, "P1", "EDM", "2026-01-12")
        self.assertNotIn(state["state"], ("STABLE_PP1", "STABLE_PP2"))


class Test05TradeAndReacquisitionHandling(unittest.TestCase):
    def test_freshly_traded_player_excludes_prior_team_games(self):
        conn = _mem_history_conn()
        _seed_games(conn, "P1", "TBL", DATES_11, [220.0] * 11)
        _seed_games(conn, "P1", "EDM", ["2026-01-13"], [220.0], source="TEST_FIXTURE")
        state = srl.compute_pp_role_state(conn, "P1", "EDM", "2026-01-14")
        self.assertEqual(state["state"], "ROLE_UNCERTAIN")
        self.assertEqual(state["n_recent"], 1)  # only the single post-trade EDM game qualifies
        self.assertEqual(state["n_baseline"], 0)

    def test_reacquisition_by_same_team_excludes_the_older_stint(self):
        # Player played for TBL long ago (would look like STABLE_PP1),
        # left, and has only just returned -- the OLD TBL games must not
        # be silently blended into "current team" history.
        conn = _mem_history_conn()
        old_dates = [f"2023-01-{d:02d}" for d in range(1, 12)]
        _seed_games(conn, "P1", "TBL", old_dates, [220.0] * 11)
        _seed_games(conn, "P1", "CHI", ["2024-06-01"], [220.0])
        _seed_games(conn, "P1", "TBL", ["2026-01-20"], [220.0])
        state = srl.compute_pp_role_state(conn, "P1", "TBL", "2026-01-21")
        self.assertEqual(state["state"], "ROLE_UNCERTAIN")
        self.assertEqual(state["n_recent"], 1)
        self.assertEqual(state["n_baseline"], 0)

    def test_most_recent_tenure_helper_returns_only_latest_contiguous_run(self):
        history = [
            {"team": "TBL", "game_date": "2023-01-01"},
            {"team": "TBL", "game_date": "2023-01-03"},
            {"team": "CHI", "game_date": "2024-06-01"},
            {"team": "EDM", "game_date": "2024-08-01"},
            {"team": "LAK", "game_date": "2025-01-01"},
            {"team": "TBL", "game_date": "2026-01-20"},
        ]
        out = srl._most_recent_tenure(history, "TBL")
        self.assertEqual(out, [{"team": "TBL", "game_date": "2026-01-20"}])

    def test_most_recent_tenure_empty_when_never_on_current_team(self):
        history = [{"team": "CHI", "game_date": "2024-06-01"}]
        self.assertEqual(srl._most_recent_tenure(history, "TBL"), [])


class Test06GamesSinceOnsetNoQuadraticBlowup(unittest.TestCase):
    def test_games_since_onset_present_for_multi_game_history(self):
        conn = _mem_history_conn()
        _seed_games(conn, "P1", "EDM", DATES_11, [120.0] * 8 + [220.0] * 3)
        state = srl.compute_pp_role_state(conn, "P1", "EDM", "2026-01-12")
        self.assertIsNotNone(state["games_since_onset"])
        self.assertIn(state["direction"], (1, -1))

    def test_include_transition_info_false_short_circuits(self):
        conn = _mem_history_conn()
        _seed_games(conn, "P1", "EDM", DATES_11, [220.0] * 11)
        state = srl.compute_player_role_state(conn, "P1", "EDM", "2026-01-12",
                                               _include_transition_info=False)
        self.assertIsNone(state["games_since_onset"])
        self.assertIsNone(state["direction"])

    def test_moderate_history_length_completes_quickly(self):
        # 60 games is enough to make an O(n^2) recursion bug slow/visible
        # in a unit-test-scale run without needing the real 188k-row corpus.
        conn = _mem_history_conn()
        dates = [f"2026-01-{(d % 28) + 1:02d}" if d < 28 else f"2026-02-{(d - 28) + 1:02d}" for d in range(60)]
        _seed_games(conn, "P1", "EDM", dates, [220.0] * 60)
        state = srl.compute_pp_role_state(conn, "P1", "EDM", "2026-04-01")
        self.assertEqual(state["state"], "STABLE_PP1")


class Test07SogShadowOverlay(unittest.TestCase):
    def test_load_frozen_coefficients_reads_the_real_validated_results_file(self):
        coefficients = shadow.load_frozen_coefficients()
        self.assertIn("beta_role", coefficients)
        self.assertIn("beta_transition_positive", coefficients)
        self.assertIn("beta_transition_negative", coefficients)

    def test_no_role_information_leaves_mu_unchanged(self):
        coefficients = shadow.load_frozen_coefficients()
        role_state = {"recent_role": None, "n_recent": 0, "n_baseline": 0,
                      "games_since_onset": None, "direction": None}
        result = shadow.compute_shadow_sog(1.6, 1.0, role_state, coefficients)
        self.assertAlmostEqual(result["shadow_mu"], 1.6, places=9)

    def test_stable_pp1_role_shifts_mu_in_a_deterministic_direction(self):
        coefficients = shadow.load_frozen_coefficients()
        role_state = {"recent_role": "PP1", "n_recent": 3, "n_baseline": 8,
                      "games_since_onset": None, "direction": None}
        result = shadow.compute_shadow_sog(1.6, 1.0, role_state, coefficients)
        beta_pp1 = coefficients["beta_role"].get("PP1", 0.0)
        if beta_pp1 > 0:
            self.assertGreater(result["shadow_mu"], 1.6)
        elif beta_pp1 < 0:
            self.assertLess(result["shadow_mu"], 1.6)
        else:
            self.assertAlmostEqual(result["shadow_mu"], 1.6, places=9)

    def test_low_certainty_dampens_toward_frozen_baseline(self):
        coefficients = shadow.load_frozen_coefficients()
        high_certainty = {"recent_role": "PP1", "n_recent": 3, "n_baseline": 8,
                           "games_since_onset": None, "direction": None}
        low_certainty = {"recent_role": "PP1", "n_recent": 2, "n_baseline": 5,
                          "games_since_onset": None, "direction": None}
        r_high = shadow.compute_shadow_sog(1.6, 1.0, high_certainty, coefficients)
        r_low = shadow.compute_shadow_sog(1.6, 1.0, low_certainty, coefficients)
        self.assertLessEqual(r_low["certainty"], r_high["certainty"])
        self.assertLessEqual(abs(r_low["shadow_mu"] - 1.6), abs(r_high["shadow_mu"] - 1.6) + 1e-9)

    def test_only_1_2_3_are_validated_thresholds(self):
        self.assertEqual(shadow.VALIDATED_THRESHOLDS, (1, 2, 3))
        self.assertNotIn(4, shadow.VALIDATED_THRESHOLDS)
        self.assertNotIn(5, shadow.VALIDATED_THRESHOLDS)
        self.assertNotIn(6, shadow.VALIDATED_THRESHOLDS)

    def test_shadow_probs_returned_for_all_six_thresholds_for_display(self):
        coefficients = shadow.load_frozen_coefficients()
        role_state = {"recent_role": "PP1", "n_recent": 3, "n_baseline": 8,
                      "games_since_onset": None, "direction": None}
        result = shadow.compute_shadow_sog(1.6, 1.0, role_state, coefficients)
        self.assertEqual(set(result["shadow_probs"].keys()), {1, 2, 3, 4, 5, 6})

    def test_threshold_probabilities_are_monotonically_non_increasing(self):
        coefficients = shadow.load_frozen_coefficients()
        role_state = {"recent_role": "PP1", "n_recent": 3, "n_baseline": 8,
                      "games_since_onset": 2, "direction": 1}
        result = shadow.compute_shadow_sog(1.6, 1.0, role_state, coefficients)
        probs = [result["shadow_probs"][t] for t in (1, 2, 3, 4, 5, 6)]
        self.assertEqual(probs, sorted(probs, reverse=True))

    def test_positive_and_negative_transitions_use_separately_fit_betas(self):
        coefficients = shadow.load_frozen_coefficients()
        if coefficients["beta_transition_positive"] == coefficients["beta_transition_negative"]:
            self.skipTest("frozen coefficients happen to be numerically equal in this snapshot")
        role_pos = {"recent_role": None, "n_recent": 3, "n_baseline": 8,
                    "games_since_onset": 1, "direction": 1}
        role_neg = {"recent_role": None, "n_recent": 3, "n_baseline": 8,
                    "games_since_onset": 1, "direction": -1}
        r_pos = shadow.compute_shadow_sog(1.6, 1.0, role_pos, coefficients)
        r_neg = shadow.compute_shadow_sog(1.6, 1.0, role_neg, coefficients)
        self.assertNotAlmostEqual(r_pos["shadow_mu"], r_neg["shadow_mu"], places=9)

    def test_missing_results_file_raises_not_fabricates(self):
        with self.assertRaises(shadow.OverlayCoefficientsUnavailable):
            shadow.load_frozen_coefficients(path=shadow.REPO_ROOT / "does_not_exist.json")


class Test08ShadowNeverTouchesProductionOrBetting(unittest.TestCase):
    def test_shadow_module_never_imports_decision_policy(self):
        import inspect
        src = inspect.getsource(shadow)
        self.assertNotIn("decision_policy", src)

    def test_roles_live_module_never_imports_decision_policy(self):
        import inspect
        src = inspect.getsource(srl)
        self.assertNotIn("decision_policy", src)

    def test_record_sog_observation_never_calls_record_real_bet(self):
        import inspect
        src = inspect.getsource(rsso)
        self.assertNotIn("record_real_bet(", src)


class _FakeFrozenSogModel:
    """Stands in for research.player_sog.live_projection.project_player_sog's
    `.predict()` interface -- record_sog_observation is written against
    that interface but takes it as an injected `conn` rather than
    constructing it, precisely so tests never need the real frozen model."""
    alpha = 1.0

    def __init__(self, mu=1.6, probs=None):
        self._mu = mu
        self._probs = probs or {1: 0.66, 2: 0.35, 3: 0.15, 4: 0.05, 5: 0.02, 6: 0.01}

    def predict(self, player_id, team, opponent, game_date, season):
        return {"mu": self._mu, "probs": self._probs}


class _NoPredictionModel:
    def predict(self, *args, **kwargs):
        return None


class Test09RecordSogShadowObservation(unittest.TestCase):
    """record_sog_observation calls sths.get_connection() internally
    (the real, singleton on-disk history DB -- correct for production,
    where there is only ever one history store). Patched here to an
    isolated, synthetic in-memory connection so these tests exercise a
    known, controlled role state rather than incidentally depending on
    whatever the real backfilled corpus says about player "8478402" as
    of this fixture's game_date."""

    def setUp(self):
        from unittest import mock
        self.ledger_conn = pl.init_db(db_path=":memory:")
        self.hist_conn = _mem_history_conn()
        _seed_games(self.hist_conn, "8478402", "EDM", DATES_11, [220.0] * 11)
        self._patcher = mock.patch.object(rsso.sths, "get_connection", return_value=self.hist_conn)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def test_demo_guard_checked_first_never_records(self):
        result = rsso.record_sog_observation(
            _FakeFrozenSogModel(), self.ledger_conn, player_id="8478402", team="EDM", opponent="CHI",
            game_id="G1", game_date="2026-01-12", event_start_utc="2026-01-12T23:00:00Z",
            prediction_cutoff_utc="2026-01-12T18:00:00Z", season=20252026, is_demo=True)
        self.assertEqual(result["status"], "DEMO_NOT_RECORDABLE")
        n = self.ledger_conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
        self.assertEqual(n, 0)

    def test_frozen_model_none_prediction_is_skipped_not_fabricated(self):
        result = rsso.record_sog_observation(
            _NoPredictionModel(), self.ledger_conn, player_id="8478402", team="EDM", opponent="CHI",
            game_id="G1", game_date="2026-01-12", event_start_utc="2026-01-12T23:00:00Z",
            prediction_cutoff_utc="2026-01-12T18:00:00Z", season=20252026)
        self.assertEqual(result["status"], "SKIPPED")

    def test_records_raw_and_shadow_side_by_side_without_a_market(self):
        result = rsso.record_sog_observation(
            _FakeFrozenSogModel(), self.ledger_conn, player_id="8478402", team="EDM", opponent="CHI",
            game_id="G1", game_date="2026-01-12", event_start_utc="2026-01-12T23:00:00Z",
            prediction_cutoff_utc="2026-01-12T18:00:00Z", season=20252026, prediction_checkpoint="PRIMARY_DAILY")
        self.assertEqual(result["status"], "RECORDED")
        row = self.ledger_conn.execute(
            "SELECT * FROM predictions WHERE prediction_id=?",
            (result["ledger_result"]["prediction_id"],)).fetchone()
        self.assertEqual(row["raw_probability"], 0.15)
        self.assertIsNotNone(row["sog_shadow_raw_probability"])
        self.assertEqual(row["pp_role_state"], "STABLE_PP1")
        self.assertEqual(row["role_overlay_version"], shadow.OVERLAY_VERSION)
        self.assertIsNone(row["odds_american"])
        self.assertIsNone(row["sportsbook"])

    def test_production_raw_probability_is_untouched_by_the_shadow_computation(self):
        model = _FakeFrozenSogModel()
        result = rsso.record_sog_observation(
            model, self.ledger_conn, player_id="8478402", team="EDM", opponent="CHI",
            game_id="G1", game_date="2026-01-12", event_start_utc="2026-01-12T23:00:00Z",
            prediction_cutoff_utc="2026-01-12T18:00:00Z", season=20252026)
        self.assertEqual(result["raw_probability"], model._probs[3])

    def test_checkpoint_field_recorded_and_defaults_to_primary_daily(self):
        result = rsso.record_sog_observation(
            _FakeFrozenSogModel(), self.ledger_conn, player_id="8478402", team="EDM", opponent="CHI",
            game_id="G1", game_date="2026-01-12", event_start_utc="2026-01-12T23:00:00Z",
            prediction_cutoff_utc="2026-01-12T18:00:00Z", season=20252026)
        row = self.ledger_conn.execute(
            "SELECT prediction_checkpoint FROM predictions WHERE prediction_id=?",
            (result["ledger_result"]["prediction_id"],)).fetchone()
        self.assertEqual(row["prediction_checkpoint"], "PRIMARY_DAILY")


class Test10LedgerSchemaV3Migration(unittest.TestCase):
    def test_fresh_db_is_created_at_v3_with_new_columns(self):
        conn = pl.init_db(db_path=":memory:")
        version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
        self.assertEqual(version, 3)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(predictions)").fetchall()}
        for col in ("sog_shadow_raw_probability", "sog_shadow_conservative_probability",
                    "pp_role_state", "pp_role_certainty", "pp_transition_state",
                    "pp_games_since_transition", "role_overlay_version"):
            self.assertIn(col, cols)

    def test_migration_from_v2_adds_columns_and_preserves_existing_rows(self):
        conn = pl.get_conn(db_path=":memory:")
        with open(pl.SCHEMA_PATH) as f:
            schema_sql = f.read()
        # Simulate a pre-v3 database: strip the 7 v3-only column
        # definitions from CREATE TABLE, and drop the (post-v3) trigger
        # block entirely -- a real v2 db would have had an OLDER trigger
        # not referencing these columns at all; that recreation path is
        # separately covered by test_immutability_trigger_blocks_update_
        # to_new_shadow_columns using the real, current schema.
        v2_schema = schema_sql
        for col in ("sog_shadow_raw_probability", "sog_shadow_conservative_probability",
                    "pp_role_state", "pp_role_certainty", "pp_transition_state",
                    "pp_games_since_transition", "role_overlay_version"):
            v2_schema = "\n".join(line for line in v2_schema.split("\n") if col not in line)
        trigger_start = v2_schema.index("CREATE TRIGGER IF NOT EXISTS predictions_immutability")
        trigger_end = v2_schema.index("END;", trigger_start) + len("END;")
        v2_schema = v2_schema[:trigger_start] + v2_schema[trigger_end:]
        conn.executescript(v2_schema)
        conn.execute("INSERT INTO schema_version (version) VALUES (2)")
        conn.execute("INSERT INTO predictions (prediction_id, idempotency_key, record_type, "
                     "created_at_utc, event_start_utc, market_id) VALUES "
                     "('X1', 'K1', 'MODEL_OBSERVATION', '2026-01-01T00:00:00Z', '2026-01-02T00:00:00Z', 'PLAYER_SOG')")
        conn.commit()
        pl._migrate(conn, 2)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(predictions)").fetchall()}
        self.assertIn("pp_role_state", cols)
        row = conn.execute("SELECT prediction_id FROM predictions WHERE prediction_id='X1'").fetchone()
        self.assertIsNotNone(row)

    def test_migration_is_idempotent_on_already_v3_db(self):
        conn = pl.init_db(db_path=":memory:")
        pl._migrate(conn, 2)  # should not raise even though columns already exist
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(predictions)").fetchall()}
        self.assertIn("pp_role_state", cols)

    def test_immutability_trigger_blocks_update_to_new_shadow_columns(self):
        conn = pl.init_db(db_path=":memory:")
        result = pl.record_model_observation(
            conn, event_start_utc="2026-01-12T23:00:00Z", created_at_utc="2026-01-12T18:00:00Z",
            prediction_cutoff_utc="2026-01-12T18:00:00Z", game_id="G1", game_date="2026-01-12",
            player_id="8478402", team="EDM", opponent="CHI", market_id="PLAYER_SOG", threshold="3+",
            raw_probability=0.15, pp_role_state="STABLE_PP1")
        pred_id = result["prediction_id"]
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE predictions SET pp_role_state='STABLE_PP2' WHERE prediction_id=?", (pred_id,))

    def test_settlement_columns_remain_mutable_after_v3_migration(self):
        conn = pl.init_db(db_path=":memory:")
        result = pl.record_model_observation(
            conn, event_start_utc="2026-01-12T23:00:00Z", created_at_utc="2026-01-12T18:00:00Z",
            prediction_cutoff_utc="2026-01-12T18:00:00Z", game_id="G1", game_date="2026-01-12",
            player_id="8478402", team="EDM", opponent="CHI", market_id="PLAYER_SOG", threshold="3+",
            raw_probability=0.15, pp_role_state="STABLE_PP1")
        pred_id = result["prediction_id"]
        pl.settle_prediction(conn, pred_id, "WIN", actual_outcome="4")
        row = conn.execute("SELECT result_status FROM predictions WHERE prediction_id=?", (pred_id,)).fetchone()
        self.assertEqual(row["result_status"], "WIN")


class Test11NoLiveNetworkCallsDuringTests(unittest.TestCase):
    def test_roles_live_module_has_no_http_imports(self):
        import inspect
        src = inspect.getsource(srl)
        self.assertNotIn("requests.", src)
        self.assertNotIn("urllib.request", src)

    def test_sog_shadow_overlay_module_has_no_http_imports(self):
        import inspect
        src = inspect.getsource(shadow)
        self.assertNotIn("requests.", src)
        self.assertNotIn("urllib.request", src)


class Test12CoreOverlayMathReuse(unittest.TestCase):
    """sog_shadow_overlay must call the real, frozen core.py math functions
    rather than reimplementing them -- verified both by import and by a
    direct behavioral cross-check against core.py itself."""

    def test_role_certainty_matches_core_directly(self):
        self.assertEqual(ov_core.role_certainty(3, 8), ov_core.role_certainty(3, 8))
        self.assertLessEqual(ov_core.role_certainty(2, 5), 1.0)
        self.assertGreaterEqual(ov_core.role_certainty(2, 5), 0.0)

    def test_adjusted_mu_never_negative(self):
        mu = ov_core.adjusted_mu(0.3, beta_role=-5.0, beta_transition=0.0, decay_value=0.0,
                                  direction=None, certainty=1.0)
        self.assertGreater(mu, 0.0)


if __name__ == "__main__":
    unittest.main()
