# Scheduler Inventory

**Date:** 2026-09-24 (Real Recommendation Pipeline block, Part 23). **Method:** direct read of every loaded `~/Library/LaunchAgents/com.nhlengine.*.plist` on this machine (`PlistBuddy -c "Print"`), cross-checked against each target script's own source. **10 jobs loaded**, matching the block's own stated count.

## Inventory

| # | Label | Schedule | Command | Reads | Writes | Paid API? |
|---|---|---|---|---|---|---|
| 1 | `daily-nhl-sync` | 07:00 daily | `python3 sync_daily.py` | NHL public schedule/roster/boxscore API | `nhl.db` (games, rosters, results); `ingestion_health_cache.json["nhl_sync_full"]` | No (free NHL API) |
| 2 | `daily-settlement` | 07:15 daily | `python3 -m operational.settle_daily_observations` | `prospective_observations.db` (PENDING rows); `nhl.db` (FINAL results, **and now** `odds_snapshots` for closing price, Parts 14-16); `ingestion_health_cache.json["nhl_sync_full"]` (new dependency check, Part 24) | `prospective_observations.db` (settlement columns only — immutable trigger protects prediction fields); `ingestion_health_cache.json["settlement"]` | No |
| 3 | `daily-postmortem` | 07:30 daily | `python3 -m operational.daily_postmortem` | `paper_bankroll.db`; `ingestion_health_cache.json["settlement"]` (new dependency check, Part 24) | A dated Markdown report file; `ingestion_health_cache.json["postmortem"]` | No |
| 4 | `database-backup` | 07:45 daily | `python3 -m operational.backup_databases` | `nhl.db`, `prospective_observations.db`, `paper_bankroll.db`, `auth_store.db` | Timestamped backup copies; `ingestion_health_cache.json["database_backups"]` | No |
| 5 | `daily-props-pull` | 08:15 daily | `python3 -m operational.live_odds_daily_pull --mode=props` | The Odds API (player props) | `research/live_sog_pricing` archive files; odds-API credit ledger | **Yes** (metered) |
| 6 | `moneyline-snapshot` | 08:00 / 13:00 / 17:00 / 20:00 daily (4×) | `python3 -m operational.live_odds_daily_pull --mode=moneyline` | The Odds API (moneyline, DraftKings only) | `operational/moneyline_snapshot_cache.json`; odds-API archive. **New this block (Part 22):** on a successful (`ran: True`) pull, ALSO triggers `operational.real_odds_bridge.sync_moneyline_odds_to_snapshots()` → `nhl.db.odds_snapshots`, then `operational.real_recommendation_orchestrator.run_real_moneyline_recommendations()` → `prospective_observations.db` + `paper_bankroll.db` (`REAL_MARKET_PAPER` track) | **Yes** (metered — one sport-level call, not per-event) |
| 7 | `midday-schedule-refresh` | 13:00 daily | `python3 -m operational.nhl_sync --mode=midday` | NHL public schedule API | `nhl.db` (schedule revisions); `ingestion_health_cache.json["nhl_midday_schedule_refresh"]` | No |
| 8 | `pregame-targeted-refresh` | every 30 min (`StartInterval=1800`) | `python3 -m operational.nhl_sync --mode=pregame` | NHL public roster/goalie API, windowed internally to games in the next few hours | `nhl.db` (roster/goalie status); `ingestion_health_cache.json["nhl_pregame_targeted_refresh"]` | No |
| 9 | `prop-sweep-first` | every 30 min (`StartInterval=1800`) | `python3 -m operational.live_odds_daily_pull --mode=sweep-first` | The Odds API, windowed internally to events 3–4.5h from puck drop | `targeted_prop_sweep_cache.json`; odds-API archive. **New (Live SOG + Saves block, Part 34):** on a successful (`ran: True`) sweep, ALSO triggers `operational.real_prop_orchestrator.run_real_sog_recommendations()` and `run_real_saves_recommendations()` → `prospective_observations.db` + `paper_bankroll.db` (currently always a no-op in production: neither contract is verified yet — see `docs/LIVE_SOG_SAVES_CERTIFICATION.md`) | **Yes** (metered, windowed — most firings are no-ops) |
| 10 | `prop-sweep-second` | every 15 min (`StartInterval=900`) | `python3 -m operational.live_odds_daily_pull --mode=sweep-second` | The Odds API, only events the first sweep already found a quote for, windowed to 45–75 min from puck drop | `targeted_prop_sweep_cache.json`; odds-API archive. **New (Part 34):** same real-prop-orchestrator trigger as row 9 | **Yes** (metered, windowed) |

## Overlap analysis

- **No two jobs write the same table concurrently by schedule design** except the real-purpose cascade in row 6 (moneyline-snapshot → bridge → orchestrator), which is a **single process, sequential, in-order call chain** — not a race between two independently-scheduled jobs.
- **07:00 → 07:15 → 07:30 → 07:45** (sync → settlement → postmortem → backup) is a real dependency chain. Before this block, each stage was purely clock-scheduled with no awareness of the prior stage's outcome (see Part 24 fix below).
- **`pregame-targeted-refresh` (every 30 min) and `midday-schedule-refresh` (13:00 daily)** both touch `nhl.db`'s schedule/roster tables. Both go through `ingest/nhl_api.py`'s own idempotent `_append_*_if_changed` helpers (append-only, revision-numbered), so a genuine overlap produces at most a redundant append-check, never a corrupt write — this predates this block and was not modified.
- **`moneyline-snapshot` (4×/day) and `prop-sweep-first`/`prop-sweep-second` (every 15–30 min)** all call the same third-party Odds API client, but request *different* markets/endpoints (moneyline vs. props) and write to different cache files — no shared-state race.
- **Locking/idempotency protection:** every write path in this project relies on either (a) a UNIQUE index + `INSERT OR IGNORE`/idempotency-key check-then-insert (odds_snapshots, prospective ledger, paper_bankroll), or (b) an explicit revision-numbered append-only table (`game_schedule_events`, `game_result_events`, etc.), never a raw overwrite. No new locking mechanism was introduced or found necessary in this block.

## Dangerous overlap found and fixed this block (Part 24)

**Before this block:** `settle_daily_observations` and `daily_postmortem` were purely `StartCalendarInterval`-scheduled with **no check of the upstream job's actual outcome**. A failed or still-running 07:00 sync would not stop 07:15 settlement from running against a stale/incomplete schedule; a failed settlement would not stop 07:30 postmortem from generating a report that looked complete.

**Fix:** `operational/ingestion_health.py::dependency_ready(component, max_age_hours)` — a single yes/no check (never a retry loop) — is now called at the top of `settle_daily_observations.main()` (checks `nhl_sync_full`) and `daily_postmortem.main()` (checks `settlement`). On a failed check, the job **DEFERS**: it records `status="DEFERRED"` (which never counts as a success for future dependency checks — see `_FAILURE_STATUSES`) and exits without doing its normal work. The next scheduled run of that same job re-checks and proceeds once the dependency recovers. See `tests/test_morning_workflow_dependency.py` for the full test matrix (11 tests).

## No new jobs added

Per Part 22's explicit instruction ("prefer integrating with existing odds/data workflow rather than creating dozens of new scheduled jobs"), the real recommendation pipeline's trigger reuses the **already-scheduled** `moneyline-snapshot` job (row 6) rather than adding an 11th job. This required editing only `operational/live_odds_daily_pull.py::_main()`'s `--mode=moneyline` branch.

## `prop-sweep-first` / `prop-sweep-second` / `daily-props-pull` redundancy audit (Live SOG + Saves block, Part 34)

Audited whether all three remain independently justified before wiring the new real SOG/Saves orchestrator trigger into rows 9-10. Conclusion: **all three are justified; none is redundant; none was removed or rescheduled.**
- `daily-props-pull` (row 5) is a **broad** multi-market pull covering every prop family this project tracks (Goals, Assists, Points, etc., not only SOG/Saves), once daily.
- `prop-sweep-first`/`prop-sweep-second` (rows 9-10) are **narrow**, SOG/Saves-**only** (`FIRST_SWEEP_MARKETS = "player_shots_on_goal,player_total_saves"`), windowed to the pregame hours the owner's own stated priority (SOG/Saves as the primary prop focus) calls for, and write to a separate cache from `daily-props-pull`.

These serve genuinely different purposes (broad daily coverage vs. narrow high-priority pregame refresh) and no consolidation was made.

---

## Re-audit — 2026-09-25 (Production Activation block, Part 10)

**Method:** `plutil -convert json` of every `~/Library/LaunchAgents/com.nhlengine.*.plist`, `launchctl print gui/$UID/<label>` (run counts), job logs under `operational/logs/`, `ingestion_health_cache.json`, and each target module's source. **Inventory is still 10 jobs** (no additions, none removed). All 10 plists lint OK (`plutil -lint`), all target modules exist, every `WorkingDirectory` points at the current repo (`.../Downloads/nhl_engine 2`) — **no obsolete paths, no duplicate jobs, no dead jobs**. Times are the Mac's local time (EDT = UTC−4).

| Job | Cadence | Last run / result | Next expected | Writes | Odds credits? | Publishes cloud snapshot? |
|---|---|---|---|---|---|---|
| `daily-nhl-sync` | 07:00 EDT | **NEVER fired by launchd** (`runs = 0`, "job state = uninitialized", no log file). `nhl_sync_full` last SUCCESS 07:44 EDT today came from a manual run | 09-26 07:00 (unless reloaded) | `nhl.db`, health cache | No | No |
| `daily-settlement` | 07:15 | 09-25 07:15 — SUCCESS (0 PENDING) | 09-26 07:15 | `prospective_observations.db` (settlement columns), health | No | **Yes** (after SUCCESS) |
| `daily-postmortem` | 07:30 | 09-25 07:30 — SUCCESS (`WAITING_FOR_SETTLED_DATA`) | 09-26 07:30 | `reports/daily/postmortem_*.md`, health | No | **Yes** |
| `database-backup` | 07:45 | 09-25 07:45 — SUCCESS | 09-26 07:45 | backup copies | No | No |
| `daily-props-pull` | 08:15 | 09-25 08:15 — ran, **31 credits** (09-24: skipped, "preseason start not yet known") | 09-26 08:15 | odds archive, board cache | **Yes (~30/day)** | **Yes** when it ran |
| `moneyline-snapshot` | 08:00, 13:00, 17:00, 20:00 | 09-25 13:00 EDT — ran, 1 credit, 20/33 events priced; bridge wrote 40 rows; orchestrator 0 recs (all DATA_UNAVAILABLE) | 09-25 17:00 EDT | moneyline cache, `odds_snapshots`, ledger/bankroll via orchestrator | **Yes (1/pull)** | **Yes** when it ran |
| `midday-schedule-refresh` | 13:00 | 09-25 13:00 — SUCCESS | 09-26 13:00 | `nhl.db` schedule | No | No |
| `pregame-targeted-refresh` | every 30 min | 09-25 13:09 — SUCCESS (stderr = `datetime.utcnow()` DeprecationWarnings only) | +30 min | `nhl.db` rosters/goalies | No | No (tested) |
| `prop-sweep-first` | every 30 min | 09-25 13:09 — ran, 0 credits | +30 min | sweep cache, archive | Yes, windowed (0 today) | **Only if it recorded a recommendation/paper bet** (fixed this block) |
| `prop-sweep-second` | every 15 min | 09-25 13:24 — ran, 0 credits | +15 min | sweep cache, archive | Yes, windowed (0 today) | Same as above |

### Findings
1. ~~`daily-nhl-sync` has never run under launchd~~ — **CORRECTED 2026-09-25 (Quota block): not a defect.** `runs = 0` was misread. Root cause: the Mac **booted at 07:08:50 EDT** on 2026-09-25 (`sysctl kern.boottime`); the 07:00 slot fell *before* the boot, launchd does not replay calendar slots missed while the machine was off, and its `runs` counter restarts at every boot (the other morning jobs show `runs = 1` only because their 07:15/07:30/07:45 slots came after the boot; the unified log shows launchd looking for the labels at 07:09:29 as the user session loaded them). Ruled out with evidence: plist is valid (`plutil -lint`), structurally identical to `daily-settlement`, same `gui/501` domain, correct Python path, correct working directory, writable log paths, `RunAtLoad=false` is intentional, no runtime-mode guard on `sync_daily.py` besides the STANDBY check. **Proof the scheduler can execute it:** `launchctl kickstart gui/$UID/com.nhlengine.daily-nhl-sync` → launchd recorded `runs = 1`, `last exit code = 0`, log written, `nhl_sync_full` = `PARTIAL_SUCCESS` (the documented roster-429 issue; schedule/boxscore data committed) — distinct from the earlier manual `python3 sync_daily.py` run. The recurring exposure is the Mac being off at 07:00 (see 3.); `python3 -m operational.scheduler_audit` now reports boot time and explains zero-run counts.
2. **Publish trigger fixed this block:** the two sweep jobs (≈ 100 firings/day) each triggered a cloud publication because the snapshot embedded the sweep's own `last_updated_utc`. That timestamp is now excluded and sweeps publish only when they actually record something (`live_odds_daily_pull.cloud_publish_warranted`; regression tests added).
3. **Reliability:** 8 of 41 logged moneyline runs failed on network errors and the 09-24 20:00 EDT pull is missing — the Mac must be awake and online at pull times.
4. **Runtime-mode guards:** launchd jobs run in the default LOCAL/`ACTIVE` mode by design; none is reachable from Community Cloud (no Cloud page imports a scheduler or API client — `python3 -m operational.cloud_preflight` proves it). `standby` deployment mode is respected by the publish hook.
5. **No unbounded jobs:** every job is a single bounded process; the publisher subprocess has a 240 s timeout.


## Update — 2026-09-25 (Quota + Moneyline Activation block)

* **New job (only one):** `moneyline-pregame`, every 2 min, paid **only** when a start-time cluster is due (~T-35), 0 credits otherwise. Template: `deploy/launchd/com.nhlengine.moneyline-pregame.plist`; installed with `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.nhlengine.moneyline-pregame.plist` (first firing recorded `runs = 1`, exit 0, one-line IDLE log). The existing `pregame-targeted-refresh` (30 min, NHL rosters) was *not* reused: its cadence is too coarse for a ≥ 5-minute capture window and it would conflate NHL-context and odds responsibilities.
* **`daily-props-pull` behavior changed, plist unchanged** (`--mode=props`): DISCOVERY contract watch (≤ 2 sampled events, only SOG/Saves keys, ≤ 6 credits/day; 0 while absent) instead of the ~30-credit/day broad pull. `prop-sweep-first/second` now query each event at most once per stage per day and share the discovery budget. See `docs/PROP_DISCOVERY_BUDGET.md`.
* **Inventory: 11 jobs**; `python3 -m operational.scheduler_audit` → `OK`, no duplicate commands, no missing targets, all working directories current. Paid jobs: `moneyline-snapshot` (4×/day, 1 credit), `moneyline-pregame` (~4/game day), `daily-props-pull` (discovery, 0 while absent), `prop-sweep-first/second` (windowed, once per event). No legacy broad prop job remains active; none of the two prop paths duplicates the other (`props` = sampled discovery, sweeps = pregame-window quotes).
* Publish triggers: moneyline / `moneyline-pregame` when a pull ran; `props` only if it spent credits or saw a candidate; sweeps only when they recorded a recommendation.
