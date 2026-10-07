# Paper ticket settlement rules

> **Status: void and parlay-reduction rules are UNVERIFIED.** DraftKings Ontario's published house rules could not be
> retrieved (the official page returned HTTP 403; only secondary articles were reachable, and they describe other US
> books' practice). `paper_bet_settlement_driver.VOID_RULES_VERIFIED` is therefore `False`: any ticket whose result
> depends on a void or a reduction (a did-not-play leg, alone or in a parlay) stays **UNRESOLVED** with its $10 still
> open, and the provisional outcome (e.g. "WIN repriced to +150" or "VOID") is stored in its notes and
> `settlement_json` but not applied to the account. A person must read Ontario's rules, confirm or correct the
> sections below, then flip the flag. A lost leg still loses the parlay (that holds under every convention).

These are the rules the paper ledger (`operational/paper_bankroll.db`) applies.
They are this project's own documented convention modelled on common
sportsbook practice. They have **not** been verified against DraftKings
Ontario's published house rules (no source for those is available to this
system), so treat the void/reduction behaviour as an assumption to confirm
before relying on it for real money.

## Account

* Starting bankroll $500, fixed stake $10 per ticket, per track.
* `available cash = $500 + realized P&L - stakes of open tickets`.
  Open = `PENDING` or `UNRESOLVED`.
* A ticket is recorded only if available cash >= $10, checked inside the same
  `BEGIN IMMEDIATE` transaction as the insert. Below $10 nothing is recorded.
  There is no automatic top-up.
* Recorded tickets are immutable (entry columns are protected by a trigger).
  Only settlement columns change, once.

## When a leg is void

A leg is void only when the real game result shows the named participant did
not take part in an otherwise final game:

| Resolver status | Meaning |
|---|---|
| `PLAYER_DID_NOT_DRESS` | skater not in the game's box score |
| `GOALIE_DID_NOT_PLAY` | goalie did not appear |
| `TEAM_DID_NOT_PLAY` | team absent from the game |

Nothing else voids a leg. In particular **none of these void or refund a
recorded ticket**: a changed goalie, a roster/injury report, a trade, a
schedule revision, a missed confirmation, or an error while revalidating.
Those are written as alerts (`ticket_alerts`) and shown on the ticket. If the
player really does not play, the leg resolves as a did-not-play void at
settlement.

Alternate-ladder thresholds ("2+ shots") are over-N-minus-half lines, so they
cannot push on an exact number.

## Settlement by ticket type

**Single (one leg):** win pays `stake * decimal - stake`, loss `-stake`,
void refunds the stake (profit 0).

**Parlay (several legs, cross-game):**

1. Any leg lost -> **LOSS**, even if other games are not finished.
2. Otherwise, any leg whose game is not final -> stays pending.
3. Otherwise, any leg the system cannot resolve (data gap, unsupported
   market) -> **UNRESOLVED**. The ticket stays open, keeps its stake in open
   exposure, and is retried on later runs. No outcome is guessed.
4. Every leg void -> **VOID** (stake refunded).
5. Some legs void, the rest won -> **WIN, repriced**: the voided legs are
   removed and the ticket pays at the product of the remaining legs' recorded
   decimal prices. The repriced odds and per-leg results are stored in
   `settlement_json`; `entry_odds` is never changed.
6. All legs won -> **WIN** at the recorded entry price.

The multiplied entry price is an **estimated combined price** (product of the
legs' own sportsbook prices). It is not a quoted DraftKings parlay price.

## Not handled (left open, never guessed)

* Postponed or cancelled games: the ticket stays pending until a result exists.
  There is no automatic void for postponements.
* Same-game parlays: never built (no verified joint-probability model or
  combined pricing).
* Settlement of puck-line and goal-scorer markets: not wired.
