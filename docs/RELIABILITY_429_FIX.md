# Reliability Fix: NHL API 429 Handling

**Date:** 2026-09-24. **Trigger:** a real, manual dry run of `operational.nhl_sync` (performed during the Production Readiness Audit, before this job had ever been scheduled) hit a genuine `429 Too Many Requests` from `GET /v1/roster/TBL/current` partway through a roster batch. `ingest/nhl_api.py` had no retry/backoff logic anywhere — the single rate-limited response propagated all the way up and made the entire daily sync report `status: "FAIL"`, even though schedule/boxscore data for every game in that day's window had already been ingested and committed successfully (confirmed: `games_seen: 19, games_finalized: 4` in the same run that reported `FAIL`).

## Root cause

1. `ingest/nhl_api.py::_get_json()` — the single HTTP call wrapper used by every fetch function in the module — called `resp.raise_for_status()` immediately with no retry of any kind.
2. `ingest_current_roster_identities()` looped over teams and only called `conn.commit()` once, after the *entire* loop finished — so a failure on team N discarded every prior team's already-synced roster data from that batch (never actually lost from the database, since it was never committed, but silently absent from the result either way).
3. `operational/nhl_sync.py::run_nhl_sync()` had one binary `status: "OK"/"FAIL"` for the whole run, with no way to distinguish "the critical schedule/boxscore path failed" from "roster sync alone degraded."

## Fix

**`ingest/nhl_api.py`:**
- `_get_json()` now retries on `429` and `5xx` responses, up to `MAX_RETRIES` (4) additional attempts, with exponential backoff (`BASE_RETRY_DELAY_SECONDS * 2**attempt`, capped at `MAX_RETRY_DELAY_SECONDS` = 30s).
- A `Retry-After` header (integer-seconds form) is honored over the computed backoff when present and parseable.
- Any other `4xx` (400/401/403/404/...) is **not** retried — those indicate a request that will never succeed no matter how many times it's repeated.
- Every retry attempt and the final exhausted-retry failure are logged as structured JSONL records to `operational/logs/nhl_api_retry_log.jsonl` (append-only, same convention as `operational/new_contract_candidates.jsonl`).
- `max_retries`/`base_delay` default to `None` and are resolved from the module constants **inside the function body**, not bound into the signature — this project's own well-documented "default bound at import time" footgun (see `archive_result()`'s and `_credits_spent_since()`'s identical fix in the live-odds module) would otherwise make `mock.patch("ingest.nhl_api.MAX_RETRIES", ...)` silently do nothing.
- `ingest_current_roster_identities()` now commits **per team**, the moment that team's sync succeeds, and catches a per-team exception rather than letting it abort the whole batch. Returns `status: "SUCCESS" | "PARTIAL_SUCCESS" | "FAILED"` plus `failed_teams: [{"team", "error"}, ...]`.

**`operational/nhl_sync.py`:**
- `run_nhl_sync()`'s `status` is now `"SUCCESS" | "PARTIAL_SUCCESS" | "FAILED"`, with a `components: {"schedule_boxscore": ..., "roster": ...}` breakdown. Only a genuine failure of the schedule/boxscore path itself produces `"FAILED"` — a degraded or fully failed roster sync produces `"PARTIAL_SUCCESS"`, because settlement and the rest of the daily pipeline read game results, never roster data (confirmed in the Production Readiness Audit, Section F).

**Consumers updated to match:** `sync_daily.py` (exit code 1 only on `"FAILED"`; `"PARTIAL_SUCCESS"` prints a visible warning and exits 0), `operational/report.py` (three-way PASS/PARTIAL/FAIL line), `operational/readiness.py` (schedule/results readiness treats `SUCCESS` and `PARTIAL_SUCCESS` both as current data).

## Tests

`tests/test_nhl_api_retry.py` (11 tests): 429-then-success, `Retry-After` honored, malformed `Retry-After` falls back to exponential backoff, backoff doubles per attempt, retries-exhausted raises and logs both events, non-retryable 4xx never retries, a response with no `status_code` attribute at all (several existing test doubles in this suite model exactly this) is treated as success, the structured log file actually gets written, one team failing doesn't block the others, an earlier team's successful write survives a later team's failure (the durability bug), and all-teams-failing reports `FAILED` not `PARTIAL_SUCCESS`.

`tests/test_operational_daily_sync.py` — one new test (`TestNHLSyncRosterDegradationIsNonCritical`) proving a roster 429 degrades the overall run to `PARTIAL_SUCCESS` while `games_finalized` and the `games` table both still reflect the real ingested data.

**Full suite: 2,607 / 2,607 passing** (was 2,595 before this fix).

## What this does NOT change

No settlement, prediction, or model logic was touched. No database schema changed. `pricing/odds_math.py` and every other module unrelated to NHL API HTTP calls are unaffected.
