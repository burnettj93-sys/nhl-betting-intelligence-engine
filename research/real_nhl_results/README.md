# Real NHL Historical Results — Research Corpus

**This is a SEPARATE research corpus. It is NOT part of the production
point-in-time (PIT) database (`nhl.db`) and NOT part of the accepted
`ingest/nhl_api.py` / `features/point_in_time.py` pipeline.** Nothing here
was inserted into `game_schedule_events` or `game_result_events`, and
`nhl.db` was not opened for writing at any point while building this.

## What this is

Real, unmodified NHL regular-season schedule/result data captured live from
`api-web.nhle.com` via the browser bridge, covering four complete NHL
regular seasons: **2022-23, 2023-24, 2024-25, 2025-26**.

- `raw/<season>/<date>.json` — 112 files, one per real weekly
  `/v1/schedule/{date}` API response, **byte-for-byte as returned by the
  NHL API** (verified: none contain a transport-truncation marker, all
  parse as valid JSON). 28 weeks × 4 seasons. Nothing in this directory
  was edited after capture.
- `normalized_regular_season_games.jsonl` — one line per validated real
  regular-season (`gameType == 2`) game, fields: `game_id`, `season`,
  `gameType`, `game_date`, `scheduled_start_utc`, `home_team`,
  `away_team`, `home_score`, `away_score`, `game_state`, `period_type`
  (REG/OT/SO). Derived from the raw files above by
  `build_research_corpus.py` — never hand-edited.
- `corpus_quality_report.json` — full machine-readable output of every
  check described below (dedup, conflicts, invalid records, per-season
  breakdown).

## Why this corpus exists, and its scope

Built specifically to support a controlled walk-forward comparison of four
Elo update-rule candidates (baseline / OT-SO-aware / capped-MOV /
combined) — see `TEAM_STRENGTH_ELO_REPORT.md`. It is real data, not the
synthetic `demo_generator` data in `nhl.db`.

**This corpus MAY be used for:** team-strength model comparison, Brier
score comparison, log-loss comparison, calibration comparison,
season-by-season probability evaluation, testing Elo update formulas.

**This corpus may NOT be used as evidence of:** exact production PIT
replay, historical injury knowledge, historical goalie-confirmation
knowledge, historical sportsbook availability, ROI, or CLV. Those remain
separate, unrelated problems this corpus says nothing about.

## RESEARCH AVAILABILITY POLICY: STRICT PRIOR-GAME-DATE

We do not possess, and are not claiming to possess, the historical
timestamp at which this engine's production pipeline would have actually
learned each game's final result. Fabricating a historical
`observed_at_utc`/`result_observed_at_utc` for these games — backdating
today's capture to the game's real calendar date — would violate the
project's accepted temporal architecture (see `features/point_in_time.py`
and the v2.1/v2.1.1 temporal-hardening docs), which is exactly why this
data lives here and not in `nhl.db`.

Instead, for any model-comparison study run against this corpus, game
eligibility as "already known" for a target game on NHL calendar date `D`
is defined as:

```
a completed game is eligible to be learned from if and only if
its game_date is STRICTLY EARLIER than D.
```

A same-day game — even one that in reality finished hours before the
target game's puck drop — is **not** eligible. This deliberately discards
some legitimate real-world information in exchange for never having to
guess or fabricate an intra-day observation time. It is a conservative
historical-research approximation, not a re-implementation of the
production point-in-time system, and every candidate model in any future
comparison sees exactly the same eligibility rule, so the *comparison*
between candidates stays valid even though the absolute calibration
numbers are conservative versus what a live system with real sub-day
timestamps could achieve.

Eligibility here is **only** ever `game_date`. It is never `game_id`
ordering, list position, a future or later-revised score, or same-day
completion order — those are the exact leakage patterns this project's
existing `tests/test_training_path_structural_audit.py` guards production
code against, and this research corpus follows the same discipline even
though it lives outside that test's scope.

## Collection method

- Source: `api-web.nhle.com/v1/schedule/{date}` (the real, public,
  unauthenticated NHL schedule endpoint) — nothing scraped, no
  third-party or Kaggle dataset used.
- Strategy chosen after comparing two options: (1) `/v1/schedule/{date}`
  walked forward via the response's own `regularSeasonStartDate` /
  `regularSeasonEndDate` fields at a fixed 7-day cadence (confirmed
  empirically: requesting date `D` always returns the exact 7-day window
  `[D, D+6]`, and `nextStartDate == D+7` with zero gaps/overlaps — so the
  full walk is precomputable without reading `nextStartDate` from each
  response), covering every team in one league-wide feed; vs. (2)
  `/v1/club-schedule-season/{team}/{season}` once per team (32 requests/
  season, each game duplicated across two teams' responses, requiring
  game-ID dedup). Strategy (1) was chosen: ~28 requests/season vs. ~32,
  no per-team duplication to reconcile, and it is immune to a team's
  historical abbreviation changing across seasons (e.g. ARI→UTA) since it
  never needs a hardcoded team list at all.
- Fetched via `fetch()` executed in-page (same-origin, `javascript_tool`)
  against the already-open `api-web.nhle.com` tab, batched several weeks
  per browser round trip. Every response's raw `.text()` was preserved
  unmodified and saved to its own file; normalization into
  `normalized_regular_season_games.jsonl` happened only afterward, from
  those saved raw files, never from re-transformed or hand-edited data.
