# Web Prototype Review Checklist

Open `dashboard_prototype/index.html` via `python3 -m http.server 8765` (see launch instructions in `UX_AUDIT_AND_REDESIGN_REPORT.md`) and walk through each question below. This prototype uses fabricated demo data for layout review only — none of it represents real players, real NHL predictions, real DraftKings odds, or real results.

## Orientation
- Can you identify today's slate immediately on the Today page?
- Can you tell within a few seconds which games are ready vs. waiting on data?
- Does the system-health strip communicate freshness without needing to click anything?

## Decision clarity
- Can you distinguish BET vs WATCH vs WAIT vs PASS at a glance, from badge color and text alone (not color alone)?
- Is Model Probability (Raw P) clearly visually distinct from Market Probability (No-Vig P)?
- Is Context-Adjusted P understandable on its own, or does it need the tooltip to make sense?
- Is Max Acceptable Price visible enough, or does it get lost among the other probability stages?
- When a market has "NO LIVE PRICE," is that unmistakable from an actual price (e.g. could it be confused with "+100" or "0%")?

## Model trust
- Are validated vs. partial vs. rejected vs. insufficient-data models unmistakable on the Goalies and Model Health pages?
- Does the redundant-leg warning on the Combinations page make its point clearly (that Goal+Point isn't two independent legs)?
- Do you understand why Goal+Point shows a naive probability that's *wrong*, and what the "validated joint P" actually represents instead?

## Density and hierarchy
- Is any page too dense — do you feel like you have to hunt for the one number that matters?
- Are the filters on the Player Props page obvious without instruction?
- Does the product feel trustworthy — professional and quantitative, or does anything read as casino-like/gimmicky?

## Content
- What information should be removed from the opportunity card?
- What information should be more prominent?
- Is the "Drivers"/"Risks" split on each card useful, or does it feel like generic filler?

## Separation of concerns
- Are research pages sufficiently separated from operational pages in the nav?
- Would you know, without being told, that the Research section is not where you'd go to place a bet?

## Structural / layout
- At 1440px, 1200px, and 900px widths, does any critical field fall off-screen or become unreadable?
- Does the drawer (Game Detail) feel natural, or would a full page be better?

## Final gut check
- If you were shown this dashboard cold, would you believe it was built by a serious quantitative shop?
- What's the single most confusing element on the page right now?
