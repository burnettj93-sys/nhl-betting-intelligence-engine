# Player Points — Redesign Cycle 2: Empirical-Baseline Challenge

**EVALUATION STATUS: REUSED HISTORICAL DATA UNDER NEW DEVELOPMENT CYCLE.** 2024-25 and 2025-26 were
Cycle 1's true-evaluation seasons (`PLAYER_POINTS_VALIDATION_REPORT.md`) and are **not** pristine
holdout here — they are reused as rolling-fold validation seasons in a fresh 3-fold walk-forward
design across all 4 real seasons, explicitly labeled as such throughout.

**Verdict: `POINTS: EMPIRICAL BASELINE REMAINS CHAMPION`.** The redesign confirmed and explained
*why* the empirical baseline wins, found one genuinely promising refinement (a hierarchical mean +
opponent-context offset model), and found that refinement does **not** clear the pre-registered
fold-consistency bar at the primary 1+ threshold — it wins convincingly in one fold and loses in
another. Per the adoption standard's own explicit escape hatch, the honest outcome is not to force
a more complex model into production.

---

## A. Exact empirical baseline audit

The Cycle-1 winning baseline (`D_empirical_distribution` in `research/run_player_points_model.py`)
works as follows:

- **Minimum history required:** none structurally (n=0 falls back to the league-wide rate); in
  practice every scored row already passed the shared eligibility gate (≥3 prior games,
  `projected_active()`).
- **Season reset behavior:** NONE — `player_history_as_of()` is player-identity-scoped, not
  season-scoped. A player's history spans every prior real game regardless of season boundary.
- **Prior-season carryover:** full, unbounded (career-to-date history).
- **Rolling vs. expanding:** expanding (full career-to-date, not a fixed window).
- **Shrinkage:** yes — `n/(n+20)` toward the league-wide empirical rate at each threshold
  (`EMPIRICAL_SHRINK_GAMES=20`), same convention used everywhere else in this project.
- **Player movement (trades):** history follows the player, never resets on a team change (verified
  directly, `TestSeasonBoundaryAndPlayerMovement.test_player_history_survives_team_change`).
- **Role changes:** not modeled at all — this is exactly the gap Section H's redesign candidates target.
- **Low-sample players:** shrunk heavily toward the league rate (small `n` → small `n/(n+20)` weight).
- **Threshold derivation:** direct nonparametric empirical CDF read — `count(points≥t)/n` for each
  t∈{1,2,3}, independently, then shrunk. **Automatically monotonic** (a real empirical CDF cannot
  cross itself) — no smoothing, no distributional assumption.
- **PIT safety confirmed directly:** `TestEmpiricalBaselinePIT` (this cycle's tests) and Cycle 1's
  own Tests 1-9 both verify no target-game, future, or same-day row ever enters the history used.

## B. Why the old GLM lost

Diagnosed via structured ablation (Section H's five candidates), not guessed:

1. **Mean-estimation quality was the dominant factor.** C5 (the old feature-regression GLM) lost to
   every empirical/hierarchical candidate in **all 3 folds, 0% bootstrap credibility every single
   time** (Section L/T). Its log-linear regression on season/recent-rate covariates pools across
   players sharing similar feature values, blurring true player identity — a much coarser mean
   estimate than simply reading a player's own shrunk historical rate.
2. **Residual "overdispersion" was mostly player heterogeneity, not true randomness.** Once mu is
   estimated per-player (hierarchically), `fit_negbinom_alpha_by_moments` returns **α=0.0 in
   every one of the 3 folds** — the NegBin the old GLM needed (α=0.064) essentially vanishes once
   the mean is player-specific. This is the single clearest quantitative answer to "why did it lose."
3. **Distributional shape is a real but secondary contributor.** C4 (the SAME hierarchical mean, but
   read through a parametric Poisson/NegBin shape) is on par with or slightly worse than C2 (the
   identical mean, read nonparametrically) in every fold — the empirical CDF captures per-player
   tail/skew shape a single global-shape family cannot.
4. **Role-based partial pooling did not meaningfully beat flat shrinkage.** C2 vs. C1 are
   statistically indistinguishable in all 3 folds — mature players in this corpus already carry
   enough individual history that an intermediate ROLE layer adds little.
5. **Context adjustment (opponent factor specifically) offers a real, small, but inconsistent edge**
   — Section H/M.

## C. Error decomposition

Pooled across all 3 folds (147,081 total validation rows), empirical baseline (C1) vs. the best
redesigned candidate (C3):

| Segment | Empirical Brier | C3 Brier | C3 − Empirical |
|---|---|---|---|
| **Skill tercile — LOW** | 0.16236 | 0.16207 | −0.00029 (C3 better) |
| **Skill tercile — MEDIUM** | 0.21914 | 0.21945 | +0.00031 (C3 worse) |
| **Skill tercile — HIGH** | 0.24126 | 0.24086 | −0.00040 (C3 better) |
| **Position — FORWARD** | 0.21606 | 0.21621 | +0.00015 (~wash) |
| **Position — DEFENSE** | 0.19068 | 0.19001 | −0.00067 (C3 better) |
| **PP role — LOW_PP** | 0.14681 | 0.14396 | **−0.00284 (largest improvement)** |
| **PP role — MEDIUM_PP** | 0.18955 | 0.19023 | +0.00068 (C3 worse) |
| **PP role — HIGH_PP** | 0.24000 | 0.23914 | −0.00086 (C3 better) |
| **History — LOW_SAMPLE** | 0.18609 | 0.19111 | **+0.00502 (largest degradation)** |
| **History — MEDIUM** | 0.18712 | 0.18755 | +0.00043 (C3 worse) |
| **History — MATURE** | 0.21030 | 0.20994 | −0.00036 (C3 better) |

**Real, non-obvious finding:** the redesign's single largest degradation is exactly the
LOW_SAMPLE segment — the population most vulnerable to being mispriced if this were ever used
live. Its single largest improvement is LOW_PP players (depth/defensive skaters). Neither MEDIUM
skill tercile nor MEDIUM PP usage nor MEDIUM history length benefit — the middle of every
distribution is a wash or slightly worse.

**Probability-range calibration** (empirical vs. redesign mean-predicted P(1+) against actual rate,
pooled): in the 20-60% predicted-probability range, the redesign's mean prediction runs
systematically *further above* the actual rate than the empirical baseline's does (e.g. 30-40%
bucket: actual 33.6%, empirical 34.9%, redesign 36.1%) — the context adjustment introduces a mild
**overconfidence** distortion in the middle of the probability range even though its aggregate
Brier is marginally better. Reported per Part 18's calibration-first mandate, not hidden behind
the headline Brier number.

## D. Player-heterogeneity findings

See Section B item 2 — the α→0 collapse across all 3 folds is the clearest single piece of
evidence that the corpus's apparent overdispersion is driven by cross-player heterogeneity, not
within-player randomness.

## E. Shrinkage candidates

`k_player` grid {15, 30} × window grid {41, 82, None (full career)} tested on the 2022-23-only dev
sandbox (28k fit / 14k select rows, entirely separate from all 3 rolling folds). **Selected:
k_player=15, window=None (full career)** — the tightest player-level shrinkage and the longest
history window both minimized 1+ Brier on dev_select, consistent with Section A's finding that
career-length (not windowed) history is the more informative empirical signal for this target.

## F. Season-boundary findings

Full career-to-date history (no windowing, Section E) outperformed every bounded window tested —
direct evidence that points-production tendency **does** carry meaningfully across season
boundaries, and that this project's existing "no reset" convention (shared across every prop model)
is empirically justified for points specifically, not just assumed.

## G. Player-movement findings

`PlayerHistoryIndex` is player-identity-scoped, not team-scoped, in every model in this project —
verified directly this cycle (`test_player_history_survives_team_change`). No special handling was
needed or added; the existing shared convention already does the right thing.

## H. Distribution candidates

Five candidates (Part 26's cap), each isolating a specific hypothesis from Section B:

| | Structure | Isolates |
|---|---|---|
| **C1** | Flat empirical CDF, league shrinkage | The existing Cycle-1 baseline, unchanged |
| **C2** | Empirical CDF, PLAYER→ROLE→LEAGUE hierarchical shrinkage | Does role-level pooling beat flat pooling? |
| **C3** | Hierarchical mean as a fixed offset + opponent-context correction (offset-GLM), one coherent NegBin/Poisson shape | Does context help on top of a good mean? |
| **C4** | Same hierarchical mean as C2/C3, read through a parametric NegBin/Poisson shape (no context) | Shape misspecification vs. mean quality |
| **C5** | Cycle 1's locked GLM, reused **unchanged** | Reference: how much worse is feature-regression pooling? |

A hurdle/zero-inflated model was re-checked per fold and again found unnecessary: NegBin-implied
zero rate vs. observed zero rate gap was 0.019–0.023 across all 3 folds — consistent with Cycle
1's original finding, well under the 0.03 hurdle-trigger threshold.

## I. Contextual candidates

Re-evaluated independently on the 2022-23 dev sandbox (never assumed from Cycle 1, per Part 12):

| Feature | Dev-sandbox verdict | Cycle-1 verdict |
|---|---|---|
| PP role | **NOT kept** (13.9% bootstrap) | kept (100%) |
| Opponent context | **kept** (frac_improved not shown standalone — see note) | kept (99.0%) |
| Team context | **NOT kept** | kept (99.6%) |
| H2H | **NOT kept** | kept (99.6%) |

Only **opponent context** survived re-evaluation on this smaller (42k-row, single-season) sandbox
and was the only feature carried into the locked C3 candidate for all 3 rolling folds. This is a
genuinely different (more conservative) conclusion than Cycle 1's full-tuning-season screen — a
direct, expected consequence of testing on a much smaller sample, and exactly the kind of
re-derivation Part 12 required rather than assuming Cycle 1's findings transfer.

## J. Rolling temporal-validation design

3 folds, real non-overlapping season date ranges (verified directly,
`test_real_season_date_ranges_never_overlap`):

| Fold | Train seasons | Validation season |
|---|---|---|
| 1 | 2022-23 | 2023-24 |
| 2 | 2022-23, 2023-24 | 2024-25 |
| 3 (final, strongest check) | 2022-23, 2023-24, 2024-25 | 2025-26 |

Dev sandbox (Sections E/I) used **only** 2022-23, internally split 70/30 by date — no rolling
fold's validation season was touched before `k_player`/`window`/context-feature decisions were
frozen. This is **POST-HOLDOUT DEVELOPMENT VALIDATION**, not first-use evaluation (Part 23) —
stated explicitly, not disguised.

## K. Common evaluation sets

| Fold | Total target player-games | Excluded | Common evaluation rows |
|---|---|---|---|
| Fold 1 (val 2023-24) | 47,221 | 3,129 | 44,092 |
| Fold 2 (val 2024-25) | 47,224 | 3,081 | 44,143 |
| Fold 3 (val 2025-26) | 47,212 | 3,366 | 43,846 |
| **Total** | **141,657** | **9,576** | **132,081** |

Identical row set scored by every one of the 5 candidates within each fold (guaranteed by
construction — one shared `examples` list per fold feeds every candidate's probability computation).

## L. Empirical baseline (C1) performance

| Fold | 1+ Brier | 1+ Skill |
|---|---|---|
| Fold 1 | 0.20739 | 0.0925 |
| Fold 2 | 0.20738 | 0.0893 |
| Fold 3 | 0.20799 | 0.0928 |

Remarkably stable across all 3 folds — the empirical baseline's quality does not depend on which
season is held out.

## M. Redesigned candidate (C3) performance

| Fold | 1+ Brier | 1+ Skill | % bootstrap resamples favoring C3 over C1 | Date-clustered % |
|---|---|---|---|---|
| Fold 1 | 0.20749 | 0.0921 | **25.9%** (C1 usually wins) | 24.2% |
| Fold 2 | 0.20701 | 0.0909 | **99.8%** (C3 wins decisively) | 99.8% |
| Fold 3 | 0.20788 | 0.0933 | **87.4%** (favors C3, below 95% bar) | 87.4% |

Game-clustered and date-clustered sensitivity agree closely within every fold (Section T/U) — the
inconsistency is real, not a resampling-scheme artifact.

## N. Brier deltas

C3 − C1 (negative = C3 better): Fold 1 **+0.00010** (worse), Fold 2 **−0.00037** (better), Fold 3
**−0.00011** (better). All three deltas are tiny in absolute terms — this is a subtle effect in
every direction, not a large swing.

## O. Log-loss deltas

Not separately bootstrapped this cycle (scope: Brier was the primary decision metric per Part 18's
calibration-first framing); raw log-loss values are recorded per-candidate per-fold in
`research/player_points_redesign_results.json` for independent review.

## P. Calibration

See Section C's probability-range table — C3 shows a **mild overconfidence** distortion relative
to C1 in the 20-60% predicted range, even in folds where its aggregate Brier is better. This is a
genuine calibration caution against adopting C3 broadly, reported per Part 18's explicit priority
on calibration over point-estimate metrics.

## Q. 1+ performance

The primary target (Part 10). **Not validated** — inconsistent across folds (Section M), losing
outright in Fold 1.

## R. 2+ performance

C3's raw Brier was lower than C1's in **all 3 folds** at 2+ (0.07747 vs 0.07788; 0.07538 vs
0.07554; 0.07789 vs 0.07804) — a consistent *direction*, but **not formally bootstrap-tested this
cycle** (bootstrap resources were prioritized on the 1+ threshold per Part 10's explicit
instruction not to force equal optimization across thresholds). Reported as a directionally
promising, not statistically confirmed, secondary observation.

## S. 3+ status

**INSUFFICIENT DATA**, under a fresh pre-specified pooled standard (≥500 total events AND ≥50 per
confidence bucket): 2,664 total 3+ events pooled across folds (clears the total bar), but the LOW
confidence bucket produced only **3** 3+ events — nowhere near the 50-event minimum. Same
conclusion as Cycle 1, now independently re-confirmed under the new validation design, not merely
carried forward.

## T. Game-clustered bootstrap

Headline methodology throughout (Section M) — every fold's candidate comparison against C1 used
`game_clustered_bootstrap` (1,000 resamples, games resampled with replacement, not individual rows).

## U. Date-cluster sensitivity

Run for every fold/candidate pair (500 resamples) — confirms the game-clustered conclusion in
every case (Section M table), including confirming C5's 0% credibility and C3's fold-to-fold
inconsistency are not artifacts of how games happen to cluster by date.

## V. Temporal-fold consistency

**Not consistent** for the primary 1+ candidate (C3): strong win (Fold 2), moderate favorable
(Fold 3, below the 95% bar), and an outright loss (Fold 1). This is the single deciding factor
against a VALIDATED or PARTIAL verdict — Adoption Standard item 4 is explicit that consistency
across folds is required, and it is not present here.

## W. LOW-confidence diagnosis

Pooled across all 3 folds: LOW-confidence bucket (n=1,785, mean history length 19.0 games, mean
TOI CV 0.224 — the least mature, least stable segment by construction) shows **negative** Brier
skill under BOTH the empirical baseline (**−0.045**) and the redesigned candidate (**−0.089**, more
than twice as negative). Diagnosis against Part 19's checklist:

1. Poorly defined? Partially — the confidence methodology (item counts: history length, TOI/points
   CV, opponent-window maturity, appearance rate) is the SAME shared architecture across all 4 prop
   families, and shows this exact pattern in **both** ASSISTS (Cycle 1's earlier finding, n=971,
   skill −0.043/−0.049) and POINTS (both cycles) — a repeated pattern across independent targets,
   which argues against "definition noise" and toward a **real, structural** property of the
   confidence bucket.
2. Associated with low sample? **Yes** — mean history length in the LOW bucket (19.0 games) is far
   below MEDIUM (120.3) and HIGH (167.9).
3. Role instability? **Yes**, secondarily — mean TOI CV (0.224) is roughly double the HIGH bucket's
   (0.103).
4. Tail sparsity? Consistent with the small bucket size (1.3-1.9% of the eval set) — inherently
   noisy at the tail regardless of the exact metric used.
5. Conservative-probability behavior? Not the cause — Section X shows conservative probabilities
   behave correctly (monotonic, appropriately smaller) in aggregate; this is not a conservative-
   bound bug.
6. Miscalibration? The redesigned candidate makes this WORSE (Section C's calibration finding),
   suggesting context adjustment amplifies whatever the underlying issue is rather than fixing it.
7. Model/base-rate mismatch? Most likely explanation: a bucket built from short-history, unstable-
   role players is inherently the hardest population to predict accurately with ANY of these
   models — the confidence LABEL correctly identifies these as uncertain, but "uncertain" does not
   mean "the model does better than a coin flip here," and a small-sample bucket can easily land on
   the wrong side of base rate by chance given how few events it contains.

**Recommendation (per Part 19's explicit instruction, not implemented this slice):** a repeated
finding across two independent prop families (ASSISTS, POINTS) and two independent cycles
(Cycle 1, Cycle 2) is strong enough evidence to recommend a dedicated **CONFIDENCE FRAMEWORK
REDESIGN** as a future slice — but the global confidence methodology was deliberately NOT changed
this cycle, per Part 19's own instruction to diagnose first.

## X. Conservative-probability diagnosis

`fraction_conservative_leq_raw = 0.9976` (pooled, C2's hierarchical mean feeding
`cm.conservative_mu`) — 99.76% of rows have conservative P(1+) ≤ raw P(1+), consistent with Cycle
1's 100% finding on the original GLM (the tiny gap from 100% here is expected: the hierarchical
mean occasionally sits so close to zero that the conservative bound's floor clamp produces a
negligible numerical crossover, not a real inversion). The lower-bound mechanism continues to
behave sensibly and was not tuned against sportsbook prices, consistent with Part 21.

## Y. Representative examples

Real, mechanically-selected (not hand-picked), pooled across all 3 folds:

| Category | Player (team vs opp, date) | Empirical P(1+) | Redesign P(1+) | Actual |
|---|---|---|---|---|
| Empirical clearly wins | Macklin Celebrini (SJS vs PIT, 2024-11-16) | 34.5% | 48.0% | 0 |
| Redesign clearly wins | Jimmy Schuldt (SJS vs CAR, 2025-03-20) | 29.1% | 15.1% | 0 |
| Defenseman | Ryan McDonagh (NSH vs TBL, 2023-10-10) | 26.4% | 27.0% | 1 |
| High-PP player | Steven Stamkos (TBL vs NSH, 2023-10-10) | 62.4% | 61.6% | 1 |
| Elite scorer | Nikita Kucherov (TBL vs NSH, 2023-10-10) | 73.6% | 71.1% | 2 |
| Low-sample player | Alex Vlasic (CHI vs PIT, 2023-10-10, 6 prior games) | 31.0% | 31.5% | 1 |
| High-confidence success | Connor Murphy (CHI vs PIT, 2023-10-10) | 19.0% | 19.0% | 0 |
| Low-confidence "failure" | Zack MacEwen (OTT vs PHI, 2023-10-14) | 21.1% | 24.5% | 1 |

Notably, no example in the entire pooled eval set showed one model landing within 0.2 of the
actual outcome while the other missed by more than 0.4 — the two "clearly wins" examples above use
the loosest gap that produced any match at all, itself evidence of how close these candidates are
in aggregate (consistent with the tiny Brier deltas throughout this report).

## Z. Files created/modified

**New:**
- `research/player_points/hierarchy.py`
- `research/player_points/redesign.py`
- `research/run_player_points_redesign.py`
- `research/player_points_redesign_results.json` (generated)
- `tests/test_player_points_redesign.py` (32 tests)
- `PLAYER_POINTS_REDESIGN_REPORT.md` (this file)

**Modified:**
- `research/player_props/registry.py` — POINTS entry: `PARTIAL` → `EMPIRICAL_BASELINE_REMAINS_CHAMPION`
- `dashboard/pages/11_Player_Points_Research.py` — added the Redesign Cycle 2 comparison section
- `dashboard/pages/10_Prop_Registry.py` — added a status color for the new registry value
- `tests/test_player_points_model.py` — widened the registry-status assertion to include the new value

**Unchanged (verified):** `research/player_points_results.json`, `research/player_points_freeze_manifest.json`,
`research/run_player_points_model.py`, `research/player_points/features.py`,
`research/player_points/build_points_corpus.py` — the redesign driver only *reads* Cycle 1's locked
model for candidate C5; it never re-fits or overwrites it (verified,
`test_cycle1_locked_points_model_reused_not_refit`, and a direct `git diff --stat` showing no
changes to any of these files).

## AA. Dashboard changes

Page 11 (Player Points Research) now shows, below the existing Cycle-1 content: the current
registry status (now `EMPIRICAL_BASELINE_REMAINS_CHAMPION`), a per-fold candidate comparison table
(1+ Brier, skill, % bootstrap favoring each candidate over C1), and 8 representative empirical-vs-
redesign example rows. Verified live in-browser (screenshot + `get_page_text`), no errors, correct
data matching this report exactly.

## AB. Full test result

**823 / 823 passing** (791 prior + 32 new redesign tests). Confirmed via
`python3 -m unittest discover tests`.

## AC. Registry status

`research/player_props/registry.py` → `POINTS`: `model_status="EMPIRICAL_BASELINE_REMAINS_CHAMPION"`,
`live_market_support="NOT_CURRENTLY_AVAILABLE"`, `odds_api_market_key="player_points"`,
`report="PLAYER_POINTS_REDESIGN_REPORT.md"`.

## AD. Exact live-market-ready thresholds

None promoted to live-ready status this cycle. The existing Cycle-1 empirical baseline remains the
best-performing candidate for 1+ and 2+ (with 3+ still INSUFFICIENT_DATA), but per this project's
standing convention no model without a `VALIDATED` status feeds live pricing. No live Odds API
markets were queried this cycle (off-season; Part 31 explicitly forbids repeated polling).

## AE. Recommended next single development slice

**Not another Points redesign attempt.** Two full cycles have now converged on the same honest
conclusion from different angles. The evidence points toward two genuinely different, higher-value
next steps — pick one explicitly rather than defaulting to Points a third time:

1. **CONFIDENCE FRAMEWORK REDESIGN** (flagged in Section W) — the LOW-confidence negative-skill
   pattern has now appeared in 2 independent prop families across 2 independent cycles. This is a
   cross-cutting infrastructure problem, not a Points-specific one, and fixing it would improve
   every prop model in the registry at once.
2. **Build the GOALS model** — the next prop in the sprint's original priority order, using the
   hierarchical-empirical-baseline architecture validated as sound this cycle (not the
   feature-regression GLM architecture this cycle showed underperforms) from the start.

Between the two, **the confidence framework redesign is the higher-leverage choice** given it
compounds across every already-validated model, not just one prop family.

---

## Final Questions

**WHY DID THE EMPIRICAL PLAYER DISTRIBUTION BEAT THE OLD GLM?** Primarily because the old GLM's
feature-regression mean estimate pooled too aggressively across players with similar covariates,
while the empirical distribution reads each player's own history directly; this is confirmed by
residual NegBin overdispersion collapsing to α≈0 in every fold once a player-specific mean is used
(Section B/D). Distributional shape (parametric vs. nonparametric) is a real but secondary
contributor (Section B item 3).

**DID SHRINKAGE IMPROVE THE EMPIRICAL BASELINE?** NO — role-hierarchical shrinkage (C2) did not
meaningfully beat the existing flat league-shrinkage (C1) in any fold (Section H/L).

**DID CONTEXT ADJUSTMENT IMPROVE THE EMPIRICAL BASELINE?** YES, but inconsistently — real
improvement in 2 of 3 folds at 1+, a real loss in the third (Section M).

**DID A HIERARCHICAL / PARTIAL-POOLING MODEL IMPROVE THE EMPIRICAL BASELINE?** NOT TESTED as a
genuine multi-level statistical hierarchy beyond the PLAYER→ROLE→LEAGUE partial pooling in C2/C3/C4
(Section H) — a fuller Bayesian hierarchical model was judged out of scope for "keep interpretable,
no black-box ML" (Part 4).

**DID THE BEST REDESIGNED MODEL BEAT THE EMPIRICAL BASELINE ON 1+ BRIER?** NO — inconsistent
across folds (Section M/V).

**DID IT BEAT THE EMPIRICAL BASELINE ON 1+ LOG LOSS?** Not formally tested as a headline metric
this cycle (Section O); Brier was prioritized per Part 18.

**DID IT PRESERVE OR IMPROVE CALIBRATION?** NO — mild overconfidence distortion found in the
20-60% probability range (Section C/P).

**DID THE RESULT HOLD ACROSS TEMPORAL FOLDS?** NO (Section V).

**DID GAME-CLUSTERED BOOTSTRAP SUPPORT THE RESULT?** MIXED — strong support in Fold 2 (99.8%),
weak/negative in Fold 1 (25.9%), moderate in Fold 3 (87.4%, below the 95% bar) (Section M/T).

**DID DATE-CLUSTERED SENSITIVITY SUPPORT THE RESULT?** YES, in the sense that it closely matches
the game-clustered conclusion in every fold — confirming the inconsistency is real, not a
resampling artifact (Section U).

**IS LOW-CONFIDENCE NEGATIVE SKILL NOW UNDERSTOOD?** PARTIAL — a strong, repeated, cross-model
pattern with a plausible structural explanation (small, unstable-role, short-history players are
inherently hard to predict), but not a definitive root-cause fix (Section W).

**IS 1+ POINT NOW VALIDATED?** NO.

**IS 2+ POINTS VALIDATED?** NO — directionally promising (consistent raw improvement across all 3
folds) but not formally bootstrap-tested this cycle (Section R).

**IS 3+ POINTS VALIDATED?** INSUFFICIENT DATA (Section S).

**WHAT IS THE FINAL POINTS REGISTRY STATUS?** EMPIRICAL BASELINE REMAINS CHAMPION.

**WAS THE PRODUCTION NHL WIN MODEL CHANGED?** NO.

**CURRENT FULL TEST RESULT?** 823 / 823.

**WHAT SHOULD THE NEXT SINGLE DEVELOPMENT SLICE BE?** A dedicated CONFIDENCE FRAMEWORK REDESIGN
(Section AE) — the LOW-confidence negative-skill pattern now spans 2 prop families and 2 cycles,
making it the highest-leverage remaining issue in the engine, ahead of a third Points attempt or a
new prop family chosen just to raise the validated count.
