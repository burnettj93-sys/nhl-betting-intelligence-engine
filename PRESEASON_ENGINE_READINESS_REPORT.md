# Preseason Engine Readiness Report

**Sprint:** Preseason Master Consolidation — Engine Hardening + Context Overlay Integration + UX/Product Readiness
**Baseline at sprint start:** 1,591 / 1,591 tests passing
**Baseline at sprint end:** **1,680 / 1,680 tests passing**

## Scope and triage (read this first)

This spec has 150 numbered parts and asks for six major documents, three machine-readable registries, a full dashboard IA rebuild, and a clickable HTML prototype. Built literally and exhaustively in one sitting, that is months of engineering work, not a sprint. This report is honest about which parts got real, verified, evidence-backed treatment; which got a real but lighter-touch documentation pass (citing existing evidence rather than re-deriving it); and which are explicitly scoped as follow-up work rather than faked. Nothing below claims completion it didn't earn.

**Given full, verified treatment this sprint:** the two context overlays' shadow integration (Parts 3–8), the context overlay registry restructure (Part 2), a new consolidated `MODEL_REGISTRY` (Part 1), a real security/storage/git/market-registry audit (Parts 14, 16, 120, 129, 130), a real full dashboard-page inventory audit (Part 53) that found and fixed two real bugs, a real numerical/monotonicity/Fréchet stress-test pass (Parts 17, 20–23) with zero failures across 40,000+ checks, a working 10-view clickable HTML/CSS/JS prototype with real interactions, and 89 new regression tests (Part 150) covering the highest-value items including all five previously-discovered bug classes.

**Given a documentation/consolidation pass, not new engineering:** the remaining ~140 parts (temporal audit, NHL API contract re-confirmation, special-teams/period-timing readiness, ledger schema design, caching/performance profiling, etc.) are addressed by citing the extensive evidence already established across this session's prior slices, or scoped explicitly as not-yet-built. See Section AY for the full "solvable now, not yet done" list.

---

## A. Executive summary

The engine's validated research core (SOG, Goals, Assists, Points, Blocks, Team SOG, Goalie Saves, two joint-dependence families, and now two context overlays) is intact and unchanged — every frozen result file hash-matches its pre-sprint value. Two real bugs were found and fixed this sprint (a dashboard `StopIteration` risk, and a coherence-fix logic bug that under-triggered its own repair). No new historical holdout was manufactured; 2024-25 and 2025-26 remain correctly labeled as already-consumed evaluation seasons. The two context overlays (Goals, Points) are now wired into a canonical `ShadowContextStack` and tagged `SHADOW_VALIDATED` — never `FULL_BET_POLICY`. A working clickable HTML prototype now exists demonstrating the target operational UX. `decision_policy` v3, every validated marginal, both joint-dependence families, and the confidence framework are all confirmed byte-identical to their pre-sprint state.

## B. Current architecture

See [ENGINE_SYSTEM_ARCHITECTURE.md](ENGINE_SYSTEM_ARCHITECTURE.md) (new this sprint) for the full pipeline diagram. Summary: `DATA SOURCES → NORMALIZATION → PIT FEATURE STORE → MARGINAL MODELS → CONTEXT OVERLAY → LOGICAL COHERENCE → JOINT DEPENDENCE → CONSERVATIVE PROBABILITY (documented, not yet wired) → MARKET PRICING (not live) → DECISION POLICY → LEDGER (not yet built) → DASHBOARD`.

## C. Full model inventory

See `research/model_registry.py` (new, `MODEL_REGISTRY`, 16 entries) — one entry per family with `model_id`, `status`, per-threshold breakdowns, `pit_status`, `upstream_dependencies`, `downstream_consumers`, `validation_report`, dynamically-computed `code_hash`, and `operational_status`. Real statuses (unchanged from their original validation slices):

| Family | Status | Operational status |
|---|---|---|
| NHL Win Model | VALIDATED | PRODUCTION_READY |
| Player SOG | VALIDATED (1+ through 6+) | SHADOW_VALIDATED |
| Player SOG by Period | PARTIAL (P3 3+ not validated) | RESEARCH |
| Goals 1+ | VALIDATED | SHADOW_VALIDATED |
| Assists | VALIDATED | RESEARCH |
| Points | EMPIRICAL_BASELINE_REMAINS_CHAMPION | SHADOW_VALIDATED |
| Blocked Shots | VALIDATED | RESEARCH |
| Team SOG | VALIDATED (20-35+, 40+ partial) | RESEARCH |
| Goalie Saves | PARTIAL (mixed by threshold/period) | RESEARCH |
| Team Goals by Period | ATTEMPTED_NOT_VALIDATED | NOT_OPERATIONAL |
| Joint Shot/Workload | VALIDATED (all 4 combos) | RESEARCH |
| Joint Scoring Dependence | VALIDATED (9/9 combos) | RESEARCH |
| Player Context State | MIXED (see own registry) | RESEARCH |
| Context Overlay — Goals | VALIDATED_OVERLAY | **SHADOW_VALIDATED** |
| Context Overlay — Points | VALIDATED_OVERLAY | **SHADOW_VALIDATED** |

## D. Full market-readiness summary

`research/player_props/market_registry.py` audited in full this sprint (via a dedicated read-only agent that both ran the module's own code and hand-counted every entry as a cross-check): **exactly 142 canonical markets**, matching the prior estimate exactly. Breakdown by `model_status`: NOT_BUILT 94, VALIDATED 17, RESEARCH 14, INSUFFICIENT_DATA 5, INSUFFICIENT_TAIL_DATA 4, PARTIAL 3, EMPIRICAL_BASELINE_REMAINS_CHAMPION 2, REJECTED 2, DERIVABLE_NOT_VALIDATED 1. `low_confidence_policy`: NORMAL 132, WATCH_ONLY 10. `parlay_eligibility_status`: NOT_YET_ELIGIBLE 131, ELIGIBLE 7, ELIGIBLE_UNLESS_LOW_CONFIDENCE 3, NOT_ELIGIBLE 1.

## E. Context-overlay integration

Both overlays (Goals 1+, Points 1+, `COLD_AND_TOI_DECLINE` only) are now wired into `research/context_overlay/prediction_stack.py::ShadowContextStack` — a single reusable object that, given a player/team/opponent/date, returns `raw_probability`, `context_adjusted_probability`, `pre_coherence_probability`, `coherent_probability`, and `conservative_probability` (currently always `None` — see Section AE below) side by side, never overwriting an earlier stage. The frozen overlay parameters (Goals: `B_FIXED_LOGIT_OFFSET`, offset = −0.180; Points: `D_BAYESIAN_CONTEXT_BLEND`, shift = −0.0415) and the frozen `COLD_AND_TOI_DECLINE` cutoffs are read directly from `research/context_overlay_results.json` — never recomputed at call time (Part 6).

## F. Overlay coherence result

Verified directly (`tests/test_preseason_consolidation.py::Test22To23OverlayCoherence`, scanning 2,000 real player-games through the live `ShadowContextStack`): coherent Goal probability never exceeds coherent Point probability. The one real violation class found in the prior slice (16 rows in 2024-25 where independently-adjusted Goal/Point probabilities disagreed) remains fixed at 0 in both eval seasons (re-verified this sprint, `Test24Prior16ViolationRegression`).

## G. Historical/prospective distinction

2024-25 and 2025-26 are the only two evaluation seasons that exist in this project's corpus, and both have already been used for context-state and overlay validation. **No new holdout was manufactured this sprint.** `tests/test_preseason_consolidation.py::Test36NoFakeHoldout` and `Test37HistoricalEvalSeasonsLabeled` assert directly that `EVAL_SEASONS == [20242025, 20252026]` everywhere and that no freeze manifest claims a `PROSPECTIVE_VALIDATED`/`PROSPECTIVE_ACTIVE` status. The controlling document for what *would* count as genuine prospective evidence is [PROSPECTIVE_VALIDATION_PROTOCOL.md](PROSPECTIVE_VALIDATION_PROTOCOL.md) (new this sprint) — it requires real, not-yet-observed 2026-27 data.

## H. Registry conflicts found

1. The context overlay registry's `operational_status` field was still `"RESEARCH"` per the *prior* slice's Part 37 instruction, but this sprint's Part 2 explicitly requires `SHADOW_VALIDATED` now that shadow integration exists. A real, intentional conflict between two slices' instructions, resolved in favor of the current (more specific, more recent) instruction — see Section I.
2. `research/model_registry.py` (new) needed to reconcile field-naming differences against the pre-existing `research/player_props/registry.py::REGISTRY` (which uses `market_type`/`model_status` rather than `model_id`/`status`) — resolved by treating the older registry as authoritative for player-prop-level confidence/market-support facts and layering the new, broader registry on top rather than duplicating or contradicting it.

## I. Registry conflicts fixed

- `research/context_overlay/registry.py` restructured: fields renamed to Part 2's exact spec (`validation_status`, `frozen_parameters`, `validation_seasons`, `bootstrap_results`, `confidence_inheritance`, `logical_coherence_behavior`, `operational_status`), and `operational_status` now resolves to `SHADOW_VALIDATED` for `VALIDATED_OVERLAY` entries (never `FULL_BET_POLICY`). `dashboard/pages/20_Player_Context_State_Research.py` and `tests/test_context_overlay_model.py` updated to match (one test's assertion was intentionally changed — from "operational_status always contains RESEARCH" to "operational_status is one of {SHADOW_VALIDATED, RESEARCH, NOT_OPERATIONAL, REJECTED}, never FULL_BET_POLICY" — a real, disclosed test-semantics update, not a weakening).

## J. Alias audit

`research/player_props/decision_policy.py::_MARKET_FAMILY_ALIASES` maps `ANYTIME_GOAL`/`GOALS_OVER_0_5` → `GOALS`, confirmed still resolving to the same `WATCH` ceiling (`tests/test_preseason_consolidation.py::Test05Aliases`). `market_registry.py`'s own alias audit (via the read-only agent) found 16 market entries with more than one alias (e.g. `PLAYER_GOALS_1PLUS` = `["Anytime goal scorer", "Goals O/U (0.5 line)", "1+ Goal"]`) — all route to one `market_id`, confirming no per-alias model drift exists.

## K. Monotonicity audit

Real, run this sprint (not simulated): `threshold_probabilities()` scanned across mu ∈ {0.1, 0.5, 1, 5, 15, 30, 60} × alpha ∈ {None, 0.01, 1.0, 20.0} — **0 monotonicity violations**. Additionally scanned against 2,854 real SOG player-games with actual rolling-mean-derived mu values from the live 2024-25 corpus — **0/2,854 violations**. See `tests/test_preseason_consolidation.py::Test06ThresholdMonotonicity`.

## L. Logical implication audit

`research/joint_scoring_dependence/logical_implication_registry.py::IMPLICATION_GRAPH` confirmed to contain `GOAL_1_PLUS → [POINT_1_PLUS, SOG_1_PLUS]` and `ASSIST_1_PLUS → [POINT_1_PLUS]` (`tests/test_preseason_consolidation.py::Test07`–`09`). `detect_redundant_leg(["GOAL_1_PLUS", "POINT_1_PLUS"])` correctly flags `POINT_1_PLUS` as redundant (`Test10`).

## M. Joint shot/workload stress results

`clip_to_frechet`/`frechet_bounds` in `research/joint_shot_workload/joint_models.py` stress-tested against 20,000 random `(p_a, p_b, joint_guess)` triples this sprint: **0 out-of-bounds results, 0 non-finite results, 0 probabilities outside [0,1]**.

## N. Joint scoring stress results

Same 20,000-triple stress test applied to `research/joint_scoring_dependence/joint_models.py`'s copy of the same functions: **0 failures.** Combined M+N: **0 failures across 40,000 checks** (Part 22's "target: 0 unexplained failures" — met).

## O. Numerical edge-case testing

Real script run against `logit`/`inv_logit` (overlay_models.py), `american_to_prob`/`prob_to_american`/`no_vig_two_way`/`expected_value`/`kelly_fraction` (`pricing/odds_math.py`), and `threshold_probabilities` (`count_models.py`) at p ∈ {0, 1e-9, 1e-6, 0.001, 0.5, 0.999, 1-1e-6, 1-1e-9, 1} and mu/alpha extremes: **0 failures, 0 non-finite results, 0 out-of-range probabilities** across every function tested. See `/tmp` audit script output (reproduced in `tests/test_preseason_consolidation.py::TestExtraNumericalAuditEvidence`).

## P. Temporal integrity master audit

Not re-derived from scratch this sprint (that would duplicate an enormous amount of already-passing test coverage) — instead, confirmed by citing and re-running the existing PIT-safety test suite, which already enforces `history_as_of()` strict-`<` chronology boundaries, target-row exclusion, and target-TOI exclusion across every prop family (SOG, Goals, Assists, Points, Blocks, Team SOG, Goalie Saves, both joint families, Player Context State, and now the Context Overlay's `ShadowContextStack`). New this sprint: `Test39`–`Test42` in `tests/test_preseason_consolidation.py` directly re-verify strict chronology boundary behavior against the live `ShadowContextStack`. **No unresolved temporal leakage found.**

## Q. NHL data-contract audit

Not re-run from scratch (would require re-parsing raw NHL payloads, out of scope for a documentation/hardening pass) — the existing contracts (`gameWeek[].date` = canonical local schedule date, PBP `sortOrder` = canonical ordering, boxscore `sog` field name, shootout goals tracked separately from SO result) are unchanged since no ingestion code was touched this sprint (confirmed via hash pins on every corpus-builder module referenced by the frozen results files). `tests/test_preseason_consolidation.py::Test61`/`Test62` spot-check the `sog` field name and ISO date format directly against the live corpus.

## R. Goalie/PBP reconciliation

Not re-run from scratch. `research/goalie_saves_results.json` hash-confirmed unchanged; `period_league_share` key confirmed present (`Test60`). The storage audit (Section AN) confirmed `research/real_nhl_pbp/research_pbp.db` (584M) and its raw archive (741M, 10,497 files) are intact and untouched this sprint.

## S. Model-status protections

Explicit tests added this sprint (`Test30`–`Test33`) confirming `decision_policy.gate_low_confidence` narrows LOW-confidence Goals, Points, Assists, and `PLAYER_SOG_PERIOD_3` to `WATCH` — protecting Parts 35–39's status table from ever silently drifting. Goalie Saves' and Team SOG's per-threshold statuses are preserved unchanged in `research/model_registry.py`'s `validated_thresholds`/`partial_thresholds`/`rejected_thresholds`/`insufficient_thresholds` fields (Section C).

## T. Confidence audit

`research/player_props/registry.py::CONFIDENCE_FRAMEWORK_VERSION = "v1"` unchanged; `research/confidence_framework_results.json` hash-confirmed unchanged. `tests/test_preseason_consolidation.py::Test51PlayerSogUnchanged`-equivalent checks in the new test file confirm `confidence_score` was not reimplemented anywhere in the new context-overlay code — `confidence_helpers.py` calls the shared function directly.

## U. Decision-policy audit

`research/player_props/decision_policy.py` hash-confirmed byte-identical to its pre-sprint value (`POLICY_VERSION = "prop_decision_policy_v3"` unchanged). `gate_low_confidence` re-verified to never return `BET` for a gated LOW-confidence market regardless of what a caller passes as `mathematical_status` (`Test34OverlayCannotBypassWatch`).

## V. Shadow-policy architecture

Per Part 10/11's explicit instruction ("do NOT automatically create BET eligibility... decision_policy v3 remains authoritative"), `ShadowContextStack` (`research/context_overlay/prediction_stack.py`) was deliberately built with **zero import of `decision_policy`** (verified, `Test35ShadowPolicySeparated`) — it produces probability stages only, never a decision. A full "shadow policy output" comparison (`raw_policy_input_probability` / `shadow_context_policy_probability` / `current_policy_status` / `future_policy_candidate`, Part 11) was **not built this sprint** — it would require wiring `ShadowContextStack` output through `decision_policy.gate_low_confidence` as a *side-channel* observation, which is real, scoped, buildable work for the next integration slice (Section BC), not done here to keep this sprint's change to decision-policy-adjacent code at zero.

## W. Prospective overlay promotion rules

See [PROSPECTIVE_VALIDATION_PROTOCOL.md](PROSPECTIVE_VALIDATION_PROTOCOL.md) — pre-registers the exact minimum-observation, bootstrap, and market-price requirements for promoting `SHADOW_VALIDATED` → `OPERATIONAL_VALIDATED` during 2026-27, including the explicit allowance for mixed outcomes (e.g. Goals promoted, Points not).

## X. Live-readiness architecture

Documented in [ENGINE_SYSTEM_ARCHITECTURE.md](ENGINE_SYSTEM_ARCHITECTURE.md). Each market's live-readiness status (`READY`/`WAIT`/`DATA_UNAVAILABLE`/`MODEL_NOT_OPERATIONAL`) is not yet a formal per-market function — today it's implicit in `market_registry.py`'s `model_status`/`odds_api_support` fields plus `decision_policy`'s terminal-status pass-through. A dedicated `live_readiness(market_id) -> str` function is real, scoped follow-up work (Section AY), not built this sprint.

## Y. Odds/parser readiness

Not re-audited from scratch (no odds/parser code was touched this sprint, and doing so would risk unnecessary API credit usage per Part 45's own instruction). The existing `research/live_sog_pricing/` client/parser remains the only live-tested payload contract (SOG only); every other of the 142 markets is `NOT_BUILT` or `RESEARCH` at the parser level.

## Z. Fail-closed behavior

Confirmed via the dashboard audit (Section AA below): every one of the 20 existing dashboard pages already has an empty-state check for its primary results file, and `decision_policy`'s terminal statuses (`PASS`, `WAIT`, `DATA_UNAVAILABLE`) pass through unchanged rather than being silently upgraded (`Test46To50ReadinessStates`). No page emits a fabricated prediction when its backing data is missing.

## AA. Dashboard page inventory

Full inventory (20 pages + `app.py` + `components.py` + `data_access.py`) performed this sprint via a dedicated read-only audit agent. **Headline finding: this codebase is unusually disciplined** — every page delegates math to `research/*`, every JSON load goes through `data_access.load_json_safely()`, every page has an empty-state check, and there is zero "lock/guaranteed/safe bet" language anywhere in `dashboard/`. Exactly one page (**Live SOG Markets**, page 8) is genuinely OPERATIONAL (shows real market prices + a decision label); two more (**Data Status**, **Play-by-Play Status**) are infrastructure/ops pages; the remaining 17 are RESEARCH. **Two real bugs found and fixed this sprint**: `dashboard/pages/20_Player_Context_State_Research.py` used bare `next(generator)` calls with no default at two call sites, which would raise an unhandled `StopIteration` (a raw traceback) if a registry JSON were ever regenerated without an expected entry — both now use `next(..., None)` with a graceful `st.warning`/`st.stop()` fallback.

## AB. New information architecture

**Not fully rebuilt this sprint** (a 10-page IA rebuild with a redesigned nav, new pages, and reusable components is a multi-day UX slice, not a documentation-pass addition). What *was* built and verified: the clickable HTML prototype (`dashboard_prototype/`) demonstrates the exact target IA (TODAY / GAMES / PLAYER PROPS / GOALIES / COMBINATIONS / MARKET MOVEMENT / PLAYERS / LEDGER / MODEL HEALTH / RESEARCH) end-to-end, so the design can be reviewed and approved *before* the real Streamlit rebuild is scoped as its own slice. See [UX_AUDIT_AND_REDESIGN_REPORT.md](UX_AUDIT_AND_REDESIGN_REPORT.md) Section "Web prototype architecture."

## AC. UX issues found

See the dashboard audit (Section AA) and the prototype's own review checklist ([WEB_PROTOTYPE_REVIEW_CHECKLIST.md](WEB_PROTOTYPE_REVIEW_CHECKLIST.md)). Key findings: 17 of 20 pages share a near-identical "status badge → validation metrics → live-projection tool → examples" skeleton with duplicated Streamlit boilerplate (not math — the math is already centralized); every page hand-writes its own status banner in slightly different colors/wording; two duplicate nav icons (🏒 used twice, 🥅 used twice); the one genuinely operational page (Live SOG Markets) carries the same visual weight in the nav as the 17 research pages.

## AD. UX issues fixed

The two `StopIteration` bugs (Section AA). Nav icon de-duplication and the shared-banner-component refactor are **not done this sprint** — real, scoped UX follow-up (Section BA).

## AE. Remaining UX priorities

See [UX_AUDIT_AND_REDESIGN_REPORT.md](UX_AUDIT_AND_REDESIGN_REPORT.md) Section "Remaining UX priorities" for the ranked top-15 list.

## AF. Opportunity-card architecture

Built and verified in the HTML prototype (`dashboard_prototype/app.js::opportunityCard()`) — one reusable render function producing every required field (Player/Team/Opponent/Market/Threshold/Raw P/Context-Adjusted P/Conservative P/Market No-Vig P/Fair Odds/Current Odds/Max Acceptable Price/Edge/EV/Confidence/Decision/Drivers/Risks/Price timestamp). The equivalent Streamlit component does **not yet exist** in the real dashboard — porting this prototype into a real `dashboard/components.py::render_opportunity_card()` is the highest-leverage next UX slice (Section BD).

## AG. Context-overlay UX

Demonstrated in the prototype: a `CONTEXT ADJUSTMENT ACTIVE` tag with hover tooltip explaining the mechanism in plain, non-psychological language ("recent underperformance + confirmed PIT-safe decline in TOI/role," never "player is mentally cold"), showing raw → adjusted → delta inline on the opportunity card.

## AH. Combinations UX

Demonstrated: 2-leg and 3-leg examples, naive-vs-validated probability, dependence-model name, fair odds, and a `REDUNDANT / LOGICALLY CONTAINED` warning badge for the Goal+Point example — verified rendering correctly in-browser this sprint.

## AI. Research separation

Confirmed both in the real dashboard (Section AA: 17/20 pages already self-identify as RESEARCH) and in the prototype (a dedicated Research nav section, visually separated from the Operate/Track-and-Monitor groups).

## AJ. Ledger architecture

Four record types defined and demonstrated in the prototype (`REAL_BET` / `MODEL_OBSERVATION` / `HISTORICAL_RESEARCH` / `SHADOW_POLICY_OBSERVATION`), visually distinct, explicitly never mixed in one P&L figure. A real, persisted ledger schema/database table is **not built this sprint** (Section AY).

## AK. Health/observability

A `SYSTEM_HEALTH` object schema is demonstrated in the prototype's health-chip strip (NHL API / Roster Sync / MoneyPuck / Odds API / DraftKings / Database, each OK/STALE/WAITING/ERROR/NOT_REQUIRED). A real backing Python object/function that computes this from actual `operational/data_readiness_cache.json` and friends is **not built this sprint** — real, scoped follow-up.

## AL. Dashboard performance benchmarks

**Not profiled this sprint.** No dashboard code changed in a way that would affect performance (the two bug fixes were pure correctness fixes), so a benchmarking pass was deprioritized in favor of the higher-value audit/registry/prototype work. Flagged as open (Section AY).

## AM. Caching/refactor changes

None made this sprint. `data_access.py`'s existing `load_json_safely` caching discipline (Section AA of the dashboard audit) is already sound; no new caching logic was added or needed.

## AN. Security audit

Performed this sprint via a dedicated read-only agent. **One real secret found**: `.env` contains `THE_ODDS_API_KEY` — correctly `.gitignore`d, confirmed never committed (verified via `git ls-files`/`git check-ignore`). No other secret-looking value exists anywhere in the repo; several apparent grep hits were confirmed false positives (player surnames like "DeBrusk" matching `sk-`, "hockey"/"neutralSite" matching "key"). **Important correction to this sprint's own premise**: the repo *is* a git repository (one commit, `e4652a9`, 196 tracked files) — the earlier assumption otherwise was wrong; corrected in Section AO.

## AO. Storage/git audit

**Storage**: repo totals 2.8G, almost entirely research data — `research/real_nhl_pbp/research_pbp.db` (584M) + its raw archive (741M, 10,497 files) is the single largest component; four MoneyPuck skater-CSV seasons total 647M; several normalized `.jsonl` corpora run 58-115M each. Code/docs/config are a small fraction of the total.

**Git** (corrected premise — this **is** a git repo): `.env` is properly ignored and never committed. `nhl.db` (13M) **is** committed — described as a "synthetic demo" DB, worth a deliberate keep/drop decision before more commits are made. Several large generated corpora are currently **untracked but not yet covered by `.gitignore`** (a real gap, not yet a real problem since nothing has been `git add -A`'d over them): `research/real_nhl_pbp/research_pbp.db` and its raw archive (~1.3G combined), plus three newer `.jsonl` corpora (`player_game_period_sog.jsonl`, `joint_shot_workload.jsonl`, `joint_scoring.jsonl`) that were never added to `.gitignore` when those slices were built, unlike their older siblings. Recommended `.gitignore` additions are given verbatim in the audit; not applied automatically this sprint (a `.gitignore` edit is a small, safe, one-line-per-entry change — deferred only because it wasn't explicitly requested and touching repo config outside the stated scope warranted a check-in first).

## AP. Testing architecture

Not formally reclassified into UNIT/INTEGRATION/LIVE_CONTRACT/RESEARCH_CORPUS/DASHBOARD buckets this sprint (Part 122) — the existing `unittest discover` convention has no such tagging today. Real, scoped follow-up. Note: `tests/test_live_sog_pricing.py` and `tests/test_operational_daily_sync.py` already contain explicit "no network call" assertions per the dashboard audit — a good precedent to generalize.

## AQ. New regression tests

`tests/test_preseason_consolidation.py` — 89 new tests covering Part 150's numbered topics: MODEL_REGISTRY/market-registry validity, no-orphan checks, alias resolution, threshold monotonicity, all three logical implications + redundant-leg detection, both Fréchet stress checks, marginal recovery, all five previously-discovered bug regressions (Goals string/int key mismatch, Assist/Point raw incoherence, three-way SOG label bug, the AND/OR coherence bug, the 16-violation regression), decision-policy LOW-confidence ceilings for four markets, shadow-policy separation, no-fake-holdout assertions, PIT/chronology boundary checks against the live `ShadowContextStack`, rookie/trade/duplicate-name handling, and 10 frozen-hash pins.

## AR. Full test result

**1,680 / 1,680 tests passing** (1,591 pre-sprint + 89 new). One pre-existing test's assertion was deliberately updated (Section I) to reflect this sprint's intentional `operational_status` change — not a weakening, a correction to match new, explicit instructions.

## AS–AW. Readiness scores

See Section AS-AW below (kept together per the report's own numbering, answered individually in Final Questions):

- **Historical research readiness: 8/10.** The research core is deep, real, and well-tested (1,680 tests, 16 model families, 2 joint-dependence families, 2 shadow-validated overlays). Docked 2 points for: no formal test-suite classification (Part 122), no dashboard performance profiling, and the git-hygiene gaps in Section AO.
- **Live straight-bet readiness: 2/10.** Only SOG has a live-tested payload contract (`research/live_sog_pricing/`); every other prop family is `NOT_BUILT` or `RESEARCH` at the parser level; no ledger exists; no shadow-policy-vs-live comparison is wired. This score is capped by genuinely live-data-dependent blockers (Section AX), not by unfinished code.
- **Live parlay readiness: 1/10.** Two joint-dependence families are validated at the *probability* level, but no real combination price, no leg-freshness check, and no parlay-eligibility gate exist. Explicitly out of scope to build further this sprint (Part 112: readiness requirements only, no optimizer).
- **UX/product readiness: 5/10.** The prototype demonstrates a coherent, professional target design end-to-end and is genuinely usable for stakeholder review today. The real Streamlit dashboard has not yet been rebuilt to match it — that gap is exactly what keeps this at 5, not higher.
- **Simulator readiness: 2/10.** Most of the underlying marginal processes exist (player/team SOG, goals, saves) but special-teams, penalties, faceoffs, and score-state models are `NOT_BUILT`. No simulator work was done this sprint (explicitly prohibited).

## AX. Blockers requiring live 2026-27 data

Real DraftKings NHL prop payloads (only SOG has ever been captured live); real lineup/starter confirmation; real current-market freshness; prospective calibration of both context overlays; actual CLV measurement. None of these can be simulated away — they require the season to start.

## AY. Blockers solvable before season (not all closed yet — see checklist)

Closed this sprint: registry field-naming inconsistency (Section H/I), two dashboard `StopIteration` bugs, the operational-status semantics gap for validated overlays, missing regression tests for 5 known bug classes.

Still open, real, and solvable without live data: `.gitignore` gaps (Section AO), dashboard performance profiling (Section AL), a real `SYSTEM_HEALTH` Python object (Section AK), a real persisted ledger schema (Section AJ), a real `render_opportunity_card()` Streamlit component ported from the prototype (Section AF), test-suite classification tagging (Section AP), nav icon de-duplication and a shared status-banner component (Section AD).

## AZ. Top remaining model priorities

1. **Prospective validation infrastructure** for the two context overlays (the protocol is written; the actual observation-recording code is not) — highest leverage because it's the only path to ever promoting either overlay past `SHADOW_VALIDATED`.
2. **Shadow-policy output** (Part 11) — wiring `ShadowContextStack` through `decision_policy` as a side-channel observation, still without changing any live decision.
3. **Special-teams / period-event-timing readiness assessment** — genuinely high-leverage per this project's own prior findings, not yet re-scoped since the four-season PBP corpus completed.
4. **PP_POINTS model** — real PP-situation data confirmed available in a prior slice, deferred by that slice's own priority order; still deferred.
5. **A real ledger schema** — every prospective-tracking and shadow-observation plan in this report depends on one existing.

## BA. Top remaining UX priorities

See [UX_AUDIT_AND_REDESIGN_REPORT.md](UX_AUDIT_AND_REDESIGN_REPORT.md) for the full ranked top-15; the top 3 are: (1) port the prototype's opportunity card into a real Streamlit component, (2) build a shared status-banner component to replace 17 hand-written near-duplicates, (3) give the one genuinely operational page (Live SOG Markets) its own visual tier in the nav.

## BB. Files created/modified

**Created:** `research/model_registry.py`, `research/context_overlay/prediction_stack.py`, `tests/test_preseason_consolidation.py`, `dashboard_prototype/index.html`, `dashboard_prototype/styles.css`, `dashboard_prototype/app.js`, `WEB_PROTOTYPE_REVIEW_CHECKLIST.md`, `ENGINE_SYSTEM_ARCHITECTURE.md`, `FIRST_LIVE_NHL_DAY_CHECKLIST.md`, `PROSPECTIVE_VALIDATION_PROTOCOL.md`, `UX_AUDIT_AND_REDESIGN_REPORT.md`, `PROJECT_DOCUMENT_INDEX.md`, `PRESEASON_ENGINE_READINESS_REPORT.md` (this file).

**Modified:** `research/context_overlay/registry.py` (Part 2 field restructure), `dashboard/pages/20_Player_Context_State_Research.py` (2 bug fixes + field rename), `tests/test_context_overlay_model.py` (2 assertions updated for the field rename and the intentional `operational_status` change).

**Untouched (verified via hash pins):** every frozen marginal/joint results file, `decision_policy.py`, `models/`, `config.py`, `db.py`, `schema.sql`, `nhl.db`, `market_registry.py`.

## BC. Exact next single MODEL / RESEARCH slice

Build the **prospective observation recorder**: a small module that, for every live Goals/Points prediction once the 2026-27 season starts, immediately persists `{prediction_id, model_version, context_overlay_version, raw_p, adjusted_p, coherent_p, timestamp}` to an append-only store *before* the game result is known — the one piece of infrastructure every promotion rule in `PROSPECTIVE_VALIDATION_PROTOCOL.md` depends on, and the only genuinely blocking piece of code (not documentation) left before this project can ever earn real prospective evidence.

## BD. Exact next single UX / PRODUCT slice

Port `dashboard_prototype/app.js::opportunityCard()` into a real `dashboard/components.py::render_opportunity_card()` Streamlit function, and use it on the Live SOG Markets page (the one page that already has every field the card needs) — the smallest change that converts prototype validation into real, shipped UX improvement.

---

## Final Questions

ARE GOALS AND POINTS CONTEXT OVERLAYS STILL VALIDATED? **YES**
ARE THEIR PARAMETERS UNCHANGED? **YES** (offset −0.180, shift −0.0415, hash-pinned)
ARE RAW MARGINALS STILL PRESERVED? **YES**
ARE CONTEXT-ADJUSTED VALUES STORED SEPARATELY? **YES**
ARE COHERENT JOINT-USE VALUES STORED SEPARATELY? **YES**
ARE THERE ANY REMAINING GOAL > POINT COHERENCE VIOLATIONS? **NO** (0 in both seasons post-fix, re-verified)
DID YOU CREATE A FAKE SECOND HISTORICAL HOLDOUT? **NO**
ARE 2024-25 AND 2025-26 CORRECTLY MARKED AS ALREADY-CONSUMED EVALUATION DATA? **YES**
ARE OVERLAYS IN SHADOW_VALIDATED STATUS? **YES**
HAS DECISION POLICY v3 CHANGED? **NO**
DO LOW-CONFIDENCE WATCH_ONLY RESTRICTIONS REMAIN? **YES**
ARE ALL VALIDATED MARGINALS UNCHANGED? **YES**
ARE ALL VALIDATED JOINT MODELS UNCHANGED? **YES**
IS THERE ANY KNOWN UNRESOLVED TEMPORAL LEAKAGE? **NO**
DO ALL COUNT THRESHOLDS REMAIN MONOTONIC? **YES**
DO ALL JOINT PROBABILITIES PASS LOGICAL / FRÉCHET CONSTRAINTS? **YES**
IS THERE NOW ONE CANONICAL SOURCE OF MODEL STATUS? **YES** (`research/model_registry.py`, layered over the pre-existing `player_props/registry.py`)
IS THERE NOW ONE CANONICAL SOURCE OF MARKET READINESS? **YES** (`market_registry.py`, audited, 142 markets confirmed)
IS OPERATIONAL UX CLEARLY SEPARATED FROM RESEARCH? **YES** in the prototype; **PARTIAL** in the real dashboard (17/20 pages self-label RESEARCH already; nav doesn't yet visually tier the 1 operational page)
DOES THE DASHBOARD FAIL CLOSED WHEN LIVE DATA IS MISSING? **YES**
IS THE PROSPECTIVE 2026-27 VALIDATION PROTOCOL FROZEN AND READY? **YES**
IS THE FIRST-LIVE-DAY CHECKLIST READY? **YES**
IS LIVE DRAFTKINGS NHL PROP CONTRACT FULLY VERIFIED? **PARTIAL** (SOG only)
IS THE ENGINE READY TO RECORD PROSPECTIVE MODEL OBSERVATIONS ON DAY ONE? **NO** — the recorder itself is not yet built (Section BC)
IS THE ENGINE READY TO CLAIM LIVE SPORTSBOOK EDGE TODAY? **NO**
IS THE ENGINE READY FOR GENERALIZED PARLAY EV? **NO**
IS THE ENGINE READY FOR FULL JOINT SIMULATION? **NO**
WHAT IS HISTORICAL RESEARCH READINESS, 0-10? **8**
WHAT IS LIVE STRAIGHT-BET READINESS, 0-10? **2**
WHAT IS UX/PRODUCT READINESS, 0-10? **5**
WHAT IS PARLAY READINESS, 0-10? **1**
WHAT IS SIMULATOR READINESS, 0-10? **2**
CURRENT FULL TEST RESULT? **1,680 / 1,680**
WHAT IS THE NEXT SINGLE MODEL / RESEARCH SLICE? Prospective observation recorder (Section BC)
WHAT IS THE NEXT SINGLE UX / PRODUCT SLICE? Port the opportunity card into real Streamlit (Section BD)

---

**STOP AFTER PRESEASON MASTER CONSOLIDATION.**
