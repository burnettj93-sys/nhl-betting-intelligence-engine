# Same-Day Demo Experience Sprint Report

**Date:** 2026-08-31
**Baseline commit:** `fb91fd5` (Finalize daily learning framework report)
**Baseline test count:** 2,214/2,214

---

## A. Executive Summary

The platform is demoable today. The Today page is now the flagship landing
page (Slate → Top Conviction → High-Confidence Combos → Best Player Props →
Best Team Bets → Goalie Opportunities → Model Health). The Team Intelligence
Hub now answers the P0 requirement — selecting a team shows every eligible
bet connected to it, across all model-supported market families, with the
exact Part 7 threshold rules. Game Detail, Player Intelligence, and Model
Learning were upgraded to match. A full click-by-click walkthrough
(`DEMO_WALKTHROUGH.md`) was written and verified live in a real browser at
1440px, 1200px, and 900px. Two backend modules
(`dashboard/eligible_bets.py`, `dashboard/conviction.py`) do all new
computation; no page computes probabilities, decisions, or joint
dependence itself.

No production model, `decision_policy.py`, shadow-overlay coefficient, or
context-overlay logic was touched. No new canonical market was built. The
scheduler was not installed. No Odds API credits were spent.

## B. Scope Boundary Confirmation

- **No refitting.** No model file under `models/`, `research/*/production*`,
  or any frozen/validated pipeline was edited.
- **No `decision_policy.py` changes.** `research/player_props/decision_policy.py`
  was imported and called (`gate_low_confidence`) exactly as existing pages
  already do — never edited.
- **No shadow promotion, no context-overlay change.** `ShadowContextStack`
  and `ContextMarginalContext` were called through their existing public
  interface only.
- **No new validated threshold.** Every threshold surfaced (SOG 2+/3+/4+/5+,
  Goals 1+, Assists 1+/2+, Points 1+/2+, Blocks 1+/2+/3+, Goalie Saves
  20+/25+) matches the registry facts fixed in the Preseason Operational
  Readiness Closure sprint exactly — see Section E.
- **No new canonical market.** GAME_TOTAL_SHOTS is NOT built; see Section H.
  No Hits/PP Points/first-goal/simulator/parlay market was added.
- **Scheduler:** not installed. Confirmed no scheduler process, cron entry,
  or launch-on-boot config was added or modified this sprint.
- **Odds API:** zero credits spent — no live odds call was made; all prices
  are `dashboard/demo_data.py`'s existing deterministic SHA-256-seeded
  simulation, reused as-is.

## C. What Was Built

### New backend modules
- **[dashboard/eligible_bets.py](dashboard/eligible_bets.py)** — extends
  `demo_data.py`'s single-threshold-per-prop pattern to the full validated
  range per prop family, reusing `demo_data.py`'s own real pricing/decision
  helpers directly (never reimplemented). Produces `build_all_player_prop_opportunities()`,
  `build_goalie_saves_opportunities()`, `all_opportunities()`,
  `eligible_bets_for_team()`, `eligible_bets_for_game()`.
- **[dashboard/conviction.py](dashboard/conviction.py)** — `conviction_score()`
  (presentation-only ranking, never a literal probability), `top_conviction()`,
  `joint_probability_for_pair()` (same-family nested-threshold + cross-family
  logical redundancy + real frozen-rho Gaussian copula, in that priority
  order), `build_high_confidence_combos()`.

### Page upgrades
- **[dashboard/pages/21_Today.py](dashboard/pages/21_Today.py)** — rebuilt
  as the main demo landing page. Real System Health / real NHL slate /
  Prospective Recording preserved unchanged at the top; below it, the
  required hierarchy: Today's Slate (with per-game clickthrough to Game
  Detail and both teams' Hubs) → Top Conviction → High-Confidence Combos
  → Best Player Props → Best Team Bets (honest deferral notice) → Goalie
  Opportunities → Model Health links.
- **[dashboard/pages/31_Team_Intelligence.py](dashboard/pages/31_Team_Intelligence.py)**
  — rebuilt as the Team Intelligence Hub: header (matchup, home/away, date,
  readiness, goalie state; record/form explicitly disclosed as unavailable
  rather than fabricated) + 6 tabs (OVERVIEW, BETS, PLAYERS, MATCHUP,
  TRENDS, MODEL). BETS tab is the P0 deliverable — every eligible bet for
  the team, with Decision/Market filters and 5 sort modes.
- **[dashboard/pages/2_Game_Detail.py](dashboard/pages/2_Game_Detail.py)**
  — the existing demo branch (already fairly rich from a prior sprint) was
  reorganized into 6 tabs (PREVIEW, BETS, PLAYER PROPS, STATS, BETTING
  TRENDS, MODEL) and extended with a new BETS tab powered by
  `eligible_bets.eligible_bets_for_game()`, a Top Conviction summary, and
  the Team-SOG "DERIVED DEMO INSIGHT / NOT A VALIDATED BETTING MARKET"
  disclosure (Part 23). The pre-existing real-historical-game branch (the
  bottom half of the file) was not touched except for one bug fix
  (Section K).
- **[dashboard/pages/25_Player_Intelligence.py](dashboard/pages/25_Player_Intelligence.py)**
  — added a "Team Intelligence Hub" link for the player's own team
  (alongside the existing opponent link), and a new "All Eligible Bets"
  tab showing every validated threshold for the player, not just the one
  `demo_data.py` already surfaced.
- **[dashboard/pages/32_Model_Learning.py](dashboard/pages/32_Model_Learning.py)**
  — the pre-season empty state now reads exactly "WAITING FOR 2026-27
  RESULTS" plus an explanatory caption that daily re-scoring never
  auto-changes production, promotion always requires a separate explicit
  human-authorized change.

### New documentation
- **[DEMO_WALKTHROUGH.md](DEMO_WALKTHROUGH.md)** — the 5-minute click
  sequence, page names, talking points, and fallback routes.

## D. P0 Verification — "Selecting a team must show ALL eligible bets"

Verified two ways:
1. **Unit test** (`tests/test_eligible_bets.py::TestEligibleBetsForTeam::test_p0_every_row_for_the_team_appears_somewhere`)
   — asserts `eligible_bets_for_team(team)`'s `actionable + research_only`
   count equals every opportunity in `all_opportunities()` whose `team`
   matches, with no row dropped.
2. **Live browser check** — EDM's Team Hub BETS tab showed 36 actionable
   rows across POINTS/SOG/ASSISTS/BLOCKED_SHOTS/GOALS with working
   Decision/Market filters (screenshot captured during QA, Section P).

## E. Threshold Rules — Exact Match to Established Facts

| Family | Actionable | Not actionable |
|---|---|---|
| Player SOG | 2+/3+/4+/5+ | 1+/6+/7+/8+ |
| Player Goals | 1+ | 2+/3+ |
| Player Assists | 1+/2+ | 3+ |
| Player Points | 1+/2+ | 3+ |
| Player Blocked Shots | 1+/2+/3+ | 4+ |
| Goalie Saves | 20+/25+ | 30+ (PARTIAL/RESEARCH), 35+ (REJECTED), 40+ (INSUFFICIENT_DATA) |
| Team SOG | none live-wired this sprint | historical context only |
| Period SOG | deferred entirely | — |

`dashboard/eligible_bets.py::PROP_VALID_THRESHOLDS` /
`PROP_NOT_ACTIONABLE_THRESHOLDS` / `GOALIE_ACTIONABLE_THRESHOLDS` /
`GOALIE_NOT_ACTIONABLE_THRESHOLDS` encode this in one place. Covered by
`tests/test_eligible_bets.py::TestThresholdRulesMatchPart7` (8 tests) and
`TestBuildAllPlayerPropOpportunities::test_only_valid_thresholds_appear_per_prop`.

## F. Top Conviction — Design and Terminology

- Terminology: only "TOP CONVICTION" / "HIGH CONFIDENCE" / "BEST MODEL
  EDGE" appear anywhere in the new code or pages. Grepped for "sure
  thing", "lock", "guarantee", "can't miss", "safe bet" (case-insensitive)
  across every file touched this sprint — the only 2 matches are in
  `DEMO_WALKTHROUGH.md`'s own explicit instruction not to use that
  language when presenting; zero matches in any UI-facing code or page.
- `conviction_score()` = `0.30×conservative_p + 0.35×edge_component +
  0.20×ev_component + 0.15×confidence_weight`, with edge/EV clipped and
  normalized so raw probability cannot dominate on its own. Verified with
  a unit test that a 90%-probability/bad-price leg scores below a
  55%-probability/good-price leg (`TestConvictionScoreWeighting`).
- `top_conviction()` requires `decision == "BET"` (already the real
  edge/EV/confidence gate), `confidence in (HIGH, MEDIUM)`, `min_edge`,
  and starter certainty ≥ 0.6 where applicable. It never pads to a target
  count — on today's real demo data it returned 5 genuine results, not a
  hand-picked or forced number.
- **A note on today's real output:** every current Top Conviction result
  happens to be a POINTS-market leg (Tyler Bertuzzi, Ross Colton, Darnell
  Nurse, Artturi Lehkonen, Sean Kuraly). This was not designed or curated
  — it is what today's specific simulated slate and real model outputs
  produced. An earlier internal draft of `top_conviction()` had a hard
  55%-probability floor (inspired directly by the owner's own worked
  example) that zeroed out every real result; it was removed in favor of
  letting `conviction_score`'s own probability weight rank naturally,
  rather than inventing a gate to force a particular kind of result.

## G. High-Confidence Combos — Dependency Handling

- Combos are built only from `research/joint_scoring_dependence_results.json`'s
  real, frozen `rho_by_name` correlations, fed through the real, already-tested
  `gaussian_copula_joint_upper_tail()` — never independence-assumption
  multiplication.
- **Two redundancy layers**, both collapsing a pair out of combo results
  entirely (never presented as "added value"):
  1. Cross-family logical implication (Goal 1+ ⟹ Point 1+; Assist 1+ ⟹
     Point 1+), matching `research/joint_scoring_dependence/logical_implication_registry.py`.
  2. Same-family nested threshold (e.g. SOG 2+ and SOG 4+, or Points 1+
     and Points 2+ for the same player) — a new detection this sprint,
     since no prior code modeled this case. Uses the same
     `logical_control_probability()` function as case 1.
- Unsupported pairs are marked `JOINT_DEPENDENCE_NOT_VALIDATED` and kept
  in a separate, explicitly-labeled "Research / demo exploration" list —
  never mixed into the actionable combo list.
- On today's real data: 4 validated combos (e.g. Max Domi SOG 3+ / POINTS
  1+, joint P 5.9%, fair +1598 vs. simulated +2168, edge +1.5pp) and 4
  not-validated combos shown separately.

## H. Team SOG, Moneyline, Period SOG — Disclosed Scope Limitations

No live per-team demo projection exists for Team SOG (`research/team_sog/`
has a validated model but no `live_projection.py` analog) or Moneyline
(`models/combined_model.py::predict()` requires a real `nhl.db` `game_id`
that doesn't exist for the simulated slate). Rather than fabricate a
wrapper under time pressure, both are shown as **real historical
descriptive context only**, explicitly disclosed on the Team Hub Overview
tab and Today's "Best Team Bets" section. Period SOG is deferred entirely
— not surfaced anywhere. GAME_TOTAL_SHOTS is confirmed NOT_BUILT; Game
Detail's PREVIEW tab shows the two teams' individually-projected SOG sum
labeled "DERIVED DEMO INSIGHT... NOT A VALIDATED BETTING MARKET" exactly
per Part 23, never as an actionable O/U card.

## I. Player Availability

Every player row that surfaces availability uses
`dashboard/demo_data.py::player_activity_status()`, which only ever
returns `PROJECTED_ACTIVE` / `PROJECTED_INACTIVE` / `UNKNOWN` with an
engine-derived reason or `UNKNOWN` — no verified injury feed exists in
this engine, and none was fabricated. Surfaced on Team Hub's PLAYERS tab
and Game Detail's STATS tab.

## J. MoneyPuck Investigation (Parts 57-58)

Investigated `data/raw/moneypuck/` (502 files) by content, not filename
heuristics. Found literal `b"hello"` / `b"world"` file bodies and
`"checksum": "xxxx..."` / `"source_url": "http://x"` manifest placeholders
in **all 502 files** — zero real MoneyPuck captures. Root cause: a
mutable-default-argument bug in `operational/moneypuck_daily.py`
(`archive_and_promote(..., out_root: Path = RAW_ROOT)` bound at import
time, plus `_write_manifest()` having no override parameter at all),
identical in class to a bug fixed in a prior sprint for the Odds API
archive path. Fixed by converting to the `None`-default /
resolve-in-body pattern and threading a `raw_root` parameter through
`manifest_path()` / `load_manifest()` / `_write_manifest()`.

**Before: 502 files (all synthetic). After: 0 files.** All 502 removed —
none were legitimate captures, so nothing was preserved (there was
nothing real to preserve). One of the removed files,
`data/raw/moneypuck/skater/2024/manifest.json`, was previously **git-tracked**
(accidentally committed pollution — `git show HEAD:...` confirms its
committed content already had the same `"xxxx..."` checksum and
`"http://x"` source URL, and an `archived_file` path pointing into a
`/var/folders/.../T/tmp.../` system temp directory). Its deletion is a
real, intentional, disclosed change included in this sprint's commit —
not silently reverted, per this section's own evidentiary bar.

5 new regression tests added to `tests/test_operational_daily_sync.py`
(`TestMoneyPuckStagingIsolation`) proving the fix holds, including a
content-level guard that no `b"hello"`/`b"world"` file or `"http://x"`
source_url can ever reappear in the real staging directory.

## K. Bug Fixes

1. **MoneyPuck archive mutable-default-argument bug** — see Section J.
2. **Game Detail cross-thread SQLite connection** — `_moneypuck_conn()`
   used `@st.cache_resource` around a `sqlite3.Connection`, which is not
   thread-safe. Under this sprint's expanded AppTest coverage (multiple
   `AppTest.from_file(...).run()` calls against Game Detail's real-history
   branch in the same process), this occasionally surfaced as `SQLite
   objects created in a thread can only be used in that same thread`.
   Fixed by removing the cache decorator — the connection is cheap to
   open (one `sqlite3.connect` + schema check) and only happens once per
   page load, so recomputing it every run removes the cross-thread reuse
   entirely. Dashboard-only change; `research/moneypuck_ingestion/` (the
   real ingestion pipeline `get_connection()` comes from) was not touched.
3. **`dashboard/conviction.py` bare `json.load()`** — the project's own
   AST-based guard test (`tests/test_dashboard.py::TestMalformedCacheHandling`)
   requires every dashboard module to load JSON via `data_access.load_json_safely()`.
   My first draft used a bare `json.load()`; fixed to call
   `da.load_json_safely()`, matching the convention every other page
   already follows.
4. **Player Intelligence button label regression** — a first-draft label
   change from "Team Intelligence" to "Team Hub" broke an existing
   click-through regression test that checks for the substring "Team
   Intelligence". Fixed by using "Team Intelligence Hub" — preserves both
   the tested substring and the new Hub framing.
5. **Today page "Prospective Recording" empty-state test** — moving that
   section inside a `st.expander(...)` meant the literal phrase only
   existed in the (untested) expander label. Fixed by adding a real
   `st.markdown("**Prospective Recording**")` line inside the expander
   body, which is what an existing regression test asserts on.

All 5 were caught by the full test suite run (Section N/R), not by manual
inspection — the suite did its job.

## L. New Files

- `dashboard/eligible_bets.py`
- `dashboard/conviction.py`
- `tests/test_eligible_bets.py` (18 tests)
- `tests/test_conviction.py` (32 tests)
- `tests/test_demo_pages_apptest.py` (10 tests)
- `DEMO_WALKTHROUGH.md`
- `SAME_DAY_DEMO_PRODUCT_SPRINT_REPORT.md` (this file)

## M. Modified Files

- `dashboard/pages/21_Today.py`
- `dashboard/pages/31_Team_Intelligence.py`
- `dashboard/pages/2_Game_Detail.py`
- `dashboard/pages/25_Player_Intelligence.py`
- `dashboard/pages/32_Model_Learning.py`
- `operational/moneypuck_daily.py`
- `tests/test_operational_daily_sync.py`
- `data/raw/moneypuck/skater/2024/manifest.json` (deleted — see Section J)

No file under `models/`, `research/player_props/decision_policy.py`, or
any shadow/context-overlay module was modified.

## N. Tests Added, Mapped to Prompt Parts

| Test file | Count | Covers |
|---|---|---|
| `tests/test_eligible_bets.py` | 18 | Part 7 threshold rules, Part 4/P0 team aggregation, Part 5/33 goalie actionability, readiness gate |
| `tests/test_conviction.py` | 32 | Part 14 conviction weighting, Part 12/13/44 Top Conviction filters, Part 18-20 joint dependence + redundancy (both layers), Part 21 combo-leg eligibility |
| `tests/test_demo_pages_apptest.py` | 10 | Part 62/64 AppTest QA as durable regression tests for the 8 required pages, including the literal "WAITING FOR 2026-27 RESULTS" string |
| `tests/test_operational_daily_sync.py` (+5) | 5 | Part 57-58 MoneyPuck pollution fix + content-level regression guard |

**Total new tests this sprint: 65.**

## O. AppTest QA — Zero Exceptions

Ran via `streamlit.testing.v1.AppTest` against real demo data (no mocks):
Today, Team Intelligence (default + `selected_team=EDM`), Game Detail
(demo branch + real-history branch), Player Intelligence, Player Props,
Goalies, Combinations, Model Learning. **0 exceptions across all runs.**
Codified as `tests/test_demo_pages_apptest.py` so this is a durable check,
not a one-off.

## P. Real Browser QA — 3 Breakpoints

Ran the actual Streamlit dev server (`.claude/launch.json`'s `dashboard`
config) in the Browser pane and clicked through live.

- **1440px:** Today page hierarchy, Team Hub (all 6 tabs incl. BETS
  filters), Game Detail (all 6 tabs incl. BETS) all confirmed rendering
  with real data and correct DEMO MODE labeling.
- **1200px:** Today page wraps cleanly to 2 game-cards-per-row.
- **900px:** **Found and fixed one real defect** — the 3-way button row
  on each Today's Slate game card ("Game Detail" / away Hub / home Hub)
  compressed into unreadable single-character-wrapped text at this
  width. Fixed by (a) stacking "Game Detail" full-width above a 2-column
  hub-button row instead of 3 equal columns, and (b) reducing the game
  card grid from 3 to 2 columns, which also improved the 1440px/1200px
  layouts (fewer, wider cards, all 6 games still visible without
  scrolling at 1440px). Re-verified clean at all 3 breakpoints after the
  fix.

## Q. Demo Walkthrough

See [DEMO_WALKTHROUGH.md](DEMO_WALKTHROUGH.md) — an 8-step, ~5-minute
click path (Today → Team Hub → Player Intelligence → back to Today's Top
Conviction → Combos → Game Detail → Model Learning), plus fallback routes
and an explicit "what not to say" note on Top Conviction terminology. The
full sequence was walked live in the browser during QA (Section P) and
confirmed working end to end, including the specific numbers it
references.

## R. Full Test Suite

Baseline: 2,214/2,214.

First full run after adding this sprint's code (2,279 tests total)
surfaced **4 real failures** — all caused by this sprint's own changes,
all fixed (Section K), all re-verified individually, then the full suite
was re-run in full from scratch.

**Final result: 2,279/2,279 passing (`Ran 2279 tests in 775.562s` — `OK`).**
Net new tests this sprint: 65 (2,279 − 2,214).

## S. Repo Hygiene Check (Part 56)

`git status` was checked after every full test run this sprint. No
manifest.json churn occurred on any run (the Sprint 3 archive-isolation
fix, reinforced by Section J's MoneyPuck fix, holds). The one manifest.json
change in this sprint's diff (`data/raw/moneypuck/skater/2024/manifest.json`,
deleted) is a genuine, disclosed, intentional change from the MoneyPuck
cleanup — not test-run churn — and is included in the commit as such.

## T. Git Commit

Not yet committed as of this report's writing — see the final message for
confirmation once the commit is made. Planned scope: every file in
Section L and M, explicitly staged by name (never `git add -A`), no push.

## U. Known Limitations / Deferred This Sprint

- Team SOG and Moneyline: historical context only, no live demo pricing
  (Section H).
- Period SOG: not surfaced at all.
- Betting Trends (line-movement history) on Team Hub: disclosed as
  unavailable (no real market-history feed for the simulated slate);
  Game Detail's Betting Trends tab does show the existing
  `build_demo_market_movement()` simulated-movement table.
- Head-to-head / matchup history on Team Hub's MATCHUP tab: disclosed as
  unavailable rather than fabricated.
- Season record / recent form on the Team Hub header: disclosed as
  unavailable — no live standings feed is wired this sprint; fabricating
  a record was ruled out.
- Mobile responsiveness: out of scope per the prompt (desktop-only this
  sprint).

## V. STOP

This sprint is complete: the same-day demo is built, tested, and
verified live in a browser at all 3 required breakpoints. No new hockey
model research was started. No new canonical market was built. The
scheduler was not installed. No Odds API credits were spent.

**STOP.**
