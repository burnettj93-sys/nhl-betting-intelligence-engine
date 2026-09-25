# Live Moneyline (T-35) Certification

**Status (2026-09-25, Live Run Reliability block):** `ARCHITECTURE_READY = YES`, `LIVE_OBSERVED = NO`.
The cadence is **not** called live-certified until a real, provider-listed start-time cluster completes the whole chain. The first candidate is the **2026-09-29 21:00Z** game (T-35 pull ≈ 20:25Z = 16:25 EDT); the provider lists no NHL event earlier than 2026-09-29, so today's and the weekend's preseason clusters are `PROVIDER_NOT_LISTED` (0 credits).

## What "LIVE_OBSERVED" requires (all of it, for one real cluster)

1. the `moneyline-pregame` job **triggers** inside the capture window [T-40, T-30];
2. the provider **lists** the game (free `/events` check) and the pull **spends the expected credit** (≥ 1);
3. the quote is **stored** (`odds_snapshots` rows) with a capture time inside the decision window;
4. the unchanged orchestrator **evaluates at T-30** — at least one decision that is BET, WAIT or PASS (a BET is *not* required; `DATA_UNAVAILABLE` alone does not count);
5. an **immutable observation is persisted** in the prospective ledger (and a `REAL_MARKET_PAPER` bet exists if — and only if — the action was BET);
6. the resulting **cloud snapshot is published** (`SUCCESS` / `PARTIAL_SUCCESS`).

Read-only report (spends nothing, imports no API client, opens the ledgers `mode=ro`):

```bash
python3 -m operational.first_live_certification
```

## Per-cluster audit record

`operational/runtime/moneyline_pregame_audit.json` (last 200 clusters; no payloads, no secrets): cluster id · games · scheduled starts · target pull time · actual pull time · provider-listed · API status · credits spent · odds rows stored · decision anchor · recommendations evaluated · BET / WAIT / PASS / DATA_UNAVAILABLE counts · cloud publish result · in-decision-window flag · one primary outcome plus tags.

## Outcome classification (never a bare FAILED)

| Outcome | Meaning |
|---|---|
| `PROVIDER_NOT_LISTED` | the free events call shows no game near the cluster → no credit spent |
| `QUOTA_DEFERRED` | the quota guard refused (hard reserve / daily soft budget) |
| `NETWORK_FAILED` | transport error (connection / timeout) after the client's bounded retries and the job's one retry |
| `API_FAILED` | the provider answered with an error (401 / 404 / 429 / 5xx) |
| `EMPTY_RESPONSE` | request succeeded but no odds rows were stored |
| `MISSED_WINDOW` (+ `MACHINE_ASLEEP`) | the capture window closed without a valid pull; `MACHINE_ASLEEP` when no firing of the job fell inside it (heartbeat gap) |
| `STORED_SUCCESSFULLY` + `DECISION_SUCCESS` | quote stored and ≥ 1 decision evaluated |
| `STORED_SUCCESSFULLY` + `DECISION_DATA_UNAVAILABLE` | quote stored but every side stayed `DATA_UNAVAILABLE` |

## Missed window = missed, never patched

If the Mac sleeps through [T-40, T-30] the job **does not** make a late paid pull — a quote captured after the window cannot satisfy the decision policy, and pretending it does would break temporal integrity. It records `MISSED_WINDOW` (once) and the decision stays `DATA_UNAVAILABLE`. Windows that closed before the job first ran are not reported as missed.

## Admin visibility

Diagnostics → *Owner daily check*: **Next decision cluster** (cluster, target pull, decision anchor, scheduler armed, quota sufficient) and **Moneyline T-35 live status** (ARCHITECTURE_READY / LIVE_OBSERVED, last cluster outcome). ADMIN-only; derived from the published snapshot (`health.operations`). Readiness: `MONEYLINE_T35_ARCHITECTURE` (READY) vs `MONEYLINE_T35_LIVE_OBSERVED` (`WAITING_FOR_FIRST_REAL_CLUSTER` until observed).
