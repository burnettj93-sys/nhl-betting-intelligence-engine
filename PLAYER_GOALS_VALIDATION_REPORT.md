# Player Goals + Anytime Goal — Locked Walk-Forward Validation

**Verdict: GOALS 1+ — VALIDATED.** A hierarchical player→role→league empirical baseline plus a
shrunk-shooting-talent / opponent-context / H2H-goals offset adjustment beats the strongest naive
baseline with 99.4% game-clustered and 99.2% date-clustered bootstrap credibility, generalizes
cleanly across both real evaluation seasons, and is well calibrated. **GOALS 2+ remains
INSUFFICIENT DATA** (fails only the per-confidence-bucket support check). **ANYTIME GOAL is
supported conceptually** (`P(anytime goal) = P(goals≥1)`) pending real market-payload confirmation
once live markets return.

---

## A. Data audit

Real MoneyPuck skater-game fields confirmed present (`research/player_sog/raw/*.csv`):

| Concept | Real field |
|---|---|
| Goals | `I_F_goals` |
| SOG | `I_F_shotsOnGoal` |
| Shot attempts / unblocked attempts | `I_F_shotAttempts` / `I_F_unblockedShotAttempts` |
| Individual xG | `I_F_xGoals` |
| High/medium/low-danger attempts | `I_F_highDangerShots` / `I_F_mediumDangerShots` / `I_F_lowDangerShots` |
| High-danger xG | `I_F_highDangerxGoals` |
| Rebounds / rebound goals | `I_F_rebounds` / `I_F_reboundGoals` |
| Total / 5v5 / PP TOI | `icetime` (situation="all"/"5on5"/"5on4") |
| Player/team/opponent/game/date/season/home-away/position | `playerId`/`playerTeam`/`opposingTeam`/`gameId`/`gameDate`/`season`/`home_or_away`/`position` |

**No "rush attempt" field exists** in this MoneyPuck skater export — checked directly against the
raw header, not inferred or invented.

## B. Goals corpus size

`research/player_goals/build_goals_corpus.py` → `research/player_goals/player_game_goals.jsonl`:
**188,863 rows**, 1,330 unique players, matching every other prop's corpus scale exactly (same
underlying source files).

## C. Projected-active accounting

**GOALS-MODEL EVALUATION: CONDITIONAL ON ACTUAL GAME PARTICIPATION** — same standing disclosure as
every other prop this session; the MoneyPuck corpus contains only players who actually appeared.

| | 2024-25 | 2025-26 |
|---|---|---|
| Total target player-games | 47,224 | 47,212 |
| Excluded (insufficient history OR not projected-active) | 3,081 | 3,366 |
| **Common evaluation rows** | **44,143** | **43,846** |

Final evaluation rows: **87,989** (both seasons combined).

## D. Goal-event frequency

Real, full-corpus: mean 0.171 goals/game, variance/mean ratio 1.08 (mild overdispersion), **zero
rate 84.8%**, 1-goal rate 13.5%, 2+ rate 1.72%, 3+ rate 0.20% — confirming the prompt's own
instruction that 3+ should not be prioritized (real 3+ event rate is an order of magnitude sparser
than 2+).

## E. Baselines

| Baseline | Definition |
|---|---|
| A — season-to-date | `season_to_date_mean(goals)` per game |
| B — per-60 × recent TOI | season-to-date goals/60 × recent-10 TOI |
| C — SOG × shooting% | season-to-date SOG rate × shrunk career shooting% (mechanistic) |
| D — empirical, unshrunk | raw player historical `P(goals≥1)`, no shrinkage (deliberately weak) |
| E — empirical, shrunk | same, `n/(n+20)` shrinkage toward league rate |

## F. Shooting-talent stability

**Real, direct season-to-season persistence measurement** (Pearson correlation of shooting% between
consecutive seasons for the same player, by prior-season shot volume):

| Prior-season SOG volume | n player-season pairs | Season-to-season correlation |
|---|---|---|
| 20-49 shots | 202 | **r = 0.37** |
| 50-99 shots | 577 | **r = 0.53** |
| 100+ shots | 988 | **r = 0.61** |

Shooting talent is **real and meaningfully persistent, and persistence increases with shot
volume** — exactly the pattern that justifies volume-weighted shrinkage (Section G), not an
assumption.

## G. Shooting-talent shrinkage methodology

`research/player_goals/features.py::career_shooting_pct_shrunk()` — credibility weight
`n_shots/(n_shots+K)` toward the league shooting%, using **career shots-on-goal** (not game count,
since shooting% is inherently a per-shot rate). `K=75`, chosen via a tuning-validate grid search
over {75, 150, 250} — the tightest of the three tested, meaning the real data supported trusting
observed shooting% somewhat sooner than an initial conservative guess of 150. Real example: Shane
Wright's raw 31.2% shooting% (16 career shots, small-sample noise) shrinks to a far more defensible
12.5%.

## H. Upstream SOG eligibility

**UPSTREAM SOG FEATURE: NOT ELIGIBLE FOR CLEAN EVALUATION.** Same structural reasoning already
established for Points (`PLAYER_POINTS_VALIDATION_REPORT.md` Section I), applied consistently
rather than re-litigated: the validated SOG model exposes a single globally-fitted weight state
whose own headline-stage selection was reported directly against its eval seasons rather than a
separate pre-registered tuning-only split, so a genuinely clean out-of-fold reuse cannot be
guaranteed. A player's own PIT-safe rolling SOG rate (NOT the SOG model's prediction) was used
instead as the shot-volume signal — see Section I.

## I. Upstream SOG value

N/A — not used, per Section H. The player's own rolling-10 SOG rate (`shot_volume_log_ratio`
feature) WAS tested as an ordinary PIT-safe historical feature (distinct from the validated SOG
model) and did **NOT** clear the 95% tuning-validate bootstrap bar (Section K details).

## J. Shot-quality value

**NOT TESTED THIS CYCLE — a disclosed scope gap, not a silent omission.** Individual xG and
high/medium/low-danger attempt-rate fields were confirmed real and captured in the corpus (Section
A), but were not included as candidate features in the locked GLM/offset-adjustment stages given
the scope already covered (11 other features across 6 candidate model families). Recommended as the
most natural next refinement (Section AM).

## K. TOI value

**NO** — `M1→M2` (+TOI) did not clear the 95% tuning-validate bootstrap bar.

## L. PP-role value

**NO** — `M2→M3` (+PP role) did not clear the bar. A genuinely different finding from Points
(where PP role was the single strongest feature) — independently tested, not assumed to transfer.

## M. H2H-goals value

**YES** — `M8→M9` (+H2H goals) cleared the 95% bar. Locked into candidate E's context adjustment.

## N. H2H-SOG value

**NO** — `M7→M8` (+H2H SOG) did NOT clear the bar. **This is the OPPOSITE of the prompt's own
stated hypothesis** ("H2H shots useful, H2H goals useless") — reported exactly as found, not forced
toward the expected direction. A plausible explanation: goals are the actual scarce, decision-
relevant event for this specific opponent matchup (a player who has scored against a given goalie/
system combination before may carry real signal), while H2H SOG volume is a noisier, less specific
proxy once the model already has the player's own overall shot-volume history.

## O. Recent-form value

**NO** — `M0→M1` (+recent form) did not clear the bar, consistent with every other prop tested this
session (SOG's own recent-form finding, Blocks, Assists, Points all found weak/no recent-form
value).

## P. Team-context value

**NO** — `M6→M7` (+team context) did not clear the bar.

## Q. Opponent-context value

**YES** — `M5→M6` (+opponent context) cleared the bar. Locked into candidate E.

## R. Home/road value

Not independently tested this cycle as a standalone feature (scope: 11 features across 9 ablation
stages was already the largest feature set built this session); no home/road signal was assumed or
implied by omission.

## S. Candidate models

Six candidates (Part 10's full list), tested on the identical common evaluation set:

| | Structure | 1+ Brier | 1+ Skill | vs. best baseline (bootstrap) |
|---|---|---|---|---|
| **A** — hierarchical empirical | Player→role→league shrinkage, nonparametric | 0.12260 | 0.0633 | 15.8% (loses) |
| **B** — logistic | Direct P(1+), full feature set, plain gradient descent | 0.12402 | 0.0525 | 0.0% (loses) |
| **C** — Poisson GLM | Full feature set, log-link | 0.12460 | 0.0481 | 0.0% (loses) |
| **D** — Negative Binomial | Same as C, α=0.166 (real overdispersion found) | 0.12459 | 0.0481 | 0.0% (loses) |
| **E** — hierarchical + context offset | A's mean as a fixed offset + {shooting talent, opponent, H2H-goals} adjustment | **0.12251** | **0.0641** | **99.4% (wins)** |
| **F** — shot-generation × conversion | mu = recent SOG rate × shrunk shooting% (mechanistic, unfit) | 0.12313 | 0.0593 | 0.0% (loses) |

**E is the clear, decisive winner** — the only candidate to beat the best baseline. This mirrors
the STRUCTURE that worked for Points' best (but ultimately inconsistent) redesign candidate C3 —
but here it wins outright and consistently, a genuinely different and more successful outcome.

## T. Final frozen model

**Candidate E**: hierarchical empirical mean (`k_player=15`, role→league shrinkage) as a fixed
log-offset, plus a Poisson offset-GLM correction using exactly 3 features:
`shrunk_shooting_pct_log`, `opponent_log_factor`, `h2h_goals_delta`. Fitted weights:
`shrunk_shooting_pct_log = -0.0495`, `opponent_log_factor = +0.0393`, `h2h_goals_delta = -0.0083`
(all others masked to zero). No further NegBin correction needed (`alpha_e = 0.0` — consistent with
Points' own finding that a good player-specific mean absorbs most population-level overdispersion).

## U. Freeze manifest path

[`research/player_goals_freeze_manifest.json`](research/player_goals_freeze_manifest.json)

## V. Common evaluation set

| Season | Total target player-games | Excluded | Common evaluation |
|---|---|---|---|
| 2024-25 | 47,224 | 3,081 | 44,143 |
| 2025-26 | 47,212 | 3,366 | 43,846 |
| **Total** | **94,436** | **6,447** | **87,989** |

Identical row set used for every candidate AND every baseline (verified,
`test_24_common_evaluation_set_shared_across_candidates`).

## W. 1+ Brier

Locked (E): **0.12251**. Best baseline (E — empirical, shrunk): 0.12258. Delta: -0.00007 (small in
absolute terms, but backed by 99.4% game-clustered / 99.2% date-clustered bootstrap credibility —
tight, consistent, not noise).

## X. 1+ log loss

Locked (E): **0.3995**. Best baseline: 0.3999.

## Y. 1+ calibration

Mean predicted probability vs. actual rate, both essentially exact (actual rate 0.15488, and both
candidate and baseline predictions track it closely — E's calibration is at least as good as the
baseline's, not merely a Brier-optimized but miscalibrated fit).

## Z. 2+ metrics / status

**GOALS 2+: INSUFFICIENT DATA.** Uncalibrated metrics reported for completeness (E: Brier 0.01678,
log loss 0.0789, skill 0.0189 — still beats the baseline's 0.01683/0.0804/0.0164), but the
pre-specified standard fails on exactly one sub-check:

| Check | Result | Pass? |
|---|---|---|
| Total 2+ events (need ≥300) | 1,532 | ✅ |
| Per-confidence-bucket events (need ≥30 each) | HIGH=1,202, MEDIUM=325, **LOW=5** | ❌ |
| Per-season events (need ≥100 each) | 2024-25=742, 2025-26=790 | ✅ |
| Bootstrap CI half-width (need ≤0.01) | 0.000054 | ✅ |

The LOW-confidence bucket produced only 5 real 2+ events across both full evaluation seasons —
this is now the **third** prop family (after Points 3+) to fail its support standard on exactly
this same sub-check, a recurring pattern worth noting: LOW-confidence buckets are structurally too
small and too sparse-outcome-conditioned to support higher-threshold claims, regardless of prop.

## AA. Season-by-season results

| Season | n | 1+ Brier (skill) | 2+ Brier (skill) |
|---|---|---|---|
| 2024-25 | 44,143 | 0.12151 (0.0646) | 0.01624 (0.0172) |
| 2025-26 | 43,846 | 0.12351 (0.0635) | 0.01733 (0.0206) |

Both seasons show consistent, similarly-sized positive skill — clean generalization, not a
one-season artifact.

## AB. Game-clustered bootstrap

vs. best baseline (E — empirical shrunk), threshold=1, 1,000 resamples, 2,624 distinct games:

| Candidate | Point delta | 95% CI | % beating baseline |
|---|---|---|---|
| A | +0.0000243 | [-0.0000254, +0.0000748] | 15.8% |
| B | +0.00144 | [+0.00119, +0.00169] | 0.0% |
| C | +0.00202 | [+0.00174, +0.00232] | 0.0% |
| D | +0.00202 | [+0.00173, +0.00231] | 0.0% |
| **E** | **-0.0000731** | **[-0.000142, -0.0000112]** | **99.4%** |
| F | +0.00055 | [+0.00033, +0.00078] | 0.0% |

Negative delta = candidate improves. Only E's confidence interval sits entirely on the
"improves" side.

## AC. Date-cluster sensitivity

Repeated with date-clustered resampling (500 resamples, 345 distinct dates): E vs. best baseline —
point delta -0.0000731, 95% CI [-0.00014, -0.0000146], **99.2% favoring E**. Confirms the
game-clustered conclusion is not a clustering-scheme artifact.

## AD. Confidence results

| Bucket | n | 1+ Skill |
|---|---|---|
| HIGH | 63,166 | 0.0670 |
| MEDIUM | 23,951 | 0.0487 |
| LOW | 872 | **-0.0320** |

HIGH > MEDIUM > LOW ordering holds cleanly at the top, but **LOW shows negative skill** — this is
now the **third** sparse prop (after Assists and Points) to show this exact pattern, strengthening
the cross-prop evidence gathered in the prior Confidence Framework Redesign cycle. Per that cycle's
explicit "diagnose first, don't automatically gate" instruction (and this slice's own Part 25),
**no new WATCH_ONLY gate was created for Goals this slice** — the finding is reported, not acted on
unilaterally.

## AE. Conservative-probability results

`fraction_conservative_leq_raw = 1.0` — 100% of eval rows have conservative P(1+) ≤ raw P(1+),
using the unchanged shared `conservative_mu`. Mean raw P(1+) = 0.162 vs. mean conservative P(1+) =
0.100 — a real, substantial (6.2 percentage point) downward adjustment.

## AF. Representative examples

Real, mechanically-selected:

| Category | Player (context) | P(1+) | Raw shooting% | Shrunk | Confidence | Actual |
|---|---|---|---|---|---|---|
| Elite scorer | Timo Meier (NJD vs BUF) | 36% | 12.7% | 12.2% | HIGH | 0 |
| High-volume shooter | Jack Hughes (610 career shots) | 38% | 11.5% | 11.3% | MEDIUM | 0 |
| Low-volume finisher | Connor Clifton (D) | 6% | 5.3% | 7.7% | MEDIUM | 0 |
| PP-heavy player | Alex Tuch | 31% | 14.1% | 13.2% | HIGH | 0 |
| Defenseman | Connor Clifton | 6% | 5.3% | 7.7% | MEDIUM | 0 |
| High shooting% / small sample | Shane Wright (16 career shots) | 25% | **31.2%** | **12.5%** | LOW | 0 |
| Heavily-shrunk talent | Shane Wright (same — the shrinkage story) | 25% | 31.2% | 12.5% | LOW | 0 |
| High confidence | Erik Haula | 18% | 9.4% | 9.7% | HIGH | 0 |
| Low confidence | Shane Wright | 25% | 31.2% | 12.5% | LOW | 0 |
| Model hit | Erik Haula | 18% | — | — | HIGH | 0 (correct: predicted <50%, actual 0) |
| Model miss | Auston Matthews | **48%** | — | — | HIGH | **0** |

The Matthews miss is a genuine, non-cherry-picked example of a real failure mode — a 48% prediction
on an elite, high-confidence player that didn't hit — exactly the kind of case that must be shown
honestly, not hidden.

## AG. Goals registry status

`research/player_props/registry.py` → `GOALS`: `model_status="VALIDATED"`,
`confidence_validation_status="CONDITIONAL"` (per AD's negative LOW-confidence skill),
`live_market_support="NOT_CURRENTLY_AVAILABLE"`, `odds_api_market_key="player_goals"`.

## AH. Anytime Goal status

`ANYTIME_GOAL`: `model_status="SUPPORTED_BY_GOALS_MODEL"` — a distinct status, deliberately NOT
`"VALIDATED"` (Part 39's explicit instruction), since real DraftKings settlement semantics for
`player_goal_scorer_anytime` have not been verified against a live payload (no live NHL goal
markets currently posted).

## AI. Dashboard changes

New page: **Player Goals Research** (`dashboard/pages/12_Player_Goals_Research.py`), mirroring the
Points page's structure — locked-model-vs-baseline summary, live "project a player" section using
the exact frozen candidate-E weights (verified end-to-end with a real prediction: Connor McDavid,
EDM vs. WPG, 2024-10-09 → 43.2% P(1+ goal), 15.6% raw / 14.6% shrunk shooting%), and representative
examples. Uses the shared `render_confidence_badge()` component (no LOW-confidence warning banner
enabled yet, consistent with Part 25's "don't gate automatically" instruction). Verified live
in-browser: page loads without error, all displayed numbers match this report exactly.

## AJ. Bugs found/fixed

Two implementation bugs were found and fixed DURING this slice's own development (not in any
previously-validated model), both before the freeze/eval was finalized:
1. `RoleLeagueRates` was initially passed `build_example()`-output dicts (which use `actual_*` key
   names) instead of raw corpus rows (which use plain `goals`/`sog`/`position` keys) — caught
   immediately via a `KeyError` on first run, fixed by threading raw-row buckets alongside the
   example buckets.
2. Baseline D and candidate B's threshold-1-only probability dicts were being passed through a
   helper (`candidate_metrics`) that unconditionally expects both thresholds 1 and 2 — caught via a
   second `KeyError`, fixed by giving both a dedicated single-threshold metrics path.

Both were caught by the driver's own execution before any freeze manifest was written — no eval
data was ever scored with buggy code.

## AK. Files created/modified

**New:**
- `research/player_goals/build_goals_corpus.py`
- `research/player_goals/features.py`
- `research/player_goals/hierarchy.py`
- `research/player_goals/live_projection.py`
- `research/player_goals/player_game_goals.jsonl` (generated, gitignored)
- `research/run_player_goals_model.py`
- `research/player_goals_results.json` (generated)
- `research/player_goals_freeze_manifest.json` (generated)
- `dashboard/player_goals_view.py`
- `dashboard/pages/12_Player_Goals_Research.py`
- `tests/test_player_goals_model.py` (43 tests)
- `PLAYER_GOALS_VALIDATION_REPORT.md` (this file)

**Modified:**
- `research/player_props/registry.py` — GOALS: `RESEARCH`→`VALIDATED`; ANYTIME_GOAL:
  `RESEARCH`→`SUPPORTED_BY_GOALS_MODEL`
- `dashboard/app.py` — added page 12 to navigation
- `.gitignore` — added the new generated corpus path

**Unchanged (verified via `git status --porcelain`, no "M" entries):** every other prop's raw model
file/results file, `research/player_props/decision_policy.py`, `research/confidence_lab/reliability.py`,
`research/player_sog/count_models.py`, the production NHL model, and `nhl.db`.

## AL. Full test result

**938 / 938 passing** (895 prior + 43 new goals tests). Confirmed via
`python3 -m unittest discover tests`.

## AM. Recommended next single development slice

**Add shot-quality (xG/high-danger) features to the Goals model as a dedicated refinement cycle** —
Section J's disclosed scope gap is the single most natural next step: the data is already captured
in the corpus, the tuning/lock/freeze infrastructure already exists, and real analytics literature
strongly suggests individual xG-per-shot should carry information beyond raw SOG volume and
shooting% alone. This is a smaller, more targeted ask than a new prop family, and directly follows
up on real, disclosed unfinished work rather than starting something new.

---

## Final Questions

**IS THE GOALS MODEL VALIDATED?** YES (1+ only; 2+ is INSUFFICIENT DATA).

**IS 1+ GOAL VALIDATED?** YES.

**IS 2+ GOALS VALIDATED?** INSUFFICIENT DATA.

**CAN THE MODEL SUPPORT ANYTIME GOAL PROBABILITY?** YES.

**WAS FIRST-GOAL SCORER BUILT?** NO.

**DID EXPECTED SOG ADD TRUE INCREMENTAL VALUE?** NOT ELIGIBLE (upstream SOG model ruled ineligible
for clean evaluation, Section H; the player's own rolling SOG rate — a different, ordinary PIT-safe
feature — was tested and did NOT add value, Section I/K).

**DID SHOT QUALITY ADD VALUE?** NOT TESTED THIS CYCLE (Section J — disclosed scope gap).

**DID SHRUNK SHOOTING TALENT ADD VALUE?** YES.

**DID PP ROLE ADD VALUE?** NO.

**DID H2H GOALS ADD VALUE?** YES.

**DID H2H SOG ADD VALUE?** NO (contrary to the prompt's own stated hypothesis — reported exactly as
found, Section N).

**DID RECENT GOAL FORM ADD VALUE?** NO.

**DID TEAM CONTEXT ADD VALUE?** NO.

**DID OPPONENT CONTEXT ADD VALUE?** YES.

**ARE LOW-CONFIDENCE GOAL PREDICTIONS POSITIVE SKILL?** NO — negative skill (-0.032), the third
sparse prop to show this pattern.

**DOES GOALS REQUIRE A WATCH-ONLY LOW-CONFIDENCE GATE?** WARRANTED BY THE EVIDENCE, BUT NOT
IMPLEMENTED THIS SLICE — per Part 25's explicit instruction not to automatically extend the
Assists/Points gating policy without a dedicated design decision; recommended for a future slice
alongside the other two sparse props' gates as a single combined policy review.

**CAN GOALS FEED THE EXISTING GENERIC PROP-PRICING INTERFACE?** YES — `PropPrediction` is fully
prop-agnostic; `market_type="GOALS"` (or `"ANYTIME_GOAL"`) populates it with no interface changes.

**WERE ANY EXISTING VALIDATED RAW PROP MODELS CHANGED?** NO.

**WAS THE CONFIDENCE FRAMEWORK CHANGED?** NO.

**WAS THE LOW-CONFIDENCE GATING POLICY CHANGED?** NO.

**WAS THE NHL WIN MODEL CHANGED?** NO.

**CURRENT FULL TEST RESULT?** 938 / 938.

**WHAT SHOULD THE NEXT SINGLE DEVELOPMENT SLICE BE?** Add shot-quality (xG/high-danger) features to
the Goals model as a dedicated, targeted refinement cycle (Section AM) — not First Goal Scorer, not
PP Points, not Goalie Saves, and not a new gating-policy slice unless the user specifically wants
the three sparse props' LOW-confidence gates reviewed together.
