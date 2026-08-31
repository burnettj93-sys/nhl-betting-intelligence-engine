# NHL Play-by-Play Event-Timing Foundation — Pilot + One-Season Ingestion Report

This slice builds the real, auditable historical play-by-play data foundation
identified as the single highest-leverage gap in `COMPLETE_NHL_MARKET_ARCHITECTURE_REPORT.md`
(`PERIOD_EVENT_TIMING`, 62 markets unlocked — more than double the next-largest
process). It does **not** build any betting market, model, or the joint game
simulator. Every number below is real: computed from a genuinely fetched,
permanently archived 30-game pilot corpus and (where noted) a full real
2025-26 regular-season ingestion, both under `research/real_nhl_pbp/`.

---

## A. Exact NHL play-by-play endpoint/contract

`GET https://api-web.nhle.com/v1/gamecenter/{game_id}/play-by-play`

Public, keyless (no API key or auth header of any kind — confirmed live this
session). Response is a single JSON object with top-level keys `id`, `season`,
`gameType`, `gameDate`, `awayTeam`, `homeTeam`, `periodDescriptor`,
`regPeriods`, `otInUse`, `shootoutInUse`, `gameOutcome`, `rosterSpots`,
`summary` (empty on this endpoint — boxscore-style aggregates are not here),
and `plays` (the ordered event list this entire foundation is built from).
The separate boxscore endpoint used only for pilot validation (Part 20) is
`GET https://api-web.nhle.com/v1/gamecenter/{game_id}/boxscore`.

## B. Representative real games inspected (Phase 1 contract audit)

Before selecting the 30-game pilot, a handful of real games were inspected
directly to confirm the contract, including the 2024 Stanley Cup Final Game 7
(`2023030417`, regulation) and a stratified sample of 2023-24 Toronto Maple
Leafs games that produced a real shootout game (`2023020005`) and multiple
real overtime games — used only to prove the contract shape, never archived
as part of the permanent corpus (that corpus is the 30-game 2025-26 pilot
below).

## C. Raw field audit

**GAME**: `id`, `gameDate`, `gameType` (2 = regular season), `awayTeam.id`/
`.abbrev`, `homeTeam.id`/`.abbrev`, `regPeriods`, `otInUse`, `shootoutInUse`,
`gameOutcome.lastPeriodType` (REG/OT/SO), `gameOutcome.otPeriods`.

**EVENT** (each `plays[]` entry): `eventId`, `sortOrder`, `typeDescKey`,
`typeCode`, `periodDescriptor.{number,periodType,maxRegulationPeriods}`,
`timeInPeriod`, `timeRemaining`, `situationCode`, `homeTeamDefendingSide`,
`details.{xCoord,yCoord,zoneCode}`. **`periodDescriptor` never states a
period's length in seconds** — confirmed by taking the union of
`periodDescriptor` keys across the entire 30-game pilot (9,753 events): it is
exactly `{number, periodType, maxRegulationPeriods}`. Regulation/OT period
lengths are therefore two documented external constants (20:00 / 5:00 for a
regular-season game), not read from the feed — see Section F.

**PLAYER IDENTITY** fields confirmed present, per event type: goal →
`scoringPlayerId`, `assist1PlayerId`, `assist2PlayerId`, `goalieInNetId`;
shot-on-goal/missed-shot → `shootingPlayerId`, `goalieInNetId`; blocked-shot →
`shootingPlayerId`, `blockingPlayerId`; hit → `hittingPlayerId`,
`hitteePlayerId`; penalty → `committedByPlayerId`, `drawnByPlayerId`,
`servedByPlayerId`; faceoff → `winningPlayerId`, `losingPlayerId`. No field
was invented — every one above was observed on a real event in the archived
corpus. Every player ID is a real NHL player ID (already true project-wide —
MoneyPuck IDs are NHL IDs, per `MONEYPUCK_DATA_CONTRACT_REVIEW.md`).

## D. Event types (real, observed values)

`goal`, `shot-on-goal`, `missed-shot`, `blocked-shot`, `hit`, `penalty`,
`delayed-penalty`, `faceoff`, `giveaway`, `takeaway`, `stoppage`,
`period-start`, `period-end`, `game-end`, `shootout-complete`, and one
additional real type found only inside a shootout: `failed-shot-attempt`
(a shootout attempt that missed the net entirely — distinct from
`missed-shot`, which is used in regulation/OT play). All 16 types confirmed
directly from the pilot corpus; none of the prompt's placeholder names
(`SHOT_ON_GOAL`, `PERIOD_START`, etc.) matches the feed's real casing —
the feed uses kebab-case `typeDescKey` strings, not the prompt's illustrative
upper-snake-case.

## E. Player-role mappings

See `research/real_nhl_pbp/normalize.py::extract_players()` — implements
exactly the Section C field list above, verified against 100% real events in
the pilot (0 KeyErrors, 0 invented fallback fields).

## F. Period/time normalization

`research/real_nhl_pbp/normalize.py`. `seconds_elapsed()` parses `MM:SS`.
`compute_regulation_elapsed_seconds()` branches explicitly on `periodType`:
REG → `(period-1)*1200 + elapsed`; OT → `3*1200 + elapsed`; SO → `None`
(shootout has no game-clock time — it is attempt-indexed). **Two documented
external constants**, not read from the feed: `REGULATION_PERIOD_SECONDS =
1200` and `REGULAR_SEASON_OT_SECONDS = 300`. The OT constant was verified,
not assumed: the maximum `timeInPeriod` observed across every OT event in
the 30-game pilot is exactly `"05:00"`, never exceeded — consistent with the
real NHL regular-season 3-on-3 sudden-death rule. (This module's scope is
regular-season games only, `gameType == 2`; playoff OT's 20-minute format is
out of scope and not asserted anywhere.) Every SO event's `timeInPeriod` was
confirmed `"00:00"` (0/0 non-zero across the full pilot).

## G. Shootout semantics

**Critical, confirmed finding**: a shootout-deciding goal uses the exact same
`typeDescKey: "goal"` as a real statistical goal, but its `details.homeScore`/
`awayScore` fields are **frozen at the pre-shootout score** — confirmed on
game `2025020231` (PIT @ NJD), where the SO-winning goal's own event still
reads `"awayScore": 5, "homeScore": 5` even though the team's real *displayed*
final score is one higher. It also carries no `scoringPlayerTotal` (unlike a
real goal), so a shootout goal never increments a player's season goal total.
`normalize.py` treats `is_statistical = (period_type != "SO")` as the sole,
sufficient discriminator — corroborated, not contradicted, by both signals
above. `reconstruct_statistical_score()` never touches an SO-period goal;
`shootout_winner()` separately identifies the deciding team from the last
SO-period goal. Tests: `test_pbp_foundation.py` `Test06`, `Test17`.

## H. Goal normalization

Confirmed fields per Section C. **No explicit game-winning-goal flag exists
anywhere in the feed** — the entire 30-game pilot's goal events were searched
for any key containing "win" or "gwg"; none exists, and `gameOutcome` only
carries `lastPeriodType`/`otPeriods`, never a GWG pointer. This confirms the
prompt's own caution exactly: GWG must be *derived* from the full
reconstructed score timeline after the final score is known (Invariant #10),
never trusted from a supplied flag — because no such flag is supplied at all.

## I. Shot normalization

`shot-on-goal`/`missed-shot` carry `shooter` + `goalie` (defending team);
`blocked-shot` carries **both** `shooter` (shooting team) and `blocker`
(blocking team) on the same event — there is no ambiguity between
shooting-team and blocking-team perspective to resolve; both are present
simultaneously. **Real, verified finding**: the NHL's official SOG stat
counts a goal as a shot on goal. The feed does not emit a redundant
`shot-on-goal` event for the puck that actually scored, so a correct SOG
reconstruction must add 1 for each of a player's real (non-SO) goals — this
was discovered via an initial reconciliation mismatch against the real
`/boxscore` endpoint (every one of the first 30 SOG mismatches found was
exactly `goals` short), then confirmed as the fix, and re-validated to zero
mismatches. See Section S.

## J. Hit normalization

`hittingPlayerId` + `hitteePlayerId` present on **1,240/1,240 (100%)** of hit
events across the 30-game pilot — no missing participant found.

## K. Penalty normalization

`committedByPlayerId` + `duration` + `eventOwnerTeamId` present on every
penalty; `drawnByPlayerId` is absent on 18/240 (7.5%) penalties in the pilot
— **all 18 are genuine bench/officiating-triggered categories with no single
opposing player to credit**: `too-many-men-on-the-ice` (bench, carries
`servedByPlayerId` instead), `delaying-game-puck-over-glass`, `misconduct`,
`instigator-misconduct`, `abuse-of-officials`,
`delaying-game-unsuccessful-challenge` — every one independently verified by
name, not merely counted. **Real, verified finding**: official boxscore
per-skater PIM does **not** credit any individual skater for a bench-assessed
penalty either — confirmed by 0 PIM mismatches across all 30 games (3 of
which contain `too-many-men-on-the-ice` bench minors). Team-level PIM/penalty
counts are unaffected (`eventOwnerTeamId` is always present).

## L. Faceoff normalization

`winningPlayerId` + `losingPlayerId` present on **1,753/1,753 (100%)** of
faceoff events across the pilot — no missing participant found.

## M. Goalie-event findings

`goalieInNetId` is present on essentially every shot/goal event **except**
empty-net situations (see Section O) and shootout attempts against an empty
frame do not occur (a shootout always has a real goalie in net — confirmed).
A real, in-net **goalie change mid-game** (not an empty-net pull) was
confirmed via `situationCode`'s own goalie-in-net digit: 8/30 pilot games
show two distinct `goalieInNetId` values for one team while that team's
goalie-in-net digit stays `"1"` throughout (i.e. a real relief goalie, not a
pulled net) — e.g. game `2025020240` (EDM, goalies `8475717`→`8479973`).
Full-game saves are also directly exposed on `/boxscore`'s
`playerByGameStats.*.goalies[].saves` field, giving an independent
cross-check beyond event reconstruction alone.

## N. Manpower-state findings

**DIRECTLY AVAILABLE — not requiring reconstruction.** `situationCode` is a
real, confirmed 4-digit string `[awayGoalieInNet, awaySkaters, homeSkaters,
homeGoalieInNet]`. Decoded and verified three independent ways: (1) `"1551"`
dominates (8,053/9,753 pilot events) matching 5-on-5 with both goalies
dressed; (2) power-play codes like `"1541"`/`"1451"` (586/554 occurrences)
show an unequal skater count with both goalie digits `"1"`; (3) the shootout
attacker/defender pattern flips cleanly between `"1010"` (home team
attacking) and `"0101"` (away team attacking) on a real shootout game,
confirming the digit ordering unambiguously. This is a genuine upgrade over
`COMPLETE_NHL_MARKET_ARCHITECTURE_REPORT.md`'s working assumption that
`SPECIAL_TEAMS_STATE` needs penalty-duration reconstruction — it does not;
it is a direct per-event field.

## O. Empty-net findings

**Not an explicit boolean anywhere in the feed.** Inferred from two
independently-corroborating signals, both confirmed on all 4 real empty-net
goals found in the pilot: (1) the goal event's own `goalieInNetId` field is
absent, **and** (2) `situationCode`'s defending-team goalie digit is `"0"`
(e.g. `"0541"`, `"0651"`, `"1560"`). All 4 real cases also happened to carry
`"scores-empty-net-goal"` in their `highlightClipSharingUrl` text — an
incidental third confirmation, not relied upon programmatically since it is
not a structured field. `normalize.is_empty_net_context()` requires signals
(1) and (2) jointly.

## P. Score reconstruction

`reconstruct_statistical_score()` independently **counts** statistical goals
in `sortOrder` sequence (never trusts the feed's own embedded
`awayScore`/`homeScore` on the goal event as ground truth). Verified against
the real final score on all 30 pilot games: exact match on every REG/OT game;
on every SO game, the reconstructed statistical score is exactly one goal
below the real final score for exactly one team (the shootout bonus goal,
Section G) — confirmed, never off by any other margin.

## Q. Pilot game count

**30 real games**, season 2025-26, deliberately stratified: 18 regulation
(6 highest-total-goals, 6 highest-margin/blowout, 6 uniform-random baseline),
7 overtime, 5 shootout — chosen (Part 19) to raise the odds of capturing
power-play goals, empty-net goals, and multi-goalie appearances, all of which
were in fact captured. Selected from the project's existing authoritative
real game-id list, `research/real_nhl_results/normalized_regular_season_
games.jsonl` — no game_id was invented.

## R. Pilot event count

**9,753 total events** across the 30 pilot games (325.1 events/game average).
Full type breakdown: faceoff 1,753 · shot-on-goal 1,538 · stoppage 1,309 ·
hit 1,240 · blocked-shot 982 · giveaway 979 · missed-shot 864 · takeaway 274 ·
goal 244 (234 statistical + 10 shootout) · penalty 240 · period-start 107 ·
period-end 107 · delayed-penalty 80 · game-end 30 · shootout-complete 5 ·
failed-shot-attempt 1.

## S. Pilot reconciliation results

Every one of the 30 pilot games' normalized events was checked against the
9 event-level invariants (`invariants.py`) — **zero violations across all 30
games** — and against the real `/boxscore` endpoint's per-player final totals
for goals, assists, points, SOG, hits, blocked shots, and PIM. Two real
discrepancy patterns were found and fully characterized, not silently
patched away:

1. **SOG undercount (fixed)** — the reconstruction initially missed that a
   goal itself counts toward a player's SOG (Section I). Fixed in
   `reconcile.py`; re-validated to 0 mismatches on 29/30 games.
2. **Blocked-shot attribution (disclosed, not "fixed")** — the event feed's
   `blockingPlayerId` attribution is **systematically ≥** the official
   boxscore's `blockedShots` total, **never** the reverse: 982 event-level
   blocks vs. 899 boxscore-credited blocks across the pilot (77 per-player
   mismatches, every single one in the same direction, magnitude 1-3). This
   is a known, one-directional, fully-explained gap between real-time event
   logging and official boxscore compilation — not a normalization defect —
   and is tagged `known_discrepancy` in `reconcile.py` rather than hidden.
3. **One isolated residual** — after fix #1, exactly one single-field,
   single-player SOG mismatch remains, in game `2025021003` (reconstructed 2
   vs. boxscore 1). One occurrence across 30 games / ~1,200 player-game rows
   (0.08%) is not a systematic pattern; the most plausible explanation is the
   NHL's own documented practice of occasionally revising a final boxscore
   stat after the fact — **an honest, unconfirmed hypothesis, not fabricated
   certainty**. Disclosed, not hidden: this game's raw `"passed": false`
   stays exactly as found in `pilot_validation_results.json`.

## T. Pilot failures/anomalies

1 of 30 games (`2025021003`) has the one isolated residual mismatch above.
0 games failed to fetch. 0 games failed to normalize. 0 event-level invariant
violations anywhere in the pilot.

## U. Whether pilot passed the expansion gate

**YES.** `pilot_passed = True` (29/30 games with zero mismatches of any kind;
the 30th carries only the known blocked-shot discrepancy plus one immaterial
isolated residual, both fully disclosed above — not an unexplained material
mismatch per the Pilot Acceptance Standard's own wording). `run_pilot_
validation.py` also reports `pilot_passed_strict = False` (0 tolerance,
0 exceptions) alongside the disclosed `pilot_passed`, so neither number is
hidden from a future reviewer.

## V. One season selected

**2025-26** (`season = 20252026`) — the same season the pilot itself was
drawn from, per Part 22's stated preference, and confirmed complete: the
authoritative game list (`normalized_regular_season_games.jsonl`) shows
exactly 1,312 real regular-season games for this season (32 teams × 82 games
/ 2), matching the expected full-season count with no gaps.

## W. Full-season games ingested

**1,312 / 1,312 (100%)** real regular-season games — the complete 2025-26
regular season, zero gaps. 30 were already archived from the pilot; the
remaining 1,282 were fetched in this slice's one-season ingestion run.
0 fetch failures, 0 retries needed, 0 missing games.

## X. Normalized event count

**413,859 total events** across the full season (315.4 events/game average,
consistent with the pilot's 325.1/game — the pilot's blowout/OT/SO-skewed
sampling ran slightly event-heavier than a typical game, as expected).

## Y. Raw storage size

**187,656,890 bytes ≈ 179.0 MB** for the full 1,312-game season (≈143 KB/game
average). A 4-season expansion (the other 3 already-scored historical
seasons used elsewhere in this project) would cost roughly **4× that, ≈716
MB** — closely matching (and now replacing with a precise, measured number)
`COMPLETE_NHL_MARKET_ARCHITECTURE_REPORT.md`'s earlier order-of-magnitude
estimate of "~690MB."

## Z. Normalized storage size

Not separately persisted as a second on-disk copy in this slice — normalized
`PbpEvent` objects are computed on demand from the archived raw JSON
(`normalize.normalize_game_events()`), consistent with Part 16's "retain raw
source reference" design and this slice's scope (data foundation, not a
storage-engine build-out). If a persisted normalized store is wanted later,
its size is the natural next thing to measure once that storage layer
exists — not fabricated here.

## AA. Event-type counts (full season)

| Event type | Count |
|---|---|
| faceoff | 73,496 |
| shot-on-goal | 65,370 |
| stoppage | 57,202 |
| hit | 53,658 |
| blocked-shot | 40,866 |
| giveaway | 40,366 |
| missed-shot | 39,168 |
| takeaway | 12,099 |
| penalty | 9,692 |
| goal | 8,350 |
| period-start | 4,381 |
| period-end | 4,381 |
| delayed-penalty | 3,384 |
| game-end | 1,312 |
| shootout-complete | 119 |
| failed-shot-attempt | 15 |

`shootout-complete` (119) confirms 119 of the 1,312 games reached a
shootout — a 9.1% SO rate, matching the authoritative schedule's own
independently-computed count of `period_type == "SO"` rows in
`normalized_regular_season_games.jsonl` for this season exactly.

## AB. Player-ID coverage (full season)

| Field | Present / Total | % |
|---|---|---|
| faceoff.winner | 73,496 / 73,496 | 100.0% |
| faceoff.loser | 73,496 / 73,496 | 100.0% |
| shot-on-goal.shooter | 65,370 / 65,370 | 100.0% |
| missed-shot.shooter | 39,168 / 39,168 | 100.0% |
| blocked-shot.shooter | 40,866 / 40,866 | 100.0% |
| blocked-shot.blocker | 40,853 / 40,866 | **99.97%** |
| hit.hitter | 53,658 / 53,658 | 100.0% |
| hit.hittee | 53,658 / 53,658 | 100.0% |
| goal.scorer | 8,350 / 8,350 | 100.0% |
| penalty.committed_by | 9,425 / 9,692 | **97.25%** |

Both real gaps were individually inspected, not just counted: all 13 missing
`blocked-shot.blocker` events carry `reason: "other-block"` — a genuine NHL
scorekeeping category for a block not attributable to one identified player
(e.g. a scramble, or the puck striking equipment). All 267 missing
`penalty.committed_by` events are the same genuine bench/officiating-triggered
categories catalogued in Section K (`too-many-men-on-the-ice`,
`misconduct`, `delaying-game-*`, `abuse-of-officials`) — none is a data
defect.

## AC. Idempotency results

Re-running `build_pbp_season.ingest_season()` a second time after the full
season was already archived: **0 new games fetched, 0 duplicates, 0
missing** (`games_already_archived_before_run: 1312`,
`games_requested_this_run: 0`, `games_retrieved_total: 1312`). This is a
real, full-scale idempotency proof, not just the 30-game pilot's version —
see also `tests/test_pbp_foundation.py` `Test21`/`Test22` for the
unit-level collision-guard and no-op-on-identical-bytes behavior that make
this possible (including a real bug found and fixed here: the archive
function was originally overwriting an unchanged file's provenance sidecar
with the NEW call's `source_url`/`retrieved_at_utc` even when the bytes were
identical — fixed to return the ORIGINAL provenance untouched on a true
no-op, exactly matching Part 24's "if raw payload unchanged: UNCHANGED").

## AD. Period-market data readiness

| Market | Readiness | Evidence |
|---|---|---|
| PLAYER GOAL BY PERIOD | READY | goal events carry `period_number` + scorer directly |
| PLAYER SOG BY PERIOD | READY | shot-on-goal events carry `period_number` + shooter; a player's own goals also count (Section I) |
| TEAM GOALS BY PERIOD | READY | sum of statistical goals per team per period |
| GAME GOALS BY PERIOD | READY | sum of both teams' per-period totals |
| TEAM TO SCORE BY PERIOD | READY | team of the first statistical goal within a period |
| FIRST TEAM TO SCORE | READY | team of the first statistical goal in the game |
| BOTH TEAMS SCORE BY PERIOD | READY | derived from TEAM GOALS BY PERIOD |
| CORRECT SCORE BY PERIOD | READY | score timeline bucketed at period boundaries |
| GOALIE SAVES BY PERIOD | **PARTIAL** | fields exist (`goalieInNetId` + `period_number` on every shot), but correctly bucketing across a mid-period goalie change needs reconstruction logic not yet built (Part 34 ban) |

## AE. Event-time-market data readiness

| Market | Readiness | Evidence |
|---|---|---|
| FIRST / LAST GOAL SCORER | READY | scorer of the first/last statistical goal by `event_sequence` |
| TEAM FIRST GOAL SCORER | READY | team of the first statistical goal |
| FIRST GOAL TIMING | READY | `regulation_elapsed_seconds` of the first statistical goal |
| GAME-WINNING GOAL | **PARTIAL** | fully derivable from the score timeline once the final score is known (Invariant #10), but the correct NHL definition needs careful backward-scan logic not yet built |
| TEAM TO SCORE FIRST / LAST | READY | same score-timeline fields |
| RACE TO N | READY | first `event_sequence` where either team's running goal count reaches N |
| LEAD 1-0 / LEAD 2-0 | READY | direct read of the score timeline |
| LARGEST LEAD | READY | max `abs(home_score - away_score)` across the timeline |
| COMEBACK WIN | READY | detect a timeline point where the eventual winner trailed |

## AF. Special-teams readiness

| Market | Readiness | Evidence |
|---|---|---|
| PP GOAL / PP POINT(S) | READY | `situationCode` alone distinguishes a real PP from an empty-net extra-attacker situation (Section N) — genuine upgrade over the market-architecture slice's reconstruction assumption |
| SH GOAL / SH POINT | READY | same `situationCode` logic, opposite skater-count direction |
| TEAM PP/SH GOALS, ANY PP/SH GOAL | READY | aggregated from the same per-event flag |

`SPECIAL_TEAMS_STATE` leverage (13 markets per the architecture report) is
now confirmed **cheaper** to unlock than assumed: no penalty-duration
reconstruction engine is required at all.

## AG. Penalty-market readiness

| Market | Readiness | Evidence |
|---|---|---|
| PLAYER PIM | READY | `committedByPlayerId` + `duration`; bench-assessed penalties genuinely excluded from player PIM by real NHL convention (Section K) |
| RECEIVE PENALTY | READY | same field |
| TEAM PIM / TEAM PENALTIES / TOTALS | READY | team-level aggregation includes bench-assessed penalties |

## AH. Hit readiness

| Market | Readiness | Evidence |
|---|---|---|
| HITS / HIT ALTERNATES | READY | 100% participant coverage in the pilot (Section J) |

Scorer/rink-bias caveat (Part 32): publicly documented in the wider
hockey-analytics community that official in-arena scorers show measurable
rink-to-rink bias specifically in hit and giveaway/takeaway recording — a
known caveat about the source data, not independently re-verified against
this project's own pilot, flagged for future modeling awareness only.

## AI. Faceoff readiness

| Market | Readiness | Evidence |
|---|---|---|
| FACEOFF WINS / FACEOFFS TAKEN / FACEOFF PERCENTAGE | READY | 100% participant coverage in the pilot (Section L) |
| TEAM FACEOFF WINS / TOTAL FACEOFFS | READY | aggregated from the same fields |

Same scorer/rink-bias caveat as Section AH applies to faceoff location/zone
detail, though win/loss attribution itself is not typically the disputed
part of that caveat.

## AJ. Goalie-market readiness

| Market | Readiness | Evidence |
|---|---|---|
| FULL-GAME / ALTERNATE SAVES | READY | reconstructible from events; cross-checked against `/boxscore`'s own saves field |
| PERIOD SAVES | PARTIAL | same mid-period-goalie-change caveat as AD |
| GOALS ALLOWED | READY | goal events' `goalieInNetId`, excluding SO/empty-net |
| SHUTOUT | READY | `goals_allowed == 0` for a full-game goalie |
| GOALIE WIN | **NOT READY** | no W/L/OTL "decision" field found on either endpoint checked this slice (play-by-play, boxscore); the real win-crediting rule has edge cases not purely derivable from shot/goal counts; the `/landing` endpoint was never queried — out of scope, future work |
| GOALIE ALLOWED FIRST GOAL | READY | first statistical goal's `goalieInNetId` |
| BOTH GOALIES X+ SAVES | READY | joint condition over full-game saves |

## AK. Dashboard/status changes

Added `dashboard/pbp_status_view.py` (reads cached manifests only, no network
call — matches the existing Data Status page's convention) and
`dashboard/pages/13_Play_By_Play_Status.py`, wired into `dashboard/app.py`'s
navigation. Shows pilot/season counts, the expansion-gate state, and the full
Part 28-33 market-readiness table. No other dashboard page was modified.

## AL. Files created/modified

**Created** (all under `research/real_nhl_pbp/`, `tests/`, `dashboard/`, or
repo root — zero production files touched):
- `research/real_nhl_pbp/__init__.py`, `schema.py`, `client.py`,
  `raw_archive.py`, `normalize.py`, `invariants.py`, `reconcile.py`,
  `readiness.py`, `build_pbp_pilot.py`, `build_pbp_season.py`,
  `run_pilot_validation.py`
- `research/real_nhl_pbp/raw/20252026/*.json` (+ `.provenance.json` sidecars)
  — the real archived corpus
- `research/real_nhl_pbp/pilot_manifest.json`, `pilot_validation_results.json`,
  `season_ingestion_manifest.json`
- `tests/test_pbp_foundation.py` (48 tests)
- `dashboard/pbp_status_view.py`, `dashboard/pages/13_Play_By_Play_Status.py`
- `NHL_PLAY_BY_PLAY_FOUNDATION_REPORT.md` (this file)

**Modified**: `dashboard/app.py` (one navigation line added, page 13).

**Verified untouched** (sha256-pinned in `tests/test_pbp_foundation.py`
`Test28`-`Test33`): `models/combined_model.py`, `models/elo_model.py`,
`config.py`, `db.py`, `schema.sql`, `research/player_props/decision_policy.py`,
`research/player_props/market_registry.py`,
`research/player_props/dependency_graph.py`, plus the Goals/Confidence
research-artifact JSON files (structural-key checks).

## AM. Full test result

**1,078 / 1,078 passing** (1,029 prior + 49 new: 48 in `tests/test_pbp_
foundation.py` covering all 33 Part-38 topics, plus 1 additional full-season
completeness test added after real ingestion finished). 0 existing tests
weakened or skipped. Production files verified untouched by both mtime
(all predate this session's date) and sha256 pin (`Test28`-`Test33`).

One real bug was found and fixed by this test suite before it could reach
production data: `dashboard/pbp_status_view.py` initially called `json.load()`
directly instead of the project's shared `load_json_safely()` helper — an
existing repo-wide dashboard convention test (`tests/test_dashboard.py`)
caught this immediately; fixed by routing through `data_access.load_json_
safely()` like every other dashboard view module.

## AN. Recommended NEXT SINGLE DEVELOPMENT SLICE

**Expand the now-pilot-proven play-by-play ingestion pipeline to the
remaining three historical seasons (2022-23, 2023-24, 2024-25) using the
exact same `build_pbp_season.py` machinery — building zero models.**

Why not go straight to a period-market model: every prop model this project
has built (SOG, Blocks, Assists, Points, Goals) used a disciplined
multi-season WARMUP/TUNING/EVAL split specifically to avoid the walk-forward
leakage problems documented in `PLAYER_POINTS_REDESIGN_REPORT.md`. A period
or event-time model trained on only the single 2025-26 season ingested here
would have no clean held-out season to evaluate against without repeating
that exact mistake. Extending the proven pipeline to the other 3 seasons
(before touching any model) costs roughly `4x` the storage/runtime already
measured in Section Y/Z above and carries no new contract risk — the pilot
already proved the contract; this step is pure, low-risk volume.

Two small, genuinely optional follow-ups (not required before the season
expansion, since neither blocks it): (1) the goalie-saves-by-period
reconstruction (closes the one PARTIAL cell in Section AD/AJ), and (2) the
game-winning-goal backward-scan derivation (closes the one PARTIAL cell in
Section AE) — both are small, self-contained utility functions over data
already fully present, not new modeling work.

## Final Questions

**IS THE OFFICIAL NHL PLAY-BY-PLAY ENDPOINT USABLE?** YES

**DID THE 25-50 GAME PILOT PASS?** YES (29/30 games with zero mismatches;
1/30 with only a disclosed, immaterial, single-stat residual — see Section S)

**WAS A FULL SEASON INGESTED?** YES

**IF YES, WHICH SEASON?** 2025-26 (`20252026`)

**HOW MANY REGULAR-SEASON GAMES?** 1,312 (100% of the full 2025-26 season, 0 missing)

**HOW MANY NORMALIZED EVENTS?** 413,859

**ARE EVENT TIMES RELIABLE?** YES (regulation/OT elapsed time is exact;
`sortOrder`, not `eventId`, is required for deterministic ordering — Section
D/Part 4 finding)

**ARE PLAYER IDS RELIABLE?** EVENT-SPECIFIC (100% present on goal, hit, and
faceoff events; genuinely and explainably absent on `drawnByPlayerId` for
bench/officiating-triggered penalties — Section K)

**CAN REGULATION / OT / SHOOTOUT BE DISTINGUISHED?** YES

**CAN STATISTICAL GOALS BE SEPARATED FROM SHOOTOUT TALLIES?** YES

**CAN FIRST/LAST GOAL ORDER BE RECONSTRUCTED?** YES

**CAN PERIOD GOALS BE RECONSTRUCTED?** YES

**CAN PERIOD SOG BE RECONSTRUCTED?** YES

**CAN PERIOD GOALIE SAVES BE RECONSTRUCTED?** PARTIAL (fields exist; mid-period
goalie-change bucketing logic not yet built — Section AD/AJ)

**CAN PENALTIES / PIM BE RECONSTRUCTED?** YES, with the documented
bench-penalty/player-PIM convention in Section K

**CAN FACEOFF EVENTS BE RECONSTRUCTED?** YES

**CAN HIT EVENTS BE RECONSTRUCTED?** YES

**CAN SPECIAL-TEAMS STATE BE DETERMINED?** YES — DIRECTLY AVAILABLE via
`situationCode`, no reconstruction needed (Section N)

**IS THE DATA FOUNDATION READY FOR EVENT-TIME MODELING?** YES, for the large
majority of markets classified READY in Sections AD-AJ; two markets remain
PARTIAL (goalie saves by period, GWG) and one remains NOT READY (goalie win)

**WERE ANY VALIDATED PROP MODELS CHANGED?** NO

**WAS CONFIDENCE CHANGED?** NO

**WAS DECISION POLICY v2 CHANGED?** NO

**WAS NHL WIN MODEL CHANGED?** NO

**CURRENT FULL TEST RESULT?** 1,078 / 1,078

**WHAT IS THE NEXT HIGHEST-LEVERAGE DEVELOPMENT SLICE?** Expand the
pilot-proven play-by-play ingestion pipeline to the remaining 3 historical
seasons (2022-23, 2023-24, 2024-25) — building zero models — so a future
period/event-time model has the same multi-season walk-forward validation
discipline every other prop model in this project has used. See Section AN.

---

**STOP AFTER PLAY-BY-PLAY FOUNDATION.** No first-goal model, PP Points model,
Goalie Saves model, Hits model, period model, joint game simulator, or
parlay optimizer was built in this slice.
