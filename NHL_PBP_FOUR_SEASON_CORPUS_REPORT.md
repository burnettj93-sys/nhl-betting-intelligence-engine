# NHL Play-by-Play — Complete 4-Season Event-Timing Research Corpus

This slice expands the single-season (2025-26) play-by-play foundation built
and gated in `NHL_PLAY_BY_PLAY_FOUNDATION_REPORT.md` to all four regular
seasons this project's other research already covers: 2022-23, 2023-24,
2024-25, 2025-26. It reuses the exact, unmodified normalization pipeline
from that slice — no season-specific parsing fork — after a real
contract-drift audit confirmed every prior finding holds identically across
history. **Zero new betting models were built.**

---

## A. Seasons covered

**2022-23, 2023-24, 2024-25, 2025-26** (`20222023`, `20232024`, `20242025`,
`20252026`) — matching this project's existing historical research window
exactly (the same 4 seasons `research/real_nhl_results/normalized_regular_
season_games.jsonl` already covers).

## B. Expected games per season

**1,312 per season**, confirmed against the accepted results corpus, not
assumed: `research/real_nhl_results/normalized_regular_season_games.jsonl`
shows exactly 1,312 `gameType == 2` rows for each of the four seasons
(32 teams × 82 games / 2). Expected 4-season total: **5,248 games**.

## C. Retrieved games per season

| Season | Retrieved | Expected |
|---|---|---|
| 2022-23 | 1,312 | 1,312 |
| 2023-24 | 1,312 | 1,312 |
| 2024-25 | 1,312 | 1,312 |
| 2025-26 | 1,312 | 1,312 (reused, unchanged — Part 1) |

One transient failure was encountered and fully resolved during ingestion:
5 consecutive 2024-25 games (`2024020070`-`2024020074`) failed with a DNS
resolution error on first attempt — a classic transient network blip, not a
real 404 or contract problem (Part 7's own explicit distinction). All 5
were successfully retrieved on retry once network stability was confirmed;
final coverage is 100% with 0 unexplained gaps.

## D. Normalized games per season

**1,312 / 1,312 for every season** — every archived raw payload normalizes
successfully via the exact, unmodified `normalize.py` pipeline. 0 games
failed normalization.

## E. Total games

**5,248 / 5,248 (100%)**.

## F. Total events

**1,656,340** normalized events across all 4 seasons.

## G. Event counts by season

| Event type | 2022-23 | 2023-24 | 2024-25 | 2025-26 |
|---|---|---|---|---|
| faceoff | 74,659 | 74,188 | 74,296 | 73,496 |
| shot-on-goal | 74,064 | 71,715 | 66,571 | 65,370 |
| hit | 59,077 | 59,648 | 56,293 | 53,658 |
| stoppage | 57,260 | 57,734 | 58,684 | 57,202 |
| blocked-shot | 38,615 | 44,677 | 43,877 | 40,866 |
| missed-shot | 32,195 | 35,463 | 38,104 | 39,168 |
| giveaway | 22,656 | 20,233 | 39,364 | 40,366 |
| takeaway | 19,383 | 18,379 | 12,628 | 12,099 |
| penalty | 10,063 | 10,008 | 9,011 | 9,692 |
| goal | 8,474 | 8,268 | 8,070 | 8,350 |
| period-start | 4,334 | 4,290 | 4,285 | 4,381 |
| period-end | 4,333 | 4,290 | 4,284 | 4,381 |
| delayed-penalty | 2,185 | 3,376 | 3,210 | 3,384 |
| game-end | 1,312 | 1,312 | 1,312 | 1,312 |
| shootout-complete | 95 | 82 | 77 | 119 |
| failed-shot-attempt | 27 | 13 | 7 | 15 |

Two real season-over-season trends worth disclosing (neither claimed as
causal — Part 20's own caution applied consistently here too): `giveaway`
counts nearly double from 2022-23 (22,656) to 2025-26 (40,366) while
`takeaway` counts fall by roughly a third over the same span; `hit` counts
decline by about 9% from 2022-23 to 2025-26. These could reflect real
rule/style-of-play changes, or a scorekeeping-convention shift — this
slice does not investigate which, consistent with "do not call them causal
without further analysis."

## H. Event vocabulary differences (historical contract-drift audit)

Before committing to bulk ingestion, 12 real games (3 per historical season
— 2 regulation, 1 OT, 1 shootout, deterministically sampled) were fetched
live from 2022-23, 2023-24, and 2024-25 and compared field-by-field against
every accepted 2025-26 finding. **Every finding held identically**:

- `sortOrder` monotonic and unique; `eventId` **non-monotonic in all 12/12**
  historical sample games — the exact same finding as 2025-26's 30/30.
- Event-type vocabulary is **byte-identical** to 2025-26's 16 types,
  including `failed-shot-attempt` (already present historically, not a
  2025-26-only addition) — confirmed again at full corpus scale: every
  type in the Section G table above appears in every one of the 4 seasons,
  just at different real frequencies.
- `periodDescriptor` keys are the same 3-key shape in every historical
  sample game — no period-length field existed historically either.
- Real (non-SO) goals carry `scoringPlayerTotal` in 100% of the historical
  sample; SO goals carry it in 0% — identical to 2025-26.
- `situationCode` is always a 4-character string in every historical sample
  event (see Section J for the one real historical coverage footnote).
- Blocked-shot (`shooter`+`blocker`), hit (`hitter`+`hittee`), and faceoff
  (`winner`+`loser`) fields are 100% present in every historical sample
  game — same as 2025-26.
- The same bench-penalty `committed_by`-absent pattern (`too-many-men-on-
  the-ice`, etc.) appears historically too.
- Both real empty-net goals found in the historical sample satisfy the
  joint two-signal rule (missing `goalieInNetId` **and** `situationCode`
  goalie digit `"0"`).
- SOG+goal reconciliation (Section I of the single-season report) was
  re-verified against a real `2022-23` game's `/boxscore`: **0 mismatches**.
- Shootout score-freeze semantics (pre-SO score preserved on the SO goal
  event) confirmed on real SO games in 2022-23, 2023-24, and 2024-25.

**One genuine, benign difference found**: an optional top-level
`specialEvent` key appears on special outdoor/showcase games (e.g. the 2024
NHL Stadium Series, game `2023020859`, and the 2022 NHL Global Series
opener, game `2022020001`) — pure metadata (`{"parentId":..., "name":
{"default": "2024 Navy Federal Credit Union NHL Stadium Series"}}`), never
present on ordinary games, and already handled correctly without any code
change because `normalize_game()`/`normalize_event()` never assume an
exhaustive top-level key set. **No narrow normalization rule was needed —
the existing pipeline already tolerates this variant.** Regression tests
using both real games are included (Part 41 topic 3).

**One more real, narrow historical variant, found only via the full-corpus
scan** (not the 12-game sample): `situationCode` coverage on `game-end`
events differs by season — see Section J. Inconsequential (no market
depends on `game-end`'s manpower state), and again required no
normalization change since the field was already nullable.

**Conclusion**: no historical contract variant required forking or
rewriting any normalization logic. Part 3's instruction to reuse the exact
accepted pipeline was honored because the evidence supported it, not by
default.

## I. Player-ID coverage (all 4 seasons combined)

| Field | Present / Total | % |
|---|---|---|
| faceoff.winner / loser | 296,639 / 296,639 | 100.0% |
| shot-on-goal.shooter | 277,720 / 277,720 | 100.0% |
| blocked-shot.shooter | 168,035 / 168,035 | 100.0% |
| blocked-shot.blocker | 168,002 / 168,035 | 99.98% |
| hit.hitter / hittee | 228,676 / 228,676 | 100.0% |
| goal.scorer | 33,162 / 33,162 | 100.0% |
| penalty.committed_by | 37,648 / 38,774 | 97.1% |

Every gap is the same genuine, already-explained category found in the
single-season report (bench/officiating-triggered penalties; the
`other-block` reason code) — re-confirmed at 4-season scale, not new.

## J. situationCode coverage

| Season | Coverage | Notes |
|---|---|---|
| 2022-23 | 407,441 / 408,732 = **99.68%** | see below |
| 2023-24 | 413,669 / 413,676 = 100.0% | |
| 2024-25 | 420,073 / 420,073 = 100.0% | |
| 2025-26 | 413,859 / 413,859 = 100.0% | |

**One real historical variant found**: all 1,291 missing 2022-23
`situationCode` values are on `game-end` events (1,291 of that season's
1,312 `game-end` events), spread across the entire season (2022-10-07
through 2023-04-14), not clustered in any date range. Every later season's
`game-end` events carry a `situationCode` value. This is inconsequential
for every market classification in this report — `game-end` carries no
game-state information any market depends on — and required no
normalization change, since `situation_code` was already nullable in
`schema.py`. Format shape is 100% valid (always exactly 4 characters)
everywhere it is present, in every season.

## K. Empty-net coverage

| Season | Statistical goals | Empty-net goals | % |
|---|---|---|---|
| 2022-23 | 8,248 | 438 | 5.31% |
| 2023-24 | 8,086 | 447 | 5.53% |
| 2024-25 | 7,901 | 524 | 6.63% |
| 2025-26 | 8,086 | 508 | 6.28% |
| **Total** | **32,321** | **1,917** | **5.93%** |

Every one of the 1,917 empty-net goals across all 4 seasons was confirmed
to satisfy the joint two-signal rule exactly, and cross-validates a second,
independent way: of the 33,162 total goal events (32,321 statistical + 841
shootout), 31,245 carry a `goalie` role (Section V) — all 841 shootout
goals plus 30,404 statistical goals. `32,321 − 30,404 = 1,917`, an exact
match to the empty-net count above.

## L. Shootout coverage

| Season | SO games | SO attempts | SO goals |
|---|---|---|---|
| 2022-23 | 95 | 670 | 226 |
| 2023-24 | 82 | 617 | 182 |
| 2024-25 | 77 | 513 | 169 |
| 2025-26 | 119 | 810 | 264 |
| **Total** | **373** | **2,610** | **841** |

373 total shootout games across 4 seasons matches this project's own
independently-computed `period_type == "SO"` count in
`normalized_regular_season_games.jsonl` exactly. Every SO game's
statistical score was confirmed frozen at the pre-shootout value (Section
G of the single-season report), reconciled against the official result
corpus with 0 unexplained differences (Section M).

## M. Score reconstruction

Reconstructed statistical score matches the official final score exactly
for every REG/OT game reconciled, and is exactly one shootout-bonus-goal
below the official score for exactly one team on every SO game reconciled
— **0 unexplained material mismatches** across all reconciliation sampling
(Section Q below) and the full invariant sweep (0 violations across every
sampled game in every season).

## N. Goal reconciliation

0 goal-count mismatches against the real `/boxscore` endpoint across the
full reconciliation sample (120 games, 30/season) in all 4 seasons.

## O. Assist reconciliation

0 assist-count mismatches; 0 goals found anywhere in the 4-season corpus
with an `assist2` role present without `assist1`; 0 goals with a third
assist field. No assist was ever invented for a goal the feed supplied
fewer than two for (Part 17's explicit instruction) — `extract_players()`
only ever reads keys the feed actually supplies.

## P. SOG reconciliation

0 SOG mismatches across the full reconciliation sample once the accepted
fix (a real goal also counts as a shot on goal, Section I of the
single-season report) is applied — re-confirmed in every one of the 4
seasons' samples, not just 2025-26's.

## Q. Blocked-shot attribution gap across all four seasons

**Mandatory finding — the gap is NOT constant across history:**

| Season | PBP-derived blocks | Official boxscore blocks | Absolute gap | Relative gap |
|---|---|---|---|---|
| 2022-23 | 829 | 829 | **0** | **0.0%** |
| 2023-24 | 1,015 | 942 | 73 | 7.75% |
| 2024-25 | 1,015 | 940 | 75 | 7.98% |
| 2025-26 | 982 | 899 | 83 | 9.23% |

(30-game stratified sample per season, identical methodology to the
original single-season pilot — not the full corpus, per Part 6's "do not
hammer the API" and Part 16/17's "where feasible" wording.)

**The gap did not always exist.** 2022-23's sample shows an EXACT match (0
games with any block-count mismatch, out of 30) — genuinely different in
kind from the other three seasons, which all show a consistent ~8-9%
systematic, one-directional gap (event feed ≥ boxscore, never the
reverse — confirmed again this slice). This strongly suggests the gap was
**introduced by a real change starting in the 2023-24 season** — either in
how the live play-by-play feed attributes a block, or in how the official
boxscore compiles the `blockedShots` stat, or both. This slice does not
determine which side of that pipeline changed, or why — only that the
transition is real, dated, and season-specific, not present since day one
and not something to calibrate away. **Preserved exactly as found**, not
"corrected": `reconcile.py`'s `known_discrepancy` tag remains a disclosure
mechanism, never a silent overwrite of either the PBP or boxscore number.

## R. Hit coverage

| Season | Total hits |
|---|---|
| 2022-23 | 59,077 |
| 2023-24 | 59,648 |
| 2024-25 | 56,293 |
| 2025-26 | 53,658 |

100% hitter+hittee field coverage in every season (Section I).

## S. Rink-level hit variation summary

Across all 4 seasons and 34 real team identities (32 current franchises
plus 2 historical team_ids from a mid-window relocation/rename, confirmed
against the results corpus, not treated as a data error), home-game hit
totals (both teams combined, in games where a team is host) range from
**35.7 to 54.7 hits per home game** — a real, measured **1.53×** spread
between the highest- and lowest-hit home arenas, mean 43.6, stdev 4.6.
Consistent with (not proof of) the publicly documented rink-scorer-bias
caveat already flagged in the single-season report's Section AH — **not
claimed as causal**; team style-of-play differences are an equally
plausible explanation and this slice does not attempt to separate them.

## T. Penalty coverage

**38,774 total penalty events** across 4 seasons. 1,126 (2.9%) lack a
`committed_by` player — exclusively the same genuine bench/officiating
categories catalogued in the single-season report (`too-many-men-on-the-
ice`, `misconduct`, `delaying-game-*`, `abuse-of-officials`), confirmed
again at 4-season scale. Team-level PIM/penalty totals are unaffected
(`eventOwnerTeamId` always present). **The 4-season corpus is sufficient**
for Player PIM, Receive Penalty, Team PIM, Team Penalties, Total PIM, and
Total Penalties (all READY, Section Z).

## U. Faceoff coverage

**296,639 total faceoff events**, 100% winner+loser coverage in every
season. **The corpus is data-ready** for historical player and team
faceoff markets (Section AB).

## V. Goalie event coverage

| Field | Present / Total | % |
|---|---|---|
| shot-on-goal events with goalie identity | 277,720 / 277,720 | 100.0% |
| goal events with goalie identity | 31,245 / 33,162 | 94.22% (gap = exactly the 1,917 empty-net goals, Section K) |

**Multi-goalie games** (a real in-net goalie change, not just an
empty-net pull — confirmed via `situationCode`'s own goalie-in-net digit
staying `"1"` across two distinct `goalieInNetId` values for one team):

| Season | Multi-goalie games | % of season |
|---|---|---|
| 2022-23 | 152 | 11.6% |
| 2023-24 | 164 | 12.5% |
| 2024-25 | 136 | 10.4% |
| 2025-26 | 140 | 10.7% |
| **Total** | **592** | **11.3%** |

**Ready** for Goalie Saves, Alternate Saves, Goals Allowed, Shutout
(Section AC); **partial** for Period Saves (mid-period goalie-change
bucketing not yet built, Part 34 ban).

## W. Period-market data readiness

| Market | Readiness |
|---|---|
| Player Goal by Period | READY |
| Player SOG by Period | READY |
| Player Point by Period | READY |
| Team Goals by Period | READY |
| Game Goals by Period | READY |
| Team to Score by Period | READY |
| First Team to Score | READY |
| Both Teams Score by Period | READY |
| Correct Score by Period | READY |
| Goalie Saves by Period | **PARTIAL** (fields exist; mid-period goalie-change bucketing not built) |
| Period Winning Margin | READY |
| Highest-Scoring Period | READY |

11 READY, 1 PARTIAL. Every verdict reconfirmed identical across all 4
seasons via the Section H contract audit — none is 2025-26-only.

## X. Event-time-market data readiness

| Market | Readiness |
|---|---|
| First / Last Goal Scorer | READY |
| Team First Goal Scorer | READY |
| First Goal Timing | READY |
| First Goal Method | READY |
| Game-Winning Goal | **PARTIAL** (derivable once final score known; backward-scan logic not built) |
| Team to Score First / Last | READY |
| Race to 1 / 2 / 3 / 4 | READY |
| Lead 1-0 / 2-0 | READY |
| Largest Lead | READY |
| Lead After Every Period | READY |
| Come-from-Behind Win | READY |

12 READY, 1 PARTIAL.

## Y. Special-teams readiness

All READY: PP Goal/Point(s), SH Goal/Point, Team PP/SH Goals, Any PP/SH
Goal, Total PP Goals. `situationCode` alone (Section N) distinguishes a real
power play from an empty-net extra-attacker situation — **no penalty-
duration reconstruction is required**, a genuine cost reduction versus the
single-season report's working assumption, now confirmed across all 4
seasons.

## Z. Penalty-market readiness

All READY: Player PIM, Receive Penalty, Team PIM/Penalties, Total
PIM/Penalties. Bench-assessed penalties (`too-many-men-on-the-ice`, etc.)
carry no individual `committed_by` player and are NOT credited to any
skater's personal PIM by real NHL convention (confirmed via 0 PIM
mismatches in the single-season pilot and present in every historical
season's sample too).

## AA. Hit-market readiness

READY: Hits / Hit Alternates. 100% hitter+hittee field coverage confirmed
in every historical sample game, matching the single-season corpus's
53,658/53,658 (100%).

## AB. Faceoff-market readiness

READY: Faceoff Wins / Faceoffs Taken / Faceoff Percentage / Team Faceoff
Wins / Total Faceoffs. 100% winner+loser field coverage confirmed in every
historical sample game, matching the single-season corpus's 73,496/73,496
(100%).

## AC. Goalie-market readiness

| Market | Readiness |
|---|---|
| Full-Game / Alternate Saves | READY |
| Period Saves | PARTIAL |
| Goals Allowed | READY |
| Shutout | READY |
| Goalie Win (official decision credit) | **NOT READY** — no W/L/OTL field on either endpoint checked |
| Goalie Allowed First Goal | READY |
| Both Goalies X+ Saves | READY |

5 READY, 1 PARTIAL, 1 NOT READY — unchanged from the single-season report;
the /landing endpoint remains unqueried (out of scope) and may carry the
missing decision field.

## AD. Game-state reconstruction readiness

**`GAME_STATE_RECONSTRUCTION_READY: YES`** — all 5 components (period, game
clock, home/away score, manpower state, goalie-present state) are READY.
No simulator was built (Part 39); this is strictly a data-sufficiency
assessment for a future one.

## AE. OT/shootout readiness

All 5 READY: OT yes/no, Shootout yes/no, Method of Victory, Exact Score,
Winning Margin. `gameOutcome.lastPeriodType`/`otPeriods` shape confirmed
byte-identical across all 4 seasons (Section H).

## AF. Raw storage size

**746,461,392 bytes ≈ 711.8 MB** for the complete 4-season raw archive
(5,248 games). Per season: 181.9 MB (2022-23) / 187.0 MB (2023-24) / 189.9
MB (2024-25) / 187.7 MB (2025-26) — closely matching, and now replacing
with an exact measured number, the single-season report's ≈4× projection
of ≈716 MB.

## AG. Normalized storage size

**612,093,952 bytes ≈ 583.8 MB** — the unified SQLite research store
(`research/real_nhl_pbp/research_pbp.db`), covering all 4 seasons: 5,248
games, 1,656,340 events, 2,609,945 player-role rows. Built in **22.26
seconds** from the already-archived raw corpus (no network calls).

## AH. Ingestion/runtime

Real wall-clock window across every archived game's `retrieved_at_utc`,
this slice: **2026-08-28T12:45:24Z → 2026-08-28T14:42:44Z** (≈1h57m,
including this slice's own investigation/contract-audit time interleaved
with the actual fetch bursts — not a pure network-throughput figure).
Store build: 22.26s for all 4 seasons from already-archived raw JSON.
Season summary/manifest computation: a few seconds per season, dominated
by JSON parsing, not network I/O (0 additional API calls).

## AI. Query benchmarks

5 practical research queries, benchmarked against the full 4-season store
(1,656,340 events, 2,609,945 player-role rows), 5-run average:

| Query | Avg time |
|---|---|
| All events in one game | 0.49 ms |
| All player SOG events before a date | 57.9 ms (7,231 rows) |
| All goals against an opponent before a date | 239.6 ms (31,199 rows) |
| All penalties before a date | 104.9 ms (37,943 rows) |
| All goalie shot events before a date | 18.7 ms (7,188 rows) |

All five are well within practical bounds for offline research use (this
is not a live-serving path). The slowest query (goals-against-opponent)
has no composite `(team_id, game_date)` index — deliberately not added,
per Part 32's "do not over-index blindly": 240ms for a full 4-season scan
is not currently a bottleneck for anything this project does.

## AJ. Idempotency

Proven at full 4-season scale, twice: once immediately after the initial
ingestion run (5 real games re-verified via retry, not duplicated), and
once again via a full `build_pbp_all_seasons.run_all()` re-run afterward —
**0 new games fetched, 0 duplicates, 0 missing, 0 failures** across all
5,248 games. The store itself is also rebuild-idempotent by construction:
`store.build_store()` deletes and reinserts only the named seasons' rows,
so rebuilding never duplicates another season's data.

## AK. Corpus manifest

See `research/real_nhl_pbp/corpus_manifest.json` (Part 34), built by
`build_corpus_manifest.py`:

```json
{
  "corpus_name": "nhl_play_by_play_research_corpus",
  "contract_version": "pbp_contract_v1",
  "seasons": ["20222023", "20232024", "20242025", "20252026"],
  "total_games_expected": 5248,
  "total_games_retrieved": 5248,
  "total_events": 1656340,
  "total_raw_bytes": 746461392,
  "anomaly_count": 0,
  "acceptance_status": "COMPLETE"
}
```

Per-season detail (`expected_games`, `retrieved_games`, `normalized_games`,
`event_count`, `raw_bytes`, `acceptance_status`) is nested under
`per_season` in the same file — every season shows `"COMPLETE"`.

## AL. Market-registry readiness updates

**64 markets** transitioned `historical_data_status` from
`REQUIRES_PLAY_BY_PLAY` → `AVAILABLE_UNUSED` in
`research/player_props/market_registry.py` (verified: the `AVAILABLE_
UNUSED` count rose from 30 to 94, a delta of exactly 64; `REQUIRES_PLAY_
BY_PLAY` no longer appears anywhere in the file). `model_status` was left
**untouched** at `NOT_BUILT` for every one of them (Part 35's explicit
instruction) — this is a data-availability update only, never a model or
market-definition change. `total_canonical_markets()` (142),
`derivable_today()` (21), and `validated_today()` (12) are all
byte-identical to before this slice, confirming no market definition or
model status shifted.

## AM. Dependency-readiness updates

`PROCESS_DEPENDENCY_GRAPH`'s structure is **byte-identical** — the
contract-drift audit found no finding requiring a prerequisite-edge
correction. A new, separate `PROCESS_DATA_FOUNDATION_STATUS` dict was
added to `dependency_graph.py` (Part 36): 16 of 17 processes are now
`DATA_FOUNDATION_READY` or `PARTIAL`; `JOINT_DEPENDENCE_SIMULATION` is
deliberately `NOT_APPLICABLE` — **every one of its data prerequisites is
now ready, but the simulator itself remains entirely unbuilt**, and this
field is designed so it can never be misread as "simulator built."
`is_acyclic()` remains `True`.

## AN. Dashboard/status updates

`dashboard/pages/13_Play_By_Play_Status.py` gained a new "4-season corpus"
panel at the top (Part 37's exact spec): `CORPUS: 4 seasons`, `SEASONS:
2022-23 through 2025-26`, `GAMES: 5,248 / 5,248`, `COVERAGE: 100.0%`,
`CONTRACT: PASS`, `DATA FOUNDATION: READY`, plus a per-season games-archived
breakdown. The existing pilot/single-season panel and the Parts 28-33
readiness expander are unchanged below it. No new betting-market page was
created.

## AO. Files created/modified

**Created**: `research/real_nhl_pbp/build_pbp_all_seasons.py`, `store.py`,
`query.py`, `build_corpus_manifest.py`, `run_multi_season_reconciliation.py`,
plus the real archived corpus under `research/real_nhl_pbp/raw/{20222023,
20232024,20242025}/` (2025-26 reused unchanged), `research/real_nhl_pbp/
research_pbp.db`, `four_season_ingestion_manifest.json`,
`multi_season_reconciliation_results.json`, `corpus_manifest.json`,
`season_summary.json` updates; `tests/test_pbp_multi_season.py` (45 tests);
`NHL_PBP_FOUR_SEASON_CORPUS_REPORT.md` (this file).

**Modified**: `research/player_props/market_registry.py` (Part 35, 64
markets' `historical_data_status` field only — see AL),
`research/player_props/dependency_graph.py` (Part 36, additive readiness
metadata only — see AM), `research/real_nhl_pbp/readiness.py` (6 new market
entries using already-established fields, plus 2 new readiness categories:
game-state reconstruction, OT/shootout), `dashboard/pbp_status_view.py` and
`dashboard/pages/13_Play_By_Play_Status.py` (Part 37),
`tests/test_pbp_foundation.py` (2 pinned-hash constants updated to reflect
this slice's authorized edits to market_registry.py/dependency_graph.py,
with explanatory comments).

**Verified untouched**: `models/combined_model.py`, `models/elo_model.py`,
`config.py`, `db.py`, `schema.sql`, `research/player_props/decision_
policy.py`, the Goals/Confidence research-artifact JSON files — all
sha256-pinned in both test files' `Test30`-`Test33`.

## AP. Full test result

**1,123 / 1,123 passing** (1,078 prior + 45 new in `tests/test_pbp_multi_
season.py`, covering all 33 Part-41 topics). 0 existing tests weakened.
Production files verified untouched by both mtime (all predate this
session) and sha256 pin.

## AQ. Recommended NEXT SINGLE DEVELOPMENT SLICE

**Build the mid-period goalie-change reconstruction and the game-winning-
goal backward-scan derivation** — the two remaining PARTIAL cells
(`GOALIE SAVES BY PERIOD`/`PERIOD SAVES`, and `GAME-WINNING GOAL`) that
block a small number of markets from being fully READY. Both are small,
self-contained utility functions over data this 4-season corpus already
has in full — not new modeling work, not a new data source, and not
covered by this slice's "no model development" ban (Part 38 bans building
the *markets themselves* — Period Props, First/Last Goal models — not
finishing the two derivation gaps in the data layer beneath them). Closing
these gives every period and event-time market in this report a clean
READY verdict before the first real period-market model is attempted.

---

## Final Questions

**ARE ALL FOUR REGULAR SEASONS INGESTED?** YES

**TOTAL GAMES?** 5,248 / 5,248 (100%)

**TOTAL NORMALIZED EVENTS?** 1,656,340

**IS COVERAGE 100% OR FULLY EXPLAINED?** YES (100% — the one transient
DNS-failure batch of 5 games was retried successfully; 0 games remain
missing)

**IS sortOrder RELIABLE ACROSS ALL FOUR SEASONS?** YES — confirmed
monotonic/unique in every sampled game across all 4 seasons;
`eventId` confirmed non-monotonic historically too

**ARE SHOOTOUT TALLIES CLEANLY ISOLATED?** YES

**IS situationCode RELIABLE ACROSS ALL FOUR SEASONS?** YES, with one minor
historical footnote: 2022-23's `game-end` events are 98.4% missing
`situationCode` (inconsequential — no market depends on it); every other
season/event-type combination is 100.0%, and shape is always valid where
present

**CAN EMPTY-NET STATE BE IDENTIFIED RELIABLY?** YES

**DO SCORES RECONSTRUCT CLEANLY?** YES

**ARE PLAYER IDS RELIABLE?** EVENT-SPECIFIC (100% on faceoff/shot/hit/goal
roles; the same two genuine, already-explained categories — bench
penalties, `other-block` blocks — account for every gap)

**IS PERIOD / EVENT TIME DATA READY FOR MULTI-SEASON MODELING?** YES

**IS SPECIAL-TEAMS EVENT DATA READY?** YES

**IS PENALTY EVENT DATA READY?** YES

**IS HIT EVENT DATA READY?** YES

**IS FACEOFF EVENT DATA READY?** YES

**IS GOALIE EVENT DATA READY?** PARTIAL (full-game/period-adjacent markets
READY; period-bucketed saves and official win/loss decision remain PARTIAL
/ NOT READY respectively)

**IS GAME-STATE RECONSTRUCTION READY?** YES

**HOW LARGE IS THE COMPLETE RAW CORPUS?** 746,461,392 bytes ≈ 711.8 MB

**HOW LARGE IS THE NORMALIZED STORE?** 612,093,952 bytes ≈ 583.8 MB

**WERE ANY EXISTING VALIDATED MODELS CHANGED?** NO

**WAS CONFIDENCE CHANGED?** NO

**WAS DECISION POLICY v2 CHANGED?** NO

**WAS NHL WIN MODEL CHANGED?** NO

**CURRENT FULL TEST RESULT?** 1,123 / 1,123

**WHAT IS NOW THE HIGHEST-LEVERAGE NEXT DEVELOPMENT SLICE?** Build the
mid-period goalie-change reconstruction and the game-winning-goal
backward-scan derivation — two small, self-contained utility functions
that close the only two remaining PARTIAL cells in the entire readiness
matrix, using data this corpus already has in full. See Section AQ.

---

**STOP AFTER FOUR-SEASON EXPANSION.** No new prop model, period model, PP
Points, Goalie Saves, Hits, simulation, or parlay logic was built in this
slice.
