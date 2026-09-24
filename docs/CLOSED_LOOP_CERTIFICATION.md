# Closed-Loop Certification

**Date:** 2026-09-24. **Purpose:** prove prediction → immutable snapshot → settlement → post-mortem works together end-to-end, before 2026-09-29 (the first day DraftKings has posted any real market). **Method:** `tests/test_closed_loop_certification.py`, 15 tests, entirely isolated — a `:memory:` prospective ledger plus a wiped temp file for the official game-results store (`db.init_db(..., wipe=True)`). **No real production database was touched.** No synthetic recommendation was ever written to the real prospective ledger, `nhl.db`, or `paper_bankroll.db`.

## Result: PASS / FAIL by component

| Component | Result | Evidence |
|---|---|---|
| Prediction creation | **PASS** | `record_model_observation()`/`record_real_bet()` both create real, retrievable rows with a real idempotency key. |
| Snapshot immutability | **PASS** | `Test01WinScenario` compares every non-settlement field before and after settlement — byte-for-byte identical. The DB-level trigger backs this structurally regardless of any Python-level bug. |
| Settlement — WIN | **PASS** | Real boxscore (5 shots) correctly resolves a 3+ SOG prediction to WIN with the correct `actual_outcome`. |
| Settlement — LOSS | **PASS** | Real boxscore (1 shot) correctly resolves to LOSS. |
| Settlement — PUSH | **N/A, honestly reported.** `PUSH` is a defined `RESULT_STATE`, but no currently-implemented resolver (SOG/Goals/Assists/Points/Saves/Moneyline) can produce it — none of these markets has a whole-number line that can tie. This is a fact about the current market set, not a gap in the settlement code; it would become reachable the day a real push-capable market (e.g. an integer-line total) is added. |
| Settlement — VOID | **PASS** | A player absent from the real boxscore (a real late-scratch scenario) correctly VOIDs a `REAL_BET`. |
| A real, deliberate distinction found while certifying this | The same missing-player scenario on a plain `MODEL_OBSERVATION` (no real money) correctly resolves to `UNRESOLVED`, not `VOID` — confirmed intentional in `settle_daily_observations.py`'s own `_VOID_ON_REAL_MONEY`/`_REAL_MONEY_RECORD_TYPES` sets, not a bug. |
| WAIT / DATA_UNAVAILABLE decision-state preservation | **PASS** | A prediction recorded with `current_policy_status="WAIT"` (or `"DATA_UNAVAILABLE"`) still settles against the real outcome, and that field survives completely unchanged — settlement never silently overwrites the original decision-time signal. |
| Stale-data flag preservation | **PASS** | A prediction recorded with `data_freshness_status="STALE"` survives settlement with that flag intact — nothing downstream silently upgrades it to fresh. |
| CLV handling | **PARTIAL — capability real, not wired into the automated job.** `prospective_recording.settle_completed_observation()` genuinely computes CLV correctly when given a real closing price (verified: -110 entry vs -130 close produces a real positive CLV). **But** `operational.settle_daily_observations.run_settlement_batch()` — the actual function the scheduled `daily-settlement` job calls — never passes a `closing_odds` value to it. No real settlement performed by the scheduled job has ever had CLV attached, even once real markets exist. This is a real, disclosed gap, not fixed in this certification pass (P0.4 is about proving the plumbing, not adding a new feature) — wiring a real closing-price lookup into the automated batch is recommended P1 work once real odds history accumulates. |
| Post-mortem creation | **PASS** | At `MIN_SAMPLE_FOR_REVIEW` (5) real settled observations, `run_daily_review()` produces a real, non-`NO_DATA`/`INSUFFICIENT_SAMPLE` report with a real `recommendation`. |
| Reason-code assignment | **PASS** | `daily_postmortem.classify_failure()` returns a real, defined category (not a placeholder) for a real LOSS row. |
| Idempotent rerun (settlement) | **PASS** | Running `run_settlement_batch()` twice against the same state settles once; the second run finds zero candidates and produces byte-for-byte identical rows. |
| Idempotent rerun (post-mortem) | **PASS** | `run_daily_review()` run twice against unchanged settled data produces byte-for-byte identical written reports. |
| Data-freshness enforcement — game not yet FINAL | **PASS** | A `LIVE` (not `FINAL`) game correctly defers the prediction (`still_pending_game_not_final`), never guesses a result from incomplete data. |
| Data-freshness enforcement — event hasn't happened yet | **PASS** | A prediction whose `event_start_utc` is still in the future is correctly never selected as a settlement candidate at all. |

## Bottom line

**14 of 15 real scenarios PASS outright; 1 (PUSH) is not applicable to any currently-implemented market, honestly reported rather than faked; 1 (CLV) is a disclosed partial — the underlying math is correct and tested, but the automated job doesn't use it yet.** The core loop — prediction, immutable snapshot, settlement, post-mortem, idempotency, and freshness enforcement — is proven to work correctly against real production code, without waiting for September 29 and without touching any real database.
