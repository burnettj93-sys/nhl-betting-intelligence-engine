# Phase 1.5 — Provider Contract Trial: Report

## A. `closing_draftkings_snapshot()` fix

Fixed in `features/point_in_time.py`. The query now requires:
```sql
AND event_start_utc IS NOT NULL
AND captured_at_utc < event_start_utc
```
(strict `<`, so a quote captured exactly at puck drop is excluded, and a
row with no `event_start_utc` at all is excluded rather than silently
treated as "no boundary to enforce"). `received_at_utc` semantics were
deliberately left untouched, per your instruction that this depends on
the still-unresolved historical-provider knowledge-time question.

New file: `tests/test_closing_line_pre_start_integrity.py`, 6 tests:
- 18:59 quote is the closing line (the exact required scenario: 18:59 /
  19:00 puck drop / 19:05 quote)
- a 19:05 post-start quote is never returned as closing
- a quote captured exactly AT puck drop is excluded
- only at-or-after-start quotes available → `None`, not a fabricated price
- a `NULL event_start_utc` row is never treated as a closing candidate
- no data at all → `None`, not an exception

## B. Full local test results

```
previous = 322 (316 from Phase 1 + prior)
new total = 322 + 6 = 328
passed   = 328
failed   = 0
errors   = 0
skipped  = 0
```
No existing test was modified or weakened. One pre-existing test
(`test_point_in_time.py::test_closing_snapshot_ignores_prediction_time_by_design`)
already anticipated this fix — it deliberately set a far-future
`event_start_utc` "to avoid post-start rejection," which continues to pass
unchanged under the real predicate.

## C. Trial access status

```
THE ODDS API PAID HISTORICAL TRIAL:
BLOCKED — HISTORICAL API KEY REQUIRED
```
I checked this session's environment variables, a `.env` file in the
project directory, and the usual local-credential locations — no
`ODDS_API_KEY` or equivalent was present anywhere. The Odds API requires
an API key on **every** request, including free-tier live odds, not just
historical — so there is no key-less way to inspect even the *shape* of a
real response, and the browser bridge (Part C's fallback for the
sandbox's network restriction) can't substitute for a credential that
doesn't exist. Per your explicit instruction, I have not purchased a
subscription, generated a key, or substituted any documentation-example
JSON as if it were a genuine response.

**Parts D through M could not be executed** — every one of them depends on
having at least one authenticated request against the real historical
endpoint. Rather than partially answer them from marketing-page prose
(which is exactly what Phase 1 already did, and what this Phase 1.5 trial
exists to get past), I'm reporting them as blocked rather than guessing.

## D–M

**BLOCKED** — no historical-enabled API key available. Nothing here is
answered.

## N. Required report items

```
A. closing_draftkings_snapshot fix     -- DONE (see A above)
B. full local test results             -- 328/328 passing (see B above)
C. trial access status                 -- BLOCKED (see C above)
D. exact provider requests made        -- NONE MADE (blocked before any request)
E. NHL games sampled                   -- NONE (blocked)
F. raw historical response structure   -- UNKNOWN (blocked)
G. DraftKings coverage by horizon      -- UNKNOWN (blocked)
H. outer snapshot timestamp semantics  -- UNKNOWN (blocked)
I. bookmaker last_update semantics     -- UNKNOWN (blocked)
J. market last_update semantics        -- UNKNOWN (blocked)
K. market-status availability          -- UNKNOWN (blocked)
L. two-sided h2h coherence             -- UNKNOWN (blocked)
M. event-mapping quality               -- UNKNOWN (blocked, no sample events)
N. closing-line feasibility (provider) -- UNKNOWN (blocked)
O. recommended knowledge-time model    -- NOT PROPOSED YET -- would be
                                            guessing without B/H/F evidence
P. revised normalized schema proposal  -- NOT REVISED -- Phase 1's proposal
                                            stands as provisional only,
                                            unchanged, per Part L's
                                            instruction not to build the
                                            final schema on assumptions
Q. estimated credit/cost requirements  -- see below (public pricing only,
                                            not usage-header-confirmed)
R. revised provider recommendation     -- UNCHANGED from Phase 1 (The Odds
                                            API as first trial candidate) --
                                            nothing was learned to revise it
                                            either up or down
```

### Q. Cost estimate (from public pricing pages only — not confirmed against real credit-usage headers, since no request was made)

The Odds API's documented historical cost is **10× the standard per-region-per-market
credit rate** for the main historical-odds endpoint. Their live-odds
endpoint costs roughly 1 credit per (region × market) combination
requested; scaling that stated 10× multiplier to a single historical
snapshot query (1 sport, 1 region, `bookmakers=draftkings` restricting to
one book) gives a rough order-of-magnitude estimate:

| Scope | Requests (approx.) | Est. credits (10×, unconfirmed multiplier base) |
|---|---|---|
| 1 game × 8 horizons | 8 historical snapshot calls | ~80 credits (order-of-magnitude only) |
| 100 games × 8 horizons | 800 calls | ~8,000 credits |
| 1 full season (~1,312 games) × 8 horizons | ~10,500 calls | ~105,000 credits |
| Full 5-minute time-series equivalent (season, ~3 hours pregame ≈ 36 snapshots/game) | ~47,000 calls | ~470,000 credits |

**These numbers are not trustworthy enough to budget against.** They're
extrapolated from the stated "10×" multiplier and an assumed per-call
credit base that I could not confirm without a real request (no
X-RateLimit/credit-usage response header has actually been observed). The
real number could differ meaningfully depending on how "per region per
market" actually resolves for a single-bookmaker, single-market historical
query. **A real cost figure requires making at least one real authenticated
call and reading its actual credit-usage header** — which is exactly what's
blocked right now.

## Answers

```
CAN WE OBTAIN SUITABLE HISTORICAL DRAFTKINGS NHL DATA?
UNKNOWN — unchanged from Phase 1. No new evidence either way; still
blocked on credential access.

CAN THE ODDS API RECONSTRUCT COHERENT TWO-SIDED NHL MONEYLINES?
UNKNOWN — could not be tested.

DO WE HAVE A DEFENSIBLE HISTORICAL KNOWLEDGE TIMESTAMP?
UNKNOWN — could not be tested.

CAN WE RELIABLY DEFINE A PRE-PUCK DRAFTKINGS CLOSING LINE?
UNKNOWN as to the PROVIDER side (no data). Note: our OWN engine-side
closing-line selector (closing_draftkings_snapshot()) is now correctly
implemented (Part A) and independently verified via 6 passing tests —
that half of the question is resolved. Whether a real provider can
SUPPLY a genuine pre-puck DraftKings quote to feed it remains unknown.

IS THE ODDS SCHEMA READY TO BE CHANGED?
NO — unchanged from Phase 1, and per Part L, deliberately not touched
this pass either.

ARE WE READY TO BUILD THE ECONOMIC BACKTEST?
NO.
```

## What I need from you to unblock Parts D–M

One of:
1. An existing The Odds API key (even a free-tier key would let me
   confirm request/response mechanics and error semantics, though
   historical endpoints specifically require a paid plan) — pasted here
   or set as an environment variable I can read.
2. Explicit authorization to purchase the lowest-cost paid plan myself
   (you'd be authorizing a real charge — I won't do this without you
   saying so directly, per Part C).
3. You obtain a key/trial yourself and hand it to me, or run the sample
   queries yourself and share the raw JSON back for me to analyze under
   the same Part F–M framework.

I have not attempted any workaround (no doc-example substitution, no
scraping, no synthetic payload) — this is a genuine, reportable stop, not
a partial answer dressed up as one.
