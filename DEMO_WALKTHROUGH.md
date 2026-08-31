# Demo Walkthrough — NHL Betting Intelligence Engine

Updated for the Live DK / Paper Bankroll completion sprint (2026-08-31).
A ~6-minute click-by-click path through the demo. Everything labeled
**SIMULATED — DEMO ONLY** is a deterministic simulated price; everything
labeled **LIVE — DRAFTKINGS** is a real DraftKings price captured via a
real, credit-metered Odds API probe. Every model probability shown is
the real, frozen production engine's own output for real NHL player
identities.

## Before you start

Launch the dashboard (`streamlit run dashboard/app.py` or the project's
existing launcher) and open the **Today** page. That is the intended
landing page for this demo.

## The 6-minute path (Today → Live Model Edges → Top Conviction → High-Confidence Combo → Team Hub → All Eligible Bets → Bet Detail → Paper Performance → Model Learning)

1. **Today page — orient.** Point out the System Health chips (real,
   live status of the actual data pipelines) and the `DEMO MODE` banner.
   Say: *"Everything above this banner is real, live engine status.
   Everything below it is today's simulated game night, built on the
   same real, frozen models."*

2. **Live Model Edges.** If present (it is, right now — two real
   DraftKings-priced games), point out the **LIVE — DRAFTKINGS** label
   and the captured timestamp. Say: *"This number is not simulated.
   This is an actual DraftKings market, captured by the engine, compared
   against the frozen model's own fair probability."* Then show the
   ⚠ staleness disclosure on a WAIT row: *"The model sees a real edge
   here, but the Elo rating behind it predates this game by months —
   so the engine refuses to call it a bet. That refusal is the honest
   answer, not a bug."*

3. **Today's Slate.** Scroll to section 1. Click a team button on any
   game card. → Lands on the **Team Intelligence Hub** for that team.

4. **Team Hub — BETS tab.** Every eligible bet connected to the team,
   across every market family, with filters and sort. Click a player's
   bet row. → Lands on **Player Intelligence** for that player.

5. **Player Intelligence — All Eligible Bets.** Every validated
   threshold for that player (e.g. SOG 2+/3+/4+/5+, not just one).

6. **Back to Today — Top Conviction.** Scroll to section 2. Say:
   *"Never 'sure things' or 'locks' — these are the best combination of
   probability, price value, AND how mature the underlying model is.
   A market backed by a fully validated model outranks an equally
   strong empirical-baseline market, all else equal."*

7. **High-Confidence Combos.** Scroll to section 3. Explain the three
   classes: *"HIGH-CONFIDENCE means every leg is individually a real
   favorite with real value — not just a mathematically valid parlay.
   A 6% longshot combo, even with perfectly real joint-dependence math
   behind it, lands in Value Combinations, never here."* If none qualify
   today, that's expected — show Value Combinations instead, and read
   one leg's numbers aloud.

8. **Game Detail.** From Today's Slate, click **"Game Detail"** on any
   matchup. Walk PREVIEW → BETS → PLAYER PROPS → STATS → BETTING TRENDS
   → MODEL.

9. **Paper Performance.** Open from the sidebar or Today's Model Health
   section. Show both tabs: *"Real-Market Paper only uses real
   DraftKings prices — right now it's empty because every live edge we
   found was flagged stale, so honestly, nothing should have been bet
   yet."* Switch to Demo Paper: *"Six $10 paper bets exist here, one per
   BET-grade opportunity on tonight's simulated slate — all still
   pending, because no game has actually been played. That's the exact
   state this should be in before the season starts."*

10. **Model Learning.** Show **"WAITING FOR 2026-27 RESULTS"** and read
    the explanatory caption: daily re-scoring — including paper
    performance — never auto-changes production.

## Fallback routes

- If Live Model Edges shows nothing, that's a real, possible state
  (DraftKings hasn't posted a moneyline for any tracked event right
  now) — fall back to Today's Slate and continue from step 3.
- If a team's BETS tab is empty for a filter combination, switch the
  Decision filter to "ALL".
- If Top Conviction is empty on a given run (it never pads to force a
  result), fall back to section 4, **Best Player Props**.
- If no High-Confidence Combo exists, open the Value Combinations
  expander instead — the underlying math is still real.
- If Game Detail's BETS tab looks thin for one matchup, pick a different
  one of the six from Today's Slate.
- Search (top of every page) is the universal fallback.

## What NOT to do live

- Do not click into Team SOG or Moneyline (outside the Live Model Edges
  section) expecting a priced bet card — both are disclosed, deliberate
  scope limitations (shown as real historical context only).
- Do not describe Top Conviction picks, or any Live Model Edges row, as
  "locks" or "sure things" — the UI itself never uses that language.
- Do not present a Live Model Edges row marked WAIT as if it were
  actionable — the staleness disclosure is the point being demonstrated,
  not a defect to explain away.
- Do not place a real bet from this dashboard — there is no such control,
  and Paper Performance is explicitly theoretical.
