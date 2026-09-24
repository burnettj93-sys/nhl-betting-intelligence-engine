# Implementation Plan

Companion to `docs/PRODUCTION_READINESS_AUDIT.md` — read that first for evidence behind every item here. Ordered so that the very first milestone from the brief — *tonight's preseason games flow automatically from ingestion through to a real settled paper bet and a real morning post-mortem* — becomes achievable, before any new predictive feature work.

`P0` = must work before regular season · `P1` = important shortly after · `P2` = valuable enhancement · `P3` = future idea

---

## P0 — must work before regular season

1. **Schedule real NHL data sync.** Install `operational.nhl_sync` as a launchd job (daily, early morning — matches the brief's own "EARLY MORNING: ingest previous night's final data" step). This is the single highest-leverage fix in this whole plan: everything in Settlement, Post-Mortem, and Model Performance is currently blocked on `nhl.db` having zero rows since 2025-11-27.
2. **Schedule settlement.** Install `operational.settle_daily_observations` as a launchd job, run after (1) each morning. Already idempotent and tested — this is pure activation, not new code.
3. **Schedule the daily post-mortem.** Add a real `operational.daily_postmortem` entry point (a thin CLI wrapper around `run_daily_postmortem()`, since today it's only reachable via the dashboard page) and schedule it after (2). Decide whether it should write a persisted report artifact (the brief's `postmortems/YYYY-MM-DD` idea) — today's Morning Review page deliberately never writes one; a scheduled job should, so history survives even if nobody opens the dashboard that day.
4. **Game-type classification.** Confirm/add `game_type` (PRESEASON/REGULAR_SEASON/PLAYOFFS) on every ingested game and thread it through `nhl_sync` → `predictions` → any rolling-stat feature so preseason data can never silently contaminate regular-season rolling stats (this may already partially exist via `gameType` from the NHL API — verify before adding a parallel concept).
5. **DATA_CERTIFIED concept.** Add the explicit `DATA_PENDING` / `DATA_CERTIFIED` / `DATA_UNAVAILABLE` state the brief asks for, gating settlement so no bet ever settles off a boxscore that arrived incomplete. Likely a thin layer on top of the existing `game_state` transitions (SCHEDULED/LIVE/FINAL) already in `schema.sql`.
6. **Yahoo compliance decision.** This blocks any further Yahoo work under the new agreement. Needs your decision between the three options in the audit's Section J before I touch `fantasy/` — building on non-compliant storage now would mean redoing it later. My recommendation: (b) keep `fantasy/` as the isolated module (matches your own Phase 10 diagram) but rework `fantasy_store.py`/`cache.py` to a short-TTL, session-scoped in-memory cache instead of a persistent DB table, and re-verify against §2.c.vii line by line before re-enabling any real OAuth flow.
7. **Preseason data certification pass (Phase 1 & 15).** Once (1)-(5) are live, replay every 2026 preseason game played so far (real games exist from 2026-09-19 onward) through the pipeline and produce `docs/PRESEASON_REPLAY_REPORT.md` exactly as the brief specifies (games expected/discovered/ingested/certified, missing fields, failed requests, settlement/post-mortem errors).
8. **Verify immutable snapshot completeness.** Read what `operational/record_daily_predictions.py` actually writes into `data_snapshot_references` today; either confirm it already carries goalie/roster/injury/line/TOI state, or extend it. Don't rebuild the ledger itself — it's already well-built (DB-trigger immutability, idempotency, version pinning all present).
9. **Reconciliation tests (Phase 1).** Team-shots-vs-player-shots, TOI plausibility bounds, game-state transition tests — add these against the now-real data from (1), since they were previously untestable with no real rows to check.
10. **Multi-user auth, server-side enforced.** Even a minimal ADMIN/USER split with Yahoo routes hard-gated server-side (not just hidden in the UI) needs to land before any friend gets a link — this is a from-scratch build (confirmed nothing exists today), so it should be scoped and started early rather than bolted on last.

## P1 — important shortly after

- Central source-health record (`source_name`, `last_success_at`, `status`, etc.) as its own structured table, feeding a real Data Health page — today's `system_health.py` is close but ad hoc per source; formalizing it makes the "don't generate recommendations from stale data" gate uniform across all sources, not just odds.
- Admin Operations page (Phase 13) — most of the underlying data (`system_health.py`, scheduler state, credit counters) already exists; this is mostly a new dashboard page assembling existing signals, not new backend work.
- CLV / market-snapshot display wired into the Model Performance dashboard once real closing lines exist to show.
- Structured logging + basic alerting (even a local notification or email on a failed scheduled job) — currently a failure in any of the 4+ scheduled jobs is silent unless someone checks `operational/logs/`.
- Database backup job for `paper_bankroll.db` and the not-yet-created `prospective_observations.db`.
- Injury ingestion, once a reliable real source is confirmed to exist.

## P2 — valuable enhancement

- Line-combination / linemate ingestion.
- Fantasy recommendation features beyond what `fantasy/recommendations/` already stubs out (most of Phase 10's wish list already has a file — verify each is real logic vs. a placeholder before assuming "missing").
- `/health` and `/health/data` as literal HTTP endpoints, if this ever moves off pure Streamlit (Streamlit itself has no route model for this today).
- Standardize model version strings across all registry entries to the `family_vX.Y.Z` convention the brief suggests (cosmetic/consistency, not functional).

## P3 — future idea

- Draft-day / start-of-season fantasy tooling (draft rankings already has a file — revisit once real Yahoo league data is flowing under a compliant architecture).
- Anything from Phase 10's longer wish list not already covered above (schedule optimization, off-night streaming targets, etc.) — real value here depends on the Yahoo compliance decision in P0 #6 landing first.

---

## What I will NOT do without stopping first

Per the brief's own rules and this repo's demonstrated care about historical integrity:
- No migration on `nhl.db`, `paper_bankroll.db`, or the prospective ledger schema without a backwards-safe path and your sign-off.
- No change to `research/model_registry.py` statuses, `decision_policy` versions, or any validated threshold.
- No re-architecture of `operational/prospective_ledger.py`'s immutability mechanism — it's already correct; only extending what gets written into it, if needed.
- No Yahoo OAuth flow re-enabled, and no change to `fantasy/storage/`'s persistence model, until you pick an option in P0 #6.
