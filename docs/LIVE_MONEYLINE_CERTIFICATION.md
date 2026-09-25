# Live Moneyline (T-35) Certification

**Status (2026-09-25, First Live Prep block):** `ARCHITECTURE_READY = YES` (when the scheduler runs clean master), `LIVE_OBSERVED = NO`, `LIVE_CERTIFIED = NO`.
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


## Certification states and exact transitions (`python3 -m operational.first_live_certification`)

| State | Meaning | Enters when |
|---|---|---|
| `NOT_READY` | a prerequisite is missing | launchd job not loaded, **scheduler not running clean master**, quota insufficient, publisher off, deployment mode not ACTIVE, or end-to-end dry run not certified |
| `ARCHITECTURE_READY` | prerequisites met, no listed cluster upcoming | — |
| `WAITING_FOR_FIRST_REAL_CLUSTER` | prerequisites met **and** a provider-listed cluster is upcoming | default state today |
| `LIVE_OBSERVED` | a real, provider-listed cluster fired and produced real provider data (credit spent, odds rows stored) | but at least one gate is missing (quote outside T-40..T-30, no decision evaluated, cloud publish not SUCCESS, or the ledger proof failed) |
| `LIVE_CERTIFIED` | one real cluster met **every** gate | quote inside [T-40, T-30] · expected credit spent · rows stored · ≥ 1 decision (BET / WAIT / PASS) evaluated at T-30 · immutable observation persisted in the ledger · a `REAL_MARKET_PAPER` bet exists iff the action was BET · cloud snapshot published. **An HTTP 200 alone never certifies.** |
| `FAILED` | the latest real, listed cluster ended with no provider data (network/API failure, empty response, missed window, quota deferral) and nothing has certified | clears when a later cluster observes or certifies |

Evidence outranks configuration: once a real cluster certified, a later configuration problem does not un-certify history. Unlisted clusters are never a failure.

## Pre-flight (before the event) and post-event report

The same read-only command prints both. **Pre-flight** (zero paid requests, no network): next cluster, games, expected start, T-35 target, T-30 anchor, provider listing (from the newest archived events response), scheduler loaded + **the branch/commit it executes** (must be clean master), credits remaining + sufficiency, cloud publisher enabled, deployment mode, machine power risk, architecture status. **Post-event**: cluster fired, actual pull timestamp, pull in window, provider listed, credits spent, DK rows stored, decisions evaluated, BET / WAIT / PASS / DATA_UNAVAILABLE counts, observations persisted, paper bets, cloud publish status and snapshot hash, final state.

## Power reliability (Final Pre-Live Ops)

**Verified on this Mac (`pmset -g custom`, `pmset -g sched`, `man pmset`):** AC `sleep 1` (idle-sleep after **one minute**; the machine is awake only while an app such as Claude holds an assertion), `displaysleep 180`, Power Nap on, `womp` on, low-power mode on. `pmset` supports one-time scheduled events — syntax from `man pmset`: `pmset schedule wake "MM/dd/yy HH:mm:ss" [owner]` (24-hour **local** time, **two-digit year**, quoted; types `sleep|wake|poweron|shutdown|wakeorpoweron`) — but **modifying power state requires root** (`sudo -n` here answers "a password is required"), so this code never runs it on its own.

**What each mechanism can and cannot do**

| | Can | Cannot |
|---|---|---|
| `pmset schedule wake` (owner, sudo) | wake a *sleeping* Mac at a set time | wake a Mac that is **shut down** (`wakeorpoweron` is documented for that but power-on from shutdown is not verifiable here and is unreliable on Apple silicon); make a **lid-closed** MacBook fully operational — it normally only dark-wakes and drops back to sleep, so the **lid must be open** (or an external display attached) and the Mac on AC |
| `caffeinate -i -t` (no root) | keep an *awake* Mac from idle-sleeping, self-ending | wake a sleeping Mac, override lid-close, or stop an explicit sleep/shutdown/restart |

**Wake → assertion handoff (no gap).** After a wake the 1-minute idle timer starts immediately, but the 2-minute `moneyline-pregame` timer can be up to two minutes late (launchd's interval does not count sleep). So the job also keeps a tiny **wake-guard** process alive whenever a provider-listed hold window is within 36 h. The guard is merely *suspended* during sleep; the instant the Mac wakes its 5-second poll notices it is inside an assertion window and starts one `caffeinate -i -t <seconds>` — well inside the 60-second idle timer. The assertion window opens 10 minutes before the hold window (T-80) so a wake at T-75 is held from its first second; the assertion ends by itself at T-10 (after the T-30 decision and the cloud publish). The guard exits when no listed window remains and after 36 h at most; it is never started under test or on a STANDBY machine.

**Planning (read-only):** `python3 -m operational.first_live_certification --wake-plan` (or `python3 -m operational.schedule_next_wake`) prints the next provider-listed cluster, its T-40 / T-35 / T-30 times, the assertion window and the exact verified command; `--verify` reads `pmset -g sched` and reports whether a wake event covering the cluster exists (event in [assert-start − 20 min, T-40]). `--apply` (owner-invoked only) tries `sudo -n` and **fails closed** with the exact command if a password would be needed; it never asks for or stores a password and never changes any sleep/display setting.

**Missed window is still missed.** If the Mac is nonetheless asleep/off through [T-40, T-30] the cluster is recorded `MISSED_WINDOW` / `MACHINE_ASLEEP`, a macOS notification says so, and **nothing is pulled late**. None of the power code touches the pull.

**Notifications.** Zero-cost macOS Notification Center via `osascript` (no email/SMS/paid service), detached and non-blocking, off under tests or with `NHL_ENGINE_NOTIFY=OFF`, provider-listed clusters only: T-35 pull SUCCESS, pull FAILED, MISSED_WINDOW.

**Retention.** The rolling audit keeps the last 200 clusters; the first real cluster's compact record (first observed / first certified / first failure) is additionally preserved **write-once** in `operational/runtime/moneyline_pregame_first_live.json` (no raw responses, no credentials).
