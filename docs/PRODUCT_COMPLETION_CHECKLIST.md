# Product completion checklist (revision 11, 2026-10-10)

Status words, used strictly: **WORKS** (built, tested, and checked on the hosted app — evidence named) · **PARTIAL** (works, but a stated part does not) · **BROKEN** (does not work; none open at delivery) ·
**BLOCKED** (needs an action only the owner or a third party can take; the exact action is stated) · **PENDING** (needs a live event that has not happened yet). Nothing here claims a betting edge.
Earlier "finished" claims were not used as evidence: every row below was re-checked in this audit, on the hosted app where it is a hosted feature.

## Versions (one commit everywhere)

| What | Commit | Evidence |
|---|---|---|
| Pre-change audit ZIP (before any work in this round) | `475bcbf0bc` | `nhl_engine_audit_PRE_475bcbf0bc.zip`, exported 2026-10-09T02:13Z |
| Repository `master` = pinned release checkout the scheduled jobs run from = hosted app = published data | see the delivery message (`FINAL_COMMIT`) | Diagnostics → "Versions and the unattended engine" shows the hosted app's commit and the data's commit; the watchdog's `release_pinned` check compares release to `origin/master` every 30 minutes |

## 0. Morning workflow (owner requirement 2026-10-09) — `docs/MORNING_WORKFLOW.md`

| Requirement | Status | Evidence / limit |
|---|---|---|
| Initial update about 8 AM ET: games, statistics, available DraftKings prices, provisional recommendations | **UNMET until observed** (built, tested, deployed; not yet seen on a scheduled morning or on the hosted app) | `com.nhlengine.morning-update` (08:00) → `operational/morning_update.py`; watchdog `morning_update` check (warn 08:45, fail 10:00) and a persistent per-day record; product readiness says NOT VERIFIED until a day shows a first look within 45 min of 08:00 |
| Remove the late capture window where it is only our policy; check provider availability | Done in code (slots replace the five-hour/105-minute horizon); availability **checked**: shots posted on game-day morning (observed 08:15 ET), points and goals by midday (not yet observed at 8 AM), saves late, **nothing a day ahead** (live probe 23.6 h ahead: nothing, 0 credits) | `operational/capture_schedule.py`; evidence table in the doc |
| Distinguish "not posted" / "not fetched" / "budget prevented" | Done | `operational/price_availability.py`: POSTED, NOT_POSTED, NOT_FETCHED, BUDGET_BLOCKED, FETCH_ERROR; shown on Today, Best Options, Tomorrow, Diagnostics |
| Refresh during the day; revalidate before recording | Morning + midday (leftover credits only) + pregame; **several refreshes for every game: NOT MET under the free allowance** | recording re-judges every leg on the clock at the moment of recording (fresh price, game not started); a fresh morning price is usable |
| Fresh, qualifying morning options can be added to a personal log (once the write credential exists) | Done in code and tests; hosted add **cannot be shown until `LOG_WRITE_TOKEN` exists** and a populated morning card exists | actual freshness decides; aged prices are provisional and not addable; revalidated again when the order is processed |
| Morning picks must not use up the five slots | Done by a cap of 2 early-price tickets a day (a blanket pregame-only rule was removed as too blunt), with the justification and its limits in the doc | `daily_tickets.EARLY_TICKET_CAP`; wave reservation unchanged |
| Frozen tickets and model/personal separation preserved | Unchanged; full suite | |
| Separate Tomorrow view with early prices, refreshed, yesterday's never fresh | Built (page `Tomorrow`); early prices STALE after 12 h or from an earlier Eastern day | availability is recorded per check (time, game, market, hours before puck drop, posted or not) twice a day; one probe is an observation, not a conclusion |
| Credits for morning coverage plus daytime and pregame refreshes; minimum budget; reduced service | **Cost model confirmed against actual charges (all 1,331 archived calls; counter 218 = headers 218). Minimum for every game: 60 credits/day (1,812/month); the smallest plan that meets it is 20K ($30/month), not purchased.** Reduced service under the existing allowance: morning look at 3 games, pregame price for 2 (any slate size) | `operational/service_plan.py`, Diagnostics, doc |
| Verified on the hosted app | **UNMET until the first scheduled morning** | listed as an owner-visible gap, not as done |

## 1. Core engine

| Requirement | Status | Evidence / limit |
|---|---|---|
| Up to five distinct cross-game tickets a day, combined ≥ +100, defensible value | WORKS | Rules in `research/real_market_parlay/engine.py` (EV ≥ 5% after a 3-point probability haircut; one leg on at most 2 tickets, one game on at most 3); 2026-10-07: 3 tickets, 2026-10-08: 5. Fewer than five is explained on Today ("N slots empty — …"); standards were not lowered to fill slots. |
| Real DraftKings odds, refreshed several times a day | **NOT MET on the free allowance (PARTIAL; BLOCKED on a budget decision)** — moneyline is pulled every 2 minutes only for display/decision, but player props get **one capture per priced game (≈100 min before puck drop), not several refreshes**, and only 3 of today's 4 games are priced (`audit_evidence/credit_plan_state.json`). This must not be read as meeting the multiple-refresh requirement. | Moneyline decision pull per start cluster + one display refresh; player props one capture per priced game ≈100 min before puck drop. The free 500-credit plan prices 78 of 163 remaining games (`docs/ODDS_BUDGET_CONFIGURATIONS.md`); more coverage/refreshes need the provider's $30/month tier — **not purchased**. |
| **Does the selector match the objective (+100 or better with a strong estimated hit chance, not just top EV)?** | WORKS — audited (`docs/SELECTOR_AUDIT.md`, `deploy/audit_selector.py`) | It already ranks qualifying tickets by **estimated hit chance** first. On 2026-10-08, 106 tickets qualified; the +458 (20.8%) and +483 (20.2%) tickets were rank 16 and 17 because every higher-hit alternative was blocked by the shared-exposure limits (Coleman 2+ and Lee 2+ were already on 2 tickets each), not outranked by payout; an exhaustive search for the best five-ticket set under the same limits returns the same set. The per-player limit added last round stops the third Coleman ticket. The two tail tickets remain because only two legs were strong that day; no floor was invented and no slot was forced. **The +0.2-point leg:** not meaningful (the model's individual-player error is ~8–10 points in this band; it also under-predicted this band by ~3 points), and the tested fix — every leg must survive the haircut alone — cut the best ticket from 38.7% to 28.5% and expected hits from 1.40 to 1.08 (longer odds, lower hit chance, the opposite of the objective), so the rule stays; legs with under one point of edge are now labelled "no edge of its own". Every recording cycle now stores and publishes the selection report (Today, Diagnostics). **Revision 7 answers** (`docs/SELECTOR_AUDIT.md` §4–5): the ~20% tail tickets are *permitted by the rules, not endorsed by the evidence*; every card now shows a plausible range (each leg ±8 points, the measured player-level error) beside the model chance; the 3-point haircut is described as a fixed policy margin, never calibration or proof of an edge; recording early is mitigated by wave slot reservation but a recorded ticket is never swapped for a better later one within a wave. |
| Shots (alternate) | WORKS | 14 ticket legs so far, 8 won / 6 lost (mean model chance 61% → ~8.6 expected); full chain price → identity → probability → ticket → settlement → page verified (`tests/`, hosted Ticket History). |
| Points | WORKS (tiny sample) | 2 legs, both lost (expected ≈0.9 wins). Model validated; the live sample says nothing. |
| Anytime goals | PARTIAL | Priced for the games the credit plan covers; model validated (`goals>=1`); no ticket has used it yet. |
| Goalie saves | **PARTIAL (BLOCKED: starter source)** | Model validated (16/16 markets beat baselines) and displayed; **no saves leg can be priced until a start is confirmed**, and no permitted automatic source exists (see 4). No saves ticket has ever existed. |
| Moneyline | PARTIAL | All games priced by the T-35 pull; Elo path prices tickets; strength model **not promoted** (evidence gate: 150 finished game-sides). No moneyline ticket yet (legs go stale or games have started when evaluated). |
| Spread / puck line | **BLOCKED** | Settlement, shape validator and leg builder are done and tested; the provider contract needs one real payload (1 credit — the capture was rejected by the approval layer, not bypassed) and the model is unvalidated and **kept out of selection**. |
| $10 per automatic ticket vs the $500 model book; open bets, settlement, balance, P&L, postmortems tracked | WORKS | Model book as of 2026-10-10: **$435.76** = $500 − $120 staked + $55.76 returned; 12 settled bets (11 parlay tickets + 1 moneyline single), **2W–10L**, −$64.24, nothing open; independently re-derived and checked against official box scores (`docs/POSTMORTEM_2026-10-10.md`). **Automatic recording is PAUSED by owner request (2026-10-10)** pending review of the postmortem's restart policy. |

## 2a. Stale prices on the board
Any displayed price is judged **when the page is opened** against the engine's own limit (180 min; 90 min within 4 h of puck drop): on Today, Games, Game Detail and option cards it is marked **STALE** with its age, a banner counts stale prices, a stale option card says so and **cannot be added**, and stale prices are never used for selection (tested: stale legs, including moneyline, never enter the pool). Verified on the hosted app: all 4 moneyline prices on Today show "STALE — quoted 9.5 h ago". Cause of the staleness: the moneyline refresh is limited by the odds-credit allowance (Data Status → budget-limited); the decision pulls happen about 35 minutes before each start cluster.

## 2. Current data, no demo content

| Requirement | Status | Evidence |
|---|---|---|
| Today / Game Detail / Players / Goalies / board default to the current ET day and season | WORKS | Hosted Today shows 2026-10-09 games; Players "season = 2026-27"; no demo/simulated wording (page-wide test). |
| Update times and "data through" shown; odds freshness from the provider's quote time | WORKS | Header line on every page; Data Status table (data through, last fetch, age now, policy, next refresh, reason). |
| A stale snapshot must not say "CURRENT"; Data Status and the cards agree | WORKS (defect found and fixed) | The top banner used a 13-hour rule and said CURRENT at 63 minutes while Data Status said STALE. It now says "SNAPSHOT PUBLISHED N MIN AGO" and turns to "NOT RECENTLY PUBLISHED" after 60 minutes — the same limit Data Status uses (`tests/test_cloud_live_data.py`). |
| Stale data traced to a cause | WORKS | Previous Data Status defect (daily readiness cache, wrong odds file) fixed earlier and re-verified; watchdog reports the publication age. |
| Known limit | PARTIAL | Player game logs come from MoneyPuck and trail by 1–2 days ("Player logs through 2026-10-06" on 10-09); stated on every header and on Data Status (≤ 36 h policy). |

## 3. Unfamiliar-player recommendations (Arttu Hyry, Mavrik Bourque)

| Finding | Detail |
|---|---|
| Identity / team / schedule | Both correct (regular-season game ids; Hyry DAL, Bourque NSH; no preseason/AHL/old-season mixing in the model history). |
| Hyry | **Defect found and fixed.** 28 NHL games, ~13–14 min, no power play, 0 points in 3 games this season; DraftKings offered +370 for 1+ point and the model said 27–29%. Walk-forward on 2025-26 shows the calibrated model **over-predicts below 40 prior games** (points 1+: 26–28% predicted vs 24% observed; shots 2+: 32–34% vs 28–30%) and is calibrated from 40 (27.5% vs 27.1%) — `docs/validation/low_sample_calibration.json`. The floor moved from 20 to 40 games; Hyry is no longer priced, and his hosted page says so ("Limited history … no recommendation is made"). The ticket that used him was a manual add of the option card. |
| Bourque | **Re-audited in full** (`docs/BOURQUE_AUDIT.md`): identity, role (inferred from only 3 games; no reported PP unit), quote (fresh, but taken 4 h 52 min before puck drop), probability (sane) and selection (the shots leg had **no edge of its own**, +0.2 pts). The three losses came from an **8:15-minute game** (his others: 17–20 min; no play-by-play event after 11:07 of the 2nd — most likely an in-game exit, cause unknown) — not ordinary variance, and the same player was on 3 of 5 tickets. Defects found and **fixed**: false "traded to DAL" alerts (stale archived corpus; now roster-first, 6 stored alerts retracted, never deleted) and no per-player ticket cap (now ≤ 2 tickets per player). Cards separate *inferred* PP usage from a *reported* PP unit. |
| Representative audit (`docs/RECOMMENDATION_AUDIT.md`, `deploy/audit_recommendations.py`) | 23 sampled players (named three, five under the floor, forwards by tier, defensemen): **all pass** identity/team (no false TEAM_CHANGED), the 40-game gate (limited, unpriced, absent from options), explanation labelling (inferred vs reported) and a model-vs-own-rate sanity check (largest gap 11 points). The audit has negative controls (`tests/test_audit_recommendations.py`). |
| Sample of other recommended players | Every other leg ever recommended has 62–331 prior games (Leonard 86, Kadri 325, Malkin 291, Landeskog 62, Coleman 314, Lee 331, Seguin 194, Dvorak 260). |
| Card rationale | Every option and ticket card has "Why this selection": role (labelled inferred), season and recent production, expected output, sample size and the main uncertainty (`operational/leg_context.py`). |
| Discarded analysis | A role-specific slice (low ice time, no power play) was dropped because it was defined by the game's actual ice time — a leak; it is not used as evidence. |

## 4. Player and goalie details

| Item | Status | Note |
|---|---|---|
| Players: ice time, PP time, shots/goals/assists/hits/blocks (season and recent), expected production, estimated tier | WORKS | Players page. |
| Reported line / PP unit | **BLOCKED (permission)** | Shown empty and labelled; the estimated tier is separate and labelled "inferred". Daily Faceoff stays disabled (Nation Network terms prohibit scrapers; the owner's acknowledgement does not grant permission). Permitted alternatives and their costs: `docs/STARTING_GOALIE_SOURCE_AUDIT.md`. |
| Goalies: W-L-OTL, SV%, GAA, SO, recent starts, start chance (estimate), expected saves/GA with ranges, team win % if he starts | WORKS | Goalies page. No team data substitutes for a goalie's own. |
| Starter confirmation with source and time | **BLOCKED** | Only a person-recorded confirmation or a team post is accepted; none is automatic. |

## 5. Best +100 option and redundant bets

| Item | Status | Evidence |
|---|---|---|
| Best supported +100 option on player and goalie pages (single, else a labelled estimated parlay; no invented combined prices) | WORKS in code; hosted populated state **PENDING** | Player and goalie pages show the option card or the exact reason there is none. Options need captured prices (about 2–5 PM ET for tonight's games), so the populated card could not be shown on the hosted app this morning. |
| Redundant recommendations reworked or hidden; technical rejections in Diagnostics | WORKS | Option de-duplication by legs; rejections in Diagnostics. |
| Browsing, filtering or refreshing never records a bet | WORKS | Tests: no order and no ledger write without a button press. |

## 6. Personal bet logs (earlier code-based logs; see 6a for accounts)

| Item | Status | Evidence |
|---|---|---|
| My Bets / Model Bets / Both, clearly separated; labelled "Manually added" | WORKS | Hosted My Bets (desktop and 390 px mobile). |
| **Another person cannot write to your log** | WORKS in code and tests; hosted one-click path BLOCKED (below) | Each log has a separate **write key** (100 bits, shown once). Only the public half is stored; every order is **Ed25519-signed** (pure-Python, no new dependency), the engine refuses anything else (`BAD_SIGNATURE`). Knowing a code gives read-only access. Tested: wrong key, unsigned, altered stake, redirected order, creation-id takeover, replay (`tests/test_personal_logs.py::TestWriteProtection`, `tests/test_log_signing.py` against the RFC 8032 vectors and the `cryptography` library). **Public-data trade-off, stated on the page and in `docs/PERSONAL_LOGS.md`:** reading is not private (public repository, by design); writing is. A lost key cannot be recovered. |
| Personal bets never affect the $500 model book | WORKS | Separate database file; the model's queries count `origin = 'AUTOMATIC'` only; `deploy/qa_two_logs.py` on a copy of the live ledger: two logs × two bets, settled WIN/LOSS — hash of every ledger table identical before/after, account identical, each log reconciles (`audit_evidence/isolated_two_log_qa.json`). |
| Create/open by code, collision refusal, durable, no duplicate submissions | WORKS | Hash-only storage, `CODE_IN_USE` / `LOG_NOT_FOUND` refusals, order-id idempotence, same-bet-same-day refusal, rate limits (`tests/test_personal_logs.py`, 30 tests). The page and `docs/PERSONAL_LOGS.md` say a code is **not authentication** and the published data is public. |
| Destination obvious before adding | WORKS | "Adding to your personal log *X* — separate from the model book" above every add button. |
| Earlier manual ticket | WORKS | `ME3D7C508EBFDD3` (lost, −$10) left the model accounting (model P&L −$44.24 → −$34.24, cash $455.76 → $465.76, tickets 10 → 9); the ledger row is untouched (hash of the table identical), a copy sits in "unclaimed earlier manual tickets" with migration and audit rows; ledger backed up first. The owner claims it with `python3 -m operational.personal_logs claim-legacy`. |
| **The hosted add button end to end (create → add → persist → settle, two logs, model book unchanged)** | **BLOCKED — `LOG_WRITE_TOKEN` not configured** (My Bets still says "no write credential") | Needs the app's write credential. Without it the button only builds a pre-filled GitHub issue that the repository owner alone can submit — which is not an end-to-end test and is not offered to friends. Exact action: `docs/PERSONAL_LOGS.md` → create a fine-grained token (Issues: read/write on this one repository) and add `LOG_WRITE_TOKEN` to the app's Streamlit secrets. Once it exists I run the hosted test with isolated test logs and prove it with `python3 deploy/verify_personal_workflow.py` (PENDING until two logs were created through the app's own write path and one bet settled; FAIL if a personal bet ever touches the model ledger or a book does not reconcile). The two-log separation and unchanged model accounting are already proven on a copy of the live ledger (`deploy/qa_two_logs.py`). |

## 6a. Last-name accounts, personal bankrolls and the Paper Parlay Builder (owner requirement 2026-10-09) — `docs/PERSONAL_LOGS.md`

| Requirement | Status | Evidence / limit |
|---|---|---|
| Last-name identifier; duplicate surname handled clearly ("Burnett 2"); no long key in everyday use; write protection kept | WORKS in code and tests; hosted My Bets checked signed out 2026-10-09 (opens with a last name and optional passcode, shows the account form, model book unchanged) | `personal_logs.surname_slug/parse_account_name/next_free_number`; 8-character passcode (40 bits) with a slow derivation, Ed25519 signatures; `NAME_TAKEN` and `BAD_SIGNATURE` refusals (`tests/test_personal_accounts.py`, 42 tests). Creating an account on the hosted app needs `LOG_WRITE_TOKEN`. |
| Own $500 per person: available cash, open stakes, payouts, settled P&L; funds enforced; never reset on reopening | WORKS in code and tests | Cash recomputed from the account's own bets (never stored); funds checked in the insert's transaction (`INSUFFICIENT_FUNDS`); reopen test; per-account second derivation `personal_logs.reconcile` run by the watchdog every 30 minutes. |
| Separate from the model's $500 book and from each other | WORKS in tests and on a copy of the live ledger | `deploy/qa_two_logs.py` (two accounts sharing a surname, builder + model-option bets, overdraft refused, forged cross-account order refused, settlement WIN/LOSS): model ledger hash and account identical before/after; each account reconciles. |
| Migrate existing logs without losing history or double counting | WORKS in tests; run on the live database at promotion | `migrate_bankrolls` is idempotent; the live database holds only the unclaimed earlier-ticket bucket (1 bet, no bankroll), so no live account is changed by it. |
| Paper Parlay Builder: player → market → line → slip → browse others → edit/remove → stake → explicit submit; actual prices, freshness, estimated combined odds, return, destination | WORKS in code and page tests (17). Hosted, signed out, 2026-10-09 18:55 ET (release `1d8487dfaf`): the page lists real DraftKings lines with quote ages, and the freshness gate was seen working on live data — every quote was 100 min old inside the two-hour pregame limit, so Add was disabled and labelled STALE. **A fresh hosted add-to-slip has not been demonstrated yet** (no fresh quote was on file that evening); it needs a fresh morning or pregame quote, and submitting needs `LOG_WRITE_TOKEN` | `dashboard/pages/40_Parlay_Builder.py`, `operational/builder_pool.py` (price list written each trader cycle, published as a snapshot section). Shots, points, anytime goal; saves only for a confirmed starter. |
| Builder bets are the person's own choices (no edge or +100); price, participation, funds, duplicate and settlement safeguards remain | WORKS in tests | Engine revalidates price (moved → `NEEDS_ACCEPTANCE`, nothing recorded), freshness from the provider quote time, game not started, identity, funds, same-slip duplicate; settlement by the shared resolver. A made-up market is refused, not a crash. |
| Multiplied same-game prices are not presented as a sportsbook quote | WORKS | Labelled "Multiplied price — NOT a DraftKings quote", needs an acknowledgement, stored as `SAME_GAME_MULTIPLIED_NOT_A_QUOTE`; different-game slips are "Estimated"; only a single leg is a "Quoted price". |
| **Hosted create → build → add → persist → settle, two isolated accounts** | **BLOCKED — `LOG_WRITE_TOKEN` not configured** | Same owner action as section 6. `deploy/verify_personal_workflow.py` stays PENDING until two last-name accounts were created through the hosted write path, each with a builder bet, one bet has settled, and the model ledger is untouched. |

## 7. Paper book, settlement, postmortems

| Item | Status |
|---|---|
| Ticket ids / frozen entry / provenance preserved; exact $10; atomic funds; no duplicate on refresh/restart; invalid stakes rejected without writes; alerts never refund | WORKS (existing test suites, unchanged and green) |
| Official results with documented rules; unresolved stays visible; cash/open/payouts/equity/P&L reconcile independently | WORKS (`audit_evidence/model_vs_personal_reconciliation.json`: cash by P&L = cash by stake/return flows) |
| Every supported market against official box scores (`docs/SETTLEMENT_AUDIT.md`, `deploy/audit_settlement.py`) | WORKS for the 11 recorded bets (all agree, no correction found). Pushes cannot occur for supported markets; DNP → UNRESOLVED; postponed → PENDING; corrections are reported, not applied. **Ontario void rules remain UNVERIFIED** (owner/third-party blocker). Goalie-saves, goals and puck-line settlement are proved by tests only (no live ticket). |
| Model and personal bookkeeping separate | WORKS (section 6) |
| Postmortems from stored predictions + actual settlement, variance vs defect | WORKS (`audit_evidence/postmortems/`) |

## 8. Model health, coverage, operations

| Item | Status | Evidence |
|---|---|---|
| Freshness, market support, predictive validation, calibration and betting-value evidence kept apart | WORKS | Model Health → "What the evidence does and does not say". The betting-value row says "none yet" on purpose. |
| Odds budget and every paid job audited; trade-offs shown; no claim of full coverage | WORKS | `docs/ODDS_BUDGET_CONFIGURATIONS.md`, `docs/ODDS_CREDIT_AUDIT.md`, Diagnostics credit plan. Nothing purchased. |
| Scheduled jobs run from the clean release, refresh, settle, publish compatible snapshots, report failures | WORKS | 14 launchd jobs (13 plus the watchdog) from `~/nhl_engine_release` (they continue after this session; the 30-minute watchdog is itself a launchd job and notifies when it fails — the session-only reminders used earlier are *not* monitoring); the **watchdog** checks, as **operational health**: jobs loaded, release == origin/master, trader and publication recency, **publishing enabled** (a demonstration that turns it off cannot be left off), **quote freshness** (provider quote time, not retrieval time), database path, **model/personal reconciliation** (the model book re-derived three ways; every personal log vs its rows; no bet in both books; migrated ticket == original), **settlement backlog** (open > 6 h after puck drop warns, > 12 h fails) and **source freshness**. **Product readiness** is a separate table on Diagnostics (working / limited / not verified / blocked / owner action) and says in words that an operational OK does not mean a blocked feature works. Re-aged on the page; FAIL by itself if the watchdog stops. |
| Market-by-market audit (works / partial / blocked) with data, contract, validation, calibration, betting-value and live evidence kept apart | WORKS | Model Health → per-market expanders (`operational/market_matrix.py`). Puck line: contract unpaid-for and model unvalidated — what remains is in `docs/PUCK_LINE_REQUIREMENTS.md` (one real payload plus the frozen model scored on 2026-27 games; one payload does not prove readiness). |
| Credential rotation | **BLOCKED** | The exposed Odds API key is not resolved until the owner rotates it (`docs/CREDENTIAL_ROTATION.md`, then `python3 deploy/verify_odds_key.py`). No credential is printed anywhere. |

## 9. Visual upgrade and Eggy

| Item | Status | Evidence |
|---|---|---|
| Premium polish pass: Inter type scale with brighter secondary text, bet-slip cards (selection, price pill, model chance and edge vs price, stake, return, result), tighter spacing, two-up metrics on phones, consistent hover/focus/disabled states, Eggy as the title mark of every page, a larger brand block and larger empty-state mascot | WORKS | Hosted desktop screenshots of Today (populated settled slips), Best Options layout is verified on fixtures only until tonight's prices (PENDING), My Bets, Players, Diagnostics; phone layout checked at 390 px width (see delivery notes for how). |
| Layered slate theme (not black), consistent cards/metrics/tables/buttons/badges, clear active navigation | WORKS | Hosted screenshots (desktop, 390 px mobile); no horizontal overflow on any of the 14 pages; metric values scale instead of truncating (defect found and fixed in QA). |
| Eggy, the supplied artwork, unmodified | WORKS | `dashboard/assets/eggy/eggy_original.webp` is byte-identical to the supplied file; other files are plain resized copies (`README.md` there). Used as the sidebar logo, browser-tab icon, brand block and in empty states (Today empty slots, My Bets empty log). |

## 1a. Navigation
`/Today` and `/` both work (a hidden default page forwards the root to Today), verified on the hosted app. One platform quirk remains: if the very first request after a reboot is a deep link, Streamlit briefly shows its unthemed legacy page list; loading the root first (or any reload) shows the real navigation.

## 5a. Populated option cards
**Verified on the hosted app, 2026-10-09 5:16 PM ET (desktop and 390 px)**: the first pregame captures produced 12 option cards on Best Options (quoted price, stake, return, model chance with its plausible range, value after the haircut, "why this selection", Ontario check) and 3 recorded automatic tickets on Today. **Not verifiable**: the add control (no write credential: a visitor is told to open a log first), a morning-priced card (no early price was bought on the release day), and the stale/provisional card states with real data. Those stay open.

## 12. Losing-streak postmortem, the pause, the objective and the singles (owner requests 2026-10-10) — `docs/POSTMORTEM_2026-10-10.md`

| Item | Status | Evidence / limit |
|---|---|---|
| Automatic recording paused; collection, publishing, settlement, personal accounts and shadow logging running | **WORKS (paused since 2026-10-10 08:23 ET)** | `operational/recording_pause.py` covers the ticket selector and every other automatic writer; fails safe; watchdog row. **Resume is refused until the owner approves the active ticket policy by digest.** |
| Which tickets lost; tickets vs legs; personal excluded | **DONE** | Nine consecutive losses = 8 parlay tickets + 1 moneyline single; parlay tickets 2W–9L −$54.24; single 0W–1L −$10; whole account 2W–10L −$64.24. |
| Independent settlement check | **DONE: no error** | 12 of 12 agree with the official box scores. |
| Streak assessed with shared exposure, dependencies stated, conditional label | **DONE** | 17.1% (shared legs exact) to at most 18.4% (same-player upper bound); conditional on the recorded probabilities; wording is "compatible with variance under the model's assumptions". Same-game/opposite-team dependence measured at about −0.01, teammates +0.10 (none share a ticket). |
| Causes: demonstrated vs hypothesis | **DONE** | Shared exposure and weak acceptance demonstrated; overstated probabilities a **hypothesis**. |
| Selector enforces the objective (strong estimated chance, meaningful value, up to five, empty slots allowed) | **DONE in code as a PROPOSAL, not live** | `TicketPolicy`; floor 25%, +3% after the 3-point haircut, one ticket per player, two per game; each number a judgement, none validated; digest `cb34b5563d244396`; unapproved. |
| Automatic moneyline and prop singles out of the parlay experiment; results shown separately; reconciled | **DONE** | `record_paper_bet` refuses automatic singles (observations still logged); `book_breakdown` + Today / Paper Performance + watchdog: parlay + singles = whole = cash. Nothing reset. |
| 620-leg and 150-game targets: assumptions, dependence, selected vs general pool | **DONE (documented)** | 620 = independent player-market-nights, realistically 690–1,230 for the edge-filtered legs; 150 is a model-vs-model floor, cannot validate moneyline bets. |
| Replay of the original decisions | **DONE** | Reproduces the recorded tickets exactly; entry information only; no result reported. |
| Forward evidence | **COLLECTING** | Shadow selection log + scorer (units, clustering, top-edge slice, model-vs-price blend); verdict "NOT ENOUGH YET". |
| Restart | **AWAITING OWNER APPROVAL** | `python3 -m operational.ticket_policy show` then `approve <digest>`, then `python3 -m operational.recording_pause resume`. |

## 10. QA

Every page was opened on the hosted app by direct link at 390 px width: no exception, no horizontal overflow (Today, Games, Game Detail, Best Options, My Bets, Players, Goalies, Team Intelligence, Model Health, Paper Performance, Ticket History, Morning Review, Data Status, Diagnostics); desktop views of Today, My Bets (open/create, My/Model/Both), Players (Hyry), Goalies, Data Status, Paper Performance and Diagnostics were inspected. Not yet possible: populated option cards and the real add flow (PENDING / BLOCKED above). Tests: see the delivery message.

## Owner actions that remain (only these)

0. **Review the restart policy** (`docs/POSTMORTEM_2026-10-10.md` section 13): approve it (`python3 -m operational.ticket_policy show`, then `approve <digest>`, then `python3 -m operational.recording_pause resume`) or tell me what to change. Recording stays paused until then.
1. **`LOG_WRITE_TOKEN`** Streamlit secret (section 6) — enables one-click adding, the Parlay Builder submit and account creation for friends (the app itself is public).
2. **Rotate the Odds API key** (`docs/CREDENTIAL_ROTATION.md`).
3. **Budget decision**: stay on A (78/163 games), switch to A2 (`NHL_ENGINE_PROP_MARKETS=player_points`, 132/163, no shots), or buy the 20K tier ($30/month, not bought).
4. **Source permissions** if lineups/starters should be automatic (Nation Network or another permitted feed — see the audit); until then confirmations are manual.
5. **Puck line**: authorise one credit (`python3 deploy/capture_puck_line_contract.py --confirm-spend-1-credit`) if you want the contract certified; the model stays out of selection regardless.
