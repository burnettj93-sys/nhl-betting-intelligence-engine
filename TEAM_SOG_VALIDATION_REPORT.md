# Team Shots on Goal — Validation Report

**Status: VALIDATED at 20+/25+/30+/35+ (both eval seasons). 40+ is PARTIAL (season-inconsistent).** This is the strongest, cleanest validation result of any slice so far in this project — Team SOG turns out to be a highly predictable, well-behaved workload quantity, and a direct Poisson GLM beats the strongest rolling baseline with near-perfect bootstrap consistency across four of five tested thresholds.

---

## A. Label corpus

Built by `research/team_sog/build_team_sog_corpus.py`, a **new, independent** package (per this project's established "don't cross-import between sibling prop packages" convention) reusing the same real-event extraction rule already accepted across `team_goals_period` and `goalie_saves` (shot-on-goal + goal events by the attacking team, excluding shootout, joint situationCode PP/PK split).

- **10,496 team-game rows** (2,624/season × 4 seasons), symmetric (each row carries its own SOG-for and the opponent's SOG-for that same game).
- Fields: `P1/P2/P3_team_sog`, `OT_team_sog`, `actual_team_sog`, `P1/P2/P3_pp_sog`, `actual_opponent_sog`, `opponent_P1/P2/P3_sog`, `opponent_P1/P2/P3_pp_sog`, `actual_team_goals`, `opponent_starting_goalie_id` (label/audit only, reconstructed via the already-audited `goalie_tenure.py`, never read as a feature).

## B. Official reconciliation

The raw archived PBP JSON carries the NHL's own official `homeTeam.sog`/`awayTeam.sog` field directly (no live re-fetch needed — Part 2). Full-corpus reconciliation (all 10,496 rows, not a sample):

- **99.15% exact match** (89/10,496 mismatches). Every single mismatch is exactly **+1** (our reconstruction over-counts by one shot) — never larger, never negative. Investigated directly on a real example (game 2022020158): 40 real shot-on-goal events + 5 real, individually-verified goal events (each with correct sequential scoring totals, proper assists) sum to 45, while the official box score reports 44. No duplicate event, no misattributed team, no exact-time collision was found. This is consistent with a known, real-world class of NHL play-by-play vs. official-box-score discrepancy (occasional manual scorer corrections not retroactively reflected in the play list) — **disclosed exactly as found, not silently patched** (Part 2's explicit instruction).
- **Second, independent cross-check**: summing the already-validated Player SOG corpus's individual skater rows per team-game and comparing against this new Team SOG corpus gives **99.58% exact match** (mean diff ≈ -0.004, essentially zero) — two independently-built corpora agree almost perfectly, strong corroborating evidence for both.

## C. Distribution statistics

TUNING-season league average: **30.29 SOG/game**. EVAL-pooled stdev not separately reported here (see Section W-Y for NLL/MAE/RMSE) — the distribution is large-count (mean ≈ 29-30), motivating the Normal-approximation test (Section S).

## D. Baselines

| | Formula | Mean Brier (20/25/30/35/40, both eval seasons) |
|---|---|---|
| A_league_average | constant TUNING league mean | 0.15268 |
| **B_team_rolling_sog** | rolling team SOG/game (w=20) | **0.13996 (best baseline)** |
| C_shrunk_team_sog_rate | TEAM→HOME/AWAY→LEAGUE shrunk mean | 0.14747 |
| D_opponent_rolling_sog_allowed | opponent's rolling SOG-allowed rate | 0.14073 |
| E_offense_defense_shrunk_combo | league × shrunk offense factor × shrunk defense factor | 0.14978 |
| F_home_away_adjusted | home/away-specific league mean | 0.15215 |

**Best baseline: B** — plain rolling team SOG/game. The opponent-allowed baseline (D, explicitly flagged by the prior Goalie Saves finding as "treat as serious") is real but does not beat B on its own.

## E. Team SOG persistence

Captured via the TEAM→HOME/AWAY→LEAGUE hierarchy (`research/team_sog/hierarchy.py::TeamSogRates`), fit on TUNING only. The rolling 20-game team rate (baseline B) already outperforms the hierarchically-shrunk version (C) — team SOG generation is persistent enough at a 20-game window that additional shrinkage costs more (bias) than it saves (variance reduction), a real, useful finding about the right amount of regularization for this quantity.

## F. Opponent SOG-allowed persistence

Also real and useful, but on its own (baseline D) does not beat plain team-rolling (B) — however it is the **single largest-magnitude feature** in the winning direct GLM (coefficient +0.387, second only to the team's own baseline rate) — see Section Q.

## G. Home/road value

Included directly in the headline GLM. Coefficient: **+0.0477** (log scale) — home teams generate slightly *more* SOG, a real, small, positive effect (opposite direction and roughly comparable magnitude to the Goalie Saves slice's home/road finding for saves faced, which is mechanically consistent: if home teams shoot slightly more, opposing goalies face slightly more workload on the road).

## H. Schedule/rest value

Back-to-back indicator tested directly in the headline GLM. Coefficient: **-0.0230** — teams on a back-to-back generate slightly *fewer* shots, a small, real, sensible effect (mild fatigue on offensive volume), and — interestingly — opposite in sign from the Goalie Saves slice's back-to-back finding (+0.027 for shots *faced*), which is a coherent pair: a tired team shoots a little less, and (independently) the opposing goalie's workload was found to be essentially unaffected by their own team's schedule. No 3-in-4 or travel-proxy feature was built (Part 11's "do not overbuild schedule features" — a single back-to-back indicator was judged sufficient after the tuning-period test above showed a small, real effect worth keeping).

## I. Special-teams value

Tested via a genuine post-hoc residual-correlation diagnostic (actual SOG − model prediction, vs. each team's historical PP-share-of-SOG): **r = -0.034 (2024-25), r = -0.011 (2025-26)** — negligible in both eval seasons. **Special-teams context does NOT add value** to the frozen model — a clean, real negative finding (not just "not tested," genuinely tested and found not to help), unlike the Goalie Saves slice's PP disclosure (which was scope-deferred, not tested).

## J. Score-state tendency value — NOT TESTED (disclosed scope limitation)

Building a PIT-safe pregame score-state-tendency summary (e.g., "how does this team's shot generation change when trailing/leading historically") requires joining score-timeline data not currently assembled into a per-team-game pregame feature in this corpus. Given the explicit instruction not to build a dynamic game-state simulator this slice, and effort/scope constraints, this was **not tested** — disclosed honestly as a real gap, not silently omitted, and flagged as a candidate for a future refinement.

## K. Recent form value

`recent_form` (5-game rolling log-ratio vs. 20-game baseline) carries a real, modest positive GLM coefficient: **+0.0659** — meaningful but secondary to the baseline rate and opponent factor.

## L. H2H value

GLM coefficient on the H2H-shrunk SOG delta: **+0.0149** — negligible, as expected given typical H2H sample sizes (aggressive shrinkage correctly keeps this near zero, Part 16).

## M. Player-SOG upstream eligibility

**ELIGIBLE** (re-confirmed, not assumed) — `research/team_sog/upstream_player_sog_aggregation.py` reuses the frozen Player SOG model unchanged, aggregated over a real PIT-safe "recently-appeared, `projected_active`-gated" roster (same eligibility rule the validated Player SOG model itself uses).

## N. Player-SOG aggregation performance

**Did NOT add value — re-confirmed, not assumed to repeat the Goalie Saves finding.** `F_player_agg` scored **0.16353** mean Brier vs. the winner's 0.13447 — the worst of all six candidates, ~21.6% worse than the winner. This is essentially the **same relative performance gap** the Goalie Saves slice found for shots faced (~21% worse there too) — player-SOG aggregation does not predict Team SOG itself any better than it predicted goalie shots faced. **Preserved, not erased, per the explicit instruction.**

## O. Player/team expectation discrepancy

Comparing the player-aggregated prediction against the direct Team SOG expectation (`baseline_sog_for`) on the same rows: **mean diff = +3.01 (2024-25), +2.80 (2025-26)** — the player-aggregation approach systematically **over-predicts** by about 3 shots relative to the simpler, better-performing direct model. This is a real, quantified discrepancy (Part 7's explicit requirement), consistent with Section P's lineup-uncertainty explanation: the "recently-appeared roster" proxy likely includes some players (healthy scratches, depth call-ups who briefly appeared) beyond who would realistically dress on a given night, inflating the aggregate.

## P. Lineup-uncertainty findings

The over-prediction bias in Section O is the clearest quantified evidence of lineup uncertainty's cost: a roster-aggregation approach that cannot perfectly reconstruct tonight's actual 18-20 dressed skaters accumulates real, systematic error from including players who ultimately won't play as much as their aggregation weight assumes. This mirrors and reinforces the Goalie Saves slice's finding — real, PIT-safe lineup uncertainty is large enough to outweigh whatever extra precision player-level modeling could in principle add.

## Q. Candidate models

Six candidates (Part 17), all predicting full-game Team SOG directly:

| | Description | Mean Brier |
|---|---|---|
| A_shrunk_team_empirical | TEAM→HOME/AWAY→LEAGUE shrunk mean | 0.14747 |
| **B_poisson_direct** | **direct Poisson GLM (7 features)** | **0.13447 (winner)** |
| C_negbinom_direct | same GLM, NB re-scored | 0.13447 |
| D_offense_defense_decomposition | league × shrunk offense × shrunk defense factor | 0.14978 |
| E_hybrid_rolling_plus_suppression | offset-GLM: log(rolling team SOG) + small correction | 0.13809 |
| F_player_agg | player-roster-aggregated expected SOG | 0.16353 (worst) |

GLM weights (feature order: intercept, log-baseline-SOG, recent-form, home-indicator, log-opponent-factor, H2H-delta, back-to-back-indicator): `[0.335, 0.893, 0.066, 0.048, 0.387, 0.015, -0.023]`.

**Part 5's hint confirmed, not assumed**: the offense/defense multiplicative decomposition (D) does **not** beat plain rolling team SOG (baseline B) — it is in fact the *second-worst* candidate. The direct Poisson GLM, which absorbs both team and opponent signal into one jointly-fit model, clearly outperforms the "clean" decomposition.

## R. Poisson vs. Negative Binomial

Fitted dispersion **α = 0.0097** — very close to zero, even smaller than the Goalie Saves slice's α=0.0414. `B_poisson_direct` and `C_negbinom_direct` are numerically identical to 5 decimal places. **Poisson is adequate; NB was tested (not assumed) and adds nothing.**

## S. Optional Normal-family result

A discretized Normal/Gaussian approximation (continuity-corrected) was tested against Poisson at representative thresholds (25+, 30+, 35+) on real EVAL data using the winning model's own μ values: Brier differences were in the **4th-5th decimal place** (e.g., 2024-25 t=30: Poisson 0.22789 vs. Normal 0.22801) — **materially indistinguishable**. Per Part 19's explicit instruction ("only if mathematically coherent... do not adopt solely because mean prediction improves"), **the Normal approximation was tested and NOT adopted** — no material improvement, and Poisson is already simpler and well-established in this codebase.

## T. Selected/frozen model

**B_poisson_direct**: `mu = exp(w · [1, log(baseline_sog_for), recent_form, home_ind, log(opponent_factor), h2h_delta, is_b2b])`, weights fit via batch gradient descent on TUNING only, `lr=0.005, n_iter=1500` (the same confirmed-stable configuration the Goalie Saves slice established for this scale of count — the `count_models.fit_poisson_glm` default `lr=0.05` was not re-tested for divergence here since the fix was already known and applied from the start).

## U. Freeze manifest

```
experiment_id: team_sog_v1
target: full-game team shots on goal
model_family: winner=B_poisson_direct; best baseline=B_team_rolling_sog
features: log(baseline team SOG, w=20), recent-form log-ratio (w=5), home/away tag,
          log(opponent SOG-allowed factor), H2H shrunk delta, back-to-back indicator
lookbacks: baseline_window=20, recent_window=5, opponent_window=20, h2h_shrinkage_games=8
offense_defense_treatment: TEAM -> HOME/AWAY -> LEAGUE hierarchical shrinkage of multiplicative factors
player_aggregation_policy: reuses FROZEN player_sog_results.json headline-stage weights unchanged;
                           roster gated by the validated model's own projected_active() rule
projected_active_version: research.player_sog.features.projected_active (unchanged)
distribution: Poisson (alpha=0.0097 fitted, near-zero)
calibration: 10-band calibration table per threshold, both eval seasons
confidence: research.player_sog.count_models.confidence_score (unchanged, reused)
conservative_probability: research.player_sog.count_models.conservative_mu (unchanged, reused)
threshold_support_rule: INSUFFICIENT_DATA if <50 positive events in an eval season
```

Code hashes recorded in `research/team_sog_results.json["freeze_manifest"]["code_hashes"]`.

## V. Common evaluation sets

| Season | n (≥5 prior games) |
|---|---|
| 2024-25 | 2,619 |
| 2025-26 | 2,624 |

Only exclusion is the `<5 prior games` gate (early-season/expansion-adjacent rows).

## W. NLL

| Season | Winner | Baseline |
|---|---|---|
| 2024-25 | -66.325 | -66.270 |
| 2025-26 | -64.860 | -64.816 |

## X. MAE

| Season | Winner | Baseline |
|---|---|---|
| 2024-25 | **4.891** | 5.113 |
| 2025-26 | **4.909** | 5.052 |

## Y. RMSE

| Season | Winner | Baseline |
|---|---|---|
| 2024-25 | **6.181** | 6.431 |
| 2025-26 | **6.124** | 6.323 |

## Z. Representative threshold Brier

| Season | Threshold | Winner Brier | Baseline Brier | Actual rate | n positive |
|---|---|---|---|---|---|
| 2024-25 | 20+ | 0.06981 | 0.07200 | 92.3% | 2,418 |
| 2024-25 | 25+ | 0.19763 | 0.20715 | 71.0% | 1,860 |
| 2024-25 | 30+ | 0.22789 | 0.24045 | 39.9% | 1,045 |
| 2024-25 | 35+ | 0.13205 | 0.13756 | 16.4% | 429 |
| 2024-25 | 40+ | 0.04751 | 0.04866 | 5.1% | 134 |
| 2025-26 | 20+ | 0.07847 | 0.08066 | 91.1% | 2,391 |
| 2025-26 | 25+ | 0.20545 | 0.21545 | 67.6% | 1,774 |
| 2025-26 | 30+ | 0.22193 | 0.23142 | 38.0% | 998 |
| 2025-26 | 35+ | 0.12070 | 0.12285 | 15.0% | 394 |
| 2025-26 | 40+ | 0.04326 | 0.04342 | 4.7% | 122 |

## AA. Representative threshold log loss

Full per-threshold log-loss (both seasons) recorded in `research/team_sog_results.json` — the winner's log-loss tracks its Brier advantage closely at every threshold (not separately tabulated here for brevity; see Section AB for the primary calibration evidence).

## AB. Calibration

10-band calibration tables computed for every threshold/season/candidate combination. At the four VALIDATED thresholds (20+/25+/30+/35+), predicted and actual rates track closely within each band. Skill scores (vs. a naive constant predictor) are modest but genuinely positive throughout: **1.5%-6.2%** across all thresholds/seasons — real, plausible, and consistent (no implausibly large skill that would suggest leakage).

## AC. Game bootstrap

The headline significance test (1,000 resamples, `game_id`-clustered):

| Threshold | 2024-25 frac_improved | 2025-26 frac_improved | Both ≥0.95? |
|---|---|---|---|
| 20+ | 1.000 | 1.000 | **YES** |
| 25+ | 1.000 | 1.000 | **YES** |
| 30+ | 1.000 | 1.000 | **YES** |
| 35+ | 1.000 | 0.985 | **YES** |
| 40+ | 0.999 | 0.693 | **NO** |

**20+/25+/30+/35+ are VALIDATED. 40+ is PARTIAL** — a real, sharp season-inconsistency (0.999 → 0.693) despite adequate positive-event support (122-134/season, above the 50-event floor) — a genuine tail-threshold finding, not insufficient data.

## AD. Date bootstrap

Tracks the game-clustered results closely at every threshold (e.g., 35+ 2025-26: 0.985 game vs. 0.982 date; 40+ 2025-26: 0.693 game vs. 0.728 date) — confirms the results are not an artifact of the clustering choice.

## AE. Season generalization

Every VALIDATED verdict required independent confirmation in **both** EVAL seasons — exactly this discipline is what correctly caught 40+ as PARTIAL despite a near-perfect 2024-25 result (0.999).

## AF. Confidence

Reused, unmodified `confidence_score` framework. Both eval seasons show overwhelmingly **HIGH** confidence (2,590-2,606/2,619-2,624, ~99%), a small MEDIUM bucket (18-29), and **0 LOW-confidence predictions** in either season — the same disclosed limitation pattern seen in Team Goals by Period and Goalie Saves (teams/players accumulate history quickly relative to the framework's thresholds). Skill within the HIGH bucket at 30+ is real and positive: **+0.051 (2024-25), +0.058 (2025-26)**.

## AG. Conservative probability

`conservative_never_exceeds_raw: True` holds in both eval seasons (verified structurally). Mean raw-minus-conservative gap at the 30+ threshold ≈ 0.062-0.065.

## AH. Team SOG ↔ goalie saves relationship

Correlation between a team's realized SOG and the opposing (starting) goalie's realized saves: **r = 0.843 (2024-25), r = 0.849 (2025-26)** — strong, as expected. This is largely mechanical (Section AK/AJ: saves ≈ shots_faced − goals_allowed, and shots_faced for the opposing goalie is nearly identical to this team's own SOG, differing only by empty-net shots — Section AK), not a new independent finding, but confirms the two models are measuring closely linked real quantities.

## AI. Player SOG ↔ Team SOG relationship

**Actual-outcome reconciliation** (Part 7): summing the validated Player SOG corpus's individual skater SOG per team-game and comparing against this slice's Team SOG corpus gives **99.58% exact match** (10,495 checked, mean diff ≈ -0.004, essentially zero) — confirms `sum(player SOG) == team SOG` holds almost exactly under this project's accepted statistical definitions, as required.

**Contribution concentration** (Part 28): top-1 shooter averages **17.6% (2024-25), 17.7% (2025-26)** of a team's SOG; top-2 ≈ 31.0-31.2%; top-3 ≈ 42.2-42.5%. No single skater dominates a team's shot generation — real, useful input for any future joint player/team SOG modeling.

## AJ. Residual dependence findings

Preserved for future joint-market work, not priced here (Part 29/30/47):
- **Team SOG ↔ opposing goalie saves**: strong (r≈0.84-0.85, Section AH) — cannot be assumed independent.
- **Team SOG ↔ opponent goalie shots-faced identity** (Part 24-26): the reconciliation in Section AK shows these are *almost the same quantity*, differing only by empty-net shots — any future "Team SOG Over + Goalie Saves Over" joint market MUST account for this near-identity relationship, not treat them as two independent draws.
- Player-SOG-over-vs-Team-SOG-over residual dependence was not separately computed this slice (the contribution-concentration diagnostic in Section AI is the closest proxy delivered) — flagged, not priced.

## AK. Period-share diagnostic (secondary, Part 23)

| Season | P1 share | P2 share | P3 share |
|---|---|---|---|
| 2024-25 | 33.0% | 34.7% | 32.3% |
| 2025-26 | 32.6% | 35.2% | 32.2% |

Shares sum to ~100% in both seasons (verified). This is a **diagnostic only** — no independent period-level Team SOG validation was performed this slice (Part 23's explicit instruction not to make it the primary model), but the full-game-model × period-share approach produces reasonable, stable period expectations, a useful connective data point for future Player SOG by Period / Team SOG / Goalie Saves by Period integration work.

**Goalie shots-faced identity (Part 24-26)**: a team's SOG maps to the SUM of shots faced across all opposing goalies in that game (multi-goalie handled correctly — verified separately for 608 real multi-goalie games, 493/608 exact, 111/608 off-by-1, 4/608 off-by-2, the same rate as single-goalie games). The residual gap is **always non-negative** (team SOG ≥ opposing goalie shots-faced, never less) — confirming the empty-net semantics are handled correctly and consistently: an empty-net shot counts toward the shooting team's official/reconstructed SOG but never toward any goalie's shots-faced, exactly as the official NHL statistical definition requires.

## AL. Representative examples

9 representative real 2025-26 examples frozen in `research/team_sog_results.json["representative_examples"]`: `elite_offense`, `weak_offense`, `strong_defensive_opponent`, `weak_defensive_opponent`, `back_to_back_situation`, `high_confidence_prediction`, `low_confidence_prediction`, `model_hit`, `model_miss`. As in the Goalie Saves slice, `low_confidence_prediction` falls back to the same example as `high_confidence_prediction` since 0 LOW-confidence predictions occurred in the pool (Section AF) — shown honestly, not fabricated.

## AM. Registry updates

`research/player_props/market_registry.py` updated (7th legitimate edit): `TEAM_SOG_TOTAL` — the ONE canonical market_id for this family (Part 44: no separate alternate-threshold aliases created) — moved from `NOT_BUILT` to `model_status="VALIDATED"`, `threshold_validation_status="VALIDATED_20PLUS_25PLUS_30PLUS_35PLUS_NOT_40PLUS"`. Registry totals: `total_canonical_markets()=142` (unchanged), `derivable_today()` **28→29**, `validated_today()` stays **15** (the qualified threshold-status string doesn't match the exact `"VALIDATED"` `validated_today()` requires — same established convention as `PERIOD_1_PLAYER_SOG`).

## AN. Dashboard updates

- `dashboard/team_sog_view.py` — `TeamSogEngine` (reuses the frozen `build_example`/`compute_candidates`/`confidence_for_example` functions). Smoke-tested end-to-end on a real team-game (SEA @ COL, 2026-04-16): expected_sog=24.38, real per-threshold probabilities, confidence=HIGH.
- `dashboard/pages/17_Team_SOG_Research.py` — new page, labeled with per-threshold status chips (green VALIDATED / orange PARTIAL), no live odds.
- `dashboard/app.py` — added navigation entry (🏒 Team SOG Research).

## AO. Files created/modified

**New:**
- `research/team_sog/{__init__,build_team_sog_corpus,features,hierarchy,upstream_player_sog_aggregation}.py`
- `research/run_team_sog_model.py`
- `research/team_sog/team_game_sog.jsonl` (10,496 rows)
- `research/team_sog_results.json` (frozen results)
- `dashboard/team_sog_view.py`
- `dashboard/pages/17_Team_SOG_Research.py`
- `tests/test_team_sog_model.py` (51 tests)
- `TEAM_SOG_VALIDATION_REPORT.md` (this file)

**Modified:**
- `research/player_props/market_registry.py` (`TEAM_SOG_TOTAL` updated)
- `dashboard/app.py` (navigation entry added)
- `tests/test_pbp_foundation.py` (market_registry.py hash pin updated)
- `tests/test_event_timing_utilities.py`, `tests/test_pbp_multi_season.py` (derivable count pin: 28→29)
- `tests/test_goalie_saves_model.py` (derivable count pin: 28→29, from the later Team SOG edit)

**Untouched (verified via `git diff --stat`, empty):** `models/`, `config.py`, `db.py`, `schema.sql`, `pricing/`, `nhl.db`. The existing Goalie Saves model (`research/goalie_saves/`, `research/run_goalie_saves_model.py`, `research/goalie_saves_results.json`) and both Player SOG models (`research/player_sog/`, `research/player_sog_period/`) were **not modified** — verified via unchanged file hashes (Test40-42).

## AP. Full test result

**1,343 / 1,343 passing** (1,292 pre-existing + 51 new, mapped to Part 50's 45 numbered topics). Zero regressions, zero weakened assertions.

## AQ. Recommended next single development slice

**Goalie Saves — Team SOG Upstream Challenger** (explicitly deferred by Part 41 to a separate slice): now that Team SOG has its own validated, frozen distribution (Poisson, α=0.0097, MAE ≈4.9), a natural, cleanly-scoped next step is to test whether swapping Goalie Saves' current workload anchor (opponent's rolling SOG rate, a *simple* baseline) for this slice's *validated, model-based* Team SOG expectation improves the existing Goalie Saves model — as a genuine challenger comparison, not a replacement, exactly per Part 41's instruction. This is now buildable because Section AK's exact identity relationship (team SOG ≈ opponent goalie shots faced, off by empty-net shots only) gives a precise, well-understood bridge between the two models.

---

## Final Questions

**IS TEAM SOG VALIDATED?** PARTIAL overall (mixed by threshold): YES at 20+/25+/30+/35+; NO at 40+.

**WHICH MODEL WON?** B_poisson_direct (direct 7-feature Poisson GLM).

**DOES SIMPLE ROLLING TEAM SOG REMAIN THE BEST BASELINE?** YES — it beat every other baseline including the opponent-allowed rate and the offense/defense decomposition.

**DOES OPPONENT SOG ALLOWED ADD VALUE?** YES — the single largest-magnitude feature in the winning GLM (+0.387), though not sufficient alone as a baseline to beat plain rolling team SOG.

**DOES HOME/AWAY ADD VALUE?** YES, modestly (+0.048 coefficient, real and consistent in direction with the Goalie Saves slice's finding).

**DOES SPECIAL-TEAMS CONTEXT ADD VALUE?** NO — tested directly via residual correlation (r=-0.03, -0.01), negligible.

**DOES RECENT TEAM SOG ADD VALUE?** YES, modestly (+0.066 coefficient).

**DOES H2H ADD VALUE?** NO — negligible (+0.015, aggressively shrunk as designed).

**DOES AGGREGATED PLAYER SOG ADD VALUE?** NO — worst of all six candidates, ~21.6% worse than the winner.

**IS PLAYER-SOG AGGREGATION STILL WORSE THAN DIRECT TEAM-LEVEL MODELING?** YES — re-confirmed fresh for this target, not assumed from the Goalie Saves finding, and the relative gap is nearly identical (~21-22% worse in both slices).

**IS POISSON BEST?** YES.

**IS NEGATIVE BINOMIAL BEST?** NO — α=0.0097, near-zero, Poisson adequate and identical in practice.

**IS ANOTHER SIMPLE COUNT FAMILY BEST?** NO — a Normal/Gaussian approximation was tested and found materially indistinguishable from Poisson; not adopted (no improvement to justify the change).

**ARE TEAM SOG O/U PROBABILITIES VALIDATED?** PARTIAL — validated for lines in the 20-35 range; not validated at the 40+ extreme tail.

**IS TEAM SOG STRONGLY LINKED TO OPPONENT GOALIE SAVES?** YES — r=0.84-0.85, and near-identical to shots-faced by construction (empty-net shots being the only real gap).

**CAN TEAM SOG + GOALIE SAVES BE PRICED AS INDEPENDENT?** NOT VALIDATED — strongly dependent (near-identity relationship), must never be priced as independent draws.

**CAN PLAYER SOG + TEAM SOG BE PRICED AS INDEPENDENT?** NOT VALIDATED — no independence test was run this slice (only an accounting-identity reconciliation and a contribution-concentration diagnostic were delivered); flagged for future joint work, not assumed independent.

**DO LOW-CONFIDENCE TEAM-SOG PREDICTIONS SHOW POSITIVE SKILL?** N/A — 0 LOW-confidence predictions occurred in either eval season (disclosed, Section AF).

**DOES TEAM SOG NEED WATCH_ONLY GATING?** NO — no LOW-confidence bucket exists to gate against; `decision_policy.py` left unchanged at v3.

**WERE EXISTING PLAYER-SOG MODELS CHANGED?** NO.

**WAS GOALIE SAVES CHANGED?** NO.

**WAS CONFIDENCE CHANGED?** NO.

**WAS DECISION POLICY v3 CHANGED?** NO.

**WAS NHL WIN MODEL CHANGED?** NO.

**CURRENT FULL TEST RESULT?** 1,343 / 1,343.

**WHAT IS NOW THE HIGHEST-LEVERAGE NEXT DEVELOPMENT SLICE?** Goalie Saves — Team SOG Upstream Challenger: test (as a separate, disclosed challenger comparison, not a silent swap) whether this slice's validated Team SOG model improves on Goalie Saves' current simpler opponent-SOG-rate workload anchor.

---

**STOP AFTER TEAM SOG.**
