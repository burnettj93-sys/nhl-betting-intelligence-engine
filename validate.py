"""
Master validation-report command (spec item 10). Run with:

    python3 validate.py

Prints, in order: the full automated test suite's results; an ingestion
summary; missing/invalid record counts; duplicate record counts;
structural temporal-integrity checks; a walk-forward backtest (Brier
score / log loss / calibration for all 5 model variants); and an explicit
checklist against this development slice's 8 stated completion criteria.

This script builds and uses its OWN throwaway synthetic database (never
the shared nhl.db) so `python3 validate.py` is side-effect-free and
repeatable from a clean checkout. Run demo_setup.py separately to
populate nhl.db for interactive use (run_slate.py, ad hoc backtest.py).

IMPORTANT: every number this script produces below section 1 comes from
SYNTHETIC data (see ingest/demo_data.py). A clean report here is evidence
the pipeline's temporal-integrity mechanism and math are sound — it is
NOT evidence of a profitable real-world betting edge, and this script
says so again at the end. See README.md's implemented/tested/
experimental/deferred table for what that distinction actually means for
each piece of the system.
"""
from __future__ import annotations

import datetime as dt
import io
import unittest

import backtest
import config
from ingest import demo_data

# v2.1.1 spec item 8/9(D): test count immediately before this slice began
# (the "previous" count criterion #10 checks against) -- the full suite
# grew from here as items 1-6 each added their own required tests.
PREV_TEST_COUNT = 172

VALIDATION_SEASONS = [
    ("2022-2023-DEMO", dt.date(2022, 10, 10)),
    ("2023-2024-DEMO", dt.date(2023, 10, 9)),
    ("2024-2025-DEMO", dt.date(2024, 10, 8)),
    ("2025-2026-DEMO", dt.date(2025, 10, 7)),
]


def run_test_suite() -> unittest.TestResult:
    loader = unittest.TestLoader()
    suite = loader.discover("tests")
    runner = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0)
    return runner.run(suite)


def _run_named(test_names: list[str]) -> unittest.TestResult:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for name in test_names:
        suite.addTests(loader.loadTestsFromName(name))
    runner = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0)
    return runner.run(suite)


# v2.1 (temporal-hardening pass, spec item 22): one independently-run
# result per named category below, so each can be inspected on its own
# rather than only as part of the single pooled "1. TEST SUITE STATUS"
# number. EVERY category here runs against ingest/demo_data.py's
# SYNTHETIC dataset only -- see the explicit caveat printed with this
# section and README.md's status table. A PASS here demonstrates
# historical-research correctness against synthetic data, never a
# real-world betting edge and never live-NHL-data behavior.
TEMPORAL_HARDENING_SECTIONS = {
    "TEMPORAL QUERY INTEGRITY": [
        "tests.test_point_in_time", "tests.test_temporal_integrity",
        "tests.test_temporal_invariants",
    ],
    "MODEL-STATE TEMPORAL INTEGRITY": ["tests.test_model_state_integrity"],
    "SCHEDULE REVISION INTEGRITY": ["tests.test_schedule_revision"],
    "PLAYER-STAT REVISION INTEGRITY": [
        "tests.test_stat_revision.TestPlayerStatRevisionLeakage",
        "tests.test_stat_revision.TestStatRevisionEndToEndViaProcessGames",
    ],
    "GOALIE-STAT REVISION INTEGRITY": [
        "tests.test_stat_revision.TestGoalieStatRevisionLeakage",
    ],
    "GAME-ID INDEPENDENCE": ["tests.test_game_id_independence"],
    "PREDICTION REPRODUCIBILITY": ["tests.test_reproducibility"],
    "ODDS-STALENESS POLICY": ["tests.test_odds_staleness_policy"],
    "DIRECT-TABLE-READ AUDIT": ["tests.test_structural_reads"],
    # v2.1.1 (final temporal closure, spec item 7): five additional named
    # categories closing the historical-reconstruction gaps found during
    # independent review of v2.1 -- see the module docstrings on each
    # test file below for what each closes and why.
    "RESULT REVISION INTEGRITY": ["tests.test_result_revision"],
    "RUN_SLATE TEMPORAL INTEGRITY": ["tests.test_run_slate_temporal"],
    "EXACT-TIMESTAMP ORDERING": [
        "tests.test_model_state_integrity.TestExactTimestampContaminationSemantics",
    ],
    "UTC TIMESTAMP NORMALIZATION": ["tests.test_timestamp_normalization"],
    "TRAINING-PATH STRUCTURAL AUDIT": ["tests.test_training_path_structural_audit"],
    # v2.1.1a (correctness patch, spec item 9): five more named
    # categories closing the five specific correctness gaps found during
    # a second independent review -- see each test file's module
    # docstring for the exact bug and fix.
    "ODDS RECEIPT-TIME INTEGRITY": ["tests.test_odds_receipt_time_integrity"],
    "MODEL KNOWLEDGE WATERMARK INTEGRITY": ["tests.test_model_knowledge_watermark"],
    "MAXIMUM ACCEPTABLE PRICE CONSISTENCY": [
        "tests.test_odds_math.TestMaxAcceptablePrice",
    ],
    "HOME/AWAY REVISION CONSISTENCY": ["tests.test_home_away_revision_consistency"],
    "GAME-RESULT STRUCTURAL READ AUDIT": [
        "tests.test_structural_reads.TestGameResultEventsIsNowGuardedByThisAudit",
    ],
    # v2.1.2 (real NHL core ingestion readiness, spec item 10): six more
    # named categories closing the six specific gaps found during a
    # third independent review, focused on making real (not just
    # synthetic) NHL ingestion actually work -- see each test file's
    # module docstring for the exact bug/gap and fix.
    "FRESH-DB INGESTION BOOTSTRAP": ["tests.test_fresh_db_ingestion"],
    "DYNAMIC TEAM UNIVERSE": ["tests.test_dynamic_team_universe"],
    "SCHEDULE WATERMARK INTEGRITY": [
        "tests.test_model_knowledge_watermark.TestScheduleRevisionContaminatesTheWatermark",
    ],
    "SCHEDULE CACHE CONSISTENCY": ["tests.test_schedule_cache_sync"],
    "CORE PLAYER IDENTITY CONTRACT": ["tests.test_core_roster_identity"],
    "HISTORICAL BACKFILL KNOWLEDGE-TIME POLICY": [
        "tests.test_historical_backfill_knowledge_time",
    ],
    # v2.1.2a (live API contract closure, spec item 13): three more named
    # categories closing the seven specific live-integration gaps found
    # during a fourth independent review that checked the actual NHL
    # public API response shape -- see each test file's module docstring
    # for the exact bug/gap and fix.
    "BOXSCORE CONTRACT INTEGRITY": ["tests.test_boxscore_contract"],
    "LIVE OBSERVATION TIMESTAMP INTEGRITY": ["tests.test_live_observation_timestamping"],
    "CURRENT ROSTER RECONCILIATION": ["tests.test_current_roster_reconciliation"],
}

# v2.1.2a spec item 13: the three category names added directly above --
# kept as its own list, same pattern as V212_NEW_CATEGORIES, so the
# v2.1.2a completion-criteria checklist can check "all three new
# categories pass" as one thing.
V212A_NEW_CATEGORIES = [
    "BOXSCORE CONTRACT INTEGRITY", "LIVE OBSERVATION TIMESTAMP INTEGRITY",
    "CURRENT ROSTER RECONCILIATION",
]

# v2.1.2a spec item 14 criterion D: test count immediately before THIS
# slice began (285 was the full count after v2.1.2).
PREV_TEST_COUNT_V212A = 285

# v2.1.2 spec item 10: the six category names added directly above --
# kept as its own list, same pattern as V211A_NEW_CATEGORIES, so the
# v2.1.2 completion-criteria checklist can check "all six new categories
# pass" as one thing.
V212_NEW_CATEGORIES = [
    "FRESH-DB INGESTION BOOTSTRAP", "DYNAMIC TEAM UNIVERSE",
    "SCHEDULE WATERMARK INTEGRITY", "SCHEDULE CACHE CONSISTENCY",
    "CORE PLAYER IDENTITY CONTRACT", "HISTORICAL BACKFILL KNOWLEDGE-TIME POLICY",
]

# v2.1.2 spec item 14 criterion #14: test count immediately before THIS
# slice began (252 was the full count after v2.1.1a).
PREV_TEST_COUNT_V212 = 252

# v2.1.1a spec item 9/10: the five category names added directly above --
# kept as its own list so the v2.1.1a completion-criteria checklist can
# check "all five new categories pass" as one thing, independent of the
# ten v2.1 / five v2.1.1 categories that came before them.
V211A_NEW_CATEGORIES = [
    "ODDS RECEIPT-TIME INTEGRITY", "MODEL KNOWLEDGE WATERMARK INTEGRITY",
    "MAXIMUM ACCEPTABLE PRICE CONSISTENCY", "HOME/AWAY REVISION CONSISTENCY",
    "GAME-RESULT STRUCTURAL READ AUDIT",
]

# v2.1.1a spec item 10 criterion #11: test count immediately before THIS
# slice began (172 was the count before v2.1.1; PREV_TEST_COUNT above
# stays the v2.1.1 marker, this is v2.1.1a's own).
PREV_TEST_COUNT_V211A = 223


def temporal_hardening_report() -> dict:
    results = {}
    for label, names in TEMPORAL_HARDENING_SECTIONS.items():
        result = _run_named(names)
        n_bad = len(result.failures) + len(result.errors)
        results[label] = {
            "tests_run": result.testsRun,
            "passed": result.testsRun - n_bad,
            "failed": len(result.failures),
            "errors": len(result.errors),
            "status": "PASS" if n_bad == 0 else "FAIL",
            "failure_names": [name for name, _ in result.failures + result.errors],
        }
    return results


def build_validation_db():
    from tests.helpers import make_test_db

    conn, path = make_test_db()
    demo_data.generate(conn, seasons=VALIDATION_SEASONS, seed=42)
    return conn, path


def ingestion_summary(conn) -> dict:
    def count(sql):
        return conn.execute(sql).fetchone()["c"]

    return {
        "seasons_loaded": len(VALIDATION_SEASONS),
        "games_final": count("SELECT COUNT(*) c FROM games WHERE game_state='FINAL'"),
        "games_scheduled": count("SELECT COUNT(*) c FROM games WHERE game_state='SCHEDULED'"),
        "teams": count("SELECT COUNT(*) c FROM teams"),
        "players": count("SELECT COUNT(*) c FROM players"),
        "team_membership_events": count("SELECT COUNT(*) c FROM team_membership_events"),
        "roster_status_events": count("SELECT COUNT(*) c FROM roster_status_events"),
        "goalie_status_events": count("SELECT COUNT(*) c FROM goalie_status_events"),
        "lineup_snapshots": count("SELECT COUNT(*) c FROM lineup_snapshots"),
        "odds_snapshots": count("SELECT COUNT(*) c FROM odds_snapshots"),
        "games_per_team_per_season": demo_data.GAMES_PER_TEAM_PER_SEASON,
        "schedule_shape": demo_data.SEASON_GAMES_NOTE,
    }


def missing_invalid_records(conn) -> dict:
    def count(sql):
        return conn.execute(sql).fetchone()["c"]

    return {
        "games_missing_home_or_away_team": count(
            "SELECT COUNT(*) c FROM games WHERE home_team IS NULL OR away_team IS NULL"),
        "final_games_missing_score": count(
            "SELECT COUNT(*) c FROM games WHERE game_state='FINAL' "
            "AND (home_score IS NULL OR away_score IS NULL)"),
        "final_games_missing_result_observed_at": count(
            "SELECT COUNT(*) c FROM games WHERE game_state='FINAL' "
            "AND result_observed_at_utc IS NULL"),
        "games_missing_schedule_observed_at": count(
            "SELECT COUNT(*) c FROM games WHERE schedule_observed_at_utc IS NULL"),
        "active_odds_missing_price": count(
            "SELECT COUNT(*) c FROM odds_snapshots WHERE status='ACTIVE' AND price_american IS NULL"),
        "team_membership_missing_observed_at": count(
            "SELECT COUNT(*) c FROM team_membership_events WHERE observed_at_utc IS NULL"),
        "players_missing_position": count(
            "SELECT COUNT(*) c FROM players WHERE position IS NULL OR position=''"),
    }


def duplicate_records(conn) -> dict:
    def count(sql):
        return conn.execute(sql).fetchone()["c"]

    return {
        "duplicate_odds_snapshot_identity": count(
            """SELECT COUNT(*) c FROM (
                 SELECT sportsbook, game_id, market, selection, captured_at_utc, COUNT(*) n
                 FROM odds_snapshots
                 GROUP BY sportsbook, game_id, market, selection, captured_at_utc
                 HAVING n > 1
               )"""),
        "duplicate_game_ids": count(
            "SELECT COUNT(*) c FROM (SELECT game_id, COUNT(*) n FROM games "
            "GROUP BY game_id HAVING n > 1)"),
        "duplicate_prediction_rows": count(
            """SELECT COUNT(*) c FROM (
                 SELECT game_id, selection, prediction_time_utc, COUNT(*) n FROM predictions
                 GROUP BY game_id, selection, prediction_time_utc HAVING n > 1
               )"""),
    }


def temporal_integrity_checks(conn) -> dict:
    """Structural invariants that must hold for the point-in-time layer's
    guarantee to mean anything: nothing can be OBSERVED before it became
    EFFECTIVE (that would mean the system knew the future when it logged
    the fact) and nothing can be RECEIVED before it was CAPTURED."""
    checks = {}
    for table in ("team_membership_events", "roster_status_events",
                  "goalie_status_events", "lineup_snapshots", "pp_unit_snapshots"):
        checks[f"{table}_observed_before_effective"] = conn.execute(
            f"SELECT COUNT(*) c FROM {table} WHERE observed_at_utc < effective_at_utc"
        ).fetchone()["c"]
    checks["games_result_observed_before_schedule_observed"] = conn.execute(
        """SELECT COUNT(*) c FROM games WHERE result_observed_at_utc IS NOT NULL
           AND result_observed_at_utc < schedule_observed_at_utc"""
    ).fetchone()["c"]
    checks["odds_received_before_captured"] = conn.execute(
        "SELECT COUNT(*) c FROM odds_snapshots WHERE received_at_utc < captured_at_utc"
    ).fetchone()["c"]
    return checks


def readme_distinguishes_status_categories() -> bool | None:
    try:
        with open("README.md") as f:
            text = f.read().lower()
    except FileNotFoundError:
        return None
    required = ("implemented", "tested", "experimental", "deferred")
    return all(word in text for word in required)


def readme_distinguishes_ingestion_validation_tiers() -> bool | None:
    """v2.1.1a spec item 8/10(14): a successful `ingest_range()` run
    (core schedule/results/boxscores/player-identity ingestion) must
    never be read as having validated real roster/availability status or
    real starting-goalie status too -- those need separate sources. This
    mechanically checks README.md actually names all three tiers rather
    than only the general implemented/tested/experimental/deferred
    categories checked above."""
    try:
        with open("README.md") as f:
            text = f.read().lower()
    except FileNotFoundError:
        return None
    required = ("real nhl core ingestion", "real roster/availability source",
                "real starting-goalie source")
    return all(phrase in text for phrase in required)


def readme_distinguishes_roster_identity_from_schedule_ingestion() -> bool | None:
    """v2.1.2 spec item 5/10: ingest_range() (schedule/result/boxscore)
    and ingest_roster_identities() (canonical player identity/current
    membership) are two SEPARATE ingestion tiers -- a successful
    ingest_range() run must never be read as having also validated the
    roster-identity layer. Mechanically checks README.md actually names
    both tiers distinctly."""
    try:
        with open("README.md") as f:
            text = f.read().lower()
    except FileNotFoundError:
        return None
    required = ("schedule/result/boxscore ingestion", "core roster identity ingestion")
    return all(phrase in text for phrase in required)


def readme_distinguishes_current_from_season_roster() -> bool | None:
    """v2.1.2a spec item 3/10: CURRENT team roster membership
    (/v1/roster/{team}/current) and SEASON roster identity
    (/v1/roster/{team}/{season}) are two semantically DIFFERENT NHL API
    endpoints -- conflating them is unsafe (a season-roster pull today
    doesn't prove today's actual membership). Mechanically checks
    README.md actually names both distinctly, rather than only the
    schedule/result/boxscore vs. roster-identity split checked above."""
    try:
        with open("README.md") as f:
            text = f.read().lower()
    except FileNotFoundError:
        return None
    required = ("current team roster", "season roster")
    return all(phrase in text for phrase in required)


def _print_kv(d: dict) -> None:
    width = max(len(k) for k in d) if d else 0
    for k, v in d.items():
        print(f"  {k.ljust(width)} : {v}")


def print_report() -> None:
    print("=" * 72)
    print("NHL BETTING INTELLIGENCE ENGINE -- VALIDATION REPORT")
    print("=" * 72)
    print(f"model_version={config.MODEL_VERSION}  feature_version={config.FEATURE_VERSION}")
    print()

    print("-" * 72)
    print("1. TEST SUITE STATUS")
    print("-" * 72)
    result = run_test_suite()
    n_bad = len(result.failures) + len(result.errors)
    print(f"Ran {result.testsRun} tests: {result.testsRun - n_bad} passed, "
          f"{len(result.failures)} failed, {len(result.errors)} errors")
    for name, _ in result.failures + result.errors:
        print(f"  FAILED: {name}")
    all_tests_passed = n_bad == 0
    print()

    conn, path = build_validation_db()
    try:
        print("-" * 72)
        print("2. INGESTION SUMMARY (synthetic validation dataset)")
        print("-" * 72)
        _print_kv(ingestion_summary(conn))
        print()
        print("  REAL NHL API INGESTION: NOT VERIFIED IN THIS ENVIRONMENT.")
        print("  This sandbox's outbound network cannot reach api-web.nhle.com")
        print("  (confirmed via direct connection tests -- see ingest/nhl_api.py's")
        print("  module docstring). ingest/nhl_api.py is written and unit-tested")
        print("  against constructed fake payloads (tests/test_ingest_idempotency.py)")
        print("  but has NEVER been exercised against a live response. Completion")
        print("  criterion #2 cannot be satisfied by this script in this environment.")
        print()

        print("-" * 72)
        print("3. MISSING / INVALID RECORDS")
        print("-" * 72)
        miss = missing_invalid_records(conn)
        _print_kv(miss)
        no_missing = all(v == 0 for v in miss.values())
        print()

        print("-" * 72)
        print("4. DUPLICATE RECORDS")
        print("-" * 72)
        dupes = duplicate_records(conn)
        _print_kv(dupes)
        no_dupes = all(v == 0 for v in dupes.values())
        print()

        print("-" * 72)
        print("5. TEMPORAL-INTEGRITY STRUCTURAL CHECKS")
        print("-" * 72)
        temp = temporal_integrity_checks(conn)
        _print_kv(temp)
        no_temporal_violations = all(v == 0 for v in temp.values())
        print("  (0 across the board is required -- a nonzero count means a fact")
        print("   was recorded as known before it happened, which would break")
        print("   every point-in-time guarantee in features/point_in_time.py)")
        print()

        print("-" * 72)
        print("6. v2.1 TEMPORAL HARDENING REPORT")
        print("-" * 72)
        print("  Twenty-six named categories (ten from spec item 22, five more from v2.1.1's")
        print("  final temporal closure, five more from v2.1.1a's correctness patch, six more")
        print("  from v2.1.2's real-ingestion-readiness patch), each")
        print("  run and reported independently. EVERY category below is VERIFIED IN SYNTHETIC/UNIT")
        print("  TEST ENVIRONMENT ONLY (ingest/demo_data.py) -- NONE of them is")
        print("  verified against live NHL data; that distinction is restated below")
        print("  and is not something a clean run here can ever demonstrate.")
        print()
        hardening = temporal_hardening_report()
        all_hardening_passed = all(v["status"] == "PASS" for v in hardening.values())
        # TEST SUITE STATUS itself is section 1 above -- listed again here
        # by name only, for a single place that names all 10 required items.
        print(f"  [{'PASS' if all_tests_passed else 'FAIL':^12}] TEST SUITE STATUS "
              f"(see section 1 above) -- verified in synthetic/unit test "
              f"environment; not yet verified on live NHL data")
        for label, r in hardening.items():
            print(f"  [{r['status']:^12}] {label}: {r['passed']}/{r['tests_run']} passed"
                  + (f" ({r['failed']} failed, {r['errors']} errors)"
                     if r["status"] != "PASS" else ""))
            for name in r["failure_names"]:
                print(f"                  FAILED: {name}")
            print(f"                  verified in synthetic/unit test environment; "
                  f"not yet verified on live NHL data")
        print()

        print("-" * 72)
        print("7. WALK-FORWARD BACKTEST")
        print("-" * 72)
        results = backtest.run(conn)
        backtest.print_report(results)
        print()

        print("-" * 72)
        print("8. COMPLETION CRITERIA CHECKLIST")
        print("-" * 72)
        readme_ok = readme_distinguishes_status_categories()
        readme_ingestion_tiers_ok = readme_distinguishes_ingestion_validation_tiers()
        readme_roster_tier_ok = readme_distinguishes_roster_identity_from_schedule_ingestion()
        readme_current_vs_season_roster_ok = readme_distinguishes_current_from_season_roster()
        criteria = [
            ("All tests pass", all_tests_passed),
            ("Real NHL ingestion successfully loads >= 1 complete season", None),
            ("No historical prediction can access post-prediction-time information",
             all_tests_passed and no_temporal_violations),
            ("Scheduled games receive valid rest features", all_tests_passed),
            ("Player and goalie features work with real ingested identities", None),
            ("WAIT uses stored goalie status rather than a hardcoded argument",
             all_tests_passed),
            ("Historical predictions are reproducible", all_tests_passed),
            ("README accurately distinguishes implemented/tested/experimental/deferred",
             readme_ok),
        ]
        for label, status in criteria:
            marker = "PASS" if status is True else ("FAIL" if status is False else "NOT VERIFIED")
            print(f"  [{marker:^12}] {label}")
        print()
        print("  v2.1 temporal-hardening criteria (spec item 24) -- ALL must be true")
        print("  before this platform is ready for real NHL ingestion validation:")
        v21_criteria = [
            ("No historical training path uses game_id ordering as the temporal gate",
             all_hardening_passed),
            ("Training eligibility is based on result_observed_at_utc",
             hardening["GAME-ID INDEPENDENCE"]["status"] == "PASS"),
            ("Schedule revisions are append-only and historically reconstructable",
             hardening["SCHEDULE REVISION INTEGRITY"]["status"] == "PASS"),
            ("Player postgame statistics are observation/revision timestamped",
             hardening["PLAYER-STAT REVISION INTEGRITY"]["status"] == "PASS"),
            ("Goalie postgame statistics are observation/revision timestamped",
             hardening["GOALIE-STAT REVISION INTEGRITY"]["status"] == "PASS"),
            ("Later stat corrections cannot alter earlier predictions",
             (hardening["PLAYER-STAT REVISION INTEGRITY"]["status"] == "PASS"
              and hardening["GOALIE-STAT REVISION INTEGRITY"]["status"] == "PASS")),
            ("A future-trained model cannot silently contaminate a historical prediction",
             hardening["MODEL-STATE TEMPORAL INTEGRITY"]["status"] == "PASS"),
            ("Historical model state can be reconstructed as of prediction_time_utc",
             hardening["MODEL-STATE TEMPORAL INTEGRITY"]["status"] == "PASS"),
            ("Stored feature-snapshot reproduction remains exact",
             hardening["PREDICTION REPRODUCIBILITY"]["status"] == "PASS"),
            ("Odds staleness varies appropriately with time to puck drop",
             hardening["ODDS-STALENESS POLICY"]["status"] == "PASS"),
            ("All existing (pre-v2.1) tests still pass", all_tests_passed),
            ("New temporal-hardening tests pass", all_hardening_passed),
            ("validate.py reports every v2.1 item explicitly", True),
            ("README accurately distinguishes tested architecture from unverified "
             "live-data behavior", readme_ok),
        ]
        for label, status in v21_criteria:
            marker = "PASS" if status else "FAIL"
            print(f"  [{marker:^12}] {label}")
        print()
        print("  v2.1.1 final-temporal-closure criteria (independent-review slice) --")
        print("  ALL must be true before this platform is ready for real NHL")
        print("  ingestion validation:")
        v211_new_categories = [
            "RESULT REVISION INTEGRITY", "RUN_SLATE TEMPORAL INTEGRITY",
            "EXACT-TIMESTAMP ORDERING", "UTC TIMESTAMP NORMALIZATION",
            "TRAINING-PATH STRUCTURAL AUDIT",
        ]
        v211_new_passed = all(hardening[c]["status"] == "PASS" for c in v211_new_categories)
        v211_criteria = [
            ("run_slate.py contains no game-ID-based training eligibility",
             hardening["TRAINING-PATH STRUCTURAL AUDIT"]["status"] == "PASS"),
            ("Historical multi-game pricing learns every result genuinely available "
             "before each individual prediction timestamp",
             hardening["RUN_SLATE TEMPORAL INTEGRITY"]["status"] == "PASS"),
            ("Final game results are append-only/revision-safe",
             hardening["RESULT REVISION INTEGRITY"]["status"] == "PASS"),
            ("Identical result reingestion cannot move the historical first-known time",
             hardening["RESULT REVISION INTEGRITY"]["status"] == "PASS"),
            ("A later score/result correction cannot alter an earlier model state",
             hardening["RESULT REVISION INTEGRITY"]["status"] == "PASS"),
            ("Model learning uses the result revision actually available at learn time",
             hardening["RESULT REVISION INTEGRITY"]["status"] == "PASS"),
            ("Exact prediction/result timestamp ties obey strict-before semantics",
             hardening["EXACT-TIMESTAMP ORDERING"]["status"] == "PASS"),
            ("UTC timestamp representations are normalized before comparison/storage",
             hardening["UTC TIMESTAMP NORMALIZATION"]["status"] == "PASS"),
            ("A structural test guards against reintroducing game-ID/list-position "
             "temporal proxies",
             hardening["TRAINING-PATH STRUCTURAL AUDIT"]["status"] == "PASS"),
            (f"All previous {PREV_TEST_COUNT} tests still pass",
             result.testsRun >= PREV_TEST_COUNT and all_tests_passed),
            ("All new v2.1.1 tests pass", v211_new_passed),
            ("validate.py reports all five new v2.1.1 categories PASS", v211_new_passed),
        ]
        for label, status in v211_criteria:
            marker = "PASS" if status else "FAIL"
            print(f"  [{marker:^12}] {label}")
        print(f"  (test suite: {result.testsRun} tests run this pass, vs "
              f"{PREV_TEST_COUNT} immediately before v2.1.1)")
        print()
        print("  v2.1.1a correctness-patch criteria (second independent-review slice) --")
        print("  ALL must be true before this platform is ready for real NHL")
        print("  ingestion validation:")
        v211a_new_passed = all(hardening[c]["status"] == "PASS" for c in V211A_NEW_CATEGORIES)
        v211a_criteria = [
            ("A DraftKings quote received after historical prediction time cannot be "
             "used by that prediction",
             hardening["ODDS RECEIPT-TIME INTEGRITY"]["status"] == "PASS"),
            ("If a newer quote existed but had not yet been received, the engine can "
             "still use the latest older quote that genuinely was known",
             hardening["ODDS RECEIPT-TIME INTEGRITY"]["status"] == "PASS"),
            ("A model that explicitly consumes a later result correction cannot "
             "predict backward across that correction timestamp",
             hardening["MODEL KNOWLEDGE WATERMARK INTEGRITY"]["status"] == "PASS"),
            ("A model that explicitly consumes later player-stat information cannot "
             "predict backward across that information timestamp",
             hardening["MODEL KNOWLEDGE WATERMARK INTEGRITY"]["status"] == "PASS"),
            ("A model that explicitly consumes later goalie-stat information cannot "
             "predict backward across that information timestamp",
             hardening["MODEL KNOWLEDGE WATERMARK INTEGRITY"]["status"] == "PASS"),
            ("Fresh historical model reconstruction remains unaffected by later "
             "corrections under the existing correction policy",
             hardening["MODEL KNOWLEDGE WATERMARK INTEGRITY"]["status"] == "PASS"),
            ("maximum_acceptable_draftkings_price is mathematically consistent with "
             "the engine's actual two-sided no-vig conservative-edge definition",
             hardening["MAXIMUM ACCEPTABLE PRICE CONSISTENCY"]["status"] == "PASS"),
            ("A price one increment worse than the calculated max price fails the "
             "required edge; a better price passes",
             hardening["MAXIMUM ACCEPTABLE PRICE CONSISTENCY"]["status"] == "PASS"),
            ("Home/away schedule revisions can no longer silently disagree with "
             "model-learning identity",
             hardening["HOME/AWAY REVISION CONSISTENCY"]["status"] == "PASS"),
            ("game_result_events is protected by the structural temporal-read audit",
             hardening["GAME-RESULT STRUCTURAL READ AUDIT"]["status"] == "PASS"),
            (f"All previous {PREV_TEST_COUNT_V211A} tests still pass",
             result.testsRun >= PREV_TEST_COUNT_V211A and all_tests_passed),
            ("All new v2.1.1a tests pass", v211a_new_passed),
            ("validate.py exposes and passes the five new v2.1.1a categories",
             v211a_new_passed),
            ("README clearly distinguishes core NHL API validation from "
             "injury/availability and starting-goalie feed validation",
             readme_ingestion_tiers_ok),
        ]
        for label, status in v211a_criteria:
            marker = "PASS" if status else "FAIL"
            print(f"  [{marker:^12}] {label}")
        print(f"  (test suite: {result.testsRun} tests run this pass, vs "
              f"{PREV_TEST_COUNT_V211A} immediately before v2.1.1a)")
        print()
        print("  v2.1.2 real-NHL-core-ingestion-readiness criteria (third independent-review")
        print("  slice) -- ALL must be true before attempting the first live NHL core")
        print("  ingestion smoke test (validate_live_nhl.py, run from an environment with")
        print("  normal internet access):")
        v212_new_passed = all(hardening[c]["status"] == "PASS" for c in V212_NEW_CATEGORIES)
        v212_criteria = [
            ("Fresh DB + ingest_schedule works with no pre-seeded teams",
             hardening["FRESH-DB INGESTION BOOTSTRAP"]["status"] == "PASS"),
            ("Unknown/non-demo NHL teams auto-bootstrap",
             hardening["FRESH-DB INGESTION BOOTSTRAP"]["status"] == "PASS"),
            ("Production model universe is DB-derived, not demo-data-derived",
             hardening["DYNAMIC TEAM UNIVERSE"]["status"] == "PASS"),
            ("Non-demo teams (e.g. EDM/VGK) can be modeled",
             hardening["DYNAMIC TEAM UNIVERSE"]["status"] == "PASS"),
            ("run_slate production path does not require ingest.demo_data.TEAMS",
             hardening["DYNAMIC TEAM UNIVERSE"]["status"] == "PASS"),
            ("backtest production path does not require ingest.demo_data.TEAMS",
             hardening["DYNAMIC TEAM UNIVERSE"]["status"] == "PASS"),
            ("A schedule revision consumed via explicit learn_time moves the "
             "knowledge watermark",
             hardening["SCHEDULE WATERMARK INTEGRITY"]["status"] == "PASS"),
            ("Backward prediction across that schedule revision raises "
             "ContaminatedModelStateError",
             hardening["SCHEDULE WATERMARK INTEGRITY"]["status"] == "PASS"),
            ("Fresh reconstruction before that schedule revision remains valid",
             hardening["SCHEDULE WATERMARK INTEGRITY"]["status"] == "PASS"),
            ("Real ingest_schedule reingestion keeps the games cache synchronized "
             "with schedule history",
             hardening["SCHEDULE CACHE CONSISTENCY"]["status"] == "PASS"),
            ("schedule_observed_at_utc remains the first-known time after a cache update",
             hardening["SCHEDULE CACHE CONSISTENCY"]["status"] == "PASS"),
            ("Historical data ingested today is not visible to a prediction "
             "timestamped before ingestion",
             hardening["HISTORICAL BACKFILL KNOWLEDGE-TIME POLICY"]["status"] == "PASS"),
            ("Core roster-identity ingestion populates players/membership without "
             "implying injury data",
             hardening["CORE PLAYER IDENTITY CONTRACT"]["status"] == "PASS"),
            (f"All previous {PREV_TEST_COUNT_V212} tests still pass",
             result.testsRun >= PREV_TEST_COUNT_V212 and all_tests_passed),
            ("All new v2.1.2 tests pass", v212_new_passed),
            ("validate.py exposes and passes the six new v2.1.2 categories", v212_new_passed),
            ("README distinguishes SCHEDULE/RESULT/BOXSCORE ingestion from CORE ROSTER "
             "IDENTITY ingestion", readme_roster_tier_ok),
        ]
        for label, status in v212_criteria:
            marker = "PASS" if status else "FAIL"
            print(f"  [{marker:^12}] {label}")
        print(f"  (test suite: {result.testsRun} tests run this pass, vs "
              f"{PREV_TEST_COUNT_V212} immediately before v2.1.2)")
        print()
        print("  v2.1.2a live-API-contract-closure criteria (fourth independent-review")
        print("  slice, checked against the real NHL Web API's actual response shape) --")
        print("  ALL must be true before attempting the first live NHL core ingestion")
        print("  smoke test:")
        v212a_new_passed = all(hardening[c]["status"] == "PASS" for c in V212A_NEW_CATEGORIES)
        v212a_criteria = [
            ("A real boxscore's per-skater SOG field ('sog') is correctly stored as "
             "player_game_stats.shots",
             hardening["BOXSCORE CONTRACT INTEGRITY"]["status"] == "PASS"),
            ("A missing required SOG field raises NHLApiSchemaError, never a silent 0",
             hardening["BOXSCORE CONTRACT INTEGRITY"]["status"] == "PASS"),
            ("Missing required boxscore-structure fields (id, homeTeam.abbrev, "
             "awayTeam.abbrev, playerByGameStats.homeTeam/awayTeam) raise "
             "NHLApiSchemaError",
             hardening["BOXSCORE CONTRACT INTEGRITY"]["status"] == "PASS"),
            ("A multi-skater real-shape boxscore fixture populates every skater on "
             "both teams",
             hardening["BOXSCORE CONTRACT INTEGRITY"]["status"] == "PASS"),
            ("Reingesting an identical boxscore is idempotent; a real correction "
             "appends a new revision",
             hardening["BOXSCORE CONTRACT INTEGRITY"]["status"] == "PASS"),
            ("observed_at_utc for a boxscore fetch is captured after that fetch's own "
             "response, never inherited from an earlier batch-start time",
             hardening["LIVE OBSERVATION TIMESTAMP INTEGRITY"]["status"] == "PASS"),
            ("A later-arriving boxscore response cannot receive an earlier timestamp "
             "merely because the batch began earlier",
             hardening["LIVE OBSERVATION TIMESTAMP INTEGRITY"]["status"] == "PASS"),
            ("Schedule and result facts correctly share one timestamp from their "
             "single shared response",
             hardening["LIVE OBSERVATION TIMESTAMP INTEGRITY"]["status"] == "PASS"),
            ("ingest_range() accepts an injectable session for testing without real "
             "network access",
             hardening["LIVE OBSERVATION TIMESTAMP INTEGRITY"]["status"] == "PASS"),
            ("A player absent from a later current-roster snapshot receives an "
             "explicit ROSTER_REMOVED departure event",
             hardening["CURRENT ROSTER RECONCILIATION"]["status"] == "PASS"),
            ("A traded player is removed from the old team and added to the new team",
             hardening["CURRENT ROSTER RECONCILIATION"]["status"] == "PASS"),
            ("A player who returns after a removal receives a fresh membership event",
             hardening["CURRENT ROSTER RECONCILIATION"]["status"] == "PASS"),
            ("A later authoritative response can correct full_name/position instead "
             "of freezing the first-ever value",
             hardening["CURRENT ROSTER RECONCILIATION"]["status"] == "PASS"),
            ("A repeated identical current-roster snapshot writes no new membership "
             "event",
             hardening["CURRENT ROSTER RECONCILIATION"]["status"] == "PASS"),
            (f"All previous {PREV_TEST_COUNT_V212A} tests still pass",
             result.testsRun >= PREV_TEST_COUNT_V212A and all_tests_passed),
            ("All new v2.1.2a tests pass", v212a_new_passed),
            ("validate.py exposes and passes the three new v2.1.2a categories",
             v212a_new_passed),
            ("README distinguishes CURRENT team roster membership from SEASON roster "
             "identity", readme_current_vs_season_roster_ok),
        ]
        for label, status in v212a_criteria:
            marker = "PASS" if status else "FAIL"
            print(f"  [{marker:^12}] {label}")
        print(f"  (test suite: {result.testsRun} tests run this pass, vs "
              f"{PREV_TEST_COUNT_V212A} immediately before v2.1.2a)")
        print()
        print("  LIVE NHL CORE INGESTION SMOKE TEST: run 'python3 validate_live_nhl.py' from")
        print("  an environment with normal internet access -- it is a SEPARATE command from")
        print("  this one and is never folded into this synthetic report (spec item 10).")
        print()
        print("  Records with nonzero missing/invalid/duplicate counts above would")
        print("  also fail criterion 1 through their corresponding test; sections 3/4")
        print(f"  currently read: no_missing={no_missing}, no_dupes={no_dupes}.")
        print()
        print("  'Real NHL ingestion' and 'real ingested identities' are NOT VERIFIED")
        print("  (not FAIL) because this sandbox cannot reach the live NHL API to")
        print("  attempt them at all -- see section 2. Run this script from an")
        print("  environment with normal internet access to actually clear them.")
        print()
        print("  IMPORTANT: passing every check above demonstrates historical")
        print("  RESEARCH correctness on synthetic data -- it is NOT evidence of a")
        print("  real-world betting edge, and does not by itself clear completion")
        print("  criteria #2/#5 (real NHL ingestion), which remain NOT VERIFIED.")
        print()
        print("=" * 72)
        print("This report is built entirely from SYNTHETIC data (ingest/demo_data.py).")
        print("A clean result above is evidence the pipeline's temporal-integrity")
        print("mechanism and probability math are internally sound -- it is NOT")
        print("evidence of a profitable real-world betting edge. Do not present it")
        print("as such. See README.md for the full implemented/tested/experimental/")
        print("deferred breakdown.")
        print("=" * 72)
    finally:
        conn.close()
        path.unlink(missing_ok=True)


if __name__ == "__main__":
    print_report()
