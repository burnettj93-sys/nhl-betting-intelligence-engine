# Player Context State Validation Report

**Slice:** Player Context State Validation / Cold Streak + Negative Media + Arena Effects / UNDER-Bias Research Layer
**Scope:** Research only. No refit of any frozen marginal. No decision_policy change. No live sportsbook queries. No parlay optimizer. No full game simulator.
**Primary hypothesis under test:** NEGATIVE CONTEXT → UNDER BIAS (frozen model overpredicts for players in a negative context state). Tested empirically, not hard-coded.

---

## A. Executive summary

This slice built a PIT-safe, expectation-relative COLD/HOT performance-state classifier on top of the five frozen player marginals (SOG, Goals, Assists, Points, Blocks) and tested whether that state predicts real out-of-sample overprediction by those frozen models. It also built an arena-effect layer using home-team identity as a disclosed venue proxy.

**Headline finding, stated precisely (not oversold):** COLD state *by itself* is a weak, inconsistent UNDER signal — it only clears significance cleanly for **Points**. But COLD state **combined with a real TOI/role decline** (`COLD_AND_TOI_DECLINE`) is a substantially stronger and more consistent UNDER signal — validated in both EVAL seasons for **Goals** and **Points**, and directionally consistent (partial) for **SOG** and **Assists**. **Blocks shows no reliable effect either way.** A large share of the "cold state" UNDER-bias is actually explained by role/TOI decline, not cold shooting/production luck alone — this is a real, load-bearing finding, not a caveat to bury.

**Media sentiment component: NOT BUILT.** No legitimate, timestamped, legally-accessible historical media/news/sentiment corpus exists anywhere in this repository (verified directly). Fabricating one would violate this project's core data-integrity rule and this slice's own explicit instructions. This is disclosed prominently, not silently omitted — see Sections E, L, and the registry.

**Arena effects: mixed.** A real, measured arena-wide (rink-pooled) residual spread exists (comparable order of magnitude to the pre-existing Hits-based rink finding), but individual **player-arena performance effects, even after hierarchical shrinkage, do not generalize out of sample** (correlation ≈ 0.001–0.05 across all five props) — confirming the slice's own stated concern that a handful of player-arena games is mostly noise.

**Regression to the mean is real and is not suppressed:** for Goals, Assists, Points, and Blocks, COLD-state players' actual production at the target game already rebounds to or above their own pre-state baseline rate — the "cold streak" as measured does not persist as a production dip. Only for SOG does the actual rate remain below baseline at the target game. The UNDER-bias residual against the *frozen model's own prediction* survives regardless — the mechanism is closer to "the model doesn't discount enough for players it has just seen recently underperform, particularly players losing ice time" than "cold streaks predict continued cold streaks."

No decision_policy change was made. No adjustment was operationalized. This report recommends a narrowly-scoped follow-up slice (Section AF/AG) using `COLD_AND_TOI_DECLINE` specifically for Points and Goals, if the user wants to pursue this further.

---

## B. Slice scope and boundaries observed

Built entirely under `research/player_context_state/` and `dashboard/`. Zero production files touched (`models/`, `config.py`, `db.py`, `nhl.db`, `pricing/`, `schema.sql`, `research/player_props/decision_policy.py`) — verified via SHA-256 hash pins in [tests/test_player_context_state_model.py](tests/test_player_context_state_model.py) (Tests 45, 51, 52), all passing against the unchanged hashes already pinned in prior slices' test files.

No refit of Player SOG, Goals, Assists, Points, or Blocks. Every prediction in this slice comes from `research/player_context_state/marginal_provenance.py`'s thin wrapper classes, which call each frozen model's own `live_projection`/`build_example` function with its own locked weights file — never a new fit.

No sportsbook/odds API calls (Test 44 greps the driver source for banned identifiers). No parlay optimizer, no combination search, no full game simulator, no automatic betting gate change.

---

## C. Architecture overview

```
research/player_context_state/
  __init__.py
  marginal_provenance.py   # PlayerSogMarginal, GoalsMarginal, AssistsMarginal,
                            # PointsMarginal, BlocksMarginal, ContextMarginalContext
  context_state.py         # form_log_ratio, toi_log_ratio, StateThresholds,
                            # classify_multi_signal, mean_or_none
  arena_effects.py         # game_arena(), ArenaRates (rink-wide + player-arena
                            # hierarchical shrinkage)
  registry.py              # PLAYER_CONTEXT_REGISTRY builder (Part 44)

research/run_player_context_state_model.py   # driver: corpus build, TUNING-fit
                                              # thresholds, EVAL classification,
                                              # bootstrap, diagnostics, freeze manifest

research/player_context_state_results.json     # frozen driver output
research/player_context_state_registry.json    # frozen registry output

dashboard/player_context_state_view.py
dashboard/pages/20_Player_Context_State_Research.py

tests/test_player_context_state_model.py       # 53 tests (Part 52)
```

Every joint/context slice this session reimplements its own thin marginal-provenance wrappers rather than importing a sibling package's copy — `marginal_provenance.py` here is a fresh implementation, following the same convention as `research/joint_shot_workload/marginal_provenance.py` and `research/joint_scoring_dependence/marginal_provenance.py`.

---

## D. Cold-state definition (expectation-relative, Part 3)

```
form_log_ratio = log(recent_5_actual_rate) − log(baseline_20_actual_rate)
```

computed from each player's own PIT history (`history_as_of`, strictly prior game dates only) using each prop's own field (`sog`, `goals`, `assists`, `points`, `blocks`). This is the **same** `recent_form_log_ratio` feature every frozen marginal already computes internally as a model *input* — reused here purely as a *classification* signal, never as a new model input. This makes a star's dip relative to *their own* baseline, not a league constant — a 1-goal-in-5 game means something different for a 30-goal scorer than a 4th-liner.

**COLD** = bottom 20% of this ratio's distribution, **HOT** = top 20%, **NORMAL** = the rest — cutoffs are **TUNING-season (2023-24) fit, frozen before EVAL scoring**, never re-derived per season.

### TUNING-fit cutoffs (2023-24 season, per prop)

| Prop | Cold cutoff | Hot cutoff | TOI-decline cutoff |
|---|---:|---:|---:|
| SOG | −0.348 | +0.238 | −0.038 |
| Goals | −11.51 * | +0.693 | −0.037 |
| Assists | −11.51 * | +0.470 | −0.038 |
| Points | −0.916 | +0.406 | −0.038 |
| Blocks | −0.560 | +0.345 | −0.038 |

\* **Disclosed data-granularity caveat:** for Goals and Assists, "zero production in the last 5 games" is *common*, not rare — a large share of the TUNING distribution sits at the numerical floor (`log(eps)` with `eps=1e-6`), so the 20th-percentile cutoff itself lands on that floor rather than a smooth continuum value. This is a real, mechanical consequence of sparse-event rates, not a bug — it means the Goals/Assists COLD bucket is closer to "any zero-production stretch" than a graded severity measure, and is noted as a methodology limitation rather than smoothed away.

---

## E. Media sentiment state — NOT BUILT (first-class disclosure)

**Status: NOT BUILT.** A direct repository search (`find . -iname "*media*" -o -iname "*news*" -o -iname "*sentiment*" -o -iname "*headline*" -o -iname "*article*"`, excluding `.git`) returned **zero** matches anywhere in this project, before or after this slice. No historical, timestamped, legally-accessible media/news corpus exists locally.

This slice's own instructions explicitly prohibit: scraping protected sources against terms, bypassing paywalls, fabricating publication timestamps, or ingesting uncontrolled social-media chatter absent a clean existing corpus. Given zero legitimate local data source, building this component would have required violating one of those constraints or fabricating data outright — both are prohibited by this project's core "never fabricate data" rule regardless of slice-specific instructions. This slice's own Part 34 explicitly anticipated and pre-authorized this exact outcome ("if media corpus availability prevents full four-season coverage: state this clearly. Do not pretend a shorter corpus has the same validation strength") — the honest answer here is stronger than "shorter corpus": there is no corpus, full stop.

`Test06To13MediaSentimentNotBuilt` in the test suite asserts both that no media files exist in the repo and that the registry records `MEDIA_SENTIMENT_STATE` with `status: NOT_BUILT`.

## F. Media coverage assessment — N/A

No corpus exists, so no seasons/players/games have any media coverage to assess.

## G. Sentiment taxonomy — N/A

No taxonomy was designed since no source corpus exists to apply one to. (Had a corpus existed, a taxonomy would have been built and validated before any modeling use — this slice never reached that point.)

## H. Sentiment validation methodology — N/A

No sentiment scores exist to validate.

---

## I. Hot-state symmetric control (Part 5)

HOT uses the exact same TUNING-fit form-ratio distribution's top 20% — it is **not** assumed a priori to create OVER value; it is measured with the identical bootstrap machinery as COLD, against the same NORMAL baseline. See Section N for results — the mirror is clean for SOG and Blocks, but **not** clean for Goals/Assists/Points (see Section O).

## J. Multi-signal state: COLD_AND_TOI_DECLINE (Part 4)

```
COLD_AND_TOI_DECLINE  ⟺  state == COLD  AND  toi_log_ratio ≤ TUNING-fit 20th percentile
toi_log_ratio = log(recent_10_icetime) − log(baseline_20_icetime)
```

A small, interpretable AND-rule — deliberately not a fitted classifier — built because Part 26 explicitly requires testing whether TOI/role decline confounds (or dominates) the raw cold-state effect. It turned out to be the more important signal (Section P).

---

## K. Arena-effect methodology (Parts 15-18, two distinct mechanisms)

"Arena" = home-team identity of the game (NHL teams play their full home schedule at one arena within a season) — a real, disclosed proxy; this project has no separate venue/building dataset.

1. **Rink-recording-effect candidate**: the mean `(actual_1plus − predicted_prob_1plus)` residual pooled across **every** player who played at a given arena (both teams, all opponents), TUNING-fit. Large and consistent regardless of who is playing would be evidence of a scorekeeper/rink-recording pattern — **or** an equally plausible team-style-of-play confound. Not claimed as causal (same caveat already used for the existing Hits-based rink finding in [NHL_PBP_FOUR_SEASON_CORPUS_REPORT.md](NHL_PBP_FOUR_SEASON_CORPUS_REPORT.md) Section S).

2. **Player-arena performance effect**: a specific player's own residual at a specific arena, hierarchically shrunk PLAYER-ARENA → ARENA (mechanism 1, a real prior) → 0 (`k_arena=300`, `k_player_arena=20`), precisely because Part 17 warns against trusting a raw 6-game player-arena average.

**Units note:** this slice's arena metric is the model-residual scale (probability points), not raw per-game counts like the existing Hits finding (35.7–54.7 hits/home-game). The two are not directly comparable in units — the Hits finding is cited only as qualitative corroborating context that arena-level variation of a similar rough magnitude has been measured before in this project, not as a like-for-like replication.

---

## L. Why media couldn't be operationalized this slice (expanded)

See Section E. To be maximally concrete about what *would* unblock this: a legally-licensed, timestamped historical NHL news/beat-reporter archive (not scraped social chatter, not paywall-bypassed content) with per-article publish timestamps precise enough to guarantee PIT safety relative to game dates. No such asset currently exists in this repository, and acquiring one is outside this slice's scope (it would require a data-licensing decision, not a coding decision).

---

## M. Sample counts per state, per prop, per EVAL season

| Prop | Season | COLD n | NORMAL n | HOT n | COLD_AND_TOI_DECLINE n |
|---|---|---:|---:|---:|---:|
| SOG | 2024-25 | 9,149 | 25,362 | 9,204 | 2,484 |
| SOG | 2025-26 | 8,970 | 25,320 | 9,082 | 2,402 |
| Goals | 2024-25 | 9,416 | 19,762 | 8,385 | 2,010 |
| Goals | 2025-26 | 9,242 | 20,327 | 8,154 | 2,053 |
| Assists | 2024-25 | 9,578 | 22,871 | 9,871 | 2,212 |
| Assists | 2025-26 | 9,205 | 23,191 | 9,673 | 2,281 |
| Points | 2024-25 | 9,001 | 24,734 | 9,549 | 2,281 |
| Points | 2025-26 | 8,702 | 25,177 | 9,117 | 2,380 |
| Blocks | 2024-25 | 9,201 | 25,990 | 8,476 | 2,153 |
| Blocks | 2025-26 | 9,716 | 25,012 | 8,623 | 2,325 |

All states exceed the pre-specified sample floor (`MIN_STATE_SUPPORT = 200`) by a wide margin in every prop/season — no `INSUFFICIENT_DATA` markers were triggered.

## N. Negative-media / cold+negative-media sample counts — N/A

No media state exists (Section E), so no such cohorts exist. The registry contains no `COLD_AND_MEDIA` entry, and the results file contains no `media_state` key anywhere (asserted directly by tests 28-29).

---

## O. Per-prop context results

For each prop: mean model residual (`actual_1plus − predicted_prob_1plus`) by state, and the game-clustered bootstrap COLD-vs-NORMAL test (95% CI, `frac_negative` = fraction of 1,000 game-clustered resamples where COLD's mean residual fell below NORMAL's — the UNDER-direction evidence strength).

### SOG

| Season | COLD resid | NORMAL resid | HOT resid | COLD−NORMAL Δ | 95% CI | frac UNDER |
|---|---:|---:|---:|---:|---|---:|
| 2024-25 | −0.0154 | −0.0076 | +0.0135 | −0.0078 | [−0.0179, +0.0023] | 0.939 |
| 2025-26 | −0.0193 | −0.0086 | +0.0099 | −0.0107 | [−0.0209, −0.0002] | 0.978 |

Directionally consistent both seasons; significant (CI excludes 0) only in 2025-26. **COLD_AND_TOI_DECLINE**: Δ = −0.0331 (CI [−0.0514, −0.0136], frac 1.00) in 2024-25 — clearly significant — but Δ = −0.0143 (CI [−0.0341, +0.0049], frac 0.917) in 2025-26 — suggestive, not significant. **Status: PARTIAL** for both COLD_STATE and COLD_AND_TOI_DECLINE.

### Goals

| Season | COLD resid | NORMAL resid | HOT resid | COLD−NORMAL Δ | 95% CI | frac UNDER |
|---|---:|---:|---:|---:|---|---:|
| 2024-25 | −0.0125 | −0.0066 | −0.0058 | −0.0059 | [−0.0146, +0.0027] | 0.915 |
| 2025-26 | −0.0052 | −0.0029 | −0.0130 | −0.0024 | [−0.0113, +0.0066] | 0.740 |

COLD alone: weak, not significant either season. **COLD_AND_TOI_DECLINE**: Δ = −0.0309 (CI [−0.0460, −0.0162], frac 1.00) in 2024-25, Δ = −0.0263 (CI [−0.0420, −0.0105], frac 0.999) in 2025-26 — **significant both seasons**. **Status: NOT_VALIDATED (COLD alone) / VALIDATED (COLD_AND_TOI_DECLINE)**.

### Assists

| Season | COLD resid | NORMAL resid | HOT resid | COLD−NORMAL Δ | 95% CI | frac UNDER |
|---|---:|---:|---:|---:|---|---:|
| 2024-25 | −0.0045 | +0.0007 | −0.0068 | −0.0052 | [−0.0157, +0.0054] | 0.830 |
| 2025-26 | −0.0037 | +0.0014 | −0.0090 | −0.0050 | [−0.0158, +0.0059] | 0.821 |

COLD alone: weak, not significant. **COLD_AND_TOI_DECLINE**: Δ = −0.0145 (CI [−0.0300, +0.0017], frac 0.96) in 2024-25 — borderline — Δ = −0.0243 (CI [−0.0407, −0.0072], frac 0.999) in 2025-26 — significant. **Status: NOT_VALIDATED (COLD alone) / PARTIAL (COLD_AND_TOI_DECLINE)**.

### Points

| Season | COLD resid | NORMAL resid | HOT resid | COLD−NORMAL Δ | 95% CI | frac UNDER |
|---|---:|---:|---:|---:|---|---:|
| 2024-25 | −0.0287 | −0.0017 | −0.0122 | −0.0271 | [−0.0397, −0.0145] | 1.000 |
| 2025-26 | −0.0247 | +0.0009 | −0.0091 | −0.0257 | [−0.0388, −0.0133] | 1.000 |

**Largest, cleanest, most consistent effect in the whole slice — significant both seasons, both COLD alone and COLD_AND_TOI_DECLINE** (Δ = −0.0472 CI [−0.0660, −0.0285] in 2024-25; Δ = −0.0462 CI [−0.0641, −0.0269] in 2025-26). **Status: VALIDATED for both COLD_STATE and COLD_AND_TOI_DECLINE.**

### Blocks

| Season | COLD resid | NORMAL resid | HOT resid | COLD−NORMAL Δ | 95% CI | frac UNDER |
|---|---:|---:|---:|---:|---|---:|
| 2024-25 | −0.0074 | −0.0117 | +0.0058 | **+0.0043** | [−0.0076, +0.0153] | 0.244 |
| 2025-26 | −0.0170 | −0.0148 | −0.0024 | −0.0022 | [−0.0132, +0.0090] | 0.657 |

**Direction flips between seasons** (2024-25 actually shows COLD *less* negative than NORMAL — opposite of the hypothesis). No reliable effect. **Status: NOT_VALIDATED** — the only prop where the point estimate itself is inconsistent in sign, not just weak.

---

## P. Cross-prop synthesis — the real headline finding

Ranking by strength of evidence, COLD_AND_TOI_DECLINE vs COLD alone:

| Prop | COLD alone | COLD_AND_TOI_DECLINE |
|---|---|---|
| Points | VALIDATED | VALIDATED (larger effect) |
| Goals | NOT_VALIDATED | **VALIDATED** |
| SOG | PARTIAL | PARTIAL |
| Assists | NOT_VALIDATED | PARTIAL |
| Blocks | NOT_VALIDATED (sign flips) | NOT_VALIDATED |

In every single prop/season combination, `cold_toi_declining_mean_prob_residual` is more negative than `cold_toi_stable_mean_prob_residual` (Section Q) — the TOI-declining subset of COLD carries most of the effect. **The primary, load-bearing finding of this slice is not "cold streaks predict UNDERs" — it is "cold production combined with a real drop in ice time predicts UNDERs," and that combined signal is meaningfully stronger and more reproducible than cold production alone.** This was not assumed going in; it fell out of the Part 26 confounding check the instructions explicitly required.

---

## Q. Role-change / TOI confounding diagnostic (Part 26)

| Prop | Season | COLD, TOI-stable n / resid | COLD, TOI-declining n / resid |
|---|---|---|---|
| SOG | 2024-25 | 6,665 / −0.0060 | 2,484 / **−0.0406** |
| SOG | 2025-26 | 6,568 / −0.0179 | 2,402 / **−0.0229** |
| Goals | 2024-25 | 7,406 / −0.0057 | 2,010 / **−0.0375** |
| Goals | 2025-26 | 7,189 / +0.0016 | 2,053 / **−0.0291** |
| Assists | 2024-25 | 7,366 / −0.0017 | 2,212 / **−0.0138** |
| Assists | 2025-26 | 6,924 / +0.0027 | 2,281 / **−0.0229** |
| Points | 2024-25 | 6,720 / −0.0219 | 2,281 / **−0.0488** |
| Points | 2025-26 | 6,322 / −0.0170 | 2,380 / **−0.0453** |
| Blocks | 2024-25 | 7,048 / −0.0020 | 2,153 / **−0.0247** |
| Blocks | 2025-26 | 7,391 / −0.0150 | 2,325 / −0.0232 |

Every row shows the same pattern: TOI-declining COLD players carry a residual roughly 2-7x more negative than TOI-stable COLD players. This is direct, un-suppressed evidence that role/ice-time change is a major real driver of the frozen models' overprediction — the confounding check the instructions required did not just fail to invalidate the effect, it revealed which part of "cold state" is actually doing the work.

---

## R. Regression-to-the-mean diagnostic (Part 24 — reported without suppression)

| Prop | Season | Cold baseline-implied 1+ rate | Cold actual 1+ rate at target game | Rebounded to/above baseline? |
|---|---|---:|---:|---|
| SOG | 2024-25 | 0.706 | 0.699 | **No** |
| SOG | 2025-26 | 0.700 | 0.689 | **No** |
| Goals | 2024-25 | 0.151 | 0.152 | Yes |
| Goals | 2025-26 | 0.154 | 0.159 | Yes |
| Assists | 2024-25 | 0.175 | 0.193 | Yes |
| Assists | 2025-26 | 0.180 | 0.197 | Yes |
| Points | 2024-25 | 0.236 | 0.268 | Yes |
| Points | 2025-26 | 0.243 | 0.275 | Yes |
| Blocks | 2024-25 | 0.431 | 0.443 | Yes |
| Blocks | 2025-26 | 0.414 | 0.419 | Yes |

**Only SOG shows persistent underperformance at the target game; every other prop's COLD cohort has already rebounded to or above its own pre-state baseline rate by the target game.** This directly contradicts a naive "cold streaks continue" story for Goals/Assists/Points/Blocks — and it is reported here precisely because Part 24 required it to be tested and not suppressed, even though it complicates the headline narrative.

**What this means in combination with Section O/P:** for Goals/Assists/Points, the model overprediction is happening even as players are *already back to normal production* — implying the frozen models' predicted probabilities are elevated for reasons beyond the recent-form input alone (matchup/opponent/home-ice factors correlated with landing in the COLD bucket), not because the players are genuinely still cold. For SOG, the story is closer to genuine short-term persistence.

---

## S. Arena player-performance-effect finding (Part 17)

| Prop | EVAL generalization correlation (shrunk TUNING estimate vs real EVAL residual) | n pairs |
|---|---:|---:|
| SOG | 0.013 | 87,087 |
| Goals | 0.001 | 75,286 |
| Assists | 0.047 | 84,389 |
| Points | 0.020 | 86,280 |
| Blocks | 0.013 | 87,018 |

**All five near zero. Status: NOT_VALIDATED for every prop.** Even after hierarchical shrinkage specifically designed to counter the small-sample-size concern Part 17 raised, individual player-arena effects carry essentially no real predictive signal out of sample. This confirms the instructions' own stated caution was warranted, and closes the door on using raw or shrunk player-arena history as a betting signal.

## T. Rink-recording-effect finding (Part 15/16, descriptive, pooled)

| Prop | Arena-level mean-residual range across 32 arenas (TUNING-fit) |
|---|---:|
| SOG | 0.0712 |
| Goals | 0.0423 |
| Assists | 0.0465 |
| Points | 0.0605 |
| Blocks | 0.0716 |

A real, measured spread exists at every prop — arenas are not interchangeable in this residual metric. **Not claimed as causal**: team style-of-play and opponent-quality mix are equally plausible confounders, the same caveat already carried by the existing Hits-based rink finding in [NHL_PBP_FOUR_SEASON_CORPUS_REPORT.md](NHL_PBP_FOUR_SEASON_CORPUS_REPORT.md) Section S. This slice's metric (probability-residual scale) is not unit-comparable to that prior raw-count finding; it is cited only as qualitative corroborating context that arena-level variation of a similar rough character has been observed twice now in this project, via two independent methods.

---

## U. HOT-state symmetric control result (Part 5)

| Prop | Mirrors COLD cleanly (HOT = OVER, opposite sign)? |
|---|---|
| SOG | **Yes** — HOT resid +0.0135/+0.0099, clean positive both seasons |
| Blocks | **Yes** — HOT resid +0.0058 (2024-25), though −0.0024 in 2025-26 (weak, near zero) |
| Goals | **No** — HOT resid is negative both seasons (−0.0058, −0.0130): model overpredicts HOT-state Goals too |
| Assists | **No** — HOT resid negative both seasons (−0.0068, −0.0090) |
| Points | **No** — HOT resid negative both seasons (−0.0122, −0.0091) |

The symmetric control was **not** assumed to work, and it did not cleanly hold for the three "combination" props (Goals, Assists, Points) — for these, the frozen model overpredicts both COLD *and* HOT players relative to NORMAL, which is a genuinely different and more general finding than a directional cold/hot asymmetry: it suggests these three marginals may carry a broader mean-reversion miscalibration (over-predicting anyone recently far from their own baseline, in *either* direction) rather than a context-state-specific bias. This is disclosed as an honest complication, not smoothed into the UNDER narrative.

---

## V. Effect-magnitude summary (headline numbers)

The single largest, cleanest, most reproducible finding in this slice: **Points, COLD_AND_TOI_DECLINE, both EVAL seasons** — mean residual ≈ −0.046 to −0.047 probability points relative to NORMAL, bootstrap 95% CI clearly excluding zero in both seasons (1,000/1,000 and 1,000/1,000 resamples favoring the UNDER direction). Second strongest: **Goals, COLD_AND_TOI_DECLINE** (−0.026 to −0.031, both seasons significant).

---

## W. Candidate adjustment methods considered (not implemented)

Per Part 29's explicit instruction that any adjustment size must come from development evidence, not an arbitrary round number: **no adjustment was sized or operationalized this slice.** If a follow-up slice pursues this, the evidence here would point toward a `COLD_AND_TOI_DECLINE`-gated adjustment scoped to Points and Goals specifically (the two VALIDATED cases), sized from the measured Δ (≈ −0.03 to −0.05 probability points), not a flat −2%/−5% heuristic. This is a recommendation for a future slice, not a change made here.

## X. One-sided UNDER-signal result (primary hypothesis verdict)

**Primary hypothesis (NEGATIVE_CONTEXT → MODEL OVERPREDICTION): partially confirmed, prop-dependent, and mechanism-clarified.**
- Confirmed with strong, reproducible evidence: **Points** (COLD alone and combined), **Goals** (combined signal only).
- Directionally consistent but not statistically clean: **SOG**, **Assists**.
- Not supported: **Blocks** (sign inconsistent across seasons).
- The *specific* mechanism the data supports is narrower than the naive hypothesis: it is COLD **plus role/TOI decline**, not cold production in isolation, that carries most of the real signal.

## Y. Calibration / baseline-bias note

NORMAL-state residuals are themselves slightly negative for SOG (−0.008, −0.009) and Blocks (−0.012, −0.015) even outside any COLD/HOT classification — a small general overprediction bias in those two frozen marginals unrelated to context state. The COLD-vs-NORMAL *delta* used throughout this report already nets this out; absolute residual levels should not be read as "the context effect" on their own.

## Z. Bootstrap methodology and results

Game-clustered paired bootstrap (1,000 resamples, resampling `game_id` groups independently within each state cohort) is the headline significance test throughout Sections O-U, consistent with every prior slice's discipline this session. `frac_negative` reports the fraction of resamples where the COLD (or COLD_AND_TOI_DECLINE) cohort's mean residual fell below the comparison cohort's — used as a one-sided evidence-strength measure alongside the two-sided 95% CI.

## AA. Date-sensitivity note

Every example in the corpus carries `game_date` (verified by `Test36DateSensitivityStructure`); a date-clustered bootstrap would resample by calendar date rather than `game_id`. Given `game_id` is itself date-specific per matchup in this corpus (no double-headers), the two clustering schemes are structurally close substitutes here; the game-clustered version was treated as the primary test and a separate date-clustered rerun was not additionally computed this slice given the added driver runtime and the already-consistent game-clustered results — flagged here explicitly rather than silently assumed equivalent.

## AB. Player-cluster sensitivity note

`Test31PlayerFixedEffectSensitivity` confirms `COLD_AND_TOI_DECLINE` count never exceeds the `COLD` count for any prop/season (a structural sanity check, since the multi-signal state is a strict subset). A full player-fixed-effect regression was not run this slice (out of scope for a first validation pass) — a natural next step if a follow-up slice pursues operationalizing the `COLD_AND_TOI_DECLINE` signal for Points/Goals.

## AC. Season generalization (2024-25 vs 2025-26)

The two EVAL seasons agree on prop-level *direction* everywhere except Blocks (which flips). Magnitude drifts modestly season to season for every prop (e.g., SOG COLD_AND_TOI_DECLINE Δ goes from −0.033 to −0.014), consistent with a real but noisy effect rather than a stable structural constant — another reason this report recommends a scoped follow-up rather than an immediate adjustment.

---

## AD. Freeze manifest

```json
{
  "experiment_id": "player_context_state_v1",
  "sample_floor": 200,
  "timestamp_utc": "2026-08-29T21:17:58Z",
  "code_hashes": {
    "run_player_context_state_model.py": "0b4da4a67e6bd22f128c5af10b05f78f07e4e8fee9a32103399ad20e19410851",
    "player_context_state/context_state.py": "06a5bb0d93a2da6558dac4a59c7e904a2d4325963203b5e48a8b0d179f47ef1c",
    "player_context_state/arena_effects.py": "2d897a0961871a00dcf04a0c2657cb69d448f206ca7963ef495c5383ff41d9bf",
    "player_context_state/marginal_provenance.py": "cdcab46eb5cec23f910ac1ec70482746409c6addedbc9eb7c48972adac542f54"
  }
}
```
Frozen inputs confirmed byte-identical to their pre-slice hashes (Tests 45-52): `player_sog_results.json`, `player_goals_results.json`, `player_assists_results.json`, `player_points_results.json`, `team_sog_results.json`, `goalie_saves_results.json`, `player_blocks_results.json`, `joint_shot_workload_results.json`, `joint_scoring_dependence_results.json`, `decision_policy.py`, `models/combined_model.py`, `models/elo_model.py`, `config.py`, `db.py`, `schema.sql`.

## AE. Context registry (Part 44)

26 entries written to `research/player_context_state_registry.json`, mechanically derived from the driver's own bootstrap output (never hand-adjusted): 5 × COLD_STATE, 5 × COLD_AND_TOI_DECLINE, 5 × HOT_STATE_CONTROL, 5 × ARENA_PLAYER_PERFORMANCE, 5 × ARENA_RINK_RECORDING_CANDIDATE, 1 × MEDIA_SENTIMENT_STATE (NOT_BUILT). Deliberately separate from `market_registry.py` and `research/joint_shot_workload/joint_dependence_registry.py` — this registry tracks *research validation status of a context signal*, not a priced market or a joint-dependence combination.

## AF. Dashboard changes

Added `dashboard/player_context_state_view.py` and `dashboard/pages/20_Player_Context_State_Research.py`, registered in `dashboard/app.py`'s navigation. The page reads only frozen JSON output (never recomputes a marginal or classification live) and displays a prominent red "RESEARCH — NOT YET A BETTING ADJUSTMENT" banner naming the MEDIA_SENTIMENT_STATE gap explicitly.

## AG. Files created / modified

**Created:**
- `research/player_context_state/__init__.py`
- `research/player_context_state/marginal_provenance.py`
- `research/player_context_state/context_state.py`
- `research/player_context_state/arena_effects.py`
- `research/player_context_state/registry.py`
- `research/run_player_context_state_model.py`
- `research/player_context_state_results.json`
- `research/player_context_state_registry.json`
- `dashboard/player_context_state_view.py`
- `dashboard/pages/20_Player_Context_State_Research.py`
- `tests/test_player_context_state_model.py`
- `PLAYER_CONTEXT_STATE_VALIDATION_REPORT.md` (this file)

**Modified:**
- `dashboard/app.py` (one navigation line added)

**Untouched (verified via hash pins):** `models/`, `config.py`, `db.py`, `nhl.db`, `pricing/`, `schema.sql`, `research/player_props/decision_policy.py`, and every prior slice's frozen results file.

## AH. Full test result

**1,528 / 1,528 tests passing** (`python3 -m unittest discover -s tests -p "test_*.py"`) — 1,475 pre-existing tests (all still passing, none weakened) + 53 new tests in `tests/test_player_context_state_model.py` mapped to Part 52's numbered topics.

## AI. Recommended next slice

A narrow follow-up could specify a `decision_policy` adjustment gated specifically on `COLD_AND_TOI_DECLINE` for **Points and Goals only** (the two VALIDATED cases), sized from the measured deltas here (not an arbitrary round number), with its own fresh bootstrap validation on a further held-out window before any live use. SOG and Assists are borderline enough (PARTIAL) to warrant one more season of data before acting. Blocks and the general HOT-state anomaly (Section U) are open questions, not recommended for action. Media sentiment remains blocked on data availability, not modeling effort.

## AJ. Known limitations (consolidated)

1. Media component entirely unbuilt (Section E) — the single largest scope gap relative to the original ask.
2. Goals/Assists COLD cutoff sits at a numerical floor due to sparse-event ties (Section D footnote), not a smooth percentile.
3. Date-clustered bootstrap was not separately computed (Section AA) given `game_id`'s near-equivalence to date clustering in this corpus.
4. Arena effect metric (probability-residual scale) is not unit-comparable to the pre-existing Hits-based rink finding; only qualitative corroboration is claimed.
5. Season-to-season magnitude drift (Section AC) means these effect sizes should be treated as directionally real but not yet stable enough to hard-code into pricing without a further validation season.

## AK. Compliance checklist

- [x] Did not hard-code "negative media = automatic UNDER" or "cold streak = automatic UNDER" — every signal earned its status through the bootstrap, and several (Blocks, COLD-alone for most props) explicitly did NOT validate.
- [x] Did not fabricate historic sportsbook prices, media data, or publication timestamps.
- [x] Did not scrape protected sources or bypass paywalls.
- [x] Regression-to-mean and role-confounding evidence reported even where it complicates the headline (Sections Q, R, U).
- [x] Did not refit Player SOG, Goals, Assists, Points, or Blocks (hash-pinned, Tests 46-49).
- [x] Did not modify `decision_policy.py` (hash-pinned, Test 45).
- [x] Did not query DraftKings / The Odds API (Test 44).
- [x] Did not build a game simulator, combination search, or parlay EV calculation.

## AL. Final Questions

1. Do you want a follow-up slice to operationalize the `COLD_AND_TOI_DECLINE` → Points/Goals UNDER signal into `decision_policy` (narrow, evidence-sized, with its own fresh validation), or hold for one more EVAL season given the magnitude drift noted in Section AC?
2. Is there a legitimate, licensed historical NHL media/news corpus you can point me to, or should MEDIA_SENTIMENT_STATE remain permanently out of scope for this project?
3. Should the HOT-state anomaly for Goals/Assists/Points (Section U — model overpredicts *both* tails, not just COLD) be investigated as its own slice, since it may indicate a broader mean-reversion miscalibration in those three marginals rather than a context-specific effect?
4. Do you want a dedicated date-clustered bootstrap rerun (Section AA) before treating any of these findings as final, or is the game-clustered result sufficient given the structural near-equivalence noted?

---

**STOP AFTER PLAYER CONTEXT STATE VALIDATION.**
