# Odds API budget: three quantified configurations (2026-10-08)

Nothing here purchases or upgrades anything. Every figure uses costs observed in the provider's own response headers (`x-requests-last`) and the real schedule on file.

## Inputs, verified

| Item | Value | Evidence |
|---|---|---|
| Cost of the all-games moneyline call | 1 credit per call, regardless of how many games it returns | 59 archived calls, 59 credits |
| Cost of a per-game props call | 1 credit per market that returns data (shots 1, points 1, anytime goals 1, saves 1) | archived calls: 2 markets → 2, 5-market probe returning 4 markets → 3–4; provider docs: `cost = markets returned × regions` |
| Events listing | 0 credits | 435 archived calls, 0 credits; provider docs |
| Allowance today | 500 credits per cycle, 288 remaining at 23:05 UTC on 2026-10-08, 20-credit reserve kept | `x-requests-remaining` / `-used` |
| **Reset date** | **Not stated by the provider** (its docs and pricing page say only "usage credits remaining until the quota resets"; billing "happens … on the same day of the month that the subscription started" for paid plans). **Observed once:** the counter fell from 182 used to 0 between 2026-09-30 23:55:04Z and 2026-10-01 00:04:24Z, i.e. a calendar-month reset at 00:00 UTC on the 1st for this free plan. One observation is not confirmation: the account dashboard (login required) is the authority. | the archive's headers; https://the-odds-api.com pricing and v4 guide |
| Paid tiers (provider's pricing page) | Starter free 500 credits/month · **20K: $30/month, 20,000 credits** · 100K: $59 · 5M: $119 · 15M: $249 | https://the-odds-api.com |
| Games still to price this cycle | 163 over Oct 9–31 (100 on file through Oct 22: days range from 2 to 16 games; 7 a day assumed after) | schedule in `nhl.db` |
| Moneyline decision pulls | one per start cluster (a cluster = start times within 5 minutes); observed 2 on Oct 7, 5 on Oct 8; 3 a day assumed | `moneyline_pregame_state.json` |

## The three configurations

"Priority markets" = moneyline (all games, via the T-35 pull) + shots (alternate) + points. "Required" adds anytime goals and saves (saves only when the starter is confirmed; counted for every game here to be conservative).

| | A. Existing allowance | B. All games, priority markets, actionable captures | C. Full required markets, repeated pregame captures |
|---|---|---|---|
| Game coverage (props) | **78 of 163 games (48%)**; 3 priced on a 14–16 game night, 2–4 on light nights | **163 of 163** | **163 of 163** |
| Markets per priced game | shots + points; goals for 18 of the 78 (leftover credits) | shots + points | shots + points + goals + saves |
| Captures per game | 1, about 105 minutes before puck drop (fresh through puck drop) | 1 at that time | 3 (≈ 4 h, 105 min and 45 min before) |
| Moneyline | T-35 decision pull for every cluster (all games); 1 display refresh a day | same + 2 display refreshes | same + display refresh every ~90 min on game days |
| Credits/day | 10–12 (the allowance divided over the days left) | 19.2 average (37 on the busiest night) | 92 average (199 on the busiest night) |
| Credits Oct 9–31 | ≈ 266 of 266 usable | 441 | 2,117 |
| Fits the existing 500? | yes (by construction) | **no — short 175 credits** (needs 461 incl. reserve vs 286) | **no — short 1,851** |
| Month total incl. the 212 already used | 500 | 673 | 2,349 |
| What would cover it | nothing needed | a larger allowance: the provider's 20K tier ($30/month) is the smallest above the free one | the same 20K tier (2,349 of 20,000, 12%) |

**An in-allowance alternative (A2), the owner's choice:** `NHL_ENGINE_PROP_MARKETS=player_points` in `.env` buys one market (1 credit) per game instead of two, so the same allowance prices **132 of 163 games (81%)** (it ends the cycle at the 20-credit reserve, i.e. it fits). The cost is the shots ladder: no shots legs. Default stays shots + points until the owner chooses; the selection is implemented and tested (`operational/credit_planner.py::prop_markets`).

## What limited coverage does to the five-ticket target

The ticket rules cap one ticket at two cross-game legs and each game at three tickets (`research/real_market_parlay/engine.py`), so with *k* priced games the most tickets possible in a day is `floor(3k / 2)`, capped at 5:

| Priced games on the day | 1 | 2 | 3 | 4+ |
|---|---|---|---|---|
| Maximum tickets possible | 0 (no cross-game pair) | 3 | 4 | 5 |

Observed: 2026-10-07, 3 games priced → 3 tickets (2 distinct games); 2026-10-08, 7 games priced → 5 automatic tickets (5 distinct games). Under A, 13 of the 23 remaining nights have at least 4 priced games and so *can* reach 5; the other 10 are capped at 3–4 by coverage before any edge test, and every night is further limited by how few legs beat their price (9% of priced legs on 2026-10-08). Moneyline legs are priced for every game by the T-35 pull and give extra cross-game partners, which softens but does not remove the cap. Under A2, 81% of games are priced and the cap is rarely the binding constraint, at the cost of the shots markets. Under B and C every night with at least four games can reach five.

## Duplicate and unnecessary calls: audit and what changed

Done this session (all in `operational/credit_planner.py` and the jobs): shots were bought by two jobs for the same game → bought once; the 3–4.5 h first sweep bought prices that would be stale at the decision → retired; up to four captures per game as a price neared its freshness limit → one capture in the actionable window, a second only with leftover credits; every job borrowed up to 3× the even pace on its own → one daily plan; saves bought for games with no confirmed starter → bought only for confirmed ones; the display-only moneyline refresh ran every 75–150 minutes → capped by the plan. Found and fixed during QA: the T-35 decision pull was still on the old soft daily budget and was **deferred for the 7 PM cluster (6 games) on 2026-10-08** (44 credits counted against a 33.6 soft limit, including last night's spend after 00:00 UTC), so those six games had no moneyline decision quote today; the pull now obeys only the hard reserve and the 23:30 UTC cluster was captured normally at 22:50 UTC.
Remaining, by design: one T-35 pull per start cluster (up to 5 a night on spread-out slates) because the moneyline decision policy only accepts a quote from the 10 minutes before its anchor; merging clusters would change that policy. The display refresh is capped at one a day, which means the Games page price can approach its freshness limit between decision pulls (it shows as stale or budget-limited on Data Status rather than hiding it).
