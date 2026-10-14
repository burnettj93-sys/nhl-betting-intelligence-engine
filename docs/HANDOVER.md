# Handover — NHL betting app (updated 2026-10-14)

Resume from this file alone. Repository: `/Users/johnburnett/Downloads/nhl_engine 2` (GitHub `burnettj93-sys/nhl-betting-intelligence-engine`, branch `master`). It is **not** the Yahoo repository
(`/Users/johnburnett/yahoo-fantasy-cockpit`); nothing here attaches to it.

## Authoritative requirements (the owner's 2026-10-14 master prompt; it supersedes conflicting earlier selection requirements)
1. For every game of the Eastern day: most likely goal scorers (with DraftKings price; likelihood is not value); the two best shots-on-goal values (best supported threshold from the offered ladder);
   goalie saves (starter status/source/time, expected saves and range, line and price, conditional until the starter is confirmed); moneyline or puck line (exact market, line, price) or "No recommended side".
2. ONE primary parlay, +100 to +500 inclusive, the fewest legs, strongest supported hit chance with price and value considered; never a same-game combination without a quoted combined price; $10 paper stake;
   if nothing qualifies, show the strongest individual selections and the reason. No weaker extra slips.
3. Corrected shots cap **+0.02 retained**; no inflated probabilities, no weakened safeguards, no loosened thresholds to produce a slip.
4. Full-slate coverage: analysed / priced / supported / recommended / missing-and-why for every game; "not posted" vs "not fetched" vs "budget blocked" vs "fetch failed"; useful around 8 AM ET and updated all day.
5. **Automatic recording stays PAUSED** until the owner reviews the completed live slip and explicitly approves activation (the owner runs `ticket_policy approve <digest>` then `recording_pause resume`).
6. **Scope correction (2026-10-14, supersedes item 6 of the earlier prompt):** bets submitted by individual people were REMOVED: no last-name accounts, passcodes, personal bankrolls, bet submission,
   personal-order jobs or `LOG_WRITE_TOKEN`. Kept: the model's original $500 paper book (history intact), automatic recording controls, settlement, reconciliation, prediction scoring, postmortems, public access,
   mobile layouts, Eggy design, and the Parlay Builder as a READ-ONLY tool (hypothetical stake and return; it never writes the model book; a same-game slip stays an unpriced draft). The earlier personal records are an
   inactive archive (`operational/archive/`, `deploy/archive_personal_logs.py`), separate from model accounting. The launchd job `com.nhlengine.manual-order-job` was unloaded and its plist moved to
   `~/Library/LaunchAgents.disabled/`; only the owner evidence queue (goalie confirmations, Ontario checks, path checks; docs/MANUAL_ORDERS.md) remains.
7. **Eggy's Parlay Pick of the Day:** the single primary ticket is shown as a branded card (Slate Board, Today) with prices, estimated hit chance and range, $10 stake, return and a short explanation; no pick
   shows "No Eggy pick today" with the reason. It is the same ticket, not an additional bet. At most one primary ticket per Eastern day at exactly $10 is enforced in the recording path itself
   (`daily_tickets.record_tickets`), not only on the page.

**Superseded:** the five-ticket-a-day workflow (policy `anchor-tickets-2026-10-13`, digest `9f5e3448e10ff62e`, kept unchanged for the record); the 2026-10-11 policy `c236c526e00b9f04` (cap +0.10, approved
then, superseded 2026-10-13); "points add-on" credit priority (goals now take that credit).

## Policy and state
* Active, **unapproved**: `primary-slip-2026-10-14`, digest `1fad3ba03503cf94` (1 slot, ceiling +500; every other number identical to the cap-correction policy).
* Recording paused since 2026-10-13T15:35:57Z (owner request). Collection, settlement, publishing and prediction logging run.
* Release == master (pinned launchd jobs); hosted app auto-pulls master; a manual Reboot is needed for new modules, and the app must be loaded in a browser afterwards (one reboot crashed once).

## Backlog (five stories; a story is done only with the evidence named)
| # | Story | Acceptance (observable) | Status | Evidence so far | Next action |
|---|---|---|---|---|---|
| 1 | Per-game analysis board | All four sections for every scheduled game; coverage block per game; prior-season baseline; mobile ok; hosted | **IN PROGRESS** | Page live (desktop + 375 px), 2-game slate 2026-10-14; `tests/test_slate_board.py` | Verify on a populated slate: goals prices posted, a saves line, two shots picks per game; confirm the explanation text on a 16-game day |
| 2 | Primary-parlay selection and display | One slip +100..+500, fewest legs, recorded once at $10, shown on Slate Board and Today; none → reason + singles | **IN PROGRESS** | Selector ceiling + single slot tested through the real cycle on isolated data (`TestThePrimarySlipThroughTheNormalCycle`) | See a real qualifying slip on the hosted board; owner reviews it |
| 3 | Fresh-data integration and coordinated refreshes | Roster/schedule/box scores current; wave captures compare fresh prices across games; not-posted vs not-fetched | **IN PROGRESS** | Roster second pass, schedule check (29 games), season split, top-up, goals add-on | Observe a coordinated wave on a multi-game day with goals priced; confirm morning look ~08:00 ET |
| 4 | Read-only Parlay Builder | Browse players/markets/lines, build a slip, edit/remove, hypothetical stake, odds and return; no accounts, no submit, no model-book write; same-game = unpriced draft | **DONE (tests); hosted QA pending** | `tests/test_parlay_builder_page.py` (read-only source scan, no model-book write) | Hosted check after the reboot |
| 5 | Integrated hosted QA, settlement, release verification | Chain verified: data → board → slip → recording safeguards → settlement → reconciliation → postmortem; ZIP at the release | **IN PROGRESS** | Full suite 4,077 OK on the scope-removal branch (2026-10-14; 4,172 before the personal-account tests were removed); first live wave settled and audited 2026-10-12 | Needs story 2's first real slip settled (recording is paused, so only after approval) |

## Owner actions (the exact minimum)
1. Review today's board, then decide on digest `1fad3ba03503cf94` (approve + resume, or not).
2. (No personal-account credential is needed any more.)
3. Odds credits: the free plan prices about 2-3 games a day. Full coverage of a 16-game day (shots + anytime goals + morning look + confirmed-saves lines + moneylines) needs roughly 55-60 credits a day;
   the 20K tier ($30 a month, not purchased) covers it. Starting-goalie confirmation has no permitted automatic source: an owner can record a confirmation by hand (operational/goalie_confirmations).

## Storybloq status (honest)
`/Applications/Storybloq.app` (v1.1.3, a macOS viewer for a `.story/` project folder) is installed, but in this Claude Code environment there is **no** `/story` skill, no Storybloq MCP tool, no
`storybloq` command, no hooks and no review backend, and **no `.story/` folder in this repository**. Nothing was created, because the owner said not to create a new project. This backlog and handover
therefore live in this file. To use Storybloq: install its Claude Code integration and open or initialise this repository's folder in it; this file maps one-to-one onto its tickets.
Settings the owner asked for once it exists: `maxTicketsPerSession = 0`, test command `python3 -m unittest discover -s tests -t .`, independent review on, production ledger writes and deployment serialized.
Usage-limit recovery and hooks are not verified and no unattended or automatic wake-up is claimed; the scheduled launchd jobs are what keeps collection, settlement, publishing and scoring running.

## Boundaries
Production ledger mutations and deployment are serialized (one trader process holds a lock). Tests use isolated data. Never run `ticket_policy approve` or `recording_pause resume` without the owner's explicit instruction.

## Review of PR 139 (independent code review, 2026-10-14) — what was fixed
Fixed: moneyline freshness judged by quote age (not the row status); empty-board reason names combinations above the +500 ceiling; the primary slip shows RECORDED in the cycle that records it;
thin goalie projections no longer break the board; the haircut comes from the policy; `slot_count()` fails safe to one; the previous-day check parses only that day's log records; live saves-line path tested.
**Not changed, owner trade-off:** with the free odds plan the credit that used to buy lead games' points prices now buys anytime-goal prices, so the read-only builder usually has no Points market
(`NHL_ENGINE_POINTS_ADDON=on` restores points; the 20K plan covers both).
**Timing guard (resolved 2026-10-14):** the 16-game search guard used to take 58-73 s; the search now skips slips already covered by a passing prefix (exact: same 3,676 tickets on that pool) and takes about 4 s; the bound is 30 s.

## Review rounds on the primary slip (2026-10-14)
Round 1 (PR 142) and round 2 (PR 143) code-review findings are fixed with regression tests: fail-closed policy loading (`ticket_policy.checked_active`, blocked cycle, watchdog `ticket_policy` check),
moneyline freshness from the provider quote time against `quote_freshness.price_limit_min` (both sides, again when the hosted page is opened), rejection reasons by cause (below +100 / above +500 / value / other),
primary slip RECORDED in the recording cycle, per-game failure isolation, points planned after goals with explicit budget gaps in the builder, shared price helpers, observable saves-capture read errors and
policy errors, incremental previous-day scan. Points support: bought after the morning look and anytime goals with what the day has left (free plan: usually none; the builder says why).

## Live-workflow status (2026-10-14, after the second review round) — what was checked and why items are open
| Item | Source / capture state found | Status |
|---|---|---|
| Two shots picks in one game | Both games of today's slate show two picks with the ladder, quote age and STALE/fresh flag, on the hosted Slate Board | **Verified live (hosted)** |
| Posted anytime-goal prices | DraftKings does post them (35 real outcomes archived 2026-10-10 for a 1 PM game, posted the evening before). Today's plan (made 09:53Z) bought points, not goals; the credit order now funds goals for the lead games before the display refresh/tomorrow check and migrates saved plans (tests). The pregame wave (about 21:45Z) is the first capture that can include them | **Pending the live pregame wave**; parser and board verified on the real archived payload |
| A real saves line | DraftKings posts them (44 archived payloads, 40 with outcomes, last 2026-10-08). The engine buys a saves line only for a confirmed starter, and no permitted automatic source confirms starters, so none is requested | **Blocked on starter confirmation**; archived real payloads parse and match 10 of 10 goalies by name; board shows the projection as conditional |
| A qualifying primary slip | Replay of all 18 frozen snapshots with anchor fields through the primary-slip policy: 0 qualify (closest: -0.1% estimated value before the haircut on 2026-10-12 21:18Z; the old rule found 2 tickets there). The board now shows the closest non-qualifying combination | **Not observed**: the value bars have never been met under the corrected estimates in the logged period |
