# Live Special-Teams Role Intelligence + Shadow SOG Validation

**Sprint objective (as given):** the historical `PLAYER_SOG_PP_ROLE_OVERLAY`
finding (SHADOW_VALIDATED, thresholds 1+/2+/3+, both eval seasons,
bootstrap-supported) could not be prospectively validated because the
live pipeline had no PIT-safe way to compute a player's current PP-role
state before his next game. This sprint closes that specific gap: it
makes the historical signal **observable prospectively**, wires it into
a shadow (never production) SOG probability path, and records both
side-by-side in the prospective ledger. It does **not** re-litigate the
historical finding, promote anything to production, touch
`decision_policy`, or revisit Blocks/Goals/Assists/Points.

---

## A. What was built

| Component | File | Status |
|---|---|---|
| Official NHL TOI report parser (live PP/SH/EV ice time per player-game) | `operational/special_teams_toi_report.py` | Built, verified against a real game |
| Unified special-teams history store (backfill + live share one schema) | `operational/special_teams_history_store.py` | Built, 188,863 rows backfilled |
| One-time historical backfill (reuses existing corpus, no re-fetch) | `operational/backfill_special_teams_history.py` | Run, completed |
| Live/prospective role-state computation (exact parity with research detector) | `operational/special_teams_roles_live.py` | Built, parity-tested |
| SOG shadow overlay (frozen coefficients, never refit) | `operational/sog_shadow_overlay.py` | Built |
| Prospective ledger schema v3 (7 new shadow columns + immutability) | `operational/prospective_ledger.py`, `operational/prospective_schema.sql` | Built, migration tested |
| Shadow SOG prospective recording entry point | `operational/record_sog_shadow_observation.py` | Built, end-to-end verified |
| Dashboard: Player Intelligence PP Role expander | `dashboard/pages/25_Player_Intelligence.py`, `dashboard/components.py` | Built, browser-verified |
| Model Health registry entry (`PLAYER_SOG_PP_ROLE_OVERLAY`, SHADOW_VALIDATED) | `research/model_registry.py` | Built, renders on Model Health page |
| Prospective Validation Protocol addendum (SOG overlay pre-registered minimums) | `PROSPECTIVE_VALIDATION_PROTOCOL.md` | Added |
| Test suite | `tests/test_special_teams_roles_live.py` | 47 new tests, all passing |

## B. Live data source (Part 1-2)

The official NHL TOI HTML report
(`https://www.nhl.com/scores/htmlreports/{season}/T{H,V}{gameIdTail}.HTM`,
discovered via `/v1/gamecenter/{game_id}/right-rail`'s `gameReports`
field) is the real, live, per-game PP/SH/EV ice-time source. It has no
`player_id` field directly — identity is resolved via the real boxscore
JSON's `(team, sweaterNumber) -> playerId` crosswalk, never by
name-matching (Part 4's canonical-ID requirement).

`operational/moneypuck_daily.py`'s existing daily sync was confirmed to
be season-aggregate only (`seasonPlayersSummary`), not per-game — this
is why the NHL-report path, not an extension of the existing MoneyPuck
sync, is the live path going forward.

## C. Cross-validation against the independent historical archive

A random 20-game sample (715 player-games, all 4 seasons) was parsed
from the new NHL-report path and compared against the independent,
already-validated MoneyPuck historical archive:

- **Total TOI: 715/715 exact match (100%).**
- **PP TOI: 665/715 exact match (93.01%).** The remaining 50 discrepancies
  cluster as constant, team-wide, game-level offsets — a differing
  PP/EV boundary convention between the two sources on specific
  power-play sequences — not random parser error or a systematic bug.
  This is disclosed, not hidden: PP TOI from the two sources will not
  always agree to the second, though total TOI reconciles perfectly.

## D. Storage (Part 4-5)

One unified `special_teams_history` table (`game_id, player_id` primary
key) serves both the one-time backfill (`source =
"MONEYPUCK_ARCHIVAL_BACKFILL"`) and future live ingestion — the live
role-feature computation never needs to know which source populated a
row. `INSERT OR REPLACE` makes re-ingestion of an already-stored game a
safe no-op; a corrected re-ingestion of the same game intentionally
overwrites the prior row (current-best-knowledge table, not an
append-only revision log — the prospective ledger's own snapshot at
prediction time is the append-only record of what was known *then*).

Backfill result: **188,863 records**, 2022-10-07 through 2026-04-16,
100% `MONEYPUCK_ARCHIVAL_BACKFILL` (no live-ingested rows yet — no NHL
games have occurred since this sprint began that required live
ingestion; the parser is built and tested but has not yet ingested a
real live game end-to-end outside the cross-validation sample).

## E. PIT safety (Part 3)

`player_history_before(conn, player_id, before_date)` enforces strict
`game_date < before_date` (never `<=`). Verified directly:
`tests/test_special_teams_roles_live.py::Test01PitBoundary` (exact
boundary exclusion, no future leakage into a role state computed as of
an earlier date).

## F. Exact parity with the historical research detector (Part 7-9)

The live pipeline (`operational/special_teams_roles_live.py`) does not
re-derive the unit-ranking / mode-classification / transition-naming
logic. It imports and calls the SAME functions from
`research/period_event_timing/special_teams_roles.py`
(`classify_role_state`, `role_change_magnitude`) and the SAME
`research/special_teams_role_overlay/core.py::add_games_since_onset`
used to fit and validate the historical overlay. The only new code is
data plumbing (reading from the live SQLite store instead of the
research JSONL corpus) and the team-tenure filter described in Section
G.

**Parity QA performed:**
- 150-sample historical-vs-live cross-check (carried over from
  pipeline construction): **145/150 exact match (96.7%)**. All 5
  mismatches are cases where the live pipeline correctly downgrades to
  `ROLE_UNCERTAIN` for a genuine trade/short-tenure edge case the
  team-agnostic historical research pipeline never had to handle — a
  safety-conservative divergence, not an error.
- 3 additional named, real-player transition examples pulled directly
  from the historical corpus (Section H) were independently
  recomputed by the live pipeline and matched exactly.

## G. Trade handling and the re-acquisition bug (Part 6, 42)

The live role state restricts the recent/baseline windows to games
played for the player's **current** team — a trade resets the
operationally-relevant role history, even though the player's own
skill history elsewhere in this project is preserved. A player with
too few current-team games lands in `ROLE_UNCERTAIN` rather than
carrying a stale unit label from the old team.

A real bug was found and fixed while building this: a player who LEFT
a team and was later RE-ACQUIRED by the SAME team (confirmed on a real
player, TBL -> other teams -> TBL, years apart) would otherwise have
BOTH stints blended into one "current team" history, mixing a 3-year-old
game with yesterday's. Fixed via `_most_recent_tenure()`, which returns
only the player's latest CONTIGUOUS run with the current team. Covered
by `Test05TradeAndReacquisitionHandling` (4 tests).

## H. Named real-player QA (Part 56)

| Player | Real ID | Team | As-of | Live-computed state |
|---|---|---|---|---|
| Connor McDavid | 8478402 | EDM | 2026-04-17 | `STABLE_PP1` (recent=PP1, baseline=PP1, n=3/8) |
| Auston Matthews | 8479318 | TOR | 2026-03-13 | `STABLE_PP1` (recent=PP1, baseline=PP1, n=3/8) |
| Cale Makar | 8480069 | COL | 2026-04-17 | `STABLE_PP1` (recent=PP1, baseline=PP1, n=3/8) |
| Justin Faulk | 8475753 | STL | 2025-12-11 | `PROMOTED_PP2_TO_PP1` (matches historical corpus exactly) |
| Noah Ostlund | 8483500 | BUF | 2026-01-24 | `ADDED_TO_PP1` (matches historical corpus exactly) |
| Shea Theodore | 8477447 | VGK | 2024-04-12 | `DEMOTED_PP1_TO_PP2` (matches historical corpus exactly) |

The three stars all show a sensible, expected `STABLE_PP1`. The three
transition examples were selected by scanning the real, already-computed
`research/special_teams_role_transitions_table.jsonl` for the first
real occurrence of each named transition state, then independently
recomputing that exact (player, team, date) through the NEW live
pipeline — all three matched exactly.

## I. Role certainty and rookie/insufficient-history handling (Part 10-11)

`role_certainty()` (reused, unmodified, from Sprint D's
`research/special_teams_role_overlay/core.py`) linearly ramps from the
minimum-support gate to the target window size, capped at 1.0. A player
with zero games on record for his current team returns
`{"state": "ROLE_UNCERTAIN", "n_recent": 0, "n_baseline": 0, "reason":
"no games on record for current team"}` — never a fabricated unit
membership. Covered by `Test04RookieAndInsufficientHistory`.

## J. SOG shadow overlay pipeline (Part 14-17)

`compute_shadow_sog()` applies the FROZEN, already-validated
coefficients from `research/special_teams_role_overlay_sog_results.json`
— never refit:

```
beta_role:      PP1 = +0.0642   PP2 = +0.0094
transition (+): beta = +0.01446  decay = step_2  (declines: 0.01576 -> 0.00678)
transition (-): beta = -0.03144  decay = step_2  (declines: -0.03318 -> -0.02166)
```

`log(mu_adjusted) = log(mu_frozen) + beta_role*certainty +
beta_transition*decay(games_since)*direction*certainty`, then threshold
probabilities are re-derived via the real, reused
`research.player_sog.count_models.threshold_probabilities()` — never
independent per-threshold adjustments, so monotonicity is preserved by
construction. Verified: `Test07SogShadowOverlay::
test_threshold_probabilities_are_monotonically_non_increasing`.

**Only 1+/2+/3+ are `SHADOW_VALIDATED`.** 4+/5+/6+ are still computed
(for display completeness) but explicitly tagged as not validated
(`shadow.VALIDATED_THRESHOLDS = (1, 2, 3)`) — nothing downstream can
present them as research-backed.

Low certainty (thin recent/baseline support) dampens the adjustment
toward the frozen baseline; it never amplifies it — verified directly
(`test_low_certainty_dampens_toward_frozen_baseline`).

## K. Production/shadow separation (Part 19-22)

`record_sog_observation()` computes the frozen production SOG
prediction UNCHANGED (calls the exact interface production already
uses) and the shadow prediction side by side, and records both into the
SAME `MODEL_OBSERVATION` ledger row — it never calls `record_real_bet`
(verified: `Test08ShadowNeverTouchesProductionOrBetting`) and neither
module imports `decision_policy` (verified directly by source-text
inspection in the same test class). Records even when no sportsbook
market exists — market-price columns are simply left `NULL`, matching
every other `MODEL_OBSERVATION` in this project's convention.

## L. Prospective ledger schema v3 (Part 21, 62)

7 new columns added to `predictions`: `sog_shadow_raw_probability`,
`sog_shadow_conservative_probability`, `pp_role_state`,
`pp_role_certainty`, `pp_transition_state`, `pp_games_since_transition`,
`role_overlay_version`. SQLite has no `ALTER TRIGGER`, so the
`predictions_immutability` trigger is dropped and recreated from the
current `schema.sql` (single source of truth) so the new columns are
protected on an ALREADY-EXISTING database, not just a fresh one — this
was tested directly (`Test10LedgerSchemaV3Migration`, including a
synthetic pre-v3 database migrated live, and confirming the trigger
raises `sqlite3.IntegrityError` on any attempted post-insert `UPDATE` of
a shadow column, while settlement columns remain mutable).

## M. Dashboard integration

**Done:** Player Intelligence page (`25_Player_Intelligence.py`) has a
new "Power Play Role" expander showing the real, live role state for
the viewed player (browser-verified via `streamlit.testing.v1.AppTest`
against a real player — expander text renders, no exceptions). Model
Health (`22_Model_Health.py`) now shows `PLAYER_SOG_PP_ROLE_OVERLAY` as
a `SHADOW_VALIDATED` row (browser-verified) automatically, because it
reads `MODEL_REGISTRY` directly — no bespoke widget needed.

**Not done this sprint (explicitly deferred, not silently skipped):**
- Player Props page PP-role filter (All/PP1/PP2/None/Transition/Uncertain).
- "Today" page badge for actionable players with a meaningful PP transition.
- Game Detail page surfacing of players with a meaningful PP transition.
- A dedicated `SPECIAL_TEAMS_HISTORY` / `PP_ROLE_PROJECTION` System
  Health component with explicit data-freshness/staleness (OK/STALE/
  WAITING/ERROR) semantics — the Model Health row above shows
  validation status, not pipeline freshness.

Given this sprint's own framing ("closing the prospective-availability
gap is more valuable than further retrospective fitting") and the time
already spent building and verifying the core pipeline end to end, the
remaining dashboard surface area was deprioritized rather than built
quickly and unverified.

## N. Season transition / preseason policy (Part 38-41)

**Not formally implemented as a distinct schema field.** No
`game_type`/season-phase column exists in `special_teams_history` to
separate `PRESEASON_USAGE` from `REGULAR_SEASON_USAGE`. However, the
strict PIT query (`game_date < as_of_date`) already produces the
functionally-correct fallback behavior the spec asked for: because
`dd.SIMULATED_DATE` (2026-10-14) is after the last backfilled game
(2026-04-16, end of 2025-26 season), a role query as-of the simulated
"today" naturally uses the player's LAST KNOWN regular-season role as
recent/baseline evidence rather than blind-resetting to
`ROLE_UNCERTAIN` — verified directly against real McDavid data above.
What is genuinely missing is the EXPLICIT distinction and blending
policy (weighting a handful of new preseason games against a full
prior-season baseline) the spec asked for; this is disclosed as
deferred, not claimed as done.

## O. Prospective validation protocol (Part 43, 45, 46)

Added an addendum to `PROSPECTIVE_VALIDATION_PROTOCOL.md` registering
the SOG PP-role overlay's own pre-registered minimums (300
observations, 75 unique players, 30 distinct game dates), evaluated
independently of the existing Goals/Points overlay table, with an
explicit no-early-promotion rule.

## P0. Structural audit false positive (found and fixed)

The project's own `tests/test_training_path_structural_audit.py` (which
scans production code for game-id/list-position used as an apparent
training-eligibility proxy) flagged the two new list slices in
`operational/special_teams_roles_live.py`
(`recent_slice = current_team_games[-RECENT_GAMES:]` and the paired
`baseline_slice`). This is the exact same false-positive shape already
seen and justified in Sprint B/C for
`research/run_special_teams_role_transitions.py`'s equivalent slices:
`current_team_games` is one player's own real games, already filtered
to strictly-before `as_of_date` by
`special_teams_history_store.player_history_before()`'s own `game_date
< before_date` query, before this function ever sees the list — slicing
the tail of an already-PIT-filtered list is the intended window
construction, not a training-eligibility split. Per this project's
standing rule, the detector itself was never weakened — two new,
specifically-justified `JUSTIFIED_EXCEPTIONS` entries were added
instead, following the identical precedent and wording style already
on file. Confirmed fixed:
`tests/test_training_path_structural_audit.py` passes (10/10) with the
new entries in place.

## P. Tests

47 new tests in `tests/test_special_teams_roles_live.py`, covering: PIT
boundary exclusion, idempotent/duplicate ingestion, all 5 tested role
transition states plus PK-vs-PP field independence, rookie/insufficient
history, trade exclusion and same-team re-acquisition, games-since-onset
correctness and its `_include_transition_info=False` short-circuit
(avoiding O(n^2) recursion), the real frozen-coefficients file loading,
shadow-mu direction/certainty/monotonicity behavior, positive/negative
transition beta separation, shadow-never-touches-production/betting
guards (by source inspection), end-to-end `record_sog_observation`
recording (demo guard, no-prediction skip, raw-vs-shadow side by side,
no-market recording, checkpoint field), ledger schema v3 migration
(fresh-db, from-v2, idempotent-on-v3, immutability trigger, settlement
columns remain mutable), no-network-import guards, and direct reuse of
the frozen `core.py` math functions.

**Full suite: 1,994/1,994 passing** (`python3 -m unittest discover -s
tests -p "test_*.py"`, 257.7s) — baseline before this sprint was 1,947;
this sprint adds exactly 47, zero failures, zero regressions.

## Q. Frozen boundary confirmation

`git status` confirms the only TRACKED files modified since the
project's baseline snapshot commit are `.gitignore`, `README.md`,
`requirements.txt`, and `tests/test_training_path_structural_audit.py`
(none are model/decision-policy files). `tests/
test_preseason_consolidation.py::TestExtraNumericalAuditEvidence::
test_production_boundary_files_unchanged` passed. No frozen model,
joint model, context overlay, or `decision_policy.py` file was touched.
The new `special-teams role-overlay historical coefficients/results`
addition to the frozen list (per this sprint's own instructions) was
also not touched — `research/special_teams_role_overlay_sog_results.json`
is read-only, never rewritten, by every module built this sprint.

## Odds API / scheduler / credits

**Zero Odds API credits used.** This sprint used only the official NHL
API (schedule/boxscore/right-rail/TOI-report endpoints) and the
already-local, previously-backfilled research corpus. The launchd
scheduler was not installed (per the standing instruction to hold off
until mid-September).

---

## Final Questions

**Does the live pipeline reproduce the historical role-state detector
exactly?** Yes, by construction (same functions, not a reimplementation)
and by verification (145/150 = 96.7% raw match on a large sample, 100%
of the 5 divergences being intentional safety downgrades for
trade/re-acquisition edge cases the historical pipeline never had to
handle; 3/3 named real transition examples matched exactly).

**Is the SOG shadow overlay now prospectively observable?** Yes — every
call to `record_sog_observation` computes and stores the real, live
role state and the real, frozen-coefficient shadow probability
alongside the unmodified production probability, with a full
governing-field snapshot (role state, certainty, transition state,
games-since-transition, overlay version) for later independent
scoring. No observation has been recorded from a live/real game yet
(2026-27 season has not started); the pipeline is built and
unit/parity-tested, not yet exercised against a live day.

**Did anything here change a real probability, price, or bet decision?**
No. `decision_policy` was never imported by either new module; the
shadow path only ever writes to shadow-labeled columns; no `REAL_BET`
was ever recorded.

**Were Odds API credits used?** No — 0.

**Was the scheduler installed?** No, per standing instruction.

**What is explicitly NOT done and should not be assumed done:** Player
Props/Today/Game-Detail dashboard surfacing of PP role, a dedicated
System Health freshness component for this pipeline, explicit
preseason-vs-regular-season storage labeling (the PIT query happens to
produce correct fallback behavior without it, but the labeling itself
does not exist), and any live ingestion of a real 2026-27 game (none
have occurred yet).

---

Do not promote the role overlay into production. Do not start
PP_POINTS. Do not build Hits. Do not revisit PK Blocks. Do not change
betting policy.

**STOP AFTER THIS OPERATIONAL/MODEL-BRIDGE SPRINT.**
