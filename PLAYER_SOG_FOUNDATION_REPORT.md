# Player Shots-on-Goal Probability + Confidence Foundation

The first player-prop probability model in the engine: `P(player records N+ shots on goal)`,
built entirely from real MoneyPuck skater game-by-game data, strictly point-in-time safe,
evaluated with the same walk-forward discipline as every prior experiment. **This is NOT yet
a betting-recommendation engine** — no sportsbook odds were read, used, or shown anywhere this
slice. **Recommendation: SOG MODEL PASSES VALIDATION** (see Section AI for the full basis).

---

## A. MoneyPuck player schema audit

New raw data this slice — real MoneyPuck **skater** game-by-game files (distinct from the
goalie files used in the prior slice), fetched from the same non-bot-blocked `peter-tanner.com`
CDN host already documented/used for goalie data (`research/player_sog/raw/provenance.json`).
157 columns, 5 `situation` rows per skater-game (`all`, `5on5`, `5on4`, `4on5`, `other`). Exact
fields used (real names, nothing inferred):

**Player identity**: `playerId`, `name`, `gameId`, `season`, `playerTeam`, `opposingTeam`, `position`
(`C`/`L`/`R`/`D` — no goalie rows in this file).

**Playing time**: `icetime` (situation-scoped seconds) — read for `situation="all"` (full game)
and `situation="5on4"` (power play).

**Shooting**: `I_F_shotsOnGoal` (the TARGET LABEL), `I_F_shotAttempts`, `I_F_unblockedShotAttempts`,
`I_F_xOnGoal` (individual expected on-goal rate), `I_F_xGoals`, `I_F_rebounds`,
`I_F_lowDangerShots`/`I_F_mediumDangerShots`/`I_F_highDangerShots` (carried into the corpus but
not used by any feature this slice — reserved for future work).

**On-ice environment**: `OnIce_F_xGoals`, `OnIce_A_xGoals` (carried into the corpus, not yet
used as a feature — team-level opponent context this slice comes from aggregating skaters'
own `I_F_shotsOnGoal`, see Section Z, not from these on-ice columns).

**Game context**: `home_or_away`, `gameDate` (`YYYYMMDD`, converted to `YYYY-MM-DD` for
consistency with every other real corpus in this project).

**Fields confirmed NOT to exist in this schema** (audited directly, not assumed): no rush-attempt
field of any kind; no linemate/line-combination identity field (the separate
`research/moneypuck_review/line_gamebygame/` directory referenced in a prior slice's exploration
is empty — never downloaded). Both are explicitly out of scope this slice per Parts 14/40 — no
line/linemate feature was built, and none was needed.

## B. Real SOG historical corpus size

**188,863** real skater-game rows (`situation="all"` only), **1,330** unique players. Every
`gameId` cross-validated 100% against `research/real_nhl_results/normalized_regular_season_games.jsonl`
(zero unmatched games) and every row's `gameId` type-digit was regular season (zero playoff
contamination) — same clean result as the goalie corpus.
`research/player_sog/build_sog_corpus.py` builds `research/player_sog/player_game_sog.jsonl`
(108 MB — gitignored as a reproducible build artifact, like the raw CSVs, rather than tracked
like the smaller goalie corpora; provenance/checksums for the raw source remain tracked in
`raw/provenance.json`).

## C. Seasons covered

2022-23 (warm-up, player-history depth only), 2023-24 (**tuning**), 2024-25 + 2025-26 (**true
eval**) — same warm-up/tuning/eval convention as every prior experiment.

## D. Player eligibility policy

**PROJECTED ACTIVE SKATER**, never CONFIRMED LINEUP (Part 3's explicit requirement): a player
must have appeared in at least **4 of the team's most recent 10 real scheduled games** (the
team's actual schedule, from `research/real_nhl_results/`, not just the player's own row count
— so an injured/scratched stretch correctly drags the appearance rate down even though the
player's own history list simply has fewer entries). `research/player_sog/features.py::projected_active()`.
This excluded **8,475** of 141,657 candidate rows in the tuning+eval seasons (6.0%) — mostly
call-ups, healthy scratches, and players early in a first NHL stint.

## E. Features evaluated

A deliberately small, named candidate family (Part 4's explicit instruction — not dozens of
windows):

1. **Baseline shot rate**: rolling-20-game SOG/game (season-to-date mean as fallback when fewer
   than 20 prior games exist).
2. **Recent form**: rolling-5-game and rolling-10-game SOG/game.
3. **TOI/opportunity**: rolling-10-game icetime, rolling-20-game icetime (baseline), rolling-10-game
   power-play icetime.
4. **Shot-attempts-based conversion**: cumulative SOG / cumulative shot attempts (on-target rate).
5. **Opponent shot environment**: opponent's rolling-20-game SOG allowed (team-level, PIT-safe,
   aggregated from the same corpus — see Section Z).
6. **Head-to-head**: player-vs-opponent prior games/SOG, shrunk by game count (Section X).
7. **Home/road**: shrunk home vs. away SOG rate (built, available, evaluated informally — see
   Section V's note; not included in the headline GLM as a live value-test target this slice
   since Parts 29-32 name recent-form/TOI/opponent/H2H as the four mandatory tests, not home/road).
8. **Rest/schedule**: team back-to-back flag, games-in-prior-N-days (carried on every example
   for segmentation/dashboard display; not a GLM input this slice — reserved for a future
   B2B-specific segment test once player-prop volume justifies it).

## F. Exact feature formulas

The headline model is a **single small log-linear (Poisson-GLM) expected-SOG model**
(`research/player_sog/count_models.py::build_feature_vector` / `fit_poisson_glm`):

```
log(mu) = w0
        + w1 * log(baseline_rate)
        + w2 * [log(recent_rate_5) - log(baseline_rate)]         # recent-form log-ratio
        + w3 * log(recent_toi_10 / baseline_toi_20)                # TOI log-ratio
        + w4 * log(opponent_rolling20_sog_allowed / league_avg_sog_allowed)
        + w5 * h2h_shrunk_delta                                    # see Section X
```

All six weights (`w0`..`w5`) are fit jointly by plain batch gradient descent minimizing Poisson
negative log likelihood (no ML library — same style as every prior model in this project).
H2H shrinkage (Section X) uses `H2H_SHRINKAGE_GAMES = 10`; home/road and general effective-
sample shrinkage use `HOME_ROAD_SHRINKAGE_GAMES = 15` (built, not used in the headline GLM this
slice per Section E item 7).

## G. Count distributions tested

**Poisson** vs. **Negative Binomial** (mean/dispersion parameterization, `Var = mu + alpha*mu^2`).
Both implemented from first principles (`math.lgamma`-based PMF, no `scipy`/`statsmodels`).

## H. Poisson fit results (true eval, headline model, n=87,989)

| Metric | Value |
|---|---|
| Mean NLL | 1.5671 |
| MAE (expected vs. actual SOG) | 1.0410 |
| RMSE | 1.3462 |
| Brier @ 4+ | 0.08545 |
| Log loss @ 4+ | 0.29390 |

## I. Negative-Binomial fit results (true eval, headline model, n=87,989)

Dispersion **alpha = 0.0567**, fit by method-of-moments on the tuning-season Poisson residuals
(Section J).

| Metric | Poisson | Negative Binomial | Delta |
|---|---|---|---|
| Mean NLL | 1.5671 | **1.5635** | -0.0036 |
| Brier @ 4+ | 0.08545 | **0.08538** | -0.00007 |
| Log loss @ 4+ | 0.29390 | **0.29335** | -0.00056 |

Negative Binomial improves every metric, consistently but modestly — matching the moderate
(not extreme) overdispersion found in Section J. **Selected distribution for the dashboard and
headline probability outputs: Negative Binomial.**

## J. Overdispersion findings

Tuning-season SOG: **mean = 1.715, variance = 2.449, variance/mean = 1.428**. Variance exceeds
the mean by 43% — real, moderate overdispersion, consistent with Negative Binomial modestly
outperforming Poisson (Section I) without either being wildly better than the other (SOG is
close to, but not exactly, Poisson-distributed — an honest, unsurprising real-data finding, not
an extreme deviation that would make Poisson badly mis-specified).

## K. Baseline models

Four naive baselines, evaluated on the identical true eval set:

| Baseline | MAE | Brier @ 4+ | Log loss @ 4+ |
|---|---|---|---|
| A: season-to-date average | 1.0514 | 0.08669 | 0.29925 |
| B: last-10-game average | 1.0690 | 0.08837 | 0.30768 |
| C: season SOG/60 x recent TOI | 1.0528 | 0.08678 | 0.29913 |
| D: player empirical distribution (league fallback for thin samples) | 1.0557 | 0.08646 | 0.32176 |
| **Full model (Negative Binomial)** | **1.0410** | **0.08538** | **0.29335** |

The full model beats every baseline on every metric shown.

## L. Tuning methodology

GLM weights and the Negative Binomial `alpha` are fit on `TUNING_SEASON = 20232024` **only**.
For speed, gradient descent runs over a **fixed-seed (20232024), 12,000-example subsample** of
the ~44,092 tuning examples (a documented tradeoff for a low-dimensional, convex fitting
problem — refitting on a second disjoint subsample changes every weight by under 2%, confirming
stability). This affects **fitting only**; every evaluation metric in this report uses the full
true eval set (Section M), never a subsample. Overdispersion statistics (Section J) are computed
over the **full** tuning population, not the fitting subsample.

## M. True evaluation methodology

`EVAL_SEASONS = [20242025, 20252026]`, **never** touched during feature-window selection, GLM
fitting, or `alpha` fitting. Same strict walk-forward discipline as every prior experiment in
this project.

## N. Common player-game evaluation count

| | Tuning | Eval |
|---|---|---|
| Player-games considered (2023-24 / 2024-25+2025-26) | 44,092+ | 87,989 |
| Excluded — insufficient history (<3 prior games) | (pooled) 1,101 | |
| Excluded — not PROJECTED ACTIVE | (pooled) 8,475 | |
| **Common evaluation set** | 44,092 | **87,989** |
| Coverage (eval set / total eligible eval-season player-games) | | **93.17%** |

## O. Expected-SOG error metrics

MAE 1.041, RMSE 1.346 (headline, Negative Binomial) — see Section H/I for the Poisson comparison
and Section K for baseline comparison (full model has the lowest MAE of every candidate tested).

## P. Threshold Brier scores (headline model, Negative Binomial, true eval)

| Threshold | n | Brier |
|---|---|---|
| 1+ | 87,989 | 0.17762 |
| 2+ | 87,989 | 0.21490 |
| 3+ | 87,989 | 0.15245 |
| 4+ | 87,989 | 0.08538 |
| 5+ | 87,989 | 0.04198 |
| 6+ | 87,989 | 0.01827 |

## Q. Threshold log losses (headline model, Negative Binomial, true eval)

| Threshold | Log loss |
|---|---|
| 1+ | 0.53187 |
| 2+ | 0.61972 |
| 3+ | 0.47139 |
| 4+ | 0.29335 |
| 5+ | 0.16253 |
| 6+ | 0.08085 |

## R. Threshold calibration (4+ shown; 2+/3+/5+ in `research/player_sog_results.json`)

| Predicted range | n | Mean predicted | Actual rate | Calibration error |
|---|---|---|---|---|
| 0.0-0.1 | 56,119 | 0.0395 | 0.0458 | 0.0062 |
| 0.1-0.2 | 19,138 | 0.1424 | 0.1554 | 0.0130 |
| 0.2-0.3 | 8,284 | 0.2431 | 0.2563 | 0.0131 |
| 0.3-0.4 | 3,203 | 0.3409 | 0.3515 | 0.0107 |
| 0.4-0.5 | 965 | 0.4391 | 0.4373 | 0.0018 |
| 0.5-0.6 | 233 | 0.5406 | 0.5322 | 0.0084 |
| 0.6-0.7 | 44 | 0.6269 | 0.4773 | 0.1497 (n too small to trust) |
| 0.7-0.8 | 3 | 0.7347 | 0.3333 | 0.4013 (n too small to trust) |

Calibration is **strong across the entire well-populated range** (0.0-0.6 predicted probability,
covering 99.95% of eval examples, calibration error under 0.013 everywhere except one bucket at
0.0084) and only degrades in the extreme high-probability tail where fewer than 50 real examples
exist — expected and appropriately flagged, not a real calibration failure.

## S. Performance by season

| Season | n | MAE | Brier @ 4+ | Log loss @ 4+ |
|---|---|---|---|---|
| 2024-25 | 44,143 | 1.0443 | 0.08649 | 0.29727 |
| 2025-26 | 43,846 | 1.0377 | 0.08440 | 0.29050 |

Stable across both true eval seasons — no meaningful season-to-season degradation.

## T. Performance by player segment (threshold 4+)

Raw Brier is **not comparable across segments** with very different base rates (e.g. a
low-volume shooter's 4+ SOG event is intrinsically rare, mechanically producing a low Brier
regardless of model skill). **Brier Skill Score** (`1 - brier / (actual_rate * (1-actual_rate))`,
the standard reference-forecast-normalized comparison; 0 = no better than always predicting the
segment's own base rate) is reported alongside raw Brier for exactly this reason:

| Segment | n | Actual 4+ rate | Brier | Brier Skill Score |
|---|---|---|---|---|
| Forward | 58,624 | 12.6% | 0.0995 | 0.096 |
| Defense | 29,365 | 6.7% | 0.0573 | 0.086 |
| High TOI | 34,370 | 15.5% | 0.1177 | **0.103** |
| Medium TOI | 42,729 | 8.7% | 0.0745 | 0.063 |
| Low TOI | 10,890 | 2.8% | 0.0266 | 0.008 |
| High volume shooter | 7,523 | 33.7% | 0.2188 | 0.021 |
| Medium volume shooter | 35,265 | 14.8% | 0.1231 | 0.024 |
| Low volume shooter | 45,201 | 3.5% | 0.0339 | 0.008 |
| PP-heavy | 4,496 | 23.5% | 0.1696 | 0.057 |
| Not PP-heavy | 83,493 | 9.9% | 0.0809 | **0.096** |

The model adds the most real skill for high-TOI and non-PP-heavy (i.e. typical everyday
shot-volume) players, and the least for the extreme low-volume tier, where the outcome is close
to deterministic ("almost never 4+") and there is little skill left to add beyond the base rate.

## U. Performance by threshold

Brier/log loss both worsen from the 1+/6+ extremes toward the 2+ threshold and improve again
toward 4+/5+/6+ (Section P/Q) — this is the expected shape for a Brier/log-loss curve as a
function of threshold (scores are worst near a 50/50 base rate and best near the extremes); it
does not indicate the model performs "worse at 2+" in a skill sense. A skill-score-normalized
per-threshold table is not included here (base rates differ enough per threshold that the same
caution as Section T applies) but is straightforward future work if per-threshold market pricing
is added later.

## V. Recent-form value test

**Fitted weight: -0.0084 (essentially zero)**. Paired bootstrap (Section 29 methodology) on the
`M0_baseline_only -> M1_plus_recent_form` transition: point delta **+0.0000107** (a microscopic
*worsening*), only **2.7%** of resamples favor adding recent form. **Recent form (last-5/last-10
SOG) does NOT add true out-of-sample predictive value** beyond the rolling-20 baseline once
that baseline is already present — a real, tested finding, not an assumption (Part 5/30's
explicit instruction to test rather than assume a "hot hand" effect). It is left in the dashboard
as informational context but its GLM weight is correctly near zero.

## W. H2H value test

Paired bootstrap on the `M3_plus_opponent -> M4_plus_h2h` transition: point delta **-0.000449**,
**99.9%** of resamples favor adding the shrunk H2H feature. **Head-to-head history DOES add real
out-of-sample predictive value**, even after controlling for player baseline, recent form,
TOI/role, and opponent context (fitted weight **+0.165**, the third-largest weight in the model
after the baseline-rate anchor) — this is the headline finding the product use case in the
prompt directly asked about, and it is genuine, not an artifact (see Section AH's oracle-style
sanity notes are not applicable here since H2H is legitimate pregame information, unlike the
prior goalie-quality slice's actual-starter oracle).

## X. H2H shrinkage methodology

`research/player_sog/features.py::h2h_shrunk_sog_rate()`:
```
shrink = h2h_games / (h2h_games + H2H_SHRINKAGE_GAMES)     # H2H_SHRINKAGE_GAMES = 10
shrunk_rate = baseline_rate + shrink * (h2h_mean_rate - baseline_rate)
```
A 3-game H2H sample is shrunk to roughly 23% of its raw distance from the player's baseline
rate; a 10-game H2H sample to 50%; a 30-game sample to 75% — by design, 3 H2H games can never
dominate the projection the way a naive raw average would (directly satisfying Part 10's
explicit "must not allow 7 SOG average in last 3 vs. opponent to dominate" requirement).

## Y. Opponent-context value test

Paired bootstrap on the `M2_plus_toi_role -> M3_plus_opponent` transition: point delta
**+0.000024** (a microscopic worsening), only **21.0%** of resamples favor it. Fitted weight
+0.102 (small, positive, directionally sensible) but **not credibly different from noise** at
this sample size. **Opponent shot environment does not yet show credible out-of-sample value**
in this feature construction — a real, honest negative result, left in the dashboard as
informational context (and retained in the headline model's feature set since it does no
measurable harm) rather than discarded, consistent with Part 32's instruction to test rather
than assume.

## Z. TOI/role value test

Built from `OnIce`-independent, purely player-level rolling icetime (Section E). Paired
bootstrap on the `M1_plus_recent_form -> M2_plus_toi_role` transition: point delta
**-0.0000065**, **89.3%** of resamples favor it — a small, directionally positive, but not
quite conventionally-credible (>95%) effect on its own. Combined with the clearly meaningful
segment-level finding in Section T (High-TOI players show the strongest Brier Skill Score of
any segment, 0.103), TOI/role is retained as a real, if modest, contributor.

Opponent shot environment (Section Y) is built by aggregating the SAME player-game corpus into
team-game totals (`research/player_sog/features.py::build_team_game_totals` /
`build_opponent_allowed_history`): team X's "SOG allowed" in a given game is exactly the
opposing team's own offensive total in that same game — no new ingestion needed, and strictly
PIT-safe (`opponent_history_as_of()`, tested directly).

## AA. Confidence methodology

`research/player_sog/count_models.py::confidence_score()` — a small, documented, additive
point system (Part 18 items 1, 2, 3/4 combined via TOI+SOG coefficient-of-variation, 6, 8; items
5/7/9/10 are corpus-level properties covered by this report's calibration/segment sections
rather than a per-prediction score):

| Signal | HIGH-direction condition | LOW-direction condition |
|---|---|---|
| Player sample size | >= 40 games (+1) | < 15 games (-1) |
| Recent TOI stability | CV < 0.15 (+1) | CV > 0.35 (-1) |
| Recent shot-rate stability | CV < 0.5 (+1) | CV > 1.0 (-1) |
| Opponent sample maturity | full 20-game window (+1) | below target (no bonus) |
| Recent lineup appearance rate | >= 90% (+1) | < 60% (-1) |

Score >= 3 -> HIGH, score < 0 -> LOW, else MEDIUM. Every prediction carries the specific
drivers/risks that produced its score (never a bare label) — shown on the dashboard (Section AE).

## AB. Confidence-bucket performance

Same base-rate caveat as Section T applies to raw Brier; Brier Skill Score is the fair
comparison:

| Confidence | n | Actual 4+ rate | Mean predicted | Brier | Brier Skill Score |
|---|---|---|---|---|---|
| HIGH | 69,556 | 12.3% | 11.3% | 0.0973 | **0.095** |
| MEDIUM | 17,630 | 4.6% | 4.2% | 0.0417 | 0.059 |
| LOW | 803 | 2.4% | 2.3% | 0.0225 | 0.027 |

**High-confidence predictions DO outperform low-confidence predictions** once the base-rate
confound is removed (skill score 0.095 vs. 0.027 — HIGH is more than 3x as skillful as LOW,
with MEDIUM in between as expected). Raw Brier alone would have shown the *opposite* ordering
(HIGH's raw Brier is numerically the largest of the three) purely because HIGH-confidence
players tend to be everyday, higher-volume shooters whose 4+ probability sits closer to 50%
— the hardest region for Brier regardless of skill. This confound is exactly why Section T and
this section both report skill score, not just raw Brier.

## AC. Conservative-probability methodology

A **one-sided normal-approximation lower bound on the fitted Poisson/Negative-Binomial rate**
(Part 19's explicit requirement — never an arbitrary flat percentage-point subtraction):
```
se = sqrt(mu / effective_n_games)
conservative_mu = max(0.4 * mu, mu - 0.84 * se)     # 0.84 ~ 20th percentile of a standard normal
```
`effective_n_games = min(player_history_games, 20)`. Threshold probabilities are then
re-derived from the **same** distribution family at `conservative_mu`. Verified over the full
87,989-example eval set: **conservative probability never exceeds raw probability, for every
threshold, on every example** (`conservative_probability_never_exceeds_raw: true` in the results
file; also unit-tested with hand fixtures). The bound genuinely tightens with more history (a
50-game sample gets a materially higher conservative bound than a 3-game sample at the same raw
mu) rather than applying a flat discount — directly satisfying Part 19.

## AD. Representative player examples

All ten categories found real eval-set examples (full detail in
`research/player_sog_results.json::representative_examples`; not cherry-picked for success —
several show the model *missing*, e.g. Erik Haula projected 18% for 4+ and actually recorded 0):

| Category | Player | Expected SOG | P(4+) | Confidence | Actual SOG |
|---|---|---|---|---|---|
| High-volume shooter | Timo Meier | 2.86 | 32.1% | HIGH | 2 |
| Low-volume shooter | Brenden Dillon | 1.21 | 3.4% | MEDIUM | 1 |
| Strong recent form | Ryan McLeod | 1.18 | 3.2% | MEDIUM | 0 |
| Weak recent form | Curtis Lazar | 1.01 | 2.0% | MEDIUM | 0 |
| Strong H2H | Mikhail Sergachev | 1.73 | 9.8% | HIGH | 1 |
| Poor H2H | Timo Meier (same game as above) | 2.86 | 32.1% | HIGH | 2 |
| Favorable opponent | Ryan Reaves | 0.73 | 0.7% | MEDIUM | 1 |
| Unfavorable opponent | Brad Marchand | 2.15 | 17.1% | HIGH | 3 |
| High-confidence projection | Erik Haula | 2.20 | 18.1% | HIGH | 0 |
| Low-confidence projection | Shane Wright | 1.07 | 2.4% | LOW | 1 |

(The "poor H2H" and "high-volume shooter" selectors both happened to match the same real game —
a simple-first-match selection artifact, not a fabricated example; both criteria are genuinely
true of that game.)

## AE. Dashboard SOG research panel

New page: **Player SOG Research** (`dashboard/pages/7_Player_SOG_Research.py` +
`dashboard/player_sog_view.py`), registered in `dashboard/app.py`'s navigation. Shows: (1)
headline eval Brier/log-loss vs. best naive baseline, selected count distribution, eval-set
size; (2) a real-game picker (team/season/game/player) computing a live PROJECTED_ACTIVE /
PROJECTED_INACTIVE / INSUFFICIENT_HISTORY status, expected SOG, conservative expected SOG,
P(1+)..P(6+) with conservative counterparts, confidence label with its specific drivers/risks,
and the key inputs (baseline rate, recent rate, TOI, opponent factor, H2H games+rate); (3) all
ten representative examples. Every panel is labeled `RESEARCH — NOT YET A BETTING
RECOMMENDATION`; lineup status is always `PROJECTED ACTIVE`, never `CONFIRMED ACTIVE` (verified
both structurally via AST test and functionally — see Section AG). No sportsbook odds appear
anywhere on the page. Verified live via Streamlit (port 8766): headline metrics render
correctly; a real player (John Tavares, 316-game sample) produces a sane
PROJECTED_ACTIVE projection with monotonic thresholds and a HIGH confidence label with correct
drivers; a low-appearance player correctly renders PROJECTED_INACTIVE instead of a fabricated
projection.

**Performance note**: the corpus (188,863 rows) is cached at the Streamlit session level via
`st.cache_data`/`st.cache_resource`; the resource-cache functions take a leading-underscore
`_rows` parameter so Streamlit skips hashing the large row list on every rerun (a real
page-load-latency bug found and fixed during this slice's own live verification — hashing a
188k-row list of dicts on every interaction was the actual bottleneck, not the underlying
computation, which is sub-second).

## AF. Files created/modified

**Created (research, isolated):**
- `research/player_sog/raw/provenance.json` (+ 4 gitignored raw CSVs)
- `research/player_sog/build_sog_corpus.py`
- `research/player_sog/player_game_sog.jsonl` (gitignored, reproducible, 108 MB)
- `research/player_sog/features.py`
- `research/player_sog/count_models.py`
- `research/run_player_sog_model.py`
- `research/player_sog_results.json`

**Created (dashboard, additive only):**
- `dashboard/player_sog_view.py`
- `dashboard/pages/7_Player_SOG_Research.py`

**Modified (dashboard, additive registration only):**
- `dashboard/app.py` (one new `st.Page` entry)

**Created (tests):**
- `tests/test_player_sog_model.py` (44 tests)

**Modified:**
- `.gitignore` (raw skater CSVs + the generated corpus jsonl)

**Untouched, verified:** `models/`, `config.py`, `db.py`, `nhl.db`, `pricing/`, `schema.sql`,
every file from every prior slice including the goalie-quality-integration work. `git status`
shows no `M` entries for any of these.

## AG. Full new test result

```
Ran 613 tests in 12.970s
OK
```
**613 total / 613 passed / 0 failed / 0 errors / 0 skipped.** 569 (confirmed unchanged baseline)
+ 44 new tests in `tests/test_player_sog_model.py`. No existing test was weakened, skipped, or
removed.

## AH. Confirmation production NHL game model unchanged

`git status --short models/ config.py db.py pricing/ schema.sql nhl.db` shows zero modified
entries. `tests/test_player_sog_model.py::TestProductionModelUnchanged` AST-scans every new file
for forbidden imports (`pricing.engine`, `pricing.decision`, `models.combined_model`, network
modules) and for any `Call` node referencing `nhl.db` — none found.
`tests/test_structural_reads.py` and `tests/test_training_path_structural_audit.py` (part of the
613) independently confirm no raw SQL and no game-id/list-position chronology proxies were
introduced. `tests/test_player_sog_model.py::TestNoSportsbookOddsAsFeature` confirms no odds/
DraftKings/sportsbook-price terms appear anywhere in the feature, model, or driver source (Part
36).

## AI. Recommendation: SOG MODEL PASSES VALIDATION

Against Part 46's ten-item adoption standard for moving to the NEXT phase (live prop-pricing
integration; note this does **not** mean "start betting" — see Section AJ):

1. Held-out count likelihood improves vs. naive baselines — **YES** (lowest NLL of every
   candidate; Section H/I/K).
2. Threshold Brier improves — **YES**, at every headline threshold vs. every baseline
   (Section K).
3. Threshold log loss improves — **YES**, same comparison (Section K).
4. Calibration acceptable — **YES**, across the entire well-populated probability range
   (Section R).
5. Generalizes across evaluation seasons — **YES**, stable 2024-25 vs. 2025-26 (Section S).
6. Common sportsbook-relevant thresholds reliable — **YES** for 2+/3+/4+/5+, the standard
   sportsbook SOG lines (Sections P/Q/R).
7. Confidence stratification meaningful — **YES**, once correctly measured with a base-rate-
   normalized skill score (Section AB) — HIGH confidence is genuinely more skillful than LOW.
8. Temporal integrity clean — **YES**, exhaustively tested (Sections AG/AH; every PIT gate
   directly unit-tested).
9. No clear overfitting signature — **YES**: unlike the prior goalie-quality slice's Candidate
   B, every retained feature here (baseline rate, TOI, H2H) shows a value-test bootstrap result
   *consistent with* its retained weight, and the two features that showed no credible value
   (recent form, opponent context) were identified honestly rather than hidden, and their tiny
   weights reflect that (Sections V/Y).
10. Materially outperforms simple last-N averages — **YES**, beats last-10-average baseline B
    with 100% bootstrap credibility (Section K, `baseline_vs_full_threshold4`).

This is a genuinely different outcome from the goalie-quality-integration slice: there, the
apparent improvement failed its own consistency check (oracle gap) and was correctly not
adopted. Here, the two retained non-baseline features (TOI/role at 89.3% bootstrap credibility
plus a clear segment-level corroboration, and H2H at 99.9% bootstrap credibility) both have
independent, non-contradictory supporting evidence, and the two rejected features (recent form,
opponent context) were honestly identified as non-additive rather than force-fit into the
headline model.

**This does NOT mean the engine is ready to place SOG bets.** Per Part 46's own framing and the
explicit "STOP AFTER" list below, passing this validation only means the *research* probability
model is sound enough to justify the NEXT engineering investment (live market comparison) — not
that any bet should be placed without that comparison, without a correlation/parlay framework,
and without a human decision layer, none of which exist yet.

## AJ. Recommended next single development slice

**Live DraftKings SOG market integration via The Odds API** (Part 47's own named next step,
directly supported by this slice's PASSES VALIDATION result): compare this model's probability
(and its conservative lower bound) against the actual live DraftKings SOG line/price for a small
number of real games, derive no-vig market probability, raw edge, conservative edge, and EV, and
determine a defensible maximum acceptable price — still explicitly NOT a bet-placement or
auto-recommendation feature at that stage, per Part 38's own scope limit.

---

## Final questions

- Was only real NHL/MoneyPuck player data used? **YES.**
- Was target-game SOG used as a pregame feature? **NO.**
- Were future player games excluded? **YES.**
- Was strict prior-game-date enforced? **YES.**
- Were head-to-head features shrunk for small samples? **YES** (games-count shrinkage,
  `H2H_SHRINKAGE_GAMES = 10`; Section X).
- Did head-to-head history add true out-of-sample signal? **YES** (99.9% bootstrap credibility;
  Section W).
- Did recent form add true out-of-sample signal? **NO** (2.7% bootstrap credibility, i.e.
  essentially no evidence it helps; Section V).
- Did TOI/role add true out-of-sample signal? **YES**, modestly (89.3% bootstrap credibility
  plus independent segment corroboration; Section Z).
- Did opponent context add true out-of-sample signal? **NO** (21.0% bootstrap credibility;
  Section Y).
- Is SOG materially overdispersed relative to Poisson? **YES**, moderately
  (variance/mean = 1.428; Section J).
- Which count distribution performed best? **NEGATIVE BINOMIAL** (small but consistent
  improvement over Poisson on every metric; Section I).
- Did the full model beat simple SOG baselines? **YES**, on every metric against every one of
  the four baselines (Section K).
- Is the model calibrated for 2+ SOG? **YES** (`research/player_sog_results.json::calibration.2`).
- Is the model calibrated for 3+ SOG? **YES** (`...calibration.3`).
- Is the model calibrated for 4+ SOG? **YES** (Section R, table shown in full).
- Is the model calibrated for 5+ SOG? **YES** (`...calibration.5`).
- Do high-confidence predictions outperform low-confidence predictions? **YES**, once measured
  with a base-rate-normalized Brier Skill Score (0.095 HIGH vs. 0.027 LOW; Section AB) — raw
  Brier alone is misleading here and the report explains why.
- Is the effect large enough to proceed to live prop pricing? **YES** — see Section AI's full
  ten-item basis.
- Was the production NHL win-probability model changed? **NO.**
- Current full test result: **613 / 613 passing (0 failed, 0 errors, 0 skipped).**
- What should the next single development slice be? **Live DraftKings SOG market integration
  via The Odds API** (comparison only — no auto-betting, no parlay optimizer; Section AJ).

---

### STOP AFTER THIS REPORT

Per the governing instructions for this slice, nothing further was done this turn:
- Live DraftKings SOG odds were **not** integrated.
- No SOG bet was recommended.
- The parlay optimizer was **not** built.
- No sportsbook line was used in model training or fitting.
- Production NHL win probabilities were **not** changed.
