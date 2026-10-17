# Prediction evaluation (frozen estimates vs official results)

frozen predictions vs official results; per market and model version; paired Brier difference vs leave-one-game-out climatology and vs market no-vig probability; 95% interval = wider of the game-cluster and player-cluster bootstrap; no retuning.

## MONEYLINE / v2.1.2a: 140 settled of 152
* vs climatology: model Brier 0.2371 vs 0.2500; diff -0.0129 (95% -0.0278 to +0.0024, 70 clusters, n=140): **NO_EVIDENCE_MODEL_BETTER**
* vs market: model Brier 0.2371 vs 0.2266; diff +0.0105 (95% -0.0071 to +0.0278, 70 clusters, n=140): **NO_EVIDENCE_MODEL_BETTER**

## PLAYER_SOG_2PLUS / player_sog_v1: 46 settled of 46
* vs climatology: model Brier 0.2143 vs 0.2669; diff -0.0526 (95% -0.1050 to -0.0009, 4 clusters, n=46): **INSUFFICIENT_SAMPLE**
* vs market: NO_MARKET_PRICES_ON_THESE_ROWS

## PLAYER_SOG_3PLUS / player_sog_v1: 46 settled of 46
* vs climatology: model Brier 0.1644 vs 0.1920; diff -0.0276 (95% -0.0862 to +0.0257, 4 clusters, n=46): **INSUFFICIENT_SAMPLE**
* vs market: NO_MARKET_PRICES_ON_THESE_ROWS

## PLAYER_SOG_4PLUS / player_sog_v1: 46 settled of 46
* vs climatology: model Brier 0.0836 vs 0.0975; diff -0.0138 (95% -0.0540 to +0.0187, 4 clusters, n=46): **INSUFFICIENT_SAMPLE**
* vs market: NO_MARKET_PRICES_ON_THESE_ROWS

## PLAYER_SOG_5PLUS / player_sog_v1: 44 settled of 44
* vs climatology: model Brier 0.0247 vs 0.0234; diff +0.0013 (95% -0.0228 to +0.0147, 4 clusters, n=44): **INSUFFICIENT_SAMPLE**
* vs market: NO_MARKET_PRICES_ON_THESE_ROWS


## How to read this (2026-10-17 snapshot)

* Moneyline v2.1.2a: 140 settled rows over 70 games. The model's Brier score is lower than a coin flip and higher than the market's, and neither difference is distinguishable from zero. **There is no evidence yet that the moneyline model beats the market.** It is not shown as a recommendation source on that basis.
* Player shots: all 46 settled rows come from about 4 games and a handful of players, so no verdict is offered (INSUFFICIENT_SAMPLE) and the apparent advantage over climatology is not evidence. No threshold or model was changed because of these numbers.
* Goals, saves, points and the handicap have no settled frozen predictions yet: they are not evaluated, and this report does not pretend otherwise.
* Regenerate with `python3 -m operational.prediction_evaluation`. Read-only; it never writes the ledger.
