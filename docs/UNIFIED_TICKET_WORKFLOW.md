# Unified NHL paper-ticket workflow

One selector, one ticket object, one id. What Today displays is what the ledger records, what settles, and
what a postmortem explains.

## Flow

```
launchd (every 15 min)  operational/real_parlay_paper_trader.py
  1 revalidate recorded tickets      -> ALERTS only (ticket_alerts); never refunds or voids
  2 refresh DraftKings prices        operational/best_bets.py (bounded credit capture + rolling-form model)
  3 collect legs                     operational/daily_tickets.py::collect_candidate_legs
        validated adapters (moneyline, standard shots, saves, alternate shots)  +  rolling-form shots and points legs
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

## Where the shots and points probabilities come from

The validated shots model reads a research corpus that ends 2026-04-15, so the sweeps log `CORPUS_STALE` for the 2026
season. The rolling-form model (last 20 / 60 games from the daily MoneyPuck file, shrunk toward the position average,
lower of the two) works on current data and requires the player to have dressed in his team's last game. A shots leg
that both models price takes the **lower** probability. A validated shots leg the rolling model cannot confirm is
dropped. This is a research model, not validated against live results; the model version is stored on every leg.

Points (1+ / 2+) use the locked points model blended 50/50 with the player's last-60-game rate, home/away adjusted,
priced against DraftKings' `player_points` Over 0.5 / 1.5. The contract was certified against a real archived payload
(`tests/fixtures/draftkings_player_points_real_payload.json`), settlement is mapped (goals + assists), and only the
rolling-form source supplies these legs. Same research-model caveat applies.

## Start times: which source controls what

The provider (The Odds API) and the league schedule can disagree. PIT at WSH on 2026-10-07: the NHL API (`api-web.nhle.com`,
confirmed live that day) says 23:30Z; the provider has listed 23:40Z in all 849 archived listings since 2026-09-24, so the
10-minute offset is persistent, not a late schedule change. The league schedule is the authority for the game; the provider's
time only describes the book's listing.

| Decision | Source |
|---|---|
| Which game a provider event is | team abbreviations matched to the nhl.db schedule (ids, never times) |
| Final state, scores, settlement | nhl.db / NHL API |
| Start time shown on a card and stored as `game_start_utc`; ticket `event_start_utc` (when settlement may begin) | official NHL schedule (earliest leg) |
| "Game has started" cutoff, price-freshness limit (150 min if >= 2 h out, 100 min inside), capture window (5 h before) | the **earlier** of provider and official start, so a leg is never offered after either source says the game began |
| Schedule-change alerts on recorded tickets | `game_schedule_events` (NHL API revisions) |
| Moneyline T-35 evaluation | official schedule (pricing engine) |

The provider's start is stored on every frozen leg as `provider_start_utc` for audit; Today's technical section lists both
times and the difference per game.

## Capture cadence

A refresh is due once the newest price is within 45 minutes (three 15-minute trader cycles) of its freshness limit: at
105 minutes of age while the game is 2+ hours away, at 55 minutes inside two hours. Cutoffs are unchanged; prices are replaced
before they would be rejected. The newest capture of either job counts (the prop sweeps' pulls are not duplicated), shots
and points prices are taken from the newest capture of each market, and the existing 36-credit daily cap and the global
quota guard are untouched. One game costs about four captures (8 credits); a three-game evening about 24. On a larger slate
the cap stops the farthest-out refreshes first, and a price that is not refreshed simply drops out as stale.


## Recording windows (reserved slots) — added 2026-10

Prices appear within about five hours of each puck drop, so on a day with early and late games the first qualifying tickets could use all five slots
before the later games are priced. The selector now groups the day's games into *waves* (puck drops within 90 minutes of the first puck drop of that wave). A ticket belongs
to the wave of its earliest game. While later waves have yet to start, an earlier wave may hold at most 5 slots minus the number of later waves (up to 2) — so with one
later wave, an earlier wave can record at most 4 tickets; with two or more, at most 3. On a single-wave day nothing changes. The reservation only removes a
qualifying ticket from the current cycle; it never admits a ticket the ticket rules reject (+100 combined, estimated edge ≥ 5%, positive after the 3-point haircut,
per-leg/per-game limits). If a later wave never produces a qualifying ticket its reserved slots stay empty and Today says why. The wave table is in the ticket
board's diagnostics (`recording_policy`). Tests: `tests/test_daily_tickets.py::TestRecordingWaves`.

## Origins

Tickets carry `origin`: `AUTOMATIC` (selected and recorded by the engine) or `MANUALLY_ADDED` (docs/MANUAL_ORDERS.md). The five daily slots, the per-leg and per-game
limits used by the selector, and the automatic performance record count AUTOMATIC tickets only. Exposure tables, the account and settlement cover both.
