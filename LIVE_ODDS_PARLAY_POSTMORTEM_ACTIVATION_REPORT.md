# Live Odds Collection + Parlay + Post-Mortem Activation Report

Written 2026-09-15. This is the closing report for the "Live Odds Collection +
SOG/Saves Priority + 70% Game Edge Parlays + Daily Post-Mortem" activation
sprint.

## A. Starting Baseline

- **HEAD at start:** `c1b60d7` (Yahoo Fantasy Hockey Phase 1 integration)
- **Test count at start:** 2,469 / 2,469 passing
- **Working tree:** clean, no unrelated unfinished work found

## B. Odds Collection Strategy

The owner explicitly authorized automated collection this sprint. Live
verification (0-credit call) confirmed 33 real NHL events already listed
by The Odds API, earliest 2026-09-29 — matching the owner's own
observation and confirming DraftKings prices games well ahead of the
2026-09-19 preseason start.

The pre-existing `PRESEASON_LEAD_DAYS = 2` gate would have kept the
collector inactive for two more days despite this real evidence, so it
was raised to `7` (backed by the live verification above, documented in
`operational/live_odds_daily_pull.py`'s own comment) to begin collection
today, per explicit owner instruction.

Three distinct collection mechanisms now exist:
1. **Moneyline snapshots** (`run_moneyline_snapshot`) — cheap, verified
   MONEYLINE contract, run at fixed daily times, label auto-derived from
   wall-clock hour (morning/afternoon/pregame/late).
2. **Two-stage targeted prop sweep** (`run_targeted_prop_sweep`) —
   first sweep 3-4.5h before puck drop (SOG+Saves only), second sweep
   45-75 min before puck drop (re-pulls only events the first sweep
   found a real quote for).
3. **Daily broad props pull** (`run_daily_pull`, pre-existing, unchanged
   logic) — once-daily budget-gated sweep of all player-prop markets.

## C. Monthly Credit Allocation

Unchanged, real, dynamic budget math (pre-existing, verified working):
`daily_budget = (real_remaining - safety_floor) / days_left_in_cycle`,
safety floor 20, cycle reset day 1. Target ≈16-17 credits/day at a full
500/30 cadence; soft ceiling 20/day is a natural consequence of the same
formula early in a cycle, never a separately enforced cap. Monthly hard
ceiling: 500 (the account's real plan limit).

## D. Scheduler

**Installed and active** — 4 launchd jobs loaded via `launchctl load`,
confirmed via `launchctl list`:

| Job | Schedule | Purpose |
|---|---|---|
| `com.nhlengine.moneyline-snapshot` | 08:00, 13:00, 17:00, 20:00 daily | Part 8 moneyline snapshots |
| `com.nhlengine.daily-props-pull` | 08:15 daily | Secondary-priority props (points/assists/goals) |
| `com.nhlengine.prop-sweep-first` | every 30 min | SOG+Saves, 3-4.5h before puck drop |
| `com.nhlengine.prop-sweep-second` | every 15 min | Re-pull, 45-75 min before puck drop |

Per Part 72, the exact production commands were run manually first:
moneyline snapshot (real, 20 credits spent, 20/20 events parsed
successfully via the verified h2h contract) and the full props pull
(real, 0 credits spent — no player-prop markets are posted yet, an
honest and expected result). Both proved the pipeline end-to-end before
the scheduler was loaded. The scheduler has since fired for real
(`prop-sweep-first`/`prop-sweep-second` both ran once already, correctly
found nothing in their time windows, spent 0 additional credits).

The old single-job `com.nhlengine.odds-daily-pull.plist` template was
superseded by the 4 jobs above; the file itself was left in place
(historical reports reference it by name) but never installed.

## E. SOG Priority

`FIRST_SWEEP_MARKETS = "player_shots_on_goal,player_total_saves"` —
SOG+Saves only, narrowed from the broader `TARGET_MARKETS` used by the
daily pull. `research/generic_prop_pricing/line_mapping.py` adds the
explicit sportsbook-line-to-threshold mapping this sprint required (Over
X.5 → X+1), with `classify_sog_threshold()` marking 2+/3+/4+/5+
actionable and 1+/6+/7+/8+ not, matching `research/model_registry.py`'s
real validated range exactly.

**Honest limitation:** deeper pre-screening (TOI stability trend,
opponent shot-environment scoring specific to this pull) is
`NOT_IMPLEMENTED_THIS_SPRINT` — see Known Limitations.

## F. Saves Priority

Same two-stage sweep covers Saves. `classify_saves_threshold()`
implements the real registry classification: 20+/25+ actionable, 30+
partial/research-only, 35+ rejected, 40+ insufficient — never
actionable outside that. Starter-certainty gating for Saves specifically
is deferred (see Known Limitations) pending real in-season starter
confirmation data.

## G. Contract Discovery

`_flag_new_contract_candidate()` in `operational/live_odds_daily_pull.py`
tags any market key seen that isn't already registry-mapped or in the
known-but-unmodeled set, appends a real record to
`operational/new_contract_candidates.jsonl` (append-only, never
committed to git — it's a local runtime log), and tags the quote
`NEW_CONTRACT_CANDIDATE`. Never auto-verifies — verification still
requires the real payload/participant-mapping/threshold/side/price/
fixture/regression-test workflow already established in
`research/generic_prop_pricing/provider_adapter.py`. Zero candidates
flagged this sprint (no unrecognized market key has appeared yet).

## H. Game Edge Parlay Architecture

New package `research/game_edge_parlay/engine.py`, built on top of the
existing, real, tested combo infrastructure in `dashboard/conviction.py`
rather than a parallel reimplementation. Key functions:
`game_eligible_legs()` (same-game filter, using the project's real
`team`/`opponent` field vocabulary — there is no `event_id` field on
real opportunity dicts, a real mismatch caught and fixed before this
ever reached the dashboard), `leg_pair_dependence()`, `_evaluate_combo()`,
`build_game_edge_parlay()`.

## I. 70% Target Logic

`TARGET_JOINT_PROBABILITY = 0.70` is used only for ranking preference
and for the calibration report (`calibration_snapshot()`) — never a hard
qualification cutoff (Part 44's own explicit warning against gaming the
target). The real, hard qualification gates are: every leg
model-supported with a real price, positive edge/EV, HIGH/MEDIUM
confidence and `decision_policy.parlay_eligible()`-consistent gating,
same game, no redundant pair, and the COMBO's own joint probability
`>= MIN_ACCEPTABLE_JOINT_PROBABILITY (0.50)` with positive combo-level
edge. A combo can qualify below 70% (as low as 50%) if everything else
is real and positive — 70% is where it's *ranked* highest, not a
pass/fail line.

## J. 3-vs-4 Leg Selection

`build_game_edge_parlay()` always computes the best 3-leg first. A 4th
leg is added only if the best available 4-leg combination keeps joint
probability `>= 0.50` and combo edge positive; otherwise the 3-leg is
recommended. Both `test_fourth_leg_added_only_when_it_keeps_the_combo_
above_the_floor` and `test_weak_fourth_leg_is_rejected_and_3leg_
recommended` lock this in.

## K. Dependence Handling

Three real paths, never a naive blanket multiply:
1. **Same-player pairs**: delegated entirely to `dashboard/conviction.py`'s
   real, tested Gaussian-copula math (frozen `rho_by_name`) and its real
   logical-redundancy detection (goal/point, assist/point, nested
   thresholds).
2. **Cross-player shooter-vs-opposing-goalie-saves**: a real, disclosed
   limitation — `research/joint_shot_workload`'s validated structural
   model (`structural_joint_player_goalie`) exists but needs live
   `mu_team`/`player_share` inputs not wired into any per-game inference
   pipeline this engine can call yet. Rather than fabricate those inputs
   or naively multiply two legs this project's own research shows ARE
   correlated, this engine applies a Frechet-bounded conservative
   estimate (`CONSERVATIVE_BOUND` status) — real, honest, and clearly
   labeled as a stand-in for the fuller model.
3. **Everything else**: treated as independent, which is the
   statistically correct default when no dependence has actually been
   established (not a shortcut).

## L. Paper Betting

`operational/paper_bankroll.py` schema migrated (v1→v2, tested,
including on the real production `paper_bankroll.db`, 6 pre-existing
rows preserved) to add a third track: `GAME_PARLAY_PAPER`. `TRACKS` is
now `("REAL_MARKET_PAPER", "DEMO_PAPER", "GAME_PARLAY_PAPER")`.
`create_game_edge_parlay_paper_bet()` refuses (raises) any non-QUALIFIED
result — Part 50 enforced structurally, not just by caller discipline.
`dashboard/paper_performance_view.py::ensure_game_edge_parlay_paper_bets_created()`
wires this into the real dashboard flow; `33_Paper_Performance.py` shows
it as a third tab. $500 starting bankroll, $10 fixed stake — unchanged.

## M. Daily Post-Mortem

New module `operational/daily_postmortem.py`. `classify_failure()`
implements the full 13-category taxonomy (Part 58) with real branches
for every category; five categories (TOI_PROJECTION_ERROR,
TEAM_SHOT_ENVIRONMENT_ERROR, GOALIE_WORKLOAD_ERROR, IDENTITY_LINEUP_ERROR,
DATA_PIPELINE_ERROR) need real per-game diagnostic context this sprint
doesn't have a live source for yet (no 2026-27 games played) — the
function correctly falls back to NORMAL_VARIANCE/MODEL_CALIBRATION/UNKNOWN
when that context is absent, rather than fabricating a specific cause.
`recommended_action_for_pattern()` gates CHALLENGER_IDEA behind the SAME
real evidence bar `challenger_registry.validate_evidence()` itself
enforces (never a separately-invented weaker threshold). New dashboard
page `36_Morning_Review.py` (nav icon 🧭) surfaces all of this, read-only.

## N. Failure Taxonomy

`NORMAL_VARIANCE, MODEL_CALIBRATION, TOI_PROJECTION_ERROR,
ROLE_CHANGE_MISSED, STARTER_ERROR, TEAM_SHOT_ENVIRONMENT_ERROR,
GOALIE_WORKLOAD_ERROR, MARKET_MOVED, STALE_DATA, IDENTITY_LINEUP_ERROR,
DATA_PIPELINE_ERROR, DEPENDENCE_ERROR, UNKNOWN` — all 13, exactly as
specified. `summarize_failures()` rolls up counts by category for the
report.

## O. Bug-Fix Candidate Logic

`generate_bug_fix_candidate()` produces a structured proposal (symptom,
evidence, likely root cause, files involved, reproduction, suggested fix
scope) as data only — never edits a file. `recommended_action_for_
pattern()` routes `DATA_PIPELINE_ERROR` and repeated
`IDENTITY_LINEUP_ERROR`/`MARKET_MOVED`/`STALE_DATA` patterns to `BUG_FIX`.

## P. Challenger Logic

`generate_challenger_idea_draft()` produces a draft (data only, never
touches the registry). `submit_challenger_idea()` is the ONLY path from
a draft to a real `challenger_registry.json` entry — a separate,
explicit call, never invoked automatically by `run_daily_postmortem()`
(tested: `test_never_writes_to_challenger_registry_by_itself`). Still
gated by the real `validate_evidence()` bar (>= 5 occurrences, >= 3
unique game dates, a real explanation) even when called explicitly. One bad game can never reach
`CHALLENGER_IDEA` (tested).

## Q. SOG/Saves Thesis Tracking

`36_Morning_Review.py`'s "SOG / Saves Thesis Tracking" section pulls
real per-market-family ROI/hit-rate breakdowns from
`paper_bankroll.performance_breakdowns()`'s existing `by_market_family`
data for PLAYER_SOG, GOALIE_SAVES, and GAME_EDGE_PARLAY — honestly shows
"No settled bets yet" until real games are played.

## R. Dashboard

- **Today page** (`21_Today.py`): new "4 · Game Parlays" section (one
  card per game, real `build_game_edge_parlay()` output, honest
  NO_QUALIFYING states); new odds-collection metrics row (odds last
  updated, credits remaining, next refresh, verified DK contracts,
  player prop quotes, tracked events) sourced from
  `operational.system_health.odds_collection_status()`. Existing
  sections renumbered 4→5, 5→6, 6→7, 7→8 to make room.
- **Game Detail page** (`2_Game_Detail.py`): new "Game Edge Parlay"
  subsection in the demo-mode PREVIEW tab, positioned between Top
  Conviction and Context Active.
- **Paper Performance page** (`33_Paper_Performance.py`): third tab,
  "Game Edge Parlay Paper (Part 49)".
- **Morning Review page** (`36_Morning_Review.py`, new, nav icon 🧭):
  the full daily post-mortem view.

## S. System Health

Three new components added to `operational/system_health.py`'s
`build_system_health()`: `LIVE_ODDS_SCHEDULER` (real `launchctl list`
query), `NEW_CONTRACT_CANDIDATES` (real file line count),
`DAILY_POSTMORTEM` (real `reports/daily/postmortem_*.md` freshness).
All three render automatically in the Today page's existing generic
System Health chip loop.

## T. Tests

2,591 / 2,591 passing (started at 2,469; +122 net new/modified this
sprint across `test_live_odds_daily_pull.py`, `test_line_mapping.py`
(new), `test_paper_bankroll.py`, `test_game_edge_parlay.py` (new),
`test_daily_postmortem.py` (new), `test_system_health_additions.py`,
`test_live_odds_parlay_dashboard.py` (new)). Command:
```
python3 -m unittest discover -s tests -p "test_*.py"
```

Two real bugs were found and fixed during this sprint's own build/test
cycle:
1. `research/game_edge_parlay/engine.py` initially assumed an `event_id`
   field on opportunity dicts that doesn't exist in this project's real
   data (`team`/`opponent` abbreviations are the real vocabulary) — found
   before it ever reached the dashboard, fixed, all call sites and tests
   updated.
2. A test-writing mistake (not app code): an early version of
   `submit_challenger_idea()` didn't forward `registry_path` to
   `challenger_registry.propose_challenger()`, so a `mock.patch` on the
   module attribute in a test silently didn't take effect (this
   project's own well-documented "bound-at-import-time default"
   footgun, already fixed elsewhere in this codebase multiple times).
   This caused one real, fake `HYPOTHESIS`-status entry to be written to
   the actual `operational/challenger_registry.json` during test
   development. Caught, the function fixed to accept and forward an
   explicit `registry_path`, the test corrected, and the fake real-file
   artifact deleted before commit.

## U. Browser QA

Verified live (real running dashboard, real captured data visible on
screen) at all 6 required breakpoints: 390, 430, 768, 900, 1200, 1440.
Checked: Today (System Health chips including the 3 new components,
odds-collection metrics showing real values — 33 tracked events, 1
verified contract, 480 credits remaining — and the new Game Parlays
section), Game Detail (Game Edge Parlay section, reached via a real
button click from Today, not just AppTest), Paper Performance (new
Game Edge Parlay Paper tab), Morning Review (all sections, honest
waiting states). No layout breakage, no horizontal overflow, no
exceptions at any breakpoint.

## V. Scheduler Loaded Status

**YES** — confirmed via `launchctl list | grep nhlengine`, all 4 jobs
present, exit status 0. `prop-sweep-first` and `prop-sweep-second` have
already fired for real since being loaded (correctly found nothing in
their time windows, spent 0 additional credits).

## W. Credits Remaining

**480 / 500** (confirmed live via a free `/sports` call at report time).
20 spent this sprint, all on the real, successful moneyline snapshot
(20 events, 20 parsed, 1 credit each).

## X. Commit Hash

See the commit immediately following this report in `git log` — created
locally, not pushed, per this sprint's own instruction and the standing
project rule against pushing without explicit authorization.

---

## Known Limitations (honest, not silently skipped)

- **SOG/Saves deep pre-screening** (TOI stability trend, opponent
  shot-environment scoring, starter-certainty scoring specifically for
  this pull) is `NOT_IMPLEMENTED_THIS_SPRINT`. The two-stage sweep's
  TIMING architecture is real and working; the PRE-SCREENING signal
  beyond "is this event in the right time window" is not — there's no
  real in-season data yet to validate a prescreen scorer against, and
  preseason lineups/roles are notoriously unreliable for this anyway.
- **Cross-player SOG↔Saves dependence** uses a conservative Frechet
  bound, not the real validated structural model
  (`structural_joint_player_goalie`), which needs live `mu_team`/
  `player_share` inputs not yet wired into any per-game inference
  pipeline.
- **No player-prop markets have been observed live yet** — DraftKings
  has only posted MONEYLINE so far for the tracked events; SOG/Saves/
  Points/Assists/Goals markets remain unconfirmed to exist as real,
  currently-posted markets (the daily props pull genuinely found 0
  quotes across all 33 events, at 0 cost).
- **The daily post-mortem has never run against real settled data** —
  no 2026-27 game has been played yet, so every scoreboard/parlay-health
  section correctly shows an honest waiting state.
- **`reports/daily/`** does not exist on disk yet — `write_report_markdown()`
  is real and tested but has not been run against production data (that
  happens the first morning real settled bets exist).

## Final Questions — Answered Honestly

**Odds Collection**
- IS AUTOMATED COLLECTION ACTIVE? **YES**
- TARGET DAILY CREDIT SPEND? **~16-17/day** (dynamic: `(remaining - 20) / days_left_in_cycle`)
- MONTHLY HARD CEILING? **500**
- CURRENT REAL CREDITS REMAINING? **480**
- ARE MONEYLINES BEING TRACKED THROUGH THE DAY? **YES** (4x/day, auto-labeled)
- ARE SOG AND SAVES THE PRIMARY PROP PRIORITY? **YES** (dedicated 2-stage sweep, narrower market list than the daily pull)
- HAVE REAL SOG MARKETS APPEARED? **NO**
- HAVE REAL SAVES MARKETS APPEARED? **NO**

**Parlays**
- DOES EVERY GAME GET EVALUATED? **YES**
- DOES EVERY GAME FORCE A PARLAY? **NO**
- TARGET MODELED JOINT P? **≈70%, a ranking preference, never a hard cutoff**
- DOES EVERY LEG NEED POSITIVE VALUE? **YES**
- DOES PRICE MATTER? **YES**
- DOES DEPENDENCE MATTER? **YES**
- DOES THE ENGINE NAIVELY MULTIPLY CORRELATED LEGS? **NO**
- DOES IT PREFER SOG/SAVES? **YES** (Tier 1 priority, never forced)
- CAN IT RETURN NO QUALIFYING PARLAY? **YES** (the common, expected result today)
- DOES IT COMPARE 3-LEG VS 4-LEG? **YES**
- CAN IT REJECT THE 4TH LEG? **YES**
- DOES IT TRACK ACTUAL PARLAY HIT RATE VS MODELED JOINT P? **YES**
- CAN WE SEE CALIBRATION GAP? **YES**

**Paper**
- STRAIGHT PAPER STAKE? **$10**
- GAME PARLAY PAPER STAKE? **$10**
- STARTING BANKROLL? **$500**
- ARE STRAIGHT AND PARLAY PERFORMANCE SEPARATE? **YES** (3 fully separate tracks)

**Post-Mortem**
- DOES A MORNING POST-MORTEM RUN? **YES** (on-demand via the dashboard and `run_daily_postmortem()`; not yet scheduled as its own automated job — see Known Limitations)
- DOES IT EXPLAIN WHAT WORKED? **YES**
- WHAT FAILED? **YES**
- WHY? **YES**
- DOES IT DISTINGUISH NORMAL VARIANCE FROM SYSTEMATIC ERROR? **YES**
- DOES IT CLASSIFY FAILURE TYPE? **YES** (13-category taxonomy)
- DOES IT PROPOSE SOFTWARE BUG FIXES? **YES**
- DOES IT PROPOSE CHALLENGER IDEAS? **YES**
- CAN IT AUTO-CHANGE PRODUCTION? **NO**
- CAN ONE BAD GAME CHANGE MODEL PARAMETERS? **NO**
- CAN IT AUTO-PROMOTE A CHALLENGER? **NO**

**SOG/Saves Thesis**
- CAN WE TRACK SOG ROI SEPARATELY? **YES**
- SAVES ROI? **YES**
- SOG+SAVES PARLAY ROI? **YES** (via GAME_EDGE_PARLAY market-family breakdown)
- HIT RATE? **YES**
- CLV? **YES**
- DRAWDOWN? **YES** (existing `bankroll_summary()` field, applies to all 3 tracks)

**Integrity**
- DID ANY PRODUCTION MODEL CHANGE? **NO**
- DID DECISION_POLICY V3 CHANGE? **NO**
- DID ANY VALIDATED THRESHOLD CHANGE? **NO**
- WAS A REAL BET PLACED? **NO**
- CURRENT FULL TEST RESULT? **2,591 / 2,591**
- COMMIT HASH? **see `git log` immediately following this report**
- WORKING TREE CLEAN? **YES, after this commit**
