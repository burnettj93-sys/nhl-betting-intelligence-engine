# Low-Confidence Assists / Points Gating Policy

A narrow, centralized BET-eligibility policy: LOW-confidence Assists and Points predictions can no
longer resolve to `BET`, regardless of how attractive their edge/EV would otherwise look. Raw
probability, conservative probability, edge, EV, fair price, and the confidence framework itself
are all untouched — verified structurally and via `git status`, not just asserted.

## A. Current decision-layer audit

Read directly from source (not summarized):

- **`pricing/engine.py`** — the PRODUCTION NHL moneyline decision engine (`evaluate_moneyline_for_game`).
  Game-level, not a player prop, and on this project's permanent do-not-touch list. Not used or
  imported anywhere in this slice.
- **`pricing/decision.py`** — persists production moneyline predictions/decisions to `nhl.db`.
  Also untouched, also not a player-prop file.
- **`research/live_sog_pricing/pricing.py`** — the ONLY existing player-prop pricing/decision code.
  SOG-only. Its `decide(conservative_edge, ev, raw_edge, confidence, lineup_status)` already
  implements exactly the audited precedence this slice needed to understand:
  1. Compute a **base action** from edge/EV alone: `BET` if both `conservative_edge >=
     config.MIN_CONSERVATIVE_EDGE` and `ev >= config.MIN_EV`; `WATCH` if `raw_edge > 0` but the
     BET bar isn't cleared; `PASS` otherwise.
  2. `quality_ok = confidence != "LOW"` — LOW confidence (or unconfirmed lineup) downgrades a
     would-be `BET`/`WATCH` to **`WAIT`**; `PASS` never changes.
  3. `price_observation()` separately returns `DATA_UNAVAILABLE` upstream of all of this, before
     `decide()` is even called, when a quote is stale beyond `odds_math.dynamic_max_staleness_minutes()`.

  **Assists and Points have no equivalent pricing/decision code at all** — both are
  `live_market_support="NOT_CURRENTLY_AVAILABLE"` in the registry, confirmed directly. There is
  nothing existing for either prop to wire a policy into yet.

**Audited precedence used (Part 3):** `DATA_UNAVAILABLE` (upstream staleness) → `WAIT` (upstream
lineup/data uncertainty) → **confidence eligibility gate (this slice)** → the base edge/EV action.
This matches SOG's own already-working precedence exactly — this slice did not invent a new model,
it generalized the one already proven correct.

## B. Exact policy implementation

New module: [`research/player_props/decision_policy.py`](research/player_props/decision_policy.py).
One function, `gate_low_confidence(market_type, confidence, mathematical_status,
mathematical_reason="")`, and one central table:

```python
PROP_LOW_CONFIDENCE_CEILING = {"ASSISTS": "WATCH", "POINTS": "WATCH"}
```

Behavior (verified by 38 new tests):
- `mathematical_status` already `PASS`, `WAIT`, or `DATA_UNAVAILABLE` → passed through **completely
  unchanged**, regardless of confidence (Part 4/11).
- `confidence != "LOW"`, or `market_type` not in the ceiling table (SOG, Blocks, anything else) →
  passed through **completely unchanged**.
- `confidence == "LOW"` and `market_type` has a ceiling and `mathematical_status` exceeds it (i.e.
  `BET` when the ceiling is `WATCH`) → **capped at the ceiling**, with an explicit
  `policy_reason` and a `policy_override` code (`"LOW_CONFIDENCE_ASSISTS"` / `"LOW_CONFIDENCE_POINTS"`).

Deliberately absent: SOG and Blocked Shots have **no entry** in the ceiling table — the correct
generic default (no restriction) rather than an explicit `"NORMAL"` sentinel needing special-casing.
This module never imports `config`, `pricing.odds_math`, `pricing.engine`, or `pricing.decision`,
and contains no arithmetic beyond string concatenation for the reason text (verified via AST,
`test_15_no_edge_math_in_module` / `test_16_never_imports_odds_math_or_pricing_engine`).

## C. WATCH vs. WAIT precedence

Implemented exactly per Part 4's conceptual model: this module **never produces `WAIT`** — it only
ever narrows `BET`/`WATCH` down to `WATCH`, or passes an already-terminal status through unchanged.
An upstream `WAIT` (unresolved lineup/data uncertainty, decided by whatever future
Assists/Points pricing engine mirrors `research/live_sog_pricing/pricing.py`'s own staleness/lineup
checks) always takes precedence and is never touched or "un-gated" by this layer (verified,
`test_10_upstream_wait_precedence`).

## D. Files modified

**New:**
- `research/player_props/decision_policy.py`
- `tests/test_decision_policy.py` (38 tests)
- `LOW_CONFIDENCE_GATING_POLICY_REPORT.md` (this file)

**Modified:**
- `research/player_props/registry.py` — added `low_confidence_bet_eligibility` field
  (`"WATCH_ONLY"` for Assists/Points, `"NORMAL"` default for everything else); `model_status` on
  every entry untouched (Points remains exactly `EMPIRICAL_BASELINE_REMAINS_CHAMPION`, per Part 15)
- `dashboard/components.py::render_confidence_badge()` — accepts an optional `market_type`; when
  the registry marks that market `WATCH_ONLY`, the policy note is folded into the SAME LOW-
  confidence warning caption already shown (Part 13 — one coherent explanation, never two boxes;
  verified structurally, `test_25b_single_caption_call_not_two_separate_warning_boxes`)
- `dashboard/pages/11_Player_Points_Research.py` — passes `market_type="POINTS"` to the badge call

**Unchanged (verified via `git status --porcelain`, no "M" entries):**
`research/live_sog_pricing/pricing.py`, `research/run_player_sog_model.py`,
`research/run_player_blocks_model.py`, `research/run_player_assists_model.py`,
`research/run_player_points_model.py`, `research/player_points_results.json`,
`pricing/engine.py`, `pricing/decision.py`, `config.py`.

## E. Policy version

`research.player_props.decision_policy.POLICY_VERSION = "prop_decision_policy_v1"` — returned on
every `gate_low_confidence()` call, ready for a future observation ledger to store alongside its
mathematical/final decision fields (Section H).

## F. Registry changes

| Prop | MODEL | CONFIDENCE | LOW BET ELIGIBILITY |
|---|---|---|---|
| SOG | VALIDATED | VALIDATED | NORMAL |
| BLOCKED_SHOTS | VALIDATED | VALIDATED | NORMAL |
| ASSISTS | VALIDATED | CONDITIONAL | **WATCH_ONLY** |
| POINTS | EMPIRICAL_BASELINE_REMAINS_CHAMPION | CONDITIONAL | **WATCH_ONLY** |

## G. Dashboard changes

`render_confidence_badge()` now takes an optional `market_type`; for Points (the only prop with a
live "project a player" page), a LOW-confidence badge shows one merged caption: the existing
negative-skill warning **plus** a one-line reliability-gate note ("LOW-confidence Points predictions
are not currently BET-eligible (WATCH only) under future live pricing"). Verified end-to-end with a
real prediction (Shane Wright, SEA vs. STL, 2024-10-08 — a genuine LOW-confidence Points row from
this project's own eval data): confidence resolves to `LOW`, and feeding a hypothetical `BET`
through `gate_low_confidence("POINTS", "LOW", "BET", ...)` correctly returns `final_decision:
"WATCH"` with the expected reason and override code. SOG's page passes no `market_type` change (its
existing marginal-weakness comment is preserved) since SOG carries no restriction.

## H. Observation-ledger changes

No ledger table exists yet for Assists/Points (no live pricing engine exists to write one from —
Section A). Per Part 17's explicit instruction to document rather than build speculative
architecture, `gate_low_confidence()`'s return shape **is** the schema a future ledger must adopt:

```json
{"mathematical_status": "BET", "final_decision": "WATCH",
 "policy_reason": "would otherwise be BET (...), but POINTS LOW-confidence predictions have "
                  "demonstrated negative historical model skill -- not BET-eligible under policy "
                  "prop_decision_policy_v1.",
 "policy_override": "LOW_CONFIDENCE_POINTS", "policy_version": "prop_decision_policy_v1"}
```

The underlying pricing result (`mathematical_status`) is always preserved alongside the gated
`final_decision` — never overwritten (verified, `test_22_mathematical_status_preserved_alongside_final_decision`).

## I. Historical trigger frequency

Using the real, already-computed Assists/Points evaluation data (`research/confidence_framework_results.json`,
2024-25 + 2025-26 combined, 87,989 rows each):

| Prop | Total predictions | LOW-confidence count | LOW-confidence % |
|---|---|---|---|
| Assists | 87,989 | 971 | **1.10%** |
| Points | 87,989 | 1,140 | **1.30%** |

**Per Part 11's explicit instruction: true historical BET-eligibility (how many of these would
otherwise have cleared a real edge/EV bar) cannot be reconstructed** — neither Assists nor Points
has ever had a live DraftKings market posted, so no historical sportsbook price exists to compute
edge/EV against. No historical price was invented to fill this gap. This table reports model-
reliability gating frequency only, exactly as scoped.

## J. Confirmation: pricing mathematics unchanged

`decision_policy.py` contains no reference to `model_probability`, `conservative_probability`,
`raw_edge`, `conservative_edge`, `raw_ev`, `conservative_ev`, or `fair_price` anywhere in its source
(verified, `test_13`/`test_14`), imports neither `pricing.odds_math` nor `config` (verified,
`test_16`/`test_17`), and contains no arithmetic operator beyond string concatenation (verified via
AST, `test_15`). The gate function's own inputs/outputs are pure status strings — never a number
(verified, `test_18`/`test_19`).

## K. Confirmation: confidence framework unchanged

`cm.confidence_score()` (the shared, already-validated confidence architecture from the prior
slice) is neither imported nor referenced anywhere in `decision_policy.py` (verified,
`test_27_confidence_score_function_unmodified_and_unimported_here`) — this module takes an
already-computed confidence LABEL as a plain string input and never computes one itself.

## L. Full test result

**895 / 895 passing** (857 prior + 38 new gating-policy tests). Confirmed via
`python3 -m unittest discover tests`.

## M. Recommended next single development slice

The confidence/decision-policy work across the last three slices (redesign attempt → kept current
→ gating policy implemented) has closed out the reliability side of this engine's player-prop
infrastructure. With that settled, the highest-value next step reverts to model coverage: **build
the GOALS model**, the next prop in the sprint's original priority order, now with the most mature
methodology this project has had at any point — rolling walk-forward validation, hierarchical
empirical baselines where a parametric GLM underperforms, honest "reused historical data" labeling,
and a working decision-eligibility policy layer ready to gate it the moment it needs one.

---

## Final Questions

**CAN LOW-CONFIDENCE ASSISTS RETURN BET?** NO.

**CAN LOW-CONFIDENCE POINTS RETURN BET?** NO.

**CAN LOW-CONFIDENCE SOG STILL FOLLOW THE NORMAL DECISION POLICY?** YES.

**CAN LOW-CONFIDENCE BLOCKS STILL FOLLOW THE NORMAL DECISION POLICY?** YES.

**DOES THE GATE CHANGE RAW MODEL PROBABILITY?** NO.

**DOES THE GATE CHANGE CONSERVATIVE PROBABILITY?** NO.

**DOES THE GATE CHANGE EDGE?** NO.

**DOES THE GATE CHANGE EV?** NO.

**IS THE GATING POLICY CENTRALLY VERSIONED?** YES — `POLICY_VERSION = "prop_decision_policy_v1"`.

**IS THE POLICY REASON STORED WITH FUTURE OBSERVATIONS?** YES — every `gate_low_confidence()` call
returns a `policy_reason`, ready for a future ledger (Section H); no ledger table exists yet since
no live pricing engine exists for Assists/Points to write one from.

**WAS THE CONFIDENCE FRAMEWORK CHANGED?** NO.

**WERE ANY RAW PROP MODELS REFIT?** NO.

**WAS THE NHL WIN MODEL CHANGED?** NO.

**CURRENT FULL TEST RESULT?** 895 / 895.

**WHAT SHOULD THE NEXT SINGLE DEVELOPMENT SLICE BE?** Build the GOALS model — the next prop in the
sprint's original priority order, now backed by this project's most complete methodology to date.
