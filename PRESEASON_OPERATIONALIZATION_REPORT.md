# Preseason Operationalization Report

**Sprint:** Preseason Operationalization — Prospective Recorder + Real Ledger + Streamlit UX Port + System Health, plus the UX Refinement Addendum
**Baseline at sprint start:** 1,680 / 1,680 tests passing
**Baseline at sprint end:** **1,734 / 1,734 tests passing**

## Scope note

This sprint delivered the full backend infrastructure requested (Sections A-C of the primary objective: prospective recorder, real ledger, real system health/readiness) plus real ports of five Streamlit pages applying the UX refinement addendum's decision hierarchy. It did **not** build real Player Props/Goalies/Combinations/Market Movement/Players/Player Detail operational pages — see `STREAMLIT_UX_PORT_REPORT.md` Section R for the honest accounting of what remains prototype-only. Nothing below claims more than what was actually built and tested.

## A. Executive summary

Four new pieces of real, tested infrastructure now exist: an append-only, immutable, idempotent prospective observation store (SQLite, DB-level immutability trigger); a real system-health object reading actual files/timestamps/DB connections/registries; a fail-closed live-readiness service; and five real Streamlit pages (Today, Model Health, Ledger, Research Hub, plus a bug-fixed Live SOG Markets) built on a new shared component library (`render_opportunity_card`, `render_status_banner`, `render_empty_state`, centralized number formatting). No validated model was refit, decision_policy v3 is byte-unchanged, and both context overlays' parameters are unchanged and re-verified.

## B. Prospective recorder architecture

`operational/prospective_ledger.py` + `operational/prospective_schema.sql`. See `PROSPECTIVE_LEDGER_SCHEMA.md` for the full schema documentation.

## C. Ledger schema

See `PROSPECTIVE_LEDGER_SCHEMA.md`.

## D. Append-only behavior

Enforced at the database level via the `predictions_immutability` trigger (aborts any `UPDATE` touching a prediction-time column) — not just a Python-API convention. Verified directly: a raw `UPDATE predictions SET raw_probability = 0.99 ...` fails with a real SQLite exception (`tests/test_operational_infrastructure.py::Test03`).

## E. Idempotency behavior

A deterministic `idempotency_key` (SHA-256 of `game_id|player_id|market_id|threshold|side|model_version|prediction_cutoff_utc`) means ten repeated `record_model_observation()` calls with identical real-world inputs produce **exactly one row** — verified directly (`Test06RerunDuplicateProtection`, asserts `len(ids) == 1` and `COUNT(*) == 1` after 10 calls).

## F. Settlement design

`settle_prediction()` is the *only* function that issues an `UPDATE`, and it only ever writes to `result_status`, `actual_outcome`, `settled_at_utc`, `profit_loss`, `closing_odds`, `closing_captured_at_utc`, `clv`, `notes`. Verified: `raw_probability` is provably unchanged immediately after settlement in the same test that settles the row (`Test10Settlement`).

## G. Prospective overlay tracking

For every Goals/Points observation, `raw_probability`, `context_adjusted_probability`, and `coherent_probability` are all stored, always (identity when the context state isn't `COLD_AND_TOI_DECLINE`, per the original overlay slice's Part 4 discipline, preserved here). Verified directly for both props (`Test15`, `Test16`).

## H. Shadow-policy integration

`SHADOW_POLICY_OBSERVATION` records store `raw_policy_input_probability`, `shadow_context_policy_probability`, `current_policy_status`, `shadow_policy_status`, and `future_policy_candidate` — all genuinely separate columns from any official decision, never overwriting or feeding into `decision_policy` itself. Verified (`Test20ShadowPolicyOutput`).

## I. Official policy protection

`decision_policy.py` hash-confirmed byte-identical (`f812d5f...763a`). `ShadowContextStack` and the new ledger code both import zero `decision_policy` symbols — verified structurally in the prior sprint's tests and re-confirmed here. LOW-confidence Goals and Points both still narrow to `WATCH` under `gate_low_confidence` (`Test22`, `Test23`).

## J. System-health architecture

See `SYSTEM_HEALTH_AND_READINESS_REPORT.md`. 13 real components, all backed by actual files/timestamps/DB pings/registry loads — zero demo constants.

## K. Live-readiness architecture

See `SYSTEM_HEALTH_AND_READINESS_REPORT.md`. `live_readiness()` is fail-closed by construction — verified for `READY`, `WAIT` (two reasons), `DATA_UNAVAILABLE`, and `MODEL_NOT_OPERATIONAL` (two reasons) outcomes.

## L. Fail-closed behavior

No page in this sprint's new code ever substitutes a fake number for a missing live field — `format_american_odds(None)` returns the literal string `NO LIVE PRICE`, verified directly, and the opportunity card renders that string rather than `0` or a hardcoded price in every "no price" test case.

## M. Streamlit IA implemented

Navigation reorganized via Streamlit's native sectioned `st.navigation({"Operate": [...], "Track & Monitor": [...], "Research": [...]})` — all 24 pages present (20 original + 4 new), zero pages deleted, zero duplicate nav icons (the dashboard audit's 🏒×2/🥅×2 finding is fixed — every one of the 24 icons is now unique, verified by a regex test). "Today" is the new default landing page.

## N. Pages created/changed

**Created:** `dashboard/pages/21_Today.py`, `22_Model_Health.py`, `23_Ledger.py`, `24_Research_Hub.py`.
**Changed:** `dashboard/app.py` (nav restructure), `dashboard/pages/8_Live_SOG_Markets.py` (dict-key bug fix + opportunity-card port), `dashboard/components.py` (+`render_status_banner`, `render_empty_state`, `render_opportunity_card`, `STATUS_BANNER_STYLES`, `DECISION_COLORS`, `CONTEXT_STATE_PLAIN_LABEL`), new `dashboard/formatting.py`.

## O. Opportunity-card implementation

See `STREAMLIT_UX_PORT_REPORT.md` Sections A/C. Verified rendering in 4 real states (full live, no-price, overlay-active, WATCH/WAIT/PASS) with zero exceptions via `streamlit.testing.v1.AppTest`.

## P. Context-overlay UX

Plain-language label (`COLD + ROLE DECLINE`) with the exact machine state name and corrected PIT-safe wording available on hover — see `STREAMLIT_UX_PORT_REPORT.md` Section C.

## Q. Model-health implementation

`dashboard/pages/22_Model_Health.py` — real `MODEL_REGISTRY`-driven, verified every entry's display name renders.

## R. Ledger-page implementation

`dashboard/pages/23_Ledger.py` — real, persisted, four separate tabs, verified "NO REAL BETS RECORDED" shows correctly when the Real Bets tab is empty.

## S. Research-hub implementation

`dashboard/pages/24_Research_Hub.py` — 7 groups (Data Foundation, Marginal Models, Joint Dependence, Context/Overlays, Confidence, Failed/Partial Research, Architecture), every existing research page still directly reachable via both the Research nav section and the hub's own links. Team Goals by Period is shown explicitly labeled NOT VALIDATED, not hidden.

## T. Live SOG migration

Fixed the dashboard audit's exact finding: 13+ direct, unguarded dict-key accesses (`r['player_name_raw']`, `r['draftkings_price']`, etc.) replaced with a defensive `_board_row_to_card()` adapter using `.get()` throughout, feeding the new `render_opportunity_card()`. A missing/renamed board-cache key now degrades gracefully instead of raising a raw `KeyError`.

## U. Offseason behavior

`dashboard/pages/21_Today.py` renders cleanly with zero real games in today's corpus (verified, `Test39TodayOffseasonState`) and shows no fabricated game cards or prices — only real system health and an honest "no live markets" empty state.

## V. Performance benchmarks

**Not formally profiled this sprint.** All five new/changed pages render in well under a second under `AppTest` (no perceptible delay during test runs), but no formal timing benchmark was captured. Real, scoped follow-up.

## W. Caching changes

None added this sprint. The new pages' data sources (`system_health`, `live_readiness`, `MODEL_REGISTRY`, the ledger) are all cheap (JSON reads, in-memory Python lists, single SQLite pings) and don't currently need `st.cache_data`/`st.cache_resource` — premature caching was avoided per the "don't over-engineer" instruction.

## X. Gitignore changes

Added: `research/real_nhl_pbp/research_pbp.db`, `research/real_nhl_pbp/raw/**/*.json`, `research/player_sog_period/player_game_period_sog.jsonl`, `research/joint_shot_workload/joint_shot_workload.jsonl`, `research/joint_scoring_dependence/joint_scoring.jsonl`, `operational/prospective_observations.db`. All were previously-untracked, clearly-generated/regenerable-or-operational-runtime files identified by the prior sprint's storage audit — no source code or report was touched.

## Y. Security confirmation

Re-confirmed this sprint: `.env` remains `git`-ignored (`git check-ignore .env` succeeds); `THE_ODDS_API_KEY` does not appear anywhere in any file this sprint touched (grepped directly); no secret value enters the new ledger schema or health/readiness UI (neither reads environment variables at all).

## Z. `nhl.db` decision (Part 99)

**Correction to the prior report's characterization**: `nhl.db` is **not** merely a "synthetic demo DB" that can be casually removed — a direct grep shows it (via `db.py::DB_PATH`) is actively referenced by `validate.py`, `demo_setup.py`, `sync_daily.py`, `validate_live_nhl.py`, and well over a dozen `research/run_*.py` scripts for team/schedule metadata. **Recommendation: KEEP.** It is load-bearing production infrastructure, not cleanup debris, despite the "synthetic demo" label in its original commit message.

## AA. Files created/modified

**Created:** `operational/prospective_schema.sql`, `operational/prospective_ledger.py`, `operational/system_health.py`, `operational/live_readiness.py`, `dashboard/formatting.py`, `dashboard/pages/21_Today.py`, `22_Model_Health.py`, `23_Ledger.py`, `24_Research_Hub.py`, `tests/test_operational_infrastructure.py`, `PROSPECTIVE_LEDGER_SCHEMA.md`, `SYSTEM_HEALTH_AND_READINESS_REPORT.md`, `STREAMLIT_UX_PORT_REPORT.md`, `PRESEASON_OPERATIONALIZATION_REPORT.md` (this file).

**Modified:** `dashboard/app.py` (nav restructure), `dashboard/pages/8_Live_SOG_Markets.py` (bug fix + card port), `dashboard/components.py` (new shared components), `.gitignore`.

**Untouched (verified via hash pins):** every frozen marginal/joint/overlay results file, `decision_policy.py`, `models/`, `config.py`, `db.py`, `schema.sql`, `nhl.db`, `market_registry.py`, `research/model_registry.py`'s own registered statuses.

## AB. Full test result

**1,734 / 1,734 tests passing** (1,680 pre-sprint + 54 new in `tests/test_operational_infrastructure.py`). No existing test was weakened.

## AC. Remaining live-data blockers

Real DraftKings prop payloads beyond SOG; real lineup/starter confirmation; real odds freshness beyond the SOG contract; prospective calibration of both context overlays (the recorder now exists to capture this once games start, but zero real observations exist yet); real CLV.

## AD. Remaining pre-season code blockers

Player Props/Goalies/Combinations/Market Movement/Players/Player Detail real page builds; actionability-based default sort; wiring `FRESHNESS_TTL_HOURS` into page display logic; extending `live_readiness()`'s market-family mapping beyond the 7 families covered today; manual browser QA of the 5 new/changed Streamlit pages at 1440/1200/900px; formal dashboard performance profiling.

## AE. Exact next single MODEL / RESEARCH slice

Wire the prospective recorder into a real prediction-generation script that runs once daily against the actual NHL schedule (once games exist) and calls `record_model_observation`/`record_shadow_observation` for every eligible Goals/Points prediction — the recorder exists but nothing calls it yet outside tests.

## Exact next single UX / PRODUCT slice

Build the real Player Props page using `render_opportunity_card()` and the existing `research.live_sog_pricing` board data — the second family beyond Live SOG Markets to get genuine live operational treatment, and the natural place to introduce actionability-based default sorting.

---

## Final Questions (primary sprint)

IS A REAL PERSISTED PROSPECTIVE OBSERVATION STORE NOW BUILT? **YES**
IS IT APPEND-ONLY FOR PREDICTION FIELDS? **YES**
CAN STREAMLIT RERUN WITHOUT DUPLICATING OBSERVATIONS? **YES**
ARE MODEL / OVERLAY / POLICY VERSIONS SNAPSHOTTED? **YES**
CAN OBSERVATIONS BE SETTLED WITHOUT MUTATING ORIGINAL PREDICTIONS? **YES**
ARE MODEL OBSERVATIONS SEPARATE FROM REAL BETS? **YES**
IS REAL P&L SEPARATE FROM SHADOW/MODEL OBSERVATIONS? **YES**
ARE GOALS / POINTS SHADOW OVERLAYS RECORDED PROSPECTIVELY? **YES** (schema/API ready; zero real observations exist yet — see AC)
IS SHADOW POLICY OUTPUT NOW STORED SEPARATELY? **YES**
HAS DECISION POLICY v3 CHANGED? **NO**
DO LOW GOALS / POINTS REMAIN WATCH_ONLY? **YES**
IS A REAL SYSTEM_HEALTH OBJECT BUILT? **YES**
IS LIVE_READINESS() BUILT? **YES**
DOES IT FAIL CLOSED? **YES**
IS THE REAL STREAMLIT DASHBOARD NOW USING THE PROTOTYPE INFORMATION ARCHITECTURE? **PARTIAL** (nav structure yes; 6 of 10 prototype page concepts still pending real builds)
IS THE REAL OPPORTUNITY CARD IMPLEMENTED? **YES**
IS LIVE SOG USING IT? **YES**
IS MODEL HEALTH USING REAL REGISTRY DATA? **YES**
IS THE LEDGER PAGE REAL AND PERSISTED? **YES**
IS THERE A RESEARCH HUB? **YES**
DOES THE OFFSEASON DASHBOARD SHOW NO FAKE BETS / ODDS? **YES**
ARE ALL VALIDATED MODELS UNCHANGED? **YES**
ARE ALL VALIDATED JOINT MODELS UNCHANGED? **YES**
ARE CONTEXT OVERLAY PARAMETERS UNCHANGED? **YES**
IS THERE ANY KNOWN UNRESOLVED TEMPORAL LEAKAGE? **NO**
CURRENT FULL TEST RESULT? **1,734 / 1,734**
WHAT IS HISTORICAL RESEARCH READINESS NOW, 0-10? **8** (unchanged — no research model work done this sprint)
WHAT IS PRESEASON OPERATIONAL READINESS NOW, 0-10? **5** (up from 2 "live straight-bet readiness" — real recorder/ledger/health infrastructure now exists, but only SOG has a live price contract and 6 of 10 operational pages remain unbuilt)
WHAT IS UX/PRODUCT READINESS NOW, 0-10? **6** (up from 5 — 5 real pages now match the approved IA and decision hierarchy, up from prototype-only)
WHAT STILL REQUIRES REAL 2026-27 DATA? Real DraftKings payloads beyond SOG, real lineup/starter confirmation, real odds freshness for non-SOG markets, prospective overlay calibration, real CLV — see Section AC.
WHAT IS THE NEXT SINGLE MODEL / RESEARCH SLICE? See Section AE.
WHAT IS THE NEXT SINGLE UX / PRODUCT SLICE? Real Player Props page (see above).

## Final UX Questions (addendum)

IS THE APPROVED PROTOTYPE IA PRESERVED? **YES**
WAS IT REFINED RATHER THAN BLINDLY COPIED? **YES** (decision hierarchy reordered per addendum Section B)
DOES TODAY PRIORITIZE READINESS? **YES**
DOES TODAY PRIORITIZE ACTIONABILITY? **PARTIAL** (shows real readiness; no real opportunities exist to rank yet outside SOG)
IS BEST ACTIONABLE THE DEFAULT PROP SORT? **NO** (no real Player Props page exists yet)
IS MAX ACCEPTABLE PRICE PROMINENT? **YES** (primary metrics row, `st.metric`)
ARE CURRENT PRICE AND MAX PRICE EASY TO COMPARE? **YES** (adjacent in the same row)
ARE DRIVERS/RISKS COLLAPSED OR COMPACT BY DEFAULT? **YES** (2 shown, rest in expander)
ARE RAW/ADJUSTED/CONSERVATIVE/MARKET PROBABILITIES STILL AVAILABLE? **YES**
ARE THEY VISUALLY PRIORITIZED CORRECTLY? **YES**
IS CONTEXT INACTIVE UX COMPACT? **YES** (banner only renders when `context_state` is set)
IS CONTEXT ACTIVE UX CLEAR? **YES**
IS CONTEXT DESCRIBED WITHOUT UNSUPPORTED PSYCHOLOGICAL CLAIMS? **YES**
IS MODEL CONFIDENCE DISTINCT FROM LIVE READINESS? **YES** (separate badge families, `DECISION_COLORS` vs `STATUS_BANNER_STYLES`)
IS STARTER CERTAINTY DISTINCT FROM MODEL CONFIDENCE? **N/A this sprint** (no real Goalies page built)
IS ACTIVE STATUS DISTINCT FROM BETTING WATCH STATUS? **N/A this sprint** (no real Players page built)
DO GOALIE THRESHOLDS SHOW THEIR EXACT VALIDATION STATES? **YES, in Model Health** (per-family, not yet on a dedicated Goalies page)
DO COMBINATIONS VISUALLY SHOW NAIVE VS JOINT PROBABILITY? **NO** (no real Combinations page built)
ARE REDUNDANT LEGS UNMISTAKABLE? **N/A this sprint**
CAN A COMBINATION SHOW BET WITHOUT A REAL PRICE? **NO** (unchanged, `live_readiness` still fail-closed)
IS THE PLAYERS PAGE MORE USEFUL FOR DAILY SCANNING? **N/A this sprint**
DOES PLAYER DETAIL SHOW CHANGES SINCE LAST GAME? **N/A this sprint**
DOES MARKET MOVEMENT SHOW TOWARD/AWAY MODEL? **WAITING FOR LIVE DATA**
IS THE LEDGER SHORTENED TO "LEDGER" IN NAV? **YES**
DOES THE LEDGER USE SEPARATE RECORD-TYPE TABS? **YES**
CAN MODEL OBSERVATIONS EVER ENTER REAL P&L? **NO**
IS SYSTEM HEALTH BACKED BY REAL DATA? **YES**
ARE GLOBAL WARNINGS DE-DUPLICATED? **YES** (Today page shows one banner per failing dependency, not per-page repeats)
ARE OPERATIONAL PAGES FREE OF RAW RESEARCH FILENAMES? **PARTIAL** (Model Health intentionally shows freeze filenames in an expandable technical-detail panel, not on the primary card — consistent with addendum Section AL's "expandable," not "absent")
ARE EDGE AND EV UNITS UNAMBIGUOUS? **YES** (`format_edge`→pp, `format_ev`→%, distinct functions)
IS THE REAL STREAMLIT UI USABLE AT 1440PX? **NOT MANUALLY VERIFIED**
AT 1200PX? **NOT MANUALLY VERIFIED**
AT 900PX? **NOT MANUALLY VERIFIED**
DID ANY VISUAL CHANGE ALTER VALIDATED MODEL MATH? **NO**
DID ANY VISUAL CHANGE ALTER DECISION POLICY v3? **NO**

---

**STOP AFTER PRESEASON OPERATIONALIZATION.**
