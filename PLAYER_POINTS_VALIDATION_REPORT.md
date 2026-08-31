# Player Total Points — Locked Walk-Forward Validation

**Verdict: POINTS — PARTIAL.** The locked model beats 3 of 4 naive baselines robustly, but loses to
the 4th (a simple per-player empirical-distribution baseline) across every threshold, in both a
game-clustered and a date-clustered bootstrap. That is reported plainly below, not smoothed over.
3+ points is **INSUFFICIENT DATA** against its own pre-specified support standard. See Final
Questions at the end for the itemized answers this prompt required.

---

## A. Data audit

Real MoneyPuck skater-game fields confirmed present (`research/player_sog/raw/*.csv`, the same
already-archived files used by SOG/blocks/assists — no new download):

| Concept | Real field |
|---|---|
| Goals | `I_F_goals` |
| Assists (primary+secondary, no combined column exists) | `I_F_primaryAssists` + `I_F_secondaryAssists` |
| Points (direct field) | `I_F_points` |
| Primary / secondary assists | `I_F_primaryAssists` / `I_F_secondaryAssists` |
| SOG | `I_F_shotsOnGoal` |
| Shot attempts | `I_F_shotAttempts` |
| Individual xG | `I_F_xGoals` |
| On-ice xGF | `OnIce_F_xGoals` |
| On-ice xGA | `OnIce_A_xGoals` |
| Total TOI | `icetime` (situation="all") |
| 5v5 TOI | `icetime` (situation="5on5") |
| PP TOI / PP production | `icetime`, `I_F_points`/`I_F_goals`/assists (situation="5on4") |
| Player / team / opponent / game / date / season | `playerId` / `playerTeam` / `opposingTeam` / `gameId` / `gameDate` / `season` |

**Cross-check (Part 1 requirement):** `I_F_points == I_F_goals + I_F_primaryAssists +
I_F_secondaryAssists` re-verified for every row at corpus-build time, all four raw seasons —
**0 mismatches out of 188,863 rows**. `actual_points` is taken as the direct `I_F_points` field.

## B. Points corpus size

`research/player_points/build_points_corpus.py` → `research/player_points/player_game_points.jsonl`:
**188,863 rows**, 1,330 unique players, 4 seasons (2022-23 … 2025-26), 0 rows excluded as
outside the real regular-season game-ID corpus, 0 label cross-check mismatches, 119,355 rows
carry real PP-situation data (5-on-4 icetime > 0).

## C. Projected-active vs. actually-played accounting

**POINTS-MODEL EVALUATION: CONDITIONAL ON ACTUAL GAME PARTICIPATION.** The MoneyPuck skater-game
corpus only contains rows for players who actually appeared — there is no pregame scratch/injury
candidate-pool data source in this project to evaluate the `projected_active()` gate against the
full pregame population, so per Part 3's own instruction this conditionality is stated explicitly
rather than implied.

| | 2024-25 | 2025-26 |
|---|---|---|
| Total target player-games (real "all"-situation rows) | 47,224 | 47,212 |
| Excluded (insufficient history OR not projected-active) | 3,081 | 3,366 |
| **Common evaluation rows** | **44,143** | **43,846** |

- Projected-active candidate rows: same as common evaluation rows above (188,863 total scored-season rows minus 1,101 insufficient-history + 8,475 not-projected-active across tuning+eval).
- Projected-active true/false positives: **NOT MEASURABLE WITH THIS DATASET** — no pregame roster/scratch source exists to check against.
- Actual participants = final points-model evaluation rows = **87,989** (both eval seasons combined).

## D. Goal/assist aggregate dependence

Computed over all scored-season rows (tuning + eval, diagnostic only — never used for design):

| | Value |
|---|---|
| P(goal) | 0.1507 |
| P(assist) | 0.2389 |
| P(goal AND assist), observed | 0.04466 |
| P(goal) × P(assist), independence | 0.03600 |
| **Lift ratio (observed / independence)** | **1.241** |

Goals and assists co-occur 24% more often than independence predicts — real, material aggregate
dependence, confirming the prompt's instruction not to naively multiply/sum independent goal and
assist probabilities.

## E. Goal/assist within-player dependence

Restricted to the 622 players with ≥100 scored-season games, computing each player's own
P(goal)·P(assist) vs. observed joint rate:

**Within-player mean lift ratio: 1.015** (n=622 players) — essentially 1.0, i.e. no material
dependence once player identity is controlled for. **Interpretation:** the aggregate 1.24×
lift in Section D is almost entirely explained by player heterogeneity (some players are simply
generally more productive across the board), not a true within-player joint tendency for goals and
assists to cluster in the same game beyond what each player's own overall production rate already
implies. This does not change the modeling decision — the headline model remains a direct
total-points count model regardless (per the prompt's explicit instruction) — but it is a real,
non-obvious finding worth recording for any future joint-modeling work.

## F. Baseline models

All four evaluated on the identical 87,989-row common evaluation set:

| Baseline | Definition |
|---|---|
| A — season-to-date | `season_to_date_mean(points)` per game |
| B — last-10 | `rolling_mean(points, 10)` per game |
| C — per-60 × recent TOI | season points/60 (via `rolling_per60`, reused unchanged from `player_sog.features`) × recent-10 TOI |
| D — empirical distribution | player's own historical `P(points≥t)` empirical frequency, shrunk `n/(n+20)` toward the league-wide pre-lock empirical rate at each threshold — **not** a parametric family |

## G. Distribution models

Fit on `tuning_fit` only (2023-24, before 2024-02-24):

| | Value |
|---|---|
| Mean | 0.4725 |
| Variance | 0.5464 |
| Variance/mean ratio | 1.156 |
| Observed zero rate | 0.6455 |
| Poisson-implied zero rate at same mean | 0.6234 (gap 0.0221) |
| NegBin-implied zero rate (fitted α=0.0636) | 0.6278 (gap 0.0177) |
| **Hurdle/zero-inflated model needed?** | **NO** — NegBin closes most of the small Poisson gap on its own; the residual 1.8-point gap does not meet this project's 3-point hurdle-trigger threshold. |

**Selected: Negative Binomial**, mildly overdispersed (α=0.0636, milder than SOG/blocks, close to
assists' ~0.09-0.14 range). `threshold_probabilities()` (reused unchanged from
`research.player_sog.count_models`) guarantees `P(1+) ≥ P(2+) ≥ P(3+)` by construction — verified
directly (Test 12).

## H. Final tuning-period candidate set

Feature-stage keep/drop decided on `tuning_validate` (2023-24 rows on/after 2024-02-24, **13,855**
rows, 410 distinct games) via **game-clustered bootstrap**, 95% credibility bar (this project's
established convention — see the assists slice's "70% below the 95% bar" precedent):

| Stage transition | frac_improved | Verdict |
|---|---|---|
| baseline → +recent form | 0.139 | recent form: **NOT kept** |
| +recent form → +TOI/role | 0.122 | TOI/role: **NOT kept** |
| +TOI/role → +PP role | 1.000 | PP role: **kept** |
| +PP role → +opponent context | 0.990 | opponent context: **kept** |
| +opponent context → +team context | 0.996 | team context: **kept** |
| +team context → +H2H | 0.996 | H2H: **kept** |

Locked candidate: the **full 8-feature model (`M6_plus_h2h`)** — every feature independently
tested; weak features (recent form, TOI/role) simply carry a near-zero learned GLM weight rather
than being architecturally removed, so a single fixed feature set is what the freeze manifest
records (see Section S).

## I. Upstream SOG eligibility

**UPSTREAM SOG FEATURE: NOT ELIGIBLE FOR CLEAN TRUE-EVALUATION USE.** The validated SOG model
(`PLAYER_SOG_FOUNDATION_REPORT.md`) exposes only a single globally-fitted weight state (fit once on
its own tuning season, then applied uniformly to score every eval-season row), not a sequence of
point-in-time model states re-derivable as of an arbitrary earlier target date. Its own headline
value-tests were also reported directly against its eval seasons rather than a separate
pre-registered tuning-only holdout. Reconstructing a genuinely pre-target, out-of-fold SOG
prediction for each of 87,989 points target rows is not possible with the SOG driver's current
architecture without risking exactly the holdout contamination Part 10 warns against. Per Part 10's
explicit instruction, the safe call is **not used**. `research/run_player_points_model.py` and
`research/player_points/features.py` contain no import of `player_sog.live_projection` or
`player_sog_results` (verified, Test 13).

## J. Upstream assists eligibility

**UPSTREAM ASSISTS FEATURE: NOT ELIGIBLE FOR CLEAN TRUE-EVALUATION USE**, for the identical
reason as Section I (the assists driver has the same single-fitted-state architecture). No import
of the assists model exists in the points modules (verified, Test 14).

## K. TOI value

**NO** — `M1→M2` (+TOI/role) frac_improved = 0.122 on tuning_validate, far below the 95% bar.
Learned weight on `toi_log_ratio` in the locked model: 0.0130 (near zero). A genuinely different
finding from SOG (where TOI/role *did* add value) — independently tested, not assumed.

## L. PP-role value

**YES** — `M2→M3` (+PP role) frac_improved = 1.000 (100% of 1,000 bootstrap resamples favored
adding it). Learned weight: 0.1393, the largest non-baseline coefficient in the locked model. This
is the first prop family in this project with a dedicated PP-role ablation, and it is the single
strongest independently-tested feature found.

## M. H2H value

**YES** — `M5→M6` (+H2H) frac_improved = 0.996. Learned weight: 0.0367 (small but real and durable
across 1,000 resamples).

## N. Recent-form value

**NO** — `M0→M1` frac_improved = 0.139. Consistent with assists' own earlier finding ("recent form
only marginal, 70% bootstrap, below the 95% bar") — recent-form signal is weak or absent for
counting-stat props generally in this corpus, now independently confirmed a second time for a
different target.

## O. Team-context value

**YES** — `M4→M5` (+team context) frac_improved = 0.996. This is the first prop family with a
dedicated team-offense-environment ablation (Parts 14/15 distinguish team context from opponent
context for the first time), and it clears the bar. Learned weight: **-0.0434** — a small,
statistically-supported-in-tuning but directionally *counterintuitive* coefficient (a stronger
team offensive environment predicts *slightly lower* individual points, holding the other features
fixed). Reported honestly: this is likely a genuine multicollinearity/shrinkage artifact of the
GLM (team context correlates with a player's own baseline rate, which is already the dominant
feature), not a claim that playing for a lower-scoring team helps an individual player.

## P. Opponent-context value

**YES** — `M3→M4` (+opponent context) frac_improved = 0.990. Learned weight: 0.1259 (second-largest
after PP role), correctly signed (playing a weaker defensive opponent predicts more points).

## Q. Model freeze timestamp

`2026-08-27T20:39:29.582076Z`

## R. Freeze manifest path

[`research/player_points_freeze_manifest.json`](research/player_points_freeze_manifest.json)

## S. Final locked feature set

`intercept, log_baseline_rate, recent_form_log_ratio, toi_log_ratio, pp_role_rate,
opponent_log_factor, team_context_log_factor, h2h_shrunk_delta` — exact formulas recorded in the
manifest's `feature_formulas` block (reproduced in Section H's table above and the manifest file
itself).

## T. Final locked model family

Negative Binomial, α = 0.06361255157281723 (fit by method-of-moments on `tuning_fit` residuals,
via the shared, unmodified `research.player_sog.count_models.fit_negbinom_alpha_by_moments`).

## U. Final locked hyperparameters

GLM: plain batch gradient descent, `lr=0.05`, `n_iter=400` (shared `cm.fit_poisson_glm`, unmodified).
Fitted weights (on `tuning_fit`, 30,237 rows):

```
intercept                -0.2301
log_baseline_rate         0.6683
recent_form_log_ratio     0.0065
toi_log_ratio              0.0130
pp_role_rate               0.1393
opponent_log_factor        0.1259
team_context_log_factor   -0.0434
h2h_shrunk_delta            0.0367
```

## V. Final locked calibration

**NONE.** Tuning_validate calibration gaps at every threshold were within the 0.02 absolute
tolerance set before any threshold was checked (1+: gap -0.00006; 2+: gap -0.0052; 3+: gap
-0.0033) — the uncalibrated model was already acceptable, so no scaling was fit. `calibration_scales`
in the manifest are the identity (1.0) at every threshold, recorded explicitly rather than omitted.

## W. Final locked conservative-probability methodology

Shared, unmodified `research.player_sog.count_models.conservative_mu` (normal-approximation lower
bound on the fitted rate, z=0.84) — identical to SOG/blocks/assists, frozen before this slice began.

## X. Final locked confidence methodology

Shared, unmodified `research.player_sog.count_models.confidence_score` — identical to
SOG/blocks/assists, frozen before this slice began. No points-specific tuning was performed or
needed.

## Y. Locked 3+ support standard

Pre-specified before any eval-season row was scored (module-level constant
`THREE_PLUS_SUPPORT_STANDARD`, Test 22 confirms it is a static constant, not eval-derived):

| Check | Threshold |
|---|---|
| Minimum total 3+ events, common eval set | ≥ 500 |
| Minimum events per confidence bucket (HIGH/MEDIUM/LOW) | ≥ 50 each |
| Minimum events per eval season | ≥ 150 |
| Max bootstrap CI half-width | ≤ 0.01 |
| Max tail variance/mean ratio (among 3+ rows) | ≤ 3.0 |

## Z. Confirmation: holdout not viewed before freeze

The freeze manifest (Section Q/R) was written by code positioned before the `# PART 22: FREEZE
COMPLETE` marker in `research/run_player_points_model.py`; every line of code between `PHASE 2 --
TUNING-VALIDATE` and that marker was verified (Test 25, Test 26) to contain no reference to the
literal season identifiers `20242025` or `20252026`, and Test 19 confirms the calibration
computation in that same block never references `eval_examples`/`eval_fm`. No 2024-25 or 2025-26
row was read before the manifest was written to disk.

## AA. True evaluation common-set counts by season

| Season | Total target player-games | Excluded | Common evaluation |
|---|---|---|---|
| 2024-25 | 47,224 | 3,081 | 44,143 |
| 2025-26 | 47,212 | 3,366 | 43,846 |
| **Total** | **94,436** | **6,447** | **87,989** |

Identical row set used for the headline model AND every baseline (verified, Test 27 — headline `n`
and every baseline `n` are the same 87,989).

## AB. Uncalibrated holdout metrics

| Threshold | Brier | Log loss | Actual rate | Brier skill score |
|---|---|---|---|---|
| 1+ | 0.20955 | 0.60840 | 0.35334 | 0.0829 |
| 2+ | 0.07735 | 0.27601 | 0.09116 | 0.0663 |
| 3+ | 0.01859 | 0.08516 | 0.01953 | 0.0290 |

MAE (expected points): 0.5432. NLL (mean, NegBin): 0.8573.

## AC. Calibrated holdout metrics

Identical to Section AB — no calibration was fit (Section V), so `headline_calibrated` in
`research/player_points_results.json` is numerically the same as `headline_uncalibrated` (scale
factors are 1.0 at every threshold).

## AD. 1+ Brier/log loss/calibration

Brier 0.20955, log loss 0.60840. Mean predicted probability vs. actual rate on tuning_validate
(the calibration diagnostic, Section V): predicted 0.3504 vs. actual 0.3505 — a 0.00006 gap,
essentially perfectly calibrated at this threshold.

## AE. 2+ Brier/log loss/calibration

Brier 0.07735, log loss 0.27601. Tuning_validate calibration gap: predicted 0.0880 vs. actual
0.0933 (gap -0.0052) — small under-confidence, within tolerance.

## AF. 3+ metrics or INSUFFICIENT DATA

**POINTS 3+: INSUFFICIENT DATA.** Uncalibrated eval metrics are reported for completeness (Brier
0.01859, log loss 0.08516, actual rate 0.01953, skill 0.0290), but the pre-specified Section Y
standard fails on exactly one sub-check:

| Check | Result | Pass? |
|---|---|---|
| Total 3+ events (need ≥500) | 1,718 | ✅ |
| Per-confidence-bucket events (need ≥50 each) | HIGH=1,480, MEDIUM=237, **LOW=1** | ❌ |
| Per-season events (need ≥150 each) | 2024-25=813, 2025-26=905 | ✅ |
| Bootstrap CI half-width (need ≤0.01) | 0.000119 | ✅ |
| Tail variance/mean ratio (need ≤3.0) | 0.0671 | ✅ |

The LOW-confidence bucket produced only 1 real 3+ event across both full evaluation seasons —
nowhere near enough to say anything meaningful about 3+ calibration specifically within that
bucket. Per Section Y's own binding rule, this standard is not relaxed after seeing the result:
**3+ points stays INSUFFICIENT DATA**, not adopted.

## AG. Season-by-season results

| Season | n | MAE | 1+ Brier (skill) | 2+ Brier (skill) | 3+ Brier (skill) |
|---|---|---|---|---|---|
| 2024-25 | 44,143 | 0.5391 | 0.20926 (0.0810) | 0.07600 (0.0630) | 0.01763 (0.0246) |
| 2025-26 | 43,846 | 0.5473 | 0.20985 (0.0847) | 0.07872 (0.0695) | 0.01955 (0.0329) |

Both seasons show positive, similarly-sized skill at every threshold — the model generalizes
consistently rather than gaining only in one season (Adoption Standard item 6 passes on its own
terms, independent of the baseline-D finding below).

## AH. Game-clustered bootstrap

vs. each baseline, threshold=1, 1,000 resamples, 2,624 distinct games:

| Baseline | Point delta (candidate − baseline) | 95% CI | % favoring candidate |
|---|---|---|---|
| A — season-to-date | -0.00083 | [-0.00137, -0.00030] | **99.9%** |
| B — last-10 | -0.00875 | [-0.00943, -0.00807] | **100%** |
| C — per60 × recent TOI | -0.00329 | [-0.00416, -0.00247] | **100%** |
| D — empirical distribution | **+0.00186** | [+0.00132, +0.00238] | **0%** |

Negative delta = candidate improves (lower Brier is better). The locked model **beats A, B, and C**
with overwhelming bootstrap credibility, but **loses to D in all 1,000 resamples** — the CI is
tight and entirely on the "worse" side, i.e. this is not sampling noise.

## AI. Date-cluster sensitivity

Repeated with date-clustered (not game-clustered) resampling, 500 resamples, 345 distinct dates —
same conclusion for every baseline including D (point delta +0.00186, 0% favoring candidate,
95% CI [+0.00142, +0.00234]). The baseline-D finding is robust to the clustering scheme used, not
an artifact of how games happen to group.

## AJ. Confidence performance

| Bucket | n | 1+ skill | 2+ skill | 3+ skill |
|---|---|---|---|---|
| HIGH | 60,157 | 0.0789 | 0.0634 | 0.0275 |
| MEDIUM | 26,692 | 0.0426 | 0.0367 | 0.0181 |
| LOW | 1,140 | **-0.0362** | -0.0086 | -0.0660 |

HIGH clearly outperforms LOW at every threshold, and HIGH > MEDIUM in the expected direction.
Reported honestly: the LOW bucket (1.3% of the eval set) shows a **negative** skill score at every
threshold — the model performs *worse than the naive base rate* specifically for its own
lowest-confidence predictions. This mirrors an open finding already reported for the assists model
(a small negative-skill LOW bucket) — now observed a second time for a different prop family. Not
explained away without evidence; flagged as a genuine open question about the confidence
methodology's behavior in its sparsest bucket.

## AK. Conservative-probability performance

100% of eval rows have conservative P(1+) ≤ raw P(1+) (`fraction_conservative_leq_raw = 1.0`) —
the monotonicity guarantee holds with no exceptions. Mean raw P(1+) = 0.3510 vs. mean conservative
P(1+) = 0.2707, a real, non-trivial (8.0 percentage point) downward adjustment on average.

## AL. Representative examples

(Player names/dates are real, pulled mechanically from the eval set by the selection rules below —
not hand-picked for a favorable outcome.)

| Category | Player (team vs opp, date) | Expected pts | P(1+) | P(2+) | Confidence | Actual |
|---|---|---|---|---|---|---|
| Elite scorer/playmaker | Timo Meier (NJD vs BUF, 2024-10-04) | 0.906 | 58.6% | 23.1% | HIGH | 0 |
| High-assist player | Jack Hughes (NJD vs BUF, 2024-10-05) | 0.785 | 53.5% | 18.8% | HIGH | 2 |
| Shooting-heavy player | Jordan Kyrou (STL vs SEA, 2024-10-08) | 0.803 | 54.3% | 19.4% | HIGH | 2 |
| Defenseman | Rasmus Dahlin (BUF vs NJD, 2024-10-04) | 0.599 | 44.5% | 12.4% | HIGH | 0 |
| PP-heavy player | Alex Tuch (BUF vs NJD, 2024-10-04) | 0.791 | 53.8% | 19.0% | HIGH | 0 |
| Strong H2H (6 games) | Erik Haula (NJD vs BUF, 2024-10-04) | 0.354 | 29.5% | 5.1% | HIGH | 0 |
| Weak H2H (0 games) | Kurtis MacDermid (NJD vs BUF, 2024-10-05) | 0.100 | 9.5% | 0.5% | MEDIUM | 0 |
| High confidence | Erik Haula (NJD vs BUF, 2024-10-04) | 0.354 | 29.5% | 5.1% | HIGH | 0 |
| Low confidence | Shane Wright (SEA vs STL, 2024-10-08) | 0.469 | 37.0% | 8.3% | LOW | 0 |
| Correct prediction | Erik Haula (predicted <1, actual 0) | 0.354 | 29.5% | 5.1% | HIGH | 0 |
| Model miss | Connor McDavid (EDM vs WPG, 2024-10-09) | 1.260 | 70.2% | 35.5% | HIGH | **0** |

The McDavid miss is a genuine, non-cherry-picked example of the model's real failure mode: a
70% P(1+) prediction on a player it is highly confident in, that did not hit — exactly the kind
of case a betting board needs to show honestly rather than hide. All examples above are the FIRST
row in date order matching each selection rule (mechanical, not hand-picked for a favorable
outcome) — full real values live in `research/player_points_results.json`'s
`representative_examples` block and render live on the **Player Points Research** dashboard page
for any historical date/player the user selects.

## AM. Files created/modified

**New:**
- `research/player_points/build_points_corpus.py`
- `research/player_points/features.py`
- `research/player_points/live_projection.py`
- `research/run_player_points_model.py`
- `research/player_points/player_game_points.jsonl` (generated, gitignored)
- `research/player_points_results.json` (generated)
- `research/player_points_freeze_manifest.json` (generated)
- `dashboard/player_points_view.py`
- `dashboard/pages/11_Player_Points_Research.py`
- `tests/test_player_points_model.py` (37 tests)
- `PLAYER_POINTS_VALIDATION_REPORT.md` (this file)

**Modified:**
- `research/player_props/registry.py` — POINTS entry updated from `RESEARCH` to `PARTIAL`, summary rewritten to reflect the real result
- `dashboard/app.py` — added page 11 to navigation
- `.gitignore` — added the new generated corpus path

## AN. Bugs found/fixed

None found this slice. The dashboard page, view module, and live-projection module were exercised
directly (imports, a full model run producing real output, and a source-level check that the page
reads status from the registry rather than a hardcoded string) with no new defects surfacing.

## AO. Full test result

**791 / 791 passing** (754 prior baseline + 37 new points tests). Confirmed via
`python3 -m unittest discover tests`.

## AP. Final registry status

`research/player_props/registry.py` → `POINTS`: `model_status="PARTIAL"`,
`live_market_support="NOT_CURRENTLY_AVAILABLE"`, `odds_api_market_key="player_points"`,
`report="PLAYER_POINTS_VALIDATION_REPORT.md"`.

## AQ. Market thresholds ready for future live pricing

1+ and 2+ are structurally ready to feed the shared `PropPrediction` contract (`market_type=
"POINTS"`) the moment `player_points`/`player_points_alternate` markets are posted — no naive
odds queries were made this slice (Odds API is off-season; Part 30 explicitly forbids repeated
offseason polling). 3+ is not ready (Section AF). Given the PARTIAL verdict, none of these
thresholds should be priced live yet regardless of market availability — see Section AR.

## AR. Recommended next single development slice

**Do not build Goals next.** The real finding this slice is that the parametric Negative Binomial
GLM loses to a simple nonparametric per-player empirical-distribution baseline (Section AH/AI),
consistently across both clustering schemes and all three thresholds. Per Part 27's own rule
("if [ablations/diagnostics] expose a problem requiring a design change... return NEEDS NEW
DEVELOPMENT CYCLE"), the correct next slice is a **dedicated POINTS redesign cycle**: investigate
why the empirical-shrinkage baseline outperforms the fitted GLM (candidates: a richer per-player
shape than a single global (μ, α) NegBin can express; blending the empirical baseline directly into
the model rather than treating it only as a comparison point; or a properly-specified
semi-parametric hybrid), using the SAME tuning/lock/freeze discipline established here, before any
new prop family is started.

---

## Final Questions

**WAS THE MODEL SPECIFICATION FROZEN BEFORE TRUE EVALUATION?** YES

**WERE 2024-25 OR 2025-26 OUTCOMES USED FOR FEATURE SELECTION?** NO

**WERE 2024-25 OR 2025-26 OUTCOMES USED FOR HYPERPARAMETER SELECTION?** NO

**WERE 2024-25 OR 2025-26 OUTCOMES USED TO FIT CALIBRATION?** NO

**WERE UPSTREAM MODEL OUTPUTS GENUINELY OUT-OF-FOLD / PRE-TARGET?** NOT USED

**WAS HEADLINE BOOTSTRAP GAME-CLUSTERED?** YES

**IS THE EVALUATION CONDITIONAL ON ACTUAL PARTICIPATION?** YES — the corpus contains only rows for
players who actually appeared; there is no pregame scratch/injury candidate-pool data source to
evaluate the projected-active gate against the full pregame population (Section C).

**DID ANY MATERIAL POST-LOCK MODEL CHANGE OCCUR?** NO

**IS THE POINTS MODEL VALIDATED?** PARTIAL

**IS 1+ POINT VALIDATED?** NO — beats baselines A/B/C robustly but loses to baseline D across both
clustering schemes (Section AH/AI); fails Adoption Standard item 1 ("beats simple baselines").

**IS 2+ POINTS VALIDATED?** NO — same baseline-D pattern holds at 2+ (Brier 0.07735 vs. D's
0.07679).

**IS 3+ POINTS VALIDATED?** INSUFFICIENT DATA (Section AF — fails only the per-confidence-bucket
support check, driven by the LOW bucket's single 3+ event).

**DO GOALS AND ASSISTS SHOW MATERIAL DEPENDENCE?** YES in aggregate (1.24× lift, Section D) — but
essentially NO within-player once player identity is controlled for (1.015× lift, Section E); the
aggregate effect is mostly player heterogeneity, not true joint dependence.

**DID TOI / ROLE ADD VALUE IN THE TUNING PERIOD?** NO

**DID PP ROLE ADD VALUE IN THE TUNING PERIOD?** YES

**DID H2H ADD VALUE IN THE TUNING PERIOD?** YES

**DID RECENT FORM ADD VALUE IN THE TUNING PERIOD?** NO

**DID TEAM CONTEXT ADD VALUE IN THE TUNING PERIOD?** YES

**DID OPPONENT CONTEXT ADD VALUE IN THE TUNING PERIOD?** YES

**WAS UPSTREAM SOG USED?** NO

**WAS UPSTREAM ASSISTS USED?** NO

**DO HIGH-CONFIDENCE PREDICTIONS OUTPERFORM LOW-CONFIDENCE PREDICTIONS?** YES (HIGH skill 0.079 vs.
LOW skill -0.036 at 1+) — though the LOW bucket's own negative skill score at n=1,140 is itself a
genuine open finding, not explained away (Section AJ).

**CAN POINTS FEED THE EXISTING COMMON PROP-PRICING INTERFACE?** YES — `PropPrediction` is fully
prop-agnostic; `market_type="POINTS"` populates it with no interface changes (verified, test in
`tests/test_player_points_model.py`).

**WAS THE PRODUCTION NHL WIN MODEL CHANGED?** NO

**CURRENT FULL TEST RESULT?** 791 / 791

**WHAT SHOULD THE NEXT SINGLE DEVELOPMENT SLICE BE?** A dedicated POINTS redesign cycle targeting
the baseline-D finding above (Section AR) — not Goals, not a new UX build, and not a prop chosen
just to raise the validated count.
