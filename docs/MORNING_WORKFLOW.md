# Morning workflow: the app populated from 8 AM, not from 5:15 PM

Owner requirement (2026-10-09): an initial update around 8 AM Eastern with today's games, updated statistics, available DraftKings prices and provisional recommendations; prices and recommendations
refreshed during the day and re-checked before any automatic ticket is recorded; morning picks must not use up the five model slots; a separate Tomorrow view with early prices, refreshed; and the
credits it takes, the minimum budget, and what the existing allowance can do instead. Nothing here purchases anything.

## Status of the requirements (read this first)

| Requirement | Status | Why |
|---|---|---|
| Morning update at about 8 AM ET (games, statistics, prices, provisional picks) | **UNMET until proven** — built, tested, deployed; not yet observed on a scheduled morning or on the hosted app | The first scheduled run is 08:00 ET on the first morning after the release. The watchdog records each morning's outcome; `Diagnostics → Product readiness` says NOT VERIFIED until a day shows a first look within 45 minutes of 08:00. |
| Several price refreshes a day, for every game | **UNMET under the free allowance** | The existing 500-credit allowance buys a morning look at part of the slate and a pregame price for fewer games; it cannot also refresh every game midday. Meeting it for every game needs about **60 credits a day** (below). |
| Re-check before recording an automatic ticket | Met in code and tests | A ticket is recorded only on a price retrieved inside its game's pregame window (105 minutes before puck drop) and re-judged on the clock at the moment of recording. |
| Morning picks do not consume the five slots | Met in code and tests | Morning prices can only produce *provisional* tickets: not recorded, no slot, no money. |
| Tomorrow view | Built; early moneylines shown with age and STALE marking; props availability stated | See "Tomorrow". Provider availability of tomorrow's props: not posted (below). |

## What DraftKings actually posts (provider availability, checked — not assumed)

The old rule (one capture per game, once the game was within ~5 hours, in practice ~105 minutes) was **our own policy**, not the bookmaker's. Evidence from the archived provider answers
(`operational/odds_archive/live`, every row with its `retrieved_at` and `commence_time`) and one live probe:

| When asked (before puck drop) | Answer | Source |
|---|---|---|
| 4 – 15 days ahead (2026-09-25 08:15 ET) | no player props; only alternate team totals | 33 archived calls |
| 32 – 35 hours ahead (2026-09-28 08:19 ET) | nothing posted | 2 archived calls |
| **23.6 hours ahead (2026-10-09, live probe, 4 markets)** | **nothing posted, cost 0 credits** | `x-requests-last: 0` |
| 8.9 hours ahead (2026-09-29 08:15 ET) | **shots (alternate) posted, 86 prices**; saves not posted | archived call |
| 7.1 – 8.1 hours ahead (2026-10-05 12:05 ET) | shots (66–82 prices), **points (26–32), anytime goals (36–37)** posted; saves 0–2 | 4 archived calls |
| 3 – 5 hours ahead | shots 70–120 prices, saves 2–4 (goalie confirmations land late) | ~50 archived calls |

So: shots are posted on the **morning of the game**; points and anytime goals were present by midday and have **not yet been observed at 8 AM**; saves appear in the last hours and only for some goalies;
**nothing is posted a day ahead** (tomorrow's player props do not exist yet at the bookmaker). A market the bookmaker has not posted **costs 0 credits** when asked for, so asking early is free until it posts.

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
| TOMORROW | 20:15 | — | one check whether tomorrow's player props are posted (first market: shots; a market not posted costs 0) |

`launchd` job `com.nhlengine.morning-update` runs `operational/morning_update.py` at 08:00: it records how fresh the 07:00 statistics are, makes the first moneyline pull, then runs one full trader cycle
(capture, provisional picks, publication). The 15-minute trader keeps looking afterwards. The older 08:15 league-wide props pull is **retired** under the credit plan (it would buy the same prices twice).
The watchdog warns from 08:45 and fails from 10:00 if no game was looked at.

## Morning picks versus recorded tickets

* A price is **recordable** only if the provider's quote time is inside the freshness limit **and** it was retrieved inside that game's pregame window. Without the second rule an 8 AM price (fresh by age for
  150 minutes) could have recorded all five tickets before lineups or later prices existed.
* A price older than that but from today's look (provider quote time ≤ 14 h, retrieved today) is **provisional**: it can be shown; it is never recorded and a personal log cannot add it.
* Before recording, every selected ticket is re-judged on the clock at that moment: each leg must be non-provisional, inside its limit, retrieved inside its pregame window, game not started
  (`daily_tickets.revalidate_before_recording`); a ticket that fails is held back with its reasons in Diagnostics.
* The existing exposure limits, wave slot reservation (two slots held for later puck drops), frozen recorded tickets and the personal/model separation are unchanged.

## Tomorrow

`Tomorrow` (Operate section) lists tomorrow's games with the model's win chance, the DraftKings moneyline where posted, and the player-prop availability line. Early moneylines come from the same
league-wide pull as today's (morning pull, display refresh, pre-game decision pulls), so they refresh without extra credits. An early price is **STALE** when it is older than 12 hours **or was retrieved
on an earlier Eastern day** (yesterday's capture is never treated as fresh), judged when the page is opened. Nothing on it can be recorded or added to a log.

## What it costs (`operational/service_plan.py`; published on Diagnostics)

Costs are the provider's: the league-wide moneyline call 1 credit; a per-game props call 1 credit per market that **returns** data; the events listing 0. Schedule on file: 6.8 games per game day
(15 days, Oct 9–23), up to 16 on the busiest night; 3 moneyline decision pulls a day.

| Service level | Looks per game | Credits per game | Per day (avg) | Per month | Free 500? | Smallest plan |
|---|---|---|---|---|---|---|
| **Minimum that meets the requirement** | morning (shots, points) → daytime (shots, points) → pregame (shots, points, goals, saves) | 8 | **60.4** (134 on a 16-game night) | **1,812** | no | 20K ($30/month) |
| Fuller | adds goals to the early looks and a last refresh before puck drop | 13 | 96.4 | 2,892 | no | 20K |

**Minimum budget: about 1,812 credits a month (60 a day).** The smallest provider tier above the free allowance is **20K for $30 a month** (20,000 credits, 11× the need). *Not purchased.*

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

## Verification still owed

1. **A scheduled morning on the hosted app**: first look within 45 minutes of 08:00 ET, the strip on Today ("Morning update done"), provisional options labelled and not addable, availability words per game.
2. **Whether DraftKings has points and goals posted at 8 AM** (shots were observed at 8:15 AM on 2026-09-29; points and goals first observed by 12:05 PM). The engine asks again hourly for anything not posted.
3. **Tomorrow's props**: the evening check will say whether DraftKings has posted any; so far nothing has been posted a day ahead.
