# Shots-cap correction: what the live board does, 2026-10-13

Recording was paused at 15:35:57Z on the owner's instruction; the active (unapproved) policy is `anchor-tickets-2026-10-13`, digest `9f5e3448e10ff62e`. The frozen-prediction log keeps being written (record at 20:28:11Z carries that digest and `recording: false`).

## Live pregame wave, 20:28Z (121 priced legs, 2 games with prop prices)
Replayed through the real selector with both rules (replay of the 2026-10-12 evening snapshot under the old rule reproduces the two recorded tickets exactly: +138.6 at 51.6% and +155.1 at 44.4%).

| rule | cap | supported legs | qualifying tickets | result |
|---|---|---|---|---|
| `c236c526e00b9f04` (superseded) | +0.10 | 9 | 2 | 1 ticket: Gritsyuk 2+ (−120) + Tkachuk 2+ (−310), +142, estimated hit 48.3%, value +17.2% (+7.2% after the 3-point haircut) |
| `9f5e3448e10ff62e` (proposed) | +0.02 | 5 | 0 | none: best of 6 combinations hits about 46% at estimated value −5.6% (−13.8% after the haircut); policy needs +5% and +3% |

## Earlier replays
| snapshot | old rule | corrected rule |
|---|---|---|
| 2026-10-12 21:18Z (the recorded wave) | 13 legs, 2 tickets (Gritsyuk+Eichel, Batherson+Coleman) | 6 legs, none; best −0.1% |
| 2026-10-13 12:00Z (morning) | 12 legs, none (best −2.2%) | 8 legs, none (best −2.5%) |

Every other threshold is identical in both policies (floor 30%, value +5% and +3% after the haircut, midpoint test, +100, one per leg and player, three per game). No threshold was loosened to offset fewer tickets.
