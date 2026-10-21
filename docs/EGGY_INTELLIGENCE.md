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

**Thesis types.** Moneyline: `MODEL_SEES_VALUE` (the hockey model is 3 or more points above the market's no-vig probability), `MODEL_SAYS_OVERPRICED` (3 or more below), `LIKELY_WINNER_AT_FAIR_PRICE`, `UNDERDOG_AT_FAIR_PRICE`. Prop: `MODEL_SEES_VALUE`, `FAIR_PRICED`, `PRICE_ABOVE_ESTIMATE`. The estimate used for expected value on a moneyline is the market's own no-vig probability, because nothing shows a model beating it; Elo plays no part in any decision.

**Contradiction level** is a count, not a weighted sum: one MODERATE flag is MODERATE; two MODERATE or one HIGH is HIGH; two HIGH or any SEVERE is SEVERE. Flags name the quantity they rest on: `HOCKEY_PICTURE_AGAINST`, `MARKET_MAY_KNOW_MORE` (a large model-market gap is suspicion, not confidence), `MARKET_BEAT_THE_MODEL` (the market has had the better log loss on the games priced so far), `MODEL_ABOVE_RECORD`, `ROLE_ASSUMPTION`, `POWER_PLAY_ASSUMPTION`, `LOW_USAGE`, `GAME_ENVIRONMENT`, `HISTORY_SAYS_MODEL_RUNS_HIGH`, `LONGSHOT_TAIL`, `THIN_HISTORY`, `MODEL_FAR_ABOVE_MARKET`, `PRICE_ABOVE_ESTIMATE`.

**Calibration confidence** is HIGH when 1,000 or more historical analogues exist and the model's average sat within two standard errors of what happened (or the estimate was recalibrated to such a cell); MEDIUM at 300 or more; LOW otherwise; INSUFFICIENT with no analogue. A longshot (price implying 5% or less, +1900 and longer) needs HIGH calibration and a 100-game record or it is capped at LOW.

**Recalibration** takes the single strongest cell with 300 or more cases and a gap of two standard errors, in either direction, and scales the model by what happened. It is never a product of overlapping cells. Out of time (cells trained on games before 2026-02-01, scored on 20,970 skater-games after it) it left no market worse and improved all seven slightly (`docs/validation/eggy_prop_calibration_replay.json`): shots 3+ Brier 0.1449 to 0.1441, goals unchanged. It over-corrects some goal bands, so its goal estimates lean conservative.

**Actions.** A PASS is never recorded. A RECORD needs the thesis to survive: for a prop, 3 or more points over the price, contradiction no worse than MODERATE, calibration at least MEDIUM, and a longshot needs HIGH calibration and the long record. A fair-priced prop or favourite with a well-calibrated estimate is LOWER_PRIORITY (a control). A price above the estimate is LOWER_PRIORITY only as a bounded experiment (the event at least 25% likely, the price within 10 points of the estimate, calibration at least MEDIUM) and otherwise a PASS. A moneyline model edge is a RECORD only while the model has led the market on the games priced so far; while the market has been the better guide (the case today: log loss 0.6438 market, 0.6467 hockey model, 0.6456 goalie-team strength, 0.6726 Elo, 87 games) it is a LOWER_PRIORITY experiment, and the card says why. A LOWER_PRIORITY bet is recorded only in the last 25 minutes before its own game and only inside the strict wave caps.

**Ranking** is lexicographic: action, edge class, contradiction, calibration confidence, hit chance to the nearest point, edge, then the bet's key. A game that already holds three of the day's bets sends its further candidates behind every other game's.

**Edge classes** use the main engine's own 3-point threshold (they used 2, which is the root of the underdog bias below). POSITIVE_EDGE needs 3 points and calibration at least MEDIUM; NEGATIVE is 3 points or worse; a moneyline is judged on the hockey model's edge, not the market-anchored estimate.

**Fail closed.** If the evidence file or either state is missing, the cohort records nothing and says why. It never falls back to unreviewed ranking.

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

- The team model has not been shown to beat the market (market log loss 0.6438 against 0.6467 on 87 games; the 95% range for the weight a disagreement deserves is -1.0 to 1.96). It is used to understand the matchup and to scrutinise disagreement, never as proof the market is wrong. Replay by disagreement size found no signal either way (sides the model liked by 3 or more points won 31 against 32.6 expected).
- Starters, lineups and injuries are missing, and the card says so every time. The market knows them.
- Eggy's reviews of past tickets are not kept (the ticket ledger has no review column); the learning book's reviews are.
- Eighty-seven games and a few hundred bets are small samples; same-game bets are one cluster.
- The evidence file is a snapshot (data through 2026-10-20). Re-run `deploy/build_eggy_evidence.py` after the MoneyPuck files change.

## 9. Reproducing the evidence

```bash
python3 deploy/build_eggy_evidence.py                     # the team model, the skater cells, the model-against-market record
python3 deploy/replay_eggy_review.py --instant 2026-10-20T23:33:49Z --date 2026-10-20 --out /tmp/pool.json   # a past candidate pool through the review (sandboxed)
python3 deploy/replay_moneyline_log.py --out /tmp/ml.json      # old class split against new actions on the shadow log
python3 deploy/replay_prop_calibration.py --split 2026-02-01 --out /tmp/props.json   # the recalibration, out of time
```
