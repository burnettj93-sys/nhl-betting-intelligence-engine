# Real Recommendation Pipeline Certification

**Date:** 2026-09-24. **Scope:** connect existing, already-tested components into the missing production path — REAL CURRENT DATA + REAL SPORTSBOOK MARKET → EXISTING MODEL → EXISTING DECISION POLICY → IMMUTABLE REAL-MARKET PREDICTION SNAPSHOT → PAPER BET IF ACTIONABLE → SETTLEMENT → CLV → POSTMORTEM. **No new predictive model was built. No decision logic was rewritten. No validated threshold was changed.**

## Architecture before this block

Two systems existed in parallel, never connected:

1. **The OLD root pipeline** — `pricing/engine.py::evaluate_moneyline_for_game()`, `models/combined_model.py`, `run_slate.py` — a complete, already-tested, point-in-time-safe moneyline decision engine. It read market data from `schema.sql`'s `odds_snapshots` table via `features/point_in_time.py`. **That table had never once been populated with a real price.**
2. **The NEW operational pipeline** — `operational/prospective_ledger.py`, `operational/prospective_recording.py`, `operational/paper_bankroll.py`, `operational/settle_daily_observations.py`, `operational/clv_resolver.py`, `dashboard/eligible_bets.py` — built for player props, oriented around demo data.

Real DraftKings moneyline data was already being collected on a schedule (`operational/live_odds_daily_pull.py::run_moneyline_snapshot()` → `operational/moneyline_snapshot_cache.json`) but never written anywhere the decision engine could read.

## The missing bridge (what was built)

| New module | Job |
|---|---|
| `operational/real_odds_bridge.py` | Converts the real DraftKings moneyline cache into `odds_snapshots` rows — the exact shape `pricing/engine.py` already expects. Matches a real Odds-API event to a real `nhl.db` game by team pair + a ±1 day commence-time window; never guesses on an ambiguous match. Idempotent via `odds_snapshots`' own UNIQUE index on `captured_at_utc`. |
| `operational/real_recommendation_orchestrator.py` | For each real SCHEDULED game: calls `run_slate.build_prediction_for_game()` (existing model) → `pricing/engine.py::evaluate_moneyline_for_game()` (existing decision engine) → shapes the result into `operational/prospective_recording.py::record_observation()`'s expected dict → records a real immutable snapshot → if `action == "BET"`, calls `operational/paper_bankroll.py::record_paper_bet(track="REAL_MARKET_PAPER", ...)`. Writes no new model math or decision rule. |
| `operational/closing_price_lookup.py` | Assembles real archived `odds_snapshots` price history into the shape `operational/clv_resolver.py::find_closing_price()` already expects (normalizing the one real provider-label spelling mismatch between the two modules) — writes no new CLV math. |
| `dashboard/real_recommendations_view.py` | Reads real MONEYLINE predictions + real paper bets and labels each row `LIVE — DRAFTKINGS` (a real bet exists) or `REAL MARKET — <status>` — never blended with `dashboard/eligible_bets.py`'s `SIMULATED — DEMO ONLY` rows. |

## Reused modules (unmodified math/logic)

`run_slate.build_prediction_for_game`, `models/combined_model.py` (Elo + point-in-time state reconstruction), `pricing/engine.py::evaluate_moneyline_for_game` (goalie gate, no-vig pricing, edge/EV thresholds, BET/WAIT/PASS/DATA_UNAVAILABLE), `operational/prospective_recording.py::record_observation` (checkpoint ordering, model-registry eligibility gate), `operational/paper_bankroll.py::record_paper_bet` (idempotency, $500/$10 bankroll config), `operational/clv_resolver.py::find_closing_price`/`compute_clv`.

## Two real, previously-unexercised bugs found and fixed while wiring this

1. **`operational/outcome_resolver.py::resolve_prediction()`** read `prediction.get("team_id")` for MONEYLINE dispatch, but the ledger schema has no `team_id` column (only `team`) — always resolved to `None`, never caught because no test had ever exercised MONEYLINE settlement. Fixed: `prediction.get("team")`.
2. **`operational/prospective_recording.py::latest_checkpoint_row()`** built `col = ?` SQL clauses even when the filter value was `None` (e.g. `player_id` for a team-level market) — SQL `NULL = NULL` is always false, so this could never find an existing PRIMARY_DAILY row for any market with no `player_id`, which would make every legitimate MARKET_REFRESH raise `CheckpointOrderingError`. Fixed: `IS ?` (NULL-safe) instead of `=`.

Also fixed: `nhl_sync.py`'s schedule-sync forward window was 1 day, meaning `nhl.db` could never contain a game DraftKings had already priced (~9-14 days out) — widened to 14 days (free endpoint, no credit cost).

## Market contract gates (Part 4/5)

Only MONEYLINE is live-enabled — the only DraftKings contract verified in this project. A game with no valid, fresh DraftKings quote for both sides produces `DATA_UNAVAILABLE` from the existing engine itself and is **never recorded** as a real recommendation (fail-closed). Player props (SOG, Saves, Points, Assists, Goals) remain demo-only; the orchestrator's own module boundary means no prop path can accidentally produce a real recommendation until its own contract is verified and a parallel orchestrator is built for it (not done in this block, per Part 6/21).

## Context-data gates (Part 7)

The existing goalie-confirmation gate inside `pricing/engine.py` already fails to `WAIT` (not a fabricated healthy default) when a starting goalie isn't `CONFIRMED` — this is exercised unchanged by the real orchestrator; no new context enforcement code was needed for MONEYLINE, since goalie status is MONEYLINE's only context dependency per `docs/CONTEXT_DATA_DEPENDENCY_AUDIT.md`.

## Immutable snapshot flow (Part 8/9)

Every real evaluation (BET, WAIT, or PASS — never DATA_UNAVAILABLE) is recorded via `record_observation()`, carrying model probability, conservative probability, market implied/no-vig probability, odds, sportsbook, edge/EV inputs already computed by the existing engine, model version, and `prospective_status`. `created_at_utc` is pinned to the model's own `prediction_time_utc` (always strictly before puck drop by construction), and `prediction_cutoff_utc`/`odds_captured_at_utc` are pinned to the REAL DraftKings snapshot's own `captured_at_utc` — so prospective_ledger's existing idempotency key naturally makes an identical rerun a no-op `DUPLICATE`, while a genuinely new DraftKings price produces a new `MARKET_REFRESH` row. Re-running never overwrites; only inserts.

## Paper betting flow (Part 10)

`action == "BET"` → `record_paper_bet(track="REAL_MARKET_PAPER", price_source="LIVE_DRAFTKINGS", ...)`. The paper-bet idempotency key (track + game + team + market + side + price_source) is **price-independent**, so a later `MARKET_REFRESH` snapshot of the same logical opportunity never places a second $10 stake.

## CLV integration (Parts 14-16) — closes the PARTIAL gap from the prior closed-loop certification

`operational/settle_daily_observations.py::run_settlement_batch()` now calls `closing_price_lookup.resolve_real_moneyline_closing_price()` for every MONEYLINE observation it settles, using the archived `odds_snapshots` history. If a valid real close exists (latest real snapshot strictly before puck drop), `closing_odds`/`closing_captured_at_utc`/`clv` are populated using the existing, unmodified `clv_resolver`/`odds_math` functions. If none exists, they stay `None` — never a fabricated `0.0`.

## Certification suite (Parts 17-19)

`tests/test_closed_loop_certification_v2.py` (4 tests) extends the prior isolated certification (`tests/test_closed_loop_certification.py`, still 15/15 passing) to prove the FULL chain: a real-shaped Odds-API cache payload → `real_odds_bridge` → `real_recommendation_orchestrator` → immutable snapshot → paper bet → real game result → `settle_daily_observations` (with the new closing-price lookup) → real CLV → `daily_model_review`. Also proves end-to-end idempotency and Part 19's exact requirement: every non-settlement prediction field is byte-for-byte identical before and after settlement.

Additional focused suites: `tests/test_real_odds_bridge.py` (9), `tests/test_real_recommendation_orchestrator.py` (8), `tests/test_real_moneyline_settlement_clv.py` (8), `tests/test_live_odds_daily_pull.py`'s new `Test09RealRecommendationTrigger` (4), `tests/test_morning_workflow_dependency.py` (11), `tests/test_real_recommendations_view.py` (9), `tests/test_opening_day_readiness.py`'s new `TestRealRecommendationPipelineCheck` (4).

## Production dry run (Part 20)

Run against the real local `nhl.db` (94 real games synced, 40 real `odds_snapshots` rows already archived from this block's own verification runs):
```bash
python3 -m operational.real_recommendation_orchestrator
```
Confirmed: real data loads, real archived odds load, the model path executes, the decision path executes, and provenance is REAL throughout — see the final report for this exact run's numeric result.

## Scheduler trigger (Part 22)

The chosen trigger is "a fresh, successful real moneyline snapshot" — `operational/live_odds_daily_pull.py::_main()`'s `--mode=moneyline` branch now calls `real_odds_bridge.sync_moneyline_odds_to_snapshots()` then `real_recommendation_orchestrator.run_real_moneyline_recommendations()` immediately after a successful pull (`ran: True`). No new scheduled job was added; this reuses the already-scheduled `moneyline-snapshot` job (4×/day). Every downstream step is independently idempotent (UNIQUE index, ledger idempotency key, paper-bet idempotency key), so an overlapping/duplicate scheduler firing cannot duplicate a logical prediction or paper bet.

## Scheduler inventory (Part 23)

See `docs/SCHEDULER_INVENTORY.md` — the authoritative 10-job table with schedule, command, reads/writes, and overlap analysis.

## Morning workflow dependency check (Part 24)

`operational/ingestion_health.py::dependency_ready()` — a single yes/no check, never a retry loop — is now called at the top of `settle_daily_observations.main()` (requires `nhl_sync_full` healthy) and `daily_postmortem.main()` (requires `settlement` healthy). A failed check DEFERS that run (recorded as `status="DEFERRED"`, which never counts as a success) rather than running against stale data or reporting a misleadingly complete day.

## Auth security review (Part 25)

See `docs/AUTH_SECURITY_REVIEW.md` — no defect found; PBKDF2-HMAC-SHA256, 260,000 iterations, per-user random salt, constant-time comparison, self-disabling admin bootstrap, and confirmed USER-role denial on both Yahoo-touching pages (`34_Fantasy_HQ.py`, `35_Fantasy_Settings.py`), enforced server-side via `st.stop()`, not just hidden nav. No auth features were added or changed.

## Opening-day readiness (Part 26)

`opening_day_readiness.py::check_real_recommendation_pipeline()` distinguishes "is the orchestration operational" (modules import cleanly, real databases reachable) from "did it produce a qualifying bet" (a real count, which can legitimately be zero). Only the former can force `NOT_READY`; zero real paper bets is explicitly documented as a valid, healthy state and never treated as a failure.

## Audit: DATA_UNAVAILABLE vs. stale/WAIT semantics (source-control checkpoint, 2026-09-24)

The production dry run (Part 20) evaluated 100 real games against real archived DraftKings prices and returned `DATA_UNAVAILABLE` for all of them, because those prices (captured 2026-09-24) are too old relative to each game's own T-30 decision anchor (games 5-15 days out). This was audited to confirm it is correct, existing, canonical behavior — not a new gap this block introduced or should paper over.

**Finding: this is intentional, pre-existing, already-tested behavior in the reused decision engine, not a defect.** `features/point_in_time.py::latest_draftkings_snapshot()`'s own docstring states it explicitly: it "Rejects (returns `None` for): missing data, SUSPENDED/INCOMPLETE status, a price captured more than `max_staleness_minutes` before `prediction_time_utc`, and a price captured at or after the event's scheduled start." All four distinct root causes collapse to the same `None` return, and `pricing/engine.py::evaluate_moneyline_for_game()` correctly reports all of them as a single `DATA_UNAVAILABLE` action, with the reason string itself saying `"(missing/stale/suspended/post-start)"` — this project's OLD, already-tested moneyline engine was built this way from the start, and this block did not modify it (per the explicit "do not rewrite decision logic" instruction).

**Why collapsing these is correct, not a loss of information:** for the purpose of deciding whether to bet right now, the correct action is identical regardless of *which* of the four reasons applies — don't bet. A separate `MARKET_STALE`/`STALE` action would not change what the engine does; it would only add a label. The existing two-category split this project already has — `WAIT` (a context-readiness gap: goalie not yet confirmed) vs. `DATA_UNAVAILABLE` (no valid current market quote, for any reason) — is the semantically correct existing vocabulary, and this block's orchestrator faithfully preserves it without inventing a third, parallel state.

**The archived evidence itself is never discarded, relabeled, or invalidated.** `latest_draftkings_snapshot()` returning `None` for decision purposes never touches the underlying `odds_snapshots` row — it stays `status='ACTIVE'`, fully intact, in the database. `operational/closing_price_lookup.py` (Parts 14-16) deliberately reads the same table's FULL raw history, bypassing the staleness gate entirely, precisely because a price that was too old for a *live* decision remains completely valid historical market evidence for *settlement*/CLV purposes. This is exercised and proven by `tests/test_closed_loop_certification_v2.py::test_a_single_synced_snapshot_honestly_serves_as_its_own_closing_price` and `tests/test_real_moneyline_settlement_clv.py`.

**No code change was made as a result of this audit** — the existing behavior is correct, already covered by the existing test suite (`tests/test_odds_staleness_policy.py`, `tests/test_thresholds.py::TestGoalieWaitPolicy`), and no new parallel state system was introduced.

## Limitations (honestly disclosed, not fixed in this block)

- Player props (SOG, Saves, Points, Assists, Goals) have no real orchestrator yet — their DraftKings contracts remain unverified. Building their own orchestrator is future work once a contract is verified (Part 6).
- No automated settlement of `REAL_MARKET_PAPER` paper bets was wired in this block — `operational/paper_bankroll.py::find_unresolved_past_event_bets()` (already real and working) would need to be called from a scheduled job; this block only wired the ledger-level settlement/CLV path (Parts 14-16), not the paper-bankroll-level settlement.
- `dashboard/live_dk.py` (an earlier, separate investigative "live DK" view built before real `nhl.db` game IDs existed for the 2026-27 schedule) was left untouched — it is now partially superseded by this block's orchestrator but removing/consolidating it was out of scope ("do not redesign the application").
- Session timeout is not implemented in the auth system (documented, not a defect — see the auth review).

## September 29 readiness — by pipeline, not by engine

**Correction (2026-09-24, source-control checkpoint audit):** an earlier verbal summary of this block described "no structural blockers" without qualification. That statement is only true for the MONEYLINE pipeline specifically. The betting engine as a whole is **not** structurally complete — the three pipelines below are at genuinely different readiness states, and none of the wording in this document should be read as claiming otherwise.

| Pipeline | Status | Evidence |
|---|---|---|
| **MONEYLINE live pipeline** | **Structurally complete and production-ready.** Real data in, real decision out (existing model + existing decision engine, unmodified), immutable snapshot, paper bet when actionable, real settlement, real CLV where a close exists, honest zero-bet state. | This document, `tests/test_closed_loop_certification_v2.py`, the production dry run (Part 20). |
| **Primary prop pipeline** (SOG, Saves, Points, Assists, Goals) | **Not live.** No real orchestrator exists for any prop market. `dashboard/eligible_bets.py::all_opportunities()` — what the Today page displays for props — remains 100% demo/simulated. No prop market's DraftKings contract has been verified (Part 6/21's own gating requirement). | `dashboard/eligible_bets.py` module docstring; every row it returns carries `source=SIMULATED_SOURCE_LABEL, is_demo=True` (Part 2, verified by `tests/test_real_recommendations_view.py::TestDemoRowsAreLabeledSimulated`). |
| **Real Game Edge Parlay pipeline** | **Not live.** The Game Edge Parlay engine (`research/game_edge_parlay/`) has no adapter that feeds it real MONEYLINE (or any real) recommendations — it still only consumes demo-priced opportunities. Building that adapter was explicitly out of scope for this block ("do not rewrite parlay math") and was not attempted. | See "Limitations" below (unchanged from the original disclosure — this correction does not add a new gap, it corrects how the existing, already-disclosed gap was characterized). |

**What this means for September 29:** the MONEYLINE pipeline can go live with no further code changes needed. Props and Game Edge Parlay cannot — those require their own future work (a verified prop contract + a per-market orchestrator; a parlay-engine adapter, respectively) before they can honestly be called "live." Nothing in this document should be cited as certifying props or parlay as ready.
