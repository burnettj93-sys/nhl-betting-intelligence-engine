# Odds-plan decision (2026-10-17) — nothing is purchased by the engine or by me

## The decision

| | Free (today) | **Recommended: The Odds API 20K** |
|---|---|---|
| Price (provider pricing page, fetched 2026-10-14 and 2026-10-17) | $0, 500 credits/month | **$30/month**, 20,000 credits/month, all bookmakers and markets; billed on the day you subscribe |
| Credits per day (what the planner may spend) | about 11 | about **666** (19,980 usable / 30) |
| Credits the full plan needs | 29 (4 games) to 102 (16 games) | **the same: 30 / 48 / 102** a day for 4 / 7 / 16 games (about **1,440 a month** at 7 games a day; worst case with two extra refreshes per game 63 / 102 / 219 a day, up to **about 6,600 a month** on 16 games every day) |
| Headroom | none | **3x or more in the worst case** (the 100K tier at $59 is not needed) |

Cost model (verified from archived provider responses, `docs/validation/credit_cost_audit.json`): one credit per market that returns data, per game; the league-wide moneyline call is 1 credit for all games; a market DraftKings has not posted costs 0.

## What it covers, from the engine's own allocator (`credit_planner.allocate`, run at the 20K budget)

| Games on the day | Priced pregame | Morning shots look | Anytime goals | Goalie-saves prices | Points |
|---|---|---|---|---|---|
| 4 | 4 of 4 | 4 | 4 | 4 | 4 |
| 7 | 7 of 7 | 7 | 7 | 7 | 7 |
| 16 | 16 of 16 | 16 | 16 | 16 | 16 |

On the free plan the same allocator prices 2 of 16 games, 0 goals, 0 saves.

## Refresh frequency (what actually triggers a purchase)

* **Moneylines (all games, 1 credit per pull):** a T-35 decision pull per start cluster (never bought late), the morning pull, a second planned display refresh, and up to **6 recovery refreshes a day** (new: `UI_RECOVERY`) so that after a sleep or lost connection the still-upcoming games are re-priced; the display refresh runs whenever the newest moneyline is older than its target (150 minutes; 75 minutes when the next game is within 4 hours), as long as the day's moneyline allowance lasts.
* **Player props:** a morning look from 8:00 AM ET (shots ladder, every game); the **pregame capture** about 105 minutes before puck drop, synchronised across a wave of games so prices can be compared at one time, carrying **shots + anytime goals + goalie saves (+ points)** in one call; a refresh of the shots ladder whenever a price is within 45 minutes of its freshness limit (usually one in the last 2 hours). A window that closes without its capture is reported as MISSED_WINDOW on the Slate Board and by the watchdog and is **never bought late** (a price taken after puck drop is not a pregame price).
* **Tomorrow:** one availability check each evening (free when nothing is posted).

## Goals and saves prices are collected independently of starter confirmation (change in this branch)

Before: a saves line was bought only for a confirmed starter, at most 3 games a day, so saves were never priced (no permitted source confirms starters). Now the planner funds saves prices for every priced game after shots and goals; games with a confirmed starter are served first; the saves line rides on the pregame capture (one extra credit). **Confirmation is unchanged as a requirement for recommending or recording a saves leg:** the starter-certainty gate in `real_slate_adapter.goalie_saves_candidate_legs` is untouched, the Slate Board shows the saves line as conditional ("no saves selection is made and none goes in the slip"), and the builder hides unconfirmed saves lines. No selection threshold changed.

## What I need from you

1. Approve (or decline) the 20K subscription; I cannot buy it. If approved: tell me the subscription date (the quota resets on the billing day; set `NHL_ENGINE_ODDS_RESET_DAY` in `.env`), generate the new key, revoke the old one, then I run `python3 deploy/verify_odds_key.py` and raise the plan from "free" to the tier's allowance (docs/ODDS_BUDGET_CONFIGURATIONS.md, configuration B).
2. Until then the free plan prices 2 games pregame on a 14-game day and no goals or saves prices; the Slate Board says why for every game ("budget blocked").
