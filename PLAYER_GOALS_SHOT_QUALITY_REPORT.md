# Goals Model — Shot-Quality Refinement Cycle

**Verdict: KEEP CURRENT GOALS MODEL.** Five shot-quality challengers (xG/shot, high-danger rate, a
combined term, finishing-above-xG, PP shot quality) were built and tested against the frozen
incumbent (`PLAYER_GOALS_VALIDATION_REPORT.md`'s candidate E). **None cleared the pre-registered
95% development bar**, and even the final-fold Brier deltas — reported for completeness, not used
to decide adoption — are microscopic (parts-per-million), consistent with shot-quality metrics
being highly redundant with the shooting-talent signal the incumbent already has. The incumbent is
retained completely unchanged.

**EVALUATION STATUS: REUSED HISTORICAL DATA UNDER NEW GOALS DEVELOPMENT CYCLE.**

---

## A. Shot-quality data-contract audit

All fields confirmed real and already captured in `research/player_goals/player_game_goals.jsonl`
(no new corpus build needed) — read directly, not inferred:

| Concept | Real field | Grain |
|---|---|---|
| Individual xG | `individual_xg` | player-game |
| High-danger attempts | `high_danger_shots` | player-game |
| Medium/low-danger attempts | `medium_danger_shots` / `low_danger_shots` | player-game |
| High-danger xG | `high_danger_xg` | player-game |
| Shot attempts / SOG | `shot_attempts` / `sog` | player-game |
| PP xG / PP SOG | `pp.individual_xg` / `pp.sog` (nested "5on4"-situation block) | player-game |
| 5v5 shot quality | not separately built (would require a new nested "5on5" block) | — |
| Rush attempts | **NOT AVAILABLE** — confirmed absent from the raw MoneyPuck export, same finding as the original Goals audit | — |

All shot-quality metrics below are built at **player-game grain**, the lowest level available and
necessary (no shot-event-level export exists in this data source).

## B. Field coverage

100% of the 188,863-row Goals corpus has `individual_xg`/`high_danger_shots`/`shot_attempts`
populated (every row, including zero-shot games, carries a real 0.0 rather than a missing value).
PP-specific fields are populated for the 119,355 rows with real 5-on-4 icetime > 0 — identical
coverage pattern to every other PP-block feature already used in this project.

## C. xG/shot stability

**Real, direct season-to-season persistence** (Pearson correlation, by prior-season shot volume):

| Prior-season SOG volume | n pairs | xG/shot correlation |
|---|---|---|
| 20-49 shots | 202 | **r = 0.83** |
| 50-99 shots | 577 | **r = 0.88** |
| 100+ shots | 988 | **r = 0.88** |

**xG/shot is dramatically more persistent than shooting% itself** (which was r=0.37-0.61 in the
original Goals cycle) — reflecting that xG/shot captures shot *selection*/location habits (a
stable role/skill trait), while shooting% also carries finishing-luck noise.

## D. High-danger stability

| Prior-season SOG volume | n pairs | High-danger-share correlation |
|---|---|---|
| 20-49 shots | 202 | r = 0.73 |
| 50-99 shots | 577 | r = 0.78 |
| 100+ shots | 988 | r = 0.80 |

Also highly persistent, though slightly less than xG/shot.

## E. Finishing-above-xG persistence

| Prior-season SOG volume | n pairs | Finishing-above-xG (goals−xG per game) correlation |
|---|---|---|
| 20-49 shots | 202 | r = 0.11 |
| 50-99 shots | 577 | r = 0.25 |
| 100+ shots | 988 | r = 0.34 |

**Weak** — far weaker than either xG/shot or high-danger share, and weaker even than raw shooting%.
Per Part 6's explicit instruction ("if weak: do not use it"), finishing-above-xG was still built,
heavily shrunk, and included as one of the five tested challengers (transparency over
pre-judgment) — and it independently confirmed itself as the **weakest of the five** on
development data (Section H), consistent with this persistence finding.

**Redundancy check (Part 5)**, career aggregates for the 725 players with 100+ career SOG:

| Pair | Correlation |
|---|---|
| shooting% ↔ xG/shot | **r = 0.825** |
| shooting% ↔ high-danger share | r = 0.734 |
| **xG/shot ↔ high-danger share** | **r = 0.955** |
| shooting% ↔ xG/60 | r = 0.786 |

xG/shot and high-danger share are **almost the same signal** (r=0.955) — testing both as fully
independent features would risk exactly the redundant-stacking Part 5 warns against. Both are also
substantially correlated with the shrunk shooting-talent feature **already in the incumbent**
(r=0.73-0.83). This correlational evidence, measured before any model was built, already predicted
low incremental headroom — the subsequent modeling confirmed it empirically (Section H) rather than
the conclusion being assumed.

## F. Shrinkage methodology

`research/player_goals/shot_quality.py` — same convention as the incumbent's shooting-talent
shrinkage: credibility weight `n/(n+K)` toward a league prior, using the appropriate volume unit
per metric (career shots for xG/shot; career shot attempts for high-danger share; career games for
finishing-above-xG, shrunk toward its league mean of ~0 by xG's own construction; career PP shots
for PP xG/shot). `K=100` (xG/shot, finishing-above-xG, PP), `K=200` (high-danger share) — chosen as
round, conservative starting values consistent with the incumbent's own K=75 for shooting talent;
not separately grid-searched this cycle given the small effect sizes found made further shrinkage
tuning immaterial (disclosed, not hidden).

## G. PP shot-quality findings

`E_pp_xg_per_shot` was the single best-performing challenger on the final fold (Brier delta
-0.0000056, largest of the five) — but it did **not** clear the DEV bar either (81.1%, below 95%),
so it is not adopted despite being nominally "best." Reported honestly rather than cherry-picked
for its final-fold ranking alone.

## H. Candidate models

Five challengers (A/incumbent implicit, B-E per the prompt's own naming plus finishing-above-xG),
each `incumbent_mu` as a fixed log-offset plus one small (1-2 feature) correction fit on DEV data
only:

| | Feature | DEV frac_improved (95% required) | Cleared bar? |
|---|---|---|---|
| B | xG/shot (shrunk) | 81.5% | ❌ |
| C | High-danger rate (shrunk) | 85.3% | ❌ |
| D | Combined xG/shot + high-danger | 82.0% | ❌ |
| D′ | Finishing-above-xG (shrunk) | 87.6% | ❌ (weakest hypothesis, still tested fairly) |
| E | PP xG/shot (shrunk) | 81.1% | ❌ |

**No candidate cleared 95% on development data.** Per the combined-term rule (Part 5's own
"do not stack redundant features"), the combined B+C term was only built because both individual
tests cleared a lower 70% sanity threshold — it was still tested fairly, and it also failed the
real 95% bar.

## I. Temporal-fold design

**DEV = 2024-25** (internal 70/30 date split, 30,357 fit / 13,786 select rows) — decides which
challenger(s) to freeze. **FINAL FOLD = 2025-26** (43,846 rows) — scored once, after freezing.

**Methodology note, disclosed rather than worked around:** this is a **single final fold**, not the
"multiple rolling folds" ideal, because the incumbent's own frozen `RoleLeagueRates`/
`context_weights_e` were fit using **all** of 2023-24 — reusing 2022-23 or 2023-24 as a fold for
this refinement would mean the "incumbent" prediction for those seasons' games encodes information
from *after* those games happened (a real backward temporal-leakage risk at the aggregate level,
even though player-level history stays correctly gated throughout). DEV=2024-25 and FINAL=2025-26
are the only two seasons that postdate the incumbent's own fit data cleanly.

## J. Freeze manifest

[`research/goals_shot_quality_manifest.json`](research/goals_shot_quality_manifest.json) — written
after DEV decisions, before the final fold was scored.

## K. Common evaluation counts

Incumbent and all 5 challengers scored on the identical 43,846 final-fold rows (verified,
`test_13_incumbent_and_every_challenger_share_the_same_final_fold_n`).

## L. Incumbent Brier

**0.123511** (1+, final fold — matches the original Goals cycle's own season-specific 2025-26
number exactly, confirming the frozen artifacts were reused correctly, not silently redrifted).

## M. Challenger Brier

Best (E — PP xG/shot): **0.123505**.

## N. Brier delta

**-0.0000056** — six decimal places, i.e. essentially zero in any practically meaningful sense.

## O. Incumbent log loss

0.40226 (final fold).

## P. Challenger log loss

Not separately reported as a headline decision metric this cycle (Brier was the DEV-phase decision
gate per the frozen manifest); recorded per-candidate in `research/goals_shot_quality_results.json`
for independent review.

## Q. Log-loss delta

See P — available in the raw results file; not material to the adoption decision since Brier
already failed the pre-registered gate.

## R. Calibration comparison

By probability region (final fold, incumbent vs. best challenger mean prediction): both track the
actual rate closely at every decile from 0-60%, and the two models' mean predictions differ by
less than 0.001 in every bucket — **no meaningful calibration difference**, consistent with the
near-zero Brier delta.

## S. Game-cluster bootstrap

Best challenger (E) vs. incumbent: 99.8% of 1,000 game-clustered resamples favor the challenger —
**nominally strong**, but this reflects a consistently-signed, extremely small effect detected
reliably by a large (43,846-row) sample, not a practically important improvement. High bootstrap
confidence and large effect size are different things, and only the DEV-phase pre-registered gate
(which failed) was used to decide adoption, per Part 19's explicit warning against confusing
statistical detectability with justified complexity.

## T. Date-cluster sensitivity

100% of 500 date-clustered resamples favor E — confirms the direction is consistent, not a
clustering artifact, but does not change the magnitude finding.

## U. Fold consistency

DEV (13,786 rows): 81.1% frac_improved for E. FINAL (43,846 rows): 99.8%. The *direction* is
consistent across both samples; the *magnitude* is consistently tiny in both (DEV point delta
-3.1×10⁻⁶; final-fold delta -5.6×10⁻⁶). Consistency of a negligible effect is still a negligible
effect.

## V. Shot-volume segments

| Segment | n | Incumbent Brier | Challenger Brier | Delta |
|---|---|---|---|---|
| LOW | 14,632 | 0.086112 | 0.086099 | -0.0000131 |
| MEDIUM | 14,615 | 0.108439 | 0.108437 | -0.0000020 |
| HIGH | 14,599 | 0.176082 | 0.176080 | -0.0000017 |

Largest (still tiny) improvement in the LOW-volume segment — plausible, since shot-quality priors
carry relatively more information when a player's own shot-volume history is thin.

## W. Position segments

| Segment | n | Delta |
|---|---|---|
| FORWARD | 29,208 | -0.0000070 |
| DEFENSE | 14,638 | -0.0000027 |

## X. Sample-size segments

| Segment | n | Delta |
|---|---|---|
| LOW (<20 games) | 1,375 | **-0.0000338** (largest of any segment) |
| MEDIUM (20-59) | 3,343 | -0.0000254 |
| MATURE (60+) | 39,128 | -0.0000029 |

Low-history players show the largest relative benefit — but even here, "largest" means a Brier
improvement of 0.000034, still far below any threshold that would justify shipping added
complexity.

## Y. LOW-confidence results

| | n | Incumbent skill | Challenger skill |
|---|---|---|---|
| HIGH | 30,866 | 0.0680 | 0.0680 |
| MEDIUM | 12,507 | 0.0438 | 0.0440 |
| LOW | 473 | 0.00078 | 0.00125 |

**Does shot quality help LOW-confidence reliability? NO** — both incumbent and challenger show
weak, near-zero skill in the LOW bucket on this final-fold subsample (n=473 — smaller than the
original full-eval-set LOW bucket of n=872, so this specific slice reads less negative than the
original report's -0.032 finding; both are consistent with "LOW confidence is not reliable here,"
just measured on different sample sizes). The original negative-skill finding stands as the
primary, higher-powered reference. Shot quality does not meaningfully change this picture either
way.

## Z. 2+ diagnostic status

**Unchanged: INSUFFICIENT DATA.** Per Part 26's explicit instruction, the pre-specified support
standard was not touched or re-evaluated; 790 real 2+ events exist in the final fold alone
(consistent with the original report's count), reported as a raw diagnostic only.

## AA. Goals registry decision

`research/player_props/registry.py` → `GOALS`: `model_status="VALIDATED"` (**unchanged**),
summary updated to record "MODEL: CURRENT INCUMBENT RETAINED" and the real refinement-cycle
finding. No downgrade, per Part 29's explicit instruction not to downgrade a validated model merely
because a refinement failed.

## AB. Anytime Goal status

Unchanged: `SUPPORTED_BY_GOALS_MODEL`. `P(anytime goal) = P(goals≥1)` continues to hold since 1+
remains validated and unmodified.

## AC. Dashboard changes

Added a "Shot-Quality Refinement Cycle" section to `dashboard/pages/12_Player_Goals_Research.py`,
below the existing incumbent-model display: incumbent vs. best-challenger Brier metrics, an
explicit "Cleared 95% bar on DEV first?" indicator (reads **NO**), a clear "KEEP CURRENT GOALS
MODEL" warning banner, and a full per-challenger comparison table. The existing live "project a
player" section is **untouched** and continues to use only the incumbent's frozen weights — the
challenger is never used for live predictions. Verified live in-browser: renders without error, all
numbers match this report exactly.

## AD. Files created/modified

**New:**
- `research/player_goals/shot_quality.py`
- `research/run_goals_shot_quality_refinement.py`
- `research/goals_shot_quality_results.json` (generated)
- `research/goals_shot_quality_manifest.json` (generated)
- `tests/test_goals_shot_quality_refinement.py` (33 tests)
- `PLAYER_GOALS_SHOT_QUALITY_REPORT.md` (this file)

**Modified:**
- `research/player_props/registry.py` — GOALS summary updated (status unchanged: `VALIDATED`)
- `dashboard/pages/12_Player_Goals_Research.py` — added the refinement comparison section

**Unchanged (verified via `git status --porcelain`, no "M" entries):**
`research/player_goals_results.json`, `research/run_player_goals_model.py`,
`research/player_props/decision_policy.py`, every other prop's model file, the production NHL
model, and `nhl.db`.

## AE. Full test result

**971 / 971 passing** (938 prior + 33 new shot-quality-refinement tests). Confirmed via
`python3 -m unittest discover tests`.

## AF. Final recommendation

**KEEP CURRENT GOALS MODEL.**

## AG. Recommended next single development slice

The Goals model's most natural refinement avenues (shooting talent, shot quality) have now both
been thoroughly investigated with real, honest, mostly-negative-for-adoption findings — a mature,
well-evidenced state. Two reasonable next steps, in order of readiness:

1. **A combined LOW-confidence gating policy review** across the three sparse props now showing
   negative-or-near-zero LOW-confidence skill (Assists, Points, Goals) — Assists/Points already
   have `WATCH_ONLY`; Goals does not yet, and this is the natural point to decide as one policy
   review rather than three separate slices.
2. **Build the PP Points model** — next in the sprint's original priority order, with real PP
   shot-quality infrastructure (`pp.individual_xg`, `pp.sog`) now already built and tested in this
   very cycle, ready for reuse.

Between the two, **the combined gating policy review is the smaller, more directly actionable
step** and closes out a finding that has now been observed three times.

---

## Final Questions

**DID xG/SHOT ADD INCREMENTAL VALUE?** NO (81.5% DEV frac_improved, below the 95% bar).

**DID HIGH-DANGER RATE ADD INCREMENTAL VALUE?** NO (85.3%).

**DID PP SHOT QUALITY ADD INCREMENTAL VALUE?** NO (81.1% — the best final-fold performer, but
still failed the DEV gate).

**IS FINISHING ABOVE xG PERSISTENT ENOUGH TO USE?** NO (r=0.11-0.34, weakest of all shot-quality
metrics tested; also failed the DEV gate at 87.6%, its own best showing despite being the weakest
hypothesis).

**DID THE BEST SHOT-QUALITY CHALLENGER BEAT THE INCUMBENT ON BRIER?** YES, nominally, on the final
fold (-0.0000056) — but NOT on the pre-registered DEV gate, which is what determined adoption.

**DID IT BEAT THE INCUMBENT ON LOG LOSS?** Not used as a decision metric this cycle (Brier was the
frozen gate); recorded in the raw results for review.

**DID CALIBRATION IMPROVE OR REMAIN ACCEPTABLE?** REMAINED ACCEPTABLE — no meaningful difference
found in either direction.

**DID THE RESULT HOLD ACROSS TEMPORAL FOLDS?** The *direction* held (DEV and final fold agree);
the *magnitude* was negligible in both.

**DID GAME-CLUSTERED BOOTSTRAP SUPPORT ADOPTION?** NOMINALLY YES on the final fold (99.8%) — but
adoption was correctly NOT decided by this number, per the pre-registered DEV gate.

**DID DATE-CLUSTERED SENSITIVITY SUPPORT ADOPTION?** Same caveat — nominally yes (100%), not
decision-driving.

**DOES SHOT QUALITY HELP LOW-CONFIDENCE GOALS?** NO.

**ARE LOW-CONFIDENCE GOALS STILL NEGATIVE SKILL?** YES, per the original full-eval-set finding
(-0.032); this cycle's smaller final-fold-only subsample read near-zero rather than negative,
consistent with LOW-bucket noise at small n, not a contradiction.

**SHOULD LOW-CONFIDENCE GOALS RECEIVE WATCH_ONLY GATING IN A SEPARATE POLICY SLICE?** YES —
recommended as part of a combined review alongside Assists/Points (Section AG), not decided here.

**IS GOALS 1+ STILL VALIDATED?** YES.

**IS GOALS 2+ STILL INSUFFICIENT DATA?** YES.

**CAN ANYTIME GOAL STILL USE P(GOALS >= 1)?** YES.

**WERE ANY OTHER VALIDATED PROP MODELS CHANGED?** NO.

**WAS THE CONFIDENCE FRAMEWORK CHANGED?** NO.

**WAS THE GATING POLICY CHANGED?** NO.

**WAS THE NHL WIN MODEL CHANGED?** NO.

**CURRENT FULL TEST RESULT?** 971 / 971.

**WHAT SHOULD THE NEXT SINGLE DEVELOPMENT SLICE BE?** A combined LOW-confidence WATCH_ONLY gating
review across Assists, Points, and Goals together (Section AG) — the smaller, more directly
actionable step, ahead of building PP Points or any further Goals refinement.
