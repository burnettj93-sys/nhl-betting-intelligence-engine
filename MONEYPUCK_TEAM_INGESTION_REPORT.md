# MoneyPuck Team xG / Shot-Quality Ingestion Foundation — Report

**This turn built ingestion + validation only.** No production model,
Elo, pricing, threshold, or PIT-table code was touched. No derived
feature (rolling xG, xG%, team-strength score) was computed anywhere.
`nhl.db` is untouched (confirmed by mtime, unchanged from prior turns).
All new code lives under `research/moneypuck_ingestion/` plus one new
test file. Full suite: **378 / 378 passing** (345 pre-existing + 33 new).

## A note on how the real data was obtained this turn

Before writing any ingestion code, a direct HTTP request to MoneyPuck's
`all_teams.csv` endpoint was attempted to confirm reachability, and it
returned an automated "Data License" redirect page:
*"it looks like you're using MoneyPuck to scrape data... Please reach
out to us... to get a data license agreement."* Since this slice's own
instructions say both "do not scrape MoneyPuck webpages" and "do not
contact MoneyPuck," that left no path for me to resolve unilaterally —
building an automated bulk-fetch pipeline that routes around an explicit
anti-scraping notice isn't something I'll do. This was raised with you
directly; **you downloaded the file yourself**, through your own
browser, using the exact download link MoneyPuck's own `data.htm` page
advertises — ordinary, non-automated use of a publicly listed download,
not scraping. I ingested the file you provided. This is why Section C
below reports `downloaded_at_utc` as the file's own real download
timestamp rather than a time I fetched it.

---

## A. Exact MoneyPuck file(s) ingested

`all_teams.csv` — MoneyPuck's team game-by-game dataset, all seasons
2008-09 through 2025-26 combined in one file (111 columns, 232,220 data
rows). Verified genuine: byte count (126,483,024) and row count (232,220)
match exactly what `MONEYPUCK_DATA_CONTRACT_REVIEW.md` documented for
this same file from its own earlier (small-sample) review.

## B. Source URL

`https://moneypuck.com/moneypuck/playerData/careers/gameByGame/all_teams.csv`

## C. downloaded_at_utc

`2026-08-27T13:23:32.440135+00:00` — the file's own real filesystem
mtime from your download, used as-is (not fabricated, not backdated to
any game's own date).

## D. Valid SHA-256 checksum

```
d4ca191ccc57e54c56f46497f09240b3e7f2d80f66dc2c41fe635825d035dd8f
```
64 lowercase hex characters (mechanically verified — see Section
"checksum fix" below and `tests/test_moneypuck_ingestion.py::TestChecksums`).
Matches `shasum -a 256` on the file independently.

**Checksum helper bug, fixed**: `MONEYPUCK_DATA_CONTRACT_REVIEW.md`
Section C flagged that its in-browser SHA-256 helper had an off-by-one
hex-encoding bug producing 65-character digests. This slice's
`research/moneypuck_ingestion/checksums.py` does not hand-roll hex
encoding at all — it calls `hashlib.sha256(...).hexdigest()` directly
(always correct) and validates every digest it produces against a
`^[0-9a-f]{64}$` regex before returning it, raising `ChecksumError` if
that check ever somehow failed. Regression tests pin down both the fix
and the exact failure shape of the old bug
(`test_is_valid_sha256_hex_rejects_the_prior_off_by_one_bug_shape`).

---

## E. Exact normalized schema

`research/moneypuck_ingestion/schema.sql` — new table
`research_moneypuck_team_game_stats`, entirely separate from `nhl.db` /
production `schema.sql` (own SQLite file: `research/moneypuck_ingestion/research_moneypuck.db`).
Append-only (never `UPDATE`s a row — a changed value is a new inserted
revision, see Section R): game identity (`game_id`, `season`,
`game_date`, `team`, `opponent`, `situation`, `home_or_away`), result
(`goals_for/against`, `shots_for/against`), xG (`xg_for/against`),
shot/chance-quality (shot/unblocked-shot attempts, high/medium/low-danger
shots and xG, rebounds, all for/against), score-adjusted fields, and
provenance (`provenance_type`, `source`, `source_file`, `source_sha256`,
`downloaded_at_utc`, `ingested_at_utc`, `xg_model_version_semantics`).
Unique index on `(game_id, team, situation, source_sha256)`.

## F. Source-to-normalized field mapping

| Normalized column | MoneyPuck source column |
|---|---|
| `game_id` | `gameId` |
| `season` | `season` (mechanically reformatted: `2022` → `20222023`) |
| `game_date` | `gameDate` (reformatted `YYYYMMDD` → `YYYY-MM-DD`) |
| `team` | `team` |
| `opponent` | `opposingTeam` |
| `situation` | `situation` (verbatim, never collapsed) |
| `home_or_away` | `home_or_away` |
| `goals_for` / `goals_against` | `goalsFor` / `goalsAgainst` |
| `shots_for` / `shots_against` | `shotsOnGoalFor` / `shotsOnGoalAgainst` |
| `xg_for` / `xg_against` | `xGoalsFor` / `xGoalsAgainst` |
| `shot_attempts_for/against` | `shotAttemptsFor` / `shotAttemptsAgainst` |
| `unblocked_shot_attempts_for/against` | `unblockedShotAttemptsFor` / `Against` |
| `{high,medium,low}_danger_shots_for/against` | `{high,medium,low}DangerShotsFor` / `Against` |
| `{high,medium,low}_danger_xg_for/against` | `{high,medium,low}DangerxGoalsFor` / `Against` |
| `rebounds_for/against` | `reboundsFor` / `reboundsAgainst` |
| `score_adjusted_shot_attempts_for/against` | `scoreAdjustedShotsAttemptsFor` / `Against` |
| `score_venue_adjusted_xg_for/against` | `scoreVenueAdjustedxGoalsFor` / `Against` |

Fields explicitly **not** ingested this slice (present in the source but
out of scope): `xGoalsPercentage`/`corsiPercentage`/`fenwickPercentage`
(pre-computed ratios — excluded on the same "no derived features yet"
principle even though MoneyPuck computes them, not us), rush attempts
(not a team-level field in this file — only exists per-shot in the
separate shots file, per the prior review), and every player/goalie/line
column (this file doesn't have them; those are separate MoneyPuck files,
explicitly out of scope this slice).

## G. Row grain

Verified directly against the real file: **one row per (team, game,
situation)** — every `gameId` appears exactly twice (once per team) ×
5 `situation` values. Confirmed exactly matching the prior review's
documented grain, and confirmed no collapsing occurred: the normalized
table holds all 5 situations per team per game, not just `all`.

## H. Canonical keys

`(game_id, team, situation)` for identity/uniqueness;
`(game_id, team, situation, source_sha256)` for the SQL-level idempotency
guard (a natural-key row from an already-ingested raw file is a
structural no-op). `game_id` is MoneyPuck's `gameId`, used as-is as the
NHL's own native game id (re-verified this turn — see Section J).

## I. Situation values observed

`{4on5, 5on4, 5on5, all, other}` — exactly the 5 documented in the prior
review, confirmed again from the real file and preserved verbatim
(never collapsed) in the normalized table.

## J. Seasons ingested

Target-filtered to the 4 seasons matching the real NHL corpus / Elo
research: **2022-23, 2023-24, 2024-25, 2025-26** (MoneyPuck's own
`season` values `2022`–`2025`). The raw archive preserves the entire
file (all seasons 2008-25) unchanged, per instruction — only the
*normalized* table is scoped to these 4.

## K. MoneyPuck raw row counts

232,220 total data rows in the raw file; **55,920 rows** fall within the
4 target seasons and regular-season game type (`gameId` digits 5-6 ==
`02`, cross-checked against MoneyPuck's own `playoffGame` flag — 0
disagreements found across the entire file). Of those 55,920, all
52,480 pass every validation check (5,248 games × 2 teams × 5
situations); the remaining 3,440 target-season rows are legitimate
playoff games (`gameId` type `03`), correctly excluded by design since
the real NHL corpus is regular-season-only.

## L. Normalized row counts

**52,480 rows** in `research_moneypuck_team_game_stats` — exactly
5,248 games × 2 teams × 5 situations. **0 rejected.**

## M. Unique NHL games represented

**5,248** distinct `game_id`s (verified two ways: direct SQL
`COUNT(DISTINCT game_id)`, and independently via
`query.py::unique_game_coverage()` — both agree, mechanically asserted
in `validate_against_nhl_corpus.py`).

## N. Coverage vs. 5,248-game NHL corpus

**5,248 / 5,248 matched — 100.0000% coverage.** Every real NHL
regular-season game in `research/real_nhl_results/` has a corresponding
MoneyPuck team-game record, and every MoneyPuck regular-season-target
game_id is a real NHL corpus game (verified via unique-game-ID set
comparison, not raw row-count comparison, per instruction).

## O. Unmatched games

**0 unmatched MoneyPuck games, 0 NHL games missing from MoneyPuck.**

## P. Score / team / SOG discrepancies

- **Team discrepancies: 0.** Every matched game's MoneyPuck
  `{team, opponent}` pair exactly equals the NHL corpus's
  `{home_team, away_team}` pair.
- **Duplicate/conflicting-row conflicts: 0.** Every matched game has
  exactly 2 team rows at `situation='all'`.
- **Score discrepancies: 373 — found, verified, and explained, not
  silently resolved.** Investigated directly rather than left as a raw
  count: **all 373**, and only those 373, are shootout-decided games
  (`period_type == 'SO'` in the real corpus — exactly the corpus's own
  373 SO-game total, 0 REG/OT discrepancies). In every case,
  MoneyPuck's `goalsFor`/`goalsAgainst` for both teams are tied (the
  actual on-ice score at the end of overtime), while the NHL's official
  final score credits the shootout winner with one additional goal.
  This is a well-documented, real convention difference — MoneyPuck (like
  most advanced-stats providers) counts on-ice goals only, since a
  shootout-winning goal isn't a "real" statistical goal and doesn't
  count toward any player's goal total — **not a data error on either
  side**, and this ingester does **not** silently correct MoneyPuck's
  values to match the NHL's shootout-inclusive score. Anyone using
  `goals_for`/`goals_against` from this table for a shootout game should
  read it as "regulation+OT score," not "final score" — flagged here
  explicitly rather than glossed over.
- **SOG discrepancies**: not separately re-checked this turn beyond the
  prior review's own 5-game sample (which found exact SOG matches) —
  full SOG cross-checking across all 5,248 games was not part of this
  slice's minimum-foundation scope; `shots_for`/`shots_against` are
  ingested and available for a future, more exhaustive check if wanted.

**Specific games reconfirmation** (`2025030412`, `2025030413`,
`2025030414` — the real Stanley Cup Final games from the prior review):
all 3 verified present in the raw MoneyPuck archive (confirmed by direct
scan), but **correctly absent** from both the normalized table and the
NHL corpus comparison — these are playoff games (`gameId` type `03`),
outside this slice's regular-season-only scope by design, exactly
matching the real NHL corpus's own regular-season-only convention. Not a
coverage gap.

**Season samples** (5 evenly-spaced games per season, 20 total): all 20
found in MoneyPuck, all 20 scores match exactly (none of the sampled
games happened to be shootout games).

## Q. Idempotency results

First ingest: 52,480 NEW, 0 revised, 0 unchanged, 0 rejected. **Second
identical ingest of the same raw file: 0 NEW, 0 revised, 52,480
unchanged, 0 rejected** — confirmed both via the CLI's own report output
and by re-querying total row count (still exactly 52,480 after the
second run — no duplicates written).

## R. Revision behavior

Append-only by design (schema.sql's module docstring): a natural key
(`game_id, team, situation`) that reappears with a **different** raw
snapshot (different `source_sha256`) and **different** metric values is
classified `REVISED` and **inserted as a new row**, never overwriting the
earlier one — both revisions remain queryable, `ingested_at_utc` orders
them. Proven by
`tests/test_moneypuck_ingestion.py::test_revised_row_is_appended_not_overwritten`.

## S. Provenance implementation

Every normalized row carries `provenance_type` (`'ARCHIVAL_RESEARCH'`
for this ingest — a file downloaded today describing past games, not a
genuine historical system observation), `source='MoneyPuck'`,
`source_file` (the archived raw path), `source_sha256`,
`downloaded_at_utc` (the file's real download time, never fabricated —
see Section C), and `ingested_at_utc` (when this pipeline actually wrote
the row). The same `ingest_file()`/`normalize_row()` code accepts either
`'ARCHIVAL_RESEARCH'` or `'LIVE_OBSERVED'` via an explicit parameter (not
inferred), so a future daily-sync slice can reuse this exact parser
unmodified — not built this slice, per instruction.

## T. STRICT PRIOR-GAME-DATE query behavior

`research/moneypuck_ingestion/query.py::team_stats_as_of(team,
prediction_game_date, situation)` returns only rows with
`game_date < prediction_game_date` — same-day and future rows are
excluded (`tests/test_moneypuck_ingestion.py`'s
`TestResearchQueryApiStrictPriorGameDate` class: same-day exclusion,
future-game exclusion, and situation-scoping all directly tested).
Ordering/eligibility is by the real `game_date` column, never `game_id`
or row/list position — `research/moneypuck_ingestion/validate_against_nhl_corpus.py`
also passes `tests/test_training_path_structural_audit.py`'s AST-level
scan for exactly this reason.

## U. xG version-semantics warning

```
ARCHIVAL HISTORICAL MONEYPUCK XG
MODEL VERSION SEMANTICS: UNKNOWN
```

Carried as a literal column (`xg_model_version_semantics`, default
`'UNKNOWN'`) on **every** normalized row, per instruction — so the
caveat travels with the data itself rather than living only in a
document a future query might not read. Per the prior review: it is
unknown whether MoneyPuck's xG model has been retrained since its
documented ~2007-2015 training window, and unknown whether historical
shots get rescored when it changes. This does not prohibit research use;
it limits the historical claim — an archived xG value here should not be
presented as "the exact value a real-time bettor would have seen."

## V. Licensing warning

Preserved unchanged from `MONEYPUCK_DATA_CONTRACT_REVIEW.md`: MoneyPuck's
data is licensed "free to use for non-commercial purposes and by
journalists for ad-hoc use," with mandatory attribution and an explicit
requirement to contact MoneyPuck for anything beyond that. This
integration is marked **RESEARCH / DEVELOPMENT DATA SOURCE**, pending
any broader licensing permission required for commercial use. No
documentation here implies unrestricted or commercial use. MoneyPuck was
not contacted this turn (per instruction) — see the note at the top of
this report on how the raw file was actually obtained (by you, directly,
not by an automated scrape) given the live bot-detection/licensing gate
encountered when reachability was tested.

## W. Files created / modified

**Created (nothing pre-existing touched):**
- `research/moneypuck_ingestion/checksums.py` — fixed SHA-256 helper
- `research/moneypuck_ingestion/raw_archive.py` — raw-file archival + sidecar provenance
- `research/moneypuck_ingestion/schema.sql` — separate research table
- `research/moneypuck_ingestion/ingest.py` — parse/validate/normalize/idempotent-write
- `research/moneypuck_ingestion/query.py` — STRICT PRIOR-GAME-DATE research query API
- `research/moneypuck_ingestion/ingest_moneypuck_team.py` — CLI entry point
- `research/moneypuck_ingestion/validate_against_nhl_corpus.py` — cross-check driver
- `research/moneypuck_ingestion/raw/team_gamebygame/all/2026-08-27T132332Z_all_teams (2).csv` + `.provenance.json` sidecar — **the archived raw file, ~121MB**
- `research/moneypuck_ingestion/research_moneypuck.db` — the normalized research database, **~34MB**
- `tests/test_moneypuck_ingestion.py` — 33 new tests
- `MONEYPUCK_TEAM_INGESTION_REPORT.md` — this report

**Size flag, your call, not decided here**: the raw archive + research DB
together are ~155MB of new working-tree content. `.gitignore` currently
excludes only `__pycache__/`, `*.pyc`, `.DS_Store` — nothing here is
committed yet (`git status` shows everything untracked), but if you
plan to commit, you may want to `.gitignore` `research/moneypuck_ingestion/raw/`
and/or `research_moneypuck.db` (both are fully reproducible from the
original `all_teams.csv` via `ingest_moneypuck_team.py`) rather than
checking in a 155MB blob. Left as-is pending your preference.

**Modified:** none. `nhl.db`, `models/`, `config.py`, pricing/decision/
threshold code, `research/real_nhl_results/`, `research/moneypuck_review/`,
and every other pre-existing file are untouched.

## X. Full test result

**378 / 378 passing, 0 failed, 0 errors, 0 skipped** (345 pre-existing +
33 new).

## Y. Recommendation on whether the data foundation is approved

```
APPROVED (as a foundation) -- with the SO-goal caveat (Section P)
carried forward explicitly into any future feature-engineering slice.
```

100% real, verified game coverage against the accepted NHL corpus, 0
team/duplicate discrepancies, a fully understood and documented (not
silently papered over) score-discrepancy pattern limited to shootout
games, idempotent and revision-safe ingestion, clean provenance
separation from production PIT tables, and a PIT-safe research query API
already in place. This is a solid foundation to build derived team-xG
features on top of in a future slice — but that next slice should be the
one to decide how (or whether) to reconcile the shootout-goal difference
for any feature that uses `goals_for`/`goals_against`, not this one.

## Z. Recommended NEXT single development slice

Per instruction, still ingestion/foundation work, not feature-building
yet — the next single slice should be the first **derived** feature
built on top of this foundation:

```
ROLLING TEAM xG-FOR/xG-AGAINST FEATURE (5v5, PIT-safe, via
team_stats_as_of()), evaluated the same rigorous way the Elo candidates
were: real walk-forward, real held-out seasons, Brier/log-loss/
calibration comparison against the current model -- before any
production integration.
```

That slice, not this one, is where "do not build derived features yet"
stops applying.

---

## Final questions

```
WAS REAL MONEYPUCK DATA INGESTED?
YES

WERE MONEYPUCK GAME IDS JOINED DIRECTLY TO NHL GAME IDS?
YES -- no crosswalk needed or used; MoneyPuck's gameId is the NHL's own
native id, re-verified this turn (100% coverage, 0 team mismatches).

WAS FOUR-SEASON NHL GAME COVERAGE VALIDATED?
YES

WHAT % OF THE 5,248 NHL GAMES ARE REPRESENTED?
100.0000% (5,248 / 5,248)

WERE MATERIAL NHL/MONEYPUCK RESULT DISCREPANCIES FOUND?
YES -- 373 games (all shootout-decided), all fully explained: MoneyPuck
excludes the shootout-winning goal from goals_for/against by design.
Not a data-quality defect; documented, not silently resolved.

IS THE INGESTION IDEMPOTENT?
YES

ARE ARCHIVAL_RESEARCH AND LIVE_OBSERVED PROVENANCE KEPT DISTINCT?
YES

WERE HISTORICAL observed_at TIMESTAMPS FABRICATED?
NO

IS STRICT PRIOR-GAME-DATE ENFORCED FOR RESEARCH QUERIES?
YES

WAS PRODUCTION PREDICTION LOGIC CHANGED?
NO

CURRENT FULL TEST RESULT?
378 / 378

IS THE MONEYPUCK TEAM DATA FOUNDATION APPROVED?
YES

WHAT SHOULD THE NEXT SINGLE DEVELOPMENT SLICE BE?
A rolling team xG-for/xG-against feature (5v5, via the STRICT
PRIOR-GAME-DATE team_stats_as_of() query API), evaluated with the same
real-data walk-forward rigor as the Elo comparison, before any
production model integration.
```

---

## STOP AFTER THIS SLICE

Per instruction: no rolling xG features were added. xG was not added to
the model. Elo was not changed. Goalie/player/line MoneyPuck ingestion
was not built. Daily automation was not built. The Odds API was not
integrated. No UI was built. This report is returned for independent
review; no further action was taken this turn.
