# Selector audit: does it pick what the objective asks for?

**Objective (yours):** parlays at +100 or better with a *strong estimated chance of hitting* and defensible value — not simply the highest estimated EV at much longer odds.
Evidence: `docs/validation/selector_audit_2026-10-08.json`, reproducible with `python3 deploy/audit_selector.py` (rebuilds the 2026-10-08 18:07:56Z candidate pool from the archived
DraftKings captures and the model as it stood that morning; it reproduces all five recorded tickets exactly).

## 1. What the selector does

Among the tickets that pass the policy (combined price ≥ +100, estimated EV ≥ 5% at the model's probabilities, EV still ≥ 0 after lowering every leg 3 points), it takes them in order of
**estimated hit chance** (then EV, then fewer legs), skipping any that would put too much on one leg (≤ 2 tickets), one player (≤ 2) or one game (≤ 3). It never reorders by payout. So
the order is already hit-chance-first; the question is why the day's last two tickets were +458 / +483.

## 2. Why +458 (20.8%) and +483 (20.2%) were picked — the candidates on 2026-10-08

106 tickets qualified, built from 27 eligible legs in 6 games (only 2 legs were strong: Coleman 2+ shots 63.5%, Lee 2+ shots 61.0%). The five recorded tickets ranked 1, 2, 5, 16 and 17 by hit chance:

| Rank | Hit chance | Price | EV | EV after haircut | Ticket | What happened |
|---|---|---|---|---|---|---|
| 1 | 38.7% | +198 | +15.5% | +4.7% | Coleman 2+ & Lee 2+ shots | taken |
| 2 | 35.4% | +213 | +11.0% | +0.1% | Coleman 2+ & Bourque 2+ shots | taken |
| 3 | 28.5% | +326 | +21.4% | +7.9% | Coleman 2+ shots & Seguin 1+ pt | **skipped: Coleman 2+ already on 2 tickets** |
| 4 | 27.9% | +361 | +28.9% | +14.4% | Bourque 1+ pt & Coleman 2+ | skipped: same limit |
| 5 | 27.3% | +320 | +14.8% | +1.8% | Seguin 1+ pt & Lee 2+ shots | taken |
| 6–15 | 22.0–26.8% | +354…+431 | | | each uses Coleman 2+ or Lee 2+ | skipped: those legs already on 2 tickets |
| 16 | 20.8% | +458 | +16.1% | +1.0% | Coleman 3+ & Bourque 2+ shots | taken (**was a third Coleman ticket — the new player limit would now stop it**) |
| 17 | 20.2% | +483 | +17.7% | +2.5% | Bourque 1+ pt & Dvorak 2+ shots | taken |

So **shorter, higher-hit alternatives did exist, and every one of them was blocked by the shared-exposure limits**, not outranked by a bigger payout. Nothing in the pool had a higher hit chance
than the ones taken and was free to take. I also checked whether the greedy order loses anything: an exhaustive search for the five-ticket set with the most expected hits under the same limits
finds **the same set** (1.40 expected hits both ways).

With the player limit added in the previous round, the 2026-10-08 book would have been 38.7% / 35.4% / 27.3% / 20.2% (+483) / 18.6% (+549): the two **tail tickets remain**, because with only
two strong legs in the pool nothing better can be formed without a third ticket on Coleman or Lee. I did not add a hit-chance floor (it would be invented) and did not force the slots: the report
now says, for every cycle, which tickets were taken, which higher-hit ones were skipped, and under which limit (Today → "Why these tickets…", Diagnostics → selection report; stored per recording cycle).

## 3. The +0.2-point leg (Bourque 2+ shots at −125, model 55.8% vs 55.6% implied)

* **Is the edge meaningful?** No — in either direction. In the 2025-26 walk-forward, the model's probability for an individual veteran in this band is off by **about 8 points (1 SD, between players)** for shots 2+ and
  **about 10 points** for points 1+ (after removing coin-flip noise), so a +0.2-point edge — and also the 3-point haircut — are small next to the model's own player-level error. In the same band the model
  **under**-predicted by about 3 points on average (observed 59.0% vs predicted 55.9% for shots 2+; 46.3% vs 43.6% for points 1+), so the true edge on the veteran legs is, if anything, larger than shown. Nothing here shows a profitable edge;
  these are estimates.
* **Does the leg improve the ticket against alternatives?** For the stated objective, yes: it is a high-probability (56%) leg that turns Coleman (−135, not +100 on its own) into a +213 ticket with a **35.4%** chance — the
  second-best hit chance of the day. The alternatives for Coleman's second slot (Seguin 1+ pt) offer 28.5% at +326 with EV after haircut +7.9% vs +0.1%.
* **Tested fix: require every leg to survive the haircut on its own.** Result on the same pool: only 12 of 27 legs survive; the best ticket falls from **38.7% to 28.5%**, expected hits from
  **1.40 to 1.08**, and the book moves to +549…+708 tickets — **exactly the longer-odds, lower-hit pattern you objected to**. The evidence does not support it.
* **Decision:** the rule stays (a leg must beat its own price; the ticket must stay non-negative with every leg lowered 3 points). What changed instead: (1) the per-player limit (one bad night no longer sinks three tickets);
  (2) each leg on a card now says how far the model's chance is from its price, and shows **"no edge of its own"** when it is under one point, so a ticket carried by its other leg is visible; (3) the
  selection report; (4) the card and Model Health state the ~8-point player-level model error, so small differences in value are not mistaken for findings.

## 4. What is not established
Hit chances are model estimates (35% does not mean 35%); the five tickets of 2026-10-08 all lost (probability of that under the stated chances: about 18%), which proves nothing either way. There are no historical
DraftKings prices to test realised profit against. Moneyline legs were not reconstructed (their prices are not archived by capture time).
