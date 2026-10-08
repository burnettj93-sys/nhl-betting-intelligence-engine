# Product completion checklist (deployed revision)

Status words, used strictly:

* **VERIFIED** — built, tested, and checked on the hosted app (or, for a record of evidence, the evidence file is named).
* **INCOMPLETE** — does not work end to end yet; the exact missing piece is stated. "Built but waiting for an input" is never VERIFIED.
* **EXTERNAL BLOCKER** — needs an action only the owner or a third party can take; the exact action is stated.

Code on `master`; the scheduled jobs run the pinned release checkout at the same commit (see "Release" at the end). Hosted checks below were made on the private Community Cloud app after
a reboot on the final code. Suite on the deployed code: **3,680 tests, OK (1 skipped)**.

## 1. Remote manual-add flow

| Piece | Status | Evidence |
|---|---|---|
| Queue → revalidation → ledger → page feedback (link path) | VERIFIED | order `ME3D7C508EBFDD3` (2026-10-08): ledger 8 → 9 tickets, cash $475.76 → $465.76; shown on hosted Today and Paper Performance. This order was filed with `gh`, so it proves the queue, not the button. |
| Duplicate prevention, durable record, shared-account reconciliation | VERIFIED | `tests/test_manual_orders.py`; `audit_evidence/ledger_board_reconciliation.json` |
| **What identity the hosted authentication supplies** | VERIFIED (measured on the hosted app, Diagnostics → Order path) | Streamlit 1.62.0, app private ("Only specific people can view"). `st.user.is_logged_in` = None and `st.user` has **no fields — no email**. The platform's request headers include `X-Streamlit-User`: an **opaque 76-character value** (not an email, not a signed token with claims). The viewer's one-way fingerprint is **`748a81cd1777`** and was identical across separate sessions and two reboots. Consequence: `ORDER_ALLOWED_EMAILS` can never match; the allow-list is `ORDER_ALLOWED_VIEWER_IDS` (fingerprints). |
| Non-staking check, engine side | VERIFIED | The hosted button produced check `chk_31ae716bb25fc4bf` (link path, because the direct path is not configured); the identical document filed as owner-authored issue #60 was **ACCEPTED by the queue processor 12 seconds later** (author check passed, comment posted, issue closed, row recorded; **no order, ticket or stake; ledger untouched**) and appears in Diagnostics → "Checks the engine has answered". |
| Token's issue author accepted by the processor | VERIFIED for the owner account; not yet exercised with the real token | The processor accepts only `burnettj93-sys`; a check by anyone else is listed as `IGNORED_AUTHOR`. A fine-grained token created by the owner authors as the owner. |
| Pressing the button files the order itself (one click), recorded and shown in the hosted app | **EXTERNAL BLOCKER** | The app has no `PAPER_ORDER_TOKEN` and no allow-list. Owner steps (exact, no secret in chat) are in `docs/MANUAL_ORDERS.md`: (1) create a fine-grained GitHub token for this repository, **Issues: read and write** only; (2) read your fingerprint from Diagnostics → Order path; (3) in Streamlit → Settings → Secrets set `PAPER_ORDER_TOKEN` and `ORDER_ALLOWED_VIEWER_IDS = "748a81cd1777"`; (4) re-open Diagnostics: all rows `yes`, then "Run non-staking order-path check". |
| Real one-click stake | not done, by instruction | **No new test stake was created.** It will be made only after the owner explicitly selects an option and the secrets above exist. |

Trust model for the viewer id (stated, not hidden): the platform gateway adds the header after viewer authentication; outsiders cannot reach the app; an invited viewer who learned another viewer's raw id could replay it, so keep the invite list to trusted people. Worst case: a $10 paper order.

## 2. Player roles — VERIFIED

Hosted Players detail (Erik Karlsson, tested after reboot): "Estimated usage tier: Defense 1 · Estimated PP usage: high", labelled "inferred from ice time — not an assigned line or power-play unit"; a separate "Reported lineup" block (none shown: the lineup source is off, see 8). Columns "Est. usage tier / Est. PP usage / Reported line / Reported PP" on Players, Game Detail and Team Intelligence. A reported lineup is displayed only if its source is identifiable (team or recognized beat reporter, with name and link) and under 48 hours old; on 2026-10-08, 14 of 32 teams' reports had no identifiable source (the site's automatic "Last Game" lineup, or "Projected") and are never shown as lines or units. (`tests/test_dailyfaceoff.py::TestLineupStandard`.)

## 3. Starting goalies — policy built; automation INCOMPLETE; source needs an owner decision

| Piece | Status | Evidence |
|---|---|---|
| Revised policy (accept Daily Faceoff "Confirmed" backed by an identifiable team source or a recognized beat reporter; store source, link, time, basis; keep Expected/Likely/unsupported distinct; freshness; conflicts; not team-post-only) | VERIFIED in tests, not exercisable live | `operational/dailyfaceoff.py`, `operational/goalie_confirmations.py`, `operational/recognized_starter_sources.json` (26 handles seeded from the sources Daily Faceoff itself cited on 2026-10-08, 2 excluded for handle/name mismatch; owner-editable). 31 tests in `tests/test_dailyfaceoff.py`: team post and recognized reporter confirm; Likely, blank, unrecognized, suspended, stale (>30 h), future-dated, "text does not name the goalie" do not; a later report naming another goalie is a CONFLICT and the gate stays closed; a dead feed (>90 min) withdraws automated confirmations; an expectation never walks back a confirmation; per-reporter accuracy record suspends a reporter after 2 misses in 10. On the 2026-10-08 sample all 11 "Confirmed" items qualify (1 team, 10 recognized reporters). |
| Failure handling, caching, timestamps, off switch | VERIFIED in tests | failed fetch keeps the last good copy with its own fetch time and records the cause; fetch intervals 20 min / 3 h; each row carries the item's own timestamp and the fetch time; `NHL_ENGINE_DAILYFACEOFF` unset ⇒ **zero requests** (default). |
| **Access terms** | **EXTERNAL BLOCKER (owner decision)** | Concrete restriction found: Daily Faceoff belongs to The Nation Network, whose Terms of Service (https://oilersnation.com/terms-of-service; they state they govern "any and all of its subsidiaries, affiliates, brands") prohibit using "any robot, spider, rover, scraper or any other data-mining technology or automatic or manual process to monitor, cache, frame, mask, extract data from, copy or distribute any data from the Services" and any commercial use beyond keeping information "for your own non-commercial purposes". Daily Faceoff's own `/terms-of-use` redirects to a 404 and its footer links only a privacy policy. `robots.txt` allows the pages, but robots permission and a low request rate do not establish permission. About 150 requests were made on 2026-10-08 during development, before this finding. **The reader is therefore OFF by default**; to use it, set `NHL_ENGINE_DAILYFACEOFF=ON` in `.env` (your acknowledgment that you have read the terms and accept the use), or obtain a licence (Nation Network data access, or RotoWire's API). Until then the hosted Goalies and Game Detail pages correctly show every goalie Unconfirmed unless a person records a confirmation. |
| Manual fallback and the gate | VERIFIED | unchanged; hosted Goalies page shows the updated status text. |

## 4. Moneyline — VERIFIED as evidence-based; promotion not made

Chronological comparison (`docs/validation/moneyline_model_comparison.json`, `docs/MODEL_VALIDATION.md`): the strength model is nominally ahead of Elo in all four rows (−0.0014 to −0.0058 log loss) but every paired 95% interval reaches zero. **Not promoted.** The opt-in switch (`NHL_ENGINE_MONEYLINE_MODEL=strength-v1`) is now **inert unless the prospective evidence also supports it**: it takes effect only after 150 finished game-sides are scored and the paired interval of (strength − Elo) lies wholly below zero, and only if the owner asks (`tests/test_model_paths.py`). The shadow scoreboard (Elo, strength model, market no-vig) is live on the hosted Model Health page: 0 of 0 scored so far.

## 5. Credits and anytime goals — plan VERIFIED; coverage INCOMPLETE within the existing allowance

* **Audit** (`docs/ODDS_CREDIT_AUDIT.md`, provider headers, 0 credits unaccounted): trailing burn 27.0 vs an even pace of 11.9; duplicate shots purchases; first sweep and 3–4 captures per game; 3× per-job borrowing; month short ≈ 350 at the old cadence (pricing would have stopped ≈ Oct 19).
* **Implemented and running** (`operational/credit_planner.py`): day budget from the provider's remaining header; waterfall (T-35 decision pulls → display refresh → shots + points once per game in its actionable window → saves for confirmed starters → goals → refresh); wave-fair choice of priced games saved for the day; every paid job asks the planner and writes a ledger; sweeps no longer re-buy shots and the early sweep is retired. **Production evidence (2026-10-08):** the plan priced 3 of 10 games on a D = 11.46-credit day; the trader bought BOS (shots + points + **goals**, 3 credits) and DAL (2 credits) at ≈ 1.5 h before puck drop; the hosted Diagnostics page shows the credit position, the plan and the month view.
* **Anytime goals**: built end to end (certified against a real archived payload, legs, settlement, coverage) and **live**: the production option board now contains 6 `PLAYER_GOALS` legs. Goals are bought only for games the plan has credits for (1 on 2026-10-08).
* **What does not fit** (exact): pricing every game with the required markets at 7 games a night needs 18 credits/day against 11.5 → **short 6.5/day (≈ 156 credits through the cycle)**; everything incl. goals for all games and saves: 29/day, **short ≈ 420**. Trade-offs, with nothing purchased or changed: `docs/ODDS_CREDIT_AUDIT.md` §5 (A. run as built; B. price more games with fewer markets; C. a larger provider allowance — a purchase, not made; D. accept running dry).
* An action I attempted and was denied: a one-credit capture of a real `spreads` payload ("Real-World Transactions"); not bypassed (see 6).

## 6. Puck line — unmet requirement; components separated

* **Built and tested**: settlement (`resolve_puck_line` on the official final score; an extra-time margin is one goal; mapped from the ledger leg `PUCK_LINE`, team, signed line); a shape validator for the provider's `spreads` market; an owner-run capture script that spends **one** credit only with `--confirm-spend-1-credit` and archives and trims a real payload for certification.
* **Not built — needs your approval**: the price **contract is uncertified** because no real `spreads` payload exists; the one-credit capture was denied by the approval layer and not bypassed. Run `python3 deploy/capture_puck_line_contract.py --confirm-spend-1-credit` (or tell me to) to unblock it.
* **Unvalidated**: the prediction. The Skellam result stays on record (log loss 0.52926 vs base rate 0.52751). `puck-line-direct-v1` beats the base rate on two earlier development folds (95% intervals below zero) but is untouched by any evaluation: its parameters are frozen, 2025-26 is not reused, and it is scored only on 2026-27 games as they finish (0 of 14 logged so far, visible on hosted Model Health; a review needs 300). It is **not enabled**, not in the ticket allowlist, and no puck-line ticket can exist.

## 7. Stray database — VERIFIED

`data/nhl.db`: 0 bytes, no tables, untracked, not referenced by `db.py` (which resolves `operational/runtime/nhl.db`), `.env`, or any launchd job; created by a connection to a missing path. Removed after verifying that (my first removal attempt was denied by the approval layer; your instruction authorized the second, which succeeded). `db.get_conn()` with no explicit path now raises `WrongDatabaseError` unless the resolved database exists, is non-empty and has a `games` table; `db.init_db()` is the only way to create one (`tests/test_runtime_db_hygiene.py`, `docs/RUNTIME_DB_HYGIENE.md`).

## 8. Hosted QA — VERIFIED

After merging and rebooting on the final code, every navigation item loaded without an exception on the hosted app by in-app navigation (Today, Games, Game Detail, Best Options, Players, Goalies, Team Intelligence, Model Health, Paper Performance, Ticket History, Morning Review, Data Status, Diagnostics) and by direct URL; changed pages verified visually (Players detail, Goalies detail, Model Health incl. "Evidence collecting on live games", Diagnostics credit position/plan/Order path/answered checks); mobile width (500 px minimum in this browser): no horizontal overflow on Players detail and Diagnostics. Safeguards unchanged: no demo content, current games, freshness labels, the account ($500 start, $465.76 cash, 9 tickets, history intact), provenance, settlement rules; automatic and manual evaluation reported separately. A new app build needs a reboot (a stale module cache caused a transient error until the first reboot) and a cold start briefly shows the legacy page list.

Incidents during deployment, all fixed: (1) a path-check row carried a key (`token_configured`) the snapshot secret-name guard forbids, so one 15-minute cloud publish failed (21:22–21:24 UTC; the previous snapshot stayed up); renamed, old rows migrated, test added; (2) the plan's first capture never fired for games that already had an older capture from the previous cadence; fixed and verified (captures at 21:30 UTC).

## 9. Credential rotation — EXTERNAL BLOCKER

The exposed Odds API key must be rotated by the owner (`docs/CREDENTIAL_ROTATION.md`). Afterwards: `python3 deploy/verify_odds_key.py`, then confirm the credit counter on Diagnostics.

## Release

Merged PRs: 56 (gap closure), 57–59 (identity probe and viewer-id allow-list), 61 (publish key fix), 62 (answered-checks list), 63 (plan first-capture fix), plus the final documentation change. `~/nhl_engine_release` is pinned to the final `master` commit shown in the delivery message.

## What is and is not demonstrated

* **A functioning product**: yes for what is VERIFIED above — observed data only, automatic paper tickets with timestamps and provenance, settlement, a shared $500 account, manual add through the verified link path, anytime-goal legs, a self-limiting credit plan.
* **A demonstrated betting edge**: **no**. Probabilities are better calibrated than simple baselines on held-out games; there are no historical sportsbook prices, so profitability cannot be tested on past data, and the paper record is too short to say anything. The 3-point haircut is a policy margin, not evidence of an edge.
