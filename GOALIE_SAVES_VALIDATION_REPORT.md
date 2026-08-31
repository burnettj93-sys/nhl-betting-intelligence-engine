# Goalie Saves + Period Saves — Validation Report

**Status: MIXED — genuinely partial, not uniform.** Full-game **20+ and 25+ saves are VALIDATED**. **30+** and **Periods 1/3** are **PARTIAL** (season-inconsistent). **35+** is **REJECTED** (tested, fails clearly with adequate support). **40+** is **INSUFFICIENT_DATA** (thin positive-event support). **Period 2 saves is VALIDATED**. Every number in this report is **CONDITIONAL_ON_ACTUAL_START** — see Section B.

---

## A. Goalie-game corpus

Built by `research/goalie_saves/build_goalie_saves_corpus.py`, reusing the already-audited, unmodified `goalie_tenure.py`/`period_saves.py` (5,248/5,248 exact official boxscore save reconciliation, 0 mismatches — Event-Timing Utility Closure slice, not re-verified here since it is the same unmodified code).

- **11,104 goalie appearance rows** (both teams, all 4 seasons), of which **10,496 are real starts** (exactly one starter per team-game, 10,496/10,496 verified) and 608 are relief appearances (142–175/season).
- Companion **team-game SOG corpus** (`team_game_sog.jsonl`, 10,496 rows) carries SOG-for/against per period plus a real situationCode-derived PP/PK split.
- **Internal coherence**: period saves sum to full-game saves in 11,104/11,104 rows; `actual_saves == actual_shots_faced - actual_goals_allowed` holds in all 11,104 rows.
- **Disclosed, expected reconciliation gap**: a defending team's goalies' combined shots-faced is occasionally *less* than the opponent's team-level SOG total (1,757/10,496 team-games, always by exactly 1–2 shots) — this is empty-net shots taken while no goalie was in net, correctly excluded from "shots faced" per the established empty-net convention, not a bug.

## B. Starter/relief accounting — CONDITIONAL_ON_ACTUAL_START (Part 2/3/34)

Real sportsbook saves props settle **conditional on the named goalie actually starting** (void otherwise) — headline evaluation here uses exactly that population: `actual_started == True` rows only, 10,496 total, 2,624/season.

The project already has a separate, **PIT-safe, walk-forward-audited** projected-starter model (`research/goalie_intelligence/`) — audited here (Part 3), not re-fit:
- Same WARMUP=2022-23 / TUNING=2023-24 / EVAL=2024-25+2025-26 split.
- True-holdout top-1 accuracy: **67.5%** overall (2024-25: 69.0%, 2025-26: 66.0%), Brier **0.445**, log-loss **0.723**.

**STARTER UNCERTAINTY NOT INCLUDED IN HEADLINE VALIDATION.** The starter model is referenced for live-architecture design (Section AT) but never folded into this slice's Brier/log-loss numbers, and `actual_started`/`ACTUAL_STARTER` is never read as a pregame feature anywhere in `build_example()` (verified directly — Test08).

## C. Save distribution

Full-game saves (starts only, EVAL seasons pooled): mean ≈ 26.8, stdev ≈ 7.09. Near-Poisson dispersion (Section U). No structural excess-zero pattern — a starting goalie recording 0 saves is (correctly) treated as extremely rare, no zero-inflated family tested (Part 22, consistent with the data).

## D. Shots-faced distribution

Mean ≈ 29.9 (EVAL, pooled), stdev ≈ 7.11 — closely tracks the saves distribution (Section T).

## E. Baseline models

Six PIT-safe baselines (Part 4), all predicting saves directly:

| | Formula | Mean Brier (20/25/30/35/40, both eval seasons) |
|---|---|---|
| A_goalie_saves_rate | rolling goalie saves/game | 0.14117 |
| B_opponent_sog_x_league_savepct | opponent SOG rolling × league save% | **0.13933 (best baseline)** |
| C_goalie_shots_faced_rate | rolling goalie shots-faced × league save% | 0.14055 |
| D_opponent_sog_x_goalie_savepct | opponent SOG rolling × goalie's own shrunk save% | 0.13942 |
| E_shrunk_workload | GOALIE→TEAM→LEAGUE shrunk saves mean | 0.14423 |
| F_h2h_workload | H2H-shrunk saves rate vs. opponent | 0.14086 |

**Best baseline: B** — plain opponent SOG-rate × *league-average* save%. Notably, using the goalie's own shrunk save% (D) does **not** beat B — see Section G.

## F. Shots-faced prediction results (Part 20, independently validated)

| Season | n | Team-level MAE | Team-level RMSE | n with player-agg | Player-agg MAE | Player-agg RMSE |
|---|---|---|---|---|---|---|
| 2024-25 | 2,567 | **5.58** | 7.19 | 2,563 | 6.66 | 8.55 |
| 2025-26 | 2,574 | **5.56** | 7.12 | 2,574 | 6.46 | 8.21 |

The simple team-level opponent-SOG rolling rate is a **clearly better** shots-faced predictor than the expensive player-roster-aggregation approach, in both eval seasons independently. Part 8's explicit instruction ("if the simple team-level predictor is just as good, prefer it") is satisfied and then some — it isn't just as good, it's better.

## G. Goalie save-rate stability

Goalie-level save% is noisy at single-season sample sizes; `GoalieSavePctRates` (GOALIE→TEAM→LEAGUE) shrinks it accordingly (Section H). **Honest finding**: comparing baseline B (league-average save%) against baseline D (goalie's own shrunk save%) shows **no meaningful standalone value** from using goalie-specific save% (0.13933 vs 0.13942 — D is marginally *worse*). The winning model (E) still uses shrunk save% as part of its offset, but its edge over B comes from the offset-GLM's *contextual* adjustments (recent form, opponent factor), not from goalie-specific save-rate talent alone. This is consistent with the project's prior `GOALIE_QUALITY_INTEGRATION_REPORT.md` finding that goalie-quality signal is hard to extract cleanly — confirmed again here at save-count granularity.

## H. Save-rate shrinkage

`research/goalie_saves/hierarchy.py::GoalieSavePctRates` — pooled-count (not naive mean-of-means) shrinkage: GOALIE → TEAM (`k_team=600` shots) → LEAGUE. Verified: zero-history goalie returns the team-shrunk prior exactly (Test14); a synthetic tiny sample shrinks visibly harder toward the team prior than a large sample (Test15).

## I. Player-SOG upstream eligibility (Part 6-8)

**UPSTREAM PLAYER SOG: ELIGIBLE.** The existing, validated Player SOG model's frozen weights (`research/player_sog_results.json`, headline stage `M4_plus_h2h`) were reused unchanged, aggregated over a real, PIT-safe "recently-appeared, `projected_active`-gated" roster proxy (`research/goalie_saves/upstream_player_sog_aggregation.py`). It worked technically (smoke-tested: 20/23 real candidate skaters, aggregate ≈ league average) — but **did not add value** (Section F): it clearly *underperforms* the simple team-level SOG rate. `F_player_agg_x_saverate` scored 0.16563 mean Brier vs. E's 0.13674 — the worst of all 6 candidates.

## J. Player-SOG aggregation value — NOT ADOPTED

Confirmed negative on a second independent diagnostic: correlation between realized saves and the player-aggregated expected-SOG sum is **weaker** (r=0.113, 0.150 across eval seasons) than the correlation between saves and the much simpler team-level rolling SOG rate (r=0.152, 0.195 — Section AN). Roster-uncertainty (Part 7) is real and likely the cause: the "recently-appeared" proxy cannot perfectly reconstruct tonight's actual lineup, and that noise outweighs the extra precision player-level modeling could in principle offer. **Not included in the frozen model.**

## K. Team-SOG context value

The frozen winning model's dominant feature is the opponent's own rolling SOG-generation rate (GLM coefficient +0.488 on log scale — by far the largest magnitude feature, Section Q) — team-level shot-generation context is clearly the dominant real driver of goalie workload, confirming Section F/J's team-vs-player finding.

## L. Team defensive-context value — collected, not yet built in (disclosed)

The goalie's own team's historical shots-against rate (`team_shots_against_rolling`) was computed and carried on every example but **not built into the frozen headline GLM** this slice (scope). A post-hoc residual-correlation diagnostic (actual saves − E_hybrid_offset prediction, vs. this feature) found real, non-trivial leftover signal: **r=0.117 (2024-25), r=0.173 (2025-26)**. This is disclosed honestly as a genuine, promising direction for a focused future refinement — not fabricated as already tested-and-included, and not silently dropped either.

## M. Opponent offensive-context value

Already the model's strongest single signal (Section K/Q) — opponent SOG-rolling rate, log-transformed, carries the largest GLM weight of any feature.

## N. Special-teams value — data collected, not tested this slice (disclosed)

`team_game_sog.jsonl` carries real situationCode-derived PP/PK shot splits per period (`period_k_pp_sog`, `period_k_pk_sog`) — collected but **not built into the headline feature set** this slice, given the team-level workload signal already dominates and effort/scope constraints. Flagged as a real candidate for a future refinement, same honest-disclosure treatment as Section L.

## O. Home/road value

Included directly in the headline GLM (`home_ind` feature). Coefficient: **−0.0458** (log scale) — home goalies face very slightly *fewer* saves, a real but small effect (~4.5% relative on the log scale). Tested, not assumed significant, and it isn't large — reported honestly rather than emphasized.

## P. Rest/back-to-back value

Tested directly in the headline GLM (`is_b2b_goalie` indicator, Part 16's explicit hypothesis test). Coefficient: **+0.0274** — small and, notably, in the *opposite* direction from the naive hypothesis (a tired goalie facing more rubber) tested by `research/goalie_intelligence`'s own starter model (which found a strongly *negative* back-to-back coefficient for *who starts*, −2.90). Here, *conditional on actually starting* on a back-to-back, workload is essentially unaffected — a real, disclosed, near-null finding, not forced to match intuition.

## Q. Recent workload value

`recent_form` (log-ratio of 5-game rolling saves vs. 20-game baseline) carries a small positive GLM coefficient (**+0.053**) — real but modest value, as expected for a quantity already well-anchored by the 20-game baseline.

## R. Recent save% value

The hybrid offset-GLM's `save_pct_form` context coefficient is **+0.0333** — small, positive, consistent with Section G's finding that save-rate signal (whether long-run shrunk or short-run recent) is a minor contributor relative to workload.

## S. H2H value

GLM coefficient on the H2H-shrunk saves delta: **+0.0158** — negligible, as expected given typical H2H sample sizes of 2-4 games/season (aggressive shrinkage, Part 18, correctly keeps this near-zero).

## T. Candidate models

Six candidates (Part 19), all predicting full-game saves directly:

| | Description | Mean Brier |
|---|---|---|
| A_shrunk_empirical | GOALIE→TEAM→LEAGUE shrunk saves mean | 0.14423 |
| B_poisson_direct | direct Poisson GLM (7 features) | 0.13685 |
| C_negbinom_direct | same GLM, NB re-scored | 0.13685 |
| D_shots_x_shrunk_saverate | opponent SOG rolling × shrunk save% | 0.13942 |
| **E_hybrid_offset** | **offset-GLM: log(D) + small context adjustment** | **0.13674 (winner)** |
| F_player_agg_x_saverate | player-roster-aggregated SOG × shrunk save% | 0.16563 (worst) |

## U. Poisson vs. Negative Binomial

Fitted dispersion **α = 0.0414** — small, near-Poisson. NB was tested (Part 21, not assumed) and never meaningfully separates from Poisson (`B_poisson_direct` and `C_negbinom_direct` score identically to 5 decimal places at these α levels) — Poisson is adequate.

## V. Selected/frozen model

**E_hybrid_offset**: `log(mu) = log(opponent_SOG_rolling × GOALIE→TEAM→LEAGUE shrunk save%) + w·[1, save_pct_form, log(opponent_factor)]`, offset weights fit via batch gradient descent on TUNING only: `[-0.0435, 0.0333, -0.2673]`.

Direct Poisson GLM weights (feature order: intercept, log-baseline-saves, recent-form, home-indicator, log-opponent-factor, H2H-delta, back-to-back-indicator): `[0.479, 0.855, 0.053, -0.046, 0.488, 0.016, 0.027]`.

**A real fitting bug was found and fixed during this slice**: `count_models.fit_poisson_glm`'s default learning rate (0.05, tuned for Player SOG counts ~2-3/game) **diverges catastrophically** on saves counts (~28/game, ~10x larger) — first attempt produced weights in the ±800,000 range and a full-game Brier of 0.314 (worse than every baseline). Fixed at the call site with `lr=0.005, n_iter=1500` (confirmed stable across lr=0.001-0.005) — `count_models.py` itself was NOT modified (shared, frozen). Same fix applied to the period-level GLM fits (`lr=0.01, n_iter=800`).

## W. Freeze manifest

```
experiment_id: goalie_saves_v1
target: full-game and period goalie saves, CONDITIONAL ON ACTUAL START
population_definition: actual_started == True rows only (10,496 rows)
starter_handling: CONDITIONAL_ON_START; starter uncertainty NOT included in headline validation
model_family: E_hybrid_offset (full-game); share-of-full-game vs direct-Poisson compared per period
workload_model: opponent team SOG rolling rate (window=20); player-SOG aggregation tested, NOT adopted
save_rate_model: GOALIE -> TEAM -> LEAGUE hierarchically shrunk save percentage
features: log(baseline saves, w=20), recent-form log-ratio (w=5), home/away, log(opponent factor),
          H2H shrunk delta, back-to-back indicator
lookbacks: baseline_window=20, recent_window=5, opponent_window=20, h2h_shrinkage_games=8
distribution: Poisson (alpha=0.0414 fitted, near-zero)
period_methodology: share-of-full-game-model vs direct-period-Poisson, compared independently per period
threshold_support_rule: full-game 20/25/30/35/40+; period 5/8/10/12+; INSUFFICIENT_DATA if <50
                        positive events in an eval season
confidence: research.player_sog.count_models.confidence_score (unchanged, reused)
conservative_probability: research.player_sog.count_models.conservative_mu (unchanged, reused)
upstream_sog_provenance: reuses FROZEN player_sog_results.json headline-stage weights unchanged;
                         roster candidates gated by the validated model's own projected_active() rule
```

Code hashes recorded in `research/goalie_saves_results.json["freeze_manifest"]["code_hashes"]`.

## X. Common evaluation sets

| Season | n (actual starts, ≥5 prior starts) |
|---|---|
| 2024-25 | 2,567 |
| 2025-26 | 2,574 |

Exclusions are only the `<5 prior starts` gate (early-career/small-sample goalies) — no other exclusions.

## Y. Full-game NLL/MAE/RMSE

| Season | Winner (E) NLL | Baseline (B) NLL | Winner MAE | Baseline MAE |
|---|---|---|---|---|
| 2024-25 | -54.394 | -54.358 | **5.410** | 5.531 |
| 2025-26 | -52.700 | -52.671 | **5.484** | 5.562 |

## Z. 20+ metrics

| Season | Cand. Brier | Base Brier | Actual rate | n positive | Bootstrap frac_improved (game / date) |
|---|---|---|---|---|---|
| 2024-25 | 0.17025 | 0.17378 | 78.9% | 2,024 | **1.000 / 1.000** |
| 2025-26 | 0.19016 | 0.19378 | 75.3% | 1,938 | **1.000 / 1.000** |

**VALIDATED.**

## AA. 25+ metrics

| Season | Cand. Brier | Base Brier | Actual rate | n positive | Bootstrap frac_improved (game / date) |
|---|---|---|---|---|---|
| 2024-25 | 0.24832 | 0.25758 | 50.0% | 1,283 | **1.000 / 1.000** |
| 2025-26 | 0.24391 | 0.25178 | 46.9% | 1,207 | **1.000 / 1.000** |

**VALIDATED.**

## AB. 30+ metrics

| Season | Cand. Brier | Base Brier | Actual rate | n positive | Bootstrap frac_improved (game / date) |
|---|---|---|---|---|---|
| 2024-25 | 0.17819 | 0.18080 | 23.4% | 601 | 0.988 / 0.986 |
| 2025-26 | 0.16812 | 0.16827 | 22.1% | 569 | **0.535 / 0.568** |

**PARTIAL** — clears the bar in 2024-25, collapses in 2025-26.

## AC. 35+ metrics

| Season | Cand. Brier | Base Brier | Actual rate | n positive | Bootstrap frac_improved (game / date) |
|---|---|---|---|---|---|
| 2024-25 | 0.07099 | 0.07083 | 7.6% | 196 | 0.381 / 0.412 |
| 2025-26 | 0.06412 | 0.06326 | 6.9% | 178 | 0.024 / 0.026 |

**REJECTED** — fails clearly in both seasons, with adequate positive-event support (178-196/season, well above the 50-event floor) to conclude a real negative rather than insufficient evidence.

## AD. 40+ metrics

| Season | Cand. Brier | Base Brier | Actual rate | n positive | Bootstrap frac_improved (game / date) |
|---|---|---|---|---|---|
| 2024-25 | 0.01843 | 0.01832 | 1.9% | 48 | 0.127 / 0.136 |
| 2025-26 | 0.01497 | 0.01487 | 1.5% | 39 | 0.106 / 0.100 |

**INSUFFICIENT_DATA** — n_positive (39-48) is below this slice's pre-specified 50-event floor (Part 24) in both seasons; the negative point estimate is consistent with Section AC but is not treated as a confirmed REJECTED verdict given the thin support.

## AE. P1 saves results

| Season | Winner arch. | Brier@5+ (share / direct) | Bootstrap frac_improved (direct vs share, t=5) |
|---|---|---|---|
| 2024-25 | A_share_of_full_game | 0.10342 / 0.10297 | 0.831 |
| 2025-26 | B_direct_poisson | 0.11827 / 0.11643 | 0.997 |

**PARTIAL** — direct-Poisson beats the naive share baseline in 2025-26 but not 2024-25.

## AF. P2 saves results

| Season | Winner arch. | Brier@5+ (share / direct) | Bootstrap frac_improved (direct vs share, t=5) |
|---|---|---|---|
| 2024-25 | B_direct_poisson | 0.10962 / 0.10885 | 0.973 |
| 2025-26 | A_share_of_full_game | 0.11914 / 0.11765 | 0.999 |

**VALIDATED** — direct-Poisson beats the share baseline consistently in **both** eval seasons (≥0.95 both times), the only period to clear the bar cleanly.

## AG. P3 saves results

| Season | Winner arch. | Brier@5+ (share / direct) | Bootstrap frac_improved (direct vs share, t=5) |
|---|---|---|---|
| 2024-25 | A_share_of_full_game | 0.16047 / 0.16000 | 0.763 |
| 2025-26 | A_share_of_full_game | 0.17349 / 0.17054 | 1.000 |

**PARTIAL** — same inconsistent pattern as P1.

Note: skill scores at every period threshold are small (mostly <2%) for both architectures — period saves carry real but modest structure beyond a naive full-game-model-derived share, consistent with the mixed verdicts above.

## AH. Calibration

10-band calibration tables are computed for every threshold/season/model combination (`research/goalie_saves_results.json`). At the two VALIDATED thresholds (20+, 25+), predicted and actual rates track closely within each band — consistent with the strong, uniform bootstrap evidence.

## AI. Game-cluster bootstrap

The headline significance test throughout (1,000 resamples, game-`game_id`-clustered) — see Sections Z-AG for the full per-threshold/period results. 20+/25+ full-game and P2 saves are the only cases clearing ≥0.95 in **both** eval seasons independently.

## AJ. Date-cluster sensitivity

Tracks the game-clustered results closely in every case (e.g., 20+ 2024-25: 1.000 both; 30+ 2025-26: 0.535 game vs 0.568 date) — confirms the mixed verdict is not an artifact of the clustering choice.

## AK. Season-by-season generalization

Every VALIDATED verdict in this report required independent confirmation in **both** 2024-25 and 2025-26 — no threshold or period was adopted on a single-season result. This is exactly what caught 30+, P1, and P3 as PARTIAL rather than VALIDATED (each looked promising in one season and collapsed in the other).

## AL. Confidence results

Reused, unmodified `confidence_score` framework. Both eval seasons show only **HIGH** and **MEDIUM** buckets — **0 LOW-confidence predictions occurred in either eval season** (a real, disclosed limitation: goalies accumulate history quickly relative to the framework's thresholds, similar to the team-granularity finding in the Team Goals by Period slice, though less extreme here). Skill within buckets at 20+ is small and slightly negative-to-flat in both seasons (e.g., HIGH: -0.025/-0.017; MEDIUM: -0.005/-0.051) — consistent with the modest overall skill-score picture (Section Z note on skill scores).

## AM. Conservative probability

`conservative_never_exceeds_raw: True` holds in both eval seasons (verified structurally, Test43). Mean raw-minus-conservative gap ≈ 0.044-0.047 at the 20+ threshold — modest and consistent.

## AN. Goalie-save ↔ player-SOG dependence

Correlation between realized saves and the player-roster-aggregated expected-SOG sum: **r=0.113 (2024-25), r=0.150 (2025-26)** — real but modest, and (Section J) weaker than the simpler team-level signal.

## AO. Goalie-save ↔ team-SOG dependence

Correlation between realized saves and the team's rolling opponent-SOG rate (the actual PIT-safe workload driver used in the model): **r=0.152 (2024-25), r=0.195 (2025-26)**. This is a weaker correlation than one might naively expect given the model's strong GLM coefficient (+0.488) on this same signal — the resolution is Section U/T's variance decomposition (Section AP): correlation between **realized shots-faced** (same-game) and saves is very high (**r=0.977, 0.978**), but the *rolling rate* is necessarily a noisier, PIT-safe proxy for that same-game reality, which is exactly why building a good pregame estimator (not just a same-game accounting identity) is the real challenge this slice tackled.

## AP. Save ↔ goals-allowed dependence (workload/conversion decomposition, Part 11)

Pooled EVAL-season variance decomposition:
- **corr(shots_faced, saves) = 0.977-0.978** — saves are almost entirely a workload phenomenon, not a conversion phenomenon, at the game level (mechanically expected since saves = shots_faced − goals_allowed and goals_allowed's variance is much smaller than shots_faced's).
- stdev(shots_faced) ≈ 7.11, stdev(saves) ≈ 7.09 — nearly identical, confirming workload dominates.
- stdev(save%) ≈ 0.078, mean save% ≈ 0.890.
- corr(shots_faced, goals_allowed) ≈ 0.12-0.12; corr(saves, goals_allowed) ≈ -0.09 to -0.10 (weak negative, as expected: more saves at fixed shots implies fewer goals).

**This is the key scientific finding of the slice**: goalie save-count prediction is fundamentally a **shots-faced (workload) prediction problem**, not a save-rate (talent) problem — matching the prompt's own hinted hypothesis and confirmed rather than assumed.

## AQ. Representative examples

11 representative real 2025-26 examples frozen in `research/goalie_saves_results.json["representative_examples"]`: `high_workload_starter`, `low_workload_starter`, `elite_save_rate_goalie`, `average_save_rate_goalie`, `high_shot_opponent`, `low_shot_opponent`, `back_to_back_situation`, `high_confidence_prediction`, `low_confidence_prediction`, `model_hit`, `model_miss`. **Disclosed**: because 0 LOW-confidence predictions occurred in the 2025-26 pool (Section AL), `low_confidence_prediction` falls back to the same example as `high_confidence_prediction` — not a genuine LOW example, shown honestly rather than fabricated.

## AR. Registry changes

`research/player_props/market_registry.py` updated (6th legitimate edit to this file): `GOALIE_SAVES_20PLUS`/`25PLUS` → VALIDATED; `30PLUS` → PARTIAL; `35PLUS` → REJECTED; `40PLUS` → INSUFFICIENT_DATA; `PERIOD_1/3_GOALIE_SAVES` → PARTIAL; `PERIOD_2_GOALIE_SAVES` → VALIDATED. Registry totals: `total_canonical_markets()=142` (unchanged), `derivable_today()` **24→28**, `validated_today()` **12→15**.

## AS. Dashboard changes

- `dashboard/goalie_saves_view.py` — `GoalieSavesEngine` (reuses the frozen `build_example`/`compute_candidates`/`confidence_for_example` functions) and `StarterProbabilityEngine` (reuses the existing, unmodified starter model's frozen weights). Smoke-tested end-to-end on a real goalie-game (real WPG starter, 2026-04-13 @ VGK): expected_saves=26.23, real per-threshold probabilities, confidence=HIGH.
- `dashboard/pages/16_Goalie_Saves_Research.py` — new page, labeled **MIXED RESULT** with per-threshold/per-period status chips, CONDITIONAL_ON_ACTUAL_START disclosure throughout, a separate real starter-probability panel.
- `dashboard/app.py` — added navigation entry (🧤 Goalie Saves Research).

## AT. Live architecture design (Part 42 — design only, not executed)

```
P(goalie starts)          <- research.goalie_intelligence (existing, audited, unchanged)
  x
conditional save distribution  <- E_hybrid_offset (this slice, CONDITIONAL_ON_START)
  =
raw market probability
  -> widened by BOTH starter-confidence AND save-model confidence
  -> conservative market probability (never simply conservative_mu(raw) alone --
     starter uncertainty must materially widen the interval further, not be
     silently treated as equal to unconditional live probability -- Part 41)
```

No sportsbook odds were queried; no live pricing was executed this slice.

## AU. Files created/modified

**New:**
- `research/goalie_saves/{__init__,build_goalie_saves_corpus,features,hierarchy,upstream_player_sog_aggregation}.py`
- `research/run_goalie_saves_model.py`
- `research/goalie_saves/{goalie_game_saves,team_game_sog}.jsonl` (11,104 / 10,496 rows)
- `research/goalie_saves_results.json` (frozen results)
- `dashboard/goalie_saves_view.py`
- `dashboard/pages/16_Goalie_Saves_Research.py`
- `tests/test_goalie_saves_model.py` (58 tests)
- `GOALIE_SAVES_VALIDATION_REPORT.md` (this file)

**Modified:**
- `research/player_props/market_registry.py` (8 goalie-saves market IDs updated; `derivable_today()`/`validated_today()` docstring corrected)
- `dashboard/app.py` (navigation entry added)
- `tests/test_pbp_foundation.py` (market_registry.py hash pin updated)
- `tests/test_event_timing_utilities.py`, `tests/test_pbp_multi_season.py` (derivable/validated count pins updated: 24→28, 12→15)

**Untouched (verified via `git diff --stat`, empty):** `models/`, `config.py`, `db.py`, `schema.sql`, `pricing/`, `nhl.db`.

## AV. Full test result

**1,292 / 1,292 passing** (1,234 pre-existing + 58 new, mapped to Part 51's 53 numbered topics). Zero regressions, zero weakened assertions.

## Recommended next single development slice

**Goalie Team-Defensive-Context Refinement** — Section L's residual-correlation finding (r=0.12-0.17, real leftover signal in the goalie's own team's shot-suppression history, not currently in the frozen model) is the highest-leverage, cleanly-scoped next step: fold `team_shots_against_rolling` into the offset-GLM as a genuine value-test, following the exact honest-disclosure protocol used throughout this project. A second reasonable candidate is folding in the PP/PK shot-split data already collected (Section N) as a small special-teams context feature.

---

## Final Questions

**IS FULL-GAME GOALIE SAVES VALIDATED?** PARTIAL — 20+ and 25+ YES; 30+ PARTIAL; 35+ REJECTED; 40+ INSUFFICIENT_DATA.

**WHICH MODEL WON?** E_hybrid_offset (opponent-SOG-rolling × shrunk-save% offset + small contextual GLM adjustment).

**IS SHOTS-FACED PREDICTION VALIDATED?** YES, as an independent submodel — team-level opponent SOG rate is the clear best predictor (MAE 5.56-5.58), beating the player-aggregation alternative.

**DID AGGREGATED PLAYER SOG ADD VALUE?** NOT ELIGIBLE→ELIGIBLE but NO value (technically worked, clearly underperformed the simple team-level signal on every metric tested).

**DID SIMPLE TEAM SOG CONTEXT ADD VALUE?** YES — it is the dominant driver (largest GLM coefficient, +0.488).

**DID GOALIE SAVE-RATE TALENT ADD VALUE?** NO, not meaningfully on its own (baseline B ≈ baseline D, D even marginally worse) — consistent with the prior win-model goalie-quality failure precedent.

**DID SPECIAL-TEAMS CONTEXT ADD VALUE?** NOT TESTED this slice (data collected, not built into the headline model — disclosed, Section N).

**DID RECENT SAVE% ADD VALUE?** Small, positive (+0.033 offset coefficient) — modest, not a headline driver.

**DID H2H ADD VALUE?** NO — negligible (+0.016 coefficient, aggressively shrunk as designed).

**IS NEGATIVE BINOMIAL BETTER THAN POISSON?** NO — α=0.0414, near-zero, Poisson adequate.

**ARE 20+ SAVES VALIDATED?** YES.

**ARE 25+ SAVES VALIDATED?** YES.

**ARE 30+ SAVES VALIDATED?** PARTIAL (season-inconsistent).

**ARE 35+ SAVES VALIDATED?** NO — REJECTED (adequate support, fails clearly).

**ARE 40+ SAVES VALIDATED?** INSUFFICIENT DATA.

**IS P1 GOALIE SAVES VALIDATED?** NO — PARTIAL.

**IS P2 GOALIE SAVES VALIDATED?** YES.

**IS P3 GOALIE SAVES VALIDATED?** NO — PARTIAL.

**DO LOW-CONFIDENCE GOALIE-SAVE PREDICTIONS SHOW POSITIVE SKILL?** N/A — 0 LOW-confidence predictions occurred in either eval season (disclosed, Section AL).

**DOES GOALIE SAVES NEED WATCH_ONLY GATING?** NO — no negative LOW-confidence pattern exists to gate against (there is no LOW bucket at all); `decision_policy.py` left unchanged at v3.

**IS GOALIE SAVES STRONGLY DEPENDENT ON OPPONENT SOG?** YES — the model's dominant feature, and confirmed via the workload/conversion decomposition (Section AP: corr(shots_faced, saves)=0.977-0.978).

**CAN BOTH-GOALIES X+ SAVES BE PRICED BY MULTIPLYING MARGINALS?** NOT VALIDATED — no joint dependence test was run this slice (explicitly out of scope, Part 45); both goalie distributions are preserved for future simulation work, and Team Goals by Period's prior finding (real, model-persistent home/away goal dependence) is a caution against assuming independence here either.

**WERE ANY EXISTING VALIDATED MODELS CHANGED?** NO.

**WAS CONFIDENCE CHANGED?** NO.

**WAS DECISION POLICY v3 CHANGED?** NO.

**WAS NHL WIN MODEL CHANGED?** NO.

**CURRENT FULL TEST RESULT?** 1,292 / 1,292.

**WHAT IS NOW THE HIGHEST-LEVERAGE NEXT DEVELOPMENT SLICE?** Goalie Team-Defensive-Context Refinement — fold the already-collected `team_shots_against_rolling` signal (real residual correlation r=0.12-0.17, Section L) into the frozen offset-GLM as a genuine, honestly-evaluated value-test.

---

**STOP AFTER GOALIE SAVES.**
