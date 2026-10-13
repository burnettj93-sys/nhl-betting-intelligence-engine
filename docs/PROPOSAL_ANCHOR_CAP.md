# Shots-estimate correction: the cap goes from +0.10 to +0.02 (implemented; awaiting your approval to activate)

**State (2026-10-13):** implemented and tested; **recording is paused** at your request; the new policy `anchor-tickets-2026-10-13` has a new digest that you have **not** approved. Nothing is recorded under it until you approve that digest and the pause is lifted.

## What was wrong
A shots leg's final estimate is the player's last-30-game record (shrunk toward the league rate), held to **the model's calibrated probability plus a cap**. The cap was +0.10. Where the record runs more than 10 points above the model the cap binds, and the estimate was then the model + 10 points. The chronological validation shows that estimate was too high by about 8 points, in every season.

## The affected subset (2+ shots legs where the +0.10 cap bound), predicted against observed
`docs/validation/anchor_cap_study.json`, key `subset_capped_at_live_cap`. "Observed" = how often the player reached the line.

| season (role) | legs | model said | old estimate (+0.10) | new estimate (+0.02) | observed | old − observed | new − observed |
|---|---|---|---|---|---|---|---|
| 2023 (model-fitting) | 555 | 0.572 | 0.672 | 0.592 | 0.591 | **+0.081** | +0.001 |
| 2024 (calibration; cap chosen here) | 693 | 0.578 | 0.678 | 0.598 | 0.599 | **+0.079** | −0.001 |
| 2025 (out of sample) | 665 | 0.572 | 0.672 | 0.592 | 0.592 | **+0.080** | −0.000 |

Standard error of each observed rate about ±0.018 to ±0.020 (independence assumed, so slightly too small). 3+ lines, same subset: 123 / 71 / 69 legs; old − observed +0.067 / +0.087 / +0.107, new − observed −0.011 / +0.009 / +0.027 (small samples, standard errors 0.04–0.06).

## How +0.02 was chosen, and what was not used
* **Rule, fixed before the 2025 evaluation was read:** the cap with the lowest 2024 log loss, pooled over the 2+ and 3+ lines weighted by player-games, from the grid 0.10, 0.08, 0.06, 0.04, 0.02, 0.00. The script prints the rule's own choice next to the value used (`selection` in the JSON). Pooled 2024 log loss: 0.60094, 0.60028, 0.59935, 0.59829, **0.59773**, 0.59832 → **+0.02**. (For the 3+ line alone 0.00 was lower than 0.02 by 0.00001, which is nothing; one cap is used for both.)
* **Evaluated on 2025**, which the choice did not use: 2+ lines (7,962 player-games) log loss 0.59608 → **0.59327**; 3+ lines (611) 0.65948 → **0.65590**; Brier improves too.
* **No ticket outcome was used.** The study script reads only the validation walk (`research.product_models`), never the ledger or the forward log. The weakness itself is in `docs/validation/anchor_calibration.json`, committed on 2026-10-11 (before the 2026-10-12 tickets existed).
* **Honest limit on "untouched":** that calibration table lists all three seasons including 2025, so the *existence* of the weakness was seen in a table that included 2025. The *size* of the cap was chosen on 2024 alone and 2025 was used only to evaluate it. There is one out-of-sample season, no sportsbook history, and the tests cover calibration of the estimate, not betting value against price.

## What changed in the code (one number)
* `anchor.ANCHOR_CAP = 0.02` (the old value is kept as `ANCHOR_CAP_V1 = 0.10`); the cap is part of the policy (`TicketPolicy.anchor_cap`), so it is in the digest.
* Policy `anchor-tickets-2026-10-11` is kept unchanged as `ANCHOR_V1`; its digest is still **c236c526e00b9f04**. The new policy `anchor-tickets-2026-10-13` differs from it only in the id and `anchor_cap=0.02`: floor 30%, value ≥ +5% and ≥ +3% after the 3-point haircut, midpoint test, +100, 1 per leg / 1 per player / 3 per game, five slots, the 0.60 shots-leg floor, 70% for 3+, 20 games and the moneyline rule are all unchanged. No threshold was loosened to offset fewer tickets (`tests/test_shots_cap_correction.py`).
* Your 2026-10-11 approval names the old digest and does **not** carry over.

## Which entries had the overstated estimates (nothing was rewritten)
* Every shots leg and ticket estimated under the old cap carries `+anchor-last30` at the end of its `model_version` (ledger rows and logged legs); corrected ones carry `+anchor-last30-cap02`. Today, Ticket History and Morning Review show an "Earlier estimate rule" notice on the two tickets recorded under the old rule: `T6B227B0036C56E` (won) and `TBF0D9DBCC38D72` (lost). Their entry probabilities, prices, stakes and results are exactly as recorded.
* The frozen-prediction log records the policy digest with each snapshot; the scorer now reports each rule separately (`by_policy`), so legs estimated at +0.10 are never averaged with corrected ones. Everything logged up to the switch is under `c236c526e00b9f04`.

## What it does to the recommendations
See `docs/validation/` and the delivery note: the live board and Best Options under the corrected cap, and the replay of the 2026-10-12 pool (18 supported legs become 8 and no ticket qualifies; the two recorded tickets would not have been chosen). Expect many days with empty slots; that is the intended outcome when value rests on the record alone.

## To activate (yours alone)
1. `python3 -m operational.ticket_policy show` (prints the digest) → `python3 -m operational.ticket_policy approve <new digest>`.
2. `python3 -m operational.recording_pause resume` (refuses until the digest is approved).
