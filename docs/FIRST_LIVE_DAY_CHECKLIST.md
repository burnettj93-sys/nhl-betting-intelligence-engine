# First Live Day Checklist

A simple timeline for the owner's first real game day with the engine and the Community Cloud app. Times are Eastern (the Mac's launchd clock). Everything marked **auto** runs by itself; everything marked **you** is a check or action. One command answers most questions:

```bash
python3 opening_day_readiness.py        # per-component states (READY / WAITING_FOR_LIVE_MARKET / PARTIAL / ...)
```

Read the `OVERALL` line and the table under it. `WAITING_FOR_LIVE_MARKET`, `WAIT_ONLY` and `NO_REAL_SAMPLE_YET` are expected on day one. `PARTIAL`, `STALE` or `FAILED` need attention.

## Owner quick card — first real T-35 (Tuesday 2026-09-29, first listed game 21:00Z = 17:00 EDT)

Times are computed from the schedule: `python3 -m operational.first_live_certification --wake-plan` prints the live values (trust it over this card if the schedule moves).

| When (EDT) | Do |
|---|---|
| **Before the day (once)** | Schedule the one-time wake — the Mac idle-sleeps after **1 minute** on AC and `caffeinate` cannot wake a sleeping Mac. Run in a terminal (asks for your password): `sudo pmset schedule wake "09/29/26 15:45:00" nhl-engine` — or try `python3 -m operational.schedule_next_wake --apply` (non-interactive; if it needs a password it just prints the command above). Confirm with `pmset -g sched` or `python3 -m operational.schedule_next_wake --verify` (reads the OS's own event list). |
| **15:45** | Mac wakes by itself. Must be: **plugged in, lid OPEN** (or external display), logged in, **not shut down**. A wake-guard already running (started by the 2-minute job while the Mac was awake) starts a self-ending `caffeinate` within ~5 s of the wake and holds until ≈ 16:50. |
| **≈ 16:15** | `python3 -m operational.first_live_certification` → `PRE-FLIGHT: READY` (or the exact owner action / failed prerequisite). |
| **16:25** | T-35 pull (one request, 1 credit). A macOS notification says SUCCESS / FAILED / MISSED_WINDOW. |
| **16:30** | T-30 decision. |
| **≈ 16:35** | `python3 -m operational.first_live_certification` again → `LIVE_CERTIFIED` (PASS / WAIT is enough), or the exact failed gate. The first real cluster's record is also preserved permanently in `operational/runtime/moneyline_pregame_first_live.json`. |

## Night before

| | Check |
|---|---|
| you | Mac plugged in, **prevent sleep** while the lid is closed or use `caffeinate -s` (missed pulls were the top reliability problem: 8 of 41 moneyline runs failed offline). |
| you | `python3 -m operational.scheduler_audit` — 11 jobs, status `OK`. (`runs` restarts at every boot; a 0 after a late boot is normal — see `docs/SCHEDULER_INVENTORY.md`.) |
| you | `python3 -m operational.cloud_preflight` → `PASS_WITH_OWNER_ACTIONS`; do the owner actions in `docs/STREAMLIT_COMMUNITY_CLOUD_RUNBOOK.md`. |
| you | Open the Cloud app (Streamlit private sharing is the only gate; no app login): banner shows **SNAPSHOT CURRENT**; Diagnostics shows source `REMOTE`, fetch `OK`, schema 2. |

## Morning (07:00–09:00)

| When | Job (auto) | What you check |
|---|---|---|
| 07:00 | `daily-nhl-sync` — NHL schedule/results/rosters | Diagnostics → Owner daily check: **Did the NHL sync run? YES** |
| 07:15 | `daily-settlement` | Settlement YES. Zero real predictions on day one → `0 PENDING`, that is correct |
| 07:30 | `daily-postmortem` | Morning Review says **NO_DATA / WAITING_FOR_SETTLED_DATA** until real bets settle — never a model conclusion on an empty sample |
| 07:45 | `database-backup` | Backups READY |
| 08:00 | `moneyline-snapshot` (1 credit) | Odds current (≤ 3 h). The cloud snapshot republishes after it (`NHL_ENGINE_CLOUD_PUBLISH=ON`) |
| 08:15 | `daily-props-pull` (~30 credits) | Credits remaining trending as expected (`ODDS API QUOTA`) |

**You:** open Today on your phone. Confirm: DATA AS OF and LAST UPDATED show today; `MARKET FRESHNESS` shows odds CURRENT; the demo board is labeled **SIMULATED — DEMO ONLY**; *Recorded Recommendations* may say "No real-market MONEYLINE recommendation has been recorded yet" — expected until a pregame pull lands in the decision window (see `docs/ODDS_FRESHNESS_QUOTA_ANALYSIS.md`).

## Midday (12:00–16:00)

| When | Job (auto) | What you check |
|---|---|---|
| 13:00 | `moneyline-snapshot`, `midday-schedule-refresh` | Schedule changes picked up; odds still CURRENT |
| every 30 min | `pregame-targeted-refresh` (NHL rosters/goalies) | Starter data stays `PARTIAL` (projection only — no confirmed-starter source) → Saves stays **WAIT_ONLY** |
| every 15/30 min | `prop-sweep-first/second` | 0 credits until DraftKings posts `player_shots_on_goal` / `player_total_saves`. A new market key is flagged in `New Contract Candidates` — **never** auto-certified |

## Pregame — the first REAL moneyline cluster (earliest: 2026-09-29, game 21:00Z → pull ≈ 20:25Z / 16:25 EDT)

`ARCHITECTURE_READY = YES`, `LIVE_OBSERVED = NO` until this cycle completes once. PASS / WAIT decisions certify it; a BET is not required. Details: `docs/LIVE_MONEYLINE_CERTIFICATION.md`.

**BEFORE T-35** (do at least 30 minutes ahead)

| Check | Expected |
|---|---|
| Mac on, awake, online, plugged in | a missed window is *never* pulled late — it is recorded `MISSED_WINDOW`/`MACHINE_ASLEEP` |
| `python3 -m operational.scheduler_audit` | 11 jobs, `moneyline-pregame` loaded, status OK |
| `python3 opening_day_readiness.py` | `MONEYLINE_T35_ARCHITECTURE READY`, `NEXT_T35_CLUSTER READY` (cluster, target pull, anchor, scheduler armed, quota sufficient), `ODDS API QUOTA` READY |
| Cloud publisher healthy | `CLOUD SNAPSHOT` READY; `NHL_ENGINE_CLOUD_PUBLISH=ON` |

**AT T-35** — one league-wide DraftKings moneyline request (1 credit), only if the provider lists the game.

| Check | Expected |
|---|---|
| `operational/logs/moneyline_pregame.log` | a multi-line JSON block: `status: SUCCESS`, `credits_spent_this_run: 1`, `captured_at_utc` between T-40 and T-30, `real_odds_bridge.rows_written` > 0, `audit` outcome |
| `operational/runtime/moneyline_pregame_state.json` | cluster `status: DONE`, `in_decision_window: true`, `attempts: 1` |

**AT T-30** — the unchanged engine decides.

| Check | Expected |
|---|---|
| `real_recommendation_orchestrator` in the same log block | `data_unavailable` **0** for that cluster's games; each side is BET, WAIT or PASS (`WAIT` is likely at first: Elo-staleness / goalie gates) |

**AFTER**

| Check | Expected |
|---|---|
| `python3 -m operational.first_live_certification` | every check PASS (`paper_bet_if_bet` is `NOT_APPLICABLE` unless a BET); `LIVE_OBSERVED=True` |
| Ledger | immutable MONEYLINE observations for the cluster's games; a `REAL_MARKET_PAPER` bet only for a BET |
| Cloud | snapshot republished after the pull (`cloud_publish` in the audit record); Today → Recorded Recommendations shows the rows with price captured / game start / freshness; banner odds CURRENT |
| Diagnostics | *Moneyline T-35 live status* → `LIVE_OBSERVED` |

If a cluster misses: the audit record says exactly why (`PROVIDER_NOT_LISTED`, `QUOTA_DEFERRED`, `NETWORK_FAILED`, `API_FAILED`, `EMPTY_RESPONSE`, `MISSED_WINDOW`/`MACHINE_ASLEEP`). Nothing is patched afterwards.

## Postgame (after the last game goes FINAL)

| Check | Expected |
|---|---|
| `pregame-targeted-refresh` / `midday` ingest finals | `games_finalized` > 0 in `operational/logs/midday_schedule_refresh.log` |
| Tomorrow 07:15 settlement | Settles any recorded predictions exactly once; unresolved games stay PENDING; pushes handled; demo results never mix with `REAL_MARKET_PAPER` |

## Next morning

| Check | Expected |
|---|---|
| `python3 opening_day_readiness.py` | No `FAILED`; `SCHEDULERS 10/10`; `BACKUPS`, `SETTLEMENT`, `POSTMORTEM` READY |
| Diagnostics → Owner daily check | Engine healthy YES; cloud data current; odds current at the morning pull; credits ≥ 150 |
| Cloud app | Same DATA AS OF as your morning pull; if the banner says **REMOTE UPDATE FAILED**, the app is showing last-known-good — re-run `python3 -m operational.publish_cloud_snapshot` |
| Git | `git fetch && git log origin/cloud-data --oneline \| head` — a handful of commits per day, not dozens (history is bounded to 50, then reset) |

## Expected automated jobs (10)

`daily-nhl-sync` 07:00 · `daily-settlement` 07:15 · `daily-postmortem` 07:30 · `database-backup` 07:45 · `daily-props-pull` 08:15 · `moneyline-snapshot` 08/13/17/20 · `midday-schedule-refresh` 13:00 · `pregame-targeted-refresh` /30 min · `prop-sweep-first` /30 min · `prop-sweep-second` /15 min. Cloud publication piggybacks on the odds pull, settlement and postmortem (and the sweeps only when they record something); it is not a job of its own.

## Never do on day one

Do not place real money based on this app. Do not loosen any gate to "see a recommendation". Do not add Yahoo or paid providers. Do not deploy the VPS unless free Streamlit fails (`docs/VPS_CUTOVER_RUNBOOK.md`).


## If the Mac boots late in the morning

Nothing to do: the next 30-minute NHL refresh runs the bounded morning catch-up (sync → settlement → post-mortem → backup, only the stages that missed their slot, ≥ 30 min after each slot, ≤ 2 attempts per stage per day). Check `operational/runtime/morning_catchup_state.json` or `python3 opening_day_readiness.py` → `NHL_DAILY_SYNC_CATCHUP`.
