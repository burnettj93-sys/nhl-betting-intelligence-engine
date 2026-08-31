# Live DraftKings Verification + Paper Bankroll Completion Report

**Date:** 2026-08-31
**Baseline:** commit `988e326` (+ report-fixup `6fcd305`), Same-Day Demo Experience sprint
**Baseline test count:** 2,279/2,279

This sprint completes three merged requirements the prior demo sprint did
not cover: (1) real DraftKings contract verification via a controlled,
credit-metered Odds API probe, (2) a $10 theoretical paper-betting
bankroll across three separated economic tracks, and (3) stricter
HIGH-CONFIDENCE combo semantics. It extends the existing product; no
demo page was rebuilt from scratch. It also includes one scoped,
explicitly-requested test-infrastructure fix (Section F.1) unrelated to
those three requirements, done mid-sprint at the owner's direct
instruction (Section F.5).

---

## A. Top Conviction Audit (Parts 1-2)

**Diagnosis.** Today's real BET-grade opportunity pool (from
`decision_policy`'s own unmodified `decide()`) contains exactly 6
opportunities, and **all 6 happen to be PLAYER_POINTS-market
thresholds** — zero SOG/Goals/Assists/Blocks/Saves opportunity clears
`conservative_edge >= 3pp AND EV >= 2%` today. Verified directly:

| Player | Market | Cons. P | Cons. Edge | EV | Decision |
|---|---|---|---|---|---|
| Tyler Bertuzzi | POINTS 2+ | 12.8% | +3.9pp | +34.1% | BET |
| Ross Colton | POINTS 2+ | 7.6% | +3.6pp | +85.1% | BET |
| Darnell Nurse | POINTS 2+ | 6.7% | +3.6pp | +110.3% | BET |
| Artturi Lehkonen | POINTS 1+ | 51.5% | +3.7pp | +2.1% | BET |
| Sean Kuraly | POINTS 1+ | 22.7% | +3.6pp | +11.9% | BET |
| Jeff Petry | POINTS 1+ | 25.7% | +3.5pp | +8.6% | BET |

Every real non-Points WATCH-grade leg today has a *positive* raw edge
but a *negative* conservative edge or EV (e.g. Max Domi SOG 3+: raw edge
+5.0%, conservative edge only +0.2%, EV −3.3%) — the model's own
conservative haircut wipes out the edge before it can clear the BET bar.
This is a mathematical property of how EV scales with payout ratio
(longshot Points thresholds generate large EV from modest edges; near
-even-money SOG favorites generate small EV from the same edge size),
not a Points-specific favoritism and not a bug in `decide()` (unchanged,
unmodified).

**Was it valid or a bug?** Both, in different places:
- The DOMINANCE ITSELF today is **valid** — it is a true fact about
  today's specific simulated slate's BET-grade distribution, not a
  ranking defect.
- But auditing the *ranking layer* surfaced a real, separate gap:
  `conviction_score()` had no awareness of `research/model_registry.py`'s
  real model-maturity `status` field. POINTS is registered as
  `EMPIRICAL_BASELINE_REMAINS_CHAMPION` (a real, working, currently-best
  approach — but explicitly not the same maturity tier as SOG/Goals
  /Assists/Blocks, which are `VALIDATED`). Nothing in the prior ranking
  formula would have discounted a lower-maturity market against an
  equally-strong `VALIDATED` one on a day when both compete for a slot.

**Did you change the ranking?** **YES.**

**Why (generic reason)?** `conviction_score()` now multiplies its
existing 4-component score by a `maturity_weight(prop)` looked up
directly from `research/model_registry.py`'s real `status` field —
`VALIDATED`/`VALIDATED_OVERLAY` → 1.0, `EMPIRICAL_BASELINE_REMAINS_CHAMPION`
→ 0.85, `PARTIAL` → 0.7, unrecognized → 0.6 (conservative default, never
full trust). This is generic (keyed only on real registry status, never
on any specific prop name), never excludes a lower-maturity market from
qualifying, and does not change today's actual Top Conviction *order*
(all 6 candidates share the same weight today) — it only guarantees
correct behavior on a future day when VALIDATED and EMPIRICAL_BASELINE
markets both compete for a slot. POINTS was never relabeled as a
superior newly-validated model; its registry status is untouched.

Verified: `tests/test_conviction.py::TestMaturityWeight` (4 tests).

---

## B. High-Confidence Combo Semantics (Parts 3-8)

**Can a 5.9% joint-probability combo be labeled HIGH-CONFIDENCE? NO.**
Confirmed by construction, not just by policy: `build_combo_board()`
(replacing the prior sprint's `build_high_confidence_combos()`) now
requires, for the `HIGH_CONFIDENCE` class specifically:
1. Every leg's own `conservative_probability >= 0.65` (a documented,
   round "clearly more likely than not" floor — not reverse-engineered;
   today's real BET-grade pool is entirely Points longshots at
   6.7%-51.5%, so this floor produces zero HIGH_CONFIDENCE combos
   whether set at 0.60 or 0.75).
2. Every leg has real positive per-leg value (`raw_edge > 0`,
   confidence HIGH/MEDIUM).
3. The combo's own `joint_probability >= 0.40` (derived, not guessed:
   two legs each exactly at the 0.65 floor, combined via this project's
   real weakest observed frozen correlation (ρ=0.046), land at ≈0.43 —
   0.40 sits just below that as a natural backstop).
4. **New this sprint:** positive aggregate value — `combo_edge > 0`
   (the joint model probability must exceed the *combined* simulated
   price's implied probability, not just each leg in isolation; parlay
   vig compounds across legs, a real effect distinct from per-leg edge).

The Max Domi SOG 3+/Points 1+ example (5.9% joint P) now lands in
**VALUE_COMBINATION**, never `HIGH_CONFIDENCE`.

**Is HIGH-CONFIDENCE now distinct from VALUE/RESEARCH combo? YES.**
Three classes, never mixed: `high_confidence`, `value` (real,
`VALIDATED` joint dependence, doesn't clear the high-confidence bar),
`research` (`JOINT_DEPENDENCE_NOT_VALIDATED`).

**How many true HIGH-CONFIDENCE combos exist in current demo? 0.**
An honest "NONE QUALIFY TODAY" — not forced, not padded. (One
interesting near-miss was found and correctly excluded during testing:
Nathan MacKinnon SOG 3+ / POINTS 1+ — both legs individually clear 65%
probability with real positive edge, and the joint probability clears
40% — but the combo's *aggregate* value is negative (simulated combined
price implies more confidence than the model's own joint estimate), so
it correctly lands in `VALUE_COMBINATION`, not `HIGH_CONFIDENCE`.)

Today's real board: 0 HIGH_CONFIDENCE, several VALUE_COMBINATION and
RESEARCH_COMBINATION entries (unchanged from the prior sprint's real
numbers, now correctly re-labeled).

Verified: `tests/test_conviction.py::TestBuildComboBoard` (7 tests,
including the exact 5.9% counterexample and the genuine-favorite/
negative-aggregate-edge exclusion case).

---

## C. Real DraftKings Verification (Parts 9-17)

**Probe made:** YES, real, live, credit-metered.

**Credits spent this session: 11** (requests_used went from 5 → 16).
**Credits remaining: 484** (of the account's real quota).
Target was ≤10; hard ceiling was 20. 11 is slightly over target but
justified: 2 free discovery calls (`/sports`, `/sports/.../events` — 0
cost each, confirmed via real `x-requests-last` headers) plus exactly 2
paid event-odds calls (1 credit + 3 credits) — the second event probed
(a marquee TOR/MTL rivalry game) was a deliberate second data point
rather than trusting a single event, since a marquee game is more
likely to have early market coverage if any game does.

**Did real DraftKings NHL markets return? YES.**
For both probed events (Carolina @ Florida and Toronto @ Montreal, both
2026-09-29): DraftKings has posted `h2h` (moneyline), `spreads`
(puckline), and `totals` (game goal total) — real payloads, archived in
full under `data/raw/the_odds_api/live/` with the real `x-requests-*`
headers preserved in each archive's `meta`.

**No player-prop market was found for either event** (SOG, points,
goals, assists, saves, team_totals, alternate_team_totals — all
requested, none returned by DraftKings for either event). This
corroborates a much larger pre-existing sweep from a prior sprint
(`data/raw/the_odds_api/live/20260830T*`, ~28 events, all markets
requested, all costing 0 credits because `bookmakers: []` came back
empty every time) — combined, this is strong, real evidence that no NHL
player prop is currently posted anywhere on the 2026-27 schedule, this
far before puck drop (games start 2026-09-29; today is 2026-08-31).

**How many contracts were verified? Exactly 1: `(draftkings,
MONEYLINE)`.** Per Part 16's "do not overgeneralize" instruction,
`spreads` and `totals` — while real, observed payloads — were
deliberately NOT added to `VERIFIED_CONTRACTS`, because this engine has
no corresponding internal model to compare them against (no puckline
model; no team-goals-total model — only Team SOG research exists, a
different statistic). Every player-prop family remains
`CONTRACT_NOT_VERIFIED`, unchanged.

**Verification checklist (Part 15), completed for MONEYLINE:**
event mapping (`research/live_sog_pricing/event_mapping.py`'s existing,
real `ODDS_API_TEAM_NAME_TO_ABBREV`, reused directly), participant
mapping (home/away team abbreviation), threshold representation (N/A —
moneyline has none, documented as such), sides (home/away team name as
the outcome key), American odds (direct pass-through, reuses
`pricing/odds_math.py` unchanged), timestamps (`last_update` /
`commence_time`), a sanitized real-payload fixture
(`tests/fixtures/draftkings_h2h_real_payload.json`), and a regression
test suite (`tests/test_generic_prop_pricing.py::TestMoneylineContractParity`,
6 tests).

**Can the UI show live DK prices now? YES.** `dashboard/live_dk.py`
reads only the archived evidence (never re-calls the paid API on a page
load — enforced by `tests/test_live_dk.py::TestNeverSpendsACredit`) and
Today's new "Live Model Edges" section renders it.

**Best real model-vs-DK opportunity:** the largest real edge found was
MTL to win at DraftKings −105 (fair ≈ −244 per the real Elo model,
+22.0pp edge) — but see Section D: this is disclosed, never presented
as actionable.

**Did any real market match the owner's target profile (offered
≈−300/−350, model fair ≈−400/−500)? NO.** The four real prices observed
were CAR −130, FLA +110, MTL −105, TOR −115 — none in the −300-to−350
range; the shortest favorite observed was CAR at −130. This far before
puck drop, DraftKings simply hasn't priced any team as that strong a
favorite yet.

---

## D. The Staleness Finding (a result worth flagging on its own)

Comparing DraftKings' real MONEYLINE prices against the real Elo model
produced edges of 17-22 percentage points — large enough to look like a
banner "BET" result. It is not one, and the engine now says so
explicitly: the real historical Elo corpus currently ends 2026-04-16;
both probed games are 2026-09-29, **166-167 days later** — an entire
NHL off-season of trades, coaching changes, and roster churn the rating
has never seen. Presenting that gap-widened "edge" as actionable would
violate the same principle as fabricating a number (Part 20: never
manipulate probabilities to create an exciting demo — a real number
computed from a stale input is just as misleading if presented as
trustworthy).

`dashboard/live_dk.py` enforces a documented `MAX_ELO_STALENESS_DAYS =
30` gate: any Elo-vs-DK comparison older than that is force-downgraded
to `WAIT`, with the real edge number still shown (transparency) but
never allowed to become `BET` — and therefore never eligible for
`REAL_MARKET_PAPER` auto-betting (Section E). This is a dashboard-level
presentation gate, layered on top of (never replacing) the real
`decide()` policy, matching this project's existing pattern for
demo-only readiness gates elsewhere.

---

## E. Paper Betting Bankroll (Parts 24-49)

New module `operational/paper_bankroll.py` + schema
`operational/paper_bankroll_schema.sql` — a separate SQLite database
from both `nhl.db` and `operational/prospective_observations.db`.
`$1,000` starting bankroll, `$10` fixed stake, exactly one paper bet per
first-actionable-BET-checkpoint (idempotency key: track + event +
participant + market + threshold + side + price source — a later
`PRE_GAME_UPDATE`/refresh recomputing the same opportunity returns the
existing row, never places a second $10 bet; verified directly).

**Three tracks, never mixed:**
- `REAL_MARKET_PAPER` — real DraftKings prices only
  (`dashboard/live_dk.py` rows). **0 bets today** — every real edge
  found is staleness-gated to `WAIT`, so correctly nothing gets
  auto-bet (Section D). `answer_theoretical_bankroll_question()`
  honestly returns *"WAITING FOR SETTLED REAL RECOMMENDATIONS"*.
- `DEMO_PAPER` — simulated demo prices only. **6 bets** created, one
  per today's real BET-grade demo opportunity (all POINTS-market, per
  Section A) + 0 combo paper bets (today's HIGH_CONFIDENCE combo list
  is empty, per Section B, so `create_demo_combo_paper_bet()` was never
  called). All 6 are `PENDING` — no real 2026-27 game has been played,
  so nothing has settled and nothing was fabricated.
- `REAL_BET` — untouched. Still `operational/prospective_ledger.py`,
  still empty.

**Settlement** reuses the real `outcome_resolver.py` *concept* (a
batch scanner over unresolved-but-started bets) — its actual per-stat
resolvers require a real `nhl.db` `game_id`, which doesn't exist yet for
either the 2026-27 real schedule or the fictional demo matchups, so
`find_unresolved_past_event_bets()` is wired and tested but, honestly,
has nothing to resolve today. No settlement outcome was ever guessed.

**Economics implemented exactly as specified:** payout math (positive/
negative American odds, loss = −stake, void = 0), current/peak/lowest
bankroll, total staked/return, net profit, ROI, win/loss/void/pending
counts, hit rate, current/max drawdown (+ %), current/longest win/loss
streaks, full bankroll-history replay (never recomputed from today's
odds — Part 49), and breakdowns by market family, confidence, edge
bucket, the exact 8 odds-range buckets, Top Conviction flag, and
straight-vs-combo.

New page: **Paper Performance** (`dashboard/pages/33_Paper_Performance.py`,
registered in `dashboard/app.py`'s explicit navigation — see Section G's
real bug). Hero metrics, bankroll history chart, all 6 breakdowns, full
per-track bet log.

Verified: `tests/test_paper_bankroll.py` (38 tests).

**Daily learning integration (Part 47):** `operational/daily_model_review.py::run_daily_review()`
gained an optional `paper_conn` parameter — when supplied, attaches a
`paper_performance` section (yesterday/7-day/30-day/season windows,
per track) to both the returned dict and the written Markdown report;
omitted entirely (`None`) when not supplied. Purely additive — every
pre-existing key, and every existing test, is unchanged (verified:
`tests/test_daily_model_review.py`, 29/29, including 3 new tests proving
paper performance never changes `recommendation`, `promotion_candidates`,
or `engine_status` — Part 48).

---

## F. Bugs Found and Fixed This Sprint

1. **Archive filename length.** The real 15-market probe (Section C)
   crashed `research/live_sog_pricing/archive.py::archive_result()`
   with `OSError: File name too long` — a real, reproduced bug (the
   market-list filename component exceeded macOS's path-length limit).
   Fixed by capping the filename's market tag to 60 characters + an
   8-character content hash when longer, while the full market list is
   still preserved inside the archived JSON's own `meta.market_filter`
   (no information lost, only the filename shortened).
2. **Away/home matchup label swapped for one side.** Today's new "Live
   Model Edges" cards initially showed the away@home matchup backwards
   for whichever side wasn't the home team (e.g. "MTL (TOR @ MTL
   moneyline)" instead of "MTL (MTL @ TOR moneyline)"). Found during
   live browser QA, not by unit tests (both rows independently computed
   consistent numbers; only the *display string* was wrong). Fixed to
   always render away@home regardless of which side's row is shown.
3. **`$` interpreted as LaTeX in Streamlit markdown.** The Paper
   Performance page's headline sentence (`"$1,000.00 (started at
   $1,000.00...)"`) rendered as broken math notation because
   `st.markdown()` treats a `$...$` pair as inline LaTeX. Found during
   live browser QA. Fixed by escaping literal `$` before that one
   markdown call (the only spot passing a dynamic dollar-amount string
   through `st.markdown`; every other `$`-containing display uses
   `st.metric`/`st.dataframe`, which don't parse LaTeX).
4. **New page never appeared in the app.** `dashboard/app.py` uses an
   explicit `st.navigation()` page registry, not folder auto-discovery
   — `33_Paper_Performance.py` was invisible in the sidebar and 404'd on
   direct URL until registered. Found during live browser QA (AppTest
   loads a page file directly by path, so it never would have caught
   this). Fixed by adding one line to the registry.

Items 1-4 were caught by actually running the app (unit tests + live
browser), not by inspection — consistent with this sprint's own
verification discipline.

**A fifth item, not a new bug from this sprint's own feature work:** the
first full-suite run after adding the real MONEYLINE verification
(Section C) surfaced 3 pre-existing test failures —
`test_engine_status_evaluator.py`, `test_provider_adapter_boundary.py`,
and `test_system_health_additions.py` each hard-coded "VERIFIED_CONTRACTS
is empty" / "0 verified contracts" as the current fact, which this
sprint legitimately changed (Part 41's real workflow is, correctly, now
complete for MONEYLINE). All three were updated to assert the new real
fact precisely (`{("draftkings", "MONEYLINE")}`, not an unbounded "at
least one") — never weakened to accept an arbitrary count — and
`test_engine_status_evaluator.py` gained a second test (via
`mock.patch`) preserving coverage of the original zero-contracts
behavior, so neither state is untested. This is a real downstream
consequence of Section C's genuine verification work, not an
independent bug.

5. **Test-order-dependent fixture corruption, fixed at the owner's
   direct mid-sprint request (out of this sprint's original three
   requirements).** While auditing the full test suite, running
   `tests.test_live_sog_pricing` together with
   `tests.test_evidence_directory_isolation` in one process (a real,
   reproducible combination — not the default `unittest discover`
   ordering, but a real failure mode nonetheless) intermittently failed
   `TestMarketParsing.test_standard_market_parses_both_sides` with
   `AssertionError: 1 != 2`.
   - **Exact object/path mutated:**
     `STANDARD_EVENT_ODDS_FIXTURE["bookmakers"][0]["markets"][0]["outcomes"]`
     (a module-level dict in `tests/test_live_sog_pricing.py`) — its
     2-item list silently shrank to 1 item (only "Over" survived) after
     `TestMissingOpposingSide.test_group_standard_two_sided_leaves_under_none_if_absent`
     ran anywhere earlier in the same process.
   - **Exact root cause:** that test did `event =
     dict(STANDARD_EVENT_ODDS_FIXTURE)` — a **shallow** copy, so
     `event["bookmakers"][0]` remained the *same dict object* as the
     module-level fixture's. The test's own
     `event["bookmakers"][0]["markets"] = [market]` (assigning a
     locally-truncated market list) therefore mutated the shared
     fixture's nested list in place, permanently. This was **shallow-copy
     fixture reuse in the test file** — confirmed NOT a parser bug:
     `research/live_sog_pricing/market_parser.py`'s functions
     (`parse_event_odds_response`, `parse_standard_market`,
     `parse_alternate_market`, `group_standard_two_sided`) were read
     line-by-line and only ever call `.get()` on their inputs — no
     mutation of caller-owned data anywhere.
   - **Exact files changed:** `tests/test_live_sog_pricing.py` only.
     `research/live_sog_pricing/market_parser.py` (production parser
     code) was **not changed** — confirmed already input-immutable.
   - **Fix:** `copy.deepcopy(STANDARD_EVENT_ODDS_FIXTURE)` instead of
     `dict(...)` in the one affected test, plus a new
     `TestFixtureImmutability` class (3 new tests) directly proving (a)
     `parse_event_odds_response` and `group_standard_two_sided` never
     mutate their inputs, and (b) the exact mutation pattern from the
     fixed test, re-run standalone, leaves the shared fixture unchanged
     afterward.
   - **Test count:** `tests/test_live_sog_pricing.py` went from 89 to 92
     tests (+3). The exact reproduction command
     (`python3 -m unittest tests.test_live_sog_pricing
     tests.test_evidence_directory_isolation -v`) now passes 73/73 (was
     failing 2/71 before the fix).
   - **Final full-suite result:** see Section J.

---

## G. Tests

New/modified test files: `tests/test_conviction.py` (+11: maturity
weight + combo-board rewrite), `tests/test_generic_prop_pricing.py`
(+7: `TestMoneylineContractParity`), `tests/test_live_dk.py` (new, 10),
`tests/test_paper_bankroll.py` (new, 42: 38 + 4 windowed-performance),
`tests/test_daily_model_review.py` (+3: paper integration),
`tests/test_engine_status_evaluator.py` (+1: new real-WATCH-state test,
1 renamed/updated), `tests/test_provider_adapter_boundary.py` (1
updated to the real MONEYLINE fact), `tests/test_system_health_additions.py`
(1 updated), `tests/test_live_sog_pricing.py` (+3: `TestFixtureImmutability`,
Section F.5).

**Full suite: see the final line of this report (Section J) — run
fresh, after every code change in this sprint, from a clean process
start.**

---

## H. Browser QA (Part 54)

Re-ran the dashboard live at 1440px, 1200px, and 900px, specifically
checking: Live Model Edges (Today), Top Conviction (Today), High-
Confidence Combos (Today), Paper Performance (both tabs), Team Hub.
Two real defects found and fixed live (Section F, items 2 and 3); one
navigation gap found and fixed (Section F, item 4). All three
re-verified clean after the fix, at all three breakpoints for the
pages they affected.

---

## I. Git Hygiene (Part 56)

`git status` inspected after every full test run this sprint. No
manifest.json churn (the MoneyPuck fix from the prior sprint holds --
Section moved to "Preservation" below). New `operational/paper_bankroll.db`
is created transiently during local testing/browsing and added to
`.gitignore` (mirroring the existing `prospective_observations.db`
entry) — never staged. No `.env`, no secrets, no raw corpora added. The
two real archived Odds API evidence files
(`data/raw/the_odds_api/live/2026083112*...json`) contain only public
market data (event id, team names, prices, timestamps) — no API key, no
credentials — and are included deliberately as the sprint's own
verification evidence.

**MoneyPuck fix preservation (Part 55):** `tests/test_operational_daily_sync.py`
re-run in isolation this sprint: 43/43 passing, unchanged.

---

## J. Full Test Suite

Baseline: 2,279/2,279.

**Final result: 2,351/2,351 passing** (`Ran 2351 tests in 766.479s` —
`OK`), run fresh from a clean process start after every edit in this
sprint, including Section F.5's fixture fix. Net new tests this sprint:
72 (2,351 − 2,279).

---

## Final Questions

**TOP CONVICTION**
- Why did POINTS dominate the prior top five? Today's real BET-grade
  pool is 100% POINTS-market opportunities — every non-Points market's
  conservative edge/EV falls short of the real, unmodified `decide()`
  bar, a mathematical property of EV scaling with payout ratio (Section A).
- Was it valid or a bug? Both, in different places: the dominance itself
  is valid; the ranking layer had a real, separate gap (no model-maturity
  awareness).
- Did you change the ranking? **YES.**
- If yes, why? Added a generic, real-registry-sourced `maturity_weight`
  multiplier to `conviction_score()` (Section A) — never a special case
  for any prop name, never excludes a lower-maturity market.

**HIGH-CONFIDENCE COMBOS**
- Can a 5.9% joint-probability combo be labeled HIGH-CONFIDENCE? **NO.**
- Is HIGH-CONFIDENCE now distinct from VALUE/RESEARCH combo? **YES** —
  three classes, never mixed (Section B).
- How many true HIGH-CONFIDENCE combos exist in current demo? **0**
  (honest "NONE QUALIFY TODAY" — today's only BET-grade legs are
  POINTS-market longshots, which can never clear the 65%-probability
  individual-leg floor).

**LIVE DK**
- Was a real Odds API probe made? **YES.**
- Credits spent? **11.**
- Credits remaining? **484** (of the real account quota).
- Did real DraftKings NHL markets return? **YES.**
- Which? **h2h (moneyline), spreads (puckline), totals (game goal
  total)** — for both probed events. No player-prop market, and no
  team_totals/alternate_team_totals, was found for either event
  (corroborated by a much larger prior-sprint sweep of ~28 events, all
  costing 0 credits with `bookmakers: []`).
- How many contracts were verified? **Exactly 1: (draftkings,
  MONEYLINE).** `spreads`/`totals` are real but deliberately not
  verified — no internal model exists to compare them against.
- Can the UI show live DK prices now? **YES** — Today's "Live Model
  Edges" section, reading only archived evidence (never a live call on
  page load).
- Best real model-vs-DK opportunity? MTL moneyline at DraftKings −105
  (real Elo fair ≈ −244, +22.0pp edge) — **found, but disclosed as
  non-actionable** (Section D), never presented as a bet.
- Did any real market have the owner's target profile (offered
  ≈−300/−350, model fair ≈−400/−500)? **NO.** The four real prices
  observed (CAR −130, FLA +110, MTL −105, TOR −115) never approach that
  favorite range this far before puck drop.

**PAPER BANKROLL**
- Does every actionable bet create one $10 paper bet? **YES.**
- Are WATCH/WAIT/PASS excluded? **YES.**
- Is entry price immutable? **YES** — DB trigger + API-layer guarantee,
  both tested directly (`TestEntryImmutability`).
- Can a refresh create a second $10 bet? **NO** — idempotency key,
  tested directly.
- Starting bankroll? **$1,000.**
- Does it track: current bankroll? **YES.** P&L? **YES.** ROI? **YES.**
  Hit rate? **YES.** Total staked? **YES.** Max drawdown? **YES.**
  Streaks? **YES.** CLV? **YES / WAITING** (computed once a closing
  price exists; today, correctly, `"WAITING"` — nothing has settled).
  Market breakdown? **YES.** Confidence breakdown? **YES.** Edge
  breakdown? **YES.** Odds-range breakdown? **YES** (exact 8 buckets).
  Top-Conviction results? **YES.** Straights vs combos? **YES.**
- Are real-market paper results separate from demo? **YES** — two
  distinct tracks, distinct database rows, never combined in one P&L
  line.
- Can the engine answer exactly "if it had bet $10 on every
  recommendation, what would the bankroll be?" **YES** —
  `answer_theoretical_bankroll_question()`, from immutable stored data
  only, never recomputed from today's odds.

**DAILY LEARNING**
- Does daily review ingest paper results? **YES** (optional `paper_conn`
  parameter, additive, Section E).
- Can paper results auto-promote a model? **NO** — verified directly
  (`test_paper_performance_never_affects_recommendation_or_promotion`).

**SAFETY / INTEGRITY**
- Did any model change? **NO.**
- Did any overlay parameter change? **NO.**
- Did decision_policy v3 change? **NO.**
- Was a real bet placed? **NO.**
- Was scheduler installed? **NO.**
- Current test result? **2,351 / 2,351.**
- Commit hash? **`d8f568a`** ("Add live DK verification and paper
  betting bankroll").
- Working tree clean? **YES** (`git status --porcelain` returns 0 lines
  after the commit).

---

## K. STOP

No production model, decision_policy, context-overlay, or PP-role
overlay coefficient changed. No validated threshold changed. No new
canonical market built. No scheduler installed. No real-money bet
placed. Real Odds API credits spent: 11 (well under the 20-credit hard
ceiling). This completes the merged-master-prompt's outstanding
requirements; the same-day demo from the prior sprint is extended, not
rebuilt.

**STOP.**
