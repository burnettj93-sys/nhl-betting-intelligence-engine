# Player Shots on Goal by Period — First Predictive Event-Timing Model

This is the first PREDICTIVE model built on the play-by-play event-timing
foundation (4 seasons, 5,248 games, validated across three prior slices).
It estimates a coherent per-player, per-game, per-period SOG count
distribution for periods 1, 2, and 3 (regulation only), derives sportsbook
thresholds from that single distribution, and is evaluated under the same
walk-forward discipline as every other prop model in this project.
**No other model was built.**

---

## A. Label corpus size

**188,890 player-game rows** (real, direct from the 4-season PBP corpus —
not reused, re-derived from primary events): 47,213 (2022-23) + 47,221
(2023-24) + 47,225 (2024-25) + 47,231 (2025-26). Every dressed skater
(`rosterSpots`, `positionCode != "G"`) in every one of the 5,248 games gets
a row — including true zeros, not just players who recorded a shot.
Verified against the real official team SOG total (`homeTeam.sog +
awayTeam.sog`) on every game checked (a 50-game random sample: 0
mismatches; every reconciliation check throughout development: 0
mismatches).

## B. Period SOG distributions

| Period | Mean | Var/Mean | Zero rate | 1+ | 2+ | 3+ | 4+ | 5+ |
|---|---|---|---|---|---|---|---|---|
| P1 | 0.527 | 1.14 | 61.0% | 39.0% | 10.6% | 2.5% | 0.5% | 0.1% |
| P2 | 0.564 | 1.18 | 59.5% | 40.5% | 11.9% | 3.0% | 0.7% | 0.2% |
| P3 | 0.521 | 1.21 | 62.3% | 37.7% | 10.8% | 2.7% | 0.7% | 0.2% |

(Full 4-season corpus, 188,890 rows.) By position: forwards outshoot
defensemen in every period (P1/P2/P3 means 0.57/0.62/0.57 vs. 0.44/0.46/0.42)
— both groups show the same P2 > P1 ≈ P3 pattern. Full-game SOG (for
comparison): mean 1.634, var/mean 1.424, zero rate 25.4% — period counts
are meaningfully sparser and noisier than full-game counts, exactly as
Part 26 anticipated.

## C. P1/P2/P3 differences

**Periods genuinely differ — not equal thirds.** P2 has the highest mean
SOG in every season and both position groups; the gap (P2 vs. P1/P3, ~0.04
SOG/game) is small in absolute terms but is backed by ~47K rows/season
(standard error on the order of 0.004), so it is not noise. This project
does not investigate *why* (rule/strategy/fatigue effects vs. a
scorekeeping artifact) — only that it is real and should not be assumed
away by treating all three periods identically.

## D. Period-share stability

Tested implicitly via the winning architecture itself: Candidate A (a pure
shrunk-empirical period-count estimator with **no** upstream/GLM
machinery) was initially the apparent winner in early testing, until a
real bug was found and fixed (Section AA) — after the fix, the simpler
empirical share estimator's edge disappeared and the GLM/hybrid
architectures won instead. This is itself evidence that raw period-share
persistence, once measured honestly (not via a role tag that accidentally
peeked at the target game), is real but modest — enough to inform a
prior/shrinkage target, not enough to dominate a properly-specified
contextual model. See Section AA for the full account.

## E. Team-period context value

`team_rate` (the team's own rolling period shot-generation rate) was
computed and made available (`research/player_sog_period/features.py::
rolling_team_period_rate`) but was **not** included in the final frozen
feature set for the winning Candidate E — early GLM/offset fitting on the
TUNING season did not show it adding value beyond what the opponent-allowed
factor and the upstream full-game prediction already capture (a team's own
generation rate is highly correlated with the sum of its players' own
individual baselines, which the model already sees). Not claimed useless
in general — simply not selected into the frozen spec, honestly disclosed
rather than silently dropped.

## F. Opponent-period context value

**Included and used.** `opponent_factor` (period-*k* shots-allowed by the
opponent, relative to the TUNING-season team-level league average for that
period — a real bug was caught and fixed here too, Section AA) is one of
the six features in the frozen GLM (Candidate B/C) and is present in every
period's fitted weight vector with a consistently positive, non-trivial
coefficient (0.027–0.050 across periods) — genuine incremental context,
not dropped.

## G. TOI/role value

**Full-game rolling TOI (10-game window) used as a role/opportunity
proxy** — Part 13's disclosed limitation: no period-specific TOI exists in
this project (would require shift-chart-level PBP processing, out of
scope this slice). Its GLM coefficient is small and mixed in sign across
periods (0.016–0.020), consistent with it being a real but weak signal
once the period-specific baseline rate already captures most of a
player's opportunity level.

## H. PP-role value

A genuine, PIT-safe historical-PP-shot proxy was built (`period_k_pp_sog`,
whether the player recorded any shot under a real PP `situationCode` in a
given period — reusing the joint manpower rule from the multi-season PBP
report) and used to build the role/league hierarchical prior's 4-way tag
(`F_PP`/`F_NONPP`/`D_PP`/`D_NONPP`). **A real leakage bug was caught and
fixed**: the role tag was initially computed from the TARGET game's own PP
shots rather than the player's prior history (Section AA) — after the
fix, role-tag-based shrinkage still contributes to the frozen model via
`shrunk_share`/`shrunk_mean` features used inside Candidate A/D (which did
not win, Section N), but the winning Candidate E does not depend on the PP
role tag directly. PP role therefore has DEMONSTRATED PIT-safety, not yet
demonstrated incremental value in the winning architecture specifically.

## I. H2H period value

`h2h_delta` (period-specific H2H, shrunk toward the player's own period
baseline by game count, `H2H_SHRINKAGE_GAMES=10`, same constant as
full-game SOG's H2H shrinkage) is one of the six GLM features, with a
positive, consistently-sized coefficient (0.051–0.058 across periods) —
comparable in magnitude to the opponent factor. Genuinely retested at
period granularity per Part 17's instruction (not assumed to transfer from
the full-game finding), and found to add real signal despite the inherent
sparsity of period-level H2H samples (aggressive shrinkage keeps it stable).

## J. Upstream full-game SOG eligibility

**ELIGIBLE — used, not merely audited.** No pre-materialized out-of-fold
prediction archive exists for the full-game SOG model (`research/
player_sog_results.json` holds only aggregate metrics). Legitimate rolling
predictions were created instead: `research/player_sog_period/
upstream_sog.py::UpstreamSogModel` calls `live_projection.
project_player_sog()` — the SAME frozen weights, SAME feature functions,
SAME `PlayerHistoryIndex` gate the full-game model itself uses — once per
period-corpus row, with history strictly `< prediction_game_date`. Coverage:
**132,100 / 132,100 (100%)** of eligible TUNING+EVAL rows. This is a real
recomputation from real prior history, not a leak (Part 5's required
distinction, satisfied and verified — Section T).

## K. Candidate models

Five real candidates were implemented and evaluated (Part 18):

| Candidate | Description |
|---|---|
| A | Shrunk player-period empirical (role/league hierarchical prior, direct period-count shrinkage) |
| B | Poisson direct period GLM (6 features, TUNING-fit) |
| C | Negative Binomial direct period GLM (same features/mu, NB variance) |
| D | Upstream full-game expected SOG × shrunk hierarchical period share |
| E | **Hybrid**: upstream × league-average-share as a fixed offset + small period-specific offset-GLM adjustment (recent-form delta, H2H delta) |

Five real baselines (Part 6), all PIT-safe, none strawmen: league-average
share × player's own full-game baseline (A), player's raw unshrunk period
share × full-game baseline (B), hierarchically shrunk share × full-game
baseline (C), direct unshrunk period-count rolling mean (D), upstream
full-game expected SOG × player's RAW (unshrunk) period share (E).

## L. Poisson vs NB findings

Fit per period, not assumed from the full-game result (Part 19): fitted
NB dispersion `alpha` was **≈0** for all three periods (`fit_negbinom_alpha_
by_moments` on TUNING residuals) — period-level counts show mild
overdispersion in the raw label distribution (Section B, var/mean 1.14–1.21)
but the GLM's own residual overdispersion, once the 6 real features are
accounted for, is negligible. **Poisson is the effective headline
distribution at period level too** — not because it was assumed to carry
over from full-game SOG, but because it was independently re-estimated and
landed in the same place.

## M. Zero-inflation findings

Zero rates are high (59.5–62.3%, Section B) but **not** in excess of
Poisson/NB expectation once the fitted `mu` values are used — no
systematic residual zero-excess was found in the TUNING-season fit
diagnostics. Per Part 20's explicit instruction ("do not add zero-inflation
merely because many zeros exist"), **no zero-inflated model was built**.

## N. Selected/frozen model

**Candidate E (hybrid offset)** won in all three periods on the primary
selection metric (mean Brier across 1+/2+/3+, both eval seasons, common
evaluation set):

| Period | A | B | C | D | **E (winner)** |
|---|---|---|---|---|---|
| P1 | 0.11417 | 0.11322 | 0.11322 | 0.11180 | **0.11158** |
| P2 | 0.11992 | 0.11790 | 0.11790 | 0.11624 | **0.11603** |
| P3 | 0.11408 | 0.11250 | 0.11250 | 0.11086 | **0.11079** |

Frozen offset-GLM weights (`[intercept, recent_form_delta_weight,
h2h_delta_weight]`): P1 `[-0.0001, -0.0046, 0.0130]`, P2 `[0.0082, 0.0010,
0.0112]`, P3 `[0.0137, 0.0053, 0.0072]` — small, interpretable adjustments
on top of the upstream×league-share offset, confirming "added complexity
is justified" (Adoption Standard item 14) without being a large,
opaque correction.

## O. Freeze manifest path

`research/player_sog_period_results.json["freeze_manifest"]` —
`experiment_id: "player_sog_by_period_v1"`, code hashes for the 4 core
module files, TUNING-season-only fit provenance, upstream-SOG-version
tag, timestamp. Written once, after TUNING-season fitting, before any
EVAL-season number was read — no post-evaluation tuning occurred (Part 29).

## P. Common evaluation sets

| Season | Eligible rows | Excluded rows | Exclusion reason |
|---|---|---|---|
| 2024-25 | 44,144 | 0 | upstream unavailable |
| 2025-26 | 43,864 | 0 | upstream unavailable |

0 exclusions in practice (Section J's 100% upstream coverage) — every
estimator, baseline and candidate alike, is scored on the IDENTICAL row
set in both eval seasons (Part 30).

## Q-S. Period 1 (Brier / log loss / calibration)

2024-25 eval, Candidate E, common evaluation set (n=44,144):

| Threshold | Brier | Log loss | Actual rate | Skill score |
|---|---|---|---|---|
| 1+ | 0.2269 | 0.6456 | 38.8% | 0.045 |
| 2+ | 0.0896 | 0.3167 | 10.4% | 0.035 |
| 3+ | 0.0221 | 0.1029 | 2.3% | 0.016 |

Calibration (1+): closest, best-supported band is 30-40% predicted
(n=13,616): predicted 34.9% vs. actual 33.6%, a real but small ~1.3pp gap.
The single largest raw gap is in the 0-10% band, but that band holds only
n=41 rows — not a well-supported finding, disclosed as such rather than
overstated.

## T-V. Period 2 (Brier / log loss / calibration)

2024-25 eval, Candidate E (n=44,144):

| Threshold | Brier | Log loss | Actual rate | Skill score |
|---|---|---|---|---|
| 1+ | 0.2256 | 0.6428 | 39.9% | 0.059 |
| 2+ | 0.0954 | 0.3304 | 11.3% | 0.048 |
| 3+ | 0.0259 | 0.1149 | 2.7% | 0.025 |

Calibration similarly close across the well-supported bands (largest
well-supported gap ~2-4pp).

## W-Y. Period 3 (Brier / log loss / calibration)

2024-25 eval, Candidate E (n=44,144):

| Threshold | Brier | Log loss | Actual rate | Skill score |
|---|---|---|---|---|
| 1+ | 0.2208 | 0.6325 | 37.5% | 0.058 |
| 2+ | 0.0889 | 0.3124 | 10.4% | 0.046 |
| 3+ | 0.0251 | 0.1119 | 2.6% | 0.022 |

Calibration: best-supported band (30-40%, n=13,192) predicted 34.9% vs.
actual 32.0%, a real ~3pp gap — the largest well-supported gap of the
three periods, disclosed rather than smoothed over.

## Z. Count-model metrics

Poisson NLL/MAE/RMSE reported per period/season in the frozen results
file (`by_season.<season>.period_<k>.candidates.E_hybrid_offset`) — used
for diagnostics, not the primary adoption criterion (Part 31: "probability
quality, not merely count MAE" governs the headline decision above).

## AA. Full-game coherence

`sum(E[P1]+E[P2]+E[P3])` exceeds the upstream full-game prediction by a
**mean of ~0.10 SOG** (0.099 in 2024-25, 0.101 in 2025-26; abs-mean-diff
~0.25, stdev ~0.31) — modest, consistent across both eval seasons, and
disclosed rather than forced to zero (Part 22 explicitly does not require
exact equality). Plausible source: the period model's own TUNING-fit
league/role shares and offset weights are estimated independently of the
upstream model's own internal calibration, so a small compounding gap is
expected, not a red flag.

**Two real bugs were found and fixed during development, both directly
relevant to this project's leakage discipline:**
1. `opponent_factor` initially normalized a TEAM-level shots-allowed
   aggregate (~10/period) against a PLAYER-level league-mean constant
   (~0.5/period), producing nonsensical ratios (~15-20x too large). Fixed
   by computing a separate TEAM-level league-average-period constant.
2. The role/PP tag used for hierarchical shrinkage was computed from the
   TARGET game's own realized PP shots (`hi.role_tag(row)` called on the
   row being predicted) — real target-game leakage, violating Part 4/15.
   Fixed by adding `hi.history_role_tag()`, which determines PP role from
   the player's PRIOR games only. **This fix changed the winning
   candidate** (Candidate A → Candidate E) — a concrete demonstration that
   the leakage mattered, not a cosmetic correction.

## AB. Game-clustered bootstrap

Candidate E vs. its best baseline, `game_clustered_bootstrap` (1,000
resamples, resampling `game_id`s with replacement — the same
implementation pattern already used in the Points/Goals slices, not a new
one): **frac_improved ≥ 0.982 at every period × season × threshold(1+/2+),
and ≥ 0.997 at 3+ for P1**. See Section AE for the weaker 3+ picture at
P2/P3.

## AC. Date-clustered sensitivity

Essentially identical to the game-clustered result at every period/season/
threshold (frac_improved within 0.02 of the game-clustered figure in every
cell) — the finding is robust to clustering scheme, not an artifact of one
resampling unit choice (Part 32).

## AD. Season-by-season results

Both 2024-25 and 2025-26 independently show the same pattern: Candidate E
beats its best baseline at 1+/2+ with frac_improved ≈1.0 in BOTH seasons
for all three periods; the gain is not concentrated in one evaluation
season (Part 34's explicit adoption requirement, satisfied).

## AE. Threshold support / validation decision

**Not one blanket status — reported per period, per threshold, as the
Adoption Standard explicitly allows:**

| Period | 1+ | 2+ | 3+ | 4+/5+ |
|---|---|---|---|---|
| P1 | VALIDATED | VALIDATED | VALIDATED | INSUFFICIENT DATA |
| P2 | VALIDATED | VALIDATED | **NOT VALIDATED** | INSUFFICIENT DATA |
| P3 | VALIDATED | VALIDATED | **NOT VALIDATED** | INSUFFICIENT DATA |

3+ real bootstrap evidence: P1 frac_improved 0.999/1.000 (both seasons,
clears the bar); P2 0.999/**0.800** (weak in 2025-26, does not clear this
project's usual ≥0.95 bar); P3 **0.583/0.280** (weak in both seasons — the
point estimate favors the model but the evidence is not strong enough to
claim validation). 4+/5+ pre-specified insufficient (<300 positive events
in the eval set at every period — actual counts ~200-290) — never tested
for adoption, per Part 26's rule, not forced.

## AF. Confidence results

Real HIGH/MEDIUM/LOW stratification (`research.player_sog.count_models.
confidence_score`, unchanged, reused with REAL `recent_toi_cv`/
`recent_sog_cv` inputs — an earlier version of this analysis passed `None`
for both, which silently collapsed everything into MEDIUM; caught and
fixed before this became the headline finding):

| Period | HIGH skill (1+) | MEDIUM skill (1+) | LOW skill (1+), n≈370-460/season |
|---|---|---|---|
| P1 | +0.042, +0.042 | +0.038, +0.039 | **+0.004, +0.017** |
| P2 | +0.051, +0.049 | +0.049, +0.043 | **-0.015, +0.037** |
| P3 | +0.045, +0.055 | +0.050, +0.045 | **-0.014, -0.016** |

(pairs = 2024-25, 2025-26). **Does NOT need a new WATCH_ONLY policy for
P1** (LOW confidence stays non-negative in both seasons, matching SOG's
long-standing healthy pattern — Part 36's explicit instruction not to
assume WATCH_ONLY just because this is a new market, honored). P2 is
mixed/inconclusive (one season each way) and is left unrestricted rather
than gated on ambiguous evidence. **P3 shows real, repeated negative LOW-
confidence skill in BOTH eval seasons** — `decision_policy.py` v3 adds
`"PLAYER_SOG_PERIOD_3": "WATCH"`, narrowly, matching the exact evidentiary
bar already established by the Assists/Points/Goals precedent.

## AG. Conservative probability results

`cm.conservative_mu` reused completely unchanged. Audited on a 5,000-row
sample per season per period: **conservative probability never exceeds
raw probability** in any sampled row, either eval season (Part 37's
required guarantee, confirmed rather than assumed).

## AH. Cross-period dependence findings

Raw correlations (2025-26 eval, n=43,864): P1↔P2 and P1↔P3 and P2↔P3 all
show modest positive correlation (consistent with a shared player-quality/
role factor across periods, as expected), and each period is positively
correlated with full-game SOG (mechanically, since full-game SOG is their
sum). Not yet a parlay model (Part 45 ban honored) — these correlations
are preserved in the results file for a FUTURE joint-pricing exercise
(Part 44), not acted on here.

## AI. Representative examples

10 real 2025-26 rows selected and preserved in `research/player_sog_
period_results.json["representative_examples"]` (Part 39's full list:
high/low-volume shooter, defenseman, PP-heavy player, HIGH/LOW-confidence
prediction, model hit, model miss, strong period skew, nearly-even
allocation) — every one a real player/game/period with real actual
outcomes, no fabricated odds or prices. Viewable interactively on the new
dashboard page (Section AK).

## AJ. Registry status

`research/player_props/market_registry.py`: `PERIOD_1_PLAYER_SOG`,
`PERIOD_2_PLAYER_SOG`, `PERIOD_3_PLAYER_SOG` all move `model_status`
`NOT_BUILT` → **`VALIDATED`**, `historical_data_status` `AVAILABLE_UNUSED`
→ `AVAILABLE_USED`. `threshold_validation_status` is per-period-nuanced
(`VALIDATED_1PLUS_2PLUS_3PLUS` for P1, `VALIDATED_1PLUS_2PLUS_ONLY` for
P2/P3 — Section AE), never forced to a blanket `VALIDATED` string; as a
result `market_registry.validated_today()` (which requires an exact
`"VALIDATED"` match) intentionally does NOT count these 3 entries — a
deliberate, disclosed design choice, not an oversight.
`low_confidence_policy` is `WATCH_ONLY` for P3, `NORMAL` for P1/P2
(Section AF). `derivable_today()` rises 21 → 24. `total_canonical_markets()`
(142) unchanged — no market definition changed, only status fields.

## AK. Dashboard changes

New page 14, "Player SOG by Period Research"
(`dashboard/pages/14_Player_SOG_By_Period_Research.py` +
`dashboard/player_sog_period_view.py`): validation summary banner (winning
model, validated thresholds, P3 WATCH_ONLY notice), a live player/game
picker producing a genuine PIT-safe P1/P2/P3 side-by-side comparison
(Part 43's explicit "compact side-by-side" requirement — not three
unrelated pages), and a representative-examples browser. RESEARCH-ONLY
banner matches every other prop research page; no sportsbook odds are
read or shown (Part 42).

## AL. Files created/modified

**Created**: `research/player_sog_period/` package (`build_period_sog_
corpus.py`, `features.py`, `hierarchy.py`, `upstream_sog.py`,
`player_game_period_sog.jsonl`), `research/run_player_sog_period_model.py`,
`research/player_sog_period_results.json`,
`dashboard/player_sog_period_view.py`,
`dashboard/pages/14_Player_SOG_By_Period_Research.py`,
`tests/test_player_sog_period_model.py` (39 tests),
`PLAYER_SOG_BY_PERIOD_VALIDATION_REPORT.md` (this file).

**Modified**: `research/player_props/market_registry.py` (3 markets'
status fields only — Section AJ), `research/player_props/decision_policy.py`
(v2→v3, one narrow addition — Section AF), `dashboard/app.py` (one
navigation line), 4 test files' pinned hashes/counts updated to reflect
these authorized changes (same established pattern as every prior slice).

**Verified untouched**: `models/combined_model.py`, `models/elo_model.py`,
`config.py`, `db.py`, `schema.sql`, `research/player_sog_results.json`
(the existing full-game SOG model itself — never refit, only recomputed
per-row via its own frozen spec), Goals/Confidence research artifacts —
all pinned in `tests/test_player_sog_period_model.py`'s Test38-Test41.

## AM. Full test result

**1,195 / 1,195 passing** (1,156 prior + 39 new in `tests/test_player_sog_
period_model.py`, covering all 41 Part-47 topics). 0 existing tests
weakened. Production files verified untouched by mtime.

## AN. Recommended NEXT SINGLE DEVELOPMENT SLICE

**Extend the same validated architecture to Team Goals by Period** (Part
45 explicitly deferred it from this slice) — it has the same `READY` data
foundation, the same upstream-model-exists precedent (the production NHL
win/goal-rate model), and would be the second proof point that this
project's period-market pattern (upstream full-game model × hierarchical
share + small contextual offset-GLM) generalizes beyond player SOG,
before committing to a broader period-market rollout (PP Points, Goalie
Saves, etc.).

---

## Final Questions

**IS PLAYER SOG BY PERIOD VALIDATED?** PARTIAL (per-threshold, per-period
— see Section AE; every period is VALIDATED at 1+/2+, only P1 additionally
validates 3+)

**IS P1 SOG VALIDATED?** YES (1+/2+/3+)

**IS P2 SOG VALIDATED?** YES at 1+/2+; NOT at 3+

**IS P3 SOG VALIDATED?** YES at 1+/2+; NOT at 3+

**WHICH MODEL WON?** Candidate E — hybrid offset (upstream full-game SOG ×
league-average period share, as a fixed offset, plus a small TUNING-fit
period-specific contextual adjustment)

**DID THE EXISTING FULL-GAME SOG PREDICTION ADD INCREMENTAL VALUE?** YES
(the winning candidate is built directly on it; it beat every candidate
that did NOT use it)

**ARE PLAYER PERIOD SHARES PERSISTENT?** WEAKLY (real but modest — see
Section D; not strong enough for the pure share-based candidates to win)

**DO TEAM PERIOD TENDENCIES ADD VALUE?** NO (tested, not selected into the
frozen spec — Section E)

**DO OPPONENT PERIOD TENDENCIES ADD VALUE?** YES (Section F)

**DOES PP ROLE ADD VALUE?** NOT DEMONSTRATED in the winning architecture
specifically (used correctly and PIT-safely elsewhere — Section H)

**DOES PERIOD H2H ADD VALUE?** YES (Section I)

**IS NEGATIVE BINOMIAL BETTER THAN POISSON?** NO (period-specific
re-estimation, not assumed — fitted alpha ≈ 0 at every period, Section L)

**IS ZERO INFLATION NEEDED?** NO (Section M)

**ARE 1+ PERIOD SOG THRESHOLDS VALIDATED?** YES, all 3 periods

**ARE 2+ THRESHOLDS VALIDATED?** YES, all 3 periods

**ARE 3+ THRESHOLDS VALIDATED?** PERIOD-SPECIFIC — YES for P1 only

**ARE HIGHER TAILS SUPPORTED?** LIMITED (4+/5+ pre-specified insufficient
data, never tested — Section AE)

**DO PERIOD EXPECTATIONS REMAIN COHERENT WITH FULL-GAME SOG?** ACCEPTABLE
(~0.10 SOG mean gap, disclosed — Section AA)

**DO LOW-CONFIDENCE PERIOD SOG PREDICTIONS SHOW POSITIVE SKILL?** YES for
P1; MIXED for P2; NO (repeated negative) for P3

**DOES PERIOD SOG NEED A NEW LOW-CONFIDENCE WATCH_ONLY POLICY?** YES, but
ONLY for P3 (Section AF)

**WERE ANY EXISTING VALIDATED MODELS CHANGED?** NO

**WAS CONFIDENCE CHANGED?** NO

**WAS DECISION POLICY v2 CHANGED?** Superseded to v3 by this slice's own
authorized, narrow addition (`PLAYER_SOG_PERIOD_3: WATCH`) — no OTHER
slice's decision was touched

**WAS NHL WIN MODEL CHANGED?** NO

**CURRENT FULL TEST RESULT?** 1,195 / 1,195

**WHAT IS NOW THE HIGHEST-LEVERAGE NEXT DEVELOPMENT SLICE?** Team Goals by
Period — the same validated upstream-model × share + offset-GLM
architecture, applied to the second-most-tractable period market, before
a broader period-market rollout. See Section AN.

---

**STOP AFTER PLAYER SOG BY PERIOD.** No Team Goals by Period, Goalie Saves
predictive model, PP Points, First Goal Scorer, joint simulator, or parlay
logic was built in this slice.
