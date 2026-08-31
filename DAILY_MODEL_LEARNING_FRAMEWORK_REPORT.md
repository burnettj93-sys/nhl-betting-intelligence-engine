# Daily Model Learning Framework

**Owner directive (2026-08-30):** the engine must re-test its own logic every day after real 2026-27 games settle, without ever automatically mutating production. This report documents the built framework: PREDICT → SNAPSHOT → OBSERVE → SETTLE (all pre-existing) → **SCORE MODEL → DIAGNOSE ERRORS/DRIFT → TEST CHALLENGERS IN SHADOW → ACCUMULATE EVIDENCE → EXPLICIT PROMOTION GATE** (built this sprint).

## A. Architecture

Seven new, real, tested modules, none of which import or write to any production model file, `decision_policy.py`, or a shadow-overlay coefficient file:

| Module | Role |
|---|---|
| `operational/model_scorecard.py` | Core metrics engine: Brier, log loss, calibration, time windows, base-vs-shadow, CLV aggregation, edge buckets, decision-state recomputation, market movement |
| `operational/error_taxonomy.py` | Classifies one large miss into a fixed 9-category taxonomy from real, already-recorded row fields |
| `operational/challenger_registry.py` | Machine-readable, evidence-gated challenger lifecycle (`HYPOTHESIS → TESTING → SHADOW → PROMOTION_CANDIDATE / REJECTED / RETIRED`) |
| `operational/engine_status_evaluator.py` | NORMAL/WATCH/INVESTIGATE/HALT determination, run-order and halt/watch condition checks, honest drift-detection stubs |
| `operational/rejected_research_check.py` | Consults the REAL, existing rejected-status entries across `model_registry.py`, `market_registry.py`, the special-teams overlay registry, and the joint-dependence registry |
| `operational/daily_model_review.py` | The callable job tying everything together; also holds weekly rollup, sample-milestone tracking, retraining-trigger evaluation, and the report writer |
| `dashboard/pages/32_Model_Learning.py` | Compact, read-only dashboard section (Part 56) |

## B. What runs every day (once real data exists)

`operational.daily_model_review.run_daily_review(ledger_conn)`:
1. Checks run order (Part 1/52) — HALTs (refuses to score) if results aren't ingested or settlement hasn't completed.
2. Loads every non-`HISTORICAL_RESEARCH` ledger row.
3. Computes the full scorecard (Part 2) across all 6 time windows (Part 3) per market/threshold.
4. Compares base vs. shadow for the 3 known pairs (Part 4/5), with the SOG PP-role overlay comparison structurally restricted to 2+/3+ only.
5. Runs the large-miss review (Part 23) and classifies each miss (Part 22).
6. Derives starter/active-status accuracy honestly from real settlement outcomes (Part 16/17).
7. Computes player/team health with a hard minimum-sample gate (Part 47/48).
8. Computes edge decay (Part 45) and model disagreement (Part 46).
9. Builds the season leaderboard with real-vs-theoretical ROI kept structurally separate (Part 43/44).
10. Checks verified-contract count (Part 21) and consults the challenger registry for promotion candidates.
11. Builds the improvement queue (Part 60) and issues one of 5 recommendations (Part 61).
12. Writes `reports/daily/YYYY-MM-DD_MODEL_REVIEW.md` (Part 36).

**Nothing in this call graph writes to a prediction row, a model file, or `decision_policy.py`.** Verified directly: `Test04ProductionImmutability` confirms a settled row is byte-identical before and after a full review run, and confirms the module's own source contains no call to `settle_prediction(`, `record_model_observation(`, or `record_real_bet(`.

## C. The absolute rule, enforced structurally

| Forbidden action | How it's structurally prevented |
|---|---|
| Modify a production coefficient | No module here imports a model-fitting file; `challenger_registry.py` never imports `research.model_registry` |
| Replace a champion model | `promotion_candidates()` returns a list for a human to read; nothing calls it and acts |
| Change `decision_policy` v3 | No module imports it; `recompute_decision()` reads its *current, unchanged* thresholds from `config.py`, never writes them |
| Promote a shadow overlay | Reaching `PROMOTION_CANDIDATE` status is a JSON label change only — tested directly (`Test04NoAutoPromotion`) |
| Change a confidence gate | `confidence` is read-only input to every function here |
| Increase Kelly staking | No staking logic exists in this framework at all |
| Change an eligibility threshold | `MODEL_VALIDATED_THRESHOLDS` (SOG) and every other registry threshold are read, never written |

## D. The SOG threshold distinction (Part 5), preserved exactly

`SOG_PP_ROLE_VALID_COMPARISON_THRESHOLDS = ("2+", "3+")` in `daily_model_review.py`. Any row at threshold `1+` is structurally excluded from the shadow-vs-base comparison before it ever reaches the scoring code, and a diagnostic message reports exactly how many such rows were excluded and why — the overlay's own historical validation covers 1+/2+/3+, but base SOG 1+ remains `DERIVABLE_NOT_VALIDATED` per the Preseason Operational Readiness Closure sprint, and this framework does not let the overlay comparison quietly relabel it. Directly tested (`Test03SogOneplusExclusion`).

## E. Error taxonomy (Part 22)

`RANDOM_VARIANCE, ROLE_CHANGE, STARTER_ERROR, ACTIVE_STATUS_ERROR, MODEL_CALIBRATION, FEATURE_DRIFT, DATA_ERROR, MARKET_MAPPING_ERROR, UNKNOWN`. Classification uses only real, already-recorded fields (`pp_transition_state`, `pp_games_since_transition`, `pp_role_certainty`, settlement `notes` from `outcome_resolver.py`'s own status strings, `confidence`) — never a guess beyond what the row shows. `UNKNOWN` is a legitimate, expected outcome for a residual the row's own fields don't explain.

## F. Challenger lifecycle (Part 25-29, 34)

A challenger may only be **proposed** when `validate_evidence()` clears three hard floors: ≥5 occurrences, ≥3 distinct game dates, and a non-empty explanation — directly implementing Part 24's "one McDavid 0-SOG game does not create a new model." Status transitions are a strict state machine (`HYPOTHESIS → {TESTING, REJECTED}`, `TESTING → {SHADOW, REJECTED}`, `SHADOW → {PROMOTION_CANDIDATE, REJECTED, RETIRED}`, `PROMOTION_CANDIDATE → {RETIRED, REJECTED}`); `REJECTED`/`RETIRED` are terminal. Entering `TESTING` now requires the full version-control field set (Part 34: feature version, training cutoff, evaluation cutoff, code commit, reason for change) — enforced by `require_version_control_fields()`, not left as optional metadata a challenger could skip.

**Training-vs-evaluation separation (Part 29)** is tracked via each entry's own `training_window`/`evaluation_cutoff` fields, giving a real audit trail of what data a challenger has already seen — this is a documented, inspectable boundary rather than a mechanically-enforced one, since the registry itself doesn't run a challenger's model code and can't police what that code internally touches.

**Daily re-scoring without re-tuning (Part 28)** is enforced by omission: `challenger_registry.py` exposes no function that modifies a challenger's own parameters at all — only status labels. There is structurally nothing here a daily job could call to "tune" a challenger against new data.

## G. Retraining triggers (Part 30-31)

`evaluate_retraining_triggers()` checks four real conditions (minimum new games, calibration drift vs. a baseline, a flagged league-environment shift, sustained degradation across consecutive windows) and — if any trigger — recommends `CREATE_CHALLENGER_VERSION`, explicitly never a production replacement. Verified directly that the recommendation string never contains "PRODUCTION_REPLACEMENT" and the function's own source never imports `model_registry`.

## H. Rejected-research protection (Part 49)

`rejected_research_check.all_rejected_entries()` aggregates REAL `REJECTED`-status entries from the 4 registries that carry one — confirmed to include the real, known Blocked-Shots PK-removal overlay rejection and the real Goalie Saves 35+ rejection, and confirmed to exclude validated models. `matches_a_rejected_idea()` does an exact, case-insensitive ID match — deliberately not fuzzy text matching, which could false-positive on an unrelated hypothesis that merely mentions a rejected concept in passing.

## I. Status determination (Part 38-40)

`combine_status()` always takes the MOST SEVERE of its inputs — a mix of NORMAL and one HALT signal is HALT, never averaged away. `check_run_order()` HALTs before any scoring if results aren't ingested or settlement hasn't completed (Part 1/52/53) — the daily review flags an incomplete run rather than scoring partial data as if it were whole, verified directly (`Test01RunOrder`, `Test02DeterministicReport::test_incomplete_run_writes_a_minimal_report`).

## J. Drift monitoring — honest, not fabricated (Part 18-21)

Every drift check accepts real historical and real current rates and returns `INSUFFICIENT_DATA` when the current side doesn't exist yet — which is the case for **everything** right now, since no real 2026-27 season data exists. `check_league_environment_flags()` aggregates WATCH-level drift into a labeled observation and explicitly never applies a correction (`"no automatic model adjustment was made"` is in the returned dict, not just a comment). `check_contract_status()` reports `VERIFIED LIVE CONTRACTS: 0` today, matching the current frozen state exactly.

## K. What genuinely cannot be built yet

- **"Expected-count MAE"** (Part 2): the ledger persists only threshold probabilities, not the underlying model mu — reported as `NOT_AVAILABLE` with the real reason, never approximated.
- **False-inactive tracking** (Part 17): no row is ever created for a player the frozen model projected inactive, so there is no record to check a false-inactive projection against — reported as `NOT_OBSERVABLE`, not silently omitted.
- **Position/rest-days/recent-TOI residual grouping** (Part 14): not currently joinable from ledger fields alone without a broader join to `nhl.db` this sprint didn't build; player/team/opponent/confidence/role-state/role-certainty grouping IS built and real.
- **Market contract drift monitoring** (Part 21): structurally impossible to exercise meaningfully while `VERIFIED_CONTRACTS` is empty — the check exists and correctly reports `NORMAL` / "nothing to drift-check" today.

## L. Tests

99 new tests across 6 new test files (`test_model_scorecard.py` 32, `test_error_taxonomy.py` 11, `test_challenger_registry.py` 17, `test_engine_status_evaluator.py` 16, `test_rejected_research_check.py` 7, `test_daily_model_review.py` 26), covering every required area from the sprint's own list: daily scorecard, calibration, Brier/log-loss, time windows, model-vs-shadow (with the 1+ exclusion proven), CLV, decision-state separation, real-vs-theoretical ROI, error taxonomy, challenger lifecycle (including the version-control gate), no-automatic-promotion, production immutability, rejected-research protection, low-sample warnings, run ordering, settlement-incomplete handling, and deterministic daily reports.

## M. Full Test Result

**2,214 / 2,214 passing**, 0 failures (`python3 -m unittest discover -s tests -p "test_*.py"`, 276.4s). Starting baseline was 2,105; this sprint adds 109 new tests across 6 new test files.

## N. Dashboard

`dashboard/pages/32_Model_Learning.py` — a new, compact page (not a redesign) under Track & Monitor, reading `run_daily_review()` directly. Shows the 4 trend windows the owner asked for (Yesterday/Last 7/Last 30/Season), shadow-vs-production comparisons, the improvement queue, and the challenger list. Gracefully reports the expected "no observations yet" state (verified via `AppTest`, zero exceptions) rather than erroring, since no real prospective observation has ever been recorded to disk.

## O. Odds API / Scheduler

Zero Odds API credits used — everything in this sprint was built and tested against synthetic ledger fixtures, since no real 2026-27 data exists to run the real job against yet. Scheduler was not installed; `operational/daily_model_review.py` exists only as a callable job (`python3 -m operational.daily_model_review`), per Part 65's explicit instruction.

---

## Final Questions

**CAN THE ENGINE RETEST ITS LOGIC EVERY DAY AFTER RESULTS SETTLE?** YES.

**DOES DAILY RETESTING AUTOMATICALLY CHANGE PRODUCTION?** NO.

**DOES IT SCORE EVERY PROSPECTIVE MODEL?** YES — every market_id/threshold combination present in the ledger, across all 6 time windows.

**DOES IT COMPARE BASE VS SHADOW?** YES — for SOG PP-role, Goals context overlay, and Points context overlay, with the SOG 1+ exclusion structurally enforced.

**DOES IT MONITOR CALIBRATION?** YES.

**CLV?** WAITING_FOR_REAL_PRICES — the aggregation mechanism is built and tested (`clv_summary()`), but no real closing price has ever existed to populate a real `clv` value.

**INPUT DRIFT?** YES — the check exists and is tested; it honestly reports `INSUFFICIENT_DATA` today because no current-season rate exists yet to compare against.

**ROLE / STARTER / ACTIVE-STATUS ERROR?** YES — derived from real settlement outcomes (`GOALIE_DID_NOT_PLAY`/`PLAYER_DID_NOT_DRESS`), not a separate unbuilt field.

**DOES IT IDENTIFY REPEATED RESIDUAL PATTERNS?** YES — via `weekly_rollup()`'s persistence check (an issue must appear in ≥3 of a week's daily runs to be flagged, never a single day).

**CAN IT CREATE A CHALLENGER HYPOTHESIS?** YES — gated by `validate_evidence()`'s hard floors.

**CAN IT AUTO-PROMOTE THAT CHALLENGER?** NO.

**CAN IT AUTO-CHANGE DECISION_POLICY?** NO.

**CAN IT ALTER HISTORICAL PREDICTION SNAPSHOTS?** NO — verified directly, byte-identical before/after a full review run.

**DOES IT PRODUCE DAILY 1/7/14/30/SEASON METRICS?** YES.

**DOES IT DISTINGUISH MODEL QUALITY FROM REAL BET P&L?** YES — `decision_state_breakdown()` and `season_leaderboard()` both keep `REAL_BET`-only P&L/ROI structurally separate from counterfactual/theoretical hit rates.

**DOES IT CONSULT THE REJECTED-RESEARCH REGISTER?** YES — every run, from the real, live registries.

**IS THERE A DAILY LEARNING REPORT?** YES — `reports/daily/YYYY-MM-DD_MODEL_REVIEW.md`.

**IS THERE A WEEKLY ROLLUP?** YES — `weekly_rollup()`.

**IS THERE A SEASON-TO-DATE MODEL LEADERBOARD?** YES — `season_leaderboard()`.

**WAS THE DAILY JOB SCHEDULED?** NO.

**WERE ODDS API CREDITS USED?** NO.

**DID ANY EXISTING MODEL CHANGE?** NO.

**DID DECISION_POLICY V3 CHANGE?** NO.

**CURRENT TEST RESULT?** 2,214 / 2,214.

**COMMIT HASH?** `b9fafe3` (on top of the preseason freeze commit `6335ce3`)
