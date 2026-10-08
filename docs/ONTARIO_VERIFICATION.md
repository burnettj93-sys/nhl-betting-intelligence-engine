# DraftKings Ontario: what is verified, what is not, and how to check

**Status (2026-10-08): prices and house rules are NOT verified for Ontario.** Every price in the product is a DraftKings quote from The Odds
API's US feed ("draftkings" bookmaker). Each page that shows a price says so. Nothing in the product is labelled an Ontario price.

## What was checked

* **Market menu.** `research/dk_ontario_market_registry.py` records which market families DraftKings Ontario lists (owner screenshots, 2026-09-29).
  That is evidence about *which markets exist*, not about lines or prices. Lines (for example saves ladders 24+ to 34+) and prices can differ from the US feed.
* **House rules for voids.** On 2026-10-08 a search for DraftKings Ontario's NHL player-prop rules returned only secondary sources and US-state house-rule PDFs. They describe
  standard practice (a prop on a player who does not play is voided; an "Early Exit" feature refunds a single, or voids the leg of a parlay, when an eligible
  player leaves a game early through injury and does not return). None is Ontario's own published rule, and the Early Exit behaviour is not implemented in
  this system's settlement (a player who played and left is graded on the stat he produced). So `VOID_RULES_VERIFIED` stays `False`: any ticket whose result
  depends on a void stays UNRESOLVED with its $10 open (see PAPER_SETTLEMENT_RULES.md). Nothing is guessed.

## The manual check (price, jurisdiction, source, time)

Any option shown on **Best Options** or **Players** has a **Check this on DraftKings Ontario** control. A person opens the exact selection in the Ontario app or site, types the American
price they see and where they saw it; the engine stores:

| Field | Meaning |
|---|---|
| selection | game, player, market, line, side (the same identity tickets use) |
| ontario_price | the price seen |
| us_price_shown | the US-feed price displayed at the time, for comparison |
| observed_at_utc | when it was seen (never in the future) |
| where_seen | free text, required (e.g. "DraftKings Ontario app") |
| recorded_at_utc / source | when and how the record reached the engine |

The record is evidence only: it is shown next to the option ("Ontario −140 seen 2:05 PM ET vs US feed −125") and never changes a stored ticket, a probability or a
price used for selection. The path is the same authenticated order queue as manual tickets (docs/MANUAL_ORDERS.md).

## What would make Ontario verified

1. A person confirms the Ontario house rules for did-not-play and early-exit cases and corrects `docs/PAPER_SETTLEMENT_RULES.md`, then sets `VOID_RULES_VERIFIED = True`.
2. Ontario prices are available from a source the system may use (a licensed feed or repeated manual checks that show the US feed tracks Ontario within a stated tolerance).
   Until then the US-feed label stays on every price, and same-game parlays remain off (no joint model, no real combined quote, no verified settlement).
