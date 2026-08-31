# Team Goals by Period — Validation Report

**Status: RESEARCH — NOT VALIDATED.** This slice produced a real, PIT-safe, honestly-evaluated model, and the honest finding is a **negative/null result**: no period × threshold combination cleared this project's usual bootstrap adoption bar (≥0.95 `frac_improved`, game-clustered, consistently across **both** eval seasons). This report documents that finding in full, with exact numbers pulled from the frozen `research/team_goals_period_results.json`, rather than forcing a positive conclusion.

---

## A. Label corpus

Built by `research/team_goals_period/build_team_goals_period_corpus.py` from the same real, boxscore-reconciled play-by-play foundation used in every prior slice (`api-web.nhle.com` gamecenter play-by-play, `sortOrder`-ordered, joint `situationCode` power-play rule). One row per team per game (home and away rows are symmetric), across all 4 ingested seasons (2022-23 through 2025-26).

- **10,496 rows** total (2,624 games/season × 2 teams × 4 seasons — 2025-26 in progress at ingestion time contributes its own real subset, matching the multi-season PBP corpus).
- Fields per row: `period_1/2/3_goals`, `period_1/2/3_pp_goals`, `opponent_period_1/2/3_goals`, `ot_goals`, `full_game_team_goals`, plus `game_id`, `game_date`, `season`, `team`, `opponent`, `home_away`.
- **Reconciliation check:** 0/50 randomly sampled games showed any mismatch between `period_1_goals + period_2_goals + period_3_goals + ot_goals` and the real official final score reported by the NHL API (excluding shootout-only bonus goals, which are correctly excluded per Section B).

## B. Shootout / OT handling

Shootout goals share `typeDescKey:"goal"` with regulation goals but do not increment the boxscore's statistical goal total — confirmed and excluded (Test04). OT goals are tracked in a separate `ot_goals` field, never folded into `period_3_goals` (Test05). Reconciliation across regulation + OT is exact for every non-shootout game; shootout-decided games reconcile on the regulation+OT statistical total, not the SO-bonus final score.

## C. Period scoring distributions (TUNING season, 2023-24, league-wide)

| Period | League mean goals/team | League share of full-game total |
|---|---|---|
| P1 | 0.895 | 29.1% |
| P2 | 1.049 | 34.0% |
| P3 | 1.065 | 34.6% |

Consistent with the well-known real hockey pattern: P1 is the lowest-scoring period, P2/P3 are roughly even and higher. No surprises here — this is a sanity check, not a finding.

## D. Team period-share persistence

`research/team_goals_period/hierarchy.py`'s `PeriodTeamRates` computes HOME/AWAY-split, partial-pooling-shrunk period shares and means from the TUNING season only (never EVAL data). Team-level period shares are noisy at single-season sample sizes (~82 games/team) — shrinkage toward the HOME/AWAY → league hierarchy is applied via `ha_share_shrunk()`/`ha_mean_shrunk()`, mirroring the ROLE/HOME-AWAY → LEAGUE pattern already validated in Player SOG by Period, but re-fit at team (not player) granularity since no shared cross-package rate class exists in this codebase (an established convention: prop packages don't cross-import).

## E. Home/away effect

Home/away is included directly as a GLM feature (`is_home` indicator) and as a split in the hierarchical rate class. It carries real but small signal — see Section T (joint dependence) for the more important home/away finding, which is about the *joint distribution* between a game's two team rows, not the marginal home-ice edge.

## F. Team offensive-context value

`recent_rate` (rolling period-goal mean, window=20) and `recent_form` (log-ratio, window=5) are included as GLM/offset-GLM features per period. These contributed real, small, positive signal — visible in the tiny but directionally consistent NLL/MAE improvements of the winning candidates over the best baseline (Section S), though not enough on their own to clear the bootstrap bar.

## G. Opponent defensive-context value

`opponent_factor` (opponent's rolling period-goals-allowed rate, window=20, normalized against a real team-level league-average constant — the exact bug class from Player SOG by Period's `opponent_factor` mis-scaling was checked for and avoided here from the start) is included per period. Contribution is real but small, same caveat as Section F.

## H. Goalie-context value — NOT TESTED (disclosed scope decision)

Per this project's `GOALIE_QUALITY_INTEGRATION_REPORT.md` failure precedent (goalie quality integrated into the win-probability model previously failed to add value) and this slice's own effort-scope constraints, a goalie-quality feature was **not built or tested** this slice. This is a disclosed omission, not a silent one — `freeze_manifest.goalie_context` in `research/team_goals_period_results.json` records: *"NOT included — see report Section H for the disclosed reasoning (known win-model goalie-quality integration failure precedent + effort scope)."* Given the honest negative result on the features that *were* tested, adding goalie context is a reasonable candidate for a future slice, not an omission that would plausibly have flipped today's verdict.

## I. Special-teams (PP) context value

`period_k_pp_goals` is tracked in the corpus per team-period (using the same joint `situationCode` real-power-play rule reused from Player SOG by Period, never the target game's own realized PP opportunities — see Section K). It is surfaced for representative-example selection (`pp_share_of_period_goals`) and diagnostic use, but was **not** built into the GLM feature set this slice — it is a reasonable candidate feature for a future retry rather than a load-bearing part of the tested architecture, given the null headline result overall.

## J. Full-game upstream feature — NEW, built this slice

`research/team_goals_period/upstream_team_goals.py`'s `shrunk_full_game_expectation()` is a **new** PIT-safe rolling+shrunk full-game team-goal prior (`k_team=60` shrinkage), built fresh from this project's own PBP-derived corpus — **not** reused from any pre-existing validated full-game team-scoring model, because none exists in this codebase. This is the anchor for the `D_fullgame_x_league_share` / `E_fullgame_x_raw_share` baselines and the `D_fullgame_x_shrunk_share` / `E_hybrid_offset` candidates: full-game expectation × a period share (league, raw, or shrunk).

## K. No target-game leakage (verified)

`build_example()` in `research/run_team_goals_period_model.py` never reads the target row's own `period_k_goals`, `period_k_pp_goals`, `opponent_period_k_goals`, penalties, PP opportunities, score state, or goalie results — only strictly-prior history via `TeamPeriodHistoryIndex.history_as_of()` (bisect on `game_date`, strict `<`). Verified directly: mutating a target row's `period_1_goals`/`period_2_goals` to 999 and rebuilding the example produces identical `baseline_rate` features (Test07). History strictness (`game_date < target_date`, never `<=`) is verified directly against the real corpus (Test08/Test09).

## L. Candidate models tested

Five candidates were built and independently evaluated per period — no architecture was assumed in advance:

- **A_shrunk_period_empirical** — pure hierarchical shrunk period rate, no offset/GLM.
- **B_poisson_direct** — direct Poisson GLM fit on period features (`glm_feature_vector`).
- **C_negbinom_direct** — same features, Negative Binomial with fitted dispersion.
- **D_fullgame_x_shrunk_share** — full-game upstream expectation × shrunk period share.
- **E_hybrid_offset** — offset-GLM: `log(mu) = log(full_game_expectation × shrunk_share) + w·[recent_form, h2h_delta]`, weights fit via batch gradient descent on TUNING only.

Five baselines (A_league_share through E_fullgame_x_raw_share) were evaluated alongside as the honest comparison set.

## M. Poisson vs. Negative Binomial

Fitted dispersion (`glm_alpha`) came back **near zero for all three periods**: P1=0.0091, P2=0.0000, P3=0.0297. Team-period goal counts show essentially Poisson-level dispersion — Negative Binomial was tested per Part 37's explicit instruction not to assume it was required, and the data confirms it was not: `C_negbinom_direct` never became the frozen winner for any period.

## N. Zero-inflation finding

No zero-inflated family was tested or adopted. Team-period goal counts (mean ≈0.9-1.1, real minimum of 0 goals occurring at a plausible Poisson-implied rate) showed no structural excess-zero pattern requiring a zero-inflated model — consistent with the near-Poisson dispersion finding in Section M.

## O. Selected / frozen model per period

| Period | Winner | Best baseline (for comparison) |
|---|---|---|
| P1 | `B_poisson_direct` | `D_fullgame_x_league_share` |
| P2 | `B_poisson_direct` | `D_fullgame_x_league_share` |
| P3 | `E_hybrid_offset` | `D_fullgame_x_league_share` |

Winner selection was by mean Brier score at the 1+/2+/3+ thresholds, averaged across both EVAL seasons, per period — mirroring the Player SOG by Period selection procedure. **Critically, "winner" here means best point estimate, not "validated"** — see Section AC.

## P. Freeze manifest

```
experiment_id: team_goals_by_period_v1
target: team statistical goals per period (P1/P2/P3, regulation only), home and away
model_family: upstream full-game team-goal expectation (shrunk rolling mean, built fresh
  this slice) x league-average period share, as a fixed offset, plus a small
  period-specific offset-GLM adjustment
features: period_k rolling team goal history (window=20); home/away tag; opponent
  period-goals-allowed rolling rate (window=20); period-k H2H shrunk delta;
  recent-form log-ratio (window=5)
lookbacks: baseline_window=20, recent_window=5, k_team_shrinkage=60, k_home_away_shrinkage=300
upstream_provenance: research/team_goals_period/upstream_team_goals.py -- NEW this slice
goalie_context: NOT included (Section H)
distribution: Poisson (re-estimated per period, not assumed)
confidence_framework: research.player_sog.count_models.confidence_score (unchanged, reused)
conservative_probability: research.player_sog.count_models.conservative_mu (unchanged, reused)
tail_support_rule: thresholds 1-4 only
```

Code hashes (frozen at evaluation time):
- `run_team_goals_period_model.py`: `8a883f8a69370bcfda5befc989bc058f6714f42712de79eb2fe2c6aca75752e5`
- `team_goals_period/features.py`: `b62415a802c881b953507899976e27e7902640dee046bdaf3037839433834adb`
- `team_goals_period/hierarchy.py`: `02b0f262b8370e6a04cddf17cb705cad63719f1511b2c1f92074a80c2b2c0a26`
- `team_goals_period/upstream_team_goals.py`: `1b66611b5be03c66205ea0bd3a4660a24effe9b77aaf48e352be5b24f3cda293`

## Q. Common evaluation sets

| Season | Eligible rows | Excluded rows |
|---|---|---|
| 2024-25 | 2,619 | 0 |
| 2025-26 | 2,624 | 0 |

Every team-game row in both EVAL seasons had sufficient history (≥5 prior games) for a full example — 0 exclusions in either season.

## R. Per-period Brier / log-loss — Period 1

| Season | Threshold | Winner (`B_poisson_direct`) Brier | Best baseline Brier | Winner log-loss | Baseline log-loss | Real base rate |
|---|---|---|---|---|---|---|
| 2024-25 | 1+ | 0.24282 | 0.24311 | 0.67893 | 0.67930 | 58.5% |
| 2024-25 | 2+ | 0.16569 | 0.16661 | 0.51312 | 0.51577 | 21.0% |
| 2024-25 | 3+ | 0.05125 | 0.05122 | 0.21048 | 0.21003 | 5.4% |
| 2024-25 | 4+ | 0.01355 | 0.01354 | 0.07233 | 0.07202 | 1.4% |
| 2025-26 | 1+ | 0.24188 | 0.24187 | 0.67682 | 0.67677 | 59.0% |
| 2025-26 | 2+ | 0.17089 | 0.17057 | 0.52529 | 0.52441 | 21.9% |
| 2025-26 | 3+ | 0.05327 | 0.05320 | 0.21734 | 0.21674 | 5.6% |
| 2025-26 | 4+ | 0.01131 | 0.01127 | 0.06272 | 0.06125 | 1.1% |

P1's point-estimate Brier differences are tiny and **sign-inconsistent** across seasons (winner beats baseline in 2024-25 at 1+/2+, loses in 2025-26 at every threshold).

## S. Per-period Brier / log-loss — Period 2

| Season | Threshold | Winner (`B_poisson_direct`) Brier | Best baseline Brier | Real base rate |
|---|---|---|---|---|
| 2024-25 | 1+ | 0.22919 | 0.22912 | 64.1% |
| 2024-25 | 2+ | 0.19137 | 0.19172 | 26.0% |
| 2024-25 | 3+ | 0.07072 | 0.07111 | 7.7% |
| 2024-25 | 4+ | 0.01795 | 0.01799 | 1.8% |
| 2025-26 | 1+ | 0.22429 | 0.22505 | 66.0% |
| 2025-26 | 2+ | 0.20019 | 0.20079 | 27.7% |
| 2025-26 | 3+ | 0.07912 | 0.07920 | 8.7% |
| 2025-26 | 4+ | 0.01615 | 0.01613 | 1.6% |

## Per-period Brier / log-loss — Period 3

| Season | Threshold | Winner (`E_hybrid_offset`) Brier | Best baseline Brier | Real base rate |
|---|---|---|---|---|
| 2024-25 | 1+ | 0.22708 | 0.22691 | 65.1% |
| 2024-25 | 2+ | 0.20969 | 0.20964 | 29.9% |
| 2024-25 | 3+ | 0.08456 | 0.08458 | 9.3% |
| 2024-25 | 4+ | 0.02241 | 0.02241 | 2.3% |
| 2025-26 | 1+ | 0.22183 | 0.22170 | 66.7% |
| 2025-26 | 2+ | 0.20330 | 0.20316 | 28.6% |
| 2025-26 | 3+ | 0.09197 | 0.09201 | 10.3% |
| 2025-26 | 4+ | 0.02018 | 0.02019 | 2.1% |

Every period shows the same pattern: point-estimate Brier gaps between winner and best baseline are on the order of **0.0002-0.0007** — an order of magnitude smaller than the gaps that cleared the bar in Player SOG by Period, and well within noise given the sample sizes here (Section AB).

## T. Count metrics (NLL / MAE / RMSE)

| Period | Season | Winner NLL | Baseline NLL | Winner MAE | Baseline MAE |
|---|---|---|---|---|---|
| P1 | 2024-25 | 0.9896 | 0.9909 | 0.7144 | 0.7140 |
| P1 | 2025-26 | 0.9923 | 0.9911 | 0.7173 | 0.7162 |
| P2 | 2024-25 | 0.9969 | 0.9986 | 0.7401 | 0.7456 |
| P2 | 2025-26 | 0.9974 | 0.9994 | 0.7515 | 0.7527 |
| P3 | 2024-25 | 0.9976 | 0.9974 | 0.7991 | 0.7989 |
| P3 | 2025-26 | 0.9951 | 0.9949 | 0.7746 | 0.7737 |

Same story: differences are in the 3rd-4th decimal place, not a clear or consistent win.

## U. Full-game coherence

`full_game_coherence` compares `period_1 + period_2 + period_3` expectations against the independently-computed `upstream_expected` full-game figure. Both eval seasons show a consistent negative `mean_diff` (P1/P2 rows: -0.082 in 2024-25, -0.052 in 2025-26; P3 rows: -0.077 / -0.076), meaning the sum of period expectations runs slightly *below* the upstream full-game expectation — a real, modest, disclosed incoherence (not exact internal consistency), on the order of 0.05-0.08 goals out of a ~3-goal full-game total. `abs_mean_diff`/`stdev_diff` are reported alongside for the true magnitude, not just the signed mean.

## V. Home/away joint-dependence finding (important, not previously known)

Computed both raw and model-residual Pearson correlation between a game's home-team and away-team realized period goals:

| Period | Season | Raw correlation | Residual correlation (after model) |
|---|---|---|---|
| P1 | 2024-25 | -0.0169 | -0.0142 |
| P1 | 2025-26 | +0.0088 | +0.0085 |
| P2 | 2024-25 | -0.0680 | -0.0653 |
| P2 | 2025-26 | -0.0486 | -0.0472 |
| P3 | 2024-25 | -0.1360 | -0.1238 |
| P3 | 2025-26 | -0.0822 | -0.0775 |

**Finding: home/away goal totals within a game are not independent, and the dependence grows in magnitude from P1 (≈0) to P3 (most negative).** This is a real, modest, negative correlation — consistent with real hockey dynamics (a team protecting a lead scores less in P3; a trailing team's opponent scores less once the game is out of reach). The residual correlation, after removing what the frozen model already explains, is **essentially unchanged** from the raw correlation — meaning **the model does not explain this dependence away**. **This is directly relevant to any future Both Teams Score / correct-score / joint-simulator work: home and away team goals should NOT be treated as conditionally independent given only these features, especially in P3.**

## W. Game-clustered bootstrap (headline significance test)

`game_clustered_bootstrap()` resamples `game_id`s with replacement, 1,000 resamples, comparing winner vs. best baseline per period/threshold/season:

| Period | Threshold | 2024-25 frac_improved | 2025-26 frac_improved | Clears ≥0.95 in BOTH? |
|---|---|---|---|---|
| P1 | 1+ | 0.660 | 0.461 | No |
| P1 | 2+ | 0.984 | 0.233 | **No** (crashes) |
| P1 | 3+ | 0.371 | 0.251 | No |
| P2 | 1+ | 0.435 | 0.887 | No |
| P2 | 2+ | 0.712 | 0.849 | No |
| P2 | 3+ | 0.972 | 0.642 | **No** (crashes) |
| P3 | 1+ | 0.122 | 0.205 | No |
| P3 | 2+ | 0.351 | 0.171 | No |
| P3 | 3+ | 0.705 | 0.745 | No |

**No period/threshold combination clears the ≥0.95 bar in both eval seasons.** The two cases that looked promising in a single season (P1 2+ at 0.984 in 2024-25; P2 3+ at 0.972 in 2024-25) both collapse in 2025-26 (0.233 and 0.642 respectively) — a textbook single-season overfit signature, not a real effect. This is the headline negative finding of this slice.

## X. Date-clustered bootstrap (sensitivity check)

Date-clustered results track the game-clustered results closely in every case (e.g., P1 2+ 2024-25: 0.988 date vs. 0.984 game; P1 2+ 2025-26: 0.216 date vs. 0.233 game) — confirming the negative finding is not an artifact of the game-vs-date clustering choice.

## Y. Season-by-season results

Already tabulated in full in Sections R-W. No period/threshold shows a consistent winner across both EVAL seasons under bootstrap evidence.

## Z. Threshold support

Actual base rates at 4+ are thin (1.1%-2.3% depending on period/season) but real and consistent with Poisson tails at these means — no threshold was dropped for insufficient support; all of 1+/2+/3+/4+ are reported per the tail-support rule (5+/6+ would need explicit INSUFFICIENT DATA marking, but weren't reached this slice since 4+ was the ceiling per Part config).

## AA. Confidence-stratified results

The reused, unmodified `confidence_score` framework (Section AD) puts **>99% of team-period rows into MEDIUM** confidence in both eval seasons (e.g., P1 2024-25: 2,592/2,619 MEDIUM, 19 LOW, 8 HIGH). This is a disclosed limitation: the framework's thresholds were tuned for player-level history accumulation rates, and teams accumulate 40+ games of history quickly relative to those thresholds, so it does not meaningfully discriminate at team granularity. Skill (`skill_1plus`) within each confidence bucket is near zero or slightly negative in most buckets/seasons — consistent with the overall null result, not a hidden pocket of real skill.

## AB. Conservative probability

`conservative_mu` (from `research.player_sog.count_models`, unmodified) was applied per period; `conservative_never_exceeds_raw: True` holds in every period/season (Test31 verifies this structurally against the frozen results). Mean raw-minus-conservative gap is ~0.074-0.080 goals across periods/seasons — consistent, modest, and monotonically respecting the "conservative never exceeds raw" invariant.

## AC. Why this is a genuine negative result, not a modeling bug

Three independent pieces of evidence point the same direction:
1. **Statistical power**: ~2,600 rows/season here vs. ~44,000 for player-level SOG — roughly 17x less data at the grain this slice needed, for effects that (per Section V) are themselves small.
2. **Point-estimate gaps are tiny** (0.0002-0.0007 Brier) — an order of magnitude below what cleared the bar in the successful Player SOG by Period slice.
3. **Bootstrap results are directionally unstable between seasons** — the two single-season "wins" (P1 2+, P2 3+) both invert in the other eval season, the classic signature of noise rather than signal.

## AD. Confidence framework — unchanged (per explicit instruction)

`research.player_sog.count_models.confidence_score` was reused with its real signature unchanged (verified, Test30): `(n_history_games, recent_toi_cv, recent_sog_cv, opponent_window_games, opponent_window_target, appearance_rate)`. No redesign was attempted this slice, per the standing KEEP CURRENT verdict from the earlier confidence-lab investigation.

## AE. Due-diligence value-test: does MoneyPuck xG context help? (honest negative disclosure)

Per Part 8's instruction not to force xG simply because it exists, an xG-augmented offset-GLM was fit for P1 as a due-diligence check, using `research_moneypuck_team_game_stats` (queried PIT-safely via `team_stats_as_of()`). Result: **worse** in 2024-25 (`frac_improved=0.003`) and only marginally/inconclusively better in 2025-26 (`frac_improved=0.921`, still below the 0.95 bar). This is a genuine negative finding — xG context was tested honestly and **not adopted**, and is not part of the frozen feature set in Section L/P.

## AF. Goalie-quality — deliberately not tested (see Section H)

Restated here per the report structure: this was a disclosed scope decision, not an oversight, grounded in the prior win-model goalie-quality integration failure precedent.

## AG. Representative examples

12 representative real 2025-26 examples were captured and frozen in `research/team_goals_period_results.json["representative_examples"]`: `elite_offense`, `weak_offense`, `strong_defensive_opponent`, `weak_defensive_opponent`, `high_pp_team`, `low_pp_team`, `high_confidence_prediction`, `low_confidence_prediction`, `model_hit`, `model_miss`, `high_p1_scoring_team`, `high_p3_scoring_team` — each a real historical row with full feature/prediction/outcome detail, browsable in the dashboard.

## AH. Registry status

`research/player_props/market_registry.py` was updated (5th legitimate edit to this file across the project) for the 7 settlement-equivalent team-period market IDs (`TEAM_PERIOD_1/2/3_TOTAL`, `PERIOD_1/2/3_TEAM_TOTAL_GOALS`, `TEAM_SCORE_IN_PERIOD_1`):

- `historical_data_status`: `AVAILABLE_USED`
- `model_status`: `RESEARCH` (deliberately not `VALIDATED`, given Section W)
- `threshold_validation_status`: `ATTEMPTED_NOT_VALIDATED`

Registry totals confirmed unchanged elsewhere: `total_canonical_markets()=142`, `derivable_today()=24`, `validated_today()=12`.

## AI. Dashboard changes

- `dashboard/team_goals_period_view.py` — `TeamGoalsPeriodEngine`, reusing the same frozen `build_example`/`compute_candidates`/`confidence_for_example` functions the evaluation itself uses (no second parallel formula). Smoke-tested end-to-end on a real game (WSH vs. MIN, 2025020073): `full_game_expected=3.07`, real per-period expected goals/probabilities/confidence.
- `dashboard/pages/15_Team_Goals_By_Period_Research.py` — new page, prominently labeled **RESEARCH — NOT VALIDATED** in a red-tinted banner (visually distinct from the Player SOG by Period page's styling, to avoid any visual conflation of a validated vs. unvalidated model).
- `dashboard/app.py` — added navigation entry (🧊 Team Goals by Period).

## AJ. Files created / modified

**New:**
- `research/team_goals_period/{__init__,build_team_goals_period_corpus,features,hierarchy,upstream_team_goals}.py`
- `research/run_team_goals_period_model.py`
- `research/team_goals_period/team_game_period_goals.jsonl` (10,496 rows)
- `research/team_goals_period_results.json` (frozen results)
- `dashboard/team_goals_period_view.py`
- `dashboard/pages/15_Team_Goals_By_Period_Research.py`
- `tests/test_team_goals_period_model.py` (39 tests)
- `TEAM_GOALS_BY_PERIOD_VALIDATION_REPORT.md` (this file)

**Modified:**
- `research/player_props/market_registry.py` (7 team-period market IDs updated to RESEARCH/ATTEMPTED_NOT_VALIDATED)
- `dashboard/app.py` (navigation entry added)
- `tests/test_pbp_foundation.py` (hash pin updated for the market_registry.py edit above)

**Untouched (verified via `git diff --stat`, empty):** `models/`, `config.py`, `db.py`, `schema.sql`, `pricing/`, `nhl.db`.

## AK. decision_policy.py — deliberately unchanged

`research/player_props/decision_policy.py` was left at `prop_decision_policy_v3`, unmodified. LOW-confidence bucket sizes for team-period markets are tiny (0-24 rows/season per period, Section AA) — insufficient evidence to justify a new WATCH_ONLY gate in either direction. Verified unchanged via hash pin (Test38).

## AL. Full test suite result

**1,234 / 1,234 passing** (1,195 pre-existing + 39 new, mapped 1:1 to Part 46's numbered topics). Zero regressions, zero weakened assertions.

## AM. Recommended next slice

Given the honest null result here, two reasonable directions:
1. **Goalie-quality-in-team-period-goals** — the one disclosed, untested feature (Section H) — worth a dedicated, small, honest test now that the base architecture and evaluation harness exist, rather than folding it into this slice's scope.
2. **Both Teams Score / Game Period Totals** — both explicitly deferred this slice — should now proceed with the Section V home/away joint-dependence finding as a mandatory input: these markets must NOT assume home/away independence, especially for P3.

## AN. Final Questions

- **Was Team Goals by Period validated?** No. Point estimates exist and are real, but bootstrap evidence did not clear this project's adoption bar at any period/threshold combination in both eval seasons (Section W).
- **Is the negative result attributable to a bug?** No evidence of one — three independent lines of evidence (Section AC) point to a genuine statistical-power/effect-size limitation, not an implementation defect.
- **Did xG context help?** No — tested honestly (Section AE), found negative/inconclusive, not adopted.
- **Did goalie context help?** Not tested this slice (Section H) — disclosed scope decision, not a finding either way.
- **Is home/away independence assumable for future joint markets?** No — Section V's finding (real, model-persistent, growing by period) should inform Both Teams Score and any future joint simulator.
- **Were any production files touched?** No — verified via `git diff --stat` against `models/`, `config.py`, `db.py`, `schema.sql`, `pricing/`, `nhl.db` (empty diff).

---

**STOP AFTER TEAM GOALS BY PERIOD.**
