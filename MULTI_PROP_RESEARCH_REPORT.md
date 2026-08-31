# Multi-Prop Research Report

Generalizes the SOG player-prop architecture into a reusable framework and applies it to two new
prop families end-to-end (blocked shots, assists — both **VALIDATED**), while auditing real data
for the rest of the sprint's requested market list and classifying each honestly. **No prop
becomes VALIDATED because SOG worked** — every family was independently tested.

---

## A. Market reality (The Odds API, as documented)

Standard: `player_shots_on_goal`, `player_points`, `player_power_play_points`, `player_assists`,
`player_blocked_shots`, `player_goals`, `player_total_saves`, `player_goal_scorer_first`,
`player_goal_scorer_last`, `player_goal_scorer_anytime`. Alternate: `player_points_alternate`,
`player_assists_alternate`, `player_power_play_points_alternate`, `player_goals_alternate`.
**Hits and plus/minus are not in this documented list** — classified
`LIVE MARKET SUPPORT: NOT CURRENTLY AVAILABLE` / `UNSUPPORTED_MARKET` without spending additional
Odds API credits to re-confirm (per the sprint's own explicit credit-consciousness instruction —
the documented contract given in this slice's own prompt is treated as authoritative).

## B. Shared player-prop framework

See `PRESEASON_PRODUCT_AUDIT_REPORT.md` Section G for the full description. Summary: `research/
player_props/prediction.py` (the `PropPrediction` contract) and `registry.py` (Section W's
registry) are new; the actual count-distribution math, GLM fitting, confidence scoring, and
conservative-probability bound are **reused directly** from `research/player_sog/count_models.py`
(confirmed prop-agnostic, zero duplication) by both new prop models.

---

## C. SOG — VALIDATED (unchanged this sprint)

See `PLAYER_SOG_FOUNDATION_REPORT.md`. Not re-evaluated, not re-fit, not touched.

## D. Blocked shots — VALIDATED

**A. Corpus**: 188,863 real skater-games, same 4 seasons (2022-23 warm-up, 2023-24 tuning,
2024-25+2025-26 eval), same source files as SOG (no new download).
**B. Target label**: `shotsBlockedByPlayer` — MoneyPuck's own data dictionary: "Number of shot
attempts blocked by the player" (audited directly, not assumed — and explicitly NOT
`I_F_blockedShotAttempts`, which is the opposite concept).
**C. Distribution**: mean 0.832, variance 1.191, variance/mean **1.428** (moderate overdispersion,
almost identical magnitude to SOG's 1.428). Negative Binomial (alpha=0.126) modestly beats
Poisson on every metric.
**D. Common evaluation set**: 87,989 eval player-games (93.17% coverage of eligible player-games),
identical exclusion structure to SOG (insufficient history: 1,101; not projected active: 8,475).
**E. Headline thresholds**: 1+/2+/3+ (blocked shots is lower-volume than SOG; 4+/5+/6+ exist in
the data but are too rare for the sportsbook-relevant range).

| Threshold | Brier (NegBin) | Log loss | Actual rate |
|---|---|---|---|
| 1+ | 0.22196 | 0.63495 | 50.2% |
| 2+ | 0.13890 | 0.43852 | 20.4% |
| 3+ | 0.06376 | 0.22657 | 7.7% |

**F. Baselines beaten**: season-average and last-10-average, both at **100% bootstrap
credibility** (`baseline_vs_full_threshold2`).
**G. Value tests** (all independently re-tested, none assumed from SOG):

| Feature | Credible? | Bootstrap |
|---|---|---|
| Recent form | NO | 7.0% |
| TOI/role | YES | 100% |
| Opponent shot-attempt environment | **YES** (differs from SOG!) | 99.6%–100% |
| H2H (shrunk) | YES | 100% |

**H. Calibration**: error under 0.03 through the entire well-populated 0.0-0.7 predicted range
(27,098 to 216 examples per bucket).
**I. Segments**: Defense (mean 1.46 blocks/game) vs. forward (0.46-0.56) — confirmed via raw data
audit before modeling; Brier Skill Score: Defense 0.043, Forward 0.010 (model adds more real
skill for the position that blocks more, as expected).
**J. Confidence**: clean monotonic skill-score ordering, HIGH (0.152) > MEDIUM (0.091) > LOW
(~0.002).
**K. Fitted headline weights**: `log_baseline_rate` 0.853 (dominant anchor), `h2h_shrunk_delta`
0.111, `opponent_log_factor` 0.096, `toi_log_ratio` 0.024, `recent_form_log_ratio` -0.003
(correctly near zero).

**Recommendation: VALIDATED.** Beats every baseline, well-calibrated, clean confidence
stratification, every retained feature independently justified by its own bootstrap test.

## E. Assists — VALIDATED (with one reported caveat)

**A. Corpus**: same 188,863 real skater-games. **B. Target label**:
`I_F_primaryAssists + I_F_secondaryAssists` (MoneyPuck has no single combined "assists" column;
this sum is documented, not assumed). **C. Distribution**: mean 0.296, variance 0.326,
variance/mean **1.10** (mild overdispersion — much closer to Poisson than SOG/blocks). **D.
Common evaluation set**: 87,989 eval player-games, identical structure. **E. Headline
thresholds**: 1+/2+ (3+ occurs in only 0.6% of real games — too rare for this corpus size to
evaluate meaningfully).

| Threshold | Brier (Poisson) | Actual rate |
|---|---|---|
| 1+ | 0.17603 | 24.5% |
| 2+ | 0.03929 | 4.2% |

**F. Baseline beaten**: season-average, **100% bootstrap credibility** (only one baseline built
this slice, given time — see the audit report's honest scope note).
**G. Value tests**:

| Feature | Credible? | Bootstrap |
|---|---|---|
| Recent form | Not at the 95% bar used elsewhere (directionally positive but weak) | 70.0% |
| TOI/role | YES | 97.3% |
| Opponent points-allowed environment | YES | 100% |
| H2H (shrunk) | YES | 100% |

**H. Confidence caveat, reported honestly**: HIGH (skill 0.045) > MEDIUM (0.026) as expected, but
LOW (n=971, small) shows a **negative** skill score (-0.043) — worse than that bucket's own base
rate. Not investigated further this slice; flagged as a real, open finding rather than smoothed
over. Likely small-sample noise given n=971 vs. 23,966-60,444 in the other buckets, but this is a
hypothesis, not a confirmed explanation.
**I. Fitted headline weights**: `log_baseline_rate` 0.601, `h2h_shrunk_delta` 0.042,
`opponent_log_factor` 0.085, `toi_log_ratio` 0.006, `recent_form_log_ratio` 0.006 (both TOI and
recent form have small magnitude here despite TOI's bootstrap being credible — a real, reported
tension worth future investigation, not resolved this slice).

**Recommendation: VALIDATED for 1+/2+.** Beats its baseline with full credibility, real and
independently-tested feature value structure, but carries the LOW-confidence-bucket anomaly as an
open item — this is exactly why every prop is required to prove itself independently rather than
inherit SOG's clean result.

## F. Points — RESEARCH (not modeled this slice)

Real data audited directly (2024 season, `I_F_points`): mean 0.449, variance 0.510,
variance/mean **1.14**, 65.8% zero-games, max 6. **Why not modeled**: points = goals + assists,
and the sprint explicitly warns against naively summing independently-fit goal/assist
probabilities when the two are correlated (a player who scores is often also more likely to
assist in the same high-event game). A defensible points model needs either (a) its own direct
count model on `I_F_points` (straightforward given the shared framework — the SAME architecture
used for blocks/assists would apply directly), or (b) an explicit joint/correlation term over the
now-separately-built assists model and a future goals model. Deferred to the next slice (see
audit report Section AH) specifically so it isn't rushed to hit a prop-count target.

## G. Goals — RESEARCH (not modeled this slice)

Real data audited: mean 0.167, variance 0.178, variance/mean **1.07**, 85.1% zero-games, max 4 —
sparser and closer to pure-Poisson than any other prop checked. Per the sprint's explicit
warning, recent goals/shooting% must not be over-weighted (well-known small-sample noise in
hockey shooting percentage) — any future goals model should test recent-form value with the same
skepticism blocks/assists/SOG applied, expecting a real chance it fails the credibility bar again.
**Cross-prop dependency named, not tested**: whether the (already-validated) SOG model's own
expected-count OUTPUT is a useful upstream feature for goals probability is explicitly flagged by
the sprint as "one of the most important cross-prop dependencies" — a real, promising, untested
hypothesis for the next goals-model slice (shots create goals, mechanically).

## H. Power-play points — RESEARCH (deferred, not blocked)

Real PP-situation data (`5on4` MoneyPuck rows) already confirmed present and already used as a
feature source for both the SOG and assists models this project has built — no new data
availability question here. Deferred purely on the sprint's own Tier ordering (build core
assists/points/goals first). Would need the SAME lineup-uncertainty discipline already used for
starter goalies applied to PP-unit deployment: historical PP-TOI share is not confirmed PP1
tonight.

## I. Goalie saves — RESEARCH (architecture only)

Structure: `P(goalie starts)` (already validated, Stage 1 starter-projection system) ×
`distribution of saves conditional on start`. The actual historical starter must never be a
pregame feature (same discipline enforced throughout every goalie-adjacent slice this project has
built). Settlement semantics (DNP/void rules) are a real open question requiring DraftKings-
specific research, explicitly deferred per the sprint's own instruction. Not built this slice —
the conditional-on-start saves distribution itself would need its own real-data validation before
combining it with starter probability, exactly mirroring the "genuine probability mixture, never
collapse to most-likely-starter" discipline from the goalie-quality-integration slice.

## J. Hits — PROMISING (data), UNSUPPORTED_MARKET (live)

Real data confirmed good: `I_F_hits`, mean 1.19/game, variance 2.04, variance/mean **1.71**
(the MOST overdispersed of any stat checked this project), only 40.9% zero-games — genuinely
strong volume and variance for a count model, arguably better raw material than blocks. **But**:
`player_hits` is not a documented Odds API NHL market key (Section A). Per the sprint's explicit
instruction, modelability and live-market access are kept strictly separate: **no live pricing
plumbing was built** for hits, even though the underlying stat is real and well-suited to this
project's exact modeling approach. A future research-only hits model (no live pricing) remains a
legitimate, low-risk option if ever prioritized.

## K. Plus/minus — REJECTED (do not prioritize)

Not a documented Odds API market key. Depends on team scoring, team conceding, deployment,
empty-net states, teammates, and goalie — a compound, high-noise target with no current market to
even validate against. Per the sprint's own explicit permission to make this call:
**DEFER / DO NOT PRIORITIZE.**

## L. Anytime goal — RESEARCH, blocked on Goals

`player_goal_scorer_anytime` is documented. Likely reusable directly as `P(goals >= 1)` from a
future goals count model IF settlement semantics match (not yet confirmed against a real
DraftKings rule set). Blocked on Section G.

## M. First goal — RESEARCH, not ready

`player_goal_scorer_first` is documented but is **NOT** equivalent to anytime-goal probability —
it requires modeling event order/timing (who scores first among ~12-20 skaters who could score at
all that game), a materially harder problem than a per-player count/threshold model. No
architecture designed beyond naming this requirement, per the sprint's explicit instruction not
to pretend equivalence.

---

## N. Files created/modified

See `PRESEASON_PRODUCT_AUDIT_REPORT.md` Section AE (shared file list, both reports cover the same
sprint).

## O. Full test result

```
754 / 754 passing
```
See audit report Section AF for the breakdown.

## P. Confirmation production model unchanged

`models/`, `config.py`, `pricing/engine.py`, `pricing/decision.py`, `schema.sql` untouched;
`nhl.db` mtime predates this entire sprint. The validated SOG model was never imported for
modification by either new prop module (only its genuinely shared, prop-agnostic utility
functions were reused) — verified directly by
`tests/test_player_blocks_model.py`/`test_player_assists_model.py`'s
`test_sog_model_module_never_imports_from_blocks`-style structural tests.

---

## Final questions

- Is SOG still validated? **YES.**
- Is blocked shots validated? **YES.**
- Is assists validated? **YES** (with the LOW-confidence-bucket caveat reported in Section E).
- Is points validated? **NOT READY** (RESEARCH — design only, correlation-with-goals caution).
- Is goals validated? **NOT READY** (RESEARCH — real data audited, not modeled).
- Is PP points validated? **NOT READY** (RESEARCH — deferred per Tier ordering).
- Is goalie saves validated? **NOT READY** (RESEARCH — architecture only).
- Are hits available historically? **YES** — real, good-quality data confirmed (mean 1.19,
  var/mean 1.71, 40.9% zero-games).
- Are hits available through the current Odds API NHL market contract? **NO.**
- Is plus/minus a high-priority prop? **NO** — recommend defer, per Section K.
- Is plus/minus supported by the current Odds API? **NO.**
- Is anytime-goal pricing supportable from the goals model? **PARTIAL** — likely, pending a built
  goals model and confirmed settlement-semantics match.
- Is first-goal scorer ready for modeling? **NO** — needs an event-order model, not built or
  designed beyond naming the requirement.
- Does the dashboard now feel like a cohesive product rather than separate research pages?
  **PARTIAL** — the new Prop Registry page and consistent status/labeling conventions are a real
  step, but the larger UX unification (global shell, Today board, unified prop cards) was
  deliberately deferred (see audit report Section E) rather than built prematurely.
- Were all identified critical/high bugs fixed? **YES** (BUG-201, BUG-202, BUG-204 — all
  HIGH/CRITICAL-class; BUG-203/205 were LOW/MEDIUM and also fixed).
- Are live and historical/research data visually unambiguous? **YES** — every page's existing
  RESEARCH/LIVE/PROJECTED/CONFIRMED labeling discipline was audited this sprint and found intact;
  the new Prop Registry follows the same convention.
- Can all prop models feed a common downstream pricing interface? **YES** — the `PropPrediction`
  contract exists and is demonstrated by an SOG adapter; the actual live-pricing plumbing
  (no-vig, fair price, EV, decision) built for SOG in the prior slice is itself already
  market-key/threshold-generic and would need only a per-prop feature/model swap to serve blocks
  or assists once either gets a live market.
- Current full test result: **754 / 754 passing (0 failed, 0 errors, 0 skipped).**
- How many prop families are now validated? **3** (SOG, Blocked Shots, Assists).
- Which prop families are ready to become live market inputs when NHL markets return?
  **SOG** (`player_shots_on_goal`, market key confirmed) and **Assists** (`player_assists`,
  market key confirmed) — both validated AND have a real documented Odds API market key.
  Blocked shots is validated but has no documented live market key yet.
- What should the next single development slice be? **Build the POINTS model**, explicitly
  designed around the goals/assists correlation caution named in Section F — not a new UX build,
  not a fourth prop chosen just to raise the count.

---

### STOP AFTER PRESEASON PRODUCT SPRINT

No parlay optimizer was built. No bets were placed. The current NHL win-probability model was not
tuned. No unavailable live-market contract was bypassed. No restricted goalie site was called.
