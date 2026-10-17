# Odds allowance: how the existing 500 credits are spent (revised 2026-10-17)

**The working plan is the existing free allowance. Nothing is purchased, upgraded or assumed.** The 20K tier ($30/month, USD, provider pricing page fetched 2026-10-17) remains an option the owner may choose; no feature below depends on it, it only widens how many games can be priced.

## Actual state (from the provider's response headers recorded in the credit ledger)

| | |
|---|---|
| Credits remaining | **192** (last provider header) |
| Reset date | **UNVERIFIED**: assumed the 1st of the month (`NHL_ENGINE_ODDS_RESET_DAY`); the plan is built on the conservative reading, 15 days left in this cycle. Confirm the date in the Odds API account and set the variable; the planner then uses it |
| Reserve kept | 20 credits |
| Standard-spread cost | **ASSUMED 2 credits per league pull (h2h + spreads, one credit per market)**; replaced by the observed header cost after the first live pull (`observed_league_cost`) |

## The 30-day envelope the planner uses (`operational/credit_planner.py`, ALLOC_VERSION 4)

| Pool | Credits / 30 days | Rule |
|---|---|---|
| Morning league moneyline + spreads | about 60 | one pull a day at 08:00 ET |
| Pregame league moneyline + spreads | about 60 | one shared pull for the first wave; later waves only from surplus |
| Player odds (shots + anytime goals + goalie saves, 3 credits per game) | about 360 at 4 games a day | divided across the REMAINING days by schedule size (weight = games, capped at 8) before anything is spent |
| Recovery / selective retries | about 20 | `ceil(20 x days_left / 30)`; spent only on missing markets of still-upcoming games |
| Points, extra refreshes | 0 | only from an explicit surplus |
| Tomorrow price check, morning player look, T-35 decision pulls | **0** | removed from the core allocation (they were the redundant spend, see `docs/COVERAGE_REGRESSION.md`) |

Planning always starts from the real remaining balance, the real remaining schedule and the (labelled) reset date, so unused credits roll forward automatically inside the cycle and the plan shrinks honestly when the balance is low. Today's numbers (`month_view`): 192 remaining, 60 for league pulls, 10 for recovery, **102 for player odds = about 2.3 games a day** for the remaining 15 days. That is the actual coverage the free allowance can buy this cycle; the board says so game by game ("not priced: budget") and never implies more.

## How a day is spent

1. **Waves.** Games whose start times are within 60 minutes form a wave. Each wave's selected games are captured once, targeted 90 minutes before the wave's first game and complete 60 minutes before it (`operational/capture_timeline.py`). Late captures (sleep, network) are still made until 15 minutes before the first game; after that the window is reported MISSED and never bought late.
2. **Selection** is automatic: the first wave first (early games always included), then the larger later waves, then data quality (player statistics, participation, market availability).
3. **Reservation before spending** (`credit_reservations.json`, file-locked, 15-minute expiry) and **settlement against the provider's actual charge** afterwards. Two concurrent jobs cannot spend the same credits; `reconciliation()` compares planned and charged credits per day.
4. **One capture, many readers.** The Today page, Slate Board, Best Options, Parlay Builder and prediction logging all read the same wave capture. Nothing pays twice for the same market.
5. **Freshness** is judged by the provider's quote timestamp, not by when we fetched.
6. Markets that were not priced stay visible on the board with the reason (budget, not posted, window missed).

## What I need from you

Nothing for the product to work. Optional: (a) confirm the reset date and set `NHL_ENGINE_ODDS_RESET_DAY`; (b) if you want more than about 2 games a day priced this cycle, the 20K tier is $30/month USD, billed when you subscribe. I cannot buy it, and the engine does not assume it.
