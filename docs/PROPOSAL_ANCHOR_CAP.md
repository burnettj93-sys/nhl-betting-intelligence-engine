# Proposal (NOT active): lower the shots-leg cap from +10 points to +2 points over the model

Status: **proposed, not activated.** The approved policy `c236c526e00b9f04` and `anchor.ANCHOR_CAP = 0.10` are unchanged and still drive recording. This is a change to the approved probability method, so it is
written down for review first.

## The weakness (found in the existing validation, not in recent results)
A shots leg's final estimate is the player's last-30 record (shrunk) held to at most the model's calibrated probability **plus a cap** (and 70% for 3+ lines). When the record is far above the model, the cap
binds and the estimate is the model + 10 points. `docs/validation/anchor_calibration.json` shows those capped 2+ legs were over-stated by about 8 points in every season:

| season (role) | capped 2+ legs | model | final estimate | happened |
|---|---|---|---|---|
| 2023 (model-fitting) | 555 | 0.572 | 0.672 | 0.591 |
| 2024 (calibration) | 693 | 0.578 | 0.678 | 0.599 |
| 2025 (out of sample) | 665 | 0.572 | 0.672 | 0.592 |

Where the record runs well above the model, what happened was close to the model (+2 points), not the record. These are also the legs a ticket finds "cheap" (price below the estimate), so the over-statement flows straight into the ticket's estimated value.

## The correction, chosen on one season and tested on the next
`deploy/anchor_cap_study.py` (output `docs/validation/anchor_cap_study.json`) varies only the cap, on the legs the current rule would ticket (same population for every cap). **Chosen on 2024** (the calibration season): the
cap that minimises log loss is **+0.02** for 2+ lines (0.59226 against 0.59540 at +0.10) and +0.00/+0.02 for 3+ lines (0.66099 against 0.66500). **Tested on 2025, out of sample, nothing fitted there**:

| 2025, log loss (lower is better) | cap +0.10 (live) | cap +0.02 (proposed) |
|---|---|---|
| shots 2+ (7,962 player-games) | 0.59608 | **0.59327** |
| shots 3+ (611 player-games) | 0.65948 | **0.65590** |
| capped 2+ legs: final estimate vs happened | 0.672 vs 0.592 | 0.648 vs 0.647 |

2023 (model-fitting, for stability only) points the same way (0.57163 → 0.56940 for 2+). The gain is small in log loss, but it is concentrated exactly where the live rule over-states.

## What it would change in practice
* Fewer legs are "well supported" (final estimate >= 0.60), and the ones that remain are the ones where the model and the record agree.
* Replaying the real 2026-10-12 pool through the live selector with the cap at +0.02: **18 supported legs become 8, and no ticket qualifies** (the best hits about 40% with estimated value −2.4%). Today's two recorded tickets would not have been selected: in the 21:18Z snapshot Batherson (model 0.56, final 0.66 → 0.58) and Gritsyuk (model 0.55, final 0.62 → 0.57) fall below the 0.60 floor.
* So most of the recent tickets' estimated value came from the over-stated part of the estimate. This does not prove the tickets lose; it says the value shown for them is probably too high.

## Limits
No historical sportsbook prices exist, so this is a calibration test over all supported player-games, not a test of value against the price. Games of one night and one player's consecutive games are correlated, so the standard errors in the study (independence assumed) are too small. One out-of-sample season.

## To activate (needs your approval; nothing else in the policy changes)
1. `research/real_market_parlay/anchor.py`: `ANCHOR_CAP = 0.02` (and the "held to the model +10 points" wording in `leg_evidence` becomes "+2 points").
2. Add the cap to `policy.as_dict()` so the approved digest covers it; the digest changes, so the owner approves the new digest (`ticket_policy approve`) before recording uses it.
3. Re-run `deploy/anchor_calibration.py`; the Best Options and Today wording follow from the same constant.
Expect many days with empty slots; that is the intended behaviour when value rests on the record alone.
