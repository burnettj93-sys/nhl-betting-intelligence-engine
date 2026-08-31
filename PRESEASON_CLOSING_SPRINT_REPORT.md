# Preseason Closing Sprint Report

Real-browser QA (Track 7/8) against a genuinely running, curl-verified Streamlit server (never
assumed alive), plus targeted new build work (Game Detail combinations, click-through fixes,
prospective operations widgets, search enrichment). **8 real, reproducible bugs were found and
fixed, each verified live in the browser and covered by a new regression test.** Several tracks
in the original 13-track spec were not completed to full literal breadth this session — see
Section T for an honest accounting of what remains.

---

## A. Dashboard visibility — root cause and fix

The user reported twice, in two consecutive turns, that the dashboard was not visible, after this
assistant had asserted (without checking) that it was "running and confirmed working." Root
cause: the server process had died and a port mismatch existed between what was told to the user
(8765) and what `.claude/launch.json` actually specified (8501) — neither was ever verified with
`curl`. Fixed by starting the server, polling `curl -o /dev/null -w "%{http_code}"` until a real
`200`, confirming the process with `ps -p <pid>`, and opening it in the user's own browser. **This
curl-then-screenshot discipline was used before every claim of server health for the rest of this
sprint** — no "it's running" statement in this report is unverified.

## B. Total bugs found and fixed this sprint

**8**, all real, reproducible, found via actual browser interaction (not AppTest alone), each
fixed and covered by a new test in `tests/test_preseason_closing_sprint.py`.

## C. Bugs by severity

| ID | Severity | Summary |
|---|---|---|
| BUG-301 | HIGH | Game Detail's demo intelligence view was missing the "Combinations" section entirely — one of the six named pieces of Track 2's own gap list — despite the real joint-dependence math already existing and working on the standalone Combinations page |
| BUG-302 | HIGH | Goalie search results, and the Goalies page's own name-click button, routed to Player Intelligence — which only recognizes the skater roster — so clicking **any** goalie (confirmed with Connor Hellebuyck) crashed with "Player not found in the demo roster." This was a pre-existing bug from the prior sprint that an earlier status summary had incorrectly assumed was "already working." |
| BUG-303 | MEDIUM | Player Intelligence's "Best Available Market: NONE" empty state used a generic message for real players (confirmed: Auston Matthews, Cale Makar, Leon Draisaitl, and 9 others — ~25% of the 47-player demo roster) whose real recent-game history genuinely falls short of the SOG engine's own minimum-evidence bar (`PROJECTED_INACTIVE`). A real superstar's page showing a blank board with no explanation reads as broken, not as an honest model output. |
| BUG-304 | MEDIUM | Player Intelligence's "Active Status" header badge was hardcoded to always display `PROJECTED ACTIVE`, contradicting the real model's own status for any inactive player (compounds BUG-303). |
| BUG-305 | MEDIUM | Player Intelligence had no click-through from a player's next opponent to Team Intelligence, and no "Open Game Detail" button on the Next Game tab — a gap in Track 4's own clickability requirement for exactly this page. |
| BUG-306 | LOW | Game Detail's "Top Player Opportunities" cards had no click-through to Player Intelligence, unlike the equivalent section on Team Intelligence — an inconsistency, not a crash. |
| BUG-307 | LOW | Today page's 13-item System Health strip, rendered as `st.columns()`, wrapped labels mid-word at 1440px ("MoneyP uck", "DraftKin gs") — only visible via a real rendered screenshot, not AppTest. |
| BUG-308 | LOW | Today page had no global search box at all, despite Section 98's explicit requirement — a residual gap from the prior sprint, where the test suite had been given an explicit skip for this exact page rather than the page being fixed. |
| — | — | (Track 10 realism, not a functional bug) Next 5 Games could show the same simulated opponent up to three times in a five-game stretch (confirmed for Connor McDavid: OTT three times) — fixed by sampling without replacement from the 8-team opponent pool. |

## D. Bugs fixed

All 8, plus the Track 10 realism item:
- BUG-301: `dashboard/game_detail_view.py::game_combinations()` (new, reuses the real
  `gaussian_copula_joint_upper_tail` / `logical_control_probability` functions and `COMBO_SPECS`
  pattern from `dashboard/pages/28_Combinations.py` verbatim — never a second implementation of
  the dependence math), wired into `dashboard/pages/2_Game_Detail.py`'s demo branch. Covered by
  `Test01GameCombinationsRealDependence` (3 tests).
- BUG-302: `dashboard/components.py::_route_to_search_result()` now routes `GOALIE` results to
  Team Intelligence (which already has a real per-team goalie section) instead of Player
  Intelligence; `dashboard/pages/27_Goalies.py`'s own button fixed the same way. Covered by
  `Test05GoalieRoutingFix` (4 tests).
- BUG-303/304: `dashboard/demo_data.py::player_activity_status()` (new — calls the real
  `research.player_sog.live_projection.project_player_sog()` directly to surface its own
  `status`/`note`, never reimplementing the activity gate), wired into both the header badge and
  the empty-state caption in `dashboard/pages/25_Player_Intelligence.py`. Covered by
  `Test04InactivePlayerHonestMessaging` (4 tests).
- BUG-305: two new buttons in `dashboard/pages/25_Player_Intelligence.py` (opponent → Team
  Intelligence, Next Game → Game Detail). Covered by `Test02PlayerIntelligenceOpponentClickThrough`
  (2 tests).
- BUG-306: one new button per card in `dashboard/pages/2_Game_Detail.py`'s Top Player
  Opportunities loop. Covered by `Test03GameDetailPlayerClickThrough` (1 test).
- BUG-307: replaced the 13-column layout with wrapping HTML chips in
  `dashboard/pages/21_Today.py` (fixed earlier in this session, before this report was written;
  re-verified live in this pass).
- BUG-308: added `comp.render_global_search(key_prefix="today")` to
  `dashboard/pages/21_Today.py` and removed the corresponding test's skip branch (fixed earlier
  in this session; re-verified live in this pass).
- Track 10 realism: `dashboard/player_intelligence_view.py::next_five_games()` now shuffles the
  8-team opponent pool once and draws without replacement instead of `rng.choice()` with
  replacement. Covered by `Test12NextFiveGamesRealism` (checks all 47 roster players, not just
  McDavid).

## E. A note on browser-automation methodology (not a product bug)

During this pass, `left_click` on this app's react-aria-based search input and BaseWeb-style
selectboxes did not reliably transfer real DOM focus or commit a selection through the
automation tool's synthetic mouse events — confirmed by checking `document.activeElement` and
widget `.value` directly after each click. This is a quirk of the browser-automation tool against
this specific component library, not a Streamlit or product defect: a real mouse click from an
actual user reliably focuses and submits these same elements (this is standard browser behavior
for `<input>` and listbox options). The reliable workaround used for the rest of this session was
dispatching a full `pointerdown`/`mousedown`/`pointerup`/`mouseup`/`click` event sequence via
`javascript_tool`, and for the search text input, setting `.value` via the native property setter
plus a synthetic `Enter` `KeyboardEvent`. One transient false alarm this produced during testing:
an initial "Validation Status: VALIDATED still shows 175/175 unfiltered" reading was traced to
insufficient wait time after a rapid double-interaction, not a filter bug — re-tested cleanly with
more settle time and confirmed correct (175 → 140, excluding the 35 `POINTS` rows, exactly as
expected from `EMPIRICAL_BASELINE_REMAINS_CHAMPION`'s real registry status).

## F. Track 2 — Game Detail enrichment

Confirmed live (EDM @ COL demo game) via `get_page_text` and DOM inspection: Win Model (real Elo,
simulated matchup), Team SOG (real `TeamSogEngine`, simulated matchup), Starters & Goalie Saves
(honest "no mapping" when absent), Top Player Opportunities (6 cards, now clickable — BUG-306),
Full game prop table (35 rows, canvas-rendered `st.dataframe`, confirmed via `textContent` since
`get_page_text` cannot see canvas-virtualized grids), **Combinations (new — BUG-301)**, Context
Active (per-player NOT_ELIGIBLE/eligible), Waiting On, Data Freshness. No exceptions on any of the
6 demo games in `dashboard/demo_data.build_demo_games()`.

## G. Track 3 — Odds Detail panel

Confirmed live from Player Props Cards view: `comp.render_odds_detail_panel()` renders the
`⚠ SIMULATED MARKET PRICE` label, Player/Current Odds/Decimal/Max Buy/Status/Raw P/No-Vig
P/Fair Odds/Edge/Decision — all real numbers pulled from the same opportunity dict, no
duplicated math. Covered by `Test10OddsDetailPanel`.

## H. Track 4 — Click-through audit (partial; see Section T for what's left)

Fixed and verified this sprint: Player Intelligence → Team Intelligence (opponent) and → Game
Detail (next game) [BUG-305]; Game Detail → Player Intelligence (top opportunities) [BUG-306];
Goalies page and goalie search results → Team Intelligence instead of crashing [BUG-302]. Already
working, re-confirmed: Team Intelligence → Player Intelligence (top opportunities) and → Game
Detail; Player Props → Player Intelligence and → Odds Detail; Today's Demo Slate → Game Detail.
**Not audited this sprint**: inline market-name text within opportunity cards (only the page-level
Market filter and global search route to a market; text inside a rendered card is not itself
clickable).

## I. Track 5 — Player Props filters

All 5 new filters (Player, Team, Validation Status, Context, Price) confirmed live: Player
filtered 175 → 5 (all Connor McDavid rows); Validation Status filtered 175 → 140 (VALIDATED,
correctly excluding the 35 POINTS rows, whose real registry status is
`EMPIRICAL_BASELINE_REMAINS_CHAMPION`) and 175 → 35 (that status alone). Covered by
`Test09PlayerPropsFilters`.

## J. Track 6 — Search richness (light touch, matching/ranking untouched)

Added, without touching `search()`'s matching or ranking logic: player/goalie subtitles now show
`Next: vs <OPP> · <time>` (e.g. `EDM · C · PLAYER · Next: vs COL · 7:00 PM ET`); market subtitles
now show a real demo-row count (e.g. `DERIVABLE_NOT_VALIDATED · MARKET · 35 demo rows`), computed
from the same `build_demo_opportunities()` used everywhere else — never a separate count. Covered
by `Test11SearchSubtitleRichness` (4 tests, including one asserting ranking is unaffected).

## K. Track 7 — Real browser walkthroughs

**McDavid**: search → select → Player Intelligence header/hero/metrics → Next Game
(Best/Watchlist/Waiting/Passes groups, all real) → Next 5 Games (5 distinct simulated opponents,
"NOT POSTED" prices, never fabricated) → Markets (full 5-row real table) → opponent click-through
to Team Intelligence (COL, real goalie section, real Top Player Opportunities) → Open Game Detail
→ full Game Detail intelligence view including the new Combinations section → player click-through
back to Player Intelligence (Brent Burns). All steps produced zero exceptions and real,
internally-consistent numbers throughout.

**Auston Matthews** (second star): found and fixed BUG-303/304 (see Section C/D) — his real
recent-game history in this project's historical corpus falls short of the SOG engine's own
recency bar, and the page now says so honestly instead of looking broken.

**Cale Makar** (defenseman): also genuinely `PROJECTED_INACTIVE` under the same real engine gate
(confirmed via `dd.player_activity_status`, not fabricated) — this means the Blocks/SOG UI could
not be visually confirmed for an active defenseman this session; his page correctly shows the same
honest empty state as Matthews. **Not yet confirmed**: Blocks/SOG UI for an active defenseman
(needs a different demo-roster defenseman with `PROJECTED_ACTIVE` status).

**Connor Hellebuyck** (goalie): found and fixed BUG-302 — the goalie path is now Search →
Team Intelligence (WPG) → real goalie card (`Connor Hellebuyck — PROJECTED STARTER (82%)`,
expected saves 22.6), confirmed both from global search and from the Goalies page itself.

**~25% of the 47-player demo roster is `PROJECTED_INACTIVE`** under the real SOG engine's own
recency gate (12 players, including three real stars: Draisaitl, Matthews, Makar) — this is a
real, disclosed data characteristic of the historical corpus relative to the simulated date, not a
bug and not something to fabricate around. It does mean roughly 1 in 4 demo-roster lookups will
show the (now honest) inactive state rather than a full market board.

## L. Track 8 — Responsive QA

1440px: found and fixed BUG-307 and BUG-308 (Section C/D). 1200px: Player Props' 9 selectboxes
(2 rows of comboboxes) all render with intact, non-truncated labels — confirmed via the
accessibility tree, not just a screenshot. 900px: structural content confirmed complete and
exception-free via `get_page_text` and `read_page` on Today, Player Props, and Game Detail (the
in-pane screenshot renderer itself produced visually unreliable captures at 900×900 — a pane
scaling artifact, not a page bug, so text/DOM inspection was used as the authoritative check
instead); one cosmetic, non-blocking wrap (`Validation Status` label breaking to two lines in the
5-column filter row) was observed and judged acceptable, not fixed. **Not done this sprint**: a
dedicated 1200px/900px pass on Player Intelligence, Goalies, Combinations, Market Movement,
Ledger, and System & Model Health specifically (Today/Player Props/Game Detail were the three
checked at all three breakpoints).

## M. Track 9 — Visual/terminology polish

No dedicated full pass this sprint; fixes in this report (chip wrapping, consistent click-through
button labeling as `Open <X> — <Page>`) are the only terminology/visual changes made, applied
consistently across the new buttons added in Section D.

## N. Track 10 — Demo realism

Confirmed unchanged and still correct: 175 total demo opportunities, exactly 4 `BET` (not
increased). Fixed the Next 5 Games opponent-repeat issue (Section C, Track 10 item). No other
realism issues found in this pass.

## O. Track 11 — Prospective operations widgets

New `operational/prospective_ledger.py::operational_summary()` (total rows, recorded-today count,
pending-past-event-start settlement count, last-recorded timestamp, per-checkpoint breakdown —
honest zeros on an empty ledger, never fabricated). Wired into:
- **Today page**: new "Prospective Recording" section (Model observations today / Shadow
  observations / Pending settlement / Last recorded), or an honest "not recorded yet" caption
  when the ledger DB doesn't exist.
- **Ledger page**: new "Prospective Recording Status" summary strip plus a per-checkpoint
  breakdown line, above the existing four record-type tabs (unchanged).

Both confirmed live with the real (currently empty) ledger — correct honest-zero rendering, no
fabricated activity. Covered by `Test06OperationalSummary` (4 tests) and
`Test07LedgerPageOperationalWidgets` / `Test08TodayPageProspectiveWidgets`.

## P. Track 1 — Prospective recording call sites

**Not rebuilt or re-verified this session.** Per the conversation record, `operational/
prospective_ledger.py`, `operational/prospective_recording.py` (with the `DEMO_NOT_RECORDABLE`
guard and MODEL_REGISTRY eligibility gate), and the `record_daily_predictions.py` /
`settle_daily_observations.py` CLI entry points were built in the portion of this sprint before
this session's context was summarized. This session's full test suite run (Section Q) exercises
their existing tests and all pass, but this session did not perform new, additional live QA on
Track 1 specifically beyond what the full suite already covers. `FIRST_LIVE_NHL_DAY_CHECKLIST.md`
step 21 was stale (said "does not exist yet") and has been corrected (Section S).

## Q. Test suite

New file: `tests/test_preseason_closing_sprint.py`, **28 tests**, all passing, covering Sections
B/C/D of this report (combinations, click-through fixes, honest-inactive messaging, goalie
routing, operational widgets, search subtitle richness, Next-5-Games realism) plus one smoke test
each for the Ledger and Today pages' new sections.

Full suite: **1,844 tests, all passing** (`python3 -m unittest discover -s tests -p "test_*.py"`),
up from the 1,816 baseline at the start of this session (1,814 prior baseline + 2 bug-fix tests
already added earlier this session, both carried forward unchanged) plus this sprint's 28 new
tests. No existing test was weakened, skipped, or deleted — the one existing test change this
sprint was removing `tests/test_preseason_interactive_product.py`'s explicit skip of the Today
page from the "every new page calls `render_global_search`" check, which now correctly holds
Today to the same standard as every other page (this made the check *stricter*, not weaker).

## R. Production-boundary / frozen-model check

`git status` confirms no tracked frozen file was modified this session: only `.gitignore`,
`README.md`, and `requirements.txt` show as modified-tracked; every dashboard/operational/test
file touched this sprint is new, untracked work (this repository's entire history is a single
`Snapshot: full history through MoneyPuck data-contract review` commit — everything since then,
across many sprints, has lived only in the working tree, since committing was never requested).
The existing hash-check regression tests (`Test85DecisionPolicyUnchanged`,
`Test86ValidatedMarginalsUnchanged`, `Test87JointHashesUnchanged`,
`Test88OverlayParametersUnchanged`) all still pass. **This repository currently has a large amount
of uncommitted work spanning many sprints** — worth a deliberate commit decision outside this
report's scope, since committing was never requested this session.

## S. Documentation updates

`FIRST_LIVE_NHL_DAY_CHECKLIST.md`: steps 21-23 updated to reflect that the shadow/prospective
ledger, recording orchestration, and CLI entry points now exist (they did not when that checklist
was last written), while being explicit that none of it has been exercised against a real live
game day yet — that remains the actual blocker, not "does the code exist." `PROSPECTIVE_
VALIDATION_PROTOCOL.md` was read and left unchanged: it is explicitly pre-registered and frozen,
and its schema requirements already match what `prospective_ledger.py` implements, so no
correction was needed.

## T. What this sprint did NOT complete (honest accounting)

Given the scope of the original 13-track spec, the following were not done to full literal
breadth this session:
- The full enumerated 28-step McDavid walkthrough was not checked off as a literal numbered list,
  though its substance (search → player → tabs → decision groups → opponent → game detail →
  combinations → back) was covered end-to-end (Section K).
- Blocks/SOG UI was not visually confirmed for an *active* defenseman (Makar, the intended
  subject, turned out to be genuinely `PROJECTED_INACTIVE` — see Section K).
- Responsive QA at 1200px/900px was done for 3 of the 9 named pages (Today, Player Props, Game
  Detail); Player Intelligence, Goalies, Combinations, Market Movement, Ledger, and System &
  Model Health were not individually checked at those breakpoints this session (Section L).
- Inline market-name text inside opportunity cards is still not clickable (only the page-level
  filter and global search route to a market) — Track 4's narrower reading of "market labels
  clickable" is not fully closed (Section H).
- No dedicated Track 9 visual/terminology consistency pass beyond what was needed to fix the bugs
  in Section C.
- Track 1's recording call-sites were not independently re-verified beyond the existing (passing)
  test suite this session (Section P).

## U. Final Questions

1. Given ~25% of the demo roster is genuinely `PROJECTED_INACTIVE` (Section K), is that an
   acceptable demo characteristic to leave disclosed-but-present, or should the demo roster be
   curated to favor players with recent, active real histories — and if so, on what basis (not
   simply "whichever players show a full board," which would start to resemble curating for
   effect)?
2. Should the remaining responsive-QA breakpoints (Section L) and the literal 28-step walkthrough
   checklist (Section T) be completed in a dedicated follow-up pass, or is the verification done
   this session (same components, different pages) sufficient?
3. This repository has a large body of uncommitted work spanning many sprints (Section R) — is a
   deliberate commit (or a series of them) wanted now, and if so, at what granularity?
4. Should inline market-name/team-name text inside opportunity cards become clickable (closing the
   remainder of Track 4), or is the current filter/search-only routing considered sufficient?

---

**STOP AFTER PRESEASON CLOSING SPRINT.**
