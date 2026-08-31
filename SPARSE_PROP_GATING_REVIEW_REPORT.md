# Unified Sparse-Prop LOW-Confidence Gating Review

**Decision: GOALS LOW joins the existing WATCH_ONLY policy, alongside ASSISTS and POINTS (retained
unchanged). SOG and BLOCKS remain unrestricted.** The evidence is not just directionally similar
across the three sparse props — the root-cause composition of their LOW buckets is nearly
identical in absolute terms (~19-20 game mean history, ~51% mean appearance rate, all independently
measured). No raw model was refit, no probability was changed, and the confidence framework itself
was not reopened.

---

## A. LOW-confidence metrics for SOG

Threshold 4+ (SOG's own headline threshold), n=87,989 total:

| Bucket | n | Share | Brier | Skill | Log loss | Calib. error |
|---|---|---|---|---|---|---|
| HIGH | 69,556 | 79.1% | 0.09727 | **+0.0953** | 0.3275 | -0.0094 |
| MEDIUM | 17,630 | 20.0% | 0.04166 | +0.0585 | 0.1696 | -0.0042 |
| LOW | 803 | 0.9% | 0.02248 | **+0.0271** | 0.1083 | -0.0009 |

**SOG LOW is healthy — positive skill, small calibration error.**

## B. LOW-confidence metrics for Blocks

Threshold 2+ (Blocks' own headline threshold), n=87,989 total:

| Bucket | n | Share | Brier | Skill |
|---|---|---|---|---|
| HIGH | 62,955 | 71.5% | 0.14930 | +0.1524 |
| MEDIUM | 23,966 | 27.2% | 0.11329 | +0.0907 |
| LOW | 1,068 | 1.2% | 0.10172 | **+0.0019** |

**Blocks LOW is essentially neutral — barely positive, not negative.** (Log loss/mean-pred were
not persisted per-bucket in the original Blocks result file — reported as unavailable, not
fabricated.)

## C. LOW-confidence metrics for Assists

Threshold 1+, n=87,989 total:

| Bucket | n | Share | Brier | Skill |
|---|---|---|---|---|
| HIGH | 60,444 | 68.7% | 0.18822 | +0.0452 |
| MEDIUM | 26,574 | 30.2% | 0.15031 | +0.0258 |
| LOW | 971 | 1.1% | 0.12090 | **-0.0426** |

## D. LOW-confidence metrics for Points

Threshold 1+, n=87,989 total:

| Bucket | n | Share | Brier | Skill |
|---|---|---|---|---|
| HIGH | 60,157 | 68.4% | 0.21985 | +0.0789 |
| MEDIUM | 26,692 | 30.3% | 0.18851 | +0.0426 |
| LOW | 1,140 | 1.3% | 0.15913 | **-0.0362** |

## E. LOW-confidence metrics for Goals

Threshold 1+, n=87,989 total:

| Bucket | n | Share | Brier | Skill |
|---|---|---|---|---|
| HIGH | 63,166 | 71.8% | 0.12861 | +0.0670 |
| MEDIUM | 23,951 | 27.2% | 0.10774 | +0.0487 |
| LOW | 872 | 1.0% | 0.08600 | **-0.0320** |

(Log loss and mean-predicted-probability were not persisted per-confidence-bucket in the original
Assists/Points/Goals result files — Brier/skill/actual-rate were, and are reported in full above;
missing fields are stated as unavailable, not invented.)

## F. Sparse-vs-higher-frequency comparison

Real event-rate/zero-rate data (not assumed), plus each prop's LOW-bucket skill:

| Prop | Mean events/game | Zero-rate | LOW skill | LOW share of predictions |
|---|---|---|---|---|
| SOG | 1.72 | 25.4% | **+0.027** | 0.9% |
| Blocks | 0.88 | 50.0% | **+0.002** | 1.2% |
| Points | 0.47 | 64.6% | **-0.036** | 1.3% |
| Assists | 0.30 | 76.0% | **-0.043** | 1.1% |
| Goals | 0.18 | 84.5% | **-0.032** | 1.0% |

**The split is clean and binary, not a smooth gradient**: the two least-sparse props (SOG, Blocks)
show non-negative LOW skill; the three sparsest props (Assists, Points, Goals) all show negative
LOW skill clustered tightly in a -0.032 to -0.043 band, despite differing individually in exact
mean/zero-rate ordering (Points is technically less sparse than Assists by zero-rate but slightly
worse by LOW skill — the three-way clustering is what matters, not a strict monotonic ranking).
Correlational, not asserted as proven causal — reported as the pattern actually observed.

## G. Goals LOW root-cause comparison

Real, directly re-measured (read-only re-scoring of the already-frozen Goals model, exactly the
Confidence Framework Redesign cycle's own reuse pattern — no refit):

| | Assists LOW | Points LOW | **Goals LOW** | (contrast) Goals HIGH |
|---|---|---|---|---|
| n | 971 | 1,140 | **872** | 63,166 |
| Mean history length | 19.3 games | 19.9 games | **19.3 games** | 195.2 games |
| Mean appearance rate | 0.509 | 0.517 | **0.511** | 0.981 |

**Goals LOW's composition is essentially identical to Assists' and Points'** — short history
(~19-20 games, a tenth of HIGH's ~195-197), and roughly half the lineup-appearance consistency of
HIGH (~51% vs. ~98%). Position/role mix was already ruled out as a driver in the original
Confidence Framework cycle and is not revisited here (Part 5's explicit instruction). The structure
is confirmed, not merely assumed to transfer.

## H. Assists policy recommendation

**RETAIN WATCH_ONLY.** No new evidence contradicts the original finding; LOW skill remains
materially negative (-0.043).

## I. Points policy recommendation

**RETAIN WATCH_ONLY.** Same reasoning; LOW skill remains materially negative (-0.036). Points'
`EMPIRICAL_BASELINE_REMAINS_CHAMPION` model status is unaffected — model maturity and decision
eligibility are tracked as separate registry fields, as before.

## J. Goals policy recommendation

**ADD WATCH_ONLY.** LOW skill is materially negative (-0.032), in the same range as Assists/Points,
with matching root-cause composition (Section G). SOG and Blocks are explicitly NOT restricted —
their LOW buckets remain non-negative, confirming this is not a blanket "LOW confidence is always
bad" rule but one that correctly isolates the genuinely weaker segment (Adoption Standard item 5).

## K. Final centralized policy table

`research/player_props/decision_policy.py::PROP_LOW_CONFIDENCE_CEILING`:

```python
PROP_LOW_CONFIDENCE_CEILING: dict[str, str] = {
    "ASSISTS": "WATCH",
    "POINTS": "WATCH",
    "GOALS": "WATCH",
}
```

SOG and Blocks have no entry — confirmed no restriction (the correct generic default). No parallel
policy logic was created anywhere; this is the same single table extended.

## L. Policy version

`POLICY_VERSION` bumped from `"prop_decision_policy_v1"` to `"prop_decision_policy_v2"`. v1's own
semantics were not deleted — the module's changelog comment documents exactly what changed and why.

## M. Anytime Goal inheritance

Implemented via a **market-family alias**, not a duplicated table entry — the structurally safer
choice (Part 12's explicit warning against divergence): `_MARKET_FAMILY_ALIASES = {"ANYTIME_GOAL":
"GOALS", "GOALS_OVER_0_5": "GOALS"}`. `gate_low_confidence()` and `parlay_eligible()` both resolve
through this alias before looking up the ceiling table, so `ANYTIME_GOAL`, `GOALS_OVER_0_5`, and
`GOALS` are **structurally guaranteed** to always gate identically — verified directly,
`test_3b_all_three_goals_family_labels_gate_identically`.

## N. Goals O/U inheritance

Same mechanism as M — `GOALS_OVER_0_5` is pre-registered in the same alias map now, before any live
market for it exists, so a future live-pricing integration cannot accidentally diverge from
`GOALS`'s own eligibility.

## O. Observation-ledger behavior

`gate_low_confidence()`'s return shape is unchanged in structure from v1
(`{mathematical_status, final_decision, policy_reason, policy_override, policy_version}`) — a
future ledger entry for a gated Goals prediction would read exactly like Part 18's own example:
`mathematical_status: "BET"`, `final_decision: "WATCH"`, `policy_override:
"LOW_CONFIDENCE_GOALS"`, `policy_version: "prop_decision_policy_v2"`. Verified end-to-end against a
real prediction (Shane Wright, SEA vs. STL, 2024-10-08 — a genuine LOW-confidence Goals row):
confidence resolves to `LOW`, and feeding a hypothetical `BET` through the gate correctly returns
`WATCH` with the expected reason and override code.

## P. Registry changes

| Prop | MODEL | 1+/2+ | CONFIDENCE | LOW BET ELIGIBILITY |
|---|---|---|---|---|
| SOG | VALIDATED | — | VALIDATED | NORMAL |
| BLOCKED_SHOTS | VALIDATED | — | VALIDATED | NORMAL |
| ASSISTS | VALIDATED | — | CONDITIONAL | **WATCH_ONLY** (unchanged) |
| POINTS | EMPIRICAL_BASELINE_REMAINS_CHAMPION | — | CONDITIONAL | **WATCH_ONLY** (unchanged) |
| GOALS | VALIDATED | 1+ VALIDATED / 2+ INSUFFICIENT DATA | CONDITIONAL | **WATCH_ONLY** (NEW) |
| ANYTIME_GOAL | SUPPORTED_BY_GOALS_MODEL | — | CONDITIONAL | **WATCH_ONLY** (NEW, inherited) |

No `model_status` was touched on any entry — Points was not upgraded, Goals was not downgraded, per
Part 14/15/29's explicit instructions carried forward from prior slices.

## Q. Dashboard changes

`dashboard/pages/12_Player_Goals_Research.py`'s confidence badge call now passes
`low_confidence_negative_skill=True, market_type="GOALS"` — the SAME shared
`render_confidence_badge()` component already used for Points, which folds the reliability warning
and the bet-ineligibility note into **one** caption (no duplicate boxes, verified structurally in
the prior gating-policy slice's own test and unchanged here). Verified end-to-end: the page loads
without error, and a real LOW-confidence Goals prediction (Shane Wright) correctly shows
`confidence: LOW` feeding into a `WATCH`-capped gate decision.

## R. Files modified

**New:**
- `research/run_sparse_prop_gating_review.py`
- `research/sparse_prop_gating_review_results.json` (generated)
- `tests/test_sparse_prop_gating_review.py` (31 tests)
- `SPARSE_PROP_GATING_REVIEW_REPORT.md` (this file)

**Modified:**
- `research/player_props/decision_policy.py` — `POLICY_VERSION` v1→v2; `PROP_LOW_CONFIDENCE_CEILING`
  gained `"GOALS": "WATCH"`; new `_MARKET_FAMILY_ALIASES` + `_canonical_market_family()` helper;
  `gate_low_confidence()`/`parlay_eligible()` now resolve through it
- `research/player_props/registry.py` — GOALS and ANYTIME_GOAL gained
  `low_confidence_bet_eligibility="WATCH_ONLY"`; no `model_status` changes
- `dashboard/pages/12_Player_Goals_Research.py` — confidence badge call updated to show the merged
  reliability/eligibility warning

**Unchanged (verified via `git status --porcelain`, no "M" entries):** every raw prop model file
and result file, `research/player_sog/count_models.py`, `research/confidence_lab/reliability.py`,
`pricing/engine.py`, `pricing/decision.py`, `config.py`, and `nhl.db`.

## S. Full test result

**1,002 / 1,002 passing** (971 prior + 31 new gating-review tests). Confirmed via
`python3 -m unittest discover tests`.

## T. Recommended next single development slice

The reliability/decision-policy thread across five props is now fully closed out — every validated
or partially-validated prop has an explicit, evidence-backed LOW-confidence eligibility rule, and
the underlying pattern (short history + low appearance rate driving negative LOW skill in sparse
props) has been confirmed a third time with matching root-cause composition. With that settled,
**build the PP Points model** — next in the sprint's original priority order, and the PP
shot-quality infrastructure built during the Goals refinement cycle (`pp.individual_xg`, `pp.sog`)
is directly reusable groundwork.

---

## Final Questions

**DOES SOG LOW SHOW NEGATIVE SKILL?** NO (+0.027).

**DOES BLOCKS LOW SHOW NEGATIVE SKILL?** NO (+0.002, essentially neutral).

**DOES ASSISTS LOW SHOW NEGATIVE SKILL?** YES (-0.043).

**DOES POINTS LOW SHOW NEGATIVE SKILL?** YES (-0.036).

**DOES GOALS LOW SHOW NEGATIVE SKILL?** YES (-0.032).

**IS THE FAILURE CONCENTRATED IN SPARSER SCORING PROPS?** YES — a clean binary split, not a smooth
gradient (Section F).

**SHOULD ASSISTS LOW REMAIN WATCH_ONLY?** YES.

**SHOULD POINTS LOW REMAIN WATCH_ONLY?** YES.

**SHOULD GOALS LOW BECOME WATCH_ONLY?** YES.

**IF YES, DOES ANYTIME GOAL INHERIT THE SAME GATE?** YES (via the market-family alias, Section M).

**IF YES, DOES GOALS OVER 0.5 INHERIT THE SAME GATE?** YES (same alias, pre-registered for when
that market label exists).

**DID ANY RAW MODEL PROBABILITY CHANGE?** NO.

**DID THE CONFIDENCE FRAMEWORK CHANGE?** NO.

**DID PRICING MATH CHANGE?** NO.

**WAS THE PRODUCTION NHL WIN MODEL CHANGED?** NO.

**CURRENT FULL TEST RESULT?** 1,002 / 1,002.

**WHAT SHOULD THE NEXT SINGLE DEVELOPMENT SLICE BE?** Build the PP Points model — the next prop in
the sprint's original priority order, now with real PP shot-quality infrastructure already built
and tested as reusable groundwork.
