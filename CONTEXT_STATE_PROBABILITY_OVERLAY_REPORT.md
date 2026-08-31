# Context-State Probability Overlay Report

**Slice:** Context-State Probability Overlay / Goals 1+ + Points 1+ UNDER Adjustment Validation / COLD_AND_TOI_DECLINE Only
**Scope:** Research only. No decision_policy change. No refit of any frozen marginal. No sportsbook queries. No parlay/simulator work.
**Critical question:** For players in COLD_AND_TOI_DECLINE state, does P_adjusted beat P_frozen on Brier, log loss, and calibration in **both** evaluation seasons?

---

## A. Exact frozen context definition

Reused directly (not reimplemented) from `research.run_player_context_state_model.build_prop_examples`, guaranteeing byte-identical cutoffs to the completed Player Context State Validation slice:

| Prop | Cold cutoff | Hot cutoff | TOI-decline cutoff |
|---|---:|---:|---:|
| Goals | −11.5129 | +0.6931 | −0.0372 |
| Points | −0.9163 | +0.4055 | −0.0381 |

`COLD_AND_TOI_DECLINE` = form-ratio ≤ cold cutoff **AND** TOI log-ratio ≤ TOI-decline cutoff (`research/player_context_state/context_state.py::classify_multi_signal`, unmodified). No threshold, lookback, or combination rule was changed after seeing evaluation results.

## B. Goals target cohort size

Development (2023-24, TUNING): **2,176** rows. Evaluation: **2,010** (2024-25), **2,053** (2025-26).

## C. Points target cohort size

Development (2023-24, TUNING): **2,338** rows. Evaluation: **2,281** (2024-25), **2,380** (2025-26).

## D. State frequency

`COLD_AND_TOI_DECLINE` occurs in **5.3–5.5%** of eligible player-games across both props and both eval seasons (Goals: 5.35% / 5.44%; Points: 5.27% / 5.54%) — common enough to matter, rare enough that it is a genuine tail state, not a default classification.

## E. Raw Goals calibration (inside the target cohort)

| Season | n | Mean predicted | Mean actual | Residual (overprediction) |
|---|---:|---:|---:|---:|
| 2024-25 | 2,010 | 0.1564 | 0.1189 | −0.0375 |
| 2025-26 | 2,053 | 0.1636 | 0.1344 | −0.0291 |

## F. Raw Points calibration (inside the target cohort)

| Season | n | Mean predicted | Mean actual | Residual (overprediction) |
|---|---:|---:|---:|---:|
| 2024-25 | 2,281 | 0.2742 | 0.2253 | −0.0488 |
| 2025-26 | 2,380 | 0.2844 | 0.2391 | −0.0453 |

This is the exact miscalibration the overlay targets: in both props, both seasons, the frozen model overpredicts scoring probability by 3–5 percentage points for players in this specific state.

## G. Candidate adjustment methods

Five candidates, all fit on DEVELOPMENT (2023-24) data only, frozen before evaluation:

- **A. NO_ADJUSTMENT** — null candidate, always in the contest.
- **B. FIXED_LOGIT_OFFSET** — `logit(p_adj) = logit(p_raw) + offset`, offset ≤ 0, grid-searched to minimize DEV log loss.
- **C. SHRUNK_LOGIT_OFFSET** — B's offset shrunk toward 0 by DEV sample size (`n/(n+100)`).
- **D. BAYESIAN_CONTEXT_BLEND** — probability-space shift `p_raw + shrink·(dev_actual_rate − dev_mean_raw_p)`, `shrink = n/(n+200)`.
- **E. ISOTONIC_BIN_RECAL** — equal-frequency DEV bins pooled via pool-adjacent-violators (PAVA); only fit if DEV n ≥ 400 and bins ≥ 40 rows each (both props cleared this floor).

## H. Selected Goals adjustment

**B_FIXED_LOGIT_OFFSET**, offset = **−0.180** (DEV log loss 0.38724 vs. 0.38929 baseline; C/D/E all close behind at 0.38725/0.38755/0.38826 — B genuinely won a real contest, not a default).

## I. Selected Points adjustment

**D_BAYESIAN_CONTEXT_BLEND**, shift = **−0.0415** (DEV log loss 0.52845 vs. 0.53386 baseline; B/C close behind at 0.52850/0.52851, E at 0.52883). Note the winner differs by prop — this was not assumed going in; the two prop families warranted architecturally different candidates.

## J. Development-fit adjustment magnitudes

| Prop | Mean abs. change | Median | 5th pct. | 95th pct. |
|---|---:|---:|---:|---:|
| Goals | 0.0222 | 0.0222 | 0.0100 | 0.0345 |
| Points | 0.0415 | 0.0415 | 0.0415 | 0.0415 |

Points' shift is a **constant** additive probability shift (candidate D applies the same shrink-weighted shift to every row regardless of baseline probability) — this is a mechanical property of the winning candidate, not a rounding artifact, and is disclosed explicitly here rather than implied to vary. Goals' offset (candidate B, logit-additive) varies by baseline probability level, ranging roughly 1–3.5 percentage points across the DEV distribution. EVAL-season magnitudes match DEV almost exactly (Goals: −2.17pp / −2.25pp mean-predicted shift; Points: −4.15pp / −4.15pp) — a good generalization signal, not overfit to development data.

Example: **Eeli Tolvanen** (SEA vs. STL, 2024-10-08): raw Points 1+ probability 40.7% → adjusted 36.6% (−4.2pp); actual outcome: scored a point (this is a disclosed context-state "false positive" in the sense that the overlay pointed the right direction on average across the cohort, but any individual game can still go the other way — see Section AJ).

## K. Freeze manifest

```json
{
  "experiment_id": "context_overlay_v1",
  "context_state_used": "COLD_AND_TOI_DECLINE only",
  "winner_by_prop": {"goals": "B_FIXED_LOGIT_OFFSET", "points": "D_BAYESIAN_CONTEXT_BLEND"},
  "development_season": 20232024,
  "eval_seasons": [20242025, 20252026],
  "sample_floors": {"dev": 300, "eval": 150, "confidence_bucket": 30},
  "no_media_used": true, "no_arena_adjustment": true, "no_sportsbook_calls": true,
  "no_marginal_refit": true, "no_decision_policy_change": true,
  "code_hashes": {
    "run_context_overlay_model.py": "fe487ae36f6e3d5ae2e718ab640126ea4e5bbef0ac970fac2796cc8886f665bf",
    "context_overlay/overlay_models.py": "5ce96fe6708f5a059d62ff96bdc95a0bd842ab14e573e3f4ae0d042333e976d6",
    "context_overlay/confidence_helpers.py": "0bc99662d78255e7efe1b6ff10fea16a3a3a197037c4659ccf145a26e5dbfb94"
  }
}
```

## L. Goals 2024-25: Brier raw vs. adjusted

**0.10244 → 0.10127** (−0.00117, a 1.1% relative reduction).

## M. Goals 2024-25: log loss raw vs. adjusted

**0.35338 → 0.34863** (−0.00476, a 1.3% relative reduction).

## N. Goals 2025-26: Brier raw vs. adjusted

**0.11542 → 0.11440** (−0.00102, a 0.9% relative reduction).

## O. Goals 2025-26: log loss raw vs. adjusted

**0.38899 → 0.38581** (−0.00318, a 0.8% relative reduction).

## P. Goals calibration changes

| Season | Raw residual | Adjusted residual |
|---|---:|---:|
| 2024-25 | −0.0375 | **−0.0158** |
| 2025-26 | −0.0291 | **−0.0066** |

The overlay does not fully close the gap (a single global offset can't chase every subgroup exactly), but it closes 58–77% of the raw overprediction in both seasons.

## Q. Goals game bootstrap

| Season | Point Δ | 95% CI | Frac. improved |
|---|---:|---|---:|
| 2024-25 | −0.00117 | [−0.00194, −0.00047] | **1.000** |
| 2025-26 | −0.00102 | [−0.00177, −0.00021] | **0.995** |

## R. Goals date sensitivity

| Season | Point Δ | 95% CI | Frac. improved |
|---|---:|---|---:|
| 2024-25 | −0.00117 | [−0.00180, −0.00049] | **1.000** |
| 2025-26 | −0.00102 | [−0.00169, −0.00031] | **0.998** |

Date-clustered bootstrap agrees with game-clustered in both direction and magnitude — this closes the open question flagged in the prior Player Context State report (Section AA there noted date-clustering had not been separately run).

## S. Goals player sensitivity

| Season | Point Δ | 95% CI | Frac. improved | Unique players | Top-10 share | Top-1 share |
|---|---:|---|---:|---:|---:|---:|
| 2024-25 | −0.00117 | [−0.00191, −0.00041] | 1.000 | 418 | 7.5% | 0.80% |
| 2025-26 | −0.00102 | [−0.00178, −0.00020] | 0.991 | 439 | 7.5% | 0.93% |

Player-clustered bootstrap does not invalidate the result, and the cohort is not dominated by a handful of stars — 418–439 unique players per season, top-10 players account for only ~7.5% of rows.

## T. Points 2024-25: Brier raw vs. adjusted

**0.16901 → 0.16668** (−0.00233, a 1.4% relative reduction).

## U. Points 2024-25: log loss raw vs. adjusted

**0.51943 → 0.51356** (−0.00587, a 1.1% relative reduction).

## V. Points 2025-26: Brier raw vs. adjusted

**0.17598 → 0.17394** (−0.00204, a 1.2% relative reduction).

## W. Points 2025-26: log loss raw vs. adjusted

**0.53390 → 0.52850** (−0.00539, a 1.0% relative reduction).

## X. Points calibration changes

| Season | Raw residual | Adjusted residual |
|---|---:|---:|
| 2024-25 | −0.0488 | **−0.0073** |
| 2025-26 | −0.0453 | **−0.0038** |

The Points overlay closes 85–92% of the raw overprediction — a larger fractional correction than Goals, consistent with the constant-shift candidate (D) being close to the cohort's actual mean miscalibration by construction.

## Y. Points game bootstrap

| Season | Point Δ | 95% CI | Frac. improved |
|---|---:|---|---:|
| 2024-25 | −0.00233 | [−0.00385, −0.00086] | **0.998** |
| 2025-26 | −0.00204 | [−0.00340, −0.00056] | **0.999** |

## Z. Points date sensitivity

| Season | Point Δ | 95% CI | Frac. improved |
|---|---:|---|---:|
| 2024-25 | −0.00233 | [−0.00371, −0.00078] | **0.999** |
| 2025-26 | −0.00204 | [−0.00343, −0.00060] | **0.996** |

## AA. Points player sensitivity

| Season | Point Δ | 95% CI | Frac. improved | Unique players | Top-10 share | Top-1 share |
|---|---:|---|---:|---:|---:|---:|
| 2024-25 | −0.00233 | [−0.00372, −0.00081] | 1.000 | 482 | 8.0% | 1.10% |
| 2025-26 | −0.00204 | [−0.00338, −0.00059] | 0.995 | 500 | 7.6% | 0.88% |

Same conclusion as Goals: broad-based, not star-driven.

## AB. Adjustment magnitude distribution

See Section J. Both props' adjustments are small relative to the underlying probabilities being adjusted (Goals: ~10–20% relative reduction off a ~15–16% base rate; Points: ~15% relative reduction off a ~27–28% base rate) — neither overlay dominates or replaces the frozen model's own signal.

## AC. Confidence interaction

| Prop | Season | HIGH n / raw→adj Brier | MEDIUM n / raw→adj Brier | LOW n / raw→adj Brier |
|---|---|---|---|---|
| Goals | 2024-25 | 1,205 / 0.1090→0.1079 | 792 / 0.0920→0.0908 | 13 / INSUFFICIENT_DATA |
| Goals | 2025-26 | 1,202 / 0.1212→0.1206 | 821 / 0.1070→0.1054 | 30 / 0.1133→0.1133 (flat) |
| Points | 2024-25 | 1,042 / 0.1792→0.1781 | 1,216 / 0.1604→0.1570 | 23 / INSUFFICIENT_DATA |
| Points | 2025-26 | 1,094 / 0.1832→0.1817 | 1,231 / 0.1706→0.1684 | 55 / 0.1522→0.1443 |

The overlay improves Brier in every HIGH and MEDIUM bucket across both props and both seasons. LOW-confidence buckets are mostly below the 30-row floor (INSUFFICIENT_DATA) or effectively flat — moot regardless, since LOW-confidence Goals/Points are already policy-capped to WATCH_ONLY (Section AD) and cannot reach BET eligibility no matter what this overlay does.

## AD. LOW policy inheritance

`decision_policy.gate_low_confidence("GOALS", "LOW", "BET")` → `final_decision = "WATCH"`. `decision_policy.gate_low_confidence("POINTS", "LOW", "BET")` → `final_decision = "WATCH"`. Confirmed structurally (Tests 32–33): the overlay only ever changes a probability value; it cannot and does not touch `decision_policy.py` or narrow/widen any existing eligibility ceiling. `POLICY_VERSION = "prop_decision_policy_v3"` unchanged (hash-pinned).

## AE. Conservative-probability architecture

**Documented, not operationalized** (Part 27's own instruction). Preferred order: **RAW MARGINAL → CONTEXT ADJUSTMENT → CONSERVATIVE PROBABILITY.** For Goals, a `mu` exists, so a future integration would apply `count_models.conservative_mu` to the *adjusted* mu (or equivalently reformulate the offset in mu-space) rather than penalizing an already-conservative probability a second time. For **Points**, there is no `mu` under the empirical-baseline champion — the conservative-probability layer as currently implemented (`conservative_mu`, count-scale) does not apply to Points at all today, overlay or no overlay; a probability-domain conservative treatment for Points would need its own separate design, not built this slice. No code change was made to the conservative-probability layer.

## AF. Logical-coherence result

Raw marginals: **0 violations** of P(Goal≥1) ≤ P(Point≥1) in both eval seasons (37,563 / 37,723 shared player-games checked) — this specific pair is cleaner than the ASSIST/POINT pair that required clipping in the prior Joint Scoring Dependence slice.

**A real, disclosed problem was found and fixed:** applying the two overlays independently (different candidate families, different offsets) introduced **16 new violations** in 2024-25 (0.57% of 2,800 checked adjusted pairs) — all from a single player (id `8476822`) whose Goals-adjusted probability exceeded their Points probability (which itself was not COLD_AND_TOI_DECLINE-adjusted in those specific games). **0 violations** in 2025-26. Fixed via the same non-destructive clip pattern already used in `research/joint_scoring_dependence` (raise the lower, logically-implied-by, probability up to match: `point_adjusted = max(point_adjusted, goal_adjusted)`), verified to bring violations to exactly **0** in both seasons. The fix costs a small, real Brier degradation on the 16 affected rows (mean +0.00142) since all 16 had `actual = 0` for Points — an honest, expected cost of respecting a logical constraint, immaterial to the aggregate results in Sections L–W (16 rows out of 2,281 Points rows in that season, 0.7%).

## AG. Existing joint-model integration architecture

**Documented, not implemented this slice** (Part 28/41). Intended flow: RAW MARGINAL → CONTEXT ADJUSTMENT (this slice) → existing non-destructive Fréchet/logical-coherence layer (unchanged) → JOINT DEPENDENCE LAYER (unchanged ρ/copula parameters from `joint_scoring_dependence_results.json`, hash-pinned, confirmed untouched). If this overlay is later adopted, the joint layer would consume the *adjusted* Goals/Points marginals in place of raw ones; the copula/structural correlation parameters themselves would not need retraining, since those parameters describe the dependence structure between outcomes, not the marginal levels — but this substitution was **not implemented or tested** this slice (Part 28 explicitly: "do not retrain SOG+Goal, SOG+Point, etc.").

## AH. Context overlay registry

Two entries in `research/context_overlay_registry.json`:

| Signal | Status | Adjustment | Operational status |
|---|---|---|---|
| `PLAYER_GOALS_1PLUS__COLD_AND_TOI_DECLINE` | **VALIDATED_OVERLAY** | B_FIXED_LOGIT_OFFSET (−0.180) | RESEARCH |
| `PLAYER_POINTS_1PLUS__COLD_AND_TOI_DECLINE` | **VALIDATED_OVERLAY** | D_BAYESIAN_CONTEXT_BLEND (−0.0415) | RESEARCH |

Status is computed mechanically from Part 40's checkable criteria (Brier/log-loss/calibration improvement, all three bootstrap clusterings, no new coherence violations after fix) — both entries pass every checkable criterion in both eval seasons. `market_registry.py` is unmodified: Goals stays `VALIDATED`, Points stays `EMPIRICAL_BASELINE_REMAINS_CHAMPION` at the marginal level (this registry sits above, not inside, that one).

## AI. Dashboard changes

Extended `dashboard/pages/20_Player_Context_State_Research.py` (not a new page, per Part 38's "extend") with a new "Context-State Probability Overlay" section, backed by new `dashboard/context_overlay_view.py`. Shows raw vs. adjusted Brier/log-loss/calibration per season, the game-clustered bootstrap, and the registry status — all read from frozen JSON, nothing recomputed live. A red "RESEARCH CONTEXT OVERLAY — NOT YET A BETTING ADJUSTMENT" banner is shown prominently, naming that WATCH_ONLY restrictions still apply regardless of overlay status.

## AJ. Representative examples (real historical rows, 2024-25)

| Case | Player | Game | Raw P | Adj. P | Actual | Confidence |
|---|---|---|---:|---:|---:|---|
| Goals — large adjustment | David Pastrnak (BOS vs ANA, 2025-03-26) | 0.438 | 0.394 | scored | HIGH |
| Goals — small adjustment | TJ Brodie (CHI vs CAR, 2025-01-20) | 0.033 | 0.028 | no goal | HIGH |
| Goals — correct UNDER direction | Tye Kartye (SEA vs WPG, 2024-10-24) | 0.148 | 0.127 | no goal | MEDIUM |
| Goals — context-state false positive | Jonatan Berggren (DET vs UTA, 2025-03-06) | 0.176 | 0.152 | scored | MEDIUM |
| Goals — LOW confidence (WATCH_ONLY regardless) | Justin Brazeau (BOS vs DAL, 2024-10-24) | 0.202 | 0.174 | scored | LOW |
| Points — large/only adjustment (constant shift) | Eeli Tolvanen (SEA vs STL, 2024-10-08) | 0.407 | 0.366 | scored a point | HIGH |
| Points — correct UNDER direction | Simon Benoit (TOR vs CAR, 2025-01-09) | 0.124 | 0.082 | no point | HIGH |
| Points — context-state false positive | Brandon Tanev (SEA vs BUF, 2025-01-11) | 0.301 | 0.260 | scored a point | HIGH |
| Points — LOW confidence (WATCH_ONLY regardless) | Dougie Hamilton (NJD vs UTA, 2024-10-14) | 0.548 | 0.506 | no point | LOW |

No sportsbook odds are shown or referenced anywhere in this table or this report.

## AK. Files created / modified

**Created:**
- `research/context_overlay/__init__.py`
- `research/context_overlay/overlay_models.py`
- `research/context_overlay/confidence_helpers.py`
- `research/context_overlay/registry.py`
- `research/run_context_overlay_model.py`
- `research/context_overlay_results.json`
- `research/context_overlay_registry.json`
- `dashboard/context_overlay_view.py`
- `tests/test_context_overlay_model.py`
- `CONTEXT_STATE_PROBABILITY_OVERLAY_REPORT.md` (this file)

**Modified:**
- `dashboard/pages/20_Player_Context_State_Research.py` (extended with an overlay section, per Part 38)

**Untouched (verified via hash pins):** `models/`, `config.py`, `db.py`, `nhl.db`, `pricing/`, `schema.sql`, `research/player_props/decision_policy.py`, `market_registry.py`, every frozen marginal results file (SOG/Goals/Assists/Points/Blocks/Team SOG/Goalie Saves), both joint-dependence results files, `research/player_context_state/*.py` and its results file's *code*.

## AL. Full test result

**1,591 / 1,591 tests passing** (`python3 -m unittest discover -s tests -p "test_*.py"`) — 1,528 pre-existing tests (all still passing, none weakened) + 63 new tests in `tests/test_context_overlay_model.py` mapped to Part 44's 53 numbered topics.

## AM. Recommended next single development slice

A **narrow decision_policy integration slice** for exactly these two validated overlays: wire `COLD_AND_TOI_DECLINE`-gated Goals and Points adjusted probabilities into a new, explicit `decision_policy` code path (still HIGH/MEDIUM confidence only, still respecting the existing WATCH_ONLY ceiling), with its own pre-registered adoption bar and a genuinely held-out validation window (ideally early-2025-26 data not yet used in EITHER this slice's DEV or EVAL). This should explicitly NOT attempt sportsbook-price comparison in the same slice (Part 26's own caution: probability improvement ≠ market edge) — that would be a separate, later slice once/if historical price data are confirmed locally available.

---

## Compliance checklist

- [x] Frozen state definition reused byte-identically, never changed after seeing results.
- [x] Adjustment tuned on DEVELOPMENT (2023-24) only; EVAL seasons used only for scoring.
- [x] Raw probability preserved everywhere; adjusted probability stored separately, never overwriting raw.
- [x] No hand-chosen percentage penalty — every offset/shift came from a DEV-data-minimized grid search or blend.
- [x] Regression/confounding-style honesty preserved: LOW-confidence buckets, small-sample flags, and the 16-row coherence-fix cost are disclosed, not hidden.
- [x] Did not claim sportsbook edge — this slice validates probability improvement only (Part 26).
- [x] decision_policy v3 unchanged (hash-pinned). No sportsbook/odds API calls. No marginal refits. No media, no arena adjustment, no HOT-state overlay, no full simulator, no parlay logic.

---

## Final Questions

**DOES COLD_AND_TOI_DECLINE REMAIN A VALID GOALS UNDER SIGNAL?**
YES

**DOES A GOALS CONTEXT OVERLAY IMPROVE BRIER IN BOTH EVAL SEASONS?**
YES

**DOES IT IMPROVE LOG LOSS IN BOTH?**
YES

**IS GOALS CONTEXT OVERLAY VALIDATED?**
YES

**DOES COLD_AND_TOI_DECLINE REMAIN A VALID POINTS UNDER SIGNAL?**
YES

**DOES A POINTS CONTEXT OVERLAY IMPROVE BRIER IN BOTH EVAL SEASONS?**
YES

**DOES IT IMPROVE LOG LOSS IN BOTH?**
YES

**IS POINTS CONTEXT OVERLAY VALIDATED?**
YES

**WHAT IS THE TYPICAL ABSOLUTE PROBABILITY ADJUSTMENT?**
~2.2 percentage points (Goals, ranging ~1.0–3.5pp), ~4.15 percentage points (Points, constant)

**IS THE ADJUSTMENT SMALL RATHER THAN MODEL-DOMINATING?**
YES

**DOES PURE COLD STATE RECEIVE AN ADJUSTMENT?**
NO

**DO SOG / ASSISTS / BLOCKS RECEIVE AN ADJUSTMENT?**
NO

**DO ARENA PLAYER EFFECTS RECEIVE AN ADJUSTMENT?**
NO

**IS MEDIA SENTIMENT USED?**
NO

**DO EXISTING LOW-CONFIDENCE WATCH_ONLY POLICIES REMAIN?**
YES

**ARE RAW MARGINAL PROBABILITIES PRESERVED?**
YES

**ARE ADJUSTED PROBABILITIES STORED SEPARATELY?**
YES

**DO ADJUSTED GOAL/POINT PROBABILITIES REMAIN LOGICALLY COHERENT FOR JOINT USE?**
YES (after the disclosed 16-row fix in 2024-25; 0 violations in both seasons post-fix)

**IS THE CONTEXT OVERLAY READY TO MODIFY DECISION POLICY?**
NO

**IS IT READY TO CLAIM SPORTSBOOK UNDER EDGE WITHOUT MARKET PRICES?**
NO

**WERE ANY EXISTING MARGINAL MODELS REFIT?**
NO

**WERE ANY EXISTING JOINT MODELS REFIT?**
NO

**WAS CONFIDENCE CHANGED?**
NO

**WAS DECISION POLICY v3 CHANGED?**
NO

**WAS NHL WIN MODEL CHANGED?**
NO

**CURRENT FULL TEST RESULT?**
1,591 / 1,591

**WHAT IS NOW THE HIGHEST-LEVERAGE NEXT DEVELOPMENT SLICE?**
A narrow decision_policy integration slice wiring these two validated overlays (Goals + Points, COLD_AND_TOI_DECLINE, HIGH/MEDIUM confidence only) into a new explicit policy path, with a genuinely held-out validation window — before any sportsbook-price/edge work.

---

**STOP AFTER CONTEXT PROBABILITY OVERLAY.**
