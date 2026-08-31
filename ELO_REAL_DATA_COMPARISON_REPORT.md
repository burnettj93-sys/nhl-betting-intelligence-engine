# Real-Corpus Elo Comparison — Controlled Model Experiment Report

**This turn is a controlled experiment only.** No production file was
modified. `models/elo_model.py`, `models/combined_model.py`, `config.py`,
player/goalie/rest/pricing/threshold/decision code, MoneyPuck, xG, and
The Odds API are all untouched. All work lives under `research/` plus one
new test file. Full suite re-confirmed at the end of this report: **345 /
345 passing** (322 pre-existing + 23 new).

Everything numeric in this report is generated directly by
`research/run_elo_comparison.py` and persisted verbatim in
`research/elo_comparison_results.json` — nothing below was hand-typed or
estimated; if a number here and that file ever disagree, the JSON file is
the source of truth (rerun the script to regenerate both).

---

## A. Exact audit of current Elo (read from source this turn, not from prior reports)

Read directly from `models/elo_model.py` and `config.py`:

1. **Expected-score formula**: standard logistic Elo —
   `1.0 / (1.0 + 10 ** (-(home_r - away_r) / 400.0))`.
2. **Starting Elo**: `config.ELO_START = 1500.0`.
3. **K-factor**: `config.ELO_K_FACTOR = 20.0`, a single constant, no
   dynamic/variable K.
4. **Home-ice adjustment**: `config.ELO_HOME_ADVANTAGE = 35.0`, a flat
   additive bump to the home rating before the logistic transform.
5. **Season-reset/regression**: `maybe_regress_new_season()` — on a
   season-label change, every team's rating moves
   `config.ELO_SEASON_REGRESSION = 0.30` (30%) of the distance back
   toward `ELO_START`.
6. **Regulation wins**: not distinguished from any other decision type —
   `update()`'s only inputs are `home_team`, `away_team`, `home_won`
   (bool).
7. **Overtime wins**: not distinguished at all — `final_period_type` is
   never read by `EloModel`.
8. **Shootout wins**: same — not distinguished.
9. **Goal differential**: never used — `home_score`/`away_score` are not
   passed into `update()`.
10. **Opponent strength / update magnitude**: opponent rating already
    enters through `p_home` (standard Elo — a bigger surprise produces a
    bigger update), but there is no *separate* opponent-strength
    adjustment beyond that.
11. **Elo → win probability**: `win_probability()`, the same logistic
    transform as #1, optionally with `extra_home_adj`/`extra_away_adj`
    (unused by `update()`, see #12).
12. **Elo → combined model**: `combined_model.py::compute_probability_from_features()`
    adds Elo rating (already `+home_advantage`) plus player/goalie/rest
    terms into ONE logistic transform for *prediction*; `update()` itself
    always uses the **base** expectation only
    (`config.ELO_UPDATES_ON_BASE_EXPECTATION = True`) — deliberately not
    double-counting player/goalie/rest signal into team-rating learning.
13. **Caps/floors**: none on the update `delta` itself; season regression
    is the only mechanism pulling ratings back toward `ELO_START`.
14. **Files/constants**: `models/elo_model.py` (`EloModel`); `config.py`
    lines ~65-78 (`ELO_START`, `ELO_K_FACTOR`, `ELO_HOME_ADVANTAGE`,
    `ELO_SEASON_REGRESSION`, `ELO_UPDATES_ON_BASE_EXPECTATION`).

**Conclusion**: current Elo sees a single bit (`home_won`) per game and
ignores both margin and how the game ended, even though nothing here was
changed — this audit simply re-confirms what the prior
`TEAM_STRENGTH_ELO_REPORT.md` already found, this time from source, this
turn.

---

## B. Research evaluation methodology

- **Scope**: ELO-ONLY win probability (rating diff + home-ice, via
  `win_probability()`'s exact formula), evaluated against real final
  results. Player quality, goalie adjustment, rest penalties, the
  uncertainty band, pricing, and thresholds are frozen and not part of
  this comparison — required by Part 2, and unavoidable anyway since the
  real corpus (`research/real_nhl_results/normalized_regular_season_games.jsonl`)
  contains only game-level schedule/result data, no boxscore/roster
  information the combined model would need.
- **Isolation**: every candidate uses the identical `win_probability()`
  formula and identical season-regression rule (both reusing
  `config.py`'s real constants, not re-typed copies) — only the update
  **weight** (how much a result moves the rating) varies across
  candidates. See `research/elo_comparison.py`'s
  `TestProductionEloEquivalence` test for a direct proof that Candidate A
  (`weight_fn=None`) is mathematically identical to unmodified production
  `EloModel`.
- **Data eligibility**: STRICT PRIOR-GAME-DATE (see Part C/Section D
  below and `research/real_nhl_results/README.md`) — a game on date `D`
  may only be learned from if `game_date < D`. Enforced structurally by a
  two-pass-per-date loop (`run_walkforward()`): every game on date `D` is
  PREDICTED first, using only state built from strictly-earlier dates;
  only once every game on `D` has been predicted does the function LEARN
  from `D`'s results. Same-day games never influence each other's
  predictions regardless of real-world completion order.
- **Chronology**: entirely by the corpus's real `game_date` field. No
  `game_id` comparison or list-position slicing is used anywhere in
  `research/elo_comparison.py` or `research/run_elo_comparison.py` — both
  pass `tests/test_training_path_structural_audit.py`'s AST-level scan
  even though (being outside `models/` and never touching a bitemporal
  table) they aren't required to.

---

## C. Tuning/evaluation split

Followed Part 11's **PREFERRED** approach exactly:

| Season | Role |
|---|---|
| 2022-23 | **WARM-UP** — builds initial rating state only; its own numbers are shown below for context but never used as evidence of anything |
| 2023-24 | **TUNING/CANDIDATE-SELECTION** — used only to pick the winning parameter value within each of Candidate B's and Candidate C's small grids |
| 2024-25 | **TRUE EVALUATION** — never touched during selection |
| 2025-26 | **TRUE EVALUATION** — never touched during selection |

Each candidate (baseline + every tuning-grid variant) is run as **one
continuous walk-forward trajectory across all 4 seasons** — ratings carry
forward from warm-up through tuning into evaluation, with the same
season-boundary regression production uses applied at every season
change. Evaluation metrics are then sliced out of that single trajectory
by season; no season is ever re-predicted from a restarted state, and no
game is ever shuffled or randomly split.

No hyperparameter choice was made using 2024-25 or 2025-26 data at any
point. `research/run_elo_comparison.py` computes the B/C winners from the
2023-24 slice alone, *then* builds Candidate D from those winners, *then*
evaluates all four final candidates (A/B*/C*/D) on 2024-25+2025-26.

---

## D. Candidate formulas

All four keep `win_probability()` byte-identical to production. `p_home`
is the existing base expectation; `actual` is 1.0/0.0 for
home win/loss.

- **A — baseline (current, unchanged)**: `delta = K * (actual - p_home)`.
- **B — OT/SO-aware**: `delta = K * w_otso * (actual - p_home)`, where
  `w_otso = 1.0` for `REG` and a single reduced weight for `OT`/`SO`
  (not split separately between OT and SO this slice — kept small per
  instruction).
- **C — capped margin-of-victory**: `delta = K * mov_mult * (actual - p_home)`,
  `mov_mult = log(1 + min(|home_score - away_score|, mov_cap)) / log(2)`
  — normalized so a 1-goal margin (the modal NHL result, and the *exact*
  margin every OT/SO game has by rule) is neutral (`mov_mult == 1.0`).
  Ignores `period_type` entirely.
- **D — combined**: `delta = K * w_otso * mov_mult * (actual - p_home)`.

---

## E. Parameter values tested (tuning grid, 2023-24 season only)

| Candidate | Grid | Selection metric |
|---|---|---|
| B (OT/SO weight) | `{0.75, 0.67, 0.50}` | lowest Brier on 2023-24 |
| C (MOV cap) | `{2, 3, 4}` | lowest Brier on 2023-24 |

7 total tuning-phase runs (1 baseline + 3 B + 3 C) plus 1 final D run = 8
parameterizations total — close to, and more disciplined than, the ~9
originally floated, since D is built directly from B's and C's own
winners rather than a full 3×3 cross product.

**Tuning results (2023-24, n=1,312 games each):**

| Config | Brier | Log loss |
|---|---|---|
| A baseline | 0.23797 | 0.66864 |
| B, otso=0.75 | 0.23775 | 0.66807 |
| B, otso=0.67 | 0.23771 | 0.66797 |
| **B, otso=0.50 (selected)** | **0.23771** | **0.66789** |
| C, cap=2 | 0.24049 | 0.67447 |
| C, cap=3 | 0.24188 | 0.67793 |
| C, cap=4 | 0.24231 | 0.67905 |
| **C winner: cap=2 (least-bad of a uniformly worse-than-baseline grid)** | | |

**Selected parameters**: `B* = otso_weight 0.50`, `C* = mov_cap 2`,
`D = combined(otso_weight=0.50, mov_cap=2)`.

Worth flagging plainly: every single value in Candidate C's tuning grid
is *worse* than baseline on the tuning season — `cap=2` was selected only
because "least bad" is still how a minimum-Brier selection rule works,
not because it looked promising. That result already foreshadows Section
K below.

---

## F. Exact game counts used

- Full corpus: **5,248** real regular-season games, 4 seasons, 1,312 each
  (verified again this run: `all_seasons_present == {2022-23, 2023-24,
  2024-25, 2025-26}`).
- Warm-up (2022-23): 1,312 games — state-building only.
- Tuning (2023-24): 1,312 games — candidate-selection only.
- **True evaluation (2024-25 + 2025-26): 2,624 games** — the number
  every headline metric in this report (Sections I-M) is computed from.
- Every candidate predicted the **identical** 2,624-game evaluation set
  (mechanically asserted in `run_elo_comparison.py`, and by
  `tests/test_elo_comparison_research.py::TestReproducibilityAndSharedEvaluationSet`).

## G. Seasons used for warm-up/tuning

2022-23 (warm-up), 2023-24 (tuning).

## H. Seasons used for true evaluation

2024-25, 2025-26.

---

## I / J. Baseline aggregate Brier / log loss (true evaluation set, n=2,624)

- **Brier: 0.245456**
- **Log loss: 0.684295**
- Mean predicted P(home win): 0.5467; actual home win rate: 0.5423;
  calibration error: 0.00444.

## K / L. Each candidate's aggregate Brier / log loss (true evaluation set, n=2,624)

| Candidate | Brier | Log loss | Mean pred | Actual rate | Cal. error |
|---|---|---|---|---|---|
| A baseline | 0.245456 | 0.684295 | 0.5467 | 0.5423 | 0.00444 |
| **B (otso=0.50)** | **0.245141** | **0.683550** | 0.5467 | 0.5423 | 0.00438 |
| C (cap=2) | 0.247996 | 0.689909 | 0.5457 | 0.5423 | 0.00343 |
| D (combined) | 0.248009 | 0.689926 | 0.5456 | 0.5423 | 0.00333 |

## M. Absolute / relative deltas vs. baseline (true evaluation set)

| Candidate | Δ Brier (abs) | Δ Brier (rel) | Δ log loss (abs) | Δ log loss (rel) |
|---|---|---|---|---|
| B | **-0.000315** | **-0.128%** | -0.000745 | -0.109% |
| C | +0.002540 | +1.035% | +0.005614 | +0.820% |
| D | +0.002554 | +1.040% | +0.005630 | +0.823% |

B improves both metrics, but by a fraction of a percent. C and D are
clearly, consistently *worse* than baseline — not a rounding artifact
(see the bootstrap intervals in Section T, which put 0% of resampled
outcomes on the "C/D improves" side).

---

## N / O. Season-by-season Brier / log loss (true evaluation seasons only)

| Season | A baseline Brier | B Brier | Δ | A baseline LL | B LL | Δ |
|---|---|---|---|---|---|---|
| 2024-25 | 0.24018 | 0.23964 | -0.00054 | 0.67332 | 0.67210 | -0.00122 |
| 2025-26 | 0.25073 | 0.25064 | -0.00009 | 0.69527 | 0.69500 | -0.00027 |

| Season | A baseline Brier | C Brier | Δ | A baseline LL | C LL | Δ |
|---|---|---|---|---|---|---|
| 2024-25 | 0.24018 | 0.24299 | +0.00281 | 0.67332 | 0.67963 | +0.00631 |
| 2025-26 | 0.25073 | 0.25300 | +0.00227 | 0.69527 | 0.70019 | +0.00492 |

| Season | A baseline Brier | D Brier | Δ | A baseline LL | D LL | Δ |
|---|---|---|---|---|---|---|
| 2024-25 | 0.24018 | 0.24269 | +0.00251 | 0.67332 | 0.67897 | +0.00565 |
| 2025-26 | 0.25073 | 0.25333 | +0.00260 | 0.69527 | 0.70088 | +0.00561 |

**Consistency finding**: B improves Brier and log loss in *both*
evaluation seasons (small each time). C and D are worse in *both*
evaluation seasons — and worse in the warm-up and tuning seasons too
(Section E) — a consistent, not noisy, direction of harm across all 4
seasons.

*(Context only, 2022-23/2023-24 not part of the blind evaluation — full
per-season numbers for all 4 candidates across all 4 seasons are in
`research/elo_comparison_results.json` under `final_candidates.*.season_breakdown`.)*

---

## P. Calibration tables (true evaluation set, buckets 0.30-0.75)

Baseline (A) vs. Candidate B, `[lo, hi)` buckets:

| Bucket | A n | A mean pred | A actual | A cal. err | B n | B mean pred | B actual | B cal. err |
|---|---|---|---|---|---|---|---|---|
| 0.30-0.35 | 74 | 0.3292 | 0.4189 | 0.0898 | 74 | 0.3315 | 0.3514 | 0.0198 |
| 0.35-0.40 | 159 | 0.3771 | 0.4528 | 0.0757 | 170 | 0.3782 | 0.4765 | 0.0983 |
| 0.40-0.45 | 232 | 0.4264 | 0.4483 | 0.0219 | 215 | 0.4276 | 0.4605 | 0.0329 |
| 0.45-0.50 | 350 | 0.4762 | 0.5429 | 0.0667 | 379 | 0.4761 | 0.5303 | 0.0542 |
| 0.50-0.55 | 448 | 0.5245 | 0.5491 | 0.0246 | 412 | 0.5258 | 0.5728 | 0.0471 |
| 0.55-0.60 | 449 | 0.5742 | 0.5033 | 0.0708 | 449 | 0.5757 | 0.5078 | 0.0679 |
| 0.60-0.65 | 350 | 0.6239 | 0.5771 | 0.0467 | 366 | 0.6255 | 0.5492 | 0.0763 |
| 0.65-0.70 | 264 | 0.6737 | 0.6629 | 0.0108 | 240 | 0.6746 | 0.6708 | 0.0038 |
| 0.70-0.75 | 150 | 0.7229 | 0.6267 | 0.0962 | 156 | 0.7228 | 0.6282 | 0.0946 |

No bucket has fewer than 30 predictions (`low_n` never true here — all
buckets are safely interpretable). Per-bucket calibration error is noisy
and mixed for B vs. A (better in some buckets, worse in others) — this is
exactly what a statistically inconclusive aggregate improvement should
look like, and is consistent with Section T's bootstrap finding. Full
tables for C and D are in the JSON results file; both show the same
"worse in most buckets" pattern their aggregate numbers already indicate.

---

## Q. Probability distribution comparison (true evaluation set)

| Candidate | frac p > 0.70 | frac p < 0.30 | p10 | p50 | p90 |
|---|---|---|---|---|---|
| A baseline | 9.26% | 2.10% | 0.392 | 0.549 | 0.696 |
| B | 9.72% | 2.44% | 0.390 | 0.550 | 0.697 |
| C | 12.73% | 3.93% | 0.371 | 0.551 | 0.719 |
| D | 12.96% | 4.23% | 0.364 | 0.551 | 0.721 |

**Finding (Part 17)**: C and D push meaningfully more predictions into
the extreme tails (>0.70 or <0.30) than baseline — roughly 37-41% more
extreme-probability games — *while simultaneously having worse
calibration* (Section K). That combination — more confident predictions
that are also less well-calibrated — is exactly the failure mode Part 17
asks this report to watch for and refuse to paper over. B's distribution
is nearly indistinguishable from baseline's.

---

## R. Representative real-game examples (true evaluation set, first chronological match per criterion — not cherry-picked)

Baseline vs. Candidate D (`otso=0.50, mov_cap=2`) shown, since D exhibits
both mechanisms at once:

| Type | Game | Result | Pregame ratings (H/A) | p(home) | Baseline Δ | Candidate D Δ | D weight applied |
|---|---|---|---|---|---|---|---|
| Regulation blowout | PIT vs NYR, 2024-10-09 | PIT 0 – NYR 6 (REG, margin 6) | 1490.3 / 1582.0 | 0.4192 | -8.38 | **-15.05** | 1.5850 (mov cap=2, `log(3)/log(2)`) |
| One-goal regulation win | SEA vs STL, 2024-10-08 | SEA 2 – STL 3 (REG, margin 1) | 1453.3 / 1506.0 | 0.4745 | -9.49 | -9.61 | 1.0 (margin-1 is neutral either way) |
| Overtime game | VAN vs CGY, 2024-10-09 | VAN 5 – CGY 6 (OT) | 1531.0 / 1476.3 | 0.6263 | -12.53 | **-6.66** | 0.50 |
| Shootout game | VAN vs PHI, 2024-10-11 | VAN 2 – PHI 3 (SO) | 1518.5 / 1456.7 | 0.6358 | -12.72 | **-6.68** | 0.50 |

The one-goal example's small non-identical deltas despite an identical
`weight=1.0` are expected, not a bug: by this point in the season each
candidate's rating trajectory has already diverged slightly from earlier
OT/SO/MOV-weighted games, so `p_home` itself differs marginally between
the two independent trajectories even on a game where *this* game's
weight is neutral. The blowout and OT/SO rows show the mechanism working
exactly as designed: a capped-margin blowout moves the rating ~1.8x
further, an OT/SO decision moves it exactly half as far.

---

## S. Rating stability findings

Three illustrative teams (highest-final-rating, lowest-final-rating, and
TOR for familiarity, under the baseline trajectory), baseline vs.
Candidate D, over all 1,312 games each played across the 4-season run:

| Team | Candidate | Min | Max | Final | Biggest single-game jump |
|---|---|---|---|---|---|
| CAR (highest final, baseline) | baseline | 1486.0 | 1636.4 | 1604.9 | 51.9 |
| CAR | D | 1487.1 | 1676.3 | 1618.3 | 71.3 |
| VAN (lowest final, baseline) | baseline | 1312.6 | 1596.7 | 1355.3 | 21.2 |
| VAN | D | 1275.6 | 1641.1 | 1299.0 | 30.4 |
| TOR | baseline | 1409.9 | 1608.6 | 1409.9 | 28.0 |
| TOR | D | 1383.8 | 1617.2 | 1383.8 | 34.0 |

No runaway ratings, no instability, no failure to regress at season
boundaries (regression is identical production logic for every
candidate, see Section D's isolation guarantee) — every trajectory stays
within a plausible NHL Elo band (~1275-1680) across all 4 real seasons
for every candidate. Candidate D's single-game jumps run 30-50% larger
than baseline's, which is the expected, bounded consequence of the
capped MOV multiplier (max ~1.585x at `mov_cap=2`) combined with the
0.50x OT/SO reduction — not evidence of instability, just a wider (still
bounded) per-game step size.

---

## T. Paired bootstrap uncertainty (Part 16, n=2,000 resamples, paired over the identical 2,624-game evaluation set, seed=1337)

| Candidate | Metric | Point Δ | 95% CI | Fraction of resamples where candidate improves |
|---|---|---|---|---|
| B | Brier | -0.000315 | **[-0.001074, +0.000489]** | 78.0% |
| B | Log loss | -0.000745 | **[-0.002341, +0.000950]** | 80.7% |
| C | Brier | +0.002540 | [+0.001475, +0.003600] | 0.0% |
| C | Log loss | +0.005614 | [+0.003234, +0.007995] | 0.0% |
| D | Brier | +0.002554 | [+0.001144, +0.003947] | 0.0% |
| D | Log loss | +0.005630 | [+0.002533, +0.008685] | 0.0% |

**B's 95% interval crosses zero on both metrics** — the data cannot
distinguish B's small aggregate improvement from noise at conventional
confidence. **C's and D's intervals are entirely on the "worse" side** —
0% of 2,000 paired resamples showed either candidate improving on either
metric. That is a strong, not a marginal, negative finding for the MOV
candidates.

---

## U. Temporal-integrity tests added this slice

`tests/test_elo_comparison_research.py`, 23 tests, covering (Part 21):

1. Same-day games never affect each other's predictions.
2. A later-dated game's result cannot change an earlier prediction.
3. An earlier result *does* correctly move a later prediction (positive
   control, so the leakage tests above aren't vacuously true).
4. Regulation always gets full OT/SO weight (1.0).
5. Overtime gets the reduced OT/SO weight.
6. Shootout gets the reduced OT/SO weight.
7. MOV weight is neutral at 1-goal margin, and grows monotonically up to
   the cap.
8. MOV weight saturates beyond the cap — a 21-goal margin produces the
   identical multiplier as the at-cap margin (no explosion on blowouts).
9. Combined (Candidate D) weight is exactly the product of the B and C
   components.
10. Season regression matches the exact production fraction/formula, and
    does not fire within the same season.
11. Running the same candidate twice on the same games is byte-identical
    (reproducibility).
12. Every candidate predicts the exact same set of game IDs.
13. **Candidate A (`weight_fn=None`) is proven, step-by-step, to produce
    IDENTICAL ratings to unmodified production `models/elo_model.py::EloModel`**
    run over the same game sequence — the direct "production Elo remains
    unchanged" check.
14. Metrics unit tests (Brier/log-loss hand-computation, season
    breakdown, calibration bucket low-N flagging, paired-bootstrap
    determinism and input validation).
15. Corpus loading (`load_corpus` reads all 5,248 real games across the
    correct 4 seasons) and date-not-game-id ordering.

## V. Files created/modified

**Created (all new, nothing pre-existing touched):**
- `research/elo_comparison.py` — isolated candidate logic (pure, no DB/I-O
  besides corpus loading).
- `research/run_elo_comparison.py` — experiment driver; writes
  `research/elo_comparison_results.json` and re-runs the full test suite.
- `research/elo_comparison_results.json` — every computed number, for
  independent audit.
- `tests/test_elo_comparison_research.py` — 23 new tests (Section U).
- `ELO_REAL_DATA_COMPARISON_REPORT.md` — this report.

**Modified:** none. `models/elo_model.py`, `models/combined_model.py`,
`config.py`, `nhl.db`, `research/real_nhl_results/*`,
`research/moneypuck_review/*`, pricing/decision/threshold code, and every
other production file are byte-identical to their pre-experiment state.

## W. Final full-suite test count

**345 / 345 passing, 0 failed, 0 errors, 0 skipped** (322 pre-existing +
23 new, re-confirmed by `research/run_elo_comparison.py`'s own
end-of-run `python3 -m unittest discover tests` subprocess call, captured
verbatim in `research/elo_comparison_results.json.test_suite_returncode`
== 0).

## X. Recommendation

```
KEEP CURRENT ELO.
```

Candidate B is the only one that improves both Brier and log loss,
consistently across both true-evaluation seasons — but the improvement
is a fraction of one percent and its 95% bootstrap interval crosses zero
on both metrics. Per Part 25's adoption rule (ALL eight conditions must
hold, including "improvement large enough to justify the added
complexity"), B does not clear the bar: statistically inconclusive is not
the same as "no improvement," but it is not adoption-worthy either.
Candidates C and D are not close calls — they are consistently worse
across all 4 seasons, with 0% of bootstrap resamples showing improvement
on either metric, and they simultaneously push more predictions into
poorly-calibrated extreme-probability territory (Section Q). No
candidate should replace production Elo this slice.

## Y. Recommended next development slice

Per Part 27 and consistent with `MONEYPUCK_DATA_CONTRACT_REVIEW.md`'s own
ranking (which already flagged Result-Quality/MOV Elo as the top
candidate specifically *because* it was cheap to test first): with that
now tested and not adopted, the next-ranked, still-untested candidate is

```
MONEYPUCK TEAM xG / SHOT-QUALITY INGESTION FOUNDATION
```

— i.e. building the actual MoneyPuck ingestion pipeline (starting with
`all_teams.csv`, per that report's Section AB) that would be needed to
test the team xG/xGA candidate the same rigorous, real-data,
walk-forward way this Elo candidate was just tested. Not another
increasingly-complex Elo variant chasing a win that this real-data test
did not find.

---

## Final questions

```
WERE ONLY REAL NHL GAMES USED FOR MODEL EVALUATION?
YES

WAS THE SYNTHETIC nhl.db USED FOR TUNING?
NO

WAS STRICT PRIOR-GAME-DATE ENFORCED?
YES

WERE SAME-DAY RESULTS EXCLUDED?
YES

WAS WALK-FORWARD EVALUATION USED?
YES

DID ANY CANDIDATE IMPROVE OUT-OF-SAMPLE BRIER SCORE?
YES -- Candidate B only (baseline 0.245456 -> 0.245141, -0.128% relative).
Candidates C and D were worse.

DID ANY CANDIDATE IMPROVE OUT-OF-SAMPLE LOG LOSS?
YES -- Candidate B only (baseline 0.684295 -> 0.683550, -0.109% relative).
Candidates C and D were worse.

WAS THE IMPROVEMENT CONSISTENT ACROSS MULTIPLE SEASONS?
YES for B (improved in both 2024-25 and 2025-26, though narrowly); NO
improvement to be consistent about for C/D -- they were worse in both.

IS THE IMPROVEMENT LARGE ENOUGH TO JUSTIFY THE EXTRA COMPLEXITY?
NO -- B's 95% paired-bootstrap interval crosses zero on both Brier and
log loss; the point improvement is a fraction of one percent.

SHOULD PRODUCTION ELO BE REPLACED?
NO

CURRENT FULL TEST RESULT?
345 / 345

WHAT SHOULD THE NEXT SINGLE DEVELOPMENT SLICE BE?
MoneyPuck team xG / shot-quality ingestion foundation (starting with
all_teams.csv, scoped to the same 2022-23 -> 2025-26 window as the real
NHL corpus) -- per MONEYPUCK_DATA_CONTRACT_REVIEW.md Section AB/AE, now
that the cheaper, already-designed Elo candidate has been properly tested
and did not clear the adoption bar.
```

---

## STOP AFTER THIS REPORT

Per instruction: production Elo was not replaced. MoneyPuck was not
ingested. No MoneyPuck daily sync was built. No xG was added. No goalie
workload was added. Player modeling was not changed. The Odds API was not
integrated. No UI was built. This report is returned for independent
review; no further action was taken this turn.
