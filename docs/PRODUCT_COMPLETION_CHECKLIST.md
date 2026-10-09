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

## 5. Data Status defect (7:01 AM badges still showing at 6 PM ET) — VERIFIED; evidence at the end

## Unchanged VERIFIED items (release carried forward, tests green)

Today / ET dates / current games; best +100 option per person; shared account and exposure ($500 start, history intact, 9 tickets); automatic recording and settlement; no demo content; freshness and provenance rules; settlement safeguards; postmortems; Ontario verification flow; anytime-goal legs live; credit plan running; player-role relabelling; moneyline evidence gate (strength model not promoted); DB path guard.

## Credential rotation — EXTERNAL BLOCKER

The exposed Odds API key must be rotated by the owner (`docs/CREDENTIAL_ROTATION.md`), then `python3 deploy/verify_odds_key.py`.

## Data Status defect (2026-10-08) — trace, fix and evidence

**Where updates stopped.** Source updates → readiness/status → publication → hosted page: the page rendered `operational/data_readiness_cache.json` verbatim, a file written **once a day** by the 07:00 ET sync (`sync_daily.py`), so every badge was frozen at that
moment (11:01Z) and the age fields ("0.0 h") never grew; nothing re-published after the sync either. Its **odds entry was wrong evidence, not stale odds**: `operational/readiness.py` read `research/live_sog_board_cache.json`, a retired research file last touched 2026-08-27 ("1000 hours"), while real
moneyline quotes were minutes old (e.g. quote `2026-10-08T21:04:04Z`, fetched 21:04:32Z). So Odds read STALE for two reasons at once: wrong file, and a status that could never change.

**Fix** (`operational/source_status.py`, `dashboard/pages/9_Data_Status.py`): the engine records, at every publication (the trader at least every ~25 minutes; also after the daily and midday NHL syncs), the **timestamps** of each source's own evidence — data-through, last successful fetch, last attempt, freshness policy, next refresh, limiter —
never ages; the page derives each state from those timestamps and the current time when it is opened, with source-specific policies (daily files in hours, prices in minutes and tighter near puck drop). Separate states: Current, Stale (past policy), Not due, Disabled (a switched-off feed, with the reason), Budget-limited, Blocked, Unavailable, Estimate; a stale **status snapshot** (the engine stopped publishing) is a separate red banner. The readiness cache's odds evidence now points at the real pull. The old "two disagreeing caches"
explanation and the second snapshot banner are gone from the page; raw evidence, the old cache and job health are on Diagnostics. Reloading the page reads the published state and spends no credits. 10 tests (`tests/test_source_status.py`).

**Timestamp agreement after a real update (published 2026-10-09 00:24:41Z, hosted page evaluated 00:32:23Z):**

| Source | Engine evidence | Published status document | Hosted page |
|---|---|---|---|
| Moneyline prices | `moneyline_snapshot_cache.json`: newest quote `00:20:59Z` | basis `00:20:59Z` | "newest quote 2026-10-09T00:20:59Z", fetched 8:21 PM ET, age 11 min, policy ≤ 90 min |
| NHL schedule/results | `ingestion_health_cache.json`: pregame refresh success `00:06:05Z` | basis `00:06:05Z` | fetched 8:06 PM ET, age 26 min, next 8:36 PM ET |
| Player prop prices | credit-plan ledger: capture `2026-10-08T23:38:57Z` (archive header `23:38:58Z`) | basis `23:38:58Z` | newest capture …23:38:58Z, "Not due" (all 3 planned games captured; 7 of 10 games not priced under the credit plan) |
| MoneyPuck | manifest accepted `2026-10-08T11:01:19Z` | basis `11:01:19Z` | 7:01 AM ET, age 13.5 h, ≤ 36 h, next 7:00 AM ET |
| Starting-goalie confirmations; reported lines | feed switched off | fixed state Disabled, limiter PERMISSION | Disabled, with the reason |

**Stale status when updates stop (real, not simulated):** I set `NHL_ENGINE_CLOUD_PUBLISH=OFF` from 23:25Z to 00:24Z (the engine kept running; only publication stopped; restored afterwards, key set verified against a backup). At 00:23Z the hosted page showed the red "STATUS SNAPSHOT IS STALE: the engine last published this status 63 min ago", the NHL rows aged by themselves to 78 min and showed "next refresh … (overdue)", the moneyline row aged 64 min, and the prop-price row went Stale (2.9 h) —
none of them green by default; after the publication resumed, the banner cleared on the next page load. Publication-to-page lag is up to about 8 minutes (GitHub raw cache about 5 minutes plus the app's 180-second snapshot cache). Hosted QA on the final code (`95af5ae`): all 13 navigation items load without an exception.

**Incidents found and fixed while doing this:** (1) the T-35 moneyline decision pull was still on the old soft daily budget and was **deferred for the 7 PM cluster (6 games)** on 2026-10-08 — those games had no moneyline decision quote that evening; it now obeys the hard reserve only (the next cluster was captured at 22:50Z); (2) a goals capture was accounted as a base capture and starved the third planned game; goals now have their own class and legacy ledger rows are split (the third game was captured at 23:38Z); (3) one 15-minute publish failure earlier (forbidden key) — fixed.
