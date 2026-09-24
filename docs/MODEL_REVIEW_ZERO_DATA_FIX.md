# Model Review Zero-Data Fix

**Date:** 2026-09-24. **Trigger:** exercising `operational.settle_daily_observations` for real (Production Readiness Audit) created `operational/prospective_observations.db` for the first time ever. The Model Learning dashboard page immediately started showing **"ENGINE STATUS: WATCH"** with zero real settled predictions — an invalid system state.

## Root cause

Two independent bugs compounded:

1. **`dashboard/pages/32_Model_Learning.py`** gated its "waiting" state on `pl.DB_PATH.exists()` — file *existence*, never row count. The moment the file existed (even empty), this branch was skipped entirely.
2. **`operational/daily_model_review.py::run_daily_review()`** had no sample-size check of its own. `engine_status` was computed as `combine_status([run_order["status"], contract_status["status"]])` — neither input reflects how many real settled predictions exist. `check_contract_status()` returns `WATCH` whenever any contract is verified (`VERIFIED_CONTRACTS` has held `{("draftkings", "MONEYLINE")}` since 2026-09-01) — a real, permanent, correct signal that drift-monitoring for that contract isn't built yet, but one that has nothing to do with whether there's any data to review *today*. With zero rows, this unrelated WATCH signal became the entire displayed status.

## Fix

- `operational/engine_status_evaluator.py`: added `NO_DATA` and `INSUFFICIENT_SAMPLE` constants, deliberately **not** part of `combine_status()`'s severity ordering — they mean "not enough data to say anything," not a severity level to blend with WATCH/INVESTIGATE/HALT.
- `operational/daily_model_review.py::run_daily_review()`: after computing `engine_status` normally, an explicit override — `sample_size == 0` → `NO_DATA`; `0 < sample_size < MIN_SAMPLE_FOR_REVIEW` (5, matching this project's own `challenger_registry.MIN_REPEATED_OCCURRENCES` precedent) → `INSUFFICIENT_SAMPLE`; both set `incomplete=True` and a human-readable `reason`. Every other field (`recommendation`, `promotion_candidates`, `rejected_research_entries_on_file`, `paper_performance`) is still computed normally — these reflect challenger-registry/rejected-research/paper-ledger state, not ledger row count, and remain real and meaningful even with zero fresh predictions.
- `dashboard/pages/32_Model_Learning.py`: removed the file-existence special case entirely. Now always calls `pl.init_db()` (idempotent) and `run_daily_review()`, and relies on the (pre-existing) `if result.get("incomplete"): st.stop()` branch to show the honest state — this eliminates the "file exists but is empty" class of bug structurally, not just for this one case.
- `dashboard/components.py`: added `NO_DATA`/`INSUFFICIENT_SAMPLE` entries to `STATUS_BANNER_STYLES` (neutral gray, matching the existing `INSUFFICIENT_DATA` style).

## Tests

`tests/test_daily_model_review.py::Test12ZeroAndLowSampleHandling` (8 new tests): 0 observations → `NO_DATA`; 1 observation and `MIN_SAMPLE_FOR_REVIEW - 1` → `INSUFFICIENT_SAMPLE`; exactly `MIN_SAMPLE_FOR_REVIEW` → proceeds normally; a real WATCH case above the minimum still surfaces correctly; promotion-candidate logic still works correctly above the minimum; `sample_size` always present; `recommendation`/`promotion_candidates`/`rejected_research_entries_on_file` remain present and real even at zero samples. `test_proceeds_when_both_complete` (pre-existing, encoded the bug) updated to assert the corrected behavior. `tests/test_demo_pages_apptest.py::test_model_learning_waiting_state` updated to assert the real `NO_DATA` state instead of a hardcoded banner string.

**Full suite: 2,624 / 2,624 passing.**
