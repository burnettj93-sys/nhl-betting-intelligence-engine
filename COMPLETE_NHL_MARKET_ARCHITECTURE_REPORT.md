# Complete NHL Market Universe + Dependency Architecture

**This is an architecture slice: no new models were built, nothing was refit, and no play-by-play
corpus was ingested.** One real, live, single-game NHL play-by-play API call was made to audit the
real data contract (Section Q) — not bulk ingestion. Every DERIVABLE/VALIDATED claim below traces
to an already-existing result file from this project; every NOT_BUILT claim is honest, not a
placeholder.

**The headline finding:** of **164 raw sportsbook labels**, only **142 are actually distinct
statistical events** — the rest are the same target under a different sportsbook label. Of those
142, **21 are already mathematically derivable** from the five prop models already built this
session, and **12 are genuinely validated**. The single highest-leverage unfinished foundation is
**event timing (play-by-play)**, gating **62 of the remaining 130 markets** — more than four times
the leverage of the next-largest gap. **PP Points is not the highest-leverage next slice.**

---

## A. Total raw sportsbook labels supplied

**164** — computed directly from `research/player_props/market_registry.py::RAW_MARKET_LABELS`
(`total_raw_labels()`), not hand-counted. Breakdown by category:

| Category | Raw labels |
|---|---|
| Player Goals/Scoring | 15 |
| Player Points | 6 |
| Player Assists | 5 |
| Player SOG | 9 |
| Player Blocked Shots | 5 |
| Player Hits | 5 |
| Player Special Teams | 6 |
| Player Penalties | 2 |
| Player Faceoffs | 3 |
| Player Usage/Other | 2 |
| Goalie | 15 |
| Team | 23 |
| Game/Outcome | 34 |
| Period Markets | 34 |
| **Total** | **164** |

## B. Total unique canonical markets

**142** — computed via `total_canonical_markets()`. Every canonical entry passed a structural
validation pass (`_validate_registry()`): no duplicate `market_id`, every `underlying_process`
references one of the 17 real process families, every `derivation_type` is one of the 5 real
derivation types.

## C. Aliases consolidated

**22** raw labels collapsed into an existing canonical entry rather than becoming their own market
— the clearest examples: `"Power-play goal"` and `"Game-winning goal"` each appear twice in the raw
list (once under Goals, once under Special Teams) and resolve to ONE canonical market each;
`"Anytime goal scorer"`, `"Goals O/U"` (0.5 line), and `"1+ Goal"` all resolve to the single
canonical `PLAYER_GOALS_1PLUS`; every `N+` threshold market also absorbs its generic `"X O/U"`
sportsbook label as an alias (e.g. `"SOG O/U"` → `PLAYER_SOG_2PLUS`/`PLAYER_SOG_3PLUS` depending on
the posted line, via the SAME `threshold_from_point()` line-to-threshold conversion already
implemented and reused unchanged in `research/live_sog_pricing/pricing.py`).

## D. Every canonical market

The full, machine-readable list is `research/player_props/market_registry.py::CANONICAL_MARKETS`
(142 entries) — not reproduced line-by-line here; Sections M/N/O below summarize it by status, and
the registry file itself is the authoritative source, exactly as Part 30 requires.

## E. Underlying process for every market

Every one of the 142 canonical markets carries an explicit `underlying_process` tuple drawn from
the 17 families in Part 2 (`PROCESS_FAMILIES`) — confirmed sufficient; no 18th foundation was found
necessary during this exercise. See Section AD for per-process market counts.

## F. Derivation type

Every market is tagged exactly one of `DIRECT_MODEL`, `DISTRIBUTION_THRESHOLD`, `EVENT_TIME`,
`SIMULATION`, `COMPOSITE`:

| Derivation type | Count | Meaning |
|---|---|---|
| EVENT_TIME | 64 | requires knowing WHEN events happen, not just how many |
| DISTRIBUTION_THRESHOLD | 56 | one coherent count/rate distribution, read at different thresholds |
| SIMULATION | 11 | derivable from a joint final-score distribution alone, no event timing needed |
| COMPOSITE | 10 | combines two processes multiplicatively/conditionally (e.g. goalie win = P(starts) × P(team wins)) |
| DIRECT_MODEL | 1 | a standalone distribution fit directly on its own target (e.g. team SOG) |

## G. Historical-data status

| Status | Count | Meaning |
|---|---|---|
| REQUIRES_PLAY_BY_PLAY | 64 | genuinely does not exist in this project's current corpora at all |
| AVAILABLE_USED | 35 | real data, already captured in an existing corpus, already used as a model target |
| AVAILABLE_UNUSED | 30 | real data confirmed present in already-downloaded files, never used as a target |
| AVAILABLE_UNUSED_AS_STANDALONE_TARGET | 9 | real data used only as a CONTEXT feature for player props, never its own target |
| REQUIRES_NEW_EXTRACTION | 4 | raw situation-level rows exist but were never parsed for this specific split |

## H. Current-model status

See Section D's registry; summarized in Sections M/N.

## I. Threshold-validation status

Preserved exactly per prop (Section D of each prop's own report) — never re-litigated or upgraded
by this architecture exercise. See Sections M/N and the per-threshold tables in Part 4-8 audits
below.

## J. Provider-support status

No Odds API credits were spent this slice (Part 32's explicit instruction). Only the 7 market keys
already confirmed real and in use in `research/player_props/registry.py` this session are marked
`SUPPORTED`: `player_shots_on_goal`, `player_assists`, `player_points`, `player_goals`,
`player_power_play_points`, `player_total_saves`, `player_goal_scorer_anytime`. 5 are marked
`UNSUPPORTED_MARKET` (no documented Odds API key exists at all, e.g. Hits, Plus/Minus). The
remaining **130 are honestly `UNKNOWN`** — not checked, not assumed either way.

## K. Real DK contract-verification status

**0 markets have a real, live-payload-verified DraftKings contract.** Every SUPPORTED market above
is "documented Odds API market key exists" — not the same as "a real DK payload for this exact
market was inspected during live NHL games" (no live NHL props are currently posted; confirmed
multiple times this session). `dk_contract_verified=False` for all 142 entries, honestly.

## L. Existing models mapped into architecture

| Model | Process | Status |
|---|---|---|
| SOG | PLAYER_SHOT_GENERATION | VALIDATED (2+ through 5+); INSUFFICIENT_TAIL_DATA beyond |
| Blocked Shots | PLAYER_BLOCK_EVENT_GENERATION | VALIDATED (1+ through 3+) |
| Assists | GOAL_ASSIST_POINT_ATTRIBUTION | VALIDATED (1+, 2+); INSUFFICIENT_DATA at 3+ |
| Points | GOAL_ASSIST_POINT_ATTRIBUTION | EMPIRICAL_BASELINE_REMAINS_CHAMPION (1+, 2+ usable, not a "validated new model"); INSUFFICIENT_DATA at 3+ |
| Goals | PLAYER_GOAL_GENERATION | VALIDATED (1+); INSUFFICIENT_DATA at 2+/3+ |
| **PLAYER_ACTIVE_ROLE_TOI** | (foundational) | **Already fully built** — `PlayerHistoryIndex`, `projected_active()`, and every rolling-TOI feature are shared, unmodified across all 5 models above. This is the one process Part 9 asked to "design"; it already exists. |

## M. Markets already derivable today

**21** (`derivable_today()`) — every threshold instantiation of the 5 already-fitted count/
empirical models above, *regardless* of whether that specific threshold cleared bootstrap
validation (Part 3's required distinction: SOG 6+/7+/8+ and Points/Assists/Goals' unsupported tails
are mathematically derivable from the SAME already-fitted distribution, just not separately
validated).

## N. Markets genuinely validated today

**12** (`validated_today()`): SOG 2+/3+/4+/5+ (4), Blocks 1+/2+/3+ (3), Assists 1+/2+ (2), Points
1+/2+ (2, via the champion baseline — explicitly NOT relabeled as a new validated model), Goals 1+
(1).

## O. Unsupported tails

SOG 6+/7+/8+, Blocks 4+, Assists 3+, Points 3+, Goals 2+/3+ — all `INSUFFICIENT_TAIL_DATA` or
`INSUFFICIENT_DATA`, all preserved exactly as each prop's own report already concluded. **Not one
tail status was upgraded or downgraded by this architecture review.**

## P. Processes still missing

**12 of 17** process families have **no validated or champion-baseline market yet** — only
`PLAYER_ACTIVE_ROLE_TOI`, `PLAYER_SHOT_GENERATION`, `PLAYER_BLOCK_EVENT_GENERATION`,
`PLAYER_GOAL_GENERATION`, and `GOAL_ASSIST_POINT_ATTRIBUTION` have any validated/champion coverage.
The 12 still missing: `PLAYER_HIT_EVENT_GENERATION`, `SPECIAL_TEAMS_STATE`, `PENALTY_PROCESS`,
`FACEOFF_PROCESS`, `GOALIE_WORKLOAD_SAVE_PROCESS`, `TEAM_SHOT_GENERATION`, `TEAM_GOAL_GENERATION`,
`PERIOD_EVENT_TIMING`, `GAME_SCORE_STATE`, `EMPTY_NET_STATE`, `OT_SHOOTOUT_STATE`,
`JOINT_DEPENDENCE_SIMULATION`. See Section AD for leverage ranking.

## Q. Play-by-play contract findings

**Real, live-verified** (one GET request to `https://api-web.nhle.com/v1/gamecenter/2022020001/
play-by-play`, a real historical game already in this project's own corpus — 200 OK, 131KB, 323
real events) — not assumed from general knowledge:

| Field | Confirmed present |
|---|---|
| Game ID | `id` (top-level) |
| Period | `periodDescriptor.number`, `.periodType` |
| Time | `timeInPeriod`, `timeRemaining` |
| Event type | `typeDescKey` (real values observed: `period-start`, `faceoff`, `hit`, `shot-on-goal`, `missed-shot`, `blocked-shot`, `goal`, `penalty`, `giveaway`, `takeaway`, `stoppage`, `period-end`, `game-end`) |
| Team | `details.eventOwnerTeamId` |
| Player IDs | present on every event type, named per role (see below) |
| Goal | `scoringPlayerId`, `scoringPlayerTotal`, `assist1PlayerId`, `assist2PlayerId`, `goalieInNetId`, `awayScore`/`homeScore` (running score state) |
| Shot/miss/block | `shootingPlayerId`, `goalieInNetId` (shot/miss), `blockingPlayerId` + `shootingPlayerId` (block), `awaySOG`/`homeSOG` (running totals) |
| Hit | `hittingPlayerId`, `hitteePlayerId` |
| Faceoff | `winningPlayerId`, `losingPlayerId` |
| Penalty | `committedByPlayerId`, `drawnByPlayerId`, `typeCode` (e.g. "MIN"), `descKey` (e.g. "slashing"), `duration` |
| Strength/manpower state | `situationCode` (a 4-digit code, e.g. `"1551"`) |

**This CAN become the canonical event-time truth source** — every field Part 20 asked about is
confirmed real and present, at the granularity needed for every EVENT_TIME market in this registry.

## R. Estimated event-level corpus requirement

Real, computed (not guessed): **5,248 real regular-season games** exist in this project's own
4-season corpus (`research/real_nhl_results/normalized_regular_season_games.jsonl`). The one real
sample game returned **323 events** in a **131KB** JSON payload.

| | Estimate |
|---|---|
| API requests | 5,248 (one per game — same order of magnitude as the boxscore ingestion already performed for these exact games) |
| Raw storage | ~5,248 × 131KB ≈ **690 MB** (uncompressed JSON; real per-game sample size × real game count, not a guess) |
| Total events | ~5,248 × 323 ≈ **1.7 million** (order-of-magnitude estimate from n=1 game; real variance expected from OT/shootout games) |
| Normalization complexity | Moderate — the event schema is already flat and well-typed per `typeDescKey`; the main work is period-relative time-to-absolute-time conversion and per-team/per-player rollups, not schema archaeology |

**Not ingested this slice** (Part 21's explicit instruction) — this is a sizing estimate only.

## S. Goalie dependencies

`GOALIE_WORKLOAD_SAVE_PROCESS` requires `TEAM_SHOT_GENERATION` (shots faced = opponent's team SOG).
The **starter-probability layer already exists** (referenced throughout this session as "the
already-validated starter-projection system") — the missing piece is a **conditional-on-start saves
count distribution**, not the starter layer itself. `GOALIE_WIN` is the cheapest goalie market to
build (Section AD) since it only needs `P(starts) × P(team wins)`, and the production NHL win model
already estimates the second factor.

## T. Penalty dependencies

**Real data confirmed available and unused**: `I_F_penalityMinutes`, `penalties` fields exist in the
same already-downloaded MoneyPuck skater CSVs used by every prop model this session (confirmed from
the very first raw-column audit of this project). Real play-by-play (Section Q) additionally
confirms `committedByPlayerId`/`drawnByPlayerId`/`typeCode`/`descKey`/`duration` at event grain —
either source alone is sufficient for `PLAYER_PIM_OU`; the play-by-play source is required for any
event-time penalty market (e.g. timing of the penalty that led to a PP goal).

## U. Faceoff dependencies

**Real data confirmed available and unused**: `I_F_faceOffsWon`, `faceoffsWon`, `faceoffsLost`
fields exist in the same raw CSVs. Play-by-play confirms `winningPlayerId`/`losingPlayerId` at
event grain, sufficient to reconstruct player-level faceoff win% with zone context if ever needed.

## V. Special-teams dependencies

PP-situation data (`pp.individual_xg`, `pp.sog`, `pp.goals`) is **already captured** in the
Goals/Points/Assists corpora built this session — never turned into its OWN standalone PP-point/
PP-goal distribution target. This is the **prior PP Points recommendation, now correctly evaluated
by market leverage rather than sprint ordering** (Part 11): it unlocks `PLAYER_PP_POINT`,
`PLAYER_PP_GOAL`, `TEAM_PP_GOAL_ANYTIME`, `TEAM_PP_GOALS_TOTAL` directly from data already on disk
— genuinely low-effort, but a leverage count of exactly 13 markets, well below `PERIOD_EVENT_TIMING`'s 62.

## W. Team/game dependencies

`TEAM_GOAL_GENERATION` and `TEAM_SHOT_GENERATION` are the biggest **currently-missing-but-cheap**
foundations: the exact `team_game_totals`-style aggregation pattern already used as a CONTEXT
feature in every player prop this session could be turned into its OWN coherent count-distribution
target (Poisson/NegBin on team goals, exactly like the player-level models) **without any new data
collection at all** — see `TEAM_SOG_TOTAL`'s notes in the registry. `TEAM_GOAL_GENERATION` alone
gates 24 markets (Section AD), second only to event timing.

## X. Period dependencies

**Every one of the 33 real period-specific raw labels requires play-by-play** (`PERIOD_EVENT_TIMING`)
by construction — full-game marginal probabilities cannot safely be split by period without
knowing WHEN goals/shots/etc. actually occurred (empty-net effects, score-effects on shot rates,
etc. all vary meaningfully by period in real hockey).

## Y. Event-time dependencies

43 canonical markets are tagged `EVENT_TIME`: first/last scorer (player and team), goal-in-period,
game-winning goal, first-goal timing/method, race-to-N, lead markets, come-from-behind, any-empty-
net/SH/PP-goal-in-game, and the entire period-market tail. **Confirmed explicitly: P(first goal)
CANNOT be safely derived from P(anytime goal) alone** (Part 23) — anytime-goal is a marginal
per-player probability; first-goal is a competing-risk problem across every skater on both teams,
weighted by real ice time and matchup context that a marginal model discards.

## Z. Simulation-state design

Minimum game state for a future simulator (Part 22), confirmed sufficient by cross-referencing
against the real play-by-play fields in Section Q: `period`, `game_clock` (`timeInPeriod`/
`timeRemaining` already exist as real fields), `home_score`/`away_score` (`homeScore`/`awayScore`
already exist as running totals on every real goal event), `manpower_state` (`situationCode` already
exists), `active_goalies` (`goalieInNetId` already exists per shot/goal event), `empty_net_status`
(derivable: no `goalieInNetId` present, or a known pulled-goalie situation code), `OT_state`/
`shootout_state` (`periodDescriptor.periodType` already distinguishes `REG`/`OT`, and the top-level
payload carries `shootoutInUse`/`otInUse` flags — confirmed real). **Team possession/attack state
was NOT found necessary** — no requested market in this registry needs it.

## AA. Simulation invariants

See [`SIMULATION_INVARIANTS.md`](SIMULATION_INVARIANTS.md) — 13 formal invariants, each written to
be directly testable once a simulator exists (none does yet).

## AB. Joint/parlay dependencies

| Combination | Dependence type |
|---|---|
| SOG Over + Anytime Goal | POSITIVE DEPENDENCE (shared shot-volume driver) |
| Goal + 1+ Point | STRUCTURAL DEPENDENCE (a goal IS a point, by definition — not merely correlated) |
| Assist + Teammate Goal | DIRECT EVENT DEPENDENCE (the same physical event) |
| Goalie Saves Over + Opponent SOG Over | STRONG STRUCTURAL DEPENDENCE (saves ≈ f(shots faced)) |
| Player Goal + Team Win | GAME-STATE DEPENDENCE (confirmed empirically relevant to production win-model-adjacent context throughout this session) |
| Player Goal + Game Over (total) | SCORING-ENVIRONMENT DEPENDENCE (shared team/game pace driver) |
| Cross-game combinations | LIKELY WEAKLY DEPENDENT to APPROXIMATELY INDEPENDENT — but this project has never empirically tested even that "weak" claim; it requires validation before operational use, not an assumption (Part 37's explicit final caveat). |

**No correlation was quantified this slice** (Part 27 permits this only "where existing data make it
trivial" — none of the above qualifies as trivial).

## AC. Dependency graph

[`research/player_props/dependency_graph.py`](research/player_props/dependency_graph.py) —
`PROCESS_DEPENDENCY_GRAPH` (17 process nodes, confirmed **acyclic** via direct DFS check,
`is_acyclic() == True`) plus `market_process_dependencies()` (re-derived live from the registry
itself, so the two structures can never silently drift apart).

## AD. Market-leverage counts

Real, computed (`dependency_graph.unfinished_process_leverage()`), counting only canonical markets
not already VALIDATED/champion-usable:

| Process | Markets gated (not yet validated) |
|---|---|
| **PERIOD_EVENT_TIMING** | **62** |
| TEAM_GOAL_GENERATION | 24 |
| PLAYER_ACTIVE_ROLE_TOI | 14 |
| GAME_SCORE_STATE | 14 |
| PLAYER_GOAL_GENERATION | 13 |
| SPECIAL_TEAMS_STATE | 13 |
| GOALIE_WORKLOAD_SAVE_PROCESS | 9 |
| TEAM_SHOT_GENERATION | 8 |
| PENALTY_PROCESS | 6 |
| FACEOFF_PROCESS | 5 |
| OT_SHOOTOUT_STATE | 5 |
| PLAYER_SHOT_GENERATION | 4 |
| PLAYER_HIT_EVENT_GENERATION | 4 |
| GOAL_ASSIST_POINT_ATTRIBUTION | 4 |
| JOINT_DEPENDENCE_SIMULATION | 4 |
| PLAYER_BLOCK_EVENT_GENERATION | 2 |
| EMPTY_NET_STATE | 2 |

**Event timing has more than double the leverage of the next-largest gap** (62 vs. 24).

## AE. Prioritized development roadmap

Combining leverage (Section AD), data availability (Sections T/U/V/W), modelability, dependency
centrality (`PLAYER_ACTIVE_ROLE_TOI`/`PLAYER_SHOT_GENERATION`/`TEAM_SHOT_GENERATION` sit underneath
many others), implementation cost, and live provider support (Section J):

1. **Real play-by-play ingestion pilot** (small: 1 real season, not all 4) — unlocks the single
   largest leverage pool (62 markets) and is the prerequisite for `GAME_SCORE_STATE`,
   `EMPTY_NET_STATE`, `OT_SHOOTOUT_STATE`, and ultimately `JOINT_DEPENDENCE_SIMULATION` itself. Real
   field contract already confirmed (Section Q) — this is now a scoping/cost decision, not a
   feasibility unknown.
2. **TEAM_GOAL_GENERATION** (a coherent team-goals count distribution, reusing the ALREADY-BUILT
   `team_game_totals` aggregation pattern with zero new data collection) — 24 markets, genuinely
   cheap given existing infrastructure, and a real prerequisite for most GAME_OUTCOME
   `SIMULATION`-type markets (Section F: 7 markets are derivable from final-score simulation alone,
   without event timing).
3. **Special-teams state** (PP Points/PP Goals/Team PP totals) — 13 markets, data already on disk
   in 3 existing corpora, the correct "next PP Points"-shaped slice once evaluated honestly by
   leverage rather than automatic sprint-order continuation.
4. **Goalie saves distribution**, conditional on the already-existing starter layer — 9 markets,
   moderate cost (needs its own count-distribution fit, same discipline as SOG/Blocks/Assists).
5. Penalty/faceoff/hits count-distribution fits — lower leverage individually (5-6 each) but
   genuinely cheap given confirmed unused data; reasonable to batch together in one slice.
6. Full joint game simulation and same-game parlay dependence modeling — correctly last; every
   other item above is a real prerequisite for it (Section AC's dependency graph, `JOINT_DEPENDENCE_
   SIMULATION`'s own 7-process prerequisite list).

## AF. UX roadmap implications

Every planned page (Today's Edge Board, Player/Goalie/Team/Game/Period Props, Best Combinations,
Parlay Builder, Game/Player Detail, Bet Ledger) can be driven directly from
`market_registry.CANONICAL_MARKETS` filtered by `category`/`model_status`/`odds_api_support` — no
page-specific data model is needed. **Not built this slice** (Part 38's explicit instruction);
this is only a confirmation that the registry's shape already supports it.

## AG. Files created/modified

**New:**
- `research/player_props/market_registry.py` (142 canonical markets, 164 raw labels)
- `research/player_props/dependency_graph.py`
- `SIMULATION_INVARIANTS.md`
- `COMPLETE_NHL_MARKET_ARCHITECTURE_REPORT.md` (this file)
- `tests/test_market_architecture.py` (27 tests)

**Modified:** none. No existing prop model, confidence framework, decision policy, or production
file was touched — verified via `git status --porcelain` showing zero "M" entries for any of them.

## AH. Full test result

**1,029 / 1,029 passing** (1,002 prior + 27 new architecture tests). Confirmed via
`python3 -m unittest discover tests`.

## AI. Recommended NEXT single development slice

**A small, real play-by-play ingestion pilot for ONE season** (not all 4) — build the real
event-level corpus for a single season, validate the accounting identities in
`SIMULATION_INVARIANTS.md` against it, and confirm `TEAM_GOAL_GENERATION`/`GAME_SCORE_STATE` can be
derived cleanly before committing to the full 4-season, ~690MB ingestion. This directly targets the
single highest-leverage gap found in this architecture review (62 markets), is bounded in scope
(one season, not the full corpus), and produces the real event-time data every other high-leverage
item in the roadmap (Section AE items 1-2) ultimately depends on.

---

## Final Questions

**HOW MANY RAW MARKET LABELS WERE PROVIDED?** 164.

**HOW MANY UNIQUE CANONICAL MARKETS EXIST AFTER ALIAS NORMALIZATION?** 142.

**HOW MANY CAN ALREADY BE DERIVED MATHEMATICALLY FROM CURRENT MODELS?** 21.

**HOW MANY HAVE SUFFICIENT VALIDATION TO BE CONSIDERED CURRENTLY USABLE?** 12.

**HOW MANY REQUIRE NEW MARGINAL MODELS?** 29 (NOT_BUILT, and needing neither play-by-play nor joint
simulation — the "cheap wins": special teams, penalties, faceoffs, hits, goalie saves, team-level
distributions).

**HOW MANY REQUIRE PLAY-BY-PLAY / EVENT-TIME DATA?** 65.

**HOW MANY REQUIRE GAME-STATE OR JOINT SIMULATION?** 40.

**HOW MANY CURRENTLY HAVE KNOWN ODDS-PROVIDER SUPPORT?** 7 SUPPORTED (real, already-used market
keys), 5 UNSUPPORTED_MARKET (confirmed no key exists), 130 UNKNOWN (honestly not checked, per Part
32's explicit instruction not to spend credits on this exercise).

**WHICH EXISTING MODEL UNLOCKS THE MOST CURRENT MARKETS?** The shared `PLAYER_ACTIVE_ROLE_TOI`
foundation (`PlayerHistoryIndex`, `projected_active()`) — every one of the 5 built prop models
depends on it, and it already exists, fully built, reused unmodified across all of them.

**WHICH UNFINISHED FOUNDATION WOULD UNLOCK THE MOST ADDITIONAL MARKETS?** `PERIOD_EVENT_TIMING`
(play-by-play) — 62 markets, more than double the next-largest gap (`TEAM_GOAL_GENERATION`, 24).

**IS PP POINTS ACTUALLY THE HIGHEST-LEVERAGE NEXT DEVELOPMENT SLICE?** NO.

**DO WE NEED A SEPARATE MODEL FOR EVERY ALT THRESHOLD?** NO.

**CAN ANYTIME GOAL / GOALS O0.5 / 1+ GOAL SHARE ONE UNDERLYING PROBABILITY?** YES.

**CAN FIRST GOAL BE SAFELY DERIVED FROM ANYTIME GOAL ALONE?** NO.

**CAN LAST GOAL BE SAFELY DERIVED FROM ANYTIME GOAL ALONE?** NO.

**CAN SAME-GAME PARLAY PROBABILITY GENERALLY BE COMPUTED BY MULTIPLYING LEG PROBABILITIES?** NO.

**WILL THE FULL REQUIRED PRODUCT EVENTUALLY NEED A COHERENT JOINT GAME SIMULATION / DEPENDENCE
ENGINE?** YES.

**WERE ANY VALIDATED RAW MODELS CHANGED?** NO.

**WAS CONFIDENCE CHANGED?** NO.

**WAS DECISION POLICY v2 CHANGED?** NO.

**WAS NHL WIN MODEL CHANGED?** NO.

**CURRENT FULL TEST RESULT?** 1,029 / 1,029.

**WHAT IS THE NEXT HIGHEST-LEVERAGE DEVELOPMENT SLICE?** A small, single-season real play-by-play
ingestion pilot (Section AI) — not PP Points, not the full 4-season corpus, not the game simulator
itself.
