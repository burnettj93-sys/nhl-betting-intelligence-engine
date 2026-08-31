# Goalie Quality x Starter-Probability Integration Experiment

Combines the Stage 1 pregame starting-goalie projection model
(`research/goalie_intelligence/`, see `GOALIE_INTELLIGENCE_FOUNDATION_REPORT.md`)
with two goalie-quality candidate metrics, through a genuine
probability-weighted scenario mixture, and tests whether the result
improves real, out-of-sample NHL moneyline win-probability estimation.
**Recommendation: KEEP CURRENT MODEL.** No production file was modified.

---

## A. Audit of the current production goalie-quality model

Read directly from `models/goalie_model.py` and `config.py` (both untouched
this slice):

- `GoalieRatingModel` tracks cumulative `saves` / `shots_against` per
  `player_id` via `update(player_id, saves, shots_against)` — running
  totals, never reset.
- `save_pct(player_id)` = `saves / shots_against` (undefined/guarded when
  `shots_against == 0`).
- `sample_size(player_id)` = cumulative `shots_against`.
- League-average seed: `league_save_pct = 0.905` (hardcoded constant on
  the class).
- Shrinkage: `shrink = shots_against / (shots_against + config.GOALIE_SHRINKAGE_STARTS * 25)`,
  with `config.GOALIE_SHRINKAGE_STARTS = 15` (so full shrinkage denominator
  add-on is `15 * 25 = 375` "neutral" shots).
- Elo conversion: `elo_delta = (save_pct - league_save_pct) * shrink * config.SAVE_PCT_TO_ELO`,
  with `config.SAVE_PCT_TO_ELO = 2500.0`.
- `rating_adjustment_elo(player_id, confirmed)` returns `(elo_delta, uncertainty_multiplier)`.
  If `player_id is None`, it returns `(0.0, config.UNCONFIRMED_GOALIE_UNCERTAINTY_WIDENING)`
  — `config.UNCONFIRMED_GOALIE_UNCERTAINTY_WIDENING = 1.4`.
- Missing/unconfirmed goalie status handling (`features/point_in_time.py::goalie_status`):
  reads the append-only `goalie_status_events` table, latest
  `observed_at_utc <= prediction_time_utc` wins; returns
  `GoalieStatus(None, "UNKNOWN", None)` if nothing found. **No WAIT is
  triggered by goalie quality itself** — WAIT is a separate policy
  decision inside `pricing/engine.py` (not read or touched this slice);
  the goalie-quality/Elo path always produces a real number (0.0 delta,
  widened uncertainty when unconfirmed).
- No shot-danger-tier or rebound-control logic exists in production —
  save% is the only quality signal, confirmed by direct source read.
- No synthetic-era TODOs were found referencing this formula.

This is the exact, unmodified formula Candidate A reuses below (Part 18
freeze) — cross-checked bit-for-bit against the live class in
`tests/test_goalie_quality_integration.py::TestSavePctShrinkageCorrectness`.

## B. MoneyPuck goalie schema audit

Reused the same raw archived files from the prior slice
(`research/goalie_intelligence/raw/{2022,2023,2024,2025}.csv`,
`peter-tanner.com`-sourced, `provenance.json` unchanged, no new
download this slice) — 39 columns, `situation == "all"`, `position == "G"`
rows. Fields actually used for goalie quality (Part 13):
`playerId`, `name`, `gameId`, `season`, `playerTeam`, `opposingTeam`,
`icetime`, `ongoal` (shots that reached the goalie), `goals`, `xGoals`
(expected goals against, from this goalie's own faced shots). No
danger-tier or rebound columns are used — Part 14 calls for only a few
transparent formulas, and neither candidate needs them.

## C. Candidate quality metrics tested

| Candidate | Description | New coefficient fit this slice? |
|---|---|---|
| A | Existing production save%-shrinkage, formula unchanged | No — reuses `config.SAVE_PCT_TO_ELO`/`config.GOALIE_SHRINKAGE_STARTS` verbatim |
| B | MoneyPuck shot-quality-style metric (`xGoalsAgainst - goalsAgainst`, pooled, per-60, shrunk) — deliberately **not** called "GSAx" since this exact formula's match to any published GSAx definition was never verified | Yes — a single logit-space scale coefficient (β), fit on `TUNING_SEASON` only |
| D | Blend: A's adjustment + B's adjustment, summed in logit space | No new fit — reuses both A and B's own scales |

Baseline is the Elo-only, production-equivalent model
(`research.elo_comparison.run_walkforward(games, weight_fn=None)`),
frozen and unweighted, per Part 18. Same precedent as every prior
experiment in this project (`tests/test_elo_comparison_research.py::TestProductionEloEquivalence`).

## D. Exact formulas

**Candidate A** (`research/goalie_intelligence/quality.py::shrunk_save_pct_production`):
```
cum_shots = sum(shots_against over goalie's full prior history)
cum_saves = sum(saves over same history)
shrink    = cum_shots / (cum_shots + GOALIE_SHRINKAGE_STARTS * 25)
save_pct  = cum_saves / cum_shots
elo_delta = (save_pct - LEAGUE_AVG_SAVE_PCT) * shrink * SAVE_PCT_TO_ELO
```
`LEAGUE_AVG_SAVE_PCT` is read directly from a real `GoalieRatingModel()`
instance (`0.905`), never hand-typed as a second copy.

**Candidate B** (`research/goalie_intelligence/quality.py::rolling_gsax_per60`):
```
recent      = most recent `window` appearances (or all history if window=None)
raw_per60   = (sum(xg_against) - sum(goals_against)) * 3600 / sum(icetime_seconds)
shrink      = sum(shots_against) / (sum(shots_against) + GOALIE_SHRINKAGE_STARTS * 25)
raw_shrunk  = raw_per60 * shrink
adj_logit   = beta * (raw_shrunk - tuning_mean) / tuning_stdev     # standardized, then scaled
```

## E. Shrinkage / sample-size policy

Both candidates shrink toward a neutral baseline (league-average save%
for A, zero differential for B) using the **same shots-faced-based
shape** (`shots / (shots + K*25)`), reusing `config.GOALIE_SHRINKAGE_STARTS`
for both rather than inventing a second, unrelated constant — this was
a deliberate choice for internal consistency, not a claim that the two
metrics' true optimal shrinkage rates are identical. Per Part 5, this
is an **effective-sample-size** shrinkage (shots faced), not a raw
game-count shrinkage.

## F. Rolling window policy

Grid tested for Candidate B (Part 15): **5, 15, cumulative (all
history)**. Not every integer window — a small, interpretable set, same
spirit as the prior MoneyPuck feature experiments' `WINDOW_GRID`.

## G. Season-boundary policy (goalie identity persistence)

Goalie quality is **deliberately NOT season-scoped** (Part 16), unlike
every team-level MoneyPuck feature module in this project. A goalie's
cumulative appearance history (2022-23 onward) carries across season
boundaries with no reset — `tests/test_goalie_quality_integration.py::TestSeasonBoundaryNoReset`
confirms this directly. The shrinkage formula itself *is* the
"regressed prior, current evidence gradually takes over" mechanism: a
goalie with a small cumulative sample is pulled hard toward neutral; a
veteran with thousands of career shots faced is barely shrunk. No
separate two-stage prior/current blend was needed. Quality also follows
**goalie identity, not team** — `goalie_history_as_of()` is keyed by
`goalie_id` first, so a mid-season trade does not reset a goalie's
quality estimate (`TestGoalieHistoryAsOfPIT::test_goalie_identity_survives_a_team_change_not_reset`).

## H. Projected-starter integration formula

Every goalie-quality adjustment is converted into **natural-logit
units** before being combined with the starter-probability distribution:

- Candidate A: `adj_logit = elo_delta * (ln(10) / 400)` — this is
  production's *own* Elo-to-probability conversion
  (`p = 1/(1+10^(-diff/400))`, from `research/elo_comparison.py`'s
  `win_probability()`, which mirrors `config.py`'s convention), applied
  as a **unit conversion**, not a new fitted coefficient. Part 18's
  freeze is respected — nothing about Candidate A's scale was tuned.
- Candidate B: `adj_logit = beta * standardized(raw_gsax_style)` — beta
  IS a new fitted parameter (Section J), since this metric has no
  pre-existing production scale to reuse.

## I. Scenario-combination (mixture) formula

For one real game, with home candidate goalies `{h}` (`P(h)` from the
Stage 1 model) and away candidates `{a}` (`P(a)`):

```
p_home_win(h, a) = sigmoid( logit(p_baseline) + adj(h) - adj(a) )
p_candidate       = Sum_h Sum_a  P(h) * P(a) * p_home_win(h, a)
```

**Independence assumption, stated explicitly (Part 7):** `P(h)` and
`P(a)` are treated as independent — there is no realistic mechanism
linking two different teams' independent goalie-rotation decisions, so
this is a reasonable, named simplification rather than a silent one.

This is a weighted **average of probabilities**, not a sigmoid of a
weighted average of adjustments — those two are mathematically
different because `sigmoid` is nonlinear, and using the wrong one was
explicitly flagged as an error to avoid in the prompt for this slice.
`tests/test_goalie_quality_integration.py::TestScenarioWeightedProbability::test_mixture_differs_from_sigmoid_of_averaged_adjustments`
constructs a case where the two approaches disagree by >0.05 in
probability, to make the distinction concrete and regression-tested
rather than just asserted in prose.

Two additional evaluated modes, both built from the same machinery
(Part 9):
- **Top-1**: collapse to the single most-likely starter per team,
  treat as certain (`P=1`), price only that one scenario.
- **Oracle** (diagnostic only, Section Y): use the quality of the
  *actual* historical starter, as if perfectly known in advance.

## J. GSAx-style scale (β) tuning methodology + window selection

β and the standardization (mean/stdev) are fit on `TUNING_SEASON`
(20232024) **only**, via the exact same plain-gradient-descent
`fit_logistic_weights` used throughout this project
(`research/xg_model_comparison.py`), on the **top-1** basis (the raw
quality differential between each side's single most-likely projected
starter) — not the full mixture. This reuses machinery already needed
for the required top-1 comparison mode (Section X) rather than writing
a second, more complex multi-scenario gradient descent; the fitted β
is then applied uniformly to every candidate goalie in the full
mixture at evaluation time. Window selection uses the lowest resulting
**tuning-season** top-1 Brier score among `{5, 15, None}` — the true
eval seasons are never touched during this step
(`tests/test_goalie_quality_integration.py::TestTuningEvalSeparation`).

**Selected window: 5.** See Section AA for why this selection should
be treated skeptically rather than as the headline finding.

| Window | β (fitted) | Tuning top-1 Brier | Raw unconditional corr. (top-1 diff vs. outcome) |
|---|---|---|---|
| 5 (selected) | -0.1647 | 0.23796 | **-0.025** |
| 15 | -0.1387 | 0.23856 | +0.038 |
| None (cumulative) | -0.0233 | 0.23951 | **+0.129** |

## K. True walk-forward evaluation methodology

Same split as every prior experiment: `WARMUP_SEASON = 20222023`
(unused directly here — only feeds Elo state warm-up via the frozen
baseline itself), `TUNING_SEASON = 20232024` (β/window selection only),
`EVAL_SEASONS = [20242025, 20252026]` (true untouched holdout). No true
eval game ever contributed to fitting anything in this experiment.

## L. Common evaluation set / coverage

| | Tuning season | Eval seasons |
|---|---|---|
| Total real games (baseline-eligible) | 1,312 | 2,624 |
| Games with a valid starter-probability distribution on **both** sides | 1,238 | **2,474** |
| Coverage | 94.4% | **94.28%** |

Exclusions (pooled across tuning+eval): 54 games where one side had no
starter-corpus row at all, 170 where a side's starter-model example
was rejected (insufficient history or the actual starter fell outside
the eligible-candidate pool) — identical exclusion logic to the Stage 1
slice's own `build_example()`, reused unchanged. Every game in the
2,474-game eval set has both actual starters with at least one prior
quality appearance (100% "mature" by that minimal bar); no game was
additionally excluded for immature quality data, since the shrinkage
formulas are specifically designed to handle small samples gracefully
rather than requiring a hard maturity gate (consistent with production's
own behavior — see Section A).

## M. Starter-model + quality feature coverage detail

The Stage 1 starter model's fitted weights were **reused unchanged**
(not refit):

| Feature | Fitted weight |
|---|---|
| `back_to_back_after_playing_previous_night` | -2.897 |
| `recent_start_share_10` | 2.136 |
| `season_start_share` | 0.887 |
| `started_previous_game` | -0.245 |
| `consecutive_start_count` | -0.004 |

## N. Headline Brier scores (eval seasons, n=2,474)

| Candidate / mode | Brier |
|---|---|
| Baseline (no goalie adjustment) | 0.246157 |
| A, mixture | 0.247452 |
| A, top-1 | 0.247618 |
| A, oracle | 0.246533 |
| B, mixture | 0.244761 |
| B, top-1 | 0.245455 |
| B, oracle | 0.245591 |
| D (A+B blend), mixture | 0.245757 |

## O. Brier deltas vs. baseline

| Candidate | Absolute delta | Relative delta |
|---|---|---|
| A, mixture | **+0.001295 (worse)** | +0.53% |
| B, mixture | -0.001396 (better) | -0.57% |
| D, mixture | -0.000400 (better) | -0.16% |
| A, oracle | +0.000376 (worse) | +0.15% |
| B, oracle | -0.000566 (better) | -0.23% |

## P. Headline log loss

| Candidate / mode | Log loss |
|---|---|
| Baseline | 0.685843 |
| A, mixture | 0.688744 |
| B, mixture | 0.682777 |
| D, mixture | 0.684955 |

## Q. Log-loss deltas vs. baseline

| Candidate | Absolute delta | Relative delta |
|---|---|---|
| A, mixture | +0.002901 (worse) | +0.42% |
| B, mixture | -0.003066 (better) | -0.45% |
| D, mixture | -0.000888 (better) | -0.13% |

All effect sizes here are well under 1% relative — small by this
project's own standard for what counts as meaningful (see prior
experiments' conclusions).

## R. Calibration comparison (baseline vs. best mixture candidate, B)

| Bucket | n | Baseline pred | Baseline actual | Baseline err | B pred | B actual | B err |
|---|---|---|---|---|---|---|---|
| [0.30,0.35) | 68 | 0.329 | 0.441 | 0.112 | 0.326 | 0.386 | 0.060 |
| [0.35,0.40) | 150 | 0.377 | 0.440 | 0.063 | 0.379 | 0.472 | 0.093 |
| [0.40,0.45) | 220 | 0.427 | 0.441 | 0.014 | 0.427 | 0.422 | 0.005 |
| [0.45,0.50) | 337 | 0.476 | 0.540 | 0.064 | 0.477 | 0.508 | 0.032 |
| [0.50,0.55) | 415 | 0.525 | 0.554 | 0.029 | 0.526 | 0.555 | 0.029 |
| [0.55,0.60) | 422 | 0.574 | 0.509 | 0.065 | 0.575 | 0.522 | 0.053 |
| [0.60,0.65) | 337 | 0.624 | 0.576 | 0.048 | 0.623 | 0.583 | 0.040 |
| [0.65,0.70) | 249 | 0.674 | 0.659 | 0.015 | 0.673 | 0.633 | 0.040 |
| [0.70,0.75) | 140 | 0.723 | 0.607 | 0.116 | 0.720 | 0.674 | 0.047 |

Mixed picture — Candidate B improves calibration error in some buckets
(0.30-0.35, 0.45-0.50, 0.70-0.75) and worsens it in others (0.35-0.40,
0.65-0.70). No consistent, one-directional calibration improvement.
Probability-extremity check: `frac_above_0.70` / `frac_below_0.30` for
mixture B (8.6% / 1.5%) are actually slightly *less* extreme than
baseline (9.1% / 2.0%) — Candidate A pushes the opposite way (11.8% /
3.0%, more extreme). No candidate shows runaway overconfidence.

## S. Paired bootstrap (2,000 resamples, same evaluated games)

| Candidate | Metric | Point delta | 95% CI | % resamples favoring candidate |
|---|---|---|---|---|
| A, mixture | Brier | +0.001295 | [+0.00031, +0.00222] | **0.5%** |
| A, mixture | Log loss | +0.002901 | [+0.00077, +0.00491] | 0.3% |
| B, mixture | Brier | -0.001396 | [-0.00252, -0.00021] | **99.1%** |
| B, mixture | Log loss | -0.003066 | [-0.00543, -0.00055] | 99.3% |
| D, mixture | Brier | -0.000400 | [-0.00169, +0.00082] | 74.5% |

Candidate A's harm is statistically credible (bootstrap strongly
against it). Candidate B's improvement is *also* statistically credible
by this test alone — **but see Section Z**, where the oracle-gap check
undermines trusting this as real goalie-quality signal. Candidate D's
blend is not credible (74.5% is well short of a normal 95%+ bar).

## T. Season-by-season breakdown

| Season | n | Baseline Brier | A mixture | B mixture |
|---|---|---|---|---|
| 2024-25 | 1,229 | 0.24079 | 0.24152 | 0.23853 |
| 2025-26 | 1,245 | 0.25145 | 0.25331 | 0.25091 |

Direction is consistent both seasons (A worse, B better) but magnitude
is tiny in both.

## U. Confidence-bucket (HIGH/MEDIUM/LOW starter-inference confidence) breakdown

| Confidence | n | Baseline | A mixture | B mixture |
|---|---|---|---|---|
| HIGH | 472 | 0.24451 | 0.24579 | 0.24309 |
| MEDIUM | 1,413 | 0.24744 | 0.24906 | 0.24620 |
| LOW | 589 | 0.24439 | 0.24493 | 0.24264 |

No clean pattern of "goalie quality helps more at HIGH confidence" —
Candidate B's improvement is present in all three buckets at similarly
small magnitude, which is itself mildly inconsistent with a real,
confidence-dependent starter-quality signal (a genuine effect would be
expected to concentrate where starter certainty is high).

## V. Back-to-back vs. non-B2B breakdown

| | n | Baseline | A mixture | B mixture |
|---|---|---|---|---|
| B2B | 650 | 0.24423 | 0.24441 | 0.24408 |
| non-B2B | 1,824 | 0.24684 | 0.24854 | 0.24500 |

Candidate A's harm and Candidate B's (small) improvement are both
*larger* in the non-B2B subset than the B2B subset — the opposite of
what "goalie quality matters more when the backup unexpectedly starts
on a B2B" would predict.

## W. Tandem/workhorse hierarchy breakdown

| | n | Baseline | A mixture | B mixture |
|---|---|---|---|---|
| Clear hierarchy (workhorse/uncertain teams) | 364 | 0.24024 | 0.24054 | 0.23772 |
| Tandem-team games | 2,110 | 0.24718 | 0.24864 | 0.24598 |

No meaningfully larger effect on tandem-team games specifically, though
tandem games dominate the eval set by volume (85%) simply because
strict workhorse (top-share >=0.65 for a full season) is relatively
uncommon in this corpus.

## X. Top-1 vs. full-mixture comparison

| Candidate | Top-1 Brier | Mixture Brier | Mixture wins? |
|---|---|---|---|
| A | 0.247618 | 0.247452 | Yes (barely) |
| B | 0.245455 | 0.244761 | Yes |

The full probability-weighted mixture beat naive top-1 for **both**
candidates, on real held-out data — this part of Part 9's comparison
holds up regardless of whether the underlying quality signal itself is
trustworthy: propagating starter uncertainty is better than pretending
the most likely starter is certain.

## Y. Oracle diagnostic (methodology + isolation)

The oracle mode uses the *actual* historical starter's quality value,
never the actual starter's identity as a *pregame* input — it is
computed by exactly the same PIT-safe, prior-game-date-only quality
formula, just applied to a name a legitimate pregame model cannot know.
Structurally isolated: `gqi.scenario_weighted_probability` and
`gqi.top1_probability` have no "actual starter" parameter at all
(`TestOracleIsolation::test_functions_have_no_actual_starter_parameter_by_signature`),
so there is no code path by which the headline mixture result could
have been contaminated by this diagnostic.

## Z. Oracle gap analysis — the key caveat on Candidate B

| Candidate | Eval Brier, mixture | Eval Brier, oracle | Gap (oracle - mixture) |
|---|---|---|---|
| A | 0.247452 | 0.246533 | **-0.000919** (oracle better, as expected) |
| B | 0.244761 | 0.245591 | **+0.000830 (oracle WORSE than mixture)** |

For Candidate A, perfect starter knowledge modestly *improves* the
result relative to the projected mixture — the expected, sane
direction for a real quality signal (more certainty about who is
actually playing should not make a genuinely informative quality metric
perform worse).

**For Candidate B, it is backwards: knowing the actual starter with
certainty performs *worse* than the probability-weighted projection.**
Combined with Section J's finding that the selected window (5) has
essentially **zero unconditional correlation** with the outcome
(-0.025) while the theoretically soundest, most stable window
(cumulative, +0.129 raw correlation) shows the **smallest** headline
improvement (Section AA), this is strong evidence that Candidate B's
apparent Brier/bootstrap win is a curve-fitting artifact of the
short-window tuning-selection process rather than genuine goalie-quality
predictive value. A real signal should not evaporate — or reverse —
under perfect information.

## AA. Window-selection robustness diagnostic

| Window | Mixture Brier (eval) | Top-1 Brier (eval) | Oracle Brier (eval) | Oracle gap |
|---|---|---|---|---|
| 5 (tuning-selected) | 0.244761 | 0.245455 | 0.245591 | +0.000830 |
| 15 | 0.244888 | 0.245683 | 0.245528 | +0.000640 |
| None (cumulative) | 0.246068 | 0.246106 | 0.246218 | +0.000150 |

Every window shows a *positive* oracle gap (oracle worse than mixture),
but the effect shrinks monotonically as the window gets longer and more
stable — the cumulative window's "improvement" over baseline
(-0.000089) is essentially zero and well within noise, exactly where
the raw correlation evidence (Section J) says the real signal (if any)
actually lives. The shorter, noisier windows show larger *apparent*
improvement precisely because they have more freedom to fit tuning-set
noise. This pattern, taken together with Section Z, is the primary
reason this report does not recommend adopting Candidate B despite its
favorable bootstrap number in isolation.

## AB. Representative real-game examples

All drawn from the true eval set (2024-25/2025-26), selected
programmatically by simple criteria (not hand-picked for a favorable
narrative) — `research/goalie_quality_integration_results.json::representative_examples`
has full detail for all eight categories.

- **Clear workhorse pairing** (DAL @ ... vs NYI, 2024-10-12): both teams
  workhorse-tier, HIGH confidence, top-1 correct both sides. Baseline
  Brier 0.1188; B mixture improves to 0.0983; A mixture is roughly flat
  (0.1222).
- **Tandem, low confidence / back-to-back** (BUF @ NJD, 2024-10-05): a
  4-goalie-history NJD side and a true 50/50 BUF tandem, NJD on a
  back-to-back. Projected top-1 was wrong on this particular game;
  mixture (B: 0.5517) and top-1 (A: 0.5483) both moved the same
  direction from baseline (0.5284), B mixture Brier (0.201) beat
  baseline (0.222).
- **Projected starter wrong** (NYR @ PIT, 2024-10-09): PIT's projected
  top-1 (backup) did not match the actual starter; baseline Brier
  0.1757 vs. A mixture 0.1596 (slightly better here) vs. B mixture
  0.2035 (worse here) — a case illustrating that any single game can
  cut either way regardless of the aggregate trend.
- **Projected starter right, HIGH confidence** (DAL vs NYI, same game
  as workhorse example above) — both sides' top-1 picks were correct.
- **Distribution outperforms top-1** (SEA @ MIN, 2024-10-12): baseline
  0.3892, A top-1 0.4552, A mixture 0.4223 — the mixture meaningfully
  beat naive top-1 by accounting for MIN's genuine 3-goalie uncertainty
  (53%/28%/18%).
- **Adjustment helps** (BUF @ CBJ, 2024-10-17, B2B, tandem both sides):
  baseline 0.2812, B mixture 0.2180 — among the largest single-game
  improvements found.
- **Adjustment hurts** (NYI @ COL, 2024-10-14): baseline 0.5875, B
  mixture 0.6298 — worse than baseline on this game.

These are presented as a mix of helps/hurts/neutral, not cherry-picked
successes, per Part 26's explicit instruction.

## AC. Files created / modified this slice

**Created (research, isolated):**
- `research/goalie_intelligence/build_quality_corpus.py`
- `research/goalie_intelligence/goalie_appearances.jsonl` (11,112 rows, 151 goalies)
- `research/goalie_intelligence/quality.py`
- `research/goalie_quality_integration.py`
- `research/run_goalie_quality_comparison.py`
- `research/goalie_quality_integration_results.json`

**Created (dashboard, additive only):**
- `dashboard/goalie_quality_view.py`

**Modified (dashboard, additive extension only):**
- `dashboard/pages/6_Goalie_Intelligence.py` (new panel appended; existing
  Stage 1 content untouched)

**Created (tests):**
- `tests/test_goalie_quality_integration.py` (42 tests)

**Modified (docs):**
- `README.md` (one paragraph added to the dashboard section)

**Untouched, verified:** `models/`, `config.py`, `db.py`, `nhl.db`,
`pricing/`, `schema.sql`, and every pre-existing research/dashboard/test
file. `git status` shows no `M` (modified) entries for any of these; all
new files are untracked (`??`) only.

## AD. Dashboard changes

`dashboard/pages/6_Goalie_Intelligence.py` gained a new section below
the existing Stage 1 panel: **"Goalie Quality x Starter Probability
Integration (Experiment)"**, clearly bannered `STATUS: RESEARCH — NOT
PRODUCTION`. It shows: (1) headline eval Brier for baseline vs. both
mixture candidates, with the KEEP CURRENT MODEL recommendation stated
directly; (2) a real-game picker (team/season/game) that displays, for
both the home and away projected candidate pools, each goalie's
P(starts), save%-quality delta (Elo points + sample size), and GSAx-
style logit adjustment (+ sample size); (3) the resulting
scenario-weighted P(home wins) for baseline / +save%-quality mixture /
+GSAx-style-quality mixture / +save%-quality top-1-only, side by side.
Verified live in-browser (Streamlit on port 8766) against a real game
(2026-04-16, WPG vs SJS) — all values render correctly, including a
3-goalie SJS tandem on a back-to-back. The existing production win-
probability display elsewhere in the dashboard was not touched.

## AE. Full test suite result

```
Ran 569 tests in 10.662s
OK
```
**569 total / 569 passed / 0 failed / 0 errors / 0 skipped.** This is
527 (prior baseline, confirmed unchanged) + 42 new tests in
`tests/test_goalie_quality_integration.py`. No existing test was
weakened, skipped, or removed.

## AF. Production-model-untouched verification

`git status --short` confirms zero `M` (modified) entries for any file
under `models/`, `config.py`, `db.py`, `pricing/`, or `schema.sql`.
`nhl.db`'s on-disk mtime (`Aug 26 16:15`) predates this entire slice's
work and was not touched. `tests/test_goalie_quality_integration.py::TestNoExternalSourceOrForbiddenImports`
AST-scans every new file for forbidden imports (`pricing.engine`,
`pricing.decision`, `models.combined_model`, `urllib.request`,
`requests`, `http.client`) and for any `Call` node referencing
`nhl.db` — none found. `tests/test_structural_reads.py` and
`tests/test_training_path_structural_audit.py` (part of the full 569)
independently confirm no raw SQL and no game-id/list-position
chronology proxies were introduced.

## AG. Recommendation and rationale

**KEEP CURRENT MODEL.** Neither candidate clears the adoption bar from
Part 35:

- **Candidate A** (existing production save%-quality, applied to
  probability via the projected-starter mixture) makes the model
  measurably *worse* (Brier +0.53% relative, log loss +0.42% relative,
  bootstrap only 0.5% of resamples favor it, oracle *confirms* the
  small negative-to-neutral signal is real but genuinely tiny). This
  itself is a useful, real finding: the goalie-quality signal that
  *does* help production's synthetic-era-adjacent Elo/rating pipeline
  does not obviously transfer to a probability-mixture integration on
  this real corpus.
- **Candidate B** (new GSAx-style metric) shows a statistically
  credible-*looking* improvement (Brier -0.57% relative, 99.1% of
  bootstrap resamples favor it) — but **fails the oracle-consistency
  sanity check** (Section Z): perfect knowledge of the actual starter
  performs *worse* than the probability-weighted projection, and the
  tuning-selected window has essentially zero raw unconditional
  correlation with the outcome while the theoretically soundest window
  shows almost no effect at all (Section AA). This pattern is the
  signature of overfitting a short-window metric's noise during
  tuning-season selection, not of real predictive signal. Presenting
  Candidate B's headline Brier number alone, without this context,
  would overstate the case for adoption.
- **Candidate D** (blend) inherits both problems and clears no
  threshold (74.5% bootstrap credibility, well short of a normal bar).
- Effect sizes throughout are small in absolute terms (<0.6% relative
  on every metric, every candidate) — consistent with this project's
  established bar for "not yet meaningful," matching the outcome of
  every completed feature experiment prior to the goalie-starter model
  itself.

The **architecture is sound and reusable**: the scenario-weighted
mixture formula, the PIT-safe goalie-quality feature layer, and the
oracle-diagnostic isolation all worked correctly end-to-end on real
data, and are ready to reprice instantly once a live CONFIRMED-starter
source exists (Part 28) — the same quality model, the same mixture
formula, just with `P(h)`/`P(a)` collapsed to 1.0/0.0. What is missing
is not the integration mechanism but a goalie-quality *metric* that
survives its own oracle check.

## AH. Next single development slice

Recommend a **focused goalie-quality metric iteration** before
attempting integration again, rather than moving on to player
props/SOG modeling (Part 32 explicitly gates that on goalie
intelligence "becoming reliable first," which this slice's honest
result says it is not yet): investigate whether a **rebound-control or
high-danger-shot-quality-tier save% split** (rather than an aggregate
per-60 differential) produces a metric that (a) has real unconditional
correlation with outcomes at multiple window lengths, not just one,
and (b) passes its own oracle-consistency check the way Candidate A
did (even though A's real effect was tiny). If that also fails, the
honest conclusion may be that save-percentage-family goalie metrics
have limited transferable signal for *win-probability* integration on
this real corpus at this sample size, and the project should move on
to player-level shot/scoring-chance features instead.

---

## Final questions

- Was the actual historical starter ever used as a pregame feature? **NO.**
- Were projected (not actual) starter probabilities used for the headline result? **YES.**
- Was goalie quality itself PIT-safe (no same-day/future/target-game stats)? **YES.**
- Did the integration improve Brier score / log loss? **Candidate A: NO (both worse). Candidate B: numerically yes (-0.57% / -0.45% relative) but this improvement is not trusted — see the oracle-gap and window-robustness findings (Sections Z/AA).**
- Did any improvement persist across both eval seasons? **Direction was consistent (Candidate B improved in both 2024-25 and 2025-26), but magnitude was tiny in both and the improvement's validity itself is in question.**
- Was the effect size large enough to matter? **NO — every metric, every candidate, was under 0.6% relative change.**
- Did the probability mixture outperform the naive top-1-only approach? **YES, for both candidates** (0.247452 vs 0.247618 for A; 0.244761 vs 0.245455 for B) — this part of the hypothesis held up regardless of the quality-signal concerns.
- How much better was oracle (perfect starter) knowledge? **Candidate A: oracle Brier was 0.000919 LOWER (better) than the mixture — small but in the expected direction. Candidate B: oracle Brier was 0.000830 HIGHER (worse) than the mixture — the wrong direction, the key red flag of this experiment.**
- Does this result support building future live CONFIRMED-starter integration? **Partially.** The architecture (scenario mixture -> instant 1.0/0.0 repricing once a starter is confirmed) is validated and ready to reuse. But the goalie-quality *metrics* tested this slice don't yet provide strong enough, self-consistent evidence to prioritize wiring up a live confirmation source next — that investment is better justified once a quality metric passes its own oracle check.
- Should the production model change now? **NO.**
- Current full test suite result: **569 total / 569 passed / 0 failed / 0 errors / 0 skipped.**
- Recommended next single development slice: **A focused goalie-quality metric iteration (rebound-control / high-danger-shot-quality-tier save% split) that must pass its own oracle-consistency check before any future integration attempt** (see Section AH).

---

### STOP AFTER THIS REPORT

Per the governing instructions for this slice, nothing further was done
this turn:
- Production win probabilities were **not** modified.
- No restricted external goalie source (Daily Faceoff, RotoWire, Goalie
  Post/Frozen Tools, NHL.com) was integrated.
- The Odds API was **not** touched.
- Player-prop modeling was **not** started.
- The parlay optimizer was **not** built.
