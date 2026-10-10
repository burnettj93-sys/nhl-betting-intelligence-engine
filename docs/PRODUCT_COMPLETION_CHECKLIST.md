# Product completion checklist — one complete list (revision 12, 2026-10-10)

This is the single checklist. Every requirement from the owner's brief appears once, with its status and the evidence. Earlier "finished" claims were not used as evidence: each row was re-checked, on the
hosted app where it is a hosted feature. Nothing here claims a betting edge; the model's value over the price has never been shown (there are no historical sportsbook prices).

## Legend

| Word | Meaning |
|---|---|
| **VERIFIED** | Built, tested, and seen working on the hosted app or in the live engine (evidence named). |
| **VERIFIED IN TESTS** | Built and covered by automated tests (and a fixture or isolated run), but the live or hosted step is not possible yet; the reason is stated. |
| **PARTIAL** | Works, but a stated part does not. |
| **EXTERNAL BLOCKER** | Needs an action only the owner or a third party can take; the exact action is stated. Nothing on this list can be finished by code. |
| **AWAITING OWNER APPROVAL** | Done as a proposal; deliberately not switched on. |
| **PENDING** | Needs a live event that has not happened yet. |

## Summary

**Verified now:** the public hosted app opens signed out in a phone-sized view with no sign-in (real-phone Safari Private not tested); Today shows the paper book split into parlay tickets and single bets, the pause, the
ticket policy and the 8:00 AM run; the Paper Parlay Builder lists real DraftKings lines with quote ages and adds a fresh leg to a slip; the engine's account arithmetic, builder checks and model-book isolation for
two separate accounts (real pages plus the real engine, transport simulated); the model book ($435.76) agrees three ways and with the official box scores; collection, settlement, publishing and shadow logging run
while automatic recording is paused.

**Partial:** several price refreshes a day for every game (credit-limited: 3 of 14 games priced today); the daytime board (moneyline prices go stale between pulls); player and goalie data (one day behind until the new
13:30 / 17:30 source re-checks have run for a day); Eggy interface (polished, a first-deep-link quirk is now handled cosmetically).

**External blockers (owner or third party):** `LOG_WRITE_TOKEN` (hosted account creation and submit, so the hosted two-account test); Odds API key rotation; Ontario price/void-rule verification; a permitted source for
lineups, power-play units, injuries and starting goalies; the odds budget decision (a plan that prices every game); one paid credit to certify the puck-line contract; the real-phone Safari Private check.

**Awaiting your approval:** the restart policy (`docs/POSTMORTEM_2026-10-10.md` section 13). Automatic recording stays paused until then; a technical gate refuses to resume without it.

## Versions (one commit everywhere)

| What | Commit | Evidence |
|---|---|---|
| `master` = pinned release the scheduled jobs run from = hosted app = published data = this ZIP | the commit named in the ZIP's file name (`nhl_engine_audit_<commit>.zip`) | The watchdog's `release_pinned` check compares the release with `origin/master` every 30 minutes; Diagnostics shows the hosted app's and the data's commit. |
| Tests on the final code | run recorded in the delivery message and in `audit_evidence/test_results.txt` | The full suite is run on the final code, with skips named (two: the puck-line real-payload certification and a missing local corpus file). |

## A. The automatic engine and the restart

| Requirement | Status | Evidence / limit |
|---|---|---|
| Up to five worthwhile parlays a day, +100 or better, a strong estimated hit chance, defensible value, empty slots allowed | **AWAITING OWNER APPROVAL** (enforced in code as a proposal) | The selector takes a `TicketPolicy` (`research/real_market_parlay/policy.py`): hit-chance floor 25%, at least +3% edge after the 3-point haircut, one ticket per player, two per game, up to five, zero allowed. Every number is a judgement, none validated; digest `cb34b5563d244396`. Tests: `tests/test_ticket_policy.py`. |
| Show the ticket the policy kept, and whether the engine can find such tickets regularly | **DONE (honest answer: not reliably)** | `docs/POSTMORTEM_2026-10-10.md` section 12b: Coleman 2+ shots (−135) + Lee 2+ shots (−140), +198, estimated 38.7%, 1-SD range 31.6–45.8% against a break-even of 33.5%, +$1.55 expected on $10 (+$0.47 after the haircut). About one such ticket in three days from 3 to 6 games priced a day; coverage is the binding limit; three days cannot say how often a full slate would. |
| Automatic recording paused; collection, settlement, publishing, personal accounts, shadow log running | **VERIFIED** (paused since 2026-10-10 08:23 ET) | `operational/recording_pause.py` covers the ticket selector and every other automatic writer; fails safe; hosted Today shows the pause; the watchdog warns after 3 days. **Resume is refused until the owner approves the active policy by digest** (checked live). |
| Automatic moneyline and prop singles out of the parlay experiment; results separate; reconciled; nothing reset | **VERIFIED** | `record_paper_bet` refuses automatic singles (`EXCLUDED_SINGLE`; observations still logged). Today and Paper Performance show parlay tickets (11, 2W–9L, −$54.24), the earlier single (1, 0W–1L, −$10.00) and the whole ($435.76, −$64.24); the watchdog re-checks that they add up. `tests/test_book_breakdown.py`. |
| Reduced shared-player exposure | **VERIFIED IN TESTS** (and in the replay) | One ticket per player in the proposal; the per-player limit of 2 in force from 2026-10-09; the replay reproduces the recorded Oct 8 tickets only without a limit. |
| Postmortem of the nine consecutive losses | **DONE** | `docs/POSTMORTEM_2026-10-10.md` + frozen evidence under `docs/validation/` (ledger extract, official box scores, audited candidate pools, dependence estimates): 8 parlay tickets + 1 moneyline single; 12 of 12 settlements agree with the official box scores; the streak is compatible with variance under the model's assumptions (17.1–18.4% that all eight tickets lose, conditional on the recorded probabilities); shared exposure and weak acceptance demonstrated; overstated probabilities a hypothesis. The 25% floor explanation was corrected (a two-leg ticket needs the legs' probabilities to multiply to 0.25, not each to exceed 50%) and the +3% margin is stated not to verify Ontario prices or settlement rules. |
| Forward evidence on the legs the selector picks | **PENDING** (collecting) | Shadow selection log + scorer (`operational/shadow_selection.py`, `deploy/score_selection_shadow.py`); verdict "NOT ENOUGH YET": about 690–1,230 independent resolved units are needed (`docs/POSTMORTEM_2026-10-10.md` section 11). |
| Selector audit (rank by hit chance, exposure limits) | **VERIFIED** | `docs/SELECTOR_AUDIT.md`: on 2026-10-08 the greedy set equals the best possible set under the same limits. |
| Shots (alternate) | **VERIFIED** | Full chain price → identity → probability → ticket → settlement → page; 14 ticket legs, 8 won. |
| Points | **PARTIAL** | Model validated; live sample tiny (the positive-edge points legs hit 4 of 25 against 9.1 expected on Oct 8–9: watch, do not act). |
| Anytime goals | **PARTIAL** | Model validated; priced only for games the credit plan covers; no ticket has used it. |
| Goalie saves | **EXTERNAL BLOCKER** (starter source) | Model validated; no saves leg can be priced until a start is confirmed and no permitted automatic source exists. |
| Moneyline | **PARTIAL** | Priced by the T-35 pull; Elo path; the strength model is not promoted; the legacy win model's probabilities are squeezed toward 50% (76 side-observations) and its singles are out of the experiment. Cannot be validated forward in practical time (`docs/POSTMORTEM_2026-10-10.md` section 11). |
| Spread / puck line | **EXTERNAL BLOCKER** | Settlement, shape validator and leg builder are done and tested; the provider contract needs one real payload (1 credit, owner authorisation) and the model is unvalidated and kept out of selection. |
| Paper book: $10 per ticket, atomic funds, frozen entry data, no duplicates, official settlement | **VERIFIED** | `docs/SETTLEMENT_AUDIT.md`; all 12 settled bets checked against official box scores; the watchdog re-derives the book three ways. |

## B. Data, prices and refreshes

| Requirement | Status | Evidence / limit |
|---|---|---|
| Real, current data (no demo content); odds freshness from the provider's quote time | **VERIFIED** | Header line on every page; Data Status; no simulated wording (page-wide test). A price is judged when the page is opened (stale marked with its age, never added, never selected). |
| 8 AM board | **VERIFIED (the run) / PENDING (a populated card)** | The scheduled 08:00 ET run happened by itself on 2026-10-10 (08:00:05 ET, DONE, 14 of 14 games looked at, 3 credits, shots posted for the 3 games the plan covered) and the hosted Today strip showed it (`docs/MORNING_WORKFLOW.md`). No qualifying card resulted; points and goals at 8 AM are still unobserved (shots only were fetched). |
| Daytime refreshes | **PARTIAL (credit-limited)** | Morning look (3 games), a display moneyline pull, pregame captures (2 games). Several refreshes for every game need about 46 credits a day (79 for every market) against about 11 affordable (`credit_plan_state.json`); the smallest sufficient plan is 20K at $30 a month, not purchased. Moneyline prices on Today go stale between pulls and say so. |
| Current player and goalie information | **PARTIAL** | Player and goalie logs come from MoneyPuck, which publishes the night's games after the 07:00 download; the pages said "through Oct 8" all morning on Oct 10 while the source had updated by afternoon. **Repair:** a free re-check at 13:30 and 17:30 ET (`operational/moneypuck_refresh.py`, job `com.nhlengine.moneypuck-refresh`, watched by the watchdog). Lineups, line combinations, power-play units, injuries: **EXTERNAL BLOCKER** (no permitted source; the Daily Faceoff scraper stays off). Goalies: season and recent stats, start chance, expected saves/goals against with ranges; confirmation is manual only. |
| Tomorrow view | **VERIFIED** | Page `Tomorrow`; early prices stale after 12 h or from an earlier Eastern day; every availability check recorded. |
| Distinguish "not posted" / "not fetched" / "budget prevented" | **VERIFIED** | `operational/price_availability.py`; shown on Today, Best Options, Tomorrow, Diagnostics. |
| Odds budget and every paid job audited; nothing purchased | **VERIFIED / EXTERNAL BLOCKER (decision)** | Costs confirmed against 1,331 archived provider calls (counter 218 = headers 218); `docs/ODDS_BUDGET_CONFIGURATIONS.md`, `docs/ODDS_CREDIT_AUDIT.md`. The budget decision is the owner's. |

## C. Pages, access and interface

| Requirement | Status | Evidence / limit |
|---|---|---|
| Best +100 option per player and goalie (single, else a labelled estimated parlay; no invented prices) | **VERIFIED IN TESTS** / populated state **PENDING** | Option cards or the exact reason there is none; populated cards seen on the hosted app 2026-10-09 5:16 PM ET (12 options); the morning-priced state needs a qualifying morning price. |
| Public access on a phone or computer without a Streamlit sign-in | **VERIFIED (signed out, phone-sized view) / EXTERNAL BLOCKER (real phone)** | Hosted app "public and searchable"; opens signed out in the built-in browser at 375 px and desktop, and by anonymous request. **Real-phone Safari Private was not tested** (no simulator available): please open the app link in a Safari Private tab and confirm Today loads without a sign-in. |
| Public visitors cannot reach model-account writes or admin actions | **VERIFIED** | Visitors have the USER role; admin pages need the owner passphrase secret (not configured); evidence writes need an allow-listed login plus a token. A deep link opened first after a restart runs the page script alone; the page now applies the theme and hides the raw page list itself (`ui.standalone_page_fallback`). |
| Eggy interface, polished | **VERIFIED / PARTIAL** | Layered slate theme, bet-slip cards, two-up metrics on phones, Eggy as logo, tab icon, brand block and empty-state mascot (the supplied artwork unmodified); desktop and 375 px reviewed on the hosted app. First-deep-link-after-restart quirk handled cosmetically; opening the root address always gives the full navigation. |
| Browsing, filtering or refreshing never records a bet | **VERIFIED IN TESTS** | No order and no ledger write without a button press. |

## D. Personal accounts and the Paper Parlay Builder

| Requirement | Status | Evidence / limit |
|---|---|---|
| Simple last-name accounts; duplicate surname handled clearly ("Burnett 2"); no long key in everyday use; write protection | **VERIFIED IN TESTS** (creation on the hosted app: **EXTERNAL BLOCKER**) | 8-character passcode, Ed25519 signatures, `NAME_TAKEN` / `BAD_SIGNATURE` refusals (`tests/test_personal_accounts.py`, 42 tests). The hosted My Bets opens an account by last name and says creating "is not switched on yet" because `LOG_WRITE_TOKEN` is missing. |
| A separate $500 bankroll per account: cash, open stakes, payouts, settled P&L; funds enforced; never reset | **VERIFIED IN TESTS** | Cash recomputed from the account's own bets; funds checked in the insert's transaction; second derivation re-checked by the watchdog. |
| Paper Parlay Builder: player → market → line → slip → browse others → edit/remove → stake → explicit submit; real prices, freshness, estimated combined odds, return, destination; no model edge or +100 required; same-game slips labelled multiplied, not a sportsbook quote | **VERIFIED** (up to submit) | Hosted, signed out, 2026-10-10 10:20 ET: a 2.3-hour-old morning quote shown fresh, Anthony Mantha 1+ shots added at −600 ($10 → $11.67); the destination said to open an account first and Submit stayed blocked. Stale quotes disabled Add (seen live 2026-10-09). 17 page tests. |
| Create → build → submit → persist → settle for two separate accounts, model book unchanged | **VERIFIED IN TESTS** (real pages + real engine, transport simulated) / hosted run **EXTERNAL BLOCKER** | `tests/test_two_account_workflow.py`: two people create accounts on My Bets, each builds and submits a slip, the engine records it, a fresh session sees it, the game settles into the right account, overdrafts and cross-account orders are refused, and a hash of every model-ledger table is identical throughout. The hosted run needs `LOG_WRITE_TOKEN`; the runbook is `docs/HOSTED_ACCOUNT_TEST.md`. |
| Personal bets never affect the model's $500 book | **VERIFIED** | Separate database; the model queries count `origin = 'AUTOMATIC'`; `deploy/qa_two_logs.py` on a copy of the live ledger and the integration test above: ledger hash and account identical before and after; the watchdog checks no bet id is in both books. |
| Migration of earlier logs; the earlier manual ticket | **VERIFIED** | `migrate_bankrolls` idempotent (the live database holds only the unclaimed bucket); `ME3D7C508EBFDD3` left model accounting earlier, row untouched; claim with the single-use phrase (`docs/PERSONAL_LOGS.md`). |

## E. Settlement and ledger integrity

| Item | Status | Evidence |
|---|---|---|
| Official results with documented rules; unresolved stays visible; reconciliation three ways | **VERIFIED** | `docs/SETTLEMENT_AUDIT.md`, `deploy/audit_settlement.py`; independent box-score check of all 12 settled model bets (`docs/validation/postmortem_2026-10-10.json`). |
| Ontario void and parlay-reduction rules | **EXTERNAL BLOCKER** | `VOID_RULES_VERIFIED=False`; a leg whose player does not play leaves the bet open (UNRESOLVED) rather than guessing. Prices are US-feed DraftKings quotes, not verified against DraftKings Ontario. The +3% margin does not verify either. |
| Historical records and bankroll unchanged | **VERIFIED** | No row rewritten, no balance reset; ledger extract committed for audit. |
| Model and personal bookkeeping separate | **VERIFIED** | See D. |

## F. Operations

| Item | Status | Evidence |
|---|---|---|
| Scheduled jobs run from the clean pinned release, refresh, settle, publish, report failures | **VERIFIED** | 16 launchd jobs (15 plus the watchdog) from `~/nhl_engine_release`; the 30-minute watchdog checks jobs loaded, release == origin/master, trader and publication recency, publishing enabled, quote freshness, source freshness, the morning update, the recording pause, settlement backlog and reconciliation (model book, parlay/single split, every personal account). Operational OK does not mean a blocked feature works: Diagnostics' product-readiness table says so. |
| Persistent monitoring | **VERIFIED** | Launchd jobs continue after any session; session-only reminders are not monitoring. |
| Credential rotation | **EXTERNAL BLOCKER** | The exposed Odds API key is not resolved until the owner rotates it (`docs/CREDENTIAL_ROTATION.md`, then `python3 deploy/verify_odds_key.py`). No credential is printed anywhere. |
| Model health, coverage and budget shown honestly | **VERIFIED** | Model Health separates freshness, support, validation, calibration and betting-value evidence ("none yet" on purpose). |

## Owner actions that remain (only these)

0. **Review and approve (or change) the restart policy**: `docs/POSTMORTEM_2026-10-10.md` section 13, then `python3 -m operational.ticket_policy show`, `approve <digest>`, `python3 -m operational.recording_pause resume`.
1. **`LOG_WRITE_TOKEN`** Streamlit secret (`docs/PERSONAL_LOGS.md`, "Owner setup"): enables account creation, the Parlay Builder submit and one-click adds for friends. I then complete the hosted two-account test with you (account creation and passcodes are yours to type).
2. **Rotate the Odds API key** (`docs/CREDENTIAL_ROTATION.md`).
3. **Budget decision**: stay on the free plan (about 3 of 14 games priced), or buy the 20K tier ($30 a month, not bought) so every game is priced.
4. **Open the app in Safari Private on a phone** and confirm Today loads without a sign-in.
5. **Source permissions** if lineups, power-play units, injuries or starters should be automatic.
6. **Puck line**: authorise one credit (`python3 deploy/capture_puck_line_contract.py --confirm-spend-1-credit`) if you want the contract certified; the model stays out of selection regardless.
7. Optional: `OWNER_ACCESS_PASSPHRASE` so you can reach Diagnostics on the public app.
