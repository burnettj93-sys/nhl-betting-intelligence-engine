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

## 4. The credit plan (implemented 2026-10-08, `operational/credit_planner.py`)

**Duplicates and unnecessary refreshes found** (Oct 1–8): shots (alternate) was bought by two jobs for the same game (trader with points; both prop sweeps with saves); the 3–4.5 h "first" sweep bought
shots and saves hours before any starter is confirmed and before a price taken then could still be fresh at the decision; prop captures refreshed every ~55–105 minutes as a price neared its freshness limit
(up to four captures a game); each job could borrow up to 3× the even daily pace on its own; the display-only moneyline refresh ran every 75–150 minutes; the shots + points call and the sweeps were unaware of one another.

**What the plan does** (all recomputed from the provider's own remaining-credit header, so it corrects itself day to day):

| Step | Spend | Rule |
|---|---|---|
| Day budget D | `(remaining + spent today − 20 reserve) / days left at the start of today` | anchored to the start of the day so it does not shrink as the day's own spending happens |
| 1. Moneyline decision pulls | up to 3 credits, always funded | the T-35 league-wide pull per start cluster (the decision feed). Never starved. |
| 2. Display refresh | 1 credit (a second only once every game is priced) | the Games page moneyline age; lowest priority |
| 3. Shots + points | 2 credits per game, **one capture per game** inside its actionable window (≤ 1.75 h before puck drop, fresh through puck drop) | games chosen wave by wave (a wave = puck drops within 90 minutes), one from each wave in turn, earliest first; the chosen set is saved for the day and not reshuffled |
| 4. Saves | 1 credit, only for a game with a **confirmed** starter, in the 45–75 minute sweep | the first sweep is retired; sweeps no longer re-buy shots |
| 5. Anytime goals | +1 credit for a game already priced in step 3 | after saves; only credits left over |
| 6. Refresh | a second props capture | only with leftover credits |

Every paid job records its spend in a ledger (`credit_plan_ledger.jsonl`) and asks `credit_planner.authorize` first; the hard reserve always wins. Timestamp safeguards are untouched (provider update time, retrieval time, freshness limits).

**What fits** (2026-10-08, 297 credits remaining, 24 days to the reset): D = 11.5 credits/day.

| Games that day | Credits needed: required markets for every game (decision 3 + display 1 + 2/game) | Credits needed: everything (+ saves for confirmed starters, goals, second refresh) | Games priced with D = 11.5 |
|---|---|---|---|
| 4 | 12 | 20 | 3 of 4 |
| 7 (typical) | 18 | 29 | 3 of 7 (+1 goals market) |
| 12 | 28 | 44 | 3 of 12 |

Shortfall to price every game with the required markets at 7 games a day: **6.5 credits/day, about 156 credits through the cycle**; for everything including goals and saves: about 420 credits. The month balances under the plan (it never reaches into the reserve); what it cannot do inside the existing allowance is price every game.

## 5. Decision for the owner — trade-offs (nothing purchased or changed)

| Option | Effect |
|---|---|
| A. Run the plan as built (no purchase) | Month balances. About 3 games priced on a 7-game night; goals for at most one of them; saves only on light nights. Tickets form from fewer games. |
| B. Price more games, fewer markets | e.g. points only (1 credit/game): about 7 games priced, no shots ladder. Fewer legs per game, wider coverage. Requires choosing which market to drop. |
| C. Larger provider allowance | Full coverage at 7 games a night needs about 29 credits/day (about 700 for the rest of the cycle). That is a plan change; check the provider's current price list. Not made. |
| D. Accept running dry | The previous behaviour: full coverage until about Oct 19, then no paid pricing until Nov 1. |

Anytime-goal pricing is therefore *built and allocated where it fits* (leftover credits on lighter nights); it is not blocked, and it is not affordable for every game within the current allowance.

## 6. Hygiene

The credit position and today's plan are shown on Diagnostics (admin). A new key starts a new counter, which the planner reads from the first response. The key-rotation blocker is unchanged: after rotating, run `python3 deploy/verify_odds_key.py`.
