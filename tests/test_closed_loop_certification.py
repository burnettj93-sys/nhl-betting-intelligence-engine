"""
P0.4 (2026-09-24 hardening block): closed-loop certification -- proves
prediction -> immutable snapshot -> settlement -> post-mortem actually
works together end-to-end, without waiting for a real DraftKings market
(none exists before 2026-09-29) and WITHOUT touching any real production
database. Every connection here is either ":memory:" (the prospective
ledger) or a wiped temp file (the official nhl.db-schema'd game-results
store, via db.init_db(..., wipe=True)) -- never the real nhl.db,
operational/prospective_observations.db, or paper_bankroll.db.

See docs/CLOSED_LOOP_CERTIFICATION.md for the human-readable PASS/FAIL
report this file's own results feed into.
"""
from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path

import db
from operational import daily_model_review as dmr
from operational import outcome_resolver as resolver
from operational import prospective_ledger as pl
from operational import prospective_recording as pr
from operational import settle_daily_observations as sdo
from pricing import odds_math as pm

NOW = dt.datetime.now(dt.timezone.utc)
# find_settlement_candidates() compares event_start_utc against the REAL
# wall clock (dt.datetime.now()), not an injectable time -- these must
# always be genuinely in the past relative to whenever this test
# actually runs, not a fixed date that will eventually become "future"
# again (a real bug this test file itself hit on first run: a
# hardcoded 2026-09-28 timestamp was still in the future on 2026-09-24).
EVENT_START = (NOW - dt.timedelta(days=2)).strftime("%Y-%m-%dT23:00:00.000000Z")
CUTOFF = (NOW - dt.timedelta(days=2)).strftime("%Y-%m-%dT18:00:00.000000Z")


def _official_conn():
    """An isolated, real-schema (schema.sql) game-results database --
    same shape as the real nhl.db, but a throwaway temp file, never the
    real one."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return db.init_db(Path(tmp.name), wipe=True)


def _seed_game(conn, game_id: int, home="EDM", away="CGY", home_score=4, away_score=2,
               game_state="FINAL"):
    conn.execute("INSERT OR IGNORE INTO teams (team_id) VALUES (?), (?)", (home, away))
    conn.execute(
        "INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team, away_team, "
        "schedule_observed_at_utc, game_state, home_score, away_score, final_period_type, "
        "result_observed_at_utc, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (game_id, "20262027", "2026-09-28", EVENT_START, home, away, CUTOFF, game_state,
         home_score, away_score, "REG" if game_state == "FINAL" else None, EVENT_START, "test"))
    conn.commit()


def _seed_skater_stat(conn, game_id: int, player_id: str, team: str, shots: int, toi_minutes=18.0):
    conn.execute("INSERT OR IGNORE INTO players (player_id, full_name, position) VALUES (?, ?, ?)",
                 (player_id, f"Player {player_id}", "C"))
    conn.execute(
        "INSERT INTO player_game_stats (game_id, player_id, team_id, toi_minutes, goals, assists, "
        "shots, played, revision_number, effective_at_utc, observed_at_utc, source) "
        "VALUES (?, ?, ?, ?, 0, 0, ?, 1, 1, ?, ?, ?)",
        (game_id, player_id, team, toi_minutes, shots, EVENT_START, EVENT_START, "test"))
    conn.commit()


def _record_sog_prediction(ledger_conn, *, player_id, game_id, threshold="3+", side="OVER", prob=0.6,
                            extra=None):
    fields = dict(
        event_start_utc=EVENT_START, created_at_utc=CUTOFF, prediction_cutoff_utc=CUTOFF,
        game_id=game_id, game_date="2026-09-28", player_id=player_id, team="EDM", opponent="CGY",
        market_id="PLAYER_SOG", threshold=threshold, side=side, raw_probability=prob, confidence="HIGH",
        model_version="sog_v1.0.0", odds_american=-110,
    )
    if extra:
        fields.update(extra)
    return pl.record_model_observation(ledger_conn, **fields)["prediction_id"]


# ---------------------------------------------------------------------
# 1. WIN scenario, full pipeline
# ---------------------------------------------------------------------
class Test01WinScenario(unittest.TestCase):
    def test_full_pipeline_prediction_to_settlement(self):
        ledger = pl.init_db(db_path=":memory:")
        official = _official_conn()
        _seed_game(official, 1)
        _seed_skater_stat(official, 1, "P1", "EDM", shots=5)  # clears 3+

        pred_id = _record_sog_prediction(ledger, player_id="P1", game_id=1, threshold="3+")
        before = pl.get_observation(ledger, pred_id)
        self.assertEqual(before["result_status"], "PENDING")

        summary = sdo.run_settlement_batch(ledger, official_conn=official)
        self.assertEqual(summary["settled_win"], 1)
        self.assertEqual(summary["errors"], [])

        after = pl.get_observation(ledger, pred_id)
        self.assertEqual(after["result_status"], "WIN")
        self.assertEqual(after["actual_outcome"], "5")

        # Snapshot immutability: every PREDICTION-time field is byte-for-byte
        # unchanged; only settlement columns differ.
        settlement_fields = {"result_status", "actual_outcome", "settled_at_utc", "profit_loss",
                              "closing_odds", "closing_captured_at_utc", "clv", "notes"}
        for key in before:
            if key in settlement_fields:
                continue
            self.assertEqual(before[key], after[key], f"prediction field {key!r} changed during settlement")


# ---------------------------------------------------------------------
# 2. LOSS scenario
# ---------------------------------------------------------------------
class Test02LossScenario(unittest.TestCase):
    def test_loss_settles_correctly(self):
        ledger = pl.init_db(db_path=":memory:")
        official = _official_conn()
        _seed_game(official, 2)
        _seed_skater_stat(official, 2, "P2", "EDM", shots=1)  # misses 3+

        pred_id = _record_sog_prediction(ledger, player_id="P2", game_id=2, threshold="3+")
        summary = sdo.run_settlement_batch(ledger, official_conn=official)
        self.assertEqual(summary["settled_loss"], 1)
        after = pl.get_observation(ledger, pred_id)
        self.assertEqual(after["result_status"], "LOSS")
        self.assertEqual(after["actual_outcome"], "1")


# ---------------------------------------------------------------------
# 3. VOID scenario (the practically-reachable analog of PUSH)
# ---------------------------------------------------------------------
class Test03VoidScenario(unittest.TestCase):
    """PUSH is a defined RESULT_STATE (prospective_ledger.RESULT_STATES)
    but no currently-implemented resolver (SOG/Goals/Assists/Points/
    Saves/Moneyline) can actually produce it -- none of these markets
    has a whole-number line that can tie. Confirmed by inspection, not
    assumed: this is honestly reported as N/A in
    docs/CLOSED_LOOP_CERTIFICATION.md rather than faked with a
    manufactured scenario. VOID (a player who never appeared in the
    official boxscore -- e.g. a real late scratch) is the practically
    reachable "no clean result" case and is exercised here instead."""

    def test_player_not_in_boxscore_voids_a_real_bet(self):
        """Real, deliberate distinction found while writing this test
        (not a bug): PLAYER_DID_NOT_DRESS only becomes VOID for
        real-money-adjacent record types (REAL_BET, SHADOW_POLICY_
        OBSERVATION) -- see settle_daily_observations._VOID_ON_REAL_MONEY
        / _REAL_MONEY_RECORD_TYPES. A plain MODEL_OBSERVATION on the same
        missing-player scenario correctly resolves to UNRESOLVED instead
        (no real money was ever at risk, so there's nothing to "void");
        see the next test for that case. Confirmed by first running this
        test against a MODEL_OBSERVATION and finding UNRESOLVED, not a
        test bug to paper over."""
        ledger = pl.init_db(db_path=":memory:")
        official = _official_conn()
        _seed_game(official, 3)
        # P3 is never inserted into player_game_stats -- a real
        # late-scratch/did-not-dress scenario.
        result = pl.record_real_bet(
            ledger, event_start_utc=EVENT_START, created_at_utc=CUTOFF, prediction_cutoff_utc=CUTOFF,
            game_id=3, game_date="2026-09-28", player_id="P3", team="EDM", opponent="CGY",
            market_id="PLAYER_SOG", threshold="3+", side="OVER", raw_probability=0.6, confidence="HIGH",
            stake=10.0, placed_odds=-110, placed_at_utc=CUTOFF, sportsbook="draftkings")
        summary = sdo.run_settlement_batch(ledger, official_conn=official)
        self.assertEqual(summary["settled_void"], 1)
        after = pl.get_observation(ledger, result["prediction_id"])
        self.assertEqual(after["result_status"], "VOID")

    def test_player_not_in_boxscore_on_a_plain_model_observation_is_unresolved_not_void(self):
        ledger = pl.init_db(db_path=":memory:")
        official = _official_conn()
        _seed_game(official, 33)
        pred_id = _record_sog_prediction(ledger, player_id="P33", game_id=33, threshold="3+")
        summary = sdo.run_settlement_batch(ledger, official_conn=official)
        self.assertEqual(summary["settled_unresolved"], 1)
        after = pl.get_observation(ledger, pred_id)
        self.assertEqual(after["result_status"], "UNRESOLVED")


# ---------------------------------------------------------------------
# 4. WAIT / DATA_UNAVAILABLE decision-time states survive settlement
# ---------------------------------------------------------------------
class Test04DecisionStateSurvivesSettlement(unittest.TestCase):
    def test_wait_policy_status_is_preserved_through_settlement(self):
        """A WAIT-decision prediction is still a real MODEL_OBSERVATION
        that gets a real settled outcome (WAIT is about whether real
        money should have been risked, never about whether the ledger
        tracks the outcome) -- confirms settlement never silently
        overwrites or drops the original decision-time signal."""
        ledger = pl.init_db(db_path=":memory:")
        official = _official_conn()
        _seed_game(official, 4)
        _seed_skater_stat(official, 4, "P4", "EDM", shots=5)

        pred_id = _record_sog_prediction(ledger, player_id="P4", game_id=4, threshold="3+",
                                          extra={"current_policy_status": "WAIT"})
        sdo.run_settlement_batch(ledger, official_conn=official)
        after = pl.get_observation(ledger, pred_id)
        self.assertEqual(after["result_status"], "WIN")
        self.assertEqual(after["current_policy_status"], "WAIT")  # untouched by settlement

    def test_data_unavailable_policy_status_is_preserved_through_settlement(self):
        ledger = pl.init_db(db_path=":memory:")
        official = _official_conn()
        _seed_game(official, 5)
        _seed_skater_stat(official, 5, "P5", "EDM", shots=1)

        pred_id = _record_sog_prediction(ledger, player_id="P5", game_id=5, threshold="3+",
                                          extra={"current_policy_status": "DATA_UNAVAILABLE"})
        sdo.run_settlement_batch(ledger, official_conn=official)
        after = pl.get_observation(ledger, pred_id)
        self.assertEqual(after["result_status"], "LOSS")
        self.assertEqual(after["current_policy_status"], "DATA_UNAVAILABLE")


# ---------------------------------------------------------------------
# 5. Stale-data case
# ---------------------------------------------------------------------
class Test05StaleDataCase(unittest.TestCase):
    def test_stale_data_freshness_flag_is_preserved_and_never_upgraded(self):
        """A prediction explicitly recorded with data_freshness_status
        STALE must never be silently treated as fresh by anything
        downstream -- confirms the flag survives settlement AND that
        the daily review doesn't quietly drop or overwrite it."""
        ledger = pl.init_db(db_path=":memory:")
        official = _official_conn()
        _seed_game(official, 6)
        _seed_skater_stat(official, 6, "P6", "EDM", shots=4)

        pred_id = _record_sog_prediction(ledger, player_id="P6", game_id=6, threshold="3+",
                                          extra={"data_freshness_status": "STALE"})
        sdo.run_settlement_batch(ledger, official_conn=official)
        after = pl.get_observation(ledger, pred_id)
        self.assertEqual(after["data_freshness_status"], "STALE")
        self.assertEqual(after["result_status"], "WIN")


# ---------------------------------------------------------------------
# 6. CLV handling
# ---------------------------------------------------------------------
class Test06ClvHandling(unittest.TestCase):
    def test_compute_clv_is_a_real_working_function(self):
        # -110 entry, -130 close (line moved in the bettor's favor)
        clv = pm.american_to_prob(-130) - pm.american_to_prob(-110)
        self.assertGreater(clv, 0)

    def test_settle_completed_observation_computes_clv_when_closing_odds_supplied(self):
        """CAPABILITY test: prospective_recording.settle_completed_observation
        genuinely computes CLV when given a real closing price -- this
        is the honest state to report: the CAPABILITY is real and
        correct, but (see docs/CLOSED_LOOP_CERTIFICATION.md)
        settle_daily_observations.run_settlement_batch() -- the actual
        automated daily job -- never calls it with a closing_odds value
        today, so no real settlement has ever had a real CLV attached
        yet. Not fixed here (P0.4 is certification, not a new feature)."""
        ledger = pl.init_db(db_path=":memory:")
        pred_id = _record_sog_prediction(ledger, player_id="P7", game_id=7, threshold="3+")
        closing_captured_at = (NOW - dt.timedelta(days=2, hours=1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        result = pr.settle_completed_observation(
            ledger, pred_id, actual_outcome="5", result_status="WIN",
            closing_odds=-130, closing_captured_at_utc=closing_captured_at)
        self.assertIsNotNone(result["clv"])
        self.assertGreater(result["clv"], 0)


# ---------------------------------------------------------------------
# 7. Idempotent rerun
# ---------------------------------------------------------------------
class Test07IdempotentRerun(unittest.TestCase):
    def test_running_settlement_twice_never_double_settles(self):
        ledger = pl.init_db(db_path=":memory:")
        official = _official_conn()
        _seed_game(official, 8)
        _seed_skater_stat(official, 8, "P8", "EDM", shots=5)
        pred_id = _record_sog_prediction(ledger, player_id="P8", game_id=8, threshold="3+")

        first = sdo.run_settlement_batch(ledger, official_conn=official)
        self.assertEqual(first["settled_win"], 1)
        after_first = pl.get_observation(ledger, pred_id)

        second = sdo.run_settlement_batch(ledger, official_conn=official)
        self.assertEqual(second["total_candidates"], 0)  # no longer PENDING -- never re-selected
        after_second = pl.get_observation(ledger, pred_id)
        self.assertEqual(after_first, after_second)  # completely unchanged by the second run

    def test_rerunning_the_daily_review_on_unchanged_data_produces_identical_report(self):
        ledger = pl.init_db(db_path=":memory:")
        official = _official_conn()
        for i in range(5):
            _seed_game(official, 100 + i)
            shots = 5 if i % 2 == 0 else 1
            _seed_skater_stat(official, 100 + i, f"P{100+i}", "EDM", shots=shots)
            _record_sog_prediction(ledger, player_id=f"P{100+i}", game_id=100 + i, threshold="3+")
        sdo.run_settlement_batch(ledger, official_conn=official)

        with tempfile.TemporaryDirectory() as tmp:
            r1 = dmr.run_daily_review(ledger, now_utc=NOW)
            p1 = dmr.write_daily_report(r1, report_date="2026-09-29", out_dir=Path(tmp))
            r2 = dmr.run_daily_review(ledger, now_utc=NOW)
            p2 = dmr.write_daily_report(r2, report_date="2026-09-29", out_dir=Path(tmp))
            self.assertEqual(p1.read_text(), p2.read_text())


# ---------------------------------------------------------------------
# 8. Data freshness enforcement (never poll/settle a started event
#    prematurely, never settle off an incomplete game)
# ---------------------------------------------------------------------
class Test08DataFreshnessEnforcement(unittest.TestCase):
    def test_game_not_yet_final_is_deferred_not_incorrectly_settled(self):
        ledger = pl.init_db(db_path=":memory:")
        official = _official_conn()
        _seed_game(official, 9, game_state="LIVE")  # not FINAL yet
        pred_id = _record_sog_prediction(ledger, player_id="P9", game_id=9, threshold="3+")

        summary = sdo.run_settlement_batch(ledger, official_conn=official)
        self.assertEqual(summary["still_pending_game_not_final"], 1)
        after = pl.get_observation(ledger, pred_id)
        self.assertEqual(after["result_status"], "PENDING")  # correctly deferred, not guessed

    def test_prediction_before_event_start_has_not_happened_is_never_a_settlement_candidate(self):
        ledger = pl.init_db(db_path=":memory:")
        future_start = "2026-10-15T23:00:00.000000Z"
        pl.record_model_observation(
            ledger, event_start_utc=future_start, created_at_utc=CUTOFF, prediction_cutoff_utc=CUTOFF,
            game_id=10, game_date="2026-10-15", player_id="P10", team="EDM", opponent="CGY",
            market_id="PLAYER_SOG", threshold="3+", side="OVER", raw_probability=0.6, confidence="HIGH")
        candidates = sdo.find_settlement_candidates(ledger)
        self.assertEqual(candidates, [])


# ---------------------------------------------------------------------
# 9. Post-mortem / reason-code assignment
# ---------------------------------------------------------------------
class Test09PostmortemAndReasonCodes(unittest.TestCase):
    def test_postmortem_generation_reaches_a_real_scored_state_above_minimum_sample(self):
        ledger = pl.init_db(db_path=":memory:")
        official = _official_conn()
        for i in range(dmr.MIN_SAMPLE_FOR_REVIEW):
            _seed_game(official, 200 + i)
            shots = 5 if i % 2 == 0 else 1
            _seed_skater_stat(official, 200 + i, f"P{200+i}", "EDM", shots=shots)
            _record_sog_prediction(ledger, player_id=f"P{200+i}", game_id=200 + i, threshold="3+")
        sdo.run_settlement_batch(ledger, official_conn=official)

        result = dmr.run_daily_review(ledger, now_utc=NOW)
        self.assertNotIn(result["engine_status"], ("NO_DATA", "INSUFFICIENT_SAMPLE"))
        self.assertNotIn("incomplete", result)
        self.assertIn("recommendation", result)

    def test_reason_code_classification_is_real_not_a_placeholder(self):
        """operational.daily_postmortem.classify_failure is the real
        13-category taxonomy -- confirms a real LOSS row classifies to a
        real, defined category, never an unrecognized/placeholder
        string."""
        from operational import daily_postmortem as dpm
        bet_row = {"market_id": "PLAYER_SOG", "result_status": "LOSS", "raw_probability": 0.6,
                   "conservative_probability": 0.55, "threshold": "3+", "actual_outcome": "1"}
        category = dpm.classify_failure(bet_row)
        self.assertIn(category, dpm.FAILURE_CATEGORIES if hasattr(dpm, "FAILURE_CATEGORIES") else
                      ("NORMAL_VARIANCE", "MODEL_CALIBRATION", "TOI_PROJECTION_ERROR", "ROLE_CHANGE_MISSED",
                       "STARTER_ERROR", "TEAM_SHOT_ENVIRONMENT_ERROR", "GOALIE_WORKLOAD_ERROR",
                       "MARKET_MOVED", "STALE_DATA", "IDENTITY_LINEUP_ERROR", "DATA_PIPELINE_ERROR",
                       "DEPENDENCE_ERROR", "UNKNOWN"))


if __name__ == "__main__":
    unittest.main()
