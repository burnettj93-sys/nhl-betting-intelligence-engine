# Eggy intelligence

Eggy decides in this order, for every bet it might record or show, and never the reverse:

> understand the game, then the player, then the market, then why the model disagrees with the market, then argue against the bet, then ask whether the thesis survives, and only then rank and record.

The old learning-book engine ran the opposite way (eligible, edge, sort, bet), and on 2026-10-20 it recorded ten bets that a careful look at the data on disk would have passed on (`docs/validation/eggy_intelligence_oct20.md`). This document says what the replacement reads, what it decides and why, how it is judged afterwards, and what it does not know.

No new API, database, scheduler, ingestion or market was added. Everything below reads files the platform already held.

## 1. The review card

`operational/eggy_intelligence.py` produces one card per candidate bet. It is a plain dict stored with the bet (`intelligence_json`) and shown in the app. Its sections are the ones the owner asked for: the bet (player or team, market, threshold, price, with the market key the provider's archived payload actually carried), the most likely hockey outcome, why tonight, the player or team thesis, the model's probability, the market's probability and the disagreement, why they disagree, the case for, the case against, historical support with sample sizes, the model state, data completeness (present and missing), contradiction level, calibration confidence, final decision confidence and the action (RECORD, LOWER_PRIORITY or PASS).

`answers` restates the card as the thirteen things a reader should get at a glance: why this team or player, why tonight, why this market, why this price, who is most likely to win, what the model believes, what the market believes, why they disagree, what supports it, what argues against it, what history says, what data is missing and what would make Eggy pass. They are assembled from the card's own fields, so the two cannot disagree.

There is **no intelligence score**. A score needs weights, and no history can say what they should be. Every judgment is a rule on a named, printed quantity; where a number can be checked against history it is, with its sample size; where it cannot (is the market wrong about a moneyline?) the card says UNVALIDATED.

## 2. What it reads

| | Inputs |
|---|---|
| **Available and used** | The MoneyPuck team game-by-game file, 2008-09 to date (goals, expected goals, high-danger chances, shot attempts, special-teams time, penalties, finishing), for the team matchup model `eggy-matchup-v1`. The skater game logs (2022-23 to date: ice time, power-play time, shots, goals, points) through the validated skater model and its Platt calibration, with inferred usage tiers and power-play units, hit rates over the last 150, 30 and 10 games, and a recalibration read from 2025-26 history. Rest days and back-to-backs. Both sides of every moneyline price. The one-sided prop prices (their margin is unknown and is said to be included). The moneyline shadow log, for the model-against-market record. The evidence file `docs/validation/eggy_evidence.json`. |
| **Available, ignored by the old engine, used now** | The 18-season team table for moneylines (the old engine used Elo, rebuilt from the current season's results only, plus a goalie-team strength model). Expected-goal and high-danger strength, shot attempts, special teams, rest. Role and power-play time against the player's own recent games (the old engine did not check that the assumed role existed). Calibration by cell: what happened to forwards expected to play 12-14 minutes, to defensemen with no power play, to players on a clear-underdog team. The team's own win probability as the environment for its skaters' goals. |
| **Available, still not used** | Research artifacts that exist but have no validated decision path yet: goalie quality and workload (`research/goalie_intelligence`, `goalie_quality_integration`), shot-quality and special-teams features (`research/moneypuck_*`), period-event timing, the context overlay. The unconfirmed-starter probabilities in the product data. Play-by-play. Each would need its own validation before a decision uses it; none needs new ingestion. |
| **Not available** | Confirmed starting goalies, lineups, injuries and scratches: the Daily Faceoff reader is OFF by policy (its terms restrict automated access; the owner has not opted in). Historical sportsbook prices and closing prices: none were ever stored, so nothing here can be profit evidence and no closing-line value is claimed. |

## 3. The decision rules

**Thesis types.** Moneyline: `MODEL_SEES_VALUE` (the hockey model is 3 or more points above the market's no-vig probability), `MODEL_SAYS_OVERPRICED` (3 or more below), and within 3 points of the market: `COIN_FLIP_AT_FAIR_PRICE` (the model's probability is within 3 points of 50%: no better team to argue for or against), `LIKELY_WINNER_AT_FAIR_PRICE` (the hockey favourite) or `UNDERDOG_AT_FAIR_PRICE` (the hockey underdog). Prop: `MODEL_SEES_VALUE`, `FAIR_PRICED`, `PRICE_ABOVE_ESTIMATE`. The estimate used for expected value on a moneyline is the market's own no-vig probability, because nothing shows a model beating it; Elo plays no part in any decision.

**Contradiction level** is a count, not a weighted sum: one MODERATE flag is MODERATE; two MODERATE or one HIGH is HIGH; two HIGH or any SEVERE is SEVERE. Flags name the quantity they rest on: `HOCKEY_PICTURE_AGAINST`, `MARKET_MAY_KNOW_MORE` (a large model-market gap is suspicion, not confidence), `MODEL_NOT_SHOWN_TO_BEAT_MARKET` (the model's paired log-loss advantage over the market on the games priced so far is not at least two standard errors: a gap of a few thousandths on 87 games is noise, so this does not flip with each day's results), `LONG_PRICE` (the owner's skepticism ladder: a +150 or longer moneyline and a +300 prop are MODERATE, a +300 moneyline and a +600 prop HIGH), `MODEL_ABOVE_RECORD`, `ROLE_ASSUMPTION`, `POWER_PLAY_ASSUMPTION`, `LOW_USAGE`, `GAME_ENVIRONMENT`, `HISTORY_SAYS_MODEL_RUNS_HIGH`, `LONGSHOT_TAIL`, `THIN_HISTORY`, `MODEL_FAR_ABOVE_MARKET`, `PRICE_ABOVE_ESTIMATE`.

**Calibration confidence** is HIGH when 1,000 or more historical analogues exist and the model's average sat within two standard errors of what happened (or the estimate was recalibrated to such a cell); MEDIUM at 300 or more; LOW otherwise; INSUFFICIENT with no analogue. A longshot (price implying 5% or less, +1900 and longer) needs HIGH calibration and a 100-game record or it is capped at LOW.

**Recalibration can only lower an estimate.** The estimate is the leg's own (for shots, the player's record held to the model plus at most 0.02: the anchor policy's cap, which stays). Where the single strongest cell with 300 or more cases and a gap of two standard errors says the model ran HIGH for players like this, the estimate is lowered to what happened (never below a quarter of the model's number, and the standard error is the one under what the model said, so a cell with no events cannot produce a near-zero error). Where history says the model ran LOW, the estimate is NOT raised: a recalibration that lifted an estimate above the leg's own number would manufacture an edge the capped estimate does not have, and the card says so (`HISTORY_SAYS_MODEL_RUNS_LOW`, level LOW). Out of time (cells trained on games before 2026-02-01, scored on 20,970 skater-games after it) the lowering rule is calibration-neutral: Brier moves by no more than 0.00003 in six of the seven markets and improves points 1+ by 0.00014 (`docs/validation/eggy_prop_calibration_replay.json`). An earlier symmetric version improved shots 3+ by 0.0008, but only by raising estimates above the cap, which is not allowed; it over-corrected some goal bands in both directions.

**Calibration confidence** treats a model that ran LOW in a cell as conservative, not as miscalibrated (a capped estimate cannot be overstated by it), and a model that ran HIGH two-sidedly; the card says so. For a lowered estimate it is HIGH only when a cell specific to the player (his ice time, his power play, his team's environment) has 1,000 or more cases; the population-level probability band alone gives MEDIUM, because it vouches for the model in the band and not for him.

**Actions.** A PASS is never recorded. A RECORD needs the thesis to survive: for a prop, 3 or more points over the price, contradiction no worse than MODERATE, calibration at least MEDIUM, and a longshot needs HIGH calibration and the long record. A fair-priced prop or favourite with a well-calibrated estimate is LOWER_PRIORITY (a control). A price above the estimate is LOWER_PRIORITY only as a bounded experiment (the event at least 25% likely, the price within 10 points of the estimate, calibration at least MEDIUM) and otherwise a PASS. A moneyline model edge is a RECORD only once the model has led the market on the games priced so far by at least two standard errors (paired per-game log loss); until then it is a LOWER_PRIORITY experiment, and the card says why. On the 87 games priced so far the market was slightly ahead (log loss 0.6438 market, 0.6467 hockey model, 0.6456 goalie-team strength, 0.6726 Elo; paired difference z = -0.2). A LOWER_PRIORITY bet is recorded only in the last 25 minutes before its own game and only inside the strict wave caps.

**Ranking** is lexicographic: action, edge class, contradiction, calibration confidence, hit chance to the nearest point, edge, then the bet's key. A game that already holds three of the day's bets sends its further RECORD candidates behind every other game's; a LOWER_PRIORITY candidate is simply not recorded past three, and a game holds at most one negative-edge experiment a day (fewer than ten bets is a correct answer).

**Edge classes** use the main engine's own 3-point threshold (they used 2, which is the root of the underdog bias below). POSITIVE_EDGE needs 3 points and calibration at least MEDIUM; NEGATIVE is 3 points or worse; a moneyline is judged on the hockey model's edge, not the market-anchored estimate.

**Fail closed.** If the evidence file is missing or more than 21 days old, or either state is missing or its newest game is more than 3 days before the date being reviewed, the cohort records nothing and says why. It never falls back to unreviewed ranking. The evidence file's hash is part of the policy digest and is named on every card, so a rebuilt file needs a fresh approval and bets made under different evidence can be told apart. An engine error in one candidate's review costs only that candidate and is reported as an error (never as "passed on judgment").

## 4. The underdog bias, and its mathematical reason

The old engine's edge on a moneyline was the market's no-vig probability less the price's implied probability. For a proportional margin that is `-p x (overround - 1)`: the side's own share of the margin, linear in its probability, with no hockey in it. On the 87 games the shadow log priced the mean overround was 1.0433, so the class flips at a no-vig probability of 46.2%. Of the 133 eligible sides (-150 or better), every one of the 48 favourites was classed NEGATIVE; 66 of the 85 underdogs were NEUTRAL, the preferred class. The ranking then promoted NEUTRAL over NEGATIVE, the -150 floor removed the heavy favourites, and Elo (the worst-calibrated estimate) was the "model" probability. Underdogs were favoured by arithmetic alone. The fix does not ban underdogs: an underdog the hockey model rates as underpriced is reviewed on the same terms and can be recorded or held lower, with the hockey picture declared against it. Detail: `docs/validation/eggy_moneyline_replay.json`.

## 5. Judging the decision, apart from the outcome

Every bet has two results: the OUTCOME (win, loss, push) and the DECISION (GOOD, QUESTIONABLE, BAD, INSUFFICIENT_EVIDENCE). `operational/eggy_decisions.py` judges the decision from the pre-bet card alone, then uses the outcome only to check the card's factual claims (the ice time, the power-play role, who controlled the game), to ask whether a loss is NORMAL_VARIANCE, and to say what Eggy should learn.

| | Won | Lost |
|---|---|---|
| **Good decision** | The expected reward of a sound process; confidence unchanged | NORMAL_VARIANCE, only if earned (below); otherwise a named cause |
| **Questionable** | No reward; confidence not increased | No punishment for the loss; the doubts were real |
| **Bad** | LUCK: confidence must not rise, the process still needs fixing | A process failure; the loss itself is not blamed, the decision is |

NORMAL_VARIANCE is earned only when the thesis was defensible, calibration was at least MEDIUM, no data integrity defect exists, no factual claim failed, and the outcome was not rarer than 1 in 10 by Eggy's own estimate; the postmortem then states each of those. A specific cause the box score shows (the ice time that did not happen, the power play that did not exist) is never overruled. A bet recorded before the review existed is reconstructed from its own day's data and labelled RECONSTRUCTED.

## 6. Where it shows

Opening any learning-book bet on the Paper Performance page, or any leg of Eggy's pick or best candidate on the Slate Board, shows the card and, after the game, the two results, what happened, whether each claim in the thesis held, what to learn, the classification and the action. Every bet is named completely everywhere: `Filip Chytil — SHOTS ON GOAL 2+ — -130`, `Jamie Oleksiak — GOALS 1+ — +2500`, `VAN — MONEYLINE — +240` (`operational/bet_labels.py`).

## 7. What did not change

The 10-bet learning book: 10 a day as a target, $10 each, -150 or better, 14 Eastern days, the $500 paper bankroll with logged resets that are funding and never profit, price age as metadata, automatic settlement, and the owner's recording pause. Eggy's own selection and policy digest (one official pick a day) are untouched: its legs gain a review card for display, and a leg the review would pass is shown as a pass rather than hidden.

## 8. Limits

- The team model has not been shown to beat the market (market log loss 0.6438 against 0.6467 on 87 games; the 95% range for the weight a disagreement deserves is -1.2 to 1.96). It is used to understand the matchup and to scrutinise disagreement, never as proof the market is wrong. Replay by disagreement size found no signal either way (sides the model liked by 3 or more points won 31 against 32.6 expected).
- Starters, lineups and injuries are missing, and the card says so every time. The market knows them.
- Eggy's reviews of past tickets are not kept (the ticket ledger has no review column); the learning book's reviews are.
- Eighty-seven games and a few hundred bets are small samples; same-game bets are one cluster.
- The evidence file is a snapshot (data through 2026-10-20). Re-run `deploy/build_eggy_evidence.py` after the MoneyPuck files change; the layer refuses to run on an evidence file more than 21 days old.
- The half-life (30 games) and shrink (10 games) of the team model are fixed constants chosen in exploratory work on 2014-2016; the builder re-derives only the feature set.
- A replayed decision uses the evidence file as it stands today, which includes the replayed day's own results (the skater cells by under 1% of their rows, the model-against-market record by one day of 87 games); the significance rule keeps that from flipping a decision on noise.
- `model_probability` on a moneyline row is Elo for the four Oct 20 bets and the first Oct 21 bet (recorded before this release) and the hockey-model probability afterwards.

## 9. Reproducing the evidence

```bash
python3 deploy/build_eggy_evidence.py                     # the team model, the skater cells, the model-against-market record
python3 deploy/replay_eggy_review.py --instant 2026-10-20T23:33:49Z --date 2026-10-20 --out /tmp/pool.json   # a past candidate pool through the review (sandboxed)
python3 deploy/replay_moneyline_log.py --out /tmp/ml.json      # old class split against new actions on the shadow log
python3 deploy/replay_prop_calibration.py --split 2026-02-01 --out /tmp/props.json   # the recalibration, out of time
```

## 10. Learning parlays (the learning unit, 2026-10-21)

The reviewed candidate legs are the raw material; the learning unit is a PARLAY (`operational/learning_parlays.py`, recorded in the cohort ledger's `parlays` table, up to 10 a day, none forced). No new scoring framework, data, API, scheduler or database.

- **A leg qualifies** only if the hockey/player review survives it (contradiction LOW or MODERATE once the straight bet's price-versus-estimate flags are set aside, calibration at least MEDIUM) and the event is likely (estimate 55% or more). No lottery legs; no leg is added to lengthen the payout.
- **3 legs are preferred.** A 4th is added only if it independently passes the same review, is at least as strong as the ticket's weakest leg, and adds no strong dependence.
- **Weakest leg** = the lowest estimate. It sets the quality: GOOD needs it at 62% or better and at most one MODERATE doubt; BAD (any leg under 55% or a HIGH doubt) is a PASS.
- **Dependence** comes from the existing `eggy_candidates.classify_pair`. Different games: ASSUMED INDEPENDENT ABSENT KNOWN SHARED DEPENDENCE (an assumption, not a finding), probability = product of the leg estimates. Two legs of one game are never multiplied: the combined probability is NOT GIVEN, only assumption-free Frechet bounds are, and an unclear pair (opposing players) is labelled UNCERTAIN. One player twice, a goal and a point, or both sides of a game are refused.
- **Price.** No combined sportsbook price exists anywhere in the system, so every parlay is an UNPRICED LEARNING PARLAY. The product of the legs' own quoted prices is kept as a labelled reference, never a quote; nothing is staked and no return or ROI is claimed. The ticket is settled by its legs (HIT / MISS / VOID / UNRESOLVED) and records the leg(s) that killed it. The $500 bankroll is untouched by parlays.
- **Eggy's flagship** is the strongest GOOD ticket of the day by the same order (quality, fewer legs, strongest weakest leg, likelier). It is shown as a proposal only; Eggy's official pick and its policy digest are unchanged until the owner approves wiring it in.
- **Postmortem**: a PARLAY SUMMARY (legs, priced or not, hit or miss, weakest leg, killers, GOOD / QUESTIONABLE / BAD PARLAY judged from the construction, never the result) sits above the unchanged leg-level reviews, which every leg keeps.
- **No price floor on a leg** (owner 2026-10-21). A leg qualifies on the review, calibrated probability (>= 0.55, calibration >= MEDIUM), role/data completeness, non-price contradiction <= MODERATE, then weakest-leg quality and dependence. Its price is stored and enters the ticket's reference product; the +100 objective belongs to a finished PRICED parlay. The only price check is validity (an American price inside (-100, +100) is impossible). No extreme-price bound is added: an invented cut would be arbitrary and the estimate and review already carry the risk.
