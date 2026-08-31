# Demo Walkthrough — NHL Betting Intelligence Engine

Same-Day Demo Experience sprint (2026-08-31). A ~5-minute click-by-click
path through the demo. Everything priced below the header banner is
**SIMULATED MARKET (DEMO ONLY)** — never DraftKings, never a live book.
Every model probability shown is the real, frozen production engine's
own output for real NHL player identities.

## Before you start

Launch the dashboard (`streamlit run dashboard/app.py` or the project's
existing launcher) and open the **Today** page. That is the intended
landing page for this demo.

## The 5-minute path

1. **Today page — orient.** Point out the System Health chips at the
   top (real, live status of the actual data pipelines) and the
   `DEMO MODE` banner directly below it. Say: *"Everything above this
   banner is real, live engine status. Everything below it is today's
   simulated game night, built on the same real, frozen models."*

2. **Today's Slate.** Scroll to section 1. Six simulated matchups, each
   showing its strongest opportunity inline. Click **"EDM-COL Hub"**
   (or any team button) on one of the game cards.
   → Lands on the **Team Intelligence Hub** for that team.

3. **Team Hub — BETS tab.** This is the P0 requirement: every eligible
   bet connected to the team, across every market family (SOG, Goals,
   Assists, Points, Blocks, Goalie Saves), with filters and sort. Show
   the filter row, then click a player's bet row.
   → Lands on **Player Intelligence** for that player.

4. **Player Intelligence.** Show the Best Available Market card, then
   open the **All Eligible Bets** tab to show every validated threshold
   for that player (e.g. SOG 2+/3+/4+/5+, not just one). Click **"Open
   {team} — Team Hub"** or **"Open Game Detail"** to show the
   clickthrough back out.

5. **Back to Today — Top Conviction.** Return to Today (browser back or
   the search bar). Scroll to section 2, **Top Conviction**. Say:
   *"These are the highest-confidence model edges from today's slate —
   never 'sure things' or 'locks,' because that's not how this engine
   talks about probability. Notice these aren't just the highest
   probabilities — they're the best combination of probability and
   price value."* Click a card to jump to that player.

6. **High-Confidence Combos.** Scroll to section 3. Show a validated
   2-leg combo (same player, two markets with a real, frozen
   correlation) and open the "Research / demo exploration" expander to
   show a combo that was found but correctly excluded for having no
   validated dependence — say: *"We never assume independence just
   because it would make a bigger number."*

7. **Game Detail.** From Today's Slate, click **"Game Detail"** on any
   matchup. Walk the tabs: PREVIEW (win model, team SOG, this game's Top
   Conviction), BETS (every eligible bet for the game + same-game
   combinations), PLAYER PROPS, STATS (availability), BETTING TRENDS
   (simulated line movement), MODEL (readiness/freshness disclosure).

8. **Model Learning.** Open from the Today page's Model Health section
   or the sidebar. Show the **"WAITING FOR 2026-27 RESULTS"** state and
   read the explanatory caption: daily re-scoring never auto-changes
   production. Say: *"This page is empty right now because the season
   hasn't produced real results yet — that's honest, not broken. Once
   real games are settled, this populates itself automatically."*

## Fallback routes

- If a team's BETS tab is empty for a filter combination, switch the
  Decision filter to "ALL" — every team has at least a few RESEARCH_ONLY
  rows even when nothing is actionable.
- If Top Conviction is empty on a given run (it never pads to force a
  result), fall back to section 4, **Best Player Props**, which always
  reflects the full slate.
- If Game Detail's BETS tab looks thin for one matchup, pick a different
  one of the six from Today's Slate — Team SOG/Moneyline are
  intentionally not priced this sprint (no live projection engine
  wired), so some matchups lean more on player props than others.
- Search (top of every page) is the universal fallback: type a player,
  team, or game name and it routes correctly from anywhere.

## What NOT to do live

- Do not click into Team SOG or Moneyline expecting a priced bet card —
  both are disclosed, deliberate scope limitations this sprint (shown
  as real historical context only).
- Do not describe Top Conviction picks as "locks" or "sure things" —
  the UI itself never uses that language; keep the talk track consistent
  with it.
