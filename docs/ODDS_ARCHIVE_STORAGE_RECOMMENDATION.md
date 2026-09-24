# Odds Archive Storage Recommendation

**Date:** 2026-09-24 (Live SOG + Saves Production Certification block, Part 35). **No files were moved or deleted in this pass** — this is a recommendation only, per the explicit instruction not to take a destructive action this block.

## Current state

`data/raw/the_odds_api/live/` currently holds **924 real, captured Odds API response files, ~8.5MB total**, all git-tracked per this repository's own established, explicitly-documented convention (`.gitignore`: *"Raw captured live-odds JSON under data/raw/the_odds_api/ is intentionally NOT ignored — small, genuine market observations worth preserving, same precedent as every other raw-provenance file in this project"*). The prior source-control checkpoint (commit `7d9a076`) committed 750 of these files in one pass; more accumulate continuously from the live, already-scheduled scrapers (`moneyline-snapshot` 4×/day, `prop-sweep-first` every 30 min, `prop-sweep-second` every 15 min, `daily-props-pull` once daily) — every one of which archives its raw response via `research/live_sog_pricing/archive.py::archive_result()` before any parsing happens.

At ~8.5MB for roughly one month of intermittent real captures (this project's archive predates this session), and with the season now generating far more frequent captures via the newly-wired real orchestration triggers (Parts 22/34), this directory's growth rate will accelerate materially from here — a full 2026-27 season at the current 4×/day moneyline + 30-/15-min windowed prop-sweep cadence could plausibly reach hundreds of megabytes to low gigabytes by season's end.

## Recommendation: split by purpose, not by delete

**Keep git-tracked (small, curated, permanent):**
- One real, sanitized example file per verified contract shape, already established at `tests/fixtures/draftkings_h2h_real_payload.json` (MONEYLINE — the one real, verified contract) and this block's new `tests/fixtures/draftkings_player_shots_on_goal_shaped.json` / `draftkings_player_total_saves_shaped.json` (SOG/Saves — realistically-shaped but explicitly-labeled synthetic, since no real payload has ever existed for either).
- A small, rotating "recent evidence" set — e.g. the last N days' worth of real captures per market family — kept as regression-test fixtures and human-inspectable real-world evidence, mirroring how `research/goalie_saves/`, `research/player_sog/`, etc. already keep small, curated, git-tracked corpora rather than every raw source file.

**Move to runtime/operational storage (not git-tracked):**
- The bulk, ongoing stream of raw captures from the now-frequent real scheduled jobs. This project already has an established pattern for exactly this kind of accumulating real operational data: `operational/prospective_observations.db`, `operational/paper_bankroll.db`, and `operational/auth_store.db` are all real, accumulating, and deliberately **not** committed — instead backed up on a schedule via `operational/backup_databases.py` (see `docs/BACKUP_AND_RESTORE.md`). The raw odds archive should follow the identical pattern: retained on disk (or moved to a dedicated `operational/odds_archive/` directory, gitignored, backed up alongside the other operational databases) rather than accumulating indefinitely inside the git history itself, which is a poor fit for a fast-growing stream of small JSON files (git history size only ever grows, never shrinks, without a history rewrite — which this repository should never need to do for this reason alone).

## Why this matters now, not later

Two forces just changed materially in this block: (1) the real orchestration triggers (Parts 22 and 34) now fire automatically on every real scheduled snapshot/sweep, meaning capture frequency is no longer occasional/manual (as it was when this archive convention was first written) but continuous; (2) this project's own real, independently-running scheduler (confirmed live during this session — see the routine 17:00 UTC capture committed separately from this block's feature work) is already producing new files outside of any development session. Left unaddressed, `git log`/`git clone`/`git status` costs for this repository will grow proportionally with every real day the scheduler runs, for files whose long-term value is "operational history," not "source code."

## What this recommendation does NOT propose

- Deleting any currently-tracked historical evidence.
- Changing `.gitignore` in this pass (a change here should be deliberate and reviewed, not bundled into a certification block).
- Building the migration tooling itself (moving 924 files, updating `research/live_sog_pricing/archive.py`'s write path, adding a new backup job) — that is real, separate engineering work with its own review, intentionally left for a dedicated future pass.
