# Product completion checklist (gap-closure revision)

Status words, used strictly:

* **VERIFIED** — built, tested, and checked on the hosted app (or, where a requirement is a record of evidence, the evidence file is named).
* **INCOMPLETE** — a requirement that does not work end to end yet, with exactly what is missing. "Built but waiting for an input" is **not** VERIFIED.
* **EXTERNAL BLOCKER** — needs an action only the owner (or a third party) can take; the exact action is stated.

The previous revision's "DONE (blocked input)" is retired: each such item is restated below as INCOMPLETE or EXTERNAL BLOCKER.
Items marked **(branch)** are implemented and tested on `feature/gap-closure` (PR 56, full suite 3,618 tests OK) but are **not yet deployed**: the merge of that PR was not
permitted in this session, so nothing in this revision has been checked on the hosted app yet. Everything not marked (branch) was verified remotely on release `bd24a9b10f` and is unchanged.

## 1. Remote manual-add flow — INCOMPLETE (+ EXTERNAL BLOCKER for the secrets)

| Piece | Status | Evidence / what is missing |
|---|---|---|
| Link path (owner-authored pre-filled issue → engine → ledger) | VERIFIED | order `ME3D7C508EBFDD3` recorded 2026-10-08 via an owner-authored issue; ledger 8 → 9 tickets, cash $475.76 → $465.76. **This was filed with `gh`, so it is evidence for the queue, the revalidation, the ledger and the page feedback — not for the button.** |
| Pressing the button in the authenticated hosted app files the order itself (one click) | INCOMPLETE | Never demonstrated. Needs the Streamlit secrets `PAPER_ORDER_TOKEN` and `ORDER_ALLOWED_EMAILS` (EXTERNAL BLOCKER: only the owner can create the token; exact steps in `docs/MANUAL_ORDERS.md`, no secret goes through chat) and the hosted app must supply `st.user.email`. |
| A way to prove the above without staking | (branch) | Diagnostics → "Order path (one-click add) check": yes/no rows (token configured, allowed emails, viewer email present/allowed, direct ready) and a "Run non-staking order-path check" button → `order-path-check` issue → owner-author check → result recorded, **no order, ticket or stake, ledger untouched** (`tests/test_manual_orders.py::TestOrderPathCheck`, `tests/test_product_pages.py`). Not deployed, so `st.user.email` availability on the hosted app is still unknown. |
| Token issue author accepted by the queue processor | (branch) | The processor accepts only `burnettj93-sys`; a check opened by anyone else is listed as `IGNORED_AUTHOR` with the login. A fine-grained token created by the owner authors issues as the owner. |
| Duplicate prevention, durable recording, status feedback, shared-account reconciliation | VERIFIED | order-id and bet-identity idempotency, concurrency test, `NEEDS_ACCEPTANCE` flow, reconciliation (`tests/test_manual_orders.py`, `audit_evidence/ledger_board_reconciliation.json`) |
| No additional test stake | — | None created. A real one-click stake will be made only after the owner selects an option explicitly. |

## 2. Player roles — VERIFIED (branch: remote check pending)

* Inferred fields renamed everywhere: **Est. usage tier** (forwards/defense ranked by recent ice time) and **Est. PP usage** (High/Some by recent power-play minutes). No page says "Line 1" or "PP1" for an inferred value (`research/product_models/live.py::infer_roles`, `tests/test_product_pages.py`, `tests/test_product_data.py::TestRoles`).
* **Reported lineup** is a separate block: forward line / defense pair, PP unit, PK unit, injury status, the reporter, link, report time and our fetch time, from Daily Faceoff line combinations for all 32 teams (HTTP 200 on 2026-10-08; `operational/dailyfaceoff.py`; fixtures and tests in `tests/test_dailyfaceoff.py`). It is a reporter's published expectation for the next game, not a confirmed lineup. Players the source does not list show "—", never an inferred substitute.

## 3. Starting goalies — INCOMPLETE (automation partial)

| Piece | Status | Evidence |
|---|---|---|
| Automated source exists and is reachable | (branch) VERIFIED in tests and by live fetch | Daily Faceoff starting-goalies page, HTTP 200, 10 games / 20 slots on 2026-10-08; robots allow; no terms page found (owner to read); rate-limited; kill switch. `docs/STARTING_GOALIE_SOURCE_AUDIT.md` addendum. Ingest into `goalie_status_events` was run against a copy of the real DB: 15 rows written, repeat run 0. |
| Automated confirmations are usable for gating | INCOMPLETE | Only **1 of 11** "Confirmed" labels on 2026-10-08 cited the team's own post; 10 cited beat reporters. The reader records CONFIRMED only for team-sourced posts; reporter-sourced ones stay EXPECTED. So saves/moneyline confirmations still mostly need a person. Accepting reporter-sourced confirmations is the owner's decision (`NHL_ENGINE_ACCEPT_REPORTER_CONFIRMATIONS=ON`). |
| Manual fallback and the gate | VERIFIED | unchanged; `tests/test_product_data.py`, `tests/test_dailyfaceoff.py::TestIngestAndGate` (an expectation never walks back a confirmation). |

## 4. Moneyline — INCOMPLETE (no promotion; evidence collecting)

* Why the displayed model differs from the pricing model: the strength model (`goalie-team-v1`) was validated for the product pages; the ticket path predates it and uses the unmodified Elo path (`moneyline-t35-v1`) with a heuristic band (`operational/moneyline_model_path.py` docstring).
* Chronological comparison on the same games, two rolling-origin folds, paired bootstrap (`docs/validation/moneyline_model_comparison.json`, `docs/MODEL_VALIDATION.md`): the strength model is nominally ahead in all four rows (−0.0014 to −0.0058 log loss) but every 95% interval reaches zero; in 2025-26 Elo is itself within 0.0002 of the home-rate baseline when shootouts are included. Neither is shown to beat a sportsbook price (no historical prices exist). **Not promoted.**
* Delivered: versioned opt-in switch `NHL_ENGINE_MONEYLINE_MODEL=strength-v1` (default Elo, same conservative band, version tag on legs) and a shadow log scoring Elo, the strength model and the market's no-vig probability on live games; Model Health shows the scoreboard (needs 150 finished game-sides before any statement). `tests/test_model_paths.py`. (branch)

## 5. Anytime goals and credit quota — INCOMPLETE (built; capture gated by the budget)

* **Credit audit** (`docs/ODDS_CREDIT_AUDIT.md`, all numbers from the provider's own headers, 0 credits unaccounted): trailing burn 27.0 credits/day against an even pace of 11.9; 297 credits remain on 2026-10-08, so paid pulls stop around **2026-10-19** with about **350 credits missing** for the month. Shots (alternate) is bought by two jobs for the same game.
* **Marginal cost of goals**: exactly one credit per captured game (4 real calls: 3, 3, 4, 3 credits for 3–4 returned markets). DraftKings quotes 36–37 players a game, one-sided "Yes" prices.
* **Built (branch)**: contract certified against a real archived payload (`tests/fixtures/draftkings_player_goal_scorer_real_payload.json`), `PLAYER_GOALS` family in the allowlist (threshold 1), leg builder, settlement mapping to the existing resolver, revalidation, labels, coverage rows (`tests/test_goals_market.py`).
* **Bounded allocation (branch)**: goals are added to the per-game call only when `(trailing burn + goals cost) × days left ≤ usable credits`. Today it **denies** (needs 729, has 277, short 452), so goals prices are not being captured. Options and trade-offs, with no plan or limit changed: `docs/ODDS_CREDIT_AUDIT.md` §5. This is not externally blocked; it needs the owner's choice of trade-off.

## 6. Puck line — INCOMPLETE (unmet requirement)

* The failed Skellam model stays on record (log loss 0.52926 vs base rate 0.52751 on 2025-26).
* Alternative `puck-line-direct-v1` (direct logistic on the strength difference): judged only on two earlier folds (2023, 2024), where it beats the base rate with 95% intervals below zero and also beats Skellam; parameters then frozen; 2025-26 not used again. The untouched evaluation set is 2026-27, logged before each game and scored when final (needs 300 games). (`docs/validation/puck_line_alternative.json`, branch)
* **Not enabled**: no puck-line prices are requested, no settlement mapping exists, and the prospective evidence has not accumulated. The requirement is unmet.

## 7. QA on the hosted app — INCOMPLETE

Pages, navigation paths, mobile width and the no-demo / current-game / freshness / account / provenance / settlement safeguards were verified remotely on `bd24a9b10f` and their tests still pass. The changed pages (Players, Goalies, Game Detail, Team Intelligence, Model Health, Diagnostics) and every navigation path need a fresh remote pass **after the branch is merged and deployed**. Not done.

## 8. Credential rotation — EXTERNAL BLOCKER

The exposed Odds API key must be rotated by the owner (`docs/CREDENTIAL_ROTATION.md`). After rotation: `python3 deploy/verify_odds_key.py`, then confirm the credit counter and the Diagnostics credit panel on the new key.

## Unchanged and VERIFIED (release `bd24a9b10f`)

Today / ET dates / current-game defaults / exact Game Detail; best +100 option per person; shared account and exposure; automatic recording and settlement; no demo content anywhere; freshness and provenance rules; settlement safeguards; postmortems; Ontario verification flow (price spot-checks; settlement void rules are not guessed); automatic and manual evaluation kept separate.

## What is and is not demonstrated

* **A functioning product**: yes, for what is VERIFIED above — observed data only, automatic paper tickets with timestamps and provenance, settlement, a shared $500 account, manual add through the verified link path.
* **A demonstrated betting edge**: **no**. The probabilities are better calibrated than simple baselines on held-out games; there are no historical sportsbook prices, so profitability cannot be tested on past data, and the paper record is too short to say anything. The 3-point probability haircut is a policy margin, not evidence of an edge.
