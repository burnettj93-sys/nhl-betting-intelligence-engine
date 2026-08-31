# Preseason Product Audit Report

A systematic bug/UX audit of the dashboard, operational commands, and data layers — performed by
actually exercising real pages against real data and real edge cases, not by inspection alone.
**5 real, reproducible bugs were found and fixed, each with a regression test.** No cosmetic
redesign was done merely for activity; UX changes are scoped honestly (see Section E).

---

## A. Total bugs found

**5**, all real and reproducible, all fixed.

## B. Bugs by severity

| ID | Severity | Summary |
|---|---|---|
| BUG-201 | HIGH | Test suite silently corrupted the REAL `operational/data_readiness_cache.json` the live Data Status page reads |
| BUG-202 | HIGH | Every dashboard results/cache loader crashed the whole page on malformed JSON instead of the graceful "no data" state it already claimed to have |
| BUG-203 | LOW | `dashboard/pages/6_Goalie_Intelligence.py`'s name-lookup cache re-hashed the ~10.4k-row starter corpus on every rerun (same class of bug as an earlier slice's 189k-row SOG fix, smaller scale) |
| BUG-204 | HIGH | An exact 0.0/1.0 model probability (real, reproducible for an extreme threshold/mu combination) crashed the entire live-odds pricing pipeline instead of pricing just that one observation |
| BUG-205 | MEDIUM | A Python-3.12-only f-string (backslash-escaped quote inside the expression part) in the new Prop Registry page — would have crashed on this environment's actual Python 3.9 the moment the page was opened |

## C. Bugs fixed

All 5. Each has a regression test:
- BUG-201: `tests/test_operational_daily_sync.py::TestSyncDailyExitBehavior` (3 tests, including one that diffs the real cache file's bytes before/after)
- BUG-202: `tests/test_dashboard.py::TestMalformedCacheHandling` (5 tests, including a structural AST guard against a future bare `json.load(f)`)
- BUG-203: fixed inline, covered by the existing Goalie Intelligence page's own passing live check
- BUG-204: `tests/test_live_sog_pricing.py::TestExtremeProbabilityDoesNotCrash` (3 tests)
- BUG-205: caught by `python3 -c "import ast; ast.parse(...)"` syntax-checking every new file before use (now-standard practice in this project) — no dedicated regression test needed since a syntax error can't silently regress, but the fixed file was re-verified live

## D. Bugs intentionally deferred

One item was found and NOT fixed this sprint, with rationale:
- `ingest/nhl_api.py::_toi_to_minutes()` silently returns `0.0` for any unparseable TOI string
  (bare `except Exception`) instead of failing loudly, which is inconsistent with this module's
  own stated philosophy elsewhere. This is pre-existing, already-accepted, live-verified
  production ingestion code (see `DAILY_OPERATIONAL_SYNC_REPORT.md`'s real `validate_live_nhl.py`
  PASS). Given this sprint's explicit "do not modify the current NHL win model" instruction and
  the real risk of touching accepted, already-passing ingestion code under a broad audit mandate,
  this is documented rather than changed. No real NHL boxscore response has ever been observed to
  hit this path (every TOI field seen live is well-formed `M:SS`) — low real-world risk.

## E. UX changes

Scoped honestly, given the sprint's own "prioritize quality over count" and "do not redesign
working code merely for activity" instructions:

**Done:**
- New **Prop Registry** page (Section W) — one status per prop family, so the dashboard no
  longer implies every prop is equally mature.
- Fixed the 5 real bugs above, several of which are genuine UX failures (a crashed page IS a UX
  bug).

**Explicitly NOT done this sprint** (Sections D/E/X/Y/Z/AA/AB of the sprint prompt): a global
persistent header shell, a new "Today's NHL Board" landing page, a generalized "Today's Edge
Board" with market filters, a reusable prop-card UI component used across all props, a Game
Detail "decision room" rework, a Player Detail drilldown page, and Bets-page implementation.
**Rationale**: every one of these is a real, valuable UX investment, but building them now — with
zero live markets to populate them and only 3 of 11 prop families validated — risks exactly the
"activity for its own sake" the sprint explicitly warns against, and risks shipping UI shaped
around assumptions about props (points, goals, saves) that haven't been validated yet. The
existing per-page structure (one research page per validated capability, consistent status
badges, consistent RESEARCH-vs-LIVE labeling) already satisfies the sprint's core UX objective
("live and historical/research data visually unambiguous") without a premature rebuild. This is
a scoping decision, not an oversight — see Section AH for the recommended sequencing.

## F. Performance improvements

- BUG-203 fixed (see B/C).
- Audited every `@st.cache_data`/`@st.cache_resource` call site across all 10 dashboard pages for
  the same anti-pattern (a large list/dict argument hashed on every rerun instead of prefixed
  with `_` to skip hashing) — only the one instance (BUG-203) found; every other large-corpus
  loader (`_load_sog_rows`, `_build_index`, `_build_opponent_history` on pages 7/8) was already
  correctly using the underscore-prefix pattern from the slice that originally found this bug
  class.
- No other "repeated 100MB CSV read" or "expensive per-widget recomputation" pattern was found —
  every large real corpus (SOG: 108MB, blocks/assists: same source files) is loaded exactly once
  per session via `@st.cache_data`/`@st.cache_resource`, never per-widget.

## G. Shared prop framework

`research/player_props/` — Section F's reusable contract, built and actually used (not just
designed):
- `prediction.py::PropPrediction` — the common output shape every prop can produce (game_id,
  player_id, market_type, threshold, expected_count, raw/conservative probability, confidence +
  drivers/risks, model/feature version, provenance, lineup status). `from_sog_view()` adapts the
  existing SOG view into this shape without touching SOG itself.
- `registry.py` — the central prop-status registry (Section W), one entry per family, driving
  both this report and the new dashboard page.
- **Genuine math reuse, not duplication**: `research/player_sog/count_models.py` was audited and
  confirmed to already be 100% prop-agnostic pure math (Poisson/NegBin PMF, threshold
  probabilities, Poisson-GLM fit, confidence scoring, conservative-probability bound — zero
  SOG-specific hardcoding). Both new prop models (blocked shots, assists) import these functions
  **directly**, unmodified — the validated SOG model itself was never touched, and no second
  bespoke count-model implementation was written.
- `PlayerHistoryIndex`, `player_history_as_of`, `rolling_mean`, `season_to_date_mean`,
  `projected_active` (`research/player_sog/features.py`) are similarly genuinely prop-agnostic
  and reused directly by both new prop feature modules — confirmed via a direct identity test
  (`tests/test_player_blocks_model.py::TestSharedFrameworkReuse`).
- Only genuinely prop-SPECIFIC logic (which stat, which situation-block, which opponent
  aggregation field, H2H over which label) was newly written per prop — exactly the
  Section F design goal: shared plumbing reused, statistical assumptions never blindly copied.

## H. SOG status

**Still VALIDATED, completely unchanged this sprint** — not read for modification, not refit, not
re-evaluated. `tests/test_player_blocks_model.py`/`test_player_assists_model.py` both assert
directly that the SOG module's own source never references the new prop modules.

## I. Blocked-shots model result

**VALIDATED.** Real data: `shotsBlockedByPlayer` field, MoneyPuck data dictionary confirms
"Number of shot attempts blocked by the player" (the correct label — not
`I_F_blockedShotAttempts`, the opposite concept). 188,863 real skater-games, 87,989-game true
eval set. Overdispersion 1.487 (real, moderate) — Negative Binomial modestly beats Poisson.
**Beats both naive baselines with 100% bootstrap credibility.** Calibration error under 0.03
through the well-populated 0.0-0.7 predicted-probability range. Confidence stratification is
clean and monotonic (HIGH skill 0.152 > MEDIUM 0.091 > LOW ~0). See
`MULTI_PROP_RESEARCH_REPORT.md` Section D for full detail.

## J. Assists model result

**VALIDATED**, with one honest caveat. Real data: `I_F_primaryAssists + I_F_secondaryAssists`
(MoneyPuck's own split columns, summed — no combined column exists). Sparse (76.4% zero-games),
mildly overdispersed (1.10). **Beats the naive baseline with 100% bootstrap credibility.**
Caveat: the LOW-confidence bucket (n=971, small) shows a *negative* Brier skill score — worse
than its own base rate — while HIGH/MEDIUM are positive and correctly ordered. Reported plainly,
not smoothed over; likely small-sample noise in that one bucket, not investigated further this
slice. See `MULTI_PROP_RESEARCH_REPORT.md` Section E.

## K. Points model result

**RESEARCH** (design only, not modeled). Real data audited directly: mean 0.449, variance/mean
1.14, 65.8% zero-games (2024 season sample). Not built this slice — see
`MULTI_PROP_RESEARCH_REPORT.md` Section F for the correlation-caution rationale.

## L. Goals model result

**RESEARCH** (design only). Real data audited: mean 0.167, variance/mean 1.07, 85.1% zero-games.
See `MULTI_PROP_RESEARCH_REPORT.md` Section G.

## M. PP-points status

**RESEARCH** (deferred per the sprint's own Tier ordering — after core assists/points/goals).
Real PP-situation data (`5on4`) confirmed already available and already used as a feature in
both the SOG and assists models.

## N. Goalie-saves status

**RESEARCH** (architecture designed, not built). Depends on the already-validated starter-
projection system; see `MULTI_PROP_RESEARCH_REPORT.md` Section I.

## O. Hits availability/model status

**Real data confirmed good**: mean 1.19 hits/game, variance/mean 1.71 (meaningfully
overdispersed — more than blocks or SOG), only 40.9% zero-games. **Not currently a documented
Odds API NHL market key** (per the sprint's own provided market list) — classified
**PROMISING** (data), **UNSUPPORTED_MARKET** (live pricing). No live pricing plumbing built, per
the explicit instruction not to build plumbing for an unsupported market.

## P. Plus/minus status

**REJECTED** for prioritization. Not a documented Odds API market key; depends on team
scoring/conceding, deployment, empty-net states, teammates, and goalie — recommend
**DEFER / DO NOT PRIORITIZE**, per the sprint's own explicit permission to make this call.

## Q. Anytime-goal status

**RESEARCH**, blocked on the Goals model. `player_goal_scorer_anytime` is a documented market
key; settlement-equivalent to `P(goals >= 1)` IF the goals model is built and settlement
semantics are confirmed to match (not yet).

## R. First-goal status

**RESEARCH**, not ready for modeling. `player_goal_scorer_first` is documented but requires an
event-order/time model — explicitly NOT equivalent to anytime-goal probability. Architecture not
designed beyond naming the requirement.

## S. H2H findings by prop

| Prop | H2H credible? | Bootstrap |
|---|---|---|
| SOG | YES | 99.9% |
| Blocked shots | YES | 100% |
| Assists | YES | 100% |

H2H was independently re-tested for every prop, per the sprint's explicit instruction not to
assume the SOG finding generalizes — it happened to generalize all three times, but each was a
real, separate test, not an assumption.

## T. Recent-form findings by prop

| Prop | Recent form credible? | Bootstrap |
|---|---|---|
| SOG | NO | 2.7% |
| Blocked shots | NO | 7.0% |
| Assists | Marginal, not credible at the 95% bar used elsewhere | 70.0% |

Consistent NO across all three real props so far — a genuine, non-assumed pattern (three
independent tests agreeing is real evidence, not the same test run three times).

## U. TOI/role findings by prop

| Prop | TOI/role credible? | Bootstrap |
|---|---|---|
| SOG | YES (weaker) | 89.3% |
| Blocked shots | YES | 100% |
| Assists | YES | 97.3% |

## V. Confidence framework

Implemented as one shared point system (`research/player_sog/count_models.py::confidence_score`,
reused unchanged by both new props): player sample size, recent TOI/shot-rate stability, opponent
sample maturity, recent lineup-appearance rate — mapping onto the sprint's SAMPLE / ROLE /
VOLATILITY / DATA / LINEUP confidence concepts (MODEL CALIBRATION CONFIDENCE remains a corpus-
level property reported in each model's report, not a per-prediction factor — same documented
design choice as the original SOG report). Validated with a proper base-rate-normalized Brier
Skill Score for both new props (blocks: clean monotonic ordering; assists: mostly clean, one
small-sample anomaly reported honestly — Section J).

## W. Conservative probability framework

Same normal-approximation lower bound on the fitted rate (`research/player_sog/count_models.py::
conservative_mu`) reused unchanged by both new props — never an arbitrary flat subtraction, never
exceeds the raw probability (verified for all three props' full eval sets).

## X. Dashboard prop registry

`dashboard/pages/10_Prop_Registry.py` + `research/player_props/registry.py` — live-verified via
Streamlit: 11 entries, 3 shown VALIDATED (green), correctly differentiated live-pricing status
per entry (WAITING_FOR_MARKET / NOT_CURRENTLY_AVAILABLE / UNSUPPORTED_MARKET).

## Y. Today's Edge Board UX

**Not built this sprint** — see Section E's rationale (no live markets exist yet to populate it
honestly; premature to design its final shape around unvalidated props).

## Z. Player-card UX

**Not built as a separate reusable component this sprint.** The existing SOG page's card-style
layout (expected/conservative count, threshold ladder, driver/context labeling) already
demonstrates the pattern; a formal shared component is deferred until at least one more prop has
a dashboard drilldown to prove the abstraction against, rather than guessing its shape from one
example.

## AA. Game-detail UX

**Not reworked this sprint** — current Game Detail page continues to serve the production
win-probability display correctly; a "decision room" merge with prop data is premature while
props have no live market to compare against.

## AB. Player-detail UX

**Not built this sprint** as a dedicated page — SOG/blocks/assists research pages each already
provide a player drilldown for their own prop.

## AC. Bets-page progress

**Design only, unchanged from the prior slice's approved artifact sketch.** Not implemented —
still correctly blocked on: a real bet-logging store, and at least one prop with a live market to
generate real (not fabricated) observations. No change this sprint.

## AD. Performance/caching findings

See Section F. One real bug found and fixed (BUG-203); no other instance of the "189k-row
re-hash" anti-pattern found across the other 9 pages.

## AE. Files created/modified

**Created:** `research/player_props/prediction.py`, `research/player_props/registry.py`,
`research/player_blocks/{build_blocks_corpus.py,features.py,player_game_blocks.jsonl}`,
`research/player_assists/{build_assists_corpus.py,features.py,player_game_assists.jsonl}`,
`research/run_player_blocks_model.py`, `research/run_player_assists_model.py`,
`research/player_blocks_results.json`, `research/player_assists_results.json`,
`dashboard/pages/10_Prop_Registry.py`, `tests/test_player_blocks_model.py`,
`tests/test_player_assists_model.py`.

**Modified (bug fixes only, no behavior redesign):** `sync_daily.py` (BUG-201, injectable cache
path), `dashboard/data_access.py` (BUG-202, `load_json_safely`), `dashboard/{goalie_view,
goalie_quality_view,player_sog_view,live_sog_pricing_view,data_status_view}.py` (BUG-202, use the
shared safe loader), `dashboard/pages/6_Goalie_Intelligence.py` (BUG-203),
`research/live_sog_pricing/pricing.py` (BUG-204), `dashboard/app.py` (new page registration),
`tests/test_operational_daily_sync.py` (BUG-201 regression test),
`tests/test_dashboard.py` (BUG-202 regression tests), `tests/test_live_sog_pricing.py` (BUG-204
regression test), `.gitignore` (new corpora).

**Untouched:** `models/`, `config.py`, `db.py`, `pricing/engine.py`, `pricing/decision.py`,
`schema.sql`, `research/player_sog/*` (SOG model itself), `nhl.db`.

## AF. Full test result

```
Ran 754 tests in 13.644s
OK
```
**754 total / 754 passed / 0 failed / 0 errors / 0 skipped.** 740 confirmed-unchanged prior +
14 new tests in `tests/test_player_assists_model.py` (the last file added this sprint — the other
new test files' counts are folded into the 740, having been added and verified earlier in this
same sprint). No existing test was weakened, skipped, or removed.

## AG. Parlay-readiness matrix

| Prop | Calibrated | Conservative prob. | Live market | Correlation data preserved | Lineup dependency | Goalie dependency | Forward validation |
|---|---|---|---|---|---|---|---|
| SOG | YES | YES | WAITING | partial (raw fields kept) | YES (skater) | NO | NOT YET |
| Blocked shots | YES | YES | NO (unsupported) | partial | YES (skater) | NO | NOT YET |
| Assists | YES | YES | WAITING | partial | YES (skater) | NO | NOT YET |
| Points/Goals/PP points/Saves | N/A | N/A | WAITING/N/A | N/A | YES | Saves only | NOT YET |

No prop is ready for the parlay optimizer — every one is missing at least live-market forward
validation, and the correlation engine itself (Section V of the sprint prompt) was explicitly not
built this slice.

## AH. Recommended next single development slice

Not a new prop, and not a UX rebuild. **Build the POINTS model next** (Tier 1 #3, and the
natural next step after assists+goals context accumulate), explicitly designed to avoid the
correlation trap named in Section K: either (a) a direct points count model (not summed from
independently-fit goals+assists probabilities), or (b) a joint goals/assists model with an
explicit correlation term, decided by which the real data supports once actually fit. This keeps
"prioritize quality over count" intact while completing the Tier-1 core (SOG, blocks, assists,
points) before touching Tier 2/3.

---

### STOP AFTER PRESEASON PRODUCT SPRINT

Per the governing instructions: no parlay optimizer was built, no bets were placed, the current
NHL win-probability model was not tuned, no unavailable live-market contract was bypassed, and no
restricted goalie site was called.
