# Prospective Validation Protocol

**Status: PRE-REGISTERED, PENDING 2026-27 DATA.** This document is the controlling protocol for ever promoting a `SHADOW_VALIDATED` model or context overlay to `OPERATIONAL_VALIDATED`. It is written and frozen *before* the season starts specifically so that no promotion decision can ever be tuned after seeing results.

## Why this exists

2024-25 and 2025-26 are both already-consumed historical evaluation seasons — every model and overlay in this project has already been scored against them. **Neither can ever become a genuine holdout again.** The only path to real prospective evidence is observations recorded *before* the corresponding game result is known, during the 2026-27 season (or a later season, if 2026-27 observations prove insufficient).

## What must be recorded, and when

At prediction time (before the game), for every eligible Goals/Points prediction, store immutably:

```
prediction_id            (unique, generated at prediction time)
model_version            (frozen results-file hash, e.g. player_goals_results.json's hash)
context_overlay_version  (frozen context_overlay_results.json hash)
policy_version            ("prop_decision_policy_v3" or whatever is current)
data_snapshot_references  (which corpus/roster/schedule snapshot fed this prediction)
timestamp_utc
player_id, team, opponent, game_date
raw_probability
context_adjusted_probability
coherent_probability
conservative_probability   (once wired -- see PRESEASON_ENGINE_READINESS_REPORT.md Section V)
context_state              (COLD_AND_TOI_DECLINE / NOT_ELIGIBLE)
market_price               (if a real DraftKings price exists at prediction time; else null)
market_no_vig_probability  (if available; else null)
```

**No retroactive backfilling after the result is known.** A prediction not recorded before the game does not count as a prospective observation, regardless of how confident the after-the-fact reconstruction would be.

## Minimum requirements before promotion review

Pre-registered now, not to be loosened later:

| Requirement | Minimum |
|---|---|
| Observations (COLD_AND_TOI_DECLINE, per prop) | 200 (matches this project's existing `MIN_STATE_SUPPORT`/`OVERLAY_MIN_EVAL_N` convention) |
| Unique players represented | 50 |
| Distinct game dates | 30 |
| Evaluation window | A full season, or a pre-declared partial-season checkpoint (e.g. "first 500 team-games") stated in advance, never chosen after seeing early results |
| Brier comparison | Adjusted < raw, game-clustered bootstrap 95% CI excludes 0 in the improvement direction |
| Log-loss comparison | Adjusted < raw, same bootstrap standard |
| Calibration comparison | `|adjusted_residual| < |raw_residual|` |
| Market-price requirement (if claiming edge) | Real captured DraftKings price at prediction time, `captured_at < game_start_time`; no edge claim permitted without this |
| CLV tracking | Recorded per observation once a real closing price exists; not required to be positive per-bet, tracked as a distribution |
| Confidence strata | Report separately for HIGH/MEDIUM/LOW; LOW stays WATCH_ONLY regardless of overlay result |
| State-frequency reporting | Report `%` of eligible player-games landing in `COLD_AND_TOI_DECLINE`, same as the historical validation did |
| Bootstrap methodology | Game-clustered (primary), date-clustered and player-clustered (sensitivity) — identical to every prior slice's discipline in this project |

## Explicit allowance for mixed outcomes

Promotion is evaluated **per overlay, independently**. A plausible, fully acceptable outcome:

```
GOALS overlay:  OPERATIONAL_VALIDATED
POINTS overlay: SHADOW_ONLY  (insufficient or inconsistent prospective evidence)
```

Neither overlay's outcome constrains the other's.

## What promotion does NOT mean

Promotion to `OPERATIONAL_VALIDATED` means the probability calibration is prospectively confirmed. It does **not**, by itself, mean:
- `decision_policy` v3 has been changed (that is a separate, later integration decision)
- a sportsbook edge exists (edge requires the market-price requirement above, independently)
- automatic betting is permitted (never, under any status, without separate explicit authorization)

## Review cadence

A promotion review may be conducted once the minimum requirements above are met — not before, and not on a fixed calendar date (e.g. "every Monday") that could create pressure to promote early on a thin sample.

## Addendum: SOG PP-role shadow overlay (Live Special-Teams Role Shadow sprint)

A second, independent overlay under this same protocol: `PLAYER_SOG_PP_ROLE_OVERLAY`
(`operational/sog_shadow_overlay.py`), currently `SHADOW_VALIDATED` on
**historical** data at thresholds **1+/2+/3+ only** (4+/5+/6+ never
cleared the historical bar and are not eligible for prospective
promotion review under any circumstances). Recorded prospectively via
`operational/record_sog_shadow_observation.py` into
`prospective_observations.db` (schema v3) alongside the unmodified
production SOG prediction — see `sog_shadow_raw_probability`,
`sog_shadow_conservative_probability`, `pp_role_state`,
`pp_role_certainty`, `pp_transition_state`,
`pp_games_since_transition`, `role_overlay_version` columns.

Pre-registered minimums, independent of the Goals/Points table above
(a different overlay, a different market, a different signal):

| Requirement | Minimum |
|---|---|
| Observations (any PP role state, SOG market) | 300 |
| Unique players represented | 75 |
| Distinct game dates | 30 |
| Evaluation window | A full season, or a pre-declared partial-season checkpoint stated in advance |
| Brier/log-loss comparison | Shadow < production raw, at each of 1+/2+/3+ independently, game-clustered bootstrap 95% CI excludes 0 |
| CLV comparison | Production vs. shadow **theoretical** CLV only — never a real shadow P&L, since no real bet is ever placed from the shadow probability |

**No early promotion.** Not after 20 shadow bets' worth of observations,
not after 50 observations, not after one statistically hot month. The
300/75/30 minimums above are hard floors, not targets to approach and
round up from. Promotion of this overlay is evaluated **independently**
of the Goals/Points overlays in this document — a good SOG shadow
result does not accelerate or justify promoting Goals/Points, and vice
versa.

**What promotion would NOT mean**, in addition to the three points
above: it would not mean Blocked Shots PK removal (`REJECTED`) or
Goals/Assists/Points role overlays (`PARTIAL`, per
`SPECIAL_TEAMS_ROLE_OVERLAY_VALIDATION_REPORT.md`) are revisited or
promoted by association.
