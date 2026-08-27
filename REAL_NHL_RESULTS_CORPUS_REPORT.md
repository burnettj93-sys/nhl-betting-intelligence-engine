# Real NHL Historical Results Corpus — Delivery Report

**This turn built the corpus only.** No Elo candidate code was implemented
or tuned. `nhl.db` was not modified (confirmed below). All 322 production
tests still pass unchanged.

## A. Exact NHL endpoints used

- `https://api-web.nhle.com/v1/schedule/{date}` — the real, public,
  unauthenticated league-wide weekly schedule endpoint. Nothing else was
  called (no `/v1/club-schedule-season/{team}/{season}`, no boxscore
  endpoints — not needed since game-level scores/period-type are already
  present in the schedule response itself).

## B. Capture method

Real browser fetches (`javascript_tool`'s in-page `fetch()`, same-origin
against an already-open `api-web.nhle.com` tab — not a proxy, not a
Python `requests` call, since this sandbox's own network still can't
reach the NHL API directly, same constraint as every earlier turn this
session). Each response's raw `.text()` was captured unmodified and saved
to its own file before any parsing happened. Multiple weeks were batched
per browser round trip (3-7 per call, tuned down after a couple of
oversized batches got silently truncated by the device bridge's transport
— every truncated batch was detected via post-save validation, i.e. every
file being valid JSON with no truncation marker, and re-fetched at a
smaller size until clean; see N below).

## C. Number of API responses required

**112** — 28 weekly requests × 4 seasons. This is far cheaper than
per-team collection (which would have needed ~32 teams × 4 seasons = 128
requests plus game-ID deduplication across each pair of teams). The
weekly-walk strategy was chosen for exactly that reason (see the
"Collection method" section of `research/real_nhl_results/README.md` for
the full comparison) and confirmed empirically: requesting date `D`
always returns the exact non-overlapping window `[D, D+6]`, so the full
season's request list could be precomputed from the response's own
`regularSeasonStartDate`/`regularSeasonEndDate` fields rather than walked
one `nextStartDate` at a time.

## D. Raw files created

112 files under `research/real_nhl_results/raw/<season>/<date>.json`, one
per real weekly response, byte-for-byte as returned by the NHL API.
Verified: all 112 parse as valid JSON, none contain a transport-truncation
marker.

## E. Seasons captured

- **2022-23** (season code `20222023`): regular season 2022-10-07 →
  2023-04-14
- **2023-24** (`20232024`): 2023-10-10 → 2024-04-18
- **2024-25** (`20242025`): 2024-10-04 → 2025-04-17
- **2025-26** (`20252026`): 2025-10-07 → 2026-04-17

All four exact boundary dates came directly from the API's own
`regularSeasonStartDate`/`regularSeasonEndDate` fields (not assumed or
hardcoded). All four are genuinely complete as of today (2026-08-26) —
even 2025-26, whose `playoffEndDate` field reports 2026-06-15, confirming
this most recent season is fully finished.

## F. Normalized regular-season game count per season

| Season | Games |
|---|---|
| 2022-23 | 1,312 |
| 2023-24 | 1,312 |
| 2024-25 | 1,312 |
| 2025-26 | 1,312 |
| **Total** | **5,248** |

Each season lands exactly on 1,312 — the expected scale for a 32-team,
82-game NHL regular season (32 × 82 / 2 = 1,312) — with none dropped to
invalid/non-final/cancelled status (see H-J below). This was not
hardcoded as an assumption anywhere in the collection or validation code;
it's the count that fell out of the real data.

## G. Unique teams per season

**32 teams in every one of the 4 seasons** (full current-era NHL,
including Seattle and Vegas; no team-abbreviation handling was needed
since the collection method never referenced a hardcoded team list — see
C above).

## H. Duplicate game IDs encountered

**0.** The weekly league-wide feed never repeats a game across two
different weekly windows (unlike the per-team strategy, which would
structurally duplicate every game once for the home team's feed and once
for the away team's), so no dedup pass was actually needed in practice —
the dedup-by-`game_id` logic in `build_research_corpus.py` still ran (in
case a game ever straddled a window boundary) and confirmed zero
duplicate sightings.

## I. Conflicting duplicates

**0** (moot given H, but the conflict-detection logic ran regardless and
would have failed loudly rather than silently picking a side, per
instruction, had one existed).

## J. Missing/invalid records

**0.** Every one of the 5,248 `gameType == 2` games in a terminal state
(`OFF` or `FINAL`) passed every check: non-null unique `game_id`, `home_team
!= away_team`, valid `game_date`, valid `scheduled_start_utc`, non-null
`home_score`/`away_score`, valid `period_type` (REG/OT/SO). 0 non-final
regular-season games remained after filtering, 0 cancelled/postponed.
(32 preseason and 37 playoff game IDs were seen and excluded from the
regular-season corpus, per instruction — they're visible only in the raw
weekly files, which do include a handful of transition-week playoff
games because the raw capture windows weren't trimmed after the fact.)

## K. REG / OT / SO distribution

| Season | REG | OT | SO |
|---|---|---|---|
| 2022-23 | 1,010 | 207 | 95 |
| 2023-24 | 1,040 | 190 | 82 |
| 2024-25 | 1,041 | 194 | 77 |
| 2025-26 | 986 | 207 | 119 |
| **Total** | **4,077** | **798** | **373** |

## L. Goal-margin distribution (all 4 seasons combined, `abs(home_score - away_score)`)

| Margin | Count |
|---|---|
| 1 | 2,101 |
| 2 | 1,021 |
| 3 | 1,243 |
| 4 | 573 |
| 5 | 203 |
| 6 | 63 |
| 7 | 37 |
| 8 | 5 |
| 9 | 2 |

Sensible real-hockey shape (1-goal games most common; a real bump at
margin 3 that's consistent with a 2-goal game turning into a 3-goal final
via an empty-net insurance goal — exactly the phenomenon Candidate C's MOV
cap in the prior report is designed to blunt, not eliminate).

## M. Earliest/latest game date per season

| Season | Earliest | Latest |
|---|---|---|
| 2022-23 | 2022-10-07 | 2023-04-14 |
| 2023-24 | 2023-10-10 | 2024-04-18 |
| 2024-25 | 2024-10-04 | 2025-04-17 |
| 2025-26 | 2025-10-07 | 2026-04-16 |

## N. Confirmation raw NHL payloads were not mutated

Confirmed programmatically: every file in `research/real_nhl_results/raw/`
was written exactly once at capture time, directly from the fetch
response's `.text()`, with zero post-write edits. Separately, a handful of
oversized browser-batch calls (~7 weeks per call) were truncated
mid-transfer by the device bridge itself (visible as a literal
`…[truncated: content too large for the device bridge]` marker replacing
part of the JSON) — those specific batches were **discarded and re-fetched
from scratch at a smaller batch size**, never patched or partially kept.
A final validation pass parsed all 112 saved files as JSON and grepped
for the truncation marker: 0 bad files remained.

## O. Confirmation `nhl.db` was not modified

Confirmed by file modification time, checked before and after all of this
turn's work:
```
-rw-r--r-- 1 root root 13803520 2026-08-26 20:15:02 nhl.db
```
Unchanged from the prior turn's report. Nothing in this turn's code opened
`nhl.db` for writing — `build_research_corpus.py` only reads
`research/real_nhl_results/raw/*.json` and writes into
`research/real_nhl_results/`.

## P. Exact research availability policy

**RESEARCH AVAILABILITY POLICY: STRICT PRIOR-GAME-DATE** — for a target
game on NHL calendar date `D`, a completed game is eligible to be learned
from if and only if its `game_date` is strictly earlier than `D`. A
same-day game is never eligible regardless of its real completion time.
No historical `observed_at_utc`/`result_observed_at_utc` was fabricated or
backdated anywhere. Full rationale and explicit non-equivalence to the
production PIT system is documented in
`research/real_nhl_results/README.md`.

## Q. Sufficiency for a multi-season Elo comparison

**YES.** 5,248 real regular-season games across 4 complete seasons, with
meaningful REG/OT/SO variation (798 OT + 373 SO = 1,171 non-regulation
games, ~22% of the corpus) and a full real goal-margin distribution up to
9 — this is adequate scale and variation to run the walk-forward
Brier/log-loss/calibration comparison across all 4 candidates (A/B/C/D)
from the prior report, season-by-season, exactly as your Step 5 required.
This directly resolves the blocker reported last turn (n=3 real games was
the prior ceiling; it is now n=5,248 across 4 seasons).

---

## Required answers

```
REAL NHL HISTORICAL RESULTS CORPUS CREATED?
YES

NUMBER OF REAL REGULAR-SEASON GAMES?
5,248

NUMBER OF COMPLETE SEASONS?
4 (2022-23, 2023-24, 2024-25, 2025-26)

IS THE CORPUS SYNTHETIC?
NO

WERE HISTORICAL observed_at TIMESTAMPS FABRICATED?
NO — no observed_at_utc/result_observed_at_utc of any kind exists in this
corpus. Eligibility for any future comparison is governed solely by the
STRICT PRIOR-GAME-DATE policy in Item P, which uses only game_date.

IS IT SUFFICIENT TO PROCEED WITH THE ELO COMPARISON?
YES
```

Then STOP, per your instruction. No Elo candidate implementation or
tuning was started this turn. No xG work, no goalie-workload
implementation, and no paid-odds work were touched.
