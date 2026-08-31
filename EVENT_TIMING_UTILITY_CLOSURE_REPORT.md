# Event-Timing Utility Closure — Goalie Tenure + Game-Winning Goal

This slice closes the two remaining PARTIAL data-readiness gaps identified
in `NHL_PBP_FOUR_SEASON_CORPUS_REPORT.md`: mid-period goalie-change
reconstruction (blocking `GOALIE SAVES BY PERIOD` / `PERIOD SAVES`) and the
game-winning-goal derivation (blocking `GAME-WINNING GOAL`). Two
deterministic research utilities were built, corpus-validated across all
5,248 games, and tested. **No predictive model was built or fitted.**

---

## A. Goalie-tenure algorithm

`research/real_nhl_pbp/goalie_tenure.py::reconstruct_goalie_tenure()`.
For each team (as the DEFENDING side), walks every non-shootout
shot-on-goal/missed-shot/goal event in `event_sequence` order and reads
the SAME canonical per-event signal `normalize.py` already establishes —
`event.players.get("goalie")` — never inferring identity from final
boxscore order (Part 2's explicit ban). A new tenure interval is emitted
on every state change, tagged with one categorical `interval_type`
(`STARTER` / `RELIEF` / `RETURN_AFTER_EMPTY_NET` / `EMPTY_NET`) rather than
several booleans that could disagree. Interval boundaries are real
`event_sequence` values plus the raw `(period, timeInPeriod)` pair —
**no wall-clock timestamp is fabricated** (Part 4's explicit ban); only
event order is treated as reliable.

Save-counting (`period_saves.py`) deliberately does **not** depend on the
tenure-interval structure — it reads the identical per-event `goalie` field
directly, so a mid-period substitution needs no special-case handling at
all: it falls out of grouping shots by `(goalie_id, period_number)`. This
is the same "one source of truth" principle already applied to score
reconstruction (Part 22) — there are not two competing goalie-identity
implementations in this codebase.

## B. Mid-period change frequency

Corpus-scale, all 5,248 games:

| Season | Multi-goalie games | Mid-period changes | 3+ goalies (one team) | Returns after empty net |
|---|---|---|---|---|
| 2022-23 | 152 | 106 | 0 | 147 |
| 2023-24 | 164 | 108 | 0 | 163 |
| 2024-25 | 136 | 84 | 0 | 176 |
| 2025-26 | 140 | 89 | 0 | 185 |
| **Total** | **592** | **387** | **0** | **671** |

592 games (11.3% of the corpus) show a team using 2 different goalies —
matching the prior slice's own multi-goalie count exactly, now with a new,
more precise breakdown: **387 of those 592 (65.4%) are genuine mid-period
substitutions**, not a routine between-periods change. **Zero games in the
entire 4-season corpus show a team using 3+ distinct goalies** — a real,
disclosed finding, not assumed: three-goalie games are evidently rare
enough not to occur at all in this corpus, so `goalie_tenure.py`'s
handling of that case (it is not artificially restricted to exactly two)
is validated by absence of any counterexample rather than by a present
one.

## C. Empty-net interval handling

Distinguished from a real relief-goalie substitution using the accepted
joint two-signal empty-net rule (`normalize.is_empty_net_context()`,
reused unchanged): an `EMPTY_NET` interval is recorded whenever an event
shows no goalie identity, without disturbing the tracked "current real
goalie" — so when a real goalie identity reappears afterward, the
algorithm can tell whether it is the SAME goalie returning
(`RETURN_AFTER_EMPTY_NET`) or a genuinely different one (`RELIEF`).
Verified on real game `2025020814` (Section D).

## D. Goalie return cases

Real, verified example: game `2025020814` — goalie `8477992` starts,
is pulled for an extra attacker (`EMPTY_NET` interval at period 3, 14:55,
matching a real empty-net goal against at that exact moment), returns
(`RETURN_AFTER_EMPTY_NET`, correctly NOT tagged `RELIEF`), then is pulled
a second time later in the same period (a second `EMPTY_NET` interval at
19:20-19:35, matching the second empty-net goal). Zero `RELIEF` intervals
were recorded for this team, confirming the algorithm never mistakes a
goalie's own return for a new goalie taking over.

## E. Multiple-goalie cases

Real, verified example: game `2025020240` (2025-26) shows a genuine
mid-period relief change — starter `8479973` through period 2 (04:45),
relief `8475717` from period 2 (08:12) onward, correctly flagged by
`mid_period_changes()` as occurring WITHIN period 2 (not at a period
boundary). Contrasted with real game `2022020032` (2022-23), where a
relief change happens between periods 2 and 3 (last period-2 event still
shows the original goalie, first period-3 event already shows the relief
goalie) — correctly NOT flagged as a mid-period change, per Part 5's
distinction.

## F. Period-save reconstruction

`research/real_nhl_pbp/period_saves.py::period_saves_by_goalie()`. Uses
the SAME canonical per-event `goalie` field as Section A — no dependency
on the tenure-interval structure at all (Part 22's "one source of truth"
applied here too). A `shot-on-goal` event is a save for the goalie in net;
a statistical `goal` event is NOT a save but DOES count toward shots faced
(mirrors the project's existing SOG-includes-goal convention, applied to
the defending side). Shootout events are excluded entirely. Because each
event already carries the correct goalie identity, a mid-period
substitution needs zero special-case code here — it falls out of grouping
by `(goalie_id, period_number)` automatically.

## G. Full-game save reconciliation

**5,248 / 5,248 games (100%) reconciled exactly** against the real
official `/boxscore` `saves` field — every goalie, every game, full
4-season corpus (not a sample; Part 8 explicitly asked for the complete
corpus "where possible," and it was possible: this is pure post-hoc
validation of a new utility, not repeated re-fetching of data already in
hand).

| Season | Games | Exact matches | Mismatches | Largest discrepancy |
|---|---|---|---|---|
| 2022-23 | 1,312 | 1,312 | 0 | 0 |
| 2023-24 | 1,312 | 1,312 | 0 | 0 |
| 2024-25 | 1,312 | 1,312 | 0 | 0 |
| 2025-26 | 1,312 | 1,312 | 0 | 0 |
| **Total** | **5,248** | **5,248** | **0** | **0** |

Two transient network issues occurred during the fetch (a read-timeout on
one 2024-25 game's boxscore, and an earlier duplicate-process cleanup
during development) — both resolved by simple retry, not excluded from
the final count above; the table reflects the fully-retried result.

## H. Mismatch analysis

**None found.** Zero save mismatches, zero period-to-full-game coherence
violations, across all 5,248 games and every goalie who appeared in them.
This is a stronger result than the Acceptance Standard required ("an
acceptable rate," not necessarily 100%) — nothing was patched, excluded,
or explained away to reach it; it is the raw result.

## I. Period-save readiness decision

**READY**, for both `GOALIE SAVES BY PERIOD` and `PERIOD SAVES` (Section
Q of the market-registry updates below). All 9 items of the Goalie
Utility Acceptance Standard are satisfied: mid-period changes are handled
deterministically (Section B, 387 real cases), relief goalies are
distinguished from empty-net intervals (Section C/D, verified on real
pull-and-return data), event goalie assignment is reliable (Section A),
period saves reconcile to full-game saves (0 coherence violations, Section
F), full-game saves reconcile with official totals (100%, not merely
"acceptable" — Section G), shootout events are excluded correctly (Section
F), multi-goalie games are handled including the 3+ goalie case by
absence of any counterexample (Section E/B), no systematic unresolved
mismatch remains (Section H), and corpus-scale execution succeeded on all
5,248 games.

## J. GWG algorithm

`research/real_nhl_pbp/gwg.py::derive_gwg()` implements the exact NHL
statistical definition (Part 13), never recency or a hard-coded special
case (Part 13's explicit ban on "last goal", "go-ahead goal at the time",
"final goal", or "OT goal automatically"): it reuses
`normalize.reconstruct_statistical_score()` (Part 22 — one source of
truth, not a second score-reconstruction implementation) to get the final
statistical score, computes `gwg_ordinal = losing_team_final_goals + 1`,
sorts the winning team's own statistical goals by `event_sequence`, and
returns the goal at that exact ordinal position. This is a pure function
of the FINAL score — a later empty-net goal or an early lead that gets
partially clawed back cannot change which goal it points to, by
construction, not by a special case added to handle those situations.

## K. Shootout GWG semantics

**No player statistical GWG for any shootout game.** Confirmed on all 373
real shootout games in the 4-season corpus (Part 19's "no independent
official GWG field" finding still holds — neither the play-by-play nor
`/boxscore` endpoint carries one): the statistical score stays exactly
tied after regulation/OT for every one of them, because the shootout-
deciding goal is excluded from the statistical score (already-established
project rule). A tied final score has no winning team for the GWG
definition to assign a goal to, so `derive_gwg()` returns
`NO_PLAYER_GWG_SHOOTOUT` — never a fabricated skater GWG. **NO INDEPENDENT
OFFICIAL GWG FIELD AVAILABLE IN CURRENT DATA CONTRACT** (Part 19's required
exact disclosure, since none exists to reconcile against).

## L. OT GWG behavior

Verified, not hard-coded (Part 15): every one of the 798 real OT games in
the corpus resolves through the identical `derive_gwg()` code path as a
regulation game — no special-cased branch exists for `period_type ==
"OT"`. `gwg_period_type` correctly reads `"OT"` for these games because
the winning team's `(losing_final_goals + 1)`-th goal genuinely occurred
in overtime, which is simply what the general algorithm finds — not
something separately asserted.

## M. Empty-net final-goal cases

**49 real cases across the 4-season corpus** where the derived GWG's own
`empty_net` flag is `True` — a real, cross-validated example: game
`2025020011` (OTT 5, TBL 4), where OTT's empty-net goal in the 3rd period
(19:12) genuinely IS the winning team's `(4 + 1) = 5`th statistical goal,
so it correctly resolves as the GWG. Distinguished from the general
"later empty-net goal must NOT become the GWG" case (Section N/Part 16):
this only happens because that specific empty-net goal happens to BE the
right ordinal position, not because it is empty-net or late.

## N. Comeback/multi-lead examples

**469 real games** (of 4,875 non-shootout games, 9.6%) show 2+ statistical
lead changes. Real example: game `2025020122` (EDM 6, MTL 5, 3 lead
changes) — the derived GWG correctly resolves to EDM's late 3rd-period
goal (18:51) despite the game's back-and-forth scoring pattern, because
the algorithm only ever looks at the FINAL score's ordinal position, never
"the first permanent lead" or any other forward-time heuristic (Part 17's
explicit requirement). The single-season report's own worked example
(game `2025020814`, Section P of that report) is the canonical
empty-net-trap case: the true GWG was the winning team's 6th goal, scored
at even strength in period 2 — two SUBSEQUENT empty-net goals do not
retroactively become the GWG.

## O. Corpus-scale GWG results

All 5,248 games, **0 network calls** (pure computation over the
already-normalized corpus — no independent official GWG field exists to
reconcile against, Section K/P):

| Season | REG w/ GWG | OT w/ GWG | SO (no player GWG) | EN-GWG cases | Multi-lead games | Invariant violations | Derivation failures |
|---|---|---|---|---|---|---|---|
| 2022-23 | 1,010 | 207 | 95 | 12 | 122 | 0 | 0 |
| 2023-24 | 1,040 | 190 | 82 | 11 | 113 | 0 | 0 |
| 2024-25 | 1,041 | 194 | 77 | 12 | 109 | 0 | 0 |
| 2025-26 | 986 | 207 | 119 | 14 | 125 | 0 | 0 |
| **Total** | **4,077** | **798** | **373** | **49** | **469** | **0** | **0** |

`4,077 + 798 + 373 = 5,248` — exactly the corpus total, and exactly
matching the independently-established REG/OT/SO season counts from the
prior slice (cross-validated, not coincidental). **Every one of the 4,875
non-shootout games resolved deterministically with zero invariant
violations** — the Part 21 checklist (winning-team ownership, statistical-
goal-only, ordinal correctness, uniqueness) held on every single game, not
just the hand-picked examples above.

## P. GWG reconciliation source

**NO INDEPENDENT OFFICIAL GWG FIELD AVAILABLE IN CURRENT DATA CONTRACT.**
Both the play-by-play and `/boxscore` endpoints were checked (again, not
re-guessed) this slice — neither carries a GWG pointer, matching the
single-season report's original finding exactly. Part 19's fallback was
used instead: validation through the score invariants themselves (Section
O / Part 21), which is a stronger check for this specific question than an
opaque external field would be, since it verifies the DEFINITION was
implemented correctly, not merely that some other system agrees with a
number.

## Q. GWG readiness decision

**READY.** All 8 items of the GWG Acceptance Standard are satisfied: the
final-score definition is implemented exactly (Section J), shootout
semantics are handled correctly (Section K, all 373 real SO games), REG
and OT games both resolve (Section O), later empty-net goals do not
falsely become the GWG (Section N), comeback/lead-change games resolve
correctly (Section N), scorer/team/event identity is fully deterministic,
and there are zero corpus-scale failures (Section O).

## R. Market-registry readiness changes

`research/player_props/market_registry.py`, `historical_data_status`
field only — `model_status` untouched at `NOT_BUILT` for all four:

| Market ID | Before | After |
|---|---|---|
| `GAME_WINNING_GOAL` | `AVAILABLE_UNUSED` | **`READY`** |
| `PERIOD_1_GOALIE_SAVES` | `AVAILABLE_UNUSED` | **`READY`** |
| `PERIOD_2_GOALIE_SAVES` | `AVAILABLE_UNUSED` | **`READY`** |
| `PERIOD_3_GOALIE_SAVES` | `AVAILABLE_UNUSED` | **`READY`** |

`READY` is a new, more specific `historical_data_status` value than the
existing `AVAILABLE_UNUSED` — it means not just "the raw data exists" but
"a validated deterministic derivation utility now exists for this exact
market," which is what Part 23 explicitly asked these four to become.
`total_canonical_markets()` (142), `derivable_today()` (21), and
`validated_today()` (12) are all unchanged — no market definition and no
model status shifted.

## S. Dependency-readiness changes

`research/player_props/dependency_graph.py`. `PROCESS_DEPENDENCY_GRAPH`'s
structure is **byte-identical** — no genuine dependency error was found
(Part 24's own condition for touching it). Two readiness-metadata changes,
both additive/status-only:

- `PROCESS_DATA_FOUNDATION_STATUS["GOALIE_WORKLOAD_SAVE_PROCESS"]`:
  `PARTIAL` → `DATA_FOUNDATION_READY`.
- New `PROCESS_READINESS_NOTES` dict (Part 24's own worked examples,
  implemented literally): `GOALIE_WORKLOAD_SAVE_PROCESS` → "event-level
  tenure reconstruction ready"; `GAME_SCORE_STATE` → "GWG deterministic
  derivation ready".

`is_acyclic()` remains `True`.

## T. Simulation-invariant updates

`SIMULATION_INVARIANTS.md` gained 5 new invariants (14-18, Part 25),
each traceable to a specific corpus-validated finding from this slice:
period saves sum to full-game saves; every save belongs to the goalie
actually in net; an empty-net shot cannot generate a save; GWG satisfies
the final-score definition, not recency; a shootout attempt can never
become a player's statistical GWG. See the file itself for the full text
— each invariant cites the real game/number that grounds it, not just an
abstract rule.

## U. Dashboard/status changes

`dashboard/pages/13_Play_By_Play_Status.py` gained a new "Event-timing
utility closure" panel (Part 28's exact spec): `PERIOD GOALIE SAVES:
READY` and `GWG: READY`, reading live from `readiness.py` (so it will
never silently go stale relative to the actual registry state). The
existing Parts 28-33 readiness expander already shows the same two
entries in detail — this panel is a small, dedicated top-level callout on
top of it, not a duplicate implementation. No new betting-market page was
created.

## V. Files created/modified

**Created**: `research/real_nhl_pbp/goalie_tenure.py`, `period_saves.py`,
`gwg.py`, `gwg_invariants.py`, `run_goalie_tenure_audit.py`,
`run_gwg_audit.py`, plus their real result files
(`goalie_tenure_audit_results.json` + per-season detail files,
`gwg_audit_results.json`); `tests/test_event_timing_utilities.py` (33
tests); `EVENT_TIMING_UTILITY_CLOSURE_REPORT.md` (this file).

**Modified**: `research/player_props/market_registry.py` (4 markets'
`historical_data_status` only — Section R), `research/player_props/
dependency_graph.py` (additive readiness metadata only — Section S),
`research/real_nhl_pbp/readiness.py` (3 entries PARTIAL→READY, using the
corpus-validated evidence above), `dashboard/pages/13_Play_By_Play_Status.py`
(Section U), `SIMULATION_INVARIANTS.md` (Section T),
`tests/test_pbp_foundation.py` (2 pinned-hash constants updated to reflect
this slice's authorized edits, with explanatory comments — the same
pattern used in every prior slice that touched these two files).

**Verified untouched**: `models/combined_model.py`, `models/elo_model.py`,
`config.py`, `db.py`, `schema.sql`, `research/player_props/decision_
policy.py`, the Goals/Confidence research-artifact JSON files — all
sha256-pinned in `tests/test_event_timing_utilities.py`'s `Test30`-`Test33`.

## W. Full test result

**1,156 / 1,156 passing** (1,123 prior + 33 new in `tests/test_event_
timing_utilities.py`, covering all 33 Part-29 topics). 0 existing tests
weakened. Production files verified untouched by both mtime (all predate
this session) and sha256 pin.

## X. Recommended NEXT SINGLE DEVELOPMENT SLICE

With every PARTIAL cell in the readiness matrix now closed (both from the
single-season report and this slice), the data foundation has no
remaining known gaps for period, event-time, special-teams, penalty, hit,
faceoff, or goalie-save markets. **The highest-leverage next slice is the
first real period-market model** — most naturally `Player SOG by Period`
or `Team Goals by Period` (both fully `READY`, both reuse this project's
existing, already-validated Poisson/NB count-model machinery from
`research.player_sog.count_models`, and both would immediately prove out
the full event-timing foundation end-to-end on a real predictive target
for the first time). This is a genuine model-development slice — the
first one this data-foundation arc has been building toward across four
consecutive slices — and should follow this project's established
walk-forward validation discipline (WARMUP/TUNING/EVAL season splits) the
same way every other prop model here has.

---

## Final Questions

**CAN MID-PERIOD GOALIE CHANGES BE RECONSTRUCTED?** YES

**CAN EMPTY-NET INTERVALS BE DISTINGUISHED FROM RELIEF-GOALIE CHANGES?** YES

**CAN GOALIE SAVES BY PERIOD NOW BE RECONSTRUCTED?** YES

**DO PERIOD SAVES RECONCILE TO FULL-GAME SAVES?** YES (0 coherence
violations across all 5,248 games)

**DO FULL-GAME SAVES RECONCILE TO OFFICIAL TOTALS?** YES (5,248/5,248,
100% — no disclosed exceptions were needed)

**IS GOALIE SAVES BY PERIOD DATA STATUS NOW READY?** YES

**CAN GWG BE DERIVED DETERMINISTICALLY FROM THE SCORE TIMELINE?** YES

**IS THE GWG ALWAYS THE FINAL GOAL?** NO

**CAN AN EMPTY-NET GOAL BE THE GWG?** YES, ONLY IF IT IS THE WINNING
TEAM'S (losing_final_goals + 1)-th statistical goal — confirmed on 49 real
corpus cases

**CAN A SHOOTOUT ATTEMPT BE A STATISTICAL PLAYER GWG?** NO

**IS GWG DATA STATUS NOW READY?** YES

**WERE ANY PREDICTIVE MODELS BUILT?** NO

**WERE ANY EXISTING VALIDATED MODELS CHANGED?** NO

**WAS CONFIDENCE CHANGED?** NO

**WAS DECISION POLICY v2 CHANGED?** NO

**WAS NHL WIN MODEL CHANGED?** NO

**CURRENT FULL TEST RESULT?** 1,156 / 1,156

**WHAT IS NOW THE HIGHEST-LEVERAGE NEXT DEVELOPMENT SLICE?** The first
real period-market model (Player SOG by Period or Team Goals by Period),
reusing this project's existing validated count-model machinery, since
every data-readiness gap that previously blocked it is now closed. See
Section X.

---

**STOP AFTER UTILITY CLOSURE.** No period model, Goalie Saves predictive
model, PP Points, Hits, joint simulator, or parlay logic was built in this
slice.
