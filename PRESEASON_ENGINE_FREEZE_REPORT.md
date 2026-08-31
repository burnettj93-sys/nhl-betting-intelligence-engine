# Preseason Engine Freeze Report

## A. Executive Summary

Two things happened: the one documented checkpoint-ordering gap from the prior closure report was closed, and the repository was audited, classified, and checkpointed into a single, clean, recoverable local git commit. No model, overlay, or decision policy changed. No Odds API credits were spent. The scheduler remains uninstalled. The engine is now frozen and should not be touched again until one of the four resume conditions at the end of this report actually occurs.

## B. Shadow Checkpoint Inconsistency Fix

`operational/record_sog_shadow_observation.py` previously called `operational.prospective_ledger.record_model_observation()` directly, bypassing the checkpoint-ordering guard (`PRIMARY_DAILY` required before `PRE_GAME_UPDATE`/`MARKET_REFRESH`) the prior closure sprint added to `operational.prospective_recording.record_observation()`. It now routes through that canonical entry point. Nothing about what gets recorded changed — every field (raw SOG probability, shadow-adjusted probability, conservative probabilities, role state, role certainty, transition state, games-since-transition, model version, overlay version) is identical; the only behavioral change is that a `PRE_GAME_UPDATE`/`MARKET_REFRESH` shadow observation for a logical bet with no prior `PRIMARY_DAILY` now correctly raises `CheckpointOrderingError` instead of silently succeeding.

## C. Tests Added

`tests/test_sog_shadow_checkpoint_ordering.py` — 12 tests (10 required + 2 sub-cases), covering: PRIMARY_DAILY records correctly; PRE_GAME_UPDATE without PRIMARY_DAILY is rejected; PRE_GAME_UPDATE after PRIMARY_DAILY succeeds; MARKET_REFRESH follows the same ordering (both the rejection and success cases); PRIMARY_DAILY's own row is provably unchanged after a later checkpoint is recorded; shadow probability is confirmed distinct from production probability; the module never calls `record_real_bet` and every recorded row is `MODEL_OBSERVATION`; no stake/placed_odds/profit_loss is ever populated; the full PP-role feature snapshot (role state, certainty, transition state, games-since-transition, overlay version) survives the canonical path; recording the identical checkpoint twice is idempotent (returns the same `prediction_id`, no duplicate row).

## D. Full Test Result

**2,105 / 2,105 passing**, 0 failures (`python3 -m unittest discover -s tests -p "test_*.py"`, ~286s). Starting baseline for this task was 2,093; this task adds exactly 12 new tests. Re-verified once more immediately before the git commit in Part K.

## E. Repository Classification

Inspected via `git status`, `git diff`, `git ls-files`, and `.gitignore` before any mutation. 196 files were already tracked from the repo's single prior snapshot commit; ~465 more (real source code and small research artifacts written across every sprint this session, never committed) were untracked. Classification:

| Category | Examples | Disposition |
|---|---|---|
| **SHOULD_COMMIT** | `dashboard/`, `research/*.py`, `operational/*.py`, `pricing/`, `tests/*.py`, `dashboard_prototype/`, `sync_daily.py`, `.claude/launch.json`, `.streamlit/config.toml`, every root `*_REPORT.md`, small result JSONs (`research/*_results.json`, all well under 400KB, real validation-run output — part of the reproducible research record, not raw data), `ENGINE_MARKET_MATRIX.csv`, `ENGINE_STATUS_SNAPSHOT.json` | Committed |
| **SHOULD_IGNORE** (newly found this task) | `operational/special_teams_history.db` (40M), `research/special_teams_role_transitions_table.jsonl` (98M), `research/team_game_special_teams_table.jsonl` (3.7M), 5 more per-game/per-player corpora (3-7M each, structurally identical to already-ignored siblings) | Added to `.gitignore`, not committed |
| **REPRODUCIBLE_RAW_DATA** (already correctly ignored, verified) | `research/player_sog/raw/*.csv` (169M×4, MANUAL_DOWNLOAD_REQUIRED), `data/raw/moneypuck/**/*.{csv,zip}` (1.9M, 485 files, EXTERNAL_API_REQUIRED), `research/real_nhl_pbp/raw/**/*.json` | Confirmed still excluded, no change needed |
| **LOCAL_OPERATIONAL_DATA** | `operational/prospective_observations.db` | Does not exist yet (verified) — already gitignored for when it does |
| **SECRET** | `.env` (50 bytes, The Odds API key) | Confirmed gitignored, confirmed not tracked, confirmed no other secret-pattern files exist anywhere in the dry-run add list. Contents never inspected. |
| **TEMPORARY** | None remaining requiring action this task (the Odds API evidence-directory pollution was already fixed and cleaned last sprint) | N/A |

One out-of-scope observation, documented not fixed (Part 16 forbids new development this task): `data/raw/moneypuck/` has accumulated 485 small zip snapshots since 2026-08-23 (1.9M total) — a pattern consistent with the same class of test-isolation bug fixed for the Odds API archive last sprint, applied to the MoneyPuck download-staging path instead. Already gitignored, so no git risk; flagged in `PRESEASON_FREEZE_MANIFEST.md` for a future narrow fix.

## F. `.gitignore` Verification

Confirmed protects: secrets (`.env`), local operational databases (`operational/prospective_observations.db`), all raw/regenerable research data (MoneyPuck CSV/ZIP, PBP raw JSON, the two research DBs, 8 derived per-game JSONL corpora — 3 newly added this task, 5 correcting a pre-existing inconsistency), and transient API/test artifacts (the Odds-API test-pollution quarantine pattern from last sprint). 3 new, narrowly-justified rule additions this task — see Part E. Verified via `git add -A -n` (dry run): zero files over 1MB, zero secret-pattern filenames, in the resulting add list.

## G. Files Included in Checkpoint

~465 new files (all of `dashboard/`, `research/` source + small result artifacts, `operational/`, `pricing/`, `tests/`, every root report, the 3 config/prototype files) plus updates to the 5 already-tracked files (`.gitignore`, `README.md`, `requirements.txt`, `tests/test_structural_reads.py`, `tests/test_training_path_structural_audit.py`). Exact count and commit hash in Parts K/L below.

## H. Files Intentionally Excluded

All 8 newly-gitignored derived corpora/databases (Part E), all previously-ignored raw/regenerable data, `.env`, and the non-existent-yet `prospective_observations.db`. `nhl.db` (13M, synthetic/demo data) remains tracked exactly as it was in the original snapshot commit — not modified, not newly added, a pre-existing state this task did not change.

## I. Local-Only Data Inventory

See `PRESEASON_FREEZE_MANIFEST.md`'s full table — every local-only artifact classified as REGENERABLE, MANUAL_DOWNLOAD_REQUIRED, OPERATIONAL_BACKUP_REQUIRED, or EXTERNAL_API_REQUIRED, with the exact regeneration command for each.

## J. Reproducibility Status

A fresh clone plus `pip install -r requirements.txt` reproduces: all source code, all tests (2,105/2,105 should pass immediately — no data files are required for the test suite itself, which uses synthetic fixtures throughout), and the dashboard in DEMO mode. Reproducing full RESEARCH mode (real historical corpora) requires either re-running the real ingestion pipeline against the official NHL API and a manually-sourced MoneyPuck archive (see manifest), or restoring the gitignored `.db`/`.jsonl` files from a backup taken on this machine. **No such backup currently exists** — see Part 9's instruction and the manifest's honest statement that `operational/prospective_observations.db` has no backup because it does not exist yet, and the large research DBs/JSONLs have not been separately backed up outside this git checkpoint (which correctly excludes them as regenerable).

## K. Git Commit Hash

**`6335ce3`** — "Preseason engine freeze: operational readiness closure", on top of the original snapshot commit `e4652a9`. Local commit only; nothing was pushed, no remote was added, no history was rewritten, squashed, or force-anything. 462 files changed, 202,533 insertions, 1 deletion (a trailing-content fix in `requirements.txt`).

Verified directly after committing:
- `git show --name-only HEAD` contains no `.env`/secret/credential-pattern filename.
- `git ls-tree -r --name-only HEAD | grep '\.db$'` returns exactly one file: `nhl.db` (13.8M) — no `prospective_observations.db`, `special_teams_history.db`, `research_pbp.db`, or `research_moneypuck.db` was committed.
- `git diff --stat e4652a9 HEAD -- nhl.db` is empty — `nhl.db` is byte-identical to the parent commit, not modified or newly added by this task.
- `git ls-tree -r -l HEAD | sort -k4 -nr` shows `nhl.db` (pre-existing) as the single largest blob; every other file is under 1.4MB, consistent with the SHOULD_COMMIT classification in Part E.

## L. Post-Commit Git Status

**Clean.** `git status --short` returns nothing — no modified files, no untracked files. `git status --ignored --short` lists 551 correctly-ignored paths: `.env`, every `__pycache__/`, the MoneyPuck raw CSV/ZIP staging directories, and the 8 derived research corpora/databases classified `SHOULD_IGNORE` in Part E. Everything remaining untracked-and-ignored is accounted for in `PRESEASON_FREEZE_MANIFEST.md`'s data-artifact table.

## M. Current Authoritative Engine Facts

- PLAYER SOG validated: 2+, 3+, 4+, 5+. SOG 1+: NOT separately validated (trivial/near-universal). SOG 6+/7+/8+: insufficient tail data.
- DK verified contracts: 0.
- Scheduler: NOT INSTALLED.
- PP-role SOG overlay: SHADOW_VALIDATED (1+/2+/3+ only).
- `decision_policy` v3: unchanged.

## N. September 17 Activation Dependency

Scheduler installation and `launchd` load remain a deliberately separate, future, explicitly-requested action — see `FIRST_LIVE_NHL_DAY_CHECKLIST.md`'s "Sept 17 activation checklist" section (added last sprint, unexecuted). Nothing in this task moves that date closer or further.

## O. Real-Payload Dependency

Every remaining path to a real operational decision (Goals/Assists/Points/Saves pricing, real CLV, real settlement, prospective-validation sample accumulation) is blocked on a real DraftKings payload appearing — verified, not assumed, as of 2026-08-30 (zero coverage across 142 markets, checked twice). This task did not re-check live Odds API state (Part 14 forbids it) and relies entirely on the prior audit's already-real evidence.

## P. Exact Conditions That Should Cause Development to Resume

1. A real DraftKings NHL market payload is observed (any market, any threshold) — triggers Part 41's first-real-payload workflow in `FIRST_LIVE_NHL_DAY_CHECKLIST.md`.
2. September 17 scheduler activation is explicitly requested by the owner.
3. A real preseason/regular-season run exposes a genuine software defect (something that actually breaks against real data, not a hypothetical).
4. The owner explicitly chooses to start a new research family (Hits, PP_POINTS, first-goal, etc.) despite the current wait recommendation.

---

## Final Questions

**DOES SOG SHADOW RECORDING NOW USE THE CANONICAL CHECKPOINT PATH?** YES.

**CAN PRE_GAME_UPDATE EXIST WITHOUT PRIMARY_DAILY?** NO.

**DID ANY MODEL CHANGE?** NO.

**DID ANY OVERLAY PARAMETER CHANGE?** NO.

**DID DECISION_POLICY V3 CHANGE?** NO.

**WERE ODDS API CREDITS USED?** NO.

**IS THE SCHEDULER INSTALLED?** NO.

**WERE SECRETS COMMITTED?** NO.

**WERE RAW LARGE RESEARCH DATASETS COMMITTED?** NO.

**WAS THE PROSPECTIVE OPERATIONAL DB COMMITTED?** NO — it does not exist yet, and is gitignored for when it does.

**WAS A LOCAL PRESEASON FREEZE COMMIT CREATED?** YES.

**COMMIT HASH?** `6335ce3`

**CURRENT TEST RESULT?** 2,105 / 2,105.

**HOW MANY DK CONTRACTS ARE VERIFIED?** 0.

**WHAT EVENT SHOULD TRIGGER THE NEXT DEVELOPMENT SPRINT?** Any one of the 4 conditions in Part P above — most likely, in practice, a real DraftKings payload finally appearing as the season approaches.
