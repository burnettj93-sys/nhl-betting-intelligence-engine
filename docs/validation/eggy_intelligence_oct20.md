# Eggy intelligence: the October 20 regression test

The ten bets the old engine recorded on 2026-10-20, each reviewed as the intelligence layer would have reviewed it on that day's data, with the old and the new rank, the outcome and the judgment of the DECISION apart from the outcome.

Provenance: `deploy/replay_eggy_review.py` rebuilt the candidate pool at each recording instant (sandboxed copy; nothing written to production); `operational/eggy_decisions.reconstruct_card` rebuilt each bet's pre-bet review from data strictly before the day; the facts of what happened are the official result and the MoneyPuck game logs. Machine-readable detail: `eggy_intelligence_oct20.json`.

- Caveat: The roster identity gate cannot be replayed (the roster file on disk is today's): NHL_ENGINE_ROSTER_GATE=off in the replay.
- Caveat: The evidence file's skater cells include 2026-27 games through Oct 20 (under 1% of its 51,516 rows); the team model's coefficients were fitted on 2009-2025 only.
- Caveat: The old ranking was a weighted score that no longer exists in the code: the old rank and score are those frozen on each ledger row.

## The ten bets at a glance

| Old rank | Bet | Outcome | New rank (of eligible) | New action | Thesis | Contradiction | Decision | Classification |
|---|---|---|---|---|---|---|---|---|
| 1 | WPG — MONEYLINE — +114 | LOSS (-10.00) | 9 of 10 | PASS | MODEL SAYS OVERPRICED | SEVERE | BAD DECISION | MODEL CALIBRATION |
| 2 | BOS — MONEYLINE — +142 | LOSS (-10.00) | 6 of 10 | PASS | UNDERDOG AT FAIR PRICE | HIGH | BAD DECISION | MODEL CALIBRATION |
| 3 | VAN — MONEYLINE — +240 | LOSS (-10.00) | 10 of 10 | PASS | MODEL SAYS OVERPRICED | SEVERE | BAD DECISION | MODEL CALIBRATION |
| 4 | MIN — MONEYLINE — +114 | LOSS (-10.00) | 8 of 10 | PASS | MODEL SAYS OVERPRICED | SEVERE | BAD DECISION | MODEL CALIBRATION |
| 5 | Filip Chytil — SHOTS ON GOAL 2+ — -130 | WIN (+7.69) | 2 of 81 | LOWER PRIORITY | FAIR PRICED | LOW | QUESTIONABLE DECISION | — |
| 6 | Max Sasson — GOALS 1+ — +800 | WIN (+80.00) | 27 of 81 | PASS | FAIR PRICED | SEVERE | BAD DECISION | LUCKY WIN |
| 7 | Paul Cotter — GOALS 1+ — +600 | LOSS (-10.00) | 29 of 81 | PASS | FAIR PRICED | SEVERE | BAD DECISION | MODEL CALIBRATION |
| 8 | Drew O'Connor — GOALS 1+ — +700 | LOSS (-10.00) | 28 of 81 | PASS | FAIR PRICED | SEVERE | BAD DECISION | ROLE CHANGE MISSED |
| 9 | Liam Ohgren — GOALS 1+ — +700 | LOSS (-10.00) | 18 of 81 | PASS | FAIR PRICED | HIGH | BAD DECISION | TOI PROJECTION ERROR |
| 10 | Jamie Oleksiak — GOALS 1+ — +2500 | LOSS (-10.00) | 25 of 81 | PASS | FAIR PRICED | HIGH | BAD DECISION | MODEL CALIBRATION |

The old engine ranked by a weighted score; the new layer orders by action, edge class, contradiction, calibration confidence, hit chance and edge (no score). 9 of the ten would have been a PASS under the new layer; the other: Filip Chytil — SHOTS ON GOAL 2+ — -130 (lower priority).

## What the new layer would have done with the same two pools

**19:02 ET (the four moneylines were recorded)**, pool as of 23:02:42Z: 14 legs, 10 eligible at -150 or better. Actions: LOWER PRIORITY 5, PASS 5.

| New rank | Bet | Action | Thesis | Contradiction | Calibration | Estimate | Edge |
|---|---|---|---|---|---|---|---|
| 1 | VGK — MONEYLINE — -110 | LOWER PRIORITY | MODEL SEES VALUE | HIGH | HIGH | 50.0% | +3.8 pts |
| 2 | DET — MONEYLINE — -118 | LOWER PRIORITY | COIN FLIP AT FAIR PRICE | LOW | HIGH | 51.7% | -2.8 pts |
| 3 | SEA — MONEYLINE — -102 | LOWER PRIORITY | COIN FLIP AT FAIR PRICE | LOW | HIGH | 48.3% | -1.8 pts |
| 4 | STL — MONEYLINE — -135 | LOWER PRIORITY | MODEL SEES VALUE | HIGH | HIGH | 55.1% | +1.4 pts |
| 5 | EDM — MONEYLINE — -135 | LOWER PRIORITY | MODEL SEES VALUE | HIGH | HIGH | 55.1% | +1.2 pts |

**19:33 ET (the six props were recorded)**, pool as of 23:33:49Z: 103 legs, 81 eligible at -150 or better. Actions: LOWER PRIORITY 16, PASS 65.

| New rank | Bet | Action | Thesis | Contradiction | Calibration | Estimate | Edge |
|---|---|---|---|---|---|---|---|
| 1 | VGK — MONEYLINE — -110 | LOWER PRIORITY | MODEL SEES VALUE | HIGH | HIGH | 50.0% | +3.8 pts |
| 2 | Filip Chytil — SHOTS ON GOAL 2+ — -130 | LOWER PRIORITY | FAIR PRICED | LOW | HIGH | 56.4% | -0.1 pts |
| 3 | DET — MONEYLINE — -118 | LOWER PRIORITY | COIN FLIP AT FAIR PRICE | LOW | HIGH | 51.7% | -2.8 pts |
| 4 | SEA — MONEYLINE — -102 | LOWER PRIORITY | COIN FLIP AT FAIR PRICE | LOW | HIGH | 48.3% | -1.8 pts |
| 5 | Filip Chytil — SHOTS ON GOAL 3+ — +225 | LOWER PRIORITY | FAIR PRICED | LOW | MEDIUM | 30.3% | -0.5 pts |
| 6 | Logan Stankoven — GOALS 1+ — +205 | LOWER PRIORITY | FAIR PRICED | MODERATE | HIGH | 31.1% | -1.7 pts |
| 7 | STL — MONEYLINE — -135 | LOWER PRIORITY | MODEL SEES VALUE | HIGH | HIGH | 55.1% | +1.4 pts |
| 8 | EDM — MONEYLINE — -135 | LOWER PRIORITY | MODEL SEES VALUE | HIGH | HIGH | 55.1% | +1.2 pts |

Outcomes of the moneylines the new layer kept as lower priority at 19:02 (official results, shown for completeness and NOT as validation: a handful of bets says nothing about an edge): STL beat WPG 2-1, EDM beat MIN 4-3 in overtime, VGK lost to TBL 2-6, DET lost to SEA 3-4 in a shootout, SEA won that game. The new layer would have recorded nothing at RECORD level in the 19:33 pool: it kept 16 candidates as lower-priority controls and experiments and passed on 65. With a conservative estimate and a real bookmaker margin in the price, few props show an edge, so it records fewer bets rather than ten for the sake of ten.

## Bet by bet

### 1. WPG — MONEYLINE — +114: LOSS

- **Prices and probabilities.** Price +114 implies 46.7% with the margin included; the market's own (no-vig) probability is 44.9%. The hockey/skater model says 41.2%; the estimate the new review uses is 44.9% (history may only lower it); the old engine recorded 44.9% (its Elo said 46.7%). Model edge at the last observed price: -5.6 pts (the model alone); on the estimate the review uses, -1.9 pts; the old engine's edge was -1.9 pts (neutral uncertain edge, score 0.39511).
- **Most likely outcome.** STL wins (58.8% by the hockey model; the market says 55.1% for STL).
- **Thesis (model says overpriced).** The hockey model gives WPG 41.2% against the market's 44.9% (-3.7 pts): the model thinks WPG is OVERPRICED.
- **Case for.** expected-goal differential favours WPG | decayed goal differential per game: WPG -0.27 (league rank 22), STL +0.13 (rank 14)
- **Case against.** STL is the better team on the model's own terms (58.8%, a slight gap) | our own model says WPG is overpriced by +3.7 pts | Elo, the old moneyline model, says 46.7% for WPG, +5.6 pts from the hockey model. Elo is rebuilt from this season's results only (no earlier seasons), so after a handful of games its ratings are mostly noise; it has the worst record of every estimate priced so far and plays no part in this decision
- **History.** In 2017-2025 (expanding window: each season predicted from the seasons before it), when the model gave a side about 0.40-0.45, that side won 42.8% of 2,884 (the model averaged 42.6%; standard error 0.9%) | On the 87 games priced since 2026-10-08, log loss: market 0.6438, this model 0.6467, goalie-team strength 0.6456, Elo 0.6726. The market was at least as good as every model; the weight a model's disagreement deserves is 0.34 with a 95% range of [-1.2, 1.96]: too few games to say. UNVALIDATED.
- **Contradiction SEVERE**: HOCKEY_PICTURE_AGAINST (HIGH); MODEL_SAYS_OVERPRICED (HIGH). Calibration HIGH; data 5 of 7 (missing: confirmed starting goalies, injuries and lineup); decision confidence LOW.
- **New action PASS** (new rank 9 of 10, old rank 1).
- **What happened.** WPG 1 at STL 2. STL is the better team (p 59%): held (STL won)
- **Decision: BAD DECISION** (Eggy's own model said the side was overpriced; the contradiction was SEVERE). Outcome LOSS. a bad decision that lost: a process failure, not variance. The loss itself was the likelier outcome (Eggy gave the bet 45% to win), so the loss is not what is being judged: the decision is, and the flaws in it were visible before the game. Classification MODEL CALIBRATION: the estimate that made the side look attractive was not the best available one (Elo said 47%); the hockey model and the market did not agree with it, and that was known before the game Action: INVESTIGATE.

### 2. BOS — MONEYLINE — +142: LOSS

- **Prices and probabilities.** Price +142 implies 41.3% with the margin included; the market's own (no-vig) probability is 39.6%. The hockey/skater model says 37.6%; the estimate the new review uses is 39.6% (history may only lower it); the old engine recorded 39.6% (its Elo said 46.6%). Model edge at the last observed price: -3.8 pts (the model alone); on the estimate the review uses, -1.7 pts; the old engine's edge was -1.7 pts (neutral uncertain edge, score 0.38395).
- **Most likely outcome.** DAL wins (62.4% by the hockey model; the market says 60.4% for DAL).
- **Thesis (underdog at fair price).** BOS is the weaker team by the hockey model (37.6%) and the market is within 3% of that (39.6%): a fair-priced underdog. The only argument for it is the price; the hockey picture favours DAL.
- **Case for.** decayed goal differential per game: BOS +0.28 (league rank 8), DAL +0.31 (rank 6)
- **Case against.** net expected-goal strength favours DAL | DAL is the better team on the model's own terms (62.4%, a clear gap) | the price is fair, so the expected value is the bookmaker's margin, a loss on average | Elo, the old moneyline model, says 46.6% for BOS, +9.0 pts from the hockey model. Elo is rebuilt from this season's results only (no earlier seasons), so after a handful of games its ratings are mostly noise; it has the worst record of every estimate priced so far and plays no part in this decision
- **History.** In 2017-2025 (expanding window: each season predicted from the seasons before it), when the model gave a side about 0.35-0.40, that side won 37.3% of 2,177 (the model averaged 37.7%; standard error 1.0%) | On the 87 games priced since 2026-10-08, log loss: market 0.6438, this model 0.6467, goalie-team strength 0.6456, Elo 0.6726. The market was at least as good as every model; the weight a model's disagreement deserves is 0.34 with a 95% range of [-1.2, 1.96]: too few games to say. UNVALIDATED.
- **Contradiction HIGH**: HOCKEY_PICTURE_AGAINST (HIGH). Calibration HIGH; data 5 of 7 (missing: confirmed starting goalies, injuries and lineup); decision confidence LOW.
- **New action PASS** (new rank 6 of 10, old rank 2).
- **What happened.** BOS 2 at DAL 3 (OT). DAL is the better team (p 62%): held (DAL won)
- **Decision: BAD DECISION** (the only argument was the price of a fair-priced underdog). Outcome LOSS. a bad decision that lost: a process failure, not variance. The loss itself was the likelier outcome (Eggy gave the bet 40% to win), so the loss is not what is being judged: the decision is, and the flaws in it were visible before the game. Classification MODEL CALIBRATION: the estimate that made the side look attractive was not the best available one (Elo said 47%); the hockey model and the market did not agree with it, and that was known before the game Action: INVESTIGATE.

### 3. VAN — MONEYLINE — +240: LOSS

- **Prices and probabilities.** Price +240 implies 29.4% with the margin included; the market's own (no-vig) probability is 28.2%. The hockey/skater model says 17.4%; the estimate the new review uses is 28.2% (history may only lower it); the old engine recorded 28.2% (its Elo said 41.8%). Model edge at the last observed price: -12.0 pts (the model alone); on the estimate the review uses, -1.2 pts; the old engine's edge was -1.2 pts (neutral uncertain edge, score 0.38525).
- **Most likely outcome.** CAR wins (82.6% by the hockey model; the market says 71.8% for CAR).
- **Thesis (model says overpriced).** The hockey model gives VAN 17.4% against the market's 28.2% (-10.8 pts): the model thinks VAN is OVERPRICED.
- **Case for.** decayed goal differential per game: VAN -1.08 (league rank 32), CAR +0.83 (rank 1)
- **Case against.** net expected-goal strength favours CAR | CAR is the better team on the model's own terms (82.6%, a dominant gap) | our own model says VAN is overpriced by +10.8 pts | Elo, the old moneyline model, says 41.8% for VAN, +24.4 pts from the hockey model. Elo is rebuilt from this season's results only (no earlier seasons), so after a handful of games its ratings are mostly noise; it has the worst record of every estimate priced so far and plays no part in this decision
- **History.** In 2017-2025 (expanding window: each season predicted from the seasons before it), when the model gave a side about 0.00-0.25, that side won 19.9% of 517 (the model averaged 21.2%; standard error 1.8%) | On the 87 games priced since 2026-10-08, log loss: market 0.6438, this model 0.6467, goalie-team strength 0.6456, Elo 0.6726. The market was at least as good as every model; the weight a model's disagreement deserves is 0.34 with a 95% range of [-1.2, 1.96]: too few games to say. UNVALIDATED.
- **Contradiction SEVERE**: HOCKEY_PICTURE_AGAINST (HIGH); MODEL_SAYS_OVERPRICED (SEVERE); LONG_PRICE (MODERATE). Calibration MEDIUM; data 5 of 7 (missing: confirmed starting goalies, injuries and lineup); decision confidence LOW.
- **New action PASS** (new rank 10 of 10, old rank 3).
- **What happened.** CAR 6 at VAN 3. CAR is the better team (p 83%): held (CAR won)
- **Decision: BAD DECISION** (Eggy's own model said the side was overpriced; the contradiction was SEVERE). Outcome LOSS. a bad decision that lost: a process failure, not variance. The loss itself was the likelier outcome (Eggy gave the bet 28% to win), so the loss is not what is being judged: the decision is, and the flaws in it were visible before the game. Classification MODEL CALIBRATION: the estimate that made the side look attractive was not the best available one (Elo said 42%); the hockey model and the market did not agree with it, and that was known before the game Action: INVESTIGATE.

### 4. MIN — MONEYLINE — +114: LOSS

- **Prices and probabilities.** Price +114 implies 46.7% with the margin included; the market's own (no-vig) probability is 44.9%. The hockey/skater model says 41.4%; the estimate the new review uses is 44.9% (history may only lower it); the old engine recorded 44.9% (its Elo said 41.3%). Model edge at the last observed price: -5.3 pts (the model alone); on the estimate the review uses, -1.9 pts; the old engine's edge was -1.9 pts (neutral uncertain edge, score 0.38973).
- **Most likely outcome.** EDM wins (58.6% by the hockey model; the market says 55.1% for EDM).
- **Thesis (model says overpriced).** The hockey model gives MIN 41.4% against the market's 44.9% (-3.5 pts): the model thinks MIN is OVERPRICED.
- **Case for.** decayed goal differential per game: MIN +0.29 (league rank 7), EDM +0.34 (rank 5)
- **Case against.** net expected-goal strength favours EDM | EDM is the better team on the model's own terms (58.6%, a slight gap) | our own model says MIN is overpriced by +3.5 pts
- **History.** In 2017-2025 (expanding window: each season predicted from the seasons before it), when the model gave a side about 0.40-0.45, that side won 42.8% of 2,884 (the model averaged 42.6%; standard error 0.9%) | On the 87 games priced since 2026-10-08, log loss: market 0.6438, this model 0.6467, goalie-team strength 0.6456, Elo 0.6726. The market was at least as good as every model; the weight a model's disagreement deserves is 0.34 with a 95% range of [-1.2, 1.96]: too few games to say. UNVALIDATED.
- **Contradiction SEVERE**: HOCKEY_PICTURE_AGAINST (HIGH); MODEL_SAYS_OVERPRICED (HIGH). Calibration HIGH; data 5 of 7 (missing: confirmed starting goalies, injuries and lineup); decision confidence LOW.
- **New action PASS** (new rank 8 of 10, old rank 4).
- **What happened.** MIN 3 at EDM 4 (OT). EDM is the better team (p 59%): held (EDM won)
- **Decision: BAD DECISION** (Eggy's own model said the side was overpriced; the contradiction was SEVERE). Outcome LOSS. a bad decision that lost: a process failure, not variance. The loss itself was the likelier outcome (Eggy gave the bet 45% to win), so the loss is not what is being judged: the decision is, and the flaws in it were visible before the game. Classification MODEL CALIBRATION: the estimate that made the side look attractive was not the best available one (Elo said 41%); the hockey model and the market did not agree with it, and that was known before the game Action: INVESTIGATE.

### 5. Filip Chytil — SHOTS ON GOAL 2+ — -130: WIN

- **Prices and probabilities.** Price -130 implies 56.5% with the margin included. The hockey/skater model says 56.2%; the estimate the new review uses is 56.2% (history may only lower it); the old engine recorded 56.2%. Model edge at the last observed price: -0.3 pts (the model alone); on the estimate the review uses, -0.3 pts; the old engine's edge was -0.3 pts (neutral uncertain edge, score 0.62889).
- **Most likely outcome.** Filip Chytil reaches 2+ shots on goal (56.2%).
- **Thesis (fair priced).** The price (56.5%) and our estimate (56.2%) are within 3% of each other: fair, no edge.
- **Case for.** calibrated model 56.2% (raw 57.0%); estimate used 56.2% (model probability record not usable) vs price-implied 56.5% | expected shots per game 1.97; season 2 in 2 games; last 5 2, 0, 1, 2, 1 (shots) | reached 2+ shots on goal in 104 of his last 150 games (69.3%), 22 of last 30, 5 of last 10, 1 of 2 this season
- **Case against.** opponent CAR: expected-goal differential rank 1 of 32, goal differential rank 1; tonight's win probability for VAN: 17.4%
- **History.** any player, model 50%-60%: the model averaged 54.9%; 57.7% happened (7,668 cases; 2025-26 and 2026-27 to date) | forwards, model 50%-60%: the model averaged 55.1%; 57.5% happened (6,072 cases; 2025-26 and 2026-27 to date)
- **Contradiction LOW**: HISTORY_SAYS_MODEL_RUNS_LOW (LOW). Calibration HIGH; data 8 of 10 (missing: confirmed lineup, injury news); decision confidence HIGH.
- **New action LOWER PRIORITY** (new rank 2 of 81, old rank 5).
- **What happened.** CAR 6 at VAN 3. expected 15.5 minutes on ice: held (played 18.7 (+3.2)); expected 1.38 power-play minutes: failed (had 0.00); expected shots 1.97: information (had 2); VAN win probability 17%: information (VAN 3, CAR 6)
- **Decision: QUESTIONABLE DECISION** (lower priority: estimate and price agree and the estimate is well calibrated: a calibration control, no edge). Outcome WIN. a questionable decision that won: no reward; the win does not make the reasoning better.

### 6. Max Sasson — GOALS 1+ — +800: WIN

- **Prices and probabilities.** Price +800 implies 11.1% with the margin included. The hockey/skater model says 16.4%; the estimate the new review uses is 13.8% (history may only lower it); the old engine recorded 16.4%. Model edge at the last observed price: +5.3 pts (the model alone); on the estimate the review uses, +2.6 pts; the old engine's edge was +5.3 pts (neutral uncertain edge, score 0.49854).
- **Most likely outcome.** Max Sasson does NOT reach 1+ goals (anytime goal scorer) (86.2%); this is a longshot bet.
- **Thesis (fair priced).** The price (11.1%) and our estimate (13.8%) are within 3% of each other: fair, no edge.
- **Case for.** calibrated model 16.4% (raw 16.9%); estimate used 13.8% (model probability unanchored) vs price-implied 11.1% | expected goals per game 0.19; season 2 in 10 games; last 5 0, 0, 1, 0, 0 (goals) | reached 1+ goals (anytime goal scorer) in 18 of his last 105 games (17.1%), 5 of last 30, 2 of last 10, 2 of 10 this season
- **Case against.** inferred fourth-line usage (ice time ranks in the bottom third of his team's forwards) | VAN is a clear underdog tonight (17.4% to win): fewer goals for its skaters on average | for forwards expected to play 12-14 min, model 15%-20% the model averaged 17.0% and 14.2% happened (2,322 cases, z -3.5): the model's 16.4% is recalibrated to 13.8% | a +800 prop is +600 or longer: a small error in the probability is a large relative edge, the margin is widest here, and long prices are more often too short than too long
- **History.** any player, model 15%-20%: the model averaged 17.4%; 17.1% happened (9,944 cases; 2025-26 and 2026-27 to date) | forwards, model 15%-20%: the model averaged 17.4%; 17.2% happened (9,454 cases; 2025-26 and 2026-27 to date)
- **Contradiction SEVERE**: LOW_USAGE (MODERATE); GAME_ENVIRONMENT (MODERATE); HISTORY_SAYS_MODEL_RUNS_HIGH (HIGH); LONG_PRICE (HIGH). Calibration HIGH; data 8 of 10 (missing: confirmed lineup, injury news); decision confidence LOW.
- **New action PASS** (new rank 27 of 81, old rank 6).
- **What happened.** CAR 6 at VAN 3. expected 13.1 minutes on ice: held (played 13.7 (+0.6)); expected goals 0.19: information (had 1); VAN win probability 17%: information (VAN 3, CAR 6)
- **Decision: BAD DECISION** (the contradiction was SEVERE). Outcome WIN. a bad decision that won: this was LUCK. Eggy must not become more confident in this pattern, and the process behind the bet still needs fixing. Classification LUCKY WIN: a bad decision that won: this was LUCK. Eggy must not become more confident in this pattern, and the process behind the bet still needs fixing. Action: WATCH.

### 7. Paul Cotter — GOALS 1+ — +600: LOSS

- **Prices and probabilities.** Price +600 implies 14.3% with the margin included. The hockey/skater model says 16.9%; the estimate the new review uses is 14.2% (history may only lower it); the old engine recorded 16.9%. Model edge at the last observed price: +2.6 pts (the model alone); on the estimate the review uses, -0.1 pts; the old engine's edge was +2.6 pts (neutral uncertain edge, score 0.46702).
- **Most likely outcome.** Paul Cotter does NOT reach 1+ goals (anytime goal scorer) (85.8%); this is a longshot bet.
- **Thesis (fair priced).** The price (14.3%) and our estimate (14.2%) are within 3% of each other: fair, no edge.
- **Case for.** calibrated model 16.9% (raw 17.4%); estimate used 14.2% (model probability unanchored) vs price-implied 14.3% | expected goals per game 0.19; season 4 in 10 games; last 5 0, 0, 0, 0, 0 (goals) | reached 1+ goals (anytime goal scorer) in 20 of his last 150 games (13.3%), 5 of last 30, 3 of last 10, 3 of 10 this season
- **Case against.** VAN is a clear underdog tonight (17.4% to win): fewer goals for its skaters on average | for forwards expected to play 12-14 min, model 15%-20% the model averaged 17.0% and 14.2% happened (2,322 cases, z -3.5): the model's 16.9% is recalibrated to 14.2% | a +600 prop is +600 or longer: a small error in the probability is a large relative edge, the margin is widest here, and long prices are more often too short than too long | opponent CAR: expected-goal differential rank 1 of 32, goal differential rank 1; tonight's win probability for VAN: 17.4%
- **History.** any player, model 15%-20%: the model averaged 17.4%; 17.1% happened (9,944 cases; 2025-26 and 2026-27 to date) | forwards, model 15%-20%: the model averaged 17.4%; 17.2% happened (9,454 cases; 2025-26 and 2026-27 to date)
- **Contradiction SEVERE**: GAME_ENVIRONMENT (MODERATE); HISTORY_SAYS_MODEL_RUNS_HIGH (HIGH); LONG_PRICE (HIGH). Calibration HIGH; data 8 of 10 (missing: confirmed lineup, injury news); decision confidence LOW.
- **New action PASS** (new rank 29 of 81, old rank 7).
- **What happened.** CAR 6 at VAN 3. expected 13.3 minutes on ice: held (played 15.4 (+2.1)); expected goals 0.19: information (had 0); VAN win probability 17%: information (VAN 3, CAR 6)
- **Decision: BAD DECISION** (the contradiction was SEVERE). Outcome LOSS. a bad decision that lost: a process failure, not variance. The loss itself was the likelier outcome (Eggy gave the bet 14% to win), so the loss is not what is being judged: the decision is, and the flaws in it were visible before the game. Classification MODEL CALIBRATION: the pre-bet review flagged the model as running high for this kind of bet (history said so before the game) Action: INVESTIGATE.

### 8. Drew O'Connor — GOALS 1+ — +700: LOSS

- **Prices and probabilities.** Price +700 implies 12.5% with the margin included. The hockey/skater model says 15.4%; the estimate the new review uses is 13.5% (history may only lower it); the old engine recorded 15.4%. Model edge at the last observed price: +2.9 pts (the model alone); on the estimate the review uses, +1.0 pts; the old engine's edge was +2.9 pts (neutral uncertain edge, score 0.46518).
- **Most likely outcome.** Drew O'Connor does NOT reach 1+ goals (anytime goal scorer) (86.5%); this is a longshot bet.
- **Thesis (fair priced).** The price (12.5%) and our estimate (13.5%) are within 3% of each other: fair, no edge.
- **Case for.** calibrated model 15.4% (raw 15.9%); estimate used 13.5% (model probability unanchored) vs price-implied 12.5% | expected goals per game 0.17; season 1 in 10 games; last 5 0, 0, 0, 0, 0 (goals) | reached 1+ goals (anytime goal scorer) in 23 of his last 150 games (15.3%), 3 of last 30, 1 of last 10, 1 of 10 this season
- **Case against.** the model expects 0.81 power-play min but he has had 0.04 lately | VAN is a clear underdog tonight (17.4% to win): fewer goals for its skaters on average | for forwards with 0.25-1 expected power-play min, model 15%-20% the model averaged 16.9% and 14.9% happened (3,426 cases, z -3.2): the model's 15.4% is recalibrated to 13.5% | a +700 prop is +600 or longer: a small error in the probability is a large relative edge, the margin is widest here, and long prices are more often too short than too long
- **History.** any player, model 15%-20%: the model averaged 17.4%; 17.1% happened (9,944 cases; 2025-26 and 2026-27 to date) | forwards, model 15%-20%: the model averaged 17.4%; 17.2% happened (9,454 cases; 2025-26 and 2026-27 to date)
- **Contradiction SEVERE**: POWER_PLAY_ASSUMPTION (MODERATE); GAME_ENVIRONMENT (MODERATE); HISTORY_SAYS_MODEL_RUNS_HIGH (HIGH); LONG_PRICE (HIGH). Calibration HIGH; data 8 of 10 (missing: confirmed lineup, injury news); decision confidence LOW.
- **New action PASS** (new rank 28 of 81, old rank 8).
- **What happened.** CAR 6 at VAN 3. expected 15.6 minutes on ice: held (played 16.4 (+0.8)); expected 0.81 power-play minutes: failed (had 0.00); expected goals 0.17: information (had 0); VAN win probability 17%: information (VAN 3, CAR 6)
- **Decision: BAD DECISION** (the contradiction was SEVERE). Outcome LOSS. a bad decision that lost: a process failure, not variance. The loss itself was the likelier outcome (Eggy gave the bet 14% to win), so the loss is not what is being judged: the decision is, and the flaws in it were visible before the game. Classification ROLE CHANGE MISSED: the power-play role the thesis assumed did not exist Action: INVESTIGATE.

### 9. Liam Ohgren — GOALS 1+ — +700: LOSS

- **Prices and probabilities.** Price +700 implies 12.5% with the margin included. The hockey/skater model says 14.9%; the estimate the new review uses is 13.7% (history may only lower it); the old engine recorded 14.9%. Model edge at the last observed price: +2.4 pts (the model alone); on the estimate the review uses, +1.2 pts; the old engine's edge was +2.4 pts (neutral uncertain edge, score 0.45766).
- **Most likely outcome.** Liam Ohgren does NOT reach 1+ goals (anytime goal scorer) (86.3%); this is a longshot bet.
- **Thesis (fair priced).** The price (12.5%) and our estimate (13.7%) are within 3% of each other: fair, no edge.
- **Case for.** calibrated model 14.9% (raw 15.5%); estimate used 13.7% (model probability unanchored) vs price-implied 12.5% | expected goals per game 0.17; season 2 in 10 games; last 5 0, 1, 0, 0, 0 (goals) | reached 1+ goals (anytime goal scorer) in 13 of his last 107 games (12.1%), 5 of last 30, 2 of last 10, 2 of 10 this season
- **Case against.** VAN is a clear underdog tonight (17.4% to win): fewer goals for its skaters on average | for forwards, model 12%-15% the model averaged 13.5% and 12.4% happened (5,598 cases, z -2.4): the model's 14.9% is recalibrated to 13.7% | a +700 prop is +600 or longer: a small error in the probability is a large relative edge, the margin is widest here, and long prices are more often too short than too long | opponent CAR: expected-goal differential rank 1 of 32, goal differential rank 1; tonight's win probability for VAN: 17.4%
- **History.** any player, model 12%-15%: the model averaged 13.5%; 12.6% happened (6,534 cases; 2025-26 and 2026-27 to date) | forwards, model 12%-15%: the model averaged 13.5%; 12.4% happened (5,598 cases; 2025-26 and 2026-27 to date)
- **Contradiction HIGH**: GAME_ENVIRONMENT (MODERATE); HISTORY_SAYS_MODEL_RUNS_HIGH (MODERATE); LONG_PRICE (HIGH). Calibration HIGH; data 8 of 10 (missing: confirmed lineup, injury news); decision confidence LOW.
- **New action PASS** (new rank 18 of 81, old rank 9).
- **What happened.** CAR 6 at VAN 3. expected 15.4 minutes on ice: failed (played 11.1 (-4.3)); expected 1.14 power-play minutes: failed (had 0.00); expected goals 0.17: information (had 0); VAN win probability 17%: information (VAN 3, CAR 6)
- **Decision: BAD DECISION** (no edge, and the contradiction is HIGH / calibration HIGH: nothing to learn from). Outcome LOSS. a bad decision that lost: a process failure, not variance. The loss itself was the likelier outcome (Eggy gave the bet 14% to win), so the loss is not what is being judged: the decision is, and the flaws in it were visible before the game. Classification TOI PROJECTION ERROR: the ice time the thesis assumed did not happen Action: INVESTIGATE.

### 10. Jamie Oleksiak — GOALS 1+ — +2500: LOSS

- **Prices and probabilities.** Price +2500 implies 3.9% with the margin included. The hockey/skater model says 5.7%; the estimate the new review uses is 4.4% (history may only lower it); the old engine recorded 5.7%. Model edge at the last observed price: +1.8 pts (the model alone); on the estimate the review uses, +0.5 pts; the old engine's edge was +1.8 pts (neutral uncertain edge, score 0.41799).
- **Most likely outcome.** Jamie Oleksiak does NOT reach 1+ goals (anytime goal scorer) (95.6%); this is a longshot bet.
- **Thesis (fair priced).** The price (3.8%) and our estimate (4.4%) are within 3% of each other: fair, no edge.
- **Case for.** calibrated model 5.7% (raw 6.1%); estimate used 4.4% (model probability unanchored) vs price-implied 3.8% | expected goals per game 0.06; season 1 in 9 games; last 5 0, 0, 0, 0, 0 (goals) | reached 1+ goals (anytime goal scorer) in 8 of his last 150 games (5.3%), 3 of last 30, 1 of last 10, 1 of 9 this season
- **Case against.** VAN is a clear underdog tonight (17.4% to win): fewer goals for its skaters on average | for defensemen with 0-0.25 expected power-play min, model 4%-6% the model averaged 5.2% and 4.0% happened (1,803 cases, z -2.3): the model's 5.7% is recalibrated to 4.4% | a +2500 prop is +600 or longer: a small error in the probability is a large relative edge, the margin is widest here, and long prices are more often too short than too long | a price of +2500 implies 3.8%: small errors in a tail probability become large relative edges
- **History.** any player, model 4%-6%: the model averaged 5.2%; 5.3% happened (3,595 cases; 2025-26 and 2026-27 to date) | defensemen, model 4%-6%: the model averaged 5.2%; 5.3% happened (3,585 cases; 2025-26 and 2026-27 to date)
- **Contradiction HIGH**: GAME_ENVIRONMENT (MODERATE); HISTORY_SAYS_MODEL_RUNS_HIGH (MODERATE); LONG_PRICE (HIGH); LONGSHOT_TAIL (MODERATE). Calibration HIGH; data 8 of 10 (missing: confirmed lineup, injury news); decision confidence LOW.
- **New action PASS** (new rank 25 of 81, old rank 10).
- **What happened.** CAR 6 at VAN 3. expected 17.5 minutes on ice: held (played 22.9 (+5.4)); expected goals 0.06: information (had 0); VAN win probability 17%: information (VAN 3, CAR 6)
- **Decision: BAD DECISION** (no edge, and the contradiction is HIGH / calibration HIGH: nothing to learn from). Outcome LOSS. a bad decision that lost: a process failure, not variance. The loss itself was the likelier outcome (Eggy gave the bet 4% to win), so the loss is not what is being judged: the decision is, and the flaws in it were visible before the game. Classification MODEL CALIBRATION: the pre-bet review flagged the model as running high for this kind of bet (history said so before the game) Action: INVESTIGATE.

## Vancouver +240, in full

**Why did the old engine bet Vancouver?** Three things, none of them hockey. (1) Its moneyline probability was Elo, which is rebuilt from the current season's results only (no earlier seasons were loaded), so a team's rating after five or six games is mostly noise plus the home-ice constant: Elo had Vancouver at 41.8% against a market of 28.2%. (2) Carolina at -298 is past the -150 floor, so Vancouver was the only eligible side of the game. (3) The old edge class compared the market's no-vig probability with the price's implied probability, a difference that is only the bookmaker's margin on that side (about -1.2 points at +240), which fell inside the +/-2 point NEUTRAL band; every favourite fell outside it. The ranking then promoted NEUTRAL over NEGATIVE.

**What data did it use?** Elo (this season only), the market price, and the -150 floor. **What did it have and ignore?** The MoneyPuck team table, 2008 to 2026: Vancouver ranked 32nd of 32 in decayed goal differential (-1.08 per game) and 31st in expected-goal differential (-0.61); Carolina ranked 1st in both (+0.83, +0.82) and 1st in shot attempts and penalty kill. Vancouver had lost its last five, all on the road. Rest was 3 days against 2. Every one of these was on disk.

**Was the old team-strength state trustworthy?** No: the old engine's own comparison log shows Elo with the worst log loss of every estimate priced so far (0.6726 against 0.6438 for the market, 0.6467 for the hockey model and 0.6456 for the goalie-team strength model, 87 games).

**Was Vancouver genuinely +EV?** There is no evidence it was. The market's no-vig probability was 28.2%; the hockey model said 17.4% (-12.0 pts against the price), the strength model said 28.0%. Only Elo disagreed, and Elo is the least reliable of the four. The recorded expected value used the market's own number, so the bet's modelled edge was the margin: about -1.2 points.

**Was there a defensible hockey explanation?** None on the data: the hockey picture favoured Carolina, CAR wins (82.6% by the hockey model; the market says 71.8% for CAR). The only argument for Vancouver was the price.

**Would the new layer still record it?** No. The thesis is MODEL_SAYS_OVERPRICED (the hockey model puts Vancouver 10.8 points below the market) and the contradiction is SEVERE (the hockey picture is against it; the model calls it overpriced), so the action is PASS, whatever the price. It does not ban underdogs: an underdog the model rates as UNDERpriced is reviewed on the same terms (see the replay in `eggy_moneyline_replay.json`: the new layer passed on 64 of the 85 eligible underdog sides, each a fair-priced underdog or one the model calls overpriced, and kept 21 as lower priority).

**Honest limit.** Carolina won 6-3, which agrees with the review, but one game proves nothing about the model: on the 87 games priced so far the market was a slightly better guide than the hockey model, and the review says so on every card.

## The five Vancouver goal props, and Chytil

All five goal props were Vancouver skaters in one game, so they are one cluster of dependent outcomes (a Vancouver goal), not five independent chances. The old engine concentrated seven of its ten bets in CAR @ VAN because that was the only game with prop prices in the wave and the old diversification penalty was negligible. The new layer orders by quality first and then holds a game to three bets before its further candidates wait behind every other game's.

| Bet | Price implies | Model (raw / calibrated) | Estimate after history | What history said | Expected / actual ice time, power play | Result |
|---|---|---|---|---|---|---|
| Filip Chytil — SHOTS ON GOAL 2+ — -130 | 56.5% | 57.0% / 56.2% | 56.2% | any player, model 50%-60%: the model averaged 54.9%, 57.7% happened (7,668 cases, z +4.9) | 15.5 / 18.7 min; 1.38 / 0.0 PP | WIN (2 shots, 0 goals) |
| Max Sasson — GOALS 1+ — +800 | 11.1% | 16.9% / 16.4% | 13.8% | forwards expected to play 12-14 min, model 15%-20%: the model averaged 17.0%, 14.2% happened (2,322 cases, z -3.5) | 13.1 / 13.7 min; 0.38 / 0.0 PP | WIN (3 shots, 1 goals) |
| Paul Cotter — GOALS 1+ — +600 | 14.3% | 17.4% / 16.9% | 14.2% | forwards expected to play 12-14 min, model 15%-20%: the model averaged 17.0%, 14.2% happened (2,322 cases, z -3.5) | 13.3 / 15.4 min; 0.72 / 0.0 PP | LOSS (0 shots, 0 goals) |
| Drew O'Connor — GOALS 1+ — +700 | 12.5% | 15.9% / 15.4% | 13.5% | forwards with 0.25-1 expected power-play min, model 15%-20%: the model averaged 16.9%, 14.9% happened (3,426 cases, z -3.2) | 15.6 / 16.4 min; 0.81 / 0.0 PP | LOSS (2 shots, 0 goals) |
| Liam Ohgren — GOALS 1+ — +700 | 12.5% | 15.5% / 14.9% | 13.7% | forwards, model 12%-15%: the model averaged 13.5%, 12.4% happened (5,598 cases, z -2.4) | 15.4 / 11.1 min; 1.14 / 0.0 PP | LOSS (0 shots, 0 goals) |
| Jamie Oleksiak — GOALS 1+ — +2500 | 3.9% | 6.1% / 5.7% | 4.4% | defensemen with 0-0.25 expected power-play min, model 4%-6%: the model averaged 5.2%, 4.0% happened (1,803 cases, z -2.3) | 17.5 / 22.9 min; 0.17 / 0.0 PP | LOSS (2 shots, 0 goals) |

Reading: for four of the five goal props (Cotter, O'Connor, Ohgren, Oleksiak) the estimate, once history had lowered it, sat within about 3 points of the price (edges of +1.9 to +2.9 points, under the 3-point threshold), so there was no thesis, and each carried doubts: the length of the price (+600 to +2500), a clear-underdog team, and, for O'Connor, a power-play role that did not exist (expected 0.81 minutes, had 0:00); for Ohgren, ice time that did not happen (expected 15.4 minutes, had 11.1). Sasson, a fourth-line forward at +800, is the closest call: the model's 16.4% was lowered to 13.8% against a price implying 11.1%, an edge of 2.6 points (the model alone says 5.3), below the 3-point threshold, with four doubts (fourth-line usage, a clear-underdog team, history saying the model runs high for such players, and the +800 price): a SEVERE contradiction and a PASS. He scored: the review calls that LUCK, not skill. Oleksiak at +2500 is the longshot the owner flagged: the model said 5.7%, history lowered it to 4.4% (defensemen with almost no power-play time scored 4.0% against the model's 5.2%), the price implied 3.8%, and the price that would have given a 3-point edge is about +6,990. Filip Chytil's 2+ shots at -130 is the best of the ten: the shots model runs LOW (it averaged 54.9% and 57.7% happened over 7,668 cases), so the review does not raise the capped estimate of 56.2% (history may lower an estimate, never lift it) and finds the price, 56.5%, fair: a calibration control, rank 2 of 81 in the new ordering, QUESTIONABLE rather than GOOD because there was no edge. His expected power play (1.4 minutes) did not happen (0:00) and he still reached the line.
