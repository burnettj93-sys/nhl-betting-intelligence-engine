# Odds API credit audit, goals cost and the bounded allocation

**Evidence base:** the provider's own headers (`x-requests-last`, `-used`, `-remaining`) on all 1,217+ archived responses in `operational/odds_archive/live/`.
Nothing here is estimated from code or from the provider's documentation. `operational/credit_allocation.py` recomputes every number below from the archive
(`python3 -c "import operational.credit_allocation as c; print(c.status())"`). As of 2026-10-08 19:35 UTC.

## 1. The plan and the cycle

* Allowance observed: 500 credits per cycle (`used + remaining = 500` on every response of the current cycle).
* Reset observed once: the counter fell from 182 used to 0 used between 2026-09-30 23:55Z and 2026-10-01 00:04Z, consistent with a calendar-month reset at 00:00 UTC on the
  1st. One observation; `NHL_ENGINE_ODDS_RESET_DAY` stays an owner-verification item (provider dashboard).
* On 2026-10-08 19:31Z: **203 used, 297 remaining**. With the 20-credit reserve, 277 are usable for the 23.2 days to 2026-11-01 00:00Z, an even pace of **11.9 credits/day**.

## 2. Actual credit use, every job (trailing 7 days = 189 credits)

| Call class | Job(s) that make it | Calls | Credits | Share |
|---|---|---|---|---|
| All-games moneyline (`/odds?markets=h2h`, 1 credit per call) | `moneyline-snapshot` (4 fixed times), `moneyline-pregame` (2-minute checks, pulls once per start cluster), `odds-daily-pull` | 59 | 59 | 31% |
| Per-game props: shots (alternate) + points | `real-parlay-paper-trader` via `best_bets.capture_prices` | 31 | 62 | 33% |
| Per-game props: shots (alternate) + saves | `prop-sweep-first` (3–4.5 h before puck drop), `prop-sweep-second` (45–75 min before) | 28 | 55 | 29% |
| Per-game contract probe: shots + saves + blocks + points + **anytime goals** | one manual diagnostic on 2026-10-05 | 4 | 13 | 7% |
| Events listing (`/events`) | every job | 429 | 0 | 0% |

Oct 1–8 totals 200 credits and matches the provider's own counter exactly (0 credits unaccounted).

Findings:
1. **The current cadence cannot last the month.** Trailing burn is 27.0 credits/day against an even pace of 11.9. At this burn the usable 277 credits run out around
   **2026-10-19**; after that the hard reserve stops every paid pull until 2026-11-01. The month is short by about **350 credits**. The jobs' guards allow up to 3× the even
   daily pace (`odds_quota.PREGAME_SOFT_MULTIPLIER`), which is why the early days overspent.
2. **Shots (alternate) is bought twice for the same game**: once with points (trader) and once with saves (sweeps). The trader treats the newest shots capture from either job as fresh,
   so the second purchase is not wasted when it lands inside the freshness window, but the combined per-game spend (2 + 2) is a third higher than one merged call (shots + points +
   saves = 3) would cost. Merging changes capture timing for saves, which is gated on a recorded starter confirmation, so it is a design decision, not a free fix.
3. Overnight moneyline pulls (00–10 UTC) cost about 3 credits/day with no games to price.

## 3. Marginal cost of anytime-goal prices

* **One credit per game per capture.** Evidence: the four 2026-10-05 five-market calls returned 3, 3, 4 and 3 markets (the anytime-goals market was among them every time) and were
  charged 3, 3, 4 and 3 credits — one credit per market that returned data.
* The market exists and is priced: DraftKings, 36–37 players per game, **one-sided "Yes" prices only** (e.g. +145 Guentzel, +150 Connor, +160 Pastrnak). There is no "No" price, so no
  no-vig probability can be derived; value is judged from the model probability against the quoted price (vig included), which is conservative.
* At the trader's measured 4.4 captured games per day: **+4.4 credits/day** if added to the shots + points call. If added only to the second sweep (the freshest window, 45–75 min out)
  about half of that.

## 4. Bounded allocation (implemented)

Optional markets (today: anytime goals) may be captured only when the month still balances after their cost:

`allow = (trailing_daily_burn + extra_per_day) × days_left ≤ usable_credits`

`operational/credit_allocation.py::goals_capture_decision` implements this rule. Today it evaluates **DENY**: need 729 credits against 277 usable (short 452; 350 of that shortfall exists
without goals). The rule never reaches into the reserve and never lowers an existing market's cadence on its own.

## 5. What it takes to price goals — decision for the owner (nothing was changed)

| Option | Credits/day | Month balance (Oct 8 → Nov 1) | Cost of the option |
|---|---|---|---|
| A. Status quo, no goals | 27.0 | −350 (pricing stops ≈ Oct 19) | none; but all paid pricing ends 12 days early |
| B. Hold to the even pace, no goals | 11.9 | 0 | roughly halves captures (about 4–5 games/night priced instead of all) |
| C. Hold to the even pace, goals included, fewer games | 11.9 | 0 | goals priced, but only for the games the budget reaches |
| D. Keep today's cadence + goals on a larger provider allowance | 31.4 | needs about 730 credits for the rest of the month (about 450 more than remain) | a paid plan change — not made; check the provider's current price list for the tier that covers it |
| E. Merge shots into one per-game call (shots + points + saves + goals = 4 credits/game vs 4 today for shots/points + shots/saves) | ≈ 27 + 0 | −350 | goals at no extra cost, but still over budget; saves timing must move |

No spending limit or plan was changed. With the pipeline built (see MARKET_COVERAGE_AUDIT.md), pricing goals is a single configuration decision once the month balances.

## 6. Hygiene

The credit position is shown on Diagnostics (admin) from `credit_allocation.status()`. The key-rotation blocker is unchanged: after rotating, run `python3 deploy/verify_odds_key.py`;
a new key starts a new counter, which this module reads from the first response.
