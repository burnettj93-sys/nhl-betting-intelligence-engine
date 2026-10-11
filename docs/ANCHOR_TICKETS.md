# Anchor tickets — how the engine builds parlays (revision 2, 2026-10-11)

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

A moneyline quote is a ticket leg for as long as it is **fresh by the same limit as a shots price** (150 minutes more than two hours before puck drop, 100 minutes inside two hours; the quote's own retrieval time
is the age), whatever pull produced it: the 8:00 AM league-wide pull, a display refresh or the T-35 pull. An older quote from today is shown **provisional** (never recordable); a quote from another day is excluded.
Morning quotes are *early* (more than three hours before puck drop) and count toward the existing cap of two early tickets a day; every ticket is re-judged against the clock at the moment of recording.
No pull was added: moneylines still cost one credit for the whole league, and the plan's allowance (display refreshes and the T-35 decision pulls) is unchanged.

## Other markets stay in scope

Tickets use shots and moneylines, but the credit plan still buys **points** as an add-on to the pregame shots capture: the lead games first (funded before morning breadth), other priced games with whatever
shots coverage leaves over; a game without a points credit is recorded as "not fetched — credit budget" rather than silently missing. Goals, saves and puck lines are unchanged. The personal Paper Parlay Builder
and Players pages read every priced line from the archive regardless of the ticket policy. An owner can buy points with every game's shots (two credits a game) with `NHL_ENGINE_PROP_MARKETS`.
