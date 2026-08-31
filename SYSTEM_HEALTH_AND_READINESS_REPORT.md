# System Health and Readiness Report

## Architecture

`operational/system_health.py::build_system_health()` returns one entry per component, each real (never demo constants):

| Component | Source |
|---|---|
| NHL_API, SCHEDULE, ROSTERS | `operational/data_readiness_cache.json` (written by `sync_daily.py` via the pre-existing `operational/readiness.py`) |
| MONEYPUCK | Same cache, aggregated across team/skater/goalie datasets |
| ODDS_API, DRAFTKINGS_MARKETS | Same cache's `odds` block (DraftKings-specific signal doesn't exist separately yet — disclosed, not fabricated) |
| MODEL_REGISTRY, MARKET_REGISTRY, JOINT_REGISTRY, CONTEXT_OVERLAY_REGISTRY | Live import + `len()` of each real registry |
| DATABASE | Live `sqlite3.connect(nhl.db)` + `SELECT 1` |
| PROSPECTIVE_LEDGER | Live query against `operational/prospective_observations.db` if it exists, else `NOT_REQUIRED` |
| LAST_SYNC | Same readiness cache's `generated_at_utc` |

This module deliberately **reuses** `operational/readiness.py`'s existing per-source status model (`CURRENT`/`STALE`/`UNAVAILABLE`/`PROJECTED`/etc.) rather than building a second, competing readiness system — it translates that already-real data into this sprint's requested canonical taxonomy (`OK`/`STALE`/`WAITING`/`ERROR`/`NOT_REQUIRED`/`UNKNOWN`).

## Freshness configuration

Centralized in `operational/system_health.py::FRESHNESS_TTL_HOURS` (schedule 24h, roster 24h, odds 24h, MoneyPuck 36h, starter projection 12h, prediction 6h) — a single place, not scattered per-page. Note: this sprint centralizes the *values*; wiring every page to read from this dict specifically (rather than the underlying `operational/readiness.py` thresholds, which already exist and are consistent) is a small follow-up, not yet done.

## Live readiness service

`operational/live_readiness.py::live_readiness(market_id, ...)` — fail-closed by construction. Verified real behavior:

| Input | Output |
|---|---|
| `PLAYER_SOG` (normal conditions) | `READY` (or `WAIT` if odds happen to be stale in this environment) |
| `GOALS` | `WAIT` / `MARKET_UNSUPPORTED` — no live-tested DraftKings contract exists for Goals yet |
| Unknown market string | `MODEL_NOT_OPERATIONAL` / `MARKET_UNSUPPORTED` |
| Rejected/not-validated model (`TEAM_GOALS_PERIOD`) | `MODEL_NOT_OPERATIONAL` |
| `player_mapped=False` | `DATA_UNAVAILABLE` / `PLAYER_UNMAPPED` |
| `lineup_confirmed=False` | `WAIT` / `LINEUP_PENDING` |

All verified directly in `tests/test_operational_infrastructure.py` (Tests 24-27), not merely asserted.

## Fail-closed behavior

`live_readiness()` never returns `READY` unless: the model is validated, the market has a live-tested payload contract (SOG only today), odds are current (not stale/missing/erroring), and no explicit unconfirmed-lineup/unmapped-player flag was passed. Every ambiguous or missing input defaults to the more restrictive outcome, never to `READY`.

## Known limitations (disclosed)

- `ROSTERS` health reuses the NHL results-sync timestamp — no dedicated roster-freshness signal exists separately from schedule/results sync today.
- `DRAFTKINGS_MARKETS` health reuses the Odds API cache — no DraftKings-specific availability signal exists separately.
- `live_readiness()`'s `_MARKET_FAMILY_TO_MODEL_ID` mapping covers the families this sprint touched (SOG, Goals, Points, Assists, Blocks, Team SOG, Goalie Saves) — extending it to all 142 canonical markets is real, scoped follow-up work.
