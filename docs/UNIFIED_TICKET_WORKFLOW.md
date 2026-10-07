# Unified NHL paper-ticket workflow

One selector, one ticket object, one id. What Today displays is what the ledger records, what settles, and
what a postmortem explains.

## Flow

```
launchd (every 15 min)  operational/real_parlay_paper_trader.py
  1 revalidate recorded tickets      -> ALERTS only (ticket_alerts); never refunds or voids
  2 refresh DraftKings prices        operational/best_bets.py (bounded credit capture + rolling-form model)
  3 collect legs                     operational/daily_tickets.py::collect_candidate_legs
        validated adapters (moneyline, standard shots, saves, alternate shots)  +  rolling-form shots legs
  4 select                           research/real_market_parlay/engine.py::select_tickets   (the only selector)
  5 record ($10 each, atomic funds check)    operational/paper_bankroll.py::create_real_market_combo_paper_bet
  6 settle                           operational/paper_bet_settlement_driver.py   (docs/PAPER_SETTLEMENT_RULES.md)
  7 write the board                  operational/runtime/tickets_state.json  -> Today, cloud snapshot "tickets" section
```

## Ticket identity

`ticket_id = "T" + sha256(Eastern date | sorted leg identities)[:14]`, leg identity = (game, participant, market,
line, side). A price move does not change it. It is the ledger's `paper_bet_id` and idempotency key, so a refresh or
restart can never stake the same ticket twice. Recording freezes, in `legs_json`: legs, player/team identities, lines,
prices, price timestamps, per-leg probabilities, model versions, game start times; and the `$10` stake, estimated
combined price, joint probability and EV on the row. Entry columns are protected by a database trigger.

Status on Today: **Recommended** (qualifies, not a paper bet, may change with the next refresh) -> **Recorded**
(paper bet, frozen) -> **Pending** (a game has started) -> **Won / Lost / Void / Unresolved**.

## Selection policy (engine.py constants)

| Rule | Value |
|---|---|
| Combined decimal price | >= 2.0 (+100), product of leg prices, always labelled *estimated* |
| Legs | 2 by default; more only if no 2+-leg sub-ticket passes the policy by itself |
| Same-game legs | never combined (no joint model, no verified combined price) |
| Value test | estimated EV >= 5%, and EV >= 0 after lowering every leg's probability by 3 points |
| Each leg | conservative probability must beat its own implied price; decimal >= 1.20 |
| Tickets per Eastern day | up to 5 distinct; singles are a separate, unstaked list |
| Cross-ticket exposure | identical leg sets are duplicates; a leg on <= 2 tickets; a game on <= 3 tickets; never both moneyline sides |
| Ranking | modeled hit probability, then EV |

The inherited 70% joint-probability rule is gone: a 70% four-leg parlay is rarely +100, and a high hit chance with no
edge loses money. The haircut only ever lowers probabilities; nothing is inflated.

## Paper account

$500 start, $10 per ticket. `available cash = 500 + realized P&L - open stakes` (open = pending or unresolved).
Funds are checked inside the same `BEGIN IMMEDIATE` transaction as the insert; under $10 nothing is recorded and no
top-up exists. Five fresh tickets leave $450 cash and $50 open.

## Where the shots probabilities come from

The validated shots model reads a research corpus that ends 2026-04-15, so the sweeps log `CORPUS_STALE` for the 2026
season. The rolling-form model (last 20 / 60 games from the daily MoneyPuck file, shrunk toward the position average,
lower of the two) works on current data and requires the player to have dressed in his team's last game. A shots leg
that both models price takes the **lower** probability. A validated shots leg the rolling model cannot confirm is
dropped. This is a research model, not validated against live results; the model version is stored on every leg.
