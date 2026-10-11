# Anchor tickets — how the engine now builds parlays (2026-10-11)

This replaces the selector's earlier "every leg must beat its own price" rule. It is implemented in `research/real_market_parlay/anchor.py` and switched on through the policy object
(`research/real_market_parlay/policy.py`, `PROPOSED`, `mode="anchor"`). It is a **proposal awaiting the owner's approval**: automatic recording stays paused, and the board shows what it
would record as *proposed, not recorded*.

## What a ticket is

* 2 to 5 legs, every leg from a different game (two legs from one game would need a joint price DraftKings has not quoted and this code cannot honestly derive).
* Legs are **well supported**: 2+ (or 3+) shots on goal for a player who reaches the line in most of his last 30 games, from the real DraftKings alternate ladder; or a moneyline at the market's own
  no-vig chance when the Elo model does not disagree by more than five points. Points and anytime-goal legs are not used.
* Combined price +100 or better (product of each leg's real DraftKings price; an estimate, never a quoted parlay price).
* Ranked by estimated hit chance. Up to five tickets; a slot with no worthwhile ticket stays empty.

## Where each leg's number comes from

| Leg | Estimate | Why it is the honest number |
|---|---|---|
| 2+ shots | the player's last-30-game frequency (shrunk toward the league rate by 8 pseudo-games), never more than the model + 10 points | The calibrated skater model under-predicted consistent shooters on held-out 2023, 2024 and 2025 (its 0.70–0.80 estimates happened 5–7 points more often); the last-30 frequency was calibrated in every season (`docs/validation/anchor_calibration.json`, reproduced by `deploy/anchor_calibration.py`). |
| 3+ shots and higher | the same, capped at 70% | The record was calibrated at 0.60–0.70 only; above that there are too few player-games and 2024 over-stated it. |
| Moneyline | the market's no-vig probability | No model here beats a DraftKings moneyline. Such a leg has no value of its own (it costs about the margin); it can only complete a ticket's price. |

Every card shows the estimate beside the model's number, the player's record (k of n games) and the probability the price implies.

## What "defensible value" means here

A ticket is kept only if all of these hold at the real prices:

1. estimated value (hit chance × combined decimal − 1) of at least +5%;
2. at least +3% with every leg's chance lowered three points;
3. not negative if the true chance is halfway between the player's record and the price-implied chance (the market sees the opponent, lineup and news; this test does not need the market to be wrong);
4. estimated hit chance of at least 30%;
5. no leg gives up more than about a bookmaker's margin on its own price (−5%), so a filler leg cannot be carried by one great leg;
6. a longer ticket is kept only if no shorter ticket from its own legs already passes.

Exposure (kept from the postmortem): a player or leg on at most one ticket a day, a game on at most three.

## What is not shown by this evidence

* The records are calibrated on all eligible player-games, not on the subset where a sportsbook prices a leg cheaper than the record implies. If DraftKings is right where it disagrees with the record,
  the value shown here is not real. The forward shadow log now stores each priced leg's record, model and market chance and the proposed tickets, so this can be scored against official results.
* Prices are DraftKings US-feed quotes, not verified for Ontario, and DraftKings' own parlay pricing and void rules are unverified.

## Coverage

Per-game player prices cost one credit per market that returns data. The default buys the shots ladder only (it used to buy shots and points, two credits a game), so the same free-plan credits price
about twice as many games. Full coverage of a 14-game night needs about 14 shots credits plus 3 moneyline pulls: roughly 20 credits a night against the free plan's ~11 a day.
