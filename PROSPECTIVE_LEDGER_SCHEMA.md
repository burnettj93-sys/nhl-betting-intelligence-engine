# Prospective Ledger Schema

Real, tested SQLite schema at `operational/prospective_schema.sql`, driven by `operational/prospective_ledger.py`. Separate database (`operational/prospective_observations.db`) from `nhl.db` — new operational infrastructure, never mixed with the production schema.

## Tables

**`predictions`** — one row per recorded observation. Columns split into two groups:

- **Immutable after insertion** (everything except the eight settlement columns below) — enforced by the `predictions_immutability` trigger, which `RAISE(ABORT, ...)` on any `UPDATE` touching a prediction-time column. This is enforced at the database level, not just in the Python API, so even a careless future SQL edit can't silently bypass it.
- **Settlement fields** (mutable, via `settle_prediction()` only): `result_status`, `actual_outcome`, `settled_at_utc`, `profit_loss`, `closing_odds`, `closing_captured_at_utc`, `clv`, `notes`.

Full column list matches the Preseason Operationalization sprint's Section 5 spec exactly — see the `.sql` file for the authoritative list (prediction identity, market identity, all four probability stages, confidence/context/policy fields, version-snapshot fields, market/odds fields, and the settlement fields).

**`audit_log`** — append-only `{timestamp_utc, prediction_id, action}` rows for every `INSERT`/`SETTLE`/`VOID`.

**`schema_version`** — single-row version marker (`SCHEMA_VERSION = 1` today), so future migrations have an explicit version to check against rather than ad hoc table creation.

## Guarantees, and how each is enforced

| Guarantee | Enforcement |
|---|---|
| Append-only prediction fields | DB trigger (`predictions_immutability`) + `settle_prediction()` only ever writes settlement columns |
| No duplicate `prediction_id` | `PRIMARY KEY` constraint → `DuplicatePredictionError` on violation |
| No duplicate real-world observation from a Streamlit rerun | `idempotency_key` (`UNIQUE`, computed from `game_id/player_id/market_id/threshold/side/model_version/prediction_cutoff_utc`) — `insert_prediction()` returns the existing row's id instead of inserting again |
| Pre-game guard | `created_at_utc < event_start_utc` enforced in `insert_prediction()`, except `HISTORICAL_RESEARCH` |
| Odds pre-game guard | `odds_captured_at_utc < event_start_utc` enforced the same way |
| REAL_BET requires explicit stake/odds/book/placed_at | `insert_prediction()` raises `InvalidPredictionError` if any is missing |
| Real P&L never includes MODEL_OBSERVATION/SHADOW_POLICY_OBSERVATION | `summary_metrics()` computes each record type in its own dict section; `REAL_BET` is the only section with a `total_profit_loss` key |

## API surface (`operational/prospective_ledger.py`)

`init_db()`, `insert_prediction()`, `record_model_observation()`, `record_shadow_observation()`, `record_real_bet()`, `record_historical_research()`, `get_observation()`, `query_observations()`, `settle_prediction()`, `summary_metrics()`, `export_observations_csv()`, `backup_db()`, `context_cohort()`, `raw_vs_adjusted_summary()`.

## Verified this sprint (real, not simulated)

- Immutability trigger genuinely blocks a direct `UPDATE` of `raw_probability`.
- Ten repeated `record_model_observation()` calls with identical real-world inputs produce exactly one row.
- Post-game-start prediction insertion is rejected; `HISTORICAL_RESEARCH` is correctly exempted.
- `settle_prediction()` changes only settlement fields — `raw_probability` provably unchanged after settlement in the same test.

See `tests/test_operational_infrastructure.py` for the full test suite (43 test classes covering schema, insertion, immutability, idempotency, guards, settlement, record-type separation, and P&L isolation).
