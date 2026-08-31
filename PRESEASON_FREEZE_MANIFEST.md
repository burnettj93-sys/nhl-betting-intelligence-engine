# Preseason Freeze Manifest

**Freeze date: 2026-08-30.** This document is the single reference for what state the engine was in when active development stopped, what's in git vs. what isn't, and exactly how to reproduce or restore anything that isn't. It is written to survive this machine dying — assume the reader has nothing but this repo's git history and this file.

## Freeze facts

- **Full test count: 2,105 / 2,105 passing** (see `PRESEASON_ENGINE_FREEZE_REPORT.md` for the exact command and timing).
- **Authoritative model statuses**: `research/model_registry.py` (16 entries) is the single source of truth. Summary: `NHL_WIN_MODEL` VALIDATED/PRODUCTION_READY; `PLAYER_SOG` VALIDATED/SHADOW_VALIDATED; `GOALS` VALIDATED (1+ only)/SHADOW_VALIDATED; `ASSISTS` VALIDATED (1+/2+ only)/RESEARCH; `POINTS` EMPIRICAL_BASELINE_REMAINS_CHAMPION/SHADOW_VALIDATED; `BLOCKED_SHOTS` VALIDATED (1+/2+/3+)/RESEARCH; `TEAM_SOG` VALIDATED (20-35+)/RESEARCH; `GOALIE_SAVES` PARTIAL (20+/25+ clean)/RESEARCH; `TEAM_GOALS_PERIOD` ATTEMPTED_NOT_VALIDATED/NOT_OPERATIONAL; joint/context-overlay/PP-role-overlay entries as documented in `NHL_ENGINE_STATE_OF_THE_UNION_2026_08_30.md` Part 8.
- **Authoritative PLAYER_SOG thresholds**: validated **2+, 3+, 4+, 5+**. NOT validated: 1+ (trivial/near-universal, never separately tested), 6+/7+/8+ (insufficient tail data). Corrected this closure cycle; enforced by `tests/test_registry_cross_consistency.py` and `research/live_sog_pricing/pricing.py::MODEL_VALIDATED_THRESHOLDS`.
- **Verified DraftKings contracts: 0.** `research/generic_prop_pricing/provider_adapter.py::VERIFIED_CONTRACTS` is empty. No market's real payload has ever been observed.
- **Scheduler: NOT INSTALLED.** `operational/com.nhlengine.odds-daily-pull.plist` exists in the repo only; never copied into `~/Library/LaunchAgents/` or loaded via `launchctl`. Activation deliberately deferred to ~September 17, per standing instruction.
- **Prospective ledger schema: v3.** `operational/prospective_schema.sql` / `operational/prospective_ledger.py::SCHEMA_VERSION = 3`. Record types: `MODEL_OBSERVATION`, `SHADOW_POLICY_OBSERVATION`, `REAL_BET`, `HISTORICAL_RESEARCH`. Immutable (DB trigger), idempotent (SHA-256 key), checkpoint-ordered (`PRIMARY_DAILY` required before `PRE_GAME_UPDATE`/`MARKET_REFRESH`, enforced in `operational/prospective_recording.py`, and — as of this freeze — enforced for the SOG shadow path too).
- **Settlement: built, real, idempotent, never exercised on a real game.** `operational/outcome_resolver.py` (SOG/Goals/Assists/Points/Saves/Moneyline; Team SOG and Blocks fail closed) + `operational/settle_daily_observations.py::run_settlement_batch()`.
- **Generic pricing: built.** `research/generic_prop_pricing/` — `NormalizedPropMarket`, `evaluate_prop()` (proven identical to SOG's own untouched pricing for shared inputs), `provider_adapter.py` (contract boundary, currently empty).
- **PP-role SOG overlay: SHADOW_VALIDATED** at thresholds 1+/2+/3+ only (`operational/sog_shadow_overlay.py`, `research/special_teams_role_overlay_sog_results.json`). Never affects a real probability or decision.

## Reproducing this state from a clean clone

### 1. Install and verify

```bash
pip install -r requirements.txt
python3 -m unittest discover tests -v
```

Expect 2,105/2,105 passing (or the number in `PRESEASON_ENGINE_FREEZE_REPORT.md` Part D if this file is ever out of sync with that report — the report is authoritative for the exact count at freeze time).

### 2. Launch the dashboard

```bash
streamlit run dashboard/app.py
```

### 3. Run a manual odds pull (real network call, free-tier `/events` cost, costs real Odds API credits only for markets actually posted — see `operational/live_odds_daily_pull.py`'s own credit-budgeting logic)

```bash
python3 -m operational.live_odds_daily_pull
```

### 4. Run settlement (idempotent — safe to re-run)

```bash
python3 -m operational.settle_daily_observations
```

### 5. Inspect system health

```bash
python3 -c "from operational import system_health as sh; import json; print(json.dumps(sh.build_system_health(), indent=2))"
```

## Data artifacts NOT in git, and how to restore them

| Artifact | Size | Status | Regeneration / restoration |
|---|---|---|---|
| `research/real_nhl_pbp/research_pbp.db` | 584M | Gitignored, REGENERABLE | Rebuild from `research/real_nhl_pbp/raw/**/*.json` (also gitignored, itself fetched from the official NHL API — see `research/real_nhl_pbp/`'s own ingestion scripts) |
| `research/moneypuck_ingestion/research_moneypuck.db` | 33M | Gitignored, REGENERABLE | `research/moneypuck_ingestion/ingest_moneypuck_team.py` from the raw MoneyPuck CSV/ZIP (also gitignored; MANUAL_DOWNLOAD_REQUIRED if MoneyPuck's own historical archive URL ever changes — see `MONEYPUCK_DATA_CONTRACT_REVIEW.md`) |
| `operational/special_teams_history.db` | 40M, 188,863 rows | **Newly gitignored this freeze** (was an oversight), REGENERABLE | `python3 -m operational.backfill_special_teams_history` from the already-ignored `research/player_*/player_game_*.jsonl` corpora |
| `research/special_teams_role_transitions_table.jsonl` | 98M | **Newly gitignored this freeze** (was an oversight), REGENERABLE | `python3 -m research.run_special_teams_role_transitions` — **run before** any of `run_special_teams_role_overlay_{sog,scoring,blocks}.py`, `run_special_teams_time_to_adapt.py`, `run_special_teams_role_residuals.py`, which read it as input |
| `research/team_game_special_teams_table.jsonl` | 3.7M | **Newly gitignored this freeze** (was an oversight), REGENERABLE | `python3 -m research.build_team_game_special_teams_table` — run before the special-teams role scripts above |
| `research/goalie_saves/{goalie_game_saves,team_game_sog}.jsonl` | 6.9M + 6.2M | **Newly gitignored this freeze** (pre-existing inconsistency corrected), REGENERABLE | `research/goalie_saves/build_goalie_saves_corpus.py` |
| `research/team_sog/team_game_sog.jsonl` | 5.6M | **Newly gitignored this freeze**, REGENERABLE | `research/team_sog/build_team_sog_corpus.py` |
| `research/team_goals_period/team_game_period_goals.jsonl` | 4.5M | **Newly gitignored this freeze**, REGENERABLE | `research/team_goals_period/build_team_goals_period_corpus.py` |
| `research/goalie_intelligence/{actual_starters,goalie_appearances}.jsonl` | 3.5M + 3.4M | **Newly gitignored this freeze**, REGENERABLE | `research/goalie_intelligence/build_starter_corpus.py` |
| `research/player_sog/raw/{2022,2023,2024,2025}.csv` | ~169M each | Gitignored, MANUAL_DOWNLOAD_REQUIRED | Real, one-time MoneyPuck skater season archives. No automated re-fetch script exists for this specific historical snapshot — see `research/player_sog/raw/provenance.json` for source URLs and checksums. This is the ROOT of the SOG/Goals/Assists/Points/Blocks derived corpora; if lost, those 5 markets' entire historical training data must be manually re-sourced. |
| `data/raw/moneypuck/**/*.csv,*.zip` | 1.9M total, 485 files | Gitignored, REGENERABLE (EXTERNAL_API_REQUIRED) | `operational/moneypuck_daily.py`'s own daily-sync download path. **Observation, not a fix**: this directory has accumulated ~485 small zip snapshots since 2026-08-23, most only a few KB — consistent with repeated test runs writing into the real download-staging path rather than a temp directory, the same class of bug fixed for the Odds API archive last sprint. Flagged for a future narrow fix; out of scope for this freeze task (Part 16 forbids new development work). |
| `data/raw/the_odds_api/live/*.json` | ~130K, 37 files | **Tracked in git** (small, real, intentional — same precedent as every other raw-provenance file in this project) | N/A — already preserved |
| `nhl.db` | 13M | **Tracked in git** (pre-existing, from the original snapshot commit) | Synthetic/demo data (`ingest/demo_data.py`'s generator); not a secret, not operational data, safe as committed |
| `operational/prospective_observations.db` | Does not exist yet | Gitignored, OPERATIONAL_BACKUP_REQUIRED once created | **No backup exists because the file does not exist** — no real prospective observation has ever been recorded to disk. Once the 2026-27 season starts and real observations accumulate, back it up deliberately via `operational/prospective_ledger.py::backup_db()` — this is real operational data, never reproducible from source. |
| `.env` | 50 bytes | Gitignored, SECRET | Contains The Odds API key. Never committed, never inspected by any automated process in this project beyond reading the key value at runtime. If lost, re-obtain the key from the Odds API account dashboard. |

## What this freeze does NOT include

No real 2026-27 game has been played. No real DraftKings payload has ever been observed. No real prospective observation, settlement, or CLV value has ever been recorded from a real game. All of the above are the explicit, expected triggers for resuming development — see `PRESEASON_ENGINE_FREEZE_REPORT.md`'s development-resume conditions.
