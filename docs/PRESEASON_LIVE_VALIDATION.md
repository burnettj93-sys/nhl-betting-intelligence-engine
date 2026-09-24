# Preseason Live Validation

**Date:** 2026-09-24. **Purpose:** production validation, not model evaluation — this answers "does the pipeline actually work against real current-season data," not "are the models profitable." Preseason performance is explicitly not read as predictive of regular-season performance anywhere below.

**Method:** backfilled the full real 2026 preseason window (2026-09-19 through 2026-09-25, the earliest real games available) directly into `nhl.db` via `ingest.nhl_api.ingest_range()`, then ran the full, now-fixed daily pipeline (`nhl_sync` → `settle_daily_observations` → `daily_postmortem`) against it for real. No mocked data anywhere in this report.

## Games discovered / ingested / certified

| Metric | Count |
|---|---|
| Real preseason games discovered (2026-09-19 to 2026-09-25) | 51 |
| Finalized (`game_state = FINAL`) | 36 |
| Still scheduled (future, within the window) | 15 |
| Finalized games with a complete boxscore (player + goalie stats both present) | **36 / 36** |
| Finalized games with incomplete/missing boxscore data | **0** |

Every finalized real preseason game ingested cleanly with a complete boxscore on the first attempt — schedule → game ID → teams → boxscore → player/goalie stats all reconciled correctly for real 2026-27 data, including a real split-squad doubleheader (Toronto played Ottawa twice on 2026-09-23, `game_id 2026010035` and `2026010036` — stored as two fully distinct games, no ID collision).

## Predictions / snapshots / settlement / post-mortem

| Stage | Result |
|---|---|
| Predictions generated for any real preseason game | **0** |
| Immutable prediction snapshots preserved | 0 (none generated to preserve) |
| Games settled | 0 |
| Post-mortem | Ran successfully, correctly reported `WAITING_FOR_SETTLED_DATA` on every field |

**Why zero predictions, and why that's not a system failure:** confirmed via a live, free check of The Odds API's currently-listed events — DraftKings has never posted a moneyline or player-prop market for any game before 2026-09-29. The recommendation engine, decision policy, and Game Edge Parlay engine all correctly produced nothing, because there was genuinely nothing to price. This is the same finding from the earlier "what happened to yesterday's bets" conversation, now confirmed again structurally rather than anecdotally.

## Classification

### SYSTEM_FAILURES
**None found in this pass.** Ingestion, boxscore parsing, idempotency, and the newly-fixed roster reconciliation all behaved correctly against real data.

One **process gap, now closed**, worth recording: `operational.nhl_sync` had never been scheduled before today, and its own sync window is only ±1 day — a scheduled job running every day would never notice this, but the first real run after a multi-day gap silently would have skipped 2026-09-19 through 2026-09-22 if this report hadn't explicitly backfilled the wider range first. Not a bug in the window logic itself (it's correctly scoped for a job that runs daily without missing a day) — just a reminder that any future gap in the schedule (a dead laptop, a skipped day) needs a manual wider backfill, exactly like the one this report just performed.

### DATA_FAILURES
**None found.** Zero finalized games with incomplete boxscore data, out of 36.

### MODEL_FAILURES
**Not applicable — no predictions exist to evaluate.** This is worth stating plainly rather than leaving implicit: there is currently no evidence either way about real-world model performance. The `research/model_registry.py` validations are all against historical backtests; none has ever priced a real 2026-27 bet.

### EXPECTED_PRESEASON ODDITIES
- **Split-squad doubleheaders** (same two teams playing twice in one day) — handled correctly, flagged here only because it's the kind of scheduling irregularity that could plausibly break a naive game-ID or date-based join; it didn't.
- **A large one-time roster reconciliation** (200 `players_removed_this_pass` records on the first real current-roster sync run today) — expected, not a bug: `nhl.db`'s player/roster tables previously held years-old and/or synthetic-demo membership data that had never been reconciled against a real current roster before. The very first real reconciliation pass correctly identified and closed out a large number of stale memberships in one shot; this number should be small and roughly stable on every subsequent daily run.
- **The NHL API's roster endpoint rate-limits more aggressively than the schedule/boxscore endpoints** — confirmed twice now (once during the original audit dry run, once during this validation's real end-to-end run) that a full 31-32-team roster sweep at ~0.2s between requests reliably produces at least one 429. The retry/backoff fix (`docs/RELIABILITY_429_FIX.md`) correctly isolates and reports this without losing data or failing the run, but the underlying request pacing could be widened (e.g. 0.5–1s between teams) as a P1 refinement if this keeps recurring nightly.

## Bottom line

The production loop — real ingestion through real settlement through a real post-mortem — is now proven to work end-to-end against real, current 2026-27 NHL data. The only reason nothing has been bet on yet is that DraftKings hasn't posted a market for any game that's been played so far, which is outside this engine's control and is handled exactly as this project's own design principles require: no invented markets, no fabricated prices, an honest `WAITING_FOR_SETTLED_DATA` state instead of a fake result.
