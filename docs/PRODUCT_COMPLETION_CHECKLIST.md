# Product completion checklist — one complete list (revision 20, 2026-10-14)

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
ticket policy and the 8:00 AM run; the read-only Parlay Builder lists real DraftKings lines with quote ages and builds a slip with a hypothetical stake; the model book ($435.76) agrees three ways and with the official box scores; collection, settlement, publishing and shadow logging run
whether automatic recording is on, paused or finds no ticket.

**Partial:** several price refreshes a day for every game (credit-limited: 3 of 14 games priced today); the daytime board (moneyline prices go stale between pulls); player and goalie data (one day behind until the new
13:30 / 17:30 source re-checks have run for a day); Eggy interface (polished, a first-deep-link quirk is now handled cosmetically).

**External blockers (owner or third party):** Odds API key rotation; Ontario price/void-rule verification; a permitted source for
lineups, power-play units, injuries and starting goalies; the odds budget decision (a plan that prices every game); one paid credit to certify the puck-line contract; the real-phone Safari Private check.

**Repaired 2026-10-12 (this revision):**
- *Selector.* The per-game pruning that hid qualifying tickets (the Batherson + Eichel case) is gone; one exact search (`anchor.qualifying`) now feeds Today's provisional tickets, Best Options, and automatic recording, and tests prove the three agree (`tests/test_anchor_tickets.py`, `TestTheSearchHidesNothing`, `TestTheRealPoolIsRecordedThroughTheNormalCycle`). The morning tickets were not back-filled (their quotes had expired); only fresh quotes are recorded.
- *Prediction scoring (rev 2).* Moneylines are graded as team outcomes, not player participation; affected shadow reports and daily reviews were regenerated from the frozen evidence (`docs/validation/CORRECTIONS_2026-10-12.md`; originals kept).
- *Logging gap.* A nine-hour gap in the prediction log is identified as a gap on the Morning Review page and in the scorer; no history was invented to fill it.
- *Hosted.* Footer states the app's code version and the data's build version; Players tops up from the NHL API when MoneyPuck lags; the builder shows four separate numbers (recent record, model, final estimate, price-implied).

**First live wave, settled (2026-10-12/13):** two $10 tickets recorded once at 21:18:55Z after revalidation (not re-recorded the next cycle); both settled automatically at 03:11Z; the independent audit (`docs/validation/settlement_audit_2026-10-13.json`) agrees on all 16 bets and the recomputed payouts (win +$13.86, loss −$10.00); cash $439.62, open $0, settled P&L −$60.38; the hosted Today page shows the same numbers; frozen-prediction scoring resolves 550 legs with moneylines graded as team outcomes (`docs/validation/live_wave_2026-10-12.md`). Two tickets prove the machinery, not an edge.

**Shots-estimate correction (2026-10-13), awaiting your approval:** capped 2+ shots legs were over-stated by about 8 points in every validation season (n = 555 / 693 / 665; estimate 0.67 against 0.59 observed). The cap went from +0.10 to +0.02, chosen on 2024 only and evaluated on 2025 (log loss 0.59608 → 0.59327), no ticket outcome used; only that number changed. New policy `anchor-tickets-2026-10-13`, digest `9f5e3448e10ff62e`; the 2026-10-11 policy and its digest `c236c526e00b9f04` are preserved and the two tickets recorded under it are labelled "Earlier estimate rule" (entries untouched). **Automatic recording is paused at your request** (since 2026-10-13 15:35:57Z); it resumes only after you approve the new digest and lift the pause (`docs/PROPOSAL_ANCHOR_CAP.md`). Collection, settlement, publishing, prediction logging and personal accounts continue.

**Slate Board and the single primary parlay, state at release 102f139 (2026-10-14), awaiting your review:** every scheduled game has four analyses (likely goal scorers with price, two shots values from the offered
ladder, goalie saves with starter status, moneyline or "No recommended side"), a per-game coverage block (analysed / priced / supported / recommended / missing and why), and ONE primary parlay under policy
`primary-slip-2026-10-14` (digest `1fad3ba03503cf94`, **not approved**): +100 to +500, fewest legs, ranked by estimated hit chance, every leg rule and value test of the cap-correction policy unchanged. Verified on the
public app: Slate Board, Today's primary-slip section, builder gap explanations, mobile. In: fail-closed policy loading, quote-age freshness (provider time, both moneyline sides, re-judged when a page opens), per-game
failure isolation, rejection reasons by cause, incremental previous-day scan, an exact and much faster qualifying search; three independent review rounds (20 findings, all fixed with tests); full suite 4,201 tests OK
(2 skipped). **Open and honest:** no qualifying primary slip has been produced under the corrected estimates (0 of 18 frozen snapshots; the board shows the closest non-qualifying combination); anytime-goal prices are
bought for the lead games from the 2026-10-14 pregame wave (first live check pending); a saves line needs a confirmed starter and no permitted automatic source confirms starters (archived real payloads parse and match
10 of 10 goalies); Recording stays paused. Handover: `docs/HANDOVER.md`.

## Versions (one commit everywhere)

| What | Commit | Evidence |
|---|---|---|
| `master` = pinned release the scheduled jobs run from = hosted app = published data = this ZIP | the commit named in the ZIP's file name (`nhl_engine_audit_<commit>.zip`) | The watchdog's `release_pinned` check compares the release with `origin/master` every 30 minutes; Diagnostics shows the hosted app's and the data's commit. |
| Tests on the final code | run recorded in the delivery message and in `audit_evidence/test_results.txt` | The full suite is run on the final code, with skips named (two: the puck-line real-payload certification and a missing local corpus file). |

## A. The automatic engine and the restart

| Requirement | Status | Evidence / limit |
|---|---|---|
| Up to five worthwhile parlays a day, +100 or better, a strong estimated hit chance, defensible value, empty slots allowed | **VERIFIED RUNNING** (policy `c236c526e00b9f04` approved by the owner 2026-10-11 15:29 UTC; recording resumed) | Anchor tickets (`docs/ANCHOR_TICKETS.md`). Cycle 2026-10-11 15:44 UTC recorded nothing on stale prices (not back-filled); 21:49 UTC: no qualifying ticket (best near-miss −1.3% value, refused). Balance verified from the ledger: $435.76, 12 tickets, nothing open. Tests: `tests/test_anchor_tickets.py`. |
| Show the ticket the policy kept, and whether the engine can find such tickets regularly | **DONE (honest answer: not reliably)** | `docs/POSTMORTEM_2026-10-10.md` section 12b: Coleman 2+ shots (−135) + Lee 2+ shots (−140), +198, estimated 38.7%, 1-SD range 31.6–45.8% against a break-even of 33.5%, +$1.55 expected on $10 (+$0.47 after the haircut). About one such ticket in three days from 3 to 6 games priced a day; coverage is the binding limit; three days cannot say how often a full slate would. |
| Prediction logging, collection, settlement, publishing continue whether recording is on, paused or finds no ticket | **VERIFIED IN TESTS + LIVE** | `operational/recording_pause.py` (paused 2026-10-10 to 2026-10-11, resumed on the owner's approval of the policy digest). The shadow log is written every cycle before the pause is consulted; tests cover paused, resumed, no-ticket and no-legs cycles (`TestTheRealPoolIsRecordedThroughTheNormalCycle`, `tests/test_shadow_scoring.py`); the watchdog's `prediction_log` check alerts if it stops. |
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
| Captures that can be compared at one evaluation time | **VERIFIED IN TESTS** (first live wave day pending) | Pregame captures are bought a whole wave at a time, biggest wave first, and a wave is captured together when its first game reaches its window (`credit_planner.connected_order`, `best_bets.capture_prices`); on 2026-10-11 the old one-game-per-wave order left the two priced games never fresh together. |
| Moneylines usable all day | **VERIFIED IN TESTS / LIVE** | A moneyline quote is a ticket leg while fresh by the shots limit (150/100 min) from any pull; stale ones are provisional, never recorded. Daytime refreshes are limited by the display-refresh allowance (budget). |
| Frozen predictions, scored without rewriting | **VERIFIED IN TESTS** (first scored day pending) | Every cycle freezes each priced leg with recent hit rate, model, market and final estimate, decision and reason, versions; watchdog check `prediction_log`; scorer and daily review (`anchor_estimates`). The log had stopped when recording resumed (found and fixed 2026-10-12). |
| Odds budget and every paid job audited; nothing purchased | **VERIFIED / EXTERNAL BLOCKER (decision)** | Costs confirmed against 1,331 archived provider calls (counter 218 = headers 218); `docs/ODDS_BUDGET_CONFIGURATIONS.md`, `docs/ODDS_CREDIT_AUDIT.md`. The budget decision is the owner's. |

## C. Pages, access and interface

| Requirement | Status | Evidence / limit |
|---|---|---|
| Best +100 option per player and goalie (the same estimates and tests as the tickets; none made up) | **VERIFIED IN TESTS** / populated state **PENDING** | `player_options` (anchor mode): fewest legs, ranked by estimated hit chance; a player with only points/goals/saves prices, and every goalie, gets the reason there is no option. Populated cards depend on a qualifying day. |
| Public access on a phone or computer without a Streamlit sign-in | **VERIFIED (signed out, phone-sized view) / EXTERNAL BLOCKER (real phone)** | Hosted app "public and searchable"; opens signed out in the built-in browser at 375 px and desktop, and by anonymous request. **Real-phone Safari Private was not tested** (no simulator available): please open the app link in a Safari Private tab and confirm Today loads without a sign-in. |
| Public visitors cannot reach model-account writes or admin actions | **VERIFIED** | Visitors have the USER role; admin pages need the owner passphrase secret (not configured); evidence writes need an allow-listed login plus a token. A deep link opened first after a restart runs the page script alone; the page now applies the theme and hides the raw page list itself (`ui.standalone_page_fallback`). |
| Eggy interface, polished | **VERIFIED / PARTIAL** | Layered slate theme, bet-slip cards, two-up metrics on phones, Eggy as logo, tab icon, brand block and empty-state mascot (the supplied artwork unmodified); desktop and 375 px reviewed on the hosted app. First-deep-link-after-restart quirk handled cosmetically; opening the root address always gives the full navigation. |
| Browsing, filtering or refreshing never records a bet | **VERIFIED IN TESTS** | No order and no ledger write without a button press. |

## D. Read-only Parlay Builder (individual bets and personal accounts were removed 2026-10-14)

| Requirement | Status | Evidence / limit |
|---|---|---|
| No last-name accounts, passcodes, personal bankrolls, bet submission, personal-order job or `LOG_WRITE_TOKEN` | **VERIFIED IN TESTS** | Pages, navigation entry, order builders, signing, personal database module and the `manual-order-job` launchd job are gone; `tests/test_parlay_builder_page.py::TestItIsReadOnly` scans the page source and the rendered words. The installed job was unloaded and its plist moved to `~/Library/LaunchAgents.disabled/`. |
| Parlay Builder as a read-only tool: player → market → line → add to slip → browse → edit/remove → hypothetical stake → odds and return | **VERIFIED IN TESTS** (hosted check after the release reboot) | `tests/test_parlay_builder_page.py`. Real prices with quote ages; cross-game price is a calculated estimate; same-game slips stay an UNPRICED DRAFT with no invented payout; nothing is recorded, submitted or debited. |
| The $500 model book is the only book | **VERIFIED** | Ledger rows with `origin = 'AUTOMATIC'` only are counted; the watchdog re-derives the account from raw rows (`check_reconciliation`, model only); `deploy/export_audit_zip.py` writes `model_book_reconciliation.json`. |
| Earlier personal records preserved, inactive, separate | **VERIFIED** | `deploy/archive_personal_logs.py` copied `operational/runtime/personal_logs.db` read-only to `operational/archive/` (1 log, 1 bet, 1 migration row) and nothing reads it; the model ledger's earlier manual ticket stays in the ledger outside the model book, untouched. |

## D2. Eggy's Parlay Pick of the Day and the one-ticket rule (2026-10-14)

| Requirement | Status | Evidence / limit |
|---|---|---|
| The single primary ticket is branded "Eggy's Parlay Pick of the Day": Eggy artwork, legs with prices, combined price, estimated hit chance with range, $10 stake, potential return, a short explanation; the same ticket, not an extra bet | **VERIFIED IN TESTS** (hosted check after the release reboot) | `ui.eggy_pick_card` on Slate Board and Today; `tests/test_slate_board.py` (pick state, no-pick state, banned words absent), `tests/test_product_pages.py`. Desktop and 375 px layouts checked in a browser on a fixture. |
| No qualifying ticket: "No Eggy pick today" with the exact reason; no invented pick; strongest singles and the closest non-qualifying combination stay visible | **VERIFIED IN TESTS** | Same tests; the live board is in this state until a combination qualifies (0 of 18 logged snapshots qualified under the corrected estimates). |
| Never "guaranteed", "lock" or "slam dunk"; estimates stated as estimates with a range | **VERIFIED IN TESTS** | Asserted on the card text and on the generated explanation. |
| At most one primary ticket per Eastern day at exactly $10, enforced in the recording path (not only on the page) | **VERIFIED IN TESTS** | `daily_tickets.record_tickets`: DAILY_LIMIT, REVALIDATION_FAILED, BLOCKED_POLICY, STAKE_NOT_PERMITTED; the ledger keeps funds, duplicates and immutability. `tests/test_anchor_tickets.py::TestThePrimarySlipThroughTheNormalCycle`. |
| Anytime-goal legs are candidates compared like other legs | **PARTIAL (by design)** | Logged with a decision code (`BELOW_STRONG_LEG_FLOOR` / `GOALS_NO_CAPPED_ESTIMATE`) and never promoted without a capped estimate: no cap study exists for the goals model and the strong-leg floor (60%) is not loosened. Goals appear in the per-game analysis with price and value. Adding them to the ticket search needs a validated capped estimate and a new policy digest for your approval. |
| Automatic recording | **PAUSED, awaiting owner approval** | Digest `1fad3ba03503cf94` unchanged and unapproved. |

## E. Settlement and ledger integrity

| Item | Status | Evidence |
|---|---|---|
| Official results with documented rules; unresolved stays visible; reconciliation three ways | **VERIFIED** | `docs/SETTLEMENT_AUDIT.md`, `deploy/audit_settlement.py`; independent box-score check of all 12 settled model bets (`docs/validation/postmortem_2026-10-10.json`). |
| Ontario void and parlay-reduction rules | **EXTERNAL BLOCKER** | `VOID_RULES_VERIFIED=False`; a leg whose player does not play leaves the bet open (UNRESOLVED) rather than guessing. Prices are US-feed DraftKings quotes, not verified against DraftKings Ontario. The +3% margin does not verify either. |
| Historical records and bankroll unchanged | **VERIFIED** | No row rewritten, no balance reset; ledger extract committed for audit. |
| Only the model book is accounted | **VERIFIED** | See D. |

## F. Operations

| Item | Status | Evidence |
|---|---|---|
| Scheduled jobs run from the clean pinned release, refresh, settle, publish, report failures | **VERIFIED** | 15 launchd jobs (14 plus the watchdog; the personal-order job was removed 2026-10-14) from `~/nhl_engine_release`; the 30-minute watchdog checks jobs loaded, release == origin/master, trader and publication recency, publishing enabled, quote freshness, source freshness, the morning update, the recording pause, settlement backlog and reconciliation (model book, parlay/single split). Operational OK does not mean a blocked feature works: Diagnostics' product-readiness table says so. |
| Persistent monitoring | **VERIFIED** | Launchd jobs continue after any session; session-only reminders are not monitoring. |
| Credential rotation | **EXTERNAL BLOCKER** | The exposed Odds API key is not resolved until the owner rotates it (`docs/CREDENTIAL_ROTATION.md`, then `python3 deploy/verify_odds_key.py`). No credential is printed anywhere. |
| Model health, coverage and budget shown honestly | **VERIFIED** | Model Health separates freshness, support, validation, calibration and betting-value evidence ("none yet" on purpose). |

## Owner actions that remain (only these)

0. **Review the corrected live slip and approve (or not) policy digest `1fad3ba03503cf94`; recording stays paused until you do.**
1. (Removed: no personal-account credential is needed.)
2. **Rotate the Odds API key** (`docs/CREDENTIAL_ROTATION.md`).
3. **Budget decision**: stay on the free plan (about 3 of 14 games priced), or buy the 20K tier ($30 a month, not bought) so every game is priced.
4. **Open the app in Safari Private on a phone** and confirm Today loads without a sign-in.
5. **Source permissions** if lineups, power-play units, injuries or starters should be automatic.
6. **Puck line**: authorise one credit (`python3 deploy/capture_puck_line_contract.py --confirm-spend-1-credit`) if you want the contract certified; the model stays out of selection regardless.
7. Optional: `OWNER_ACCESS_PASSPHRASE` so you can reach Diagnostics on the public app.
