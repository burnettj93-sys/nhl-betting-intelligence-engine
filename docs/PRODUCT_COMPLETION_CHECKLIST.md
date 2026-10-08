# Product completion checklist (revision 3)

Status words, used strictly: **VERIFIED** (built, tested, and checked on the hosted app, or the evidence file is named) · **INCOMPLETE** (does not work end to end; the exact missing piece is stated) ·
**EXTERNAL BLOCKER** (needs an action only the owner or a third party can take; the action is stated). The product is **not** complete: items 1–4 below are unmet. Code on `master`; the scheduled jobs run the
pinned release checkout at the same commit (see the delivery message for the commit). Nothing in this revision is a claim of a betting edge.

## 1. Manual writes (one click from the hosted app) — INCOMPLETE; EXTERNAL BLOCKER for credentials

| Piece | Status | Evidence |
|---|---|---|
| Is `X-Streamlit-User` a trusted authenticated identity? | **Trust not established → not used** | Measured on the hosted app: `st.user` empty, header present as an opaque 76-character value. Streamlit staff (https://discuss.streamlit.io/t/community-cloud-authentication-is-x-streamlit-user-a-supported-fallback/121998, 2026-07-19): "not a documented or stable public API … should not be relied upon for authentication or user identification … no guarantees about its format or presence"; "the only supported and stable approach is to configure your own OIDC provider and use `st.login`/`st.user`". No documentation says the gateway overwrites a client-supplied copy, and it cannot be tested from outside (the app is reachable only through the platform's login). Stable observations and hashing do not prove trust, so the earlier fingerprint allow-list **was removed**. |
| Writes authorised by a supported sign-in | VERIFIED in code and tests; hosted state verified | `st.login()` (OIDC) → verified `st.user.email` → `ORDER_ALLOWED_EMAILS`, plus `PAPER_ORDER_TOKEN`; the platform header is never read for authorisation (`tests/test_manual_orders.py::TestSupportedSignInOnly`); `Authlib` added to `dashboard/requirements.txt`; the hosted Diagnostics panel shows the new rows and the setup message after a reboot (sign-in configured: no). |
| One-click write after configuration (button → durable record → status → duplicate prevention → reconciliation) | **EXTERNAL BLOCKER — not verified** | Needs the owner's Google OAuth client, GitHub token and Streamlit secrets (exact steps: `docs/MANUAL_ORDERS.md`, repeated in the setup checklist). I will verify it the moment they exist. |
| Non-staking verification | VERIFIED, kept | Diagnostics → "Run non-staking order-path check" (hosted button → link or direct; queue accepted check #60 in 12 s on 2026-10-08, no order, ticket or stake). |
| Real stake | not done, by instruction | none recorded; only after the owner's explicit selection. |

## 2. Goalie, lineup and injury sources — INCOMPLETE; EXTERNAL BLOCKER

Daily Faceoff stays **disabled** (the opt-in switch does not itself grant permission; the Nation Network terms prohibit scrapers and automated extraction; no permission was requested or granted). The product shows only what
it can source: Goalies are Unconfirmed unless a person records a confirmation; Players shows "Estimated usage tier / Est. PP usage" (inferred) and an empty "Reported lineup" block; Data Status says Disabled with the reason.
Investigated alternatives with exact blockers: `docs/STARTING_GOALIE_SOURCE_AUDIT.md` Addendum 2 — NHL's own API (official dressed rosters ≈ 90 minutes before puck drop, both goalies listed, no starter, lines, PP units or injuries; **NHL.com terms also prohibit unauthorized automated compiling, which is a standing exposure of the existing core feed**), MySportsFeeds (from CA$5/month personal "if you qualify", CA$25 commercial; lineup and injury add-ons unpriced; crowd-sourced), SportsDataIO (quote only; free trial is UEFA only), Sportradar (trial key; quote), RotoWire (sales). None is integrated, purchased or registered.

## 3. Odds budget — INCOMPLETE (existing allowance cannot meet the requested coverage)

`docs/ODDS_BUDGET_CONFIGURATIONS.md` (observed costs, schedule on file, reset date check): **A (existing allowance, running):** 78 of 163 remaining games (48%) get shots + points, 18 of them goals, one capture per game, 3 moneyline decision pulls + 1 display refresh a day;
**in-allowance alternative A2 (owner's choice, implemented, off):** points only → 132 of 163 (81%); **B (all games, priority markets, actionable captures):** 441 credits for Oct 9–31, short 175 on the free plan; **C (full markets, 3 captures per game):** 2,117 credits, short 1,851; both need the provider's next tier
(20K credits, $30/month — a purchase, **not made**). Reset date: the provider does not state it; observed once at 2026-10-01 00:04Z (calendar month, UTC); only the account dashboard can confirm. Five-ticket target: at most `floor(3k/2)` tickets from k priced games (4+ games to reach 5); under A 13 of 23 nights can reach 5.
Duplicate/unnecessary calls removed (see the document). A defect found and fixed: the T-35 moneyline decision pull for the 7 PM cluster (6 games) on 2026-10-08 was deferred by the old soft daily budget — those six games had no moneyline decision quote that evening; it now obeys only the hard reserve (the next cluster was captured at 22:50Z).

## 4. Puck line — unmet; components separated

* **Non-spending work, done and tested:** settlement (`resolve_puck_line`, official final score, extra-time margin one goal, ledger mapping), a shape validator for the provider's `spreads` market, a leg builder whose legs can never be selected (`provider_contract_verified` False, `model_threshold_eligible` False, family not in the allowlist), an owner-run capture script, and a certification test that activates when the real fixture exists.
* **Contract certification — needs one user action:** one real DraftKings `spreads` payload, which costs **one Odds API credit**. My capture was rejected by the approval layer ("Real-World Transactions") and was not bypassed. The exact action: run `python3 deploy/capture_puck_line_contract.py --confirm-spend-1-credit` yourself, or tell me in chat that you authorise spending one credit on it. Then the fixture, a parity test and the contract entry follow. This is **separate from** validation.
* **Model validation — separate requirement, unmet:** the Skellam result stays on record (0.52926 vs base rate 0.52751); `puck-line-direct-v1` is frozen and scored only on untouched 2026-27 games (see Model Health: 0 of its logged games finished; a review needs 300). Nothing unvalidated is in selection.

## 5. Data Status defect — see the section at the end (VERIFIED after the evidence below)

## Unchanged VERIFIED items (release carried forward, tests green)

Today / ET dates / current games; best +100 option per person; shared account and exposure ($500 start, history intact, 9 tickets); automatic recording and settlement; no demo content; freshness and provenance rules; settlement safeguards; postmortems; Ontario verification flow; anytime-goal legs live; credit plan running; player-role relabelling; moneyline evidence gate (strength model not promoted); DB path guard.

## Credential rotation — EXTERNAL BLOCKER

The exposed Odds API key must be rotated by the owner (`docs/CREDENTIAL_ROTATION.md`), then `python3 deploy/verify_odds_key.py`.

## Data Status defect (2026-10-08) — evidence

*(filled in below from the hosted checks)*
