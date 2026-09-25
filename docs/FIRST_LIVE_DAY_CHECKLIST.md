# First Live Day Checklist

A simple timeline for the owner's first real game day with the engine and the Community Cloud app. Times are Eastern (the Mac's launchd clock). Everything marked **auto** runs by itself; everything marked **you** is a check or action. One command answers most questions:

```bash
python3 opening_day_readiness.py        # per-component states (READY / WAITING_FOR_LIVE_MARKET / PARTIAL / ...)
```

Read the `OVERALL` line and the table under it. `WAITING_FOR_LIVE_MARKET`, `WAIT_ONLY` and `NO_REAL_SAMPLE_YET` are expected on day one. `PARTIAL`, `STALE` or `FAILED` need attention.

## Night before

| | Check |
|---|---|
| you | Mac plugged in, **prevent sleep** while the lid is closed or use `caffeinate -s` (missed pulls were the top reliability problem: 8 of 41 moneyline runs failed offline). |
| you | `python3 -m operational.scheduler_audit` — 11 jobs, status `OK`. (`runs` restarts at every boot; a 0 after a late boot is normal — see `docs/SCHEDULER_INVENTORY.md`.) |
| you | `python3 -m operational.cloud_preflight` → `PASS_WITH_OWNER_ACTIONS`; do the owner actions in `docs/STREAMLIT_COMMUNITY_CLOUD_RUNBOOK.md`. |
| you | Open the Cloud app as an allowed viewer: banner shows **SNAPSHOT CURRENT**, Diagnostics (ADMIN) shows source `REMOTE`, fetch `OK`, schema 2. |

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

## Pregame (2–0.5 h before the first puck drop)

The engine prices each game at **puck drop − 30 min** and only accepts a DraftKings quote captured in the 10 minutes before that. The `moneyline-pregame` job (every 2 min, launchd) makes **one league-wide pull ≈ 35 min before each start-time cluster** for games the provider lists (first: 2026-09-29). Games the provider does not list are skipped at 0 credits. Keep the Mac awake and online.

| Check | Expected |
|---|---|
| Diagnostics → **Next decision cluster** / `python3 opening_day_readiness.py` → `NEXT_T35_CLUSTER` | Shows the next cluster, target pull time, decision anchor, scheduler armed, quota sufficient |
| `operational/runtime/moneyline_pregame_state.json` | After the pull: `status: DONE`, `in_decision_window: true`, `credits: 1` |
| Today → Live Model Edges | Rows show `WAIT` (Elo staleness / fail-closed gates) and MARKET FRESHNESS; prices > 3 h old (or > 90 min inside 4 h of puck drop) read **STALE — not live** |
| Any recommendation cards | Show created-at, price captured, game start, freshness. A stale leg makes a Game Edge Parlay "not a current opportunity" |
| Game Edge Parlay | `NO_QUALIFYING_GAME_EDGE_PARLAY` is the correct answer until a prop contract is verified |
| Top Conviction | Only the simulated demo slate has cards (each labeled SIMULATED — DEMO ONLY); nothing is forced |

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
