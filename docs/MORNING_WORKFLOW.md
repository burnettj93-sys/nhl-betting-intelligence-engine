# Morning workflow: the app populated from 8 AM, not from 5:15 PM

Owner requirement (2026-10-09): an initial update around 8 AM Eastern with today's games, updated statistics, available DraftKings prices and provisional recommendations; prices and recommendations
refreshed during the day and re-checked before any automatic ticket is recorded; morning picks must not use up the five model slots; a separate Tomorrow view with early prices, refreshed; and the
credits it takes, the minimum budget, and what the existing allowance can do instead. Nothing here purchases anything.

## Status of the requirements (read this first)

| Requirement | Status | Why |
|---|---|---|
| Morning update at about 8 AM ET (games, statistics, prices, provisional picks) | **UNMET until proven** — built, tested, deployed; not yet observed on a scheduled morning or on the hosted app | The first scheduled run is 08:00 ET on the first morning after the release. The watchdog records each morning's outcome; `Diagnostics → Product readiness` says NOT VERIFIED until a day shows a first look within 45 minutes of 08:00. |
| Several price refreshes a day, for every game | **UNMET under the free allowance** | The existing 500-credit allowance buys a morning look at part of the slate and a pregame price for fewer games; it cannot also refresh every game midday. Meeting it for every game needs about **60 credits a day** (below). |
| Re-check before recording an automatic ticket | Met in code and tests | Every selected ticket is re-judged on the clock at the moment of recording: each leg's price must still be inside the freshness limit, the game not started. |
| Morning options usable, not just displayed | Met in code and tests | A morning price that is still fresh can be added to a personal log (once the write credential exists) and recorded on an automatic ticket. An aged price is shown as provisional and cannot be used. |
| Morning picks must not use up the five slots | Met by a cap, not a blanket rule | At most 2 automatic tickets a day may rest on early (morning/midday) prices; see "Why a cap" below. |
| Tomorrow view | Built; early moneylines shown with age and STALE marking; every availability check listed with its time | See "Tomorrow". One probe found nothing posted a day ahead; that is one observation, and the checks continue. |

## What DraftKings had posted when the engine asked (observations, not conclusions)

The old rule (one capture per game, once the game was within ~5 hours, in practice ~105 minutes) was **our own policy**, not the bookmaker's. These are what the archived provider answers
(`operational/odds_archive/live`, every row with its `retrieved_at` and `commence_time`) and one live probe showed **at those times** — a handful of days, not a finding about how DraftKings always behaves:

| When asked (before puck drop) | Answer | Source |
|---|---|---|
| 4 – 15 days ahead (2026-09-25 08:15 ET) | no player props; only alternate team totals | 33 archived calls |
| 32 – 35 hours ahead (2026-09-28 08:19 ET) | nothing posted | 2 archived calls |
| **23.6 hours ahead (2026-10-09, live probe, 4 markets)** | **nothing posted, cost 0 credits** | `x-requests-last: 0` |
| 8.9 hours ahead (2026-09-29 08:15 ET) | **shots (alternate) posted, 86 prices**; saves not posted | archived call |
| 7.1 – 8.1 hours ahead (2026-10-05 12:05 ET) | shots (66–82 prices), **points (26–32), anytime goals (36–37)** posted; saves 0–2 | 4 archived calls |
| 3 – 5 hours ahead | shots 70–120 prices, saves 2–4 (goalie confirmations land late) | ~50 archived calls |

What these observations support, and no more: on 2026-09-29 shots were posted at 8:15 AM for a game 8.9 hours away; on 2026-10-05 shots, points and goals were posted at 12:05 PM for games 7–8 hours away;
saves were mostly absent until the last hours; on 2026-09-25, 09-28 and 10-09 nothing was posted for games more than 23 hours away. Points and goals at 8 AM, and tomorrow's props at any hour, have
**not been observed** either way beyond those dates. The engine therefore records every check (see "Tomorrow") rather than assuming. A market the bookmaker has not posted **costs 0 credits** when asked for.

## How the engine tells "not posted" from "not fetched" from "budget"

`operational/price_availability.py` records, per game and market, the newest answer and its time (state file plus an append-only history):

| Status | Meaning |
|---|---|
| POSTED | the provider returned the market (n prices, last updated at T) |
| NOT_POSTED | we asked, and the bookmaker has not posted it (asked again after 60 minutes) |
| NOT_FETCHED | we have not asked yet (the slot has not opened) |
| BUDGET_BLOCKED | we did not ask because today's credit plan did not cover it — whether DraftKings has posted it is then **unknown** |
| FETCH_ERROR | we asked and the request failed |

A plan statement never overwrites a real answer from the provider. Today, Best Options and Tomorrow show these words per game and market, and Diagnostics carries the full table.

## The schedule (`operational/capture_schedule.py`; Eastern time)

| Slot | Opens | Closes | What |
|---|---|---|---|
| MORNING | 08:00 | 3 h before puck drop | first look at every game the plan covers: provisional options and provisional tickets; also the day's first league-wide moneyline pull (1 credit; it carries tomorrow's early lines) |
| MIDDAY | 12:30 | 3 h before puck drop | a second look, only with leftover credits; if the morning look was missed it is made late as the *first* look |
| PREGAME | 105 min before puck drop | puck drop | **the price a ticket is recorded on**; fresh through puck drop |
| TOMORROW | 08:10 and 20:15 | — | a check whether tomorrow's player props are posted (earliest game: all four markets; the others: shots; a market not posted costs 0); each answer is stored with its time |

`launchd` job `com.nhlengine.morning-update` runs `operational/morning_update.py` at 08:00: it records how fresh the 07:00 statistics are, makes the first moneyline pull, then runs one full trader cycle
(capture, provisional picks, publication). The 15-minute trader keeps looking afterwards. The older 08:15 league-wide props pull is **retired** under the credit plan (it would buy the same prices twice).
The watchdog warns from 08:45 and fails from 10:00 if no game was looked at.

## Morning options, personal logs and automatic tickets

Whether a price may be used depends on its **actual age**, not on which slot fetched it:

* **Fresh** (the provider's own quote time within 150 minutes, 100 when the game is under two hours away): it can be shown, added to a personal log, and used on an automatic ticket, at any hour.
  An option built on a fresh morning price shows "Morning price", is addable, and is re-checked against the current price when the order is processed (price moved → asks for acceptance; stale or
  started → no bet and no side effect). Market-specific safeguards are unchanged: a saves leg still needs a confirmed starter, a player under the 40-game floor is not priced, and the roster/team checks
  run on every add.
* **Aged** (older than that but from today's look, quote time ≤ 14 hours, retrieved today): shown as **Provisional**; nothing can be added or recorded on it until it is refreshed.
* **Older, missing, malformed, future or from an earlier day**: not shown as an option.

### Why a cap on early automatic tickets, instead of a pregame-only rule

An earlier version of this change recorded an automatic ticket only on a price retrieved in the last 105 minutes before puck drop. That was too blunt: it made the app show bets all morning and become
actionable only near the games, which is not the requirement. It is replaced by this:

* An automatic ticket may rest on a fresh early price (retrieved more than 3 hours before puck drop), but **at most 2 tickets a day may**. The other three slots are left for prices closer to the games.
* Every ticket, early or not, is **re-judged on the clock at the moment of recording** (price inside its limit, game not started), and the existing wave reservation (two slots held for later puck drops),
  per-leg / per-player / per-game exposure limits, frozen entries and the personal/model separation are unchanged.

Justification, and its limits. A recorded ticket is frozen, so a ticket taken at 8:05 AM cannot be improved by later information, and the exposure limits (a leg on at most 2 tickets, a player on at most 2, a
game on at most 3) are spent by whatever is recorded first. The 2026-10-08 selector audit (`docs/SELECTOR_AUDIT.md` §2, §5) showed that the day's best tickets by estimated hit chance used up those limits
and that early recording is the one way a better later ticket can be shut out. Against that, nothing new arrives between a morning price and the afternoon except price moves: there is no lineup, injury or
confirmed-starter feed here (they are blocked sources), so waiting adds less information than it would elsewhere. **The cap of 2 is a judgement, not a measured optimum**: there is no evidence yet that early
tickets do better or worse (9 tickets exist). Each recorded ticket carries its prices' retrieval times, so early and late tickets can be compared once there are enough of them; the constant is
`daily_tickets.EARLY_TICKET_CAP`.

## Tomorrow

`Tomorrow` (Operate section) lists tomorrow's games with the model's win chance, the DraftKings moneyline where posted, and the player-prop availability. The engine asks DraftKings **twice a day**
(08:10 and 20:15 ET) whether tomorrow's player props are posted: the earliest game is asked about all four markets (shots, points, anytime goals, saves), the others about shots; an answer with nothing in
it costs 0 credits. **Every check is stored as an observation** — when it was asked, for which game, how many hours before puck drop, and whether the market was posted — and the page lists them. It says
what DraftKings had posted at those times; it is not a statement about what DraftKings does in general. Early moneylines come from the same league-wide pull as today's, so they refresh without extra
credits. An early price is **STALE** when it is older than 12 hours **or was retrieved on an earlier Eastern day**, judged when the page is opened. Nothing on it can be recorded or added to a log.

## What it costs (`operational/service_plan.py`; published on Diagnostics)

Costs are the provider's, and were **checked against what was actually charged** (`deploy/audit_credit_costs.py`, `docs/validation/credit_cost_audit.json`): of 1,331 archived provider calls, all 1,105 events listings were charged 0; all 109 league-wide moneyline calls 1; and all 117 per-game props calls were charged exactly the number of markets that returned data (193 credits). The account counter for this cycle reads 218, and the charges in the response headers sum to 218 — every credit accounted for (25 consecutive-call pairs on 2026-09-25 disagree only because parallel calls answered out of order). The provider's documentation states the same rule ("unique markets returned × regions"; the events endpoint "does not count against the usage quota"). Schedule on file: 6.8 games per game day
(15 days, Oct 9–23), up to 16 on the busiest night; 3 moneyline decision pulls a day.

| Service level | Looks per game | Credits per game | Per day (avg) | Per month | Free 500? | Smallest plan |
|---|---|---|---|---|---|---|
| **Minimum that meets the requirement** | morning (shots, points) → daytime (shots, points) → pregame (shots, points, goals, saves) | 8 | **60.4** (134 on a 16-game night) | **1,812** | no | 20K ($30/month) |
| Fuller | adds goals to the early looks and a last refresh before puck drop | 13 | 96.4 | 2,892 | no | 20K |

**Minimum budget: about 1,812 credits a month (60 a day), assuming every market is posted at every look, which makes it an upper bound for those looks.** Provider plans (checked on the-odds-api.com, 2026-10-09): Starter free 500 credits; **20K $30/month, 20,000 credits**; 100K $59; 5M $119; 15M $249. **The smallest plan that meets the requested coverage (a morning, a daytime and a pregame look at every game, with moneyline and tomorrow's checks) is 20K at $30 a month**: it covers the 60-credit day 11 times over and even a month of 134-credit 16-game nights (4,020). The free 500 credits cover under a third of one month of it. *Not purchased; nothing has been upgraded.*

### Reduced service possible under the existing allowance (does NOT meet the requirement)

Today's budget is 11.43 credits (282 left, 22 days to the 1st; reset day assumed, owner verification pending). After the 3 moneyline decision pulls and the display pull, the planner funds the
pregame price for the first two games (the least that makes a cross-game ticket possible), then a morning look (shots) at as many games as credits allow, then pregame prices for the rest:

| Games that day | Morning look at | Pregame price for | Midday look |
|---|---|---|---|
| 4 | 3 (75%) | 2 (50%) | none |
| 7 | 3 (43%) | 2 (29%) | none |
| 16 | 3 (19%) | 2 (12%) | none |

This trades pregame coverage (a 4-game day used to price 3 games before puck drop; now 2) for morning visibility of 3. `NHL_ENGINE_MORNING_LOOK=off` in `.env` restores the previous allocation (no morning look,
the credits all go to the pregame price). Anytime goals and saves are funded only after those, so they are rarely bought on the free allowance. The month does not balance even so: the trailing seven-day burn
before this change was 27 a day against a 12 a day pace.

## The release day (2026-10-09, afternoon) — what was and was not observed

* Deployed at about 2:40 PM ET with the 08:00 job installed (`com.nhlengine.morning-update`, 15 jobs loaded). The day's credit plan had been saved that morning under the old rules and already committed its whole
  11.43 credits, so the morning look was funded with **nothing** (a guard stops a new class from pushing a saved plan over budget). All four of today's games were recorded **BUDGET_BLOCKED** (not "not posted").
* That exposed a defect, fixed the same afternoon: the check called it "morning update done: 4 of 4 games looked at" although DraftKings had been asked about none. A morning with no real answer is now
  `BUDGET_ONLY` (watchdog WARN; Today says "ran, but today's credit plan left nothing for an early look"), and only a morning with at least one provider answer can count as done.
* Hosted app (Chrome, desktop): Today shows the morning strip, Best Options shows the per-game availability table when empty, **Tomorrow** shows 14 games for 2026-10-10 with early moneylines (13 of 14 not posted; one
  14.9 hours old, marked STALE), Diagnostics shows the minimum budget and the reduced-service table.
* A live probe of tomorrow's first game (23.6 hours ahead, four markets) returned nothing and cost 0 credits; one availability row per probe is in the credit ledger (`AVAILABILITY_PROBE`).
* **Not observed**: a scheduled 08:00 run; points and goals at 8 AM; any provisional option or ticket on the hosted app (none could exist: no early price was bought today); the Tomorrow evening check
  (due 8:15 PM ET today). On 2026-10-10 the budget (about 11.9 credits for 14 games) buys a shots look at 3 games.

## Verification still owed

1. **A scheduled morning on the hosted app**: first look within 45 minutes of 08:00 ET, the strip on Today ("Morning update done"), provisional options labelled and not addable, availability words per game.
2. **Whether DraftKings has points and goals posted at 8 AM** (shots were observed at 8:15 AM on 2026-09-29; points and goals first observed by 12:05 PM). The engine asks again hourly for anything not posted.
3. **Tomorrow's props**: the 08:10 and 20:15 checks add observations each day; so far (three dates) nothing had been posted more than 23 hours ahead.
