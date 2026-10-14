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
6. Preserved: accounts, ledger, historical tickets, settlement, public access, mobile layouts, Eggy design, personal last-name accounts with separate $500 bankrolls, the personal parlay builder, model/personal
   isolation. Personal-account creation/submission is NOT complete until the hosted write path works (needs `LOG_WRITE_TOKEN`) and the hosted two-account test passes.

**Superseded:** the five-ticket-a-day workflow (policy `anchor-tickets-2026-10-13`, digest `9f5e3448e10ff62e`, kept unchanged for the record); the 2026-10-11 policy `c236c526e00b9f04` (cap +0.10, approved
then, superseded 2026-10-13); "points add-on" credit priority (goals now take that credit).

## Policy and state
* Active, **unapproved**: `primary-slip-2026-10-14`, digest `1fad3ba03503cf94` (1 slot, ceiling +500; every other number identical to the cap-correction policy).
* Recording paused since 2026-10-13T15:35:57Z (owner request). Collection, settlement, publishing, prediction logging and personal accounts run.
* Release == master (pinned launchd jobs); hosted app auto-pulls master; a manual Reboot is needed for new modules, and the app must be loaded in a browser afterwards (one reboot crashed once).

## Backlog (five stories; a story is done only with the evidence named)
| # | Story | Acceptance (observable) | Status | Evidence so far | Next action |
|---|---|---|---|---|---|
| 1 | Per-game analysis board | All four sections for every scheduled game; coverage block per game; prior-season baseline; mobile ok; hosted | **IN PROGRESS** | Page live (desktop + 375 px), 2-game slate 2026-10-14; `tests/test_slate_board.py` | Verify on a populated slate: goals prices posted, a saves line, two shots picks per game; confirm the explanation text on a 16-game day |
| 2 | Primary-parlay selection and display | One slip +100..+500, fewest legs, recorded once at $10, shown on Slate Board and Today; none → reason + singles | **IN PROGRESS** | Selector ceiling + single slot tested through the real cycle on isolated data (`TestThePrimarySlipThroughTheNormalCycle`) | See a real qualifying slip on the hosted board; owner reviews it |
| 3 | Fresh-data integration and coordinated refreshes | Roster/schedule/box scores current; wave captures compare fresh prices across games; not-posted vs not-fetched | **IN PROGRESS** | Roster second pass, schedule check (29 games), season split, top-up, goals add-on | Observe a coordinated wave on a multi-game day with goals priced; confirm morning look ~08:00 ET |
| 4 | Hosted personal-account and builder submission | Create → build → submit → reopen → settle → reconcile for two accounts on the hosted app | **BLOCKED (owner)** | Tests with transport simulated (`tests/test_two_account_workflow.py`) | Owner: set `LOG_WRITE_TOKEN` (docs/OWNER_STEPS.md); then run the hosted test with the owner typing accounts and passcodes |
| 5 | Integrated hosted QA, settlement, release verification | Chain verified: data → board → slip → recording safeguards → settlement → reconciliation → postmortem; ZIP at the release | **IN PROGRESS** | Full suite 4,172 OK at 63c9b5d; first live wave settled and audited 2026-10-12 | Needs story 2's first real slip settled (recording is paused, so only after approval) |

## Owner actions (the exact minimum)
1. Review today's board, then decide on digest `1fad3ba03503cf94` (approve + resume, or not).
2. `LOG_WRITE_TOKEN` (docs/OWNER_STEPS.md, step 1) for story 4.
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
**Not changed, owner trade-off:** with the free odds plan the credit that used to buy lead games' points prices now buys anytime-goal prices, so the personal builder usually has no Points market
(`NHL_ENGINE_POINTS_ADDON=on` restores points; the 20K plan covers both).
**Known flaky guard:** `test_a_sixteen_game_night_is_searched_in_full_and_quickly` has a 60 s bound and fails on a loaded machine (load average about 6: 57-73 s measured, 61-69 s on the commit before this change);
the search itself did not slow down.
