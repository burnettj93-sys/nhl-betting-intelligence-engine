# Product completion checklist (revision 5, 2026-10-09)

Status words, used strictly: **WORKS** (built, tested, and checked on the hosted app — evidence named) · **PARTIAL** (works, but a stated part does not) · **BROKEN** (does not work; none open at delivery) ·
**BLOCKED** (needs an action only the owner or a third party can take; the exact action is stated) · **PENDING** (needs a live event that has not happened yet). Nothing here claims a betting edge.
Earlier "finished" claims were not used as evidence: every row below was re-checked in this audit, on the hosted app where it is a hosted feature.

## Versions (one commit everywhere)

| What | Commit | Evidence |
|---|---|---|
| Pre-change audit ZIP (before any work in this round) | `475bcbf0bc` | `nhl_engine_audit_PRE_475bcbf0bc.zip`, exported 2026-10-09T02:13Z |
| Repository `master` = pinned release checkout the scheduled jobs run from = hosted app = published data | see the delivery message (`FINAL_COMMIT`) | Diagnostics → "Versions and the unattended engine" shows the hosted app's commit and the data's commit; the watchdog's `release_pinned` check compares release to `origin/master` every 30 minutes |

## 1. Core engine

| Requirement | Status | Evidence / limit |
|---|---|---|
| Up to five distinct cross-game tickets a day, combined ≥ +100, defensible value | WORKS | Rules in `research/real_market_parlay/engine.py` (EV ≥ 5% after a 3-point probability haircut; one leg on at most 2 tickets, one game on at most 3); 2026-10-07: 3 tickets, 2026-10-08: 5. Fewer than five is explained on Today ("N slots empty — …"); standards were not lowered to fill slots. |
| Real DraftKings odds, refreshed several times a day | **PARTIAL (BLOCKED on a budget decision)** | Moneyline decision pull per start cluster + one display refresh; player props one capture per priced game ≈100 min before puck drop. The free 500-credit plan prices 78 of 163 remaining games (`docs/ODDS_BUDGET_CONFIGURATIONS.md`); more coverage/refreshes need the provider's $30/month tier — **not purchased**. |
| Shots (alternate) | WORKS | 14 ticket legs so far, 8 won / 6 lost (mean model chance 61% → ~8.6 expected); full chain price → identity → probability → ticket → settlement → page verified (`tests/`, hosted Ticket History). |
| Points | WORKS (tiny sample) | 2 legs, both lost (expected ≈0.9 wins). Model validated; the live sample says nothing. |
| Anytime goals | PARTIAL | Priced for the games the credit plan covers; model validated (`goals>=1`); no ticket has used it yet. |
| Goalie saves | **PARTIAL (BLOCKED: starter source)** | Model validated (16/16 markets beat baselines) and displayed; **no saves leg can be priced until a start is confirmed**, and no permitted automatic source exists (see 4). No saves ticket has ever existed. |
| Moneyline | PARTIAL | All games priced by the T-35 pull; Elo path prices tickets; strength model **not promoted** (evidence gate: 150 finished game-sides). No moneyline ticket yet (legs go stale or games have started when evaluated). |
| Spread / puck line | **BLOCKED** | Settlement, shape validator and leg builder are done and tested; the provider contract needs one real payload (1 credit — the capture was rejected by the approval layer, not bypassed) and the model is unvalidated and **kept out of selection**. |
| $10 per automatic ticket vs the $500 model book; open bets, settlement, balance, P&L, postmortems tracked | WORKS | Model book $465.76 = $500 − $90 staked + $55.76 returned; 9 settled tickets, 2W–7L, −$34.24, no open tickets (hosted Today / Paper Performance; independent re-derivation in `audit_evidence/model_vs_personal_reconciliation.json`). |

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

## 6. Personal bet logs by code

| Item | Status | Evidence |
|---|---|---|
| My Bets / Model Bets / Both, clearly separated; labelled "Manually added" | WORKS | Hosted My Bets (desktop and 390 px mobile). |
| **Another person cannot write to your log** | WORKS in code and tests; hosted one-click path BLOCKED (below) | Each log has a separate **write key** (100 bits, shown once). Only the public half is stored; every order is **Ed25519-signed** (pure-Python, no new dependency), the engine refuses anything else (`BAD_SIGNATURE`). Knowing a code gives read-only access. Tested: wrong key, unsigned, altered stake, redirected order, creation-id takeover, replay (`tests/test_personal_logs.py::TestWriteProtection`, `tests/test_log_signing.py` against the RFC 8032 vectors and the `cryptography` library). **Public-data trade-off, stated on the page and in `docs/PERSONAL_LOGS.md`:** reading is not private (public repository, by design); writing is. A lost key cannot be recovered. |
| Personal bets never affect the $500 model book | WORKS | Separate database file; the model's queries count `origin = 'AUTOMATIC'` only; `deploy/qa_two_logs.py` on a copy of the live ledger: two logs × two bets, settled WIN/LOSS — hash of every ledger table identical before/after, account identical, each log reconciles (`audit_evidence/isolated_two_log_qa.json`). |
| Create/open by code, collision refusal, durable, no duplicate submissions | WORKS | Hash-only storage, `CODE_IN_USE` / `LOG_NOT_FOUND` refusals, order-id idempotence, same-bet-same-day refusal, rate limits (`tests/test_personal_logs.py`, 30 tests). The page and `docs/PERSONAL_LOGS.md` say a code is **not authentication** and the published data is public. |
| Destination obvious before adding | WORKS | "Adding to your personal log *X* — separate from the model book" above every add button. |
| Earlier manual ticket | WORKS | `ME3D7C508EBFDD3` (lost, −$10) left the model accounting (model P&L −$44.24 → −$34.24, cash $455.76 → $465.76, tickets 10 → 9); the ledger row is untouched (hash of the table identical), a copy sits in "unclaimed earlier manual tickets" with migration and audit rows; ledger backed up first. The owner claims it with `python3 -m operational.personal_logs claim-legacy`. |
| **The hosted add button end to end** | **BLOCKED** | Needs the app's write credential. Without it the button only builds a pre-filled GitHub issue that the repository owner alone can submit — which is not an end-to-end test and is not offered to friends. Exact action: `docs/PERSONAL_LOGS.md` → create a fine-grained token (Issues: read/write on this one repository) and add `LOG_WRITE_TOKEN` to the app's Streamlit secrets. I will run the hosted add + settle test with isolated test data as soon as it exists. |

## 7. Paper book, settlement, postmortems

| Item | Status |
|---|---|
| Ticket ids / frozen entry / provenance preserved; exact $10; atomic funds; no duplicate on refresh/restart; invalid stakes rejected without writes; alerts never refund | WORKS (existing test suites, unchanged and green) |
| Official results with documented rules; unresolved stays visible; cash/open/payouts/equity/P&L reconcile independently | WORKS (`audit_evidence/model_vs_personal_reconciliation.json`: cash by P&L = cash by stake/return flows) |
| Model and personal bookkeeping separate | WORKS (section 6) |
| Postmortems from stored predictions + actual settlement, variance vs defect | WORKS (`audit_evidence/postmortems/`) |

## 8. Model health, coverage, operations

| Item | Status | Evidence |
|---|---|---|
| Freshness, market support, predictive validation, calibration and betting-value evidence kept apart | WORKS | Model Health → "What the evidence does and does not say". The betting-value row says "none yet" on purpose. |
| Odds budget and every paid job audited; trade-offs shown; no claim of full coverage | WORKS | `docs/ODDS_BUDGET_CONFIGURATIONS.md`, `docs/ODDS_CREDIT_AUDIT.md`, Diagnostics credit plan. Nothing purchased. |
| Scheduled jobs run from the clean release, refresh, settle, publish compatible snapshots, report failures | WORKS | 13 launchd jobs from `~/nhl_engine_release`; the **watchdog** (every 30 min) checks, as **operational health**: jobs loaded, release == origin/master, trader and publication recency, database path, **model/personal reconciliation** (the model book re-derived three ways; every personal log vs its rows; no bet in both books; migrated ticket == original), **settlement backlog** (open > 6 h after puck drop warns, > 12 h fails) and **source freshness**. **Product readiness** is a separate table on Diagnostics (working / limited / not verified / blocked / owner action) and says in words that an operational OK does not mean a blocked feature works. Re-aged on the page; FAIL by itself if the watchdog stops. |
| Credential rotation | **BLOCKED** | The exposed Odds API key is not resolved until the owner rotates it (`docs/CREDENTIAL_ROTATION.md`, then `python3 deploy/verify_odds_key.py`). No credential is printed anywhere. |

## 9. Visual upgrade and Eggy

| Item | Status | Evidence |
|---|---|---|
| Premium polish pass: Inter type scale with brighter secondary text, bet-slip cards (selection, price pill, model chance and edge vs price, stake, return, result), tighter spacing, two-up metrics on phones, consistent hover/focus/disabled states, Eggy as the title mark of every page, a larger brand block and larger empty-state mascot | WORKS | Hosted desktop screenshots of Today (populated settled slips), Best Options layout is verified on fixtures only until tonight's prices (PENDING), My Bets, Players, Diagnostics; phone layout checked at 390 px width (see delivery notes for how). |
| Layered slate theme (not black), consistent cards/metrics/tables/buttons/badges, clear active navigation | WORKS | Hosted screenshots (desktop, 390 px mobile); no horizontal overflow on any of the 14 pages; metric values scale instead of truncating (defect found and fixed in QA). |
| Eggy, the supplied artwork, unmodified | WORKS | `dashboard/assets/eggy/eggy_original.webp` is byte-identical to the supplied file; other files are plain resized copies (`README.md` there). Used as the sidebar logo, browser-tab icon, brand block and in empty states (Today empty slots, My Bets empty log). |

## 1a. Navigation
`/Today` and `/` both work (a hidden default page forwards the root to Today), verified on the hosted app. One platform quirk remains: if the very first request after a reboot is a deep link, Streamlit briefly shows its unthemed legacy page list; loading the root first (or any reload) shows the real navigation.

## 10. QA

Every page was opened on the hosted app by direct link at 390 px width: no exception, no horizontal overflow (Today, Games, Game Detail, Best Options, My Bets, Players, Goalies, Team Intelligence, Model Health, Paper Performance, Ticket History, Morning Review, Data Status, Diagnostics); desktop views of Today, My Bets (open/create, My/Model/Both), Players (Hyry), Goalies, Data Status, Paper Performance and Diagnostics were inspected. Not yet possible: populated option cards and the real add flow (PENDING / BLOCKED above). Tests: see the delivery message.

## Owner actions that remain (only these)

1. **`LOG_WRITE_TOKEN`** Streamlit secret (section 6) — enables one-click adding for friends. Also invite friends to the private app.
2. **Rotate the Odds API key** (`docs/CREDENTIAL_ROTATION.md`).
3. **Budget decision**: stay on A (78/163 games), switch to A2 (`NHL_ENGINE_PROP_MARKETS=player_points`, 132/163, no shots), or buy the 20K tier ($30/month, not bought).
4. **Source permissions** if lineups/starters should be automatic (Nation Network or another permitted feed — see the audit); until then confirmations are manual.
5. **Puck line**: authorise one credit (`python3 deploy/capture_puck_line_contract.py --confirm-spend-1-credit`) if you want the contract certified; the model stays out of selection regardless.
