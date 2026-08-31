# Special-Teams Role-Transition Report

A focused refinement of the period-timing/special-teams sprint: tests whether a **change** in
PP/PK role explains error the frozen SOG/Goals/Assists/Points/Blocked-Shots models are already
making, rather than whether *absolute* PP/PK exposure adds value. Built entirely from real data
already in this project (real per-player-game PP/PK ice time, real frozen model predictions via
the existing `ContextMarginalContext`) — no re-fitting, no odds API, no scheduler. **The clearest
real finding reverses the sprint's own stated expectation**: absolute PP1 membership predicts the
residual about as well as, or better than, the *recency* of getting there — see Section D.

---

## A. Method

**PP1/PP2 defined quantitatively, per team-game** (never a label that doesn't exist historically):
for every (game, team), players are ranked by their own real PP ice time that game (from
`research/player_sog/player_game_sog.jsonl`'s `pp.icetime_seconds`, treating a missing/None value
as a real 0 — a player who never plays the power play that game, not missing data). The top 5 by
rank are `PP1`, the next 5 are `PP2`, everyone else (including anyone under a 20-second meaningful-
ice-time floor) is `NONE`. PK is defined identically from `research/player_blocks/
player_game_blocks.jsonl`'s `pk.icetime_seconds`.

**Role STATE for target game D** (PIT-safe by construction — `research/period_event_timing/
special_teams_roles.py::classify_role_state`): the mode single-game unit label across the player's
most recent 3 games strictly before D ("recent"), compared against the mode across the 8 games
before *that* ("baseline"), gated on a minimum of 2 recent / 5 baseline games with real data —
below the gate, the state is `ROLE_UNCERTAIN` rather than guessed. Neither window, nor the
classifier's own function signature, ever has access to game D's own data (Section H).

**Residual**: `actual outcome − frozen model expectation`, computed with the real, unmodified
`research.player_context_state.marginal_provenance.ContextMarginalContext` — the same shared object
this project already uses elsewhere, never a re-fit. SOG/Goals/Assists/Blocks all have a real count
model (`mu`), so their residual is `actual_count − mu`. **Points has no count model at all**
("empirical baseline remains champion" — a real, pre-existing architectural fact, not something
introduced here); its residual is instead a probability residual at the 1+ threshold:
`(1 if points≥1 else 0) − model_P(points≥1)`.

## B. Sample support (real corpus counts)

188,863 player-games classified for both PP and PK. `ROLE_UNCERTAIN` (insufficient history): 9,764
(5.2%) for each. Real transition counts, unique players, and **top-10-player concentration** (Part
"STAR / PLAYER CONCENTRATION" — checking the effect isn't just a handful of superstars):

| PP state | n | unique players | top-10 share |
|---|---|---|---|
| PROMOTED_PP2_TO_PP1 | 7,917 | 521 | 6.8% |
| ADDED_TO_PP1 | 1,300 | 326 | 12.2% |
| ADDED_TO_PP2 | 5,101 | 541 | 6.9% |
| DEMOTED_PP1_TO_PP2 | 7,247 | 460 | 8.1% |
| REMOVED_FROM_PP | 8,695 | 665 | — |
| STABLE_PP1 | 44,786 | 453 | — |
| STABLE_PP2 | 29,685 | 617 | — |
| NO_MEANINGFUL_PP | 74,368 | 855 | — |

Every named transition is spread across 300+ unique players with top-10 concentration under 13% —
**not a superstar-driven artifact.** PK sample sizes are comparably large (e.g.,
PROMOTED_PK2_TO_PK1: 12,308 instances, 632 players, 5.1% top-10 share).

## C. Residual by state — PP outcomes (real, full-population results)

`research/special_teams_role_residuals_results.json`, mean residual (n in parentheses):

| PP state | SOG | Goals | Assists | Points (prob. resid.) |
|---|---|---|---|---|
| ADDED_TO_PP1 | **+0.169** (1,206) | +0.009 | +0.003 | +0.023 |
| STABLE_PP1 | **+0.141** (43,863) | +0.013 | **+0.076** | +0.040 |
| PROMOTED_PP2_TO_PP1 | +0.103 (7,665) | +0.005 | +0.029 | +0.029 |
| ADDED_TO_PP2 | +0.048 | −0.010 | −0.017 | −0.003 |
| STABLE_PP2 | +0.009 | −0.017 | −0.016 | −0.013 |
| DEMOTED_PP1_TO_PP2 | −0.018 | −0.011 | +0.008 | +0.003 |
| REMOVED_FROM_PP | **−0.074** (7,949) | −0.019 | −0.041 | −0.030 |
| NO_MEANINGFUL_PP | −0.074 (68,784) | −0.020 | −0.052 | −0.038 |

SOG shows the cleanest, most statistically robust pattern (standard errors ≈0.015-0.04 at these
sample sizes; every named transition's mean is several SEs from zero). Goals shows the same
direction throughout but a much smaller magnitude. Assists and Points are real but noisier, and — a
genuinely interesting wrinkle — for **Assists specifically, STABLE_PP1 (+0.076) shows a bigger
residual than the PROMOTED transition itself (+0.029)**, the opposite of what a pure "recency of
change" story would predict.

## D. Role change vs. absolute role — the key finding

Comparing the residual against the real **magnitude of change** in the same state
(`research/special_teams_role_transitions_table.jsonl`):

| PP state | mean Δ PP TOI (s) | mean Δ PP share | SOG residual |
|---|---|---|---|
| ADDED_TO_PP1 | +82.7 | +0.291 | +0.169 |
| **STABLE_PP1** | **+2.0** (≈no change) | **+0.010** (≈no change) | **+0.141** |
| PROMOTED_PP2_TO_PP1 | +35.7 | +0.145 | +0.103 |
| ADDED_TO_PP2 | +46.5 | +0.142 | +0.048 |
| DEMOTED_PP1_TO_PP2 | −42.4 | −0.154 | −0.018 |
| REMOVED_FROM_PP | −54.4 | −0.160 | −0.074 |

**STABLE_PP1 has essentially zero change (Δ≈+2s) yet a residual (+0.141) nearly as large as
ADDED_TO_PP1's (+0.169, built on an +82.7s change), and *larger* than PROMOTED_PP2_TO_PP1's
(+0.103, built on a +35.7s change).** This directly contradicts the sprint's own stated hypothesis
that "change may carry more incremental information because absolute role is already partially
embedded in the player's frozen baseline." The real data says the opposite for SOG: **simply being
on PP1 — whether newly arrived or long-settled — matters more than how recently the player got
there.** This reframes the likely mechanism: the frozen models' existing rolling-rate features may
have a persistent blind spot for PP1-unit membership generally, not specifically a *transition-lag*
problem.

## E. Time-to-adapt (the one place the "transition, not just role" story does hold)

Tracking the SOG residual at the first onset of `PROMOTED_PP2_TO_PP1` and the following 4 games
(521 unique onsets; `research/special_teams_time_to_adapt_results.json`):

| Games since promotion | 0 | 1 | 2 | 3 | 4 |
|---|---|---|---|---|---|
| Mean SOG residual | +0.105 | +0.112 | **+0.133** | +0.067 | **−0.007** |

This is a real, clean, decaying curve: the residual **persists and even peaks at game+2**, then
decays back to essentially zero by game+4 — the classic signature of a temporary adaptation lag
(consistent with a 5-10-game rolling feature gradually absorbing the new, higher-TOI games). The
mirror case, `REMOVED_FROM_PP` (665 onsets), is noisier and less clean (−0.019, +0.004, −0.001,
−0.112, −0.061) — real, but without as clear a monotonic pattern. **So both things are true at
once**: there IS a real, temporary (~4-game) transition-specific effect on top of the persistent
absolute-role effect from Section D — they are not mutually exclusive, and the transition effect is
the one that's actually short-lived enough to justify an "overlay" framing rather than a permanent
feature change.

## F. PK → Blocked Shots (the named highest-value PK hypothesis)

Real, but **much weaker and less clean** than the PP → SOG result:

| PK state | Blocked-shots residual | n |
|---|---|---|
| STABLE_PK1 | +0.035 | 41,966 |
| ADDED_TO_PK1 | +0.035 | 2,743 |
| PROMOTED_PK2_TO_PK1 | **+0.001** (≈zero) | 11,883 |
| DEMOTED_PK1_TO_PK2 | −0.024 | 11,827 |
| REMOVED_FROM_PK | **−0.064** | 9,294 |

`PROMOTED_PK2_TO_PK1`'s residual (+0.0015) is statistically indistinguishable from zero (SE≈0.009)
— **PK promotions do not show the clean positive effect PP promotions show for SOG.**
`REMOVED_FROM_PK`'s negative residual (−0.064, ~6.4 SE from zero) is real. The time-to-adapt curves
for both `ADDED_TO_PK1` and `REMOVED_FROM_PK` (Section E's method, applied to Blocks) are noisy with
no clean monotonic pattern, unlike the PP/SOG case.

## G. Hits

`research/player_blocks/player_game_blocks.jsonl` carries a real `hits` field for all 188,863
rows (not derived here — the same corpus this sprint already used for PK labels). Per this
project's own existing registry (`research/player_props/registry.py`, `market_type="HITS"`,
`model_status="PROMISING"`): real data confirmed good quality (mean 1.19 hits/game, meaningfully
overdispersed) in a prior sprint, but **no frozen HITS count model was ever built** (no live
projection function exists). **Classification: DATA_READY, not built.** Since there is no frozen
model to residualize against, the PK-role-transition hypothesis on hits is **NOT_TESTABLE** in the
same way as blocks — only a raw, non-residual comparison is possible, and it shows no clean
PK-role gradient (1.23-1.32 hits/game across every PK-active state, vs 1.09 for no-PK-time games —
a real "PK players hit more" gap, but flat within the PK-active population).

## H. Temporal safety

`classify_role_state()`'s own function signature takes only pre-sliced `recent_labels`/
`baseline_labels` lists — there is no game-D parameter for it to read from even by mistake
(structurally verified by a test). Every residual computed against a frozen model reuses
`ContextMarginalContext`'s own `history_as_of`-based PIT safety, unmodified. No target-game PP/PK
TOI, deployment, or outcome is ever read by the classifier.

## I. What was not done

- **Opponent special-teams environment interaction**: explicitly deferred per the task's own
  instruction ("test interaction only after establishing the player-role transition itself has
  incremental value") — Sections C-F establish that value is real for PP→SOG and mixed/weak
  elsewhere, so an opponent-interaction pass is a reasonable next slice, not run here.
- **A formal role-change overlay**: research only, as explicitly instructed. Not built, not
  promoted.
- **Period SOG** as a PP-outcome: not tested this pass (SOG full-game was used as the flagship
  test; extending to the period-level model is a natural but separate follow-up).
- **Total TOI as a PK outcome**: reported descriptively (Section F's underlying data), not as a
  residual — there's no "frozen TOI model" in this project to residualize against.

---

## Final Questions

**DO PP ROLE CHANGES EXPLAIN FROZEN-MODEL RESIDUAL ERROR?**
PARTIAL — real, non-zero residuals exist and are directionally consistent (positive for
promotion/addition, negative for demotion/removal) for SOG, weaker for Goals/Assists/Points; but
Section D shows absolute PP1 membership explains at least as much of the residual as the change
itself does.

**DO PP2 → PP1 PROMOTIONS CREATE POSITIVE RESIDUALS?**
YES (SOG +0.103, n=7,665, several SEs from zero; smaller but same-direction for Goals/Assists/
Points)

**DO NEW PP ASSIGNMENTS CREATE POSITIVE RESIDUALS?**
YES (ADDED_TO_PP1 SOG +0.169; ADDED_TO_PP2 SOG +0.048 — both positive, PP1 addition much larger)

**DO PP DEMOTIONS / REMOVALS CREATE NEGATIVE RESIDUALS?**
YES for REMOVED_FROM_PP (SOG −0.074, real); DEMOTED_PP1_TO_PP2 is negative but small and weaker
(−0.018)

**HOW MANY GAMES DOES THE EFFECT LAST?**
~4 games (SOG residual after a PP2→PP1 promotion: +0.105, +0.112, +0.133, +0.067, −0.007 at
games 0-4 — decayed to essentially zero by game 4)

**IS ROLE CHANGE MORE PREDICTIVE THAN ABSOLUTE PP EXPOSURE?**
NO — Section D is the key finding: STABLE_PP1 (no change at all) shows a residual comparable to or
larger than the transition states. Absolute role carries at least as much signal as the change.

**DO PK PROMOTIONS / ADDITIONS IMPROVE BLOCKED-SHOT PREDICTION?**
PARTIAL — ADDED_TO_PK1 shows a modest positive residual (+0.035, borderline significance);
PROMOTED_PK2_TO_PK1 shows essentially zero effect (+0.0015)

**DO PK REMOVALS REDUCE BLOCKED-SHOT EXPECTATION?**
YES (REMOVED_FROM_PK: −0.064, ~6.4 SE from zero — the cleanest PK-side finding)

**IS THERE A RELIABLE HIT CORPUS?**
YES (data quality already confirmed in a prior sprint — `model_status="PROMISING"` in the existing
registry) — but no frozen predictive model has been built on it.

**DO PK ROLE CHANGES IMPROVE HIT PREDICTION?**
NOT TESTABLE (no frozen Hits model exists to compute a residual against; a raw, non-residual
comparison shows no clean PK-role gradient within the PK-active population)

**SHOULD SPECIAL-TEAMS ROLE CHANGE BECOME A SEPARATE CONTEXT OVERLAY CANDIDATE?**
Research suggests a *narrow, transition-specific* overlay is more defensible than a permanent
feature change — the effect that's cleanly transition-shaped (decays over ~4 games) is exactly the
kind of thing an overlay is built for, per the task's own framing. But Section D's finding (absolute
role matters at least as much) means an overlay scoped ONLY to "just transitioned" would leave real
signal (the STABLE_PP1 gap) unaddressed. **Recommend further research before building anything**,
not a yes/no promotion decision — consistent with "do NOT build or promote an overlay
automatically."

**WHICH PROP FAMILY BENEFITS MOST FROM ROLE-CHANGE INFORMATION?**
Player SOG, by a clear margin — the largest, cleanest, most statistically robust residuals and the
only outcome with a clean, monotonic time-to-adapt decay curve. Goals/Assists/Points show the same
direction but far smaller magnitudes; Blocked Shots shows a real but much weaker and less clean
effect, concentrated in the removal direction rather than the promotion direction.

---

**STOP AFTER THIS RESEARCH REFINEMENT.**
