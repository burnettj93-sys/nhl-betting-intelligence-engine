# Anchor tickets — how the engine builds parlays (revision 3, 2026-10-12)

Implemented in `research/real_market_parlay/anchor.py` and switched on through the policy object (`research/real_market_parlay/policy.py`, `PROPOSED`, `mode="anchor"`). It is a **proposal awaiting the
owner's approval**: automatic recording stays paused, and the board shows what it would record as *proposed, not recorded*.

## What a ticket is

* 2 to 5 legs, every leg from a different game (two legs from one game would need a joint price DraftKings has not quoted and this code cannot honestly derive).
* Legs are **well supported**: 2+ (or 3+) shots on goal for a player who reaches the line in most of his last 30 games, from the real DraftKings alternate ladder; or a moneyline at the market's own no-vig
  chance when the Elo model also has that side as the favourite. Points and anytime-goal legs are not used in tickets.
* Combined price +100 or better (product of each leg's real DraftKings price; an estimate, never a quoted parlay price).
* Ranked by estimated hit chance. Up to five tickets; a slot with no worthwhile ticket stays empty.

## Four numbers per leg, always shown apart

| Number | What it is |
|---|---|
| Recent hit rate | hits ÷ games over the player's last 30 games, unshrunk (24 of 30 = 80%) |
| Model | the validated skater model's Platt-calibrated probability |
| Final estimate | what the ticket uses: the recent record shrunk toward the league rate by 8 pseudo-games, held to the model + 10 points, and to 70% for 3+ shots |
| Price implies | 1 ÷ the leg's decimal price (includes the bookmaker's margin) |

A moneyline's final estimate is the market's no-vig probability; such a leg costs about the margin, so it can complete a ticket's price but never creates value.

## Calibration of the exact estimate used (`deploy/anchor_calibration.py` → `docs/validation/anchor_calibration.json`)

The project's own walk-forward evaluation (`research/product_models/validate.walk`), with the live model's own configuration, dispersion and Platt parameters, applying the exact rule above to every player-game
the selector would have priced (final estimate ≥ 60%, ≥ 40 games of history, ≥ 12 expected minutes).

**Season roles:** 2022–2023 train the dispersion; 2024 chooses the configuration and fits the Platt calibration; **2025 is the only out-of-sample season** (it fitted nothing). 2023 and 2024 are model-fitting
seasons, shown for information only. The last-30 record has no fitted parameter in the script (window 30, 8 pseudo-games predate it; the repository cannot show earlier seasons did not influence them).

| Season (role) | Line | Supported player-games | Recent hit rate | Model | Final estimate | Happened |
|---|---|---|---|---|---|---|
| 2025 (out of sample) | 2+ shots | 7,962 | 75.7% | 67.3% | 69.1% | 70.5% |
| 2025 (out of sample) | 3+ shots | 611 | 73.6% | 58.9% | 62.8% | 62.8% |
| 2024 (model-fitting) | 2+ shots | 8,766 | 76.3% | 68.6% | 69.7% | 70.8% |
| 2024 (model-fitting) | 3+ shots | 758 | 74.5% | 61.7% | 63.5% | 62.1% |
| 2023 (model-fitting) | 2+ shots | 10,121 | 77.2% | 70.2% | 70.8% | 72.6% |
| 2023 (model-fitting) | 3+ shots | 1,410 | 77.1% | 66.3% | 65.3% | 70.3% |

Reading it: the unshrunk recent hit rate overstates what happens (75.7% vs 70.5%); the model understates it by about 3 points; the final estimate is within 1.4 points on 2+ shots and 0.0 on 3+ in the out-of-sample
season. In the 0.80+ band for 2+ shots the final estimate was 81.9% against 82.3% (n = 560); the 3+ band above 0.70 is always held at 70% and has few cases (n = 29 out of sample; 79 in 2024, where it over-stated
by 6.7 points). Standard errors assume independent player-games and are too small: games of one night and a player's consecutive games are correlated.

**What this does not show:** calibration over all supported player-games is not calibration over the subset where DraftKings prices a leg cheaper than the estimate. If the price knows something the record does not
(opponent, lineup, injury), the value shown on a ticket is not real. No historical prices exist to test this; the forward shadow log (`selection_shadow.jsonl`) stores each priced leg's recent rate, model and
market probability and the proposed tickets, so it can be scored against official results.

## What "defensible value" means

A ticket is kept only if, at the real prices: estimated value ≥ +5%; ≥ +3% with every leg lowered three points; not negative if the true chance is halfway between the final estimate and the price; estimated hit
chance ≥ 30%; no leg gives up more than about a bookmaker's margin (−5%) on its own price; and no shorter ticket from its legs already passes. Exposure: a player or leg on at most one ticket a day, a game on at most three.

## Moneylines in morning and daytime combinations

A moneyline quote is a ticket leg for as long as it is **fresh by the same limit as a shots price** (150 minutes more than two hours before puck drop, 100 minutes inside two hours; the provider's own quote time, which the odds bridge stores with the snapshot, is the age), whatever pull produced it: the 8:00 AM league-wide pull, a display refresh or the T-35 pull. An older quote from today is shown **provisional** (never recordable); a quote from another day is excluded.
Morning quotes are *early* (more than three hours before puck drop) and count toward the existing cap of two early tickets a day; every ticket is re-judged against the clock at the moment of recording.
No pull was added: moneylines still cost one credit for the whole league, and the plan's allowance (display refreshes and the T-35 decision pulls) is unchanged.

## Other markets stay in scope

Tickets use shots and moneylines, but the credit plan still buys **points** as an add-on to the pregame shots capture: the lead games first (funded before morning breadth), other priced games with whatever
shots coverage leaves over; a game without a points credit is recorded as "not fetched — credit budget" rather than silently missing. Goals, saves and puck lines are unchanged. The personal Paper Parlay Builder
and Players pages read every priced line from the archive regardless of the ticket policy. An owner can buy points with every game's shots (two credits a game) with `NHL_ENGINE_PROP_MARKETS`.

## Captures that can be compared (2026-10-12)

A ticket needs prices from several games that are all fresh at one evaluation time, before the first of its games starts. Two defects made that rare and are fixed:

* **Games are bought a whole wave at a time.** `credit_planner.connected_order` buys pregame captures for the biggest wave of games first (a wave = puck drops within 90 minutes of its first). The old
  order took one game from each wave in turn, so on 2026-10-11 the two priced games (5:00 PM and 7:00 PM ET) were never fresh together and no ticket could form. An isolated tail (one game of a wave left
  alone by the budget) is not bought; its credit stays in the day.
* **A wave is captured together.** Every priced game of a wave is captured when the wave's FIRST game reaches its pregame window (105 minutes before its puck drop), in one pass, instead of at each game's own
  window an hour apart (`best_bets.capture_prices`, `capture_plan` shows the same windows). Later waves get their own look.

## Same-game slips (Paper Parlay Builder)

DraftKings prices legs from one game together, and this system has no source for a combined quote. A slip with two legs from one game is an **unpriced draft**: the builder shows no price or return and
disables Submit, and the engine refuses such an order (`SAME_GAME_NOT_PRICED`) whatever it says; nothing is recorded or debited. Cross-game slips are recorded as an *Estimated* product of the leg prices; a
single leg is a DraftKings quote.

## Best option for each person

`player_options.build_options` now uses the same estimates and tests as the tickets: for each player the fewest-leg combination (his supported legs plus companions from other games) that reaches +100 with
defensible value, ranked by estimated hit chance. A player with only points, goals or saves prices, and every goalie, gets no option, with the reason; nothing is made up.

## Frozen predictions

`operational/shadow_selection.py` runs on every trader cycle, recording or paused. For every fresh priced leg it freezes the quote times and price, the recent hit record, the model's probability, the market's
probability, the final estimate, the selector's decision and the reason (`SUPPORTED`, `THIN_SAMPLE`, `BELOW_STRONG_LEG_FLOOR`, `GIVES_UP_MORE_THAN_THE_MARGIN`, …), whether the leg is on a selected ticket,
the code and policy versions, and the tickets chosen with the nearest rejected ones. The watchdog warns if the log has not been looked at for an hour. `deploy/score_selection_shadow.py` scores it against
official results (`anchor_estimates`: recent rate, model, final estimate, price-implied and what happened, counted as player-games and games, not independent bets) and the daily review shows the day's score,
shared exposure, participation problems and corrective actions.

## The primary slip and the Slate Board (2026-10-14)

**One parlay a day.** The five-ticket workflow is replaced by a single primary parlay (policy `primary-slip-2026-10-14`, one slot): combined price **+100 to +500 inclusive**, the **fewest legs** that qualify,
ranked by estimated hit chance. Every leg rule and value test is the one of the cap-correction policy (`anchor-tickets-2026-10-13`, digest `9f5e3448e10ff62e`, kept unchanged): corrected +0.02 shots cap, the
30% floor, estimated value of at least +5% and +3% after the 3-point haircut, the midpoint test, one ticket per leg and per player, three per game. Nothing was loosened to produce a slip; a day on which
nothing qualifies records nothing and the board says why.

* **Same-game combinations are never built.** A same-game price needs a quoted combined price and this feed has none; the slip is always cross-game and its price is labelled *calculated* (the product of the
  legs' DraftKings prices).
* **Recording** (only after the owner approves the new digest and resumes): at most one ticket, only on pregame-window prices (no morning-look price), after the same recording-time revalidation as before.
* **Legs that can enter a slip:** shots legs (the corrected estimate) and moneylines (the market's own no-vig chance, strength model agreeing); goalie saves only with a confirmed starter and a validated model
  (none today); goals and points are shown on the board but are not slip legs (their estimates are not calibrated for tickets).

**The Slate Board** (`operational/slate_board.py`, page *Slate Board*) analyses every scheduled game: (1) the most likely goal scorers with the DraftKings price beside them (likelihood is not value); (2) the
two best shots values from the offered 1+ to 5+ ladder (the best value after the haircut among thresholds at least 45% likely and not priced shorter than about -400, so a -4000 1+ is not the "safe" pick);
(3) goalie saves: expected starter and status, expected saves and range, the offered line, conditional until the start is confirmed; (4) the moneyline with its exact price, or *No recommended side*
(puck line not assessed: its cover model has no validated result and no spreads price is fetched). Each game shows what was analysed, priced, supported and recommended, and what is missing and why
(*not posted*, *not fetched*, *budget blocked*, *fetch failed*). Prices missing for budget reasons are the odds-credit allowance, not a defect (docs/ODDS_BUDGET_CONFIGURATIONS.md).

Credits: after pregame shots and the morning look, anytime-goal prices are bought for the lead games (the board's section 1), then the Tomorrow availability check, the second display refresh and, with
what is left, the builder's points (`NHL_ENGINE_POINTS_ADDON=off` switches the points add-on off). A game with no points or goals prices is recorded BUDGET_BLOCKED and the builder and the board say so.
