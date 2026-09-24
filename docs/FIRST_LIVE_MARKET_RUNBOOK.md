# First Live Market Runbook — 2026-09-29

DraftKings' first posted market is 2026-09-29. This is the exact sequence to verify, stage by stage, that the whole real pipeline works the first time it has a real market to work against. Every command below is real and already exists — nothing here needs to be written on the day.

**Updated 2026-09-24 (Real Recommendation Pipeline block):** step 6's gap — "no live, non-demo recommendation-generation code exists yet" — is now closed for MONEYLINE. `operational/real_odds_bridge.py` + `operational/real_recommendation_orchestrator.py` connect real DraftKings moneyline data through the existing model (`run_slate.build_prediction_for_game`) and existing decision engine (`pricing/engine.py::evaluate_moneyline_for_game`) into the real prospective ledger and paper bankroll. This is wired to run automatically after every real `moneyline-snapshot` scheduler firing (08:00/13:00/17:00/20:00) — see `docs/SCHEDULER_INVENTORY.md`. Player props remain demo-only until their own provider contract is verified (Part 6 of that block).

## Before the day

Run once, the evening before:
```bash
python3 opening_day_readiness.py
```
Confirm `VERDICT: READY` or `READY_WITH_WARNINGS` (never `NOT_READY`) before relying on anything below. Resolve any `hard_failures` first.

## 1. Games discovered

```bash
python3 -c "
import db
conn = db.get_conn()
rows = conn.execute(\"SELECT game_id, home_team, away_team, scheduled_start_utc FROM games WHERE game_date='2026-09-29'\").fetchall()
for r in rows: print(dict(r))
"
```
Or: **Data Status page** → "NHL Schedule" row should show `CURRENT`, and the "Scheduled ingestion job health" section should show `nhl_sync_full` with a recent `last_success_utc`.

## 2. Odds discovered

```bash
python3 -m operational.live_odds_daily_pull --mode=moneyline --label=manual_check
```
Check the printed `games_seen`/`parsed_count`. Or: **Today page** → the new odds-collection metrics row (Odds last updated, Credits remaining, Tracked events).

As of this block, a successful run (`"ran": true` in the printed JSON) also prints `real_odds_bridge` and `real_recommendation_orchestrator` sub-results inline — `real_odds_bridge.rows_written` should be `> 0` the first time a real event matches a real `nhl.db` game (it will legitimately be `0` on a rerun against the same snapshot — that's idempotency working, not a bug; see `rows_skipped_unmatched`/`unmatched` if a real event never matches).

## 3. Props discovered

```bash
python3 -m operational.live_odds_daily_pull --mode=props
```
`quotes_captured > 0` for the first time ever is the real milestone — check `operational/new_contract_candidates.jsonl` for any newly-flagged market key too (a genuinely new DraftKings prop type would show up there, `auto_verified: false`, needing manual verification before ever being trusted — never auto-trust a new contract).

## 4. Feature generation succeeds

No single command — verify indirectly via step 6 (a recommendation actually being generated implies the feature layer underneath it worked). If step 6 produces nothing at all despite real odds existing, check `operational/logs/*.err.log` for the relevant job first.

## 5. Models load

```bash
python3 -c "from research import model_registry as mr; print(len(mr.MODEL_REGISTRY), 'entries loaded')"
```
Also covered automatically by `opening_day_readiness.py`'s `models` section.

## 6. Recommendations generate

For MONEYLINE, this now runs automatically after step 2 (see the update note at the top of this document). To run it manually and inspect the result directly:
```bash
python3 -m operational.real_recommendation_orchestrator
```
Read the printed JSON: `games_evaluated`, `recommendations_recorded`, `data_unavailable`, `paper_bets_created`. **`paper_bets_created: 0` is a legitimately healthy result** (Part 13 — a real day can have zero qualifying bets); `data_unavailable` should only be non-zero for games missing a fresh DraftKings quote or unconfirmed goalies, never fabricated. Player props are NOT covered by this step yet — `dashboard/eligible_bets.py::all_opportunities()` (what `21_Today.py` displays for props) remains demo-only until a prop market's own DraftKings contract is verified.

## 7. Immutable snapshots persist

```bash
python3 -c "
from operational import prospective_ledger as pl
conn = pl.init_db()
print(conn.execute(\"SELECT COUNT(*) c FROM predictions WHERE game_date='2026-09-29' AND market_id='MONEYLINE'\").fetchone()['c'], 'real MONEYLINE predictions recorded for 2026-09-29')
"
```
Every row here carries a real `prospective_status` (BET/WAIT/PASS — never DATA_UNAVAILABLE, which is never recorded at all per Part 4's fail-closed rule) and a real `odds_american`/`sportsbook`. Or: **Today / Game Detail pages**, via `dashboard/real_recommendations_view.py::real_moneyline_recommendations()` — every row there is labeled `LIVE — DRAFTKINGS` (a real paper bet exists) or `REAL MARKET — <status>` (WAIT/PASS), never blended with the `SIMULATED — DEMO ONLY` demo rows from `dashboard/eligible_bets.py`.

## 8. Odds refresh updates appropriately

Watch `operational/moneyline_snapshot_cache.json` and `operational/live_multimarket_board_cache.json` change across the day's 4 moneyline runs (08:00/13:00/17:00/20:00) and the 08:15 props pull. **Data Status page** shows the freshness age directly. Also watch `nhl.db`'s `odds_snapshots` table grow a new row pair per real intraday price change (never per poll — `real_odds_bridge`'s own UNIQUE index on `captured_at_utc` means an unchanged price at a new poll time still gets its own honest timestamped row, while an identical rerun against the same cache file is a no-op):
```bash
python3 -c "
import db
conn = db.get_conn()
print(conn.execute(\"SELECT COUNT(*) c FROM odds_snapshots WHERE data_provider='the-odds-api'\").fetchone()['c'], 'real archived odds rows total')
"
```

## 9. Pregame data freshness checks run

```bash
tail -f "operational/logs/pregame_targeted_refresh.log"
```
Should show `teams_targeted > 0` starting ~3-4.5h before the first 2026-09-29 puck drop, `0` otherwise (by design — see `docs/INTRADAY_REFRESH_SCHEDULE.md`).

## 10. Game results ingest

The morning after (2026-09-30, 07:00 run):
```bash
python3 -m operational.nhl_sync --mode=full
```
Check `games_finalized` in the output, or the Data Status page's "NHL Results" row.

## 11. Recommendations settle

```bash
python3 -m operational.settle_daily_observations
```
`total_candidates` should equal however many real predictions were recorded the day before (step 7); `still_pending_game_not_final` should be 0 once all 2026-09-29 games are FINAL.

**New this block (Part 24):** this command now DEFERS (prints `DEFERRED: upstream nhl_sync_full not ready...` and records `status="DEFERRED"` rather than running) if the 07:00 `nhl_sync_full` job hasn't recorded a real success within the last 30 hours — check `operational/ingestion_health_cache.json` if you see this instead of the usual settlement summary.

**New this block (Parts 14-16):** for a settled MONEYLINE prediction, check that `closing_odds`/`clv` were actually populated from a real archived DraftKings snapshot:
```bash
python3 -c "
from operational import prospective_ledger as pl
conn = pl.init_db()
rows = conn.execute(\"SELECT prediction_id, side, odds_american, closing_odds, clv, result_status FROM predictions WHERE game_date='2026-09-29' AND market_id='MONEYLINE' AND result_status != 'PENDING'\").fetchall()
for r in rows: print(dict(r))
"
```
`closing_odds`/`clv` being `None` for a specific row is legitimate (Part 16) only if no real DraftKings snapshot for that side was ever archived strictly before puck drop — check `odds_snapshots` for that `game_id`/`selection` if that's unexpected.

## 12. Next-morning post-mortem runs

```bash
python3 -m operational.daily_postmortem
```
Or: **Morning Review page**. With real settled data finally existing, `engine_status` should move past `NO_DATA`/`INSUFFICIENT_SAMPLE` once `MIN_SAMPLE_FOR_REVIEW` (5) real settled predictions accumulate — likely not on the very first day, honestly expected per `docs/MODEL_REVIEW_ZERO_DATA_FIX.md`.

**New this block (Part 24):** this command now DEFERS the same way step 11 can — if `settlement`'s last recorded run isn't a confirmed success within the last 30 hours, this prints `DEFERRED: upstream settlement not ready...` and records `status="DEFERRED"` instead of writing a report, rather than ever writing a report that looks complete when settlement hasn't actually run.

## If anything looks wrong

- **Data Status page** → "Scheduled ingestion job health" for per-component last-attempt/last-success/status/age.
- `operational/logs/*.log` and `*.err.log` for the specific job.
- `python3 opening_day_readiness.py` for a full, structured, factual snapshot.
- `operational/new_contract_candidates.jsonl` for any newly-observed market key DraftKings posts that this engine has never seen before — flagged, never auto-trusted.
